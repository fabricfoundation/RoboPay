"""Fetch pinned official Unitree Go2 MJCF and URDF assets.

The large upstream models stay out of this repository.  Exact commits are
recorded in models/model.lock.json, copied to ignored caches, and the official
URDF is converted into a Webots PROTO at setup time.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCK = HERE / "models" / "model.lock.json"


def _checkout(entry: dict, destination: Path) -> Path:
    if destination.is_dir():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="robopay-go2-") as temp:
        repo = Path(temp) / "source"
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", entry["source"], str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "fetch", "--depth", "1", "origin", entry["commit"]], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "--detach", entry["commit"]], check=True)
        subprocess.run(["git", "-C", str(repo), "sparse-checkout", "set", entry["directory"]], check=True)
        source = repo / entry["directory"]
        if not source.is_dir():
            raise RuntimeError(f"Pinned Go2 model path is missing: {source}")
        shutil.copytree(source, destination)
    return destination


def download() -> tuple[Path, Path]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    mjcf = _checkout(lock["mujoco"], HERE / "models" / "unitree_go2_mujoco")
    urdf = _checkout(lock["urdf"], HERE / "models" / "unitree_go2_urdf")
    proto = HERE / "models" / "Go2Official.proto"
    if not proto.is_file():
        source_urdf = urdf / "urdf" / "go2_description.urdf"
        webots_urdf = urdf / "urdf" / "go2_webots.urdf"
        # urdf2webots does not resolve ROS package:// URLs outside a sourced
        # ROS workspace.  Preserve the official URDF and create a local-path
        # derivative solely for conversion.
        webots_urdf.write_text(
            source_urdf.read_text(encoding="utf-8").replace(
                "package://go2_description/dae/", "../dae/"
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable, "-m", "urdf2webots.importer",
                f"--input={webots_urdf}",
                f"--output={proto}",
                # urdf2webots 2.0.0 supports schema targets through R2022b;
                # that PROTO schema remains loadable by the pinned R2025a runtime.
                "--target=R2022b", "--link-to-def", "--joint-to-def",
                "--init-pos=[0,0.79,-1.58,0,0.79,-1.58,0,0.79,-1.58,0,0.79,-1.58]",
            ],
            check=True,
        )
        # urdf2webots emits Windows separators in mesh URLs when invoked on
        # Windows; Webots VRML treats backslashes as escapes. Forward slashes
        # are portable on Windows and Linux.
        proto.write_text(proto.read_text(encoding="utf-8").replace("\\", "/"), encoding="utf-8")
    if not (mjcf / "go2.xml").is_file() or not proto.is_file():
        raise RuntimeError("Official Go2 model setup did not produce the required assets.")
    return mjcf, proto


if __name__ == "__main__":
    mjcf_path, proto_path = download()
    print(f"Official Go2 MJCF: {mjcf_path}")
    print(f"Official Go2 Webots PROTO: {proto_path}")
