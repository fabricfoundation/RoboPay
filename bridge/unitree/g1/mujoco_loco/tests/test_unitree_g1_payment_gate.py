"""Exercise unitree-g1's x402 payment gate through the real Go Tunnel binary.

Covers every point of PR #90's CHANGES_REQUESTED:

  * a reproducible unpaid 402 case          -> test_unpaid_malformed_rejected_fail_closed
  * a Tunnel-verified paid action           -> test_paid_action_publishes_and_settles
  * a correlated simulator result           -> result matched by action_id/params_hash
  * success-only settlement                 -> settle only on simulator success
  * failure / timeout left unsettled        -> test_failed_execution_does_not_settle,
                                                test_timeout_does_not_settle

The proxy speaks the same WebSocket envelope as Fabric, while the Tunnel
binary, its x402 middleware, its facilitator HTTP calls and its Zenoh action
handoff stay real. A simulator-side subscriber drives the real MuJoCo
executor and publishes the correlated result envelope, so the ActionEvent ->
execution -> correlated result -> settlement chain is exercised end to end.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

# Make tests/ importable when pytest collects as a package (tests/__init__.py
# exists, so x402_harness is not on the top-level sys.path automatically).
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

try:
    import zenoh
    HAS_ZENOH = True
except Exception:  # pragma: no cover - zenoh wheels only on Linux/macOS
    HAS_ZENOH = False

from x402_harness import (
    ActionBoundaryObserver,
    FacilitatorHandler,
    LocalFabricProxy,
    NETWORK,
    PAYEE,
    _TunnelConnection,
    find_tunnel_binary,
    http_get,
    http_post,
    payment_signature_from_402,
    start_facilitator,
)

# bridge/unitree-g1/tests -> bridge/unitree-g1 -> repo root
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[1]
SKILL_CATALOG = (
    ROOT
    / "registry/vendors/laok/unitree-g1-arm-001"
    / "laok.unitree-g1-arm-001.loco.v1/skill-catalog.json"
)
BRIDGE_PYTHONPATH = str(PACKAGE_ROOT)
ROBOT_ID = "unitree_g1_payment_gate"
ZENOH_TEST_PORT = int(os.environ.get("UNITREE_G1_PAYMENT_GATE_ZENOH_PORT", "7447"))
PRICE = "0.10"
ALLOWED_ACTIONS = "move_forward,navigate_obstacle,stop"
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
EXECUTION_TIMEOUT_SECONDS = "8"


def _server_frame(payload: bytes, opcode: int, final: bool) -> bytes:
    header = bytes([(0x80 if final else 0) | opcode])
    length = len(payload)
    if length < 126:
        return header + bytes([length]) + payload
    if length <= 0xFFFF:
        return header + bytes([126]) + length.to_bytes(2, "big") + payload
    return header + bytes([127]) + length.to_bytes(8, "big") + payload


class SimulatorSide:
    """Subscribes to the Tunnel's ActionEvent and publishes the correlated
    result envelope on the official result topic. Execution uses the real
    MuJoCo executor; the outcome (success/failure/silent) is selectable per
    test so the settlement contract can be asserted on every path."""

    def __init__(self, port: int, outcome: str = "success"):
        self.outcome = outcome
        config = zenoh.Config.from_json5(
            '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
            f'"connect":{{"endpoints":["tcp/127.0.0.1:{port}"]}}}}'
        )
        self.session = zenoh.open(config)
        self._lock = threading.Lock()
        self.executed_actions: list[dict] = []
        self.subscriber = self.session.declare_subscriber(
            ACTION_TOPIC, self._on_action
        )
        self.publisher = self.session.declare_publisher(RESULT_TOPIC)
        self.executor = None

    def _on_action(self, sample) -> None:
        event = json.loads(bytes(sample.payload.to_bytes()))
        with self._lock:
            self.executed_actions.append(event)
        action_id = event.get("action_id") or (event.get("payload") or {}).get("action_id")
        params = (event.get("payload") or {}).get("params") or {}
        skill_id = event.get("skill_id") or (event.get("payload") or {}).get("skill")
        if self.outcome == "silent":
            # Timeout path: no result is ever published.
            return
        if self.executor is None:
            from flow.executor import MuJoCoExecutor
            self.executor = MuJoCoExecutor()
        res = self.executor.execute(skill_id or "move_forward", params)
        if self.outcome == "failure":
            res = type(res)(False, "reviewer-forced-failure", res.metrics)
        result = {
            "action_id": action_id,
            "robot_id": event.get("robot_id"),
            "skill_id": event.get("skill_id"),
            "params_hash": event.get("params_hash"),
            "idempotency_key": event.get("idempotency_key"),
            "status": "success" if res.success else "failure",
            "error_code": "" if res.success else res.reason,
            "result": {"message": res.message, "metrics": res.metrics},
        }
        self.publisher.put(json.dumps(result).encode("utf-8"))

    def close(self) -> None:
        try:
            self.subscriber.undeclare()
            self.publisher.undeclare()
            self.session.close()
        except Exception:
            pass


@unittest.skipIf(not HAS_ZENOH, "zenoh not importable (Linux/macOS wheels only)")
class UnitreeG1PaymentGateTests(unittest.TestCase):
    def test_websocket_reader_reassembles_continuation_frames(self) -> None:
        reader, writer = socket.socketpair()
        try:
            writer.sendall(
                _server_frame(b'{"id":"paid-1",', opcode=1, final=False)
                + _server_frame(b'"status":202}', opcode=0, final=True)
            )
            opcode, payload = _TunnelConnection(reader)._read_message()
            self.assertEqual(opcode, 1)
            self.assertEqual(json.loads(payload), {"id": "paid-1", "status": 202})
        finally:
            reader.close()
            writer.close()

    def _start_stack(self, outcome: str = "success"):
        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator()
        observer = ActionBoundaryObserver(
            action_topic=ACTION_TOPIC, port=ZENOH_TEST_PORT
        )
        simulator = SimulatorSide(port=ZENOH_TEST_PORT, outcome=outcome)
        proxy.start()
        return proxy, facilitator, facilitator_thread, observer, simulator

    def _write_configs(self, temp_dir: Path) -> tuple[Path, Path]:
        config_path = temp_dir / "tunnel.json"
        config_path.write_text(
            json.dumps(
                {
                    "robot_id": ROBOT_ID,
                    "evm_payee_address": PAYEE,
                    "price": f"${PRICE}",
                    "network": NETWORK,
                }
            ),
            encoding="utf-8",
        )
        zenoh_config_path = temp_dir / "zenoh.json5"
        zenoh_config_path.write_text(
            json.dumps(
                {
                    "mode": "peer",
                    "scouting": {"multicast": {"enabled": False}},
                    "connect": {
                        "endpoints": [f"tcp/127.0.0.1:{ZENOH_TEST_PORT}"]
                    },
                }
            ),
            encoding="utf-8",
        )
        return config_path, zenoh_config_path

    def _start_tunnel(self, tunnel_binary, config_path, temp_dir, proxy, facilitator):
        child_env = os.environ.copy()
        child_env.update(
            {
                "PROXY_WS_URL": f"ws://127.0.0.1:{proxy.port}/ws",
                "FACILITATOR_URL": f"http://127.0.0.1:{facilitator.server_address[1]}",
                "AIP_ENABLED": "false",
                "ZENOH_CONFIG": str(temp_dir / "zenoh.json5"),
                "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                "ALLOWED_ACTIONS": ALLOWED_ACTIONS,
                "MAX_ACTION_DURATION_SECONDS": "30",
                "EXECUTION_TIMEOUT_SECONDS": EXECUTION_TIMEOUT_SECONDS,
                "PYTHONPATH": BRIDGE_PYTHONPATH,
            }
        )
        tunnel = subprocess.Popen(
            [tunnel_binary, "--config", str(config_path)],
            cwd=ROOT,
            env=child_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        return tunnel

    def _teardown(self, proxy, facilitator, facilitator_thread, observer, simulator, tunnel):
        if simulator is not None:
            simulator.close()
        if observer is not None:
            observer.close()
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
        proxy.close()
        facilitator.shutdown()
        facilitator.server_close()
        facilitator_thread.join(timeout=5)

    def _action_url(self, proxy) -> str:
        return f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"

    def _paid_post(self, action_url, unpaid_headers, action_id, params):
        return http_post(
            action_url,
            {
                "action": "move_forward",
                "robot_id": ROBOT_ID,
                "action_id": action_id,
                "idempotency_key": action_id,
                "params": params,
            },
            {"PAYMENT-SIGNATURE": payment_signature_from_402(unpaid_headers)},
        )

    def _poll_status(self, proxy, action_id, terminal_states, timeout=60) -> dict:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            status, _, body = http_get(
                f"http://127.0.0.1:{proxy.port}/action/{action_id}/status"
            )
            if status == 200:
                last = json.loads(body)
                if last.get("state") in terminal_states:
                    return last
            time.sleep(0.5)
        raise AssertionError(
            f"action {action_id} never reached {terminal_states}; last: {last}"
        )

    def test_unpaid_malformed_and_facilitator_rejected_requests_fail_closed(self) -> None:
        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with make build")

        proxy = facilitator = facilitator_thread = observer = simulator = None
        tunnel = None
        try:
            proxy, facilitator, facilitator_thread, observer, simulator = self._start_stack()
            with tempfile.TemporaryDirectory(prefix="unitree_g1_payment_gate_") as temp_dir:
                temp_dir = Path(temp_dir)
                config_path, _ = self._write_configs(temp_dir)
                tunnel = self._start_tunnel(
                    tunnel_binary, config_path, temp_dir, proxy, facilitator
                )
                self.assertIsNotNone(
                    proxy.wait_for_connection(15),
                    "real Tunnel did not connect to the local Fabric proxy",
                )
                action_url = self._action_url(proxy)

                # 1) Discovery: robot profile + skills (real Tunnel -> catalog).
                robot_status, _, robot_body = http_get(
                    f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}"
                )
                self.assertEqual(robot_status, 200)
                self.assertEqual(json.loads(robot_body)["robot_id"], ROBOT_ID)
                skills_status, _, skills_body = http_get(
                    f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/skills"
                )
                self.assertEqual(skills_status, 200)
                discovered = json.loads(skills_body)
                self.assertEqual(
                    {item["skill_id"] for item in discovered["skills"]},
                    {"move_forward", "navigate_obstacle", "stop"},
                )
                self.assertTrue(
                    all(item["price_usdc"] == PRICE for item in discovered["skills"])
                )

                # 2) Reproducible unpaid 402.
                unpaid_status, unpaid_headers, _ = http_post(
                    action_url, {"action": "move_forward", "robot_id": ROBOT_ID}
                )
                self.assertEqual(unpaid_status, 402)
                self.assertTrue(
                    "PAYMENT-REQUIRED" in {name.upper() for name in unpaid_headers},
                    "402 response must carry PAYMENT-REQUIRED",
                )

                # 3) Malformed request (params not an object) also fails closed.
                malformed_status, _, _ = http_post(
                    action_url,
                    {"action": "move_forward", "params": "not-an-object"},
                )
                self.assertEqual(malformed_status, 402)
                self.assertEqual(
                    FacilitatorHandler.calls,
                    [],
                    "unpaid requests must not verify or settle a payment",
                )

                # 4) Payment-shaped but facilitator-rejected (isValid:false).
                FacilitatorHandler.verify_response = {
                    "isValid": False,
                    "invalidReason": "reviewer-tampered-payment",
                }
                tampered_id = f"g1-tampered-{uuid.uuid4().hex}"
                rejected_status, _, _ = self._paid_post(
                    action_url, unpaid_headers, tampered_id, {}
                )
                self.assertEqual(rejected_status, 402)
                verify_calls = [
                    path for path, _ in FacilitatorHandler.calls if path == "/verify"
                ]
                settle_calls = [
                    path for path, _ in FacilitatorHandler.calls if path == "/settle"
                ]
                self.assertEqual(len(verify_calls), 1)
                self.assertEqual(settle_calls, [])
                self.assertFalse(
                    observer.action_received.wait(2),
                    "an isValid:false payment must not publish an ActionEvent",
                )
                self.assertEqual(
                    observer.snapshot(),
                    (0, 0),
                    "payment rejection must emit zero ActionEvents",
                )
                print("[UNITREE_G1 DISCOVERY] robot + skills + price: OK")
                print("[UNITREE_G1 PAYMENT GATE] unpaid/malformed/isValid:false -> HTTP 402, zero ActionEvents")
        finally:
            self._teardown(proxy, facilitator, facilitator_thread, observer, simulator, tunnel)

    def test_paid_action_publishes_and_settles(self) -> None:
        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with make build")

        proxy = facilitator = facilitator_thread = observer = simulator = None
        tunnel = None
        try:
            proxy, facilitator, facilitator_thread, observer, simulator = self._start_stack(outcome="success")
            with tempfile.TemporaryDirectory(prefix="unitree_g1_paid_") as temp_dir:
                temp_dir = Path(temp_dir)
                config_path, _ = self._write_configs(temp_dir)
                tunnel = self._start_tunnel(
                    tunnel_binary, config_path, temp_dir, proxy, facilitator
                )
                self.assertIsNotNone(
                    proxy.wait_for_connection(15),
                    "real Tunnel did not connect to the local Fabric proxy",
                )
                action_url = self._action_url(proxy)

                unpaid_status, unpaid_headers, _ = http_post(
                    action_url, {"action": "move_forward", "robot_id": ROBOT_ID}
                )
                self.assertEqual(unpaid_status, 402)

                paid_id = f"g1-paid-{uuid.uuid4().hex}"
                paid_status, _, _ = self._paid_post(
                    action_url, unpaid_headers, paid_id, {}
                )
                self.assertEqual(paid_status, 202, "verified payment -> 202 accepted")

                self.assertTrue(
                    observer.action_received.wait(10),
                    "a verified payment must publish an ActionEvent",
                )
                actions, executable = observer.snapshot()
                self.assertGreaterEqual(executable, 1)
                self.assertTrue(
                    any(a.get("action_id") == paid_id for a in actions),
                    "ActionEvent must be correlated by action_id",
                )

                # Terminal state: succeeded with settlement after the real
                # MuJoCo simulator reported success.
                status = self._poll_status(
                    proxy, paid_id, {"succeeded", "failed", "settlement_failed", "timeout"}
                )
                self.assertEqual(status["state"], "succeeded")
                self.assertTrue(status.get("settled"), "success must settle")
                settle_calls = [
                    path for path, _ in FacilitatorHandler.calls if path == "/settle"
                ]
                self.assertGreaterEqual(len(settle_calls), 1)
                print("[UNITREE_G1 PAID] verified payment -> ActionEvent -> correlated result -> settle: OK")
        finally:
            self._teardown(proxy, facilitator, facilitator_thread, observer, simulator, tunnel)

    def test_failed_execution_does_not_settle(self) -> None:
        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with make build")

        proxy = facilitator = facilitator_thread = observer = simulator = None
        tunnel = None
        try:
            proxy, facilitator, facilitator_thread, observer, simulator = self._start_stack(outcome="failure")
            with tempfile.TemporaryDirectory(prefix="unitree_g1_fail_") as temp_dir:
                temp_dir = Path(temp_dir)
                config_path, _ = self._write_configs(temp_dir)
                tunnel = self._start_tunnel(
                    tunnel_binary, config_path, temp_dir, proxy, facilitator
                )
                self.assertIsNotNone(proxy.wait_for_connection(15))
                action_url = self._action_url(proxy)

                unpaid_status, unpaid_headers, _ = http_post(
                    action_url, {"action": "move_forward", "robot_id": ROBOT_ID}
                )
                self.assertEqual(unpaid_status, 402)

                failed_id = f"g1-fail-{uuid.uuid4().hex}"
                paid_status, _, _ = self._paid_post(
                    action_url, unpaid_headers, failed_id, {"goalDistance": 5.0}
                )
                self.assertEqual(paid_status, 202)

                status = self._poll_status(
                    proxy, failed_id, {"failed", "succeeded", "settlement_failed", "timeout"}
                )
                self.assertEqual(status["state"], "failed")
                self.assertFalse(status.get("settled"), "failed execution must NOT settle")
                settle_calls = [
                    path for path, _ in FacilitatorHandler.calls if path == "/settle"
                ]
                self.assertEqual(settle_calls, [], "failure path must never call /settle")
                print("[UNITREE_G1 FAILURE] failed execution -> no settlement: OK")
        finally:
            self._teardown(proxy, facilitator, facilitator_thread, observer, simulator, tunnel)

    def test_timeout_does_not_settle(self) -> None:
        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with make build")

        proxy = facilitator = facilitator_thread = observer = simulator = None
        tunnel = None
        try:
            proxy, facilitator, facilitator_thread, observer, simulator = self._start_stack(outcome="silent")
            with tempfile.TemporaryDirectory(prefix="unitree_g1_timeout_") as temp_dir:
                temp_dir = Path(temp_dir)
                config_path, _ = self._write_configs(temp_dir)
                tunnel = self._start_tunnel(
                    tunnel_binary, config_path, temp_dir, proxy, facilitator
                )
                self.assertIsNotNone(proxy.wait_for_connection(15))
                action_url = self._action_url(proxy)

                unpaid_status, unpaid_headers, _ = http_post(
                    action_url, {"action": "move_forward", "robot_id": ROBOT_ID}
                )
                self.assertEqual(unpaid_status, 402)

                timeout_id = f"g1-timeout-{uuid.uuid4().hex}"
                paid_status, _, _ = self._paid_post(
                    action_url, unpaid_headers, timeout_id, {}
                )
                self.assertEqual(paid_status, 202)

                # No simulator result -> tunnel timeout after
                # EXECUTION_TIMEOUT_SECONDS -> never settles.
                status = self._poll_status(
                    proxy, timeout_id, {"timeout", "failed", "succeeded", "settlement_failed"},
                    timeout=45,
                )
                self.assertEqual(status["state"], "timeout")
                self.assertFalse(status.get("settled"), "timeout must NOT settle")
                settle_calls = [
                    path for path, _ in FacilitatorHandler.calls if path == "/settle"
                ]
                self.assertEqual(settle_calls, [], "timeout path must never call /settle")
                print("[UNITREE_G1 TIMEOUT] no simulator result -> timeout -> no settlement: OK")
        finally:
            self._teardown(proxy, facilitator, facilitator_thread, observer, simulator, tunnel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
