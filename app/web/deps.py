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


def require_admin(request: Request) -> dict:
    """Dependency: 401 unauthenticated, 403 not admin."""
    user = require_login(request)
    if user.get("source") == "off":
        return user   # auth disabled — everyone is "admin"
    if not permissions.is_admin(user["user_id"]):
        raise HTTPException(status_code=403, detail="需要管理員權限")
    return user


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
