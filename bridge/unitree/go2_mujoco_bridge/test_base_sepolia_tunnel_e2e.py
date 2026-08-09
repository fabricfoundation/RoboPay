"""Live Base Sepolia x402 -> Tunnel -> Go2 MuJoCo settlement test.

The test intentionally uses the real public x402 test facilitator and a real
USDC ``transferWithAuthorization`` settlement on Base Sepolia.  It must only
be run with a funded *testnet* payer key supplied through ``PRIVATE_KEY``.
Neither the key nor the authorization payload is written to disk.
"""

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


def main() -> int:
    private_key = _required_env("PRIVATE_KEY")
    payee = _required_env("ROBO_PAYEE_ADDRESS")
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    if not TUNNEL_BINARY.is_file():
        raise SystemExit(f"Tunnel binary missing: {TUNNEL_BINARY}")

    account = Account.from_key(private_key)
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
        tunnel_log_path = temp / "tunnel.log"
        tunnel_log = tunnel_log_path.open("w", encoding="utf-8")
        bridge_env = os.environ.copy()
        for secret_name in ("PRIVATE_KEY", "EVM_PRIVATE_KEY"):
            bridge_env.pop(secret_name, None)
        bridge_env["PYTHONPATH"] = str(PACKAGE_ROOT) + os.pathsep + bridge_env.get("PYTHONPATH", "")
        bridge_env["ZENOH_CONFIG"] = str(zenoh_config)
        bridge_env["ROBOT_ID"] = robot_id
        bridge = subprocess.Popen(
            [sys.executable, "-m", "go2_mujoco_bridge.bridge"],
            cwd=PACKAGE_ROOT,
            env=bridge_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
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
        tunnel = subprocess.Popen(
            [str(TUNNEL_BINARY), "--config", str(tunnel_config)],
            cwd=ROOT,
            env=tunnel_env,
            stdout=tunnel_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_tunnel(tunnel, tunnel_log_path)

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
            requirements = _decode_header(
                unpaid.headers.get("PAYMENT-REQUIRED") or unpaid.headers.get("Payment-Required")
            )
            accepted = requirements.get("accepts", [{}])[0]
            if accepted.get("payTo", "").lower() != payee.lower():
                raise RuntimeError(f"Unexpected payment recipient: {accepted.get('payTo')}")
            if accepted.get("network") != NETWORK:
                raise RuntimeError(f"Unexpected payment network: {accepted.get('network')}")

            client = x402ClientSync()
            register_exact_evm_client(client, EthAccountSigner(account), networks=NETWORK)
            paid = x402_requests(client).post(action_url, json=action_body, timeout=120)
            if paid.status_code != 202:
                raise RuntimeError(f"Paid action failed: HTTP {paid.status_code}: {paid.text}")
            accepted_body = paid.json()
            if accepted_body.get("action_id") != request_id:
                raise RuntimeError(f"Action ID mismatch: {accepted_body}")

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
                "network": NETWORK,
                "payer": account.address,
                "payee": payee,
                "robot_id": robot_id,
                "request_id": request_id,
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
            print(json.dumps(evidence, indent=2))
            return 0
        finally:
            if tunnel.poll() is None:
                tunnel.terminate()
                tunnel.wait(timeout=15)
            if bridge.poll() is None:
                bridge.terminate()
                bridge.wait(timeout=15)
            tunnel_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
