"""Webots backend: build the world from the pinned URDF and run it headless.

Webots needs a PROTO and a ``.wbt`` world rather than a URDF, so both are
*generated* from the same pinned Atlas description and the same
:mod:`task` geometry that MuJoCo and PyBullet use.  Neither generated file is
committed — regenerating them is part of setup, exactly like fetching the model.

If Webots is not installed, :func:`webots_available` returns ``False`` and
:mod:`sim2sim` reports the engine as unavailable.  It never silently substitutes
a different robot or a different task.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .model import physics_urdf
from .task import EPISODE_BUDGET_S, SHELF_PARTS

HERE = Path(__file__).resolve().parent
WEBOTS_DIR = HERE / "webots"
PROTO_DIR = WEBOTS_DIR / "protos"
WORLD_DIR = WEBOTS_DIR / "worlds"
CONTROLLER_DIR = WEBOTS_DIR / "controllers"
WORLD_PATH = WORLD_DIR / "atlas_shelf_inspection.wbt"
RESULT_PATH = WEBOTS_DIR / "webots_inspection_result.json"
PROTO_NAME = "Atlas"

#: Pelvis spawn height, matching the other two engines' settled stance.
SPAWN_HEIGHT_M = 0.9148
#: How often to check whether the controller has written its result.
RESULT_POLL_SECONDS = 1.0


def find_webots() -> Path | None:
    """Locate the Webots executable via ``WEBOTS_EXE``, PATH, or install paths."""
    override = os.environ.get("WEBOTS_EXE")
    if override and Path(override).is_file():
        return Path(override)
    for executable in ("webots", "webots.exe"):
        found = shutil.which(executable)
        if found:
            return Path(found)
    candidates = [
        Path(r"C:\Program Files\Webots\msys64\mingw64\bin\webots.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Webots"
        / "msys64" / "mingw64" / "bin" / "webots.exe",
        Path("/usr/local/webots/webots"),
        Path("/snap/bin/webots"),
    ]
    return next((item for item in candidates if item.is_file()), None)


def webots_available() -> bool:
    return find_webots() is not None


def _webots_home(executable: Path) -> Path:
    for candidate in (executable.parent, *executable.parents):
        if (candidate / "resources").is_dir():
            return candidate
    return executable.parents[min(3, len(executable.parents) - 1)]


def generate_proto() -> Path:
    """Convert the pinned Atlas URDF into a Webots PROTO.

    The output directory is cleared first: urdf2webots names the PROTO after the
    URDF's ``<robot name>`` and writes it into a subdirectory, so regenerating
    on top of a previous run would otherwise leave stale files that the next
    lookup could pick up instead.
    """
    from urdf2webots.importer import convertUrdfFile

    if PROTO_DIR.exists():
        shutil.rmtree(PROTO_DIR)
    PROTO_DIR.mkdir(parents=True)
    convertUrdfFile(
        input=str(physics_urdf()),
        output=str(PROTO_DIR / PROTO_NAME),
        initTranslation=f"0 0 {SPAWN_HEIGHT_M}",
        linkToDef=True,
    )
    generated = sorted(PROTO_DIR.rglob("*.proto"))
    if not generated:
        raise RuntimeError("urdf2webots produced no PROTO file")
    if len(generated) > 1:
        raise RuntimeError(f"urdf2webots produced several PROTOs: {generated}")

    source = generated[0]
    target = PROTO_DIR / f"{PROTO_NAME}.proto"
    if source != target:
        target.write_text(
            source.read_text(encoding="utf-8").replace(source.stem, PROTO_NAME),
            encoding="utf-8",
        )
        shutil.rmtree(source.parent) if source.parent != PROTO_DIR else source.unlink()
    return target


def _shelf_nodes() -> str:
    """Render :data:`task.SHELF_PARTS` as Webots Solid nodes."""
    nodes = []
    for part in SHELF_PARTS:
        x, y, z = part["pos"]
        half_x, half_y, half_z = part["half"]
        nodes.append(
            f'  DEF {part["name"]} Solid {{\n'
            f'    translation {x} {y} {z}\n'
            f'    children [\n'
            f'      Shape {{\n'
            f'        appearance PBRAppearance {{ baseColor 0.55 0.42 0.28 roughness 1 }}\n'
            f'        geometry Box {{ size {half_x * 2} {half_y * 2} {half_z * 2} }}\n'
            f'      }}\n'
            f'    ]\n'
            f'    name "{part["name"]}"\n'
            f'    boundingObject Box {{ size {half_x * 2} {half_y * 2} {half_z * 2} }}\n'
            f'  }}'
        )
    return "\n".join(nodes)


def generate_world(max_duration_seconds: float = EPISODE_BUDGET_S) -> Path:
    """Write the ``.wbt`` world from the shared task geometry."""
    WORLD_DIR.mkdir(parents=True, exist_ok=True)
    # Only base nodes and the generated Atlas PROTO are used, so the world needs
    # no EXTERNPROTO fetches from the network and runs offline in CI.
    world = f"""#VRML_SIM R2025a utf8

