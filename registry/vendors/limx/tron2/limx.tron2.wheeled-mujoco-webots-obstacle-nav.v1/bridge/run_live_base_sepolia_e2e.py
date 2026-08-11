"""Record a real Base Sepolia x402 -> Tunnel -> Zenoh -> MuJoCo proof.

This operator-only command uses a funded testnet payer supplied at run time.
It never writes the private key, payee address, or generated receipt into a
tracked file.  The Tunnel, x402 client/facilitator, Zenoh transport, and TRON 2
MuJoCo episode are real implementations; the local proxy merely exposes the
Tunnel's WebSocket HTTP boundary for an OBS-friendly localhost URL.
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
from typing import Any

import requests
import zenoh
from eth_account import Account
from x402 import x402ClientSync
from x402.http.clients import x402_requests
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

from limx_tron2_sim.contracts import NAVIGATION_SKILL, ROBOT_ID
from limx_tron2_sim.bridge import READY_TOPIC
from limx_tron2_sim.visual_proxy import LocalTunnelProxy


PROFILE_ROOT = Path(__file__).resolve().parents[1]
SKILL_CATALOG = PROFILE_ROOT / "skill-catalog.json"
NETWORK = "eip155:84532"
PRICE_USDC = "0.001"
FACILITATOR_URL = "https://x402.org/facilitator"
FABRIC_API_BASE = os.environ.get("FABRIC_API_BASE_URL", "https://api.fabric.foundation/api/core").rstrip("/")
FABRIC_PROXY_WS = os.environ.get("PROXY_WS_URL", "wss://api.fabric.foundation/api/core/ws/robot")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name}. Supply it through the current process environment; never put it in this repository.")
    return value


def _wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":")
    if len(drive) != 1 or not drive.isalpha():
        raise RuntimeError(f"WSL requires a drive-backed path, got {resolved}")
    return f"/mnt/{drive.lower()}{resolved.as_posix()[2:]}"


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
    raise RuntimeError("could not determine the Windows host address from WSL")


def _tunnel_binary() -> Path:
    path = Path(_required("TUNNEL_BIN"))
    if not path.is_file():
        raise SystemExit(f"TUNNEL_BIN is not a file: {path}")
    return path


def _stream_output(process: subprocess.Popen[str], *, prefix: str, enabled: bool) -> threading.Thread | None:
    if process.stdout is None:
        return None

    def mirror() -> None:
        for line in iter(process.stdout.readline, ""):
            if enabled:
                print(f"[{prefix}] {line}", end="", flush=True)

    worker = threading.Thread(target=mirror, name=f"{prefix}-output", daemon=True)
    worker.start()
    return worker


def _wait_for_tunnel(proxy: LocalTunnelProxy, tunnel: subprocess.Popen[str]) -> None:
    if proxy.wait_for_connection(30) is not None:
        return
    if tunnel.poll() is not None:
        raise RuntimeError(f"Tunnel exited before connecting (exit={tunnel.returncode})")
    raise RuntimeError("Tunnel did not connect to the local visual proxy")


def _wait_for_public_tunnel(
    tunnel: subprocess.Popen[str],
    skills_url: str,
    *,
    timeout_seconds: float = 90.0,
) -> None:
    """Wait until Fabric can route a supported Tunnel discovery request."""

    deadline = time.monotonic() + timeout_seconds
    last_observation = "no discovery response"
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            raise RuntimeError(f"Tunnel exited before connecting to the Fabric proxy (exit={tunnel.returncode})")
        try:
            response = requests.get(skills_url, timeout=10)
            if response.status_code == 200:
                return
            last_observation = f"HTTP {response.status_code}: {response.text[:300]}"
        except requests.RequestException as error:
            last_observation = f"transport error: {error}"
        time.sleep(1)
    raise RuntimeError(
        f"Tunnel did not become publicly discoverable within {timeout_seconds:g}s: {last_observation}"
    )


def _wait_for_terminal(status_url: str, *, timeout_seconds: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: str | None = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(status_url, timeout=20)
            if response.status_code == 200:
                terminal = response.json()
                if terminal.get("state") in {"succeeded", "failed", "timeout", "settlement_failed"}:
                    return terminal
                last = json.dumps(terminal, sort_keys=True)
            else:
                last = f"HTTP {response.status_code}: {response.text[:300]}"
        except requests.RequestException as error:
            last = f"transport error: {error}"
        time.sleep(1)
    raise RuntimeError(f"action did not reach terminal status: {last}")


def _wait_for_bridge_ready(ready: threading.Event, bridge: subprocess.Popen[str]) -> None:
    """Fail before payment if the real bridge has not subscribed to Zenoh."""

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready.wait(0.2):
            return
        if bridge.poll() is not None:
            raise RuntimeError(f"LimX bridge exited before declaring its Zenoh action subscription (exit={bridge.returncode})")
    raise RuntimeError("LimX bridge never declared ready; refusing to send the first paid action")


def _tunnel_environment(
    *,
    binary: Path,
    zenoh_config: Path,
    proxy_ws_url: str,
    payee: str,
    idempotency_path: Path,
) -> dict[str, str]:
    tunnel_root = binary.resolve().parents[1]
    library_dir = tunnel_root / ".zenoh-c" / "lib"
    return {
        "PROXY_WS_URL": proxy_ws_url,
        "FACILITATOR_URL": FACILITATOR_URL,
        "AIP_ENABLED": "false",
        "ROBOT_ID": ROBOT_ID,
        "ROBO_PAYEE_ADDRESS": payee,
        # Keep shell metacharacters out of the Windows -> WSL environment handoff.
        # Tunnel accepts the canonical decimal form and still advertises the same
        # 1,000-microunit USDC charge in its x402 payment requirements.
        "ROBO_PRICE": PRICE_USDC,
        "ROBO_NETWORK": NETWORK,
        "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
        "ALLOWED_ACTIONS": f"{NAVIGATION_SKILL},stop",
        "EXECUTION_TIMEOUT_SECONDS": "90",
        "ZENOH_CONFIG": str(zenoh_config),
        "IDEMPOTENCY_STORE_PATH": str(idempotency_path),
        "LD_LIBRARY_PATH": str(library_dir),
    }


def _start_wsl_tunnel(
    *,
    binary: Path,
    config: Path,
    zenoh_config: Path,
    proxy_ws_url: str,
    payee: str,
    idempotency_path: Path,
) -> subprocess.Popen[str]:
    variables = _tunnel_environment(
        binary=binary,
        zenoh_config=zenoh_config,
        proxy_ws_url=proxy_ws_url,
        payee=payee,
        idempotency_path=idempotency_path,
    )
    variables["SKILL_CATALOG_PATH"] = _wsl_path(SKILL_CATALOG)
    variables["ZENOH_CONFIG"] = _wsl_path(zenoh_config)
    variables["IDEMPOTENCY_STORE_PATH"] = _wsl_path(idempotency_path)
    variables["LD_LIBRARY_PATH"] = _wsl_path(binary.resolve().parents[1] / ".zenoh-c" / "lib")
    environment = " ".join(f"{key}={shlex.quote(value)}" for key, value in sorted(variables.items()))
    command = f"exec env {environment} {shlex.quote(_wsl_path(binary))} --config {shlex.quote(_wsl_path(config))}"
    return subprocess.Popen(
        ["wsl.exe", "-d", "Ubuntu-22.04", "--", "bash", "-lc", command],
        cwd=PROFILE_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _start_native_tunnel(
    *,
    binary: Path,
    config: Path,
    zenoh_config: Path,
    proxy_ws_url: str,
    payee: str,
    idempotency_path: Path,
) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment.update(
        _tunnel_environment(
            binary=binary,
            zenoh_config=zenoh_config,
            proxy_ws_url=proxy_ws_url,
            payee=payee,
            idempotency_path=idempotency_path,
        )
    )
    return subprocess.Popen(
        [str(binary), "--config", str(config)],
        cwd=binary.resolve().parents[1],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visual", action="store_true", help="show MuJoCo and mirror Tunnel/client events for OBS")
    parser.add_argument("--open-basescan", action="store_true", help="open the actual settlement transaction after success")
    parser.add_argument("--dry-run", action="store_true", help="perform discovery plus an unpaid 402 only; never sign or settle")
    parser.add_argument("--ci", action="store_true", help="run natively without WSL or desktop viewers (GitHub Actions)")
    args = parser.parse_args(argv)

    payee = os.environ.get("ROBO_PAYEE_ADDRESS") or _required("ROBOT_PAYEE_ADDRESS")
    private_key = "" if args.dry_run else (os.environ.get("PRIVATE_KEY") or _required("BASE_SEPOLIA_PRIVATE_KEY"))
    binary = _tunnel_binary()
    action_id = f"limx-tron2-navigation-{int(time.time())}"
    action_body = {
        "action": NAVIGATION_SKILL,
        "robot_id": ROBOT_ID,
        "action_id": action_id,
        "idempotency_key": action_id,
        "params": {},
    }

    with tempfile.TemporaryDirectory(prefix="robopay_limx_tron2_base_sepolia_") as directory:
        temp = Path(directory)
        config = temp / "tunnel.json"
        config.write_text(
            json.dumps({"robot_id": ROBOT_ID, "evm_payee_address": payee, "price": f"${PRICE_USDC}", "network": NETWORK}),
            encoding="utf-8",
        )
        zenoh_config = temp / "zenoh-client.json5"
        zenoh_config.write_text('{"mode":"client","connect":{"endpoints":["tcp/127.0.0.1:7447"]}}', encoding="utf-8")
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
        bridge_env = os.environ.copy()
        for name in ("BASE_SEPOLIA_PRIVATE_KEY", "PRIVATE_KEY", "EVM_PRIVATE_KEY"):
            bridge_env.pop(name, None)
        bridge_env.update(
            {
                "PYTHONPATH": str(PROFILE_ROOT / "bridge"),
                "ZENOH_CONFIG": str(zenoh_config),
                "LIMX_TRON2_MUJOCO_VIEWER": "true" if args.visual else "false",
                # Keep the terminal pose visible briefly for the recording, then
                # close the viewer so the bridge can publish ActionResult and let
                # Tunnel settle without operator interaction.
                "LIMX_TRON2_MUJOCO_VIEWER_HOLD_SECONDS": "1.5",
            }
        )
        bridge = subprocess.Popen(
            [sys.executable, "-m", "limx_tron2_sim.bridge"],
            cwd=PROFILE_ROOT / "bridge",
            env=bridge_env,
            stdout=None if args.visual else subprocess.DEVNULL,
            stderr=None if args.visual else subprocess.STDOUT,
            text=True,
        )
        # The OBS launcher intentionally uses localhost so the operator can
        # show every request.  CI instead connects the real Tunnel to the
        # hosted Fabric proxy, matching the trusted-branch evidence workflow.
        proxy: LocalTunnelProxy | None = None
        if args.ci:
            start_tunnel = _start_native_tunnel
            proxy_url = FABRIC_PROXY_WS
            base_url = f"{FABRIC_API_BASE}/robots/{ROBOT_ID}"
        else:
            proxy = LocalTunnelProxy(verbose=args.visual)
            proxy.start()
            start_tunnel = _start_wsl_tunnel
            proxy_url = f"ws://{_wsl_host_address()}:{proxy.port}/ws"
            base_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}"
        tunnel = start_tunnel(
            binary=binary,
            config=config,
            zenoh_config=zenoh_config,
            proxy_ws_url=proxy_url,
            payee=payee,
            idempotency_path=temp / "idempotency.json",
        )
        output_thread = _stream_output(tunnel, prefix="tunnel", enabled=args.visual or args.ci)
        try:
            _wait_for_bridge_ready(ready, bridge)
            if proxy is None:
                # Fabric maps /robots/{id}/skills to Tunnel's supported
                # internal /skills route.  The robot root maps to `/`, which
                # intentionally returns 404 and therefore is not a readiness
                # endpoint (the approved Spot E2E follows the same contract).
                _wait_for_public_tunnel(tunnel, base_url + "/skills")
            else:
                _wait_for_tunnel(proxy, tunnel)
            discovery = requests.get(base_url + "/skills", timeout=30)
            discovery.raise_for_status()
            print("[client] skill discovery", json.dumps(discovery.json(), indent=2), flush=True)

            unpaid = requests.post(base_url + "/action", json=action_body, timeout=45)
            print(f"[client] unpaid action -> HTTP {unpaid.status_code}", flush=True)
            if unpaid.status_code != 402:
                raise RuntimeError(f"expected unpaid 402, got {unpaid.status_code}: {unpaid.text}")
            payment_required = unpaid.headers.get("PAYMENT-REQUIRED") or unpaid.headers.get("Payment-Required")
            if not payment_required:
                raise RuntimeError("unpaid response omitted PAYMENT-REQUIRED")
            requirement = json.loads(base64.b64decode(payment_required))["accepts"][0]
            if requirement.get("network") != NETWORK or requirement.get("payTo", "").lower() != payee.lower():
                raise RuntimeError(f"payment requirement drift: {requirement}")
            print("[client] payment requirement", json.dumps(requirement, indent=2), flush=True)
            if args.dry_run:
                print("[client] dry run complete: no signature, settlement, or chain transaction was created.", flush=True)
                return 0

            account = Account.from_key(private_key if private_key.startswith("0x") else "0x" + private_key)
            client = x402ClientSync()
            register_exact_evm_client(client, EthAccountSigner(account), networks=NETWORK)
            print(f"[client] sending first paid action from {account.address}", flush=True)
            paid = x402_requests(client).post(base_url + "/action", json=action_body, timeout=120)
            print(f"[client] paid action -> HTTP {paid.status_code}: {paid.text}", flush=True)
            if paid.status_code != 202 or paid.json().get("action_id") != action_id:
                raise RuntimeError("first paid action was not accepted with the requested action_id")
            terminal = _wait_for_terminal(base_url + f"/action/{action_id}/status")
            print("[client] terminal status", json.dumps(terminal, indent=2), flush=True)
            if terminal.get("state") != "succeeded" or terminal.get("settled") is not True:
                raise RuntimeError("simulator success or deferred settlement did not complete")
            settlement = terminal.get("settlement") or {}
            transaction = settlement.get("transaction") or settlement.get("txHash")
            if not transaction:
                raise RuntimeError("successful terminal status omitted settlement transaction")
            evidence = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "network": NETWORK,
                "payer": account.address,
                "payee": payee,
                "robot_id": ROBOT_ID,
                "action_id": action_id,
                "unpaid_http_status": unpaid.status_code,
                "paid_http_status": paid.status_code,
                "terminal_status": terminal,
                "transaction_hash": transaction,
                "basescan_url": f"https://sepolia.basescan.org/tx/{transaction}",
            }
            output = PROFILE_ROOT / "artifacts" / f"base_sepolia_result_{int(time.time())}.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print("[client] live evidence", json.dumps(evidence, indent=2), flush=True)
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
            if output_thread is not None:
                output_thread.join(timeout=2)
            if proxy is not None:
                proxy.close()


if __name__ == "__main__":
    raise SystemExit(main())
