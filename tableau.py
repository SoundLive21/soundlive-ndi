#!/usr/bin/env python3
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
import argparse
from base64 import b64decode
from contextlib import contextmanager
from getpass import getuser
from hashlib import sha256
import hmac
from html import escape
from ipaddress import IPv4Address
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

from acces_regie import (PORT as REMOTE_PORT, SESSION_COOKIE, SESSION_WARNING, USERNAME,
                         RemoteConnections, RemoteDashboard, prepare_access)
from commun import (APP_NAME, MEDIA_CHUNK, MEDIA_COUNT_LIMIT, MEDIA_DISK_PERCENT, MEDIA_FILE_LIMIT, MEDIA_FOLDER_LIMIT,
                    MEDIA_LIBRARY_LIMIT, MEDIA_TOTAL_LIMIT,
                    ORIENTATIONS, PREVIEW_LIMIT, SOFTWARE_VERSION as CLIENT_VERSION, SSH_PAIRING_PREFIX,
                    media_available, media_config, media_disk, media_name, media_revision, media_timing,
                    options, release_number, ssh_fingerprints, ssh_pairing_proof)
from diagnostic import Diagnostic, NetworkRates, OsUpdateCheck, desktop_protocol, desktop_services, network_info, os_update_report
from installation_lecteur import KEYS_PATH, installation_keys, prepare_installations, ssh_access_public
from mise_a_jour import (CLIENT_UPDATE_ISSUES, ENGINE_ASSETS, OS_NAMESPACE, SAFE_ID, OpenSSHError, contents,
                         digest, engine_name, full_contents, maintenance_manifest, package, prepare_key, sign_manifest)
from qrencoder import QRCode, QRErrorCorrectLevel
from terminal_ssh import GROUP_LIMIT, USER as SSH_USER, HostKeyCheck, Terminals
from versions_regie import PublicationStatus

ROOT = Path(__file__).resolve().parent
SOFTWARE_VERSION = "1.4.92"
RESOLUTION_CONTROLS_ENABLED = False
PREVIEW_AUTO_INTERVAL = 30
DATA = ROOT / "ecrans.json"
SCREEN_ORDER_FILE = ROOT / "disposition-ecrans.json"
MEDIA_ROOT = ROOT / ".soundlive-medias"
MEDIA_WRITE_LOCK = threading.Lock()
MEDIA_DEFAULT_FOLDER = "general"
MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4",
    ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska",
}
REMOTE_ACCESS = ROOT / ".soundlive-acces"
PAGE = ((ROOT / "tableau.html").read_bytes()
        .replace(b"__SOFTWARE_VERSION__", SOFTWARE_VERSION.encode("ascii"))
        .replace(b"__CLIENT_VERSION__", CLIENT_VERSION.encode("ascii"))
        .replace(b"__MEDIA_DISK_PERCENT__", str(MEDIA_DISK_PERCENT).encode("ascii"))
        .replace(b"__RESOLUTION_CONTROLS_ENABLED__", str(RESOLUTION_CONTROLS_ENABLED).lower().encode("ascii"))
        .replace(b"__PREVIEW_INTERVAL__", str(PREVIEW_AUTO_INTERVAL).encode("ascii")))
LOGIN_PAGE = (ROOT / "acces-refuse.html").read_text(encoding="utf-8")
TERMINAL_PAGE = (ROOT / "terminal.html").read_bytes()
BLOCKED_MESSAGE = "Cette adresse IP est bloquée sur la régie. Contacte la régie pour demander le déblocage."
LOGIN_NOTICES = {
    "fermee": "Tu as été déconnecté de la régie.",
    "expiree": "La session a expiré ou a été fermée. Reconnecte-toi pour reprendre.",
}
LOCK = threading.Lock()
INSTALLATION_KEYS_LOCK = threading.Lock()
POWER_REQUEST_AGE = 25
ONLINE_AGE = 12
RESOLUTIONS = ("360p", "720p", "1080p", "4K")
RESOLUTION_SUFFIX = re.compile(
    r"(?:^|\s)(?:\[(360p|720p|1080p|4K)\]|(360p|720p|1080p|4K))$", re.IGNORECASE)
LATENCY_TEST_AGE = 15
DEVICE_KINDS = ("pi", "ubuntu", "windows")
SCREENS = {}
SCREEN_ORDER = {"groups": [], "screens": {}}
MEDIA_EXPORTS = {}
LATENCY_TESTS = {}
CLIENT_UPDATES = {}
UPDATE_AGE = 120
OS_STATES = ("queued", "waiting", "starting", "running", "rebooting", "updated", "failed")
UPDATE_KEY = ROOT / ".soundlive-update" / "regie"
UPDATE_FILES = contents(ROOT)
UPDATE_DIGEST = digest(UPDATE_FILES)
def build_full_updates(ssh_public=""):
    updates = {}
    for target in ENGINE_ASSETS:
        try:
            files = full_contents(ROOT, target, release=True, ssh_public=ssh_public)
        except (OSError, ValueError) as error:
            print(f"Paquet client {target} : {error}", file=sys.stderr)
            continue
        updates[target] = (files, digest(files))
    return updates


try:
    FULL_UPDATES = build_full_updates(ssh_access_public(ROOT))
except (OSError, ValueError) as error:
    print(f"Clé publique SSH de la régie : {error}", file=sys.stderr)
    FULL_UPDATES = build_full_updates()

RECEIVER_PATHS = {"/api/enroll", "/api/poll", "/api/client-package", "/api/preview-result"}
PREVIEWS = {}
PREVIEW_WAIT = 16
PREVIEW_CONSENT_WAIT = 90
PREVIEW_FRESH = 40
PREVIEW_CLIENT = (1, 4, 7)
DISPLAY_PREVIEW_CLIENT = (1, 4, 8)


def source_resolution(source):
    host, opening, name = source.partition(" (")
    if host and opening and name.endswith(")"):
        source = name[:-1]
    match = RESOLUTION_SUFFIX.search(source.rstrip())
    if not match:
        return None
    return (match[1] or match[2]).lower().replace("4k", "4K")


def resolution_sources(row, now):
    limit = row.get("resolution_limit")
    report = row.get("report", {})
    available = {resolution: [] for resolution in RESOLUTIONS}
    if limit in RESOLUTIONS and now - report.get("seen", 0) < ONLINE_AGE:
        for source in dict.fromkeys(report.get("sources", [])):
            resolution = source_resolution(source)
            if resolution and RESOLUTIONS.index(resolution) <= RESOLUTIONS.index(limit):
                available[resolution].append(source)
    return available


def resolution_rejection(row, settings, now, requested):
    if not RESOLUTION_CONTROLS_ENABLED:
        return "Les réglages de résolution sont temporairement désactivés." if requested is not None else None
    resolution = source_resolution(settings["source"])
    if requested is not None and (requested not in RESOLUTIONS or requested != resolution):
        raise ValueError("La source ne correspond pas à la résolution choisie.")
    if requested is not None and settings["bandwidth"] != "0":
        raise ValueError("Choisis High pour recevoir la résolution annoncée par la source.")
    changed = (settings["source"] != row["options"]["source"]
               or settings["bandwidth"] != row["options"]["bandwidth"])
    if resolution and (changed or requested is not None):
        if settings["source"] not in resolution_sources(row, now)[resolution]:
            return f"« {row['name']} » ne détecte plus cette source ou sa limite validée est insuffisante."
    return None


def preview_prune(now):
    for source in [source for source, job in PREVIEWS.items() if job["until"] <= now]:
        del PREVIEWS[source]


def preview_status(job):
    public = {"id": job["id"], "state": job["state"]}
    for field in ("image", "error", "captured", "consent"):
        if field in job:
            public[field] = job[field]
    return public


def preview_target(data):
    if "screen" in data:
        return ("screen", safe_text(data["screen"])), None
    source = safe_text(data["source"], 256)
    return ("source", source), source


