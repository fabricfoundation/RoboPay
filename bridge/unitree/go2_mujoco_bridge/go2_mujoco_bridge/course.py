"""Simulator-independent geometry for the Unitree Go2 task."""

from __future__ import annotations

# The corridor passes between two physical boxes.  The boxes are deliberately
# staggered: a controller that walks straight on y=0 hits the first one, while
# a controller that drifts too far left hits the second one.
COURSE_OBSTACLES = (
    {"name": "right_block", "x": 1.35, "y": -0.45, "half_x": 0.18, "half_y": 0.18, "height": 0.55},
    {"name": "left_block", "x": 2.15, "y": 1.85, "half_x": 0.18, "half_y": 0.18, "height": 0.55},
)
COURSE_START_POSITION = (0.0, 0.0)
COURSE_GOAL = (3.25, 1.625)
COURSE_REFERENCE_ROUTE = (
    (1.20, 0.50),
    (1.90, 0.95),
    (2.60, 1.35),
    COURSE_GOAL,
)
# Start deliberately misaligned. Reaching the first waypoint therefore
# requires measured-yaw feedback and an observable turn, not straight replay.
COURSE_START_YAW_RAD = 0.0
