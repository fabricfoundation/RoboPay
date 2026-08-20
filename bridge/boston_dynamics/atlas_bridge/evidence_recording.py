"""One recording of one paid action, from the 402 to the settlement.

The profile already has a GIF of the robot and JSON artifacts for the payment,
but a reviewer has to hold the two side by side and trust that they describe the
same run. This records them together instead: a terminal pane that fills in as
the real HTTP exchange happens, next to the simulator rendering the episode that
exchange paid for.

    GET  /robots/{id}/skills        ->  the price, discovered
    POST /action, unpaid            ->  402 with requirements
    sign EIP-3009, nonce = keccak256(action_id)
    POST /action, paid              ->  202 accepted
    Zenoh robot/tunnel/action       ->  [ the simulator pane runs here ]
    Zenoh robot/tunnel/result       ->  succeeded, 3/3
    GET  /action/{id}/status        ->  settled
    settlement + BaseScan link, and the token's own record of the nonce

It is a single pass. The frames come from the episode the paid action triggered,
because the bridge is given a rendering executor rather than being run twice —
a recording of a second, unpaid episode would not be evidence of the first.

Usage (the key stays in your shell; it is never printed or written)::

    SETTLEMENT_MNEMONIC="..." python -m bridge.boston_dynamics.atlas_bridge.evidence_recording \\
        --tunnel /path/to/tunnel --output docs/evidence/atlas-paid-action.gif

``--dry-run`` records discovery and the 402 only, signs nothing, and is enough
to check the layout without spending anything.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

from . import demo_fabric_e2e as flow
from .task import INSPECTION_TARGETS

SIM_WIDTH, SIM_HEIGHT = 640, 520
LOG_WIDTH = 560
FRAME_WIDTH, FRAME_HEIGHT = LOG_WIDTH + SIM_WIDTH, SIM_HEIGHT
#: One rendered frame per this many control steps (2 ms each).
FRAME_STRIDE = 40
GIF_FRAME_MS = 90
GIF_PALETTE_COLORS = 96
#: How long a caption-only step is held, in frames.
HOLD_FRAMES = 9

BACKGROUND = (14, 16, 22)
DIM = (120, 132, 148)
TEXT = (226, 232, 240)
OK = (74, 222, 128)
WARN = (250, 204, 21)
LINK = (125, 211, 252)


class Transcript:
    """The terminal pane: lines appear as the run produces them."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, tuple[int, int, int]]] = []
        self._lock = threading.Lock()

    def add(self, tag: str, text: str, colour=TEXT) -> None:
        with self._lock:
            self.lines.append((tag, text, colour))
        print(f"  {tag:<10} {text}", flush=True)

    def snapshot(self) -> list[tuple[str, str, tuple[int, int, int]]]:
        with self._lock:
            return list(self.lines)


