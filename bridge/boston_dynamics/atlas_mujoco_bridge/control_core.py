"""Atlas 30-DOF sinusoidal gait controller.

Uses capsule collision model (atlas_working.xml) with tuned gear ratios.
Strategy: sinusoidal gait with balance corrections from torso orientation feedback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


POLICY_ID = "atlas-sinusoidal-gait-v1"

ACTUATOR_ORDER = [
    "back_bkz", "back_bky", "back_bkx",
    "l_leg_hpz", "l_leg_hpx", "l_leg_hpy",
    "l_leg_kny", "l_leg_aky", "l_leg_akx",
    "r_leg_hpz", "r_leg_hpx", "r_leg_hpy",
    "r_leg_kny", "r_leg_aky", "r_leg_akx",
    "l_arm_shz", "l_arm_shx", "l_arm_ely",
    "l_arm_elx", "l_arm_uwy", "l_arm_mwx",
    "l_arm_lwy",
    "r_arm_shz", "r_arm_shx", "r_arm_ely",
    "r_arm_elx", "r_arm_uwy", "r_arm_mwx",
    "r_arm_lwy",
    "neck_ay",
]

NEUTRAL_POSE = {
    "back_bkz": 0.0,
    "back_bky": 0.15,
    "back_bkx": 0.0,
    "l_leg_hpz": 0.02,
    "l_leg_hpx": 0.05,
    "l_leg_hpy": -0.3,
    "l_leg_kny": 0.6,
    "l_leg_aky": -0.3,
    "l_leg_akx": -0.05,
    "r_leg_hpz": -0.02,
    "r_leg_hpx": -0.05,
    "r_leg_hpy": -0.3,
    "r_leg_kny": 0.6,
    "r_leg_aky": -0.3,
    "r_leg_akx": 0.05,
    "l_arm_shz": 0.0,
    "l_arm_shx": 0.0,
    "l_arm_ely": 0.5,
    "l_arm_elx": -0.5,
    "l_arm_uwy": 0.0,
    "l_arm_mwx": 0.0,
    "l_arm_lwy": 0.0,
    "r_arm_shz": 0.0,
    "r_arm_shx": 0.0,
    "r_arm_ely": 0.5,
    "r_arm_elx": -0.5,
    "r_arm_uwy": 0.0,
    "r_arm_mwx": 0.0,
    "r_arm_lwy": 0.0,
    "neck_ay": 0.0,
}

NEUTRAL_ARRAY = [NEUTRAL_POSE[name] for name in ACTUATOR_ORDER]

EFFORT_LIMITS = {
    "back_bkz": 106, "back_bky": 445, "back_bkx": 300,
    "l_leg_hpz": 275, "l_leg_hpx": 530, "l_leg_hpy": 840,
    "l_leg_kny": 890, "l_leg_aky": 740, "l_leg_akx": 360,
    "r_leg_hpz": 275, "r_leg_hpx": 530, "r_leg_hpy": 840,
    "r_leg_kny": 890, "r_leg_aky": 740, "r_leg_akx": 360,
    "l_arm_shz": 87, "l_arm_shx": 99, "l_arm_ely": 63,
    "l_arm_elx": 112, "l_arm_uwy": 25, "l_arm_mwx": 25,
    "l_arm_lwy": 25, "r_arm_shz": 87, "r_arm_shx": 99,
    "r_arm_ely": 63, "r_arm_elx": 112, "r_arm_uwy": 25,
    "r_arm_mwx": 25, "r_arm_lwy": 25, "neck_ay": 25,
}

GAIT_FREQ_HZ = 1.5
HIP_Y_AMPLITUDE = 0.3
KNEE_SWING_LIFT = 0.3
ANKLE_Y_AMPLITUDE = 0.2
ARM_SWING_AMPLITUDE = 0.3
KP = 500
KD = 100
STEER_GAIN = 0.10
STEER_LIMIT = 0.05
GOAL_REACHED_RADIUS_M = 0.5


def wrap_angle(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


@dataclass(frozen=True)
class Waypoint:
    x: float
    y: float


@dataclass
class GaitPlan:
    phase: str
    goal_distance: float
    heading_error_rad: float
    steering: float
    active_waypoint: int
    desired_joints: dict = field(default_factory=dict)
    swing_leg: str = "none"
    step_progress: float = 0.0


class AtlasObstacleControlCore:
    WAYPOINT_RADIUS_M = 0.40

    def __init__(
        self,
        goal: tuple[float, float],
        side: str = "left",
        reference_route: tuple[tuple[float, float], ...] | None = None,
        speed_scale: float = 1.0,
    ) -> None:
        if side != "left":
            raise ValueError("The validated Atlas corridor uses side='left'.")
        if isinstance(speed_scale, bool) or not 0.5 <= float(speed_scale) <= 1.0:
            raise ValueError("speed_scale must be between 0.5 and 1.0.")
        self.goal = Waypoint(*goal)
        self.side = side
        self.speed_scale = float(speed_scale)
        self._route = tuple(Waypoint(*item) for item in (reference_route or (goal,)))
        if self._route[-1] != self.goal:
            raise ValueError("reference_route must end at goal")
        self._waypoint_index = 0
        self.phase = "IDLE"

    def reset(self, start: tuple[float, float], obstacles) -> None:
        del start, obstacles
        self._waypoint_index = 0
        self.phase = "NAVIGATING"

    @property
    def waypoint_count(self) -> int:
        return len(self._route)

    @property
    def waypoints_completed(self) -> int:
        return self._waypoint_index

    @property
    def route(self) -> tuple[Waypoint, ...]:
        return self._route

    @property
    def gait_phase_name(self) -> str:
        return "GAITING"

    def _target(self, x: float, y: float) -> Waypoint:
        target = self._route[self._waypoint_index]
        if (
            math.hypot(target.x - x, target.y - y) <= self.WAYPOINT_RADIUS_M
            and self._waypoint_index < len(self._route) - 1
        ):
            self._waypoint_index += 1
            target = self._route[self._waypoint_index]
        return target

    def compute_plan(self, observation: dict) -> GaitPlan:
        x, y = map(float, observation["position"][:2])
        target = self._target(x, y)
        goal_distance = math.hypot(self.goal.x - x, self.goal.y - y)
        if self._waypoint_index == len(self._route) - 1 and goal_distance <= GOAL_REACHED_RADIUS_M:
            self.phase = "GOAL_REACHED"
            return GaitPlan(self.phase, goal_distance, 0.0, 0.0, self._waypoint_index)

        heading_error = wrap_angle(
            math.atan2(target.y - y, target.x - x) - float(observation["yaw"])
        )
        steering = max(-STEER_LIMIT, min(STEER_LIMIT, -STEER_GAIN * heading_error))

        sim_time = float(observation["sim_time"])
        gait_t = sim_time * GAIT_FREQ_HZ * self.speed_scale
        phase_l = 2.0 * math.pi * gait_t
        phase_r = phase_l + math.pi

        torso_pitch = float(observation.get("torso_pitch", 0.0))
        torso_roll = float(observation.get("torso_roll", 0.0))
        ang_vel = observation.get("angular_velocity", [0.0, 0.0, 0.0])
        pitch_rate = float(ang_vel[1]) if len(ang_vel) > 1 else 0.0
        roll_rate = float(ang_vel[0]) if len(ang_vel) > 0 else 0.0

        pitch_corr = -0.15 * torso_pitch - 0.04 * pitch_rate
        roll_corr = -0.10 * torso_roll - 0.03 * roll_rate

        desired = {
            "back_bky": 0.15 + pitch_corr,
            "back_bkx": roll_corr,
            "back_bkz": steering * 0.5,
            "l_leg_hpy": -0.3 + HIP_Y_AMPLITUDE * math.sin(phase_l),
            "l_leg_kny": 0.6 + KNEE_SWING_LIFT * max(0, math.sin(phase_l)),
            "l_leg_aky": -0.3 + ANKLE_Y_AMPLITUDE * math.sin(phase_l),
            "l_leg_hpx": 0.05,
            "l_leg_hpz": 0.02 + steering,
            "l_leg_akx": -0.05 - roll_corr * 0.2,
            "r_leg_hpy": -0.3 + HIP_Y_AMPLITUDE * math.sin(phase_r),
            "r_leg_kny": 0.6 + KNEE_SWING_LIFT * max(0, math.sin(phase_r)),
            "r_leg_aky": -0.3 + ANKLE_Y_AMPLITUDE * math.sin(phase_r),
            "r_leg_hpx": -0.05,
            "r_leg_hpz": -0.02 + steering,
            "r_leg_akx": 0.05 - roll_corr * 0.2,
            "l_arm_ely": 0.5 + ARM_SWING_AMPLITUDE * math.sin(phase_l),
            "l_arm_elx": -0.5,
            "r_arm_ely": 0.5 + ARM_SWING_AMPLITUDE * math.sin(phase_r),
            "r_arm_elx": -0.5,
        }

        step_progress = (gait_t % 1.0)

        return GaitPlan(
            phase=self.phase,
            goal_distance=goal_distance,
            heading_error_rad=heading_error,
            steering=steering,
            active_waypoint=self._waypoint_index,
            desired_joints=desired,
            swing_leg="right" if math.sin(phase_l) > 0 else "left",
            step_progress=step_progress,
        )

    def diagnostics(self, plan: GaitPlan) -> dict:
        return {
            "policy_id": POLICY_ID,
            "phase": plan.phase,
            "gait_phase": self.gait_phase_name,
            "goal_distance": plan.goal_distance,
            "heading_error_rad": plan.heading_error_rad,
            "steering": plan.steering,
            "active_waypoint": self._waypoint_index,
            "waypoints_completed": self._waypoint_index,
            "waypoint_count": self.waypoint_count,
            "route": [{"x": item.x, "y": item.y} for item in self.route],
            "swing_leg": plan.swing_leg,
            "step_progress": plan.step_progress,
            "parameters": {
                "gait_freq_hz": GAIT_FREQ_HZ,
                "hip_y_amplitude": HIP_Y_AMPLITUDE,
                "knee_swing_lift": KNEE_SWING_LIFT,
                "ankle_y_amplitude": ANKLE_Y_AMPLITUDE,
                "steer_limit": STEER_LIMIT,
                "kp": KP,
                "kd": KD,
                "model_constraints": {
                    "hip_y_gear": 840,
                    "knee_gear": 890,
                    "ankle_gear": 740,
                    "note": "Sinusoidal gait with PD torque control.",
                },
            },
        }
