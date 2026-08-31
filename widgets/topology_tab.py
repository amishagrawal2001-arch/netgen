"""Topology tab — IXIA IXNetwork-style fabric view.

Modelled after IXNetwork's topology canvas:

  * **Port lane** at the bottom — one badge per unique server-side
    interface (e.g. eth0, ens1). Devices visually sit on top of the
    port they bind to, like emulated DUTs on a test port.
  * **Device cards** stacked above their port: header with device
    name + instance count, a vertical stack of protocol chips
    (ETH → IPv4 → IPv6 → BGP → OSPF → IS-IS → DHCP) colour-coded
    by status, and a status LED indicating the rolled-up health.
  * **Cables** from each device card down to its port (thick green).
  * **Protocol edges** between device cards for BGP / OSPF / IS-IS
    peerings, dashed for the IGPs, solid grey for BGP, offset
    perpendicular when multiple protocols share a pair.
  * **Right-side property panel** that updates on selection — shows
    device metadata, per-protocol state, recent state-history rows.
  * **Layout toggle** — Hierarchical (ports-at-bottom, default) vs.
    Circular (legacy MVP layout). Persisted via QSettings.

All read-only; no drag-from-palette creation workflow. To add or
change devices, use the Devices tab — this view auto-reflects changes
on the next Refresh.
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from PyQt5.QtCore import Qt, QRectF, QPointF, QSettings, QThread, pyqtSignal
from PyQt5.QtGui import (
    QBrush, QColor, QFont, QPainter, QPainterPath, QPen,
)
from PyQt5.QtWidgets import (
    QGraphicsItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
    QGraphicsRectItem, QGraphicsTextItem, QGraphicsPathItem,
    QGraphicsEllipseItem,
    QSplitter, QFrame, QComboBox, QSizePolicy, QMessageBox,
    QTextEdit, QScrollArea,
)


logger = logging.getLogger(__name__)


# ====================================================================
# Visual constants — keep the IXIA-ish palette in one place so it's
# easy to retheme later.
# ====================================================================

COLOR_BG               = "#f5f5f7"
COLOR_CARD_BORDER      = "#1f2937"
COLOR_CARD_FILL        = "#ffffff"
COLOR_PORT_FILL        = "#14532d"   # IXIA-green port badge
COLOR_PORT_TEXT        = "#f0fdf4"
COLOR_CABLE            = "#16a34a"
COLOR_LED_UP           = "#16a34a"
COLOR_LED_PARTIAL      = "#d97706"
COLOR_LED_DOWN         = "#dc2626"
COLOR_LED_IDLE         = "#94a3b8"

# Protocol chip colours — each chip is keyed to its "configured-and-
# up" state. The fill comes from a (configured, up) tuple lookup.
CHIP_PALETTE: Dict[str, Tuple[str, str, str]] = {
    # protocol_key: (border, fill_idle, fill_up)
    "ETH":  ("#475569", "#e2e8f0", "#cbd5e1"),
    "IPv4": ("#1d4ed8", "#dbeafe", "#bfdbfe"),
    "IPv6": ("#4338ca", "#e0e7ff", "#c7d2fe"),
    "BGP":  ("#a16207", "#fef3c7", "#bbf7d0"),
    "OSPF": ("#7c3aed", "#ede9fe", "#bbf7d0"),
    "ISIS": ("#0891b2", "#cffafe", "#bbf7d0"),
    "DHCP": ("#be185d", "#fce7f3", "#bbf7d0"),
}

# Up-state strings every protocol uses (broad enough to catch FRR,
# legacy OSTG, and ARP variants).
UP_STATES = {
    "established", "full", "up", "resolved", "leased", "running",
    # v0.5.229 (audit U monitor-4): DHCP server-mode healthy state is
    # "Server Running" (see utils/dhcp.py:2133 and
    # utils/dhcp_monitor.py) — case-lowered here to match `_is_state_up`
    # which lowercases both operands. Pre-fix, a healthy DHCP server
    # chip rendered red on the Topology canvas because "server running"
    # wasn't recognised as up.
    "server running",
}


# ====================================================================
# Background fetch worker
# ====================================================================


# ====================================================================
# QGraphicsView subclass — handles wheel zoom + Ctrl-+/Ctrl-- shortcuts.
#
# We *must* subclass to override wheelEvent — assigning a Python function
# to `self._view.wheelEvent = ...` on the instance does NOT intercept the
# C++ virtual dispatch in Qt, so the original wheel-handler (vertical
# scroll) was still firing. Subclass + real override fixes it.
# ====================================================================


class _TopologyView(QGraphicsView):
    """QGraphicsView with cursor-anchored wheel zoom + min/max bounds."""

    MIN_SCALE = 0.2
    MAX_SCALE = 6.0

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._current_scale = 1.0
        # AnchorUnderMouse keeps the point under the cursor stationary
        # as we scale — feels like IXNetwork / Google Maps.
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

    def wheelEvent(self, event):
        # Compute new total scale; clamp so users can't zoom into
        # subatomic territory or out to where everything's a dot.
        angle = event.angleDelta().y()
        if angle == 0:
            return
        step = 1.15 if angle > 0 else 1 / 1.15
        new_scale = self._current_scale * step
        if new_scale < self.MIN_SCALE or new_scale > self.MAX_SCALE:
            event.accept()
            return
        self._current_scale = new_scale
        self.scale(step, step)
        event.accept()   # swallow so the default vertical-scroll behaviour doesn't also fire

    def zoom_in(self):
        """Programmatic zoom-in (for toolbar buttons / shortcuts)."""
        if self._current_scale * 1.15 > self.MAX_SCALE:
            return
        self._current_scale *= 1.15
        self.scale(1.15, 1.15)

    def zoom_out(self):
        if self._current_scale / 1.15 < self.MIN_SCALE:
            return
        self._current_scale /= 1.15
        self.scale(1 / 1.15, 1 / 1.15)

    def zoom_reset(self):
        """Restore scale to 1.0 — useful when the user has zoomed
        themselves into a corner and just wants to start over."""
        if self._current_scale == 1.0:
            return
        inverse = 1.0 / self._current_scale
        self.scale(inverse, inverse)
        self._current_scale = 1.0


class _JsonFetchWorker(QThread):
    """Background HTTP-GET worker so the GUI stays responsive while
    /api/device/database/devices resolves. See git history for the
    rationale on the dangling-handle pattern fixed in _on_worker_finished."""

    finished_ok = pyqtSignal(object, int)
    failed = pyqtSignal(str)

    def __init__(self, url: str, timeout_s: float = 5.0):
        super().__init__()
        self._url = url
        self._timeout = timeout_s

    def run(self):
        try:
            r = requests.get(self._url, timeout=self._timeout)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        if r.status_code != 200:
            self.failed.emit(f"HTTP {r.status_code}: {r.text[:200]}")
            return
        try:
            self.finished_ok.emit(r.json(), r.status_code)
        except Exception as exc:
            self.failed.emit(f"bad JSON: {exc}")


# ====================================================================
# Status helpers
# ====================================================================


def _is_state_up(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower() in UP_STATES


def _device_overall_health(row: Dict[str, Any]) -> str:
    """Bucket the device into one of: 'up' / 'partial' / 'down' / 'idle'.

    * up      — every configured protocol reports an up state
    * partial — some up, some not
    * down    — configured protocols, none up
    * idle    — nothing configured to begin with
    """
    configured = []
    up = []
    protos = row.get("protocols") or []
    if isinstance(protos, str):
        try:
            protos = json.loads(protos)
        except Exception:
            protos = []
    if not isinstance(protos, list):
        protos = []

    proto_to_state_keys = {
        "bgp":  ("bgp_ipv4_state", "bgp_ipv6_state"),
        "ospf": ("ospf_state",),
        "isis": ("isis_state",),
        "arp":  ("arp_status",),
        "dhcp": ("dhcp_state",),
    }
    # Always count ARP since every applied device should resolve it.
    if row.get("arp_status") or row.get("status") == "Running":
        configured.append("arp")
        if _is_state_up(row.get("arp_status")):
            up.append("arp")

    for p in protos:
        if not isinstance(p, str):
            continue
        pkey = p.strip().lower()
        if pkey in proto_to_state_keys:
            configured.append(pkey)
            for sk in proto_to_state_keys[pkey]:
                if _is_state_up(row.get(sk)):
                    up.append(pkey)
                    break

    if not configured:
        return "idle"
    if len(up) == len(configured):
        return "up"
    if not up:
        return "down"
    return "partial"


def _led_color(health: str) -> QColor:
    return QColor({
        "up":      COLOR_LED_UP,
        "partial": COLOR_LED_PARTIAL,
        "down":    COLOR_LED_DOWN,
        "idle":    COLOR_LED_IDLE,
    }.get(health, COLOR_LED_IDLE))


# ====================================================================
# Port badge (bottom lane)
# ====================================================================


class _PortBadge(QGraphicsRectItem):
    """The green port pill at the bottom of each device-stack column.

    Plays the role of an IXNetwork "Test Port" — devices visually
    "plug into" it via a cable to communicate the parent-child
    relationship (this interface hosts these devices).
    """

    WIDTH = 200
    HEIGHT = 36

    def __init__(self, interface: str, device_ids: List[str]):
        super().__init__(0, 0, self.WIDTH, self.HEIGHT)
        self._interface = interface
        self._device_ids = device_ids
        self.setBrush(QBrush(QColor(COLOR_PORT_FILL)))
        self.setPen(QPen(QColor(COLOR_PORT_FILL).darker(120), 1.5))
        self.setZValue(-2)

        label = QGraphicsTextItem(self)
        label.setDefaultTextColor(QColor(COLOR_PORT_TEXT))
        label.setHtml(
            f"<div style='text-align:center'>"
            f"<b>● {interface}</b>"
            f"<br/><span style='font-size:9pt;'>{len(device_ids)} device"
            f"{'s' if len(device_ids) != 1 else ''}</span></div>"
        )
        label.setTextWidth(self.WIDTH - 8)
        # Vertical-center the label inside the rect.
        br = label.boundingRect()
        label.setPos((self.WIDTH - br.width()) / 2,
                     (self.HEIGHT - br.height()) / 2)


# ====================================================================
# Device card — protocol-stack + LED, à la IXNetwork DeviceGroup
# ====================================================================


class _DeviceCard(QGraphicsRectItem):
    """One device, rendered IXNetwork-style.

    Implementation note: we use a QGraphicsRectItem (the card itself
    *is* the background rect) with parented child items, rather than
    a QGraphicsItemGroup. Newer PyQt5 builds dropped
    `setHandlesChildEvents()` from QGraphicsItemGroup so clicks on a
    child item (text label, chip rect) wouldn't route back to the
    group for selection/drag. With a parent-rect model, children
    inherit position from the parent and events on the rect work as
    expected because the rect is the topmost interactive surface
    in its area.

    Composition (top → bottom):
      [LED]  device_name                       [x N]   ← header
      ─────────────────────────────────────────────
      ETH   eth0 / MAC                                  ← chip
      IPv4  10.0.0.1                                    ← chip
      IPv6  fd00::1                                     ← chip (if any)
      BGP   Established (v4+v6)                         ← chip
      OSPF  Full                                        ← chip
      ISIS  Up                                          ← chip
      DHCP  Leased                                      ← chip
    """

    WIDTH = 240
    HEADER_HEIGHT = 32
    CHIP_HEIGHT = 22
    CHIP_VGAP = 4
    PADDING = 8

    def __init__(self, row: Dict[str, Any], parent_tab: "TopologyTab"):
        # Compute height up front since super().__init__ needs the rect.
        chips = self._collect_chips(row)
        self._height = (
            self.HEADER_HEIGHT
            + len(chips) * (self.CHIP_HEIGHT + self.CHIP_VGAP)
            + self.PADDING
        )
        super().__init__(0, 0, self.WIDTH, self._height)
        self._row = row
        self._parent_tab = parent_tab
        self._device_id = row.get("device_id") or ""

        # Card visual — the rect is the card itself.
        self.setBrush(QBrush(QColor(COLOR_CARD_FILL)))
        self.setPen(QPen(QColor(COLOR_CARD_BORDER), 1.5))

        # Draggable / selectable; emit position changes so cables and
        # peer edges can track the move in real time. With children
        # parented to this rect, drags propagate naturally.
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsScenePositionChanges, True)

        # Header divider line — parented to the rect, so it moves with us.
        sep = QGraphicsRectItem(0, self.HEADER_HEIGHT, self.WIDTH, 1, self)
        sep.setBrush(QBrush(QColor("#e5e7eb")))
        sep.setPen(QPen(Qt.NoPen))
        # Make children mouse-transparent so clicks on labels / chips
        # also count as a click on the card itself. Without this, the
        # text item over the LED could eat the press and selection
        # wouldn't toggle.
        sep.setAcceptedMouseButtons(Qt.NoButton)

        # Status LED
        health = _device_overall_health(row)
        led = QGraphicsEllipseItem(8, 10, 12, 12, self)
        led.setBrush(QBrush(_led_color(health)))
        led.setPen(QPen(QColor("#0f172a"), 0.75))
        led.setToolTip(f"Status: {health}")
        led.setAcceptedMouseButtons(Qt.NoButton)

        # Device name (header)
        name = row.get("device_name") or self._device_id or "?"
        if len(name) > 22:
            name = name[:19] + "…"
        title = QGraphicsTextItem(self)
        title.setHtml(f"<b>{name}</b>")
        title.setPos(26, 6)
        title.setAcceptedMouseButtons(Qt.NoButton)

        # Multiplicity badge (top-right) — when scale_count > 1.
        count = 1
        for k in ("scale_count", "device_count", "instances", "count"):
            v = row.get(k)
            if isinstance(v, int) and v > 1:
                count = v
                break
        if count > 1:
            badge = QGraphicsRectItem(self.WIDTH - 44, 6, 36, 20, self)
            badge.setBrush(QBrush(QColor("#1f2937")))
            badge.setPen(QPen(Qt.NoPen))
            badge.setAcceptedMouseButtons(Qt.NoButton)
            badge_text = QGraphicsTextItem(self)
            badge_text.setHtml(
                f"<span style='color:#fff;font-size:9pt;font-weight:600;'>"
                f"x{count}</span>"
            )
            br = badge_text.boundingRect()
            badge_text.setPos(
                self.WIDTH - 44 + (36 - br.width()) / 2,
                6 + (20 - br.height()) / 2,
            )
            badge_text.setAcceptedMouseButtons(Qt.NoButton)

        # Protocol chips
        y = self.HEADER_HEIGHT + 6
        for proto, line, is_up in chips:
            border_c, fill_idle, fill_up = CHIP_PALETTE.get(
                proto, ("#475569", "#e2e8f0", "#dcfce7")
            )
            chip = QGraphicsRectItem(
                self.PADDING, y,
                self.WIDTH - 2 * self.PADDING, self.CHIP_HEIGHT,
                self,
            )
            chip.setBrush(QBrush(QColor(fill_up if is_up else fill_idle)))
            chip.setPen(QPen(QColor(border_c), 1))
            chip.setAcceptedMouseButtons(Qt.NoButton)

            chip_label = QGraphicsTextItem(self)
            chip_label.setHtml(
                f"<span style='font-size:9pt;'>"
                f"<b style='color:{border_c}'>{proto}</b>"
                f"&nbsp;&nbsp;{line}</span>"
            )
            chip_label.setPos(self.PADDING + 6, y + 2)
            chip_label.setAcceptedMouseButtons(Qt.NoButton)

            y += self.CHIP_HEIGHT + self.CHIP_VGAP

        # Tooltip = full row summary
        self.setToolTip(
            f"{name}\n{self._device_id}\nhealth={health}\n"
            f"interface={row.get('server_interface') or row.get('interface') or '—'}"
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _collect_chips(row: Dict[str, Any]) -> List[Tuple[str, str, bool]]:
        """Build the chip list for this row: returns (proto_key,
        display_line, is_up_flag). Order is intentional — Ethernet
        first, IGP last — to mirror the OSI-style stack IXIA shows."""
        chips: List[Tuple[str, str, bool]] = []

        # ETH — always show, source of truth = MAC.
        mac = row.get("mac_address") or row.get("mac") or "—"
        iface = row.get("server_interface") or row.get("interface") or "—"
        chips.append(("ETH", f"{iface} · {mac}", True))

        # IPv4
        ipv4 = row.get("ipv4") or row.get("ipv4_address")
        if ipv4:
            mask = row.get("ipv4_mask") or row.get("subnet_mask")
            chips.append(("IPv4", f"{ipv4}{'/' + str(mask) if mask else ''}",
                          bool(row.get("arp_ipv4_resolved"))))

        # IPv6
        ipv6 = row.get("ipv6") or row.get("ipv6_address")
        if ipv6:
            chips.append(("IPv6", str(ipv6),
                          bool(row.get("arp_ipv6_resolved"))))

        # Decode the protocols list once for the IGP/EGP chips
        protos = row.get("protocols") or []
        if isinstance(protos, str):
            try:
                protos = json.loads(protos)
            except Exception:
                protos = []
        proto_set = {p.lower() for p in protos if isinstance(p, str)}

        if "bgp" in proto_set:
            v4 = row.get("bgp_ipv4_state") or "—"
            v6 = row.get("bgp_ipv6_state")
            line = f"v4: {v4}" + (f" · v6: {v6}" if v6 else "")
            chips.append(("BGP", line,
                          _is_state_up(row.get("bgp_ipv4_state"))
                          or _is_state_up(row.get("bgp_ipv6_state"))))
        if "ospf" in proto_set:
            chips.append(("OSPF", str(row.get("ospf_state") or "—"),
                          _is_state_up(row.get("ospf_state"))))
        if "isis" in proto_set:
            chips.append(("ISIS", str(row.get("isis_state") or "—"),
                          _is_state_up(row.get("isis_state"))))
        if row.get("dhcp_state") or row.get("dhcp_running"):
            chips.append(("DHCP", str(row.get("dhcp_state") or "—"),
                          _is_state_up(row.get("dhcp_state"))))

        return chips

    # ------------------------------------------------------------------
    def itemChange(self, change, value):
        # Trigger cable + edge redraw on every position change so the
        # links track in real time as the user drags.
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._parent_tab._redraw_links()
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            # Selection drives the right-side property panel.
            try:
                if bool(value):
                    self._parent_tab._on_card_selected(self._row)
            except Exception as _exc:
                logger.debug(f"[TOPOLOGY] selection signal failed: {_exc}")
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        try:
            self._parent_tab._open_config_viewer(self._row)
        except Exception as exc:
            logger.debug(f"[TOPOLOGY] viewer open failed: {exc}")
        super().mouseDoubleClickEvent(event)


# ====================================================================
# Right-side property panel
# ====================================================================


class _PropertyPanel(QWidget):
    """Selection-driven detail view. Lives to the right of the canvas
    so operators can poke a node and see the underlying config + last
    state transitions without leaving the tab.

    Renders three sections:
      * device metadata block (name / ID / interface / IPs)
      * per-protocol detail (peer addresses, AS, state) for each
        protocol configured on the device
      * recent state-history rows (best-effort — pulled from
        /api/device/database/devices/<id>/history when shown)
    """

    def __init__(self, server_url_getter, parent=None):
        super().__init__(parent)
        self._server_url_getter = server_url_getter
        self.setMinimumWidth(280)
        self.setMaximumWidth(420)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        title = QLabel("<b>Properties</b>")
        title.setStyleSheet("color: #1f2937; font-size: 13px;")
        v.addWidget(title)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        v.addWidget(self._scroll, 1)

        self._inner = QWidget()
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(self._inner)

        self.show_placeholder()

    # ------------------------------------------------------------------
    def _clear(self):
        while self._inner_layout.count():
            item = self._inner_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def show_placeholder(self):
        self._clear()
        ph = QLabel(
            "Select a device on the canvas to see its properties, "
            "protocol state, and recent state-history rows here."
        )
        ph.setWordWrap(True)
        ph.setStyleSheet("color: #6b7280; padding: 8px;")
        self._inner_layout.addWidget(ph)
        self._inner_layout.addStretch(1)

    def show_device(self, row: Dict[str, Any]):
        self._clear()

        # ---- Metadata block ----
        meta_html_rows = []
        for key, label in (
            ("device_name", "Name"),
            ("device_id",   "ID"),
            ("server_interface", "Server interface"),
            ("ipv4",        "IPv4"),
            ("ipv6",        "IPv6"),
            ("mac_address", "MAC"),
            ("loopback_ipv4", "Loopback v4"),
            ("vlan",        "VLAN"),
            ("status",      "Status"),
        ):
            v = row.get(key)
            if v in (None, ""):
                continue
            meta_html_rows.append(
                f"<tr><td style='color:#6b7280;padding:2px 8px 2px 0;'>"
                f"{label}</td><td><code>{v}</code></td></tr>"
            )
        meta_box = QLabel(
            "<table style='font-size:11px;'>"
            + "".join(meta_html_rows) + "</table>"
        )
        meta_box.setTextFormat(Qt.RichText)
        meta_box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        meta_box.setWordWrap(True)
        self._inner_layout.addWidget(meta_box)

        # ---- Per-protocol block ----
        protos = row.get("protocols") or []
        if isinstance(protos, str):
            try:
                protos = json.loads(protos)
            except Exception:
                protos = []
        proto_set = {p.lower() for p in protos if isinstance(p, str)}

        for proto in ("bgp", "ospf", "isis", "dhcp"):
            if proto not in proto_set and not row.get(f"{proto}_state"):
                continue
            self._inner_layout.addWidget(self._make_section(
                proto.upper(),
                self._protocol_summary(proto, row),
            ))

        # ---- Recent state-history block ----
        history_section = self._make_section("Recent transitions", "Loading…")
        self._inner_layout.addWidget(history_section)
        self._inner_layout.addStretch(1)

        # Fire off a background fetch for the history rows; refresh
        # the section in-place when it comes back.
        self._fetch_history_async(row.get("device_id") or "", history_section)

    def _make_section(self, title: str, content_html: str) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background: #f9fafb; border: 1px solid #e5e7eb; "
            "border-radius: 4px; margin-top: 6px; }"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(6, 4, 6, 6)
        hdr = QLabel(f"<b style='color:#1f2937;font-size:11px;'>{title}</b>")
        lay.addWidget(hdr)
        body = QLabel(content_html)
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet("font-size: 11px;")
        lay.addWidget(body)
        # Stash the body label so the async history fetch can update it.
        frame._body = body
        return frame

    @staticmethod
    def _protocol_summary(proto: str, row: Dict[str, Any]) -> str:
        bits: List[str] = []
        if proto == "bgp":
            for k, lbl in (
                ("bgp_ipv4_state", "v4 state"),
                ("bgp_ipv6_state", "v6 state"),
                ("bgp_as",         "Local AS"),
                ("bgp_peer_as",    "Peer AS"),
                ("bgp_peer_ip",    "Peer IP"),
                ("bgp_router_id",  "Router-ID"),
            ):
                v = row.get(k)
                if v:
                    bits.append(f"<b>{lbl}:</b> {v}")
        elif proto == "ospf":
            for k, lbl in (
                ("ospf_state",     "State"),
                ("ospf_area",      "Area"),
                ("ospf_router_id", "Router-ID"),
            ):
                v = row.get(k)
                if v:
                    bits.append(f"<b>{lbl}:</b> {v}")
        elif proto == "isis":
            for k, lbl in (
                ("isis_state",     "State"),
                ("isis_system_id", "System ID"),
                ("isis_net",       "NET"),
            ):
                v = row.get(k)
                if v:
                    bits.append(f"<b>{lbl}:</b> {v}")
        elif proto == "dhcp":
            for k, lbl in (
                ("dhcp_state",    "State"),
                ("dhcp_lease_ip", "Lease IP"),
                ("dhcp_running",  "Running"),
            ):
                v = row.get(k)
                if v not in (None, ""):
                    bits.append(f"<b>{lbl}:</b> {v}")
        return "<br/>".join(bits) if bits else "<i style='color:#6b7280;'>no state yet</i>"

    # ------------------------------------------------------------------
    def _fetch_history_async(self, device_id: str, section_frame: QFrame):
        if not device_id:
            section_frame._body.setText(
                "<i style='color:#6b7280;'>device not yet applied</i>"
            )
            return
        url_base = self._server_url_getter()
        if not url_base:
            section_frame._body.setText(
                "<i style='color:#6b7280;'>no server URL</i>"
            )
            return

        # Tiny inline worker — same pattern as the canvas refresh.
        worker = _JsonFetchWorker(
            f"{url_base}/api/device/database/devices/{device_id}/history?limit=10",
            timeout_s=3.0,
        )

        def _ok(payload, _code):
            rows = (payload or {}).get("history") or []
            if not rows:
                section_frame._body.setText(
                    "<i style='color:#6b7280;'>no transitions yet</i>"
                )
                return
            lines = []
            for r in rows[:10]:
                ts = str(r.get("timestamp", ""))[:19].replace("T", " ")
                proto = (r.get("protocol") or "").upper()
                state = r.get("state") or ""
                lines.append(
                    f"<span style='color:#6b7280;'>{ts}</span> "
                    f"<b style='color:#1f2937;'>{proto}</b> &rarr; {state}"
                )
            section_frame._body.setText("<br/>".join(lines))

        def _fail(msg):
            section_frame._body.setText(
                f"<i style='color:#dc2626;'>fetch failed: {msg[:80]}</i>"
            )

        worker.finished_ok.connect(_ok)
        worker.failed.connect(_fail)
        # Lifetime owned by the global keepalive registry — do NOT
        # connect finished→deleteLater (destroys the C++ QThread mid-
        # teardown → SIGABRT on PyQt5 5.15.11 + Python 3.14). See
        # utils/qthread_keepalive.py.
        try:
            from utils.qthread_keepalive import keep
            keep(worker)
        except Exception:
            pass
        # Keep a handle on the frame too (harmless belt-and-suspenders).
        section_frame._history_worker = worker
        worker.start()


# ====================================================================
# Main tab
# ====================================================================


class TopologyTab(QWidget):
    """The Topology view — port lane + device cards + property panel."""

    LAYOUT_HIERARCHICAL = "hierarchical"
    LAYOUT_CIRCULAR     = "circular"

    def __init__(self, parent_window):
        super().__init__(parent_window)
        self._parent_window = parent_window
        self._cards: Dict[str, _DeviceCard] = {}
        self._ports: Dict[str, _PortBadge] = {}
        self._edges: List[QGraphicsPathItem] = []   # peer protocol edges
        self._cables: List[QGraphicsPathItem] = []  # device → port lines
        self._rows_cache: List[Dict[str, Any]] = []
        self._settings = QSettings("Netgen", "TopologyLayout")
        self._layout_mode = self._settings.value(
            "layout_mode", self.LAYOUT_HIERARCHICAL,
        )
        if self._layout_mode not in (self.LAYOUT_HIERARCHICAL,
                                     self.LAYOUT_CIRCULAR):
            self._layout_mode = self.LAYOUT_HIERARCHICAL
        self._rebuilding = False
        self._fetch_worker: Optional[QThread] = None
        # SSE worker — connects on first refresh() and stays connected.
        # Live state-transition events trigger a coalesced re-fetch so
        # the canvas stays in sync without polling.
        self._sse_worker: Optional[QThread] = None
        self._sse_refresh_pending = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ---- Toolbar ----
        outer.addLayout(self._build_toolbar())

        # ---- Splitter: canvas | property panel ----
        self._splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(self._splitter, 1)

        # Canvas — _TopologyView is a QGraphicsView subclass that
        # properly overrides wheelEvent for cursor-anchored zoom (an
        # instance-attribute monkey-patch is silently bypassed by Qt's
        # C++ virtual dispatch).
        self._scene = QGraphicsScene(self)
        self._scene.setBackgroundBrush(QBrush(QColor(COLOR_BG)))
        self._scene.setSceneRect(-3000, -3000, 6000, 6000)
        self._view = _TopologyView(self._scene)
        self._view.setRenderHint(QPainter.Antialiasing, True)
        self._view.setDragMode(QGraphicsView.ScrollHandDrag)
        self._view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._view.setStyleSheet(
            "QGraphicsView { background: " + COLOR_BG +
            "; border: 1px solid #d1d5db; }"
        )
        self._splitter.addWidget(self._view)

        # Property panel
        self._panel = _PropertyPanel(self._get_server_url, self)
        self._panel.setStyleSheet(
            "QWidget { background: #ffffff; border-left: 1px solid #e5e7eb; }"
        )
        self._splitter.addWidget(self._panel)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([900, 320])

        # Hint footer
        hint = QLabel(
            "Drag empty canvas to pan · drag a card to reposition · "
            "click to inspect · double-click to view full config · "
            "scroll wheel (or +/- buttons) to zoom."
        )
        hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        outer.addWidget(hint)

    # ------------------------------------------------------------------
    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self._refresh_btn)

        self._relayout_btn = QPushButton("Reset layout")
        self._relayout_btn.setToolTip(
            "Clear saved positions and re-run the current layout algorithm."
        )
        self._relayout_btn.clicked.connect(self._reset_layout)
        toolbar.addWidget(self._relayout_btn)

        # Zoom controls — fallback for hosts where the wheel is bound
        # to something else (touchpads in scroll-only mode, etc.).
        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedWidth(28)
        zoom_in_btn.setToolTip("Zoom in")
        zoom_in_btn.clicked.connect(lambda: self._view.zoom_in())
        toolbar.addWidget(zoom_in_btn)

        zoom_out_btn = QPushButton("−")
        zoom_out_btn.setFixedWidth(28)
        zoom_out_btn.setToolTip("Zoom out")
        zoom_out_btn.clicked.connect(lambda: self._view.zoom_out())
        toolbar.addWidget(zoom_out_btn)

        zoom_fit_btn = QPushButton("Fit")
        zoom_fit_btn.setFixedWidth(40)
        zoom_fit_btn.setToolTip("Fit all devices in view")
        zoom_fit_btn.clicked.connect(self._zoom_fit)
        toolbar.addWidget(zoom_fit_btn)

        toolbar.addWidget(QLabel("Layout:"))
        self._layout_combo = QComboBox()
        self._layout_combo.addItem("Hierarchical (port-based)", self.LAYOUT_HIERARCHICAL)
        self._layout_combo.addItem("Circular", self.LAYOUT_CIRCULAR)
        idx = self._layout_combo.findData(self._layout_mode)
        if idx >= 0:
            self._layout_combo.setCurrentIndex(idx)
        self._layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        toolbar.addWidget(self._layout_combo)

        self._info_label = QLabel("No devices loaded.")
        self._info_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        toolbar.addWidget(self._info_label)

        toolbar.addStretch(1)

        legend = QLabel(
            "<span style='color:#16a34a;'>●</span> up &nbsp;"
            "<span style='color:#d97706;'>●</span> partial &nbsp;"
            "<span style='color:#dc2626;'>●</span> down &nbsp;"
            "<span style='color:#94a3b8;'>●</span> idle &nbsp;|&nbsp; "
            "<span style='color:#16a34a;'>━</span> cable &nbsp;"
            "<span style='color:#6b7280;'>━</span> BGP &nbsp;"
            "<span style='color:#7c3aed;'>┄</span> OSPF &nbsp;"
            "<span style='color:#0891b2;'>┄</span> IS-IS"
        )
        legend.setTextFormat(Qt.RichText)
        toolbar.addWidget(legend)
        return toolbar

    # ------------------------------------------------------------------
    # Server-URL resolver, refresh, and dangling-handle-safe finish hook
    # ------------------------------------------------------------------
    def _get_server_url(self) -> Optional[str]:
        try:
            dt = getattr(self._parent_window, "devices_tab", None)
            if dt and hasattr(dt, "get_server_url"):
                return dt.get_server_url(silent=True)
        except Exception:
            pass
        return os.environ.get("NETGEN_SERVER_URL", "http://localhost:5050").rstrip("/")

    def refresh(self):
        url = self._get_server_url()
        if not url:
            self._info_label.setText("No server URL configured.")
            return
        existing = getattr(self, "_fetch_worker", None)
        if existing is not None:
            try:
                if existing.isRunning():
                    return
            except RuntimeError:
                self._fetch_worker = None
        self._info_label.setText("Loading…")
        self._refresh_btn.setEnabled(False)

        worker = _JsonFetchWorker(f"{url}/api/device/database/devices", timeout_s=5.0)
        worker.finished_ok.connect(self._on_refresh_ok)
        worker.failed.connect(self._on_refresh_failed)
        worker.finished.connect(self._on_worker_finished)
        self._fetch_worker = worker
        worker.start()

    def _on_worker_finished(self):
        try:
            self._refresh_btn.setEnabled(True)
        except Exception:
            pass
        # Clear the guard only. Do NOT deleteLater() here — see
        # _on_worker_finished in l2_emulation_tab.py for why (QThread
        # teardown race → SIGABRT). The global keepalive registry owns
        # the worker's lifetime and trims it safely >30s after finish.
        self._fetch_worker = None

    def _on_refresh_ok(self, payload, _code: int):
        rows = (payload or {}).get("devices") or []
        self._rows_cache = rows
        self._rebuild(rows)
        self._info_label.setText(
            f"{len(rows)} device(s) · {len(self._edges)} peering(s) · "
            f"{len(self._ports)} port(s)"
        )
        # Spin up the SSE worker once we know the server URL works.
        # Live state-transition events will trigger a coalesced refresh
        # of the canvas so LED colours and chip backgrounds stay current
        # without operator intervention.
        self._ensure_sse_worker()

    def _on_refresh_failed(self, msg: str):
        self._info_label.setText(f"Fetch failed: {msg[:120]}")

    # ------------------------------------------------------------------
    # SSE live-updates
    # ------------------------------------------------------------------
    def _ensure_sse_worker(self):
        """Start the SSE consumer if not already running. Reused across
        refresh() calls so we don't pile up multiple workers if the
        operator clicks Refresh repeatedly."""
        try:
            if self._sse_worker is not None:
                if self._sse_worker.isRunning():
                    return
                # Tombstoned worker handle — let go and start fresh.
                self._sse_worker = None
        except RuntimeError:
            self._sse_worker = None
        url = self._get_server_url()
        if not url:
            return
        try:
            from utils.sse_client import SSEWorker
        except Exception as exc:
            logger.debug(f"[TOPOLOGY] SSE client unavailable: {exc}")
            return
        worker = SSEWorker(f"{url}/api/events/stream")
        worker.event.connect(self._on_sse_event)
        worker.disconnected.connect(self._on_sse_disconnected)
        # No finished→deleteLater (QThread teardown race → SIGABRT).
        # Global keepalive owns lifetime. See utils/qthread_keepalive.py.
        try:
            from utils.qthread_keepalive import keep
            keep(worker)
        except Exception:
            pass
        self._sse_worker = worker
        worker.start()
        logger.debug("[TOPOLOGY] SSE worker started")

    def _on_sse_event(self, event_type: str, payload: dict):
        """Handle one event from the SSE stream.

        Today we only care about `state_transition`. Multiple events
        in quick succession (e.g. a fabric-wide BGP flap touches every
        device) coalesce into a single refresh via QTimer.singleShot
        so we don't slam the server with one /devices call per neighbor.
        """
        if event_type != "state_transition":
            return
        if self._sse_refresh_pending:
            return
        self._sse_refresh_pending = True
        from PyQt5.QtCore import QTimer

        def _fire():
            self._sse_refresh_pending = False
            try:
                self.refresh()
            except Exception as exc:
                logger.debug(f"[TOPOLOGY] SSE-triggered refresh failed: {exc}")

        QTimer.singleShot(750, _fire)   # tight window — feels live, not noisy

    def _on_sse_disconnected(self, reason: str):
        # SSEWorker auto-reconnects in its run loop; we just leave a
        # breadcrumb in the info label so the operator sees that live
        # updates lapsed and will resume.
        logger.debug(f"[TOPOLOGY] SSE disconnect: {reason}")

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    def _rebuild(self, rows: List[Dict[str, Any]]):
        self._rebuilding = True
        try:
            self._cards.clear()
            self._ports.clear()
            self._edges.clear()
            self._cables.clear()
            self._scene.clear()
            self._panel.show_placeholder()

            if not rows:
                return

            # Create cards
            for row in rows:
                card = _DeviceCard(row, self)
                self._scene.addItem(card)
                did = row.get("device_id") or ""
                if did:
                    self._cards[did] = card

            # Layout
            if self._layout_mode == self.LAYOUT_HIERARCHICAL:
                self._layout_hierarchical(rows)
            else:
                self._layout_circular(rows)

            # Peer protocol edges between cards
            self._infer_peer_edges(rows)
        finally:
            self._rebuilding = False

        self._redraw_links()
        rect = self._scene.itemsBoundingRect()
        if not rect.isEmpty():
            self._view.fitInView(rect.adjusted(-60, -60, 60, 60),
                                 Qt.KeepAspectRatio)
            # fitInView writes the transform directly without going
            # through .scale(); keep _current_scale in sync so the
            # next wheel/+/- tick computes a sensible bound.
            try:
                self._view._current_scale = self._view.transform().m11()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Layout algorithms
    # ------------------------------------------------------------------
    def _layout_hierarchical(self, rows: List[Dict[str, Any]]):
        """Group cards by server-side interface, render a port badge
        per interface at y=PORT_Y, stack cards vertically above each
        port column. Width auto-scales with the largest column."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            iface = (
                row.get("server_interface")
                or row.get("interface")
                or "(unbound)"
            )
            groups.setdefault(str(iface), []).append(row)

        # Sort columns alphabetically — predictable ordering
        col_keys = sorted(groups.keys())

        PORT_Y = 60                 # ports near the top, cards below
        CARD_TOP = PORT_Y + 80
        COL_GAP = 50

        # Each column width = max(port width, card width)
        col_width = max(_PortBadge.WIDTH, _DeviceCard.WIDTH)
        for idx, key in enumerate(col_keys):
            x = idx * (col_width + COL_GAP)
            members = groups[key]

            # Port badge
            port = _PortBadge(key, [m.get("device_id", "") for m in members])
            port.setPos(x + (col_width - _PortBadge.WIDTH) / 2, PORT_Y)
            self._scene.addItem(port)
            self._ports[key] = port

            # Stack cards vertically above the port
            for j, row in enumerate(members):
                did = row.get("device_id") or ""
                card = self._cards.get(did)
                if not card:
                    continue
                stored = self._read_pos_setting(did)
                if stored is not None:
                    card.setPos(stored[0], stored[1])
                else:
                    # Stagger cards downward as the column grows
                    card.setPos(
                        x + (col_width - _DeviceCard.WIDTH) / 2,
                        CARD_TOP + j * (card._height + 30),
                    )

    def _layout_circular(self, rows: List[Dict[str, Any]]):
        n = len(rows)
        if n == 0:
            return
        radius = max(240, 90 * n)
        for i, row in enumerate(rows):
            did = row.get("device_id") or ""
            card = self._cards.get(did)
            if not card:
                continue
            stored = self._read_pos_setting(did)
            if stored is not None:
                card.setPos(stored[0], stored[1])
                continue
            if n == 1:
                card.setPos(0, 0)
                continue
            angle = (2 * math.pi * i) / n
            card.setPos(radius * math.cos(angle), radius * math.sin(angle))

    def _read_pos_setting(self, device_id: str) -> Optional[Tuple[float, float]]:
        """Defensive QSettings reader — see commit notes; QSettings
        round-trips lists differently across backends and `isinstance`
        gating loses saved layouts on some PyQt5 builds."""
        if not device_id:
            return None
        key = f"pos/{self._layout_mode}/{device_id}"
        try:
            raw = self._settings.value(key, None)
        except Exception:
            return None
        if raw is None:
            return None
        try:
            if hasattr(raw, "toList"):
                raw = raw.toList()
        except Exception:
            return None
        if isinstance(raw, str):
            raw = [p.strip() for p in raw.split(",") if p.strip()]
        try:
            seq = list(raw)
        except TypeError:
            return None
        if len(seq) != 2:
            return None
        try:
            return (float(seq[0]), float(seq[1]))
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Edge / cable rendering
    # ------------------------------------------------------------------
    def _infer_peer_edges(self, rows: List[Dict[str, Any]]):
        """Same address-match heuristic as the previous implementation —
        find peer addresses on each row that match another row's local
        IP, draw an edge per (a, b, protocol) tuple. Edges are deduped
        by sorted (a, b) so reciprocal entries collapse into one line."""
        addr_to_did: Dict[str, str] = {}
        for row in rows:
            did = row.get("device_id") or ""
            for key in ("ipv4", "ipv4_address", "ipv6",
                        "loopback_ipv4", "loopback_ipv6"):
                v = row.get(key)
                if v and isinstance(v, str):
                    addr_to_did[v.split("/")[0].strip()] = did

        pairs: Dict[Tuple[str, str], List[str]] = {}

        def _add(a: str, b: str, proto: str):
            if not a or not b or a == b:
                return
            k = tuple(sorted([a, b]))
            pairs.setdefault(k, [])
            if proto not in pairs[k]:
                pairs[k].append(proto)

        for row in rows:
            src = row.get("device_id") or ""
            if not src:
                continue
            for nb_key in ("bgp_neighbors", "bgp_peers"):
                v = row.get(nb_key)
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except Exception:
                        v = []
                if isinstance(v, list):
                    for nb in v:
                        ip = None
                        if isinstance(nb, dict):
                            ip = nb.get("peer_ip") or nb.get("neighbor_ip") or nb.get("ip")
                        elif isinstance(nb, str):
                            ip = nb
                        if ip:
                            dst = addr_to_did.get(ip.split("/")[0].strip())
                            if dst:
                                _add(src, dst, "bgp")
            for k, proto in (("ospf_neighbors", "ospf"),
                             ("isis_neighbors", "isis")):
                v = row.get(k)
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except Exception:
                        v = []
                if isinstance(v, list):
                    for nb in v:
                        ip = None
                        if isinstance(nb, dict):
                            ip = nb.get("ip") or nb.get("address") or nb.get("neighbor_ip")
                        elif isinstance(nb, str):
                            ip = nb
                        if ip:
                            dst = addr_to_did.get(ip.split("/")[0].strip())
                            if dst:
                                _add(src, dst, proto)

        proto_color = {
            "bgp":  QColor("#6b7280"),
            "ospf": QColor("#7c3aed"),
            "isis": QColor("#0891b2"),
        }
        for (a, b), protos in pairs.items():
            for i, proto in enumerate(protos):
                edge = QGraphicsPathItem()
                pen = QPen(proto_color.get(proto, QColor("#888")), 2)
                if proto != "bgp":
                    pen.setStyle(Qt.DashLine)
                edge.setPen(pen)
                edge.setZValue(-1)
                edge.setData(0, ("peer", a, b, i))
                self._scene.addItem(edge)
                self._edges.append(edge)

    def _redraw_links(self):
        """Redraw both peer-edges and device→port cables. Re-entrancy-
        guarded so the in-flight tear-down inside _rebuild can't call
        through to a half-populated cache."""
        if getattr(self, "_rebuilding", False):
            return

        # Peer-edges between cards
        for edge in self._edges:
            meta = edge.data(0)
            if not meta or len(meta) != 4:
                continue
            _, a, b, off = meta
            na = self._cards.get(a)
            nb = self._cards.get(b)
            if not na or not nb:
                continue
            ca = na.pos() + QPointF(_DeviceCard.WIDTH / 2, na._height / 2)
            cb = nb.pos() + QPointF(_DeviceCard.WIDTH / 2, nb._height / 2)
            dx, dy = cb.x() - ca.x(), cb.y() - ca.y()
            d = math.hypot(dx, dy) or 1.0
            px = -dy / d * (8 * off)
            py = dx / d * (8 * off)
            path = QPainterPath(QPointF(ca.x() + px, ca.y() + py))
            path.lineTo(QPointF(cb.x() + px, cb.y() + py))
            edge.setPath(path)

        # Device → port cables
        # We rebuild cables fresh on each redraw call rather than
        # tracking them as items, because the membership can change
        # when the user drags a card between columns (visually).
        for cable in self._cables:
            try:
                self._scene.removeItem(cable)
            except Exception:
                pass
        self._cables.clear()

        if self._layout_mode != self.LAYOUT_HIERARCHICAL:
            return

        # Re-derive each card → port linkage from the original row data
        for row in self._rows_cache:
            did = row.get("device_id") or ""
            iface = (row.get("server_interface")
                     or row.get("interface")
                     or "(unbound)")
            card = self._cards.get(did)
            port = self._ports.get(str(iface))
            if not card or not port:
                continue
            top = card.pos() + QPointF(_DeviceCard.WIDTH / 2, card._height)
            bottom = port.pos() + QPointF(_PortBadge.WIDTH / 2, 0)
            path = QPainterPath(top)
            # Subtle S-curve via cubic so cables look like cables.
            ctrl1 = QPointF(top.x(), (top.y() + bottom.y()) / 2)
            ctrl2 = QPointF(bottom.x(), (top.y() + bottom.y()) / 2)
            path.cubicTo(ctrl1, ctrl2, bottom)
            cable = QGraphicsPathItem(path)
            pen = QPen(QColor(COLOR_CABLE), 3)
            pen.setCapStyle(Qt.RoundCap)
            cable.setPen(pen)
            cable.setZValue(-3)
            self._scene.addItem(cable)
            self._cables.append(cable)

    # ------------------------------------------------------------------
    # Selection / property-panel hook
    # ------------------------------------------------------------------
    def _on_card_selected(self, row: Dict[str, Any]):
        try:
            self._panel.show_device(row)
        except Exception as exc:
            logger.debug(f"[TOPOLOGY] panel update failed: {exc}")

    # ------------------------------------------------------------------
    # Layout-mode toggle / reset / persistence
    # ------------------------------------------------------------------
    def _zoom_fit(self):
        """Reset zoom + scroll so every card is visible. Equivalent to
        the auto-fit that fires at the end of _rebuild()."""
        rect = self._scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        # zoom_reset() returns scale to 1.0, then fitInView rescales.
        self._view.zoom_reset()
        self._view.fitInView(rect.adjusted(-60, -60, 60, 60), Qt.KeepAspectRatio)
        # fitInView sets the transform directly; sync our _current_scale
        # so subsequent +/- still clamp sensibly.
        self._view._current_scale = self._view.transform().m11()

    def _on_layout_changed(self, _idx: int):
        new_mode = self._layout_combo.currentData()
        if new_mode not in (self.LAYOUT_HIERARCHICAL, self.LAYOUT_CIRCULAR):
            return
        if new_mode == self._layout_mode:
            return
        self._layout_mode = new_mode
        self._settings.setValue("layout_mode", new_mode)
        if self._rows_cache:
            self._rebuild(self._rows_cache)

    def _reset_layout(self):
        try:
            self._settings.beginGroup(f"pos/{self._layout_mode}")
            for k in self._settings.allKeys():
                self._settings.remove(k)
            self._settings.endGroup()
        except Exception as exc:
            logger.debug(f"[TOPOLOGY] reset clear failed: {exc}")
        if self._rows_cache:
            self._rebuild(self._rows_cache)

    def save_layout(self):
        """Persist current card positions per layout-mode so the next
        session opens with the same layout. Called from main_window
        on close. Also tears down the SSE worker so the GUI close
        doesn't leave a thread orphaned on a half-open HTTP socket."""
        try:
            if self._sse_worker is not None:
                try:
                    self._sse_worker.stop()
                    if self._sse_worker.isRunning():
                        self._sse_worker.wait(1000)
                except RuntimeError:
                    pass
        except Exception as exc:
            logger.debug(f"[TOPOLOGY] SSE worker shutdown failed: {exc}")
        try:
            for did, card in self._cards.items():
                pos = card.pos()
                self._settings.setValue(
                    f"pos/{self._layout_mode}/{did}",
                    [pos.x(), pos.y()],
                )
            self._settings.setValue("layout_mode", self._layout_mode)
        except Exception as exc:
            logger.debug(f"[TOPOLOGY] save_layout: {exc}")

    # ------------------------------------------------------------------
    # Hook back into the Devices-tab config viewer
    # ------------------------------------------------------------------
    def _open_config_viewer(self, row: Dict[str, Any]):
        try:
            from widgets.devices_tab import _DeviceConfigViewerDialog
        except Exception as exc:
            logger.debug(f"[TOPOLOGY] viewer import failed: {exc}")
            return
        device_id = row.get("device_id") or ""
        if not device_id:
            return
        device_name = row.get("device_name") or device_id
        url = self._get_server_url()
        if not url:
            payload = row
        else:
            try:
                r = requests.get(
                    f"{url}/api/device/database/devices/{device_id}",
                    timeout=3,
                )
                payload = r.json() if r.status_code == 200 else row
            except Exception as exc:
                logger.debug(f"[TOPOLOGY] single-device fetch failed: {exc}")
                payload = row
        dlg = _DeviceConfigViewerDialog(self, device_name, device_id, payload)
        dlg.exec_()
