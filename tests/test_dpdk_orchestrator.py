"""v0.3.11 — DPDK orchestrator pure-logic tests.

The orchestrator collapses the 9-step "fresh server → DPDK ready"
chain into a single declarative plan. This file pins the plan
ordering and the satisfied-step filtering — the GUI shells
(Make DPDK Ready button, Quick Start wizard, Blast a Flow) all
depend on these behaviours and would silently regress if the
filtering broke (e.g. by re-prescribing an install on a host that
already has DPDK).
"""

from __future__ import annotations

import pytest


def test_empty_status_returns_full_plan():
    """A fresh server with nothing set up gets the full 5-action plan
    in canonical order: install → IOMMU → VFIO → hugepages → bind."""
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({})
    kinds = [a.kind for a in actions]
    assert kinds == [
        ActionKind.INSTALL_DPDK,
        ActionKind.ENABLE_IOMMU,
        ActionKind.LOAD_VFIO,
        ActionKind.ALLOCATE_HUGEPAGES,
        ActionKind.BIND_INTERFACE,
    ]


def test_already_installed_skips_install_step():
    """Server with `dpdk_installed=True` and `tx_worker_exists=True`
    shouldn't get an install action."""
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({
        "dpdk_installed": True,
        "tx_worker_exists": True,
    })
    assert ActionKind.INSTALL_DPDK not in [a.kind for a in actions]


def test_install_missing_if_tx_worker_missing():
    """Edge: DPDK libs installed but tx_worker binary missing (broken
    build) — must STILL run the install step. The script handles
    re-building tx_worker against the existing libdpdk."""
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({
        "dpdk_installed": True,
        "tx_worker_exists": False,
    })
    assert ActionKind.INSTALL_DPDK in [a.kind for a in actions]


def test_iommu_action_marks_reboot_required():
    """Enabling IOMMU touches the kernel cmdline → reboot required.
    The GUI uses this flag to pause and prompt the operator."""
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({})
    iommu = next(a for a in actions if a.kind == ActionKind.ENABLE_IOMMU)
    assert iommu.needs_reboot is True


def test_no_other_action_is_reboot_required():
    """Reboot is reserved for IOMMU. If install / VFIO / hugepages /
    bind ever start claiming reboot we want this test to fire so
    we re-evaluate the wizard's pause logic."""
    from utils.dpdk_orchestrator import plan, ActionKind
    for a in plan({}):
        if a.kind != ActionKind.ENABLE_IOMMU:
            assert a.needs_reboot is False, (
                f"Action {a.kind} claims reboot — only IOMMU should"
            )


def test_iommu_skipped_when_already_enabled():
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({"iommu_enabled": True})
    assert ActionKind.ENABLE_IOMMU not in [a.kind for a in actions]


def test_vfio_skipped_when_both_modules_loaded():
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({"vfio_loaded": True, "vfio_pci_loaded": True})
    assert ActionKind.LOAD_VFIO not in [a.kind for a in actions]


def test_vfio_runs_when_only_one_module_loaded():
    """Edge: vfio loaded but vfio-pci not (or vice versa). The
    server's load_modules handler does both, so the action is
    idempotent — but it MUST run, otherwise binding fails later
    with cryptic 'vfio-pci not available' errors."""
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({"vfio_loaded": True, "vfio_pci_loaded": False})
    assert ActionKind.LOAD_VFIO in [a.kind for a in actions]
    actions = plan({"vfio_loaded": False, "vfio_pci_loaded": True})
    assert ActionKind.LOAD_VFIO in [a.kind for a in actions]


def test_hugepages_skipped_when_configured():
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({"hugepages_configured": True})
    assert ActionKind.ALLOCATE_HUGEPAGES not in [a.kind for a in actions]


def test_hugepages_count_override():
    """Caller can override the default 1024 hugepages — the wizard
    UI surfaces this as a spinbox for tuning. Payload must match
    the server's /api/dpdk/hugepages contract: `num_pages` (int) +
    `page_size` (string)."""
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({}, hugepages=256)
    hp = next(a for a in actions if a.kind == ActionKind.ALLOCATE_HUGEPAGES)
    assert hp.body["num_pages"] == 256
    assert hp.body["page_size"] == "2MB"


def test_hugepages_payload_matches_server_contract():
    """v0.3.11 follow-up regression: the orchestrator originally
    sent `count`/`size_kb` but the server's handler reads
    `num_pages`/`page_size`. Mismatch meant the step always
    returned 400 'num_pages required'. Pin the field names so a
    refactor can't quietly drift again."""
    from utils.dpdk_orchestrator import plan, ActionKind
    a = next(
        ac for ac in plan({})
        if ac.kind == ActionKind.ALLOCATE_HUGEPAGES
    )
    # Required server fields present.
    assert "num_pages" in a.body
    assert "page_size" in a.body
    # No leftover legacy field names.
    assert "count" not in a.body
    assert "size_kb" not in a.body


def test_bind_always_included_even_when_bound_nic_exists():
    """Bind is a placeholder action — always emitted so the GUI can
    surface 'bind another NIC' affordance. The bind dialog itself
    decides whether the chosen interface needs binding (no-op if
    already bound)."""
    from utils.dpdk_orchestrator import plan, ActionKind
    actions = plan({
        "dpdk_installed": True,
        "tx_worker_exists": True,
        "iommu_enabled": True,
        "vfio_loaded": True,
        "vfio_pci_loaded": True,
        "hugepages_configured": True,
        "interfaces": [{"name": "eth1", "driver": "vfio-pci"}],
    })
    kinds = [a.kind for a in actions]
    assert kinds == [ActionKind.BIND_INTERFACE], (
        f"Fully-set-up server should have ONLY the bind placeholder, "
        f"got: {kinds}"
    )


def test_is_dpdk_ready_requires_bound_nic():
    """All prereqs satisfied + NO bound NIC → not ready. Operator
    still needs to bind something before traffic flows."""
    from utils.dpdk_orchestrator import is_dpdk_ready
    base = {
        "dpdk_installed": True,
        "tx_worker_exists": True,
        "iommu_enabled": True,
        "vfio_loaded": True,
        "vfio_pci_loaded": True,
        "hugepages_configured": True,
    }
    assert not is_dpdk_ready({**base, "interfaces": []})
    assert is_dpdk_ready({
        **base, "interfaces": [{"name": "eth1", "driver": "vfio-pci"}],
    })


def test_is_dpdk_ready_returns_false_on_any_missing_prereq():
    """Pin one False per prereq → ready=False."""
    from utils.dpdk_orchestrator import is_dpdk_ready
    base = {
        "dpdk_installed": True,
        "tx_worker_exists": True,
        "iommu_enabled": True,
        "vfio_loaded": True,
        "vfio_pci_loaded": True,
        "hugepages_configured": True,
        "interfaces": [{"name": "eth1", "driver": "vfio-pci"}],
    }
    for key in (
        "dpdk_installed", "tx_worker_exists", "iommu_enabled",
        "vfio_loaded", "vfio_pci_loaded", "hugepages_configured",
    ):
        broken = {**base, key: False}
        assert not is_dpdk_ready(broken), (
            f"is_dpdk_ready returned True when {key} was False"
        )


def test_is_dpdk_ready_handles_none_and_empty():
    from utils.dpdk_orchestrator import is_dpdk_ready
    assert not is_dpdk_ready(None)
    assert not is_dpdk_ready({})


def test_recommended_hugepages_downgrades_small_ram():
    """Hosts with <8 GB RAM get the 512 downgrade so we don't OOM
    the rest of the system."""
    from utils.dpdk_orchestrator import recommended_hugepages
    assert recommended_hugepages(4 * 1024**3) == 512
    assert recommended_hugepages(16 * 1024**3) == 1024
    assert recommended_hugepages(None) == 1024


# ─────────────────────────────────── NIC picker

def test_picker_returns_none_on_empty_list():
    from utils.dpdk_orchestrator import pick_default_bind_target
    assert pick_default_bind_target([]) is None
    assert pick_default_bind_target([{}]) is None


def test_picker_skips_already_bound():
    """vfio-bound NICs are filtered out — those don't need re-binding."""
    from utils.dpdk_orchestrator import pick_default_bind_target
    interfaces = [
        {"name": "eth0", "driver": "vfio-pci"},
        {"name": "eth1", "driver": "i40e", "link": "up"},
    ]
    assert pick_default_bind_target(interfaces) == "eth1"


def test_picker_skips_management_interface():
    """Binding the iface the client is talking to the server over
    would disconnect the client. Picker MUST avoid it."""
    from utils.dpdk_orchestrator import pick_default_bind_target
    interfaces = [
        {"name": "ens5", "driver": "i40e", "link": "up"},
        {"name": "eth0", "driver": "mlx5_core", "link": "up"},
    ]
    assert pick_default_bind_target(
        interfaces, management_iface="ens5",
    ) == "eth0"


def test_picker_prefers_link_up_no_ip():
    """Prefer an iface that's link-up AND has no IP — that's the
    typical DPDK target (a freshly-attached NIC the operator wants
    to blast on, not a configured management interface)."""
    from utils.dpdk_orchestrator import pick_default_bind_target
    interfaces = [
        {"name": "eth0", "driver": "i40e", "link": "up", "ipv4": "10.0.0.1"},
        {"name": "eth1", "driver": "i40e", "link": "up"},  # no IP
    ]
    assert pick_default_bind_target(interfaces) == "eth1"


