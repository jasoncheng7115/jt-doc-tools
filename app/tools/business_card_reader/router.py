"""名片辨識：頁面與 API。

存取控制：工具權限由中介層依 `/tools/business-card-reader/` 判斷；批次只看得到自己的（`store` 每個查詢都帶擁有者）。
編號錯誤、別人的、不存在的，一律 404（不分原因）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.routing import APIRoute

from . import books, pipeline, store


class _LocalizedRoute(APIRoute):
    """這支工具的 API 錯誤訊息照使用者的介面語言回（訊息原文是中文；檔名、數字照原樣填回）。"""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except HTTPException as e:
                if isinstance(e.detail, str):
                    from ...core import i18n, ui_locale
                    try:
                        e.detail = i18n.translate_message(e.detail, ui_locale.resolve(request))
                    except Exception:                    # noqa: BLE001 —— 翻不了就給中文，不可以因此變 500
                        pass
                raise
        return wrapped


router = APIRouter(route_class=_LocalizedRoute)
log = logging.getLogger(__name__)

TOOL_ID = "business-card-reader"
#: 單檔上限（手機照片、掃描 PDF）
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_TOTAL_BYTES = 400 * 1024 * 1024


def owner_of(request: Request) -> tuple[str, str]:
    """（擁有者鍵, 顯示名稱）。啟用登入時是帳號；單機（未啟用登入）是共用的 `local`。"""
    from ...core import auth_settings, sessions
    user = getattr(request.state, "user", None)
    if auth_settings.is_enabled():
        if not user or not user.get("user_id"):
            raise HTTPException(401, "請先登入")
        return f"u{int(user['user_id'])}", sessions.user_label(user) or ""
    return "local", ""


def _nf():
    return HTTPException(404, "找不到這個名片批次")


def _audit(request: Request, event: str, target: str, **details) -> None:
    try:
        from ...core import audit_db
        from ...core.client_ip import real_client_ip
        from ...core import sessions
        audit_db.log_event(event, username=sessions.user_label(getattr(request.state, "user", None)) or "",
                           ip=real_client_ip(request), target=target, details=details)
    except Exception:                                    # noqa: BLE001
        log.exception("名片辨識稽核寫入失敗")


def _origin(request: Request) -> str:
    """送出者瀏覽器所在的網址（完成通知的連結用，不拿來做任何判斷）。"""
    from ...core.http_utils import browser_origin
    try:
        return browser_origin(request.headers)
    except Exception:  # noqa: BLE001
        return ""


def _batch_view(owner: str, bid: str, lite: bool = False) -> dict:
    """批次內容。`lite`：不帶每張卡的 OCR 文字行與 QR（辨識中每 1.5 秒輪詢一次，300 張卡的完整內容約 1 MB）。"""
    b = store.get_batch(owner, bid)
    pages = store.pages(bid)
    cards = store.cards(bid)
    contacts = store.contacts(bid)
    by_page: dict[str, list] = {}
    for c in cards:
        by_page.setdefault(c["page_id"], []).append({
            "id": c["id"], "idx": c["idx"], "quad": c["quad"], "rotate": c["rotate"], "source": c["source"],
            "flags": sorted(set(c["flags"]) | set(c["ocr"].get("flags") or [])), "status": c["status"],
            "error": c["error"], "memo": c["memo"], "engine": c["ocr"].get("engine"),
            "secs": c["ocr"].get("secs"), "ocr_secs": c["ocr"].get("ocr_secs"),
            "lines": [] if lite else (c["ocr"].get("lines") or []), "qr": [] if lite else (c["ocr"].get("qr") or []),
            "size": c["ocr"].get("size"),
        })
    return {
        "id": b["id"], "mode": b["mode"], "status": b["status"], "notes": b["notes"],
        "created_at": b["created_at"], "updated_at": b["updated_at"], "expires_at": store.expires_at_of(b["created_at"]),
        "pages": [{"id": p["id"], "idx": p["idx"], "filename": p["filename"], "page_no": p["page_no"],
                   "width": p["width"], "height": p["height"], "status": p["status"],
                   "multi_hint": bool(p["multi_hint"]), "cards": by_page.get(p["id"], [])} for p in pages],
        "contacts": [{k: v for k, v in c.items() if k != "merged"} | {"merged": len(c.get("merged") or [])} for c in contacts],
        "merge_hints": _merge_hints(contacts, pages, cards),
        "counts": {
            "files": len({p["filename"] for p in pages}), "pages": len(pages), "cards": len(cards),
            "cards_done": sum(1 for c in cards if c["status"] == "done"),
            "cards_failed": sum(1 for c in cards if c["status"] == "failed"),
            "cards_pending": sum(1 for c in cards if c["status"] in ("pending", "ocr")),
            "contacts": sum(1 for c in contacts if c["status"] != "excluded"),
            "confirmed": sum(1 for c in contacts if c["status"] == "confirmed"),
            "check": sum(1 for c in contacts if c["status"] == "check"),
        },
    }


def _merge_hints(contacts: list[dict], pages: list[dict], cards: list[dict]) -> list[dict]:
    """同一批裡可能是同一位的兩筆：Email 或手機相同。兩張卡相鄰、一張以英文為主一張以中文為主 → 多半是正反面。"""
    from .extract import normalize_phone
    order = {}
    pidx = {p["id"]: p["idx"] for p in pages}
    for i, c in enumerate(sorted(cards, key=lambda c: (pidx.get(c["page_id"], 0), c["idx"]))):
        order[c["id"]] = i
    live = [c for c in contacts if c["status"] != "excluded"]

    def keys(c):
        ks = set()
        for f in c["fields"]:
            if f.get("status") == "deleted" or not (f.get("value") or "").strip():
                continue
            if f["key"] == "email":
                ks.add("e:" + f["value"].strip().lower())
            elif f["key"] == "mobile":
                d = normalize_phone(f["value"]).lstrip("+")
                if len(d) >= 8:
                    ks.add("m:" + d[-9:])
        return ks

    def latin_share(c):
        txt = "".join((f.get("value") or "") for f in c["fields"] if f["key"] in ("name", "name_en", "company", "company_en", "title"))
        letters = sum(ch.isascii() and ch.isalpha() for ch in txt)
        cjk = sum("一" <= ch <= "鿿" for ch in txt)
        return letters / max(1, letters + cjk * 2)
    out = []
    ks = {c["id"]: keys(c) for c in live}
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            shared = ks[a["id"]] & ks[b["id"]]
            if not shared:
                continue
            adjacent = abs(order.get(a["card_id"], -9) - order.get(b["card_id"], -99)) == 1
            mixed = abs(latin_share(a) - latin_share(b)) >= 0.5
            out.append({"a": a["id"], "b": b["id"], "reason": "front_back" if adjacent and mixed else "duplicate",
                        "by": "email" if any(k.startswith("e:") for k in shared) else "mobile"})
            if len(out) >= 50:
                return out
    return out


def _wrap(fn):
    try:
        return fn()
    except store.NotFound:
        raise _nf()
    except store.LimitError as e:
        raise HTTPException(429, str(e))
    except pipeline.InputError as e:
        raise HTTPException(400, str(e))
    except books.Forbidden:
        raise HTTPException(403, "這本名片簿你只能查詢，不能修改")
    except books.BookError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- 頁面

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from ...core import auth_settings
    from ...core.image_utils import heif_available
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "business_card_reader.html", {
        "request": request, "heif": heif_available(), "accept": ",".join(sorted(pipeline.accepted_exts())),
        "max_files": store.MAX_FILES, "max_pages": store.MAX_PAGES, "max_cards": store.MAX_CARDS,
        "keep_days": store.keep_days(), "shared_env": not auth_settings.is_enabled(),
    })


# --------------------------------------------------------------------------- 批次

async def _read_files(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    out, total = [], 0
    for f in files or []:
        data = await f.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"「{f.filename}」超過 {MAX_FILE_BYTES // 1024 // 1024} MB")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(413, f"這一批合計超過 {MAX_TOTAL_BYTES // 1024 // 1024} MB，請分批上傳")
        if data:
            out.append(((f.filename or "image.jpg")[:200], data))
    return out


@router.post("/api/batches")
async def create_batch(request: Request, files: list[UploadFile] = File(...), mode: str = Form("single"),
                       event: str = Form(""), source: str = Form(""), tags: str = Form(""), memo: str = Form("")):
    """建立辨識批次：`mode`＝`single`（每張圖片一張名片，直接辨識）/ `multi`（每張圖片多張名片，先確認框位）。
    `event` / `source` / `tags` / `memo` 是人工補充，套用到這批每一張（不是名片原文）。"""
    if mode not in ("single", "multi"):
        raise HTTPException(400, "mode 只能是 single 或 multi")
    owner, label = owner_of(request)
    data = await _read_files(files)
    notes = {"event": event, "source": source, "tags": tags, "memo": memo}
    bid = await asyncio.to_thread(lambda: _wrap(lambda: pipeline.create(owner, label, mode, data, notes,
                                                                                    origin=_origin(request), request=request)))
    view = await asyncio.to_thread(_batch_view, owner, bid)
    _audit(request, "business_card_batch", TOOL_ID, batch=bid, mode=mode, files=len(data),
           pages=view["counts"]["pages"], cards=view["counts"]["cards"])
    return JSONResponse(view, status_code=201)


@router.post("/api/business-card-reader")
async def recognize_once(request: Request, files: list[UploadFile] = File(...), mode: str = Form("single"),
                         event: str = Form(""), source: str = Form(""), tags: str = Form(""), memo: str = Form(""),
                         wait: int = Form(120)):
    """一次完成：上傳 → 切卡 → 辨識 → 回聯絡人 JSON（給程式與 MCP 用，不經過畫面的確認範圍與校對）。

    `mode=multi` 時自動找到的每一張都直接辨識（沒有人確認範圍）。等最多 `wait` 秒（10～600）；
    還沒做完就回目前的結果與 `done: false`，之後用批次 API 接著讀。**結果還沒經過人工校對**：
    `status` 是 `check` 的欄位是規則判斷沒把握的，要人看過再用。批次照保留期限自動刪除。
    """
    if mode not in ("single", "multi"):
        raise HTTPException(400, "mode 只能是 single 或 multi")
    wait = max(10, min(600, int(wait or 120)))
    owner, label = owner_of(request)
    data = await _read_files(files)
    notes = {"event": event, "source": source, "tags": tags, "memo": memo}

    def work():
        bid = pipeline.create(owner, label, mode, data, notes, origin=_origin(request), request=request)
        if mode == "multi":
            pipeline.start(owner, bid, request=request)
        end = time.time() + wait
        while time.time() < end:
            b = store.get_batch(owner, bid)
            if b["status"] == "review" and not store.pending_cards(bid):
                break
            time.sleep(0.5)
        return bid
    bid = await asyncio.to_thread(lambda: _wrap(work))
    view = await asyncio.to_thread(_batch_view, owner, bid)
    done = view["status"] == "review" and view["counts"]["cards_pending"] == 0
    _audit(request, "business_card_batch", TOOL_ID, batch=bid, mode=mode, files=len(data), via="api",
           pages=view["counts"]["pages"], cards=view["counts"]["cards"])

    def slim(c):
        return {"id": c["id"], "status": c["status"], "notes": c["notes"],
                "fields": [{k: f[k] for k in ("key", "value", "status", "reason", "ext", "label", "zip") if f.get(k) not in (None, "")}
                           for f in c["fields"] if f.get("status") != "deleted"]}
    return {"batch_id": bid, "done": done, "counts": view["counts"],
            "contacts": [slim(c) for c in view["contacts"] if c["status"] != "excluded"],
            "review_url": f"/tools/business-card-reader/#batch={bid}",
            "note": "未經人工校對：status 為 check 的欄位請人確認後再使用"}


@router.get("/handoff-qr")
async def handoff_qr(request: Request):
    """「用手機接續」：這一頁網址的 QR Code（手機掃了用自己的帳號登入、拍完的批次電腦這邊就看得到）。"""
    from ...core import handoff_qr
    return handoff_qr.qr_response(request, "/tools/business-card-reader/")


@router.get("/api/batches")
async def list_batches(request: Request):
    owner, _ = owner_of(request)
    rows = await asyncio.to_thread(store.list_batches, owner)
    return {"items": [{"id": r["id"], "mode": r["mode"], "status": r["status"], "notes": r["notes"],
                       "created_at": r["created_at"], "expires_at": store.expires_at_of(r["created_at"]), "pages": r["pages"],
                       "cards": r["cards"], "contacts": r["contacts"]} for r in rows]}


@router.get("/api/batches/{bid}")
async def get_batch(request: Request, bid: str, lite: bool = False):
    """批次內容；`lite=true` 不帶 OCR 文字行（辨識中輪詢用）。"""
    owner, _ = owner_of(request)
    return await asyncio.to_thread(lambda: _wrap(lambda: _batch_view(owner, bid, lite=lite)))


@router.patch("/api/batches/{bid}")
async def update_batch(request: Request, bid: str):
    """整批的人工補充（活動 / 來源 / 標籤 / 備註）。`apply_to_contacts=true` 時一併寫到每位聯絡人（已填的不覆蓋）。"""
    owner, _ = owner_of(request)
    body = await _json(request)
    notes = body.get("notes") or {}

    def work():
        store.set_batch(owner, bid, notes=notes)
        if body.get("apply_to_contacts"):
            clean = store.clean_notes(notes)
            for c in store.contacts(bid):
                n = dict(c["notes"] or {})
                for k in ("event", "source", "memo"):
                    if not n.get(k) and clean.get(k):
                        n[k] = clean[k]
                n["tags"] = sorted(set(n.get("tags") or []) | set(clean.get("tags") or []))
                store.save_contact(owner, bid, c["id"], notes=n)
        return _batch_view(owner, bid)
    return await asyncio.to_thread(lambda: _wrap(work))


@router.delete("/api/batches/{bid}")
async def delete_batch(request: Request, bid: str):
    """立即清除：原圖、裁圖、辨識結果、編修都刪掉（已下載或另存工作區的副本不受影響）。"""
    owner, _ = owner_of(request)
    await asyncio.to_thread(lambda: _wrap(lambda: store.delete_batch(owner, bid)))
    _audit(request, "business_card_delete", TOOL_ID, batch=bid)
    return {"ok": True}


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "需要 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "需要 JSON 物件")
    return body


# --------------------------------------------------------------------------- 影像

def _jpeg_response(path, max_edge: int):
    from PIL import Image
    import io
    if not path.exists():
        raise _nf()
    if not max_edge:
        return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
    im = Image.open(path)
    im.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.get("/api/batches/{bid}/pages/{pid}/image")
async def page_image(request: Request, bid: str, pid: str, max: int = 1600):
    owner, _ = owner_of(request)
    m = max if max in (0, 400, 800, 1600, 2400) else 1600
    return await asyncio.to_thread(lambda: _wrap(lambda: (
        store.get_page(owner, bid, pid), _jpeg_response(store.page_image_path(bid, pid), m))[1]))


@router.get("/api/batches/{bid}/cards/{cid}/image")
async def card_image(request: Request, bid: str, cid: str, max: int = 0):
    owner, _ = owner_of(request)
    m = max if max in (0, 400, 800, 1600) else 0
    return await asyncio.to_thread(lambda: _wrap(lambda: (
        store.get_card(owner, bid, cid), _jpeg_response(store.card_image_path(bid, cid), m))[1]))


# --------------------------------------------------------------------------- 框位

@router.put("/api/batches/{bid}/pages/{pid}/cards")
async def set_page_cards(request: Request, bid: str, pid: str):
    """一頁的卡面框（多卡模式補框、刪框、拖四角、旋轉）。`cards`：`[{id?, quad:[[x,y]×4], rotate, memo}]`。
    框沒變的保留辨識結果；改過的回到待辨識，`recognize=true` 時立刻重跑。"""
    from . import detector
    owner, _ = owner_of(request)
    body = await _json(request)
    items = body.get("cards")
    if not isinstance(items, list) or len(items) > 60:
        raise HTTPException(400, "cards 要是陣列（一頁最多 60 張）")

    def work():
        page = store.get_page(owner, bid, pid)
        clean = []
        for it in items:
            if not isinstance(it, dict):
                raise HTTPException(400, "cards 的每一項要是物件")
            try:
                quad = detector.clamp_quad(it.get("quad"), page["width"], page["height"])
                rot = int(it.get("rotate") or 0)
            except (TypeError, ValueError) as e:
                raise HTTPException(400, "框位不正確：要四個點、數值在圖片範圍內，而且不可以太小")
            if rot % 90:
                raise HTTPException(400, "rotate 只能是 0 / 90 / 180 / 270")
            cid = it.get("id") if isinstance(it.get("id"), str) and store.ID_RE.fullmatch(it.get("id") or "") else None
            clean.append({"id": cid, "quad": quad, "rotate": rot % 360, "source": "manual" if not cid else None,
                          "memo": str(it.get("memo") or "")[:500], "flags": []})
        todo = store.replace_page_cards(bid, pid, clean)
        store.set_page(pid, status="detected" if clean else "zero")
        if body.get("recognize") and todo:
            pipeline.set_origin(bid, _origin(request))
            pipeline.start(owner, bid, request=request)
        return _batch_view(owner, bid)
    return await asyncio.to_thread(lambda: _wrap(work))


@router.post("/api/batches/{bid}/mode")
async def change_mode(request: Request, bid: str):
    """改收件模式（不用重新上傳）：`{"mode": "multi" | "single", "pages": [頁編號…]?}`。

    只改指定的頁（預設整批），那幾頁重新找卡面、原本的辨識結果與修改清掉；其他頁不動。
    `multi` 停在確認範圍、`single` 直接辨識。還有一張在辨識時等它做完（最多 60 秒，否則 409）。
    """
    owner, _ = owner_of(request)
    body = await _json(request)
    mode = body.get("mode")
    pages = body.get("pages")
    if pages is not None and (not isinstance(pages, list) or len(pages) > store.MAX_PAGES):
        raise HTTPException(400, "pages 要是頁編號清單")

    def work():
        try:
            done = pipeline.change_mode(owner, bid, mode, pages or None, request=request)
        except pipeline.BusyError as e:
            raise HTTPException(409, str(e))
        pipeline.set_origin(bid, _origin(request))
        return {"pages": done, "batch": _batch_view(owner, bid)}
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_mode", bid, mode=mode, pages=len(out["pages"]))
    return out


@router.post("/api/batches/{bid}/recognize")
async def recognize(request: Request, bid: str):
    """「全部辨識」：把待辨識的卡排進背景。"""
    owner, _ = owner_of(request)

    def work():
        b = store.get_batch(owner, bid)
        if not any(c for c in store.cards(bid)):
            raise HTTPException(400, "還沒有任何卡面框；請先補框或重新拍攝")
        pipeline.set_origin(bid, _origin(request))
        n = pipeline.start(owner, bid, request=request)
        return {"queued": n, "batch": _batch_view(owner, b["id"])}
    return await asyncio.to_thread(lambda: _wrap(work))


@router.post("/api/batches/{bid}/cards/{cid}/retry")
async def retry_card(request: Request, bid: str, cid: str):
    owner, _ = owner_of(request)
    await asyncio.to_thread(lambda: _wrap(lambda: pipeline.retry(owner, bid, cid, request=request)))
    return {"ok": True}


# --------------------------------------------------------------------------- 聯絡人

def _clean_fields(fields) -> list[dict]:
    from .extract import FIELD_KEYS
    if not isinstance(fields, list) or len(fields) > 120:
        raise HTTPException(400, "fields 要是陣列（最多 120 項）")
    out = []
    for f in fields:
        if not isinstance(f, dict) or f.get("key") not in FIELD_KEYS:
            raise HTTPException(400, "欄位不正確")
        st = f.get("status") or "confirmed"
        if st not in ("auto", "check", "confirmed", "deleted"):
            raise HTTPException(400, "欄位狀態不正確")
        src = f.get("source") if isinstance(f.get("source"), dict) else {"method": "manual"}
        item = {"id": str(f.get("id") or store.new_id()[:12])[:32], "key": f["key"],
                "value": str(f.get("value") or "")[:500], "status": st, "reason": str(f.get("reason") or "")[:200],
                "source": {k: src.get(k) for k in ("method", "line", "box", "conf") if k in src}}
        for k in ("ext", "label", "zip", "raw", "norm"):
            if f.get(k) not in (None, ""):
                item[k] = str(f[k])[:200]
        if item["key"] in ("mobile", "phone", "fax"):
            from .extract import normalize_phone
            item["norm"] = normalize_phone(item["value"])
        if item["key"] in ("email", "url"):
            item["norm"] = item["value"].strip().lower()
        out.append(item)
    return out


@router.put("/api/batches/{bid}/contacts/{ctid}")
async def save_contact(request: Request, bid: str, ctid: str):
    """存校對結果：`fields`（整份欄位清單）、`notes`（人工補充）、`status`（check / confirmed / excluded）。"""
    owner, _ = owner_of(request)
    body = await _json(request)
    fields = _clean_fields(body["fields"]) if "fields" in body else None
    st = body.get("status")
    if st is not None and st not in store.CONTACT_STATUSES:
        raise HTTPException(400, "status 不正確")
    if st == "confirmed" and fields is not None:
        # 「儲存並確認」：這位聯絡人的每個欄位都算人工確認過
        for f in fields:
            if f["status"] in ("auto", "check"):
                f["status"] = "confirmed"
    return await asyncio.to_thread(lambda: _wrap(lambda: store.save_contact(
        owner, bid, ctid, fields=fields, notes=body.get("notes"), status=st)))


@router.post("/api/batches/{bid}/contacts/save-all")
async def save_all_contacts(request: Request, bid: str):
    """全部儲存：一次存好幾位的校對結果（畫面上切換名片時留著的修改）。

    `items`＝`[{"id", "fields"?, "notes"?}]`；`confirm_all=true` 另外把這一批所有沒排除、還沒確認的聯絡人
    標成已確認（每個欄位一併算人工確認 —— 畫面上先問過，並寫明還有幾位有「需確認」的欄位）。
    先整份檢查（有一筆不對就整批不存），再逐筆存。回 `saved`、`confirmed` 與批次內容。
    """
    owner, _ = owner_of(request)
    body = await _json(request)
    items = body.get("items") or []
    if not isinstance(items, list) or len(items) > store.MAX_CARDS * 3:
        raise HTTPException(400, "items 要是陣列")
    confirm_all = bool(body.get("confirm_all"))
    clean = []
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("id"), str):
            raise HTTPException(400, "items 的每一項要有 id")
        clean.append({"id": it["id"], "fields": _clean_fields(it["fields"]) if "fields" in it else None,
                      "notes": it.get("notes") if isinstance(it.get("notes"), dict) else None})

    def work():
        store.get_batch(owner, bid)
        mine = {c["id"]: c for c in store.contacts(bid)}
        if any(it["id"] not in mine for it in clean):
            raise store.NotFound()
        saved = confirmed = 0
        for it in clean:
            if it["fields"] is not None or it["notes"] is not None:
                store.save_contact(owner, bid, it["id"], fields=it["fields"], notes=it["notes"])
                saved += 1
        if confirm_all:
            for c in store.contacts(bid):
                if c["status"] in ("excluded", "confirmed"):
                    continue
                fs = [dict(f, status="confirmed") if f.get("status") in ("auto", "check") else f for f in c["fields"]]
                store.save_contact(owner, bid, c["id"], fields=fs, status="confirmed")
                confirmed += 1
        return {"saved": saved, "confirmed": confirmed, "batch": _batch_view(owner, bid)}
    out = await asyncio.to_thread(lambda: _wrap(work))
    if out["confirmed"]:
        _audit(request, "business_card_confirm_all", bid, confirmed=out["confirmed"])
    return out


@router.post("/api/batches/{bid}/contacts/merge")
async def merge_batch_contacts(request: Request, bid: str):
    """同一批裡兩筆是同一位（正反面、同一人兩張）：`{"keep", "other"}`，other 併進 keep。可以解除。"""
    owner, _ = owner_of(request)
    body = await _json(request)
    keep, other = body.get("keep"), body.get("other")
    if not isinstance(keep, str) or not isinstance(other, str) or keep == other:
        raise HTTPException(400, "keep 與 other 要是兩個不同的聯絡人")

    def work():
        store.get_batch(owner, bid)
        return store.merge_contacts(owner, bid, keep, other)
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_contact_merge", bid, keep=keep, other=other)
    return out


@router.post("/api/batches/{bid}/contacts/{ctid}/unmerge")
async def unmerge_batch_contact(request: Request, bid: str, ctid: str):
    """解除最近一次合併：兩位都回到合併前的樣子（合併之後的修改會不見）。"""
    owner, _ = owner_of(request)

    def work():
        store.get_batch(owner, bid)
        try:
            return store.unmerge_contact(owner, bid, ctid)
        except ValueError:
            raise HTTPException(409, "這一位沒有合併過")
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_contact_unmerge", bid, contact=ctid)
    return out


@router.post("/api/batches/{bid}/cards/{cid}/contacts")
async def add_contact(request: Request, bid: str, cid: str):
    """同一張名片上的第二位聯絡人（共用這張卡的來源）。"""
    owner, _ = owner_of(request)
    ctid = await asyncio.to_thread(lambda: _wrap(lambda: store.add_contact(owner, bid, cid)))
    return await asyncio.to_thread(lambda: _wrap(lambda: store.get_contact(owner, bid, ctid)))


@router.delete("/api/batches/{bid}/contacts/{ctid}")
async def delete_contact(request: Request, bid: str, ctid: str):
    owner, _ = owner_of(request)
    await asyncio.to_thread(lambda: _wrap(lambda: store.delete_contact(owner, bid, ctid)))
    return {"ok": True}


# --------------------------------------------------------------------------- 匯出

FORMATS = {"csv": "text/csv; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
           "ods": "application/vnd.oasis.opendocument.spreadsheet", "vcf": "text/vcard; charset=utf-8",
           "json": "application/json"}


def _scope(contacts: list[dict], scope: str, ids: Optional[list[str]]) -> list[dict]:
    live = [c for c in contacts if c["status"] != "excluded"]
    if scope == "confirmed":
        return [c for c in live if c["status"] == "confirmed"]
    if scope == "all":
        return live
    if scope == "selected":
        want = set(ids or [])
        return [c for c in live if c["id"] in want]
    raise HTTPException(400, "scope 只能是 confirmed / all / selected")


def _export_args(body: dict) -> dict:
    cols = body.get("columns")
    if cols is not None and (not isinstance(cols, list) or not all(isinstance(x, str) for x in cols)):
        raise HTTPException(400, "columns 要是字串陣列")
    lang = body.get("lang") or "zh"
    if lang not in ("zh", "en"):
        raise HTTPException(400, "lang 只能是 zh 或 en")
    return {"columns": cols, "lang": lang}


@router.post("/api/batches/{bid}/export/preview")
async def export_preview(request: Request, bid: str):
    """下載前的預覽：筆數、略過幾筆（待確認 / 排除）、前幾列。"""
    from . import exporter
    owner, _ = owner_of(request)
    body = await _json(request)
    args = _export_args(body)

    def work():
        store.get_batch(owner, bid)
        allc = store.contacts(bid)
        chosen = _scope(allc, body.get("scope") or "confirmed", body.get("ids"))
        pv = exporter.preview(chosen, n=5, **args)
        live = [c for c in allc if c["status"] != "excluded"]
        bad = exporter.big5_missing(chosen, **args)
        return {**pv, "total": len(live), "skipped_check": sum(1 for c in live if c["status"] == "check" and c not in chosen),
                "excluded": len(allc) - len(live), "big5_missing": bad[:10], "big5_missing_count": len(bad)}
    return await asyncio.to_thread(lambda: _wrap(work))


def _export_opts(body: dict) -> dict:
    """匯出參數檢查（批次與名片簿共用）。"""
    from . import exporter
    fmt = body.get("format")
    if fmt not in FORMATS:
        raise HTTPException(400, "format 只能是 csv / xlsx / ods / vcf / json")
    enc = body.get("encoding") or "utf-8"
    if enc not in exporter.CSV_ENCODINGS:
        raise HTTPException(400, "encoding 只能是 utf-8 或 big5")
    return {"fmt": fmt, "args": _export_args(body), "table": bool(body.get("table")), "enc": enc,
            "bom": body.get("bom", True) is not False, "safe": body.get("safe", True) is not False,
            "include_source": bool(body.get("include_source"))}


def _render(chosen: list[dict], o: dict) -> bytes:
    from . import exporter
    fmt, args = o["fmt"], o["args"]
    if fmt == "csv":
        try:
            return exporter.export_csv(chosen, bom=o["bom"], safe=o["safe"], encoding=o["enc"], **args)
        except exporter.EncodingError as e:
            raise HTTPException(422, f"有 {e.total} 個字 Big5 沒有（例如：「{'、'.join(e.chars[:5])}」），"
                                     "存成 Big5 會變成問號；請改用 UTF-8")
    if fmt == "xlsx":
        return exporter.export_xlsx(chosen, table=o["table"], **args)
    if fmt == "ods":
        return exporter.export_ods(chosen, table=o["table"], **args)
    if fmt == "vcf":
        return exporter.export_vcf(chosen)
    return exporter.export_json(chosen, include_source=o["include_source"])


def _download(data: bytes, o: dict, stem: str) -> Response:
    from ...core.http_utils import content_disposition
    media = "text/csv; charset=big5" if o["fmt"] == "csv" and o["enc"] == "big5" else FORMATS[o["fmt"]]
    name = f"{stem}-{time.strftime('%Y%m%d-%H%M', time.localtime())}.{o['fmt']}"
    return Response(data, media_type=media, headers={"Content-Disposition": content_disposition(name),
                                                     "Cache-Control": "no-store"})


def _fmt_audit(o: dict) -> dict:
    return {"format": o["fmt"], **({"encoding": o["enc"]} if o["fmt"] == "csv" else
                                   {"table": o["table"]} if o["fmt"] in ("xlsx", "ods") else {})}


@router.post("/api/batches/{bid}/export")
async def export(request: Request, bid: str):
    """匯出：`format`＝csv / xlsx / ods / vcf / json；`scope`＝confirmed（預設）/ all（含待確認）/ selected（`ids`）；
    試算表 `table=true` 打開就有篩選與搜尋、`false` 純資料；CSV `encoding`（`utf-8` 預設 / `big5`）、
    `bom`（預設 true，只對 UTF-8）、`safe`（預設 true，中和公式開頭）。Big5 沒有的字回 422（不自動換成問號）。
    沒有任何一筆可以匯出時回 409，不給空檔。"""
    owner, _ = owner_of(request)
    body = await _json(request)
    o = _export_opts(body)

    def work():
        store.get_batch(owner, bid)
        chosen = _scope(store.contacts(bid), body.get("scope") or "confirmed", body.get("ids"))
        if not chosen:
            raise HTTPException(409, "沒有可以匯出的聯絡人：還沒有確認任何一筆。請先校對，或選「包含待確認」")
        return chosen, _render(chosen, o)
    chosen, data = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_export", TOOL_ID, batch=bid, count=len(chosen),
           scope=body.get("scope") or "confirmed", **_fmt_audit(o))
    return _download(data, o, "名片聯絡人")


# --------------------------------------------------------------------------- 名片簿

def viewer_of(request: Request) -> books.Viewer:
    from ...core import auth_settings, permissions
    owner, label = owner_of(request)
    auth = auth_settings.is_enabled()
    uid = None
    subjects: list = []
    if auth:
        user = getattr(request.state, "user", None) or {}
        uid = int(user.get("user_id")) if user.get("user_id") else None
        if uid:
            try:
                subjects = permissions.subjects_for_user(uid)
            except Exception:                            # noqa: BLE001 —— 查不到群組就只認本人
                subjects = [("user", str(uid))]
    return books.Viewer(owner=owner, user_id=uid, label=label or "", auth=auth, subjects=subjects)


@router.get("/api/books")
async def list_books(request: Request):
    """我看得到的名片簿（個人那本第一次用到時建立）與我在每本的角色（owner / edit / view）。"""
    v = viewer_of(request)
    return {"items": await asyncio.to_thread(lambda: _wrap(lambda: books.list_books(v))),
            "personal_max_days": books.personal_max_days()}


@router.patch("/api/books/{book_id}")
async def update_book(request: Request, book_id: str):
    """個人名片簿的保留天數（0 ＝永久；不可以超過管理員的上限）。共用名片簿由管理員設定。"""
    v = viewer_of(request)
    body = await _json(request)
    out = await asyncio.to_thread(lambda: _wrap(lambda: books.set_personal_keep(v, book_id, body.get("keep_days"))))
    _audit(request, "business_card_book_keep", book_id, keep_days=out["keep_days"])
    return out


@router.get("/api/books/{book_id}/people")
async def book_people(request: Request, book_id: str, q: str = "", tag: str = "", limit: int = 100, offset: int = 0):
    v = viewer_of(request)

    def work():
        book, role = books.get_book(v, book_id)
        return {**books.list_people(book, q=q[:200], tag=tag[:60], limit=limit, offset=offset), "role": role,
                "book": {"id": book["id"], "name": book["name"], "kind": book["kind"]}}
    out = await asyncio.to_thread(lambda: _wrap(work))
    # 稽核只記查了什麼種類的條件與筆數，不記關鍵字（關鍵字本身常常就是人名）
    _audit(request, "business_card_book_query", book_id, has_query=bool(q.strip()), has_tag=bool(tag), total=out["total"])
    return out


@router.get("/api/books/{book_id}/people/{pid}")
async def book_person(request: Request, book_id: str, pid: str):
    v = viewer_of(request)

    def work():
        book, role = books.get_book(v, book_id)
        return {**books.get_person(book, pid), "role": role}
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_book_view", book_id, person=pid)
    return out


@router.put("/api/books/{book_id}/people/{pid}")
async def book_person_save(request: Request, book_id: str, pid: str):
    v = viewer_of(request)
    body = await _json(request)
    fields = _clean_fields(body["fields"]) if "fields" in body else None
    notes = body.get("notes") if isinstance(body.get("notes"), dict) else None

    def work():
        book, _ = books.get_book(v, book_id, "edit")
        return books.update_person(book, pid, v.label or v.owner, fields=fields, notes=notes)
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_book_edit", book_id, person=pid)
    return out


@router.delete("/api/books/{book_id}/people/{pid}")
async def book_person_delete(request: Request, book_id: str, pid: str):
    v = viewer_of(request)

    def work():
        book, _ = books.get_book(v, book_id, "edit")
        books.delete_person(book, pid)
    await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_book_delete", book_id, person=pid)
    return {"ok": True}


@router.post("/api/books/{book_id}/people/merge")
async def book_people_merge(request: Request, book_id: str):
    """同一人被存成兩筆：`{"keep": 保留的那位, "others": [併進來的…]}`。名片紀錄與互動都搬過去。"""
    v = viewer_of(request)
    body = await _json(request)
    keep = body.get("keep")
    others = body.get("others")
    if not isinstance(keep, str) or not isinstance(others, list) or not all(isinstance(x, str) for x in others):
        raise HTTPException(400, "keep 要是編號、others 要是編號清單")

    def work():
        book, _ = books.get_book(v, book_id, "edit")
        return books.merge_people(book, keep, others[:20], v.label or v.owner)
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_book_merge", book_id, keep=keep, merged=len(others))
    return out


@router.get("/api/books/{book_id}/people/{pid}/cards/{cid}/image")
async def book_card_image(request: Request, book_id: str, pid: str, cid: str):
    v = viewer_of(request)

    def work():
        book, _ = books.get_book(v, book_id)
        p = books.get_person(book, pid)
        if not any(c["id"] == cid and c["image"] for c in p["cards"]):
            raise store.NotFound()
        path = books.card_image_path(book, cid)
        if not path.is_file():
            raise store.NotFound()
        return path
    path = await asyncio.to_thread(lambda: _wrap(work))
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


def _interaction_body(body: dict) -> tuple:
    return body.get("kind"), body.get("note"), body.get("at")


@router.post("/api/books/{book_id}/people/{pid}/interactions")
async def interaction_add(request: Request, book_id: str, pid: str):
    """互動紀錄：`{"kind": meeting / call / email / visit / other, "note": "…", "at": 時間戳記（省略＝現在）}`。"""
    v = viewer_of(request)
    body = await _json(request)

    def work():
        book, _ = books.get_book(v, book_id, "edit")
        return books.add_interaction(book, pid, *_interaction_body(body), by=v.label or v.owner)
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_interaction", book_id, person=pid, action="add")
    return out


@router.put("/api/books/{book_id}/people/{pid}/interactions/{iid}")
async def interaction_update(request: Request, book_id: str, pid: str, iid: str):
    v = viewer_of(request)
    body = await _json(request)

    def work():
        book, _ = books.get_book(v, book_id, "edit")
        return books.update_interaction(book, pid, iid, *_interaction_body(body))
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_interaction", book_id, person=pid, action="edit")
    return out


@router.delete("/api/books/{book_id}/people/{pid}/interactions/{iid}")
async def interaction_delete(request: Request, book_id: str, pid: str, iid: str):
    v = viewer_of(request)

    def work():
        book, _ = books.get_book(v, book_id, "edit")
        books.delete_interaction(book, pid, iid)
    await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_interaction", book_id, person=pid, action="delete")
    return {"ok": True}


@router.post("/api/books/{book_id}/export")
async def book_export(request: Request, book_id: str):
    """名片簿匯出（格式與參數同批次匯出）。`ids` 只匯出這幾位，省略＝整本（或 `q` / `tag` 查到的那些）。"""
    v = viewer_of(request)
    body = await _json(request)
    o = _export_opts(body)
    ids = body.get("ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids)):
        raise HTTPException(400, "ids 要是編號清單")

    def work():
        book, _ = books.get_book(v, book_id)
        people = books.all_people(book)
        if ids is not None:
            want = set(ids)
            people = [p for p in people if p["id"] in want]
        elif body.get("q") or body.get("tag"):
            hit = {i["id"] for i in books.list_people(book, q=str(body.get("q") or "")[:200],
                                                      tag=str(body.get("tag") or "")[:60], limit=500)["items"]}
            people = [p for p in people if p["id"] in hit]
        if not people:
            raise HTTPException(409, "沒有可以匯出的聯絡人")
        return people, _render(books.as_contacts(people), o), book
    people, data, book = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_book_export", book_id, count=len(people), **_fmt_audit(o))
    return _download(data, o, "名片簿")


# ------------------------------------------------------------------ 名片簿：匯入檔案、找重複

def _book_import_path(token: str):
    from ...config import settings
    if not isinstance(token, str) or not LIST_TOKEN_RE.fullmatch(token):
        raise store.NotFound()
    return settings.temp_dir / f"bcbook_{token}.json"


def _book_import_load(owner: str, book_id: str, token: str) -> dict:
    try:
        d = json.loads(_book_import_path(token).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(410, "上傳的檔案已經過期，請重新上傳")
    if d.get("owner") != owner or d.get("book") != book_id:
        raise store.NotFound()
    return d


def _book_import_contacts(d: dict, mapping_in) -> tuple[dict, list[dict]]:
    from . import importer
    mapping = importer.clean_mapping(mapping_in or importer.auto_map(d["headers"]), len(d["headers"]))
    return mapping, importer.rows_to_contacts(d["headers"], d["rows"], mapping)


def _book_import_view(book: dict, contacts: list[dict]) -> dict:
    items = books.import_preview(book, contacts)
    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return {"items": items, "counts": counts}


@router.post("/api/books/{book_id}/import")
async def book_import_upload(request: Request, book_id: str, file: UploadFile = File(...)):
    """把檔案匯入名片簿（之前匯出的 XLSX / ODS / CSV / VCF / JSON，或手上的聯絡人清單）。先回欄位對應與
    **自動去重**的比對結果（每一列：新的人 / 名片簿已有 / 檔案裡重複），按 `apply` 才真的寫進去。"""
    from . import importer
    from ...config import settings
    from ...core.atomic_json import write_json
    v = viewer_of(request)
    data = await file.read(importer.MAX_BYTES + 1)
    name = file.filename or "list"

    def work():
        book, _ = books.get_book(v, book_id, "edit")
        try:
            headers, rows = importer.parse_table(data, name)
        except importer.TableError as e:
            raise HTTPException(400, str(e))
        token = store.new_id()
        settings.temp_dir.mkdir(parents=True, exist_ok=True)
        d = {"owner": v.owner, "book": book_id, "headers": headers, "rows": rows, "filename": name[:200],
             "ts": time.time()}
        write_json(_book_import_path(token), d, indent=0)
        mapping, contacts = _book_import_contacts(d, None)
        return {"token": token, "kind": importer.source_kind(name), "headers": headers, "sample": rows[:5],
                "rows": len(rows), "mapping": {str(k): x for k, x in mapping.items()}, "targets": _list_targets(),
                **_book_import_view(book, contacts)}
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_book_import_upload", book_id, rows=out["rows"], columns=len(out["headers"]))
    return out


@router.post("/api/books/{book_id}/import/{token}/preview")
async def book_import_preview(request: Request, book_id: str, token: str):
    """改了欄位對應之後重新比對：`{"mapping": {欄號: 欄位}}`。"""
    v = viewer_of(request)
    body = await _json(request)

    def work():
        book, _ = books.get_book(v, book_id, "edit")
        _, contacts = _book_import_contacts(_book_import_load(v.owner, book_id, token), body.get("mapping"))
        return _book_import_view(book, contacts)
    return await asyncio.to_thread(lambda: _wrap(work))


@router.post("/api/books/{book_id}/import/{token}/apply")
async def book_import_apply(request: Request, book_id: str, token: str):
    """真的匯入：`{"mapping"?, "decisions": {列號: "add" | "merge" | "skip"}}`（沒給的用比對的預設）。
    做完刪掉暫存的檔案，回 `added` / `merged` / `skipped`。"""
    v = viewer_of(request)
    body = await _json(request)
    decisions = body.get("decisions") or {}
    if not isinstance(decisions, dict) or any(x not in books.IMPORT_ACTIONS for x in decisions.values()):
        raise HTTPException(400, "decisions 的值只能是 add / merge / skip")

    def work():
        book, _ = books.get_book(v, book_id, "edit")
        _, contacts = _book_import_contacts(_book_import_load(v.owner, book_id, token), body.get("mapping"))
        out = books.import_apply(book, contacts, decisions, v.label or v.owner)
        _book_import_path(token).unlink(missing_ok=True)
        return out
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_book_import", book_id, **out)
    return out


@router.delete("/api/books/{book_id}/import/{token}")
async def book_import_discard(request: Request, book_id: str, token: str):
    v = viewer_of(request)

    def work():
        books.get_book(v, book_id, "edit")
        _book_import_load(v.owner, book_id, token)
        _book_import_path(token).unlink(missing_ok=True)
        return {"ok": True}
    return await asyncio.to_thread(lambda: _wrap(work))


@router.get("/api/books/{book_id}/duplicates")
async def book_duplicates(request: Request, book_id: str):
    """名片簿裡可能是同一人的幾組（Email、手機、姓名＋公司相同），給「檢查重複」用；合併走 `people/merge`。"""
    v = viewer_of(request)

    def work():
        book, role = books.get_book(v, book_id)
        return {"groups": books.duplicate_groups(book), "can_edit": role in ("owner", "edit")}
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_book_duplicates", book_id, groups=len(out["groups"]))
    return out


# ------------------------------------------------------------------ 存進名片簿

def _save_targets(owner: str, bid: str, body: dict) -> list[dict]:
    contacts = store.contacts(bid)
    ids = body.get("ids")
    if ids is not None:
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise HTTPException(400, "ids 要是編號清單")
        return _scope(contacts, "selected", ids)
    return _scope(contacts, body.get("scope") or "confirmed", None)


@router.post("/api/batches/{bid}/save-to-book/preview")
async def save_to_book_preview(request: Request, bid: str):
    """存進名片簿之前：每一位在那本裡有沒有可能是同一人的（Email → 手機 → 姓名＋公司），讓使用者選「新增 / 合併到某人 / 略過」。"""
    v = viewer_of(request)
    owner = v.owner
    body = await _json(request)
    book_id = body.get("book")
    if not isinstance(book_id, str):
        raise HTTPException(400, "要指定名片簿")

    def work():
        store.get_batch(owner, bid)
        book, _ = books.get_book(v, book_id, "edit")
        out = []
        for c in _save_targets(owner, bid, body):
            f = c["fields"]
            out.append({"contact_id": c["id"], "name": books._val(f, "name") or books._val(f, "name_en"),
                        "company": books._val(f, "company"), "title": books._val(f, "title"),
                        "matches": books.find_matches(book, f)})
        return {"book": {"id": book["id"], "name": book["name"]}, "items": out}
    return await asyncio.to_thread(lambda: _wrap(work))


@router.post("/api/batches/{bid}/save-to-book")
async def save_to_book(request: Request, bid: str):
    """`{"book", "items": [{"contact_id", "action": new / merge / skip, "person_id"?}]}`。
    合併：那個人多一張名片紀錄（含名片圖），目前資料換成這張的公司 / 職稱，舊的留在紀錄裡。"""
    v = viewer_of(request)
    owner = v.owner
    body = await _json(request)
    book_id, items = body.get("book"), body.get("items")
    if not isinstance(book_id, str) or not isinstance(items, list) or len(items) > store.MAX_CARDS * 3:
        raise HTTPException(400, "要指定名片簿與 items")
    for it in items:
        if not isinstance(it, dict) or it.get("action") not in ("new", "merge", "skip") or not isinstance(it.get("contact_id"), str):
            raise HTTPException(400, "items 的每一項要有 contact_id 與 action（new / merge / skip）")
        if it["action"] == "merge" and not isinstance(it.get("person_id"), str):
            raise HTTPException(400, "合併要指定 person_id")

    def work():
        b = store.get_batch(owner, bid)
        book, _ = books.get_book(v, book_id, "edit")
        mine = {c["id"]: c for c in store.contacts(bid)}
        if any(it["contact_id"] not in mine for it in items):
            raise store.NotFound()
        for it in items:
            if it["action"] == "merge":
                books.get_person(book, it["person_id"])  # 先全部檢查，有一筆不對就整批不存
        added = merged = 0
        by = v.label or v.owner
        for it in items:
            if it["action"] == "skip":
                continue
            c = mine[it["contact_id"]]
            img = None
            if c.get("card_id"):
                ip = store.card_image_path(bid, c["card_id"])
                if ip.is_file():
                    img = ip.read_bytes()
            if it["action"] == "new":
                books.add_person(book, c["fields"], c["notes"], by, image=img, captured_at=b["created_at"])
                added += 1
            else:
                books.add_card(book, it["person_id"], c["fields"], c["notes"], by, image=img, captured_at=b["created_at"])
                merged += 1
        return {"added": added, "merged": merged}
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_book_save", book_id, batch=bid, **out)
    return out


# ------------------------------------------------------------------ 跟手上的名片清單整合

LIST_TOKEN_RE = __import__("re").compile(r"^[0-9a-f]{32}$")


def _list_path(token: str):
    from ...config import settings
    if not isinstance(token, str) or not LIST_TOKEN_RE.fullmatch(token):
        raise store.NotFound()
    return settings.temp_dir / f"bclist_{token}.json"


def _list_load(owner: str, token: str) -> dict:
    p = _list_path(token)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(410, "上傳的清單已經過期，請重新上傳")
    if d.get("owner") != owner:
        raise store.NotFound()
    return d


def _list_contacts(owner: str, bid: str, body: dict) -> list[dict]:
    store.get_batch(owner, bid)
    scope = body.get("scope") or "confirmed"
    return _scope(store.contacts(bid), scope, body.get("ids"))


def _apply_pairs(plan: list[dict], pairs) -> list[dict]:
    """使用者手動指定（或接受 AI 建議）「這位就是清單第幾列」：那一項改成跟那一列合併。"""
    if not pairs:
        return plan
    if not isinstance(pairs, list):
        raise HTTPException(400, "pairs 要是陣列")
    want = {}
    for pr in pairs[:500]:
        if not isinstance(pr, dict) or not isinstance(pr.get("contact_id"), str) or not isinstance(pr.get("row"), int):
            raise HTTPException(400, "pairs 的每一項要有 contact_id 與 row")
        want[pr["contact_id"]] = pr["row"]
    out = []
    taken = {want[k] for k in want}
    for it in plan:
        if it.get("contact_id") in want:
            it = {**it, "row": want[it["contact_id"]], "kind": "update", "match": "manual"}
        elif it.get("kind") == "untouched" and it.get("row") in taken:
            continue
        out.append(it)
    return out


@router.post("/api/batches/{bid}/list-merge")
async def list_merge_upload(request: Request, bid: str, file: UploadFile = File(...)):
    """上傳手上的名片清單或之前匯出的檔（CSV / XLSX / ODS / VCF / JSON）：回欄位自動對應與前幾列。清單只暫存在伺服器的暫存區（約 2 小時後清掉），
    只用在這次整合，不會存進名片簿。"""
    from . import importer
    from ...config import settings
    owner, _ = owner_of(request)
    data = await file.read(importer.MAX_BYTES + 1)
    name = file.filename or "list"

    def work():
        store.get_batch(owner, bid)
        try:
            headers, rows = importer.parse_table(data, name)
        except importer.TableError as e:
            raise HTTPException(400, str(e))
        token = store.new_id()
        settings.temp_dir.mkdir(parents=True, exist_ok=True)
        from ...core.atomic_json import write_json
        write_json(_list_path(token), {"owner": owner, "batch": bid, "headers": headers, "rows": rows,
                                       "filename": name[:200], "ts": time.time()}, indent=0)
        mapping = importer.auto_map(headers)
        return {"token": token, "kind": importer.source_kind(name), "headers": headers, "sample": rows[:5],
                "rows": len(rows), "mapping": {str(k): v for k, v in mapping.items()}, "targets": _list_targets()}
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_list_upload", bid, rows=out["rows"], columns=len(out["headers"]))
    return out


def _list_targets() -> list[dict]:
    from . import exporter, importer
    labels = {**exporter.LABELS_ZH, "notes.event": "活動", "notes.source": "來源", "notes.tags": "標籤", "notes.memo": "備註",
              "name.first": "名字（First name）", "name.middle": "中間名", "name.last": "姓氏（Last name）",
              "address.part": "地址的一部分"}
    out = []
    for t in importer.TARGETS:
        base = t.split(".", 1)[1] if t.startswith("notes.") else t
        out.append({"key": t, "label": labels.get(t) or exporter.LABELS_ZH.get(base, t)})
    return out


@router.post("/api/batches/{bid}/list-merge/{token}/plan")
async def list_merge_plan(request: Request, bid: str, token: str):
    """比對：每一位辨識出來的聯絡人跟清單的關係（new 新增 / same 相同 / update 清單缺的補上 / conflict 兩邊不同），
    清單裡沒配到的列（untouched）照原樣留著。`mapping` 是使用者調整後的欄位對應（欄號 → 欄位）。"""
    from . import importer
    owner, _ = owner_of(request)
    body = await _json(request)

    def work():
        d = _list_load(owner, token)
        mapping = importer.clean_mapping(body.get("mapping") or importer.auto_map(d["headers"]), len(d["headers"]))
        imported = importer.rows_to_contacts(d["headers"], d["rows"], mapping)
        cards = _list_contacts(owner, bid, body)
        plan = _apply_pairs(importer.merge_plan(cards, imported), body.get("pairs"))
        by_id = {c["id"]: c for c in cards}
        rows_by = {c["row"]: c for c in imported}

        def who(fields):
            return {"name": books._val(fields, "name") or books._val(fields, "name_en"), "company": books._val(fields, "company"),
                    "email": books._val(fields, "email"), "mobile": books._val(fields, "mobile")}
        items = []
        for it in plan:
            c = by_id.get(it.get("contact_id"))
            r = rows_by.get(it.get("row"))
            items.append({**it, "card": who(c["fields"]) if c else None, "list": who(r["fields"]) if r else None})
        pairs = importer.possible_pairs(cards, imported)
        for pr in pairs:
            pr["card"] = who(by_id[pr["contact_id"]]["fields"]) if pr["contact_id"] in by_id else None
            pr["list"] = who(rows_by[pr["row"]]["fields"]) if pr["row"] in rows_by else None
        counts = {}
        for it in items:
            counts[it["kind"]] = counts.get(it["kind"], 0) + 1
        return {"items": items, "counts": counts, "pairs": pairs, "decisions": importer.DECISIONS,
                "default_decision": importer.DEFAULT_DECISION, "ai": _ai_available()}
    return await asyncio.to_thread(lambda: _wrap(work))


@router.post("/api/batches/{bid}/list-merge/{token}/download")
async def list_merge_download(request: Request, bid: str, token: str):
    """整合後的清單：原本的列與欄全部保留、順序不變，補上清單缺的、照使用者的選擇處理不同的值，新的人接在最後。
    `format`＝csv / xlsx / ods / vcf / json（其他參數同匯出）；VCF / JSON 是把整合後的每一列再轉回聯絡人輸出
    （試算表裡對應不到欄位的欄不會出現在 VCF / JSON 裡）。"""
    from . import exporter, importer
    owner, _ = owner_of(request)
    body = await _json(request)
    o = _export_opts(body)
    decisions = body.get("decisions") or {}
    if not isinstance(decisions, dict) or any(v not in importer.DECISIONS for v in decisions.values()):
        raise HTTPException(400, "decisions 的值只能是 add / merge / skip / keep_list / use_card")

    def work():
        d = _list_load(owner, token)
        mapping = importer.clean_mapping(body.get("mapping") or importer.auto_map(d["headers"]), len(d["headers"]))
        imported = importer.rows_to_contacts(d["headers"], d["rows"], mapping)
        cards = _list_contacts(owner, bid, body)
        plan = _apply_pairs(importer.merge_plan(cards, imported), body.get("pairs"))
        headers, rows = importer.build_merged(d["headers"], d["rows"], mapping, cards, plan, decisions)
        if o["fmt"] in ("vcf", "json"):
            # 原本的欄照使用者調好的對應，後面新加的欄（欄名照匯出的寫法）自動對應
            full = {**importer.auto_map(headers), **mapping}
            merged = importer.rows_to_contacts(headers, rows, full)
            data = exporter.export_vcf(merged) if o["fmt"] == "vcf" else exporter.export_json(merged)
            return data, len(rows)
        try:
            data = importer.write_table(headers, rows, o["fmt"], table=o["table"], bom=o["bom"], encoding=o["enc"],
                                        safe=o["safe"])
        except exporter.EncodingError as e:
            raise HTTPException(422, f"有 {e.total} 個字 Big5 沒有（例如：「{'、'.join(e.chars[:5])}」），"
                                     "存成 Big5 會變成問號；請改用 UTF-8")
        return data, len(rows)
    data, n = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_list_merge", bid, rows=n, **_fmt_audit(o))
    return _download(data, o, "整合後的名片清單")


@router.delete("/api/batches/{bid}/list-merge/{token}")
async def list_merge_discard(request: Request, bid: str, token: str):
    """整合完：立刻刪掉暫存的清單（不刪也會在暫存區保留期限到了自動清掉）。"""
    owner, _ = owner_of(request)

    def work():
        _list_load(owner, token)
        _list_path(token).unlink(missing_ok=True)
    await asyncio.to_thread(lambda: _wrap(work))
    return {"ok": True}


def _ai_available() -> bool:
    try:
        from . import ai
        return ai.available()
    except Exception:                                    # noqa: BLE001
        return False


# ------------------------------------------------------------------ AI 協助（選用）

@router.get("/api/ai")
async def ai_info(request: Request):
    """AI 協助有沒有開、用哪個模型、資料送到哪台主機（畫面要寫清楚）。"""
    from . import ai
    owner_of(request)
    return await asyncio.to_thread(ai.info)


def _ai_guard():
    from . import ai
    if not ai.available():
        raise HTTPException(409, "名片辨識的 AI 協助沒有開啟（要全站 LLM 開啟，並由管理員在「設定 → 名片簿」打開）")


@router.post("/api/batches/{bid}/list-merge/{token}/ai-check")
async def list_merge_ai_check(request: Request, bid: str, token: str):
    """選用：把「可能是同一人、規則判斷不了」的幾組送給 AI 看（只送那兩筆的欄位文字，不送名片圖）。
    `pairs`＝`[{"contact_id", "row"}]`（最多 30 組）。AI 只回建議與理由，合併要使用者自己按。"""
    from . import ai, importer
    owner, _ = owner_of(request)
    body = await _json(request)
    _ai_guard()
    pairs = body.get("pairs")
    if not isinstance(pairs, list) or not pairs or len(pairs) > ai.MAX_PAIRS:
        raise HTTPException(400, f"pairs 要是 1～{ai.MAX_PAIRS} 組")

    def work():
        d = _list_load(owner, token)
        mapping = importer.clean_mapping(body.get("mapping") or importer.auto_map(d["headers"]), len(d["headers"]))
        imported = {c["row"]: c for c in importer.rows_to_contacts(d["headers"], d["rows"], mapping)}
        cards = {c["id"]: c for c in _list_contacts(owner, bid, body)}
        todo = []
        for pr in pairs:
            if not isinstance(pr, dict) or pr.get("contact_id") not in cards or pr.get("row") not in imported:
                raise HTTPException(400, "pairs 裡有對不上的項目")
            todo.append((pr, cards[pr["contact_id"]]["fields"], imported[pr["row"]]["fields"]))
        try:
            res = ai.same_person([(a, b) for _pr, a, b in todo])
        except ai.AIError as e:
            raise HTTPException(409, str(e))
        return [{**pr, **r} for (pr, _a, _b), r in zip(todo, res)]
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_ai", bid, action="same_person", pairs=len(out))
    return {"items": out, "info": ai.info()}


@router.post("/api/batches/{bid}/contacts/{ctid}/ai-suggest")
async def contact_ai_suggest(request: Request, bid: str, ctid: str):
    """選用：這一位的欄位請 AI 依 OCR 文字（看圖模式另送那一張的裁圖）建議歸類。建議一律標需確認，不自動套用。"""
    from . import ai
    owner, _ = owner_of(request)
    _ai_guard()

    def work():
        store.get_batch(owner, bid)
        ct = store.get_contact(owner, bid, ctid)
        card = store.get_card(owner, bid, ct["card_id"]) if ct.get("card_id") else None
        lines = (card or {}).get("ocr", {}).get("lines") or []
        if not lines:
            raise HTTPException(409, "這一位沒有辨識出來的文字可以給 AI")
        img = None
        ip = store.card_image_path(bid, card["id"]) if card else None
        if ip is not None and ip.is_file():
            img = ip.read_bytes()
        try:
            return ai.suggest_fields(lines, img)
        except ai.AIError as e:
            raise HTTPException(409, str(e))
    out = await asyncio.to_thread(lambda: _wrap(work))
    _audit(request, "business_card_ai", bid, action="suggest", contact=ctid, mode=out["mode"], fields=len(out["fields"]))
    return {**out, "info": ai.info()}
