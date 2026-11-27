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
import ssl
import stat
import sys
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile

from commun import APP_NAME, release_number
from mise_a_jour import ssh_keygen

NAMESPACE = "soundlive-release"
DEFAULT_REPOSITORY = "SoundLive21/soundlive-ndi"
DEFAULT_PUBLICATION_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBG1cUgElZaDG/D0sEuXIhl1EaONYhPrl4N4sKHN5CZg"
)
ARCHIVE = f"{APP_NAME}.zip"
LEGACY_ARCHIVE = "SoundLive-Ecrans.zip"
ARCHIVES = (ARCHIVE, LEGACY_ARCHIVE)
LEGACY_LAUNCHER_NAMES = {"SoundLive-NDI.c": "SoundLive-Regie.c",
                         "SoundLive-NDI.exe": "SoundLive-Regie.exe"}
MAX_ARCHIVE = 64 * 1024 * 1024
FILES = frozenset((
    "desinstaller_soundlive.py", "logo-soundlive.txt", "logo_soundlive.py", "sauvegarde_regie.py", "tableau.py", "tableau.html", "acces_regie.py", "certificats_acme.py", "acme_powerdns.py", "gestion_acme_powerdns.py", "acces-refuse.html", "commun.py", "qrencoder.py", "regie-windows.py",
    "demarrer-windows.bat", "demarrer-regie-ubuntu.sh", "SoundLive-NDI.c", "SoundLive-NDI.exe",
    "ecran.py", "diagnostic.py", "mise_a_jour.py", "lancer-client.py",
    "installer-client.sh", "installer-client.cmd", "installer-client-windows.py",
    "addsshkey.html", "page_ssh.py", "deploiement_lecteur.py",
    "setup.html", "setup-linux.sh", "setup-windows.cmd", "raccorder-lecteur.py", "setup-reseau-windows.ps1",
    "configurer-session-lecteur-linux.sh", "configurer-session-lecteur-windows.ps1",
    "preparer-lecteur-linux.sh", "preparer-lecteur-windows.ps1", "installation_lecteur.py",
    "installer-ssh-linux.sh",
    "regie-internet.py", "regie-internet-windows.ps1", "regie_dhcp.py", "regie_reseau.py",
    "terminal.html", "terminal_ssh.py", "installer-bureau-distant-linux.sh", "installer-bureau-distant-windows.ps1",
    "identification-linux.txt", "maintenance-os.sh", "soundlive-ndi-maintenance.service",
    "installer-ssh-windows.ps1", "installation-lecteur-linux.sh", "installer-regie-linux.sh",
    "installer-regie-windows.cmd", "installer-regie-windows.ps1",
    "soundlive-ndi-network-static.sh", "soundlive-ndi-network-windows.ps1",
    "soundlive-ndi-network.service", "soundlive-ndi-network.timer",
    "installer-pi.sh", "installer-ubuntu.sh", "lecteur-ndi-ubuntu.c", "compiler-lecteur-windows.ps1",
    "lecteur-ndi-ubuntu-amd64", "lecteur-ndi-ubuntu-arm64", "lecteur-ndi-windows.exe",
    "LISEZ-MOI.txt", "versions_regie.py", "publier-version.py", "README.md",
    ".gitignore", "LICENSE", "LICENSE.txt", "COPYING",
))
REQUIRED = {"commun.py", "ecran.py", "diagnostic.py", "mise_a_jour.py", "tableau.py",
            "tableau.html", "acces-refuse.html", "regie-windows.py", "versions_regie.py"}
ADDITIONAL_FILE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}\.(?:py|html|css|js|c|h|sh|ps1|cmd|bat|exe|service|timer|txt|md)")
WINDOWS_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[0-9]|lpt[0-9])", re.IGNORECASE)


