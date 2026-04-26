"""Shared fixtures for the jt-doc-tools test suite.

Provides:
  • ``client``: a TestClient bound to the FastAPI app.
  • ``sample_pdf``: bytes of a freshly-built minimal PDF (one page,
    A4, with a label so PDF Fill can detect at least one field).
  • ``two_page_pdf`` / ``ten_page_pdf``: small multi-page PDFs.
  • ``stamp_png``: bytes for a coloured PNG used as a stamp/watermark.

IMPORTANT: this module **must** set ``JTDT_DATA_DIR`` BEFORE importing
``app.main`` — Settings is a singleton frozen at first import. If we let
the import use the dev's real `data/` dir, tests would write into it.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from io import BytesIO
from pathlib import Path

# ---- Isolate test data dir BEFORE any app.* import ----
# (Module-level: runs once per pytest invocation.)
if "JTDT_DATA_DIR" not in os.environ:
    _TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="jtdt_test_"))
    os.environ["JTDT_DATA_DIR"] = str(_TEST_DATA_DIR)
    # Seed: copy a few harmless files from real data/ if present (avoids
    # tests that need a default profile / assets failing). We deliberately
    # DO NOT copy api_tokens.json / auth.sqlite / audit.sqlite to keep
    # auth tests on a clean slate.
    _real = Path(__file__).resolve().parent.parent / "data"
    if _real.exists():
        for sub in ("assets", "fonts"):
            src = _real / sub
            if src.is_dir():
                shutil.copytree(src, _TEST_DATA_DIR / sub, dirs_exist_ok=True)
        for f in ("profile.json", "label_synonyms.json",
                  "form_templates.json", "office_paths.json"):
            sf = _real / f
            if sf.is_file():
                shutil.copy2(sf, _TEST_DATA_DIR / f)

import fitz
import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient

import app.main as app_main


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app_main.app)


@pytest.fixture
def auth_off():
    """Reset auth to OFF and wipe any users between tests so each test
    starts on a clean canvas. Defensively init schemas — the app's startup
    hook runs lazily under TestClient so tests that touch the DB before
    making any HTTP request would otherwise see "no such table"."""
    from app.core import auth_settings, auth_db, audit_db, db
    auth_db.init()
    audit_db.init()
    s = auth_settings.get()
    s["backend"] = "off"
    auth_settings.save(s)
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM lockouts")
        conn.execute("DELETE FROM subject_perms")
        conn.execute("DELETE FROM subject_roles")
        conn.execute("DELETE FROM group_members")
        conn.execute("DELETE FROM groups")
        conn.execute("DELETE FROM users")
    yield
    # cleanup on teardown too
    s = auth_settings.get()
    s["backend"] = "off"
    auth_settings.save(s)
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM lockouts")
        conn.execute("DELETE FROM users")


@pytest.fixture
def admin_session(auth_off):
    """Bootstrap auth=local with a known admin and return a logged-in
    TestClient. Yields (client, admin_username, admin_password)."""
    from app.core import auth_settings, sessions
    pw = "TestAdmin1234"
    auth_settings.enable_local_with_admin(
        admin_username="jtdt-admin",
        admin_display_name="管理員",
        admin_password=pw,
        admin_password_confirm=pw,
        actor_ip="127.0.0.1",
    )
    # Issue a fresh session directly (skip login flow noise)
    from app.core import auth_db
    uid = auth_db.conn().execute(
        "SELECT id FROM users WHERE username='jtdt-admin'"
    ).fetchone()["id"]
    token, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, token)
    yield c, "jtdt-admin", pw


def _make_pdf(pages: int, label: str | None = None) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # A4 portrait, pt
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=14)
        if i == 0 and label:
            page.insert_text((72, 120), label, fontsize=12)
    buf = BytesIO()
    doc.save(buf, garbage=3, deflate=True)
    doc.close()
    return buf.getvalue()


@pytest.fixture
def sample_pdf() -> bytes:
    # "公司名稱:" is in our LABEL_MAP so detection produces ≥ 1 field.
    return _make_pdf(1, "公司名稱: ")


@pytest.fixture
def two_page_pdf() -> bytes:
    return _make_pdf(2)


@pytest.fixture
def ten_page_pdf() -> bytes:
    return _make_pdf(10)


@pytest.fixture
def stamp_png() -> bytes:
    im = Image.new("RGBA", (240, 120), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((4, 4, 235, 115), outline=(220, 30, 30, 255), width=4)
    d.text((20, 40), "STAMP", fill=(220, 30, 30, 255))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()
