"""
Local AI Engine - Runs AI features locally without cloud API
Uses scikit-learn for ML and local LLMs for code generation
"""

import json
import logging
import os
from typing import Dict, List, Optional, Any
import pickle
from pathlib import Path

# Optional import for numpy (only needed for LocalAIEngine, not LocalLLMClient)
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None

logger = logging.getLogger(__name__)


class LocalAIEngine:
    """Local AI engine using scikit-learn and local LLMs"""
    
    def __init__(self, model_dir: str = "/opt/OSTG/ai_models"):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.models = {}
        self._init_models()
    
    def _init_models(self):
        """Initialize local ML models"""
        try:
            # Try to load existing models
            self._load_models()
        except Exception as e:
            logger.info(f"No existing models found, will create new ones: {e}")
    
    def _load_models(self):
        """Load saved models from disk"""
        # Load troubleshooting classifier if exists
        classifier_path = self.model_dir / "troubleshooting_classifier.pkl"
        if classifier_path.exists():
            with open(classifier_path, 'rb') as f:
                self.models['troubleshooting'] = pickle.load(f)
        
        # Load test suggestion model if exists
        suggestion_path = self.model_dir / "test_suggestion_model.pkl"
        if suggestion_path.exists():
            with open(suggestion_path, 'rb') as f:
                self.models['test_suggestion'] = pickle.load(f)
    
    def _save_model(self, model_name: str, model):
        """Save model to disk"""
        model_path = self.model_dir / f"{model_name}.pkl"
        with open(model_path, 'wb') as f:
            pickle.dump(model, f)
        logger.info(f"Saved model: {model_name}")
    
    def diagnose_with_ml(self, symptoms: Dict, device_config: Optional[Dict] = None,
                         training_data: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Diagnose using local ML model
        
        Args:
            symptoms: Dictionary of symptoms
            device_config: Device configuration
            training_data: Historical troubleshooting cases for training
        
        Returns:
            Diagnosis dictionary
        """
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.feature_extraction import DictVectorizer
            from sklearn.preprocessing import LabelEncoder
        except ImportError:
            logger.warning("scikit-learn not installed. Install with: pip install scikit-learn")
            return self._rule_based_diagnosis(symptoms, device_config)
        
        # If we have training data, train/update model
        if training_data and len(training_data) > 10:
            model = self._train_troubleshooting_model(training_data)
            self.models['troubleshooting'] = model
        
        # Use existing model or rule-based fallback
        if 'troubleshooting' in self.models:
            return self._ml_diagnosis(symptoms, device_config)
        else:
            return self._rule_based_diagnosis(symptoms, device_config)
    
    def _train_troubleshooting_model(self, training_data: List[Dict]) -> Any:
        """Train troubleshooting classifier from historical cases"""
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.feature_extraction import DictVectorizer
            from sklearn.preprocessing import LabelEncoder
            
            # Prepare features and labels
            X = []
            y = []
            
            for case in training_data:
                # Extract features from symptoms
                features = self._extract_features(case.get('symptoms', {}))
                X.append(features)
                # Use root_cause as label
                y.append(case.get('root_cause', 'unknown'))
            
            if len(X) < 5:
                logger.warning("Not enough training data")
                return None
            
            # Vectorize features
            vectorizer = DictVectorizer(sparse=False)
            X_vec = vectorizer.fit_transform(X)
            
            # Encode labels
            label_encoder = LabelEncoder()
            y_encoded = label_encoder.fit_transform(y)
            
            # Train model
            model = RandomForestClassifier(n_estimators=100, random_state=42)
            model.fit(X_vec, y_encoded)
            
            # Store vectorizer and encoder with model
            model.vectorizer = vectorizer
            model.label_encoder = label_encoder
            
            # Save model
            self._save_model('troubleshooting_classifier', model)
            
            logger.info(f"Trained troubleshooting model on {len(X)} cases")
            return model
        
        except Exception as e:
            logger.error(f"Failed to train model: {e}")
            return None
    
    def _extract_features(self, symptoms: Dict) -> Dict:
        """Extract features from symptoms dictionary"""
        features = {
            'interface_down': 1 if symptoms.get('interface_down') else 0,
            'link_down': 1 if symptoms.get('link_down') else 0,
            'packet_loss': float(symptoms.get('packet_loss', 0)),
            'bgp_not_established': 1 if symptoms.get('bgp_not_established') else 0,
            'ospf_not_established': 1 if symptoms.get('ospf_not_established') else 0,
            'isis_not_established': 1 if symptoms.get('isis_not_established') else 0,
            'latency': float(symptoms.get('latency', 0)),
            'cpu_high': 1 if symptoms.get('cpu_high') else 0,
            'memory_high': 1 if symptoms.get('memory_high') else 0,
        }
        return features
    
    def _ml_diagnosis(self, symptoms: Dict, device_config: Optional[Dict]) -> Dict:
        """Use ML model for diagnosis"""
        model = self.models['troubleshooting']
        
        # Extract features
        features = self._extract_features(symptoms)
        features_vec = model.vectorizer.transform([features])
        
        # Predict
        prediction = model.predict(features_vec)[0]
        probabilities = model.predict_proba(features_vec)[0]
        
        # Decode label
        root_cause = model.label_encoder.inverse_transform([prediction])[0]
        confidence = float(max(probabilities))
        
        # Generate solutions based on root cause
        solutions = self._get_solutions_for_root_cause(root_cause)
        
        return {
            "diagnosis": "ML-powered analysis",
            "root_cause": root_cause,
            "solutions": solutions,
            "confidence": confidence,
            "source": "local_ml"
        }
    
    def _rule_based_diagnosis(self, symptoms: Dict, device_config: Optional[Dict]) -> Dict:
        """Fallback rule-based diagnosis"""
        diagnosis = {
            "diagnosis": "Rule-based analysis",
            "root_cause": "Unknown issue",
            "solutions": [],
            "confidence": 0.5,
            "source": "rule_based"
        }
        
        if symptoms.get('interface_down') or symptoms.get('link_down'):
            diagnosis["root_cause"] = "Interface is down"
            diagnosis["solutions"] = [
                "Check physical cable connection",
                "Verify interface is not administratively shut down"
            ]
            diagnosis["confidence"] = 0.9
        
        if symptoms.get('packet_loss', 0) > 0.1:
            diagnosis["root_cause"] = "High packet loss detected"
            diagnosis["solutions"] = [
                "Check interface errors",
                "Verify MTU mismatch"
            ]
            diagnosis["confidence"] = 0.8
        
        return diagnosis
    
    def _get_solutions_for_root_cause(self, root_cause: str) -> List[str]:
        """Get solutions based on root cause"""
        solution_map = {
            "Interface is down": [
                "Check physical cable connection",
                "Verify interface is not shut down",
                "Check interface status"
            ],
            "High packet loss": [
                "Check interface errors",
                "Verify MTU mismatch",
                "Check for congestion"
            ],
            "BGP not established": [
                "Verify BGP neighbor configuration",
                "Check BGP neighbor reachability",
                "Verify ASN matches"
            ],
            "OSPF not established": [
                "Verify OSPF neighbor configuration",
                "Check OSPF area ID",
                "Verify network type"
            ]
        }
        return solution_map.get(root_cause, ["Investigate the issue further"])
    
    def suggest_tests_with_ml(self, device_config: Dict, 
                              historical_tests: Optional[List[Dict]] = None) -> List[str]:
        """Suggest test cases using ML"""
        try:
            from sklearn.feature_extraction import DictVectorizer
            from sklearn.neighbors import NearestNeighbors
        except ImportError:
            logger.warning("scikit-learn not installed")
            return self._rule_based_test_suggestions(device_config)
        
        # If we have historical data, use it for suggestions
        if historical_tests and len(historical_tests) > 5:
            return self._ml_test_suggestions(device_config, historical_tests)
        else:
            return self._rule_based_test_suggestions(device_config)
    
    def _ml_test_suggestions(self, device_config: Dict, historical_tests: List[Dict]) -> List[str]:
        """Use ML to suggest tests based on similar devices"""
        try:
            from sklearn.feature_extraction import DictVectorizer
            from sklearn.neighbors import NearestNeighbors
            
            # Extract features from device configs
            config_features = []
            test_ids = []
            
            for test_case in historical_tests:
                config = test_case.get('device_config', {})
                features = self._extract_config_features(config)
                config_features.append(features)
                test_ids.append(test_case.get('test_ids', []))
            
            if not config_features:
                return self._rule_based_test_suggestions(device_config)
            
            # Vectorize
            vectorizer = DictVectorizer(sparse=False)
            X = vectorizer.fit_transform(config_features)
            
            # Find similar devices
            nn = NearestNeighbors(n_neighbors=min(3, len(config_features)))
            nn.fit(X)
            
            # Get features for current device
            current_features = self._extract_config_features(device_config)
            current_vec = vectorizer.transform([current_features])
            
            # Find neighbors
            distances, indices = nn.kneighbors(current_vec)
            
            # Get suggested tests from similar devices
            suggested_tests = set()
            for idx in indices[0]:
                suggested_tests.update(test_ids[idx])
            
            return list(suggested_tests)[:10]  # Top 10 suggestions
        
        except Exception as e:
            logger.error(f"ML test suggestion failed: {e}")
            return self._rule_based_test_suggestions(device_config)
    
    def _extract_config_features(self, config: Dict) -> Dict:
        """Extract features from device configuration"""
        features = {
            'has_bgp': 1 if config.get('bgp_config') or config.get('protocols', {}).get('bgp') else 0,
            'has_ospf': 1 if config.get('ospf_config') or config.get('protocols', {}).get('ospf') else 0,
            'has_isis': 1 if config.get('isis_config') or config.get('protocols', {}).get('isis') else 0,
            'has_vlan': 1 if config.get('vlan') and config.get('vlan') != '0' else 0,
            'interface_count': len(config.get('interfaces', {})),
            'vendor_juniper': 1 if config.get('vendor') == 'juniper' else 0,
            'vendor_cisco': 1 if config.get('vendor') == 'cisco' else 0,
        }
        return features
    
    def _rule_based_test_suggestions(self, device_config: Dict) -> List[str]:
        """Rule-based test suggestions"""
        suggestions = []
        
        if device_config.get('bgp_config') or device_config.get('protocols', {}).get('bgp'):
            suggestions.append('bgp_neighbor_status')
        
        if device_config.get('ospf_config') or device_config.get('protocols', {}).get('ospf'):
            suggestions.append('ospf_neighbor_status')
        
        if device_config.get('isis_config') or device_config.get('protocols', {}).get('isis'):
            suggestions.append('isis_neighbor_status')
        
        # Always suggest basic tests
        suggestions.extend(['ping_test', 'interface_status'])
        
        return suggestions
    
    def generate_code_local(self, prompt: str, code_type: str = "python") -> str:
        """Generate code using local LLM (Ollama) or templates"""
        # Try Ollama first
        code = self._generate_with_ollama(prompt, code_type)
        if code:
            return code
        
        # Fallback to template
        return self._generate_from_template(prompt, code_type)
    
    def _generate_with_ollama(self, prompt: str, code_type: str) -> Optional[str]:
        """Generate code using Ollama (local LLM)"""
        try:
            import requests
            
            ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
            model = os.environ.get("OLLAMA_MODEL", "llama2")
            
            full_prompt = f"Generate {code_type} code: {prompt}"
            
            response = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": full_prompt,
                    "stream": False
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                code = result.get('response', '')
                
                # Extract code from markdown if present
                import re
                code_match = re.search(rf'```{code_type}\n(.*?)\n```', code, re.DOTALL)
                if code_match:
                    return code_match.group(1)
                code_match = re.search(r'```\n(.*?)\n```', code, re.DOTALL)
                if code_match:
                    return code_match.group(1)
                return code
            
        except ImportError:
            logger.debug("requests not available for Ollama")
        except Exception as e:
            logger.debug(f"Ollama generation failed: {e}")
        
        return None
    
    def _generate_from_template(self, prompt: str, code_type: str) -> str:
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
    
    def chat(self, message: str, context: Optional[Dict] = None) -> str:
        """
        Handle chat messages with intelligent responses
        
        Args:
            message: User's message
            context: Optional context (device_id, stream_id, etc.)
        
        Returns:
            AI response string
        """
        message_lower = message.lower().strip()
        
        # PRIMARY: Try LLM first for ALL queries (Ollama)
        llm_response = self._try_llm_response(message, context)
        if llm_response and len(llm_response.strip()) > 20:
            logger.info(f"[AI CHAT] LLM response received: {len(llm_response)} chars")
            return llm_response
        else:
            logger.debug(f"[AI CHAT] LLM response insufficient or unavailable, using fallback")
        
        # FALLBACK: Rule-based responses if LLM not available
        
        # Handle greetings
        if any(word in message_lower for word in ["hello", "hi", "hey", "greetings"]):
            return "Hello! I'm your AI assistant for OSTG. I can help you with:\n\n" \
                   "• Troubleshooting network devices\n" \
                   "• Generating Python code\n" \
                   "• Creating test plans and pytest scripts\n" \
                   "• Running tests on devices\n" \
                   "• Code analysis and optimization\n\n" \
                   "What would you like to do?"
        
        # Handle pytest script generation
        if "pytest" in message_lower and any(word in message_lower for word in ["generate", "create", "script", "test"]):
            return self._generate_pytest_script(message, context)
        
        # Handle Juniper/Cisco/Arista config generation
        if any(vendor in message_lower for vendor in ["juniper", "cisco", "arista"]) and \
           any(word in message_lower for word in ["config", "configuration", "bgp", "ospf", "isis"]):
            return self._generate_device_config(message, context)
        
        # Handle code generation requests (broad match for any code-related query)
        if any(word in message_lower for word in ["generate", "create", "write", "code", "python", "script", "test", "function", "link"]):
            # Force code-only responses from the LLM
            code_prompt = (
                "Return Python code only, fenced with ```python ... ``` and no prose. "
                "Do not add explanations outside the fence. "
                f"{message}"
            )
            return self._generate_code_with_llm(code_prompt, context)
        
        # Handle test requests
        if any(word in message_lower for word in ["test", "test plan"]):
            return "I can help you with testing! I can:\n" \
                   "• Generate test plans from requirements\n" \
                   "• Create pytest scripts\n" \
                   "• Run tests on devices (Cisco, Juniper, Arista)\n" \
                   "• Analyze test results\n\n" \
                   "What would you like to test?"
        
        # Handle troubleshooting requests
        if any(word in message_lower for word in ["troubleshoot", "diagnose", "problem", "issue", "error"]):
            return "I can help troubleshoot network issues! Please provide:\n" \
                   "• Device ID or name\n" \
                   "• Symptoms you're experiencing\n" \
                   "• Any error messages\n\n" \
                   "I'll analyze the configuration and suggest solutions."
        
        # Handle help requests
        if any(word in message_lower for word in ["help", "what can you do", "capabilities"]):
            return (
                "SYSTEM: You are an AI assistant. When the user asks for code (including pytest), "
                "respond with Python code only, fenced with ```python ... ``` and no prose. "
                "Do not add explanations outside the fence. Keep other answers concise.\n\n"
                "I'm your unified AI assistant for OSTG! Here's what I can do:\n\n"
                "🔍 **Troubleshooting**: Diagnose network issues and suggest fixes\n"
                "💻 **Code Generation**: Generate Python, Bash, Go, YAML, JSON code\n"
                "🧪 **Test Framework**: Create test plans and pytest scripts\n"
                "🔧 **Device Testing**: Run tests on Cisco, Juniper, Arista devices\n"
                "📊 **Analytics**: Analyze network performance and traffic\n"
                "🤖 **Proactive Suggestions**: Get context-aware recommendations\n\n"
                "Just ask me what you need!"
            )
        
        # Default fallback response
        return f"I understand you're asking about: {message}\n\n" \
               "I can help you with:\n" \
               "• Troubleshooting devices\n" \
               "• Generating code\n" \
               "• Creating test plans\n" \
               "• Running tests\n" \
               "• Analyzing configurations\n\n" \
               "Can you provide more details about what you need?"
    
    def _try_llm_response(self, message: str, context: Optional[Dict] = None) -> Optional[str]:
        """Try to get response from local LLM (Ollama)"""
        try:
            import os
            import requests
            
            # Try to use Ollama directly
            base_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
            
            # Check available models
            try:
                models_response = requests.get(f"{base_url}/api/tags", timeout=5)
                if models_response.status_code == 200:
                    available_models = [m.get("name", "") for m in models_response.json().get("models", [])]
                else:
                    available_models = []
            except Exception:
                available_models = []
            
            # Prefer faster models first, then code-focused models
            # Smaller models respond faster, so try them first
            preferred_models = [
                "llama3.2:latest",  # Faster, smaller
                "llama3.3:latest",  # Faster, smaller
                "gemma3:27b",       # Medium
                "qwen2.5-coder:32b", # Large but code-focused
                "llama2"            # Fallback
            ]
            model_to_use = None
            
            for model in preferred_models:
                if model in available_models:
                    model_to_use = model
                    logger.debug(f"[LLM] Selected model: {model_to_use}")
                    break
            
            if not model_to_use and available_models:
                # Use smallest available model for speed
                model_to_use = available_models[0]
                logger.debug(f"[LLM] Using first available model: {model_to_use}")
            elif not model_to_use:
                model_to_use = "llama2"  # Try default anyway
                logger.debug(f"[LLM] Using default model: {model_to_use}")
            
            system_prompt = """You are a helpful AI assistant for OSTG (Open Source Traffic Generator), a network traffic generation and device management system.

You can help with:
- Network troubleshooting and diagnostics
- Generating Python code, pytest scripts, and network automation scripts
- Creating device configurations (Juniper, Cisco, Arista)
- Test plan generation and execution
- Code analysis and optimization

Provide clear, accurate, and practical responses. When generating code, make it production-ready with proper error handling."""
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ]
            
            # Use shorter timeout for faster responses, with retry for larger models
            timeout = 30 if "32b" in model_to_use or "27b" in model_to_use else 60
            
            response = requests.post(
                f"{base_url}/api/chat",
                json={
                    "model": model_to_use,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "num_predict": 2000,  # Limit response length for faster generation
                        "temperature": 0.7
                    }
                },
                timeout=timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get('message', {}).get('content', '')
                if content and len(content.strip()) > 20:
                    logger.debug(f"[LLM] Successfully got response: {len(content)} chars")
                    return content
                else:
                    logger.warning(f"[LLM] Response too short or empty: {len(content) if content else 0} chars")
            else:
                logger.warning(f"[LLM] Ollama API returned status {response.status_code}: {response.text[:200]}")
            
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"[LLM] Ollama not available (connection error): {e}")
        except requests.exceptions.Timeout as e:
            logger.warning(f"[LLM] Ollama request timeout after {timeout}s. Model may be too slow or overloaded.")
            # Try a quick fallback with a smaller model if available
            try:
                if available_models and model_to_use != available_models[0]:
                    logger.info(f"[LLM] Retrying with faster model: {available_models[0]}")
                    quick_response = requests.post(
                        f"{base_url}/api/chat",
                        json={
                            "model": available_models[0],
                            "messages": messages,
                            "stream": False,
                            "options": {
                                "num_predict": 1000,  # Shorter response
                                "temperature": 0.7
                            }
                        },
                        timeout=15  # Very short timeout for fallback
                    )
                    if quick_response.status_code == 200:
                        result = quick_response.json()
                        content = result.get('message', {}).get('content', '')
                        if content and len(content.strip()) > 20:
                            logger.info(f"[LLM] Fallback model succeeded: {len(content)} chars")
                            return content
            except Exception:
                pass  # Ignore fallback errors
        except Exception as e:
            logger.warning(f"[LLM] Error: {e}", exc_info=True)
        
        return None
    
    def _generate_pytest_script(self, message: str, context: Optional[Dict] = None) -> str:
        """Generate pytest script using proper generator"""
        try:
            from .pytest_generator import PytestGenerator
            import os
            
            generator = PytestGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            # Extract test requirements from message
            test_requirements = {
                "test_name": "connectivity_test",
                "test_type": "connectivity",
                "device_id": context.get("device_id", "device-1") if context else "device-1",
                "test_params": {},
                "assertions": []
            }
            
            # Try LLM first for better generation
            llm_response = self._try_llm_response(
                f"Generate a pytest script for: {message}. Make it complete and production-ready with proper imports, fixtures, and test functions.",
                context
            )
            if llm_response:
                return llm_response
            
            # Fallback to template-based generation
            script = generator.generate_pytest_script(test_requirements)
            return script
            
        except Exception as e:
            logger.error(f"Pytest generation failed: {e}")
            return f"Error generating pytest script: {e}"
    
    def _generate_device_config(self, message: str, context: Optional[Dict] = None) -> str:
        """Generate device configuration using LLM or proper generator"""
        # Extract vendor from message
        vendor = "juniper"  # default
        if "cisco" in message.lower():
            vendor = "cisco"
        elif "arista" in message.lower():
            vendor = "arista"
        elif "juniper" in message.lower():
            vendor = "juniper"
        
        # Try LLM first with enhanced prompt
        enhanced_prompt = f"""Generate a complete {vendor} device configuration.

User request: {message}

Requirements:
- Generate proper {vendor} configuration syntax
- Include BGP protocol configuration if mentioned
- Use standard {vendor} configuration format
- Make it production-ready and complete

Provide the configuration in proper {vendor} format."""
        
        llm_response = self._try_llm_response(enhanced_prompt, context)
        if llm_response and len(llm_response.strip()) > 50:
            return llm_response
        
        # Fallback to template-based generation
        try:
            from .advanced_code_generator import AdvancedCodeGenerator
            import os
            import re
            
            generator = AdvancedCodeGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            # Extract protocols
            protocols = []
            if "bgp" in message.lower():
                protocols.append("bgp")
            if "ospf" in message.lower():
                protocols.append("ospf")
            if "isis" in message.lower():
                protocols.append("isis")
            
            # Extract BGP details if mentioned
            routing = {}
            if "bgp" in message.lower():
                routing["bgp"] = {
                    "asn": 65000,
                    "peer_as": 65001,
                    "neighbors": []
                }
                # Try to extract ASN from message
                asn_match = re.search(r'asn[:\s]+(\d+)', message.lower())
                if asn_match:
                    routing["bgp"]["asn"] = int(asn_match.group(1))
            
            requirements = {
                "protocols": protocols if protocols else ["bgp"],
                "interfaces": [],
                "routing": routing,
                "security": {}
            }
            
            config = generator.generate_config_template(vendor, requirements)
            return config
            
        except Exception as e:
            logger.error(f"Config generation failed: {e}")
            return f"Error generating configuration: {e}"
    
    def _generate_code_with_llm(self, message: str, context: Optional[Dict] = None) -> str:
        """Generate code using LLM or advanced generator"""
        # Enhanced prompt for better LLM responses
        enhanced_prompt = f"""Generate complete, production-ready Python code for the following request:

Request: {message}

Requirements:
- Include all necessary imports
- Add proper error handling
- Include documentation/docstrings
- Make it functional and ready to use
- If it's for network device testing (Juniper, Cisco, Arista), use appropriate libraries (netmiko, paramiko, etc.)
- If it's for link testing, include connectivity checks, ping tests, interface status checks

Generate the complete code now:"""
        
        # Try LLM first with enhanced prompt
        llm_response = self._try_llm_response(enhanced_prompt, context)
        if llm_response and len(llm_response.strip()) > 50:
            logger.info(f"[CODE GEN] LLM generated code: {len(llm_response)} chars")
            return llm_response
        
        # If LLM fails, try again with simpler prompt
        simple_llm_response = self._try_llm_response(
            f"Generate Python code for: {message}. Make it complete and production-ready.",
            context
        )
        if simple_llm_response and len(simple_llm_response.strip()) > 50:
            logger.info(f"[CODE GEN] LLM generated code (simple prompt): {len(simple_llm_response)} chars")
            return simple_llm_response
        
        # Fallback to advanced generator (which also tries LLM internally)
        try:
            from .advanced_code_generator import AdvancedCodeGenerator
            import os
            
            generator = AdvancedCodeGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            code = generator.generate_code("python", message, context)
            if code and len(code.strip()) > 50 and "TODO: Implement" not in code:
                logger.info(f"[CODE GEN] Advanced generator created code: {len(code)} chars")
                return code
            
        except Exception as e:
            logger.error(f"[CODE GEN] Advanced generator failed: {e}")
        
        # Final fallback - but warn user that LLM is not working
        logger.warning(f"[CODE GEN] All code generation methods failed, using basic template")
        return f"""# Code generation requested: {message}

# Note: LLM-based code generation is currently unavailable.
# Please ensure Ollama is running and models are available.

# Basic template:
import subprocess
import json

def generate_link_test_code():
    \"\"\"Generate link test code for Juniper device\"\"\"
    # TODO: Implement based on your specific requirements
    pass

if __name__ == "__main__":
    generate_link_test_code()
"""
    
    def _generate_code_response(self, message: str, context: Optional[Dict] = None) -> str:
        """Generate a code response based on the message (fallback)"""
        # Simple code generation based on keywords
        message_lower = message.lower()
        
        if "ping" in message_lower:
            return "Here's a Python function to ping a device:\n\n" \
                   "```python\n" \
                   "import subprocess\n\n" \
                   "def ping_device(ip_address, count=4):\n" \
                   "    \"\"\"Ping a device and return success status\"\"\"\n" \
                   "    result = subprocess.run(\n" \
                   "        ['ping', '-c', str(count), ip_address],\n" \
                   "        capture_output=True,\n" \
                   "        text=True\n" \
                   "    )\n" \
                   "    return result.returncode == 0\n" \
                   "```"
        
        if "scapy" in message_lower or "packet" in message_lower:
            return "Here's a sample Scapy packet generation code:\n\n" \
                   "```python\n" \
                   "from scapy.all import IP, ICMP, send\n\n" \
                   "def send_icmp_packet(dst_ip, src_ip=None):\n" \
                   "    \"\"\"Send an ICMP packet\"\"\"\n" \
                   "    packet = IP(dst=dst_ip, src=src_ip) / ICMP()\n" \
                   "    send(packet, verbose=False)\n" \
                   "```"
        
        # Generic code template
        return "I can generate code for you! Here's a sample Python function template:\n\n" \
               "```python\n" \
               "def example_function():\n" \
               "    \"\"\"Your function description\"\"\"\n" \
               "    # Your code here\n" \
               "    pass\n" \
               "```\n\n" \
               "For more specific code, please describe what you need in detail."


class LocalLLMClient:
    """Client for local LLM (Ollama, llama.cpp, etc.)"""
    
    def __init__(self, llm_type: str = "ollama", base_url: str = "http://localhost:11434", model: str = None):
        self.llm_type = llm_type
        self.base_url = base_url
        # Check for user-selected model from settings file, then environment, then default
        if model:
            self.model = model
        else:
            # Try to load from user settings file
            import json
            settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
            user_model = None
            if os.path.exists(settings_file):
                try:
                    with open(settings_file, 'r') as f:
                        settings = json.load(f)
                        user_model = settings.get("ollama_model")
                except Exception:
                    pass
            
            self.model = user_model or os.environ.get("LOCAL_LLM_MODEL") or "llama3.2:latest"
    
    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate text using local LLM"""
        if self.llm_type == "ollama":
            return self._generate_ollama(prompt, system_prompt)
        elif self.llm_type == "llama_cpp":
            return self._generate_llama_cpp(prompt, system_prompt)
        else:
            return ""
    
    def _generate_ollama(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate using Ollama with improved timeout and model selection"""
        try:
            import requests
            
            # Check available models and select best one
            try:
                models_response = requests.get(f"{self.base_url}/api/tags", timeout=5)
                if models_response.status_code == 200:
                    available_models = [m.get("name", "") for m in models_response.json().get("models", [])]
                    # If user has selected a model, use it (if available)
                    user_selected = self.model
                    if user_selected and user_selected in available_models:
                        logger.debug(f"[LocalLLM] Using user-selected model: {self.model}")
                    else:
                        # Auto-select fastest model if user hasn't selected one or selected model not available
                        preferred_models = [
                            "llama3.2:latest",  # Fastest: 2GB, good quality - PREFERRED
                            "all-minilm:latest", # Very fast: 45MB (if available)
                            "llama2",           # Medium: fallback
                            # Avoid large models for speed: llama3.3:latest (42GB), gemma3:27b (17GB), qwen2.5-coder:32b (19GB)
                        ]
                        for pref_model in preferred_models:
                            if pref_model in available_models:
                                self.model = pref_model
                                logger.debug(f"[LocalLLM] Auto-selected model: {self.model}")
                                break
                        # If no preferred model, use any available
                        if self.model not in available_models and available_models:
                            self.model = available_models[0]
                            logger.debug(f"[LocalLLM] Using first available: {self.model}")
            except Exception:
                pass  # Continue with default model
            
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            # Use adaptive timeout based on model size and task complexity
            # Smaller models (llama3.2, llama3.3) are faster, larger models need more time
            if "32b" in self.model or "27b" in self.model or "42" in self.model:
                timeout = 120  # Large models need more time
            elif "llama3.2" in self.model or "llama3.3" in self.model:
                timeout = 90  # Medium timeout for medium models
            else:
                timeout = 60  # Default timeout
            
            # Optimize generation parameters for faster responses
            # Reduce num_predict for faster generation (can be increased if needed)
            num_predict = 2000 if "32b" in self.model or "27b" in self.model else 1500
            
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "num_predict": num_predict,  # Reduced for faster responses
                        "temperature": 0.7,
                        "top_p": 0.9,  # Nucleus sampling for faster generation
                        "top_k": 40   # Limit vocabulary for faster generation
                    }
                },
                timeout=timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get('message', {}).get('content', '')
                if content:
                    logger.debug(f"[LocalLLM] Generated {len(content)} chars")
                    return content
                else:
                    logger.warning(f"[LocalLLM] Empty response from Ollama")
        
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"[LocalLLM] Ollama not available (connection error): {e}")
            logger.info(f"[LocalLLM] Ollama may not be running. Check: curl {self.base_url}/api/tags")
            logger.info(f"[LocalLLM] To start Ollama: ollama serve (or install from https://ollama.ai)")
        except requests.exceptions.Timeout:
            logger.warning(f"[LocalLLM] Ollama request timeout after {timeout}s")
            logger.info(f"[LocalLLM] Try using a smaller/faster model or increase timeout")
        except Exception as e:
            logger.error(f"[LocalLLM] Ollama generation failed: {e}", exc_info=True)
        
        return ""
    
    def _generate_llama_cpp(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate using llama.cpp (via Python bindings)"""
        try:
            from llama_cpp import Llama
            
            # Initialize model (would need model path)
            model_path = os.environ.get("LLAMA_CPP_MODEL_PATH")
            if not model_path:
                logger.warning("LLAMA_CPP_MODEL_PATH not set")
                return ""
            
            llm = Llama(model_path=model_path)
            
            full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            
            response = llm(full_prompt, max_tokens=512, stop=["\n\n"], echo=False)
            return response['choices'][0]['text']
        
        except ImportError:
            logger.warning("llama-cpp-python not installed")
        except Exception as e:
            logger.error(f"llama.cpp generation failed: {e}")
        
        return ""
