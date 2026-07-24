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


ACTION_TO_TASK = {
    "look_at":           "object_tracking",
    "look_at_apple":     "object_tracking",
    "object_tracking":   "object_tracking",
    "inspect_table":     "multi_object_inspection",
    "inspect_scene":     "multi_object_inspection",
    "track":             "object_tracking",
    "wave":              "object_tracking",
    "express_happiness": "object_tracking",
    "pick_and_place":    "object_tracking",  # fallback — robot has no arms
    "door_open":         "object_tracking",  # fallback
    "stop":              "object_tracking",
}

# Keep backward compat alias
ACTION_TO_PHASE = ACTION_TO_TASK


class ReachyMapper:
    """Maps Fabric ActionEvents to Reachy Mini task names."""

    def map(self, event: ActionEvent) -> str:
        """Return the task name to execute in MuJoCo for this action."""
        return ACTION_TO_TASK.get(event.action.lower(), "object_tracking")
