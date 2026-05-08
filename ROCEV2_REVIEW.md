# RoCEv2 Increment Options & Opcode Review

## Current Status

### ✅ UI Fields Available

#### GID (Global Identifier) Increment Options:
- **Source GID**: Text field (default: `0:0:0:0:0:ffff:192.168.0.2`)
- **Source GID Step**: Text field (default: `0:0:0:0:0:0:0:1`)
- **GID Source Mode**: Dropdown (`Fixed`, `Increment`)
- **GID Source Step**: Integer field (default: `1`)
- **GID Source Count**: Integer field (default: `1`)
- **Increment Source GID**: Checkbox
- **Destination GID**: Text field (default: `0:0:0:0:0:ffff:192.168.0.3`)
- **Destination GID Step**: Text field (default: `0:0:0:0:0:0:0:1`)
- **GID Destination Mode**: Dropdown (`Fixed`, `Increment`)
- **GID Destination Step**: Integer field (default: `1`)
- **GID Destination Count**: Integer field (default: `1`)
- **Increment Destination GID**: Checkbox

#### QP (Queue Pair) Increment Options:
- **Source QP**: Integer field (default: `0`)
- **Destination QP**: Integer field (default: `0`)
- **QP Count**: Integer field (default: `1`)
- **Increment QP**: Checkbox
- **QP Increment Step**: Integer field (default: `1`)

#### Opcode Options (Current):
- `SendOnly` (0x64)
- `SendOnlySolicited` (not in opcode_map!)
- `SendLast` (not in opcode_map!)
- `SendLastSolicited` (not in opcode_map!)
- `RDMAWrite` (0x08)
- `RDMAWriteOnlyImm` (not in opcode_map!)
- `RDMAReadRequest` (0x06)
- `RDMAReadResponse` (not in opcode_map!)
- `AtomicCompareSwap` (0x12)
- `AtomicFetchAdd` (0x13)
- `CNP` (0x81)

### ❌ Issues Found

#### 1. **GID Increment Not Implemented**
- **Problem**: GID increment fields exist in UI but are **NOT processed** in `get_packet_config()`
- **Location**: `utils/generic.py` - `get_packet_config()` function
- **Impact**: GID values remain fixed even when increment is enabled
- **Fix Needed**: Add GID increment processing similar to MAC/IP increment logic

#### 2. **QP Increment Not Implemented**
- **Problem**: QP increment fields exist in UI but are **NOT processed** in packet generation
- **Location**: 
  - `utils/generic.py` - `get_packet_config()` doesn't process QP increments
  - `utils/rocev2.py` - `build_rocev2_bth()` reads QP directly from `stream_data`, doesn't use increment lists
- **Impact**: QP values remain fixed even when increment is enabled
- **Fix Needed**: 
  - Add QP increment processing in `get_packet_config()`
  - Modify RoCEv2 packet generation to use increment lists

#### 3. **RoCEv2 Packet Generation Doesn't Use Increment Lists**
- **Problem**: `generate_rocev2_packet()` reads values directly from `stream_data`, not from increment lists
- **Location**: `utils/rocev2.py` - `generate_rocev2_packet()` and `build_rocev2_bth()`
- **Impact**: Even if increment lists are created, they're not used
- **Fix Needed**: Modify RoCEv2 generation to accept increment lists and use an index (similar to generic packet generation)

#### 4. **Incomplete Opcode Mapping**
- **Problem**: UI has 11 opcodes, but only 8 are mapped in `build_rocev2_bth()`
- **Missing Mappings**:
  - `SendOnlySolicited` → Should map to `SendOnly` with `solicited=1`
  - `SendLast` → Not mapped (should be 0x66 for UD transport)
  - `SendLastSolicited` → Not mapped
  - `RDMAWriteOnlyImm` → Should map to `RDMAWriteWithImmediate` (0x09)
  - `RDMAReadResponse` → Should map to one of: `RDMAReadResponseOnly` (0x0A), `RDMAReadResponseFirst` (0x0B), `RDMAReadResponseLast` (0x0C), `RDMAReadResponseMiddle` (0x0D)
- **Location**: `utils/rocev2.py` - `build_rocev2_bth()` opcode_map

#### 5. **Limited Opcode Options**
- **Problem**: Only 11 opcodes in UI, but Scapy supports many more (see `scapy/contrib/roce.py`)
- **Available in Scapy but Missing in UI**:
  - `SendFirst` (0x60)
  - `SendMiddle` (0x61)
  - `SendLast` (0x62)
  - `SendLastWithImmediate` (0x63)
  - `SendOnlyWithImmediate` (0x65)
  - `RDMAWriteFirst` (0x66)
  - `RDMAWriteMiddle` (0x67)
  - `RDMAWriteLast` (0x68)
  - `RDMAWriteLastWithImmediate` (0x69)
  - `RDMAWriteOnlyWithImmediate` (0x6B)
  - `RDMAReadResponseFirst` (0x6D)
  - `RDMAReadResponseMiddle` (0x6E)
  - `RDMAReadResponseLast` (0x6F)
  - `RDMAReadResponseOnly` (0x70)
  - `Acknowledge` (0x71)
  - `AtomicAcknowledge` (0x72)
  - `CompareSwap` (0x73)
  - `FetchAdd` (0x74)
- **Transport Types**: Scapy supports RC, UC, RD, UD transports, but UI only supports UD implicitly

#### 6. **GID Format Confusion**
- **Problem**: Two different GID increment mechanisms:
  - `rocev2_source_gid_step` (text field, IPv6-like format: `0:0:0:0:0:0:0:1`)
  - `rocev2_gid_source_step` (integer field, default: `1`)
