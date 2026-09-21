"""Record a montage of every Spot skill offscreen and assemble spot.gif.

Runs each skill in order (hold, wave, sit, stand, bow, nod, turn_to_face)
and renders frames through the MuJoCo offscreen renderer using the
per-step observer, then encodes them into a GIF with imageio.
"""

import pathlib
import sys

import numpy as np

from PIL import Image

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

import mujoco  # noqa: E402
from spot_control import SpotController  # noqa: E402

SKILLS = ["hold", "wave", "sit", "stand", "bow", "nod", "turn_to_face"]
FPS = 12                 # render roughly one frame every 80 ms of sim time
W, H = 400, 300


def main():
    model_path = str(HERE.parent / "models" / "mujoco_menagerie"
                     / "boston_dynamics_spot" / "scene.xml")
    ctl = SpotController(model_path)
    renderer = mujoco.Renderer(ctl.model, H, W)
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_TRACKING
    cam.trackbodyid = ctl._body_id
    cam.distance = 1.6
    cam.azimuth = 110
    cam.elevation = -18

    frames = []
    step_count = [0]

    def observe(controller):
        step_count[0] += 1
        if step_count[0] % 20 != 0:      # 0.004 s/dt * 20 = ~80 ms/frame
            return
        renderer.update_scene(controller.data, camera=cam)
        frames.append(renderer.render())

    ctl.set_on_step(observe)
    for skill in SKILLS:
        params = {"headingDeg": 30.0} if skill == "turn_to_face" else {}
        ctl.execute(skill, params)

    out = HERE.parent / "docs"
    out.mkdir(parents=True, exist_ok=True)
    gif_path = out / "spot.gif"
    pal = [Image.fromarray(f).convert("P", palette=Image.ADAPTIVE, colors=128)
           for f in frames]
    pal[0].save(gif_path, save_all=True, append_images=pal[1:],
                duration=int(1000 / FPS), loop=0, optimize=True)
    print(f"wrote {gif_path} ({len(frames)} frames, "
          f"{gif_path.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
