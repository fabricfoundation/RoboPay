"""Drake back end, for sim-to-sim validation against MuJoCo.

Same robot, same task, same policy object -- a genuinely different physics
engine underneath. That is the point: if the skill only works because of one
engine's contact model or solver tolerances, running it in a second engine
exposes that, and the Tier 1 criteria require the check.

Two things are deliberately shared with the MuJoCo back end and must stay
shared, or the comparison stops meaning anything:

  * the robot description resolves to the same 43 joints by name (the
    menagerie MJCF and the official Unitree URDF agree on all of them), and
  * the pelvis is fixed at the same standing height, so both engines and the
    IK planner are working in one frame.

What differs is everything the comparison is actually about: contact
resolution, integrator, and how the joints are driven. MuJoCo's model ships
position servos; this URDF has no transmissions at all, so the joints are
driven here by an explicit gravity-compensated PD law. Two different routes to
"hold this angle" is a fair test of whether the plan survives the trip.
"""

from __future__ import annotations

import numpy as np
from pydrake.geometry import Box, Cylinder
from pydrake.math import RigidTransform
from pydrake.multibody.parsing import Parser
from pydrake.multibody.plant import (
    AddMultibodyPlantSceneGraph,
    CoulombFriction,
)
from pydrake.multibody.tree import (
    BodyIndex,
    PdControllerGains,
    SpatialInertia,
    UnitInertia,
)
from pydrake.systems.analysis import Simulator
from pydrake.systems.framework import DiagramBuilder

from ..policy.ik import PELVIS_HEIGHT, default_urdf
from .base import END_EFFECTOR_BODY, Observation, SimEnv

#: Joint stiffness and damping. These feed Drake's *implicit* PD actuators
#: rather than an explicit torque law.
#:
#: The explicit version -- computing KP*err - KD*rate each control tick and
#: pushing it through the applied-generalized-force port -- was tried first and
#: is not viable here. On a 43-DOF tree with a discrete solver it went
#: non-finite within a couple of seconds of real arm motion at every gain pair
#: tried, because the force is held constant across the solver's substeps and
#: the stiff joints integrate straight past their setpoints. Drake's PD
#: actuators are solved simultaneously with the contact problem, which is
#: unconditionally stable at these gains.
KP = 400.0
KD = 40.0

#: Bodies whose geometry counts as "the hand" for contact accounting, matching
#: the MuJoCo back end's definition.
_HAND_PREFIXES = ("right_hand_", "right_wrist_")


