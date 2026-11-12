"""設定 → MCP（v1.17.1）：總開關、開放的工具、限流、傳輸票證、哪幾張 Token 能連、客戶端設定範例。

掛在 `/admin` 底下（繼承 require_admin）。改設定寫稽核記錄（`settings_change`，對象 `mcp`），不記 Token。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import audit_db


def _actor(r: Request) -> str:
    user = getattr(r.state, "user", None)
    return (user or {}).get("username", "") if isinstance(user, dict) else ""


def _ip(r: Request) -> str:
    from ..core.client_ip import real_client_ip
    return real_client_ip(r)


def _limits() -> dict:
    """設定頁「檔案大小」那一格：AI 傳檔的上限，跟實際檢查用同一份設定（工作區設定、票證）。"""
    from ..core import workspace as ws
    from ..mcp import server, tickets
    s = ws.get_settings()

    def mb(v):
        try:
            v = int(v or 0)
        except (TypeError, ValueError):
            v = 0
        return v if v > 0 else 0
    return {"inline_kb": server.INLINE_MAX // 1024, "file_mb": mb(s.get("max_file_mb")),
            "audio_mb": mb(s.get("max_audio_mb")), "quota_mb": mb(s.get("per_user_quota_mb")),
            "retention_hours": int(s.get("retention_hours") or 0), "ticket_min": tickets.TTL // 60,
            "download_uses": tickets.DOWNLOAD_MAX_USES}


def build_mcp_router(templates) -> APIRouter:
    router = APIRouter()

    @router.get("/mcp", response_class=HTMLResponse)
    async def mcp_page(request: Request):
        from ..core import api_tokens as at, auth_db, auth_settings, notify_settings
        from ..mcp import registry, server, settings as ms
        names = {}
        if auth_settings.is_enabled():
            # 帳號@來源：本機、LDAP、SSO 同名的是不同帳號
            for r in auth_db.conn().execute("SELECT id, username, source FROM users").fetchall():
                names[r["id"]] = f"{r['username']}@{r['source']}"
        tokens = [dict(t, owner=names.get(t.get("owner_user_id"), "")) for t in at.api_tokens.list_tokens()]
        size = len(json.dumps({"tools": list(server.FIXED_TOOLS)}, ensure_ascii=False))
        # 開放的工具：照分類分組，每支用它在側欄的圖示、顏色與中文說明
        from .. import main as _main
        meta = {t.metadata.id: t.metadata for t in _main.tools}
        enabled = set(ms.enabled_tools())
        groups = []
        # 點工具卡片看的說明：跟 AI 呼叫 jtdt_describe 拿到的是同一份（registry.describe），再加輸出種類
        tool_docs = {t.name: dict(registry.describe(t), output_kind=t.output) for t in registry.TOOLS}
        for cid, label in registry.CATEGORIES.items():
            items = []
            for t in registry.TOOLS:
                if t.category != cid or t.stamp:      # AI 用印另有開關（「允許 AI 用印」），不在這裡逐支開
                    continue
                m = meta.get(t.tool_id)
                items.append({"name": t.name, "title": t.title, "on": t.name in enabled,
                              "icon": (m.icon if m else "tool"), "color": _main._tool_color_map.get(t.tool_id, 0),
                              "desc": (m.description if m else ""), "loaded": m is not None})
            if items:
                groups.append({"id": cid, "items": items})
        return templates.TemplateResponse(request, "admin_mcp.html", {
            "request": request,
            "cfg": ms.get(),
            "enabled_tools": ms.enabled_tools(),
            "tools": [t for t in registry.TOOLS if not t.stamp],   # AI 用印另有開關，不算在逐支開放的工具裡
            "categories": registry.CATEGORIES,
            "tokens": tokens,
            "auth_enabled": auth_settings.is_enabled(),
            "site_url": (notify_settings.get().get("site_url") or "").strip(),
            "fixed_count": len(server.FIXED_TOOLS),
            "list_chars": size,
            "list_tokens_est": size // 4,
            "groups": groups,
            "tool_docs": tool_docs,
            "mcp_tokens": sum(1 for t in tokens if t.get("mcp")),
            "limits": _limits(),
        })

    @router.post("/mcp/save")
    async def mcp_save(request: Request):
        from ..core import notify_settings
        from ..mcp import settings as ms
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, "需要 JSON 物件")
        if "site_url" in body:
            url = str(body.get("site_url") or "").strip()
            if url and not url.startswith(("http://", "https://")):
                raise HTTPException(400, "站台網址要以 http:// 或 https:// 開頭")
            notify_settings.save({"site_url": url})
        cfg = ms.save({k: body[k] for k in ("enabled", "tools", "rate_per_minute", "max_concurrent",
                                            "ticket_bind_ip", "ticket_allow_http", "allow_stamp") if k in body})
        audit_db.log_event("settings_change", username=_actor(request), ip=_ip(request), target="mcp",
                           details={"enabled": cfg["enabled"], "tools": cfg["tools"],
                                    "rate_per_minute": cfg["rate_per_minute"],
                                    "max_concurrent": cfg["max_concurrent"],
                                    "ticket_bind_ip": cfg["ticket_bind_ip"],
                                    "ticket_allow_http": cfg["ticket_allow_http"],
                                    "allow_stamp": cfg["allow_stamp"],
                                    "site_url_changed": "site_url" in body})
        return {"ok": True, "cfg": cfg, "site_url": (notify_settings.get().get("site_url") or "")}

    @router.post("/mcp/token")
    async def mcp_token(request: Request):
        """打開 / 關閉某一張 Token 連 MCP（用畫面上的遮罩辨認，不傳 Token 本身）。"""
        from ..core import api_tokens as at
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("preview"), str):
            raise HTTPException(400, "需要 preview")
        row = next((t for t in at.api_tokens.list_tokens() if t.get("token_preview") == body["preview"]), None)
        if row is None:
            raise HTTPException(404, "找不到這張 Token")
        label = str(row.get("label") or "")[:64]
        if "stamp" in body:
            # AI 用印：要先綁使用者（用印要追得到是誰），而且這張 Token 要能連 MCP
            stamp = body.get("stamp") is True
            if stamp and not row.get("owner_user_id"):
                raise HTTPException(400, "這張 Token 還沒綁定使用者，不能允許用印")
            if stamp and not row.get("mcp"):
                raise HTTPException(400, "先允許這張 Token 連 MCP，才能允許用印")
            at.api_tokens.set_stamp(body["preview"], stamp)
            audit_db.log_event("settings_change", username=_actor(request), ip=_ip(request), target="mcp",
                               details={"token": label, "stamp": stamp})
            return {"ok": True, "stamp": stamp}
        allowed = body.get("allowed") is True
        at.api_tokens.set_mcp(body["preview"], allowed)
        if not allowed and row.get("stamp"):
            at.api_tokens.set_stamp(body["preview"], False)     # 不能連 MCP 就一併收回用印
        audit_db.log_event("settings_change", username=_actor(request), ip=_ip(request), target="mcp",
                           details={"token": label, "mcp": allowed})
        return {"ok": True, "mcp": allowed}

    # ---- AI 用印紀錄（v1.17.38）----

    def _ai_rows(request: Request) -> list[dict]:
        import time as _t
        from ..core import ai_stamp
        q = request.query_params
        def day(v: str, end: bool = False) -> float:
            try:
                return _t.mktime(_t.strptime(v, "%Y-%m-%d")) + (86400 if end else 0)
            except (TypeError, ValueError):
                return 0.0
        uid = q.get("user_id") or ""
        rows = ai_stamp.list_all(user_id=int(uid) if uid.isdigit() else None, asset_id=q.get("asset_id") or "",
                                 since=day(q.get("since") or ""), until=day(q.get("until") or "", True))
        return rows

    @router.get("/mcp/ai-stamp", response_class=HTMLResponse)
    async def ai_stamp_log_page(request: Request):
        from ..core.asset_manager import asset_manager
        seals = [{"id": a.id, "name": a.name} for t in ("stamp", "signature") for a in asset_manager.list(type=t)]
        return templates.TemplateResponse(request, "admin_ai_stamp_log.html", {"request": request, "seals": seals})

    @router.get("/mcp/ai-stamp.json")
    async def ai_stamp_log_json(request: Request):
        import asyncio as _a
        rows = await _a.to_thread(_ai_rows, request)
        return JSONResponse({"items": rows}, headers={"Cache-Control": "no-store"})

    @router.get("/mcp/ai-stamp.csv")
    async def ai_stamp_log_csv(request: Request):
        import asyncio as _a
        import csv
        import io
        import time as _t
        from fastapi.responses import Response
        from ..core.http_utils import content_disposition
        rows = await _a.to_thread(_ai_rows, request)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["request_id", "created_at", "user", "token", "tool", "file", "seals", "pages", "placements",
                    "needs_confirmation", "status", "decided_by", "decided_at", "ip"])
        fmt = lambda v: _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(v)) if v else ""
        from ..core import csv_safe
        for r in rows:
            # 檔名由 AI 決定、使用者名稱與章名也是人填的：試算表會把 = + - @（含 tab、換行、前置空白）開頭的
            # 格子當成公式 → 整列走共用的 csv_safe
            w.writerow(csv_safe.row([r["request_id"], fmt(r["created_at"]), r["user"], r["token"], r["tool"],
                                     r["filename"], "、".join(a.get("name", "") for a in r["assets"]),
                                     " ".join(str(p + 1) for p in r["pages"]), r["placements"],
                                     r["needs_confirmation"], r["status"], r["decided_by"] or "",
                                     fmt(r["decided_at"]), r["ip"]]))
        audit_db.log_event("export", username=_actor(request), ip=_ip(request), target="ai_stamp_log",
                           details={"rows": len(rows)})
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": content_disposition("ai-stamp-log.csv"),
                                 "Cache-Control": "no-store"})

    return router
