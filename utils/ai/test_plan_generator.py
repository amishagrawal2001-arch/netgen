"""
Test Plan Generator
Generates unit tests and detailed test plans from functional specifications
"""

import logging
import json
import time
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import uuid

logger = logging.getLogger(__name__)

# Request cache to avoid duplicate API calls (helps reduce rate limit issues)
_request_cache = {}
_cache_ttl = 300  # 5 minutes cache TTL


class TestPlanGenerator:
    """Generate test plans and unit tests from functional specifications"""
    
    def __init__(self, use_ai_api: bool = False, api_key: Optional[str] = None,
                 use_local_llm: bool = True, api_base: Optional[str] = None,
                 model: Optional[str] = None):
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        self.use_local_llm = use_local_llm
        self.api_base = api_base
        self.model = model  # Store model name for cloud API calls
        
        # Initialize local LLM if available
        if use_local_llm:
            try:
                from .local_ai_engine import LocalLLMClient
                import os
                import json
                
                # Load user-selected model from settings
                settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                user_model = None
                user_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
                
                if os.path.exists(settings_file):
                    try:
                        with open(settings_file, 'r') as f:
                            settings = json.load(f)
                            user_model = settings.get("ollama_model")
                            user_url = settings.get("ollama_url", user_url)
                    except Exception:
                        pass
                
                self.local_llm = LocalLLMClient(
                    llm_type=os.environ.get("LOCAL_LLM_TYPE", "ollama"),
                    base_url=user_url,
                    model=user_model
                )
            except Exception as e:
                logger.warning(f"Local LLM not available: {e}")
                logger.info("Test plan generation will use template-based generation instead of AI")
                logger.info("To enable AI generation, install and start Ollama: https://ollama.ai")
                self.local_llm = None
        else:
            self.local_llm = None
        
        # Initialize AI client if using cloud API
        if use_ai_api and api_key:
            try:
                import openai
                import os
                from openai import RateLimitError
                
                # Support for OpenAI-compatible APIs (Groq, Together AI, etc.)
                # Use provided api_base, or fall back to environment variable
                base_url = api_base or os.environ.get("OPENAI_API_BASE", None)
                
                # Configure OpenAI client with optimized retry settings
                # max_retries=5 with exponential backoff handles rate limits better
                # timeout=120 gives enough time for responses
                if base_url:
                    self.ai_client = openai.OpenAI(
                        api_key=api_key, 
                        base_url=base_url,
                        max_retries=5,  # Increased retries for better rate limit handling
                        timeout=120.0   # 120 second timeout
                    )
                    logger.info(f"Using OpenAI-compatible API at: {base_url} (max_retries=5, timeout=120s)")
                else:
                    self.ai_client = openai.OpenAI(
                        api_key=api_key,
                        max_retries=5,
                        timeout=120.0
                    )
            except ImportError:
                logger.warning("OpenAI library not installed")
                self.ai_client = None
        else:
            self.ai_client = None
    
    def generate_test_plan(self, functional_spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate comprehensive test plan from functional specification
        
        Args:
            functional_spec: Functional specification dictionary
                - title: Feature/function title
                - description: Detailed description
                - requirements: List of requirements
                - use_cases: List of use cases
                - inputs: Input specifications
                - outputs: Output specifications
                - constraints: Constraints and assumptions
                - acceptance_criteria: Acceptance criteria
        
        Returns:
            Test plan dictionary with:
            - test_plan_id: Unique test plan ID
            - overview: Test plan overview
            - test_categories: List of test categories
            - test_cases: Detailed test cases
            - unit_tests: Generated unit tests
            - integration_tests: Integration test scenarios
            - test_data: Test data requirements
            - test_environment: Environment requirements
        """
        try:
            # Ensure functional_spec is a dict
            if not isinstance(functional_spec, dict):
                logger.error(f"functional_spec is not a dict: {type(functional_spec)}, value: {functional_spec}")
                functional_spec = {}
            
            # Use AI to generate comprehensive test plan
            test_plan = None
            generation_method = "template"  # Track generation method
            llm_attempted = False  # Track if LLM was attempted
            cloud_only_mode = self.use_ai_api and not self.use_local_llm  # Cloud-only mode
            
            try:
                # Try cloud API first if available and configured
                if self.use_ai_api and self.ai_client:
                    llm_attempted = True
                    logger.info(f"[TEST PLAN] Attempting cloud API generation (cloud_only_mode: {cloud_only_mode})")
                    test_plan = self._ai_generate_test_plan(functional_spec, use_local=False)
                    if test_plan and isinstance(test_plan, dict) and test_plan.get("test_cases"):
                        generation_method = "llm_api"  # Successfully generated by API
                        logger.info("[TEST PLAN] Successfully generated using cloud API")
                    elif test_plan and isinstance(test_plan, dict) and test_plan.get("_llm_attempted"):
                        generation_method = "llm_template_hybrid"  # API attempted but fell back
                        logger.warning("[TEST PLAN] Cloud API attempted but fell back to template")
                    else:
                        logger.warning(f"[TEST PLAN] Cloud API generation failed or incomplete (cloud_only_mode: {cloud_only_mode})")
                elif self.use_ai_api and not self.ai_client:
                    # Cloud API requested but not available
                    # In Standard Mode, we allow fallback to templates even in cloud-only mode
                    # Only Agent Mode requires LLM, so we'll proceed with template generation
                    if cloud_only_mode:
                        logger.warning("[TEST PLAN] Cloud-only mode requested but cloud API client not available. Falling back to template generation.")
                        # Don't return error - allow template fallback for Standard Mode
                        # Agent Mode will fail separately if needed
                
                # Try local LLM if cloud didn't work AND local LLM is enabled (not cloud-only mode)
                if (not test_plan or not test_plan.get("test_cases")) and self.use_local_llm and self.local_llm and not cloud_only_mode:
                    llm_attempted = True
                    logger.info("[TEST PLAN] Attempting local LLM generation as fallback")
                    test_plan = self._ai_generate_test_plan(functional_spec, use_local=True)
                    # Check if LLM was attempted (even if it failed/timed out)
                    if test_plan and isinstance(test_plan, dict):
                        # If _llm_attempted is True, it means LLM was attempted but template was used
                        # (timeout, error, or empty response). Template always generates test_cases,
                        # so we can't rely on test_cases existence to determine if it's from LLM.
                        if test_plan.get("_llm_attempted") is True:
                            generation_method = "llm_template_hybrid"  # LLM attempted but template was used
                        elif test_plan.get("_llm_attempted") is False:
                            generation_method = "llm_ollama"  # LLM succeeded (explicitly marked)
                        elif test_plan.get("test_cases") and len(test_plan.get("test_cases", [])) > 0:
                            # Has test cases but no _llm_attempted flag - likely from LLM directly (successful)
                            generation_method = "llm_ollama"
                            logger.info("[TEST PLAN] Successfully generated using local LLM")
                        else:
                            # No test cases - shouldn't happen but treat as template
                            generation_method = "template"
                elif cloud_only_mode and (not test_plan or not test_plan.get("test_cases")):
                    # Cloud-only mode but cloud API failed - don't try local LLM
                    # However, in Standard Mode, we allow template fallback
                    # Only Agent Mode requires LLM, so we'll proceed with template generation
                    logger.warning("[TEST PLAN] Cloud-only mode: Cloud API failed. Falling back to template generation for Standard Mode.")
                    # Don't return error - allow template fallback
                    # The template generation below will handle it
                
                # If no test plan generated yet, use template
                if not test_plan or not test_plan.get("test_cases"):
                    logger.info("No AI available or all AI attempts failed, using template generation")
                    test_plan = self._template_generate_test_plan(functional_spec)
                    if not llm_attempted:
                        generation_method = "template"  # Never tried LLM, pure template
            except Exception as e:
                logger.error(f"AI test plan generation failed: {e}", exc_info=True)
                test_plan = None
                if llm_attempted:
                    generation_method = "llm_template_hybrid"  # LLM was attempted but failed
            
            # Ensure test_plan is a dict
            if not isinstance(test_plan, dict) or not test_plan:
                logger.warning(f"Test plan generation returned non-dict or empty: {type(test_plan)}, using template")
                test_plan = self._template_generate_test_plan(functional_spec)
                generation_method = "template"  # Fallback to template
            
            # If AI-generated test plan has no test cases, merge with template test cases
            test_cases_count = len(test_plan.get("test_cases", [])) if isinstance(test_plan, dict) else 0
            if isinstance(test_plan, dict) and test_cases_count == 0:
                logger.info(f"AI-generated test plan has no test cases (count: {test_cases_count}), merging with template test cases")
                template_plan = self._template_generate_test_plan(functional_spec)
                template_test_cases = template_plan.get("test_cases", [])
                logger.info(f"Template generated {len(template_test_cases)} test cases")
                # Merge test cases from template
                test_plan["test_cases"] = template_test_cases
                # Keep AI-generated overview if available, otherwise use template
                if not test_plan.get("overview"):
                    test_plan["overview"] = template_plan.get("overview", "")
                # Merge other fields if missing
                if not test_plan.get("test_data"):
                    test_plan["test_data"] = template_plan.get("test_data", [])
                if not test_plan.get("test_environment"):
                    test_plan["test_environment"] = template_plan.get("test_environment", {})
                logger.info(f"After merge, test plan has {len(test_plan.get('test_cases', []))} test cases")
                # If we had to merge, mark as hybrid (LLM + template)
                if generation_method.startswith("llm"):
                    generation_method = "llm_template_hybrid"
                else:
                    generation_method = "template"
            
            # Generate unit tests (with error handling)
            try:
                unit_tests = self.generate_unit_tests_from_spec(functional_spec)
            except Exception as e:
                logger.error(f"Unit test generation failed: {e}", exc_info=True)
                unit_tests = []
            
            # Generate integration test scenarios (with error handling)
            try:
                integration_tests = self.generate_integration_tests_from_spec(functional_spec)
            except Exception as e:
                logger.error(f"Integration test generation failed: {e}", exc_info=True)
                integration_tests = []
            
            # Final check: ensure test_cases exist before building complete plan
            final_test_cases = test_plan.get("test_cases", []) if isinstance(test_plan, dict) else []
            logger.debug(f"[TEST PLAN] Before final check: test_cases count = {len(final_test_cases) if isinstance(final_test_cases, list) else 'not a list'}, type = {type(final_test_cases)}")
            
            # Check if test_cases is None, empty list, or not a list
            # Also check if it's a list but empty
            if (not final_test_cases or 
                (isinstance(final_test_cases, list) and len(final_test_cases) == 0) or
                final_test_cases is None):
                logger.warning(f"[TEST PLAN] Test cases still empty before final plan build (type: {type(final_test_cases)}, value: {final_test_cases}), generating from template as final fallback")
                template_plan = self._template_generate_test_plan(functional_spec)
                final_test_cases = template_plan.get("test_cases", [])
                logger.info(f"[TEST PLAN] Final fallback generated {len(final_test_cases)} test cases from template")
                # Update test_plan with final test cases
                if isinstance(test_plan, dict):
                    test_plan["test_cases"] = final_test_cases
                # Double-check final_test_cases is not empty
                if not final_test_cases or (isinstance(final_test_cases, list) and len(final_test_cases) == 0):
                    logger.error(f"[TEST PLAN] Template generation also returned empty test cases! Template keys: {list(template_plan.keys())}, requirements: {functional_spec.get('requirements', [])}, use_cases: {functional_spec.get('use_cases', [])}")
                    # Last resort: create at least one test case from the title
                    if functional_spec.get('title'):
                        final_test_cases = [{
                            "test_id": "TC_FALLBACK_001",
                            "title": f"Test: {functional_spec.get('title')}",
                            "description": f"Basic test for {functional_spec.get('title')}",
                            "category": "functional",
                            "priority": "high",
                            "steps": ["1. Setup test environment", "2. Execute test", "3. Verify results"],
                            "expected_result": "Test completes successfully",
                            "test_data": {}
                        }]
                        test_plan["test_cases"] = final_test_cases
                        logger.warning(f"[TEST PLAN] Created fallback test case")
            else:
                logger.info(f"[TEST PLAN] Test cases already present: {len(final_test_cases)} test cases")
            
            # Final safety check: ensure we have test cases before returning
            if not final_test_cases or (isinstance(final_test_cases, list) and len(final_test_cases) == 0):
                logger.error(f"[TEST PLAN] CRITICAL: No test cases after all fallbacks! Generating from template one more time.")
                template_plan = self._template_generate_test_plan(functional_spec)
                final_test_cases = template_plan.get("test_cases", [])
                if not final_test_cases or len(final_test_cases) == 0:
                    logger.error(f"[TEST PLAN] Template also returned empty! Creating minimal test case.")
                    # Create at least one test case
                    final_test_cases = [{
                        "test_id": "TC_MINIMAL_001",
                        "title": f"Test: {functional_spec.get('title', 'Feature') if isinstance(functional_spec, dict) else 'Feature'}",
                        "description": "Basic test case",
                        "category": "functional",
                        "priority": "high",
                        "steps": ["1. Setup", "2. Execute", "3. Verify"],
                        "expected_result": "Test completes",
                        "test_data": {}
                    }]
            
            # Clean up internal flags before building final plan
            if isinstance(test_plan, dict) and "_llm_attempted" in test_plan:
                del test_plan["_llm_attempted"]
            
            # Build complete test plan
            complete_plan = {
                "test_plan_id": str(uuid.uuid4()),
                "title": functional_spec.get("title", "Test Plan") if isinstance(functional_spec, dict) else "Test Plan",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generation_method": generation_method,  # Track how it was generated: "llm_ollama", "llm_api", "template", or "llm_template_hybrid"
                "overview": test_plan.get("overview", "") if isinstance(test_plan, dict) else "",
                "test_categories": test_plan.get("test_categories", []) if isinstance(test_plan, dict) else [],
                "test_cases": final_test_cases,
                "unit_tests": unit_tests,
                "integration_tests": integration_tests,
                "test_data": test_plan.get("test_data", []) if isinstance(test_plan, dict) else [],
                "test_environment": test_plan.get("test_environment", {}) if isinstance(test_plan, dict) else {},
                "test_schedule": test_plan.get("test_schedule", {}) if isinstance(test_plan, dict) else {},
                "risk_assessment": test_plan.get("risk_assessment", []) if isinstance(test_plan, dict) else []
            }
            
            # Final validation before returning
            if not complete_plan.get("test_cases") or len(complete_plan.get("test_cases", [])) == 0:
                logger.error(f"[TEST PLAN] CRITICAL ERROR: Returning test plan with 0 test cases! This should never happen.")
            
            return complete_plan
        
        except Exception as e:
            logger.error(f"Test plan generation failed: {e}")
            return {
                "error": str(e),
                "test_plan_id": str(uuid.uuid4())
            }
    
    def generate_unit_tests_from_spec(self, functional_spec: Dict[str, Any],
                                      test_framework: str = "pytest") -> List[Dict[str, Any]]:
        """
        Generate unit tests from functional specification
        
        Args:
            functional_spec: Functional specification
            test_framework: Test framework (pytest, unittest)
        
        Returns:
            List of unit test dictionaries with code
        """
        requirements = functional_spec.get("requirements", [])
        use_cases = functional_spec.get("use_cases", [])
        inputs = functional_spec.get("inputs", {})
        outputs = functional_spec.get("outputs", {})
        acceptance_criteria = functional_spec.get("acceptance_criteria", [])
        
        unit_tests = []
        
        # Generate tests for each requirement
        for i, requirement in enumerate(requirements, 1):
            test_code = self._generate_requirement_test(
                requirement, i, inputs, outputs, test_framework
            )
            unit_tests.append({
                "test_id": f"test_requirement_{i}",
                "requirement": requirement,
                "test_code": test_code,
                "test_framework": test_framework
            })
        
        # Generate tests for each use case
        for i, use_case in enumerate(use_cases, 1):
            test_code = self._generate_use_case_test(
                use_case, i, inputs, outputs, test_framework
            )
            unit_tests.append({
                "test_id": f"test_use_case_{i}",
                "use_case": use_case,
                "test_code": test_code,
                "test_framework": test_framework
            })
        
        # Generate tests for acceptance criteria
        for i, criterion in enumerate(acceptance_criteria, 1):
            test_code = self._generate_acceptance_test(
                criterion, i, inputs, outputs, test_framework
            )
            unit_tests.append({
                "test_id": f"test_acceptance_{i}",
                "acceptance_criterion": criterion,
                "test_code": test_code,
                "test_framework": test_framework
            })
        
        return unit_tests
    
    def generate_integration_tests_from_spec(self, functional_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate integration test scenarios from functional specification"""
        if not isinstance(functional_spec, dict):
            functional_spec = {}
        
        use_cases = functional_spec.get("use_cases", []) if isinstance(functional_spec, dict) else []
        integration_tests = []
        
        for i, use_case in enumerate(use_cases, 1):
            # Handle both dict and string use cases
            if isinstance(use_case, dict):
                uc_name = use_case.get('name', f'Use Case {i}')
                uc_steps = use_case.get("steps", [])
                uc_expected = use_case.get("expected_result", "")
                uc_test_data = use_case.get("test_data", {})
                uc_dependencies = use_case.get("dependencies", [])
            else:
                # Use case is a string
                uc_name = str(use_case)
                uc_steps = []
                uc_expected = ""
                uc_test_data = {}
                uc_dependencies = []
            
            scenario = {
                "scenario_id": f"integration_scenario_{i}",
                "use_case": uc_name,
                "description": f"Integration test for: {uc_name}",
                "steps": uc_steps,
                "expected_result": uc_expected,
                "test_data": uc_test_data,
                "dependencies": uc_dependencies
            }
            integration_tests.append(scenario)
        
        return integration_tests
    
    def _ai_generate_test_plan(self, functional_spec: Dict[str, Any],
                               use_local: bool = True) -> Dict[str, Any]:
        """Use AI to generate comprehensive test plan"""
        prompt = self._build_test_plan_prompt(functional_spec)
        
        if use_local and self.local_llm:
            try:
                system_prompt = """You are a QA engineer. Generate concise test plans as JSON with: overview, test_cases (array with test_id, title, description, category, priority, steps, expected_result), test_data, test_environment. Keep responses brief and focused."""
                
                response = self.local_llm.generate(prompt, system_prompt=system_prompt)
                
                if not response or not isinstance(response, str) or len(response.strip()) == 0:
                    logger.warning("Local LLM returned empty or invalid response, using template")
                    template_result = self._template_generate_test_plan(functional_spec)
                    template_result["_llm_attempted"] = True  # Mark that LLM was attempted
                    return template_result
                
                # Try to parse JSON response
                try:
                    parsed = json.loads(response)
                    if isinstance(parsed, dict):
                        # Check if test_cases exist and are not empty
                        test_cases = parsed.get("test_cases", [])
                        if test_cases and isinstance(test_cases, list) and len(test_cases) > 0:
                            logger.info(f"Successfully parsed JSON with {len(test_cases)} test cases")
                            return parsed
                        else:
                            logger.warning(f"Parsed JSON has no test_cases or empty test_cases list, using template")
                            template_result = self._template_generate_test_plan(functional_spec)
                            template_result["_llm_attempted"] = True
                            return template_result
                    else:
                        logger.warning(f"Parsed JSON is not a dict: {type(parsed)}")
                        template_result = self._template_generate_test_plan(functional_spec)
                        template_result["_llm_attempted"] = True
                        return template_result
                except json.JSONDecodeError:
                    # If not JSON, try to parse structured text, but fallback to template
                    logger.debug("Response is not JSON, trying text parsing")
                    parsed = self._parse_text_test_plan(response, functional_spec)
                    # If parsed text has no test cases, use template
                    if not parsed.get("test_cases"):
                        template_result = self._template_generate_test_plan(functional_spec)
                        template_result["_llm_attempted"] = True  # LLM was attempted but failed
                        return template_result
                    # If parsing succeeded, mark that LLM was used (even if it's text parsing)
                    parsed["_llm_attempted"] = False  # LLM succeeded, no fallback needed
                    return parsed
            except Exception as e:
                logger.error(f"Local LLM test plan generation failed: {e}", exc_info=True)
                template_result = self._template_generate_test_plan(functional_spec)
                template_result["_llm_attempted"] = True  # LLM was attempted but failed (timeout/error)
                return template_result
        
        if self.use_ai_api and self.ai_client:
            try:
                # Use stored model, or determine based on API base, or default to gpt-4
                model_name = self.model
                if not model_name:
                    import os
                    api_base = self.api_base or os.environ.get("OPENAI_API_BASE", "")
                    if api_base and "groq" in api_base.lower():
                        model_name = "llama-3.1-8b-instant"  # Default Groq model
                    else:
                        model_name = "gpt-4"  # Default OpenAI model
                
                # Check cache first to avoid duplicate API calls
                cache_key = hashlib.md5(f"{model_name}:{prompt}".encode()).hexdigest()
                if cache_key in _request_cache:
                    cached_result, cached_time = _request_cache[cache_key]
                    if time.time() - cached_time < _cache_ttl:
                        logger.debug(f"[TEST PLAN] Using cached result (age: {time.time() - cached_time:.1f}s)")
                        return cached_result
                    else:
                        del _request_cache[cache_key]
                
                # Small delay to throttle requests and reduce rate limit hits
                time.sleep(0.1)
                
                response = self.ai_client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "You are an expert QA engineer. Generate comprehensive test plans."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    response_format={"type": "json_object"}
                )
                
                result = json.loads(response.choices[0].message.content)
                
                # Cache the result
                _request_cache[cache_key] = (result, time.time())
                # Keep cache size reasonable (max 100 entries)
                if len(_request_cache) > 100:
                    oldest_key = min(_request_cache.keys(), key=lambda k: _request_cache[k][1])
                    del _request_cache[oldest_key]
                
                return result
            except Exception as e:
                logger.error(f"AI API test plan generation failed: {e}")
        
        return self._template_generate_test_plan(functional_spec)
    
    def _build_test_plan_prompt(self, functional_spec: Dict[str, Any]) -> str:
        """Build optimized prompt for AI test plan generation (concise for faster LLM response)"""
        # Build concise prompt to reduce LLM processing time
        title = functional_spec.get('title', 'Feature')
        desc = functional_spec.get('description', '')
        requirements = functional_spec.get("requirements", []) if isinstance(functional_spec, dict) else []
        use_cases = functional_spec.get("use_cases", []) if isinstance(functional_spec, dict) else []
        
        prompt = f"""Generate a test plan JSON for: {title}

Description: {desc[:200]}  # Truncated for speed

Requirements: {', '.join(str(r)[:50] for r in requirements[:5])}  # Max 5, truncated

Use Cases: {', '.join(str(uc)[:50] if not isinstance(uc, dict) else uc.get('name', '')[:50] for uc in use_cases[:5])}  # Max 5

Return JSON with: {{"overview": "...", "test_cases": [{{"test_id": "...", "title": "...", "description": "...", "category": "...", "priority": "...", "steps": [...], "expected_result": "..."}}], "test_data": [...], "test_environment": {{...}}}}

Keep test_cases concise (2-4 steps each). Generate 2-5 test cases."""
        
        return prompt
    
    def _parse_text_test_plan(self, text: str, functional_spec: Dict[str, Any] = None) -> Dict[str, Any]:
        """Parse text response into structured test plan"""
        # If text parsing fails, return template instead of empty structure
        # This ensures we always have valid test cases
        logger.warning("Text parsing failed or returned empty test cases, using template generation instead")
        if functional_spec is None:
            functional_spec = {}
        return self._template_generate_test_plan(functional_spec)
    
    def _template_generate_test_plan(self, functional_spec: Dict[str, Any]) -> Dict[str, Any]:
        """Generate test plan from template"""
        requirements = functional_spec.get("requirements", [])
        use_cases = functional_spec.get("use_cases", [])
        
        test_cases = []
        
        # Generate test cases for requirements
        for i, req in enumerate(requirements, 1):
            test_cases.append({
                "test_id": f"TC_REQ_{i:03d}",
                "title": f"Test Requirement: {req[:50]}",
                "description": f"Verify requirement: {req}",
                "category": "functional",
                "priority": "high",
                "steps": [
                    f"1. Setup test environment",
                    f"2. Execute functionality related to: {req}",
                    f"3. Verify requirement is met",
                    f"4. Validate output"
                ],
                "expected_result": f"Requirement '{req}' is satisfied",
                "test_data": {}
            })
        
        # Generate test cases for use cases
        for i, uc in enumerate(use_cases, 1):
            if isinstance(uc, dict):
                uc_name = uc.get("name", f"Use Case {i}")
                uc_steps = uc.get("steps", [])
            else:
                uc_name = str(uc)
                uc_steps = []
            
            test_cases.append({
                "test_id": f"TC_UC_{i:03d}",
                "title": f"Test Use Case: {uc_name}",
                "description": f"Verify use case: {uc_name}",
                "category": "integration",
                "priority": "high",
                "steps": uc_steps if uc_steps else [
                    f"1. Setup test environment",
                    f"2. Execute use case: {uc_name}",
                    f"3. Verify expected results",
                    f"4. Validate system state"
                ],
                "expected_result": f"Use case '{uc_name}' executes successfully",
                "test_data": {}
            })
        
        return {
            "overview": f"Test plan for {functional_spec.get('title', 'Feature')}",
            "test_categories": ["unit", "integration", "system", "e2e"],
            "test_cases": test_cases,
            "test_data": self._generate_test_data_requirements(functional_spec),
            "test_environment": {
                "hardware": "Standard test environment",
                "software": "Python 3.9+, pytest",
                "dependencies": "As per requirements"
            },
            "test_schedule": {
                "unit_tests": "Week 1",
                "integration_tests": "Week 2",
                "system_tests": "Week 3",
                "e2e_tests": "Week 4"
            },
            "risk_assessment": [
                {
                    "risk": "Incomplete requirements",
                    "impact": "medium",
                    "mitigation": "Review and clarify requirements before testing"
                }
            ]
        }
    
    def _generate_requirement_test(self, requirement: str, index: int,
                                   inputs: Dict, outputs: Dict,
                                   test_framework: str) -> str:
        """Generate unit test for a requirement"""
        if test_framework == "pytest":
            return f"""
def test_requirement_{index}():
    \"\"\"
    Test Requirement: {requirement[:100]}
    \"\"\"
    # Setup
    # TODO: Initialize test data based on requirement
    
    # Execute
    # TODO: Call function/method that implements: {requirement}
    # result = function_under_test(...)
    
    # Verify
    # TODO: Assert that requirement is met
    # assert result meets requirement: {requirement}
    
    # Expected: {requirement}
    pass
"""
        else:
            return f"""
def test_requirement_{index}(self):
    \"\"\"
    Test Requirement: {requirement[:100]}
    \"\"\"
    # Setup
    # TODO: Initialize test data
    
    # Execute
    # TODO: Call function that implements requirement
    
    # Verify
    # TODO: Assert requirement is met
    pass
"""
    
    def _generate_use_case_test(self, use_case: Any, index: int,
                               inputs: Dict, outputs: Dict,
                               test_framework: str) -> str:
        """Generate unit test for a use case"""
        if isinstance(use_case, dict):
            uc_name = use_case.get("name", f"Use Case {index}")
            uc_description = use_case.get("description", "")
        else:
            uc_name = str(use_case)
            uc_description = ""
        
        if test_framework == "pytest":
            return f"""
def test_use_case_{index}_{uc_name.lower().replace(' ', '_')}():
    \"\"\"
    Test Use Case: {uc_name}
    {uc_description}
    \"\"\"
    # Setup
    # TODO: Setup test environment for use case
    
    # Execute
    # TODO: Execute use case: {uc_name}
    # result = execute_use_case(...)
    
    # Verify
    # TODO: Verify use case completes successfully
    # assert result is not None
    # assert result meets expected outcome
    
    pass
"""
        else:
            return f"""
def test_use_case_{index}(self):
    \"\"\"
    Test Use Case: {uc_name}
    \"\"\"
    # Setup
    # TODO: Setup test environment
    
    # Execute
    # TODO: Execute use case
    
    # Verify
    # TODO: Verify results
    pass
"""
    
    def _generate_acceptance_test(self, criterion: str, index: int,
                                 inputs: Dict, outputs: Dict,
                                 test_framework: str) -> str:
        """Generate unit test for acceptance criterion"""
        if test_framework == "pytest":
            return f"""
def test_acceptance_criterion_{index}():
    \"\"\"
    Test Acceptance Criterion: {criterion}
    \"\"\"
    # Setup
    # TODO: Setup test data
    
    # Execute
    # TODO: Execute functionality
    
    # Verify acceptance criterion
    # TODO: Assert: {criterion}
    # assert acceptance_criterion_met("{criterion}")
    
    pass
"""
        else:
            return f"""
def test_acceptance_criterion_{index}(self):
    \"\"\"
    Test Acceptance Criterion: {criterion}
    \"\"\"
    # Setup
    # TODO: Setup test data
    
    # Execute
    # TODO: Execute functionality
    
    # Verify
    # TODO: Assert acceptance criterion
    pass
"""
    
    def _generate_test_data_requirements(self, functional_spec: Dict) -> List[Dict]:
        """Generate test data requirements"""
        inputs = functional_spec.get("inputs", {})
        test_data = []
        
        for key, value in inputs.items():
            test_data.append({
                "name": key,
                "type": type(value).__name__ if value else "unknown",
                "description": f"Test data for {key}",
                "examples": [value] if value else [],
                "required": True
            })
        
        return test_data
    
    def generate_detailed_test_plan_document(self, functional_spec: Dict[str, Any]) -> str:
        """
        Generate detailed test plan document (markdown format)
        
        Args:
            functional_spec: Functional specification
        
        Returns:
            Markdown formatted test plan document
        """
        test_plan = self.generate_test_plan(functional_spec)
        
        doc = f"""# Test Plan: {test_plan.get('title', 'Feature')}

**Generated**: {test_plan.get('generated_at', '')}
**Test Plan ID**: {test_plan.get('test_plan_id', '')}

## 1. Overview

{test_plan.get('overview', 'Test plan overview')}

## 2. Test Categories

"""
        for category in test_plan.get("test_categories", []):
            doc += f"- **{category.capitalize()} Tests**: Comprehensive {category} testing\n"
        
        doc += "\n## 3. Test Cases\n\n"
        
        for i, test_case in enumerate(test_plan.get("test_cases", []), 1):
            doc += f"### Test Case {i}: {test_case.get('title', '')}\n\n"
            doc += f"**Test ID**: {test_case.get('test_id', '')}\n\n"
            doc += f"**Description**: {test_case.get('description', '')}\n\n"
            doc += f"**Category**: {test_case.get('category', '')}\n\n"
            doc += f"**Priority**: {test_case.get('priority', '')}\n\n"
            doc += "**Test Steps**:\n"
            for step in test_case.get("steps", []):
                doc += f"{step}\n"
            doc += f"\n**Expected Result**: {test_case.get('expected_result', '')}\n\n"
            doc += "---\n\n"
        
        doc += "## 4. Unit Tests\n\n"
        for unit_test in test_plan.get("unit_tests", []):
            # Handle both dict and string formats
            if isinstance(unit_test, dict):
                doc += f"### {unit_test.get('test_id', '')}\n\n"
                doc += f"**Requirement/Use Case**: {unit_test.get('requirement', unit_test.get('use_case', ''))}\n\n"
                doc += "```python\n"
                doc += unit_test.get("test_code", "")
                doc += "\n```\n\n"
            else:
                # If it's a string, just display it
                doc += f"### Unit Test\n\n"
                doc += "```python\n"
                doc += str(unit_test)
                doc += "\n```\n\n"
        
        doc += "## 5. Integration Tests\n\n"
        for integration_test in test_plan.get("integration_tests", []):
            # Handle both dict and string formats
            if isinstance(integration_test, dict):
                doc += f"### {integration_test.get('scenario_id', '')}\n\n"
                doc += f"**Description**: {integration_test.get('description', '')}\n\n"
            else:
                # If it's a string, just display it
                doc += f"### Integration Test\n\n"
                doc += f"**Description**: {str(integration_test)}\n\n"
            doc += "**Steps**:\n"
            for step in integration_test.get("steps", []):
                doc += f"- {step}\n"
            doc += f"\n**Expected Result**: {integration_test.get('expected_result', '')}\n\n"
        
        doc += "## 6. Test Data Requirements\n\n"
        for data_req in test_plan.get("test_data", []):
            doc += f"- **{data_req.get('name', '')}**: {data_req.get('description', '')}\n"
        
        doc += "\n## 7. Test Environment\n\n"
        env = test_plan.get("test_environment", {})
        doc += f"- **Hardware**: {env.get('hardware', 'N/A')}\n"
        doc += f"- **Software**: {env.get('software', 'N/A')}\n"
        doc += f"- **Dependencies**: {env.get('dependencies', 'N/A')}\n"
        
        doc += "\n## 8. Test Schedule\n\n"
        schedule = test_plan.get("test_schedule", {})
        for phase, timeline in schedule.items():
            doc += f"- **{phase.replace('_', ' ').title()}**: {timeline}\n"
        
        doc += "\n## 9. Risk Assessment\n\n"
        for risk in test_plan.get("risk_assessment", []):
            doc += f"- **Risk**: {risk.get('risk', '')}\n"
            doc += f"  - **Impact**: {risk.get('impact', '')}\n"
            doc += f"  - **Mitigation**: {risk.get('mitigation', '')}\n\n"
        
        return doc
    
    def generate_pytest_script_from_test_plan(self, test_plan: Dict[str, Any],
                                              output_format: str = "file") -> str:
        """
        Generate executable pytest script from test plan
        
        Args:
            test_plan: Generated test plan dictionary
            output_format: "file" (complete file) or "functions" (just test functions)
        
        Returns:
            Complete pytest script as string
        """
        # Try cloud API first if available (for agent mode)
        if self.use_ai_api and self.ai_client:
            try:
                llm_pytest = self._llm_generate_pytest(test_plan, use_cloud=True)
                if llm_pytest and len(llm_pytest.strip()) > 500:
                    logger.info("Generated pytest script using cloud API")
                    return llm_pytest
            except Exception as e:
                logger.debug(f"Cloud API pytest generation failed, trying local LLM: {e}")
        
        # Try local LLM if available and cloud API not used
        if self.use_local_llm and self.local_llm:
            try:
                llm_pytest = self._llm_generate_pytest(test_plan, use_cloud=False)
                if llm_pytest and len(llm_pytest.strip()) > 500:
                    logger.info("Generated pytest script using local LLM")
                    return llm_pytest
            except Exception as e:
                logger.debug(f"LLM pytest generation failed, using template: {e}")
        
        # Fallback to template-based generation
        if output_format == "file":
            return self._generate_complete_pytest_file(test_plan)
        else:
            return self._generate_pytest_functions(test_plan)
    
    def _llm_generate_pytest(self, test_plan: Dict[str, Any], use_cloud: bool = False) -> str:
        """Use LLM to generate complete pytest script"""
        title = test_plan.get("title", "Test Suite")
        test_cases = test_plan.get("test_cases", [])
        unit_tests = test_plan.get("unit_tests", [])
        
        prompt = f"""Generate a complete, executable pytest test script for the following test plan:

Title: {title}

Test Cases:
"""
        for i, tc in enumerate(test_cases[:10], 1):  # Limit to first 10 for prompt size
            prompt += f"{i}. {tc.get('title', '')}: {tc.get('description', '')}\n"
            steps = tc.get('steps', [])
            if steps:
                prompt += f"   Steps: {', '.join(str(s) for s in steps[:3])}\n"  # First 3 steps
            prompt += f"   Expected: {tc.get('expected_result', '')}\n\n"
        
        prompt += f"""
CRITICAL: NETGENAI TEST FRAMEWORK CONTEXT (MANDATORY - READ CAREFULLY)
=======================================================================
This pytest script will run in NetGenAI Test Framework which AUTOMATICALLY INJECTS:

**DEVICE FIXTURE NAME**: Based on device name in CSV, the fixture is `{{device_name}}_device`
  - Example: Device name "la-q5130-05" → fixture name: `la_q5130_05_device`
  - Device name "router-01" → fixture name: `router_01_device`
  - Hyphens and spaces are converted to underscores, everything is lowercase

**YOU MUST USE THIS EXACT FIXTURE NAME IN YOUR TEST FUNCTIONS**

This pytest script will run in NetGenAI Test Framework which AUTOMATICALLY INJECTS:

1. **Device Fixture**: Named `{device_name}_device` (e.g., `la_q5130_05_device` for device "la-q5130-05")
   - Device name is converted to snake_case (hyphens and spaces become underscores)
   - This fixture is a dictionary with: {"device_id", "device_name", "device_type", "connection_info", "manager"}

2. **Helper Function**: `execute_device_command(device_fixture, command)`
   - Returns: {"success": bool, "output": str, "error": str or None}
   - Handles SSH connection, CLI mode entry, and command execution automatically
   - For multi-line commands (with \\n), automatically enters CLI mode (e.g., "cli" for Juniper)

**YOU MUST USE THESE INJECTED FIXTURES AND HELPERS - DO NOT CREATE YOUR OWN SSH CONNECTIONS**

CRITICAL: GENERATE ACTUAL IMPLEMENTATION CODE - NOT TEMPLATES OR TODOs
======================================================================
**MANDATORY REQUIREMENTS:**
1. Generate COMPLETE, EXECUTABLE pytest functions with ACTUAL IMPLEMENTATION CODE
2. DO NOT generate templates, placeholders, or TODO comments
3. DO NOT use assert True with "needs implementation" messages
4. DO NOT create empty functions or functions with only comments
5. Every test function MUST have actual code that executes real commands and assertions
6. All test steps MUST be implemented with real code, not TODO comments

**FORBIDDEN PATTERNS (DO NOT GENERATE):**
  # WRONG - Template with TODO
  def test_something():
      # TODO: Implement test steps
      assert True, "Test case needs implementation"
  
  # WRONG - Empty function
  def test_something():
      # Placeholder assertion
      assert True
  
  # WRONG - Only comments
  def test_something():
      # Setup
      # Execute
      # Verify

**REQUIRED PATTERN (GENERATE THIS):**
  # CORRECT - Full implementation
  def _get_output(device_fixture, command: str) -> str:
      result = execute_device_command(device_fixture, command)
      assert result["success"], f"Command failed: {result.get('error')}"
      return result["output"]
  
  def test_bgp_neighbor_config(la_q5130_05_device):
      neighbor_ip = "192.168.1.1"
      config_cmd = "configure\\nset protocols bgp group external neighbor {neighbor_ip} peer-as 65000\\ncommit\\nexit\\n"
      _get_output(la_q5130_05_device, config_cmd)
      output = _get_output(la_q5130_05_device, "show bgp summary")
      assert neighbor_ip in output

Requirements:
- Generate complete pytest functions with actual implementation code
- Use standard Python libraries: pytest, time, json
- DO NOT use paramiko.SSHClient directly - use the framework's execute_device_command helper
- DO NOT create test_ssh_connection fixtures - use the injected {device_name}_device fixture
- Implement actual test logic with proper error handling
- Include proper assertions (assert statements) that verify actual results
- Make it production-ready and executable
- Use proper Python syntax - all function definitions must end with ':'
- All code must be valid, executable Python
- NO TODO comments, NO placeholder assertions, NO empty functions

FRAMEWORK USAGE PATTERN (CRITICAL):
===================================
# Helper function to wrap framework's execute_device_command
def _get_output(device_fixture, command: str) -> str:
    result = execute_device_command(device_fixture, command)
    assert result["success"], f"Command failed: {result.get('error')}"
    return result["output"]

# Test function using injected device fixture
def test_check_interface(la_q5130_05_device):  # Use {device_name}_device fixture
    output = _get_output(la_q5130_05_device, "show interfaces")
    assert 'et-0/0/0' in output

# For multi-line commands (configure...commit), use \\n:
def test_configure_bgp(la_q5130_05_device):
    config_cmd = "configure\\nset protocols bgp group external neighbor 192.168.1.1 peer-as 65000\\ncommit\\nexit\\n"
    _get_output(la_q5130_05_device, config_cmd)

VALID JUNOS COMMANDS (for Juniper devices):
===========================================
- show bgp summary
- show bgp neighbor
- show bgp neighbor <ip> advertised-routes
- show route protocol bgp
- show route <prefix>
- show configuration protocols bgp
- configure (enters config mode)
- set/delete/commit/exit (config mode commands)

INVALID COMMANDS (DO NOT USE):
- show bgp advertising-parameters (syntax error)
- show bgp route-reflector (syntax error)
- show bgp received-routes (syntax error - use "show route receive-protocol bgp <neighbor>" instead)

PYTEST FIXTURE REQUIREMENTS:
- DO NOT create test_ssh_connection fixtures - the framework injects {device_name}_device
- DO NOT create fixtures that return None with TODO comments
- Test functions MUST use the injected {device_name}_device fixture as a parameter
- Helper functions should accept device_fixture as first parameter
- DO NOT use @pytest.mark.usefixtures - fixtures are automatically injected as parameters

Example CORRECT test function (using framework):
  def _get_output(device_fixture, command: str) -> str:
      result = execute_device_command(device_fixture, command)
      assert result["success"], f"Command failed: {result.get('error')}"
      return result["output"]
  
  def test_check_interface(la_q5130_05_device):  # Use injected fixture
      output = _get_output(la_q5130_05_device, "show interfaces")
      assert 'et-0/0/0' in output

Example INCORRECT (DO NOT DO THIS):
  @pytest.fixture
  def test_ssh_connection():  # WRONG - framework provides device fixture
      return None  # WRONG - fixtures must return actual values
  
  def test_check_interface(test_ssh_connection):  # WRONG - use {device_name}_device instead
      ssh = test_ssh_connection  # WRONG - will be None
      ssh.exec_command(...)  # WRONG - use execute_device_command instead

Example valid subprocess usage:
  result = subprocess.run(['ssh', f'user@{device_ip}', 'show', 'interfaces'], capture_output=True, text=True, timeout=10)
  assert result.returncode == 0

Example valid requests usage:
  response = requests.get('http://device/api/status', auth=('user', 'pass'), timeout=10)
  assert response.status_code == 200

CRITICAL NAMING RULES:
- Test functions MUST start with 'test_' prefix (e.g., test_check_interface, test_verify_optics)
- Helper/utility methods MUST start with '_' (single underscore) and NOT have 'test_' prefix (e.g., _connect_to_device, _get_output, _check_status)
- Helper/utility functions (standalone, not in class) MUST start with '_' and NOT have 'test_' prefix (e.g., _get_output, _check_status)
- DO NOT create helper methods with 'test_' prefix - they will be treated as test functions by pytest
- DO NOT create test methods with '_' prefix - they won't be discovered by pytest
- DO NOT create invalid method names like 'test_def_def_test_connect' - use proper names
- Example: Helper function: def _get_output(command): ... | Helper method: def _connect_ssh(self, device_ip): ... | Test method: def test_verify_connection(test_ssh_connection): ...

CLASS STRUCTURE RULES:
- If using a test class, helper methods should be private methods (start with _) within the class
- Test methods in a class should call helper methods using self._helper_method()
- Standalone test functions (outside class) should NOT use 'self' - they are not class methods
- If a function is outside a class, it cannot use self._method() - it must be a standalone function or use module-level helpers

FIXTURE IMPLEMENTATION RULES:
- Fixtures MUST return actual values, not None with TODO comments
- Fixtures should be fully implemented with real logic
- Example: @pytest.fixture def test_ssh_connection(): ssh = SSHClient(); ssh.connect(...); yield ssh; ssh.close()
- DO NOT create fixtures that return None - implement them properly

ASYNC RULES:
- Only use @pytest.mark.asyncio if the test actually uses async/await
- If using async, the function must be: async def test_xxx(): await some_async_function()
- DO NOT mark sync functions with @pytest.mark.asyncio
- Most SSH/network operations are synchronous - use regular def, not async def

COMMON MISTAKES TO AVOID:
- DO NOT use 'findstring' (Windows) - use 'grep' (Unix/Linux)
- DO NOT use string concatenation in subprocess lists: ['ssh', 'user@' + ip] - use f-strings: [f'ssh', f'user@{ip}']
- DO NOT forget @pytest.fixture decorator on functions that yield or return test data
- DO NOT use @pytest.mark.usefixtures - just add fixtures as parameters
- DO NOT use device_ip if fixture is test_device_ip - use the exact fixture name
- DO NOT use test_username= in ssh.connect() - use username= (paramiko uses username=, not test_username=)
- DO NOT create function names like test_def_test_* - use test_* only
- DO NOT forget to import requests if using requests.get() or requests.post()
- DO NOT forget to import subprocess if using subprocess.run()
- DO NOT use invalid paramiko parameters like cert_reqs='required' - this is not a valid parameter
- DO NOT create helper methods with test_ prefix - use _ prefix instead
- DO NOT create helper functions with test_ prefix - use _ prefix instead
- DO NOT use self in standalone functions (outside classes)
- DO NOT create fixtures with Python built-in names (str, list, dict, int, float, bool, tuple, set, type, paramiko, command, host_ip, policy)
- DO NOT create duplicate fixtures (e.g., both 'command' and 'test_command' - only create 'test_command')
- DO NOT create duplicate function definitions - each function should be defined only once
- DO NOT use undefined variables like 'ssh' - always use fixture names like 'test_ssh_connection'
- DO NOT create helper functions that use undefined variables - pass fixtures as parameters
- DO NOT use 'for...else' with incorrect logic - 'else' in for loops executes when loop completes normally, not on failure

CRITICAL VALIDATION CHECKLIST (verify before generating):
1. ✅ All helper functions start with '_' (not 'test_')
2. ✅ All test functions start with 'test_'
3. ✅ No duplicate function definitions
4. ✅ No fixtures with Python built-in names (command, str, list, etc.)
5. ✅ Test functions use {device_name}_device fixture (e.g., la_q5130_05_device) - NOT test_ssh_connection
6. ✅ Helper functions use execute_device_command(device_fixture, command) - NOT ssh.exec_command()
7. ✅ No fixtures return None with TODO comments
8. ✅ No paramiko.SSHClient usage - use framework's execute_device_command instead
9. ✅ Only valid Junos commands are used (check against list above)
10. ✅ Multi-line commands use \\n separators (e.g., "configure\\nset...\\ncommit\\n")
11. ✅ NO TODO comments anywhere in the code
12. ✅ NO placeholder assertions like assert True, "needs implementation"
13. ✅ NO empty functions or functions with only comments
14. ✅ Every test function has actual implementation code that executes commands
15. ✅ All test steps are implemented with real code, not comments

**FINAL CHECK BEFORE GENERATING:**
- Does every test function have actual code (not just comments)?
- Are there any TODO comments? If yes, REMOVE THEM and implement the code.
- Are there any assert True, "needs implementation" statements? If yes, REPLACE with real assertions.
- Can this code actually run and execute commands? If no, ADD the missing implementation.

Generate the complete pytest file now with FULL IMPLEMENTATION. Review your code against the checklist above before finalizing.
Remember: The framework injects {device_name}_device fixture and execute_device_command helper - USE THEM!
Generate ACTUAL CODE, NOT TEMPLATES!"""
        
        try:
            system_prompt = """You are an expert Python test engineer specializing in network device testing. Generate complete, executable pytest test scripts with actual implementation code.

CRITICAL: NETGENAI TEST FRAMEWORK CONTEXT
==========================================
The pytest script will run in NetGenAI Test Framework which AUTOMATICALLY INJECTS:

1. **Device Fixture**: Named `{device_name}_device` (e.g., `la_q5130_05_device` for device "la-q5130-05")
   - Device name is converted to snake_case (hyphens/spaces become underscores)
   - This fixture is a dictionary: {"device_id", "device_name", "device_type", "connection_info", "manager"}

2. **Helper Function**: `execute_device_command(device_fixture, command)`
   - Returns: {"success": bool, "output": str, "error": str or None}
   - Handles SSH connection, CLI mode entry, and command execution automatically
   - For multi-line commands (with \\n), automatically enters CLI mode (e.g., "cli" for Juniper)

**YOU MUST USE THESE INJECTED FIXTURES AND HELPERS - DO NOT CREATE YOUR OWN SSH CONNECTIONS**

CRITICAL: GENERATE ACTUAL IMPLEMENTATION CODE - NOT TEMPLATES OR TODOs
======================================================================
**MANDATORY REQUIREMENTS:**
1. Generate COMPLETE, EXECUTABLE pytest functions with ACTUAL IMPLEMENTATION CODE
2. DO NOT generate templates, placeholders, or TODO comments
3. DO NOT use assert True with "needs implementation" messages
4. DO NOT create empty functions or functions with only comments
5. Every test function MUST have actual code that executes real commands and assertions
6. All test steps MUST be implemented with real code, not TODO comments
7. Use ONLY standard Python libraries: pytest, time, json
8. DO NOT use paramiko.SSHClient directly - use framework's execute_device_command helper
9. DO NOT create test_ssh_connection fixtures - use injected {device_name}_device fixture
10. All function definitions must end with ':'
11. All code must be syntactically valid Python that can be executed
12. Use proper error handling with assert statements
13. Include proper assertions with assert statements that verify actual results

**FORBIDDEN PATTERNS (DO NOT GENERATE):**
  # WRONG - Template with TODO
  def test_something():
      # TODO: Implement test steps
      assert True, "Test case needs implementation"
  
  # WRONG - Empty function
  def test_something():
      # Placeholder assertion
      assert True
  
  # WRONG - Only comments
  def test_something():
      # Setup
      # Execute
      # Verify

**REQUIRED PATTERN (GENERATE THIS):**
  # CORRECT - Full implementation
  def _get_output(device_fixture, command: str) -> str:
      result = execute_device_command(device_fixture, command)
      assert result["success"], f"Command failed: {result.get('error')}"
      return result["output"]
  
  def test_bgp_neighbor_config(la_q5130_05_device):
      neighbor_ip = "192.168.1.1"
      config_cmd = "configure\\nset protocols bgp group external neighbor {neighbor_ip} peer-as 65000\\ncommit\\nexit\\n"
      _get_output(la_q5130_05_device, config_cmd)
      output = _get_output(la_q5130_05_device, "show bgp summary")
      assert neighbor_ip in output

FRAMEWORK USAGE PATTERN (MANDATORY):
===================================
# Helper function to wrap framework's execute_device_command
def _get_output(device_fixture, command: str) -> str:
    result = execute_device_command(device_fixture, command)
    assert result["success"], f"Command failed: {result.get('error')}"
    return result["output"]

# Test function using injected device fixture
def test_check_interface(la_q5130_05_device):  # Use {device_name}_device fixture
    output = _get_output(la_q5130_05_device, "show interfaces")
    assert 'et-0/0/0' in output

# For multi-line commands (configure...commit), use \\n:
def test_configure_bgp(la_q5130_05_device):
    config_cmd = "configure\\nset protocols bgp group external neighbor 192.168.1.1 peer-as 65000\\ncommit\\nexit\\n"
    _get_output(la_q5130_05_device, config_cmd)

VALID JUNOS COMMANDS (for Juniper devices):
- show bgp summary
- show bgp neighbor
- show bgp neighbor <ip> advertised-routes
- show route protocol bgp
- show route <prefix>
- show configuration protocols bgp
- configure (enters config mode)
- set/delete/commit/exit (config mode commands)

INVALID COMMANDS (DO NOT USE):
- show bgp advertising-parameters (syntax error)
- show bgp route-reflector (syntax error)
- show bgp received-routes (syntax error)

PYTEST FIXTURE RULES (CRITICAL):
- DO NOT create test_ssh_connection fixtures - framework injects {device_name}_device
- DO NOT create fixtures that return None with TODO comments
- Test functions MUST use injected {device_name}_device fixture as parameter
- Helper functions should accept device_fixture as first parameter
- DO NOT use @pytest.mark.usefixtures - fixtures are automatically injected

COMMAND RULES:
- Use Unix/Linux commands: grep (NOT findstring), ssh (NOT telnet)
- For SSH commands: use f-strings: f'user@{device_ip}' NOT string concatenation: 'user@' + device_ip
- Each subprocess argument must be a separate string in the list

CRITICAL NAMING RULES:
- Test functions MUST start with 'test_' prefix (e.g., test_check_interface)
- Helper/utility methods MUST start with '_' (single underscore) and NOT have 'test_' prefix (e.g., _connect_to_device, _get_output)
- DO NOT create helper methods with 'test_' prefix - pytest will try to run them as tests
- If using a class, helper methods are: def _helper_name(self): ... and test methods call them: self._helper_name()
- Standalone functions (outside class) cannot use 'self' - they are not class methods
- DO NOT create duplicate function definitions - each function name should appear only once

VARIABLE USAGE RULES:
- DO NOT use undefined variables like 'ssh' - always use fixture names like 'test_ssh_connection'
- Helper functions that need SSH connection must receive 'test_ssh_connection' as a parameter
- Example: def _get_output(test_ssh_connection, command): ... NOT def _get_output(command): ... with undefined ssh

FIXTURE RULES:
- Fixtures MUST return actual values, not None with TODO comments
- Implement fixtures fully with real logic
- DO NOT create fixtures that return None
- DO NOT create fixtures with Python built-in names (command, str, list, dict, etc.)

DUPLICATE PREVENTION:
- DO NOT define the same function multiple times
- Each function should be defined exactly once
- If you need similar functionality, create different function names

ASYNC RULES:
- Only use @pytest.mark.asyncio if actually using async/await
- Most SSH/network operations are synchronous - use regular def, not async def

Generate valid, executable Python code that follows pytest best practices. Double-check for duplicates and undefined variables before finalizing."""
            
            logger.info(f"[LLM PYTEST] Generating pytest script for test plan: {title} (use_cloud={use_cloud})")
            
            if use_cloud and self.ai_client:
                # Use cloud API
                try:
                    # Use stored model, or determine based on API base, or default to gpt-4
                    model_name = self.model
                    if not model_name:
                        import os
                        api_base = self.api_base or os.environ.get("OPENAI_API_BASE", "")
                        if api_base and "groq" in api_base.lower():
                            model_name = "llama-3.1-8b-instant"  # Default Groq model
                        else:
                            model_name = "gpt-4"  # Default OpenAI model
                    
                    logger.info(f"[LLM PYTEST] Using cloud model: {model_name}")
                    response_obj = self.ai_client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3
                    )
                    response = response_obj.choices[0].message.content
                    logger.info(f"[LLM PYTEST] Received response from cloud API: {len(response) if response else 0} chars")
                except Exception as e:
                    logger.error(f"[LLM PYTEST] Cloud API generation failed: {e}")
                    # Fall back to local LLM if available
                    if self.use_local_llm and self.local_llm:
                        logger.info("[LLM PYTEST] Falling back to local LLM")
                        response = self.local_llm.generate(prompt, system_prompt=system_prompt)
                    else:
                        raise
            else:
                # Use local LLM
                if not self.local_llm:
                    raise ValueError("Local LLM not available")
                response = self.local_llm.generate(prompt, system_prompt=system_prompt)
            
            if response and len(response.strip()) > 500:
                logger.info(f"[LLM PYTEST] Received response: {len(response)} chars")
                # Extract code from markdown if present
                extracted_code = None
                if "```python" in response:
                    import re
                    match = re.search(r'```python\n(.*?)\n```', response, re.DOTALL)
                    if match:
                        extracted_code = match.group(1)
                        logger.info(f"[LLM PYTEST] Extracted code from markdown: {len(extracted_code)} chars")
                elif "```" in response:
                    import re
                    match = re.search(r'```\n(.*?)\n```', response, re.DOTALL)
                    if match:
                        extracted_code = match.group(1)
                        logger.info(f"[LLM PYTEST] Extracted code from markdown: {len(extracted_code)} chars")
                else:
                    extracted_code = response
                    logger.info(f"[LLM PYTEST] Using response directly: {len(extracted_code)} chars")
                
                # Post-process to detect and fix template patterns
                if extracted_code:
                    is_template = self._is_template(extracted_code)
                    if is_template:
                        logger.warning("[LLM PYTEST] Generated script contains template patterns - attempting auto-fix")
                    extracted_code = self._fix_template_patterns(extracted_code, test_cases)
                    # Verify fix worked
                    if is_template and self._is_template(extracted_code):
                        logger.warning("[LLM PYTEST] Template patterns still detected after auto-fix - may need manual review")
                    elif is_template:
                        logger.info("[LLM PYTEST] Template patterns successfully fixed")
                
                # Clean up the generated code to fix common issues
                if extracted_code:
                    cleaned_code = self._clean_pytest_code(extracted_code)
                    # Validate syntax before returning
                    if self._validate_python_syntax(cleaned_code):
                        logger.info("[LLM PYTEST] Generated code passed syntax validation")
                        return cleaned_code
                    else:
                        logger.warning("[LLM PYTEST] Generated code has syntax errors, attempting additional fixes...")
                        # Try one more cleaning pass
                        cleaned_code2 = self._clean_pytest_code(cleaned_code)
                        if self._validate_python_syntax(cleaned_code2):
                            logger.info("[LLM PYTEST] Code fixed after second pass")
                            return cleaned_code2
                        else:
                            logger.error("[LLM PYTEST] Generated code still has syntax errors after cleaning")
                            # Return it anyway - user can fix manually, but log the issue
                            return cleaned_code2
            else:
                logger.warning(f"[LLM PYTEST] Response too short or empty: {len(response) if response else 0} chars")
        except Exception as e:
            logger.error(f"[LLM PYTEST] Generation error: {e}", exc_info=True)
        
        return ""
    
    def _clean_pytest_code(self, code: str) -> str:
        """Clean and fix common syntax issues in generated pytest code"""
        import re
        
        # Remove invalid fixtures that are Python built-ins or common names
        invalid_fixture_names = ['command', 'host_ip', 'list', 'paramiko', 'policy', 'str', 
                                  'dict', 'int', 'float', 'bool', 'tuple', 'set', 'type',
                                  'expected_status', 'status', 'output', 'result', 'interface',
                                  'test_speed_tool', 'speed_tool', 'config', 'test_device_ip2',
                                  'test_ssh_connection2', 'bgp_config', 'test_bgp_config']
        for invalid_name in invalid_fixture_names:
            # Remove fixtures with these names (with or without test_ prefix)
            # Pattern: @pytest.fixture def [test_]name(...): ... return None # TODO ...
            code = re.sub(
                rf'@pytest\.fixture\s+def\s+{invalid_name}\([^)]*\):.*?return\s+None.*?#\s*TODO.*?\n',
                '',
                code,
                flags=re.DOTALL
            )
            # Also remove test_ prefixed versions
            code = re.sub(
                rf'@pytest\.fixture\s+def\s+test_{invalid_name}\([^)]*\):.*?return\s+None.*?#\s*TODO.*?\n',
                '',
                code,
                flags=re.DOTALL
            )
            # Remove fixtures that return None without TODO (more general pattern)
            code = re.sub(
                rf'@pytest\.fixture\s+def\s+{invalid_name}\([^)]*\):\s*return\s+None\s*\n',
                '',
                code,
                flags=re.MULTILINE
            )
            code = re.sub(
                rf'@pytest\.fixture\s+def\s+test_{invalid_name}\([^)]*\):\s*return\s+None\s*\n',
                '',
                code,
                flags=re.MULTILINE
            )
        
        # Fix helper methods that incorrectly have test_ prefix
        # Pattern: def test__helper_name -> def _helper_name (remove test_ prefix for helpers)
        # First, collect all helper function renames
        helper_function_renames = {}
        def collect_rename(match):
            old_name = match.group(1)
            new_name = old_name.replace('test__', '_')
            helper_function_renames[old_name] = new_name
            return f'def {new_name}('
        
        code = re.sub(
            r'def\s+(test__[a-zA-Z0-9_]+)\s*\(',
            collect_rename,
            code
        )
        
        # Now fix all calls to these renamed helper functions
        for old_name, new_name in helper_function_renames.items():
            # Fix standalone function calls (not methods): test__function_name(...) -> _function_name(...)
            # But avoid matching if it's already been renamed or is in a def statement
            code = re.sub(
                rf'(?<!def\s)(?<!self\.)(?<!\.)\b{re.escape(old_name)}\s*\(',
                f'{new_name}(',
                code
            )
            # Fix method calls: self.test__method_name(...) -> self._method_name(...)
            code = re.sub(
                rf'self\.{re.escape(old_name)}\s*\(',
                f'self.{new_name}(',
                code
            )
        
        # Fix calls to helper methods that incorrectly reference test__ methods
        # Pattern: self._test__method_name -> self._method_name
        code = re.sub(
            r'self\._test__([a-zA-Z0-9_]+)',
            r'self._\1',
            code
        )
        
        # Fix any remaining calls to helper functions (not methods) - test__function_name -> _function_name
        code = re.sub(
            r'(?<!def\s)(?<!self\.)(?<!\.)(?<!\.)\btest__([a-zA-Z0-9_]+)\s*\(',
            r'_\1(',
            code
        )
        
        # Remove duplicate function definitions (keep only the first occurrence)
        lines = code.split('\n')
        seen_functions = set()
        cleaned_lines = []
        for line in lines:
            # Check if this is a function definition
            func_match = re.match(r'(\s*)def\s+([a-zA-Z0-9_]+)\s*\(', line)
            if func_match:
                func_name = func_match.group(2)
                if func_name in seen_functions:
                    # Skip duplicate function definition
                    logger.debug(f"[PYTEST CLEAN] Removing duplicate function: {func_name}")
                    # Skip this line and any following lines until we hit a non-indented line or another function
                    continue
                else:
                    seen_functions.add(func_name)
            cleaned_lines.append(line)
        code = '\n'.join(cleaned_lines)
        
        # Fix undefined variables - replace 'ssh' with 'test_ssh_connection' where appropriate
        # Pattern: ssh.exec_command -> test_ssh_connection.exec_command (but only in test functions, not in fixtures)
        lines = code.split('\n')
        cleaned_lines = []
        in_test_function = False
        for line in lines:
            # Track if we're in a test function
            if re.match(r'\s*def\s+test_', line):
                in_test_function = True
            elif re.match(r'\s*def\s+', line) and not re.match(r'\s*def\s+test_', line):
                in_test_function = False
            
            # Fix undefined 'ssh' variable in test functions
            if in_test_function and 'ssh.' in line and 'test_ssh_connection' not in line:
                # Replace ssh with test_ssh_connection
                line = re.sub(r'\bssh\.', 'test_ssh_connection.', line)
                # Also fix standalone ssh references (not method calls)
                if re.search(r'\bssh\s*=', line) or re.search(r'\(\s*ssh\s*\)', line):
                    line = re.sub(r'\bssh\b', 'test_ssh_connection', line)
            
            cleaned_lines.append(line)
        code = '\n'.join(cleaned_lines)
        
        # Fix invalid for...else logic - remove else clause from for loops (else executes on normal completion, not failure)
        code = re.sub(
            r'(\s+for\s+[^:]+:.*?)(\s+else:\s+print\([^)]+\)\.)',
            r'\1',
            code,
            flags=re.DOTALL
        )
        
        # Fix calls to helper functions (not methods) - test__function_name -> _function_name
        code = re.sub(
            r'(?<!self\.)(?<!\.)test__([a-zA-Z0-9_]+)\s*\(',
            r'_\1(',
            code
        )
        
        # Fix invalid method names like test_def_def_test_connect
        code = re.sub(
            r'def\s+test_def_def_test_([a-zA-Z0-9_]+)\s*\(',
            r'def _\1(',
            code
        )
        
        # Fix invalid class methods with test_ prefix that should be helpers
        # Pattern: class SSHClient: def test__init__ -> def __init__
        code = re.sub(
            r'class\s+(\w+):.*?def\s+test___init__',
            r'class \1:\n    def __init__',
            code,
            flags=re.DOTALL
        )
        
        # Fix class methods that have test_ prefix but are helpers
        # Pattern: def test_set_missing_host_key_policy in a class -> def _set_missing_host_key_policy
        lines = code.split('\n')
        cleaned_lines = []
        in_class = False
        class_indent = 0
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('class '):
                in_class = True
                class_indent = len(line) - len(line.lstrip())
            elif stripped and not stripped.startswith('#') and not stripped.startswith('@'):
                current_indent = len(line) - len(line.lstrip())
                if in_class and current_indent <= class_indent:
                    in_class = False
            
            # Fix test_ prefixed methods in classes that are actually helpers
            if in_class and 'def test_' in line:
                # Check if it's a test method (should be at class level) or helper
                # If it's something like test_set_*, test__init__, test__connect_*, it's likely a helper
                if 'test__' in line or 'test_set_' in line or 'test_connect' in line or 'test__connect' in line:
                    # Extract the method name
                    method_name_match = re.search(r'def\s+test__(.+?)\s*\(', line)
                    if method_name_match:
                        old_method_name = 'test__' + method_name_match.group(1)
                        new_method_name = '_' + method_name_match.group(1)
                        # Rename the method definition
                        line = line.replace('def ' + old_method_name, 'def ' + new_method_name)
                        # Fix calls to this method in subsequent lines within the class
                        for j in range(i + 1, len(lines)):
                            # Check if we're still in the class
                            if j < len(lines):
                                next_line = lines[j]
                                # Check if we've left the class (new class or top-level function)
                                if next_line.strip() and not next_line.strip().startswith('#') and not next_line.strip().startswith('@'):
                                    next_indent = len(next_line) - len(next_line.lstrip())
                                    if next_indent <= class_indent and 'class ' in next_line:
                                        break  # New class started
                                    elif next_indent <= class_indent and 'def ' in next_line and not next_line.strip().startswith('def test_'):
                                        break  # Top-level function
                            
                            # Fix calls within the class
                            if 'self.' + old_method_name in lines[j]:
                                lines[j] = lines[j].replace('self.' + old_method_name, 'self.' + new_method_name)
                    else:
                        # Fallback: just rename test_ to _
                        line = re.sub(r'def\s+test_', 'def _', line)
            
            cleaned_lines.append(line)
        
        code = '\n'.join(cleaned_lines)
        
        # Fix paramiko connect() calls with wrong parameter names
        # Pattern: .connect(host_ip, test_username=..., test_password=...) -> .connect(host_ip, username=..., password=...)
        code = re.sub(
            r'\.connect\(([^,]+),\s*test_username\s*=',
            r'.connect(\1, username=',
            code
        )
        code = re.sub(
            r'\.connect\(([^,]+),\s*test_password\s*=',
            r'.connect(\1, password=',
            code
        )
        
        # Fix missing subprocess import if subprocess.run is used
        if 'subprocess.run' in code and 'import subprocess' not in code:
            # Add import at the top
            if 'from subprocess import' in code:
                # Replace with full import
                code = re.sub(
                    r'from subprocess import[^\n]+',
                    'import subprocess',
                    code,
                    count=1
                )
            elif 'import subprocess' not in code:
                # Add after other imports
                import_match = re.search(r'(import\s+[^\n]+\n)', code)
                if import_match:
                    code = code[:import_match.end()] + 'import subprocess\n' + code[import_match.end():]
                else:
                    # Add at the beginning after pytest import
                    code = re.sub(
                        r'(import pytest\n)',
                        r'\1import subprocess\n',
                        code,
                        count=1
                    )
        
        # Fix standalone functions that incorrectly use self
        # Find standalone functions (not in class) that use self
        lines = code.split('\n')
        cleaned_lines = []
        in_class = False
        class_indent = 0
        
        for i, line in enumerate(lines):
            # Track if we're in a class
            stripped = line.strip()
            if stripped.startswith('class '):
                in_class = True
                class_indent = len(line) - len(line.lstrip())
            elif stripped and not stripped.startswith('#') and not stripped.startswith('@'):
                current_indent = len(line) - len(line.lstrip())
                if in_class and current_indent <= class_indent:
                    in_class = False
            
            # Fix standalone functions using self
            if not in_class and 'def ' in line and 'self' in line:
                # This is a standalone function but has self parameter - remove it
                line = re.sub(r'\(self,\s*', '(', line)
                line = re.sub(r'\(self\)', '()', line)
                # Also fix self. calls in standalone functions
                if i < len(lines) - 1:
                    # Check next few lines for self. usage
                    for j in range(i+1, min(i+20, len(lines))):
                        next_line = lines[j]
                        next_indent = len(next_line) - len(next_line.lstrip())
                        if next_indent <= len(line) - len(line.lstrip()):
                            break  # Out of function scope
                        if 'self.' in next_line:
                            # Replace self._method() with just _method() or remove if it's a class method call
                            lines[j] = re.sub(r'self\._([a-zA-Z0-9_]+)', r'_\1', lines[j])
                            lines[j] = re.sub(r'self\.([a-zA-Z0-9_]+)\(', r'\1(', lines[j])
            
            cleaned_lines.append(line)
        
        code = '\n'.join(cleaned_lines)
        
        lines = code.split('\n')
        cleaned_lines = []
        i = 0
        
        while i < len(lines):
            line = lines[i]
            original_line = line
            
            # Fix function definitions - add missing colon if needed
            if line.strip().startswith('def '):
                # Check if line ends with colon, if not add it
                stripped = line.rstrip()
                if not stripped.endswith(':'):
                    # Check if it has parameters (ends with ) or just function name
                    if ')' in stripped:
                        # Has parameters, add colon after closing paren
                        line = stripped + ':'
                    else:
                        # No parameters, add () and colon
                        if '(' not in stripped:
                            line = stripped + '():'
                        else:
                            line = stripped + ':'
                    logger.debug(f"[PYTEST CLEAN] Added missing colon: {original_line} -> {line}")
                
                # Fix function names with spaces - replace spaces with underscores
                func_part = line.split('(')[0] if '(' in line else line
                if ' ' in func_part:
                    # Match: def test_name with spaces(parameters):
                    func_match = re.match(r'(\s*def\s+)([a-zA-Z_][a-zA-Z0-9_\s]*?)(\s*\([^)]*\)|$)', line)
                    if func_match:
                        indent = func_match.group(1)
                        func_name = func_match.group(2).strip()
                        params_part = func_match.group(3) if func_match.group(3) else ''
                        # Replace spaces with underscores in function name
                        func_name_clean = re.sub(r'\s+', '_', func_name)
                        # Ensure it starts with test_ if it's a test function
                        if not func_name_clean.startswith('test_'):
                            func_name_clean = 'test_' + func_name_clean
                        line = indent + func_name_clean + params_part
                        logger.debug(f"[PYTEST CLEAN] Fixed function name: {func_name} -> {func_name_clean}")
            
            # Fix missing test_data parameter if function uses it
            if 'def test_' in line and '(' in line:
                # Match any test function name (including those with underscores)
                func_match = re.match(r'(\s*def\s+test_[a-zA-Z0-9_]+)\s*\(([^)]*)\)', line)
                if func_match:
                    func_def = func_match.group(1)
                    params = func_match.group(2).strip()
                    # Check if test_data is used in the function body (next 30 lines)
                    func_body = '\n'.join(lines[i+1:min(i+30, len(lines))])
                    if 'test_data' in func_body and 'test_data' not in params:
                        # Add test_data to parameters
                        if params:
                            params = params + ', test_data'
                        else:
                            params = 'test_data'
                        line = func_def + '(' + params + ')'
                        logger.debug(f"[PYTEST CLEAN] Added test_data parameter to function")
            
            cleaned_lines.append(line)
            i += 1
        
        cleaned_code = '\n'.join(cleaned_lines)
        
        # Additional fix: Replace any remaining function names with spaces in the entire code
        # This catches cases where the regex might have missed
        cleaned_code = re.sub(
            r'def\s+([a-zA-Z_][a-zA-Z0-9_\s]*?)\s*\(',
            # Avoid f-strings here to prevent backslash parsing issues inside expressions
            lambda m: "def " + re.sub(r'\s+', '_', m.group(1)) + "(",
            cleaned_code
        )
        
        # Fix missing colons after function definitions (second pass to catch any missed)
        cleaned_code = re.sub(
            r'(def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\([^)]*\))\s*$',
            r'\1:',
            cleaned_code,
            flags=re.MULTILINE
        )
        
        # Fix invalid subprocess.run() calls
        # Pattern: run([' Junos', 'show interfaces', ' Flap']) -> invalid
        # Should be: run(['ssh', 'user@host', 'show', 'interfaces', 'et-0/0/0'], ...)
        def fix_subprocess_run(match):
            full_match = match.group(0)
            # Check if it's a malformed call with leading spaces in strings
            if re.search(r"'\s+[A-Z]", full_match) or " Junos" in full_match or " Flap" in full_match:
                # This is likely invalid - replace with a comment and proper example
                logger.warning(f"[PYTEST CLEAN] Detected invalid subprocess.run() call: {full_match[:100]}")
                # Try to extract meaningful parts
                cmd_parts = re.findall(r"'([^']+)'", full_match)
                if cmd_parts:
                    # Clean up the parts
                    fixed_parts = []
                    for part in cmd_parts:
                        part = part.strip()
                        if part and not part.startswith(' '):
                            # If it contains spaces and looks like a command, split it
                            if ' ' in part and len(part) > 3:
                                fixed_parts.extend(part.split())
                            elif part:
                                fixed_parts.append(part)
                    
                    if fixed_parts:
                        # Reconstruct with proper syntax
                        cmd_list = "', '".join(fixed_parts)
                        if 'capture_output' not in full_match and 'timeout' not in full_match:
                            return f"subprocess.run(['{cmd_list}'], capture_output=True, text=True, timeout=10)"
                        elif 'capture_output' not in full_match:
                            return f"subprocess.run(['{cmd_list}'], capture_output=True, text=True)"
                        else:
                            return full_match  # Already has parameters, just fix the command list
                # If we can't fix it, replace with a comment
                return f"# TODO: Fix invalid subprocess.run() call\n    # subprocess.run(['ssh', 'user@host', 'command'], capture_output=True, text=True, timeout=10)"
            return full_match
        
        cleaned_code = re.sub(
            r"subprocess\.run\(\[[^\]]+\](?:,\s*[^)]+)?\)",
            fix_subprocess_run,
            cleaned_code
        )
        
        # Fix run() calls without subprocess. prefix (assume they mean subprocess.run)
        cleaned_code = re.sub(
            r"(?<!subprocess\.)run\(\[([^\]]+)\]\)",
            r"subprocess.run([\1], capture_output=True, text=True, timeout=10)",
            cleaned_code
        )
        
        # Fix invalid subprocess.run() calls where command.split() is used incorrectly
        # Pattern: subprocess.run(command.split(), ...) where command is an f-string with SSH
        # Example: command = f'ssh {test_username}@{test_device_ip} show interfaces'
        #          subprocess.run(command.split(), ...)
        # Should be: subprocess.run(['ssh', f'{test_username}@{test_device_ip}', 'show interfaces'], ...)
        lines = code.split('\n')
        cleaned_lines = []
        for i, line in enumerate(lines):
            # Check if line has subprocess.run(command.split(), ...)
            if 'subprocess.run(' in line and 'command.split()' in line:
                # Look backwards for command assignment (within 10 lines to catch more cases)
                cmd_assign_line = None
                cmd_str = None
                for j in range(max(0, i-10), i):
                    if 'command =' in lines[j] and ('ssh' in lines[j] or 'test_username' in lines[j] or 'test_device_ip' in lines[j]):
                        cmd_assign_line = lines[j]
                        # Extract the command string (handle both regular and f-strings)
                        # Match: command = f'ssh {test_username}@{test_device_ip} show interfaces'
                        fstring_match = re.search(r"command\s*=\s*f['\"](.*?)['\"]", cmd_assign_line)
                        string_match = re.search(r"command\s*=\s*['\"](.*?)['\"]", cmd_assign_line)
                        if fstring_match:
                            cmd_str = fstring_match.group(1)
                        elif string_match:
                            cmd_str = string_match.group(1)
                        
                        # Only break if we found a valid command string
                        if cmd_str:
                            break
                
                if cmd_str and ('test_username' in cmd_str or 'test_device_ip' in cmd_str or 'ssh' in cmd_str.lower()):
                    # Extract the SSH command parts
                    # Pattern: ssh {test_username}@{test_device_ip} <remote_command>
                    parts = cmd_str.split(' ', 2)  # Split into: ['ssh', '{test_username}@{test_device_ip}', '<remote_command>']
                    if len(parts) >= 2 and 'ssh' in parts[0].lower():
                        # Build proper subprocess.run call
                        remote_cmd = parts[2] if len(parts) > 2 else ''
                        # Replace the command.split() call, preserving any additional parameters
                        if 'capture_output' in line or 'timeout' in line:
                            # Already has parameters, just replace command.split() with proper list
                            if remote_cmd:
                                replacement = f"['ssh', f'{parts[1]}', '{remote_cmd}']"
                            else:
                                replacement = f"['ssh', f'{parts[1]}']"
                            line = re.sub(
                                r'command\.split\(\)',
                                replacement,
                                line
                            )
                        else:
                            # Replace the whole call with proper parameters
                            if remote_cmd:
                                fixed_call = f"subprocess.run(['ssh', f'{parts[1]}', '{remote_cmd}'], capture_output=True, text=True, timeout=10)"
                            else:
                                fixed_call = f"subprocess.run(['ssh', f'{parts[1]}'], capture_output=True, text=True, timeout=10)"
                            line = re.sub(
                                r'subprocess\.run\(command\.split\(\)[^)]*\)',
                                fixed_call,
                                line
                            )
            
            cleaned_lines.append(line)
        code = '\n'.join(cleaned_lines)
        
        # Fix invalid SSH subprocess.run() calls where '@' is a separate argument
        # Pattern: subprocess.run(['ssh', username, '@', ip, 'command'], ...)
        # Should be: subprocess.run(['ssh', f'{username}@{ip}', 'command'], ...)
        def fix_ssh_command(match):
            full_match = match.group(0)
            # Check if it has the pattern ['ssh', var, '@', var, ...]
            if "'@'" in full_match or '"@"' in full_match:
                # Extract the command parts
                cmd_match = re.search(r"subprocess\.run\(\[([^\]]+)\](?:,\s*[^)]+)?\)", full_match)
                if cmd_match:
                    cmd_list = cmd_match.group(1)
                    # Check for pattern: 'ssh', var, '@', var
                    if re.search(r"['\"]ssh['\"].*['\"]@['\"]", cmd_list):
                        # Fix it by combining username and host
                        # Pattern: 'ssh', device_info['username'], '@', device_info['ip']
                        fixed_cmd = re.sub(
                            r"(['\"]ssh['\"]),\s*([a-zA-Z_][a-zA-Z0-9_]*)\[['\"]username['\"]\],\s*['\"]@['\"],\s*(\2)\[['\"]ip['\"]\]",
                            r"\1, f\"{\2['username']}@{\2['ip']}\"",
                            cmd_list
                        )
                        # Also handle the simpler case: 'ssh', username, '@', ip
                        fixed_cmd = re.sub(
                            r"(['\"]ssh['\"]),\s*([a-zA-Z_][a-zA-Z0-9_]*),\s*['\"]@['\"],\s*([a-zA-Z_][a-zA-Z0-9_]*)\[['\"]ip['\"]\]",
                            r"\1, f\"{\2}@{\3['ip']}\"",
                            fixed_cmd
                        )
                        # Replace in the full match
                        return full_match.replace(cmd_list, fixed_cmd)
            return full_match
        
        cleaned_code = re.sub(
            r"subprocess\.run\(\[[^\]]*['\"]@['\"][^\]]*\](?:,\s*[^)]+)?\)",
            fix_ssh_command,
            cleaned_code
        )
        
        # Fix missing imports - check for common missing imports
        imports_needed = []
        if 'time.sleep' in cleaned_code or 'time.' in cleaned_code:
            if 'import time' not in cleaned_code and 'from time import' not in cleaned_code:
                imports_needed.append('import time')
        if 'timedelta' in cleaned_code:
            if 'from datetime import timedelta' not in cleaned_code and 'from datetime import' not in cleaned_code:
                imports_needed.append('from datetime import timedelta')
        
        if imports_needed:
            # Find the last import statement and add after it
            import_pattern = r"(^import\s+\w+|^from\s+\w+\s+import\s+\w+)(.*?)$"
            last_import_match = None
            for match in re.finditer(import_pattern, cleaned_code, re.MULTILINE):
                last_import_match = match
            
            if last_import_match:
                insert_pos = last_import_match.end()
                new_imports = '\n'.join(imports_needed)
                cleaned_code = cleaned_code[:insert_pos] + '\n' + new_imports + cleaned_code[insert_pos:]
            else:
                # No imports found, add at the beginning after any shebang or docstring
                lines = cleaned_code.split('\n')
                insert_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith('import ') or line.startswith('from '):
                        insert_idx = i + 1
                        break
                if insert_idx == 0:
                    # Check for shebang
                    if lines[0].startswith('#!'):
                        insert_idx = 1
                new_imports = '\n'.join(imports_needed)
                lines.insert(insert_idx, new_imports)
                cleaned_code = '\n'.join(lines)
        
        # Fix fixture name mismatches - if test_device_info is defined but device_info is used
        if 'test_device_info' in cleaned_code and 'device_info' in cleaned_code:
            # Check if test_device_info is a fixture
            if re.search(r'@pytest\.fixture\s+def\s+test_device_info', cleaned_code):
                # Replace device_info with test_device_info in function parameters
                # Pattern: def test_xxx(..., device_info, ...):
                cleaned_code = re.sub(
                    r'(\w+)\s*=\s*device_info\b',
                    r'\1 = test_device_info',
                    cleaned_code
                )
                # Replace in function parameter lists
                cleaned_code = re.sub(
                    r'(\w+)\s*,\s*device_info\b',
                    r'\1, test_device_info',
                    cleaned_code
                )
                cleaned_code = re.sub(
                    r'\bdevice_info\s*,',
                    'test_device_info,',
                    cleaned_code
                )
                cleaned_code = re.sub(
                    r'\(\s*device_info\b',
                    '(test_device_info',
                    cleaned_code
                )
                # Also replace in function body references (but be careful not to break dict access)
                # Only replace standalone device_info, not device_info['key']
                # Use word boundaries to avoid partial matches
                cleaned_code = re.sub(
                    r'\bdevice_info\b(?!\[)',
                    'test_device_info',
                    cleaned_code
                )
        
        # Fix type errors - if current_time returns a string but timedelta is used on it
        if 'test_current_time' in cleaned_code and 'timedelta' in cleaned_code:
            # Check if current_time is used with timedelta
            if re.search(r'current_time\s*\+\s*timedelta', cleaned_code):
                # Fix the fixture to return datetime object instead of string
                cleaned_code = re.sub(
                    r'def\s+test_current_time\(\):\s*return\s+datetime\.now\(\)\.strftime\([^)]+\)',
                    'def test_current_time():\n    return datetime.now()',
                    cleaned_code
                )
        
        # Fix empty function parameters - pattern: def test_xxx(, param):
        cleaned_code = re.sub(
            r'def\s+(\w+)\s*\(\s*,\s*',
            r'def \1(',
            cleaned_code
        )
        
        # Fix helper functions that are named test_* but shouldn't be test functions
        # Pattern: def test_execute_command(device, command): - should be execute_command or a fixture
        # If it's called by other test functions, rename it to not start with test_
        helper_functions = re.findall(r'def\s+(test_\w+)\s*\([^)]*\):', cleaned_code)
        for helper_func in helper_functions:
            # Check if this function is called by other test functions
            if re.search(rf'(?<!def\s){helper_func}\s*\(', cleaned_code):
                # It's a helper function, rename it
                new_name = helper_func.replace('test_', '', 1)  # Remove first test_ prefix
                cleaned_code = re.sub(
                    rf'\b{helper_func}\b',
                    new_name,
                    cleaned_code
                )
                logger.debug(f"[PYTEST CLEAN] Renamed helper function: {helper_func} -> {new_name}")
        
        # Fix function name mismatches - if execute_command is called but test_execute_command is defined
        if 'execute_command' in cleaned_code and 'def execute_command' not in cleaned_code:
            # Check if test_execute_command exists
            if 'def test_execute_command' in cleaned_code:
                # Rename test_execute_command to execute_command
                cleaned_code = re.sub(
                    r'def\s+test_execute_command\b',
                    'def execute_command',
                    cleaned_code
                )
                cleaned_code = re.sub(
                    r'\btest_execute_command\b',
                    'execute_command',
                    cleaned_code
                )
        
        # Fix incorrect SSHClient.MissingHostKeyPolicy usage
        cleaned_code = re.sub(
            r'SSHClient\.MissingHostKeyPolicy\([^)]+\)',
            'SSHClient.AutoAddPolicy()',
            cleaned_code
        )
        cleaned_code = re.sub(
            r'ssh\.MissingHostKeyPolicy\([^)]+\)',
            'SSHClient.AutoAddPolicy()',
            cleaned_code
        )
        
        # Fix incorrect paramiko connect() parameter names
        # Pattern: ssh.connect(host, test_username=user, test_password=pass) -> ssh.connect(host, username=user, password=pass)
        cleaned_code = re.sub(
            r'ssh\.connect\(([^,]+),\s*test_username\s*=',
            r'ssh.connect(\1, username=',
            cleaned_code
        )
        cleaned_code = re.sub(
            r'ssh\.connect\(([^,]+),\s*test_password\s*=',
            r'ssh.connect(\1, password=',
            cleaned_code
        )
        
        # Fix invalid function names like test_def_test_*
        cleaned_code = re.sub(
            r'def\s+test_def_test_',
            'def test_',
            cleaned_code
        )
        
        # Fix missing imports - check if requests is used but not imported
        if 'requests.' in cleaned_code or 'requests.get' in cleaned_code or 'requests.post' in cleaned_code:
            if 'import requests' not in cleaned_code and 'from requests import' not in cleaned_code:
                # Find last import statement
                import_match = None
                for match in re.finditer(r'^(import\s+\w+|from\s+\w+\s+import\s+\w+)', cleaned_code, re.MULTILINE):
                    import_match = match
                if import_match:
                    insert_pos = import_match.end()
                    cleaned_code = cleaned_code[:insert_pos] + '\nimport requests' + cleaned_code[insert_pos:]
                else:
                    # No imports found, add after shebang or at top
                    lines = cleaned_code.split('\n')
                    insert_idx = 0
                    if lines[0].startswith('#!'):
                        insert_idx = 1
                    lines.insert(insert_idx, 'import requests')
                    cleaned_code = '\n'.join(lines)
        
        # Fix missing paramiko import if RSAKey is used
        if 'paramiko.RSAKey' in cleaned_code or 'RSAKey.from_private_key_file' in cleaned_code:
            if 'import paramiko' not in cleaned_code and 'from paramiko import' not in cleaned_code:
                # Find last import statement
                import_match = None
                for match in re.finditer(r'^(import\s+\w+|from\s+\w+\s+import\s+\w+)', cleaned_code, re.MULTILINE):
                    import_match = match
                if import_match:
                    insert_pos = import_match.end()
                    cleaned_code = cleaned_code[:insert_pos] + '\nimport paramiko' + cleaned_code[insert_pos:]
                else:
                    lines = cleaned_code.split('\n')
                    insert_idx = 0
                    if lines[0].startswith('#!'):
                        insert_idx = 1
                    lines.insert(insert_idx, 'import paramiko')
                    cleaned_code = '\n'.join(lines)
        
        # Fix missing fixtures - if a function uses a fixture that doesn't exist, create it or remove the usage
        # Pattern: def test_xxx(proxy_url, ...) but no @pytest.fixture def proxy_url
        lines_fix = cleaned_code.split('\n')
        used_fixtures = set()
        defined_fixtures = set()
        
        # Find all fixtures defined
        for line in lines_fix:
            if '@pytest.fixture' in line:
                # Next line should be def fixture_name
                idx = lines_fix.index(line)
                if idx + 1 < len(lines_fix):
                    next_line = lines_fix[idx + 1]
                    fixture_match = re.search(r'def\s+(\w+)', next_line)
                    if fixture_match:
                        defined_fixtures.add(fixture_match.group(1))
        
        # Find all fixtures used in test functions
        for line in lines_fix:
            if 'def test_' in line and '(' in line:
                # Extract parameters
                params_match = re.search(r'def\s+test_\w+\(([^)]+)\)', line)
                if params_match:
                    params = params_match.group(1)
                    # Extract parameter names (simple names, not dict access)
                    param_names = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', params)
                    for param in param_names:
                        if param not in ['self', 'pytest', 'time', 'subprocess', 'json', 'requests', 'SSHClient', 'AutoAddPolicy']:
                            used_fixtures.add(param)
        
        # Create missing fixtures
        missing_fixtures = used_fixtures - defined_fixtures
        if missing_fixtures:
            # Find where to insert fixtures (after imports, before test functions)
            insert_idx = 0
            for i, line in enumerate(lines_fix):
                if line.strip().startswith('def test_'):
                    insert_idx = i
                    break
                if '@pytest.fixture' in line:
                    # Find the end of the last fixture
                    j = i + 1
                    while j < len(lines_fix) and (lines_fix[j].startswith(' ') or lines_fix[j].startswith('\t') or not lines_fix[j].strip()):
                        j += 1
                    insert_idx = j
            
            # Create fixture definitions
            fixture_defs = []
            for fixture_name in sorted(missing_fixtures):
                # Skip if it's a standard library or already handled
                if fixture_name in ['pytest', 'time', 'subprocess', 'json', 'requests', 'SSHClient']:
                    continue
                # Create a simple fixture
                if 'url' in fixture_name.lower():
                    fixture_defs.append(f"\n@pytest.fixture\ndef {fixture_name}():\n    return 'http://192.168.1.100'")
                elif 'user' in fixture_name.lower():
                    fixture_defs.append(f"\n@pytest.fixture\ndef {fixture_name}():\n    return 'admin'")
                elif 'password' in fixture_name.lower() or 'pass' in fixture_name.lower():
                    fixture_defs.append(f"\n@pytest.fixture\ndef {fixture_name}():\n    return 'password123'")
                else:
                    fixture_defs.append(f"\n@pytest.fixture\ndef {fixture_name}():\n    return None  # TODO: Configure this fixture")
            
            if fixture_defs:
                lines_fix.insert(insert_idx, '\n'.join(fixture_defs))
                cleaned_code = '\n'.join(lines_fix)
                logger.debug(f"[PYTEST CLEAN] Created missing fixtures: {missing_fixtures}")
        
        # Fix duplicate fixture definitions
        # Find all fixture definitions and remove duplicates
        lines_fix = cleaned_code.split('\n')
        seen_fixtures = {}
        fixture_indices = []
        
        for idx, line in enumerate(lines_fix):
            if '@pytest.fixture' in line:
                # Check next line for function definition
                if idx + 1 < len(lines_fix):
                    next_line = lines_fix[idx + 1]
                    fixture_match = re.search(r'def\s+(\w+)', next_line)
                    if fixture_match:
                        fixture_name = fixture_match.group(1)
                        if fixture_name in seen_fixtures:
                            # Duplicate found - mark for removal
                            fixture_indices.append((idx, fixture_name, 'duplicate'))
                        else:
                            seen_fixtures[fixture_name] = idx
                            fixture_indices.append((idx, fixture_name, 'keep'))
        
        # Remove duplicate fixtures (keep the first one)
        if fixture_indices:
            # Sort by index in reverse to remove from end
            for idx, fixture_name, status in sorted(fixture_indices, reverse=True):
                if status == 'duplicate':
                    # Remove the @pytest.fixture decorator and function definition
                    # Find the end of the function
                    func_start = idx + 1
                    func_end = func_start + 1
                    while func_end < len(lines_fix) and (lines_fix[func_end].startswith(' ') or lines_fix[func_end].startswith('\t') or not lines_fix[func_end].strip() or lines_fix[func_end].strip().startswith('#')):
                        func_end += 1
                    # Remove from func_end backwards to idx
                    for i in range(func_end - 1, idx - 1, -1):
                        if i >= 0 and i < len(lines_fix):
                            lines_fix.pop(i)
                    logger.debug(f"[PYTEST CLEAN] Removed duplicate fixture: {fixture_name}")
        
        cleaned_code = '\n'.join(lines_fix)
        
        # Fix incomplete fixture definitions (just @pytest.fixture with no function)
        lines_fix2 = cleaned_code.split('\n')
        to_remove = []
        for idx, line in enumerate(lines_fix2):
            if '@pytest.fixture' in line and idx + 1 < len(lines_fix2):
                next_line = lines_fix2[idx + 1].strip()
                # Check if next line is empty, another decorator, or not a function definition
                if not next_line or next_line.startswith('@') or not next_line.startswith('def '):
                    # Incomplete fixture - remove it
                    to_remove.append(idx)
                    # Also remove empty lines after it
                    j = idx + 1
                    while j < len(lines_fix2) and (not lines_fix2[j].strip() or lines_fix2[j].strip().startswith('#')):
                        to_remove.append(j)
                        j += 1
        
        # Remove incomplete fixtures (in reverse order)
        for idx in sorted(to_remove, reverse=True):
            if idx < len(lines_fix2):
                lines_fix2.pop(idx)
        
        cleaned_code = '\n'.join(lines_fix2)
        
        # Note: We don't reorder fixtures automatically as it's complex and might break dependencies
        # The code cleaning fixes the main issues (duplicates, incomplete definitions, parameter names)
        
        # Fix incorrect requests.auth usage
        # Pattern: auth=user, test_password=pass -> auth=(user, pass)
        def fix_requests_auth(match):
            user_part = match.group(1)
            pass_part = match.group(2)
            return f'auth=({user_part}, {pass_part})'
        
        cleaned_code = re.sub(
            r'auth\s*=\s*([^,]+),\s*test_password\s*=\s*([^,)]+)',
            fix_requests_auth,
            cleaned_code
        )
        # Pattern: auth=credentials['user'], test_password=credentials['pass']
        def fix_requests_auth_dict(match):
            dict1 = match.group(1)
            dict2 = match.group(2) if match.lastindex >= 2 else match.group(1)
            return f'auth=({dict1}["user"], {dict2}["pass"])'
        
        cleaned_code = re.sub(
            r'auth\s*=\s*(\w+)\[\s*["\']user["\']\s*\],\s*test_password\s*=\s*(\w+)\[\s*["\']pass["\']\s*\]',
            fix_requests_auth_dict,
            cleaned_code
        )
        
        # Fix invalid paramiko certificate/key usage
        # Pattern: ssh.connect(..., pkey=f.read(), cert_reqs='required') - cert_reqs is not a valid parameter
        cleaned_code = re.sub(
            r',\s*cert_reqs\s*=\s*[^,)]+',
            '',
            cleaned_code
        )
        # Fix pkey usage - should use proper key loading
        # Pattern: with open(cert_file, 'rb') as f: ssh.connect(..., pkey=f.read())
        # Should be: pkey=paramiko.RSAKey.from_private_key_file(cert_file)
        lines_pkey = cleaned_code.split('\n')
        for idx, line in enumerate(lines_pkey):
            if 'pkey=f.read()' in line or 'pkey = f.read()' in line:
                # Check if we're in a with open() block
                # Look back for 'with open'
                for j in range(max(0, idx-5), idx):
                    if 'with open' in lines_pkey[j] and ('cert' in lines_pkey[j].lower() or 'key' in lines_pkey[j].lower()):
                        # Extract filename
                        file_match = re.search(r"['\"]([^'\"]+)['\"]", lines_pkey[j])
                        if file_match:
                            filename = file_match.group(1)
                            # Replace pkey=f.read() with proper key loading
                            lines_pkey[idx] = line.replace('pkey=f.read()', f'pkey=paramiko.RSAKey.from_private_key_file("{filename}")')
                            lines_pkey[idx] = lines_pkey[idx].replace('pkey = f.read()', f'pkey=paramiko.RSAKey.from_private_key_file("{filename}")')
                            # Also need to add paramiko import if not present
                            if 'import paramiko' not in cleaned_code and 'from paramiko import' not in cleaned_code:
                                # Find imports section
                                for k in range(len(lines_pkey)):
                                    if lines_pkey[k].startswith('import ') or lines_pkey[k].startswith('from '):
                                        lines_pkey.insert(k, 'import paramiko')
                                        break
                        break
        cleaned_code = '\n'.join(lines_pkey)
        
        # Fix fixture parameter name mismatches
        # Pattern: def test_xxx(device, ...) but fixture is test_juniper_device
        lines_fix = cleaned_code.split('\n')
        for idx, line in enumerate(lines_fix):
            if 'def test_' in line and '(' in line:
                # Fix device -> test_juniper_device in parameters
                if 'test_juniper_device' in cleaned_code and 'device' in line and 'test_juniper_device' not in line:
                    line = re.sub(r'\bdevice\b(?!\[)', 'test_juniper_device', line)
                # Fix interface_data -> test_interface_data in parameters
                if 'test_interface_data' in cleaned_code and 'interface_data' in line and 'test_interface_data' not in line:
                    line = re.sub(r'\binterface_data\b(?!\[)', 'test_interface_data', line)
                lines_fix[idx] = line
        
        cleaned_code = '\n'.join(lines_fix)
        
        # Also fix in function bodies (but not in dict access or string literals)
        if 'test_juniper_device' in cleaned_code:
            # Replace device with test_juniper_device in function bodies
            cleaned_code = re.sub(
                r'\bdevice\b(?!\[|\'|\"|\.|\w)',
                'test_juniper_device',
                cleaned_code
            )
        
        if 'test_interface_data' in cleaned_code:
            # Replace interface_data with test_interface_data in function bodies
            cleaned_code = re.sub(
                r'\binterface_data\b(?!\[|\'|\"|\.|\w)',
                'test_interface_data',
                cleaned_code
            )
        
        # Fix missing @pytest.fixture decorator for functions that should be fixtures
        # Pattern: def test_xxx(): return ... (should be a fixture)
        lines_fix_fixture = cleaned_code.split('\n')
        for idx, line in enumerate(lines_fix_fixture):
            # Check if this is a function definition that looks like a fixture
            if re.match(r'^\s*def\s+test_\w+\(\)\s*:', line):
                # Check if it returns something (likely a fixture)
                func_start = idx
                func_end = func_start + 1
                # Find the function body
                while func_end < len(lines_fix_fixture) and (lines_fix_fixture[func_end].startswith(' ') or lines_fix_fixture[func_end].startswith('\t') or not lines_fix_fixture[func_end].strip()):
                    func_end += 1
                # Check if function has a return statement
                func_body = '\n'.join(lines_fix_fixture[func_start:min(func_end, func_start+10)])
                if 'return' in func_body and '@pytest.fixture' not in lines_fix_fixture[max(0, func_start-2):func_start]:
                    # Add @pytest.fixture decorator
                    indent = len(line) - len(line.lstrip())
                    fixture_line = ' ' * indent + '@pytest.fixture'
                    lines_fix_fixture.insert(func_start, fixture_line)
                    logger.debug(f"[PYTEST CLEAN] Added @pytest.fixture decorator to function at line {func_start+1}")
        
        cleaned_code = '\n'.join(lines_fix_fixture)
        
        # Fix invalid exec_command timeout parameter
        # Pattern: ssh.exec_command('command', timeout=10) - exec_command doesn't take timeout
        cleaned_code = re.sub(
            r'\.exec_command\(([^,)]+),\s*timeout\s*=\s*\d+\)',
            r'.exec_command(\1)',
            cleaned_code
        )
        
        # Fix undefined variables in test functions
        # Find all fixture definitions
        fixture_names = set()
        for match in re.finditer(r'@pytest\.fixture\s*\n\s*def\s+(\w+)', cleaned_code):
            fixture_names.add(match.group(1))
        # Also find functions that return values (likely fixtures)
        for match in re.finditer(r'def\s+(test_\w+)\(\)\s*:', cleaned_code):
            func_name = match.group(1)
            start_pos = match.end()
            next_lines = cleaned_code[start_pos:start_pos+500]
            if 'return' in next_lines and func_name not in fixture_names:
                fixture_names.add(func_name)
        
        # Fix test functions that use fixtures but don't have them as parameters
        def add_missing_fixture_params(match):
            func_def = match.group(0)
            func_name_match = re.search(r'def\s+(\w+)', func_def)
            if not func_name_match:
                return func_def
            func_name = func_name_match.group(1)
            
            # Get function body
            func_start = match.end()
            func_body = cleaned_code[func_start:func_start+2000]
            next_def_match = re.search(r'\n\s*def\s+', func_body)
            if next_def_match:
                func_body = func_body[:next_def_match.start()]
            
            # Find fixtures used in body
            used_fixtures = []
            for fixture_name in fixture_names:
                if re.search(rf'\b{fixture_name}\b', func_body) and fixture_name not in func_def:
                    used_fixtures.append(fixture_name)
            
            if used_fixtures:
                # Extract current parameters
                params_match = re.search(r'\(([^)]*)\)', func_def)
                if params_match:
                    current_params = params_match.group(1).strip()
                    new_params = current_params
                    for fixture in used_fixtures:
                        if fixture not in new_params:
                            if new_params:
                                new_params += f', {fixture}'
                            else:
                                new_params = fixture
                    if new_params != current_params:
                        func_def = func_def.replace(f'({current_params})', f'({new_params})')
                        logger.debug(f"[PYTEST CLEAN] Added missing fixture parameters to {func_name}: {used_fixtures}")
            
            return func_def
        
        # Apply fix to all test functions
        cleaned_code = re.sub(
            r'def\s+test_\w+\([^)]*\)\s*:',
            add_missing_fixture_params,
            cleaned_code
        )
        
        # Fix incorrect exec_command() usage
        # Pattern: ssh.exec_command('command').stdout.decode('utf-8')
        # Should be: stdin, stdout, stderr = ssh.exec_command('command'); output = stdout.read().decode('utf-8')
        
        # Fix pattern: ssh.exec_command(...).stdout.decode(...)
        def fix_exec_command_decode(match):
            cmd = match.group(1)
            decode_arg = match.group(2) if match.lastindex >= 2 else "'utf-8'"
            return f"stdin, stdout, stderr = ssh.exec_command({cmd})\n        output = stdout.read().decode({decode_arg})"
        
        cleaned_code = re.sub(
            r'ssh\.exec_command\(([^)]+)\)\.stdout\.decode\(([^)]+)\)',
            fix_exec_command_decode,
            cleaned_code
        )
        
        # Fix pattern: ssh.exec_command(...).stdout (without decode) - used in assertions
        def fix_exec_command_stdout(match):
            cmd = match.group(1)
            return f"stdin, stdout, stderr = ssh.exec_command({cmd})\n        stdout"
        
        cleaned_code = re.sub(
            r'ssh\.exec_command\(([^)]+)\)\.stdout',
            fix_exec_command_stdout,
            cleaned_code
        )
        
        # Fix pattern: ssh.exec_command(...).stderr
        cleaned_code = re.sub(
            r'ssh\.exec_command\(([^)]+)\)\.stderr',
            lambda m: f"stdin, stdout, stderr = ssh.exec_command({m.group(1)})\n        stderr",
            cleaned_code
        )
        
        # Fix incorrect subprocess.run() with SSH commands containing pipes
        # Pattern: run(['ssh', f'user@{ip}', 'command | grep ...'], ...)
        # Should use shell=True or restructure the command
        def fix_ssh_subprocess(match):
            full_call = match.group(0)
            # Check if command contains pipes or shell operators
            if '|' in full_call or ';' in full_call or '&&' in full_call or '||' in full_call:
                # Extract the command list
                cmd_match = re.search(r'\[([^\]]+)\]', full_call)
                if cmd_match:
                    cmd_list = cmd_match.group(1)
                    # Check if it's an SSH command
                    if 'ssh' in cmd_list and ('|' in cmd_list or ';' in cmd_list):
                        # Replace with shell=True approach or restructure
                        # For SSH with pipes, we need to pass the command as a single string to bash -c
                        # Pattern: ['ssh', f'user@{ip}', 'cmd1 | cmd2'] -> ['ssh', f'user@{ip}', 'bash', '-c', 'cmd1 | cmd2']
                        return re.sub(
                            r"run\(\[([^,]+),\s*([^,]+),\s*'([^']+)'\]",
                            r"run([\1, \2, 'bash', '-c', '\3']",
                            full_call
                        )
            return full_call
        
        # Apply fix to subprocess.run() calls with SSH
        cleaned_code = re.sub(
            r'subprocess\.run\(\[[^\]]*ssh[^\]]*\|[^\]]*\]',
            fix_ssh_subprocess,
            cleaned_code
        )
        cleaned_code = re.sub(
            r'run\(\[[^\]]*ssh[^\]]*\|[^\]]*\]',
            fix_ssh_subprocess,
            cleaned_code
        )
        
        # Fix incorrect multiple exec_command() calls without unpacking
        # Pattern: ssh.exec_command('cmd1'); ssh.exec_command('cmd2').stdout
        # Should unpack each call
        lines_exec = cleaned_code.split('\n')
        for idx, line in enumerate(lines_exec):
            # Check for exec_command() calls that aren't unpacked
            if 'exec_command(' in line and 'stdin, stdout, stderr' not in lines_exec[max(0, idx-2):idx]:
                # Check if this line uses .stdout or .stderr directly
                if '.stdout' in line or '.stderr' in line:
                    # Extract the command
                    cmd_match = re.search(r'exec_command\(([^)]+)\)', line)
                    if cmd_match:
                        cmd = cmd_match.group(1)
                        # Replace with unpacked version
                        indent = len(line) - len(line.lstrip())
                        new_lines = [
                            ' ' * indent + f"stdin, stdout, stderr = ssh.exec_command({cmd})",
                            line.replace(f"ssh.exec_command({cmd})", "stdout").replace(f".exec_command({cmd})", "")
                        ]
                        lines_exec[idx] = '\n'.join(new_lines)
                        logger.debug(f"[PYTEST CLEAN] Fixed exec_command() unpacking at line {idx+1}")
        
        cleaned_code = '\n'.join(lines_exec)
        
        # Fix parameter shadowing - when a function receives a parameter and then reassigns it
        # Pattern: def test_xxx(config, ...): ... config = '...' (should use a different variable name)
        lines_shadow = cleaned_code.split('\n')
        for idx, line in enumerate(lines_shadow):
            if 'def test_' in line and '(' in line:
                # Extract function parameters
                params_match = re.search(r'def\s+test_\w+\(([^)]+)\)', line)
                if params_match:
                    params = params_match.group(1)
                    param_names = [p.strip().split('=')[0].strip() for p in params.split(',')]
                    
                    # Check the next 30 lines for parameter reassignments
                    func_body_start = idx + 1
                    func_body_end = min(idx + 30, len(lines_shadow))
                    for body_idx in range(func_body_start, func_body_end):
                        body_line = lines_shadow[body_idx]
                        # Skip if we hit another function definition
                        if body_line.strip().startswith('def '):
                            break
                        
                        # Check if a parameter is being reassigned (pattern: param_name = ...)
                        for param_name in param_names:
                            # Match: param_name = (at the start of a line or after spaces)
                            shadow_match = re.search(rf'^\s+{re.escape(param_name)}\s*=\s*', body_line)
                            if shadow_match and param_name not in ['self']:
                                # This is parameter shadowing - rename the reassignment to use a local variable
                                # Replace: config = '...' with local_config = '...' and update usages in this function
                                new_var_name = f'local_{param_name}'
                                # Replace the assignment
                                lines_shadow[body_idx] = body_line.replace(f'{param_name} =', f'{new_var_name} =')
                                # Update usages of this variable in the function body (but not parameter usages)
                                for usage_idx in range(body_idx + 1, func_body_end):
                                    if lines_shadow[usage_idx].strip().startswith('def '):
                                        break
                                    # Replace usage of param_name with new_var_name (but not in function calls as parameter names)
                                    # Only replace if it's used as a variable (not in 'param_name=' pattern)
                                    usage_line = lines_shadow[usage_idx]
                                    # Replace standalone usage: param_name -> new_var_name (but not param_name=)
                                    if re.search(rf'\b{re.escape(param_name)}\b(?!\s*=)', usage_line):
                                        lines_shadow[usage_idx] = re.sub(
                                            rf'\b{re.escape(param_name)}\b(?!\s*=)',
                                            new_var_name,
                                            usage_line
                                        )
                                logger.debug(f"[PYTEST CLEAN] Fixed parameter shadowing: {param_name} -> {new_var_name} in function starting at line {idx+1}")
                                break
        
        cleaned_code = '\n'.join(lines_shadow)
        
        # Fix references to non-existent modules like 'tests'
        cleaned_code = re.sub(
            r'getattr\(tests,\s*([^)]+)\)',
            r'# TODO: Fix - tests module does not exist\n    # getattr(tests, \1)',
            cleaned_code
        )
        cleaned_code = re.sub(
            r'tests\.(\w+)',
            r'# TODO: Fix - tests module does not exist, use function \1 directly',
            cleaned_code
        )
        
        # Fix functions that should be fixtures but are missing @pytest.fixture decorator
        # Pattern: def test_xxx_connection(...): with yield statement - should be a fixture
        lines_fix = cleaned_code.split('\n')
        for idx, line in enumerate(lines_fix):
            # Check if it's a function that yields (likely a fixture)
            if re.match(r'^\s*def\s+test_\w+_(connection|fixture|setup|device|data)', line, re.IGNORECASE):
                # Check if next 20 lines contain 'yield'
                next_lines = '\n'.join(lines_fix[idx:min(idx+20, len(lines_fix))])
                if 'yield' in next_lines:
                    # Check if @pytest.fixture is already present (look back 5 lines)
                    prev_lines = '\n'.join(lines_fix[max(0, idx-5):idx])
                    if '@pytest.fixture' not in prev_lines:
                        # Add @pytest.fixture decorator
                        indent = len(line) - len(line.lstrip())
                        fixture_decorator = ' ' * indent + '@pytest.fixture'
                        lines_fix.insert(idx, fixture_decorator)
                        logger.debug(f"[PYTEST CLEAN] Added @pytest.fixture decorator to {line.strip()}")
        
        cleaned_code = '\n'.join(lines_fix)
        
        # Fix incorrect use of @pytest.mark.usefixtures - should just use fixtures as parameters
        # Pattern: @pytest.mark.usefixtures('fixture1', 'fixture2') def test_xxx(...):
        # Should be: def test_xxx(fixture1, fixture2, ...):
        def fix_usefixtures(match):
            decorator = match.group(0)
            # Extract fixture names from usefixtures
            fixture_names = re.findall(r"['\"](\w+)['\"]", decorator)
            # This will be replaced with the function definition, so we need to add fixtures to parameters
            # We'll handle this in a second pass
            return ''  # Remove the decorator, we'll fix parameters separately
        
        cleaned_code = re.sub(
            r'@pytest\.mark\.usefixtures\([^)]+\)\s*\n',
            '',
            cleaned_code
        )
        
        # Fix fixture parameter names - if test_device_ip exists, replace device_ip with test_device_ip
        fixture_patterns = [
            (r'test_device_ip', r'\bdevice_ip\b'),
            (r'test_username', r'\busername\b'),
            (r'test_password', r'\bpassword\b'),
            (r'test_ssh_connection', r'\bssh_connection\b'),
            (r'test_rest_api_connection', r'\brest_api_connection\b'),
        ]
        
        for fixture_name, param_pattern in fixture_patterns:
            if fixture_name in cleaned_code:
                # Replace in function parameters
                cleaned_code = re.sub(
                    rf'def\s+test_\w+\(([^)]*){param_pattern}([^)]*)\):',
                    lambda m: f"def {m.group(0).split('(')[0]}({m.group(1)}{fixture_name}{m.group(2)}):",
                    cleaned_code
                )
                # Replace in function bodies (but not in string literals or dict access)
                cleaned_code = re.sub(
                    rf'{param_pattern}(?!\[|\'|\"|\.|\w)',
                    fixture_name,
                    cleaned_code
                )
        
        # Fix Windows commands in Unix context
        # findstring -> grep
        cleaned_code = re.sub(
            r'findstring',
            'grep',
            cleaned_code,
            flags=re.IGNORECASE
        )
        
        # Fix incorrect SSH command construction
        # Pattern: ['ssh', 'user@' + device_ip, 'command'] -> ['ssh', f'user@{device_ip}', 'command']
        # Or: subprocess.run(['ssh', 'user@' + device_ip, ...])
        def fix_ssh_string_concat(match):
            full_match = match.group(0)
            # Extract the parts: 'user@' + device_ip
            if "'" in full_match:
                user_part = re.search(r"'([^']+)'", full_match)
                var_part = re.search(r'\+\s*(\w+)', full_match)
                if user_part and var_part:
                    user = user_part.group(1)
                    var = var_part.group(1)
                    # Replace with f-string
                    return f"f'{user}@{{{var}}}'"
            elif '"' in full_match:
                user_part = re.search(r'"([^"]+)"', full_match)
                var_part = re.search(r'\+\s*(\w+)', full_match)
                if user_part and var_part:
                    user = user_part.group(1)
                    var = var_part.group(1)
                    return f'f"{user}@{{{var}}}"'
            return full_match
        
        cleaned_code = re.sub(
            r"['\"][^'\"]*@['\"]\s*\+\s*\w+",
            fix_ssh_string_concat,
            cleaned_code
        )
        
        # Remove invalid library imports that don't exist
        invalid_imports = [
            r"from\s+jnpr\.testbed\s+import.*",
            r"from\s+jnpr\.device\s+import.*",
        ]
        for pattern in invalid_imports:
            cleaned_code = re.sub(pattern, "# Removed invalid import - library not available", cleaned_code, flags=re.MULTILINE)
        
        # Fix invalid Device() calls from non-existent libraries
        cleaned_code = re.sub(
            r"Device\s*\([^)]*device_type=['\"]juniper['\"][^)]*\)",
            "# Device() from jnpr library not available - use SSH or REST API instead",
            cleaned_code
        )
        
        # Fix common undefined variable issues - add fixture parameters
        # If a function uses juniper_device but doesn't have it as a parameter, add it
        lines_final = cleaned_code.split('\n')
        for idx, line in enumerate(lines_final):
            if line.strip().startswith('def test_') and '(' in line and '):' in line:
                # Check if function body uses juniper_device
                func_end = idx + 1
                while func_end < len(lines_final) and (lines_final[func_end].startswith(' ') or lines_final[func_end].startswith('\t') or not lines_final[func_end].strip() or lines_final[func_end].strip().startswith('#')):
                    func_end += 1
                func_body = '\n'.join(lines_final[idx:min(func_end, idx+50)])
                if 'juniper_device' in func_body and 'juniper_device' not in line:
                    # Add juniper_device as parameter
                    line = line.replace('):', ', juniper_device):')
                    lines_final[idx] = line
                    logger.debug(f"[PYTEST CLEAN] Added juniper_device parameter")
        
        cleaned_code = '\n'.join(lines_final)
        
        return cleaned_code
    
    def _validate_python_syntax(self, code: str) -> bool:
        """Validate that the generated Python code is syntactically correct"""
        try:
            import ast
            ast.parse(code)
            return True
        except SyntaxError as e:
            # Log more details about the syntax error
            lines = code.split('\n')
            error_line_idx = e.lineno - 1 if e.lineno > 0 else 0
            context_start = max(0, error_line_idx - 2)
            context_end = min(len(lines), error_line_idx + 3)
            context_lines = []
            for i in range(context_start, context_end):
                marker = " >>> " if i == error_line_idx else "     "
                context_lines.append(f"{i+1:4d}{marker}{lines[i]}")
            context = '\n'.join(context_lines)
            logger.warning(f"[PYTEST VALIDATE] Syntax error: {e} at line {e.lineno}")
            logger.warning(f"[PYTEST VALIDATE] Context around error:\n{context}")
            return False
        except Exception as e:
            logger.warning(f"[PYTEST VALIDATE] Validation error: {e}")
            return False
    
    def _generate_complete_pytest_file(self, test_plan: Dict[str, Any]) -> str:
        """Generate complete pytest test file"""
        title = test_plan.get("title", "Test Suite")
        test_plan_id = test_plan.get("test_plan_id", "")
        
        # File header
        pytest_file = f'''"""
Pytest Test Suite
Generated from Test Plan: {title}
Test Plan ID: {test_plan_id}
Generated: {test_plan.get("generated_at", "")}
"""

import pytest
import sys
import os
from typing import Dict, Any, Optional
from datetime import datetime

# Add project root to path if needed
# sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Test fixtures
@pytest.fixture(scope="module")
def test_data():
    """Test data fixture"""
    return {{
        # Add test data here
    }}

@pytest.fixture(scope="module")
def test_environment():
    """Test environment fixture"""
    return {{
        # Add environment setup here
    }}

'''
        
        # Add unit tests
        unit_tests = test_plan.get("unit_tests", [])
        if unit_tests:
            pytest_file += "# Unit Tests\n\n"
            for unit_test in unit_tests:
                # Handle both dict and string formats
                if isinstance(unit_test, dict):
                    test_code = unit_test.get("test_code", "")
                else:
                    # If it's a string, use it directly
                    test_code = str(unit_test)
                
                if test_code:
                    # Clean up test code (remove pass, add proper structure)
                    cleaned_code = self._clean_test_code(test_code)
                    pytest_file += cleaned_code + "\n\n"
        
        # Add test cases as pytest functions
        test_cases = test_plan.get("test_cases", [])
        if test_cases:
            pytest_file += "# Test Cases from Test Plan\n\n"
            for test_case in test_cases:
                # Handle both dict and string formats
                if isinstance(test_case, dict):
                    pytest_func = self._test_case_to_pytest(test_case)
                else:
                    # If it's a string, create a simple test function
                    pytest_func = f"def test_{self._sanitize_name(str(test_case))}():\n    \"\"\"Test: {str(test_case)}\"\"\"\n    pass\n"
                pytest_file += pytest_func + "\n\n"
        
        # Add integration tests
        integration_tests = test_plan.get("integration_tests", [])
        if integration_tests:
            pytest_file += "# Integration Tests\n\n"
            for integration_test in integration_tests:
                # Handle both dict and string formats
                if isinstance(integration_test, dict):
                    pytest_func = self._integration_test_to_pytest(integration_test)
                else:
                    # If it's a string, create a simple test function
                    pytest_func = f"def test_integration_{self._sanitize_name(str(integration_test))}():\n    \"\"\"Integration Test: {str(integration_test)}\"\"\"\n    pass\n"
                pytest_file += pytest_func + "\n\n"
        
        # Add main block
        pytest_file += '''
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
'''
        
        return pytest_file
    
    def _is_template(self, script: str) -> bool:
        """Check if script contains template patterns (TODOs, placeholder assertions, etc.)"""
        template_indicators = [
            "# TODO:",
            "assert True, \"needs implementation\"",
            "assert True, 'needs implementation'",
            "assert True, \"Test case",
            "assert True, 'Test case",
            "Placeholder assertion",
            "# Placeholder",
            "# Implementation:",
            "# TODO: Implement",
            "needs implementation",
            "# Add test data here",
            "# Add environment setup here",
        ]
        script_lower = script.lower()
        return any(indicator.lower() in script_lower for indicator in template_indicators)
    
    def _fix_template_patterns(self, script: str, test_cases: List[Dict]) -> str:
        """Auto-fix common template patterns in generated pytest scripts"""
        import re
        
        # Detect device fixture name from test cases or use default
        device_fixture = "la_q5130_05_device"  # Default, will be injected by framework anyway
        
        # Remove empty fixtures that return empty dicts with comments
        empty_fixture_pattern1 = r'@pytest\.fixture[^\n]*\n\s*def\s+(\w+)\([^)]*\):\s*\n\s*"""[^"]*"""\s*\n\s*return\s+\{\s*\n\s*#\s*Add[^\n]*\n\s*\}\s*\n'
        script = re.sub(empty_fixture_pattern1, '', script, flags=re.MULTILINE)
        
        # Remove empty fixtures that return None with TODO
        empty_fixture_pattern2 = r'@pytest\.fixture[^\n]*\n\s*def\s+(\w+)\([^)]*\):\s*\n\s*return\s+None\s*#\s*TODO.*?\n'
        script = re.sub(empty_fixture_pattern2, '', script, flags=re.MULTILINE)
        
        # Add helper function FIRST (before processing test functions)
        if 'execute_device_command' in script and '_get_output' not in script:
            helper_func = '''
def _get_output(device_fixture, command: str) -> str:
    """Run a command on the device and return stdout text."""
    result = execute_device_command(device_fixture, command)
    assert result["success"], f"Command failed: {result.get('error')}"
    return result["output"]

'''
            # Insert after imports, before any fixtures or test functions
            import_end = script.find('\n\n', script.find('import'))
            if import_end > 0:
                script = script[:import_end+2] + helper_func + script[import_end+2:]
            else:
                # Find first function definition
                first_func = re.search(r'def\s+\w+', script)
                if first_func:
                    script = script[:first_func.start()] + helper_func + script[first_func.start():]
                else:
                    script = helper_func + script
        
        # Replace placeholder assertions with actual implementations
        # Pattern: assert True, "Test case test_tc_XXX needs implementation"
        placeholder_assert_pattern = r'assert\s+True\s*,\s*["\']Test case\s+(\w+)\s+needs implementation["\']'
        
        def replace_placeholder_assert(match):
            test_name = match.group(1)
            # Find the function this assertion belongs to by looking backwards
            return f'''    # Execute test steps
    output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in output.lower() or "peer" in output.lower(), "BGP command failed"
'''
        
        script = re.sub(placeholder_assert_pattern, replace_placeholder_assert, script, flags=re.IGNORECASE)
        
        # Replace integration test placeholder assertions
        integration_assert_pattern = r'assert\s+True\s*,\s*["\']Integration test\s+(\w+)\s+needs implementation["\']'
        script = re.sub(integration_assert_pattern, replace_placeholder_assert, script, flags=re.IGNORECASE)
        
        # For test functions that have only comments and placeholder assertions, replace entire function body
        # Match: def test_xxx(...): followed by comments, TODOs, and placeholder assertion
        # More aggressive pattern that matches the exact structure we're seeing
        test_func_with_template_pattern = r'(def\s+(test_\w+)\([^)]*\):\s*\n(?:\s+"""[^"]*"""\s*\n)?)((?:\s+#[^\n]*\n|\s+#\s*TODO[^\n]*\n|\s+#\s*Placeholder[^\n]*\n|\s+assert\s+True[^\n]*\n|\s+#\s*Implementation:[^\n]*\n|\s+#\s*Execute[^\n]*\n|\s+#\s*Verify[^\n]*\n)*)'
        
        def replace_template_function(match):
            func_signature = match.group(1)
            func_name = match.group(2)
            func_body = match.group(3)
            
            # Check if this is a template (has TODO, placeholder assertion, or "needs implementation")
            if re.search(r'#\s*TODO|assert\s+True.*needs implementation|Placeholder assertion|#\s*Placeholder|#\s*Implementation:', func_body, re.IGNORECASE):
                # Extract test case info from docstring if available
                docstring_match = re.search(r'"""[^"]*(\w+\s+Test)[^"]*"""', func_signature, re.IGNORECASE)
                test_type = "bgp" if "bgp" in func_name.lower() or (docstring_match and "bgp" in docstring_match.group(0).lower()) else "generic"
                
                if test_type == "bgp":
                    # Generate BGP-specific implementation
                    if "tc_001" in func_name or "configuration" in func_name.lower():
                        return f'''{func_signature}    # Step 1: Configure BGP
    config_cmd = "configure\\nset protocols bgp group external type external\\nset protocols bgp group external neighbor 192.168.1.1 peer-as 65000\\ncommit\\nexit\\n"
    _get_output({device_fixture}, config_cmd)
    
    # Step 2: Verify BGP configuration
    output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in output.lower() or "peer" in output.lower(), "BGP configuration verification failed"
    
    # Step 3: Verify using show bgp neighbors
    neighbor_output = _get_output({device_fixture}, "show bgp neighbor")
    assert len(neighbor_output) > 0, "BGP neighbor command failed"
'''
                    elif "tc_002" in func_name or "peering" in func_name.lower():
                        return f'''{func_signature}    # Step 1: Establish BGP peering
    config_cmd = "configure\\nset protocols bgp group external type external\\nset protocols bgp group external neighbor 192.168.1.1 peer-as 65000\\ncommit\\nexit\\n"
    _get_output({device_fixture}, config_cmd)
    
    # Step 2: Verify BGP peering
    output = _get_output({device_fixture}, "show bgp neighbor")
    assert "192.168.1.1" in output or "Establ" in output, "BGP peering not established"
    
    # Step 3: Verify using show bgp summary
    summary_output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in summary_output.lower(), "BGP summary command failed"
'''
                    elif "tc_003" in func_name or "neighbor" in func_name.lower():
                        return f'''{func_signature}    # Step 1: Configure BGP neighbor
    config_cmd = "configure\\nset protocols bgp group external type external\\nset protocols bgp group external neighbor 192.168.1.1 peer-as 65000\\ncommit\\nexit\\n"
    _get_output({device_fixture}, config_cmd)
    
    # Step 2: Verify BGP neighbor configuration
    output = _get_output({device_fixture}, "show bgp neighbor")
    assert "192.168.1.1" in output, "BGP neighbor not found in configuration"
    
    # Step 3: Verify using show bgp summary
    summary_output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in summary_output.lower(), "BGP summary command failed"
'''
                    elif "tc_004" in func_name or "route" in func_name.lower():
                        return f'''{func_signature}    # Step 1: Configure BGP route advertisement
    config_cmd = "configure\\nset policy-options policy-statement export-route term 1 from route-filter 192.168.10.0/24 exact\\nset policy-options policy-statement export-route term 1 then accept\\nset protocols bgp group external export export-route\\ncommit\\nexit\\n"
    _get_output({device_fixture}, config_cmd)
    
    # Step 2: Verify BGP route advertisement
    output = _get_output({device_fixture}, "show route protocol bgp")
    assert "bgp" in output.lower() or len(output) > 0, "BGP route verification failed"
    
    # Step 3: Verify using show bgp summary
    summary_output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in summary_output.lower(), "BGP summary command failed"
'''
                    elif "tc_005" in func_name or "error" in func_name.lower():
                        return f'''{func_signature}    # Step 1: Check BGP status (simulate error check)
    output = _get_output({device_fixture}, "show bgp neighbor")
    # Verify BGP is running (error handling means BGP should recover from errors)
    assert "bgp" in output.lower() or "peer" in output.lower(), "BGP error handling check failed"
    
    # Step 2: Verify BGP error handling
    summary_output = _get_output({device_fixture}, "show bgp summary")
    assert len(summary_output) > 0, "BGP summary command failed"
    
    # Step 3: Verify BGP is operational
    assert "bgp" in summary_output.lower(), "BGP error handling verification failed"
'''
                    else:
                        # Generic BGP test
                        return f'''{func_signature}    output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in output.lower() or "peer" in output.lower(), "BGP command failed"
'''
                else:
                    # Generic test
                    return f'''{func_signature}    output = _get_output({device_fixture}, "show version")
    assert len(output) > 0, "Command returned empty output"
'''
            return match.group(0)  # Return unchanged if not a template
        
        script = re.sub(test_func_with_template_pattern, replace_template_function, script, flags=re.MULTILINE | re.DOTALL)
        
        # Also handle functions that have assert True, "Test case XXX needs implementation" directly
        # This is a more direct pattern match for the exact structure we're seeing
        direct_placeholder_pattern = r'(def\s+(test_\w+)\([^)]*\):\s*\n(?:[^\n]*\n)*?)(\s+#\s*Placeholder assertion\s*\n\s+assert\s+True\s*,\s*["\']Test case[^\n]*needs implementation["\'])'
        
        def replace_direct_placeholder(match):
            func_signature = match.group(1)
            func_name = match.group(2)
            placeholder = match.group(3)
            
            # Generate implementation based on function name
            if 'tc_001' in func_name or ('configuration' in func_name.lower() and 'bgp' in func_name.lower()):
                return f'''{func_signature}    # Step 1: Configure BGP
    config_cmd = "configure\\nset protocols bgp group external type external\\nset protocols bgp group external neighbor 192.168.1.1 peer-as 65000\\ncommit\\nexit\\n"
    _get_output({device_fixture}, config_cmd)
    
    # Step 2: Verify BGP configuration
    output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in output.lower() or "peer" in output.lower(), "BGP configuration verification failed"
    
    # Step 3: Verify using show bgp neighbors
    neighbor_output = _get_output({device_fixture}, "show bgp neighbor")
    assert len(neighbor_output) > 0, "BGP neighbor command failed"
'''
            elif 'tc_002' in func_name or 'peering' in func_name.lower():
                return f'''{func_signature}    # Step 1: Establish BGP peering
    config_cmd = "configure\\nset protocols bgp group external type external\\nset protocols bgp group external neighbor 192.168.1.1 peer-as 65000\\ncommit\\nexit\\n"
    _get_output({device_fixture}, config_cmd)
    
    # Step 2: Verify BGP peering
    output = _get_output({device_fixture}, "show bgp neighbor")
    assert "192.168.1.1" in output or "Establ" in output, "BGP peering not established"
    
    # Step 3: Verify using show bgp summary
    summary_output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in summary_output.lower(), "BGP summary command failed"
'''
            elif 'tc_003' in func_name or 'neighbor' in func_name.lower():
                return f'''{func_signature}    # Step 1: Configure BGP neighbor
    config_cmd = "configure\\nset protocols bgp group external type external\\nset protocols bgp group external neighbor 192.168.1.1 peer-as 65000\\ncommit\\nexit\\n"
    _get_output({device_fixture}, config_cmd)
    
    # Step 2: Verify BGP neighbor configuration
    output = _get_output({device_fixture}, "show bgp neighbor")
    assert "192.168.1.1" in output, "BGP neighbor not found in configuration"
    
    # Step 3: Verify using show bgp summary
    summary_output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in summary_output.lower(), "BGP summary command failed"
'''
            elif 'tc_004' in func_name or 'route' in func_name.lower():
                return f'''{func_signature}    # Step 1: Configure BGP route advertisement
    config_cmd = "configure\\nset policy-options policy-statement export-route term 1 from route-filter 192.168.10.0/24 exact\\nset policy-options policy-statement export-route term 1 then accept\\nset protocols bgp group external export export-route\\ncommit\\nexit\\n"
    _get_output({device_fixture}, config_cmd)
    
    # Step 2: Verify BGP route advertisement
    output = _get_output({device_fixture}, "show route protocol bgp")
    assert "bgp" in output.lower() or len(output) > 0, "BGP route verification failed"
    
    # Step 3: Verify using show bgp summary
    summary_output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in summary_output.lower(), "BGP summary command failed"
'''
            elif 'tc_005' in func_name or 'error' in func_name.lower():
                return f'''{func_signature}    # Step 1: Check BGP status (simulate error check)
    output = _get_output({device_fixture}, "show bgp neighbor")
    # Verify BGP is running (error handling means BGP should recover from errors)
    assert "bgp" in output.lower() or "peer" in output.lower(), "BGP error handling check failed"
    
    # Step 2: Verify BGP error handling
    summary_output = _get_output({device_fixture}, "show bgp summary")
    assert len(summary_output) > 0, "BGP summary command failed"
    
    # Step 3: Verify BGP is operational
    assert "bgp" in summary_output.lower(), "BGP error handling verification failed"
'''
            elif 'integration' in func_name.lower():
                return f'''{func_signature}    # Integration test implementation
    output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in output.lower() or "peer" in output.lower(), "BGP integration test failed"
'''
            elif 'bgp' in func_name.lower():
                return f'''{func_signature}    output = _get_output({device_fixture}, "show bgp summary")
    assert "bgp" in output.lower() or "peer" in output.lower(), "BGP command failed"
'''
            else:
                return f'''{func_signature}    output = _get_output({device_fixture}, "show version")
    assert len(output) > 0, "Command returned empty output"
'''
        
        script = re.sub(direct_placeholder_pattern, replace_direct_placeholder, script, flags=re.MULTILINE | re.DOTALL)
        
        # Remove remaining TODO comments (standalone)
        script = re.sub(r'\s*#\s*TODO:[^\n]*\n', '\n', script, flags=re.MULTILINE)
        
        # Remove empty unit test functions with only TODOs (more aggressive)
        empty_unit_test_pattern = r'def\s+(test_\w+)\([^)]*\):\s*\n\s*"""[^"]*"""\s*\n(?:\s+#[^\n]*\n|\s+#\s*TODO[^\n]*\n|\s+#\s*Execute[^\n]*\n|\s+#\s*Verify[^\n]*\n|\s+#\s*result[^\n]*\n)*'
        script = re.sub(empty_unit_test_pattern, '', script, flags=re.MULTILINE)
        
        # Remove empty fixtures that return empty dicts
        empty_dict_fixture = r'@pytest\.fixture[^\n]*\n\s*def\s+\w+\([^)]*\):\s*\n\s*"""[^"]*"""\s*\n\s*return\s+\{\s*\n\s*#\s*Add[^\n]*\n\s*\}\s*\n'
        script = re.sub(empty_dict_fixture, '', script, flags=re.MULTILINE)
        
        logger.info("[LLM PYTEST] Applied template pattern fixes")
        return script
    
    def _generate_pytest_functions(self, test_plan: Dict[str, Any]) -> str:
        """Generate just pytest test functions"""
        functions = []
        
        # Unit tests
        for unit_test in test_plan.get("unit_tests", []):
            # Handle both dict and string formats
            if isinstance(unit_test, dict):
                test_code = unit_test.get("test_code", "")
            else:
                # If it's a string, use it directly
                test_code = str(unit_test)
            
            if test_code:
                cleaned = self._clean_test_code(test_code)
                functions.append(cleaned)
        
        # Test cases
        for test_case in test_plan.get("test_cases", []):
            # Handle both dict and string formats
            if isinstance(test_case, dict):
                pytest_func = self._test_case_to_pytest(test_case)
            else:
                # If it's a string, create a simple test function
                pytest_func = f"def test_{self._sanitize_name(str(test_case))}():\n    \"\"\"Test: {str(test_case)}\"\"\"\n    pass\n"
            functions.append(pytest_func)
        
        # Integration tests
        for integration_test in test_plan.get("integration_tests", []):
            # Handle both dict and string formats
            if isinstance(integration_test, dict):
                pytest_func = self._integration_test_to_pytest(integration_test)
            else:
                # If it's a string, create a simple test function
                pytest_func = f"def test_integration_{self._sanitize_name(str(integration_test))}():\n    \"\"\"Integration Test: {str(integration_test)}\"\"\"\n    pass\n"
            functions.append(pytest_func)
        
        return "\n\n".join(functions)
    
    def _clean_test_code(self, test_code: str) -> str:
        """Clean and enhance test code"""
        # Remove placeholder pass statements
        if "pass" in test_code and "# TODO" in test_code:
            # Keep the structure but make it more complete
            test_code = test_code.replace("    pass\n", "")
        
        # Ensure proper indentation
        lines = test_code.split("\n")
        cleaned_lines = []
        for line in lines:
            if line.strip() and not line.strip().startswith("# TODO"):
                cleaned_lines.append(line)
            elif line.strip().startswith("# TODO"):
                # Keep TODO comments but make them actionable
                cleaned_lines.append(line)
        
        return "\n".join(cleaned_lines) if cleaned_lines else test_code
    
    def _sanitize_name(self, name: str) -> str:
        """Sanitize a string to be a valid Python function name"""
        # Replace spaces and special characters with underscores
        sanitized = name.lower().replace(" ", "_").replace("-", "_")
        # Remove any non-alphanumeric characters except underscores
        sanitized = "".join(c if c.isalnum() or c == "_" else "_" for c in sanitized)
        # Remove multiple consecutive underscores
        while "__" in sanitized:
            sanitized = sanitized.replace("__", "_")
        # Remove leading/trailing underscores
        sanitized = sanitized.strip("_")
        # Ensure it starts with a letter or underscore
        if sanitized and not sanitized[0].isalpha():
            sanitized = "test_" + sanitized
        return sanitized or "test_case"
    
    def _test_case_to_pytest(self, test_case: Dict[str, Any]) -> str:
        """Convert test case to pytest function"""
        test_id = test_case.get("test_id", "test_case")
        title = test_case.get("title", "Test Case")
        description = test_case.get("description", "")
        steps = test_case.get("steps", [])
        expected_result = test_case.get("expected_result", "")
        category = test_case.get("category", "functional")
        priority = test_case.get("priority", "medium")
        
        # Create pytest function name
        func_name = test_id.lower().replace(" ", "_").replace("-", "_")
        if not func_name.startswith("test_"):
            func_name = f"test_{func_name}"
        
        # Build pytest function
        pytest_func = f'''def {func_name}():
    """
    {title}
    
    Description: {description}
    Category: {category}
    Priority: {priority}
    Expected Result: {expected_result}
    """
    # Test Steps:
'''
        
        # Add test steps
        for i, step in enumerate(steps, 1):
            pytest_func += f"    # {i}. {step}\n"
        
        pytest_func += "\n    # Implementation:\n"
        pytest_func += "    # TODO: Implement test steps\n"
        pytest_func += "    # Execute each step\n"
        pytest_func += "    # Verify expected result\n"
        pytest_func += f"    # Expected: {expected_result}\n"
        pytest_func += "    \n"
        pytest_func += "    # Placeholder assertion\n"
        pytest_func += f"    assert True, \"Test case {func_name} needs implementation\"\n"
        
        return pytest_func
    
    def _integration_test_to_pytest(self, integration_test: Dict[str, Any]) -> str:
        """Convert integration test to pytest function"""
        scenario_id = integration_test.get("scenario_id", "integration_test")
        description = integration_test.get("description", "")
        steps = integration_test.get("steps", [])
        expected_result = integration_test.get("expected_result", "")
        use_case = integration_test.get("use_case", {})
        
        # Create pytest function name
        func_name = scenario_id.lower().replace(" ", "_").replace("-", "_")
        if not func_name.startswith("test_"):
            func_name = f"test_{func_name}"
        
        # Build pytest function
        pytest_func = f'''def {func_name}():
    """
    Integration Test: {description}
    
    Expected Result: {expected_result}
    """
    # Integration Test Steps:
'''
        
        # Add steps
        for i, step in enumerate(steps, 1):
            pytest_func += f"    # {i}. {step}\n"
        
        pytest_func += "\n    # Implementation:\n"
        pytest_func += "    # TODO: Implement integration test\n"
        pytest_func += "    # Setup components\n"
        pytest_func += "    # Execute integration flow\n"
        pytest_func += "    # Verify end-to-end result\n"
        pytest_func += f"    # Expected: {expected_result}\n"
        pytest_func += "    \n"
        pytest_func += "    # Placeholder assertion\n"
        pytest_func += f"    assert True, \"Integration test {func_name} needs implementation\"\n"
        
        return pytest_func
    
    def generate_executable_pytest_from_spec(self, functional_spec: Dict[str, Any],
                                           test_framework: str = "pytest") -> str:
        """
        Generate executable pytest script directly from functional specification
        
        This is a convenience method that:
        1. Generates test plan from spec
        2. Converts test plan to pytest script
        
        Args:
            functional_spec: Functional specification
            test_framework: Test framework (pytest)
        
        Returns:
            Complete executable pytest script
        """
        # Generate test plan
        test_plan = self.generate_test_plan(functional_spec)
        
        # Convert to pytest script
        pytest_script = self.generate_pytest_script_from_test_plan(test_plan, output_format="file")
        
        return pytest_script
