"""Regression test for the Starlette BADHOST path-poisoning bypass
(CVE-2026-48710, GHSA-86qp-5c8j-p5mr).

Starlette rebuilds `request.url.path` from the Host header. A crafted
`Host: h/login?x` makes `request.url.path` == "/login" (a public path) while
the ASGI `scope["path"]` stays the real "/tools/pdf-fill/". Our security
middlewares must decide on the raw scope path, NOT request.url.path — otherwise
an attacker bypasses the per-tool permission gate by poisoning Host.

We assert that a logged-in user WITHOUT a tool's permission is blocked on that
tool's page even when sending a poisoning Host header (default-user lacks
pdf-fill per the role model).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as app_main


# Host values that, under the unpatched code, poison request.url.path into a
# public path (verified against starlette 0.52.1).
POISON_HOSTS = ["h/login?x", "x/healthz", "a#/login"]


def _default_user_client(username: str) -> TestClient:
    from app.core import user_manager, sessions, permissions
    uid = user_manager.create_local(username, username, "UserPass1234")  # default-user
    assert not permissions.user_can_use_tool(uid, "pdf-fill"), \
        "test premise broken: default-user should NOT have pdf-fill"
    token, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, token)
    return c


def test_unpermitted_tool_blocked_normally(admin_session):
    c = _default_user_client("bh_base")
    r = c.get("/tools/pdf-fill/", follow_redirects=False)
    assert r.status_code == 403, r.status_code  # baseline: gated without poisoning


@pytest.mark.parametrize("host", POISON_HOSTS)
def test_poison_host_cannot_bypass_tool_gate(admin_session, host):
    c = _default_user_client(f"bh_{abs(hash(host)) % 10000}")
    r = c.get("/tools/pdf-fill/", headers={"Host": host}, follow_redirects=False)
    # Must STILL be forbidden — the gate uses scope["path"], not the poisoned
    # url.path. A 200 here would mean the permission check was bypassed.
    assert r.status_code == 403, (
        f"BADHOST bypass: Host={host!r} let an unpermitted user reach the tool "
        f"(status {r.status_code})")


@pytest.mark.parametrize("host", POISON_HOSTS)
def test_poison_host_cannot_bypass_admin_gate(admin_session, host):
    # Unauthenticated request to an admin page with a poisoning Host must still
    # be bounced to /login (not served).
    c = TestClient(app_main.app)
    r = c.get("/admin/users", headers={"Host": host, "Accept": "text/html"},
              follow_redirects=False)
    assert r.status_code in (302, 401), r.status_code
    if r.status_code == 302:
        assert "/login" in r.headers.get("location", "")
