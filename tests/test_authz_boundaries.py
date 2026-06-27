"""登入後的授權邊界測試（2026-06-27 使用者要求）：

① 垂直越權：已登入的「非 admin」一般使用者不可碰任何 admin 頁 / admin 寫入。
② 工具權限：default-user 沒有的工具（pdf-fill / pdf-stamp）要被擋,有的要能用。
③ 水平越權：A 使用者不可存取 B 使用者的上傳檔 / 工作區檔。

在獨立 TestClient + 臨時資料庫做,不碰生產機。CSRF 在 conftest 以
JTDT_CSRF_DISABLE=1 關閉,故 POST 不需 token（驗的是授權,非 CSRF）。
"""
from __future__ import annotations

from io import BytesIO

import fitz
from fastapi.testclient import TestClient

from app import main as app_main


def _pdf() -> bytes:
    d = fitz.open()
    d.new_page().insert_text((72, 72), "secret")
    b = BytesIO()
    d.save(b)
    d.close()
    return b.getvalue()


def _user_client(username, password="UserPass1234", roles=None):
    """建一般使用者（預設 default-user 角色）+ 回已登入 client。"""
    from app.core import user_manager, sessions
    uid = user_manager.create_local(username, username, password, roles=roles)
    token, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, token)
    return uid, c


# ───────────────────────── ① 垂直越權 ─────────────────────────
def test_regular_user_cannot_view_admin_pages(admin_session):
    _, c = _user_client("alice_admin_view")
    for p in ["/admin/", "/admin/users", "/admin/audit", "/admin/auth-settings",
              "/admin/permissions", "/admin/log-forward", "/admin/api-tokens",
              "/admin/retention", "/admin/sso", "/admin/system-status"]:
        r = c.get(p, follow_redirects=False)
        assert r.status_code != 200, f"非 admin 竟然看得到 {p}（status 200）"
        assert r.status_code in (401, 403, 302, 303, 307), f"{p} → {r.status_code}"


def test_regular_user_cannot_call_admin_writes(admin_session):
    _, c = _user_client("alice_admin_write")
    # 改站名
    r = c.post("/admin/branding/site-name", json={"name": "hacked"})
    assert r.status_code in (401, 403, 302, 303), f"改站名 → {r.status_code}"
    # 關閉認證（最敏感）
    r = c.post("/admin/auth-settings/disable")
    assert r.status_code in (401, 403, 302, 303), f"關認證 → {r.status_code}"
    # 列出所有使用者（資料外洩）
    r = c.get("/admin/api/users", follow_redirects=False)
    assert r.status_code in (401, 403, 302, 303, 404), f"列 users → {r.status_code}"


def test_regular_user_cannot_create_admin_token(admin_session):
    _, c = _user_client("alice_token")
    r = c.post("/admin/api-tokens/create", json={"name": "evil"})
    assert r.status_code in (401, 403, 302, 303, 404), f"建 token → {r.status_code}"


# ───────────────────────── ② 工具權限 ─────────────────────────
def test_default_user_blocked_from_unpermitted_tools(admin_session):
    _, c = _user_client("bob_tools")
    # default-user 不含 pdf-fill / pdf-stamp
    for tool in ["pdf-fill", "pdf-stamp"]:
        r = c.get(f"/tools/{tool}/", follow_redirects=False)
        assert r.status_code != 200, f"default-user 竟能開 {tool}"
        assert r.status_code in (401, 403, 302, 303, 307), f"{tool} → {r.status_code}"


def test_default_user_can_use_permitted_tool(admin_session):
    _, c = _user_client("bob_ok")
    # 含 v1.12.41 補進 default-user 的 5 個無害工具
    for tool in ["pdf-merge", "pdf-wordcount", "submission-check",
                 "pdf-annotations", "pdf-annotations-flatten",
                 "pdf-annotations-strip"]:
        r = c.get(f"/tools/{tool}/", follow_redirects=False)
        assert r.status_code == 200, f"default-user 開不了被授權的 {tool} → {r.status_code}"


def test_unpermitted_tool_api_also_blocked(admin_session):
    """工具頁擋了,後端動作端點也要擋（不能只擋 UI）。"""
    _, c = _user_client("bob_api")
    r = c.post("/tools/pdf-fill/detect",
               files={"file": ("a.pdf", _pdf(), "application/pdf")})
    assert r.status_code in (401, 403, 302, 303), f"pdf-fill 動作端點 → {r.status_code}"


# ───────────────────────── ③ 水平越權 ─────────────────────────
def test_cross_user_workspace_file_blocked(admin_session):
    import app.core.workspace as ws
    ws.save_settings({"enabled": True, "per_user_quota_mb": 500,
                      "max_file_mb": 50, "retention_hours": -1})
    _, ca = _user_client("alice_ws")
    _, cb = _user_client("bob_ws")
    # A 存一個工作區檔
    r = ca.post("/workspace/save",
                data={"name": "secret"},
                files={"file": ("s.pdf", _pdf(), "application/pdf")})
    assert r.status_code == 200, r.text
    fid = r.json()["file"]["file_id"]
    # A 自己拿得到
    assert ca.get(f"/workspace/file/{fid}", follow_redirects=False).status_code == 200
    # B 不可下載 A 的檔（水平越權）
    rb = cb.get(f"/workspace/file/{fid}", follow_redirects=False)
    assert rb.status_code in (401, 403, 404), f"B 拿到 A 的工作區檔！→ {rb.status_code}"


def test_cross_user_upload_file_blocked(admin_session):
    """A 用 pdf-editor 上傳產生 upload_id,B 不可用該 id 取原檔（upload_owner ACL）。"""
    _, ca = _user_client("alice_up")
    _, cb = _user_client("bob_up")
    r = ca.post("/tools/pdf-editor/load",
                files={"file": ("a.pdf", _pdf(), "application/pdf")})
    assert r.status_code == 200, r.text
    uid = r.json().get("upload_id")
    assert uid, r.text
    # A 自己拿得到原檔
    assert ca.get(f"/tools/pdf-editor/file/{uid}", follow_redirects=False).status_code == 200
    # B 用 A 的 upload_id 取原檔 → 必須被擋（水平越權）
    rb = cb.get(f"/tools/pdf-editor/file/{uid}", follow_redirects=False)
    assert rb.status_code in (401, 403, 404), f"B 拿到 A 的上傳檔！→ {rb.status_code}"
