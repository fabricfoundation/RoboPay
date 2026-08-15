"""Convert the Unitree G1 description from STL meshes to OBJ for Drake.

Drake computes convex hulls for collision geometry and only accepts .obj,
.vtk, or .gltf; the official Unitree description ships .STL exclusively. The
MuJoCo side reads the menagerie MJCF and is unaffected, so this conversion
exists purely to let both engines load the *same* robot description.

The output is written to a separate tree so the upstream checkout stays
pristine and the conversion stays reproducible:

    python tools/convert_meshes.py \
        --src  ~/g1_urdf/robots/g1_description \
        --dest ~/robopay-g1/assets/g1_description_obj
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import trimesh

MESH_REF = re.compile(r'filename="([^"]+\.stl)"', re.IGNORECASE)


def convert_meshes(src_meshes: Path, dest_meshes: Path) -> tuple[int, int]:
    """Convert every STL under src_meshes to OBJ under dest_meshes."""
    dest_meshes.mkdir(parents=True, exist_ok=True)
    converted = 0
    skipped = 0
    for stl in sorted(src_meshes.rglob("*")):
        if stl.suffix.lower() != ".stl":
            continue
        out = dest_meshes / stl.relative_to(src_meshes).with_suffix(".obj")
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            skipped += 1
            continue
        mesh = trimesh.load_mesh(stl, process=False)
        # A handful of the hand meshes load as scenes; merge them so each
        # output file maps 1:1 onto its URDF reference.
        if isinstance(mesh, trimesh.Scene):
            mesh = trimesh.util.concatenate(list(mesh.geometry.values()))
        mesh.export(out)
        converted += 1
    return converted, skipped


def rewrite_urdf(src_urdf: Path, dest_urdf: Path) -> int:
    """Copy a URDF, repointing every .stl reference at its .obj twin."""
    text = src_urdf.read_text()
    refs = MESH_REF.findall(text)
    text = MESH_REF.sub(lambda m: f'filename="{m.group(1)[:-4]}.obj"', text)
    dest_urdf.write_text(text)
    return len(refs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path,
                    help="upstream g1_description directory")
    ap.add_argument("--dest", required=True, type=Path,
                    help="output directory for the OBJ-based description")
    ap.add_argument("--urdf", default="g1_29dof_with_hand.urdf",
                    help="URDF variant to convert (default: %(default)s)")
    args = ap.parse_args(argv)

    src = args.src.expanduser()
    dest = args.dest.expanduser()
    src_urdf = src / args.urdf
    if not src_urdf.is_file():
        print(f"error: no such URDF: {src_urdf}", file=sys.stderr)
        return 1

    dest.mkdir(parents=True, exist_ok=True)
    converted, skipped = convert_meshes(src / "meshes", dest / "meshes")
    refs = rewrite_urdf(src_urdf, dest / args.urdf)

    print(f"meshes converted : {converted}")
    print(f"meshes reused    : {skipped}")
    print(f"urdf refs rewritten: {refs}")
    print(f"output           : {dest / args.urdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
