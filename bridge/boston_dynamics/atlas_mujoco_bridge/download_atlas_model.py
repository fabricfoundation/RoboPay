"""Fetch the pinned MuJoCo humanoid model (Atlas-compatible locomotion)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCK = HERE / "models" / "model.lock.json"


def _checkout(entry: dict, destination: Path) -> Path:
    if destination.is_dir():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robopay-atlas-") as temp:
        repo = Path(temp) / "source"
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", entry["source"], str(repo)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "fetch", "--depth", "1", "origin", entry["commit"]],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "--detach", entry["commit"]],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "sparse-checkout", "set", entry["directory"]],
            check=True,
        )
        source = repo / entry["directory"]
        if not source.is_dir():
            raise RuntimeError(f"Pinned humanoid model path is missing: {source}")
        shutil.copytree(source, destination)
    return destination


def download() -> Path:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    model_dir = HERE / "models" / "mujoco_humanoid"
    _checkout(lock["mujoco_humanoid"], model_dir)
    if not (model_dir / "humanoid.xml").is_file():
        raise RuntimeError("Humanoid model download did not produce humanoid.xml")
    return model_dir


if __name__ == "__main__":
    path = download()
    print(f"Humanoid MJCF: {path}")
