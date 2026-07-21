"""ReachyMiniEnvironment — loads the official Reachy Mini MJCF from the
installed `reachy_mini` package and exposes a clean step/obs interface.

Actuator layout (nu = 9):
  0  yaw_body       torso rotation         [-2.79, 2.79]
  1  stewart_1      neck Stewart leg 1     [-0.84, 1.40]
  2  stewart_2      neck Stewart leg 2     [-1.40, 1.22]
  3  stewart_3      neck Stewart leg 3     [-0.84, 1.40]
  4  stewart_4      neck Stewart leg 4     [-1.40, 0.84]
  5  stewart_5      neck Stewart leg 5     [-1.22, 1.40]
  6  stewart_6      neck Stewart leg 6     [-1.40, 0.84]
  7  right_antenna  passive (range [0,0])
  8  left_antenna   passive (range [0,0])
"""
import os
import pathlib
import tempfile
import numpy as np
import mujoco

# ─── Official MJCF path ────────────────────────────────────────────────────────
# The local bridge has a `reachy_mini/` folder that shadows the installed package.
# We resolve the installed package explicitly via sysconfig / site-packages.
def _find_installed_mjcf() -> pathlib.Path:
    """Return path to the official minimal.xml, bypassing local package shadowing."""
    import sysconfig, importlib.util

    # 1) Try all site-packages directories directly
    for site_dir in sysconfig.get_paths().values():
        candidate = (
            pathlib.Path(site_dir)
            / "reachy_mini" / "descriptions" / "reachy_mini"
            / "mjcf" / "scenes" / "minimal.xml"
        )
        if candidate.exists():
            return candidate

    # 2) Temporarily remove local shadowing entries from sys.path and re-import
    import sys
    _local_shadow = os.path.normcase(os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    ))
    saved = sys.path[:]
    sys.path = [p for p in sys.path
                if os.path.normcase(os.path.abspath(p)) != _local_shadow]
    try:
        spec = importlib.util.find_spec("reachy_mini")
        if spec and spec.origin:
            candidate = (
                pathlib.Path(spec.origin).parent
                / "descriptions" / "reachy_mini"
                / "mjcf" / "scenes" / "minimal.xml"
            )
            if candidate.exists():
                return candidate
    except Exception:
        pass
    finally:
        sys.path = saved

    return pathlib.Path("__nonexistent__")


_MJCF_PATH = _find_installed_mjcf()

