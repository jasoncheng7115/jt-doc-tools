"""ACL test for the login-gated shared-asset image endpoints (GitHub #28).

Background: 「資產管理」assets (logo / stamp / signature / watermark) are uploaded
by admins, but the *image files* are rendered by ordinary tool pages
(pdf-stamp / pdf-watermark / pdf-editor). Those previously pointed at
`/admin/assets/{id}/file|thumb`, which is admin-gated — so once auth was on, a
non-admin user (who can legitimately use the stamp / watermark tools) got 403
and saw blank pickers + an empty editor preview.

The new `/assets/{id}/file` and `/assets/{id}/thumb` endpoints are gated only by
require_login, so any authenticated user can *view* shared assets (but not
manage them — no list / upload / delete here). Auth OFF → everyone passes,
exactly as before.

Test list:
  - auth OFF: anyone can fetch /assets/{id}/file + /thumb            → 200
  - auth ON, non-admin logged in: can fetch /assets/{id}/file+/thumb → 200
  - auth ON, non-admin logged in: /admin/assets/{id}/file STILL 403  (unchanged)
  - auth ON, unauthenticated: /assets/{id}/file is NOT served        → 401/302
  - unknown asset id                                                 → 404
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as app_main


def _make_stamp_asset(stamp_png: bytes):
    """Create a real asset via the asset manager (writes to the isolated test
    data dir) and return its id."""
    from app.core.asset_manager import asset_manager
    asset = asset_manager.create_from_bytes("測試章", "stamp", stamp_png)
    return asset.id


def _non_admin_client(username: str = "userA") -> TestClient:
    """Create a non-admin local user (default-user role), issue a session, and
    return a TestClient carrying its cookie. Assumes auth=local is already
    enabled (call under the admin_session fixture)."""
    from app.core import user_manager, sessions
    uid = user_manager.create_local(username, username, "UserPass1234")
    token, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, token)
    return c


def test_auth_off_anyone_can_view_asset_images(auth_off, stamp_png):
    asset_id = _make_stamp_asset(stamp_png)
    c = TestClient(app_main.app)
    for suffix in ("file", "thumb"):
        r = c.get(f"/assets/{asset_id}/{suffix}")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/png"
        assert len(r.content) > 0


def test_auth_on_non_admin_can_view_asset_images(admin_session, stamp_png):
    asset_id = _make_stamp_asset(stamp_png)
    c = _non_admin_client()
    for suffix in ("file", "thumb"):
        r = c.get(f"/assets/{asset_id}/{suffix}", follow_redirects=False)
        assert r.status_code == 200, (
            f"non-admin should view shared asset {suffix}: got {r.status_code}")
        assert r.headers["content-type"] == "image/png"


def test_auth_on_non_admin_still_blocked_from_admin_asset_route(
    admin_session, stamp_png
):
    asset_id = _make_stamp_asset(stamp_png)
    c = _non_admin_client("userB")
    # The admin management route stays admin-only — this fix only adds a
    # parallel read-only path, it does not loosen the admin router.
    r = c.get(f"/admin/assets/{asset_id}/file", follow_redirects=False)
    assert r.status_code == 403, r.text


def test_auth_on_unauthenticated_not_served(admin_session, stamp_png):
    asset_id = _make_stamp_asset(stamp_png)
    c = TestClient(app_main.app)  # no session cookie
    # Browser GET → 302 to /login; XHR → 401. Either way, not 200.
    r = c.get(f"/assets/{asset_id}/file", headers={"Accept": "text/html"},
              follow_redirects=False)
    assert r.status_code in (302, 401), r.status_code
    if r.status_code == 302:
        assert "/login" in r.headers.get("location", "")


def test_unknown_asset_id_404(admin_session):
    c = _non_admin_client("userC")
    r = c.get("/assets/deadbeefdeadbeefdeadbeefdeadbeef/file",
              follow_redirects=False)
    assert r.status_code == 404, r.text
