#!/usr/bin/python3
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
import configparser
from concurrent.futures import Future, ThreadPoolExecutor
from base64 import b64encode
from http.client import HTTPException
import json
import os
import re
import secrets
import shutil
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import warnings
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from commun import (APP_NAME, COPYRIGHT, DEFAULT_SOURCE, ORIENTATIONS, PREVIEW_LIMIT,
                    SOFTWARE_VERSION, client_lock, options, release_number)
from diagnostic import Diagnostic
from mise_a_jour import ClientUpdate, FULL_LIMIT, RESTART, digest, engine_name, update_target

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
PLAYER = NDI / "bin/ffplay"
if WINDOWS or UBUNTU:
    filename = "lecteur-ndi-windows.exe" if WINDOWS else "lecteur-ndi-ubuntu"
    staged_player = CLIENT_DIR / filename
    PLAYER = staged_player if staged_player.is_file() else APP / filename
PLAYER = str(PLAYER)
if UBUNTU:
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (str(APP / "lib"), os.environ.get("LD_LIBRARY_PATH"))))
READY = STATE / "video.prete"
RENDER_PREVIEW = STATE / "apercu-interne"
BOOT = "windows-" + secrets.token_hex(16) if WINDOWS else Path("/proc/sys/kernel/random/boot_id").read_text().strip()
REGIE_UNPAIRED = ("Lecteur non associé à la régie", "#ffacb8")
REGIE_CHECKING = ("Vérification du rattachement…", "#dfcbd1")
REGIE_CONNECTED = ("Lecteur rattaché à la régie", "#95e6b3")
REGIE_UNAVAILABLE = ("Régie indisponible · association enregistrée", "#ffc78a")
REGIE_REPAIR = ("Réassociation à la régie nécessaire", "#ffacb8")
MUTTER_DISPLAY = "org.gnome.Mutter.DisplayConfig"
MUTTER_DISPLAY_PATH = "/org/gnome/Mutter/DisplayConfig"

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