class DrakeG1Env(SimEnv):
    """Drake implementation of the push-to-target world."""

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
        time_step: float = 0.002,
    ) -> None:
        self._target = np.array([target_x, target_y], dtype=float)
        self._goal_xy = np.array([goal_x, goal_y], dtype=float)
        self._table_top = float(table_top)
        self._table_half = float(table_half)
        self._puck_radius = float(puck_radius)
        self._puck_half_height = float(puck_half_height)
        self._surface_z = 2.0 * self._table_top
        self._puck_z = self._surface_z + self._puck_half_height

        builder = DiagramBuilder()
        self._plant, self._scene_graph = AddMultibodyPlantSceneGraph(
            builder, time_step=time_step
        )
        urdf = default_urdf()
        parser = Parser(self._plant)
        parser.package_map().PopulateFromFolder(str(urdf.parent))
        parser.AddModels(str(urdf))
        self._plant.WeldFrames(
            self._plant.world_frame(),
            self._plant.GetFrameByName("pelvis"),
            RigidTransform([0.0, 0.0, PELVIS_HEIGHT]),
        )
        # The robot arrives as its own model instance, so scene props need an
        # explicit one of their own rather than the default.
        self._props = self._plant.AddModelInstance("world_objects")
        self._add_table()
        self._puck_body = self._add_puck(puck_mass)
        self._actuators = self._add_pd_actuators()
        self._filter_hand_table_contact()
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
        self._wrist = self._plant.GetFrameByName(END_EFFECTOR_BODY)
        self._robot_instance = self._plant.GetModelInstanceByName(
            self._plant.GetModelInstanceName(
                self._plant.GetBodyByName("pelvis").model_instance()
            )
        )
        # Actuator order defines the layout of the desired-state port.
        self._ordered_joints = [
            self._plant.get_joint_actuator(i).joint().name()
            for i in self._plant.GetJointActuatorIndices(self._robot_instance)
        ]
        self._hand_bodies = self._collect_hand_bodies()
        self._puck_friction = float(puck_friction)
        self._nq = self._plant.num_positions()
        self._time = 0.0

    # -- construction -----------------------------------------------------

    def _add_table(self) -> None:
        half = self._table_half
        table = self._plant.AddRigidBody(
            "table",
            self._props,
            SpatialInertia.SolidBoxWithMass(
                1.0, 2 * half, 2 * half, 2 * self._table_top
            ),
        )
        self._plant.WeldFrames(
            self._plant.world_frame(),
            table.body_frame(),
            RigidTransform([0.42, -0.08, self._table_top]),
        )
        shape = Box(2 * half, 2 * half, 2 * self._table_top)
        self._plant.RegisterCollisionGeometry(
            table, RigidTransform(), shape, "table_collision",
            CoulombFriction(0.6, 0.5),
        )
        self._plant.RegisterVisualGeometry(
            table, RigidTransform(), shape, "table_visual", [0.42, 0.40, 0.38, 1.0]
        )

    def _add_puck(self, mass: float):
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
            CoulombFriction(0.45, 0.4),
        )
        self._plant.RegisterVisualGeometry(
            puck, RigidTransform(), shape, "puck_visual", [0.85, 0.35, 0.15, 1.0]
        )
        return puck

    def _add_pd_actuators(self) -> dict[str, object]:
        """Give every 1-DOF robot joint a PD-controlled actuator.

        The Unitree URDF ships no <transmission> blocks, so the plant would
        otherwise have zero actuators and no way to hold a pose.
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

    def _filter_hand_table_contact(self) -> None:
        """Stop the hand colliding with the table in Drake.

        Drake derives collision geometry from the convex hull of each mesh.
        For the G1's fingers those hulls are appreciably fatter than the
        collision primitives the MuJoCo model ships, so a hand skimming a few
        millimetres over the table bottoms out on it here and stalls ~48mm
        above its commanded height -- measured, and unaffected by servo gain
        from 400 through 10000.

        What this task needs from contact is hand against puck. Hand against
        table is an artefact of the hull approximation, so it is filtered out
        rather than worked around by reshaping the task. Puck/table contact is
        untouched, which is what actually holds the puck up.
        """
        from pydrake.geometry import CollisionFilterDeclaration, GeometrySet

        table = self._plant.GetBodyByName("table")
        hand = [
            self._plant.get_body(index)
            for index in self._collect_hand_bodies()
        ]
        table_set = GeometrySet(self._plant.GetCollisionGeometriesForBody(table))
        hand_set = GeometrySet(
            [g for body in hand
             for g in self._plant.GetCollisionGeometriesForBody(body)]
        )
        self._scene_graph.collision_filter_manager().Apply(
            CollisionFilterDeclaration().ExcludeBetween(table_set, hand_set)
        )

    def _collect_hand_bodies(self) -> set:
        """Bodies that count as "the hand", matched by name across the plant."""
        return {
            body.index()
            for body in (
                self._plant.get_body(BodyIndex(i))
                for i in range(self._plant.num_bodies())
            )
            if body.name().startswith(_HAND_PREFIXES)
        }

    # -- SimEnv -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "drake"

    @property
    def goal(self) -> np.ndarray:
        return np.array([*self._goal_xy, self._puck_z], dtype=float)

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
        # Match the menagerie 'stand' pose for the joints both models share.
        for name, value in _STAND_POSE.items():
            joint = self._joints.get(name)
            if joint is not None:
                joint.set_angle(self._context, value)
        self._plant.SetFreeBodyPose(
            self._context,
            self._plant.GetBodyByName("puck"),
            RigidTransform([*self._target, self._puck_z]),
        )
        self._plant.SetVelocities(self._context, np.zeros(self._plant.num_velocities()))
        self._command = {n: j.get_angle(self._context) for n, j in self._joints.items()}
        self._simulator.Initialize()
        return self.observe()

    def step(self, targets: dict[str, float]) -> Observation:
        self._command.update(targets)
        limits = self.joint_limits
        desired = np.zeros(2 * len(self._ordered_joints))
        for slot, name in enumerate(self._ordered_joints):
            lo, hi = limits[name]
            desired[slot] = float(np.clip(self._command[name], lo, hi))
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
            self._context, self._plant.world_frame(), self._wrist
        )
        puck = self._plant.GetFreeBodyPose(
            self._context, self._plant.GetBodyByName("puck")
        )
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
        puck_body = self._plant.GetBodyByName("puck").index()
        contacts = 0
        total = 0.0
        for i in range(results.num_point_pair_contacts()):
            info = results.point_pair_contact_info(i)
            a, b = info.bodyA_index(), info.bodyB_index()
            if puck_body not in (a, b):
                continue
            other = b if a == puck_body else a
            if other in self._hand_bodies:
                contacts += 1
                total += float(np.linalg.norm(info.contact_force()))
        return contacts, total

    def ee_jacobian(self) -> np.ndarray:  # pragma: no cover - unused by the policy
        raise NotImplementedError(
            "the IK planner supersedes Jacobian servoing; see policy/ik.py"
        )

    def render(self, width: int = 640, height: int = 480) -> np.ndarray:
        """Drake runs headless here; MuJoCo produces the demo recording."""
        raise NotImplementedError(
            "rendering is provided by the MuJoCo back end"
        )

    def close(self) -> None:
        pass


#: The joints the menagerie 'stand' keyframe sets away from zero. Everything
#: else starts at zero in both engines.
_STAND_POSE = {
    "left_shoulder_pitch_joint": 0.2,
    "left_shoulder_roll_joint": 0.2,
    "left_elbow_joint": 1.28,
    "left_wrist_roll_joint": 1.05,
    "right_shoulder_pitch_joint": 0.2,
    "right_shoulder_roll_joint": -0.2,
    "right_elbow_joint": 1.28,
    "right_wrist_roll_joint": -1.05,
}
