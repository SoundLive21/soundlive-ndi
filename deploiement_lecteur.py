"""Déploiement initial d'un écran via SSH (régie -> nouveau lecteur).
Les clés d'hôte doivent être vérifiées explicitement. Aucune exécution
arbitraire n'est acceptée : dépôt public et installateur fixés.
"""
from concurrent.futures import ThreadPoolExecutor
from ipaddress import IPv4Address
import base64
import json
import shlex
import threading
import time

from installation_lecteur import ssh_identity
from terminal_ssh import HostKeyCheck, USER, connect_client
from commun import SSH_FINGERPRINT

REPOSITORY = "https://github.com/SoundLive21/soundlive-ndi.git"
MAX_JOBS = 2
MAX_OUTPUT = 24000
INSTALL_TIMEOUT = 35 * 60


def validate_ipv4(text):
    if not isinstance(text, str):
        raise ValueError("Adresse IPv4 invalide.")
    try:
        address = IPv4Address(text)
    except ValueError:
        raise ValueError("Adresse IPv4 invalide.") from None
    if not (address.is_private and not address.is_loopback and
            not address.is_link_local and not address.is_multicast and
            not address.is_unspecified):
        raise ValueError("Utilise une adresse IPv4 privée du réseau des écrans.")
    return str(address)


def command_linux(kind, regie, port, code, password=False):
    if kind not in ("ubuntu", "pi"):
        raise ValueError("Type de lecteur Linux invalide.")
    server = f"http://{regie}:{port}"
    # Le mot de passe sudo éventuel est transmis par le canal SSH après
    # désactivation de l'écho terminal, jamais dans les arguments de commande.
    auth = (
        "stty -echo; printf '__SOUNDLIVE_SUDO_READY__\\n'; "
        "IFS= read -r soundlive_sudo_password; stty echo; "
        "printf '%s\\n' \"$soundlive_sudo_password\" | sudo -S -p '' -v >/dev/null; "
        "unset soundlive_sudo_password; "
        "(while sleep 45; do sudo -n -v >/dev/null 2>&1 || break; done) >/dev/null 2>&1 & "
        "SOUNDLIVE_SUDO_REFRESH=$!; "
    ) if password else ""
    # Chaque déploiement repart d'un dossier temporaire distinct.
    return ("set -eu; " + auth
            "command -v git >/dev/null || { echo 'Installe git sur ce lecteur.' >&2; exit 20; }; "
            "sudo -n true >/dev/null 2>&1 || { "
            "echo 'Installation automatique : sudo sans demande de mot de passe nécessaire. "
            "Prépare les droits localement sur ce lecteur dédié.' >&2; exit 21; }; "
            "folder=$(mktemp -d); trap 'rm -rf -- \"$folder\"; "
            "[ -z \"${SOUNDLIVE_SUDO_REFRESH:-}\" ] || "
            "kill \"$SOUNDLIVE_SUDO_REFRESH\" 2>/dev/null || true' EXIT; "
            "GIT_TERMINAL_PROMPT=0 git clone --depth 1 --single-branch "
            + shlex.quote(REPOSITORY) + ' "$folder/kit"; '
            'cd "$folder/kit"; '
            'SOUNDLIVE_DEPLOY_REMOTE=1 bash installer-client.sh --plateforme '
            + shlex.quote(kind) + ' ' + shlex.quote(server) + ' ' + shlex.quote(code))


