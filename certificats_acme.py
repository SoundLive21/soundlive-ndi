"""Certificat public (par exemple ACME/Let's Encrypt) pour la régie HTTPS.

L'émission et le renouvellement restent du ressort d'un client ACME externe.
La régie charge les PEM locaux et suit leurs rotations sans exposer le port
d'administration au réseau public.
"""
from datetime import datetime, timezone
from ipaddress import ip_address
import json
import logging
from pathlib import Path
import re
import ssl
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic

CONFIG_NAME = "certificat-acme.json"
RELOAD_INTERVAL = 10
LOGGER = logging.getLogger(__name__)


def check_identifier(name):
    if not isinstance(name, str) or not name or len(name) > 253:
        raise ValueError("Indique un nom DNS public ou une adresse IP publique.")
    try:
        address = ip_address(name)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise ValueError("Les certificats ACME d'adresse IP exigent une IP publique, pas une IP du LAN.")
        return str(address)
    ascii_name = name.removesuffix(".").lower()
    labels = ascii_name.split(".")
    if ascii_name.endswith((".local", ".internal", ".localhost", ".test", ".invalid")):
        raise ValueError("Les noms locaux ou réservés ne peuvent pas obtenir de certificat ACME public.")
    if len(labels) < 2 or any(not 1 <= len(label) <= 63 or
                               not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                               for label in labels) or labels[-1].isdigit():
        raise ValueError("Indique un nom DNS public valide (ex. regie.exemple.fr).")
    return ascii_name


def check_certificate(identifier, fullchain, private_key):
    """Vérifie la paire clé/certificat, la validité et le SAN prévu.

    Cette vérification ne remplace PAS la validation de confiance du
    navigateur : un certificat auto-signé n'est pas automatiquement ACME.
    """
    try:
        from cryptography import x509
    except ImportError as error:
        raise ValueError("Installe cryptography pour vérifier le certificat ACME.") from error
    paths = (Path(fullchain).expanduser(), Path(private_key).expanduser())
    for path in paths:
        if not path.is_absolute() or not path.is_file():
            raise ValueError("Le certificat et sa clé doivent être des fichiers PEM existants avec chemins absolus.")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.load_cert_chain(str(paths[0]), str(paths[1]))
    except (OSError, ssl.SSLError) as error:
        raise ValueError("Certificat PEM et clé privée illisibles ou incompatibles.") from error
    try:
        first_cert = paths[0].read_bytes().split(b"-----END CERTIFICATE-----", 1)[0] + b"-----END CERTIFICATE-----\n"
        cert = x509.load_pem_x509_certificate(first_cert)
        now = datetime.now(timezone.utc)
        start = cert.not_valid_before_utc
        end = cert.not_valid_after_utc
        if not start <= now < end:
            raise ValueError("Le certificat est expiré ou n'est pas encore valable.")
        try:
            names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound as error:
            raise ValueError("Le certificat ne contient pas d'extension SubjectAlternativeName.") from error
        try:
            address = ip_address(identifier)
        except ValueError:
            address = None
        if address is not None:
            match = address in names.get_values_for_type(x509.IPAddress)
        else:
            permitted = names.get_values_for_type(x509.DNSName)
            match = identifier.lower() in {entry.lower() for entry in permitted}
            # Wildcards DNS autorisés pour un seul niveau de sous-domaine.
            if not match:
                for entry in permitted:
                    if entry.startswith("*.") and identifier.count(".") == entry.count(".") and identifier.endswith(entry[1:]):
                        match = True
                        break
        if not match:
            raise ValueError("Le certificat ne contient pas le DNS ou l'IP demandé dans ses SAN.")
    except (OSError, TypeError) as error:
        raise ValueError("Impossible de lire le certificat X.509 fourni.") from error
    return ctx, end


def certificate_configuration(folder):
    path = Path(folder) / CONFIG_NAME
    if not path.is_file():
        return None
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or set(config) != {"identifier", "fullchain", "private_key"}:
        raise ValueError("Configuration du certificat externe invalide.")
    return config


def configure_certificate(folder, identifier, fullchain, private_key):
    identifier = check_identifier(identifier)
    chain = Path(fullchain).expanduser().resolve(strict=True)
    key = Path(private_key).expanduser().resolve(strict=True)
    check_certificate(identifier, chain, key)
    folder = Path(folder)
    if not (folder / "acces.json").is_file():
        raise ValueError("Active d'abord l'accès HTTPS de la régie avant de configurer ACME.")
    payload = {"identifier": identifier, "fullchain": str(chain), "private_key": str(key)}
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as stream:
        json.dump(payload, stream)
        temporary = Path(stream.name)
    try:
        temporary.chmod(0o600)
        temporary.replace(folder / CONFIG_NAME)
    finally:
        temporary.unlink(missing_ok=True)
    return identifier


def disable_certificate(folder):
    (Path(folder) / CONFIG_NAME).unlink(missing_ok=True)


class ReloadableCertificate:
    """Recharge à chaud les PEM, y compris après première émission ACME.

    Le contexte TLS actuellement valide est conservé pendant une rotation
    incomplète. Si les fichiers expirent et ne sont pas renouvelés, le contexte
    local SoundLive demeure utilisable comme solution de secours.
    """

    def __init__(self, folder, fallback_pem):
        self.folder = Path(folder)
        self.lock = Lock()
        self.last_checked = 0.0
        self.signature = None
        self.identifier = None
        self.expires = None
        self.error = None
        self.config = None
        self.fallback = self._fallback_context(fallback_pem)
        self.context = self.fallback
        self.current(force=True)

    def _fallback_context(self, pem):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        with NamedTemporaryFile(dir=self.folder, delete=False) as certificate:
            certificate.write(pem.encode("ascii"))
        try:
            ctx.load_cert_chain(certificate.name)
        finally:
            Path(certificate.name).unlink(missing_ok=True)
        return ctx

    def _signature(self, config):
        def info(filename):
            path = Path(filename)
            stat = path.stat()
            return (stat.st_mtime_ns, stat.st_size)
        return tuple(info(config[key]) for key in ("fullchain", "private_key"))

    def current(self, force=False):
        if not force and monotonic() - self.last_checked < RELOAD_INTERVAL:
            return self.context
        with self.lock:
            if not force and monotonic() - self.last_checked < RELOAD_INTERVAL:
                return self.context
            self.last_checked = monotonic()
            try:
                config = certificate_configuration(self.folder)
                if config is None:
                    if self.config is not None:
                        self.context = self.fallback
                        self.identifier = None
                        self.expires = None
                        self.signature = None
                    self.config = None
                    self.error = None
                    return self.context
                signature = self._signature(config)
                if config != self.config or signature != self.signature:
                    context, expiry = check_certificate(**config)
                    self.context = context
                    self.expires = expiry
                    self.signature = signature
                    self.config = config
                    self.identifier = config["identifier"]
                    self.error = None
                    LOGGER.info("Certificat TLS ACME chargé pour %s", self.identifier)
            except (OSError, ValueError, ssl.SSLError) as error:
                message = str(error)
                if self.error != message:
                    LOGGER.warning("Certificat ACME non rechargé : %s", message)
                self.error = message
            return self.context
