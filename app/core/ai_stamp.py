"""AI 用印（v1.17.38）：AI 客戶端經 MCP 蓋資產庫裡事先核准的章。

設計 `docs-share/mcp/jtdt-ai-stamp-design-v1.md`（內部文件）。使用者 2026-11-12 決定：
受管制（僅限核准案件使用）的章 AI 一律不能用；需要確認時由 Token 綁定的本人確認；
騎縫章只收資產庫核准的章；印章與簽名預設都要確認。

**要同時符合，缺一個就拒絕**（`require` 管前四項、`check` 管每一顆章）：

1. 站台啟用登入（要分得出是誰）
2. 設定 → MCP 的「允許 AI 用印」
3. 經 API Token 呼叫，而且那張 Token 勾了「允許用印」（綁定使用者才能勾）
4. 使用者有那支工具的權限
5. 那顆章開了 AI 授權、使用者在可以用的對象裡、今天還沒用完上限
6. 那顆章是「直接使用」（不是受管制）；設定讀不出來時當成受管制

需要確認的章：蓋好的檔案先放在待確認區（`<資料目錄>/ai_stamp/<編號>/`），不進工作區；本人在網頁上
核准才存進工作區，退回或 24 小時沒處理就作廢並刪檔。核准只能在網頁上做（MCP 沒有核准的工具）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from . import db

log = logging.getLogger(__name__)

REQ_ID_RE = re.compile(r"^[0-9a-f]{32}$")
CONFIRM_HOURS = 24
#: 每支工具能用的章的種類
TOOL_TYPES: dict[str, tuple[str, ...]] = {"pdf-stamp": ("stamp", "signature"), "pdf-seam-stamp": ("stamp",)}
#: void＝核准時條件已經不成立（章被收回 AI 授權、權限被拿掉、功能被關掉…），整件作廢、檔案刪除
STATUSES = ("pending", "approved", "rejected", "expired", "done", "void")
#: 每個人同時待確認的上限（件數與待確認區的總大小）：AI 一直送不確認的，不可以把磁碟塞滿
MAX_PENDING_PER_USER = 20
MAX_PENDING_BYTES_PER_USER = 500 * 1024 * 1024

MESSAGES = {
    "NEEDS_LOGIN": "AI 用印要先啟用帳號登入",
    "STAMP_OFF": "AI 用印沒有開啟（設定 → MCP 的「允許 AI 用印」）",
    "TOKEN_REQUIRED": "AI 用印要經 API Token 呼叫",
    "TOKEN_NOT_ALLOWED": "這張 Token 沒有允許用印（設定 → MCP）",
    "NO_USER": "這張 Token 沒有綁定使用者",
    "NO_PERMISSION": "沒有這支工具的使用權限",
    "ASSET_NOT_ALLOWED": "這顆章不能經 AI 使用（要由管理員在「設定 → 用印申請」開放 AI 使用）",
    "LIMIT_REACHED": "這顆章今天經 AI 使用的次數已達上限",
    "NOT_FOUND": "找不到這件 AI 用印",
    "NOT_PENDING": "這件 AI 用印已經處理過或已過期",
    "NO_ASSET": "AI 用印每一處都要指定資產庫裡的章（asset_id），不收上傳的圖片",
    "MCP_OFF": "MCP 沒有開啟（設定 → MCP）",
    "TOKEN_NOT_MCP": "這張 Token 沒有允許 MCP（設定 → MCP）",
    "NOT_VIA_MCP": "AI 用印只能經由 MCP 呼叫",
    "TOO_MANY_PENDING": "待你確認的 AI 用印太多了，請先到「AI 用印確認」頁處理",
    "NO_LONGER_ALLOWED": "送出之後條件已經改變（章的 AI 授權、工具權限或 AI 用印開關），這件已作廢",
}


class AiStampError(Exception):
    def __init__(self, code: str, status: int = 403, detail: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail or {}

    @property
    def message(self) -> str:
        return MESSAGES.get(self.code, self.code)


def db_path() -> Path:
    from ..config import settings
    return settings.data_dir / "ai_stamp.sqlite"


def files_root() -> Path:
    from ..config import settings
    return settings.data_dir / "ai_stamp"


def _m1_initial(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ai_stamp_requests (
            id           TEXT PRIMARY KEY,
            created_at   REAL NOT NULL,
            user_id      INTEGER NOT NULL,
            user_label   TEXT NOT NULL DEFAULT '',
            token_label  TEXT NOT NULL DEFAULT '',
            ip           TEXT NOT NULL DEFAULT '',
            tool         TEXT NOT NULL,
            filename     TEXT NOT NULL DEFAULT '',
            ws_key       TEXT NOT NULL DEFAULT '',
            status       TEXT NOT NULL,
            need_confirm INTEGER NOT NULL DEFAULT 1,
            expires_at   REAL NOT NULL DEFAULT 0,
            decided_at   REAL NOT NULL DEFAULT 0,
            decided_by   TEXT NOT NULL DEFAULT '',
            src_sha256   TEXT NOT NULL DEFAULT '',
            out_sha256   TEXT NOT NULL DEFAULT '',
            pages        TEXT NOT NULL DEFAULT '[]',
            assets       TEXT NOT NULL DEFAULT '[]',
            placements   INTEGER NOT NULL DEFAULT 0,
            file_id      TEXT NOT NULL DEFAULT '',
            result_name  TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ai_stamp_req_user ON ai_stamp_requests(user_id, status);
        CREATE INDEX IF NOT EXISTS ai_stamp_req_created ON ai_stamp_requests(created_at);
        CREATE TABLE IF NOT EXISTS ai_stamp_usage (
            user_id  INTEGER NOT NULL,
            asset_id TEXT NOT NULL,
            day      TEXT NOT NULL,
            count    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, asset_id, day)
        );
    """)


