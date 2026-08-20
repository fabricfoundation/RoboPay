"""Convert the pinned official K1 URDF to a local ignored Webots PROTO."""

from __future__ import annotations

from pathlib import Path

from urdf2webots.importer import convertUrdfFile

from download_k1_model import download


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "scenes" / "K1.proto"


def build() -> Path:
    model_dir = download()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    convertUrdfFile(
        input=str(model_dir / "K1_22dof.urdf"),
        output=str(OUTPUT),
        boxCollision=True,
        relativePathPrefix="../models/booster_k1/",
        targetVersion="R2025a",
    )
    if not OUTPUT.is_file():
        raise RuntimeError("urdf2webots did not create K1.proto")
    # The Tier 1 skill is intentionally a supported inspection station. Keep
    # every articulated link dynamic, but replace only the generated root-body
    # Physics node with NULL so Webots matches MuJoCo's disclosed safety weld.
    content = OUTPUT.read_text(encoding="utf-8")
    start = content.rfind("\n    physics Physics {")
    if start < 0:
        raise RuntimeError("Generated K1 PROTO did not contain root physics")
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
        raise RuntimeError("Generated K1 root Physics node was malformed")
    content = content[:start] + "\n    physics NULL" + content[end:]
    content = content.replace("\\", "/")
    OUTPUT.write_text(content, encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    print(f"Webots PROTO generated at: {build()}")
