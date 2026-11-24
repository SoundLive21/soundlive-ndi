#!/usr/bin/env python3
"""Tests locaux sans réseau et sans toucher à la vraie régie."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sauvegarde_regie import create_backup, read_backup, restore_backup, allowed
from gestion_acme_powerdns import _protect_secret, _restore_secret

password = "mot-de-passe-test-2026"
for name in ("../ecrans.json", ".soundlive-acces/../../tableau.py",
             "tableau.py", ".soundlive-acces/CON", "/ecrans.json"):
    assert not allowed(name)

with TemporaryDirectory() as tmp:
    base = Path(tmp)
    src, dst = base / "source", base / "destination"
    access = src / ".soundlive-acces"
    access.mkdir(parents=True)
    (src / "ecrans.json").write_text('{"test":"jeton-fictif"}', encoding="utf-8")
    (access / "acces.json").write_text('{"ip":"192.168.1.2"}', encoding="utf-8")
    (access / "acme-powerdns.json").write_text(json.dumps({
        "secret": _protect_secret("secret-fictif"), "domain": "ndi.example.fr"}), encoding="utf-8")
    external = base / "external.pem"
    external.write_bytes(b"certificat-fictif")
    key = base / "external-key.pem"
    key.write_bytes(b"cle-fictive")
    (access / "certificat-acme.json").write_text(json.dumps({
        "identifier": "ndi.example.fr", "fullchain": str(external),
        "private_key": str(key)}), encoding="utf-8")
    dhcp = src / ".soundlive-dhcp"
    dhcp.mkdir()
    (dhcp / "configuration.json").write_text('{"enabled":true}', encoding="utf-8")
    archive = base / "test.slbackup"
    # Ne jamais récupérer les clés SSH réelles de l'utilisateur des tests.
    with patch("installation_lecteur.ssh_identity", side_effect=lambda root: root / ".soundlive-update/ssh"):
        assert create_backup(src, archive, password) >= 7
        assert b"secret-fictif" not in archive.read_bytes()
        files = read_backup(archive, password)
        assert json.loads(files[".soundlive-acces/acme-powerdns.json"])["secret"]["type"] == "backup"
        for wrong in ("mot-de-passe-incorrect",):
            try:
                read_backup(archive, wrong)
            except ValueError:
                pass
            else:
                raise AssertionError("Mot de passe incorrect accepté")
        damaged = base / "damaged.slbackup"
        raw = archive.read_bytes()
        damaged.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        try:
            read_backup(damaged, password)
        except ValueError:
            pass
        else:
            raise AssertionError("Archive modifiée acceptée")
        dst.mkdir()
        (dst / "ecrans.json").write_text("ancienne configuration", encoding="utf-8")
        count, safety = restore_backup(dst, archive, password, "192.168.1.3")
        assert count == len(files) and safety.is_file()
        assert read_backup(safety, password)["ecrans.json"] == b"ancienne configuration"
        assert (dst / "ecrans.json").read_bytes() == (src / "ecrans.json").read_bytes()
        restored = dst / ".soundlive-acces"
        cfg = json.loads((restored / "certificat-acme.json").read_text())
        assert Path(cfg["fullchain"]).parent == restored
        assert Path(cfg["fullchain"]).read_bytes() == external.read_bytes()
        assert Path(cfg["private_key"]).read_bytes() == key.read_bytes()
        assert _restore_secret(json.loads((restored / "acme-powerdns.json").read_text())["secret"]) == "secret-fictif"
        assert json.loads((restored / "acces.json").read_text())["ip"] == "192.168.1.3"
        assert not json.loads((dst / ".soundlive-dhcp/configuration.json").read_text())["enabled"]
print("OK: sauvegarde chiffrée, contrôle d'intégrité, secret portable, restauration et copie de sécurité.")