_MIGRATIONS = [_m1_initial]
_INITED: set[str] = set()
_INIT_LOCK = threading.Lock()


def conn() -> sqlite3.Connection:
    p = db_path()
    key = str(p)
    if key not in _INITED:
        with _INIT_LOCK:
            if key not in _INITED:
                p.parent.mkdir(parents=True, exist_ok=True)
                db.migrate(p, _MIGRATIONS)
                _INITED.add(key)
    return db.get_conn(p)


# --------------------------------------------------------------------------- 條件

def require(request, tool_id: str) -> dict[str, Any]:
    """前四項條件；回 `{"id", "label", "token"}`。"""
    from . import auth_settings, permissions, sessions
    from ..mcp import settings as mcp_settings
    if not auth_settings.is_enabled():
        raise AiStampError("NEEDS_LOGIN")
    cfg = mcp_settings.get()
    if not cfg.get("enabled"):
        raise AiStampError("MCP_OFF")
    if not cfg.get("allow_stamp"):
        raise AiStampError("STAMP_OFF")
    st = getattr(request, "state", None)
    token = getattr(st, "api_token_label", None)
    if token is None:
        raise AiStampError("TOKEN_REQUIRED")
    if not getattr(st, "api_token_mcp", False):
        raise AiStampError("TOKEN_NOT_MCP")
    if not getattr(st, "api_token_stamp", False):
        raise AiStampError("TOKEN_NOT_ALLOWED")
    if not getattr(st, "api_via_mcp", False):        # 只認 MCP 在行程內轉呼叫（外部偽造不了那個密值）
        raise AiStampError("NOT_VIA_MCP")
    user = getattr(st, "user", None)
    if not user or not user.get("user_id"):
        raise AiStampError("NO_USER")
    uid = int(user["user_id"])
    if not permissions.user_can_use_tool(uid, tool_id):
        raise AiStampError("NO_PERMISSION")
    return {"id": uid, "label": sessions.user_label(user) or str(uid), "token": str(token)[:64]}


def _subjects(uid: int) -> set[tuple[str, str]]:
    from . import permissions
    return {(str(a), str(b)) for a, b in permissions.subjects_for_user(int(uid))}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def used_today(uid: int, asset_id: str) -> int:
    row = conn().execute("SELECT count FROM ai_stamp_usage WHERE user_id=? AND asset_id=? AND day=?",
                         (int(uid), str(asset_id), _today())).fetchone()
    return int(row["count"]) if row else 0


