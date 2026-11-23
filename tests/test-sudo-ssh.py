#!/usr/bin/env python3
"""Simule le canal SSH pour vérifier que le mot de passe sudo n'est pas affiché."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deploiement_lecteur as deploy

PASSWORD = "Test-Passphrase-DoNotLog"
MARKER = b"__SOUNDLIVE_SUDO_READY__"

class Channel:
    def __init__(self):
        self.stage = 0
        self.pty = False
        self.sent = None
    def get_pty(self, **kwargs):
        self.pty = True
    def exec_command(self, cmd):
        assert self.pty, "Le mot de passe sudo ne doit jamais être transmis sans PTY."
        assert "stty -echo" in cmd, "L'écho doit être désactivé avant la demande."
        assert PASSWORD not in cmd, "Le secret ne doit pas se trouver dans la commande SSH."
        self.stage = 1
    def recv_ready(self):
        return self.stage == 1
    def recv(self, size):
        self.stage = 2
        return MARKER
    def sendall(self, data):
        self.sent = data
        self.stage = 3
    def recv_stderr_ready(self):
        return False
    def exit_status_ready(self):
        return self.stage == 3
    def recv_exit_status(self):
        return 0
    def close(self):
        pass

class Client:
    def __init__(self):
        self.channel = Channel()
    def get_transport(self):
        return SimpleNamespace(open_session=lambda **kw: self.channel)
    def close(self):
        pass

fake = Client()
jobs = deploy.Deployments()
jobs.jobs["test"] = {
    "owner": ("127.0.0.1", None), "screen": "123", "state": "running",
    "stdout": "", "stderr": "", "finished": None,
}
command = deploy.command_linux("ubuntu", "192.168.56.1", 8765, "CODE_1234", True)
with patch.dict(sys.modules, {"paramiko": SimpleNamespace(SSHClient=lambda:fake)}):
    with patch.object(deploy, "connect_client", return_value=""):
        jobs._run("test", "192.168.56.21", "soundlive", "SHA256:example",
                  Path("/unused/private"), command, PASSWORD)

assert fake.channel.sent == (PASSWORD + "\n").encode("utf-8")
assert fake.channel.pty
assert jobs.jobs["test"]["state"] == "done", jobs.jobs["test"]
assert PASSWORD not in jobs.jobs["test"]["stdout"] + jobs.jobs["test"]["stderr"]
print("OK: mot de passe sudo transmis uniquement après marqueur et PTY sans écho.")
print("OK: secret absent du journal et de la commande distante.")

# Une RuntimeError métier doit être expliquée, jamais masquée par son seul type.
for detail in deploy.SAFE_SSH_RUNTIME_ERRORS:
    failure = deploy.deployment_error(RuntimeError(detail), "Authentification SSH")
    assert detail in failure, failure
assert "SECRET-NOT-FOR-LOGS" not in deploy.deployment_error(
    RuntimeError("SECRET-NOT-FOR-LOGS"), "Authentification SSH")
print("OK: cause SSH lisible, RuntimeError arbitraires masquées.")

# Vérifie que les nouvelles erreurs de connexion sont remontées dans le statut.
fake_failed = Client()
jobs.jobs["failed"] = {
    "owner": ("127.0.0.1", None), "screen": "321", "state": "running",
    "stdout": "", "stderr": "", "finished": None,
}
with patch.dict(sys.modules, {"paramiko": SimpleNamespace(SSHClient=lambda:fake_failed)}):
    with patch.object(deploy, "connect_client", side_effect=RuntimeError(
            "Clé refusée. Vérifie le compte SSH et autorise la clé de la régie.")):
        jobs._run("failed", "192.168.56.21", "soundlive", "SHA256:example",
                  Path("/unused/private"), "echo hello", "")
failed = jobs.status(("127.0.0.1", None), "failed")
assert failed["phase"] == "Authentification SSH"
assert "Clé refusée" in failed["message"], failed
print("OK: erreur de clé refusée identifiée avant le lancement de l'installateur.")
jobs.executor.shutdown(wait=False, cancel_futures=True)
