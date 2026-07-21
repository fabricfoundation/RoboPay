"""Trot gait controller for the Unitree Go2 in MuJoCo.

Layers (top to bottom):
  velocity command (vx, vy, wz)
    -> trot gait generator (diagonal leg pairs, Raibert foot placement)
    -> per-leg analytic IK (abduction, thigh, calf)
    -> joint PD -> motor torques (the menagerie model uses torque actuators)

The controller is purely reactive: foot targets are recomputed every step
from the commanded velocity, so no trajectory is ever replayed.
"""

import math

import numpy as np

# Leg geometry from mujoco_menagerie/unitree_go2/go2.xml
HIP_OFFSET_X = 0.1934   # base -> hip joint, forward
HIP_OFFSET_Y = 0.0465   # base -> hip joint, lateral
THIGH_OFFSET = 0.0955   # hip joint -> thigh plane, lateral
L_THIGH = 0.213
L_CALF = 0.213

# Legs in actuator order: FL FR RL RR, each (sign_x, sign_y)
LEG_SIGNS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
LEG_NAMES = ["FL", "FR", "RL", "RR"]

STAND_HEIGHT = 0.27
STAND_JOINTS = np.array([0.0, 0.9, -1.8] * 4)

# Gait parameters
GAIT_PERIOD = 0.35      # s, full trot cycle (fast steps stabilize the trot)
SWING_HEIGHT = 0.06     # m, foot clearance
# Diagonal pairs: FL+RR at phase 0, FR+RL at 0.5
LEG_PHASE = [0.0, 0.5, 0.5, 0.0]

# Joint PD gains (per leg: abduction, thigh, calf)
KP = np.array([100.0, 100.0, 120.0] * 4)
KD = np.array([3.0, 3.0, 3.0] * 4)
TORQUE_LIMIT = np.array([23.7, 23.7, 45.43] * 4)
RAIBERT_GAIN = 0.05     # touchdown correction per m/s of velocity error
SPEED_GAIN = np.array([3.0, 1.0])  # stance-sweep boost per (x, y) m/s of
                                   # velocity deficit; 3.0 laterally overshoots


def leg_ik(px, py, pz, side):
    """Foot position in hip-joint frame -> (abduction, thigh, calf) angles.

    Hip frame: x forward, y left, z up, origin at the abduction joint.
    side: +1 for left legs, -1 for right legs.
    """
    d = THIGH_OFFSET * side
    r_yz = math.sqrt(py * py + pz * pz)
    l_leg = math.sqrt(max(r_yz * r_yz - d * d, 1e-9))
    q_abd = math.atan2(pz, py) + math.acos(min(max(d / max(r_yz, 1e-9), -1.0), 1.0))
    # Sagittal plane target after removing abduction rotation
    u = -px
    v = l_leg
    r_sq = u * u + v * v
    cos_knee = (r_sq - L_THIGH**2 - L_CALF**2) / (2 * L_THIGH * L_CALF)
    q_calf = -math.acos(min(max(cos_knee, -1.0), 1.0))
    q_thigh = math.atan2(u, v) - math.atan2(
        L_CALF * math.sin(q_calf), L_THIGH + L_CALF * math.cos(q_calf))
    return q_abd, q_thigh, q_calf


def foot_fk(q_abd, q_thigh, q_calf, side):
    """Forward kinematics matching leg_ik, for self-checks."""
    x = -L_THIGH * math.sin(q_thigh) - L_CALF * math.sin(q_thigh + q_calf)
    z_plane = -L_THIGH * math.cos(q_thigh) - L_CALF * math.cos(q_thigh + q_calf)
    d = THIGH_OFFSET * side
    c, s = math.cos(q_abd), math.sin(q_abd)
    y = d * c - z_plane * s
    z = d * s + z_plane * c
    return x, y, z


