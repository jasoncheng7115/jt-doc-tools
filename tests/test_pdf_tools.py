"""End-to-end tests for the simple PDF tools (merge / split / rotate / pages /
pageno / extract-images). Each driver:

  1. POSTs to the tool's /submit
  2. Polls /api/jobs/{id} until done
  3. Downloads the result and verifies basic structure (page count, file type)
"""
from __future__ import annotations

import time
import zipfile
from io import BytesIO

import fitz
import pytest


def _wait_done(client, job_id, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        j = r.json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def _download(client, job_id) -> bytes:
    r = client.get(f"/api/jobs/{job_id}/download")
    assert r.status_code == 200, r.text
    return r.content


# ---- pdf-merge ----
def test_merge_two_pdfs(client, sample_pdf, two_page_pdf):
    r = client.post(
        "/tools/pdf-merge/submit",
        files=[("file", ("a.pdf", sample_pdf, "application/pdf")),
               ("file", ("b.pdf", two_page_pdf, "application/pdf"))],
    )
    assert r.status_code == 200, r.text
    job = _wait_done(client, r.json()["job_id"])
    assert job["status"] == "done"
    out = _download(client, r.json()["job_id"])
    with fitz.open(stream=out, filetype="pdf") as doc:
        assert doc.page_count == 1 + 2


def test_merge_rejects_single_file(client, sample_pdf):
    r = client.post(
        "/tools/pdf-merge/submit",
        files=[("file", ("a.pdf", sample_pdf, "application/pdf"))],
    )
    assert r.status_code == 400


# ---- pdf-split ----
def test_split_each_page(client, ten_page_pdf):
    r = client.post(
        "/tools/pdf-split/submit",
        data={"mode": "each"},
        files=[("file", ("ten.pdf", ten_page_pdf, "application/pdf"))],
    )
    assert r.status_code == 200, r.text
    job = _wait_done(client, r.json()["job_id"])
    assert job["status"] == "done"
    out = _download(client, r.json()["job_id"])
    with zipfile.ZipFile(BytesIO(out)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".pdf")]
        assert len(names) == 10


def test_split_by_ranges(client, ten_page_pdf):
    r = client.post(
        "/tools/pdf-split/submit",
        data={"mode": "ranges", "ranges": "1-3, 5, 7-"},
        files=[("file", ("ten.pdf", ten_page_pdf, "application/pdf"))],
    )
    assert r.status_code == 200, r.text
    job = _wait_done(client, r.json()["job_id"])
    out = _download(client, r.json()["job_id"])
    with zipfile.ZipFile(BytesIO(out)) as zf:
        names = sorted(n for n in zf.namelist() if n.endswith(".pdf"))
        # 3 segments → 3 PDFs (1-3, 5, 7-10)
        assert len(names) == 3


# ---- pdf-rotate ----
def test_rotate_all_pages_90(client, two_page_pdf):
    r = client.post(
        "/tools/pdf-rotate/submit",
        data={"angle": "90", "pages": "all"},
        files=[("file", ("a.pdf", two_page_pdf, "application/pdf"))],
    )
    assert r.status_code == 200, r.text
    _wait_done(client, r.json()["job_id"])
    out = _download(client, r.json()["job_id"])
    with fitz.open(stream=out, filetype="pdf") as doc:
        for p in doc:
            assert p.rotation == 90


def test_rotate_specific_page(client, ten_page_pdf):
    r = client.post(
        "/tools/pdf-rotate/submit",
        data={"angle": "180", "pages": "3,5"},
        files=[("file", ("a.pdf", ten_page_pdf, "application/pdf"))],
    )
    _wait_done(client, r.json()["job_id"])
    out = _download(client, r.json()["job_id"])
    with fitz.open(stream=out, filetype="pdf") as doc:
        rotations = [p.rotation for p in doc]
    assert rotations[2] == 180 and rotations[4] == 180
    assert rotations[0] == 0 and rotations[1] == 0 and rotations[3] == 0


# ---- pdf-pages (reorder / drop) ----
def test_pages_drop(client, ten_page_pdf):
    r = client.post(
        "/tools/pdf-pages/submit",
        data={"mode": "drop", "spec": "2-4"},
        files=[("file", ("a.pdf", ten_page_pdf, "application/pdf"))],
    )
    _wait_done(client, r.json()["job_id"])
    out = _download(client, r.json()["job_id"])
    with fitz.open(stream=out, filetype="pdf") as doc:
        assert doc.page_count == 7  # 10 - 3 dropped


def test_pages_reorder(client, ten_page_pdf):
    r = client.post(
        "/tools/pdf-pages/submit",
        data={"mode": "reorder", "spec": "5,4,3,2,1"},
        files=[("file", ("a.pdf", ten_page_pdf, "application/pdf"))],
    )
    _wait_done(client, r.json()["job_id"])
    out = _download(client, r.json()["job_id"])
    with fitz.open(stream=out, filetype="pdf") as doc:
        assert doc.page_count == 5


# ---- pdf-pageno ----
def test_pageno_inserts_text(client, two_page_pdf):
    r = client.post(
        "/tools/pdf-pageno/submit",
        data={"position": "br", "fmt": "{n}/{N}", "start": "1",
              "font_size": "11", "margin_mm": "10"},
        files=[("file", ("a.pdf", two_page_pdf, "application/pdf"))],
    )
    _wait_done(client, r.json()["job_id"])
    out = _download(client, r.json()["job_id"])
    with fitz.open(stream=out, filetype="pdf") as doc:
        # Each page should now contain the page-number text we just stamped.
        assert "1/2" in doc[0].get_text()
        assert "2/2" in doc[1].get_text()


# ---- universal PNG download ----
def test_universal_png_download(client, two_page_pdf):
    """After any job completes, /download-png returns PNG (or ZIP of PNGs)."""
    r = client.post(
        "/tools/pdf-rotate/submit",
        data={"angle": "90", "pages": "all"},
        files=[("file", ("a.pdf", two_page_pdf, "application/pdf"))],
    )
    job_id = r.json()["job_id"]
    _wait_done(client, job_id)
    r = client.get(f"/api/jobs/{job_id}/download-png")
    assert r.status_code == 200
    # Two-page PDF → ZIP of two PNGs
    ct = r.headers.get("content-type", "")
    if "zip" in ct.lower() or r.content[:2] == b"PK":
        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            pngs = [n for n in zf.namelist() if n.endswith(".png")]
            assert len(pngs) == 2
    else:
        assert r.content[:8].startswith(b"\x89PNG")
