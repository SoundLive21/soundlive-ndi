# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.server import ThreadingHTTPServer
from ipaddress import IPv4Address, IPv4Network
import json
from pathlib import Path
import secrets
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic, time

from commun import APP_NAME
from certificats_acme import ReloadableCertificate


PORT = 8766
PRIVATE_NETWORKS = tuple(map(IPv4Network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
)))
USERNAME = "regie"
CONNECTION_AGE = 20
SESSION_DURATION = 30 * 60
SESSION_WARNING = 2 * 60
SESSION_COOKIE = "soundlive_regie"


def is_local_ip(address):
    return any(IPv4Address(address) in network for network in PRIVATE_NETWORKS)


def prepare_access(folder, ip, password=None, renew=False):
    if not is_local_ip(ip):
        raise ValueError("Indique l’IPv4 privée du PC de régie sur le réseau des écrans.")
    address = IPv4Address(ip)
    public = folder / "autorite-soundlive.cer"
    try:
        settings = {} if renew else json.loads((folder / "acces.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        settings = {}
    if password == "":
        raise ValueError("Saisis un mot de passe ou laisse le lanceur en générer un.")
    if password is not None and (len(password.encode("utf-8")) > 256 or not password.isprintable()):
        raise ValueError("Choisis un mot de passe imprimable de 256 octets maximum.")
    if not renew and settings.get("ip") == ip and public.is_file():
        if password is not None:
            settings["credentials"] = password_hash(password)
            save_access(folder, settings)
        return None, public

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ImportError as error:
        raise ValueError("Installe cryptography comme indiqué dans LISEZ-MOI.txt.") from error

    now = datetime.now(timezone.utc)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{APP_NAME} · Régie {ip}")])
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    authority = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=366))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)]))
        .issuer_name(ca_name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1)).not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                     key_encipherment=True, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=False, crl_sign=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    generated = secrets.token_urlsafe(18) if password is None else None
    settings = {
        "ip": ip,
        "credentials": password_hash(generated if generated is not None else password),
        "pem": pem.decode("ascii"),
    }
    folder.mkdir(mode=0o700, exist_ok=True)
    public.write_bytes(authority.public_bytes(serialization.Encoding.DER))
    save_access(folder, settings)
    return generated, public


def save_access(folder, settings):
    with NamedTemporaryFile(dir=folder, delete=False) as temporary:
        temporary.write(json.dumps(settings).encode())
    try:
        Path(temporary.name).replace(folder / "acces.json")
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def derive(credentials, salt):
    return hashlib.scrypt(credentials, salt=salt, n=2**14, r=8, p=1).hex()


def password_hash(password):
    salt = secrets.token_bytes(16)
    return f"scrypt:{salt.hex()}:{derive(f'{USERNAME}:{password}'.encode('utf-8'), salt)}"


def credentials_valid(username, password, expected):
    if username != USERNAME or not 0 < len(password.encode("utf-8")) <= 256:
        return False
    credentials = f"{username}:{password}".encode("utf-8")
    if expected.startswith("scrypt:"):
        _, salt, digest = expected.split(":")
        return hmac.compare_digest(derive(credentials, bytes.fromhex(salt)), digest)
    return hmac.compare_digest(hashlib.sha256(credentials).hexdigest(), expected)


class RemoteConnections:
    def __init__(self, blocked_file):
        self.lock = Lock()
        self.rows = {}
        self.blocked_file = blocked_file
        try:
            self.blocked = {str(IPv4Address(ip)) for ip in json.loads(blocked_file.read_bytes())}
        except FileNotFoundError:
            self.blocked = set()

    def issue(self, address, user_agent, username=USERNAME):
        agent = "".join(char for char in user_agent[:180] if char.isprintable())
        identifier = secrets.token_urlsafe(32)
        with self.lock:
            if address in self.blocked:
                return None
            now = monotonic()
            self.prune(now)
            if len(self.rows) >= 200:
                oldest = min(self.rows, key=lambda key: self.rows[key]["activity"])
                del self.rows[oldest]
            self.rows[identifier] = dict(ip=address, agent=agent, username=username, seen=time(), activity=now,
                                         expires=now + SESSION_DURATION)
            return identifier

    def is_blocked(self, address):
        with self.lock:
            return address in self.blocked

    def observe(self, address, identifier, renew=False):
        with self.lock:
            if address in self.blocked:
                return "remote_blocked", None
            now = monotonic()
            row = self.rows.get(identifier)
            if row is None or row["ip"] != address:
                return "remote_disconnected", None
            if row["expires"] <= now:
                del self.rows[identifier]
                return "remote_disconnected", None
            if renew:
                row["expires"] = now + SESSION_DURATION
            row["activity"] = now
            row["seen"] = time()
            return None, max(1, int((row["expires"] - now) * 1000))

    def principal(self, identifier):
        with self.lock:
            return self.rows.get(identifier, {}).get("username")

    def revoke_user(self, username):
        with self.lock:
            for key, row in list(self.rows.items()):
                if row.get("username") == username:
                    del self.rows[key]

    def disconnect(self, identifier):
        with self.lock:
            self.rows.pop(identifier, None)

    def disconnect_if_owned(self, address, identifier):
        with self.lock:
            row = self.rows.get(identifier)
            if row is not None and row["ip"] == address:
                del self.rows[identifier]

    def change_block(self, address, blocked):
        address = str(IPv4Address(address))
        with self.lock:
            updated = self.blocked | {address} if blocked else self.blocked - {address}
            self.blocked_file.parent.mkdir(mode=0o700, exist_ok=True)
            with NamedTemporaryFile(dir=self.blocked_file.parent, delete=False) as temporary:
                temporary.write(json.dumps(sorted(updated)).encode("ascii"))
            try:
                Path(temporary.name).replace(self.blocked_file)
            finally:
                Path(temporary.name).unlink(missing_ok=True)
            self.blocked = updated
            if blocked:
                for identifier, row in list(self.rows.items()):
                    if row["ip"] == address:
                        del self.rows[identifier]

    def blocked_addresses(self):
        with self.lock:
            return sorted(self.blocked)

    def prune(self, now):
        for key, row in list(self.rows.items()):
            if row["expires"] <= now:
                del self.rows[key]

    def snapshot(self):
        with self.lock:
            now = monotonic()
            self.prune(now)
            return [dict(id=identifier, ip=row["ip"], agent=row["agent"], seen=row["seen"])
                    for identifier, row in sorted(self.rows.items(), key=lambda item: item[1]["seen"], reverse=True)
                    if now - row["activity"] < CONNECTION_AGE]


