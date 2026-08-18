"""Simulator-independent geometry for the Atlas humanoid task."""

from __future__ import annotations

COURSE_OBSTACLES = (
    {"name": "right_block", "x": 1.5, "y": -0.55, "half_x": 0.22, "half_y": 0.22, "height": 0.50},
    {"name": "left_block", "x": 2.5, "y": 1.65, "half_x": 0.22, "half_y": 0.22, "height": 0.50},
)
COURSE_START_POSITION = (0.0, 0.0)
COURSE_GOAL = (3.5, 1.2)
COURSE_REFERENCE_ROUTE = (
    (1.40, 0.30),
    (2.20, 0.70),
    (3.00, 1.00),
    COURSE_GOAL,
)
COURSE_START_YAW_RAD = 0.0
