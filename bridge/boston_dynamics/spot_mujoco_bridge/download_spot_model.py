"""Fetch the pinned Boston Dynamics Spot MJCF model without nesting a Git repo.

The model is intentionally ignored by Git: it is a 57 MB third-party asset set.
This script pins the exact MuJoCo Menagerie revision recorded in
``models/model.lock.json`` and copies only ``boston_dynamics_spot`` into this
bridge's local model directory.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "models" / "model.lock.json"
DEFAULT_DESTINATION = HERE / "models" / "boston_dynamics_spot"


def download(destination: Path = DEFAULT_DESTINATION) -> Path:
    """Download the pinned model and return its directory.

    Existing complete downloads are reused.  The temporary Git checkout is
    outside the project so it can never become an accidental nested repository.
    """

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    required = destination / "spot.xml"
    if required.is_file() and (destination / "assets").is_dir():
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robopay-spot-menagerie-") as tmp:
        checkout = Path(tmp) / "menagerie"
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--sparse",
                lock["source"],
                str(checkout),
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "fetch", "--depth", "1", "origin", lock["commit"]],
            check=True,
        )
        subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", lock["commit"]], check=True)
        subprocess.run(
            ["git", "-C", str(checkout), "sparse-checkout", "set", lock["directory"]],
            check=True,
        )
        source = checkout / lock["directory"]
        if not (source / "spot.xml").is_file():
            raise RuntimeError(f"Pinned Spot model is incomplete: {source}")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)

    return destination


if __name__ == "__main__":
    model_dir = download()
    print(f"Spot MJCF downloaded to: {model_dir}")
