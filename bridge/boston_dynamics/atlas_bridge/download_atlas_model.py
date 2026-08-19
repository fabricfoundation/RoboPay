"""Fetch the pinned Boston Dynamics Atlas v4 description.

The description is never vendored into this repository: it is checked out from
the upstream commit pinned in ``models/model.lock.json`` (MIT licensed, see
``NOTICE.md``) into a local cache directory that is git-ignored.

The same URDF feeds MuJoCo, PyBullet and Webots, so every simulator in this
bridge runs one identical robot.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "models" / "model.lock.json"
CACHE_DIR = HERE / "models" / "atlas_v4"


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, stdout=subprocess.DEVNULL)


def _checkout(entry: dict, destination: Path) -> None:
    """Sparse-checkout ``entry['directory']`` at the pinned commit."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robopay-atlas-") as temp:
        repo = Path(temp) / "source"
        _git("clone", "--quiet", "--filter=blob:none", "--no-checkout", entry["source"], str(repo))
        _git("-C", str(repo), "sparse-checkout", "set", entry["directory"])
        _git("-C", str(repo), "checkout", "--quiet", entry["commit"])
        source = repo / entry["directory"]
        if not source.is_dir():
            raise RuntimeError(f"Pinned Atlas path missing at {entry['commit']}: {entry['directory']}")
        shutil.copytree(source, destination)


def download(force: bool = False) -> Path:
    """Return the local Atlas description directory, fetching it if needed."""
    entry = json.loads(LOCK_PATH.read_text(encoding="utf-8"))["atlas_v4"]
    urdf = CACHE_DIR / entry["urdf"]
    if urdf.is_file() and not force:
        return CACHE_DIR
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
    _checkout(entry, CACHE_DIR)
    if not urdf.is_file():
        raise RuntimeError(f"Atlas download did not produce {entry['urdf']}")
    return CACHE_DIR


def urdf_path() -> Path:
    """Absolute path to the pinned Atlas v4 URDF, downloading on first use."""
    entry = json.loads(LOCK_PATH.read_text(encoding="utf-8"))["atlas_v4"]
    return download() / entry["urdf"]


if __name__ == "__main__":
    print(f"Atlas v4 URDF: {urdf_path()}")
