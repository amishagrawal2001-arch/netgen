# utils/qicon_loader.py
'''from importlib.resources import files, as_file
from PyQt5.QtGui import QIcon, QPixmap

def qicon(package: str, relpath: str) -> QIcon:
    """
    Load a QIcon from data bundled inside a package.
    Example: qicon("resources", "icons/start.png")
    """
    res = files(package).joinpath(relpath)
    with as_file(res) as on_disk:
        return QIcon(str(on_disk))

def qpixmap(package: str, relpath: str) -> QPixmap:
    """
    Load a QPixmap from data bundled inside a package.
    Example: qpixmap("resources", "icons/start.png")
    """
    res = files(package).joinpath(relpath)
    with as_file(res) as on_disk:
        return QPixmap(str(on_disk))

# Optional convenience for your common case:
def r_icon(relpath: str) -> QIcon:
    """Shorthand for qicon('resources', <relpath>)"""
    return qicon("resources", relpath)'''

# utils/qicon_loader.py
from functools import lru_cache
from typing import Optional
from PyQt5.QtGui import QIcon
from importlib import resources as ir
from pathlib import Path
import os

PKG_DEFAULT = "resources"  # your package that contains icons/

def _resource_path(package: str, relpath: str) -> Optional[str]:
    """
    Return a *real* filesystem path to a resource inside a package.
    Works even if the dist is zipped (uses as_file to materialize).
    """
    try:
        res = ir.files(package).joinpath(relpath)
        if not res.exists():
            return None
        # as_file gives a real path even for zipped resources
        with ir.as_file(res) as fp:
            return str(fp)
    except Exception:
        return None

@lru_cache(maxsize=256)
def r_icon(relpath: str, package: str = PKG_DEFAULT) -> Optional[str]:
    """
    Get a real path to an icon resource inside the package (e.g. 'icons/start.png').
    Falls back to repo-relative path for dev runs.
    """
    # 1) Try from installed package
    p = _resource_path(package, relpath)
    if p:
        return p

    # 2) Fallback to dev tree: utils/../resources/<relpath>
    #    This covers running from source checkout.
    here = Path(__file__).resolve()
    dev_path = here.parent.parent / "resources" / relpath
    if dev_path.exists():
        return str(dev_path)

    return None

@lru_cache(maxsize=256)
def qicon(package: str, relpath: str) -> QIcon:
    """
    Build a QIcon from package resource path (e.g. qicon('resources', 'icons/stop.png')).
    Returns null QIcon if not found.
    """
    p = r_icon(relpath, package)
    return QIcon(p) if p else QIcon()


# Inline-painted status dot.
# The PNG sprite (resources/icons/red_dot.png at 256×256) was being scaled
# down to 12-14px by Qt and rendering with visible aliasing, so on the
# stream table's Status column it read as a square block. Painting the
# circle directly with QPainter sidesteps PNG scaling entirely — the dot
# is crisp at every size and platform DPI.
_STATUS_DOT_COLORS = {
    "red":    "#dc2626",   # stopped
    "green":  "#10b981",   # running
    "blue":   "#2563eb",   # tracking RX
    "yellow": "#f59e0b",   # pending / in-flight
    "gray":   "#9ca3af",   # disabled
}


@lru_cache(maxsize=64)
def status_dot_icon(color_name: str, size: int = 14) -> QIcon:
    """Return a QIcon containing a centered filled circle of the given
    color name. Renders inline via QPainter so the dot stays circular
    and antialiased at any size.

    Cached per (color, size) — typical UI uses 2-3 unique combinations
    per session.
    """
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor, QPainter, QPixmap

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.Antialiasing, True)
        hex_color = _STATUS_DOT_COLORS.get(color_name, "#6b7280")
        p.setBrush(QColor(hex_color))
        p.setPen(Qt.NoPen)
        # Inset by 1px so the circle's anti-aliased edge isn't clipped
        # by the pixmap boundary on high-DPI displays.
        p.drawEllipse(1, 1, size - 2, size - 2)
    finally:
        p.end()
    return QIcon(pm)