def command_windows(regie, port, code):
    server = f"http://{regie}:{port}"
    # Dans Windows SSH, on utilise PowerShell sans paramètres interpolés
    # depuis l'hôte utilisateur. Les valeurs ont été validées au serveur.
    def ps(value):
        return "'" + value.replace("'", "''") + "'"
    script = """
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Compte SSH administrateur nécessaire pour installer le lecteur Windows.'
}
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    throw 'Git for Windows requis : installe Git avant de déployer.'
}
$temporary = Join-Path $env:TEMP ('soundlive-ndi-deploy-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary -Force | Out-Null
try {
    $env:SOUNDLIVE_DEPLOY_REMOTE = '1'
    & git.exe clone --depth 1 --single-branch """+ps(REPOSITORY)+""" (Join-Path $temporary 'kit')
    if ($LASTEXITCODE -ne 0) { throw 'Clonage du dépôt GitHub impossible.' }
    $kit = Join-Path $temporary 'kit'
    Push-Location $kit
    try {
        & (Join-Path $kit 'installer-client.cmd') """+ps(server)+" "+ps(code)+"""
        if ($LASTEXITCODE -ne 0) { throw ('Installation Windows échouée (code ' + $LASTEXITCODE + ').') }
    } finally { Pop-Location }
} finally { Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue }
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " + encoded


def install_command(kind, regie, port, code, password=False):
    if kind == "windows":
        return command_windows(regie, port, code)
    return command_linux(kind, regie, port, code, password)


class Deployments:
    def __init__(self):
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=MAX_JOBS, thread_name_prefix="deploy-ndi")
        self.jobs = {}

    def start(self, owner, screen, host, user, fingerprint, kind, regie, port, code, sudo_password=""):
        host, regie = validate_ipv4(host), validate_ipv4(regie)
        if host == regie:
            raise ValueError("Le lecteur ne peut pas utiliser l'adresse de la régie.")
        if not isinstance(user, str) or not USER.fullmatch(user):
            raise ValueError("Compte SSH du nouveau lecteur invalide.")
        if not isinstance(fingerprint, str) or not SSH_FINGERPRINT.fullmatch(fingerprint):
            raise ValueError("Relève l'empreinte SSH SHA256 sur le lecteur avant le déploiement.")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Port HTTP de la régie invalide.")
        if not isinstance(code, str) or not 8 <= len(code) <= 128:
            raise ValueError("Code d'association invalide.")
        if kind not in ("windows", "ubuntu", "pi"):
            raise ValueError("Type de lecteur invalide.")
        if not isinstance(sudo_password, str) or len(sudo_password) > 256 or any(
                ord(char) < 32 for char in sudo_password):
            raise ValueError("Mot de passe sudo invalide.")
        if kind == "windows" and sudo_password:
            raise ValueError("Le mot de passe sudo ne concerne que Linux.")
        key = ssh_identity()
        if not key.is_file():
            raise ValueError("La clé SSH privée de la régie est introuvable.")
        command = install_command(kind, regie, port, code, bool(sudo_password))
        import secrets
        identifier = secrets.token_urlsafe(22)
        with self.lock:
            active = [j for j in self.jobs.values() if j["state"] == "running"]
            if len(active) >= MAX_JOBS or any(j["screen"] == screen for j in active):
                raise ValueError("Un déploiement est déjà en cours. Attends sa fin.")
            job = dict(owner=owner, screen=screen, state="running", stdout="", stderr="",
                       code=None, started=time.time(), finished=None, message="Connexion SSH…")
            self.jobs[identifier] = job
            if len(self.jobs) > 30:
                finished = [k for k,v in self.jobs.items() if v["state"] != "running"]
                if finished:
                    del self.jobs[finished[0]]
        self.executor.submit(self._run, identifier, host, user, fingerprint, key, command, sudo_password)
        return {"job": identifier}

    def status(self, owner, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or job["owner"] != owner:
                raise ValueError("Déploiement inconnu ou session expirée.")
            return {key:value for key,value in job.items() if key != "owner"}

    def _append(self, identifier, stream, raw):
        with self.lock:
            job = self.jobs[identifier]
            field = "stdout" if stream == "out" else "stderr"
            job[field] = (job[field] + raw.decode("utf-8", "replace"))[-MAX_OUTPUT:]

    def _run(self, identifier, host, user, fingerprint, key, command, sudo_password):
        client = None
        outcome = dict(state="failed", message="Échec de connexion ou d'installation.", code=None)
        try:
            import paramiko
            client = paramiko.SSHClient()
            connect_client(paramiko, client, host, user,
                           {"key_filename": str(key)}, (fingerprint,))
            with self.lock:
                self.jobs[identifier]["message"] = "Connecté. Téléchargement Git et installation en cours…"
            channel = client.get_transport().open_session(timeout=12)
            if sudo_password:
                channel.get_pty(term="xterm", width=120, height=40)
            channel.exec_command(command)
            started = time.monotonic()
            marker = b"__SOUNDLIVE_SUDO_READY__"
            buffered = b""
            while True:
                if channel.recv_ready():
                    received = channel.recv(8192)
                    if sudo_password:
                        buffered += received
                        if marker in buffered:
                            before, after = buffered.split(marker, 1)
                            if before or after:
                                self._append(identifier, "out", before + after)
                            channel.sendall((sudo_password + "\\n").encode("utf-8"))
                            sudo_password = ""
                            buffered = b""
                        elif len(buffered) > len(marker):
                            self._append(identifier, "out", buffered[:-len(marker)])
                            buffered = buffered[-len(marker):]
                    else:
                        self._append(identifier, "out", received)
                if channel.recv_stderr_ready():
                    self._append(identifier, "err", channel.recv_stderr(8192))
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    if buffered:
                        self._append(identifier, "out", buffered)
                    code = channel.recv_exit_status()
                    outcome = dict(state="done" if code == 0 else "failed", code=code,
                                   message="Installation terminée." if code == 0
                                   else "L'installateur a signalé une erreur. Consulte le journal.")
                    break
                if time.monotonic() - started > INSTALL_TIMEOUT:
                    outcome = dict(state="failed", code=None, message="Déploiement trop long (35 minutes).")
                    break
                time.sleep(0.2)
            channel.close()
        except HostKeyCheck as error:
            outcome["message"] = str(error) + " Vérifie l'empreinte locale du lecteur."
        except Exception as error:
            # On n'expose pas les secrets ou les commandes internes dans la réponse.
            outcome["message"] = f"Connexion SSH / installation impossible ({type(error).__name__})."
        finally:
            if client:
                client.close()
            with self.lock:
                self.jobs[identifier].update(outcome, finished=time.time())
