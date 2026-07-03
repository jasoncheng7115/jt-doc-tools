"""Reverse-proxy (Kerberos/SPNEGO) SSO — app/core/proxy_sso.py + middleware.

We can't stand up a real AD + Nginx + Kerberos here, so the LDAP lookup
(`auth_ldap.sync_user_by_username`) is monkeypatched to a fake directory. What
IS exercised for real: header normalisation, trusted-proxy IP gating, audit
events, session issuance via the shared 2FA-aware complete_login, forced-2FA
routing for auditors, fallback behaviour, and jtdt-admin break-glass.

Acceptance criteria mapped (client spec):
  1/2. trusted header → auto-login ; no header → /login
  3.   first login syncs user (mock records the call)
  5.   untrusted client sending the header does NOT log in
  6.   jtdt-admin still reaches /login
  7.   auditor still forced through 2FA
"""
from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from app import main as app_main
from app.core import audit_db, auth_db, auth_settings, db, proxy_sso, sessions


# ----------------------------- helpers ------------------------------------

class _FakeReq:
    """Minimal stand-in for a Starlette Request for resolve_user() unit tests."""
    def __init__(self, headers=None, client_host="127.0.0.1", path="/", query=""):
        self.headers = headers or {}
        self.client = type("C", (), {"host": client_host})() if client_host else None
        self.scope = {"path": path}
        self.url = type("U", (), {"query": query})()


@pytest.fixture(autouse=True)
def _reset_proxy_cfg_after():
    """proxy_sso config lives in the shared auth_settings.json. Reset it to the
    disabled default on teardown so we don't leak enabled=True /
    fallback_login=False into other test files (which would flip unauthenticated
    requests from a /login redirect to a 401)."""
    yield
    s = auth_settings.get()
    s["proxy_sso"] = {"enabled": False, "header": "X-Remote-User",
                      "fallback_login": True,
                      "trusted_proxies": ["127.0.0.1", "::1"]}
    auth_settings.save(s)


def _set_proxy_cfg(**over):
    s = auth_settings.get()
    s.setdefault("proxy_sso", {})
    s["proxy_sso"] = {
        "enabled": True, "header": "X-Remote-User",
        "fallback_login": True, "trusted_proxies": ["127.0.0.1", "::1"],
        **over,
    }
    auth_settings.save(s)


