"""Tests for the SSO feature (OIDC + SAML): settings encryption, JIT
provisioning + group/role mapping, OIDC claim mapping + SSRF guard, route
gating (state/nonce, disabled, public-path), and admin-page ACL.

Network calls (OIDC discovery / id_token verify) are monkeypatched — we test
our wiring, not the IdP.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as app_main


# ---------------- sso_settings ----------------

class TestSettings:
    def test_secret_encrypted_masked_and_preserved(self, auth_off, monkeypatch, tmp_path):
        from app.core import sso_settings as s
        monkeypatch.setattr(s, "_path", lambda: tmp_path / "sso.json")
        s._invalidate_cache()
        s.save({"oidc": {"enabled": True, "issuer": "https://i", "client_id": "c",
                         "client_secret_enc": "supersecret"}})
        # get() masks the secret
        assert s.get()["oidc"]["client_secret_enc"] == s.SECRET_KEPT
        # on-disk value is NOT the plaintext
        raw = (tmp_path / "sso.json").read_text()
        assert "supersecret" not in raw
        # reveal decrypts
        assert s.get_oidc(reveal=True)["client_secret"] == "supersecret"
        # re-save with SECRET_KEPT preserves it
        s.save({"oidc": {"enabled": True, "issuer": "https://i", "client_id": "c",
                         "client_secret_enc": s.SECRET_KEPT}})
        assert s.get_oidc(reveal=True)["client_secret"] == "supersecret"
        # empty string clears it
        s.save({"oidc": {"enabled": True, "issuer": "https://i", "client_id": "c",
                         "client_secret_enc": ""}})
        assert s.get_oidc(reveal=True)["client_secret"] == ""

    def test_login_buttons_gated_on_config(self, auth_off, monkeypatch, tmp_path):
        from app.core import sso_settings as s
        monkeypatch.setattr(s, "_path", lambda: tmp_path / "sso.json")
        s._invalidate_cache()
        assert s.login_buttons() == []
        s.save({"oidc": {"enabled": True, "issuer": "https://i", "client_id": "c"}})
        btns = s.login_buttons()
        assert len(btns) == 1 and btns[0]["provider"] == "oidc"
        # enabled but missing client_id → not shown
        s.save({"oidc": {"enabled": True, "issuer": "https://i", "client_id": ""}})
        assert s.login_buttons() == []


# ---------------- oidc service ----------------

class TestOIDC:
    def test_check_url_blocks_metadata_and_bad_scheme(self):
        from app.core import oidc
        with pytest.raises(oidc.OIDCError):
            oidc._check_url("http://169.254.169.254/latest")
        with pytest.raises(oidc.OIDCError):
            oidc._check_url("ftp://example.com")
        assert oidc._check_url("https://idp.example.com/x")  # ok

    def test_map_claims(self):
        from app.core import oidc
        cfg = {"username_claim": "preferred_username", "email_claim": "email",
               "name_claim": "name", "groups_claim": "groups"}
        out = oidc.map_claims(cfg, {"sub": "abc", "preferred_username": "jo",
                                    "email": "jo@x.com", "name": "Jo",
                                    "groups": ["Sales", "Admins"]})
        assert out == {"sub": "abc", "username": "jo", "email": "jo@x.com",
                       "name": "Jo", "groups": ["Sales", "Admins"]}
        # string groups + fallbacks
        out2 = oidc.map_claims(cfg, {"sub": "s2", "groups": "A, B"})
        assert out2["groups"] == ["A", "B"] and out2["username"] == "s2"


# ---------------- JIT provisioning ----------------

class TestProvision:
    def test_jit_create_idempotent_and_group_admin(self, admin_session):
        from app.core import sso_provision as p, permissions, auth_db
        u1 = p.provision("oidc", external_id="sub-1", username="alice",
                         display_name="Alice", groups=["Sales"], admin_group="Admins")
        assert u1["created"] is True
        assert "default-user" in permissions.list_roles_for_subject("user", str(u1["user_id"]))
        assert not permissions.is_admin(u1["user_id"])  # not in Admins yet
        # Second login = same user, not admin (groups changed: now in Admins)
        u2 = p.provision("oidc", external_id="sub-1", username="alice",
                         display_name="Alice R", groups=["Sales", "Admins"],
                         admin_group="Admins")
        assert u2["user_id"] == u1["user_id"] and u2["created"] is False
        assert permissions.is_admin(u2["user_id"])  # admin_group matched → admin
        # Drop out of Admins → admin revoked
        p.provision("oidc", external_id="sub-1", username="alice",
                    display_name="Alice", groups=["Sales"], admin_group="Admins")
        assert not permissions.is_admin(u1["user_id"])
        # user row carries source=oidc + external_dn=sub-1
        row = auth_db.conn().execute(
            "SELECT source, external_dn FROM users WHERE id=?", (u1["user_id"],)).fetchone()
        assert row["source"] == "oidc" and row["external_dn"] == "sub-1"

    def test_username_clash_rejected(self, admin_session):
        from app.core import sso_provision as p
        p.provision("oidc", external_id="ext-A", username="bob", display_name="Bob")
        with pytest.raises(p.SSOProvisionError):
            p.provision("oidc", external_id="ext-B", username="bob", display_name="Bob2")

    def test_missing_external_id_rejected(self, admin_session):
        from app.core import sso_provision as p
        with pytest.raises(p.SSOProvisionError):
            p.provision("oidc", external_id="", username="x", display_name="x")


# ---------------- routes ----------------

class TestRoutes:
    def test_oidc_login_disabled_redirects_error(self, admin_session, monkeypatch, tmp_path):
        from app.core import sso_settings as s
        monkeypatch.setattr(s, "_path", lambda: tmp_path / "sso.json")
        s._invalidate_cache()
        client, _, _ = admin_session
        r = client.get("/auth/oidc/login", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["location"] and "error" in r.headers["location"]

    def test_oidc_login_redirects_to_idp(self, admin_session, monkeypatch, tmp_path):
        from app.core import sso_settings as s, oidc
        monkeypatch.setattr(s, "_path", lambda: tmp_path / "sso.json")
        s._invalidate_cache()
        s.save({"oidc": {"enabled": True, "issuer": "https://idp.example.com",
                         "client_id": "cid", "client_secret_enc": "sec"}})
        monkeypatch.setattr(oidc, "discover", lambda cfg: {
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks"})
        client, _, _ = admin_session
        r = client.get("/auth/oidc/login", follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith("https://idp.example.com/authorize")
        assert "state=" in loc and "nonce=" in loc and "client_id=cid" in loc
        # tx cookie set
        assert "jtdt_sso_tx" in r.headers.get("set-cookie", "")

    def test_oidc_callback_state_mismatch(self, admin_session, monkeypatch, tmp_path):
        from app.core import sso_settings as s
        monkeypatch.setattr(s, "_path", lambda: tmp_path / "sso.json")
        s._invalidate_cache()
        s.save({"oidc": {"enabled": True, "issuer": "https://i", "client_id": "c"}})
        client, _, _ = admin_session
        # no tx cookie → state can't match
        r = client.get("/auth/oidc/callback?code=x&state=evil", follow_redirects=False)
        assert r.status_code == 302
        assert "error" in r.headers["location"]

    def test_sso_endpoints_are_public_paths(self, admin_session, monkeypatch, tmp_path):
        # With auth ON and NO session, /auth/oidc/login must NOT be bounced by the
        # auth gate to /login?next=... (it's a public prefix); it reaches the
        # handler which (SSO disabled) redirects to /login?error=...
        from app.core import sso_settings as s
        monkeypatch.setattr(s, "_path", lambda: tmp_path / "sso.json")
        s._invalidate_cache()
        c = TestClient(app_main.app)  # no cookie
        r = c.get("/auth/oidc/login", follow_redirects=False)
        assert r.status_code == 302
        assert "next=" not in r.headers["location"]  # not the gate redirect


# ---------------- admin page ACL ----------------

class TestAdminAcl:
    def _non_admin(self, username="sso_na"):
        from app.core import user_manager, sessions
        uid = user_manager.create_local(username, username, "UserPass1234")
        token, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
        c = TestClient(app_main.app)
        c.cookies.set(sessions.COOKIE_NAME, token)
        return c

    def test_admin_can_open_sso_page(self, admin_session):
        client, _, _ = admin_session
        r = client.get("/admin/sso", follow_redirects=False)
        assert r.status_code == 200

    def test_non_admin_blocked_from_sso_page_and_save(self, admin_session):
        c = self._non_admin()
        assert c.get("/admin/sso", follow_redirects=False).status_code == 403
        r = c.post("/admin/sso/save", json={"oidc": {"enabled": True}},
                   follow_redirects=False)
        assert r.status_code == 403


class TestAccountIsolation:
    """Classic SSO pitfall: an IdP identity must NOT take over a same-named
    local account or inherit its privileges."""

    def test_sso_user_does_not_shadow_or_inherit_local_admin(self, admin_session):
        from app.core import user_manager, permissions, sso_provision as p, auth_db
        # A privileged LOCAL user named 'vip'
        local_uid = user_manager.create_local("vip", "VIP", "LocalPass1234",
                                               roles=["admin"])
        assert permissions.is_admin(local_uid)
        # An OIDC identity claiming the same username 'vip' (attacker-controlled)
        u = p.provision("oidc", external_id="evil-sub", username="vip",
                        display_name="Not VIP", groups=["randoms"])
        # → a SEPARATE account, different id, source=oidc, NOT admin
        assert u["user_id"] != local_uid
        row = auth_db.conn().execute("SELECT source FROM users WHERE id=?",
                                     (u["user_id"],)).fetchone()
        assert row["source"] == "oidc"
        assert not permissions.is_admin(u["user_id"])
        # the local admin is untouched
        assert permissions.is_admin(local_uid)

    def test_disabled_sso_user_cannot_relogin(self, admin_session):
        from app.core import sso_provision as p, auth_db
        u = p.provision("oidc", external_id="sub-dis", username="dwight",
                        display_name="D")
        auth_db.conn().execute("UPDATE users SET enabled=0 WHERE id=?",
                               (u["user_id"],))
        auth_db.conn().commit()
        with pytest.raises(p.SSOProvisionError):
            p.provision("oidc", external_id="sub-dis", username="dwight",
                        display_name="D")
