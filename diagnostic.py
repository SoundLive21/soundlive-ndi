# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Mesures locales et vérification des mises à jour système."""
from ipaddress import ip_address
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from urllib.parse import urlsplit

ARM_CORES = {
    "0xd03": "ARM Cortex-A53",
    "0xd07": "ARM Cortex-A57",
    "0xd08": "ARM Cortex-A72",
    "0xd0b": "ARM Cortex-A76",
}


WINDOWS_UPDATE_QUERY = """
$ErrorActionPreference = 'Stop'
$searcher = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
$searcher.Online = $true
$result = $searcher.Search("IsInstalled=0 and IsHidden=0 and Type='Software' and BrowseOnly=0")
if ($result.ResultCode -ne 2) { exit 1 }
[Console]::WriteLine($result.Updates.Count)
"""


def apt_update_count(apt):
    # Read the host's sources and installed package database in a separate user cache.
    with TemporaryDirectory(prefix="soundlive-apt-") as directory:
        root = Path(directory)
        for name in ("lists/partial", "archives/partial", "conf.d"):
            (root / name).mkdir(parents=True)
        config = root / "check.conf"
        config.write_text(
            f'Dir::Etc::Parts {json.dumps(str(root / "conf.d"))};\n'
            'Dir::Etc::main "/dev/null";\n'
            f'Dir::State::lists {json.dumps(str(root / "lists"))};\n'
            f'Dir::Cache {json.dumps(str(root))};\n'
            'Dir::Cache::pkgcache "";\nDir::Cache::srcpkgcache "";\n'
            'APT::Update::Error-Mode "any";\nAcquire::Retries "0";\n'
            'Acquire::Languages "none";\nAcquire::http::Timeout "15";\nAcquire::https::Timeout "15";\n',
            encoding="utf-8",
        )
        env = {**os.environ, "APT_CONFIG": str(config), "LC_ALL": "C"}
        subprocess.run([apt, "update"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=150)
        if not any((root / "lists").glob("*_Packages*")):
            raise ValueError("Aucun index de paquets APT disponible.")
        simulated = subprocess.check_output([apt, "--simulate", "--no-remove", "--with-new-pkgs", "upgrade"],
                                            env=env, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
        return len(re.findall(rb"^Inst ", simulated, re.MULTILINE))


def count_os_updates():
    if sys.platform == "win32":
        output = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_UPDATE_QUERY],
            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return int(output.strip())
    if sys.platform.startswith("linux") and (apt := shutil.which("apt-get")):
        return apt_update_count(apt)
    return None


def os_update_report(value):
    if not isinstance(value, dict):
        return None
    state = value.get("state")
    if state not in ("checking", "current", "available", "unavailable", "unsupported"):
        return None
    count, checked = value.get("count"), value.get("checked")
    if state in ("current", "available"):
        if type(count) is not int or not 0 <= count <= 100000 or (state == "available") != bool(count):
            return None
        if type(checked) is not int or not 0 < checked <= 4102444800:
            return None
        return {"state": state, "count": count, "checked": checked}
    return {"state": state}


class OsUpdateCheck:
    def __init__(self):
        self.lock = threading.Lock()
        self.result = {"state": "checking"}
        self.next_check = 0.0
        self.running = False

    def report(self):
        with self.lock:
            if not self.running and time.monotonic() >= self.next_check:
                self.running = True
                self.result = {"state": "checking"}
                threading.Thread(target=self.run, name="verification-systeme", daemon=True).start()
            return self.result.copy()

    def run(self):
        try:
            count = count_os_updates()
            if count is None:
                result = {"state": "unsupported"}
            else:
                result = {"state": "available" if count else "current", "count": count, "checked": int(time.time())}
        except (OSError, ValueError, subprocess.SubprocessError):
            result = {"state": "unavailable"}
        with self.lock:
            self.result = result
            self.next_check = time.monotonic() + (3600 if result["state"] == "unavailable" else 12 * 3600)
            self.running = False

def desktop_services():
    """Ports RDP/VNC TCP IPv4 en écoute sur le lecteur Linux.

    Ne considère pas un service lié uniquement à 127.0.0.1, car il ne
    serait pas joignable depuis la régie. N'effectue aucune connexion ni
    modification du système.
    """
    if not sys.platform.startswith("linux"):
        return {}
    listening = set()
    try:
        with open("/proc/net/tcp", encoding="ascii") as entries:
            next(entries, None)
            for line in entries:
                fields = line.split()
                if len(fields) < 4 or fields[3] != "0A":
                    continue
                ip_hex, port_hex = fields[1].split(":", 1)
                port = int(port_hex, 16)
                if port not in (3389, 3390, 5900):
                    continue
                octets = bytes.fromhex(ip_hex)[::-1]
                address = socket.inet_ntoa(octets)
                if address.startswith("127.") or address.startswith("169.254."):
                    continue
                listening.add(port)
    except (OSError, ValueError, IndexError):
        return {}
    result = {}
    if 3389 in listening or 3390 in listening:
        result["rdp"] = 3389 if 3389 in listening else 3390
    if 5900 in listening:
        result["vnc"] = 5900
    return result


def desktop_protocol():
    if sys.platform == "win32":
        import winreg
        for path in (r"SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services",
                     r"SYSTEM\CurrentControlSet\Control\Terminal Server"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as settings:
                    deny, _ = winreg.QueryValueEx(settings, "fDenyTSConnections")
            except OSError:
                continue
            return "rdp" if deny == 0 else "vnc"
        return "vnc"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and (
            "gnome" in desktop or "ubuntu" in desktop):
        return "rdp"
    return "vnc"


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
    hostname = socket.gethostname()
    if sys.platform == "win32":
        return dict(hostname=hostname, os=f"Windows {platform.release()}", architecture=platform.machine(),
                    cpu=os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()), cores=os.cpu_count())
    cpu = {}
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            cpu.setdefault(key.strip(), value.strip())
    model_path = Path("/sys/firmware/devicetree/base/model")
    return dict(
        hostname=hostname,
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
                  "$ssids = @{}; "
                  "if ($env:SOUNDLIVE_WIFI_SSID -eq '1') { try { "
                  "$network = [Windows.Networking.Connectivity.NetworkInformation,Windows,ContentType=WindowsRuntime]; "
                  "foreach ($p in $network::GetConnectionProfiles()) { try { "
                  "if ($p.IsWlanConnectionProfile -and $p.NetworkAdapter) { "
                  "$ssid = $p.WlanConnectionProfileDetails.GetConnectedSsid(); "
                  "if ($ssid) { $ssids[$p.NetworkAdapter.NetworkAdapterId.ToString('D')] = $ssid } } "
                  "} catch {} } } catch {} }; "
                  "$rows = foreach ($a in [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) { "
                  "if ($a.NetworkInterfaceType -eq 'Loopback') { continue }; "
                  "$kind = switch ($a.NetworkInterfaceType) { 'Wireless80211' {'wifi'} "
                  "'Ethernet' {'ethernet'} 'GigabitEthernet' {'ethernet'} default {'other'} }; "
                  "$stats = $null; "
                  "if ($a.OperationalStatus -eq 'Up') { try { $stats = $a.GetIPStatistics() } catch {} }; "
                  "[pscustomobject]@{id=$a.Id; name=$a.Name; kind=$kind; connected=$a.OperationalStatus -eq 'Up'; "
                  "ssid=$(if ($kind -eq 'wifi' -and $a.OperationalStatus -eq 'Up') { $ssids[$a.Id.Trim('{}')] }); "
                  "tx_bytes=$stats.BytesSent; rx_bytes=$stats.BytesReceived; "
                  "addresses=@($a.GetIPProperties().UnicastAddresses | "
                  "Where-Object {$_.Address.AddressFamily -eq 'InterNetwork'} | "
                  "ForEach-Object {$_.Address.ToString()})} }; ConvertTo-Json -Compress -InputObject @($rows)")
        run = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, encoding="utf-8-sig", check=True, timeout=3,
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             env={**os.environ, "SOUNDLIVE_WIFI_SSID":"1" if server else "0"})
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
        row = dict(
            id=name, name=name, kind=kind, connected="LOWER_UP" in item.get("flags", []),
            to_regie=name == route_device,
            addresses=[addr["local"] for addr in item.get("addr_info", [])
                       if addr.get("family") == "inet"][:4],
        )
        if server and kind == "wifi" and row["connected"]:
            row["ssid"] = connected_ssid(name)
        interfaces.append(row)
    return sorted(interfaces, key=lambda item: (not item["to_regie"], not item["connected"], item["name"]))[:12]


