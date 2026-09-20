"""v0.5.361 — capture_client: timeout on every requests call +
`with` context on the streaming download.

Post-v0.5.360 audit A1 + A10. Pre-fix, `PacketCaptureClient.
start_capture` / `stop_capture` / `download_capture` all called
`requests.post` or `requests.get` with NO `timeout=` — a wedged
capture server (deadlocked sniffer, slow disk, network partition)
hung the Qt slot / worker thread indefinitely. Same file also
opened the streaming download response without a `with` context,
so the TCP connection leaked on any exception in `iter_content`.

Fixes (one marker `v0.5.361 (audit capture-client-no-timeout)` +
`v0.5.361 (audit capture-client-stream-not-closed)`):

- Every `requests.*` gets `timeout=(connect, read)`. Connect
  fixed at 5s (LAN, immediate ACK); read at 30s for the quick
  start/stop routes and 60s for the streaming download.
- `download_capture` wraps the `requests.get(..., stream=True)`
  in a `with` block so the underlying connection is released
  even when `iter_content` raises.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _src():
    return (_REPO / "capture_client.py").read_text()


def test_all_markers_present():
    src = _src()
    assert "v0.5.361 (audit capture-client-no-timeout)" in src
    assert "v0.5.361 (audit capture-client-stream-not-closed)" in src


def test_start_capture_has_timeout():
    """`requests.post` in start_capture must carry a `timeout=`
    kwarg. Structural check via AST so a rewrite that keeps the
    kwarg but reformats the source still passes."""
    import ast
    tree = ast.parse(_src())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "start_capture":
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            _fn = ast.unparse(call.func)
            if _fn.endswith("requests.post"):
                _kwargs = {kw.arg for kw in call.keywords}
                assert "timeout" in _kwargs, (
                    "start_capture's requests.post is missing timeout="
                )
                return
    raise AssertionError("no requests.post call found in start_capture")


def test_stop_capture_has_timeout():
    import ast
    tree = ast.parse(_src())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "stop_capture":
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            _fn = ast.unparse(call.func)
            if _fn.endswith("requests.post"):
                _kwargs = {kw.arg for kw in call.keywords}
                assert "timeout" in _kwargs
                return
    raise AssertionError("no requests.post call found in stop_capture")


def test_download_capture_has_timeout():
    import ast
    tree = ast.parse(_src())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "download_capture":
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            _fn = ast.unparse(call.func)
            if _fn.endswith("requests.get"):
                _kwargs = {kw.arg for kw in call.keywords}
                assert "timeout" in _kwargs
                assert "stream" in _kwargs  # still streaming
                return
    raise AssertionError("no requests.get call found in download_capture")


def test_download_capture_uses_with_context_for_response():
    """The streaming response must be used inside a `with` block so
    the underlying TCP connection is released even when
    `iter_content` raises. Structural check for a `With` node
    whose context expression is a `requests.get(...)` call."""
    import ast
    tree = ast.parse(_src())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "download_capture":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.With):
                continue
            for item in sub.items:
                if not isinstance(item.context_expr, ast.Call):
                    continue
                _fn = ast.unparse(item.context_expr.func)
                if _fn.endswith("requests.get"):
                    return
    raise AssertionError(
        "download_capture must wrap `requests.get(..., stream=True)` "
        "in a `with` block so the TCP socket is released on error paths"
    )


# --- Runtime proof: mocked requests. no server needed. ---


def _install_dummy_requests(monkeypatch, capture):
    """Install a dummy `requests` module that records every kwarg
    dict passed to `post` / `get` so tests can verify `timeout=`
    landed on the wire."""
    import types

    class _DummyResp:
        def __init__(self):
            self.status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

        def iter_content(self, chunk_size=1024):
            yield b"x" * chunk_size

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _record(method):
        def _f(url, **kwargs):
            capture.append({"method": method, "url": url, **kwargs})
            return _DummyResp()
        return _f

    dummy = types.SimpleNamespace(
        post=_record("post"),
        get=_record("get"),
        RequestException=Exception,
    )
    monkeypatch.setattr(
        "capture_client.requests", dummy, raising=True,
    )


def test_runtime_start_capture_passes_timeout(monkeypatch, tmp_path):
    """End-to-end proof: patch `requests`, call `start_capture`,
    inspect the captured kwargs — must include a timeout tuple."""
    _seen = []
    _install_dummy_requests(monkeypatch, _seen)
    import capture_client  # noqa: F401 — imported through monkeypatch shim
    c = capture_client.PacketCaptureClient("http://x:5000")
    out = c.start_capture("vlan10")
    assert out == {"ok": True}
    assert _seen and _seen[0]["method"] == "post"
    _t = _seen[0].get("timeout")
    assert _t is not None
    # tuple (connect, read).
    assert isinstance(_t, tuple) and len(_t) == 2
    assert _t[0] > 0 and _t[1] > 0


def test_runtime_download_capture_streams_and_closes(monkeypatch, tmp_path):
    """End-to-end: streaming download works, and the response acts
    as a context manager (our dummy provides __exit__)."""
    _seen = []
    _install_dummy_requests(monkeypatch, _seen)
    import capture_client
    c = capture_client.PacketCaptureClient("http://x:5000")
    out_path = tmp_path / "out.pcap"
    out = c.download_capture("/tmp/x.pcap", str(out_path))
    assert "Saved to" in out.get("message", "")
    assert out_path.exists() and out_path.stat().st_size > 0
    assert _seen[0]["method"] == "get"
    assert _seen[0].get("stream") is True
    _t = _seen[0].get("timeout")
    assert isinstance(_t, tuple) and _t[1] >= 30  # download read timeout


def test_runtime_error_path_returns_error_dict(monkeypatch):
    """Regression guard: on RequestException the caller still gets
    the pre-fix contract `{"error": ...}` back — the fix must not
    have broken exception-shape."""
    import types

    class _BoomRequests(types.SimpleNamespace):
        RequestException = Exception

        def post(self, *a, **kw):
            raise self.RequestException("connection refused")

        def get(self, *a, **kw):
            raise self.RequestException("connection refused")

    monkeypatch.setattr("capture_client.requests", _BoomRequests(), raising=True)
    import capture_client
    c = capture_client.PacketCaptureClient("http://x:5000")
    assert "error" in c.start_capture("vlan10")
    assert "error" in c.stop_capture("vlan10")
    assert "error" in c.download_capture("/tmp/x", "/tmp/y")


def test_ast_parses():
    import ast
    ast.parse(_src())
