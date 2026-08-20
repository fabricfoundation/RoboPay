"""Download and verify the official LimX TRON 2 assets used by this profile.

Binary meshes and ONNX weights remain in their official repositories rather
than being redistributed by RoboPay. Every downloaded byte is pinned by
upstream commit and SHA-256 before either simulator may use it.
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PROFILE_ROOT = Path(__file__).resolve().parents[1]
DESCRIPTION_COMMIT = "682d513d03f7e3d2a59ae791d50adc5ccb84dd1a"
CONTROL_COMMIT = "16db4e19eb28664a101fed7135d20d6c7f52bd38"
DESCRIPTION_RAW = (
    "https://raw.githubusercontent.com/limxdynamics/tron2-robot-description/"
    f"{DESCRIPTION_COMMIT}/"
)
CONTROL_RAW = (
    "https://raw.githubusercontent.com/limxdynamics/tron2_rl_deploy_python/"
    f"{CONTROL_COMMIT}/"
)


@dataclass(frozen=True)
class Asset:
    url: str
    destination: str
    sha256: str


def _description(path: str, sha256: str) -> Asset:
    return Asset(
        DESCRIPTION_RAW + f"tron2/WF_TRON2A/{path}",
        f"vendor/limx-tron2/robot-description/WF_TRON2A/{path}",
        sha256,
    )


def _control(path: str, sha256: str) -> Asset:
    return Asset(
        CONTROL_RAW + f"controllers/model/WF_TRON2A/{path}",
        f"vendor/limxdynamics/tron2_rl_deploy_python/controllers/model/WF_TRON2A/{path}",
        sha256,
    )


ASSETS = (
    _description("urdf/robot.urdf", "33e5137702f0c602a703c6a98800f31bc0635ea09c1341970cee64cc08fef997"),
    _description("xml/robot.xml", "aebf198529d8fa8dc98224c9cf58597d0b7f5303b6c5ef95c835cea0cae78d06"),
    _description("meshes/base_Link.STL", "af0a489a76e13223f82bc523f6f16c80922ee9182b2b674e75b41588c40413c8"),
    _description("meshes/knee_L_Link.STL", "688c03378a2a7c80b47eb82c59dc4da64ffa2335fdb981bedd0666cdfbba9ca9"),
    _description("meshes/knee_R_Link.STL", "f762b8f5635f39a2d8aee3916aa1e866eac6ca24e87f98dc7b171767d40d3a3f"),
    _description("meshes/proximal_pitch_L_Link.STL", "e7ec21fce1f8a8a877437df78da1dd44dbee500b6d2bf18ae19e6e0a44fb0e3d"),
    _description("meshes/proximal_pitch_R_Link.STL", "18d038d6ab285f6ed9caa84cc30575b0289e9da5f99aef2f4be44f96a1d6b501"),
    _description("meshes/proximal_roll_L_Link.STL", "b393fb46129cb5faca4da07880a7f146a0386f28ae8569b221980d6f08a5cb7d"),
    _description("meshes/proximal_roll_R_Link.STL", "399f3fcd0e8392e9d1ef8366df32ad1d2dc5a8e3796a0911298335e88ca9b295"),
    _description("meshes/proximal_yaw_L_Link.STL", "ea8fe400b5242ae5cef0c192e10b52a438a7cf3140eb3c1cd633692c0ced73c8"),
    _description("meshes/proximal_yaw_R_Link.STL", "ff37bb272dfdd3ef1bf02788b03139f0522de02b91e6502588d7af51e9c6fc1d"),
    _description("meshes/wheel_L_Link.STL", "d46ba856d43f16313c7ac4bfcf6d9ff3d67585df19fe907099588f81847dc74b"),
    _description("meshes/wheel_R_Link.STL", "4ef27a449de13ce84f85cecbb6b6e701c0b4db3736eae8aa054bb9ae8ead9e7e"),
    _control("policy.onnx", "3000df452681056738a15b46fa67f4f8436b34bbb6dcc6b22fa08b1b1f8dd071"),
    _control("encoder.onnx", "507d0630d78873f7aabfeab4eae9d7669610d709fcc903c4296d1908da54b3e7"),
    _control("params.yaml", "6ef76b28d28055686a3dd0670cd5698cb87a58636e84871c4fd34606239e328d"),
)


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def ensure_asset(asset: Asset, *, verify_only: bool) -> str:
    destination = PROFILE_ROOT / asset.destination
    if destination.is_file() and _digest(destination) == asset.sha256:
        return "verified"
    if verify_only:
        raise RuntimeError(f"missing or invalid pinned asset: {asset.destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".download")
    try:
        with urllib.request.urlopen(asset.url, timeout=120) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        actual = _digest(temporary)
        if actual != asset.sha256:
            raise RuntimeError(
                f"SHA-256 mismatch for {asset.destination}: expected {asset.sha256}, got {actual}"
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return "downloaded"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    downloaded = 0
    for asset in ASSETS:
        status = ensure_asset(asset, verify_only=args.verify_only)
        downloaded += status == "downloaded"
        print(f"{status}: {asset.destination}")
    print(f"Official WF_TRON2A assets ready: {len(ASSETS)} verified, {downloaded} downloaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
