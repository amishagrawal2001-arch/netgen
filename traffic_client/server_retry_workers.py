# server_retry_workers.py
import requests
import time
import threading
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import List, Dict, Optional


class ServerRetryWorker(QThread):
    """Background worker for automatic server retry with exponential backoff."""
    
    # Signals
    server_reconnected = pyqtSignal(dict)  # server object
    server_still_failed = pyqtSignal(dict, str)  # server object, error message
    retry_progress = pyqtSignal(str, str)  # server_address, status_message
    
    def __init__(self, failed_servers: List[Dict], parent=None):
        super().__init__(parent)
        self.failed_servers = failed_servers.copy()
        self._should_stop = False
        self._retry_counts = {}  # Track retry counts per server
        self._max_retries = 10  # Maximum retry attempts
        self._base_delay = 5  # Base delay in seconds
        self._max_delay = 300  # Maximum delay in seconds (5 minutes)
        
        # Create session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=2,  # Only 2 retries per request
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    def stop(self):
        """Request the worker to stop gracefully."""
        self._should_stop = True
    
    def add_failed_server(self, server: Dict):
        """Add a server to the retry list."""
        server_address = server.get("address")
        if server not in self.failed_servers:
            self.failed_servers.append(server)
            self._retry_counts[server_address] = 0
            print(f"[RETRY WORKER] Added {server_address} to retry list")
    
    def remove_failed_server(self, server: Dict):
        """Remove a server from the retry list."""
        server_address = server.get("address")
        if server in self.failed_servers:
            self.failed_servers.remove(server)
            self._retry_counts.pop(server_address, None)
            print(f"[RETRY WORKER] Removed {server_address} from retry list")
    
    def _calculate_delay(self, retry_count: int) -> float:
        """Calculate exponential backoff delay."""
        delay = min(self._base_delay * (2 ** retry_count), self._max_delay)
        # Add some jitter to prevent thundering herd
        jitter = delay * 0.1 * (0.5 - threading.random())
        return delay + jitter
    
    def _test_server_connection(self, server: Dict) -> bool:
        """Test if a server is reachable."""
        server_address = server.get("address")
        try:
            # Use shorter timeout for retry attempts
            response = self.session.get(f"{server_address}/api/ping", timeout=3)
            return response.status_code == 200
        except Exception as e:
            print(f"[RETRY WORKER] Connection test failed for {server_address}: {e}")
            return False
    
    def _fetch_server_interfaces(self, server: Dict) -> Optional[List]:
        """Fetch interfaces from a server."""
        server_address = server.get("address")
        try:
            response = self.session.get(f"{server_address}/api/interfaces", timeout=3)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[RETRY WORKER] Server {server_address} returned status {response.status_code}")
                return None
        except Exception as e:
            print(f"[RETRY WORKER] Failed to fetch interfaces from {server_address}: {e}")
            return None
    
    def run(self):
        """Main retry loop with exponential backoff."""
        print(f"[RETRY WORKER] Started with {len(self.failed_servers)} failed servers")
        
        while not self._should_stop and self.failed_servers:
            servers_to_retry = self.failed_servers.copy()
            
            for server in servers_to_retry:
                if self._should_stop:
                    break
                
                server_address = server.get("address")
                retry_count = self._retry_counts.get(server_address, 0)
                
                # Skip if max retries reached
                if retry_count >= self._max_retries:
                    print(f"[RETRY WORKER] Max retries reached for {server_address}, removing from retry list")
                    self.remove_failed_server(server)
                    self.server_still_failed.emit(server, f"Max retries ({self._max_retries}) reached")
                    continue
                
                # Emit progress signal
                self.retry_progress.emit(server_address, f"Retry attempt {retry_count + 1}/{self._max_retries}")
                
                # Test connection
                if self._test_server_connection(server):
                    print(f"[RETRY WORKER] ✅ Server {server_address} is reachable, testing full connection...")
                    
                    # Try to fetch interfaces
                    interfaces = self._fetch_server_interfaces(server)
                    if interfaces is not None:
                        print(f"[RETRY WORKER] ✅ Server {server_address} fully recovered!")
                        
                        # Update server status
                        server["online"] = True
                        server["interfaces"] = interfaces
                        
                        # Remove from failed list
                        self.remove_failed_server(server)
                        
                        # Emit success signal
                        self.server_reconnected.emit(server)
                        continue
                
                # Connection failed, increment retry count
                self._retry_counts[server_address] = retry_count + 1
                print(f"[RETRY WORKER] ❌ Server {server_address} still failed (attempt {retry_count + 1})")
            
            # Calculate delay for next retry cycle
            if not self._should_stop and self.failed_servers:
                # Use the minimum retry count for delay calculation
                min_retry_count = min(self._retry_counts.values()) if self._retry_counts else 0
                delay = self._calculate_delay(min_retry_count)
                
                print(f"[RETRY WORKER] Waiting {delay:.1f}s before next retry cycle...")
                
                # Sleep in small increments to allow for early termination
                sleep_increment = 0.5
                total_sleep_time = 0
                while total_sleep_time < delay and not self._should_stop:
                    time.sleep(sleep_increment)
                    total_sleep_time += sleep_increment
        
        print(f"[RETRY WORKER] Stopped. Remaining failed servers: {len(self.failed_servers)}")


