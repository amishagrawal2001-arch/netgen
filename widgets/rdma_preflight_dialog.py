"""widgets/rdma_preflight_dialog.py — v0.5.150 RDMA Pre-flight check.

Opens before Start fires on the Blast / Topology dialogs. Probes
every endpoint via `/api/rdma/probe`, surfaces port-state /
link-layer / IP / GID for each, detects the classic same-host
same-subnet routing trap, and lets the operator apply temporary
test IPs (with full validation) without leaving the dialog.

Lifecycle:
* On Validate → `/api/rdma/test_ifaces/validate` (no side effects).
* On Apply → `/api/rdma/test_ifaces/configure` (runtime-only,
  state-tracked).
* Returns `state_id` to the parent so it can clean up on dialog
  close, perftest job finish, or Stop button.

The dialog never persists anything to the operator's permanent
config. Reboot → all changes vanish.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


# Endpoint shape passed in by parent dialogs. Tuple of
# (label, tg_url, hca, ib_port). label is "Server"/"Client" or a
# topology pair index ("pair 0 server", etc.) — purely display.
Endpoint = Tuple[str, str, str, int]


# ─────────────────────────────────────────── async helpers


def _api_worker():
    """Reuse the QThread worker the other RDMA dialogs use so we
    don't multiply HTTP primitives."""
    from traffic_client.dpdk_menu_actions import _DpdkApiWorker
    return _DpdkApiWorker


def _request_async(parent, method: str, url: str, body: Optional[dict],
                   on_done, *, timeout: float = 8.0) -> None:
    W = _api_worker()
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if body is not None:
        kwargs["json"] = body
    w = W(method, url, **kwargs)
    w.done.connect(on_done)
    if not hasattr(parent, "_rdma_workers"):
        parent._rdma_workers = set()
    parent._rdma_workers.add(w)
    w.done.connect(lambda *_a, _w=w: parent._rdma_workers.discard(_w))
    w.start()


# ─────────────────────────────────────────── dialog


