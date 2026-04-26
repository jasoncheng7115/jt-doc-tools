"""End-to-end tests for the auth HTTP layer.

Test list:
  - GET /login when auth off → redirect to /
  - GET /login when auth on → 200 HTML form
  - POST /login wrong creds → 200 with error displayed
  - POST /login right creds → 302 set cookie + redirect to next
  - POST /login next= must be sanitised (no open-redirect)
  - GET /setup-admin when auth on → redirect /login
  - GET /setup-admin when auth off → 200 form
  - POST /setup-admin happy path → bootstraps + auto-login + redirect /admin/
  - POST /setup-admin bad pw → 200 with error, no bootstrap
  - POST /logout → 302 /login + cookie cleared
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as app_main


def test_login_get_redirects_when_off(auth_off):
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.get("/login")
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_login_get_form_when_on(admin_session):
    client, _, _ = admin_session
    # admin_session client already logged in; new client without cookie
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.get("/login")
    assert r.status_code == 200
    assert "登入" in r.text or "name=\"username\"" in r.text


def test_login_post_wrong_creds(admin_session):
    _, username, _ = admin_session
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.post("/login", data={
        "username": username, "password": "wrongPass1234",
        "next": "/",
    })
    assert r.status_code == 200
    assert "錯誤" in r.text or "error" in r.text.lower()


def test_login_post_right_creds_sets_cookie(admin_session):
    _, username, password = admin_session
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.post("/login", data={
        "username": username, "password": password, "next": "/",
    })
    assert r.status_code == 302
    assert "jtdt_session" in r.cookies or "jtdt_session" in (
        r.headers.get("set-cookie", "")
    )


@pytest.mark.parametrize("evil_next", [
    "https://evil.com",
    "//evil.com/x",
    "javascript:alert(1)",
    "ftp://internal/x",
    "",
    "no-leading-slash",
])
def test_login_open_redirect_blocked(admin_session, evil_next):
    _, username, password = admin_session
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.post("/login", data={
        "username": username, "password": password, "next": evil_next,
    })
    assert r.status_code == 302
    loc = r.headers["location"]
    # Must redirect to a same-origin path
    assert loc.startswith("/")
    assert "evil.com" not in loc
    assert not loc.startswith("//")
    assert not loc.startswith("javascript:")


def test_setup_admin_get_when_on(admin_session):
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.get("/setup-admin")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_setup_admin_get_when_off(auth_off):
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.get("/setup-admin")
    assert r.status_code == 200
    assert "啟用認證" in r.text or "管理員" in r.text


def test_setup_admin_post_happy(auth_off):
    from app.core import auth_settings
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.post("/setup-admin", data={
        "username": "newadmin",
        "display_name": "",
        "password": "BootPass1234",
        "password_confirm": "BootPass1234",
    })
    assert r.status_code == 302
    assert r.headers["location"] == "/admin/"
    # Check cookie set + auth flipped on
    assert auth_settings.is_enabled()
    cookies = r.headers.get("set-cookie", "")
    assert "jtdt_session" in cookies


def test_setup_admin_post_bad_pw(auth_off):
    from app.core import auth_settings
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.post("/setup-admin", data={
        "username": "x",
        "display_name": "",
        "password": "short",
        "password_confirm": "short",
    })
    assert r.status_code == 200
    # NOT bootstrapped
    assert not auth_settings.is_enabled()


def test_setup_admin_blocked_when_already_on(admin_session):
    """Critical: once auth is on, /setup-admin must NOT let an attacker
    create another admin. We redirect to /login instead."""
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.post("/setup-admin", data={
        "username": "evil-admin",
        "display_name": "",
        "password": "EvilPass1234",
        "password_confirm": "EvilPass1234",
    })
    assert r.status_code == 302
    assert r.headers["location"] == "/login"
    # Verify no new user was created
    from app.core import auth_db
    n = auth_db.conn().execute(
        "SELECT count(*) FROM users WHERE username='evil-admin'"
    ).fetchone()[0]
    assert n == 0


def test_logout(admin_session):
    client, _, _ = admin_session
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"
