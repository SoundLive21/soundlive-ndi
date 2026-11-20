"""Autorisation locale des opérations réseau demandées par le lanceur ou la console."""
from base64 import b64encode
from hmac import compare_digest
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
from runpy import run_path
import secrets
import shutil
import socket
import struct
import subprocess
import sys
from threading import Lock, Thread
from time import monotonic


ROOT = Path(__file__).resolve().parent
INTERNET_OPERATIONS = frozenset(("activer", "desactiver", "diagnostic"))
LIMIT = 65536


def send(channel, value):
    data = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if len(data) > LIMIT:
        raise ValueError("Rapport réseau trop volumineux.")
    channel.sendall(struct.pack("!I", len(data)) + data)


def receive(channel):
    def read(size):
        data = bytearray()
        while len(data) < size:
            chunk = channel.recv(size - len(data))
            if not chunk:
                raise ConnectionError("La liaison avec l’assistant réseau a été fermée.")
            data.extend(chunk)
        return data

    size, = struct.unpack("!I", read(4))
    if not 0 < size <= LIMIT:
        raise ValueError("Message réseau invalide.")
    value = json.loads(read(size))
    if not isinstance(value, dict):
        raise ValueError("Message réseau invalide.")
    return value


def admin_internet(source, operation, gateway):
    if operation not in INTERNET_OPERATIONS:
        raise ValueError("Action de partage Internet inconnue.")
    address = str(IPv4Address(gateway)) if gateway else None
    if (operation == "activer" and not address) or (operation == "desactiver" and address):
        raise ValueError("Adresse IPv4 du réseau écrans invalide pour cette action.")
    options = {"input": source, "encoding": "utf-8"}
    if sys.platform == "win32":
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        run = ("[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
               "$code = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())); "
               "$parameters = @{Action=$env:SOUNDLIVE_NETWORK_ACTION}; "
               "if ($env:SOUNDLIVE_NETWORK_IPV4) {$parameters.Gateway=$env:SOUNDLIVE_NETWORK_IPV4}; "
               "$LASTEXITCODE=0; & ([ScriptBlock]::Create($code)) @parameters; exit $LASTEXITCODE")
        command = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", run]
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
        options["input"] = b64encode(source.encode("utf-8")).decode("ascii")
        options["env"] = {**os.environ, "SOUNDLIVE_NETWORK_ACTION": operation, "SOUNDLIVE_NETWORK_IPV4": address or ""}
    else:
        run = "import sys; exec(compile(sys.stdin.buffer.read(), 'regie-internet.py', 'exec'))"
        command = ["/usr/bin/python3", "-I", "-c", run, operation]
        command.extend([address] if address else [])
    result = subprocess.run(command, capture_output=True, errors="replace", timeout=55, **options)
    report = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())[-12000:]
    if result.returncode:
        raise RuntimeError(report or f"Échec du partage Internet (code {result.returncode}).")
    return report if operation == "diagnostic" else ""


def serve(channel, token=None):
    # Keep the approved implementation in memory for the lifetime of this authorization.
    if sys.platform == "win32":
        internet = (ROOT / "regie-internet-windows.ps1").read_text(encoding="utf-8-sig")
        bind = None
    else:
        internet = (ROOT / "regie-internet.py").read_text(encoding="utf-8")
        bind = run_path(str(ROOT / "regie_dhcp.py"))["bind_listener"]
    send(channel, {"ready": True, "token": token})
    while True:
        try:
            request = receive(channel)
        except ConnectionError:
            return
        try:
            if request.get("kind") == "internet":
                report = admin_internet(internet, request.get("operation"), request.get("gateway"))
                send(channel, {"ok": True, "report": report})
            elif request.get("kind") == "dhcp" and sys.platform.startswith("linux"):
                interface = request.get("interface")
                if not isinstance(interface, str) or not 0 < len(interface.encode()) < 16 or "\0" in interface:
                    raise ValueError("Interface DHCP invalide.")
                socket.if_nametoindex(interface)
                with bind(interface) as listener:
                    send(channel, {"ok": True})
                    socket.send_fds(channel, [b"D"], [listener.fileno()])
            else:
                raise ValueError("Action réseau inconnue.")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            send(channel, {"ok": False, "error": str(error)})


