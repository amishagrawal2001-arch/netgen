"""DPDK remediation orchestrator — pure-logic core that the
'Make DPDK Ready' / 'Quick Start Wizard' / 'Blast a Flow' GUI flows
all share (v0.3.11).

Why this module exists: pre-v0.3.11 the user had to chain ~9 menu
clicks in the right order to go from a fresh server to a stream
running on DPDK: install → IOMMU → VFIO modules → hugepages →
interface bind → device → stream → start. Each step exposed its
own dialog and failure mode. This module collapses the recipe into
a single declarative pipeline.

The orchestrator is **pure-logic** — it owns the decision tree
("given this status, what's the next action?") but knows nothing
about Qt. The GUI shells wrap it with QProgressDialog / QWizard
and translate ``Action`` instances into HTTP calls to the existing
``/api/dpdk/*`` and ``/api/admin/install_dpdk`` endpoints.

Design rules:

  * Pure data — dataclasses, no requests. The HTTP layer is the
    caller's. This file unit-tests cleanly without network or Qt.
  * Idempotent — every action is safe to skip if already satisfied.
    The orchestrator filters out already-satisfied actions before
    handing the plan to the GUI.
  * Reboot-aware — IOMMU enabling needs a kernel cmdline change +
    reboot. ``Action.needs_reboot=True`` signals the GUI to pause
    and prompt the operator.
  * Single chokepoint for defaults — hugepage count, IOMMU mode,
    NIC selection heuristics live here, not scattered across the
    GUI files. One place to tune.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ─────────────────────────────────── defaults

# Default hugepage allocation: 1024 × 2 MB = 2 GB. Enough for a
# single tx_worker driving a 10 G NIC at line-rate (Mellanox guides
# recommend ~512 MB hugepage memory per port; 2 GB covers four
# ports with room for the DPDK rings + mbuf pool). Smaller hosts
# (<8 GB RAM) get a downgrade — see `recommended_hugepages()`.
DEFAULT_HUGEPAGES_2MB = 1024

# IOMMU defaults — passthrough mode (`iommu=pt`) is what vfio-pci
# wants for NICs we control directly. Without `pt` the IOMMU
# walks every DMA which costs throughput.
DEFAULT_IOMMU_MODE = "pt"


# ─────────────────────────────────── action model

class ActionKind(str, Enum):
    """The set of remediations the orchestrator can prescribe.

    String-valued so it serializes cleanly into logs / progress
    dialogs without a custom encoder.
    """
    INSTALL_DPDK = "install_dpdk"
    ENABLE_IOMMU = "enable_iommu"
    LOAD_VFIO = "load_vfio"
    ALLOCATE_HUGEPAGES = "allocate_hugepages"
    BIND_INTERFACE = "bind_interface"


@dataclass
class Action:
    """One remediation step.

    The GUI uses ``label`` for the progress text, ``kind`` to dispatch
    to the right HTTP endpoint, ``body`` as the POST payload, and
    ``needs_reboot`` to decide whether to pause after running.
    """
    kind: ActionKind
    label: str
    endpoint: str            # relative URL e.g. "/api/dpdk/hugepages"
    method: str = "POST"
    body: Dict[str, Any] = field(default_factory=dict)
    needs_reboot: bool = False
    # The status flag(s) this action satisfies. After running, the
    # GUI re-fetches /api/dpdk/status and re-runs `plan()`; this lets
    # us verify the action actually achieved its goal instead of
    # trusting the HTTP 200.
    satisfies_keys: List[str] = field(default_factory=list)
    # v0.5.18: human-readable duration estimate shown in the wizard so
    # operators know what they're getting into. Example values:
    # "5-10 min" (DPDK build), "<1 sec" (modprobe), "few sec + REBOOT"
    # (IOMMU). Empty string = no estimate displayed.
    eta: str = ""


# ─────────────────────────────────── decision tree

def recommended_hugepages(total_ram_bytes: Optional[int] = None) -> int:
    """Pick a sensible hugepage count.

    Default is 1024 × 2 MB (=2 GB) — enough for line-rate on a few
    10 G NICs. Downgrade to 512 (=1 GB) on hosts with <8 GB RAM
    visible to avoid OOMing the rest of the system. The caller can
    pass `total_ram_bytes` from /proc/meminfo via the server-side
    `/api/dpdk/recommend` endpoint; when None, returns the default.
    """
    if total_ram_bytes is not None and total_ram_bytes < (8 * 1024**3):
        return 512
    return DEFAULT_HUGEPAGES_2MB


def is_dpdk_ready(status: Dict[str, Any]) -> bool:
    """True iff ``/api/dpdk/status`` reports that every prerequisite
    is satisfied AND at least one NIC is bound. The 'at least one
    bound NIC' is the gate that lets a stream actually transmit;
    everything else is necessary-but-not-sufficient.

    Used by the orchestrator UIs to short-circuit the wizard when
    the operator clicks 'Make Ready' on a server that's already
    set up. Returns immediately with a 'You're good to go' message
    instead of opening an empty progress dialog.
    """
    if not status:
        return False
    must_be_true = [
        "dpdk_installed",
        "tx_worker_exists",
        "hugepages_configured",
        "iommu_enabled",
        "vfio_pci_loaded",
        "vfio_loaded",
    ]
    for key in must_be_true:
        if not status.get(key):
            return False
    # At least one interface bound to vfio-pci. The status payload's
    # interfaces list is whatever the server's dpdk-devbind parser
    # returned — each entry's `driver` field tells us the binding.
    interfaces = status.get("interfaces") or []
    bound = [
        i for i in interfaces
        if isinstance(i, dict)
        and "vfio" in str(i.get("driver", "")).lower()
    ]
    return len(bound) > 0


def plan(
    status: Dict[str, Any],
    *,
    hugepages: Optional[int] = None,
    hugepage_size_kb: Optional[int] = None,
    iommu_mode: str = DEFAULT_IOMMU_MODE,
    cpu_vendor: Optional[str] = None,
) -> List[Action]:
    """Return the ordered list of actions needed to make DPDK ready.

    Order matters: install must complete before binding can work;
    IOMMU before VFIO module load (else `vfio-pci` falls back to
    `noiommu` mode which is dangerous); hugepages before the first
    `tx_worker` invocation; bind LAST so the operator can pick a
    NIC after everything else is in place.

    The function filters out already-satisfied steps based on
    ``status`` — for a server that already has DPDK installed and
    IOMMU on, only the missing steps make it into the returned
    plan. Bind is intentionally always included as a placeholder
    even when the user has bound NICs, because the GUI flow lets
    the operator add another bind; the bind dialog itself decides
    whether to no-op.

    :param status: parsed JSON from ``GET /api/dpdk/status``.
    :param hugepages: override the default 2 MB hugepage count.
    :param iommu_mode: "pt" (passthrough, default) or "" (full).
    :param cpu_vendor: "intel" / "amd" — server discovers this via
        ``/api/dpdk/cpu-vendor``. When None, the IOMMU action's
        endpoint payload omits the vendor and lets the server pick.
    """
    if status is None:
        status = {}
    out: List[Action] = []

    # 1. Install DPDK runtime + tx_worker. The install_dpdk.sh
    # script handles both — and runs apt-get if libdpdk-dev is
    # missing. ~5-10 min on a fresh Ubuntu host.
    if not (status.get("dpdk_installed") and status.get("tx_worker_exists")):
        out.append(Action(
            kind=ActionKind.INSTALL_DPDK,
            label="Install DPDK runtime + build tx_worker",
            endpoint="/api/admin/install_dpdk",
            method="POST",
            body={
                # install_dpdk.sh --auto skips the interactive prompts.
                "auto": True,
            },
            needs_reboot=False,
            satisfies_keys=["dpdk_installed", "tx_worker_exists"],
            eta="5-10 min (apt + DPDK clone + meson + ninja)",
        ))

    # 2. IOMMU. Required for vfio-pci to attach NICs. Touches the
    # kernel cmdline (update-grub) — REQUIRES REBOOT to take effect.
    # The orchestrator pauses here and the GUI prompts the operator.
    if not status.get("iommu_enabled"):
        iommu_body: Dict[str, Any] = {
            "enable": True,
            "mode": iommu_mode,
        }
        if cpu_vendor:
            iommu_body["cpu_vendor"] = cpu_vendor
        out.append(Action(
            kind=ActionKind.ENABLE_IOMMU,
            label=(
                f"Enable IOMMU in kernel cmdline "
                f"(mode={iommu_mode or 'full'}) — reboot required"
            ),
            endpoint="/api/dpdk/iommu",
            method="POST",
            body=iommu_body,
            needs_reboot=True,
            satisfies_keys=["iommu_enabled"],
            eta="<5 sec + REBOOT (~1-2 min for host to come back)",
        ))

    # 3. Load vfio + vfio-pci modules. modprobe-based; takes effect
    # immediately. No-op if the modules are builtin (some custom
    # kernels), but the server-side handler is idempotent.
    if not (status.get("vfio_loaded") and status.get("vfio_pci_loaded")):
        out.append(Action(
            kind=ActionKind.LOAD_VFIO,
            label="Load vfio + vfio-pci kernel modules",
            endpoint="/api/dpdk/load_modules",
            method="POST",
            body={},
            needs_reboot=False,
            satisfies_keys=["vfio_loaded", "vfio_pci_loaded"],
            eta="<1 sec (modprobe)",
        ))

    # 4. Allocate hugepages. /sys/kernel/mm/hugepages tunable —
    # takes effect immediately, persists until reboot (the install
    # script also wires it into /etc/sysctl.d for persistence).
    #
    # v0.3.11: payload shape MUST match the server's existing
    # /api/dpdk/hugepages handler: `num_pages` (int) +
    # `page_size` (string: "2MB" or "1GB"). The Configure
    # Hugepages dialog has used this contract since v0.2.x; the
    # orchestrator now matches it. Sending `count`/`size_kb`
    # made the server return {"error": "num_pages required"}
    # and the step always failed.
    #
    # NOTE: as of v0.3.11 the server only supports "2MB" — the
    # 1GB path returns 400 ("Unsupported page size"). The dialog
    # disables the 1GB radio with a tooltip until server-side
    # support lands. We still accept hugepage_size_kb=1048576
    # here so when the server is upgraded, no client change is
    # needed.
    hp_size_kb = (hugepage_size_kb if hugepage_size_kb else 2048)
    page_size_str = "1GB" if hp_size_kb == 1048576 else "2MB"
    hp_count = hugepages or recommended_hugepages()
    if not status.get("hugepages_configured"):
        out.append(Action(
            kind=ActionKind.ALLOCATE_HUGEPAGES,
            label=(
                f"Allocate {hp_count} × {page_size_str} hugepages"
            ),
            endpoint="/api/dpdk/hugepages",
            method="POST",
            body={
                "num_pages": hp_count,
                "page_size": page_size_str,
            },
            needs_reboot=False,
            satisfies_keys=["hugepages_configured"],
            eta="<2 sec (write /sys + persist to /etc/sysctl.d)",
        ))

    # 5. Bind interface. ALWAYS included — even if there are bound
    # NICs already, the GUI may want to bind another one. The bind
    # step is special: the orchestrator emits it as a placeholder
    # (no body fields filled in) and the GUI pops a NIC picker
    # dialog to populate `body["interface"]` before running. We
    # don't mark it `satisfies_keys=["interfaces"]` because there's
    # no single status flag for "at least one NIC bound" — the
    # GUI's post-run re-poll computes that from the interfaces list.
    out.append(Action(
        kind=ActionKind.BIND_INTERFACE,
        label="Bind a NIC to vfio-pci (GUI prompts for which one)",
        endpoint="/api/dpdk/bind",
        method="POST",
        body={},  # GUI fills in "interface" before running
        needs_reboot=False,
        satisfies_keys=[],
        eta="<5 sec (per NIC) + GUI picker time",
    ))

    return out


# ─────────────────────────────────── driver classification

# Software/virtual interfaces that DPDK can't bind. Two signals:
# (1) driver name in the kernel module set below; (2) interface
# name prefix (some virtual ifaces don't report a driver string in
# /sys, or report an empty one). Either match → exclude from bind
# candidates.
#
# The list is conservative — only include names/drivers we're
# certain are unbindable. Real NIC drivers like `igb`, `bnxt`,
# `i40e` are NOT here; they go through the standard vfio-pci
# pathway.
_VIRTUAL_DRIVERS = frozenset({
    "bridge", "bonding", "tun", "veth", "dummy", "gre", "vxlan",
    "wireguard", "openvswitch", "macvlan", "macvtap", "ipvlan",
    "team", "vrf",
})
_VIRTUAL_NAME_PREFIXES = (
    "lo",         # loopback
    "br",         # linux bridge (br0, br-mgmt, …)
    "bond",       # link-aggregation
    "vnet",       # libvirt VM nics on the host
    "tun",        # tun
    "tap",        # tap
    "veth",       # container peer
    "dummy",      # dummy iface
    "gre",        # gre tunnel
    "wg",         # wireguard
    "vxlan",      # vxlan iface
    "ovs-",       # openvswitch
    "tmfifo",     # bluefield management interconnect
    "docker",     # docker0
    "virbr",      # libvirt bridge
)

# Mellanox / NVIDIA bifurcated drivers — DPDK's mlx4/mlx5 PMD runs
# alongside the kernel driver via RDMA verbs. NO vfio-pci bind
# needed. The dpdk_start.sh script already skips the rebind for
# these; this constant is the GUI's mirror of that policy.
_BIFURCATED_DRIVERS = frozenset({
    "mlx4_core", "mlx5_core",
})
# PCI vendor ID for Mellanox/NVIDIA — fallback signal when the
# server's interfaces payload reports the vendor but not the
# driver name (older /api/dpdk/interfaces parsers did this).
_BIFURCATED_VENDOR_IDS = frozenset({"15b3"})


def _bare(name: str) -> str:
    return (name or "").strip().lstrip("• ").strip()


def classify_iface_driver(iface: Dict[str, Any]) -> str:
    """Return one of: 'virtual' / 'bifurcated' / 'bindable' / 'bound'.

    The GUI uses this to render the NIC picker:
      * 'virtual'    — hide from the picker (can't bind).
      * 'bifurcated' — show with "no bind needed" badge; bind step
                       is a no-op for these.
      * 'bound'      — show with "ALREADY BOUND" badge; ditto.
      * 'bindable'   — normal candidate, will be POSTed to bind.

    Defensive on missing/malformed input — returns 'bindable' when
    we can't tell, so the operator at least sees the NIC. The bind
    call itself will surface any error from the server.
    """
    if not isinstance(iface, dict):
        return "bindable"
    driver = str(iface.get("driver") or "").strip().lower()
    name = _bare(iface.get("name") or iface.get("interface") or "")
    vendor = str(
        iface.get("vendor_id") or iface.get("vendor") or "",
    ).strip().lower().lstrip("0x")

    # Already bound to vfio-pci — terminal state, don't classify
    # as bindable.
    if "vfio" in driver:
        return "bound"

    # Bifurcated Mellanox/NVIDIA — by driver OR vendor ID.
    if driver in _BIFURCATED_DRIVERS:
        return "bifurcated"
    if vendor and vendor in _BIFURCATED_VENDOR_IDS:
        return "bifurcated"

    # Virtual / software ifaces — by driver OR name prefix. The name
    # check is a safety net for `lo` / `bridge` style ifaces whose
    # driver field is empty in some parser outputs.
    if driver in _VIRTUAL_DRIVERS:
        return "virtual"
    if name in ("lo", ):
        return "virtual"
    for prefix in _VIRTUAL_NAME_PREFIXES:
        if name.startswith(prefix) and not name.startswith(
            ("brcm", "broad", "bond_master_doesnt_exist"),
        ):
            # Stop short-prefix collisions with real drivers.
            # 'br' would match 'broadcom-style' names if any
            # ever existed; we whitelist known prefixes above to
            # avoid that bite.
            return "virtual"

    return "bindable"


def is_unbindable_virtual(iface: Dict[str, Any]) -> bool:
    return classify_iface_driver(iface) == "virtual"


def is_bifurcated_nic(iface: Dict[str, Any]) -> bool:
    return classify_iface_driver(iface) == "bifurcated"


# ─────────────────────────────────── bind-history merge

def augment_with_bind_history(
    interfaces: List[Dict[str, Any]],
    history: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Decorate vfio-pci-bound interfaces with their pre-bind kernel
    name. Returns a new list — input is not mutated.

    Why: ``/api/dpdk/interfaces`` reports vfio-bound NICs with no
    iface name (the kernel forgets the name once the driver detaches),
    so the picker shows ``0000:22:00.0 · (no interface) · vfio-pci``
    which is correct but useless — the operator can't tell which NIC
    that BDF used to be. ``/api/admin/bind_history`` records the
    pre-bind name on every bind; this function joins the two so
    bound rows render as ``eno8303 (DPDK) · was tg3 · …``.

    The server's admin portal at ``/admin`` does this same join client-
    side in JS; this helper mirrors that logic for the Qt picker.

    :param interfaces: list of iface dicts from ``/api/dpdk/interfaces``.
    :param history: ``{pci_bdf: {"name": "eno8303", "kernel_driver":
        "tg3", "bound_at": "…"}}`` from ``/api/admin/bind_history``.
    :returns: new list with `name` rewritten for bound NICs that have
        a history entry. Also sets ``previous_driver`` so the picker
        can show "was tg3" alongside the current "vfio-pci".
    """
    if not history:
        return list(interfaces or [])
    out: List[Dict[str, Any]] = []
    for i in interfaces or []:
        if not isinstance(i, dict):
            out.append(i)
            continue
        driver_l = str(i.get("driver", "")).lower()
        is_bound = "vfio" in driver_l
        # PCI BDF lives under several possible keys depending on the
        # parser version. dpdk-devbind.py output uses "0000:bb:dd.f";
        # check both `pci` (newer) and `bdf` (older).
        pci = (i.get("pci") or i.get("bdf") or "").strip()
        if not (is_bound and pci and pci in history):
            out.append(i)
            continue
        entry = history.get(pci) or {}
        pre_name = (entry.get("name") or "").strip()
        if not pre_name:
            out.append(i)
            continue
        copy = dict(i)
        # Annotate the name so the operator sees both the recognised
        # iface and the "(DPDK)" hint that it's no longer kernel-
        # owned. Keep the raw `name` field too — some callers (e.g.
        # the bind-step short-circuit for re-binding) need the
        # canonical no-DPDK form.
        copy["name"] = f"{pre_name} (DPDK)"
        copy["_pre_bind_name"] = pre_name
        # Surface the previous kernel driver so the picker can show
        # "was tg3" — useful for telling identical-looking NICs apart.
        prev_drv = entry.get("kernel_driver") or ""
        if prev_drv and not copy.get("previous_driver"):
            copy["previous_driver"] = prev_drv
        out.append(copy)
    return out


# ─────────────────────────────────── NIC picker heuristic

def pick_default_bind_target(
    interfaces: List[Dict[str, Any]],
    *,
    management_iface: Optional[str] = None,
) -> Optional[str]:
    """From the server's interface list, suggest a NIC to bind.

    Rules (most specific first):

      1. Already-vfio-bound NICs are SKIPPED — already done.
      2. Virtual / software ifaces are SKIPPED — `lo`, `br*`,
         `bond*`, `vnet*`, `tun*`, `tap*`, `veth*`, etc. Binding
         them is meaningless (no PMD).
      3. NICs that match `management_iface` are SKIPPED — binding
         it would disconnect the client.
      4. Bifurcated NICs (Mellanox mlx4/mlx5) get a special role:
         they're returned as the preferred default IF nothing else
         scores higher, but the GUI knows they don't need an actual
         bind call.
      5. Of what's left, pick the first NIC whose ``link`` is "up"
         and that has no IP assigned (typical DPDK target shape).
      6. Falls back to first non-virtual non-management entry.
      7. Returns None if no candidate exists.
    """
    if not interfaces:
        return None

    mgmt = _bare(management_iface or "")

    candidates = []
    for i in interfaces:
        if not isinstance(i, dict):
            continue
        name = _bare(i.get("name") or i.get("interface") or "")
        if not name:
            continue
        # Rule 1: skip already-bound.
        cls = classify_iface_driver(i)
        if cls == "bound":
            continue
        # Rule 2: skip virtual.
        if cls == "virtual":
            continue
        # Rule 3: skip management.
        if mgmt and name == mgmt:
            continue
        candidates.append(i)

    if not candidates:
        return None

    # Rule 5: prefer up + no-IP.
    for i in candidates:
        link_up = str(i.get("link", "")).lower() == "up"
        has_ip = bool(i.get("ipv4") or i.get("ipv6") or i.get("address"))
        if link_up and not has_ip:
            return _bare(i.get("name") or i.get("interface") or "")

    # Rule 6: fallback.
    first = candidates[0]
    return _bare(first.get("name") or first.get("interface") or "")
