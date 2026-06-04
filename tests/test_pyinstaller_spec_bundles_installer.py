"""Regression test pinning that install_ostg_complete.py ships in
the PyInstaller-built client .dmg / .exe.

Operator-visible bug this guards against: a new user downloads the
.dmg (macOS) or .exe (Windows), installs it, opens Help → Install /
Upgrade Server → Fresh Install — and finds the Installer field
empty. The dialog's ``_guess_installer_path()`` looks for the file
at ``<widgets>/../install_ostg_complete.py``, which resolves to
``<bundle>/install_ostg_complete.py`` inside a frozen PyInstaller
app. If the .spec file doesn't include the script in ``datas=``,
PyInstaller never copies it into the bundle, and the guess returns
empty.

Pre-v0.3.16 datas block:
    datas=[
        ('resources', 'resources'),
        ('widgets', 'widgets'),
        ('traffic_client', 'traffic_client'),
        ('utils', 'utils'),
        ('server', 'server'),
    ],

Missing entry: ('install_ostg_complete.py', '.'). Add it so
PyInstaller drops the file at the bundle root.

This test parses each .spec file (they're plain Python) and checks
the datas literal for the entry. The check applies to BOTH the
macOS and Windows specs because the bug surfaces identically on
both platforms."""
from __future__ import annotations

import ast


_SPECS = (
    "/Users/surajsharma/dev/netgen/ostg_client.spec",
    "/Users/surajsharma/dev/netgen/ostg_client_windows.spec",
)


def _extract_static_tuples(node):
    """Pull (constant, constant) tuples from an ast.List node. Used by
    _datas_tuples_from_spec. Returns [] if node isn't a List."""
    if not isinstance(node, ast.List):
        return []
    out = []
    for elt in node.elts:
        if (isinstance(elt, ast.Tuple)
                and len(elt.elts) == 2
                and all(isinstance(e, ast.Constant) for e in elt.elts)):
            out.append((elt.elts[0].value, elt.elts[1].value))
    return out


def _datas_tuples_from_spec(path: str):
    """Return the static (src, dst) tuples from the spec's
    ``Analysis(...datas=...)`` argument. Handles both:
      datas=[(...), (...)]                        ← bare list
      datas=[(...), (...)] + BUNDLED_WHEEL_DATA   ← list + identifier
    The latter is the v0.3.16+ pattern that folds in a dynamically-
    discovered wheel. We can only extract STATIC tuples (the
    identifier side is a runtime variable), which is fine — the
    static side is what this test cares about."""
    src = open(path).read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # Locate the Analysis(...) call. The spec assigns it to `a`.
        if isinstance(fn, ast.Name) and fn.id == "Analysis":
            for kw in node.keywords:
                if kw.arg != "datas":
                    continue
                v = kw.value
                # Case 1: bare List
                if isinstance(v, ast.List):
                    return _extract_static_tuples(v)
                # Case 2: List + something (e.g. + BUNDLED_WHEEL_DATA)
                if (isinstance(v, ast.BinOp)
                        and isinstance(v.op, ast.Add)):
                    # Try left side first, then right; only one is
                    # typically a literal list in our specs.
                    left = _extract_static_tuples(v.left)
                    right = _extract_static_tuples(v.right)
                    return left + right
    return []


def test_macos_spec_bundles_installer():
    """ostg_client.spec must include ('install_ostg_complete.py', '.')."""
    datas = _datas_tuples_from_spec(_SPECS[0])
    assert ("install_ostg_complete.py", ".") in datas, (
        f"ostg_client.spec datas missing the install_ostg_complete.py "
        f"entry → .dmg won't include it → Fresh Install dialog can't "
        f"auto-populate the Installer field. Current datas: {datas}"
    )


