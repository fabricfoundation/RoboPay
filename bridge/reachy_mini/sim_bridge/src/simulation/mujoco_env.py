"""MuJoCo wrapper around the official Reachy Mini model.

Uses the vendor's own geometry and IK utilities end-to-end:
  - reachy_mini.vision.look_at.look_at_world_pose  -> target head pose
  - reachy_mini.kinematics.analytical_kinematics    -> head pose -> joints
  - joints are written to data.ctrl (actuators), matching the vendor's
    own MujocoBackend -- writing to qpos directly breaks the Stewart
    platform's closed-loop rod constraints and destabilizes the sim.
"""
import glob
import math
import os
import sys
import types
from typing import Optional, Tuple

import mujoco
import numpy as np

# reachy_mini/__init__.py eagerly imports its whole app/io/vision stack
# (including face_tracking, which needs PyGObject/`gi` -> libgirepository).
# We only need AnalyticalKinematics + look_at, both pure-numpy geometry
# with no GUI/vision dependency. Some sandboxed runtimes (e.g. Webots'
# snap-confined controller subprocess) can't see the system libgirepository
# .so even when it's installed and resolvable from a normal shell -- so we
# stub `gi` before triggering reachy_mini's import chain. This never
# affects behavior here since nothing in this file uses gi/FaceTracker.
if "gi" not in sys.modules:
    try:
        import gi  # noqa: F401
        from gi.repository import Gst  # noqa: F401
    except (ImportError, ValueError):
        # reachy_mini/__init__.py eagerly imports its whole app/io/vision/
        # media stack, which transitively does `from gi.repository import
        # <Whatever>` for an open-ended set of GObject-Introspection
        # submodules (Gst, GstApp, GLib, and possibly more depending on
        # reachy_mini's version). We only need AnalyticalKinematics + a
        # pure-numpy look_at helper, neither of which touches gi at all --
        # so rather than whack-a-mole each submodule name, `gi.repository`
        # is stubbed with __getattr__ that lazily fabricates an empty
        # module for *any* attribute requested, satisfying
        # `from gi.repository import X` for arbitrary X.
        class _LazyGiRepository(types.ModuleType):
            def __getattr__(self, name):
                mod = types.ModuleType(f"gi.repository.{name}")
                setattr(self, name, mod)
                sys.modules[f"gi.repository.{name}"] = mod
                return mod

        _gi_stub = types.ModuleType("gi")
        _gi_stub.require_version = lambda *a, **k: None
        _gi_stub.__path__ = []
        sys.modules["gi"] = _gi_stub

        _gi_repository_stub = _LazyGiRepository("gi.repository")
        sys.modules["gi.repository"] = _gi_repository_stub

from reachy_mini.kinematics.analytical_kinematics import AnalyticalKinematics
from reachy_mini.vision.look_at import look_at_world_pose, default_head_to_camera_transform


def locate_official_scene() -> str:
    import site
    candidates = []
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        candidates += glob.glob(os.path.join(sp, "reachy_mini", "descriptions",
                                              "reachy_mini", "mjcf", "scenes", "*.xml"))
    if not candidates:
        raise FileNotFoundError(
            "Could not locate reachy_mini MJCF scenes. Install with: "
            'pip install "reachy-mini[mujoco]" --break-system-packages'
        )
    for c in candidates:
        if c.endswith("minimal.xml"):
            return c
    return candidates[0]


# Vendor's real actuator order/count for the head (7 = body_yaw + 6 stewart).
HEAD_JOINT_NAMES = ["yaw_body", "stewart_1", "stewart_2", "stewart_3",
                     "stewart_4", "stewart_5", "stewart_6"]


class ReachyMiniMujocoEnv:
    def __init__(self, scene_path: Optional[str] = None):
        self.scene_path = scene_path or locate_official_scene()
        self.model = mujoco.MjModel.from_xml_path(self.scene_path)
        self.data = mujoco.MjData(self.model)

        self.head_site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "head")
        self.kin = AnalyticalKinematics(automatic_body_yaw=True)
        self.T_head_cam = default_head_to_camera_transform()

        # Map actuator index -> name, same approach as the vendor backend,
        # so we write to ctrl in the model's actual actuator order rather
        # than assuming it matches HEAD_JOINT_NAMES order.
        self.actuator_names = [self.model.actuator(k).name for k in range(self.model.nu)]

        # Settle the sim at a neutral pose before any control, same as the
        # vendor backend does at startup (avoids a violent first step).
        mujoco.mj_forward(self.model, self.data)
        for _ in range(50):
            mujoco.mj_step(self.model, self.data)

    def get_head_pose(self) -> np.ndarray:
        """Current head pose (4x4) from the 'head' site, world frame."""
        pose = np.eye(4)
        pose[:3, :3] = self.data.site_xmat[self.head_site_id].reshape(3, 3)
        pose[:3, 3] = self.data.site_xpos[self.head_site_id]
        return pose

    def look_at_target(self, target_world_pos: np.ndarray, body_yaw: float = 0.0) -> bool:
        """Command the head to look at a world-frame point, using the
        vendor's look_at_world_pose + AnalyticalKinematics.ik(), writing
        results to ctrl (actuators). Returns True if the pose was feasible.
        """
        head_pose = self.get_head_pose()
        rel = target_world_pos - head_pose[:3, 3]
        # look_at_world_pose expects the target expressed in the head's
        # local frame (it aims local +X at it), so rotate into head frame.
        rel_head = head_pose[:3, :3].T @ rel

        target_pose = look_at_world_pose(*rel_head.tolist())
        joints = self.kin.ik(target_pose, body_yaw=body_yaw)
        if joints is None or np.any(np.isnan(joints)):
            return False

        for name, value in zip(HEAD_JOINT_NAMES, joints.tolist()):
            if name in self.actuator_names:
                idx = self.actuator_names.index(name)
                self.data.ctrl[idx] = value
        return True

    def step(self, n_substeps: int = 1) -> None:
        for _ in range(n_substeps):
            mujoco.mj_step(self.model, self.data)

    def angular_error_to(self, target_body: str) -> Tuple[float, float, bool]:
        """Angular error (yaw, pitch) between the head's aiming axis
        (+X, per look_at_world_pose's own convention) and the target."""
        tid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, target_body)
        if tid < 0:
            return 0.0, 0.0, False

        head_pose = self.get_head_pose()
        target_pos = self.data.xpos[tid]
        rel = target_pos - head_pose[:3, 3]
        rel_head = head_pose[:3, :3].T @ rel
        if np.linalg.norm(rel_head) < 1e-6:
            return 0.0, 0.0, False

        # Head aiming convention: +X = forward, +Y = left, +Z = up
        # (matches look_at_world_pose's straight_head_vector = [1,0,0]).
        yaw_err = math.atan2(rel_head[1], rel_head[0])
        horiz = math.hypot(rel_head[0], rel_head[1])
        pitch_err = math.atan2(rel_head[2], horiz)

        fov_half = math.radians(60)
        visible = rel_head[0] > 0 and abs(yaw_err) < fov_half and abs(pitch_err) < fov_half
        return float(yaw_err), float(pitch_err), visible

    def get_target_world_pos(self, target_body: str) -> Optional[np.ndarray]:
        tid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, target_body)
        if tid < 0:
            return None
        return self.data.xpos[tid].copy()
