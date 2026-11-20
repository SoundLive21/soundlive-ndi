"""Interactive SSH sessions for the authenticated SoundLive dashboard."""

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from commun import SSH_FINGERPRINT
from installation_lecteur import ROOT, ssh_identity


MAX_SESSIONS = 6
IDLE_SECONDS = 65
COLUMNS = 100
LINES = 28
MAX_OUTPUT = 64 * 1024
GROUP_LIMIT = 32
GROUP_OUTPUT = 16384
GROUP_TIMEOUT = 45
USER = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def key_fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")


class HostKeyCheck(RuntimeError):
    def __init__(self, key, expected=None):
        self.presented = key_fingerprint(key)
        self.expected = expected
        super().__init__("Empreinte SSH différente." if expected else "Empreinte SSH inconnue.")


def connect_client(paramiko, client, host, user, auth, fingerprints, on_verified=None):
    warning = ""
    expected = (fingerprints,) if isinstance(fingerprints, str) and fingerprints else fingerprints

    class HostKeyPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, _client, _hostname, key):
            nonlocal warning
            presented = key_fingerprint(key)
            if not any(hmac.compare_digest(fingerprint, presented) for fingerprint in expected):
                raise HostKeyCheck(key, ", ".join(expected) or None)
            if on_verified:
                warning = on_verified()

    if not expected:
        client.load_system_host_keys()
    client.set_missing_host_key_policy(HostKeyPolicy())
    try:
        client.connect(host, username=user, allow_agent=False, look_for_keys=False,
                       timeout=5, auth_timeout=8, banner_timeout=6, **auth)
    except paramiko.BadHostKeyException as error:
        raise HostKeyCheck(error.key, key_fingerprint(error.expected_key)) from error
    except paramiko.PasswordRequiredException as error:
        raise RuntimeError("La clé privée nécessite sa phrase secrète.") from error
    except paramiko.AuthenticationException as error:
        detail = ("Mot de passe refusé. Vérifie les identifiants et que le serveur autorise cette méthode."
                  if "password" in auth else "Clé refusée. Vérifie le compte SSH et autorise la clé de la régie.")
        raise RuntimeError(detail) from error
    except (paramiko.SSHException, OSError) as error:
        raise RuntimeError("Connexion SSH impossible. Vérifie le service et l’adresse.") from error
    return warning


