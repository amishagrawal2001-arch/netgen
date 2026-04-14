"""
Network Analytics
Performance analytics, traffic analysis, and insights generation
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone, timedelta
import json

logger = logging.getLogger(__name__)


class NetworkAnalytics:
    """Network analytics and insights"""
    
    def __init__(self, use_ai_api: bool = False, api_key: Optional[str] = None):
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        
        # Initialize device database
        try:
            from utils.device_database import DeviceDatabase
            self.device_db = DeviceDatabase()
        except Exception as e:
            logger.error(f"Device database not available: {e}")
            self.device_db = None
        
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
    
    def analyze_performance(self, time_range: Tuple[datetime, datetime],
                           device_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze network performance
        
        Args:
            time_range: Tuple of (start_time, end_time)
            device_id: Optional device ID to filter
        
        Returns:
            Performance analysis dictionary
        """
        start_time, end_time = time_range
        
        try:
            # Get device statistics from database
            if self.device_db:
                if device_id:
                    # Get stats for specific device
                    stats = self._get_device_stats(device_id, start_time, end_time)
                else:
                    # Get stats for all devices
                    stats = self._get_all_device_stats(start_time, end_time)
            else:
                stats = {}
            
            # Analyze performance metrics
            analysis = {
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat()
                },
                "device_id": device_id,
                "metrics": {
                    "average_latency": self._calculate_average_latency(stats),
                    "packet_loss": self._calculate_packet_loss(stats),
                    "throughput": self._calculate_throughput(stats),
                    "error_rate": self._calculate_error_rate(stats)
                },
                "trends": self._analyze_trends(stats),
                "insights": []
            }
            
            # Generate insights
            analysis["insights"] = self.generate_insights(analysis)
            
            return analysis
        
        except Exception as e:
            logger.error(f"Performance analysis failed: {e}")
            return {
                "error": str(e),
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat()
                }
            }
    
    def analyze_traffic(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze network traffic
        
        Args:
            filters: Filter criteria
                - device_id: Device ID
                - protocol: Protocol filter
                - time_range: Time range
                - source_ip: Source IP filter
                - destination_ip: Destination IP filter
        
        Returns:
            Traffic analysis dictionary
        """
        try:
            # Get traffic data (would need traffic capture/stats)
            # For now, return template analysis
            
            analysis = {
                "filters": filters,
                "traffic_summary": {
                    "total_packets": 0,
                    "total_bytes": 0,
                    "packets_per_second": 0,
                    "bytes_per_second": 0
                },
                "protocol_distribution": {},
                "top_talkers": [],
                "top_flows": [],
                "anomalies": []
            }
            
            # Generate insights
            analysis["insights"] = self.generate_insights(analysis)
            
            return analysis
        
        except Exception as e:
            logger.error(f"Traffic analysis failed: {e}")
            return {
                "error": str(e),
                "filters": filters
            }
    
    def analyze_protocols(self, protocol: str, time_range: Tuple[datetime, datetime]) -> Dict[str, Any]:
        """
        Analyze protocol performance
        
        Args:
            protocol: Protocol name (bgp, ospf, isis, etc.)
            time_range: Time range for analysis
        
        Returns:
            Protocol analysis dictionary
        """
        try:
            start_time, end_time = time_range
            
            # Get protocol-specific statistics
            if self.device_db:
                devices = self.device_db.get_all_devices()
                protocol_stats = []
                
                for device in devices:
                    device_id = device.get("device_id")
                    
                    # Get protocol status
                    if protocol == "bgp":
                        bgp_established = device.get("bgp_ipv4_established", False) or device.get("bgp_ipv6_established", False)
                        protocol_stats.append({
                            "device_id": device_id,
                            "established": bgp_established,
                            "state": device.get("bgp_ipv4_state", "Unknown")
                        })
                    elif protocol == "ospf":
                        ospf_established = device.get("ospf_established", False)
                        protocol_stats.append({
                            "device_id": device_id,
                            "established": ospf_established,
                            "state": device.get("ospf_state", "Unknown")
                        })
                    elif protocol == "isis":
                        isis_established = device.get("isis_established", False)
                        protocol_stats.append({
                            "device_id": device_id,
                            "established": isis_established,
                            "state": device.get("isis_state", "Unknown")
                        })
            else:
                protocol_stats = []
            
            # Analyze protocol performance
            established_count = sum(1 for stat in protocol_stats if stat.get("established"))
            total_count = len(protocol_stats)
            
            analysis = {
                "protocol": protocol,
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat()
                },
                "statistics": {
                    "total_devices": total_count,
                    "established": established_count,
                    "not_established": total_count - established_count,
                    "establishment_rate": (established_count / total_count * 100) if total_count > 0 else 0
                },
                "device_status": protocol_stats,
                "insights": []
            }
            
            # Generate insights
            analysis["insights"] = self.generate_insights(analysis)
            
            return analysis
        
        except Exception as e:
            logger.error(f"Protocol analysis failed: {e}")
            return {
                "error": str(e),
                "protocol": protocol
            }
    
    def generate_insights(self, data: Dict[str, Any]) -> List[str]:
        """
        Generate insights from analysis data
        
        Args:
            data: Analysis data
        
        Returns:
            List of insight strings
        """
        insights = []
        
        # Performance insights
        if "metrics" in data:
            metrics = data["metrics"]
            
            if metrics.get("packet_loss", 0) > 0.1:
                insights.append(f"High packet loss detected: {metrics['packet_loss']*100:.2f}%")
            
            if metrics.get("average_latency", 0) > 100:
                insights.append(f"High latency detected: {metrics['average_latency']:.2f}ms")
            
            if metrics.get("error_rate", 0) > 0.05:
                insights.append(f"High error rate: {metrics['error_rate']*100:.2f}%")
        
        # Protocol insights
        if "statistics" in data:
            stats = data["statistics"]
            establishment_rate = stats.get("establishment_rate", 0)
            
            if establishment_rate < 50:
                insights.append(f"Low {data.get('protocol', 'protocol')} establishment rate: {establishment_rate:.1f}%")
            elif establishment_rate < 80:
                insights.append(f"Moderate {data.get('protocol', 'protocol')} establishment rate: {establishment_rate:.1f}%")
            else:
                insights.append(f"Good {data.get('protocol', 'protocol')} establishment rate: {establishment_rate:.1f}%")
        
        # Traffic insights
        if "traffic_summary" in data:
            traffic = data["traffic_summary"]
            if traffic.get("packets_per_second", 0) > 10000:
                insights.append("High packet rate detected - consider traffic shaping")
        
        # Use AI for advanced insights if available
        if self.use_ai_api and self.ai_client and len(insights) == 0:
            ai_insights = self._ai_generate_insights(data)
            insights.extend(ai_insights)
        
        return insights if insights else ["No significant issues detected"]
    
    def _ai_generate_insights(self, data: Dict) -> List[str]:
        """Use AI to generate insights"""
        if not self.ai_client:
            return []
        
        try:
            prompt = f"""Analyze this network data and provide 3-5 key insights:

{json.dumps(data, indent=2, default=str)}

Provide insights as a JSON array of strings."""
            
            response = self.ai_client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            
            insights_text = response.choices[0].message.content
            # Try to parse as JSON
            try:
                insights = json.loads(insights_text)
                if isinstance(insights, list):
                    return insights
            except Exception:
                # If not JSON, split by lines
                return [line.strip() for line in insights_text.split("\n") if line.strip()]
            
            return []
        except Exception as e:
            logger.error(f"AI insight generation failed: {e}")
            return []
    
    def _get_device_stats(self, device_id: str, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Get device statistics for time range"""
        if not self.device_db:
            return []
        
        try:
            # Query device_stats table
            import sqlite3
            conn = sqlite3.connect(self.device_db.db_path)
            conn.row_factory = sqlite3.Row
            
            cursor = conn.execute("""
                SELECT * FROM device_stats
                WHERE device_id = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            """, (device_id, start_time.isoformat(), end_time.isoformat()))
            
            stats = [dict(row) for row in cursor.fetchall()]
            conn.close()
            
            return stats
        except Exception as e:
            logger.error(f"Failed to get device stats: {e}")
            return []
    
    def _get_all_device_stats(self, start_time: datetime, end_time: datetime) -> Dict[str, List[Dict]]:
        """Get statistics for all devices"""
        if not self.device_db:
            return {}
        
        try:
            devices = self.device_db.get_all_devices()
            all_stats = {}
            
            for device in devices:
                device_id = device.get("device_id")
                stats = self._get_device_stats(device_id, start_time, end_time)
                if stats:
                    all_stats[device_id] = stats
            
            return all_stats
        except Exception as e:
            logger.error(f"Failed to get all device stats: {e}")
            return {}
    
    def _calculate_average_latency(self, stats: Dict) -> float:
        """Calculate average latency from stats"""
        # Placeholder - would need actual latency data
        return 0.0
    
    def _calculate_packet_loss(self, stats: Dict) -> float:
        """Calculate packet loss from stats"""
        # Placeholder - would need actual packet loss data
        return 0.0
    
    def _calculate_throughput(self, stats: Dict) -> float:
        """Calculate throughput from stats"""
        # Placeholder - would need actual throughput data
        return 0.0
    
    def _calculate_error_rate(self, stats: Dict) -> float:
        """Calculate error rate from stats"""
        # Placeholder - would need actual error data
        return 0.0
    
    def _analyze_trends(self, stats: Dict) -> Dict[str, str]:
        """Analyze trends in statistics"""
        # Placeholder - would analyze trends over time
        return {
            "latency_trend": "stable",
            "throughput_trend": "stable",
            "error_trend": "stable"
        }




