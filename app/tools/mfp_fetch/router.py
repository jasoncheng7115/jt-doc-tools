"""事務機掃描取件（Beta；原名事務機取件）路由。

目前做得到的：新增事務機（IP / 主機名稱）、連線測試（逐步診斷＋型號辨識＋下載診斷報告）、
我的事務機清單（改名、重新測試、移除）、**從事務機掃描進工作區**（eSCL）、**共用事務機**（管理員建立、
加成員）、**FUJIFILM 機內信箱取件**（WebDAV，推測實作、尚未實機驗證，見 `mfp_box`；規格書與決策在
docs-share/mfp-fetch/）。

## 擁有者

* 啟用認證：`u<user_id>`（API token 呼叫時是 token 的持有者 —— 認證閘門已把持有者放進
  `request.state.user`；沒有持有者的 token 在那裡就被擋掉了）。
* 未啟用認證：`local`。**只准本機**（傳輸層對端是回送位址、Host 是 localhost、沒有轉送標頭）
  或帶有效 API token —— 規格書要求未啟用認證時不可以讓遠端用這支（它會讓伺服器去連區網設備）。
* 共用事務機：`shared`。成員（使用者、群組含巢狀上層、組織單位）看得到、可以測試與掃描；
  改名、移除、成員只有管理員（未啟用認證時就是本機）。

## 排隊

同一台**實體設備**一次只做一件事（`mfp_queue`）：連線測試最多等 `PROBE_WAIT` 秒，掃描在背景排隊。
每台、每人都有上限，超過直接拒絕、不往設備送（使用者 2026-10-14：以免塞爆事務機作業）。

網頁與 API 走同一組端點（`/tools/mfp-fetch/api/...`）。
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import logging
import secrets
import threading
import time
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ...core import mfp_box, mfp_client, mfp_escl, mfp_profiles, mfp_queue, mfp_scan, mfp_settings, mfp_share, mfp_store
from ...core.http_utils import content_disposition

logger = logging.getLogger("app.mfp_fetch")

router = APIRouter()

#: 每位擁有者 5 分鐘內最多幾次連線測試（含「重新測試」）。
RATE_LIMIT = 10
RATE_WINDOW = 300
#: 列文件（信箱、檔案分享、文件伺服器的資料夾）另外算：切資料夾、輸入資料夾密碼每次都是一次請求，
#: 跟連線測試共用 10 次的話，客戶在三個資料夾之間切幾次就被擋（客戶 RICOH 實測 2026-10-20）
BROWSE_RATE_LIMIT = 30
#: 連線測試結果保留多久可以「儲存」（秒）。
DETECTION_TTL = 900
#: 連線測試排隊最多等幾秒（設備正在幫別人掃描時）。
PROBE_WAIT = 30.0

MESSAGES: dict[str, str] = {
    "LOCAL_ONLY": "未啟用登入時，事務機掃描取件只能在這台主機本機使用；多人或從其他電腦使用請先啟用登入",
    "NOT_LOGGED_IN": "尚未登入",
    "RATE_LIMITED": "連線測試太頻繁，請稍候再試",
    "BROWSE_RATE_LIMITED": "列出文件太頻繁（5 分鐘內最多 30 次），請稍候再試",
    "BUSY": "上一個連線測試還在進行中，請等它完成",
    "BAD_REQUEST": "請求格式不正確",
    "DETECTION_EXPIRED": "連線測試結果已過期，請重新測試後再儲存",
    "NOT_FOUND": mfp_store.NOT_FOUND_MESSAGE,
    "DUPLICATE": mfp_store.DUPLICATE_MESSAGE,
    "LIMIT": mfp_store.LIMIT_MESSAGE,
    "NO_REPORT": "這台事務機還沒有連線測試的報告",
    "REPORT_FORMAT_INVALID": "診斷報告的格式只能是 txt、md、json、html 或 pdf",
    "REPORT_EXPIRED": "連線測試結果已過期，請重新測試後再下載診斷報告",
    "SHARED_READ_ONLY": "這是管理員設定的共用事務機，只有管理員可以改名或移除",
    "ADMIN_ONLY": "只有管理員可以管理共用事務機",
    "MEMBERS_INVALID": "成員清單不正確（只能是使用者、群組或組織單位）",
    "USER_DEVICES_DISABLED": "管理員已關閉「使用者自行新增事務機」：只能使用管理員設定的共用事務機",
    "SETTINGS_INVALID": "設定格式不正確",
    "BOX_NOT_FUJIFILM": "機內信箱取件目前只支援 FUJIFILM（富士軟片）事務機",
    "BOX_NO_TARGET": "這台事務機最近一次連線測試沒有連上，請先重新測試",
    "BOX_TARGET_INVALID": "連線方式要是這台事務機連線測試時連得上的那一組",
    **mfp_queue.MESSAGES,
    **{k: v for k, v in mfp_scan.MESSAGES.items() if k.startswith("SCAN_")},
    **mfp_box.MESSAGES,
    **mfp_share.MESSAGES,
}


def _err(code: str, status: int, **extra) -> JSONResponse:
    msg = MESSAGES.get(code) or mfp_escl.MESSAGES.get(code) or code
    return JSONResponse({"ok": False, "code": code, "message": msg, **extra}, status_code=status)


# --------------------------------------------------------------------------- 擁有者

_FORWARD_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _is_local_request(request: Request) -> bool:
    peer = (request.client.host if request.client else "") or ""
    try:
        if not ipaddress.ip_address(peer).is_loopback:
            return False
    except ValueError:
        return False
    if any(request.headers.get(h) for h in _FORWARD_HEADERS):
        return False          # 經過反向代理 —— 對端是回送位址也不算本機
    host = (request.headers.get("host") or "").strip().lower()
    if host.startswith("["):
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host in _LOCAL_HOSTS


class _OwnerError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code, self.status = code, status


def _owner(request: Request) -> tuple[str, Optional[int]]:
    from ...core import auth_settings
    if auth_settings.is_enabled():
        user = getattr(request.state, "user", None)
        uid = user.get("user_id") if isinstance(user, dict) else None
        if uid is None:
            raise _OwnerError("NOT_LOGGED_IN", 401)
        return f"u{int(uid)}", int(uid)
    if getattr(request.state, "api_token_label", None) is not None:
        return "local", None       # 有效 API token（閘門已驗過）
    if not _is_local_request(request):
        raise _OwnerError("LOCAL_ONLY", 403)
    return "local", None


class _Who:
    """這次請求是誰、看得到哪些共用事務機、是不是管理員。"""

    def __init__(self, request: Request):
        self.owner, self.uid = _owner(request)
        if self.uid is None:
            # 未啟用登入（只准本機）：單人模式，共用事務機全部看得到、可以管理
            self.subjects: list[tuple[str, str]] = []
            self.all_shared = True
            self.admin = True
        else:
            from ...core import permissions
            self.subjects = permissions.subjects_for_user(self.uid)
            self.all_shared = False
            self.admin = permissions.is_admin(self.uid)
        # 可不可以有自己的事務機（管理員設定「允許使用者自行新增事務機」；管理員自己不受限）
        self.can_own = self.admin or mfp_settings.allow_user_devices()

    def usable(self, c: dict) -> bool:
        """這台現在能不能拿來測試、掃描、取件：共用的照常；自己的要在允許自行新增時才行。"""
        return bool(c.get("shared")) or self.can_own

    def visible(self, conn_id: int, with_report: bool = False) -> Optional[dict]:
        """自己的、或自己是成員的共用事務機；管理員看得到每一台共用事務機（管理頁要看詳細與報告）。
        清單（`list_visible`）不一樣：管理員自己的「事務機掃描取件」頁照樣只列他是成員的。"""
        return mfp_store.get_visible(self.owner, self.subjects, conn_id,
                                     all_shared=self.all_shared or self.admin, with_report=with_report)


def _user_label(request: Request) -> str:
    from ...core import sessions as _sess
    return _sess.user_label(getattr(request.state, "user", None)) or ""


# --------------------------------------------------------------------------- 次數限制與暫存結果

_LOCK = threading.Lock()
_recent: dict[str, list[float]] = {}
_busy: set[str] = set()
#: token → {owner, host, port, scheme, report, exp}
_detections: dict[str, dict] = {}


def _acquire(owner: str, browse: bool = False) -> Optional[str]:
    now = time.time()
    with _LOCK:
        hist = [t for t in _recent.get(owner, []) if now - t < RATE_WINDOW]
        if owner in _busy:
            return "BUSY"
        if len(hist) >= (BROWSE_RATE_LIMIT if browse else RATE_LIMIT):
            _recent[owner] = hist
            return "BROWSE_RATE_LIMITED" if browse else "RATE_LIMITED"
        hist.append(now)
        _recent[owner] = hist
        _busy.add(owner)
    return None


def _release(owner: str) -> None:
    with _LOCK:
        _busy.discard(owner)


def _remember(owner: str, host: str, port, scheme, report: dict) -> str:
    now = time.time()
    tok = secrets.token_urlsafe(24)
    with _LOCK:
        for k in [k for k, v in _detections.items() if v["exp"] < now]:
            _detections.pop(k, None)
        mine = sorted((v["exp"], k) for k, v in _detections.items() if v["owner"] == owner)
        for _exp, k in mine[:-20]:
            _detections.pop(k, None)
        _detections[tok] = {"owner": owner, "host": host, "port": port, "scheme": scheme,
                            "report": report, "exp": now + DETECTION_TTL}
    return tok


def _peek(owner: str, tok: str) -> Optional[dict]:
    """看一筆還沒儲存的連線測試（下載診斷報告用；不取走，之後照樣可以儲存）。"""
    with _LOCK:
        d = _detections.get(tok or "")
        if not d or d["owner"] != owner or d["exp"] < time.time():
            return None
        return d


def _take(owner: str, tok: str) -> Optional[dict]:
    with _LOCK:
        d = _detections.get(tok or "")
        if not d or d["owner"] != owner or d["exp"] < time.time():
            return None
        return _detections.pop(tok)


# --------------------------------------------------------------------------- 稽核

def _client_ip(request: Request) -> str:
    from ...core import client_ip
    try:
        return client_ip.real_client_ip(request) or ""
    except Exception:  # noqa: BLE001
        return ""


def _audit(request: Request, event: str, target: str, details: dict) -> None:
    try:
        from ...core import audit_db, client_ip, sessions as _sess
        audit_db.log_event(event, username=_sess.user_label(getattr(request.state, "user", None)) or "",
                           ip=client_ip.real_client_ip(request), target=target[:200], details=details)
    except Exception:  # noqa: BLE001 —— 稽核寫不進去不可以讓操作失敗
        logger.warning("mfp 稽核寫入失敗：%s", event)


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def _norm_port(v) -> Optional[int]:
    if v in (None, "", "auto"):
        return None
    return v  # 由 mfp_client.parse_port_scheme 驗證


def _norm_scheme(v) -> Optional[str]:
    return None if v in (None, "", "auto") else v


def _policy_error(e: mfp_client.PolicyError) -> JSONResponse:
    return JSONResponse({"ok": False, "code": e.code, "message": e.message, "detail": e.detail},
                        status_code=400)


def _queue_error(e: mfp_queue.QueueError) -> JSONResponse:
    return JSONResponse({"ok": False, "code": e.code, "message": e.message, "waiting": e.waiting},
                        status_code=429)


def _probe_in_turn(owner: str, host: str, port, scheme) -> dict:
    """輪到這台設備才做連線測試（同一台一次只做一件）。"""
    device = mfp_client.device_key(host)
    with mfp_queue.turn(device, owner, "test", timeout=PROBE_WAIT):
        return mfp_client.probe(host, port, scheme)


async def _run_probe(owner: str, host: str, port, scheme) -> tuple[Optional[dict], Optional[JSONResponse]]:
    busy = _acquire(owner)
    if busy:
        return None, _err(busy, 429)
    try:
        mfp_client.parse_port_scheme(port, scheme)          # 先驗輸入（不合法就不排隊、不連線）
        report = await asyncio.to_thread(_probe_in_turn, owner, host, port, scheme)
        return report, None
    except mfp_client.PolicyError as e:
        return None, _policy_error(e)
    except mfp_queue.QueueError as e:
        return None, _queue_error(e)
    finally:
        _release(owner)


def _device_state(host: str) -> dict:
    """清單用：這台現在有沒有在忙、後面排了幾件（查不到位址就當閒置；不連線）。"""
    try:
        ip = str(ipaddress.ip_address(host))
    except ValueError:
        ip = _resolved.get(host)
        if not ip:
            return {"busy": False, "waiting": 0, "kind": None}
    return mfp_queue.snapshot(ip)


#: 主機名稱 → 最近一次排隊用的位址（清單顯示忙碌狀態用；不為了顯示去查 DNS）。
_resolved: dict[str, str] = {}


def _with_state(c: dict, who: _Who) -> dict:
    c = dict(c)
    c["queue"] = _device_state(c["host"])
    # 共用事務機一律在管理頁（設定 → 共用事務機）改名、移除；工具頁上的這兩個動作只對自己的
    c["can_manage"] = not c.get("shared")
    escl = c.get("escl") or None
    c["scan"] = ({"model": escl.get("model"), "sources": escl.get("sources") or {}}
                 if escl and escl.get("sources") else None)
    c.pop("escl", None)
    # 機內信箱取件（推測實作）：FUJIFILM、而且連線測試連得上
    c["box"] = c.get("vendor") == "FUJIFILM" and bool(c.get("reachable_on"))
    # 檔案分享取件（實驗功能）：Canon Advanced Box（WebDAV / SMB）、Konica 公用信箱、SHARP NAS（SMB）、
    # KYOCERA 文件匣與 RICOH 文件伺服器（SOAP）。WebDAV、RICOH 要用連線測試連得上的那組協定與連接埠
    routes = [r for r in mfp_share.routes_for(c.get("vendor"))
              if not mfp_share.needs_reachable_target(r) or c.get("reachable_on")]
    c["share"] = {"routes": routes} if routes else None
    # 管理員關掉「使用者自行新增事務機」：自己的暫停使用（資料留著，可以移除）
    c["paused"] = not c.get("shared") and not who.can_own
    return c


# --------------------------------------------------------------------------- 頁面

def page_context(request: Request, mode: str = "") -> dict:
    """工具頁與「設定 → 共用事務機」共用同一份模板，**資料也只在這裡組一次**
    （原本兩支路由各自組，工具頁加了東西、管理頁就漏了）。"""
    from ...core import auth_settings
    # 未啟用登入（單機模式）：沒有帳號，畫面上「只有你的帳號看得到」「你是管理員」這類說明都不成立
    return {"request": request, "mode": mode, "single": not auth_settings.is_enabled(),
            "allowed_ports": list(mfp_client.ALLOWED_PORTS),
            "status_messages": mfp_profiles.STATUS_MESSAGES,
            "status_labels": {k: list(v) for k, v in mfp_profiles.STATUS_LABELS.items()},
            "field_tests": mfp_profiles.FIELD_TESTS, "field_result_labels": mfp_profiles.FIELD_RESULT_LABELS}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "mfp_fetch.html", page_context(request))


# --------------------------------------------------------------------------- API：連線測試與我的事務機

@router.post("/api/detect")
async def api_detect(request: Request):
    """連線測試（不儲存）。回傳完整報告與一個 15 分鐘內有效、只屬於本人的 token，用來「儲存這台」。

    管理員關掉「使用者自行新增事務機」時，一般使用者回 403（管理員建立共用事務機也走這支，不受限）。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    if not who.can_own:
        return _err("USER_DEVICES_DISABLED", 403)
    owner = who.owner
    body = await _json(request)
    host = str(body.get("host") or "")[:300]
    port, scheme = _norm_port(body.get("port")), _norm_scheme(body.get("scheme"))
    report, err = await _run_probe(owner, host, port, scheme)
    _audit(request, "mfp_detect", host, {
        "ok": err is None, "status": (report or {}).get("detection", {}).get("status"),
        "reachable": (report or {}).get("summary", {}).get("reachable_on")})
    if err:
        return err
    # 存正規化後的值（已經過 parse_port_scheme 驗證）：數字的連接埠、小寫的協定
    port = int(port) if port is not None else None
    scheme = str(scheme).lower() if scheme else None
    tok = _remember(owner, report["target"]["host"], port, scheme, report)
    return {"ok": True, "token": tok, "report": report}


