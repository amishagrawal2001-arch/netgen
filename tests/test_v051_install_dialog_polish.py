"""v0.5.1 — full polish of the Install/Upgrade Server dialog
for the v0.5.0 tarball flow.

After v0.5.0 shipped, five gaps remained in the dialog:

  #1 (BUG)  — Upgrade tab's pip3 install ran against system pip,
              installing the wheel to /usr/lib/python3.X/dist-
              packages. On a v0.5.0 tarball-installed host the
              systemd unit's ExecStart points at the BUNDLED venv,
              so the system-pip upgrade landed in the wrong Python
              and the server kept running the old code SILENTLY.
  #2 (UX)   — No visual cue for which install path will run after
              the operator picks a file (wheel vs tarball).
  #3 (UX)   — --no-dpdk / --skip-dpdk-build flags don't apply to
              bin/netgen-install but were still passed through,
              silently ignored.
  #4 (UX)   — install_ostg_complete.py field stayed enabled for
              tarball installs even though it's irrelevant.
  #5 (UX)   — File picker defaulted to .whl filter even on the
              Fresh Install tab where tarball is now the
              recommended artifact.

This test file pins all five fixes in source so a refactor that
rolls back any of them surfaces here, not at the next operator
who upgrades a v0.5.0 host and watches the version stay the same.
"""
from __future__ import annotations

import re
from pathlib import Path


_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "install_server_dialog.py"
)


# ─────────────────────────── #1 — Upgrade dispatch fix ────────────────


def test_upgrade_worker_dispatches_via_netgen_upgrade_on_v050_hosts():
    """SshUpgradeWorker's shell payload must check for
    /opt/netgen-server/bin/netgen-upgrade and dispatch through it
    when present. On a v0.5.0 host, system pip3 lands the wheel in
    the wrong Python — silent upgrade failure."""
    src = _DIALOG.read_text()
    # Find the cmd_payload string inside SshUpgradeWorker
    m = re.search(
        r"cmd_payload\s*=\s*\([\s\S]+?\)",
        src,
    )
    assert m, "cmd_payload assignment not found"
    payload = m.group(0)
    # Must shell-test for the tarball-install marker
    assert "/opt/netgen-server/bin/netgen-upgrade" in payload, (
        "SshUpgradeWorker doesn't dispatch through netgen-upgrade. "
        "On a v0.5.0 tarball host, the system-pip path silently "
        "lands the wheel in the wrong Python and the server keeps "
        "running the OLD code. This was the #1 critical gap "
        "after v0.5.0 shipped."
    )
    # Both legacy AND v0.5.0 branches must coexist — the legacy path
    # still applies to v0.4.x hosts that haven't migrated.
    assert "pip3 install --upgrade --force-reinstall --no-deps" in payload, (
        "SshUpgradeWorker dropped the legacy pip3 path. v0.4.x hosts "
        "would now fail to upgrade."
    )
    # And the test-for-executable form so the shell test actually
    # gates correctly.
    assert re.search(
        r"if\s+\[\s*-x\s+/opt/netgen-server/bin/netgen-upgrade\s*\]",
        payload,
    ), (
        "Dispatch isn't a `test -x` shell condition — could fire on a "
        "host where the script exists but isn't executable, or vice versa."
    )


# ─────────────────────────── #2 — install-mode indicator ──────────────


def test_install_mode_indicator_method_exists():
    """The label-refresh method must exist + be wired to the
    file-path field's textChanged signal so the indicator updates
    live."""
    src = _DIALOG.read_text()
    assert "def _refresh_install_mode_indicator(self)" in src, (
        "_refresh_install_mode_indicator method missing. Without "
        "it the operator has no visual confirmation of which "
        "install path will run."
    )
    # Must be wired to textChanged
    assert "self.ssh_wheel.textChanged.connect(self._refresh_install_mode_indicator)" in src, (
        "Install-mode indicator isn't wired to ssh_wheel.textChanged "
        "— the label would stay frozen on whatever it was at dialog "
        "construction."
    )
    # Must distinguish at least the two cases (tarball vs wheel) by
    # different label content.
    method_start = src.find("def _refresh_install_mode_indicator(self)")
    method_end = src.find("def ", method_start + 1)
    method = src[method_start:method_end]
    assert "tarball install" in method.lower(), (
        "Mode indicator doesn't mention 'tarball install' for "
        ".tar.gz files"
    )
    assert "wheel install" in method.lower(), (
        "Mode indicator doesn't mention 'wheel install' for "
        ".whl files"
    )


