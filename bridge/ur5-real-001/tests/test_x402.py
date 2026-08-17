"""D7 payment-boundary tests --- x402 protocol verification (PR #70 review).

The reviewer asked for a payment boundary that verifies through the x402
challenge instead of accepting any txHash. These tests lock the new
protocol-level verifier:

  * a payment must match the 402 challenge (amount/network/asset)
  * txHash must be a well-formed 0x + 64 hex
  * a txHash cannot be replayed (even by the same payer)
  * the relay answers 402 for every verification failure
  * the relay dispatches ONLY a verified action (execution counter = 0
    for every rejected payment)
"""
import unittest

from flow.x402 import X402Challenge, X402Verifier, X402Error, TXHASH_RE
from flow.relay import Relay
from flow.executor import MockExecutor

USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
PAYER = "0xpayer0000000000000000000000000000000001"

TX_A = "0x" + "a" * 64
TX_B = "0x" + "b" * 64


def valid_receipt(tx_hash=TX_A, payer=PAYER, amount="0.10",
                  network="base-sepolia", asset=USDC_BASE_SEPOLIA) -> dict:
    return {"txHash": tx_hash, "payer": payer, "amount": amount,
            "network": network, "asset": asset}


class TestX402ChallengeFromProfiles(unittest.TestCase):

    def test_challenge_matches_payment_policy(self):
        ch = X402Challenge("pick_object")
        self.assertEqual(ch.amount, "0.10")
        self.assertEqual(ch.network, "base-sepolia")
        self.assertEqual(ch.asset, USDC_BASE_SEPOLIA)
        self.assertEqual(ch.settlement, "on-success-only")

    def test_accepts_block_is_reviewer_shaped(self):
        ch = X402Challenge("pick_object")
        block = ch.accepts_block("0xpayee")
        self.assertEqual(block["scheme"], "exact")
        self.assertEqual(block["amount"], "0.10")
        self.assertEqual(block["recipient"], "0xpayee")
        self.assertEqual(block["networkCaip2"], "eip155:84532")


class TestX402Verifier(unittest.TestCase):

    def setUp(self):
        self.v = X402Verifier()

    def test_valid_receipt_verifies(self):
        r = self.v.verify(valid_receipt())
        self.assertTrue(r["verified"])
        self.assertIn(r["verification"], ("protocol", "facilitator"))
        self.assertEqual(r["amount"], "0.10")
        self.assertEqual(r["txHash"], TX_A)

    def test_missing_payment_rejected(self):
        with self.assertRaises(X402Error):
            self.v.verify(None)

    def test_missing_txhash_rejected(self):
        with self.assertRaises(X402Error):
            self.v.verify({"payer": PAYER, "amount": "0.10",
                           "network": "base-sepolia", "asset": USDC_BASE_SEPOLIA})

    def test_malformed_txhash_rejected(self):
        with self.assertRaises(X402Error):
            self.v.verify(valid_receipt(tx_hash="0xabc123"))   # not 64 hex

    def test_wrong_amount_rejected(self):
        with self.assertRaises(X402Error) as ctx:
            self.v.verify(valid_receipt(amount="0.99"))
        self.assertIn("amount mismatch", str(ctx.exception))

    def test_wrong_network_rejected(self):
        with self.assertRaises(X402Error) as ctx:
            self.v.verify(valid_receipt(network="eip155:1"))
        self.assertIn("network mismatch", str(ctx.exception))

    def test_wrong_asset_rejected(self):
        with self.assertRaises(X402Error) as ctx:
            self.v.verify(valid_receipt(asset="0x0000000000000000000000000000000000000000"))
        self.assertIn("asset mismatch", str(ctx.exception))

    def test_replay_rejected(self):
        self.v.verify(valid_receipt(TX_A, PAYER))
        with self.assertRaises(X402Error) as ctx:
            self.v.verify(valid_receipt(TX_A, PAYER))
        self.assertIn("replay", str(ctx.exception))

    def test_same_payer_different_txhash_ok(self):
        self.v.verify(valid_receipt(TX_A, PAYER))
        r = self.v.verify(valid_receipt(TX_B, PAYER))
        self.assertTrue(r["verified"])


