"""
AI-Powered Code Analyzer
Code quality analysis, security scanning, performance optimization
"""

import ast
import re
import logging
from typing import Dict, List, Optional, Any, Tuple
import json

logger = logging.getLogger(__name__)


class CodeAnalyzer:
    """Comprehensive code analysis and quality checking"""
    
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
    
    def analyze(self, code: str, language: str = "python") -> Dict[str, Any]:
        """
        Comprehensive code analysis
        
        Returns:
            Dictionary with:
            - quality_score: Overall quality score (0-100)
            - security_issues: List of security vulnerabilities
            - performance_issues: List of performance problems
            - best_practices: List of best practice violations
            - suggestions: List of improvement suggestions
            - complexity: Code complexity metrics
        """
        if language == "python":
            return self._analyze_python(code)
        else:
            return {
                "quality_score": 0,
                "error": f"Analysis for {language} not yet implemented",
                "security_issues": [],
                "performance_issues": [],
                "best_practices": [],
                "suggestions": []
            }
    
    def _analyze_python(self, code: str) -> Dict[str, Any]:
        """Analyze Python code"""
        issues = []
        security_issues = []
        performance_issues = []
        best_practices = []
        suggestions = []
        
        # Parse code
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return {
                "quality_score": 0,
                "error": f"Syntax error: {str(e)}",
                "security_issues": [],
                "performance_issues": [],
                "best_practices": [],
                "suggestions": [f"Fix syntax error: {str(e)}"]
            }
        
        # Security checks
        security_issues.extend(self._check_security(code, tree))
        
        # Performance checks
        performance_issues.extend(self._check_performance(code, tree))
        
        # Best practice checks
        best_practices.extend(self._check_best_practices(code, tree))
        
        # Code complexity
        complexity = self._calculate_complexity(tree)
        
        # Generate suggestions
        suggestions.extend(self._generate_suggestions(security_issues, performance_issues, best_practices))
        
        # Calculate quality score
        quality_score = self._calculate_quality_score(
            security_issues, performance_issues, best_practices, complexity
        )
        
        return {
            "quality_score": quality_score,
            "security_issues": security_issues,
            "performance_issues": performance_issues,
            "best_practices": best_practices,
            "suggestions": suggestions,
            "complexity": complexity
        }
    
    def _check_security(self, code: str, tree: ast.AST) -> List[Dict]:
        """Check for security vulnerabilities"""
        issues = []
        
        # Check for dangerous functions
        dangerous_functions = [
            "eval", "exec", "compile", "__import__", "input",
            "pickle.loads", "yaml.load", "subprocess.call"
        ]
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in dangerous_functions:
                        issues.append({
                            "type": "security",
                            "severity": "high",
                            "message": f"Use of dangerous function: {node.func.id}",
                            "line": getattr(node, "lineno", 0),
                            "suggestion": f"Consider safer alternative for {node.func.id}"
                        })
        
        # Check for hardcoded secrets
        secret_patterns = [
            (r'password\s*=\s*["\']([^"\']+)["\']', "Hardcoded password"),
            (r'api_key\s*=\s*["\']([^"\']+)["\']', "Hardcoded API key"),
            (r'secret\s*=\s*["\']([^"\']+)["\']', "Hardcoded secret"),
            (r'token\s*=\s*["\']([^"\']+)["\']', "Hardcoded token"),
        ]
        
        for pattern, description in secret_patterns:
            matches = re.finditer(pattern, code, re.IGNORECASE)
            for match in matches:
                issues.append({
                    "type": "security",
                    "severity": "critical",
                    "message": description,
                    "line": code[:match.start()].count('\n') + 1,
                    "suggestion": "Use environment variables or secure credential storage"
                })
        
        # Check for SQL injection risks
        sql_patterns = [
            (r'execute\s*\(\s*["\']([^"\']*%[^"\']*)["\']', "Potential SQL injection"),
            (r'query\s*=\s*["\']([^"\']*%[^"\']*)["\']', "Potential SQL injection"),
        ]
        
        for pattern, description in sql_patterns:
            matches = re.finditer(pattern, code, re.IGNORECASE)
            for match in matches:
                issues.append({
                    "type": "security",
                    "severity": "high",
                    "message": description,
                    "line": code[:match.start()].count('\n') + 1,
                    "suggestion": "Use parameterized queries or ORM"
                })
        
        return issues
    
    def _check_performance(self, code: str, tree: ast.AST) -> List[Dict]:
        """Check for performance issues"""
        issues = []
        
        # Check for nested loops
        loop_depth = 0
        max_depth = 0
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                loop_depth += 1
                max_depth = max(max_depth, loop_depth)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                if loop_depth > 0:
                    loop_depth = 0
        
        if max_depth > 2:
            issues.append({
                "type": "performance",
                "severity": "medium",
                "message": f"Deeply nested loops (depth: {max_depth})",
                "line": 0,
                "suggestion": "Consider refactoring to reduce nesting or use vectorized operations"
            })
        
        # Check for inefficient string operations
        if code.count('+') > code.count('join'):
            # Count string concatenations in loops
            for node in ast.walk(tree):
                if isinstance(node, ast.For):
                    # Check if loop contains string concatenation
                    for child in ast.walk(node):
                        if isinstance(child, ast.BinOp) and isinstance(child.op, ast.Add):
                            issues.append({
                                "type": "performance",
                                "severity": "low",
                                "message": "String concatenation in loop",
                                "line": getattr(node, "lineno", 0),
                                "suggestion": "Use ''.join() for better performance"
                            })
                            break
        
        return issues
    
    def _check_best_practices(self, code: str, tree: ast.AST) -> List[Dict]:
        """Check for best practice violations"""
        issues = []
        
        # Check for missing docstrings
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                if not ast.get_docstring(node):
                    issues.append({
                        "type": "best_practice",
                        "severity": "low",
                        "message": f"Missing docstring for {node.name}",
                        "line": getattr(node, "lineno", 0),
                        "suggestion": "Add docstring describing the function/class"
                    })
        
        # Check for bare except clauses
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    issues.append({
                        "type": "best_practice",
                        "severity": "medium",
                        "message": "Bare except clause",
                        "line": getattr(node, "lineno", 0),
                        "suggestion": "Specify exception type (e.g., except ValueError:)"
                    })
        
        # Check for unused imports
        imports = set()
        used_names = set()
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imports.add(alias.asname or alias.name.split('.')[0])
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    used_names.add(node.id)
        
        unused = imports - used_names
        if unused:
            issues.append({
                "type": "best_practice",
                "severity": "low",
                "message": f"Unused imports: {', '.join(unused)}",
                "line": 0,
                "suggestion": "Remove unused imports"
            })
        
        return issues
    
    def _calculate_complexity(self, tree: ast.AST) -> Dict[str, int]:
        """Calculate code complexity metrics"""
        complexity = 1  # Base complexity
        max_depth = 0
        function_count = 0
        class_count = 0
        
        def visit_node(node, depth=0):
            nonlocal complexity, max_depth, function_count, class_count
            
            max_depth = max(max_depth, depth)
            
            if isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
                complexity += 1
            
            if isinstance(node, ast.FunctionDef):
                function_count += 1
            
            if isinstance(node, ast.ClassDef):
                class_count += 1
            
            for child in ast.iter_child_nodes(node):
                visit_node(child, depth + 1)
        
        visit_node(tree)
        
        return {
            "cyclomatic_complexity": complexity,
            "max_nesting_depth": max_depth,
            "function_count": function_count,
            "class_count": class_count
        }
    
    def _generate_suggestions(self, security_issues: List[Dict],
                             performance_issues: List[Dict],
                             best_practices: List[Dict]) -> List[str]:
        """Generate improvement suggestions"""
        suggestions = []
        
        if security_issues:
            critical = [i for i in security_issues if i.get("severity") == "critical"]
            if critical:
                suggestions.append(f"Fix {len(critical)} critical security issue(s)")
        
        if performance_issues:
            suggestions.append(f"Address {len(performance_issues)} performance issue(s)")
        
        if best_practices:
            suggestions.append(f"Improve {len(best_practices)} best practice violation(s)")
        
        return suggestions
    
    def _calculate_quality_score(self, security_issues: List[Dict],
                                performance_issues: List[Dict],
                                best_practices: List[Dict],
                                complexity: Dict) -> int:
        """Calculate overall quality score (0-100)"""
        score = 100
        
        # Deduct for security issues
        for issue in security_issues:
            if issue.get("severity") == "critical":
                score -= 20
            elif issue.get("severity") == "high":
                score -= 10
            elif issue.get("severity") == "medium":
                score -= 5
        
        # Deduct for performance issues
        for issue in performance_issues:
            if issue.get("severity") == "high":
                score -= 5
            elif issue.get("severity") == "medium":
                score -= 3
            else:
                score -= 1
        
        # Deduct for best practice violations
        score -= len(best_practices) * 2
        
        # Deduct for high complexity
        if complexity.get("cyclomatic_complexity", 0) > 20:
            score -= 10
        elif complexity.get("cyclomatic_complexity", 0) > 10:
            score -= 5
        
        return max(0, min(100, score))
    
    def detect_vulnerabilities(self, code: str, language: str = "python") -> List[Dict]:
        """Detect security vulnerabilities"""
        analysis = self.analyze(code, language)
        return analysis.get("security_issues", [])
    
    def suggest_optimizations(self, code: str, language: str = "python") -> List[str]:
        """Suggest performance optimizations"""
        analysis = self.analyze(code, language)
        optimizations = []
        
        for issue in analysis.get("performance_issues", []):
            optimizations.append(issue.get("suggestion", ""))
        
        return [opt for opt in optimizations if opt]




