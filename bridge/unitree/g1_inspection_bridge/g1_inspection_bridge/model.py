"""Location and loading helpers for the pinned official Unitree G1 assets."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MODEL_DIR = PACKAGE_ROOT / "models" / "unitree_g1"


def resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    candidates = []
    if model_dir:
        candidates.append(Path(model_dir))
    if os.environ.get("UNITREE_G1_MODEL_DIR"):
        candidates.append(Path(os.environ["UNITREE_G1_MODEL_DIR"]))
    candidates.append(LOCAL_MODEL_DIR)
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (
            (resolved / "mujoco" / "g1_29dof.xml").is_file()
            and (resolved / "mujoco" / "meshes").is_dir()
            and (resolved / "webots" / "g1_29dof.urdf").is_file()
            and (resolved / "webots" / "meshes").is_dir()
        ):
            return resolved
    raise FileNotFoundError(
        "Official Unitree G1 assets were not found. Run "
        "python bridge/unitree/g1_inspection_bridge/download_g1_model.py or set UNITREE_G1_MODEL_DIR."
    )


def model_assets(model_dir: Path) -> dict[str, bytes]:
    """Return the exact pinned MJCF and mesh bytes for in-memory compilation."""
    mujoco_root = model_dir / "mujoco"
    assets = {"g1_29dof.xml": (mujoco_root / "g1_29dof.xml").read_bytes()}
    for path in sorted((mujoco_root / "meshes").iterdir()):
        if path.is_file():
            assets[f"meshes/{path.name}"] = path.read_bytes()
    return assets
