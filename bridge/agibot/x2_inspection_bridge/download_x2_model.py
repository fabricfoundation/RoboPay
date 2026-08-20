"""Fetch the exact official AGIBot X2 model revision used by this profile."""

from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "models" / "model.lock.json"
DEFAULT_DESTINATION = HERE / "models" / "agibot_x2"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _official_text_sha256(path: Path) -> str:
    """Hash the upstream text blob independent of Git checkout line endings."""
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def _verify(directory: Path, lock: dict) -> None:
    for filename_key, hash_key in (
        ("mjcf", "mjcfSha256"),
        ("urdf", "urdfSha256"),
        ("webotsUrdf", "webotsUrdfSha256"),
    ):
        path = directory / lock[filename_key]
        # Git may materialize the official XML/URDF blobs with CRLF on Windows.
        # Canonicalizing that checkout-only difference keeps the lock bound to
        # the exact LF blobs at the pinned upstream commit on every CI host.
        actual = _official_text_sha256(path)
        if actual != lock[hash_key]:
            raise RuntimeError(f"Pinned AGIBot X2 hash mismatch for {path.name}: {actual}")
    if not (directory / "LICENSE").is_file():
        raise RuntimeError("Pinned AGIBot X2 checkout is missing its MulanPSL-2.0 license")


def download(destination: Path = DEFAULT_DESTINATION) -> Path:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    required = destination / lock["mjcf"]
    if required.is_file() and (destination / "meshes").is_dir():
        _verify(destination, lock)
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robopay-agibot-x2-") as temporary:
        checkout = Path(temporary) / "agibot_assets"
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", lock["source"], str(checkout)],
            check=True,
        )
        subprocess.run(["git", "-C", str(checkout), "fetch", "--depth", "1", "origin", lock["commit"]], check=True)
        subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", lock["commit"]], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "sparse-checkout",
                "set",
                "--skip-checks",
                lock["directory"],
                "LICENSE",
            ],
            check=True,
        )
        source = checkout / lock["directory"]
        if not (source / lock["mjcf"]).is_file() or not (source / lock["urdf"]).is_file():
            raise RuntimeError(f"Pinned AGIBot X2 model is incomplete: {source}")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        shutil.copy2(checkout / "LICENSE", destination / "LICENSE")
    _verify(destination, lock)
    return destination


if __name__ == "__main__":
    print(f"AGIBot X2 assets downloaded to: {download()}")
