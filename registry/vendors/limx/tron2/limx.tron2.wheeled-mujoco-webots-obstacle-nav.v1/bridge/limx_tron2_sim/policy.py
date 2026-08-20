"""Direct adapter for LimX's pinned WF_TRON2A Isaac Gym ONNX policy."""

from __future__ import annotations

import numpy as np

from .model import ENCODER_ONNX, POLICY_ONNX


JOINT_NAMES = (
    "proximal_pitch_L_Joint",
    "proximal_roll_L_Joint",
    "proximal_yaw_L_Joint",
    "knee_L_Joint",
    "wheel_L_Joint",
    "proximal_pitch_R_Joint",
    "proximal_roll_R_Joint",
    "proximal_yaw_R_Joint",
    "knee_R_Joint",
    "wheel_R_Joint",
)
NON_WHEEL = (0, 1, 2, 3, 5, 6, 7, 8)
WHEELS = (4, 9)
LIGHT_JOINTS = (2, 7)
STAND_TARGET = np.zeros(10, dtype=np.float64)


def quaternion_matrix(quat_wxyz: np.ndarray) -> np.ndarray:
    w, x, y, z = (float(value) for value in quat_wxyz)
    norm = max(w * w + x * x + y * y + z * z, 1e-12)
    scale = 2.0 / norm
    return np.array(
        [
            [1 - scale * (y * y + z * z), scale * (x * y - z * w), scale * (x * z + y * w)],
            [scale * (x * y + z * w), 1 - scale * (x * x + z * z), scale * (y * z - x * w)],
            [scale * (x * z - y * w), scale * (y * z + x * w), 1 - scale * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


class LimXOnnxPolicy:
    """Inference and torque mapping matching LimX's published controller."""

    def __init__(self) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        providers = ["CPUExecutionProvider"]
        self.policy = ort.InferenceSession(str(POLICY_ONNX), sess_options=options, providers=providers)
        self.encoder = ort.InferenceSession(str(ENCODER_ONNX), sess_options=options, providers=providers)
        self.policy_input = self.policy.get_inputs()[0].name
        self.encoder_input = self.encoder.get_inputs()[0].name
        self.history = np.zeros(340, dtype=np.float32)
        self.last_actions = np.zeros(10, dtype=np.float64)
        self.initialized = False

    def _observation(
        self,
        q: np.ndarray,
        dq: np.ndarray,
        quat_wxyz: np.ndarray,
        gyro: np.ndarray,
    ) -> np.ndarray:
        rotation = quaternion_matrix(quat_wxyz)
        projected_gravity = rotation.T @ np.array([0.0, 0.0, -1.0])
        obs = np.concatenate(
            [
                np.asarray(gyro, dtype=np.float64) * 0.25,
                projected_gravity,
                np.asarray(q, dtype=np.float64)[list(NON_WHEEL)],
                np.asarray(dq, dtype=np.float64) * 0.05,
                self.last_actions,
            ]
        ).astype(np.float32)
        if not self.initialized:
            self.history = np.tile(obs, 10)
            self.initialized = True
        else:
            self.history[:-34] = self.history[34:]
            self.history[-34:] = obs
        return np.clip(obs, -100.0, 100.0)

    def actions(
        self,
        q: np.ndarray,
        dq: np.ndarray,
        quat_wxyz: np.ndarray,
        gyro: np.ndarray,
        command: tuple[float, float, float],
    ) -> np.ndarray:
        obs = self._observation(q, dq, quat_wxyz, gyro)
        encoded = self.encoder.run(None, {self.encoder_input: self.history.astype(np.float32)})[0].reshape(-1)
        scaled = np.asarray(command, dtype=np.float32) * np.array([0.7, 0.0, 1.0], dtype=np.float32)
        policy_input = np.concatenate([encoded, obs, scaled]).astype(np.float32)
        actions = self.policy.run(None, {self.policy_input: policy_input})[0].reshape(-1).astype(np.float64)
        self.last_actions = np.clip(actions, -100.0, 100.0)
        return self.last_actions.copy()

    @staticmethod
    def stand_torques(q: np.ndarray, dq: np.ndarray) -> np.ndarray:
        torques = np.zeros(10, dtype=np.float64)
        for index in NON_WHEEL:
            kp, kd = (53.22, 3.39) if index in LIGHT_JOINTS else (159.67, 10.16)
            limit = 40.0 if index in LIGHT_JOINTS else 140.0
            torques[index] = np.clip(kp * (STAND_TARGET[index] - q[index]) - kd * dq[index], -limit, limit)
        for index in WHEELS:
            torques[index] = np.clip(-0.6 * dq[index], -22.0, 22.0)
        return torques

    @staticmethod
    def action_torques(actions: np.ndarray, q: np.ndarray, dq: np.ndarray) -> np.ndarray:
        torques = np.zeros(10, dtype=np.float64)
        for index in NON_WHEEL:
            kp, kd = (53.22, 3.39) if index in LIGHT_JOINTS else (159.67, 10.16)
            limit = 40.0 if index in LIGHT_JOINTS else 140.0
            q_desired = 0.25 * actions[index]
            torques[index] = np.clip(kp * (q_desired - q[index]) - kd * dq[index], -limit, limit)
        for index in WHEELS:
            velocity_desired = actions[index]
            torques[index] = np.clip(0.6 * (velocity_desired - dq[index]), -22.0, 22.0)
        return torques
