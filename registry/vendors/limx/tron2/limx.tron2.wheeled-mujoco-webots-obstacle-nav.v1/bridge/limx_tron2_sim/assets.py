"""Prepare portable Webots input from the unmodified pinned LimX URDF."""

from __future__ import annotations

from pathlib import Path

from .model import MESHES, PROFILE_ROOT, URDF


GENERATED_ROOT = PROFILE_ROOT / "artifacts" / "generated" / "webots_input"


def prepare_webots_urdf() -> Path:
    output = GENERATED_ROOT / "robot.urdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    text = URDF.read_text(encoding="utf-8")
    package_prefix = "package://bipedal_robot/meshes/"
    if package_prefix not in text:
        raise RuntimeError("pinned LimX URDF mesh prefix changed")
    text = text.replace(package_prefix, MESHES.resolve().as_posix() + "/")
    output.write_text(text, encoding="utf-8")
    return output
