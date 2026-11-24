"""Configuration, émission et renouvellement automatique ACME PowerDNS.

Sur Windows, la clé PowerDNS est chiffrée par DPAPI avec le compte Windows.
Sur Linux, le fichier de configuration est privé (0600) au compte de régie.
Aucun secret n'est renvoyé à la page web de la régie ou dans les journaux.
"""
from datetime import datetime, timezone, timedelta
import base64
import ctypes
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from tempfile import NamedTemporaryFile

from acme_powerdns import (AcmeClient, AcmeFailure, PowerDNS, check_identifier,
                           create_account_key, create_certificate_key,
                           load_private_key, private_pem, validate_powerdns_url,
                           validate_zone, wait_public_txt)
from certificats_acme import check_certificate, configure_certificate

FILE = "acme-powerdns.json"
STATUS = "acme-powerdns-status.json"
ACCOUNT = "acme-account.pem"
CERT = "acme-chain.pem"
KEY = "acme-key.pem"
CHECK_SECONDS = 6 * 3600
RENEW_BEFORE = timedelta(days=25)
SCHEDULE = threading.Lock()
PROCESS_LOCK = threading.Lock()
_SERVICE = None


def _atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as stream:
        stream.write(data)
        temporary = Path(stream.name)
    try:
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _dpapi(data, decrypt=False):
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    protect = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    protect.restype = wintypes.BOOL
    protect.argtypes = ([ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(DATA_BLOB)] if decrypt
                        else [ctypes.POINTER(DATA_BLOB), ctypes.c_wchar_p,
                              ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(DATA_BLOB)])
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if decrypt:
        ok = protect(ctypes.byref(source), None, None, None, None,
                     flags, ctypes.byref(destination))
    else:
        ok = protect(ctypes.byref(source), "SoundLive-NDI PowerDNS",
                     None, None, None, flags, ctypes.byref(destination))
    if not ok:
        raise ValueError("Windows DPAPI ne peut pas accéder à la clé API du compte courant.")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(destination.pbData, ctypes.c_void_p))


def _protect_secret(secret):
    if sys.platform == "win32":
        return {"type": "dpapi-user", "data": base64.b64encode(_dpapi(secret.encode())).decode("ascii")}
    return {"type": "restricted-file", "data": secret}


def _restore_secret(data):
    if data.get("type") == "dpapi-user" and sys.platform == "win32":
        return _dpapi(base64.b64decode(data["data"]), decrypt=True).decode("utf-8")
    if data.get("type") == "restricted-file" and sys.platform != "win32":
        return data["data"]
    raise ValueError("La clé PowerDNS est liée à un autre compte ou système d'exploitation.")


def get_config(folder):
    path = Path(folder) / FILE
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {"domain", "zone", "api_url", "server_id", "email", "environment", "secret"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Configuration ACME PowerDNS invalide.")
    return value


def configure(folder, domain, zone, api_url, api_key, email,
              server_id="localhost", environment="production", accept_terms=False):
    if not accept_terms:
        raise ValueError("Accepte les conditions de Let's Encrypt avant de créer un compte.")
    domain = check_identifier(domain)
    try:
        from ipaddress import ip_address
        ip_address(domain)
    except ValueError:
        pass
    else:
        raise ValueError("La validation PowerDNS DNS-01 exige un nom de domaine, pas une adresse IP.")
    zone = validate_zone(zone, domain)
    api_url = validate_powerdns_url(api_url)
    if not isinstance(api_key, str) or len(api_key) > 2048:
        raise ValueError("Clé API PowerDNS invalide.")
    previous = get_config(folder) if Path(folder).is_dir() else None
    if not api_key:
        if not previous:
            raise ValueError("Renseigne une clé API PowerDNS pour la première activation.")
        if (previous["api_url"] != api_url or previous["zone"] != zone
                or previous["server_id"] != server_id):
            raise ValueError("Renseigne de nouveau la clé API si tu changes de serveur ou de zone.")
        api_key = _restore_secret(previous["secret"])
    if (not isinstance(email, str) or len(email) > 250 or
            not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)):
        raise ValueError("Adresse e-mail Let's Encrypt invalide.")
    if environment not in ("production", "staging"):
        raise ValueError("Environnement ACME inconnu.")
    folder = Path(folder)
    if not (folder / "acces.json").is_file():
        raise ValueError("Active d'abord l'accès HTTPS de la régie.")
    # Valide l'adresse et la zone AVANT de stocker la clé API.
    PowerDNS(api_url, api_key, zone, server_id).verify()
    config = {"domain": domain, "zone": zone, "api_url": api_url,
              "server_id": server_id, "email": email, "environment": environment,
              "secret": _protect_secret(api_key)}
    _atomic(folder / FILE, json.dumps(config, separators=(",", ":")).encode("utf-8"))
    update_status(folder, "configured", "Configuration PowerDNS validée. Certificat à demander.")
    return domain


