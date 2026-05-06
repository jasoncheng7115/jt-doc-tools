"""Tests for app.core.safe_paths and app.core.upload_owner.

Covers:
- Path traversal rejection (../, /, \\, NUL, dotdot, unicode separators)
- UUID hex strict validation
- safe_join containment (incl. symlink-style escapes)
- Owner record/check ACL with auth ON / OFF, admin override, missing record
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core import safe_paths
from app.core import upload_owner


# ---------- safe_paths ----------

class TestSanitizeFilename:
    def test_valid_simple(self):
        assert safe_paths.sanitize_filename("foo.png") == "foo.png"

    def test_valid_with_uuid(self):
        n = "a" * 32 + "_p1.png"
        assert safe_paths.sanitize_filename(n) == n

    @pytest.mark.parametrize("name", [
        "../etc/passwd",
        "..",
        ".",
        "foo/bar.png",
        "foo\\bar.png",
        "foo\x00.png",
        "foo..bar",
        "",
        "中文.png",  # non-ASCII rejected by current allowlist
        "a" * 256,  # over 255 chars
    ])
    def test_rejects_evil(self, name):
        with pytest.raises(HTTPException) as exc:
            safe_paths.sanitize_filename(name)
        assert exc.value.status_code == 400


class TestSafeJoin:
    def test_basic_containment(self, tmp_path):
        p = safe_paths.safe_join(tmp_path, "ok.png")
        assert p.parent == tmp_path
        assert p.name == "ok.png"

    def test_blocks_dotdot(self, tmp_path):
        with pytest.raises(HTTPException):
            safe_paths.safe_join(tmp_path, "../escape.png")

    def test_blocks_absolute_attempt(self, tmp_path):
        with pytest.raises(HTTPException):
            safe_paths.safe_join(tmp_path, "/etc/passwd")

    def test_blocks_symlink_escape(self, tmp_path):
        # Create a symlink in tmp_path that points outside
        evil = tmp_path / "evil"
        target = tmp_path.parent / "outside"
        target.mkdir(exist_ok=True)
        try:
            evil.symlink_to(target)
            # Even though "evil" is a valid filename, joining and resolving
            # should detect it points outside tmp_path
            # Note: safe_join uses .resolve() which follows symlinks
            with pytest.raises(HTTPException):
                safe_paths.safe_join(tmp_path, "evil")
        except OSError:
            pytest.skip("symlink not permitted on this filesystem")
        finally:
            if target.exists():
                try:
                    target.rmdir()
                except OSError:
                    pass


class TestUuidHex:
    def test_valid(self):
        assert safe_paths.is_uuid_hex("a" * 32)
        assert safe_paths.is_uuid_hex("0123456789abcdef" * 2)

    @pytest.mark.parametrize("s", [
        "",
        "X" * 32,           # uppercase rejected (we standardize on lower)
        "abc",              # too short
        "a" * 33,           # too long
        "a" * 31 + "/",     # invalid char
        None,
    ])
    def test_invalid(self, s):
        assert not safe_paths.is_uuid_hex(s or "")

    def test_require_raises(self):
        with pytest.raises(HTTPException) as e:
            safe_paths.require_uuid_hex("not-a-uuid", "upload_id")
        assert e.value.status_code == 400


# ---------- upload_owner ----------

@pytest.fixture
def fake_request():
    """Minimal Request stand-in with state.user dict."""
    def make(user_id=None):
        req = MagicMock()
        req.state.user = {"user_id": user_id, "username": "u"} if user_id is not None else None
        return req
    return make


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    """Stub out _owners_dir so each test gets isolated owner storage."""
    owners = tmp_path / ".owners"
    owners.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(upload_owner, "_owners_dir", lambda: owners)
    return tmp_path


class TestUploadOwner:
    def test_check_passes_when_auth_off(self, fake_request):
        with patch("app.core.upload_owner._auth_enabled", return_value=False):
            assert upload_owner.check("a" * 32, fake_request(123)) is True
            # Even bogus id passes when auth is off
            assert upload_owner.check("invalid", fake_request(None)) is True

    def test_check_denies_anonymous_when_auth_on(self, fake_request, temp_settings):
        with patch("app.core.upload_owner._auth_enabled", return_value=True):
            assert upload_owner.check("a" * 32, fake_request(None)) is False

    def test_check_denies_unknown_id_format(self, fake_request, temp_settings):
        with patch("app.core.upload_owner._auth_enabled", return_value=True):
            assert upload_owner.check("not-uuid", fake_request(1)) is False

    def test_record_then_check_owner_ok(self, fake_request, temp_settings):
        with patch("app.core.upload_owner._auth_enabled", return_value=True):
            uid = "a" * 32
            upload_owner.record(uid, fake_request(42))
            assert upload_owner.check(uid, fake_request(42)) is True

    def test_record_then_check_other_user_denied(self, fake_request, temp_settings):
        with patch("app.core.upload_owner._auth_enabled", return_value=True), \
             patch("app.core.upload_owner._is_admin", return_value=False):
            uid = "b" * 32
            upload_owner.record(uid, fake_request(42))
            assert upload_owner.check(uid, fake_request(99)) is False

    def test_admin_override(self, fake_request, temp_settings):
        with patch("app.core.upload_owner._auth_enabled", return_value=True), \
             patch("app.core.upload_owner._is_admin", return_value=True):
            uid = "c" * 32
            upload_owner.record(uid, fake_request(42))
            # User 99 is admin → can access user 42's upload
            assert upload_owner.check(uid, fake_request(99)) is True

    def test_missing_record_denies_non_admin(self, fake_request, temp_settings):
        with patch("app.core.upload_owner._auth_enabled", return_value=True), \
             patch("app.core.upload_owner._is_admin", return_value=False):
            assert upload_owner.check("d" * 32, fake_request(42)) is False

    def test_missing_record_allows_admin(self, fake_request, temp_settings):
        with patch("app.core.upload_owner._auth_enabled", return_value=True), \
             patch("app.core.upload_owner._is_admin", return_value=True):
            assert upload_owner.check("e" * 32, fake_request(42)) is True

    def test_require_raises_403_on_deny(self, fake_request, temp_settings):
        with patch("app.core.upload_owner._auth_enabled", return_value=True):
            with pytest.raises(HTTPException) as exc:
                upload_owner.require("f" * 32, fake_request(None))
            assert exc.value.status_code == 403

    def test_extract_upload_id(self):
        uid = "a" * 32
        assert upload_owner.extract_upload_id(f"{uid}_p1.png") == uid
        assert upload_owner.extract_upload_id(f"{uid}_filled.pdf") == uid
        assert upload_owner.extract_upload_id("not-a-uuid_p1.png") == ""
        assert upload_owner.extract_upload_id("") == ""