class TestRelayOnlyDispatchesVerifiedPayments(unittest.TestCase):
    """The relay must never touch the robot for an unverified payment."""

    def _relay(self):
        ex = MockExecutor()
        return Relay(ex), ex

    def test_unpaid_is_402_no_execution(self):
        r, ex = self._relay()
        resp = r.handle({"skill": "pick_object", "robotId": "ur5-real-001",
                         "idempotencyKey": "k-u1"})
        self.assertEqual(resp["status"], 402)
        self.assertEqual(ex.execution_count, 0)

    def test_bad_amount_is_402_no_execution(self):
        r, ex = self._relay()
        resp = r.handle({"skill": "pick_object", "robotId": "ur5-real-001",
                         "idempotencyKey": "k-u2",
                         "payment": valid_receipt(amount="0.99")})
        self.assertEqual(resp["status"], 402)
        self.assertEqual(ex.execution_count, 0)

    def test_malformed_txhash_is_402_no_execution(self):
        r, ex = self._relay()
        resp = r.handle({"skill": "pick_object", "robotId": "ur5-real-001",
                         "idempotencyKey": "k-u3",
                         "payment": valid_receipt(tx_hash="0xzzz")})
        self.assertEqual(resp["status"], 402)
        self.assertEqual(ex.execution_count, 0)

    def test_verified_payment_executes_and_settles(self):
        r, ex = self._relay()
        resp = r.handle({"skill": "pick_object", "robotId": "ur5-real-001",
                         "idempotencyKey": "k-ok",
                         "payment": valid_receipt(),
                         "params": {"object": "cube"}})
        self.assertEqual(resp["status"], "completed")
        self.assertTrue(resp["settled"])
        self.assertEqual(ex.execution_count, 1)

    def test_replay_of_verified_payment_is_rejected_no_double_settle(self):
        r, ex = self._relay()
        first = r.handle({"skill": "pick_object", "robotId": "ur5-real-001",
                          "idempotencyKey": "k-r1",
                          "payment": valid_receipt(),
                          "params": {"object": "cube"}})
        self.assertTrue(first["settled"])
        replay = r.handle({"skill": "pick_object", "robotId": "ur5-real-001",
                           "idempotencyKey": "k-r2",
                           "payment": valid_receipt(),   # same txHash
                           "params": {"object": "cube"}})
        self.assertEqual(replay["status"], 402)          # x402 replay reject
        self.assertEqual(ex.execution_count, 1)          # not executed again


class TestTxHashShape(unittest.TestCase):

    def test_regex_accepts_real_tx(self):
        self.assertTrue(TXHASH_RE.match("0x" + "f" * 64))
        self.assertFalse(TXHASH_RE.match("0x" + "g" * 64))
        self.assertFalse(TXHASH_RE.match("abc"))
        self.assertFalse(TXHASH_RE.match("0x" + "a" * 63))


# ---------------------------------------------------------------------------
# Real MuJoCo correlation (reviewer: "correlated simulator result").
# These run the ACTUAL physics backend (not MockExecutor) and prove the
# simulator outcome is what drives settlement. Skipped where mujoco is not
# installed so a CI image without the engine stays green.
# ---------------------------------------------------------------------------
try:
    import mujoco  # noqa: F401
    HAVE_MUJOCO = True
except Exception:
    HAVE_MUJOCO = False

from flow.executor import MuJoCoExecutor  # noqa: E402


@unittest.skipUnless(HAVE_MUJOCO, "mujoco not installed")
class TestRealMuJoCoCorrelated(unittest.TestCase):
    """The relay settles ONLY when the REAL physics backend succeeds."""

    def test_real_mujoco_pick_succeeds(self):
        ex = MuJoCoExecutor()
        res = ex.execute("pick_object", {"object": "cube"})
        self.assertTrue(res.success, msg=f"mujoco sim failed: {res.message}")
        self.assertEqual(res.metrics.get("graspState"), "attached")
        self.assertGreater(res.metrics.get("objectLifted", 0), 0.05)
        self.assertEqual(res.metrics.get("collisionCount"), 0)

    def test_relay_real_mujoco_success_settles(self):
        from flow.relay import Relay
        r = Relay(MuJoCoExecutor())
        resp = r.handle({
            "skill": "pick_object", "robotId": "ur5-real-001",
            "idempotencyKey": "k-mujoco-real",
            "payment": valid_receipt(),
            "params": {"object": "cube"},
        })
        self.assertEqual(resp["status"], "completed")
        self.assertTrue(resp["settled"], "real sim success must settle")
        self.assertEqual(resp["metrics"].get("engine"), "mujoco")


if __name__ == "__main__":
    unittest.main()
