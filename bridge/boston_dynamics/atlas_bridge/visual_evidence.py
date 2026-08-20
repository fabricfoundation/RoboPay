"""Render the MuJoCo inspection episode to an annotated animated GIF.

The GIF is produced from the same episode the metrics come from, and every frame
carries the live numbers — phase, active target, end-effector error, targets
completed, pelvis height — so the picture and the evidence cannot disagree.

Green spheres mark the three inspection targets at their tolerance radius. They
are non-colliding markers; :func:`render_episode` asserts that the annotated
episode produces identical metrics to a plain one before writing anything.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import mujoco

from .control_core import ShelfInspectionController
from .episode import run_episode
from .kinematics import jacobian
from .mujoco_env import AtlasInspectionEnvironment
from .task import EPISODE_BUDGET_S, FALL_THRESHOLD_M, INSPECTION_TARGETS

FRAME_WIDTH = 640
FRAME_HEIGHT = 480
#: One rendered frame per this many control steps (2 ms each).
FRAME_STRIDE = 32
GIF_FRAME_MS = 70
#: Shared adaptive palette size for the GIF.
GIF_PALETTE_COLORS = 64
#: Metrics that must not change when the markers are added.
COMPARED_METRICS = (
    "status",
    "targets_completed",
    "mean_position_error_m",
    "max_position_error_m",
    "min_pelvis_height_m",
    "shelf_contacts",
    "control_steps",
    "sim_duration_seconds",
)


def _camera() -> mujoco.MjvCamera:
    """Framed on the working volume so the arm motion is actually visible."""
    camera = mujoco.MjvCamera()
    camera.lookat[:] = (0.26, -0.46, 0.98)
    camera.distance = 2.55
    camera.azimuth = -128
    camera.elevation = -10
    return camera


def _annotate(image, lines: list[tuple[str, str]]):
    """Draw a compact metric panel in the corner of a frame."""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image, "RGBA")
    padding, leading = 10, 15
    height = leading * len(lines) + padding * 2
    draw.rectangle([(0, 0), (250, height)], fill=(12, 14, 18, 205))
    for index, (label, value) in enumerate(lines):
        y = padding + index * leading
        draw.text((padding, y), label, fill=(150, 158, 170))
        draw.text((padding + 118, y), value, fill=(236, 240, 246))
    return image


def render_episode(
    destination: Path, max_duration_seconds: float = EPISODE_BUDGET_S
) -> tuple[Path, dict]:
    """Run one episode, capture annotated frames, and write the GIF."""
    from PIL import Image

    environment = AtlasInspectionEnvironment(show_targets=True)
    controller = ShelfInspectionController(budget_seconds=max_duration_seconds)
    observation = environment.reset(controller.reset(environment.joint_limits()))

    renderer = mujoco.Renderer(environment.model, height=FRAME_HEIGHT, width=FRAME_WIDTH)
    camera = _camera()
    frames: list[Image.Image] = []
    steps = 0

    while observation["sim_time"] < max_duration_seconds:
        angles = environment.joint_angles()
        plan = controller.step(
            environment.end_effector(),
            jacobian(angles, base_rotation=environment.base_rotation()),
            observation["sim_time"],
            angles,
        )
        observation = environment.step(plan.joint_targets)
        steps += 1
        if steps % FRAME_STRIDE == 0:
            renderer.update_scene(environment.data, camera)
            frames.append(
                _annotate(
                    Image.fromarray(renderer.render()),
                    [
                        ("Atlas v4", "MuJoCo"),
                        ("phase", plan.phase),
                        ("target", plan.active_target),
                        ("error", f"{plan.position_error_m * 1000:6.1f} mm"),
                        ("completed", f"{plan.targets_completed}/{len(INSPECTION_TARGETS)}"),
                        ("pelvis", f"{observation['pelvis_height']:.3f} m"),
                        ("fall below", f"{FALL_THRESHOLD_M:.2f} m"),
                        ("shelf hits", str(environment.shelf_contacts)),
                    ],
                )
            )
        if environment.fall_detected or controller.finished:
            break

    renderer.close()
    if not frames:
        raise RuntimeError("No frames were rendered")

    plain = run_episode(
        AtlasInspectionEnvironment(), engine="MuJoCo",
        max_duration_seconds=max_duration_seconds,
    )
    annotated = run_episode(
        AtlasInspectionEnvironment(show_targets=True), engine="MuJoCo",
        max_duration_seconds=max_duration_seconds,
    )
    drift = [key for key in COMPARED_METRICS if plain[key] != annotated[key]]
    if drift:
        raise RuntimeError(f"Target markers changed the episode: {drift}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    # One shared adaptive palette keeps the file small and avoids per-frame
    # colour flicker.
    palette = frames[0].quantize(colors=GIF_PALETTE_COLORS, method=Image.MEDIANCUT)
    quantized = [frame.quantize(palette=palette, dither=Image.FLOYDSTEINBERG) for frame in frames]
    quantized[0].save(
        destination,
        save_all=True,
        append_images=quantized[1:],
        duration=GIF_FRAME_MS,
        loop=0,
        optimize=True,
    )
    # Keep the artefact small enough to load inline in a pull request.
    if destination.stat().st_size > 4_000_000:
        raise RuntimeError(f"GIF is too large: {destination.stat().st_size} bytes")

    return destination, {
        "frames": len(frames),
        "control_steps": steps,
        "metrics_match_plain_run": True,
        "targets_completed": plain["targets_completed"],
        "targets_total": plain["targets_total"],
        "mean_position_error_m": plain["mean_position_error_m"],
        "min_pelvis_height_m": plain["min_pelvis_height_m"],
        "shelf_contacts": plain["shelf_contacts"],
        "fall_detected": plain["fall_detected"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the inspection episode to a GIF.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/evidence/atlas-shelf-inspection.gif"),
    )
    parser.add_argument("--max-duration", type=float, default=EPISODE_BUDGET_S)
    args = parser.parse_args()

    path, summary = render_episode(args.output, args.max_duration)
    print(f"GIF: {path} ({path.stat().st_size / 1024:.0f} KiB)")
    for key, value in summary.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