# ─── Fallback procedural MJCF (box-based approximation) ───────────────────────
_FALLBACK_XML = """
<mujoco model="reachy_mini_fallback">
  <option timestep="0.002" gravity="0 0 -9.81"/>
  <default>
    <joint damping="0.3" armature="0.01"/>
    <geom density="500" friction="1 0.5 0.5"/>
    <position kp="30" kv="3"/>
  </default>

  <worldbody>
    <geom name="ground" type="plane" size="3 3 0.1" rgba="0.85 0.85 0.9 1"/>
    <light pos="0 0 4" dir="0 0 -1" directional="true"/>

    <!-- Table at (0.35, 0, 0.0) with surface ~z=0.0 -->
    <body name="table" pos="0.35 0 -0.8">
      <geom name="table_top" type="box" size="0.4 0.4 0.02" rgba="0.3 0.2 0.1 1"/>
    </body>

    <!-- Objects on table -->
    <body name="apple" pos="0.6 -0.2 0.03">
      <freejoint name="apple_free"/>
      <geom name="apple_geom" type="sphere" size="0.04" rgba="0.9 0.1 0.1 1" mass="0.1"/>
    </body>
    <body name="croissant" pos="0.6 0.1 0.03">
      <freejoint name="croissant_free"/>
      <geom name="croissant_geom" type="box" size="0.04 0.025 0.02" rgba="0.9 0.7 0.2 1" mass="0.08"/>
    </body>
    <body name="duck" pos="0.6 0.3 0.0">
      <freejoint name="duck_free"/>
      <geom name="duck_geom" type="ellipsoid" size="0.04 0.03 0.05" rgba="1.0 0.9 0.1 1" mass="0.05"/>
    </body>

    <!-- Robot base -->
    <body name="body_foot_3dprint" pos="0 0 0">
      <geom name="base_geom" type="cylinder" size="0.09 0.05" rgba="0.2 0.2 0.25 1"/>

      <!-- Torso with yaw joint -->
      <body name="torso_yaw" pos="0 0 0.05">
        <joint name="yaw_body" type="hinge" axis="0 0 1" range="-2.79 2.79"/>
        <geom name="torso_geom" type="capsule" size="0.06 0.12" rgba="0.9 0.9 0.9 1"/>

        <!-- Neck platform / Stewart base -->
        <body name="neck_base" pos="0 0 0.25">
          <!-- Stewart legs (simplified as hinge joints on a plate) -->
          <joint name="stewart_1" type="hinge" axis="1 0 0" range="-0.84 1.40"/>
          <joint name="stewart_2" type="hinge" axis="0 1 0" range="-1.40 1.22"/>
          <joint name="stewart_3" type="hinge" axis="1 0 0" range="-0.84 1.40"/>
          <joint name="stewart_4" type="hinge" axis="0 1 0" range="-1.40 0.84"/>
          <joint name="stewart_5" type="hinge" axis="1 0 0" range="-1.22 1.40"/>
          <joint name="stewart_6" type="hinge" axis="0 1 0" range="-1.40 0.84"/>
          <geom name="neck_plate" type="cylinder" size="0.04 0.01" rgba="0.3 0.3 0.4 1"/>

          <!-- Head (xl_330 equivalent) -->
          <body name="xl_330" pos="0 0 0.12">
            <geom name="head_sphere" type="sphere" size="0.075" rgba="0.95 0.95 1.0 1"/>
            <geom name="eye_l" type="sphere" size="0.012" pos="-0.03 0.065 0.02" rgba="0.1 0.1 0.1 1"/>
            <geom name="eye_r" type="sphere" size="0.012" pos="0.03 0.065 0.02" rgba="0.1 0.1 0.1 1"/>

            <!-- Antennae -->
            <body name="right_antenna_body" pos="0.035 0.0 0.07">
              <joint name="right_antenna" type="hinge" axis="0 1 0" range="-0.8 0.8"/>
              <geom type="capsule" size="0.005 0.05" pos="0 0 0.05" rgba="0.2 0.6 1.0 1"/>
            </body>
            <body name="left_antenna_body" pos="-0.035 0.0 0.07">
              <joint name="left_antenna" type="hinge" axis="0 1 0" range="-0.8 0.8"/>
              <geom type="capsule" size="0.005 0.05" pos="0 0 0.05" rgba="0.2 0.6 1.0 1"/>
            </body>
          </body>
        </body>
      </body>
    </body>
  </worldbody>

  <actuator>
    <position name="yaw_body"   joint="yaw_body"   kp="30" ctrlrange="-2.79 2.79"/>
    <position name="stewart_1"  joint="stewart_1"  kp="20" ctrlrange="-0.84 1.40"/>
    <position name="stewart_2"  joint="stewart_2"  kp="20" ctrlrange="-1.40 1.22"/>
    <position name="stewart_3"  joint="stewart_3"  kp="20" ctrlrange="-0.84 1.40"/>
    <position name="stewart_4"  joint="stewart_4"  kp="20" ctrlrange="-1.40 0.84"/>
    <position name="stewart_5"  joint="stewart_5"  kp="20" ctrlrange="-1.22 1.40"/>
    <position name="stewart_6"  joint="stewart_6"  kp="20" ctrlrange="-1.40 0.84"/>
    <position name="right_antenna" joint="right_antenna" kp="5" ctrlrange="-0.8 0.8"/>
    <position name="left_antenna"  joint="left_antenna"  kp="5" ctrlrange="-0.8 0.8"/>
  </actuator>
</mujoco>
"""

# Body IDs in the official MJCF (from inspection)
_BODY_IDS_OFFICIAL = {
    "xl_330":             15,
    "body_foot_3dprint":   1,
    "apple":              20,
    "croissant":          19,
    "duck":               22,
}

_TARGET_OBJECTS = {
    "apple":    "apple",
    "croissant": "croissant",
    "duck":     "duck",
}


