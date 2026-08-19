"""Location and loading helpers for the pinned official AGIBot X2 assets."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MODEL_DIR = PACKAGE_ROOT / "models" / "agibot_x2"


def resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    candidates = []
    if model_dir:
        candidates.append(Path(model_dir))
    if os.environ.get("AGIBOT_X2_MODEL_DIR"):
        candidates.append(Path(os.environ["AGIBOT_X2_MODEL_DIR"]))
    candidates.append(LOCAL_MODEL_DIR)
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "X2-Ultra.xml").is_file() and (resolved / "X2-Ultra.urdf").is_file() and (resolved / "meshes").is_dir():
            return resolved
    raise FileNotFoundError(
        "Official AGIBot X2 assets were not found. Run "
        "python bridge/agibot/x2_inspection_bridge/download_x2_model.py or set AGIBOT_X2_MODEL_DIR."
    )


def model_assets(model_dir: Path) -> dict[str, bytes]:
    """Return the exact pinned MJCF and mesh bytes for in-memory compilation."""
    assets = {"X2-Ultra.xml": (model_dir / "X2-Ultra.xml").read_bytes()}
    for path in sorted((model_dir / "meshes").iterdir()):
        if path.is_file():
            assets[f"meshes/{path.name}"] = path.read_bytes()
    return assets
