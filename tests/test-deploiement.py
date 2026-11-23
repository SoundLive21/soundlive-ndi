#!/usr/bin/env python3
"""Tests non destructifs du parcours ajout de clé / déploiement SSH."""
from base64 import b64decode
from html import unescape
from ipaddress import IPv4Address
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploiement_lecteur import command_linux, command_windows, validate_ipv4
from page_ssh import render, linux_command, windows_command

for address in ("10.164.106.23", "192.168.56.21", "172.20.1.10"):
    assert validate_ipv4(address) == address
for address in ("127.0.0.1", "8.8.8.8", "169.254.12.2", "0.0.0.0",
                "192.168.56.21;touch /tmp/exploit", "localhost"):
    try:
        validate_ipv4(address)
    except ValueError:
        pass
    else:
        raise AssertionError("IPv4 dangereuse ou non locale acceptée : " + address)
print("OK: limitation des IPv4 cibles au LAN privé.")

key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAQAB"
page = render(key).decode("utf-8")
assert "<title>SoundLive-NDI" in page
assert key in unescape(page)
assert "/addsshkey" in page
assert "__SSH_PUBLIC_KEY__" not in page
assert "sudo apt-get install" in page
assert "administrators_authorized_keys" in page
assert "Déployer" in page
print("OK: page de préparation SSH pour Ubuntu/Pi/Windows.")

linux = command_linux("ubuntu", "192.168.56.1", 8765, "CODE_1234")
assert "git clone --depth 1" in linux
assert "SoundLive21/soundlive-ndi.git" in linux
assert "--plateforme ubuntu http://192.168.56.1:8765 CODE_1234" in linux
assert "sudo -n" in linux
assert 'mktemp -d' in linux
pi = command_linux("pi", "192.168.56.1", 8765, "CODE_1234")
assert "--plateforme pi" in pi
for kind in ("windows", "invalid"):
    try:
        command_linux(kind, "192.168.56.1", 8765, "CODE_1234")
    except ValueError:
        pass
    else:
        raise AssertionError("Plateforme Linux inattendue acceptée")
print("OK: commande de déploiement Linux avec clonage Git officiel.")

encoded = command_windows("192.168.56.1", 8765, "CODE_1234").split()[-1]
powershell = b64decode(encoded).decode("utf-16le")
assert "Git for Windows requis" in powershell
assert "soundlive-ndi.git" in powershell
assert "installer-client.cmd" in powershell
assert "CODE_1234" in powershell
assert "192.168.56.1:8765" in powershell
assert "Administrateur" not in powershell or "administrateur" in powershell
print("OK: commande de déploiement Windows PowerShell encodée.")

for filename in ("tableau.py", "versions_regie.py", "page_ssh.py",
                 "deploiement_lecteur.py"):
    source = Path(filename).read_text(encoding="utf-8")
    compile(source, filename, "exec")
print("OK: syntaxe Python et dépendances du déploiement.")