def _asset_policy(uid: int, subjects: set, asset, tool_id: str) -> Optional[dict]:
    """這顆章這個人經 AI 能不能用（第 5、6 項，不含今日上限）；可以就回 AI 授權設定。"""
    from . import asset_access, seal_policy
    if asset is None or asset.type not in TOOL_TYPES.get(tool_id, ()):
        return None
    if asset_access.is_controlled_asset(asset):      # 受管制的章 AI 一律不能用（跟用印工具同一個判斷）
        return None
    pol = seal_policy.get_policy(asset.id)
    if pol.get("unreadable") or pol.get("mode") != "direct":
        return None
    ai = pol.get("ai") or {}
    if not ai.get("enabled"):
        return None
    want = {(s["type"], str(s["key"])) for s in ai.get("subjects") or []}
    if not want or not (subjects & want):
        return None
    return ai


def eligible(uid: int, tool_id: str) -> list[dict[str, Any]]:
    """這個人經 AI 可以用的章（清單用；不含圖檔）。"""
    from .asset_manager import asset_manager
    subjects = _subjects(uid)
    out = []
    for t in TOOL_TYPES.get(tool_id, ()):
        for a in asset_manager.list(type=t):
            ai = _asset_policy(uid, subjects, a, tool_id)
            if ai is None:
                continue
            limit = int(ai.get("daily_limit") or 0)
            left = None if not limit else max(0, limit - used_today(uid, a.id))
            p = a.preset
            out.append({"id": a.id, "name": a.name, "type": a.type, "needs_confirmation": bool(ai.get("confirm")),
                        "remaining_today": left,
                        "default_position_mm": {"x_mm": p.x_mm, "y_mm": p.y_mm, "width_mm": p.width_mm,
                                                "height_mm": p.height_mm, "rotation_deg": p.rotation_deg}})
    return out


def _policies(uid: int, tool_id: str, asset_ids: Iterable[str]) -> dict[str, dict]:
    from .asset_manager import asset_manager
    subjects = _subjects(uid)
    out: dict[str, dict] = {}
    for aid in asset_ids:
        a = asset_manager.get(aid) if isinstance(aid, str) and REQ_ID_RE.fullmatch(aid) else None
        ai = _asset_policy(uid, subjects, a, tool_id)
        if ai is None:
            raise AiStampError("ASSET_NOT_ALLOWED", 403, {"asset_id": str(aid)[:64]})
        out[aid] = {"asset": a, "ai": ai}
    return out


def check(uid: int, tool_id: str, uses: dict[str, int]) -> dict[str, dict]:
    """這次要用的每一顆章都要可以用（第 5、6 項＋今日上限），**同時把用量預扣下來**；
    `uses` 是 {asset_id: 這次蓋幾處}，每日上限算的是「處」（一次送 500 處不能只算 1 次）。
    有任何一顆不行就整件拒絕（不會只蓋一半）。之後產出失敗要呼叫 `release()` 退回。
    回 {asset_id: {"asset", "ai"}}。"""
    if not uses:
        raise AiStampError("NO_ASSET", 400)
    out = _policies(uid, tool_id, uses)
    if any(p["ai"].get("confirm") for p in out.values()) and pending_count(uid) >= MAX_PENDING_PER_USER:
        raise AiStampError("TOO_MANY_PENDING", 429, {"limit": MAX_PENDING_PER_USER})
    # 檢查與預扣在同一個寫入交易裡（BEGIN IMMEDIATE）：同時送進來的兩件不會都看到「還剩一次」
    c = conn()
    day = _today()
    with db.tx(c):
        for aid, n in uses.items():
            limit = int(out[aid]["ai"].get("daily_limit") or 0)
            row = c.execute("SELECT count FROM ai_stamp_usage WHERE user_id=? AND asset_id=? AND day=?",
                            (int(uid), aid, day)).fetchone()
            used = int(row["count"]) if row else 0
            if limit and used + int(n) > limit:
                raise AiStampError("LIMIT_REACHED", 429,
                                   {"asset_id": aid, "limit": limit, "remaining": max(0, limit - used)})
            c.execute("INSERT INTO ai_stamp_usage(user_id, asset_id, day, count) VALUES (?,?,?,?) "
                      "ON CONFLICT(user_id, asset_id, day) DO UPDATE SET count=count+excluded.count",
                      (int(uid), aid, day, int(n)))
    return out


