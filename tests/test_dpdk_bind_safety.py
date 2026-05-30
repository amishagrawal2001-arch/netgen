"""Pre-bind safety helper tests (v0.2.76).

The server's /api/dpdk/bind endpoint pre-flights the bind against three
hazards before invoking the bind script. Pure-function tests covering
every combo so future changes can't accidentally lock an operator out
of their own host.
"""

import pytest
from types import SimpleNamespace

from utils.dpdk_bind_safety import (
    check_bind_safe,
    collect_default_route_iface,
    collect_ssh_client_iface,
)


# ──────────────────────────────────────────────────── check_bind_safe
def test_safe_when_no_hazards():
    assert check_bind_safe(
        "ens1f0",
        default_route_iface="eno1",
        ssh_client_iface="eno1",
        active_stream_ifaces={"ens2f0"},
    ) is None


def test_refuses_management_interface_by_default_route():
    reason = check_bind_safe(
        "eno1",
        default_route_iface="eno1",
        ssh_client_iface=None,
        active_stream_ifaces=None,
    )
    assert reason is not None
    assert "default route" in reason
    assert "eno1" in reason


def test_refuses_management_interface_by_ssh_session():
    reason = check_bind_safe(
        "eno1",
        default_route_iface=None,
        ssh_client_iface="eno1",
        active_stream_ifaces=None,
    )
    assert reason is not None
    assert "SSH" in reason


def test_refuses_iface_with_active_stream():
    reason = check_bind_safe(
        "ens1f0",
        default_route_iface="eno1",
        ssh_client_iface="eno1",
        active_stream_ifaces={"ens1f0", "ens2f0"},
    )
    assert reason is not None
    assert "active traffic stream" in reason
    assert "ens1f0" in reason


def test_handles_empty_iface_gracefully():
    """Empty / None iface = nothing to check, no crash."""
    assert check_bind_safe("") is None
    assert check_bind_safe("   ") is None
    assert check_bind_safe(None) is None


def test_strips_iface_whitespace():
    """Operator's iface string from a form may have stray whitespace
    — don't false-negative because of it."""
    reason = check_bind_safe(
        "  eno1  ",
        default_route_iface="eno1",
    )
    assert reason is not None


def test_active_stream_set_tolerates_duplicates_and_blanks():
    reason = check_bind_safe(
        "ens1f0",
        active_stream_ifaces=["ens1f0", "ens1f0", "", None, "ens2f0"],
    )
    assert reason is not None
    assert "active traffic stream" in reason


def test_default_route_check_takes_precedence_over_ssh():
    """Both hazards apply → default-route reason wins (it's the more
    accurate description of what's broken; SSH is symptom, route is
    cause)."""
    reason = check_bind_safe(
        "eno1",
        default_route_iface="eno1",
        ssh_client_iface="eno1",
    )
    assert reason is not None
    assert "default route" in reason


# ───────────────────────────────────────────── collect_default_route_iface
def test_collect_default_route_iface_parses_ip_route_output():
    """Parses `ip -o route show default` for the `dev <iface>` field."""
    fake = SimpleNamespace(
        stdout="default via 10.0.0.1 dev eno1 proto static metric 100\n",
        returncode=0,
    )
    iface = collect_default_route_iface(run=lambda _cmd: fake)
    assert iface == "eno1"


def test_collect_default_route_iface_returns_none_when_no_default():
    fake = SimpleNamespace(stdout="", returncode=0)
    assert collect_default_route_iface(run=lambda _cmd: fake) is None


def test_collect_default_route_iface_swallows_subprocess_errors():
    def raise_oops(_cmd):
        raise OSError("ip not found")
    assert collect_default_route_iface(run=raise_oops) is None


# ──────────────────────────────────────────── collect_ssh_client_iface
def test_collect_ssh_client_iface_parses_ip_route_get():
    fake = SimpleNamespace(
        stdout="10.0.0.5 via 10.0.0.1 dev eno1 src 10.0.0.42 uid 0\n",
        returncode=0,
    )
    iface = collect_ssh_client_iface(
        ssh_client_env="10.0.0.5 54321 22",
        run=lambda _cmd: fake,
    )
    assert iface == "eno1"


def test_collect_ssh_client_iface_returns_none_when_no_ssh_env():
    """No SSH session (e.g. local console run) → None, no crash."""
    assert collect_ssh_client_iface(None) is None
    assert collect_ssh_client_iface("") is None


def test_collect_ssh_client_iface_handles_malformed_env():
    """$SSH_CLIENT can in theory be malformed — don't crash."""
    fake = SimpleNamespace(stdout="", returncode=0)
    # Empty first token after split should still produce None gracefully.
    result = collect_ssh_client_iface("   ", run=lambda _c: fake)
    assert result is None
