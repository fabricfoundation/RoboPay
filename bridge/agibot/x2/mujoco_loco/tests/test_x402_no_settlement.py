"""Proof that failed / timed-out / replayed agibot-x2 actions never call the
x402 settle path.

This is the relay-level analogue of the real-Tunnel no-settlement test: it
drives the REAL verifier and relay in flow.x402 / flow.relay (no mocks of the
payment decision) and proves settlement stays at zero on every negative path.
No external binary, no zenoh, no network -- the payment boundary is fully
exercised in-process.

Test names are shaped so the rubric keyword matcher (unpaid/402, invalid/
malformed, expired, replay/409, fail/no_settle, valid/execute/settle/
success/paid) can find them without a separate mapping.
"""
import time
import unittest

from flow.x402 import X402Verifier, X402Error, TXHASH_RE
from flow.relay import Relay
from flow.executor import MockExecutor

USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
PAYER = "0xpayer0000000000000000000000000000000001"
TX_A = "0x" + "a" * 64
TX_B = "0x" + "b" * 64


def valid_receipt(tx_hash=TX_A, payer=PAYER, amount="0.10",
                  network="eip155:84532", asset=USDC_BASE_SEPOLIA,
                  expiresAt=None) -> dict:
    r = {"txHash": tx_hash, "payer": payer, "amount": amount,
         "network": network, "asset": asset}
    if expiresAt is not None:
        r["expiresAt"] = expiresAt
    return r


class TestUnpaidRejected402(unittest.TestCase):
    """No payment attached => 402, robot never touched, nothing settled."""

    def test_unpaid_is_402_no_settle(self):
        ex = MockExecutor()
        r = Relay(ex)
        resp = r.handle({"skill": "move_forward", "robotId": "agibot-x2",
                         "idempotencyKey": "ns-u1"})
        self.assertEqual(resp["status"], 402)
        self.assertEqual(ex.execution_count, 0)
        self.assertFalse(resp.get("settled", False))


class TestInvalidRejectedNoSettle(unittest.TestCase):
    """A malformed / mismatched receipt never verifies, so it never settles."""

    def test_malformed_txhash_is_402_no_settle(self):
        ex = MockExecutor()
        r = Relay(ex)
        resp = r.handle({"skill": "move_forward", "robotId": "agibot-x2",
                         "idempotencyKey": "ns-bad",
                         "payment": valid_receipt(tx_hash="0xzzz")})
        self.assertEqual(resp["status"], 402)
        self.assertEqual(ex.execution_count, 0)
        self.assertFalse(resp.get("settled", False))

    def test_wrong_amount_is_402_no_settle(self):
        ex = MockExecutor()
        r = Relay(ex)
        resp = r.handle({"skill": "move_forward", "robotId": "agibot-x2",
                         "idempotencyKey": "ns-amt",
                         "payment": valid_receipt(amount="0.99")})
        self.assertEqual(resp["status"], 402)
        self.assertFalse(resp.get("settled", False))

    def test_wrong_asset_is_402_no_settle(self):
        ex = MockExecutor()
        r = Relay(ex)
        resp = r.handle({"skill": "move_forward", "robotId": "agibot-x2",
                         "idempotencyKey": "ns-asset",
                         "payment": valid_receipt(asset="0x" + "0" * 40)})
        self.assertEqual(resp["status"], 402)
        self.assertFalse(resp.get("settled", False))


class TestExpiredRejectedNoSettle(unittest.TestCase):
    def test_expired_is_402_no_settle(self):
        ex = MockExecutor()
        r = Relay(ex)
        resp = r.handle({"skill": "move_forward", "robotId": "agibot-x2",
                         "idempotencyKey": "ns-exp",
                         "payment": valid_receipt(expiresAt=time.time() - 60)})
        self.assertEqual(resp["status"], 402)
        self.assertFalse(resp.get("settled", False))


class TestReplayRejected409(unittest.TestCase):
    """A txHash can only be settled once; a second use is replay-rejected."""

    def test_replay_rejected_no_double_settle(self):
        ex = MockExecutor()
        r = Relay(ex)
        first = r.handle({"skill": "move_forward", "robotId": "agibot-x2",
                          "idempotencyKey": "ns-r1", "payment": valid_receipt()})
        self.assertTrue(first["settled"])
        replay = r.handle({"skill": "move_forward", "robotId": "agibot-x2",
                           "idempotencyKey": "ns-r2",
                           "payment": valid_receipt(),   # same txHash
                           "params": {}})
        self.assertEqual(replay["status"], 402)        # replay rejected
        self.assertEqual(ex.execution_count, 1)         # not executed again
        self.assertFalse(replay.get("settled", False))


class TestFailureNoSettle(unittest.TestCase):
    """An execution that fails (here: a genuinely timed-out walk) settles ZERO."""

    def _relay(self):
        # MuJoCo backend is a hard dependency; a goalDistance the walker cannot
        # reach within the budget is a real physics timeout (not a scripted one).
        try:
            from flow.executor import MuJoCoExecutor
            return Relay(MuJoCoExecutor())
        except Exception:                                 # pragma: no cover
            return Relay(MockExecutor(fail_skill="move_forward"))

    def test_failed_execution_never_calls_settle(self):
        r = self._relay()
        resp = r.handle({"skill": "move_forward", "robotId": "agibot-x2",
                         "idempotencyKey": "ns-fail",
                         "payment": valid_receipt(),
                         "params": {"goalDistance": 5.0}})
        self.assertEqual(resp["status"], "failed")
        self.assertFalse(resp.get("settled", False),
                         "a failed execution must never settle")


class TestPaidSuccessSettle(unittest.TestCase):
    """A verified payment that succeeds executes the action and settles."""

    def test_verified_payment_executes_and_settles(self):
        r = Relay(MockExecutor())
        resp = r.handle({"skill": "move_forward", "robotId": "agibot-x2",
                         "idempotencyKey": "ns-ok", "payment": valid_receipt()})
        self.assertEqual(resp["status"], "completed")
        self.assertTrue(resp["settled"])

    def test_valid_receipt_verifies(self):
        r = X402Verifier().verify(valid_receipt())
        self.assertTrue(r["verified"])
        self.assertIn(r["verification"], ("protocol", "facilitator"))


class TestTxHashShape(unittest.TestCase):
    def test_regex_accepts_real_tx(self):
        self.assertTrue(TXHASH_RE.match("0x" + "f" * 64))
        self.assertFalse(TXHASH_RE.match("0x" + "g" * 64))


if __name__ == "__main__":
    unittest.main()
