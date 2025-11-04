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
                            "last_host": pool['last_host'],
                            "increment_type": pool.get('increment_type', 'host')
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
                            "last_host": pool['last_host'],
                            "increment_type": pool.get('increment_type', 'host')
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
        self.setFixedSize(900, 600)  # Increased width for better column visibility
        
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
        
        # Increment controls
        increment_group = QGroupBox("Increment Controls")
        increment_layout = QHBoxLayout(increment_group)
        
        # Host and Network increment checkboxes in same row
        self.host_increment_checkbox = QCheckBox("Increment Host Addresses")
        self.host_increment_checkbox.setToolTip("Generate multiple host routes by incrementing host addresses based on route count")
        self.host_increment_checkbox.toggled.connect(self.on_host_increment_toggled)
        increment_layout.addWidget(self.host_increment_checkbox)
        
        self.network_increment_checkbox = QCheckBox("Increment Network Addresses")
        self.network_increment_checkbox.setToolTip("Generate multiple subnets by incrementing network addresses based on route count")
        self.network_increment_checkbox.setChecked(True)  # Default to network increment
        self.network_increment_checkbox.toggled.connect(self.on_network_increment_toggled)
        increment_layout.addWidget(self.network_increment_checkbox)
        
        # Add stretch to push checkboxes to the left
        increment_layout.addStretch()
        
        input_layout.addRow("", increment_group)
        
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
        self.pools_table.setColumnCount(8)
        self.pools_table.setHorizontalHeaderLabels(["Select", "Pool Name", "Network Subnet", "Address Family", "Route Count", "First Host IP", "Last Host IP", "Actions"])
        
        # Optimize column widths for better visibility of subnet and host routes
        self.pools_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Select (checkbox)
        self.pools_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Pool Name
        self.pools_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Network Subnet (needs space for IPv6)
        self.pools_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Address Family
        self.pools_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Route Count
        self.pools_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)  # First Host IP (needs space for IPv6)
        self.pools_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)  # Last Host IP (needs space for IPv6)
        self.pools_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)  # Actions
        
        # Set minimum column widths to ensure visibility
        self.pools_table.setColumnWidth(0, 60)   # Select
        self.pools_table.setColumnWidth(1, 120)  # Pool Name
        self.pools_table.setColumnWidth(2, 180)  # Network Subnet (wider for IPv6)
        self.pools_table.setColumnWidth(3, 100)  # Address Family
        self.pools_table.setColumnWidth(4, 80)   # Route Count
        self.pools_table.setColumnWidth(5, 180)  # First Host IP (wider for IPv6)
        self.pools_table.setColumnWidth(6, 180)  # Last Host IP (wider for IPv6)
        self.pools_table.setColumnWidth(7, 100)  # Actions
        self.pools_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        pools_layout.addWidget(self.pools_table)
        main_layout.addWidget(pools_group)
        
        # Populate existing pools
        self.populate_pools_table()
        
        # Dialog buttons
        button_box = QDialogButtonBox()
        self.save_button = button_box.addButton("Save", QDialogButtonBox.AcceptRole)
        self.delete_selected_button = button_box.addButton("Delete Selected", QDialogButtonBox.ActionRole)
        self.cancel_button = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.save_button.clicked.connect(self.save_both_local_and_database)
        self.delete_selected_button.clicked.connect(self.delete_selected_pools)
        self.cancel_button.clicked.connect(self.reject)
        
        main_layout.addWidget(button_box)
        self.setLayout(main_layout)
    
    def generate_host_ips(self, network, count):
        """Generate first and last host IPs from a network for the specified count."""
        try:
            if network.version == 4:
                # IPv4: Get all host addresses from the network
                hosts = list(network.hosts())
                
                if len(hosts) < count:
                    raise ValueError(f"Not enough host addresses in network {network}")
                
                # Take the first 'count' host addresses
                selected_hosts = hosts[:count]
                first_host = selected_hosts[0]
                last_host = selected_hosts[-1]
                
            else:
                # IPv6: Optimized generation without creating massive lists
                # Start from network address + 1
                network_addr = network.network_address
                first_host = network_addr + 1
                
                # Calculate last host address
                last_host = network_addr + count
                
                # Verify we don't exceed the network boundary
                if last_host >= network.broadcast_address:
                    raise ValueError(f"Not enough host addresses in network {network}")
            
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
            
            # Check if increment controls are enabled
            if self.host_increment_checkbox.isChecked() or self.network_increment_checkbox.isChecked():
                # Use increment logic
                actual_count, first_host, last_host, generated_routes = self.generate_incremented_routes(subnet, route_count)
                
                if actual_count == 0:
                    QMessageBox.warning(self, "No Routes Generated", 
                                      f"No routes could be generated from subnet {subnet} with the current increment settings.\n\n"
                                      f"Please check your increment parameters or use a different subnet.")
                    return
                
                # Use generated routes for preview
                host_routes = generated_routes
                route_count = actual_count
                
            else:
                # Use original logic
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
            
            # Show increment information if enabled
            if self.host_increment_checkbox.isChecked():
                preview_text += f"Increment Mode: Host Addresses\n"
                preview_text += f"Route Count: {route_count}\n"
            elif self.network_increment_checkbox.isChecked():
                preview_text += f"Increment Mode: Network Addresses\n"
                preview_text += f"Route Count: {route_count}\n"
            else:
                preview_text += f"Increment Mode: None (Standard)\n"
            
            preview_text += f"First Route: {first_host}\n"
            preview_text += f"Last Route: {last_host}\n\n"
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
            if network.version == 4:
                # IPv4: Get all host addresses from the network
                hosts = list(network.hosts())
                
                if len(hosts) < count:
                    raise ValueError(f"Not enough host addresses in network {network}")
                
                # Take the first 'count' host addresses and format as /32 routes
                selected_hosts = hosts[:count]
                return [f"{host}/32" for host in selected_hosts]
                
            else:
                # IPv6: Optimized generation without creating massive lists
                network_addr = network.network_address
                host_routes = []
                
                # Generate routes starting from network address + 1
                for i in range(1, count + 1):
                    host_ip = network_addr + i
                    # Verify we don't exceed the network boundary
                    if host_ip >= network.broadcast_address:
                        raise ValueError(f"Not enough host addresses in network {network}")
                    host_routes.append(f"{host_ip}/128")
                
                return host_routes
                
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
            
            # Check if increment controls are enabled
            if self.host_increment_checkbox.isChecked() or self.network_increment_checkbox.isChecked():
                # Use increment logic
                actual_count, first_host, last_host, generated_routes = self.generate_incremented_routes(subnet, route_count)
                
                if actual_count == 0:
                    QMessageBox.warning(self, "No Routes Generated", 
                                      f"No routes could be generated from subnet {subnet} with the current increment settings.\n\n"
                                      f"Please check your increment parameters or use a different subnet.")
                    return
                
                # Update route count to actual generated count
                route_count = actual_count
                
            else:
                # Use original logic
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
        
        # Determine increment type
        increment_type = "network" if self.network_increment_checkbox.isChecked() else "host"
        
        # Add to pools list
        pool_entry = {
            "name": pool_name,
            "subnet": subnet,
            "count": route_count,
            "first_host": str(first_host),
            "last_host": str(last_host),
            "increment_type": increment_type
        }
        self.route_pools.append(pool_entry)
        
        # Add to table
        self.add_pool_row(pool_name, subnet, route_count, str(first_host), str(last_host))
        
        # Clear inputs
        self.pool_name_input.clear()
        self.subnet_input.clear()
        self.route_count_input.setValue(1)
        
        # Clear increment controls (reset to default: network increment)
        self.host_increment_checkbox.setChecked(False)
        self.network_increment_checkbox.setChecked(True)  # Default to network increment
    
    def add_pool_row(self, name, subnet, count, first_host="", last_host="", address_family=None):
        """Add a pool to the table."""
        row = self.pools_table.rowCount()
        self.pools_table.insertRow(row)
        
        # Checkbox for selection
        checkbox = QCheckBox()
        self.pools_table.setCellWidget(row, 0, checkbox)
        
        # Pool name
        self.pools_table.setItem(row, 1, QTableWidgetItem(name))
        
        # Subnet
        self.pools_table.setItem(row, 2, QTableWidgetItem(subnet))
        
        # Address Family (detect if not provided)
        if address_family is None:
            address_family = self._detect_address_family(subnet)
        self.pools_table.setItem(row, 3, QTableWidgetItem(address_family.upper()))
        
        # Count
        self.pools_table.setItem(row, 4, QTableWidgetItem(str(count)))
        
        # First Host IP
        self.pools_table.setItem(row, 5, QTableWidgetItem(first_host))
        
        # Last Host IP
        self.pools_table.setItem(row, 6, QTableWidgetItem(last_host))
        
        # Remove button
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(lambda: self.remove_pool_row(row))
        self.pools_table.setCellWidget(row, 7, remove_btn)
    
    def remove_pool_row(self, row):
        """Remove a pool from the table and database."""
        if row < len(self.route_pools):
            pool_name = self.route_pools[row]["name"]
            
            # Ask for confirmation
            reply = QMessageBox.question(self, "Confirm Delete", 
                                       f"Are you sure you want to delete route pool '{pool_name}'?\n\n"
                                       f"This will remove it from both the local table and the database.",
                                       QMessageBox.Yes | QMessageBox.No, 
                                       QMessageBox.No)
            
            if reply == QMessageBox.Yes:
                # Delete from database
                try:
                    response = requests.delete(f"{self.server_url}/api/bgp/pools/{pool_name}", timeout=10)
                    if response.status_code == 200:
                        print(f"[BGP ROUTE POOLS] Successfully deleted pool '{pool_name}' from database")
                        QMessageBox.information(self, "Pool Deleted", f"Route pool '{pool_name}' has been deleted from the database.")
                        
                        # Remove from local table and list
                        del self.route_pools[row]
                        print(f"[BGP ROUTE POOLS] Removed pool '{pool_name}' from local table")
                        self.pools_table.removeRow(row)
                        
                        # Rebuild pools list from table (in case row indices changed)
                        self.route_pools = []
                        for i in range(self.pools_table.rowCount()):
                            name = self.pools_table.item(i, 1).text()
                            subnet = self.pools_table.item(i, 2).text()
                            address_family = self.pools_table.item(i, 3).text().lower() if self.pools_table.item(i, 3) else "ipv4"
                            count = int(self.pools_table.item(i, 4).text())
                            first_host = self.pools_table.item(i, 5).text() if self.pools_table.item(i, 5) else ""
                            last_host = self.pools_table.item(i, 6).text() if self.pools_table.item(i, 6) else ""
                            self.route_pools.append({
                                "name": name, 
                                "subnet": subnet, 
                                "address_family": address_family,
                                "count": count,
                                "first_host": first_host,
                                "last_host": last_host
                            })
                        
                    else:
                        print(f"[BGP ROUTE POOLS] Failed to delete pool '{pool_name}' from database: HTTP {response.status_code}")
                        QMessageBox.warning(self, "Delete Failed", 
                                          f"Failed to delete pool '{pool_name}' from database.\n\n"
                                          f"Error: {response.text}")
                        return  # Don't remove from local table if database delete failed
                except Exception as e:
                    print(f"[BGP ROUTE POOLS] Error deleting pool '{pool_name}' from database: {e}")
                    QMessageBox.warning(self, "Delete Error", 
                                      f"Error deleting pool '{pool_name}' from database.\n\n"
                                      f"Error: {str(e)}")
                    return  # Don't remove from local table if database delete failed
            else:
                return  # User cancelled
    
    def delete_selected_pools(self):
        """Delete multiple selected pools from the table and database."""
        selected_pools = []
        
        # Find selected pools
        for row in range(self.pools_table.rowCount()):
            checkbox = self.pools_table.cellWidget(row, 0)
            if checkbox and checkbox.isChecked():
                if row < len(self.route_pools):
                    pool_name = self.route_pools[row]["name"]
                    selected_pools.append((row, pool_name))
        
        if not selected_pools:
            QMessageBox.information(self, "No Selection", "Please select pools to delete by checking the boxes.")
            return
        
        # Ask for confirmation
        pool_names = [name for _, name in selected_pools]
        reply = QMessageBox.question(self, "Confirm Delete", 
                                   f"Are you sure you want to delete {len(selected_pools)} selected route pool(s)?\n\n"
                                   f"Selected pools:\n" + "\n".join(f"• {name}" for name in pool_names) + "\n\n"
                                   f"This will remove them from both the local table and the database.",
                                   QMessageBox.Yes | QMessageBox.No, 
                                   QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            deleted_count = 0
            failed_deletions = []
            
            # Delete pools in reverse order to maintain row indices
            for row, pool_name in reversed(selected_pools):
                try:
                    # Delete from database
                    response = requests.delete(f"{self.server_url}/api/bgp/pools/{pool_name}", timeout=10)
                    if response.status_code == 200:
                        print(f"[BGP ROUTE POOLS] Successfully deleted pool '{pool_name}' from database")
                        deleted_count += 1
                        
                        # Remove from local table and list
                        if row < len(self.route_pools):
                            del self.route_pools[row]
                        self.pools_table.removeRow(row)
                        
                    else:
                        print(f"[BGP ROUTE POOLS] Failed to delete pool '{pool_name}' from database: HTTP {response.status_code}")
                        failed_deletions.append(f"{pool_name}: HTTP {response.status_code}")
                        
                except Exception as e:
                    print(f"[BGP ROUTE POOLS] Error deleting pool '{pool_name}' from database: {e}")
                    failed_deletions.append(f"{pool_name}: {str(e)}")
            
            # Rebuild pools list from table
            self.route_pools = []
            for i in range(self.pools_table.rowCount()):
                name = self.pools_table.item(i, 1).text()
                subnet = self.pools_table.item(i, 2).text()
                address_family = self.pools_table.item(i, 3).text().lower() if self.pools_table.item(i, 3) else "ipv4"
                count = int(self.pools_table.item(i, 4).text())
                first_host = self.pools_table.item(i, 5).text() if self.pools_table.item(i, 5) else ""
                last_host = self.pools_table.item(i, 6).text() if self.pools_table.item(i, 6) else ""
                self.route_pools.append({
                    "name": name, 
                    "subnet": subnet, 
                    "address_family": address_family,
                    "count": count,
                    "first_host": first_host,
                    "last_host": last_host
                })
            
            # Show results
            if failed_deletions:
                QMessageBox.warning(self, "Partial Delete Success", 
                                  f"Successfully deleted {deleted_count} out of {len(selected_pools)} pools.\n\n"
                                  f"Failed deletions:\n" + "\n".join(f"• {failure}" for failure in failed_deletions))
            else:
                QMessageBox.information(self, "Delete Successful", 
                                      f"Successfully deleted {deleted_count} route pool(s) from both local table and database.")
        else:
            return  # User cancelled
    
    def populate_pools_table(self):
        """Populate table with existing pools."""
        self.pools_table.setRowCount(0)
        for pool in self.route_pools:
            first_host = pool.get("first_host", "")
            last_host = pool.get("last_host", "")
            address_family = pool.get("address_family", "ipv4")
            self.add_pool_row(pool["name"], pool["subnet"], pool["count"], first_host, last_host, address_family)
    
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
                        'address_family': pool.get('address_family', 'ipv4'),
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
    
    def save_both_local_and_database(self):
        """Save route pools both locally and to database."""
        if not self.route_pools:
            QMessageBox.information(self, "No Pools", "No route pools to save.")
            return
        
        # Create progress dialog
        self.progress_dialog = QProgressDialog("Saving route pools locally and to database...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.show()
        
        # Create and start worker thread for database save
        self.save_worker = DatabaseSaveWorker(self.server_url, self.route_pools, self.route_pools)
        self.save_worker.progress.connect(self.progress_dialog.setValue)
        self.save_worker.finished.connect(self.on_save_finished)
        self.save_worker.start()
    
    def on_save_finished(self, success, message):
        """Handle save completion."""
        self.progress_dialog.close()
        
        if success:
            QMessageBox.information(self, "Save Successful", 
                                  f"Route pools saved successfully!\n\n"
                                  f"✅ Saved locally (in dialog)\n"
                                  f"✅ Saved to database\n\n"
                                  f"{message}")
            # Close the dialog after successful save
            self.accept()
        else:
            QMessageBox.warning(self, "Save Failed", 
                              f"Failed to save route pools to database.\n\n"
                              f"❌ Database save failed\n"
                              f"✅ Local save completed\n\n"
                              f"Error: {message}\n\n"
                              f"The pools are saved locally but not in the database.")
            # Still close the dialog since local save is always successful
            self.accept()
    
    def _detect_address_family(self, subnet: str) -> str:
        """
        Detect the address family (IPv4 or IPv6) from a subnet string.
        
        Args:
            subnet: Subnet string (e.g., "192.168.1.0/24" or "2001:db8::/64")
            
        Returns:
            str: "ipv4" or "ipv6"
        """
        if not subnet:
            return "ipv4"  # Default to IPv4
        
        # Check if it contains IPv6 indicators
        if ":" in subnet:
            return "ipv6"
        else:
            return "ipv4"
    
    def on_host_increment_toggled(self, checked):
        """Handle host increment checkbox toggle."""
        if checked:
            self.network_increment_checkbox.setChecked(False)
    
    def on_network_increment_toggled(self, checked):
        """Handle network increment checkbox toggle."""
        if checked:
            self.host_increment_checkbox.setChecked(False)
    
    def generate_incremented_routes(self, base_subnet, route_count):
        """Generate routes using increment logic based on current settings."""
        try:
            import ipaddress
            
            # Parse the base subnet
            base_network = ipaddress.ip_network(base_subnet, strict=False)
            address_family = "ipv6" if base_network.version == 6 else "ipv4"
            
            generated_routes = []
            step_size = 1  # Always use step size of 1
            
            if self.host_increment_checkbox.isChecked():
                # Host increment: Generate multiple host routes from the same subnet based on route count
                network_addr = base_network.network_address
                for i in range(route_count):
                    host_ip = network_addr + 1 + (i * step_size)
                    
                    # Check if we're still within the network boundary
                    if host_ip >= base_network.broadcast_address:
                        break
                    
                    if address_family == "ipv4":
                        route = f"{host_ip}/32"
                    else:
                        route = f"{host_ip}/128"
                    
                    generated_routes.append(route)
                
                # Update route count to actual generated routes
                actual_count = len(generated_routes)
                first_host = generated_routes[0].split('/')[0] if generated_routes else ""
                last_host = generated_routes[-1].split('/')[0] if generated_routes else ""
                
            elif self.network_increment_checkbox.isChecked():
                # Network increment: Generate multiple subnets based on route count
                base_addr = base_network.network_address
                prefix_len = base_network.prefixlen
                
                for i in range(route_count):
                    if address_family == "ipv4":
                        # For IPv4, increment by step_size in the appropriate octet
                        if prefix_len <= 24:  # /24 or larger network
                            # Increment the third octet
                            addr_parts = str(base_addr).split('.')
                            third_octet = int(addr_parts[2]) + (i * step_size)
                            if third_octet > 255:
                                break
                            addr_parts[2] = str(third_octet)
                            new_addr = ipaddress.IPv4Address('.'.join(addr_parts))
                        else:
                            # For smaller networks, increment the second octet
                            addr_parts = str(base_addr).split('.')
                            second_octet = int(addr_parts[1]) + (i * step_size)
                            if second_octet > 255:
                                break
                            addr_parts[1] = str(second_octet)
                            new_addr = ipaddress.IPv4Address('.'.join(addr_parts))
                    else:
                        # For IPv6, increment the network portion correctly
                        # Use a more direct approach for network increment
                        if prefix_len <= 64:
                            # For /64 and larger networks, increment by 2^64 (one /64 subnet)
                            # This will increment the network portion correctly
                            subnet_size = 2 ** 64
                            new_addr = base_addr + (i * step_size * subnet_size)
                        elif prefix_len <= 80:
                            # For /80, increment by 2^48 (one /80 subnet)
                            subnet_size = 2 ** 48
                            new_addr = base_addr + (i * step_size * subnet_size)
                        elif prefix_len <= 120:
                            # For /120, increment by 2^8 (256 addresses)
                            subnet_size = 2 ** 8
                            new_addr = base_addr + (i * step_size * subnet_size)
                        else:
                            # For very small networks, use minimal increment
                            new_addr = base_addr + (i * step_size)
                        
                        # Check if we're still within a reasonable range
                        if prefix_len <= 64:
                            # For /64 and larger, limit to reasonable number of routes
                            if i >= route_count:  # Stop when we've generated the requested number
                                break
                        else:
                            # For smaller networks, check against broadcast address
                            if new_addr >= base_network.broadcast_address:
                                break
                    
                    route = f"{new_addr}/{prefix_len}"
                    generated_routes.append(route)
                
                # Update route count to actual generated routes
                actual_count = len(generated_routes)
                first_host = generated_routes[0] if generated_routes else ""
                last_host = generated_routes[-1] if generated_routes else ""
                
            else:
                # No increment: Use original logic
                return self.generate_host_ips(base_network, route_count)
            
            return actual_count, first_host, last_host, generated_routes
            
        except Exception as e:
            raise ValueError(f"Error generating incremented routes: {str(e)}")


class AttachRoutePoolsDialog(QDialog):
    """Dialog for attaching route pools to a device (Step 2: Attach to device)."""
    
    def __init__(self, parent=None, device_name="", available_pools=None, attached_pools=None, bgp_config=None):
        super().__init__(parent)
        self.setWindowTitle(f"Attach Route Pools - {device_name}")
        self.setFixedSize(550, 450)
        
        self.device_name = device_name
        self.available_pools = available_pools or []  # List of all defined pools
        self.attached_pool_names = attached_pools or []  # List of pool names attached to this device
        self.bgp_config = bgp_config or {}  # BGP configuration to determine address families
        
        # Determine which address families are enabled
        self.enabled_address_families = self._get_enabled_address_families()
        
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
            # Create checkbox for each pool (filtered by address family)
            self.pool_checkboxes = {}
            
            # Filter pools based on enabled address families
            filtered_pools = self._filter_pools_by_address_family(self.available_pools)
            
            if not filtered_pools:
                no_compatible_pools_label = QLabel(f"No compatible route pools found.\n\n"
                                                 f"BGP is configured for: {', '.join(self.enabled_address_families)}\n"
                                                 f"Available pools must match these address families.")
                no_compatible_pools_label.setStyleSheet("color: #888; font-style: italic; padding: 20px;")
                no_compatible_pools_label.setAlignment(Qt.AlignCenter)
                pools_layout.addWidget(no_compatible_pools_label)
            else:
                for pool in filtered_pools:
                    pool_name = pool["name"]
                    subnet = pool["subnet"]
                    count = pool["count"]
                    address_family = pool.get("address_family", "ipv4")
                    
                    # Create checkbox with address family indicator
                    checkbox_text = f"{pool_name} - {subnet} ({count} routes) [{address_family.upper()}]"
                    checkbox = QCheckBox(checkbox_text)
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
    
    def _get_enabled_address_families(self):
        """Determine which address families are enabled in the BGP configuration."""
        enabled_families = []
        
        # Check if IPv4 BGP is enabled (has neighbor IPs)
        ipv4_neighbors = self.bgp_config.get("bgp_neighbor_ipv4", "").strip()
        if ipv4_neighbors:
            enabled_families.append("ipv4")
        
        # Check if IPv6 BGP is enabled (has neighbor IPs)
        ipv6_neighbors = self.bgp_config.get("bgp_neighbor_ipv6", "").strip()
        if ipv6_neighbors:
            enabled_families.append("ipv6")
        
        # If no address families are explicitly enabled, default to both
        if not enabled_families:
            enabled_families = ["ipv4", "ipv6"]
        
        return enabled_families
    
    def _filter_pools_by_address_family(self, pools):
        """Filter pools to only include those matching enabled address families."""
        if not self.enabled_address_families:
            return pools
        
        filtered_pools = []
        for pool in pools:
            pool_address_family = pool.get("address_family", "ipv4")
            if pool_address_family in self.enabled_address_families:
                filtered_pools.append(pool)
        
        return filtered_pools
