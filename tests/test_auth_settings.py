"""Tests for app.core.auth_settings (backend selection + bootstrap).

Test list:
  - Default backend is 'off'
  - Session secret file created on first call, persists across calls
  - Session secret file mode = 0o600 (POSIX only — Windows skips)
  - Settings file mode = 0o600 (POSIX only)
  - enable_local_with_admin: happy path creates user + flips backend + audit
  - Refuses if backend already on
  - Refuses if username invalid (regex / too long / empty)
  - Refuses if password fails policy (too short etc)
  - Refuses if password ≠ confirm
  - disable_auth flips back to off + wipes sessions, KEEPS users
"""
from __future__ import annotations

import os
import sys

import pytest

from app.core import auth_settings


def test_default_backend(auth_off):
    assert auth_settings.get_backend() == "off"
    assert auth_settings.is_enabled() is False


def test_session_secret_persists():
    s1 = auth_settings.get_session_secret()
    s2 = auth_settings.get_session_secret()
    assert s1 == s2
    assert len(s1) == 32


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode only")
def test_session_secret_file_mode():
    # Force-create the file by reading the secret
    auth_settings.get_session_secret()
    p = auth_settings._secret_path()
    mode = os.stat(p).st_mode & 0o777
    assert mode == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode only")
def test_auth_settings_file_mode(auth_off):
    # save() always writes the file with chmod 600
    auth_settings.save({"backend": "off"})
    p = auth_settings._path()
    mode = os.stat(p).st_mode & 0o777
    assert mode == 0o600


def test_bootstrap_happy_path(auth_off):
    uid = auth_settings.enable_local_with_admin(
        admin_username="bootstrap-admin",
        admin_display_name="Boot Admin",
        admin_password="BootPass1234",
        admin_password_confirm="BootPass1234",
    )
    assert uid > 0
    assert auth_settings.get_backend() == "local"
    assert auth_settings.is_enabled() is True


def test_bootstrap_refuses_if_already_on(admin_session):
    # admin_session already bootstrapped
    with pytest.raises(auth_settings.BootstrapError):
        auth_settings.enable_local_with_admin(
            admin_username="another-admin",
            admin_display_name="",
            admin_password="OtherPass1234",
            admin_password_confirm="OtherPass1234",
        )


@pytest.mark.parametrize("bad_user", [
    "", "  ", "user with space", "中文帳號", "user@host", "../../etc/passwd",
])
def test_bootstrap_rejects_bad_username(auth_off, bad_user):
    with pytest.raises(auth_settings.BootstrapError):
        auth_settings.enable_local_with_admin(
            admin_username=bad_user,
            admin_display_name="",
            admin_password="OkayPass1234",
            admin_password_confirm="OkayPass1234",
        )


def test_bootstrap_rejects_short_password(auth_off):
    with pytest.raises(auth_settings.BootstrapError):
        auth_settings.enable_local_with_admin(
            admin_username="admin",
            admin_display_name="",
            admin_password="short",
            admin_password_confirm="short",
        )


def test_bootstrap_rejects_mismatched_confirm(auth_off):
    with pytest.raises(auth_settings.BootstrapError):
        auth_settings.enable_local_with_admin(
            admin_username="admin",
            admin_display_name="",
            admin_password="OkayPass1234",
            admin_password_confirm="OtherPass1234",
        )


def test_disable_auth_keeps_user_wipes_sessions(admin_session):
    from app.core import auth_db
    # Confirm 1 user + 1 session right now
    user_count = auth_db.conn().execute("SELECT count(*) FROM users").fetchone()[0]
    sess_count = auth_db.conn().execute("SELECT count(*) FROM sessions").fetchone()[0]
    assert user_count == 1
    assert sess_count >= 1

    auth_settings.disable_auth(actor="jtdt-admin")

    assert auth_settings.get_backend() == "off"
    user_count = auth_db.conn().execute("SELECT count(*) FROM users").fetchone()[0]
    sess_count = auth_db.conn().execute("SELECT count(*) FROM sessions").fetchone()[0]
    assert user_count == 1   # user kept
    assert sess_count == 0   # sessions wiped
