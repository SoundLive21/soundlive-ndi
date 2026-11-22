#!/usr/bin/env python3
"""Vérifie le protocole proposé selon le système et le bureau du lecteur."""
import ast
from pathlib import Path

source = ast.parse(Path("tableau.py").read_text(encoding="utf-8"))
node = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "support_methods")
program = compile(ast.Module(body=[node], type_ignores=[]), "<support_methods>", "exec")
scope = {}
exec(program, scope)
support = scope["support_methods"]

tests = (
    ("ubuntu", "rdp", None, ("ssh", "rdp")),
    ("ubuntu", "vnc", None, ("ssh", "vnc")),
    ("ubuntu", None, None, ("ssh", "vnc")),
    ("ubuntu", "vnc", {"rdp": 3389}, ("ssh", "rdp")),
    ("ubuntu", "rdp", {"vnc": 5900}, ("ssh", "vnc")),
    ("ubuntu", "rdp", {"rdp": 3390, "vnc": 5900}, ("ssh", "rdp", "vnc")),
    ("ubuntu", "rdp", {}, ("ssh",)),
    ("pi", "rdp", {"rdp": 3389}, ("ssh", "vnc")),
    ("windows", "rdp", {"vnc": 5900}, ("rdp", "vnc")),
    ("windows", "vnc", None, ("rdp", "vnc")),
)
for kind, protocol, services, expected in tests:
    actual = support(kind, protocol, services)
    assert actual == expected, f"{kind} / {protocol} / {services}: {actual} != {expected}"
    print(f"OK: {kind} / {protocol} / {services}: {actual}")