def _wait_audit(event_type: str, timeout: float = 3.0) -> int:
    """Poll the (async-written) audit log for at least one row of event_type."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        n = audit_db.conn().execute(
            "SELECT COUNT(1) FROM audit_events WHERE event_type=?",
            (event_type,)).fetchone()[0]
        if n:
            return n
        time.sleep(0.05)
    return 0


def _clear_audit():
    conn = audit_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM audit_events")


# ----------------------------- normalize ----------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("EXAMPLE\\jsmith", "jsmith"),      # DOMAIN\user
    ("jsmith@example.local", "jsmith"), # UPN
    ("jsmith", "jsmith"),               # bare
    ("EXAMPLE\\jsmith@example.local", "jsmith"),  # both forms (rare)
    ("  jsmith  ", "jsmith"),           # trimmed
])
def test_normalize_valid(raw, expected):
    assert proxy_sso.normalize_remote_user(raw) == expected


@pytest.mark.parametrize("raw", [
    "", None, "   ",
    "js mith",                # internal space
    "jsmith\r\nSet-Cookie: x",  # CRLF injection attempt
    "js\tmith",               # tab
])
def test_normalize_rejects(raw):
    assert proxy_sso.normalize_remote_user(raw) == ""


# --------------------------- trusted proxy --------------------------------

def test_trusted_proxy_exact(auth_off):
    _set_proxy_cfg(trusted_proxies=["127.0.0.1", "::1"])
    assert proxy_sso.client_is_trusted(_FakeReq(client_host="127.0.0.1"))
    assert proxy_sso.client_is_trusted(_FakeReq(client_host="::1"))
    assert not proxy_sso.client_is_trusted(_FakeReq(client_host="10.0.0.9"))


def test_trusted_proxy_cidr_and_mapped(auth_off):
    _set_proxy_cfg(trusted_proxies=["10.0.0.0/24"])
    assert proxy_sso.client_is_trusted(_FakeReq(client_host="10.0.0.55"))
    assert not proxy_sso.client_is_trusted(_FakeReq(client_host="10.0.1.55"))
    # IPv4-mapped IPv6 form of a trusted v4 address
    _set_proxy_cfg(trusted_proxies=["127.0.0.1"])
    assert proxy_sso.client_is_trusted(_FakeReq(client_host="::ffff:127.0.0.1"))


def test_trusted_proxy_rejects_garbage_peer(auth_off):
    _set_proxy_cfg(trusted_proxies=["127.0.0.1"])
    assert not proxy_sso.client_is_trusted(_FakeReq(client_host="testclient"))
    assert not proxy_sso.client_is_trusted(_FakeReq(client_host=None))


# --------------------------- resolve_user ---------------------------------

def test_resolve_missing_header(auth_off):
    _set_proxy_cfg()
    _clear_audit()
    status, user = proxy_sso.resolve_user(_FakeReq(headers={}))
    assert status == proxy_sso.MISSING and user is None
    assert _wait_audit("proxy_sso_header_missing")


def test_resolve_untrusted(auth_off):
    _set_proxy_cfg(trusted_proxies=["10.9.9.9"])
    _clear_audit()
    req = _FakeReq(headers={"X-Remote-User": "EXAMPLE\\jsmith"},
                   client_host="127.0.0.1")
    status, user = proxy_sso.resolve_user(req)
    assert status == proxy_sso.UNTRUSTED and user is None
    assert _wait_audit("proxy_sso_untrusted_proxy")


def test_resolve_ok_and_fail(auth_off, monkeypatch):
    _set_proxy_cfg()
    _clear_audit()

    calls = []

    def fake_sync(username, *, ip=""):
        calls.append(username)
        return {"user_id": 4242, "username": username, "dn": "CN=x,DC=e"}

    monkeypatch.setattr("app.core.auth_ldap.sync_user_by_username", fake_sync)
    req = _FakeReq(headers={"X-Remote-User": "EXAMPLE\\jsmith"},
                   client_host="127.0.0.1")
    status, user = proxy_sso.resolve_user(req)
    assert status == proxy_sso.OK
    assert user["user_id"] == 4242
    assert calls == ["jsmith"]  # normalised before lookup
    assert _wait_audit("proxy_sso_login_success")

    # Now make the directory lookup blow up → FAIL, audited, no crash.
    _clear_audit()

    def boom(username, *, ip=""):
        raise RuntimeError("ldap down")

    monkeypatch.setattr("app.core.auth_ldap.sync_user_by_username", boom)
    status, user = proxy_sso.resolve_user(req)
    assert status == proxy_sso.FAIL and user is None
    assert _wait_audit("proxy_sso_login_fail")


# --------------------------- middleware e2e -------------------------------

@pytest.fixture
def proxy_env(admin_session, monkeypatch):
    """auth=local (admin bootstrapped) + proxy SSO enabled + trust bypassed
    (the real IP gate is unit-tested above; TestClient's peer isn't a real IP)
    + a fake AD directory that provisions a real local user row so sessions +
    RBAC work end-to-end."""
    _set_proxy_cfg()
    monkeypatch.setattr(proxy_sso, "client_is_trusted", lambda request: True)

    def fake_sync(username, *, ip=""):
        # Provision a real 'ad' user so complete_login/session/RBAC are real.
        conn = auth_db.conn()
        row = conn.execute(
            "SELECT id FROM users WHERE username=? AND source='ad'",
            (username,)).fetchone()
        if row:
            uid = row["id"]
        else:
            now = time.time()
            with db.tx(conn):
                cur = conn.execute(
                    "INSERT INTO users(username, display_name, source, "
                    "external_dn, enabled, is_admin_seed, created_at, "
                    "last_login_at) VALUES (?,?,'ad',?,1,0,?,?)",
                    (username, username, f"CN={username},DC=e", now, now))
                uid = cur.lastrowid
            from app.core import permissions, roles
            permissions.set_subject_roles("user", str(uid),
                                          [roles.get_default_role_id()])
        return {"user_id": uid, "username": username, "source": "ad",
                "dn": f"CN={username},DC=e"}

    monkeypatch.setattr("app.core.auth_ldap.sync_user_by_username", fake_sync)
    # Fresh client with NO session cookie (unauthenticated browser).
    return TestClient(app_main.app), fake_sync


def test_e2e_trusted_header_auto_logs_in(proxy_env):
    c, _ = proxy_env
    r = c.get("/", headers={"X-Remote-User": "EXAMPLE\\alice"},
              follow_redirects=False)
    assert r.status_code == 302
    assert sessions.COOKIE_NAME in r.cookies  # session issued
    # The issued cookie authorises a follow-up request.
    c.cookies.set(sessions.COOKIE_NAME, r.cookies[sessions.COOKIE_NAME])
    r2 = c.get("/", follow_redirects=False)
    assert r2.status_code == 200


def test_e2e_no_header_falls_back_to_login(proxy_env):
    c, _ = proxy_env
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")
    assert sessions.COOKIE_NAME not in r.cookies


def test_e2e_untrusted_does_not_login(admin_session, monkeypatch):
    # Do NOT bypass the trust gate: TestClient peer 'testclient' is untrusted.
    _set_proxy_cfg(trusted_proxies=["10.9.9.9"])
    called = []
    monkeypatch.setattr("app.core.auth_ldap.sync_user_by_username",
                        lambda u, *, ip="": called.append(u))
    c = TestClient(app_main.app)
    r = c.get("/", headers={"X-Remote-User": "EXAMPLE\\mallory"},
              follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")
    assert sessions.COOKIE_NAME not in r.cookies
    assert called == []  # never even attempted a directory lookup


def test_e2e_fallback_off_returns_401(proxy_env, monkeypatch):
    _set_proxy_cfg(fallback_login=False)
    c = TestClient(app_main.app)
    r = c.get("/", follow_redirects=False)  # no header
    assert r.status_code == 401


def test_e2e_login_page_reachable_breakglass(proxy_env):
    """jtdt-admin break-glass: /login must always render (proxy SSO never runs
    on the public /login path), even with a domain header present."""
    c, _ = proxy_env
    r = c.get("/login", headers={"X-Remote-User": "EXAMPLE\\alice"},
              follow_redirects=False)
    assert r.status_code == 200
    assert sessions.COOKIE_NAME not in r.cookies


def test_e2e_auditor_forced_through_2fa(proxy_env, monkeypatch):
    """An auditor arriving via proxy SSO must still be routed to /2fa-verify —
    proxy SSO cannot bypass forced TOTP."""
    c, _ = proxy_env

    def auditor_sync(username, *, ip=""):
        conn = auth_db.conn()
        now = time.time()
        with db.tx(conn):
            cur = conn.execute(
                "INSERT INTO users(username, display_name, source, external_dn,"
                " enabled, is_admin_seed, created_at, last_login_at, "
                "totp_required) VALUES (?,?,'ad',?,1,0,?,?,1)",
                (username, username, f"CN={username},DC=e", now, now))
            uid = cur.lastrowid
        from app.core import permissions
        permissions.set_subject_roles("user", str(uid), ["auditor"])
        return {"user_id": uid, "username": username, "dn": "CN=x"}

    monkeypatch.setattr("app.core.auth_ldap.sync_user_by_username", auditor_sync)
    r = c.get("/", headers={"X-Remote-User": "EXAMPLE\\audrey"},
              follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location") == "/2fa-verify"
    # No full session cookie — only the pending-2FA cookie.
    assert sessions.COOKIE_NAME not in r.cookies


def test_disabled_proxy_is_noop(admin_session):
    _set_proxy_cfg(enabled=False)
    c = TestClient(app_main.app)
    r = c.get("/", headers={"X-Remote-User": "EXAMPLE\\alice"},
              follow_redirects=False)
    # Falls straight to normal /login redirect; header ignored entirely.
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


# ---------------------- admin save endpoint guards -------------------------

def _set_ldap_configured():
    s = auth_settings.get()
    s["ldap"]["server_url"] = "ldaps://ad.example.local:636"
    s["ldap"]["service_dn"] = "CN=svc,DC=example,DC=local"
    auth_settings.save(s)


def test_proxy_save_disabled_ok(admin_session):
    c, _, _ = admin_session
    r = c.post("/admin/sso/proxy-save", json={
        "enabled": False, "header": "X-Remote-User",
        "fallback_login": True, "trusted_proxies": "127.0.0.1\n::1"})
    assert r.status_code == 200, r.text
    assert auth_settings.get()["proxy_sso"]["enabled"] is False


def test_proxy_save_enable_without_ldap_409(admin_session):
    c, _, _ = admin_session
    # ensure ldap NOT configured
    s = auth_settings.get()
    s["ldap"]["server_url"] = ""
    s["ldap"]["service_dn"] = ""
    auth_settings.save(s)
    r = c.post("/admin/sso/proxy-save", json={
        "enabled": True, "trusted_proxies": "127.0.0.1"})
    assert r.status_code == 409


def test_proxy_save_enable_empty_proxies_400(admin_session):
    c, _, _ = admin_session
    _set_ldap_configured()
    r = c.post("/admin/sso/proxy-save", json={
        "enabled": True, "trusted_proxies": ""})
    assert r.status_code == 400


def test_proxy_save_rejects_all_encompassing_proxy(admin_session):
    """trusted_proxies must reject 0.0.0.0/0, ::/0, wildcards and invalid IPs —
    an all-source list makes the header-spoofing defence meaningless."""
    c, _, _ = admin_session
    _set_ldap_configured()
    for bad in ["0.0.0.0/0", "::/0", "0.0.0.0", "*", "not-an-ip", "10.0.0.0/0"]:
        r = c.post("/admin/sso/proxy-save", json={
            "enabled": True, "trusted_proxies": bad})
        assert r.status_code == 400, f"{bad!r} should be rejected, got {r.status_code}"


def test_proxy_save_enable_ok_with_ldap_and_proxies(admin_session):
    c, _, _ = admin_session
    _set_ldap_configured()
    r = c.post("/admin/sso/proxy-save", json={
        "enabled": True, "header": "X-Remote-User",
        "fallback_login": False, "trusted_proxies": "10.0.0.5, 10.0.0.6"})
    assert r.status_code == 200, r.text
    cfg = auth_settings.get()["proxy_sso"]
    assert cfg["enabled"] is True
    assert cfg["fallback_login"] is False
    assert cfg["trusted_proxies"] == ["10.0.0.5", "10.0.0.6"]
