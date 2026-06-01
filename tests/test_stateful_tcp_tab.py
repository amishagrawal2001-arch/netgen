"""Stateful TCP tab — dialog + table widget tests.

Headless qapp tests for `widgets/stateful_tcp_tab.py`. The pattern
mirrors `tests/test_dpdk_readiness_chip.py` and `tests/test_evpn_inject_dialog.py`:
monkeypatch `requests.get` / `requests.post` at the module the widget
imports, drive `refresh()` and the dialog's accept path directly,
assert on the rendered cells and the captured request bodies.

Bypasses the QTimer/QThread machinery — we test the synchronous
handlers (`_on_refresh_ok`, `_on_refresh_failed`, `_on_accept`,
`_stop_session_by_id`, etc.) rather than waiting for real polls.
"""

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QMessageBox, QWidget


# ──────────────────────────────────────────────────────── pure validators


def test_validate_ip_accepts_v4_v6_and_bind_any():
    from widgets.stateful_tcp_tab import _validate_ip
    assert _validate_ip("10.0.0.5") is None
    assert _validate_ip("0.0.0.0") is None           # server bind-any
    assert _validate_ip("127.0.0.1") is None
    assert _validate_ip("::1") is None
    assert _validate_ip("2001:db8::1") is None


def test_validate_ip_rejects_garbage():
    from widgets.stateful_tcp_tab import _validate_ip
    assert _validate_ip("") is not None
    assert _validate_ip("999.0.0.1") is not None
    assert _validate_ip("not-an-ip") is not None


def test_validate_port_rejects_zero_and_overflow():
    from widgets.stateful_tcp_tab import _validate_port
    assert _validate_port(1) is None
    assert _validate_port(5001) is None
    assert _validate_port(65535) is None
    assert _validate_port(0) is not None
    assert _validate_port(65536) is not None


def test_is_loopback_handles_v4_v6_and_garbage():
    from widgets.stateful_tcp_tab import _is_loopback
    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("127.5.5.5") is True   # entire 127/8
    assert _is_loopback("::1") is True
    assert _is_loopback("10.0.0.5") is False
    assert _is_loopback("") is False           # graceful fallback
    assert _is_loopback("garbage") is False


# ──────────────────────────────────────────────────────── dialog fixtures


@pytest.fixture
def open_dialog(qapp, monkeypatch):
    """Build a config dialog with the modal QMessageBox calls
    silenced — pop-ups would otherwise block the headless run.

    The dialog is `show()`-n (against the offscreen Qt platform) so
    `QWidget.isVisible()` works in the visibility-toggle assertions
    below. Without show(), every widget reports `isVisible() == False`
    regardless of `setVisible()` state because no ancestor is mapped."""
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    from widgets.stateful_tcp_tab import _StatefulTcpConfigDialog
    parent = QWidget()
    parent.show()
    dlg = _StatefulTcpConfigDialog(parent)
    dlg.show()
    yield dlg, parent
    try:
        dlg.close()
        parent.close()
    except Exception:
        pass


# ─────────────────────────────────────────────── dialog: visibility wiring


def test_dialog_default_role_is_client(open_dialog):
    dlg, _ = open_dialog
    assert dlg._role_client.isChecked() is True
    assert dlg._role_server.isChecked() is False
    # Stack should be on the client panel (index 0).
    assert dlg._role_stack.currentIndex() == 0


def test_dialog_role_toggle_swaps_field_set(open_dialog):
    """Flipping to Server moves the stack to index 1 and hides the
    client-only loopback warning region."""
    dlg, _ = open_dialog
    dlg._role_server.setChecked(True)
    assert dlg._role_stack.currentIndex() == 1
    assert dlg._loopback_warn.isVisible() is False
    # Flipping back returns to client panel.
    dlg._role_client.setChecked(True)
    assert dlg._role_stack.currentIndex() == 0


def test_dialog_tls_toggle_shows_tls_group(open_dialog):
    dlg, _ = open_dialog
    assert dlg._tls_group.isVisible() is False
    dlg._tls_check.setChecked(True)
    assert dlg._tls_group.isVisible() is True
    dlg._tls_check.setChecked(False)
    assert dlg._tls_group.isVisible() is False


def test_dialog_tls_rows_swap_with_role(open_dialog):
    """Client-side: verify + SNI visible, cert+key hidden.
    Server-side: cert+key visible, verify+SNI hidden."""
    dlg, _ = open_dialog
    dlg._tls_check.setChecked(True)

    # Client default — verify + SNI shown, cert/key hidden.
    assert dlg._tls_verify.isVisible() is True
    assert dlg._tls_sni.isVisible() is True
    assert dlg._tls_cert.isVisible() is False
    assert dlg._tls_key.isVisible() is False

    # Flip to server — cert/key shown, verify + SNI hidden.
    dlg._role_server.setChecked(True)
    assert dlg._tls_verify.isVisible() is False
    assert dlg._tls_sni.isVisible() is False
    assert dlg._tls_cert.isVisible() is True
    assert dlg._tls_key.isVisible() is True


