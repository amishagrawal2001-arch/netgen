"""
AI-Powered Network Switch/Router Troubleshooting System
Learns from device configurations and provides intelligent diagnostics
"""

import json
import logging
import re
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import sqlite3
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


class NetworkConfigParser:
    """Parse network device configurations (Juniper, Cisco, etc.)"""
    
    @staticmethod
    def parse_juniper_config(config_text: str) -> Dict[str, Any]:
        """Parse Juniper configuration into structured format"""
        config = {
            "vendor": "juniper",
            "interfaces": {},
            "protocols": {},
            "routing_options": {},
            "vlans": {},
            "system": {}
        }
        
        lines = config_text.split('\n')
        current_section = None
        current_interface = None
        indent_level = 0
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Detect section headers
            if line.startswith('interfaces {'):
                current_section = 'interfaces'
                continue
            elif line.startswith('protocols {'):
                current_section = 'protocols'
                continue
            elif line.startswith('routing-options {'):
                current_section = 'routing_options'
                continue
            elif line.startswith('vlans {'):
                current_section = 'vlans'
                continue
            elif line.startswith('system {'):
                current_section = 'system'
                continue
            
            # Parse interface configuration
            if current_section == 'interfaces':
                if re.match(r'^\w+-\d+/\d+/\d+', line) or line.startswith('lo0'):
                    # Interface name
                    iface_name = line.split()[0].rstrip(' {')
                    current_interface = iface_name
                    config['interfaces'][iface_name] = {
                        "name": iface_name,
                        "unit": {},
                        "description": "",
                        "mtu": None,
                        "speed": None
                    }
                elif current_interface:
                    if 'unit' in line:
                        unit_match = re.search(r'unit (\d+)', line)
                        if unit_match:
                            unit_num = unit_match.group(1)
                            config['interfaces'][current_interface]['unit'][unit_num] = {}
                    elif 'description' in line:
                        desc = line.split('description')[1].strip().strip('"')
                        config['interfaces'][current_interface]['description'] = desc
                    elif 'mtu' in line:
                        mtu_match = re.search(r'mtu (\d+)', line)
                        if mtu_match:
                            config['interfaces'][current_interface]['mtu'] = int(mtu_match.group(1))
                    elif 'family inet' in line or 'family inet6' in line:
                        # IP address configuration
                        pass  # Can be extended
            
            # Parse protocols
            elif current_section == 'protocols':
                if 'ospf' in line.lower():
                    config['protocols']['ospf'] = config['protocols'].get('ospf', {})
                elif 'isis' in line.lower():
                    config['protocols']['isis'] = config['protocols'].get('isis', {})
                elif 'bgp' in line.lower():
                    config['protocols']['bgp'] = config['protocols'].get('bgp', {})
        
        return config
    
    @staticmethod
    def parse_cisco_config(config_text: str) -> Dict[str, Any]:
        """Parse Cisco IOS/IOS-XE configuration into structured format"""
        config = {
            "vendor": "cisco",
            "interfaces": {},
            "protocols": {},
            "vlans": {}
        }
        
        lines = config_text.split('\n')
        current_interface = None
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('!'):
                continue
            
            # Interface configuration
            if line.startswith('interface '):
                iface_name = line.split()[1]
                current_interface = iface_name
                config['interfaces'][iface_name] = {
                    "name": iface_name,
                    "description": "",
                    "ip_address": None,
                    "shutdown": False
                }
            elif current_interface:
                if line.startswith('description '):
                    config['interfaces'][current_interface]['description'] = line.split('description', 1)[1].strip()
                elif line.startswith('ip address '):
                    ip_parts = line.split()[2:4]
                    config['interfaces'][current_interface]['ip_address'] = f"{ip_parts[0]}/{ip_parts[1]}"
                elif line == 'shutdown':
                    config['interfaces'][current_interface]['shutdown'] = True
            
            # Protocol configuration
            if 'router ospf' in line.lower():
                config['protocols']['ospf'] = config['protocols'].get('ospf', {})
            elif 'router bgp' in line.lower():
                config['protocols']['bgp'] = config['protocols'].get('bgp', {})
        
        return config
    
    @staticmethod
    def detect_vendor(config_text: str) -> str:
        """Auto-detect configuration vendor"""
        config_lower = config_text.lower()
        if 'juniper' in config_lower or 'set' in config_lower[:100] or 'interfaces {' in config_lower:
            return 'juniper'
        elif 'cisco' in config_lower or 'interface ' in config_lower[:100]:
            return 'cisco'
        elif 'frr' in config_lower or 'vtysh' in config_lower:
            return 'frr'
        elif 'sonic' in config_lower or 'show interfaces status' in config_lower or 'config interface' in config_lower:
            return 'sonic'
        return 'unknown'
    
    @staticmethod
    def parse_config(config_text: str, vendor: Optional[str] = None) -> Dict[str, Any]:
        """Parse configuration with auto-detection"""
        if not vendor:
            vendor = NetworkConfigParser.detect_vendor(config_text)
        
        if vendor == 'juniper':
            return NetworkConfigParser.parse_juniper_config(config_text)
        elif vendor == 'cisco':
            return NetworkConfigParser.parse_cisco_config(config_text)
        else:
            logger.warning(f"Unknown vendor: {vendor}, returning raw config")
            return {"vendor": vendor, "raw": config_text}


