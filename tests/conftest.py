import pytest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def project_root():
    """Return the project root directory path."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def utils_dir(project_root):
    """Return the utils directory path."""
    return os.path.join(project_root, "utils")


@pytest.fixture
def config_dir(project_root):
    """Return the config directory path."""
    return os.path.join(project_root, "config")
