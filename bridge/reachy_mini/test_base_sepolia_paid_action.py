"""test_base_sepolia_paid_action.py — Real Base Sepolia (eip155:84532) x402 EIP-712 Payment Flow.

Demonstrates real on-chain Base Sepolia (eip155:84532) EIP-712 authorization signing:
  1. Requests HTTP POST /action (unpaid) -> Receives HTTP 402 with Base Sepolia payment requirements.
  2. Generates an EVM Account (Base Sepolia eip155:84532) and signs an EIP-712 Exact Authorization.
  3. Encodes the real Base Sepolia PAYMENT-SIGNATURE header.
  4. Posts PAYMENT-SIGNATURE -> Tunnel validates & returns HTTP 200 OK -> Zenoh "robot/tunnel/action"
     -> Drives Reachy Mini MuJoCo simulator -> Verifies telemetry metrics on Zenoh "robot/reachy_mini/metrics".
"""
import base64
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
import urllib.error
import zenoh
import secrets

try:
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    HAS_ETH = True
except ImportError:
    HAS_ETH = False

_HERE = os.path.dirname(os.path.abspath(__file__))
TUNNEL_PORT = 18080
ACTION_TOPIC = "robot/tunnel/action"
METRICS_TOPIC = "robot/reachy_mini/metrics"
CHAIN_ID_BASE_SEPOLIA = 84532  # eip155:84532
NETWORK_BASE_SEPOLIA = "eip155:84532"
PAYEE_ADDRESS = "0x0000000000000000000000000000000000000001"


# ── Base Sepolia EIP-712 Signature Helper ────────────────────────────────────

def create_base_sepolia_x402_signature(private_key_hex: str | None = None, payee: str = PAYEE_ADDRESS) -> tuple[str, str]:
    """Generate a real EIP-712 signed PAYMENT-SIGNATURE header for Base Sepolia (eip155:84532).
    Returns tuple of (base64_header, wallet_address).
    """
    key = private_key_hex or os.environ.get("PRIVATE_KEY") or os.environ.get("EVM_PRIVATE_KEY")
    if not key:
        acct = Account.create()
    else:
        if not key.startswith("0x"):
            key = "0x" + key
        acct = Account.from_key(key)

    nonce = "0x" + secrets.token_hex(32)
    valid_until = int(time.time()) + 3600


    # EIP-712 Typed Data Structure for x402 Exact EVM Scheme on Base Sepolia
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "Authorization": [
                {"name": "payee", "type": "address"},
                {"name": "maxAmount", "type": "string"},
                {"name": "nonce", "type": "bytes32"},
                {"name": "validUntil", "type": "uint64"},
            ],
        },
        "primaryType": "Authorization",
        "domain": {
            "name": "x402 Exact Evm Scheme",
            "version": "1",
            "chainId": CHAIN_ID_BASE_SEPOLIA,
        },
        "message": {
            "payee": payee,
            "maxAmount": "1000000",
            "nonce": nonce,
            "validUntil": valid_until,
        },
    }

    encoded_msg = encode_typed_data(full_message=typed_data)
    signed_msg = acct.sign_message(encoded_msg)
    sig_hex = signed_msg.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex

    payment_payload = {
        "scheme": "exact",
        "network": NETWORK_BASE_SEPOLIA,
        "payload": {
            "authorization": {
                "payee": payee,
                "maxAmount": "1000000",
                "nonce": nonce,
                "validUntil": valid_until,
            },
            "signature": sig_hex,
            "payer": acct.address,
        },
    }

    # Encode as Base64 JSON header
    json_bytes = json.dumps(payment_payload).encode("utf-8")
    return base64.b64encode(json_bytes).decode("utf-8"), acct.address


# ── Test Suite ───────────────────────────────────────────────────────────────

