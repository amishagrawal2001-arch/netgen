"""Install / Upgrade Server dialog — drives both paths from the client GUI.

Tab 1 — "Upgrade running server": uploads a wheel to /api/admin/upgrade_wheel
on a running server. Server pip-installs it and restarts itself via systemd.
Pure HTTP; no SSH.

Tab 2 — "Fresh install via SSH": SSHes (paramiko) into a bare Linux host,
sftp-copies install_ostg_complete.py + the wheel, runs the installer as
root, streams its output back. Covers the green-field provisioning case.

Both tabs use background QThreads so the UI stays responsive while long
operations (pip install ~30 s, full install_ostg_complete.py ~15-45 min)
run. Output is appended into a read-only log pane as it arrives.
"""

from __future__ import annotations

import html as _html
import os
import re
import time
from collections import deque
from shlex import quote as _shquote
from typing import Optional


# Lines we treat as errors / warnings / successes for log-pane coloring
# and the "what went wrong" extraction at install end. Matched case-
# insensitively against each line. Order matters — first match wins.
_ERROR_RX = re.compile(
    r"\b(error|exception|failed|fatal|traceback|"
    r"\[error\]|\[err\]|-E-|✗)\b",
    re.IGNORECASE,
)
_WARN_RX = re.compile(
    r"\b(warning|warn|\[warn\]|\[warning\]|⚠)\b",
    re.IGNORECASE,
)
_OK_RX = re.compile(r"(✓|\bsuccess(?:fully)?\b|\bOK\b)", re.IGNORECASE)
# Explicit bracket tags that override the noise heuristic. Some lines
# from install_ostg_complete.py contain both an "[ERROR]" tag AND a
# substring like "not found" that NOISE_RX would otherwise filter out
# (e.g. "[ERROR] Wheel file not found: dist/..."). When operators ship
# an explicit tag they mean it.
_EXPLICIT_ERR_RX = re.compile(r"\[(ERROR|ERR|FATAL)\]", re.IGNORECASE)
_EXPLICIT_WARN_RX = re.compile(r"\[(WARN|WARNING)\]", re.IGNORECASE)
# Strip ANSI escape sequences (\x1b[...m). install_dpdk.sh and the
# server's installer emit colored output; we re-color via HTML on the
# client side, so the raw escapes would otherwise show as visual junk.
_ANSI_RX = re.compile(r"\x1b\[[0-9;]*m")
# Lines we DON'T want to flag as errors even though they contain the word.
# The DPDK install for instance prints "no errors" / "0 errors" lots.
_NOISE_RX = re.compile(
    r"\b(no error|0 error|errors=0|error_count=0|may not exist|"
    r"if any were present|not loaded|no such image|not found)\b",
    re.IGNORECASE,
)

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


class WheelUploadWorker(QThread):
    """POST /api/admin/upgrade_wheel + poll /log until restart completes."""

    log_chunk = pyqtSignal(str)
    status = pyqtSignal(str)        # human-readable status updates
    finished_ok = pyqtSignal(bool)  # True = upgrade succeeded + restart OK

    def __init__(self, server_url: str, wheel_path: str, auth_token: str = ""):
        super().__init__()
        self.server_url = server_url.rstrip("/")
        self.wheel_path = wheel_path
        self.auth_token = (auth_token or "").strip()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _headers(self) -> dict:
        h = {}
        if self.auth_token:
            h["Authorization"] = f"Bearer {self.auth_token}"
        return h

    def run(self) -> None:
        try:
            import requests
        except Exception as e:
            self.log_chunk.emit(f"[client] requests import failed: {e}\n")
            self.finished_ok.emit(False)
            return

        wheel_name = os.path.basename(self.wheel_path)
        wheel_size = os.path.getsize(self.wheel_path)

        # 1. Upload
        self.status.emit(f"Uploading {wheel_name} ({wheel_size/1024:.0f} KB)...")
        self.log_chunk.emit(f"[client] POST {self.server_url}/api/admin/upgrade_wheel\n")
        try:
            with open(self.wheel_path, "rb") as fh:
                r = requests.post(
                    f"{self.server_url}/api/admin/upgrade_wheel",
                    headers=self._headers(),
                    files={"wheel": (wheel_name, fh, "application/octet-stream")},
                    timeout=120,
                )
        except Exception as e:
            self.log_chunk.emit(f"[client] upload failed: {e}\n")
            self.finished_ok.emit(False)
            return

        if r.status_code != 200:
            self.log_chunk.emit(
                f"[client] server rejected upload: {r.status_code} {r.text[:400]}\n"
            )
            self.finished_ok.emit(False)
            return
        body = r.json()
        self.log_chunk.emit(f"[client] pip pid={body.get('pid')} log={body.get('log_path')}\n")

        # 2. Poll log
        self.status.emit("pip install running on server...")
        last_len = 0
        deadline = time.time() + 600  # 10 min cap for pip alone
        restart_seen = False
        while time.time() < deadline and not self._stop:
            try:
                lr = requests.get(
                    f"{self.server_url}/api/admin/upgrade_wheel/log",
                    headers=self._headers(),
                    timeout=10,
                )
                if lr.status_code != 200:
                    self.log_chunk.emit(f"[client] log poll: HTTP {lr.status_code}\n")
                    time.sleep(2)
                    continue
                lb = lr.json()
                log_text = lb.get("log", "") or ""
                # Emit just the new tail since last poll
                if len(log_text) > last_len:
                    new = log_text[last_len:]
                    self.log_chunk.emit(new)
                    last_len = len(log_text)

                if not lb.get("running", False):
                    rc = lb.get("return_code")
                    if rc == 0:
                        if lb.get("restart_scheduled") and not restart_seen:
                            restart_seen = True
                            self.status.emit("pip ok — server restarting via systemd...")
                        break
                    else:
                        self.log_chunk.emit(
                            f"[client] pip exited rc={rc}; aborting\n"
                        )
                        self.finished_ok.emit(False)
                        return
            except Exception as e:
                # Server probably restarting — log poll will fail mid-restart.
                # Don't bail; the health probe below will reconfirm.
                if restart_seen:
                    break
                self.log_chunk.emit(f"[client] log poll error: {e} (retrying)\n")
            time.sleep(2)

        # 3. Wait for /api/health to come back (server reboot)
        self.status.emit("Waiting for server to come back...")
        self.log_chunk.emit("[client] polling /api/health for restart...\n")
        health_deadline = time.time() + 90
        while time.time() < health_deadline and not self._stop:
            try:
                hr = requests.get(f"{self.server_url}/api/health", timeout=4)
                if hr.status_code == 200:
                    self.log_chunk.emit("[client] server healthy — upgrade complete\n")
                    self.status.emit("Upgrade complete — server back online")
                    self.finished_ok.emit(True)
                    return
            except Exception:
                pass
            time.sleep(2)

        self.log_chunk.emit("[client] server did not return to health within 90s\n")
        self.status.emit("Upgrade finished but server is not responding")
        self.finished_ok.emit(False)


