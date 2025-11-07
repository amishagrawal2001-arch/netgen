# How `frr.conf.template` is Used

## Overview
The `frr.conf.template` file is used as an **initial/default FRR configuration** when FRR Docker containers start. However, this configuration is **overridden by dynamic `vtysh` commands** that configure protocols based on user input.

---

## Template Processing Flow

### **Step 1: Docker Image Build** (`Dockerfile.frr`)
- `frr.conf.template` is copied into the Docker image at `/etc/frr/frr.conf.template`
- This happens during `docker build` when creating the FRR image

### **Step 2: Container Startup** (`start-frr.sh`)
When a FRR container starts, the `start-frr.sh` script runs:

1. **Template Processing**:
   ```bash
   # Replace template variables with environment variables
   sed -e "s/{{DEVICE_NAME}}/${DEVICE_NAME:-frr-device}/g" \
       -e "s/{{LOCAL_ASN}}/${LOCAL_ASN:-65001}/g" \
       -e "s/{{ROUTER_ID}}/${ROUTER_ID:-192.168.0.2}/g" \
       -e "s/{{NETWORK}}/${NETWORK:-192.168.0.0}/g" \
       -e "s/{{NETMASK}}/${NETMASK:-24}/g" \
       -e "s/{{INTERFACE}}/${INTERFACE:-eth0}/g" \
       -e "s/{{IP_ADDRESS}}/${IP_ADDRESS:-192.168.0.2}/g" \
       -e "s/{{IP_MASK}}/${IP_MASK:-24}/g" \
       /etc/frr/frr.conf.template > /etc/frr/frr.conf
   ```

2. **Template Variables**:
   - `{{DEVICE_NAME}}` → `$DEVICE_NAME` environment variable
   - `{{LOCAL_ASN}}` → `$LOCAL_ASN` environment variable (default: 65001)
   - `{{ROUTER_ID}}` → `$ROUTER_ID` environment variable (default: 192.168.0.2)
   - `{{NETWORK}}` → `$NETWORK` environment variable (default: 192.168.0.0)
   - `{{NETMASK}}` → `$NETMASK` environment variable (default: 24)
   - `{{INTERFACE}}` → `$INTERFACE` environment variable (default: eth0)
   - `{{IP_ADDRESS}}` → `$IP_ADDRESS` environment variable (default: 192.168.0.2)
   - `{{IP_MASK}}` → `$IP_MASK` environment variable (default: 24)

3. **Output**: Processed template is written to `/etc/frr/frr.conf`

4. **FRR Daemons Start**: Daemons (bgpd, ospfd, ospf6d, isisd) are started with `/etc/frr/frr.conf`

### **Step 3: Dynamic Configuration Override**
After the container starts with the template configuration:

1. **Global Router-ID** is configured via `vtysh`:
   - `utils/frr_docker.py` → `_configure_global_router_id()`
   - Sets `ip router-id {loopback_ipv4}` (overrides template's `ROUTER_ID`)

2. **BGP Configuration** is configured via `vtysh`:
   - `utils/bgp.py` → `configure_bgp_for_device()`
   - **Removes existing BGP config** from template (lines 76-110 in `bgp.py`)
   - Configures new BGP with user-specified ASN, neighbors, etc.

3. **OSPF Configuration** is configured via `vtysh`:
   - `utils/ospf.py` → `configure_ospf_neighbor()`
   - Overrides template's OSPF configuration

4. **ISIS Configuration** is configured via `vtysh`:
   - `utils/isis.py` → `configure_isis_neighbor()`
   - Overrides template's ISIS configuration

---

## Template Configuration Contents

The template includes:
- **BGP**: `router bgp {{LOCAL_ASN}}` with router-id and network advertisement
- **OSPF**: `router ospf` with router-id and network area 0
- **OSPFv6**: `router ospf6` with router-id and interface area
- **ISIS**: `router isis` with NET and interface configuration
- **Interface**: `interface {{INTERFACE}}` with IP address and OSPF settings
- **Logging**: Log file and syslog configuration

**Note**: All these configurations are **overridden** by dynamic `vtysh` commands.

---

## Potential Issues

### **1. Configuration Conflicts**
The template may create initial configurations that conflict with dynamic configuration:
- Template has `router bgp 65001` but user wants `router bgp 65000`
- Template has `interface eth0` but device uses `vlan20`
- Template has `network 192.168.0.0/24 area 0` but user wants different area

### **2. Current Mitigation**
The code handles this by:
- **BGP**: Removing existing BGP AS before configuring new one (in `utils/bgp.py` lines 76-110)
- **OSPF/ISIS**: Overriding template config with `vtysh` commands
- **Global Router-ID**: Overridden by `_configure_global_router_id()`

### **3. Template Still Useful For**:
- Starting FRR daemons with basic configuration
- Providing default values for router-id, interface, etc.
- Ensuring daemons start correctly even if dynamic config fails

---

## Recommendation

Since we're using **fully dynamic configuration via `vtysh`**, the template could be simplified to:
1. Only enable daemons (bgpd, ospfd, ospf6d, isisd)
2. Basic zebra configuration
3. Logging configuration
4. **Remove** protocol-specific configurations (BGP, OSPF, ISIS) since they're configured dynamically

This would:
- Reduce conflicts with dynamic configuration
- Make the configuration flow clearer
- Ensure no template config interferes with user settings

---

## Current Files

1. **`frr.conf.template`**: Template file in project root
2. **`ostg_docker/frr.conf.template`**: Template file in ostg_docker directory
3. **`Dockerfile.frr`**: Copies template to `/etc/frr/frr.conf.template` in image
4. **`start-frr.sh`**: Processes template and starts FRR daemons
5. **`utils/bgp.py`**: Removes existing BGP config from template before configuring




