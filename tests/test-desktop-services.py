#!/usr/bin/env python3
"""Test de la détection RDP/VNC sans lancer de serveur ou modifier le réseau."""
import ast
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import socket
import sys

source = ast.parse(Path("diagnostic.py").read_text(encoding="utf-8"))
node = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "desktop_services")
namespace = {"sys": sys, "socket": socket}
exec(compile(ast.Module(body=[node], type_ignores=[]), "diagnostic.py", "exec"), namespace)
detect = namespace["desktop_services"]

def row(address, port, state="0A"):
    return f"    0: {address}:{port:04X} 00000000:0000 {state} 00000000:00000000 00:00000000 00000000 0 0 0"

def fake_ports(ipv4, ipv6=""):
    def open_file(path, encoding="ascii"):
        return StringIO("sl local_address rem_address st other\n" + (
            ipv6 if path.endswith("tcp6") else ipv4
        ))
    with patch("builtins.open", side_effect=open_file):
        return detect()

assert fake_ports(row("00000000", 3389)+"\n"+row("00000000", 5900)) == {"rdp":3389,"vnc":5900}
print("OK: RDP 3389 + TigerVNC 5900 => les deux boutons.")
assert fake_ports(row("00000000", 3390)) == {"rdp":3390}
print("OK: RDP 3390 => le port dynamique est conservé.")
assert fake_ports(row("0100007F", 3389)) == {}
print("OK: service lié uniquement à 127.0.0.1 est ignoré.")
assert fake_ports(row("00000000", 3389,"01")) == {}
print("OK: socket TCP non LISTEN est ignoré.")
assert fake_ports("",row("00000000000000000000000000000000",3389)) == {"rdp":3389}
print("OK: GNOME lié à [::]:3389 est détecté.")
assert fake_ports("",row("00000000000000000000000001000000",3389)) == {}
print("OK: socket IPv6 lié à ::1 uniquement est ignoré.")
assert fake_ports("") == {}
print("OK: aucun service => aucun protocole proposé.")
