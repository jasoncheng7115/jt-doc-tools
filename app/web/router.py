from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..core import auth_settings as _as, permissions as _perm

router = APIRouter()


def build_router(templates, tools, app_name: str, version: str) -> APIRouter:
    @router.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        # Reuse the color mapping injected into Jinja globals by main.py,
        # so the home-page colored tile matches the sidebar tile for the
        # same tool id instead of every tool rendering in the default
        # (`.tile-color-0`) indigo.
        nav_lookup = {
            n["id"]: n.get("color", 0)
            for n in (templates.env.globals.get("nav_tools") or [])
        }
        # Filter the tile list by what the viewer can actually use, so
        # non-admins don't see tiles that 403 on click. Auth OFF → show all.
        allowed: set[str] | str = "ALL"
        if _as.is_enabled():
            user = getattr(request.state, "user", None)
            if not user:
                allowed = set()
            else:
                allowed = _perm.effective_tools(user.get("user_id", 0))
        tools_ctx = [
            {
                "id": t.metadata.id,
                "name": t.metadata.name,
                "description": t.metadata.description,
                "icon": t.metadata.icon,
                "category": t.metadata.category,
                "color": nav_lookup.get(t.metadata.id, 0),
            }
            for t in tools
            if allowed == "ALL" or t.metadata.id in allowed
        ]
        # Group tools by category, preserving the order categories first appear
        # in. Each group: {"title": str, "tools": [...]}.
        groups_by_title: dict[str, list[dict]] = {}
        for t in tools_ctx:
            groups_by_title.setdefault(t["category"] or "其他", []).append(t)
        groups = [
            {"title": title, "tools": items}
            for title, items in groups_by_title.items()
        ]
        return templates.TemplateResponse(
            "home.html",
            {
                "request": request,
                "tools": tools_ctx,
                "groups": groups,
                "app_name": app_name,
                "version": version,
            },
        )

    @router.get("/healthz")
    async def healthz():
        return {"ok": True}

    @router.get("/whoami")
    async def whoami(request: Request):
        """Return the current viewer's identity + roles + effective tools.
        Used by the sidebar's account-detail modal."""
        from ..core import auth_db
        nav_lookup = {
            n["id"]: n.get("name", n["id"])
            for n in (templates.env.globals.get("nav_tools") or [])
        }
        if not _as.is_enabled():
            return {
                "auth_enabled": False,
                "username": "(anonymous)",
                "display_name": "單機模式",
                "source": "off",
                "is_admin": True,
                "roles": [],
                "tools": [],
                "tools_all": True,
            }
        user = getattr(request.state, "user", None)
        if not user:
            return {"auth_enabled": True, "anonymous": True}
        uid = user.get("user_id", 0)
        et = _perm.effective_tools(uid)
        # Roles assigned directly to the user subject (groups/OUs aren't shown
        # explicitly here — keep the modal lean; the effective tools list
        # already reflects the union).
        role_ids = _perm.list_roles_for_subject("user", str(uid))
        roles_out = []
        if role_ids:
            conn = auth_db.conn()
            placeholders = ",".join("?" * len(role_ids))
            rows = conn.execute(
                f"SELECT id, display_name FROM roles WHERE id IN ({placeholders}) ORDER BY display_name",
                tuple(role_ids),
            ).fetchall()
            roles_out = [{"id": r["id"], "display_name": r["display_name"]} for r in rows]
        if et == "ALL":
            tools_out = []
            tools_all = True
        else:
            tools_all = False
            tools_out = sorted(
                [{"id": tid, "name": nav_lookup.get(tid, tid)} for tid in et],
                key=lambda x: x["name"],
            )
        return {
            "auth_enabled": True,
            "username": user.get("username"),
            "display_name": user.get("display_name") or user.get("username"),
            "source": user.get("source", "local"),
            "is_admin": (et == "ALL"),
            "roles": roles_out,
            "tools": tools_out,
            "tools_all": tools_all,
        }

    return router
