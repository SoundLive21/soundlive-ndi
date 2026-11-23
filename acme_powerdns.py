"""Client ACME v2 DNS-01 (RFC 8555) pour PowerDNS Authoritative API.

Ce module ne prend en charge que les annuaires officiels Let's Encrypt.
Ne journalise ni clé privée, ni clé d'API DNS, ni token de challenge.
"""
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import secrets
import socket
import ssl
import time
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.x509.oid import NameOID
from certificats_acme import check_certificate, check_identifier

DIRECTORIES = {
    "production": "https://acme-v02.api.letsencrypt.org/directory",
    "staging": "https://acme-staging-v02.api.letsencrypt.org/directory",
}
TIMEOUT = 25


class AcmeFailure(RuntimeError):
    """Erreur d'émission résumée, sans données d'authentification."""


class PowerDNSRejected(AcmeFailure):
    """Requête DNS refusée par le serveur (HTTP 400/422)."""


def b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def encoded(data):
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def post_json(url, data, headers=None, method="POST", timeout=TIMEOUT):
    request = Request(url, data=encoded(data), headers={
        "Accept": "application/json", "Content-Type": "application/json",
        **(headers or {}),
    }, method=method)
    with urlopen(request, timeout=timeout) as response:
        payload = response.read(65536)
        return json.loads(payload) if payload else {}


def validate_powerdns_url(url):
    if not isinstance(url, str) or len(url) > 512:
        raise ValueError("Adresse API PowerDNS invalide.")
    url = url.rstrip("/")
    parsed = urlsplit(url)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.path not in ("", "/", "/api/v1")):
        raise ValueError("Adresse attendue : https://serveur-pdns:port (sans clé API dans l'URL).")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Utilise HTTPS pour l'API PowerDNS.")
    if parsed.scheme == "http":
        from ipaddress import ip_address
        try:
            address = ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is None or not (address.is_private or address.is_loopback):
            raise ValueError("Une API PowerDNS accessible par DNS ou IP publique doit utiliser HTTPS.")
    return url.removesuffix("/api/v1")


def validate_zone(name, identifier):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Indique le nom de la zone PowerDNS.")
    zone = check_identifier(name.strip().rstrip(".")) + "."
    if not (identifier + ".").endswith(zone):
        raise ValueError("Le nom du certificat doit appartenir à la zone PowerDNS choisie.")
    return zone


class PowerDNS:
    def __init__(self, api_url, api_key, zone, server_id="localhost"):
        self.api_url = validate_powerdns_url(api_url)
        if not isinstance(api_key, str) or not api_key or "\n" in api_key or "\r" in api_key:
            raise ValueError("Clé API PowerDNS invalide.")
        if not isinstance(server_id, str) or not server_id or len(server_id) > 128:
            raise ValueError("Identifiant de serveur PowerDNS invalide.")
        self._legacy = False
        self._lock = RLock()
        self.api_key = api_key
        self.zone = zone
        self.endpoint = (self.api_url + "/api/v1/servers/" + quote(server_id, safe="")
                         + "/zones/" + quote(zone, safe="."))

    def request(self, method, body=None):
        request = Request(self.endpoint, data=encoded(body) if body is not None else None,
                          headers={"X-API-Key": self.api_key, "Accept": "application/json",
                                   "Content-Type": "application/json"},
                          method=method)
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read(2_000_000)
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            if error.code in (401, 403):
                raise AcmeFailure("API PowerDNS : accès refusé. Vérifie la clé API et les droits de la zone.") from None
            if error.code == 404:
                raise AcmeFailure("API PowerDNS : zone ou identifiant de serveur introuvable.") from None
            if error.code in (400, 422):
                raise PowerDNSRejected(f"API PowerDNS : requête {method} refusée (HTTP {error.code}).") from None
            raise AcmeFailure(f"API PowerDNS : erreur HTTP {error.code}.") from None
        except (OSError, URLError) as error:
            raise AcmeFailure("API PowerDNS inaccessible. Vérifie l'adresse, le réseau et son certificat TLS.") from error

    def verify(self):
        payload = self.request("GET")
        if payload.get("name", "").rstrip(".").lower() != self.zone.rstrip(".").lower():
            raise AcmeFailure("L'API PowerDNS ne retourne pas la zone attendue.")
        if payload.get("kind", "").lower() in ("slave", "secondary"):
            raise AcmeFailure("La zone PowerDNS est secondaire : configure l'API du serveur primaire.")
        return payload

    def txt(self, fqdn, value, action):
        if action not in ("EXTEND", "PRUNE"):
            raise ValueError("Action PowerDNS non autorisée.")
        fqdn = fqdn.rstrip(".").lower() + "."
        if not fqdn.endswith(self.zone.lower()) or not fqdn.startswith("_acme-challenge."):
            raise ValueError("Seuls les TXT ACME de la zone configurée peuvent être modifiés.")
        if not isinstance(value, str) or len(value) != 43 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in value):
            raise ValueError("Valeur du challenge DNS-01 invalide.")
        record = {"content": '"' + value + '"', "disabled": False}
        with self._lock:
            if not self._legacy:
                try:
                    self.request("PATCH", {"rrsets": [{
                        "name": fqdn, "type": "TXT", "changetype": action,
                        "ttl": 60, "records": [record],
                    }]})
                    return
                except PowerDNSRejected:
                    # Les anciennes versions ne connaissent pas EXTEND/PRUNE.
                    # Ne pas basculer sur erreur réseau ou d'authentification.
                    pass
            self._legacy_txt(fqdn, record, action)
            self._legacy = True

    def _legacy_txt(self, fqdn, record, action):
        # GET doit réussir et retourner une liste complète avant toute écriture.
        # Le verrou protège cette instance, pas les autres clients DNS.
        payload = self.verify()
        rrsets = payload.get("rrsets")
        if not isinstance(rrsets, list):
            raise AcmeFailure("API PowerDNS : liste des RRsets absente, modification annulée.")
        matches = [rr for rr in rrsets
                   if rr.get("name", "").lower() == fqdn and rr.get("type") == "TXT"]
        if len(matches) > 1:
            raise AcmeFailure("API PowerDNS : plusieurs RRsets TXT identiques, modification annulée.")
        existing = matches[0] if matches else None
        records = existing.get("records") if existing else []
        if not isinstance(records, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("content"), str)
                or not isinstance(row.get("disabled"), bool) for row in records):
            raise AcmeFailure("API PowerDNS : enregistrements TXT invalides, modification annulée.")
        ttl = existing.get("ttl") if existing else 60
        if not isinstance(ttl, int) or ttl < 0:
            raise AcmeFailure("API PowerDNS : TTL invalide, modification annulée.")
        if action == "EXTEND":
            if record in records:
                return
            if any(row["content"] == record["content"] for row in records):
                raise AcmeFailure("API PowerDNS : le TXT du challenge existe mais est désactivé.")
            updated = records + [record]
        else:
            updated = [row for row in records if row != record]
            if updated == records:
                return
        rrset = {"name": fqdn, "type": "TXT",
                 "changetype": "REPLACE" if updated else "DELETE"}
        if updated:
            rrset.update(ttl=ttl, records=updated)
        try:
            self.request("PATCH", {"rrsets": [rrset]})
        except PowerDNSRejected as error:
            raise AcmeFailure(
                "API PowerDNS : modification TXT refusée également en mode compatible "
                "(REPLACE/DELETE). " + str(error)) from None



