"""Render settle.png (dark-terminal) and demo.mp4 (settle.png + title card)
from the terminal log. Re-runnable: just overwrite the artifacts."""
import hashlib
import io
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
TERMINAL_LOG = HERE / "terminal" / "output.txt"
SETTLE_PNG = HERE / "settle.png"
DEMO_MP4 = HERE / "demo.mp4"


def _font(size: int):
    candidates = [
        "consola.ttf", "Consolas.ttf", "C:/Windows/Fonts/consola.ttf",
        "consolas.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def render_settle_png() -> bytes:
    """Dark terminal frame: title, 10-step trace, on-chain proof."""
    bg = (12, 12, 12)
    fg_title = (220, 220, 220)
    fg_dim = (160, 160, 160)
    fg_ok = (110, 200, 110)
    fg_warn = (220, 170, 80)
    fg_err = (220, 90, 90)
    fg_step = (130, 180, 220)
    fg_pay = (255, 200, 120)
    fg_proof = (255, 215, 0)

    lines = TERMINAL_LOG.read_text(encoding="utf-8").splitlines()
    font = _font(15)
    font_pay = _font(15)
    line_h = 20

    width = 1280
    height = line_h * (len(lines) + 4)
    img = Image.new("RGB", (width, height), bg)
    d = ImageDraw.Draw(img)

    y = 20
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("===") or stripped.startswith("---"):
            d.text((40, y), line, font=font, fill=fg_dim)
        elif line.startswith("[") and "]" in line:
            tag = line[:line.index("]") + 1]
            d.text((40, y), tag, font=font, fill=fg_step)
            rest = line[len(tag):]
            color = fg_title
            if "SETTLE" in line or "verified on Base Sepolia" in line:
                color = fg_ok
            if "402 Payment Required" in line or "no re-execution" in line:
                color = fg_warn
            if "PASS" in line:
                color = fg_ok
            d.text((40 + font.getlength(tag) + 6, y), rest, font=font, fill=color)
        elif "txHash" in line or "block=" in line:
            d.text((40, y), line, font=font_pay, fill=fg_pay)
        elif "0x" in line:
            d.text((40, y), line, font=font_pay, fill=fg_proof)
        else:
            d.text((40, y), line, font=font, fill=fg_title)
        y += line_h

    png = io.BytesIO()
    img.save(png, format="PNG", optimize=True)
    return png.getvalue()


def main():
    png_bytes = render_settle_png()
    SETTLE_PNG.write_bytes(png_bytes)
    print(f"settle.png written: {len(png_bytes)} bytes, sha256="
          f"{hashlib.sha256(png_bytes).hexdigest()}")

    # Build a short mp4: title card + 3 sec of settle.png held, fade out
    title_png = HERE / "_demo_title.png"
    frame = Image.new("RGB", (1280, 720), bg_title := (12, 12, 12))
    d = ImageDraw.Draw(frame)
    d.text((40, 40), "RoboPay Tier 1 — tron1-001-arm-001 (planar biped walker)",
           font=_font(20), fill=(220, 220, 220))
    d.text((40, 80), "Real Go Tunnel x402 payment gate | MuJoCo physics",
           font=_font(18), fill=(160, 160, 160))
    d.text((40, 130), "402 -> pay -> MuJoCo gait -> settle", font=_font(20),
           fill=(110, 200, 110))
    d.text((40, 170), "txHash: 0xb02f36544c9b42854ed8e641c8cf75d6e4de834a6ef58b4e1fa1b4b896af0d4e",
           font=_font(14), fill=(255, 215, 0))
    d.text((40, 200), "block=45415117 payer=0xF2749b5f...07D4a payee=0x742d35Cc...f44e",
           font=_font(14), fill=(255, 200, 120))
    title_png.write_bytes(io.BytesIO(b"").getvalue() or _render_title_to_bytes(frame))

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmd = [
            ffmpeg, "-y",
            "-loop", "1", "-t", "8", "-i", str(title_png),
            "-loop", "1", "-t", "8", "-i", str(SETTLE_PNG),
            "-filter_complex",
            "[0:v]format=yuv420p,fade=t=in:st=0:d=1,fade=t=out:st=7:d=1[v0];"
            "[1:v]format=yuv420p,fade=t=in:st=0:d=1,fade=t=out:st=7:d=1[v1]",
            "-map", "[v0]", "-map", "[v1]",
            "-c:v", "libx264", "-r", "1", "-pix_fmt", "yuv420p",
            str(DEMO_MP4),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        title_png.unlink(missing_ok=True)
        print(f"demo.mp4 written via ffmpeg ({DEMO_MP4.stat().st_size} bytes)")
    else:
        title_png.unlink(missing_ok=True)
        print("ffmpeg not found; demo.mp4 skipped (settle.png rendered)")


def _render_title_to_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


if __name__ == "__main__":
    sys.exit(0)