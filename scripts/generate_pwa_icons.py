#!/usr/bin/env python3
"""Generate the branded PWA / home-screen icons for Wizzrobe.

This is a one-off developer tool. The PNGs it writes are committed to the repo
under ``static/icons/`` and are served as static assets at runtime — the running
application never imports Pillow. Re-run this only when the mark or palette
changes.

Run from the project root:
    poetry run python scripts/generate_pwa_icons.py

The output is deterministic, so the script is idempotent: running it repeatedly
produces byte-identical PNGs.

The mark is the SpeedGaming triforce, drawn GEOMETRICALLY (never rendered from a
font) so it stays crisp at every size: an apex-up equilateral triangle
subdivided into three filled upward gold triangles around a hollow charcoal
centre — the same geometry as ``static/triforce.svg``. Edges are anti-aliased by
drawing at 4x resolution and downsampling with LANCZOS.
"""
import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.stderr.write(
        "Pillow is required to generate the PWA icons but is not installed.\n"
        "Install the project dependencies first:\n\n"
        "    poetry install\n\n"
        "then re-run: poetry run python scripts/generate_pwa_icons.py\n"
    )
    sys.exit(1)

CHARCOAL = (0x17, 0x12, 0x0D)
GOLD = (0xE0, 0xA8, 0x2E)
DEEP_GOLD = (0x9C, 0x6B, 0x12)

SUPERSAMPLE = 4
CORNER_FRACTION = 0.20
EDGE_FRACTION = 0.012

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = PROJECT_ROOT / "static" / "icons"


def _triforce_triangles(cx: float, cy: float, mark_w: float) -> list[list[tuple[float, float]]]:
    """Return the three upward gold triangles of an apex-up triforce.

    The triforce's equilateral bounding box is ``mark_w`` wide and centred on
    ``(cx, cy)``. The three returned triangles tile everything except the
    central inverted triangle, which is left as background (the hollow centre).
    """
    height = mark_w * math.sqrt(3) / 2.0
    apex = (cx, cy - height / 2)
    bottom_left = (cx - mark_w / 2, cy + height / 2)
    bottom_right = (cx + mark_w / 2, cy + height / 2)

    def mid(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    left_mid = mid(apex, bottom_left)
    right_mid = mid(apex, bottom_right)
    base_mid = mid(bottom_left, bottom_right)

    return [
        [apex, left_mid, right_mid],
        [left_mid, bottom_left, base_mid],
        [right_mid, base_mid, bottom_right],
    ]


def _render_icon(size: int, mark_fraction: float, *, rounded: bool, opaque: bool) -> Image.Image:
    """Render a single icon.

    ``mark_fraction`` is the triforce bounding-box width as a fraction of the
    canvas. ``rounded`` draws a rounded-square charcoal plate with transparent
    corners; otherwise the charcoal fills full-bleed. ``opaque`` flattens the
    result onto a charcoal RGB canvas (no alpha channel) — required for the
    maskable and Apple touch icons.
    """
    big = size * SUPERSAMPLE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if rounded:
        radius = round(CORNER_FRACTION * big)
        draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=radius, fill=(*CHARCOAL, 255))
    else:
        draw.rectangle([0, 0, big - 1, big - 1], fill=(*CHARCOAL, 255))

    mark_w = mark_fraction * big
    centre = big / 2
    edge = max(1, round(mark_w * EDGE_FRACTION))
    for triangle in _triforce_triangles(centre, centre, mark_w):
        draw.polygon(triangle, fill=(*GOLD, 255), outline=(*DEEP_GOLD, 255), width=edge)

    img = img.resize((size, size), Image.LANCZOS)

    if opaque:
        flat = Image.new("RGB", (size, size), CHARCOAL)
        flat.paste(img, (0, 0), img)
        return flat
    return img


ICONS: dict[str, tuple[int, float, bool, bool]] = {
    "icon-192.png": (192, 0.78, True, False),
    "icon-512.png": (512, 0.78, True, False),
    # Maskable: keep the mark inside the central 60% safe zone or launcher masks clip it.
    "icon-maskable-512.png": (512, 0.56, False, True),
    # Apple touch: full-bleed and opaque (RGB); iOS applies its own mask.
    "apple-touch-icon.png": (180, 0.82, False, True),
}


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    for name, (size, mark_fraction, rounded, opaque) in ICONS.items():
        icon = _render_icon(size, mark_fraction, rounded=rounded, opaque=opaque)
        out_path = ICONS_DIR / name
        icon.save(out_path, format="PNG", optimize=True)
        print(f"wrote {out_path.relative_to(PROJECT_ROOT)} ({icon.width}x{icon.height}, {icon.mode})")


if __name__ == "__main__":
    main()
