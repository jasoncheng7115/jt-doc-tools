"""Regression tests for the /api/jobs/* per-job ownership ACL (v1.12.61).

Security fix: /api/* bypasses the cookie auth gate, and the job endpoints used
to serve/cancel any job by id with NO ownership check — plus a fallback that
globbed every user's *_work dir by basename (unauthenticated arbitrary read).
These tests lock in: owner-only access, unauthenticated denial, and that the
cross-user filename glob is gone.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from starlette.testclient import TestClient

from app import main as app_main
from app.config import settings
from app.core import auth_db, sessions, user_manager
from app.core.job_manager import Job, job_manager


def _session_client(uid: int) -> TestClient:
    tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, tok)
    return c


def _make_job(owner_id, name="result.pdf") -> str:
    work = settings.temp_dir / "acltest_work"
    work.mkdir(parents=True, exist_ok=True)
    f = work / name
    f.write_bytes(b"%PDF-1.4 secret owner content")
    job = Job(id="acltestjob" + name.replace(".", ""), tool_id="test")
    job.owner_id = owner_id
    job.result_path = f
    job.result_filename = name
    job.status = "done"
    job_manager._jobs[job.id] = job
    return job.id


def test_job_download_is_owner_only(admin_session):
    """auth ON: only the owning user can download / see / cancel a job."""
    c_a, admin_name, _ = admin_session
    a_uid = auth_db.conn().execute(
        "SELECT id FROM users WHERE username=?", (admin_name,)).fetchone()["id"]
    b_uid = user_manager.create_local("bob-job", "Bob", "TestPass1234")
    c_b = _session_client(b_uid)
    c_anon = TestClient(app_main.app)  # no cookie

    jid = _make_job(a_uid)

    # owner A: full access
    assert c_a.get(f"/api/jobs/{jid}").status_code == 200
    r = c_a.get(f"/api/jobs/{jid}/download")
    assert r.status_code == 200 and b"secret owner content" in r.content

    # other user B: 404 (no confirmation of existence)
    assert c_b.get(f"/api/jobs/{jid}").status_code == 404
    assert c_b.get(f"/api/jobs/{jid}/download").status_code == 404
    assert c_b.get(f"/api/jobs/{jid}/download-png").status_code == 404
    assert c_b.post(f"/api/jobs/{jid}/cancel").status_code == 404

    # unauthenticated (auth is on): 404
    assert c_anon.get(f"/api/jobs/{jid}/download").status_code == 404


def test_cross_user_filename_glob_removed(admin_session):
    """The old fallback served any *_work/<basename> to anyone. A request for a
    non-existent job with a real basename must NOT serve the file anymore."""
    c_a, _, _ = admin_session
    work = settings.temp_dir / "victim_work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "output.pdf").write_bytes(b"victim private doc")
    # unknown job id + a real basename → must be 404, not the victim's file
    r = c_a.get("/api/jobs/doesnotexist/download/output.pdf")
    assert r.status_code == 404
    assert b"victim private doc" not in r.content


def test_job_open_when_auth_off(auth_off):
    """auth OFF (single-user install): no owners → job download works."""
    jid = _make_job(None, name="open.pdf")
    c = TestClient(app_main.app)
    r = c.get(f"/api/jobs/{jid}/download")
    assert r.status_code == 200 and b"secret owner content" in r.content
