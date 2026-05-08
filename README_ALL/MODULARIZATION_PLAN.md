# DevicesTab Modularization Plan

## Overview
This document outlines the plan to modularize `devices_tab.py` by extracting OSPF, BGP, and ISIS functionality into separate modules.

## Statistics
- **Total file size**: 10,266 lines
- **OSPF methods**: 18 methods
- **BGP methods**: 22 methods  
- **ISIS methods**: 20 methods
- **Total protocol methods**: 60 methods

## Module Structure

### Created Modules
1. **utils/devices_tab_ospf.py** - OSPF handler class with all OSPF-related methods
2. **utils/devices_tab_bgp.py** - BGP handler class (to be created)
3. **utils/devices_tab_isis.py** - ISIS handler class (to be created)

### Handler Class Pattern
Each module follows this pattern:
```python
class OSPFHandler:
    def __init__(self, parent_tab):
        self.parent = parent_tab
    
    # All OSPF methods here
```

### Integration Pattern
In `devices_tab.py`:
```python
from utils.devices_tab_ospf import OSPFHandler
from utils.devices_tab_bgp import BGPHandler
from utils.devices_tab_isis import ISISHandler

class DevicesTab(QWidget):
    def __init__(self, main_window=None):
        # ... existing code ...
        
        # Initialize protocol handlers
        self.ospf_handler = OSPFHandler(self)
        self.bgp_handler = BGPHandler(self)
        self.isis_handler = ISISHandler(self)
        
        # Delegate setup methods
        self.setup_ospf_subtab = self.ospf_handler.setup_ospf_subtab
        self.setup_bgp_subtab = self.bgp_handler.setup_bgp_subtab
        self.setup_isis_subtab = self.isis_handler.setup_isis_subtab
```

## Migration Strategy

### Phase 1: Create OSPF Module ✅
- [x] Create `utils/devices_tab_ospf.py`
- [x] Extract all OSPF methods
- [ ] Update `devices_tab.py` to use OSPF handler

### Phase 2: Create BGP Module
- [ ] Create `utils/devices_tab_bgp.py`
- [ ] Extract all BGP methods
- [ ] Update `devices_tab.py` to use BGP handler

### Phase 3: Create ISIS Module
- [ ] Create `utils/devices_tab_isis.py`
- [ ] Extract all ISIS methods
- [ ] Update `devices_tab.py` to use ISIS handler

### Phase 4: Testing
- [ ] Test OSPF functionality
- [ ] Test BGP functionality
- [ ] Test ISIS functionality
- [ ] Test integration between protocols

## Key Methods to Extract

### OSPF Methods
- setup_ospf_subtab
- update_ospf_table
- refresh_ospf_status
- prompt_add_ospf
- prompt_edit_ospf
- prompt_delete_ospf
- on_ospf_table_cell_changed
- apply_ospf_configurations
- start_ospf_protocol
- stop_ospf_protocol
- start_ospf_monitoring
- stop_ospf_monitoring
- periodic_ospf_status_check
- _safe_update_ospf_table
- set_ospf_status_icon
- _apply_ospf_to_server_sync
- _cleanup_ospf_table_for_device

### BGP Methods
- setup_bgp_subtab
- update_bgp_table
- refresh_bgp_status
- on_bgp_selection_changed
- prompt_add_bgp
- prompt_edit_bgp
- prompt_delete_bgp
- on_bgp_table_cell_changed
- apply_bgp_configurations
- start_bgp_protocol
- stop_bgp_protocol
- start_bgp_monitoring
- stop_bgp_monitoring
- periodic_bgp_status_check
- _safe_update_bgp_table
- _get_bgp_neighbor_state
- _get_bgp_neighbor_state_from_database
- _apply_bgp_to_server_sync
- _set_bgp_interim_stopping_state
- _cleanup_bgp_table_for_device
- prompt_attach_route_pools

### ISIS Methods
- setup_isis_subtab
- update_isis_table
- refresh_isis_status
- prompt_add_isis
- prompt_edit_isis
- prompt_delete_isis
- on_isis_table_cell_changed
- apply_isis_configurations
- start_isis_protocol
- stop_isis_protocol
- start_isis_monitoring
- stop_isis_monitoring
- periodic_isis_status_check
- _safe_update_isis_table
- set_isis_status_icon
- _get_isis_status_from_database
- _apply_isis_to_server_sync
- _apply_isis_to_devices
- _remove_isis_from_devices
- _cleanup_isis_table_for_device

## Notes
- All methods should access `self.parent` to access DevicesTab instance
- Methods that need access to DevicesTab attributes should use `self.parent.attribute`
- Signal connections should be maintained in the handler classes
- The modularization maintains backward compatibility by keeping the same method signatures


