"""Goal-conditioned obstacle navigation for the Go2 on top of TrotController.

Pipeline:
  obstacle map + goal -> A* on an inflated occupancy grid (planned once,
  the world is static) -> pure-pursuit path following on the live robot
  pose from the physics state -> velocity command (vx, wz)
    -> TrotController -> joint torques

The scene (obstacle layout + goal) is parameterized, so changing either
produces a different plan and trajectory — nothing is replayed.

Metrics (all read back from the physics engine): distance to goal, path
completion, collision events (foot-ground contacts excluded), elapsed time.
"""

import heapq
import math
import pathlib

import numpy as np
import mujoco

from go2_gait import TrotController, LEG_NAMES

MENAGERIE_DIR = pathlib.Path(__file__).resolve().parents[1] / \
    "models" / "mujoco_menagerie" / "unitree_go2"

GOAL_RADIUS = 0.3       # m, episode succeeds inside this
ROBOT_RADIUS = 0.35     # m, trunk + leg span

# Planner / path following
V_MAX = 0.5
GRID_RES = 0.15         # m per cell
INFLATION = ROBOT_RADIUS + 0.1   # obstacle inflation for planning
LOOKAHEAD = 0.6         # m, pure-pursuit target distance
K_YAW = 2.0


def build_scene_xml(obstacles, goal):
    """Scene XML: official Go2 + floor + obstacles + goal marker.

    obstacles: list of ("box", x, y, sx, sy, sz) or ("cylinder", x, y, r, h).
    """
    parts = []
    for i, ob in enumerate(obstacles):
        if ob[0] == "box":
            _, x, y, sx, sy, sz = ob
            geom = f'<geom type="box" size="{sx} {sy} {sz}" rgba="0.6 0.3 0.2 1"/>'
            z = sz
        else:
            _, x, y, r, h = ob
            geom = f'<geom type="cylinder" size="{r} {h}" rgba="0.6 0.3 0.2 1"/>'
            z = h
        parts.append(f'<body name="obstacle_{i}" pos="{x} {y} {z}">{geom}</body>')
    return f"""<mujoco model="go2 nav">
  <include file="go2.xml"/>
  <worldbody>
    <light pos="0 0 3" dir="0 0 -1" directional="true"/>
    <geom name="floor" type="plane" size="20 20 0.05" rgba="0.3 0.35 0.4 1"/>
    {''.join(parts)}
    <body name="goal" pos="{goal[0]} {goal[1]} 0.01">
      <geom type="cylinder" size="{GOAL_RADIUS} 0.01" rgba="0.1 0.8 0.2 0.5"
        contype="0" conaffinity="0"/>
    </body>
  </worldbody>
</mujoco>"""


def load_scene(obstacles, goal):
    assets = {"go2.xml": (MENAGERIE_DIR / "go2.xml").read_bytes()}
    for f in (MENAGERIE_DIR / "assets").iterdir():
        assets[f"assets/{f.name}"] = f.read_bytes()
    return mujoco.MjModel.from_xml_string(build_scene_xml(obstacles, goal), assets)


def surface_clearance(pos, ob):
    """Distance from a point to an obstacle's true 2D footprint."""
    if ob[0] == "box":
        _, x, y, sx, sy, _ = ob
        d = np.abs(pos - (x, y)) - (sx, sy)
        return float(np.linalg.norm(np.maximum(d, 0.0)))
    _, x, y, r, _ = ob
    return max(float(np.linalg.norm(pos - (x, y))) - r, 0.0)


def plan_path(start, goal, obstacles):
    """A* over an 8-connected occupancy grid with inflated obstacles.

    Returns a list of world-frame waypoints ending at the goal, or None.
    """
    pts = [start, goal] + [ob[1:3] for ob in obstacles]
    lo = np.min(pts, axis=0) - 2.0
    hi = np.max(pts, axis=0) + 2.0
    nx, ny = (np.ceil((hi - lo) / GRID_RES)).astype(int)
    xs = lo[0] + (np.arange(nx) + 0.5) * GRID_RES
    ys = lo[1] + (np.arange(ny) + 0.5) * GRID_RES
    occupied = np.zeros((nx, ny), dtype=bool)
    for ob in obstacles:
        for i in range(nx):
            for j in range(ny):
                if not occupied[i, j] and \
                        surface_clearance(np.array([xs[i], ys[j]]), ob) < INFLATION:
                    occupied[i, j] = True

    def cell(p):
        return (int((p[0] - lo[0]) / GRID_RES), int((p[1] - lo[1]) / GRID_RES))

    start_c, goal_c = cell(start), cell(goal)

    def h(c):
        return math.hypot(c[0] - goal_c[0], c[1] - goal_c[1])

    frontier = [(h(start_c), start_c)]
    g_cost = {start_c: 0.0}
    came = {}
    while frontier:
        _, cur = heapq.heappop(frontier)
        if cur == goal_c:
            path = [cur]
            while path[-1] in came:
                path.append(came[path[-1]])
            return [np.array([xs[i], ys[j]]) for i, j in reversed(path)]
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == dj == 0:
                    continue
                nb = (cur[0] + di, cur[1] + dj)
                if not (0 <= nb[0] < nx and 0 <= nb[1] < ny) or occupied[nb]:
                    continue
                ng = g_cost[cur] + math.hypot(di, dj)
                if ng < g_cost.get(nb, float("inf")):
                    g_cost[nb] = ng
                    came[nb] = cur
                    heapq.heappush(frontier, (ng + h(nb), nb))
    return None


