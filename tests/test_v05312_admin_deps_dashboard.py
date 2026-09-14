"""v0.5.312 — admin-console host-side dependency dashboard.

Operator ask post-v0.5.310: "as a backup server admin console
should check all package dependencies and notify user and allow
manual install from admin console".

Rationale: `scripts/tarball/netgen-install` only auto-installs
deps on FRESH installs. Operators upgrading from older builds
silently miss anything added later (arping was added in v0.5.310;
srv06 didn't have it because the install predates v0.5.310).

This ship adds an in-console dashboard: enumerate every host-side
apt dep, show installed state, and offer one-click Install
buttons backed by a whitelisted `/api/admin/deps/install`
endpoint (safe: only accepts names from the same manifest).

Source-level checks only — Flask endpoint integration lives on
srv06.
"""
from __future__ import annotations

from pathlib import Path

_SERVER_PY = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER_PY.read_text()


# ─────────────────────────── Manifest


def test_manifest_declared():
    """_NETGEN_HOST_DEPS list must exist at module scope so both
    the enumerate endpoint and install endpoint drive off it."""
    src = _src()
    assert "_NETGEN_HOST_DEPS = [" in src


def test_manifest_covers_v0_5_310_ship():
    """v0.5.310 added iputils-arping to the install script; the
    manifest MUST include it so operators upgrading from pre-
    v0.5.310 see it as missing + can install it in-console."""
    src = _src()
    idx = src.index("_NETGEN_HOST_DEPS = [")
    body = src[idx:idx + 5000]
    assert '"name": "iputils-arping"' in body
    assert '"binary": "arping"' in body


def test_manifest_covers_lldpd_and_libpcap():
    """Existing tarball-installed deps (lldpd v0.5.82, libpcap0.8
    v0.4.0 scapy-runtime) also in the manifest — dashboard must
    show them AND allow re-install if the operator broke the
    system apt state."""
    src = _src()
    idx = src.index("_NETGEN_HOST_DEPS = [")
    body = src[idx:idx + 5000]
    assert '"name": "lldpd"' in body
    assert '"binary": "lldpcli"' in body
    assert '"name": "libpcap0.8"' in body


def test_manifest_covers_iproute2_and_ethtool():
    """Basic-networking deps (iproute2, ethtool) — universally
    preinstalled but must be enumerated for completeness so an
    operator with a broken minimal-container image can spot
    them missing."""
    src = _src()
    idx = src.index("_NETGEN_HOST_DEPS = [")
    body = src[idx:idx + 5000]
    assert '"name": "iproute2"' in body
    assert '"name": "ethtool"' in body


def test_manifest_entries_have_all_required_fields():
    """Every entry needs name + purpose + criticality + since_version.
    Enforced structurally so a future addition can't drop fields
    the UI depends on."""
    src = _src()
    # Grab just the manifest body.
    idx = src.index("_NETGEN_HOST_DEPS = [")
    end = src.index("]\n", idx)
    body = src[idx:end]
    # Every entry has these keys (count occurrences).
    n_entries = body.count('"name":')
    assert n_entries >= 5, f"expected >= 5 manifest entries, saw {n_entries}"
    assert body.count('"purpose":') == n_entries
    assert body.count('"criticality":') == n_entries
    assert body.count('"since_version":') == n_entries


# ─────────────────────────── Helpers + endpoints


def test_dep_installed_helper_defined():
    """_dep_installed(dep) helper decides installed-state — used by
    both the enumerate endpoint and the "already installed" short-
    circuit in the install endpoint."""
    src = _src()
    assert "def _dep_installed(dep):" in src


def test_get_deps_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/deps", methods=["GET"])' in src
    assert "def api_admin_deps():" in src


def test_get_deps_endpoint_requires_viewer():
    """Enumerate is read-only + leaks host info (package
    presence), gated on viewer role like /api/admin/health."""
    src = _src()
    idx = src.index('@app.route("/api/admin/deps", methods=["GET"])')
    body = src[idx:idx + 800]
    assert '@require_role("viewer")' in body


