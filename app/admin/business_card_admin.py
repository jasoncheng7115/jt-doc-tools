"""設定 → 名片簿：共用名片簿（名稱、保留期限、成員與角色）與個人名片簿的保留上限。

掛在管理員的 router 底下（整個 router 已經要求管理員）。**管理員只管名片簿本身，看不到裡面的人**
（要看就把自己加進成員）。每個動作都有 API、都記稽核。資料與規則在 `app/tools/business_card_reader/books.py`。
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

NO_STORE = {"Cache-Control": "no-store"}


def _audit(request: Request, event: str, target: str, details: dict) -> None:
    from ..core import audit_db, client_ip, sessions
    audit_db.log_event(event, username=sessions.user_label(getattr(request.state, "user", None)) or "",
                       ip=client_ip.real_client_ip(request), target=target, details=details)


def _state() -> dict:
    from ..core import auth_settings
    from ..tools.business_card_reader import books
    from .seal_request_admin import _describe, _subject_names
    users, groups = _subject_names()
    items = []
    for b in books.admin_list():
        d = {k: b[k] for k in ("id", "kind", "name", "keep_days", "people", "created_at", "updated_at")}
        if b["kind"] == "shared":
            d["members"] = [{**m, **{k: v for k, v in _describe([m], users, groups)[0].items() if k in ("name", "exists")}}
                            for m in b["members"]]
        items.append(d)
    shared = [i for i in items if i["kind"] == "shared"]
    personal = [i for i in items if i["kind"] == "personal"]
    from ..tools.business_card_reader import ai
    return {"ok": True, "auth_enabled": auth_settings.is_enabled(), "personal_max_days": books.personal_max_days(),
            "ai": ai.info(),
            "shared": shared, "personal": {"books": len(personal), "people": sum(i["people"] for i in personal)}}


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        b = None
    if not isinstance(b, dict):
        raise HTTPException(400, "需要 JSON 物件")
    return b


def _run(fn):
    from ..tools.business_card_reader import books, store
    try:
        return fn()
    except books.BookError as e:
        raise HTTPException(400, str(e))
    except store.NotFound:
        raise HTTPException(404, "找不到這本名片簿")


def register(router, templates) -> None:
    from ..tools.business_card_reader import books

    @router.get("/business-cards", response_class=HTMLResponse)
    async def business_cards_page(request: Request):
        return templates.TemplateResponse(request, "admin_business_cards.html", {"request": request})

    @router.get("/business-cards/api/state")
    async def business_cards_state():
        return JSONResponse(_state(), headers=NO_STORE)

    @router.put("/business-cards/api/settings")
    async def business_cards_settings(request: Request):
        body = await _body(request)
        before = books.personal_max_days()
        _run(lambda: books.set_personal_max_days(body.get("personal_max_days")))
        _audit(request, "business_card_admin_settings", "business-cards",
               {"personal_max_days": [before, books.personal_max_days()]})
        return JSONResponse(_state(), headers=NO_STORE)

    @router.put("/business-cards/api/ai")
    async def business_cards_ai(request: Request):
        """AI 協助（選用）：`{"enabled": bool, "mode": "text" | "vision"}`。全站 LLM 沒開時開了也不會出現。"""
        from ..tools.business_card_reader import ai
        body = await _body(request)
        mode = body.get("mode") or "text"
        if mode not in ai.MODES or not isinstance(body.get("enabled"), bool):
            raise HTTPException(400, "enabled 要是 true / false，mode 只能是 text 或 vision")
        before = ai.info()
        ai.set_settings(body["enabled"], mode)
        _audit(request, "business_card_admin_ai", "business-cards",
               {"enabled": [before["enabled"], body["enabled"]], "mode": [before["mode"], mode]})
        return JSONResponse(_state(), headers=NO_STORE)

    @router.post("/business-cards/api/books")
    async def business_cards_create(request: Request):
        from ..core import sessions
        body = await _body(request)
        by = sessions.user_label(getattr(request.state, "user", None)) or ""
        b = _run(lambda: books.create_shared(body.get("name"), body.get("keep_days", 0), by))
        _audit(request, "business_card_admin_book", b["name"], {"action": "create", "book": b["id"], "keep_days": b["keep_days"]})
        return JSONResponse({**_state(), "created": b["id"]}, headers=NO_STORE)

    @router.put("/business-cards/api/books/{book_id}")
    async def business_cards_update(request: Request, book_id: str):
        body = await _body(request)
        b = _run(lambda: books.update_shared(book_id, body.get("name"), body.get("keep_days")))
        _audit(request, "business_card_admin_book", b["name"], {"action": "update", "book": book_id,
                                                                 "keep_days": b["keep_days"]})
        return JSONResponse(_state(), headers=NO_STORE)

    @router.put("/business-cards/api/books/{book_id}/members")
    async def business_cards_members(request: Request, book_id: str):
        body = await _body(request)
        members = body.get("members")
        if not isinstance(members, list):
            raise HTTPException(400, "members 要是陣列")
        out = _run(lambda: books.set_members(book_id, members))
        _audit(request, "business_card_admin_members", book_id, {"members": len(out),
                                                                  "edit": sum(1 for m in out if m["role"] == "edit")})
        return JSONResponse(_state(), headers=NO_STORE)

    @router.delete("/business-cards/api/books/{book_id}")
    async def business_cards_delete(request: Request, book_id: str):
        """刪掉一本共用名片簿（連同裡面的人、名片圖、互動）。要帶 `{"confirm": 名片簿名稱}`，避免手滑。"""
        body = await _body(request)
        b = _run(lambda: books._shared(book_id))
        if body.get("confirm") != b["name"]:
            raise HTTPException(400, "請輸入名片簿的名稱確認刪除")
        n = _run(lambda: books.delete_book(book_id))
        _audit(request, "business_card_admin_book", b["name"], {"action": "delete", "book": book_id, "people": n})
        return JSONResponse(_state(), headers=NO_STORE)
