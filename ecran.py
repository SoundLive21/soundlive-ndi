#!/usr/bin/python3
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
import configparser
from concurrent.futures import ThreadPoolExecutor
from base64 import b64encode
import json
import os
import secrets
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from commun import APP_NAME, COPYRIGHT, DEFAULT_SOURCE, PREVIEW_LIMIT, SOFTWARE_VERSION, client_lock, options, release_number
from diagnostic import Diagnostic
from mise_a_jour import ClientUpdate, LIMIT, RESTART, contents, digest

HOME = Path.home()
WINDOWS = sys.platform == "win32"
WINDOWS_DATA = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local")) / "SoundLive"
CONFIG = WINDOWS_DATA / "config" if WINDOWS else Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "soundlive-ndi"
STATE = WINDOWS_DATA / "state" if WINDOWS else Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "soundlive-ndi"
PAIRING = CONFIG / "association.json"
LAST_SETTINGS = CONFIG / "lecture.json"
WAITING_SETTINGS = CONFIG / "attente.json"
NDI = Path("/opt/ndi-pi")
UBUNTU = os.environ.get("SOUNDLIVE_PLAYER") == "ubuntu"
CLIENT_DIR = Path(__file__).resolve().parent
APP = Path(os.environ.get("SOUNDLIVE_APP", CLIENT_DIR))
DEVICE_KIND = "windows" if WINDOWS else os.environ.get("SOUNDLIVE_DEVICE_KIND", "ubuntu" if UBUNTU else "pi")
PLAYER = str(APP / "lecteur-ndi-windows.exe" if WINDOWS else
             APP / "lecteur-ndi-ubuntu" if UBUNTU else NDI / "bin/ffplay")
READY = STATE / "video.prete"
BOOT = "windows-" + secrets.token_hex(16) if WINDOWS else Path("/proc/sys/kernel/random/boot_id").read_text().strip()
REGIE_UNPAIRED = ("Lecteur non associé à la régie", "#ffacb8")
REGIE_CHECKING = ("Vérification du rattachement…", "#dfcbd1")
REGIE_CONNECTED = ("Lecteur rattaché à la régie", "#95e6b3")
REGIE_UNAVAILABLE = ("Régie indisponible · association enregistrée", "#ffc78a")
REGIE_REPAIR = ("Réassociation à la régie nécessaire", "#ffacb8")

def serial():
    if WINDOWS:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return "win-" + str(winreg.QueryValueEx(key, "MachineGuid")[0])
    if DEVICE_KIND == "ubuntu":
        return "pc-" + Path("/etc/machine-id").read_text().strip()
    try:
        return Path("/proc/device-tree/serial-number").read_bytes().rstrip(b"\0").decode()
    except FileNotFoundError:
        return socket.gethostname()

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    temp.replace(path)

def initial_settings():
    cached = read_json(LAST_SETTINGS)
    if cached:
        return options(cached)
    ini = configparser.ConfigParser(interpolation=None)
    ini.read(CONFIG / "ecran.ini", encoding="utf-8")
    return options(dict(source=ini.get("ndi", "source", fallback=DEFAULT_SOURCE),
                        bandwidth=ini.get("ndi", "bandwidth", fallback="0" if UBUNTU else "1"),
                        volume=100, muted=False, enabled=True))