def display_snapshot_linux(player_pid, capture):
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, Gio, GLib

    Gdk.init_check([])
    display = Gdk.Display.get_default()
    if display is None:
        raise RuntimeError("Session graphique indisponible.")
    x11 = display.__gtype__.name == "GdkX11Display"
    gnome_portal = not x11 and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    screen = display.get_default_screen()
    if player_pid < 0:
        monitor = display.get_monitor(-player_pid - 1)
    elif player_pid and display.get_n_monitors() > 1:
        if not x11:
            if gnome_portal and not capture:
                return {"portal": True}
            raise RuntimeError("Sortie du lecteur indéterminée en Wayland avec plusieurs moniteurs.")
        for window in reversed(screen.get_window_stack() or []):
            try:
                found, _, _, value = Gdk.property_get(window, Gdk.Atom.intern("_NET_WM_PID", False),
                                                      Gdk.Atom.intern("CARDINAL", False), 0, 4, False)
                if found and value and (list(value) == [player_pid] or
                                        int.from_bytes(bytes(value)[:4], sys.byteorder) == player_pid):
                    monitor = display.get_monitor_at_window(window)
                    break
            except (TypeError, ValueError, OverflowError):
                continue
        else:
            raise RuntimeError("Sortie du lecteur indéterminée.")
    if monitor is None:
        raise RuntimeError("Aucun moniteur détecté.")
    rect = monitor.get_geometry()
    scale = monitor.get_scale_factor()
    index = next((i for i in range(display.get_n_monitors()) if display.get_monitor(i) == monitor), -1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        connector = screen.get_monitor_plug_name(index) if index >= 0 else None
    grim = shutil.which("grim") if not x11 else None
    info = dict(width=rect.width * scale, height=rect.height * scale,
                model=" ".join(dict.fromkeys(filter(None, (monitor.get_manufacturer(), monitor.get_model())))),
                connector=connector or "", estimated=not x11, capture=bool(x11 or grim))
    if not capture and gnome_portal:
        info["capture"] = False
        info["portal"] = True
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            state = bus.call_sync(MUTTER_DISPLAY, MUTTER_DISPLAY_PATH, MUTTER_DISPLAY, "GetCurrentState",
                                  None, None, Gio.DBusCallFlags.NO_AUTO_START, 800, None).unpack()
            for spec, modes, properties in state[1]:
                logical = next((item for item in state[2] if spec in item[5]), None)
                if logical is None or logical[:2] != (rect.x, rect.y):
                    continue
                for _, width, height, _, _, _, flags in modes:
                    if flags.get("is-current"):
                        if logical[3] in (1, 3, 5, 7):
                            width, height = height, width
                        info.update(width=width, height=height, connector=spec[0],
                                    model=properties.get("display-name") or info["model"], estimated=False)
                        break
                break
        except (GLib.Error, IndexError, TypeError, ValueError):
            pass
    if not capture:
        connector = info["connector"]
        outputs = list(Path("/sys/class/drm").glob("card*-*/edid"))
        exact = [item for item in outputs if item.parent.name.partition("-")[2] == connector]
        if not exact and display.get_n_monitors() == 1:
            for item in outputs:
                try:
                    if (item.parent / "status").read_text().strip() == "connected":
                        exact.append(item)
                except OSError:
                    continue
        if len(exact) == 1:
            try:
                edid = exact[0].read_bytes()
            except OSError:
                edid = b""
            if len(edid) >= 128 and edid[:8] == bytes.fromhex("00ffffffffffff00"):
                info["connector"] = exact[0].parent.name.partition("-")[2]
                for start in (54, 72, 90, 108):
                    descriptor = edid[start:start + 18]
                    if descriptor[:5] == b"\x00\x00\x00\xfc\x00":
                        model = descriptor[5:].decode("ascii", "replace").strip(" \r\n\x00")
                        if model:
                            info["model"] = model
                            break
        return info
    if x11:
        image = Gdk.pixbuf_get_from_window(screen.get_root_window(), rect.x, rect.y, rect.width, rect.height)
    else:
        if not grim:
            raise RuntimeError("Capture Wayland indisponible. Une session compatible avec grim est nécessaire.")
        region = f"{rect.x},{rect.y} {rect.width}x{rect.height}"
        factor = min(240 / rect.width, 135 / rect.height, 1)
        try:
            raw = subprocess.run([grim, "-g", region, "-s", str(factor), "-t", "png", "-"],
                                 capture_output=True, timeout=8)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Grim n’a pas reçu l’image du moniteur en 8 secondes. Relance la capture.") from error
        if raw.returncode != 0:
            raise RuntimeError("Capture indisponible sur ce bureau Wayland.")
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(raw.stdout)
        loader.close()
        image = loader.get_pixbuf()
    if image is None:
        raise RuntimeError("Lecture du moniteur indisponible.")
    width, height = preview_size(image.get_width(), image.get_height())
    image = image.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
    if image.get_has_alpha():
        output = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
        output.fill(0)
        image.composite(output, 0, 0, width, height, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
        image = output
    ok, png = image.save_to_bufferv("png", [], [])
    if not ok:
        raise RuntimeError("Impossible d’encoder la capture.")
    return bytes(png)


def orient_desktop_mutter(connector, transform):
    from gi.repository import Gio, GLib

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        state = bus.call_sync(MUTTER_DISPLAY, MUTTER_DISPLAY_PATH, MUTTER_DISPLAY, "GetCurrentState",
                              None, None, Gio.DBusCallFlags.NO_AUTO_START, 900, None)
        serial, monitors, logical, properties = state.unpack()
        if connector:
            targets = [item for item in logical if any(spec[0] == connector for spec in item[5])]
        else:
            targets = logical
        if not targets and len(logical) == 1:
            targets = logical
        if len(targets) != 1 or len(targets[0][5]) != 1:
            raise RuntimeError("Sortie GNOME indéterminée. Vérifie la configuration des moniteurs.")
        target = targets[0]
        if target[3] == transform:
            return False
        active_modes = {}
        for spec, modes, monitor_properties in monitors:
            for mode in modes:
                if mode[-1].get("is-current"):
                    flags = {}
                    if "is-underscanning" in monitor_properties:
                        flags["underscanning"] = GLib.Variant("b", monitor_properties["is-underscanning"])
                    if "color-mode" in monitor_properties:
                        flags["color-mode"] = GLib.Variant("u", monitor_properties["color-mode"])
                    active_modes[tuple(spec)] = (spec[0], mode[0], flags)
                    break
        layout = []
        for item in logical:
            x, y, scale, rotation, primary, outputs, _ = item
            layout.append((x, y, scale, transform if item == target else rotation, primary,
                           [active_modes[tuple(spec)] for spec in outputs]))
        global_options = {}
        if properties.get("supports-changing-layout-mode") and "layout-mode" in properties:
            global_options["layout-mode"] = GLib.Variant("u", properties["layout-mode"])
        original = state.get_child_value(3)
        lease = original.lookup_value("monitors-for-lease", None)
        if lease is not None:
            global_options["monitors-for-lease"] = lease
        # La méthode de session applique la rotation sans boîte de confirmation. Le client
        # réapplique la valeur enregistrée par la régie à la prochaine ouverture de session.
        arguments = GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (serial, 1, layout, global_options))
        bus.call_sync(MUTTER_DISPLAY, MUTTER_DISPLAY_PATH, MUTTER_DISPLAY, "ApplyMonitorsConfig",
                      arguments, None, Gio.DBusCallFlags.NO_AUTO_START, 2200, None)
    except (GLib.Error, KeyError) as error:
        raise RuntimeError(f"GNOME a refusé l’orientation de l’écran. {error}") from error
    return True


def orient_desktop_linux(player_pid, orientation):
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk

    Gdk.init_check([])
    display = Gdk.Display.get_default()
    if display is None:
        raise RuntimeError("Session graphique indisponible.")
    info = display_snapshot_linux(player_pid, False)
    connector = info.get("connector")
    index = ORIENTATIONS.index(orientation)
    wayland = (display.__gtype__.name == "GdkWaylandDisplay"
               or os.environ.get("XDG_SESSION_TYPE") == "wayland")
    if wayland and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        return orient_desktop_mutter(connector, (0, 1, 3)[index])

    utility = "wlr-randr" if wayland else "xrandr"
    executable = shutil.which(utility)
    if not executable:
        raise RuntimeError(f"Installe {utility if wayland else 'x11-xserver-utils'} sur ce lecteur.")

    def command(*args):
        result = subprocess.run([executable, *args], capture_output=True, text=True, timeout=2,
                                env={**os.environ, "LC_ALL": "C"})
        if result.returncode:
            message = result.stderr.strip() or "Le bureau a refusé l’orientation."
            raise RuntimeError(f"{utility} · {message}")
        return result.stdout

    outputs = {}
    if wayland:
        # La sortie texte existe aussi dans les anciennes versions de wlr-randr.
        output = None
        enabled = False
        for line in command().splitlines():
            if line and not line[0].isspace():
                output = line.split()[0]
                enabled = False
            elif line.strip() == "Enabled: yes":
                enabled = True
            elif output and enabled and line.strip().startswith("Transform:"):
                outputs[output] = line.partition(":")[2].strip()
        desired, flag = ("normal", "90", "270")[index], "--transform"
    else:
        for line in command("--query").splitlines():
            match = re.match(r"^(\S+) connected (?:primary )?\d+x\d+[-+]\d+[-+]\d+\s+([^()]*)", line)
            if match:
                fields = match[2].split()
                outputs[match[1]] = fields[0] if fields and fields[0] in ("left", "right", "inverted") else "normal"
        desired, flag = ("normal", "left", "right")[index], "--rotate"
    if connector not in outputs:
        if len(outputs) == 1:
            connector = next(iter(outputs))
        else:
            raise RuntimeError("La sortie à tourner n’a pas pu être identifiée.")
    if outputs[connector] == desired:
        return False
    command("--output", connector, flag, desired)
    return True


def orient_desktop_windows(player_pid, orientation):
    import ctypes as c
    from ctypes import wintypes as w

    connector = display_snapshot_windows(player_pid, False)["connector"]
    user = c.windll.user32

    class DisplayMode(c.Structure):
        # Branche écran de DEVMODEW (l’union imprimante a la même taille).
        _fields_ = [
            ("device", w.WCHAR * 32), ("spec_version", w.WORD), ("driver_version", w.WORD),
            ("size", w.WORD), ("driver_extra", w.WORD), ("fields", w.DWORD),
            ("position", w.POINT), ("orientation", w.DWORD), ("fixed_output", w.DWORD),
            ("printer_fields", w.WORD * 5), ("form", w.WCHAR * 32), ("log_pixels", w.WORD),
            ("bits_per_pixel", w.DWORD), ("width", w.DWORD), ("height", w.DWORD),
            ("display_fields", w.DWORD * 10),
        ]

    query = user.EnumDisplaySettingsW
    query.argtypes = (w.LPCWSTR, w.DWORD, c.POINTER(DisplayMode))
    query.restype = w.BOOL
    change = user.ChangeDisplaySettingsExW
    change.argtypes = (w.LPCWSTR, c.POINTER(DisplayMode), w.HWND, w.DWORD, c.c_void_p)
    change.restype = w.LONG
    mode = DisplayMode(size=c.sizeof(DisplayMode))
    if not query(connector, 0xffffffff, c.byref(mode)):
        raise RuntimeError("Windows ne peut pas lire le mode de cet écran.")
    desired = (0, 1, 3)[ORIENTATIONS.index(orientation)]
    if mode.orientation == desired:
        return False
    if (mode.orientation - desired) % 2:
        mode.width, mode.height = mode.height, mode.width
    mode.orientation = desired
    mode.fields |= 0x00000080 | 0x00080000 | 0x00100000  # DM_DISPLAYORIENTATION, DM_PELSWIDTH, DM_PELSHEIGHT
    result = change(connector, c.byref(mode), None, 2, None)  # CDS_TEST
    if result == 0:
        result = change(connector, c.byref(mode), None, 1, None)  # CDS_UPDATEREGISTRY
    if result != 0:
        raise RuntimeError(f"Windows a refusé l’orientation de cet écran (code {result}).")
    return True


def preview_size(width, height):
    scale = min(240 / width, 135 / height, 1)
    return max(1, round(width * scale)), max(1, round(height * scale))


def player_render_preview():
    import struct
    import zlib

    token = secrets.token_hex(12)
    request = RENDER_PREVIEW.with_suffix(".request")
    temporary = RENDER_PREVIEW.with_suffix(".new")
    output = RENDER_PREVIEW.with_suffix(".rgb")
    temporary.write_text(token + "\n", encoding="ascii")
    temporary.replace(request)
    try:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                with output.open("rb") as stream:
                    if stream.readline(26) == token.encode() + b"\n":
                        width, height = map(int, stream.readline(16).split())
                        if not (1 <= width <= 240 and 1 <= height <= 135):
                            raise ValueError("Dimensions d’aperçu invalides.")
                        stride = width * 3
                        pixels = stream.read(stride * height + 1)
                        if len(pixels) != stride * height:
                            raise ValueError("Image du lecteur incomplète.")
                        raw = b"".join(b"\0" + pixels[row:row + stride] for row in range(0, len(pixels), stride))

                        def chunk(kind, data):
                            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

                        return (b"\x89PNG\r\n\x1a\n" +
                                chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
                                chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))
            except FileNotFoundError:
                pass
            time.sleep(.10)
        raise RuntimeError("Le lecteur n’a pas produit d’image. Réessaie quand le flux est en lecture.")
    finally:
        request.unlink(missing_ok=True)


