"""
Basic tests for utility functions that can run without Flask/Scapy/PyQt.
Focuses on pure-Python logic in utils/helpers.py and utils/path_utils.py.
"""

import pytest
import os
import sys


# ---------------------------------------------------------------------------
# Import checks
# ---------------------------------------------------------------------------

class TestImports:
    """Verify that key project modules are importable."""

    def test_import_utils_helpers(self):
        from utils import helpers
        assert hasattr(helpers, "increment_ip")
        assert hasattr(helpers, "increment_mac")

    def test_import_utils_path_utils(self):
        from utils import path_utils
        assert hasattr(path_utils, "get_ostg_data_directory")
        assert hasattr(path_utils, "get_session_file_path")

    def test_import_utils_package(self):
        import utils
        assert utils is not None

    def test_import_ostg_package(self):
        import ostg
        assert ostg is not None


# ---------------------------------------------------------------------------
# utils/helpers.py - increment_ip
# ---------------------------------------------------------------------------

class TestIncrementIP:
    """Tests for helpers.increment_ip."""

    def test_basic_increment(self):
        from utils.helpers import increment_ip
        assert increment_ip("10.0.0.1") == "10.0.0.2"

    def test_increment_with_step(self):
        from utils.helpers import increment_ip
        assert increment_ip("10.0.0.1", step=10) == "10.0.0.11"

    def test_increment_rolls_octet(self):
        from utils.helpers import increment_ip
        assert increment_ip("10.0.0.255") == "10.0.1.0"

    def test_increment_zero_step(self):
        from utils.helpers import increment_ip
        assert increment_ip("192.168.1.1", step=0) == "192.168.1.1"

    def test_invalid_ip_returns_original(self):
        from utils.helpers import increment_ip
        assert increment_ip("not_an_ip") == "not_an_ip"


# ---------------------------------------------------------------------------
# utils/helpers.py - increment_ipv6
# ---------------------------------------------------------------------------

class TestIncrementIPv6:
    """Tests for helpers.increment_ipv6."""

    def test_basic_increment(self):
        from utils.helpers import increment_ipv6
        result = increment_ipv6("::1")
        assert result == "::2"

    def test_invalid_returns_original(self):
        from utils.helpers import increment_ipv6
        assert increment_ipv6("not_ipv6") == "not_ipv6"


# ---------------------------------------------------------------------------
# utils/helpers.py - increment_mac
# ---------------------------------------------------------------------------

class TestIncrementMAC:
    """Tests for helpers.increment_mac."""

    def test_basic_increment(self):
        from utils.helpers import increment_mac
        result = increment_mac("00:00:00:00:00:01")
        assert result == "00:00:00:00:00:02"

    def test_increment_with_step(self):
        from utils.helpers import increment_mac
        result = increment_mac("00:00:00:00:00:01", step=255)
        assert result == "00:00:00:00:01:00"

    def test_invalid_mac_returns_original(self):
        from utils.helpers import increment_mac
        assert increment_mac("not_a_mac") == "not_a_mac"

    def test_mac_wrap_around(self):
        from utils.helpers import increment_mac
        result = increment_mac("ff:ff:ff:ff:ff:ff", step=1)
        assert result == "00:00:00:00:00:00"


# ---------------------------------------------------------------------------
# utils/path_utils.py
# ---------------------------------------------------------------------------

class TestPathUtils:
    """Tests for path_utils functions."""

    def test_get_ostg_data_directory_returns_string(self):
        from utils.path_utils import get_ostg_data_directory
        result = get_ostg_data_directory()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_session_file_path_ends_with_json(self):
        from utils.path_utils import get_session_file_path
        result = get_session_file_path()
        assert result.endswith("session.json")

    def test_get_logs_directory_returns_string(self):
        from utils.path_utils import get_logs_directory
        result = get_logs_directory()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Project structure validation
# ---------------------------------------------------------------------------

class TestProjectStructure:
    """Verify expected project directories and files exist."""

    def test_utils_dir_exists(self, utils_dir):
        assert os.path.isdir(utils_dir)

    def test_config_dir_exists(self, config_dir):
        assert os.path.isdir(config_dir)

    def test_requirements_txt_exists(self, project_root):
        assert os.path.isfile(os.path.join(project_root, "requirements.txt"))

    def test_ostg_package_exists(self, project_root):
        assert os.path.isdir(os.path.join(project_root, "ostg"))

    def test_utils_init_exists(self, utils_dir):
        assert os.path.isfile(os.path.join(utils_dir, "__init__.py"))