def waiting_bitmap(matrix):
    if not isinstance(matrix, list) or len(matrix) not in range(21, 58, 4):
        return None
    count = len(matrix)
    if any(not isinstance(row, str) or len(row) != count or set(row) - {"0", "1"}
           for row in matrix):
        return None
    # Integer scaling and four light modules around the symbol keep its edges sharp.
    scale = max(1, 220 // (count + 8))
    side = (count + 8) * scale
    light = b"\xff" * (3 * scale)
    dark = b"\x00" * (3 * scale)
    empty = b"\xff" * (side * 3 * 4 * scale)
    rows = [(light * 4 + b"".join(dark if cell == "1" else light for cell in row)
             + light * 4) * scale for row in matrix]
    return side, empty + b"".join(rows) + empty


def capture_ndi_preview(source_name):
    import ctypes as c
    import struct
    import zlib

    class Source(c.Structure):
        _fields_ = [("name", c.c_char_p), ("address", c.c_char_p)]

    class Receiver(c.Structure):
        _fields_ = [("source", Source), ("color", c.c_int), ("bandwidth", c.c_int),
                    ("fields", c.c_bool), ("name", c.c_char_p)]

    class Frame(c.Structure):
        _fields_ = [("width", c.c_int), ("height", c.c_int), ("fourcc", c.c_int),
                    ("rate_n", c.c_int), ("rate_d", c.c_int), ("aspect", c.c_float),
                    ("format", c.c_int), ("timecode", c.c_int64), ("data", c.c_void_p),
                    ("stride", c.c_int), ("metadata", c.c_char_p), ("timestamp", c.c_int64)]

    library = (APP / "Processing.NDI.Lib.x64.dll" if WINDOWS else
               (APP if UBUNTU else NDI) / "lib/libndi.so.6")
    ndi = c.CDLL(str(library))
    for name, returns, args in (
        ("initialize", c.c_bool, ()),
        ("destroy", None, ()),
        ("find_create_v2", c.c_void_p, (c.c_void_p,)),
        ("find_destroy", None, (c.c_void_p,)),
        ("find_wait_for_sources", c.c_bool, (c.c_void_p, c.c_uint32)),
        ("find_get_current_sources", c.POINTER(Source), (c.c_void_p, c.POINTER(c.c_uint32))),
        ("recv_create_v3", c.c_void_p, (c.POINTER(Receiver),)),
        ("recv_destroy", None, (c.c_void_p,)),
        ("recv_capture_v2", c.c_int, (c.c_void_p, c.POINTER(Frame), c.c_void_p, c.c_void_p, c.c_uint32)),
        ("recv_free_video_v2", None, (c.c_void_p, c.POINTER(Frame))),
    ):
        function = getattr(ndi, "NDIlib_" + name)
        function.restype, function.argtypes = returns, args

    if not ndi.NDIlib_initialize():
        raise RuntimeError("Le runtime NDI est indisponible.")
    finder = receiver = None
    try:
        finder = ndi.NDIlib_find_create_v2(None)
        if not finder:
            raise RuntimeError("La découverte NDI est indisponible.")
        target = source_name.encode("utf-8")
        expires = time.monotonic() + 3.3
        while time.monotonic() < expires:
            count = c.c_uint32()
            sources = ndi.NDIlib_find_get_current_sources(finder, c.byref(count))
            candidate = next((sources[i] for i in range(count.value) if sources[i].name == target), None)
            if candidate is not None:
                # Valeurs SDK pour BGRX/BGRA et le mode NDI à bande passante réduite.
                receiver = ndi.NDIlib_recv_create_v3(c.byref(Receiver(candidate, 0, 0, False, b"SoundLive-NDI Preview")))
                break
            ndi.NDIlib_find_wait_for_sources(finder, 180)
        if not receiver:
            raise RuntimeError("Source NDI introuvable sur ce lecteur.")
        expires = time.monotonic() + 3.5
        while time.monotonic() < expires:
            frame = Frame()
            if ndi.NDIlib_recv_capture_v2(receiver, c.byref(frame), None, None, 200) != 1:
                continue
            try:
                formats = (int.from_bytes(b"BGRX", "little"), int.from_bytes(b"BGRA", "little"))
                if not frame.data or frame.fourcc not in formats or not (1 <= frame.width <= 16384 and 1 <= frame.height <= 16384):
                    continue
                stride = frame.stride or frame.width * 4
                if not frame.width * 4 <= stride <= 131072:
                    continue
                aspect = frame.aspect
                if not .1 <= aspect <= 10:
                    aspect = frame.width / frame.height
                width, height = max(1, min(240, int(135 * aspect))), max(1, min(135, int(240 / aspect)))
                scanlines = bytearray()
                columns = [4 * (x * frame.width // width) for x in range(width)]
                for y in range(height):
                    line = c.string_at(frame.data + (y * frame.height // height) * stride, frame.width * 4)
                    scanlines.append(0)
                    for x in columns:
                        scanlines.extend((line[x + 2], line[x + 1], line[x]))

                def chunk(kind, payload):
                    return (struct.pack(">I", len(payload)) + kind + payload
                            + struct.pack(">I", zlib.crc32(kind + payload)))

                return (b"\x89PNG\r\n\x1a\n"
                        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                        + chunk(b"IDAT", zlib.compress(scanlines, 1)) + chunk(b"IEND", b""))
            finally:
                ndi.NDIlib_recv_free_video_v2(receiver, c.byref(frame))
        raise RuntimeError("Aucune image reçue sur ce flux.")
    finally:
        if receiver:
            ndi.NDIlib_recv_destroy(receiver)
        if finder:
            ndi.NDIlib_find_destroy(finder)
        ndi.NDIlib_destroy()


def waiting_update_message(recommendation):
    if not isinstance(recommendation, dict):
        return ""
    version = recommendation.get("version")
    state = recommendation.get("state")
    offered = release_number(version)
    installed = release_number(SOFTWARE_VERSION)
    if not offered or offered < installed:
        return ""
    if state == "available":
        return f"Mise à jour {version} disponible depuis la régie"
    if state in ("unsupported", "key_required"):
        return f"Mise à jour {version} disponible · intervention sur la régie"
    return ""


def waiting_group(details):
    group = details.get("group")
    if isinstance(group, str) and 0 < len(group) <= 80 and group.strip() and group.isprintable():
        return f"Groupe · {group.strip()}"
    return ""


def waiting_key(identity):
    return [identity["server"], identity["id"]]


def restore_waiting_details(identity, log):
    if identity:
        try:
            saved = read_json(WAITING_SETTINGS)
            if isinstance(saved, dict) and saved.get("screen") == waiting_key(identity):
                return saved.get("waiting")
        except (ValueError, OSError) as error:
            print(f"Détails de l’écran d’attente : {error}", file=log)
    return None


def save_waiting_details(identity, details, log):
    try:
        write_json(WAITING_SETTINGS, dict(screen=waiting_key(identity), waiting=details))
    except OSError as error:
        print(f"Détails de l’écran d’attente : {error}", file=log)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class RegieResponseError(RuntimeError):
    def __init__(self, status, message, code=None):
        super().__init__(f"HTTP {status} : {message}")
        self.reassociation_required = (
            (status, code) in ((403, "association_invalid"), (404, "screen_unknown"))
            or (status, message) in ((403, "Réassocie le récepteur depuis le tableau."), (404, "Écran inconnu."))
        )


def regie_unavailable(error):
    if isinstance(error, RegieResponseError) and error.reassociation_required:
        return REGIE_REPAIR
    return REGIE_UNAVAILABLE


def post(server, path, data, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(server + path, json.dumps(data).encode(), headers, method="POST")
    try:
        if path == "/api/client-package":
            with build_opener(NoRedirect).open(request, timeout=8) as response:
                body = response.read(2 * LIMIT + 1)
                if len(body) > 2 * LIMIT:
                    raise ValueError("Paquet de mise à jour trop volumineux.")
                return json.loads(body)
        with urlopen(request, timeout=3) as response:
            return json.load(response)
    except HTTPError as error:
        code = None
        with error:
            try:
                details = json.load(error)
                message = details.get("error", error.reason)
                code = details.get("code")
            except (AttributeError, ValueError):
                message = error.reason
        raise RegieResponseError(error.code, message, code) from error

def server_address(server):
    url = urlsplit(server)
    if (url.scheme != "http" or not url.hostname or url.port == 0
            or url.path not in ("", "/") or url.query or url.fragment or url.username is not None):
        raise ValueError("Utilise http://ADRESSE_DU_PC:8765.")
    return server.rstrip("/")

def association():
    identity = read_json(PAIRING)
    if not isinstance(identity, dict) or any(
        not isinstance(identity.get(key), str) or not identity[key]
        for key in ("server", "serial", "id", "token")
    ):
        raise ValueError("Association absente ou invalide pour ce compte.")
    if identity["serial"] != serial():
        raise ValueError("Cette association appartient à un autre appareil. Utilise un code d’association propre à cet écran.")
    return identity

def check_connection(address=None):
    identity = association()
    server = server_address(address if address is not None else identity["server"])
    try:
        reply = post(server, "/api/poll", dict(id=identity["id"], boot=BOOT,
                     state="Démarrage", check=True), identity["token"])
        options(reply["options"])
    except (KeyError, TypeError) as error:
        raise RuntimeError(f"{server} : réponse du tableau invalide.") from error
    except (OSError, RuntimeError, ValueError) as error:
        raise RuntimeError(f"{server} : {error}") from error
    if address is not None and server != identity["server"]:
        write_json(PAIRING, {**identity, "server": server})
    print(f"Connexion et association vérifiées avec {server}.")

def pair(server, code):
    server = server_address(server)
    board = serial()
    result = post(server, "/api/enroll", dict(code=code, serial=board, kind=DEVICE_KIND, options=initial_settings()))
    write_json(PAIRING, dict(server=server, serial=board, **result))
    print("Écran associé. Ouvre une nouvelle session pour lancer le lecteur.")

class DashboardClient:
    def __init__(self, identity):
        self.identity = identity
        self.diagnostic = Diagnostic()
        self.latency_ms = None
        self.latency_test = None
        self.updater = ClientUpdate(APP, CLIENT_DIR)
        self.can_update = "SOUNDLIVE_APP" in os.environ and self.updater.public.is_file()
        self.version = digest(contents(CLIENT_DIR))
        self.preview_job = None

    def poll(self, report):
        server = self.identity["server"]
        token = self.identity["token"]
        report.update(kind=DEVICE_KIND, latency_ms=self.latency_ms, latency_test=self.latency_test,
                      diagnostic=self.diagnostic.sample(server), can_update=self.can_update,
                      client_version=self.version, client_release=SOFTWARE_VERSION)
        if self.can_update:
            report["update_result"] = self.updater.report()
        started = time.monotonic()
        try:
            reply = post(server, "/api/poll", report, token)
        except (OSError, RuntimeError, ValueError):
            self.latency_ms = None
            self.latency_test = None
            raise
        self.latency_ms = round((time.monotonic() - started) * 1000, 1)
        preview = reply.get("preview_request")
        if isinstance(preview, dict) and not (reply.get("reboot") or reply.get("poweroff")):
            job, source = preview.get("id"), preview.get("source")
            if isinstance(job, str) and isinstance(source, str) and job != self.preview_job:
                self.preview_job = job
                threading.Thread(target=self.send_preview, args=(job, source), daemon=True).start()
        reply.pop("restart_client", None)
        if self.can_update and not (reply.get("reboot") or reply.get("poweroff")):
            job = reply.get("client_update")
            if job and job != (report.get("update_result") or {}).get("id"):
                self.receive_update(reply, report["id"], job)
        test = reply.get("latency_test")
        if test and test != (self.latency_test or {}).get("id"):
            check = dict(id=report["id"], boot=BOOT, state="Démarrage", check=True)
            try:
                started = time.monotonic()
                post(server, "/api/poll", check, token)
                self.latency_test = dict(id=test, ms=round((time.monotonic() - started) * 1000, 1))
            except (OSError, RuntimeError, ValueError):
                self.latency_test = dict(id=test, ms=None)
        return reply

    def send_preview(self, job, source):
        result = {"id": self.identity["id"], "job": job}
        python = Path(sys.executable)
        if WINDOWS and python.name.lower() == "pythonw.exe" and python.with_name("python.exe").is_file():
            python = python.with_name("python.exe")
        try:
            probe = subprocess.run(
                [str(python), "-B", str(Path(__file__).resolve()), "--apercu-ndi", source],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=9,
                creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0,
            )
            if probe.returncode == 0 and 0 < len(probe.stdout) <= PREVIEW_LIMIT:
                result["image"] = b64encode(probe.stdout).decode("ascii")
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            post(self.identity["server"], "/api/preview-result", result, self.identity["token"])
        except (OSError, RuntimeError, ValueError):
            pass

    def receive_update(self, reply, device, job):
        try:
            nonce = secrets.token_hex(32)
            bundle = post(self.identity["server"], "/api/client-package",
                          dict(id=device, job=job, nonce=nonce), self.identity["token"])
            if self.updater.stage(bundle, device, job, nonce):
                reply["restart_client"] = True
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, SyntaxError,
                subprocess.SubprocessError) as error:
            self.updater.failed(job, error)


def desktop():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

    class Screen:
        def __init__(self, log):
            self.log = log
            self.settings = initial_settings()
            try:
                self.identity = association()
            except (OSError, ValueError) as error:
                self.identity = None
                print(error, file=log)
            self.dashboard = DashboardClient(self.identity) if self.identity else None
            self.connection_status = None
            self.regie_state = None
            self.sources = []
            self.player = None
            self.active_source = None
            self.video_ready = False
            self.stop_at = None
            self.retry_at = 0
            self.misses = 0
            self.error = ""
            self.discovery_error = ""
            self.power_id = None
            self.work = ThreadPoolExecutor(max_workers=1)
            self.request = None
            self.restart_client = False
            self.waiting_data = None

            self.window = Gtk.Window(title=APP_NAME, decorated=False)
            self.window.connect("destroy", lambda *_: Gtk.main_quit())
            self.window.connect("key-press-event", self.key_press)
            css = Gtk.CssProvider()
            css.load_from_data(b"""
                window {background-image:linear-gradient(145deg,#230c11,#100e11 46%,#08080a);}
                label {color:white;}
                .line {background:#d93249;border-radius:3px;min-height:5px;}
                .waiting-status {color:#dfcbd1;font-size:22px;}
                .reader-group {color:#ffc0cd;font-size:18px;}
                .reader-regie {font-size:15px;}
                .qr-caption {color:#f0dce2;font-size:16px;}
                .reader-version {color:#dfcbd1;font-size:16px;}
                .reader-update {color:#ffc78a;font-size:18px;}
            """)
            Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), css,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_halign(Gtk.Align.CENTER)
            box.set_valign(Gtk.Align.CENTER)
            box.set_border_width(16)
            self.window.add(box)
            brand = Gtk.Label()
            brand.set_markup(f'<span size="46000" weight="bold">{APP_NAME}</span>')
            box.pack_start(brand, False, False, 0)
            line = Gtk.Box()
            line.set_size_request(64, 5)
            line.set_halign(Gtk.Align.CENTER)
            line.get_style_context().add_class("line")
            box.pack_start(line, False, False, 0)
            self.screen_name = Gtk.Label(label=(self.identity or {}).get("name") or socket.gethostname())
            self.screen_name.set_line_wrap(True)
            self.screen_name.set_max_width_chars(44)
            self.screen_name.set_justify(Gtk.Justification.CENTER)
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_size_new(28 * Pango.SCALE))
            attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
            self.screen_name.set_attributes(attrs)
            box.pack_start(self.screen_name, False, False, 0)
            self.group_label = Gtk.Label(no_show_all=True)
            self.group_label.set_line_wrap(True)
            self.group_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.group_label.set_max_width_chars(44)
            self.group_label.set_justify(Gtk.Justification.CENTER)
            self.group_label.get_style_context().add_class("reader-group")
            box.pack_start(self.group_label, False, False, 0)
            self.title = Gtk.Label()
            self.title.get_style_context().add_class("waiting-status")
            box.pack_start(self.title, False, False, 0)
            self.regie_label = Gtk.Label()
            self.regie_label.get_style_context().add_class("reader-regie")
            self.regie_label.set_line_wrap(True)
            self.regie_label.set_max_width_chars(44)
            self.regie_label.set_justify(Gtk.Justification.CENTER)
            box.pack_start(self.regie_label, False, False, 0)
            version = Gtk.Label(label=f"Version du lecteur {SOFTWARE_VERSION}")
            version.get_style_context().add_class("reader-version")
            box.pack_start(version, False, False, 0)
            copyright_label = Gtk.Label(label=COPYRIGHT)
            copyright_label.get_style_context().add_class("reader-version")
            box.pack_start(copyright_label, False, False, 0)
            self.update_label = Gtk.Label(no_show_all=True)
            self.update_label.get_style_context().add_class("reader-update")
            self.update_label.set_line_wrap(True)
            self.update_label.set_justify(Gtk.Justification.CENTER)
            box.pack_start(self.update_label, False, False, 0)
            self.qr_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, no_show_all=True)
            self.qr_image = Gtk.Image()
            self.qr_panel.pack_start(self.qr_image, False, False, 0)
            caption = Gtk.Label(label="Scanner pour ouvrir le tableau de régie")
            caption.get_style_context().add_class("qr-caption")
            self.qr_panel.pack_start(caption, False, False, 0)
            self.qr_image.show()
            caption.show()
            box.pack_start(self.qr_panel, False, False, 0)
            self.show_waiting_details(restore_waiting_details(self.identity, log))
            self.show_regie_state(REGIE_CHECKING if self.identity else REGIE_UNPAIRED)
            self.waiting()
            GLib.idle_add(self.probe)
            GLib.timeout_add(500, self.check)
            GLib.timeout_add_seconds(2, self.poll)

        def key_press(self, _window, event):
            if event.keyval == Gdk.KEY_Escape:
                Gtk.main_quit()
                return True
            return False

        def waiting(self):
            self.title.set_text("En attente du flux vidéo" if self.settings["enabled"] else "Diffusion arrêtée")
            self.window.fullscreen()
            self.window.show_all()

        def show_regie_state(self, state):
            if state != self.regie_state:
                text, color = state
                self.regie_label.set_markup(f'<span foreground="{color}">{text}</span>')
                self.regie_state = state
            self.group_label.set_visible(bool(waiting_group(self.waiting_data or {})) and state != REGIE_REPAIR)

        def show_waiting_details(self, details):
            if not isinstance(details, dict) or details == self.waiting_data:
                return False
            name = details.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                return False
            matrix = details.get("qr")
            self.screen_name.set_text(name)
            group = waiting_group(details)
            self.group_label.set_text(group)
            if matrix != (self.waiting_data or {}).get("qr"):
                bitmap = waiting_bitmap(matrix)
                if bitmap:
                    side, pixels = bitmap
                    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB,
                                                           False, 8, side, side, side * 3)
                    self.qr_image.set_from_pixbuf(pixbuf)
                    self.qr_panel.show()
                else:
                    self.qr_image.clear()
                    self.qr_panel.hide()
            self.waiting_data = details
            return True

        def probe(self):
            cmd = ["/usr/bin/timeout", "-k", "2s", "12s", PLAYER,
                   "-hide_banner", "-nostats", "-nodisp", "-an", "-f", "libndi_newtek",
                   "-wait_sources", "3", "-find_sources", "1", "-i", "dummy"]
            if UBUNTU:
                cmd = cmd[:4] + [PLAYER, "--sources"]
            try:
                proc = Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
                proc.communicate_utf8_async(None, None, self.probe_finished)
            except GLib.Error as error:
                print(error, file=self.log)
                GLib.timeout_add_seconds(5, self.probe)
            return GLib.SOURCE_REMOVE

        def probe_finished(self, proc, result):
            try:
                _, output, _ = proc.communicate_utf8_finish(result)
                if UBUNTU:
                    if not proc.get_successful():
                        self.sources = []
                        self.error = (output or "Découverte NDI indisponible.").strip()[:240]
                        if self.error != self.discovery_error:
                            print(self.error, file=self.log)
                        self.discovery_error = self.error
                    else:
                        self.sources = sorted(set(filter(None, (output or "").splitlines())))
                        if self.discovery_error == self.error:
                            self.error = ""
                        self.discovery_error = ""
                else:
                    self.sources = sorted({
                        line.split("\t")[1][1:-1]
                        for line in (output or "").splitlines()
                        if len(line.split("\t")) >= 3
                        and line.split("\t")[1].startswith("'") and line.split("\t")[1].endswith("'")
                    })
            except GLib.Error as error:
                print(error, file=self.log)
            if self.settings["source"] in self.sources:
                self.misses = 0
                if self.settings["enabled"] and self.player is None and GLib.get_monotonic_time() >= self.retry_at:
                    self.start()
            elif self.player is not None and not UBUNTU:
                self.misses += 1
                if self.misses >= 2:
                    self.terminate()
            GLib.timeout_add_seconds(5, self.probe)

        def start(self):
            volume = 0 if self.settings["muted"] else self.settings["volume"]
            command = [
                PLAYER, "-hide_banner", "-nostats", "-fs", "-framedrop",
                "-volume", str(volume), "-f", "libndi_newtek", "-wait_sources", "3",
                "-bandwidth", self.settings["bandwidth"], "-i", self.settings["source"],
            ]
            try:
                if UBUNTU:
                    READY.unlink(missing_ok=True)
                    command = [PLAYER, self.settings["source"], self.settings["bandwidth"],
                               str(volume), str(READY)]
                self.player = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT)
                self.active_source = self.settings["source"]
                self.video_ready = not UBUNTU
            except OSError as error:
                self.error = str(error)
                print(error, file=self.log)
                return
            if self.video_ready:
                self.window.hide()

        def terminate(self):
            if self.player is not None and self.player.poll() is None and self.stop_at is None:
                self.player.terminate()
                self.stop_at = GLib.get_monotonic_time() + 3 * GLib.USEC_PER_SEC

        def check(self):
            if self.player is not None:
                now = GLib.get_monotonic_time()
                status = self.player.poll()
                if status is not None:
                    if UBUNTU and status > 0 and self.stop_at is None:
                        self.error = "Le lecteur NDI a quitté avec une erreur. Consulte gestion.log."
                    self.player = self.stop_at = None
                    self.active_source = None
                    self.video_ready = False
                    self.retry_at = now + 2 * GLib.USEC_PER_SEC
                    self.waiting()
                elif self.stop_at is not None:
                    if now >= self.stop_at:
                        self.player.kill()
                elif UBUNTU and not self.video_ready and READY.exists():
                    self.video_ready = True
                    self.error = ""
                    self.window.hide()
            return GLib.SOURCE_CONTINUE

        def poll(self):
            if not self.identity:
                return GLib.SOURCE_CONTINUE
            if self.request:
                if not self.request.done():
                    return GLib.SOURCE_CONTINUE
                try:
                    result = self.request.result()
                    self.apply(result)
                    self.show_regie_state(REGIE_CONNECTED)
                    if self.dashboard.can_update:
                        self.dashboard.updater.ready()
                    if result.get("restart_client") is True:
                        self.restart_client = True
                        Gtk.main_quit()
                        return GLib.SOURCE_REMOVE
                    status = f"Connecté au tableau {self.identity['server']}."
                except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
                    self.show_regie_state(regie_unavailable(error))
                    status = f"Connexion au tableau {self.identity['server']} : {error}"
                if status != self.connection_status:
                    print(status, file=self.log)
                    self.connection_status = status
            state = "Arrêté" if not self.settings["enabled"] else "Lecture" if self.video_ready else "En attente"
            data = dict(id=self.identity["id"], boot=BOOT, state=state, sources=self.sources,
                        active_source=self.active_source if state == "Lecture" else None,
                        error=self.error, can_poweroff=True)
            self.request = self.work.submit(self.dashboard.poll, data)
            return GLib.SOURCE_CONTINUE

        def apply(self, result):
            new = options(result["options"])
            message = waiting_update_message(result.get("reader_update"))
            self.update_label.set_text(message)
            self.update_label.set_visible(bool(message))
            if self.show_waiting_details(result.get("waiting")):
                save_waiting_details(self.identity, self.waiting_data, self.log)
            if new != self.settings:
                write_json(LAST_SETTINGS, new)
                self.settings = new
                self.terminate()
                self.waiting()
            if result.get("poweroff"):
                self.power("poweroff", result["poweroff"], "Extinction")
            elif result.get("reboot"):
                self.power("reboot", result["reboot"], "Redémarrage")

        def power(self, operation, request_id, label):
            if request_id == self.power_id:
                return
            self.power_id = request_id
            self.error = ""
            try:
                proc = subprocess.run(["sudo", "-n", "/usr/bin/systemctl", operation],
                                      capture_output=True, text=True, timeout=4)
                if proc.returncode:
                    self.error = label + " : " + proc.stderr.strip()
            except (OSError, subprocess.TimeoutExpired) as error:
                self.error = label + " : " + str(error)

    if not UBUNTU:
        os.environ["LD_LIBRARY_PATH"] = str(NDI / "lib")
    STATE.mkdir(parents=True, exist_ok=True)
    with client_lock(STATE / "ecran.lock") as acquired:
        if not acquired:
            return
        with (STATE / "gestion.log").open("w", buffering=1) as log:
            screen = Screen(log)
            def quit_screen():
                Gtk.main_quit()
                return GLib.SOURCE_REMOVE
            for sig in (signal.SIGINT, signal.SIGTERM):
                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, quit_screen)
            try:
                Gtk.main()
            finally:
                screen.terminate()
                if screen.player:
                    try:
                        screen.player.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        screen.player.kill()
                        screen.player.wait()
                screen.work.shutdown(wait=False, cancel_futures=True)
            return RESTART if screen.restart_client else 0


