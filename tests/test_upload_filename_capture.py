"""上傳檔名記進稽核（`main._capture_upload_filename`），而且不可以弄壞後面讀請求內容與回檔案的流程。

這個中介層先把 multipart 的內容整份讀進來、從裡面找 `filename="…"`。原本讀完之後自己把 `request._receive`
換成「每次都回同一份內容」的函式來重播 —— `BaseHTTPMiddleware` 本來就會把讀過的內容快取起來交給後面，
那段重播是多的，而且永遠不回「斷線」：Starlette 1.8 起檔案回應送出時會等斷線，一直收到 `http.request`
就丟「Unexpected message received: http.request」（所以 Starlette 一直被限制在 `<1.8`）。

判準：上傳之後端點照樣讀得到檔案、回得出 PDF；`tool_invoke` 的稽核帶著檔名（中文檔名也對）；
沒有 multipart 的請求不受影響。另外直接驗中介層不再換掉 `_receive`。
"""
from __future__ import annotations

import inspect
import json
import sqlite3
import time

import fitz
import pytest

from app import main as app_main


def _pdf() -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "upload filename capture")
    return doc.tobytes()


def _max_id() -> int:
    from app.core import audit_db
    audit_db.init()
    conn = sqlite3.connect(str(audit_db.audit_db_path()), timeout=5.0)
    try:
        return conn.execute("SELECT COALESCE(MAX(id), 0) FROM audit_events").fetchone()[0]
    finally:
        conn.close()


def _tool_invokes(after: int, timeout: float = 10.0) -> list[dict]:
    """等背景寫入落地：等到有這次之後的 `tool_invoke` 才讀。"""
    from app.core import audit_db
    deadline = time.monotonic() + timeout
    rows: list[dict] = []
    while time.monotonic() < deadline:
        conn = sqlite3.connect(str(audit_db.audit_db_path()), timeout=5.0)
        try:
            rows = [{"target": r[0], "details": json.loads(r[1] or "{}")} for r in conn.execute(
                "SELECT target, details_json FROM audit_events WHERE id > ? AND event_type = 'tool_invoke' ORDER BY id",
                (after,))]
        finally:
            conn.close()
        if rows:
            return rows
        time.sleep(0.1)
    return rows


def test_upload_still_works_and_the_filename_is_audited(admin_session):
    c, _, _ = admin_session
    after = _max_id()
    r = c.post("/tools/pdf-compress/api/pdf-compress",
               files={"file": ("採購合約.pdf", _pdf(), "application/pdf")}, data={"preset": "balanced"})
    assert r.status_code == 200, r.text[:300]
    assert r.content[:4] == b"%PDF", "中介層讀過內容之後，端點照樣要讀得到檔案、回得出檔案"
    rows = [x for x in _tool_invokes(after) if x["target"] == "pdf-compress"]
    assert rows, "沒有 tool_invoke"
    assert rows[-1]["details"].get("filename") == "採購合約.pdf", rows[-1]


def test_several_files_are_all_listed(admin_session):
    c, _, _ = admin_session
    after = _max_id()
    r = c.post("/tools/pdf-merge/api/pdf-merge",
               files=[("files", ("甲.pdf", _pdf(), "application/pdf")), ("files", ("乙.pdf", _pdf(), "application/pdf"))])
    assert r.status_code == 200, r.text[:300]
    rows = [x for x in _tool_invokes(after) if x["target"] == "pdf-merge"]
    assert rows and rows[-1]["details"].get("filenames") == ["甲.pdf", "乙.pdf"], rows
    assert rows[-1]["details"].get("count") == 2


def test_the_middleware_does_not_replace_receive():
    """讀過的內容交給 `BaseHTTPMiddleware` 的快取，不自己重播（見檔頭）。"""
    src = inspect.getsource(app_main._capture_upload_filename)
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "_receive" not in code.split('"""', 2)[-1], "不要再換掉 request._receive"


@pytest.mark.parametrize("ctype", ["application/json", "application/x-www-form-urlencoded"])
def test_non_multipart_requests_are_untouched(admin_session, ctype):
    """不是 multipart 的請求不讀內容、照常處理（這裡只驗不會 5xx）。"""
    c, _, _ = admin_session
    body = b"{}" if ctype == "application/json" else b"a=1"
    r = c.post("/tools/pdf-compress/api/pdf-compress", content=body, headers={"content-type": ctype})
    assert r.status_code < 500, r.status_code
