"""Tests for v1.1.5 - v1.1.7 visibility / identity changes.

Test list (mapped to behavior, not implementation):

  whoami endpoint (v1.1.7)
    1. auth OFF                         → 200 JSON, auth_enabled=False, is_admin=True
    2. auth ON, no session              → 302 /login (middleware)
    3. auth ON, admin session           → is_admin=True, tools_all=True
    4. auth ON, non-admin (clerk role)  → is_admin=False, tools list reflects role

  nav_settings sidebar filter (v1.1.5)
    5. auth OFF                         → items without requires_auth only
    6. auth ON, no user                 → []
    7. auth ON, non-admin               → []   (all settings are admin-only)
    8. auth ON, admin                   → full list (incl. requires_auth ones)

  nav_tool_groups sidebar filter (v1.1.5)
    9. auth OFF                         → all groups, all tools
    10. auth ON, no user                → []
    11. auth ON, admin                  → all groups
    12. auth ON, non-admin (clerk)      → only tools the role grants

  home page tile filtering (v1.1.5)
    13. auth ON, non-admin (clerk)      → /  page only contains the role's tools
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as app_main


# ---------------------------------------------------------------- whoami (1)
def test_whoami_auth_off_returns_anonymous(client, auth_off):
    r = client.get("/whoami")
    assert r.status_code == 200
    j = r.json()
    assert j["auth_enabled"] is False
    assert j["source"] == "off"
    assert j["is_admin"] is True
    assert j["tools_all"] is True


# ---------------------------------------------------------------- whoami (2)
def test_whoami_auth_on_no_session_redirects(auth_off):
    """Auth gate intercepts unauthenticated browser nav with a 302."""
    from app.core import auth_settings
    auth_settings.enable_local_with_admin(
        admin_username="jtdt-admin", admin_display_name="A",
        admin_password="TestAdmin1234", admin_password_confirm="TestAdmin1234",
        actor_ip="127.0.0.1",
    )
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.get("/whoami")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")


# ---------------------------------------------------------------- whoami (3)
def test_whoami_admin_session_returns_admin(admin_session):
    c, _, _ = admin_session
    r = c.get("/whoami")
    assert r.status_code == 200
    j = r.json()
    assert j["auth_enabled"] is True
    assert j["username"] == "jtdt-admin"
    assert j["is_admin"] is True
    assert j["tools_all"] is True
    # admin role badge present
    assert any(role["id"] == "admin" for role in j["roles"])


# ---------------------------------------------------------------- whoami (4)
def test_whoami_non_admin_returns_role_tools(admin_session):
    """Create a clerk user, log in as them, expect tools_all=False and a
    tool list that matches the clerk role definition."""
    from app.core import auth_db, sessions, user_manager, permissions, db
    c_admin, _, _ = admin_session

    # Create non-admin user assigned to the clerk role.
    uid = user_manager.create_local(
        username="alice", display_name="Alice",
        password="ClerkUser1234", roles=["clerk"],
    )
    permissions.invalidate_cache()

    token, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, token)

    r = c.get("/whoami")
    assert r.status_code == 200
    j = r.json()
    assert j["username"] == "alice"
    assert j["is_admin"] is False
    assert j["tools_all"] is False
    assert any(role["id"] == "clerk" for role in j["roles"])
    # Sanity: clerk role grants pdf-merge but NOT pdf-fill
    tool_ids = {t["id"] for t in j["tools"]}
    assert "pdf-merge" in tool_ids
    assert "pdf-fill" not in tool_ids


# ---------------------------------------------------- nav_settings (5)
def test_nav_settings_auth_off_hides_requires_auth_items(auth_off):
    """Auth OFF == single-machine mode: hide requires_auth items."""
    items = app_main._nav_settings_visible(None)
    assert items, "should still show non-auth settings (assets, fonts, …)"
    assert all(not x.get("requires_auth") for x in items)


# ---------------------------------------------------- nav_settings (6, 7)
def test_nav_settings_auth_on_hides_all_for_non_admin(auth_off):
    """Settings pages are 100% admin-only — non-admin sees nothing."""
    from app.core import auth_settings
    auth_settings.enable_local_with_admin(
        admin_username="jtdt-admin", admin_display_name="A",
        admin_password="TestAdmin1234", admin_password_confirm="TestAdmin1234",
        actor_ip="127.0.0.1",
    )

    class _State: user = None
    class _Req: state = _State()
    # No user attached at all
    assert app_main._nav_settings_visible(_Req()) == []

    # User attached but not admin
    from app.core import user_manager, permissions
    uid = user_manager.create_local(
        username="bob", display_name="Bob",
        password="BobPwd123456", roles=["clerk"],
    )
    permissions.invalidate_cache()
    _State.user = {"user_id": uid, "username": "bob"}
    assert app_main._nav_settings_visible(_Req()) == []


# ---------------------------------------------------- nav_settings (8)
def test_nav_settings_auth_on_admin_sees_full_list(admin_session):
    c, _, _ = admin_session
    # The HTML carries the sidebar; check that requires_auth items appear
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    # admin should see /admin/users (requires_auth=True), /admin/audit, etc.
    assert "/admin/users" in body
    assert "/admin/audit" in body
    assert "/admin/log-forward" in body


# ---------------------------------------------------- nav_tool_groups (9)
def test_nav_tool_groups_auth_off_returns_all(auth_off):
    groups = app_main._nav_tool_groups_visible(None)
    assert groups
    total = sum(len(g["tools"]) for g in groups)
    assert total >= 5


# ---------------------------------------------------- nav_tool_groups (10)
def test_nav_tool_groups_auth_on_no_user_empty(auth_off):
    from app.core import auth_settings
    auth_settings.enable_local_with_admin(
        admin_username="jtdt-admin", admin_display_name="A",
        admin_password="TestAdmin1234", admin_password_confirm="TestAdmin1234",
        actor_ip="127.0.0.1",
    )
    class _State: user = None
    class _Req: state = _State()
    assert app_main._nav_tool_groups_visible(_Req()) == []


# ---------------------------------------------------- nav_tool_groups (11)
def test_nav_tool_groups_admin_sees_all(admin_session):
    c, _, _ = admin_session
    # Admin role short-circuits to ALL tools — sidebar should include
    # at least one well-known tool from each group.
    r = c.get("/")
    body = r.text
    assert "/tools/pdf-merge/" in body
    assert "/tools/pdf-fill/" in body
    assert "/tools/doc-deident/" in body


# ---------------------------------------------------- nav_tool_groups (12)
def test_nav_tool_groups_clerk_filtered(admin_session):
    """Clerk role excludes pdf-fill / pdf-stamp — sidebar should reflect that."""
    from app.core import sessions, user_manager, permissions
    uid = user_manager.create_local(
        username="carol", display_name="Carol",
        password="CarolPwd1234", roles=["clerk"],
    )
    permissions.invalidate_cache()
    token, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, token)

    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert "/tools/pdf-merge/" in body       # clerk has this
    assert "/tools/pdf-fill/" not in body    # clerk doesn't


# ---------------------------------------------------- home page (13)
def test_home_page_tile_count_matches_perms(admin_session):
    """Home page tile list mirrors sidebar filter — clerk sees fewer tiles
    than admin."""
    from app.core import sessions, user_manager, permissions
    c_admin, _, _ = admin_session
    admin_html = c_admin.get("/").text
    admin_tile_count = admin_html.count("tool-icon")
    # ... and a non-admin viewer
    uid = user_manager.create_local(
        username="dave", display_name="Dave",
        password="DavePwd123456", roles=["clerk"],
    )
    permissions.invalidate_cache()
    token, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, token)
    user_html = c.get("/").text
    user_tile_count = user_html.count("tool-icon")
    assert user_tile_count > 0
    assert user_tile_count < admin_tile_count, (
        f"non-admin saw {user_tile_count} tiles, admin saw {admin_tile_count}"
    )