def group_command(client, command, ssh_error):
    output, errors = bytearray(), bytearray()
    truncated = False
    deadline = time.monotonic() + GROUP_TIMEOUT
    timer = threading.Timer(GROUP_TIMEOUT, client.close)
    timer.daemon = True
    timer.start()
    try:
        with client.get_transport().open_session(timeout=5) as channel:
            channel.exec_command(command)
            channel.shutdown_write()
            while time.monotonic() < deadline:
                status_ready = channel.exit_status_ready()
                received = False
                for ready, read, destination in ((channel.recv_ready, channel.recv, output),
                                                  (channel.recv_stderr_ready, channel.recv_stderr, errors)):
                    if ready():
                        chunk = read(4096)
                        available = GROUP_OUTPUT - len(destination)
                        truncated |= len(chunk) > available
                        destination.extend(chunk[:available])
                        received = True
                if not received:
                    if status_ready and (channel.closed or channel.eof_received):
                        code = channel.recv_exit_status()
                        message = "Connexion fermée sans code de sortie." if code < 0 else ""
                        return dict(state="done" if code == 0 else "failed", code=code, message=message,
                                    stdout=output.decode("utf-8", "replace"),
                                    stderr=errors.decode("utf-8", "replace"), truncated=truncated)
                    time.sleep(0.04)
    except (OSError, EOFError, ssh_error):
        if time.monotonic() < deadline:
            raise
    finally:
        timer.cancel()
    return dict(state="timeout", message="Délai SSH dépassé. Vérifie l’état du lecteur avant de relancer.",
                stdout=output.decode("utf-8", "replace"), stderr=errors.decode("utf-8", "replace"),
                truncated=truncated)


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
    def __init__(self, pin_file=ROOT / ".soundlive-update" / "ssh-lecteurs.json"):
        self.sessions = {}
        self.groups = {}
        self.executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ssh-groupe")
        self.pin_file = pin_file
        try:
            pins = json.loads(pin_file.read_text(encoding="ascii"))
        except (OSError, ValueError):
            pins = {}
        self.pins = {key: value for key, value in pins.items()
                     if isinstance(value, str) and SSH_FINGERPRINT.fullmatch(value)} if isinstance(pins, dict) else {}
        self.lock = threading.Lock()
        self.ended = threading.Event()
        threading.Thread(target=self.reap, daemon=True).start()

    def reap(self):
        while not self.ended.wait(10):
            now = time.monotonic()
            with self.lock:
                for key, job in list(self.groups.items()):
                    if job["finished"] and now - job["finished"] > 1800:
                        del self.groups[key]
                expired = [self.sessions.pop(key) for key, session in list(self.sessions.items())
                           if now - session.touched > IDLE_SECONDS]
            for session in expired:
                session.close()

    def close(self):
        self.ended.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
        with self.lock:
            active = list(self.sessions.values())
            self.sessions.clear()
        for session in active:
            session.close()

    def connect_reader(self, paramiko, client, key, address, user, auth, entered, enrolled=()):
        with self.lock:
            expected = entered or self.pins.get(key) or enrolled
        callback = (lambda: self.save_pin(key, entered)) if entered else None
        return connect_client(paramiko, client, address, user, auth, expected, callback)

    def start(self, owner, key, host, data, enrolled=()):
        user = data.get("user", "")
        method = data.get("auth", "key")
        fingerprint = data.get("fingerprint", "")
        if not isinstance(user, str) or not USER.fullmatch(user):
            raise ValueError("Indique le nom du compte SSH.")
        if method not in ("key", "password"):
            raise ValueError("Choisis la clé SSH ou le mot de passe.")
        if not isinstance(fingerprint, str) or (fingerprint and
                not SSH_FINGERPRINT.fullmatch(fingerprint)):
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
            identity = ssh_identity()
            if not identity.is_file():
                raise RuntimeError("La clé privée id_soundlive est introuvable sur la régie (voir README.md).")
            auth = {"key_filename": str(identity), "passphrase": phrase or None}
        try:
            import paramiko
            import pyte
        except ImportError as error:
            raise RuntimeError("Installe Paramiko et pyte sur le PC de régie (voir README.md).") from error

        identifier = secrets.token_urlsafe(24)
        session = Session(owner, client=paramiko.SSHClient())
        with self.lock:
            if self.ended.is_set() or len(self.sessions) >= MAX_SESSIONS:
                raise RuntimeError("La limite de sessions SSH est atteinte. Ferme un terminal et réessaie.")
            self.sessions[identifier] = session
        try:
            client = session.client
            warning = self.connect_reader(paramiko, client, key, host, user, auth, fingerprint, enrolled)
            session.screen = pyte.Screen(COLUMNS, LINES)
            session.stream = pyte.ByteStream(session.screen)
            session.channel = client.invoke_shell(term="xterm", width=COLUMNS, height=LINES)
            session.channel.settimeout(3)
        except BaseException as error:
            self.end(owner, identifier)
            if isinstance(error, (paramiko.SSHException, OSError)):
                raise RuntimeError("Connexion SSH impossible. Vérifie le service et l’adresse.") from error
            raise
        return {"session": identifier, "columns": COLUMNS, "lines": LINES, "pin_warning": warning}

    def start_group(self, owner, targets, data):
        user, command = data.get("user"), data.get("command")
        pins = data.get("fingerprints", {})
        if not isinstance(user, str) or not USER.fullmatch(user):
            raise ValueError("Indique le nom du compte SSH.")
        if not isinstance(command, str) or not command.strip() or len(command.encode("utf-8")) > 4096 or "\0" in command:
            raise ValueError("Saisis une commande SSH de 4 Kio maximum.")
        if not isinstance(pins, dict) or not all(key in targets and isinstance(value, str) and SSH_FINGERPRINT.fullmatch(value)
                                                for key, value in pins.items()):
            raise ValueError("Saisis uniquement les empreintes SSH SHA256 vérifiées des lecteurs sélectionnés.")
        identity = ssh_identity()
        if not identity.is_file():
            raise ValueError("Prépare d’abord la clé SSH de la régie.")
        try:
            import paramiko
        except ImportError as error:
            raise RuntimeError("Installe Paramiko avec le Python de la régie (voir README.md).") from error
        with self.lock:
            if self.ended.is_set() or any(not job["finished"] for job in self.groups.values()):
                raise RuntimeError("Une commande SSH groupée est déjà en cours sur la régie.")
            if len(self.groups) >= 8:
                del self.groups[next(iter(self.groups))]
            identifier = secrets.token_urlsafe(24)
            rows = {key: dict(id=key, name=target["name"], state="pending" if "address" in target else "failed",
                              message=target.get("error", "")) for key, target in targets.items()}
            job = self.groups[identifier] = dict(owner=owner, results=rows, finished=None)
            for key, target in targets.items():
                if "address" in target:
                    pin = pins.get(key, "")
                    self.executor.submit(self.run_target, paramiko, job, key, target, user, command, identity, pin)
            if not any(row["state"] == "pending" for row in rows.values()):
                job["finished"] = time.monotonic()
        return {"job": identifier}

    def run_target(self, paramiko, job, key, target, user, command, identity, pin):
        client = None
        try:
            client = paramiko.SSHClient()
            message = self.connect_reader(paramiko, client, key, target["address"], user,
                                          {"key_filename": str(identity)}, pin,
                                          target.get("ssh_host_fingerprints", ()))
            result = group_command(client, command, paramiko.SSHException)
            if message:
                result["message"] = " ".join(filter(None, (result["message"], message)))
        except HostKeyCheck as error:
            result = dict(state="host_key", message=str(error), presented=error.presented, expected=error.expected)
        except (paramiko.SSHException, OSError, RuntimeError, EOFError) as error:
            result = dict(state="failed", message=str(error) or "La connexion SSH a été interrompue.")
        except Exception:
            result = dict(state="failed", message="Le client SSH de la régie a rencontré une erreur.")
        finally:
            if client is not None:
                client.close()
        with self.lock:
            job["results"][key].update(result)
            if all(row["state"] != "pending" for row in job["results"].values()):
                job["finished"] = time.monotonic()

    def save_pin(self, key, fingerprint):
        with self.lock:
            if self.pins.get(key) == fingerprint:
                return ""
            new = {**self.pins, key: fingerprint}
            try:
                self.pin_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                temporary = self.pin_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(new), encoding="ascii")
                temporary.replace(self.pin_file)
            except OSError:
                return "L’empreinte vérifiée n’a pas pu être enregistrée sur la régie."
            self.pins = new
        return ""

    def group_status(self, owner, identifier):
        with self.lock:
            job = self.groups.get(identifier) if isinstance(identifier, str) else None
            if job is None or job["owner"] != owner:
                raise ValueError("Cette exécution SSH est indisponible.")
            return {"done": bool(job["finished"]), "results": [row.copy() for row in job["results"].values()]}

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
