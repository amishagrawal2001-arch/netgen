"""
AI-Powered Code Generator (Cursor.ai-like capabilities)
Generates Python code, test scripts, configurations, and more
"""

import json
import logging
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class CodeGenerator:
    """Generate code using AI (similar to Cursor.ai)"""
    
    def __init__(self, use_ai_api: bool = False, api_key: Optional[str] = None,
                 use_local_llm: bool = True):
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        self.use_local_llm = use_local_llm
        
        # Initialize local LLM if available
        if use_local_llm:
            try:
                from .local_ai_engine import LocalLLMClient
                import os
                self.local_llm = LocalLLMClient(
                    llm_type=os.environ.get("LOCAL_LLM_TYPE", "ollama"),
                    base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434")
                )
            except Exception as e:
                logger.debug(f"Local LLM not available: {e}")
                self.local_llm = None
        else:
            self.local_llm = None
        
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
    
    def generate_code(self, prompt: str, code_type: str = "python",
                      context: Optional[Dict] = None,
                      requirements: Optional[List[str]] = None) -> str:
        """
        Generate code based on natural language prompt
        
        Args:
            prompt: Natural language description of what code to generate
            code_type: Type of code (python, bash, json, yaml, etc.)
            context: Additional context (existing code, configs, etc.)
            requirements: List of specific requirements
        
        Returns:
            Generated code as string
        """
        # Try local LLM first
        if self.use_local_llm and self.local_llm:
            try:
                full_prompt = self._build_prompt(prompt, code_type, context, requirements)
                code = self.local_llm.generate(
                    full_prompt,
                    system_prompt=f"You are an expert {code_type} developer. Generate complete, production-ready code."
                )
                if code and len(code) > 50:  # Basic validation
                    return code
            except Exception as e:
                logger.debug(f"Local LLM generation failed: {e}")
        
        # Fallback to cloud AI
        if self.use_ai_api and self.ai_client:
            return self._ai_generate_code(prompt, code_type, context, requirements)
        
        # Final fallback to template
        return self._template_generate_code(prompt, code_type, context, requirements)
    
    def _build_prompt(self, prompt: str, code_type: str,
                     context: Optional[Dict], requirements: Optional[List[str]]) -> str:
        """Build full prompt for LLM"""
        full_prompt = f"Generate {code_type} code: {prompt}\n\n"
        
        if context:
            full_prompt += f"Context:\n{json.dumps(context, indent=2)}\n\n"
        
        if requirements:
            full_prompt += "Requirements:\n"
            for req in requirements:
                full_prompt += f"- {req}\n"
            full_prompt += "\n"
        
        full_prompt += "Return only the code, no explanations."
        return full_prompt
    
    def _ai_generate_code(self, prompt: str, code_type: str,
                         context: Optional[Dict], requirements: Optional[List[str]]) -> str:
        """Use AI to generate code"""
        if not self.ai_client:
            return self._template_generate_code(prompt, code_type, context, requirements)
        
        full_prompt = f"""
Generate {code_type} code based on this request:

{prompt}

"""
        
        if context:
            full_prompt += f"""
Context:
{json.dumps(context, indent=2)}

"""
        
        if requirements:
            full_prompt += f"""
Requirements:
{chr(10).join(f'- {req}' for req in requirements)}

"""
        
        full_prompt += """
Generate complete, production-ready code. Include:
- Proper imports
- Error handling
- Documentation/comments
- Best practices

Return only the code, no explanations unless requested.
"""
        
        try:
            response = self.ai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": f"You are an expert {code_type} developer. Generate complete, production-ready code."},
                    {"role": "user", "content": full_prompt}
                ],
                temperature=0.3
            )
            
            code = response.choices[0].message.content
            
            # Extract code from markdown if present
            import re
            code_match = re.search(rf'```{code_type}\n(.*?)\n```', code, re.DOTALL)
            if code_match:
                code = code_match.group(1)
            else:
                code_match = re.search(r'```\n(.*?)\n```', code, re.DOTALL)
                if code_match:
                    code = code_match.group(1)
            
            return code
        except Exception as e:
            logger.error(f"AI code generation failed: {e}")
            return self._template_generate_code(prompt, code_type, context, requirements)
    
    def _template_generate_code(self, prompt: str, code_type: str,
                               context: Optional[Dict], requirements: Optional[List[str]]) -> str:
        """Generate code from template (fallback)"""
        if code_type == "python":
            return f"""# Generated code based on: {prompt}

def main():
    # TODO: Implement functionality
    pass

if __name__ == "__main__":
    main()
"""
        elif code_type == "bash":
            return f"""#!/bin/bash
# Generated script based on: {prompt}

# TODO: Implement functionality
echo "Script placeholder"
"""
        else:
            return f"# Generated {code_type} code based on: {prompt}\n# TODO: Implement"
    
    def generate_test_script(self, function_code: str, test_type: str = "pytest") -> str:
        """Generate test script for given function code"""
        prompt = f"""
Generate {test_type} test cases for this Python function:

{function_code}

Include:
- Test cases for normal operation
- Edge cases
- Error handling
- Proper assertions
"""
        
        return self.generate_code(prompt, code_type="python")
    
    def generate_config_file(self, config_type: str, requirements: Dict) -> str:
        """Generate configuration file"""
        prompt = f"""
Generate a {config_type} configuration file with these requirements:
{json.dumps(requirements, indent=2)}
"""
        
        return self.generate_code(prompt, code_type=config_type)
    
    def refactor_code(self, code: str, refactoring_request: str) -> str:
        """Refactor existing code"""
        prompt = f"""
Refactor this code:
{code}

Refactoring request: {refactoring_request}

Return the refactored code with improvements.
"""
        
        return self.generate_code(prompt, code_type="python", context={"original_code": code})
    
    def explain_code(self, code: str) -> str:
        """Explain what code does"""
        if self.use_ai_api and self.ai_client:
            prompt = f"""
Explain what this code does in detail:

{code}

Provide:
1. Overview of what the code does
2. Key functions/classes
3. Important logic flows
4. Dependencies
"""
            
            try:
                response = self.ai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.error(f"AI code explanation failed: {e}")
                return f"Code explanation unavailable. Error: {e}"
        else:
            return "Code explanation requires AI API. Enable use_ai_api and provide api_key."
    
    def fix_code(self, code: str, error_message: Optional[str] = None) -> str:
        """Fix code errors"""
        prompt = f"""
Fix the errors in this code:

{code}
"""
        
        if error_message:
            prompt += f"\nError message: {error_message}\n"
        
        prompt += "\nReturn the fixed code."
        
        return self.generate_code(prompt, code_type="python", context={"original_code": code})
    
    def optimize_code(self, code: str) -> str:
        """Optimize code for performance"""
        prompt = f"""
Optimize this code for better performance:

{code}

Return optimized code with:
- Better algorithms
- Reduced complexity
- Performance improvements
- Maintained functionality
"""
        
        return self.generate_code(prompt, code_type="python", context={"original_code": code})
    
    def generate_documentation(self, code: str, doc_format: str = "markdown") -> str:
        """Generate documentation for code"""
        prompt = f"""
Generate {doc_format} documentation for this code:

{code}

Include:
- Overview
- Function/class descriptions
- Parameters
- Return values
- Examples
- Usage instructions
"""
        
        return self.generate_code(prompt, code_type=doc_format, context={"code": code})
    
    def generate_from_spec(self, spec: Dict[str, Any]) -> str:
        """Generate code from specification"""
        prompt = f"""
Generate code based on this specification:

{json.dumps(spec, indent=2)}

Create complete, working code that implements the specification.
"""
        
        code_type = spec.get("language", "python")
        return self.generate_code(prompt, code_type=code_type, context=spec)