class NavPolicy:
    """A* plan once (static world), then pure-pursuit on the live pose."""

    def __init__(self, goal, obstacles):
        self.goal = np.asarray(goal, dtype=float)
        self.path = plan_path(np.zeros(2), self.goal, obstacles)
        if self.path is None:
            raise ValueError("no collision-free path exists for this layout")
        self._idx = 0   # monotonic progress along the path

    def command(self, x, y, yaw):
        """Returns ((vx, vy, wz), done)."""
        pos = np.array([x, y])
        dist = float(np.linalg.norm(self.goal - pos))
        if dist < GOAL_RADIUS * 0.8:
            return (0.0, 0.0, 0.0), True
        # advance to the first waypoint at least LOOKAHEAD away
        while self._idx < len(self.path) - 1 and \
                np.linalg.norm(self.path[self._idx] - pos) < LOOKAHEAD:
            self._idx += 1
        target = self.path[self._idx]
        err = math.atan2(*(target - pos)[::-1]) - yaw
        err = math.atan2(math.sin(err), math.cos(err))
        wz = max(-1.0, min(1.0, K_YAW * err))
        vx = V_MAX * max(0.0, math.cos(err)) * min(1.0, dist / 0.5)
        return (vx, 0.0, wz), False


def yaw_of(qpos):
    w, x, y, z = qpos[3:7]
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def tilt_of(qpos):
    w, x, y, z = qpos[3:7]
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    return max(abs(roll), abs(pitch))


def run_episode(obstacles, goal, max_time=90.0):
    """Run one navigation episode; all metrics come from the physics state."""
    model = load_scene(obstacles, goal)
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    ctrl = TrotController(model.opt.timestep)
    policy = NavPolicy(goal, obstacles)

    foot_ids = {model.geom(n).id for n in LEG_NAMES}
    floor_id = model.geom("floor").id
    robot_geoms = {g for g in range(model.ngeom)
                   if model.body(model.geom_bodyid[g]).rootid == model.body("base").id}

    start = data.qpos[:2].copy()
    start_dist = float(np.linalg.norm(np.asarray(goal) - start))
    trajectory = []
    active_pairs = set()
    collisions = 0
    path_len = 0.0
    prev_xy = start.copy()
    fell = False
    n_steps = int(max_time / model.opt.timestep)
    done = False

    for i in range(n_steps):
        if i % 10 == 0:   # policy at 50 Hz is plenty
            cmd, done = policy.command(data.qpos[0], data.qpos[1], yaw_of(data.qpos))
            ctrl.set_command(*cmd)
        data.ctrl[:] = ctrl.compute(data.qpos, data.qvel)
        mujoco.mj_step(model, data)

        pairs = set()
        for c in data.contact[:data.ncon]:
            g1, g2 = c.geom1, c.geom2
            rob = g1 in robot_geoms, g2 in robot_geoms
            if rob[0] == rob[1]:            # self-contact or env-env
                continue
            robot_g, other_g = (g1, g2) if rob[0] else (g2, g1)
            if robot_g in foot_ids and other_g == floor_id:
                continue                    # normal foot touchdown
            pairs.add((robot_g, other_g))
        collisions += len(pairs - active_pairs)
        active_pairs = pairs

        xy = data.qpos[:2]
        path_len += float(np.linalg.norm(xy - prev_xy))
        prev_xy = xy.copy()
        if i % 50 == 0:
            trajectory.append([round(float(xy[0]), 3), round(float(xy[1]), 3)])
        if data.qpos[2] < 0.15 or tilt_of(data.qpos) > 0.6:
            fell = True
            break
        if done and ctrl.standing and i % 10 == 0:
            break

    final_dist = float(np.linalg.norm(np.asarray(goal) - data.qpos[:2]))
    return {
        "goal": list(goal),
        "time_s": round(data.time, 2),
        "final_goal_distance_m": round(final_dist, 3),
        "path_completion": round(max(0.0, 1 - final_dist / start_dist), 3),
        "path_length_m": round(path_len, 2),
        "collisions": collisions,
        "fell": fell,
        "reached": bool(final_dist < GOAL_RADIUS and not fell),
        "trajectory": trajectory,
    }