def test_picker_strips_bullet_prefix():
    """Server-tree-formatted names may carry a leading '• ' bullet.
    Picker normalizes."""
    from utils.dpdk_orchestrator import pick_default_bind_target
    interfaces = [{"name": "• eth1", "driver": "i40e"}]
    assert pick_default_bind_target(interfaces) == "eth1"


def test_picker_falls_back_when_no_ideal_candidate():
    """If nothing matches the up+no-IP heuristic, return the first
    non-bound non-management entry rather than None — operator
    expects SOMETHING preselected."""
    from utils.dpdk_orchestrator import pick_default_bind_target
    interfaces = [
        {"name": "eth0", "driver": "i40e", "link": "down"},
        {"name": "eth1", "driver": "i40e", "link": "down"},
    ]
    assert pick_default_bind_target(interfaces) == "eth0"


# ─────────────────────────────────── dialog + menu integration

def test_make_ready_dialog_module_loads():
    """Importing the dialog module must succeed standalone — pulls
    the orchestrator + late-imports the HTTP worker. Catches
    circular-import regressions early."""
    import importlib
    mod = importlib.import_module("widgets.dpdk_make_ready_dialog")
    assert hasattr(mod, "MakeDpdkReadyDialog")


def test_make_ready_dialog_constructs(qapp):
    """Constructing the dialog with a stub URL must not crash. The
    initial status fetch fires async so we don't need a real server
    for the dialog itself to render."""
    from widgets.dpdk_make_ready_dialog import MakeDpdkReadyDialog
    dlg = MakeDpdkReadyDialog("http://stub:5050")
    assert dlg.windowTitle() == "Make DPDK Ready"
    # The run button starts disabled — only enabled after the status
    # fetch returns a non-empty plan.
    assert not dlg._run_btn.isEnabled()


def test_dpdk_menu_mixin_exposes_handler():
    """The DPDK menu mixin must expose `show_dpdk_make_ready_dialog`
    so main.py's menu action can connect to it without raising."""
    from traffic_client.dpdk_menu_actions import (
        TrafficGenClientDPDKMenuActions,
    )
    assert hasattr(
        TrafficGenClientDPDKMenuActions,
        "show_dpdk_make_ready_dialog",
    ), "DPDK menu mixin no longer exposes the Make Ready handler"


