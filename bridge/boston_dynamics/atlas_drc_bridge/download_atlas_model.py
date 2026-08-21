"""Fetch the pinned, legacy Atlas v4 model used by the MuJoCo bridge.

The current commercial Atlas does not have a public MuJoCo model.  This
profile deliberately uses the older DRC/v4 Atlas model and says so everywhere
it presents evidence.  The upstream model stays out of Git; this helper pins
its revision and verifies the downloaded URDF before it is used.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "models" / "model.lock.json"
DEFAULT_DESTINATION = HERE / "models" / "atlas_v4"


def _sha256(path: Path) -> str:
    # Git for Windows may materialize these XML assets with CRLF despite a
    # repository-level checkout setting. The locked blobs are LF canonical;
    # normalize only line endings so the same signed source revision validates
    # on Windows and Linux without weakening content verification.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def download(destination: Path = DEFAULT_DESTINATION) -> Path:
    """Download the exact model revision and return its local directory."""

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    required = destination / lock["urdf"]
    extras = lock.get("extra_directories", [])
    extras_valid = all(
        (destination / extra["destination"] / extra["verify_path"]).is_file()
        and _sha256(destination / extra["destination"] / extra["verify_path"]) == extra["sha256"]
        for extra in extras
    )
    if required.is_file() and _sha256(required) == lock["urdf_sha256"] and extras_valid:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robopay-atlas-roboschool-") as temp_dir:
        checkout = Path(temp_dir) / "roboschool"
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
                lock["source"],
                str(checkout),
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "fetch", "--depth", "1", "origin", lock["commit"]],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "checkout", "--detach", lock["commit"]],
            check=True,
        )
        sparse_paths = [lock["directory"], *(extra["source"] for extra in extras)]
        subprocess.run(["git", "-C", str(checkout), "sparse-checkout", "set", *sparse_paths], check=True)
        source = checkout / lock["directory"]
        source_urdf = source / lock["urdf"]
        if not source_urdf.is_file():
            raise RuntimeError(f"Pinned Atlas URDF is missing: {source_urdf}")
        if _sha256(source_urdf) != lock["urdf_sha256"]:
            raise RuntimeError("Pinned Atlas URDF checksum did not match model.lock.json")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        for extra in extras:
            source_extra = checkout / extra["source"]
            verified = source_extra / extra["verify_path"]
            if not verified.is_file() or _sha256(verified) != extra["sha256"]:
                raise RuntimeError(f"Pinned extra Atlas asset failed verification: {verified}")
            shutil.copytree(source_extra, destination / extra["destination"])
        upstream_license = checkout / "LICENSE.md"
        if upstream_license.is_file():
            shutil.copy2(upstream_license, destination / "LICENSE.openai-roboschool.md")

    return destination


if __name__ == "__main__":
    model_dir = download()
    print(f"Atlas DRC v4 model downloaded to: {model_dir}")
