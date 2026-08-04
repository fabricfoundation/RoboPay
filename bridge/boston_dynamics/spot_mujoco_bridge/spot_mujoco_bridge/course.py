"""Simulator-independent specification for Spot's obstacle course."""

from __future__ import annotations

import math


COURSE_OBSTACLES = (
    {"name": "obstacle_center", "x": 1.40, "y": 0.00, "half_x": 0.26, "half_y": 0.32, "height": 0.80},
)
COURSE_GOAL = (2.30, 1.60)
# A single reference corridor used by both engines.  It begins aligned with
# the common initial yaw, widens around the red box, then reaches the marker.
COURSE_REFERENCE_ROUTE = (
    (0.55, 0.55),
    (1.85, 1.30),
    COURSE_GOAL,
)
COURSE_START_POSITION = (0.00, 0.00)
# Both engines start pointed toward the first shared corridor waypoint.  This
# avoids treating a model-specific, sharp initial turn as part of the task.
COURSE_START_YAW_RAD = math.pi / 4.0
