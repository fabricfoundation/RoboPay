"""Live Base Sepolia x402 -> Tunnel -> K1 MuJoCo -> settlement proof."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from eth_account import Account
from x402 import x402ClientSync
from x402.http.clients import x402_requests
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner


PACKAGE_ROOT = Path(__file__).resolve().parent
ROOT = PACKAGE_ROOT.parents[2]
SKILL_CATALOG = ROOT / "registry/vendors/booster/k1/booster.k1.mujoco-webots-active-inspection.v1/skill-catalog.json"
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


def wait_for_tunnel(process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Tunnel exited early:\n" + log_path.read_text(encoding="utf-8", errors="replace"))
        if "ws connected to proxy" in log_path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.5)
    raise RuntimeError("Tunnel did not connect within 30 seconds")


def wait_for_bridge(process: subprocess.Popen, ready_path: Path, robot_id: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("K1 bridge exited before declaring readiness")
        if ready_path.is_file():
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            if ready.get("ready") is True and ready.get("robot_id") == robot_id:
                print("[READY] K1 Zenoh subscriber declared; no warm-up action used")
                return
        time.sleep(0.1)
    raise RuntimeError("K1 bridge did not declare readiness within 30 seconds")


def main() -> int:
    private_key = required("PRIVATE_KEY")
    payee = required("ROBO_PAYEE_ADDRESS")
    account = Account.from_key(private_key if private_key.startswith("0x") else "0x" + private_key)
    if not TUNNEL_BINARY.is_file():
        raise SystemExit(f"Tunnel binary missing: {TUNNEL_BINARY}")
    robot_id = os.environ.get("ROBOT_ID", f"booster-k1-base-sepolia-{int(time.time())}")
    action_id = f"k1-active-inspection-{int(time.time())}"
    action_url = f"{FABRIC_API_BASE}/robots/{robot_id}/action"
    action_body = {
        "action": "inspect_target_sequence", "robot_id": robot_id, "action_id": action_id,
        "idempotency_key": action_id,
        "params": {"maxDurationSec": 18, "targets": ["left", "center", "right"], "speedScale": 1.0},
    }
    with tempfile.TemporaryDirectory(prefix="robopay_k1_base_sepolia_") as temporary:
        temp = Path(temporary); tunnel_config = temp / "tunnel.json"; zenoh_config = temp / "zenoh.json5"; log_path = temp / "tunnel.log"; ready_path = temp / "bridge-ready.json"
        tunnel_config.write_text(json.dumps({"robot_id": robot_id, "evm_payee_address": payee, "price": "$0.001", "network": NETWORK}), encoding="utf-8")
        zenoh_config.write_text('{"mode":"client","connect":{"endpoints":["tcp/127.0.0.1:7447"]}}', encoding="utf-8")
        clean_env = os.environ.copy(); clean_env.pop("PRIVATE_KEY", None); clean_env.pop("EVM_PRIVATE_KEY", None)
        bridge_env = clean_env.copy(); bridge_env.update({"PYTHONPATH": str(PACKAGE_ROOT), "ZENOH_CONFIG": str(zenoh_config), "ROBOT_ID": robot_id, "BOOSTER_K1_READY_FILE": str(ready_path)})
        bridge = subprocess.Popen([sys.executable, "-m", "k1_inspection_bridge.bridge"], cwd=PACKAGE_ROOT, env=bridge_env)
        wait_for_bridge(bridge, ready_path, robot_id)
        tunnel_env = clean_env.copy(); tunnel_env.update({
            "PROXY_WS_URL": PROXY_WS_URL, "FACILITATOR_URL": FACILITATOR_URL, "AIP_ENABLED": "false",
            "SKILL_CATALOG_PATH": str(SKILL_CATALOG), "ALLOWED_ACTIONS": "inspect_target_sequence,stop",
            "EXECUTION_TIMEOUT_SECONDS": "45", "ZENOH_CONFIG": str(zenoh_config),
        })
        log = log_path.open("w", encoding="utf-8")
        tunnel = subprocess.Popen([str(TUNNEL_BINARY), "--config", str(tunnel_config)], cwd=ROOT, env=tunnel_env, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_for_tunnel(tunnel, log_path)
            discovery_response = requests.get(f"{FABRIC_API_BASE}/robots/{robot_id}/skills", timeout=45)
            discovery_response.raise_for_status(); discovery = discovery_response.json()
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
            client = x402ClientSync(); register_exact_evm_client(client, EthAccountSigner(account), networks=NETWORK)
            paid = x402_requests(client).post(action_url, json=action_body, timeout=120)
            if paid.status_code != 202 or paid.json().get("action_id") != action_id:
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
                raise RuntimeError(f"Execution or settlement failed: {terminal}")
            print("[RESULT] correlated K1 execution state=succeeded")
            settlement = terminal.get("settlement") or {}; tx_hash = settlement.get("transaction") or settlement.get("txHash")
            if not tx_hash:
                raise RuntimeError(f"Settled action has no transaction hash: {terminal}")
            print(f"[SETTLEMENT] settled=true tx={tx_hash}")
            evidence = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "network": NETWORK,
                "payer": account.address, "payee": payee, "robot_id": robot_id, "action_id": action_id,
                "paid_http_status": paid.status_code, "cold_start": True, "warmup_action": False,
                "discovery": discovery, "terminal_status": terminal, "settlement": settlement,
                "transaction_hash": tx_hash, "basescan_url": f"https://sepolia.basescan.org/tx/{tx_hash}",
            }
            output = PACKAGE_ROOT / "artifacts" / f"base_sepolia_result_{int(time.time())}.json"
            output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(evidence, indent=2)); return 0
        finally:
            if tunnel.poll() is None: tunnel.terminate(); tunnel.wait(15)
            if bridge.poll() is None: bridge.terminate(); bridge.wait(15)
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