def test_dialog_protocol_change_disables_response_bytes_for_raw(open_dialog):
    """response_bytes is HTTP-only; greyed (but not hidden) for raw so
    the operator doesn't think it's a noop knob they forgot to set."""
    dlg, _ = open_dialog
    # Protocol combo defaults to raw (index 0).
    assert dlg._proto_combo.currentData() == "raw"
    assert dlg._srv_response_bytes.isEnabled() is False
    # Switch to http → enabled.
    http_idx = next(i for i in range(dlg._proto_combo.count())
                    if dlg._proto_combo.itemData(i) == "http")
    dlg._proto_combo.setCurrentIndex(http_idx)
    assert dlg._srv_response_bytes.isEnabled() is True


def test_dialog_loopback_warning_fires_on_loopback_zero_interval(open_dialog):
    """The bug we just fixed in pytest needs an inline UX warning here."""
    dlg, _ = open_dialog
    # Empty IP → no warning yet.
    assert dlg._loopback_warn.isVisible() is False

    dlg._cli_dst_ip.setText("127.0.0.1")
    # interval defaults to 0.0 → loopback + 0 = warn.
    assert dlg._loopback_warn.isVisible() is True
    assert "ephemeral" in dlg._loopback_warn.text().lower()

    # Raising interval to >=0.005 clears the warning.
    dlg._cli_interval.setValue(0.02)
    assert dlg._loopback_warn.isVisible() is False


def test_dialog_loopback_warning_silent_for_non_loopback(open_dialog):
    dlg, _ = open_dialog
    dlg._cli_dst_ip.setText("10.0.0.5")
    dlg._cli_interval.setValue(0.0)
    assert dlg._loopback_warn.isVisible() is False


# ─────────────────────────────────────────────── dialog: payload assembly


def test_dialog_client_payload_default_shape(open_dialog):
    """Filling required fields + accept → payload has client role, the
    right kwargs, and optional fields omitted when blank."""
    dlg, _ = open_dialog
    dlg._cli_dst_ip.setText("10.0.0.5")
    dlg._cli_dst_port.setValue(5001)
    dlg._cli_duration.setValue(10.0)
    dlg._cli_concurrency.setValue(2)
    dlg._cli_interval.setValue(0.02)
    dlg._on_accept()
    pl = dlg.accepted_payload()
    assert pl is not None
    assert pl["role"] == "client"
    body = pl["body"]
    assert body["role"] == "client"
    assert body["dst_ip"] == "10.0.0.5"
    assert body["dst_port"] == 5001
    assert body["duration_s"] == 10.0
    assert body["concurrency"] == 2
    assert body["interval_s"] == 0.02
    assert body["protocol"] == "raw"
    assert body["tls"] is False
    # Optional fields not in body when blank.
    assert "src_ip" not in body
    assert "vrf" not in body


def test_dialog_client_payload_carries_optional_vrf_and_src_ip(open_dialog):
    dlg, _ = open_dialog
    dlg._cli_dst_ip.setText("10.0.0.5")
    dlg._cli_src_ip.setText("10.0.0.10")
    dlg._cli_vrf.setText("vrf-blue")
    dlg._on_accept()
    body = dlg.accepted_payload()["body"]
    assert body["src_ip"] == "10.0.0.10"
    assert body["vrf"] == "vrf-blue"


def test_dialog_client_payload_with_tls_carries_verify_and_sni(open_dialog):
    dlg, _ = open_dialog
    dlg._cli_dst_ip.setText("10.0.0.5")
    dlg._tls_check.setChecked(True)
    dlg._tls_verify.setChecked(True)
    dlg._tls_sni.setText("server.example.com")
    dlg._on_accept()
    body = dlg.accepted_payload()["body"]
    assert body["tls"] is True
    assert body["tls_verify"] is True
    assert body["tls_server_hostname"] == "server.example.com"


def test_dialog_server_payload_default_shape(open_dialog):
    dlg, _ = open_dialog
    dlg._role_server.setChecked(True)
    dlg._srv_listen_port.setValue(5001)
    dlg._on_accept()
    pl = dlg.accepted_payload()
    assert pl is not None
    assert pl["role"] == "server"
    body = pl["body"]
    assert body["role"] == "server"
    assert body["listen_ip"] == "0.0.0.0"
    assert body["listen_port"] == 5001
    assert body["mode"] == "echo"
    assert body["protocol"] == "raw"
    # response_bytes omitted for raw — it's HTTP-only.
    assert "response_bytes" not in body


