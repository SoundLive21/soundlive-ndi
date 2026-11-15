#!/usr/bin/env python3
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
import argparse
from base64 import b64decode
import hmac
from html import escape
from ipaddress import IPv4Address
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

from acces_regie import (PORT as REMOTE_PORT, SESSION_COOKIE, SESSION_WARNING, USERNAME,
                         RemoteConnections, RemoteDashboard, prepare_access)
from commun import APP_NAME, PREVIEW_LIMIT, SOFTWARE_VERSION as CLIENT_VERSION, options, release_number
from diagnostic import NetworkRates, network_info
from mise_a_jour import SAFE_ID, contents, digest, package, prepare_key
from qrencoder import QRCode, QRErrorCorrectLevel
from versions_regie import PublicationStatus

ROOT = Path(__file__).resolve().parent
SOFTWARE_VERSION = "1.4.9"
DATA = ROOT / "ecrans.json"
REMOTE_ACCESS = ROOT / ".soundlive-acces"
PAGE = ((ROOT / "tableau.html").read_bytes()
        .replace(b"__SOFTWARE_VERSION__", SOFTWARE_VERSION.encode("ascii"))
        .replace(b"__CLIENT_VERSION__", CLIENT_VERSION.encode("ascii")))
LOGIN_PAGE = (ROOT / "acces-refuse.html").read_text(encoding="utf-8")
BLOCKED_MESSAGE = "Cette adresse IP est bloquée sur la régie. Contacte la régie pour demander le déblocage."
LOGIN_NOTICES = {
    "fermee": "Tu as été déconnecté de la régie.",
    "expiree": "La session a expiré ou a été fermée. Reconnecte-toi pour reprendre.",
}
LOCK = threading.Lock()
POWER_REQUEST_AGE = 25
ONLINE_AGE = 12
LATENCY_TEST_AGE = 15
DEVICE_KINDS = ("pi", "ubuntu", "windows")
SCREENS = {}
LATENCY_TESTS = {}
CLIENT_UPDATES = {}
UPDATE_AGE = 120
UPDATE_KEY = ROOT / ".soundlive-update" / "regie"
UPDATE_FILES = contents(ROOT)
UPDATE_DIGEST = digest(UPDATE_FILES)
RECEIVER_PATHS = {"/api/enroll", "/api/poll", "/api/client-package", "/api/preview-result"}
PREVIEWS = {}
PREVIEW_WAIT = 16
PREVIEW_FRESH = 20
PREVIEW_CLIENT = (1, 4, 7)


def preview_prune(now):
    for source in [source for source, job in PREVIEWS.items() if job["until"] <= now]:
        del PREVIEWS[source]


def preview_status(job):
    public = {"id": job["id"], "state": job["state"]}
    for field in ("image", "error", "captured"):
        if field in job:
            public[field] = job[field]
    return public


def request_preview(data, now):
    preview_prune(now)
    source = safe_text(data["source"], 256)
    online = [
        (key, row["report"]) for key, row in SCREENS.items()
        if row.get("token") and now - row.get("report", {}).get("seen", 0) < ONLINE_AGE
        and (source in row["report"].get("sources", []) or source == row["report"].get("active_source"))
    ]
    existing = PREVIEWS.get(source)
    if not online:
        return 409, {"error": "Ce flux n’est plus détecté par les lecteurs."}
    if existing and (existing["state"] != "ready" or existing["id"] != data.get("previous")):
        return 200, preview_status(existing)
    supported = [(key, report) for key, report in online
                 if release_number(report.get("client_release")) >= PREVIEW_CLIENT]
    if not supported:
        return 409, {"error": "Mets à jour un lecteur qui détecte ce flux pour afficher son aperçu."}
    busy = {job["reader"] for job in PREVIEWS.values() if job["state"] == "pending"}
    available = [(key, report) for key, report in supported if key not in busy]
    if not available or len(busy) >= 4:
        return 429, {"error": "Captures en cours. Réessaie dans quelques secondes."}
    device, _ = min(available, key=lambda item: item[1].get("active_source") != source)
    if len(PREVIEWS) >= 24:
        expired_first = min((name for name in PREVIEWS if PREVIEWS[name]["state"] != "pending"),
                            key=lambda name: PREVIEWS[name]["until"])
        del PREVIEWS[expired_first]
    job = dict(id=secrets.token_hex(12), reader=device, source=source, state="pending", until=now + PREVIEW_WAIT)
    PREVIEWS[source] = job
    return 200, preview_status(job)


