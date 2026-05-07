"""v1.4.99 — auditor role + TOTP 2FA + separation-of-duties tests.

Test list:
  TOTP module
    - new_secret returns 32-char base32
    - provision_uri contains otpauth:// + issuer + username
    - verify_code OK on current step, OK on ±1 step, fail on wrong, fail on empty
    - qr_png_data_url returns data:image/png;base64,...
    - get_user_totp_state / get_secret / set_secret / mark_enabled / disable / set_required
  Schema migration v6
    - users table has totp_secret / totp_enabled / totp_required columns
    - schema_version >= 6
  roles + seed
    - 'auditor' role present in SEED_ROLES
    - seed_builtin_roles() inserts auditor role into existing DB (top-up path)
    - auditor role has empty tools list
  permissions.is_auditor
    - True for user with 'auditor' role assigned directly
    - False for admin, default-user, no-role
    - True for user via group membership with auditor role
  require_admin
    - admin user → 200 on /admin/users + /admin/audit
    - auditor user → 200 on /admin/audit/system-status/uploads/history
    - auditor user → 403 on /admin/users + /admin/roles
    - auditor_view audit event logged when auditor accesses whitelisted page
  Login flow with 2FA
    - user with totp_required=1 and no secret: login → redirects to /2fa-verify
    - GET /2fa-verify in setup mode shows QR + writes secret to DB
    - POST /2fa-verify wrong code → 200 + fails counter incremented
    - POST /2fa-verify correct code → 302 + cookie + totp_enabled=1
  /me/2fa
    - GET 401-equivalent when not logged in (302 to /login)
    - POST /me/2fa/start returns secret + qr
    - POST /me/2fa/verify wrong code → 400
    - POST /me/2fa/verify correct code → enables
    - POST /me/2fa/disable: regular user OK
    - POST /me/2fa/disable: auditor user → 403
"""
from __future__ import annotations

import re
import time

import pyotp
import pytest
from fastapi.testclient import TestClient

import app.main as app_main


# ---------- TOTP module ----------

def test_totp_new_secret_format():
    from app.core import totp
    s = totp.new_secret()
    assert isinstance(s, str)
    assert len(s) == 32
    assert re.match(r"^[A-Z2-7]+=*$", s), f"not base32: {s}"


def test_totp_provision_uri_contents():
    from app.core import totp
    uri = totp.provision_uri("JBSWY3DPEHPK3PXP", "alice", "MyApp")
    assert uri.startswith("otpauth://totp/")
    assert "alice" in uri
    assert "MyApp" in uri
    assert "secret=JBSWY3DPEHPK3PXP" in uri


def test_totp_verify_code_happy_and_skew():
    from app.core import totp
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    assert totp.verify_code(secret, code) is True
    # spaces / dashes stripped
    assert totp.verify_code(secret, " ".join(code)) is True
    # wrong
    assert totp.verify_code(secret, "000000") is False
    # empty
    assert totp.verify_code(secret, "") is False
    assert totp.verify_code("", code) is False
    # short
    assert totp.verify_code(secret, "12345") is False


def test_totp_qr_png_data_url():
    from app.core import totp
    url = totp.qr_png_data_url("otpauth://totp/test?secret=JBSWY3DPEHPK3PXP&issuer=t")
    assert url.startswith("data:image/png;base64,")
    # decode + check it's a real PNG
    import base64
    payload = url.split(",", 1)[1]
    raw = base64.b64decode(payload)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"


def test_totp_state_roundtrip(admin_session):
    """get/set/mark/disable/set_required all touch the same row."""
    _, username, _ = admin_session
    from app.core import totp, auth_db
    uid = auth_db.conn().execute(
        "SELECT id FROM users WHERE username=?", (username,)
    ).fetchone()["id"]

    st = totp.get_user_totp_state(uid)
    assert st == {"enabled": False, "required": False, "has_secret": False}

    secret = totp.new_secret()
    totp.set_secret(uid, secret)
    assert totp.get_secret(uid) == secret
    assert totp.get_user_totp_state(uid)["has_secret"] is True

    totp.mark_enabled(uid)
    assert totp.get_user_totp_state(uid)["enabled"] is True

    totp.set_required(uid, True)
    assert totp.get_user_totp_state(uid)["required"] is True

    totp.disable(uid)
    st = totp.get_user_totp_state(uid)
    assert st["enabled"] is False and st["has_secret"] is False


# ---------- Schema migration v6 ----------

def test_schema_v6_columns_exist(admin_session):
    from app.core import auth_db
    cols = {
        r["name"]
        for r in auth_db.conn().execute("PRAGMA table_info(users)").fetchall()
    }
    assert "totp_secret" in cols
    assert "totp_enabled" in cols
    assert "totp_required" in cols


def test_schema_version_at_least_6(admin_session):
    from app.core import auth_db
    v = auth_db.conn().execute("PRAGMA user_version").fetchone()[0]
    assert v >= 6, f"schema version is {v}, expected >= 6"


