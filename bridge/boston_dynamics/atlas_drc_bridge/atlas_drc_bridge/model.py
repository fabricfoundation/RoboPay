"""Resolve and load the pinned Atlas DRC v4 URDF for MuJoCo."""

from __future__ import annotations

import os
import xml.etree.ElementTree as element_tree
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MODEL_DIR = PACKAGE_ROOT / "models" / "atlas_v4"
URDF_RELATIVE_PATH = Path("urdf") / "atlas_v4_with_multisense.urdf"
PHYSICS_URDF_RELATIVE_PATH = Path("atlas_v4_with_multisense_physics.urdf")
VISUAL_URDF_RELATIVE_PATH = Path("atlas_v4_with_multisense_visual.urdf")


def resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    """Return a complete downloaded model directory, never a partial asset set."""

    candidates: list[Path] = []
    if model_dir:
        candidates.append(Path(model_dir))
    if os.environ.get("ATLAS_DRC_MODEL_DIR"):
        candidates.append(Path(os.environ["ATLAS_DRC_MODEL_DIR"]))
    candidates.append(LOCAL_MODEL_DIR)
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if (candidate / URDF_RELATIVE_PATH).is_file():
            return candidate
    raise FileNotFoundError(
        "Atlas DRC v4 URDF was not found. Run "
        "python bridge/boston_dynamics/atlas_drc_bridge/download_atlas_model.py "
        "or set ATLAS_DRC_MODEL_DIR to its atlas_v4 directory."
    )


def _source_mesh(model_dir: Path, package_reference: str) -> Path:
    """Resolve an original package:// visual mesh without changing the URDF."""

    if not package_reference.startswith("package://"):
        raise ValueError(f"Expected package:// mesh reference, got {package_reference!r}")
    relative = Path(package_reference.removeprefix("package://"))
    if relative.parts[0] == "atlas_description":
        return model_dir.joinpath(*relative.parts[1:])
    if relative.parts[0] == "multisense_sl_description":
        return model_dir / relative
    raise ValueError(f"Unsupported pinned Atlas mesh package: {package_reference}")


def _convert_dae_to_obj(source: Path, destination: Path) -> None:
    """Convert an upstream visual DAE into an unmodified triangle OBJ mesh."""

    import trimesh

    loaded = trimesh.load(source, force="scene")
    if isinstance(loaded, trimesh.Scene):
        mesh = loaded.dump(concatenate=True)
    else:
        mesh = loaded
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise RuntimeError(f"Could not read visual mesh from {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(destination, file_type="obj")


def _name_unnamed_link_geometry(root: element_tree.Element) -> None:
    """Give local import-only names to otherwise anonymous URDF geometry.

    The pinned source URDF intentionally leaves its ``visual`` and
    ``collision`` elements unnamed.  MuJoCo accepts that valid URDF but emits
    a warning for every repeated empty name.  Names carry no physics meaning,
    so adding deterministic local names keeps the import auditable without
    changing source geometry, joints, materials, or limits.
    """

    for link in root.findall("link"):
        link_name = link.get("name", "unnamed_link")
        for kind in ("visual", "collision"):
            for index, element in enumerate(link.findall(kind)):
                element.set("name", f"{link_name}_{kind}_{index}")


def prepare_physics_urdf(model_dir: str | Path | None = None) -> Path:
    """Write a name-sanitized local import of the immutable source URDF."""

    directory = resolve_model_dir(model_dir)
    root = element_tree.parse(directory / URDF_RELATIVE_PATH).getroot()
    _name_unnamed_link_geometry(root)
    output_urdf = directory / PHYSICS_URDF_RELATIVE_PATH
    element_tree.ElementTree(root).write(output_urdf, encoding="utf-8", xml_declaration=True)
    return output_urdf


def prepare_visual_urdf(model_dir: str | Path | None = None) -> Path:
    """Generate a local visual-only OBJ view of the pinned original URDF.

    MuJoCo accepts OBJ/STL meshes but not the source Atlas DAE visuals.  This
    leaves the checked-source URDF untouched, converts its original visual
    triangles locally, and writes a sibling URDF used only when an operator
    opts into the desktop viewer.
    """

    directory = resolve_model_dir(model_dir)
    source_urdf = directory / URDF_RELATIVE_PATH
    output_urdf = directory / VISUAL_URDF_RELATIVE_PATH
    root = element_tree.parse(source_urdf).getroot()
    mujoco_extension = root.find("mujoco")
    if mujoco_extension is None:
        mujoco_extension = element_tree.Element("mujoco")
        root.insert(0, mujoco_extension)
    compiler = mujoco_extension.find("compiler")
    if compiler is None:
        compiler = element_tree.SubElement(mujoco_extension, "compiler")
    # MuJoCo's URDF compiler otherwise drops visual-only meshes when a link
    # already has collision geometry.
    compiler.set("discardvisual", "false")
    _name_unnamed_link_geometry(root)
    for mesh_element in root.iter("mesh"):
        reference = mesh_element.get("filename")
        if not reference or not reference.endswith(".dae"):
            continue
        source = _source_mesh(directory, reference)
        if not source.is_file():
            raise FileNotFoundError(f"Pinned visual mesh is missing: {source}")
        package_relative = Path(reference.removeprefix("package://")).with_suffix(".obj")
        # MuJoCo's URDF importer resolves a mesh filename relative to the
        # URDF and discards parent/subdirectory components. Keep generated
        # display assets beside that URDF, with a collision-free flat name.
        converted = output_urdf.parent / (
            "atlas_visual_" + "_".join(package_relative.parts)
        )
        if not converted.is_file() or converted.stat().st_mtime < source.stat().st_mtime:
            _convert_dae_to_obj(source, converted)
        mesh_element.set("filename", converted.name)
    output_urdf.parent.mkdir(parents=True, exist_ok=True)
    element_tree.ElementTree(root).write(output_urdf, encoding="utf-8", xml_declaration=True)
    return output_urdf


def load_mujoco_model(model_dir: str | Path | None = None, visual: bool = False):
    """Load the pinned upstream URDF with MuJoCo's real physics parser.

    ``visual=True`` adds converted upstream display meshes, then marks those
    meshes non-colliding so the viewer cannot alter the validated collision
    physics used by the payment-gated controller.
    """

    import mujoco

    directory = resolve_model_dir(model_dir)
    path = prepare_visual_urdf(directory) if visual else prepare_physics_urdf(directory)
    model = mujoco.MjModel.from_xml_path(str(path))
    if visual:
        mesh_geometries = model.geom_type == int(mujoco.mjtGeom.mjGEOM_MESH)
        model.geom_contype[mesh_geometries] = 0
        model.geom_conaffinity[mesh_geometries] = 0
    return model
