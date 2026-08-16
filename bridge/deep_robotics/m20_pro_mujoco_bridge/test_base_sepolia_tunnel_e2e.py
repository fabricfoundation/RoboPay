"""Live Base Sepolia x402 -> Tunnel -> M20 MuJoCo settlement proof.

This is only for a trusted repository workflow or an operator with a funded
testnet payer. It creates a generated receipt/result artifact and never stores
private key material in the bridge, Tunnel configuration, or repository.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import shlex
from pathlib import Path

import requests
import zenoh
from eth_account import Account
from x402 import x402ClientSync
from x402.http.clients import x402_requests
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

from m20_pro_mujoco_bridge.bridge import READY_TOPIC


PACKAGE_ROOT = Path(__file__).resolve().parent
ROOT = PACKAGE_ROOT.parents[2]
SKILL_CATALOG = (
    ROOT
    / "registry/vendors/deep-robotics/lynx-m20-pro"
    / "deep-robotics.lynx-m20-pro.mujoco-webots-obstacle-nav.v1/skill-catalog.json"
)
TUNNEL_BINARY = Path(os.environ.get("TUNNEL_BIN", ROOT / "bin" / "tunnel"))
NETWORK = "eip155:84532"
ROBOT_ID = "lynx-m20-pro-sim-01"
FABRIC_API_BASE = os.environ.get("FABRIC_API_BASE_URL", "https://api.fabric.foundation/api/core").rstrip("/")
PROXY_WS_URL = os.environ.get("PROXY_WS_URL", "wss://api.fabric.foundation/api/core/ws/robot")
FACILITATOR_URL = os.environ.get("FACILITATOR_URL", "https://x402.org/facilitator")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing {name}; configure funded Base Sepolia testnet credentials.")
    return value


def _source_commit_sha() -> str:
    configured = os.environ.get("ROBO_PAY_COMMIT_SHA", "").strip()
    if configured:
        return configured
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _wait_for_bridge_ready(ready: threading.Event, bridge: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready.wait(0.2):
            return
        if bridge.poll() is not None:
            raise RuntimeError(
                f"M20 bridge exited before declaring its Zenoh action subscription (exit={bridge.returncode})"
            )
    raise RuntimeError("M20 bridge never declared ready; refusing to send the first paid action")


def _wait_for_tunnel(tunnel: subprocess.Popen[str], log_path: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            raise RuntimeError(f"Tunnel exited early:\n{log_path.read_text(encoding='utf-8')}")
        if "ws connected to proxy" in log_path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Tunnel did not connect:\n{log_path.read_text(encoding='utf-8')}")


def _wait_for_terminal_status(status_url: str, *, timeout_seconds: float = 180.0) -> dict:
    """Poll the public status endpoint without aborting a paid action on a reset.

    The Tunnel/bridge must remain alive for the entire simulator episode. A
    one-off proxy TLS reset or a brief 503 while the robot session reconnects
    is therefore retryable transport state, not proof of action failure.
    """

    deadline = time.monotonic() + timeout_seconds
    last_issue = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(status_url, timeout=45)
        except requests.RequestException as error:
            last_issue = f"status transport reset: {type(error).__name__}: {error}"
            print(f"[status] {last_issue}; retrying", flush=True)
            time.sleep(2)
            continue
        if response.status_code == 503:
            last_issue = f"status endpoint temporarily unavailable: {response.text[:300]}"
            print(f"[status] {last_issue}; retrying", flush=True)
            time.sleep(2)
            continue
        if response.status_code == 200:
            candidate = response.json()
            if candidate.get("state") in {"succeeded", "failed", "timeout", "settlement_failed"}:
                return candidate
        else:
            last_issue = f"status HTTP {response.status_code}: {response.text[:300]}"
        time.sleep(2)
    raise RuntimeError(f"M20 action did not reach terminal status: {last_issue}")


def _stream_tunnel_output(
    tunnel: subprocess.Popen[str], log_path: Path, *, visual: bool
) -> threading.Thread | None:
    """Persist Tunnel output and optionally mirror it into the operator terminal."""

    if tunnel.stdout is None:
        return None
    log_path.touch()

    def copy_output() -> None:
        with log_path.open("w", encoding="utf-8") as log:
            for line in iter(tunnel.stdout.readline, ""):
                log.write(line)
                log.flush()
                if visual and any(
                    marker in line
                    for marker in (
                        "ws connected to proxy",
                        "action settled after successful execution",
                        "deferred settlement failed",
                    )
                ):
                    print(f"[tunnel] {line}", end="", flush=True)

    worker = threading.Thread(target=copy_output, name="m20-tunnel-log", daemon=True)
    worker.start()
    return worker


def _wsl_path(path: Path) -> str:
    """Translate a Windows workspace path for the real Linux Tunnel process."""

    if os.name != "nt":
        raise RuntimeError("--wsl-tunnel is only available from Windows")
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":")
    if len(drive) != 1 or not drive.isalpha():
        raise RuntimeError(f"WSL requires a drive-backed workspace path, got {resolved}")
    # All RoboPay workspaces here are on a Windows drive mounted by WSL. This
    # avoids passing backslashes through a nested Windows-to-WSL shell layer.
    native = resolved.as_posix()
    return f"/mnt/{drive.lower()}{native[2:]}"


def _wsl_host_address() -> str:
    completed = subprocess.run(
        ["wsl.exe", "-d", "Ubuntu-22.04", "--", "ip", "route", "show", "default"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in completed.stdout.splitlines():
        fields = line.split()
        if "via" in fields:
            host = fields[fields.index("via") + 1]
            if host:
                return host
    raise RuntimeError("Could not determine the Windows host address from WSL")


def _start_wsl_tunnel(
    root: Path,
    tunnel_config: Path,
    tunnel_env: dict[str, str],
    zenoh_config: Path,
) -> subprocess.Popen[str]:
    """Run the compiled Linux Tunnel while the visual client remains Windows-native."""

    root_wsl = _wsl_path(root)
    # Never serialize the complete Windows process environment into a Bash
    # command. Apart from being needlessly broad, Windows permits variable
    # names such as PROGRAMFILES(X86), which are not valid Bash assignments.
    required_names = (
        "PROXY_WS_URL",
        "FACILITATOR_URL",
        "AIP_ENABLED",
        "SKILL_CATALOG_PATH",
        "ALLOWED_ACTIONS",
        "EXECUTION_TIMEOUT_SECONDS",
        "ZENOH_CONFIG",
        "IDEMPOTENCY_STORE_PATH",
    )
    translated = {name: tunnel_env[name] for name in required_names if name in tunnel_env}
    translated.update(
        {
            "SKILL_CATALOG_PATH": _wsl_path(SKILL_CATALOG),
            "ZENOH_CONFIG": _wsl_path(zenoh_config),
            "IDEMPOTENCY_STORE_PATH": _wsl_path(tunnel_config.parent / "idempotency.json"),
            "LD_LIBRARY_PATH": f"{root_wsl}/.zenoh-c/lib",
        }
    )
    environment = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in sorted(translated.items())
    )
    command = (
        f"exec env {environment} {shlex.quote(f'{root_wsl}/bin/tunnel')} "
        f"--config {shlex.quote(_wsl_path(tunnel_config))}"
    )
    return subprocess.Popen(
        ["wsl.exe", "-d", "Ubuntu-22.04", "--", "bash", "-lc", command],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--visual",
        action="store_true",
        help="mirror real Tunnel logs and show the MuJoCo action execution",
    )
    parser.add_argument(
        "--open-basescan",
        action="store_true",
        help="open the real BaseScan transaction after correlated settlement",
    )
    parser.add_argument(
        "--wsl-tunnel",
        action="store_true",
        help="run the real Linux Tunnel in Ubuntu while the client/UI stay on Windows",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="prove discovery and unpaid 402 only; never sign or submit a paid action",
    )
    parser.add_argument(
        "--local-zenoh-router",
        action="store_true",
        help="start an isolated local Zenoh peer on tcp/0.0.0.0:7447",
    )
    args = parser.parse_args(argv)
    private_key = None if args.dry_run else _required("PRIVATE_KEY")
    payee = _required("ROBO_PAYEE_ADDRESS")
    commit_sha = _source_commit_sha()
    if private_key and not private_key.startswith("0x"):
        private_key = "0x" + private_key
    if not TUNNEL_BINARY.is_file():
        raise SystemExit(f"Tunnel binary missing: {TUNNEL_BINARY}")
    account = Account.from_key(private_key) if private_key else None
    action_id = f"m20-drive-{int(time.time())}"
    action_body = {
        "action": "navigate_obstacle_course",
        "robot_id": ROBOT_ID,
        "action_id": action_id,
        "idempotency_key": action_id,
        "params": {"goalDistanceM": 1.35, "wheelSpeedRadS": 4.0, "maxDurationSec": 16},
    }
    print(f"Evidence commit: {commit_sha}", flush=True)

    with tempfile.TemporaryDirectory(prefix="robopay_m20_base_sepolia_") as temp_dir:
        temp = Path(temp_dir)
        tunnel_config = temp / "tunnel.json"
        tunnel_config.write_text(
            json.dumps(
                {"robot_id": ROBOT_ID, "evm_payee_address": payee, "price": "$0.001", "network": NETWORK}
            ),
            encoding="utf-8",
        )
        zenoh_config = temp / "zenoh-client.json5"
        zenoh_config.write_text('{"mode":"client","connect":{"endpoints":["tcp/127.0.0.1:7447"]}}', encoding="utf-8")
        tunnel_zenoh_config = zenoh_config
        if args.wsl_tunnel:
            tunnel_zenoh_config = temp / "zenoh-wsl-client.json5"
            tunnel_zenoh_config.write_text(
                json.dumps(
                    {
                        "mode": "client",
                        "connect": {"endpoints": [f"tcp/{_wsl_host_address()}:7447"]},
                    }
                ),
                encoding="utf-8",
            )
        router_session = None
        if args.local_zenoh_router:
            router_session = zenoh.open(
                zenoh.Config.from_json5(
                    '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
                    '"listen":{"endpoints":["tcp/0.0.0.0:7447"]}}'
                )
            )
        ready = threading.Event()
        ready_session = zenoh.open(zenoh.Config.from_file(str(zenoh_config)))

        def on_ready(sample) -> None:
            try:
                payload = json.loads(bytes(sample.payload.to_bytes()))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if payload.get("status") == "ready" and payload.get("robot_id") == ROBOT_ID:
                ready.set()

        ready_subscriber = ready_session.declare_subscriber(READY_TOPIC, on_ready)
        log_path = temp / "tunnel.log"
        bridge_env = os.environ.copy()
        bridge_env.pop("PRIVATE_KEY", None)
        bridge_env.pop("EVM_PRIVATE_KEY", None)
        bridge_env.update(
            {
                "PYTHONPATH": str(PACKAGE_ROOT),
                "ZENOH_CONFIG": str(zenoh_config),
                "M20_MUJOCO_VIEWER": "true" if args.visual else "false",
                "M20_MUJOCO_VIEWER_HOLD_SECONDS": os.environ.get(
                    "M20_MUJOCO_VIEWER_HOLD_SECONDS", "5"
                ),
            }
        )
        bridge = subprocess.Popen(
            [sys.executable, "-m", "m20_pro_mujoco_bridge.bridge"],
            cwd=PACKAGE_ROOT,
            env=bridge_env,
            stdout=None if args.visual else subprocess.DEVNULL,
            stderr=None if args.visual else subprocess.STDOUT,
            text=True,
        )
        tunnel_env = os.environ.copy()
        tunnel_env.pop("PRIVATE_KEY", None)
        tunnel_env.pop("EVM_PRIVATE_KEY", None)
        tunnel_env.update(
            {
                "PROXY_WS_URL": PROXY_WS_URL,
                "FACILITATOR_URL": FACILITATOR_URL,
                "AIP_ENABLED": "false",
                "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                "ALLOWED_ACTIONS": "navigate_obstacle_course,stop",
                "EXECUTION_TIMEOUT_SECONDS": "90",
                "ZENOH_CONFIG": str(zenoh_config),
                "IDEMPOTENCY_STORE_PATH": str(temp / "idempotency.json"),
            }
        )
        tunnel = (
            _start_wsl_tunnel(ROOT, tunnel_config, tunnel_env, tunnel_zenoh_config)
            if args.wsl_tunnel
            else subprocess.Popen(
                [str(TUNNEL_BINARY), "--config", str(tunnel_config)],
                cwd=ROOT,
                env=tunnel_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        )
        log_thread = _stream_tunnel_output(tunnel, log_path, visual=args.visual)
        try:
            _wait_for_bridge_ready(ready, bridge)
            print("Bridge ready: action subscription declared; no warm-up action used", flush=True)
            _wait_for_tunnel(tunnel, log_path)
            print("Tunnel connected to Fabric Gateway", flush=True)
            skills = requests.get(f"{FABRIC_API_BASE}/robots/{ROBOT_ID}/skills", timeout=45)
            if skills.status_code != 200:
                raise RuntimeError(f"skill discovery failed: HTTP {skills.status_code}: {skills.text}")
            discovery = skills.json()
            if {item.get("skill_id") for item in discovery.get("skills", [])} != {"navigate_obstacle_course", "stop"}:
                raise RuntimeError(f"skill discovery drift: {discovery}")
            if any(item.get("price_usdc") != "0.001" for item in discovery["skills"]):
                raise RuntimeError(f"price drift: {discovery}")
            print("Robot discovery: Deep Robotics Lynx M20 Pro simulator-only", flush=True)
            print("Skill discovery: navigate_obstacle_course, stop @ 0.001 USDC", flush=True)

            action_url = f"{FABRIC_API_BASE}/robots/{ROBOT_ID}/action"
            unpaid = requests.post(action_url, json=action_body, timeout=45)
            if unpaid.status_code != 402:
                raise RuntimeError(f"expected unpaid 402, got {unpaid.status_code}: {unpaid.text}")
            print("Unpaid action: HTTP 402", flush=True)
            encoded = unpaid.headers.get("PAYMENT-REQUIRED") or unpaid.headers.get("Payment-Required")
            requirements = json.loads(base64.b64decode(encoded)) if encoded else {}
            accepted = requirements.get("accepts", [{}])[0]
            if accepted.get("network") != NETWORK or accepted.get("payTo", "").lower() != payee.lower():
                raise RuntimeError(f"payment requirements drift: {requirements}")
            authorization_window = int(accepted.get("maxTimeoutSeconds") or 0)
            if authorization_window < 300:
                raise RuntimeError(
                    "x402 authorization window is too short for deferred simulator settlement: "
                    f"{authorization_window}s"
                )
            print(f"Payment authorization window: {authorization_window}s", flush=True)
            if args.dry_run:
                print("Dry run complete: no payment was signed or submitted", flush=True)
                return 0

            # This is the first paid action after a clean bridge/Tunnel start.
            if account is None:
                raise RuntimeError("paid run requires a Base Sepolia account")
            client = x402ClientSync()
            register_exact_evm_client(client, EthAccountSigner(account), networks=NETWORK)
            print(f"Sending first paid action after clean start: {action_id}", flush=True)
            paid = x402_requests(client).post(action_url, json=action_body, timeout=120)
            if paid.status_code != 202 or paid.json().get("action_id") != action_id:
                raise RuntimeError(f"first paid action was not accepted: HTTP {paid.status_code}: {paid.text}")
            print(f"Paid action: HTTP 202 accepted ({action_id})", flush=True)
            status_url = f"{FABRIC_API_BASE}/robots/{ROBOT_ID}/action/{action_id}/status"
            terminal = _wait_for_terminal_status(status_url)
            if terminal.get("state") != "succeeded" or not terminal.get("settled"):
                tunnel_log = log_path.read_text(encoding="utf-8", errors="replace")
                settlement_lines = [
                    line
                    for line in tunnel_log.splitlines()
                    if "settlement" in line.lower() or "facilitator" in line.lower()
                ]
                if settlement_lines:
                    print("Tunnel settlement diagnostics:", flush=True)
                    print("\n".join(settlement_lines[-10:]), flush=True)
                raise RuntimeError(f"M20 execution or settlement failed: {terminal}")
            settlement = terminal.get("settlement") or {}
            tx_hash = settlement.get("transaction") or settlement.get("txHash")
            if not tx_hash:
                raise RuntimeError(f"successful paid M20 action lacks transaction receipt: {terminal}")
            print("Correlated terminal result:", json.dumps(terminal, indent=2), flush=True)
            print("Settlement settled: true", flush=True)
            print(f"Settlement transaction: {tx_hash}", flush=True)
            print(f"BaseScan: https://sepolia.basescan.org/tx/{tx_hash}", flush=True)

            evidence = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source_commit": commit_sha,
                "network": NETWORK,
                "payer": account.address,
                "payee": payee,
                "robot_id": ROBOT_ID,
                "action_id": action_id,
                "unpaid_http_status": unpaid.status_code,
                "paid_http_status": paid.status_code,
                "discovery": discovery,
                "terminal_status": terminal,
                "settlement": settlement,
                "transaction_hash": tx_hash,
                "basescan_url": f"https://sepolia.basescan.org/tx/{tx_hash}",
            }
            output = PACKAGE_ROOT / "artifacts" / f"base_sepolia_result_{int(time.time())}.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print(f"Evidence artifact: {output}", flush=True)
            if args.open_basescan:
                webbrowser.open(evidence["basescan_url"])
            return 0
        finally:
            if tunnel.poll() is None:
                tunnel.terminate()
                tunnel.wait(timeout=15)
            if bridge.poll() is None:
                bridge.terminate()
                bridge.wait(timeout=15)
            if log_thread is not None:
                log_thread.join(timeout=2)
            ready_subscriber.undeclare()
            ready_session.close()
            if router_session is not None:
                router_session.close()


if __name__ == "__main__":
    raise SystemExit(main())