def test_dialog_server_http_carries_response_bytes(open_dialog):
    dlg, _ = open_dialog
    dlg._role_server.setChecked(True)
    http_idx = next(i for i in range(dlg._proto_combo.count())
                    if dlg._proto_combo.itemData(i) == "http")
    dlg._proto_combo.setCurrentIndex(http_idx)
    dlg._srv_response_bytes.setValue(2048)
    dlg._on_accept()
    body = dlg.accepted_payload()["body"]
    assert body["protocol"] == "http"
    assert body["response_bytes"] == 2048


def test_dialog_rejects_invalid_dst_ip(open_dialog, monkeypatch):
    """An invalid IP fires the Invalid-input warning AND leaves
    payload None so the caller doesn't fire a malformed POST."""
    dlg, _ = open_dialog
    warnings: List[Any] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: warnings.append(a))
    )
    dlg._cli_dst_ip.setText("999.999.999.999")
    dlg._on_accept()
    assert dlg.accepted_payload() is None
    assert warnings, "expected a QMessageBox.warning to be raised"


def test_dialog_rejects_server_tls_without_cert_and_key(open_dialog, monkeypatch):
    dlg, _ = open_dialog
    warnings: List[Any] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: warnings.append(a))
    )
    dlg._role_server.setChecked(True)
    dlg._tls_check.setChecked(True)
    # Cert + key both blank → reject.
    dlg._on_accept()
    assert dlg.accepted_payload() is None
    assert warnings
    msg = " ".join(str(w) for w in warnings[0])
    assert "cert" in msg.lower() and "key" in msg.lower()


# ──────────────────────────────────────────────── tab fixtures


@pytest.fixture
def make_tab(qapp, monkeypatch):
    """Construct a StatefulTcpTab against a stubbed parent_window and
    a network module patched to never actually call out. Stop the
    poll timer immediately so tests drive refresh state by hand."""
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))

    from widgets import stateful_tcp_tab as mod

    parent = QWidget()
    # Match the real main window's `devices_tab.get_server_url(silent=…)`
    # contract so _get_server_url returns a predictable URL.
    parent.devices_tab = SimpleNamespace(
        get_server_url=lambda silent=False: "http://test-server:5050"
    )

    tab = mod.StatefulTcpTab(parent)
    # Kill the poll timer + cancel the 300ms first-refresh so no test
    # accidentally fires a real fetch worker.
    tab._timer.stop()
    return tab, mod, parent


def _running_session(sid="abc-1", role="client", proto="raw",
                     **counter_overrides) -> Dict[str, Any]:
    """Helper to build a session payload the server would return."""
    counters = {
        "uptime_s": 12.3,
        "conns_attempted": 412, "conns_established": 410, "conns_failed": 2,
        "bytes_tx": 419840, "bytes_rx": 419840,
        "avg_handshake_ms": 0.92, "avg_rtt_ms": 1.43, "rtt_samples": 410,
        "avg_kernel_rtt_us": 920.4, "kernel_rtt_samples": 410,
        "retransmits_total": 0,
        "http_status_2xx": 0, "http_status_other": 0,
        "dns_noerror": 0, "dns_nxdomain": 0, "dns_servfail": 0, "dns_other": 0,
        "sip_2xx": 0, "sip_3xx": 0, "sip_4xx": 0, "sip_5xx": 0, "sip_other": 0,
        "last_error": None,
    }
    counters.update(counter_overrides)
    config = {
        "dst_ip": "10.0.0.5", "dst_port": 5001,
        "listen_ip": "0.0.0.0", "listen_port": 5001,
        "protocol": proto, "tls": False, "vrf": None,
    }
    return {
        "session_id": sid, "role": role,
        "protocol": proto, "running": True,
        "config": config, "counters": counters,
    }


# ──────────────────────────────────────────────── tab: rendering


def test_tab_constructs_with_empty_session_table(make_tab):
    tab, _, _ = make_tab
    assert tab._table.rowCount() == 0
    # Count chip starts at zero/zero.
    assert "0" in tab._count_chip.text()


def test_tab_renders_running_client_session(make_tab):
    tab, _, _ = make_tab
    tab._on_refresh_ok({"sessions": [_running_session()]}, 200)
    assert tab._table.rowCount() == 1
    status_item = tab._table.item(0, tab.COL_STATUS)
    assert status_item is not None
    assert "Running" in status_item.text()
    # Session ID stashed in UserRole on the Status cell — used by
    # per-row Stop and Stop selected.
    assert status_item.data(Qt.UserRole) == "abc-1"
    # Role cell carries the badge text.
    assert tab._table.item(0, tab.COL_ROLE).text() == "CLIENT"
    # Target column built from dst_ip:dst_port for clients.
    assert tab._table.item(0, tab.COL_TARGET).text() == "10.0.0.5:5001"
    # Conns established → count column.
    assert "410" in tab._table.item(0, tab.COL_CONNS).text()


