"""
Advanced AI-Powered Code Generator
Multi-language support, network-specific code, configuration templates
"""

import json
import logging
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class AdvancedCodeGenerator:
    """Advanced code generator with multi-language and network-specific support"""
    
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
    
    def generate_network_script(self, requirements: Dict[str, Any]) -> str:
        """
        Generate network automation script
        
        Args:
            requirements: Dictionary with requirements
                - description: What the script should do
                - library: Library to use (scapy, netmiko, napalm, paramiko)
                - devices: List of devices or device patterns
                - operations: List of operations to perform
                - output_format: Output format (json, yaml, text)
        
        Returns:
            Complete Python script
        """
        description = requirements.get("description", "")
        library = requirements.get("library", "netmiko")
        devices = requirements.get("devices", [])
        operations = requirements.get("operations", [])
        output_format = requirements.get("output_format", "json")
        
        # Build prompt
        prompt = f"""Generate a Python network automation script using {library}.

Requirements:
- Description: {description}
- Library: {library}
- Devices: {json.dumps(devices)}
- Operations: {json.dumps(operations)}
- Output format: {output_format}

The script should:
1. Import necessary libraries
2. Connect to devices
3. Perform operations
4. Handle errors gracefully
5. Output results in {output_format} format
6. Include logging
7. Include proper error handling

Generate complete, production-ready code."""
        
        return self._generate_code(prompt, "python")
    
    def generate_config_template(self, vendor: str, requirements: Dict[str, Any]) -> str:
        """
        Generate device configuration template
        
        Args:
            vendor: Device vendor (juniper, cisco, arista, nokia)
            requirements: Configuration requirements
                - protocols: List of protocols (bgp, ospf, isis)
                - interfaces: Interface configurations
                - routing: Routing configurations
                - security: Security configurations
        
        Returns:
            Configuration template in vendor-specific format
        """
        protocols = requirements.get("protocols", [])
        interfaces = requirements.get("interfaces", [])
        routing = requirements.get("routing", {})
        security = requirements.get("security", {})
        
        # Vendor-specific templates
        if vendor.lower() == "juniper":
            return self._generate_juniper_config(protocols, interfaces, routing, security)
        elif vendor.lower() == "cisco":
            return self._generate_cisco_config(protocols, interfaces, routing, security)
        elif vendor.lower() == "arista":
            return self._generate_arista_config(protocols, interfaces, routing, security)
        else:
            return self._generate_generic_config(vendor, protocols, interfaces, routing, security)
    
    def generate_code(self, language: str, prompt: str, 
                     context: Optional[Dict] = None,
                     requirements: Optional[List[str]] = None) -> str:
        """
        Generate code in specified language
        
        Args:
            language: Programming language (python, bash, go, yaml, json)
            prompt: Natural language description
            context: Additional context
            requirements: List of requirements
        
        Returns:
            Generated code
        """
        full_prompt = f"Generate {language} code: {prompt}"
        
        if context:
            full_prompt += f"\n\nContext:\n{json.dumps(context, indent=2)}"
        
        if requirements:
            full_prompt += "\n\nRequirements:"
            for req in requirements:
                full_prompt += f"\n- {req}"
        
        return self._generate_code(full_prompt, language)
    
    def _generate_code(self, prompt: str, language: str) -> str:
        """Generate code using AI or templates"""
        # Try local LLM first
        if self.use_local_llm and self.local_llm:
            try:
                system_prompt = f"You are an expert {language} developer. Generate complete, production-ready code with proper error handling, logging, and documentation."
                code = self.local_llm.generate(prompt, system_prompt=system_prompt)
                if code and len(code) > 50:
                    return self._extract_code_from_response(code, language)
            except Exception as e:
                logger.debug(f"Local LLM generation failed: {e}")
        
        # Fallback to cloud AI
        if self.use_ai_api and self.ai_client:
            try:
                response = self.ai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": f"You are an expert {language} developer. Generate complete, production-ready code."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3
                )
                code = response.choices[0].message.content
                return self._extract_code_from_response(code, language)
            except Exception as e:
                logger.error(f"AI API generation failed: {e}")
        
        # Final fallback to template
        return self._template_generate_code(prompt, language)
    
    def _extract_code_from_response(self, response: str, language: str) -> str:
        """Extract code from AI response (may include markdown formatting)"""
        # Remove markdown code blocks if present
        if "```" in response:
            lines = response.split("\n")
            code_lines = []
            in_code_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_code_block = not in_code_block
                    continue
                if in_code_block:
                    code_lines.append(line)
            return "\n".join(code_lines)
        return response
    
    def _template_generate_code(self, prompt: str, language: str) -> str:
        """Generate code from template (fallback)"""
        if language == "python":
            return self._python_template(prompt)
        elif language == "bash":
            return self._bash_template(prompt)
        elif language == "yaml":
            return self._yaml_template(prompt)
        elif language == "json":
            return self._json_template(prompt)
        else:
            return f"# {language} code generation not yet implemented\n# Request: {prompt}"
    
    def _generate_juniper_config(self, protocols: List[str], interfaces: List[Dict],
                                 routing: Dict, security: Dict) -> str:
        """Generate Juniper configuration"""
        config_lines = ["# Juniper Configuration"]
        
        # Interfaces
        if interfaces:
            config_lines.append("interfaces {")
            for iface in interfaces:
                iface_name = iface.get("name", "ge-0/0/0")
                ip = iface.get("ip", "")
                config_lines.append(f"    {iface_name} {{")
                if ip:
                    config_lines.append(f"        unit 0 {{")
                    config_lines.append(f"            family inet {{")
                    config_lines.append(f"                address {ip};")
                    config_lines.append(f"            }}")
                    config_lines.append(f"        }}")
                config_lines.append(f"    }}")
            config_lines.append("}")
        
        # Protocols
        if "bgp" in protocols:
            bgp_config = routing.get("bgp", {})
            asn = bgp_config.get("asn", 65000)
            config_lines.append(f"protocols {{")
            config_lines.append(f"    bgp {{")
            config_lines.append(f"        group external {{")
            config_lines.append(f"            type external;")
            config_lines.append(f"            local-as {asn};")
            if bgp_config.get("neighbors"):
                for neighbor in bgp_config["neighbors"]:
                    config_lines.append(f"            neighbor {neighbor} {{")
                    config_lines.append(f"                peer-as {bgp_config.get('peer_as', 65001)};")
                    config_lines.append(f"            }}")
            config_lines.append(f"        }}")
            config_lines.append(f"    }}")
            config_lines.append(f"}}")
        
        if "ospf" in protocols:
            ospf_config = routing.get("ospf", {})
            area = ospf_config.get("area", "0.0.0.0")
            config_lines.append(f"protocols {{")
            config_lines.append(f"    ospf {{")
            config_lines.append(f"        area {area} {{")
            if interfaces:
                for iface in interfaces:
                    iface_name = iface.get("name", "ge-0/0/0")
                    config_lines.append(f"            interface {iface_name}.0;")
            config_lines.append(f"        }}")
            config_lines.append(f"    }}")
            config_lines.append(f"}}")
        
        return "\n".join(config_lines)
    
    def _generate_cisco_config(self, protocols: List[str], interfaces: List[Dict],
                              routing: Dict, security: Dict) -> str:
        """Generate Cisco IOS configuration"""
        config_lines = ["! Cisco IOS Configuration"]
        
        # Interfaces
        if interfaces:
            for iface in interfaces:
                iface_name = iface.get("name", "GigabitEthernet0/0")
                ip = iface.get("ip", "")
                config_lines.append(f"interface {iface_name}")
                if ip:
                    config_lines.append(f" ip address {ip}")
                config_lines.append(" no shutdown")
                config_lines.append("!")
        
        # Protocols
        if "bgp" in protocols:
            bgp_config = routing.get("bgp", {})
            asn = bgp_config.get("asn", 65000)
            config_lines.append(f"router bgp {asn}")
            if bgp_config.get("neighbors"):
                for neighbor in bgp_config["neighbors"]:
                    config_lines.append(f" neighbor {neighbor} remote-as {bgp_config.get('peer_as', 65001)}")
            config_lines.append("!")
        
        if "ospf" in protocols:
            ospf_config = routing.get("ospf", {})
            process_id = ospf_config.get("process_id", 1)
            area = ospf_config.get("area", "0")
            config_lines.append(f"router ospf {process_id}")
            if interfaces:
                for iface in interfaces:
                    iface_name = iface.get("name", "GigabitEthernet0/0")
                    config_lines.append(f" network {iface.get('network', '0.0.0.0')} 0.0.0.0 area {area}")
            config_lines.append("!")
        
        return "\n".join(config_lines)
    
    def _generate_arista_config(self, protocols: List[str], interfaces: List[Dict],
                               routing: Dict, security: Dict) -> str:
        """Generate Arista EOS configuration"""
        config_lines = ["! Arista EOS Configuration"]
        
        # Similar to Cisco but with EOS-specific syntax
        return self._generate_cisco_config(protocols, interfaces, routing, security)
    
    def _generate_generic_config(self, vendor: str, protocols: List[str],
                                 interfaces: List[Dict], routing: Dict, security: Dict) -> str:
        """Generate generic configuration template"""
        return f"""# {vendor.capitalize()} Configuration Template
# Protocols: {', '.join(protocols)}
# Interfaces: {len(interfaces)}
# Generated configuration template
# Please customize for your specific {vendor} device
"""
    
    def _python_template(self, prompt: str) -> str:
        """Python code template"""
        return f"""#!/usr/bin/env python3
\"\"\"
Generated Python Script
Request: {prompt}
\"\"\"

import logging
import sys
from typing import Dict, List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    \"\"\"Main function\"\"\"
    try:
        # TODO: Implement functionality based on: {prompt}
        logger.info("Script started")
        
        # Your code here
        
        logger.info("Script completed successfully")
        return 0
    except Exception as e:
        logger.error(f"Error: {{e}}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
"""
    
    def _bash_template(self, prompt: str) -> str:
        """Bash script template"""
        return f"""#!/bin/bash
# Generated Bash Script
# Request: {prompt}

set -euo pipefail

# Error handling
error_exit() {{
    echo "Error: $1" >&2
    exit 1
}}

# Main script
main() {{
    echo "Script: {prompt}"
    # TODO: Implement functionality
}}

# Run main function
main "$@"
"""
    
    def _yaml_template(self, prompt: str) -> str:
        """YAML template"""
        return f"""# Generated YAML Configuration
# Request: {prompt}

# TODO: Add YAML configuration based on requirements
config:
  # Add configuration here
"""
    
    def _json_template(self, prompt: str) -> str:
        """JSON template"""
        return json.dumps({
            "generated": True,
            "request": prompt,
            "config": {
                "todo": "Add configuration based on requirements"
            }
        }, indent=2)




