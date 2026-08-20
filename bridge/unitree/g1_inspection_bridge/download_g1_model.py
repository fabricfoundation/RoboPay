"""Fetch the exact official Unitree G1 model revision used by this profile."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "models" / "model.lock.json"
DEFAULT_DESTINATION = HERE / "models" / "unitree_g1"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _official_text_sha256(path: Path) -> str:
    """Hash the upstream text blob independent of Git checkout line endings."""
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def _verify(directory: Path, lock: dict) -> None:
    for path, hash_key in (
        (directory / "mujoco" / lock["mjcf"], "mjcfSha256"),
        (directory / "webots" / lock["webotsUrdf"], "webotsUrdfSha256"),
    ):
        actual = _official_text_sha256(path)
        if actual != lock[hash_key]:
            raise RuntimeError(f"Pinned Unitree G1 hash mismatch for {path.name}: {actual}")
    if not (directory / "mujoco" / "meshes").is_dir() or not (directory / "webots" / "meshes").is_dir():
        raise RuntimeError("Pinned Unitree G1 checkout is missing official mesh assets")
    if not (directory / "LICENSE-unitree_mujoco").is_file() or not (directory / "LICENSE-unitree_ros").is_file():
        raise RuntimeError("Pinned Unitree G1 checkouts are missing their BSD-3-Clause licenses")


def _checkout(source: str, commit: str, directory: str, target: Path) -> Path:
    subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", source, str(target)],
        check=True,
    )
    subprocess.run(["git", "-C", str(target), "fetch", "--depth", "1", "origin", commit], check=True)
    subprocess.run(["git", "-C", str(target), "checkout", "--detach", commit], check=True)
    subprocess.run(
        ["git", "-C", str(target), "sparse-checkout", "set", "--skip-checks", directory, "LICENSE"],
        check=True,
    )
    return target / directory


def download(destination: Path = DEFAULT_DESTINATION) -> Path:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    required = destination / "mujoco" / lock["mjcf"]
    if required.is_file() and (destination / "webots" / lock["webotsUrdf"]).is_file():
        _verify(destination, lock)
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robopay-unitree-g1-") as temporary:
        temporary_root = Path(temporary)
        mujoco_checkout = temporary_root / "unitree_mujoco"
        webots_checkout = temporary_root / "unitree_ros"
        mujoco_source = _checkout(
            lock["mujocoSource"], lock["mujocoCommit"], lock["mujocoDirectory"], mujoco_checkout
        )
        webots_source = _checkout(
            lock["webotsSource"], lock["webotsCommit"], lock["webotsDirectory"], webots_checkout
        )
        if not (mujoco_source / lock["mjcf"]).is_file() or not (webots_source / lock["webotsUrdf"]).is_file():
            raise RuntimeError("Pinned official Unitree G1 model checkout is incomplete")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(mujoco_source, destination / "mujoco")
        shutil.copytree(webots_source, destination / "webots")
        shutil.copy2(mujoco_checkout / "LICENSE", destination / "LICENSE-unitree_mujoco")
        shutil.copy2(webots_checkout / "LICENSE", destination / "LICENSE-unitree_ros")
    _verify(destination, lock)
    return destination


if __name__ == "__main__":
    print(f"Unitree G1 assets downloaded to: {download()}")