def request_preview(data, now):
    preview_prune(now)
    target, source = preview_target(data)
    screen = target[0] == "screen"
    online = [
        (key, row["report"]) for key, row in SCREENS.items()
        if row.get("token") and now - row.get("report", {}).get("seen", 0) < ONLINE_AGE
        and (key == target[1] if screen else
             source in row["report"].get("sources", []) or source == row["report"].get("active_source"))
    ]
    existing = PREVIEWS.get(target)
    if not online:
        return 409, {"error": "Ce lecteur est hors ligne." if screen else "Ce flux n’est plus détecté par les lecteurs."}
    if screen and online[0][1].get("display_capture") is not True:
        return 409, {"error": "La capture de l’affichage est indisponible sur ce lecteur."}
    automatic = data.get("automatic") is True
    consent = screen and online[0][1].get("capture_needs_consent") is True
    if automatic and consent:
        return 409, {"error": "Autorise le partage sur le lecteur Ubuntu avant de lancer la capture automatique."}
    if existing:
        if existing["state"] != "ready":
            return 200, preview_status(existing)
        if automatic and now - existing["captured"] < PREVIEW_AUTO_INTERVAL:
            return 200, preview_status(existing)
        if not automatic and not consent and existing["id"] != data.get("previous"):
            return 200, preview_status(existing)
    minimum = DISPLAY_PREVIEW_CLIENT if screen else PREVIEW_CLIENT
    supported = [(key, report) for key, report in online
                 if release_number(report.get("client_release")) >= minimum]
    if not supported:
        return 409, {"error": "Mets à jour un lecteur qui détecte ce flux pour afficher son aperçu."}
    busy = {job["reader"] for job in PREVIEWS.values() if job["state"] == "pending"}
    available = [(key, report) for key, report in supported if key not in busy]
    auto_busy = automatic and any(job["state"] == "pending" and job.get("automatic") for job in PREVIEWS.values())
    if not available or len(busy) >= 4 or auto_busy:
        return 429, {"error": "Captures en cours. Réessaie dans quelques secondes."}
    device, _ = min(available, key=lambda item: item[1].get("active_source") != source)
    if existing is None and len(PREVIEWS) >= 24:
        expired_first = min((name for name in PREVIEWS if PREVIEWS[name]["state"] != "pending"),
                            key=lambda name: PREVIEWS[name]["until"])
        del PREVIEWS[expired_first]
    job = dict(id=secrets.token_hex(12), reader=device, source=source, screen=screen, state="pending",
               automatic=automatic, consent=consent, until=now + (PREVIEW_CONSENT_WAIT if consent else PREVIEW_WAIT))
    PREVIEWS[target] = job
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
    error = data.get("error")
    if not isinstance(error, str) or not error or not job["screen"]:
        error = ("Ce lecteur n’a pas pu capturer son affichage." if job["screen"] else
                 "Ce lecteur n’a pas pu capturer une image NDI.")
    job.update(state="error", error=error[:240], until=now + 4)
    return 200, {"ok": True}


def waiting_access(address, port):
    url = f"https://{address}:{port}"
    code = QRCode(None, QRErrorCorrectLevel.M)
    code.addData(url)
    code.make()
    return ["".join("1" if dark else "0" for dark in row) for row in code.modules]


def update_payload(report):
    if report.get("update_target") is None:
        return UPDATE_FILES, UPDATE_DIGEST
    return FULL_UPDATES.get(report["update_target"])


def update_availability(report, now, key_exists):
    if now - report.get("seen", 0) >= ONLINE_AGE:
        return "offline"
    if release_number(report.get("client_release")) > release_number(CLIENT_VERSION):
        return "newer"
    payload = update_payload(report)
    if payload and report.get("client_version") == payload[1]:
        return "current"
    if not report.get("can_update"):
        issue = report.get("update_issue")
        return issue if issue in CLIENT_UPDATE_ISSUES else "unsupported"
    if payload is None or (report.get("update_has_engine") and engine_name(report.get("update_target")) not in payload[0]):
        return "package_missing"
    return "available" if key_exists else "key_required"


def update_view(key, report, now):
    job = CLIENT_UPDATES.get(key)
    if not job:
        return None
    result = report.get("update_result")
    if result and result["id"] == job["id"]:
        if result["state"] == "failed":
            return result
        if result["state"] == "updated":
            if report.get("client_version") == job["version"]:
                return result
            return dict(id=job["id"], state="failed",
                        message="Le lecteur a répondu, mais son empreinte diffère du paquet envoyé.")
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
        if targets[key].get("media"):
            shutil.rmtree(MEDIA_ROOT / key, ignore_errors=True)
    for source in [source for source, job in PREVIEWS.items() if job["reader"] in targets]:
        del PREVIEWS[source]


def os_status(row, now):
    job = row.get("os_update")
    if job and job["state"] == "queued" and now >= job["until"]:
        return {**job, "state": "failed", "message": "Le lecteur n’a pas reçu la demande."}
    return job


def os_busy(row, now):
    return (os_status(row, now) or {}).get("state") in OS_STATES[:5]


def os_updates(targets, now):
    before = {}
    results = []
    for key, row in targets.items():
        report = row.get("report", {})
        state = report.get("os_capability", "unsupported")
        current = row.get("power", {})
        client_update = update_view(key, report, now) or {}
        if not report.get("boot") or now - report.get("seen", 0) >= ONLINE_AGE:
            state = "offline"
        elif os_busy(row, now) or client_update.get("state") in ("queued", "installing") or (
                current.get("boot") == report["boot"] and now - current.get("time", 0) < POWER_REQUEST_AGE):
            state = "maintenance"
        elif state == "ready":
            if UPDATE_KEY.is_file():
                before[key] = row.get("os_update")
                row["os_update"] = dict(id=secrets.token_hex(16), boot=report["boot"],
                                        until=now + UPDATE_AGE, state="queued", message="")
                state = "queued"
            else:
                state = "key_required"
        results.append(dict(id=key, name=row["name"], state=state))
    if before:
        try:
            save()
        except OSError:
            for key, job in before.items():
                if job is None:
                    targets[key].pop("os_update", None)
                else:
                    targets[key]["os_update"] = job
            raise
    return results


def os_poll(row, data, boot, now):
    job = row.get("os_update")
    if not job or job["state"] in ("updated", "failed"):
        return None
    result = data.get("os_result")
    if isinstance(result, dict) and result.get("id") == job["id"] and result.get("state") in OS_STATES[1:]:
        state = result["state"]
        if state == "updated" and boot == job["boot"]:
            return None
        if state in OS_STATES[1:5] and boot != job["boot"]:
            state = "failed"
            result = {"message": "Le lecteur a redémarré avant la fin de la mise à jour."}
        update = {**job, "state": state, "message": str(result.get("message") or "")[:240]}
        if update != job:
            row["os_update"] = update
            try:
                save()
            except OSError:
                row["os_update"] = job
                raise
        return None
    if (job["state"] == "queued" and now < job["until"] and job["boot"] == boot
            and data.get("os_capability") == "ready"):
        nonce = data.get("os_nonce")
        if isinstance(nonce, str) and SAFE_ID.fullmatch(nonce):
            return {"id": job["id"]}
    return None


def queue_power(row, operation, now):
    report = row.get("report", {})
    if not report.get("boot") or now - report.get("seen", 0) >= ONLINE_AGE:
        return "offline"
    if os_busy(row, now):
        return "maintenance"
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
    if path == "/api/os-updates":
        return 200, {"results": os_updates(targets, now)}
    if path == "/api/group":
        operation = data.get("operation")
        if operation is not None:
            if operation not in ("reboot", "poweroff", "remove") or "changes" in data:
                raise ValueError("Action de groupe invalide.")
            return 200, {"results": group_device_action(targets, operation, now)}
        patch = data.get("changes")
        fields = {"source", "muted", "enabled", "bandwidth", "volume", "orientation"}
        if not isinstance(patch, dict) or not patch or not set(patch) <= fields:
            raise ValueError("Aucun réglage de groupe valide.")
        changes = {key: options({**row["options"], **patch}) for key, row in targets.items()}
        for key, row in targets.items():
            error = resolution_rejection(row, changes[key], now, data.get("resolution"))
            if error:
                return 409, {"error": error}
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
            state = "maintenance" if os_busy(row, now) else update_availability(report, now, key_exists=True)
            if state == "available":
                state = "queued"
                existing = update_view(key, report, now)
                if not existing or existing["state"] not in ("queued", "installing"):
                    files, version = update_payload(report)
                    CLIENT_UPDATES[key] = dict(id=secrets.token_hex(16), until=now + UPDATE_AGE,
                                               version=version, files=files)
            job = CLIENT_UPDATES.get(key)
            results.append(dict(id=key, name=row["name"], state=state,
                                job=job["id"] if state == "queued" else None))
    return 200, {"results": results}