class TrotController:
    """Velocity-commanded trot for the 12-motor Go2 model."""

    def __init__(self, dt):
        self.dt = dt
        self.phase = 0.0
        self.cmd = np.zeros(3)          # vx, vy, wz (body frame)
        self.standing = True
        self._v_filt = np.zeros(3)      # low-passed body velocity for placement
        self._prev_q_des = None

    def set_command(self, vx, vy, wz):
        self.cmd = np.array([vx, vy, wz])
        self.standing = not np.any(np.abs(self.cmd) > 1e-3)

    @staticmethod
    def _hip_velocity(vx, vy, wz, hip_xy):
        """Velocity of the body at a hip location: v + w x r."""
        return np.array([vx - wz * hip_xy[1], vy + wz * hip_xy[0]])

    def _foot_target(self, leg, phase, v_act):
        """Desired foot position in the leg's hip-joint frame.

        Touchdown point follows the Raibert heuristic on the *measured*
        velocity, so at gait start feet land under the hips and the stance
        sweep produces net propulsion instead of a brake/thrust cancel.
        """
        sx, sy = LEG_SIGNS[leg]
        home = np.array([0.0, THIGH_OFFSET * sy, -STAND_HEIGHT])
        hip_xy = (HIP_OFFSET_X * sx, (HIP_OFFSET_Y + THIGH_OFFSET) * sy)
        vc = self._hip_velocity(self.cmd[0], self.cmd[1], self.cmd[2], hip_xy)
        va = self._hip_velocity(v_act[0], v_act[1], v_act[2], hip_xy)
        t_stance = GAIT_PERIOD / 2
        touchdown = va * t_stance / 2 + RAIBERT_GAIN * (va - vc)
        touchdown = np.clip(touchdown, -0.15, 0.15)
        # slip/compliance eats stride, so close the loop on measured speed
        v_sweep = np.clip(vc + SPEED_GAIN * (vc - va), -0.8, 0.8)
        liftoff = touchdown - v_sweep * t_stance

        p = (phase + LEG_PHASE[leg]) % 1.0
        if p < 0.5:   # stance: sweep back from touchdown
            s = p / 0.5
            xy = home[:2] + touchdown - v_sweep * (s * t_stance)
            z = home[2]
        else:         # swing: sine-profiled step to the next touchdown
            s = (p - 0.5) / 0.5
            xy = home[:2] + liftoff + (touchdown - liftoff) * s
            z = home[2] + SWING_HEIGHT * math.sin(math.pi * s)
        return np.array([xy[0], xy[1], z])

    def compute(self, qpos, qvel):
        """qpos/qvel: full mujoco state. Returns 12 motor torques."""
        q = qpos[7:19]
        dq = qvel[6:18]
        if self.standing:
            q_des = STAND_JOINTS
            qd_des = np.zeros(12)
            self._prev_q_des = None
        else:
            self.phase = (self.phase + self.dt / GAIT_PERIOD) % 1.0
            # world -> body rotation of the base linear velocity
            w, x, y, z = qpos[3:7]
            vw = qvel[0:3]
            vx_b = (1 - 2 * (y * y + z * z)) * vw[0] + 2 * (x * y + w * z) * vw[1] \
                + 2 * (x * z - w * y) * vw[2]
            vy_b = 2 * (x * y - w * z) * vw[0] + (1 - 2 * (x * x + z * z)) * vw[1] \
                + 2 * (y * z + w * x) * vw[2]
            alpha = self.dt / 0.1   # ~0.1 s low-pass: strip trot rocking
            self._v_filt += alpha * (np.array([vx_b, vy_b, qvel[5]]) - self._v_filt)
            q_des = np.zeros(12)
            for leg in range(4):
                p = self._foot_target(leg, self.phase, self._v_filt)
                q_des[leg * 3: leg * 3 + 3] = leg_ik(
                    p[0], p[1], p[2], LEG_SIGNS[leg][1])
            prev = self._prev_q_des
            qd_des = np.zeros(12) if prev is None else \
                np.clip((q_des - prev) / self.dt, -15.0, 15.0)
            self._prev_q_des = q_des
        tau = KP * (q_des - q) + KD * (qd_des - dq)
        return np.clip(tau, -TORQUE_LIMIT, TORQUE_LIMIT)