@router.get("/api/connections")
async def api_list(request: Request):
    """我的事務機：自己新增的＋自己是成員的共用事務機（`shared: true`）。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    items = mfp_store.list_visible(who.owner, who.subjects, all_shared=who.all_shared)
    # 目前登入的是哪個帳號（`帳號@來源`）：同一個人可能有 AD、SSO、本機好幾個帳號，共用事務機的成員是按帳號算的。
    # 客戶 2026-10-21：關掉瀏覽器重開後共用的「消失」—— 畫面上要看得出這次登入的是哪一個
    from ...core import sessions
    you = sessions.user_label(getattr(request.state, "user", None)) if not who.all_shared else ""
    return {"ok": True, "connections": [_with_state(c, who) for c in items],
            "max": mfp_store.MAX_PER_OWNER, "admin": who.admin, "can_add": who.can_own, "you": you}


@router.post("/api/connections")
async def api_create(request: Request):
    """把一次連線測試的結果存成「我的事務機」。只收 token（不收位址）：存的一定是測過的那一台。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    if not who.can_own:
        return _err("USER_DEVICES_DISABLED", 403)
    owner, uid = who.owner, who.uid
    body = await _json(request)
    det = _take(owner, str(body.get("token") or ""))
    if det is None:
        return _err("DETECTION_EXPIRED", 410)
    try:
        conn = mfp_store.create(owner, uid, det["host"], det["port"], det["scheme"],
                                str(body.get("display_name") or ""), det["report"])
    except mfp_store.StoreError as e:
        return _err(e.code, 409)
    _audit(request, "mfp_connection_create", det["host"], {"id": conn["id"], "model": conn.get("model")})
    return {"ok": True, "connection": conn}