class NetworkAuthorization:
    def __init__(self):
        self.channel = None
        self.lock = Lock()
        self.closed = False

    @property
    def ready(self):
        return self.channel is not None

    def open(self):
        if sys.platform == "win32":
            channel = self.open_windows()
        else:
            channel = self.open_linux()
        with self.lock:
            if self.closed or self.ready:
                channel.close()
            else:
                self.channel = channel

    def open_windows(self):
        import ctypes

        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(3)
            token = secrets.token_hex(32)
            args = ["-I", str(Path(__file__).resolve()), "--windows", str(server.getsockname()[1]), token]
            launch = ctypes.windll.shell32.ShellExecuteW
            launch.argtypes = [ctypes.c_void_p, *([ctypes.c_wchar_p] * 4), ctypes.c_int]
            launch.restype = ctypes.c_void_p
            result = launch(None, "runas", sys.executable, subprocess.list2cmdline(args), str(ROOT), 0)
            if not result or result <= 32:
                raise RuntimeError("Windows n’a pas accordé l’autorisation réseau.")
            deadline = monotonic() + 90
            while monotonic() < deadline:
                server.settimeout(max(0.1, deadline - monotonic()))
                channel, _ = server.accept()
                channel.settimeout(3)
                try:
                    response = receive(channel)
                    provided = response.get("token")
                    if (response.get("ready") is True and isinstance(provided, str)
                            and provided.isascii() and compare_digest(provided, token)):
                        return channel
                except (OSError, ValueError):
                    pass
                channel.close()
        raise RuntimeError("L’assistant réseau Windows n’a pas répondu.")

    def open_linux(self):
        command = ["/usr/bin/python3", "-I", str(Path(__file__).resolve()), "--linux"]
        if os.geteuid() != 0:
            pkexec = shutil.which("pkexec")
            if not pkexec:
                raise RuntimeError("Installe le paquet pkexec pour autoriser la gestion réseau à distance.")
            command[:0] = [pkexec, "--disable-internal-agent"]
        channel, child = socket.socketpair()
        try:
            with child:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=child, stderr=subprocess.DEVNULL)
            Thread(target=process.wait, daemon=True).start()
            channel.settimeout(90)
            if receive(channel).get("ready") is not True:
                raise RuntimeError("L’assistant réseau Ubuntu n’a pas répondu.")
        except (OSError, ValueError, RuntimeError) as error:
            channel.close()
            raise RuntimeError("L’autorisation réseau Ubuntu a échoué ou a été annulée.") from error
        return channel

    def request(self, kind, **values):
        with self.lock:
            channel = self.channel
            if channel is None:
                raise RuntimeError("Autorise la gestion réseau à distance dans le lanceur de la régie.")
            try:
                channel.settimeout(60)
                send(channel, {"kind": kind, **values})
                result = receive(channel)
                if result.get("ok") and kind == "dhcp":
                    message, descriptors, _, _ = socket.recv_fds(channel, 1, 1)
                    try:
                        if message != b"D" or len(descriptors) != 1:
                            raise ValueError("Socket DHCP absent de la réponse.")
                        listener = socket.fromfd(descriptors[0], socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                    finally:
                        for descriptor in descriptors:
                            os.close(descriptor)
                    listener.settimeout(0.25)
                    return listener
            except (OSError, ValueError) as error:
                channel.close()
                self.channel = None
                raise RuntimeError("L’assistant réseau ne répond plus. Vérifie l’état avant de relancer l’action.") from error
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "Action réseau impossible.")
            return result.get("report", "")

    def revoke(self, *, closing=False):
        with self.lock:
            if closing:
                self.closed = True
            if self.channel is not None:
                self.channel.close()
                self.channel = None


if __name__ == "__main__":
    if sys.argv[1:] == ["--linux"] and sys.platform.startswith("linux") and os.geteuid() == 0:
        with socket.socket(fileno=sys.stdout.fileno()) as channel:
            serve(channel)
    elif len(sys.argv) == 4 and sys.argv[1] == "--windows" and sys.platform == "win32":
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            sys.exit(1)
        with socket.create_connection(("127.0.0.1", int(sys.argv[2])), timeout=5) as channel:
            channel.settimeout(None)
            serve(channel, sys.argv[3])
    else:
        sys.exit(1)