class PortalCapture:
    name = "org.freedesktop.portal.Desktop"
    path = "/org/freedesktop/portal/desktop"
    interface = "org.freedesktop.portal.ScreenCast"

    def __init__(self):
        self.session = None
        self.stream = None
        self.bus = None

    def request(self, method, signature, arguments, options, timeout):
        from gi.repository import Gio, GLib

        token = "ndi_" + secrets.token_hex(12)
        options = {**options, "handle_token": GLib.Variant("s", token)}
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"{self.path}/request/{sender}/{token}"
        context = GLib.MainContext.new()
        loop = GLib.MainLoop.new(context, False)
        response = None

        def received(_bus, _sender, _path, _interface, _signal, parameters):
            nonlocal response
            response = parameters.unpack()
            loop.quit()

        def expired(*_):
            loop.quit()
            return GLib.SOURCE_REMOVE

        timer = GLib.timeout_source_new(timeout * 1000)
        context.push_thread_default()
        try:
            subscription = self.bus.signal_subscribe(self.name, "org.freedesktop.portal.Request",
                                                     "Response", path, None, Gio.DBusSignalFlags.NONE, received)
            try:
                (request_path,) = self.bus.call_sync(
                    self.name, self.path, self.interface, method, GLib.Variant(signature, (*arguments, options)),
                    GLib.VariantType.new("(o)"), Gio.DBusCallFlags.NONE, 3000, None).unpack()
                if request_path != path:
                    raise RuntimeError("Le portail a renvoyé une requête de partage inattendue.")
                timer.set_callback(expired)
                timer.attach(context)
                loop.run()
                if response is None:
                    self.close(path, "Request")
                    raise RuntimeError("Le délai d’autorisation a expiré. Relance la capture et valide sur Ubuntu.")
                if response[0] != 0:
                    raise RuntimeError("Partage d’écran annulé ou refusé sur Ubuntu.")
                return response[1]
            finally:
                self.bus.signal_unsubscribe(subscription)
        finally:
            timer.destroy()
            context.pop_thread_default()

    def close(self, path, interface):
        from gi.repository import Gio, GLib

        try:
            self.bus.call_sync(self.name, path, "org.freedesktop.portal." + interface, "Close",
                               None, None, Gio.DBusCallFlags.NONE, 1000, None)
        except GLib.Error:
            pass

    def snapshot(self, allow_prompt):
        from gi.repository import Gio, GLib

        gst = shutil.which("gst-launch-1.0")
        if not gst:
            raise RuntimeError("Installe les dépendances GStreamer sur le lecteur Ubuntu.")
        try:
            if self.session is None:
                if not allow_prompt:
                    raise RuntimeError("Clique sur Autoriser la capture puis valide le partage sur Ubuntu.")
                self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                session = self.request("CreateSession", "(a{sv})", (),
                                       {"session_handle_token": GLib.Variant("s", "ndi_" + secrets.token_hex(12))}, 4)
                self.session = session["session_handle"]
                self.request("SelectSources", "(oa{sv})", (self.session,),
                             {"types": GLib.Variant("u", 1), "multiple": GLib.Variant("b", False)}, 4)
                result = self.request("Start", "(osa{sv})", (self.session, ""), {}, 55)
                [(node, props)] = result["streams"]
                self.stream = (node, props.get("pipewire-serial"))
            response, fds = self.bus.call_with_unix_fd_list_sync(
                self.name, self.path, self.interface, "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self.session, {})), GLib.VariantType.new("(h)"),
                Gio.DBusCallFlags.NONE, 3000, None, None)
            fd = fds.get(response.unpack()[0])
            try:
                node, serial = self.stream
                target = f"target-object={serial}" if serial is not None else f"path={node}"
                result = subprocess.run(
                    [gst, "-q", "pipewiresrc", f"fd={fd}", target, "num-buffers=1",
                     "!", "videoconvert", "!", "videoscale", "add-borders=true",
                     "!", "video/x-raw,format=RGB,width=240,height=135,pixel-aspect-ratio=1/1",
                     "!", "pngenc", "!", "fdsink", "fd=1", "sync=false"],
                    capture_output=True, pass_fds=(fd,), timeout=8)
            finally:
                os.close(fd)
            if result.returncode or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                if b"no element" in result.stderr or b"no property" in result.stderr:
                    raise RuntimeError("Un module GStreamer manque sur Ubuntu. Installe les paquets indiqués dans le README.")
                raise RuntimeError("PipeWire n’a pas pu lire l’écran partagé. Relance la capture.")
            return result.stdout
        except (GLib.Error, KeyError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            if self.session:
                self.close(self.session, "Session")
            self.session = self.stream = None
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError("Partage d’écran indisponible. Relance la capture et valide sur Ubuntu.") from error


def display_snapshot_windows(player_pid, capture):
    import ctypes as c
    from ctypes import wintypes as w
    import struct
    import zlib

    user, gdi = c.windll.user32, c.windll.gdi32
    if not hasattr(user, "SetProcessDpiAwarenessContext") or not user.SetProcessDpiAwarenessContext(c.c_void_p(-4)):
        user.SetProcessDPIAware()
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    class Monitor(c.Structure):
        _fields_ = [("size", w.DWORD), ("area", w.RECT), ("work", w.RECT),
                    ("flags", w.DWORD), ("name", w.WCHAR * 32)]

    class Device(c.Structure):
        _fields_ = [("size", w.DWORD), ("name", w.WCHAR * 32), ("description", w.WCHAR * 128),
                    ("flags", w.DWORD), ("id", w.WCHAR * 128), ("key", w.WCHAR * 128)]

    for library, name, result, args in (
        (user, "EnumWindows", w.BOOL, (callback_type, w.LPARAM)),
        (user, "GetWindowThreadProcessId", w.DWORD, (w.HWND, c.POINTER(w.DWORD))),
        (user, "IsWindowVisible", w.BOOL, (w.HWND,)),
        (user, "IsIconic", w.BOOL, (w.HWND,)),
        (user, "MonitorFromWindow", w.HANDLE, (w.HWND, w.DWORD)),
        (user, "GetMonitorInfoW", w.BOOL, (w.HANDLE, c.POINTER(Monitor))),
        (user, "EnumDisplayDevicesW", w.BOOL, (w.LPCWSTR, w.DWORD, c.POINTER(Device), w.DWORD)),
        (user, "GetDC", w.HDC, (w.HWND,)),
        (user, "ReleaseDC", c.c_int, (w.HWND, w.HDC)),
        (gdi, "CreateCompatibleDC", w.HDC, (w.HDC,)),
        (gdi, "CreateDIBSection", w.HBITMAP, (w.HDC, c.c_void_p, w.UINT, c.POINTER(c.c_void_p), w.HANDLE, w.DWORD)),
        (gdi, "SelectObject", w.HANDLE, (w.HDC, w.HANDLE)),
        (gdi, "SetStretchBltMode", c.c_int, (w.HDC, c.c_int)),
        (gdi, "StretchBlt", w.BOOL, (w.HDC, c.c_int, c.c_int, c.c_int, c.c_int,
                                    w.HDC, c.c_int, c.c_int, c.c_int, c.c_int, w.DWORD)),
        (gdi, "GdiFlush", w.BOOL, ()),
        (gdi, "DeleteObject", w.BOOL, (w.HANDLE,)),
        (gdi, "DeleteDC", w.BOOL, (w.HDC,)),
    ):
        function = getattr(library, name)
        function.restype, function.argtypes = result, args
    window = None

    @callback_type
    def find_window(handle, _):
        nonlocal window
        owner = w.DWORD()
        user.GetWindowThreadProcessId(handle, c.byref(owner))
        if owner.value == player_pid and user.IsWindowVisible(handle) and not user.IsIconic(handle):
            window = handle
            return False
        return True

    if player_pid:
        user.EnumWindows(find_window, 0)
        if not window:
            raise RuntimeError("Fenêtre du lecteur introuvable.")
    monitor = Monitor(size=c.sizeof(Monitor))
    if not user.GetMonitorInfoW(user.MonitorFromWindow(window, 1), c.byref(monitor)):
        raise RuntimeError("Aucun moniteur détecté.")
    rect = monitor.area
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if not capture:
        device = Device(size=c.sizeof(Device))
        model = ""
        if user.EnumDisplayDevicesW(monitor.name, 0, c.byref(device), 0):
            model = device.description
            identifier = device.id.split("\\")
            if len(identifier) > 1 and identifier[0] == "MONITOR" and identifier[1] != "Default_Monitor":
                model = f"{model} · {identifier[1]}" if model else identifier[1]
        return dict(width=width, height=height, model=model, connector=monitor.name, capture=True)
    w_out, h_out = preview_size(width, height)
    header = c.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, w_out, -h_out, 1, 32, 0, 0, 0, 0, 0, 0))
    dc = user.GetDC(None)
    if not dc:
        raise RuntimeError("Session graphique Windows indisponible.")
    memory = bitmap = previous = None
    try:
        memory = gdi.CreateCompatibleDC(dc)
        pixels = c.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, header, 0, c.byref(pixels), None, 0)
        if not memory or not bitmap or not pixels:
            raise RuntimeError("Impossible d’allouer la capture Windows.")
        previous = gdi.SelectObject(memory, bitmap)
        if not previous or previous == c.c_void_p(-1).value:
            previous = None
            raise RuntimeError("Impossible de sélectionner le bitmap Windows.")
        gdi.SetStretchBltMode(memory, 4)
        if not gdi.StretchBlt(memory, 0, 0, w_out, h_out, dc, rect.left, rect.top, width, height, 0x40CC0020):
            raise RuntimeError("Impossible de capturer l’affichage Windows.")
        gdi.GdiFlush()
        pixels = c.string_at(pixels, w_out * h_out * 4)
    finally:
        if previous:
            gdi.SelectObject(memory, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory:
            gdi.DeleteDC(memory)
        user.ReleaseDC(None, dc)
    scanlines = bytearray()
    for start in range(0, len(pixels), w_out * 4):
        scanlines.append(0)
        for offset in range(start, start + w_out * 4, 4):
            scanlines.extend((pixels[offset + 2], pixels[offset + 1], pixels[offset]))

    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w_out, h_out, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scanlines, 1)) + chunk(b"IEND", b""))


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
            with build_opener(NoRedirect).open(request, timeout=20) as response:
                body = response.read(2 * FULL_LIMIT + 1)
                if len(body) > 2 * FULL_LIMIT:
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
            except (AttributeError, ValueError, HTTPException):
                message = error.reason
        raise RegieResponseError(error.code, message, code) from error
    except HTTPException as error:
        raise OSError(f"Réponse HTTP interrompue ou invalide ({type(error).__name__}).") from error

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
    def __init__(self, identity, orientation):
        self.identity = identity
        self.diagnostic = Diagnostic()
        self.latency_ms = None
        self.latency_test = None
        self.updater = ClientUpdate(APP, CLIENT_DIR)
        self.can_update = "SOUNDLIVE_APP" in os.environ and self.updater.public.is_file()
        self.target = update_target()
        files = self.updater.installed_files(self.target)
        self.version = digest(files)
        self.has_versioned_engine = CLIENT_DIR != APP and engine_name(self.target) in files
        self.preview_job = None
        self.display_work = ThreadPoolExecutor(max_workers=1)
        self.display_request = None
        self.display_target = None
        self.display_info = {}
        self.display_check_at = 0
        self.orientation = orientation
        self.orientation_report = None
        self.portal = PortalCapture() if not WINDOWS else None
        self.internal_preview = None
        self.internal_preview_required = False
        self.local_screen_capture = None
        self.internal_preview_error = ""

    def probe_display(self, display_pid, orientation):
        status = dict(value=orientation)
        try:
            self.client_command("--orienter-ecran", f"{display_pid} {orientation}", 6)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            status["error"] = ("La rotation n’a pas répondu dans le délai prévu."
                               if isinstance(error, subprocess.TimeoutExpired) else str(error))
        try:
            info = json.loads(self.client_command("--info-ecran", str(display_pid), 3))
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            info = None
        return status, info

    def refresh_display(self, display_pid, orientation):
        target = (display_pid, orientation)
        if self.display_request is not None:
            if not self.display_request.done():
                return
            status, info = self.display_request.result()
            self.display_request = None
            if target == self.display_target:
                self.orientation_report = status
                if info is not None:
                    self.display_info = info
        now = time.monotonic()
        changed = self.display_target != target
        if changed or now >= self.display_check_at:
            if self.display_target is None or self.display_target[0] != display_pid:
                self.display_info = {}
            self.display_target = target
            self.display_check_at = now + (15 if changed or not self.orientation_report
                                           or self.orientation_report.get("error") else 45)
            self.display_request = self.display_work.submit(self.probe_display, *target)

    def close(self):
        self.display_work.shutdown(wait=False, cancel_futures=True)

    def poll(self, report, display_pid=0):
        server = self.identity["server"]
        token = self.identity["token"]
        uses_internal = self.internal_preview is not None
        uses_portal = (not self.internal_preview_required and self.portal is not None
                       and self.display_info.get("portal") is True)
        monitor_capture, monitor_error = self.local_screen_capture or (self.display_info.get("capture") is True, "")
        uses_monitor = not self.internal_preview_required and monitor_capture
        report.update(kind=DEVICE_KIND, latency_ms=self.latency_ms, latency_test=self.latency_test,
                      diagnostic=self.diagnostic.sample(server), can_update=self.can_update,
                      client_version=self.version, client_release=SOFTWARE_VERSION, update_target=self.target,
                      update_has_engine=self.has_versioned_engine,
                      orientation_status=self.orientation_report,
                      display=self.display_info, display_capture=uses_internal or uses_portal or uses_monitor,
                      display_preview_kind="player" if self.internal_preview_required else "monitor",
                      display_preview_error=self.internal_preview_error if self.internal_preview_required else monitor_error,
                      capture_needs_consent=uses_portal and self.portal.stream is None)
        if self.can_update:
            report["update_result"] = self.updater.report()
        started = time.monotonic()
        try:
            reply = post(server, "/api/poll", report, token)
        except (OSError, RuntimeError, ValueError):
            self.latency_ms = None
            self.latency_test = None
            self.refresh_display(display_pid, self.orientation)
            raise
        self.latency_ms = round((time.monotonic() - started) * 1000, 1)
        if not (reply.get("reboot") or reply.get("poweroff")):
            self.orientation = options(reply["options"])["orientation"]
            self.refresh_display(display_pid, self.orientation)
        preview = reply.get("preview_request")
        if isinstance(preview, dict) and not (reply.get("reboot") or reply.get("poweroff")):
            job, source = preview.get("id"), preview.get("source")
            screen = preview.get("screen") is True
            if isinstance(job, str) and (screen or isinstance(source, str)) and job != self.preview_job:
                self.preview_job = job
                if screen and uses_internal:
                    mode = "waiting" if report["state"] != "Lecture" else "player"
                elif screen and self.internal_preview_required:
                    mode = "unavailable"
                else:
                    mode = "portal" if screen and uses_portal else "--capture-ecran" if screen else "--apercu-ndi"
                argument = str(display_pid) if screen else source
                threading.Thread(target=self.send_preview,
                                 args=(job, mode, argument, screen, preview.get("automatic") is not True),
                                 daemon=True).start()
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

    def client_command(self, mode, argument, timeout):
        python = Path(sys.executable)
        if WINDOWS and python.name.lower() == "pythonw.exe" and python.with_name("python.exe").is_file():
            python = python.with_name("python.exe")
        probe = subprocess.run(
            [str(python), "-B", str(Path(__file__).resolve()), mode, argument],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0,
        )
        if probe.returncode:
            raise RuntimeError(probe.stderr.decode("utf-8", "replace").strip()[-240:])
        return probe.stdout

    def send_preview(self, job, mode, argument, screen, allow_prompt):
        result = {"id": self.identity["id"], "job": job}
        try:
            if mode == "player":
                png = player_render_preview()
            elif mode == "unavailable":
                raise RuntimeError(self.internal_preview_error)
            elif mode == "waiting":
                png = self.internal_preview()
            elif mode == "portal":
                png = self.portal.snapshot(allow_prompt)
            else:
                png = self.client_command(mode, argument, 11 if screen else 9)
            if 0 < len(png) <= PREVIEW_LIMIT:
                result["image"] = b64encode(png).decode("ascii")
        except RuntimeError as error:
            if screen and str(error):
                result["error"] = str(error)
        except (OSError, ValueError, subprocess.SubprocessError):
            if screen:
                result["error"] = "La capture locale n’a pas abouti. Relance la capture."
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
            self.dashboard = DashboardClient(self.identity, self.settings["orientation"]) if self.identity else None
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
            wayland = (self.window.get_display().__gtype__.name == "GdkWaylandDisplay"
                       or os.environ.get("XDG_SESSION_TYPE") == "wayland")
            if self.dashboard and UBUNTU and wayland:
                self.dashboard.internal_preview_required = True
                self.prepare_internal_preview()
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

        def prepare_internal_preview(self):
            try:
                check = subprocess.run([PLAYER, "--apercu-interne"], capture_output=True, timeout=2)
                if check.returncode or check.stdout.strip() != b"apercu-interne":
                    raise RuntimeError("Moteur SDL ancien. Sur le lecteur, lance installer-ubuntu.sh --mettre-a-jour-rendu puis redémarre-le.")
                try:
                    gi.require_foreign("cairo")
                except ImportError as error:
                    raise RuntimeError("Paquet python3-gi-cairo manquant sur le lecteur. Relance installer-ubuntu.sh --mettre-a-jour-rendu.") from error
                RENDER_PREVIEW.with_suffix(".request").unlink(missing_ok=True)
                RENDER_PREVIEW.with_suffix(".rgb").unlink(missing_ok=True)
            except RuntimeError as error:
                self.dashboard.internal_preview_error = str(error)
            except (OSError, subprocess.SubprocessError):
                self.dashboard.internal_preview_error = "Moteur SDL inaccessible. Relance installer-ubuntu.sh --mettre-a-jour-rendu puis redémarre le lecteur."
            else:
                self.dashboard.internal_preview = self.waiting_preview
                self.dashboard.internal_preview_error = ""
            print(self.dashboard.internal_preview_error or "Aperçu du rendu SoundLive-NDI actif.", file=self.log)

        def waiting_preview(self):
            import cairo

            result = Future()

            def draw():
                if not result.set_running_or_notify_cancel():
                    return GLib.SOURCE_REMOVE
                try:
                    allocation = self.window.get_allocation()
                    if not self.window.get_mapped() or allocation.width < 2 or allocation.height < 2:
                        raise RuntimeError("L’écran d’attente n’est plus affiché. Relance la capture.")
                    width, height = preview_size(allocation.width, allocation.height)
                    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
                    context = cairo.Context(surface)
                    context.scale(width / allocation.width, height / allocation.height)
                    self.window.draw(context)
                    pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)
                    ok, png = pixbuf.save_to_bufferv("png", [], [])
                    if not ok:
                        raise RuntimeError("Impossible d’encoder l’écran d’attente.")
                    if png[25] != 2:
                        rgb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
                        rgb.fill(0)
                        pixbuf.composite(rgb, 0, 0, width, height, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
                        ok, png = rgb.save_to_bufferv("png", [], [])
                        if not ok:
                            raise RuntimeError("Impossible d’encoder l’écran d’attente.")
                    result.set_result(bytes(png))
                except (RuntimeError, ValueError, AttributeError, GLib.Error, cairo.Error) as error:
                    result.set_exception(RuntimeError(str(error)))
                return GLib.SOURCE_REMOVE

            GLib.idle_add(draw)
            try:
                return result.result(timeout=3)
            except TimeoutError as error:
                result.cancel()
                raise RuntimeError("Le rendu de l’écran d’attente n’a pas répondu.") from error

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
                env = None
                if self.dashboard and self.dashboard.internal_preview is not None:
                    env = {**os.environ, "SOUNDLIVE_RENDER_PREVIEW": str(RENDER_PREVIEW)}
                self.player = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT, env=env)
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
            display_pid = self.player.pid if state == "Lecture" and self.player else 0
            display = self.window.get_display()
            if DEVICE_KIND == "pi" and not self.dashboard.internal_preview_required:
                if display.__gtype__.name == "GdkX11Display":
                    self.dashboard.local_screen_capture = (True, "")
                elif not shutil.which("grim"):
                    self.dashboard.local_screen_capture = (False, "Capture Wayland : installe grim sur ce Raspberry Pi.")
                elif display.get_n_monitors() == 0:
                    self.dashboard.local_screen_capture = (False, "Aucun moniteur détecté sur ce Raspberry Pi.")
                elif display_pid > 0 and display.get_n_monitors() > 1:
                    self.dashboard.local_screen_capture = (
                        False, "Capture Wayland : la sortie de lecture est indéterminée avec plusieurs moniteurs.")
                else:
                    self.dashboard.local_screen_capture = (True, "")
                    if display.get_n_monitors() == 1:
                        display_pid = -1
            if not display_pid and self.window.get_window():
                monitor = display.get_monitor_at_window(self.window.get_window())
                display_pid = next((-index - 1 for index in range(display.get_n_monitors())
                                    if display.get_monitor(index) == monitor), 0)
            self.request = self.work.submit(self.dashboard.poll, data, display_pid)
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
                playback_changed = new != {**self.settings, "orientation": new["orientation"]}
                self.settings = new
                if playback_changed:
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
                screen.work.shutdown(wait=True, cancel_futures=True)
                if screen.dashboard:
                    screen.dashboard.close()
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
    settings = initial_settings()
    client = DashboardClient(identity, settings["orientation"]) if identity else None
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
                        playback_changed = new != {**settings, "orientation": new["orientation"]}
                        settings = new
                        if playback_changed:
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
                    error=failure, can_poweroff=True, can_reboot=True),
                    player.pid if state == "Lecture" and player else os.getpid())
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
            if client:
                client.close()
            root.destroy()
    return RESTART if restart else 0

