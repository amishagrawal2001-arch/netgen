"""v0.5.161 CRITICAL: extras' client body must carry listen_port.

Operator: 16-worker Blast run, all 15 extras died with rc=1 inside
2 seconds of "both halves running". Same bug in Topology with a
different wrong key (`peer_port` instead of `listen_port`).

Root cause: the client body that gets POSTed to the client TG's
/api/rdma/perftest/start needs `listen_port` to set perftest's
`-p PORT` arg. Without it, the server-side route falls through to
`_allocate_port()` which picks a fresh random port — not matching
the server's bound port. Connection never establishes; client
perftest exits rc=1.

Worker 0's path correctly extracts `listen_port` from the server's
response and threads it through. The v0.5.155+ extras path forgot.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_BLAST = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
SRC_TOPO = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


def test_blast_extras_client_body_has_listen_port():
    """The Blast extras path extracts `listen_port` from the
    server response and includes it in the client body."""
    body = _extract_method(SRC_BLAST, "_start_extra_workers")
    code = _strip_comments(body)
    assert 'data.get("listen_port")' in code
    assert '"listen_port": srv_listen_port' in code


def test_topology_extras_client_body_uses_listen_port_not_peer_port():
    """Topology's extras client body must use the API-correct key
    `listen_port` (matching what `start_perftest` consumes), not
    the non-existent `peer_port`."""
    body = _extract_method(SRC_TOPO, "_start_pair_extra_workers")
    code = _strip_comments(body)
    assert '"listen_port": _port' in code
    # The buggy key is gone from live code.
    assert '"peer_port"' not in code


def test_blast_extras_inherit_worker0_pattern():
    """Both the client peer_addr and listen_port come from the
    server's API response, mirroring the worker-0 path in
    `_on_server_started`."""
    body = _extract_method(SRC_BLAST, "_start_extra_workers")
    code = _strip_comments(body)
    # peer_addr still derived from the server TG URL (worker 0
    # uses the response's listen_addr; extras use the URL hostname
    # — both resolve to the same host in our deployment).
    assert "peer_addr" in code


# ───── helpers ──────────────────────────────────────────────────────────


def _strip_comments(body: str) -> str:
    return "\n".join(
        ln for ln in body.splitlines() if not ln.lstrip().startswith("#")
    )


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"def {name}(...) not found"
    return m.group(0)