def json_http(method, url, body=None, headers=None):
    request = Request(url, method=method,
                      data=body if isinstance(body, bytes) else
                      (encoded(body) if body is not None else None),
                      headers=headers or {})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(1_000_000)
            content_type = response.headers.get_content_type()
            return (json.loads(raw) if raw and ("json" in content_type)
                    else raw.decode("utf-8", "replace")), {
                        key.lower(): value for key, value in response.headers.items()
                    }, response.status
    except HTTPError as error:
        raw = error.read(4096)
        try:
            problem = json.loads(raw)
            kind = problem.get("type", "")
            detail = problem.get("detail", "")
        except (ValueError, TypeError):
            kind, detail = "", ""
        # Ne jamais inclure la réponse brute, qui peut révéler une requête.
        if kind.endswith("badNonce"):
            raise BadNonce() from None
        detail = detail[:180] if isinstance(detail, str) else ""
        raise AcmeFailure(f"Let's Encrypt : HTTP {error.code} {detail or kind.split(':')[-1]}") from None
    except (URLError, OSError):
        raise AcmeFailure("Serveur ACME Let's Encrypt indisponible ou connexion TLS impossible.") from None


class BadNonce(Exception):
    pass


class AcmeClient:
    def __init__(self, environment, account_key, email):
        if environment not in DIRECTORIES:
            raise ValueError("Environnement Let's Encrypt invalide.")
        self.account_key = account_key
        self.email = email
        self.directory, _, _ = json_http("GET", DIRECTORIES[environment])
        self.account_url = None
        self.nonce = None
        self.jwk = self.public_jwk()

    def public_jwk(self):
        numbers = self.account_key.public_key().public_numbers()
        def raw(value):
            return value.to_bytes((value.bit_length() + 7) // 8, "big")
        return {"kty": "RSA", "n": b64(raw(numbers.n)), "e": b64(raw(numbers.e))}

    def get_nonce(self):
        url = self.directory["newNonce"]
        response, headers, _ = json_http("HEAD", url)
        self.nonce = headers.get("replay-nonce")
        if not self.nonce:
            raise AcmeFailure("Serveur ACME : nonce manquant.")
        return self.nonce

    def sign(self, url, payload):
        for attempt in range(2):
            nonce = self.nonce or self.get_nonce()
            self.nonce = None
            protected = {"alg": "RS256", "nonce": nonce, "url": url}
            if self.account_url:
                protected["kid"] = self.account_url
            else:
                protected["jwk"] = self.jwk
            protected64 = b64(encoded(protected))
            payload64 = b64(encoded(payload)) if payload is not None else ""
            signature = self.account_key.sign(
                (protected64 + "." + payload64).encode("ascii"),
                padding.PKCS1v15(), hashes.SHA256())
            try:
                result, headers, status = json_http(
                    "POST", url, {"protected": protected64, "payload": payload64,
                                  "signature": b64(signature)},
                    {"Content-Type": "application/jose+json", "Accept": "application/json"})
                self.nonce = headers.get("replay-nonce")
                return result, headers, status
            except BadNonce:
                self.get_nonce()
        raise AcmeFailure("Serveur ACME : échec de synchronisation des nonces.")

    def account(self):
        if "termsOfService" not in self.directory.get("meta", {}):
            pass
        result, headers, _ = self.sign(self.directory["newAccount"], {
            "termsOfServiceAgreed": True,
            "contact": ["mailto:" + self.email],
        })
        if result.get("status") != "valid" or not headers.get("location"):
            raise AcmeFailure("Let's Encrypt : compte ACME non créé.")
        self.account_url = headers["location"]

    def post_as_get(self, url):
        return self.sign(url, None)[0]

    def issue(self, identifier, private_key, publish_txt, cleanup_txt, wait_dns, progress):
        names = x509.SubjectAlternativeName([x509.DNSName(identifier)])
        csr = (x509.CertificateSigningRequestBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identifier)]))
               .add_extension(names, critical=False)
               .sign(private_key, hashes.SHA256()))
        order, headers, _ = self.sign(self.directory["newOrder"],
                                      {"identifiers": [{"type": "dns", "value": identifier}]})
        order_url = headers.get("location")
        if not order_url:
            raise AcmeFailure("Let's Encrypt : commande ACME sans identifiant.")
        for authorization_url in order.get("authorizations", []):
            authorization = self.post_as_get(authorization_url)
            if authorization.get("status") == "valid":
                continue
            challenge = next((row for row in authorization.get("challenges", [])
                              if row.get("type") == "dns-01"), None)
            if not challenge:
                raise AcmeFailure("Let's Encrypt : challenge DNS-01 indisponible.")
            account_thumbprint = b64(sha256(encoded(self.jwk)).digest())
            key_authorization = challenge["token"] + "." + account_thumbprint
            txt = b64(sha256(key_authorization.encode("ascii")).digest())
            name = "_acme-challenge." + identifier
            progress("Création du TXT DNS-01 dans PowerDNS…")
            publish_txt(name, txt)
            try:
                progress("Vérification de la propagation du TXT DNS public…")
                wait_dns(name, txt)
                progress("Validation DNS-01 par Let's Encrypt…")
                self.sign(challenge["url"], {})
                for _ in range(30):
                    time.sleep(3)
                    auth = self.post_as_get(authorization_url)
                    if auth.get("status") == "valid":
                        break
                    if auth.get("status") == "invalid":
                        raise AcmeFailure("Let's Encrypt a refusé le challenge DNS-01. Vérifie la délégation DNS publique et les CAA.")
                else:
                    raise AcmeFailure("Validation DNS-01 non achevée après 90 secondes.")
            finally:
                try:
                    cleanup_txt(name, txt)
                except (OSError, ValueError, AcmeFailure):
                    progress("Attention : nettoyage du TXT DNS-01 à vérifier dans PowerDNS.")
        progress("Finalisation et signature du certificat…")
        self.sign(order["finalize"], {"csr": b64(csr.public_bytes(serialization.Encoding.DER))})
        for _ in range(35):
            time.sleep(2)
            status = self.post_as_get(order_url)
            if status.get("status") == "valid" and status.get("certificate"):
                certificate, _, _ = self.sign(status["certificate"], None)
                if not isinstance(certificate, str) or "BEGIN CERTIFICATE" not in certificate:
                    raise AcmeFailure("Let's Encrypt : chaîne PEM du certificat indisponible.")
                return certificate.encode("ascii")
            if status.get("status") == "invalid":
                raise AcmeFailure("Let's Encrypt : demande de certificat rejetée.")
        raise AcmeFailure("Let's Encrypt n'a pas finalisé la demande dans le délai prévu.")


def public_txt_contains(name, value):
    """Recherche via DNS-over-HTTPS pour ne pas confondre split-DNS et DNS public."""
    query = urlencode({"name": name, "type": "TXT"})
    try:
        result, _, _ = json_http("GET", "https://dns.google/resolve?" + query,
                                headers={"Accept": "application/dns-json"})
    except (AcmeFailure, OSError):
        return False
    if not isinstance(result, dict) or result.get("Status") != 0:
        return False
    return any(row.get("type") == 16 and row.get("data", "").strip('"') == value
               for row in result.get("Answer", []))


def wait_public_txt(name, value, progress, timeout=300):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if public_txt_contains(name, value):
            return
        time.sleep(10)
    raise AcmeFailure("Le TXT DNS-01 n'est pas visible depuis le DNS public après 5 minutes. Vérifie les NS, la zone et la propagation.")


def create_account_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


def create_certificate_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def private_pem(key):
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def load_private_key(path):
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
