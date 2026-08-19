"""Build full-detail viewer and lightweight CI PROTOs from the official X2 URDF."""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import trimesh
from urdf2webots.importer import convertUrdfFile

from download_x2_model import LOCK_PATH, download


HERE = Path(__file__).resolve().parent
FULL_OUTPUT = HERE / "scenes" / "X2Ultra.proto"
CI_OUTPUT = HERE / "scenes" / "X2UltraCI.proto"
OUTPUT = FULL_OUTPUT
WEBOTS_MODEL = HERE / "models" / "agibot_x2_webots"
MAX_FACES_PER_LINK = 6_000


def _referenced_meshes(urdf: Path) -> set[Path]:
    root = ET.parse(urdf).getroot()
    return {
        Path(mesh.attrib["filename"].removeprefix("./"))
        for mesh in root.findall(".//mesh")
        if mesh.attrib.get("filename")
    }


def _build_optimized_model(source: Path) -> Path:
    """Create ignored CI-only visual derivatives; never used by the viewer."""
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    marker_value = f'{lock["commit"]}:faces={MAX_FACES_PER_LINK}'
    marker = WEBOTS_MODEL / ".robopay-derived-from"
    urdf_name = lock["webotsUrdf"]
    optimized_urdf = WEBOTS_MODEL / urdf_name
    if marker.is_file() and optimized_urdf.is_file() and marker.read_text(encoding="utf-8") == marker_value:
        return optimized_urdf

    if WEBOTS_MODEL.exists():
        shutil.rmtree(WEBOTS_MODEL)
    (WEBOTS_MODEL / "meshes").mkdir(parents=True)
    source_urdf = source / urdf_name
    for relative in sorted(_referenced_meshes(source_urdf)):
        source_mesh = source / relative
        target_mesh = WEBOTS_MODEL / relative
        target_mesh.parent.mkdir(parents=True, exist_ok=True)
        mesh = trimesh.load_mesh(source_mesh, process=False)
        if not isinstance(mesh, trimesh.Trimesh):
            raise RuntimeError(f"Expected one STL mesh in {source_mesh}")
        if len(mesh.faces) > MAX_FACES_PER_LINK:
            mesh = mesh.simplify_quadric_decimation(face_count=MAX_FACES_PER_LINK)
        mesh.export(target_mesh, file_type="stl")
    shutil.copy2(source_urdf, optimized_urdf)
    shutil.copy2(source / "LICENSE", WEBOTS_MODEL / "LICENSE")
    marker.write_text(marker_value, encoding="utf-8")
    return optimized_urdf


def _fix_root_physics(output: Path) -> None:
    content = output.read_text(encoding="utf-8")
    start = content.rfind("\n    physics Physics {")
    if start < 0:
        raise RuntimeError(f"Generated {output.name} did not contain root physics")
    brace = content.find("{", start)
    depth = 0
    end = None
    for index in range(brace, len(content)):
        if content[index] == "{":
            depth += 1
        elif content[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise RuntimeError(f"Generated {output.name} root Physics node was malformed")
    content = content[:start] + "\n    physics NULL" + content[end:]
    content = content.replace("# license: Apache License 2.0", "# license: MulanPSL-2.0")
    content = content.replace(
        "# license url: http://www.apache.org/licenses/LICENSE-2.0",
        "# license url: https://github.com/AgibotTech/agibot_x2_urdf/blob/77f43eb0904dae4c48ccd9154fee824f8ffd4d38/LICENSE",
    )
    output.write_text(content.replace("\\", "/"), encoding="utf-8")


def _convert(input_urdf: Path, output: Path, mesh_prefix: str) -> None:
    output.unlink(missing_ok=True)
    convertUrdfFile(
        input=str(input_urdf),
        output=str(output),
        boxCollision=True,
        relativePathPrefix=mesh_prefix,
        targetVersion="R2025a",
    )
    if not output.is_file():
        raise RuntimeError(f"urdf2webots did not create {output.name}")
    _fix_root_physics(output)


def build() -> Path:
    source = download()
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    official_urdf = source / lock["webotsUrdf"]
    optimized_urdf = _build_optimized_model(source)
    FULL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # Human evidence uses every original upstream vertex.
    _convert(official_urdf, FULL_OUTPUT, "../models/agibot_x2/")
    # Automated CI uses the same URDF state model with visual-only derivatives.
    _convert(optimized_urdf, CI_OUTPUT, "../models/agibot_x2_webots/")
    return FULL_OUTPUT


if __name__ == "__main__":
    print(f"Webots viewer PROTO generated at: {build()}")
    print(f"Webots CI PROTO generated at: {CI_OUTPUT}")
