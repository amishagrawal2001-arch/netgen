"""Regression tests for the v0.3.16 SFTP fresh-install failure.

Operator report:
    [client] sftp put .../requirements.txt → /tmp/netgen_install/requirements.txt
    [client] sftp put .../resources/dpdk/ → /tmp/netgen_install/resources/dpdk/ (recursive)
    [client] SFTP upload failed: [Errno 2] No such file

Root cause was in widgets/install_server_dialog.SshInstallWorker._sftp_put_tree:

    try:
        sftp.mkdir(remote_dir)   # remote_dir = /tmp/netgen_install/resources/dpdk
    except IOError:
        pass  # exists

paramiko's ``sftp.mkdir`` has no ``-p`` semantics. When the parent
``/tmp/netgen_install/resources`` doesn't exist yet, mkdir raises
IOError. The except swallows it silently. Subsequent ``sftp.put``
calls into the non-existent directory then fail with "[Errno 2] No
such file" — which the operator saw.

The fix adds ``_sftp_makedirs`` which walks each path component,
calling mkdir on each in order. Same "ignore IOError on each" pattern
so already-existing directories don't abort the walk.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5")

# Some PyQt5 imports indirectly pull paramiko which isn't available on
# every test box. Stub it so the import chain doesn't fail.
sys.modules.setdefault("paramiko", MagicMock())

from widgets.install_server_dialog import SshInstallWorker


@pytest.fixture
def fake_worker():
    """Stub instance that exposes _sftp_makedirs as a bound method
    without running SshInstallWorker.__init__ (which needs Qt + args)."""
    class _Fake:
        pass
    f = _Fake()
    f._sftp_makedirs = SshInstallWorker._sftp_makedirs.__get__(f)
    return f


def _mock_sftp(recording_list):
    """sftp mock whose mkdir appends to recording_list and never raises."""
    sftp = MagicMock()
    sftp.mkdir = lambda p: recording_list.append(p)
    return sftp


def test_user_scenario_walks_each_path_component(fake_worker):
    """Verbatim repro of the operator's failure: uploading
    /tmp/netgen_install/resources/dpdk when only /tmp existed remotely.

    Pre-v0.3.16 only the leaf mkdir was attempted → silent IOError →
    subsequent put failed with [Errno 2] No such file. Post-fix every
    intermediate component gets a mkdir call in order."""
    calls = []
    fake_worker._sftp_makedirs(_mock_sftp(calls),
                               "/tmp/netgen_install/resources/dpdk")
    assert calls == [
        "/tmp",
        "/tmp/netgen_install",
        "/tmp/netgen_install/resources",
        "/tmp/netgen_install/resources/dpdk",
    ]


def test_deeply_nested_path_walks_all_components(fake_worker):
    """resources/dpdk/tx_worker/build is the real depth the install
    encounters (meson build artifacts inside the tx_worker subdir).
    All 6 components must be mkdir'd in order."""
    calls = []
    fake_worker._sftp_makedirs(
        _mock_sftp(calls),
        "/tmp/netgen_install/resources/dpdk/tx_worker/build",
    )
    assert calls == [
        "/tmp",
        "/tmp/netgen_install",
        "/tmp/netgen_install/resources",
        "/tmp/netgen_install/resources/dpdk",
        "/tmp/netgen_install/resources/dpdk/tx_worker",
        "/tmp/netgen_install/resources/dpdk/tx_worker/build",
    ]


def test_all_components_already_exist_does_not_raise(fake_worker):
    """Already-exists case: every mkdir raises IOError, but the walk
    must swallow each one and complete cleanly. The subsequent
    sftp.put will be the authoritative success/failure signal."""
    sftp = MagicMock()
    sftp.mkdir = MagicMock(side_effect=IOError("File exists"))
    # Must not raise
    fake_worker._sftp_makedirs(sftp, "/tmp/x/y/z")
    # All 4 components attempted (/tmp + 3 levels)
    assert sftp.mkdir.call_count == 4


def test_single_level_absolute_path(fake_worker):
    """Edge case: just /tmp itself → single mkdir attempt."""
    calls = []
    fake_worker._sftp_makedirs(_mock_sftp(calls), "/tmp")
    assert calls == ["/tmp"]


def test_ostg_docker_upload_path(fake_worker):
    """The other tree_uploads target in the worker: /tmp/netgen_install/
    ostg_docker. Verifies the walk handles the second recursive upload
    target the same way as resources/dpdk."""
    calls = []
    fake_worker._sftp_makedirs(_mock_sftp(calls),
                               "/tmp/netgen_install/ostg_docker")
    assert calls == [
        "/tmp",
        "/tmp/netgen_install",
        "/tmp/netgen_install/ostg_docker",
    ]


def test_double_slashes_are_normalised(fake_worker):
    """Defensive: f-strings in path construction occasionally produce
    '//' segments (e.g. ``f'{base}/{sub}/'`` when base ends with /).
    Empty path components must be skipped so we don't try to mkdir('')."""
    calls = []
    fake_worker._sftp_makedirs(_mock_sftp(calls),
                               "/tmp/netgen_install//resources//dpdk")
    assert calls == [
        "/tmp",
        "/tmp/netgen_install",
        "/tmp/netgen_install/resources",
        "/tmp/netgen_install/resources/dpdk",
    ]


def test_trailing_slash_does_not_create_empty_component(fake_worker):
    """If a caller passes ``/tmp/netgen_install/`` with the trailing /,
    the split produces an empty final component. Walk must skip it."""
    calls = []
    fake_worker._sftp_makedirs(_mock_sftp(calls),
                               "/tmp/netgen_install/")
    assert calls == ["/tmp", "/tmp/netgen_install"]
