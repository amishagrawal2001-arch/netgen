"""Tests for the preflight GUI bar (v0.2.70).

Layers:
  * `_apply_summary` mapping a /api/preflight/check payload to pill
    counts, pill text + colours, and the bar-background tint.
  * Singular/plural correctness on the pill labels.
  * Details button enabled iff there are findings.
  * Auto-refresh wiring: timer is OFF when ``poll_interval_ms=0``, on
    otherwise.
  * `refresh()` is silent on HTTP failure (no exception, no modal).
  * Details dialog renders one row per finding, level cells colour-coded.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt5.QtWidgets import QWidget


@pytest.fixture
def make_bar(qapp, monkeypatch):
    """Factory returning a fresh PreflightBar with auto-polling OFF so
    tests drive `refresh()` explicitly. The server-URL provider returns
    a fixed test URL by default.

    The fixture keeps a strong reference to the parent QWidget — without
    it Python GC sweeps the parent, Qt cascade-deletes the children, and
    every later access blows up with "wrapped C/C++ object has been
    deleted."
    """
    from widgets import preflight_bar as mod

    parents: list[QWidget] = []
    bars: list = []

    def _make(server_url="http://1.1.1.1",
              poll_interval_ms=0,
              get_response=None):
        if get_response is not None:
            monkeypatch.setattr(mod.requests, "get",
                                lambda *a, **k: get_response)
        else:
            # Don't let the singleShot first-refresh hit the network —
            # default to "no server selected" which the bar treats as
            # a no-op.
            monkeypatch.setattr(mod.requests, "get",
                                lambda *a, **k: (_ for _ in ()).throw(
                                    AssertionError("no get_response set")))
        provider = (lambda: server_url) if server_url else (lambda: None)
        parent = QWidget()
        parents.append(parent)
        bar = mod.PreflightBar(provider, parent=parent,
                                poll_interval_ms=poll_interval_ms)
        bars.append(bar)
        return bar, mod

    yield _make

    # Stop timers + tear down in deterministic order before parents drop.
    for bar in bars:
        try:
            bar.stop()
        except Exception:
            pass
    bars.clear()
    parents.clear()


def _payload(error=0, warning=0, ok=0, findings=None):
    return {
        "summary": {"error": error, "warning": warning,
                    "ok": ok, "total": error + warning + ok},
        "findings": findings or [],
        "by_device": {},
    }


def _fake_get(payload, status=200):
    return SimpleNamespace(
        status_code=status,
        json=lambda: payload,
        text="",
    )


# ───────────────────────────────────────────── _apply_summary
def test_clean_summary_shows_green_only(make_bar):
    bar, _ = make_bar()
    bar._apply_summary(_payload(ok=5))
    assert "5 OK" in bar._ok_pill.text()
    assert "0 errors"   in bar._error_pill.text()
    assert "0 warnings" in bar._warning_pill.text()
    # Bar background tinted green when there are no problems.
    assert "#f0fdf4" in bar.styleSheet()


def test_warning_summary_tints_bar_amber(make_bar):
    bar, _ = make_bar()
    bar._apply_summary(_payload(warning=2, ok=3))
    assert "2 warnings" in bar._warning_pill.text()
    # Amber background for warnings (no errors).
    assert "#fffbeb" in bar.styleSheet()


def test_error_summary_tints_bar_red(make_bar):
    """Error severity wins — even if there are also warnings."""
    bar, _ = make_bar()
    bar._apply_summary(_payload(error=1, warning=2, ok=3))
    assert "1 error" in bar._error_pill.text()
    assert "#fef2f2" in bar.styleSheet()


def test_singular_vs_plural_in_pill_text(make_bar):
    """Operators notice when copy reads "1 errors" — pin the grammar."""
    bar, _ = make_bar()
    bar._apply_summary(_payload(error=1, warning=1, ok=1))
    assert "1 error,"   not in bar._error_pill.text()    # no s
    assert "1 errors"   not in bar._error_pill.text()
    assert "1 error"    in bar._error_pill.text()
    assert "1 warning"  in bar._warning_pill.text()
    assert "1 warnings" not in bar._warning_pill.text()

    bar._apply_summary(_payload(error=2, warning=2, ok=2))
    assert "2 errors"   in bar._error_pill.text()
    assert "2 warnings" in bar._warning_pill.text()


def test_details_button_disabled_when_no_findings(make_bar):
    bar, _ = make_bar()
    bar._apply_summary(_payload(ok=10))
    assert bar.details_btn.isEnabled() is False


def test_details_button_enabled_when_findings_exist(make_bar):
    bar, _ = make_bar()
    bar._apply_summary(_payload(
        error=1,
        findings=[{"level": "error", "code": "BGP_NO_REMOTE_ASN",
                   "device_name": "R1",
                   "message": "no remote ASN"}],
    ))
    assert bar.details_btn.isEnabled() is True


# ────────────────────────────────────────────────────── refresh()
def test_refresh_silent_on_http_exception(make_bar, monkeypatch):
    """Network failures must be invisible — log only, no modal, no
    exception. Pills stay in their previous state."""
    bar, mod = make_bar()
    # First populate with a known good payload.
    bar._apply_summary(_payload(error=1, warning=2, ok=3))
    snapshot_err = bar._error_pill.text()
    snapshot_warn = bar._warning_pill.text()
    snapshot_ok = bar._ok_pill.text()

    # Now make every request raise.
    def boom(*a, **k):
        raise ConnectionError("server is down")
    monkeypatch.setattr(mod.requests, "get", boom)

    # Should not raise.
    bar.refresh()

    # Pills unchanged.
    assert bar._error_pill.text()   == snapshot_err
    assert bar._warning_pill.text() == snapshot_warn
    assert bar._ok_pill.text()      == snapshot_ok


def test_refresh_no_op_when_no_server_url(make_bar, monkeypatch):
    """Empty server URL → silently do nothing (don't even hit the
    network)."""
    bar, mod = make_bar(server_url=None)
    called = {"get": False}
    def _spy(*a, **k):
        called["get"] = True
        return _fake_get(_payload())
    monkeypatch.setattr(mod.requests, "get", _spy)
    bar.refresh()
    assert called["get"] is False


def test_refresh_silent_on_http_400(make_bar):
    """Non-200 → silent. Don't tank the bar over a transient 503."""
    bar, _ = make_bar(get_response=_fake_get({"error": "boom"}, status=503))
    bar._apply_summary(_payload(error=5))   # known starting state
    bar.refresh()
    # Pills unchanged from the seed.
    assert "5 error" in bar._error_pill.text()


