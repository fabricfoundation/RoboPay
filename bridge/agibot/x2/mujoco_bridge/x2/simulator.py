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
    def execute(self, action: str, duration: float = 1.0) -> dict:
        start, steps = time.monotonic(), max(1, int(duration / self.model.opt.timestep))
        for _ in range(steps):
            if action == "standing_balance": self.data.ctrl[:] = 0
            elif action == "wave_arm" and self.model.nu: self.data.ctrl[0] = 0.2
            elif action in ("move_forward", "forward") and self.model.nu: self.data.ctrl[0] = 0.1
            mujoco.mj_step(self.model, self.data)
        return {"steps": steps, "sim_time": float(self.data.time), "wall_time": time.monotonic() - start, "qpos_norm": float((self.data.qpos ** 2).sum() ** 0.5)}