def finish_preview(reader, data, now):
    preview_prune(now)
    job = next((item for item in PREVIEWS.values() if item["id"] == data.get("job")
                and item["reader"] == reader and item["state"] == "pending"), None)
    if job is None:
        return 409, {"error": "Cette capture n’est plus demandée."}
    image = data.get("image")
    if isinstance(image, str) and 0 < len(image) <= 4 * ((PREVIEW_LIMIT + 2) // 3):
        try:
            raw = b64decode(image, validate=True)
        except ValueError:
            raw = b""
        if (33 <= len(raw) <= PREVIEW_LIMIT and raw.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
                and 1 <= int.from_bytes(raw[16:20], "big") <= 240
                and 1 <= int.from_bytes(raw[20:24], "big") <= 135
                and raw[24:29] == b"\x08\x02\x00\x00\x00"):
            job.update(state="ready", image=image, captured=now, until=now + PREVIEW_FRESH)
            return 200, {"ok": True}
    job.update(state="error", error="Ce lecteur n’a pas pu capturer une image NDI.", until=now + 4)
    return 200, {"ok": True}


def waiting_access(address, port):
    url = f"https://{address}:{port}"
    code = QRCode(None, QRErrorCorrectLevel.M)
    code.addData(url)
    code.make()
    return ["".join("1" if dark else "0" for dark in row) for row in code.modules]


def update_availability(report, now, key_exists):
    if now - report.get("seen", 0) >= ONLINE_AGE:
        return "offline"
    if report.get("client_version") == UPDATE_DIGEST:
        return "current"
    if release_number(report.get("client_release")) > release_number(CLIENT_VERSION):
        return "newer"
    if not report.get("can_update"):
        return "unsupported"
    return "available" if key_exists else "key_required"


def update_view(key, report, now):
    job = CLIENT_UPDATES.get(key)
    if not job:
        return None
    result = report.get("update_result")
    if result and result["id"] == job["id"] and result["state"] == "failed":
        return result
    if report.get("client_version") == job["version"] and result and result["id"] == job["id"] and result["state"] == "updated":
        return result
    if now >= job["until"]:
        return dict(id=job["id"], state="expired", message="Pas de confirmation du récepteur.")
    if result and result["id"] == job["id"] and result["state"] == "installing":
        return result
    return dict(id=job["id"], state="queued")


def remove_screens(targets):
    for key in targets:
        del SCREENS[key]
    try:
        save()
    except OSError:
        SCREENS.update(targets)
        raise
    for key in targets:
        LATENCY_TESTS.pop(key, None)
        CLIENT_UPDATES.pop(key, None)
    for source in [source for source, job in PREVIEWS.items() if job["reader"] in targets]:
        del PREVIEWS[source]


def queue_power(row, operation, now):
    report = row.get("report", {})
    if not report.get("boot") or now - report.get("seen", 0) >= ONLINE_AGE:
        return "offline"
    if operation == "poweroff" and not report.get("can_poweroff"):
        return "unsupported"
    if operation == "reboot" and report.get("can_reboot") is False:
        return "unsupported"
    current = row.get("power", {})
    if (current.get("operation") != operation or current.get("boot") != report["boot"]
            or now - current.get("time", 0) >= POWER_REQUEST_AGE):
        row["power"] = dict(boot=report["boot"], time=now, id=secrets.token_hex(8),
                            operation=operation)
    return "requested"


def group_device_action(targets, operation, now):
    results = []
    if operation == "remove":
        remove_screens(targets)
        return [dict(id=key, name=row["name"], state="removed") for key, row in targets.items()]
    previous = {key: row.get("power") for key, row in targets.items()}
    for key, row in targets.items():
        state = queue_power(row, operation, now)
        results.append(dict(id=key, name=row["name"], state=state))
    try:
        if any(row.get("power") != previous[key] for key, row in targets.items()):
            save()
    except OSError:
        for key, row in targets.items():
            if previous[key] is None:
                row.pop("power", None)
            else:
                row["power"] = previous[key]
        raise
    return results


def batch(path, data, now):
    devices = data.get("ids")
    if not isinstance(devices, list) or not devices or len(devices) > 250 or any(not isinstance(key, str) for key in devices):
        raise ValueError("Sélectionne au moins un écran.")
    targets = {key: SCREENS.get(key) for key in devices}
    if path == "/api/screen-groups":
        if any(row is None for row in targets.values()):
            return 409, {"error": "Un écran sélectionné a été retiré du tableau."}
        name = data.get("group")
        if not isinstance(name, str) or any(not char.isprintable() for char in name):
            raise ValueError("Nom de groupe invalide.")
        name = safe_text(name) if name.strip() else None
        if name:
            name = next((row["group"] for row in SCREENS.values()
                         if row.get("group") and row["group"].casefold() == name.casefold()), name)
        previous = {key: row.get("group") for key, row in targets.items()}
        for row in targets.values():
            if name:
                row["group"] = name
            else:
                row.pop("group", None)
        try:
            save()
        except OSError:
            for key, group in previous.items():
                if group is None:
                    SCREENS[key].pop("group", None)
                else:
                    SCREENS[key]["group"] = group
            raise
        return 200, {"group": name, "count": len(targets)}
    if any(not row or "options" not in row for row in targets.values()):
        return 409, {"error": "La sélection contient un écran qui n’est plus associé."}
    results = []
    if path == "/api/group":
        operation = data.get("operation")
        if operation is not None:
            if operation not in ("reboot", "poweroff", "remove") or "changes" in data:
                raise ValueError("Action de groupe invalide.")
            return 200, {"results": group_device_action(targets, operation, now)}
        patch = data.get("changes")
        fields = {"source", "muted", "enabled", "bandwidth", "volume"}
        if not isinstance(patch, dict) or not patch or not set(patch) <= fields:
            raise ValueError("Aucun réglage de groupe valide.")
        changes = {key: options({**row["options"], **patch}) for key, row in targets.items()}
        previous = {}
        for key, row in targets.items():
            if changes[key] != row["options"]:
                previous[key] = row["options"], row["version"]
                row.update(options=changes[key], version=row["version"] + 1)
            results.append(dict(id=key, name=row["name"], state="saved",
                                online=now - row.get("report", {}).get("seen", 0) < ONLINE_AGE))
        try:
            if previous:
                save()
        except OSError:
            for key, (settings, version) in previous.items():
                SCREENS[key].update(options=settings, version=version)
            raise
    else:
        if not UPDATE_KEY.is_file():
            return 409, {"error": "Crée la clé de régie avec python tableau.py --preparer-mises-a-jour."}
        for key, row in targets.items():
            report = row.get("report", {})
            state = update_availability(report, now, key_exists=True)
            if state == "available":
                state = "queued"
                existing = update_view(key, report, now)
                if not existing or existing["state"] not in ("queued", "installing"):
                    CLIENT_UPDATES[key] = dict(id=secrets.token_hex(16), until=now + UPDATE_AGE,
                                               version=UPDATE_DIGEST, files=UPDATE_FILES)
            job = CLIENT_UPDATES.get(key)
            results.append(dict(id=key, name=row["name"], state=state,
                                job=job["id"] if state == "queued" else None))
    return 200, {"results": results}

def save():
    temporary = DATA.with_suffix(".tmp")
    temporary.write_text(json.dumps(SCREENS, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(DATA)

def token_matches(candidate, expected):
    return isinstance(candidate, str) and hmac.compare_digest(candidate.encode(), expected.encode())

def safe_text(value, limit=80):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Texte invalide.")
    return value.strip()

def bounded_number(value, maximum):
    if type(value) in (float, int) and 0 <= value <= maximum:
        return value
    return None


def diagnostic_report(payload):
    if not isinstance(payload, dict):
        return None
    report = {}
    for field in ("os", "architecture", "model", "cpu"):
        value = payload.get(field)
        if isinstance(value, str):
            report[field] = value[:160]
    for field, maximum in (("cpu_pct", 100), ("cores", 4096), ("ram_total", 2**60),
                           ("ram_used", 2**60), ("disk_total", 2**60), ("disk_free", 2**60)):
        report[field] = bounded_number(payload.get(field), maximum)
    interfaces = payload.get("interfaces")
    if isinstance(interfaces, list):
        report["interfaces"] = [
            dict(id=str(item.get("id", item.get("name", "")))[:80],
                 name=str(item.get("name", ""))[:64],
                 kind=item.get("kind") if item.get("kind") in ("wifi", "ethernet") else "other",
                 connected=item.get("connected") is True, to_regie=item.get("to_regie") is True,
                 addresses=[ip[:45] for ip in item["addresses"][:4] if isinstance(ip, str)]
                 if isinstance(item.get("addresses"), list) else [],
                 rx_mbps=bounded_number(item.get("rx_mbps"), 1_000_000),
                 tx_mbps=bounded_number(item.get("tx_mbps"), 1_000_000))
            for item in interfaces[:12] if isinstance(item, dict)
        ]
    return report


def interface_addresses():
    if sys.platform == "win32":
        # Enumerate IPv4 addresses on each active adapter, including isolated LANs.
        script = (
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
            "$rows = foreach ($adapter in "
            "[System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) { "
            "if ($adapter.OperationalStatus -ne 'Up' -or "
            "$adapter.NetworkInterfaceType -eq 'Loopback') { continue }; "
            "foreach ($entry in $adapter.GetIPProperties().UnicastAddresses) { "
            "if ($entry.Address.AddressFamily -eq 'InterNetwork' -and "
            "$entry.DuplicateAddressDetectionState -in @('Preferred', 'Deprecated')) { "
            "[PSCustomObject]@{ip=$entry.Address.IPAddressToString; label=$adapter.Name} "
            "} } }; ConvertTo-Json -InputObject @($rows | Where-Object { $_ }) -Compress"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, check=True, encoding="utf-8-sig", timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        rows = json.loads(result.stdout or "[]")
        return rows if isinstance(rows, list) else [rows]

    result = subprocess.run(["ip", "-j", "-4", "address", "show", "up"],
                            capture_output=True, check=True, text=True, timeout=2)
    return [
        dict(ip=entry["local"], label=interface["ifname"])
        for interface in json.loads(result.stdout)
        for entry in interface.get("addr_info", [])
        if entry.get("scope") in {"global", "link"}
    ]


class InterfaceTraffic:
    def __init__(self):
        self.lock = threading.Lock()
        self.rates = NetworkRates()
        self.refresh_at = 0
        self.result = {"interfaces": [], "available": False}

    def read(self):
        with self.lock:
            if time.monotonic() >= self.refresh_at:
                try:
                    counters = network_info()
                except (OSError, KeyError, ValueError, subprocess.SubprocessError):
                    counters = None
                rows = self.rates.sample(counters)
                self.result = {"interfaces": [row for row in rows or [] if row["connected"]],
                               "available": rows is not None}
                self.refresh_at = time.monotonic() + 2
            return self.result


def local_addresses():
    try:
        rows = interface_addresses()
    except (OSError, ValueError, subprocess.SubprocessError):
        rows = []
    # Connecting an unbound UDP socket selects the local route without sending data.
    preferred = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(("192.0.2.1", 9))
            preferred = route.getsockname()[0]
    except OSError:
        pass
    if not rows:
        try:
            rows = [dict(ip=ip, label=socket.gethostname())
                    for ip in socket.gethostbyname_ex(socket.gethostname())[2]]
        except OSError:
            pass
    if preferred:
        rows.append(dict(ip=preferred, label="Route par défaut"))

    found = {}
    for row in rows:
        try:
            address = IPv4Address(row["ip"])
        except (KeyError, ValueError):
            continue
        if address.is_loopback or address.is_unspecified or address.is_multicast or address.packed[0] in (0, 255):
            continue
        ip = str(address)
        found.setdefault(ip, dict(ip=ip, label=row["label"]))
    ordered = sorted(found.values(), key=lambda item: (
        IPv4Address(item["ip"]).is_link_local, item["ip"] != preferred, item["label"], item["ip"]
    ))
    return ordered

class Request(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(8)
        super().setup()

    def log_message(self, *_):
        pass

    def reply(self, status, value, *, location=None, session=None):
        if isinstance(value, bytes):
            body, content_type = value, "text/html; charset=utf-8"
        else:
            body = json.dumps(value, ensure_ascii=False).encode()
            content_type = "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if getattr(self.server, "remote_dashboard", False):
            self.send_header("Referrer-Policy", "same-origin")
        if session is not None:
            max_age = "" if session else "; Max-Age=0"
            self.send_header("Set-Cookie",
                             f"{SESSION_COOKIE}={session}; Path=/; Secure; HttpOnly; SameSite=Strict{max_age}")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def refuse_blocked(self, page):
        if page:
            self.login_page(403, blocked=True)
        else:
            self.reply(403, {"code": "remote_blocked", "error": BLOCKED_MESSAGE})

    def dashboard(self, path, write=False):
        port = self.server.server_port
        remote = getattr(self.server, "remote_dashboard", False)
        if remote:
            hosts = {f"{self.server.server_address[0]}:{port}"}
            scheme = "https"
        else:
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.client_address[0] != "127.0.0.1":
                self.reply(403, {"error": "Utilise l’adresse HTTPS affichée sur le PC de régie."})
                return False
            scheme = "http"
        host = self.headers.get("Host")
        if host not in hosts or (write and self.headers.get("Origin") != f"{scheme}://{host}"):
            self.reply(403, {"error": "Adresse d’accès au tableau non autorisée."})
            return False
        if not remote:
            return True
        address = self.client_address[0]
        connections = self.server.remote_connections
        page = path in ("/", "/connexion")
        if connections.is_blocked(address):
            self.refuse_blocked(page)
            return False
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            cookies.clear()
        cookie = cookies.get(SESSION_COOKIE)
        self.remote_session = cookie.value if cookie else None
        if path == "/connexion":
            return True
        reason, self.session_remaining = connections.observe(address, self.remote_session)
        if not reason:
            return True
        if reason == "remote_blocked":
            self.refuse_blocked(page)
        elif page:
            destination = "/connexion?etat=expiree" if cookie else "/connexion"
            self.reply(303, b"", location=destination)
        else:
            self.reply(401, {"code": reason, "error": LOGIN_NOTICES["expiree"]})
        return False

    def login_page(self, status=200, notice="", error="", blocked=False):
        description = BLOCKED_MESSAGE if blocked else "Accède au tableau de régie pour piloter tes écrans."
        self.reply(status, LOGIN_PAGE.replace("__VERSION__", SOFTWARE_VERSION)
                   .replace("__USERNAME__", escape(USERNAME))
                   .replace("__TITLE__", "Accès bloqué" if blocked else "Connexion")
                   .replace("__DESCRIPTION__", escape(description))
                   .replace("__FORM_HIDDEN__", "hidden" if blocked else "")
                   .replace("__NOTICE__", escape(notice))
                   .replace("__NOTICE_HIDDEN__", "" if notice else "hidden")
                   .replace("__ERROR__", escape(error))
                   .replace("__ERROR_HIDDEN__", "" if error else "hidden").encode("utf-8"))

    def login(self, payload):
        if self.headers.get_content_type() != "application/x-www-form-urlencoded":
            self.login_page(400, error="Utilise le formulaire de connexion.")
            return
        try:
            values = parse_qs(payload.decode("utf-8"), max_num_fields=2, strict_parsing=True,
                              errors="strict")
            username, = values["identifiant"]
            password, = values["mot_de_passe"]
        except (KeyError, ValueError):
            self.login_page(400, error="Renseigne ton identifiant et ton mot de passe.")
            return
        if not self.server.authenticate(username, password):
            self.login_page(401, error="Identifiant ou mot de passe incorrect.")
            return
        connections = self.server.remote_connections
        session = connections.issue(self.client_address[0], self.headers.get("User-Agent", ""))
        if session is None:
            self.refuse_blocked(True)
            return
        if self.remote_session:
            connections.disconnect_if_owned(self.client_address[0], self.remote_session)
        self.reply(303, b"", location="/", session=session)

    def session_status(self):
        self.reply(200, {"remaining_ms": self.session_remaining, "warning_ms": SESSION_WARNING * 1000})

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        remote = getattr(self.server, "remote_dashboard", False)
        if not self.dashboard(path):
            return
        if path == "/":
            self.reply(200, PAGE.replace(b"__REMOTE_ONLY__", b"" if remote else b"hidden"))
        elif remote and path == "/connexion":
            notice = LOGIN_NOTICES.get(url.query.removeprefix("etat="), "")
            self.login_page(notice=notice)
        elif remote and path == "/api/session":
            self.session_status()
        elif path == "/api/network":
            self.reply(200, {"addresses": local_addresses(),
                             "port": getattr(self.server, "receiver_port", self.server.server_port)})
        elif path == "/api/interface-traffic":
            self.reply(200, self.server.interface_traffic.read())
        elif path == "/api/regie-version":
            self.reply(200, self.server.publication_status.read())
        elif path == "/api/preview":
            try:
                source = parse_qs(url.query, max_num_fields=4).get("source", [""])[0]
            except ValueError:
                self.reply(400, {"error": "Source NDI invalide."})
                return
            with LOCK:
                preview_prune(time.time())
                job = PREVIEWS.get(source)
                value = preview_status(job) if job else {"state": "empty"}
            self.reply(200, value)
        elif path == "/api/remote-connections":
            connections = getattr(self.server, "remote_connections", None)
            self.reply(200, dict(enabled=connections is not None,
                                 connections=[{key: row[key] for key in ("ip", "agent", "seen")}
                                              for row in connections.snapshot()] if connections else []))
        elif path == "/api/screens":
            now = time.time()
            key_exists = UPDATE_KEY.is_file()
            with LOCK:
                rows = []
                for key, row in SCREENS.items():
                    report = row.get("report", {})
                    rows.append(dict(
                        id=key, name=row["name"], options=row.get("options"),
                        report=report, online=now - report.get("seen", 0) < ONLINE_AGE,
                        pairing=row.get("pairing") if now < row.get("pairing_until", 0) else None,
                        paired="token" in row, version=row.get("version", 0), kind=row.get("kind"),
                        group=row.get("group"),
                        client_update=update_view(key, report, now),
                        update_availability=update_availability(report, now, key_exists),
                    ))
            self.reply(200, rows)
        else:
            self.reply(404, {"error": "Page introuvable."})

    def do_POST(self):
        path = urlsplit(self.path).path
        remote = getattr(self.server, "remote_dashboard", False)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        limit = 4 * ((PREVIEW_LIMIT + 2) // 3) + 1024 if path == "/api/preview-result" else 16384
        if not 0 < length <= limit:
            self.reply(400, {"error": "Requête invalide."})
            return
        payload = self.rfile.read(length)
        if path in RECEIVER_PATHS:
            if remote:
                self.reply(404, {"error": "Les récepteurs utilisent le port d’association habituel."})
                return
        elif not self.dashboard(path, write=True):
            return
        if remote and path == "/connexion":
            self.login(payload)
            return
        try:
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("Requête invalide.")
            if remote and path == "/api/session/prolonger":
                reason, self.session_remaining = self.server.remote_connections.observe(
                    self.client_address[0], self.remote_session, renew=True)
                if reason == "remote_blocked":
                    self.refuse_blocked(False)
                elif reason:
                    self.reply(401, {"code": reason, "error": LOGIN_NOTICES["expiree"]})
                else:
                    self.session_status()
                return
            if remote and path == "/api/session/deconnexion":
                self.server.remote_connections.disconnect(self.remote_session)
                self.reply(200, {"ok": True}, session="")
                return
            with LOCK:
                status, result = self.apply(path, value)
            if path == "/api/client-package" and status == 200:
                result = package(UPDATE_KEY, **result)
        except (KeyError, ValueError, TypeError) as error:
            status, result = 400, {"error": str(error)}
        except (OSError, RuntimeError) as error:
            print(error, flush=True)
            status, result = 500, {"error": "Le serveur n’a pas pu terminer la demande."}
        self.reply(status, result)

    def apply(self, path, data):
        now = time.time()
        if path == "/api/preview":
            return request_preview(data, now)
        if path in ("/api/group", "/api/client-updates", "/api/screen-groups"):
            return batch(path, data, now)
        if path == "/api/pair":
            key = secrets.token_hex(8)
            code = secrets.token_urlsafe(9)
            kind = data.get("kind")
            if kind is not None and kind not in DEVICE_KINDS:
                raise ValueError("Choisis Raspberry Pi, Ubuntu ou Windows.")
            SCREENS[key] = dict(name=safe_text(data["name"]), kind=kind, pairing=code, pairing_until=now + 900)
            save()
            return 200, {"code": code}
        if path == "/api/enroll":
            for key, row in SCREENS.items():
                if now < row.get("pairing_until", 0) and token_matches(data.get("code"), row["pairing"]):
                    settings = options(data["options"])
                    serial = safe_text(data["serial"])
                    kind = data.get("kind")
                    if kind is None:
                        kind = "ubuntu" if serial.startswith("pc-") else "pi"
                    if kind not in DEVICE_KINDS or row.get("kind") not in (None, kind):
                        return 409, {"error": "Le type de récepteur ne correspond pas au code créé."}
                    if any(existing.get("serial") == serial for existing in SCREENS.values()):
                        return 409, {"error": "Ce récepteur est déjà associé. Retire d’abord son ancienne fiche."}
                    token = secrets.token_urlsafe(32)
                    row.update(options=settings, token=token, serial=serial, version=1, kind=kind)
                    del row["pairing"]
                    del row["pairing_until"]
                    save()
                    return 200, dict(id=key, token=token, name=row["name"])
            return 403, {"error": "Code expiré ou inconnu."}

        key = data.get("id")
        if not isinstance(key, str) or key not in SCREENS:
            return 404, {"error": "Écran inconnu.", "code": "screen_unknown"}
        row = SCREENS[key]
        if path in ("/api/poll", "/api/client-package", "/api/preview-result"):
            token = row.get("token")
            if not token or not token_matches(self.headers.get("Authorization"), "Bearer " + token):
                return 403, {"error": "Réassocie le récepteur depuis le tableau.", "code": "association_invalid"}
            if path == "/api/preview-result":
                return finish_preview(key, data, now)
            if path == "/api/client-package":
                job = CLIENT_UPDATES.get(key)
                nonce = data.get("nonce")
                if not isinstance(nonce, str) or not SAFE_ID.fullmatch(nonce):
                    raise ValueError("Identifiant de vérification invalide.")
                if not job or data.get("job") != job["id"] or now >= job["until"]:
                    return 409, {"error": "Cette mise à jour n’est plus disponible."}
                return 200, dict(device=key, job=job["id"], nonce=nonce,
                                 files=job["files"], version=job["version"])
            sources = data.get("sources", [])
            if not isinstance(sources, list):
                raise ValueError("Liste de sources invalide.")
            boot = safe_text(data["boot"])
            state = data.get("state")
            if state not in ("Lecture", "En attente", "Arrêté", "Démarrage"):
                raise ValueError("État invalide.")
            if data.get("check") is True:
                return 200, dict(options=row["options"])
            diagnostics = diagnostic_report(data.get("diagnostic"))
            kind = data.get("kind")
            if kind not in DEVICE_KINDS:
                serial = row.get("serial", "")
                kind = "ubuntu" if serial.startswith("pc-") else "pi"
            row["kind"] = row.get("kind") or kind
            test = LATENCY_TESTS.get(key)
            if test and now >= test["until"]:
                LATENCY_TESTS.pop(key)
                test = None
            answered = data.get("latency_test")
            test_result = None
            if test and isinstance(answered, dict) and answered.get("id") == test["id"]:
                test_result = dict(id=test["id"], ms=bounded_number(answered.get("ms"), 10000))
                LATENCY_TESTS.pop(key)
                test = None
            previous = row.get("report", {})
            if test_result is None and not test and previous.get("boot") == boot:
                test_result = previous.get("latency_test")
            job = CLIENT_UPDATES.get(key)
            result = data.get("update_result")
            if (isinstance(result, dict) and job and result.get("id") == job["id"]
                    and result.get("state") in ("installing", "updated", "failed")):
                result = dict(id=job["id"], state=result["state"], message=str(result.get("message", ""))[:240])
            else:
                result = None
            version = data.get("client_version")
            version = version if isinstance(version, str) and SAFE_ID.fullmatch(version) else None
            release = data.get("client_release")
            release = release if release_number(release) else None
            can_update = data.get("can_update") is True and version is not None
            active_source = data.get("active_source")
            active_source = active_source[:256] if state == "Lecture" and isinstance(active_source, str) else None
            row["report"] = dict(seen=now, ip=self.client_address[0], state=state, boot=boot,
                                 active_source=active_source,
                                 sources=[s[:256] for s in sources[:32] if isinstance(s, str)],
                                 error=str(data.get("error", ""))[:240],
                                 can_poweroff=data.get("can_poweroff") is True,
                                 can_reboot=data.get("can_reboot") is not False,
                                 diagnostic=diagnostics, latency_test=test_result,
                                 can_update=can_update, client_version=version, client_release=release,
                                 update_result=result,
                                 latency_ms=bounded_number(data.get("latency_ms"), 10000))
            power = row.get("power", {})
            pending = power.get("boot") == boot and now - power.get("time", 0) < POWER_REQUEST_AGE
            operation = power.get("operation") if pending else None
            availability = update_availability(row["report"], now, UPDATE_KEY.is_file())
            recommendation = dict(version=CLIENT_VERSION, state=availability)
            preview_prune(now)
            preview = next((dict(id=item["id"], source=item["source"]) for item in PREVIEWS.values()
                            if item["reader"] == key and item["state"] == "pending"), None)
            return 200, dict(options=row["options"],
                             preview_request=preview,
                             waiting=dict(name=row["name"], group=row.get("group"),
                                          qr=getattr(self.server, "waiting_qr", None)),
                             reader_update=recommendation,
                             reboot=power["id"] if operation == "reboot" else None,
                             poweroff=power["id"] if operation == "poweroff" else None,
                             latency_test=test["id"] if test else None,
                             client_update=job["id"] if can_update and job and result is None
                             and now < job["until"] else None)
        if path == "/api/remove":
            remove_screens({key: row})
            return 200, {"ok": True}
        elif path == "/api/latency":
            report = row.get("report", {})
            if now - report.get("seen", 0) >= ONLINE_AGE:
                return 409, {"error": "Le récepteur est hors ligne."}
            if report.get("diagnostic") is None:
                return 409, {"error": "Mets à jour le récepteur pour tester sa connexion."}
            test = LATENCY_TESTS.get(key)
            if not test or now >= test["until"]:
                test = dict(id=secrets.token_hex(8), until=now + LATENCY_TEST_AGE)
                LATENCY_TESTS[key] = test
            return 200, {"id": test["id"]}
        elif path == "/api/update" and "options" in row:
            if data.get("version") != row["version"]:
                return 409, {"error": "Réglages modifiés entre-temps. Recharge la page."}
            row["options"] = options(data["options"])
            row["version"] += 1
        elif path in ("/api/reboot", "/api/poweroff"):
            operation = "poweroff" if path == "/api/poweroff" else "reboot"
            state = queue_power(row, operation, now)
            if state == "offline":
                return 409, {"error": "Le récepteur est hors ligne."}
            if state == "unsupported":
                return 409, {"error": "Cette commande n’est pas prise en charge par le lecteur."}
        else:
            return 400, {"error": "Action indisponible."}
        save()
        return 200, {"ok": True}

class Regie:
    def __init__(self, port=8765):
        self.remote_url = None
        self.connections = None
        self.server = ThreadingHTTPServer(("0.0.0.0", port), Request)
        self.server.interface_traffic = InterfaceTraffic()
        self.server.publication_status = PublicationStatus(ROOT / "source-publications.json", SOFTWARE_VERSION)
        self.local_url = f"http://127.0.0.1:{self.server.server_port}"
        self.remote = None

    def start(self, remote_enabled=False):
        try:
            with LOCK:
                screens = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
                for row in screens.values():
                    for field in ("report", "reboot", "power"):
                        row.pop(field, None)
                SCREENS.clear()
                SCREENS.update(screens)
                CLIENT_UPDATES.clear()
                LATENCY_TESTS.clear()
                PREVIEWS.clear()
            if remote_enabled:
                self.connections = RemoteConnections(REMOTE_ACCESS / "adresses-bloquees.json")
                self.remote = RemoteDashboard(REMOTE_ACCESS, Request, self.server.server_port)
                self.remote.interface_traffic = self.server.interface_traffic
                self.server.waiting_qr = waiting_access(*self.remote.server_address)
                self.remote_url = f"https://{self.remote.server_address[0]}:{self.remote.server_port}"
                self.server.remote_connections = self.remote.remote_connections = self.connections
                self.remote.publication_status = self.server.publication_status
        except BaseException:
            if self.remote is not None:
                self.remote.server_close()
            self.server.server_close()
            raise
        if self.remote is not None:
            threading.Thread(target=self.remote.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()

    def close(self):
        for server in (self.remote, self.server):
            if server is not None:
                server.shutdown()
                server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument("--preparer-mises-a-jour", action="store_true",
                       help="créer ou réexporter la clé publique des mises à jour")
    setup.add_argument("--activer-acces-distant", metavar="IP",
                       help="préparer l’accès HTTPS sur l’IPv4 du PC de régie")
    setup.add_argument("--desactiver-acces-distant", action="store_true",
                       help="désactiver l’accès HTTPS au prochain démarrage")
    args = parser.parse_args()
    if args.activer_acces_distant:
        try:
            password, certificate = prepare_access(REMOTE_ACCESS, args.activer_acces_distant, renew=True)
        except (OSError, ValueError) as error:
            sys.exit(str(error))
        print(f"Adresse du tableau : https://{args.activer_acces_distant}:{REMOTE_PORT}")
        print(f"Identifiant : regie\nMot de passe : {password}")
        print(f"Certificat à installer sur les appareils de commande : {certificate}")
        print("Garde le mot de passe et relance le tableau.")
        return
    if args.desactiver_acces_distant:
        (REMOTE_ACCESS / "acces.json").unlink(missing_ok=True)
        print("L’accès distant sera désactivé au prochain démarrage du tableau.")
        return
    if args.preparer_mises_a_jour:
        try:
            public = prepare_key(ROOT)
        except (OSError, RuntimeError) as error:
            sys.exit(str(error))
        print(f"Clé publique à copier sur les récepteurs : {public}")
        return
    regie = Regie(args.port)
    try:
        regie.start((REMOTE_ACCESS / "acces.json").exists())
    except (OSError, ValueError, KeyError, TypeError) as error:
        if not (REMOTE_ACCESS / "acces.json").exists():
            raise
        print(f"Impossible d’ouvrir l’accès HTTPS : {error}", flush=True)
        regie = Regie(args.port)
        regie.start()
    print(f"{APP_NAME} — {regie.local_url}", flush=True)
    print(f"Fichier des écrans : {DATA}", flush=True)
    if not DATA.exists():
        print("Aucun ecrans.json dans ce dossier. Pour une mise à jour, reprends le fichier du dossier précédent.",
              flush=True)
    if regie.remote_url:
        print(f"Accès distant — {regie.remote_url}", flush=True)
    try:
        threading.Event().wait()
    finally:
        regie.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
