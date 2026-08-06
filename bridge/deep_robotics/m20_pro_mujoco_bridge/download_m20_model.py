"""Fetch the pinned, vendor-published DeepRobotics M20 model."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "models" / "model.lock.json"
DEFAULT_DESTINATION = HERE / "models" / "deep_robotics_m20"


def _sha256(path: Path, *, normalize_text_newlines: bool = False) -> str:
    """Hash source text canonically without ever rewriting binary assets."""

    content = path.read_bytes()
    if normalize_text_newlines:
        content = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def _is_complete(destination: Path, lock: dict[str, object]) -> bool:
    verified_asset = lock["verified_asset"]
    assert isinstance(verified_asset, dict)
    checks = (
        (str(lock["mjcf"]), str(lock["mjcf_sha256"]), True),
        (str(lock["urdf"]), str(lock["urdf_sha256"]), True),
        (str(verified_asset["path"]), str(verified_asset["sha256"]), False),
    )
    return all(
        (destination / relative).is_file()
        and _sha256(destination / relative, normalize_text_newlines=normalize_text_newlines) == digest
        for relative, digest, normalize_text_newlines in checks
    )


def download(destination: Path = DEFAULT_DESTINATION) -> Path:
    """Download exactly the locked M20 source tree and return its directory."""

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if destination.is_dir() and _is_complete(destination, lock):
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robopay-m20-model-") as temp_dir:
        checkout = Path(temp_dir) / "deep_robotics_model"
        subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--sparse",
                str(lock["source"]),
                str(checkout),
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "fetch", "--depth", "1", "origin", str(lock["commit"])],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "checkout", "--detach", str(lock["commit"])],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "sparse-checkout", "set", str(lock["directory"])],
            check=True,
        )
        source = checkout / str(lock["directory"])
        if not _is_complete(source, lock):
            raise RuntimeError("Pinned M20 model did not match model.lock.json")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        license_path = checkout / "LICENSE.txt"
        if license_path.is_file():
            shutil.copy2(license_path, destination / "LICENSE.deep-robotics.txt")
    return destination


if __name__ == "__main__":
    print(f"DeepRobotics M20 model downloaded to: {download()}")
