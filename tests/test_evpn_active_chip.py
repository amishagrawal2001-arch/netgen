"""EVPN active-injections chip (v0.2.78).

Lives on the VXLAN sub-tab header, polls /api/evpn/type2/list, shows
the count, and emits clicked() when clicked so the host can open the
EVPN Inject dialog.
"""

from types import SimpleNamespace

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget


@pytest.fixture
def make_chip(qapp, monkeypatch):
    parents: list[QWidget] = []
    chips: list = []

    def _make(server_url="http://1.1.1.1", payload=None, status=200):
        from widgets import evpn_active_chip as mod
        if payload is not None:
            monkeypatch.setattr(
                mod.requests, "get",
                lambda *a, **k: SimpleNamespace(
                    status_code=status, json=lambda: payload, text=""
                ),
            )
        else:
            monkeypatch.setattr(
                mod.requests, "get",
                lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError("no payload set")),
            )
        provider = (lambda: server_url) if server_url else (lambda: None)
        parent = QWidget()
        parents.append(parent)
        chip = mod.EvpnActiveChip(provider, parent=parent,
                                  poll_interval_ms=0)
        chips.append(chip)
        return chip, mod

    yield _make
    for c in chips:
        try:
            c.stop()
        except Exception:
            pass


def test_chip_starts_idle(qapp, monkeypatch):
    from widgets import evpn_active_chip as mod
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("should not poll")))
    parent = QWidget()  # noqa: F841
    chip = mod.EvpnActiveChip(lambda: None, parent=parent,
                              poll_interval_ms=0)
    assert chip.count() == 0
    assert "idle" in chip.text().lower()
    chip.stop()


def test_chip_shows_count_when_injections_exist(make_chip):
    chip, _ = make_chip(payload={
        "injections": [
            {"inject_id": "a", "kind": "type2"},
            {"inject_id": "b", "kind": "type2"},
            {"inject_id": "c", "kind": "type5"},
        ]
    })
    chip.refresh()
    assert chip.count() == 3
    assert "3 active" in chip.text()


def test_chip_falls_back_to_items_key(make_chip):
    """Some server versions return 'items' instead of 'injections'."""
    chip, _ = make_chip(payload={"items": [{"id": 1}, {"id": 2}]})
    chip.refresh()
    assert chip.count() == 2


def test_chip_silent_on_http_failure(make_chip, monkeypatch):
    """Flaky link → previous state held, no exception."""
    chip, mod = make_chip(payload={"injections": [{"id": 1}]})
    chip.refresh()
    assert chip.count() == 1
    def boom(*a, **k):
        raise ConnectionError("server down")
    monkeypatch.setattr(mod.requests, "get", boom)
    chip.refresh()
    assert chip.count() == 1  # held


def test_chip_silent_on_non_200(make_chip, monkeypatch):
    chip, mod = make_chip(payload={"injections": [{"id": 1}, {"id": 2}]})
    chip.refresh()
    assert chip.count() == 2
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: SimpleNamespace(
                            status_code=503, json=lambda: {}, text=""))
    chip.refresh()
    assert chip.count() == 2  # held


def test_chip_emits_clicked_signal(qapp, monkeypatch):
    from widgets import evpn_active_chip as mod
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("should not poll")))
    parent = QWidget()  # noqa: F841
    chip = mod.EvpnActiveChip(lambda: None, parent=parent,
                              poll_interval_ms=0)
    captured = []
    chip.clicked.connect(lambda: captured.append("clicked"))
    # Simulate a mouse-press event the way Qt fires it.
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtCore import QPointF
    ev = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(5, 5),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    chip.mousePressEvent(ev)
    assert captured == ["clicked"]
    chip.stop()


def test_chip_returns_idle_when_no_server(qapp, monkeypatch):
    """Empty provider → silent refresh, idle paint, no exception."""
    from widgets import evpn_active_chip as mod
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("should not poll")))
    parent = QWidget()  # noqa: F841
    chip = mod.EvpnActiveChip(lambda: None, parent=parent,
                              poll_interval_ms=0)
    chip.refresh()
    assert chip.count() == 0
    chip.stop()
