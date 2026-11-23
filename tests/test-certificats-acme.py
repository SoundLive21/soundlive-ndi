#!/usr/bin/env python3
"""Tests non destructifs du HTTPS ACME DNS/IP et du rechargement TLS."""
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
import ast
import os
from tempfile import TemporaryDirectory

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from certificats_acme import (ReloadableCertificate, check_identifier, check_certificate,
                               certificate_configuration, configure_certificate, disable_certificate)


def generate(name, folder, suffix=""):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    try:
        san = x509.IPAddress(ip_address(name))
    except ValueError:
        san = x509.DNSName(name)
    cert = (x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .sign(key, hashes.SHA256()))
    chain = folder / ("fullchain" + suffix + ".pem")
    private = folder / ("privkey" + suffix + ".pem")
    chain.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                          serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return chain, private


for rejected in ("192.168.56.1", "10.164.106.2", "127.0.0.1",
                 "172.20.2.1", "localhost", "regie.local", "*.example.fr"):
    try:
        check_identifier(rejected)
    except ValueError:
        pass
    else:
        raise AssertionError("Adresse ou nom non public accepté : " + rejected)
assert check_identifier("regie.Exemple.FR.") == "regie.exemple.fr"
assert check_identifier("1.1.1.1") == "1.1.1.1"
print("OK: noms DNS et IP publiques, aucune IP privée.")

with TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "acces.json").write_text('{"ip":"192.168.56.1"}', encoding="utf-8")
    chain, private = generate("regie.exemple.fr", root)
    ctx, expiry = check_certificate("regie.exemple.fr", chain, private)
    assert ctx is not None and expiry > datetime.now(timezone.utc)
    assert configure_certificate(root, "regie.exemple.fr", chain, private) == "regie.exemple.fr"
    assert certificate_configuration(root)["fullchain"] == str(chain.resolve())
    reloadable = ReloadableCertificate(root, "unused")
    assert reloadable.identifier == "regie.exemple.fr"
    initial_context = reloadable.current()
    print("OK: certificat DNS PEM chargé sans modifier le certificat local.")

    another_chain, another_key = generate("regie.exemple.fr", root, "2")
    chain.write_bytes(another_chain.read_bytes())
    private.write_bytes(another_key.read_bytes())
    reloadable.last_checked = 0
    new_context = reloadable.current()
    assert new_context is not initial_context
    assert reloadable.error is None
    print("OK: renouvellement du PEM chargé automatiquement sans redémarrage.")

    original_key = private.read_bytes()
    private.write_bytes(b"invalid private key")
    reloadable.last_checked = 0
    assert reloadable.current() is new_context
    assert reloadable.error
    private.write_bytes(original_key)
    reloadable.last_checked = 0
    assert reloadable.current() is not None
    print("OK: rotation PEM invalide : dernier certificat valide conservé.")

    try:
        check_certificate("regie.autre.fr", chain, private)
    except ValueError:
        pass
    else:
        raise AssertionError("Certificat SAN différent accepté")
    ipchain, ipkey = generate("1.1.1.1", root, "ip")
    assert configure_certificate(root, "1.1.1.1", ipchain, ipkey) == "1.1.1.1"
    assert ReloadableCertificate(root, "unused").identifier == "1.1.1.1"
    print("OK: SAN de certificat IP publique.")
    disable_certificate(root)
    assert certificate_configuration(root) is None

for file in ("regie-windows.py", "tableau.py", "acces_regie.py", "certificats_acme.py"):
    ast.parse(Path(file).read_text(encoding="utf-8"))
print("OK: syntaxe Python de la régie et du lanceur.")
