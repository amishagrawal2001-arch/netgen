"""
AI Agent for Test Plan Generation
Autonomous agent that can reason about requirements and generate comprehensive test plans
"""

import json
import logging
import time
import re
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class AgentState(Enum):
    """Agent execution state"""
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class AgentMessage:
    """Message in agent conversation"""
    role: str  # "user", "assistant", "tool"
    content: str
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Dict]] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentExecutionStep:
    """A step in agent execution"""
    step_id: int
    tool_name: str
    arguments: Dict
    result: Any = None
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentTool:
    """Represents a tool the agent can use"""
    name: str
    description: str
    function: Callable
    parameters: Dict[str, Any]
    category: str = "test_plan"


class TestPlanAgentToolRegistry:
    """Registry of tools available to the test plan agent"""
    
    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None, 
                 model: Optional[str] = None):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model  # Store model name for tool calls
        self.tools: Dict[str, AgentTool] = {}
        self._register_tools()
    
    def register(self, tool: AgentTool):
        """Register a tool"""
        self.tools[tool.name] = tool
    
    def get_tool(self, name: str) -> Optional[AgentTool]:
        """Get a tool by name"""
        return self.tools.get(name)
    
    def list_tools(self) -> List[AgentTool]:
        """List all tools"""
        return list(self.tools.values())
    
    def get_tools_schema(self) -> List[Dict]:
        """Get tools in OpenAI function calling format"""
        schemas = []
        for tool in self.tools.values():
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters
                }
            })
        return schemas
    
    def execute_tool(self, name: str, arguments: Dict) -> Any:
        """Execute a tool with given arguments"""
        tool = self.get_tool(name)
        if not tool:
            raise ValueError(f"Tool '{name}' not found")
        try:
            return tool.function(**arguments)
        except Exception as e:
            logger.error(f"Error executing tool {name}: {str(e)}", exc_info=True)
            raise
    
    def _register_tools(self):
        """Register all test plan generation tools"""
        
        # Tool 1: Generate comprehensive test plan
        self.register(AgentTool(
            name="generate_test_plan",
            description="Generate a comprehensive test plan from functional specification. Use this when user provides requirements, title, description, or use cases. This will create test cases, unit tests, integration tests, and test documentation.",
            function=self._generate_test_plan,
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the feature or functionality to test"
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed description of what needs to be tested"
                    },
                    "requirements": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of requirements that need to be tested, one per line"
                    },
                    "use_cases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of use cases (optional, can be derived from requirements)"
                    },
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Acceptance criteria (optional, can be derived from requirements)"
                    }
                },
                "required": ["title", "requirements"]
            },
            category="test_plan"
        ))
        
        # Tool 2: Generate unit tests only
        self.register(AgentTool(
            name="generate_unit_tests",
            description="Generate unit tests from a test plan or functional specification. Use this when user specifically asks for unit tests or when you need to create unit test code.",
            function=self._generate_unit_tests,
            parameters={
                "type": "object",
                "properties": {
                    "test_plan": {
                        "type": "object",
                        "description": "The test plan dictionary (if available)"
                    },
                    "functional_spec": {
                        "type": "object",
                        "description": "Functional specification dictionary with requirements"
                    },
                    "framework": {
                        "type": "string",
                        "enum": ["pytest", "unittest"],
                        "default": "pytest",
                        "description": "Test framework to use"
                    }
                },
                "required": []
            },
            category="test_plan"
        ))
        
        # Tool 3: Generate pytest script
        self.register(AgentTool(
            name="generate_pytest_script",
            description="Generate executable pytest script from a test plan. Use this when user wants executable test code or when you need to create runnable pytest tests. IMPORTANT: If test_plan is available in context, call this function with ONLY the output_format parameter. Do NOT include test_plan parameter - it will be automatically provided from context.",
            function=self._generate_pytest_script,
            parameters={
                "type": "object",
                "properties": {
                    "output_format": {
                        "type": "string",
                        "enum": ["file", "functions"],
                        "description": "Output format: 'file' for complete pytest file, 'functions' for just test functions. Default is 'file' if not specified."
                    },
                    "test_plan": {
                        "type": ["object", "string"],
                        "description": "DEPRECATED: Do NOT use this parameter. If test_plan is in context, it will be automatically provided. Only use this if test_plan is NOT in context."
                    },
                    "functional_spec": {
                        "type": "object",
                        "description": "Functional specification (only use if test_plan is NOT available in context)"
                    }
                },
                "required": []
            },
            category="test_plan"
        ))
        
        # Tool 4: Analyze requirements (helper tool)
        self.register(AgentTool(
            name="analyze_requirements",
            description="Analyze and extract structured information from user's natural language requirements. Use this when user provides unstructured text that needs to be parsed into title, description, and requirements list.",
            function=self._analyze_requirements,
            parameters={
                "type": "object",
                "properties": {
                    "user_input": {
                        "type": "string",
                        "description": "Natural language input from user describing what needs to be tested"
                    }
                },
                "required": ["user_input"]
            },
            category="test_plan"
        ))
    
    # Tool implementations
    def _generate_test_plan(self, title: str, requirements: List[str], 
                           description: str = None, use_cases: List[str] = None,
                           acceptance_criteria: List[str] = None) -> Dict:
        """Generate comprehensive test plan"""
        try:
            from .test_plan_generator import TestPlanGenerator
            import os
            
            # Use agent's API configuration if available, otherwise fall back to environment
            api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
            api_base = self.api_base or os.environ.get("OPENAI_API_BASE")
            
            # Initialize generator with agent's model and API configuration
            generator = TestPlanGenerator(
                use_ai_api=bool(api_key),
                api_key=api_key,
                use_local_llm=True,
                api_base=api_base,
                model=self.model  # Pass agent's model to generator
            )
            
            # Build functional spec
            functional_spec = {
                "title": title,
                "requirements": requirements
            }
            
            if description:
                functional_spec["description"] = description
            if use_cases:
                functional_spec["use_cases"] = use_cases
            if acceptance_criteria:
                functional_spec["acceptance_criteria"] = acceptance_criteria
            
            # Generate test plan
            test_plan = generator.generate_test_plan(functional_spec)
            
            if isinstance(test_plan, str):
                # Error message
                return {"error": test_plan}

            # v0.5.245-followup (audit AI-*): stamp with 'kind' so the receiver
            # in TestPlanAgent.execute() can distinguish a real test plan from
            # analyze_requirements output (both carry 'title').
            if isinstance(test_plan, dict):
                test_plan.setdefault("kind", "test_plan")

            return test_plan
        
        except Exception as e:
            logger.error(f"Error in _generate_test_plan: {str(e)}", exc_info=True)
            return {"error": f"Failed to generate test plan: {str(e)}"}
    
    def _generate_unit_tests(self, test_plan: Dict = None, functional_spec: Dict = None,
                            framework: str = "pytest") -> Dict:
        """Generate unit tests"""
        try:
            from .test_plan_generator import TestPlanGenerator
            import os
            
            # Use agent's API configuration if available, otherwise fall back to environment
            api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
            api_base = self.api_base or os.environ.get("OPENAI_API_BASE")
            
            generator = TestPlanGenerator(
                use_ai_api=bool(api_key),
                api_key=api_key,
                use_local_llm=True,
                api_base=api_base,
                model=self.model  # Pass agent's model to generator
            )
            
            if test_plan:
                # Extract functional spec from test plan if available
                if not functional_spec:
                    functional_spec = {
                        "title": test_plan.get("title", ""),
                        "requirements": [tc.get("requirement", "") for tc in test_plan.get("test_cases", [])]
                    }
            
            if functional_spec:
                unit_tests = generator.generate_unit_tests_from_spec(functional_spec, test_framework=framework)
                return {"unit_tests": unit_tests, "count": len(unit_tests)}
            
            return {"error": "Either test_plan or functional_spec required"}
        
        except Exception as e:
            logger.error(f"Error in _generate_unit_tests: {str(e)}", exc_info=True)
            return {"error": f"Failed to generate unit tests: {str(e)}"}
    
    def _generate_pytest_script(self, test_plan: Dict = None, functional_spec: Dict = None,
                               output_format: str = "file") -> Dict:
        """Generate pytest script"""
        try:
            from .test_plan_generator import TestPlanGenerator
            import os
            
            # Use agent's API configuration if available, otherwise fall back to environment
            api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
            api_base = self.api_base or os.environ.get("OPENAI_API_BASE")
            
            generator = TestPlanGenerator(
                use_ai_api=bool(api_key),
                api_key=api_key,
                use_local_llm=True,
                api_base=api_base,
                model=self.model  # Pass agent's model to generator
            )
            
            # Ensure output_format is valid
            if output_format not in ["file", "functions"]:
                output_format = "file"
            
            if test_plan:
                # Use test plan generator's pytest generation method
                # The generator will automatically prioritize cloud API if available
                pytest_script = generator.generate_pytest_script_from_test_plan(
                    test_plan, 
                    output_format=output_format
                )
                return {"pytest_script": pytest_script}
            elif functional_spec:
                # Generate pytest from functional spec
                pytest_script = generator.generate_executable_pytest_from_spec(functional_spec, "pytest")
                return {"pytest_script": pytest_script}
            
            return {"error": "Either test_plan or functional_spec required"}
        
        except Exception as e:
            logger.error(f"Error in _generate_pytest_script: {str(e)}", exc_info=True)
            return {"error": f"Failed to generate pytest script: {str(e)}"}
    
    def _analyze_requirements(self, user_input: str) -> Dict:
        """Analyze unstructured requirements and extract structured info"""
        # This is a helper tool that uses LLM to parse natural language
        # For now, return a simple structure
        # In future, can use LLM to intelligently parse
        
        # Simple heuristic: try to extract title and requirements
        lines = user_input.strip().split('\n')
        
        title = lines[0].strip() if lines else "Untitled Feature"
        
        # Try to find requirements (lines that look like requirements)
        requirements = []
        for line in lines[1:]:
            line = line.strip()
            if line and not line.startswith('#'):
                # Remove common prefixes
                for prefix in ['-', '*', '•', '1.', '2.', '3.']:
                    if line.startswith(prefix):
                        line = line[len(prefix):].strip()
                if line:
                    requirements.append(line)
        
        if not requirements:
            # If no requirements found, use the whole input as description
            return {
                "title": title,
                "description": user_input,
                "requirements": [user_input]
            }
        
        return {
            "title": title,
            "description": user_input,
            "requirements": requirements
        }