def ensure_publication_defaults(config):
    """Configure le dépôt officiel et sa clé publique sans modifier les choix existants."""
    config = Path(config)
    for path, content in (
        (config, json.dumps({"repository": DEFAULT_REPOSITORY}) + "\n"),
        (config.with_name("publication.pub"), DEFAULT_PUBLICATION_KEY + "\n"),
    ):
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(content)
        except FileExistsError:
            # Conserver le dépôt et la clé personnalisés, y compris sur une mise à jour.
            pass
        except OSError:
            # Un dossier en lecture seule ne doit pas empêcher le lancement de la régie.
            pass


def required_files(version):
    version = release_number(version)
    files = REQUIRED.copy()
    if version >= (1, 4, 50):
        files.update(("terminal.html", "terminal_ssh.py"))
    if version >= (1, 4, 58):
        files.update(("installation_lecteur.py", "installer-ssh-linux.sh"))
    if version >= (1, 4, 60):
        files.add("regie_dhcp.py")
    if version >= (1, 4, 66):
        files.update(("regie_reseau.py", "regie-internet.py", "regie-internet-windows.ps1"))
    if version >= (1, 4, 67):
        files.update(("installation-lecteur-linux.sh", "installer-regie-linux.sh", "installer-regie-windows.cmd",
                      "installer-regie-windows.ps1", "installer-bureau-distant-linux.sh",
                      "installer-bureau-distant-windows.ps1", "installer-ssh-windows.ps1"))
    if version >= (1, 4, 70):
        files.update(("identification-linux.txt", "lecteur-ndi-ubuntu.c"))
    if version >= (1, 4, 73):
        files.update(("maintenance-os.sh", "soundlive-ndi-maintenance.service"))
    if version >= (1, 4, 92):
        files.update(("configurer-session-lecteur-linux.sh", "configurer-session-lecteur-windows.ps1"))
    if version >= (1, 4, 93):
        files.update(("addsshkey.html", "page_ssh.py", "deploiement_lecteur.py"))
    if version >= (1, 4, 97):
        files.add("certificats_acme.py")
    if version >= (1, 4, 98):
        files.update(("acme_powerdns.py", "gestion_acme_powerdns.py"))
    if version >= (1, 4, 106):
        files.add("sauvegarde_regie.py")
    if version >= (1, 4, 107):
        files.update(("logo-soundlive.txt", "logo_soundlive.py"))
    if version >= (1, 4, 108):
        files.add("desinstaller_soundlive.py")
    if version >= (1, 4, 130):
        files.update(("setup.html", "setup-linux.sh", "setup-windows.cmd", "raccorder-lecteur.py"))
    if version >= (1, 4, 131):
        files.add("setup-reseau-windows.ps1")
    return files


def software_version(source, filename):
    for entry in ast.parse(source, filename=filename).body:
        if (isinstance(entry, ast.Assign) and isinstance(entry.value, ast.Constant)
                and any(isinstance(target, ast.Name) and target.id == "SOFTWARE_VERSION"
                        for target in entry.targets)):
            return entry.value.value if release_number(entry.value.value) else None
    return None


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
    # Sous Windows, le magasin système gère la chaîne de certificats
    # et peut retrouver les certificats intermédiaires manquants.
    # La validation TLS reste obligatoire.
    if sys.platform == "win32":
        try:
            import truststore
        except ImportError:
            context = ssl.create_default_context()
        else:
            context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        context = ssl.create_default_context()
    with urlopen(req, timeout=12, context=context) as response:
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
        self.config = Path(config)
        ensure_publication_defaults(self.config)
        self.installed = installed
        self.lock = Lock()
        self.repo = None
        self.result = {"state": "unconfigured"}
        self.next_check = 0
        self.generation = 0

    def read(self):
        # La régie officielle suit toujours son dépôt intégré. Les anciens
        # fichiers source-publications.json sont conservés mais non utilisés.
        repo = DEFAULT_REPOSITORY
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
    # Une clé intégrée permet de vérifier les releases officielles même si
    # l'installation n'a pas pu créer publication.pub sur le disque.
    key = (public if isinstance(public, str) and public.startswith("ssh-ed25519 ")
           else Path(public).read_text(encoding="ascii")).split()
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


