"""Mandatory no-settlement proof: real Tunnel + real x402 middleware.

Reviewer requirement: the failure tests must exercise the real Go router and
x402 payment middleware with a recording facilitator, inject simulator
failure and timeout, and assert ZERO settlement calls — the absence of
settlement must be an observed fact, not a comment.

Under the immediate accepted/pending contract the paid POST answers 202 with
the actionId; the terminal outcome (failed/timeout, settled=false) is then
observed on GET /action/<id>/status. Settlement is deferred and runs only
after simulator success, so these scenarios must record zero /settle calls::

    HTTP client -> local Fabric proxy -> real Tunnel (Gin + x402 verify)
    -> facilitator /verify -> 202 accepted/pending -> Zenoh ActionEvent
    -> failing/silent simulator -> status endpoint: failed/timeout
    -> NO /settle call, ever

It additionally proves durable, payment-bound idempotency end to end:
  * a replay of the same request after failure is 409 with no new actuation,
  * a byte-identical PAYMENT-SIGNATURE replay is 409 with no new actuation,
  * a full Tunnel process restart still rejects the replay (durable store)
    and still serves the terminal status for the original actionId.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

import zenoh

from test_e2e_paid_action import (
    ACTION_TOPIC,
    NETWORK,
    PAYEE,
    RESULT_TOPIC,
    LocalFabricProxy,
    _FacilitatorHandler,
    _find_tunnel_binary,
    _http_post,
    _payment_signature_from_402,
    _poll_action_status,
    _start_facilitator,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
SKILL_CATALOG_PATH = os.path.join(
    _ROOT,
    "registry",
    "vendors",
    "pollen-robotics",
    "reachy-mini",
    "pollen-robotics.reachy-mini.mujoco-webots-sim.v1",
    "skill-catalog.json",
)
ROBOT_ID = "reachy_mini_nosettle"
EXECUTION_TIMEOUT_SECONDS = "3"


class _InjectedSimulator:
    """Zenoh double for the MuJoCo bridge with injectable outcomes.

    mode == "fail":   publishes a correlated failure result for every action.
    mode == "silent": consumes actions and never publishes a result (timeout).
    """

    def __init__(self, listen_endpoint="tcp/127.0.0.1:7447"):
        conf = zenoh.Config.from_json5(
            '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
            f'"listen":{{"endpoints":["{listen_endpoint}"]}}}}'
        )
        self.session = zenoh.open(conf)
        self.mode = "fail"
        self.actions = []
        self.action_seen = threading.Event()
        self._result_pub = self.session.declare_publisher(RESULT_TOPIC)
        self._sub = self.session.declare_subscriber(ACTION_TOPIC, self._on_action)

    def _on_action(self, sample):
        event = json.loads(bytes(sample.payload.to_bytes()))
        self.actions.append(event)
        self.action_seen.set()
        if self.mode == "silent":
            return
        result = {
            "action_id": event.get("action_id", ""),
            "robot_id": event.get("robot_id", ""),
            "skill_id": event.get("skill_id", ""),
            "params_hash": event.get("params_hash", ""),
            "idempotency_key": event.get("idempotency_key", ""),
            "status": "failure",
            "execution_status": "FAILED",
            "result": {"error_code": "INJECTED_SIMULATOR_FAILURE"},
        }
        self._result_pub.put(json.dumps(result).encode())

    def close(self):
        try:
            self._sub.undeclare()
            self._result_pub.undeclare()
            self.session.close()
        except Exception:
            pass


def _settle_calls():
    return [payload for path, payload in _FacilitatorHandler.calls if path == "/settle"]


def _verify_calls():
    return [payload for path, payload in _FacilitatorHandler.calls if path == "/verify"]


class TestX402NoSettlement(unittest.TestCase):
    """Paid POST is accepted immediately; terminal failure/timeout never settles."""

    def test_failure_timeout_and_replay_never_settle(self):
        tunnel_binary = _find_tunnel_binary()
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with: make build")

        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = _start_facilitator()
        simulator = None
        tunnel_proc = None
        cfg_path = None
        zenoh_cfg_path = None
        store_dir = tempfile.mkdtemp(prefix="reachy_idem_")
        store_path = os.path.join(store_dir, "replay.json")

        def start_tunnel():
            child_env = os.environ.copy()
            child_env["PROXY_WS_URL"] = f"ws://127.0.0.1:{proxy.port}/ws"
            child_env["FACILITATOR_URL"] = f"http://127.0.0.1:{facilitator.server_address[1]}"
            child_env["AIP_ENABLED"] = "false"
            child_env["ZENOH_CONFIG"] = zenoh_cfg_path
            child_env["SKILL_CATALOG_PATH"] = SKILL_CATALOG_PATH
            child_env["ALLOWED_ACTIONS"] = "look_at_apple,inspect_table,stop"
            child_env["EXECUTION_TIMEOUT_SECONDS"] = EXECUTION_TIMEOUT_SECONDS
            child_env["IDEMPOTENCY_STORE_PATH"] = store_path
            proc = subprocess.Popen(
                [tunnel_binary, "--config", cfg_path],
                cwd=_ROOT,
                env=child_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            self.assertIsNotNone(
                proxy.wait_for_connection(15),
                "real Tunnel did not connect to the local Fabric proxy",
            )
            return proc

        try:
            proxy.start()
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix="reachy_nosettle_", delete=False
            ) as config_file:
                json.dump(
                    {
                        "robot_id": ROBOT_ID,
                        "evm_payee_address": PAYEE,
                        "price": "$0.001",
                        "network": NETWORK,
                    },
                    config_file,
                )
                cfg_path = config_file.name
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json5", prefix="reachy_zenoh_", delete=False
            ) as zenoh_file:
                json.dump(
                    {
                        "mode": "peer",
                        "scouting": {"multicast": {"enabled": False}},
                        "connect": {"endpoints": ["tcp/127.0.0.1:7447"]},
                    },
                    zenoh_file,
                )
                zenoh_cfg_path = zenoh_file.name

            simulator = _InjectedSimulator()
            tunnel_proc = start_tunnel()
            time.sleep(1.0)

            public_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"

            def status_url(action_id):
                return (
                    f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}"
                    f"/action/{action_id}/status"
                )

            # ── Scenario 1: simulator failure → 202, terminal failed, 0 /settle
            unpaid_status, unpaid_headers, _ = _http_post(
                public_url, {"action": "look_at_apple"}
            )
            self.assertEqual(unpaid_status, 402)

            simulator.mode = "fail"
            failed_request_id = f"nosettle-fail-{uuid.uuid4().hex}"
            failed_body = {
                "action": "look_at_apple",
                "robot_id": ROBOT_ID,
                "action_id": failed_request_id,
                "idempotency_key": failed_request_id,
                "params": {"target_object": "apple", "duration": 2.0},
            }
            status, _, body = _http_post(
                public_url,
                failed_body,
                {"PAYMENT-SIGNATURE": _payment_signature_from_402(unpaid_headers)},
            )
            response = json.loads(body)
            print(f"\n[NO-SETTLE] failure scenario status={status} body={response}")
            self.assertEqual(status, 202)
            self.assertEqual(response.get("state"), "pending")
            self.assertEqual(response.get("action_id"), failed_request_id)
            terminal = _poll_action_status(
                status_url(failed_request_id), {"failed", "timeout"}, timeout=30
            )
            print(f"[NO-SETTLE] failure terminal status={terminal}")
            self.assertEqual(terminal["state"], "failed")
            self.assertEqual(terminal.get("error_code"), "SIMULATOR_EXECUTION_FAILED")
            self.assertFalse(terminal["settled"])
            self.assertGreaterEqual(len(_verify_calls()), 1,
                                    "the real x402 middleware must have verified the payment")
            self.assertEqual(len(_settle_calls()), 0,
                             "simulator failure must produce ZERO settlement calls")
            self.assertEqual(len(simulator.actions), 1)

            # ── Scenario 2: replay after failure → 409, no new actuation ──
            status, _, body = _http_post(
                public_url,
                failed_body,
                {"PAYMENT-SIGNATURE": _payment_signature_from_402(unpaid_headers)},
            )
            response = json.loads(body)
            print(f"[NO-SETTLE] replay-after-failure status={status} body={response}")
            self.assertEqual(status, 409)
            self.assertEqual(response.get("error_code"), "REPLAY_DETECTED")
            self.assertEqual(len(simulator.actions), 1,
                             "replay after failure must not actuate the simulator again")
            self.assertEqual(len(_settle_calls()), 0)

            # ── Scenario 3: simulator timeout → terminal timeout, 0 /settle ─
            simulator.mode = "silent"
            timeout_request_id = f"nosettle-timeout-{uuid.uuid4().hex}"
            timeout_signature = _payment_signature_from_402(unpaid_headers)
            status, _, body = _http_post(
                public_url,
                {
                    "action": "look_at_apple",
                    "robot_id": ROBOT_ID,
                    "action_id": timeout_request_id,
                    "idempotency_key": timeout_request_id,
                    "params": {"target_object": "apple", "duration": 2.0},
                },
                {"PAYMENT-SIGNATURE": timeout_signature},
            )
            response = json.loads(body)
            print(f"[NO-SETTLE] timeout scenario status={status} body={response}")
            self.assertEqual(status, 202)
            terminal = _poll_action_status(
                status_url(timeout_request_id), {"failed", "timeout"}, timeout=30
            )
            print(f"[NO-SETTLE] timeout terminal status={terminal}")
            self.assertEqual(terminal["state"], "timeout")
            self.assertEqual(terminal.get("error_code"), "SIMULATOR_RESULT_TIMEOUT")
            self.assertFalse(terminal["settled"])
            self.assertEqual(len(_settle_calls()), 0,
                             "simulator timeout must produce ZERO settlement calls")
            self.assertEqual(len(simulator.actions), 2)

            # ── Scenario 4: byte-identical payment replay → 409 ───────────
            payment_replay_id = f"nosettle-payreplay-{uuid.uuid4().hex}"
            status, _, body = _http_post(
                public_url,
                {
                    "action": "look_at_apple",
                    "robot_id": ROBOT_ID,
                    "action_id": payment_replay_id,
                    "idempotency_key": payment_replay_id,
                    "params": {"target_object": "apple"},
                },
                {"PAYMENT-SIGNATURE": timeout_signature},
            )
            response = json.loads(body)
            print(f"[NO-SETTLE] payment replay status={status} body={response}")
            self.assertEqual(status, 409)
            self.assertEqual(response.get("error_code"), "PAYMENT_REPLAY_DETECTED")
            self.assertEqual(len(simulator.actions), 2,
                             "a replayed payment must not actuate the simulator again")
            self.assertEqual(len(_settle_calls()), 0)

            # ── Scenario 5: restart the Tunnel — replay is still 409 ──────
            stale_connection = proxy.wait_for_connection(1)
            tunnel_proc.terminate()
            try:
                tunnel_proc.wait(timeout=5)
            except Exception:
                tunnel_proc.kill()
            # Drop the dead tunnel's WebSocket so the proxy waits for the
            # restarted process instead of reusing a closed connection.
            if stale_connection is not None:
                proxy.detach(stale_connection)
            tunnel_proc = start_tunnel()
            time.sleep(1.0)

            status, _, body = _http_post(
                public_url,
                failed_body,
                {"PAYMENT-SIGNATURE": _payment_signature_from_402(unpaid_headers)},
            )
            response = json.loads(body)
            print(f"[NO-SETTLE] replay-after-restart status={status} body={response}")
            self.assertEqual(status, 409)
            self.assertEqual(response.get("error_code"), "REPLAY_DETECTED")
            self.assertEqual(len(simulator.actions), 2,
                             "restart + retry must not produce a second actuation")

            # The durable store also keeps serving the terminal status for
            # the original actionId across the restart.
            persisted = _poll_action_status(
                status_url(failed_request_id), {"failed", "timeout"}, timeout=10
            )
            self.assertEqual(persisted["state"], "failed")
            self.assertFalse(persisted["settled"])

            # ── Final invariant: zero settlements over the whole run ──────
            self.assertEqual(len(_settle_calls()), 0,
                             "no scenario in this test may ever settle the payment")
            print(f"[NO-SETTLE] verify_calls={len(_verify_calls())} settle_calls=0 — OK")
        finally:
            if simulator is not None:
                simulator.close()
            if tunnel_proc is not None:
                try:
                    tunnel_proc.terminate()
                    tunnel_proc.wait(timeout=2)
                except Exception:
                    tunnel_proc.kill()
            proxy.close()
            facilitator.shutdown()
            facilitator.server_close()
            facilitator_thread.join(timeout=5)
            for path in (cfg_path, zenoh_cfg_path, store_path):
                if path and os.path.exists(path):
                    os.unlink(path)
            if os.path.isdir(store_dir):
                try:
                    os.rmdir(store_dir)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
