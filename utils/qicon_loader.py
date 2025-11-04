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