def test_tab_renders_server_session_target_from_listen(make_tab):
    tab, _, _ = make_tab
    tab._on_refresh_ok({
        "sessions": [_running_session(sid="srv-1", role="server")]
    }, 200)
    assert tab._table.item(0, tab.COL_ROLE).text() == "SERVER"
    # Server target is listen_ip:listen_port, NOT dst_*.
    assert tab._table.item(0, tab.COL_TARGET).text() == "0.0.0.0:5001"


def test_tab_protocol_cell_carries_tls_suffix(make_tab):
    """Operator wants to see at a glance whether TLS is wrapping the
    underlying protocol — encoded as 'HTTP+TLS' / 'RAW+TLS' in the
    badge cell rather than its own column."""
    tab, _, _ = make_tab
    sess = _running_session(proto="http")
    sess["config"]["tls"] = True
    tab._on_refresh_ok({"sessions": [sess]}, 200)
    assert tab._table.item(0, tab.COL_PROTO).text() == "HTTP+TLS"


def test_tab_status_tooltip_carries_protocol_specific_bins(make_tab):
    """The 200-char-or-more last_error and per-protocol counter bins
    live in the Status cell tooltip rather than their own column."""
    tab, _, _ = make_tab
    sess = _running_session(proto="http", http_status_2xx=410,
                            last_error="OSError: [Errno 49] EADDRNOTAVAIL")
    tab._on_refresh_ok({"sessions": [sess]}, 200)
    tip = tab._table.item(0, tab.COL_STATUS).toolTip()
    assert "http: 2xx=410" in tip
    assert "EADDRNOTAVAIL" in tip


def test_tab_status_tooltip_dns_bins(make_tab):
    tab, _, _ = make_tab
    sess = _running_session(proto="dns",
                            dns_noerror=10, dns_nxdomain=400,
                            dns_servfail=0, dns_other=0)
    tab._on_refresh_ok({"sessions": [sess]}, 200)
    tip = tab._table.item(0, tab.COL_STATUS).toolTip()
    assert "dns: noerror=10" in tip
    assert "nxdomain=400" in tip


def test_tab_status_tooltip_sip_bins(make_tab):
    tab, _, _ = make_tab
    sess = _running_session(proto="sip", sip_4xx=42)
    tab._on_refresh_ok({"sessions": [sess]}, 200)
    tip = tab._table.item(0, tab.COL_STATUS).toolTip()
    assert "sip:" in tip and "4xx=42" in tip


def test_tab_stopped_session_shows_no_stop_button(make_tab):
    """Stop button only on running rows — stopped rows get a
    placeholder QTableWidgetItem so sort/filter see a stable cell."""
    tab, _, _ = make_tab
    sess = _running_session()
    sess["running"] = False
    tab._on_refresh_ok({"sessions": [sess]}, 200)
    assert tab._table.cellWidget(0, tab.COL_ACTION) is None
    assert tab._table.item(0, tab.COL_ACTION) is not None


def test_tab_count_chip_reflects_running_total(make_tab):
    tab, _, _ = make_tab
    s1 = _running_session(sid="r1")
    s2 = _running_session(sid="r2"); s2["running"] = False
    tab._on_refresh_ok({"sessions": [s1, s2]}, 200)
    txt = tab._count_chip.text()
    assert "1" in txt and "2" in txt  # 1 running · 2 total


# ──────────────────────────────────────────────── tab: failure paths


def test_tab_enters_unsupported_mode_on_404(make_tab):
    tab, _, _ = make_tab
    tab._on_refresh_failed("HTTP 404", 404)
    assert tab._unsupported is True
    assert "/api/stateful_tcp" in tab._unsupported_reason
    # Timer should be slowed dramatically so we don't hammer a missing
    # endpoint every 3 seconds.
    assert tab._timer.interval() >= 60_000
    # Count chip switches to amber "unavailable" label.
    assert "unavailable" in tab._count_chip.text().lower()


def test_tab_exits_unsupported_mode_when_sessions_returns_again(make_tab):
    tab, _, _ = make_tab
    tab._on_refresh_failed("HTTP 404", 404)
    assert tab._unsupported is True
    # Now sessions endpoint comes back online.
    tab._on_refresh_ok({"sessions": []}, 200)
    assert tab._unsupported is False
    assert tab._timer.interval() == tab.POLL_INTERVAL_MS


def test_tab_auth_failure_surfaces_in_info_label(make_tab):
    tab, _, _ = make_tab
    tab._on_refresh_failed("HTTP 401", 401)
    assert "auth failed" in tab._info_label.text().lower()
    # NOT entering unsupported mode — different recovery path (set
    # the token), so the table remains live.
    assert tab._unsupported is False


