import psutil
import ipaddress

def is_interface_up(interface):
    stats = psutil.net_if_stats().get(interface)
    return stats.isup if stats else False


def increment_ip(ip_str, step=1):
    try:
        ip_obj = ipaddress.IPv4Address(ip_str)
        ip_obj += step
        return str(ip_obj)
    except Exception:
        return ip_str

def increment_ipv6(ip_str, step=1):
    try:
        ip_obj = ipaddress.IPv6Address(ip_str)
        ip_obj += step
        return str(ip_obj)
    except Exception:
        return ip_str

def increment_mac(mac_str, step=1):
    try:
        mac_int = int(mac_str.replace(":", ""), 16)
        mac_int = (mac_int + step) & 0xFFFFFFFFFFFF
        return ':'.join(f'{(mac_int >> ele) & 0xff:02x}' for ele in range(40, -1, -8))
    except Exception:
        return mac_str

def increment_gid(gid_str, step=1):
    """
    Increment a GID (Global Identifier) in IPv6 format.
    GID format: 0:0:0:0:0:ffff:192.168.0.2 (IPv4-mapped IPv6)
    or full IPv6: 2001:db8::1
    
    For IPv4-mapped format, increment the IPv4 part.
    For full IPv6, increment as IPv6.
    """
    try:
        # Check if it's IPv4-mapped format (contains ::ffff: or :ffff:)
        if '::ffff:' in gid_str or ':ffff:' in gid_str:
            # Extract IPv4 part (last component after ffff)
            parts = gid_str.split(':')
            ipv4_part = None
            for i, part in enumerate(parts):
                if part == 'ffff' and i + 1 < len(parts):
                    # Next part should be IPv4 address
                    ipv4_part = parts[i + 1]
                    break
            
            if ipv4_part:
                # Increment IPv4 address
                ipv4_obj = ipaddress.IPv4Address(ipv4_part)
                ipv4_obj += step
                # Reconstruct GID with incremented IPv4
                new_gid = ':'.join(parts[:i+1]) + ':' + str(ipv4_obj)
                return new_gid
        
        # Try as full IPv6
        ipv6_obj = ipaddress.IPv6Address(gid_str)
        ipv6_obj += step
        return str(ipv6_obj)
    except Exception:
        return gid_str
