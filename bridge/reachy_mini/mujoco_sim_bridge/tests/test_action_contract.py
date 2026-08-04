import unittest
from types import SimpleNamespace

from reachy_mini.action_contract import ActionContractError, validate_action_event


def event(action="look_at_apple", params=None, **overrides):
    values = {
        "action": action,
        "skill_id": action,
        "robot_id": "reachy-contract-test",
        "action_id": "action-1",
        "params_hash": "sha256:test",
        "idempotency_key": "action-1",
        "params": params if params is not None else {"target_object": "apple"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ReachyActionContractTests(unittest.TestCase):
    def assertRejected(self, action_event, code):
        with self.assertRaises(ActionContractError) as raised:
            validate_action_event(action_event, "reachy-contract-test")
        self.assertEqual(raised.exception.code, code)

    def test_registered_skills_are_accepted_with_bounded_params(self):
        self.assertEqual(
            validate_action_event(event(params={"target_object": "duck", "duration": 8}), "reachy-contract-test"),
            "look_at_apple",
        )
        self.assertEqual(
            validate_action_event(
                event(
                    "inspect_table",
                    {"targets": ["apple", "croissant"], "per_target_duration": 4},
                ),
                "reachy-contract-test",
            ),
            "inspect_table",
        )
        self.assertEqual(
            validate_action_event(event("stop", {}), "reachy-contract-test"),
            "stop",
        )

    def test_missing_or_mismatched_metadata_is_rejected(self):
        self.assertRejected(event(action_id=""), "MISSING_CORRELATION")
        self.assertRejected(event(skill_id="inspect_table"), "ACTION_SKILL_MISMATCH")
        self.assertRejected(event(robot_id="other-robot"), "WRONG_ROBOT")

    def test_unknown_action_and_unsafe_parameters_never_fall_through(self):
        self.assertRejected(event("object_tracking", {"target_object": "apple"}), "UNREGISTERED_ACTION")
        self.assertRejected(event(params={"target_object": "unknown"}), "INVALID_PARAMS")
        self.assertRejected(event(params={"target_object": "apple", "unexpected": True}), "INVALID_PARAMS")
        self.assertRejected(event("stop", {"duration": 1}), "INVALID_PARAMS")
        self.assertRejected(
            event("inspect_table", {"targets": ["apple", "apple"]}), "INVALID_PARAMS"
        )


if __name__ == "__main__":
    unittest.main()
