"""RDMA topology helpers — expand a spec into N×M perftest pairs.

Operator-facing model (matches Ixia's IxNetwork Topology + Traffic Item
semantics; see Help → Install Guide §10d for the comparison):

    RdmaTopologyEndpoint  — one (tg_url, device, ib_port, gid_index) tuple
    RdmaTopologySpec      — server-side endpoints + client-side endpoints
                            + shape + shared workload opts
    expand_pairs(spec)    — pure function returning the list of perftest
                            pairs the dialog should spawn

Pure functions only — this module imports neither paramiko nor
PyQt5. Tested headless. The actual REST calls that turn each pair
into two perftest processes live in widgets/rdma_topology_dialog.py.

Topology shapes
---------------

Given S = [s0, s1, ...] server endpoints and C = [c0, c1, ...]
client endpoints:

  SINGLE   — exactly 1 server + 1 client → 1 pair (s0, c0). Errors
             if more than one endpoint on either side. This is the
             same shape the v0.3.x Blast RDMA dialog supported.

  FAN_IN   — 1 server, N clients → N pairs: (s0, c0), (s0, c1), ...
             Stress test: "can one box receive from many".

  FAN_OUT  — N servers, 1 client → N pairs: (s0, c0), (s1, c0), ...
             Stress test: "can one box send to many" (each server
             endpoint will listen; the client endpoint will run N
             client-side perftest processes, one per server).

  MESH     — N servers, M clients → N*M pairs: every server paired
             with every client. The "full cross-product" mode.

  PAIRWISE — N servers, N clients (must be equal length) → N pairs
             at the same index: (s0, c0), (s1, c1), ...
             Tests parallel link saturation without cross-traffic
             between pairs.

Per-pair allocation
-------------------

Each pair gets:
  - a unique handshake_id (UUID v4) — same one passed to both sides
    so rdma_handshake.py can match them
  - a unique listen_port on the SERVER endpoint's TG — caller picks
    base_port (default 18516, perftest's own default + 1) and we
    increment per server endpoint. Same server endpoint participating
    in K pairs (FAN_IN case) gets K different ports so the K listeners
    don't collide on the wire.

Aggregation
-----------

aggregate_stats(per_pair_jobs) sums BW + msg_rate across active
pairs, returning a single dict that mirrors a single-job response
plus a ``pair_count`` field. Latency tests aggregate as
weighted averages across pairs.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# Topology shape sentinels — string constants so they serialize cleanly
# over the REST boundary (operator can drive Topology Mode via curl).
SHAPE_SINGLE = "single"
SHAPE_FAN_IN = "fan_in"
SHAPE_FAN_OUT = "fan_out"
SHAPE_MESH = "mesh"
SHAPE_PAIRWISE = "pairwise"

ALL_SHAPES = (
    SHAPE_SINGLE, SHAPE_FAN_IN, SHAPE_FAN_OUT, SHAPE_MESH, SHAPE_PAIRWISE,
)

# Default base listen_port — perftest's own default is 18515 for the
# control channel; we start one above so a topology run doesn't
# collide with an ad-hoc single-pair Blast RDMA dialog the operator
# might have left running. Each pair adds its index to this base.
DEFAULT_BASE_LISTEN_PORT = 18516


@dataclass(frozen=True)
class RdmaTopologyEndpoint:
    """One physical RDMA test endpoint — the unit operators add to a group.

    tg_url is the netgen TG REST base (e.g. http://srv01:5050).
    device + ib_port + gid_index identify the HCA port on that TG.
    label is a display-only string for the stats grid; if omitted,
    the dialog renders ``<host>.<device>`` from the tg_url.
    """
    tg_url: str
    device: str
    ib_port: int = 1
    gid_index: int = 3
    label: Optional[str] = None

    def display(self) -> str:
        if self.label:
            return self.label
        # Trim scheme/port for compactness.
        host = self.tg_url.replace("http://", "").replace("https://", "")
        host = host.split(":")[0]
        return f"{host}.{self.device}"


@dataclass
class RdmaTopologySpec:
    """Full topology test spec — what the dialog hands to expand_pairs().

    shape is one of the SHAPE_* constants.
    server_endpoints / client_endpoints are RdmaTopologyEndpoint lists.
    test is the perftest test type ("send_bw", "write_bw", "read_bw",
    or their _lat variants).
    workload_opts is the shared params dict (msg_size, qp_count, etc.).
    base_listen_port lets the caller offset to avoid collisions with
    other running tests.
    """
    shape: str
    server_endpoints: List[RdmaTopologyEndpoint]
    client_endpoints: List[RdmaTopologyEndpoint]
    test: str
    workload_opts: Dict[str, Any] = field(default_factory=dict)
    base_listen_port: int = DEFAULT_BASE_LISTEN_PORT


@dataclass(frozen=True)
class RdmaPairPlan:
    """One concrete server-client pair after spec expansion.

    handshake_id pairs the two perftest processes; the dialog passes
    the same id to both /api/rdma/perftest/start calls.

    listen_port is the TCP port the server-side perftest will bind;
    the client side passes peer_addr = server endpoint host + this
    port via the handshake.
    """
    handshake_id: str
    listen_port: int
    server: RdmaTopologyEndpoint
    client: RdmaTopologyEndpoint
    pair_index: int  # 0-based ordinal within the topology


# ─────────────────────────────────── validation ────────────────────────


def validate_spec(spec: RdmaTopologySpec) -> Optional[str]:
    """Return an error message if the spec is invalid, else None.

    Catches obvious operator mistakes before any REST call fires.
    """
    if spec.shape not in ALL_SHAPES:
        return (f"unknown topology shape {spec.shape!r} "
                f"(want one of {', '.join(ALL_SHAPES)})")
    if not spec.server_endpoints:
        return "at least one server endpoint required"
    if not spec.client_endpoints:
        return "at least one client endpoint required"
    if not spec.test:
        return "test type required (send_bw / write_bw / read_bw / *_lat)"

    s, c = len(spec.server_endpoints), len(spec.client_endpoints)
    if spec.shape == SHAPE_SINGLE and (s != 1 or c != 1):
        return (f"shape=single requires exactly 1 server + 1 client; "
                f"got {s} server, {c} client")
    if spec.shape == SHAPE_FAN_IN and s != 1:
        return f"shape=fan_in requires exactly 1 server endpoint; got {s}"
    if spec.shape == SHAPE_FAN_OUT and c != 1:
        return f"shape=fan_out requires exactly 1 client endpoint; got {c}"
    if spec.shape == SHAPE_PAIRWISE and s != c:
        return (f"shape=pairwise requires equal server + client counts; "
                f"got {s} server, {c} client")

    if spec.base_listen_port < 1024 or spec.base_listen_port > 65000:
        return (f"base_listen_port {spec.base_listen_port} out of "
                f"sensible range (1024-65000)")

    return None


# ─────────────────────────────────── expansion ─────────────────────────


def expand_pairs(spec: RdmaTopologySpec) -> List[RdmaPairPlan]:
    """Expand a topology spec into the concrete list of perftest
    pairs the dialog must spawn. Raises ValueError on invalid spec
    (call validate_spec first to get a friendlier message).

    Pair indexing — pair_index is the natural enumeration order,
    used by the stats grid to label rows. Each pair has a unique
    handshake_id and unique listen_port.

    Listen-port allocation: pairs that share the SAME server endpoint
    (FAN_IN, MESH) need DIFFERENT listen_ports because the same TG
    host can't bind two perftests to the same port. We allocate by
    pair_index (base + index) which guarantees uniqueness across the
    whole topology, not just per-server-endpoint.
    """
    err = validate_spec(spec)
    if err:
        raise ValueError(err)

    plans: List[RdmaPairPlan] = []
    pair_index = 0

    def _emit(server: RdmaTopologyEndpoint, client: RdmaTopologyEndpoint) -> None:
        nonlocal pair_index
        plans.append(RdmaPairPlan(
            handshake_id=str(uuid.uuid4()),
            listen_port=spec.base_listen_port + pair_index,
            server=server,
            client=client,
            pair_index=pair_index,
        ))
        pair_index += 1

    if spec.shape == SHAPE_SINGLE:
        _emit(spec.server_endpoints[0], spec.client_endpoints[0])

    elif spec.shape == SHAPE_FAN_IN:
        # 1 server, N clients
        s0 = spec.server_endpoints[0]
        for c in spec.client_endpoints:
            _emit(s0, c)

    elif spec.shape == SHAPE_FAN_OUT:
        # N servers, 1 client
        c0 = spec.client_endpoints[0]
        for s in spec.server_endpoints:
            _emit(s, c0)

    elif spec.shape == SHAPE_MESH:
        # Full cross-product
        for s in spec.server_endpoints:
            for c in spec.client_endpoints:
                _emit(s, c)

    elif spec.shape == SHAPE_PAIRWISE:
        # Equal-length parallel pairing (validated above)
        for s, c in zip(spec.server_endpoints, spec.client_endpoints):
            _emit(s, c)

    else:  # pragma: no cover — validated above
        raise ValueError(f"unhandled shape {spec.shape!r}")

    return plans


# ─────────────────────────────────── REST payload builders ────────────


def server_start_payload(plan: RdmaPairPlan, test: str,
                         workload_opts: Dict[str, Any]) -> Dict[str, Any]:
    """Build the JSON body for POST /api/rdma/perftest/start on the
    server-side TG. Mirrors what RdmaBlastFlowDialog._on_start_clicked
    passes for the single-pair case."""
    body: Dict[str, Any] = {
        "role": "server",
        "test": test,
        "device": plan.server.device,
        "ib_port": int(plan.server.ib_port),
        "gid_index": int(plan.server.gid_index),
        "handshake_id": plan.handshake_id,
        "listen_port": int(plan.listen_port),
    }
    body.update(workload_opts)
    return body


def client_start_payload(plan: RdmaPairPlan, test: str,
                         workload_opts: Dict[str, Any],
                         peer_addr: str) -> Dict[str, Any]:
    """Build the JSON body for POST /api/rdma/perftest/start on the
    client-side TG. peer_addr is the server TG host (resolved by the
    handshake exchange the dialog drives)."""
    body: Dict[str, Any] = {
        "role": "client",
        "test": test,
        "device": plan.client.device,
        "ib_port": int(plan.client.ib_port),
        "gid_index": int(plan.client.gid_index),
        "handshake_id": plan.handshake_id,
        "listen_port": int(plan.listen_port),
        "peer_addr": peer_addr,
    }
    body.update(workload_opts)
    return body


# ─────────────────────────────────── stats aggregation ─────────────────


def aggregate_stats(jobs: List[Dict[str, Any]],
                    is_lat: bool = False) -> Dict[str, Any]:
    """Roll up per-pair perftest job dicts into a single aggregate
    suitable for the dialog's TOTAL row.

    Inputs are the per-job dicts returned by GET
    /api/rdma/perftest/job/<id> — same shape as PerftestJob.to_public_dict
    (running, finished_at, final_bw_avg_gbps, final_msg_rate_mpps,
    final_lat_avg_us, etc.).

    BW + msg-rate aggregate by SUM. Latency aggregates by mean of
    means (a true weighted average needs per-pair iteration counts,
    which we DO have — final_iterations — so we use that).

    Returns:
      {
        "pair_count": int,
        "pairs_running": int,
        "pairs_done": int,
        "pairs_with_data": int,    # how many contributed real numbers
        "total_bw_avg_gbps": float | None,
        "total_msg_rate_mpps": float | None,
        "weighted_lat_avg_us": float | None,
        "any_error": str | None,
      }
    """
    out: Dict[str, Any] = {
        "pair_count": len(jobs),
        "pairs_running": 0,
        "pairs_done": 0,
        "pairs_with_data": 0,
        "total_bw_avg_gbps": None,
        "total_msg_rate_mpps": None,
        "weighted_lat_avg_us": None,
        "any_error": None,
    }
    if not jobs:
        return out

    bw_sum = 0.0
    bw_has = False
    mr_sum = 0.0
    mr_has = False
    lat_weighted_num = 0.0
    lat_weight_denom = 0
    lat_has = False
    for j in jobs:
        if not isinstance(j, dict):
            continue
        if j.get("running"):
            out["pairs_running"] += 1
        if j.get("finished_at") is not None:
            out["pairs_done"] += 1
        if j.get("error") and out["any_error"] is None:
            out["any_error"] = str(j.get("error"))[:160]

        if not is_lat:
            bw = j.get("final_bw_avg_gbps")
            if isinstance(bw, (int, float)):
                bw_sum += float(bw); bw_has = True
            mr = j.get("final_msg_rate_mpps")
            if isinstance(mr, (int, float)):
                mr_sum += float(mr); mr_has = True
            if isinstance(bw, (int, float)) or isinstance(mr, (int, float)):
                out["pairs_with_data"] += 1
        else:
            avg = j.get("final_lat_avg_us")
            iters = j.get("final_iterations") or 0
            if isinstance(avg, (int, float)) and iters > 0:
                lat_weighted_num += float(avg) * int(iters)
                lat_weight_denom += int(iters)
                lat_has = True
                out["pairs_with_data"] += 1

    if bw_has:
        out["total_bw_avg_gbps"] = round(bw_sum, 3)
    if mr_has:
        out["total_msg_rate_mpps"] = round(mr_sum, 6)
    if lat_has and lat_weight_denom > 0:
        out["weighted_lat_avg_us"] = round(
            lat_weighted_num / lat_weight_denom, 3,
        )

    return out
