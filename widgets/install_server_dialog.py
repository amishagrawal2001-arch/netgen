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
    # Distinct signal for "HTTP endpoint is broken or unreachable" so the
    # dialog can offer the SSH fallback (uses pip directly via paramiko,
    # bypassing the busted endpoint). Carries a short reason string for
    # the operator-facing prompt. Emitted IN ADDITION to finished_ok(False).
    http_endpoint_broken = pyqtSignal(str)

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
            # Network-level failure → SSH fallback could still reach the
            # box if the operator has SSH creds. Emit the broken-endpoint
            # signal so the dialog knows to offer it.
            self.http_endpoint_broken.emit(f"network error: {e}")
            self.finished_ok.emit(False)
            return

        if r.status_code != 200:
            self.log_chunk.emit(
                f"[client] server rejected upload: {r.status_code} {r.text[:400]}\n"
            )
            # Which HTTP failures are SSH-recoverable?
            #   • 404 / 501 → the /api/admin/upgrade_wheel endpoint does
            #     NOT EXIST on this server (it predates the in-GUI upgrade
            #     feature). This is the classic "old server" case and is
            #     exactly what the SSH path (pip + systemctl restart) is
            #     for — so OFFER it.
            #   • 5xx → endpoint exists but errored (e.g. the latent
            #     v0.2.6-v0.2.10 NameError); SSH bypasses it.
            #   • other 4xx (400/409/413…) → genuine client/input error
            #     (bad filename, conflict, too large); SSH won't help, so
            #     don't offer it — the operator should fix the input.
            if r.status_code in (404, 501):
                self.http_endpoint_broken.emit(
                    f"server returned HTTP {r.status_code} — this server predates the "
                    f"in-GUI upgrade endpoint (/api/admin/upgrade_wheel). Falling back "
                    f"to SSH (pip install + service restart) to upgrade it."
                )
            elif r.status_code >= 500:
                self.http_endpoint_broken.emit(
                    f"server returned HTTP {r.status_code} — endpoint may have the "
                    f"latent v0.2.6-v0.2.10 NameError bug. Falling back to SSH "
                    f"would bypass it."
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


class SshUpgradeWorker(QThread):
    """Fallback path for the Upgrade tab when /api/admin/upgrade_wheel is
    broken (v0.2.6-v0.2.10 servers had a latent NameError there).

    Runs the Install Guide section 9a one-liner via paramiko:

      scp wheel.whl root@host:/tmp/
      ssh root@host 'pip3 install --upgrade --force-reinstall --no-deps wheel.whl
        && systemctl restart netgen-server
        && curl /api/health'

    Distinct from SshInstallWorker (which is for fresh installs and
    runs the full install_ostg_complete.py end-to-end). This worker
    just does the bare minimum to upgrade a running server: one
    sftp put, one pip install, one systemctl restart, one health
    probe. ~10 s round-trip vs the Fresh Install tab's 5-45 min.
    """

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
        port: int = 22,
        server_url: str = "",
    ):
        super().__init__()
        # `host` is the SSH target; `server_url` is the http URL the
        # client uses to probe /api/health post-restart. Usually
        # derived from host but the operator might be using a
        # bastion / port-forward, so we accept both.
        self.host = host
        self.user = user
        self.port = int(port) if port else 22
        self.password = password
        self.key_path = key_path
        self.wheel_path = wheel_path
        self.server_url = (server_url or f"http://{host}:5050").rstrip("/")
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            import paramiko
        except Exception as e:
            self.log_chunk.emit(f"[ssh-upgrade] paramiko import failed: {e}\n")
            self.finished_ok.emit(False)
            return

        self.status.emit(f"SSH connect to {self.user}@{self.host}:{self.port}...")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            kw = {
                "hostname": self.host, "username": self.user,
                "port": self.port, "timeout": 15,
            }
            if self.key_path:
                kw["key_filename"] = self.key_path
                self.log_chunk.emit(f"[ssh-upgrade] auth: key {self.key_path}\n")
            else:
                kw["password"] = self.password or ""
                self.log_chunk.emit("[ssh-upgrade] auth: password\n")
            client.connect(**kw)
        except Exception as e:
            self.log_chunk.emit(f"[ssh-upgrade] SSH connect failed: {e}\n")
            self.finished_ok.emit(False)
            return

        try:
            try:
                sftp = client.open_sftp()
            except Exception as e:
                self.log_chunk.emit(f"[ssh-upgrade] SFTP open failed: {e}\n")
                self.finished_ok.emit(False)
                return

            # Stage the wheel under /tmp (writeable by any user; pip
            # only needs read access to the file)
            wheel_name = os.path.basename(self.wheel_path)
            remote_wheel = f"/tmp/{wheel_name}"
            try:
                self.status.emit(f"Copying wheel to {remote_wheel}...")
                self.log_chunk.emit(
                    f"[ssh-upgrade] sftp put {self.wheel_path} → {remote_wheel} "
                    f"({os.path.getsize(self.wheel_path)} bytes)\n"
                )
                sftp.put(self.wheel_path, remote_wheel)
            except Exception as e:
                self.log_chunk.emit(f"[ssh-upgrade] SFTP put failed: {e}\n")
                self.finished_ok.emit(False)
                return
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass

            # Build the section-9a one-liner. sudo'd when user != root
            # so pip can write under the system Python's dist-packages.
            # Restart whichever service this host actually runs. New
            # installs use `netgen-server`; legacy/old servers (the exact
            # case this SSH path serves — they predate the HTTP upgrade
            # endpoint) often still run `ostg-server`. Try the new name
            # first, fall back to the legacy unit so the restart doesn't
            # fail on an old box.
            cmd_payload = (
                f"pip3 install --upgrade --force-reinstall --no-deps "
                f"{remote_wheel} && "
                f"(systemctl restart netgen-server 2>/dev/null || "
                f"systemctl restart ostg-server) && "
                f"sleep 2 && "
                # grep (not `head -2`): grep consumes pip's whole stdout so
                # pip never gets SIGPIPE. `head -2` closed the pipe early,
                # making pip print a harmless-but-alarming
                # "ERROR: Pipe to stdout was broken / BrokenPipeError" in
                # the upgrade log even on a fully successful upgrade.
                f"pip3 show ostg-trafficgen 2>/dev/null | "
                f"grep -E '^(Name|Version):' && "
                f"curl -fsS http://127.0.0.1:5050/api/health && echo"
            )
            cmd = (
                f"sudo sh -c {_shquote(cmd_payload)}"
                if self.user != "root" else cmd_payload
            )

            self.status.emit("Running pip install + systemctl restart...")
            self.log_chunk.emit(f"[ssh-upgrade] exec: {cmd_payload}\n")

            try:
                # PTY OK here — we WANT the install to die if the SSH
                # disconnects, since the round-trip is ~10s. The
                # tradeoff that drove SshInstallWorker to nohup
                # (15+ min installs surviving client exit) doesn't
                # apply to a 10-second upgrade.
                _, stdout, _ = client.exec_command(cmd, get_pty=True, timeout=180)
                stdout.channel.set_combine_stderr(True)
                for line in iter(stdout.readline, ""):
                    if self._stop:
                        self.log_chunk.emit("[ssh-upgrade] cancelled by user\n")
                        break
                    self.log_chunk.emit(line)
                rc = stdout.channel.recv_exit_status()
                self.log_chunk.emit(f"[ssh-upgrade] exit rc={rc}\n")
                if rc == 0:
                    self.status.emit("Upgrade complete — server back online")
                    self.finished_ok.emit(True)
                else:
                    self.status.emit(f"SSH upgrade failed (rc={rc})")
                    self.finished_ok.emit(False)
            except Exception as e:
                self.log_chunk.emit(f"[ssh-upgrade] exec failed: {e}\n")
                self.finished_ok.emit(False)
        finally:
            try:
                client.close()
            except Exception:
                pass


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

    def _sftp_put_tree(self, sftp, local_dir: str, remote_dir: str) -> None:
        """Recursively sftp-upload `local_dir` to `remote_dir`.

        Creates remote_dir + every subdirectory. Skips files starting
        with '.' (no .DS_Store, no .git/) and Python bytecode caches
        (no __pycache__ / *.pyc). Files are uploaded one at a time —
        paramiko's sftp.put doesn't have a recursive mode.

        Helper used by the Fresh Install upload pass to ship
        resources/dpdk/, ostg_docker/, etc. alongside the wheel +
        install_ostg_complete.py — without these the installer
        warned-and-skipped DPDK + FRR Docker setup.
        """
        try:
            sftp.mkdir(remote_dir)
        except IOError:
            pass  # exists
        for entry in sorted(os.listdir(local_dir)):
            if entry.startswith(".") or entry == "__pycache__":
                continue
            local_path = os.path.join(local_dir, entry)
            remote_path = f"{remote_dir}/{entry}"
            if os.path.isdir(local_path):
                self._sftp_put_tree(sftp, local_path, remote_path)
            elif os.path.isfile(local_path):
                if entry.endswith((".pyc", ".pyo")):
                    continue
                sftp.put(local_path, remote_path)
                # Preserve executable bit on .sh / .py scripts so the
                # installer can invoke them directly without an
                # explicit chmod step on each.
                if entry.endswith((".sh", ".py")):
                    try:
                        sftp.chmod(remote_path, 0o755)
                    except IOError:
                        pass

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

                # Upload supporting files that install_ostg_complete.py
                # expects to find alongside itself. Without these the
                # installer prints "WARNING: resources/dpdk/ not found"
                # and "ERROR: Dockerfile.frr: no such file or directory"
                # — install still succeeds but DPDK + FRR Docker image
                # never get deployed. Source paths resolved relative to
                # the installer's parent dir (same shape as the git
                # checkout layout).
                src_root = os.path.dirname(os.path.abspath(self.installer_path))
                # Top-level files: name → optional. Anything missing
                # logs a [warn] but doesn't abort — operator may not
                # need all features on a given install.
                top_files = [
                    "Dockerfile.frr",
                    "requirements.txt",
                ]
                # Recursive directory uploads: (local_dir, remote_dir)
                tree_uploads = [
                    (os.path.join(src_root, "resources", "dpdk"),
                     f"{remote_dir}/resources/dpdk"),
                    (os.path.join(src_root, "ostg_docker"),
                     f"{remote_dir}/ostg_docker"),
                ]

                for fname in top_files:
                    local = os.path.join(src_root, fname)
                    if not os.path.isfile(local):
                        self.log_chunk.emit(
                            f"[client] [warn] {fname} not found at {local} "
                            f"— some install steps will be skipped\n"
                        )
                        continue
                    remote = f"{remote_dir}/{fname}"
                    self.log_chunk.emit(f"[client] sftp put {local} → {remote}\n")
                    sftp.put(local, remote)

                for local_dir, rem_dir in tree_uploads:
                    if not os.path.isdir(local_dir):
                        self.log_chunk.emit(
                            f"[client] [warn] {local_dir} not found "
                            f"— skipping that subtree\n"
                        )
                        continue
                    self.log_chunk.emit(
                        f"[client] sftp put {local_dir}/ → {rem_dir}/ "
                        f"(recursive)\n"
                    )
                    self._sftp_put_tree(sftp, local_dir, rem_dir)
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
        # python3 -u: force unbuffered stdout/stderr. Critical for the
        # poll-loop UX:
        #
        # install_ostg_complete.py uses print(...) for its [INFO] /
        # [WARNING] / [ERROR] lines. When stdout is redirected to a
        # file (which the wrapper does via `>> /var/log/netgen-
        # install.log 2>&1`), Python detects "not a tty" and switches
        # from line-buffered to FULL-buffered (typically 4 KB / 8 KB
        # chunks). Result: print() output doesn't actually reach the
        # log file until the buffer fills OR Python exits.
        #
        # Meanwhile subprocess.run() output bypasses Python's stdout
        # buffer entirely — it writes directly to the inherited fd.
        # So `systemctl stop / docker rmi` errors appeared instantly
        # while [INFO] lines stayed invisible for 5-10 minutes until
        # the installer exited and flushed.
        #
        # Operators saw the dialog log apparently "stuck" at the last
        # subprocess error line and concluded the install had failed.
        # In reality it was progressing fine; they just couldn't see it.
        # `python3 -u` makes stdout unbuffered so each [INFO] print()
        # lands in /var/log/netgen-install.log immediately, and the
        # poll loop surfaces it within ~1-5 s.
        installer_invocation = (
            f"cd {remote_dir} && "
            f"python3 -u install_ostg_complete.py "
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

        # Server URL — editable combobox seeded from ~/.netgen/chassis_history.json
        # (the same store Add TGEN Chassis uses). Operators who've already
        # added their lab boxes there can pick from the dropdown instead
        # of retyping. Typing a new address still works — combobox is
        # editable. Each item carries the full chassis dict as user
        # data so we can react to selection with port-aware fill-in.
        self.up_server = QComboBox()
        self.up_server.setEditable(True)
        self.up_server.setInsertPolicy(QComboBox.NoInsert)
        self.up_server.lineEdit().setPlaceholderText("http://lab-box:5050")
        self._populate_chassis_dropdown(self.up_server, default_url, full_url=True)
        self.up_server.currentIndexChanged.connect(
            lambda i: self._on_chassis_picked(self.up_server, i, full_url=True)
        )
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

        # ──────────── SSH fallback (used only if HTTP fails) ────────────
        #
        # The /api/admin/upgrade_wheel endpoint had a latent NameError on
        # v0.2.6–v0.2.10 servers that returned a bare HTTP 500 — the
        # Upgrade tab couldn't fix the bug because the endpoint that
        # would do the fixing was the very thing that's broken. Operators
        # had to do one manual SSH upgrade before subsequent HTTP
        # upgrades would work.
        #
        # This section closes that gap: when the operator fills in SSH
        # credentials here, the dialog automatically falls back to a
        # paramiko-based pip-install on HTTP failure. The fallback runs
        # the exact section-9a one-liner: scp wheel + pip install +
        # systemctl restart + health check. ~10s round-trip.
        # The SSH-fallback group used to be checkable-but-always-visible.
        # That made the dialog too tall — the fields rendered (just
        # greyed out) even when the operator wasn't using them, and
        # the bottom row clipped the Upload button on smaller screens.
        # Now the group has a separate checkbox header; the form body
        # only mounts when checked, so the dialog stays compact in the
        # "I don't need SSH fallback" common case.
        ssh_box = QGroupBox()
        ssh_box.setStyleSheet("QGroupBox{border:1px solid #e2e8f0; border-radius:4px; margin-top:4px; padding-top:6px;}")
        ssh_outer = QVBoxLayout(ssh_box)
        ssh_outer.setContentsMargins(8, 4, 8, 4)
        ssh_outer.setSpacing(4)

        # Header row: checkbox + one-line description. Tightened from
        # the 3-sentence paragraph that was clipping.
        self.up_ssh_enable_cb = QCheckBox("Use SSH fallback if HTTP upgrade fails")
        self.up_ssh_enable_cb.setToolTip(
            "On HTTP 500 (e.g. v0.2.6-v0.2.10 NameError) or connect "
            "failure, sftp the wheel + run pip + systemctl restart via "
            "SSH. Install Guide §9a."
        )
        ssh_outer.addWidget(self.up_ssh_enable_cb)

        # Inner form — wrapped in a container widget we can show/hide
        # cleanly when the checkbox toggles. Hiding the container
        # reclaims its vertical space; just disabling children would
        # leave the dialog the same height.
        ssh_form_wrap = QWidget()
        ssh_layout = QFormLayout(ssh_form_wrap)
        ssh_layout.setContentsMargins(0, 4, 0, 0)
        ssh_layout.setSpacing(4)

        ssh_row = QHBoxLayout()
        self.up_ssh_user = QLineEdit("root")
        self.up_ssh_user.setMaximumWidth(120)
        self.up_ssh_port = QSpinBox()
        self.up_ssh_port.setRange(1, 65535)
        self.up_ssh_port.setValue(22)
        self.up_ssh_port.setMaximumWidth(80)
        ssh_row.addWidget(QLabel("user:"))
        ssh_row.addWidget(self.up_ssh_user)
        ssh_row.addWidget(QLabel("port:"))
        ssh_row.addWidget(self.up_ssh_port)
        ssh_row.addStretch(1)
        ssh_layout.addRow("SSH:", ssh_row)

        self.up_ssh_pw_rb = QRadioButton("Password")
        self.up_ssh_key_rb = QRadioButton("SSH key")
        self.up_ssh_pw_rb.setChecked(True)
        self._up_ssh_auth_grp = QButtonGroup(self)
        self._up_ssh_auth_grp.addButton(self.up_ssh_pw_rb)
        self._up_ssh_auth_grp.addButton(self.up_ssh_key_rb)
        auth_row = QHBoxLayout()
        auth_row.addWidget(self.up_ssh_pw_rb)
        auth_row.addWidget(self.up_ssh_key_rb)
        auth_row.addStretch(1)
        ssh_layout.addRow("Auth:", auth_row)

        # Wrap each auth row's label + field so we can toggle the
        # whole row's visibility. Without this the inactive row stays
        # rendered (just disabled) and operators saw the same overlap
        # in the Upgrade tab's SSH fallback as on the Fresh Install
        # tab — Password and Key file both visible at once.
        self.up_ssh_password = QLineEdit()
        self.up_ssh_password.setEchoMode(QLineEdit.Password)
        self.up_ssh_password.setPlaceholderText("SSH password (not stored)")
        self._up_pw_lbl = QLabel("Password:")
        ssh_layout.addRow(self._up_pw_lbl, self.up_ssh_password)

        self.up_ssh_key = QLineEdit()
        self.up_ssh_key.setPlaceholderText("~/.ssh/id_ed25519")
        key_browse = QPushButton("Browse...")
        key_browse.clicked.connect(lambda: self._browse_file(self.up_ssh_key, "SSH key files (*)"))
        up_key_wrap = QWidget()
        up_key_row = QHBoxLayout(up_key_wrap)
        up_key_row.setContentsMargins(0, 0, 0, 0)
        up_key_row.addWidget(self.up_ssh_key, 1)
        up_key_row.addWidget(key_browse)
        self._up_key_lbl = QLabel("Key file:")
        self._up_key_wrap = up_key_wrap
        ssh_layout.addRow(self._up_key_lbl, up_key_wrap)

        self.up_ssh_pw_rb.toggled.connect(self._update_up_auth_visibility)
        self._update_up_auth_visibility()

        ssh_outer.addWidget(ssh_form_wrap)
        ssh_form_wrap.setVisible(False)  # hidden by default

        # Toggle: showing/hiding the form wrap collapses the group
        # cleanly. The dialog's overall layout shrinks to match,
        # eliminating the overlap the operator reported.
        self.up_ssh_enable_cb.toggled.connect(ssh_form_wrap.setVisible)

        form.addRow(ssh_box)
        # Used downstream to decide whether to actually invoke the SSH
        # fallback worker on HTTP failure.
        self.up_ssh_box = self.up_ssh_enable_cb

        # Two ways to upgrade, side by side:
        #   • "Upload && Upgrade" — HTTP path (POST /api/admin/upgrade_wheel),
        #     auto-falls back to SSH on 404/5xx/network when the SSH box is on.
        #   • "Upgrade via SSH (manual)" — skips HTTP entirely and goes
        #     straight to pip-over-SSH. The right choice for OLD servers
        #     that don't have the HTTP endpoint at all (don't wait for a
        #     404 round-trip), or when you just prefer the direct path.
        btn_row = QHBoxLayout()
        self.up_btn = QPushButton("Upload && Upgrade")
        self.up_btn.setToolTip(
            "Upgrade via the server's HTTP endpoint (/api/admin/upgrade_wheel). "
            "Falls back to SSH automatically if that endpoint is missing/erroring "
            "and the SSH option above is enabled."
        )
        self.up_btn.clicked.connect(self._start_upgrade)
        self.up_ssh_manual_btn = QPushButton("Upgrade via SSH (manual)")
        self.up_ssh_manual_btn.setToolTip(
            "Skip the HTTP endpoint and upgrade directly over SSH: sftp the wheel, "
            "pip install --upgrade --force-reinstall --no-deps, then restart the "
            "service. Use this for OLD servers that don't have the "
            "/api/admin/upgrade_wheel endpoint. Fill in the SSH credentials above."
        )
        self.up_ssh_manual_btn.clicked.connect(self._start_ssh_upgrade_manual)
        btn_row.addWidget(self.up_btn)
        btn_row.addWidget(self.up_ssh_manual_btn)
        form.addRow("", self._wrap_row(btn_row))

        return w

    @staticmethod
    def _wrap_row(layout) -> QWidget:
        """Wrap a QLayout in a QWidget so it can be added via QFormLayout.addRow."""
        holder = QWidget()
        holder.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return holder

    def _update_up_auth_visibility(self) -> None:
        # Hide rather than disable: setEnabled left both rows visible
        # (just greyed). Toggling visibility of label+field together
        # makes QFormLayout reclaim the row, matching what
        # _update_auth_visibility does on the Fresh Install tab.
        use_pw = self.up_ssh_pw_rb.isChecked()
        self.up_ssh_password.setVisible(use_pw)
        self._up_pw_lbl.setVisible(use_pw)
        self._up_key_wrap.setVisible(not use_pw)
        self._up_key_lbl.setVisible(not use_pw)

    def _populate_chassis_dropdown(
        self, combo: QComboBox, default_text: str, *, full_url: bool
    ) -> None:
        """Seed an editable QComboBox with entries from chassis_history.json.

        Used by both the Upgrade tab's Server URL field (full_url=True,
        items show "http://host:port — label") and the Fresh Install
        tab's Host field (full_url=False, items show "host — label").
        Each item carries the full chassis dict as user data so
        _on_chassis_picked can fill the form fields with port etc.

        Adds a blank "(type a new address, or pick from history below)"
        sentinel as index 0 so the dropdown opens with the placeholder
        showing instead of auto-selecting the first chassis. default_text
        wins over the sentinel when provided (operator already has a
        connection — pre-fill it).
        """
        try:
            from widgets.add_tgen_dialog import _load_history
            entries = _load_history()
        except Exception:
            entries = []

        # Sentinel — empty text so the placeholder shows through
        combo.addItem("", None)

        for e in entries:
            address = e.get("address", "")
            if not address:
                continue
            port = int(e.get("port", 5050))
            scheme = e.get("scheme", "http")
            label = e.get("label", "") or ""
            if full_url:
                display = f"{scheme}://{address}:{port}"
            else:
                display = address
            if label:
                display = f"{display}  —  {label}"
            combo.addItem(display, e)

        if default_text:
            # Try to match an existing item; if not found, just type it
            # in (combobox stays editable so this works).
            idx = combo.findText(default_text)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(default_text)
        else:
            combo.setCurrentIndex(0)
            combo.setEditText("")  # show placeholder

    def _on_chassis_picked(
        self, combo: QComboBox, index: int, *, full_url: bool
    ) -> None:
        """Handler for chassis-dropdown selection.

        Index 0 is the sentinel — ignore. For real picks, extract the
        chassis dict via itemData and fill in:
          • full_url=True  (Upgrade tab): leave the combobox showing
            the scheme://host:port URL it already has (no extra work
            — addItem text IS the URL).
          • full_url=False (Fresh Install tab): strip back to just the
            hostname (combobox display might include " — label"), and
            bump the port spinbox to the chassis's stored port.
        """
        if index <= 0:
            return
        entry = combo.itemData(index)
        if not isinstance(entry, dict):
            return
        address = entry.get("address", "")
        port = int(entry.get("port", 5050))
        if full_url:
            scheme = entry.get("scheme", "http")
            combo.setEditText(f"{scheme}://{address}:{port}")
        else:
            combo.setEditText(address)
            # Bump the port spinbox if we have one on this tab
            if hasattr(self, "ssh_port"):
                self.ssh_port.setValue(port)

    def _browse_wheel(self, line_edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select wheel file", "", "Python wheels (*.whl);;All files (*)"
        )
        if path:
            line_edit.setText(path)

    def _set_upgrade_busy(self, busy: bool) -> None:
        """Enable/disable BOTH upgrade buttons together so a run can't be
        double-started from the other button."""
        self.up_btn.setEnabled(not busy)
        if hasattr(self, "up_ssh_manual_btn"):
            self.up_ssh_manual_btn.setEnabled(not busy)

    def _start_ssh_upgrade_manual(self) -> None:
        """Direct SSH upgrade — skip the HTTP endpoint entirely.

        For OLD servers that lack /api/admin/upgrade_wheel (no 404
        round-trip needed), or whenever the operator prefers the direct
        pip-over-SSH path. Reuses the SSH credentials entered on this tab.
        """
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "An operation is already in progress.")
            return
        url = (self.up_server.currentText() or "").strip()
        wheel = (self.up_wheel.text() or "").strip()
        token = (self.up_token.text() or "").strip()
        if not url:
            QMessageBox.warning(self, "Missing", "Server URL is required (used to derive the SSH host).")
            return
        if not wheel or not os.path.isfile(wheel):
            QMessageBox.warning(self, "Missing", "Pick a valid wheel file.")
            return

        self.log_view.clear()
        self._recent_errors.clear()
        self._log_carry = ""
        self._set_status("Starting manual SSH upgrade...")
        self._set_upgrade_busy(True)
        self._http_broken = False
        self._http_broken_reason = ""
        self._pending_upgrade = {"url": url, "wheel": wheel, "token": token}
        self._try_ssh_fallback(manual=True)

    def _start_upgrade(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "An operation is already in progress.")
            return
        url = (self.up_server.currentText() or "").strip()
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
        self._set_upgrade_busy(True)
        # Track whether HTTP failed in a recoverable way (5xx / network),
        # so finished_ok handler knows whether to trigger SSH fallback
        # vs just show the error dialog.
        self._http_broken = False
        self._http_broken_reason = ""
        # Stash inputs for the fallback path (creating a new worker
        # later needs them, can't re-read the QLineEdits from inside a
        # connected slot reliably if the user is editing them)
        self._pending_upgrade = {
            "url": url,
            "wheel": wheel,
            "token": token,
        }

        self._worker = WheelUploadWorker(url, wheel, token)
        self._worker.log_chunk.connect(self._append_log)
        self._worker.status.connect(self._set_status)
        self._worker.http_endpoint_broken.connect(self._on_http_endpoint_broken)
        self._worker.finished_ok.connect(self._upgrade_finished)
        self._worker.start()

    def _on_http_endpoint_broken(self, reason: str) -> None:
        """WheelUploadWorker says the HTTP endpoint can't be used (5xx
        or network error). Flag for the finish handler to trigger the
        SSH fallback (or show a tailored prompt if SSH creds weren't
        provided)."""
        self._http_broken = True
        self._http_broken_reason = reason

    def _upgrade_finished(self, ok: bool) -> None:
        if ok:
            # Clean success — clear fallback state and report.
            self._http_broken = False
            self._http_broken_reason = ""
            self._set_upgrade_busy(False)
            QMessageBox.information(
                self, "Upgrade complete",
                "Server has restarted with the new wheel."
            )
            return

        # Failure path. Try SSH fallback if:
        #   (1) the failure was an HTTP-recoverable one (5xx / network), AND
        #   (2) the operator enabled the SSH fallback group with creds
        if self._http_broken and getattr(self, "up_ssh_box", None) and self.up_ssh_box.isChecked():
            self._try_ssh_fallback()
            return

        # No fallback available — show the captured errors.
        self._set_upgrade_busy(False)
        if self._http_broken:
            self._show_failure_dialog(
                "Upgrade failed (HTTP endpoint broken)",
                f"The HTTP upgrade endpoint failed: {self._http_broken_reason}\n\n"
                f"Enable 'SSH fallback' in the Upgrade tab and provide "
                f"SSH credentials — the dialog will then automatically "
                f"fall back to a paramiko-based pip install (Install "
                f"Guide §9a) when this happens. Most recent errors:",
            )
        else:
            self._show_failure_dialog(
                "Upgrade failed",
                "The wheel upgrade did not complete. Most recent errors "
                "from the log:",
            )

    def _try_ssh_fallback(self, manual: bool = False) -> None:
        """Kick off SshUpgradeWorker against the server the upgrade tab
        was targeting. Reuses host from the http URL, creds from the
        SSH fallback group.

        `manual=True` when invoked directly via the "Upgrade via SSH
        (manual)" button (no preceding HTTP attempt) — only changes the
        log wording so it doesn't claim "HTTP endpoint failed".
        """
        url = self._pending_upgrade.get("url", "")
        wheel = self._pending_upgrade.get("wheel", "")

        # Parse hostname out of the URL (strip scheme + port)
        host_from_url = re.sub(r"^https?://", "", url, flags=re.I)
        host_from_url = host_from_url.split("/")[0]
        # Drop :port suffix from hostname (paramiko uses port=)
        if ":" in host_from_url:
            host_from_url = host_from_url.rsplit(":", 1)[0]

        user = (self.up_ssh_user.text() or "root").strip()
        port = int(self.up_ssh_port.value())
        if self.up_ssh_pw_rb.isChecked():
            password = self.up_ssh_password.text() or ""
            key_path = None
            if not password:
                self._set_upgrade_busy(False)
                QMessageBox.warning(
                    self, "SSH fallback: missing password",
                    "The HTTP endpoint failed and SSH fallback was "
                    "enabled, but no password was provided. Either "
                    "fill in the password / pick a key, or disable "
                    "the fallback to see the original error."
                )
                return
        else:
            password = None
            key_path = (self.up_ssh_key.text() or "").strip()
            if not key_path or not os.path.isfile(key_path):
                self._set_upgrade_busy(False)
                QMessageBox.warning(
                    self, "SSH fallback: missing key",
                    "Pick a valid SSH key file or switch to password auth."
                )
                return

        if manual:
            self._append_log(
                "\n[client] Manual SSH upgrade (skipping the HTTP endpoint)...\n"
                "[client] sftp wheel → pip install --upgrade --force-reinstall "
                "--no-deps → restart service (Install Guide §9a)...\n"
            )
        else:
            self._append_log(
                f"\n[client] HTTP endpoint failed: {self._http_broken_reason}\n"
                f"[client] Falling back to SSH-based pip install + restart "
                f"(Install Guide §9a)...\n"
            )
        self._set_status(f"SSH fallback: connecting to {user}@{host_from_url}:{port}...")
        # Reset for the new worker
        self._http_broken = False
        self._http_broken_reason = ""

        self._worker = SshUpgradeWorker(
            host=host_from_url, user=user, port=port,
            password=password, key_path=key_path,
            wheel_path=wheel, server_url=url,
        )
        self._worker.log_chunk.connect(self._append_log)
        self._worker.status.connect(self._set_status)
        self._worker.finished_ok.connect(self._upgrade_finished)
        self._worker.start()

    # -- Tab 2: SSH fresh install -------------------------------------------

    def _build_fresh_install_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        host_row = QHBoxLayout()
        # Host — editable combobox seeded from chassis_history.json.
        # Same story as the Upgrade tab's Server URL field, but here we
        # pull just the hostname (no scheme, no port — those live in
        # separate fields on this tab). Picking a row also bumps the
        # port spinbox to whatever was stored with the chassis entry.
        self.ssh_host = QComboBox()
        self.ssh_host.setEditable(True)
        self.ssh_host.setInsertPolicy(QComboBox.NoInsert)
        self.ssh_host.lineEdit().setPlaceholderText("lab-box.example.com")
        self._populate_chassis_dropdown(self.ssh_host, "", full_url=False)
        self.ssh_host.currentIndexChanged.connect(
            lambda i: self._on_chassis_picked(self.ssh_host, i, full_url=False)
        )
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

        # Password + Key file rows wrapped in container widgets so we
        # can hide the inactive one entirely (reclaims vertical space).
        # Previously _update_auth_visibility only setEnabled() which
        # left both rows visible / greyed — wasted ~30px and added
        # to the overlap operators reported.
        self.ssh_password = QLineEdit()
        self.ssh_password.setEchoMode(QLineEdit.Password)
        self.ssh_password.setPlaceholderText("SSH password (not stored)")
        self._pw_row_lbl = QLabel("Password:")
        form.addRow(self._pw_row_lbl, self.ssh_password)

        self.ssh_key = QLineEdit()
        self.ssh_key.setPlaceholderText("~/.ssh/id_ed25519")
        key_browse = QPushButton("Browse...")
        key_browse.clicked.connect(lambda: self._browse_file(self.ssh_key, "SSH key files (*)"))
        key_row_wrap = QWidget()
        key_row = QHBoxLayout(key_row_wrap)
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.ssh_key, 1)
        key_row.addWidget(key_browse)
        self._key_row_lbl = QLabel("Key file:")
        self._key_row_wrap = key_row_wrap
        form.addRow(self._key_row_lbl, key_row_wrap)

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

        # Flags — tightened from the default QGroupBox spacing which
        # made the two checkboxes consume ~80px and overflow onto the
        # info paragraph below. Now ~40px total.
        flags_box = QGroupBox("install_ostg_complete.py flags (optional)")
        flags_box.setStyleSheet(
            "QGroupBox{margin-top:6px; padding-top:6px;}"
            "QGroupBox::title{subcontrol-origin:margin; left:8px;}"
        )
        flags_layout = QVBoxLayout(flags_box)
        flags_layout.setContentsMargins(8, 4, 8, 4)
        flags_layout.setSpacing(2)
        self.flag_no_dpdk = QCheckBox("--no-dpdk  (skip DPDK install entirely)")
        self.flag_skip_dpdk_build = QCheckBox(
            "--skip-dpdk-build  (apt deps only, no 10–30 min meson build)"
        )
        flags_layout.addWidget(self.flag_no_dpdk)
        flags_layout.addWidget(self.flag_skip_dpdk_build)
        form.addRow(flags_box)

        # Info — one short line instead of the 3-sentence paragraph
        # that pushed the action buttons off-screen at the bottom.
        info = QLabel(
            "Installs to <code>/tmp/netgen_install/</code> then runs the "
            "installer. Full installs take 15–45 min; log streams below."
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
        # Hide rather than disable: setEnabled left both rows visible
        # (just greyed), and on the Fresh Install tab that contributed
        # to the overlap operators reported. Toggling the wrappers'
        # visibility makes QFormLayout reclaim the row's vertical
        # space instead of leaving a greyed-out gap.
        use_pw = self.auth_pw_rb.isChecked()
        self.ssh_password.setVisible(use_pw)
        self._pw_row_lbl.setVisible(use_pw)
        self._key_row_wrap.setVisible(not use_pw)
        self._key_row_lbl.setVisible(not use_pw)

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
        host = (self.ssh_host.currentText() or "").strip()
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
        host = (self.ssh_host.currentText() or "").strip()
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
