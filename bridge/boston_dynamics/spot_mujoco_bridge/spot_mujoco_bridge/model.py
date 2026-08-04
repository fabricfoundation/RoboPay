"""Location and loading helpers for the pinned Spot model."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MODEL_DIR = PACKAGE_ROOT / "models" / "boston_dynamics_spot"


def resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    """Resolve a complete Spot MJCF directory or explain how to obtain one."""

    candidates: list[Path] = []
    if model_dir:
        candidates.append(Path(model_dir))
    if os.environ.get("SPOT_MJCF_DIR"):
        candidates.append(Path(os.environ["SPOT_MJCF_DIR"]))
    candidates.append(LOCAL_MODEL_DIR)

    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if (candidate / "spot.xml").is_file() and (candidate / "assets").is_dir():
            return candidate

    raise FileNotFoundError(
        "Boston Dynamics Spot MJCF was not found. Run "
        "python bridge/boston_dynamics/spot_mujoco_bridge/download_spot_model.py "
        "or set SPOT_MJCF_DIR to a directory containing spot.xml and assets/."
    )


def model_assets(model_dir: Path) -> dict[str, bytes]:
    """Return assets for an in-memory MuJoCo scene without mutating the model."""

    assets = {"spot.xml": (model_dir / "spot.xml").read_bytes()}
    for path in (model_dir / "assets").iterdir():
        if path.is_file():
            assets[f"assets/{path.name}"] = path.read_bytes()
    return assets