def enroll_reader(data, now):
    code = data.get("code")
    proof = data.get("proof")
    nonce = data.get("nonce")
    request = {name: value for name, value in data.items() if name != "proof"}
    protected_request = (code is None and isinstance(proof, str) and SAFE_ID.fullmatch(proof)
                         and isinstance(nonce, str) and SAFE_ID.fullmatch(nonce))
    for key, row in SCREENS.items():
        if now >= row.get("pairing_until", 0):
            continue
        secret = row["pairing"]
        protected_code = row.get("exchange_ssh") is True
        if protected_code:
            if token_matches(code, secret):
                # An older kit sent the protected code itself. Invalidate it immediately.
                updated = row.copy()
                updated["pairing_until"] = 0
                SCREENS[key] = updated
                try:
                    save()
                except OSError:
                    SCREENS[key] = row
                    raise
                return 409, {"error": "Utilise le kit 1.4.77 puis génère un nouveau code d’association."}
            if not protected_request or not hmac.compare_digest(proof, ssh_pairing_proof(secret, "request", request)):
                continue
            fingerprints = ssh_fingerprints(data.get("ssh_host_fingerprints"))
            ssh_user = data.get("ssh_user")
            if not isinstance(ssh_user, str) or not SSH_USER.fullmatch(ssh_user):
                raise ValueError("Nom du compte SSH du lecteur invalide.")
        else:
            if not token_matches(code, secret):
                continue
            fingerprints = None
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
        updated = {**row, "options": settings, "token": token, "serial": serial, "version": 1, "kind": kind}
        for field in ("pairing", "pairing_until", "installation_fingerprint", "exchange_ssh"):
            updated.pop(field, None)
        if fingerprints:
            updated.update(ssh_host_fingerprints=fingerprints, ssh_user=ssh_user)
        result = dict(id=key, token=token, name=row["name"])
        if protected_code:
            result["proof"] = ssh_pairing_proof(secret, "response", {**result, "nonce": nonce})
        SCREENS[key] = updated
        try:
            save()
        except OSError:
            SCREENS[key] = row
            raise
        return 200, result
    return 403, {"error": "Code expiré ou inconnu."}


def save():
    temporary = DATA.with_suffix(".tmp")
    temporary.write_text(json.dumps(SCREENS, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(DATA)


def order_screens(data):
    scope, order = data.get("scope"), data.get("order")
    if not isinstance(order, list) or any(not isinstance(value, str) for value in order):
        raise ValueError("Ordre d’affichage invalide.")
    if scope == "groups":
        expected = {row.get("group") or "" for row in SCREENS.values()}
        layout = {**SCREEN_ORDER, "groups": order}
    elif scope == "screens" and isinstance(data.get("group"), str):
        group = data["group"]
        expected = {key for key, row in SCREENS.items() if (row.get("group") or "") == group}
        layout = {**SCREEN_ORDER, "screens": {**SCREEN_ORDER["screens"], group: order}}
    else:
        raise ValueError("Disposition invalide.")
    if not order or len(order) != len(expected) or set(order) != expected:
        return 409, {"error": "La liste des écrans a changé. Réessaie avec la liste à jour."}
    temporary = SCREEN_ORDER_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SCREEN_ORDER_FILE)
    SCREEN_ORDER.update(layout)
    return 200, {"ok": True}


def load_screen_order():
    try:
        layout = json.loads(SCREEN_ORDER_FILE.read_text(encoding="utf-8"))
        groups = layout["groups"]
        screens = layout["screens"]
        if not isinstance(screens, dict):
            raise ValueError("Disposition invalide.")
        if not all(isinstance(order, list) and all(isinstance(key, str) for key in order)
                   for order in (groups, *screens.values())):
            raise ValueError("Disposition invalide.")
        return {"groups": groups, "screens": screens}
    except FileNotFoundError:
        return {"groups": [], "screens": {}}
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Disposition des écrans : {error}", file=sys.stderr)
        return {"groups": [], "screens": {}}


def screen_media(row):
    state = row.get("media", {"enabled": False, "items": []})
    folder = media_folders(row)[row.get("media_folder", MEDIA_DEFAULT_FOLDER)]
    return dict(enabled=state["enabled"], items=folder["items"], **folder.get("timing", {}))


def media_folders(row):
    return row.get("media_folders") or {MEDIA_DEFAULT_FOLDER: dict(
        name="Général", items=row.get("media", {}).get("items", []))}


def catalog_items(row):
    return [item for folder in media_folders(row).values() for item in folder["items"]]


def save_media(row, folders, active, enabled, *, resume=False, empty=False):
    fields = ("media", "media_folders", "media_folder", "media_generation")
    previous = {field: row[field] for field in fields if field in row}
    settings, version = row["options"], row["version"]
    row.update(media_folders=folders, media_folder=active,
               media=media_config(dict(enabled=enabled, items=folders[active]["items"], **folders[active].get("timing", {}))))
    if empty:
        row["media_generation"] = row.get("media_generation", 0) + 1
    if resume and not settings["enabled"]:
        row.update(options={**settings, "enabled": True}, version=version + 1)
    try:
        save()
    except OSError:
        row.update(options=settings, version=version)
        for field in fields:
            row.pop(field, None)
        row.update(previous)
        raise


def media_capacity(row):
    report = row.get("report", {}).get("media_status") or {}
    disk = report.get("disk")
    if not disk:
        return None
    cached = set(report.get("cached", []))
    pending = sum(item["size"] for item in catalog_items(row) if item["id"] not in cached)
    return {**disk, "available": max(0, media_available(disk) - pending), "pending": pending}


def media_quota(row, folder, length):
    items = folder["items"]
    if len(items) >= MEDIA_COUNT_LIMIT or sum(item["size"] for item in items) + length > MEDIA_TOTAL_LIMIT:
        raise ValueError("Un dossier peut contenir jusqu’à 20 fichiers et 1 Gio.")
    if len(catalog_items(row)) >= MEDIA_LIBRARY_LIMIT:
        raise ValueError("Ce lecteur a atteint la limite de 128 fichiers.")
    disk = media_capacity(row)
    if disk is None:
        raise ValueError("Espace disque du lecteur inconnu. Connecte un lecteur à jour avant d’ajouter des fichiers.")
    if length > disk["available"]:
        raise ValueError(f"Ce fichier ferait dépasser le seuil de {MEDIA_DISK_PERCENT} % sur le lecteur.")


def media_location(folders, identifier):
    for folder, data in folders.items():
        for index, item in enumerate(data["items"]):
            if item["id"] == identifier:
                return folder, index, item
    raise ValueError("Ce média ne figure plus dans la médiathèque.")


@contextmanager
def media_stream(stream, key, identifier):
    try:
        with stream:
            yield
    finally:
        # Windows peut refuser l’effacement tant qu’un aperçu tient le fichier ouvert.
        with LOCK:
            row = SCREENS.get(key)
            if row is None or not any(item["id"] == identifier for item in catalog_items(row)):
                try:
                    (MEDIA_ROOT / key / identifier).unlink(missing_ok=True)
                except OSError:
                    pass


def media_bytes_range(value, size):
    if value is None:
        return 0, size - 1
    match = re.fullmatch(r"bytes=(\d{0,15})-(\d{0,15})", value)
    if not match or not any(match.groups()):
        raise ValueError("Plage invalide.")
    lo, hi = match.groups()
    if not lo and int(hi) > 0:
        return max(0, size - int(hi)), size - 1
    if lo and int(lo) < size and (not hi or int(hi) >= int(lo)):
        return int(lo), min(int(hi), size - 1) if hi else size - 1
    raise ValueError("Plage invalide.")

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
    if payload.get("desktop_protocol") in ("vnc", "rdp"):
        report["desktop_protocol"] = payload["desktop_protocol"]
    services = payload.get("desktop_services")
    if isinstance(services, dict):
        report["desktop_services"] = {
            protocol: port for protocol, port in services.items()
            if (protocol == "rdp" and type(port) is int and port in (3389, 3390))
            or (protocol == "vnc" and type(port) is int and port == 5900)
        }
    for field in ("hostname", "os", "architecture", "model", "cpu"):
        value = payload.get(field)
        if isinstance(value, str):
            report[field] = value[:160]
    for field, maximum in (("cpu_pct", 100), ("cores", 4096), ("ram_total", 2**60),
                           ("ram_used", 2**60), ("disk_total", 2**60), ("disk_free", 2**60),
                           ("disk_used", 2**60)):
        report[field] = bounded_number(payload.get(field), maximum)
    interfaces = payload.get("interfaces")
    if isinstance(interfaces, list):
        report["interfaces"] = [
            dict(id=str(item.get("id", item.get("name", "")))[:80],
                 name=str(item.get("name", ""))[:64],
                 kind=item.get("kind") if item.get("kind") in ("wifi", "ethernet") else "other",
                 connected=item.get("connected") is True, to_regie=item.get("to_regie") is True,
                 ssid="".join(char if char.isprintable() else "�" for char in item["ssid"][:64])
                 if item.get("kind") == "wifi" and item.get("connected") is True
                 and isinstance(item.get("ssid"), str) and item["ssid"] else None,
                 addresses=[ip[:45] for ip in item["addresses"][:4] if isinstance(ip, str)]
                 if isinstance(item.get("addresses"), list) else [],
                 rx_mbps=bounded_number(item.get("rx_mbps"), 1_000_000),
                 tx_mbps=bounded_number(item.get("tx_mbps"), 1_000_000))
            for item in interfaces[:12] if isinstance(item, dict)
        ]
    return report


def display_report(payload):
    if not isinstance(payload, dict):
        return None
    width, height = payload.get("width"), payload.get("height")
    if not all(type(value) is int and 1 <= value <= 32768 for value in (width, height)):
        return None
    report = dict(width=width, height=height, estimated=payload.get("estimated") is True)
    for field in ("model", "connector"):
        value = payload.get(field)
        if isinstance(value, str):
            report[field] = value.strip()[:160]
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
            "$properties = $adapter.GetIPProperties(); "
            "try { $index = $properties.GetIPv4Properties().Index } catch { continue }; "
            "foreach ($entry in $properties.UnicastAddresses) { "
            "if ($entry.Address.AddressFamily -eq 'InterNetwork' -and "
            "$entry.DuplicateAddressDetectionState -in @('Preferred', 'Deprecated')) { "
            "[PSCustomObject]@{ip=$entry.Address.IPAddressToString; label=$adapter.Name; id=$adapter.Id; "
            "index=$index; prefix=$entry.IPv4Mask.ToString(); static=$entry.PrefixOrigin -eq 'Manual'} "
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
        dict(ip=entry["local"], label=interface["ifname"], id=interface["ifname"],
             index=interface["ifindex"], prefix=entry["prefixlen"], static=not entry.get("dynamic", False))
        for interface in json.loads(result.stdout)
        for entry in interface.get("addr_info", [])
        if entry.get("scope") in {"global", "link"}
    ]


