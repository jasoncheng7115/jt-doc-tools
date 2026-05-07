"""FastAPI dependencies for auth-aware routes.

Use these in route handlers (and admin pages) to enforce login + role
membership. They read `request.state.user` which the auth middleware
populates after a successful session lookup.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from ..core import permissions, auth_settings


def get_current_user(request: Request) -> Optional[dict]:
    """Returns the logged-in user dict, or None when auth is OFF / not logged in."""
    return getattr(request.state, "user", None)


def require_login(request: Request) -> dict:
    """Dependency: 401 if unauthenticated. Use only inside the auth-on path —
    when auth is OFF this still raises (the middleware never set state.user)."""
    user = get_current_user(request)
    if user is None:
        if not auth_settings.is_enabled():
            # Auth is off — caller probably forgot to gate this. Treat as
            # an anonymous "synthetic admin" so existing tools keep working
            # exactly as before.
            return {"user_id": 0, "username": "(anonymous)",
                    "display_name": "(anonymous)", "source": "off",
                    "is_admin_seed": True}
        raise HTTPException(status_code=401, detail="請先登入")
    return user


# v1.5.0 起改強職責分離：
#
# AUDITOR_SHARED：admin 與稽核員都看得到（系統運維 + 合規共同需要）
#   - /admin/audit         登入 / 操作稽核紀錄
#   - /admin/system-status CPU / RAM / 網路 / 各 user 檔案用量
#
# AUDITOR_EXCLUSIVE：**只有稽核員看得到**（檔案內容 / user 隱私 — admin 也擋）
#   - /admin/history/fill        表單填寫歷史（含 user 填的真實資料）
#   - /admin/history/stamp       用印簽名歷史
#   - /admin/history/watermark   浮水印歷史
#   - /admin/uploads             上傳檔案記錄（含 user 上傳的檔名）
#
# admin 可看 AUDITOR_SHARED + 其他 admin 設定區，**不可**看 AUDITOR_EXCLUSIVE。
# 設計理由：合規上 admin 雖管系統，但不該偷看 user 的真實檔案。稽核員是
# 唯一被授權可以查 user 上傳什麼 / 處理過什麼歷史的角色。
#
# 註：/admin/history（不帶子路徑）會 redirect 到 /admin/history/fill，redirect
# 動作本身（status 302）admin 走得通，但目標頁 admin 會被擋 403。
_AUDITOR_SHARED_PREFIXES = (
    "/admin/audit",
    "/admin/system-status",
)
_AUDITOR_EXCLUSIVE_PREFIXES = (
    "/admin/history",   # 涵蓋 fill / stamp / watermark 三個子路徑
    "/admin/uploads",
)


def require_admin(request: Request) -> dict:
    """Dependency: 401 unauthenticated, 403 not authorised. Auth OFF →
    everyone passes（單機模式不分角色）。

    路由分三層：
      - AUDITOR_EXCLUSIVE：admin 也 403，**只有 auditor**通過
      - AUDITOR_SHARED：admin 與 auditor 都通過
      - 其他 /admin/*：只有 admin 通過

    Auditor 通過時寫一筆 `auditor_view` audit event，admin 看得到稽核員看了
    什麼，稽核員自己不能刪（UI 沒刪除端點）。"""
    user = require_login(request)
    if user.get("source") == "off":
        return user   # auth disabled — everyone is "admin"
    uid = user["user_id"]
    path = request.url.path or ""
    is_admin_user = permissions.is_admin(uid)
    is_aud_user = permissions.is_auditor(uid)

    # 1. AUDITOR_EXCLUSIVE：只有 auditor 過
    if any(path.startswith(p) for p in _AUDITOR_EXCLUSIVE_PREFIXES):
        if not is_aud_user:
            raise HTTPException(
                status_code=403,
                detail="此頁僅限稽核員查看（含 user 隱私資料，admin 也不可看）",
            )
        _log_auditor_view(user, request, path)
        return user

    # 2. AUDITOR_SHARED：admin 或 auditor 都過
    if any(path.startswith(p) for p in _AUDITOR_SHARED_PREFIXES):
        if is_admin_user:
            return user
        if is_aud_user:
            _log_auditor_view(user, request, path)
            return user
        raise HTTPException(status_code=403, detail="需要管理員或稽核員權限")

    # 3. 其他 admin 頁面：只有 admin 過
    if is_admin_user:
        return user
    raise HTTPException(status_code=403, detail="需要管理員權限")


def _log_auditor_view(user: dict, request: Request, path: str) -> None:
    try:
        from ..core import audit_db, sessions as _ses
        audit_db.log_event(
            "auditor_view",
            username=_ses.user_label(user) or user.get("username") or str(user.get("user_id", 0)),
            ip=(request.client.host if request.client else ""),
            target=path,
            details={"method": request.method,
                     "query": str(request.url.query or "")[:200]},
        )
    except Exception:
        pass  # audit log failure must not block the page


def require_tool(tool_id: str):
    """Dependency factory: 401 unauthenticated, 403 if user doesn't have
    permission to use this tool. Returns a dependency callable."""
    def _check(request: Request) -> dict:
        user = require_login(request)
        if user.get("source") == "off":
            return user
        if not permissions.user_can_use_tool(user["user_id"], tool_id):
            raise HTTPException(status_code=403, detail="您沒有使用此工具的權限")
        return user
    return _check
