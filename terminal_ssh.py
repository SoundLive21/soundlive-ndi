"""Interactive SSH sessions for the authenticated SoundLive dashboard."""

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import threading
import time


MAX_SESSIONS = 6
IDLE_SECONDS = 65
COLUMNS = 100
LINES = 28
MAX_OUTPUT = 64 * 1024


def key_fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")


class HostKeyCheck(RuntimeError):
    def __init__(self, key, expected=None):
        self.presented = key_fingerprint(key)
        self.expected = expected
        super().__init__("Empreinte SSH différente." if expected else "Empreinte SSH inconnue.")


@dataclass
class Session:
    owner: tuple
    client: object = None
    channel: object = None
    screen: object = None
    stream: object = None
    touched: float = field(default_factory=time.monotonic)
    lock: object = field(default_factory=threading.Lock, repr=False)

    def close(self):
        if self.client is not None:
            self.client.close()


class Terminals:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()
        self.ended = threading.Event()
        threading.Thread(target=self.reap, daemon=True).start()

    def reap(self):
        while not self.ended.wait(10):
            now = time.monotonic()
            with self.lock:
                expired = [self.sessions.pop(key) for key, session in list(self.sessions.items())
                           if now - session.touched > IDLE_SECONDS]
            for session in expired:
                session.close()

    def close(self):
        self.ended.set()
        with self.lock:
            active = list(self.sessions.values())
            self.sessions.clear()
        for session in active:
            session.close()

    def start(self, owner, host, data):
        user = data.get("user", "")
        method = data.get("auth", "key")
        fingerprint = data.get("fingerprint", "")
        if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user):
            raise ValueError("Indique le nom du compte SSH.")
        if method not in ("key", "password"):
            raise ValueError("Choisis la clé SSH ou le mot de passe.")
        if not isinstance(fingerprint, str) or (fingerprint and
                not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint)):
            raise ValueError("Saisis une empreinte SSH SHA256 complète.")
        if method == "password":
            password = data.get("password", "")
            if not isinstance(password, str) or not 0 < len(password) <= 512:
                raise ValueError("Saisis le mot de passe du compte SSH.")
            auth = {"password": password}
        else:
            phrase = data.get("passphrase", "")
            if not isinstance(phrase, str) or len(phrase) > 512:
                raise ValueError("Phrase secrète invalide.")
            identity = Path(os.environ.get("SOUNDLIVE_SSH_KEY") or Path.home() / ".ssh" / "id_soundlive")
            if not identity.is_file():
                raise RuntimeError("La clé privée id_soundlive est introuvable sur la régie (voir README.md).")
            auth = {"key_filename": str(identity), "passphrase": phrase or None}
        try:
            import paramiko
            import pyte
        except ImportError as error:
            raise RuntimeError("Installe Paramiko et pyte sur le PC de régie (voir README.md).") from error

        class HostKeyPolicy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                if not hmac.compare_digest(fingerprint, key_fingerprint(key)):
                    raise HostKeyCheck(key, fingerprint or None)

        identifier = secrets.token_urlsafe(24)
        session = Session(owner, client=paramiko.SSHClient())
        with self.lock:
            if self.ended.is_set() or len(self.sessions) >= MAX_SESSIONS:
                raise RuntimeError("La limite de sessions SSH est atteinte. Ferme un terminal et réessaie.")
            self.sessions[identifier] = session
        try:
            client = session.client
            # An explicit fingerprint pins this connection, even if known_hosts has an older key.
            if not fingerprint:
                client.load_system_host_keys()
            client.set_missing_host_key_policy(HostKeyPolicy())
            client.connect(host, username=user, allow_agent=False, look_for_keys=False,
                           timeout=5, auth_timeout=8, banner_timeout=6, **auth)
            session.screen = pyte.Screen(COLUMNS, LINES)
            session.stream = pyte.ByteStream(session.screen)
            session.channel = client.invoke_shell(term="xterm", width=COLUMNS, height=LINES)
            session.channel.settimeout(3)
        except BaseException as error:
            self.end(owner, identifier)
            if isinstance(error, paramiko.BadHostKeyException):
                raise HostKeyCheck(error.key, key_fingerprint(error.expected_key)) from error
            if isinstance(error, paramiko.PasswordRequiredException):
                raise RuntimeError("La clé privée nécessite sa phrase secrète.") from error
            if isinstance(error, paramiko.AuthenticationException):
                detail = ("Mot de passe refusé. Vérifie les identifiants et que le serveur autorise cette méthode."
                          if method == "password" else
                          "Clé refusée. Vérifie le compte, sa clé publique et la phrase secrète.")
                raise RuntimeError(detail) from error
            if isinstance(error, (paramiko.SSHException, OSError)):
                raise RuntimeError("Connexion SSH impossible. Vérifie le service et l’adresse.") from error
            raise
        return {"session": identifier, "columns": COLUMNS, "lines": LINES}

    def sync(self, owner, identifier, payload):
        if not isinstance(identifier, str) or not isinstance(payload, str) or len(payload) > 4096:
            raise ValueError("Requête terminal invalide.")
        with self.lock:
            session = self.sessions.get(identifier)
            if session is None or session.owner != owner or session.channel is None:
                raise ValueError("La session SSH est fermée.")
            session.touched = time.monotonic()
        try:
            with session.lock:
                channel = session.channel
                if payload and not channel.closed:
                    channel.sendall(payload.encode("utf-8"))
                remaining = MAX_OUTPUT
                while remaining and channel.recv_ready():
                    chunk = channel.recv(min(16384, remaining))
                    if not chunk:
                        break
                    session.stream.feed(chunk)
                    remaining -= len(chunk)
                cursor = session.screen.cursor
                result = {"screen": session.screen.display, "cursor": [cursor.x, cursor.y],
                          "closed": channel.closed and not channel.recv_ready()}
        except (OSError, EOFError):
            self.end(owner, identifier)
            raise ValueError("La connexion SSH a été interrompue.") from None
        if result["closed"]:
            self.end(owner, identifier)
        return result

    def end(self, owner, identifier):
        if not isinstance(identifier, str):
            return
        with self.lock:
            session = self.sessions.get(identifier)
            if session is None or session.owner != owner:
                return
            del self.sessions[identifier]
        session.close()
