"""ReachyTaskPolicy — Smooth FSM controller for Reachy Mini.

Features:
- Slew-rate limiting and exponential low-pass filtering on all 9 actuators
  to guarantee smooth, physically realistic servo motor movement (no snapping or "exorcist" spinning).
- Finite-State Machine phases: SCANNING → TRACKING → EXPRESSIVE → DONE
"""
import math
import numpy as np


class ReachyTaskPolicy:
    """FSM task policy with smooth motor trajectory generation."""

    PHASES = ["SCANNING", "TRACKING", "EXPRESSIVE", "DONE"]

    _TRACKING_STARTS_AT     = 1.0   # s — switch to TRACKING after scanning
    _EXPRESSIVE_MIN_FRAMES  = 30    # tracking_success_count to trigger EXPRESSIVE
    _EXPRESSIVE_DURATION    = 3.0   # s — expressive celebration duration

    # Max velocity limits per joint (rad/s) — calm, elegant servo motion
    _MAX_VELOCITY = np.array([
        0.6,   # [0] yaw_body (torso) — gentle, smooth torso turn (~34 deg/s)
        0.8,   # [1] stewart_1
        0.8,   # [2] stewart_2
        0.8,   # [3] stewart_3
        0.8,   # [4] stewart_4
        0.8,   # [5] stewart_5
        0.8,   # [6] stewart_6
        1.0,   # [7] right_antenna
        1.0,   # [8] left_antenna
    ])

    def __init__(self):
        self.phase             = "SCANNING"
        self._phase_start_time = 0.0
        self._expressive_start = None
        self._filtered_ctrl    = np.zeros(9, dtype=np.float64)
        self._last_sim_time    = 0.0

    def reset(self):
        self.phase             = "SCANNING"
        self._phase_start_time = 0.0
        self._expressive_start = None
        self._filtered_ctrl    = np.zeros(9, dtype=np.float64)
        self._last_sim_time    = 0.0

    # ── FSM transitions ────────────────────────────────────────────────────────

    def _transition(self, obs: dict, metrics_snapshot: dict | None = None):
        sim_time = float(obs.get("sim_time", 0.0))
        success_count = (metrics_snapshot or {}).get("tracking_success_count", 0)

        if self.phase == "SCANNING":
            if sim_time >= self._TRACKING_STARTS_AT:
                self.phase = "TRACKING"
                self._phase_start_time = sim_time

        elif self.phase == "TRACKING":
            if success_count >= self._EXPRESSIVE_MIN_FRAMES:
                self.phase = "EXPRESSIVE"
                self._phase_start_time = sim_time
                self._expressive_start = sim_time

        elif self.phase == "EXPRESSIVE":
            if self._expressive_start is not None:
                if (sim_time - self._expressive_start) >= self._EXPRESSIVE_DURATION:
                    self.phase = "DONE"

    # ── Control computation ────────────────────────────────────────────────────

    def compute_action(
        self,
        obs: dict,
        metrics_snapshot: dict | None = None,
    ) -> tuple[np.ndarray, str]:
        """Return (smooth_ctrl_array[9], phase_name)."""
        self._transition(obs, metrics_snapshot)

        t        = float(obs.get("sim_time", 0.0))
        dt       = max(t - self._last_sim_time, 0.002)
        self._last_sim_time = t

        phase    = self.phase
        target   = np.zeros(9, dtype=np.float64)

        # ── Yaw body (target[0]) ─────────────────────────────────────────────
        target_pos = np.array(obs.get("target_pos", np.array([0.6, 0.0, 0.03])))
        head_pos   = np.array(obs.get("head_pos",   np.zeros(3)))
        rel        = target_pos - head_pos
        target_yaw = math.atan2(rel[1], max(rel[0], 1e-3))

        if phase == "SCANNING":
            # Smooth torso sweep
            target[0] = 0.5 * math.sin(t * 0.8)

        elif phase == "TRACKING":
            # Direct P-control to target object
            target[0] = float(np.clip(target_yaw, -1.8, 1.8))

        elif phase == "EXPRESSIVE":
            # Torso gentle dance wiggle around target
            target[0] = float(np.clip(target_yaw + 0.12 * math.sin(t * 4.0), -1.8, 1.8))

        else:  # DONE
            target[0] = float(np.clip(target_yaw, -1.8, 1.8))

        # ── Stewart joints (target[1-6]) ─────────────────────────────────────
        if phase == "SCANNING":
            target[1] = 0.15 * math.sin(t * 0.8)
            target[2] = 0.15 * math.sin(t * 0.8 + math.pi / 3)
            target[3] = 0.15 * math.sin(t * 0.8 + 2 * math.pi / 3)
            target[4] = 0.15 * math.sin(t * 0.8 + math.pi)
            target[5] = 0.15 * math.sin(t * 0.8 + 4 * math.pi / 3)
            target[6] = 0.15 * math.sin(t * 0.8 + 5 * math.pi / 3)

        elif phase == "TRACKING":
            # Direct steady lock-on toward target
            target[1] = 0.0
            target[2] = 0.0
            target[3] = 0.0
            target[4] = 0.0
            target[5] = 0.0
            target[6] = 0.0

        elif phase == "EXPRESSIVE":
            # Smooth happy head tilt & nod
            target[1] =  0.35 * math.sin(t * 4.5)
            target[2] =  0.25 * math.sin(t * 4.5 + math.pi / 2)
            target[3] = -0.35 * math.sin(t * 4.5)
            target[4] = -0.25 * math.sin(t * 4.5 + math.pi / 2)
            target[5] =  0.35 * math.sin(t * 4.5 + math.pi)
            target[6] =  0.25 * math.sin(t * 4.5 + 3 * math.pi / 2)

        # ── Apply Slew-Rate & Low-Pass Filtering ─────────────────────────────
        # 1. Rate-limit maximum step change based on joint velocity limits
        max_step = self._MAX_VELOCITY * dt
        diff     = np.clip(target - self._filtered_ctrl, -max_step, max_step)
        self._filtered_ctrl += diff

        # 2. Smooth exponential filter (alpha = 0.08 for gentle, elegant motion)
        alpha = 0.08
        self._filtered_ctrl = self._filtered_ctrl + alpha * (target - self._filtered_ctrl)

        return self._filtered_ctrl.copy(), phase
