# stream_logic.py
import logging
import os
import uuid
import requests

logger = logging.getLogger(__name__)
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtCore import QTimer, QSize, Qt, QThread, QEventLoop
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QTableWidgetItem
from utils.qicon_loader import r_icon


def _normalize_interface(text: str) -> str:
    """Strip "TG N - ", "Port: ", surrounding whitespace, and any trailing junk.
    Returns just the bare interface name (e.g. "ens5np0").
    """
    if not text:
        return ""
    s = str(text).strip().strip('"').rstrip(",")
    if " - " in s:
        s = s.split(" - ", 1)[-1].strip()
    if "Port:" in s:
        s = s.replace("Port:", "").strip()
    if ":" in s:
        s = s.rsplit(":", 1)[-1].strip()
    parts = s.split()
    return parts[-1] if parts else s


def find_port_key(streams: dict, port_text: str):
    """Resolve a UI/table port string to the actual key in `streams`.

    self.streams is keyed by labels like "TG 0 - Port: ens5np0", but the table
    column may carry just "ens5np0" or "Port: ens5np0". This is the single source
    of truth for that mapping — every start/stop/edit path should call it instead
    of reinventing the normalization, which has historically drifted between sites.
    """
    target = _normalize_interface(port_text)
    if not target:
        return None
    for key in streams.keys():
        if _normalize_interface(key) == target:
            return key
    return None


class _TrafficPostWorker(QThread):
    """One-shot QThread that runs requests.post off the UI thread.

    Used by TrafficGenClientStreamLogic._post_traffic_async() to wrap the
    /api/traffic/{start,stop} POST calls. Without this, a slow server
    (which is normal under load — start/stop touches the database, the
    DPDK launcher fork, and tx_worker handshake) would block the UI for
    up to 10–15 seconds while the click handler waits for the response,
    freezing the entire app including the live stats chart.

    The worker stores the requests.Response (or the exception) on
    instance attributes; the caller pumps a local QEventLoop until our
    finished signal fires, then reads the result. Net effect: the call
    site reads as a blocking POST but the UI keeps repainting.
    """

    def __init__(self, url, payload, timeout, parent=None):
        super().__init__(parent)
        self._url = url
        self._payload = payload
        self._timeout = timeout
        self.response = None
        self.error = None

    def run(self):
        try:
            self.response = requests.post(
                self._url, json=self._payload, timeout=self._timeout
            )
        except Exception as e:
            self.error = e


class _HttpGetWorker(QThread):
    """One-shot QThread mirror of _TrafficPostWorker for GET requests.

    Used by _get_async() to fetch /api/interfaces (and similar) without
    freezing the UI. The Edit Stream and Add Stream dialogs need a list
    of available RX ports per server, and the previous code did one
    sync requests.get(timeout=5) per online TG on the UI thread —
    opening Edit on a chassis with one offline TG cost a 5s freeze
    while that GET timed out.
    """

    def __init__(self, url, timeout, parent=None):
        super().__init__(parent)
        self._url = url
        self._timeout = timeout
        self.response = None
        self.error = None

    def run(self):
        try:
            self.response = requests.get(self._url, timeout=self._timeout)
        except Exception as e:
            self.error = e


class _PcapUploadWorker(QThread):
    """One-shot QThread for streaming PCAP file uploads.

    Same pattern as _TrafficPostWorker / _HttpGetWorker but for
    multipart file uploads. Opens the file inside run() (i.e. on the
    worker thread) so the local file handle is closed before we read
    the response back, and the UI thread never owns the descriptor.

    Previously upload_pcap_to_server did a synchronous
    requests.post(files=..., timeout=15) on the UI thread, so the app
    froze for the full upload duration whenever the user started a
    PCAP-replay stream — which can be tens of seconds for moderately
    large captures over a slow link, and the 15s socket timeout
    silently truncated bigger ones.
    """

    def __init__(self, url, local_path, timeout, parent=None):
        super().__init__(parent)
        self._url = url
        self._local_path = local_path
        self._timeout = timeout
        self.response = None
        self.error = None

    def run(self):
        try:
            filename = os.path.basename(self._local_path)
            with open(self._local_path, "rb") as f:
                files = {"file": (filename, f)}
                self.response = requests.post(
                    self._url, files=files, timeout=self._timeout
                )
        except Exception as e:
            self.error = e


