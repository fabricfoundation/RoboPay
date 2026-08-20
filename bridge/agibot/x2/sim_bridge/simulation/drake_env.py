"""Drake back end, for sim-to-sim validation against MuJoCo.

Same robot, same task, same policy object -- a genuinely different physics
engine underneath. That is the point: if the skill only works because of one
engine's contact model or solver tolerances, running it in a second engine
exposes that, and the Tier 1 criteria require the check.

Two things are deliberately shared with the MuJoCo back end and must stay
shared, or the comparison stops meaning anything:

  * every joint MuJoCo actuates resolves here under the same name, and
  * the base is welded at the same height, so both engines and the IK planner
    work in one frame. That alignment is verified numerically at startup by
    the sim2sim harness rather than assumed.

The joint sets are not identical, and it is worth being exact about how. The
AgiBot MuJoCo scene actuates 19 upper-body joints; the URDF Drake parses
carries those same 19 plus 12 leg joints. Both descriptions weld the torso, so
the legs hang unloaded below the working volume and never touch the task. The
policy drives the left arm and holds the rest, so the comparison covers every
joint that moves.

What differs is everything the comparison is about: contact resolution,
integrator, and how the joints are driven. The MuJoCo model is driven by an
explicit gravity-compensated PD law over torque actuators; here the joints get
Drake's implicit PD actuators, which are solved simultaneously with the
contact problem. Two different routes to "hold this angle" is a fair test of
whether the plan survives the trip.
"""

from __future__ import annotations

import numpy as np
from pydrake.geometry import Box, CollisionFilterDeclaration, Cylinder, GeometrySet
from pydrake.math import RigidTransform
from pydrake.multibody.parsing import Parser
from pydrake.multibody.plant import AddMultibodyPlantSceneGraph, CoulombFriction
from pydrake.multibody.tree import (
    BodyIndex,
    PdControllerGains,
    SpatialInertia,
    UnitInertia,
)
from pydrake.systems.analysis import Simulator
from pydrake.systems.framework import DiagramBuilder

import os
from pathlib import Path
from .base import END_EFFECTOR_BODY, Observation, SimEnv

#: Joint stiffness and damping for Drake's implicit PD actuators. An explicit
#: torque law was tried on an earlier robot and is not viable on a tree this
#: size at a discrete timestep: the force is held constant across the solver's
#: substeps and stiff joints integrate straight past their setpoints.
KP = 400.0
KD = 40.0

#: Bodies whose geometry counts as "the hand", matching the MuJoCo back end.
_HAND_PREFIXES = ("left_wrist_",)

#: Links AgiBot's own MuJoCo model declares non-colliding, and which therefore
#: must not collide here either.
#:
#: Read off the shipped model rather than chosen: every other link carries a
#: pair of geoms, one visual and one collision, while each of these carries a
#: single geom with `contype=0 conaffinity=0`. Drake has no such notion -- it
#: gives every link with a `<collision>` tag a convex hull -- so left alone the
#: two engines are simulating different robots. That is not academic: the
#: convex hull of `left_wrist_yaw_link` struck the puck at 99.8 N partway
#: through the raise, throwing it off the table on a task MuJoCo completed.
_NON_COLLIDING_LINKS = (
    "head_yaw_link",
    "left_wrist_yaw_link",
    "right_wrist_yaw_link",
    "waist_pitch_link",
    "waist_yaw_link",
)

#: Drake loads a mesh-converted copy of the simple-collision variant. Drake
#: builds convex hulls for collision geometry and accepts only .obj/.vtk/.gltf
#: while AgiBot ships .STL throughout, so `tools/convert_meshes.py` produces
#: an OBJ-based copy first. The variant carries the same 31 joints under the
#: same names as the model MuJoCo runs; its root frame is `torso_link`, welded
#: at the height that frame sits at in the full model so both engines and the
#: IK planner share one frame.
_SIMPLE_COLLISION_URDF = "x2_ultra_simple_collision.urdf"
_TORSO_HEIGHT = 0.8351


def _converted_root() -> Path:
    """Root of the OBJ-converted description, honouring an override."""
    override = os.environ.get("X2_DESCRIPTION_OBJ")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[5] / "assets" / "x2_description_obj"


