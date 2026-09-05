"""v0.5.271 — L2 emulation scale (N-instance) support.

Two flavors of check:
  * unit-level tests of the per-protocol increment functions
    (`_scale_kwargs_*` + `_increment_last_octet`) — pure, no
    scapy/PyQt runtime.
  * source-level checks that the API + client dialog wire the
    `count` field end-to-end.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
L2 = (REPO / "utils" / "l2_protocols.py").read_text()
DLG = (REPO / "widgets" / "l2_emulation_tab.py").read_text()
SRV = (REPO / "server" / "l2_routes.py").read_text()

from utils.l2_protocols import (  # noqa: E402  (module-under-test)
    MAX_SCALE_COUNT,
    _increment_last_octet,
    _scale_kwargs_bfd,
    _scale_kwargs_igmp,
    _scale_kwargs_lacp,
    _scale_kwargs_lldp,
    _scale_kwargs_pim,
    _scale_kwargs_vrrp,
    _SCALE_INCREMENTERS,
)


# --- _increment_last_octet ---------------------------------------


def test_increment_last_octet_ipv4():
    assert _increment_last_octet("239.1.1.1", 0) == "239.1.1.1"
    assert _increment_last_octet("239.1.1.1", 4) == "239.1.1.5"
    assert _increment_last_octet("192.168.1.100", 3) == "192.168.1.103"


def test_increment_last_octet_ipv4_wraps_at_255():
    # 253 + 5 = 258 → wraps to 2 (258 & 0xff).
    assert _increment_last_octet("10.0.0.253", 5) == "10.0.0.2"


def test_increment_last_octet_mac():
    assert _increment_last_octet("00:11:22:33:44:01", 2) == "00:11:22:33:44:03"
    # Wraps at 0xff.
    assert _increment_last_octet("aa:bb:cc:dd:ee:fd", 5) == "aa:bb:cc:dd:ee:02"


def test_increment_last_octet_leaves_bad_input_unchanged():
    # Unparseable — leave the input alone rather than blow up.
    assert _increment_last_octet("not-an-address", 3) == "not-an-address"
    assert _increment_last_octet("", 3) == ""
    assert _increment_last_octet(None, 3) is None  # type: ignore[arg-type]


# --- Per-protocol incrementers (n=0 is base, n>0 increments) -----


def test_lldp_scale_appends_suffix_and_increments_mac():
    base = {"chassis_id": "netgen-host", "port_id": "eth0",
            "system_name": "netgen", "src_mac": "00:11:22:33:44:02"}
    assert _scale_kwargs_lldp(base, 0, "ens1f0") == base
    kw2 = _scale_kwargs_lldp(base, 2, "ens1f0")
    assert kw2["chassis_id"] == "netgen-host-3"   # n=2 → suffix "-3"
    assert kw2["port_id"] == "eth0-3"
    assert kw2["system_name"] == "netgen-3"
    assert kw2["src_mac"] == "00:11:22:33:44:04"  # +2


def test_lldp_scale_leaves_blank_defaults_blank():
    """Blank fields stay blank so the server keeps auto-deriving
    from the iface for every instance (rather than "-N" onto "")."""
    base = {"chassis_id": "", "port_id": "", "system_name": "",
            "src_mac": ""}
    kw = _scale_kwargs_lldp(base, 5, "ens1f0")
    assert kw["chassis_id"] == "" and kw["port_id"] == ""
    assert kw["system_name"] == "" and kw["src_mac"] == ""


def test_lacp_scale_increments_system_mac_and_port_number():
    base = {"system_mac": "00:11:22:33:44:01", "port_number": 1}
    kw = _scale_kwargs_lacp(base, 4, "ens1f0")
    assert kw["system_mac"] == "00:11:22:33:44:05"
    assert kw["port_number"] == 5


def test_vrrp_scale_increments_vrid_and_wraps_correctly():
    base = {"vrid": 1, "virtual_ips": ["192.168.1.100"],
            "src_mac": ""}
    kw = _scale_kwargs_vrrp(base, 3, "ens1f0")
    assert kw["vrid"] == 4
    assert kw["virtual_ips"] == ["192.168.1.103"]
    # 255 wraps to 1 (VRID 0 is reserved).
    kw_wrap = _scale_kwargs_vrrp(base, 254, "ens1f0")  # 1 + 254 = 255
    assert kw_wrap["vrid"] == 255
    kw_wrap2 = _scale_kwargs_vrrp(base, 255, "ens1f0")  # → wraps back to 1
    assert kw_wrap2["vrid"] == 1


def test_igmp_scale_increments_group_last_octet():
    base = {"group": "239.1.1.1", "src_mac": ""}
    kw = _scale_kwargs_igmp(base, 9, "ens1f0")
    assert kw["group"] == "239.1.1.10"


def test_pim_scale_increments_src_ip_and_generation_id():
    base = {"src_ip": "10.0.0.20", "src_mac": "",
            "generation_id": 0xABCDEF01}
    kw = _scale_kwargs_pim(base, 2, "ens1f0")
    assert kw["src_ip"] == "10.0.0.22"
    assert kw["generation_id"] == 0xABCDEF03


def test_bfd_scale_increments_dst_ip_and_discriminator():
    base = {"dst_ip": "10.0.0.2", "dst_mac": "",
            "my_discriminator": 0x11111111}
    kw = _scale_kwargs_bfd(base, 5, "ens1f0")
    assert kw["dst_ip"] == "10.0.0.7"
    assert kw["my_discriminator"] == 0x11111116


def test_all_protocols_have_scale_incrementer():
    """Every protocol the L2 routes accept has a matching
    incrementer registered — so the fan-out never falls back to
    an "unknown protocol" ValueError for a shipped proto."""
    assert set(_SCALE_INCREMENTERS.keys()) == {
        "lldp", "lacp", "vrrp", "igmp", "pim", "bfd"
    }


# --- start_scaled behavior (with the real factories monkey-patched)


def test_start_scaled_respects_max_cap(monkeypatch):
    """Requesting 10_000 caps at MAX_SCALE_COUNT (server-side
    guard against OOM on runaway counts)."""
    from utils import l2_protocols as mod
    calls = []

    def fake_factory(iface, **kwargs):
        calls.append(kwargs)
        return f"sid-{len(calls)}"

    monkeypatch.setattr(mod, "start_igmp", fake_factory)
    # start_scaled reads factory_map at call time; monkey-patch
    # the module attr so the map picks up the fake.
    sids = mod.start_scaled("igmp", "ens1f0", 10_000,
                            {"group": "239.1.1.1"})
    assert len(sids) == MAX_SCALE_COUNT
    assert len(calls) == MAX_SCALE_COUNT
    # First call = base; last call = base + MAX-1 offset.
    assert calls[0]["group"] == "239.1.1.1"
    # 1 + 499 = 500 → 500 & 0xff = 244.
    assert calls[-1]["group"] == f"239.1.1.{(1 + MAX_SCALE_COUNT - 1) & 0xff}"


def test_start_scaled_partial_success(monkeypatch):
    """If the 3rd iteration blows up, the caller gets a
    session_ids list of length 4 (0, 1, 2 succeeded; 3 failed;
    4 succeeded)."""
    from utils import l2_protocols as mod
    calls = []

    def flaky_factory(iface, **kwargs):
        calls.append(kwargs)
        if len(calls) == 4:  # 4th call (n=3) fails
            raise RuntimeError("simulated failure")
        return f"sid-{len(calls)}"

    monkeypatch.setattr(mod, "start_bfd", flaky_factory)
    sids = mod.start_scaled("bfd", "ens1f0", 5,
                            {"dst_ip": "10.0.0.2",
                             "my_discriminator": 1})
    assert len(sids) == 4   # 5 attempted, 1 failed
    assert len(calls) == 5


def test_start_scaled_all_failures_raises(monkeypatch):
    """Zero survivors → RuntimeError so the caller sees the
    fan-out was completely broken (rather than silently getting
    an empty session_ids list)."""
    import pytest
    from utils import l2_protocols as mod

    def broken(iface, **kwargs):
        raise RuntimeError("everything failed")

    monkeypatch.setattr(mod, "start_igmp", broken)
    with pytest.raises(RuntimeError):
        mod.start_scaled("igmp", "ens1f0", 3, {"group": "239.1.1.1"})


def test_start_scaled_count_1_uses_single_factory_call(monkeypatch):
    """count=1 must be byte-identical to the pre-v0.5.271 single-
    session path — one factory call with no increments applied."""
    from utils import l2_protocols as mod
    calls = []

    def fake(iface, **kwargs):
        calls.append(dict(kwargs))
        return "sid-only"

    monkeypatch.setattr(mod, "start_lldp", fake)
    sids = mod.start_scaled("lldp", "ens1f0", 1,
                            {"chassis_id": "netgen-host"})
    assert sids == ["sid-only"]
    assert calls == [{"chassis_id": "netgen-host"}]


def test_start_scaled_unknown_protocol_raises():
    """Guard against typo'd protocol names — the route layer
    already gate-keeps, but the helper does its own check."""
    import pytest
    from utils import l2_protocols as mod
    with pytest.raises(ValueError, match="unknown protocol"):
        mod.start_scaled("stp-bpdus", "ens1f0", 2, {})


# --- Server API: count wiring ------------------------------------


def test_server_start_impl_accepts_count():
    assert "v0.5.271: scale support" in SRV
    idx = SRV.find("v0.5.271: scale support")
    body = SRV[idx:idx + 1200]
    # count parsed from body with default 1 on bad input.
    assert 'int(body.get("count") or 1)' in body
    # Fan-out path only triggers when count > 1.
    assert "if count > 1:" in body
    # start_scaled dispatched via the l2 module.
    assert "_l2.start_scaled(proto, iface, count, kwargs)" in body


def test_server_start_response_shape_carries_session_ids_and_count():
    idx = SRV.find("v0.5.271: scale support")
    body = SRV[idx:idx + 2000]
    # Response has session_ids + count + requested + session_id
    # (session_id kept as the FIRST id for back-compat).
    for f in ('"session_ids"', '"count"', '"requested"', '"session_id"'):
        assert f in body


# --- Client dialog: count field + submit + response --------------


def test_client_scale_count_spinbox_defined():
    assert "v0.5.271 (L2-E1)" in DLG
    assert "self._scale_count_spin = QSpinBox()" in DLG
    # Range 1-500 caps and default 1 shown as "single session".
    idx = DLG.find("self._scale_count_spin = QSpinBox()")
    body = DLG[idx:idx + 1500]
    assert "setRange(1, 500)" in body
    assert "setValue(1)" in body
    assert '"single session"' in body


def test_client_scale_count_included_in_body_only_when_gt1():
    idx = DLG.find('body: Dict[str, Any] = {"iface": iface}')
    body = DLG[idx:idx + 500]
    # Guard: only include count when > 1 so pre-v0.5.271 servers
    # don't see a spurious count=1 (they'd ignore it anyway but
    # keep the wire clean).
    assert "if _scale > 1:" in body
    assert 'body["count"] = _scale' in body


def test_client_scale_success_dispatcher_defined():
    assert "def _on_start_ok(self, payload: Dict[str, Any]) -> None:" in DLG
    idx = DLG.find("def _on_start_ok")
    body = DLG[idx:idx + 2000]
    # Info box on clean success (count > 1).
    assert "Scale start OK" in body
    # Warning box on partial success.
    assert "Scale start partial" in body
    # Always refreshes the sessions table.
    assert "singleShot(150, self.refresh)" in body


# --- Metadata ---------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 271)