# ──────────────────────────────────────────────── tab: stop paths


def _patch_async_post_to_sync(monkeypatch, mod):
    """v0.2.91: stop paths now spawn a _JsonPostWorker QThread so the
    GUI doesn't block on slow servers. For unit tests we collapse the
    async hop by monkeypatching the worker's start() to call run()
    synchronously — the mocked module-level requests.post still
    captures the call, just on the test's thread instead of a QThread.
    Without this, tests asserting on captured calls race against the
    OS thread scheduler."""
    monkeypatch.setattr(
        mod._JsonPostWorker, "start",
        lambda self: self.run()
    )


def test_tab_per_row_stop_posts_correct_session_id(make_tab, monkeypatch):
    """Closure-in-loop trap: the row's session_id must be captured by
    the lambda, not the loop variable. Two rows → click each Stop
    button → each POST hits the right session_id."""
    tab, mod, _ = make_tab
    _patch_async_post_to_sync(monkeypatch, mod)
    captured: List[Dict[str, Any]] = []
    mod.requests = MagicMock()
    mod.requests.post = lambda url, json=None, headers=None, timeout=None: (
        captured.append({"url": url, "json": json}) or
        SimpleNamespace(status_code=200, text="", json=lambda: {})
    )
    tab._on_refresh_ok({"sessions": [
        _running_session(sid="alpha"),
        _running_session(sid="beta"),
    ]}, 200)
    btn0 = tab._table.cellWidget(0, tab.COL_ACTION)
    btn1 = tab._table.cellWidget(1, tab.COL_ACTION)
    assert btn0 is not None and btn1 is not None
    btn0.click()
    btn1.click()
    sids = [c["json"]["session_id"] for c in captured]
    assert sids == ["alpha", "beta"]
    # Every POST went to the stop endpoint.
    for c in captured:
        assert c["url"].endswith("/api/stateful_tcp/stop")


def test_tab_stop_all_posts_empty_body_after_confirm(make_tab, monkeypatch):
    tab, mod, _ = make_tab
    _patch_async_post_to_sync(monkeypatch, mod)
    captured: List[Dict[str, Any]] = []
    mod.requests = MagicMock()
    mod.requests.post = lambda url, json=None, headers=None, timeout=None: (
        captured.append({"url": url, "json": json}) or
        SimpleNamespace(status_code=200, text="", json=lambda: {})
    )
    # Q-msgbox confirm already monkeypatched to Yes in the fixture.
    tab._on_stop_all()
    assert len(captured) == 1
    assert captured[0]["url"].endswith("/api/stateful_tcp/stop")
    assert captured[0]["json"] == {}


def test_tab_stop_all_aborts_when_user_says_no(make_tab, monkeypatch):
    tab, mod, _ = make_tab
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    captured: List[Any] = []
    mod.requests = MagicMock()
    mod.requests.post = lambda *a, **k: (
        captured.append(a) or
        SimpleNamespace(status_code=200, text="", json=lambda: {})
    )
    tab._on_stop_all()
    assert captured == []


# ──────────────────────────────────────────────── tab: lifecycle


def test_tab_cleanup_threads_stops_timer(make_tab):
    tab, _, _ = make_tab
    tab._timer.start()
    assert tab._timer.isActive() is True
    tab.cleanup_threads()
    assert tab._timer.isActive() is False


# ──────────────────────────────────────────────── v0.2.91 regressions
#
# One test per finding from the v0.2.88 code-review + GUI smoke. These
# guard against the specific bugs coming back — keep them around even
# if the implementation is later restructured. Each test names the
# finding number so the audit trail is preserved in the test report.


def test_v0_2_91_finding_1_server_tls_does_not_validate_path_on_client_fs(
    open_dialog, monkeypatch
):
    """Finding #1 — the cert/key files are read by the netgen-SERVER,
    not by the GUI host. Previously the dialog rejected any path that
    didn't exist on the operator's laptop, breaking every remote-server
    deployment. The fix dropped the os.path.isfile() check entirely;
    server-side validation now surfaces via the existing non-200 path."""
    dlg, _ = open_dialog
    warnings: List[Any] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: warnings.append(a))
    )
    dlg._role_server.setChecked(True)
    dlg._tls_check.setChecked(True)
    # Path that DEFINITELY doesn't exist on the test host.
    dlg._tls_cert.setText("/nonexistent/server/path/cert.pem")
    dlg._tls_key.setText("/nonexistent/server/path/key.pem")
    dlg._on_accept()
    # Payload should be assembled (no client-side rejection).
    pl = dlg.accepted_payload()
    assert pl is not None
    assert pl["body"]["tls_cert"] == "/nonexistent/server/path/cert.pem"
    assert pl["body"]["tls_key"] == "/nonexistent/server/path/key.pem"
    # And no warning about "file not found" should have fired.
    msgs = " ".join(str(w) for w in warnings)
    assert "not found" not in msgs.lower(), (
        f"finding #1 regression — dialog re-introduced client-side "
        f"path check: {msgs}"
    )


