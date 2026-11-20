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
import platform
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

FILES = ("ecran.py", "commun.py", "diagnostic.py", "mise_a_jour.py")
LIMIT = 512 * 1024
FULL_LIMIT = 16 * 1024 * 1024
FULL_FILES = (*FILES, "lancer-client.py")
ENGINE_ASSETS = {
    "linux-amd64": ("lecteur-ndi-ubuntu-amd64", "lecteur-ndi-ubuntu"),
    "linux-arm64": ("lecteur-ndi-ubuntu-arm64", "lecteur-ndi-ubuntu"),
    "windows-amd64": ("lecteur-ndi-windows.exe", "lecteur-ndi-windows.exe"),
    "pi-ffplay": None,
}
NAMESPACE = "soundlive-update"
SAFE_ID = re.compile(r"[a-f0-9]{64}")
PUBLIC_KEY = "regie-update.pub"
RESTART = 75
CLIENT_UPDATE_ISSUES = ("launcher_required", "client_key_required", "client_openssh_required")
IDENTIFICATION_RUNTIME = {}
IDENTIFICATION_RENDER_SOURCE = b""


class OpenSSHError(RuntimeError):
    def __init__(self, message, reason="failed"):
        super().__init__(message)
        self.reason = reason


def contents(folder):
    files = {name: (folder / name).read_bytes() for name in FILES}
    if sum(map(len, files.values())) > LIMIT:
        raise ValueError("Le paquet du client est trop volumineux.")
    return files


def digest(files):
    checksums = {name: sha256(value).hexdigest() for name, value in files.items()}
    return sha256(json.dumps(checksums, sort_keys=True).encode("ascii")).hexdigest()


def update_target():
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "windows-amd64" if machine in ("amd64", "x86_64") else None
    if os.environ.get("SOUNDLIVE_PLAYER") != "ubuntu":
        return "pi-ffplay"
    if machine in ("amd64", "x86_64"):
        return "linux-amd64"
    if machine in ("arm64", "aarch64"):
        return "linux-arm64"
    return None


def engine_name(target):
    engine = ENGINE_ASSETS.get(target)
    return engine[1] if engine else None


def check_engine(payload, target):
    if target == "windows-amd64":
        header = int.from_bytes(payload[60:64], "little")
        valid = (payload.startswith(b"MZ") and header >= 64 and
                 payload[header:header + 6] == b"PE\0\0\x64\x86")
    else:
        machine = b"\xb7\x00" if target == "linux-arm64" else b"\x3e\x00"
        valid = payload.startswith(b"\x7fELF\x02\x01") and payload[18:20] == machine
    if not valid:
        raise ValueError("Le moteur fourni ne correspond pas à la plateforme du lecteur.")


def full_contents(folder, target, *, release=False, include_engine=True):
    files = contents(folder)
    launcher = folder / "lancer-client.py"
    if release or launcher.is_file():
        files[launcher.name] = launcher.read_bytes()
    engine = ENGINE_ASSETS.get(target) if include_engine else None
    if engine:
        asset, name = engine
        path = folder / (asset if release else name)
        if path.is_file() and not path.is_symlink():
            if path.stat().st_size > FULL_LIMIT:
                raise ValueError(f"Moteur trop volumineux : {path.name}.")
            files[name] = path.read_bytes()
            check_engine(files[name], target)
    if release and target in ("pi-ffplay", "linux-arm64", "linux-amd64"):
        resource = folder / "identification-linux.txt"
        if resource.stat().st_size > 2 * LIMIT:
            raise ValueError("Surcouche d’identification trop volumineuse.")
        bundle = json.loads(resource.read_text(encoding="utf-8"))
        architectures = ("arm64", "armhf") if target == "pi-ffplay" else (target.removeprefix("linux-"),)
        runtime = {"copyright": bundle["copyright"], "files": {
            name: {arch: system["architectures"][arch]["files"] for arch in architectures}
            for name, system in bundle["distributions"].items()}}
        # Existing readers accept these same signed Python files. Carry the resources as
        # data in the updater module to avoid a separate install or a second push.
        files["mise_a_jour.py"] += f"\n\nIDENTIFICATION_RUNTIME = {runtime!r}\n".encode("utf-8")
        if engine:
            source = (folder / "lecteur-ndi-ubuntu.c").read_bytes()
            files["mise_a_jour.py"] += f"IDENTIFICATION_RENDER_SOURCE = {source!r}\n".encode("ascii")
        if sum(len(files[name]) for name in FILES) > LIMIT:
            raise ValueError("Le client et sa surcouche dépassent la limite de mise à jour.")
    if sum(map(len, files.values())) > FULL_LIMIT:
        raise ValueError("Le dossier applicatif est trop volumineux.")
    return files


