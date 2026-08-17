"""MuJoCo back end for the AgiBot X2 push-to-target task.

Robot model: `AgibotTech/agibot_x2_urdf` v1.3.0, `x2_ultra.xml`, loaded from
the upstream checkout unmodified except for the floating base. The task world
lives in `scene.xml.template` and is materialised into a scratch directory of
symlinks, so the upstream checkout is never written to.

Two properties of this model shape the code:

  * Its actuators are **torque** sources, so joints are driven by an explicit
    gravity-compensated PD law rather than by writing a setpoint to `ctrl`.
  * Its base is a free joint. It is removed structurally rather than pinned
    with a soft equality weld, because a compliant constraint gets dragged out
    of place and then the IK planner and the simulator disagree about where
    the robot is.

Unlike the two robots tried before this one, the model needs no collision
filtering: it reports zero self-contacts in the rest pose and its joints track
a PD command to within 0.0001 rad.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import mujoco
import numpy as np

from .base import (
    ARM_JOINTS_LEFT,
    ARM_JOINTS_RIGHT,
    END_EFFECTOR_BODY,
    HEAD_JOINTS,
    WAIST_JOINTS,
    Observation,
    SimEnv,
    urdf_to_mujoco,
)

_TEMPLATE = Path(__file__).with_name("scene.xml.template")

#: Name we give the de-floated copy of the upstream robot model.
_FIXED_MJCF = "x2_fixed_base.xml"

#: Geometry belonging to bodies with these prefixes counts as "the hand".
_HAND_PREFIXES = ("left_wrist_",)

#: Joint stiffness and damping for the PD law, verified to track a commanded
#: pose to 1e-4 rad on this model.
KP = 150.0
KD = 12.0

#: Joints the policy is allowed to command.
CONTROLLED = ARM_JOINTS_LEFT

#: Joints held at their rest pose so they do not sag into the workspace or
#: move the frame the IK planned against.
PARKED = ARM_JOINTS_RIGHT + WAIST_JOINTS + HEAD_JOINTS


def _description_dir() -> Path:
    """Locate the AgiBot X2 checkout, honouring an explicit override."""
    override = os.environ.get("X2_DESCRIPTION_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "x2" / "X2_URDF-v1.3.0"


class MujocoX2Env(SimEnv):
    """MuJoCo implementation of the push-to-target world."""

    control_dt = 0.01

    def __init__(
        self,
        puck_x: float,
        puck_y: float,
        goal_x: float,
        goal_y: float,
        surface_z: float = 0.85,
        table_half: float = 0.16,
        puck_radius: float = 0.035,
        puck_half_height: float = 0.022,
        puck_mass: float = 0.12,
        puck_friction: float = 0.45,
    ) -> None:
        self._puck_xy = np.array([puck_x, puck_y], dtype=float)
        self._goal_xy = np.array([goal_x, goal_y], dtype=float)
        self._surface_z = float(surface_z)
        self._table_half = float(table_half)
        self._puck_radius = float(puck_radius)
        self._puck_half_height = float(puck_half_height)
        self._puck_mass = float(puck_mass)
        self._puck_friction = float(puck_friction)
        self._puck_z = self._surface_z + self._puck_half_height

        self._scratch = Path(tempfile.mkdtemp(prefix="x2_scene_"))
        self._model = self._build_model()
        self._data = mujoco.MjData(self._model)
        self._renderer: mujoco.Renderer | None = None

        act = {
            mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_ACTUATOR, i): i
            for i in range(self._model.nu)
        }
        # URDF name -> (actuator id, qpos address, dof address)
        self._joint_map: dict[str, tuple[int, int, int]] = {}
        for urdf_name in CONTROLLED + PARKED:
            aid = act.get(urdf_to_mujoco(urdf_name))
            if aid is None:
                continue
            jid = int(self._model.actuator_trnid[aid][0])
            self._joint_map[urdf_name] = (
                aid,
                int(self._model.jnt_qposadr[jid]),
                int(self._model.jnt_dofadr[jid]),
            )
        if not self._joint_map:
            raise RuntimeError("no controllable joints resolved; check naming")

        self._ee = mujoco.mj_name2id(
            self._model, mujoco.mjtObj.mjOBJ_BODY, END_EFFECTOR_BODY
        )
        self._puck_body = mujoco.mj_name2id(
            self._model, mujoco.mjtObj.mjOBJ_BODY, "puck"
        )
        self._puck_qadr = int(
            self._model.jnt_qposadr[
                mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, "puck_free")
            ]
        )
        self._puck_geoms = {
            mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_GEOM, "puck_geom")
        }
        self._hand_geoms = self._geoms_under(_HAND_PREFIXES)
        self._support_geoms = self._geoms_under(("table",))
        self._substeps = max(1, round(self.control_dt / self._model.opt.timestep))
        self._command: dict[str, float] = {}

    # -- construction -----------------------------------------------------

    def _build_model(self) -> mujoco.MjModel:
        src = _description_dir()
        robot = src / "x2_ultra.xml"
        if not robot.is_file():
            raise FileNotFoundError(
                f"X2 description not found at {robot}. Clone "
                "AgibotTech/agibot_x2_urdf or set X2_DESCRIPTION_DIR."
            )
        for entry in src.iterdir():
            if entry.name != robot.name:
                (self._scratch / entry.name).symlink_to(entry)
        (self._scratch / _FIXED_MJCF).write_text(
            self._fixed_base_model(robot.read_text())
        )

        xml = _TEMPLATE.read_text()
        for key, value in (
            ("{{PUCK_X}}", f"{self._puck_xy[0]:.6f}"),
            ("{{PUCK_Y}}", f"{self._puck_xy[1]:.6f}"),
            ("{{PUCK_Z}}", f"{self._puck_z:.6f}"),
            ("{{GOAL_X}}", f"{self._goal_xy[0]:.6f}"),
            ("{{GOAL_Y}}", f"{self._goal_xy[1]:.6f}"),
            ("{{MARKER_Z}}", f"{self._surface_z + 0.0016:.6f}"),
            # The table spans from the floor plane up to the work surface.
            ("{{SURFACE_HALF}}", f"{(self._surface_z + 1.0) / 2.0:.6f}"),
            ("{{SURFACE_HALF_POS}}", f"{(self._surface_z - 1.0) / 2.0:.6f}"),
            ("{{TABLE_HALF}}", f"{self._table_half:.6f}"),
            ("{{PUCK_R}}", f"{self._puck_radius:.6f}"),
            ("{{PUCK_HZ}}", f"{self._puck_half_height:.6f}"),
            ("{{PUCK_MASS}}", f"{self._puck_mass:.6f}"),
            ("{{PUCK_FRICTION}}", f"{self._puck_friction:.6f}"),
        ):
            xml = xml.replace(key, value)
        scene = self._scratch / "scene_generated.xml"
        scene.write_text(xml)
        return mujoco.MjModel.from_xml_path(str(scene))

    @staticmethod
    def _fixed_base_model(xml: str) -> str:
        """Return the upstream MJCF with the floating base removed.

        The IK planner welds the base to plan against, so simulating a
        floating one would mean planning in one frame and executing in
        another. Deleting the joint is what actually pins it; a soft
        `<equality><weld>` is compliant and gets dragged out of place.
        """
        return "\n".join(
            line
            for line in xml.splitlines()
            if "<freejoint" not in line and 'type="free"' not in line
        ) + "\n"

    def _geoms_under(self, prefixes: tuple[str, ...]) -> set[int]:
        found: set[int] = set()
        for gid in range(self._model.ngeom):
            body = self._model.geom_bodyid[gid]
            name = (
                mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_BODY, body) or ""
            )
            if name.startswith(prefixes):
                found.add(gid)
        return found

    # -- SimEnv -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "mujoco"

    @property
    def goal(self) -> np.ndarray:
        return np.array([*self._goal_xy, self._puck_z], dtype=float)

    @property
    def puck_radius(self) -> float:
        return self._puck_radius

    @property
    def joint_limits(self) -> dict[str, tuple[float, float]]:
        limits: dict[str, tuple[float, float]] = {}
        for urdf_name, (aid, _, _) in self._joint_map.items():
            jid = int(self._model.actuator_trnid[aid][0])
            lo, hi = self._model.jnt_range[jid]
            limits[urdf_name] = (float(lo), float(hi))
        return limits

    def reset(self) -> Observation:
        mujoco.mj_resetData(self._model, self._data)
        adr = self._puck_qadr
        self._data.qpos[adr : adr + 3] = [*self._puck_xy, self._puck_z]
        self._data.qpos[adr + 3 : adr + 7] = [1.0, 0.0, 0.0, 0.0]
        self._data.qvel[:] = 0.0
        mujoco.mj_forward(self._model, self._data)
        self._command = {
            name: float(self._data.qpos[qadr])
            for name, (_, qadr, _) in self._joint_map.items()
        }
        return self.observe()

    def step(self, targets: dict[str, float]) -> Observation:
        self._command.update(targets)
        limits = self.joint_limits
        for _ in range(self._substeps):
            # Gravity-compensated PD. Without the feed-forward term the
            # proportional error alone has to hold the arm's own weight, and
            # the joint settles short of its command no matter the gain.
            mujoco.mj_rnePostConstraint(self._model, self._data)
            for name, (aid, qadr, dadr) in self._joint_map.items():
                lo, hi = limits[name]
                desired = float(np.clip(self._command[name], lo, hi))
                torque = (
                    self._data.qfrc_bias[dadr]
                    + KP * (desired - self._data.qpos[qadr])
                    - KD * self._data.qvel[dadr]
                )
                clo, chi = self._model.actuator_ctrlrange[aid]
                self._data.ctrl[aid] = float(np.clip(torque, clo, chi))
            mujoco.mj_step(self._model, self._data)
        return self.observe()

    def observe(self) -> Observation:
        contacts, force, foreign = self._contact_summary()
        return Observation(
            t=float(self._data.time),
            joint_pos={
                name: float(self._data.qpos[qadr])
                for name, (_, qadr, _) in self._joint_map.items()
            },
            ee_pos=np.array(self._data.xpos[self._ee], dtype=float),
            ee_rot=np.array(self._data.xmat[self._ee], dtype=float).reshape(3, 3),
            object_pos=np.array(self._data.xpos[self._puck_body], dtype=float),
            hand_contacts=contacts,
            grasp_force=force,
            self_collision=foreign,
            extras={"nefc": float(self._data.nefc)},
        )

    def _contact_summary(self) -> tuple[int, float, bool]:
        """Count hand-puck contacts, sum their force, flag interference."""
        contacts = 0
        total = 0.0
        foreign = False
        buf = np.zeros(6, dtype=float)
        for i in range(self._data.ncon):
            con = self._data.contact[i]
            g1, g2 = int(con.geom1), int(con.geom2)
            if not (g1 in self._puck_geoms or g2 in self._puck_geoms):
                continue
            other = g2 if g1 in self._puck_geoms else g1
            if other in self._hand_geoms:
                mujoco.mj_contactForce(self._model, self._data, i, buf)
                contacts += 1
                total += abs(float(buf[0]))
            elif (
                other not in self._support_geoms
                and self._model.geom_bodyid[other] != 0
            ):
                # Neither the hand, nor the work surface, nor the world is
                # moving the puck -- that invalidates a clean push. The table
                # is excluded on purpose: it holds the puck up, and counting
                # it made every successful run report interference.
                foreign = True
        return contacts, total, foreign

    # -- evidence ---------------------------------------------------------

    def render(self, width: int = 640, height: int = 480) -> np.ndarray:
        """Frame of the work surface, for the demo recording.

        The camera is aimed explicitly. MuJoCo's default view frames the whole
        model, which on a humanoid standing at a table puts the legs across the
        shot and a table leg between the lens and the puck -- a recording of
        the action that does not show the action.
        """
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self._model, height=height, width=width)
        if getattr(self, "_camera", None) is None:
            camera = mujoco.MjvCamera()
            camera.type = mujoco.mjtCamera.mjCAMERA_FREE
            # Look at the middle of the puck's travel, on the surface.
            camera.lookat[:] = [
                float(self._puck_xy[0]),
                float((self._puck_xy[1] + self._goal_xy[1]) / 2.0),
                self._surface_z,
            ]
            # Close enough to see the hand meet the puck, high enough that the
            # surface reads as a surface rather than as a horizon line.
            camera.distance = 0.95
            camera.azimuth = 160.0
            camera.elevation = -25.0
            self._camera = camera
        self._renderer.update_scene(self._data, camera=self._camera)
        return self._renderer.render()

    def close(self) -> None:
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
        shutil.rmtree(self._scratch, ignore_errors=True)