def _font(size: int):
    from PIL import ImageFont

    for name in ("consola.ttf", "DejaVuSansMono.ttf", "cour.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_log(transcript: Transcript, title: str):
    """Draw the terminal pane. The newest lines win if it overflows."""
    from PIL import Image, ImageDraw

    panel = Image.new("RGB", (LOG_WIDTH, FRAME_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(panel)
    header, body = _font(15), _font(13)

    draw.text((16, 14), title, font=header, fill=TEXT)
    draw.line([(16, 36), (LOG_WIDTH - 16, 36)], fill=(38, 44, 56), width=1)

    lines = transcript.snapshot()
    capacity = (FRAME_HEIGHT - 60) // 20
    for index, (tag, text, colour) in enumerate(lines[-capacity:]):
        y = 48 + index * 20
        draw.text((16, y), tag, font=body, fill=DIM)
        draw.text((16 + 92, y), text, font=body, fill=colour)
    return panel


def _compose(transcript: Transcript, title: str, sim_frame=None):
    from PIL import Image, ImageDraw

    frame = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), BACKGROUND)
    frame.paste(_render_log(transcript, title), (0, 0))
    if sim_frame is not None:
        frame.paste(sim_frame, (LOG_WIDTH, 0))
    else:
        draw = ImageDraw.Draw(frame)
        draw.text((LOG_WIDTH + 190, FRAME_HEIGHT // 2 - 10),
                  "simulator idle", font=_font(15), fill=DIM)
    return frame


class Recorder:
    """Collects composed frames, and can hold on the current state."""

    def __init__(self, transcript: Transcript, title: str) -> None:
        self.transcript = transcript
        self.title = title
        self.frames: list = []
        self.latest_sim = None

    def hold(self, count: int = HOLD_FRAMES) -> None:
        for _ in range(count):
            self.frames.append(_compose(self.transcript, self.title, self.latest_sim))

    def add_sim(self, sim_frame) -> None:
        self.latest_sim = sim_frame
        self.frames.append(_compose(self.transcript, self.title, sim_frame))


def rendering_executor(recorder: Recorder):
    """An episode runner that renders the episode it is running.

    Signature matches what the bridge's handler calls, so the paid action
    reaches this and the frames are of that action's episode.
    """
    def execute(max_duration_seconds: float, stop_requested=None) -> dict:
        import mujoco
        from PIL import Image

        from .control_core import ShelfInspectionController
        from .episode import run_episode
        from .kinematics import jacobian
        from .mujoco_env import AtlasInspectionEnvironment
        from .visual_evidence import _annotate, _camera

        environment = AtlasInspectionEnvironment(show_targets=True)
        controller = ShelfInspectionController(budget_seconds=max_duration_seconds)
        renderer = mujoco.Renderer(environment.model, height=SIM_HEIGHT, width=SIM_WIDTH)
        camera = _camera()

        observation = environment.reset(controller.reset(environment.joint_limits()))
        steps = 0
        announced: set[str] = set()
        try:
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

                if plan.phase not in announced:
                    announced.add(plan.phase)
                    recorder.transcript.add("simulator", f"phase {plan.phase}")
                if steps % FRAME_STRIDE == 0:
                    renderer.update_scene(environment.data, camera)
                    recorder.add_sim(_annotate(
                        Image.fromarray(renderer.render()),
                        [
                            ("Atlas v4", "MuJoCo"),
                            ("phase", plan.phase),
                            ("target", plan.active_target),
                            ("error", f"{plan.position_error_m * 1000:6.1f} mm"),
                            ("completed",
                             f"{plan.targets_completed}/{len(INSPECTION_TARGETS)}"),
                            ("pelvis", f"{observation['pelvis_height']:.3f} m"),
                            ("shelf hits", str(environment.shelf_contacts)),
                        ],
                    ))
                if environment.fall_detected or controller.finished:
                    break
        finally:
            renderer.close()

        # The metrics come from the same shared loop every other engine uses, on
        # a fresh environment, so the recorded run is scored exactly as the
        # committed episodes are.
        return run_episode(
            AtlasInspectionEnvironment(), engine="MuJoCo",
            max_duration_seconds=max_duration_seconds, stop_requested=stop_requested,
        )
    return execute


def record(binary: Path, robot_id: str, payee: str, dry_run: bool,
           output: Path) -> dict:
    """Drive the real hosted-relay flow and compose one recording of it."""
    action_id = f"atlas-inspect-{int(time.time())}"
    transcript = Transcript()
    recorder = Recorder(transcript, "Atlas — one paid action, end to end")

    os.environ["ROBOT_ID"] = robot_id
    os.environ.setdefault("SKILL_CATALOG_PATH", str(flow.PROFILE_DIR / "skill-catalog.json"))
    os.environ.setdefault("ROBOT_PROFILE_ID", flow.PROFILE_ID)

    from .bridge import AtlasZenohBridge

    bridge = AtlasZenohBridge(execute=rendering_executor(recorder))
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="atlas_record_"))
    tunnel = flow.Tunnel(binary, robot_id, payee, workdir)
    settlement: dict = {}
    try:
        transcript.add("relay", flow.FABRIC_API_BASE)
        transcript.add("robot", robot_id)
        recorder.hold(6)
        if not tunnel.wait_until_connected(flow.TUNNEL_CONNECT_TIMEOUT_S):
            raise SystemExit("the tunnel never reached the relay")
        transcript.add("tunnel", "connected over WSS", OK)
        recorder.hold()

        # 1. Discovery — the price is read, not assumed.
        status, skills_body, _ = flow._request(
            "GET", f"{flow.FABRIC_API_BASE}/robots/{robot_id}/skills")
        skills = skills_body.get("skills") or []
        chosen = next((s for s in skills if s.get("skill_id") == flow.SKILL_ID), {})
        price = str(chosen.get("price_usdc") or "")
        transcript.add("GET", f"/skills  {status}  " + ", ".join(
            s.get("skill_id", "") for s in skills), OK if status == 200 else WARN)
        transcript.add("discovered", f"{flow.SKILL_ID} @ {price} USDC")
        recorder.hold()

        action_url = f"{flow.FABRIC_API_BASE}/robots/{robot_id}/action"
        body = {
            "action": flow.SKILL_ID, "skill_id": flow.SKILL_ID, "robot_id": robot_id,
            "action_id": action_id, "idempotency_key": action_id,
            "params": {"maxDurationSec": flow.EPISODE_SECONDS},
        }

        # 2. Unpaid — refused by the relay itself.
        status, _, headers = flow._request("POST", action_url, body)
        requirements = flow._decode_header(flow._header(headers, "PAYMENT-REQUIRED"))
        accepted = (requirements.get("accepts") or [{}])[0]
        amount = accepted.get("amount") or accepted.get("maxAmountRequired")
        transcript.add("POST", f"/action unpaid  ->  {status}", WARN)
        transcript.add("required", f"{amount} raw to {str(accepted.get('payTo',''))[:14]}…")
        recorder.hold()

        if dry_run:
            transcript.add("dry run", "nothing signed, nothing spent", WARN)
            recorder.hold(12)
            _write_gif(recorder.frames, output)
            return {"dry_run": True, "frames": len(recorder.frames)}

        # 3. Pay for the price that was quoted.
        authorization, signature, payer = flow.sign_for(action_id, accepted)
        transcript.add("sign", "EIP-3009 authorization")
        transcript.add("nonce", "keccak256(action_id)")
        transcript.add("payer", payer[:20] + "…")
        recorder.hold()

        header = flow.payment_header(authorization, signature, accepted,
                                     int(requirements.get("x402Version") or 1))
        status, paid_body, _ = flow._request(
            "POST", action_url, body, {"PAYMENT-SIGNATURE": header})
        transcript.add("POST", f"/action paid  ->  {status} {paid_body.get('status','')}",
                       OK if status == 202 else WARN)
        transcript.add("zenoh", "robot/tunnel/action published")
        recorder.hold()

        # 4. The simulator pane fills in here, from the executor above.
        status_url = f"{flow.FABRIC_API_BASE}/robots/{robot_id}/action/{action_id}/status"
        deadline = time.monotonic() + flow.STATUS_TIMEOUT_S
        terminal = None
        while time.monotonic() < deadline:
            code, candidate, _ = flow._request("GET", status_url)
            if code == 200 and candidate.get("state") in flow.TERMINAL_STATES:
                terminal = candidate
                if candidate.get("state") == "succeeded":
                    settle_deadline = time.monotonic() + flow.SETTLEMENT_POLL_S
                    while time.monotonic() < settle_deadline:
                        code, candidate, _ = flow._request("GET", status_url)
                        if code == 200 and (candidate.get("settled")
                                            or candidate.get("settlement_error")):
                            terminal = candidate
                            break
                        time.sleep(2)
                break
            time.sleep(2)
        if terminal is None:
            raise SystemExit("no terminal status from the relay")

        result = terminal.get("result") or {}
        transcript.add("zenoh", "robot/tunnel/result received", OK)
        transcript.add("GET", f"/status  ->  {terminal.get('state')}  "
                              f"{result.get('targets_completed')}/{result.get('targets_total')}",
                       OK if terminal.get("state") == "succeeded" else WARN)
        transcript.add("correlated", f"action_id {action_id[-12:]}", OK)
        recorder.hold()

        # 5. Settlement, and the token's own record of it.
        settlement = terminal.get("settlement") or {}
        tx_hash = settlement.get("transaction") or ""
        if tx_hash:
            chain = flow.confirm_on_chain(tx_hash, action_id)
            transcript.add("settled", f"{chain['transfer'].get('amount_usdc')} USDC  "
                                      f"block {chain['block_number']}", OK)
            transcript.add("basescan", f"sepolia.basescan.org/tx/{tx_hash[:18]}…", LINK)
            authorization_state = flow.authorization_used_on_chain(
                payer, action_id, chain.get("block_number", 0))
            transcript.add("token says", f"authorization spent: {authorization_state['used']}",
                           OK if authorization_state["used"] else WARN)
            transcript.add("bound", "nonce == keccak256(action_id)",
                           OK if chain["nonce_binds_settlement_to_action"] else WARN)
            settlement = {"tx": tx_hash, **chain}
        else:
            transcript.add("settled", "no — nothing was charged", WARN)
        recorder.hold(16)
    finally:
        tunnel.close()
        bridge.close()

    _write_gif(recorder.frames, output)
    return {
        "action_id": action_id, "robot_id": robot_id,
        "frames": len(recorder.frames), "settlement": settlement,
    }


