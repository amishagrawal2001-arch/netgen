from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
                             QLineEdit, QSpinBox, QGroupBox, QPushButton,
                             QDialogButtonBox, QTableWidget, QTableWidgetItem,
                             QMessageBox, QHeaderView, QLabel, QCheckBox, QListWidget,
                             QListWidgetItem, QProgressDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
import ipaddress
import requests
import json


class DatabaseSaveWorker(QThread):
    """Worker thread for saving route pools to database."""
    
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, server_url, pools_to_save, existing_pools=None):
        super().__init__()
        self.server_url = server_url
        self.pools_to_save = pools_to_save
        self.existing_pools = existing_pools or []
    
    def run(self):
        """Save pools to database."""
        try:
            total_pools = len(self.pools_to_save)
            saved_count = 0
            errors = []
            
            print(f"[BGP ROUTE POOLS] Starting to save {total_pools} pools to {self.server_url}")
            
            # Fetch current database pools to check for existing names
            try:
                response = requests.get(f"{self.server_url}/api/bgp/pools", timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    existing_pools_data = data.get('pools', [])
                    existing_pool_names = [p.get('name', '') for p in existing_pools_data]
                    print(f"[BGP ROUTE POOLS] Found {len(existing_pool_names)} existing pools in database: {existing_pool_names}")
                else:
                    print(f"[BGP ROUTE POOLS] Failed to fetch existing pools: HTTP {response.status_code}")
                    existing_pool_names = []
            except Exception as e:
                print(f"[BGP ROUTE POOLS] Error fetching existing pools: {e}")
                existing_pool_names = []
            
            for i, pool in enumerate(self.pools_to_save):
                try:
                    print(f"[BGP ROUTE POOLS] Processing pool: {pool}")
                    
                    if pool['name'] in existing_pool_names:
                        # Update existing pool
                        url = f"{self.server_url}/api/bgp/pools/{pool['name']}"
                        payload = {
                            "subnet": pool['subnet'],
                            "count": pool['count'],
                            "first_host": pool['first_host'],
                            "last_host": pool['last_host']
                        }
                        print(f"[BGP ROUTE POOLS] Updating pool via PUT {url} with payload: {payload}")
                        response = requests.put(url, json=payload, timeout=10)
                    else:
                        # Create new pool
                        url = f"{self.server_url}/api/bgp/pools"
                        payload = {
                            "name": pool['name'],
                            "subnet": pool['subnet'],
                            "count": pool['count'],
                            "first_host": pool['first_host'],
                            "last_host": pool['last_host']
                        }
                        print(f"[BGP ROUTE POOLS] Creating pool via POST {url} with payload: {payload}")
                        response = requests.post(url, json=payload, timeout=10)
                    
                    if response.status_code in [200, 201]:
                        saved_count += 1
                        print(f"[BGP ROUTE POOLS] Successfully saved pool '{pool['name']}'")
                    else:
                        error_msg = f"HTTP {response.status_code}: {response.text}"
                        errors.append(f"Failed to save pool '{pool['name']}': {error_msg}")
                        print(f"[BGP ROUTE POOLS] Failed to save pool '{pool['name']}': {error_msg}")
                    
                    # Update progress
                    progress = int((i + 1) / total_pools * 100)
                    self.progress.emit(progress)
                    
                except Exception as e:
                    errors.append(f"Error saving pool '{pool['name']}': {str(e)}")
            
            # Determine success/failure
            if errors:
                message = f"Saved {saved_count}/{total_pools} pools. Errors:\n" + "\n".join(errors)
                self.finished.emit(False, message)
            else:
                message = f"Successfully saved all {saved_count} route pools to database!"
                self.finished.emit(True, message)
                
        except Exception as e:
            self.finished.emit(False, f"Database save failed: {str(e)}")


class ManageRoutePoolsDialog(QDialog):
    """Dialog for managing BGP route pools (Step 1: Define route pools globally)."""
    
    def __init__(self, parent=None, existing_pools=None, server_url=None):
        super().__init__(parent)
        self.setWindowTitle("Manage BGP Route Pools")
        self.setFixedSize(650, 550)
        
        self.route_pools = existing_pools or []  # List of {name, subnet, count} dicts
        self.server_url = server_url or "http://localhost:5051"
        self.save_worker = None
        
        # Load existing pools from database if server URL is provided
        if self.server_url and not existing_pools:
            self.load_pools_from_database()
        
        # Main layout
        main_layout = QVBoxLayout()
        
        # Info label
        info_label = QLabel("Define reusable route pools that can be attached to devices.\n"
                           "Each pool can generate multiple routes from a subnet.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #555; margin: 5px; padding: 5px; background: #f0f0f0; border-radius: 3px;")
        main_layout.addWidget(info_label)
        
        # Input form group
        input_group = QGroupBox("Create New Route Pool")
        input_layout = QFormLayout(input_group)
        
        # Pool name
        self.pool_name_input = QLineEdit()
        self.pool_name_input.setPlaceholderText("e.g., customer_routes or test_pool_1")
        input_layout.addRow("Pool Name:", self.pool_name_input)
        
        # Subnet input
        self.subnet_input = QLineEdit()
        self.subnet_input.setPlaceholderText("e.g., 10.0.0.0/24 or 2001:db8::/64")
        input_layout.addRow("Network Subnet:", self.subnet_input)
        
        # Number of routes to generate
        self.route_count_input = QSpinBox()
        self.route_count_input.setRange(1, 100000)
        self.route_count_input.setValue(1)
        self.route_count_input.setToolTip("Number of routes to generate from this subnet")
        input_layout.addRow("Route Count:", self.route_count_input)
        
        # Add and Preview buttons
        add_btn_layout = QHBoxLayout()
        add_btn_layout.addStretch()
        self.preview_button = QPushButton("Preview Routes")
        self.preview_button.clicked.connect(self.preview_routes)
        self.add_pool_button = QPushButton("Add Pool")
        self.add_pool_button.clicked.connect(self.add_pool_to_table)
        add_btn_layout.addWidget(self.preview_button)
        add_btn_layout.addWidget(self.add_pool_button)
        input_layout.addRow("", add_btn_layout)
        
        main_layout.addWidget(input_group)
        
        # Pools table
        pools_group = QGroupBox("Defined Route Pools")
        pools_layout = QVBoxLayout(pools_group)
        
        self.pools_table = QTableWidget()
        self.pools_table.setColumnCount(6)
        self.pools_table.setHorizontalHeaderLabels(["Pool Name", "Network Subnet", "Route Count", "First Host IP", "Last Host IP", "Actions"])
        self.pools_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.pools_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.pools_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.pools_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.pools_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.pools_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.pools_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        pools_layout.addWidget(self.pools_table)
        main_layout.addWidget(pools_group)
        
        # Populate existing pools
        self.populate_pools_table()
        
        # Dialog buttons
        button_box = QDialogButtonBox()
        self.save_db_button = button_box.addButton("Save to Database", QDialogButtonBox.ActionRole)
        self.ok_button = button_box.addButton("Save Locally", QDialogButtonBox.AcceptRole)
        self.cancel_button = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.save_db_button.clicked.connect(self.save_to_database)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        main_layout.addWidget(button_box)
        self.setLayout(main_layout)
    
    def generate_host_ips(self, network, count):
        """Generate first and last host IPs from a network for the specified count."""
        try:
            # Get all host addresses from the network
            hosts = list(network.hosts())
            
            if network.version == 6:
                # For IPv6, use all addresses (no broadcast)
                hosts = list(network)
                # Remove the network address (first address)
                if len(hosts) > 1:
                    hosts = hosts[1:]
            
            if len(hosts) < count:
                raise ValueError(f"Not enough host addresses in network {network}")
            
            # Take the first 'count' host addresses
            selected_hosts = hosts[:count]
            
            first_host = selected_hosts[0]
            last_host = selected_hosts[-1]
            
            return first_host, last_host
            
        except Exception as e:
            raise ValueError(f"Error generating host IPs: {str(e)}")
    
    def preview_routes(self):
        """Preview the routes that would be generated from the current input."""
        pool_name = self.pool_name_input.text().strip()
        subnet = self.subnet_input.text().strip()
        route_count = self.route_count_input.value()
        
        if not subnet:
            QMessageBox.warning(self, "Input Error", "Please enter a network subnet to preview.")
            return
        
        try:
            # Parse the subnet
            try:
                network = ipaddress.IPv4Network(subnet, strict=False)
            except:
                network = ipaddress.IPv6Network(subnet, strict=False)
            
            # Check if subnet has enough addresses
            available_hosts = network.num_addresses - 2  # Subtract network and broadcast addresses
            if network.version == 6:
                available_hosts = network.num_addresses - 1  # IPv6 doesn't have broadcast
            
            if route_count > available_hosts:
                QMessageBox.warning(self, "Insufficient Addresses", 
                                  f"Subnet {subnet} has only {available_hosts} available host addresses,\n"
                                  f"but you requested {route_count} routes.\n\n"
                                  f"Please use a larger subnet or reduce the route count.")
                return
            
            # Generate first and last host IPs
            first_host, last_host = self.generate_host_ips(network, route_count)
            
            # Generate all host routes for preview
            host_routes = self.generate_all_host_routes(network, route_count)
            
            # Show preview dialog
            preview_text = f"Route Pool Preview\n"
            preview_text += f"==================\n\n"
            preview_text += f"Pool Name: {pool_name or '(not specified)'}\n"
            preview_text += f"Subnet: {subnet}\n"
            preview_text += f"Route Count: {route_count}\n"
            preview_text += f"First Host IP: {first_host}\n"
            preview_text += f"Last Host IP: {last_host}\n\n"
            preview_text += f"Generated Routes:\n"
            
            # Show first 10 and last 10 routes if there are many
            if len(host_routes) <= 20:
                for route in host_routes:
                    preview_text += f"  {route}\n"
            else:
                for route in host_routes[:10]:
                    preview_text += f"  {route}\n"
                preview_text += f"  ... ({len(host_routes) - 20} more routes) ...\n"
                for route in host_routes[-10:]:
                    preview_text += f"  {route}\n"
            
            QMessageBox.information(self, "Route Preview", preview_text)
            
        except Exception as e:
            QMessageBox.warning(self, "Preview Error", 
                              f"Error generating route preview:\n\n{str(e)}")
    
    def generate_all_host_routes(self, network, count):
        """Generate all host routes from a network for the specified count."""
        try:
            # Get all host addresses from the network
            hosts = list(network.hosts())
            
            if network.version == 6:
                # For IPv6, use all addresses (no broadcast)
                hosts = list(network)
                # Remove the network address (first address)
                if len(hosts) > 1:
                    hosts = hosts[1:]
            
            if len(hosts) < count:
                raise ValueError(f"Not enough host addresses in network {network}")
            
            # Take the first 'count' host addresses and format as /32 or /128 routes
            selected_hosts = hosts[:count]
            
            if network.version == 4:
                # IPv4: use /32 for individual host routes
                return [f"{host}/32" for host in selected_hosts]
            else:
                # IPv6: use /128 for individual host routes
                return [f"{host}/128" for host in selected_hosts]
                
        except Exception as e:
            raise ValueError(f"Error generating host routes: {str(e)}")
    
    def add_pool_to_table(self):
        """Add a route pool from the input fields to the table."""
        pool_name = self.pool_name_input.text().strip()
        subnet = self.subnet_input.text().strip()
        route_count = self.route_count_input.value()
        
        if not pool_name:
            QMessageBox.warning(self, "Input Error", "Please enter a pool name.")
            return
        
        # Check if pool name looks like a subnet (has "/" or is all digits and dots/colons)
        if "/" in pool_name or pool_name == subnet:
            QMessageBox.warning(self, "Invalid Pool Name", 
                              f"Pool name cannot be a subnet address.\n\n"
                              f"Please use a descriptive name like 'customer_routes' or 'test_pool_1'.\n\n"
                              f"Current name: {pool_name}")
            return
        
        if not subnet:
            QMessageBox.warning(self, "Input Error", "Please enter a network subnet.")
            return
        
        # Check for duplicate pool name
        for pool in self.route_pools:
            if pool["name"].lower() == pool_name.lower():
                QMessageBox.warning(self, "Duplicate Name", 
                                  f"A pool named '{pool_name}' already exists.\nPlease use a different name.")
                return
        
        # Validate subnet format and generate host IPs
        try:
            # Try parsing as IPv4 or IPv6 network
            try:
                network = ipaddress.IPv4Network(subnet, strict=False)
            except:
                network = ipaddress.IPv6Network(subnet, strict=False)
            
            # Check if subnet has enough addresses for the requested count
            available_hosts = network.num_addresses - 2  # Subtract network and broadcast addresses
            if network.version == 6:
                available_hosts = network.num_addresses - 1  # IPv6 doesn't have broadcast
            
            if route_count > available_hosts:
                QMessageBox.warning(self, "Insufficient Addresses", 
                                  f"Subnet {subnet} has only {available_hosts} available host addresses,\n"
                                  f"but you requested {route_count} routes.\n\n"
                                  f"Please use a larger subnet or reduce the route count.")
                return
            
            # Generate first and last host IPs
            first_host, last_host = self.generate_host_ips(network, route_count)
            
        except Exception as e:
            QMessageBox.warning(self, "Invalid Subnet", 
                              f"Invalid network subnet format.\n\n{str(e)}\n\nExamples:\n- 192.168.0.0/24\n- 10.0.0.0/8\n- 2001:db8::/64")
            return
        
        # Add to pools list
        pool_entry = {
            "name": pool_name,
            "subnet": subnet,
            "count": route_count,
            "first_host": str(first_host),
            "last_host": str(last_host)
        }
        self.route_pools.append(pool_entry)
        
        # Add to table
        self.add_pool_row(pool_name, subnet, route_count, str(first_host), str(last_host))
        
        # Clear inputs
        self.pool_name_input.clear()
        self.subnet_input.clear()
        self.route_count_input.setValue(1)
    
    def add_pool_row(self, name, subnet, count, first_host="", last_host=""):
        """Add a pool to the table."""
        row = self.pools_table.rowCount()
        self.pools_table.insertRow(row)
        
        # Pool name
        self.pools_table.setItem(row, 0, QTableWidgetItem(name))
        
        # Subnet
        self.pools_table.setItem(row, 1, QTableWidgetItem(subnet))
        
        # Count
        self.pools_table.setItem(row, 2, QTableWidgetItem(str(count)))
        
        # First Host IP
        self.pools_table.setItem(row, 3, QTableWidgetItem(first_host))
        
        # Last Host IP
        self.pools_table.setItem(row, 4, QTableWidgetItem(last_host))
        
        # Remove button
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(lambda: self.remove_pool_row(row))
        self.pools_table.setCellWidget(row, 5, remove_btn)
    
    def remove_pool_row(self, row):
        """Remove a pool from the table."""
        if row < len(self.route_pools):
            pool_name = self.route_pools[row]["name"]
            del self.route_pools[row]
            print(f"[BGP ROUTE POOLS] Removed pool '{pool_name}'")
        self.pools_table.removeRow(row)
        
        # Rebuild pools list from table (in case row indices changed)
        self.route_pools = []
        for i in range(self.pools_table.rowCount()):
            name = self.pools_table.item(i, 0).text()
            subnet = self.pools_table.item(i, 1).text()
            count = int(self.pools_table.item(i, 2).text())
            first_host = self.pools_table.item(i, 3).text() if self.pools_table.item(i, 3) else ""
            last_host = self.pools_table.item(i, 4).text() if self.pools_table.item(i, 4) else ""
            self.route_pools.append({
                "name": name, 
                "subnet": subnet, 
                "count": count,
                "first_host": first_host,
                "last_host": last_host
            })
    
    def populate_pools_table(self):
        """Populate table with existing pools."""
        self.pools_table.setRowCount(0)
        for pool in self.route_pools:
            first_host = pool.get("first_host", "")
            last_host = pool.get("last_host", "")
            self.add_pool_row(pool["name"], pool["subnet"], pool["count"], first_host, last_host)
    
    def get_pools(self):
        """Get the list of route pools."""
        return self.route_pools
    
    def load_pools_from_database(self):
        """Load existing route pools from database."""
        try:
            response = requests.get(f"{self.server_url}/api/bgp/pools", timeout=10)
            if response.status_code == 200:
                data = response.json()
                pools_data = data.get('pools', [])
                
                # Convert database format to dialog format
                self.route_pools = []
                for pool in pools_data:
                    self.route_pools.append({
                        'name': pool.get('name', ''),
                        'subnet': pool.get('subnet', ''),
                        'count': pool.get('count', 0),
                        'first_host': pool.get('first_host', ''),
                        'last_host': pool.get('last_host', '')
                    })
                
                print(f"[BGP ROUTE POOLS] Loaded {len(self.route_pools)} pools from database")
            else:
                print(f"[BGP ROUTE POOLS] Failed to load pools from database: HTTP {response.status_code}")
        except Exception as e:
            print(f"[BGP ROUTE POOLS] Error loading pools from database: {e}")
            # Continue with empty pools list
    
    def save_to_database(self):
        """Save route pools to database."""
        if not self.route_pools:
            QMessageBox.information(self, "No Pools", "No route pools to save.")
            return
        
        # Create progress dialog
        self.progress_dialog = QProgressDialog("Saving route pools to database...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.show()
        
        # Create and start worker thread
        self.save_worker = DatabaseSaveWorker(self.server_url, self.route_pools, self.route_pools)
        self.save_worker.progress.connect(self.progress_dialog.setValue)
        self.save_worker.finished.connect(self.on_save_finished)
        self.save_worker.start()
    
    def on_save_finished(self, success, message):
        """Handle save completion."""
        self.progress_dialog.close()
        
        if success:
            QMessageBox.information(self, "Save Successful", message)
            # Optionally close the dialog after successful save
            # self.accept()
        else:
            QMessageBox.warning(self, "Save Failed", message)


class AttachRoutePoolsDialog(QDialog):
    """Dialog for attaching route pools to a device (Step 2: Attach to device)."""
    
    def __init__(self, parent=None, device_name="", available_pools=None, attached_pools=None):
        super().__init__(parent)
        self.setWindowTitle(f"Attach Route Pools - {device_name}")
        self.setFixedSize(550, 450)
        
        self.device_name = device_name
        self.available_pools = available_pools or []  # List of all defined pools
        self.attached_pool_names = attached_pools or []  # List of pool names attached to this device
        
        # Main layout
        main_layout = QVBoxLayout()
        
        # Info label
        info_label = QLabel(f"Select which route pools to advertise from device: {device_name}")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-weight: bold; margin: 5px; padding: 5px;")
        main_layout.addWidget(info_label)
        
        # Available pools list with checkboxes
        pools_group = QGroupBox("Available Route Pools")
        pools_layout = QVBoxLayout(pools_group)
        
        if not self.available_pools:
            no_pools_label = QLabel("No route pools defined.\n\nUse 'Manage Route Pools' button to create pools first.")
            no_pools_label.setStyleSheet("color: #888; font-style: italic; padding: 20px;")
            no_pools_label.setAlignment(Qt.AlignCenter)
            pools_layout.addWidget(no_pools_label)
        else:
            # Create checkbox for each pool
            self.pool_checkboxes = {}
            
            for pool in self.available_pools:
                pool_name = pool["name"]
                subnet = pool["subnet"]
                count = pool["count"]
                
                # Create checkbox
                checkbox = QCheckBox(f"{pool_name} - {subnet} ({count} routes)")
                checkbox.setChecked(pool_name in self.attached_pool_names)
                self.pool_checkboxes[pool_name] = checkbox
                
                pools_layout.addWidget(checkbox)
        
        main_layout.addWidget(pools_group)
        
        # Summary label
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("background: #e8f4f8; padding: 10px; border-radius: 3px;")
        self.update_summary()
        main_layout.addWidget(self.summary_label)
        
        # Connect checkboxes to update summary
        if hasattr(self, 'pool_checkboxes'):
            for checkbox in self.pool_checkboxes.values():
                checkbox.toggled.connect(self.update_summary)
        
        # Dialog buttons
        button_box = QDialogButtonBox()
        self.ok_button = button_box.addButton("Apply", QDialogButtonBox.AcceptRole)
        self.cancel_button = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        main_layout.addWidget(button_box)
        self.setLayout(main_layout)
    
    def update_summary(self):
        """Update summary label with selected pools info."""
        if not hasattr(self, 'pool_checkboxes'):
            self.summary_label.setText("No pools available")
            return
        
        selected_count = sum(1 for cb in self.pool_checkboxes.values() if cb.isChecked())
        total_routes = 0
        
        for pool_name, checkbox in self.pool_checkboxes.items():
            if checkbox.isChecked():
                # Find the pool to get route count
                for pool in self.available_pools:
                    if pool["name"] == pool_name:
                        total_routes += pool["count"]
                        break
        
        if selected_count == 0:
            self.summary_label.setText("ℹ️ No route pools selected - device will not advertise any routes")
        else:
            self.summary_label.setText(f"✅ Selected {selected_count} pool(s) → Total {total_routes} routes to advertise")
    
    def get_attached_pools(self):
        """Get list of attached pool names."""
        if not hasattr(self, 'pool_checkboxes'):
            return []
        
        return [name for name, checkbox in self.pool_checkboxes.items() if checkbox.isChecked()]