def test_install_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/deps/install", methods=["POST"])' in src
    assert "def api_admin_deps_install():" in src


def test_install_endpoint_requires_admin():
    """Install runs apt-get as root — gated on admin role, not
    viewer. Never a code path where an anonymous or viewer-role
    session can trigger a package install."""
    src = _src()
    idx = src.index('@app.route("/api/admin/deps/install", methods=["POST"])')
    body = src[idx:idx + 800]
    assert '@require_role("admin")' in body


def test_install_endpoint_whitelists_manifest_names_only():
    """Body {"name": ...} must be looked up in _NETGEN_HOST_DEPS.
    An unknown name returns 400 with the known-deps list. This
    is what makes it safe to expose apt-install as an HTTP
    endpoint — the operator can't send {"name": "malware-pkg"}
    and get it installed."""
    src = _src()
    idx = src.index("def api_admin_deps_install():")
    body = src[idx:idx + 3000]
    assert "next((d for d in _NETGEN_HOST_DEPS if d[\"name\"] == name), None)" in body
    assert '"error"' in body
    assert '"known_deps"' in body


def test_install_endpoint_uses_apt_get_with_lock_timeout():
    """apt-get install with -o DPkg::Lock::Timeout=30 so we don't
    hang forever when another apt/dpkg process is running. The
    outer subprocess timeout is 180 s as belt-and-suspenders."""
    src = _src()
    idx = src.index("def api_admin_deps_install():")
    body = src[idx:idx + 3000]
    assert '"apt-get", "install", "-y"' in body
    assert '"DPkg::Lock::Timeout=30"' in body
    assert "timeout=180" in body


def test_install_endpoint_verifies_installed_post_apt():
    """Post-apt, re-check via _dep_installed to confirm the binary
    is actually present. Success is `returncode == 0 AND
    installed_now`, not just returncode."""
    src = _src()
    idx = src.index("def api_admin_deps_install():")
    body = src[idx:idx + 3000]
    assert "installed_now = _dep_installed(dep)" in body
    assert "res.returncode == 0 and installed_now" in body


def test_install_endpoint_handles_apt_not_found():
    """On non-Debian hosts (RHEL, Alpine), apt-get isn't there.
    FileNotFoundError should return a friendly error, not a
    server-side crash."""
    src = _src()
    idx = src.index("def api_admin_deps_install():")
    body = src[idx:idx + 3000]
    assert "except FileNotFoundError:" in body
    assert "isn't Debian/Ubuntu" in body


# ─────────────────────────── Admin HTML card


def test_admin_html_has_deps_card():
    src = _src()
    assert 'id="card-deps"' in src
    assert "System Dependencies" in src
    assert 'id="deps-table-wrap"' in src


def test_admin_html_has_refresh_deps_button():
    src = _src()
    assert 'id="btn-refresh-deps"' in src


def test_admin_html_wires_refresh_and_install_handlers():
    """The JS wires the Refresh button and the per-row Install
    buttons (delegated click on `.btn-dep-install`)."""
    src = _src()
    # Refresh button handler.
    assert "$('btn-refresh-deps')" in src or '$("btn-refresh-deps")' in src
    # Delegated install-button click.
    assert "button.btn-dep-install" in src
    # Fetch call from installDep.
    assert "/api/admin/deps/install" in src


def test_admin_html_deps_card_placed_before_accelerators():
    """Layout: deps card sits ABOVE the DPDK Accelerators card so
    a fresh operator sees the deps status without scrolling past
    per-NIC PCI details they didn't ask for. Ordering matters
    for information hierarchy."""
    src = _src()
    deps_idx = src.index('id="card-deps"')
    acc_idx = src.index('id="card-accelerators"')
    assert deps_idx < acc_idx, "deps card must render before accelerators card"


def test_server_ast_parses():
    """v0.5.300 lesson."""
    import ast
    ast.parse(_src())
