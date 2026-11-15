# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Paquets signés du client SoundLive-NDI et activation locale avec retour arrière."""
from base64 import b64decode, b64encode
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

FILES = ("ecran.py", "commun.py", "diagnostic.py", "mise_a_jour.py")
LIMIT = 512 * 1024
NAMESPACE = "soundlive-update"
SAFE_ID = re.compile(r"[a-f0-9]{64}")
PUBLIC_KEY = "regie-update.pub"
RESTART = 75


def contents(folder):
    files = {name: (folder / name).read_bytes() for name in FILES}
    if sum(map(len, files.values())) > LIMIT:
        raise ValueError("Le paquet du client est trop volumineux.")
    return files


def digest(files):
    checksums = {name: sha256(files[name]).hexdigest() for name in FILES}
    return sha256(json.dumps(checksums, sort_keys=True).encode("ascii")).hexdigest()


def manifest(device, job, nonce, version):
    return json.dumps([NAMESPACE, device, job, nonce, version], separators=(",", ":")).encode("ascii")


def ssh_keygen(*args, data=None):
    try:
        result = subprocess.run(["ssh-keygen", *map(str, args)], input=data,
                                capture_output=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except FileNotFoundError as error:
        raise RuntimeError("Client OpenSSH requis : ssh-keygen est introuvable.") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("ssh-keygen n’a pas répondu.") from error
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(message or "Échec de la vérification OpenSSH.")
    return result.stdout


def prepare_key(folder):
    private = folder / ".soundlive-update" / "regie"
    private.parent.mkdir(mode=0o700, exist_ok=True)
    if not private.is_file():
        ssh_keygen("-t", "ed25519", "-N", "", "-C", NAMESPACE, "-f", private)
    public = folder / PUBLIC_KEY
    public.write_bytes(ssh_keygen("-y", "-f", private))
    return public


def package(private, files, device, job, nonce, version):
    with TemporaryDirectory() as tmp:
        message = Path(tmp) / "manifest"
        message.write_bytes(manifest(device, job, nonce, version))
        ssh_keygen("-Y", "sign", "-f", private, "-n", NAMESPACE, message)
        signature = message.with_suffix(".sig").read_bytes().replace(b"\r\n", b"\n")
    # OpenSSH 9.2 exige un LF à la fin de l'en-tête de la signature.
    if not (signature.startswith(b"-----BEGIN SSH SIGNATURE-----\n")
            and signature.endswith(b"\n-----END SSH SIGNATURE-----\n")):
        raise RuntimeError("ssh-keygen a produit un bloc de signature invalide.")
    return dict(version=version, signature=b64encode(signature).decode("ascii"),
                files={name: b64encode(value).decode("ascii") for name, value in files.items()})


def verify(public, bundle, device, job, nonce):
    version = bundle["version"]
    if not isinstance(version, str) or not SAFE_ID.fullmatch(version):
        raise ValueError("Empreinte de mise à jour invalide.")
    encoded = bundle["files"]
    if not isinstance(encoded, dict) or set(encoded) != set(FILES):
        raise ValueError("Contenu de mise à jour inattendu.")
    files = {name: b64decode(encoded[name], validate=True) for name in FILES}
    if sum(map(len, files.values())) > LIMIT or digest(files) != version:
        raise ValueError("Paquet de mise à jour altéré.")
    signature = b64decode(bundle["signature"], validate=True)
    key = public.read_text(encoding="ascii").split()
    if len(key) < 2 or key[0] != "ssh-ed25519":
        raise ValueError("Clé publique de la régie invalide.")
    # Keep the allowed-signers file closed and fully specified before verification.
    with TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "allowed"
        allowed.write_text(f"regie {key[0]} {key[1]}\n", encoding="ascii")
        sigfile = Path(tmp) / "signature"
        sigfile.write_bytes(signature)
        try:
            ssh_keygen("-Y", "verify", "-f", allowed, "-I", "regie", "-n", NAMESPACE,
                       "-s", sigfile, data=manifest(device, job, nonce, version))
        except RuntimeError as error:
            raise ValueError("Signature de la mise à jour non reconnue.") from error
    return files


class ClientUpdate:
    def __init__(self, app, current):
        self.app = app
        self.current = current
        self.versions = app / ".versions"
        self.public = app / PUBLIC_KEY
        self.file = self.versions / "state.json"

    def read(self):
        if self.file.is_file():
            return json.loads(self.file.read_text(encoding="utf-8"))
        return {}

    def write(self, data):
        self.versions.mkdir(mode=0o700, exist_ok=True)
        temp = self.file.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.file)

    def report(self):
        return self.read().get("result")

    def failed(self, job, error):
        data = self.read()
        data["result"] = dict(id=job, state="failed", message=str(error)[:240])
        self.write(data)

    def stage(self, bundle, device, job, nonce):
        files = verify(self.public, bundle, device, job, nonce)
        version = bundle["version"]
        data = self.read()
        if version == digest(contents(self.current)):
            data["result"] = dict(id=job, state="updated", message="Déjà à jour.")
            self.write(data)
            return False
        self.versions.mkdir(mode=0o700, exist_ok=True)
        with TemporaryDirectory(dir=self.versions) as tmp:
            staged = Path(tmp) / version
            staged.mkdir()
            for name, content in files.items():
                compile(content, name, "exec")
                (staged / name).write_bytes(content)
            result = subprocess.run([sys.executable, "-B", "-c", "import ecran"],
                                    cwd=staged, capture_output=True, timeout=10)
            if result.returncode:
                raise ValueError("Le nouveau client ne peut pas démarrer.")
            destination = self.versions / version
            if destination.exists():
                if digest(contents(destination)) != version:
                    raise ValueError("La copie locale de cette version est altérée.")
            else:
                staged.rename(destination)
        data.update(candidate=version, trial_job=job, trial="ready",
                    result=dict(id=job, state="installing"))
        self.write(data)
        return True

    def select(self):
        data = self.read()
        if data.get("trial") == "starting":
            self.rollback()
        elif data.get("trial") == "ready":
            candidate = data.get("candidate")
            if isinstance(candidate, str) and SAFE_ID.fullmatch(candidate):
                entry = self.versions / candidate / "ecran.py"
                data["trial"] = "starting"
                self.write(data)
                if entry.is_file():
                    return entry
                self.rollback()
                data = self.read()
            else:
                raise ValueError("Version candidate invalide.")
        active = data.get("active")
        if isinstance(active, str) and SAFE_ID.fullmatch(active):
            entry = self.versions / active / "ecran.py"
            if entry.is_file():
                return entry
        return self.app / "ecran.py"

    def ready(self):
        data = self.read()
        if data.get("trial") == "starting" and self.current.name == data.get("candidate"):
            data["active"] = data.pop("candidate")
            data.pop("trial")
            data["result"] = dict(id=data.pop("trial_job"), state="updated", message="Client relancé.")
            self.write(data)

    def rollback(self):
        data = self.read()
        if data.get("trial") != "starting":
            return False
        data["result"] = dict(id=data.pop("trial_job"), state="failed",
                              message="Le nouveau client n’a pas démarré. Version précédente restaurée.")
        data.pop("trial")
        data.pop("candidate")
        self.write(data)
        return True
