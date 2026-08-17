"""MuJoCo back end for the G1 push-to-target task.

Robot model: mujoco_menagerie `unitree_g1/g1_with_hands.xml` (43 position
servos), loaded unmodified. The task world lives in `scene.xml.template` and
is materialised into a scratch directory of symlinks, so the upstream
menagerie checkout is never written to.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import mujoco
import numpy as np

from .base import (
    ARM_JOINTS_RIGHT,
    END_EFFECTOR_BODY,
    Observation,
    SimEnv,
)

_TEMPLATE = Path(__file__).with_name("scene.xml.template")

#: Geometry belonging to these bodies counts as "the hand" when deciding
#: whether the block is grasped rather than merely bumped.
_HAND_BODY_PREFIXES = ("right_hand_", "right_wrist_")


#: Name of the upstream robot MJCF included by our scene.
_ROBOT_MJCF = "g1_with_hands.xml"


def _menagerie_dir() -> Path:
    """Locate the menagerie G1 model, honouring an explicit override."""
    override = os.environ.get("G1_MENAGERIE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "menagerie" / "unitree_g1"


class MujocoG1Env(SimEnv):
    """MuJoCo implementation of the pick-and-lift world."""

    control_dt = 0.01

    def __init__(
        self,
        target_x: float,
        target_y: float,
        goal_x: float,
        goal_y: float,
        table_top: float = 0.375,
        table_half: float = 0.26,
        puck_radius: float = 0.035,
        puck_half_height: float = 0.022,
        puck_mass: float = 0.12,
        puck_friction: float = 0.45,
        seed: int = 0,
    ) -> None:
        self._target = np.array([target_x, target_y], dtype=float)
        self._goal = np.array([goal_x, goal_y], dtype=float)
        self._table_top = float(table_top)
        self._table_half = float(table_half)
        self._puck_radius = float(puck_radius)
        self._puck_half_height = float(puck_half_height)
        self._puck_mass = float(puck_mass)
        self._puck_friction = float(puck_friction)
        # Rest the puck on the table surface.
        self._surface_z = 2.0 * self._table_top
        self._block_z = self._surface_z + self._puck_half_height
        self._seed = seed
        self._scratch = Path(tempfile.mkdtemp(prefix="g1_scene_"))
        self._model = self._build_model()
        self._data = mujoco.MjData(self._model)
        self._renderer: mujoco.Renderer | None = None

        self._act_id = {
            mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_ACTUATOR, i): i
            for i in range(self._model.nu)
        }
        self._arm_dofs = np.array(
            [self._dof_of(j) for j in ARM_JOINTS_RIGHT], dtype=int
        )
        self._ee_body = mujoco.mj_name2id(
            self._model, mujoco.mjtObj.mjOBJ_BODY, END_EFFECTOR_BODY
        )
        self._puck_body = mujoco.mj_name2id(
            self._model, mujoco.mjtObj.mjOBJ_BODY, "puck"
        )
        self._puck_qadr = self._model.jnt_qposadr[
            mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, "puck_free")
        ]
        self._hand_geoms = self._geoms_under(_HAND_BODY_PREFIXES)
        # The work surface holds the puck up; contact with it is not evidence
        # of interference. Without this every successful run reported
        # foreignCollision=true, which is exactly the kind of metric a
        # reviewer is right to distrust.
        self._support_geoms = self._geoms_under(("table",))
        self._puck_geoms = {
            mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_GEOM, "puck_geom")
        }
        self._substeps = max(1, round(self.control_dt / self._model.opt.timestep))

    # -- construction -----------------------------------------------------

    def _build_model(self) -> mujoco.MjModel:
        src = _menagerie_dir()
        if not (src / "g1_with_hands.xml").is_file():
            raise FileNotFoundError(
                f"menagerie G1 model not found under {src}. Clone "
                "google-deepmind/mujoco_menagerie or set G1_MENAGERIE_DIR."
            )
        # Symlink the upstream model beside our generated scene so MuJoCo's
        # relative <include> and meshdir lookups resolve without copying 38MB.
        # The robot MJCF itself is regenerated rather than linked: see
        # _fixed_base_model.
        for entry in src.iterdir():
            if entry.name == _ROBOT_MJCF:
                continue
            (self._scratch / entry.name).symlink_to(entry)
        (self._scratch / _ROBOT_MJCF).write_text(
            self._fixed_base_model((src / _ROBOT_MJCF).read_text())
        )

        xml = _TEMPLATE.read_text()
        for key, value in (
            ("{{TARGET_X}}", f"{self._target[0]:.6f}"),
            ("{{TARGET_Y}}", f"{self._target[1]:.6f}"),
            ("{{TARGET_Z}}", f"{self._block_z:.6f}"),
            ("{{TABLE_TOP}}", f"{self._table_top:.6f}"),
            ("{{TABLE_TOP2}}", f"{self._surface_z + 0.0016:.6f}"),
            ("{{TABLE_HALF}}", f"{self._table_half:.6f}"),
            ("{{GOAL_X}}", f"{self._goal[0]:.6f}"),
            ("{{GOAL_Y}}", f"{self._goal[1]:.6f}"),
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
        """Return the menagerie MJCF with the floating base removed.

        The G1 ships with a freejoint on the pelvis and no balance controller.
        It is a humanoid standing on friction: rotating the waist and reaching
        out with a full arm topples it, and the run ends with the robot off
        camera and the puck untouched. More importantly the IK planner welds
        the pelvis to plan against (policy/ik.py), so simulating a floating
        base meant planning in one frame and executing in another -- every
        solved configuration was quietly wrong.

        A soft <equality><weld> was tried first and is not enough; the
        constraint is compliant and the robot drags it out of place. Deleting
        the joint outright is what actually pins the base.

        The 'stand' keyframe is trimmed to match: its first seven numbers are
        the free joint's position and quaternion, which no longer exist.
        """
        if "<freejoint" not in xml:
            return xml
        out = []
        for line in xml.splitlines():
            if "<freejoint" in line:
                continue
            stripped = line.strip()
            if stripped.startswith('qpos="') or stripped.startswith('ctrl="'):
                key, _, rest = stripped.partition('="')
                values = rest.rstrip('"/').split()
                if key == "qpos":
                    values = values[7:]
                indent = line[: len(line) - len(line.lstrip())]
                suffix = '"/>' if line.rstrip().endswith("/>") else '"'
                out.append(f'{indent}{key}="{" ".join(values)}{suffix}')
                continue
            out.append(line)
        return "\n".join(out) + "\n"

    def _dof_of(self, joint: str) -> int:
        jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, joint)
        if jid < 0:
            raise KeyError(f"joint not in model: {joint}")
        return int(self._model.jnt_dofadr[jid])

    def _geoms_under(self, prefixes: tuple[str, ...]) -> set[int]:
        found: set[int] = set()
        for gid in range(self._model.ngeom):
            bid = self._model.geom_bodyid[gid]
            name = mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_BODY, bid) or ""
            if name.startswith(prefixes):
                found.add(gid)
        return found

    # -- SimEnv -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "mujoco"

    @property
    def goal(self) -> np.ndarray:
        """Commanded destination on the table surface, shape (3,)."""
        return np.array([*self._goal, self._block_z], dtype=float)

    @property
    def puck_radius(self) -> float:
        return self._puck_radius

    @property
    def joint_limits(self) -> dict[str, tuple[float, float]]:
        limits: dict[str, tuple[float, float]] = {}
        for name in self._act_id:
            jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
            lo, hi = self._model.jnt_range[jid]
            limits[name] = (float(lo), float(hi))
        return limits

    def reset(self) -> Observation:
        if self._model.nkey > 0:
            mujoco.mj_resetDataKeyframe(self._model, self._data, 0)
        else:
            mujoco.mj_resetData(self._model, self._data)
        # The inherited keyframe only covers the robot; place the block
        # explicitly so its free joint never starts from a null quaternion.
        adr = self._puck_qadr
        self._data.qpos[adr : adr + 3] = [*self._target, self._block_z]
        self._data.qpos[adr + 3 : adr + 7] = [1.0, 0.0, 0.0, 0.0]
        self._data.qvel[:] = 0.0
        # Hold the start pose so the robot does not sag before the first step.
        for name, aid in self._act_id.items():
            jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
            self._data.ctrl[aid] = self._data.qpos[self._model.jnt_qposadr[jid]]
        mujoco.mj_forward(self._model, self._data)
        return self.observe()

    def step(self, targets: dict[str, float]) -> Observation:
        for joint, value in targets.items():
            aid = self._act_id.get(joint)
            if aid is None:
                raise KeyError(f"no actuator for joint: {joint}")
            lo, hi = self._model.actuator_ctrlrange[aid]
            self._data.ctrl[aid] = float(np.clip(value, lo, hi))
        for _ in range(self._substeps):
            mujoco.mj_step(self._model, self._data)
        return self.observe()

    def observe(self) -> Observation:
        contacts, force, foreign = self._contact_summary()
        joint_pos = {}
        for name in self._act_id:
            jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
            joint_pos[name] = float(self._data.qpos[self._model.jnt_qposadr[jid]])
        return Observation(
            t=float(self._data.time),
            joint_pos=joint_pos,
            ee_pos=np.array(self._data.xpos[self._ee_body], dtype=float),
            ee_rot=np.array(self._data.xmat[self._ee_body], dtype=float).reshape(3, 3),
            object_pos=np.array(self._data.xpos[self._puck_body], dtype=float),
            hand_contacts=contacts,
            grasp_force=force,
            self_collision=foreign,
            extras={"nefc": float(self._data.nefc)},
        )

    def _contact_summary(self) -> tuple[int, float, bool]:
        """Count hand-puck contacts, sum their normal force, flag foreign hits."""
        contacts = 0
        total = 0.0
        foreign = False
        buf = np.zeros(6, dtype=float)
        for i in range(self._data.ncon):
            con = self._data.contact[i]
            g1, g2 = int(con.geom1), int(con.geom2)
            touches_puck = g1 in self._puck_geoms or g2 in self._puck_geoms
            if not touches_puck:
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
                # Something that is neither the hand, nor the work surface,
                # nor the world is moving the puck -- that invalidates a
                # clean push.
                foreign = True
        return contacts, total, foreign

    def ee_jacobian(self) -> np.ndarray:
        jacp = np.zeros((3, self._model.nv), dtype=float)
        jacr = np.zeros((3, self._model.nv), dtype=float)
        mujoco.mj_jacBody(self._model, self._data, jacp, jacr, self._ee_body)
        return np.vstack([jacp[:, self._arm_dofs], jacr[:, self._arm_dofs]])

    # -- evidence ---------------------------------------------------------

    def render(self, width: int = 640, height: int = 480) -> np.ndarray:
        """Return an RGB frame; used to build the required demo recording."""
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self._model, height=height, width=width)
        self._renderer.update_scene(self._data)
        return self._renderer.render()

    def close(self) -> None:
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
        shutil.rmtree(self._scratch, ignore_errors=True)
