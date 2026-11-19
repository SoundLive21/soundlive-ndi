"""Prépare sur la régie et récupère sur le lecteur les clés publiques d'installation."""
import argparse
from hashlib import sha256
from hmac import compare_digest
import json
import os
from pathlib import Path
import re
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from mise_a_jour import PUBLIC_KEY, SAFE_ID, prepare_key, ssh_keygen, use_signing_key

ROOT = Path(__file__).resolve().parent
SSH_PUBLIC = "id_soundlive.pub"
KEYS_PATH = "/api/installation-keys"
LIMIT = 8192


def ssh_identity(root=ROOT):
    if configured := os.environ.get("SOUNDLIVE_SSH_KEY"):
        return Path(configured).expanduser()
    portable = root / ".soundlive-update" / "ssh"
    if portable.exists() or portable.with_suffix(".pub").exists():
        return portable
    legacy = Path.home() / ".ssh" / "id_soundlive"
    return legacy if legacy.exists() or legacy.with_suffix(".pub").exists() else portable


def public_line(text, *, update_key=False):
    allowed = r"ssh-ed25519" if update_key else r"ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)"
    if not isinstance(text, str) or not re.fullmatch(
            rf"({allowed}) [A-Za-z0-9+/=]+(?: [^\r\n]*)?", text):
        raise ValueError("Le kit doit contenir deux clés publiques OpenSSH valides.")
    return " ".join(text.split()[:2])


def installation_keys(root):
    ssh = ssh_identity(root)
    if not ssh.is_file() or not (root / ".soundlive-update" / "regie").is_file():
        raise ValueError("Les clés privées de la régie doivent être présentes pour préparer les installations.")
    updates = public_line((root / PUBLIC_KEY).read_text(encoding="utf-8").strip(), update_key=True)
    access = public_line(Path(f"{ssh}.pub").read_text(encoding="utf-8").strip())
    return json.dumps({PUBLIC_KEY: updates, SSH_PUBLIC: access}, separators=(",", ":")).encode("ascii")


def prepare_installations(root):
    if (root / PUBLIC_KEY).exists() and not (root / ".soundlive-update" / "regie").is_file():
        raise ValueError("Restaure la clé privée de signature de l’ancienne régie.")
    ssh = ssh_identity(root)
    public = Path(f"{ssh}.pub")
    if not ssh.is_file():
        if public.exists():
            raise ValueError("Restaure la clé privée d’accès SSH de l’ancienne régie.")
        ssh.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        ssh_keygen("-t", "ed25519", "-N", "", "-C", "soundlive-installation", "-f", ssh)
    if not public.is_file():
        public.write_bytes(use_signing_key(ssh, "-y", "-f", ssh))
    prepare_key(root)
    return sha256(installation_keys(root)).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def fetch_keys(server, fingerprint, destination):
    url = urlsplit(server)
    if (url.scheme != "http" or not url.hostname or url.port == 0 or url.username is not None
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("Indique l’adresse HTTP de la régie des lecteurs.")
    if not isinstance(fingerprint, str) or not SAFE_ID.fullmatch(fingerprint):
        raise ValueError("Utilise l’empreinte du kit fournie dans la commande d’association.")
    with build_opener(NoRedirect).open(server.rstrip("/") + KEYS_PATH, timeout=10) as response:
        contents = response.read(LIMIT + 1)
    if len(contents) > LIMIT or not compare_digest(fingerprint, sha256(contents).hexdigest()):
        raise ValueError("Les clés de la régie ont changé. Génère une nouvelle commande d’association.")
    keys = json.loads(contents)
    if not isinstance(keys, dict) or keys.keys() != {PUBLIC_KEY, SSH_PUBLIC}:
        raise ValueError("La régie n’a pas envoyé les deux clés publiques.")
    for name, line in keys.items():
        destination.joinpath(name).write_text(public_line(line, update_key=name == PUBLIC_KEY) + "\n",
                                               encoding="ascii")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("regie")
    parser.add_argument("empreinte")
    parser.add_argument("dossier", type=Path)
    args = parser.parse_args()
    try:
        fetch_keys(args.regie, args.empreinte, args.dossier)
    except (ValueError, OSError, URLError) as error:
        parser.exit(1, f"Clés publiques de la régie : {error}\n")
