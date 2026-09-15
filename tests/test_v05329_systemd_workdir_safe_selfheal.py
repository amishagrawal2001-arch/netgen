"""v0.5.329 — self-heal a defensive systemd drop-in that
neutralizes bad WorkingDirectory / RootDirectory carried by ANY
other drop-in.

Operator on srv06 2026-09-15 hit `status=200/CHDIR` in 1ms right
after `pip install --upgrade ... && systemctl restart netgen-
server`. systemd rejected chdir BEFORE any Python ran, so no
in-process self-heal could recover — the operator had to hand-
write a defensive drop-in to get unblocked.

None of the netgen code writes bad WorkingDirectory values, but
operator-created drop-ins (`tx-worker.conf`, `mlx5-rlimits.conf`
per srv06 setup notes) can. This ship writes a `50-netgen-
workdir-safe.conf` on every healthy startup — its `50-` prefix
sorts LATER than any operator-created drop-in, so the empty-
string reset + safe `/` default wins the systemd drop-in merge.

Effective on the NEXT restart. Doesn't rescue an operator who's
CURRENTLY locked out (they must apply the drop-in manually
first), but prevents future recurrences.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


# ---------- Self-heal writer defined ----------


def test_marker_present():
    src = _server_src()
    assert "v0.5.329 (audit systemd-chdir-lockout)" in src


def test_workdir_safe_path_is_50_prefix():
    """50- prefix — sorts AFTER `10-netgen-rlimits.conf`,
    `netgen-caps.conf`, `mlx5-rlimits.conf`, `tx-worker.conf`
    (all sort BEFORE `50-` in lexical order). Systemd applies
    drop-ins in lexical order; last setter of any list-valued
    directive wins the reset."""
    src = _server_src()
    assert "50-netgen-workdir-safe.conf" in src


def test_content_resets_workdir_with_empty_line():
    """Empty-string on the first `WorkingDirectory=` line CLEARS
    the merged list (systemd drop-in semantics), then the second
    sets a safe value. Both lines required."""
    src = _server_src()
    # Match the emitted content only (avoid counting mentions in
    # neighboring comments). The reset line is a bare
    # "WorkingDirectory=\n"; the safe default is "WorkingDirectory=/\n".
    idx = src.index("_NETGEN_WORKDIR_SAFE_CONTENT = ")
    body = src[idx:idx + 1500]
    assert "\nWorkingDirectory=\n" in body
    assert "\nWorkingDirectory=/\n" in body


def test_content_resets_root_directory():
    """Same shape for RootDirectory — empty-string clears any
    stale chroot. No default needed (empty = systemd default)."""
    src = _server_src()
    idx = src.index("_NETGEN_WORKDIR_SAFE_CONTENT")
    body = src[idx:idx + 1500]
    assert "RootDirectory=\n" in body


def test_self_heal_defined():
    src = _server_src()
    assert "def _ensure_netgen_workdir_safe_deployed():" in src


def test_self_heal_bails_when_not_tarball_install():
    """No-op on non-tarball installs (dev checkout, container,
    system Python) — those don't have the /opt/netgen-server
    tarball structure."""
    src = _server_src()
    idx = src.index("def _ensure_netgen_workdir_safe_deployed():")
    body = src[idx:idx + 4000]
    assert 'not os.path.isdir("/opt/netgen-server")' in body


def test_self_heal_is_idempotent():
    """If the file already matches the desired content (sha256),
    skip the write — no `daemon-reload` churn on every startup."""
    src = _server_src()
    idx = src.index("def _ensure_netgen_workdir_safe_deployed():")
    body = src[idx:idx + 4000]
    assert "hashlib.sha256" in body
    assert "already in sync" in body


def test_self_heal_runs_daemon_reload_on_write():
    """After writing the drop-in, run `systemctl daemon-reload`
    so systemd re-reads it — else the merge doesn't take effect
    until the next restart even for a live systemd."""
    src = _server_src()
    idx = src.index("def _ensure_netgen_workdir_safe_deployed():")
    body = src[idx:idx + 4000]
    assert '"systemctl", "daemon-reload"' in body


def _find_call_site(src: str, needle: str) -> int:
    """Find the index of the first CALL site of `needle`, skipping
    references inside string literals / docstrings. Returns the
    index of a line where `needle` sits at column ≥ 4 (indented
    call) with no preceding `#` on that line — good enough
    heuristic for a self-heal-caller sanity check without a full
    AST walk."""
    pos = 0
    while True:
        pos = src.find(needle, pos)
        if pos < 0:
            return -1
        # Find the start of the line.
        line_start = src.rfind("\n", 0, pos) + 1
        line = src[line_start:src.find("\n", pos)]
        stripped = line.lstrip()
        # Skip lines starting with # (comment) or that look like
        # they're inside a triple-quoted string block. Caller
        # sites are typically `    _ensure_...()` at some indent.
        if not stripped.startswith("#") and stripped.startswith(needle):
            return pos
        pos += 1


def test_self_heal_wired_into_startup():
    """The self-heal function is only useful if it's called at
    startup — pin the wiring so a future refactor can't detach it."""
    src = _server_src()
    call_idx = _find_call_site(src, "_ensure_netgen_workdir_safe_deployed()")
    assert call_idx > 0, "self-heal function is defined but never called"
    # And guarded by try/except so a self-heal failure doesn't
    # crash startup.
    ctx = src[max(0, call_idx - 500):call_idx]
    assert "try:" in ctx


def test_self_heal_runs_after_caps_selfheal():
    """Both self-heals write to the same drop-in dir. Order-
    dependency isn't strict but keep the caps self-heal first
    for consistency with the v0.5.56 audit-H8 sequencing."""
    src = _server_src()
    caps_call = _find_call_site(src, "_ensure_netgen_caps_override_deployed()")
    workdir_call = _find_call_site(src, "_ensure_netgen_workdir_safe_deployed()")
    assert caps_call > 0 and workdir_call > 0
    # workdir_safe call must come AFTER caps call.
    assert caps_call < workdir_call


# ---------- AST parse (v0.5.300 lesson) ----------


def test_server_ast_parses():
    import ast
    ast.parse(_server_src())
