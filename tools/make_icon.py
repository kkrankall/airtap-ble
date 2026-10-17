"""Draw the integration's brand icons into custom_components/airtap_ble/brand/.

Home Assistant (2026.x) serves brand images from a custom integration's own
`brand/` folder: icon.png (256 px), icon@2x.png (512 px) and dark variants.
The artwork is original: the face of a floor-register booster fan (louvred
grille and control strip) with air rising out of it, and a wireless badge.
Run with Pillow installed:

    python tools/make_icon.py
"""

from __future__ import annotations

import math
import pathlib

from PIL import Image, ImageDraw, ImageFilter

OUT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "airtap_ble" / "brand"
S = 1024  # drawing size; scaled down for smooth edges

NAVY = (0x00, 0x5D, 0x8E, 255)
BLUE = (0x4C, 0xA2, 0xFF, 255)
SKY = (0x8F, 0xC3, 0xFF, 255)
SLAT = (0xC9, 0xD4, 0xE1, 255)
WHITE = (255, 255, 255, 255)


def stroke(d: ImageDraw.ImageDraw, points, fill, width: int) -> None:
    """A smooth round-capped stroke: discs stamped densely along the path.

    Pillow's wide polylines fray on tight curves; stamping does not.
    """
    r = width / 2
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / 3))
        for i in range(n + 1):
            x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
            d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def airflow(x: float, top: float, bottom: float, sway: float) -> list[tuple[float, float]]:
    """One stream of air: a single soft S-bend from the grille up to `top`."""
    steps = 60
    return [
        (x + sway * math.sin(math.pi * i / steps), bottom - (bottom - top) * i / steps)
        for i in range(steps + 1)
    ]


def arrowhead(d: ImageDraw.ImageDraw, x: float, y: float, fill, width: int) -> None:
    """An upward chevron whose tip is at (x, y)."""
    arm = 62
    stroke(d, [(x - arm, y + arm), (x, y), (x + arm, y + arm)], fill, width)


def draw(dark: bool) -> Image.Image:
    rim = SKY if dark else NAVY
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Air pushed up out of the grille, the middle stream tallest.
    d = ImageDraw.Draw(img)
    for x, top, sway in ((300, 200, -26), (512, 110, 26), (724, 200, -26)):
        stroke(d, airflow(x, top, 395, sway), BLUE, 46)
        arrowhead(d, x, top, BLUE, 46)

    # Soft shadow under the vent.
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [90, 500, S - 90, 900], radius=80, fill=(0, 0, 0, 60 if dark else 90)
    )
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(26)))

    d = ImageDraw.Draw(img)
    body = [70, 450, S - 70, 860]
    d.rounded_rectangle(body, radius=80, fill=WHITE, outline=rim, width=44)
    # Control strip with its display at the left end, as on the T-series.
    d.rounded_rectangle([150, 530, 270, 780], radius=40, fill=NAVY)
    d.ellipse([181, 576, 239, 634], fill=SKY)
    d.rounded_rectangle([186, 672, 234, 740], radius=18, fill=SKY)
    # Louvres.
    for y in (535, 610, 685, 760):
        d.rounded_rectangle([330, y - 20, S - 150, y + 20], radius=20, fill=SLAT)

    # Wireless badge, bottom right, overlapping the vent's corner: a
    # broadcast mark, a dot with waves on both sides.
    bx, by, br = S - 205, S - 205, 170
    d.ellipse([bx - br, by - br, bx + br, by + br], fill=BLUE, outline=WHITE, width=26)
    d.ellipse([bx - 27, by - 27, bx + 27, by + 27], fill=WHITE)
    for radius in (70, 118):
        box = [bx - radius, by - radius, bx + radius, by + radius]
        d.arc(box, start=135, end=225, fill=WHITE, width=28)
        d.arc(box, start=-45, end=45, fill=WHITE, width=28)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for dark in (False, True):
        art = draw(dark)
        prefix = "dark_" if dark else ""
        for size, suffix in ((256, ""), (512, "@2x")):
            path = OUT / f"{prefix}icon{suffix}.png"
            art.resize((size, size), Image.LANCZOS).save(path, optimize=True)
            print(path.relative_to(OUT.parent.parent.parent), f"{size}x{size}")


if __name__ == "__main__":
    main()
