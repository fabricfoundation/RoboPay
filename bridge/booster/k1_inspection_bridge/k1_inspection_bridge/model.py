"""Location and loading helpers for the pinned official Booster K1 assets."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MODEL_DIR = PACKAGE_ROOT / "models" / "booster_k1"


def resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    candidates = []
    if model_dir:
        candidates.append(Path(model_dir))
    if os.environ.get("BOOSTER_K1_MODEL_DIR"):
        candidates.append(Path(os.environ["BOOSTER_K1_MODEL_DIR"]))
    candidates.append(LOCAL_MODEL_DIR)
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "K1_22dof.xml").is_file() and (resolved / "K1_22dof.urdf").is_file() and (resolved / "meshes").is_dir():
            return resolved
    raise FileNotFoundError(
        "Official Booster K1 assets were not found. Run "
        "python bridge/booster/k1_inspection_bridge/download_k1_model.py or set BOOSTER_K1_MODEL_DIR."
    )


def model_assets(model_dir: Path, trunk_height: float | None = None) -> dict[str, bytes]:
    mjcf = (model_dir / "K1_22dof.xml").read_bytes()
    if trunk_height is not None:
        source = b'<body name="Trunk" pos="0 0 1.0">'
        replacement = f'<body name="Trunk" pos="0 0 {trunk_height}">'.encode()
        if mjcf.count(source) != 1:
            raise RuntimeError("Pinned K1 MJCF no longer has the validated trunk spawn declaration")
        mjcf = mjcf.replace(source, replacement)
    assets = {"K1_22dof.xml": mjcf}
    for path in (model_dir / "meshes").iterdir():
        if path.is_file():
            assets[f"meshes/{path.name}"] = path.read_bytes()
    return assets