@router.get("/api/connections/{conn_id}")
async def api_get(request: Request, conn_id: int):
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c = who.visible(conn_id, with_report=True)
    return {"ok": True, "connection": _with_state(c, who) | {"last_report": c.get("last_report")}} \
        if c else _err("NOT_FOUND", 404)


@router.patch("/api/connections/{conn_id}")
async def api_rename(request: Request, conn_id: int):
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    c = who.visible(conn_id)
    if c and c.get("shared"):
        return _err("SHARED_READ_ONLY", 403)
    c = mfp_store.rename(who.owner, conn_id, str(body.get("display_name") or ""))
    if not c:
        return _err("NOT_FOUND", 404)
    _audit(request, "mfp_connection_rename", c["host"], {"id": conn_id})
    return {"ok": True, "connection": c}


async def _retest(request: Request, who: _Who, c: dict) -> Any:
    report, err = await _run_probe(who.owner, c["host"], c["port"], c["scheme"])
    _audit(request, "mfp_test", c["host"], {
        "id": c["id"], "ok": err is None, "shared": bool(c.get("shared")),
        "status": (report or {}).get("detection", {}).get("status")})
    if err:
        return err
    scope = mfp_store.SHARED if c.get("shared") else who.owner
    c2 = mfp_store.save_test(scope, c["id"], report)
    return {"ok": True, "connection": _with_state(c2, who), "report": report}


@router.post("/api/connections/{conn_id}/test")
async def api_test(request: Request, conn_id: int):
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c = who.visible(conn_id)
    if not c:
        return _err("NOT_FOUND", 404)
    if not who.usable(c):
        return _err("USER_DEVICES_DISABLED", 403)
    return await _retest(request, who, c)


