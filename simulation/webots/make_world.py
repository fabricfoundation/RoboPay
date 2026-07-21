"""Generate a Webots world for a Go2 navigation task.

Mirrors go2_nav.build_scene_xml: same obstacle/goal parameterization, so
both simulators run identical layouts. Writes worlds/<name>.wbt and the
task JSON the controller reads.
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent

WORLD_TEMPLATE = """#VRML_SIM R2025a utf8
EXTERNPROTO "../protos/Go2.proto"

WorldInfo {{
  basicTimeStep 2
  coordinateSystem "ENU"
}}
Viewpoint {{
  orientation -0.2 0.4 0.89 2.0
  position 5 -8 6
}}
Background {{
  skyColor [0.3 0.5 0.7]
}}
DirectionalLight {{
  direction 0 0 -1
  intensity 2
  castShadows FALSE
}}
Solid {{
  name "floor"
  children [
    Shape {{
      appearance PBRAppearance {{
        baseColor 0.3 0.35 0.4
        roughness 1
        metalness 0
      }}
      geometry DEF FLOOR_PLANE Plane {{ size 40 40 }}
    }}
  ]
  boundingObject USE FLOOR_PLANE
  locked TRUE
}}
{obstacles}
Solid {{
  name "goal_marker"
  translation {gx} {gy} 0.01
  children [
    Shape {{
      appearance PBRAppearance {{
        baseColor 0.1 0.8 0.2
        transparency 0.5
        roughness 1
        metalness 0
      }}
      geometry Cylinder {{ radius 0.3 height 0.02 }}
    }}
  ]
}}
Go2 {{
  translation 0 0 0.30
  controller "<extern>"
  supervisor TRUE
}}
"""

OBSTACLE_TEMPLATE = """DEF OBSTACLE_{i} Solid {{
  name "obstacle_{i}"
  translation {x} {y} {z}
  children [
    Shape {{
      appearance PBRAppearance {{
        baseColor 0.6 0.3 0.2
        roughness 1
        metalness 0
      }}
      geometry DEF OBS_GEO_{i} {geometry}
    }}
  ]
  boundingObject USE OBS_GEO_{i}
  locked TRUE
}}
"""


def make_world(name, obstacles, goal):
    blocks = []
    for i, ob in enumerate(obstacles):
        if ob[0] == "box":
            _, x, y, sx, sy, sz = ob
            geometry = f"Box {{ size {2 * sx} {2 * sy} {2 * sz} }}"
            z = sz
        else:
            _, x, y, r, h = ob
            geometry = f"Cylinder {{ radius {r} height {2 * h} }}"
            z = h
        blocks.append(OBSTACLE_TEMPLATE.format(i=i, x=x, y=y, z=z, geometry=geometry))
    world = WORLD_TEMPLATE.format(obstacles="".join(blocks), gx=goal[0], gy=goal[1])
    (HERE / "worlds" / f"{name}.wbt").write_text(world)
    (HERE / "worlds" / f"{name}_task.json").write_text(
        json.dumps({"obstacles": obstacles, "goal": list(goal)}))
    return HERE / "worlds" / f"{name}.wbt"


# Primary sim-to-sim world: the competitor-aligned layout (test_nav LAYOUT_A)
LAYOUT_A = [["box", 2.5, 0, 0.5, 0.5, 0.5],
            ["box", 5.0, 1.5, 0.4, 0.4, 0.6],
            ["cylinder", 7.0, -1.0, 0.3, 0.5]]
GOAL_A = (10.0, 0.0)

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "go2_nav"
    path = make_world(name, LAYOUT_A, GOAL_A)
    print(f"wrote {path}")
