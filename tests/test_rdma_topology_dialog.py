"""Tests for widgets/rdma_topology_dialog.py — v0.4.0 Topology Mode UI.

Covers: dialog constructs, endpoint-line parser handles the
documented format + edge cases, shape change re-counts the pair
preview label, stats-table skeleton populates correctly,
closeEvent stops the poll timer."""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PyQt5 = pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)


# ─────────────────────────────────── endpoint-line parser ──────────────


def test_parse_minimal_endpoint_line():
    from widgets.rdma_topology_dialog import parse_endpoint_line
    ep = parse_endpoint_line("http://srv01:5050 mlx5_0")
    assert ep is not None
    assert ep.tg_url == "http://srv01:5050"
    assert ep.device == "mlx5_0"
    assert ep.ib_port == 1
    assert ep.gid_index == 3
    assert ep.label is None


def test_parse_endpoint_with_options():
    from widgets.rdma_topology_dialog import parse_endpoint_line
    ep = parse_endpoint_line(
        "http://srv01:5050 mlx5_0 port=2 gid=0 label=primary"
    )
    assert ep.ib_port == 2
    assert ep.gid_index == 0
    assert ep.label == "primary"


def test_parse_skips_blank_and_comment_lines():
    from widgets.rdma_topology_dialog import parse_endpoint_line
    assert parse_endpoint_line("") is None
    assert parse_endpoint_line("   ") is None
    assert parse_endpoint_line("# comment here") is None
    assert parse_endpoint_line("    # indented comment") is None


def test_parse_rejects_wrong_scheme():
    from widgets.rdma_topology_dialog import parse_endpoint_line
    with pytest.raises(ValueError, match="http"):
        parse_endpoint_line("srv01:5050 mlx5_0")
    with pytest.raises(ValueError, match="http"):
        parse_endpoint_line("ssh://srv01 mlx5_0")


def test_parse_rejects_too_few_tokens():
    from widgets.rdma_topology_dialog import parse_endpoint_line
    with pytest.raises(ValueError, match="at least"):
        parse_endpoint_line("http://srv01:5050")


def test_parse_rejects_unknown_key():
    from widgets.rdma_topology_dialog import parse_endpoint_line
    with pytest.raises(ValueError, match="unknown key"):
        parse_endpoint_line("http://srv01:5050 mlx5_0 bogus=1")


def test_parse_rejects_non_int_port():
    from widgets.rdma_topology_dialog import parse_endpoint_line
    with pytest.raises(ValueError, match="port"):
        parse_endpoint_line("http://srv01:5050 mlx5_0 port=not_a_number")


def test_parse_block_collects_errors_per_line():
    from widgets.rdma_topology_dialog import parse_endpoint_block
    text = (
        "http://srv01:5050 mlx5_0\n"
        "bad-line-without-scheme mlx5_0\n"
        "http://srv02:5050 mlx5_1 port=bogus\n"
        "# comment\n"
        "http://srv03:5050 mlx5_2\n"
    )
    eps, errs = parse_endpoint_block(text)
    assert len(eps) == 2  # 2 good lines (1st + 5th)
    assert len(errs) == 2  # 2 bad lines (2nd + 3rd)
    assert "line 2" in errs[0]
    assert "line 3" in errs[1]


# ─────────────────────────────────── dialog construction ──────────────


def _make_dialog():
    from widgets.rdma_topology_dialog import RdmaTopologyDialog
    return RdmaTopologyDialog()


def test_dialog_constructs():
    d = _make_dialog()
    assert d.windowTitle() == "RDMA Topology Test"
    # Default state — no plans, no pair jobs, idle
    assert d._plans == []
    assert d._pair_jobs == {}
    assert d._poll_timer is None
    assert d._start_btn.isEnabled()
    assert not d._stop_btn.isEnabled()
    d.close()


def test_dialog_default_shape_is_mesh():
    """Mesh is the most general — sensible default. Lets operator
    see what N×M does without having to think about which radio
    to pick first."""
    from utils.rdma_topology import SHAPE_MESH
    d = _make_dialog()
    assert d._current_shape() == SHAPE_MESH
    d.close()


def test_pair_count_label_updates_on_text_change():
    """As operator types endpoints, the '0 pairs' label live-updates
    to reflect what the current shape + endpoint counts would expand
    into. Catches mismatches (shape=single but 3 endpoints) early."""
    d = _make_dialog()
    # Initially empty
    assert "0 pair" in d._pair_count_label.text()

    # Add 2 servers + 3 clients → mesh = 6 pairs
    d._server_edit.setPlainText(
        "http://srv01:5050 mlx5_0\n"
        "http://srv02:5050 mlx5_0\n"
    )
    d._client_edit.setPlainText(
        "http://srv03:5050 mlx5_0\n"
        "http://srv04:5050 mlx5_0\n"
        "http://srv05:5050 mlx5_0\n"
    )
    assert "6 pair" in d._pair_count_label.text()
    d.close()