async def _report_response(request: Request, report: dict, host: str, when: float, fmt: str,
                           target: str):
    """把（產生時就已遮蔽的）報告換成指定格式下載。PDF 排版要幾百毫秒，放到執行緒做。"""
    from ...core import mfp_report, ui_locale
    fmt = (fmt or "json").strip().lower()
    if fmt not in mfp_report.FORMATS:
        return _err("REPORT_FORMAT_INVALID", 422)
    locale = ui_locale.resolve(request)
    data, media, ext = await asyncio.to_thread(mfp_report.render, report, fmt, locale)
    _audit(request, "mfp_report_download", target, {"format": fmt})
    name = mfp_report.filename(host, time.strftime("%Y%m%d-%H%M%S", time.localtime(when)), ext)
    return Response(data, media_type=media,
                    headers={"Content-Disposition": content_disposition(name, "attachment"),
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/connections/{conn_id}/report")
async def api_report(request: Request, conn_id: int, format: str = "json"):  # noqa: A002 —— 對外參數名
    """下載最近一次連線測試的診斷報告（已遮蔽帳密與工作階段資訊）。

    `format`：`json`（預設，原始報告）、`txt`、`md`、`html`、`pdf`；後四種的固定字串依介面語言。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c = who.visible(conn_id, with_report=True)
    if not c:
        return _err("NOT_FOUND", 404)
    if not c.get("last_report"):
        return _err("NO_REPORT", 404)
    return await _report_response(request, c["last_report"], c["host"], c.get("last_test_at") or time.time(),
                                  format, c["host"])


@router.post("/api/detect/report")
async def api_detect_report(request: Request):
    """還沒儲存的連線測試結果下載診斷報告：`{"token": "…", "format": "pdf"}`（token 是連線測試回的那一個，
    只有本人能用、15 分鐘內有效；下載不會用掉它，之後照樣可以儲存）。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    det = _peek(who.owner, str(body.get("token") or ""))
    if not det:
        return _err("REPORT_EXPIRED", 404)
    return await _report_response(request, det["report"], det["host"], time.time(),
                                  str(body.get("format") or "json"), det["host"])


@router.delete("/api/connections/{conn_id}")
async def api_delete(request: Request, conn_id: int):
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c = who.visible(conn_id)
    if c and c.get("shared"):
        return _err("SHARED_READ_ONLY", 403)
    c = mfp_store.get_for(who.owner, conn_id)
    if not c or not mfp_store.delete(who.owner, conn_id):
        return _err("NOT_FOUND", 404)
    _audit(request, "mfp_connection_delete", c["host"], {"id": conn_id})
    return {"ok": True}


# --------------------------------------------------------------------------- API：掃描

def _scan_target(c: dict) -> tuple[dict, str, int]:
    """這台用哪個協定與連接埠掃描（連線測試時讀到 eSCL 的那一組）。"""
    rep = c.get("last_report") or {}
    escl = rep.get("escl") or {}
    caps = escl.get("caps") or {}
    if not caps or not escl.get("sources"):
        raise mfp_escl.ScanError("SCAN_NOT_SUPPORTED")
    sc, pt = str(escl.get("scheme") or ""), escl.get("port")
    pairs = mfp_client.parse_port_scheme(pt, sc)       # 再驗一次（紀錄被竄改也不會連到別的埠）
    return caps, pairs[0][0], pairs[0][1]


@router.post("/api/connections/{conn_id}/scan")
async def api_scan(request: Request, conn_id: int):
    """從這台事務機掃描，存進自己的工作區。回傳掃描紀錄（202）；用 `GET /api/scans/{id}` 看進度。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c = who.visible(conn_id, with_report=True)
    if not c:
        return _err("NOT_FOUND", 404)
    if not who.usable(c):
        return _err("USER_DEVICES_DISABLED", 403)
    body = await _json(request)
    opts = {k: body.get(k) for k in ("source", "color", "resolution", "paper", "file_name", "format")}
    # 先暫存、最後合併（使用者 2026-11-03）：掃完不存進工作區，畫面預覽、接著掃，最後 `/api/scans/merge`
    opts["hold"] = body.get("hold") is True
    try:
        caps, scheme, port = _scan_target(c)
        device = await asyncio.to_thread(mfp_client.device_key, c["host"])
        _resolved[c["host"]] = device
        rec = mfp_scan.start(owner=who.owner, user_id=who.uid, user_label=_user_label(request),
                       client_ip=_client_ip(request), conn=c,
                             caps=caps, opts=opts, device=device, ip=device, scheme=scheme, port=port)
    except mfp_client.PolicyError as e:
        return _policy_error(e)
    except mfp_queue.QueueError as e:
        return _queue_error(e)
    except mfp_escl.ScanError as e:
        return JSONResponse({"ok": False, "code": e.code, "message": e.message, "detail": e.detail},
                            status_code=400)
    _audit(request, "mfp_scan_start", c["host"], {"id": conn_id, "scan": rec["id"], "shared": bool(c.get("shared")),
                                                  "settings": rec.get("settings")})
    return JSONResponse({"ok": True, "scan": mfp_scan.public(rec)}, status_code=202)


@router.get("/api/scans")
async def api_scans(request: Request):
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    mfp_scan.prune()
    # 事務機名稱用**現在的**：紀錄裡存的是當時的名稱，改名後不會跟著變；刪掉的那台標出來（紀錄照樣可以下載）。
    # 客戶 2026-10-21：「取件那邊名字不會變」「刪除的機器不會消失」
    out, seen = [], {}
    for r in mfp_store.list_scans(who.owner, 20):
        d = mfp_scan.public(r)
        cid = d.get("conn_id")
        if cid is not None:
            if cid not in seen:
                seen[cid] = who.visible(int(cid))
            cur = seen[cid]
            d["conn_removed"] = cur is None
            if cur:
                d["conn_name"] = cur.get("display_name") or cur.get("host") or d.get("conn_name")
        out.append(d)
    return {"ok": True, "scans": out}


@router.get("/api/scans/held")
async def api_scans_held(request: Request, conn_id: Optional[int] = None):
    """暫存著還沒合併的掃描（畫面重新整理後接回來）。`conn_id` 只列這一台的。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    mfp_scan.prune()
    return {"ok": True, "scans": await asyncio.to_thread(mfp_scan.list_held, who.owner, conn_id)}


@router.get("/api/scans/{scan_id}/preview/{page}")
async def api_scan_preview(request: Request, scan_id: str, page: int, w: int = 240):
    """暫存掃描第 `page` 頁（從 0 起算）的預覽圖（PNG）。`w`：240（縮圖）或 1200（放大看）。只有本人看得到。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    if not re.fullmatch(r"[0-9a-f]{32}", scan_id or ""):
        return _err("SCAN_NOT_FOUND", 404)
    got = await asyncio.to_thread(mfp_scan.preview, who.owner, scan_id, page, w)
    if not got:
        return _err("SCAN_NO_FILE", 404)
    return FileResponse(got, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.post("/api/scans/merge")
async def api_scans_merge(request: Request):
    """暫存的掃描照畫面上的順序合成一份多頁 PDF，存進工作區。

    Body：`{"pages": [["作業 id", 第幾頁], …], "file_name": "…", "release": ["作業 id", …]}`
    （`release`：合併後一起清掉的暫存掃描，例如整次都刪掉頁面的那幾次）。回傳新的作業紀錄。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    release = [str(x) for x in (body.get("release") or []) if isinstance(x, str)][:mfp_scan.MAX_HELD]
    try:
        rec = await asyncio.to_thread(
            mfp_scan.merge, owner=who.owner, user_id=who.uid, user_label=_user_label(request),
            pages=body.get("pages"), file_name=str(body.get("file_name") or ""), release=release)
    except mfp_scan.MergeError as e:
        return JSONResponse({"ok": False, "code": e.code, "message": e.message},
                            status_code=404 if e.code == "MERGE_SOURCE_GONE" else 400)
    _audit(request, "mfp_scan_merge", rec.get("conn_name") or "",
           {"scan": rec.get("id"), "pages": rec.get("pages"),
            "sources": len({str(x[0]) for x in body.get("pages") or [] if isinstance(x, list) and x})})
    return {"ok": True, "scan": mfp_scan.public(rec)}


@router.post("/api/scans/discard")
async def api_scans_discard(request: Request):
    """清除暫存著還沒合併的掃描：`{"scans": ["作業 id", …]}`。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    ids = [str(x) for x in (body.get("scans") or []) if isinstance(x, str)][:mfp_scan.MAX_HELD]
    n = await asyncio.to_thread(mfp_scan.discard, who.owner, ids)
    if n:
        _audit(request, "mfp_scan_discard", "", {"scans": n})
    return {"ok": True, "discarded": n}


@router.get("/api/scans/{scan_id}")
async def api_scan_status(request: Request, scan_id: str):
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    rec = mfp_store.get_scan(who.owner, scan_id)
    return {"ok": True, "scan": mfp_scan.public(rec)} if rec else _err("SCAN_NOT_FOUND", 404)


@router.post("/api/scans/{scan_id}/cancel")
async def api_scan_cancel(request: Request, scan_id: str):
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    rec = mfp_scan.cancel(who.owner, scan_id)
    if not rec:
        return _err("SCAN_NOT_FOUND", 404)
    _audit(request, "mfp_scan_cancel", rec.get("conn_name") or "", {"scan": scan_id})
    return {"ok": True, "scan": mfp_scan.public(rec)}


@router.get("/api/scans/{scan_id}/download")
async def api_scan_download(request: Request, scan_id: str):
    """沒存進工作區的檔案（工作區停用或額度不夠時，留 24 小時）：一個檔就直接給，多個打包成 ZIP。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    got = mfp_scan.result_files(who.owner, scan_id)
    if not got:
        return _err("SCAN_NO_FILE", 404)
    if len(got) == 1:
        path, name = got[0]
        return FileResponse(path, media_type=_MEDIA.get(path.suffix.lower(), "application/octet-stream"),
                            filename=name, headers={"Cache-Control": "no-store"})
    z = await asyncio.to_thread(mfp_scan.bundle, who.owner, scan_id)
    if not z:
        return _err("SCAN_NO_FILE", 404)
    rec = mfp_store.get_scan(who.owner, scan_id) or {}
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(rec.get("finished_at") or time.time()))
    return FileResponse(z, media_type="application/zip", filename=f"mfp-{stamp}.zip",
                        headers={"Cache-Control": "no-store"})


_MEDIA = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".tif": "image/tiff"}


