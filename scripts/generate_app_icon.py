#!/usr/bin/env python3
"""Rasterize the Netgen app icon at all sizes the build pipeline
needs, plus bundle the macOS .icns and Windows .ico containers.

Source design: `resources/icons/netgen.svg`. This script renders
the icon directly with Pillow primitives — Pillow doesn't read
SVG natively and adding cairosvg as a dependency just to bootstrap
build assets felt heavy. The drawing code below mirrors the SVG
element-for-element; any visual tweak must be applied both places
to keep the source of truth (the SVG) in sync.

Outputs (written under resources/icons/):

  netgen.png           — 256×256, the canonical "use this if you
                         only need one PNG" file. AppImage points
                         here.
  netgen-{N}.png       — for N in 16, 32, 64, 128, 256, 512, 1024.
  netgen.icns          — macOS bundle, built via /usr/bin/iconutil
                         from an intermediate netgen.iconset/
                         directory. macOS-only step; skipped with
                         a warning on Linux / Windows.
  netgen.ico           — Windows multi-resolution icon, built via
                         PIL's Image.save(format="ICO").

Run with the venv python so Pillow is on the path:

    venv/bin/python scripts/generate_app_icon.py

Re-run any time the design changes. The build scripts treat the
checked-in PNGs as the canonical artifacts — CI does NOT call
this script. That keeps macOS-only steps (iconutil) off Linux CI
runners.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    print(
        "ERROR: Pillow not installed. Run "
        "`venv/bin/python -m pip install Pillow` and retry.",
        file=sys.stderr,
    )
    sys.exit(1)


REPO_ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = REPO_ROOT / "resources" / "icons"

PALETTE = {
    "bg":          (15, 23, 42),     # #0f172a slate-900
    "shine":       (255, 255, 255),  # alpha-blended onto bg
    "arc_track":   (30, 41, 59),     # #1e293b — unlit gauge bed
    "arc_gray":    (100, 116, 139),  # #64748b — Scapy / low
    "arc_blue":    (96, 165, 250),   # #60a5fa — DPDK / mid
    "arc_purple":  (192, 132, 252),  # #c084fc — RDMA / high
    "tick":        (148, 163, 184),  # #94a3b8
    "needle":      (248, 250, 252),  # #f8fafc
    "pivot_dark":  (15, 23, 42),     # #0f172a — pivot center
    "packet":      (192, 132, 252),  # #c084fc — matches RDMA
}


def _rounded_rect_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """Return an 8-bit alpha mask for a rounded rectangle of the
    given size + corner radius. Used to clip the icon body so the
    rounded-square silhouette is correct for every output size."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=radius, fill=255,
    )
    return mask


def _arc(
    draw: ImageDraw.ImageDraw,
    cx: float, cy: float, r: float,
    start_deg: float, end_deg: float,
    color: tuple[int, int, int], width: float,
) -> None:
    """Draw an arc using PIL's `arc()` with the SVG-coordinate
    angle convention.

    SVG measures angles counter-clockwise from the positive x axis
    (3 o'clock). PIL's `arc()` measures angles CLOCKWISE from 3
    o'clock. To express a gauge that runs from 9 o'clock (180°
    SVG) to 3 o'clock (0° SVG) passing through 12 o'clock (90°
    SVG), we hand PIL the same numbers in the same order — both
    libraries agree on east=0 and south=90 by accident of design,
    so the upper half-circle reads as PIL angles 180 → 360 going
    counter-clockwise. Easier to just translate and verify
    visually than to derive a closed-form mapping.
    """
    bbox = (cx - r, cy - r, cx + r, cy + r)
    # PIL's arc takes "start" and "end" with angles measured
    # CLOCKWISE from 3 o'clock. The SVG paths in netgen.svg run
    # from 9 o'clock counter-clockwise up to 3 o'clock — so in
    # PIL terms, that's 180 → 360 clockwise, equivalent to
    # 180 → 0 counter-clockwise (same arc).
    draw.arc(bbox, start_deg, end_deg, fill=color, width=int(width))


