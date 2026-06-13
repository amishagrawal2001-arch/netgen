"""v0.5.116: app icon assets are present + every build pipeline
wires to them by canonical name.

Why this matters: the v1 icon is a placeholder that a designer
will replace. We want the file paths stable across that replace
— editing build_dmg.sh / ostg_client.spec / ostg_client_windows.
spec / build_appimage.sh again would be friction. Pin the
canonical paths once, here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


ICONS_DIR = REPO / "resources" / "icons"


def test_canonical_icon_files_exist():
    """The five canonical app-icon artifacts must ship in the
    tree: SVG source, PNG (256), Windows ICO, macOS ICNS,
    1024×1024 PNG for retina. Smaller PNG sizes are convenience;
    the canonical names are what the build scripts grep for."""
    for name in (
        "netgen.svg",
        "netgen.png",
        "netgen.ico",
        "netgen.icns",
        "netgen-1024.png",
    ):
        assert (ICONS_DIR / name).is_file(), (
            f"resources/icons/{name} is missing — re-run "
            f"scripts/generate_app_icon.py"
        )


def test_png_sizes_present_for_iconset():
    """The generator writes one PNG per macOS iconset slot. If
    any are missing, iconutil will fail at build time and the
    .icns will be stale. Pin the size list explicitly."""
    for size in (16, 32, 64, 128, 256, 512, 1024):
        assert (ICONS_DIR / f"netgen-{size}.png").is_file(), (
            f"netgen-{size}.png missing — generator didn't run "
            f"or the SIZES tuple drifted"
        )


def test_macos_spec_points_at_netgen_icns():
    """ostg_client.spec is the macOS .app builder. It must point
    icon= at our .icns file, not the toolbar add.png placeholder
    that existed pre-v0.5.116."""
    src = (REPO / "ostg_client.spec").read_text(encoding="utf-8")
    assert "resources/icons/netgen.icns" in src, (
        "ostg_client.spec must reference resources/icons/"
        "netgen.icns for the macOS .app icon"
    )
    assert "resources/icons/add.png" not in src, (
        "ostg_client.spec must not point at the v0.5.115 "
        "placeholder (add.png) — that's a toolbar icon, not an "
        "app icon"
    )


def test_windows_spec_prefers_netgen_ico():
    """ostg_client_windows.spec walks a lookup list — netgen.ico
    must come first so a clean build picks it (older fallbacks
    are retained for partial-rebuild safety only)."""
    src = (REPO / "ostg_client_windows.spec").read_text(
        encoding="utf-8"
    )
    # Find the lookup tuple and confirm netgen.ico is first.
    assert "resources/icons/netgen.ico" in src, (
        "Windows spec must list netgen.ico in its lookup tuple"
    )
    # The first entry in the lookup tuple should be netgen.ico.
    # Find the position of each candidate and verify ordering.
    pos_netgen = src.find("resources/icons/netgen.ico")
    pos_ostg = src.find("resources/icons/ostg.ico")
    pos_add = src.find("resources/icons/add.png")
    assert pos_netgen < pos_ostg, (
        "netgen.ico must come before ostg.ico in the Windows "
        "spec lookup — ordering matters"
    )
    assert pos_netgen < pos_add, (
        "netgen.ico must come before add.png in the Windows "
        "spec lookup — add.png is a last-resort fallback"
    )


def test_appimage_script_prefers_netgen_png():
    """build_appimage.sh walks the same fallback list. netgen.png
    must come first."""
    src = (REPO / "build_appimage.sh").read_text(encoding="utf-8")
    assert "resources/icons/netgen.png" in src, (
        "build_appimage.sh must reference netgen.png"
    )
    pos_netgen = src.find("resources/icons/netgen.png")
    pos_add = src.find("resources/icons/add.png")
    assert pos_netgen < pos_add, (
        "netgen.png must come before add.png in the AppImage "
        "icon lookup — ordering is what makes the right icon win"
    )


def test_client_entry_sets_window_icon():
    """run_tgen_client.py is the launcher. It must call
    setWindowIcon with the resources/icons/netgen.png path so the
    PyQt title-bar / alt-tab / taskbar icon (separate from the
    Dock icon, which the .icns provides) renders correctly.
    Without this, the title bar shows PyQt's default generic
    computer glyph."""
    src = (REPO / "run_tgen_client.py").read_text(encoding="utf-8")
    assert "setWindowIcon" in src, (
        "run_tgen_client.py must call setWindowIcon — without "
        "it the PyQt title bar shows a generic icon"
    )
    assert "netgen.png" in src, (
        "run_tgen_client.py must reference netgen.png for the "
        "window icon source"
    )


def test_large_pngs_have_transparent_margin():
    """v0.5.117 fix: at 64+ px sizes, the icon shape must be
    inset inside the canvas with a transparent margin so it
    matches the visual size of Apple-supplied Dock icons. Pre-
    fix the rounded square filled the full canvas → ours
    rendered visibly smaller than other apps' Dock icons
    because we lacked the breathing-room margin Apple applies.

    Verify by checking corner pixel alpha — should be 0 at
    sizes ≥ 64."""
    from PIL import Image
    for size in (64, 128, 256, 512, 1024):
        img = Image.open(ICONS_DIR / f"netgen-{size}.png").convert("RGBA")
        corner_alpha = img.getpixel((0, 0))[3]
        center_alpha = img.getpixel((size // 2, size // 2))[3]
        assert corner_alpha == 0, (
            f"netgen-{size}.png corner alpha must be 0 (margin "
            f"required by Apple HIG); got {corner_alpha}"
        )
        assert center_alpha == 255, (
            f"netgen-{size}.png center alpha must be 255 (icon "
            f"shape is opaque); got {center_alpha}"
        )


def test_small_pngs_skip_inset():
    """At 16 / 32 px the margin would be ≤ 1 px — not worth
    sacrificing the visible icon area. The generator skips the
    inset at those sizes, so corner alpha at 16 should be
    near-opaque (the rounded-square's corner curve still
    creates a tiny transparent edge, but center+corner are both
    inside the rounded square)."""
    from PIL import Image
    # At 16/32 the rounded-square's corner curve produces an
    # alpha gradient near (0,0). Sample a pixel a few pixels in
    # from the corner — that should be opaque if the inset was
    # skipped. (At v0.5.116 with no inset, the same pixel was
    # opaque; at v0.5.117 with inset > 0, it would be
    # transparent.)
    for size, probe in ((16, 4), (32, 8)):
        img = Image.open(ICONS_DIR / f"netgen-{size}.png").convert("RGBA")
        a = img.getpixel((probe, probe))[3]
        assert a >= 200, (
            f"netgen-{size}.png probe at ({probe},{probe}) "
            f"should be (nearly) opaque — inset must be skipped "
            f"at small sizes; got alpha {a}"
        )


def test_generator_script_present_and_runnable():
    """The PIL-based generator script lives under scripts/. We
    don't run it from tests (would create files in resources/),
    but pin that it exists with a recognisable shape."""
    gen = REPO / "scripts" / "generate_app_icon.py"
    assert gen.is_file()
    src = gen.read_text(encoding="utf-8")
    # The script must produce all sizes the iconset uses.
    for size in (16, 32, 64, 128, 256, 512, 1024):
        assert str(size) in src, (
            f"generator script must reference size {size}"
        )
    # And must write the canonical netgen.png alias.
    assert "netgen.png" in src
