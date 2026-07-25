"""Headless MuJoCo simulation for the 2-DOF planar arm."""
import mujoco
import numpy as np

_ARM_XML = """
<mujoco model="simple_arm">
  <compiler angle="radian"/>
  <option timestep="0.005"/>
  <worldbody>
    <body name="link1" pos="0 0 0">
      <joint name="joint1" type="hinge" axis="0 0 1" range="-3.14 3.14" damping="3.0"/>
      <geom type="capsule" size="0.04" fromto="0 0 0 0.25 0 0"/>
      <body name="link2" pos="0.25 0 0">
        <joint name="joint2" type="hinge" axis="0 0 1" range="-3.14 3.14" damping="3.0"/>
        <geom type="capsule" size="0.03" fromto="0 0 0 0.2 0 0"/>
        <site name="end_effector" pos="0.2 0 0"/>
      </body>
    </body>
  </worldbody>
  <actuator>
    <position joint="joint1" kp="30" ctrllimited="true" ctrlrange="-3.14 3.14"/>
    <position joint="joint2" kp="30" ctrllimited="true" ctrlrange="-3.14 3.14"/>
  </actuator>
</mujoco>
"""

MAX_STEPS = 1200
SETTLE_VEL = 0.05
SUCCESS_THRESHOLD = 0.03


class SimArm01Simulator:
    """Closed-loop position-servo controller for the 2-DOF planar arm."""

    def __init__(self):
        self.model = mujoco.MjModel.from_xml_string(_ARM_XML)
        self.data = mujoco.MjData(self.model)

    def execute(self, target_qpos: list) -> dict:
        target = np.array(target_qpos, dtype=float)
        mujoco.mj_resetData(self.model, self.data)
        self.data.ctrl[:] = np.clip(target, -3.14, 3.14)

        steps = 0
        for steps in range(MAX_STEPS):
            self.data.ctrl[:] = np.clip(target, -3.14, 3.14)
            mujoco.mj_step(self.model, self.data)
            error = float(np.linalg.norm(self.data.qpos[:2] - target))
            stopped = np.max(np.abs(self.data.qvel[:2])) < SETTLE_VEL
            if error < SUCCESS_THRESHOLD and stopped:
                break

        error = float(np.linalg.norm(self.data.qpos[:2] - target))
        return {
            "joint_angles": self.data.qpos[:2].tolist(),
            "joint_velocities": self.data.qvel[:2].tolist(),
            "joint_error": round(error, 4),
            "success": error < SUCCESS_THRESHOLD,
            "collision": self.data.ncon > 0,
            "steps_taken": steps + 1,
        }
