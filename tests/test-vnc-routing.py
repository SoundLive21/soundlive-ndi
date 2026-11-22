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
    ("ubuntu", "rdp", ("ssh", "rdp")),
    ("ubuntu", "vnc", ("ssh", "vnc")),
    ("ubuntu", None, ("ssh", "vnc")),
    ("pi", "rdp", ("ssh", "vnc")),
    ("windows", "rdp", ("rdp", "vnc")),
    ("windows", "vnc", ("rdp", "vnc")),
)
for kind, protocol, expected in tests:
    actual = support(kind, protocol)
    assert actual == expected, f"{kind} / {protocol}: {actual} != {expected}"
    print(f"OK: {kind} / {protocol}: {actual}")
