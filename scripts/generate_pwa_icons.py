#!/usr/bin/env python3
"""Generate the branded PWA / home-screen icons for Wizzrobe.

This is a one-off developer tool. The PNGs it writes are committed to the repo
under ``static/icons/`` and are served as static assets at runtime — the running
application never imports Pillow. Re-run this only when the mark or palette
changes.

Run from the project root:
    poetry run python scripts/generate_pwa_icons.py            # static/icons + web/public/icons
    poetry run python scripts/generate_pwa_icons.py --native   # also the iOS/Android app icons and splash

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


def _render_icon(
    size: int, mark_fraction: float, *, rounded: bool, opaque: bool,
    corner: float = CORNER_FRACTION, plate: bool = True,
) -> Image.Image:
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

    if not plate:
        pass
    elif rounded:
        radius = round(corner * big)
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


WEB_ICONS_DIR = PROJECT_ROOT / "web" / "public" / "icons"
IOS_ASSETS = PROJECT_ROOT / "web" / "ios" / "App" / "App" / "Assets.xcassets"
ANDROID_RES = PROJECT_ROOT / "web" / "android" / "app" / "src" / "main" / "res"

ANDROID_DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}
ANDROID_SPLASH = {
    "mdpi": (320, 480), "hdpi": (480, 800), "xhdpi": (720, 1280), "xxhdpi": (960, 1600), "xxxhdpi": (1280, 1920),
}


def _save(icon: Image.Image, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    icon.save(out_path, format="PNG", optimize=True)
    print(f"wrote {out_path.relative_to(PROJECT_ROOT)} ({icon.width}x{icon.height}, {icon.mode})")


def _splash(width: int, height: int) -> Image.Image:
    """Charcoal screen with the mark centred, a fifth of the shorter side wide."""
    side = min(width, height)
    mark = _render_icon(side, 0.2, rounded=False, opaque=False, plate=False)
    flat = Image.new("RGB", (width, height), CHARCOAL)
    flat.paste(mark, ((width - side) // 2, (height - side) // 2), mark)
    return flat


def native_assets() -> None:
    """The store apps' launcher icons and launch screens (web/ios, web/android)."""
    # iOS: one 1024 opaque icon; iOS draws its own rounded mask.
    _save(_render_icon(1024, 0.62, rounded=False, opaque=True), IOS_ASSETS / "AppIcon.appiconset" / "AppIcon-512@2x.png")
    splash = _splash(2732, 2732)
    for name in ("splash-2732x2732.png", "splash-2732x2732-1.png", "splash-2732x2732-2.png"):
        _save(splash, IOS_ASSETS / "Splash.imageset" / name)

    for density, scale in ANDROID_DENSITIES.items():
        launcher = round(48 * scale)
        _save(_render_icon(launcher, 0.66, rounded=True, opaque=False), ANDROID_RES / f"mipmap-{density}" / "ic_launcher.png")
        _save(
            _render_icon(launcher, 0.6, rounded=True, opaque=False, corner=0.5),
            ANDROID_RES / f"mipmap-{density}" / "ic_launcher_round.png",
        )
        # Adaptive foreground: 108dp canvas, the mark kept inside the 66dp safe circle.
        _save(
            _render_icon(round(108 * scale), 0.42, rounded=False, opaque=False, plate=False),
            ANDROID_RES / f"mipmap-{density}" / "ic_launcher_foreground.png",
        )
        width, height = ANDROID_SPLASH[density]
        _save(_splash(width, height), ANDROID_RES / f"drawable-port-{density}" / "splash.png")
        _save(_splash(height, width), ANDROID_RES / f"drawable-land-{density}" / "splash.png")
    _save(_splash(480, 320), ANDROID_RES / "drawable" / "splash.png")
    background = ANDROID_RES / "values" / "ic_launcher_background.xml"
    background.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        f'    <color name="ic_launcher_background">#{"".join(f"{c:02X}" for c in CHARCOAL)}</color>\n'
        "</resources>\n"
    )
    print(f"wrote {background.relative_to(PROJECT_ROOT)}")


def main() -> None:
    for name, (size, mark_fraction, rounded, opaque) in ICONS.items():
        icon = _render_icon(size, mark_fraction, rounded=rounded, opaque=opaque)
        _save(icon, ICONS_DIR / name)
        _save(icon, WEB_ICONS_DIR / name)
    if "--native" in sys.argv[1:]:
        native_assets()


if __name__ == "__main__":
    main()
