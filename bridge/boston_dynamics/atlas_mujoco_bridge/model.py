"""Paths for the pinned MuJoCo humanoid model (Atlas locomotion)."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
MUJOCO_MODEL_DIR = PACKAGE_ROOT / "models" / "mujoco_humanoid"


def resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    candidates = [Path(model_dir)] if model_dir else []
    if os.environ.get("ATLAS_MJCF_DIR"):
        candidates.append(Path(os.environ["ATLAS_MJCF_DIR"]))
    candidates.append(MUJOCO_MODEL_DIR)
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if (candidate / "humanoid.xml").is_file():
            return candidate
    raise FileNotFoundError(
        "MuJoCo humanoid model missing; run download_atlas_model.py or set ATLAS_MJCF_DIR."
    )
