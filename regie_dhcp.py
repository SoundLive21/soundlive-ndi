"""Serveur DHCP IPv4 de la régie sur l'interface dédiée aux écrans."""
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
from threading import Event, Lock, Thread
from time import monotonic, time


PRIVATE = tuple(map(IPv4Network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
HEADER = struct.Struct("!BBBBIHH4s4s4s4s16s64s128s")
COOKIE = b"\x63\x82\x53\x63"
ZERO = bytes(4)
DISCOVER, OFFER, REQUEST, DECLINE, ACK, NAK, RELEASE, INFORM = range(1, 9)
# Winsock: receive only on the interface list and set the outgoing interface.
IP_IFLIST, IP_ADD_IFLIST, IP_UNICAST_IF = 28, 29, 31


def save_json(path, data):
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def candidates(rows):
    try:
        # Select the effective Internet route without transmitting anything.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            uplink = probe.getsockname()[0]
    except OSError:
        uplink = None
    prepared = [(row, IPv4Interface(f"{row['ip']}/{row['prefix']}")) for row in rows]
    uplink_ids = {row["id"] for row, address in prepared if str(address.ip) == uplink}
    result = []
    for row, address in prepared:
        network = address.network
        if (not row["static"] or row["id"] in uplink_ids or network.prefixlen > 30
                or address.ip in (network.network_address, network.broadcast_address)
                or not any(network.subnet_of(private) for private in PRIVATE)):
            continue
        if any(other["id"] != row["id"] and network.overlaps(value.network) for other, value in prepared):
            continue
        result.append({**row, "network": str(network)})
    return result


@dataclass(frozen=True)
class Settings:
    adapter: dict
    network: IPv4Network
    first: IPv4Address
    last: IPv4Address
    router: IPv4Address | None
    dns: tuple
    minutes: int

    @property
    def server(self):
        return IPv4Address(self.adapter["ip"])

    def contains(self, address):
        return self.first <= address <= self.last

    def fields(self):
        return dict(adapter=self.adapter["id"], ip=str(self.server), first=str(self.first),
                    last=str(self.last), router=str(self.router) if self.router else "",
                    dns=[str(address) for address in self.dns], minutes=self.minutes)

    @classmethod
    def parse(cls, data, adapters):
        match = [row for row in adapters if row["id"] == data.get("adapter") and row["ip"] == data.get("ip")]
        if len(match) != 1:
            raise ValueError("Sélectionne une interface active à IPv4 fixe sur le réseau des écrans.")
        adapter = match[0]
        network = IPv4Network(adapter["network"])
        server = IPv4Address(adapter["ip"])
        try:
            first = IPv4Address(data.get("first", ""))
            last = IPv4Address(data.get("last", ""))
            router = IPv4Address(data["router"]) if data.get("router") else None
            raw_dns = data.get("dns", [])
            if not isinstance(raw_dns, list) or len(raw_dns) > 2:
                raise ValueError
            dns = tuple(IPv4Address(value) for value in raw_dns)
        except (ValueError, TypeError) as error:
            raise ValueError("Indique une plage, une passerelle et des DNS IPv4 valides.") from error
        if not network.network_address < first <= last < network.broadcast_address or first <= server <= last:
            raise ValueError("La plage doit rester dans le sous-réseau et exclure l’IP de la régie.")
        if any(first <= IPv4Address(row["ip"]) <= last for row in adapters):
            raise ValueError("La plage recouvre une adresse fixe configurée sur la régie.")
        if router and (not network.network_address < router < network.broadcast_address or first <= router <= last):
            raise ValueError("La passerelle doit appartenir au sous-réseau et rester hors de la plage DHCP.")
        if any(address.is_unspecified or address.is_multicast or address.is_loopback for address in dns):
            raise ValueError("Indique des serveurs DNS IPv4 joignables par les lecteurs.")
        minutes = data.get("minutes")
        if type(minutes) is not int or not 1 <= minutes <= 10080:
            raise ValueError("La durée du bail doit être comprise entre 1 et 10 080 minutes.")
        return cls(adapter, network, first, last, router, dns, minutes)


@dataclass(frozen=True)
class Packet:
    header: tuple
    options: dict

    @classmethod
    def parse(cls, data):
        if data[HEADER.size:HEADER.size + 4] != COOKIE:
            return None
        header = HEADER.unpack_from(data)
        if header[:3] != (1, 1, 6) or header[10] != ZERO:
            return None
        options = {}
        idx = HEADER.size + 4
        while idx < len(data):
            tag = data[idx]
            idx += 1
            if tag == 0:
                continue
            if tag == 255:
                break
            if idx >= len(data):
                return None
            size = data[idx]
            idx += 1
            if idx + size > len(data):
                return None
            if tag in (12, 50, 53, 54, 61):
                if tag in options:
                    return None
                options[tag] = data[idx:idx + size]
            idx += size
        if len(options.get(53, b"")) != 1 or any(tag in options and len(options[tag]) != 4 for tag in (50, 54)):
            return None
        return cls(header, options)

    @property
    def mac(self):
        return self.header[11][:6].hex(":")

    @property
    def identity(self):
        identifier = self.options.get(61, b"")
        return "id:" + identifier.hex() if len(identifier) > 1 else self.mac

    @property
    def address(self):
        return IPv4Address(self.header[7])

    def reply(self, kind, settings, address=None):
        _, hardware, size, _, xid, _, flags, current, _, _, _, mac, _, _ = self.header
        options = [(53, bytes([kind])), (54, settings.server.packed)]
        if kind != NAK:
            options.extend(((1, settings.network.netmask.packed),
                            (28, settings.network.broadcast_address.packed)))
            if settings.router:
                options.append((3, settings.router.packed))
            if settings.dns:
                options.append((6, b"".join(item.packed for item in settings.dns)))
            if address is not None:
                options.append((51, struct.pack("!I", settings.minutes * 60)))
        if 61 in self.options:
            options.append((61, self.options[61]))
        data = HEADER.pack(2, hardware, size, 0, xid, 0, flags, current, address.packed if address else ZERO,
                           ZERO, ZERO, mac, bytes(64), bytes(128))
        encoded = b"".join(bytes((tag, len(value))) + value for tag, value in options)
        return (data + COOKIE + encoded + b"\xff").ljust(300, b"\0")


class Leases:
    def __init__(self, settings, path):
        self.settings = settings
        self.path = path
        self.entries = {}
        self.declined = {}
        self.offers = {}
        if path.is_file():
            old = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(old, dict) or not isinstance(old.get("entries"), dict):
                raise ValueError("Le fichier des baux DHCP est invalide.")
            if old.get("network") == str(settings.network):
                self.entries = old["entries"]
                self.declined = old.get("declined", {})

    def save(self):
        save_json(self.path, dict(network=str(self.settings.network), entries=self.entries, declined=self.declined))

    def prune(self, now):
        self.entries = {key: row for key, row in self.entries.items() if row["expires"] > now}
        self.declined = {key: expiry for key, expiry in self.declined.items() if expiry > now}
        self.offers = {key: pair for key, pair in self.offers.items() if pair[1] > now}

    def handle(self, packet, now):
        self.prune(now)
        settings = self.settings
        key = packet.identity
        kind = packet.options[53][0]
        chosen = packet.options.get(54)
        if chosen and chosen != settings.server.packed:
            self.offers.pop(key, None)
            return None
        lease = self.entries.get(key, {}).get("ip")
        offer = self.offers.get(key, (None, 0))[0]
        requested = str(IPv4Address(packet.options[50])) if 50 in packet.options else None
        if kind == DISCOVER:
            if lease and settings.contains(IPv4Address(lease)):
                selected = IPv4Address(lease)
            elif offer:
                selected = IPv4Address(offer)
            else:
                occupied = {row["ip"] for row in self.entries.values()} | set(self.declined)
                occupied.update(pair[0] for pair in self.offers.values())
                selected = next((address for value in range(int(settings.first), int(settings.last) + 1)
                                 if str(address := IPv4Address(value)) not in occupied), None)
            if selected is not None:
                self.offers[key] = (str(selected), now + 60)
                return OFFER, selected
        elif kind == REQUEST:
            address = IPv4Address(requested) if requested else packet.address
            known = str(address) == lease or (chosen is not None and str(address) == offer)
            if known and settings.contains(address):
                hostname = packet.options.get(12, b"")[:63].decode("ascii", errors="replace")
                self.entries[key] = dict(ip=str(address), mac=packet.mac, hostname=hostname,
                                         expires=now + settings.minutes * 60)
                self.offers.pop(key, None)
                self.save()
                return ACK, address
            if chosen is not None or lease:
                return NAK, None
        elif kind == RELEASE and lease == str(packet.address):
            self.entries.pop(key)
            self.offers.pop(key, None)
            self.save()
        elif kind == DECLINE and chosen and requested and requested in (lease, offer):
            self.entries.pop(key, None)
            self.offers.pop(key, None)
            self.declined[requested] = now + 600
            self.save()
        elif kind == INFORM and settings.network.network_address < packet.address < settings.network.broadcast_address:
            return ACK, None
        return None


def bind_listener(interface, index=None):
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.setsockopt(socket.IPPROTO_IP, IP_IFLIST, 1)
            listener.setsockopt(socket.IPPROTO_IP, IP_ADD_IFLIST, index)
            listener.setsockopt(socket.IPPROTO_IP, IP_UNICAST_IF, struct.pack("!I", index))
        elif sys.platform.startswith("linux"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                                interface.encode() + b"\0")
        else:
            raise ValueError("Le serveur DHCP intégré est disponible sous Windows et Linux.")
        listener.bind(("", 67))
        listener.settimeout(0.25)
    except BaseException:
        listener.close()
        raise
    return listener


def open_listener_elevated(interface):
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise RuntimeError("Installe pkexec pour autoriser l’ouverture du port DHCP sous Ubuntu.")
    command = [pkexec, "--disable-internal-agent", "/usr/bin/python3", "-I",
               str(Path(__file__).resolve()), "--ouvrir-socket", interface]
    # Only the bound socket is returned. Configuration and leases stay in the launcher process.
    incoming, outgoing = socket.socketpair()
    with incoming, outgoing:
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=outgoing, stderr=subprocess.PIPE,
                                    text=True, errors="replace", timeout=90)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("La demande d’autorisation DHCP a expiré.") from error
        outgoing.close()
        if result.returncode == 126:
            raise RuntimeError("La demande d’autorisation DHCP a été annulée.")
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "L’autorisation d’ouverture du port DHCP a échoué.")
        incoming.settimeout(2)
        message, descriptors, _, _ = socket.recv_fds(incoming, 1, 1)
        try:
            if message != b"D" or len(descriptors) != 1:
                raise RuntimeError("Le système n’a pas transmis le socket DHCP.")
            listener = socket.fromfd(descriptors[0], socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
    listener.settimeout(0.25)
    return listener


def open_listener(settings):
    interface = settings.adapter["label"]
    try:
        return bind_listener(interface, settings.adapter["index"])
    except PermissionError:
        if not sys.platform.startswith("linux") or os.geteuid() == 0:
            raise
    return open_listener_elevated(interface)


def windows_dhcp_port_owner():
    """Décrit l'occupation du port UDP 67 sans toucher aux services réseau."""
    if sys.platform != "win32":
        return ""
    command = (
        "$endpoints = @(Get-NetUDPEndpoint -LocalPort 67 -ErrorAction SilentlyContinue); "
        "foreach ($endpoint in $endpoints) { "
        "$process = Get-Process -Id $endpoint.OwningProcess -ErrorAction SilentlyContinue; "
        "$name = if ($process) { $process.ProcessName } else { 'inconnu' }; "
        "$services = @(Get-CimInstance Win32_Service "
        "-Filter \"ProcessId = $($endpoint.OwningProcess)\" -ErrorAction SilentlyContinue "
        "| Select-Object -ExpandProperty Name); "
        "'{0} (PID {1}, {2}) ; services : {3}' -f "
        "$name, $endpoint.OwningProcess, $endpoint.LocalAddress, ($services -join ', ') "
        "}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, errors="replace", timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode:
        return ""
    return " ; ".join(line.strip() for line in result.stdout.splitlines() if line.strip())[:700]


class Service:
    def __init__(self, path, interfaces):
        self.path = path
        self.interfaces = interfaces
        self.lock = Lock()
        self.leases = None
        self.listener = None
        self.worker = None
        self.stopped = Event()
        self.error = ""
        try:
            self.config = json.loads((path / "configuration.json").read_text(encoding="utf-8"))
            if not isinstance(self.config, dict):
                raise ValueError
        except FileNotFoundError:
            self.config = {}
        except (OSError, ValueError):
            self.config = {}
            self.error = "Impossible de lire la configuration DHCP enregistrée."

    def start(self, data, listener_factory=None):
        with self.lock:
            if self.listener:
                raise ValueError("Arrête le serveur DHCP avant de modifier ses paramètres.")
            settings = Settings.parse(data, candidates(self.interfaces()))
            leases = Leases(settings, self.path / "baux.json")
            try:
                listener = (listener_factory or open_listener)(settings)
            except PermissionError as error:
                raise ValueError("Accès au port DHCP refusé. Vérifie les droits UDP 67 de la régie.") from error
            except OSError as error:
                if sys.platform == "win32" and getattr(error, "winerror", None) == 10048:
                    owner = windows_dhcp_port_owner()
                    details = ("Processus utilisant le port : " + owner if owner else
                               "Aucun propriétaire détecté automatiquement.")
                    raise ValueError(
                        "Le port DHCP UDP 67 est déjà occupé sous Windows. " + details +
                        " Vérifie l'autre service DHCP (SharedAccess, HNS, VirtualBox, etc.) "
                        "avant de réessayer ; ne désactive pas le partage Internet à l'aveugle."
                    ) from error
                raise ValueError(f"Impossible d’ouvrir DHCP UDP 67. Vérifie qu’il est libre et que l’interface est active. {error}") from error
            config = {**settings.fields(), "enabled": True}
            try:
                save_json(self.path / "configuration.json", config)
            except BaseException:
                listener.close()
                raise
            self.config, self.leases, self.listener = config, leases, listener
            self.error = ""
            self.stopped.clear()
            self.worker = Thread(target=self.serve, args=(listener, leases), daemon=True)
            self.worker.start()

    def restore(self):
        if self.config.get("enabled"):
            try:
                self.start(self.config)
            except (OSError, RuntimeError, ValueError) as error:
                self.error = str(error)

    def serve(self, listener, leases):
        settings = leases.settings
        broadcast = str(settings.network.broadcast_address) if sys.platform == "win32" else "255.255.255.255"
        check_at = monotonic() + 5
        try:
            while not self.stopped.is_set():
                if monotonic() >= check_at:
                    if settings.adapter not in candidates(self.interfaces()):
                        raise ValueError("Interface DHCP modifiée ou déconnectée. Redémarre le serveur après vérification.")
                    check_at = monotonic() + 5
                try:
                    data, peer = listener.recvfrom(4096)
                except (TimeoutError, ConnectionResetError):
                    continue
                packet = Packet.parse(data)
                if peer[1] != 68 or packet is None:
                    continue
                with self.lock:
                    result = leases.handle(packet, int(time()))
                if result is not None and not self.stopped.is_set():
                    kind, assigned = result
                    destination = (str(packet.address) if kind == ACK and str(packet.address) == peer[0]
                                   and packet.address in settings.network
                                   and packet.address not in (settings.network.network_address, settings.network.broadcast_address)
                                   and not packet.header[6] & 0x8000 else broadcast)
                    listener.sendto(packet.reply(kind, settings, assigned), (destination, 68))
        except (OSError, RuntimeError, ValueError) as error:
            with self.lock:
                self.error = str(error)
        finally:
            listener.close()
            with self.lock:
                if self.listener is listener:
                    self.listener = None

    def stop(self, disable=True):
        failure = None
        with self.lock:
            if disable and self.config:
                config = {**self.config, "enabled": False}
                try:
                    save_json(self.path / "configuration.json", config)
                except OSError as error:
                    failure = error
                self.config = config
            self.stopped.set()
            worker = self.worker
        if worker is not None:
            worker.join()
        if failure:
            raise OSError("Serveur DHCP arrêté. Impossible d’enregistrer son arrêt pour le prochain démarrage.") from failure

    def status(self, include_interfaces=False):
        with self.lock:
            now = int(time())
            rows = [] if self.leases is None else [row for row in self.leases.entries.values() if row["expires"] > now]
            result = dict(running=self.listener is not None, error=self.error, config=dict(self.config),
                          leases=sorted(rows, key=lambda row: IPv4Address(row["ip"])))
        if include_interfaces:
            result["interfaces"] = candidates(self.interfaces())
        return result


if __name__ == "__main__":
    if not sys.platform.startswith("linux") or len(sys.argv) != 3 or sys.argv[1] != "--ouvrir-socket":
        sys.exit("Ce module DHCP est utilisé par le lanceur SoundLive-NDI.")
    interface = sys.argv[2]
    try:
        socket.if_nametoindex(interface)
        with socket.socket(fileno=sys.stdout.fileno()) as channel, bind_listener(interface) as listener:
            socket.send_fds(channel, [b"D"], [listener.fileno()])
    except OSError as error:
        sys.exit(f"Impossible d’ouvrir DHCP UDP 67 sur {interface}. {error}")
