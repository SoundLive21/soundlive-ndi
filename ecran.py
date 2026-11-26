#!/usr/bin/python3
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
import configparser
from concurrent.futures import Future, ThreadPoolExecutor
from base64 import b64decode, b64encode
import gzip
from hashlib import sha256
from hmac import compare_digest
from http.client import HTTPException
import json
import os
import platform
import re
import secrets
import shlex
import shutil
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import threading
import time
import warnings
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from commun import (APP_NAME, COPYRIGHT, DEFAULT_SOURCE, MEDIA_CHUNK, MEDIA_DISK_PERCENT, MEDIA_EXTENSIONS, MEDIA_LIBRARY_LIMIT,
                    ORIENTATIONS, PREVIEW_LIMIT, SOFTWARE_VERSION, SSH_PAIRING_PREFIX, client_lock,
                    media_available, media_config, media_disk, media_items, media_revision, options, release_number,
                    ssh_fingerprints, ssh_pairing_proof)
from diagnostic import Diagnostic, OsUpdateCheck
from mise_a_jour import (ClientUpdate, FULL_LIMIT, IDENTIFICATION_RENDER_SOURCE, IDENTIFICATION_RUNTIME,
                         MAINTENANCE_FILES, MAINTENANCE_KIT, OS_JOB_ID, OS_NAMESPACE, RESTART, SSH_ACCESS_KEY,
                         authorize_ssh_key, digest, engine_name, maintenance_manifest,
                         ssh_keygen_path, update_target, verify_manifest)


def native_player_path(app, client_dir, filename, linux_sdl=False):
    local = app / filename
    signed = client_dir / filename
    if not signed.is_file():
        return local
    if linux_sdl and signed != local and local.is_file():
        try:
            rebuilt = (app / ".versions/moteur-local").stat().st_mtime_ns
            if rebuilt >= signed.stat().st_mtime_ns:
                return local
        except OSError:
            pass
    return signed


HOME = Path.home()
WINDOWS = sys.platform == "win32"
WINDOWS_LOCAL_DATA = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
WINDOWS_CURRENT_DATA = WINDOWS_LOCAL_DATA / "soundlive-ndi"
WINDOWS_LEGACY_DATA = WINDOWS_LOCAL_DATA / "SoundLive"
WINDOWS_DATA = WINDOWS_CURRENT_DATA
if (WINDOWS and not (WINDOWS_CURRENT_DATA / "config/association.json").is_file()
        and (WINDOWS_LEGACY_DATA / "config/association.json").is_file()):
    WINDOWS_DATA = WINDOWS_LEGACY_DATA
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
    PLAYER = native_player_path(APP, CLIENT_DIR, filename, linux_sdl=UBUNTU)
PLAYER = str(PLAYER)
if UBUNTU:
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (str(APP / "lib"), os.environ.get("LD_LIBRARY_PATH"))))
READY = STATE / "video.prete"
IDENTIFICATION_TEXT = STATE / "identification.txt"
IDENTIFICATION_TEXTURE = STATE / "identification.rgba"
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


def load_local_image(path, width, height):
    from gi.repository import GdkPixbuf

    # Appliquer l’orientation EXIF avant le calcul du cadrage.
    side = max(width, height, 1)
    return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), side, side, True).apply_embedded_orientation()


def fit_local_image(pixbuf, width, height):
    from gi.repository import GdkPixbuf

    original_width, original_height = pixbuf.get_width(), pixbuf.get_height()
    scale = min(width / original_width, height / original_height)
    size = max(1, round(original_width * scale)), max(1, round(original_height * scale))
    if size == (original_width, original_height):
        return pixbuf
    return pixbuf.scale_simple(*size, GdkPixbuf.InterpType.BILINEAR)


def local_image_preview(pixbuf, screen_size):
    from gi.repository import GdkPixbuf

    width, height = preview_size(*screen_size)
    picture = fit_local_image(pixbuf, width, height)
    picture_width, picture_height = picture.get_width(), picture.get_height()
    x, y = (width - picture_width) // 2, (height - picture_height) // 2
    frame = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    frame.fill(0)
    picture.composite(frame, x, y, picture_width, picture_height, x, y, 1, 1,
                      GdkPixbuf.InterpType.NEAREST, 255)
    ok, png = frame.save_to_bufferv("png", [], [])
    if not ok:
        raise RuntimeError("Impossible d’encoder l’aperçu de l’image locale.")
    return bytes(png)


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
        return dict(width=width, height=height, model=model, connector=monitor.name, capture=True,
                    bounds=(rect.left, rect.top, width, height))
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


def identification_labels(reply):
    details = reply.get("waiting")
    if reply.get("identify") is not True or not isinstance(details, dict):
        return None
    name = details.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = "".join(char if char.isprintable() else " " for char in name[:80]).strip()
    return (name, waiting_group(details) or "Sans groupe") if name else None


