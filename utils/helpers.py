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
