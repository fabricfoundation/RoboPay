"""Fetch the exact official Booster K1 model revision used by this profile."""

from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "models" / "model.lock.json"
DEFAULT_DESTINATION = HERE / "models" / "booster_k1"


def _verify(directory: Path, lock: dict) -> None:
    for filename_key, hash_key in (("mjcf", "mjcfSha256"), ("urdf", "urdfSha256")):
        path = directory / lock[filename_key]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != lock[hash_key]:
            raise RuntimeError(f"Pinned Booster K1 hash mismatch for {path.name}: {actual}")


def download(destination: Path = DEFAULT_DESTINATION) -> Path:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    required = destination / lock["mjcf"]
    if required.is_file() and (destination / "meshes").is_dir():
        _verify(destination, lock)
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robopay-booster-k1-") as temporary:
        checkout = Path(temporary) / "booster_assets"
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", lock["source"], str(checkout)],
            check=True,
        )
        subprocess.run(["git", "-C", str(checkout), "fetch", "--depth", "1", "origin", lock["commit"]], check=True)
        subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", lock["commit"]], check=True)
        subprocess.run(["git", "-C", str(checkout), "sparse-checkout", "set", lock["directory"]], check=True)
        source = checkout / lock["directory"]
        if not (source / lock["mjcf"]).is_file() or not (source / lock["urdf"]).is_file():
            raise RuntimeError(f"Pinned Booster K1 model is incomplete: {source}")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    _verify(destination, lock)
    return destination


if __name__ == "__main__":
    print(f"Booster K1 assets downloaded to: {download()}")
