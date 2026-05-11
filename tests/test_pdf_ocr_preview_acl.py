"""End-to-end ACL test for pdf-ocr `/preview/{uid}.pdf` endpoint (v1.7.6).

Verifies the new viewer-supporting preview endpoint enforces cross-user
isolation when auth is enabled:

  - owner can preview              → 200
  - other authenticated user       → 403
  - missing owner record           → 403  (fail-secure, no ID guessing)
  - admin override                 → 200
  - bad upload_id format           → 400  (require_uuid_hex catches it first)
  - non-existent output file       → 404
  - auth disabled (single-user)    → bypasses check

Mirrors the structure of the upload_owner unit tests but exercises the real
HTTP route to catch wiring bugs (e.g. forgetting to call _uo.require()).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Logging spam suppressed by conftest if present; otherwise harmless.
    from app.main import app
    return TestClient(app)


@pytest.fixture
def fake_pdf_bytes() -> bytes:
    """Minimal valid PDF — PDF.js doesn't need to parse it for our test;
    only TestClient cares we got the right bytes back."""
    return (b"%PDF-1.4\n1 0 obj<<>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF\n")


@pytest.fixture
def stage_output(monkeypatch, tmp_path, fake_pdf_bytes):
    """Stage a fake `po_<uid>_out.pdf` in an isolated temp work dir so the
    endpoint finds something to serve."""
    work = tmp_path / "pdf_ocr"
    work.mkdir()
    # `app.tools.pdf_ocr.__init__` rebinds `router` to the APIRouter, masking
    # the submodule. Reach the actual module via sys.modules to patch _work_dir.
    import sys
    import app.tools.pdf_ocr.router  # noqa: F401  ensure module loaded
    ocr_router_mod = sys.modules["app.tools.pdf_ocr.router"]
    monkeypatch.setattr(ocr_router_mod, "_work_dir", lambda: work)

    def _stage(uid: str) -> Path:
        out = work / f"po_{uid}_out.pdf"
        out.write_bytes(fake_pdf_bytes)
        return out

    return _stage


@pytest.fixture
def stub_owner(monkeypatch, tmp_path):
    """Isolate upload_owner storage so other tests don't pollute and we can
    record/check freely."""
    from app.core import upload_owner
    owners = tmp_path / "owners"
    owners.mkdir()
    monkeypatch.setattr(upload_owner, "_owners_dir", lambda: owners)
    return owners


def _set_user(monkeypatch, user_id: int | None, *, is_admin: bool = False):
    """Patch request.state.user via the single point this endpoint reads from
    (upload_owner._user_id / _is_admin). Uses monkeypatch so changes auto-revert
    at end of test — direct assignment leaks across tests and breaks
    test_safe_paths_and_owner."""
    from app.core import upload_owner
    monkeypatch.setattr(upload_owner, "_user_id",
                          lambda req: user_id)
    monkeypatch.setattr(upload_owner, "_is_admin",
                          lambda uid: is_admin)


VALID_UID = "a" * 32


class TestPreviewEndpointAcl:
    def test_owner_can_preview_when_auth_on(
        self, client, stage_output, stub_owner, monkeypatch
    ):
        from app.core import upload_owner
        monkeypatch.setattr(upload_owner, "_auth_enabled", lambda: True)
        # Record user 42 as owner
        (stub_owner / f"{VALID_UID}.json").write_text(
            '{"user_id": 42, "ts": 0}', encoding="utf-8")
        stage_output(VALID_UID)
        _set_user(monkeypatch, 42)
        r = client.get(f"/tools/pdf-ocr/preview/{VALID_UID}.pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers.get("content-disposition", "").startswith("inline")
        assert r.content.startswith(b"%PDF-")

    def test_other_user_denied(
        self, client, stage_output, stub_owner, monkeypatch
    ):
        from app.core import upload_owner
        monkeypatch.setattr(upload_owner, "_auth_enabled", lambda: True)
        (stub_owner / f"{VALID_UID}.json").write_text(
            '{"user_id": 42, "ts": 0}', encoding="utf-8")
        stage_output(VALID_UID)
        # User 99 ≠ owner 42, not admin
        _set_user(monkeypatch, 99, is_admin=False)
        r = client.get(f"/tools/pdf-ocr/preview/{VALID_UID}.pdf")
        assert r.status_code == 403, r.text

    def test_missing_owner_record_denies_non_admin(
        self, client, stage_output, stub_owner, monkeypatch
    ):
        from app.core import upload_owner
        monkeypatch.setattr(upload_owner, "_auth_enabled", lambda: True)
        # No owner record written — simulates someone guessing a valid-looking uid
        stage_output(VALID_UID)  # File exists but no ACL record
        _set_user(monkeypatch, 42, is_admin=False)
        r = client.get(f"/tools/pdf-ocr/preview/{VALID_UID}.pdf")
        assert r.status_code == 403, r.text

    def test_admin_override(
        self, client, stage_output, stub_owner, monkeypatch
    ):
        from app.core import upload_owner
        monkeypatch.setattr(upload_owner, "_auth_enabled", lambda: True)
        (stub_owner / f"{VALID_UID}.json").write_text(
            '{"user_id": 42, "ts": 0}', encoding="utf-8")
        stage_output(VALID_UID)
        _set_user(monkeypatch, 99, is_admin=True)
        r = client.get(f"/tools/pdf-ocr/preview/{VALID_UID}.pdf")
        assert r.status_code == 200

    def test_bad_uid_format_rejected_before_acl(
        self, client, stub_owner, monkeypatch
    ):
        from app.core import upload_owner
        monkeypatch.setattr(upload_owner, "_auth_enabled", lambda: True)
        _set_user(monkeypatch, 42)
        # require_uuid_hex raises 400 before _uo.require is reached
        for bad in ("../etc/passwd", "not-a-uuid", "g" * 32, "a" * 31):
            r = client.get(f"/tools/pdf-ocr/preview/{bad}.pdf")
            assert r.status_code in (400, 404), (
                f"bad uid {bad!r} should not reach ACL: got {r.status_code}")

    def test_nonexistent_output_returns_404(
        self, client, stage_output, stub_owner, monkeypatch
    ):
        from app.core import upload_owner
        monkeypatch.setattr(upload_owner, "_auth_enabled", lambda: True)
        # Owner record exists but file isn't staged
        (stub_owner / f"{VALID_UID}.json").write_text(
            '{"user_id": 42, "ts": 0}', encoding="utf-8")
        _set_user(monkeypatch, 42)
        r = client.get(f"/tools/pdf-ocr/preview/{VALID_UID}.pdf")
        assert r.status_code == 404

    def test_auth_disabled_bypasses_check(
        self, client, stage_output, stub_owner, monkeypatch
    ):
        from app.core import upload_owner
        monkeypatch.setattr(upload_owner, "_auth_enabled", lambda: False)
        stage_output(VALID_UID)
        # No owner record, no logged-in user — single-user mode lets anyone in
        _set_user(monkeypatch, None)
        r = client.get(f"/tools/pdf-ocr/preview/{VALID_UID}.pdf")
        assert r.status_code == 200