def disable_automatic(folder):
    """Arrête les prochaines émissions ACME sans effacer les certificats existants."""
    folder = Path(folder)
    (folder / FILE).unlink(missing_ok=True)
    update_status(folder, "disabled", "Renouvellement automatique PowerDNS désactivé.")


def public_config(folder):
    config = get_config(folder)
    if not config:
        return None
    return {name: config[name] for name in
            ("domain", "zone", "api_url", "server_id", "email", "environment")}


def update_status(folder, state, message):
    _atomic(Path(folder) / STATUS, json.dumps({
        "state": state, "message": message[:250],
        "checked": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False).encode("utf-8"))


def read_status(folder):
    path = Path(folder) / STATUS
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {"state": "unconfigured", "message": "Certificat PowerDNS non configuré."}


def should_renew(folder):
    config = get_config(folder)
    if not config:
        return False
    if not (Path(folder) / CERT).is_file() or not (Path(folder) / KEY).is_file():
        return True
    try:
        _, expires = check_certificate(config["domain"], Path(folder) / CERT,
                                       Path(folder) / KEY)
        return expires - datetime.now(timezone.utc) <= RENEW_BEFORE
    except (ValueError, OSError):
        return True


def renew_once(folder, progress=None, force=False):
    """Émettre puis basculer les deux PEM après validation de leur correspondance."""
    folder = Path(folder)
    if not PROCESS_LOCK.acquire(blocking=False):
        raise AcmeFailure("Une opération de renouvellement ACME est déjà en cours.")
    try:
        config = get_config(folder)
        if not config:
            raise AcmeFailure("Configure d'abord l'API PowerDNS.")
        if not force and not should_renew(folder):
            update_status(folder, "current", "Certificat encore valide, renouvellement non nécessaire.")
            return "current"

        def notify(message):
            update_status(folder, "running", message)
            if progress:
                progress(message)

        try:
            secret = _restore_secret(config["secret"])
            pdns = PowerDNS(config["api_url"], secret, config["zone"], config["server_id"])
            pdns.progress = notify
            notify(f"Vérification API PowerDNS : zone {config['zone']}, certificat {config['domain']}.")
            pdns.verify()
            account_path = folder / ACCOUNT
            if account_path.is_file():
                account = load_private_key(account_path)
            else:
                account = create_account_key()
                _atomic(account_path, private_pem(account))
            notify(f"Connexion à Let's Encrypt ({config['environment']})…")
            client = AcmeClient(config["environment"], account, config["email"])
            notify("Enregistrement du compte Let's Encrypt…")
            client.account()
            notify("Compte ACME validé. Création de la clé du certificat…")
            key = create_certificate_key()
            chain = client.issue(
                config["domain"], key,
                lambda name, token: pdns.txt(name, token, "EXTEND"),
                lambda name, token: pdns.txt(name, token, "PRUNE"),
                lambda name, token: wait_public_txt(name, token, notify),
                notify)
            notify("Vérification et installation du certificat PEM…")
            temporary_chain = folder / "acme-chain.pending.pem"
            temporary_key = folder / "acme-key.pending.pem"
            _atomic(temporary_chain, chain)
            _atomic(temporary_key, private_pem(key))
            check_certificate(config["domain"], temporary_chain, temporary_key)
            # Le chargeur TLS garde l'ancien certificat en cas de rotation
            # incomplète et valide la nouvelle paire avant son activation.
            temporary_chain.replace(folder / CERT)
            temporary_key.replace(folder / KEY)
            configure_certificate(folder, config["domain"], folder / CERT, folder / KEY)
            notify("Certificat Let's Encrypt obtenu et installé.")
            update_status(folder, "current", "Certificat ACME valide. Renouvellement automatique actif.")
            return "updated"
        except (AcmeFailure, ValueError, OSError) as error:
            # Le détail des erreurs API ne doit jamais inclure les secrets.
            update_status(folder, "failed", str(error)[:250])
            raise
    finally:
        PROCESS_LOCK.release()


class RenewalService:
    """Vérification automatique à intervalles réguliers pendant la régie active."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if not get_config(self.folder) or self.thread is not None:
            return False
        def worker():
            while not self.stop_event.is_set():
                try:
                    renew_once(self.folder)
                except Exception:
                    # Aucun secret ou trace brute d'API ne doit apparaître.
                    try:
                        state = read_status(self.folder)
                        if state.get("state") != "failed":
                            update_status(self.folder, "failed",
                                "Renouvellement interrompu. Vérifie le réseau et PowerDNS.")
                    except OSError:
                        pass
                self.stop_event.wait(CHECK_SECONDS)
        self.thread = threading.Thread(target=worker, name="soundlive-acme-powerdns",
                                       daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
