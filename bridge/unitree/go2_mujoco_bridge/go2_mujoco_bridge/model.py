"""Paths for the pinned official Unitree Go2 models."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MUJOCO_MODEL_DIR = PACKAGE_ROOT / "models" / "unitree_go2_mujoco"
WEBOTS_PROTO = PACKAGE_ROOT / "models" / "Go2Official.proto"


def resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    candidates = [Path(model_dir)] if model_dir else []
    if os.environ.get("GO2_MJCF_DIR"):
        candidates.append(Path(os.environ["GO2_MJCF_DIR"]))
    candidates.append(MUJOCO_MODEL_DIR)
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if (candidate / "go2.xml").is_file() and (candidate / "assets").is_dir():
            return candidate
    raise FileNotFoundError("Official Go2 MJCF missing; run download_go2_model.py or set GO2_MJCF_DIR.")
