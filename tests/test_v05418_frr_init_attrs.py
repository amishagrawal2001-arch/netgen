"""v0.5.418 — FRRDockerManager.__init__ dead-code regression (GG1).

The v0.5.386 FRR-B4 patch inserted `_ensure_client` BETWEEN the two
halves of `__init__` and left the second half at 8-space indent.
Python parsed those lines as the tail of `_ensure_client`'s body —
after both try/except branches returned explicitly on line 381 —
so the lines were unreachable. `_vrf_alloc_lock`, `_vrf_allocated`,
`_vrf_state_path`, `_start_locks`, `_start_locks_meta` were never
set on the instance; every `start_frr_container(...)` call since
v0.5.386 raised AttributeError on the first line
(`self._start_lock_for(device_id)` → missing `_start_locks_meta`).

The pre-fix regression tests (`tests/test_v05373_deferred_bundle.py`,
`tests/test_v05383_frr_stats_med.py`) only grepped source strings;
they never instantiated the class, which is why 32 released
versions shipped with this latent crash.

This test instantiates FRRDockerManager (with the `docker` module
stubbed) and asserts the five attrs exist AND the two methods that
depend on them work.
"""
from __future__ import annotations

import ast
import re
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─── AST sanity ───


def test_frr_docker_ast_parses():
    ast.parse(_read("utils/frr_docker.py"))


# ─── GG1: __init__ bounds + method bounds ───


def test_gg1_marker_present():
    src = _read("utils/frr_docker.py")
    assert "v0.5.418 (audit stream-GG1)" in src


def test_gg1_init_spans_the_whole_init_body():
    """`__init__` must contain ALL 5 attr assignments, not stop
    before them at the `_ensure_client` method boundary."""
    tree = ast.parse(_read("utils/frr_docker.py"))
    _cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "FRRDockerManager"
    )
    _init = next(
        m for m in _cls.body
        if isinstance(m, ast.FunctionDef) and m.name == "__init__"
    )
    # Pre-fix end_lineno was 336; post-fix must cover both v0.5.373
    # and v0.5.383 blocks (ends on `_start_locks_meta` assignment).
    assert _init.end_lineno >= 380, (
        f"__init__ ends at line {_init.end_lineno}; expected ≥ 380 "
        f"so the orphaned v0.5.373 + v0.5.383 blocks are inside it."
    )

    # Collect all `self.<name> = ...` assignments inside __init__.
    _assigned = set()
    for _node in ast.walk(_init):
        if isinstance(_node, ast.Assign):
            for _tgt in _node.targets:
                if (isinstance(_tgt, ast.Attribute)
                        and isinstance(_tgt.value, ast.Name)
                        and _tgt.value.id == "self"):
                    _assigned.add(_tgt.attr)
        elif isinstance(_node, ast.AnnAssign):
            _tgt = _node.target
            if (isinstance(_tgt, ast.Attribute)
                    and isinstance(_tgt.value, ast.Name)
                    and _tgt.value.id == "self"):
                _assigned.add(_tgt.attr)
    for _attr in (
        "_vrf_alloc_lock",
        "_vrf_allocated",
        "_vrf_state_path",
        "_start_locks",
        "_start_locks_meta",
    ):
        assert _attr in _assigned, (
            f"__init__ must assign self.{_attr}; got {sorted(_assigned)}"
        )


def test_gg1_ensure_client_body_ends_at_return():
    """`_ensure_client` must not contain any statements after its
    final `return self.client` in the except branch — those were
    the dead statements that caused GG1."""
    tree = ast.parse(_read("utils/frr_docker.py"))
    _cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "FRRDockerManager"
    )
    _ec = next(
        m for m in _cls.body
        if isinstance(m, ast.FunctionDef) and m.name == "_ensure_client"
    )
    # Body shape: single Try node wrapping everything.
    assert len(_ec.body) == 1 and isinstance(_ec.body[0], ast.Try), (
        f"_ensure_client body shape unexpected: "
        f"{[type(s).__name__ for s in _ec.body]}"
    )


# ─── Runtime: instantiate + probe (THE test the pre-fix suite missed) ───


def _stub_docker():
    """Install a minimal `docker` module stub so FRRDockerManager()
    constructs without a running daemon. Idempotent."""
    if "docker" in sys.modules and hasattr(sys.modules["docker"], "_v05418_stub"):
        return
    _docker = types.ModuleType("docker")
    _errors = types.ModuleType("docker.errors")

    class _NotFound(Exception):
        pass

    class _APIError(Exception):
        pass

    _errors.NotFound = _NotFound
    _errors.APIError = _APIError
    _docker.errors = _errors
    _docker.from_env = lambda: types.SimpleNamespace(
        ping=lambda: None,
        containers=types.SimpleNamespace(
            get=lambda n: (_ for _ in ()).throw(_NotFound()),
        ),
        images=types.SimpleNamespace(get=lambda n: None),
    )
    _docker._v05418_stub = True
    sys.modules["docker"] = _docker
    sys.modules["docker.errors"] = _errors


def test_gg1_frr_manager_instantiates_cleanly():
    _stub_docker()
    # Re-import in case a stale cached copy from the pre-fix module
    # lingers in sys.modules.
    sys.modules.pop("utils.frr_docker", None)
    from utils.frr_docker import FRRDockerManager
    m = FRRDockerManager()
    for _attr in (
        "_vrf_alloc_lock",
        "_vrf_allocated",
        "_vrf_state_path",
        "_start_locks",
        "_start_locks_meta",
    ):
        assert hasattr(m, _attr), f"instance is missing {_attr}"


def test_gg1_start_lock_for_does_not_raise():
    _stub_docker()
    sys.modules.pop("utils.frr_docker", None)
    from utils.frr_docker import FRRDockerManager
    m = FRRDockerManager()
    # Pre-fix this raised AttributeError('_start_locks_meta').
    _lk = m._start_lock_for("dev-abc12345")
    assert _lk is not None
    # Same device must return the same Lock instance (serialisation
    # requirement — the v0.5.383 X1 contract).
    assert m._start_lock_for("dev-abc12345") is _lk


def test_gg1_vrf_table_assigns_in_range():
    _stub_docker()
    sys.modules.pop("utils.frr_docker", None)
    from utils.frr_docker import FRRDockerManager
    m = FRRDockerManager()
    # Pre-fix this raised AttributeError('_vrf_alloc_lock').
    _tid = m._vrf_table("dev-abc12345-1234")
    assert isinstance(_tid, int)
    assert 1000 <= _tid <= 3999, (
        f"vrf table id {_tid} outside configured range [1000, 3999]"
    )


# ─── version guard ───


def test_pyproject_at_least_0618():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 418)


# ─── regression guards ───


def test_v0417_isis_ff2_intact():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF2)" in src


def test_v0416_ospf_ee1_intact():
    src = _read("utils/ospf.py")
    assert "v0.5.416 (audit stream-EE1)" in src


def test_v0386_frr_b4_ensure_client_still_defined():
    src = _read("utils/frr_docker.py")
    # FRR-B4 reconnect wrapper must still exist — this fix moved
    # init blocks AROUND it, not deleted it.
    assert "def _ensure_client(self):" in src
    assert "v0.5.386 (audit FRR-B4)" in src