# ─────────────────────────── #3 — DPDK flags stripped ─────────────────


def test_dpdk_flags_stripped_for_tarball_install():
    """--no-dpdk and --skip-dpdk-build are install_ostg_complete.py-
    specific. bin/netgen-install doesn't recognise them. The click
    handler must NOT pass them through in tarball mode, and must
    log a 'ignored' note so the operator knows their checkbox
    didn't carry forward."""
    src = _DIALOG.read_text()
    # The flags loop must branch on is_tarball_mode
    assert "is_tarball_mode" in src, (
        "Click handler doesn't compute is_tarball_mode — no way "
        "to gate flag-stripping logic on install path."
    )
    # And the strip-with-warning must be explicit
    assert "ignored on tarball install" in src, (
        "DPDK flags are silently dropped instead of explicitly "
        "logged as 'ignored on tarball install'. Operator wouldn't "
        "know their checkbox state didn't carry forward."
    )


# ─────────────────────────── #4 — installer field disabled ────────────


def test_installer_field_disabled_for_tarball_mode():
    """When the operator picks a .tar.gz, the install_ostg_
    complete.py field becomes irrelevant. The indicator method
    must disable it so the operator doesn't waste time finding
    an installer path that won't be used."""
    src = _DIALOG.read_text()
    method_start = src.find("def _refresh_install_mode_indicator(self)")
    method_end = src.find("def ", method_start + 1)
    method = src[method_start:method_end]
    assert "self.ssh_installer.setEnabled(not is_tarball)" in method, (
        "_refresh_install_mode_indicator doesn't disable "
        "ssh_installer when tarball is selected"
    )
    # And the DPDK flag widgets — same logic, same UX win.
    assert "flag_no_dpdk" in method and "flag_skip_dpdk_build" in method, (
        "_refresh_install_mode_indicator doesn't grey out the DPDK "
        "flag checkboxes when in tarball mode — operators could "
        "tick them expecting them to take effect"
    )


# ─────────────────────────── #5 — default file filter ─────────────────


def test_browse_wheel_accepts_prefer_tarball_kwarg():
    """The Fresh Install tab calls _browse_wheel with
    prefer_tarball=True so the file dialog defaults to the
    server-tarball filter (tarball is the recommended fresh-install
    artifact). Routine wheel upgrades from the Upgrade tab keep
    the .whl filter as default."""
    src = _DIALOG.read_text()
    # The method signature must accept prefer_tarball.
    assert "def _browse_wheel(self, line_edit: QLineEdit" in src
    assert "prefer_tarball: bool = False" in src, (
        "_browse_wheel doesn't accept prefer_tarball kwarg — file "
        "dialog can't default-select the tarball filter on Fresh "
        "Install."
    )
    # And Fresh Install's wheel-row must pass prefer_tarball=True.
    assert re.search(
        r"_browse_wheel\(self\.ssh_wheel,\s*prefer_tarball=True\)",
        src,
    ), (
        "Fresh Install tab doesn't pass prefer_tarball=True. The "
        "tarball is the recommended v0.5.0 fresh-install artifact; "
        "defaulting to .whl makes the operator hunt for the right "
        "filter."
    )


# ─────────────────────────── meta: row label updated ──────────────────


def test_fresh_install_row_label_mentions_tarball():
    """The form-row label changed from 'Wheel:' to 'Wheel /
    tarball:' so the operator's first glance tells them both
    options are valid. Without this update, operators on v0.5.0+
    would assume only .whl works."""
    src = _DIALOG.read_text()
    assert 'form.addRow("Wheel / tarball:", wheel_row)' in src, (
        "Fresh Install wheel-row label still reads 'Wheel:' "
        "exclusively — operators wouldn't know they can pick a "
        ".tar.gz."
    )
