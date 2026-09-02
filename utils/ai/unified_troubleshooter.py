"""
Unified Troubleshooter
Multi-domain troubleshooting: Network, Code, System, Integration
"""

import logging
from typing import Dict, List, Optional, Any
import traceback

logger = logging.getLogger(__name__)


class UnifiedTroubleshooter:
    """Unified troubleshooting for all domains"""
    
    def __init__(self, use_ai_api: bool = False, api_key: Optional[str] = None):
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        
        # Initialize domain-specific troubleshooters
        self.network_troubleshooter = None
        self.code_troubleshooter = None
        self.system_troubleshooter = None
        self.integration_troubleshooter = None
        
        # Initialize network troubleshooter
        try:
            from .network_troubleshooter import NetworkTroubleshooter
            from .config_knowledge_base import ConfigKnowledgeBase
            
            kb = ConfigKnowledgeBase()
            self.network_troubleshooter = NetworkTroubleshooter(
                knowledge_base=kb,
                use_ai_api=use_ai_api,
                api_key=api_key
            )
        except Exception as e:
            logger.warning(f"Network troubleshooter not available: {e}")
        
        # Initialize code troubleshooter
        # v0.5.245-followup (audit AI-*): the previous except branch retried the
        # exact same failing constructor, which just re-raised out of __init__.
        # Fall back to a no-AI CodeDebugger; if even that fails, leave the
        # attribute as None and let downstream methods degrade gracefully.
        try:
            from .code_debugger import CodeDebugger
            self.code_troubleshooter = CodeDebugger(use_ai_api=use_ai_api, api_key=api_key)
        except Exception as e:
            logger.debug(f"Code troubleshooter (AI mode) not available: {e}")
            try:
                from .code_debugger import CodeDebugger
                self.code_troubleshooter = CodeDebugger(use_ai_api=False)
            except Exception as fallback_err:
                logger.warning(f"Code troubleshooter not available at all: {fallback_err}")
                self.code_troubleshooter = None
    
    def troubleshoot(self, domain: str, issue: Dict[str, Any]) -> Dict[str, Any]:
        """
        Troubleshoot issues across domains
        
        Args:
            domain: Domain type (network, code, system, integration)
            issue: Issue description
                - For network: device_id, symptoms
                - For code: code, error_message, stack_trace
                - For system: symptoms, logs, metrics
                - For integration: service, error, logs
        
        Returns:
            Diagnosis dictionary with:
            - root_cause: Identified root cause
            - solutions: List of solutions
            - confidence: Confidence score (0-1)
            - commands: Commands to fix (if applicable)
        """
        if domain == "network":
            return self.troubleshoot_network(issue)
        elif domain == "code":
            return self.troubleshoot_code(issue)
        elif domain == "system":
            return self.troubleshoot_system(issue)
        elif domain == "integration":
            return self.troubleshoot_integration(issue)
        else:
            return {
                "error": f"Unknown domain: {domain}",
                "supported_domains": ["network", "code", "system", "integration"]
            }
    
    def troubleshoot_network(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """Troubleshoot network issues"""
        if not self.network_troubleshooter:
            return {
                "error": "Network troubleshooter not available",
                "root_cause": "Unknown",
                "solutions": []
            }
        
        device_id = issue.get("device_id")
        symptoms = issue.get("symptoms", {})
        
        if not device_id:
            return {
                "error": "device_id is required for network troubleshooting",
                "root_cause": "Unknown",
                "solutions": []
            }
        
        return self.network_troubleshooter.diagnose(device_id, symptoms)
    
    def troubleshoot_code(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """Troubleshoot code issues"""
        if not self.code_troubleshooter:
            return {
                "error": "Code troubleshooter not available",
                "root_cause": "Unknown",
                "solutions": []
            }
        
        code = issue.get("code", "")
        error_message = issue.get("error_message", "")
        stack_trace = issue.get("stack_trace", "")
        
        if not code and not error_message:
            return {
                "error": "code or error_message is required for code troubleshooting",
                "root_cause": "Unknown",
                "solutions": []
            }
        
        return self.code_troubleshooter.debug(code, error_message, stack_trace)
    
    def troubleshoot_system(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """Troubleshoot system issues"""
        symptoms = issue.get("symptoms", {})
        logs = issue.get("logs", "")
        metrics = issue.get("metrics", {})
        
        # Basic system troubleshooting
        diagnosis = {
            "root_cause": "Unknown",
            "solutions": [],
            "confidence": 0.5,
            "source": "system_troubleshooter"
        }
        
        # Check common system issues
        if symptoms.get("high_cpu"):
            diagnosis["root_cause"] = "High CPU usage detected"
            diagnosis["solutions"] = [
                "Check running processes: top or htop",
                "Identify CPU-intensive processes",
                "Consider process optimization or resource limits"
            ]
            diagnosis["confidence"] = 0.7
        
        if symptoms.get("high_memory"):
            diagnosis["root_cause"] = "High memory usage detected"
            diagnosis["solutions"] = [
                "Check memory usage: free -h",
                "Identify memory-intensive processes",
                "Consider increasing swap or optimizing memory usage"
            ]
            diagnosis["confidence"] = 0.7
        
        if symptoms.get("disk_full"):
            diagnosis["root_cause"] = "Disk space full"
            diagnosis["solutions"] = [
                "Check disk usage: df -h",
                "Identify large files: du -sh * | sort -h",
                "Clean up temporary files and logs",
                "Consider disk expansion"
            ]
            diagnosis["confidence"] = 0.9
        
        if symptoms.get("service_down"):
            service_name = symptoms.get("service_name", "service")
            diagnosis["root_cause"] = f"Service {service_name} is down"
            diagnosis["solutions"] = [
                f"Check service status: systemctl status {service_name}",
                f"View service logs: journalctl -u {service_name}",
                f"Restart service: systemctl restart {service_name}",
                "Check service configuration"
            ]
            diagnosis["confidence"] = 0.8
        
        # Analyze logs if provided
        if logs:
            log_analysis = self._analyze_logs(logs)
            if log_analysis:
                diagnosis["root_cause"] = log_analysis.get("root_cause", diagnosis["root_cause"])
                diagnosis["solutions"].extend(log_analysis.get("solutions", []))
                diagnosis["confidence"] = max(diagnosis["confidence"], log_analysis.get("confidence", 0.5))
        
        return diagnosis
    
    def troubleshoot_integration(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """Troubleshoot integration issues"""
        service = issue.get("service", "")
        error = issue.get("error", "")
        logs = issue.get("logs", "")
        
        diagnosis = {
            "root_cause": "Unknown integration issue",
            "solutions": [],
            "confidence": 0.5,
            "source": "integration_troubleshooter"
        }
        
        # Check common integration issues
        if "connection refused" in error.lower() or "connection timeout" in error.lower():
            diagnosis["root_cause"] = "Connection issue"
            diagnosis["solutions"] = [
                "Check if service is running",
                "Verify network connectivity",
                "Check firewall rules",
                "Verify service endpoint and port"
            ]
            diagnosis["confidence"] = 0.8
        
        if "authentication" in error.lower() or "unauthorized" in error.lower():
            diagnosis["root_cause"] = "Authentication failure"
            diagnosis["solutions"] = [
                "Verify credentials",
                "Check API keys or tokens",
                "Verify authentication configuration",
                "Check token expiration"
            ]
            diagnosis["confidence"] = 0.8
        
        if "404" in error or "not found" in error.lower():
            diagnosis["root_cause"] = "Resource not found"
            diagnosis["solutions"] = [
                "Verify resource path/URL",
                "Check if resource exists",
                "Verify API version",
                "Check routing configuration"
            ]
            diagnosis["confidence"] = 0.7
        
        # Analyze logs if provided
        if logs:
            log_analysis = self._analyze_logs(logs)
            if log_analysis:
                diagnosis["root_cause"] = log_analysis.get("root_cause", diagnosis["root_cause"])
                diagnosis["solutions"].extend(log_analysis.get("solutions", []))
        
        return diagnosis
    
    def _analyze_logs(self, logs: str) -> Optional[Dict[str, Any]]:
        """Analyze logs for patterns"""
        if not logs:
            return None
        
        log_lower = logs.lower()
        analysis = {
            "root_cause": "Unknown",
            "solutions": [],
            "confidence": 0.5
        }
        
        # Check for common error patterns
        if "error" in log_lower or "exception" in log_lower:
            analysis["root_cause"] = "Error detected in logs"
            analysis["solutions"] = [
                "Review error messages in logs",
                "Check for stack traces",
                "Verify configuration",
                "Check dependencies"
            ]
            analysis["confidence"] = 0.6
        
        if "timeout" in log_lower:
            analysis["root_cause"] = "Timeout detected"
            analysis["solutions"] = [
                "Increase timeout values",
                "Check network connectivity",
                "Optimize slow operations",
                "Check resource availability"
            ]
            analysis["confidence"] = 0.7
        
        if "permission denied" in log_lower or "access denied" in log_lower:
            analysis["root_cause"] = "Permission issue"
            analysis["solutions"] = [
                "Check file/directory permissions",
                "Verify user permissions",
                "Check SELinux/AppArmor policies",
                "Review access control lists"
            ]
            analysis["confidence"] = 0.8
        
        return analysis


class CodeDebugger:
    """Code debugging and error analysis"""
    
    def __init__(self, use_ai_api: bool = False, api_key: Optional[str] = None):
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        
        # Initialize AI client if using cloud API
        if use_ai_api and api_key:
            try:
                import openai
                import os
                # Support for OpenAI-compatible APIs (Groq, Together AI, etc.)
                base_url = os.environ.get("OPENAI_API_BASE", None)
                if base_url:
                    self.ai_client = openai.OpenAI(api_key=api_key, base_url=base_url)
                    logger.info(f"Using OpenAI-compatible API at: {base_url}")
                else:
                    self.ai_client = openai.OpenAI(api_key=api_key)
            except ImportError:
                logger.warning("OpenAI library not installed")
                self.ai_client = None
        else:
            self.ai_client = None
    
    def debug(self, code: str, error_message: str = "", stack_trace: str = "") -> Dict[str, Any]:
        """
        Debug code issues
        
        Args:
            code: Problematic code
            error_message: Error message
            stack_trace: Stack trace
        
        Returns:
            Diagnosis dictionary
        """
        diagnosis = {
            "root_cause": "Unknown",
            "solutions": [],
            "confidence": 0.5,
            "source": "code_debugger"
        }
        
        # Analyze error message
        if error_message:
            error_analysis = self._analyze_error(error_message, stack_trace)
            if error_analysis:
                diagnosis.update(error_analysis)
        
        # Analyze code if provided
        if code:
            code_analysis = self._analyze_code(code)
            if code_analysis:
                # Merge analysis
                if code_analysis.get("confidence", 0) > diagnosis.get("confidence", 0):
                    diagnosis["root_cause"] = code_analysis.get("root_cause", diagnosis["root_cause"])
                diagnosis["solutions"].extend(code_analysis.get("solutions", []))
        
        # Use AI if available
        if self.use_ai_api and self.ai_client:
            ai_analysis = self._ai_debug(code, error_message, stack_trace)
            if ai_analysis and ai_analysis.get("confidence", 0) > diagnosis.get("confidence", 0):
                diagnosis.update(ai_analysis)
        
        return diagnosis
    
    def _analyze_error(self, error_message: str, stack_trace: str) -> Optional[Dict[str, Any]]:
        """Analyze error message and stack trace"""
        error_lower = error_message.lower()
        analysis = {
            "root_cause": "Unknown",
            "solutions": [],
            "confidence": 0.5
        }
        
        # Common Python errors
        # v0.5.245-followup (audit AI-*): parenthesize `A or B and C` so the AND
        # binds tighter *inside* the second branch rather than pulling in the
        # first. Without the parens, "type" + "error" matched any error string
        # (they nearly all contain "error"), so e.g. "KeyError: 'user_type_id'"
        # got misclassified as "Type mismatch".
        if "nameerror" in error_lower or ("name" in error_lower and "not defined" in error_lower):
            analysis["root_cause"] = "Undefined variable or name"
            analysis["solutions"] = [
                "Check variable name spelling",
                "Verify variable is defined before use",
                "Check import statements",
                "Verify variable scope"
            ]
            analysis["confidence"] = 0.8

        # v0.5.245-followup (audit AI-*): "KeyError: 'user_type_id'" no longer
        # matches "Type mismatch" here (used to, because "type" and "error"
        # both appear in the message; the AND now stays inside the parens).
        if "typeerror" in error_lower or ("type" in error_lower and "error" in error_lower):
            analysis["root_cause"] = "Type mismatch"
            analysis["solutions"] = [
                "Check variable types",
                "Verify function parameter types",
                "Add type checking or conversion",
                "Review type hints"
            ]
            analysis["confidence"] = 0.7

        # v0.5.245-followup (audit AI-*): same parenthesization fix as above.
        if "attributeerror" in error_lower or ("attribute" in error_lower and "not found" in error_lower):
            analysis["root_cause"] = "Attribute not found"
            analysis["solutions"] = [
                "Check object type",
                "Verify attribute name",
                "Check if object has the attribute",
                "Review object documentation"
            ]
            analysis["confidence"] = 0.8
        
        if "indentationerror" in error_lower or "indentation" in error_lower:
            analysis["root_cause"] = "Indentation error"
            analysis["solutions"] = [
                "Check indentation consistency",
                "Use tabs or spaces consistently",
                "Verify block structure",
                "Use an IDE with indentation guides"
            ]
            analysis["confidence"] = 0.9
        
        if "syntaxerror" in error_lower or "syntax" in error_lower:
            analysis["root_cause"] = "Syntax error"
            analysis["solutions"] = [
                "Check for missing colons, parentheses, brackets",
                "Verify string quotes are matched",
                "Check for typos",
                "Review Python syntax rules"
            ]
            analysis["confidence"] = 0.8
        
        if "import" in error_lower and "error" in error_lower:
            analysis["root_cause"] = "Import error"
            analysis["solutions"] = [
                "Verify module is installed: pip install <module>",
                "Check module name spelling",
                "Verify Python path",
                "Check for circular imports"
            ]
            analysis["confidence"] = 0.8
        
        return analysis
    
    def _analyze_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Analyze code for common issues"""
        import ast
        
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return {
                "root_cause": "Syntax error in code",
                "solutions": ["Fix syntax errors before analysis"],
                "confidence": 0.9
            }
        
        issues = []
        
        # Check for common patterns
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id == "print" and len(node.args) > 0:
                        # Check for debug prints
                        issues.append("Consider using logging instead of print statements")
        
        if issues:
            return {
                "root_cause": "Code quality issues detected",
                "solutions": issues,
                "confidence": 0.6
            }
        
        return None
    
    def _ai_debug(self, code: str, error_message: str, stack_trace: str) -> Optional[Dict[str, Any]]:
        """Use AI to debug code"""
        if not self.ai_client:
            return None
        
        try:
            prompt = f"""Debug this code issue:

Code:
{code}

Error Message:
{error_message}

Stack Trace:
{stack_trace}

Provide:
1. Root cause analysis
2. Solutions to fix the issue
3. Confidence level (0-1)

Format as JSON with keys: root_cause, solutions (list), confidence"""
            
            response = self.ai_client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            return result
        except Exception as e:
            logger.error(f"AI debugging failed: {e}")
            return None




