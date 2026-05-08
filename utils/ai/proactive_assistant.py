"""
Proactive AI Assistant
Proactive suggestions, context awareness, and personalized experience
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import json

logger = logging.getLogger(__name__)


class ProactiveAIAssistant:
    """Proactive AI assistant with suggestions and personalization"""
    
    def __init__(self, use_ai_api: bool = False, api_key: Optional[str] = None):
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        self.user_preferences = {}  # Store user preferences
        self.context_history = []  # Store context history
        
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
    
    def suggest_actions(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Suggest actions based on context
        
        Args:
            context: Current context
                - device_id: Selected device
                - stream_id: Selected stream
                - current_view: Current UI view
                - recent_actions: Recent user actions
                - system_state: System state
        
        Returns:
            List of suggested actions with:
            - action: Action name
            - description: Action description
            - priority: Priority level (high, medium, low)
            - confidence: Confidence score (0-1)
        """
        suggestions = []
        
        # Device-related suggestions
        if context.get("device_id"):
            device_suggestions = self._suggest_device_actions(context)
            suggestions.extend(device_suggestions)
        
        # Stream-related suggestions
        if context.get("stream_id"):
            stream_suggestions = self._suggest_stream_actions(context)
            suggestions.extend(stream_suggestions)
        
        # System health suggestions
        system_suggestions = self._suggest_system_actions(context)
        suggestions.extend(system_suggestions)
        
        # Performance optimization suggestions
        perf_suggestions = self._suggest_performance_actions(context)
        suggestions.extend(perf_suggestions)
        
        # Sort by priority and confidence
        suggestions.sort(key=lambda x: (
            {"high": 3, "medium": 2, "low": 1}.get(x.get("priority", "low"), 0),
            x.get("confidence", 0)
        ), reverse=True)
        
        return suggestions[:10]  # Return top 10 suggestions
    
    def _suggest_device_actions(self, context: Dict) -> List[Dict]:
        """Suggest device-related actions"""
        suggestions = []
        device_id = context.get("device_id")
        
        # Check device health
        try:
            from utils.ai.intelligent_device_manager import IntelligentDeviceManager
            import os
            
            manager = IntelligentDeviceManager(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            health = manager.monitor_health(device_id)
            
            if not health.get("healthy"):
                suggestions.append({
                    "action": "troubleshoot_device",
                    "description": f"Device {device_id} health check failed",
                    "priority": "high",
                    "confidence": 0.9,
                    "action_type": "troubleshoot"
                })
        except Exception as e:
            logger.debug(f"Could not check device health: {e}")
        
        # Suggest configuration optimization
        suggestions.append({
            "action": "optimize_device_config",
            "description": "Optimize device configuration for better performance",
            "priority": "medium",
            "confidence": 0.7,
            "action_type": "optimize"
        })
        
        # Suggest testing
        suggestions.append({
            "action": "run_device_tests",
            "description": "Run comprehensive tests on device",
            "priority": "medium",
            "confidence": 0.6,
            "action_type": "test"
        })
        
        return suggestions
    
    def _suggest_stream_actions(self, context: Dict) -> List[Dict]:
        """Suggest stream-related actions"""
        suggestions = []
        stream_id = context.get("stream_id")
        
        # Suggest stream optimization
        suggestions.append({
            "action": "optimize_stream",
            "description": "Optimize stream configuration for better performance",
            "priority": "medium",
            "confidence": 0.7,
            "action_type": "optimize"
        })
        
        # Suggest rate adjustment
        suggestions.append({
            "action": "adjust_stream_rate",
            "description": "Adjust stream rate based on network capacity",
            "priority": "low",
            "confidence": 0.5,
            "action_type": "configure"
        })
        
        return suggestions
    
    def _suggest_system_actions(self, context: Dict) -> List[Dict]:
        """Suggest system-related actions"""
        suggestions = []
        
        # Suggest system health check
        suggestions.append({
            "action": "system_health_check",
            "description": "Run system health check",
            "priority": "medium",
            "confidence": 0.6,
            "action_type": "monitor"
        })
        
        # Suggest backup
        suggestions.append({
            "action": "backup_configuration",
            "description": "Backup current configuration",
            "priority": "low",
            "confidence": 0.5,
            "action_type": "backup"
        })
        
        return suggestions
    
    def _suggest_performance_actions(self, context: Dict) -> List[Dict]:
        """Suggest performance optimization actions"""
        suggestions = []
        
        # Suggest performance analysis
        suggestions.append({
            "action": "analyze_performance",
            "description": "Analyze system performance and identify bottlenecks",
            "priority": "medium",
            "confidence": 0.6,
            "action_type": "analyze"
        })
        
        return suggestions
    
    def learn_preferences(self, user_id: str, actions: List[Dict[str, Any]]):
        """
        Learn user preferences from actions
        
        Args:
            user_id: User identifier
            actions: List of actions taken by user
        """
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {
                "frequent_actions": {},
                "preferred_features": [],
                "workflow_patterns": []
            }
        
        # Track frequent actions
        for action in actions:
            action_type = action.get("type", "unknown")
            if action_type not in self.user_preferences[user_id]["frequent_actions"]:
                self.user_preferences[user_id]["frequent_actions"][action_type] = 0
            self.user_preferences[user_id]["frequent_actions"][action_type] += 1
    
    def personalize_experience(self, user_id: str) -> Dict[str, Any]:
        """
        Personalize AI experience for user
        
        Args:
            user_id: User identifier
        
        Returns:
            Personalization settings
        """
        preferences = self.user_preferences.get(user_id, {})
        
        # Get most frequent actions
        frequent_actions = preferences.get("frequent_actions", {})
        top_actions = sorted(frequent_actions.items(), key=lambda x: x[1], reverse=True)[:5]
        
        return {
            "user_id": user_id,
            "preferred_actions": [action[0] for action in top_actions],
            "personalization_level": "high" if len(frequent_actions) > 10 else "medium" if len(frequent_actions) > 5 else "low",
            "suggestions_enabled": True,
            "auto_actions": False  # Don't auto-execute, just suggest
        }
    
    def get_contextual_help(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get contextual help based on current state
        
        Args:
            context: Current context
        
        Returns:
            Help information
        """
        help_info = {
            "suggestions": [],
            "tips": [],
            "documentation": []
        }
        
        # Device context
        if context.get("device_id"):
            help_info["suggestions"].append("Use AI Troubleshoot to diagnose device issues")
            help_info["tips"].append("Click the 🤖 button on devices for AI assistance")
        
        # Stream context
        if context.get("stream_id"):
            help_info["suggestions"].append("Use AI to optimize stream performance")
            help_info["tips"].append("AI can suggest optimal stream rates based on network capacity")
        
        # Code context
        if context.get("code_editor_open"):
            help_info["suggestions"].append("Use AI Code Generator to generate code")
            help_info["tips"].append("Press Ctrl+Shift+A to open AI Code Assistant")
        
        return help_info




