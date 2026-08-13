"""Obstacle world builder for the Go2 navigation skill.

The menagerie Go2 scene has no obstacles, so the obstacle-navigation skill
cannot report *physics* contacts from it. This module injects static cylinder
geoms into a copy of the fetched ``scene.xml`` (placed next to the original so
relative ``<include>`` paths resolve) and the Go2 controller discovers them by
name prefix ``obs_*`` to count real MuJoCo contact pairs.

The same obstacle list drives the potential-field repulsion in
``go2_control.run_navigate_obstacle``, so planning and contact detection agree.
"""

import pathlib
from typing import List, Optional, Sequence, Tuple

# (x, y, radius) in metres — the static course used by the tier-1 demo.
#
# The course descends gently in -y. Each cylinder sits just inside the
# nominal waypoint-to-waypoint line (the straight line would clip its
# inscribed circle), so the controller's potential-field repulsion must
# actively steer the robot around it — verified positive minimum clearance.
OBSTACLES: List[Tuple[float, float, float]] = [
    (1.750, -0.548, 0.18),
    (2.968, -0.826, 0.18),
    (3.976, -1.047, 0.15),
]

# Cylinder height (metres). Tall enough to be a clear obstacle for the Go2
# (body height ~0.27 m above the hip line) without being physically tricky.
OBSTACLE_HEIGHT = 0.6

#: Name prefix used to discover obstacle geoms in the compiled model.
OBSTACLE_GEOM_PREFIX = "obs_"


def _obstacle_body(index: int, x: float, y: float, r: float) -> str:
    return (
        f'<body name="obstacle_{index}" pos="{x} {y} 0">'
        f'<geom name="{OBSTACLE_GEOM_PREFIX}{index}" type="cylinder" '
        f'size="{r} {r} {OBSTACLE_HEIGHT}" pos="0 0 {OBSTACLE_HEIGHT}" '
        f'mass="20" rgba="0.8 0.2 0.2 1"/>'
        f"</body>"
    )


def build_obstacle_world(
    scene_path: str,
    obstacles: Optional[Sequence[Tuple[float, float, float]]] = None,
    out_path: Optional[str] = None,
) -> str:
    """Write a copy of ``scene_path`` with static obstacle geoms injected.

    Returns the path of the generated world. The copy is written next to the
    original scene so the menagerie ``<include>`` files still resolve.
    """
    scene = pathlib.Path(scene_path)
    if not scene.exists():
        raise FileNotFoundError(
            f"scene.xml not found at {scene}; run simulation/setup.sh")
    text = scene.read_text(encoding="utf-8")
    if "<worldbody>" not in text:
        raise ValueError(f"{scene} has no <worldbody>; cannot inject obstacles")
    obs = list(obstacles) if obstacles is not None else OBSTACLES
    bodies = "".join(_obstacle_body(i, x, y, r) for i, (x, y, r) in enumerate(obs))
    text = text.replace("</worldbody>", bodies + "</worldbody>", 1)
    out = pathlib.Path(out_path) if out_path else scene.parent / "go2_obstacles.xml"
    out.write_text(text, encoding="utf-8")
    return str(out)
