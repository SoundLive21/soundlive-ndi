#!/usr/bin/env python3
"""Tests PowerDNS et ACME (sans créer de véritables enregistrements DNS)."""
from datetime import datetime, timedelta, timezone
import base64
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from acme_powerdns import (
    AcmeClient, PowerDNS, b64, create_account_key, create_certificate_key,
    encoded, validate_zone, public_txt_contains,
)
from gestion_acme_powerdns import (
    configure, get_config, _restore_secret, _protect_secret,
    read_status, renew_once, should_renew,
)
from certificats_acme import certificate_configuration, check_certificate
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.oid import NameOID

class PDNSHandler(BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *_):
        pass
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"name":"exemple.fr.","kind":"Native"}')
    def do_PATCH(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["X-API-Key"] == "test-api-key"
        assert self.path == "/api/v1/servers/localhost/zones/exemple.fr."
        self.requests.append(body)
        self.send_response(204)
        self.end_headers()

with HTTPServer(("127.0.0.1", 0), PDNSHandler) as server:
    api_url = "http://127.0.0.1:" + str(server.server_port)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pdns = PowerDNS(api_url, "test-api-key", "exemple.fr.")
    assert pdns.verify()["kind"] == "Native"
    token = b64(b"abcdefghijklmnopqrstuvwxy0123456")
    assert len(token) == 43
    pdns.txt("_acme-challenge.regie.exemple.fr", token, "EXTEND")
    pdns.txt("_acme-challenge.regie.exemple.fr", token, "PRUNE")
    assert [row["rrsets"][0]["changetype"] for row in PDNSHandler.requests] == ["EXTEND", "PRUNE"]
    assert all(row["rrsets"][0]["name"] == "_acme-challenge.regie.exemple.fr."
               for row in PDNSHandler.requests)
    server.shutdown()
    thread.join(timeout=5)
print("OK: API PowerDNS GET et TXT EXTEND/PRUNE sans effacer le reste de la zone.")

assert validate_zone("exemple.fr", "regie.exemple.fr") == "exemple.fr."
try:
    validate_zone("autre.fr", "regie.exemple.fr")
except ValueError:
    pass
else:
    raise AssertionError("PowerDNS a accepté une zone différente du domaine")
print("OK: validation du rattachement nom DNS à la zone.")

# Contrôle JWS/ACME : le serveur fictif vérifie la signature RSA et les
# tokens DNS-01. Aucune connexion à Let's Encrypt n'est effectuée.
account_key = create_account_key()
certificate_key = create_certificate_key()
state = dict(account=0, check=0, order=0, token=None, records=[],
             issued_pem=None, challenge=0, collected=[])

def fake_acme(method, url, body=None, headers=None):
    if method == "GET" and url.endswith("/directory"):
        return {
            "newNonce": "https://acme.test/nonce",
            "newAccount": "https://acme.test/account",
            "newOrder": "https://acme.test/order",
        }, {}, 200
    if method == "HEAD":
        return "", {"replay-nonce": "nonce" + str(len(state["collected"]) + 1)}, 200
    assert method == "POST"
    protected = json.loads(base64.urlsafe_b64decode(body["protected"] + "==="))
    payload64 = body["payload"]
    signed = (body["protected"] + "." + payload64).encode()
    signature = base64.urlsafe_b64decode(body["signature"] + "===")
    account_key.public_key().verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    assert protected["url"] == url
    assert protected["alg"] == "RS256"
    state["collected"].append(url)
    reply_headers = {"replay-nonce": "nonce" + str(len(state["collected"]) + 1)}
    if url.endswith("/account"):
        reply_headers["location"] = "https://acme.test/acct/1"
        assert "jwk" in protected and "kid" not in protected
        return {"status": "valid"}, reply_headers, 201
    assert protected.get("kid") == "https://acme.test/acct/1"
    if url.endswith("/order"):
        return {"authorizations": ["https://acme.test/auth/1"],
                "finalize": "https://acme.test/finalize"}, {
                    **reply_headers, "location": "https://acme.test/orders/1"
                }, 201
    if url.endswith("/auth/1"):
        state["check"] += 1
        return {"status": "valid" if state["challenge"] else "pending",
                "challenges": [{"type": "dns-01", "token": "token-12345",
                                "url": "https://acme.test/challenge/1"}]}, reply_headers, 200
    if url.endswith("/challenge/1"):
        assert payload64 == b64(b"{}")
        state["challenge"] += 1
        return {"status": "pending"}, reply_headers, 200
    if url.endswith("/finalize"):
        csr = base64.urlsafe_b64decode(json.loads(base64.urlsafe_b64decode(payload64 + "==="))["csr"] + "===")
        request = x509.load_der_x509_csr(csr)
        assert "regie.exemple.fr" in request.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        return {"status": "processing"}, reply_headers, 200
    if url.endswith("/orders/1"):
        return {"status": "valid", "certificate": "https://acme.test/cert/1"}, reply_headers, 200
    if url.endswith("/cert/1"):
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "regie.exemple.fr")])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(certificate_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(hours=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=90))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("regie.exemple.fr")]), False)
            .sign(certificate_key, hashes.SHA256()))
        return cert.public_bytes(serialization.Encoding.PEM).decode("ascii"), reply_headers, 200
    raise AssertionError("Unexpected ACME URL: " + url)

