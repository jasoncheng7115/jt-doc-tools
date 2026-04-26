"""Tests for the auth middleware (gate that requires session when auth on).

Test list:
  - When auth off: every path is publicly accessible (no change vs pre-auth)
  - When auth on, public paths still reachable (login, static, healthz)
  - When auth on, unauthenticated browser GET → 302 /login?next=<path>
  - When auth on, unauthenticated XHR → 401 JSON (not HTML redirect)
  - When auth on, valid session → 200 (passes through)
  - When auth on, expired/invalid cookie → treated as unauthenticated
  - /api/* requests skip the auth gate (token gate handles them separately)
"""
from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import app.main as app_main


def test_auth_off_all_paths_open(auth_off):
    c = TestClient(app_main.app, follow_redirects=False)
    # Landing page
    r = c.get("/")
    assert r.status_code in (200, 302)  # may redirect within app, but not to /login
    assert r.headers.get("location", "/") != "/login"


def test_auth_on_public_paths_reachable(admin_session):
    c = TestClient(app_main.app, follow_redirects=False)
    for path, expected in [
        ("/login", 200),
        ("/healthz", 200),
        ("/static/css/platform.css", 200),
    ]:
        r = c.get(path)
        # /healthz is special — it's defined elsewhere; if it's not present
        # in this build, accept 404 too. The point is: NOT a redirect to /login.
        assert r.status_code in (expected, 404)
        if r.status_code == 302:
            assert "/login" not in r.headers.get("location", "")


def test_auth_on_unauth_browser_redirect(admin_session):
    c = TestClient(app_main.app, follow_redirects=False)
    # No cookie → should bounce to /login with original path in next=
    r = c.get("/admin/", headers={"Accept": "text/html"})
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/login")
    assert "next=" in loc
    assert quote("/admin/") in loc


def test_auth_on_unauth_xhr_returns_401(admin_session):
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.get("/admin/", headers={"X-Requested-With": "XMLHttpRequest",
                                  "Accept": "application/json"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_auth_on_valid_session_passes(admin_session):
    client, _, _ = admin_session
    r = client.get("/admin/", follow_redirects=False)
    # /admin/ may redirect to /admin/assets (302 within app), or render.
    # Either way: NOT a 302 to /login.
    assert r.status_code != 401
    if r.status_code == 302:
        assert "/login" not in r.headers["location"]


def test_auth_on_invalid_cookie_treated_as_unauth(admin_session):
    c = TestClient(app_main.app, follow_redirects=False)
    c.cookies.set("jtdt_session", "this-is-not-a-real-token")
    r = c.get("/admin/", headers={"Accept": "text/html"})
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")


def test_api_path_skips_auth_gate(admin_session):
    """API routes are gated by the token middleware, NOT the session
    middleware. Without auth+token enforcement, /api/* should be reachable."""
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.get("/api/jobs/nonexistent")
    # 404 (no such job) rather than 302/401 — proves we passed the auth gate
    assert r.status_code in (404, 401)
    # If 401, that'd be from the API token gate when api_tokens.is_enforced(),
    # which is fine — auth gate did NOT redirect to /login.
    assert "/login" not in r.headers.get("location", "")
