"""Sauvegarde portable SoundLive NDI. Arrêter la régie avant utilisation."""
import argparse
import base64
from datetime import datetime, timezone
from getpass import getpass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import sys
from tempfile import NamedTemporaryFile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"SLBACKUP1\n"
LIMIT = 64 * 1024 * 1024
ROOT_FILES = {"ecrans.json", "disposition-ecrans.json", "regie-update.pub",
              "source-publications.json", "publication.pub"}
DIRS = {".soundlive-acces", ".soundlive-update", ".soundlive-dhcp"}
ROOT = Path(__file__).resolve().parent


def allowed(name):
    parts = name.split("/")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in (".", "..")
           or p.endswith(".") or p.split(".")[0].lower() in
           {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
            *(f"lpt{i}" for i in range(10))} for p in parts):
        return False
    return (len(parts) == 1 and name in ROOT_FILES
            or len(parts) == 2 and parts[0] in DIRS
            and parts[1] not in {"acme-chain.pending.pem", "acme-key.pending.pem"})


def target(root, name):
    if not allowed(name):
        raise ValueError("Chemin de sauvegarde interdit.")
    path = root / name
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Lien symbolique ou jonction interdit dans la configuration.")
    return path


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.chmod(0o600)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def encode_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def derive(password, salt):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("Mot de passe de sauvegarde : 12 caractères minimum.")
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode("utf-8"))


def collect(root):
    from installation_lecteur import ssh_identity
    from gestion_acme_powerdns import _restore_secret
    from certificats_acme import certificate_configuration
    root = Path(root).resolve()
    files = {}
    size = 0

    def add(name, source):
        nonlocal size
        target(root, name)
        source = Path(source)
        if not source.is_file():
            return
        if source.is_symlink():
            raise ValueError("Un fichier de configuration est un lien symbolique.")
        if source.stat().st_size > LIMIT - size:
            raise ValueError("Sauvegarde de configuration trop volumineuse.")
        data = source.read_bytes()
        size += len(data)
        if size > LIMIT:
            raise ValueError("Sauvegarde de configuration trop volumineuse.")
        files[name] = data

    for name in ROOT_FILES:
        add(name, target(root, name))
    for directory in DIRS:
        path = root / directory
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Répertoire de configuration non portable.")
        if path.is_dir():
            for file in sorted(path.iterdir()):
                name = directory + "/" + file.name
                if allowed(name) and file.is_file():
                    add(name, file)
    identity = ssh_identity(root)
    for suffix in ("", ".pub"):
        add(".soundlive-update/ssh" + suffix, Path(str(identity) + suffix))
    config_name = ".soundlive-acces/acme-powerdns.json"
    if config_name in files:
        config = json.loads(files[config_name])
        config["secret"] = {"type": "backup", "data": _restore_secret(config["secret"])}
        files[config_name] = encode_json(config)
    cert = certificate_configuration(root / ".soundlive-acces")
    if cert:
        for field, name in (("fullchain", "acme-chain.pem"), ("private_key", "acme-key.pem")):
            source = Path(cert[field])
            if not source.is_file():
                raise ValueError("Un fichier du certificat configuré est absent.")
            relative = ".soundlive-acces/" + name
            # Les certificats externes ne doivent pas écraser un compte ACME distinct.
            if source.resolve() != (root / relative).resolve():
                relative = ".soundlive-acces/backup-external-" + name
            add(relative, source)
            cert[field] = relative
        files[".soundlive-acces/certificat-acme.json"] = encode_json(cert)
    return files


def create_backup(root, destination, password):
    destination = Path(destination)
    if destination.suffix.lower() != '.slbackup' or destination.is_symlink():
        raise ValueError('Choisis un fichier .slbackup sans lien symbolique.')
    files = collect(root)
    if not files:
        raise ValueError("Aucune configuration à sauvegarder.")
    payload = encode_json({"format": 1, "created": datetime.now(timezone.utc).isoformat(),
                           "files": {name: {"sha256": sha256(data).hexdigest(),
                                            "data": base64.b64encode(data).decode("ascii")}
                                     for name, data in files.items()}})
    if len(payload) > LIMIT:
        raise ValueError("Archive trop volumineuse.")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = MAGIC + salt + nonce
    encrypted = AESGCM(derive(password, salt)).encrypt(nonce, payload, header)
    atomic(Path(destination), header + encrypted)
    return len(files)