def test_windows_spec_bundles_installer():
    """ostg_client_windows.spec must include the same entry."""
    datas = _datas_tuples_from_spec(_SPECS[1])
    assert ("install_ostg_complete.py", ".") in datas, (
        f"ostg_client_windows.spec datas missing the entry → .exe "
        f"won't include it. Current datas: {datas}"
    )


def test_both_specs_have_same_data_set():
    """The two specs are intentionally a near-mirror — any data file
    bundled on one platform should be bundled on the other. Cross-
    check the sets to catch divergence."""
    macos = set(_datas_tuples_from_spec(_SPECS[0]))
    windows = set(_datas_tuples_from_spec(_SPECS[1]))
    diff_macos_only = macos - windows
    diff_windows_only = windows - macos
    assert not diff_macos_only and not diff_windows_only, (
        f"PyInstaller spec datas drift detected — "
        f"only-in-mac: {diff_macos_only}, "
        f"only-in-win: {diff_windows_only}"
    )


def test_install_ostg_complete_py_exists_at_repo_root():
    """The src side of the datas tuple is a path relative to the
    .spec file (which lives at repo root). If install_ostg_complete.py
    is missing from the source tree, PyInstaller will fail at build
    time, not surface a useful error here — so guard the source
    presence too."""
    import os
    assert os.path.isfile(
        "/Users/surajsharma/dev/netgen/install_ostg_complete.py"
    ), "install_ostg_complete.py missing from repo root"


# ─────────────────────────────────────────── wheel bundling (v0.3.16+)

def _spec_has_bundled_wheel_helper(path: str) -> bool:
    """Return True if the spec file defines a wheel-discovery helper
    AND folds it into the datas list."""
    src = open(path).read()
    return (
        "_discover_bundled_wheel" in src
        and "BUNDLED_WHEEL_DATA" in src
        and "+ BUNDLED_WHEEL_DATA" in src
    )


def test_macos_spec_includes_bundled_wheel_helper():
    """ostg_client.spec must define + use the wheel discovery helper.
    Without this the .dmg's Fresh Install dialog has no wheel to
    auto-populate the Wheel field with."""
    assert _spec_has_bundled_wheel_helper(_SPECS[0]), (
        "ostg_client.spec missing _discover_bundled_wheel + "
        "BUNDLED_WHEEL_DATA — .dmg won't bundle a wheel and new-user "
        "Fresh Install Wheel field will be empty."
    )


def test_windows_spec_includes_bundled_wheel_helper():
    """ostg_client_windows.spec must mirror the macOS bundling logic."""
    assert _spec_has_bundled_wheel_helper(_SPECS[1]), (
        "ostg_client_windows.spec missing wheel discovery — .exe "
        "won't bundle a wheel."
    )


def test_build_dmg_sh_builds_wheel_before_pyinstaller():
    """build_dmg.sh must run `python -m build --wheel` BEFORE invoking
    PyInstaller so the spec's _discover_bundled_wheel() has a wheel
    to find in dist/. Order matters — running PyInstaller first means
    the spec returns empty wheel data and the .dmg ships without it."""
    src = open("/Users/surajsharma/dev/netgen/build_dmg.sh").read()
    # Find positions of the two key commands
    build_wheel_pos = src.find("python -m build --wheel")
    pyinstaller_pos = src.find("pyinstaller -y ostg_client.spec")
    assert build_wheel_pos != -1, (
        "build_dmg.sh missing `python -m build --wheel` — the spec's "
        "_discover_bundled_wheel() will not find anything to bundle."
    )
    assert pyinstaller_pos != -1, \
        "build_dmg.sh missing pyinstaller invocation (sanity check)"
    assert build_wheel_pos < pyinstaller_pos, (
        "build_dmg.sh runs PyInstaller BEFORE the wheel build — "
        "swap the order so the wheel exists in dist/ when the spec "
        "scans for it."
    )


