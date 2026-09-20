"""v0.5.367 — /admin RDMA install button.

Operator on svl-d-ai-srv04 2026-09-20: the admin console's RDMA
Stack card showed missing kernel modules with a footnote pointing
at the desktop client or `install_rdma.sh` on the host, but there
was no one-click Install action in the console itself. The
endpoint `POST /api/admin/install_rdma` already existed (v0.5.27
extracted RDMA from install_dpdk); this ship wires a button to it
and pipes the log through the existing Install Log card.

### Fix shape

- `<button id="btn-install-rdma" hidden>` next to the RDMA Stack
  card header, matching the System Dependencies card's Refresh
  button placement.
- `loadHealth` reveals the button only when RDMA is unhealthy
  (perftest missing OR any module unloaded). On a fully-installed
  host the button stays hidden so operators aren't tempted to
  re-run.
- Click handler POSTs `/api/admin/install_rdma`, then polls
  `/api/admin/install_rdma/log` at 2s intervals, streaming the log
  into the shared `<pre id="log">` element. On completion (rc=0
  or non-zero), the poll stops, `install-status` reflects the
  outcome, and `loadHealth()` re-runs so the RDMA card updates
  itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _src():
    return (_REPO / "run_tgen_server.py").read_text()


def test_marker_present():
    src = _src()
    assert "v0.5.367 (audit admin-rdma-install-button)" in src


# --- HTML: button exists on the RDMA card ---


def test_rdma_card_has_install_button_hidden_by_default():
    """The button must live inside the RDMA Stack card AND start
    with `hidden` so a healthy host doesn't advertise a re-run."""
    src = _src()
    _rdma_card_idx = src.index("<h2 style=\"margin: 0;\">RDMA Stack</h2>")
    # Bounded window covers the card body.
    body = src[_rdma_card_idx:_rdma_card_idx + 2000]
    assert '<button id="btn-install-rdma"' in body
    assert 'hidden>' in body, (
        "the install button must start hidden — reveal is driven "
        "by loadHealth when modules are missing"
    )


# --- JS: reveal / hide logic follows RDMA health ---


def test_reveal_gated_on_perftest_and_modules_loaded():
    """The reveal expression checks BOTH `perftest_installed` AND
    every module in `mods` — a partial install (perftest present,
    modules missing) must still show the button."""
    src = _src()
    marker_idx = src.index("v0.5.367 (audit admin-rdma-install-button): reveal the")
    body = src[marker_idx:marker_idx + 2000]
    assert "rdma.perftest_installed" in body
    # Explicit every-module check.
    assert "modKeys.every(k => mods[k])" in body
    # The hidden flag flips based on healthy state.
    assert "_btnRdmaInstall.hidden = _rdmaHealthy" in body


# --- JS: click handler wires POST + polling ---


def test_click_handler_posts_install_rdma():
    """Click handler must POST to /api/admin/install_rdma AND
    disable the button while the install is in flight (so double-
    click doesn't kick two installs)."""
    src = _src()
    marker_idx = src.index("v0.5.367 (audit admin-rdma-install-button): wire the new")
    body = src[marker_idx:marker_idx + 5000]
    assert "fetch('/api/admin/install_rdma', { method: 'POST' })" in body
    assert "$('btn-install-rdma').disabled = true" in body


def test_click_handler_polls_log_at_2s_intervals():
    """Polling must use setInterval(2000) — the existing
    /api/admin/install_rdma/log endpoint returns the full log in
    each response (no offset), so 2s is a reasonable cadence for
    a 1-3 minute install."""
    src = _src()
    marker_idx = src.index("v0.5.367 (audit admin-rdma-install-button): wire the new")
    body = src[marker_idx:marker_idx + 5000]
    assert "setInterval(_pollRdmaLog, 2000)" in body


def test_click_handler_stops_poll_on_completion():
    """When the log endpoint reports `running: false`, the poll
    must stop — otherwise the timer keeps firing forever."""
    src = _src()
    marker_idx = src.index("v0.5.367 (audit admin-rdma-install-button): wire the new")
    body = src[marker_idx:marker_idx + 5000]
    assert "if (!d.running)" in body
    assert "_stopRdmaPoll()" in body


def test_click_handler_reruns_loadHealth_on_completion():
    """After install completes the RDMA card must re-render so its
    Install button hides itself. Cheapest way: call the existing
    `loadHealth()` which repaints every card."""
    src = _src()
    marker_idx = src.index("v0.5.367 (audit admin-rdma-install-button): wire the new")
    body = src[marker_idx:marker_idx + 5000]
    assert "loadHealth()" in body


def test_click_handler_confirms_before_install():
    """A `confirm(...)` dialog fires before the POST — the install
    isn't destructive but it takes 1-3 minutes and pulls in apt
    packages; operator should acknowledge."""
    src = _src()
    marker_idx = src.index("v0.5.367 (audit admin-rdma-install-button): wire the new")
    body = src[marker_idx:marker_idx + 5000]
    assert "confirm(" in body


def test_click_handler_shares_log_pane_with_dpdk_install():
    """Log must stream into the existing `<pre id=\"log\">` element
    the DPDK installer uses — one Install Log card, not two."""
    src = _src()
    marker_idx = src.index("v0.5.367 (audit admin-rdma-install-button): wire the new")
    body = src[marker_idx:marker_idx + 5000]
    assert "$('log')" in body


# --- Regression guards ---


def test_v0_5_27_install_rdma_endpoint_still_defined():
    """The click handler POSTs to `/api/admin/install_rdma`. That
    endpoint has to stay in place — the v0.5.367 fix is UI-only."""
    src = _src()
    assert "@app.route(\"/api/admin/install_rdma\", methods=[\"POST\"])" in src


def test_install_rdma_log_endpoint_still_defined():
    src = _src()
    assert "@app.route(\"/api/admin/install_rdma/log\", methods=[\"GET\"])" in src


def test_v0_5_74_rdma_card_state_render_still_intact():
    """The pre-existing state-render code (pill for perftest,
    modules, HCA count, ports) must still be there — v0.5.367
    added a button beside it, not replaced anything."""
    src = _src()
    assert "p-rdma-perftest" in src
    assert "p-rdma-mods" in src
    assert "p-rdma-hca-count" in src
    assert "p-rdma-ports" in src


def test_ast_parses():
    import ast
    ast.parse(_src())
