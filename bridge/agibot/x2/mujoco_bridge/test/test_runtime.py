import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from x2.runtime import X2Runtime

def test_success_is_correlated_and_replay_is_rejected():
    published = []; rt = X2Runtime("agibot-x2-sim-001", lambda a, p: {"action": a}, published.append)
    event = {"payload": {"actionId": "a1", "robotId": "agibot-x2-sim-001", "idempotencyKey": "k1", "action": "move_forward"}, "transaction_details": {"payment_payload": {"verified": True}}}
    assert rt.handle(event)["status"] == "SUCCESS"; assert rt.handle(event)["status"] == "REPLAY_REJECTED"; assert len(published) == 2

def test_missing_payment_never_executes():
    called = []; published = []; rt = X2Runtime("agibot-x2-sim-001", lambda a, p: called.append(a), published.append)
    out = rt.handle({"payload": {"actionId": "a1", "robotId": "agibot-x2-sim-001", "idempotencyKey": "k1", "action": "move_forward"}, "transaction_details": {}})
    assert out["status"] == "FAILED" and not called
