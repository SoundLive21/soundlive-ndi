# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
from contextlib import contextmanager
from hashlib import sha256
from hmac import digest as hmac_digest
import json
from pathlib import Path
import re
import shutil
import sys

DEFAULT_SOURCE = "OBS (Programme)"
APP_NAME = "SoundLive-NDI"
COPYRIGHT = "© 2026 Société SoundLive"
SOFTWARE_VERSION = "1.4.125"
PREVIEW_LIMIT = 160 * 1024
ORIENTATIONS = ("landscape", "portrait-left", "portrait-right")
MEDIA_FILE_LIMIT = 256 * 1024 * 1024
MEDIA_TOTAL_LIMIT = 1024 * 1024 * 1024
MEDIA_COUNT_LIMIT = 20
MEDIA_FOLDER_LIMIT = 32
MEDIA_LIBRARY_LIMIT = 128
MEDIA_DISK_PERCENT = 90
MEDIA_CHUNK = 128 * 1024
MEDIA_EXTENSIONS = {
    ".jpg": "image2", ".jpeg": "image2", ".png": "image2",
    ".mp4": "mov", ".m4v": "mov", ".mov": "mov", ".webm": "matroska", ".mkv": "matroska",
}


SSH_PAIRING_PREFIX = "sl77_"
SSH_FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")


def ssh_pairing_proof(code, direction, payload):
    data = json.dumps(["soundlive-ssh-enrollment", direction, payload],
                      sort_keys=True, separators=(",", ":")).encode("ascii")
    return hmac_digest(code.encode("ascii"), data, "sha256").hex()


def ssh_fingerprints(values):
    if (not isinstance(values, list) or not 1 <= len(values) <= 8
            or any(not isinstance(value, str) or not SSH_FINGERPRINT.fullmatch(value) for value in values)
            or len(set(values)) != len(values)):
        raise ValueError("Liste d’empreintes SSH du lecteur invalide.")
    return values


def media_name(name):
    if (not isinstance(name, str) or not 0 < len(name) <= 180 or
            not name.isprintable() or any(char in name for char in "/\\") or
            Path(name).suffix.lower() not in MEDIA_EXTENSIONS):
        raise ValueError("Choisis un fichier JPG, PNG, MP4, MOV, MKV ou WebM.")
    return name


def media_items(items, limit):
    if not isinstance(items, list) or len(items) > limit:
        raise ValueError("Liste de médias invalide.")
    verified = []
    for item in items:
        identifier, checksum, size = item["id"], item["sha256"], item["size"]
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier) or
                not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum) or
                type(size) is not int or not 0 < size <= MEDIA_FILE_LIMIT):
            raise ValueError("Fichier média invalide.")
        verified.append(dict(id=identifier, sha256=checksum, size=size, name=media_name(item["name"])))
    if len({item["id"] for item in verified}) != len(verified):
        raise ValueError("Liste de médias en double.")
    return verified


def media_timing(value):
    image, video = value.get("image_seconds", 10), value.get("video_seconds", 0)
    if type(image) is not int or not 1 <= image <= 3600 or type(video) is not int or not 0 <= video <= 3600:
        raise ValueError("Durée invalide. Images de 1 à 3600 secondes, vidéos de 0 à 3600 secondes.")
    return {name: seconds for name, seconds, default in
            (("image_seconds", image, 10), ("video_seconds", video, 0)) if seconds != default}


def media_config(value):
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise ValueError("Mode de lecture locale invalide.")
    verified = media_items(value.get("items"), MEDIA_COUNT_LIMIT)
    if sum(item["size"] for item in verified) > MEDIA_TOTAL_LIMIT:
        raise ValueError("Liste de médias trop volumineuse.")
    return dict(enabled=value["enabled"] and bool(verified), items=verified, **media_timing(value))


def media_revision(config):
    return sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def media_disk(path):
    total, used, free = shutil.disk_usage(path)
    return dict(total=total, used=used, free=free)


def media_available(disk):
    return max(0, min(disk["free"], disk["total"] * MEDIA_DISK_PERCENT // 100 - disk["used"]))


@contextmanager
def client_lock(path):
    with path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        yield True


def release_number(value):
    if isinstance(value, str) and len(value) <= 20:
        parts = value.split(".")
        if len(parts) == 3 and all(part.isascii() and part.isdecimal() for part in parts):
            return tuple(map(int, parts))
    return ()

def options(value):
    source = value["source"]
    mode = value["bandwidth"]
    volume = value["volume"]
    orientation = value.get("orientation", "landscape")
    if not isinstance(source, str) or not 0 < len(source.strip()) <= 256 or any(ord(c) < 32 for c in source):
        raise ValueError("Saisis le nom complet d’une source NDI.")
    if mode not in ("0", "1"):
        raise ValueError("Sélectionne Low ou High.")
    if type(volume) is not int or not 0 <= volume <= 100:
        raise ValueError("Le volume doit être compris entre 0 et 100.")
    if orientation not in ORIENTATIONS:
        raise ValueError("Orientation de l’écran invalide.")
    for key in ("enabled", "muted"):
        if type(value[key]) is not bool:
            raise ValueError("État audio ou lecture invalide.")
    return dict(source=source.strip(), bandwidth=mode, volume=volume, orientation=orientation,
                enabled=value["enabled"], muted=value["muted"])