def declared_publication_files(source):
    for entry in ast.parse(source, filename="versions_regie.py").body:
        if not isinstance(entry, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "FILES" for target in entry.targets):
            continue
        value = entry.value
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == "frozenset" and len(value.args) == 1 and not value.keywords):
            value = value.args[0]
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)) and all(
                isinstance(item, ast.Constant) and isinstance(item.value, str) for item in value.elts):
            return {item.value for item in value.elts}
        break
    raise ValueError("La publication ne déclare pas sa liste de fichiers au format attendu.")


def archive_files(archive, version, archive_name=ARCHIVE, *, signature_verified=False):
    folder = archive_name.removesuffix(".zip")
    allowed = FILES
    if archive_name == LEGACY_ARCHIVE:
        allowed = FILES.difference(LEGACY_LAUNCHER_NAMES).union(LEGACY_LAUNCHER_NAMES.values())
    known_folded = {name.casefold() for name in allowed}
    files = {}
    names_folded = set()
    additions = set()
    with ZipFile(BytesIO(archive)) as source:
        if len(source.infolist()) > 100 or sum(item.file_size for item in source.infolist()) > MAX_ARCHIVE:
            raise ValueError("Archive de publication trop volumineuse.")
        for item in source.infolist():
            prefix, _, name = item.filename.partition("/")
            folded = name.casefold()
            mode = stat.S_IFMT(item.external_attr >> 16)
            if (prefix != folder or not name or folded in names_folded or
                    mode not in (0, stat.S_IFREG) or item.is_dir()):
                raise ValueError(f"Entrée de publication inattendue : {item.filename}.")
            if name not in allowed:
                if (not signature_verified or folded in known_folded or not ADDITIONAL_FILE.fullmatch(name)
                        or WINDOWS_DEVICE.fullmatch(name.partition(".")[0])):
                    raise ValueError(f"Entrée de publication inattendue : {item.filename}.")
                additions.add(name)
            names_folded.add(folded)
            files[name] = source.read(item)
    missing = required_files(version) - files.keys()
    if missing:
        raise ValueError("Fichiers nécessaires absents de la publication : " + ", ".join(sorted(missing)))
    # The signed snapshot can explicitly introduce more source files without executing its manifest.
    if additions:
        undeclared = additions - declared_publication_files(files["versions_regie.py"])
        if undeclared:
            raise ValueError("Entrées absentes de la liste de publication : " + ", ".join(sorted(undeclared)))
    for filename, value in files.items():
        if filename.endswith(".py"):
            compile(value, filename, "exec")
    declared = software_version(files["tableau.py"], "tableau.py") or software_version(files["commun.py"], "commun.py")
    if declared != version:
        raise ValueError("La version du ZIP diffère de celle de la publication GitHub.")
    return files


def install(root, release, public=None):
    version, url = release
    parsed = urlsplit(url)
    prefix = f"/{DEFAULT_REPOSITORY}/releases/download/"
    # La signature est vérifiée exclusivement avec la clé officielle intégrée.
    # Ne jamais accepter un téléchargement provenant d'un autre dépôt.
    if (parsed.scheme != "https" or parsed.hostname != "github.com"
            or not parsed.path.lower().startswith(prefix.lower())):
        raise ValueError("La mise à jour doit provenir du dépôt officiel SoundLive-NDI.")
    archive_name = parsed.path.rsplit("/", 1)[-1]
    if archive_name not in ARCHIVES:
        raise ValueError("Nom d’archive de publication invalide.")
    archive = download(url, MAX_ARCHIVE)
    signature = download(url + ".sig", 16 * 1024)
    verify_release(DEFAULT_PUBLICATION_KEY, archive, signature, archive_name)
    files = archive_files(archive, version, archive_name, signature_verified=True)
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
