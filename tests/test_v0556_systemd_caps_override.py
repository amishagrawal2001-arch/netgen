"""v0.5.56 — netgen-server.service gets the missing capabilities
via a drop-in override that self-heals on startup.

Audit finding H8. Pre-fix the systemd unit only held:
    CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN

CAP_SYS_ADMIN (mount, in-process sysfs writes), CAP_SYS_MODULE
(modprobe), CAP_SYS_BOOT (reboot), CAP_SETUID + CAP_DAC_OVERRIDE
(sudo's setresuid + read /etc/sudoers) were all dropped. Each
missing cap drove its own workaround:

  v0.5.31 — apt sandbox disable for setgroups EPERM
  v0.5.33 — systemd-run wrap for apt cache chmod EPERM
  v0.5.44 — systemd-run wrap for modprobe init_module EPERM
  v0.5.50 — skip sudo when geteuid()==0 because sudo's
            own setresuid EPERMs without CAP_SETUID

v0.5.56 ships:

  1. `scripts/tarball/netgen-install` writes the EXPANDED cap
     set in the main unit for fresh installs.

  2. `_ensure_netgen_caps_override_deployed()` self-heals
     `/etc/systemd/system/netgen-server.service.d/netgen-caps.conf`
     on existing installs (no tarball reinstall required).

  3. Drop-in pattern (not main-unit edit) so:
       - Tarball reinstalls don't conflict
       - Operator can `rm` the drop-in to revert
       - Operator-edited main unit isn't touched

Catch-22 (same shape as v0.5.49): the v0.5.55→v0.5.56 upgrade
uses the OLD caps. New caps apply on the NEXT restart after
self-heal writes the drop-in.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"
_INSTALL = _REPO / "scripts" / "tarball" / "netgen-install"


def test_tarball_installer_writes_expanded_caps():
    """`scripts/tarball/netgen-install` writes a main unit with
    the expanded capability set for fresh installs."""
    s = _INSTALL.read_text()
    # Find the AmbientCapabilities line in the unit-template.
    m = re.search(r"AmbientCapabilities=([^\n]+)", s)
    assert m, "AmbientCapabilities line not found in installer"
    caps = m.group(1)
    for required in ("CAP_SYS_ADMIN", "CAP_SYS_MODULE",
                     "CAP_SYS_BOOT", "CAP_SETUID",
                     "CAP_DAC_OVERRIDE"):
        assert required in caps, (
            f"Tarball installer's AmbientCapabilities missing "
            f"{required} — fresh installs would still need the "
            f"workarounds we want to obsolete."
        )


def test_tarball_installer_widens_bounding_set_too():
    """The CapabilityBoundingSet must match — if it stays at
    `CAP_NET_RAW CAP_NET_ADMIN`, ambient caps outside that set
    are silently dropped at exec time."""
    s = _INSTALL.read_text()
    m = re.search(r"CapabilityBoundingSet=([^\n]+)", s)
    assert m, "CapabilityBoundingSet line not found in installer"
    bs = m.group(1)
    for required in ("CAP_SYS_ADMIN", "CAP_SYS_MODULE",
                     "CAP_SYS_BOOT", "CAP_SETUID",
                     "CAP_DAC_OVERRIDE"):
        assert required in bs, (
            f"BoundingSet missing {required} — ambient cap "
            f"would be silently dropped at exec."
        )


def test_caps_override_self_heal_helper_defined():
    src = _SERVER.read_text()
    assert "def _ensure_netgen_caps_override_deployed(" in src, (
        "Self-heal helper missing"
    )


def test_caps_override_uses_dropin_path_not_main_unit():
    """The override must go to
    `/etc/systemd/system/netgen-server.service.d/netgen-caps.conf`,
    NOT the main unit. A drop-in survives tarball reinstall and
    can be removed by the operator without touching the original."""
    src = _SERVER.read_text()
    assert "netgen-server.service.d/netgen-caps.conf" in src, (
        "Override path isn't the drop-in pattern — risks "
        "conflicting with tarball reinstalls."
    )
    # Must NOT write to the main unit directly.
    helper = re.search(
        r"def _ensure_netgen_caps_override_deployed\(\)[\s\S]+?"
        r"(?=\ndef [a-z_])",
        src,
    )
    assert helper
    body = helper.group(0)
    assert "/etc/systemd/system/netgen-server.service\"" not in body, (
        "Helper writes the main unit file — would conflict with "
        "tarball reinstall."
    )


def test_caps_override_content_includes_required_caps():
    """The override content must list all caps the workarounds
    need."""
    src = _SERVER.read_text()
    # The content lives in _NETGEN_CAPS_OVERRIDE_CONTENT constant.
    m = re.search(
        r"_NETGEN_CAPS_OVERRIDE_CONTENT\s*=\s*[\"'\"]{1,3}([\s\S]+?)[\"'\"]{1,3}\n",
        src,
    )
    assert m, "_NETGEN_CAPS_OVERRIDE_CONTENT constant not found"
    content = m.group(1)
    for cap in ("CAP_SYS_ADMIN", "CAP_SYS_MODULE", "CAP_SYS_BOOT",
                "CAP_SETUID", "CAP_DAC_OVERRIDE"):
        assert cap in content, (
            f"Override content missing {cap}"
        )
    # AmbientCapabilities AND CapabilityBoundingSet both set.
    assert "AmbientCapabilities=" in content, (
        "Override missing AmbientCapabilities="
    )
    assert "CapabilityBoundingSet=" in content, (
        "Override missing CapabilityBoundingSet="
    )


def test_caps_override_uses_sha256_skip_when_in_sync():
    """SHA-compare so restart doesn't rewrite the file when
    content is unchanged (mtime churn)."""
    src = _SERVER.read_text()
    helper = re.search(
        r"def _ensure_netgen_caps_override_deployed\(\)[\s\S]+?"
        r"(?=\ndef [a-z_])",
        src,
    ).group(0)
    assert "sha256" in helper or "hashlib" in helper, (
        "Caps self-heal doesn't hash-compare — would rewrite "
        "and bump mtime on every restart."
    )


def test_caps_override_runs_systemctl_daemon_reload():
    """After writing the drop-in, daemon-reload so systemctl
    picks up the file. The active process keeps its OLD caps
    (kernel sets caps at exec time) — operator must restart for
    new caps to apply."""
    src = _SERVER.read_text()
    helper = re.search(
        r"def _ensure_netgen_caps_override_deployed\(\)[\s\S]+?"
        r"(?=\ndef [a-z_])",
        src,
    ).group(0)
    assert "daemon-reload" in helper, (
        "Self-heal doesn't run `systemctl daemon-reload` — "
        "next manual restart would still use the old (cached) unit."
    )


def test_caps_self_heal_called_at_startup():
    """Helper must be called outside its own definition."""
    src = _SERVER.read_text()
    calls = src.count("_ensure_netgen_caps_override_deployed(")
    assert calls >= 2, (
        f"Helper appears {calls} times — needs definition plus "
        f"at least one startup call."
    )


def test_pyproject_version_at_least_0556():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 56), (
        f"Version {m.group(1)} < 0.5.56"
    )