def _draw_icon(size: int) -> Image.Image:
    """Render the icon at the requested square size.

    Apple's Big Sur app-icon template (and the equivalent
    Windows / Linux conventions, by convergence) centers the
    icon shape inside the canvas with ~10% transparent margin
    on each side, then applies a corner radius of ~22.4% of the
    icon shape's side length. This is what gives macOS Dock
    icons their consistent visual size — every app icon sits at
    the same effective size because they all share the inset.

    Pre-v0.5.117 we filled the canvas edge-to-edge with the
    rounded square, which made the netgen icon render smaller
    than other Dock icons (operator screenshot showed clearly
    visible breathing room around ours while Apple-supplied
    icons go to the Dock-slot edges). Fix: inset the icon
    shape, leaving transparent margin around it.

    The 16 / 32 px sizes skip the inset — at those scales the
    margin would be ≤ 1 px and gives up valuable detail without
    a visible benefit.
    """
    use_inset = size >= 64
    if use_inset:
        margin = max(1, round(size * 0.098))
    else:
        margin = 0
    icon_side = size - 2 * margin

    # All design coordinates are derived from a 360-unit
    # reference (matches resources/icons/netgen.svg). When the
    # icon is inset, `s` shrinks accordingly so the gauge etc.
    # stay proportional inside the smaller icon shape.
    s = icon_side / 360.0

    # Composite onto a fully-transparent canvas — the margin
    # area MUST be transparent (alpha 0), not the bg color, or
    # the Dock-slot rounding/shadow gets cropped wrong.
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Work in a smaller buffer sized to the icon shape itself,
    # then paste onto the transparent canvas at the inset
    # offset. Cleaner than rewriting every draw call with an
    # offset.
    img = Image.new("RGB", (icon_side, icon_side), PALETTE["bg"])
    draw = ImageDraw.Draw(img, "RGBA")

    # Top-half shine — light tint to mimic the Big Sur app-icon
    # convention. Alpha 0.06 over the bg gives a barely-there
    # gradient feel even though we're drawing flat fills.
    shine_h = int(200 * s)
    shine = Image.new("RGBA", (size, shine_h),
                      PALETTE["shine"] + (15,))
    img.paste(shine, (0, 0), shine)

    cx, cy = 180 * s, 250 * s
    r = 120 * s
    arc_w = max(2, 14 * s)
    track_w = max(2, 22 * s)
    tick_w = max(1, 3 * s)
    needle_w = max(2, 6 * s)

    # Gauge track — the unlit bed under the colored segments.
    _arc(draw, cx, cy, r, 180, 360, PALETTE["arc_track"], track_w)

    # Three colored segments. Angles match the SVG path's
    # midpoints (computed once: 180° → ~138° gray, 138° → ~42°
    # blue, 42° → 0° purple). PIL angles are clockwise from
    # 3 o'clock — gauge upper half = PIL 180 → 360. So:
    #   gray   = SVG 180..138 → PIL 180..222
    #   blue   = SVG 138..42  → PIL 222..318
    #   purple = SVG 42..0    → PIL 318..360
    _arc(draw, cx, cy, r, 180, 222, PALETTE["arc_gray"], arc_w)
    _arc(draw, cx, cy, r, 222, 318, PALETTE["arc_blue"], arc_w)
    _arc(draw, cx, cy, r, 318, 360, PALETTE["arc_purple"], arc_w)

    # Tick marks at SVG angles 180, 135, 90, 45, 0.
    for theta_deg in (180, 135, 90, 45, 0):
        th = math.radians(theta_deg)
        outer = (cx + r * math.cos(th), cy - r * math.sin(th))
        inner = (cx + (r - 18 * s) * math.cos(th),
                 cy - (r - 18 * s) * math.sin(th))
        draw.line([outer, inner], fill=PALETTE["tick"], width=int(tick_w))

    # Needle from pivot to a point on the ~30° arc (high end).
    needle_angle = math.radians(30)
    needle_tip = (cx + (r - 10 * s) * math.cos(needle_angle),
                  cy - (r - 10 * s) * math.sin(needle_angle))
    draw.line([(cx, cy), needle_tip],
              fill=PALETTE["needle"], width=int(needle_w))

    # Pivot — outer light ring + dark center for the "tactile"
    # look. At very small sizes (16, 32) the dark center is just
    # one pixel; tolerable.
    pivot_outer = max(2, int(11 * s))
    pivot_inner = max(1, int(5 * s))
    draw.ellipse(
        (cx - pivot_outer, cy - pivot_outer,
         cx + pivot_outer, cy + pivot_outer),
        fill=PALETTE["needle"],
    )
    draw.ellipse(
        (cx - pivot_inner, cy - pivot_inner,
         cx + pivot_inner, cy + pivot_inner),
        fill=PALETTE["pivot_dark"],
    )

    # Packet trail across the top. Drop at sizes ≤ 64 px — the
    # packets become single pixels and add noise rather than info.
    if size > 64:
        for x, y, w, h, alpha in (
            (118, 80, 44, 12, 255),
            (170, 82, 34, 10, 191),
            (212, 84, 22, 8,  127),
        ):
            box = (x * s, y * s, (x + w) * s, (y + h) * s)
            r_packet = max(1, 3 * s)
            draw.rounded_rectangle(
                box, radius=r_packet,
                fill=PALETTE["packet"] + (alpha,),
            )
        # Trailing dot — barely visible, just hints at motion
        # past the last packet.
        dot_r = max(1, 2.5 * s)
        draw.ellipse(
            (244 * s - dot_r, 88 * s - dot_r,
             244 * s + dot_r, 88 * s + dot_r),
            fill=PALETTE["packet"] + (89,),
        )

    # Clip the icon-shape buffer to the rounded-square
    # silhouette. Apple's Big Sur squircle ratio is ~22.37% of
    # the icon shape's side length; the pre-v0.5.117 80/360
    # ratio (22.2%) was already close, retained here for
    # consistency with the source SVG.
    radius = max(2, int(round(icon_side * 0.224)))
    icon_layer = Image.new("RGBA", (icon_side, icon_side), (0, 0, 0, 0))
    icon_layer.paste(img, (0, 0), _rounded_rect_mask(
        (icon_side, icon_side), radius,
    ))

    # Composite onto the transparent canvas at the inset.
    canvas.paste(icon_layer, (margin, margin), icon_layer)
    return canvas