def _write_gif(frames: list, output: Path) -> None:
    from PIL import Image

    if not frames:
        raise RuntimeError("no frames were recorded")
    output.parent.mkdir(parents=True, exist_ok=True)
    palette = frames[0].quantize(colors=GIF_PALETTE_COLORS, method=Image.MEDIANCUT)
    quantized = [f.quantize(palette=palette, dither=Image.FLOYDSTEINBERG) for f in frames]
    quantized[0].save(
        output, save_all=True, append_images=quantized[1:],
        duration=GIF_FRAME_MS, loop=0, optimize=True, disposal=2,
    )
    size = output.stat().st_size
    print(f"\n  {output}  {len(frames)} frames, {size / 1_048_576:.2f} MB", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record one paid Atlas action from the 402 to the settlement."
    )
    parser.add_argument("--tunnel", type=Path, required=True)
    parser.add_argument("--robot-id", default=f"atlas-sim-{int(time.time())}")
    parser.add_argument("--payee", default=flow.DEFAULT_PAYEE)
    parser.add_argument("--output", type=Path,
                        default=Path("docs/evidence/atlas-paid-action.gif"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    if not args.tunnel.is_file():
        raise SystemExit(f"tunnel binary not found: {args.tunnel}")

    summary = record(args.tunnel, args.robot_id, args.payee, args.dry_run, args.output)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