def dhcp_interfaces():
    try:
        return interface_addresses()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError("Impossible d’identifier les interfaces du réseau écrans.") from error


class InterfaceTraffic:
    def __init__(self):
        self.lock = threading.Lock()
        self.rates = NetworkRates()
        self.system = Diagnostic()
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
                stats = self.system.sample_system(MEDIA_ROOT if MEDIA_ROOT.is_dir() else ROOT)
                self.result = {"interfaces": [row for row in rows or [] if row["connected"]],
                               "available": rows is not None, "diagnostic": diagnostic_report(stats)}
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


def desktop_command(address, protocol, port=None):
    if protocol not in ("rdp", "vnc"):
        return None
    if port is None:
        port = 3389 if protocol == "rdp" else 5900
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    if sys.platform != "win32":
        # Remmina prend en charge RDP et VNC directement depuis une URI.
        if remmina := shutil.which("remmina"):
            return [remmina, "-c", f"{protocol}://{address}:{port}"]
        if protocol == "rdp":
            return None
        if program := shutil.which("xtigervncviewer") or shutil.which("tigervncviewer"):
            return [program, f"{address}::{port}"]
        return None
    if protocol == "rdp":
        return ["mstsc.exe", f"/v:{address}:{port}"]
    for folder in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        if programs := os.environ.get(folder):
            viewer = Path(programs) / "TigerVNC" / "vncviewer.exe"
            if viewer.is_file():
                return [str(viewer), f"{address}::{port}"]
    if viewer := shutil.which("vncviewer.exe"):
        if "tigervnc" in {part.casefold() for part in Path(viewer).parts}:
            return [viewer, f"{address}::{port}"]
    return None


def support_methods(kind, desktop=None, services=None):
    if kind == "windows":
        return ("rdp", "vnc")
    if kind == "ubuntu":
        # Les clients récents indiquent les ports réellement ouverts.
        # Un dictionnaire vide signifie qu'aucun serveur ne fonctionne.
        if isinstance(services, dict):
            return ("ssh", *(name for name in ("rdp", "vnc") if name in services))
        # Compatibilité avec les lecteurs qui n'ont pas encore été mis à jour.
        return ("ssh", "rdp") if desktop == "rdp" else ("ssh", "vnc")
    if kind == "pi":
        return ("ssh", "vnc")
    return ()


def regie_support_methods():
    return support_methods("windows" if sys.platform == "win32" else "ubuntu",
                           desktop_protocol(), desktop_services())


def support_target(key):
    if key == "regie":
        ports = desktop_services() if sys.platform != "win32" else {}
        return dict(address="127.0.0.1", name="Régie", user=getuser(),
                    support=support_methods("windows" if sys.platform == "win32" else "ubuntu",
                                            desktop_protocol(), ports),
                    desktop_ports=ports)
    with LOCK:
        row = SCREENS.get(key) if isinstance(key, str) else None
        report = row.get("report", {}) if row and row.get("token") else {}
        if time.time() - report.get("seen", 0) >= ONLINE_AGE:
            raise ValueError("Le lecteur doit être associé et en ligne.")
        address = IPv4Address(report["ip"])
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError("Adresse du lecteur invalide.")
        diagnostics = report.get("diagnostic") or {}
        ports = diagnostics.get("desktop_services")
        return dict(address=str(address), name=row["name"], user=row.get("ssh_user", "soundlive"),
                    support=support_methods(row.get("kind"), diagnostics.get("desktop_protocol"), ports),
                    desktop_ports=ports if isinstance(ports, dict) else {},
                    ssh_host_fingerprints=row.get("ssh_host_fingerprints", []))