def replace_overlay_file(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def bundled_layer_shell_directory():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64") and struct.calcsize("P") == 8:
        architecture = "amd64"
    elif machine.startswith("arm") or machine == "aarch64":
        architecture = "arm64" if struct.calcsize("P") == 8 else "armhf"
    else:
        return None
    distributions = IDENTIFICATION_RUNTIME.get("files", {})
    if not distributions:
        return None
    try:
        codename = platform.freedesktop_os_release().get("VERSION_CODENAME", "")
    except (AttributeError, OSError):
        codename = ""
    files = distributions.get(codename, distributions.get("bookworm", {})).get(architecture)
    expected = {"libgtk-layer-shell.so.0", "GtkLayerShell-0.1.typelib"}
    if not files or set(files) != expected:
        return None
    revision = digest({name: value.encode("ascii") for name, value in files.items()})[:24]
    folder = STATE / "identification-gtk" / revision
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, encoded in files.items():
        data = gzip.decompress(b64decode(encoded, validate=True))
        path = folder / name
        if not path.is_file() or path.read_bytes() != data:
            replace_overlay_file(path, data)
    notice = folder / "copyright"
    if not notice.is_file():
        replace_overlay_file(notice, IDENTIFICATION_RUNTIME["copyright"].encode("utf-8"))
    return folder


def layer_shell_module(gi):
    try:
        gi.require_version("GtkLayerShell", "0.1")
    except ValueError:
        folder = bundled_layer_shell_directory()
        if folder is None:
            return None
        gi.require_version("GIRepository", "2.0")
        from gi.repository import GIRepository
        repository = GIRepository.Repository
        repository.prepend_search_path(str(folder))
        repository.prepend_library_path(str(folder))
        gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    return GtkLayerShell if GtkLayerShell.is_supported() else None


def supports_identification(program):
    try:
        check = subprocess.run([str(program), "--identification"], capture_output=True, timeout=2)
        return check.returncode == 0 and check.stdout.strip() == b"identification"
    except (OSError, subprocess.SubprocessError):
        return False


def supports_multi_outputs(program):
    try:
        result = subprocess.run([str(program), "--multi-ecrans"], capture_output=True, timeout=2)
        return result.returncode == 0 and result.stdout.strip() == b"multi-ecrans-1"
    except (OSError, subprocess.SubprocessError):
        return False


def updated_identification_player(program, log):
    if not IDENTIFICATION_RENDER_SOURCE or supports_multi_outputs(program):
        return program
    compiler = shutil.which("gcc")
    pkg_config = shutil.which("pkg-config")
    include = APP / "ndi-sdk/include"
    library = APP / "lib/libndi.so.6"
    if not compiler or not pkg_config or not (include / "Processing.NDI.Lib.h").is_file() or not library.is_file():
        print("Identification SDL : outils de compilation ou en-têtes NDI locaux indisponibles.", file=log)
        return program

    revision = sha256(IDENTIFICATION_RENDER_SOURCE).hexdigest()[:24]
    folder = STATE / "identification-rendu" / revision
    engine = folder / "lecteur-ndi-ubuntu"
    if engine.is_file() and supports_multi_outputs(engine):
        return str(engine)
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = shlex.split(subprocess.check_output([pkg_config, "--cflags", "--libs", "sdl2"],
                                                    text=True, stderr=subprocess.PIPE, timeout=2))
        with TemporaryDirectory(dir=folder) as directory:
            source = Path(directory) / "lecteur-ndi-ubuntu.c"
            pending = Path(directory) / engine.name
            source.write_bytes(IDENTIFICATION_RENDER_SOURCE)
            subprocess.run([compiler, "-O2", "-std=c11", "-I", str(include), str(source), *flags,
                            str(library), "-o", str(pending)], check=True, capture_output=True, timeout=14)
            if not supports_multi_outputs(pending):
                raise RuntimeError("Le nouveau moteur ne répond pas au test d’identification.")
            pending.replace(engine)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        details = getattr(error, "stderr", None)
        detail = details.decode(errors="replace")[-240:] if isinstance(details, bytes) else details or str(error)
        print(f"Identification SDL : {detail}", file=log)
        return program
    print(f"Moteur SDL pour l’identification prêt : {engine}", file=log)
    return str(engine)


def identification_filter():
    return (f"drawtext=textfile={IDENTIFICATION_TEXT.name}:reload=1:expansion=none:fontcolor=white:"
            "fontsize=max(11\\,min(h/32\\,w/34)):box=1:boxcolor=black@0.72:boxborderw=8:"
            "x=max(8\\,w-tw-20):y=max(8\\,h-th-20)")


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

def local_ssh_fingerprints(directory=Path("/etc/ssh")):
    fingerprints = []
    for path in sorted(directory.glob("ssh_host_*_key.pub")):
        try:
            algorithm, encoded, *_ = path.read_text(encoding="utf-8").split()
            if not re.fullmatch(r"ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)", algorithm):
                continue
            key = b64decode(encoded, validate=True)
            name_length = int.from_bytes(key[:4], "big")
            if key[4:4 + name_length] != algorithm.encode("ascii") or len(key) <= 4 + name_length:
                continue
        except (OSError, ValueError):
            continue
        fingerprint = "SHA256:" + b64encode(sha256(key).digest()).decode("ascii").rstrip("=")
        if fingerprint not in fingerprints:
            fingerprints.append(fingerprint)
    if not fingerprints:
        raise ValueError("Aucune clé d’hôte SSH lisible dans /etc/ssh. Vérifie l’installation d’OpenSSH sur le lecteur.")
    return ssh_fingerprints(fingerprints)


def pair(server, code):
    server = server_address(server)
    board = serial()
    data = dict(serial=board, kind=DEVICE_KIND, options=initial_settings())
    if not WINDOWS and code.startswith(SSH_PAIRING_PREFIX):
        if not os.environ.get("SOUNDLIVE_INSTALL_FINGERPRINT"):
            raise ValueError("Utilise la commande complète d’installation affichée par la régie.")
        import pwd
        nonce = secrets.token_hex(32)
        data.update(nonce=nonce, ssh_user=pwd.getpwuid(os.getuid()).pw_name,
                    ssh_host_fingerprints=local_ssh_fingerprints())
        proof = ssh_pairing_proof(code, "request", data)
        result = post(server, "/api/enroll", {**data, "proof": proof})
        signed = result.pop("proof", None)
        if (not isinstance(signed, str) or not re.fullmatch(r"[a-f0-9]{64}", signed)
                or not compare_digest(signed, ssh_pairing_proof(code, "response", {**result, "nonce": nonce}))):
            raise ValueError("Impossible de vérifier la réponse d’association de la régie.")
        print("Les empreintes SSH du lecteur ont été enregistrées sur la régie.")
    else:
        result = post(server, "/api/enroll", {**data, "code": code})
    write_json(PAIRING, dict(server=server, serial=board, **result))
    print("Écran associé. Ouvre une nouvelle session pour lancer le lecteur.")


class LocalMedia:
    def __init__(self, identity):
        self.identity = identity
        self.folder = STATE / "medias" / sha256((identity or {}).get("id", "").encode()).hexdigest()
        self.manifest = self.folder / "lecture.json"
        self.applied = {"enabled": False, "items": []}
        try:
            cached = read_json(self.manifest) if identity else None
            if cached is not None:
                self.applied = media_config(cached)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.desired = None
        self.catalog = None
        self.catalog_ids = None
        self.transfer = None
        self.request = None
        self.retry_at = 0
        self.error = ""
        self.play_error = ""
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.closed = threading.Event()
        self.ffplay = shutil.which("ffplay")
        if WINDOWS and (APP / "ffplay.exe").is_file():
            self.ffplay = str(APP / "ffplay.exe")
        elif not self.ffplay and not WINDOWS and not UBUNTU and (NDI / "bin/ffplay").is_file():
            self.ffplay = str(NDI / "bin/ffplay")
        self.index = 0
        self.deadline = None

    @property
    def enabled(self):
        return self.applied["enabled"]

    def path(self, item):
        return self.folder / (item["id"] + Path(item["name"]).suffix.lower())

    def receive(self, value, catalog=None):
        if value is None:
            return
        desired = media_config(value)
        library = media_items(catalog, MEDIA_LIBRARY_LIMIT) if catalog is not None else desired["items"]
        if desired != self.desired or library != self.catalog:
            self.desired = desired
            self.catalog = library
            self.catalog_ids = frozenset(item["id"] for item in library)
            self.retry_at = 0

    def report(self):
        error = self.error or self.play_error
        items = (self.desired or self.applied)["items"]
        if not error and self.needs_ffplay(items) and not self.ffplay:
            error = self.missing_ffplay()
        try:
            disk = media_disk(self.folder if self.folder.is_dir() else STATE)
        except OSError:
            disk = None
        catalog = self.catalog if self.catalog is not None else self.applied["items"]
        cached = [item["id"] for item in catalog if self.downloaded(item)]
        transfer = self.transfer
        return dict(revision=media_revision(self.applied), error=error, local=self.enabled,
                    disk=disk, cached=cached,
                    transfer=dict(id=transfer[0], received=transfer[1]) if transfer else None)

    def needs_ffplay(self, items):
        return any(WINDOWS or MEDIA_EXTENSIONS[self.path(item).suffix] != "image2" for item in items)

    @staticmethod
    def missing_ffplay():
        return ("FFplay est absent. Rends ffplay.exe accessible au lecteur."
                if WINDOWS else "FFplay est absent. Installe FFmpeg pour lire les vidéos locales.")

    def downloaded(self, item):
        path = self.path(item)
        try:
            return path.is_file() and path.stat().st_size == item["size"]
        except OSError:
            return False

    def download_wanted(self, item):
        catalog_ids = self.catalog_ids
        return not self.closed.is_set() and (catalog_ids is None or item["id"] in catalog_ids)

    def synchronize(self, items):
        self.folder.mkdir(parents=True, exist_ok=True)
        opener = build_opener(NoRedirect)
        for item in items:
            if not self.download_wanted(item):
                continue
            target = self.path(item)
            if self.downloaded(item):
                continue
            if item["size"] > media_available(media_disk(self.folder)):
                raise ValueError(f"Le fichier ferait dépasser le seuil de {MEDIA_DISK_PERCENT} % du disque du lecteur.")
            temporary = target.with_suffix(".part")
            query = urlencode({"id": self.identity["id"], "file": item["id"]})
            request = Request(self.identity["server"] + "/api/client-media?" + query,
                              headers={"Authorization": "Bearer " + self.identity["token"]})
            try:
                checksum = sha256()
                remaining = item["size"]
                self.transfer = (item["id"], 0)
                with opener.open(request, timeout=8) as response, temporary.open("wb") as handle:
                    while remaining and self.download_wanted(item):
                        data = response.read(min(MEDIA_CHUNK, remaining))
                        if not data or not self.download_wanted(item):
                            break
                        if len(data) > media_available(media_disk(self.folder)):
                            raise ValueError(f"Le disque du lecteur a atteint le seuil de {MEDIA_DISK_PERCENT} %.")
                        handle.write(data)
                        checksum.update(data)
                        remaining -= len(data)
                        self.transfer = (item["id"], item["size"] - remaining)
                if not self.download_wanted(item):
                    continue
                if remaining or checksum.hexdigest() != item["sha256"]:
                    raise ValueError("Fichier local incomplet ou différent de celui envoyé par la régie.")
                temporary.replace(target)
            finally:
                self.transfer = None
                temporary.unlink(missing_ok=True)

    def tick(self):
        now = time.monotonic()
        changed = False
        if self.request and self.request.done():
            try:
                self.request.result()
            except (OSError, HTTPException, ValueError) as error:
                self.error = f"Transfert des médias : {error}"
                self.retry_at = now + 8
            else:
                self.error = ""
            self.request = None
        desired = self.desired
        if desired is None:
            return False
        if desired != self.applied and (not desired["enabled"] or all(map(self.downloaded, desired["items"]))):
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                write_json(self.manifest, desired)
            except OSError as error:
                self.error = f"Enregistrement de la lecture locale : {error}"
            else:
                changed = self.enabled or desired["enabled"]
                self.applied = desired
                if changed:
                    self.index = 0
                    self.play_error = ""
        if not self.request and now >= self.retry_at:
            self.prune()
            selected = {item["id"] for item in desired["items"]}
            pending = [item for item in self.catalog if not self.downloaded(item)]
            if pending:
                pending.sort(key=lambda item: item["id"] not in selected)
                self.request = self.executor.submit(self.synchronize, pending)
            elif self.error and self.error.startswith("Transfert des médias :"):
                self.error = ""
            if not pending:
                self.retry_at = now + 4
        return changed

    def prune(self):
        if self.catalog is not None and self.folder.is_dir() and not self.request:
            kept = {self.path(item) for item in (*self.applied["items"], *self.catalog)}
            for path in self.folder.iterdir():
                if path.suffix in MEDIA_EXTENSIONS and path not in kept:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass

    def selected_file(self):
        item = self.applied["items"][self.index]
        path = self.path(item)
        if not path.is_file():
            raise OSError(f"Le fichier local {item['name']} est absent.")
        return path, item["name"]

    def schedule_image(self):
        self.deadline = (time.monotonic() + self.applied.get("image_seconds", 10)
                         if len(self.applied["items"]) > 1 else None)

    def command(self, settings):
        path, name = self.selected_file()
        if not self.ffplay:
            raise OSError(self.missing_ffplay())
        container = MEDIA_EXTENSIONS[path.suffix]
        image = container == "image2"
        single = len(self.applied["items"]) == 1
        if image:
            self.schedule_image()
        else:
            duration = self.applied.get("video_seconds", 0)
            self.deadline = time.monotonic() + duration if duration else None
        seek = [] if image else ["-loop", "0"] if single else ["-autoexit"]
        still = ["-pattern_type", "none"] if image else []
        volume = 0 if settings["muted"] else settings["volume"]
        return ([self.ffplay, "-hide_banner", "-nostats", "-fs", "-showmode", "video",
                 "-volume", str(volume), "-protocol_whitelist", "file", "-f", container, *still,
                 *seek, "-i", str(path)], name)

    def finished(self, code, error=None):
        if error:
            self.play_error = str(error)
        elif code:
            name = self.applied["items"][self.index]["name"]
            self.play_error = f"Impossible de lire {name}. Consulte gestion.log."
        self.index = (self.index + 1) % len(self.applied["items"])
        self.deadline = None

    def elapsed(self):
        return self.deadline is not None and time.monotonic() >= self.deadline

    def close(self):
        self.closed.set()
        self.executor.shutdown(wait=False, cancel_futures=True)


def activate_os_maintenance():
    if WINDOWS or os.geteuid() == 0:
        raise ValueError("Lance l’activation sous le compte du lecteur Linux, sans sudo devant Python.")
    if not MAINTENANCE_KIT:
        raise ValueError("Pousse le client 1.4.38 depuis la régie, puis relance l’activation.")
    with TemporaryDirectory(prefix="soundlive-maintenance-") as folder:
        for name in MAINTENANCE_FILES:
            (Path(folder) / name).write_bytes(MAINTENANCE_KIT[name])
        code = subprocess.call(["/bin/bash", "-c",
                                'source "$1/installation-lecteur-linux.sh" && install_os_maintenance "$1"',
                                "soundlive-ndi", folder])
    if code == 0:
        print("Maintenance système activée. Lance la mise à jour du système depuis la régie.")
    return code


class OsMaintenance:
    helper = Path("/usr/local/sbin/soundlive-ndi-maintenance")
    unit = Path("/etc/systemd/system/soundlive-ndi-maintenance@.service")
    results = Path("/var/lib/soundlive-ndi-maintenance")
    pending = ("waiting", "starting", "running", "rebooting")

    def __init__(self, path):
        self.path = path
        self.job = None
        try:
            record = read_json(path)
            if (isinstance(record, dict) and OS_JOB_ID.fullmatch(str(record.get("id")))
                    and isinstance(record.get("boot"), str) and isinstance(record.get("message"), str)
                    and (record.get("state") in self.pending or record.get("state") in ("updated", "failed"))
                    and (record.get("state") != "starting" or isinstance(record.get("started"), (int, float)))):
                self.job = record
        except (OSError, ValueError):
            pass

    @property
    def active(self):
        return bool(self.job and self.job["state"] in self.pending)

    def report(self):
        if self.job:
            return {key: self.job[key] for key in ("id", "state", "message")}

    def change(self, state, message=""):
        if state != self.job["state"] or message != self.job.get("message"):
            job = {**self.job, "state": state, "message": message}
            write_json(self.path, job)
            self.job = job

    def accept(self, job):
        if self.active or (self.job and self.job["id"] == job):
            return False
        record = dict(id=job, boot=BOOT, state="waiting", message="")
        write_json(self.path, record)
        self.job = record
        return True

    def start(self):
        if not self.job or self.job["state"] != "waiting":
            return
        self.job["started"] = time.monotonic()
        self.change("starting")
        try:
            proc = subprocess.run(["sudo", "-n", str(self.helper)], input=self.job["id"] + "\n",
                                  capture_output=True, text=True, timeout=4)
        except subprocess.TimeoutExpired:
            # The start may already have been queued. Wait for the service's durable result.
            return
        except OSError as error:
            self.change("failed", str(error))
            return
        if proc.returncode:
            self.change("failed", "Activation administrateur requise : " + proc.stderr.strip()[:180])

    def refresh(self):
        if not self.active:
            return
        path = self.results / self.job["id"]
        try:
            boot, state = path.read_text(encoding="ascii").split()
            written = path.stat().st_mtime
        except (OSError, ValueError):
            if self.job["boot"] != BOOT or (self.job["state"] == "starting"
                    and time.monotonic() - self.job["started"] > 45):
                self.change("failed", "Le service de mise à jour n’a pas démarré.")
            return
        if state == "rebooting":
            if boot != BOOT:
                self.change("updated")
            elif time.time() - written > 300:
                self.change("failed", "Redémarrage non confirmé après la mise à jour.")
            else:
                self.change("rebooting")
        elif state == "running" and boot == BOOT:
            self.change("running")
        elif state == "busy":
            self.change("failed", "Une autre mise à jour système est déjà en cours.")
        elif state == "reboot_failed":
            self.change("failed", "Paquets installés. Le redémarrage automatique a échoué.")
        else:
            self.change("failed", "Mise à jour système interrompue ou APT en échec. Consulte le journal du lecteur.")


class DashboardClient:
    def __init__(self, identity, orientation):
        self.identity = identity
        self.diagnostic = Diagnostic()
        self.os_update_check = OsUpdateCheck()
        self.latency_ms = None
        self.latency_test = None
        self.updater = ClientUpdate(APP, CLIENT_DIR)
        self.update_issue = None
        if "SOUNDLIVE_APP" not in os.environ:
            self.update_issue = "launcher_required"
        elif not self.updater.public.is_file():
            self.update_issue = "client_key_required"
        elif not ssh_keygen_path():
            self.update_issue = "client_openssh_required"
        self.can_update = self.update_issue is None
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

    def poll(self, report, display_pid=0, image_preview=None):
        server = self.identity["server"]
        token = self.identity["token"]
        local_video = report.get("local_video") is True
        uses_internal = self.internal_preview is not None and not local_video
        uses_portal = (image_preview is None and not self.internal_preview_required and self.portal is not None
                       and self.display_info.get("portal") is True)
        monitor_capture, monitor_error = self.local_screen_capture or (self.display_info.get("capture") is True, "")
        uses_monitor = not self.internal_preview_required and monitor_capture
        report.update(kind=DEVICE_KIND, latency_ms=self.latency_ms, latency_test=self.latency_test,
                      diagnostic=self.diagnostic.sample(server), can_update=self.can_update,
                      client_version=self.version, client_release=SOFTWARE_VERSION, update_target=self.target,
                      update_issue=self.update_issue,
                      update_has_engine=self.has_versioned_engine,
                      orientation_status=self.orientation_report,
                      display=self.display_info, display_capture=bool(image_preview or uses_internal or uses_portal or uses_monitor),
                      display_preview_kind="player" if image_preview or (self.internal_preview_required and not local_video) else "monitor",
                      display_preview_error=("" if image_preview else
                                             "Aperçu de la vidéo locale indisponible dans cette session Wayland."
                                             if local_video and self.internal_preview_required else
                                             self.internal_preview_error if self.internal_preview_required else monitor_error),
                      capture_needs_consent=uses_portal and self.portal.stream is None)
        if (report.get("os_result") or {}).get("state") not in OsMaintenance.pending:
            report["os_health"] = self.os_update_check.report()
        if self.can_update:
            report["update_result"] = self.updater.report()
        nonce = secrets.token_hex(32) if report.get("os_capability") == "ready" else None
        if nonce:
            report["os_nonce"] = nonce
        started = time.monotonic()
        try:
            reply = post(server, "/api/poll", report, token)
        except (OSError, RuntimeError, ValueError):
            self.latency_ms = None
            self.latency_test = None
            self.refresh_display(display_pid, self.orientation)
            raise
        self.latency_ms = round((time.monotonic() - started) * 1000, 1)
        job = reply.pop("os_job", None)
        reply.pop("maintenance", None)
        if nonce and isinstance(job, dict) and OS_JOB_ID.fullmatch(str(job.get("id"))) and isinstance(job.get("signature"), str):
            try:
                verify_manifest(self.updater.public, job["signature"],
                                maintenance_manifest(report["id"], job["id"], BOOT, nonce), OS_NAMESPACE)
            except (OSError, RuntimeError, ValueError):
                pass
            else:
                reply["maintenance"] = job["id"]
        if not (reply.get("reboot") or reply.get("poweroff")):
            self.orientation = options(reply["options"])["orientation"]
            self.refresh_display(display_pid, self.orientation)
        preview = reply.get("preview_request")
        if isinstance(preview, dict) and not (reply.get("reboot") or reply.get("poweroff")):
            job, source = preview.get("id"), preview.get("source")
            screen = preview.get("screen") is True
            if isinstance(job, str) and (screen or isinstance(source, str)) and job != self.preview_job:
                self.preview_job = job
                argument = str(display_pid) if screen else source
                if screen and image_preview:
                    mode, argument = "local-image", image_preview
                elif screen and uses_internal:
                    mode = "waiting" if report["state"] != "Lecture" else "player"
                elif screen and self.internal_preview_required:
                    mode = "unavailable"
                else:
                    mode = "portal" if screen and uses_portal else "--capture-ecran" if screen else "--apercu-ndi"
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
            elif mode == "local-image":
                png = local_image_preview(*argument)
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
            self.media = LocalMedia(self.identity)
            self.connection_status = None
            self.regie_state = None
            self.sources = []
            self.player = None
            self.outputs = []
            self.extra_settings = {}
            self.extra_players = {}
            self.extra_retry = {}
            self.extra_waiting = {}
            self.output_request = None
            self.next_outputs = 0
            self.active_source = None
            self.playing_local = False
            self.local_pixbuf = None
            self.local_image_size = None
            self.local_map_deadline = 0
            self.video_ready = False
            self.stop_at = None
            self.retry_at = 0
            self.misses = 0
            self.error = ""
            self.discovery_error = ""
            self.power_id = None
            self.maintenance = OsMaintenance(STATE / "maintenance-os.json")
            self.maintenance.refresh()
            self.work = ThreadPoolExecutor(max_workers=1)
            self.request = None
            self.restart_client = False
            self.waiting_data = None

            self.window = Gtk.Window(title=APP_NAME, decorated=False)
            self.window.connect("destroy", lambda *_: Gtk.main_quit())
            self.window.connect("key-press-event", self.key_press)
            wayland = (self.window.get_display().__gtype__.name == "GdkWaylandDisplay"
                       or os.environ.get("XDG_SESSION_TYPE") == "wayland")
            self.video_engine = PLAYER
            css = Gtk.CssProvider()
            css.load_from_data(b"""
                window {background-image:linear-gradient(145deg,#230c11,#100e11 46%,#08080a);}
                .local-image {background:#000;}
                label {color:white;}
                .line {background:#d93249;border-radius:3px;min-height:5px;}
                .waiting-status {color:#dfcbd1;font-size:22px;}
                .reader-group {color:#ffc0cd;font-size:18px;}
                .reader-regie {font-size:15px;}
                .qr-caption {color:#f0dce2;font-size:16px;}
                .reader-version {color:#dfcbd1;font-size:16px;}
                .reader-update {color:#ffc78a;font-size:18px;}
                window.identification-window {background:transparent;}
                .screen-identification {background:rgba(18,15,19,.80);border-radius:9px;padding:9px 13px;}
                .screen-identification .name {font-size:18px;font-weight:bold;}
                .screen-identification .group {font-size:14px;color:#ffd4dc;}
            """)
            Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), css,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_halign(Gtk.Align.CENTER)
            box.set_valign(Gtk.Align.CENTER)
            box.set_border_width(16)
            self.pages = Gtk.Stack()
            self.waiting_box = box
            self.pages.add_named(box, "waiting")
            layer = Gtk.Overlay()
            layer.add(self.pages)
            self.window.add(layer)
            self.identification = None
            self.identification_state = {"visible": False, "error": ""}
            self.identification_output = None
            self.identification_files = (None, None)
            self.identification_probes = {}
            self.identification_monitor = None
            self.identification_reason = ""
            self.identification_badge = self.make_identification_badge()
            badge, _, _ = self.identification_badge
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.END)
            badge.set_margin_end(18)
            badge.set_margin_bottom(18)
            layer.add_overlay(badge)
            layer.set_overlay_pass_through(badge, True)
            self.identification_popup = self.make_identification_popup(wayland)
            if UBUNTU:
                self.video_engine = updated_identification_player(PLAYER, self.log)
            if self.dashboard and UBUNTU and wayland:
                self.dashboard.internal_preview_required = True
                self.prepare_internal_preview()
            replace_overlay_file(IDENTIFICATION_TEXT, b"")
            IDENTIFICATION_TEXTURE.unlink(missing_ok=True)
            self.image_page = Gtk.Layout()
            self.image_page.get_style_context().add_class("local-image")
            self.local_image = Gtk.Image()
            self.image_page.put(self.local_image, 0, 0)
            self.image_page.connect("size-allocate", self.resize_local_image)
            self.pages.add_named(self.image_page, "image")
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
            self.output_label = Gtk.Label(label="Écran 1", no_show_all=True)
            self.output_label.get_style_context().add_class("reader-group")
            box.pack_start(self.output_label, False, False, 0)
            self.group_label = Gtk.Label(no_show_all=True)
            self.group_label.set_line_wrap(True)
            self.group_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.group_label.set_max_width_chars(44)
            self.group_label.set_justify(Gtk.Justification.CENTER)
            self.group_label.get_style_context().add_class("reader-group")
            box.pack_start(self.group_label, False, False, 0)
            self.title = Gtk.Label()
            self.title.get_style_context().add_class("waiting-status")
            self.title.set_line_wrap(True)
            self.title.set_justify(Gtk.Justification.CENTER)
            self.title.set_max_width_chars(56)
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

        def make_identification_badge(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, no_show_all=True)
            box.get_style_context().add_class("screen-identification")
            labels = []
            for role in ("name", "group"):
                label = Gtk.Label(xalign=0)
                label.get_style_context().add_class(role)
                label.set_line_wrap(True)
                label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(35)
                box.pack_start(label, False, False, 0)
                label.show()
                labels.append(label)
            return box, *labels

        def make_identification_popup(self, wayland):
            layer_shell = None
            if wayland:
                try:
                    layer_shell = layer_shell_module(gi)
                except (ImportError, OSError, RuntimeError, ValueError, GLib.Error) as error:
                    print(f"Surcouche GTK pour l’identification : {error}", file=self.log)
                if layer_shell is None:
                    return None
            popup = Gtk.Window(type=Gtk.WindowType.TOPLEVEL if wayland else Gtk.WindowType.POPUP,
                               decorated=False, accept_focus=False, focus_on_map=False)
            popup.get_style_context().add_class("identification-window")
            visual = popup.get_screen().get_rgba_visual()
            if visual:
                popup.set_visual(visual)
            popup.connect("realize", lambda widget: widget.get_window().set_pass_through(True))
            if layer_shell:
                layer_shell.init_for_window(popup)
                layer_shell.set_layer(popup, layer_shell.Layer.OVERLAY)
                for edge in (layer_shell.Edge.BOTTOM, layer_shell.Edge.RIGHT):
                    layer_shell.set_anchor(popup, edge, True)
                    layer_shell.set_margin(popup, edge, 18)
            badge = self.make_identification_badge()
            popup.add(badge[0])
            return popup, badge, layer_shell

        def render_identification_texture(self, labels):
            import cairo
            gi.require_foreign("cairo")
            gi.require_version("PangoCairo", "1.0")
            from gi.repository import PangoCairo
            layouts = [self.window.create_pango_layout(value) for value in labels]
            for layout, font in zip(layouts, ("Sans Bold 18", "Sans 14")):
                layout.set_font_description(Pango.FontDescription(font))
                if layout.get_pixel_size()[0] > 450:
                    layout.set_width(450 * Pango.SCALE)
                    layout.set_ellipsize(Pango.EllipsizeMode.END)
            dimensions = [layout.get_pixel_size() for layout in layouts]
            width = max(item[0] for item in dimensions) + 26
            height = sum(item[1] for item in dimensions) + 20
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            context = cairo.Context(surface)
            context.set_source_rgba(.07, .06, .075, .80)
            context.paint()
            top = 9
            for index, layout in enumerate(layouts):
                context.move_to(13, top)
                colour = (1, 1, 1) if index == 0 else (1, .83, .87)
                context.set_source_rgb(*colour)
                PangoCairo.show_layout(context, layout)
                top += dimensions[index][1] + 2
            pixels = Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)
            if pixels is None or not pixels.get_has_alpha():
                raise RuntimeError("Impossible de préparer l’incrustation du lecteur.")
            stride = pixels.get_rowstride()
            rows = pixels.get_pixels()
            bitmap = b"".join(rows[start:start + width * 4] for start in range(0, stride * height, stride))
            header = struct.pack("<III", width, height, secrets.randbits(32))
            replace_overlay_file(IDENTIFICATION_TEXTURE, header + bitmap)

        def refresh_identification(self):
            labels = None if self.maintenance.active else self.identification
            waiting = self.window.get_visible()
            route = self.identification_output
            popup = self.identification_popup
            visible = False
            error = ""
            try:
                text = labels if route == "text" else None
                texture = labels if route == "texture" else None
                old_text, old_texture = self.identification_files
                if text != old_text:
                    message = "\n".join(textwrap.fill(line, 32) for line in text) if text else ""
                    replace_overlay_file(IDENTIFICATION_TEXT, message.encode("utf-8"))
                if texture != old_texture:
                    if texture:
                        self.render_identification_texture(texture)
                    else:
                        IDENTIFICATION_TEXTURE.unlink(missing_ok=True)
                self.identification_files = text, texture
                badge, name, group = self.identification_badge
                badge.set_visible(bool(labels and waiting))
                if labels and waiting:
                    name.set_text(labels[0])
                    group.set_text(labels[1])
                    visible = True
                if popup:
                    window, (box, name, group), layer_shell = popup
                    if labels and not waiting:
                        resize = not window.get_visible() or (name.get_text(), group.get_text()) != labels
                        if resize:
                            name.set_text(labels[0])
                            group.set_text(labels[1])
                            box.show()
                            # Wayland can allocate the label's minimum instead of its natural width.
                            box.set_size_request(-1, -1)
                            width = box.get_preferred_width()[1]
                            height = box.get_preferred_height_for_width(width)[1]
                            box.set_size_request(width, -1)
                            window.resize(width, height)
                        display = self.window.get_display()
                        monitor = display.get_monitor_at_window(self.window.get_window()) if self.window.get_window() else None
                        if monitor is None:
                            monitor = display.get_primary_monitor()
                        if monitor and layer_shell and monitor != self.identification_monitor:
                            layer_shell.set_monitor(window, monitor)
                            self.identification_monitor = monitor
                        elif monitor and not layer_shell:
                            area = monitor.get_geometry()
                            width = box.get_preferred_width()[1]
                            height = box.get_preferred_height_for_width(width)[1]
                            window.move(area.x + area.width - width - 18, area.y + area.height - height - 18)
                        window.show()
                        visible = True
                    else:
                        window.hide()
                elif labels and not waiting:
                    visible = bool(route)
                    if not visible:
                        error = self.identification_reason or "Identification indisponible avec ce moteur de lecture."
            except (OSError, RuntimeError, ImportError, AttributeError, ValueError, GLib.Error) as failure:
                error = str(failure)
            self.identification_state = {"visible": visible, "error": error}

        def identification_video(self, command, native):
            self.identification_reason = ""
            if self.identification_popup:
                return None
            program = command[0]
            signature = (program, native)
            if signature not in self.identification_probes:
                try:
                    if native:
                        supported = supports_identification(program)
                    else:
                        probe = subprocess.run([program, "-filters"], stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT, timeout=3)
                        supported = not probe.returncode and re.search(
                            rb"(?m)^\s*(?:[.A-Z]+\s+)?drawtext(?:\s|$)", probe.stdout)
                except (OSError, subprocess.SubprocessError):
                    supported = False
                self.identification_probes[signature] = supported
            if not self.identification_probes[signature]:
                self.identification_reason = ("Le moteur SDL actif doit être mis à jour pour l’identification." if native else
                    "FFplay ne fournit pas drawtext et la surcouche du bureau est indisponible.")
                print(f"Identification : {self.identification_reason} Moteur : {program}", file=self.log)
                return None
            if native:
                return "texture"
            command[1:1] = ["-vf", identification_filter()]
            return "text"

        def waiting(self):
            text = ("En attente d’un média local" if self.media.enabled else "En attente du flux vidéo")
            if self.maintenance.active:
                text = "Mise à jour système, veuillez patienter.\nLe système redémarrera automatiquement."
            elif not self.settings["enabled"]:
                text = "Diffusion arrêtée"
            self.title.set_text(text)
            primary = next((o for o in self.outputs if o["id"] == "0"), None)
            monitor = self.output_monitor(primary) if primary else None
            if monitor is not None:
                self.window.fullscreen_on_monitor(self.window.get_screen(), monitor)
            else:
                self.window.fullscreen()
            self.window.show_all()
            self.pages.set_visible_child(self.waiting_box)

        def resize_local_image(self, _widget, allocation):
            size = allocation.width, allocation.height
            if self.local_pixbuf is not None and min(size) > 1 and self.local_image_size != size:
                self.local_image_size = size
                self.image_page.set_size(*size)
                fitted = fit_local_image(self.local_pixbuf, *size)
                self.local_image.set_from_pixbuf(fitted)
                self.image_page.move(self.local_image, (size[0] - fitted.get_width()) // 2,
                                     (size[1] - fitted.get_height()) // 2)

        def show_local_image(self, path, name):
            display = self.window.get_display()
            window = self.window.get_window()
            monitor = display.get_monitor_at_window(window) if window else display.get_primary_monitor()
            allocation = monitor.get_geometry() if monitor else self.window.get_allocation()
            self.local_pixbuf = load_local_image(path, allocation.width, allocation.height)
            self.local_image_size = None
            self.resize_local_image(self.image_page, allocation)
            self.active_source = name
            self.playing_local = True
            self.video_ready = False
            self.local_map_deadline = time.monotonic() + 5
            self.media.play_error = self.error = ""
            self.window.fullscreen()
            self.pages.set_visible_child(self.image_page)
            self.window.present()

        def prepare_internal_preview(self):
            try:
                check = subprocess.run([self.video_engine, "--apercu-interne"], capture_output=True, timeout=2)
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
            if self.media.enabled:
                GLib.timeout_add_seconds(5, self.probe)
                return GLib.SOURCE_REMOVE
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
                if (self.settings["enabled"] and not self.media.enabled and self.player is None
                        and GLib.get_monotonic_time() >= self.retry_at):
                    self.start()
            elif self.player is not None and not self.playing_local and not UBUNTU:
                self.misses += 1
                if self.misses >= 2:
                    self.terminate()
            GLib.timeout_add_seconds(5, self.probe)

        def discover_outputs(self):
            result = subprocess.run([self.video_engine, "--displays"], capture_output=True,
                                    text=True, timeout=4)
            if result.returncode:
                return []
            outputs = []
            for line in result.stdout.splitlines():
                index, x, y, width, height = map(int, line.split())
                if 0 <= index < 8 and 0 < width <= 16384 and 0 < height <= 16384:
                    outputs.append(dict(id=str(index), x=x, y=y, width=width, height=height))
            return outputs

        def actual_monitor(self):
            # SDL reports the actual display rectangle; match it to GTK, not
            # the numeric index, which can differ across display backends.
            try:
                _, x, y, width, height = map(int, READY.read_text().split())
            except (OSError, ValueError):
                return None
            display = self.window.get_display()
            for index in range(display.get_n_monitors()):
                monitor = display.get_monitor(index)
                rect = monitor.get_geometry()
                if (rect.x, rect.y, rect.width, rect.height) == (x, y, width, height):
                    return -index - 1
            return None

        def stop_extra(self, index):
            entry = self.extra_players.pop(index, None)
            if entry:
                process = entry[0]
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)

        def output_monitor(self, output):
            display = self.window.get_display()
            for index in range(display.get_n_monitors()):
                rect = display.get_monitor(index).get_geometry()
                if (rect.x, rect.y, rect.width, rect.height) == tuple(output[k] for k in ("x", "y", "width", "height")):
                    return index
            return None

        def sync_waiting_outputs(self):
            multiple = len(self.outputs) > 1
            self.output_label.set_visible(multiple)
            active = {o["id"]: o for o in self.outputs if o["id"] != "0"} if multiple else {}
            for index in list(self.extra_waiting):
                if index not in active:
                    self.extra_waiting.pop(index)[0].destroy()
            for index, output in active.items():
                monitor = self.output_monitor(output)
                if monitor is None:
                    if index in self.extra_waiting:
                        self.extra_waiting.pop(index)[0].destroy()
                    continue
                if index not in self.extra_waiting:
                    window = Gtk.Window(decorated=False, accept_focus=False, focus_on_map=False)
                    window.connect("key-press-event", self.key_press)
                    panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                    panel.set_halign(Gtk.Align.CENTER)
                    panel.set_valign(Gtk.Align.CENTER)
                    window.add(panel)
                    brand = Gtk.Label()
                    brand.set_markup(f'<span size="46000" weight="bold">{APP_NAME}</span>')
                    panel.pack_start(brand, False, False, 0)
                    labels = [Gtk.Label() for _ in range(6)]
                    for widget, style in zip(labels, ("reader-group", "reader-group", "waiting-status", "reader-regie", "reader-version", "reader-version")):
                        widget.get_style_context().add_class(style)
                        widget.set_line_wrap(True)
                        panel.pack_start(widget, False, False, 0)
                    qr = Gtk.Image()
                    panel.pack_start(qr, False, False, 0)
                    self.extra_waiting[index] = (window, labels, qr)
                window, labels, qr = self.extra_waiting[index]
                selected = self.extra_settings.get(index, {})
                entry = self.extra_players.get(index)
                if entry and entry[0].poll() is None and entry[2].exists():
                    window.hide()
                    continue
                texts = (self.screen_name.get_text(), "Écran " + str(int(index) + 1),
                         "En attente du flux vidéo" if selected.get("enabled") else "Diffusion arrêtée",
                         self.regie_label.get_text(), f"Version du lecteur {SOFTWARE_VERSION}", COPYRIGHT)
                for widget, value in zip(labels, texts):
                    widget.set_text(value)
                qr.set_from_pixbuf(self.qr_image.get_pixbuf())
                if not window.get_visible():
                    window.fullscreen_on_monitor(window.get_screen(), monitor)
                    window.show_all()

        def sync_outputs(self):
            if not UBUNTU:
                return
            now = time.monotonic()
            if self.output_request and self.output_request.done():
                try:
                    self.outputs = self.output_request.result()
                except (OSError, ValueError, subprocess.SubprocessError):
                    self.outputs = []
                self.output_request = None
            if not self.output_request and now >= self.next_outputs:
                self.output_request = self.work.submit(self.discover_outputs)
                self.next_outputs = now + 10
            wanted = {}
            if (not self.maintenance.active and not self.media.enabled and
                    self.settings["enabled"] and len(self.outputs) > 1):
                for output in self.outputs:
                    index = output["id"]
                    selected = self.extra_settings.get(index, {})
                    if index == "0" or selected.get("enabled") is not True:
                        continue
                    source = self.settings["source"] if selected.get("mode") == "same" else selected.get("source")
                    if source in self.sources:
                        wanted[index] = (source, selected.get("bandwidth", self.settings["bandwidth"]), output["x"], output["y"],
                                         output["width"], output["height"], 0 if selected.get("muted", True) else selected.get("volume", 0))
            for index, (process, config, path) in list(self.extra_players.items()):
                if wanted.get(index) != config or process.poll() is not None:
                    self.stop_extra(index)
                    self.extra_retry[index] = now + 3
                elif path.exists():
                    try:
                        actual = tuple(map(int, path.read_text().split()))
                        if len(actual) == 5 and actual != (int(index), *config[2:6]):
                            self.stop_extra(index)
                            self.extra_retry[index] = now + 15
                            self.error = "Le compositeur Wayland n’a pas placé la sortie secondaire demandée."
                    except (OSError, ValueError):
                        pass
            for index, config in wanted.items():
                if index in self.extra_players or now < self.extra_retry.get(index, 0):
                    continue
                path = STATE / ("video-sortie-" + index + ".prete")
                try:
                    path.unlink(missing_ok=True)
                    process = subprocess.Popen(
                        [self.video_engine, config[0], config[1], str(config[6]), str(path)],
                        stdout=self.log, stderr=subprocess.STDOUT,
                        env={**os.environ, "SOUNDLIVE_DISPLAY_INDEX": index})
                    self.extra_players[index] = (process, config, path)
                except OSError as error:
                    self.error = str(error)[:240]
                    self.extra_retry[index] = now + 5

        def start(self):
            if self.maintenance.active:
                return
            volume = 0 if self.settings["muted"] else self.settings["volume"]
            command = [
                PLAYER, "-hide_banner", "-nostats", "-fs", "-framedrop",
                "-volume", str(volume), "-f", "libndi_newtek", "-wait_sources", "3",
                "-bandwidth", self.settings["bandwidth"], "-i", self.settings["source"],
            ]
            try:
                local = self.media.enabled
                source = self.settings["source"]
                if local:
                    path, name = self.media.selected_file()
                    if MEDIA_EXTENSIONS[path.suffix] == "image2":
                        self.show_local_image(path, name)
                        return
                    command, source = self.media.command(self.settings)
                elif UBUNTU:
                    READY.unlink(missing_ok=True)
                    command = [self.video_engine, self.settings["source"], self.settings["bandwidth"],
                               str(volume), str(READY)]
                self.identification_output = self.identification_video(command, UBUNTU and not local)
                env = None
                if self.identification_output == "texture":
                    env = {**os.environ, "SOUNDLIVE_IDENTIFICATION_TEXTURE": str(IDENTIFICATION_TEXTURE)}
                if not local and self.dashboard and self.dashboard.internal_preview is not None:
                    env = {**(env or os.environ), "SOUNDLIVE_RENDER_PREVIEW": str(RENDER_PREVIEW)}
                if UBUNTU and not local and self.outputs:
                    env = {**(env or os.environ), "SOUNDLIVE_DISPLAY_INDEX": "0"}
                self.player = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT, env=env,
                                               cwd=STATE if self.identification_output == "text" else None)
                self.active_source = source
                self.playing_local = local
                self.video_ready = local or not UBUNTU
                self.error = ""
                if local:
                    self.media.play_error = ""
            except (OSError, GLib.Error) as error:
                self.error = str(error)
                if self.media.enabled:
                    self.media.finished(1, error)
                    self.retry_at = GLib.get_monotonic_time() + 2 * GLib.USEC_PER_SEC
                print(error, file=self.log)
                return
            if self.video_ready:
                self.window.hide()

        def terminate(self):
            if self.local_pixbuf is not None:
                self.local_pixbuf = None
                self.local_image.clear()
                self.active_source = None
                self.playing_local = self.video_ready = False
            if self.player is not None and self.player.poll() is None and self.stop_at is None:
                self.player.terminate()
                self.stop_at = GLib.get_monotonic_time() + 3 * GLib.USEC_PER_SEC

        def check(self):
            maintaining = self.maintenance.active
            self.maintenance.refresh()
            if maintaining and not self.maintenance.active:
                self.waiting()
            if not self.maintenance.active and self.media.tick():
                self.terminate()
                self.waiting()
                if self.player is None:
                    self.media.prune()
            if self.player is not None:
                now = GLib.get_monotonic_time()
                status = self.player.poll()
                if status is not None:
                    if self.playing_local and self.media.enabled and self.stop_at is None:
                        self.media.finished(status)
                    elif not self.playing_local and UBUNTU and status > 0 and self.stop_at is None:
                        self.error = "Le lecteur NDI a quitté avec une erreur. Consulte gestion.log."
                    self.player = self.stop_at = self.identification_output = None
                    self.active_source = None
                    self.playing_local = False
                    self.video_ready = False
                    self.retry_at = now if self.media.enabled and status <= 0 else now + 2 * GLib.USEC_PER_SEC
                    self.waiting()
                    self.media.prune()
                elif self.stop_at is not None:
                    if now >= self.stop_at:
                        self.player.kill()
                elif UBUNTU and not self.video_ready and READY.exists():
                    self.video_ready = True
                    self.error = ""
                    self.window.hide()
                elif self.playing_local and self.media.elapsed():
                    self.media.finished(0)
                    self.terminate()
            if self.local_pixbuf is not None:
                if not self.video_ready:
                    if self.image_page.get_mapped() and self.local_image.get_mapped() and self.local_image_size:
                        self.video_ready = True
                        self.media.schedule_image()
                        print(f"Image locale affichée : {self.active_source}", file=self.log)
                    elif time.monotonic() >= self.local_map_deadline:
                        error = "Impossible d’afficher la fenêtre de l’image locale."
                        self.media.finished(1, error)
                        print(error, file=self.log)
                        self.terminate()
                        self.waiting()
                        self.retry_at = GLib.get_monotonic_time() + 2 * GLib.USEC_PER_SEC
                elif self.media.elapsed():
                    self.media.finished(0)
                    self.terminate()
                    self.waiting()
            if self.maintenance.active and self.player is None and self.local_pixbuf is None:
                self.maintenance.start()
            if (not self.maintenance.active and self.media.enabled and self.settings["enabled"]
                    and self.player is None and self.local_pixbuf is None
                    and GLib.get_monotonic_time() >= self.retry_at):
                self.start()
            self.sync_outputs()
            self.sync_waiting_outputs()
            self.refresh_identification()
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
            capability = ("setup_required" if not (self.maintenance.helper.is_file() and self.maintenance.unit.is_file())
                          else "ready" if self.dashboard.can_update else "key_required")
            data = dict(id=self.identity["id"], boot=BOOT, state=state, sources=self.sources,
                        os_capability=capability, os_result=self.maintenance.report(),
                        active_source=self.active_source if state == "Lecture" else None,
                        error=self.error, can_poweroff=True, media_status=self.media.report(),
                        local_video=self.playing_local, identification=self.identification_state,
                        outputs=[{**output, "playing": output["id"] in self.extra_players and
                                  self.extra_players[output["id"]][2].exists()} for output in self.outputs])
            display_pid = self.player.pid if state == "Lecture" and self.player else 0
            display = self.window.get_display()
            if UBUNTU and display_pid > 0 and not self.playing_local and display.__gtype__.name != "GdkX11Display":
                actual = self.actual_monitor()
                if actual is not None:
                    display_pid = actual
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
            picture = (self.local_pixbuf, self.local_image_size) if self.local_pixbuf is not None and self.video_ready else None
            self.request = self.work.submit(self.dashboard.poll, data, display_pid, picture)
            return GLib.SOURCE_CONTINUE

        def apply(self, result):
            job = result.get("maintenance")
            if job and self.maintenance.accept(job):
                self.terminate()
                self.waiting()
            new = options(result["options"])
            self.extra_settings = result.get("extra_outputs") or {}
            self.media.receive(result.get("media"), result.get("media_catalog"))
            message = waiting_update_message(result.get("reader_update"))
            self.update_label.set_text(message)
            self.update_label.set_visible(bool(message))
            if self.show_waiting_details(result.get("waiting")):
                save_waiting_details(self.identity, self.waiting_data, self.log)
            self.identification = identification_labels(result)
            self.refresh_identification()
            if new != self.settings:
                write_json(LAST_SETTINGS, new)
                playback_changed = new != {**self.settings, "orientation": new["orientation"]}
                self.settings = new
                if playback_changed:
                    self.terminate()
                    self.waiting()
            if self.maintenance.active:
                return
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
            if SSH_ACCESS_KEY:
                try:
                    authorize_ssh_key(SSH_ACCESS_KEY)
                except (OSError, RuntimeError, ValueError) as error:
                    print(f"Autorisation SSH de la régie : {error}", file=log)
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
                for window, _, _ in screen.extra_waiting.values():
                    window.destroy()
                for index in list(screen.extra_players):
                    screen.stop_extra(index)
                if screen.player:
                    try:
                        screen.player.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        screen.player.kill()
                        screen.player.wait()
                screen.work.shutdown(wait=True, cancel_futures=True)
                if screen.dashboard:
                    screen.dashboard.close()
                screen.media.close()
                IDENTIFICATION_TEXTURE.unlink(missing_ok=True)
                IDENTIFICATION_TEXT.unlink(missing_ok=True)
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
    import ctypes as c
    from ctypes import wintypes as w

    try:
        identity = association()
    except (OSError, ValueError) as error:
        identity = None
        print(error, file=log)
    settings = initial_settings()
    client = DashboardClient(identity, settings["orientation"]) if identity else None
    media = LocalMedia(identity)
    user = c.windll.user32
    if not hasattr(user, "SetProcessDpiAwarenessContext") or not user.SetProcessDpiAwarenessContext(c.c_void_p(-4)):
        user.SetProcessDPIAware()
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
    playing_local = False
    local_name = ""
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
    displays = []
    extra_settings = {}
    extra_players = {}
    next_displays = 0

    badge = tk.Toplevel(root, background="#17141a")
    badge.withdraw()
    badge.overrideredirect(True)
    badge.attributes("-topmost", True)
    badge.attributes("-alpha", .83)
    badge_labels = tuple(tk.Label(badge, justify="left", background="#17141a", fg=colour,
                                  font=font, wraplength=420) for colour, font in
                         (("white", ("Segoe UI", 16, "bold")), ("#ffd4dc", ("Segoe UI", 12))))
    for label, padding in zip(badge_labels, ((8, 1), (1, 8))):
        label.pack(anchor="w", padx=13, pady=padding)
    for function, result, args in (
        ("GetAncestor", w.HWND, (w.HWND, w.UINT)),
        ("GetWindowLongW", w.LONG, (w.HWND, c.c_int)),
        ("SetWindowLongW", w.LONG, (w.HWND, c.c_int, w.LONG)),
        ("SetWindowPos", w.BOOL, (w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)),
    ):
        getattr(user, function).restype, getattr(user, function).argtypes = result, args
    identification = None
    identification_state = {"visible": False, "error": ""}
    badge_target = None
    next_badge_check = 0

    def refresh_identification(now):
        nonlocal badge_target, next_badge_check, identification_state
        if not identification:
            badge.withdraw()
            badge_target = None
            identification_state = {"visible": False, "error": ""}
            return
        pid = player.pid if player and ready else 0
        target = identification, pid
        if badge_target == target and now < next_badge_check:
            return
        badge_target, next_badge_check = target, now + 1
        try:
            for label, text in zip(badge_labels, identification):
                label.configure(text=text)
            badge.update_idletasks()
            hwnd = user.GetAncestor(badge.winfo_id(), 2) or badge.winfo_id()
            style = user.GetWindowLongW(hwnd, -20)
            # Allow clicks to reach the player and keep the current keyboard focus.
            user.SetWindowLongW(hwnd, -20, style | 0x080000A0)
            try:
                left, top, width, height = display_snapshot_windows(pid, False)["bounds"]
            except RuntimeError:
                left, top, width, height = display_snapshot_windows(0, False)["bounds"]
            badge.deiconify()
            badge.update_idletasks()
            x = left + max(0, width - badge.winfo_width() - 18)
            y = top + max(0, height - badge.winfo_height() - 18)
            if not user.SetWindowPos(hwnd, w.HWND(-1), x, y, 0, 0, 0x0011):
                raise RuntimeError("Impossible de placer l’identification sur le moniteur.")
        except (OSError, RuntimeError, ValueError, tk.TclError) as error:
            badge.withdraw()
            identification_state = {"visible": False, "error": str(error)}
        else:
            identification_state = {"visible": True, "error": ""}

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
        text = "En attente d’un média local" if media.enabled else "En attente du flux vidéo"
        status.configure(text=text if settings["enabled"] else "Diffusion arrêtée")
        root.deiconify()
        root.attributes("-fullscreen", True)

    def stop_player():
        nonlocal ready, player, playing_local
        if player and player.poll() is None:
            player.terminate()
            player.wait(timeout=4)
        player = None
        playing_local = False
        ready = False

    def discover():
        result = subprocess.run([PLAYER, "--sources"], capture_output=True, encoding="utf-8",
                                errors="replace", timeout=10, creationflags=flags)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Découverte NDI indisponible.")
        return [line for line in result.stdout.splitlines() if line]

    def stop_extra(index):
        entry = extra_players.pop(index, None)
        if entry:
            process = entry[0]
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)

    def detect_displays():
        result = subprocess.run([PLAYER, "--displays"], capture_output=True, text=True,
                                timeout=4, creationflags=flags)
        if result.returncode:
            return []
        found = []
        for line in result.stdout.splitlines():
            index, x, y, width, height = map(int, line.split())
            if 0 <= index < 8 and width > 0 and height > 0:
                found.append(dict(id=str(index), width=width, height=height, x=x, y=y))
        return found

    def sync_extra():
        wanted = {}
        if not media.enabled and settings["enabled"] and len(displays) > 1:
            for output in displays:
                index = output["id"]
                if index == "0":
                    continue
                selected = extra_settings.get(index, {})
                if selected.get("enabled") is not True:
                    continue
                source = settings["source"] if selected.get("mode") == "same" else selected.get("source")
                if source and source in sources:
                    wanted[index] = (source, selected.get("bandwidth", settings["bandwidth"]),
                                     0 if selected.get("muted", True) else selected.get("volume", 0))
        for index, (process, config, path) in list(extra_players.items()):
            if wanted.get(index) != config or process.poll() is not None:
                stop_extra(index)
        for index, config in wanted.items():
            if index in extra_players:
                continue
            path = STATE / ("video-sortie-" + index + ".prete")
            path.unlink(missing_ok=True)
            process = subprocess.Popen([PLAYER, config[0], config[1], str(config[2]), str(path)],
                stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                env={**os.environ, "SOUNDLIVE_DISPLAY_INDEX": index})
            extra_players[index] = (process, config, path)

    def update():
        nonlocal request, discovery, next_poll, next_probe, player, settings, sources
        nonlocal failure, restart, ready, power_id, retry_at
        nonlocal playing_local, local_name, identification, displays, extra_settings, next_displays
        now = time.monotonic()
        try:
            if now >= next_displays:
                next_displays = now + 10
                try:
                    displays = detect_displays()
                except (OSError, ValueError, subprocess.SubprocessError):
                    displays = []
            if media.tick():
                stop_player()
                media.prune()
            if request and request.done():
                finished, request = request, None
                try:
                    answer = finished.result()
                    new = options(answer["options"])
                    extra_settings = answer.get("extra_outputs") or {}
                    media.receive(answer.get("media"), answer.get("media_catalog"))
                    if new != settings:
                        write_json(LAST_SETTINGS, new)
                        playback_changed = new != {**settings, "orientation": new["orientation"]}
                        settings = new
                        if playback_changed:
                            stop_player()
                    if show_waiting_details(answer.get("waiting")):
                        save_waiting_details(identity, waiting_data, log)
                    identification = identification_labels(answer)
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
                if playing_local:
                    media.finished(player.returncode)
                    retry_at = now + (2 if player.returncode else 0)
                else:
                    if player.returncode:
                        failure = f"Le lecteur NDI a quitté avec le code {player.returncode}. Consulte gestion.log."
                    retry_at = now + 3
                    sources = []
                stop_player()
            if player and playing_local and media.elapsed():
                media.finished(0)
                stop_player()
            if player and not ready and READY.exists():
                ready = True
                root.withdraw()
            if not player and now >= retry_at and settings["enabled"]:
                if media.enabled:
                    try:
                        command, local_name = media.command(settings)
                        player = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
                    except OSError as error:
                        print(error, file=log)
                        media.finished(1, error)
                        retry_at = now + 2
                    else:
                        playing_local = ready = True
                        media.play_error = ""
                        root.withdraw()
                elif settings["source"] in sources:
                    READY.unlink(missing_ok=True)
                    volume = 0 if settings["muted"] else settings["volume"]
                    player = subprocess.Popen([PLAYER, settings["source"], settings["bandwidth"], str(volume), str(READY)],
                                              stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                                              env={**os.environ, "SOUNDLIVE_DISPLAY_INDEX": "0"} if displays else None)
            sync_extra()
            if not ready:
                show_waiting()
            refresh_identification(now)
            if not media.enabled and not discovery and now >= next_probe:
                discovery = work.submit(discover)
                next_probe = now + 5
            if not request and now >= next_poll:
                state = "Arrêté" if not settings["enabled"] else "Lecture" if ready else "En attente"
                source = local_name if playing_local else settings["source"]
                request = work.submit(client.poll, dict(id=identity["id"], boot=BOOT, state=state,
                    sources=sources, active_source=source if ready else None,
                    error=failure, can_poweroff=True, can_reboot=True,
                    local_video=playing_local, media_status=media.report(), identification=identification_state,
                    outputs=[{**output, "playing": output["id"] in extra_players and extra_players[output["id"]][2].exists()}
                             for output in displays]),
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
            for index in list(extra_players):
                stop_extra(index)
        finally:
            work.shutdown(wait=True, cancel_futures=True)
            if client:
                client.close()
            media.close()
            root.destroy()
    return RESTART if restart else 0

if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--activer-maintenance-os"]:
            sys.exit(activate_os_maintenance())
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
