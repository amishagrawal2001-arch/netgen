"""v0.5.178: sibling-conflict warning fires only for LIVE siblings.

Pre-fix, the warning fired whenever another Blast dialog window
was open with the HCA selected — even after the operator had
stopped its perftest run. Operator hit this on srv06 after
stopping a send_lat sweep on rocep43s0f1: opening another
dialog and clicking Start immediately produced the false alarm.

Fix gates the claim on (`_server_job_id` AND NOT
`_server_finished`) OR the client-side equivalent.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _build_siblings_fn(dialogs, current):
    """Mirror the _siblings closure from rdma_menu_actions.show_
    rdma_blast_dialog. Kept synced with that production code —
    if you change the predicate here, change it there too."""
    def _is_live(d):
        srv_live = (getattr(d, "_server_job_id", None)
                    and not getattr(d, "_server_finished", False))
        cli_live = (getattr(d, "_client_job_id", None)
                    and not getattr(d, "_client_finished", False))
        return bool(srv_live or cli_live)

    def _siblings(excluding=current):
        claimed = set()
        for d in dialogs:
            if d is excluding:
                continue
            if not _is_live(d):
                continue
            sd = d._server_device_combo.currentData() \
                if hasattr(d, "_server_device_combo") else None
            if sd:
                claimed.add((d._server_tg_url, sd))
            cd = d._client_device_combo.currentData() \
                if hasattr(d, "_client_device_combo") else None
            if cd:
                claimed.add((d._client_tg_url, cd))
        return claimed
    return _siblings


def _make_dialog(*, server_dev, client_dev, server_running, client_running,
                 server_started=True, client_started=True):
    """Build a stand-in for a Blast dialog with the fields the
    sibling tracker reads."""
    return SimpleNamespace(
        _server_tg_url="http://srv:5050",
        _client_tg_url="http://srv:5050",
        _server_device_combo=SimpleNamespace(
            currentData=lambda: server_dev),
        _client_device_combo=SimpleNamespace(
            currentData=lambda: client_dev),
        _server_job_id="srv-job-1" if server_started else None,
        _client_job_id="cli-job-1" if client_started else None,
        _server_finished=not server_running,
        _client_finished=not client_running,
    )


def test_open_idle_dialog_does_not_claim_hca():
    """Dialog open but never Started → no claim. Operator just
    has the window up to read params."""
    idle = _make_dialog(
        server_dev="mlx5_0", client_dev="mlx5_1",
        server_started=False, client_started=False,
        server_running=False, client_running=False,
    )
    siblings = _build_siblings_fn([idle, "current"], "current")
    assert siblings() == set()


def test_stopped_dialog_does_not_claim_hca():
    """The srv06 case: operator clicked Start, then Stop. Dialog
    window still open, HCA still selected in the combos. New
    dialog opens with same HCA — should NOT warn."""
    stopped = _make_dialog(
        server_dev="mlx5_0", client_dev="mlx5_1",
        server_started=True, client_started=True,
        server_running=False, client_running=False,
    )
    siblings = _build_siblings_fn([stopped, "current"], "current")
    assert siblings() == set(), (
        "stopped dialog falsely claimed HCAs — operators hit "
        "this as the 'Another Blast dialog already targeting…' "
        "warning after explicitly stopping a test")


def test_actively_running_dialog_claims_both_hcas():
    """Both jobs live (Start fired, neither finished) → claim
    both server + client HCAs. Real concurrency conflict."""
    running = _make_dialog(
        server_dev="mlx5_0", client_dev="mlx5_1",
        server_running=True, client_running=True,
    )
    siblings = _build_siblings_fn([running, "current"], "current")
    expected = {
        ("http://srv:5050", "mlx5_0"),
        ("http://srv:5050", "mlx5_1"),
    }
    assert siblings() == expected


def test_only_server_side_running_still_claims_both():
    """Asymmetric finish (rare but possible): server still
    running, client already done. Server is the busy resource;
    we still claim its HCA. Client side selection is grey-area
    but the simplest safe behaviour is to claim BOTH while ANY
    side is live — concurrent ops on either side still affect
    the HCA."""
    half_running = _make_dialog(
        server_dev="mlx5_0", client_dev="mlx5_1",
        server_running=True, client_running=False,
    )
    siblings = _build_siblings_fn(
        [half_running, "current"], "current")
    assert ("http://srv:5050", "mlx5_0") in siblings()
    # client HCA also claimed (the dialog is overall still live)
    assert ("http://srv:5050", "mlx5_1") in siblings()


def test_finished_dialog_with_jobs_set_does_not_claim():
    """job_id present but _finished = True (the normal post-run
    state). Should NOT claim."""
    done = _make_dialog(
        server_dev="mlx5_0", client_dev="mlx5_1",
        server_running=False, client_running=False,
        server_started=True, client_started=True,
    )
    siblings = _build_siblings_fn([done, "current"], "current")
    assert siblings() == set()


def test_excluding_self():
    """The current dialog must not appear in its own siblings
    set even if its own jobs are live (idempotent across an
    in-progress Start)."""
    me = _make_dialog(
        server_dev="mlx5_0", client_dev="mlx5_1",
        server_running=True, client_running=True,
    )
    siblings = _build_siblings_fn([me], me)
    assert siblings() == set()


def test_mixed_dialog_set_only_live_counted():
    """Real-world: 3 dialogs open — one idle, one stopped, one
    running. Only the running one's HCAs appear."""
    idle = _make_dialog(
        server_dev="mlx5_2", client_dev="mlx5_3",
        server_started=False, client_started=False,
        server_running=False, client_running=False,
    )
    stopped = _make_dialog(
        server_dev="mlx5_4", client_dev="mlx5_5",
        server_started=True, client_started=True,
        server_running=False, client_running=False,
    )
    running = _make_dialog(
        server_dev="mlx5_0", client_dev="mlx5_1",
        server_running=True, client_running=True,
    )
    siblings = _build_siblings_fn(
        [idle, stopped, running, "current"], "current")
    got = siblings()
    assert got == {
        ("http://srv:5050", "mlx5_0"),
        ("http://srv:5050", "mlx5_1"),
    }
    # Specifically: idle dialog's mlx5_2/3 NOT in set; stopped
    # dialog's mlx5_4/5 NOT in set (the regression).
    assert ("http://srv:5050", "mlx5_2") not in got
    assert ("http://srv:5050", "mlx5_4") not in got
