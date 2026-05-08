"""
AI utilities for OSTG network troubleshooting and optimization
"""

import logging

logger = logging.getLogger(__name__)

from .network_troubleshooter import (
    NetworkTroubleshooter,
    NetworkConfigParser,
    ConfigKnowledgeBase,
    import_device_configs_from_ostg
)

from .network_test_framework import (
    NetworkTestFramework,
    TestCase,
    TestResult,
    TestReport,
    TestStatus,
    TestReportStorage
)

from .pytest_generator import PytestGenerator
from .pytest_runner import PytestRunner
from .code_generator import CodeGenerator

# Local AI pieces pull in optional heavy deps (numpy, sklearn, etc.).
# Import them lazily so the rest of the AI utilities (e.g., test plan generation)
# still work when those optional deps are not installed.
try:
    from .local_ai_engine import LocalAIEngine, LocalLLMClient
except Exception as e:  # pragma: no cover - defensive to keep AI features usable
    logger.warning("Local AI engine unavailable: %s", e)
    LocalAIEngine = None
    LocalLLMClient = None

# Advanced AI modules
from .advanced_code_generator import AdvancedCodeGenerator
from .code_analyzer import CodeAnalyzer
from .unified_troubleshooter import UnifiedTroubleshooter, CodeDebugger
from .comprehensive_test_framework import ComprehensiveTestFramework
from .intelligent_device_manager import IntelligentDeviceManager
from .proactive_assistant import ProactiveAIAssistant
from .network_analytics import NetworkAnalytics
from .test_plan_generator import TestPlanGenerator
from .pytest_device_runner import PytestDeviceRunner

__all__ = [
    'NetworkTroubleshooter',
    'NetworkConfigParser',
    'ConfigKnowledgeBase',
    'import_device_configs_from_ostg',
    'NetworkTestFramework',
    'TestCase',
    'TestResult',
    'TestReport',
    'TestStatus',
    'TestReportStorage',
    'PytestGenerator',
    'PytestRunner',
    'CodeGenerator',
    'LocalAIEngine',
    'LocalLLMClient',
    'AdvancedCodeGenerator',
    'CodeAnalyzer',
    'UnifiedTroubleshooter',
    'CodeDebugger',
    'ComprehensiveTestFramework',
    'IntelligentDeviceManager',
    'ProactiveAIAssistant',
    'NetworkAnalytics',
    'TestPlanGenerator',
    'PytestDeviceRunner'
]