def test_main_py_wires_make_ready_menu_action():
    """main.py's DPDK submenu must surface the make-ready orchestrator
    as the FIRST item. v0.5.18 renamed the label from "Make DPDK
    Ready..." to "★ Setup DPDK..." but the underlying handler
    (show_dpdk_make_ready_dialog) is unchanged — same engine, clearer
    entry point. Test pins: the action exists, points to the right
    handler, and appears before Diagnostics in source order (= menu
    insertion order)."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "traffic_client" / "main.py"
    ).read_text()
    # The v0.5.18 entry-point label + handler.
    assert "Setup DPDK" in src, (
        "★ Setup DPDK menu entry missing from main.py (v0.5.18 "
        "renamed from 'Make DPDK Ready')"
    )
    assert "show_dpdk_make_ready_dialog" in src, (
        "Setup DPDK action not connected to its handler"
    )
    # Order check — Setup DPDK must come before Diagnostics.
    pos_setup = src.find("Setup DPDK")
    pos_diag = src.find('"Diagnostics...')
    assert pos_setup > 0 and pos_diag > 0, (
        "Setup DPDK or Diagnostics entry missing"
    )
    assert pos_setup < pos_diag, (
        "★ Setup DPDK moved below Diagnostics — the orchestrator "
        "is supposed to be the FIRST option operators see"
    )


# ─────────────────────────────────── Phase 3: Blast a Flow

def test_sample_stream_shape():
    """The sample stream the Blast-a-Flow dialog POSTs to
    /api/traffic/start must have the canonical client-side shape —
    name, engine=dpdk, frame_type=fixed, L4=UDP, dpdk_enable=True
    inside protocol_selection. Pinning so a future refactor can't
    accidentally drop a required field and have the server reject
    the start request."""
    from widgets.dpdk_blast_flow_dialog import _build_sample_stream
    s = _build_sample_stream("TG 1 - eno8303")
    assert s["name"] == "blast-flow"
    assert s["engine"] == "dpdk"
    assert s["enabled"] is True
    assert s["rx_port"] == "TG 1 - eno8303"
    assert s["stream_id"]  # uuid was assigned
    ps = s["protocol_selection"]
    assert ps["frame_type"] == "fixed"
    # v0.3.11 line-rate tuning: default frame size is Ethernet
    # MTU (1500), sized so a single tx_worker core hits real
    # line rate (8.2 Mpps for 100 G). Pinned separately by
    # test_blast_flow_defaults_tuned_for_line_rate.
    assert ps["fixed_size"] == 1500
    assert ps["L3"] == "IPv4"
    assert ps["L4"] == "UDP"
    assert ps["dpdk_enable"] is True
    assert ps["enabled"] is True


def test_sample_stream_has_top_level_mac_and_ip_for_tx_worker():
    """User-reported v0.3.11 bug: 'Blast a Flow says blasting but
    0 packets'. Root cause: tx_worker reads src_mac / dst_mac /
    src_ip / dst_ip from TOP LEVEL (or protocol_data.{mac,ipv4}),
    NOT from protocol_selection (a GUI-only key). Pre-fix, the
    sample stream populated only protocol_selection — tx_worker
    logged 'missing required fields' and exited with code 2.
    Pin the top-level fields so a future refactor can't lose
    them again."""
    from widgets.dpdk_blast_flow_dialog import _build_sample_stream
    s = _build_sample_stream("TG 1 - eno8303")
    # The four fields tx_worker checks (utils/dpdk_tx_worker.py:230).
    assert s.get("mac_source_address"), (
        "tx_worker reads mac_source_address from top level — "
        "missing means launch fails with code 2"
    )
    assert s.get("mac_destination_address")
    assert s.get("src_ip")
    assert s.get("dst_ip")
    # v0.3.11 line-rate tuning: src + dst are BOTH locally-administered
    # unicast (02:xx). Was broadcast dst pre-tuning; switched because
    # broadcast triggers driver special-handling that often caps wire
    # rate. Hard-coded dst MAC means no ARP needed either way.
    assert s["mac_source_address"].startswith(("02:", "06:", "0a:", "0e:")), (
        "src_mac should be locally-administered (02:/06:/0a:/0e: "
        "prefix per IEEE 802) to avoid vendor-OUI collisions"
    )
    assert s["mac_destination_address"].startswith(("02:", "06:", "0a:", "0e:")), (
        "dst_mac default should be locally-administered unicast — "
        "broadcast (ff:ff:ff:ff:ff:ff) triggers per-driver special "
        "handling and rarely hits actual line rate"
    )
    assert s["mac_destination_address"].lower() != "ff:ff:ff:ff:ff:ff", (
        "dst_mac default must NOT be broadcast — see line-rate "
        "rationale in widgets/dpdk_blast_flow_dialog.py"
    )


def test_sample_stream_mac_override_threads_through():
    """The dialog exposes src_mac / dst_mac inputs for targeted
    unicast testing. The builder must thread the overrides into
    both the top-level (tx_worker) and protocol_selection (GUI
    stats display) fields."""
    from widgets.dpdk_blast_flow_dialog import _build_sample_stream
    s = _build_sample_stream(
        "TG 0 - eth1",
        src_mac="aa:bb:cc:dd:ee:01",
        dst_mac="aa:bb:cc:dd:ee:02",
    )
    assert s["mac_source_address"] == "aa:bb:cc:dd:ee:01"
    assert s["mac_destination_address"] == "aa:bb:cc:dd:ee:02"
    # protocol_selection mirror so the stream-stats display matches
    # what tx_worker is actually sending.
    assert s["protocol_selection"]["src_mac"] == "aa:bb:cc:dd:ee:01"
    assert s["protocol_selection"]["dst_mac"] == "aa:bb:cc:dd:ee:02"


def test_blast_flow_dialog_has_mac_inputs(qapp):
    """v0.3.11 UI fix: src/dst MAC fields visible in the Sample
    Stream group so the operator can override the unicast-by-
    default dst MAC if they want broadcast (or aim it at a real
    peer's MAC for an end-to-end test)."""
    from widgets.dpdk_blast_flow_dialog import (
        DEFAULT_DST_MAC, DEFAULT_SRC_MAC, DpdkBlastFlowDialog,
    )
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    assert hasattr(dlg, "_src_mac")
    assert hasattr(dlg, "_dst_mac")
    assert dlg._src_mac.text() == DEFAULT_SRC_MAC
    assert dlg._dst_mac.text() == DEFAULT_DST_MAC


def test_blast_flow_stall_warning_after_6s_of_zero_tx(qapp):
    """If tx_count stays at 0 across 3 polls (~6 s) while the
    stream is in the tracker, the label flips to a red warning
    explaining that tx_worker probably failed to launch. Catches
    future field-set regressions (the v0.3.11 MAC bug was the
    historical trigger)."""
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    dlg._active_stream_id = "fake-uuid"
    dlg._stats_label.show()
    zero_payload = {
        "active_streams": [
            {"stream_id": "fake-uuid", "tx_count": 0,
             "tx_rate": 0, "frame_size": 64, "dpdk_enable": True},
        ],
    }
    # First two ticks just update normally.
    dlg._on_stream_stats(zero_payload, "")
    dlg._on_stream_stats(zero_payload, "")
    assert "not transmitting" not in dlg._stats_label.text()
    # Third tick crosses the threshold.
    dlg._on_stream_stats(zero_payload, "")
    assert "not transmitting" in dlg._stats_label.text()
    # If tx finally moves, the stall counter resets and warning
    # clears on the next render.
    dlg._on_stream_stats({
        "active_streams": [
            {"stream_id": "fake-uuid", "tx_count": 1000,
             "tx_rate": 500.0, "frame_size": 64, "dpdk_enable": True},
        ],
    }, "")
    assert dlg._tx_stall_ticks == 0
    assert "not transmitting" not in dlg._stats_label.text()


def test_sample_stream_honors_overrides():
    from widgets.dpdk_blast_flow_dialog import _build_sample_stream
    s = _build_sample_stream(
        "TG 0 - eth1",
        frame_size=128, src_ip="1.2.3.4", dst_ip="5.6.7.8",
        dst_port=42, rate_pps=2_000_000,
    )
    assert s["protocol_selection"]["fixed_size"] == 128
    assert s["protocol_selection"]["src_ip"] == "1.2.3.4"
    assert s["protocol_selection"]["dst_ip"] == "5.6.7.8"
    assert s["protocol_selection"]["dst_port"] == 42
    assert s["protocol_selection"]["rate_pps"] == 2_000_000


def test_blast_flow_dialog_module_loads():
    """Catch circular-import / pyqt5-version mismatches at import
    time so the menu action doesn't fail with a runtime traceback."""
    import importlib
    mod = importlib.import_module("widgets.dpdk_blast_flow_dialog")
    assert hasattr(mod, "DpdkBlastFlowDialog")
    assert hasattr(mod, "_build_sample_stream")


def test_blast_flow_defaults_tuned_for_line_rate():
    """v0.3.11 line-rate tuning: pin the defaults that make a
    one-click blast actually HIT wire line rate. Two key knobs:

    1. Frame size = 1500 (Ethernet MTU). At 100 G, line rate is
       8.2 Mpps with 1500 B frames — a single tx_worker core
       handles that comfortably (it tops out around 46 Mpps).
       The old 64 B default needed 148.8 Mpps for 100 G line
       rate, way above single-core tx_worker capacity, so the
       user saw ~23 Gbps not 100 Gbps.

    2. Dst MAC = locally-administered UNICAST (02:xx prefix).
       The old broadcast (ff:ff:ff:ff:ff:ff) triggered per-driver
       special handling (software broadcast filter, rate caps)
       that often kept the NIC from hitting wire speed.

    If either default regresses, the one-click demo will not
    show actual line rate — fail loudly here."""
    from widgets.dpdk_blast_flow_dialog import (
        DEFAULT_DST_MAC, DEFAULT_FRAME_SIZE,
    )
    assert DEFAULT_FRAME_SIZE == 1500, (
        f"DEFAULT_FRAME_SIZE = {DEFAULT_FRAME_SIZE}, expected 1500. "
        f"Smaller frames need more pps to hit line rate than a "
        f"single tx_worker core can produce."
    )
    assert DEFAULT_DST_MAC.startswith(("02:", "06:", "0a:", "0e:")), (
        f"DEFAULT_DST_MAC = {DEFAULT_DST_MAC!r} — must be a "
        f"locally-administered UNICAST address (02:/06:/0a:/0e: "
        f"prefix). Broadcast hits driver special-case paths and "
        f"rarely achieves wire-rate."
    )
    assert DEFAULT_DST_MAC.lower() != "ff:ff:ff:ff:ff:ff"


def test_blast_flow_dialog_constructs(qapp):
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    assert dlg.windowTitle() == "Blast a DPDK Flow"
    # Operator-tunable sample-stream knobs are present.
    # v0.3.11 line-rate tuning: default frame size 1500 (Ethernet
    # MTU) so a single tx_worker core can hit 100 G line rate
    # (8.2 Mpps) — was 64 which capped at ~23 Gbps single-core
    # because tx_worker tops at ~46 Mpps.
    assert dlg._frame_size.value() == 1500
    assert dlg._dst_ip.text() == "10.0.0.2"
    assert dlg._rate_pps.value() == 0  # 0 == line rate


def test_blast_flow_has_live_stats_poll(qapp):
    """User-reported: 'Blast DPDK flow window shows blasting, but
    don't see any traffic/flow stats.' Root cause: for Mellanox
    bifurcated NICs, DPDK transmits via RDMA verbs which BYPASS
    the kernel — so the main window's iface stats table (which
    reads psutil) shows 0 fps regardless of how fast tx_worker
    is hammering.

    Fix: the dialog now polls /api/streams/stats every 2 s and
    surfaces per-stream tx_rate / tx_count inside the dialog
    itself. That endpoint reads from the server's stream_tracker
    which tx_worker reports into directly — independent of
    kernel iface counters."""
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    # Stats label exists but is hidden until the flow starts.
    assert hasattr(dlg, "_stats_label")
    assert not dlg._stats_label.isVisible()
    # Poll helpers wired.
    assert hasattr(dlg, "_poll_stream_stats")
    assert hasattr(dlg, "_on_stream_stats")
    assert hasattr(dlg, "_stop_stats_poll")
    # Timer attribute init.
    assert dlg._stats_timer is None
    assert dlg._last_tx_count == 0


def test_blast_flow_stats_poll_renders_running_stream(qapp):
    """Feed _on_stream_stats a synthetic /api/streams/stats payload
    matching our blast-flow stream_id; the label should render the
    pretty-formatted tx counters."""
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    dlg._active_stream_id = "abc-123-fake-uuid"
    dlg._active_iface = "enp160s0f0np0"
    # Show the label first (normally done by _on_blast_started).
    dlg._stats_label.show()
    dlg._on_stream_stats({
        "active_streams": [
            {
                "stream_id": "abc-123-fake-uuid",
                "interface": "enp160s0f0np0",
                "tx_count": 123_456_789,
                "tx_rate": 1_500_000.0,  # pps
                "frame_size": 64,
                "dpdk_enable": True,
            },
        ],
    }, "")
    text = dlg._stats_label.text()
    assert "TX:" in text
    assert "DPDK" in text
    assert "123,456,789" in text  # tx_count formatted with commas
    # 1.5 Mpps formatted
    assert "Mpps" in text or "Kpps" in text
    # bps derived from pps × 64 B × 8 = 768 Mbps
    assert "Mbps" in text or "Gbps" in text


def test_blast_flow_stats_poll_warns_when_stream_not_in_tracker(qapp):
    """If /api/streams/stats returns active_streams but ours isn't
    in the list, the label shows an amber warning so the operator
    knows tx_worker may have failed to launch."""
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    dlg._active_stream_id = "missing-stream-id"
    dlg._stats_label.show()
    dlg._on_stream_stats({
        "active_streams": [
            {"stream_id": "different-id", "tx_count": 0, "tx_rate": 0},
        ],
    }, "")
    text = dlg._stats_label.text()
    assert "Stream not in tracker" in text or "not in tracker" in text


def test_blast_flow_stop_clears_stats_timer(qapp):
    """_on_blast_stopped must halt the stats-poll timer so it stops
    firing against /api/streams/stats after the flow is gone."""
    from PyQt5.QtCore import QTimer
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    dlg._active_stream_id = "fake-uuid"
    dlg._active_iface = "ens5"
    # Simulate the post-start state.
    dlg._stats_timer = QTimer(dlg)
    dlg._stats_timer.setInterval(2000)
    dlg._stats_timer.start()
    assert dlg._stats_timer.isActive()
    # Synthesize a successful stop response (server returned the
    # stopped entry) so _on_blast_stopped takes the success branch.
    dlg._on_blast_stopped({
        "stopped": [{"interface": "ens5", "stream_id": "fake-uuid"}],
    }, "")
    # Timer attr cleared after stop.
    assert dlg._stats_timer is None


def test_blast_flow_stop_offers_restart(qapp):
    """v0.3.11: after a successful stop, the dialog must offer to
    re-launch the flow instead of locking the operator out (the
    previous "Closed" disabled-button UI forced them to close +
    reopen the whole dialog). The Stop Flow button flips to
    "Start Flow Again" and stays enabled. _active_iface is
    preserved so the restart skips the NIC-pick step.
    """
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    dlg._active_stream_id = "fake-uuid"
    dlg._active_iface = "enp181s0f0np0"
    # Successful stop.
    dlg._on_blast_stopped({
        "stopped": [{
            "interface": "enp181s0f0np0",
            "stream_id": "fake-uuid",
        }],
    }, "")
    # Button text + state for restart.
    assert dlg._run_btn.text() == "Start Flow Again"
    assert dlg._run_btn.isEnabled()
    # Active stream id cleared (a fresh UUID is generated on
    # restart); active iface preserved so restart can skip the
    # orchestrator's NIC-pick step.
    assert dlg._active_stream_id is None
    assert dlg._active_iface == "enp181s0f0np0"
    # Restart handler exists.
    assert hasattr(dlg, "_on_restart_flow")


def test_orchestrator_setup_dialogs_are_application_modal(qapp):
    """v0.3.11: the short setup flows (Make Ready, Quick Start
    wizard) must block the main window — the operator shouldn't
    be able to drift away mid-step. Pin Qt.ApplicationModal on
    those two; the Blast a Flow dialog is intentionally NON-modal
    (see test_blast_flow_dialog_is_non_modal below) because it
    keeps running indefinitely while traffic blasts.
    """
    from PyQt5.QtCore import Qt
    from widgets.dpdk_make_ready_dialog import MakeDpdkReadyDialog
    from widgets.dpdk_quick_start_wizard import DpdkQuickStartWizard

    make_ready = MakeDpdkReadyDialog("http://stub:5050")
    assert make_ready.windowModality() == Qt.ApplicationModal, (
        "MakeDpdkReadyDialog must be ApplicationModal — operator "
        "shouldn't be able to click the main window while the "
        "orchestrator is running steps"
    )
    wiz = DpdkQuickStartWizard("http://stub:5050")
    assert wiz.windowModality() == Qt.ApplicationModal, (
        "DpdkQuickStartWizard must be ApplicationModal — its "
        "page-state machine assumes the operator can't drift to "
        "the main window mid-wizard"
    )


def test_blast_flow_dialog_is_non_modal(qapp):
    """v0.3.11 modality flip: the Blast a Flow dialog INHERITS
    from MakeDpdkReadyDialog (which sets ApplicationModal for its
    short setup flow) but overrides to NonModal. Reason: blast
    runs indefinitely while traffic is in flight, and the operator
    legitimately wants to use the main window (look at chassis
    table, kick off other work) while the flow blasts. The flow
    keeps running regardless of focus; closing the dialog still
    issues the stop request via closeEvent."""
    from PyQt5.QtCore import Qt
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    blast = DpdkBlastFlowDialog("http://stub:5050")
    assert blast.windowModality() == Qt.NonModal, (
        f"DpdkBlastFlowDialog must be NonModal so the operator can "
        f"keep using the main window while traffic blasts — got "
        f"{blast.windowModality()!r}"
    )


def test_blast_flow_sample_stream_uses_grid_layout(qapp):
    """v0.3.11 layout fix: the Sample Stream group switched from
    a stack of QHBoxLayouts (which let long QLineEdit text bleed
    visually into the next column's label) to a QGridLayout so
    labels + fields align cleanly. Pin the layout type so a
    refactor doesn't silently revert to the overlapping rows."""
    from PyQt5.QtWidgets import QGridLayout, QGroupBox
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    # Find the "Sample Stream" group box.
    sample_group = None
    for g in dlg.findChildren(QGroupBox):
        if g.title() == "Sample Stream":
            sample_group = g
            break
    assert sample_group is not None, "Sample Stream group missing"
    assert isinstance(sample_group.layout(), QGridLayout), (
        f"Sample Stream group layout reverted to "
        f"{type(sample_group.layout()).__name__} — labels will "
        f"overlap field text again"
    )


def test_blast_flow_field_minimums_prevent_squeeze(qapp):
    """v0.3.11 follow-up: after a successful bind the action-list
    row text grows long ("✓ Bind a NIC to vfio-pci — enp181s0f0np0
    — bifurcated (no rebind needed)"), forcing the dialog wider
    and triggering a grid reflow. Without minimum widths on the
    line edits, the IP / MAC fields could shrink below the size
    needed to render their default values, recreating the
    overlap-with-next-label problem the grid layout was supposed
    to solve. Pin the minimums so a re-layout can never squeeze
    the fields below readable size."""
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    # IP fields need ~130 px to render ddd.ddd.ddd.ddd + padding.
    assert dlg._src_ip.minimumWidth() >= 130
    assert dlg._dst_ip.minimumWidth() >= 130
    # MAC fields need ~160 px for ff:ff:ff:ff:ff:ff + padding.
    assert dlg._src_mac.minimumWidth() >= 160
    assert dlg._dst_mac.minimumWidth() >= 160
    # v0.3.11 follow-up: explicit minimum HEIGHT so the line edit
    # can never compress its text vertically (which caused weird
    # strikethrough-looking artifacts on macOS Sonoma when the
    # custom stylesheet's padding interacted with native rendering).
    assert dlg._src_ip.minimumHeight() >= 24
    assert dlg._dst_ip.minimumHeight() >= 24
    assert dlg._src_mac.minimumHeight() >= 24
    assert dlg._dst_mac.minimumHeight() >= 24


def test_blast_flow_sample_stream_row_gaps_breathe(qapp):
    """v0.3.11 layout follow-up #3: the user's screenshot showed
    IP and MAC line edits visually TOUCHING — only ~1 px of
    vertical gap between them. Root cause: QGridLayout sizes each
    row by QLabel.sizeHint (~19 px) and IGNORES
    QLineEdit.minimumHeight(24). Line edits then overflowed their
    cells by 5 px each, eating the vertical spacing into the next
    row. Compounding it, the parent dialog's action list (stretch
    1) was hogging vertical space, squeezing the Sample Stream
    group below its sizeHint and crunching everything further.

    The fix is layered:
      1. setFixedHeight (not setMinimumHeight) on line edits so
         the grid sees an authoritative size for row-height calc.
      2. Equal min heights on the row LABELS so both column-0
         and column-1 widgets agree on the row height.
      3. setRowMinimumHeight on the line-edit rows.
      4. vSpacing 6→10 for extra breathing room.
      5. min-height pinned to sizeHint on the group itself
         (separate test below).

    Pin each LAYOUT SETTING we set so any layer getting stripped
    out later fails before shipping. We pin the API contract
    rather than pixel positions because pixel measurement needs
    show()+processEvents which races the dialog's background
    survey thread in headless Qt tests."""
    from PyQt5.QtWidgets import QGridLayout, QGroupBox, QLabel, QLineEdit
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    sample = next(
        (g for g in dlg.findChildren(QGroupBox) if g.title() == "Sample Stream"),
        None,
    )
    assert sample is not None
    grid = sample.layout()
    assert isinstance(grid, QGridLayout)
    # 1. vSpacing must be set high enough that even minor cell
    # overflow leaves a visible gap.
    assert grid.verticalSpacing() >= 8, (
        f"vSpacing={grid.verticalSpacing()} too tight — "
        f"any cell overflow will eat it entirely"
    )
    # 2. line-edit rows (1 + 2) must have explicit min row height
    # so the grid allocates real space (not just label-height).
    for row in (1, 2):
        rmh = grid.rowMinimumHeight(row)
        assert rmh >= 24, (
            f"Row {row} setRowMinimumHeight={rmh}, expected >= 24 — "
            f"grid will fall back to label sizeHint (~19 px) and "
            f"line edits will overflow into the next row's spacing"
        )
    # 3. The four line edits must be at fixed height (not just min)
    # so the grid trusts the size for row computation.
    for le in (dlg._src_ip, dlg._dst_ip, dlg._src_mac, dlg._dst_mac):
        assert le.minimumHeight() == le.maximumHeight() and le.minimumHeight() >= 24, (
            f"{le.objectName() or le.text()!r} not fixed-height "
            f"(min={le.minimumHeight()}, max={le.maximumHeight()}) — "
            f"grid will not consult it for row-height calc"
        )
    # 4. Row labels in line-edit rows must have matching min
    # height so column-0 and column-1 agree on row height.
    for row in (1, 2):
        for col in (0, 2):
            it = grid.itemAtPosition(row, col)
            assert it is not None
            w = it.widget()
            assert isinstance(w, QLabel)
            assert w.minimumHeight() >= 24, (
                f"Label {w.text()!r} at ({row},{col}) min height "
                f"{w.minimumHeight()} < 24 — row height calc will "
                f"shrink to the smaller value"
            )


def test_blast_flow_dialog_grows_to_fit_sample_stream(qapp):
    """v0.3.11 layout follow-up #4: the prior attempt pinned the
    Sample Stream group's min-height to its sizeHint. That fixed
    the squeeze but introduced a worse bug: when the parent
    dialog (MakeDpdkReadyDialog) was sized for its ORIGINAL
    child list (no Sample Stream), it didn't recompute its own
    minimum after we insertWidget(3, cfg). The group then forced
    its min size, and Qt let it OVERFLOW into the next widget —
    the action QListWidget rendered ON TOP of the Rate row in
    the user's latest screenshot.

    Correct fix: grow the DIALOG itself so every widget gets its
    sizeHint and no one has to overflow. Pin dialog.minimumHeight
    >= dialog.sizeHint so the parent can never re-shrink below
    the natural size."""
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    assert dlg.minimumHeight() >= dlg.sizeHint().height(), (
        f"Dialog min height ({dlg.minimumHeight()}) is below "
        f"its sizeHint ({dlg.sizeHint().height()}) — there isn't "
        f"enough room for every child widget; the Sample Stream "
        f"group will overflow into the action list."
    )


def test_blast_flow_sample_stream_labels_vcenter_aligned(qapp):
    """v0.3.11 follow-up: with setMinimumHeight(24) forcing line edits
    taller than their plain QLabel siblings, passing only Qt.AlignRight
    to addWidget() let each label sit at the TOP of the grid cell
    while the line edit centered itself — visually the label text
    drifted off the field's baseline and read as "overlapping rows"
    in the user's screenshot. Pin Qt.AlignVCenter on every label so
    no future refactor strips it off."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QGridLayout, QGroupBox, QLabel
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    sample_group = next(
        (g for g in dlg.findChildren(QGroupBox) if g.title() == "Sample Stream"),
        None,
    )
    assert sample_group is not None, "Sample Stream group missing"
    grid = sample_group.layout()
    assert isinstance(grid, QGridLayout)
    misaligned = []
    for r in range(grid.rowCount()):
        for c in range(grid.columnCount()):
            item = grid.itemAtPosition(r, c)
            if item is None:
                continue
            w = item.widget()
            if not isinstance(w, QLabel):
                continue
            align = int(item.alignment())
            if not (align & int(Qt.AlignVCenter)):
                misaligned.append((r, c, w.text(), align))
    assert not misaligned, (
        f"Sample Stream labels missing Qt.AlignVCenter — "
        f"labels will drift to top of cell while line edits center, "
        f"recreating the 'overlapping row' look: {misaligned!r}"
    )


def test_blast_flow_line_edits_use_native_styling(qapp):
    """v0.3.11 regression catch: the custom QLineEdit stylesheet
    that the prior fix introduced (border + border-radius +
    padding) rendered the IP / MAC field values as if struck-
    through on macOS Sonoma — `padding: 2px 6px` clipped the
    text vertically against the styled border. Native macOS
    QLineEdit already has a crisp visible border, so dropping
    the custom stylesheet IS the fix.

    Pin styleSheet=='' so a future attempt to "add a border"
    fires this test instead of re-creating the rendering bug.
    """
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    for le, name in (
        (dlg._src_ip, "src_ip"),
        (dlg._dst_ip, "dst_ip"),
        (dlg._src_mac, "src_mac"),
        (dlg._dst_mac, "dst_mac"),
    ):
        assert le.styleSheet() == "", (
            f"{name} has a custom stylesheet ({le.styleSheet()!r}) — "
            f"drop it and use native rendering. Custom padding "
            f"caused vertical text clipping on macOS Sonoma."
        )


def test_make_ready_action_list_elides_long_rows(qapp):
    """v0.3.11 follow-up: the action-list QListWidget must elide
    + word-wrap long rows so a successful-bind row doesn't drag
    the dialog wider. If the dialog grows, downstream layouts
    (like Blast a Flow's Sample Stream grid) reflow and field
    minimum widths kick in to prevent overlap — but elide-mode
    at the source is the cleanest defense."""
    from PyQt5.QtCore import Qt
    from widgets.dpdk_make_ready_dialog import MakeDpdkReadyDialog
    dlg = MakeDpdkReadyDialog("http://stub:5050")
    assert dlg._list.textElideMode() == Qt.ElideRight, (
        "Action list textElideMode must be ElideRight — long "
        "row text would otherwise force the dialog wider"
    )
    assert dlg._list.wordWrap() is True
    assert dlg._list.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff, (
        "Action list must suppress horizontal scrollbar — operator "
        "shouldn't have to scroll to read a single row"
    )


def test_blast_flow_stats_label_wraps(qapp):
    """v0.3.11 follow-up: the stats label can carry very long text
    ('TX: 46 Mpps · 23.3 Gbps · 2,193,742,528 packets total ·
    engine: DPDK'). Without word-wrap the label forces the dialog
    wider when it first appears, triggering a Sample Stream
    grid reflow."""
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    assert dlg._stats_label.wordWrap() is True


def test_blast_flow_stop_response_check_catches_zero_stopped(qapp):
    """v0.3.11 bug: /api/traffic/stop's schema expects `streams` as
    a LIST of {interface, stream_id} entries, but the dialog used
    to send a DICT keyed by interface (start-endpoint shape). Server
    iterated keys, entry.get('interface') returned None on each
    iteration, no streams were stopped, but the dialog claimed
    success because HTTP returned 200.

    Now the dialog inspects the response's `stopped` list. Empty
    list → red error banner pointing to manual pkill recovery.
    Pin the check so a future shape-regression surfaces here.
    """
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    dlg._active_stream_id = "fake-uuid"
    dlg._active_iface = "ens5"
    # Simulate server returning success-ish HTTP but zero stops.
    dlg._on_blast_stopped({"stopped": []}, "")
    # Dialog should NOT claim success — operator must see the error.
    assert "didn't actually stop" in dlg._detail.text() \
        or "Stop reported success but" in dlg._detail.text(), (
        f"Dialog claimed success on a 0-stopped response: "
        f"{dlg._detail.text()!r}"
    )
    # Run button must be re-enabled so operator can retry.
    assert dlg._run_btn.isEnabled()


def test_blast_flow_stop_payload_uses_list_shape(qapp, monkeypatch):
    """Pin that _on_stop_flow POSTs the LIST-of-entries shape
    that /api/traffic/stop expects (not the DICT-keyed-by-iface
    shape /api/traffic/start uses). Capture the JSON sent to the
    worker rather than mocking the worker class itself."""
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog
    dlg = DpdkBlastFlowDialog("http://stub:5050")
    dlg._active_stream_id = "fake-uuid"
    dlg._active_iface = "enp1s0f0"

    captured = {}

    class _FakeWorker:
        def __init__(self, method, url, json=None, timeout=None):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json

        def setParent(self, parent):
            pass

        @property
        def done(self):
            class _Sig:
                def connect(self, *a, **k):
                    pass
            return _Sig()

        def start(self):
            pass

    # Patch the worker factory.
    monkeypatch.setattr(
        "widgets.dpdk_blast_flow_dialog._api_worker",
        lambda: _FakeWorker,
    )
    dlg._on_stop_flow()

    assert captured["method"] == "POST"
    assert "/api/traffic/stop" in captured["url"]
    streams = captured["json"]["streams"]
    # MUST be a list, NOT a dict.
    assert isinstance(streams, list), (
        f"Stop payload's 'streams' must be a LIST per the "
        f"/api/traffic/stop schema; got {type(streams).__name__}"
    )
    assert len(streams) == 1
    entry = streams[0]
    assert entry["interface"] == "enp1s0f0"
    assert entry["stream_id"] == "fake-uuid"


def test_blast_flow_mixin_handler_exposed():
    from traffic_client.dpdk_menu_actions import (
        TrafficGenClientDPDKMenuActions,
    )
    assert hasattr(
        TrafficGenClientDPDKMenuActions, "show_dpdk_blast_flow_dialog",
    )


def test_blast_flow_handler_uses_show_not_exec(qapp):
    """v0.3.11 multi-blast support: show_dpdk_blast_flow_dialog must
    call show() (returns immediately), NOT exec_() (blocks the
    menu's calling code until close). With exec_(), the operator
    can't open a second Blast a Flow dialog while the first is
    still running — defeats the parallel multi-NIC blast workflow.

    Source-grep over the handler body. Pin both halves:
      • the show()/raise_()/activateWindow() trio is present
      • no .exec_( call (which would gate the second dialog)
    """
    import inspect
    from traffic_client.dpdk_menu_actions import (
        TrafficGenClientDPDKMenuActions,
    )
    src = inspect.getsource(
        TrafficGenClientDPDKMenuActions.show_dpdk_blast_flow_dialog
    )
    assert "dlg.show()" in src, (
        "Blast a Flow handler must use dlg.show() so the menu's "
        "calling code returns immediately and a second dialog can "
        "be opened in parallel"
    )
    assert ".exec_(" not in src, (
        "Blast a Flow handler must NOT use exec_() — it blocks the "
        "calling code until the dialog closes, preventing the "
        "operator from opening a second Blast a Flow for a parallel "
        "NIC blast"
    )


def test_multiple_blast_flow_dialogs_coexist(qapp, monkeypatch):
    """End-to-end behavioural: spin up the menu mixin, fire the
    handler three times in a row, and assert all three Blast a Flow
    dialogs end up tracked. Closing one prunes the list. This is
    what enables parallel multi-NIC blasts — the operator opens N
    dialogs (one per iface) without any blocking in between.

    Stubs out DpdkBlastFlowDialog with a bare QDialog so the test
    doesn't spin up the survey thread that the real dialog starts
    in __init__ (the thread races teardown under pytest on macOS
    and crashes the runner — the real-dialog smoke is covered by
    the existing test_blast_flow_dialog_constructs)."""
    from PyQt5.QtWidgets import QDialog, QWidget
    from traffic_client.dpdk_menu_actions import (
        TrafficGenClientDPDKMenuActions,
    )

    # Bare QDialog stand-in: same finished signal, same close(), no
    # survey thread, no DPDK API workers. Implements the public
    # surface the menu handler calls into.
    class _StubBlastDialog(QDialog):
        def __init__(self, server_url, management_iface=None, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Blast a DPDK Flow [stub]")
            self.server_url = server_url
            self._active_iface = None
        def set_sibling_iface_provider(self, provider):
            self._sibling_iface_provider = provider

    import widgets.dpdk_blast_flow_dialog as bf_mod
    monkeypatch.setattr(bf_mod, "DpdkBlastFlowDialog", _StubBlastDialog)

    class _Host(QWidget, TrafficGenClientDPDKMenuActions):
        def __init__(self):
            super().__init__()
        def _resolve_single_server_for_dpdk(self, _label):
            return ({"address": "http://stub:5050"}, "http://stub:5050")

    host = _Host()
    host.show_dpdk_blast_flow_dialog()
    host.show_dpdk_blast_flow_dialog()
    host.show_dpdk_blast_flow_dialog()
    qapp.processEvents()
    assert len(host._blast_dialogs) == 3, (
        f"Expected 3 parallel Blast a Flow dialogs, got "
        f"{len(host._blast_dialogs)} — if this drops to 1 the "
        f"handler is probably back to exec_() (blocks the menu) "
        f"or to a singleton pattern (replaces instead of stacks)"
    )
    # Pruning hook: close one, list shrinks via finished-signal.
    first = host._blast_dialogs[0]
    first.close()
    qapp.processEvents()
    assert first not in host._blast_dialogs, (
        "Closed dialog should be pruned from _blast_dialogs by the "
        "finished-signal hook; otherwise the list grows unbounded"
    )
    assert len(host._blast_dialogs) == 2
    for d in list(host._blast_dialogs):
        d.close()
    qapp.processEvents()


def test_blast_flow_handler_cascades_window_positions(qapp, monkeypatch):
    """v0.3.11 multi-dialog UX: opening 3 parallel Blast Flow
    dialogs at the same screen coordinates makes them stack —
    operator sees one window, doesn't realize others opened.
    Pin that the handler offsets each new dialog from the
    previous so they're visually distinct."""
    from PyQt5.QtWidgets import QDialog, QWidget
    from traffic_client.dpdk_menu_actions import (
        TrafficGenClientDPDKMenuActions,
    )

    moves = []

    class _StubBlastDialog(QDialog):
        def __init__(self, server_url, management_iface=None, parent=None):
            super().__init__(parent)
            self.server_url = server_url
        def move(self, *args):
            moves.append(args)
            super().move(*args)
        def set_sibling_iface_provider(self, provider):
            pass

    import widgets.dpdk_blast_flow_dialog as bf_mod
    monkeypatch.setattr(bf_mod, "DpdkBlastFlowDialog", _StubBlastDialog)

    class _Host(QWidget, TrafficGenClientDPDKMenuActions):
        def __init__(self):
            super().__init__()
            self.setGeometry(100, 100, 800, 600)
        def _resolve_single_server_for_dpdk(self, _label):
            return ({"address": "http://stub:5050"}, "http://stub:5050")

    host = _Host()
    host.show_dpdk_blast_flow_dialog()
    host.show_dpdk_blast_flow_dialog()
    host.show_dpdk_blast_flow_dialog()
    qapp.processEvents()

    # The FIRST dialog gets no explicit move (uses Qt's default
    # position). The 2nd and 3rd should be moved to different
    # cascaded positions.
    assert len(moves) >= 2, (
        f"Expected at least 2 explicit move() calls (for the 2nd "
        f"and 3rd dialogs), got {len(moves)} — handler is no "
        f"longer cascading window positions, parallel dialogs "
        f"will stack on top of each other"
    )
    # All cascaded positions must be distinct.
    distinct_positions = set(moves)
    assert len(distinct_positions) == len(moves), (
        f"Cascade positions overlapped: {moves!r} — dialogs will "
        f"still stack visually even though move() was called"
    )
    for d in list(host._blast_dialogs):
        d.close()
    qapp.processEvents()


def test_blast_flow_dialog_warns_on_iface_already_claimed(qapp, monkeypatch):
    """v0.3.11 multi-dialog safety: if a sibling Blast Flow dialog
    already has a stream running on the same iface, starting a
    second tx_worker on the same NIC hits PMD lock contention or
    halves throughput. The dialog must surface a warning at
    _on_dpdk_ready time — operator confirms or cancels. Without
    this guard the operator sees 'blasting' but throughput is
    silently degraded."""
    from PyQt5.QtWidgets import QMessageBox
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog

    dlg = DpdkBlastFlowDialog("http://stub:5050")
    dlg.set_sibling_iface_provider(lambda: {"eth5"})

    # Patch the modal to auto-cancel — we just want to verify it
    # was OPENED for the conflicting iface, not that the user
    # actually clicked Cancel.
    shown = []
    def _fake_exec(self):
        shown.append(self.text())
        return QMessageBox.Cancel
    monkeypatch.setattr(QMessageBox, "exec_", _fake_exec)

    dlg._on_dpdk_ready("eth5")
    qapp.processEvents()

    assert shown, (
        "No warning dialog opened when starting a Blast Flow on an "
        "iface already claimed by a sibling — operator gets silent "
        "throughput halving / DPDK PMD lock error instead"
    )
    assert "eth5" in shown[0], (
        f"Warning text didn't name the conflicting iface: {shown[0]!r}"
    )
    # Cancel must abort the start — _active_iface stays None,
    # _active_stream_id stays None.
    assert dlg._active_iface is None, (
        "Cancel on the conflict warning must NOT proceed to claim "
        "the iface, but _active_iface was set anyway"
    )
    assert dlg._active_stream_id is None, (
        "Cancel must NOT POST /api/traffic/start"
    )


def test_blast_flow_dialog_proceeds_on_iface_ignore(qapp, monkeypatch):
    """The conflict warning offers Ignore for the rare case the
    operator specifically wants to test shared-port contention.
    Verify Ignore proceeds normally (sets _active_iface)."""
    from PyQt5.QtWidgets import QMessageBox
    from widgets.dpdk_blast_flow_dialog import DpdkBlastFlowDialog

    dlg = DpdkBlastFlowDialog("http://stub:5050")
    dlg.set_sibling_iface_provider(lambda: {"eth7"})

    monkeypatch.setattr(QMessageBox, "exec_",
                        lambda self: QMessageBox.Ignore)
    # Stub the API worker so we don't actually POST.
    posted = []
    import widgets.dpdk_blast_flow_dialog as bf_mod
    class _StubWorker:
        def __init__(self, **kw):
            posted.append(kw)
            self.done = type("S", (), {"connect": lambda s, *a: None})()
        def setParent(self, p): pass
        def start(self): pass
    monkeypatch.setattr(bf_mod, "_api_worker", lambda: _StubWorker)

    dlg._on_dpdk_ready("eth7")
    qapp.processEvents()
    assert dlg._active_iface == "eth7", (
        "Ignore on the conflict warning must proceed to claim the "
        "iface (operator explicitly chose to override)"
    )
    assert dlg._active_stream_id is not None
    assert posted, "Ignore must still POST /api/traffic/start"


def test_blast_flow_handler_retains_dialog_reference(qapp):
    """show() returns immediately; if we don't hold a Python ref to
    the dialog, it gets garbage-collected mid-blast and the flow
    dies silently (and tx_worker keeps running on the server, an
    orphaned process the operator can't stop from the GUI).
    Pin that the handler appends to a list attribute the instance
    owns, and that the list-prune-on-close hook is wired so a
    long-running session doesn't leak refs forever."""
    import inspect
    from traffic_client.dpdk_menu_actions import (
        TrafficGenClientDPDKMenuActions,
    )
    src = inspect.getsource(
        TrafficGenClientDPDKMenuActions.show_dpdk_blast_flow_dialog
    )
    assert "_blast_dialogs" in src, (
        "Handler must keep dialog refs in self._blast_dialogs — "
        "without a ref the dialog is GC-eligible the moment "
        "show_dpdk_blast_flow_dialog returns, killing the flow"
    )
    assert ".append(dlg)" in src, (
        "Handler must append the new dialog to _blast_dialogs"
    )
    assert "dlg.finished.connect" in src, (
        "Handler must connect dialog.finished to a prune hook so "
        "_blast_dialogs doesn't grow unbounded over a long session"
    )


def test_main_py_wires_blast_flow_menu():
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "traffic_client" / "main.py"
    ).read_text()
    assert "Blast a Flow..." in src
    assert "show_dpdk_blast_flow_dialog" in src


# ─────────────────────────────────── Phase 4: Quick Start Wizard

def test_quick_start_wizard_module_loads():
    import importlib
    mod = importlib.import_module("widgets.dpdk_quick_start_wizard")
    assert hasattr(mod, "DpdkQuickStartWizard")


def test_quick_start_wizard_has_five_pages(qapp):
    """Wizard structure: Welcome → Survey → Plan → Run → Done.
    Pin the count so a refactor that drops a page (e.g. merges
    Survey+Plan) fires this test and asks 'are you sure?'"""
    from widgets.dpdk_quick_start_wizard import DpdkQuickStartWizard
    wiz = DpdkQuickStartWizard("http://stub:5050")
    assert len(wiz.pageIds()) == 5


def test_quick_start_wizard_pages_instantiable(qapp):
    """Each page class constructs standalone. Catches breakage in
    any individual page without needing the full wizard flow."""
    from widgets.dpdk_quick_start_wizard import (
        _DonePage, _PlanPage, _RunPage, _SurveyPage, _WelcomePage,
    )
    _WelcomePage("http://x:5050")
    _SurveyPage("http://x:5050")
    _PlanPage("http://x:5050")
    _RunPage("http://x:5050", None)
    _DonePage()


def test_quick_start_wizard_mixin_handler_exposed():
    from traffic_client.dpdk_menu_actions import (
        TrafficGenClientDPDKMenuActions,
    )
    assert hasattr(
        TrafficGenClientDPDKMenuActions, "show_dpdk_quick_start_wizard",
    )


def test_main_py_wires_quick_start_wizard_menu():
    """Quick Start Wizard must still be reachable (it's a useful
    alternative UI for the same engine) but v0.5.18 demoted it from
    a top-level entry to the Advanced submenu so it doesn't compete
    with ★ Setup DPDK for the operator's first-time attention.

    Test pins: action exists, handler wired, AND appears under the
    Advanced submenu definition (so we don't accidentally put it
    back at top-level)."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "traffic_client" / "main.py"
    ).read_text()
    assert "Quick Start Wizard..." in src
    assert "show_dpdk_quick_start_wizard" in src
    # v0.5.18: Quick Start lives under the Advanced submenu now.
    # The order constraint is: Advanced submenu defined first, then
    # Quick Start added to it.
    pos_advanced = src.find("dpdk_advanced_menu")
    pos_wizard = src.find("Quick Start Wizard...")
    pos_setup = src.find("Setup DPDK")
    pos_blast = src.find("Blast a Flow...")
    assert pos_advanced > 0, (
        "v0.5.18 should have created a dpdk_advanced_menu — Quick "
        "Start Wizard belongs in there, not at the top level."
    )
    assert pos_setup < pos_blast < pos_advanced < pos_wizard, (
        f"Menu order regressed: ★ Setup DPDK ({pos_setup}) → "
        f"Blast ({pos_blast}) → Advanced submenu ({pos_advanced}) → "
        f"Quick Start within Advanced ({pos_wizard}) is required."
    )


# ─────────────────────────────────── driver classifier
#
# v0.3.11 follow-up. The orchestrator must filter out interfaces
# DPDK has no PMD for (lo / bridge / bond / tun / veth / vnet …)
# and distinguish Mellanox bifurcated-mode (mlx4/mlx5) from
# regular vfio-pci-bound NICs. Picker UIs read this classifier
# to render the right badges and skip the bind call for
# bifurcated NICs.


@pytest.mark.parametrize("iface,expected", [
    ({"name": "lo", "driver": ""},                                "virtual"),
    ({"name": "br0", "driver": "bridge"},                         "virtual"),
    ({"name": "br-mgmt", "driver": "bridge"},                     "virtual"),
    ({"name": "bond0", "driver": "bonding"},                      "virtual"),
    ({"name": "vnet0", "driver": ""},                             "virtual"),
    ({"name": "tun0", "driver": "tun"},                           "virtual"),
    ({"name": "tap5", "driver": "tun"},                           "virtual"),
    ({"name": "veth1abc", "driver": "veth"},                      "virtual"),
    ({"name": "wg0", "driver": "wireguard"},                      "virtual"),
    ({"name": "tmfifo_net0", "driver": ""},                       "virtual"),
    ({"name": "docker0", "driver": "bridge"},                     "virtual"),
    ({"name": "vxlan100", "driver": "vxlan"},                     "virtual"),
    ({"name": "ens5", "driver": "vfio-pci"},                      "bound"),
    ({"name": "ens6", "driver": "VFIO-PCI"},                      "bound"),
    ({"name": "enp13s0f0np0", "driver": "mlx5_core"},             "bifurcated"),
    ({"name": "enp13s0f1np1", "driver": "mlx4_core"},             "bifurcated"),
    ({"name": "ens5", "driver": "ixgbe", "vendor_id": "15b3"},    "bifurcated"),
    ({"name": "eno8303", "driver": "ixgbe"},                      "bindable"),
    ({"name": "ens1f0", "driver": "i40e"},                        "bindable"),
    ({"name": "ens1f1", "driver": "ice"},                         "bindable"),
    ({"name": "ens2", "driver": "bnxt_en"},                       "bindable"),
])
def test_classify_iface_driver(iface, expected):
    from utils.dpdk_orchestrator import classify_iface_driver
    assert classify_iface_driver(iface) == expected, (
        f"{iface!r} should classify as {expected!r}"
    )


def test_classify_handles_garbage_input():
    """Defensive — caller may pass None or non-dict from a parser
    that hiccupped. Classifier returns 'bindable' so the picker
    still shows the entry (operator can see it, the bind call
    surfaces any real error)."""
    from utils.dpdk_orchestrator import classify_iface_driver
    assert classify_iface_driver(None) == "bindable"
    assert classify_iface_driver([]) == "bindable"
    assert classify_iface_driver({}) == "bindable"
    assert classify_iface_driver({"name": "", "driver": ""}) == "bindable"


def test_picker_skips_virtual_ifaces():
    """The picker default must skip lo / br / bond / vnet / tun
    even when those are the only non-vfio, non-management options."""
    from utils.dpdk_orchestrator import pick_default_bind_target
    interfaces = [
        {"name": "lo", "driver": ""},
        {"name": "br0", "driver": "bridge"},
        {"name": "vnet5", "driver": ""},
        {"name": "ens5", "driver": "i40e", "link": "up"},
    ]
    assert pick_default_bind_target(interfaces) == "ens5"


def test_picker_returns_none_when_only_virtual_present():
    """A server with only software ifaces (test VM, container) gives
    no bind candidate. Picker returns None — GUI shows 'no bindable
    NIC' rather than letting operator pick lo."""
    from utils.dpdk_orchestrator import pick_default_bind_target
    interfaces = [
        {"name": "lo", "driver": ""},
        {"name": "br0", "driver": "bridge"},
        {"name": "docker0", "driver": "bridge"},
    ]
    assert pick_default_bind_target(interfaces) is None


def test_picker_includes_bifurcated_as_candidate():
    """Mellanox NICs are bindable from the picker's POV — they're
    not skipped like virtual / bound ifaces. The bind step's
    short-circuit for bifurcated drivers happens at execution
    time, not at picker time. Operator can pick a Mellanox NIC
    and the orchestrator handles it transparently."""
    from utils.dpdk_orchestrator import pick_default_bind_target
    interfaces = [
        {"name": "enp13s0f0np0", "driver": "mlx5_core", "link": "up"},
    ]
    assert pick_default_bind_target(interfaces) == "enp13s0f0np0"


def test_is_unbindable_virtual_helper():
    from utils.dpdk_orchestrator import is_unbindable_virtual
    assert is_unbindable_virtual({"name": "lo", "driver": ""})
    assert is_unbindable_virtual({"name": "br0", "driver": "bridge"})
    assert not is_unbindable_virtual({"name": "ens5", "driver": "i40e"})
    assert not is_unbindable_virtual({"name": "ens6", "driver": "mlx5_core"})


def test_augment_with_bind_history_rewrites_bound_iface_name():
    """User-reported: vfio-pci-bound NICs show as
    '0000:22:00.0 · (no interface)' in the picker — the operator
    can't tell which NIC it used to be. Server's
    /api/admin/bind_history records the pre-bind kernel name; the
    helper joins the two so bound rows render as 'eno8303 (DPDK)'.
    """
    from utils.dpdk_orchestrator import augment_with_bind_history
    interfaces = [
        {
            "name": "(no interface)",
            "driver": "vfio-pci",
            "pci": "0000:22:00.0",
        },
        {
            "name": "eno8403",
            "driver": "tg3",
            "pci": "0000:22:00.1",
        },
    ]
    history = {
        "0000:22:00.0": {
            "name": "eno8303",
            "kernel_driver": "tg3",
        },
    }
    out = augment_with_bind_history(interfaces, history)
    # Bound NIC's name gets rewritten with (DPDK) suffix.
    assert out[0]["name"] == "eno8303 (DPDK)"
    # Pre-bind name preserved for callers that need the bare form.
    assert out[0]["_pre_bind_name"] == "eno8303"
    # Previous kernel driver surfaced so the picker can show "was tg3".
    assert out[0]["previous_driver"] == "tg3"
    # Unbound NICs pass through unchanged.
    assert out[1]["name"] == "eno8403"
    assert "_pre_bind_name" not in out[1]


def test_augment_doesnt_mutate_input():
    """Pure function — input list and dicts are not modified."""
    from utils.dpdk_orchestrator import augment_with_bind_history
    interfaces = [
        {"name": "(no interface)", "driver": "vfio-pci", "pci": "0000:01:00.0"},
    ]
    history = {"0000:01:00.0": {"name": "eth0", "kernel_driver": "ixgbe"}}
    augment_with_bind_history(interfaces, history)
    # Original dict untouched.
    assert interfaces[0]["name"] == "(no interface)"
    assert "_pre_bind_name" not in interfaces[0]


def test_augment_handles_missing_history_entry():
    """Bound NIC whose PCI isn't in history (e.g. bound before bind
    history tracking shipped) is left unchanged — caller sees the
    raw '(no interface)' which is the same behaviour as today, no
    regression."""
    from utils.dpdk_orchestrator import augment_with_bind_history
    interfaces = [
        {"name": "(no interface)", "driver": "vfio-pci", "pci": "0000:99:00.0"},
    ]
    out = augment_with_bind_history(interfaces, {})
    assert out[0]["name"] == "(no interface)"


def test_augment_handles_alternative_pci_key_names():
    """Older parsers use `bdf` instead of `pci` — helper accepts
    both to avoid silently failing on legacy server responses."""
    from utils.dpdk_orchestrator import augment_with_bind_history
    interfaces = [
        {"name": "", "driver": "vfio-pci", "bdf": "0000:01:00.0"},
    ]
    history = {"0000:01:00.0": {"name": "eth0", "kernel_driver": "ixgbe"}}
    out = augment_with_bind_history(interfaces, history)
    assert out[0]["name"] == "eth0 (DPDK)"


def test_augment_only_touches_vfio_bound_ifaces():
    """Mellanox bifurcated NICs (mlx5_core, still kernel-attached)
    must NOT have their name rewritten even if the PCI matches a
    history entry — they're not actually 'bound' in the vfio sense."""
    from utils.dpdk_orchestrator import augment_with_bind_history
    interfaces = [
        {"name": "enp13s0f0np0", "driver": "mlx5_core", "pci": "0000:0d:00.0"},
    ]
    history = {"0000:0d:00.0": {"name": "ignored", "kernel_driver": "mlx5_core"}}
    out = augment_with_bind_history(interfaces, history)
    assert out[0]["name"] == "enp13s0f0np0"


def test_is_bifurcated_nic_helper():
    from utils.dpdk_orchestrator import is_bifurcated_nic
    assert is_bifurcated_nic({"name": "ens6", "driver": "mlx5_core"})
    assert is_bifurcated_nic({"name": "ens6", "driver": "mlx4_core"})
    assert not is_bifurcated_nic({"name": "ens5", "driver": "i40e"})
    assert not is_bifurcated_nic({"name": "lo", "driver": ""})
    # Vendor-ID fallback
    assert is_bifurcated_nic({
        "name": "ens5", "driver": "ixgbe", "vendor_id": "15b3",
    })


# ─────────────────────────────────── v0.3.11 polish pass

def test_plan_supports_1gb_hugepages():
    """The orchestrator accepts hugepage_size_kb=1048576 and emits
    page_size='1GB' for when server-side support lands. As of
    v0.3.11 the server only handles '2MB' — the dialog disables
    the 1GB radio to prevent operators from picking it — but the
    orchestrator plumbing is in place for the future upgrade."""
    from utils.dpdk_orchestrator import plan, ActionKind
    a = next(
        ac for ac in plan({}, hugepages=4, hugepage_size_kb=1048576)
        if ac.kind == ActionKind.ALLOCATE_HUGEPAGES
    )
    assert a.body["page_size"] == "1GB"
    assert a.body["num_pages"] == 4
    assert "1GB" in a.label


def test_plan_default_size_is_2mb():
    """Backwards-compat: omitting hugepage_size_kb keeps 2 MB pages."""
    from utils.dpdk_orchestrator import plan, ActionKind
    a = next(
        ac for ac in plan({})
        if ac.kind == ActionKind.ALLOCATE_HUGEPAGES
    )
    assert a.body["page_size"] == "2MB"
    assert "2MB" in a.label


def test_chip_emits_clicked_signal(qapp):
    """v0.3.11 #1: chip click opens Make DPDK Ready via the
    `clicked` signal. Pin its existence + that the cursor is
    pointing-hand so the affordance is visible."""
    from widgets.dpdk_readiness_chip import DpdkReadinessChip
    from PyQt5.QtCore import Qt
    chip = DpdkReadinessChip(lambda: None, poll_interval_ms=0)
    # Signal must exist (pyqtSignal attr).
    assert hasattr(chip, "clicked")
    # Cursor reflects clickability.
    assert chip.cursor().shape() == Qt.PointingHandCursor


def test_chip_tooltip_includes_bound_nic_count(qapp):
    """v0.3.11 #5: when at least one NIC is bound, the tooltip
    surfaces the count so operators know their bound NICs aren't
    missing — they're just invisible from psutil (and the Server
    tree) once vfio-pci owns them."""
    from widgets.dpdk_readiness_chip import classify_dpdk_status
    _, _, tip = classify_dpdk_status({
        "dpdk_installed": True, "tx_worker_exists": True,
        "hugepages_configured": True, "iommu_enabled": True,
        "vfio_pci_loaded": True, "vfio_loaded": True,
        "interfaces": [
            {"name": "eth0", "driver": "vfio-pci"},
            {"name": "eth1", "driver": "vfio-pci"},
            {"name": "eth2", "driver": "i40e"},
        ],
    })
    assert "Bound NICs" in tip
    assert "2" in tip  # two bound
    # Click affordance hint.
    assert "Click this chip" in tip


def test_chip_tooltip_no_bound_section_when_none(qapp):
    """When zero NICs are bound, skip the bound row entirely — no
    point in showing 'Bound NICs: 0' (would just be noise on a
    fresh server)."""
    from widgets.dpdk_readiness_chip import classify_dpdk_status
    _, _, tip = classify_dpdk_status({
        "dpdk_installed": True, "tx_worker_exists": True,
        "interfaces": [{"name": "eth0", "driver": "i40e"}],
    })
    assert "Bound NICs" not in tip


def test_stream_dialog_has_readiness_banner_helpers():
    """v0.3.11 #2: stream-dialog Use-DPDK section gets a pre-
    validation banner that surfaces the chip's cached state. Helper
    methods must exist for the banner to render + the link to
    invoke Make DPDK Ready."""
    from widgets.stream_dialog import AddStreamDialog
    assert hasattr(AddStreamDialog, "_refresh_dpdk_readiness_banner")
    assert hasattr(AddStreamDialog, "_on_dpdk_make_ready_link")


def test_make_ready_dialog_has_advanced_options(qapp):
    """v0.3.11 #7 + #8: 1 GB hugepage radio + tx_cores override
    in the advanced options group. Defaults preserve prior
    behavior (2 MB, auto cores) so newcomers see no change.

    The 1 GB radio is DISABLED in v0.3.11 because the server's
    /api/dpdk/hugepages handler only accepts "2MB". The radio
    stays in the UI so the affordance is visible; enable when
    server-side support lands. Pin both the existence and the
    disabled-state so a refactor doesn't accidentally re-enable
    an unsupported option.
    """
    from widgets.dpdk_make_ready_dialog import MakeDpdkReadyDialog
    dlg = MakeDpdkReadyDialog("http://stub:5050")
    assert hasattr(dlg, "_hugepage_2mb")
    assert hasattr(dlg, "_hugepage_1gb")
    assert dlg._hugepage_2mb.isChecked()
    assert not dlg._hugepage_1gb.isChecked()
    assert not dlg._hugepage_1gb.isEnabled(), (
        "1 GB radio is enabled but server-side /api/dpdk/hugepages "
        "only supports 2 MB — operator would hit a 400 error"
    )
    assert hasattr(dlg, "_tx_cores")
    assert dlg._tx_cores.value() == 0  # 0 = auto
    assert dlg._tx_cores.specialValueText() == "auto"


def test_make_ready_dialog_has_unbind_button(qapp):
    """v0.3.11 #6: Unbind NIC button lives in the dialog's button
    box, wired to a handler that bridges to the existing
    Tools→DPDK→Unbind menu action."""
    from widgets.dpdk_make_ready_dialog import MakeDpdkReadyDialog
    dlg = MakeDpdkReadyDialog("http://stub:5050")
    assert hasattr(dlg, "_unbind_btn")
    assert hasattr(dlg, "_on_unbind_request")
    assert "Unbind" in dlg._unbind_btn.text()


def test_install_script_distro_check_present():
    """v0.3.11 #3: install_dpdk.sh exits early on non-apt distros
    with a clear message + RHEL/Fedora package-name hints, instead
    of failing deep in step 4 with 'apt-get not found'."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "resources" / "dpdk" / "install_dpdk.sh"
    ).read_text()
    assert "check_supported_distro" in src
    # Must be wired into the pre-flight step, not just defined.
    assert src.count("check_supported_distro") >= 2, (
        "check_supported_distro defined but not invoked from "
        "step_preflight"
    )
    # Helpful manual-install hint for RHEL operators.
    assert "dpdk-devel" in src, (
        "distro-mismatch message lost the RHEL package-name hints"
    )


def test_wizard_welcome_explains_choices(qapp):
    """v0.3.11 #9: first-timer help — the wizard's Welcome page
    now explains when to use Wizard vs Make Ready vs Blast a Flow,
    so newcomers don't have to guess from three menu items."""
    from widgets.dpdk_quick_start_wizard import _WelcomePage
    page = _WelcomePage("http://stub:5050")
    # The body QLabel is the only widget on the page; grab its text.
    from PyQt5.QtWidgets import QLabel
    body = page.findChild(QLabel)
    assert body is not None
    text = body.text()
    assert "When to use which entry point" in text
    assert "Make DPDK Ready" in text
    assert "Blast a Flow" in text


def test_shared_single_server_resolver_present():
    """The three menu handlers share `_resolve_single_server_for_dpdk`
    to avoid duplicating the 'must select exactly one server' guard.
    Pin its existence so a refactor that re-inlines the logic three
    times doesn't slip past review."""
    from traffic_client.dpdk_menu_actions import (
        TrafficGenClientDPDKMenuActions,
    )
    assert hasattr(
        TrafficGenClientDPDKMenuActions,
        "_resolve_single_server_for_dpdk",
    )
