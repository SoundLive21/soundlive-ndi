# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Récupération et installation des publications GitHub signées de la régie."""
import ast
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile

from commun import APP_NAME, release_number
from mise_a_jour import ssh_keygen

NAMESPACE = "soundlive-release"
ARCHIVE = f"{APP_NAME}.zip"
LEGACY_ARCHIVE = "SoundLive-Ecrans.zip"
ARCHIVES = (ARCHIVE, LEGACY_ARCHIVE)
LEGACY_LAUNCHER_NAMES = {"SoundLive-NDI.c": "SoundLive-Regie.c",
                         "SoundLive-NDI.exe": "SoundLive-Regie.exe"}
MAX_ARCHIVE = 64 * 1024 * 1024
FILES = frozenset((
    "tableau.py", "tableau.html", "acces_regie.py", "acces-refuse.html", "commun.py", "qrencoder.py", "regie-windows.py",
    "demarrer-windows.bat", "demarrer-regie-ubuntu.sh", "SoundLive-NDI.c", "SoundLive-NDI.exe",
    "ecran.py", "diagnostic.py", "mise_a_jour.py", "lancer-client.py",
    "installer-client.sh", "installer-client.cmd", "installer-client-windows.py",
    "preparer-lecteur-linux.sh", "preparer-lecteur-windows.ps1",
    "regie-internet.py", "regie-internet-windows.ps1",
    "soundlive-ndi-network-static.sh", "soundlive-ndi-network-windows.ps1",
    "soundlive-ndi-network.service", "soundlive-ndi-network.timer",
    "installer-pi.sh", "installer-ubuntu.sh", "lecteur-ndi-ubuntu.c", "compiler-lecteur-windows.ps1",
    "lecteur-ndi-ubuntu-amd64", "lecteur-ndi-ubuntu-arm64", "lecteur-ndi-windows.exe",
    "LISEZ-MOI.txt", "versions_regie.py", "publier-version.py", "README.md",
    ".gitignore", "LICENSE", "LICENSE.txt", "COPYING",
))
REQUIRED = {"commun.py", "ecran.py", "diagnostic.py", "mise_a_jour.py", "tableau.py",
            "tableau.html", "acces-refuse.html", "regie-windows.py", "versions_regie.py"}


def repository(value):
    value = value.strip()
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/")
    value = value.strip("/").removesuffix(".git")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,38}/[a-zA-Z0-9_.-]{1,100}", value):
        raise ValueError("Indique un dépôt GitHub sous la forme propriétaire/dépôt.")
    if value.split("/")[1] in (".", ".."):
        raise ValueError("Nom de dépôt invalide.")
    return value


def download(url, limit):
    req = Request(url, headers={"User-Agent": APP_NAME, "Accept": "application/vnd.github+json"})
    with urlopen(req, timeout=12) as response:
        address = urlsplit(response.geturl())
        if address.scheme != "https" or address.hostname not in {
                "api.github.com", "github.com", "release-assets.githubusercontent.com",
                "objects.githubusercontent.com"}:
            raise ValueError("Le téléchargement a quitté les domaines de publication GitHub.")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Publication trop volumineuse.")
    return data


def latest(repo, installed):
    repo = repository(repo)
    meta = json.loads(download(f"https://api.github.com/repos/{repo}/releases/latest", 2 * 1024 * 1024))
    tag = meta["tag_name"]
    version = tag.removeprefix("v")
    if not release_number(version) or meta.get("draft") or meta.get("prerelease"):
        raise ValueError("La publication doit avoir un numéro stable de type v1.4.0.")
    if release_number(version) <= release_number(installed):
        return None
    prefix = f"https://github.com/{repo}/releases/download/{tag}/"
    assets = {item["name"]: item["browser_download_url"] for item in meta["assets"]}
    for archive_name in ARCHIVES:
        if all(assets.get(name, "").lower() == (prefix + name).lower()
               for name in (archive_name, archive_name + ".sig")):
            return version, assets[archive_name]
    raise ValueError(f"Archive et signature absentes des fichiers de la publication {tag}.")