class TestPlanAgent:
    """
    AI Agent for autonomous test plan generation
    Uses ReAct (Reasoning + Acting) pattern
    """
    
    def __init__(self, tool_registry: TestPlanAgentToolRegistry, llm_client=None, model=None):
        self.tool_registry = tool_registry
        self.llm_client = llm_client
        self.model = model  # Store model name separately
        self.conversation_history: List[AgentMessage] = []
        self.execution_steps: List[AgentExecutionStep] = []
        self.state = AgentState.IDLE
        self.max_iterations = 10
        self.context: Dict = {}  # Store context for tool execution
    
    def execute(self, user_request: str, context: Dict = None) -> Dict:
        """
        Execute a user request using agent reasoning and tool execution
        
        Args:
            user_request: Natural language request from user
            context: Optional context (device_id, etc.)
        
        Returns:
            Dict with final_response, steps, test_plan (if generated), and metadata
        """
        self.state = AgentState.PLANNING
        context = context or {}
        self.context = context  # Store context for tool execution
        self.conversation_history = []  # Reset for new request
        self.execution_steps = []
        
        # Add user message to history, including context information if available
        user_message = user_request
        if context and "test_plan" in context:
            test_plan = context["test_plan"]
            if isinstance(test_plan, dict):
                # Add context hint to user message
                user_message = f"{user_request}\n\nIMPORTANT: A test_plan object is available in the context. When calling 'generate_pytest_script', DO NOT include the 'test_plan' parameter - call it as: generate_pytest_script(output_format='file') without any test_plan parameter. The test_plan will be automatically provided."
        
        self.conversation_history.append(AgentMessage(
            role="user",
            content=user_message
        ))
        
        # Build system prompt
        system_prompt = self._build_system_prompt(context)
        
        iteration = 0
        final_response = None
        generated_test_plan = None
        
        while iteration < self.max_iterations:
            iteration += 1
            self.state = AgentState.PLANNING
            
            try:
                # Get LLM response with tool calling
                messages = self._build_messages_for_llm(system_prompt)
                llm_response = self._call_llm(messages)
                
                # Parse LLM response
                if llm_response.get("tool_calls"):
                    # Agent wants to call tools
                    self.state = AgentState.EXECUTING
                    tool_results = self._execute_tool_calls(llm_response["tool_calls"])
                    
                    # Check if we got a test plan from the results
                    # v0.5.245-followup (audit AI-*): the previous 'title in result'
                    # check misidentified analyze_requirements output (which also
                    # carries 'title') as the test plan. Require the generator's
                    # own marker ('kind' == 'test_plan') or an explicit
                    # 'test_cases' collection before treating the result as one.
                    for result in tool_results:
                        result_payload = result.get("result")
                        if not isinstance(result_payload, dict):
                            continue
                        if "test_plan" in result_payload:
                            generated_test_plan = result_payload["test_plan"]
                        elif result_payload.get("kind") == "test_plan" or "test_cases" in result_payload:
                            generated_test_plan = result_payload
                    
                    # Add tool results to conversation
                    self.conversation_history.append(AgentMessage(
                        role="tool",
                        content="Tool execution completed",
                        tool_results=tool_results
                    ))
                    
                    # Continue loop to get next LLM response based on tool results
                    continue
                else:
                    # Agent has final answer
                    final_response = llm_response.get("content", "")
                    self.state = AgentState.COMPLETED
                    break
            
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Error in agent iteration {iteration}: {error_msg}", exc_info=True)
                self.state = AgentState.ERROR
                
                # Provide more user-friendly error messages
                if "tool_use_failed" in error_msg or "Failed to call a function" in error_msg:
                    if "generate_pytest_script" in error_msg:
                        final_response = "Error: The AI model generated an invalid function call. This can happen with certain models. Please try again, or use a different model (e.g., GPT-4 instead of llama-3.1-8b-instant)."
                    else:
                        final_response = f"Error: The AI model generated an invalid function call: {error_msg}. Please try again."
                else:
                    final_response = f"Error during agent execution: {error_msg}"
                break
        
        return {
            "response": final_response,
            "test_plan": generated_test_plan,
            "steps": [self._step_to_dict(step) for step in self.execution_steps],
            "iterations": iteration,
            "state": self.state.value
        }
    
    def _step_to_dict(self, step: AgentExecutionStep) -> Dict:
        """Convert execution step to dictionary"""
        step_dict = {
            "step_id": step.step_id,
            "tool_name": step.tool_name,
            "arguments": step.arguments,
            "has_result": step.result is not None,
            "error": step.error,
            "timestamp": step.timestamp
        }
        # Include actual result if available (for pytest_script extraction)
        if step.result is not None:
            step_dict["result"] = step.result
        return step_dict
    
    def _build_system_prompt(self, context: Dict) -> str:
        """Build system prompt with tool descriptions"""
        tools_schema = self.tool_registry.get_tools_schema()
        
        # Build context information string
        context_info = ""
        if context:
            if "test_plan" in context:
                test_plan = context["test_plan"]
                if isinstance(test_plan, dict):
                    context_info = f"\n**Available Context:**\n"
                    context_info += f"- A test_plan object is available in the context\n"
                    context_info += f"- Test plan title: {test_plan.get('title', 'N/A')}\n"
                    context_info += f"- **CRITICAL: When calling 'generate_pytest_script', DO NOT include the 'test_plan' parameter in your function call at all.**\n"
                    context_info += f"- **Simply call: generate_pytest_script(output_format='file') without any test_plan parameter.**\n"
                    context_info += f"- The test_plan will be automatically provided from context - you do not need to pass it.\n"
                    context_info += f"- If you try to pass test_plan (even as an object), the API may reject it. Leave it out entirely.\n"
        
        prompt = f"""You are NetGenAI Test Plan Agent, an autonomous AI agent specialized in generating comprehensive test plans.

**Available Tools:**
{json.dumps(tools_schema, indent=2)}
{context_info}
**Your Purpose:**
Help users create comprehensive test plans by understanding their requirements and generating:
- Test cases
- Unit tests
- Integration tests
- Test documentation
- Executable pytest scripts

**How to Work:**
1. When user provides requirements (natural language or structured), analyze what they need
2. Use 'analyze_requirements' tool if input is unstructured
3. Use 'generate_test_plan' to create comprehensive test plan
4. Optionally use 'generate_unit_tests' or 'generate_pytest_script' if user wants specific outputs
5. Provide clear, helpful responses explaining what you've created

**Important Rules:**
- Always use tools when user wants test plans generated
- Analyze user input to extract requirements, title, and description
- Generate comprehensive test plans when possible
- Break down complex requirements into specific, testable scenarios
- Include acceptance criteria tests when requirements are provided
- Generate detailed integration tests with specific test scenarios
- If user asks for specific outputs (like pytest scripts), generate those too
- **CRITICAL: When calling 'generate_pytest_script' with a test_plan from context, pass it as an object/dictionary, NOT as a string**
- Explain what you're doing in your responses

**Quality Guidelines:**
- Generate specific, actionable test cases (avoid generic placeholders)
- Break down complex features into multiple focused test scenarios
- Include both positive and negative test cases when appropriate
- Provide detailed steps for integration tests
- Include acceptance criteria validation tests"""
        
        return prompt
    
    def _build_messages_for_llm(self, system_prompt: str) -> List[Dict]:
        """Convert conversation history to LLM message format"""
        messages = [{"role": "system", "content": system_prompt}]
        
        for msg in self.conversation_history:
            if msg.role == "user":
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                # Add assistant message with tool calls if any
                assistant_msg = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    # Format tool calls for OpenAI API
                    tool_calls_list = []
                    for tc in msg.tool_calls:
                        tool_calls_list.append({
                            "id": tc.get("id", f"call_{len(tool_calls_list)}"),
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"])
                            }
                        })
                    assistant_msg["tool_calls"] = tool_calls_list
                messages.append(assistant_msg)
            elif msg.role == "tool":
                # Format tool results
                for result in (msg.tool_results or []):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": result.get("tool_call_id", ""),
                        "content": json.dumps(result.get("result", {}))
                    })
        
        return messages
    
    def _call_llm(self, messages: List[Dict]) -> Dict:
        """Call LLM with function calling enabled"""
        tools_schema = self.tool_registry.get_tools_schema()
        
        if not self.llm_client:
            raise ValueError("LLM client not initialized")
        
        try:
            # Try OpenAI-compatible function calling
            # Use stored model name, or try to get from client, or default to gpt-4
            model_name = self.model or getattr(self.llm_client, 'model', None) or 'gpt-4'
            response = self.llm_client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools_schema,
                tool_choice="auto"
            )
            
            message = response.choices[0].message
            
            # Check if model wants to call tools
            tool_calls = []
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tool_call in message.tool_calls:
                    try:
                        # Try to parse arguments JSON
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError as e:
                        # v0.5.245-followup (audit AI-*): the previous fallback
                        # unconditionally set arguments['output_format'] = 'file',
                        # which is only a valid parameter on generate_pytest_script.
                        # For every other tool this fabricated an unknown kwarg;
                        # for tools with required positional args (e.g.
                        # generate_test_plan needs title + requirements) that
                        # blew up inside the tool with a confusing signature
                        # error instead of surfacing the parse failure. Now the
                        # default is only applied when the target tool actually
                        # accepts output_format, and other tools return a
                        # structured error rather than silently inventing args.
                        raw_args = tool_call.function.arguments
                        tool_name = tool_call.function.name
                        logger.warning(
                            f"[TEST PLAN AGENT] Failed to parse tool call arguments JSON for {tool_name}: {e}"
                        )
                        logger.debug(f"[TEST PLAN AGENT] Raw arguments: {raw_args}")

                        if tool_name == "generate_pytest_script":
                            # Only this tool accepts output_format; safe to default it.
                            arguments = {}
                            format_match = re.search(r'"output_format"\s*:\s*"([^"]+)"', raw_args or "")
                            arguments["output_format"] = format_match.group(1) if format_match else "file"
                        else:
                            # Any other tool: don't fabricate args. Surface the
                            # parse failure as a tool-call error so the agent
                            # loop reports it instead of calling with garbage.
                            raise ValueError(
                                f"Malformed tool_call arguments JSON from LLM for tool "
                                f"'{tool_name}': {e}. Raw: {raw_args!r}"
                            )
                    
                    tool_calls.append({
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "arguments": arguments
                    })
            
            # Add assistant message to history
            self.conversation_history.append(AgentMessage(
                role="assistant",
                content=message.content,
                tool_calls=tool_calls
            ))
            
            return {
                "content": message.content,
                "tool_calls": tool_calls
            }
        
        except Exception as e:
            logger.error(f"Error calling LLM: {str(e)}", exc_info=True)
            raise
    
    def _execute_tool_calls(self, tool_calls: List[Dict]) -> List[Dict]:
        """Execute tool calls and return results"""
        results = []
        
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            arguments = tool_call["arguments"]
            
            # Auto-inject test_plan from context if needed
            if tool_name == "generate_pytest_script":
                # Always inject test_plan from context if available, regardless of what LLM passed
                if "test_plan" in self.context and isinstance(self.context["test_plan"], dict):
                    # Check if LLM passed a string or missing value
                    llm_test_plan = arguments.get("test_plan")
                    if not isinstance(llm_test_plan, dict):
                        logger.info(f"[TEST PLAN AGENT] Auto-injecting test_plan from context (LLM passed: {type(llm_test_plan).__name__})")
                        arguments["test_plan"] = self.context["test_plan"]
                    else:
                        # LLM passed a dict, but we prefer context version if available
                        logger.debug("[TEST PLAN AGENT] LLM provided test_plan dict, using it")
                elif "test_plan" not in arguments:
                    # No test_plan in arguments and no context - this will fail, but let it fail gracefully
                    logger.warning("[TEST PLAN AGENT] No test_plan in context or arguments for generate_pytest_script")
            
            try:
                # Execute tool
                result = self.tool_registry.execute_tool(tool_name, arguments)
                
                # Record execution step
                step = AgentExecutionStep(
                    step_id=len(self.execution_steps) + 1,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result
                )
                self.execution_steps.append(step)
                
                results.append({
                    "tool_call_id": tool_call.get("id"),
                    "tool_name": tool_name,
                    "result": result
                })
            except Exception as e:
                # Record error
                error_msg = str(e)
                step = AgentExecutionStep(
                    step_id=len(self.execution_steps) + 1,
                    tool_name=tool_name,
                    arguments=arguments,
                    error=error_msg
                )
                self.execution_steps.append(step)
                
                results.append({
                    "tool_call_id": tool_call.get("id"),
                    "tool_name": tool_name,
                    "error": error_msg
                })
        
        return results