def test_v0_2_91_finding_2_stale_stop_button_evicted_on_running_to_stopped(
    make_tab
):
    """Finding #2 — when a session flips running→stopped between
    polls, setItem() does NOT clear a previously-installed Stop
    QPushButton at the same cell. The fix calls removeCellWidget()
    before setItem(). Regression check: render running → render same
    row stopped → the cellWidget must be gone."""
    tab, _, _ = make_tab
    sess = _running_session(sid="ephemeral")
    tab._on_refresh_ok({"sessions": [sess]}, 200)
    assert tab._table.cellWidget(0, tab.COL_ACTION) is not None, (
        "running row should have a Stop QPushButton"
    )
    # Now flip the same row to stopped.
    sess["running"] = False
    tab._on_refresh_ok({"sessions": [sess]}, 200)
    assert tab._table.cellWidget(0, tab.COL_ACTION) is None, (
        "finding #2 regression — stale Stop button persisted on "
        "stopped row"
    )


def test_v0_2_91_finding_3_browse_buttons_hidden_in_client_tls_mode(
    open_dialog
):
    """Finding #3 (and smoke-surfaced #6) — the cert/key Browse
    buttons live inside cert_wrap/key_wrap QWidgets. The old
    _on_role_changed hid only the inner QLineEdits, leaving Browse
    buttons floating in client-TLS view. Fix: hide the wraps."""
    dlg, _ = open_dialog
    dlg._tls_check.setChecked(True)
    # Client mode (default) — every server-side TLS row should be hidden.
    assert dlg._tls_cert_wrap.isVisible() is False
    assert dlg._tls_key_wrap.isVisible() is False
    # And concretely the Browse buttons inside the wraps should be
    # invisible too (Qt propagates parent visibility to children).
    from PyQt5.QtWidgets import QPushButton
    visible_browse = [
        btn.text() for btn in dlg._tls_group.findChildren(QPushButton)
        if btn.text().lower().startswith("browse") and btn.isVisible()
    ]
    assert visible_browse == [], (
        f"finding #3 regression — Browse button(s) visible in "
        f"client-TLS mode: {visible_browse}"
    )


def test_v0_2_91_finding_6_sni_label_hidden_in_server_tls_mode(open_dialog):
    """Smoke-surfaced finding #6 — when QFormLayout.addRow() is called
    with a string label, Qt builds the QLabel internally; hiding only
    the field leaves the label dangling. The fix captures the SNI label
    as an explicit QLabel (and similarly for the empty-label row of the
    Verify checkbox) so we can hide it alongside the field."""
    dlg, _ = open_dialog
    dlg._tls_check.setChecked(True)
    dlg._role_server.setChecked(True)
    # In server mode the SNI label + field, and Verify label + checkbox,
    # must ALL be hidden — not just the input parts.
    assert dlg._tls_sni.isVisible() is False
    assert dlg._tls_sni_label.isVisible() is False, (
        "finding #6 regression — 'SNI hostname:' label dangling in "
        "server-TLS view"
    )
    assert dlg._tls_verify.isVisible() is False
    assert dlg._tls_verify_label.isVisible() is False


def test_v0_2_91_finding_4_stop_posts_run_off_gui_thread(
    make_tab, monkeypatch
):
    """Finding #4 — the three stop paths used to call requests.post()
    synchronously on the GUI thread (5s × N for Stop selected, 10s for
    Stop all). Fix: route every stop through _JsonPostWorker, a
    QThread that emits done(http_code, msg) for logging.

    Regression check: when Stop is clicked, a _JsonPostWorker is
    constructed and start()-ed, and requests.post is NOT invoked
    directly on the GUI thread."""
    tab, mod, _ = make_tab
    # Track every _JsonPostWorker construction.
    spawned: List[Dict[str, Any]] = []
    original_init = mod._JsonPostWorker.__init__

    def _capturing_init(self, url, json_body=None, timeout_s=5.0):
        spawned.append({"url": url, "json": json_body, "timeout_s": timeout_s})
        original_init(self, url, json_body=json_body, timeout_s=timeout_s)

    monkeypatch.setattr(mod._JsonPostWorker, "__init__", _capturing_init)
    # Stop the worker's run() from actually hitting the network in the
    # test — we only care that a worker was spawned per stop click.
    monkeypatch.setattr(mod._JsonPostWorker, "start", lambda self: None)

    tab._on_refresh_ok({"sessions": [_running_session(sid="x1")]}, 200)
    btn = tab._table.cellWidget(0, tab.COL_ACTION)
    assert btn is not None
    btn.click()
    assert len(spawned) == 1
    assert spawned[0]["url"].endswith("/api/stateful_tcp/stop")
    assert spawned[0]["json"] == {"session_id": "x1"}


