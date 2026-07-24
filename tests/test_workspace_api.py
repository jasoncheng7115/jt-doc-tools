"""HTTP-level tests for the workspace endpoints (auth OFF / single mode).

Uses the shared TestClient (data dir is isolated to a temp dir by conftest).
"""
from __future__ import annotations

from app.core import workspace as ws

PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def test_workspace_page_renders(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    r = client.get("/workspace")
    assert r.status_code == 200
    assert "我的工作區" in r.text


def test_save_list_serve_delete_roundtrip(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    r = client.post(
        "/workspace/save",
        files={"file": ("doc.pdf", PDF_BYTES, "application/pdf")},
        data={"source_tool": "pytest"},
    )
    assert r.status_code == 200, r.text
    fid = r.json()["file"]["file_id"]

    r = client.get("/workspace/api/list")
    assert r.status_code == 200
    ids = [f["file_id"] for f in r.json()["files"]]
    assert fid in ids

    r = client.get(f"/workspace/file/{fid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")

    r = client.post("/workspace/delete", data={"file_id": fid})
    assert r.status_code == 200
    ids = [f["file_id"] for f in client.get("/workspace/api/list").json()["files"]]
    assert fid not in ids


def test_save_rejects_non_pdf_png(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    r = client.post(
        "/workspace/save",
        files={"file": ("x.zip", b"PK\x03\x04not", "application/zip")},
        data={"source_tool": "pytest"},
    )
    assert r.status_code == 400


def _make_docx() -> bytes:
    from docx import Document
    from io import BytesIO
    d = Document(); d.add_paragraph("測試 workspace docx")
    buf = BytesIO(); d.save(buf); return buf.getvalue()


def _make_odt() -> bytes:
    """Minimal-but-valid ODT: leading uncompressed 'mimetype' + content.xml."""
    import zipfile
    from io import BytesIO
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml",
                   "<?xml version='1.0'?><office:document-content/>")
    return buf.getvalue()


def test_detect_kind_office_and_rejects_plain_zip():
    import io
    import zipfile
    assert ws.detect_kind(_make_docx())[1] == ".docx"
    assert ws.detect_kind(_make_odt())[1] == ".odt"
    # a renamed arbitrary zip (no doc structure) must NOT pass
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("hello.txt", "not a document")
    assert ws.detect_kind(b.getvalue()) is None
    assert ws.detect_kind(b"PK\x03\x04garbage") is None


def test_save_accepts_docx_and_odt(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    for fname, blob, want_mime in [
        ("報告.docx", _make_docx(), "wordprocessingml"),
        ("報告.odt", _make_odt(), "opendocument.text"),
    ]:
        r = client.post(
            "/workspace/save",
            files={"file": (fname, blob, "application/octet-stream")},
            data={"source_tool": "pdf-to-office"},
        )
        assert r.status_code == 200, r.text
        meta = r.json()["file"]
        assert want_mime in meta["mime"]
        # served back with the right extension
        fp = client.get(f"/workspace/file/{meta['file_id']}")
        assert fp.status_code == 200
        # office docs have no thumbnail → placeholder PNG, not a 5xx
        th = client.get(f"/workspace/thumb/{meta['file_id']}")
        assert th.status_code == 200


def test_list_accept_filter(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    client.post("/workspace/save",
                files={"file": ("a.pdf", PDF_BYTES, "application/pdf")})
    # asking only for png should hide the pdf
    files = client.get("/workspace/api/list?accept=png").json()["files"]
    assert all(f["ext"] == ".png" for f in files)


def test_save_by_job_id(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    from app.core.job_manager import job_manager, Job
    from app.config import settings
    import uuid
    jid = uuid.uuid4().hex
    p = settings.temp_dir / f"{jid}_out.pdf"
    p.write_bytes(PDF_BYTES)
    job = Job(id=jid, tool_id="pdf-merge")
    job.result_path = p
    job.result_filename = "merged.pdf"
    job.status = "done"
    job_manager._jobs[jid] = job
    r = client.post("/workspace/save", data={"job_id": jid})
    assert r.status_code == 200, r.text
    meta = r.json()["file"]
    assert meta["source_tool"] == "pdf-merge"
    assert meta["name"] == "merged.pdf"


def test_thumbnail_endpoint(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    import fitz
    doc = fitz.open(); doc.new_page(width=200, height=200)
    data = doc.tobytes(); doc.close()
    fid = client.post("/workspace/save",
                      files={"file": ("d.pdf", data, "application/pdf")}).json()["file"]["file_id"]
    r = client.get(f"/workspace/thumb/{fid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")


def test_count_endpoint(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    before = client.get("/workspace/api/count").json()["count"]
    client.post("/workspace/save",
                files={"file": ("c.pdf", PDF_BYTES, "application/pdf")})
    after = client.get("/workspace/api/count").json()["count"]
    assert after == before + 1


def test_source_tool_chinese_name(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    client.post("/workspace/save",
                files={"file": ("x.pdf", PDF_BYTES, "application/pdf")},
                data={"source_tool": "pdf-merge"})
    files = client.get("/workspace/api/list").json()["files"]
    merged = [f for f in files if f.get("source_tool") == "pdf-merge"]
    assert merged and merged[0]["source_tool_name"]  # resolved to a display name


def test_duplicate_flag(client):
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    r1 = client.post("/workspace/save",
                     files={"file": ("dup.pdf", PDF_BYTES, "application/pdf")},
                     data={"name": "dup.pdf"})
    assert r1.json().get("duplicate") is False
    r2 = client.post("/workspace/save",
                     files={"file": ("dup.pdf", PDF_BYTES, "application/pdf")},
                     data={"name": "dup.pdf"})
    assert r2.json().get("duplicate") is True


def test_save_by_job_id_owner_acl(client):
    """A job tagged with an owner can't be saved by a different identity.
    Under auth OFF the requester id is None, so an owned job is refused."""
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    from app.core.job_manager import job_manager, Job
    from app.config import settings
    import uuid
    jid = uuid.uuid4().hex
    p = settings.temp_dir / f"{jid}_owned.pdf"
    p.write_bytes(PDF_BYTES)
    job = Job(id=jid, tool_id="pdf-merge")
    job.result_path = p
    job.result_filename = "owned.pdf"
    job.status = "done"
    job.owner_id = 999  # belongs to someone else
    job_manager._jobs[jid] = job
    r = client.post("/workspace/save", data={"job_id": jid})
    assert r.status_code == 403


def test_disabled_returns_404(client):
    try:
        ws.save_settings({"enabled": False, "per_user_quota_mb": 500,
                          "max_file_mb": 50, "retention_hours": -1})
        assert client.get("/workspace").status_code == 404
        r = client.post("/workspace/save",
                        files={"file": ("a.pdf", PDF_BYTES, "application/pdf")})
        assert r.status_code == 404
        assert client.get("/workspace/api/list").status_code == 404
    finally:
        ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                          "max_file_mb": 50, "retention_hours": -1})

def test_thumb_missing_returns_placeholder_not_broken(client):
    """縮圖產不出來（檔案不存在 / 不可渲染）→ 回 200 空白 PNG placeholder,
    不可 404/500 讓 <img> 破圖（回歸 2026-06-27 破圖類問題排查）。"""
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    r = client.get("/workspace/thumb/deadbeefdeadbeefdeadbeefdeadbeef")
    assert r.status_code == 200, f"破圖：thumb 回 {r.status_code} 而非 placeholder"
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
