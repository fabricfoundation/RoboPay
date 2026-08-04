"""Reachy Mini — Fabric ActionEvent → MuJoCo task mapper."""
import sys
import os
import importlib.util

# Load action_event.py directly to avoid the __init__.py pulling in ROS2 geometry_msgs
try:
    from zenoh_bridge.action_event import ActionEvent
except ImportError:
    # Load action_event.py directly to avoid the __init__.py pulling in ROS2 geometry_msgs
    _ACTION_EVENT_FILE = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "common", "zenoh_bridge", "zenoh_bridge", "action_event.py"
    ))
    _spec = importlib.util.spec_from_file_location("action_event", _ACTION_EVENT_FILE)
    _mod  = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    ActionEvent = _mod.ActionEvent


# Registered canonical skills only. Aliases are deliberately not accepted by
# this execution boundary: every paid request must name the exact robot-profile
# skill that the Tunnel allowlist and result correlation record.
ACTION_TO_TASK = {
    "look_at_apple":     "object_tracking",
    "inspect_table":     "multi_object_inspection",
    "stop":              "safe_stop",
}

# Keep backward compat alias
ACTION_TO_PHASE = ACTION_TO_TASK


class ReachyMapper:
    """Maps Fabric ActionEvents to Reachy Mini task names."""

    def map(self, event: ActionEvent) -> str | None:
        """Return the task name to execute in MuJoCo for this action.

        Returns None for any action outside the registered skill set so the
        caller rejects it instead of running a default task. "stop" maps to
        "safe_stop", which halts without starting object tracking.
        """
        return ACTION_TO_TASK.get(event.action.lower())