EXTERNPROTO "../protos/{PROTO_NAME}.proto"

WorldInfo {{
  basicTimeStep 2
  gravity 9.81
  contactProperties [
    ContactProperties {{ coulombFriction [ 1.1 ] bounce 0 }}
  ]
}}
Viewpoint {{
  orientation -0.32 0.12 0.94 2.2
  position 2.6 -2.1 1.7
}}
DirectionalLight {{
  direction -0.3 0.3 -1
  intensity 2.6
  castShadows TRUE
}}
DirectionalLight {{
  direction 0.6 0.6 -1
  intensity 1.2
}}
DEF floor Solid {{
  children [
    Shape {{
      appearance PBRAppearance {{ baseColor 0.35 0.37 0.40 roughness 1 metalness 0 }}
      geometry Plane {{ size 20 20 }}
    }}
  ]
  name "floor"
  boundingObject Plane {{ size 20 20 }}
}}
{_shelf_nodes()}
{PROTO_NAME} {{
  translation 0 0 {SPAWN_HEIGHT_M}
  name "atlas"
  controller "atlas_inspection"
  controllerArgs [ "--max-duration" "{max_duration_seconds}" ]
  supervisor TRUE
}}
"""
    WORLD_PATH.write_text(world, encoding="utf-8")
    return WORLD_PATH


def setup(max_duration_seconds: float = EPISODE_BUDGET_S) -> tuple[Path, Path]:
    """Generate everything Webots needs to run the inspection task."""
    return generate_proto(), generate_world(max_duration_seconds)


def run_webots_episode(
    max_duration_seconds: float = EPISODE_BUDGET_S, timeout_seconds: int = 600
) -> dict:
    """Run the inspection task inside Webots and return the controller's result."""
    executable = find_webots()
    if executable is None:
        return {
            "simulator_engine": "Webots",
            "status": "failure",
            "success": False,
            "error": "Webots was not found. Install Webots R2025a or set WEBOTS_EXE.",
        }

    setup(max_duration_seconds)
    RESULT_PATH.unlink(missing_ok=True)

    environment = dict(os.environ)
    environment["WEBOTS_CONTROLLER_PATH"] = str(CONTROLLER_DIR)
    environment.setdefault("WEBOTS_HOME", str(_webots_home(executable)))
    environment["ATLAS_WEBOTS_RESULT"] = str(RESULT_PATH)
    command = [
        str(executable), "--batch", "--mode=fast", "--no-rendering",
        "--stdout", "--stderr", str(WORLD_PATH),
    ]
    # On Windows webots.exe is a launcher that outlives the simulation, so wait
    # for the controller's result file rather than for the process to exit.
    process = subprocess.Popen(
        command, cwd=WORLD_DIR, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            if RESULT_PATH.is_file():
                break
            if process.poll() is not None and not RESULT_PATH.is_file():
                break
            time.sleep(RESULT_POLL_SECONDS)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()

    if not RESULT_PATH.is_file():
        output = process.stdout.read() if process.stdout else ""
        return {
            "simulator_engine": "Webots",
            "status": "failure",
            "success": False,
            "error": "Webots produced no result file.",
            "output": output[-2000:],
        }
    return json.loads(RESULT_PATH.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Atlas inspection task in Webots.")
    parser.add_argument("--setup-only", action="store_true", help="Generate PROTO and world only.")
    parser.add_argument("--max-duration", type=float, default=EPISODE_BUDGET_S)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    if args.setup_only:
        proto, world = setup(args.max_duration)
        print(f"PROTO: {proto}\nWorld: {world}")
        return

    result = run_webots_episode(args.max_duration)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