def add(name, token):
    state["records"].append(("add", name, token))
def remove(name, token):
    state["records"].append(("remove", name, token))
def wait(name, token):
    assert state["records"][-1] == ("add", name, token)
    expected = b64(sha256(("token-12345." + b64(sha256(encoded(jwk)).digest())
                           ).encode("ascii")).digest())
    assert token == expected

numbers = account_key.public_key().public_numbers()
jwk = {"kty": "RSA",
       "n": b64(numbers.n.to_bytes((numbers.n.bit_length()+7)//8, "big")),
       "e": b64(numbers.e.to_bytes((numbers.e.bit_length()+7)//8, "big"))}
with patch("acme_powerdns.json_http", side_effect=fake_acme):
    client = AcmeClient("production", account_key, "admin@exemple.fr")
    client.account()
    pem = client.issue("regie.exemple.fr", certificate_key, add, remove, wait, lambda _: None)
assert pem.startswith(b"-----BEGIN CERTIFICATE-----")
assert state["records"][0][0] == "add" and state["records"][1][0] == "remove"
assert state["challenge"] == 1
print("OK: compte ACME, signature JWS, challenge DNS-01, CSR et récupération PEM.")

with TemporaryDirectory() as temporary:
    folder = Path(temporary)
    (folder / "acces.json").write_text('{"ip":"192.168.56.1"}', encoding="utf-8")
    with patch("gestion_acme_powerdns.PowerDNS") as mock_powerdns:
        mock_powerdns.return_value.verify.return_value = {"kind": "Native", "name": "exemple.fr."}
        assert configure(folder, "regie.exemple.fr", "exemple.fr", api_url, "test-api-key",
                         "admin@exemple.fr", accept_terms=True) == "regie.exemple.fr"
        assert _restore_secret(get_config(folder)["secret"]) == "test-api-key"
        assert configure(folder, "regie.exemple.fr", "exemple.fr", api_url, "",
                         "admin@exemple.fr", accept_terms=True) == "regie.exemple.fr"
    assert should_renew(folder)
    with patch("gestion_acme_powerdns.PowerDNS") as mock_powerdns, \
         patch("gestion_acme_powerdns.AcmeClient") as mock_acme, \
         patch("gestion_acme_powerdns.create_certificate_key", return_value=certificate_key):
        mock_acme.return_value.issue.return_value = pem
        assert renew_once(folder) == "updated"
        assert not should_renew(folder)
        assert certificate_configuration(folder)["identifier"] == "regie.exemple.fr"
        assert check_certificate("regie.exemple.fr", folder / "acme-chain.pem",
                                  folder / "acme-key.pem")
        assert renew_once(folder) == "current"
    assert read_status(folder)["state"] == "current"
print("OK: cycle complet PowerDNS configuré, émission simulée et renouvellement planifié.")
