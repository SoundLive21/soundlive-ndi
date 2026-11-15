# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Mesures locales des récepteurs Linux, relevées dans le thread réseau."""
from ipaddress import ip_address
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

ARM_CORES = {
    "0xd03": "ARM Cortex-A53",
    "0xd07": "ARM Cortex-A57",
    "0xd08": "ARM Cortex-A72",
    "0xd0b": "ARM Cortex-A76",
}


def cpu_ticks():
    if sys.platform == "win32":
        import ctypes
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError("Mesure CPU indisponible.")
        return kernel.value + user.value, idle.value
    fields = Path("/proc/stat").read_text().splitlines()[0].split()
    ticks = [int(value) for value in fields[1:9]]
    return sum(ticks), sum(ticks[3:5])


def memory_bytes():
    if sys.platform == "win32":
        import ctypes
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint), ("load", ctypes.c_uint),
                        ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                        ("remaining", ctypes.c_ulonglong * 5)]
        memory = Memory(length=ctypes.sizeof(Memory))
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise OSError("Mesure RAM indisponible.")
        return memory.total, memory.total - memory.available
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        if name in ("MemTotal", "MemAvailable"):
            memory[name] = int(value.split()[0]) * 1024
    total = memory["MemTotal"]
    return total, max(0, total - memory["MemAvailable"])


def hardware_info():
    if sys.platform == "win32":
        return dict(os=f"Windows {platform.release()}", architecture=platform.machine(),
                    cpu=os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()), cores=os.cpu_count())
    cpu = {}
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            cpu.setdefault(key.strip(), value.strip())
    model_path = Path("/sys/firmware/devicetree/base/model")
    return dict(
        os=platform.freedesktop_os_release().get("PRETTY_NAME", "Linux"),
        architecture=platform.machine(),
        model=model_path.read_text().rstrip("\0\n") if model_path.is_file() else "",
        cpu=(ARM_CORES.get(cpu.get("CPU part")) if cpu.get("CPU implementer") == "0x41" else None)
            or cpu.get("model name") or cpu.get("Hardware") or platform.machine(),
        cores=os.cpu_count(),
    )


def ip_json(*args):
    command = shutil.which("ip") or "/usr/sbin/ip"
    result = subprocess.run([command, "-j", *args], capture_output=True, check=True,
                            text=True, timeout=0.7)
    return json.loads(result.stdout)


def network_info(server=None):
    if sys.platform == "win32":
        script = ("[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
                  "$rows = foreach ($a in [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) { "
                  "if ($a.NetworkInterfaceType -eq 'Loopback') { continue }; "
                  "$kind = switch ($a.NetworkInterfaceType) { 'Wireless80211' {'wifi'} "
                  "'Ethernet' {'ethernet'} 'GigabitEthernet' {'ethernet'} default {'other'} }; "
                  "$stats = $null; "
                  "if ($a.OperationalStatus -eq 'Up') { try { $stats = $a.GetIPStatistics() } catch {} }; "
                  "[pscustomobject]@{id=$a.Id; name=$a.Name; kind=$kind; connected=$a.OperationalStatus -eq 'Up'; "
                  "tx_bytes=$stats.BytesSent; rx_bytes=$stats.BytesReceived; "
                  "addresses=@($a.GetIPProperties().UnicastAddresses | "
                  "Where-Object {$_.Address.AddressFamily -eq 'InterNetwork'} | "
                  "ForEach-Object {$_.Address.ToString()})} }; ConvertTo-Json -Compress -InputObject @($rows)")
        run = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, encoding="utf-8-sig", check=True, timeout=3,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        rows = json.loads(run.stdout or "[]")
        local_ip = None
        if server:
            try:
                url = urlsplit(server)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                    route.connect((str(ip_address(url.hostname)), url.port or 80))
                    local_ip = route.getsockname()[0]
            except (OSError, ValueError):
                pass
        for row in rows:
            row["to_regie"] = local_ip in row["addresses"]
        return sorted(rows, key=lambda item: (not item["to_regie"], not item["connected"], item["name"]))[:12]
    route_device = ""
    if server:
        try:
            host = str(ip_address(urlsplit(server).hostname))
            routes = ip_json("route", "get", host)
            if routes:
                route_device = routes[0].get("dev", "")
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    interfaces = []
    try:
        devices = ip_json("address", "show")
    except (OSError, ValueError, subprocess.SubprocessError):
        devices = []
        for path in Path("/sys/class/net").glob("*"):
            try:
                state = (path / "operstate").read_text().strip()
            except OSError:
                continue
            devices.append(dict(ifname=path.name, flags=["LOWER_UP"] if state in ("up", "unknown") else []))
    for item in devices:
        name = item["ifname"]
        if name == "lo" or "LOOPBACK" in item.get("flags", []):
            continue
        sysfs = Path("/sys/class/net") / name
        if (sysfs / "wireless").is_dir() or (sysfs / "phy80211").exists() or name.startswith("wl"):
            kind = "wifi"
        elif item.get("link_type") == "ether" and (name.startswith(("en", "eth")) or (sysfs / "device").exists()):
            kind = "ethernet"
        else:
            kind = "other"
        interfaces.append(dict(
            id=name, name=name, kind=kind, connected="LOWER_UP" in item.get("flags", []),
            to_regie=name == route_device,
            addresses=[addr["local"] for addr in item.get("addr_info", [])
                       if addr.get("family") == "inet"][:4],
        ))
    return sorted(interfaces, key=lambda item: (not item["to_regie"], not item["connected"], item["name"]))[:12]


