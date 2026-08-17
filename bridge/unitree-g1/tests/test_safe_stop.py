"""Safe-stop / bounded-policy tests for unitree-g1 — REAL MuJoCo.

Criterion #5 (bounded policy + interruptible execution + safe stop) proven
with real physics, not mocks:

  * timeout scene    -> the step budget is exhausted before the drop zone and
                        the run STOPS (bounded policy), returns failure, never
                        settles.
  * stop skill       -> the run holds a stable pose and terminates cleanly
                        inside the budget (interruptible execution).
  * normal scene     -> pick_and_carry completes inside the budget, proving the
                        bound is not an arbitrary truncation.
  * replay           -> the same idempotency key is rejected, so a paid action
                        is never re-actuated or re-settled.

The same simulator the paid flow uses (MuJoCoSimulator) is driven here, so the
stop behaviour is the production stop behaviour.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from simulator import MuJoCoSimulator
    HAS_SIM = True
except Exception:  # pragma: no cover - MuJoCo absent on some platforms
    HAS_SIM = False


@pytest.mark.skipif(not HAS_SIM, reason="MuJoCo simulator not available")
class TestSafeStopReal:
    def test_timeout_stops_on_budget(self):
        """A clipped step budget stops execution (bounded policy) and the
        run returns failure without settling."""
        sim = MuJoCoSimulator()
        result = sim.pick_and_carry({"dropDistance": 8.0})
        assert result.success is False, "timeout must fail"
        steps = result.metrics.get("stepsUsed", 0)
        budget = result.metrics.get("stepBudget", 0)
        assert steps >= budget, "execution must stop when the budget is exhausted"

    def test_stop_completes_within_budget(self):
        sim = MuJoCoSimulator()
        result = sim.stop({})
        assert result.success is True, result.msg
        assert result.metrics.get("stepsUsed", 0) <= result.metrics.get("stepBudget", 0)

    def test_normal_scene_completes_within_budget(self):
        """The nominal scene completes inside the step budget, proving the
        bounded policy is not an arbitrary truncation."""
        sim = MuJoCoSimulator()
        result = sim.pick_and_carry({})
        assert result.success is True, result.msg
        assert result.metrics.get("stepsUsed", 0) <= result.metrics.get("stepBudget", 0)

    def test_custom_drop_distance_completes_within_budget(self):
        """A non-default (custom) drop distance that is still within reach
        completes inside the step budget."""
        sim = MuJoCoSimulator()
        result = sim.pick_and_carry({"dropDistance": 1.5})
        assert result.success is True, result.msg
        assert result.metrics.get("stepsUsed", 0) <= result.metrics.get("stepBudget", 0)

    def test_timeout_never_settles(self):
        from flow.executor import MuJoCoExecutor
        from flow.relay import Relay
        r = Relay(MuJoCoExecutor())
        resp = r.handle({"skill": "pick_and_carry", "robotId": "unitree-g1",
                         "idempotencyKey": "safestop-timeout",
                         "payment": {"txHash": "0x" + "a" * 64, "verified": True,
                                     "amount": "0.10", "network": "eip155:84532",
                                     "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                                     "payer": "0xpayer0000000000000000000000000000000001"},
                         "params": {"dropDistance": 8.0}})
        assert resp["status"] == "failed"
        assert resp["settled"] is False

    def test_replay_is_interruptible(self):
        """A replayed idempotency key is rejected: no second actuation, no
        second settlement."""
        from flow.executor import MuJoCoExecutor
        from flow.relay import Relay
        r = Relay(MuJoCoExecutor())
        first = r.handle({"skill": "pick_and_carry", "robotId": "unitree-g1",
                          "idempotencyKey": "safestop-replay",
                          "payment": {"txHash": "0x" + "a" * 64, "verified": True,
                                      "amount": "0.10", "network": "eip155:84532",
                                      "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                                      "payer": "0xpayer0000000000000000000000000000000001"}})
        assert first["settled"] is True
        replay = r.handle({"skill": "pick_and_carry", "robotId": "unitree-g1",
                           "idempotencyKey": "safestop-replay",
                           "payment": {"txHash": "0x" + "a" * 64, "verified": True,
                                       "amount": "0.10", "network": "eip155:84532",
                                       "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                                       "payer": "0xpayer0000000000000000000000000000000001"}})
        assert replay["status"] == "rejected"
        assert replay["reason"] == "duplicate_idempotency_key"
