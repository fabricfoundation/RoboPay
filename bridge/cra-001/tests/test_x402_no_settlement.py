"""Prove the execution-gated Tunnel never settles on failure / timeout / replay.

The shared RoboPay Tunnel verifies payment synchronously (x402 facilitator
/verify) but defers the actual USDC settlement (/settle) until AFTER a
correlated, successful simulator result. This test drives the real Go binary
and asserts the four acceptance-critical invariants:

  * a paid action whose simulator result is FAILURE -> zero /settle calls;
  * a paid action with NO simulator result (timeout) -> zero /settle calls;
  * the same idempotency_key replayed                 -> HTTP 409 REPLAY_DETECTED,
                                                         zero /settle calls;
  * the same x402 payment payload replayed            -> HTTP 409 PAYMENT_REPLAY_DETECTED,
                                                         zero /settle calls.

A recording facilitator records every /settle it receives; an empty settle list
is the proof. These map directly to acceptance criteria #3 (only success
settles) and #4 (failure / timeout / replay never settle).
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
    InjectedFabricSimulator,
    LocalFabricProxy,
    http_get,
    http_post,
    launch_tunnel,
    payment_signature_from_402,
    start_facilitator,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
ROBOT_ID = "cra-001-no-settle"


def _settle_calls() -> list:
    return [path for path, _ in FacilitatorHandler.calls if path == "/settle"]


class NoSettlementTests(unittest.TestCase):
    def _start(self, with_simulator: bool = True):
        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator()
        simulator = InjectedFabricSimulator(robot_id=ROBOT_ID) if with_simulator else None
        tunnel = None
        proxy.start()
        try:
            tunnel = launch_tunnel(
                ROOT,
                ROBOT_ID,
                proxy,
                facilitator,
                PACKAGE_ROOT / "skill-catalog.json",
                Path(tempfile.mkdtemp(prefix="cra_no_settle_")),
            )
            action_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"
            return {
                "proxy": proxy,
                "facilitator": facilitator,
                "facilitator_thread": facilitator_thread,
                "simulator": simulator,
                "tunnel": tunnel,
                "action_url": action_url,
            }
        except Exception:
            if simulator is not None:
                simulator.close()
            if tunnel is not None and tunnel.poll() is None:
                tunnel.terminate()
            proxy.close()
            facilitator.shutdown()
            facilitator.server_close()
            facilitator_thread.join(timeout=5)
            raise

    def _stop(self, ctx) -> None:
        if ctx["simulator"] is not None:
            ctx["simulator"].close()
        if ctx["tunnel"].poll() is None:
            ctx["tunnel"].terminate()
        ctx["proxy"].close()
        ctx["facilitator"].shutdown()
        ctx["facilitator"].server_close()
        ctx["facilitator_thread"].join(timeout=5)

    def _challenge(self, action_url: str) -> dict:
        status, headers, _ = http_post(action_url, {"action": "pick_and_stack", "params": {"object": "cube"}})
        self.assertEqual(status, 402, "unpaid action must be rejected with 402")
        return headers

    def _poll_status(self, action_url: str, action_id: str, timeout: float = 12) -> dict:
        deadline = time.monotonic() + timeout
        last = {}
        while time.monotonic() < deadline:
            status, _, body = http_get(f"{action_url}/{action_id}/status")
            if status == 200:
                last = json.loads(body)
                if last.get("state") not in ("pending", "reserved", "published"):
                    return last
            time.sleep(0.3)
        return last

    def test_failure_does_not_settle(self) -> None:
        ctx = self._start(with_simulator=True)
        try:
            headers = self._challenge(ctx["action_url"])
            aid = f"fail-{uuid.uuid4().hex}"
            status, _, body = http_post(
                ctx["action_url"],
                {
                    "action": "pick_and_stack",
                    "robot_id": ROBOT_ID,
                    "action_id": aid,
                    "idempotency_key": aid,
                    "params": {"object": "unreachable"},
                },
                {"PAYMENT-SIGNATURE": payment_signature_from_402(headers)},
            )
            self.assertEqual(status, 202, "verified payment must be accepted (202)")
            doc = self._poll_status(ctx["action_url"], aid)
            self.assertIn(doc.get("state"), ("failed", "succeeded"), f"unexpected state {doc}")
            self.assertFalse(doc.get("settled"), "a failed execution must never settle")
            self.assertEqual(_settle_calls(), [], "facilitator /settle must not be called on failure")
            print("[NO-SETTLEMENT] failure result -> 0 settle calls")
        finally:
            self._stop(ctx)

    def test_timeout_does_not_settle(self) -> None:
        # No simulator: the Tunnel's execute-gated watcher must time out.
        ctx = self._start(with_simulator=False)
        try:
            headers = self._challenge(ctx["action_url"])
            aid = f"timeout-{uuid.uuid4().hex}"
            status, _, body = http_post(
                ctx["action_url"],
                {
                    "action": "pick_and_stack",
                    "robot_id": ROBOT_ID,
                    "action_id": aid,
                    "idempotency_key": aid,
                    "params": {"object": "cube"},
                },
                {"PAYMENT-SIGNATURE": payment_signature_from_402(headers)},
            )
            self.assertEqual(status, 202, "verified payment must be accepted (202)")
            # Wait past EXECUTION_TIMEOUT_SECONDS (5s) before polling.
            doc = self._poll_status(ctx["action_url"], aid, timeout=15)
            self.assertEqual(doc.get("state"), "timeout", f"expected timeout, got {doc}")
            self.assertFalse(doc.get("settled"), "a timed-out execution must never settle")
            self.assertEqual(_settle_calls(), [], "facilitator /settle must not be called on timeout")
            print("[NO-SETTLEMENT] no result (timeout) -> 0 settle calls")
        finally:
            self._stop(ctx)

    def test_idempotency_replay_rejected(self) -> None:
        ctx = self._start(with_simulator=True)
        try:
            headers = self._challenge(ctx["action_url"])
            key = f"idem-{uuid.uuid4().hex}"
            # First paid action: distinct payment signature, reserve succeeds.
            sig1 = payment_signature_from_402(headers)
            status1, _, _ = http_post(
                ctx["action_url"],
                {
                    "action": "pick_and_stack",
                    "robot_id": ROBOT_ID,
                    "action_id": key,
                    "idempotency_key": key,
                    "params": {"object": "cube"},
                },
                {"PAYMENT-SIGNATURE": sig1},
            )
            self.assertEqual(status1, 202)
            # Second paid action: SAME idempotency_key but a DIFFERENT payment.
            # The Tunnel must refuse with 409 REPLAY_DETECTED before settlement.
            sig2 = payment_signature_from_402(headers)
            status2, _, body2 = http_post(
                ctx["action_url"],
                {
                    "action": "pick_and_stack",
                    "robot_id": ROBOT_ID,
                    "action_id": key + "-dup",
                    "idempotency_key": key,
                    "params": {"object": "cube"},
                },
                {"PAYMENT-SIGNATURE": sig2},
            )
            self.assertEqual(status2, 409, "replayed idempotency key must be rejected")
            self.assertEqual(json.loads(body2).get("error_code"), "REPLAY_DETECTED")
            self.assertEqual(_settle_calls(), [], "replay must never settle")
            print("[NO-SETTLEMENT] replayed idempotency_key -> 409 REPLAY_DETECTED")
        finally:
            self._stop(ctx)

    def test_payment_replay_rejected(self) -> None:
        ctx = self._start(with_simulator=True)
        try:
            headers = self._challenge(ctx["action_url"])
            # Same x402 payment payload (same signature) used for two DIFFERENT
            # idempotency keys: the Tunnel binds replay protection to the
            # verified payment hash, so the second must be 409 PAYMENT_REPLAY_DETECTED.
            sig = payment_signature_from_402(headers)
            key1 = f"pay-{uuid.uuid4().hex}-1"
            status1, _, _ = http_post(
                ctx["action_url"],
                {
                    "action": "pick_and_stack",
                    "robot_id": ROBOT_ID,
                    "action_id": key1,
                    "idempotency_key": key1,
                    "params": {"object": "cube"},
                },
                {"PAYMENT-SIGNATURE": sig},
            )
            self.assertEqual(status1, 202)
            key2 = f"pay-{uuid.uuid4().hex}-2"
            status2, _, body2 = http_post(
                ctx["action_url"],
                {
                    "action": "pick_and_stack",
                    "robot_id": ROBOT_ID,
                    "action_id": key2,
                    "idempotency_key": key2,
                    "params": {"object": "cube"},
                },
                {"PAYMENT-SIGNATURE": sig},
            )
            self.assertEqual(status2, 409, "replayed payment payload must be rejected")
            self.assertEqual(json.loads(body2).get("error_code"), "PAYMENT_REPLAY_DETECTED")
            self.assertEqual(_settle_calls(), [], "replayed payment must never settle")
            print("[NO-SETTLEMENT] replayed payment payload -> 409 PAYMENT_REPLAY_DETECTED")
        finally:
            self._stop(ctx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
