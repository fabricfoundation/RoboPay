"""End-to-end paid-action execution through the REAL Go Tunnel + REAL MuJoCo bridge.

This is the acceptance-critical proof for criterion #3 (only a *successful*
execution settles): it drives the actual ``tunnel/`` binary (x402 payment gate
+ facilitator) and the actual ``bridge.FabricZenohBridge`` (real MuJoCo physics)
on a single Zenoh network, and asserts:

  * a paid ``pick_and_stack`` whose real MuJoCo run SUCCEEDS (object="cube")
    -> the Tunnel publishes to robot/tunnel/action, the bridge runs real
       physics, publishes a success result on robot/tunnel/result, and the
       Tunnel's x402 facilitator /settle IS called (status settled=true);
  * a paid ``pick_and_stack`` whose real MuJoCo run FAILS (object="unreachable")
    -> failure result -> the facilitator /settle is NEVER called.

A recording facilitator records every /settle it receives; an empty settle list
on the failure path is the proof. The success path must show a non-empty settle
list. Mirrors the winning RoboPay pattern exactly: payment verification AND
settlement live in the shared Go binary, never in Python.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from x402_harness import (
    FacilitatorHandler,
    LocalFabricProxy,
    http_get,
    http_post,
    launch_tunnel,
    payment_signature_from_402,
    start_facilitator,
)
from bridge import FabricZenohBridge, BridgeSettings


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]  # repository root (contains tunnel/ and bridge/)
ROBOT_ID = "cra-001-e2e"

ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
METRICS_TOPIC = "robot/cra-001/metrics"


def _settle_calls() -> list:
    return [path for path, _ in FacilitatorHandler.calls if path == "/settle"]


class BridgeExecuteTests(unittest.TestCase):
    def _poll_status(self, action_url: str, action_id: str, timeout: float = 120) -> dict:
        deadline = time.monotonic() + timeout
        last: dict = {}
        while time.monotonic() < deadline:
            status, _, body = http_get(f"{action_url}/{action_id}/status")
            if status == 200:
                last = json.loads(body)
                if last.get("state") not in ("pending", "reserved", "published"):
                    return last
            time.sleep(0.5)
        return last

    def _run_once(self, object_name: str):
        """Start real tunnel + real MuJoCo bridge, pay, return terminal status doc."""
        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator()
        bridge = None
        tunnel = None
        proxy.start()
        try:
            tunnel = launch_tunnel(
                ROOT,
                ROBOT_ID,
                proxy,
                facilitator,
                PACKAGE_ROOT / "skill-catalog.json",
                Path(tempfile.mkdtemp(prefix="cra_e2e_")),
                execution_timeout=120,
            )
            # Real bridge: subscribes to robot/tunnel/action, runs real MuJoCo,
            # publishes the correlated terminal result on robot/tunnel/result.
            bridge = FabricZenohBridge(
                BridgeSettings(
                    robot_id=ROBOT_ID,
                    action_topic=ACTION_TOPIC,
                    result_topic=RESULT_TOPIC,
                    metrics_topic=METRICS_TOPIC,
                )
            )
            # Let the bridge finish subscribing on the shared Zenoh network.
            time.sleep(1.0)

            action_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"

            # Challenge: unpaid -> 402 with PAYMENT-REQUIRED.
            status, headers, _ = http_post(
                action_url, {"action": "pick_and_stack", "params": {"object": object_name}}
            )
            self.assertEqual(status, 402, "unpaid action must be rejected with 402")

            aid = f"e2e-{object_name}-{uuid.uuid4().hex}"
            status, _, _ = http_post(
                action_url,
                {
                    "action": "pick_and_stack",
                    "robot_id": ROBOT_ID,
                    "action_id": aid,
                    "idempotency_key": aid,
                    "params": {"object": object_name},
                },
                {"PAYMENT-SIGNATURE": payment_signature_from_402(headers)},
            )
            self.assertEqual(status, 202, "verified payment must be accepted (202)")
            return self._poll_status(action_url, aid, timeout=120)
        finally:
            if bridge is not None:
                bridge.close()
            if tunnel is not None and tunnel.poll() is None:
                tunnel.terminate()
            proxy.close()
            facilitator.shutdown()
            facilitator.server_close()
            facilitator_thread.join(timeout=5)

    def test_success_settles(self) -> None:
        doc = self._run_once("cube")
        self.assertEqual(
            doc.get("state"), "succeeded", f"real MuJoCo cube pick must succeed, got {doc}"
        )
        self.assertTrue(
            doc.get("settled"), "a successful paid execution MUST settle through the facilitator"
        )
        self.assertNotEqual(
            _settle_calls(), [], "facilitator /settle must be called on success"
        )
        print("[BRIDGE E2E] cube -> success -> facilitator /settle called (settled)")

    def test_failure_does_not_settle_e2e(self) -> None:
        doc = self._run_once("unreachable")
        self.assertEqual(
            doc.get("state"), "failed", f"unreachable must fail, got {doc}"
        )
        self.assertFalse(
            doc.get("settled"), "a failed paid execution must NEVER settle"
        )
        self.assertEqual(
            _settle_calls(), [], "facilitator /settle must NOT be called on failure"
        )
        print("[BRIDGE E2E] unreachable -> failure -> 0 settle calls (not settled)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