def test_build_dmg_sh_clears_stale_wheels_first():
    """Defensive: build_dmg.sh must clear stale wheels in dist/ before
    building the fresh one. Otherwise a leftover wheel from a prior
    version + a build failure would silently bundle the WRONG version
    (mtime-by-most-recent picks the leftover)."""
    src = open("/Users/surajsharma/dev/netgen/build_dmg.sh").read()
    rm_pos = src.find("rm -f dist/ostg_trafficgen-")
    build_pos = src.find("python -m build --wheel")
    assert rm_pos != -1, (
        "build_dmg.sh must explicitly `rm -f dist/ostg_trafficgen-*.whl` "
        "before building so a failed wheel build can't silently ship "
        "a stale version inside the .dmg."
    )
    assert rm_pos < build_pos, \
        "stale-wheel cleanup must run BEFORE the wheel build"


# ─────────────────────────────────────────── _guess_wheel_path (v0.3.16+)

def test_install_dialog_has_guess_wheel_path_helper():
    """The dialog must have a _guess_wheel_path() that mirrors the
    structure of _guess_installer_path() for the install_ostg_complete.py
    discovery. Wiring this into the QLineEdit default is the LAST link
    of the auto-fill chain — without it the bundled wheel exists but
    the Wheel field stays empty."""
    src = open(
        "/Users/surajsharma/dev/netgen/widgets/install_server_dialog.py"
    ).read()
    assert "def _guess_wheel_path(self)" in src, \
        "_guess_wheel_path() helper missing from install_server_dialog.py"
    # And it must be wired into the QLineEdit default:
    assert "self._guess_wheel_path()" in src, (
        "_guess_wheel_path() is defined but not USED — the Wheel "
        "QLineEdit must be constructed with its return value."
    )


def test_guess_wheel_path_picks_most_recent_across_dirs(monkeypatch, tmp_path):
    """The helper must pick the most-recently-modified wheel ACROSS
    all candidate dirs — dir-by-dir scan was the pre-fix behaviour
    and would surface a stale wheel sitting at repo root ahead of a
    fresh build in dist/."""
    import sys, os, time, importlib
    from unittest.mock import MagicMock
    sys.modules.setdefault("paramiko", MagicMock())

    from widgets.install_server_dialog import InstallServerDialog

    # Build a fake widgets/ dir with two candidate wheels: an OLD
    # one at the repo-root analogue (here/..) and a NEW one in
    # here/../dist/.
    widgets_dir = tmp_path / "widgets"
    widgets_dir.mkdir()
    fake_self_module = type("M", (), {"__file__": str(widgets_dir / "install_server_dialog.py")})()
    # Drop the test wheels
    old_wheel = tmp_path / "ostg_trafficgen-0.1.52-py3-none-any.whl"
    old_wheel.write_text("OLD")
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    new_wheel = dist_dir / "ostg_trafficgen-0.3.16-py3-none-any.whl"
    new_wheel.write_text("NEW")
    # Force mtime ordering: new_wheel must be more recent
    os.utime(str(old_wheel), (time.time() - 3600, time.time() - 3600))
    os.utime(str(new_wheel), (time.time(), time.time()))

    # Patch __file__ on the bound method by binding to a fake self.
    class _Fake:
        pass
    fake = _Fake()
    # Use the real method's __get__ to bind it, but make abspath of
    # __file__ resolve to our tmp widgets/.
    import widgets.install_server_dialog as mod
    real_file = mod.__file__
    monkeypatch.setattr(mod, "__file__",
                        str(widgets_dir / "install_server_dialog.py"))
    try:
        # Rebind the method after the file patch so os.path.dirname(
        # os.path.abspath(__file__)) reads the patched value.
        result = InstallServerDialog._guess_wheel_path(fake)
    finally:
        monkeypatch.setattr(mod, "__file__", real_file)

    assert os.path.basename(result) == "ostg_trafficgen-0.3.16-py3-none-any.whl", (
        f"Expected NEW wheel (mtime-recent), got: {result}"
    )
