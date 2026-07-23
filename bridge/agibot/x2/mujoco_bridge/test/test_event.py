import json
from bridge.common.zenoh_bridge.zenoh_bridge.action_event import parse_action_event

def test_official_event_schema():
    raw = json.dumps({"payload": {"action": "move_forward", "params": {"speed": 0.4}}, "transaction_details": {}, "timestamp": "2026-07-23T00:00:00Z"}).encode()
    event = parse_action_event(raw)
    assert event.action == "move_forward" and event.params["speed"] == 0.4

def test_malformed_event_is_rejected():
    assert parse_action_event(b"not-json") is None
    assert parse_action_event(json.dumps({"payload": []}).encode()) is None
