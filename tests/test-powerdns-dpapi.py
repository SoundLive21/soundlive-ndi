#!/usr/bin/env python3
"""Vérification DPAPI sous Windows, sans aucun accès PowerDNS réel."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gestion_acme_powerdns import _protect_secret, _restore_secret
secret = "powerdns-api-dont-expose-1234"
data = _protect_secret(secret)
if sys.platform == "win32":
    assert data["type"] == "dpapi-user"
    assert secret not in data["data"]
else:
    assert data["type"] == "restricted-file"
assert _restore_secret(data) == secret
print("OK: protection de la clé PowerDNS avec DPAPI utilisateur sous Windows.")
