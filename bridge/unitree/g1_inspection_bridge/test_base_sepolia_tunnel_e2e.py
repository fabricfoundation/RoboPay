"""Live Base Sepolia x402 -> Tunnel -> G1 MuJoCo -> settlement proof."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import tempfile
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


PACKAGE_ROOT = Path(__file__).resolve().parent
ROOT = PACKAGE_ROOT.parents[2]
SKILL_CATALOG = ROOT / "registry/vendors/unitree/g1/unitree.g1.mujoco-webots-active-inspection.v1/skill-catalog.json"
TUNNEL_BINARY = Path(os.environ.get("TUNNEL_BIN", ROOT / "bin" / "tunnel"))
NETWORK = "eip155:84532"
FACILITATOR_URL = os.environ.get("FACILITATOR_URL", "https://x402.org/facilitator")
FABRIC_API_BASE = os.environ.get("FABRIC_API_BASE_URL", "https://api.fabric.foundation/api/core").rstrip("/")
PROXY_WS_URL = os.environ.get("PROXY_WS_URL", "wss://api.fabric.foundation/api/core/ws/robot")


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing {name}; configure a funded testnet value.")
    return value


def decode_header(value: str | None) -> dict:
    return json.loads(base64.b64decode(value).decode()) if value else {}


def source_commit_sha() -> str:
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


def wsl_path(path: Path) -> str:
    """Translate an absolute Windows path to the corresponding WSL mount path."""
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":")
    if len(drive) != 1 or not drive.isalpha():
        raise RuntimeError(f"Cannot translate path to WSL: {resolved}")
    return f"/mnt/{drive.lower()}{resolved.as_posix()[2:]}"


def wsl_host_address() -> str:
    completed = subprocess.run(
        ["wsl.exe", "-d", os.environ.get("WSL_DISTRO_NAME", "Ubuntu-22.04"), "--", "ip", "route", "show", "default"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in completed.stdout.splitlines():
        fields = line.split()
        if "via" in fields:
            return fields[fields.index("via") + 1]
    raise RuntimeError("Could not resolve the Windows host address from WSL")


def windows_tunnel_backend() -> str:
    """Choose an explicit Windows Linux backend without exposing secrets."""
    configured = os.environ.get("UNITREE_G1_TUNNEL_BACKEND", "").strip().lower()
    if configured:
        if configured not in {"docker", "wsl"}:
            raise RuntimeError("UNITREE_G1_TUNNEL_BACKEND must be docker or wsl")
        return configured
    docker = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "docker" if docker.returncode == 0 and docker.stdout.strip() else "wsl"


def start_tunnel(
    tunnel_config: Path,
    zenoh_config: Path,
    tunnel_env: dict[str, str],
    log,
) -> subprocess.Popen:
    """Run the Linux Tunnel in WSL while keeping the simulator native on Windows."""
    if os.name != "nt":
        return subprocess.Popen(
            [str(TUNNEL_BINARY), "--config", str(tunnel_config)],
            cwd=ROOT,
            env=tunnel_env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    if windows_tunnel_backend() == "docker":
        container_name = f"robopay-g1-tunnel-{os.getpid()}"
        container_config_dir = "/run/robopay"
        forwarded = (
            "PROXY_WS_URL", "FACILITATOR_URL", "AIP_ENABLED", "ALLOWED_ACTIONS",
            "EXECUTION_TIMEOUT_SECONDS",
        )
        command = [
            "docker", "run", "--rm", "--name", container_name,
            "-v", f"{ROOT}:/work",
            "-v", f"{tunnel_config.parent}:{container_config_dir}",
            "-w", "/work",
            "-e", "SKILL_CATALOG_PATH=/work/registry/vendors/unitree/g1/unitree.g1.mujoco-webots-active-inspection.v1/skill-catalog.json",
            "-e", f"ZENOH_CONFIG={container_config_dir}/{zenoh_config.name}",
            "-e", "LD_LIBRARY_PATH=/work/.zenoh-c/lib",
        ]
        for name in forwarded:
            command.extend(("-e", f"{name}={tunnel_env[name]}"))
        command.extend(("python:3.10-bookworm", "/work/bin/tunnel", "--config", f"{container_config_dir}/{tunnel_config.name}"))
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        process.robopay_docker_container = container_name
        return process

    wsl_env = tunnel_env.copy()
    wsl_env.update({
        "SKILL_CATALOG_PATH": wsl_path(SKILL_CATALOG),
        "ZENOH_CONFIG": wsl_path(zenoh_config),
        "LD_LIBRARY_PATH": wsl_path(ROOT / ".zenoh-c" / "lib"),
    })
    forwarded = (
        "PROXY_WS_URL", "FACILITATOR_URL", "AIP_ENABLED", "SKILL_CATALOG_PATH",
        "ALLOWED_ACTIONS", "EXECUTION_TIMEOUT_SECONDS", "ZENOH_CONFIG", "LD_LIBRARY_PATH",
    )
    assignments = " ".join(
        f"{name}={shlex.quote(wsl_env[name])}" for name in forwarded
    )
    command = (
        f"exec env {assignments} {shlex.quote(wsl_path(TUNNEL_BINARY))} "
        f"--config {shlex.quote(wsl_path(tunnel_config))}"
    )
    distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu-22.04")
    return subprocess.Popen(
        ["wsl.exe", "-d", distro, "--", "bash", "-lc", command],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def stop_tunnel(process: subprocess.Popen) -> None:
    container = getattr(process, "robopay_docker_container", None)
    if container:
        subprocess.run(
            ["docker", "stop", "--time", "5", container],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.terminate()
        return
    if process.poll() is None:
        process.terminate()
        process.wait(15)


def wait_for_tunnel(process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Tunnel exited early:\n" + log_path.read_text(encoding="utf-8", errors="replace"))
        if "ws connected to proxy" in log_path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.5)
    raise RuntimeError(
        "Tunnel did not connect within 30 seconds:\n"
        + log_path.read_text(encoding="utf-8", errors="replace")
    )


def wait_for_bridge(process: subprocess.Popen, ready_path: Path, robot_id: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("G1 bridge exited before declaring readiness")
        if ready_path.is_file():
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            if ready.get("ready") is True and ready.get("robot_id") == robot_id:
                print("[READY] G1 Zenoh subscriber declared; no warm-up action used")
                return
        time.sleep(0.1)
    raise RuntimeError("G1 bridge did not declare readiness within 30 seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visual", action="store_true")
    parser.add_argument("--open-basescan", action="store_true")
    parser.add_argument("--local-zenoh-router", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    private_key = None if args.dry_run else required("PRIVATE_KEY")
    payee = required("ROBO_PAYEE_ADDRESS")
    account = Account.from_key(private_key if private_key.startswith("0x") else "0x" + private_key) if private_key else None
    commit_sha = source_commit_sha()
    if not TUNNEL_BINARY.is_file():
        raise SystemExit(f"Tunnel binary missing: {TUNNEL_BINARY}")
    robot_id = os.environ.get("ROBOT_ID", f"unitree-g1-base-sepolia-{int(time.time())}")
    action_id = f"g1-active-inspection-{int(time.time())}"
    action_url = f"{FABRIC_API_BASE}/robots/{robot_id}/action"
    action_body = {
        "action": "inspect_target_sequence", "robot_id": robot_id, "action_id": action_id,
        "idempotency_key": action_id,
        "params": {"maxDurationSec": 18, "targets": ["left", "center", "right"], "speedScale": 1.0},
    }
    print(f"Evidence commit: {commit_sha}", flush=True)
    with tempfile.TemporaryDirectory(prefix="robopay_g1_base_sepolia_") as temporary:
        temp = Path(temporary); tunnel_config = temp / "tunnel.json"; zenoh_config = temp / "zenoh.json5"; log_path = temp / "tunnel.log"; ready_path = temp / "bridge-ready.json"
        tunnel_config.write_text(json.dumps({"robot_id": robot_id, "evm_payee_address": payee, "price": "$0.001", "network": NETWORK}), encoding="utf-8")
        zenoh_config.write_text('{"mode":"client","connect":{"endpoints":["tcp/127.0.0.1:7447"]}}', encoding="utf-8")
        tunnel_zenoh_config = zenoh_config
        if os.name == "nt":
            backend = windows_tunnel_backend()
            tunnel_zenoh_config = temp / f"zenoh-{backend}.json5"
            endpoint = "host.docker.internal" if backend == "docker" else wsl_host_address()
            tunnel_zenoh_config.write_text(
                json.dumps({"mode": "client", "connect": {"endpoints": [f"tcp/{endpoint}:7447"]}}),
                encoding="utf-8",
            )
            print(f"[TUNNEL] Windows backend: {backend}", flush=True)
        router_session = None
        if args.local_zenoh_router:
            router_session = zenoh.open(zenoh.Config.from_json5(
                '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
                '"listen":{"endpoints":["tcp/0.0.0.0:7447"]}}'
            ))
        clean_env = os.environ.copy()
        for secret_name in ("PRIVATE_KEY", "EVM_PRIVATE_KEY", "BASE_SEPOLIA_PRIVATE_KEY"):
            clean_env.pop(secret_name, None)
        bridge_env = clean_env.copy(); bridge_env.update({
            "PYTHONPATH": str(PACKAGE_ROOT), "ZENOH_CONFIG": str(zenoh_config),
            "ROBOT_ID": robot_id, "UNITREE_G1_READY_FILE": str(ready_path),
            "UNITREE_G1_MUJOCO_VIEWER": "true" if args.visual else "false",
            "UNITREE_G1_MUJOCO_VIEWER_HOLD_SECONDS": os.environ.get("UNITREE_G1_MUJOCO_VIEWER_HOLD_SECONDS", "2"),
            "UNITREE_G1_TARGET_HOLD_SECONDS": os.environ.get("UNITREE_G1_TARGET_HOLD_SECONDS", "1"),
            "UNITREE_G1_VIEWER_START_HOLD_SECONDS": os.environ.get("UNITREE_G1_VIEWER_START_HOLD_SECONDS", "4"),
        })
        bridge = subprocess.Popen(
            [sys.executable, "-m", "g1_inspection_bridge.bridge"], cwd=PACKAGE_ROOT,
            env=bridge_env, stdout=None if args.visual else subprocess.DEVNULL,
            stderr=None if args.visual else subprocess.STDOUT,
        )
        wait_for_bridge(bridge, ready_path, robot_id)
        tunnel_env = clean_env.copy(); tunnel_env.update({
            "PROXY_WS_URL": PROXY_WS_URL, "FACILITATOR_URL": FACILITATOR_URL, "AIP_ENABLED": "false",
            "SKILL_CATALOG_PATH": str(SKILL_CATALOG), "ALLOWED_ACTIONS": "inspect_target_sequence,stop",
            "EXECUTION_TIMEOUT_SECONDS": "75", "ZENOH_CONFIG": str(zenoh_config),
        })
        log = log_path.open("w", encoding="utf-8")
        tunnel = start_tunnel(tunnel_config, tunnel_zenoh_config, tunnel_env, log)
        try:
            wait_for_tunnel(tunnel, log_path)
            discovery_response = requests.get(f"{FABRIC_API_BASE}/robots/{robot_id}/skills", timeout=45)
            discovery_response.raise_for_status(); discovery = discovery_response.json()
            if discovery.get("robot_id") != robot_id:
                raise RuntimeError(f"Robot discovery drift: {discovery}")
            robot_discovery = {
                "robot_id": discovery["robot_id"],
                "model": "Unitree G1 29-DoF",
                "scope": "simulator-only",
                "source": "Fabric skill-discovery response",
            }
            print("[DISCOVERY] Unitree G1 simulator-only")
            if {item.get("skill_id") for item in discovery.get("skills", [])} != {"inspect_target_sequence", "stop"}:
                raise RuntimeError(f"Skill discovery drift: {discovery}")
            if any(item.get("price_usdc") != "0.001" for item in discovery["skills"]):
                raise RuntimeError(f"Price discovery drift: {discovery}")
            print("[DISCOVERY] inspect_target_sequence, stop @ 0.001 USDC")
            unpaid = requests.post(action_url, json=action_body, timeout=45)
            if unpaid.status_code != 402:
                raise RuntimeError(f"Expected initial HTTP 402, got {unpaid.status_code}: {unpaid.text}")
            print("[PAYMENT] unpaid action returned HTTP 402")
            requirements = decode_header(unpaid.headers.get("PAYMENT-REQUIRED") or unpaid.headers.get("Payment-Required"))
            accepted = requirements.get("accepts", [{}])[0]
            if accepted.get("payTo", "").lower() != payee.lower() or accepted.get("network") != NETWORK:
                raise RuntimeError(f"Payment requirements drift: {accepted}")
            authorization_window = int(accepted.get("maxTimeoutSeconds") or 0)
            if authorization_window < 300:
                raise RuntimeError(
                    "x402 authorization window is too short for deferred simulator settlement: "
                    f"{authorization_window}s"
                )
            print(f"[PAYMENT] exact EVM authorization window: {authorization_window} seconds")
            if args.dry_run:
                print("Dry run complete: no payment was signed or submitted", flush=True)
                return 0
            if account is None:
                raise RuntimeError("paid run requires a Base Sepolia account")
            client = x402ClientSync(); register_exact_evm_client(client, EthAccountSigner(account), networks=NETWORK)
            paid = x402_requests(client).post(action_url, json=action_body, timeout=120)
            if paid.status_code != 202 or paid.json().get("action_id") != action_id:
                payment_error = None
                encoded_error = paid.headers.get("PAYMENT-REQUIRED") or paid.headers.get("Payment-Required")
                if encoded_error:
                    try:
                        payment_error = decode_header(encoded_error)
                    except Exception as error:
                        payment_error = {"decode_error": str(error)}
                log.flush()
                tunnel_log = log_path.read_text(encoding="utf-8", errors="replace")
                failure_log = PACKAGE_ROOT / "artifacts" / "last_base_sepolia_tunnel_failure.log"
                failure_log.parent.mkdir(parents=True, exist_ok=True)
                failure_log.write_text(tunnel_log, encoding="utf-8")
                print(f"[PAYMENT VERIFICATION DETAIL] {json.dumps(payment_error, sort_keys=True)}", flush=True)
                print(f"[TUNNEL LOG] {failure_log}", flush=True)
                raise RuntimeError(f"Paid action was not accepted: HTTP {paid.status_code}: {paid.text}")
            print(f"[ACTION] first cold-start paid action accepted HTTP 202 action_id={action_id}")
            status_url = f"{FABRIC_API_BASE}/robots/{robot_id}/action/{action_id}/status"
            terminal = None; deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                response = requests.get(status_url, timeout=45)
                if response.status_code == 200 and response.json().get("state") in {"succeeded", "failed", "timeout", "settlement_failed"}:
                    terminal = response.json(); break
                time.sleep(2)
            if not terminal or terminal.get("state") != "succeeded" or not terminal.get("settled"):
                log.flush()
                tunnel_log = log_path.read_text(encoding="utf-8", errors="replace")
                failure_log = PACKAGE_ROOT / "artifacts" / "last_base_sepolia_tunnel_failure.log"
                failure_log.parent.mkdir(parents=True, exist_ok=True)
                failure_log.write_text(tunnel_log, encoding="utf-8")
                relevant = [
                    line for line in tunnel_log.splitlines()
                    if any(token in line.lower() for token in ("settle", "error", "fail", "warn"))
                ]
                if relevant:
                    print("[TUNNEL FAILURE DETAIL]", flush=True)
                    print("\n".join(relevant[-20:]), flush=True)
                print(f"[TUNNEL LOG] {failure_log}", flush=True)
                raise RuntimeError(f"Execution or settlement failed: {terminal}")
            print("[RESULT] correlated G1 execution state=succeeded")
            settlement = terminal.get("settlement") or {}; tx_hash = settlement.get("transaction") or settlement.get("txHash")
            if not tx_hash:
                raise RuntimeError(f"Settled action has no transaction hash: {terminal}")
            print(f"[SETTLEMENT] settled=true tx={tx_hash}")
            evidence = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "network": NETWORK,
                "source_commit": commit_sha,
                "payer": account.address, "payee": payee, "robot_id": robot_id, "action_id": action_id,
                "unpaid_http_status": unpaid.status_code, "paid_http_status": paid.status_code,
                "cold_start": True, "warmup_action": False,
                "visual_target_hold_seconds": float(bridge_env["UNITREE_G1_TARGET_HOLD_SECONDS"]) if args.visual else 0.0,
                "visual_final_hold_seconds": float(bridge_env["UNITREE_G1_MUJOCO_VIEWER_HOLD_SECONDS"]) if args.visual else 0.0,
                "visual_start_hold_seconds": float(bridge_env["UNITREE_G1_VIEWER_START_HOLD_SECONDS"]) if args.visual else 0.0,
                "robot_discovery": robot_discovery, "discovery": discovery,
                "terminal_status": terminal, "settlement": settlement,
                "transaction_hash": tx_hash, "basescan_url": f"https://sepolia.basescan.org/tx/{tx_hash}",
            }
            output = PACKAGE_ROOT / "artifacts" / f"base_sepolia_result_{int(time.time())}.json"
            output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(evidence, indent=2))
            if args.open_basescan:
                webbrowser.open(evidence["basescan_url"])
            return 0
        finally:
            stop_tunnel(tunnel)
            if bridge.poll() is None: bridge.terminate(); bridge.wait(15)
            log.close()
            if router_session is not None: router_session.close()


if __name__ == "__main__":
    raise SystemExit(main())
