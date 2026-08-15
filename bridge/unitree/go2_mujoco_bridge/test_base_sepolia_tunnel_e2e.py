"""Live Base Sepolia x402 -> Tunnel -> Go2 MuJoCo settlement test.

The test intentionally uses the real public x402 test facilitator and a real
USDC ``transferWithAuthorization`` settlement on Base Sepolia.  It must only
be run with a funded *testnet* payer key supplied through ``PRIVATE_KEY``.
Neither the key nor the authorization payload is written to disk.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

import requests
import zenoh
from eth_account import Account
from x402 import x402ClientSync
from x402.http.clients import x402_requests
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

from go2_mujoco_bridge.bridge import READY_TOPIC


PACKAGE_ROOT = Path(__file__).resolve().parent
ROOT = PACKAGE_ROOT.parents[2]
SKILL_CATALOG = (
    ROOT
    / "registry/vendors/unitree/go2"
    / "unitree.go2.mujoco-webots-obstacle-nav.v1/skill-catalog.json"
)
TUNNEL_BINARY = Path(os.environ.get("TUNNEL_BIN", ROOT / "bin" / "tunnel"))
NETWORK = "eip155:84532"
FACILITATOR_URL = os.environ.get("FACILITATOR_URL", "https://x402.org/facilitator")
FABRIC_API_BASE = os.environ.get(
    "FABRIC_API_BASE_URL", "https://api.fabric.foundation/api/core"
).rstrip("/")
PROXY_WS_URL = os.environ.get(
    "PROXY_WS_URL", "wss://api.fabric.foundation/api/core/ws/robot"
)


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing {name}; configure it locally before running this live test.")
    return value


def _source_commit_sha() -> str:
    """Return the source revision that must be visible in visual evidence."""

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


def _decode_header(value: str | None) -> dict:
    if not value:
        return {}
    return json.loads(base64.b64decode(value).decode("utf-8"))


def _wait_for_tunnel(tunnel: subprocess.Popen[str], log_path: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            raise RuntimeError(f"Tunnel exited early:\n{log_path.read_text(encoding='utf-8')}")
        log = log_path.read_text(encoding="utf-8", errors="replace")
        if "ws connected to proxy" in log:
            return
        time.sleep(0.5)
    raise RuntimeError(f"Tunnel did not connect to Fabric within 30 seconds:\n{log_path.read_text(encoding='utf-8')}")


def _wait_for_bridge_ready(ready: threading.Event, bridge: subprocess.Popen[str]) -> None:
    """Fail before payment if the real bridge has not subscribed to Zenoh."""

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready.wait(0.2):
            return
        if bridge.poll() is not None:
            raise RuntimeError(
                f"Go2 bridge exited before declaring its Zenoh action subscription (exit={bridge.returncode})"
            )
    raise RuntimeError("Go2 bridge never declared ready; refusing to send the first paid action")


def _stream_tunnel_output(
    tunnel: subprocess.Popen[str], log_path: Path, *, visual: bool
) -> threading.Thread:
    """Persist Tunnel output and mirror it into the OBS terminal when requested."""

    if tunnel.stdout is None:
        raise RuntimeError("Tunnel must be started with captured stdout")
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
                    )
                ):
                    print(f"[tunnel] {line}", end="", flush=True)

    worker = threading.Thread(target=copy_output, name="go2-tunnel-log", daemon=True)
    worker.start()
    return worker


def _wsl_path(path: Path) -> str:
    """Translate a drive-backed Windows path for the Linux Tunnel process."""

    if os.name != "nt":
        raise RuntimeError("--wsl-tunnel is only available from Windows")
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":")
    if len(drive) != 1 or not drive.isalpha():
        raise RuntimeError(f"WSL requires a drive-backed workspace path, got {resolved}")
    native = resolved.as_posix()
    return f"/mnt/{drive.lower()}{native[2:]}"


def _wsl_host_address() -> str:
    """Return the Windows host address reachable from the WSL 2 guest."""

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
    raise RuntimeError("Could not determine the Windows host address from WSL's default route")


def _start_wsl_tunnel(
    tunnel_config: Path,
    tunnel_env: dict[str, str],
    zenoh_config: Path,
) -> subprocess.Popen[str]:
    """Keep MuJoCo on Windows while the compiled Linux Tunnel runs in WSL."""

    root_wsl = _wsl_path(ROOT)
    translated = {
        "PROXY_WS_URL": tunnel_env["PROXY_WS_URL"],
        "FACILITATOR_URL": tunnel_env["FACILITATOR_URL"],
        "AIP_ENABLED": tunnel_env["AIP_ENABLED"],
        "ALLOWED_ACTIONS": tunnel_env["ALLOWED_ACTIONS"],
        "EXECUTION_TIMEOUT_SECONDS": tunnel_env["EXECUTION_TIMEOUT_SECONDS"],
        "SKILL_CATALOG_PATH": _wsl_path(SKILL_CATALOG),
        "ZENOH_CONFIG": _wsl_path(zenoh_config),
        "IDEMPOTENCY_STORE_PATH": _wsl_path(tunnel_config.parent / "idempotency.json"),
        "LD_LIBRARY_PATH": f"{root_wsl}/.zenoh-c/lib",
    }
    environment = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in sorted(translated.items())
    )
    command = (
        f"exec env {environment} {shlex.quote(f'{root_wsl}/bin/tunnel')} "
        f"--config {shlex.quote(_wsl_path(tunnel_config))}"
    )
    return subprocess.Popen(
        ["wsl.exe", "-d", "Ubuntu-22.04", "--", "bash", "-lc", command],
        cwd=ROOT,
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
        help="show MuJoCo and mirror real Tunnel logs for an operator recording",
    )
    parser.add_argument(
        "--open-basescan",
        action="store_true",
        help="open the settlement transaction after correlated success",
    )
    parser.add_argument(
        "--wsl-tunnel",
        action="store_true",
        help="run the Linux Tunnel in Ubuntu while the bridge and viewer stay on Windows",
    )
    parser.add_argument(
        "--local-zenoh-router",
        action="store_true",
        help="start an isolated local Zenoh peer on tcp/0.0.0.0:7447 for the recording",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="stop after discovery and the unpaid 402; never sign or submit payment",
    )
    args = parser.parse_args(argv)
    private_key = None if args.dry_run else _required_env("PRIVATE_KEY")
    payee = _required_env("ROBO_PAYEE_ADDRESS")
    commit_sha = _source_commit_sha()
    if private_key and not private_key.startswith("0x"):
        private_key = "0x" + private_key
    if not TUNNEL_BINARY.is_file():
        raise SystemExit(f"Tunnel binary missing: {TUNNEL_BINARY}")

    account = Account.from_key(private_key) if private_key else None
    robot_id = os.environ.get("ROBOT_ID", f"go2-mujoco-base-sepolia-{int(time.time())}")
    request_id = f"go2-obstacle-nav-{int(time.time())}"
    action_url = f"{FABRIC_API_BASE}/robots/{robot_id}/action"
    action_body = {
        "action": "navigate_obstacles",
        "robot_id": robot_id,
        "action_id": request_id,
        "idempotency_key": request_id,
        "params": {
            "maxDurationSec": 48,
            "side": "left",
            "speedScale": 1.0,
        },
    }
    print(f"Evidence commit: {commit_sha}", flush=True)

    with tempfile.TemporaryDirectory(prefix="robopay_go2_base_sepolia_") as temp_dir:
        temp = Path(temp_dir)
        tunnel_config = temp / "tunnel.json"
        tunnel_config.write_text(
            json.dumps(
                {
                    "robot_id": robot_id,
                    "evm_payee_address": payee,
                    "price": "$0.001",
                    "network": NETWORK,
                }
            ),
            encoding="utf-8",
        )
        zenoh_config = temp / "zenoh-client.json5"
        zenoh_config.write_text(
            '{"mode":"client","connect":{"endpoints":["tcp/127.0.0.1:7447"]}}',
            encoding="utf-8",
        )
        wsl_zenoh_config = zenoh_config
        if args.wsl_tunnel:
            wsl_zenoh_config = temp / "zenoh-wsl-client.json5"
            wsl_zenoh_config.write_text(
                json.dumps(
                    {
                        "mode": "client",
                        "connect": {
                            "endpoints": [f"tcp/{_wsl_host_address()}:7447"]
                        },
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
            if payload.get("status") == "ready" and payload.get("robot_id") == robot_id:
                ready.set()

        ready_subscriber = ready_session.declare_subscriber(READY_TOPIC, on_ready)
        tunnel_log_path = temp / "tunnel.log"
        bridge_env = os.environ.copy()
        for secret_name in ("PRIVATE_KEY", "EVM_PRIVATE_KEY"):
            bridge_env.pop(secret_name, None)
        bridge_env["PYTHONPATH"] = str(PACKAGE_ROOT) + os.pathsep + bridge_env.get("PYTHONPATH", "")
        bridge_env["ZENOH_CONFIG"] = str(zenoh_config)
        bridge_env["ROBOT_ID"] = robot_id
        bridge_env["GO2_MUJOCO_VIEWER"] = "true" if args.visual else "false"
        bridge_env["GO2_MUJOCO_VIEWER_HOLD_SECONDS"] = os.environ.get(
            "GO2_MUJOCO_VIEWER_HOLD_SECONDS", "15"
        )
        bridge = subprocess.Popen(
            [sys.executable, "-m", "go2_mujoco_bridge.bridge"],
            cwd=PACKAGE_ROOT,
            env=bridge_env,
            stdout=None if args.visual else subprocess.DEVNULL,
            stderr=None if args.visual else subprocess.STDOUT,
            text=True,
        )
        tunnel_env = os.environ.copy()
        for secret_name in ("PRIVATE_KEY", "EVM_PRIVATE_KEY"):
            tunnel_env.pop(secret_name, None)
        tunnel_env.update(
            {
                "PROXY_WS_URL": PROXY_WS_URL,
                "FACILITATOR_URL": FACILITATOR_URL,
                "AIP_ENABLED": "false",
                "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                "ALLOWED_ACTIONS": "navigate_obstacles,stop",
                "EXECUTION_TIMEOUT_SECONDS": "90",
                "ZENOH_CONFIG": str(zenoh_config),
            }
        )
        tunnel = (
            _start_wsl_tunnel(tunnel_config, tunnel_env, wsl_zenoh_config)
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
        tunnel_log_thread = _stream_tunnel_output(
            tunnel, tunnel_log_path, visual=args.visual
        )
        try:
            _wait_for_bridge_ready(ready, bridge)
            print("Bridge ready: action subscription declared; no warm-up action used", flush=True)
            _wait_for_tunnel(tunnel, tunnel_log_path)
            print("Tunnel connected to Fabric", flush=True)

            discovery_response = requests.get(
                f"{FABRIC_API_BASE}/robots/{robot_id}/skills", timeout=45
            )
            if discovery_response.status_code != 200:
                raise RuntimeError(
                    "Robot skill discovery failed before payment: "
                    f"HTTP {discovery_response.status_code}: {discovery_response.text}"
                )
            discovery = discovery_response.json()
            discovered_skills = {item.get("skill_id") for item in discovery.get("skills", [])}
            if discovered_skills != {"navigate_obstacles", "stop"}:
                raise RuntimeError(f"Unexpected skill discovery response: {discovery}")
            if any(item.get("price_usdc") != "0.001" for item in discovery["skills"]):
                raise RuntimeError(f"Skill discovery price drift: {discovery}")
            print("Robot discovery: Go2 simulator-only")
            print("Skill discovery: navigate_obstacles, stop @ 0.001 USDC")

            unpaid = requests.post(action_url, json=action_body, timeout=45)
            if unpaid.status_code != 402:
                raise RuntimeError(f"Expected HTTP 402, got {unpaid.status_code}: {unpaid.text}")
            print("Unpaid action: HTTP 402", flush=True)
            requirements = _decode_header(
                unpaid.headers.get("PAYMENT-REQUIRED") or unpaid.headers.get("Payment-Required")
            )
            accepted = requirements.get("accepts", [{}])[0]
            if accepted.get("payTo", "").lower() != payee.lower():
                raise RuntimeError(f"Unexpected payment recipient: {accepted.get('payTo')}")
            if accepted.get("network") != NETWORK:
                raise RuntimeError(f"Unexpected payment network: {accepted.get('network')}")
            if args.dry_run:
                print("Dry run complete: no payment was signed or submitted", flush=True)
                return 0

            if account is None:
                raise RuntimeError("paid run requires a Base Sepolia account")
            client = x402ClientSync()
            register_exact_evm_client(client, EthAccountSigner(account), networks=NETWORK)
            print(f"Sending first paid action after clean start: {request_id}", flush=True)
            paid = x402_requests(client).post(action_url, json=action_body, timeout=120)
            if paid.status_code != 202:
                raise RuntimeError(f"Paid action failed: HTTP {paid.status_code}: {paid.text}")
            accepted_body = paid.json()
            if accepted_body.get("action_id") != request_id:
                raise RuntimeError(f"Action ID mismatch: {accepted_body}")
            print(f"Paid action: HTTP 202 accepted ({request_id})", flush=True)

            status_url = f"{FABRIC_API_BASE}/robots/{robot_id}/action/{request_id}/status"
            terminal = None
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                response = requests.get(status_url, timeout=45)
                if response.status_code == 200:
                    candidate = response.json()
                    if candidate.get("state") in {"succeeded", "failed", "timeout", "settlement_failed"}:
                        terminal = candidate
                        break
                time.sleep(2)
            if terminal is None:
                raise RuntimeError("Action never reached a terminal status")
            if terminal.get("state") != "succeeded" or not terminal.get("settled"):
                raise RuntimeError(f"Action or settlement failed: {terminal}")
            settlement = terminal.get("settlement") or {}
            tx_hash = settlement.get("transaction") or settlement.get("txHash")
            if not tx_hash:
                raise RuntimeError(f"Successful action lacks settlement transaction: {terminal}")
            print("Settlement settled: true")
            print(f"Settlement transaction: {tx_hash}")
            print(f"BaseScan: https://sepolia.basescan.org/tx/{tx_hash}")

            evidence = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source_commit": commit_sha,
                "network": NETWORK,
                "payer": account.address,
                "payee": payee,
                "robot_id": robot_id,
                "request_id": request_id,
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
            if args.visual:
                result = terminal.get("result") or {}
                print(
                    "Correlated result: "
                    f"state={terminal.get('state')}, "
                    f"success={result.get('success')}, "
                    f"waypoints={result.get('waypoints_completed') + 1}/"
                    f"{result.get('waypoint_count')}, "
                    f"obstacle_contacts={result.get('obstacle_contact_count')}",
                    flush=True,
                )
                print(f"Evidence: {output}", flush=True)
            else:
                print(json.dumps(evidence, indent=2))
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
            ready_subscriber.undeclare()
            ready_session.close()
            if router_session is not None:
                router_session.close()
            tunnel_log_thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
