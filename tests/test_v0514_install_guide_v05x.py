"""v0.5.14: Install Guide must reflect the v0.5.x tarball reality.

Doc-only release. The in-app Install Guide was frozen at the v0.4.x
flow (legacy install_ostg_complete.py) through the entire v0.5.x
cascade, even though that cascade closed 7 distinct install-pipeline
bugs and introduced compat symlinks, bundled venv, etc.

v0.5.14 added three sections to _INSTALL_GUIDE_HTML:

  - §0  v0.5.x install architecture (★ current)
  - §8  rewritten with current v0.5.x paths + legacy table beside
  - §11 v0.5.x troubleshooting recipes (6 SSH one-liners)

These tests pin the contracts so a future refactor doesn't silently
revert the documentation to the v0.4.x-only state. Operators reading
the guide should always find guidance that matches the install they
actually ran.
"""
from __future__ import annotations

import re

from widgets.stream_dialog import _INSTALL_GUIDE_HTML


def test_section_0_documents_tarball_architecture():
    """§0 must orient operators to the v0.5.x tarball model BEFORE
    diving into the dialog walkthrough. Without it, a fresh operator
    reads §1 ('In-GUI installer NEW in 0.2.6') and has no idea the
    underlying install is now tarball-based, not pip-based."""
    src = _INSTALL_GUIDE_HTML
    assert "<h2>0. v0.5.x install architecture" in src, (
        "Install Guide §0 missing — operators land in §1 without "
        "context that the v0.5.x install model differs from "
        "v0.4.x's pip-based flow."
    )
    # Must specifically mention key architecture decisions.
    for term in (
        "python-build-standalone",      # bundled CPython origin
        "/opt/netgen-server",           # actual install root
        "netgen-frr:latest",            # FRR Docker image name
        "ostg-frr:latest",              # legacy dual-tag
        "/opt/OSTG",                    # compat symlink target
        "/opt/netgen",                  # compat symlink target
        "tarball",                      # architecture descriptor
        "bundled venv",                 # core property
    ):
        assert term in src, f"§0 missing reference to {term!r}"


def test_section_0_documents_what_is_NOT_installed():
    """A v0.5.x install touches dramatically less of the system than
    v0.4.x. The guide must call this out — it's the headline benefit
    and changes what operators need to plan for (firewall rules,
    user accounts, shell rc, etc.)."""
    src = _INSTALL_GUIDE_HTML
    assert "does NOT touch" in src or "does NOT install" in src or \
           "NOT touch" in src, (
        "§0 doesn't enumerate what v0.5.x does NOT touch. Operators "
        "planning a deploy need to know whether they need firewall "
        "rules, user account creation, shell rc edits, etc."
    )
    # Specifically:
    for term in ("No apt packages", "No firewall", "No user accounts"):
        assert term in src, f"§0 missing 'what is NOT touched' bullet: {term!r}"


def test_section_0_lists_seven_ci_contracts():
    """§0 ends with a table of the 7 install-pipeline contracts CI
    now validates (the v0.5.6→v0.5.13 cascade). Operators reading
    this know the install is hardened against specific failure
    classes — and which release added each gate."""
    src = _INSTALL_GUIDE_HTML
    for release in (
        "v0.5.6", "v0.5.7", "v0.5.8", "v0.5.9",
        "v0.5.10", "v0.5.11", "v0.5.12", "v0.5.13",
    ):
        assert release in src, (
            f"§0 contracts table missing {release} row — the cascade "
            f"documentation is incomplete."
        )


def test_section_8_replaced_stale_python313_paths():
    """The old §8 had `/usr/local/lib/python3.13/dist-packages/`
    paths that haven't been accurate since v0.5.0. v0.5.14 replaces
    them with current netgen-venv paths."""
    src = _INSTALL_GUIDE_HTML
    # Stale path must NOT appear (would mean revert).
    # (We exclude python3.10 because that IS the bundled version.)
    assert "/usr/local/lib/python3.13/dist-packages" not in src, (
        "§8 still references stale python3.13/dist-packages path. "
        "v0.5.x uses /opt/netgen-server/netgen-venv/lib/python3.10/"
        "site-packages/ — pin the current location."
    )
    # Current path must appear.
    assert "/opt/netgen-server/netgen-venv/lib/python3.10/" in src or \
           "netgen-venv/lib/python3.10/site-packages" in src, (
        "§8 doesn't reference the current venv site-packages path."
    )


def test_section_8_documents_both_v05x_and_legacy_paths():
    """§8 must show v0.5.x layout AND legacy v0.4.x layout. Operators
    on un-migrated hosts need both — and the diff between them
    explains WHY the compat symlinks exist."""
    src = _INSTALL_GUIDE_HTML
    # Header for the new layout section.
    assert "v0.5.0+ tarball install" in src, (
        "§8 doesn't header the v0.5.x layout table."
    )
    # Header for legacy.
    assert "legacy" in src.lower(), (
        "§8 doesn't include a legacy v0.4.x paths table."
    )
    # Legacy ostg-server.service mention is critical (the most
    # common upgrade blocker on legacy hosts).
    assert "ostg-server.service" in src, (
        "§8 doesn't mention legacy ostg-server.service — that's the "
        "#1 blocker on v0.4.x → v0.5.x migrations (holds :5050)."
    )


def test_section_11_troubleshooting_covers_v05x_cascade():
    """§11 must document the six recipes operators might need for
    each of the v0.5.6→v0.5.13 failure modes."""
    src = _INSTALL_GUIDE_HTML
    assert "<h2>11. v0.5.x troubleshooting recipes" in src, (
        "§11 troubleshooting recipes section missing."
    )
    # Each subsection's anchor:
    for sub in (
        "Server not responding on /api/health",   # 11a — port/legacy
        "203/EXEC",                                # 11b — shebang
        "in the future",                           # 11c — clock skew
        "externally-managed-environment",          # 11d — PEP 668
        "Compat warnings",                         # 11e — legacy dirs
        "frr.conf.template",                       # 11f — FRR context
    ):
        assert sub in src, (
            f"§11 missing recipe for {sub!r} — operators on "
            f"intermediate tarball versions can still hit this."
        )


def test_section_11_recipes_use_idempotent_commands():
    """The troubleshooting commands must be safe to run more than
    once (a worried operator will paste them twice). Specifically:
    no `rm -rf /opt/netgen-server` without recreating; no
    `systemctl restart` without verification."""
    src = _INSTALL_GUIDE_HTML
    # Recipe for clock skew must show a fix-the-clock command,
    # not just an extraction hack.
    assert "timedatectl set-ntp true" in src, (
        "§11c clock-skew recipe doesn't try fixing NTP first. The "
        "manual --warning=no-timestamp extract is fallback; right "
        "answer is fix-the-clock."
    )
    # Recipe for legacy svc must use --now (stop + disable atomically)
    # not just disable (which leaves it running).
    assert "disable --now ostg-server" in src, (
        "§11a recipe uses `disable` without `--now` — that leaves "
        "the legacy svc running until reboot."
    )


def test_install_guide_toc_grew_to_at_least_25():
    """The existing test_install_guide_toc_has_real_entries asserts
    ≥20; v0.5.14 added a §0 + multiple subsections. The TOC should
    now have substantially more entries."""
    from widgets.stream_dialog import _extract_toc
    items = _extract_toc(_INSTALL_GUIDE_HTML)
    assert len(items) >= 25, (
        f"Install Guide TOC has only {len(items)} entries; v0.5.14 "
        f"added §0 + §11 with multiple subsections, expected ≥25."
    )
