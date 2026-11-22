#!/usr/bin/env python3
"""Teste sans lancer de client graphique la sélection RDP/VNC de la régie."""
import ast
import os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

root = Path(__file__).resolve().parent.parent
module = ast.parse((root / "tableau.py").read_text(encoding="utf-8"))
function = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                and n.name == "desktop_command")
namespace = {
    "sys": SimpleNamespace(platform="linux"),
    "shutil": SimpleNamespace(which=lambda name: "/usr/bin/remmina" if name == "remmina" else None),
    "Path": Path,
    "os": os,
}
exec(compile(ast.Module(body=[function], type_ignores=[]),
             "tableau.py", "exec"), namespace)
command = namespace["desktop_command"]
assert command("192.168.56.21", "rdp") == [
    "/usr/bin/remmina", "-c", "rdp://192.168.56.21:3389"
]
assert command("192.168.56.21", "vnc") == [
    "/usr/bin/remmina", "-c", "vnc://192.168.56.21:5900"
]
assert command("192.168.56.21", "rdp", 3390) == [
    "/usr/bin/remmina", "-c", "rdp://192.168.56.21:3390"
]
assert command("192.168.56.21", "ssh") is None
print("OK : sur Linux, Remmina lance RDP et VNC sur les bons ports.")

namespace["shutil"] = SimpleNamespace(which=lambda name: (
    "/usr/bin/xtigervncviewer" if name == "xtigervncviewer" else None
))
assert command("192.168.56.21", "rdp") is None
assert command("192.168.56.21", "vnc") == [
    "/usr/bin/xtigervncviewer", "192.168.56.21::5900"
]
print("OK : TigerVNC reste disponible comme solution de repli pour VNC.")

namespace["sys"] = SimpleNamespace(platform="win32")
assert command("192.168.56.21", "rdp") == ["mstsc.exe", "/v:192.168.56.21:3389"]
print("OK : sous Windows, la connexion RDP garde mstsc.exe.")
