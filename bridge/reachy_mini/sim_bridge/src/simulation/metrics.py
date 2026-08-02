"""Per-episode telemetry for a gaze-tracking run.

Produces the structured metrics the bounty requires: measurable simulator
state (angular error, FOV/lock status), not just a boolean "it worked".
"""
import statistics
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StepRecord:
    t: float
    yaw: float
    pitch: float
    torso_yaw: float
    angular_error_rad: Optional[float]
    visible: bool
    state: str


class EpisodeMetrics:
    def __init__(self, robot_id: str, simulator: str, target_name: str):
        self.robot_id = robot_id
        self.simulator = simulator
        self.target_name = target_name
        self._start = time.monotonic()
        self.records: List[StepRecord] = []

    def log(self, yaw: float, pitch: float, torso_yaw: float,
            angular_error_rad: Optional[float], visible: bool, state: str) -> None:
        self.records.append(StepRecord(
            t=time.monotonic() - self._start,
            yaw=yaw, pitch=pitch, torso_yaw=torso_yaw,
            angular_error_rad=angular_error_rad, visible=visible, state=state,
        ))

    def summary(self) -> dict:
        errs = [r.angular_error_rad for r in self.records
                if r.angular_error_rad is not None and r.angular_error_rad == r.angular_error_rad]
        visible_steps = sum(1 for r in self.records if r.visible)
        locked_steps = sum(1 for r in self.records if r.state == "LOCKED")
        n = len(self.records) or 1

        return {
            "robot_id": self.robot_id,
            "simulator": self.simulator,
            "task": "gaze_tracking",
            "target": self.target_name,
            "duration_s": round(self.records[-1].t, 3) if self.records else 0.0,
            "steps": len(self.records),
            "metrics": {
                "mean_angular_error_rad": round(statistics.mean(errs), 4) if errs else None,
                "min_angular_error_rad": round(min(errs), 4) if errs else None,
                "final_angular_error_rad": round(errs[-1], 4) if errs else None,
                "fov_visibility_rate": round(visible_steps / n, 3),
                "lock_rate": round(locked_steps / n, 3),
                "reached_lock": locked_steps > 0,
            },
        }