SIZES = (16, 32, 64, 128, 256, 512, 1024)


def _write_pngs() -> dict[int, Path]:
    """Write a netgen-<N>.png for each target size; return the
    paths keyed by size so the downstream .icns / .ico builders
    can find them."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[int, Path] = {}
    for size in SIZES:
        out = ICONS_DIR / f"netgen-{size}.png"
        _draw_icon(size).save(out, "PNG")
        paths[size] = out
        print(f"  → {out.relative_to(REPO_ROOT)}")
    # Canonical netgen.png at 256×256 — the AppImage builder
    # falls through `ostg.png` → `icon.png` → `add.png` in
    # build_appimage.sh and never sees `netgen-256.png`. Drop a
    # plain `netgen.png` at the canonical name so the AppImage
    # path doesn't fall through to the toolbar add.png.
    canonical = ICONS_DIR / "netgen.png"
    shutil.copyfile(paths[256], canonical)
    print(f"  → {canonical.relative_to(REPO_ROOT)} (alias of 256)")
    return paths


def _write_icns(paths: dict[int, Path]) -> None:
    """Build the macOS .icns container. iconutil is macOS-only;
    skip with a warning elsewhere (Linux CI runners will need to
    consume a pre-built .icns from the repo — that's why these
    artifacts get committed)."""
    if sys.platform != "darwin":
        print("  → skipped: iconutil only exists on macOS. The "
              "checked-in netgen.icns is authoritative on other "
              "hosts.")
        return
    iconset = ICONS_DIR / "netgen.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    # iconutil names: <name>_<size>x<size>{,@2x}.png. The @2x
    # variants are the 2× retina renders — e.g. icon_128x128@2x
    # is a 256×256 image.
    mapping = (
        (16,   "icon_16x16.png"),
        (32,   "icon_16x16@2x.png"),
        (32,   "icon_32x32.png"),
        (64,   "icon_32x32@2x.png"),
        (128,  "icon_128x128.png"),
        (256,  "icon_128x128@2x.png"),
        (256,  "icon_256x256.png"),
        (512,  "icon_256x256@2x.png"),
        (512,  "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    )
    for size, name in mapping:
        shutil.copyfile(paths[size], iconset / name)
    icns_out = ICONS_DIR / "netgen.icns"
    subprocess.run(
        ["/usr/bin/iconutil", "-c", "icns",
         str(iconset), "-o", str(icns_out)],
        check=True,
    )
    print(f"  → {icns_out.relative_to(REPO_ROOT)}")
    # Clean up the .iconset working dir — only the .icns ships.
    shutil.rmtree(iconset)


def _write_ico(paths: dict[int, Path]) -> None:
    """Build the Windows .ico container via Pillow. Pillow's ICO
    writer accepts a list of sizes via the `sizes=` kwarg; we
    pass the same six sizes Microsoft's Icon Editor uses."""
    ico_out = ICONS_DIR / "netgen.ico"
    # Open the largest source PNG and let Pillow downscale to
    # each size internally — saves us from hand-managing a list
    # of PIL images and lets Pillow pick the best filter per
    # size.
    base = Image.open(paths[256])
    base.save(
        ico_out, format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48),
               (64, 64), (128, 128), (256, 256)],
    )
    print(f"  → {ico_out.relative_to(REPO_ROOT)}")


def main() -> int:
    print("Rendering PNGs...")
    paths = _write_pngs()
    print("Bundling .icns (macOS)...")
    _write_icns(paths)
    print("Bundling .ico (Windows)...")
    _write_ico(paths)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