class DrakeX2Env(SimEnv):
    """Drake implementation of the push-to-target world."""

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
        time_step: float = 0.002,
    ) -> None:
        self._puck_xy = np.array([puck_x, puck_y], dtype=float)
        self._goal_xy = np.array([goal_x, goal_y], dtype=float)
        self._surface_z = float(surface_z)
        self._table_half = float(table_half)
        self._puck_radius = float(puck_radius)
        self._puck_half_height = float(puck_half_height)
        self._puck_z = self._surface_z + self._puck_half_height

        builder = DiagramBuilder()
        self._plant, self._scene_graph = AddMultibodyPlantSceneGraph(
            builder, time_step=time_step
        )
        root = _converted_root()
        urdf = root / _SIMPLE_COLLISION_URDF
        if not urdf.is_file():
            raise FileNotFoundError(
                f"converted X2 description not found at {urdf}. Run "
                "tools/convert_meshes.py, or set X2_DESCRIPTION_OBJ."
            )
        parser = Parser(self._plant)
        parser.package_map().PopulateFromFolder(str(root))
        parser.AddModels(str(urdf))
        self._plant.WeldFrames(
            self._plant.world_frame(),
            self._plant.GetFrameByName("torso_link"),
            RigidTransform([0.0, 0.0, _TORSO_HEIGHT]),
        )
        self._props = self._plant.AddModelInstance("world_objects")
        self._add_table()
        self._add_puck(puck_mass, puck_friction)
        self._actuators = self._add_pd_actuators()
        # The hand is deliberately *not* filtered against the table. An
        # earlier version excluded that pair because the hand bottomed out on
        # the surface, but that was a symptom of planning to the wrist frame
        # rather than to the hand: the tool point now sits near the hand's
        # lower tip and clears the table by 20mm through the push. Leaving the
        # pair live means both engines resolve hand-against-table the same
        # way, which is the entire point of running the task twice.
        self._filter_non_colliding_links()
        self._plant.Finalize()

        self._diagram = builder.Build()
        self._root_context = self._diagram.CreateDefaultContext()
        self._context = self._plant.GetMyMutableContextFromRoot(self._root_context)
        self._simulator = Simulator(self._diagram, self._root_context)
        self._simulator.Initialize()

        self._joints = {
            self._plant.get_joint(j).name(): self._plant.get_joint(j)
            for j in self._plant.GetJointIndices()
            if self._plant.get_joint(j).num_positions() == 1
        }
        self._ee = self._plant.GetFrameByName(END_EFFECTOR_BODY)
        self._robot_instance = self._plant.GetBodyByName("torso_link").model_instance()
        # Actuator order defines the layout of the desired-state port.
        self._ordered = [
            self._plant.get_joint_actuator(i).joint().name()
            for i in self._plant.GetJointActuatorIndices(self._robot_instance)
        ]
        self._hand_bodies = self._collect_hand_bodies()
        self._puck_body = self._plant.GetBodyByName("puck")
        self._time = 0.0
        self._command: dict[str, float] = {}

    # -- construction -----------------------------------------------------

    def _add_table(self) -> None:
        half = self._table_half
        # Solid down to the floor, matching the MuJoCo scene. A thin slab was
        # tried first and the puck tunnelled straight through it on contact --
        # it left at x=0.21, y=0.18, the middle of the surface rather than an
        # edge, which is what gives a pass-through away rather than a fall.
        thickness = self._surface_z
        table = self._plant.AddRigidBody(
            "table",
            self._props,
            SpatialInertia.SolidBoxWithMass(50.0, 2 * half, 2 * half, thickness),
        )
        self._plant.WeldFrames(
            self._plant.world_frame(),
            table.body_frame(),
            RigidTransform([0.34, 0.20, self._surface_z - thickness / 2.0]),
        )
        shape = Box(2 * half, 2 * half, thickness)
        self._plant.RegisterCollisionGeometry(
            table, RigidTransform(), shape, "table_collision",
            CoulombFriction(0.6, 0.5),
        )
        self._plant.RegisterVisualGeometry(
            table, RigidTransform(), shape, "table_visual", [0.42, 0.40, 0.38, 1.0]
        )

    def _add_puck(self, mass: float, friction: float) -> None:
        puck = self._plant.AddRigidBody(
            "puck",
            self._props,
            SpatialInertia(
                mass=mass,
                p_PScm_E=np.zeros(3),
                G_SP_E=UnitInertia.SolidCylinder(
                    self._puck_radius, 2 * self._puck_half_height, [0.0, 0.0, 1.0]
                ),
            ),
        )
        shape = Cylinder(self._puck_radius, 2 * self._puck_half_height)
        self._plant.RegisterCollisionGeometry(
            puck, RigidTransform(), shape, "puck_collision",
            CoulombFriction(friction, friction * 0.9),
        )
        self._plant.RegisterVisualGeometry(
            puck, RigidTransform(), shape, "puck_visual", [0.85, 0.35, 0.15, 1.0]
        )

    def _add_pd_actuators(self) -> dict[str, object]:
        """Give every 1-DOF robot joint a PD-controlled actuator.

        The URDF ships no <transmission> blocks, so the plant would otherwise
        have zero actuators and no way to hold a pose.
        """
        actuators: dict[str, object] = {}
        for index in self._plant.GetJointIndices():
            joint = self._plant.get_joint(index)
            if joint.num_positions() != 1 or joint.type_name() == "weld":
                continue
            actuator = self._plant.AddJointActuator(f"{joint.name()}_act", joint)
            actuator.set_controller_gains(PdControllerGains(p=KP, d=KD))
            actuators[joint.name()] = actuator
        return actuators

    def _filter_non_colliding_links(self) -> None:
        """Drop collision geometry the vendor's MuJoCo model does not have.

        Excluding each listed link against every collision geometry in the
        scene, itself included, which is what `_NON_COLLIDING_LINKS` documents
        the reason for. Links absent from the description are skipped rather
        than raising, so the filter degrades quietly if AgiBot renames one.
        """
        everything = GeometrySet([
            g
            for i in range(self._plant.num_bodies())
            for g in self._plant.GetCollisionGeometriesForBody(
                self._plant.get_body(BodyIndex(i))
            )
        ])
        geoms = []
        for name in _NON_COLLIDING_LINKS:
            if not self._plant.HasBodyNamed(name):
                continue
            geoms.extend(
                self._plant.GetCollisionGeometriesForBody(
                    self._plant.GetBodyByName(name)
                )
            )
        if not geoms:
            return
        self._scene_graph.collision_filter_manager().Apply(
            CollisionFilterDeclaration().ExcludeBetween(
                GeometrySet(geoms), everything
            )
        )

    def _collect_hand_bodies(self) -> set:
        return {
            self._plant.get_body(BodyIndex(i)).index()
            for i in range(self._plant.num_bodies())
            if self._plant.get_body(BodyIndex(i)).name().startswith(_HAND_PREFIXES)
        }

    # -- SimEnv -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "drake"

    @property
    def goal(self) -> np.ndarray:
        return np.array([*self._goal_xy, self._puck_z], dtype=float)

    @property
    def puck_radius(self) -> float:
        return self._puck_radius

    @property
    def joint_limits(self) -> dict[str, tuple[float, float]]:
        return {
            name: (
                float(joint.position_lower_limits()[0]),
                float(joint.position_upper_limits()[0]),
            )
            for name, joint in self._joints.items()
        }

    def reset(self) -> Observation:
        self._root_context.SetTime(0.0)
        self._time = 0.0
        self._plant.SetDefaultContext(self._context)
        self._plant.SetFreeBodyPose(
            self._context,
            self._puck_body,
            RigidTransform([*self._puck_xy, self._puck_z]),
        )
        self._plant.SetVelocities(self._context, np.zeros(self._plant.num_velocities()))
        self._command = {n: j.get_angle(self._context) for n, j in self._joints.items()}
        self._simulator.Initialize()
        return self.observe()

    def step(self, targets: dict[str, float]) -> Observation:
        self._command.update(targets)
        limits = self.joint_limits
        desired = np.zeros(2 * len(self._ordered))
        for slot, name in enumerate(self._ordered):
            lo, hi = limits[name]
            desired[slot] = float(np.clip(self._command.get(name, 0.0), lo, hi))
            # Desired velocity stays zero: the policy commands positions and
            # lets the actuator damp the approach.
        self._plant.get_desired_state_input_port(self._robot_instance).FixValue(
            self._context, desired
        )
        self._time += self.control_dt
        self._simulator.AdvanceTo(self._time)
        return self.observe()

    def observe(self) -> Observation:
        pose = self._plant.CalcRelativeTransform(
            self._context, self._plant.world_frame(), self._ee
        )
        puck = self._plant.GetFreeBodyPose(self._context, self._puck_body)
        contacts, force = self._contact_summary()
        return Observation(
            t=self._time,
            joint_pos={
                n: float(j.get_angle(self._context)) for n, j in self._joints.items()
            },
            ee_pos=np.array(pose.translation(), dtype=float),
            ee_rot=np.array(pose.rotation().matrix(), dtype=float),
            object_pos=np.array(puck.translation(), dtype=float),
            hand_contacts=contacts,
            grasp_force=force,
            self_collision=False,
            extras={},
        )

    def _contact_summary(self) -> tuple[int, float]:
        results = self._plant.get_contact_results_output_port().Eval(self._context)
        puck_index = self._puck_body.index()
        contacts = 0
        total = 0.0
        for i in range(results.num_point_pair_contacts()):
            info = results.point_pair_contact_info(i)
            a, b = info.bodyA_index(), info.bodyB_index()
            if puck_index not in (a, b):
                continue
            other = b if a == puck_index else a
            if other in self._hand_bodies:
                contacts += 1
                total += float(np.linalg.norm(info.contact_force()))
        return contacts, total

    def render(self, width: int = 640, height: int = 480) -> np.ndarray:
        """Drake runs headless here; MuJoCo produces the demo recording."""
        raise NotImplementedError("rendering is provided by the MuJoCo back end")

    def close(self) -> None:
        pass