class SshInstallWorker(QThread):
    """SSH into a bare host, copy wheel + installer, run install_ostg_complete.py."""

    log_chunk = pyqtSignal(str)
    status = pyqtSignal(str)
    finished_ok = pyqtSignal(bool)

    def __init__(
        self,
        host: str,
        user: str,
        password: Optional[str],
        key_path: Optional[str],
        wheel_path: str,
        installer_path: str,
        extra_flags: list,
        resume_mode: bool = False,
        port: int = 22,
    ):
        super().__init__()
        self.host = host
        self.user = user
        self.port = int(port) if port else 22
        self.password = password
        self.key_path = key_path
        self.wheel_path = wheel_path
        self.installer_path = installer_path
        self.extra_flags = extra_flags or []
        # When True, skip wheel/installer sftp + nohup spawn — assume an
        # install is already running on the target and jump straight to
        # the log-poll loop. Used by the dialog's reattach flow when a
        # previous SshInstallWorker died (client crash / WiFi blip /
        # operator closed the window).
        self.resume_mode = bool(resume_mode)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            import paramiko
        except Exception as e:
            self.log_chunk.emit(
                f"[client] paramiko import failed: {e}\n"
                f"[client] install paramiko in the client env: pip install paramiko\n"
            )
            self.finished_ok.emit(False)
            return

        self.status.emit(f"Connecting to {self.user}@{self.host}...")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            kwargs = {
                "hostname": self.host,
                "username": self.user,
                "port": self.port,
                "timeout": 15,
            }
            if self.key_path:
                kwargs["key_filename"] = self.key_path
                self.log_chunk.emit(f"[client] auth: key {self.key_path}\n")
            else:
                kwargs["password"] = self.password
                self.log_chunk.emit("[client] auth: password\n")
            client.connect(**kwargs)
        except Exception as e:
            self.log_chunk.emit(f"[client] SSH connect failed: {e}\n")
            self.finished_ok.emit(False)
            return

        remote_dir = "/tmp/netgen_install"
        wheel_remote = f"{remote_dir}/{os.path.basename(self.wheel_path or 'wheel.whl')}"
        installer_remote = f"{remote_dir}/install_ostg_complete.py"

        # Resume-mode skips the entire upload + spawn flow; the install
        # is already running. We just need to attach to its log file.
        if self.resume_mode:
            self.log_chunk.emit(
                f"[client] resume mode: attaching to /var/log/netgen-install.log "
                f"on {self.host}\n"
            )
            self.status.emit("Resuming monitoring...")
        else:
            try:
                sftp = client.open_sftp()
            except Exception as e:
                self.log_chunk.emit(f"[client] SFTP open failed: {e}\n")
                client.close()
                self.finished_ok.emit(False)
                return

            try:
                try:
                    sftp.mkdir(remote_dir)
                except IOError:
                    pass  # exists
                self.status.emit(f"Copying wheel to {wheel_remote}...")
                self.log_chunk.emit(f"[client] sftp put {self.wheel_path} → {wheel_remote}\n")
                sftp.put(self.wheel_path, wheel_remote)
                self.log_chunk.emit(f"[client] sftp put {self.installer_path} → {installer_remote}\n")
                sftp.put(self.installer_path, installer_remote)
                sftp.chmod(installer_remote, 0o755)
            except Exception as e:
                self.log_chunk.emit(f"[client] SFTP upload failed: {e}\n")
                try:
                    sftp.close()
                except Exception:
                    pass
                client.close()
                self.finished_ok.emit(False)
                return
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass

        # Detached-install design: spawn the installer with nohup, redirect
        # stdout/stderr to a shared log file, exit immediately. The client
        # then polls the log file (incremental tail by byte offset) and the
        # process status until the install finishes.
        #
        # Why: get_pty=True + line-buffered stdout (what we had before)
        # streams the install live, but the PTY also means the remote
        # process gets SIGHUP when the SSH connection drops. Close the
        # dialog mid-install → install dies → leaves apt half-configured
        # on a 15-min DPDK build. With nohup + log polling:
        #   • Install survives client exit / WiFi blip / laptop close.
        #   • Operator can re-open the dialog later; we detect the live
        #     install and offer to resume monitoring.
        #   • The log file is also a permanent record on the target.
        log_path = "/var/log/netgen-install.log"
        pid_path = "/var/run/netgen-install.pid"
        exit_path = "/var/run/netgen-install.exit"

        # In normal mode, refuse to start a new install if one is
        # already running on the target. In resume mode, this check
        # is the precondition (the dialog already prompted the user
        # to attach instead of starting new).
        if not self.resume_mode:
            try:
                stdin0, stdout0, _ = client.exec_command(
                    f"[ -f {pid_path} ] && kill -0 $(cat {pid_path}) 2>/dev/null && cat {pid_path} || echo NONE",
                    timeout=10,
                )
                existing = (stdout0.read().decode().strip() or "NONE")
                if existing != "NONE":
                    self.log_chunk.emit(
                        f"[client] install already running on {self.host} (pid={existing}). "
                        f"Close this dialog and re-open it to resume monitoring.\n"
                    )
                    self.status.emit(f"Install already in progress on {self.host}")
                    client.close()
                    self.finished_ok.emit(False)
                    return
            except Exception as e:
                self.log_chunk.emit(f"[client] pre-flight pid check failed: {e}\n")

        cmd_flags = " ".join(self.extra_flags)
        # The installer is sudo'd when not running as root. The whole
        # invocation runs under a single `sudo sh -c '...'` so sudo
        # owns the nohup chain and writes the pid/exit files with
        # root privileges (matching /var/log + /var/run conventions).
        installer_invocation = (
            f"cd {remote_dir} && "
            f"python3 install_ostg_complete.py "
            f"--wheel {wheel_remote} {cmd_flags}"
        ).strip()
        # Wrapper script that:
        #   1. Truncates the log file
        #   2. Writes its own pid to pid_path
        #   3. Runs the installer, captures exit code, writes to exit_path
        #   4. Cleans up the pid file
        # Wrapped in `nohup sh -c '...' < /dev/null > /dev/null 2>&1 &`
        # so it survives the SSH disconnect.
        wrapper = (
            f"rm -f {log_path} {exit_path} && "
            f"echo $$ > {pid_path} && "
            f"({installer_invocation}) >> {log_path} 2>&1; "
            f"rc=$?; "
            f"echo $rc > {exit_path}; "
            f"rm -f {pid_path}"
        )
        spawn_cmd = (
            f"nohup sh -c {_shquote(wrapper)} < /dev/null > /dev/null 2>&1 & "
            f"echo $!"
        )
        if self.user != "root":
            spawn_cmd = f"sudo sh -c {_shquote(spawn_cmd)}"

        if not self.resume_mode:
            self.status.emit("Spawning installer (detached) on server...")
            self.log_chunk.emit(
                f"[client] spawn: {installer_invocation}\n"
                f"[client]   log: {log_path}\n"
                f"[client]   pid: {pid_path}\n"
                f"[client]  exit: {exit_path}\n"
                f"[client] (install runs detached — closing this dialog "
                f"won't kill it; re-open later to resume monitoring)\n"
            )

            try:
                stdin1, stdout1, stderr1 = client.exec_command(spawn_cmd, timeout=15)
                launcher_rc = stdout1.channel.recv_exit_status()
                spawn_out = stdout1.read().decode(errors="replace").strip()
                spawn_err = stderr1.read().decode(errors="replace").strip()
                if launcher_rc != 0:
                    self.log_chunk.emit(
                        f"[client] spawn failed rc={launcher_rc} "
                        f"stdout={spawn_out!r} stderr={spawn_err!r}\n"
                    )
                    self.finished_ok.emit(False)
                    client.close()
                    return
                if spawn_out:
                    self.log_chunk.emit(f"[client] spawned pid={spawn_out}\n")
            except Exception as e:
                self.log_chunk.emit(f"[client] spawn exception: {e}\n")
                self.finished_ok.emit(False)
                client.close()
                return

        # Poll loop: read log incrementally + check process state.
        self.status.emit("Install running on server — streaming log...")
        log_offset = 0
        idle_polls = 0
        while not self._stop:
            try:
                # Read new bytes since last offset. tail -c +N starts at
                # byte N (1-indexed in tail's syntax, hence +1).
                tail_cmd = f"tail -c +{log_offset + 1} {log_path} 2>/dev/null"
                _, tail_stdout, _ = client.exec_command(tail_cmd, timeout=15)
                new = tail_stdout.read().decode("utf-8", errors="replace")
                if new:
                    self.log_chunk.emit(new)
                    log_offset += len(new.encode("utf-8"))
                    idle_polls = 0
                else:
                    idle_polls += 1

                # Is the installer still alive?
                exit_check = (
                    f"if [ -f {exit_path} ]; then "
                    f"cat {exit_path}; "
                    f"else echo RUNNING; fi"
                )
                _, status_stdout, _ = client.exec_command(exit_check, timeout=10)
                status_line = status_stdout.read().decode().strip()
                if status_line and status_line != "RUNNING":
                    # Final tail pass to capture anything written between
                    # last poll and process exit
                    _, tail_stdout, _ = client.exec_command(
                        f"tail -c +{log_offset + 1} {log_path} 2>/dev/null",
                        timeout=15,
                    )
                    final_tail = tail_stdout.read().decode("utf-8", errors="replace")
                    if final_tail:
                        self.log_chunk.emit(final_tail)
                    try:
                        rc = int(status_line)
                    except ValueError:
                        rc = -1
                    self.log_chunk.emit(f"[client] installer exit rc={rc}\n")
                    if rc == 0:
                        self.status.emit("Install complete — server provisioned")
                        self.finished_ok.emit(True)
                    else:
                        self.status.emit(f"Install failed (rc={rc})")
                        self.finished_ok.emit(False)
                    break
            except Exception as e:
                # Transient SSH errors are expected on long installs (sshd
                # restart during apt, network blip). Log but keep polling.
                self.log_chunk.emit(f"[client] poll error: {e} (retrying)\n")
            # Adaptive backoff: poll fast (1s) while output is flowing,
            # slow down to 5s when idle to reduce SSH churn.
            time.sleep(1 if idle_polls < 3 else 5)
        else:
            # User stopped — the install keeps running on the target.
            # Don't emit finished_ok(False) here; emit a neutral status.
            self.log_chunk.emit(
                "[client] monitoring stopped — install continues on target.\n"
                "[client] re-open this dialog to resume monitoring.\n"
            )
            self.status.emit("Monitoring stopped — install still running on target")

        try:
            client.close()
        except Exception:
                pass


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class InstallServerDialog(QDialog):
    """Two-tab dialog: upgrade running server (HTTP) | fresh install (SSH)."""

    def __init__(
        self,
        parent=None,
        default_server_url: str = "",
        default_auth_token: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Install / Upgrade Server")
        self.setMinimumSize(820, 600)

        self._worker: Optional[QThread] = None
        # Ring buffer of recent ERROR-tagged lines from the log stream.
        # On install/upgrade failure the dialog peels these out into the
        # QMessageBox so operators don't have to scroll a 1000-line log
        # to find what went wrong. Capped to keep memory bounded on
        # long DPDK builds.
        self._recent_errors: deque = deque(maxlen=20)
        # Carry-over for split-mid-line chunks. SSH streams arrive on
        # arbitrary byte boundaries; we buffer trailing partial lines
        # so the error-extraction regex doesn't miss them on the next
        # chunk join.
        self._log_carry: str = ""

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Roll a new server build to a running netgen-server, or "
            "provision a fresh Linux host from this client. The server "
            "always needs Linux (DPDK + systemd + Docker for FRR); the "
            "AppImage we ship is client-only."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_upgrade_tab(default_server_url, default_auth_token),
                         "Upgrade running server")
        self.tabs.addTab(self._build_fresh_install_tab(), "Fresh install via SSH")
        layout.addWidget(self.tabs, 1)

        # Shared log pane (each tab writes here)
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)

        # Header row above the log: pop-out button on the right so the
        # operator can drag a much larger log window around alongside
        # the install dialog (15-min DPDK builds with ~1000 lines of
        # output deserve more than the cramped pane at the bottom of
        # this dialog). The popout shares the same QTextDocument so
        # appendHtml / appendPlainText calls show in both windows
        # automatically — no manual mirroring needed.
        log_header = QHBoxLayout()
        log_header.addStretch(1)
        self.popout_btn = QPushButton("Pop out ↗")
        self.popout_btn.setToolTip(
            "Open the log in a separate, freely-resizable window. "
            "The popout shares the same content — anything written "
            "to the log here appears there too."
        )
        self.popout_btn.setMaximumWidth(110)
        self.popout_btn.clicked.connect(self._toggle_log_popout)
        log_header.addWidget(self.popout_btn)
        log_layout.addLayout(log_header)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setStyleSheet(
            "QPlainTextEdit{font-family: ui-monospace, Menlo, Consolas, monospace; font-size:11px;}"
        )
        log_layout.addWidget(self.log_view)
        self.status_lbl = QLabel("Ready.")
        self.status_lbl.setStyleSheet("color:#475569; font-size:11px;")
        log_layout.addWidget(self.status_lbl)
        layout.addWidget(log_box, 1)

        # Holder for the popout window — created lazily on first click.
        self._log_popout: Optional[QDialog] = None

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)

    # -- Tab 1: HTTP upgrade -------------------------------------------------

    def _build_upgrade_tab(self, default_url: str, default_token: str) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.up_server = QLineEdit(default_url or "http://lab-box:5050")
        form.addRow("Server URL:", self.up_server)

        self.up_token = QLineEdit(default_token or "")
        self.up_token.setEchoMode(QLineEdit.Password)
        self.up_token.setPlaceholderText("(only if NETGEN_AUTH_TOKEN is set on server)")
        form.addRow("Auth token:", self.up_token)

        wheel_row = QHBoxLayout()
        self.up_wheel = QLineEdit()
        self.up_wheel.setPlaceholderText("/path/to/ostg_trafficgen-<v>-py3-none-any.whl")
        wheel_browse = QPushButton("Browse...")
        wheel_browse.clicked.connect(lambda: self._browse_wheel(self.up_wheel))
        wheel_row.addWidget(self.up_wheel, 1)
        wheel_row.addWidget(wheel_browse)
        form.addRow("Wheel file:", wheel_row)

        info = QLabel(
            "Uploads the wheel to /api/admin/upgrade_wheel. Server runs "
            "<code>pip install --upgrade --force-reinstall --no-deps</code> "
            "in its own Python, then triggers <code>systemctl restart "
            "netgen-server</code>. The client waits for /api/health to "
            "come back. Typical round-trip: 30–60 s."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#475569; font-size:11px;")
        form.addRow(info)

        self.up_btn = QPushButton("Upload && Upgrade")
        self.up_btn.clicked.connect(self._start_upgrade)
        form.addRow("", self.up_btn)

        return w

    def _browse_wheel(self, line_edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select wheel file", "", "Python wheels (*.whl);;All files (*)"
        )
        if path:
            line_edit.setText(path)

    def _start_upgrade(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "An operation is already in progress.")
            return
        url = (self.up_server.text() or "").strip()
        wheel = (self.up_wheel.text() or "").strip()
        token = (self.up_token.text() or "").strip()
        if not url:
            QMessageBox.warning(self, "Missing", "Server URL is required.")
            return
        if not wheel or not os.path.isfile(wheel):
            QMessageBox.warning(self, "Missing", "Pick a valid wheel file.")
            return

        self.log_view.clear()
        self._recent_errors.clear()
        self._log_carry = ""
        self._set_status("Starting upgrade...")
        self.up_btn.setEnabled(False)

        self._worker = WheelUploadWorker(url, wheel, token)
        self._worker.log_chunk.connect(self._append_log)
        self._worker.status.connect(self._set_status)
        self._worker.finished_ok.connect(self._upgrade_finished)
        self._worker.start()

    def _upgrade_finished(self, ok: bool) -> None:
        self.up_btn.setEnabled(True)
        if ok:
            QMessageBox.information(self, "Upgrade complete",
                                    "Server has restarted with the new wheel.")
        else:
            self._show_failure_dialog(
                "Upgrade failed",
                "The wheel upgrade did not complete. Most recent errors "
                "from the log:",
            )

    # -- Tab 2: SSH fresh install -------------------------------------------

    def _build_fresh_install_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        host_row = QHBoxLayout()
        self.ssh_host = QLineEdit()
        self.ssh_host.setPlaceholderText("lab-box.example.com")
        self.ssh_user = QLineEdit("root")
        self.ssh_user.setMaximumWidth(120)
        # SSH port — defaults to 22, but lab boxes behind jump hosts /
        # alternate sshd configs often use 2222 / 22000 / etc. Without
        # this field the dialog was unusable on those boxes.
        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(22)
        self.ssh_port.setMaximumWidth(80)
        self.ssh_port.setToolTip("SSH port (default 22)")
        host_row.addWidget(self.ssh_host, 1)
        host_row.addWidget(QLabel("user:"))
        host_row.addWidget(self.ssh_user)
        host_row.addWidget(QLabel("port:"))
        host_row.addWidget(self.ssh_port)
        form.addRow("Host:", host_row)

        # Auth toggle
        self.auth_grp = QButtonGroup(self)
        self.auth_pw_rb = QRadioButton("Password")
        self.auth_key_rb = QRadioButton("SSH key")
        self.auth_pw_rb.setChecked(True)
        self.auth_grp.addButton(self.auth_pw_rb)
        self.auth_grp.addButton(self.auth_key_rb)
        auth_row = QHBoxLayout()
        auth_row.addWidget(self.auth_pw_rb)
        auth_row.addWidget(self.auth_key_rb)
        auth_row.addStretch(1)
        form.addRow("Auth:", auth_row)

        self.ssh_password = QLineEdit()
        self.ssh_password.setEchoMode(QLineEdit.Password)
        self.ssh_password.setPlaceholderText("(SSH password — not stored)")
        form.addRow("Password:", self.ssh_password)

        key_row = QHBoxLayout()
        self.ssh_key = QLineEdit()
        self.ssh_key.setPlaceholderText("~/.ssh/id_ed25519")
        key_browse = QPushButton("Browse...")
        key_browse.clicked.connect(lambda: self._browse_file(self.ssh_key, "SSH key files (*)"))
        key_row.addWidget(self.ssh_key, 1)
        key_row.addWidget(key_browse)
        form.addRow("Key file:", key_row)

        self.auth_pw_rb.toggled.connect(self._update_auth_visibility)
        self._update_auth_visibility()

        # Wheel + installer paths
        wheel_row = QHBoxLayout()
        self.ssh_wheel = QLineEdit()
        self.ssh_wheel.setPlaceholderText("/path/to/ostg_trafficgen-<v>-py3-none-any.whl")
        wb = QPushButton("Browse...")
        wb.clicked.connect(lambda: self._browse_wheel(self.ssh_wheel))
        wheel_row.addWidget(self.ssh_wheel, 1)
        wheel_row.addWidget(wb)
        form.addRow("Wheel:", wheel_row)

        installer_row = QHBoxLayout()
        self.ssh_installer = QLineEdit(self._guess_installer_path())
        self.ssh_installer.setPlaceholderText("/path/to/install_ostg_complete.py")
        ib = QPushButton("Browse...")
        ib.clicked.connect(lambda: self._browse_file(self.ssh_installer, "Python (*.py)"))
        installer_row.addWidget(self.ssh_installer, 1)
        installer_row.addWidget(ib)
        form.addRow("Installer:", installer_row)

        # Flags
        flags_box = QGroupBox("install_ostg_complete.py flags (optional)")
        flags_layout = QVBoxLayout(flags_box)
        self.flag_no_dpdk = QCheckBox("--no-dpdk  (skip DPDK install entirely)")
        self.flag_skip_dpdk_build = QCheckBox(
            "--skip-dpdk-build  (apt deps only, no 10–30 min meson build)"
        )
        flags_layout.addWidget(self.flag_no_dpdk)
        flags_layout.addWidget(self.flag_skip_dpdk_build)
        form.addRow(flags_box)

        info = QLabel(
            "Copies the wheel + install_ostg_complete.py to <code>/tmp/netgen_install/</code> "
            "on the target host, then runs the installer (sudo'd if user != root). "
            "Full installs take 15–45 min; output streams here as it runs."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#475569; font-size:11px;")
        form.addRow(info)

        # Action row: Test connection + Install side by side. Test is a
        # 2-3 s probe that catches credential typos / wrong port / dead
        # host before kicking off a 15-min install. Pre-flight checks
        # (Python version, sudo, disk) also run as part of Install,
        # but Test lets the operator verify SSH alone first.
        action_row = QHBoxLayout()
        self.ssh_test_btn = QPushButton("Test Connection")
        self.ssh_test_btn.setToolTip(
            "Connect via SSH + run a few pre-flight checks (Python "
            "version, sudo capability, disk space). 2-3 seconds. "
            "Run this first to catch typos before the 15-min install."
        )
        self.ssh_test_btn.clicked.connect(self._start_ssh_test)
        self.ssh_btn = QPushButton("Install")
        self.ssh_btn.clicked.connect(self._start_ssh_install)
        action_row.addWidget(self.ssh_test_btn)
        action_row.addWidget(self.ssh_btn)
        action_row.addStretch(1)
        form.addRow("", action_row)

        return w

    def _update_auth_visibility(self) -> None:
        use_pw = self.auth_pw_rb.isChecked()
        self.ssh_password.setEnabled(use_pw)
        self.ssh_key.setEnabled(not use_pw)

    def _browse_file(self, line_edit: QLineEdit, filt: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select file", "", filt)
        if path:
            line_edit.setText(path)

    def _guess_installer_path(self) -> str:
        """Find install_ostg_complete.py relative to the running client."""
        here = os.path.dirname(os.path.abspath(__file__))
        for candidate in (
            os.path.join(here, "..", "install_ostg_complete.py"),
            "/opt/netgen/install_ostg_complete.py",
            "/opt/OSTG/install_ostg_complete.py",
        ):
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return ""

    def _start_ssh_install(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "An operation is already in progress.")
            return
        host = (self.ssh_host.text() or "").strip()
        user = (self.ssh_user.text() or "root").strip()
        wheel = (self.ssh_wheel.text() or "").strip()
        installer = (self.ssh_installer.text() or "").strip()
        if not host:
            QMessageBox.warning(self, "Missing", "Host is required.")
            return

        if self.auth_pw_rb.isChecked():
            password = self.ssh_password.text() or ""
            key_path = None
            if not password:
                QMessageBox.warning(self, "Missing", "Enter the SSH password.")
                return
        else:
            password = None
            key_path = (self.ssh_key.text() or "").strip()
            if not key_path or not os.path.isfile(key_path):
                QMessageBox.warning(self, "Missing", "Pick a valid SSH key file.")
                return

        port = int(self.ssh_port.value())

        # Probe for an existing detached install before bothering with
        # the wheel/installer validation. If one's running, offer to
        # reattach to its log — saves the operator from having to
        # cancel and recover.
        running_pid = self._probe_existing_install(host, user, password, key_path, port=port)
        resume_mode = False
        if running_pid:
            ret = QMessageBox.question(
                self, "Install already running",
                f"A previous install is still running on {host} "
                f"(pid {running_pid}).\n\n"
                f"Resume monitoring its log? (The install continues "
                f"either way — picking No just leaves it running in "
                f"the background.)",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return
            resume_mode = True

        # Wheel + installer paths only need validation when we're
        # actually going to upload them.
        if not resume_mode:
            if not wheel or not os.path.isfile(wheel):
                QMessageBox.warning(self, "Missing", "Pick a valid wheel file.")
                return
            if not installer or not os.path.isfile(installer):
                QMessageBox.warning(self, "Missing",
                                    "Pick install_ostg_complete.py from the repo checkout.")
                return

        flags = []
        if self.flag_no_dpdk.isChecked():
            flags.append("--no-dpdk")
        if self.flag_skip_dpdk_build.isChecked():
            flags.append("--skip-dpdk-build")

        self.log_view.clear()
        self._recent_errors.clear()
        self._log_carry = ""
        self._set_status("Resuming monitoring..." if resume_mode else "Connecting...")
        self.ssh_btn.setEnabled(False)

        self._worker = SshInstallWorker(
            host=host, user=user, port=port,
            password=password, key_path=key_path,
            wheel_path=wheel, installer_path=installer, extra_flags=flags,
            resume_mode=resume_mode,
        )
        self._worker.log_chunk.connect(self._append_log)
        self._worker.status.connect(self._set_status)
        self._worker.finished_ok.connect(self._ssh_install_finished)
        self._worker.start()

    def _start_ssh_test(self) -> None:
        """Click handler for the Test Connection button.

        Runs an SSH connect against the same host+user+auth+port the
        Install button would use, then runs four cheap pre-flight
        probes (Python version, sudo capability, free disk, target's
        own /api/health). Reports each as a ✓/✗ line in the log pane
        and a one-line summary in a QMessageBox. Total budget ~5 s.

        Doesn't kick off the install. Operators run this first to
        catch a wrong password / wrong port / dead host / Python 3.8
        target / non-root user without sudo — any of which would
        otherwise blow up 30 s to 15 min into an Install attempt.
        """
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy",
                                    "An operation is already in progress.")
            return
        host = (self.ssh_host.text() or "").strip()
        user = (self.ssh_user.text() or "root").strip()
        if not host:
            QMessageBox.warning(self, "Missing", "Host is required.")
            return
        port = int(self.ssh_port.value())

        if self.auth_pw_rb.isChecked():
            password = self.ssh_password.text() or ""
            key_path = None
            if not password:
                QMessageBox.warning(self, "Missing", "Enter the SSH password.")
                return
        else:
            password = None
            key_path = (self.ssh_key.text() or "").strip()
            if not key_path or not os.path.isfile(key_path):
                QMessageBox.warning(self, "Missing", "Pick a valid SSH key file.")
                return

        self.log_view.clear()
        self._recent_errors.clear()
        self._log_carry = ""
        self.ssh_test_btn.setEnabled(False)
        self._set_status(f"Testing {user}@{host}:{port}...")
        self._append_log(f"[test] connect to {user}@{host}:{port}\n")

        try:
            import paramiko
        except Exception as e:
            self._append_log(f"[test] paramiko import failed: {e}\n")
            self.ssh_test_btn.setEnabled(True)
            return

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        passed, failed = [], []
        try:
            kw = {"hostname": host, "username": user, "port": port, "timeout": 8}
            if key_path:
                kw["key_filename"] = key_path
            else:
                kw["password"] = password or ""
            try:
                client.connect(**kw)
            except Exception as e:
                self._append_log(f"[test] ✗ SSH connect failed: {e}\n")
                self._set_status("Test failed — see log")
                QMessageBox.warning(
                    self, "Test failed",
                    f"Couldn't reach {user}@{host}:{port}.\n\n"
                    f"{e}\n\n"
                    f"Check host, port, credentials, and that sshd is "
                    f"running on the target."
                )
                return
            self._append_log("[test] ✓ SSH connect OK\n")
            passed.append("ssh connect")

            # Probe 1: Python ≥ 3.9 (install_python_dependencies needs it)
            out, _ = self._test_remote(client, "python3 --version 2>&1")
            ok = False
            try:
                # "Python 3.10.12" → 3.10
                ver = out.strip().split()[-1]
                maj, minor = (int(x) for x in ver.split(".")[:2])
                ok = (maj, minor) >= (3, 9)
            except Exception:
                pass
            if ok:
                self._append_log(f"[test] ✓ Python: {out.strip()}\n")
                passed.append("python>=3.9")
            else:
                self._append_log(f"[test] ✗ Python (need ≥3.9): {out.strip() or '(no output)'}\n")
                failed.append("python<3.9 or missing")

            # Probe 2: sudo capability (skip for root)
            if user == "root":
                self._append_log("[test] ✓ Sudo: not needed (user is root)\n")
                passed.append("sudo (root)")
            else:
                # `sudo -n true` succeeds iff sudo is configured without
                # password prompt OR a cached credential exists.
                out, rc = self._test_remote(
                    client, "sudo -n true 2>&1 && echo OK || echo FAIL"
                )
                if "OK" in out:
                    self._append_log("[test] ✓ Sudo: passwordless / cached\n")
                    passed.append("sudo")
                else:
                    self._append_log(
                        f"[test] ✗ Sudo: user {user!r} can't sudo without prompt. "
                        "Install will block waiting for a password it can't see.\n"
                    )
                    failed.append("sudo without prompt")

            # Probe 3: free disk on /var (DPDK build needs ~4 GB)
            out, _ = self._test_remote(
                client,
                "df -BG --output=avail /var 2>/dev/null | tail -1 | tr -d 'G '"
            )
            try:
                free_gb = int(out.strip())
            except (ValueError, TypeError):
                free_gb = -1
            if free_gb >= 4:
                self._append_log(f"[test] ✓ Disk: {free_gb} GB free on /var\n")
                passed.append(f"disk={free_gb}G")
            elif free_gb < 0:
                self._append_log(f"[test] ? Disk: couldn't parse df output: {out!r}\n")
            else:
                self._append_log(
                    f"[test] ✗ Disk: only {free_gb} GB free on /var (need ≥4 GB "
                    "for DPDK build). Pass --skip-dpdk-build or --no-dpdk to fit.\n"
                )
                failed.append(f"disk={free_gb}G")

            # Probe 4: is netgen-server already running? (upgrade hint)
            out, _ = self._test_remote(
                client,
                "systemctl is-active netgen-server.service 2>/dev/null || echo missing"
            )
            state = out.strip() or "missing"
            if state == "active":
                self._append_log(
                    "[test] ℹ netgen-server already active on this host — "
                    "consider using the 'Upgrade running server' tab instead "
                    "for a 30 s upgrade (Tab 2 is a 15-min full install).\n"
                )
                passed.append(f"netgen-server={state}")
            else:
                self._append_log(f"[test] ✓ netgen-server: {state}\n")
                passed.append(f"netgen-server={state}")

        finally:
            try:
                client.close()
            except Exception:
                pass
            self.ssh_test_btn.setEnabled(True)

        if failed:
            self._set_status(f"Test: {len(passed)} ok, {len(failed)} failed")
            QMessageBox.warning(
                self, "Pre-flight checks failed",
                "Some pre-flight checks failed. Install will likely fail too:"
                "\n\n• " + "\n• ".join(failed) + "\n\nSee log for details."
            )
        else:
            self._set_status(f"Test: all {len(passed)} checks passed")
            QMessageBox.information(
                self, "Pre-flight OK",
                "All pre-flight checks passed:\n\n• " + "\n• ".join(passed)
                + "\n\nSafe to click Install."
            )

    def _test_remote(self, client, cmd: str, timeout: int = 6):
        """Run `cmd` over the client SSH connection. Returns (stdout, rc)."""
        try:
            _, stdout, _ = client.exec_command(cmd, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            rc = stdout.channel.recv_exit_status()
            return out, rc
        except Exception as e:
            return f"(exec failed: {e})", -1

    def _probe_existing_install(
        self, host: str, user: str, password: Optional[str], key_path: Optional[str],
        port: int = 22,
    ) -> Optional[str]:
        """One-shot SSH probe for /var/run/netgen-install.pid. Returns
        the pid string when a live install is running on `host`, else
        None. Blocks the UI for ~2 s — acceptable trade-off for
        avoiding a wrong-mode worker start.
        """
        try:
            import paramiko
        except Exception:
            return None
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            kw = {"hostname": host, "username": user, "port": int(port), "timeout": 5}
            if key_path:
                kw["key_filename"] = key_path
            else:
                kw["password"] = password or ""
            client.connect(**kw)
            _, stdout, _ = client.exec_command(
                "[ -f /var/run/netgen-install.pid ] && "
                "kill -0 $(cat /var/run/netgen-install.pid) 2>/dev/null && "
                "cat /var/run/netgen-install.pid || echo NONE",
                timeout=8,
            )
            out = (stdout.read().decode().strip() or "NONE")
            return None if out == "NONE" else out
        except Exception:
            return None
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _ssh_install_finished(self, ok: bool) -> None:
        self.ssh_btn.setEnabled(True)
        if ok:
            QMessageBox.information(
                self, "Install complete",
                "Server has been provisioned. Point your client at http://<host>:5050."
            )
        else:
            self._show_failure_dialog(
                "Install failed",
                "The fresh install did not complete. Most recent errors "
                "from the log:",
            )

    def _show_failure_dialog(self, title: str, lead: str) -> None:
        """Pop a QMessageBox with the last N captured error lines so the
        operator sees the actual cause without scrolling the log pane.
        Falls back to a generic "see log" message if no error lines were
        captured (rare — usually the worker emits at least one)."""
        if self._recent_errors:
            # Show up to the last 6 errors. The streaming order matches
            # chronological order, so the LAST captured line is usually
            # the proximate cause.
            errs = list(self._recent_errors)[-6:]
            bullet = "\n• " + "\n• ".join(e[:200] for e in errs)
            QMessageBox.critical(self, title, f"{lead}{bullet}")
        else:
            QMessageBox.critical(
                self, title,
                f"{lead}\n\n(No specific error lines captured — scroll the "
                f"log pane for details.)"
            )

    # -- Shared helpers ------------------------------------------------------

    def _append_log(self, text: str) -> None:
        """Append a streamed log chunk to the visible log pane.

        Does three things:
          1. Splits the chunk on newlines and carries any trailing
             partial line over for the next call (SSH chunks land on
             arbitrary byte boundaries).
          2. Colors each complete line by its category — red for
             ERROR, amber for WARNING, green for ✓/success, neutral
             otherwise. Uses appendHtml so a single line can carry
             color without re-formatting earlier lines.
          3. Captures error lines into self._recent_errors so the
             failure QMessageBox can show them without operators
             having to scroll.

        maxBlockCount cap on the QPlainTextEdit prevents scrollback
        explosion on multi-thousand-line DPDK builds.
        """
        if not text:
            return
        # Join leftover partial line from last call
        text = self._log_carry + text
        lines = text.split("\n")
        # Last piece is either "" (chunk ended on \n) or a partial line —
        # buffer it for next time.
        self._log_carry = lines.pop() if lines else ""

        for line in lines:
            self._classify_and_append(line)

        # If carry has grown unreasonably (no newline in a 10 KB blob),
        # flush it as-is to avoid pathological buffering.
        if len(self._log_carry) > 10_000:
            self._classify_and_append(self._log_carry)
            self._log_carry = ""

        self.log_view.ensureCursorVisible()

    def _classify_and_append(self, line: str) -> None:
        """Append a single complete line with category-based color."""
        # Classification order:
        #   1. Explicit bracket tag ([ERROR], [WARN]) — wins over
        #      anything else. install_ostg_complete.py adds these
        #      deliberately on lines that matter.
        #   2. Noise heuristic — strip out cleanup-style "X not found"
        #      messages that aren't actually errors.
        #   3. Generic error / warning / success word match.
        if not line:
            color = None
            self.log_view.appendPlainText("")
            return

        is_explicit_err = bool(_EXPLICIT_ERR_RX.search(line))
        is_explicit_warn = bool(_EXPLICIT_WARN_RX.search(line))
        is_noise = (not is_explicit_err) and (not is_explicit_warn) and bool(_NOISE_RX.search(line))
        is_error = is_explicit_err or ((not is_noise) and bool(_ERROR_RX.search(line)))
        is_warn  = (not is_error) and (is_explicit_warn or ((not is_noise) and bool(_WARN_RX.search(line))))
        is_ok    = (not is_error) and (not is_warn) and bool(_OK_RX.search(line))

        if is_error:
            color = "#dc2626"   # red-600
            self._recent_errors.append(line.strip())
        elif is_warn:
            color = "#d97706"   # amber-600
        elif is_ok:
            color = "#15803d"   # green-700
        else:
            color = None

        # Strip ANSI escape sequences from the server-side colored
        # output (install_dpdk.sh uses \033[0;32m style colors); we
        # color via HTML on our end so the raw escapes just produce
        # visual junk.
        line = _ANSI_RX.sub("", line)

        if color:
            self.log_view.appendHtml(
                f'<span style="color:{color};">{_html.escape(line) or "&nbsp;"}</span>'
            )
        else:
            self.log_view.appendPlainText(line)

    def _set_status(self, text: str) -> None:
        self.status_lbl.setText(text)

    # -- Log popout window ---------------------------------------------------

    def _toggle_log_popout(self) -> None:
        """Open (or focus, or close) the detached log window.

        Uses QTextDocument sharing — the popout's QPlainTextEdit calls
        setDocument(self.log_view.document()), so both widgets render
        the same underlying buffer. Any future appendHtml /
        appendPlainText / clear() on self.log_view also updates the
        popout. No mirroring code needed.

        Non-modal so the operator can keep clicking around the main
        dialog (Install / Test Connection / etc.) while watching the
        popout grow alongside.
        """
        # Already open → bring to front and focus instead of stacking
        # multiple popouts
        if self._log_popout is not None:
            try:
                if self._log_popout.isVisible():
                    self._log_popout.raise_()
                    self._log_popout.activateWindow()
                    return
            except RuntimeError:
                # Underlying Qt object was destroyed somewhere else
                self._log_popout = None

        popout = QDialog(self)
        popout.setWindowTitle("Install Log — Netgen Server")
        popout.setWindowFlags(popout.windowFlags() | Qt.Window)
        popout.resize(1100, 720)

        v = QVBoxLayout(popout)
        v.setContentsMargins(8, 8, 8, 8)

        # The actual mirror view — shares the document with the main
        # log_view, so anything written either side stays in sync.
        view = QPlainTextEdit(popout)
        view.setReadOnly(True)
        view.setDocument(self.log_view.document())
        view.setStyleSheet(
            "QPlainTextEdit{font-family: ui-monospace, Menlo, Consolas, "
            "monospace; font-size:12px; background:#0f172a; color:#e2e8f0;}"
        )
        # Cap the popout's history too — without this, setDocument
        # would inherit the embedded view's maxBlockCount (good) but
        # explicit set documents the contract.
        view.setMaximumBlockCount(self.log_view.maximumBlockCount())
        v.addWidget(view, 1)

        # Footer row: scroll-to-bottom toggle (default ON) + close
        footer = QHBoxLayout()
        self._popout_autoscroll = QCheckBox("Auto-scroll to bottom")
        self._popout_autoscroll.setChecked(True)
        self._popout_autoscroll.setToolTip(
            "Untick to freeze the view at the operator's current scroll "
            "position — useful for reading through a section while the "
            "install is still appending output."
        )
        # Wire scroll-keep behavior: whenever the document changes,
        # snap to bottom only if autoscroll is on.
        def _on_doc_changed():
            if self._popout_autoscroll.isChecked():
                cur = view.textCursor()
                cur.movePosition(cur.End)
                view.setTextCursor(cur)
                view.ensureCursorVisible()
        self.log_view.document().contentsChanged.connect(_on_doc_changed)
        # Initial scroll to bottom on open
        _on_doc_changed()

        footer.addWidget(self._popout_autoscroll)
        footer.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(popout.close)
        footer.addWidget(close_btn)
        v.addLayout(footer)

        # When the popout is closed (× button, Esc, our Close button),
        # reset the holder and the toggle button label
        def _on_popout_closed(_event):
            try:
                self.log_view.document().contentsChanged.disconnect(_on_doc_changed)
            except (TypeError, RuntimeError):
                # Already disconnected, or signal was never connected
                pass
            self._log_popout = None
            self.popout_btn.setText("Pop out ↗")
            self.popout_btn.setToolTip(
                "Open the log in a separate, freely-resizable window. "
                "The popout shares the same content — anything written "
                "to the log here appears there too."
            )
            QDialog.closeEvent(popout, _event)
        popout.closeEvent = _on_popout_closed

        self._log_popout = popout
        self.popout_btn.setText("Focus popout ↗")
        self.popout_btn.setToolTip("Bring the detached log window to the front.")
        popout.show()

    def closeEvent(self, e):
        if self._worker and self._worker.isRunning():
            # Differentiate the two worker types:
            #   • WheelUploadWorker (Tab 1): foreground HTTP request, no
            #     way to detach. Closing aborts the upload + restart wait.
            #   • SshInstallWorker (Tab 2): install runs detached via
            #     nohup on the target. Closing only stops monitoring;
            #     the install keeps running and can be re-attached by
            #     reopening this dialog.
            is_detached_ssh = isinstance(self._worker, SshInstallWorker)
            if is_detached_ssh:
                msg = (
                    "The fresh install is running detached on the target — "
                    "closing this dialog will stop monitoring the log, but "
                    "the install itself will continue to completion. "
                    "Re-open this dialog later to resume monitoring.\n\n"
                    "Close anyway?"
                )
            else:
                msg = (
                    "The upgrade is mid-flight (uploading wheel / waiting "
                    "for systemd restart). Closing now will abort it and "
                    "may leave the server in an inconsistent state.\n\n"
                    "Close anyway?"
                )
            ret = QMessageBox.question(
                self, "Operation in progress", msg,
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                e.ignore()
                return
            try:
                self._worker.stop()
                # Detached SSH installs get longer wait — the poll loop
                # may be mid-sleep when we ask it to stop.
                self._worker.wait(6000 if is_detached_ssh else 2000)
            except Exception:
                pass
        # Tear down the popout if it's still open. Without this, the
        # standalone log window would stick around as a top-level
        # widget after the parent dialog is gone — confusing and would
        # crash on the next setDocument call against a dead document.
        if self._log_popout is not None:
            try:
                self._log_popout.close()
            except (RuntimeError, AttributeError):
                pass
            self._log_popout = None
        super().closeEvent(e)
