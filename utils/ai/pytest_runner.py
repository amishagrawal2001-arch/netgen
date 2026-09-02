"""
Pytest Runner for executing pytest scripts
"""

import os
import subprocess
import logging
import json
import tempfile
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class PytestRunner:
    """Run pytest scripts and collect results"""

    # v0.5.245-followup (audit AI-*): default install path + user-writable fallback
    _DEFAULT_SCRIPTS_DIR = Path("/opt/OSTG/pytest_scripts")

    def __init__(self, pytest_path: str = "pytest"):
        self.pytest_path = pytest_path
        # v0.5.245-followup (audit AI-*): pick a writable scripts_dir but do NOT
        # create it on import — create lazily on first write so non-root installs
        # (and list/get/delete callers that don't need the dir) don't blow up.
        env_dir = os.environ.get("NETGEN_DATA_DIR")
        if env_dir:
            self.scripts_dir = Path(env_dir).expanduser() / "pytest_scripts"
        else:
            self.scripts_dir = self._DEFAULT_SCRIPTS_DIR
        self._scripts_dir_ready = False

    def _ensure_scripts_dir(self) -> Path:
        """Create the scripts directory on first write, falling back to ~ on PermissionError."""
        # v0.5.245-followup (audit AI-*): lazy mkdir with fallback
        if self._scripts_dir_ready:
            return self.scripts_dir
        try:
            self.scripts_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            fallback = Path.home() / ".netgen" / "pytest_scripts"
            logger.warning(
                "Cannot create pytest scripts dir %s (%s); falling back to %s",
                self.scripts_dir, e, fallback,
            )
            fallback.mkdir(parents=True, exist_ok=True)
            self.scripts_dir = fallback
        self._scripts_dir_ready = True
        return self.scripts_dir
    
    def run_pytest_script(self, script_content: str, script_name: Optional[str] = None,
                         additional_args: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Run a pytest script and return results
        
        Args:
            script_content: Python pytest script content
            script_name: Optional name for the script file
            additional_args: Additional pytest arguments
        
        Returns:
            Dictionary with test results
        """
        if not script_name:
            script_name = f"test_{int(datetime.now().timestamp())}.py"

        # v0.5.245-followup (audit AI-*): create scripts_dir lazily on first write
        scripts_dir = self._ensure_scripts_dir()
        # Save script to temporary file
        script_path = scripts_dir / script_name

        try:
            # Write script
            with open(script_path, 'w') as f:
                f.write(script_content)
            
            # Run pytest
            result = self._execute_pytest(script_path, additional_args or [])
            
            return {
                "success": result["returncode"] == 0,
                "script_path": str(script_path),
                "script_name": script_name,
                "returncode": result["returncode"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "tests_passed": result.get("tests_passed", 0),
                "tests_failed": result.get("tests_failed", 0),
                "tests_total": result.get("tests_total", 0),
                "duration": result.get("duration", 0)
            }
        except Exception as e:
            logger.error(f"Failed to run pytest script: {e}")
            return {
                "success": False,
                "error": str(e),
                "script_path": str(script_path) if script_path.exists() else None
            }
    
    def run_pytest_file(self, file_path: str, additional_args: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Run a pytest script from file
        
        Args:
            file_path: Path to pytest script file
            additional_args: Additional pytest arguments
        
        Returns:
            Dictionary with test results
        """
        script_path = Path(file_path)
        
        if not script_path.exists():
            return {
                "success": False,
                "error": f"Script file not found: {file_path}"
            }
        
        try:
            result = self._execute_pytest(script_path, additional_args or [])
            
            return {
                "success": result["returncode"] == 0,
                "script_path": str(script_path),
                "returncode": result["returncode"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "tests_passed": result.get("tests_passed", 0),
                "tests_failed": result.get("tests_failed", 0),
                "tests_total": result.get("tests_total", 0),
                "duration": result.get("duration", 0)
            }
        except Exception as e:
            logger.error(f"Failed to run pytest file: {e}")
            return {
                "success": False,
                "error": str(e),
                "script_path": str(script_path)
            }
    
    def _execute_pytest(self, script_path: Path, additional_args: List[str]) -> Dict[str, Any]:
        """Execute pytest and parse results"""
        # v0.5.245-followup (audit AI-*): per-run unique report path so parallel
        # runs (and re-runs after a crash) can't read stale JSON. Also delete the
        # file up-front in case something already staked its claim.
        report_dir = Path(tempfile.mkdtemp(prefix="netgen_pytest_"))
        json_report_path = report_dir / "report.json"
        try:
            json_report_path.unlink()
        except FileNotFoundError:
            pass

        # Build pytest command
        cmd = [
            self.pytest_path, str(script_path),
            "-v", "--tb=short",
            "--json-report",
            f"--json-report-file={json_report_path}",
        ]
        cmd.extend(additional_args)

        # v0.5.245-followup (audit AI-*): reserve cleanup so a mid-run raise
        # still removes the per-run temp dir.
        import shutil as _shutil
        try:
            # Run pytest
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes max
            )

            # Parse results
            result = {
                "returncode": process.returncode,
                "stdout": process.stdout,
                "stderr": process.stderr
            }

            # Try to parse JSON report if available
            report_ok = False
            if json_report_path.exists() and json_report_path.stat().st_size > 0:
                try:
                    with open(json_report_path, 'r') as f:
                        json_report = json.load(f)
                        result["tests_passed"] = json_report.get("summary", {}).get("passed", 0)
                        result["tests_failed"] = json_report.get("summary", {}).get("failed", 0)
                        result["tests_total"] = json_report.get("summary", {}).get("total", 0)
                        result["duration"] = json_report.get("duration", 0)
                        report_ok = True
                except Exception as e:
                    logger.warning("Failed to parse pytest json report %s: %s", json_report_path, e)

            # Fallback: parse from stdout
            if "tests_passed" not in result:
                import re
                # Try to extract test counts from stdout
                passed_match = re.search(r'(\d+) passed', process.stdout)
                failed_match = re.search(r'(\d+) failed', process.stdout)

                if passed_match:
                    result["tests_passed"] = int(passed_match.group(1))
                if failed_match:
                    result["tests_failed"] = int(failed_match.group(1))

                if "tests_passed" in result and "tests_failed" in result:
                    result["tests_total"] = result["tests_passed"] + result["tests_failed"]

            # v0.5.245-followup (audit AI-*): if pytest failed AND we have neither a
            # report nor parsed counts, surface stdout/stderr so callers don't
            # mistake "nothing to parse" for success. Common cause: pytest-json-report
            # not installed, or the script itself failed to import.
            if process.returncode != 0 and not report_ok and "tests_passed" not in result:
                tail_out = (process.stdout or "").strip().splitlines()[-40:]
                tail_err = (process.stderr or "").strip().splitlines()[-40:]
                result["error"] = (
                    f"pytest exited {process.returncode} with no parseable results. "
                    f"stdout tail: {tail_out!r}; stderr tail: {tail_err!r}"
                )
                result["tests_passed"] = 0
                result["tests_failed"] = 0
                result["tests_total"] = 0

            return result
        except subprocess.TimeoutExpired:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": "Pytest execution timed out",
                "tests_passed": 0,
                "tests_failed": 0,
                "tests_total": 0
            }
        except Exception as e:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "tests_passed": 0,
                "tests_failed": 0,
                "tests_total": 0
            }
        finally:
            # v0.5.245-followup (audit AI-*): drop the per-run report dir
            _shutil.rmtree(report_dir, ignore_errors=True)

    def list_scripts(self) -> List[Dict[str, Any]]:
        """List all pytest scripts"""
        scripts = []

        # v0.5.245-followup (audit AI-*): tolerate missing dir (nothing written yet)
        if not self.scripts_dir.exists():
            return scripts

        for script_file in self.scripts_dir.glob("*.py"):
            try:
                stat = script_file.stat()
                scripts.append({
                    "name": script_file.name,
                    "path": str(script_file),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
            except Exception as e:
                logger.warning(f"Failed to get info for {script_file}: {e}")
        
        return scripts
    
    def get_script_content(self, script_name: str) -> Optional[str]:
        """Get content of a pytest script"""
        script_path = self.scripts_dir / script_name
        
        if not script_path.exists():
            return None
        
        try:
            with open(script_path, 'r') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read script {script_name}: {e}")
            return None
    
    def delete_script(self, script_name: str) -> bool:
        """Delete a pytest script"""
        script_path = self.scripts_dir / script_name
        
        if not script_path.exists():
            return False
        
        try:
            script_path.unlink()
            logger.info(f"Deleted pytest script: {script_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete script {script_name}: {e}")
            return False