class ConfigKnowledgeBase:
    """Knowledge base that learns from device configurations"""
    
    def __init__(self, db_path: str = "/opt/OSTG/ai_knowledge_base.db"):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize knowledge base database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Configurations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS device_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                device_name TEXT,
                vendor TEXT,
                config_text TEXT,
                parsed_config TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Troubleshooting cases table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS troubleshooting_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                symptoms TEXT,
                root_cause TEXT,
                solution TEXT,
                config_snapshot TEXT,
                resolved BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            )
        """)
        
        # Patterns table (learned from configs)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT,
                pattern_data TEXT,
                frequency INTEGER DEFAULT 1,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    
    def add_config(self, device_id: str, device_name: str, config_text: str, vendor: Optional[str] = None):
        """Add device configuration to knowledge base"""
        parser = NetworkConfigParser()
        parsed = parser.parse_config(config_text, vendor)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if config exists
        cursor.execute("SELECT id FROM device_configs WHERE device_id = ?", (device_id,))
        existing = cursor.fetchone()
        
        if existing:
            # Update existing
            cursor.execute("""
                UPDATE device_configs 
                SET config_text = ?, parsed_config = ?, vendor = ?, updated_at = CURRENT_TIMESTAMP
                WHERE device_id = ?
            """, (config_text, json.dumps(parsed), parsed.get('vendor', 'unknown'), device_id))
        else:
            # Insert new
            cursor.execute("""
                INSERT INTO device_configs (device_id, device_name, vendor, config_text, parsed_config)
                VALUES (?, ?, ?, ?, ?)
            """, (device_id, device_name, parsed.get('vendor', 'unknown'), config_text, json.dumps(parsed)))
        
        # Extract and store patterns
        self._extract_patterns(parsed, cursor)
        
        conn.commit()
        conn.close()
        logger.info(f"Added configuration for device {device_id} to knowledge base")
    
    def _extract_patterns(self, parsed_config: Dict, cursor):
        """Extract common patterns from configuration"""
        # Extract interface patterns
        if 'interfaces' in parsed_config:
            for iface_name, iface_config in parsed_config['interfaces'].items():
                pattern_type = f"interface_{iface_name.split('-')[0] if '-' in iface_name else 'unknown'}"
                pattern_data = json.dumps({
                    "type": "interface",
                    "name_pattern": iface_name,
                    "has_mtu": iface_config.get('mtu') is not None,
                    "has_description": bool(iface_config.get('description'))
                })
                self._update_pattern(cursor, pattern_type, pattern_data)
        
        # Extract protocol patterns
        if 'protocols' in parsed_config:
            for proto_name in parsed_config['protocols'].keys():
                pattern_type = f"protocol_{proto_name}"
                pattern_data = json.dumps({"type": "protocol", "name": proto_name})
                self._update_pattern(cursor, pattern_type, pattern_data)
    
    def _update_pattern(self, cursor, pattern_type: str, pattern_data: str):
        """Update or insert pattern"""
        cursor.execute("SELECT id, frequency FROM config_patterns WHERE pattern_type = ? AND pattern_data = ?",
                      (pattern_type, pattern_data))
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute("""
                UPDATE config_patterns 
                SET frequency = frequency + 1, last_seen = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (existing[0],))
        else:
            cursor.execute("""
                INSERT INTO config_patterns (pattern_type, pattern_data, frequency)
                VALUES (?, ?, 1)
            """, (pattern_type, pattern_data))
    
    def add_troubleshooting_case(self, device_id: str, symptoms: Dict, root_cause: str, 
                                 solution: str, config_snapshot: Optional[Dict] = None):
        """Add a troubleshooting case to learn from"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO troubleshooting_cases (device_id, symptoms, root_cause, solution, config_snapshot)
            VALUES (?, ?, ?, ?, ?)
        """, (device_id, json.dumps(symptoms), root_cause, solution, 
              json.dumps(config_snapshot) if config_snapshot else None))
        
        conn.commit()
        conn.close()
        logger.info(f"Added troubleshooting case for device {device_id}")
    
    def find_similar_cases(self, symptoms: Dict, limit: int = 5) -> List[Dict]:
        """Find similar troubleshooting cases"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Simple keyword matching (can be enhanced with ML)
        symptom_text = json.dumps(symptoms).lower()
        keywords = [k for k, v in symptoms.items() if v]
        
        query = """
            SELECT symptoms, root_cause, solution, resolved
            FROM troubleshooting_cases
            WHERE resolved = 1
            ORDER BY resolved_at DESC
            LIMIT ?
        """
        
        cursor.execute(query, (limit,))
        results = []
        for row in cursor.fetchall():
            results.append({
                "symptoms": json.loads(row[0]),
                "root_cause": row[1],
                "solution": row[2],
                "resolved": bool(row[3])
            })
        
        conn.close()
        return results
    
    def get_device_config(self, device_id: str) -> Optional[Dict]:
        """Get stored configuration for a device"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT parsed_config FROM device_configs WHERE device_id = ?", (device_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return json.loads(row[0])
        return None


class NetworkTroubleshooter:
    """AI-powered network troubleshooting assistant"""
    
    def __init__(self, knowledge_base: Optional[ConfigKnowledgeBase] = None, 
                 use_ai_api: bool = False, api_key: Optional[str] = None,
                 use_local_ai: bool = True):
        self.kb = knowledge_base or ConfigKnowledgeBase()
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        self.use_local_ai = use_local_ai
        self.parser = NetworkConfigParser()
        
        # Initialize local AI engine
        if use_local_ai:
            try:
                from .local_ai_engine import LocalAIEngine
                self.local_ai = LocalAIEngine()
            except ImportError:
                logger.warning("Local AI not available. Install scikit-learn for local ML")
                self.local_ai = None
        else:
            self.local_ai = None
        
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
                logger.warning("OpenAI library not installed. Install with: pip install openai")
                self.ai_client = None
        else:
            self.ai_client = None
    
    def diagnose(self, device_id: str, symptoms: Dict[str, Any], 
                 current_config: Optional[str] = None) -> Dict[str, Any]:
        """
        Diagnose network issues based on symptoms and device configuration
        
        Args:
            device_id: Device identifier
            symptoms: Dictionary of symptoms (e.g., {"interface_down": True, "packet_loss": 0.5})
            current_config: Current device configuration (optional)
        
        Returns:
            Dictionary with diagnosis, root_cause, and solutions
        """
        # Check if device is external (not FRR container)
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device_info = device_db.get_device(device_id)
            
            if device_info:
                device_type = device_info.get("device_type", "frr_container")
                
                # If external device, use external device AI
                if device_type != "frr_container":
                    from utils.ai.external_device_ai import ExternalDeviceAI
                    from utils.external_device_manager import ExternalDeviceManager
                    
                    ext_manager = ExternalDeviceManager()
                    ext_ai = ExternalDeviceAI(external_device_manager=ext_manager)
                    
                    return ext_ai.diagnose_external_device(device_id, device_info, symptoms)
        except Exception as e:
            logger.debug(f"Failed to check device type, using default: {e}")
        
        # Default: FRR container troubleshooting
        # Get device configuration from knowledge base
        device_config = self.kb.get_device_config(device_id)
        if current_config:
            parsed_config = self.parser.parse_config(current_config)
        elif device_config:
            parsed_config = device_config
        else:
            parsed_config = None
        
        # Step 1: Rule-based quick diagnosis
        quick_diagnosis = self._rule_based_diagnosis(symptoms, parsed_config)
        if quick_diagnosis['confidence'] > 0.8:
            return quick_diagnosis
        
        # Step 2: Try local ML if available
        if self.use_local_ai and self.local_ai:
            try:
                # Get training data from knowledge base
                training_data = []
                similar_cases = self.kb.find_similar_cases(symptoms, limit=10)
                for case in similar_cases:
                    training_data.append({
                        "symptoms": case.get("symptoms", {}),
                        "root_cause": case.get("root_cause", ""),
                        "solution": case.get("solution", "")
                    })
                
                ml_diagnosis = self.local_ai.diagnose_with_ml(
                    symptoms, parsed_config, training_data if training_data else None
                )
                if ml_diagnosis.get('confidence', 0) > 0.7:
                    return ml_diagnosis
            except Exception as e:
                logger.debug(f"Local ML diagnosis failed: {e}")
        
        # Step 3: Check similar cases from knowledge base
        similar_cases = self.kb.find_similar_cases(symptoms, limit=3)
        if similar_cases:
            best_match = similar_cases[0]
            return {
                "diagnosis": "Similar case found",
                "root_cause": best_match['root_cause'],
                "solutions": [best_match['solution']],
                "confidence": 0.7,
                "source": "knowledge_base",
                "similar_cases": len(similar_cases)
            }
        
        # Step 4: Use cloud AI for complex cases (if enabled)
        if self.use_ai_api and self.ai_client:
            return self._ai_diagnosis(symptoms, parsed_config)
        
        # Step 5: Fallback to rule-based
        return quick_diagnosis
    
    def _rule_based_diagnosis(self, symptoms: Dict, config: Optional[Dict]) -> Dict:
        """Rule-based diagnosis (fast, local)"""
        # Check if SONiC-specific issue
        if isinstance(symptoms, dict):
            symptoms_text = str(symptoms).lower()
            if 'sonic' in symptoms_text or 'show interfaces status' in symptoms_text or 'empty' in symptoms_text:
                return self._sonic_diagnosis(symptoms, config)
        
        diagnosis = {
            "diagnosis": "Unknown issue",
            "root_cause": "Unable to determine",
            "solutions": [],
            "confidence": 0.0,
            "source": "rule_based"
        }
        
        # Interface down
        if symptoms.get('interface_down') or symptoms.get('link_down'):
            diagnosis["root_cause"] = "Interface is down"
            diagnosis["solutions"] = [
                "Check physical cable connection",
                "Verify interface is not administratively shut down",
                "Check interface status: 'show interfaces terse' (Juniper) or 'show ip interface brief' (Cisco)"
            ]
            diagnosis["confidence"] = 0.9
            
            # Check config for shutdown
            if config and 'interfaces' in config:
                for iface_name, iface_config in config['interfaces'].items():
                    if iface_config.get('shutdown'):
                        diagnosis["root_cause"] = f"Interface {iface_name} is administratively shut down"
                        diagnosis["solutions"] = [
                            f"Enable interface: 'no shutdown' (Cisco) or 'delete interfaces {iface_name} disable' (Juniper)"
                        ]
                        diagnosis["confidence"] = 1.0
                        break
        
        # Packet loss
        if symptoms.get('packet_loss', 0) > 0.1:
            diagnosis["root_cause"] = "High packet loss detected"
            diagnosis["solutions"] = [
                "Check interface errors: 'show interfaces detail'",
                "Verify MTU mismatch between interfaces",
                "Check for congestion or buffer drops",
                "Verify QoS/policing configuration"
            ]
            diagnosis["confidence"] = 0.8
            
            # Check MTU in config
            if config and 'interfaces' in config:
                mtu_values = []
                for iface_config in config['interfaces'].values():
                    if iface_config.get('mtu'):
                        mtu_values.append(iface_config['mtu'])
                if len(set(mtu_values)) > 1:
                    diagnosis["root_cause"] = "MTU mismatch detected between interfaces"
                    diagnosis["solutions"] = [
                        f"Standardize MTU values. Found MTUs: {set(mtu_values)}",
                        "Ensure all interfaces in path have same MTU"
                    ]
                    diagnosis["confidence"] = 0.95
        
        # Protocol not established
        if symptoms.get('bgp_not_established') or symptoms.get('ospf_not_established'):
            proto = 'BGP' if symptoms.get('bgp_not_established') else 'OSPF'
            diagnosis["root_cause"] = f"{proto} neighbor relationship not established"
            diagnosis["solutions"] = [
                f"Check {proto} neighbor configuration",
                f"Verify {proto} neighbor IP reachability",
                f"Check {proto} authentication (if configured)",
                f"Verify ASN/Area ID matches on both sides",
                f"Check firewall rules blocking {proto} packets"
            ]
            diagnosis["confidence"] = 0.85
        
        # High latency
        if symptoms.get('latency', 0) > 100:  # ms
            diagnosis["root_cause"] = "High latency detected"
            diagnosis["solutions"] = [
                "Check interface utilization",
                "Verify routing path (traceroute)",
                "Check for QoS/policing causing delays",
                "Verify CPU utilization on device"
            ]
            diagnosis["confidence"] = 0.7
        
        return diagnosis
    
    def _sonic_diagnosis(self, symptoms: Dict, config: Optional[Dict]) -> Dict:
        """SONiC-specific diagnosis for empty interfaces"""
        try:
            from .sonic_troubleshooter import SONiCTroubleshooter
            
            sonic_troubleshooter = SONiCTroubleshooter()
            
            # Extract device output if available
            device_output = {
                'show_interfaces_status': str(symptoms.get('show_interfaces_status', '')),
                'ip_link_show': str(symptoms.get('ip_link_show', '')),
                'show_platform_summary': str(symptoms.get('show_platform_summary', '')),
                'swss_status': str(symptoms.get('swss_status', '')),
                'redis_keys': str(symptoms.get('redis_keys', ''))
            }
            
            findings = sonic_troubleshooter.diagnose_empty_interfaces(device_output)
            
            # Build diagnosis from findings
            critical_issues = [f for f in findings if f['severity'] == 'critical']
            warnings = [f for f in findings if f['severity'] == 'warning']
            
            if critical_issues:
                root_cause = critical_issues[0]['finding']
                solutions = [f['recommendation'] for f in critical_issues]
                confidence = 0.9
            elif warnings:
                root_cause = warnings[0]['finding']
                solutions = [f['recommendation'] for f in warnings]
                confidence = 0.7
            else:
                root_cause = "Interfaces may need configuration"
                solutions = [
                    "Run: config interface startup Ethernet0",
                    "Check: show interfaces",
                    "Verify: sudo systemctl status swss"
                ]
                confidence = 0.5
            
            # Add diagnostic commands
            diagnostic_commands = sonic_troubleshooter.generate_troubleshooting_commands()
            
            return {
                "diagnosis": "SONiC interface issue detected",
                "root_cause": root_cause,
                "solutions": solutions,
                "confidence": confidence,
                "source": "sonic_troubleshooter",
                "findings": findings,
                "diagnostic_commands": diagnostic_commands,
                "fix_commands": sonic_troubleshooter.generate_fix_commands("service_down")
            }
        except ImportError:
            # Fallback if SONiC troubleshooter not available
            return {
                "diagnosis": "SONiC interface issue",
                "root_cause": "No interfaces showing in show interfaces status",
                "solutions": [
                    "Check: sudo systemctl status swss",
                    "Run: sudo systemctl restart swss",
                    "Verify: ip link show",
                    "Check: redis-cli KEYS PORT*"
                ],
                "confidence": 0.6,
                "source": "rule_based"
            }
    
    def _ai_diagnosis(self, symptoms: Dict, config: Optional[Dict]) -> Dict:
        """Use AI API for complex diagnosis"""
        if not self.ai_client:
            return self._rule_based_diagnosis(symptoms, config)
        
        # Build prompt with context
        prompt = f"""
        You are a network troubleshooting expert. Analyze this network issue:
        
        Symptoms:
        {json.dumps(symptoms, indent=2)}
        
        Device Configuration (if available):
        {json.dumps(config, indent=2) if config else "Not available"}
        
        Provide:
        1. Root cause analysis
        2. Step-by-step solutions
        3. Configuration commands to fix the issue
        
        Format your response as JSON:
        {{
            "root_cause": "...",
            "solutions": ["...", "..."],
            "commands": {{
                "juniper": ["command1", "command2"],
                "cisco": ["command1", "command2"]
            }}
        }}
        """
        
        try:
            response = self.ai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a network troubleshooting expert specializing in Juniper and Cisco devices."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            
            result_text = response.choices[0].message.content
            # Try to parse JSON from response
            try:
                result = json.loads(result_text)
            except Exception:
                # Extract JSON from markdown code blocks if present
                json_match = re.search(r'```json\n(.*?)\n```', result_text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(1))
                else:
                    result = {
                        "root_cause": result_text,
                        "solutions": [result_text],
                        "commands": {}
                    }
            
            return {
                "diagnosis": "AI-powered analysis",
                "root_cause": result.get("root_cause", "Unknown"),
                "solutions": result.get("solutions", []),
                "commands": result.get("commands", {}),
                "confidence": 0.8,
                "source": "ai_api"
            }
        except Exception as e:
            logger.error(f"AI diagnosis failed: {e}")
            return self._rule_based_diagnosis(symptoms, config)
    
    def suggest_config_fix(self, device_id: str, issue: str, current_config: Dict) -> Dict:
        """Suggest configuration changes to fix an issue"""
        device_config = self.kb.get_device_config(device_id)
        vendor = device_config.get('vendor', 'unknown') if device_config else 'unknown'
        
        if self.use_ai_api and self.ai_client:
            return self._ai_suggest_config_fix(issue, current_config, vendor)
        else:
            return self._rule_based_config_fix(issue, current_config, vendor)
    
    def _rule_based_config_fix(self, issue: str, config: Dict, vendor: str) -> Dict:
        """Rule-based configuration fix suggestions"""
        fixes = {
            "changes": [],
            "commands": []
        }
        
        issue_lower = issue.lower()
        
        if 'mtu' in issue_lower:
            # Standardize MTU
            if 'interfaces' in config:
                mtu_values = {}
                for iface_name, iface_config in config['interfaces'].items():
                    mtu = iface_config.get('mtu', 1500)
                    mtu_values[iface_name] = mtu
                
                # Suggest standardizing to most common or 1500
                most_common_mtu = max(set(mtu_values.values()), key=list(mtu_values.values()).count)
                fixes["changes"].append(f"Standardize all interfaces to MTU {most_common_mtu}")
                
                if vendor == 'juniper':
                    for iface_name in mtu_values.keys():
                        fixes["commands"].append(f"set interfaces {iface_name} mtu {most_common_mtu}")
                elif vendor == 'cisco':
                    for iface_name in mtu_values.keys():
                        fixes["commands"].append(f"interface {iface_name}")
                        fixes["commands"].append(f"mtu {most_common_mtu}")
        
        return fixes
    
    def _ai_suggest_config_fix(self, issue: str, config: Dict, vendor: str) -> Dict:
        """Use AI to suggest configuration fixes"""
        if not self.ai_client:
            return self._rule_based_config_fix(issue, config, vendor)
        
        prompt = f"""
        Suggest configuration changes to fix this issue:
        Issue: {issue}
        
        Current Configuration:
        {json.dumps(config, indent=2)}
        
        Vendor: {vendor}
        
        Provide configuration commands to fix the issue in JSON format:
        {{
            "changes": ["description of change 1", "description of change 2"],
            "commands": {{
                "{vendor}": ["command1", "command2"]
            }},
            "explanation": "Why these changes fix the issue"
        }}
        """
        
        try:
            response = self.ai_client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            
            result_text = response.choices[0].message.content
            result = json.loads(result_text)
            return result
        except Exception as e:
            logger.error(f"AI config fix suggestion failed: {e}")
            return self._rule_based_config_fix(issue, config, vendor)
    
    def train_from_resolved_case(self, device_id: str, symptoms: Dict, solution: str):
        """Learn from a resolved troubleshooting case"""
        self.kb.add_troubleshooting_case(
            device_id=device_id,
            symptoms=symptoms,
            root_cause=solution,
            solution=solution,
            config_snapshot=self.kb.get_device_config(device_id)
        )
        logger.info(f"Trained model from resolved case for device {device_id}")


def import_device_configs_from_ostg(knowledge_base: Optional[ConfigKnowledgeBase] = None):
    """Import existing device configurations from OSTG database"""
    kb = knowledge_base or ConfigKnowledgeBase()
    parser = NetworkConfigParser()
    
    try:
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        
        # Get all devices
        devices = device_db.get_all_devices()
        
        for device in devices:
            device_id = device.get('device_id')
            device_name = device.get('Device Name', device.get('device_name', ''))
            
            # Build config from device data
            config_parts = []
            
            # Interface config
            if device.get('interface'):
                config_parts.append(f"interface {device['interface']}")
                if device.get('ipv4_address'):
                    config_parts.append(f"  ip address {device['ipv4_address']}/{device.get('ipv4_mask', '24')}")
            
            # Protocol configs
            if device.get('bgp_config'):
                config_parts.append("\n# BGP Configuration")
                config_parts.append(json.dumps(device['bgp_config'], indent=2))
            
            if device.get('ospf_config'):
                config_parts.append("\n# OSPF Configuration")
                config_parts.append(json.dumps(device['ospf_config'], indent=2))
            
            if device.get('isis_config'):
                config_parts.append("\n# ISIS Configuration")
                config_parts.append(json.dumps(device['isis_config'], indent=2))
            
            config_text = "\n".join(config_parts)
            
            if config_text.strip():
                kb.add_config(device_id, device_name, config_text, vendor='ostg')
                logger.info(f"Imported configuration for device {device_id}")
    
    except Exception as e:
        logger.error(f"Failed to import device configs: {e}")

