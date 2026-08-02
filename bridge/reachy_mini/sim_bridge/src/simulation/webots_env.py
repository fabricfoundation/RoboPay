"""Webots wrapper used as the second physics engine for sim-to-sim checks.

Mirrors ReachyMiniMujocoEnv's responsibilities: this class owns all
geometry/target-tracking logic (look_at + angular error), so the policy
(ReachyGazePolicy) stays engine-agnostic and receives only
(target_visible, angular_error_rad).

Design note -- kinematic override, not full rigid-body IK chain:
The Stewart platform is a closed-loop 6-bar mechanism that the URDF-tree
based Webots import cannot represent as stable rigid-body physics (see
README for details). We therefore run Webots as a *kinematic* validator:
the head Solid's world transform is set directly via supervisor field
access (translation/rotation), rather than driving the 7 Stewart
actuators individually as MuJoCo does. This is a deliberate, documented
simplification -- it still exercises the same policy FSM and the same
angular-error metric, which is what sim-to-sim validation is checking.
"""
import math
from typing import Optional, Tuple

import numpy as np
from scipy.spatial.transform import Rotation as R


class ReachyMiniWebotsEnv:
    """Supervisor-driven wrapper: kinematically points the head Solid at
    a world-frame target and computes the same yaw/pitch angular error
    metric as the MuJoCo wrapper, so results from both engines are
    directly comparable.
    """

    def __init__(self, head_def: str = "HEAD", timestep_ms: int = 32):
        from controller import Supervisor  # provided by the Webots runtime

        self.supervisor = Supervisor()
        self.timestep_ms = timestep_ms

        # NOTE: `head_def` is accepted for API symmetry with the MuJoCo env
        # but is not used to look up a DEF node. PROTO body nodes (e.g. the
        # `head` Solid inside ReachyMini.proto) are encapsulated and not
        # reachable via Supervisor.getFromDef() from outside the PROTO.
        # Since the Stewart platform is already treated as a kinematic
        # simplification (see module docstring), we instead command the
        # orientation of the whole robot body -- the exposed PROTO root --
        # as the gaze proxy.
        self.head_node = self.supervisor.getSelf()
        if self.head_node is None:
            raise RuntimeError("Supervisor.getSelf() returned None -- "
                                "is 'supervisor TRUE' set on the Robot node?")

        self._rotation_field = self.head_node.getField("rotation")
        self._translation_field = self.head_node.getField("translation")

    def get_head_pose(self) -> np.ndarray:
        """Current head pose (4x4) in world frame."""
        pos = self.head_node.getPosition()
        rot = self.head_node.getOrientation()  # 3x3 row-major, flat list of 9
        pose = np.eye(4)
        pose[:3, :3] = np.array(rot).reshape(3, 3)
        pose[:3, 3] = pos
        return pose

    def look_at_target(self, target_world_pos: np.ndarray) -> bool:
        """Kinematically orient the head Solid so its +X (forward) axis
        points at the world-frame target. Returns True if feasible.
        """
        pose = self.get_head_pose()
        rel = target_world_pos - pose[:3, 3]
        norm = np.linalg.norm(rel)
        if norm < 1e-6:
            return False
        fwd = rel / norm

        # Build a rotation whose local +X axis aligns with `fwd`, keeping
        # roll stable by referencing world +Z as an approximate "up".
        world_up = np.array([0.0, 0.0, 1.0])
        if abs(np.dot(fwd, world_up)) > 0.999:
            world_up = np.array([0.0, 1.0, 0.0])

        x_axis = fwd
        y_axis = np.cross(world_up, x_axis)
        y_axis /= np.linalg.norm(y_axis)
        z_axis = np.cross(x_axis, y_axis)

        rot_mat = np.column_stack([x_axis, y_axis, z_axis])
        rotvec = R.from_matrix(rot_mat).as_rotvec()
        angle = np.linalg.norm(rotvec)
        axis = rotvec / angle if angle > 1e-9 else np.array([0.0, 0.0, 1.0])

        self._rotation_field.setSFRotation(
            [float(axis[0]), float(axis[1]), float(axis[2]), float(angle)]
        )
        return True

    def step(self, n_substeps: int = 1) -> bool:
        """Advance Webots by n_substeps timesteps. Returns False if the
        simulation ended."""
        for _ in range(n_substeps):
            if self.supervisor.step(self.timestep_ms) == -1:
                return False
        return True

    def angular_error_to(self, target_def: str) -> Tuple[float, float, bool]:
        target_node = self.supervisor.getFromDef(target_def)
        if target_node is None:
            return 0.0, 0.0, False

        pose = self.get_head_pose()
        target_pos = np.array(target_node.getPosition())
        rel = target_pos - pose[:3, 3]
        rel_head = pose[:3, :3].T @ rel

        norm = np.linalg.norm(rel_head)
        if norm < 1e-6:
            return 0.0, 0.0, False

        yaw_err = math.atan2(rel_head[1], rel_head[0])
        horiz = math.hypot(rel_head[0], rel_head[1])
        pitch_err = math.atan2(rel_head[2], horiz)

        fov_half = math.radians(60)
        visible = rel_head[0] > 0 and abs(yaw_err) < fov_half and abs(pitch_err) < fov_half
        return float(yaw_err), float(pitch_err), visible

    def get_target_world_pos(self, target_def: str) -> Optional[np.ndarray]:
        target_node = self.supervisor.getFromDef(target_def)
        if target_node is None:
            return None
        return np.array(target_node.getPosition())