# --------------------------------------------------------------------------- API：機內信箱取件（推測實作）

_FP_RE = re.compile(r"^[0-9a-f]{64}$")


def _box_target(c: dict, want: Any) -> tuple[str, int]:
    """取件用哪個協定與連接埠：連線測試連得上的那幾組裡挑（沒指定就 HTTPS 優先）。"""
    reach = [r for r in (c.get("reachable_on") or []) if isinstance(r, str)]
    if not reach:
        raise mfp_box.BoxError("BOX_NO_TARGET")
    if want:
        if str(want) not in reach:
            raise mfp_box.BoxError("BOX_TARGET_INVALID")
        pick = str(want)
    else:
        pick = next((r for r in reach if r.startswith("https:")), reach[0])
    sc, _, pt = pick.partition(":")
    pairs = mfp_client.parse_port_scheme(pt, sc)        # 再驗一次（紀錄被竄改也不會連到別的埠）
    return pairs[0]


def _box_inputs(body: dict) -> tuple[int, "mfp_box.Creds", Optional[str], bool]:
    box = mfp_box.parse_box(body.get("box"))
    creds = mfp_box.Creds(body.get("box_password") or "", body.get("user") or "", body.get("password") or "")
    fp = str(body.get("trust_sha256") or "").replace(":", "").strip().lower()
    trust = fp if _FP_RE.match(fp) else None
    return box, creds, trust, body.get("allow_http") is True


def _box_error(e: "mfp_box.BoxError", trace: Optional[list] = None) -> JSONResponse:
    """設備端的錯誤回 422（不是 401：那會讓畫面以為是本站登入過期）。"""
    return JSONResponse({"ok": False, "code": e.code, "message": e.message, "detail": e.detail,
                         "trace": trace or []}, status_code=422)


def _box_conn(who: _Who, conn_id: int) -> tuple[Optional[dict], Optional[JSONResponse]]:
    c = who.visible(conn_id, with_report=False)
    if not c:
        return None, _err("NOT_FOUND", 404)
    if not who.usable(c):
        return None, _err("USER_DEVICES_DISABLED", 403)
    if c.get("vendor") != "FUJIFILM":
        return None, _err("BOX_NOT_FUJIFILM", 400)
    return c, None


def _effective(who: "_Who", c: dict, kind: str, trust, allow_http: bool, allow_plain: bool = False):
    """憑證指紋與「同意未加密」由伺服器決定，不只信請求帶來的值（v1.17.5）：
    * 共用事務機：管理員設過的就用管理員那一份 —— 成員在請求裡帶別的指紋也沒用（不然等於繞過管理員確認過的憑證）；
    * 其他情況：請求沒帶就用這個人存過的（換一台電腦不用重新信任）。"""
    prefs = mfp_store.get_prefs(who.owner, c, kind)
    managed = set(prefs.get("managed") or [])
    if "trust" in managed or not trust:
        trust = prefs.get("trust") or (None if "trust" in managed else trust)
    if "http" in managed:
        allow_http = prefs.get("http") is True
    elif not allow_http:
        allow_http = prefs.get("http") is True
    if "plain" in managed:
        allow_plain = prefs.get("plain") is True
    elif not allow_plain:
        allow_plain = prefs.get("plain") is True
    return trust, allow_http, allow_plain


def _box_list_in_turn(owner: str, c: dict, sc: str, pt: int, box: int, creds, trust, allow_http,
                      trace: list) -> dict:
    device = mfp_client.device_key(c["host"])
    _resolved[c["host"]] = device
    with mfp_queue.turn(device, owner, "box", timeout=PROBE_WAIT):
        sess = mfp_box.open_box(c["host"], device, sc, pt, box=box, creds=creds, trust_sha256=trust,
                                allow_http=allow_http, trace=trace)
    return dict(sess.public(), via=sess.via(), tls=sess.tls(), qnames=sess.qnames[:80])