def test_pair_count_label_surfaces_shape_mismatch():
    """shape=single + multiple endpoints should surface the
    validation error in the label so operator sees it BEFORE
    clicking Start."""
    from utils.rdma_topology import SHAPE_SINGLE
    d = _make_dialog()
    d._shape_buttons[SHAPE_SINGLE].setChecked(True)
    d._server_edit.setPlainText(
        "http://srv01:5050 mlx5_0\n"
        "http://srv02:5050 mlx5_0\n"
    )
    d._client_edit.setPlainText("http://srv03:5050 mlx5_0\n")
    # validate_spec rejected → label shows error text
    lbl = d._pair_count_label.text().lower()
    assert "single" in lbl or "exactly 1" in lbl, (
        f"shape-mismatch error should surface in label; got {lbl!r}"
    )
    d.close()


def test_pair_count_label_surfaces_parse_error():
    """Malformed endpoint line → label highlights the parse error
    so operator can fix it without trial+error click."""
    d = _make_dialog()
    d._server_edit.setPlainText("bogus-line-no-scheme")
    lbl = d._pair_count_label.text().lower()
    assert "parse" in lbl or "http" in lbl
    d.close()


def test_changing_shape_recounts_pairs():
    """Toggling between shapes with same endpoints should update
    the pair count: mesh 2×3=6, fan_out 2×1=2, etc."""
    from utils.rdma_topology import (
        SHAPE_FAN_IN, SHAPE_FAN_OUT, SHAPE_MESH, SHAPE_PAIRWISE,
    )
    d = _make_dialog()
    d._server_edit.setPlainText(
        "http://srv01:5050 mlx5_0\nhttp://srv02:5050 mlx5_0\n"
    )
    d._client_edit.setPlainText(
        "http://srv03:5050 mlx5_0\nhttp://srv04:5050 mlx5_0\n"
    )
    # Default mesh: 2×2 = 4
    assert "4 pair" in d._pair_count_label.text()

    # Pairwise: equal lengths → 2
    d._shape_buttons[SHAPE_PAIRWISE].setChecked(True)
    assert "2 pair" in d._pair_count_label.text()

    # fan_in requires exactly 1 server → 2-server case is an error
    d._shape_buttons[SHAPE_FAN_IN].setChecked(True)
    assert "fan_in" in d._pair_count_label.text().lower()
    d.close()


def test_common_opts_picks_up_widget_values():
    d = _make_dialog()
    d._msg_size_spin.setValue(8192)
    d._qp_count_spin.setValue(8)
    d._duration_spin.setValue(60)
    d._bidir_check.setChecked(True)
    opts = d._common_opts()
    assert opts["msg_size"] == 8192
    assert opts["qp_count"] == 8
    assert opts["duration"] == 60
    assert opts["bidirectional"] is True
    assert opts["report_gbits"] is True
    d.close()


def test_stats_table_populates_skeleton():
    """After clicking Start, the stats table should populate one row
    per pair with queued state + endpoint labels."""
    from utils.rdma_topology import RdmaTopologyEndpoint, RdmaTopologySpec, expand_pairs
    d = _make_dialog()
    plans = expand_pairs(RdmaTopologySpec(
        shape="mesh",
        server_endpoints=[
            RdmaTopologyEndpoint("http://srv01:5050", "mlx5_0"),
            RdmaTopologyEndpoint("http://srv02:5050", "mlx5_0"),
        ],
        client_endpoints=[
            RdmaTopologyEndpoint("http://srv03:5050", "mlx5_0"),
        ],
        test="send_bw",
        workload_opts={"msg_size": 65536},
    ))
    d._plans = plans
    d._populate_stats_table_skeleton(plans)
    assert d._stats_table.rowCount() == 2
    assert d._stats_table.item(0, 0).text() == "0"
    assert d._stats_table.item(1, 0).text() == "1"
    # Server label uses display() which strips http:// + port
    assert "srv01" in d._stats_table.item(0, 1).text()
    assert "srv03" in d._stats_table.item(0, 2).text()
    assert d._stats_table.item(0, 3).text() == "queued"
    d.close()


def test_close_event_stops_poll_timer():
    """closeEvent must stop the poll timer — otherwise Qt delivers
    tick events to a deleted widget. Same SIGABRT-prevention
    pattern as RdmaBlastFlowDialog."""
    from PyQt5.QtCore import QTimer
    from PyQt5.QtGui import QCloseEvent
    d = _make_dialog()
    # Spin up the timer manually (the real start() path needs network
    # responses — fake it here just to exercise the teardown path).
    d._poll_timer = QTimer(d)
    d._poll_timer.start(2000)
    assert d._poll_timer is not None
    d.closeEvent(QCloseEvent())
    assert d._poll_timer is None


def test_all_pairs_done_false_when_no_data():
    """Empty / pre-start dialog must report 'not done' (otherwise we'd
    falsely tell the operator the topology has finished before it
    started)."""
    d = _make_dialog()
    assert d._all_pairs_done() is False
    d.close()