class RemoteDashboard(ThreadingHTTPServer):
    def __init__(self, folder, handler, receiver_port):
        settings = json.loads((folder / "acces.json").read_text(encoding="utf-8"))
        address = settings["ip"]
        if not is_local_ip(address):
            raise ValueError("L’IPv4 de l’accès distant doit être privée.")
        self.remote_dashboard = True
        self.receiver_port = receiver_port
        self.credentials = settings["credentials"]
        self.auth_lock = Lock()
        self.accounts_path = folder / "utilisateurs.json"
        # L'interface et les restrictions d'adresses LAN restent inchangées.
        # Le certificat externe est rechargé à la prochaine connexion TLS
        # quand son fichier PEM est renouvelé par le client ACME.
        self.certificates = ReloadableCertificate(folder, settings["pem"])
        self.certificate_name = self.certificates.identifier
        super().__init__((address, PORT), handler)

    def verify_request(self, request, client_address):
        return is_local_ip(client_address[0])

    def authenticate(self, username, password):
        with self.auth_lock:
            if username == USERNAME:
                return credentials_valid(username, password, self.credentials)
            account = self.accounts()["users"].get(username)
            return bool(account and account.get("enabled") and credentials_valid(USERNAME, password, account["hash"]))

    def accounts(self):
        try:
            return json.loads(self.accounts_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"users": {}, "groups": {}}

    def grants(self, username):
        db = self.accounts()
        user = db["users"].get(username)
        if not user or not user.get("enabled"):
            return None
        result = {}
        for entry in [user, *(db["groups"][g] for g in user.get("groups", []) if g in db["groups"])]:
            for screen, rights in entry.get("grants", {}).items():
                result.setdefault(screen, set()).update(rights)
        return result

    def edit_accounts(self, data, valid_screens):
        rights = {"view", "preview", "play", "source", "audio", "media", "identify", "maintenance", "configuration"}
        name = data.get("name")
        kind = data.get("kind")
        if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}",name) or name == USERNAME or kind not in ("users","groups"):
            raise ValueError("Identifiant invalide ou réservé.")
        with self.auth_lock:
            db = self.accounts()
            if data.get("delete") is True:
                db[kind].pop(name,None)
                if kind == "groups":
                    for user in db["users"].values():
                        user["groups"] = [g for g in user.get("groups",[]) if g != name]
            else:
                grants = data.get("grants",{})
                if not isinstance(grants,dict) or any(k not in valid_screens or not isinstance(v,list) or any(not isinstance(p,str) or p not in rights for p in v) for k,v in grants.items()):
                    raise ValueError("Écran ou permission invalide.")
                entry = {"grants": {k:sorted(set(v)|{"view"}) for k,v in grants.items() if v}}
                if kind == "users":
                    groups = data.get("groups",[])
                    if not isinstance(groups,list) or any(not isinstance(g,str) or g not in db["groups"] for g in groups):
                        raise ValueError("Groupe inconnu.")
                    entry.update(groups=sorted(set(groups)),enabled=data.get("enabled") is True)
                    password = data.get("password","")
                    old = db[kind].get(name,{})
                    if password:
                        if not isinstance(password,str) or not 8 <= len(password.encode("utf-8")) <= 256 or not password.isprintable():
                            raise ValueError("Mot de passe : 8 caractères minimum, 256 octets maximum.")
                        entry["hash"] = password_hash(password)
                    elif old.get("hash"):
                        entry["hash"] = old["hash"]
                    else:
                        raise ValueError("Mot de passe obligatoire.")
                db[kind][name] = entry
            self.accounts_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=self.accounts_path.parent, delete=False) as tmp:
                tmp.write(json.dumps(db).encode("utf-8"))
            try:
                Path(tmp.name).replace(self.accounts_path)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
            if kind == "users" and getattr(self,"remote_connections",None):
                self.remote_connections.revoke_user(name)

    def public_accounts(self):
        db = self.accounts()
        return {"groups":db["groups"],"users":{k:{f:v for f,v in u.items() if f!="hash"} for k,u in db["users"].items()}}

    def finish_request(self, request, client_address):
        request.settimeout(8)
        try:
            secured = self.certificates.current().wrap_socket(request, server_side=True)
        except OSError:
            return
        with secured:
            super().finish_request(secured, client_address)