class PublicationStatus:
    def __init__(self, config, installed):
        self.config = config
        self.installed = installed
        self.lock = Lock()
        self.repo = None
        self.result = {"state": "unconfigured"}
        self.next_check = 0
        self.generation = 0

    def read(self):
        try:
            repo = repository(json.loads(self.config.read_text(encoding="utf-8"))["repository"])
        except FileNotFoundError:
            repo = None
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {"state": "unavailable"}
        with self.lock:
            if not repo:
                self.repo = None
                self.result = {"state": "unconfigured"}
                return self.result
            if repo != self.repo:
                self.repo = repo
                self.result = {"state": "checking"}
                self.next_check = 0
            if monotonic() >= self.next_check:
                generation = self.begin_check(repo)
                Thread(target=self.check, args=(repo, generation), daemon=True).start()
            return self.result

    def begin_check(self, repo):
        self.repo = repo
        self.generation += 1
        self.next_check = monotonic() + 300
        return self.generation

    def check(self, repo, generation, *, manual=False):
        try:
            release = latest(repo, self.installed)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            with self.lock:
                if self.repo == repo and self.generation == generation:
                    self.result = {"state": "unavailable"}
            if manual:
                raise
            return None
        with self.lock:
            if self.repo == repo and self.generation == generation:
                self.result = {"state": "available", "version": release[0]} if release else {"state": "current"}
        return release

    def verify(self, repo):
        with self.lock:
            generation = self.begin_check(repo)
            self.result = {"state": "checking"}
        return self.check(repo, generation, manual=True)


def verify_release(public, archive, signature, archive_name=ARCHIVE):
    key = public.read_text(encoding="ascii").split()
    if len(key) < 2 or key[0] != "ssh-ed25519":
        raise ValueError("Clé publique de publication Ed25519 requise.")
    with TemporaryDirectory() as folder:
        signers = Path(folder) / "signers"
        signers.write_text(f"publication {key[0]} {key[1]}\n", encoding="ascii")
        sig = Path(folder) / "release.sig"
        sig.write_bytes(signature.replace(b"\r\n", b"\n"))
        try:
            ssh_keygen("-Y", "verify", "-f", signers, "-I", "publication",
                       "-n", NAMESPACE, "-s", sig, data=release_manifest(archive, archive_name))
        except RuntimeError as error:
            raise ValueError(f"Signature de publication refusée. {error}") from error


def release_manifest(archive, archive_name=ARCHIVE):
    return f"{archive_name} {sha256(archive).hexdigest()}".encode("ascii")


def archive_files(archive, version, archive_name=ARCHIVE):
    folder = archive_name.removesuffix(".zip")
    allowed = FILES
    if archive_name == LEGACY_ARCHIVE:
        allowed = FILES.difference(LEGACY_LAUNCHER_NAMES).union(LEGACY_LAUNCHER_NAMES.values())
    files = {}
    with ZipFile(BytesIO(archive)) as source:
        if len(source.infolist()) > 100 or sum(item.file_size for item in source.infolist()) > MAX_ARCHIVE:
            raise ValueError("Archive de publication trop volumineuse.")
        for item in source.infolist():
            prefix, _, name = item.filename.partition("/")
            if prefix != folder or name not in allowed or name in files:
                raise ValueError(f"Entrée de publication inattendue : {item.filename}.")
            files[name] = source.read(item)
    if not REQUIRED <= files.keys():
        raise ValueError("La publication ne contient pas les fichiers nécessaires à la régie.")
    for filename, value in files.items():
        if filename.endswith(".py"):
            compile(value, filename, "exec")
    declared = None
    for filename in ("tableau.py", "commun.py"):
        for item in ast.parse(files[filename]).body:
            if (isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant)
                    and any(isinstance(t, ast.Name) and t.id == "SOFTWARE_VERSION" for t in item.targets)):
                declared = item.value.value
        if declared is not None:
            break
    if declared != version:
        raise ValueError("La version du ZIP diffère de celle de la publication GitHub.")
    return files


def install(root, release, public):
    version, url = release
    archive_name = urlsplit(url).path.rsplit("/", 1)[-1]
    if archive_name not in ARCHIVES:
        raise ValueError("Nom d’archive de publication invalide.")
    archive = download(url, MAX_ARCHIVE)
    signature = download(url + ".sig", 16 * 1024)
    verify_release(public, archive, signature, archive_name)
    files = archive_files(archive, version, archive_name)
    with TemporaryDirectory(dir=root, prefix=".soundlive-publication-") as folder:
        stage = Path(folder)
        backup = stage / "backup"
        backup.mkdir()
        for filename, content in files.items():
            target = root / filename
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise ValueError(f"Chemin de mise à jour invalide : {filename}.")
            (stage / filename).write_bytes(content)
            if target.exists():
                (backup / filename).write_bytes(target.read_bytes())
                (stage / filename).chmod(target.stat().st_mode & 0o777)
            elif filename.endswith(".sh"):
                (stage / filename).chmod(0o755)
        applied = []
        try:
            for filename in files:
                os.replace(stage / filename, root / filename)
                applied.append(filename)
        except OSError:
            for filename in reversed(applied):
                previous = backup / filename
                if previous.exists():
                    previous.chmod((root / filename).stat().st_mode & 0o777)
                    os.replace(previous, root / filename)
                else:
                    (root / filename).unlink()
            raise
