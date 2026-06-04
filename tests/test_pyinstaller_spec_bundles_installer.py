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


def _datas_tuples_from_spec(path: str):
    """Return the list of (src, dst) tuples from the spec's
    ``Analysis(...datas=[...]...)``. Parses the file with ast so a
    formatting variation (extra blank lines, comments) doesn't break
    the check."""
    src = open(path).read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # Locate the Analysis(...) call. The spec assigns it to `a`.
        if isinstance(fn, ast.Name) and fn.id == "Analysis":
            for kw in node.keywords:
                if kw.arg == "datas" and isinstance(kw.value, ast.List):
                    out = []
                    for elt in kw.value.elts:
                        if (isinstance(elt, ast.Tuple)
                                and len(elt.elts) == 2
                                and all(isinstance(e, ast.Constant) for e in elt.elts)):
                            out.append((elt.elts[0].value, elt.elts[1].value))
                    return out
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