class Request(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(8)
        super().setup()

    def log_message(self, *_):
        pass

    def reply(self, status, value, *, location=None, session=None, content_type=None):
        if isinstance(value, bytes):
            body = value
            content_type = content_type or "text/html; charset=utf-8"
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
        page = path in ("/", "/connexion", "/terminal")
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
        if path == KEYS_PATH and not remote:
            try:
                with INSTALLATION_KEYS_LOCK:
                    keys = installation_keys(ROOT)
            except (OSError, ValueError):
                self.reply(404, {"error": "Prépare les clés d’installation depuis la régie."})
                return
            self.reply(200, keys, content_type="application/json; charset=utf-8")
            return
        if path == "/api/client-media":
            self.send_media(url.query, preview=False)
            return
        if not self.dashboard(path):
            return
        if path == "/api/media-preview":
            self.send_media(url.query, preview=True)
        elif path == "/api/media-download":
            self.send_media_archive(url.query)
        elif path == "/":
            self.reply(200, PAGE.replace(b"__REMOTE_ONLY__", b"" if remote else b"hidden"))
        elif path == "/terminal":
            self.reply(200, TERMINAL_PAGE)
        elif remote and path == "/connexion":
            notice = LOGIN_NOTICES.get(url.query.removeprefix("etat="), "")
            self.login_page(notice=notice)
        elif remote and path == "/api/session":
            self.session_status()
        elif path == "/api/network":
            self.reply(200, {"addresses": local_addresses(),
                             "port": getattr(self.server, "receiver_port", self.server.server_port),
                             "desktop_protocol": desktop_protocol(), "support": regie_support_methods(),
                             "desktop_ports": desktop_services() if sys.platform != "win32" else {}})
        elif path == "/api/interface-traffic":
            self.reply(200, self.server.interface_traffic.read())
        elif path == "/api/regie-network":
            network = self.server.network_control
            self.reply(200, network.status() if network else {"available": False})
        elif path == "/api/regie-latency":
            self.reply(200, {"ok": True})
        elif path == "/api/os-health":
            manager = "windows" if sys.platform == "win32" else "linux"
            can_open = not remote and (manager == "windows" or bool(shutil.which("update-manager")))
            self.reply(200, {**self.server.os_update_check.report(), "manager": manager, "can_open": can_open})
        elif path == "/api/regie-version":
            self.reply(200, self.server.publication_status.read())
        elif path == "/api/preview":
            try:
                query = {name: values[0] for name, values in parse_qs(url.query, max_num_fields=4).items()}
                target, _ = preview_target(query)
            except (KeyError, ValueError):
                self.reply(400, {"error": "Aperçu demandé invalide."})
                return
            with LOCK:
                now = time.time()
                preview_prune(now)
                job = PREVIEWS.get(target)
                if target[0] == "screen" and now - SCREENS.get(target[1], {}).get("report", {}).get("seen", 0) >= ONLINE_AGE:
                    job = None
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
                group_order = {name: index for index, name in enumerate(SCREEN_ORDER["groups"])}
                screen_order = {name: {key: index for index, key in enumerate(order)}
                                for name, order in SCREEN_ORDER["screens"].items()}
                for key, row in SCREENS.items():
                    report = row.get("report", {})
                    group = row.get("group") or ""
                    rows.append(dict(
                        id=key, name=row["name"], options=row.get("options"),
                        report=report, online=now - report.get("seen", 0) < ONLINE_AGE,
                        pairing=row.get("pairing") if now < row.get("pairing_until", 0) else None,
                        installation_fingerprint=row.get("installation_fingerprint"),
                        paired="token" in row, version=row.get("version", 0), kind=row.get("kind"),
                        support=support_methods(row.get("kind"),
                                                (report.get("diagnostic") or {}).get("desktop_protocol"),
                                                (report.get("diagnostic") or {}).get("desktop_services")),
                        desktop_ports=(report.get("diagnostic") or {}).get("desktop_services", {}),
                        group=row.get("group"), identify=row.get("identify") is True,
                        resolution_limit=row.get("resolution_limit"),
                        group_order=group_order.get(group), screen_order=screen_order.get(group, {}).get(key),
                        resolution_sources=resolution_sources(row, now),
                        source_resolutions={source: resolution for source in report.get("sources", [])
                                            if (resolution := source_resolution(source))},
                        client_update=update_view(key, report, now),
                        update_availability="maintenance" if os_busy(row, now) else update_availability(report, now, key_exists),
                        os_update=os_status(row, now), os_busy=os_busy(row, now),
                        media=screen_media(row), media_revision=media_revision(screen_media(row)),
                        media_folders=media_folders(row), media_folder=row.get("media_folder", MEDIA_DEFAULT_FOLDER),
                        media_disk=media_capacity(row),
                    ))
            self.reply(200, rows)
        elif path == "/api/media-storage":
            disk = media_disk(MEDIA_ROOT if MEDIA_ROOT.is_dir() else ROOT)
            self.reply(200, {**disk, "available": media_available(disk)})
        else:
            self.reply(404, {"error": "Page introuvable."})

    def do_POST(self):
        url = urlsplit(self.path)
        path = url.path
        remote = getattr(self.server, "remote_dashboard", False)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if path == "/api/media-upload":
            if self.dashboard(path, write=True):
                try:
                    self.upload_media(url.query, length)
                except (ValueError, KeyError) as error:
                    self.reply(400, {"error": str(error)})
                except OSError as error:
                    print(error, flush=True)
                    self.reply(500, {"error": "Le fichier n’a pas pu être reçu ou enregistré par la régie."})
            return
        limit = 16384
        if path == "/api/preview-result":
            limit = 4 * ((PREVIEW_LIMIT + 2) // 3) + 1024
        elif path == "/api/media-export":
            limit = 512 * 1024
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
            if path == "/api/regie-network":
                self.change_network(value)
                return
            if path == "/api/pair":
                value.pop("installation_fingerprint", None)
                if value.get("prepare_installation") is True:
                    try:
                        with INSTALLATION_KEYS_LOCK:
                            value["installation_fingerprint"] = prepare_installations(ROOT)
                    except (OSError, RuntimeError, ValueError) as error:
                        self.reply(409, {"error": f"Impossible de préparer les clés d’installation. {error}"})
                        return
            if path.startswith("/api/ssh/"):
                self.terminal_action(path.removeprefix("/api/ssh/"), value)
                return
            if path == "/api/os-health/open":
                if remote:
                    self.reply(403, {"error": "Ouvre le gestionnaire de mises à jour depuis le PC de régie."})
                    return
                if sys.platform == "win32":
                    os.startfile("ms-settings:windowsupdate")
                elif manager := shutil.which("update-manager"):
                    subprocess.Popen([manager], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    self.reply(409, {"error": "Utilise le gestionnaire de paquets sur le PC de régie."})
                    return
                self.reply(200, {"ok": True})
                return
            if path == "/api/desktop/open":
                if remote:
                    self.reply(403, {"error": "Le lancement d’un client sur la régie est réservé à la console locale."})
                    return
                key = value.get("id")
                target = support_target(key)
                protocol = value.get("protocol")
                if protocol not in ("vnc", "rdp") or protocol not in target["support"]:
                    raise ValueError("Ce type de bureau distant est indisponible pour ce poste.")
                address = target["address"]
                if key == "regie":
                    address = str(IPv4Address(value.get("address", "")))
                    if address not in {item["ip"] for item in local_addresses()}:
                        raise ValueError("Choisis une adresse du PC de régie.")
                port = target.get("desktop_ports", {}).get(protocol)
                command = desktop_command(address, protocol, port)
                if command:
                    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.reply(200, {"opened": command is not None})
                return
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
            if path == "/api/poll" and status == 200 and result.get("os_job"):
                job = result["os_job"]
                try:
                    job["signature"] = sign_manifest(UPDATE_KEY, maintenance_manifest(
                        value["id"], job["id"], safe_text(value["boot"]), value["os_nonce"]), OS_NAMESPACE)
                except (OSError, RuntimeError) as error:
                    print(f"Signature de la maintenance système : {error}", flush=True)
                    result["os_job"] = None
                    with LOCK:
                        row = SCREENS.get(value["id"], {})
                        previous = row.get("os_update")
                        if previous and previous["id"] == job["id"] and previous["state"] == "queued":
                            row["os_update"] = {**previous, "state": "failed",
                                                "message": "La régie n’a pas pu signer la demande."}
                            try:
                                save()
                            except OSError:
                                row["os_update"] = previous
                                raise
            if path == "/api/client-package" and status == 200:
                try:
                    result = package(UPDATE_KEY, **result)
                except (OSError, RuntimeError) as error:
                    print(f"Signature des mises à jour : {error}", flush=True)
                    reason = error.reason if isinstance(error, OpenSSHError) else "failed"
                    message = {
                        "missing": "Le client OpenSSH (ssh-keygen) est introuvable sur la régie.",
                        "timeout": "OpenSSH ne répond pas pendant la signature sur la régie.",
                        "permissions": "La régie n’a pas pu sécuriser sa clé privée de mise à jour.",
                    }.get(reason, "La régie n’a pas pu signer le paquet.")
                    status, result = 503, {"code": "update_signing_failed",
                                           "error": message + " Sur la régie, lance tableau.py --tester-mises-a-jour."}
        except (KeyError, ValueError, TypeError) as error:
            status, result = 400, {"error": str(error)}
        except (OSError, RuntimeError) as error:
            print(error, flush=True)
            status, result = 500, {"error": "Le serveur n’a pas pu terminer la demande."}
        self.reply(status, result)

    def change_network(self, value):
        network = self.server.network_control
        if network is None:
            self.reply(409, {"error": "Démarre la régie avec son lanceur graphique pour gérer le réseau."})
            return
        try:
            report = network.change(value.get("kind"), value.get("operation"), value.get("data"), web=True)
        except PermissionError as error:
            self.reply(403, {"error": str(error)})
        except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError) as error:
            self.reply(409, {"error": str(error)})
        else:
            self.reply(200, {"report": report or ""})

    def prepare_ssh_readers(self, ids):
        with INSTALLATION_KEYS_LOCK:
            prepare_installations(ROOT)
            packages = build_full_updates(ssh_access_public(ROOT))
            with LOCK:
                FULL_UPDATES.clear()
                FULL_UPDATES.update(packages)
                now = time.time()
                for key in ids:
                    report = SCREENS.get(key, {}).get("report", {})
                    current = CLIENT_UPDATES.get(key)
                    pending = update_view(key, report, now) or {}
                    desired = update_payload(report)
                    if (current and desired and current["version"] != desired[1]
                            and pending.get("state") in ("queued", "installing")):
                        return 409, {"error": "Attends la fin de la mise à jour client en cours, puis prépare l’accès par clé."}
                return batch("/api/client-updates", {"ids": ids}, now)

    def terminal_action(self, operation, value):
        owner = (self.client_address[0], getattr(self, "remote_session", None))
        terminals = self.server.terminals
        try:
            if operation in ("target", "start"):
                key = value.get("id")
                target = support_target(key)
                if "ssh" not in target["support"]:
                    raise ValueError("Le terminal web SSH est disponible pour les postes Linux.")
                result = target if operation == "target" else terminals.start(
                    owner, key, target["address"], value, target.get("ssh_host_fingerprints", ()))
            elif operation in ("group-start", "group-prepare"):
                ids = value.get("ids")
                if (not isinstance(ids, list) or not ids or len(ids) > GROUP_LIMIT
                        or any(not isinstance(key, str) or key == "regie" for key in ids)):
                    raise ValueError(f"Sélectionne entre 1 et {GROUP_LIMIT} lecteurs Raspberry Pi ou Ubuntu.")
                with LOCK:
                    if any(key not in SCREENS or not SCREENS[key].get("token")
                           or SCREENS[key].get("kind") not in ("pi", "ubuntu") for key in ids):
                        raise ValueError("Sélectionne uniquement des lecteurs Linux associés.")
                    names = {key: SCREENS[key]["name"] for key in ids}
                if operation == "group-prepare":
                    self.reply(*self.prepare_ssh_readers(list(names)))
                    return
                targets = {}
                for key, name in names.items():
                    try:
                        targets[key] = support_target(key)
                    except ValueError as error:
                        targets[key] = dict(name=name, error=str(error))
                result = terminals.start_group(owner, targets, value)
            elif operation == "group-status":
                result = terminals.group_status(owner, value.get("job"))
            elif operation == "sync":
                result = terminals.sync(owner, value.get("session"), value.get("input"))
            elif operation == "end":
                terminals.end(owner, value.get("session"))
                result = {"ok": True}
            else:
                self.reply(404, {"error": "Action terminal inconnue."})
                return
        except HostKeyCheck as error:
            self.reply(409, {"error": str(error), "code": "ssh_host_key",
                             "presented": error.presented, "expected": error.expected})
            return
        except (ValueError, RuntimeError) as error:
            self.reply(400, {"error": str(error)})
            return
        self.reply(200, result)

    def upload_media(self, raw_query, length):
        if self.headers.get_content_type() != "application/octet-stream" or not 0 < length <= MEDIA_FILE_LIMIT:
            raise ValueError("Envoie un fichier non vide de 256 Mio maximum.")
        query = parse_qs(raw_query, max_num_fields=3)
        key, name = query["id"][0], media_name(query["name"][0])
        folder_id = query.get("folder", [MEDIA_DEFAULT_FOLDER])[0]
        with LOCK:
            row = SCREENS.get(key)
            if not row or not row.get("token"):
                raise ValueError("Cet écran doit être associé.")
            folder = media_folders(row).get(folder_id)
            if folder is None:
                raise ValueError("Ce dossier n’existe plus.")
            media_quota(row, folder, length)
            generation = row.get("media_generation", 0)
            folder_generation = folder.get("generation", 0)
        MEDIA_ROOT.mkdir(mode=0o700, exist_ok=True)
        if length > media_available(media_disk(MEDIA_ROOT)):
            raise ValueError(f"Ce fichier ferait dépasser le seuil de {MEDIA_DISK_PERCENT} % sur la régie.")
        with NamedTemporaryFile(dir=MEDIA_ROOT, delete=False, buffering=0) as handle:
            staged = Path(handle.name)
            checksum = sha256()
            try:
                remaining = length
                while remaining:
                    data = self.rfile.read(min(remaining, MEDIA_CHUNK))
                    if not data:
                        raise ValueError("Le transfert du fichier a été interrompu.")
                    with MEDIA_WRITE_LOCK:
                        if len(data) > media_available(media_disk(MEDIA_ROOT)):
                            raise ValueError(f"Le disque de la régie a atteint le seuil de {MEDIA_DISK_PERCENT} %.")
                        handle.write(data)
                    checksum.update(data)
                    remaining -= len(data)
                handle.close()
                with LOCK:
                    if SCREENS.get(key) is not row:
                        raise ValueError("Cet écran a été retiré pendant le transfert.")
                    if row.get("media_generation", 0) != generation:
                        raise ValueError("L’envoi a été annulé parce que les dossiers ont été vidés.")
                    folders = media_folders(row)
                    folder = folders.get(folder_id)
                    if folder is None:
                        raise ValueError("Ce dossier a été supprimé pendant le transfert.")
                    if folder.get("generation", 0) != folder_generation:
                        raise ValueError("L’envoi a été annulé parce que le dossier a été vidé.")
                    media_quota(row, folder, length)
                    identifier = secrets.token_hex(16)
                    directory = MEDIA_ROOT / key
                    directory.mkdir(mode=0o700, exist_ok=True)
                    destination = directory / identifier
                    staged.replace(destination)
                    item = dict(id=identifier, name=name, size=length, sha256=checksum.hexdigest())
                    try:
                        folders = {**folders, folder_id: {**folder, "items": [*folder["items"], item]}}
                        save_media(row, folders, row.get("media_folder", MEDIA_DEFAULT_FOLDER), screen_media(row)["enabled"])
                    except (OSError, ValueError):
                        destination.unlink(missing_ok=True)
                        raise
            finally:
                handle.close()
                staged.unlink(missing_ok=True)
        self.reply(201, item)

    def send_media(self, raw_query, *, preview):
        if not preview and getattr(self.server, "remote_dashboard", False):
            self.reply(404, {"error": "Utilise le port d’association du récepteur."})
            return
        try:
            query = parse_qs(raw_query, max_num_fields=3)
            key, identifier = query["id"][0], query["file"][0]
        except (KeyError, ValueError):
            self.reply(400, {"error": "Fichier média invalide."})
            return
        with LOCK:
            row = SCREENS.get(key)
            token = row and row.get("token")
            allowed = token and (preview or token_matches(self.headers.get("Authorization"), "Bearer " + token))
            item = next((item for item in catalog_items(row) if item["id"] == identifier), None) if allowed else None
            try:
                stream = (MEDIA_ROOT / key / identifier).open("rb") if item else None
            except OSError:
                stream = None
        if not stream:
            self.reply(404 if allowed else 403, {"error": "Fichier média indisponible."})
            return
        with media_stream(stream, key, identifier):
            byte_range = self.headers.get("Range") if preview else None
            try:
                start, end = media_bytes_range(byte_range, item["size"])
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{item['size']}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206 if byte_range else 200)
            self.send_header("Content-Type", MEDIA_TYPES[Path(item["name"]).suffix.lower()] if preview else "application/octet-stream")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if preview:
                self.send_header("Accept-Ranges", "bytes")
            if byte_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{item['size']}")
            self.end_headers()
            try:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(MEDIA_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except OSError:
                pass

    def prepare_media_export(self, data, now):
        files = data.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("Sélectionne les fichiers à télécharger.")
        catalogs = {}
        selected = {}
        for entry in files:
            key, identifier = entry["id"], entry["file"]
            if not isinstance(key, str) or not isinstance(identifier, str):
                raise ValueError("Fichier média invalide.")
            if key not in catalogs:
                row = SCREENS.get(key)
                catalogs[key] = {item["id"]: item for item in catalog_items(row)} if row and row.get("token") else {}
            item = catalogs[key].get(identifier)
            if item is None or not (MEDIA_ROOT / key / identifier).is_file():
                return 404, {"error": "Un fichier sélectionné est indisponible."}
            selected[key, identifier] = item["name"]
        for token, (deadline, _) in tuple(MEDIA_EXPORTS.items()):
            if deadline <= now:
                del MEDIA_EXPORTS[token]
        token = secrets.token_urlsafe(24)
        MEDIA_EXPORTS[token] = now + 60, selected
        return 200, {"url": "/api/media-download?token=" + token}

    def send_media_archive(self, raw_query):
        token = raw_query.removeprefix("token=")
        with LOCK:
            deadline, files = MEDIA_EXPORTS.pop(token, (0, {}))
        if time.time() >= deadline:
            self.reply(404, {"error": "Ce téléchargement a expiré. Relance-le depuis la médiathèque."})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="SoundLive-medias.zip"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        names = set()
        missing = []
        try:
            with ZipFile(self.wfile, "w") as archive:
                for (key, identifier), original in files.items():
                    try:
                        stream = (MEDIA_ROOT / key / identifier).open("rb")
                    except OSError:
                        missing.append(original)
                        continue
                    name = re.sub(r'[<>:"|?*]', "_", original)
                    stem, suffix = Path(name).stem, Path(name).suffix
                    serial = 2
                    while name.casefold() in names:
                        name = f"{stem} ({serial}){suffix}"
                        serial += 1
                    names.add(name.casefold())
                    with media_stream(stream, key, identifier), archive.open(name, "w") as target:
                        shutil.copyfileobj(stream, target, MEDIA_CHUNK)
                if missing:
                    archive.writestr("Fichiers indisponibles.txt", "\n".join(missing) + "\n")
        except OSError:
            pass

    def change_media(self, key, row, data):
        folders = media_folders(row).copy()
        active = row.get("media_folder", MEDIA_DEFAULT_FOLDER)
        enabled = screen_media(row)["enabled"]
        action = data.get("action", "play" if "enabled" in data else "remove-file")
        folder_id = data.get("folder", active)
        if not isinstance(folder_id, str) or folder_id not in folders:
            raise ValueError("Ce dossier n’existe plus.")
        folder = folders[folder_id]
        removed = []
        if action in ("create", "rename"):
            name = safe_text(data["name"], 64)
            if not name.isprintable():
                raise ValueError("Nom de dossier invalide.")
            if any(entry["name"].casefold() == name.casefold() for identifier, entry in folders.items()
                   if action == "create" or identifier != folder_id):
                raise ValueError("Un dossier porte déjà ce nom.")
            if action == "create":
                if len(folders) >= MEDIA_FOLDER_LIMIT:
                    raise ValueError("Ce lecteur a atteint la limite de 32 dossiers.")
                folder_id = secrets.token_hex(8)
                folder = dict(items=[])
            folders[folder_id] = {**folder, "name": name}
        elif action == "empty-all":
            removed = catalog_items(row)
            folders = {identifier: {**entry, "items": []} for identifier, entry in folders.items()}
            enabled = False
        elif action == "empty-folder":
            removed = folder["items"]
            folders[folder_id] = {**folder, "items": [], "generation": folder.get("generation", 0) + 1}
        elif action == "remove-folder":
            if folder_id == MEDIA_DEFAULT_FOLDER:
                raise ValueError("Le dossier par défaut peut être renommé.")
            removed = folder["items"]
            del folders[folder_id]
            if folder_id == active:
                active, enabled = MEDIA_DEFAULT_FOLDER, False
        elif action == "timing":
            timing = media_timing(dict(image_seconds=data["image_seconds"], video_seconds=data["video_seconds"]))
            folders[folder_id] = {**folder, "timing": timing}
        elif action == "play":
            enabled = data["enabled"]
            if type(enabled) is not bool:
                raise ValueError("Mode de lecture locale invalide.")
            if enabled and not folder["items"]:
                raise ValueError("Ajoute d’abord une image ou une vidéo à ce dossier.")
            if enabled:
                active = folder_id
        elif action == "remove-files":
            files = data.get("files")
            if not isinstance(files, list) or not files or any(not isinstance(value, str) for value in files):
                raise ValueError("Sélection de fichiers invalide.")
            identifiers = set(files)
            removed = [item for item in folder["items"] if item["id"] in identifiers]
            if len(removed) != len(identifiers):
                return 409, {"error": "Le contenu du dossier a changé. Réessaie avec la liste à jour."}
            folders[folder_id] = {**folder, "items": [item for item in folder["items"] if item["id"] not in identifiers]}
        elif action == "reorder":
            source, _, item = media_location(folders, data["file"])
            destination, _, anchor = media_location(folders, data["anchor"])
            after = data.get("after")
            if source != folder_id or destination != folder_id or item == anchor or type(after) is not bool:
                raise ValueError("Déplacement invalide dans ce dossier.")
            items = [current for current in folder["items"] if current["id"] != item["id"]]
            items.insert(items.index(anchor) + after, item)
            folders[folder_id] = {**folder, "items": items}
        elif action in ("remove-file", "move", "order"):
            source, index, item = media_location(folders, data["file"])
            if action == "move" and data.get("source_folder", source) != source:
                return 409, {"error": "Le fichier a changé de dossier. Réessaie avec la liste à jour."}
            original = folders[source]
            items = list(original["items"])
            if action == "order":
                delta = data["delta"]
                if type(delta) is not int or delta not in (-1, 1) or not 0 <= index + delta < len(items):
                    raise ValueError("Ce fichier est déjà au bord de la liste.")
                items[index], items[index + delta] = items[index + delta], items[index]
            elif action == "remove-file":
                removed = [items.pop(index)]
            elif source != folder_id:
                candidate = [*folder["items"], items.pop(index)]
                media_config(dict(enabled=False, items=candidate))
                folders[folder_id] = {**folder, "items": candidate}
            folders[source] = {**original, "items": items}
        else:
            raise ValueError("Action média inconnue.")
        save_media(row, folders, active, enabled, resume=action == "play" and enabled,
                   empty=action == "empty-all")
        for item in removed:
            try:
                (MEDIA_ROOT / key / item["id"]).unlink(missing_ok=True)
            except OSError as error:
                print(error, flush=True)
        return 200, {"media": screen_media(row), "folder": folder_id}

    def apply(self, path, data):
        now = time.time()
        if path == "/api/identify":
            enabled = data.get("enabled")
            if type(enabled) is not bool:
                raise ValueError("Indique si l’identification doit être affichée.")
            if "id" in data:
                key = data["id"]
                row = SCREENS.get(key) if isinstance(key, str) else None
                if not row or not row.get("token"):
                    return 404, {"error": "Écran associé introuvable."}
                targets = [row]
            else:
                targets = [row for row in SCREENS.values() if row.get("token")]
            for row in targets:
                if enabled:
                    row["identify"] = True
                else:
                    row.pop("identify", None)
            if targets:
                save()
            return 200, {"count": len(targets)}
        if path == "/api/screen-order":
            return order_screens(data)
        if path == "/api/media-export":
            return self.prepare_media_export(data, now)
        if path == "/api/preview":
            return request_preview(data, now)
        if path in ("/api/group", "/api/client-updates", "/api/os-updates", "/api/screen-groups"):
            return batch(path, data, now)
        if path == "/api/pair":
            key = secrets.token_hex(8)
            code = secrets.token_urlsafe(9)
            # The installers can interpret positional values beginning with "-" as options.
            while code.startswith("-"):
                code = secrets.token_urlsafe(9)
            kind = data.get("kind")
            if kind is not None and kind not in DEVICE_KINDS:
                raise ValueError("Choisis Raspberry Pi, Ubuntu ou Windows.")
            fingerprint = data.get("installation_fingerprint")
            exchange_ssh = bool(fingerprint and kind in ("pi", "ubuntu") and data.get("exchange_ssh") is True)
            if exchange_ssh:
                code = SSH_PAIRING_PREFIX + code
            SCREENS[key] = dict(name=safe_text(data["name"]), kind=kind, pairing=code,
                                pairing_until=now + 900, exchange_ssh=exchange_ssh)
            if fingerprint:
                SCREENS[key]["installation_fingerprint"] = fingerprint
            save()
            return 200, {"code": code, "installation_fingerprint": fingerprint}
        if path == "/api/enroll":
            return enroll_reader(data, now)

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
            update_issue = data.get("update_issue")
            update_issue = update_issue if not can_update and update_issue in CLIENT_UPDATE_ISSUES else None
            update_target = data.get("update_target")
            update_target = update_target if isinstance(update_target, str) and update_target in ENGINE_ASSETS else None
            active_source = data.get("active_source")
            active_source = active_source[:256] if state == "Lecture" and isinstance(active_source, str) else None
            active_media = active_source if data.get("local_video") is True else None
            if active_media:
                active_source = None
            orientation_status = data.get("orientation_status")
            if isinstance(orientation_status, dict) and orientation_status.get("value") in ORIENTATIONS:
                orientation_status = dict(value=orientation_status["value"],
                                          error=str(orientation_status.get("error") or "")[:240])
            else:
                orientation_status = None
            cached_media = data.get("media_status")
            if isinstance(cached_media, dict):
                revision = cached_media.get("revision")
                disk = cached_media.get("disk")
                if (not isinstance(disk, dict) or any(type(disk.get(k)) is not int or disk[k] < 0
                        for k in ("total", "used", "free")) or disk["total"] <= 0 or disk["used"] + disk["free"] > disk["total"]):
                    disk = None
                cached = cached_media.get("cached")
                cached = [i for i in cached[:MEDIA_LIBRARY_LIMIT] if isinstance(i, str)
                          and re.fullmatch(r"[a-f0-9]{32}", i)] if isinstance(cached, list) else []
                transfer = cached_media.get("transfer")
                if (not isinstance(transfer, dict) or not isinstance(transfer.get("id"), str)
                        or not re.fullmatch(r"[a-f0-9]{32}", transfer["id"])
                        or type(transfer.get("received")) is not int or not 0 <= transfer["received"] <= MEDIA_FILE_LIMIT):
                    transfer = None
                else:
                    transfer = dict(id=transfer["id"], received=transfer["received"])
                cached_media = dict(
                    revision=revision if isinstance(revision, str) and SAFE_ID.fullmatch(revision) else None,
                    error=str(cached_media.get("error") or "")[:240],
                    local=cached_media.get("local") is True,
                    disk=disk, cached=cached, transfer=transfer,
                )
            else:
                cached_media = None
            identification = data.get("identification")
            if isinstance(identification, dict):
                identification = dict(visible=identification.get("visible") is True,
                                      error=str(identification.get("error") or "")[:240])
            else:
                identification = None
            os_capability = data.get("os_capability")
            if os_capability not in ("ready", "setup_required", "key_required") or row["kind"] == "windows":
                os_capability = "unsupported"
            row["report"] = dict(seen=now, ip=self.client_address[0], state=state, boot=boot,
                                 os_capability=os_capability,
                                 os_health=os_update_report(data.get("os_health")),
                                 identification=identification,
                                 active_source=active_source,
                                 active_media=active_media,
                                 sources=[s[:256] for s in sources[:32] if isinstance(s, str)],
                                 error=str(data.get("error", ""))[:240],
                                 can_poweroff=data.get("can_poweroff") is True,
                                 can_reboot=data.get("can_reboot") is not False,
                                 diagnostic=diagnostics, latency_test=test_result,
                                 display=display_report(data.get("display")),
                                 orientation_status=orientation_status,
                                 media_status=cached_media,
                                 display_capture=data.get("display_capture") is True
                                 if "display_capture" in data else None,
                                 capture_needs_consent=data.get("capture_needs_consent") is True,
                                 display_preview_kind="player" if data.get("display_preview_kind") == "player" else "monitor",
                                 display_preview_error=str(data.get("display_preview_error") or "")[:240],
                                 can_update=can_update, client_version=version, client_release=release,
                                 update_issue=update_issue,
                                 update_target=update_target, update_has_engine=data.get("update_has_engine") is True,
                                 update_result=result,
                                 latency_ms=bounded_number(data.get("latency_ms"), 10000))
            os_job = os_poll(row, data, boot, now) if os_capability != "unsupported" else None
            maintaining = os_busy(row, now)
            power = row.get("power", {})
            pending = not maintaining and power.get("boot") == boot and now - power.get("time", 0) < POWER_REQUEST_AGE
            operation = power.get("operation") if pending else None
            availability = update_availability(row["report"], now, UPDATE_KEY.is_file())
            recommendation = dict(version=CLIENT_VERSION, state=availability)
            preview_prune(now)
            preview = next((dict(id=item["id"], source=item["source"], screen=item["screen"],
                                 automatic=item["automatic"]) for item in PREVIEWS.values()
                            if item["reader"] == key and item["state"] == "pending"), None)
            return 200, dict(options=row["options"], identify=row.get("identify") is True,
                             media=screen_media(row),
                             media_catalog=catalog_items(row),
                             preview_request=preview,
                             waiting=dict(name=row["name"], group=row.get("group"),
                                          qr=getattr(self.server, "waiting_qr", None)),
                             reader_update=recommendation, os_job=os_job,
                             reboot=power["id"] if operation == "reboot" else None,
                             poweroff=power["id"] if operation == "poweroff" else None,
                             latency_test=test["id"] if test else None,
                             client_update=job["id"] if can_update and not maintaining and job and result is None
                             and now < job["until"] else None)
        if path == "/api/remove":
            remove_screens({key: row})
            return 200, {"ok": True}
        elif path == "/api/media-settings" and row.get("token"):
            return self.change_media(key, row, data)
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
        elif path == "/api/resolution-limit" and "options" in row:
            if not RESOLUTION_CONTROLS_ENABLED:
                return 409, {"error": "Les réglages de résolution sont temporairement désactivés."}
            limit = data["limit"]
            if limit is not None and limit not in RESOLUTIONS:
                raise ValueError("Résolution maximale invalide.")
            previous = row.get("resolution_limit")
            row["resolution_limit"] = limit
            try:
                save()
            except OSError:
                row["resolution_limit"] = previous
                raise
            return 200, {"ok": True}
        elif path == "/api/update" and "options" in row:
            if data.get("version") != row["version"]:
                return 409, {"error": "Réglages modifiés entre-temps. Recharge la page."}
            incoming = data["options"]
            settings = options({"orientation": row["options"].get("orientation", "landscape"), **incoming})
            error = resolution_rejection(row, settings, now, data.get("resolution"))
            if error:
                return 409, {"error": error}
            previous = row["options"], row["version"]
            row.update(options=settings, version=row["version"] + 1)
            try:
                save()
            except OSError:
                row["options"], row["version"] = previous
                raise
            return 200, {"ok": True}
        elif path in ("/api/reboot", "/api/poweroff"):
            operation = "poweroff" if path == "/api/poweroff" else "reboot"
            state = queue_power(row, operation, now)
            if state == "offline":
                return 409, {"error": "Le récepteur est hors ligne."}
            if state == "unsupported":
                return 409, {"error": "Cette commande n’est pas prise en charge par le lecteur."}
            if state == "maintenance":
                return 409, {"error": "Une mise à jour système est en cours sur le lecteur."}
        else:
            return 400, {"error": "Action indisponible."}
        save()
        return 200, {"ok": True}

class Regie:
    def __init__(self, port=8765, publication_status=None, network_control=None):
        self.remote_url = None
        self.connections = None
        self.server = ThreadingHTTPServer(("0.0.0.0", port), Request)
        self.server.terminals = Terminals()
        self.server.os_update_check = OsUpdateCheck()
        self.server.network_control = network_control
        self.server.interface_traffic = InterfaceTraffic()
        self.server.publication_status = publication_status or PublicationStatus(
            ROOT / "source-publications.json", SOFTWARE_VERSION)
        self.local_url = f"http://127.0.0.1:{self.server.server_port}"
        self.remote = None

    def start(self, remote_enabled=False):
        try:
            with LOCK:
                screens = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
                for row in screens.values():
                    for field in ("report", "reboot", "power"):
                        row.pop(field, None)
                    if "options" in row:
                        row["options"].setdefault("orientation", "landscape")
                SCREENS.clear()
                SCREENS.update(screens)
                SCREEN_ORDER.update(load_screen_order())
                MEDIA_EXPORTS.clear()
                CLIENT_UPDATES.clear()
                LATENCY_TESTS.clear()
                PREVIEWS.clear()
            if remote_enabled:
                self.connections = RemoteConnections(REMOTE_ACCESS / "adresses-bloquees.json")
                self.remote = RemoteDashboard(REMOTE_ACCESS, Request, self.server.server_port)
                self.remote.interface_traffic = self.server.interface_traffic
                self.remote.terminals = self.server.terminals
                self.remote.os_update_check = self.server.os_update_check
                self.remote.network_control = self.server.network_control
                self.server.waiting_qr = waiting_access(*self.remote.server_address)
                self.remote_url = f"https://{self.remote.server_address[0]}:{self.remote.server_port}"
                self.server.remote_connections = self.remote.remote_connections = self.connections
                self.remote.publication_status = self.server.publication_status
        except BaseException:
            self.server.terminals.close()
            if self.remote is not None:
                self.remote.server_close()
            self.server.server_close()
            raise
        if self.remote is not None:
            threading.Thread(target=self.remote.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()

    def close(self):
        self.server.terminals.close()
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
    setup.add_argument("--tester-mises-a-jour", action="store_true",
                       help="tester la signature de la régie avec sa clé privée existante")
    setup.add_argument("--preparer-installations", action="store_true",
                       help="préparer les clés publiques récupérées par les installateurs lecteurs")
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
    if args.preparer_installations:
        try:
            fingerprint = prepare_installations(ROOT)
        except (OSError, RuntimeError, ValueError) as error:
            sys.exit(str(error))
        print(f"Clés publiques prêtes. Empreinte : {fingerprint}")
        return
    if args.tester_mises_a_jour:
        if not UPDATE_KEY.is_file():
            sys.exit("Clé de mise à jour absente. Prépare-la avec tableau.py --preparer-mises-a-jour.")
        try:
            package(UPDATE_KEY, {}, "a" * 16, "b" * 32, "c" * 64, "d" * 64)
        except (OSError, RuntimeError) as error:
            sys.exit(f"Signature impossible : {error}")
        print("Signature OK. La régie peut signer les mises à jour.")
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