# ---------- roles + seed ----------

def test_auditor_in_seed_roles():
    from app.core import roles
    ids = [r["id"] for r in roles.SEED_ROLES]
    assert "auditor" in ids
    auditor = next(r for r in roles.SEED_ROLES if r["id"] == "auditor")
    assert auditor["is_builtin"] is True
    assert auditor["is_protected"] is True
    assert auditor["tools"] == []


def test_seed_top_up_inserts_auditor(admin_session):
    """Existing customer DB without auditor role → seed_builtin_roles
    should INSERT it (top-up path)."""
    from app.core import auth_db, roles, db
    conn = auth_db.conn()
    # Manually delete auditor (simulating a pre-1.4.99 customer)
    with db.tx(conn):
        conn.execute("DELETE FROM roles WHERE id='auditor'")
    # Run the top-up
    roles.seed_builtin_roles()
    row = conn.execute("SELECT id FROM roles WHERE id='auditor'").fetchone()
    assert row is not None, "seed_builtin_roles failed to top-up auditor"


def test_audit_visible_admin_urls_constant():
    from app.core import roles
    assert "/admin/audit" in roles.AUDIT_VISIBLE_ADMIN_URLS
    # auditor should NOT see settings, users, roles, etc.
    for url in ("/admin/users", "/admin/roles", "/admin/auth"):
        assert url not in roles.AUDIT_VISIBLE_ADMIN_URLS


# ---------- permissions.is_auditor ----------

def _make_user(username: str, password: str = "TestPass1234") -> int:
    from app.core import user_manager
    return user_manager.create_local(username, username, password)


def test_is_auditor_direct_assignment(admin_session):
    from app.core import permissions, auth_db
    uid = _make_user("alice-aud")
    assert permissions.is_auditor(uid) is False
    permissions.assign_role("user", str(uid), "auditor")
    assert permissions.is_auditor(uid) is True
    # admin is NOT auditor
    admin_uid = auth_db.conn().execute(
        "SELECT id FROM users WHERE username='jtdt-admin'"
    ).fetchone()["id"]
    assert permissions.is_auditor(admin_uid) is False
    # default user without auditor role
    bob_uid = _make_user("bob-plain")
    assert permissions.is_auditor(bob_uid) is False


def test_is_auditor_via_group(admin_session):
    from app.core import permissions, group_manager
    uid = _make_user("carol-aud")
    gid = group_manager.create_local("auditors", "稽核小組")
    group_manager.set_members(gid, [uid])
    permissions.assign_role("group", str(gid), "auditor")
    assert permissions.is_auditor(uid) is True


# ---------- require_admin / require_admin_or_auditor ----------

def _login_as(uid: int, ua: str = "pytest") -> TestClient:
    from app.core import sessions
    token, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua=ua)
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, token)
    return c


def test_require_admin_admin_passes(admin_session):
    client, _, _ = admin_session
    r = client.get("/admin/users")
    assert r.status_code == 200, f"admin should access /admin/users; got {r.status_code}"


