import ipaddress
import os

PROTO = "LANMSG/1"

UDP_PORT = int(os.environ.get("LANMSG_UDP_PORT", "45677"))
TCP_PORT = int(os.environ.get("LANMSG_TCP_PORT", "45678"))
BROADCAST_ADDR = os.environ.get("LANMSG_BROADCAST", "255.255.255.255")

ANNOUNCE_INTERVAL = float(os.environ.get("LANMSG_ANNOUNCE_INTERVAL", "10"))
NODE_TIMEOUT = float(os.environ.get("LANMSG_NODE_TIMEOUT", "45"))

MAX_UDP_PACKET = 4096
MAX_TCP_PAYLOAD = 64 * 1024
MAX_MESSAGE_LEN = 16 * 1024


def is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local