if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--apercu-ndi":
            sys.stdout.buffer.write(capture_ndi_preview(sys.argv[2]))
        elif len(sys.argv) == 3 and sys.argv[1] in ("--info-ecran", "--capture-ecran"):
            capture = sys.argv[1] == "--capture-ecran"
            backend = display_snapshot_windows if WINDOWS else display_snapshot_linux
            value = backend(int(sys.argv[2]), capture)
            sys.stdout.buffer.write(value if capture else json.dumps(value).encode("utf-8"))
        elif len(sys.argv) == 3 and sys.argv[1] == "--orienter-ecran":
            pid, orientation = sys.argv[2].split()
            backend = orient_desktop_windows if WINDOWS else orient_desktop_linux
            sys.stdout.write("1" if backend(int(pid), orientation) else "0")
        elif len(sys.argv) == 4 and sys.argv[1] == "associer":
            pair(sys.argv[2], sys.argv[3])
        elif len(sys.argv) in (2, 3) and sys.argv[1] == "verifier":
            check_connection(sys.argv[2] if len(sys.argv) == 3 else None)
        elif len(sys.argv) == 1:
            sys.exit(windows_desktop() if WINDOWS else desktop())
        else:
            raise ValueError("Usage : ecran.py associer http://ADRESSE_DU_PC:8765 CODE"
                             " ou ecran.py verifier [http://ADRESSE_DU_PC:8765]")
    except (OSError, RuntimeError, ValueError, AttributeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
