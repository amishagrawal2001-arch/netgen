# stream_logic.py
import os
import uuid
import requests
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtCore import QTimer,QSize
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QTableWidgetItem
from utils.qicon_loader import r_icon


class TrafficGenClientStreamLogic:
    # ---------- helpers ----------

    def _prepare_tx_rate(self, stream: dict) -> dict:
        """
        Normalize the selected rate into a canonical dict the server can consume.
        Prefers top-level keys and falls back to protocol_selection keys.
        Returns one of:
          {"mode":"line"}
          {"mode":"pps","pps":int}
          {"mode":"bps","bps":int}          # bits per second
          {"mode":"load","percent":float}   # 1..100
        """
        ps = stream.get("protocol_selection", {})

        rt = (stream.get("stream_rate_type")
              or ps.get("stream_rate_type")
              or "Packets Per Second (PPS)").strip()

        def _get(key, default):
            return (stream.get(key) or ps.get(key) or default)

        if rt == "Line Rate":
            return {"mode": "line"}

        if rt.startswith("Packets Per Second"):
            pps = int(str(_get("stream_pps_rate", "1000")) or "1000")
            return {"mode": "pps", "pps": max(1, pps)}

        if rt.startswith("Bit Rate"):
            # value provided in Mbps from the dialog
            mbps_str = str(_get("stream_bit_rate", "100")) or "100"
            mbps = float(mbps_str)
            bps = int(mbps * 1_000_000)
            return {"mode": "bps", "bps": max(1, bps)}

        if rt.startswith("Load"):
            pct = float(str(_get("stream_load_percentage", "50")) or "50")
            pct = max(1.0, min(100.0, pct))
            return {"mode": "load", "percent": pct}

        # fallback (legacy/default)
        pps = int(str(_get("stream_pps_rate", "1000")) or "1000")
        return {"mode": "pps", "pps": max(1, pps)}

    # inside TrafficGenClientStreamLogic

    def _prepare_duration(self, stream):
        """
        Returns a dict with {mode, seconds, continuous} using top-level or protocol_selection values.
        Mode: "Continuous" or "Seconds".
        """
        ps = stream.get("protocol_selection", {})
        mode = (stream.get("stream_duration_mode")
                or ps.get("stream_duration_mode")
                or "Continuous")
        mode = str(mode).strip()

        # seconds may be stored as str; normalize to int
        sec_raw = (stream.get("stream_duration_seconds")
                   or ps.get("stream_duration_seconds")
                   or 0)
        try:
            seconds = int(sec_raw)
        except Exception:
            seconds = 0
        seconds = max(0, seconds)

        return {
            "mode": mode,
            "seconds": seconds,
            "continuous": (mode.lower() == "continuous")
        }

    def _find_port_key_for_stream(self, stream_id):
        """Find the self.streams dict key (e.g. 'TG 0 - eth1') for a given stream_id."""
        for port_key, stream_list in self.streams.items():
            for s in stream_list:
                if s.get("stream_id") == stream_id:
                    return port_key
        return None

    def _stop_stream_by_id(self, server_url, interface, stream_id, row_idx=None):
        """POST a stop for a single stream_id, update UI + local state (do NOT flip 'enabled')."""

        # ⏹️ Cancel any pending auto-stop timer for this stream_id
        try:
            if hasattr(self, "_stop_timers"):
                t = self._stop_timers.pop(stream_id, None)
                if t:
                    t.stop()
        except Exception:
            pass

        try:
            payload = {"streams": [{"interface": interface, "stream_id": stream_id}]}
            resp = requests.post(f"{server_url}/api/traffic/stop", json=payload, timeout=6)
            ok = resp.ok
        except Exception as e:
            print(f"[AUTO-STOP ❌] {server_url} stream_id={stream_id}: {e}")
            ok = False

        # update status in memory
        port_key = self._find_port_key_for_stream(stream_id)
        if port_key and port_key in self.streams:
            for s in self.streams[port_key]:
                if s.get("stream_id") == stream_id:
                    s["status"] = "stopped"
                    break

        if row_idx is not None:
            self.update_stream_status(row_idx, "red")
        self.update_stream_table()
        print(f"[AUTO-STOP {'✅' if ok else '⚠️'}] stream_id={stream_id} on {server_url}")

    def _schedule_stream_auto_stop(self, server_url, port_label, stream_obj, row_idx):
        """
        If duration mode is 'Seconds' (>0), schedule a one-shot timer to stop this stream_id.
        """
        # lazy-init dict of timers
        if not hasattr(self, "_stop_timers"):
            self._stop_timers = {}

        d = (stream_obj.get("tx_duration")
             or self._prepare_duration(stream_obj))
        mode = str(d.get("mode", "Continuous"))
        seconds = int(d.get("seconds", 0) or 0)
        if mode.lower() != "seconds" or seconds <= 0:
            return  # nothing to schedule

        sid = stream_obj.get("stream_id")
        if not sid:
            return

        # cancel any previous timer for this stream_id
        old = self._stop_timers.pop(sid, None)
        if old and isinstance(old, QTimer):
            try:
                old.stop()
            except Exception:
                pass

        # resolve interface the backend expects in /stop
        interface = stream_obj.get("interface")
        if not interface:
            try:
                interface = port_label.split(" - ")[1].strip()
            except Exception:
                interface = port_label

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(
            lambda sid=sid, url=server_url, iface=interface, r=row_idx:
            self._stop_stream_by_id(url, iface, sid, row_idx=r)
        )
        timer.start(seconds * 1000)
        self._stop_timers[sid] = timer
        print(f"[AUTO-STOP ⏱️] Scheduled in {seconds}s for stream_id={sid}")

    def _selected_stream_rows(self):
        """
        Return sorted, unique selected row indices even if the user selected individual cells.
        Works regardless of selection behavior/mode.
        """
        sel = self.stream_table.selectionModel()
        if not sel:
            return []
        # union of selected rows and indexes (covers cell selections too)
        rows = {i.row() for i in sel.selectedRows()}
        rows.update({i.row() for i in sel.selectedIndexes()})
        return sorted(rows)

    @staticmethod
    def _normalize_interface_label(port_label: str) -> str:
        # "TG 1 - eth0" -> "eth0"
        try:
            return port_label.split(" - ", 1)[1].strip()
        except Exception:
            return port_label

    def _server_for_port(self, port_label: str):
        """Find the server dict for a table 'Interface' label like 'TG 3 - eth1'."""
        try:
            tg_part = port_label.split(" - ", 1)[0]  # "TG 3"
            tg_id = tg_part.replace("TG", "").strip()
        except Exception:
            tg_id = None

        for srv in getattr(self, "server_interfaces", []):
            if str(srv.get("tg_id")) == str(tg_id):
                return srv
        return None

    # ---------- actions ----------


    def start_stream(self):
        """Start the selected streams (incl. PCAP), normalize rate/duration, update UI, and schedule auto-stop.
           Also updates the single Start/Stop-ALL toggle if anything starts."""
        # 1) gather selection
        try:
            selected_rows = self.stream_table.selectionModel().selectedRows()
        except Exception:
            selected_rows = []

        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more streams to start.")
            return

        if not getattr(self, "server_interfaces", []):
            QMessageBox.warning(self, "No Server Selected", "Please select a TG chassis from the server list.")
            return

        server_payload_map = {}  # { server_url: { port_label: [(stream_obj, row_idx), ...] } }
        disabled_streams = []  # [(port_label, stream_name), ...]
        stream_by_id = {}  # { stream_id: stream_obj }
        row_by_id = {}  # { stream_id: row_index }
        sid_to_port = {}  # { stream_id: port_label }

        # 2) collect and prepare payloads
        for row in selected_rows:
            row_idx = row.row()
            port_item = self.stream_table.item(row_idx, 1)
            name_item = self.stream_table.item(row_idx, 2)
            if not port_item or not name_item:
                continue

            port = port_item.text().strip()  # e.g. "TG 0 - eth1"
            stream_name = name_item.text().strip()

            matched_stream = next(
                (s for s in self.streams.get(port, [])
                 if s.get("name") == stream_name or s.get("protocol_selection", {}).get("name") == stream_name),
                None
            )
            if not matched_stream:
                continue

            if not matched_stream.get("enabled", False):
                disabled_streams.append((port, stream_name))
                continue

            # ensure id/interface
            if not matched_stream.get("stream_id"):
                matched_stream["stream_id"] = str(uuid.uuid4())
            stream_id = matched_stream["stream_id"]

            try:
                normalized_interface = port.split(" - ")[1].strip()
            except Exception:
                normalized_interface = port  # fallback
            matched_stream["interface"] = normalized_interface
            matched_stream["port"] = port  # keep full label

            # sync master list entry
            for s in self.streams.get(port, []):
                if s.get("name") == matched_stream.get("name"):
                    s["interface"] = normalized_interface
                    s["stream_id"] = stream_id

            # find server for this TG
            try:
                tx_tg_id = port.split(" - ")[0].strip().replace("TG ", "")
            except Exception:
                tx_tg_id = ""
            tx_server = next((s for s in self.server_interfaces if str(s.get("tg_id")) == tx_tg_id), None)
            if not tx_server:
                print(f"[ERROR] No TX server found for TG {tx_tg_id}")
                continue

            server_url = tx_server["address"]
            stream_by_id[stream_id] = matched_stream
            row_by_id[stream_id] = row_idx
            sid_to_port[stream_id] = port

            # PCAP upload (if enabled)
            pcap_cfg = matched_stream.get("pcap_stream", {})
            if pcap_cfg.get("pcap_enabled", False):
                local_pcap = pcap_cfg.get("pcap_file_path")
                if not local_pcap or not os.path.isfile(local_pcap):
                    QMessageBox.warning(self, "Missing PCAP File",
                                        f"The PCAP file for stream '{stream_name}' is missing.")
                    continue

                server_pcap = self.upload_pcap_to_server(local_pcap, server_url)
                if not server_pcap:
                    QMessageBox.warning(self, "PCAP Upload Failed",
                                        f"Could not upload PCAP for stream '{stream_name}'.")
                    continue

                pcap_cfg["pcap_file_path"] = server_pcap
                matched_stream["pcap_stream"] = pcap_cfg

            # normalize transmit rate + duration
            try:
                matched_stream["tx_rate"] = self._prepare_tx_rate(matched_stream)
            except Exception as _e:
                print(f"[RATE] Could not normalize rate for '{stream_name}': {_e}")

            try:
                d = self._prepare_duration(matched_stream)
                matched_stream["tx_duration"] = d
                matched_stream["duration_mode"] = d.get("mode")
                matched_stream["duration_seconds"] = d.get("seconds")
                matched_stream["continuous"] = d.get("continuous")
            except Exception as _e:
                print(f"[DURATION] Could not normalize duration for '{stream_name}': {_e}")

            server_payload_map.setdefault(server_url, {}).setdefault(port, []).append((matched_stream, row_idx))

        # 3) notify about skipped disabled streams
        if disabled_streams:
            skipped = "\n".join([f"{name}  —  {port}" for port, name in disabled_streams])
            QMessageBox.information(self, "Disabled Streams Skipped",
                                    f"The following disabled streams were not started:\n\n{skipped}")

        # 4) send to servers and update UI
        any_started = False  # <-- track if anything actually started
        for server_url, per_port in server_payload_map.items():
            try:
                payload = {"streams": {p: [s for (s, _) in items] for p, items in per_port.items()}}
                resp = requests.post(f"{server_url}/api/traffic/start", json=payload, timeout=10)
                if not resp.ok:
                    print(f"[HTTP ❌] Failed to start on {server_url}: {resp.status_code} {resp.text[:200]}")
                    for items in per_port.values():
                        for _, r in items:
                            self.update_stream_status(r, "red")
                    continue

                data = resp.json()
                started = data.get("started_streams", [])
                if started:
                    ids_started = set()
                    for entry in started:
                        sid = entry.get("stream_id")
                        if not sid:
                            continue
                        ids_started.add(sid)

                        r = row_by_id.get(sid)
                        st = stream_by_id.get(sid)
                        if r is not None:
                            self.update_stream_status(r, "green")
                        if st:
                            st["status"] = "running"
                            st["enabled"] = True
                            st.setdefault("protocol_selection", {})["enabled"] = True
                            # schedule auto-stop if needed
                            self._schedule_stream_auto_stop(
                                server_url,
                                port_label=sid_to_port.get(sid, st.get("port", "")),
                                stream_obj=st,
                                row_idx=r
                            )
                            any_started = True

                    # final sync in self.streams
                    for port_key, stream_list in self.streams.items():
                        for i, s in enumerate(stream_list):
                            if s.get("stream_id") in ids_started:
                                self.streams[port_key][i]["status"] = "running"
                                self.streams[port_key][i]["enabled"] = True
                else:
                    # assume all sent are running
                    for port_label, items in per_port.items():
                        for st, r in items:
                            self.update_stream_status(r, "green")
                            st["status"] = "running"
                            st["enabled"] = True
                            st.setdefault("protocol_selection", {})["enabled"] = True
                            self._schedule_stream_auto_stop(
                                server_url,
                                port_label=port_label,
                                stream_obj=st,
                                row_idx=r
                            )
                            any_started = True

            except Exception as e:
                print(f"[ERROR] Could not reach {server_url}: {e}")
                for items in per_port.values():
                    for _, r in items:
                        self.update_stream_status(r, "red")

        # 5) refresh (session save removed - only save on explicit user action)
        self.update_stream_table()

        # 🔔 If anything started, flip the single Start/Stop-ALL toggle to the STOP icon now
        if any_started and hasattr(self, "update_all_streams_toggle_ui"):
            self.update_all_streams_toggle_ui()

    def stop_stream(self):
        """Stop only the selected streams. Do NOT toggle 'enabled'."""
        selected = self.stream_table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select a stream to stop.")
            return

        # Build requests per server
        stop_requests = {}  # server_url -> [{"interface": "...", "stream_id": "..."}]
        selected_triplets = []  # (port, name, row_idx)

        for idx in selected:
            row = idx.row()
            port = (self.stream_table.item(row, 1) or QTableWidgetItem("")).text().strip()
            name = (self.stream_table.item(row, 2) or QTableWidgetItem("")).text().strip()
            if not port or not name:
                continue
            selected_triplets.append((port, name, row))

            tg_id = port.split(" - ")[0].replace("TG", "").strip()
            interface = port.split(" - ")[1].strip()
            server = next((s for s in self.server_interfaces if str(s.get("tg_id")) == tg_id), None)
            if not server:
                continue

            # find the stream by name (supports protocol_selection.name)
            matched = next(
                (s for s in self.streams.get(port, [])
                 if s.get("name") == name or s.get("protocol_selection", {}).get("name") == name),
                None
            )
            if not matched:
                continue

            sid = matched.get("stream_id")
            if not sid:
                # nothing running on backend to stop
                continue

            stop_requests.setdefault(server["address"], []).append({
                "interface": interface,
                "stream_id": sid
            })

        # Send stop requests
        for server_url, items in stop_requests.items():
            try:
                r = requests.post(f"{server_url}/api/traffic/stop", json={"streams": items}, timeout=6)
                if r.ok:
                    print(f"[STOP ✅] Stopped {len(items)} stream(s) on {server_url}")
                else:
                    print(f"[STOP ❌] {server_url}: {r.status_code} {r.text}")
            except Exception as e:
                print(f"[STOP ERROR] {server_url}: {e}")

        # Update ONLY status locally; DO NOT alter 'enabled'
        for port, name, _ in selected_triplets:
            for s in self.streams.get(port, []):
                if s.get("name") == name or s.get("protocol_selection", {}).get("name") == name:
                    s["status"] = "stopped"

        self.update_stream_table()
        self.update_all_streams_toggle_ui()

    def _begin_button_feedback(self, button, *, busy_color=None, done_color=None, revert_delay_ms=800):
        """
        Optional visual feedback helper.
        - For the single Start/Stop-ALL toggle button, we *suppress* any color styling to avoid flicker.
        - If busy_color/done_color are None, we only disable/re-enable the button (no stylesheet changes).
        """
        if not button:
            return lambda: None

        # Don't style the unified toggle button (keep its icon steady)
        suppress_style = (hasattr(self, "all_streams_toggle_btn") and
                          button is self.all_streams_toggle_btn)

        original_style = button.styleSheet()
        button.setDisabled(True)

        if not suppress_style and busy_color:
            button.setStyleSheet(f"background-color: {busy_color}; color: white; border-radius: 6px;")
            QApplication.processEvents()

        def finish():
            try:
                if not suppress_style and done_color:
                    button.setStyleSheet(f"background-color: {done_color}; color: white; border-radius: 6px;")
                    QApplication.processEvents()
                    QTimer.singleShot(
                        revert_delay_ms,
                        lambda: (button.setStyleSheet(original_style), button.setDisabled(False))
                    )
                else:
                    if not suppress_style:
                        button.setStyleSheet(original_style)
                    button.setDisabled(False)
            finally:
                # Ensure the toggle icon/tooltip matches the current overall state
                try:
                    self.update_all_streams_toggle_ui()
                except Exception:
                    pass

        return finish

    def _any_stream_running(self) -> bool:
        """True if at least one stream is currently running."""
        for stream_list in getattr(self, "streams", {}).values():
            for s in stream_list:
                if s.get("status") == "running":
                    return True
        return False



    def _toggle_all_streams(self):
        """Click handler for the single toggle button."""
        if self._any_stream_running():
            # Will stop all
            self.stop_all_streams()
        else:
            # Will start all
            self.start_all_streams()
        # Safety: make sure icon reflects the *new* state
        self.update_all_streams_toggle_ui()

    def stop_all_streams(self):
        """
        Stop all RUNNING streams across all TGs/ports.
        - Does NOT change the 'enabled' flag of streams.
        - Updates per-row status icon to red.
        """
        finish = self._begin_button_feedback(
            getattr(self, "all_streams_toggle_btn", None),
            busy_color=None,
            done_color=None,
            revert_delay_ms=0
        )
        try:
            if not getattr(self, "server_interfaces", []):
                QMessageBox.warning(self, "No Server", "Please add/select at least one TG chassis.")
                return
            if not getattr(self, "streams", None):
                QMessageBox.information(self, "Nothing to Stop", "There are no streams loaded.")
                return

            row_index_map = {}
            try:
                for r in range(self.stream_table.rowCount()):
                    port_lbl = (self.stream_table.item(r, 1).text() or "").strip() if self.stream_table.item(r,
                                                                                                             1) else ""
                    name_lbl = (self.stream_table.item(r, 2).text() or "").strip() if self.stream_table.item(r,
                                                                                                             2) else ""
                    if port_lbl and name_lbl:
                        row_index_map[(port_lbl, name_lbl)] = r
            except Exception as _e:
                print(f"[STOP-ALL] Could not prebuild row map: {_e}")

            stop_requests = {}
            total_running = 0

            for port_label, stream_list in getattr(self, "streams", {}).items():
                try:
                    tg_id = port_label.split(" - ")[0].strip().replace("TG ", "")
                    interface = port_label.split(" - ")[1].strip()
                except Exception:
                    continue

                server = next((s for s in self.server_interfaces if str(s.get("tg_id")) == tg_id), None)
                if not server:
                    continue

                server_url = server.get("address")

                for s in stream_list:
                    if s.get("status") != "running":
                        continue
                    sid = s.get("stream_id")
                    if not sid:
                        continue

                    s_name = s.get("protocol_selection", {}).get("name") or s.get("name") or ""
                    stop_requests.setdefault(server_url, []).append({
                        "interface": interface,
                        "stream_id": sid,
                        "port_label": port_label,
                        "stream_name": s_name
                    })
                    total_running += 1

            if total_running == 0:
                QMessageBox.information(self, "Stop All", "No running streams found to stop.")
                return

            for server_url, items in stop_requests.items():
                try:
                    payload = {
                        "streams": [{"interface": it["interface"], "stream_id": it["stream_id"]} for it in items]}
                    resp = requests.post(f"{server_url}/api/traffic/stop", json=payload, timeout=10)
                    if resp.ok:
                        print(f"[STOP-ALL ✅] Stopped {len(items)} stream(s) on {server_url}")
                        for it in items:
                            port_lbl = it["port_label"]
                            sid = it["stream_id"]
                            s_name = it["stream_name"]

                            for i, s in enumerate(self.streams.get(port_lbl, [])):
                                if s.get("stream_id") == sid:
                                    self.streams[port_lbl][i]["status"] = "stopped"
                                    break

                            row_idx = row_index_map.get((port_lbl, s_name))
                            if row_idx is not None:
                                try:
                                    self.update_stream_status(row_idx, "red")
                                except Exception as _e:
                                    print(f"[STOP-ALL] Row icon update failed: {port_lbl}, {s_name}: {_e}")
                    else:
                        print(f"[STOP-ALL ❌] Server {server_url} failed: {resp.status_code} {resp.text[:200]}")
                except Exception as e:
                    print(f"[STOP-ALL ERROR] Could not reach {server_url}: {e}")

            # Session save removed - only save on explicit user action (Save Session menu or Apply button)

            self.update_stream_table()
        finally:
            finish()

    def _any_running(self) -> bool:
        """Return True if any stream has status == 'running'."""
        for stream_list in getattr(self, "streams", {}).values():
            for s in stream_list:
                if s.get("status") == "running":
                    return True
        return False

    def update_all_streams_toggle_ui(self):
        """Refresh the single Start/Stop ALL toggle button's icon + tooltip."""
        try:
            btn = getattr(self, "all_streams_toggle_btn", None)
            if not btn:
                return

            # Any stream running?
            running = any(
                s.get("status") == "running"
                for sl in getattr(self, "streams", {}).values()
                for s in sl
            )

            icon_file = "icons/stopallstream.png" if running else "icons/startallstream.png"
            tip = "Stop ALL streams on all TGs / ports" if running else "Start ALL enabled streams"
            text_fallback = "Stop All" if running else "Start All"

            icon = QIcon(r_icon(icon_file))
            btn.setToolTip(tip)
            btn.setIcon(icon)
            btn.setIconSize(QSize(16, 16))

            # If the icon can’t be found/loaded, show text so the button isn’t blank
            if icon.isNull():
                btn.setText(text_fallback)
            else:
                btn.setText("")
        except Exception as e:
            print(f"[UI] update_all_streams_toggle_ui failed: {e}")

    def on_all_streams_toggle_clicked(self):
        """Click handler: stop all if any are running, else start all."""
        try:
            if self._any_running():
                self.stop_all_streams()
            else:
                self.start_all_streams()
        finally:
            # Make sure the button reflects the latest state
            self.update_all_streams_toggle_ui()

    def _is_stream_enabled(self, s: dict) -> bool:
        """Robust 'enabled' check from top-level or protocol_selection; accepts strings like 'Yes', 'true', '1'."""
        v = s.get("enabled", None)
        if v is None:
            v = s.get("protocol_selection", {}).get("enabled", None)
        if isinstance(v, str):
            v = v.strip().lower() in ("yes", "true", "1", "on")
        return bool(v)

    def start_all_streams(self):
        """Start ALL enabled streams across all visible TG ports; skip stale/unknown ports cleanly."""
        # Use the single toggle button for feedback (amber → green)
        finish = (self._begin_button_feedback(
            getattr(self, "all_streams_toggle_btn", None),
            busy_color="#f0ad4e",  # amber while working
            done_color="#28a745",  # green on success
            revert_delay_ms=900
        ) if hasattr(self, "_begin_button_feedback") else (lambda: None))

        try:
            # --- Sanity ---
            if not getattr(self, "server_interfaces", []):
                QMessageBox.warning(self, "No Server Selected", "Please select/add at least one TG chassis.")
                return
            if not getattr(self, "streams", {}):
                QMessageBox.information(self, "No Streams", "There are no streams to start.")
                return

            # Build set of valid, currently-visible port labels from the table
            valid_ports = set()
            try:
                for r in range(self.stream_table.rowCount()):
                    itm = self.stream_table.item(r, 1)
                    if itm:
                        valid_ports.add(itm.text().strip())
            except Exception:
                # If table not ready, fall back to all keys
                valid_ports = set(self.streams.keys())

            server_payload_map = {}  # { server_url: { port_label: [(stream_obj, row_idx), ...] } }
            disabled_streams = []  # [(port_label, name)]
            stream_by_id = {}  # { stream_id: stream_obj }
            row_by_id = {}  # { stream_id: row_index }
            sid_to_port = {}  # { stream_id: port_label }
            unknown_ports = set()  # ports in self.streams but not in the current UI

            # --- Collect & prepare payloads ---
            for port_label, stream_list in self.streams.items():
                # Skip ports not visible/known right now
                if port_label not in valid_ports:
                    unknown_ports.add(port_label)
                    continue

                # Resolve server for this TG
                try:
                    tx_tg_id = port_label.split(" - ")[0].strip().replace("TG ", "")
                except Exception:
                    tx_tg_id = ""
                tx_server = next((s for s in self.server_interfaces if str(s.get("tg_id")) == tx_tg_id), None)
                if not tx_server:
                    # No reachable server for this port; skip silently
                    continue

                server_url = tx_server["address"]

                for s in list(stream_list):
                    # Name for logs/UI
                    name = s.get("protocol_selection", {}).get("name") or s.get("name", "")
                    if not self._is_stream_enabled(s):
                        # Only mark disabled if this is a valid, current port
                        disabled_streams.append((port_label, name))
                        continue

                    # Ensure id/interface
                    if not s.get("stream_id"):
                        s["stream_id"] = str(uuid.uuid4())
                    try:
                        normalized_interface = port_label.split(" - ")[1].strip()
                    except Exception:
                        normalized_interface = ""
                    s["interface"] = normalized_interface

                    # PCAP handling
                    pcap_cfg = s.get("pcap_stream", {})
                    if pcap_cfg.get("pcap_enabled", False):
                        local_pcap = pcap_cfg.get("pcap_file_path")
                        if not local_pcap or not os.path.isfile(local_pcap):
                            print(f"[PCAP ❌] Missing file for '{name}' on {port_label}")
                            continue
                        server_pcap = self.upload_pcap_to_server(local_pcap, server_url)
                        if not server_pcap:
                            print(f"[PCAP ❌] Upload failed for '{name}' on {port_label}")
                            continue
                        pcap_cfg["pcap_file_path"] = server_pcap
                        s["pcap_stream"] = pcap_cfg

                    # Normalize rate/duration
                    try:
                        if hasattr(self, "_prepare_tx_rate"):
                            s["tx_rate"] = self._prepare_tx_rate(s)
                        if hasattr(self, "_prepare_duration"):
                            d = self._prepare_duration(s)
                            s["tx_duration"] = d
                            s["duration_mode"] = d.get("mode")
                            s["duration_seconds"] = d.get("seconds")
                            s["continuous"] = d.get("continuous")
                    except Exception as _e:
                        print(f"[RATE] Could not normalize rate/duration for '{name}': {_e}")

                    # Row index (if helper exists)
                    row_idx = self._find_table_row(port_label, name) if hasattr(self, "_find_table_row") else None
                    stream_by_id[s["stream_id"]] = s
                    if row_idx is not None:
                        row_by_id[s["stream_id"]] = row_idx
                    sid_to_port[s["stream_id"]] = port_label

                    server_payload_map.setdefault(server_url, {}).setdefault(port_label, []).append((s, row_idx))

            # Let the user know about disabled streams ONLY from valid/visible ports
            if disabled_streams:
                msg = "\n".join([f"{n}  —  {p}" for p, n in disabled_streams])
                QMessageBox.information(
                    self,
                    "Disabled Streams Skipped",
                    f"The following disabled streams were not started:\n\n{msg}"
                )

            # (Optional) Log stale/unknown ports — don't show a modal dialog
            if unknown_ports:
                print(f"[INFO] Skipped stale/unknown ports (not in current UI): {sorted(unknown_ports)}")

            # --- Send to servers & update UI ---
            for server_url, per_port in server_payload_map.items():
                try:
                    payload = {"streams": {p: [s for (s, _) in items] for p, items in per_port.items()}}
                    resp = requests.post(f"{server_url}/api/traffic/start", json=payload, timeout=10)
                    if not resp.ok:
                        print(f"[HTTP ❌] Failed to start on {server_url}: {resp.status_code} {resp.text[:200]}")
                        for items in per_port.values():
                            for _, r in items:
                                if r is not None:
                                    self.update_stream_status(r, "red")
                        continue

                    data = resp.json()
                    started = data.get("started_streams", [])
                    if started:
                        ids_started = set()
                        for entry in started:
                            sid = entry.get("stream_id")
                            if not sid:
                                continue
                            ids_started.add(sid)

                            r = row_by_id.get(sid)
                            st = stream_by_id.get(sid)
                            if r is not None:
                                self.update_stream_status(r, "green")
                            if st:
                                st["status"] = "running"
                                st["enabled"] = True
                                st.setdefault("protocol_selection", {})["enabled"] = True
                                # schedule auto-stop if needed
                                self._schedule_stream_auto_stop(
                                    server_url,
                                    port_label=sid_to_port.get(sid, st.get("port", "")),
                                    stream_obj=st,
                                    row_idx=r
                                )

                        # Final sync into self.streams
                        for pkey, slist in self.streams.items():
                            for i, st in enumerate(slist):
                                if st.get("stream_id") in ids_started:
                                    self.streams[pkey][i]["status"] = "running"
                                    self.streams[pkey][i]["enabled"] = True
                                    self.streams[pkey][i].setdefault("protocol_selection", {})["enabled"] = True
                    else:
                        # Assume all we sent are running
                        for port_label, items in per_port.items():
                            for st, r in items:
                                if r is not None:
                                    self.update_stream_status(r, "green")
                                st["status"] = "running"
                                st["enabled"] = True
                                st.setdefault("protocol_selection", {})["enabled"] = True
                                self._schedule_stream_auto_stop(
                                    server_url,
                                    port_label=port_label,
                                    stream_obj=st,
                                    row_idx=r
                                )

                except Exception as e:
                    print(f"[ERROR] Could not reach {server_url}: {e}")
                    for items in per_port.values():
                        for _, r in items:
                            if r is not None:
                                self.update_stream_status(r, "red")

            # Refresh, then sync the single toggle icon (session save removed - only save on explicit user action)
            self.update_stream_table()
            if hasattr(self, "update_all_streams_toggle_ui"):
                self.update_all_streams_toggle_ui()

        finally:
            finish()

    def apply_stream(self):
        """Apply changes and restart only running streams, including inline-edited values.
           Also normalize & send tx_rate and tx_duration for each restarted stream."""
        # Session save removed - only save on explicit user action (Save Session menu or Apply button)

        row_count = self.stream_table.rowCount()

        # 🔄 Sync inline-edited values from the table into self.streams
        for row in range(row_count):
            port_item = self.stream_table.item(row, 1)
            name_item = self.stream_table.item(row, 2)
            if not port_item or not name_item:
                continue

            port = port_item.text().strip()
            stream_name = name_item.text().strip()
            if port not in self.streams:
                continue

            for s in self.streams[port]:
                ps = s.setdefault("protocol_selection", {})
                if ps.get("name") == stream_name or s.get("name") == stream_name:
                    # Enabled combo (column 3)
                    enabled_widget = self.stream_table.cellWidget(row, 3)
                    if enabled_widget:
                        is_enabled = enabled_widget.currentText().strip().lower() in ("yes", "true", "1")
                        ps["enabled"] = is_enabled
                        s["enabled"] = is_enabled

                    # Flow tracking combo (column 15)
                    flow_widget = self.stream_table.cellWidget(row, 15)
                    if flow_widget:
                        flow_enabled = flow_widget.currentText().strip().lower() in ("yes", "true", "1")
                        ps["flow_tracking_enabled"] = flow_enabled
                        s["flow_tracking_enabled"] = flow_enabled
                    break

        # 🚀 Apply only running + enabled streams per online server
        for server in getattr(self, "server_interfaces", []):
            if not server.get("online"):
                continue

            server_addr = server.get("address")
            tg_id = server.get("tg_id")

            for port_label, stream_list in self.streams.items():
                if not str(port_label).startswith(f"TG {tg_id}"):
                    continue

                running = []
                for s in stream_list:
                    ps = s.setdefault("protocol_selection", {})
                    if s.get("status") == "running" and s.get("enabled", ps.get("enabled", False)):
                        # Ensure consistency
                        ps["enabled"] = True
                        s["enabled"] = True
                        ft = ps.get("flow_tracking_enabled", s.get("flow_tracking_enabled", False))
                        ps["flow_tracking_enabled"] = ft
                        s["flow_tracking_enabled"] = ft

                        # Ensure stream_id exists
                        if not s.get("stream_id"):
                            s["stream_id"] = str(uuid.uuid4())

                        # ✅ Normalize TX rate (if helper exists)
                        try:
                            s["tx_rate"] = self._prepare_tx_rate(s)
                            # (Optional) flatten a few convenience keys if your server expects them
                            if isinstance(s["tx_rate"], dict):
                                rt = s["tx_rate"]
                                for k in ("type", "pps", "bitrate_mbps", "load_pct", "line_rate"):
                                    if k in rt:
                                        s[f"rate_{k}"] = rt[k]
                        except Exception as e:
                            print(f"[RATE] Could not normalize rate for '{ps.get('name', s.get('name', ''))}': {e}")

                        # ✅ Normalize Duration (if helper exists)
                        try:
                            s["tx_duration"] = self._prepare_duration(s)
                            if isinstance(s["tx_duration"], dict):
                                d = s["tx_duration"]
                                s["duration_mode"] = d.get("mode")
                                s["duration_seconds"] = d.get("seconds")
                                s["continuous"] = d.get("continuous")
                        except Exception as e:
                            print(
                                f"[DURATION] Could not normalize duration for '{ps.get('name', s.get('name', ''))}': {e}")

                        running.append(s)

                if not running:
                    continue

                # POST restart to this server / port
                try:
                    resp = requests.post(
                        f"{server_addr}/api/traffic/restart",
                        json={"port": port_label, "streams": running},
                        timeout=8
                    )
                    if resp.status_code == 200:
                        print(f"✅ Applied updates and restarted {len(running)} stream(s) on {port_label}")
                        # Mark as running+enabled in memory
                        for s in running:
                            s["status"] = "running"
                            s["enabled"] = True
                            s.setdefault("protocol_selection", {})["enabled"] = True
                    else:
                        print(f"❌ Failed to apply on {port_label}: {resp.status_code} - {resp.text[:200]}")
                except Exception as e:
                    print(f"⚠️ Error applying stream to {port_label} via {server_addr}: {e}")

        # 🔁 Refresh GUI
        self.update_stream_table()

    def send_inline_update_to_server(self, port, stream):
        """Send updated stream configuration to the corresponding TG server."""
        try:
            tg_id = port.split(" - ")[0]  # "TG 0"
            matching_servers = [s for s in self.server_interfaces if f"TG {s['tg_id']}" == tg_id]
            if not matching_servers:
                print(f"⚠️ No matching server found for {tg_id}")
                return

            server = matching_servers[0]
            url = f"{server['address']}/api/streams/update"
            payload = {"port": port, "stream": stream}
            response = requests.post(url, json=payload, timeout=5)

            if response.status_code == 200:
                print(f"✅ Stream update sent to {url}")
            else:
                print(f"❌ Failed to update stream. Status: {response.status_code}, Response: {response.text[:200]}")
        except Exception as e:
            print(f"❌ Error sending stream update to server: {e}")

    def upload_pcap_to_server(self, local_path, server_url):
        """Upload a PCAP file to the server and return the server-side path or None."""
        if not os.path.isfile(local_path):
            print(f"[❌] PCAP file not found: {local_path}")
            return None

        filename = os.path.basename(local_path)
        upload_url = f"{server_url}/api/pcap/upload"

        try:
            with open(local_path, "rb") as f:
                files = {"file": (filename, f)}
                response = requests.post(upload_url, files=files, timeout=15)
            if response.ok:
                data = response.json()
                return data.get("filepath")
            else:
                print(f"[UPLOAD ❌] Failed to upload PCAP: {response.status_code} {response.text[:200]}")
                return None
        except Exception as e:
            print(f"[UPLOAD ❌] Exception uploading PCAP: {e}")
            return None
