# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
from contextlib import contextmanager
import sys

DEFAULT_SOURCE = "OBS (Programme)"
APP_NAME = "SoundLive-NDI"
SOFTWARE_VERSION = "1.4.6"


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
    if not isinstance(source, str) or not 0 < len(source.strip()) <= 256 or any(ord(c) < 32 for c in source):
        raise ValueError("Saisis le nom complet d’une source NDI.")
    if mode not in ("0", "1"):
        raise ValueError("Sélectionne Low ou High.")
    if type(volume) is not int or not 0 <= volume <= 100:
        raise ValueError("Le volume doit être compris entre 0 et 100.")
    for key in ("enabled", "muted"):
        if type(value[key]) is not bool:
            raise ValueError("État audio ou lecture invalide.")
    return dict(source=source.strip(), bandwidth=mode, volume=volume,
                enabled=value["enabled"], muted=value["muted"])
