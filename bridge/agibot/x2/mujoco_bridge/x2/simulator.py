"""Small headless MuJoCo controller for reproducible Tier-1 evidence."""
from pathlib import Path
import time
try:
    import mujoco
except ImportError:  # pragma: no cover
    mujoco = None

class X2Simulator:
    def __init__(self, model_path: str):
        if mujoco is None: raise RuntimeError("Install mujoco>=3.1 to run the X2 simulator")
        path = Path(model_path)
        if not path.is_file(): raise FileNotFoundError(path)
        self.model, self.data = mujoco.MjModel.from_xml_path(str(path)), None
        self.data = mujoco.MjData(self.model)
    def execute(self, action: str, duration: float = 1.0, params=None) -> dict:
        """Run a deterministic policy and require measurable simulator progress."""
        if action not in {"standing_balance", "wave_arm", "move_forward", "forward"}:
            raise ValueError(f"unsupported X2 policy: {action}")
        if duration <= 0 or duration > 30:
            raise ValueError("duration must be between 0 and 30 seconds")
        start, steps = time.monotonic(), max(1, int(duration / self.model.opt.timestep))
        qpos_before = self.data.qpos.copy()
        actuator_ids = {self.model.actuator(i).name: i for i in range(self.model.nu)}
        required = {"forward_policy", "left_shoulder_policy", "right_shoulder_policy"}
        if not required.issubset(actuator_ids):
            raise RuntimeError("model does not expose the required X2 policy actuators")
        for index in range(steps):
            self.data.ctrl[:] = 0
            if action in ("move_forward", "forward"):
                distance = min(float((params or {}).get("distance", 0.25)), 1.0)
                self.data.ctrl[actuator_ids["forward_policy"]] = qpos_before[0] + distance
            elif action == "wave_arm":
                import math
                phase = 2 * math.pi * index / max(1, steps)
                self.data.ctrl[actuator_ids["left_shoulder_policy"]] = 0.65 * math.sin(phase)
                self.data.ctrl[actuator_ids["right_shoulder_policy"]] = -0.15
            mujoco.mj_step(self.model, self.data)
        import numpy as np
        delta = float(np.linalg.norm(self.data.qpos - qpos_before))
        if not np.isfinite(self.data.qpos).all() or (delta <= 1e-5 and action != "standing_balance"):
            raise RuntimeError("policy produced no measurable simulator state change")
        root_displacement = float(self.data.qpos[0] - qpos_before[0])
        if action in ("move_forward", "forward") and root_displacement <= 0.01:
            raise RuntimeError("forward policy did not reach its displacement threshold")
        return {"steps": steps, "sim_time": float(self.data.time), "wall_time": time.monotonic() - start,
                "qpos_norm": float(np.linalg.norm(self.data.qpos)), "state_delta": delta,
                "actuator_count": int(self.model.nu), "policy": action,
                "root_displacement": root_displacement}