def release(uid: int, uses: dict[str, int]) -> None:
    """產出失敗：把 `check()` 預扣的用量退回。"""
    try:
        c = conn()
        with db.tx(c):
            for aid, n in uses.items():
                c.execute("UPDATE ai_stamp_usage SET count=MAX(0, count-?) WHERE user_id=? AND asset_id=? AND day=?",
                          (int(n), int(uid), aid, _today()))
    except Exception:  # noqa: BLE001 —— 退不回只是今天少幾次可用，不可以蓋掉原本的錯誤
        log.exception("AI 用印退回用量失敗")


# --------------------------------------------------------------------------- 產出之後

def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mark_pdf(path: Path, req_id: str) -> None:
    """在 PDF 文件資訊裡加一筆不顯示的註記（作業編號、時間），之後拿檔案回查得到是哪一次。"""
    import fitz
    tmp = path.with_suffix(".mark.pdf")
    with fitz.open(str(path)) as doc:
        info = doc.xref_get_key(-1, "Info")
        stamp = f"{req_id} {time.strftime('%Y-%m-%dT%H:%M:%S%z')}"
        if info[0] == "xref":
            xref = int(info[1].split()[0])
            doc.xref_set_key(xref, "JTDTAIStamp", fitz.get_pdf_str(stamp))
        else:
            doc.set_metadata(doc.metadata or {})
            info = doc.xref_get_key(-1, "Info")
            if info[0] == "xref":
                doc.xref_set_key(int(info[1].split()[0]), "JTDTAIStamp", fitz.get_pdf_str(stamp))
        doc.save(str(tmp), garbage=3, deflate=True)
    tmp.replace(path)


def read_mark(path: Path) -> str:
    import fitz
    with fitz.open(str(path)) as doc:
        info = doc.xref_get_key(-1, "Info")
        if info[0] != "xref":
            return ""
        got = doc.xref_get_key(int(info[1].split()[0]), "JTDTAIStamp")
        return got[1].strip("()") if got[0] == "string" else ""


def _pending_bytes(uid: int) -> int:
    total = 0
    for r in conn().execute("SELECT id FROM ai_stamp_requests WHERE user_id=? AND status='pending'",
                            (int(uid),)).fetchall():
        d = files_root() / r["id"]
        if d.is_dir():
            total += sum(f.stat().st_size for f in d.iterdir() if f.is_file())
    return total


def _audit(event: str, user_label: str, ip: str, row: dict, **extra) -> None:
    try:
        from . import audit_db
        details = {"request_id": row["id"], "tool": row["tool"], "token": row.get("token_label", ""),
                   "file": row.get("filename", ""), "assets": json.loads(row.get("assets") or "[]"),
                   "pages": json.loads(row.get("pages") or "[]"), "placements": row.get("placements", 0),
                   "src_sha256": row.get("src_sha256", ""), "out_sha256": row.get("out_sha256", ""),
                   "needs_confirmation": bool(row.get("need_confirm")), "status": row.get("status"), **extra}
        audit_db.log_event(event, username=user_label, ip=ip, target=row["tool"], details=details)
    except Exception:  # noqa: BLE001 —— 稽核寫不進去要留紀錄，但不可以讓用印結果丟掉
        log.exception("AI 用印稽核寫入失敗")


