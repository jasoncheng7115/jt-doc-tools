"""Real end-to-end SAML login with a genuinely signed SAML Response.

No external IdP needed: we act as the IdP — generate a self-signed cert, build a
SAML Response whose Assertion is XML-signed (xmlsec, via python3-saml's signer),
then POST it to our /auth/saml/acs over the real route and assert the SP
validates the signature, provisions the user, maps groups, and issues a session.
A tampered response must be rejected.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as app_main

BASE = "https://docs.example.com"
ACS = BASE + "/auth/saml/acs"
SP_ENTITY = BASE + "/auth/saml/metadata"
IDP_ENTITY = "https://idp.test/entity"


def _make_idp_cert():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-idp")])
    cert = (x509.CertificateBuilder().subject_name(subj).issuer_name(subj)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    cert_body = "".join(cert_pem.strip().splitlines()[1:-1])
    return key_pem, cert_pem, cert_body


def _signed_response(key_pem, cert_pem, *, nameid="kelly@corp.com",
                     groups=("Sales", "JTDT-Admins"), name="Kelly Chen") -> str:
    from onelogin.saml2.utils import OneLogin_Saml2_Utils
    from onelogin.saml2.constants import OneLogin_Saml2_Constants as K
    now = datetime.now(timezone.utc)
    nb = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    na = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    aid, rid = "_" + uuid.uuid4().hex, "_" + uuid.uuid4().hex
    gvals = "".join(f"<saml:AttributeValue>{g}</saml:AttributeValue>" for g in groups)
    assertion = (
        f'<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{aid}" Version="2.0" IssueInstant="{nb}"><saml:Issuer>{IDP_ENTITY}</saml:Issuer>'
        f'<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified">{nameid}</saml:NameID>'
        f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData NotOnOrAfter="{na}" Recipient="{ACS}"/></saml:SubjectConfirmation></saml:Subject>'
        f'<saml:Conditions NotBefore="{nb}" NotOnOrAfter="{na}"><saml:AudienceRestriction>'
        f'<saml:Audience>{SP_ENTITY}</saml:Audience></saml:AudienceRestriction></saml:Conditions>'
        f'<saml:AuthnStatement AuthnInstant="{nb}"><saml:AuthnContext>'
        f'<saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:Password</saml:AuthnContextClassRef>'
        f'</saml:AuthnContext></saml:AuthnStatement><saml:AttributeStatement>'
        f'<saml:Attribute Name="groups">{gvals}</saml:Attribute>'
        f'<saml:Attribute Name="displayName"><saml:AttributeValue>{name}</saml:AttributeValue></saml:Attribute>'
        f'</saml:AttributeStatement></saml:Assertion>'
    )
    signed = OneLogin_Saml2_Utils.add_sign(assertion, key_pem, cert_pem,
        sign_algorithm=K.RSA_SHA256, digest_algorithm=K.SHA256)
    sa = signed.decode() if isinstance(signed, bytes) else signed
    response = (
        f'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="{rid}" Version="2.0" '
        f'IssueInstant="{nb}" Destination="{ACS}"><saml:Issuer>{IDP_ENTITY}</saml:Issuer>'
        f'<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
        f'{sa}</samlp:Response>'
    )
    return base64.b64encode(response.encode()).decode()


def _configure(monkeypatch, tmp_path, cert_body):
    from app.core import sso_settings as s
    monkeypatch.setattr(s, "_path", lambda: tmp_path / "sso.json")
    s._invalidate_cache()
    s.save({"base_url": BASE,
            "saml": {"enabled": True, "idp_entity_id": IDP_ENTITY,
                     "idp_sso_url": "https://idp.test/sso", "idp_x509cert": cert_body,
                     "sp_entity_id": SP_ENTITY, "want_assertions_signed": True,
                     "name_attr": "displayName", "groups_attr": "groups",
                     "admin_group": "JTDT-Admins"}})


def test_full_saml_login_provisions_and_logs_in(admin_session, monkeypatch, tmp_path):
    key_pem, cert_pem, cert_body = _make_idp_cert()
    _configure(monkeypatch, tmp_path, cert_body)
    resp_b64 = _signed_response(key_pem, cert_pem)

    c = TestClient(app_main.app)
    r = c.post("/auth/saml/acs", data={"SAMLResponse": resp_b64, "RelayState": "/"},
               follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "error" not in r.headers["location"]
    assert c.cookies.get("jtdt_session")  # real session issued

    from app.core import auth_db, permissions
    row = auth_db.conn().execute(
        "SELECT id, username, source FROM users WHERE external_dn='kelly@corp.com'"
    ).fetchone()
    assert row and row["source"] == "saml"
    assert permissions.is_admin(row["id"])  # JTDT-Admins → admin
    gnames = [r["name"] for r in auth_db.conn().execute(
        "SELECT g.name FROM groups g JOIN group_members m ON m.group_id=g.id "
        "WHERE m.user_id=? AND g.source='saml'", (row["id"],))]
    assert set(gnames) == {"Sales", "JTDT-Admins"}


def test_tampered_saml_response_rejected(admin_session, monkeypatch, tmp_path):
    key_pem, cert_pem, cert_body = _make_idp_cert()
    _configure(monkeypatch, tmp_path, cert_body)
    resp_b64 = _signed_response(key_pem, cert_pem, nameid="kelly@corp.com")
    # Tamper the SIGNED content: swap the NameID an attacker wants to become.
    # This invalidates the assertion's XML signature → must be rejected.
    xml = base64.b64decode(resp_b64).decode()
    assert "kelly@corp.com" in xml
    tampered = base64.b64encode(
        xml.replace("kelly@corp.com", "attacker@evil.com").encode()).decode()

    c = TestClient(app_main.app)
    r = c.post("/auth/saml/acs", data={"SAMLResponse": tampered, "RelayState": "/"},
               follow_redirects=False)
    assert r.status_code == 302 and "error" in r.headers["location"]
    assert not c.cookies.get("jtdt_session")
    # and the attacker identity must NOT have been provisioned
    from app.core import auth_db
    assert auth_db.conn().execute(
        "SELECT 1 FROM users WHERE external_dn='attacker@evil.com'").fetchone() is None


def test_saml_response_signed_by_wrong_key_rejected(admin_session, monkeypatch, tmp_path):
    # Configure SP to trust cert A, but sign the response with key B.
    _, _, cert_body_A = _make_idp_cert()
    key_pem_B, cert_pem_B, _ = _make_idp_cert()
    _configure(monkeypatch, tmp_path, cert_body_A)
    resp_b64 = _signed_response(key_pem_B, cert_pem_B)

    c = TestClient(app_main.app)
    r = c.post("/auth/saml/acs", data={"SAMLResponse": resp_b64, "RelayState": "/"},
               follow_redirects=False)
    assert r.status_code == 302 and "error" in r.headers["location"]
    assert not c.cookies.get("jtdt_session")
