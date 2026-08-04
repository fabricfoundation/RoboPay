"""SimulationMetricsTracker — head-tracking metrics for Reachy Mini.

Computes the angular error between the head forward vector and the direction
to the target object, as well as expressiveness (antenna activity).
"""
import numpy as np


class SimulationMetricsTracker:
    """Tracks head-tracking quality metrics for the Reachy Mini."""

    _SUCCESS_THRESHOLD_RAD = 0.65  # ~37° — FOV cone for Stewart neck geometry
    _MIN_SIM_TIME_DONE     = 3.0   # seconds before task can be flagged complete
    _MIN_SUCCESS_RATE_DONE = 0.30  # fraction of overall steps in FOV lock-on

    def __init__(self):
        self._total_steps            = 0
        self._tracking_steps         = 0
        self._tracking_success_count = 0
        self._angular_errors         = []
        self._antenna_activity       = 0.0
        self._fov_seconds            = 0.0
        self._last_sim_time          = 0.0
        self.task_completed          = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def reset(self, obs: dict):
        self._total_steps            = 0
        self._tracking_steps         = 0
        self._tracking_success_count = 0
        self._angular_errors         = []
        self._antenna_activity       = 0.0
        self._fov_seconds            = 0.0
        self._last_sim_time          = float(obs.get("sim_time", 0.0))
        self.task_completed          = False

    def update(self, obs: dict) -> dict:
        """Compute per-step metrics and return the current summary."""
        sim_time = float(obs.get("sim_time", 0.0))
        dt = max(sim_time - self._last_sim_time, 0.0)
        self._last_sim_time = sim_time
        self._total_steps += 1

        # ── Angular tracking error ────────────────────────────────────────────
        error = self._compute_angular_error(obs)
        self._angular_errors.append(error)

        success = error < self._SUCCESS_THRESHOLD_RAD
        if success:
            self._tracking_success_count += 1
            self._fov_seconds += dt

        # Track steps after scanning phase (sim_time >= 1.0s)
        if sim_time >= 1.0:
            self._tracking_steps += 1

        # ── Antenna activity (expressiveness) ─────────────────────────────────
        aq = obs.get("antenna_qpos", np.zeros(2))
        self._antenna_activity += float(np.sum(np.abs(aq)))

        # ── Task completion check ─────────────────────────────────────────────
        rate = self._tracking_success_count / max(self._total_steps, 1)
        if rate >= self._MIN_SUCCESS_RATE_DONE and sim_time >= self._MIN_SIM_TIME_DONE:
            self.task_completed = True

        return self.get_summary()

    def get_summary(self) -> dict:
        n = max(self._total_steps, 1)
        active_n = max(self._tracking_steps, 1)
        errors = self._angular_errors if self._angular_errors else [0.0]

        overall_rate = self._tracking_success_count / n
        active_rate  = min(1.0, self._tracking_success_count / active_n)

        # Success rate score: 1.0 if task_completed, else proportional to lock-on
        score = 1.0 if self.task_completed else float(np.clip(overall_rate / self._MIN_SUCCESS_RATE_DONE, 0.0, 1.0))

        return {
            "head_tracking_error_rad":  float(np.mean(errors)),
            "min_tracking_error_rad":   float(np.min(errors)),
            "tracking_success_count":   int(self._tracking_success_count),
            "tracking_success_rate":    float(round(active_rate, 3)),
            "overall_fov_lock_rate":    float(round(overall_rate, 3)),
            "object_in_fov_seconds":    float(round(self._fov_seconds, 2)),
            "antenna_activity":         float(round(self._antenna_activity, 3)),
            "task_completed":           bool(self.task_completed),
            "success_rate_score":       float(round(score, 3)),
        }

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _compute_angular_error(obs: dict) -> float:
        """Angular error between eye camera view vector and direction to target."""
        eye_pos = np.array(obs.get("eye_cam_pos", np.zeros(3)))
        eye_fwd = np.array(obs.get("eye_cam_fwd", np.array([1.0, 0.0, 0.0])))
        target_pos = np.array(obs.get("target_pos", np.array([0.6, 0.0, 0.03])))

        target_dir = target_pos - eye_pos
        norm = np.linalg.norm(target_dir)
        if norm < 1e-6:
            return 0.0
        target_dir /= norm

        cos_angle = float(np.clip(np.dot(eye_fwd, target_dir), -1.0, 1.0))
        return float(np.arccos(cos_angle))
