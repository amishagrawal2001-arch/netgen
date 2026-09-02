"""v0.5.243 — Per-device lock around ensure_dhcp_services so DHCP
monitor auto-restart and API-driven restart/apply can't race.

Operator on srv06 2026-09-01 (post-v0.5.240): `tcpdump -i vlan30`
saw ZERO DHCPDISCOVER packets despite dhclient being "running".
Inspection: TWO dhclients on vlan30 (pid 3277082 + 3278117), both
sleeping, neither sending. Root cause: the DHCP monitor tick
raced with the API Restart both calling `ensure_dhcp_services` at
the same time. Each did stop → sweep → start independently — but
between one's sweep and its own start, the other one's start had
already spawned. Two dhclients ended up bound to the same
interface's raw AF_PACKET socket; bind() succeeded on both, but
only one could actually transmit — and even that transmission
frequently got starved.

With the strays killed manually and only ONE dhclient left, a
DHCPDISCOVER appeared on vlan30 within a second.

Fix: `ensure_dhcp_services` acquires a per-device `threading.Lock`
before running its stop → sweep → start. A second concurrent
caller blocks up to 45s for the first one to finish. On
lock-timeout, it gives up with a clear error instead of piling
on a second dhclient. The lock is per-device so unrelated
devices continue to run in parallel.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()


def test_threading_imported():
    assert "import threading" in DHCP


def test_per_device_ensure_locks_registry():
    """Registry + guard around the {device_id: Lock} map."""
    assert "_ENSURE_LOCKS: Dict[str, threading.Lock]" in DHCP
    assert "_ENSURE_LOCKS_GUARD = threading.Lock()" in DHCP


def test_get_ensure_lock_helper():
    """Helper that lazily creates the per-device lock, guarded
    by the registry's own lock so two calls for a new device_id
    don't race and create two different Lock objects."""
    idx = DHCP.find("def _get_ensure_lock(")
    assert idx > 0
    body = DHCP[idx:idx + 800]
    assert "with _ENSURE_LOCKS_GUARD:" in body
    assert "_ENSURE_LOCKS[device_id] = lock" in body


def test_ensure_dhcp_services_is_now_a_wrapper():
    """The public entry-point acquires the lock, then delegates."""
    idx = DHCP.find("def ensure_dhcp_services(")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 6000]
    assert "v0.5.243 (audit U ensure-race)" in body
    assert "_get_ensure_lock(device_id)" in body
    # Non-blocking first attempt so the log line explains what happened.
    assert "_lock.acquire(blocking=False)" in body
    # Bounded wait — 45s is a reasonable ceiling given start_dhcp_client's
    # default lease_timeout of 20s + stop overhead ~5s.
    assert "_lock.acquire(timeout=45)" in body
    # And a `finally: _lock.release()` — dropping the lock is critical,
    # a leaked lock would deadlock every subsequent call for that device.
    assert "_lock.release()" in body


def test_locked_body_extracted_to_helper():
    """The stop→sweep→start body lives in a separate helper so the
    lock wrapper doesn't have to reimplement every branch."""
    idx = DHCP.find("def _ensure_dhcp_services_locked(")
    assert idx > 0
    body = DHCP[idx:idx + 4000]
    # The mode-transition, container-creation, and start_dhcp_* calls
    # all still live in the locked body — verify by checking the v0.5.229
    # mode-transition marker is inside it.
    assert "v0.5.229 (audit U server-6)" in body


def test_wrapper_delegates_to_locked_helper():
    idx = DHCP.find("def ensure_dhcp_services(")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 6000]
    assert "_ensure_dhcp_services_locked(" in body


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 243)
