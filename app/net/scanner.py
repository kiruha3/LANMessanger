"""Сканер локальной сети: параллельный ping подсети + MAC из ARP + hostname."""

import ipaddress
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 80))  # трафик не отправляется, лишь выбор интерфейса
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _default_routes() -> list[tuple[str, str]]:
    """(ip интерфейса, шлюз) для маршрутов по умолчанию. route print —
    только цифры, не зависит от языка системы."""
    try:
        res = subprocess.run(["route", "print", "-4"], capture_output=True,
                             timeout=10, creationflags=CREATE_NO_WINDOW)
        out = res.stdout.decode("ascii", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return [(local_ip(), "")]
    routes: list[tuple[str, str]] = []
    seen = set()
    for line in out.splitlines():
        m = re.match(r"\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s+"
                     r"(\d+\.\d+\.\d+\.\d+)\s+\d+", line)
        if m and m.group(2) not in seen:
            seen.add(m.group(2))
            routes.append((m.group(2), m.group(1)))
    return routes or [(local_ip(), "")]


def _interface_masks() -> dict[str, int]:
    """ip -> длина префикса. Значения в ipconfig числовые — метки не парсим."""
    try:
        res = subprocess.run(["ipconfig"], capture_output=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    for enc in ("cp866", "mbcs", "utf-8"):
        try:
            text = res.stdout.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return {}
    masks: dict[str, int] = {}
    cur_ip = None
    for line in text.splitlines():
        m = re.search(r"IPv4.*?:\s*(\d+\.\d+\.\d+\.\d+)", line)
        if m:
            cur_ip = m.group(1)
            continue
        m = re.search(r":\s*(255\.\d+\.\d+\.\d+)\s*$", line)
        if m and cur_ip:
            mask = m.group(1)
            try:
                masks[cur_ip] = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
            except ValueError:
                pass
            cur_ip = None
    return masks


def lan_interfaces() -> list[tuple[str, int, str]]:
    """(ip, prefix, шлюз) реальных интерфейсов: LAN, VPN — с маршрутом
    по умолчанию; виртуальные host-only адаптеры отфильтрованы."""
    masks = _interface_masks()
    return [(ip, masks.get(ip, 24), gw) for ip, gw in _default_routes()]


def subnet_hosts(ip: str, prefix: int = 24) -> list[str]:
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    return [str(addr) for addr in net.hosts()]


def ping(ip: str, timeout_ms: int = 250) -> bool:
    if sys.platform == "win32":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), ip]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout_ms / 1000 + 1,
                             creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    # exit code 0 бывает и при "Destination host unreachable" — надёжнее искать TTL=
    return b"TTL=" in res.stdout or b"ttl=" in res.stdout


def arp_table() -> dict[str, str]:
    """ip -> mac из кэша ARP (заполняется после ping)."""
    try:
        res = subprocess.run(["arp", "-a"], capture_output=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    table = {}
    for line in res.stdout.decode("mbcs", errors="replace").splitlines():
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})", line)
        if m:
            table[m.group(1)] = m.group(2).lower()
    return table


def resolve_hostname(ip: str, timeout: float = 0.4) -> str:
    socket.setdefaulttimeout(timeout)
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, socket.herror):
        return ""
    finally:
        socket.setdefaulttimeout(None)


def scan(on_host=None, prefix: int = 24, workers: int = 128) -> list[dict]:
    """Сканирует подсети всех реальных интерфейсов (LAN + VPN).

    on_host(ip, hostname, mac) вызывается для каждого живого хоста СРАЗУ,
    как только он ответил на ping (mac на этом этапе пустой — ARP читается
    в конце и попадает только в возвращаемый список).
    """
    nets: list[tuple[ipaddress.IPv4Network, str]] = []  # (сеть, шлюз)
    own_ips: set[str] = set()
    hosts: list[str] = []
    for iface_ip, iface_prefix, gw in lan_interfaces():
        eff = max(iface_prefix, 22)  # не шире /22
        net = ipaddress.ip_network(f"{iface_ip}/{eff}", strict=False)
        nets.append((net, gw))
        own_ips.add(iface_ip)
        hosts += [str(a) for a in net.hosts()]
    hosts = list(dict.fromkeys(hosts))  # без дублей
    results: list[dict] = []

    def probe(ip: str) -> dict | None:
        if not ping(ip):
            return None
        return {"ip": ip, "hostname": resolve_hostname(ip), "mac": ""}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(probe, ip) for ip in hosts]
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)
                if on_host:
                    on_host(r["ip"], r["hostname"], r["mac"])

    macs = arp_table()

    def is_phantom(ip_str: str) -> bool:
        """Ответил через VPN/маршрутизацию, а не локально: в ARP его нет,
        хотя интерфейс — ARP-based (у его шлюза MAC есть)."""
        if ip_str in own_ips:
            return False
        addr = ipaddress.ip_address(ip_str)
        for net, gw in nets:
            if addr in net:
                if not gw or not macs.get(gw):
                    return False  # туннель point-to-point, ARP нет и не будет
                return ip_str != gw and not macs.get(ip_str)
        return False

    filtered = []
    for r in results:
        if is_phantom(r["ip"]):
            continue
        r["mac"] = macs.get(r["ip"], "")
        filtered.append(r)
    filtered.sort(key=lambda r: tuple(int(p) for p in r["ip"].split(".")))
    return filtered