class HealthCheckWorker(QThread):
    """Background worker for non-blocking server health checks."""
    
    # Signals
    health_status_updated = pyqtSignal(dict, bool)  # server, is_online
    server_interfaces_updated = pyqtSignal(dict, list)  # server, interfaces
    
    def __init__(self, servers: List[Dict], parent=None):
        super().__init__(parent)
        self.servers = servers
        self._should_stop = False
        
        # Create session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=1,  # Only 1 retry for health checks
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    def stop(self):
        """Request the worker to stop gracefully."""
        self._should_stop = True
    
    def update_servers(self, servers: List[Dict]):
        """Update the servers list."""
        self.servers = servers.copy()
    
    def _check_server_health(self, server: Dict) -> tuple[bool, Optional[List]]:
        """Check server health and return (is_online, interfaces)."""
        server_address = server.get("address")
        try:
            # Quick ping check first with very short timeout
            ping_response = self.session.get(f"{server_address}/api/ping", timeout=0.5)
            if ping_response.status_code != 200:
                return False, None
            
            # If ping succeeds, check interfaces with short timeout
            interfaces_response = self.session.get(f"{server_address}/api/interfaces", timeout=0.5)
            if interfaces_response.status_code == 200:
                interfaces = interfaces_response.json()
                return True, interfaces
            else:
                return True, None  # Server is online but interfaces failed
                
        except Exception:
            # Don't print every health check failure to reduce spam
            return False, None
    
    def run(self):
        """Main health check loop."""
        print(f"[HEALTH CHECK] Started monitoring {len(self.servers)} servers")
        
        while not self._should_stop:
            if not self.servers:
                time.sleep(1)  # Wait a bit if no servers
                continue
            
            for server in self.servers:
                if self._should_stop:
                    break
                
                server_address = server.get("address")
                current_status = server.get("online", False)
                
                # Check server health
                is_online, interfaces = self._check_server_health(server)
                
                # Update server status if changed
                if is_online != current_status:
                    server["online"] = is_online
                    if is_online:
                        server["interfaces"] = interfaces or []
                    print(f"[HEALTH CHECK] Server {server_address} status changed: {current_status} -> {is_online}")
                    
                    # Emit status update signal
                    self.health_status_updated.emit(server, is_online)
                else:
                    print(f"[HEALTH CHECK] Server {server_address} status unchanged: {is_online}")
                
                # Emit interfaces update if server is online
                if is_online and interfaces:
                    self.server_interfaces_updated.emit(server, interfaces)
            
            # Wait before next health check cycle
            if not self._should_stop:
                time.sleep(10)  # Health check every 10 seconds to reduce load
        
        print("[HEALTH CHECK] Stopped")


class ConnectionManager:
    """Manages HTTP connections with pooling and retry strategies."""
    
    def __init__(self):
        self.session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        # Configure adapter with connection pooling
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,  # Number of connection pools
            pool_maxsize=20,      # Maximum connections per pool
            pool_block=False      # Don't block when pool is full
        )
        
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        print("[CONNECTION MANAGER] Initialized with connection pooling")
    
    def get(self, url: str, timeout: int = 5, **kwargs) -> requests.Response:
        """Make a GET request with connection pooling."""
        return self.session.get(url, timeout=timeout, **kwargs)
    
    def post(self, url: str, timeout: int = 5, **kwargs) -> requests.Response:
        """Make a POST request with connection pooling."""
        return self.session.post(url, timeout=timeout, **kwargs)
    
    def close(self):
        """Close the session and all connections."""
        self.session.close()
        print("[CONNECTION MANAGER] Closed all connections")