@router.post("/api/connections/{conn_id}/box/list")
async def api_box_list(request: Request, conn_id: int):
    """列出機內信箱裡的文件（**推測實作**：WebDAV PROPFIND）。帳密只用在這次請求，不儲存。

    設備端的錯誤（認證、憑證、找不到信箱）回 422，附協定紀錄（`trace`，不含帳密）。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c, err = _box_conn(who, conn_id)
    if err:
        return err
    body = await _json(request)
    trace: list = []
    busy = _acquire(who.owner + "#box", browse=True)
    if busy:
        return _err(busy, 429)
    try:
        box, creds, trust, allow_http = _box_inputs(body)
        trust, allow_http, _p = _effective(who, c, "box", trust, allow_http)
        sc, pt = _box_target(c, body.get("target"))
        res = await asyncio.to_thread(_box_list_in_turn, who.owner, c, sc, pt, box, creds, trust,
                                      allow_http, trace)
    except mfp_client.PolicyError as e:
        return _policy_error(e)
    except mfp_queue.QueueError as e:
        return _queue_error(e)
    except mfp_box.BoxError as e:
        _audit(request, "mfp_box_list", c["host"], {"id": conn_id, "ok": False, "code": e.code,
                                                    "box": body.get("box")})
        return _box_error(e, trace)
    finally:
        _release(who.owner + "#box")
    _audit(request, "mfp_box_list", c["host"], {"id": conn_id, "ok": True, "box": res["box"],
                                                "documents": len(res["documents"])})
    return {"ok": True, **res, "trace": trace}


@router.post("/api/connections/{conn_id}/box/fetch")
async def api_box_fetch(request: Request, conn_id: int):
    """取件（背景作業，**推測實作**）：重新開信箱、逐份下載、存進自己的工作區（預設 PDF）。回 202 與作業紀錄。

    `documents` 是 `box/list` 回的文件編號；`output` 是 `pdf`（預設）或 `original`（設備給的原始格式）。
    信箱或文件設定成「取件後刪除」時，要 `confirm_delete_after_get: true` 才會取。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c, err = _box_conn(who, conn_id)
    if err:
        return err
    body = await _json(request)
    docs = body.get("documents")
    if not isinstance(docs, list):
        return _box_error(mfp_box.BoxError("NO_DOCUMENTS_SELECTED"))
    try:
        box, creds, trust, allow_http = _box_inputs(body)
        trust, allow_http, _p = _effective(who, c, "box", trust, allow_http)
        sc, pt = _box_target(c, body.get("target"))
        device = await asyncio.to_thread(mfp_client.device_key, c["host"])
        _resolved[c["host"]] = device
        rec = mfp_scan.start_box(owner=who.owner, user_id=who.uid, user_label=_user_label(request),
                       client_ip=_client_ip(request), conn=c,
                                 device=device, ip=device, scheme=sc, port=pt, box=box, creds=creds,
                                 doc_ids=[str(d) for d in docs][:mfp_box.MAX_DOCS_PER_JOB + 1],
                                 output=str(body.get("output") or "pdf"), trust_sha256=trust,
                                 allow_http=allow_http,
                                 # 「取件並刪除」本身就是使用者要刪 —— 信箱的取件後刪除設定不用另外確認
                                 confirm_delete_after_get=(body.get("confirm_delete_after_get") is True
                                                           or body.get("delete_after") is True),
                                 delete_after=body.get("delete_after") is True)
    except mfp_client.PolicyError as e:
        return _policy_error(e)
    except mfp_queue.QueueError as e:
        return _queue_error(e)
    except mfp_box.BoxError as e:
        return _box_error(e)
    _audit(request, "mfp_box_fetch", c["host"], {"id": conn_id, "scan": rec["id"], "box": box,
                                                 "delete_after": bool(rec["settings"].get("delete_after")),
                                                 "documents": [str(d) for d in docs][:20],
                                                 "output": rec["settings"].get("output"),
                                                 "shared": bool(c.get("shared"))})
    return JSONResponse({"ok": True, "scan": mfp_scan.public(rec)}, status_code=202)


# --------------------------------------------------------------------------- API：檔案分享取件（實驗功能）

def _share_conn(who: _Who, conn_id: int, route: str) -> tuple[Optional[dict], Optional[JSONResponse]]:
    c = who.visible(conn_id, with_report=False)
    if not c:
        return None, _err("NOT_FOUND", 404)
    if not who.usable(c):
        return None, _err("USER_DEVICES_DISABLED", 403)
    if route not in mfp_share.routes_for(c.get("vendor")):
        return None, _err("SHARE_ROUTE_INVALID", 400)
    return c, None


_RICOH_MODES = ("auto", "none", "account", "user_code", "user_code_ext")


def _share_inputs(body: dict) -> dict:
    fp = str(body.get("trust_sha256") or "").replace(":", "").strip().lower()
    return {"user": str(body.get("user") or "")[:256], "password": str(body.get("password") or "")[:256],
            "box": str(body.get("box") or "").strip()[:9], "box_password": str(body.get("box_password") or "")[:128],
            "trust_sha256": fp if _FP_RE.match(fp) else None, "allow_http": body.get("allow_http") is True,
            "allow_plain": body.get("allow_plain") is True,
            # RICOH 的登入方式與 User Code（v1.17.0）；其他路線不用
            "auth_mode": mode if (mode := str(body.get("auth_mode") or "auto")) in _RICOH_MODES else "invalid",
            "user_code": str(body.get("user_code") or "")[:32],
            # RICOH 文件伺服器的資料夾密碼（4～8 位數字；當字串、保留開頭的 0）—— 跟登入密碼、文件密碼分開
            "folder_password": str(body.get("folder_password") or "")[:32]}


def _share_target(c: dict, route: str, want: Any) -> tuple[Optional[str], Optional[int]]:
    """WebDAV、RICOH：連線測試連得上的那組；KYOCERA 的連接埠由路線決定；SMB 是 445。"""
    if not mfp_share.needs_reachable_target(route):
        return None, None
    return _box_target(c, want)


def _share_list_in_turn(owner: str, c: dict, route: str, sc, pt, inp: dict, root: str, path: list,
                        trace: list) -> dict:
    device = mfp_client.device_key(c["host"])
    _resolved[c["host"]] = device
    with mfp_queue.turn(device, owner, "share", timeout=PROBE_WAIT):
        sess = mfp_share.open_session(route, host=c["host"], ip=device, scheme=sc, port=pt, user=inp["user"],
                                      password=inp["password"], box=inp["box"], box_password=inp["box_password"],
                                      trust_sha256=inp["trust_sha256"], allow_http=inp["allow_http"],
                                      allow_plain=inp["allow_plain"], trace=trace, auth_mode=inp["auth_mode"],
                                      user_code=inp["user_code"], folder_password=inp["folder_password"])
        try:
            if mfp_share.ROUTES[route]["auth"] == "box":
                root = mfp_share.konica_box_share(sess, inp["box"])
                roots = [{"id": root, "label": root, "shared": True}]
            else:
                roots = sess.roots()
                if not roots:
                    raise mfp_share.ShareError("SHARE_NOT_FOUND")
                if not root:
                    root = roots[0]["id"]
                elif root not in [r["id"] for r in roots]:
                    raise mfp_share.ShareError("SHARE_ROOT_INVALID")
            # RICOH 有密碼的資料夾：要密碼、密碼不對、被鎖 —— 照樣回位置清單（畫面才留得住選單與密碼欄），
            # 狀態另外帶；密碼只驗一次，不重試
            folder_state = None
            try:
                entries = sess.list(root, path)
            except mfp_share.ShareError as e:
                if e.code not in ("FOLDER_PASSWORD_REQUIRED", "FOLDER_PASSWORD_FAILED", "FOLDER_LOCKED"):
                    raise
                entries, folder_state = [], {"code": e.code, "message": mfp_share.MESSAGES.get(e.code) or e.code}
            shared = next((r["shared"] for r in roots if r["id"] == root), True)
            # 「自動判斷」實際用了哪一種登入方式（目前只有 RICOH 會在帳號空白時自動改用「不需登入」）
            auto_none = bool(getattr(sess, "auto_fallback_none", False))
            return {"route": route, "roots": roots, "root": root, "path": path, "entries": entries,
                    "shared": shared, "via": sess.via(), "tls": sess.tls(),
                    "auth_mode_used": getattr(sess, "auth_mode", None), "auth_auto_none": auto_none,
                    # 資料夾清單讀不到（只列得出共用資料夾）：照實告訴畫面，不當成沒有資料夾
                    "folder_error": getattr(sess, "folder_error", None), "folder_state": folder_state}
        finally:
            sess.close()