class ReachyMiniEnvironment:
    """MuJoCo simulation environment for the official Reachy Mini robot.

    Supports 9 actuators:
      [0] yaw_body, [1-6] stewart_1..6, [7] right_antenna, [8] left_antenna
    """

    def __init__(self, xml_path: str = None):
        self._using_official = False
        self._body_ids = {}

        # Priority: explicit path → official package path → fallback
        candidate = pathlib.Path(xml_path) if xml_path else _MJCF_PATH

        if candidate.exists():
            try:
                self.model = mujoco.MjModel.from_xml_path(str(candidate))
                self._using_official = True
                self._resolve_body_ids()
                print(f"[ReachyMiniEnvironment] Loaded official MJCF: {candidate}")
            except Exception as exc:
                print(f"[ReachyMiniEnvironment] MJCF load failed ({exc}), falling back.")
                self._load_fallback()
        else:
            print(f"[ReachyMiniEnvironment] Official MJCF not found at {candidate}; using fallback.")
            self._load_fallback()

        self.data = mujoco.MjData(self.model)
        self.num_actuators = self.model.nu

        # Default target
        self._target_object = "apple"

    # ── Internal helpers ────────────────────────────────────────────────────────

    def _load_fallback(self):
        with tempfile.NamedTemporaryFile(
            suffix=".xml", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(_FALLBACK_XML)
            tmp = f.name
        try:
            self.model = mujoco.MjModel.from_xml_path(tmp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self._using_official = False
        self._resolve_body_ids()

    def _resolve_body_ids(self):
        """Look up body IDs by name; fall back to hardcoded IDs for official MJCF."""
        body_names = ["xl_330", "body_foot_3dprint", "apple", "croissant", "duck"]
        for name in body_names:
            bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
            if bid >= 0:
                self._body_ids[name] = bid
            elif self._using_official and name in _BODY_IDS_OFFICIAL:
                self._body_ids[name] = _BODY_IDS_OFFICIAL[name]
            else:
                self._body_ids[name] = -1

    def _get_body_pos(self, name: str) -> np.ndarray:
        bid = self._body_ids.get(name, -1)
        if bid < 0:
            return np.zeros(3)
        return self.data.xpos[bid].copy()

    def _get_body_xmat(self, name: str) -> np.ndarray:
        bid = self._body_ids.get(name, -1)
        if bid < 0:
            return np.eye(3).flatten()
        return self.data.xmat[bid].copy()

    def _get_stewart_qpos(self) -> np.ndarray:
        """Return the 6 Stewart joint positions (indices depend on MJCF)."""
        result = np.zeros(6)
        for i, jname in enumerate(
            ["stewart_1", "stewart_2", "stewart_3", "stewart_4", "stewart_5", "stewart_6"]
        ):
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, jname)
            if jid >= 0:
                qadr = self.model.jnt_qposadr[jid]
                result[i] = float(self.data.qpos[qadr])
        return result

    def _get_antenna_qpos(self) -> np.ndarray:
        """Return [right_antenna, left_antenna] joint positions."""
        result = np.zeros(2)
        for i, jname in enumerate(["right_antenna", "left_antenna"]):
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, jname)
            if jid >= 0:
                qadr = self.model.jnt_qposadr[jid]
                result[i] = float(self.data.qpos[qadr])
        return result

    def _get_base_yaw(self) -> float:
        jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "yaw_body")
        if jid >= 0:
            return float(self.data.qpos[self.model.jnt_qposadr[jid]])
        # fallback: qpos[0]
        if len(self.data.qpos) > 0:
            return float(self.data.qpos[0])
        return 0.0

    def _get_eye_cam_info(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (eye_cam_pos, eye_cam_fwd_vec)."""
        cid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, "eye_camera")
        if cid >= 0:
            pos = self.data.cam_xpos[cid].copy()
            xmat = self.data.cam_xmat[cid].reshape(3, 3)
            fwd = -xmat[:, 2]  # view direction (-Z in camera local frame)
            return pos, fwd
        head_pos = self._get_body_pos("xl_330")
        head_xmat = self._get_body_xmat("xl_330").reshape(3, 3)
        return head_pos, head_xmat[:, 0]

    # ── Public API ──────────────────────────────────────────────────────────────

    def reset(self, target_object: str = "apple") -> dict:
        """Reset simulation and select the tracked target object."""
        self._target_object = target_object if target_object in _TARGET_OBJECTS else "apple"
        mujoco.mj_resetData(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)
        return self.get_obs()

    def set_control(self, ctrl: np.ndarray):
        """Set actuator control targets. Expects 9 elements."""
        ctrl = np.asarray(ctrl, dtype=np.float64)
        n = min(len(ctrl), self.model.nu)
        self.data.ctrl[:n] = ctrl[:n]

    def step(self, steps: int = 5) -> dict:
        for _ in range(steps):
            mujoco.mj_step(self.model, self.data)
        return self.get_obs()

    def get_obs(self) -> dict:
        target_pos = self._get_body_pos(self._target_object)
        eye_pos, eye_fwd = self._get_eye_cam_info()

        return {
            "sim_time":      float(self.data.time),
            "head_pos":      self._get_body_pos("xl_330"),
            "head_xmat":     self._get_body_xmat("xl_330"),
            "eye_cam_pos":   eye_pos,
            "eye_cam_fwd":   eye_fwd,
            "base_yaw":      self._get_base_yaw(),
            "apple_pos":     self._get_body_pos("apple"),
            "croissant_pos": self._get_body_pos("croissant"),
            "duck_pos":      self._get_body_pos("duck"),
            "target_pos":    target_pos,
            "stewart_qpos":  self._get_stewart_qpos(),
            "antenna_qpos":  self._get_antenna_qpos(),
            "num_contacts":  int(self.data.ncon),
        }
