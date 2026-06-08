"""v0.5.35 — canonical 7-phase user workflow folded into the
in-app Feature Guide.

Operator request after the v0.5.34 menu consolidation: "fold
this into the in-app help guide". The workflow I'd just summarized
in chat was useful enough to be the canonical reference, but only
discoverable by re-asking — folding it into Help → Feature Guide
makes it findable for every operator (current + future) without
leaving the client.

The section goes at the TOP of _FEATURE_GUIDE_HTML (right after
the <h1> intro, before the version-by-version highlights) so a
new operator opening the guide sees the workflow first — not a
list of bug fixes from v0.3.13.

These tests pin: section exists, all 7 phases present, key
operator-facing menu paths mentioned, compressed mental model
diagram included. Anyone trimming the workflow section earns a
test failure here, not the next "what do I do first?" question
from an operator.
"""
from __future__ import annotations

import re
from pathlib import Path


_STREAM_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "stream_dialog.py"
)


def _feature_guide_html() -> str:
    """Extract the _FEATURE_GUIDE_HTML literal."""
    src = _STREAM_DIALOG.read_text()
    m = re.search(
        r'_FEATURE_GUIDE_HTML\s*=\s*r"""([\s\S]+?)"""',
        src,
    )
    assert m, "_FEATURE_GUIDE_HTML literal not found"
    return m.group(1)


def test_user_workflow_section_exists():
    """The workflow section must be in _FEATURE_GUIDE_HTML with
    a discoverable heading. Match the <h2> tag with the canonical
    label 'User workflow'."""
    html = _feature_guide_html()
    assert re.search(
        r'<h2[^>]*>\s*User workflow',
        html,
    ), (
        "Help → Feature Guide is missing the 'User workflow' section. "
        "Operators have no canonical reference for end-to-end flow."
    )


def test_workflow_section_appears_before_version_highlights():
    """The workflow must be at the TOP of the guide — before the
    version-by-version highlights — so a new operator opening the
    guide sees workflow FIRST, not a list of v0.3.13 bug fixes.

    Match: position of 'User workflow' header < position of the
    first version-highlight header."""
    html = _feature_guide_html()
    workflow_pos = html.find("User workflow")
    # Find the first version-highlight header (any of the
    # `<span class="ver new">0.X.Y</span>` patterns)
    highlight_m = re.search(
        r'<h2[^>]*>\s*<span\s+class="ver',
        html,
    )
    assert highlight_m, "No version-highlight <h2> blocks found"
    highlight_pos = highlight_m.start()
    assert workflow_pos > 0 and workflow_pos < highlight_pos, (
        f"User workflow section is at position {workflow_pos} but "
        f"version highlights start at {highlight_pos}. Workflow "
        f"should come FIRST so new operators see it before bug "
        f"fix changelogs."
    )


def test_all_seven_phases_present():
    """Pin each phase by its `<h3>Phase N — ...</h3>` header. A
    refactor trimming any phase earns a failure."""
    html = _feature_guide_html()
    for n in range(1, 8):
        assert re.search(
            rf'<h3[^>]*>\s*Phase\s+{n}\b',
            html,
        ), (
            f"Phase {n} <h3> header missing from workflow section. "
            f"All 7 phases (install / connect / provision / devices "
            f"/ run / specialised / upgrade) must be present."
        )


def test_workflow_mentions_setup_rdma_and_setup_dpdk_menu_paths():
    """The most operator-asked menu paths must appear verbatim so
    operators can copy-paste them into chat / runbooks."""
    html = _feature_guide_html()
    # Setup RDMA menu path
    assert "Setup RDMA" in html, (
        "Workflow doesn't mention Setup RDMA — operators with "
        "Mellanox NICs won't find the entry point."
    )
    # Setup DPDK menu path
    assert "Setup DPDK" in html, (
        "Workflow doesn't mention Setup DPDK — primary high-rate "
        "TX path is unfindable."
    )
    # Tools → DPDK / Tools → RDMA prefixes
    assert "Tools → DPDK" in html or "Tools → RDMA" in html, (
        "Workflow doesn't use the canonical 'Tools → ...' menu "
        "prefix — operators searching for 'Tools' will miss it."
    )


def test_workflow_mentions_mellanox_order_dependency():
    """DPDK-on-Mellanox order is the most common workflow trap:
    Setup RDMA must run BEFORE Setup DPDK so the mlx5 PMD picks up
    libibverbs at meson configure time. The workflow section must
    surface this so operators don't hit the silent-PMD-skip."""
    html = _feature_guide_html()
    # Look for the order-dependency hint (any reasonable phrasing)
    assert re.search(
        r"Setup RDMA\s*<b>first</b>|Setup RDMA first|RDMA before|"
        r"first.*Setup DPDK",
        html, re.IGNORECASE,
    ), (
        "Workflow doesn't surface the Mellanox order dependency "
        "(Setup RDMA FIRST, then Setup DPDK). Operators with "
        "Mellanox NICs hit the silent mlx5-PMD-skip trap."
    )


def test_workflow_mentions_install_server_upgrade_path():
    """Phase 7 (Upgrade) must reference the GUI menu path
    'Install Server → Upgrade Running Server' so operators don't
    fall back to manual SSH + pip install."""
    html = _feature_guide_html()
    assert "Upgrade Running Server" in html, (
        "Workflow doesn't mention 'Upgrade Running Server' — "
        "operators won't find the wheel-upgrade GUI flow."
    )


def test_workflow_includes_compressed_mental_model():
    """The compressed mental-model diagram is the scannable
    overview for operators who don't want to read 7 tables. Pin
    its presence + key checkpoints."""
    html = _feature_guide_html()
    assert "Compressed mental model" in html, (
        "Compressed mental model section missing — operators have "
        "no at-a-glance flow diagram."
    )
    # Key flow nodes
    for node in (
        "fresh host",
        "Add TGen Chassis",
        "Devices",
        "Streams",
        "Upgrade Running Server",
    ):
        assert node in html, (
            f"Compressed mental model missing key node {node!r}. "
            f"The flow diagram should be self-contained."
        )


def test_workflow_references_recent_ux_improvements():
    """The workflow should cite at least a few of the recent UX
    wins (scrollable log, inline apt-fail tail, reboot button)
    so operators reading the guide know these features exist."""
    html = _feature_guide_html()
    # Search for the version stamps near the workflow section.
    # At minimum two of these should be cited near the workflow:
    # 0.5.30 (inline log tail), 0.5.32 (scroll/copy), 0.5.34
    # (reboot button + menu consolidation).
    workflow_start = html.find("User workflow")
    workflow_end = html.find("highlights — RDMA", workflow_start)
    workflow_block = html[workflow_start:workflow_end] if workflow_end > 0 else html[workflow_start:]
    cited = sum(
        1 for v in ("0.5.30", "0.5.32", "0.5.34", "0.5.23", "0.5.24")
        if v in workflow_block
    )
    assert cited >= 2, (
        f"Workflow section cites only {cited} recent-UX version "
        f"stamps. Operators won't realize the recent fixes "
        f"(scrollable log, inline apt-tail, reboot button) exist."
    )


def test_pyproject_version_at_least_0535():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 35), (
        f"Version {m.group(1)} < 0.5.35"
    )
