"""v0.5.240 — DHCP buttons actually work: Restart button no longer
times out, dhclient processes no longer leak.

Operator on srv06 2026-08-31 reported:
- Restart DHCP button: "Read timed out. (read timeout=10)" — clicked,
  server never seemed to respond, button felt dead.
- No periodic DHCP request packets seen on tcpdump for stuck clients.
- Three (3!) concurrent dhclient processes on the same vlan30
  interface, all in "Requesting" backoff, none getting a lease.

Root causes:

1. **dhclient process leak on Restart / Apply**: `dhclient -r -pf
   <pidfile>` only reads ONE pidfile and only kills that PID.
   Stale pidfile / mismatched pidfile name / orphaned fork paths
   leaked the old dhclient. After a few Restart cycles the operator
   ended up with N concurrent dhclients fighting each other for the
   same interface — none could hold a lease because they were
   RELEASING each other's.

2. **Client-side 10s timeout on Restart button**: server-side cycle
   is stop old daemon (≤5s) + sweep strays (~1s) + start dhclient
   + wait `lease_timeout` (default 20s) for the lease = up to ~25s.
   10s wasn't enough; operator saw a timeout on every click even
   though the server-side restart WAS in progress.

Fixes:
- `_kill_stale_dhclients(interface, container)` — new helper. Uses
  the same argv-token match as `_is_dhclient_running` (v0.5.218
  fix M) so `eth1` doesn't match `eth10`. TERMs then KILLs any
  survivor.
- Wired into `stop_dhcp_client` (after `-r` release, before flush)
  AND `start_dhcp_client` (before spawning fresh dhclient).
- `restart_dhcp_service` client-side timeout: 10s → 60s, plus a
  modal QProgressDialog so the operator sees "restarting…" instead
  of a frozen UI.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()
UI = (REPO / "utils" / "devices_tab_dhcp.py").read_text()


# --- dhclient-leak fix -----------------------------------------------


def test_kill_stale_dhclients_helper_exists():
    assert "def _kill_stale_dhclients(" in DHCP
    idx = DHCP.find("def _kill_stale_dhclients(")
    body = DHCP[idx:idx + 4000]
    # Uses `pgrep -a -f dhclient` and parses argv tokens (same
    # anti-collision pattern as _is_dhclient_running from v0.5.218
    # fix M — matches `eth1` as a whole token, not `eth10`).
    assert '"pgrep", "-a", "-f", "dhclient"' in body
    assert "if interface in argv:" in body


def test_kill_stale_dhclients_kills_all_matches_not_pidfile_only():
    """Regression guard: the whole point is to escape single-PID
    thinking. The helper builds a LIST of PIDs and iterates."""
    idx = DHCP.find("def _kill_stale_dhclients(")
    body = DHCP[idx:idx + 4000]
    assert "_pids: List[str] = []" in body
    assert "_pids.append(parts[0])" in body
    assert "for _pid in _pids:" in body


def test_kill_stale_dhclients_sigkill_survivors():
    """After TERM, sweep once more with -9 to guarantee cleanup —
    dhclient normally exits on SIGTERM in <100ms; a survivor means
    it refused TERM."""
    idx = DHCP.find("def _kill_stale_dhclients(")
    body = DHCP[idx:idx + 4000]
    assert '"kill", "-9"' in body


def test_stop_dhcp_client_sweeps_stray_dhclients():
    """stop_dhcp_client must call the sweep helper AFTER the
    per-pidfile release attempt — the sweep catches whatever `-r`
    missed. Placement matters: before _flush_ipv4 so a stray
    dhclient can't re-populate the interface address after flush."""
    idx = DHCP.find("def stop_dhcp_client(")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 8000]
    assert "v0.5.240 (audit U client-restart)" in body
    assert "_kill_stale_dhclients(interface" in body
    # Sweep must run BEFORE the address flush so a stray dhclient
    # can't re-apply an address after we flushed it.
    sweep_pos = body.find("_kill_stale_dhclients(interface")
    flush_pos = body.find("_flush_ipv4(interface")
    assert 0 < sweep_pos < flush_pos, \
        "sweep must run before _flush_ipv4 so strays can't re-apply after flush"


def test_start_dhcp_client_sweeps_before_spawn():
    """start_dhcp_client's IPv4 path must sweep strays BEFORE
    spawning the new dhclient — otherwise the new process races
    the leftover ones from the last Apply/Restart."""
    idx = DHCP.find("def start_dhcp_client(")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 12000]
    assert "v0.5.240 (audit U client-restart)" in body
    assert "_kill_stale_dhclients(interface" in body
    # Sweep must be BEFORE `cmd_v4 = [...]` construction.
    sweep_pos = body.find("_kill_stale_dhclients(interface")
    spawn_pos = body.find('cmd_v4 = ["dhclient", "-4"')
    assert 0 < sweep_pos < spawn_pos, \
        "sweep must run before spawning the new dhclient"


# --- Restart button timeout + UX fix ---------------------------------


def test_restart_button_client_timeout_bumped_to_60s():
    """v0.5.231's 10s timeout wasn't enough for the server-side
    stop+sweep+start+lease-wait cycle. Must be ≥60s so the operator
    doesn't see a spurious timeout while the server is still working."""
    idx = UI.find("def restart_dhcp_service(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 6000]
    # 10s timeout is gone.
    assert "timeout=10," not in body
    # 60s timeout is present.
    assert "timeout=60," in body
    # Rationale comment marker.
    assert "v0.5.240 (audit U client-restart)" in body


def test_restart_button_shows_progress_dialog():
    """Modal QProgressDialog is required so the UI doesn't feel
    frozen for 25s while the server-side restart runs."""
    idx = UI.find("def restart_dhcp_service(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 6000]
    assert "_prog = QProgressDialog(" in body
    assert 'setWindowTitle("Restart DHCP")' in body
    # Modal so user can't fire ANOTHER restart mid-flight.
    assert "setWindowModality(Qt.WindowModal)" in body
    # QProgressDialog / QApplication must be imported at module top.
    assert "QProgressDialog," in UI
    assert "QApplication," in UI


def test_restart_button_closes_progress_on_all_paths():
    """Progress dialog must close whether the request succeeded,
    failed at the HTTP layer, or raised an exception."""
    idx = UI.find("def restart_dhcp_service(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 6000]
    # `finally: _prog.close()` guarantees cleanup on all try-exit paths.
    assert "finally:" in body
    assert "_prog.close()" in body


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 240)
