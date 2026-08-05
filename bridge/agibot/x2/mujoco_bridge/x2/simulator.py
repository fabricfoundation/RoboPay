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
        ctrl_before = self.data.ctrl.copy()
        for index in range(steps):
            self.data.ctrl[:] = 0
            if self.model.nu:
                if action in ("move_forward", "forward"):
                    self.data.ctrl[0] = 0.1
                elif action == "wave_arm":
                    import math
                    self.data.ctrl[0] = 0.2 * math.sin(2 * math.pi * index / max(1, steps))
            mujoco.mj_step(self.model, self.data)
        import numpy as np
        delta = float(np.linalg.norm(self.data.qpos - qpos_before))
        if not np.isfinite(self.data.qpos).all() or (self.model.nu and delta <= 1e-12 and action != "standing_balance"):
            raise RuntimeError("policy produced no measurable simulator state change")
        return {"steps": steps, "sim_time": float(self.data.time), "wall_time": time.monotonic() - start,
                "qpos_norm": float(np.linalg.norm(self.data.qpos)), "state_delta": delta,
                "actuator_count": int(self.model.nu), "policy": action}