def read_backup(source, password):
    source = Path(source)
    if source.stat().st_size > LIMIT + 128:
        raise ValueError("Archive trop volumineuse.")
    raw = source.read_bytes()
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 44:
        raise ValueError("Format de sauvegarde inconnu.")
    offset = len(MAGIC)
    salt, nonce = raw[offset:offset+16], raw[offset+16:offset+28]
    header = raw[:offset+28]
    try:
        payload = AESGCM(derive(password, salt)).decrypt(nonce, raw[len(header):], header)
    except InvalidTag:
        raise ValueError("Mot de passe incorrect ou archive altérée.") from None
    manifest = json.loads(payload)
    if manifest.get("format") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Version de sauvegarde non prise en charge.")
    files, seen = {}, set()
    for name, entry in manifest["files"].items():
        if not allowed(name) or name.casefold() in seen:
            raise ValueError("Nom de fichier non portable ou dupliqué.")
        seen.add(name.casefold())
        data = base64.b64decode(entry["data"], validate=True)
        if sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Empreinte de fichier incorrecte.")
        files[name] = data
    if not files:
        raise ValueError("Archive vide.")
    return files


def restore_backup(root, source, password, ip=None):
    """Fusion contrôlée : ne touche ni au système ni aux fichiers hors périmètre."""
    from gestion_acme_powerdns import _protect_secret
    from acces_regie import is_local_ip
    root = Path(root).resolve()
    files = read_backup(source, password)
    if ip and not is_local_ip(ip):
        raise ValueError("L'adresse HTTPS doit être une IPv4 locale.")
    name = ".soundlive-acces/acme-powerdns.json"
    if name in files:
        config = json.loads(files[name])
        if config["secret"]["type"] != "backup":
            raise ValueError("Secret de sauvegarde non portable.")
        config["secret"] = _protect_secret(config["secret"]["data"])
        files[name] = encode_json(config)
    name = ".soundlive-acces/certificat-acme.json"
    if name in files:
        config = json.loads(files[name])
        for field in ("fullchain", "private_key"):
            relative = config[field]
            if relative not in files or not relative.startswith(".soundlive-acces/"):
                raise ValueError("Certificat incomplet dans l'archive.")
            config[field] = str(target(root, relative))
        files[name] = encode_json(config)
    name = ".soundlive-acces/acces.json"
    if name in files and ip:
        config = json.loads(files[name])
        config["ip"] = ip
        files[name] = encode_json(config)
    name = ".soundlive-dhcp/configuration.json"
    if name in files:
        config = json.loads(files[name])
        config["enabled"] = False  # Revalider l'interface avant d'émettre du DHCP.
        files[name] = encode_json(config)

    # Sauvegarde de sécurité chiffrée avant toute écriture, si une régie existe.
    previous = collect(root)
    safety = None
    if previous:
        backup_dir = root / ".soundlive-backups"
        if backup_dir.is_symlink() or (hasattr(backup_dir, "is_junction") and backup_dir.is_junction()):
            raise ValueError("Dossier de sauvegarde de sécurité non sûr.")
        safety = backup_dir / (
            "avant-restauration-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-" + secrets.token_hex(4) + ".slbackup")
        create_backup(root, safety, password)
    # Retirer seulement les configurations ACME absentes de la sauvegarde.
    remove = {name for name in (".soundlive-acces/certificat-acme.json",
                               ".soundlive-acces/acme-powerdns.json")
              if name not in files}
    paths = {name: target(root, name) for name in set(files) | remove}
    if Path(source).resolve() in paths.values():
        raise ValueError("L'archive ne doit pas remplacer un fichier de configuration.")
    if any(path.exists() and not path.is_file() for path in paths.values()):
        raise ValueError("Un chemin de restauration n'est pas un fichier.")
    originals = {name: path.read_bytes() if path.is_file() else None
                 for name, path in paths.items()}
    changed = []
    try:
        for name, data in files.items():
            atomic(paths[name], data)
            changed.append(name)
        for name in remove:
            paths[name].unlink(missing_ok=True)
            changed.append(name)
    except BaseException:
        for name in reversed(changed):
            if originals[name] is None:
                paths[name].unlink(missing_ok=True)
            else:
                atomic(paths[name], originals[name])
        raise
    return len(files), safety


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["sauvegarder", "verifier", "restaurer"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--dossier", type=Path, default=ROOT)
    parser.add_argument("--ip", help="Nouvelle IPv4 locale de la régie")
    args = parser.parse_args()
    password = getpass("Mot de passe de sauvegarde : ")
    if args.action == "sauvegarder":
        if password != getpass("Confirmer : "):
            raise ValueError("Les mots de passe diffèrent.")
        print(f"{create_backup(args.dossier, args.archive, password)} fichiers sauvegardés.")
    elif args.action == "verifier":
        print(f"Archive valide : {len(read_backup(args.archive, password))} fichiers.")
    else:
        if input("Régie arrêtée ? Écraser les fichiers concernés ? [oui/N] ") != "oui":
            return
        count, safety = restore_backup(args.dossier, args.archive, password, args.ip)
        print(f"{count} fichiers restaurés. Sauvegarde de sécurité : {safety or 'aucune'}.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Sauvegarde/restauration interrompue : {error}")