@router.post("/api/connections/{conn_id}/share/list")
async def api_share_list(request: Request, conn_id: int):
    """列出檔案分享裡的資料夾與檔案（**實驗功能**）。帳密只用在這次請求，不儲存；設備端的錯誤回 422 附協定紀錄。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    route = str(body.get("route") or "")
    c, err = _share_conn(who, conn_id, route)
    if err:
        return err
    trace: list = []
    busy = _acquire(who.owner + "#box", browse=True)
    if busy:
        return _err(busy, 429)
    try:
        inp = _share_inputs(body)
        inp["trust_sha256"], inp["allow_http"], inp["allow_plain"] = _effective(
            who, c, "share", inp["trust_sha256"], inp["allow_http"], inp["allow_plain"])
        path = mfp_share.check_path(body.get("path") if body.get("path") is not None else [])
        root = str(body.get("root") or "")
        if root and not mfp_share.valid_segment(root):
            raise mfp_share.ShareError("SHARE_ROOT_INVALID")
        sc, pt = _share_target(c, route, body.get("target"))
        res = await asyncio.to_thread(_share_list_in_turn, who.owner, c, route, sc, pt, inp, root, path, trace)
    except mfp_client.PolicyError as e:
        return _policy_error(e)
    except mfp_queue.QueueError as e:
        return _queue_error(e)
    except mfp_box.BoxError as e:
        _audit(request, "mfp_share_list", c["host"], {"id": conn_id, "route": route, "ok": False, "code": e.code})
        return _box_error(e, trace)
    finally:
        _release(who.owner + "#box")
    _audit(request, "mfp_share_list", c["host"], {"id": conn_id, "route": route, "ok": True, "root": res["root"],
                                                  "entries": len(res["entries"])})
    return {"ok": True, **res, "trace": trace}


@router.post("/api/connections/{conn_id}/share/fetch")
async def api_share_fetch(request: Request, conn_id: int):
    """取件（背景作業，**實驗功能**）：`files` 是路徑陣列的陣列（例如 `[["2026", "合約.pdf"]]`）。回 202 與作業紀錄。"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    route = str(body.get("route") or "")
    c, err = _share_conn(who, conn_id, route)
    if err:
        return err
    if mfp_share.ROUTES.get(route, {}).get("transport") == "soap" and str(body.get("root") or "") == "polling":
        # KYOCERA 遙控資料盒：取件參數還沒確認，連排隊都不排（不送猜的值）
        return _box_error(mfp_share.ShareError("KYOCERA_POLLING_DOWNLOAD_UNCONFIRMED"))
    try:
        inp = _share_inputs(body)
        inp["trust_sha256"], inp["allow_http"], inp["allow_plain"] = _effective(
            who, c, "share", inp["trust_sha256"], inp["allow_http"], inp["allow_plain"])
        sc, pt = _share_target(c, route, body.get("target"))
        device = await asyncio.to_thread(mfp_client.device_key, c["host"])
        _resolved[c["host"]] = device
        rec = mfp_scan.start_share(owner=who.owner, user_id=who.uid, user_label=_user_label(request),
                       client_ip=_client_ip(request), conn=c,
                                   device=device, ip=device, route=route, scheme=sc, port=pt,
                                   root=str(body.get("root") or ""), files=body.get("files"),
                                   output=str(body.get("output") or "pdf"),
                                   creds={k: inp[k] for k in ("user", "password", "box", "box_password",
                                                              "auth_mode", "user_code", "folder_password")},
                                   trust_sha256=inp["trust_sha256"], allow_http=inp["allow_http"],
                                   allow_plain=inp["allow_plain"])
    except mfp_client.PolicyError as e:
        return _policy_error(e)
    except mfp_queue.QueueError as e:
        return _queue_error(e)
    except mfp_box.BoxError as e:
        return _box_error(e)
    _audit(request, "mfp_share_fetch", c["host"], {"id": conn_id, "scan": rec["id"], "route": route,
                                                   "files": len(rec["settings"].get("files") or []),
                                                   "output": rec["settings"].get("output"),
                                                   "shared": bool(c.get("shared"))})
    return JSONResponse({"ok": True, "scan": mfp_scan.public(rec)}, status_code=202)


# --------------------------------------------------------------------------- API：共用事務機（管理員）

def _admin(request: Request) -> _Who:
    who = _Who(request)
    if not who.admin:
        raise _OwnerError("ADMIN_ONLY", 403)
    return who


@router.get("/api/settings")
async def api_settings_get(request: Request):
    """事務機掃描取件的管理員設定（目前只有「允許使用者自行新增事務機」）。"""
    try:
        _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    return {"ok": True, "settings": mfp_settings.get()}


@router.put("/api/settings")
async def api_settings_put(request: Request):
    """改設定：`{"allow_user_devices": true | false}`。只有管理員。"""
    try:
        _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    try:
        cur = mfp_settings.save({k: body[k] for k in ("allow_user_devices",) if k in body})
    except ValueError:
        return _err("SETTINGS_INVALID", 400)
    _audit(request, "mfp_settings_update", "mfp-fetch", {"allow_user_devices": cur["allow_user_devices"]})
    return {"ok": True, "settings": cur}


# --------------------------------------------------------------------------- 信任與偏好（v1.17.5）

@router.get("/api/connections/{conn_id}/prefs")
async def api_prefs_get(request: Request, conn_id: int, kind: str = "box"):
    """這個人用這台時記住的設定（信任的憑證、HTTP / 未加密的同意、上次的信箱編號與帳號）。
    原本存在瀏覽器，換一台電腦就不見；改存伺服器，跟著帳號走。**不含任何密碼。**"""
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    if kind not in mfp_store.PREF_KINDS:
        return _err("BAD_REQUEST", 400)
    c = who.visible(conn_id)
    if not c:
        return _err("NOT_FOUND", 404)
    return {"ok": True, "prefs": mfp_store.get_prefs(who.owner, c, kind)}


