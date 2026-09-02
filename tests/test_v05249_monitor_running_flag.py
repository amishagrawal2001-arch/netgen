"""v0.5.249 — BGP + OSPF monitor managers expose is_running; details
dialog shows "N s ago" when no ISO timestamp is available.

Operator on 2026-09-02 opened the new monitor-details dialog and saw:

    ARP        ✓ OK          last tick: —
    BGP        ✗ DOWN        last tick: —
    DHCP       ✓ OK          last tick: —
    ISIS       ✓ OK          last tick: —
    OSPF       ✗ DOWN        last tick: —

Two bugs:

1. **BGP and OSPF wrongly reported DOWN** even though their DB
   heartbeats were 5s fresh. Root cause: the `/api/monitors/health`
   endpoint reads `getattr(bgp_monitor, "is_running", False)` — but
   `bgp_monitor` is a `BGPStatusManager` wrapper that lacks its
   own `is_running` attribute. The default False shipped forever.
   The inner `BGPStatusMonitor` (`bgp_monitor.monitor`) has the real
   flag, but the wrapper never surfaced it. Same for OSPF's
   `OSPFStatusManager`. ISIS already had a matching `is_running`
   property (`utils/isis_monitor.py:332`) added for the same reason.

2. **"last tick: —"** for every row. The endpoint returns
   `stale_secs` (age of the freshest DB heartbeat), never an ISO
   timestamp — but the client rendered `info.get("last_tick") or
   "—"`, showing "—" every time.

Fixes:

- **utils/bgp_monitor.py `BGPStatusManager`** — new `is_running`
  `@property` that returns `bool(self.monitor and self.monitor.is_running)`.
- **utils/ospf_monitor.py `OSPFStatusManager`** — same.
- **widgets/devices_tab.py `_show_monitor_health_details`** — when
  `last_tick` / `last_update` / `last_check_at` fields are all
  absent, derive `"{int(stale_secs)} s ago"` from the endpoint's
  actual returned field. Falls back to `"no heartbeat yet"` when
  no data at all.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
BGP = (REPO / "utils" / "bgp_monitor.py").read_text()
OSPF = (REPO / "utils" / "ospf_monitor.py").read_text()
TAB = (REPO / "widgets" / "devices_tab.py").read_text()


# --- Server: BGPStatusManager + OSPFStatusManager surface is_running


def test_bgp_manager_exposes_is_running_property():
    """`bgp_monitor.is_running` (where bgp_monitor is
    BGPStatusManager) must delegate to the inner monitor's flag,
    otherwise the health endpoint sees the default False."""
    idx = BGP.find("class BGPStatusManager:")
    assert idx > 0
    body = BGP[idx:idx + 3000]
    assert "v0.5.249 (audit U monitor-running-flag)" in body
    assert "@property" in body
    assert "def is_running(self) -> bool:" in body
    assert 'return bool(self.monitor and getattr(self.monitor, "is_running", False))' in body


def test_ospf_manager_exposes_is_running_property():
    idx = OSPF.find("class OSPFStatusManager:")
    assert idx > 0
    body = OSPF[idx:idx + 3000]
    assert "v0.5.249 (audit U monitor-running-flag)" in body
    assert "@property" in body
    assert "def is_running(self) -> bool:" in body
    assert 'return bool(self.monitor and getattr(self.monitor, "is_running", False))' in body


# --- Client: dialog shows "N s ago" fallback ------------------------


def test_dialog_derives_last_display_from_stale_secs():
    """When last_tick isn't in the payload but stale_secs is,
    render 'N s ago' instead of '—'."""
    idx = TAB.find("def _show_monitor_health_details(self)")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 6000]
    assert "v0.5.249" in body
    assert '_last_display = f"{int(_stale_secs)} s ago"' in body
    assert '_last_display = "no heartbeat yet"' in body


def test_dialog_prefers_iso_timestamp_when_available():
    """If the server one day starts emitting last_tick / last_check_at,
    the dialog uses that verbatim rather than the derived age."""
    idx = TAB.find("def _show_monitor_health_details(self)")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 6000]
    assert '_info.get("last_tick") or _info.get("last_update") or _info.get("last_check_at")' in body
    assert "if _last_tick:" in body
    assert "_last_display = _last_tick" in body


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 249)