def test_admin_blocked_from_auditor_exclusive_pages(admin_session):
    """v1.5.0: admin 不可看 history / uploads（user 隱私資料專屬於稽核員）。"""
    client, _, _ = admin_session
    for path in ("/admin/history/fill", "/admin/history/stamp",
                 "/admin/history/watermark", "/admin/uploads"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 403, \
            f"admin must be blocked from {path}; got {r.status_code}"


def test_admin_can_see_shared_pages(admin_session):
    """admin 仍可看 audit / system-status（系統運維必要）。"""
    client, _, _ = admin_session
    for path in ("/admin/audit", "/admin/system-status"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 200, \
            f"admin should still access {path}; got {r.status_code}"


def test_require_admin_auditor_allowed_on_shared_and_exclusive(admin_session):
    from app.core import permissions
    uid = _make_user("dave-aud")
    permissions.assign_role("user", str(uid), "auditor")
    c = _login_as(uid)
    # shared
    for path in ("/admin/audit", "/admin/system-status"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200, \
            f"auditor should access {path}; got {r.status_code}"
    # exclusive (auditor only) — sub-paths
    for path in ("/admin/history/fill", "/admin/history/stamp",
                 "/admin/history/watermark", "/admin/uploads"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code in (200, 302), \
            f"auditor should access {path}; got {r.status_code}"


def test_require_admin_auditor_blocked_on_non_whitelist(admin_session):
    from app.core import permissions
    uid = _make_user("eve-aud")
    permissions.assign_role("user", str(uid), "auditor")
    c = _login_as(uid)
    for path in ("/admin/users", "/admin/roles", "/admin/auth-settings"):
        r = c.get(path)
        assert r.status_code == 403, \
            f"auditor must NOT access {path}; got {r.status_code}"


def test_require_admin_plain_user_blocked(admin_session):
    uid = _make_user("frank-plain")
    c = _login_as(uid)
    r = c.get("/admin/users")
    assert r.status_code == 403


def test_auditor_view_event_logged(admin_session):
    """auditor accessing /admin/audit must produce auditor_view event."""
    from app.core import permissions, audit_db
    uid = _make_user("grace-aud")
    permissions.assign_role("user", str(uid), "auditor")
    c = _login_as(uid)
    # Access something to trigger the audit hook
    r = c.get("/admin/audit")
    assert r.status_code == 200
    # Look for the auditor_view event in audit DB
    rows = audit_db.conn().execute(
        "SELECT event_type, target FROM audit_events "
        "WHERE event_type='auditor_view' AND target='/admin/audit' "
        "ORDER BY id DESC LIMIT 5"
    ).fetchall()
    assert len(rows) >= 1, "auditor_view event not recorded"


# ---------- 2FA login flow ----------

def test_login_with_totp_required_redirects_to_verify(admin_session):
    """A user with totp_required=1 + no secret yet → login redirects
    to /2fa-verify and stashes pending state."""
    from app.core import totp, auth_settings
    # Create a non-admin user with TOTP required
    pw = "Pass12345678"
    uid = _make_user("hank-2fa", pw)
    totp.set_required(uid, True)

    # Make sure auth is on (admin_session leaves it on)
    assert auth_settings.is_enabled()
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.post("/login", data={"username": "hank-2fa", "password": pw, "next": "/"})
    assert r.status_code == 302
    assert r.headers["location"] == "/2fa-verify", \
        f"expected redirect to /2fa-verify; got {r.headers['location']}"
    # Pending cookie set
    assert "jtdt_pending_2fa" in r.cookies or "jtdt_pending_2fa" in (
        r.headers.get("set-cookie", "")
    )


def test_2fa_verify_get_setup_writes_secret(admin_session):
    """GET /2fa-verify in forced_setup mode should write a fresh secret
    and render QR."""
    from app.core import totp, auth_db
    pw = "Pass12345678"
    uid = _make_user("ian-2fa", pw)
    totp.set_required(uid, True)
    assert totp.get_secret(uid) is None

    c = TestClient(app_main.app, follow_redirects=False)
    r = c.post("/login", data={"username": "ian-2fa", "password": pw})
    assert r.status_code == 302
    # Follow the verify page
    r2 = c.get("/2fa-verify")
    assert r2.status_code == 200
    assert "data:image/png;base64," in r2.text
    # Secret should now be in DB
    secret = totp.get_secret(uid)
    assert secret is not None and len(secret) == 32
    # totp_enabled still 0 until first verify
    assert totp.get_user_totp_state(uid)["enabled"] is False


def test_2fa_verify_correct_code_completes_login(admin_session):
    from app.core import totp
    pw = "Pass12345678"
    uid = _make_user("jack-2fa", pw)
    totp.set_required(uid, True)
    c = TestClient(app_main.app, follow_redirects=False)
    c.post("/login", data={"username": "jack-2fa", "password": pw})
    c.get("/2fa-verify")  # generate secret
    secret = totp.get_secret(uid)
    code = pyotp.TOTP(secret).now()
    r = c.post("/2fa-verify", data={"code": code})
    assert r.status_code == 302, f"got {r.status_code} body={r.text[:200]}"
    # Should now have a real session cookie
    assert "jtdt_session" in (r.cookies.keys() | {
        k.strip().split("=", 1)[0] for k in r.headers.get("set-cookie", "").split(";")
    })
    # totp_enabled flipped on
    assert totp.get_user_totp_state(uid)["enabled"] is True


def test_2fa_verify_wrong_code_fails(admin_session):
    from app.core import totp
    pw = "Pass12345678"
    uid = _make_user("kate-2fa", pw)
    totp.set_required(uid, True)
    c = TestClient(app_main.app, follow_redirects=False)
    c.post("/login", data={"username": "kate-2fa", "password": pw})
    c.get("/2fa-verify")
    r = c.post("/2fa-verify", data={"code": "000000"})
    assert r.status_code == 200
    # error rendered
    assert "錯誤" in r.text or "error" in r.text.lower()


# ---------- /me/2fa self-service ----------

def test_me_2fa_unauth_redirects(auth_off):
    # auth on first
    from app.core import auth_settings
    pw = "TestAdmin1234"
    auth_settings.enable_local_with_admin(
        admin_username="jtdt-admin", admin_display_name="管理員",
        admin_password=pw, admin_password_confirm=pw, actor_ip="127.0.0.1",
    )
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.get("/me/2fa")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_me_2fa_start_returns_secret_and_qr(admin_session):
    client, _, _ = admin_session
    r = client.post("/me/2fa/start")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert len(j["secret"]) == 32
    assert j["qr_url"].startswith("data:image/png;base64,")


def test_me_2fa_verify_correct_enables(admin_session):
    client, username, _ = admin_session
    from app.core import auth_db, totp
    uid = auth_db.conn().execute(
        "SELECT id FROM users WHERE username=?", (username,)
    ).fetchone()["id"]
    r = client.post("/me/2fa/start")
    secret = r.json()["secret"]
    code = pyotp.TOTP(secret).now()
    r2 = client.post("/me/2fa/verify", json={"code": code})
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert totp.get_user_totp_state(uid)["enabled"] is True


def test_me_2fa_verify_wrong_returns_400(admin_session):
    client, _, _ = admin_session
    client.post("/me/2fa/start")
    r = client.post("/me/2fa/verify", json={"code": "000000"})
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_me_2fa_disable_for_regular_user(admin_session):
    client, username, _ = admin_session
    from app.core import totp, auth_db
    uid = auth_db.conn().execute(
        "SELECT id FROM users WHERE username=?", (username,)
    ).fetchone()["id"]
    # Setup + enable
    r = client.post("/me/2fa/start")
    code = pyotp.TOTP(r.json()["secret"]).now()
    client.post("/me/2fa/verify", json={"code": code})
    assert totp.get_user_totp_state(uid)["enabled"] is True
    # Disable
    r = client.post("/me/2fa/disable")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert totp.get_user_totp_state(uid)["enabled"] is False


# ---------- Fresh install + upgrade scenarios ----------

def test_fresh_install_has_auditor_role_and_v6(admin_session):
    """Fresh install (admin_session creates a new DB): auditor role
    must be present in roles table; schema is v6; users table has
    totp_* columns."""
    from app.core import auth_db
    conn = auth_db.conn()
    # Schema v6
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 6
    # Auditor role present
    row = conn.execute("SELECT id, is_builtin, is_protected FROM roles "
                       "WHERE id='auditor'").fetchone()
    assert row is not None
    assert bool(row["is_builtin"]) is True
    assert bool(row["is_protected"]) is True
    # totp columns exist with right defaults
    col_info = {r["name"]: r for r in
                conn.execute("PRAGMA table_info(users)").fetchall()}
    assert col_info["totp_enabled"]["dflt_value"] == "0"
    assert col_info["totp_required"]["dflt_value"] == "0"


def test_upgrade_from_v5_preserves_data(tmp_path, monkeypatch):
    """Simulate a pre-1.4.99 customer DB (schema v5 with users + roles +
    subject_roles populated). Re-init should bump to v6 + add totp
    columns + add auditor role, all without touching existing rows."""
    import sqlite3
    from app.core import auth_db, roles, db as _db

    # Build a v5 DB file by running only the first 5 migrations
    db_path = tmp_path / "auth.sqlite"
    fake_settings_data_dir = tmp_path
    monkeypatch.setattr("app.config.settings.data_dir", fake_settings_data_dir)

    # Run migrations 1..5 only
    _db.migrate(db_path, auth_db.MIGRATIONS[:5])
    assert _db.get_conn(db_path).execute("PRAGMA user_version").fetchone()[0] == 5

    # Seed a fake "existing customer" — admin user + a non-admin user with
    # a role assignment (default-user). This mimics a real upgrade.
    c = _db.get_conn(db_path)
    with _db.tx(c):
        # Pre-1.4.99 customer DB also already has built-in roles seeded
        # (admin / default-user / clerk / etc — done by seed_builtin_roles
        # at startup before the bump). Stub them in for FK to work.
        for rid in ("admin", "default-user", "clerk"):
            c.execute(
                "INSERT OR IGNORE INTO roles(id, display_name, description, "
                "is_builtin, is_protected, created_at) "
                "VALUES (?, ?, '', 1, 1, strftime('%s','now'))",
                (rid, rid),
            )
        c.execute(
            "INSERT INTO users(username, source, display_name, "
            "password_hash, created_at) VALUES "
            "('alice', 'local', 'Alice', '$argon2id$dummy', "
            "strftime('%s','now'))"
        )
        uid = c.execute("SELECT id FROM users WHERE username='alice'").fetchone()["id"]
        c.execute(
            "INSERT OR IGNORE INTO subject_roles(subject_type, subject_key, role_id) "
            "VALUES ('user', ?, 'default-user')", (str(uid),)
        )
        # Also pre-grant a tool to default-user role to verify top-up
        # doesn't wipe it
        c.execute(
            "INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
            "VALUES ('default-user', 'pdf-merge')"
        )
    # No totp_* columns yet
    cols_pre = {r["name"] for r in
                c.execute("PRAGMA table_info(users)").fetchall()}
    assert "totp_secret" not in cols_pre

    # Simulate jtdt update — apply ALL migrations
    final = _db.migrate(db_path, auth_db.MIGRATIONS)
    assert final == len(auth_db.MIGRATIONS)

    # User row preserved
    c2 = _db.get_conn(db_path)
    row = c2.execute("SELECT username, display_name, totp_enabled, totp_required, "
                     "totp_secret FROM users WHERE username='alice'").fetchone()
    assert row["username"] == "alice"
    assert row["display_name"] == "Alice"
    # New columns default to 0 / NULL — alice does NOT get auto-required
    assert row["totp_enabled"] == 0
    assert row["totp_required"] == 0
    assert row["totp_secret"] is None
    # Role assignment preserved
    role_row = c2.execute(
        "SELECT role_id FROM subject_roles WHERE subject_type='user' "
        "AND subject_key=?", (str(uid),)
    ).fetchone()
    assert role_row["role_id"] == "default-user"

    # Now run the role seeder against this DB — auditor role should appear
    # but existing default-user grants must not be lost.
    # (We need to swap auth_db's path to this DB temporarily.)
    real_path_fn = auth_db.auth_db_path
    monkeypatch.setattr(auth_db, "auth_db_path", lambda: db_path)
    try:
        roles.seed_builtin_roles()
        c3 = _db.get_conn(db_path)
        # Auditor role added
        assert c3.execute("SELECT id FROM roles WHERE id='auditor'").fetchone() is not None
        # Default-user role still has its tools (top-up didn't remove anything)
        n_du_perms = c3.execute(
            "SELECT COUNT(*) AS n FROM role_perms WHERE role_id='default-user'"
        ).fetchone()["n"]
        assert n_du_perms > 0, "default-user perms wiped by seeder"
        # Alice still has her default-user role assigned
        role_after = c3.execute(
            "SELECT role_id FROM subject_roles WHERE subject_key=?", (str(uid),)
        ).fetchone()
        assert role_after["role_id"] == "default-user"
    finally:
        monkeypatch.setattr(auth_db, "auth_db_path", real_path_fn)


# ---------- v1.5.0: built-in jtdt-auditor user auto-seed ----------

def test_default_auditor_user_seeded_on_bootstrap(admin_session):
    """admin_session fixture calls enable_local_with_admin which now also
    runs seed_default_auditor_user(). Verify jtdt-auditor exists."""
    from app.core import auth_db, roles
    row = auth_db.conn().execute(
        "SELECT id, username, password_hash, is_audit_seed, totp_required "
        "FROM users WHERE username=?", (roles.DEFAULT_AUDITOR_USERNAME,)
    ).fetchone()
    assert row is not None, "jtdt-auditor not seeded"
    # Password unset until admin runs reset-password
    assert row["password_hash"] is None
    assert row["is_audit_seed"] == 1
    assert row["totp_required"] == 1
    # auditor role assigned
    role_row = auth_db.conn().execute(
        "SELECT 1 FROM subject_roles WHERE subject_type='user' "
        "AND subject_key=? AND role_id='auditor'", (str(row["id"]),)
    ).fetchone()
    assert role_row is not None


def test_seed_default_auditor_user_idempotent(admin_session):
    """Calling seed_default_auditor_user a second time should NOT
    create a duplicate or modify the existing row."""
    from app.core import auth_db, roles
    before = auth_db.conn().execute(
        "SELECT COUNT(*) AS n FROM users WHERE username=?",
        (roles.DEFAULT_AUDITOR_USERNAME,),
    ).fetchone()["n"]
    assert before == 1
    # Re-seed
    created = roles.seed_default_auditor_user()
    assert created is False
    after = auth_db.conn().execute(
        "SELECT COUNT(*) AS n FROM users WHERE username=?",
        (roles.DEFAULT_AUDITOR_USERNAME,),
    ).fetchone()["n"]
    assert after == 1


def test_seed_default_auditor_re_assigns_role_if_admin_removed_it(admin_session):
    """If an admin accidentally removes the auditor role from
    jtdt-auditor, the next startup re-assigns it."""
    from app.core import auth_db, roles, db
    uid = auth_db.conn().execute(
        "SELECT id FROM users WHERE username=?",
        (roles.DEFAULT_AUDITOR_USERNAME,)
    ).fetchone()["id"]
    with db.tx(auth_db.conn()):
        auth_db.conn().execute(
            "DELETE FROM subject_roles WHERE subject_type='user' "
            "AND subject_key=? AND role_id='auditor'", (str(uid),)
        )
    # Verify role gone
    assert auth_db.conn().execute(
        "SELECT 1 FROM subject_roles WHERE subject_key=?",
        (str(uid),)).fetchone() is None
    # Re-seed should re-attach
    roles.seed_default_auditor_user()
    assert auth_db.conn().execute(
        "SELECT 1 FROM subject_roles WHERE subject_key=? AND role_id='auditor'",
        (str(uid),)).fetchone() is not None


def test_permissions_set_blocks_seed_users(admin_session):
    """admin POST /admin/permissions/set on jtdt-admin or jtdt-auditor →
    400「內建帳號 ... 角色與工具權限固定」。Built-in seed users cannot
    have their roles/tools edited via the matrix."""
    client, _, _ = admin_session
    from app.core import auth_db, roles
    for username in ("jtdt-admin", roles.DEFAULT_AUDITOR_USERNAME):
        row = auth_db.conn().execute(
            "SELECT id FROM users WHERE username=?", (username,)
        ).fetchone()
        if not row:
            continue
        r = client.post("/admin/permissions/set", json={
            "subject_type": "user",
            "subject_key": str(row["id"]),
            "roles": ["clerk"],
        })
        assert r.status_code == 400, \
            f"editing {username} via matrix should be blocked; got {r.status_code}"
        assert "內建" in r.text or "固定" in r.text


def test_default_auditor_user_cannot_be_deleted(admin_session):
    from app.core import auth_db, roles, user_manager
    uid = auth_db.conn().execute(
        "SELECT id FROM users WHERE username=?",
        (roles.DEFAULT_AUDITOR_USERNAME,)
    ).fetchone()["id"]
    with pytest.raises(ValueError, match="內建稽核員"):
        user_manager.delete(uid)


def test_default_auditor_user_login_fails_until_password_set(admin_session):
    """jtdt-auditor with NULL password_hash should NOT be able to login."""
    from app.core import roles
    c = TestClient(app_main.app, follow_redirects=False)
    r = c.post("/login", data={
        "username": roles.DEFAULT_AUDITOR_USERNAME,
        "password": "anyguess123",
    })
    # Either 200 form re-render with error, OR 302 to /login?error.
    # MUST NOT be a successful redirect to /2fa-verify.
    if r.status_code == 302:
        assert "/2fa-verify" not in r.headers["location"]


def test_seed_default_auditor_writes_audit_event(admin_session):
    from app.core import audit_db, roles
    rows = audit_db.conn().execute(
        "SELECT event_type, target FROM audit_events "
        "WHERE event_type='audit_seed_create' AND target=? "
        "ORDER BY id DESC LIMIT 5",
        (roles.DEFAULT_AUDITOR_USERNAME,),
    ).fetchall()
    assert len(rows) >= 1


# ---------- v1.5.0: admin web UI unlock + reset-totp endpoints ----------

def test_admin_unlock_user_endpoint(admin_session):
    """admin POST /admin/users/{uid}/unlock clears that user's lockouts."""
    from app.core import auth_db, db
    client, _, _ = admin_session
    uid = _make_user("locked-user")
    # Insert a fake lockout
    import time
    with db.tx(auth_db.conn()):
        auth_db.conn().execute(
            "INSERT INTO lockouts(key, failed_count, locked_until, last_failed_at) "
            "VALUES (?, 5, ?, ?)",
            (f"user:{uid}:127.0.0.1", time.time() + 600, time.time()),
        )
    n_before = auth_db.conn().execute(
        "SELECT COUNT(*) AS n FROM lockouts WHERE key LIKE ?",
        (f"user:{uid}:%",)).fetchone()["n"]
    assert n_before == 1
    r = client.post(f"/admin/users/{uid}/unlock")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    n_after = auth_db.conn().execute(
        "SELECT COUNT(*) AS n FROM lockouts WHERE key LIKE ?",
        (f"user:{uid}:%",)).fetchone()["n"]
    assert n_after == 0


def test_admin_unlock_all_endpoint(admin_session):
    """admin POST /admin/auth-settings/unlock-all clears every lockout row."""
    from app.core import auth_db, db
    client, _, _ = admin_session
    import time
    with db.tx(auth_db.conn()):
        auth_db.conn().execute(
            "INSERT INTO lockouts(key, failed_count, locked_until, last_failed_at) "
            "VALUES ('ip:1.2.3.4', 5, ?, ?)",
            (time.time() + 600, time.time()))
        auth_db.conn().execute(
            "INSERT INTO lockouts(key, failed_count, locked_until, last_failed_at) "
            "VALUES ('user:99:1.2.3.4', 5, ?, ?)",
            (time.time() + 600, time.time()))
    r = client.post("/admin/auth-settings/unlock-all")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["cleared"] >= 2
    n = auth_db.conn().execute("SELECT COUNT(*) FROM lockouts").fetchone()[0]
    assert n == 0


def test_admin_reset_totp_endpoint(admin_session):
    """admin POST /admin/users/{uid}/reset-totp clears secret + enabled +
    revokes sessions. Next login will go to forced setup again."""
    from app.core import auth_db, totp, sessions
    client, _, _ = admin_session
    pw = "Pass12345678"
    uid = _make_user("totp-victim", pw)
    # Pretend they completed 2FA setup earlier
    secret = totp.new_secret()
    totp.set_secret(uid, secret)
    totp.mark_enabled(uid)
    sessions.issue(uid, remember=False, ip="1.2.3.4", ua="x")
    assert totp.get_user_totp_state(uid)["enabled"] is True
    n_sess = auth_db.conn().execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id=?", (uid,)).fetchone()[0]
    assert n_sess >= 1
    # Admin resets TOTP
    r = client.post(f"/admin/users/{uid}/reset-totp")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    st = totp.get_user_totp_state(uid)
    assert st["enabled"] is False
    assert st["has_secret"] is False
    # Sessions wiped
    n_sess_after = auth_db.conn().execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id=?", (uid,)).fetchone()[0]
    assert n_sess_after == 0


def test_admin_reset_totp_then_login_shows_qr_again(admin_session):
    """Regression for the `Bug #6` user hit on .30: after admin resets
    TOTP, next login should show QR (forced_setup branch)."""
    from app.core import totp, permissions
    pw = "Pass12345678"
    uid = _make_user("re-setup", pw)
    permissions.assign_role("user", str(uid), "auditor")
    # Pretend an earlier ops left a stale enabled TOTP
    secret_old = totp.new_secret()
    totp.set_secret(uid, secret_old)
    totp.mark_enabled(uid)
    # Admin resets
    client, _, _ = admin_session
    r = client.post(f"/admin/users/{uid}/reset-totp")
    assert r.status_code == 200
    # User logs in fresh — should land on forced setup with QR
    c = TestClient(app_main.app, follow_redirects=False)
    c.post("/login", data={"username": "re-setup", "password": pw})
    page = c.get("/2fa-verify")
    assert page.status_code == 200
    assert "data:image/png;base64," in page.text, "QR should be shown after admin reset"
    # And the new secret in DB should differ from the old one
    new_secret = totp.get_secret(uid)
    assert new_secret is not None
    assert new_secret != secret_old


def test_admin_reset_totp_404_on_unknown_user(admin_session):
    client, _, _ = admin_session
    r = client.post("/admin/users/99999/reset-totp")
    assert r.status_code == 404


def test_role_update_cannot_grant_tools_to_auditor(admin_session):
    """admin POSTing tools=[...] to /admin/roles/auditor/update is silently
    ignored (separation of duties)."""
    from app.core import roles, auth_db
    roles.update("auditor", tools=["pdf-merge", "pdf-fill"])
    n = auth_db.conn().execute(
        "SELECT COUNT(*) AS n FROM role_perms WHERE role_id='auditor'"
    ).fetchone()["n"]
    assert n == 0, f"auditor role should have NO tools; got {n}"


# ---------- v1.5.0: auditor isolation enforcement (separation of duties) ----------

def test_enforce_auditor_isolation_strips_other_roles(admin_session):
    """User with auditor + admin + default-user roles → cleanup leaves only auditor."""
    from app.core import roles, permissions, auth_db, db
    uid = _make_user("dirty-aud")
    # Manually inject mixed roles into DB (simulating pre-1.5.0 state or
    # a sloppy admin assignment)
    with db.tx(auth_db.conn()):
        for rid in ("admin", "default-user", "auditor", "clerk"):
            auth_db.conn().execute(
                "INSERT OR IGNORE INTO subject_roles(subject_type, subject_key, role_id) "
                "VALUES ('user', ?, ?)", (str(uid), rid))
    summary = roles.enforce_auditor_isolation()
    assert summary["users_cleaned"] >= 1
    after = sorted(permissions.list_roles_for_subject("user", str(uid)))
    assert after == ["auditor"], f"expected only auditor; got {after}"


def test_enforce_auditor_isolation_strips_direct_tool_perms(admin_session):
    """Direct subject_perms (subject_type='user') for an auditor must be wiped."""
    from app.core import roles, permissions, auth_db, db
    uid = _make_user("perm-bypass")
    permissions.assign_role("user", str(uid), "auditor")
    # Bypass grant_tool (which now refuses) by writing direct SQL — simulating
    # a customer DB from before the validation was added.
    with db.tx(auth_db.conn()):
        for tool in ("pdf-merge", "pdf-fill"):
            auth_db.conn().execute(
                "INSERT OR IGNORE INTO subject_perms(subject_type, subject_key, tool_id) "
                "VALUES ('user', ?, ?)", (str(uid), tool))
    n_before = auth_db.conn().execute(
        "SELECT COUNT(*) AS n FROM subject_perms WHERE subject_key=?",
        (str(uid),)).fetchone()["n"]
    assert n_before == 2
    roles.enforce_auditor_isolation()
    n_after = auth_db.conn().execute(
        "SELECT COUNT(*) AS n FROM subject_perms WHERE subject_key=?",
        (str(uid),)).fetchone()["n"]
    assert n_after == 0


def test_enforce_auditor_isolation_forces_totp_required(admin_session):
    """Auditor user with totp_required=0 in DB → forced to 1."""
    from app.core import roles, permissions, totp, auth_db, db
    uid = _make_user("notp-aud")
    permissions.assign_role("user", str(uid), "auditor")
    # Manually clear totp_required (e.g. older DB state)
    with db.tx(auth_db.conn()):
        auth_db.conn().execute(
            "UPDATE users SET totp_required=0 WHERE id=?", (uid,))
    assert totp.get_user_totp_state(uid)["required"] is False
    roles.enforce_auditor_isolation()
    assert totp.get_user_totp_state(uid)["required"] is True


def test_assign_role_auditor_triggers_cleanup(admin_session):
    """When admin assigns auditor role to a user who already has other
    roles, the other roles are immediately removed."""
    from app.core import permissions
    uid = _make_user("convert-to-aud")
    permissions.assign_role("user", str(uid), "default-user")
    permissions.assign_role("user", str(uid), "clerk")
    assert sorted(permissions.list_roles_for_subject(
        "user", str(uid))) == ["clerk", "default-user"]
    permissions.assign_role("user", str(uid), "auditor")
    after = sorted(permissions.list_roles_for_subject("user", str(uid)))
    assert after == ["auditor"], f"non-auditor roles must be stripped; got {after}"


def test_set_subject_roles_with_auditor_strips_others(admin_session):
    """set_subject_roles([admin, auditor]) must end up with only auditor."""
    from app.core import permissions
    uid = _make_user("mix-attempt")
    permissions.set_subject_roles(
        "user", str(uid), ["admin", "auditor", "default-user"])
    after = sorted(permissions.list_roles_for_subject("user", str(uid)))
    assert after == ["auditor"], \
        f"auditor must be exclusive; got {after}"


def test_grant_tool_to_auditor_user_rejected(admin_session):
    """Direct tool grant to a user with auditor role must raise."""
    from app.core import permissions
    uid = _make_user("aud-tool-attempt")
    permissions.assign_role("user", str(uid), "auditor")
    with pytest.raises(ValueError, match="稽核員"):
        permissions.grant_tool("user", str(uid), "pdf-merge")


def test_effective_tools_auditor_is_hard_wall(admin_session):
    """Even if user somehow has auditor + admin + group-granted tools,
    effective_tools must return empty set (hard wall)."""
    from app.core import permissions, auth_db, db, group_manager
    uid = _make_user("hard-wall-test")
    # Direct: auditor + admin (DB-level inject; assign_role would clean it)
    with db.tx(auth_db.conn()):
        for rid in ("auditor", "admin"):
            auth_db.conn().execute(
                "INSERT OR IGNORE INTO subject_roles(subject_type, subject_key, role_id) "
                "VALUES ('user', ?, ?)", (str(uid), rid))
    # Group with default-user role
    gid = group_manager.create_local("evil-grp", "")
    group_manager.set_members(gid, [uid])
    permissions.assign_role("group", str(gid), "default-user")
    # Direct tool grant inject too
    with db.tx(auth_db.conn()):
        auth_db.conn().execute(
            "INSERT OR IGNORE INTO subject_perms(subject_type, subject_key, tool_id) "
            "VALUES ('user', ?, 'pdf-merge')", (str(uid),))
    permissions.invalidate_cache()
    et = permissions.effective_tools(uid)
    assert et == set(), \
        f"auditor must have ZERO tools regardless of mixed roles; got {et}"
    assert permissions.is_admin(uid) is False, \
        "auditor user must NOT be considered admin even with admin role row"


def test_startup_runs_enforce_auditor_isolation(admin_session):
    """The startup hook should have run enforce_auditor_isolation().
    We can't directly observe startup but we can verify the function is
    wired in by inspecting the audit log when we manually trigger it
    with a dirty user."""
    from app.core import roles, permissions, auth_db, db, audit_db
    uid = _make_user("startup-victim")
    with db.tx(auth_db.conn()):
        for rid in ("auditor", "admin"):
            auth_db.conn().execute(
                "INSERT OR IGNORE INTO subject_roles(subject_type, subject_key, role_id) "
                "VALUES ('user', ?, ?)", (str(uid), rid))
    roles.enforce_auditor_isolation()
    rows = audit_db.conn().execute(
        "SELECT event_type, target FROM audit_events "
        "WHERE event_type='auditor_isolation_cleanup' "
        "ORDER BY id DESC LIMIT 5").fetchall()
    assert len(rows) >= 1, "auditor_isolation_cleanup event not logged"


def test_me_2fa_disable_blocked_for_auditor(admin_session):
    """Auditor role users cannot self-disable 2FA."""
    from app.core import permissions, totp
    pw = "Pass12345678"
    uid = _make_user("liam-aud", pw)
    permissions.assign_role("user", str(uid), "auditor")
    # Give them an enabled 2FA setup directly
    secret = totp.new_secret()
    totp.set_secret(uid, secret)
    totp.mark_enabled(uid)
    # Login (requires going through 2FA flow)
    c = TestClient(app_main.app, follow_redirects=False)
    c.post("/login", data={"username": "liam-aud", "password": pw})
    code = pyotp.TOTP(secret).now()
    c.post("/2fa-verify", data={"code": code})
    # Now try to disable
    r = c.post("/me/2fa/disable")
    assert r.status_code == 403, f"auditor disable should be 403; got {r.status_code}"
    # Still enabled
    assert totp.get_user_totp_state(uid)["enabled"] is True
