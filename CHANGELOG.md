# Changelog

All notable changes to OSTG Traffic Generator will be documented in this file.

## [0.1.52] - 2026-04-13

### Fixed
- ISIS configuration and UEC packet generation issues
- Stream stop: wait for threads to complete before removing from tracker
- MAC Decrement mode and server connectivity checks
- RX packet counting and stream statistics
- BGP IPv4 neighbor state database updates and IPv6 BGP/OSPFv6 configuration
- Interface name handling and OSPF/IS-IS interface normalization
- IPv6 OSPF passive configuration

### Improved
- UI responsiveness: instant device table display and selection preservation
- NVIDIA GPU and DPU diagnostics support
- Link down troubleshooting script and diagnostics
- VXLAN ARP/FDB configuration and SVI IP assignment

### Added
- Comprehensive link down troubleshooting script
- `--nvidia-install-help` command-line option
- Console output for nvidia-smi installation instructions
