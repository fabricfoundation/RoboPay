"""Live Base Sepolia x402 -> real Tunnel -> ROS2 Reachy -> MuJoCo proof.

This is intentionally different from the synthetic ActionEvent tests.  The
request goes to the public Fabric proxy, the local Tunnel is the real compiled
Go binary, and the public x402 facilitator verifies and settles the payment on
Base Sepolia.  The paid POST returns 202 accepted/pending immediately; the
test then polls GET /action/<id>/status until the terminal state and only
accepts the run when it is "succeeded" with a real settlement receipt.

Required environment variables (the private key stays local):

    $env:PRIVATE_KEY="0x..."              # payer wallet, never commit this
    $env:ROBOT_ID="your-unique-robot-id"  # same id registered by this Tunnel
    $env:ROBO_PAYEE_ADDRESS="0x..."       # receiving wallet

Run with the ROS2 bridge already started by ``make ROBOT=reachy_mini
bridge-run``.  The payer wallet needs Base Sepolia USDC; the facilitator
submits the settlement transaction and returns its BaseScan hash.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests
import zenoh
from eth_account import Account
from x402 import x402ClientSync
from x402.http.clients import x402_requests
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner


ROOT = Path(__file__).resolve().parents[2]
SKILL_CATALOG = (
    ROOT
    / "registry/vendors/pollen-robotics/reachy-mini"
    / "pollen-robotics.reachy-mini.mujoco-webots-sim.v1/skill-catalog.json"
)
TUNNEL_BINARY = ROOT / "bin" / "tunnel"
ACTION_TOPIC = "robot/tunnel/action"
METRICS_TOPIC = "robot/reachy_mini/metrics"
RESULT_TOPIC = "robot/tunnel/result"
NETWORK = "eip155:84532"
FABRIC_API_BASE = os.environ.get(
    "FABRIC_API_BASE_URL", "https://api.fabric.foundation/api/core"
).rstrip("/")
PROXY_WS_URL = os.environ.get(
    "PROXY_WS_URL", "wss://api.fabric.foundation/api/core/ws/robot"
)
FACILITATOR_URL = os.environ.get("FACILITATOR_URL", "https://x402.org/facilitator")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing {name}. Set it locally; never paste the private key into chat.")
    return value


def _decode_header(value: str | None) -> dict:
    if not value:
        return {}
    return json.loads(base64.b64decode(value).decode("utf-8"))


def _zenoh_session() -> zenoh.Session:
    config = zenoh.Config.from_json5(
        '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
        '"connect":{"endpoints":["tcp/127.0.0.1:7447"]}}'
    )
    return zenoh.open(config)


def main() -> int:
    private_key = os.environ.get("PRIVATE_KEY") or os.environ.get("EVM_PRIVATE_KEY")
    if not private_key:
        raise SystemExit(
            "Missing PRIVATE_KEY/EVM_PRIVATE_KEY. Set it locally; never paste the private key into chat."
        )
    robot_id = _required_env("ROBOT_ID")
    payee = _required_env("ROBO_PAYEE_ADDRESS")
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    if not TUNNEL_BINARY.exists():
        raise SystemExit("Missing bin/tunnel. Build it first with: make build")

    account = Account.from_key(private_key)
    action_url = f"{FABRIC_API_BASE}/robots/{robot_id}/action"
    request_id = f"base-sepolia-reachy-{int(time.time())}"
    if os.environ.get("REACHY_INSPECT_TABLE") == "1":
        action_body = {
            "action": "inspect_table",
            "robot_id": robot_id,
            "action_id": request_id,
            "idempotency_key": request_id,
            "params": {
                "targets": ["apple", "croissant", "duck"],
                "per_target_duration": 3.0,
            },
        }
    else:
        action_body = {
            "action": "look_at_apple",
            "robot_id": robot_id,
            "action_id": request_id,
            "idempotency_key": request_id,
            "params": {
                "duration": 4.0,
                "target_object": "apple",
            },
        }

    with tempfile.TemporaryDirectory(prefix="robopay_base_sepolia_") as temp_dir:
        temp = Path(temp_dir)
        config_path = temp / "tunnel.json"
        config_path.write_text(
            json.dumps(
                {
                    "robot_id": robot_id,
                    "evm_payee_address": payee,
                    "price": os.environ.get("ROBO_PAY_PRICE", "$0.001"),
                    "network": NETWORK,
                }
            ),
            encoding="utf-8",
        )
        zenoh_config_path = temp / "zenoh.json5"
        zenoh_config_path.write_text(
            json.dumps(
                {
                    "mode": "peer",
                    "scouting": {"multicast": {"enabled": False}},
                    "connect": {"endpoints": ["tcp/127.0.0.1:7447"]},
                }
            ),
            encoding="utf-8",
        )
        tunnel_log_path = temp / "tunnel.log"
        tunnel_log = tunnel_log_path.open("w", encoding="utf-8")
        tunnel_env = os.environ.copy()
        # The Tunnel verifies/settles an already signed x402 authorization; it
        # never needs the payer's signing key.  Do not leak that key to either
        # long-running child process just because this parent used it to build
        # the payment header.
        for secret_name in ("PRIVATE_KEY", "EVM_PRIVATE_KEY"):
            tunnel_env.pop(secret_name, None)
        tunnel_env.update(
            {
                "PROXY_WS_URL": PROXY_WS_URL,
                "FACILITATOR_URL": FACILITATOR_URL,
                "AIP_ENABLED": "false",
                "ZENOH_CONFIG": str(zenoh_config_path),
                "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                "ALLOWED_ACTIONS": "look_at_apple,inspect_table,stop",
            }
        )
        tunnel = subprocess.Popen(
            [str(TUNNEL_BINARY), "--config", str(config_path)],
            cwd=ROOT,
            env=tunnel_env,
            stdout=tunnel_log,
            stderr=subprocess.STDOUT,
        )

        bridge_proc = None
        if os.environ.get("REACHY_BRIDGE_EXTERNAL") != "1":
            main_py = Path(__file__).resolve().parent / "mujoco_sim_bridge" / "main.py"
            bridge_env = os.environ.copy()
            for secret_name in ("PRIVATE_KEY", "EVM_PRIVATE_KEY"):
                bridge_env.pop(secret_name, None)
            bridge_env["QT_QPA_PLATFORM"] = "offscreen"
            bridge_env["ROBOT_ID"] = robot_id
            bridge_dir = Path(__file__).resolve().parent / "mujoco_sim_bridge"
            sim_dir = bridge_dir / "simulation"
            bridge_env["PYTHONPATH"] = f"{bridge_dir}{os.pathsep}{sim_dir}{os.pathsep}{bridge_env.get('PYTHONPATH', '')}"
            if os.path.isfile("/opt/webots/webots"):
                bridge_env["WEBOTS_EXE"] = "/opt/webots/webots"
            bridge_proc = subprocess.Popen(
                [sys.executable, str(main_py)],
                cwd=Path(__file__).resolve().parent,
                env=bridge_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )

        session = None
        metrics_sub = None
        result_sub = None
        metrics_event = threading.Event()
        result_event = threading.Event()
        metrics: list[dict] = []
        results: list[dict] = []
        try:
            time.sleep(3)
            if tunnel.poll() is not None:
                raise RuntimeError(tunnel_log_path.read_text(encoding="utf-8"))

            session = _zenoh_session()

            def on_metrics(sample):
                result = json.loads(bytes(sample.payload.to_bytes()))
                metrics.append(result)
                if result.get("correlation_id") == request_id:
                    metrics_event.set()

            def on_result(sample):
                result = json.loads(bytes(sample.payload.to_bytes()))
                results.append(result)
                if result.get("action_id") == request_id:
                    result_event.set()

            metrics_sub = session.declare_subscriber(METRICS_TOPIC, on_metrics)
            result_sub = session.declare_subscriber(RESULT_TOPIC, on_result)

            print(f"Payer: {account.address}")
            print(f"Payee: {payee}")
            print(f"Network: {NETWORK} (Base Sepolia)")
            print(f"Public action URL: {action_url}")

            unpaid = requests.post(action_url, json=action_body, timeout=45)
            if unpaid.status_code != 402:
                raise RuntimeError(
                    f"Expected real Fabric 402, got HTTP {unpaid.status_code}: {unpaid.text}"
                )
            required = _decode_header(
                unpaid.headers.get("PAYMENT-REQUIRED")
                or unpaid.headers.get("Payment-Required")
            )
            accepted = required.get("accepts", [{}])[0]
            print(
                "402 requirements: "
                f"asset={accepted.get('asset')} amount={accepted.get('amount') or accepted.get('maxAmountRequired')} "
                f"payTo={accepted.get('payTo')}"
            )

            client = x402ClientSync()
            register_exact_evm_client(
                client,
                EthAccountSigner(account),
                networks=NETWORK,
            )
            paid_session = x402_requests(client)
            paid = paid_session.post(
                action_url,
                json=action_body,
                headers={"Access-Control-Expose-Headers": "PAYMENT-RESPONSE"},
                timeout=90,
            )
            print(f"Paid response: HTTP {paid.status_code} body={paid.text}")

            # Immediate accepted/pending contract: the paid POST answers 202
            # with the actionId; settlement happens only after the simulator
            # succeeds and is retrieved from the status endpoint.
            if paid.status_code != 202:
                raise RuntimeError(f"Real paid request failed: HTTP {paid.status_code}")
            accepted_body = json.loads(paid.text)
            if accepted_body.get("action_id") != request_id:
                raise RuntimeError(
                    f"202 did not echo the actionId: {accepted_body}"
                )

            status_url = f"{FABRIC_API_BASE}/robots/{robot_id}/action/{request_id}/status"
            terminal = None
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                poll = requests.get(status_url, timeout=45)
                if poll.status_code == 200:
                    candidate = poll.json()
                    if candidate.get("state") in {
                        "succeeded", "failed", "timeout", "settlement_failed"
                    }:
                        terminal = candidate
                        break
                time.sleep(2)
            if terminal is None:
                raise RuntimeError("Status endpoint never reached a terminal state")
            print(f"Terminal status: {json.dumps(terminal, indent=2)}")
            if terminal.get("state") != "succeeded":
                raise RuntimeError(f"Action did not succeed: {terminal}")
            if not terminal.get("settled"):
                raise RuntimeError("Execution succeeded but the payment was not settled")
            settlement = terminal.get("settlement") or {}

            if os.environ.get("RATE_LIMIT_PROBE") == "1":
                probe_statuses = []
                for probe_number in range(1, 10):
                    probe = requests.post(action_url, json=action_body, timeout=45)
                    probe_statuses.append(probe.status_code)
                    print(f"Rate-limit probe {probe_number}: HTTP {probe.status_code} body={probe.text}")
                    if probe.status_code == 429:
                        break
                if 429 not in probe_statuses:
                    raise RuntimeError(
                        f"Expected HTTP 429 from the Tunnel rate limit, got {probe_statuses}"
                    )

            tx_hash = settlement.get("transaction") or settlement.get("txHash")
            if not tx_hash:
                raise RuntimeError("Settlement succeeded but returned no transaction hash")
            print(f"BaseScan: https://sepolia.basescan.org/tx/{tx_hash}")

            if not metrics_event.wait(90):
                raise RuntimeError("Paid ActionEvent did not produce correlated ROS2 metrics")
            if not result_event.wait(10):
                raise RuntimeError("Paid ActionEvent did not produce robot/tunnel/result")
            result = next(item for item in metrics if item.get("correlation_id") == request_id)
            result_envelope = next(item for item in results if item.get("action_id") == request_id)
            if result_envelope.get("status") != "success":
                raise RuntimeError(f"Tunnel result was not successful: {result_envelope}")
            if result.get("execution_status") != "SUCCESS":
                raise RuntimeError(f"Simulator did not succeed: {result}")
            sim_metrics = result.get("metrics", {})
            sim2sim = result.get("sim_to_sim_validation", {})
            if not sim_metrics.get("task_completed"):
                raise RuntimeError("MuJoCo task did not complete")
            if sim_metrics.get("tracking_success_rate", 0) < 0.9:
                raise RuntimeError("MuJoCo tracking success rate is below 0.9")
            if sim2sim.get("overall_sim2sim_robustness_score", 0) < 0.9:
                # Accept lower score if Webots is unavailable (e.g., CI runners)
                sims = sim2sim.get("simulators_evaluated", [])
                if "Webots" in sims:
                    raise RuntimeError("Sim-to-sim robustness score is below 0.9")
                elif sim2sim.get("overall_sim2sim_robustness_score", 0) < 0.5:
                    raise RuntimeError("MuJoCo-only sim2sim score is below 0.5")
            print("Correlated simulator result:")
            print(json.dumps(result, indent=2))
            print(
                "Sim2Sim proof: "
                f"{sim2sim.get('simulators_evaluated')} "
                f"score={sim2sim.get('overall_sim2sim_robustness_score')}"
            )
            # Persist the advertised evidence artifact (uploaded by the CI
            # base-sepolia-e2e job as bridge/reachy_mini/base_sepolia_result*.json).
            evidence = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "network": NETWORK,
                "payer": account.address,
                "payee": payee,
                "robot_id": robot_id,
                "action_url": action_url,
                "request_id": request_id,
                "action_body": action_body,
                "paid_http_status": paid.status_code,
                "paid_response": json.loads(paid.text) if paid.text else None,
                "terminal_status": terminal,
                "settlement": settlement,
                "transaction_hash": tx_hash,
                "basescan_url": f"https://sepolia.basescan.org/tx/{tx_hash}",
                "tunnel_result": result_envelope,
                "simulator_result": result,
            }
            evidence_path = (
                Path(__file__).resolve().parent
                / f"base_sepolia_result_{int(time.time())}.json"
            )
            evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            print(f"Evidence artifact written: {evidence_path}")
            print("LIVE BASE SEPOLIA E2E PASSED")
            return 0
        finally:
            if metrics_sub is not None:
                metrics_sub.undeclare()
            if result_sub is not None:
                result_sub.undeclare()
            if session is not None:
                session.close()
            if bridge_proc is not None:
                try:
                    bridge_proc.terminate()
                    bridge_proc.wait(timeout=2)
                except Exception:
                    bridge_proc.kill()
            if tunnel.poll() is None:
                tunnel.terminate()
                tunnel.wait(timeout=15)
            tunnel_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
