"""Simulator-independent closed-loop policy for the G1 inspection station."""

from __future__ import annotations

from dataclasses import dataclass


POLICY_ID = "unitree-g1-29dof-active-inspection-v1-shared"
VALID_TARGETS = ("left", "center", "right")

# Joint order: waist yaw/roll/pitch, left shoulder pitch/roll/elbow/wrist
# pitch, right shoulder pitch/roll/elbow/wrist pitch. These are bounded goals
# for the official G1 29-DoF joints. Progression is gated by measured state.
TARGET_POSES = {
    "left": (0.42, 0.04, 0.02, -0.75, 1.00, 1.20, -0.24, -0.05, -0.12, 0.25, 0.00),
    "center": (0.00, 0.00, -0.04, -0.90, 0.52, 1.05, -0.12, -0.90, -0.52, 1.05, -0.12),
    "right": (-0.42, -0.04, 0.02, -0.05, 0.12, 0.25, 0.00, -0.75, -1.00, 1.20, -0.24),
}


@dataclass(frozen=True)
class InspectionPlan:
    phase: str
    target: str | None
    target_index: int
    target_count: int
    commanded_inspection_joints: tuple[float, ...]
    max_joint_error_rad: float


class G1InspectionControlCore:
    """Feedback-driven target sequencer shared by MuJoCo and Webots."""

    ERROR_TOLERANCE_RAD = 0.075
    DWELL_SECONDS = 0.55

    def __init__(self, targets: tuple[str, ...] = VALID_TARGETS, speed_scale: float = 1.0):
        if not targets or len(targets) > 3 or any(item not in VALID_TARGETS for item in targets):
            raise ValueError("targets must contain one to three left/center/right entries")
        if len(set(targets)) != len(targets):
            raise ValueError("targets must not contain duplicates")
        if isinstance(speed_scale, bool) or not 0.5 <= float(speed_scale) <= 1.0:
            raise ValueError("speed_scale must be between 0.5 and 1.0")
        self.targets = targets
        self.speed_scale = float(speed_scale)
        self.target_index = 0
        self.phase = "TRACKING"
        self._within_tolerance_since: float | None = None
        self.completed_targets: list[str] = []
        self.target_confirmations: list[dict] = []

    def reset(self) -> None:
        self.target_index = 0
        self.phase = "TRACKING"
        self._within_tolerance_since = None
        self.completed_targets = []
        self.target_confirmations = []

    def compute_plan(self, observation: dict) -> InspectionPlan:
        if self.phase == "COMPLETE":
            return InspectionPlan("COMPLETE", None, self.target_index, len(self.targets), (0.0,) * 11, 0.0)
        target = self.targets[self.target_index]
        commanded = TARGET_POSES[target]
        measured = tuple(float(value) for value in observation["inspection_joint_positions"])
        if len(measured) != len(commanded):
            raise ValueError(f"expected {len(commanded)} inspection joints, received {len(measured)}")
        error = max(abs(goal - actual) for goal, actual in zip(commanded, measured, strict=True))
        now = float(observation["sim_time"])
        if error <= self.ERROR_TOLERANCE_RAD:
            if self._within_tolerance_since is None:
                self._within_tolerance_since = now
            elif now - self._within_tolerance_since >= self.DWELL_SECONDS / self.speed_scale:
                self.completed_targets.append(target)
                self.target_confirmations.append(
                    {"target": target, "confirmed_at_sec": round(now, 3), "max_joint_error_rad": round(error, 6)}
                )
                self.target_index += 1
                self._within_tolerance_since = None
                if self.target_index == len(self.targets):
                    self.phase = "COMPLETE"
        else:
            self._within_tolerance_since = None
        return InspectionPlan(self.phase, target, self.target_index, len(self.targets), commanded, error)

    def diagnostics(self, plan: InspectionPlan) -> dict:
        return {
            "policy_id": POLICY_ID,
            "phase": plan.phase,
            "active_target": plan.target,
            "active_target_index": plan.target_index,
            "target_count": plan.target_count,
            "completed_targets": list(self.completed_targets),
            "target_confirmations": list(self.target_confirmations),
            "max_joint_error_rad": plan.max_joint_error_rad,
            "parameters": {
                "targets": list(self.targets),
                "error_tolerance_rad": self.ERROR_TOLERANCE_RAD,
                "dwell_seconds": self.DWELL_SECONDS,
                "speed_scale": self.speed_scale,
                "support_fixture": "pelvis safety fixture with feet on floor",
            },
        }
