"""Real end-to-end OIDC login against a self-hosted, spec-conformant mini IdP.

No external IdP account needed: we stand up a tiny real OIDC provider in a
background thread (real RSA key, real /.well-known/openid-configuration, /jwks,
/token), point jt-doc-tools at it, and drive the full Authorization-Code flow
over real HTTP — exercising our actual discovery + token exchange + JWKS
signature verification + nonce/state checks + JIT provisioning + session issue.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import pytest
from fastapi.testclient import TestClient

import app.main as app_main


# ---- mini IdP state (mutated per-test) ----
class _IdP:
    def __init__(self):
        self.issuer = ""
        self.client_id = "jtdt-client"
        self.private_key = None
        self.jwks = {}
        self.kid = "test-key-1"
        self.next_claims = {}     # claims to mint into the next id_token

    def mint_id_token(self) -> str:
        import jwt
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": self.client_id,
                  "iat": now, "exp": now + 300, **self.next_claims}
        return jwt.encode(claims, self.private_key, algorithm="RS256",
                          headers={"kid": self.kid})


IDP = _IdP()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/.well-known/openid-configuration":
            self._json({
                "issuer": IDP.issuer,
                "authorization_endpoint": IDP.issuer + "/authorize",
                "token_endpoint": IDP.issuer + "/token",
                "jwks_uri": IDP.issuer + "/jwks",
            })
        elif path == "/jwks":
            self._json(IDP.jwks)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if urlparse(self.path).path == "/token":
            self._json({"id_token": IDP.mint_id_token(),
                        "access_token": "at", "token_type": "Bearer"})
        else:
            self.send_response(404); self.end_headers()


@pytest.fixture
def idp():
    """Start the mini IdP on a free port; generate a real RSA key + JWKS."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    IDP.private_key = key
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": IDP.kid, "use": "sig", "alg": "RS256"})
    IDP.jwks = {"keys": [jwk]}
    IDP.next_claims = {}

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    IDP.issuer = f"http://127.0.0.1:{srv.server_address[1]}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    # clear oidc discovery cache so our fresh issuer is fetched live
    from app.core import oidc
    oidc._DISCO_CACHE.clear()
    yield IDP
    srv.shutdown()


def _configure_sso(monkeypatch, tmp_path, idp):
    from app.core import sso_settings as s
    monkeypatch.setattr(s, "_path", lambda: tmp_path / "sso.json")
    s._invalidate_cache()
    s.save({"base_url": "",
            "oidc": {"enabled": True, "issuer": idp.issuer,
                     "client_id": idp.client_id, "client_secret_enc": "shh",
                     "groups_claim": "groups", "admin_group": "JTDT-Admins"}})


def test_full_oidc_login_provisions_and_logs_in(admin_session, idp, monkeypatch, tmp_path):
    _configure_sso(monkeypatch, tmp_path, idp)
    c = TestClient(app_main.app)  # fresh client, no session

    # 1) /auth/oidc/login → 302 to the IdP authorize URL carrying state+nonce
    r = c.get("/auth/oidc/login?next=/", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["client_id"] == [idp.client_id]
    state, nonce = q["state"][0], q["nonce"][0]
    assert c.cookies.get("jtdt_sso_tx")  # tx cookie set

    # 2) IdP "authenticates" the user → mint an id_token with that nonce
    idp.next_claims = {"sub": "user-42", "nonce": nonce,
                       "preferred_username": "kelly", "email": "kelly@corp.com",
                       "name": "Kelly Chen", "groups": ["Sales", "JTDT-Admins"]}

    # 3) browser hits our callback (tx cookie auto-sent by the client)
    r2 = c.get(f"/auth/oidc/callback?code=authcode&state={state}",
               follow_redirects=False)
    assert r2.status_code == 302, r2.text
    assert "error" not in r2.headers["location"]
    assert c.cookies.get("jtdt_session")  # real session issued

    # 4) the session works on a protected page (no /login bounce)
    r3 = c.get("/", follow_redirects=False)
    assert r3.status_code != 302 or "/login" not in r3.headers.get("location", "")

    # 5) user JIT-provisioned with source=oidc + admin_group → admin
    from app.core import auth_db, permissions
    row = auth_db.conn().execute(
        "SELECT id, username, source, external_dn FROM users WHERE external_dn='user-42'"
    ).fetchone()
    assert row and row["source"] == "oidc" and row["username"] == "kelly"
    assert permissions.is_admin(row["id"])  # in JTDT-Admins → admin
    # groups synced
    gnames = [r["name"] for r in auth_db.conn().execute(
        "SELECT g.name FROM groups g JOIN group_members m ON m.group_id=g.id "
        "WHERE m.user_id=? AND g.source='oidc'", (row["id"],))]
    assert set(gnames) == {"Sales", "JTDT-Admins"}


def test_oidc_rejects_tampered_nonce(admin_session, idp, monkeypatch, tmp_path):
    _configure_sso(monkeypatch, tmp_path, idp)
    c = TestClient(app_main.app)
    r = c.get("/auth/oidc/login", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    # IdP mints a token with the WRONG nonce (replay / mismatched session)
    idp.next_claims = {"sub": "u1", "nonce": "WRONG-NONCE", "email": "x@y.com"}
    r2 = c.get(f"/auth/oidc/callback?code=c&state={state}", follow_redirects=False)
    assert r2.status_code == 302 and "error" in r2.headers["location"]
    assert not c.cookies.get("jtdt_session")  # NOT logged in


def test_oidc_rejects_forged_token_wrong_key(admin_session, idp, monkeypatch, tmp_path):
    """A token signed by a DIFFERENT key must fail JWKS verification."""
    _configure_sso(monkeypatch, tmp_path, idp)
    c = TestClient(app_main.app)
    r = c.get("/auth/oidc/login", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    nonce = parse_qs(urlparse(r.headers["location"]).query)["nonce"][0]
    # Swap the IdP's signing key to an attacker key NOT in the published JWKS.
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    real_key = IDP.private_key
    IDP.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    IDP.next_claims = {"sub": "u1", "nonce": nonce, "email": "x@y.com"}
    try:
        r2 = c.get(f"/auth/oidc/callback?code=c&state={state}", follow_redirects=False)
    finally:
        IDP.private_key = real_key
    assert r2.status_code == 302 and "error" in r2.headers["location"]
    assert not c.cookies.get("jtdt_session")