def finish(*, user: dict, ip: str, tool_id: str, filename: str, src: Path, out: Path,
           policies: dict[str, dict], uses: dict[str, int], pages: list[int]) -> dict[str, Any]:
    """蓋好之後：加註記、算雜湊、記用量與稽核。需要確認 → 放待確認區、通知本人，回 `pending`；
    不需要 → 回 `done` 與檔案路徑（由呼叫端照一般工具回傳、存工作區）。"""
    from . import workspace
    req_id = uuid.uuid4().hex
    mark_pdf(out, req_id)
    need = any(p["ai"].get("confirm") for p in policies.values())
    now = time.time()
    assets = [{"id": aid, "name": p["asset"].name, "type": p["asset"].type, "count": uses.get(aid, 0)}
              for aid, p in policies.items()]
    row = {"id": req_id, "created_at": now, "user_id": user["id"], "user_label": user["label"],
           "token_label": user.get("token", ""), "ip": ip or "", "tool": tool_id, "filename": filename[:200],
           "ws_key": workspace.key_for_user_id(user["id"]), "status": "pending" if need else "done",
           "need_confirm": 1 if need else 0, "expires_at": now + CONFIRM_HOURS * 3600 if need else 0,
           "src_sha256": _sha(src), "out_sha256": _sha(out), "pages": json.dumps(sorted(set(pages))),
           "assets": json.dumps(assets, ensure_ascii=False), "placements": sum(uses.values())}
    if need:
        if _pending_bytes(user["id"]) + src.stat().st_size + out.stat().st_size > MAX_PENDING_BYTES_PER_USER:
            raise AiStampError("TOO_MANY_PENDING", 429, {"limit_bytes": MAX_PENDING_BYTES_PER_USER})
        d = files_root() / req_id
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d / "in.pdf")
        shutil.copy2(out, d / "out.pdf")
    c = conn()
    with db.tx(c):
        c.execute(f"INSERT INTO ai_stamp_requests({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                  tuple(row.values()))
    _audit("ai_stamp", user["label"], ip, row)
    if need:
        _notify(user["id"], row)
    return {"status": row["status"], "request_id": req_id,
            "expires_at": row["expires_at"] or None}


def _link(req_id: str) -> str:
    try:
        from . import notify_settings
        base = (notify_settings.get().get("site_url") or "").strip()
    except Exception:  # noqa: BLE001
        base = ""
    return (base.rstrip("/") + f"/ai-stamp#req={req_id}") if base else ""


def _notify(uid: int, row: dict) -> None:
    # 檔名由 AI 決定：不放進通知（主旨、內文都不放），免得被拿來在信裡寫誘導的話或換行插入內容；
    # 檔名與預覽只在登入後的確認頁看。章名與 Token 名稱是管理員設的。
    names = "、".join(a["name"] for a in json.loads(row["assets"]))
    subject = "AI 用印待你確認"
    link = _link(row["id"])
    text = "\n".join([subject, f"章：{names}", f"Token：{row['token_label']}",
                      f"送出時間：{time.strftime('%Y/%m/%d %H:%M', time.localtime(row['created_at']))}",
                      f"{CONFIRM_HOURS} 小時內沒有確認會自動作廢。",
                      f"確認：{link}" if link else "請登入本站，開啟「AI 用印確認」頁。"])
    from .seal_notify import _send
    threading.Thread(target=_send, args=([int(uid)], subject, text), daemon=True, name="ai-stamp-notify").start()


# --------------------------------------------------------------------------- 查詢與確認

def _row(req_id: str) -> Optional[dict]:
    if not REQ_ID_RE.fullmatch(str(req_id or "")):
        return None
    r = conn().execute("SELECT * FROM ai_stamp_requests WHERE id=?", (req_id,)).fetchone()
    return dict(r) if r else None


def get_for_user(req_id: str, uid: int) -> dict:
    """只有本人看得到（別人的編號跟不存在一樣回 NOT_FOUND）。"""
    expire_due()
    r = _row(req_id)
    if not r or int(r["user_id"]) != int(uid):
        raise AiStampError("NOT_FOUND", 404)
    return r


def public(r: dict) -> dict[str, Any]:
    out = {"request_id": r["id"], "status": r["status"], "tool": r["tool"], "filename": r["filename"],
           "created_at": r["created_at"], "expires_at": r["expires_at"] or None,
           "decided_at": r["decided_at"] or None, "decided_by": r["decided_by"] or None,
           "token": r["token_label"], "ip": r["ip"], "assets": json.loads(r["assets"] or "[]"),
           "pages": json.loads(r["pages"] or "[]"), "placements": r["placements"],
           "needs_confirmation": bool(r["need_confirm"])}
    if r["file_id"]:
        out["file"] = {"file_id": r["file_id"], "name": r["result_name"]}
    return out


def list_pending(uid: int) -> list[dict]:
    expire_due()
    rows = conn().execute("SELECT * FROM ai_stamp_requests WHERE user_id=? AND status='pending' "
                          "ORDER BY created_at DESC", (int(uid),)).fetchall()
    return [public(dict(r)) for r in rows]


def pending_count(uid: int) -> int:
    row = conn().execute("SELECT COUNT(*) AS n FROM ai_stamp_requests WHERE user_id=? AND status='pending' "
                         "AND expires_at>?", (int(uid), time.time())).fetchone()
    return int(row["n"]) if row else 0


def out_path(r: dict) -> Path:
    return files_root() / r["id"] / "out.pdf"


def _decide(req_id: str, uid: int, label: str, ip: str, status: str) -> dict:
    r = get_for_user(req_id, uid)
    if r["status"] != "pending":
        raise AiStampError("NOT_PENDING", 409)
    now = time.time()
    c = conn()
    with db.tx(c):
        cur = c.execute("UPDATE ai_stamp_requests SET status=?, decided_at=?, decided_by=? "
                        "WHERE id=? AND status='pending' AND expires_at>?", (status, now, label, req_id, now))
        if cur.rowcount != 1:
            raise AiStampError("NOT_PENDING", 409)
    r.update(status=status, decided_at=now, decided_by=label)
    return r


def approve(req_id: str, uid: int, label: str, ip: str = "") -> dict:
    """本人核准：檔案存進 Token 擁有者的工作區、寫用印歷史；待確認區的檔案刪掉。"""
    from . import workspace
    r = get_for_user(req_id, uid)
    if r["status"] == "pending" and not _still_allowed(r):
        c = conn()
        with db.tx(c):
            cur = c.execute("UPDATE ai_stamp_requests SET status='void', decided_at=?, decided_by=? "
                            "WHERE id=? AND status='pending'", (time.time(), label, req_id))
        if cur.rowcount == 1:
            shutil.rmtree(files_root() / req_id, ignore_errors=True)
            r.update(status="void", decided_by=label)
            _audit("ai_stamp_decision", label, ip, r, reason="no_longer_allowed")
        raise AiStampError("NO_LONGER_ALLOWED", 409)
    r = _decide(req_id, uid, label, ip, "approved")
    d = files_root() / req_id
    name = f"{Path(r['filename'] or 'document.pdf').stem}_stamped.pdf"
    try:
        meta = workspace.save_bytes_for_key(r["ws_key"], (d / "out.pdf").read_bytes(), name, r["tool"],
                                            user_label=r["user_label"])
    except Exception:
        # 存不進工作區（容量滿等）：改回待確認，讓本人清出空間再按一次
        c = conn()
        with db.tx(c):
            c.execute("UPDATE ai_stamp_requests SET status='pending', decided_at=0, decided_by='' WHERE id=?",
                      (req_id,))
        raise
    c = conn()
    with db.tx(c):
        c.execute("UPDATE ai_stamp_requests SET file_id=?, result_name=? WHERE id=?",
                  (meta.get("file_id") or "", meta.get("name") or name, req_id))
    r.update(file_id=meta.get("file_id") or "", result_name=meta.get("name") or name)
    _history(r, d / "in.pdf", d / "out.pdf")
    shutil.rmtree(d, ignore_errors=True)
    _audit("ai_stamp_decision", label, ip, r)
    return public(r)


def _still_allowed(r: dict) -> bool:
    """核准那一刻再檢查一次送出時的條件：送出之後管理員收回章的 AI 授權、拿掉工具權限、
    關掉 AI 用印或 MCP、停用帳號，這件就不可以再生效。"""
    from . import auth_db, auth_settings, permissions
    from ..mcp import settings as mcp_settings
    try:
        cfg = mcp_settings.get()
        if not (auth_settings.is_enabled() and cfg.get("enabled") and cfg.get("allow_stamp")):
            return False
        u = auth_db.conn().execute("SELECT enabled FROM users WHERE id=?", (int(r["user_id"]),)).fetchone()
        if not u or not u["enabled"]:
            return False
        if not permissions.user_can_use_tool(int(r["user_id"]), r["tool"]):
            return False
        ids = [a.get("id") for a in json.loads(r["assets"] or "[]")]
        if not ids:
            return False
        _policies(int(r["user_id"]), r["tool"], ids)
        return True
    except AiStampError:
        return False
    except Exception:  # noqa: BLE001 —— 判斷不了就當不成立（fail closed）
        log.exception("AI 用印核准前檢查失敗")
        return False


def reject(req_id: str, uid: int, label: str, ip: str = "") -> dict:
    r = _decide(req_id, uid, label, ip, "rejected")
    shutil.rmtree(files_root() / req_id, ignore_errors=True)
    _audit("ai_stamp_decision", label, ip, r)
    return public(r)


def _history(r: dict, src: Path, out: Path) -> None:
    """跟工具一樣存進「用印簽名歷史」（原檔與成品）；騎縫章另有自己的紀錄，不寫這裡。"""
    if r["tool"] != "pdf-stamp":
        return
    try:
        from .history_manager import stamp_history
        stamp_history.save(original_path=src, filled_path=out, preview_path=None,
                           original_filename=r["filename"] or "document.pdf", username=r["user_label"],
                           extra={"source": "ai", "ai_request": r["id"], "assets": json.loads(r["assets"])})
    except Exception:  # noqa: BLE001
        log.exception("AI 用印寫用印歷史失敗")


def record_history(user: dict, filename: str, src: Path, out: Path, req_id: str, policies: dict) -> None:
    """不需要確認的那一種，蓋好當下寫用印歷史。"""
    _history({"tool": "pdf-stamp", "id": req_id, "filename": filename, "user_label": user["label"],
              "assets": json.dumps([{"id": k, "name": v["asset"].name} for k, v in policies.items()],
                                   ensure_ascii=False)}, src, out)


def expire_due(now: Optional[float] = None) -> int:
    """24 小時沒確認的作廢、刪檔（排程與每次查詢時都會跑）。"""
    now = now or time.time()
    try:
        rows = conn().execute("SELECT * FROM ai_stamp_requests WHERE status='pending' AND expires_at<=?",
                              (now,)).fetchall()
    except sqlite3.Error:
        return 0
    n = 0
    for r in rows:
        c = conn()
        with db.tx(c):
            cur = c.execute("UPDATE ai_stamp_requests SET status='expired', decided_at=? "
                            "WHERE id=? AND status='pending'", (now, r["id"]))
        if cur.rowcount == 1:
            n += 1
            shutil.rmtree(files_root() / r["id"], ignore_errors=True)
            row = dict(r)
            row["status"] = "expired"
            _audit("ai_stamp_decision", "system", "", row)
    return n


def list_all(*, user_id: Optional[int] = None, asset_id: str = "", since: float = 0, until: float = 0,
             limit: int = 500) -> list[dict]:
    """管理頁的「AI 用印紀錄」。"""
    expire_due()
    q, args = "SELECT * FROM ai_stamp_requests WHERE 1=1", []
    if user_id:
        q += " AND user_id=?"
        args.append(int(user_id))
    if since:
        q += " AND created_at>=?"
        args.append(float(since))
    if until:
        q += " AND created_at<?"
        args.append(float(until))
    if asset_id:
        # 篩選要在 LIMIT 之前（先取 500 筆再挑，會漏掉較舊的那幾件）；assets 是 JSON 陣列，用 json_each 比對編號
        q += " AND EXISTS (SELECT 1 FROM json_each(ai_stamp_requests.assets) WHERE json_extract(value, '$.id')=?)"
        args.append(str(asset_id))
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(5000, int(limit))))
    return [public(dict(r)) | {"user": r["user_label"]} for r in conn().execute(q, args).fetchall()]