class TrafficGenClientStreamLogic:
    # ---------- helpers ----------

    def _post_traffic_async(self, server_url, action, payload, timeout=15):
        """Synchronous-looking wrapper around requests.post that runs the
        actual HTTP call in a background QThread.

        Behaves identically to ``requests.post(...)`` from the caller's
        perspective: returns a Response on HTTP completion, raises on
        transport error. The difference is that while the POST is in
        flight, this method pumps a local QEventLoop so the UI thread
        keeps processing paint events, timers, and (importantly) the
        live throughput chart's polling tick. No more multi-second
        freezes when the user clicks Start or Stop.

        Re-entrance protection (a user clicking Start/Stop again while
        we're pumping the loop) is handled by the existing
        _streams_in_flight() set in start_stream / stop_stream and by
        button.setDisabled() on the Start/Stop-ALL toggle.
        """
        url = f"{server_url}/api/traffic/{action}"
        loop = QEventLoop()
        worker = _TrafficPostWorker(url, payload, timeout)
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec_()
        worker.wait()  # ensure thread is fully done before reading attrs
        resp = worker.response
        err = worker.error
        # Permanent keepalive instead of an immediate deleteLater. On
        # PyQt5 5.15.11 + Python 3.14, returning from this function drops
        # the only ref to `worker` before Qt's scheduled deleteLater
        # runs; PyQt's wrapper destructor then deletes the C++ QThread
        # while Qt's post-run() teardown is still settling → SIGABRT.
        # _keepalive_worker holds the ref and trims it safely later.
        # (self may be a non-keepalive context in rare reuse; guard it.)
        if hasattr(self, "_keepalive_worker"):
            self._keepalive_worker(worker)
        if err is not None:
            raise err
        return resp

    def _get_async(self, url, timeout=5):
        """Synchronous-looking wrapper around requests.get that runs the
        actual HTTP call in a background QThread.

        Used by Edit Stream and Add Stream when they need to fetch the
        list of available RX ports per server. Same pattern as
        _post_traffic_async — pumps a local QEventLoop so the UI stays
        responsive while the GET is in flight, then returns the real
        requests.Response (or raises on transport error). Critical when
        a TG is unreachable: previously the sync GET timed out on the
        UI thread for ~5s, freezing the app before the dialog opened.
        """
        loop = QEventLoop()
        worker = _HttpGetWorker(url, timeout)
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec_()
        worker.wait()
        resp = worker.response
        err = worker.error
        # Permanent keepalive — see _post_traffic_async / _keepalive_worker.
        if hasattr(self, "_keepalive_worker"):
            self._keepalive_worker(worker)
        if err is not None:
            raise err
        return resp

    def _streams_in_flight(self) -> set:
        """Set of stream_ids that currently have a start/stop request outstanding.

        Lazy-initialised so the mixin works regardless of when the host class's
        __init__ runs. Used to refuse a second start/stop on the same stream
        while the previous request is in flight, eliminating the start-vs-stop
        race that previously left stream state up to server-side ordering.
        """
        if not hasattr(self, "_in_flight_stream_ids"):
            self._in_flight_stream_ids = set()
        return self._in_flight_stream_ids


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
            pps_raw = _get("stream_pps_rate", "1000")
            pps = int(str(pps_raw) or "1000")
            logger.debug(f"[RATE DEBUG] PPS mode - stream.get('stream_pps_rate')={stream.get('stream_pps_rate')}, ps.get('stream_pps_rate')={ps.get('stream_pps_rate')}, resolved={pps}")
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
        """POST a stop for a single stream_id, update UI + local state (do NOT flip 'enabled').

        Used by both the duration-expiry auto-stop QTimer and any other
        single-stream stop callsite. Participates in `_streams_in_flight()`
        so a user-initiated Stop click that lands while this method is
        pumping the QEventLoop (via _post_traffic_async) can't fire a
        second /stop for the same stream — the audit flagged this as a
        timer-vs-user race that produced "Could not reach" log noise the
        user couldn't trace.
        """

        # ⏹️ Cancel any pending auto-stop timer for this stream_id
        try:
            if hasattr(self, "_stop_timers"):
                t = self._stop_timers.pop(stream_id, None)
                if t:
                    t.stop()
        except Exception:
            pass

        # Reserve the in-flight slot so a parallel user-click on Stop for
        # the same stream_id is rejected. start_stream / stop_stream both
        # check this set before firing.
        in_flight = self._streams_in_flight()
        if stream_id in in_flight:
            logger.info(
                f"[AUTO-STOP] Skipping {stream_id} on {server_url} — "
                f"another stop is already in flight"
            )
            return
        in_flight.add(stream_id)

        try:
            # Look up stream_name for the payload — kept here for
            # consistency with Stop Selected / Stop All so the server
            # can fall back on name matching if stream_id misses
            # (audit LOW #16). Best-effort; missing it isn't fatal.
            sname = ""
            for s in self.streams.get(self._find_port_key_for_stream(stream_id) or "", []):
                if s.get("stream_id") == stream_id:
                    sname = (s.get("name")
                             or s.get("protocol_selection", {}).get("name")
                             or "")
                    break
            payload = {"streams": [{
                "interface": interface,
                "stream_id": stream_id,
                "stream_name": sname,
            }]}
            resp = self._post_traffic_async(server_url, "stop", payload, timeout=15)
            ok = resp.ok
        except Exception as e:
            logger.error(f"[AUTO-STOP] {server_url} stream_id={stream_id}: {e}")
            ok = False
        finally:
            in_flight.discard(stream_id)

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
        logger.info(f"[AUTO-STOP {'OK' if ok else 'WARN'}] stream_id={stream_id} on {server_url}")

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
        logger.info(f"[AUTO-STOP] Scheduled in {seconds}s for stream_id={sid}")

    def _cancel_all_auto_stop_timers(self):
        """Cancel every pending auto-stop QTimer.

        Called from the session-reload paths (load_session and friends in
        menu_actions.py) which reset self.streams = {}. Without this, any
        timer scheduled for a stream that's about to vanish from
        self.streams keeps firing — _stop_stream_by_id then can't find
        the port_key, the local update silently no-ops, and the server
        gets an orphan /stop POST against the OLD server_url+stream_id
        pair that the user can't correlate to any action they took.
        Audit LOW #13.
        """
        timers = getattr(self, "_stop_timers", None)
        if not timers:
            return
        cancelled = 0
        for sid, t in list(timers.items()):
            try:
                if isinstance(t, QTimer):
                    t.stop()
                cancelled += 1
            except Exception:
                pass
        timers.clear()
        if cancelled:
            logger.info(f"[AUTO-STOP] Cancelled {cancelled} pending timer(s) on session reload")

    def _cancel_auto_stop_timer(self, stream_id):
        """Cancel any pending auto-stop QTimer for this stream_id.

        Called from edit_selected_stream / remove_selected_stream so a
        timer scheduled when a 10s-duration stream was started doesn't
        fire after the stream has been edited (potentially a different
        duration now) or removed (stream_id no longer exists locally).
        Without this, the timer fires _stop_stream_by_id with a stale
        stream_id and the server gets an orphan /stop POST that the
        user can't correlate to any action they took.
        """
        if not stream_id:
            return
        try:
            timers = getattr(self, "_stop_timers", None)
            if not timers:
                return
            t = timers.pop(stream_id, None)
            if t and isinstance(t, QTimer):
                t.stop()
                logger.debug(f"[AUTO-STOP] Cancelled pending timer for {stream_id}")
        except Exception as e:
            logger.debug(f"[AUTO-STOP] Cancel-timer error for {stream_id}: {e}")

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
        # 1) gather selection - support both row and cell selection
        try:
            selection_model = self.stream_table.selectionModel()
            # Get selected rows (works with both SelectRows and SelectItems)
            selected_rows = selection_model.selectedRows()
            # If no rows selected, try getting rows from selected cells
            if not selected_rows:
                selected_rows = selection_model.selectedIndexes()
        except Exception:
            selected_rows = []

        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more streams to start.")
            return
        
        # Extract unique row indices from selection
        selected_row_indices = sorted(set(idx.row() for idx in selected_rows))

        if not getattr(self, "server_interfaces", []):
            QMessageBox.warning(self, "No Server Selected", "Please select a TG chassis from the server list.")
            return

        server_payload_map = {}  # { server_url: { port_label: [(stream_obj, row_idx), ...] } }
        disabled_streams = []  # [(port_label, stream_name), ...]
        stream_by_id = {}  # { stream_id: stream_obj }
        row_by_id = {}  # { stream_id: row_index }
        sid_to_port = {}  # { stream_id: port_label }

        # 2) collect and prepare payloads
        for row_idx in selected_row_indices:
            port_item = self.stream_table.item(row_idx, 1)
            name_item = self.stream_table.item(row_idx, 2)
            if not port_item or not name_item:
                continue

            port_text = port_item.text().strip()  # May be "ens5np0" or "Port: ens5np0"
            stream_name = name_item.text().strip()

            # Resolve the table's port string to the canonical key in self.streams
            port_key = find_port_key(self.streams, port_text)
            if not port_key:
                logger.error(
                    f"[START] No matching port key found for '{port_text}'. "
                    f"Available keys: {list(self.streams.keys())}"
                )
                continue

            matched_stream = next(
                (s for s in self.streams.get(port_key, [])
                 if s.get("name") == stream_name or s.get("protocol_selection", {}).get("name") == stream_name),
                None
            )
            if not matched_stream:
                logger.error(f"Stream '{stream_name}' not found in port '{port_key}'")
                continue

            # Check enabled flag - sync from table combo box first, then check both locations
            # Get enabled state from table combo box (column 3)
            enabled_widget = self.stream_table.cellWidget(row_idx, 3)
            if enabled_widget is not None:
                from traffic_client.server_section import _read_enabled_cell
                is_enabled_ui = _read_enabled_cell(enabled_widget)
                # Sync UI state to stream object
                matched_stream["enabled"] = is_enabled_ui
                if "protocol_selection" in matched_stream:
                    matched_stream["protocol_selection"]["enabled"] = is_enabled_ui
                enabled = is_enabled_ui
            else:
                # Fallback: check both locations in stream object
                enabled = matched_stream.get("enabled", False) or matched_stream.get("protocol_selection", {}).get("enabled", False)
            
            if not enabled:
                disabled_streams.append((port_key, stream_name))
                continue

            # ensure id/interface
            if not matched_stream.get("stream_id"):
                matched_stream["stream_id"] = str(uuid.uuid4())
            stream_id = matched_stream["stream_id"]

            # Refuse to fire a second action on a stream that's already mid-request.
            # Prevents the start/stop race where rapid clicks both reach the server.
            if stream_id in self._streams_in_flight():
                logger.info(
                    f"[START] Skipping '{stream_name}' on {port_key} — request already in flight"
                )
                continue

            normalized_interface = _normalize_interface(port_key) or port_text
            matched_stream["interface"] = normalized_interface
            matched_stream["port"] = port_key  # keep full label

            # sync master list entry
            for s in self.streams.get(port_key, []):
                if s.get("name") == matched_stream.get("name"):
                    s["interface"] = normalized_interface
                    s["stream_id"] = stream_id

            # find server for this TG
            try:
                tx_tg_id = port_key.split(" - ")[0].strip().replace("TG ", "")
            except Exception:
                tx_tg_id = ""
            tx_server = next((s for s in self.server_interfaces if str(s.get("tg_id")) == tx_tg_id), None)
            if not tx_server:
                logger.error(f"No TX server found for TG {tx_tg_id} from port_key '{port_key}'")
                continue

            server_url = tx_server["address"]
            stream_by_id[stream_id] = matched_stream
            row_by_id[stream_id] = row_idx
            sid_to_port[stream_id] = port_key

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
                logger.warning(f"[RATE] Could not normalize rate for '{stream_name}': {_e}")

            try:
                d = self._prepare_duration(matched_stream)
                matched_stream["tx_duration"] = d
                matched_stream["duration_mode"] = d.get("mode")
                matched_stream["duration_seconds"] = d.get("seconds")
                matched_stream["continuous"] = d.get("continuous")
            except Exception as _e:
                logger.warning(f"[DURATION] Could not normalize duration for '{stream_name}': {_e}")

            server_payload_map.setdefault(server_url, {}).setdefault(port_key, []).append((matched_stream, row_idx))

        # 3) notify about skipped disabled streams
        if disabled_streams:
            skipped = "\n".join([f"{name}  —  {port}" for port, name in disabled_streams])
            QMessageBox.information(self, "Disabled Streams Skipped",
                                    f"The following disabled streams were not started:\n\n{skipped}")

        # 4) send to servers and update UI
        any_started = False  # <-- track if anything actually started
        in_flight = self._streams_in_flight()
        # Mark all selected streams as in-flight + paint pending icon BEFORE sending,
        # so the user sees immediate feedback that their click was registered.
        for per_port in server_payload_map.values():
            for items in per_port.values():
                for st, r in items:
                    sid = st.get("stream_id")
                    if sid:
                        in_flight.add(sid)
                    self.update_stream_status(r, "yellow")

        errors_for_user = []  # collected and shown in a single dialog at the end

        for server_url, per_port in server_payload_map.items():
            try:
                payload = {"streams": {p: [s for (s, _) in items] for p, items in per_port.items()}}
                # Debug: Log what streams are being sent
                for port_label, stream_list in payload.get("streams", {}).items():
                    stream_names = [s.get("name") or s.get("protocol_selection", {}).get("name", "Unknown") for s in stream_list]
                    logger.debug(f"[START] Sending {len(stream_list)} stream(s) for port '{port_label}': {stream_names}")
                resp = self._post_traffic_async(server_url, "start", payload, timeout=10)
                if not resp.ok:
                    body = (resp.text or "").strip()[:300]
                    err_msg = f"{server_url}: HTTP {resp.status_code} {body}"
                    logger.error(f"[HTTP] Failed to start on {err_msg}")
                    errors_for_user.append(err_msg)
                    # Audit LOW #14: parse the response body even on
                    # non-OK and only red-flag streams the server did
                    # NOT report as started. The server's 4xx/5xx
                    # response can still carry a partial-success
                    # `started_streams` list (e.g. one stream out of
                    # five failed because its iface is down). Marking
                    # the whole batch red was misleading and made the
                    # user re-Start the four that were already running.
                    started_ids_partial = set()
                    try:
                        partial = resp.json().get("started_streams", []) or []
                        started_ids_partial = {
                            entry.get("stream_id") for entry in partial
                            if entry.get("stream_id")
                        }
                    except Exception:
                        pass
                    for items in per_port.values():
                        for st, r in items:
                            sid = st.get("stream_id")
                            if sid in started_ids_partial:
                                # This one DID start — green it.
                                if r is not None:
                                    self.update_stream_status(r, "green")
                                st["status"] = "running"
                                st["enabled"] = True
                                st.setdefault("protocol_selection", {})["enabled"] = True
                                any_started = True
                            else:
                                if r is not None:
                                    self.update_stream_status(r, "red")
                            if sid:
                                in_flight.discard(sid)
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
                        in_flight.discard(sid)

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
                            sid = st.get("stream_id")
                            if sid:
                                in_flight.discard(sid)

            except Exception as e:
                err_msg = f"{server_url}: {e}"
                logger.error(f"Could not reach {err_msg}")
                errors_for_user.append(err_msg)
                for items in per_port.values():
                    for st, r in items:
                        self.update_stream_status(r, "red")
                        sid = st.get("stream_id")
                        if sid:
                            in_flight.discard(sid)

        # Surface HTTP / connection errors to the user in a single dialog so
        # they don't have to read logs to find out the start failed.
        if errors_for_user:
            QMessageBox.warning(
                self,
                "Failed to Start Streams",
                "Some streams could not be started:\n\n" + "\n\n".join(errors_for_user),
            )

        # 5) refresh (session save removed - only save on explicit user action)
        self.update_stream_table()

        # 🔔 If anything started, flip the single Start/Stop-ALL toggle to the STOP icon now
        if any_started and hasattr(self, "update_all_streams_toggle_ui"):
            self.update_all_streams_toggle_ui()

    def stop_stream(self):
        """Stop only the selected streams. Do NOT toggle 'enabled'."""
        # Support both row and cell selection
        selection_model = self.stream_table.selectionModel()
        selected = selection_model.selectedRows()
        if not selected:
            # Fallback: get rows from selected cells
            selected = selection_model.selectedIndexes()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select a stream to stop.")
            return

        # Build requests per server
        stop_requests = {}  # server_url -> [{"interface": "...", "stream_id": "..."}]
        selected_triplets = []  # (port, name, row_idx, stream_id)
        rows_being_stopped = []  # row indices to flip to "yellow" pending icon

        for idx in selected:
            row = idx.row()
            port_text = (self.stream_table.item(row, 1) or QTableWidgetItem("")).text().strip()
            name_item = self.stream_table.item(row, 2)
            if not port_text or not name_item:
                continue

            name = name_item.text().strip()
            if not name:
                continue

            # Get stream_id from table item UserRole (most reliable)
            stream_id_from_table = name_item.data(Qt.UserRole)
            selected_triplets.append((port_text, name, row, stream_id_from_table))

            port_key = find_port_key(self.streams, port_text)
            if not port_key:
                logger.error(
                    f"[STOP] No matching port key found for '{port_text}'. "
                    f"Available keys: {list(self.streams.keys())}"
                )
                continue

            # Find the stream - prefer stream_id from table, then fallback to name matching
            matched = None
            if stream_id_from_table:
                # Match by stream_id (most reliable, especially after apply)
                for s in self.streams.get(port_key, []):
                    if s.get("stream_id") == stream_id_from_table:
                        matched = s
                        break
            
            # Fallback: match by name if stream_id not available or not found
            if not matched:
                matched = next(
                    (s for s in self.streams.get(port_key, [])
                     if s.get("name") == name or s.get("protocol_selection", {}).get("name") == name),
                    None
                )
            
            if not matched:
                logger.error(f"[STOP] Stream '{name}' (stream_id: {stream_id_from_table}) not found in port '{port_key}'")
                continue

            # Extract TG ID from port key (e.g., "TG 0 - Port: ens5np0" -> "0")
            try:
                tg_id = port_key.split(" - ")[0].replace("TG", "").strip()
            except (IndexError, AttributeError):
                # Fallback: try to get from stream object
                tg_id = matched.get("tg_id", "")
                if not tg_id:
                    logger.error(f"[STOP] Could not extract TG ID from port key '{port_key}' or stream object")
                    continue
            
            server = next((s for s in self.server_interfaces if str(s.get("tg_id")) == tg_id), None)
            if not server:
                logger.error(f"[STOP] No server found for TG ID '{tg_id}'")
                continue

            # Single canonical normalization for the wire format the server expects
            interface = (
                _normalize_interface(matched.get("interface", ""))
                or _normalize_interface(port_key)
                or _normalize_interface(port_text)
            )

            # Use stream_id from matched stream (should match stream_id_from_table if available)
            sid = matched.get("stream_id")
            if not sid:
                logger.warning(f"[STOP] Stream '{name}' has no stream_id, skipping")
                continue

            # Refuse if a request for this stream is already in flight (avoids start/stop race)
            if sid in self._streams_in_flight():
                logger.info(
                    f"[STOP] Skipping '{name}' on {port_key} — request already in flight"
                )
                continue
            self._streams_in_flight().add(sid)
            rows_being_stopped.append(row)
            self.update_stream_status(row, "yellow")

            # Include stream name for fallback matching if stream_id doesn't match
            stream_name_for_stop = matched.get("name") or matched.get("protocol_selection", {}).get("name") or name

            stop_requests.setdefault(server["address"], []).append({
                "interface": interface,
                "stream_id": sid,
                "stream_name": stream_name_for_stop  # Include name for fallback matching
            })

        # Send stop requests
        errors_for_user = []
        in_flight = self._streams_in_flight()
        for server_url, items in stop_requests.items():
            try:
                r = self._post_traffic_async(server_url, "stop", {"streams": items}, timeout=15)
                if r.ok:
                    logger.info(f"[STOP] Stopped {len(items)} stream(s) on {server_url}")
                else:
                    body = (r.text or "").strip()[:300]
                    err_msg = f"{server_url}: HTTP {r.status_code} {body}"
                    logger.error(f"[STOP] {err_msg}")
                    errors_for_user.append(err_msg)
            except Exception as e:
                err_msg = f"{server_url}: {e}"
                logger.error(f"[STOP] {err_msg}")
                errors_for_user.append(err_msg)
            finally:
                # Clear in-flight regardless of outcome — the request has completed
                # one way or the other; another click is now safe.
                for item in items:
                    sid = item.get("stream_id")
                    if sid:
                        in_flight.discard(sid)

        # Update ONLY status locally; DO NOT alter 'enabled'
        for port_text, name, _, stream_id_from_table in selected_triplets:
            port_key = find_port_key(self.streams, port_text)
            if port_key:
                # Prefer matching by stream_id, then fallback to name
                updated = False
                if stream_id_from_table:
                    for s in self.streams.get(port_key, []):
                        if s.get("stream_id") == stream_id_from_table:
                            s["status"] = "stopped"
                            updated = True
                            break

                # Fallback to name matching if stream_id didn't match
                if not updated:
                    for s in self.streams.get(port_key, []):
                        if s.get("name") == name or s.get("protocol_selection", {}).get("name") == name:
                            s["status"] = "stopped"
                            break

        if errors_for_user:
            QMessageBox.warning(
                self,
                "Failed to Stop Streams",
                "Some streams could not be stopped:\n\n" + "\n\n".join(errors_for_user),
            )

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
                logger.warning(f"[STOP-ALL] Could not prebuild row map: {_e}")

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
                    # Audit LOW #16: include stream_name for parity with
                    # Stop Selected. The server's /api/traffic/stop uses
                    # it as fallback matching when stream_id misses (e.g.
                    # after a server restart that re-allocated tracker
                    # IDs). Inconsistent payload shape between Stop
                    # Selected vs Stop All / auto-stop was a latent
                    # bomb if the server ever started leaning on the
                    # field for matching.
                    payload = {
                        "streams": [
                            {
                                "interface": it["interface"],
                                "stream_id": it["stream_id"],
                                "stream_name": it.get("stream_name", ""),
                            }
                            for it in items
                        ]
                    }
                    resp = self._post_traffic_async(server_url, "stop", payload, timeout=15)
                    if resp.ok:
                        logger.info(f"[STOP-ALL] Stopped {len(items)} stream(s) on {server_url}")
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
                                    logger.warning(f"[STOP-ALL] Row icon update failed: {port_lbl}, {s_name}: {_e}")
                    else:
                        logger.error(f"[STOP-ALL] Server {server_url} failed: {resp.status_code} {resp.text[:200]}")
                except Exception as e:
                    logger.error(f"[STOP-ALL] Could not reach {server_url}: {e}")

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
            # Match the larger 20px icon size used by the rest of the action bar.
            btn.setIconSize(QSize(20, 20))

            # Swap the button's semantic color so it's instantly readable as
            # "click to stop everything" (red) vs "click to start" (green).
            # The styles are stashed by setup_stream_section above.
            if running:
                stop_style = getattr(self, "_all_btn_stop_style", None)
                if stop_style:
                    btn.setStyleSheet(stop_style)
            else:
                start_style = getattr(self, "_all_btn_start_style", None)
                if start_style:
                    btn.setStyleSheet(start_style)

            # If the icon can’t be found/loaded, show text so the button isn’t blank
            if icon.isNull():
                btn.setText(text_fallback)
            else:
                btn.setText("")
        except Exception as e:
            logger.error(f"[UI] update_all_streams_toggle_ui failed: {e}")

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
            
            # Ensure stream_table exists and is initialized
            # Note: This check should not prevent normal operation - stream_table should always exist
            # If it doesn't, something is wrong with initialization, but we'll try to continue
            if not hasattr(self, "stream_table") or self.stream_table is None:
                logger.warning("[START-ALL] Stream table not initialized, falling back to stream keys")
                # Don't return early - continue with fallback logic below

            # Build set of valid, currently-visible port labels from the table
            valid_ports = set()
            try:
                for r in range(self.stream_table.rowCount()):
                    itm = self.stream_table.item(r, 1)
                    if itm:
                        valid_ports.add(itm.text().strip())
            except Exception as e:
                # If table not ready, fall back to all keys
                logger.error(f"[START-ALL] Error reading stream table: {e}, falling back to stream keys")
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
                    s["interface"] = _normalize_interface(port_label)

                    # PCAP handling
                    pcap_cfg = s.get("pcap_stream", {})
                    if pcap_cfg.get("pcap_enabled", False):
                        local_pcap = pcap_cfg.get("pcap_file_path")
                        if not local_pcap or not os.path.isfile(local_pcap):
                            logger.error(f"[PCAP] Missing file for '{name}' on {port_label}")
                            continue
                        server_pcap = self.upload_pcap_to_server(local_pcap, server_url)
                        if not server_pcap:
                            logger.error(f"[PCAP] Upload failed for '{name}' on {port_label}")
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
                        logger.warning(f"[RATE] Could not normalize rate/duration for '{name}': {_e}")

                    # Row index (if helper exists)
                    row_idx = self._find_table_row(port_label, name) if hasattr(self, "_find_table_row") else None
                    stream_by_id[s["stream_id"]] = s
                    if row_idx is not None:
                        row_by_id[s["stream_id"]] = row_idx
                    sid_to_port[s["stream_id"]] = port_label

                    # Audit LOW #17: collapsed the per-stream MAC dump
                    # from three logger.debug calls into one. On a 64-
                    # stream Start All with LOG_LEVEL=DEBUG that was
                    # 192 lines per click; now it's a single line per
                    # stream with the full payload. Gated on at least
                    # one Increment/Decrement mode so Fixed-only setups
                    # stay completely quiet.
                    if logger.isEnabledFor(logging.DEBUG):
                        mac_data = s.get("protocol_data", {}).get("mac", {})
                        if mac_data:
                            src_mode = mac_data.get("mac_source_mode", "Fixed")
                            dst_mode = mac_data.get("mac_destination_mode", "Fixed")
                            if src_mode in ("Increment", "Decrement") or dst_mode in ("Increment", "Decrement"):
                                logger.debug(
                                    f"[MAC] '{name}' src={src_mode}/{mac_data.get('mac_source_address')}"
                                    f" step={mac_data.get('mac_source_step')} count={mac_data.get('mac_source_count')}"
                                    f"  dst={dst_mode}/{mac_data.get('mac_destination_address')}"
                                    f" step={mac_data.get('mac_destination_step')} count={mac_data.get('mac_destination_count')}"
                                )

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
                logger.info(f"Skipped stale/unknown ports (not in current UI): {sorted(unknown_ports)}")

            # Reserve in-flight slots for every stream we're about to
            # start, BEFORE sending. The toggle button's setDisabled
            # alone isn't enough — a user could click a per-row Start
            # button while Start All is mid-pump and double-fire on
            # the same stream. Mirrors what start_stream does at the
            # equivalent point in its own flow. Audit MED #8.
            in_flight = self._streams_in_flight()
            in_flight_added_here = []
            for per_port in server_payload_map.values():
                for items in per_port.values():
                    for st, r in items:
                        sid = st.get("stream_id")
                        if sid and sid not in in_flight:
                            in_flight.add(sid)
                            in_flight_added_here.append(sid)
                        if r is not None:
                            self.update_stream_status(r, "yellow")

            # --- Send to servers & update UI ---
            for server_url, per_port in server_payload_map.items():
                try:
                    payload = {"streams": {p: [s for (s, _) in items] for p, items in per_port.items()}}
                    resp = self._post_traffic_async(server_url, "start", payload, timeout=10)
                    if not resp.ok:
                        logger.error(f"[HTTP] Failed to start on {server_url}: {resp.status_code} {resp.text[:200]}")
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
                    logger.error(f"Could not reach {server_url}: {e}")
                    for items in per_port.values():
                        for _, r in items:
                            if r is not None:
                                self.update_stream_status(r, "red")

            # Refresh, then sync the single toggle icon (session save removed - only save on explicit user action)
            self.update_stream_table()
            if hasattr(self, "update_all_streams_toggle_ui"):
                self.update_all_streams_toggle_ui()

        finally:
            # Release the in-flight reservations made above. Done in
            # `finally` so we always clear them even on exceptions; the
            # alternative would leak the lock and prevent any further
            # Start/Stop on those streams until the next session reload.
            try:
                if "in_flight_added_here" in locals():
                    in_flight_set = self._streams_in_flight()
                    for sid in in_flight_added_here:
                        in_flight_set.discard(sid)
            except Exception:
                pass
            finish()

    def apply_stream(self):
        """Apply changes and restart only running streams, including inline-edited values.
           Also normalize & send tx_rate and tx_duration for each restarted stream."""
        # Session save removed - only save on explicit user action (Save Session menu or Apply button)

        # Re-entrance guard. apply_stream pumps a local QEventLoop via
        # _post_traffic_async (sometimes for several seconds when running
        # streams need restart), and Qt keeps processing button clicks
        # while the loop pumps. Without this guard, two rapid clicks fire
        # two parallel /api/traffic/restart POSTs with identical payloads
        # — the audit flagged this as a real footgun. Also disable the
        # button + show busy cursor so the user gets visible feedback
        # that Apply is doing work.
        if getattr(self, "_apply_in_flight", False):
            logger.info("[APPLY] Skipping click — apply already in flight")
            return
        self._apply_in_flight = True
        from PyQt5.QtCore import Qt as _Qt
        from PyQt5.QtGui import QCursor as _QCursor
        btn = getattr(self, "apply_stream_button", None)
        if btn is not None:
            btn.setEnabled(False)
        QApplication.setOverrideCursor(_QCursor(_Qt.WaitCursor))
        try:
            self._apply_stream_body()
        finally:
            QApplication.restoreOverrideCursor()
            if btn is not None:
                btn.setEnabled(True)
            self._apply_in_flight = False

    def _apply_stream_body(self):
        """The actual Apply logic — wrapped by apply_stream() so the
        re-entrance guard, busy-cursor + button-disabled feedback can
        share a single try/finally without indenting the whole method."""
        row_count = self.stream_table.rowCount()

        # 🔄 Sync inline-edited values from the table into self.streams
        for row in range(row_count):
            port_item = self.stream_table.item(row, 1)
            name_item = self.stream_table.item(row, 2)
            if not port_item or not name_item:
                continue

            port_text = port_item.text().strip()  # May be "ens5np0" or "Port: ens5np0"
            stream_name = name_item.text().strip()

            port_key = find_port_key(self.streams, port_text)
            if not port_key or port_key not in self.streams:
                continue

            # Find stream - prefer stream_id from table item UserRole, then fallback to name matching
            matched_stream = None
            # name_item already retrieved above at line 1041, reuse it
            stream_id_from_table = None
            if name_item:
                stream_id_from_table = name_item.data(Qt.UserRole)
            
            if stream_id_from_table:
                # Match by stream_id (most reliable)
                for s in self.streams[port_key]:
                    if s.get("stream_id") == stream_id_from_table:
                        matched_stream = s
                        break
            
            # Fallback: match by name if stream_id not available
            if not matched_stream:
                for s in self.streams[port_key]:
                    ps = s.setdefault("protocol_selection", {})
                    stream_name_in_obj = ps.get("name") or s.get("name", "")
                    if stream_name_in_obj == stream_name:
                        matched_stream = s
                        break
            
            # Last resort: use row index (assumes table order matches stream list order)
            if not matched_stream and row < len(self.streams[port_key]):
                matched_stream = self.streams[port_key][row]
                logger.debug(f"[INLINE EDIT] Using row index fallback to find stream at row {row}")
            
            if matched_stream:
                ps = matched_stream.setdefault("protocol_selection", {})
                
                # CRITICAL: Preserve stream_id from table UserRole if available (most reliable source)
                # This ensures the stream_id matches what's in the table after edit
                if stream_id_from_table:
                    matched_stream["stream_id"] = stream_id_from_table
                    logger.debug(f"[INLINE EDIT] Preserved stream_id from table: {stream_id_from_table}")
                elif not matched_stream.get("stream_id"):
                    # If no stream_id in table and stream doesn't have one, generate it
                    import uuid
                    matched_stream["stream_id"] = str(uuid.uuid4())
                    logger.debug(f"[INLINE EDIT] Generated new stream_id: {matched_stream['stream_id']}")
                
                # Stream name (column 2) - editable text field (name_item already retrieved above)
                if name_item:
                    new_name = name_item.text().strip()
                    original_name = ps.get("name") or matched_stream.get("name", "")
                    if new_name and new_name != original_name:
                        ps["name"] = new_name
                        matched_stream["name"] = new_name
                        logger.debug(f"[INLINE EDIT] Updated stream name: '{original_name}' -> '{new_name}'")
                
                # Enabled cell (column 3) — now a checkbox, was previously a Yes/No combo
                enabled_widget = self.stream_table.cellWidget(row, 3)
                if enabled_widget is not None:
                    from traffic_client.server_section import _read_enabled_cell
                    is_enabled = _read_enabled_cell(enabled_widget)
                    ps["enabled"] = is_enabled
                    matched_stream["enabled"] = is_enabled

                # Frame size (column 8) - editable text field
                frame_size_item = self.stream_table.item(row, 8)
                if frame_size_item:
                    new_frame_size = frame_size_item.text().strip()
                    if new_frame_size:
                        try:
                            # Validate it's a number in valid range
                            frame_size_int = int(new_frame_size)
                            if 64 <= frame_size_int <= 9216:  # Valid Ethernet frame size range
                                # Normalize to string and update both protocol_selection and top-level for consistency
                                frame_size_str = str(frame_size_int)
                                ps["frame_size"] = frame_size_str
                                matched_stream["frame_size"] = frame_size_str
                                matched_stream.setdefault("protocol_selection", {})["frame_size"] = frame_size_str
                                logger.debug(f"[INLINE EDIT] Updated frame_size: {frame_size_str}")
                            else:
                                # Invalid range - revert to previous value
                                prev_frame_size = int(ps.get("frame_size") or matched_stream.get("frame_size") or 64)
                                frame_size_item.setText(str(prev_frame_size))
                                logger.warning(f"[INLINE EDIT] Invalid frame_size range: {new_frame_size}, reverted to {prev_frame_size}")
                        except ValueError:
                            # Invalid format - revert to previous value
                            prev_frame_size = int(ps.get("frame_size") or matched_stream.get("frame_size") or 64)
                            frame_size_item.setText(str(prev_frame_size))
                            logger.warning(f"[INLINE EDIT] Invalid frame_size format: {new_frame_size}, reverted to {prev_frame_size}")

                # Flow tracking combo (column 15)
                flow_widget = self.stream_table.cellWidget(row, 15)
                if flow_widget:
                    flow_enabled = flow_widget.currentText().strip().lower() in ("yes", "true", "1")
                    ps["flow_tracking_enabled"] = flow_enabled
                    matched_stream["flow_tracking_enabled"] = flow_enabled
            else:
                logger.warning(f"[INLINE EDIT] Could not find stream '{stream_name}' in port '{port_key}' to sync inline edits")

        # 🚀 Apply changes to ALL streams (not just running ones)
        # For running streams: restart them on the server
        # For stopped streams: just update the config locally (will be used when started)
        for server in getattr(self, "server_interfaces", []):
            if not server.get("online"):
                continue

            server_addr = server.get("address")
            tg_id = server.get("tg_id")

            for port_label, stream_list in self.streams.items():
                # Audit LOW #10: substring-match bug. The previous test
                # `port_label.startswith(f"TG {tg_id}")` matches the wrong
                # TG once IDs reach two digits — "TG 1 - …" startswith
                # "TG 1" is True, but so is "TG 10 - …".startswith("TG 1").
                # An apply targeting TG 1 would also send restart payloads
                # to TG 10's port_labels. Use exact-equality on the parsed
                # TG token instead.
                tg_part = str(port_label).split(" - ", 1)[0].strip()
                if tg_part != f"TG {tg_id}":
                    continue

                # Separate running and stopped streams
                running_streams = []
                stopped_streams = []
                
                for s in stream_list:
                    ps = s.setdefault("protocol_selection", {})
                    
                    # Ensure stream_id exists for all streams (but don't overwrite existing ones)
                    # This is a safety check - stream_id should already be set from sync above
                    if not s.get("stream_id"):
                        import uuid
                        s["stream_id"] = str(uuid.uuid4())
                        logger.debug(f"[APPLY] Generated stream_id for stream without one: {s['stream_id']}")
                    
                    # Ensure consistency for all streams
                    ft = ps.get("flow_tracking_enabled", s.get("flow_tracking_enabled", False))
                    ps["flow_tracking_enabled"] = ft
                    s["flow_tracking_enabled"] = ft
                    
                    # ✅ Normalize TX rate for all streams (if helper exists)
                    try:
                        s["tx_rate"] = self._prepare_tx_rate(s)
                        if isinstance(s["tx_rate"], dict):
                            rt = s["tx_rate"]
                            for k in ("type", "pps", "bitrate_mbps", "load_pct", "line_rate"):
                                if k in rt:
                                    s[f"rate_{k}"] = rt[k]
                    except Exception as e:
                        logger.warning(f"[RATE] Could not normalize rate for '{ps.get('name', s.get('name', ''))}': {e}")

                    # ✅ Normalize Duration for all streams (if helper exists)
                    try:
                        s["tx_duration"] = self._prepare_duration(s)
                        if isinstance(s["tx_duration"], dict):
                            d = s["tx_duration"]
                            s["duration_mode"] = d.get("mode")
                            s["duration_seconds"] = d.get("seconds")
                            s["continuous"] = d.get("continuous")
                    except Exception as e:
                        logger.warning(f"[DURATION] Could not normalize duration for '{ps.get('name', s.get('name', ''))}': {e}")

                    # Categorize streams
                    # Check both status and current enabled state (not just status)
                    # A stream might have status="running" but be disabled after edit
                    is_currently_enabled = s.get("enabled", ps.get("enabled", False))
                    is_running = s.get("status") == "running"
                    
                    if is_running and is_currently_enabled:
                        # Stream is running AND enabled - restart it with new config
                        # Ensure enabled flag is consistent
                        ps["enabled"] = True
                        s["enabled"] = True
                        running_streams.append(s)
                    elif is_running and not is_currently_enabled:
                        # Stream is running but was disabled - stop it instead of restarting
                        # Mark as stopped and flag it for stopping on server
                        s["status"] = "stopped"
                        s["_was_running"] = True  # Flag to indicate it needs to be stopped on server
                        ps["enabled"] = False
                        s["enabled"] = False
                        stopped_streams.append(s)
                        logger.info(f"[APPLY] Stream '{ps.get('name', s.get('name', ''))}' was running but is now disabled - will be stopped")
                    else:
                        # For stopped streams, preserve their enabled state from the model
                        stopped_streams.append(s)

                # Stop streams that were running but are now disabled
                streams_to_stop = [s for s in stopped_streams if s.get("status") == "stopped" and s.get("_was_running", False)]
                if streams_to_stop:
                    try:
                        # Extract interface name from port_label more robustly
                        interface_name = port_label
                        if " - " in port_label:
                            interface_name = port_label.split(" - ")[-1]
                        if "Port:" in interface_name:
                            interface_name = interface_name.replace("Port:", "").strip()
                        interface_name = interface_name.strip()
                        
                        # Audit LOW #16: include stream_name on the
                        # wire for consistency with Stop Selected.
                        stop_payload = {
                            "streams": [
                                {
                                    "interface": interface_name,
                                    "stream_id": s.get("stream_id"),
                                    "stream_name": (
                                        s.get("name")
                                        or s.get("protocol_selection", {}).get("name")
                                        or ""
                                    ),
                                }
                                for s in streams_to_stop if s.get("stream_id")
                            ]
                        }
                        resp = self._post_traffic_async(
                            server_addr, "stop", stop_payload, timeout=15
                        )
                        # Audit MED #9: use resp.ok (200-299) instead of
                        # `== 200`. A 202 Accepted or 204 No Content from
                        # the server would otherwise be flagged as a
                        # spurious failure here while every other call
                        # site uses .ok.
                        if resp.ok:
                            logger.info(f"Stopped {len(streams_to_stop)} stream(s) that were disabled on {port_label}")
                        else:
                            logger.error(f"Failed to stop disabled streams on {port_label}: {resp.status_code} - {resp.text[:200]}")
                    except Exception as e:
                        logger.error(f"Error stopping disabled streams on {port_label} via {server_addr}: {e}")
                    # Remove the temporary flag
                    for s in streams_to_stop:
                        s.pop("_was_running", None)
                
                # Restart running streams on the server
                if running_streams:
                    try:
                        # Extract interface name from port_label for consistency
                        interface_name = port_label
                        if " - " in port_label:
                            interface_name = port_label.split(" - ")[-1]
                        if "Port:" in interface_name:
                            interface_name = interface_name.replace("Port:", "").strip()
                        interface_name = interface_name.strip()
                        
                        # Ensure interface field is set in all streams before sending
                        for s in running_streams:
                            if not s.get("interface"):
                                s["interface"] = interface_name
                        
                        resp = self._post_traffic_async(
                            server_addr, "restart",
                            {"port": port_label, "streams": running_streams},
                            timeout=8
                        )
                        # Same audit-MED #9 fix as the stop path above.
                        if resp.ok:
                            logger.info(f"Applied updates and restarted {len(running_streams)} running stream(s) on {port_label}")
                            # Mark as running+enabled in memory and ensure interface is set
                            for s in running_streams:
                                s["status"] = "running"
                                s["enabled"] = True
                                s.setdefault("protocol_selection", {})["enabled"] = True
                                # Ensure interface field is preserved
                                if not s.get("interface"):
                                    s["interface"] = interface_name
                        else:
                            logger.error(f"Failed to apply running streams on {port_label}: {resp.status_code} - {resp.text[:200]}")
                    except Exception as e:
                        logger.error(f"Error applying running streams to {port_label} via {server_addr}: {e}")
                
                # For stopped streams, changes are already applied to self.streams above
                # They will be used when the stream is started later
                if stopped_streams:
                    logger.info(f"Applied changes to {len(stopped_streams)} stopped stream(s) on {port_label} (will take effect when started)")

        # All in-memory edits have been pushed to the server; clear the dirty set
        # so the Apply button drops its "unapplied edits" highlight.
        if hasattr(self, "clear_dirty_streams"):
            self.clear_dirty_streams()

        # 🔁 Refresh GUI - this will show all streams with their updated configurations
        self.update_stream_table()

    def send_inline_update_to_server(self, port, stream):
        """Send updated stream configuration to the corresponding TG server."""
        try:
            tg_id = port.split(" - ")[0]  # "TG 0"
            matching_servers = [s for s in self.server_interfaces if f"TG {s['tg_id']}" == tg_id]
            if not matching_servers:
                logger.warning(f"No matching server found for {tg_id}")
                return

            server = matching_servers[0]
            url = f"{server['address']}/api/streams/update"
            payload = {"port": port, "stream": stream}
            response = requests.post(url, json=payload, timeout=5)

            if response.status_code == 200:
                logger.info(f"Stream update sent to {url}")
            else:
                logger.error(f"Failed to update stream. Status: {response.status_code}, Response: {response.text[:200]}")
        except Exception as e:
            logger.error(f"Error sending stream update to server: {e}")

    def upload_pcap_to_server(self, local_path, server_url, timeout=120):
        """Upload a PCAP file to the server and return the server-side path or None.

        Hits the UI freeze path when called from start_stream /
        start_all_streams for PCAP-replay streams. Pumps a local Qt
        event loop while a worker thread does the actual upload, so
        the user keeps seeing live stats / chart while a 100MB PCAP
        crawls up a slow link. Bumped the timeout from 15s → 120s by
        default (large captures legitimately need it; the audit
        flagged the 15s as way too tight).
        """
        if not os.path.isfile(local_path):
            logger.error(f"PCAP file not found: {local_path}")
            return None

        upload_url = f"{server_url}/api/pcap/upload"
        loop = QEventLoop()
        worker = _PcapUploadWorker(upload_url, local_path, timeout)
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec_()
        worker.wait()
        response = worker.response
        err = worker.error
        # Permanent keepalive — see _post_traffic_async / _keepalive_worker.
        if hasattr(self, "_keepalive_worker"):
            self._keepalive_worker(worker)

        if err is not None:
            logger.error(f"[UPLOAD] Exception uploading PCAP: {err}")
            return None
        if response is None or not response.ok:
            sc = getattr(response, "status_code", "?")
            body = getattr(response, "text", "")[:200] if response else ""
            logger.error(f"[UPLOAD] Failed to upload PCAP: {sc} {body}")
            return None
        try:
            return response.json().get("filepath")
        except Exception as e:
            logger.error(f"[UPLOAD] Could not parse response JSON: {e}")
            return None