@router.put("/api/connections/{conn_id}/prefs")
async def api_prefs_put(request: Request, conn_id: int):
    try:
        who = _Who(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    kind = str(body.get("kind") or "")
    if kind not in mfp_store.PREF_KINDS:
        return _err("BAD_REQUEST", 400)
    c = who.visible(conn_id)
    if not c:
        return _err("NOT_FOUND", 404)
    patch = mfp_store.clean_prefs(body.get("patch"))
    try:
        prefs = mfp_store.save_prefs(who.owner, c, kind, patch, admin=who.admin)
    except mfp_store.StoreError as e:
        return _err(e.code, 409, message=e.message)
    if any(k in patch for k in ("trust", "http", "plain")):
        # 信任新的憑證、同意以未加密方式傳送：都是安全上的決定，寫稽核（記指紋前 16 碼，不記其他）
        _audit(request, "mfp_trust", c["host"], {
            "id": conn_id, "kind": kind, "shared": bool(c.get("shared")),
            "trust": (patch.get("trust") or "")[:16] or None,
            "http": patch.get("http"), "plain": patch.get("plain")})
    return {"ok": True, "prefs": prefs}


@router.get("/api/shared/local")
async def api_shared_local(request: Request):
    """未啟用登入時新增的事務機有幾台（啟用登入後任何帳號都看不到它們）。"""
    try:
        _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    from ...core import auth_settings
    return {"ok": True, "count": mfp_store.count_local() if auth_settings.is_enabled() else 0}


@router.post("/api/shared/adopt-local")
async def api_shared_adopt_local(request: Request):
    """把未啟用登入時新增的事務機轉成共用事務機（之後再按「成員」決定誰可以用）。"""
    try:
        _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    from ...core import auth_settings
    if not auth_settings.is_enabled():
        return _err("BAD_REQUEST", 400)
    moved = mfp_store.adopt_local_as_shared()
    _audit(request, "mfp_shared_adopt_local", "mfp-fetch", {"moved": moved})
    return {"ok": True, "moved": moved, "left": mfp_store.count_local()}


@router.get("/api/shared")
async def api_shared_list(request: Request):
    try:
        who = _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    rows = []
    for c in mfp_store.list_shared():
        v = _with_state(c, who)
        # 管理員自己是不是成員：是的話這台也出現在他自己的「事務機掃描取件」頁（掃描、取件在那裡）
        v["you_member"] = who.all_shared or mfp_store.is_member(int(c["id"]), who.subjects)
        rows.append(v)
    return {"ok": True, "connections": rows, "max": mfp_store.MAX_SHARED}


@router.post("/api/shared")
async def api_shared_create(request: Request):
    """把管理員自己做的一次連線測試（`/api/detect` 的 token）存成共用事務機。"""
    try:
        who = _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    det = _take(who.owner, str(body.get("token") or ""))
    if det is None:
        return _err("DETECTION_EXPIRED", 410)
    try:
        conn = mfp_store.create(mfp_store.SHARED, who.uid, det["host"], det["port"], det["scheme"],
                                str(body.get("display_name") or ""), det["report"])
    except mfp_store.StoreError as e:
        return _err(e.code, 409)
    _audit(request, "mfp_shared_create", det["host"], {"id": conn["id"], "model": conn.get("model")})
    return {"ok": True, "connection": conn}


@router.patch("/api/shared/{conn_id}")
async def api_shared_rename(request: Request, conn_id: int):
    try:
        _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    body = await _json(request)
    c = mfp_store.rename(mfp_store.SHARED, conn_id, str(body.get("display_name") or ""))
    if not c:
        return _err("NOT_FOUND", 404)
    _audit(request, "mfp_shared_rename", c["host"], {"id": conn_id})
    return {"ok": True, "connection": c}


@router.post("/api/shared/{conn_id}/test")
async def api_shared_test(request: Request, conn_id: int):
    try:
        who = _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c = mfp_store.get_for(mfp_store.SHARED, conn_id)
    if not c:
        return _err("NOT_FOUND", 404)
    return await _retest(request, who, c)


@router.delete("/api/shared/{conn_id}")
async def api_shared_delete(request: Request, conn_id: int):
    try:
        _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c = mfp_store.get_for(mfp_store.SHARED, conn_id)
    if not c or not mfp_store.delete(mfp_store.SHARED, conn_id):
        return _err("NOT_FOUND", 404)
    _audit(request, "mfp_shared_delete", c["host"], {"id": conn_id})
    return {"ok": True}


def _subject_names() -> tuple[dict, dict]:
    from ...core import group_manager, user_manager
    users = {str(u["id"]): u for u in user_manager.list_users()}
    groups = {str(g["id"]): g for g in group_manager.list_groups()}
    return users, groups


def _describe_members(members: list[dict]) -> list[dict]:
    """成員加上顯示名稱，與「有沒有這支工具的權限」（只對使用者算得出來）。"""
    from ...core import auth_settings, permissions
    users, groups = _subject_names()
    out = []
    for m in members:
        d = dict(m)
        if m["type"] == "user":
            u = users.get(m["key"])
            d["name"] = (u or {}).get("username") or ""
            d["display_name"] = (u or {}).get("display_name") or ""
            d["exists"] = u is not None
            d["can_use_tool"] = (u is not None and auth_settings.is_enabled()
                                 and permissions.user_can_use_tool(int(m["key"]), "mfp-fetch"))
        elif m["type"] == "group":
            g = groups.get(m["key"])
            d["name"] = (g or {}).get("name") or ""
            d["exists"] = g is not None
        else:
            d["name"] = m["key"]
            d["exists"] = True
        out.append(d)
    return out


@router.get("/api/shared/{conn_id}/members")
async def api_shared_members(request: Request, conn_id: int):
    try:
        _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    if not mfp_store.get_for(mfp_store.SHARED, conn_id):
        return _err("NOT_FOUND", 404)
    return {"ok": True, "members": _describe_members(mfp_store.get_members(conn_id))}


@router.put("/api/shared/{conn_id}/members")
async def api_shared_set_members(request: Request, conn_id: int):
    """整份換掉成員：`{"members": [{"type": "user"|"group"|"ou", "key": "..."}]}`。"""
    try:
        _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    c = mfp_store.get_for(mfp_store.SHARED, conn_id)
    if not c:
        return _err("NOT_FOUND", 404)
    body = await _json(request)
    raw = body.get("members")
    if not isinstance(raw, list):
        return _err("MEMBERS_INVALID", 400)
    users, groups = _subject_names()
    pairs: list[tuple[str, str]] = []
    for m in raw:
        if not isinstance(m, dict):
            return _err("MEMBERS_INVALID", 400)
        st, sk = str(m.get("type") or ""), str(m.get("key") or "")
        if (st == "user" and sk not in users) or (st == "group" and sk not in groups):
            return _err("MEMBERS_INVALID", 400)      # 只收存在的使用者與群組
        pairs.append((st, sk))
    try:
        members = mfp_store.set_members(conn_id, pairs)
    except (ValueError, LookupError):
        return _err("MEMBERS_INVALID", 400)
    _audit(request, "mfp_shared_members", c["host"], {
        "id": conn_id, "count": len(members),
        "members": [f"{m['type']}:{m['key']}" for m in members][:50]})
    return {"ok": True, "members": _describe_members(members)}


@router.get("/api/shared-subjects")
async def api_shared_subjects(request: Request):
    """成員挑選清單：使用者與群組（帳號、顯示名稱、來源）。"""
    try:
        _admin(request)
    except _OwnerError as e:
        return _err(e.code, e.status)
    users, groups = _subject_names()
    return {"ok": True,
            "users": [{"key": k, "username": u.get("username"), "display_name": u.get("display_name"),
                       "source": u.get("source")} for k, u in users.items()],
            # 本站記到的成員數（登入過的人）：共用事務機、權限都按這個算。Domain Users 這種很多人的群組顯示 0，
            # 就是登入時沒有掛上（AD 主要群組查不到之類），管理員不用請使用者測就看得出來
            "groups": [{"key": k, "name": g.get("name"), "source": g.get("source"),
                        "members": int(g.get("member_count") or 0)}
                       for k, g in groups.items()]}