- **Impact**: Unclear which one is used or how they interact
- **Fix Needed**: Clarify/document GID increment behavior

#### 7. **Source QP Not Used**
- **Problem**: `rocev2_source_qp` field exists in UI but is **never used** in packet generation
- **Location**: `utils/rocev2.py` - Only `rocev2_destination_qp` is used in BTH
- **Note**: Source QP is not part of BTH header (only DQPN is), but it might be used elsewhere

## Recommended Fixes

### Priority 1: Implement GID & QP Increments

1. **Add GID increment processing to `get_packet_config()`**:
   ```python
   # In utils/generic.py, get_packet_config()
   rocev2 = protocol_data.get("rocev2", {}) or {}
   
   # Source GID increment
   gid_src_default = rocev2.get("rocev2_source_gid", "0:0:0:0:0:ffff:192.168.0.2")
   gid_src_list = [gid_src_default]
   if rocev2.get("rocev2_gid_source_mode") == "Increment":
       step = int(rocev2.get("rocev2_gid_source_step", 1))
       count = int(rocev2.get("rocev2_gid_source_count", 1))
       if count > 0:
           # Implement GID increment logic (parse IPv6 format, increment, format back)
           gid_src_list = [increment_gid(gid_src_default, step * i) for i in range(count)]
   
   # Destination GID increment (similar)
   # QP increment (similar, but simpler - just integer)
   ```

2. **Modify RoCEv2 packet generation to use increment lists**:
   - Change `generate_rocev2_packet()` to accept `pkt_cfg` parameter
   - Use index to select from GID/QP lists
   - Update `multithreaded_traffic_gen.py` RoCEv2 loop to use index (similar to generic loop)

### Priority 2: Fix Opcode Mapping

1. **Complete opcode_map in `build_rocev2_bth()`**:
   ```python
   opcode_map = {
       "SendOnly": 0x64,
       "SendOnlySolicited": 0x64,  # Same opcode, but solicited flag = 1
       "SendOnlyWithImmediate": 0x65,
       "SendLast": 0x62,  # UD_SEND_LAST
       "SendLastSolicited": 0x62,  # Same opcode, but solicited flag = 1
       "RDMAWrite": 0x08,
       "RDMAWriteOnlyImm": 0x6B,  # RDMA_WRITE_ONLY_WITH_IMMEDIATE
       "RDMAReadRequest": 0x06,
       "RDMAReadResponse": 0x70,  # RDMA_READ_RESPONSE_ONLY (default)
       "RDMAReadResponseOnly": 0x70,
       "RDMAReadResponseFirst": 0x6D,
       "RDMAReadResponseLast": 0x6F,
       "RDMAReadResponseMiddle": 0x6E,
       "Acknowledge": 0x71,
       "AtomicAcknowledge": 0x72,
       "CompareSwap": 0x73,
       "FetchAdd": 0x74,
       "AtomicCompareSwap": 0x73,  # Alias
       "AtomicFetchAdd": 0x74,    # Alias
       "CNP": 0x81,
   }
   ```

2. **Handle solicited flag for "Solicited" variants**:
   - When opcode contains "Solicited", set `solicited=1` in flags byte

### Priority 3: Expand Opcode Options

1. **Add missing opcodes to UI dropdown** (in `widgets/stream_dialog.py`):
   ```python
   self.rocev2_opcode.addItems([
       # Send operations
       "SendFirst", "SendMiddle", "SendLast", "SendOnly",
       "SendFirstWithImmediate", "SendMiddleWithImmediate", 
       "SendLastWithImmediate", "SendOnlyWithImmediate",
       # RDMA Write operations
       "RDMAWriteFirst", "RDMAWriteMiddle", "RDMAWriteLast", "RDMAWriteOnly",
       "RDMAWriteFirstWithImmediate", "RDMAWriteMiddleWithImmediate",
       "RDMAWriteLastWithImmediate", "RDMAWriteOnlyWithImmediate",
       # RDMA Read operations
       "RDMAReadRequest",
       "RDMAReadResponseFirst", "RDMAReadResponseMiddle", 
       "RDMAReadResponseLast", "RDMAReadResponseOnly",
       # Acknowledge operations
       "Acknowledge", "AtomicAcknowledge",
       # Atomic operations
       "CompareSwap", "FetchAdd",
       # Congestion notification
       "CNP"
   ])
   ```

2. **Add transport type selector** (optional, for advanced users):
   - Add dropdown: `RC`, `UC`, `RD`, `UD` (default: `UD`)
   - Update opcode calculation to include transport base (0x00, 0x20, 0x40, 0x60)

## Testing Checklist

- [ ] GID increment works (source and destination)
- [ ] QP increment works (destination QP in BTH)
- [ ] Multiple packets show different GID/QP values
- [ ] All opcodes map correctly to BTH opcode values
- [ ] Solicited flag is set correctly for "Solicited" opcodes
- [ ] Immediate data flag is set correctly for "WithImmediate" opcodes
- [ ] Packet dump shows correct BTH structure with incremented values

## References

- **IBTA Specification**: InfiniBand Architecture Specification (for BTH structure)
- **Scapy RoCE Module**: `scapy/contrib/roce.py` (for opcode definitions)
- **Current Implementation**: 
  - `utils/rocev2.py` - BTH building
  - `widgets/stream_dialog.py` - UI fields
  - `utils/generic.py` - Increment processing (missing RoCEv2)