def test_refresh_populates_from_200(make_bar):
    bar, _ = make_bar(get_response=_fake_get(_payload(
        error=2, warning=3, ok=10,
        findings=[
            {"level": "error",   "code": "BGP_NO_REMOTE_ASN", "device_name": "R1"},
            {"level": "warning", "code": "VXLAN_EMPTY",       "device_name": "R2"},
        ],
    )))
    bar.refresh()
    assert "2 error"   in bar._error_pill.text()
    assert "3 warning" in bar._warning_pill.text()
    assert "10 OK"     in bar._ok_pill.text()
    assert bar.details_btn.isEnabled() is True


# ─────────────────────────────────────────────────── auto-polling
def test_timer_off_when_interval_zero(make_bar):
    bar, _ = make_bar(poll_interval_ms=0)
    assert bar._timer.isActive() is False


def test_timer_on_when_interval_set(qapp, monkeypatch):
    """Construct with a non-zero interval and confirm the timer is
    running. Don't actually wait for it to fire — we just need to
    know it's scheduled."""
    from widgets import preflight_bar as mod
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _fake_get(_payload()))
    parent = QWidget()  # noqa: F841  (kept alive to anchor the bar)
    bar = mod.PreflightBar(lambda: "http://1.1.1.1", parent=parent,
                           poll_interval_ms=5_000)
    try:
        assert bar._timer.isActive() is True
        assert bar._timer.interval() == 5_000
    finally:
        bar.stop()


# ────────────────────────────────────────────── kick_refresh helper
def test_kick_refresh_calls_bar_when_present():
    """The Apply success hook should fire the bar's refresh when the
    bar is wired up on the host object."""
    from widgets.preflight_bar import kick_refresh

    host = SimpleNamespace(preflight_bar=MagicMock())
    fired = kick_refresh(host)
    assert fired is True
    host.preflight_bar.refresh.assert_called_once_with()


def test_kick_refresh_silent_when_no_bar():
    """No preflight_bar attribute → no-op, no exception (Devices tab
    construction may have failed without us)."""
    from widgets.preflight_bar import kick_refresh

    host = SimpleNamespace()
    assert kick_refresh(host) is False


def test_kick_refresh_swallows_refresh_exception():
    """A refresh that raises must not bubble up to the Apply success
    handler (which has nothing useful to do with the error)."""
    from widgets.preflight_bar import kick_refresh

    bar = MagicMock()
    bar.refresh.side_effect = RuntimeError("boom")
    host = SimpleNamespace(preflight_bar=bar)
    assert kick_refresh(host) is False


def test_kick_refresh_custom_attr():
    """The attr name is overridable so future call sites can host the
    bar under a different name without touching the helper."""
    from widgets.preflight_bar import kick_refresh

    host = SimpleNamespace(my_bar=MagicMock())
    assert kick_refresh(host, attr="my_bar") is True
    host.my_bar.refresh.assert_called_once_with()


# ─────────────────────────────────────────────── details dialog
def test_details_dialog_renders_one_row_per_finding(qapp):
    from widgets.preflight_bar import PreflightDetailsDialog
    findings = [
        {"level": "error", "code": "BGP_NO_REMOTE_ASN",
         "device_name": "R1", "interface": "ens1f0",
         "message": "no remote ASN"},
        {"level": "warning", "code": "VXLAN_EMPTY",
         "device_name": "R2", "interface": None,
         "message": "vxlan_config present but empty"},
    ]
    parent = QWidget()  # noqa: F841  (kept alive to anchor the dialog)
    dlg = PreflightDetailsDialog(findings, parent=parent)
    assert dlg.table.rowCount() == 2
    # Level cell carries the upper-cased word (visual emphasis).
    assert dlg.table.item(0, 0).text() == "ERROR"
    assert dlg.table.item(1, 0).text() == "WARNING"
    # Missing interface renders as em-dash (no Python "None" leakage).
    assert dlg.table.item(1, 3).text() == "—"
    # The level colour is per-level so the eye picks up errors first.
    from PyQt5.QtGui import QColor
    assert dlg.table.item(0, 0).foreground().color() == QColor("#b91c1c")
    assert dlg.table.item(1, 0).foreground().color() == QColor("#b45309")
