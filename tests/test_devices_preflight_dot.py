"""Per-device preflight dot in the Devices table (v0.2.78).

The dot is painted by ``DevicesTab._apply_preflight_dots`` driven by
``PreflightBar.by_device_updated`` (and a snapshot pull on table
rebuild). Standing up the full DevicesTab is heavy — instead these
tests cover the colour/strip logic as a pure helper, plus a
round-trip from PreflightBar signal to a mock subscriber.
"""

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QWidget


def _classify(stats: dict):
    """Mirror the severity-wins logic in
    DevicesTab._apply_preflight_dots so we can test it in isolation."""
    n_err = int((stats or {}).get("error", 0))
    n_warn = int((stats or {}).get("warning", 0))
    if n_err > 0:
        return "red", "#b91c1c"
    if n_warn > 0:
        return "amber", "#b45309"
    return "green", "#166534"


def test_classify_red_wins_over_warning():
    state, _ = _classify({"error": 1, "warning": 5, "ok": 2})
    assert state == "red"


def test_classify_amber_when_only_warnings():
    state, _ = _classify({"error": 0, "warning": 2, "ok": 4})
    assert state == "amber"


def test_classify_green_when_clean():
    state, _ = _classify({"error": 0, "warning": 0, "ok": 10})
    assert state == "green"


def test_classify_handles_empty_stats():
    state, _ = _classify({})
    assert state == "green"   # zero of everything → clean


# ─────────────────────────── strip + reapply behaviour
def _strip_dot(text: str) -> str:
    """Mirror the leading-dot strip in _apply_preflight_dots so
    repaints don't pile up emoji on the cell."""
    for prefix in ("●  ", "○  "):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def test_strip_dot_removes_known_prefixes():
    assert _strip_dot("●  R1") == "R1"
    assert _strip_dot("○  R2") == "R2"
    assert _strip_dot("R3") == "R3"


def test_strip_dot_idempotent_across_repaints():
    """Re-applying the dot N times must never produce '●  ●  ●  R1'."""
    name = "R1"
    for _ in range(5):
        stripped = _strip_dot(name)
        name = f"●  {stripped}"
    assert name == "●  R1"


# ─────────────────────────── bar → subscriber round-trip
def test_preflight_bar_signal_drives_subscriber(qapp, monkeypatch):
    """Wire the PreflightBar.by_device_updated signal to a callable
    (which is exactly what DevicesTab does) and confirm the snapshot
    arrives."""
    from widgets import preflight_bar as mod
    # Block the singleShot first-refresh by giving it a non-URL.
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("should not poll")))
    parent = QWidget()
    bar = mod.PreflightBar(lambda: None, parent=parent, poll_interval_ms=0)
    received = []
    bar.by_device_updated.connect(received.append)

    bar._apply_summary({
        "summary": {"error": 1, "warning": 0, "ok": 2, "total": 3},
        "findings": [],
        "by_device": {"R1": {"error": 1, "ok": 0},
                      "R2": {"ok": 2}},
    })
    assert received, "subscriber didn't get the snapshot"
    snap = received[-1]
    assert snap == {"R1": {"error": 1, "ok": 0},
                    "R2": {"ok": 2}}
    bar.stop()