def manifest(device, job, nonce, version):
    return json.dumps([NAMESPACE, device, job, nonce, version], separators=(",", ":")).encode("ascii")


def ssh_keygen_path():
    if command := shutil.which("ssh-keygen"):
        return command
    if sys.platform == "win32":
        folder = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        for system in ("Sysnative", "System32"):
            command = folder / system / "OpenSSH" / "ssh-keygen.exe"
            if command.is_file():
                return str(command)
    return None


def ssh_keygen(*args, data=None):
    try:
        result = subprocess.run([ssh_keygen_path() or "ssh-keygen", *map(str, args)], input=data,
                                capture_output=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except FileNotFoundError as error:
        raise OpenSSHError("Client OpenSSH requis : ssh-keygen est introuvable.", "missing") from error
    except subprocess.TimeoutExpired as error:
        raise OpenSSHError("ssh-keygen n’a pas répondu.", "timeout") from error
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        normalized = message.lower()
        if "bad permissions" in normalized or "unprotected private key file" in normalized:
            raise OpenSSHError(message, "permissions")
        raise OpenSSHError(message or "Échec de la vérification OpenSSH.")
    return result.stdout


def protect_windows_signing_key(private):
    script = r"""
$ErrorActionPreference = 'Stop'
$path = $env:SOUNDLIVE_UPDATE_KEY_PATH
$file = Get-Item -LiteralPath $path -Force
if ($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Invalid signing key path.'
}
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = Get-Acl -LiteralPath $path
if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) {
    $acl.SetOwner($sid)
}
$acl.SetSecurityDescriptorSddlForm(
    "D:P(A;;FA;;;$($sid.Value))",
    [Security.AccessControl.AccessControlSections]::Access
)
Set-Acl -LiteralPath $path -AclObject $acl
"""
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                                env={**os.environ, "SOUNDLIVE_UPDATE_KEY_PATH": str(private.absolute())},
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OpenSSHError(f"Impossible de protéger la clé de mise à jour Windows. {error}",
                           "permissions") from error
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise OpenSSHError(f"Impossible de protéger la clé de mise à jour Windows. {detail}", "permissions")


def use_signing_key(private, *args):
    try:
        return ssh_keygen(*args)
    except OpenSSHError as error:
        if sys.platform != "win32" or error.reason != "permissions":
            raise
    # Repair only the local signing key, and only when OpenSSH has rejected its permissions.
    protect_windows_signing_key(private)
    return ssh_keygen(*args)


def prepare_key(folder):
    private = folder / ".soundlive-update" / "regie"
    private.parent.mkdir(mode=0o700, exist_ok=True)
    if not private.is_file():
        ssh_keygen("-t", "ed25519", "-N", "", "-C", NAMESPACE, "-f", private)
    public = folder / PUBLIC_KEY
    public.write_bytes(use_signing_key(private, "-y", "-f", private))
    return public


def package(private, files, device, job, nonce, version):
    with TemporaryDirectory() as tmp:
        message = Path(tmp) / "manifest"
        message.write_bytes(manifest(device, job, nonce, version))
        use_signing_key(private, "-Y", "sign", "-f", private, "-n", NAMESPACE, message)
        signature = message.with_suffix(".sig").read_bytes().replace(b"\r\n", b"\n")
    # OpenSSH 9.2 exige un LF à la fin de l'en-tête de la signature.
    if not (signature.startswith(b"-----BEGIN SSH SIGNATURE-----\n")
            and signature.endswith(b"\n-----END SSH SIGNATURE-----\n")):
        raise RuntimeError("ssh-keygen a produit un bloc de signature invalide.")
    return dict(version=version, signature=b64encode(signature).decode("ascii"),
                files={name: b64encode(value).decode("ascii") for name, value in files.items()})


def verify(public, bundle, device, job, nonce, target=None):
    version = bundle["version"]
    if not isinstance(version, str) or not SAFE_ID.fullmatch(version):
        raise ValueError("Empreinte de mise à jour invalide.")
    encoded = bundle["files"]
    if not isinstance(encoded, dict):
        raise ValueError("Contenu de mise à jour inattendu.")
    names = set(encoded)
    full = set(FULL_FILES)
    engine = engine_name(target)
    if names == set(FILES):
        limit = LIMIT
    elif target in ENGINE_ASSETS and (names == full or engine and names == full | {engine}):
        limit = FULL_LIMIT
    else:
        raise ValueError("Contenu de mise à jour inattendu.")
    files = {name: b64decode(value, validate=True) for name, value in encoded.items()}
    if sum(map(len, files.values())) > limit or digest(files) != version:
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
    if engine in files:
        check_engine(files[engine], target)
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

    def installed_files(self, target):
        # Le moteur de l’installation de base reste local jusqu’à son déploiement par la régie.
        return full_contents(self.current, target, include_engine=self.current != self.app)

    def stage(self, bundle, device, job, nonce):
        target = update_target()
        files = verify(self.public, bundle, device, job, nonce, target)
        version = bundle["version"]
        data = self.read()
        if version == digest(self.installed_files(target)):
            data["result"] = dict(id=job, state="updated", message="Déjà à jour.")
            self.write(data)
            return False
        self.versions.mkdir(mode=0o700, exist_ok=True)
        with TemporaryDirectory(dir=self.versions) as tmp:
            staged = Path(tmp) / version
            staged.mkdir()
            for name, content in files.items():
                path = staged / name
                if name.endswith(".py"):
                    compile(content, name, "exec")
                path.write_bytes(content)
                if name == engine_name(target) and sys.platform != "win32":
                    path.chmod(0o755)
            env = {**os.environ, "SOUNDLIVE_APP": str(self.app)}
            result = subprocess.run([sys.executable, "-B", "-c", "import ecran"],
                                    cwd=staged, env=env, capture_output=True, timeout=10)
            if result.returncode:
                raise ValueError("Le nouveau client ne peut pas démarrer.")
            engine = engine_name(target)
            if engine in files:
                env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (str(self.app / "lib"), env.get("LD_LIBRARY_PATH"))))
                if sys.platform == "win32":
                    for dll in ("Processing.NDI.Lib.x64.dll", "SDL2.dll"):
                        runtime = self.app / dll
                        if not runtime.is_file():
                            raise ValueError(f"Dépendance locale manquante : {dll}.")
                        (staged / dll).write_bytes(runtime.read_bytes())
                check = subprocess.run(
                    [str(staged / engine), "--sources"], env=env, cwd=staged,
                    capture_output=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                if check.returncode:
                    detail = check.stderr.decode(errors="replace").strip()[:150]
                    raise ValueError("Le nouveau moteur ne démarre pas avec les dépendances du lecteur."
                                     + (" " + detail if detail else ""))
            destination = self.versions / version
            if destination.exists():
                if digest(full_contents(destination, target)) != version:
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
            launcher = self.current / "lancer-client.py"
            if launcher.is_file():
                boot = self.app / "lancer-client.py"
                with TemporaryDirectory(dir=self.app) as folder:
                    temporary = Path(folder) / boot.name
                    temporary.write_bytes(launcher.read_bytes())
                    if boot.is_file():
                        temporary.chmod(boot.stat().st_mode & 0o777)
                    temporary.replace(boot)
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