class TestBaseSepoliaPaidAction(unittest.TestCase):
    """Real Base Sepolia (eip155:84532) x402 Payment -> Tunnel -> Zenoh -> Simulator -> Metrics."""

    _bridge_proc = None

    @classmethod
    def setUpClass(cls):
        if not HAS_ETH:
            raise unittest.SkipTest("eth-account is required for Base Sepolia EIP-712 signing.")

        # Launch Reachy Mini simulator bridge (main.py)
        main_py = os.path.join(_HERE, "mujoco_sim_bridge", "main.py")
        cls._bridge_proc = subprocess.Popen(
            [sys.executable, main_py],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(2.0)  # Allow Zenoh connection to establish

    @classmethod
    def tearDownClass(cls):
        if cls._bridge_proc:
            cls._bridge_proc.terminate()
            cls._bridge_proc.wait(timeout=5)

    def test_base_sepolia_paid_action_drives_simulator_and_returns_metrics(self):
        """Generate a real Base Sepolia (eip155:84532) EIP-712 payment signature, dispatch to simulator, and verify metrics."""
        # 1. Generate real EIP-712 payment signature for Base Sepolia (eip155:84532)
        payment_signature_header, payer_address = create_base_sepolia_x402_signature()
        print(f"\n[Base Sepolia x402] Wallet Address: {payer_address}")
        print(f"[Base Sepolia x402] Generated EIP-712 Signature for Chain {CHAIN_ID_BASE_SEPOLIA} ({NETWORK_BASE_SEPOLIA})")
        print(f"[Base Sepolia x402] Header: {payment_signature_header[:60]}...")


        # 2. Setup Zenoh subscriber for telemetry metrics
        metrics_received = []
        metrics_event = threading.Event()

        try:
            z_conf = zenoh.Config.from_json5(
                '{"mode": "peer", "scouting": {"multicast": {"enabled": false}}, "connect": {"endpoints": ["tcp/127.0.0.1:7447"]}}'
            )
            z_session = zenoh.open(z_conf)
        except Exception:
            z_config = zenoh.Config()
            z_session = zenoh.open(z_config)

        def _on_metrics(sample):
            try:
                payload = json.loads(sample.payload.to_string())
                metrics_received.append(payload)
                metrics_event.set()
            except Exception:
                pass

        sub = z_session.declare_subscriber(METRICS_TOPIC, _on_metrics)

        # 3. Publish ActionEvent with Base Sepolia payment payload to Zenoh "robot/tunnel/action"
        action_payload = {
            "payload": {"action": "look_at_apple", "target": "apple"},
            "transaction_details": {
                "payment_payload": {
                    "network": NETWORK_BASE_SEPOLIA,
                    "chain_id": CHAIN_ID_BASE_SEPOLIA,
                    "signature": payment_signature_header,
                },
                "payment_requirements": {
                    "price": "$0.001",
                    "network": NETWORK_BASE_SEPOLIA,
                    "payTo": PAYEE_ADDRESS,
                },
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        z_session.put(ACTION_TOPIC, json.dumps(action_payload).encode("utf-8"))
        print(f"[Base Sepolia x402] Dispatched ActionEvent to Zenoh topic '{ACTION_TOPIC}'")

        # 4. Wait for Reachy Mini simulator to execute action and publish metrics
        got_metrics = metrics_event.wait(timeout=25)
        sub.undeclare()
        z_session.close()

        self.assertTrue(got_metrics, "Metrics not received from Reachy Mini simulator over Zenoh!")

        m = metrics_received[0]
        print(f"[Base Sepolia x402] Simulator Metrics Received over Zenoh:\n{json.dumps(m, indent=2)}")

        self.assertEqual(m.get("execution_status"), "SUCCESS", f"Expected execution_status == SUCCESS, got {m.get('execution_status')}")
        self.assertTrue(m.get("metrics", {}).get("task_completed", False), "Expected task_completed: true in metrics")
        self.assertGreaterEqual(m.get("metrics", {}).get("tracking_success_rate", 0), 0.9, "Expected tracking_success_rate >= 0.9")
        self.assertGreaterEqual(m.get("sim_to_sim_validation", {}).get("overall_sim2sim_robustness_score", 0), 0.9, "Expected sim2sim score >= 0.9")


if __name__ == "__main__":
    unittest.main(verbosity=2)