def test_v0_2_91_finding_5_stop_selected_skips_filter_hidden_rows(
    make_tab, monkeypatch
):
    """Finding #5 — _on_stop_selected used to walk every selected row,
    including rows hidden by _apply_session_filter (selection survives
    filter changes). The fix skips hidden rows so the operator can
    only stop sessions they can actually see."""
    tab, mod, _ = make_tab
    _patch_async_post_to_sync(monkeypatch, mod)
    captured: List[Dict[str, Any]] = []
    mod.requests = MagicMock()
    mod.requests.post = lambda url, json=None, headers=None, timeout=None: (
        captured.append({"json": json}) or
        SimpleNamespace(status_code=200, text="", json=lambda: {})
    )
    tab._on_refresh_ok({"sessions": [
        _running_session(sid="visible-1"),
        _running_session(sid="visible-2"),
        _running_session(sid="hidden-row"),
    ]}, 200)
    # Select all 3 rows...
    tab._table.selectAll()
    # ...then hide one via the filter (simulating: operator typed a
    # substring that doesn't match the third session).
    tab._table.setRowHidden(2, True)
    # Stop selected should walk visible rows only.
    tab._on_stop_selected()
    sids_stopped = [c["json"]["session_id"] for c in captured]
    assert "hidden-row" not in sids_stopped, (
        f"finding #5 regression — Stop selected hit a hidden row: "
        f"{sids_stopped}"
    )
    assert sorted(sids_stopped) == ["visible-1", "visible-2"]


# ──────────────────────────────────────────────── v0.2.94 PAIN-tier
#
# Three patterns that landed on every other session-table surface
# between v0.2.74 and v0.2.93 but were missing from Stateful TCP.
# Each test pins the v0.2.94 behaviour so a future refactor can't
# accidentally regress to the pre-v0.2.94 state where:
#   * the operator's sort column reset every 3 s on auto-refresh,
#   * the table sat blank/ambiguous when empty,
#   * Stop-selected fired async without telling the operator which
#     SIDs succeeded vs failed.


def test_v0_2_94_empty_state_overlay_shown_when_no_sessions(make_tab):
    """v0.2.94 #1 — EmptyStateOverlay placeholder is visible while
    the table has 0 rows; the parent must show() for QLabel.isVisible
    to actually return True under offscreen Qt."""
    tab, _, parent = make_tab
    parent.show()
    overlay = getattr(tab, "_empty_overlay", None)
    assert overlay is not None, "v0.2.94 regression — _empty_overlay missing"
    # Fresh tab → 0 rows → overlay visible.
    assert tab._table.rowCount() == 0
    assert overlay._label.isVisible() is True
    # Render one session → overlay hides.
    tab._on_refresh_ok({"sessions": [_running_session()]}, 200)
    assert tab._table.rowCount() == 1
    assert overlay._label.isVisible() is False
    # Sessions drain → overlay returns.
    tab._on_refresh_ok({"sessions": []}, 200)
    assert tab._table.rowCount() == 0
    assert overlay._label.isVisible() is True


def test_v0_2_94_empty_state_overlay_message_mentions_start_session(make_tab):
    """The placeholder should hint at what to do next, not just say
    'empty'. Pin that it points the operator at the Start button."""
    tab, _, _ = make_tab
    overlay = tab._empty_overlay
    txt = overlay._label.text()
    assert "Start session" in txt, (
        f"empty-state hint should mention the Start affordance "
        f"(got: {txt!r})"
    )


def test_v0_2_94_sort_state_preserved_across_render(make_tab):
    """v0.2.94 #2 — operator clicks the 'Uptime' column header to sort
    by longest-running. The next 3 s auto-refresh must NOT reset the
    sort indicator (pre-v0.2.94 the indicator vanished on every poll
    because the rebuild cycled setSortingEnabled false→true)."""
    tab, _, _ = make_tab
    tab._table.setSortingEnabled(True)
    # Render once with two sessions so sorting has something to do.
    tab._on_refresh_ok({"sessions": [
        _running_session(sid="r1"),
        _running_session(sid="r2"),
    ]}, 200)
    # Operator clicks 'Uptime' header → descending.
    tab._table.sortByColumn(tab.COL_UPTIME, Qt.DescendingOrder)
    # Sanity check: indicator landed on the right column + order.
    header = tab._table.horizontalHeader()
    assert header.sortIndicatorSection() == tab.COL_UPTIME
    assert header.sortIndicatorOrder() == Qt.DescendingOrder
    # Now an auto-refresh fires (same payload — what matters is the
    # render path runs end-to-end).
    tab._on_refresh_ok({"sessions": [
        _running_session(sid="r1"),
        _running_session(sid="r2"),
    ]}, 200)
    # Indicator must still be on the operator's chosen column + order.
    assert header.sortIndicatorSection() == tab.COL_UPTIME, (
        "v0.2.94 regression — sort indicator reset on rebuild"
    )
    assert header.sortIndicatorOrder() == Qt.DescendingOrder


