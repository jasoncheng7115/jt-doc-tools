"""上傳有開啟密碼的 PDF：檢查、解開、權限、稽核。

使用者 2026-11-02：拉進一份設了開啟密碼的 PDF，用印頁什麼反應都沒有。
畫面端（`static/js/pdf_unlock.js`）先看檔頭檔尾有沒有 `/Encrypt`，有的話問
`/pdf-password/check`；要密碼時讓使用者輸入、送 `/tools/pdf-decrypt/unlock` 解開。

這裡驗伺服器那兩支：
* 只有擁有者密碼（限制列印 / 編輯）的檔**不算**要密碼 —— 那種打得開，不要攔；
* 檢查不留暫存檔；
* 解開要密碼對、回的檔真的沒有密碼；密碼錯回 400；每次都記稽核、不記密碼；
* **沒有「PDF 密碼解除」權限的人解不開**（路徑在工具底下，由權限中介層擋），檢查也說 `can_unlock: false`；
* 用印預覽遇到有密碼的檔回 400 說清楚，不是 500。
"""
from __future__ import annotations

import json
import time
from io import BytesIO

import fitz
import pytest
from fastapi.testclient import TestClient

import app.main as app_main

PW = "s3cret-1234"


def _pdf(user_pw: str = "", owner_pw: str = "", text: str = "機密文件") -> bytes:
    d = fitz.open()
    d.new_page(width=595, height=842).insert_text((72, 100), "Doc", fontsize=20)
    buf = BytesIO()
    if user_pw or owner_pw:
        d.save(buf, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=user_pw,
               owner_pw=owner_pw or user_pw + "-owner",
               permissions=fitz.PDF_PERM_ACCESSIBILITY)
    else:
        d.save(buf)
    d.close()
    return buf.getvalue()


def _check(client, data: bytes, name="a.pdf"):
    return client.post("/pdf-password/check", files={"file": (name, data, "application/pdf")})


def test_user_password_pdf_needs_a_password(client, auth_off):
    r = _check(client, _pdf(user_pw=PW))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["needs_password"] is True
    assert j["can_unlock"] is True                       # 認證關閉：誰都能用
    assert j["unlock_url"] == "/tools/pdf-decrypt/unlock"


def test_owner_only_pdf_is_not_blocked(client, auth_off):
    data = _pdf(owner_pw="owner-only")
    assert b"/Encrypt" in data                           # 畫面那一關會送來問
    r = _check(client, data)
    assert r.status_code == 200 and r.json()["needs_password"] is False


def test_plain_pdf_and_non_pdf(client, auth_off):
    assert _check(client, _pdf()).json()["needs_password"] is False
    assert _check(client, b"hello world", "x.pdf").status_code == 400


def test_check_leaves_no_temp_file(client, auth_off):
    from app.config import settings
    _check(client, _pdf(user_pw=PW))
    assert not list(settings.temp_dir.glob("pwcheck_*"))


def _audit_rows(since: float, want: int = 1):
    """稽核是非同步寫入：等到真的寫進去。"""
    from app.core import audit_db
    rows = []
    for _ in range(50):
        rows = [dict(r) for r in audit_db.conn().execute(
            "SELECT ts, username, ip, target, details_json FROM audit_events "
            "WHERE event_type='pdf_unlock' AND ts >= ? ORDER BY id", (since,)).fetchall()]
        if len(rows) >= want:
            return rows
        time.sleep(0.1)
    return rows


def test_unlock_with_right_and_wrong_password(client, auth_off):
    since = time.time() - 1
    locked = _pdf(user_pw=PW)
    bad = client.post("/tools/pdf-decrypt/unlock", data={"password": "nope", "from_tool": "pdf-stamp"},
                      files={"file": ("合約.pdf", locked, "application/pdf")})
    assert bad.status_code == 400
    ok = client.post("/tools/pdf-decrypt/unlock", data={"password": PW, "from_tool": "pdf-stamp"},
                     files={"file": ("合約.pdf", locked, "application/pdf")})
    assert ok.status_code == 200, ok.text
    assert ok.headers["content-type"].startswith("application/pdf")
    assert "no-store" in ok.headers.get("cache-control", "")
    with fitz.open(stream=ok.content, filetype="pdf") as d:
        assert not d.needs_pass and not d.is_encrypted
        assert d.page_count == 1
    rows = _audit_rows(since, want=2)
    assert {json.loads(r["details_json"])["ok"] for r in rows} == {True, False}
    for r in rows:
        assert r["target"] == "pdf-stamp"
        assert PW not in json.dumps(r, ensure_ascii=False), "稽核不可以記密碼"


def test_unlock_from_tool_is_sanitised(client, auth_off):
    since = time.time() - 1
    r = client.post("/tools/pdf-decrypt/unlock", data={"password": PW, "from_tool": "../../x<script>"},
                    files={"file": ("sanitise-check.pdf", _pdf(user_pw=PW), "application/pdf")})
    assert r.status_code == 200
    rows = [r for r in _audit_rows(since) if "sanitise-check.pdf" in r["details_json"]]
    for _ in range(30):
        if rows:
            break
        time.sleep(0.1)
        rows = [r for r in _audit_rows(since) if "sanitise-check.pdf" in r["details_json"]]
    assert rows and all(r["target"] == "pdf-decrypt" for r in rows), rows


def _client_for(username: str, role: str) -> TestClient:
    from app.core import permissions, sessions, user_manager
    uid = user_manager.create_local(username, username, "UserPass1234", roles=[role])
    permissions.invalidate_cache()
    tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, tok)
    return c


def test_without_decrypt_permission_cannot_unlock(admin_session):
    clerk = _client_for("pw-clerk", "clerk")             # 文管：沒有 PDF 密碼解除
    finance = _client_for("pw-finance", "finance")       # 財務：有
    locked = _pdf(user_pw=PW)

    j = _check(clerk, locked).json()
    assert j["needs_password"] is True and j["can_unlock"] is False
    r = clerk.post("/tools/pdf-decrypt/unlock", data={"password": PW},
                   files={"file": ("a.pdf", locked, "application/pdf")})
    assert r.status_code == 403

    j = _check(finance, locked).json()
    assert j["needs_password"] is True and j["can_unlock"] is True
    r = finance.post("/tools/pdf-decrypt/unlock", data={"password": PW},
                     files={"file": ("a.pdf", locked, "application/pdf")})
    assert r.status_code == 200


def test_check_requires_login(admin_session):
    anon = TestClient(app_main.app, follow_redirects=False)   # 跟著轉址會拿到登入頁的 200
    r = _check(anon, _pdf(user_pw=PW))
    assert r.status_code in (401, 302, 303, 307, 403), r.status_code


def test_stamp_preview_rejects_locked_pdf_with_400(client, auth_off):
    r = client.post("/tools/pdf-stamp/preview",
                    files={"file": ("a.pdf", _pdf(user_pw=PW), "application/pdf")})
    assert r.status_code == 400
    assert "密碼" in r.json()["detail"]
