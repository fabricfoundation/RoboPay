"""Render terminal output as styled terminal screenshots for evidence."""
import sys
import os
import hashlib
from PIL import Image, ImageDraw, ImageFont

def render_terminal(text: str, filename: str, width: int = 1280, height: int = 720):
    """Render terminal output as a dark-themed screenshot."""
    img = Image.new('RGB', (width, height), (13, 14, 18))
    draw = ImageDraw.Draw(img)

    # Try to load a monospace font
    font = None
    font_paths = [
        "C:/Windows/Fonts/Courier New.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/consolaz.ttf",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 14)
                break
            except:
                continue
    if font is None:
        font = ImageFont.load_default()

    # Parse ANSI-like colors (simple approach)
    lines = text.split('\n')
    y = 20
    x = 20

    for line in lines:
        if y > height - 20:
            break

        # Handle color-coded lines
        if 'HTTP/1.1 402' in line or 'Payment Required' in line:
            draw.text((x, y), line, fill=(255, 80, 80), font=font)
        elif line.startswith('[ 1]') or line.startswith('[ 2]') or line.startswith('[ 3]') or \
             line.startswith('[ 4]') or line.startswith('[ 5]') or line.startswith('[ 6]') or \
             line.startswith('[ 7]') or line.startswith('[ 8]') or line.startswith('[ 9]') or \
             line.startswith('[10]'):
            draw.text((x, y), line, fill=(80, 255, 120), font=font)
        elif 'status=' in line or 'scene:' in line or 'PASS' in line or 'FAIL' in line:
            draw.text((x, y), line, fill=(255, 200, 80), font=font)
        elif line.startswith('     ') or '->' in line:
            draw.text((x, y), line, fill=(150, 180, 220), font=font)
        elif line.startswith('-') or line.startswith('='):
            draw.text((x, y), line, fill=(100, 100, 110), font=font)
        else:
            draw.text((x, y), line, fill=(200, 200, 210), font=font)

        y += 18

    img.save(filename, 'PNG')
    return filename

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.txt> <output.png> [width] [height]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 1280
    height = int(sys.argv[4]) if len(sys.argv) > 4 else 720

    with open(input_file, 'r', encoding='utf-8') as f:
        text = f.read()

    render_terminal(text, output_file, width, height)
    print(f"Saved: {output_file}")