class NetworkRates:
    def __init__(self):
        self.previous = {}
        self.sampled_at = None

    def sample(self, interfaces):
        if interfaces is None:
            self.previous = {}
            self.sampled_at = None
            return None
        now = time.monotonic()
        elapsed = now - self.sampled_at if self.sampled_at is not None else 0
        rows, counters = [], {}
        for interface in interfaces:
            identifier = interface["id"]
            row = dict(interface)
            statistics = Path("/sys/class/net") / row["name"] / "statistics"
            for direction in ("rx", "tx"):
                counter = direction + "_bytes"
                value = row.pop(counter, None)
                if sys.platform != "win32" and row["connected"]:
                    try:
                        value = int((statistics / counter).read_text())
                    except (OSError, ValueError):
                        value = None
                key = (identifier, direction)
                previous = self.previous.get(key)
                if type(value) is int and row["connected"]:
                    counters[key] = value
                row[direction + "_mbps"] = (
                    round((value - previous) * 8 / elapsed / 1_000_000, 3)
                    if key in counters and previous is not None and elapsed > 0 and value >= previous else None
                )
            rows.append(row)
        self.previous, self.sampled_at = counters, now
        return rows


class Diagnostic:
    def __init__(self):
        self.last_ticks = None
        self.hardware = {}
        self.disk = {}
        self.network = None
        self.rates = NetworkRates()
        self.network_refresh_at = 0
        self.refresh_at = 0

    def sample(self, server):
        data = {}
        try:
            total, idle = cpu_ticks()
            if self.last_ticks:
                elapsed = total - self.last_ticks[0]
                idle_elapsed = idle - self.last_ticks[1]
                if elapsed > 0 and 0 <= idle_elapsed <= elapsed:
                    data["cpu_pct"] = round(100 * (elapsed - idle_elapsed) / elapsed, 1)
            self.last_ticks = total, idle
        except (OSError, IndexError, ValueError):
            pass

        try:
            total, used = memory_bytes()
            if total > 0:
                data.update(ram_total=total, ram_used=used)
        except (OSError, KeyError, ValueError):
            pass

        if time.monotonic() >= self.refresh_at:
            if not self.hardware:
                try:
                    self.hardware = hardware_info()
                except (OSError, ValueError):
                    pass
            try:
                disk = shutil.disk_usage(Path.home().anchor if sys.platform == "win32" else "/")
                self.disk = dict(disk_total=disk.total, disk_free=disk.free)
            except OSError:
                self.disk = {}
            self.refresh_at = time.monotonic() + 10
        if time.monotonic() >= self.network_refresh_at:
            self.read_network(server)
            self.network_refresh_at = time.monotonic() + (0 if sys.platform == "win32" else 4)
        return {**self.hardware, **self.disk, **data, "interfaces": self.rates.sample(self.network)}

    def read_network(self, server):
        try:
            self.network = network_info(server)
        except (OSError, KeyError, ValueError, subprocess.SubprocessError):
            self.network = None
