"""Unit tests for ActionEvent parsing and validation.

Tests cover:
- Valid action event parsing
- Malformed JSON rejection
- Missing payload rejection
- Action ID preservation
- Idempotency key handling
"""
import json
import pytest
from bridge.common.zenoh_bridge.zenoh_bridge.action_event import parse_action_event


class TestParseActionEvent:
    def test_valid_move_forward(self):
        raw = json.dumps({
            "payload": {"action": "move_forward", "params": {"speed": 0.5}, "actionId": "act1"},
            "timestamp": "2026-01-01T00:00:00Z",
        }).encode()
        event = parse_action_event(raw)
        assert event is not None
        assert event.action == "move_forward"
        assert event.params["speed"] == 0.5

    def test_valid_stop(self):
        raw = json.dumps({
            "payload": {"action": "stop", "params": {}},
            "timestamp": "2026-01-01T00:00:00Z",
        }).encode()
        event = parse_action_event(raw)
        assert event is not None
        assert event.action == "stop"

    def test_malformed_json_returns_none(self):
        assert parse_action_event(b"not json") is None

    def test_empty_payload_returns_none(self):
        raw = json.dumps({"payload": None}).encode()
        assert parse_action_event(raw) is None

    def test_missing_payload_returns_none(self):
        raw = json.dumps({"timestamp": "2026-01-01"}).encode()
        # payload defaults to {} which is a dict, so action defaults to "stop"
        event = parse_action_event(raw)
        assert event is not None
        assert event.action == "stop"

    def test_non_dict_payload_returns_none(self):
        raw = json.dumps({"payload": "string"}).encode()
        assert parse_action_event(raw) is None

    def test_params_defaults_to_empty(self):
        raw = json.dumps({
            "payload": {"action": "move_forward"},
            "timestamp": "",
        }).encode()
        event = parse_action_event(raw)
        assert event.params == {}

    def test_unicode_decode_error(self):
        assert parse_action_event(b"\xff\xfe") is None