def windows_desktop():
    import tkinter as tk

    STATE.mkdir(parents=True, exist_ok=True)
    with client_lock(STATE / "ecran.lock") as acquired:
        if not acquired:
            return 0
        with (STATE / "gestion.log").open("a", encoding="utf-8", buffering=1) as log:
            return run_windows_screen(tk, log)


def run_windows_screen(tk, log):
    try:
        identity = association()
    except (OSError, ValueError) as error:
        identity = None
        print(error, file=log)
    client = DashboardClient(identity) if identity else None
    settings = initial_settings()
    root = tk.Tk()
    root.title(APP_NAME)
    root.configure(background="#100e11")
    root.attributes("-fullscreen", True)
    panel = tk.Frame(root, background="#100e11")
    panel.place(relx=.5, rely=.5, anchor="center")

    def label(text, size, color="white"):
        widget = tk.Label(panel, text=text, bg="#100e11", fg=color, font=("Segoe UI", size), wraplength=760)
        widget.pack(pady=7)
        return widget

    label(APP_NAME, 36)
    name = label((identity or {}).get("name") or socket.gethostname(), 24)
    status = label("", 18)
    group_label = label("", 18, "#ffc0cd")
    group_label.pack_forget()
    regie_label = label("", 13)
    label(f"Version du lecteur {SOFTWARE_VERSION}", 13, "#dfcbd1")
    label(COPYRIGHT, 12, "#dfcbd1")
    notice = label("", 14, "#ffc78a")
    qr = label("", 13)
    picture = None
    waiting_data = None
    sources = []
    player = None
    failure = ""
    request = None
    discovery = None
    next_poll = next_probe = 0
    restart = False
    ready = False
    power_id = None
    work = ThreadPoolExecutor(max_workers=2)
    flags = subprocess.CREATE_NO_WINDOW
    retry_at = 0

    def show_regie_state(state):
        text, color = state
        regie_label.configure(text=text, fg=color)
        group = waiting_group(waiting_data or {})
        if group and state != REGIE_REPAIR:
            group_label.configure(text=group)
            group_label.pack(before=status, pady=(0, 5))
        else:
            group_label.pack_forget()

    def show_waiting_details(details):
        nonlocal waiting_data, picture
        if not isinstance(details, dict) or details == waiting_data:
            return False
        screen_name = details.get("name")
        if not isinstance(screen_name, str) or not screen_name.strip() or len(screen_name) > 80:
            return False
        name.configure(text=screen_name)
        matrix = details.get("qr")
        if matrix != (waiting_data or {}).get("qr"):
            bitmap = waiting_bitmap(matrix)
            if bitmap:
                side, rgb = bitmap
                picture = tk.PhotoImage(data=f"P6\n{side} {side}\n255\n".encode() + rgb, format="PPM")
            else:
                picture = None
            qr.configure(image=picture if picture else "", text="")
        waiting_data = details
        return True

    def show_waiting():
        status.configure(text="En attente du flux vidéo" if settings["enabled"] else "Diffusion arrêtée")
        root.deiconify()
        root.attributes("-fullscreen", True)

    def stop_player():
        nonlocal ready, player
        if player and player.poll() is None:
            player.terminate()
            player.wait(timeout=4)
        player = None
        ready = False

    def discover():
        result = subprocess.run([PLAYER, "--sources"], capture_output=True, encoding="utf-8",
                                errors="replace", timeout=10, creationflags=flags)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Découverte NDI indisponible.")
        return [line for line in result.stdout.splitlines() if line]

    def update():
        nonlocal request, discovery, next_poll, next_probe, player, settings, sources
        nonlocal failure, restart, ready, power_id, retry_at
        now = time.monotonic()
        try:
            if request and request.done():
                finished, request = request, None
                try:
                    answer = finished.result()
                    new = options(answer["options"])
                    if new != settings:
                        write_json(LAST_SETTINGS, new)
                        settings = new
                        stop_player()
                    if show_waiting_details(answer.get("waiting")):
                        save_waiting_details(identity, waiting_data, log)
                    show_regie_state(REGIE_CONNECTED)
                    notice.configure(text=waiting_update_message(answer.get("reader_update")))
                    if client.can_update:
                        client.updater.ready()
                    if answer.get("restart_client"):
                        restart = True
                        root.quit()
                        return
                    operation = "poweroff" if answer.get("poweroff") else "reboot"
                    power = answer.get(operation)
                    if power and power != power_id:
                        power_id = power
                        action = "/s" if operation == "poweroff" else "/r"
                        result = subprocess.run(["shutdown.exe", action, "/t", "0"], capture_output=True,
                                                timeout=4, creationflags=flags)
                        if result.returncode:
                            print(result.stderr.decode(errors="replace"), file=log)
                except (OSError, RuntimeError, KeyError, TypeError, ValueError) as error:
                    show_regie_state(regie_unavailable(error))
                    print(error, file=log)
            if discovery and discovery.done():
                finished, discovery = discovery, None
                try:
                    sources = finished.result()
                    failure = ""
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    sources = []
                    failure = str(error)[:240]
            if player and player.poll() is not None:
                if player.returncode:
                    failure = f"Le lecteur NDI a quitté avec le code {player.returncode}. Consulte gestion.log."
                stop_player()
                retry_at = now + 3
                sources = []
            if player and not ready and READY.exists():
                ready = True
                root.withdraw()
            if not player and now >= retry_at and settings["enabled"] and settings["source"] in sources:
                READY.unlink(missing_ok=True)
                volume = 0 if settings["muted"] else settings["volume"]
                player = subprocess.Popen([PLAYER, settings["source"], settings["bandwidth"], str(volume), str(READY)],
                                          stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
            if not ready:
                show_waiting()
            if not discovery and now >= next_probe:
                discovery = work.submit(discover)
                next_probe = now + 5
            if not request and now >= next_poll:
                state = "Arrêté" if not settings["enabled"] else "Lecture" if ready else "En attente"
                request = work.submit(client.poll, dict(id=identity["id"], boot=BOOT, state=state,
                    sources=sources, active_source=settings["source"] if ready else None,
                    error=failure, can_poweroff=True, can_reboot=True))
                next_poll = now + 2
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            failure = str(error)[:240]
            sources = []
        root.after(250, update)

    root.bind("<Escape>", lambda _event: root.quit())
    root.protocol("WM_DELETE_WINDOW", root.quit)
    show_waiting_details(restore_waiting_details(identity, log))
    show_regie_state(REGIE_CHECKING if identity else REGIE_UNPAIRED)
    show_waiting()
    if client:
        root.after(0, update)
    try:
        root.mainloop()
    finally:
        try:
            stop_player()
        finally:
            work.shutdown(wait=True, cancel_futures=True)
            root.destroy()
    return RESTART if restart else 0

if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--apercu-ndi":
            sys.stdout.buffer.write(capture_ndi_preview(sys.argv[2]))
        elif len(sys.argv) == 4 and sys.argv[1] == "associer":
            pair(sys.argv[2], sys.argv[3])
        elif len(sys.argv) in (2, 3) and sys.argv[1] == "verifier":
            check_connection(sys.argv[2] if len(sys.argv) == 3 else None)
        elif len(sys.argv) == 1:
            sys.exit(windows_desktop() if WINDOWS else desktop())
        else:
            raise ValueError("Usage : ecran.py associer http://ADRESSE_DU_PC:8765 CODE"
                             " ou ecran.py verifier [http://ADRESSE_DU_PC:8765]")
    except (OSError, RuntimeError, ValueError, AttributeError) as error:
        sys.exit(str(error))