class RdmaPreflightDialog(QDialog):
    """Per-endpoint probe + optional test-IP apply.

    Constructor takes a list of endpoints and a single "config"
    TG URL — the URL we POST configure/cleanup to. For same-host
    Blast / Topology setups all endpoints share one TG; for
    cross-TG topologies the caller can apply config independently
    per side (one preflight dialog per host).
    """

    def __init__(
        self,
        endpoints: List[Endpoint],
        config_url: str,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("RDMA Pre-flight Check")
        self.setMinimumSize(820, 560)
        self._endpoints = list(endpoints)
        self._config_url = config_url.rstrip("/")
        # state_id from the last successful Apply — caller pulls
        # this via `applied_state_id()` and is responsible for
        # cleanup when the test ends or the dialog closes.
        self._applied_state_id: Optional[str] = None
        # Latest probe result per (tg_url, hca) — keyed for the
        # same-subnet trap detector.
        self._probes: Dict[Tuple[str, str], Dict[str, Any]] = {}

        self._build_ui()
        QTimer.singleShot(0, self._probe_all)

    # ──────────────────────────── UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.setStyleSheet(
            "QGroupBox {"
            "  font-weight: 600; color: #334155;"
            "  border: 1px solid #cbd5e1; border-radius: 4px;"
            "  margin-top: 9px; padding: 6px 8px 8px 8px;"
            "}"
            "QGroupBox::title {"
            "  subcontrol-origin: margin; subcontrol-position: top left;"
            "  left: 8px; padding: 0 4px;"
            "}"
        )

        # Header.
        hdr = QLabel(
            "<span style='font-size:13px; font-weight:600; color:#0f172a;'>"
            "RDMA Pre-flight Check</span>&nbsp;&nbsp;"
            "<span style='color:#64748b; font-size:11px;'>"
            "Verifies each endpoint and (when the same-host "
            "loopback trap is detected) offers a temporary IP "
            "fix — no persistent config changes."
            "</span>"
        )
        hdr.setWordWrap(True)
        root.addWidget(hdr)

        # Per-endpoint probe table.
        probe_box = QGroupBox("Endpoint probes")
        pl = QVBoxLayout(probe_box)
        pl.setContentsMargins(6, 4, 6, 6)
        self._probe_table = QTableWidget()
        self._probe_table.setColumnCount(7)
        self._probe_table.setHorizontalHeaderLabels([
            "Endpoint", "HCA", "Iface", "Port state",
            "Link", "IPs", "RoCEv2 GIDs",
        ])
        self._probe_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive)
        self._probe_table.horizontalHeader().setStretchLastSection(True)
        self._probe_table.setRowCount(len(self._endpoints))
        for r, ep in enumerate(self._endpoints):
            label, url, hca, ib_port = ep
            self._probe_table.setItem(
                r, 0, QTableWidgetItem(f"{label}\n{url}"))
            self._probe_table.setItem(
                r, 1, QTableWidgetItem(f"{hca} (port {ib_port})"))
            self._probe_table.setItem(r, 2, QTableWidgetItem("(probing…)"))
        self._probe_table.resizeColumnsToContents()
        pl.addWidget(self._probe_table)
        root.addWidget(probe_box, 1)

        # Verdict banner — fills in after probes return.
        self._verdict = QLabel("")
        self._verdict.setWordWrap(True)
        self._verdict.setStyleSheet(
            "padding: 6px 8px; border-radius: 4px;"
        )
        root.addWidget(self._verdict)

        # Temporary-IP config section.
        cfg_box = QGroupBox(
            "Temporary IP configuration (runtime only — gone on reboot)"
        )
        cfg = QVBoxLayout(cfg_box)
        cfg.setContentsMargins(6, 4, 6, 6)
        cfg.setSpacing(6)

        cfg.addWidget(QLabel(
            "<span style='color:#475569; font-size:11px;'>"
            "Edit either side to override. The CIDR is "
            "applied with <code>ip addr add &lt;cidr&gt; dev "
            "&lt;iface&gt; label &lt;iface&gt;:netgen</code> — "
            "the <code>netgen</code> label tags every IP we add "
            "so cleanup never touches operator-managed addresses."
            "</span>"
        ))

        self._config_grid = QGridLayout()
        self._config_grid.setHorizontalSpacing(6)
        self._config_grid.setVerticalSpacing(4)
        self._config_grid.addWidget(QLabel("<b>Iface</b>"), 0, 0)
        self._config_grid.addWidget(QLabel("<b>Test CIDR</b>"), 0, 1)
        self._config_grid.addWidget(QLabel("<b>Notes</b>"), 0, 2)
        self._config_rows: List[Dict[str, Any]] = []  # populated by _populate_config
        cfg.addLayout(self._config_grid)

        opts_row = QHBoxLayout()
        self._rp_check = QCheckBox(
            "Also disable rp_filter on these ifaces"
        )
        self._rp_check.setChecked(True)
        self._rp_check.setToolTip(
            "Linux's reverse-path filter (net.ipv4.conf.<iface>."
            "rp_filter=1) drops packets that arrive on an iface "
            "whose route table says they should have come in "
            "elsewhere. Same-host loopback testing routinely "
            "trips this. Cleanup restores the prior value."
        )
        opts_row.addWidget(self._rp_check)
        opts_row.addStretch(1)
        cfg.addLayout(opts_row)

        btn_row = QHBoxLayout()
        self._validate_btn = QPushButton("Validate")
        self._validate_btn.setToolTip(
            "Check the proposed CIDRs without applying. Surfaces "
            "format errors, same-subnet trap, existing route "
            "conflicts."
        )
        self._validate_btn.clicked.connect(self._on_validate)
        btn_row.addWidget(self._validate_btn)

        self._apply_btn = QPushButton("Apply (temporary)")
        self._apply_btn.setStyleSheet(
            "QPushButton { background-color: #0ea5e9; color: white; "
            "padding: 4px 10px; border-radius: 3px; font-weight: 600; }"
            "QPushButton:disabled { background-color: #94a3b8; }"
        )
        self._apply_btn.setToolTip(
            "Validate, then apply the test IPs via `ip addr add` "
            "with `<iface>:netgen` labels. Runtime only — nothing "
            "is written to /etc/network or netplan."
        )
        self._apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self._apply_btn)

        self._cleanup_btn = QPushButton("Clean up applied")
        self._cleanup_btn.setEnabled(False)
        self._cleanup_btn.setToolTip(
            "Undo the most recent Apply on this dialog (removes "
            "the test IPs + restores rp_filter)."
        )
        self._cleanup_btn.clicked.connect(self._on_cleanup)
        btn_row.addWidget(self._cleanup_btn)

        btn_row.addStretch(1)
        cfg.addLayout(btn_row)

        # v0.5.152: Option C-A: "📌 Keep" toggle. When checked, the
        # parent dialog's closeEvent SKIPS the auto-cleanup for any
        # state_id applied via this preflight session. Lets the
        # operator iterate on test params without re-applying IPs
        # every time. Cleanup then happens manually via the button
        # above, the orphans endpoint, or reboot.
        keep_row = QHBoxLayout()
        self._keep_check = QCheckBox(
            "📌 Keep these test IPs after this dialog closes"
        )
        self._keep_check.setToolTip(
            "When checked, the parent Blast/Topology dialog will "
            "NOT auto-clean these test IPs on close. The IPs stay "
            "applied until you click 'Clean up applied' above, run "
            "POST /api/rdma/test_ifaces/cleanup with state_id=null, "
            "or reboot the server.\n\n"
            "Useful when iterating on test params — saves the "
            "round-trip of re-applying IPs every Start."
        )
        keep_row.addWidget(self._keep_check)
        keep_row.addStretch(1)
        cfg.addLayout(keep_row)

        root.addWidget(cfg_box)

        # Status line + close button.
        self._status = QLabel("")
        self._status.setStyleSheet("color: #64748b; font-size: 11px;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        close_btns = QDialogButtonBox(QDialogButtonBox.Close)
        close_btns.rejected.connect(self.accept)
        close_btns.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        root.addWidget(close_btns)

    # ──────────────────────────── probe phase

    def _probe_all(self) -> None:
        for row, ep in enumerate(self._endpoints):
            _label, url, hca, ib_port = ep
            self._probe_one(row, url, hca, ib_port)

    def _probe_one(self, row: int, url: str, hca: str, ib_port: int) -> None:
        def _on_done(data, err, _row=row, _url=url, _hca=hca):
            try:
                self._probe_table.rowCount()
            except RuntimeError:
                return
            if err or (data and data.get("error")):
                msg = err or (data.get("error") if data else "error")
                self._probe_table.setItem(
                    _row, 2, QTableWidgetItem("error"))
                self._probe_table.setItem(
                    _row, 3, QTableWidgetItem(str(msg)))
                return
            self._probes[(_url, _hca)] = data
            iface = data.get("kernel_iface") or "(unknown)"
            state = data.get("state") or ""
            link = data.get("link_layer") or ""
            ips = data.get("ip_addresses") or []
            gids = [
                g for g in (data.get("gids") or [])
                if "RoCE v2" in (g.get("type") or "")
                or "RoCEv2" in (g.get("type") or "")
            ]
            ip_text = ", ".join(ips) if ips else "(none)"
            if gids:
                gid_text = ", ".join(
                    f"idx={g.get('index')} ndev={g.get('ndev') or '?'}"
                    for g in gids[:3]
                )
                if len(gids) > 3:
                    gid_text += f", +{len(gids) - 3} more"
            else:
                gid_text = "(no RoCEv2 GIDs — operator may need "\
                           "to assign an IP)"
            self._probe_table.setItem(_row, 2, QTableWidgetItem(iface))
            state_item = QTableWidgetItem(state or "?")
            if state == "ACTIVE":
                state_item.setForeground(QColor("#15803d"))
            elif state in ("DOWN", "PORT_DOWN"):
                state_item.setForeground(QColor("#b91c1c"))
            self._probe_table.setItem(_row, 3, state_item)
            self._probe_table.setItem(_row, 4, QTableWidgetItem(link))
            self._probe_table.setItem(_row, 5, QTableWidgetItem(ip_text))
            self._probe_table.setItem(_row, 6, QTableWidgetItem(gid_text))
            self._probe_table.resizeColumnsToContents()
            self._on_probe_finished_maybe()

        _request_async(
            self, "GET",
            f"{url.rstrip('/')}/api/rdma/probe?device={hca}&port={ib_port}",
            None, _on_done, timeout=6.0,
        )

    def _on_probe_finished_maybe(self) -> None:
        # Wait until ALL probes returned before deciding on the
        # verdict + populating the config rows.
        if len(self._probes) < len(self._endpoints):
            return
        self._render_verdict_and_populate()

    def _render_verdict_and_populate(self) -> None:
        # Group probes by (tg_url) — same host = potential trap.
        by_host: Dict[str, List[Dict[str, Any]]] = {}
        for (url, _hca), probe in self._probes.items():
            by_host.setdefault(url, []).append(probe)

        trap_hosts: List[Tuple[str, List[Dict[str, Any]]]] = []
        for url, probes in by_host.items():
            if len(probes) < 2:
                continue
            # Are any two probes' kernel ifaces in the same subnet?
            seen_nets: Dict[str, Dict[str, Any]] = {}
            for p in probes:
                for cidr in p.get("ip_addresses") or []:
                    if ":" in cidr.split("/")[0]:  # skip IPv6
                        continue
                    try:
                        import ipaddress
                        net = str(ipaddress.IPv4Interface(cidr).network)
                    except Exception:
                        continue
                    if net in seen_nets:
                        trap_hosts.append((url, probes))
                        break
                    seen_nets[net] = p
                else:
                    continue
                break

        # Also: any DOWN port is its own blocker.
        down_endpoints = [
            (url, p) for (url, _hca), p in self._probes.items()
            if p.get("state") not in (None, "ACTIVE", "")
        ]
        # And: any endpoint with no IPs at all (so no RoCEv2 GID
        # can resolve).
        no_ip_endpoints = [
            (url, p) for (url, _hca), p in self._probes.items()
            if not p.get("ip_addresses")
        ]

        if down_endpoints:
            ports = ", ".join(
                f"{p.get('hca')}({p.get('state')})"
                for _u, p in down_endpoints
            )
            self._verdict.setText(
                f"<b style='color:#b91c1c;'>BLOCKER:</b> port DOWN on: "
                f"{ports}. perftest will fail at QP setup. Fix link "
                f"state before configuring IPs."
            )
            self._verdict.setStyleSheet(
                "padding:6px 8px; border-radius:4px; "
                "background:#fee2e2; color:#7f1d1d;"
            )
        elif trap_hosts:
            self._verdict.setText(
                "<b style='color:#b45309;'>Same-subnet trap detected.</b> "
                "Two HCAs on one host share an IP subnet — Linux "
                "will route between them via <code>lo</code> instead "
                "of out the wire. Apply different test CIDRs below "
                "to fix this for the duration of the test."
            )
            self._verdict.setStyleSheet(
                "padding:6px 8px; border-radius:4px; "
                "background:#fef3c7; color:#78350f;"
            )
        elif no_ip_endpoints:
            ports = ", ".join(
                p.get("hca", "?") for _u, p in no_ip_endpoints
            )
            self._verdict.setText(
                f"<b style='color:#b45309;'>IP missing</b> on: {ports}. "
                f"RoCEv2 needs an IP for the GID handshake. Apply "
                f"test CIDRs below."
            )
            self._verdict.setStyleSheet(
                "padding:6px 8px; border-radius:4px; "
                "background:#fef3c7; color:#78350f;"
            )
        else:
            self._verdict.setText(
                "<b style='color:#15803d;'>Pre-flight OK.</b> All "
                "ports active, no same-subnet trap, every endpoint "
                "has an IP. perftest should start cleanly."
            )
            self._verdict.setStyleSheet(
                "padding:6px 8px; border-radius:4px; "
                "background:#dcfce7; color:#14532d;"
            )

        self._populate_config_rows()

    # ──────────────────────────── temporary-IP config UI

    def _populate_config_rows(self) -> None:
        """v0.5.153 rewrite: walk every probe's existing IPv4
        subnets, propose CIDRs that
          (a) don't collide with any existing iface IP,
          (b) don't share a subnet with any sibling iface's
              suggestion (the exact trap operators are escaping),
          (c) skip ifaces that already have a valid IPv4 — those
              don't need a test CIDR; the suggestion box is empty
              with a clarifying note.

        Was: hardcoded `10.42.0.1/24 + 10.42.0.2/24` for the
        2-iface case → SAME subnet, the literal trap. Operator
        screenshot showed the validate banner rejecting the dialog's
        own auto-fill."""
        import ipaddress as _ip

        seen: List[Tuple[str, str]] = []  # (url, iface)
        existing_v4: Dict[str, List[_ip.IPv4Network]] = {}
        for (url, _hca), p in self._probes.items():
            iface = p.get("kernel_iface")
            if iface and (url, iface) not in seen:
                seen.append((url, iface))
            # Collect existing IPv4 networks per iface for both the
            # "already has IP" detection and the avoid-collision
            # picker below.
            if iface:
                nets = existing_v4.setdefault(iface, [])
                for cidr in p.get("ip_addresses") or []:
                    if ":" in cidr.split("/")[0]:
                        continue
                    try:
                        nets.append(_ip.IPv4Interface(cidr).network)
                    except Exception:
                        continue

        # Build the global occupied-subnet set — every existing
        # IPv4 network across all probed ifaces. Used to skip
        # those /24s when proposing fresh ones.
        occupied: set = set()
        for nets in existing_v4.values():
            for n in nets:
                occupied.add(n)

        # Sequential /24s in 10.42.0.0/16 → 10.43, 10.44, … each
        # iface that NEEDS a test IP gets the next one not in
        # `occupied` AND not already proposed for a sibling.
        suggestions: Dict[Tuple[str, str], str] = {}
        proposed_nets: set = set()
        next_octet = 42
        for url, iface in seen:
            if existing_v4.get(iface):
                # Already has an IPv4 — leave the CIDR empty so
                # Apply doesn't try to add yet another IP.
                continue
            # Walk forward through 10.<n>.0.0/24 until we find one
            # not occupied and not proposed.
            while next_octet < 200:
                cand = _ip.IPv4Network(f"10.{next_octet}.0.0/24")
                if (cand not in occupied
                        and cand not in proposed_nets):
                    suggestions[(url, iface)] = f"10.{next_octet}.0.1/24"
                    proposed_nets.add(cand)
                    break
                next_octet += 1
            next_octet += 1

        # Surface whether any iface needs a fix at all. If every
        # iface already has an IPv4 in a non-conflicting subnet
        # (i.e. the verdict was "Pre-flight OK"), we still render
        # the rows but all CIDRs start empty and the note column
        # explains why.
        needs_fix = any((url, iface) in suggestions for url, iface in seen)
        if not needs_fix:
            self._status.setText(
                "<span style='color:#0369a1; font-size:11px;'>"
                "All endpoints already have IPv4 addresses in "
                "non-conflicting subnets. Apply is only needed if "
                "you want to add additional test IPs."
                "</span>"
            )

        # Clear existing rows below the header.
        for row_dict in self._config_rows:
            for w in row_dict.values():
                if hasattr(w, "deleteLater"):
                    w.deleteLater()
        self._config_rows.clear()

        for i, (url, iface) in enumerate(seen, start=1):
            iface_lbl = QLabel(f"<code>{iface}</code><br>"
                               f"<small style='color:#94a3b8;'>{url}</small>")
            iface_lbl.setTextFormat(Qt.RichText)
            cidr_edit = QLineEdit(suggestions.get((url, iface), ""))
            cidr_edit.setMinimumWidth(160)
            cidr_edit.setFont(QFont("Menlo"))
            cidr_edit.setPlaceholderText(
                "(leave empty to skip)")
            note_lbl = QLabel("")
            note_lbl.setStyleSheet("color:#64748b; font-size:11px;")
            note_lbl.setWordWrap(True)
            existing = existing_v4.get(iface) or []
            if existing:
                # v0.5.153: explicit note so the operator knows
                # WHY the CIDR field is empty by default. No more
                # "already on, will be skipped" surprise on
                # Validate.
                note_lbl.setText(
                    f"<span style='color:#0369a1;'>"
                    f"already has IPv4 ({existing[0]}); "
                    f"leave empty to skip"
                    f"</span>"
                )
            self._config_grid.addWidget(iface_lbl, i, 0)
            self._config_grid.addWidget(cidr_edit, i, 1)
            self._config_grid.addWidget(note_lbl, i, 2)
            self._config_rows.append({
                "url": url,
                "iface": iface,
                "iface_label": iface_lbl,
                "cidr_edit": cidr_edit,
                "note": note_lbl,
            })

    def _collect_entries(self) -> List[Dict[str, str]]:
        out = []
        for r in self._config_rows:
            cidr = r["cidr_edit"].text().strip()
            if not cidr:
                continue
            out.append({"name": r["iface"], "cidr": cidr})
        return out

    def _clear_notes(self) -> None:
        for r in self._config_rows:
            r["note"].setText("")
            r["cidr_edit"].setStyleSheet("")

    def _apply_issues_to_rows(
        self, issues: List[Dict[str, str]]
    ) -> None:
        self._clear_notes()
        for iss in issues:
            iface = iss.get("iface")
            sev = iss.get("severity")
            msg = iss.get("message")
            colour = "#b91c1c" if sev == "error" else "#b45309"
            for r in self._config_rows:
                if r["iface"] == iface:
                    existing = r["note"].text()
                    line = (f"<span style='color:{colour};'>"
                            f"{sev}: {msg}</span>")
                    r["note"].setText(
                        existing + ("<br>" if existing else "") + line
                    )
                    r["cidr_edit"].setStyleSheet(
                        f"border: 1px solid {colour};"
                    )

    # ──────────────────────────── Validate

    def _on_validate(self) -> None:
        entries = self._collect_entries()
        if not entries:
            self._status.setText(
                "<span style='color:#b91c1c;'>"
                "Nothing to validate — fill in at least one CIDR."
                "</span>"
            )
            return
        self._status.setText("<i>Validating…</i>")
        self._validate_btn.setEnabled(False)

        def _on_done(data, err):
            self._validate_btn.setEnabled(True)
            if err:
                self._status.setText(
                    f"<span style='color:#b91c1c;'>{err}</span>")
                return
            issues = (data or {}).get("issues") or []
            self._apply_issues_to_rows(issues)
            if (data or {}).get("ok"):
                self._status.setText(
                    "<span style='color:#15803d;'>"
                    "Validation passed — safe to Apply."
                    "</span>"
                )
            else:
                self._status.setText(
                    f"<span style='color:#b91c1c;'>"
                    f"Validation failed: {len(issues)} issue(s)."
                    f"</span>"
                )

        _request_async(
            self, "POST",
            f"{self._config_url}/api/rdma/test_ifaces/validate",
            {"ifaces": entries}, _on_done,
        )

    # ──────────────────────────── Apply

    def _on_apply(self) -> None:
        entries = self._collect_entries()
        if not entries:
            self._status.setText(
                "<span style='color:#b91c1c;'>"
                "Nothing to apply — fill in at least one CIDR."
                "</span>"
            )
            return
        self._status.setText("<i>Applying…</i>")
        self._apply_btn.setEnabled(False)

        def _on_done(data, err):
            self._apply_btn.setEnabled(True)
            if err:
                self._status.setText(
                    f"<span style='color:#b91c1c;'>{err}</span>")
                return
            if not (data or {}).get("ok"):
                issues = (data or {}).get("issues") or []
                self._apply_issues_to_rows(issues)
                err_msg = (data or {}).get(
                    "error", "validation failed; nothing applied")
                self._status.setText(
                    f"<span style='color:#b91c1c;'>{err_msg}</span>"
                )
                return
            self._applied_state_id = (data or {}).get("state_id")
            applied = (data or {}).get("applied") or []
            applied_errors = (data or {}).get("errors") or []
            self._clear_notes()
            for a in applied:
                for r in self._config_rows:
                    if r["iface"] == a.get("iface"):
                        r["note"].setText(
                            f"<span style='color:#15803d;'>applied "
                            f"({a.get('cidr')})</span>"
                        )
                        r["cidr_edit"].setStyleSheet(
                            "border: 1px solid #15803d;")
            for e in applied_errors:
                for r in self._config_rows:
                    if r["iface"] == e.get("iface"):
                        r["note"].setText(
                            f"<span style='color:#b45309;'>"
                            f"{e.get('message')}</span>"
                        )
            self._cleanup_btn.setEnabled(True)
            self._status.setText(
                f"<span style='color:#15803d;'>"
                f"Applied {len(applied)} test IP(s). "
                f"state_id={self._applied_state_id}. Re-run the "
                f"test, then click <b>Clean up applied</b> here "
                f"or close this dialog to drop the temporary "
                f"config."
                f"</span>"
            )
            # Re-probe to refresh the table view.
            QTimer.singleShot(400, self._probe_all)

        _request_async(
            self, "POST",
            f"{self._config_url}/api/rdma/test_ifaces/configure",
            {"ifaces": entries,
             "disable_rp_filter": self._rp_check.isChecked()},
            _on_done,
        )

    # ──────────────────────────── Cleanup

    def _on_cleanup(self) -> None:
        if not self._applied_state_id:
            self._cleanup_btn.setEnabled(False)
            return
        sid = self._applied_state_id
        self._status.setText("<i>Cleaning up…</i>")
        self._cleanup_btn.setEnabled(False)

        def _on_done(data, err):
            if err:
                self._status.setText(
                    f"<span style='color:#b91c1c;'>cleanup: {err}</span>")
                self._cleanup_btn.setEnabled(True)
                return
            self._applied_state_id = None
            removed = (data or {}).get("removed") or []
            self._status.setText(
                f"<span style='color:#15803d;'>"
                f"Removed {len(removed)} test IP(s)."
                f"</span>"
            )
            QTimer.singleShot(400, self._probe_all)

        _request_async(
            self, "POST",
            f"{self._config_url}/api/rdma/test_ifaces/cleanup",
            {"state_id": sid}, _on_done,
        )

    # ──────────────────────────── lifecycle

    def applied_state_id(self) -> Optional[str]:
        """Caller pulls this on close to know whether to schedule
        cleanup. None = nothing applied (or already cleaned)."""
        return self._applied_state_id

    def keep_applied(self) -> bool:
        """v0.5.152: caller reads this on close. When True, the
        parent dialog must NOT auto-clean the applied state_id —
        operator wants the IPs to persist until they manually
        clean (via the dialog's Clean-up button or the orphans
        endpoint).

        Returns False when the checkbox doesn't exist yet (init
        order race) or wasn't ticked."""
        try:
            return bool(self._keep_check.isChecked())
        except (AttributeError, RuntimeError):
            return False

    def closeEvent(self, event) -> None:
        # Don't auto-cleanup here — the parent dialog may want to
        # keep the test IPs in place while the perftest run lives.
        # The parent reads `applied_state_id()` and triggers
        # cleanup itself at the right moment.
        super().closeEvent(event)