def test_v0_2_94_bulk_stop_shows_multi_device_results_dialog(
    make_tab, monkeypatch,
):
    """v0.2.94 #3 — Stop selected on N rows must surface a per-row
    MultiDeviceResultsDialog (✅/❌ prefixes) so the operator sees
    exactly which SIDs succeeded. Pre-v0.2.94 the fan-out was
    fire-and-forget — operator only learned the outcome via the next
    3 s poll, which was ambiguous when some failed.
    """
    tab, mod, _ = make_tab
    _patch_async_post_to_sync(monkeypatch, mod)
    # 2/3 succeed, middle one fails with a non-2xx.
    responses = iter([
        SimpleNamespace(status_code=200, text="", json=lambda: {}),
        SimpleNamespace(status_code=500, text="boom", json=lambda: {}),
        SimpleNamespace(status_code=200, text="", json=lambda: {}),
    ])
    mod.requests = MagicMock()
    mod.requests.post = (
        lambda url, json=None, headers=None, timeout=None: next(responses)
    )
    # Intercept the dialog so the test can introspect what was passed
    # without an actual modal popping up.
    captured: List[Dict[str, Any]] = []

    class _FakeDlg:
        def __init__(self, title, summary, results, parent):
            captured.append({
                "title": title, "summary": summary,
                "results": list(results), "parent": parent,
            })

        def exec_(self):
            return 0  # accepted/rejected doesn't matter for the test

    # Patch the lazy import inside _show_bulk_stop_results.
    import widgets.devices_tab as devices_tab_mod
    monkeypatch.setattr(
        devices_tab_mod, "MultiDeviceResultsDialog", _FakeDlg
    )

    tab._on_refresh_ok({"sessions": [
        _running_session(sid="alpha"),
        _running_session(sid="bravo"),
        _running_session(sid="charlie"),
    ]}, 200)
    tab._table.selectAll()
    tab._on_stop_selected()

    # All 3 SIDs accounted for, dialog constructed exactly once.
    assert len(captured) == 1
    payload = captured[0]
    assert "Stop selected" in payload["title"]
    # Summary mentions the 2-of-3 split.
    assert "2" in payload["summary"] and "3" in payload["summary"]
    assert "fail" in payload["summary"].lower()
    # Per-row result lines carry the expected emoji prefixes — pin the
    # convention so a future refactor can't swap to plain text.
    joined = " ".join(payload["results"])
    assert joined.count("✅") == 2
    assert joined.count("❌") == 1
    # Each SID surfaces (short form: first 10 chars + ellipsis).
    for short in ("alpha", "bravo", "charlie"):
        assert any(short in r for r in payload["results"]), (
            f"bulk-stop dialog missing row for {short!r}"
        )


def test_v0_2_94_bulk_stop_falls_back_to_messagebox_on_dialog_failure(
    make_tab, monkeypatch,
):
    """Defensive: if MultiDeviceResultsDialog construction raises
    (import error, Qt teardown, whatever) the operator still gets a
    QMessageBox digest rather than silent failure. Same belt-and-
    braces pattern as the v0.2.93 VXLAN apply fallback."""
    tab, mod, _ = make_tab
    _patch_async_post_to_sync(monkeypatch, mod)
    mod.requests = MagicMock()
    mod.requests.post = lambda *a, **k: SimpleNamespace(
        status_code=200, text="", json=lambda: {},
    )
    # Force the dialog constructor to blow up.
    import widgets.devices_tab as devices_tab_mod

    def _blowup(*_a, **_k):
        raise RuntimeError("simulated dialog construction failure")

    monkeypatch.setattr(
        devices_tab_mod, "MultiDeviceResultsDialog", _blowup
    )
    # Intercept QMessageBox.information to confirm the fallback fires.
    fallback_calls: List[Any] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: fallback_calls.append(a))
    )
    tab._on_refresh_ok({"sessions": [_running_session(sid="solo")]}, 200)
    tab._table.selectAll()
    tab._on_stop_selected()
    # The fallback QMessageBox.information should have fired.
    assert fallback_calls, "v0.2.94 fallback path didn't run"