def connected_ssid(interface):
    nmcli = shutil.which("nmcli")
    if nmcli:
        try:
            result = subprocess.run([nmcli, "-t", "-e", "no", "-f", "IN-USE,SSID",
                                     "device", "wifi", "list", "ifname", interface, "--rescan", "no"],
                                    capture_output=True, encoding="utf-8", errors="replace", check=True,
                                    timeout=1, env={**os.environ, "LC_ALL": "C"})
            for line in result.stdout.split("\n"):
                if line.startswith("*:") and line[2:]:
                    return line[2:]
        except (OSError, subprocess.SubprocessError):
            pass
    iw = shutil.which("iw") or next((path for path in ("/usr/sbin/iw", "/sbin/iw") if Path(path).is_file()), None)
    if iw:
        try:
            result = subprocess.run([iw, "dev", interface, "link"], capture_output=True, encoding="utf-8",
                                    errors="replace", check=True, timeout=1)
            for line in result.stdout.split("\n"):
                line = line.lstrip()
                if line.startswith("SSID: ") and line[6:]:
                    return line[6:]
        except (OSError, subprocess.SubprocessError):
            pass
    return None


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

    def sample_system(self, disk_path=None):
        data = {"desktop_protocol": desktop_protocol(), "desktop_services": desktop_services()}
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
                disk = shutil.disk_usage(disk_path or (Path.home().anchor if sys.platform == "win32" else "/"))
                self.disk = dict(disk_total=disk.total, disk_used=disk.used, disk_free=disk.free)
            except OSError:
                self.disk = {}
            self.refresh_at = time.monotonic() + 10
        return {**self.hardware, **self.disk, **data}

    def sample(self, server):
        data = self.sample_system()
        if time.monotonic() >= self.network_refresh_at:
            self.read_network(server)
            self.network_refresh_at = time.monotonic() + (0 if sys.platform == "win32" else 4)
        return {**data, "interfaces": self.rates.sample(self.network)}

    def read_network(self, server):
        try:
            self.network = network_info(server)
        except (OSError, KeyError, ValueError, subprocess.SubprocessError):
            self.network = None
