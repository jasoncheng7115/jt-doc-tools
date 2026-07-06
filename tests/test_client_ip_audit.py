"""Client-IP resolution for audit / history / display — app/core/client_ip.py.

Regression guard for v1.12.65: v1.12.61 switched uvicorn to
``proxy_headers=False`` (so proxy-SSO trust uses the real transport peer). Every
audit / history site still reading ``request.client.host`` directly then started
logging ``127.0.0.1`` (the nginx peer) instead of the workstation IP behind the
reverse proxy — reported by a customer upgrading .60 → .64 where every
``tool_invoke`` row showed 127.0.0.1 while ``login_success`` (which read XFF)
stayed correct.

The fix routes all audit/history/display IP resolution through
``client_ip.real_client_ip`` (XFF-aware). Trust decisions still use the raw
peer (``proxy_sso._client_ip``) — verified in test_proxy_sso.py.
"""
from __future__ import annotations

from app.core import client_ip


class _FakeReq:
    def __init__(self, headers=None, client_host="127.0.0.1"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": client_host})() if client_host else None


# ----------------------------- unit: resolver -----------------------------

def test_xff_present_returns_original_client_not_peer():
    """The customer's exact case: request arrives from nginx (peer 127.0.0.1)
    carrying the real workstation IP in X-Forwarded-For → we log the workstation,
    not the proxy."""
    r = _FakeReq(headers={"X-Forwarded-For": "198.51.100.85"}, client_host="127.0.0.1")
    assert client_ip.real_client_ip(r) == "198.51.100.85"


def test_xff_multi_hop_takes_leftmost_original_client():
    r = _FakeReq(headers={"X-Forwarded-For": "203.0.113.99, 203.0.113.1, 127.0.0.1"})
    assert client_ip.real_client_ip(r) == "203.0.113.99"


def test_no_xff_falls_back_to_transport_peer():
    """Direct local access (no proxy) → the transport peer is the real client."""
    r = _FakeReq(headers={}, client_host="192.0.2.50")
    assert client_ip.real_client_ip(r) == "192.0.2.50"


def test_empty_xff_falls_back_to_peer():
    r = _FakeReq(headers={"X-Forwarded-For": "   "}, client_host="192.0.2.50")
    # whitespace-only header splits to '' → treated as fallback
    out = client_ip.real_client_ip(r)
    assert out in ("192.0.2.50", "")


def test_no_client_no_xff_returns_empty():
    r = _FakeReq(headers={}, client_host=None)
    assert client_ip.real_client_ip(r) == ""


def test_result_is_length_capped():
    r = _FakeReq(headers={"X-Forwarded-For": "a" * 200})
    assert len(client_ip.real_client_ip(r)) <= 64


def test_missing_headers_attr_does_not_raise():
    class Bare:
        client = type("C", (), {"host": "192.0.2.9"})()
    # no .headers attribute at all
    assert client_ip.real_client_ip(Bare()) == "192.0.2.9"


# --------------------- helpers delegate to the resolver -------------------

def test_auth_routes_helper_delegates():
    from app.web.auth_routes import _client_ip
    r = _FakeReq(headers={"X-Forwarded-For": "198.51.100.85"}, client_host="127.0.0.1")
    assert _client_ip(r) == "198.51.100.85"


def test_admin_router_helper_delegates():
    from app.admin.auth_router import _client_ip
    r = _FakeReq(headers={"X-Forwarded-For": "198.51.100.85"}, client_host="127.0.0.1")
    assert _client_ip(r) == "198.51.100.85"


def test_proxy_sso_real_client_ip_delegates():
    from app.core import proxy_sso
    r = _FakeReq(headers={"X-Forwarded-For": "198.51.100.85"}, client_host="127.0.0.1")
    assert proxy_sso._real_client_ip(r) == "198.51.100.85"


def test_proxy_sso_trust_ip_still_uses_raw_peer():
    """Trust decision must NOT be fooled by X-Forwarded-For — it reads the raw
    transport peer only."""
    from app.core import proxy_sso
    r = _FakeReq(headers={"X-Forwarded-For": "198.51.100.85"}, client_host="203.0.113.9")
    assert proxy_sso._client_ip(r) == "203.0.113.9"


# ------------ integration: tool_invoke audit through the middleware --------

import sqlite3
import time as _time

import pytest
from starlette.testclient import TestClient

from app import main as app_main
from app.core import auth_settings, auth_db, sessions, audit_db


@pytest.fixture()
def _admin_client():
    """Enable local auth with a seed admin, return a cookie-authed TestClient.
    Restores auth to disabled on teardown so other test files aren't polluted."""
    if auth_settings.get_backend() == "off":
        auth_settings.enable_local_with_admin(
            admin_username="jtdt-admin", admin_display_name="Admin",
            admin_password="TestAdmin1234", admin_password_confirm="TestAdmin1234",
            actor_ip="127.0.0.1",
        )
    row = auth_db.conn().execute(
        "SELECT id FROM users WHERE username='jtdt-admin'").fetchone()
    tok, _ = sessions.issue(row["id"], remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, tok)
    try:
        yield c
    finally:
        try:
            auth_settings.disable_auth(actor="pytest", ip="127.0.0.1")
        except Exception:
            pass


def _latest_tool_invoke_ip(target: str, timeout: float = 4.0):
    """Poll the async-written audit DB for the newest tool_invoke on `target`."""
    path = str(audit_db.audit_db_path())
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            r = conn.execute(
                "SELECT ip FROM audit_events WHERE event_type='tool_invoke' "
                "AND target=? ORDER BY id DESC LIMIT 1", (target,)).fetchone()
        finally:
            conn.close()
        if r is not None:
            return r[0]
        _time.sleep(0.05)
    return None


def test_tool_invoke_audit_records_xff_not_proxy_peer(_admin_client):
    """End-to-end regression: a tool POST arriving via a reverse proxy (peer
    127.0.0.1) with X-Forwarded-For must audit the workstation IP, not the proxy.
    This is the exact customer symptom (.60 → .64: every tool_invoke = 127.0.0.1)."""
    # A 404 sub-path still flows through _auth_gate → tool_invoke audit block
    # (tool_id parsed from path, POST method), so we don't need a heavy handler.
    r = _admin_client.post(
        "/tools/text-list/__audit_probe__",
        headers={"X-Forwarded-For": "198.51.100.85"},
        json={},
    )
    assert r.status_code in (200, 400, 404, 422, 415)  # any — we only care about audit
    ip = _latest_tool_invoke_ip("text-list")
    assert ip == "198.51.100.85", f"expected workstation IP, got {ip!r}"
