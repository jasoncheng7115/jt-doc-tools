"""用印申請的規則：誰能申請、誰能審、狀態怎麼走（第 2 版：申請與審核；用印在第 3 版）。

規格 `docs-share/seal-approval/jtdt-seal-approval-spec-v1.md`。重點：

* **候選人送出時凍結、核准時再驗一次資格**：凍結的是「關卡指定的人 / 群組 / OU」（全員核准還凍結
  當下的人員名單），核准當下用即時的群組成員判斷，被移出群組、停用的人不算票。
* **申請者本人一律不能核准自己的案件**，也不算進候選人數。
* **送出前檢查每一關的合格人數夠不夠**；送出後人不夠了，案件停在那一關、標「需要改派」，由管理員改派，
  不會被跳過。
* 每次送出（含補件後重送）都是新版本、從第一關重審；舊版本的核准不算數。
* 狀態轉換全部在同一個交易裡「先確認狀態、版本、關卡沒變，再寫」：兩個人同時按只有一個生效，
  同一個人不會重複計票。
"""
from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from . import seal_policy, seal_store as S

RULES = ("any", "all", "n")
MAX_STAGES = 10
MAX_N = 50
MAX_FLOWS = 100
MAX_DOC_BYTES = 100 * 1024 * 1024
MAX_PAGES = 2000
MAX_ATTACHMENTS = 10
MAX_ATTACH_BYTES = 50 * 1024 * 1024
FIELD_LIMITS = {"subject": 200, "purpose": 500, "recipient": 200, "doc_type": 60, "due_date": 10, "note": 2000}
REQUIRED_FIELDS = ("subject", "purpose")
#: 申請者標的「希望位置」（保管人最後確認）。`custodian` ＝ 交給保管人決定。
POSITIONS = ("br", "bl", "tr", "tl", "center", "custodian")
MARGIN_MM = 15.0
DOC_NAME = "document.pdf"

#: 終結狀態（不能再動）。
CLOSED = ("rejected", "withdrawn", "done", "void")
EDITABLE = ("draft", "returned")


class FlowError(Exception):
    def __init__(self, code: str, status: int = 400, detail: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail or {}


MESSAGES = {
    "NOT_ENABLED": "用印申請尚未啟用",
    "LOGIN_REQUIRED": "要先登入",
    "NOT_FOUND": "找不到這件申請",
    "FLOW_NOT_FOUND": "找不到這個流程，或你不能使用它",
    "BAD_FLOW": "流程設定不完整或格式不正確",
    "BAD_WATERMARK": "浮水印設定不正確（文字最多 100 字；要選資產庫裡的浮水印圖；透明度、角度、大小要在範圍內）",
    "TOO_MANY_FLOWS": "流程太多了",
    "BAD_STATE": "這件申請目前的狀態不能做這個動作（可能剛被別人處理過，請重新整理）",
    "NOT_APPLICANT": "只有申請者本人可以做這個動作",
    "NOT_ELIGIBLE": "你不是這一關的審核者，或已經審過了",
    "SELF_APPROVAL": "不能審核自己的申請",
    "COMMENT_REQUIRED": "請填寫原因",
    "FIELD_REQUIRED": "請填寫必填欄位",
    "FIELD_TOO_LONG": "欄位內容太長",
    "BAD_DATE": "日期格式不正確",
    "NO_DOCUMENT": "請先上傳要用印的文件",
    "DOC_TOO_LARGE": "文件太大",
    "NOT_PDF": "文件要是 PDF，或能轉成 PDF 的辦公文件",
    "ENCRYPTED": "這份 PDF 有開啟密碼，請先用「PDF 密碼解除」解開再上傳",
    "SIGNED": "這份 PDF 已有數位簽章，用印會讓簽章失效，不能送用印申請",
    "TOO_MANY_PAGES": "文件頁數太多",
    "TOO_MANY_DOCS": "一件申請最多 20 份用印文件",
    "NO_ASSETS": "請至少選一顆要用的章",
    "ASSET_NOT_ALLOWED": "這顆章你不能申請，或不適用這個流程",
    "BAD_PLACEMENT": "用印位置的設定不正確",
    "INSUFFICIENT_APPROVERS": "流程裡有一關合格的審核者不夠，無法送出，請洽管理員",
    "TOO_MANY_ATTACHMENTS": "參考附件太多",
    "ATTACHMENT_TOO_LARGE": "參考附件太大",
    "ATTACHMENT_NOT_FOUND": "找不到這個附件",
    "NOT_ADMIN": "只有管理員可以改派",
    "BAD_REASSIGN": "改派的設定不正確",
}


def message(code: str) -> str:
    return MESSAGES.get(code, code)


# --------------------------------------------------------------------------- 身分與資格

def require_ready() -> None:
    from . import auth_settings
    if not seal_policy.is_enabled() or not auth_settings.is_enabled():
        raise FlowError("NOT_ENABLED", 404)


def me(request) -> dict:
    """目前登入者：`{"id": int, "label": "帳號@來源", "admin": bool, "auditor": bool}`。"""
    from . import permissions, sessions
    u = getattr(getattr(request, "state", None), "user", None)
    if not u or not u.get("user_id"):
        raise FlowError("LOGIN_REQUIRED", 401)
    uid = int(u["user_id"])
    return {"id": uid, "label": sessions.user_label(u) or str(uid),
            "admin": permissions.is_admin(uid), "auditor": permissions.is_auditor(uid)}


def _subjects(uid: int) -> set[tuple[str, str]]:
    from . import permissions
    return {(str(a), str(b)) for a, b in permissions.subjects_for_user(int(uid))}


def _norm(subjects: list[dict]) -> set[tuple[str, str]]:
    return {(s["type"], str(s["key"])) for s in subjects or []}


def matches(uid: int, subjects: list[dict]) -> bool:
    want = _norm(subjects)
    if not want:
        return False
    if not _user_enabled(uid):
        return False
    return bool(_subjects(uid) & want)


def _admin_disabled_ids() -> set[int]:
    """管理員停用的帳號（目錄帳號登入時看這個擋，`list_users` 沒有帶出來）。"""
    from . import auth_db
    try:
        return {int(r[0]) for r in auth_db.conn().execute("SELECT id FROM users WHERE admin_disabled=1")}
    except Exception:  # noqa: BLE001 — 舊資料庫沒有這個欄位：沒有人被停用
        return set()


def _directory_pending(u: Optional[dict], admin_off: Optional[set] = None) -> bool:
    """目錄同步進來、還沒登入過本站的帳號（「未啟用」不是「停用」）：登入一次就會啟用。

    AD 端停用的、管理員停用的、上次完整掃描已經不在目錄裡的都不算。
    """
    if not u or u.get("enabled", True) or u.get("source") not in ("ldap", "ad") or u.get("last_login_at"):
        return False
    if u.get("dir_disabled") or u.get("directory_missing"):
        return False
    off = admin_off if admin_off is not None else _admin_disabled_ids()
    return int(u["id"]) not in off


def _can_take_part(u: Optional[dict], admin_off: Optional[set] = None) -> bool:
    """可以當申請者、審核者、保管人：啟用中，或還沒登入過的目錄帳號（使用者 2026-11-06：要算進去；
    AD 環境很多主管從沒登入過，不算的話送出時一律人數不足）。他們要登入一次才能真的審核 / 用印，
    登入時就會啟用。"""
    return bool(u) and (bool(u.get("enabled", True)) or _directory_pending(u, admin_off))


def _user_enabled(uid: int) -> bool:
    from . import user_manager
    u = user_manager.get_by_id(int(uid))
    return _can_take_part(u)


def eligible_users(subjects: list[dict], exclude: Optional[int] = None) -> list[int]:
    """這組人 / 群組 / OU 現在對應到哪些可以參與的使用者（排除 `exclude`；見 `_can_take_part`）。"""
    from . import user_manager
    want = _norm(subjects)
    if not want:
        return []
    off = _admin_disabled_ids()
    out = []
    for u in user_manager.list_users():
        uid = int(u["id"])
        if exclude is not None and uid == int(exclude):
            continue
        if not _can_take_part(u, off):
            continue
        if _subjects(uid) & want:
            out.append(uid)
    return out


# --------------------------------------------------------------------------- 流程範本

def _check_text(v: Any, limit: int, required: bool = False) -> str:
    s = (v if isinstance(v, str) else "").strip()
    if required and not s:
        raise FlowError("BAD_FLOW")
    if len(s) > limit:
        raise FlowError("BAD_FLOW")
    return s


#: 浮水印（流程可選）：文字或資產庫的浮水印圖，鋪滿每一頁；用印時先上浮水印再蓋章。
WM_SOURCES = ("text", "asset")
WM_TOKENS = ("{編號}", "{日期}", "{number}", "{date}")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _num(v: Any, lo: float, hi: float, default: float) -> float:
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
        raise FlowError("BAD_WATERMARK")
    return float(v)


def clean_watermark(raw: Any) -> dict:
    """流程的浮水印設定。沒開＝`{}`；開了就每一項都檢查（存進去的就是用印時照用的）。"""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise FlowError("BAD_WATERMARK")
    if raw.get("enabled") is not True:
        return {}
    src = raw.get("source")
    if src not in WM_SOURCES:
        raise FlowError("BAD_WATERMARK")
    out = {"enabled": True, "source": src,
           "opacity": _num(raw.get("opacity"), 0.05, 1.0, 0.2),
           "rotation_deg": _num(raw.get("rotation_deg"), -180, 180, 30),
           "size_mm": _num(raw.get("size_mm"), 10, 300, 60),
           "gap_mm": _num(raw.get("gap_mm"), 0, 200, 30)}
    if src == "text":
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 100:
            raise FlowError("BAD_WATERMARK")
        color = raw.get("color") or "#cc0000"
        if not isinstance(color, str) or not _HEX.match(color):
            raise FlowError("BAD_WATERMARK")
        out.update(text=text.strip(), color=color.lower(), text_size_pt=_num(raw.get("text_size_pt"), 8, 200, 36))
    else:
        from .asset_manager import asset_manager
        aid = raw.get("asset_id")
        a = asset_manager.get(aid) if isinstance(aid, str) else None
        if a is None or a.type != "watermark":
            raise FlowError("BAD_WATERMARK")
        out["asset_id"] = a.id
    return out


def watermark_text(wm: dict, case: dict, when: Optional[float] = None) -> str:
    """把 `{編號}` / `{number}`、`{日期}` / `{date}` 換成這件案件的值（日期 YYYY/MM/DD，伺服器時區）。"""
    t = wm.get("text") or ""
    num = case.get("number") or ""
    day = time.strftime("%Y/%m/%d", time.localtime(when or time.time()))
    for k, v in (("{編號}", num), ("{number}", num), ("{日期}", day), ("{date}", day)):
        t = t.replace(k, v)
    return t


def clean_flow(data: Any) -> dict:
    if not isinstance(data, dict):
        raise FlowError("BAD_FLOW")
    out = {"name": _check_text(data.get("name"), 100, True),
           "description": _check_text(data.get("description"), 500),
           "enabled": data.get("enabled", True) is True}
    try:
        out["applicants"] = seal_policy._check_subjects(data.get("applicants") or [])
    except seal_policy.PolicyError:
        raise FlowError("BAD_FLOW")
    from .asset_manager import asset_manager
    assets = []
    for aid in data.get("assets") or []:
        a = asset_manager.get(str(aid)) if isinstance(aid, str) else None
        if a is None or a.type not in seal_policy.CONTROLLABLE_TYPES:
            raise FlowError("BAD_FLOW")
        if a.id not in assets:
            assets.append(a.id)
    out["assets"] = assets
    stages_in = data.get("stages")
    if not isinstance(stages_in, list) or not 1 <= len(stages_in) <= MAX_STAGES:
        raise FlowError("BAD_FLOW")
    stages = []
    for st in stages_in:
        if not isinstance(st, dict):
            raise FlowError("BAD_FLOW")
        rule = st.get("rule")
        if rule not in RULES:
            raise FlowError("BAD_FLOW")
        n = st.get("n", 1)
        if rule == "n" and (not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_N):
            raise FlowError("BAD_FLOW")
        try:
            cands = seal_policy._check_subjects(st.get("candidates") or [])
        except seal_policy.PolicyError:
            raise FlowError("BAD_FLOW")
        if not cands:
            raise FlowError("BAD_FLOW")
        stages.append({"name": _check_text(st.get("name"), 60, True), "rule": rule,
                       "n": n if rule == "n" else 1, "candidates": cands})
    out["stages"] = stages
    out["watermark"] = clean_watermark(data.get("watermark"))
    return out


def _uid_label(uid: int) -> str:
    from . import user_manager
    u = user_manager.get_by_id(int(uid)) or {}
    name = u.get("username") or str(uid)
    return f"{name}@{u['source']}" if u.get("source") else name


#: 模擬結果的說明（中文原文；畫面用 tr() 翻）。頁面認不得新的代碼時也有字可以顯示。
SIM_MESSAGES = {
    "SIM_OK": "照這份設定可以送出，每一關都有足夠的審核者，章也有保管人可以用印。",
    "SIM_FEATURE_OFF": "用印申請還沒啟用：現在使用者看不到申請入口。",
    "SIM_AUTH_OFF": "帳號登入沒有啟用：用印申請要先啟用登入。",
    "SIM_FLOW_OFF": "這個流程目前停用，使用者看不到它。",
    "SIM_APPLICANT_DISABLED": "這個帳號已停用。",
    "SIM_APPLICANT_NOT_ACTIVATED": "這是從目錄同步進來、還沒登入過本站的帳號；要登入一次才看得到申請入口。",
    "SIM_NOT_APPLICANT": "這個人不在流程的申請對象裡，看不到這個流程。",
    "SIM_ASSET_DIRECT": "「{asset}」目前是直接使用，不用申請就能蓋；要管制請改成「僅限核准案件使用」。",
    "SIM_ASSET_NOT_IN_FLOW": "「{asset}」不在這個流程適用的章裡。",
    "SIM_NOT_REQUESTER": "這個人不是「{asset}」的可申請對象。",
    "SIM_NO_REQUESTERS": "「{asset}」還沒有設定可申請對象；沒有設定＝沒有人能申請，請在「印章與簽名」設定可申請對象。",
    "SIM_NO_CUSTODIAN": "「{asset}」沒有保管人可以用印（保管人不能是申請者本人）。",
    "SIM_CUSTODIAN_IS_SELF": "「{asset}」的保管人只有申請者本人；申請者不能用印自己的申請，請再加一位保管人。",
    "SIM_CUSTODIAN_PENDING": "「{asset}」的保管人裡有 {n} 位還沒登入過本站（目錄同步進來的帳號）；已經算進去，但要登入一次才能用印。",
    "SIM_NO_COMMON_CUSTODIAN": "選的幾顆章沒有同一位保管人；目前一件申請要由同一位保管人用印全部的章。",
    "SIM_NO_ASSETS": "沒有可以申請的章。",
    "SIM_STAGE_SHORT": "第 {stage} 關「{name}」合格的審核者不夠：需要 {need} 人，目前 {have} 人。送出時會被擋下。",
    "SIM_STAGE_ONLY_SELF": "第 {stage} 關「{name}」的審核者只有申請者本人；申請者不能核准自己的申請，請再加一位審核者。",
    "SIM_STAGE_PENDING": "第 {stage} 關「{name}」的審核者裡有 {n} 位還沒登入過本站（目錄同步進來的帳號）；已經算進去，但要登入一次才能審核。",
    "SIM_SELF_EXCLUDED": "第 {stage} 關「{name}」的審核者包含申請者本人，這一關不算他。",
    "SIM_WATERMARK_TEXT": "用印時會先附加文字浮水印「{text}」。",
    "SIM_WATERMARK_ASSET": "用印時會先附加資產庫的浮水印圖。",
}


def _pending_users(subjects: list[dict], exclude: Optional[int] = None) -> int:
    """這組人 / 群組 / OU 裡，還沒登入過本站的目錄帳號有幾位（排除 `exclude`）——
    已經算進人數，模擬時提醒「他們要登入一次才能審核 / 用印」。"""
    from . import user_manager
    want = _norm(subjects)
    if not want:
        return 0
    n = 0
    off = _admin_disabled_ids()
    for u in user_manager.list_users():
        if exclude is not None and int(u["id"]) == int(exclude):
            continue
        if _directory_pending(u, off) and _subjects(int(u["id"])) & want:
            n += 1
    return n


def simulate(flow_data: Any, applicant_id: Any, asset_ids: Any = None) -> dict:
    """模擬一件申請會怎麼走（管理員用，確認流程設得對不對）。**只讀**：不建案件、不寫紀錄、不發通知。

    跟送出時同一套判斷（`flows_for` 的申請對象、`requestable_assets` 的可申請對象、
    `_freeze_stages` 的人數、`custodian_users` 的保管人），差別是**不會停在第一個錯誤**：
    每一項都列出來，管理員一次看完要修哪裡。流程可以是還沒儲存的（編輯中那份）。

    回 `{"ok": bool, "items": [{"level": "ok"|"warn"|"error", "text": 代碼, "detail": {...}}],
    "stages": [...], "assets": [...]}`；`ok` 只看 error（warn 是「現在會怎樣」的提醒）。
    """
    from . import auth_settings, user_manager
    from .asset_manager import asset_manager
    flow = clean_flow(flow_data)
    if not isinstance(applicant_id, int) or isinstance(applicant_id, bool):
        raise FlowError("BAD_FLOW")
    applicant = user_manager.get_by_id(applicant_id)
    if applicant is None:
        raise FlowError("NOT_FOUND", 404)
    items: list[dict] = []

    def add(level: str, code: str, **detail) -> None:
        msg = SIM_MESSAGES.get(code, code)
        for k, v in detail.items():
            msg = msg.replace("{" + k + "}", str(v))
        items.append({"level": level, "code": code, "detail": detail, "message": msg})

    pending_applicant = _directory_pending(applicant)

    def is_in(subjects: list[dict]) -> bool:
        # 還沒登入過的目錄帳號：模擬時當作已經登入過（登入一次就會啟用）
        if pending_applicant:
            want = _norm(subjects)
            return bool(want) and bool(_subjects(applicant_id) & want)
        return matches(applicant_id, subjects)

    if not seal_policy.is_enabled():
        add("warn", "SIM_FEATURE_OFF")
    if not auth_settings.is_enabled():
        add("warn", "SIM_AUTH_OFF")
    if not flow["enabled"]:
        add("warn", "SIM_FLOW_OFF")
    if pending_applicant:
        add("warn", "SIM_APPLICANT_NOT_ACTIVATED")
    elif not applicant.get("enabled", True):
        add("error", "SIM_APPLICANT_DISABLED")
    if flow["applicants"] and not is_in(flow["applicants"]):
        add("error", "SIM_NOT_APPLICANT")

    # 章：沒指定就用這個流程適用的全部（流程沒限定＝所有印章與簽名）
    pool = flow["assets"] or [a.id for t in seal_policy.CONTROLLABLE_TYPES for a in asset_manager.list(type=t)]
    chosen = [a for a in (asset_ids if isinstance(asset_ids, list) else pool) if isinstance(a, str)]
    asset_rows = []
    common: Optional[set] = None
    for aid in chosen:
        a = asset_manager.get(aid)
        if a is None or a.type not in seal_policy.CONTROLLABLE_TYPES:
            continue
        pol = seal_policy.get_policy(a.id)
        row = {"id": a.id, "name": a.name, "type": a.type,
               "controlled": pol["mode"] == "approval",
               "in_flow": not flow["assets"] or a.id in flow["assets"],
               "requester": is_in(pol["requesters"]),
               "custodians": [_uid_label(u) for u in eligible_users(pol["custodians"], exclude=applicant_id)]}
        if not row["controlled"]:
            add("warn", "SIM_ASSET_DIRECT", asset=a.name)
        if not row["in_flow"]:
            add("error", "SIM_ASSET_NOT_IN_FLOW", asset=a.name)
        if not row["requester"]:
            # 沒設定可申請對象＝沒有人能申請（不是「不限」），講清楚是哪一種
            add("error", "SIM_NO_REQUESTERS" if not pol["requesters"] else "SIM_NOT_REQUESTER", asset=a.name)
        if not row["custodians"]:
            self_only = bool(pol["custodians"]) and is_in(pol["custodians"])
            add("error", "SIM_CUSTODIAN_IS_SELF" if self_only else "SIM_NO_CUSTODIAN", asset=a.name)
        pc = _pending_users(pol["custodians"], exclude=applicant_id)
        if pc:
            add("warn", "SIM_CUSTODIAN_PENDING", asset=a.name, n=pc)
        u = set(eligible_users(pol["custodians"], exclude=applicant_id))
        common = u if common is None else (common & u)
        asset_rows.append(row)
    if not asset_rows:
        add("error", "SIM_NO_ASSETS")
    elif len(asset_rows) > 1 and not common:
        # 第一版一件申請由同一位保管人用印全部的章
        add("error", "SIM_NO_COMMON_CUSTODIAN")

    stages = []
    for i, st in enumerate(flow["stages"]):
        elig = eligible_users(st["candidates"], exclude=applicant_id)
        need = {"any": 1, "n": int(st["n"]), "all": len(elig)}[st["rule"]]
        excluded_self = applicant_id in eligible_users(st["candidates"]) or (
            pending_applicant and is_in(st["candidates"]))
        pending = _pending_users(st["candidates"], exclude=applicant_id)
        ok = bool(elig) and len(elig) >= need
        stages.append({"index": i + 1, "name": st["name"], "rule": st["rule"], "need": max(need, 1),
                       "eligible": [_uid_label(u) for u in elig], "excluded_self": excluded_self, "ok": ok})
        if not ok and excluded_self and not elig:
            add("error", "SIM_STAGE_ONLY_SELF", stage=i + 1, name=st["name"])
        elif not ok:
            add("error", "SIM_STAGE_SHORT", stage=i + 1, name=st["name"], need=max(need, 1), have=len(elig))
        elif excluded_self:
            add("warn", "SIM_SELF_EXCLUDED", stage=i + 1, name=st["name"])
        if pending:
            add("warn", "SIM_STAGE_PENDING", stage=i + 1, name=st["name"], n=pending)
    wm = flow.get("watermark") or {}
    if wm.get("enabled"):
        add("info", "SIM_WATERMARK_TEXT" if wm["source"] == "text" else "SIM_WATERMARK_ASSET",
            text=wm.get("text") or "")
    ok = not any(it["level"] == "error" for it in items)
    if ok:
        add("ok", "SIM_OK")
    return {"ok": ok, "applicant": _uid_label(applicant_id), "items": items,
            "stages": stages, "assets": asset_rows,
            "common_custodians": sorted(_uid_label(u) for u in (common or set()))}


def save_flow(flow_id: Optional[int], data: Any) -> dict:
    clean = clean_flow(data)
    if flow_id is None and len(S.list_flows()) >= MAX_FLOWS:
        raise FlowError("TOO_MANY_FLOWS")
    try:
        return S.save_flow(flow_id, clean)
    except LookupError:
        raise FlowError("FLOW_NOT_FOUND", 404)


def flows_for(uid: int) -> list[dict]:
    """這個人可以用的流程（啟用中、申請對象沒設＝不限，或有對到）。"""
    out = []
    for f in S.list_flows():
        if not f["enabled"] or not f["stages"]:
            continue
        if f["applicants"] and not matches(uid, f["applicants"]):
            continue
        out.append(f)
    return out


def requestable_assets(uid: int, flow: dict) -> list:
    """這個人在這個流程裡可以申請的章：受管制、可申請對象有對到、流程有限定的話要在名單裡。"""
    from .asset_manager import asset_manager
    out = []
    for t in seal_policy.CONTROLLABLE_TYPES:
        for a in asset_manager.list(type=t):
            if not seal_policy.is_controlled(a.id, a.type):
                continue
            if flow["assets"] and a.id not in flow["assets"]:
                continue
            if matches(uid, seal_policy.get_policy(a.id)["requesters"]):
                out.append(a)
    return out


# --------------------------------------------------------------------------- 文件

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pending_dir(case: dict) -> Path:
    return S.version_dir(case["id"], int(case["version"]) + 1)


def _ensure_pending(case: dict) -> Path:
    """編輯中那一份；補件時第一次改，先從最新的正式版本複製一份。"""
    pd = pending_dir(case)
    if not pd.exists():
        prev = S.version_dir(case["id"], int(case["version"])) if case["version"] else None
        if prev is not None and prev.exists():
            # 只帶送審的東西（文件、附件、說明）；預覽快取與用印後的 PDF 不帶
            shutil.copytree(prev, pd, ignore=shutil.ignore_patterns("previews", "stamped.pdf", STAMPED_DIR))
        else:
            pd.mkdir(parents=True, exist_ok=True)
    return pd


def check_pdf(path: Path) -> int:
    """回頁數；有開啟密碼、有數位簽章、打不開、頁數太多一律擋。"""
    import fitz
    from . import pdf_signatures
    try:
        doc = fitz.open(str(path))
    except Exception:
        raise FlowError("NOT_PDF")
    try:
        if doc.needs_pass:
            raise FlowError("ENCRYPTED")
        if pdf_signatures.signed_field_count(doc) > 0:
            raise FlowError("SIGNED")
        n = doc.page_count
        if n < 1:
            raise FlowError("NOT_PDF")
        if n > MAX_PAGES:
            raise FlowError("TOO_MANY_PAGES")
        return n
    finally:
        doc.close()


def _read_meta(d: Path) -> dict:
    import json
    try:
        return json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_meta(d: Path, meta: dict) -> None:
    from . import atomic_json
    atomic_json.write_json(d / "meta.json", meta, mode=0o600)


#: 一件申請最多幾份用印文件（分開蓋、分開下載）
MAX_DOCS = 20
DOCS_DIR = "docs"
STAMPED_DIR = "stamped"
#: 舊資料（只有一份 `document.pdf`）那一份的編號
LEGACY_DOC_ID = "main"
_DOC_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _doc_entries(d: Path) -> list[dict]:
    """這個目錄（某一版或編輯中那份）的文件清單。舊資料只有 `document.pdf` → 當成一份（`main`）。"""
    meta = _read_meta(d)
    docs = meta.get("docs")
    if isinstance(docs, list) and "docs" in meta:
        return [dict(x) for x in docs if isinstance(x, dict) and _DOC_ID.match(str(x.get("id") or ""))]
    if (d / DOC_NAME).exists():
        return [{"id": LEGACY_DOC_ID, "name": meta.get("doc_name") or DOC_NAME, "pages": int(meta.get("pages") or 0),
                 "page_sizes": meta.get("page_sizes") or [], "file": DOC_NAME}]
    return []


def _doc_path(d: Path, entry: dict) -> Path:
    f = entry.get("file") or f"{DOCS_DIR}/{entry['id']}.pdf"
    if f not in (DOC_NAME, f"{DOCS_DIR}/{entry['id']}.pdf"):       # 只認這兩種，不讓 meta 指到別處
        f = f"{DOCS_DIR}/{entry['id']}.pdf"
    return d / f


def _docs_dir(case: dict, version: Optional[int] = None) -> Path:
    """某一版的目錄；沒指定＝編輯中那份（已經建立的話），否則最新正式版。"""
    if version is not None:
        return S.version_dir(case["id"], version)
    pd = pending_dir(case)
    if pd.exists() or not case["version"]:
        return pd
    return S.version_dir(case["id"], case["version"])


def documents(case: dict, version: Optional[int] = None) -> list[dict]:
    """用印文件清單：每份 `{id, name, pages, page_sizes, path, dir}`（檔案不在的不列）。"""
    d = _docs_dir(case, version)
    out = []
    for e in _doc_entries(d):
        path = _doc_path(d, e)
        if path.exists():
            out.append({"id": e["id"], "name": e.get("name") or DOC_NAME, "pages": int(e.get("pages") or 0),
                        "page_sizes": e.get("page_sizes") or [], "path": path, "dir": d})
    return out


def document(case: dict, doc_id: Optional[str], version: Optional[int] = None) -> dict:
    """一份文件；`doc_id` 沒給＝第一份。找不到丟 NOT_FOUND。"""
    docs = documents(case, version)
    if not docs:
        raise FlowError("NO_DOCUMENT", 404)
    if doc_id is None:
        return docs[0]
    for d in docs:
        if d["id"] == doc_id:
            return d
    raise FlowError("NOT_FOUND", 404)


def doc_info(case: dict, version: Optional[int] = None) -> Optional[dict]:
    """第一份文件（舊介面相容）。"""
    docs = documents(case, version)
    return docs[0] if docs else None


def version_docs(version: Optional[dict]) -> list[dict]:
    """某一版送出時凍結的文件清單 `[{id, name, sha256, pages}]`；舊版本用 doc_* 補成一份。"""
    if not version:
        return []
    docs = version.get("documents") or []
    if docs:
        return docs
    return [{"id": LEGACY_DOC_ID, "name": version.get("doc_name") or DOC_NAME,
             "sha256": version.get("doc_sha256") or "", "pages": int(version.get("doc_pages") or 0)}]


def _materialize_docs(meta: dict, pd: Path) -> list[dict]:
    """把舊格式（只有 document.pdf）轉成清單寫進 meta（檔案不搬，`file` 指回 document.pdf）。"""
    if "docs" not in meta:
        meta["docs"] = [{k: v for k, v in e.items()} for e in _doc_entries(pd)]
    return meta["docs"]


def _pdf_sizes(src_pdf: Path) -> list:
    import fitz
    with fitz.open(str(src_pdf)) as doc:
        return [[round(p.rect.width * 25.4 / 72, 2), round(p.rect.height * 25.4 / 72, 2)] for p in doc]


def _editable_by(user: dict, case: dict) -> None:
    _require_applicant(user, case)
    if case["state"] not in EDITABLE:
        raise FlowError("BAD_STATE", 409)


def add_document(user: dict, case: dict, src_pdf: Path, doc_name: str) -> dict:
    """加一份（已轉好的）PDF 到編輯中那份（排在最後）。只有申請者、只有草稿或待補件。"""
    import secrets
    _editable_by(user, case)
    if src_pdf.stat().st_size > MAX_DOC_BYTES:
        raise FlowError("DOC_TOO_LARGE")
    pages = check_pdf(src_pdf)
    sizes = _pdf_sizes(src_pdf)
    pd = _ensure_pending(case)
    meta = _read_meta(pd)
    docs = _materialize_docs(meta, pd)
    if len(docs) >= MAX_DOCS:
        raise FlowError("TOO_MANY_DOCS")
    taken = {d["id"] for d in docs}
    did = secrets.token_hex(4)
    while did in taken or did == LEGACY_DOC_ID:
        did = secrets.token_hex(4)
    (pd / DOCS_DIR).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_pdf, pd / DOCS_DIR / f"{did}.pdf")
    docs.append({"id": did, "name": _safe_name(doc_name or DOC_NAME, ".pdf"), "pages": pages, "page_sizes": sizes})
    _write_meta(pd, meta)
    return S.get_case(case["id"]) or case


def remove_document(user: dict, case: dict, doc_id: str) -> dict:
    """移除一份文件，連同標在那一份上的位置。"""
    _editable_by(user, case)
    pd = _ensure_pending(case)
    meta = _read_meta(pd)
    docs = _materialize_docs(meta, pd)
    hit = next((d for d in docs if d.get("id") == doc_id), None)
    if hit is None:
        raise FlowError("NOT_FOUND", 404)
    first = docs[0]["id"] if docs else None
    path = _doc_path(pd, hit)
    meta["docs"] = [d for d in docs if d is not hit]
    _write_meta(pd, meta)
    path.unlink(missing_ok=True)
    shutil.rmtree(pd / "previews", ignore_errors=True)
    keep = [p for p in case["placements"] if (p.get("doc") or first) != doc_id]
    if keep != case["placements"]:
        case = S.update_case(case["id"], case["rev"], placements=keep) or S.get_case(case["id"])
    return S.get_case(case["id"]) or case


def put_document(user: dict, case: dict, src_pdf: Path, doc_name: str) -> dict:
    """舊介面（`POST /document`）：把文件**整份換成這一份**；位置保留頁數範圍內的、改指向新文件。"""
    _editable_by(user, case)
    if src_pdf.stat().st_size > MAX_DOC_BYTES:
        raise FlowError("DOC_TOO_LARGE")
    pages = check_pdf(src_pdf)
    pd = _ensure_pending(case)
    meta = _read_meta(pd)
    for d in _materialize_docs(meta, pd):
        _doc_path(pd, d).unlink(missing_ok=True)
    meta["docs"] = []
    _write_meta(pd, meta)
    shutil.rmtree(pd / "previews", ignore_errors=True)
    case = add_document(user, S.get_case(case["id"]) or case, src_pdf, doc_name)
    new_id = documents(case)[-1]["id"]
    keep = [{**p, "doc": new_id} for p in case["placements"] if 1 <= int(p.get("page", 0)) <= pages]
    if keep != case["placements"]:
        case = S.update_case(case["id"], case["rev"], placements=keep) or S.get_case(case["id"])
    return case


_NAME_BAD = re.compile(r'[\x00-\x1f\\/:*?"<>|]+')


def _safe_name(name: str, default_ext: str = "") -> str:
    base = Path(str(name or "")).name
    base = _NAME_BAD.sub("_", base).strip(" .") or "file"
    if default_ext and not base.lower().endswith(default_ext):
        base = Path(base).stem + default_ext
    return base[:120]


# --------------------------------------------------------------------------- 參考附件

def attachments(case: dict, version: Optional[int] = None) -> list[dict]:
    d = (S.version_dir(case["id"], version) if version else pending_dir(case))
    if version is None and not d.exists() and case["version"]:
        d = S.version_dir(case["id"], case["version"])
    return list(_read_meta(d).get("attachments") or [])


def add_attachment(user: dict, case: dict, name: str, data_path: Path) -> list[dict]:
    _require_applicant(user, case)
    if case["state"] not in EDITABLE:
        raise FlowError("BAD_STATE", 409)
    size = data_path.stat().st_size
    if size > MAX_ATTACH_BYTES:
        raise FlowError("ATTACHMENT_TOO_LARGE")
    pd = _ensure_pending(case)
    meta = _read_meta(pd)
    items = list(meta.get("attachments") or [])
    if len(items) >= MAX_ATTACHMENTS:
        raise FlowError("TOO_MANY_ATTACHMENTS")
    aid = hashlib.sha256(f"{time.time()}{name}{size}".encode()).hexdigest()[:16]
    (pd / "attachments").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(data_path, pd / "attachments" / aid)
    items.append({"id": aid, "name": _safe_name(name), "size": size, "sha256": sha256_file(pd / "attachments" / aid)})
    meta["attachments"] = items
    _write_meta(pd, meta)
    return items


def remove_attachment(user: dict, case: dict, att_id: str) -> list[dict]:
    _require_applicant(user, case)
    if case["state"] not in EDITABLE:
        raise FlowError("BAD_STATE", 409)
    pd = _ensure_pending(case)
    meta = _read_meta(pd)
    items = list(meta.get("attachments") or [])
    left = [a for a in items if a.get("id") != att_id]
    if len(left) == len(items):
        raise FlowError("ATTACHMENT_NOT_FOUND", 404)
    (pd / "attachments" / att_id).unlink(missing_ok=True) if re.fullmatch(r"[0-9a-f]{16}", att_id or "") else None
    meta["attachments"] = left
    _write_meta(pd, meta)
    return left


def attachment_path(case: dict, att_id: str, version: Optional[int]) -> tuple[Path, str]:
    if not re.fullmatch(r"[0-9a-f]{16}", att_id or ""):
        raise FlowError("ATTACHMENT_NOT_FOUND", 404)
    d = S.version_dir(case["id"], version) if version else pending_dir(case)
    for a in _read_meta(d).get("attachments") or []:
        if a.get("id") == att_id and (d / "attachments" / att_id).exists():
            return d / "attachments" / att_id, a["name"]
    raise FlowError("ATTACHMENT_NOT_FOUND", 404)


# --------------------------------------------------------------------------- 草稿

def create_draft(user: dict, flow_id: Any) -> dict:
    require_ready()
    flow = S.get_flow(int(flow_id)) if isinstance(flow_id, int) and not isinstance(flow_id, bool) else None
    if flow is None or flow["id"] not in {f["id"] for f in flows_for(user["id"])}:
        raise FlowError("FLOW_NOT_FOUND", 404)
    return S.create_case(user["id"], user["label"], flow)


def _require_applicant(user: dict, case: dict) -> None:
    if int(case["applicant_id"]) != int(user["id"]):
        raise FlowError("NOT_APPLICANT", 403)


def update_draft(user: dict, case: dict, body: Any) -> dict:
    """改欄位、選章、希望位置、流程（只有草稿可以換流程）。"""
    _require_applicant(user, case)
    if case["state"] not in EDITABLE:
        raise FlowError("BAD_STATE", 409)
    if not isinstance(body, dict):
        raise FlowError("FIELD_REQUIRED")
    cols: dict[str, Any] = {}
    flow = S.get_flow(case["flow_id"]) if case["flow_id"] else None
    if "flow_id" in body and body["flow_id"] != case["flow_id"]:
        if case["state"] != "draft" or case["version"]:
            raise FlowError("BAD_STATE", 409)
        fid = body["flow_id"]
        flow = S.get_flow(int(fid)) if isinstance(fid, int) and not isinstance(fid, bool) else None
        if flow is None or flow["id"] not in {f["id"] for f in flows_for(user["id"])}:
            raise FlowError("FLOW_NOT_FOUND", 404)
        cols.update(flow_id=flow["id"], flow_name=flow["name"])
    for k, lim in FIELD_LIMITS.items():
        if k in body:
            v = body[k] if isinstance(body[k], str) else ""
            v = v.strip()
            if len(v) > lim:
                raise FlowError("FIELD_TOO_LONG", 400, {"field": k})
            if k == "due_date" and v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise FlowError("BAD_DATE")
            cols[k] = v
    if "assets" in body:
        if flow is None:
            raise FlowError("FLOW_NOT_FOUND", 404)
        allowed = {a.id for a in requestable_assets(user["id"], flow)}
        ids = body["assets"] if isinstance(body["assets"], list) else None
        if ids is None or any(not isinstance(i, str) or i not in allowed for i in ids):
            raise FlowError("ASSET_NOT_ALLOWED")
        cols["assets"] = list(dict.fromkeys(ids))
    if "placements" in body:
        assets = cols.get("assets", case["assets"])
        cols["placements"] = _clean_placements(case, body["placements"], assets)
    elif "assets" in body:
        cols["placements"] = [p for p in case["placements"] if p["asset_id"] in cols["assets"]]
    if not cols:
        return case
    out = S.update_case(case["id"], case["rev"], **cols)
    if out is None:
        raise FlowError("BAD_STATE", 409)
    return out


def _clean_placements(case: dict, raw: Any, assets: list[str]) -> list[dict]:
    """申請者標的希望位置。每處要指定哪一份文件（`doc`，沒給＝第一份）、第幾頁、哪個位置。"""
    from .asset_manager import asset_manager
    docs = {d["id"]: d for d in documents(case)}
    first = next(iter(docs), None)
    if not isinstance(raw, list) or len(raw) > 200:
        raise FlowError("BAD_PLACEMENT")
    out = []
    for p in raw:
        if not isinstance(p, dict) or p.get("asset_id") not in assets or p.get("position") not in POSITIONS:
            raise FlowError("BAD_PLACEMENT")
        did = p.get("doc") or first
        doc = docs.get(did) if isinstance(did, str) else None
        if doc is None:
            raise FlowError("BAD_PLACEMENT")
        pages, sizes = doc["pages"], doc["page_sizes"]
        page = p.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= pages:
            raise FlowError("BAD_PLACEMENT")
        a = asset_manager.get(p["asset_id"])
        w = float(a.preset.width_mm) if a else 40.0
        h = float(a.preset.height_mm) if a else 40.0
        pw, ph = (sizes[page - 1] if page - 1 < len(sizes) else [210.0, 297.0])
        pos = p["position"]
        x = {"br": pw - MARGIN_MM - w, "tr": pw - MARGIN_MM - w, "bl": MARGIN_MM, "tl": MARGIN_MM,
             "center": (pw - w) / 2, "custodian": (pw - w) / 2}[pos]
        y = {"br": ph - MARGIN_MM - h, "bl": ph - MARGIN_MM - h, "tr": MARGIN_MM, "tl": MARGIN_MM,
             "center": (ph - h) / 2, "custodian": (ph - h) / 2}[pos]
        out.append({"asset_id": p["asset_id"], "doc": did, "page": page, "position": pos,
                    "x_mm": round(max(0.0, x), 1), "y_mm": round(max(0.0, y), 1),
                    "width_mm": round(w, 1), "height_mm": round(h, 1)})
    return out


def delete_draft(user: dict, case: dict) -> None:
    _require_applicant(user, case)
    if case["state"] != "draft" or case["version"]:
        raise FlowError("BAD_STATE", 409)
    S.delete_case(case["id"])


# --------------------------------------------------------------------------- 送出與審核

def _freeze_stages(flow: dict, applicant_id: int) -> list[dict]:
    frozen = []
    for i, st in enumerate(flow["stages"]):
        elig = eligible_users(st["candidates"], exclude=applicant_id)
        need = {"any": 1, "n": int(st["n"]), "all": len(elig)}[st["rule"]]
        if not elig or len(elig) < need:
            raise FlowError("INSUFFICIENT_APPROVERS", 409,
                            {"stage": i + 1, "name": st["name"], "need": max(need, 1), "have": len(elig)})
        d = {"name": st["name"], "rule": st["rule"], "n": int(st["n"]), "candidates": st["candidates"]}
        if st["rule"] == "all":
            d["required_users"] = elig
        frozen.append(d)
    return frozen


def submit(user: dict, case: dict) -> dict:
    """送出（或補件後重送）：檢查 → 凍結 → 成為新版本、從第一關開始。"""
    from .asset_manager import asset_manager
    require_ready()
    _require_applicant(user, case)
    if case["state"] not in EDITABLE:
        raise FlowError("BAD_STATE", 409)
    for k in REQUIRED_FIELDS:
        if not (case.get(k) or "").strip():
            raise FlowError("FIELD_REQUIRED", 400, {"field": k})
    flow = S.get_flow(case["flow_id"]) if case["flow_id"] else None
    if flow is None or flow["id"] not in {f["id"] for f in flows_for(user["id"])}:
        raise FlowError("FLOW_NOT_FOUND", 404)
    # 退回後沒改任何東西就重送：編輯中那份還沒建立，先從上一版複製（文件、附件照舊）
    pd = _ensure_pending(case) if case["version"] else pending_dir(case)
    docs = documents(case)
    if not docs:
        raise FlowError("NO_DOCUMENT")
    if not case["assets"]:
        raise FlowError("NO_ASSETS")
    allowed = {a.id for a in requestable_assets(user["id"], flow)}
    if any(a not in allowed for a in case["assets"]):
        raise FlowError("ASSET_NOT_ALLOWED")
    for d in docs:
        check_pdf(d["path"])
    stages = _freeze_stages(flow, user["id"])
    assets = []
    for aid in case["assets"]:
        a = asset_manager.get(aid)
        assets.append({"asset_id": aid, "name": a.name, "type": a.type,
                       "sha256": sha256_file(asset_manager.file_path(a))})
    meta = _read_meta(pd)
    new_v = int(case["version"]) + 1
    vdocs = [{"id": d["id"], "name": d["name"], "sha256": sha256_file(d["path"]), "pages": d["pages"]} for d in docs]
    ver = {"version": new_v, "flow_version": flow["version"], "doc_name": vdocs[0]["name"],
           "doc_sha256": vdocs[0]["sha256"], "doc_pages": vdocs[0]["pages"], "documents": vdocs, "assets": assets,
           "placements": [{**pl, "doc": pl.get("doc") or vdocs[0]["id"]} for pl in case["placements"]],
           "stages": stages, "attachments": meta.get("attachments") or [],
           "fields": {k: case.get(k) or "" for k in S.CASE_FIELDS},
           "watermark": flow.get("watermark") or {}}
    c = S.conn()
    from . import db
    now = time.time()
    with db.tx(c):
        n = c.execute("UPDATE seal_cases SET state='reviewing', stage_idx=0, version=?, submitted_at=?, "
                      "flow_name=?, rev=rev+1, updated_at=? WHERE id=? AND rev=? AND state IN ('draft','returned')",
                      (new_v, now, flow["name"], now, case["id"], case["rev"])).rowcount
        if not n:
            raise FlowError("BAD_STATE", 409)
        S.add_version(c, case["id"], ver)
        S.add_event(c, case["id"], "resubmit" if case["version"] else "submit", actor_id=user["id"],
                    actor_label=user["label"], version=new_v)
    out = S.get_case(case["id"])
    _notify_stage(out)
    return out


def _notify_stage(case: dict) -> None:
    """通知目前這一關還沒審的人。"""
    from . import seal_notify
    st = stage_status(case)
    if st:
        seal_notify.notify("review", case, st["remaining"])


def current_stage(case: dict) -> Optional[dict]:
    if case["state"] != "reviewing" or not case["version"]:
        return None
    v = S.get_version(case["id"], case["version"])
    if not v or case["stage_idx"] >= len(v["stages"]):
        return None
    return v["stages"][case["stage_idx"]]


def _eligible_now(uid: int, case: dict, stage: dict) -> bool:
    if int(uid) == int(case["applicant_id"]):
        return False
    if stage["rule"] == "all" and int(uid) not in [int(x) for x in stage.get("required_users", [])]:
        return False
    return matches(uid, stage["candidates"])


def stage_status(case: dict) -> Optional[dict]:
    """目前這一關：需要幾票、已經幾票、還有誰能投、是不是卡住（需要改派）。"""
    st = current_stage(case)
    if st is None:
        return None
    vs = S.votes(case["id"], case["version"], case["stage_idx"])
    voted = {int(v["user_id"]) for v in vs if v["decision"] == "approve"}
    if st["rule"] == "all":
        req = [int(x) for x in st.get("required_users", [])]
        still = [u for u in req if u not in voted]
        stuck = any(not _eligible_now(u, case, st) for u in still)
        need = len(req)
        remaining = [u for u in still if _eligible_now(u, case, st)]
    else:
        need = 1 if st["rule"] == "any" else int(st["n"])
        remaining = [u for u in eligible_users(st["candidates"], exclude=case["applicant_id"]) if u not in voted]
        stuck = len(voted) + len(remaining) < need
    return {"index": case["stage_idx"], "name": st["name"], "rule": st["rule"], "need": need,
            "approved": len(voted), "remaining": remaining, "stuck": stuck,
            "voters": [{"user_id": v["user_id"], "label": v["user_label"], "ts": v["ts"]} for v in vs]}


def can_review(uid: int, case: dict) -> bool:
    st = current_stage(case)
    if st is None or not _eligible_now(uid, case, st):
        return False
    return int(uid) not in {int(v["user_id"]) for v in S.votes(case["id"], case["version"], case["stage_idx"])}


def approve(user: dict, case: dict, comment: str = "") -> dict:
    require_ready()
    if int(user["id"]) == int(case["applicant_id"]):
        raise FlowError("SELF_APPROVAL", 403)
    st = current_stage(case)
    if st is None:
        raise FlowError("BAD_STATE", 409)
    if not can_review(user["id"], case):
        raise FlowError("NOT_ELIGIBLE", 403)
    comment = (comment or "").strip()[:1000]
    c = S.conn()
    from . import db
    now = time.time()
    with db.tx(c):
        row = c.execute("SELECT state, stage_idx, version, rev FROM seal_cases WHERE id=?", (case["id"],)).fetchone()
        if (not row or row["state"] != "reviewing" or row["stage_idx"] != case["stage_idx"]
                or row["version"] != case["version"]):
            raise FlowError("BAD_STATE", 409)
        try:
            c.execute("INSERT INTO seal_votes(case_id, version, stage_idx, user_id, user_label, decision, comment, ts) "
                      "VALUES (?,?,?,?,?,?,?,?)", (case["id"], case["version"], case["stage_idx"], user["id"],
                                                   user["label"], "approve", comment, now))
        except Exception:
            raise FlowError("NOT_ELIGIBLE", 403)
        S.add_event(c, case["id"], "approve", actor_id=user["id"], actor_label=user["label"],
                    version=case["version"], stage_idx=case["stage_idx"], comment=comment)
        approved = {r[0] for r in c.execute(
            "SELECT user_id FROM seal_votes WHERE case_id=? AND version=? AND stage_idx=? AND decision='approve'",
            (case["id"], case["version"], case["stage_idx"]))}
        if st["rule"] == "all":
            passed = set(int(x) for x in st.get("required_users", [])) <= {int(x) for x in approved}
        else:
            passed = len(approved) >= (1 if st["rule"] == "any" else int(st["n"]))
        if passed:
            v = S.get_version(case["id"], case["version"])
            if case["stage_idx"] + 1 < len(v["stages"]):
                c.execute("UPDATE seal_cases SET stage_idx=stage_idx+1, rev=rev+1, updated_at=? WHERE id=?",
                          (now, case["id"]))
                S.add_event(c, case["id"], "stage_passed", actor_id=None, actor_label="", version=case["version"],
                            stage_idx=case["stage_idx"])
            else:
                c.execute("UPDATE seal_cases SET state='awaiting_stamp', rev=rev+1, updated_at=? WHERE id=?",
                          (now, case["id"]))
                S.add_event(c, case["id"], "approved", actor_id=None, actor_label="", version=case["version"],
                            stage_idx=case["stage_idx"])
        else:
            c.execute("UPDATE seal_cases SET rev=rev+1, updated_at=? WHERE id=?", (now, case["id"]))
    out = S.get_case(case["id"])
    if passed:
        from . import seal_notify
        if out["state"] == "reviewing":
            _notify_stage(out)
        elif out["state"] == "awaiting_stamp":
            seal_notify.notify("awaiting_stamp", out, custodian_users(out))
            seal_notify.notify("approved", out, [out["applicant_id"]])
    return out


def _close_by_reviewer(user: dict, case: dict, comment: str, new_state: str, action: str) -> dict:
    require_ready()
    if int(user["id"]) == int(case["applicant_id"]):
        raise FlowError("SELF_APPROVAL", 403)
    if not can_review(user["id"], case):
        raise FlowError("NOT_ELIGIBLE", 403)
    comment = (comment or "").strip()
    if not comment:
        raise FlowError("COMMENT_REQUIRED")
    comment = comment[:1000]
    c = S.conn()
    from . import db
    now = time.time()
    with db.tx(c):
        n = c.execute(f"UPDATE seal_cases SET state=?, rev=rev+1, updated_at=?{', closed_at=?' if new_state in CLOSED else ''} "
                      "WHERE id=? AND state='reviewing' AND stage_idx=? AND version=?",
                      ((new_state, now, now) if new_state in CLOSED else (new_state, now)) +
                      (case["id"], case["stage_idx"], case["version"])).rowcount
        if not n:
            raise FlowError("BAD_STATE", 409)
        S.add_event(c, case["id"], action, actor_id=user["id"], actor_label=user["label"], version=case["version"],
                    stage_idx=case["stage_idx"], comment=comment)
    out = S.get_case(case["id"])
    from . import seal_notify
    seal_notify.notify(new_state, out, [out["applicant_id"]], comment)
    return out


def return_case(user: dict, case: dict, comment: str) -> dict:
    """退回補件：交回申請者修改，重送後從第一關重審。"""
    return _close_by_reviewer(user, case, comment, "returned", "return")


def reject(user: dict, case: dict, comment: str) -> dict:
    return _close_by_reviewer(user, case, comment, "rejected", "reject")


def withdraw(user: dict, case: dict, comment: str = "") -> dict:
    """申請者撤回（第 3 版：開始用印之後就不能撤回）。"""
    _require_applicant(user, case)
    c = S.conn()
    from . import db
    now = time.time()
    with db.tx(c):
        n = c.execute("UPDATE seal_cases SET state='withdrawn', rev=rev+1, updated_at=?, closed_at=? WHERE id=? "
                      "AND state IN ('draft','reviewing','returned','awaiting_stamp')",
                      (now, now, case["id"])).rowcount
        if not n:
            raise FlowError("BAD_STATE", 409)
        S.add_event(c, case["id"], "withdraw", actor_id=user["id"], actor_label=user["label"],
                    version=case["version"], comment=(comment or "").strip()[:1000])
    return S.get_case(case["id"])


def add_comment(user: dict, case: dict, text: str) -> None:
    text = (text or "").strip()
    if not text:
        raise FlowError("COMMENT_REQUIRED")
    c = S.conn()
    from . import db
    with db.tx(c):
        S.add_event(c, case["id"], "comment", actor_id=user["id"], actor_label=user["label"],
                    version=case["version"], comment=text[:2000])


def reassign(user: dict, case: dict, candidates: Any, reason: str) -> dict:
    """管理員改派目前這一關的候選人（候選人都不在了、或人數不夠時用）。原因必填、寫進事件。"""
    require_ready()
    if not user["admin"]:
        raise FlowError("NOT_ADMIN", 403)
    reason = (reason or "").strip()
    if not reason:
        raise FlowError("COMMENT_REQUIRED")
    st = current_stage(case)
    if st is None:
        raise FlowError("BAD_STATE", 409)
    try:
        cands = seal_policy._check_subjects(candidates)
    except seal_policy.PolicyError:
        raise FlowError("BAD_REASSIGN")
    if not cands:
        raise FlowError("BAD_REASSIGN")
    v = S.get_version(case["id"], case["version"])
    stages = v["stages"]
    new = dict(stages[case["stage_idx"]])
    new["candidates"] = cands
    elig = eligible_users(cands, exclude=case["applicant_id"])
    if new["rule"] == "all":
        new["required_users"] = elig
    need = {"any": 1, "n": int(new["n"]), "all": len(elig)}[new["rule"]]
    if not elig or len(elig) < need:
        raise FlowError("INSUFFICIENT_APPROVERS", 409, {"stage": case["stage_idx"] + 1, "name": new["name"],
                                                        "need": max(need, 1), "have": len(elig)})
    stages[case["stage_idx"]] = new
    c = S.conn()
    from . import db
    import json
    now = time.time()
    with db.tx(c):
        n = c.execute("UPDATE seal_cases SET rev=rev+1, updated_at=? WHERE id=? AND rev=? AND state='reviewing'",
                      (now, case["id"], case["rev"])).rowcount
        if not n:
            raise FlowError("BAD_STATE", 409)
        c.execute("UPDATE seal_case_versions SET stages=? WHERE case_id=? AND version=?",
                  (json.dumps(stages, ensure_ascii=False), case["id"], case["version"]))
        S.add_event(c, case["id"], "reassign", actor_id=user["id"], actor_label=user["label"],
                    version=case["version"], stage_idx=case["stage_idx"], comment=reason[:1000],
                    detail={"candidates": cands})
    return S.get_case(case["id"])


# --------------------------------------------------------------------------- 可見範圍

def roles_on(user: dict, case: dict) -> set[str]:
    """這個人對這件案件是什麼身分：applicant / reviewer（任一版任一關的候選人或投過票）/ custodian / auditor / admin。"""
    roles: set[str] = set()
    uid = int(user["id"])
    if int(case["applicant_id"]) == uid:
        roles.add("applicant")
    if case["version"]:
        for ver in range(1, int(case["version"]) + 1):
            v = S.get_version(case["id"], ver)
            if not v:
                continue
            for st in v["stages"]:
                if uid != int(case["applicant_id"]) and matches(uid, st["candidates"]):
                    roles.add("reviewer")
                    break
        if uid in {int(v["user_id"]) for ver in range(1, int(case["version"]) + 1)
                   for v in S.votes(case["id"], ver)}:
            roles.add("reviewer")
    if case["state"] not in ("draft",) and any(
            matches(uid, seal_policy.get_policy(aid)["custodians"]) for aid in case["assets"]):
        roles.add("custodian")
    if user.get("auditor"):
        roles.add("auditor")
    if user.get("admin"):
        roles.add("admin")
    return roles


def can_view(user: dict, case: dict) -> bool:
    return bool(roles_on(user, case))


def can_view_documents(user: dict, case: dict) -> bool:
    """文件與附件：管理員身分本身不算（只看得到案件資料與改派）。草稿只有申請者看得到。"""
    r = roles_on(user, case)
    if case["state"] == "draft":
        return "applicant" in r
    return bool(r & {"applicant", "reviewer", "custodian", "auditor"})


# --------------------------------------------------------------------------- 用印（第 3 版）

#: 用印中超過這麼久沒有結果（例如服務在產檔途中重啟），當成失敗、讓保管人重試。
STAMP_STALE_SECONDS = 600
STAMPED_NAME = "stamped.pdf"
MIN_SEAL_MM, MAX_SEAL_MM = 3.0, 300.0
MAX_FINAL_PLACEMENTS = 200

MESSAGES.update({
    "NOT_CUSTODIAN": "你不是這件申請所有章的保管人",
    "SELF_STAMP": "申請者本人不能用印自己的申請",
    "DOC_CHANGED": "文件跟核准時的不一樣，不能用印，請退回申請者重送",
    "ASSET_CHANGED": "印章圖在核准之後被換過，不能用印，請退回申請者重送",
    "ASSET_MISSING": "核准的章已經不在資產庫裡，不能用印",
    "SEAL_NOT_USED": "每一顆核准的章至少要蓋一處",
    "DOC_NOT_STAMPED": "每一份用印文件至少要蓋一處",
    "WATERMARK_MISSING": "流程設定的浮水印圖已經從資產庫刪除，無法用印；請管理員修改流程後請申請者重新送出",
    "NOT_DONE": "這件申請還沒有用印完成",
    "VOIDED": "這件申請已作廢，不能取件",
    "WORKSPACE_OFF": "工作區沒有啟用",
    "WORKSPACE_FAILED": "存到工作區失敗（可能是工作區空間不足），可以直接從案件頁下載",
    "STAMP_FAILED": "用印失敗",
})


def custodian_users(case: dict) -> list[int]:
    """能用印這件案件的人：每一顆核准的章的保管人都要是他（第一版不做多位保管人分開蓋），排除申請者。"""
    sets = None
    for aid in case["assets"]:
        u = set(eligible_users(seal_policy.get_policy(aid)["custodians"], exclude=case["applicant_id"]))
        sets = u if sets is None else (sets & u)
    return sorted(sets or [])


def is_custodian(uid: int, case: dict) -> bool:
    if int(uid) == int(case["applicant_id"]) or not case["assets"]:
        return False
    return all(matches(uid, seal_policy.get_policy(aid)["custodians"]) for aid in case["assets"])


def recover_stale(case: dict) -> dict:
    """用印中卡太久（產檔途中服務重啟）→ 當成失敗，保留核准、可以重試。"""
    if case["state"] == "stamping" and (case.get("stamp_started_at") or 0) < time.time() - STAMP_STALE_SECONDS:
        out = S.update_case(case["id"], case["rev"], state="stamp_failed",
                            stamp_error="用印逾時（可能是服務中途重啟），請重試")
        return out or S.get_case(case["id"])
    return case


def stamp_queue(user: dict) -> list[dict]:
    rows = S.list_cases("state IN ('awaiting_stamp','stamp_failed','stamping')", limit=500)
    return [recover_stale(c) for c in rows if is_custodian(user["id"], c)]


def custodian_return(user: dict, case: dict, comment: str) -> dict:
    """保管人退回申請者補件（例如位置標錯、文件有問題）。"""
    require_ready()
    if not is_custodian(user["id"], case):
        raise FlowError("NOT_CUSTODIAN", 403)
    comment = (comment or "").strip()
    if not comment:
        raise FlowError("COMMENT_REQUIRED")
    c = S.conn()
    from . import db
    now = time.time()
    with db.tx(c):
        n = c.execute("UPDATE seal_cases SET state='returned', rev=rev+1, updated_at=? WHERE id=? AND "
                      "state IN ('awaiting_stamp','stamp_failed') AND version=?",
                      (now, case["id"], case["version"])).rowcount
        if not n:
            raise FlowError("BAD_STATE", 409)
        S.add_event(c, case["id"], "custodian_return", actor_id=user["id"], actor_label=user["label"],
                    version=case["version"], comment=comment[:1000])
    out = S.get_case(case["id"])
    from . import seal_notify
    seal_notify.notify("returned", out, [out["applicant_id"]], comment)
    return out


def _clean_final(case: dict, version: dict, raw: Any) -> list[dict]:
    """保管人確定的位置。每處指定文件（`doc`，沒給＝第一份）、頁、座標；每顆核准的章、每份文件都至少一處。"""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_FINAL_PLACEMENTS:
        raise FlowError("BAD_PLACEMENT")
    allowed = {a["asset_id"] for a in version["assets"]}
    vdocs = {d["id"]: d for d in version_docs(version)}
    sizes = {d["id"]: d["page_sizes"] for d in documents(case, int(case["version"]))}
    first = next(iter(vdocs), None)
    out = []
    for p in raw:
        if not isinstance(p, dict) or p.get("asset_id") not in allowed:
            raise FlowError("BAD_PLACEMENT")
        did = p.get("doc") or first
        if not isinstance(did, str) or did not in vdocs:
            raise FlowError("BAD_PLACEMENT")
        page = p.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= int(vdocs[did]["pages"]):
            raise FlowError("BAD_PLACEMENT")
        try:
            x, y, w, h = (float(p[k]) for k in ("x_mm", "y_mm", "width_mm", "height_mm"))
            rot = float(p.get("rotation_deg") or 0.0)
        except (KeyError, TypeError, ValueError):
            raise FlowError("BAD_PLACEMENT")
        ds = sizes.get(did) or []
        pw, ph = ds[page - 1] if page - 1 < len(ds) else (210.0, 297.0)
        if not (MIN_SEAL_MM <= w <= MAX_SEAL_MM and MIN_SEAL_MM <= h <= MAX_SEAL_MM and -180 <= rot <= 180):
            raise FlowError("BAD_PLACEMENT")
        # 章至少要有一半在紙上（騎縫那種跨出頁面一半的位置可以）
        if not (-w / 2 <= x <= pw - w / 2 and -h / 2 <= y <= ph - h / 2):
            raise FlowError("BAD_PLACEMENT")
        out.append({"asset_id": p["asset_id"], "doc": did, "page": page, "x_mm": round(x, 2), "y_mm": round(y, 2),
                    "width_mm": round(w, 2), "height_mm": round(h, 2), "rotation_deg": round(rot, 2)})
    if allowed - {p["asset_id"] for p in out}:
        raise FlowError("SEAL_NOT_USED")
    if set(vdocs) - {p["doc"] for p in out}:
        raise FlowError("DOC_NOT_STAMPED")
    return out


def _verify_hashes(case: dict, version: dict) -> None:
    from .asset_manager import asset_manager
    paths = {d["id"]: d["path"] for d in documents(case, int(case["version"]))}
    for vd in version_docs(version):
        path = paths.get(vd["id"])
        if path is None or not path.exists() or sha256_file(path) != vd["sha256"]:
            raise FlowError("DOC_CHANGED", 409)
    for a in version["assets"]:
        asset = asset_manager.get(a["asset_id"])
        if asset is None or not asset_manager.file_path(asset).exists():
            raise FlowError("ASSET_MISSING", 409)
        if sha256_file(asset_manager.file_path(asset)) != a["sha256"]:
            raise FlowError("ASSET_CHANGED", 409)


def start_stamp(user: dict, case: dict, placements: Any, exec_id: Any) -> tuple[dict, bool]:
    """保管人確認用印：檢查 → 轉「用印中」。回 (案件, 這次要不要真的產檔)。

    `exec_id` 是畫面這次按下的編號：重複送同一個編號（連按、網路重送）只會產一次檔。
    """
    require_ready()
    if int(user["id"]) == int(case["applicant_id"]):
        raise FlowError("SELF_STAMP", 403)
    if not is_custodian(user["id"], case):
        raise FlowError("NOT_CUSTODIAN", 403)
    if not isinstance(exec_id, str) or not re.fullmatch(r"[0-9a-f]{16,64}", exec_id):
        raise FlowError("BAD_PLACEMENT")
    if case["state"] in ("stamping", "done") and case.get("stamp_exec") == exec_id:
        return case, False                      # 同一次按下的重送
    if case["state"] not in ("awaiting_stamp", "stamp_failed"):
        raise FlowError("BAD_STATE", 409)
    version = S.get_version(case["id"], case["version"])
    final = _clean_final(case, version, placements)
    _verify_hashes(case, version)
    c = S.conn()
    from . import db
    now = time.time()
    with db.tx(c):
        n = c.execute("UPDATE seal_cases SET state='stamping', stamp_exec=?, stamp_started_at=?, stamp_error='', "
                      "final_placements=?, stamped_by=?, stamped_by_label=?, rev=rev+1, updated_at=? WHERE id=? AND "
                      "state IN ('awaiting_stamp','stamp_failed') AND version=?",
                      (exec_id, now, S._j(final), user["id"], user["label"], now, case["id"], case["version"])).rowcount
        if not n:
            raise FlowError("BAD_STATE", 409)
        S.add_event(c, case["id"], "stamp_start", actor_id=user["id"], actor_label=user["label"],
                    version=case["version"], detail={"placements": len(final)})
    return S.get_case(case["id"]), True


def _stamp_one(src: Path, workdir: Path, placements: list[dict], wm: dict, case: dict) -> Path:
    """一份文件：浮水印 → 一處一處蓋章 → 驗證。回產好的檔（在 workdir 裡）。"""
    import fitz
    from ..tools.pdf_stamp import service
    from .asset_manager import asset_manager
    workdir.mkdir(parents=True, exist_ok=True)
    cur = workdir / "work.pdf"
    shutil.copyfile(src, cur)
    # 浮水印先上（章要蓋在浮水印上面）；圖數基準取上完浮水印之後，下面的驗證才量得到章
    if wm.get("enabled"):
        from ..tools.pdf_watermark import service as wm_service
        png = None
        if wm.get("source") == "asset":
            wa = asset_manager.get(wm.get("asset_id") or "")
            if wa is None:
                raise FlowError("WATERMARK_MISSING")
            png = asset_manager.file_path(wa)
        wmd = workdir / "watermarked.pdf"
        wm_service.apply_watermark(cur, wmd, png, wm_service.WatermarkParams(
            mode="tile", opacity=wm["opacity"], rotation_deg=wm["rotation_deg"],
            tile_size_mm=wm["size_mm"], gap_mm=wm["gap_mm"],
            text=watermark_text(wm, case) if wm.get("source") == "text" else "",
            text_color=wm.get("color") or "#cc0000", text_size_pt=wm.get("text_size_pt") or 36))
        cur.unlink(missing_ok=True)
        cur = wmd
    with fitz.open(str(cur)) as doc:
        pages = doc.page_count
        before = [len(doc[i].get_images(full=True)) for i in range(pages)]
    for k, p in enumerate(placements):
        a = asset_manager.get(p["asset_id"])
        if a is None:
            raise FlowError("ASSET_MISSING")
        nxt = workdir / f"step{k:03d}.pdf"
        service.stamp(cur, nxt, asset_manager.file_path(a), service.StampParams(
            x_mm=p["x_mm"], y_mm=p["y_mm"], width_mm=p["width_mm"], height_mm=p["height_mm"],
            rotation_deg=p["rotation_deg"], pages=[p["page"] - 1]))
        cur.unlink(missing_ok=True)
        cur = nxt
    # 驗證：打得開、頁數一樣、每一頁多出來的圖數 ≥ 那一頁要蓋的處數
    want: dict[int, int] = {}
    for p in placements:
        want[p["page"] - 1] = want.get(p["page"] - 1, 0) + 1
    with fitz.open(str(cur)) as out:
        if out.page_count != pages:
            raise RuntimeError("page count changed")
        for i, n in want.items():
            if len(out[i].get_images(full=True)) - before[i] < 1 or n < 1:
                raise RuntimeError(f"page {i + 1} has no stamp")
    return cur


def run_stamp(case_id: str) -> dict:
    """產檔（同步；呼叫端放到背景執行緒）。每份文件各自蓋、各自驗證；**全部成功**才一起放進
    `stamped/`、改成完成、通知（不會出現「三份蓋好兩份」的完成狀態）。"""
    import tempfile
    from . import seal_notify
    case = S.get_case(case_id)
    if case is None or case["state"] != "stamping":
        return case
    d = S.version_dir(case_id, int(case["version"]))
    tmpdir = Path(tempfile.mkdtemp(prefix="sealstamp_"))
    docs = documents(case, int(case["version"]))
    first = docs[0]["id"] if docs else None
    digests: dict[str, str] = {}
    try:
        ver = S.get_version(case_id, int(case["version"])) or {}
        wm = ver.get("watermark") or {}
        made: list[tuple[str, Path]] = []
        for i, doc in enumerate(docs):
            plcs = [p for p in case["final_placements"] if (p.get("doc") or first) == doc["id"]]
            made.append((doc["id"], _stamp_one(doc["path"], tmpdir / f"d{i:02d}", plcs, wm, case)))
        if not made:
            raise RuntimeError("no document")
        out_dir = d / STAMPED_DIR
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        for did, path in made:
            dest = out_dir / f"{did}.pdf"
            shutil.move(str(path), dest)
            digests[did] = sha256_file(dest)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("用印申請 %s 產檔失敗：%s", case_id, e)
        shutil.rmtree(d / STAMPED_DIR, ignore_errors=True)
        out = S.update_case(case_id, case["rev"], state="stamp_failed", stamp_error=str(e)[:300]) \
            or S.get_case(case_id)
        c = S.conn()
        from . import db
        with db.tx(c):
            S.add_event(c, case_id, "stamp_failed", actor_id=None, actor_label="", version=case["version"],
                        comment=str(e)[:300])
        seal_notify.notify("stamp_failed", out, [case.get("stamped_by")])
        return out
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    # 一份文件時照舊存雜湊字串（舊資料、舊 API 相容）；多份時存 {文件編號: 雜湊}
    digest = next(iter(digests.values())) if len(digests) == 1 else S._j(digests)
    now = time.time()
    c = S.conn()
    from . import db
    with db.tx(c):
        n = c.execute("UPDATE seal_cases SET state='done', stamped_sha256=?, done_at=?, closed_at=?, rev=rev+1, "
                      "updated_at=? WHERE id=? AND state='stamping' AND stamp_exec=?",
                      (digest, now, now, now, case_id, case["stamp_exec"])).rowcount
        if n:
            S.add_event(c, case_id, "done", actor_id=case.get("stamped_by"), actor_label=case.get("stamped_by_label") or "",
                        version=case["version"], detail={"sha256": digests, "placements": len(case["final_placements"]),
                                                         "documents": len(digests)})
    out = S.get_case(case_id)
    if n:
        for doc in docs:
            _record_stamp_history(out, doc, d / STAMPED_DIR / f"{doc['id']}.pdf")
        seal_notify.notify("done", out, [out["applicant_id"]])
    return out


def _record_stamp_history(case: dict, doc: dict, stamped: Path) -> None:
    """跟用印工具一樣記進「用印簽名歷史」（每份文件一筆），標明來自用印申請。失敗不影響案件。"""
    try:
        from .history_manager import stamp_history
        v = S.get_version(case["id"], case["version"])
        first = documents(case, int(case["version"]))[0]["id"]
        stamp_history.save(original_path=doc["path"], filled_path=stamped, preview_path=None,
                           original_filename=doc["name"],
                           username=case.get("stamped_by_label") or "",
                           extra={"via": "seal-request", "case": case["number"], "document": doc["id"],
                                  "assets": [a["asset_id"] for a in (v["assets"] if v else [])],
                                  "placements": [p for p in case["final_placements"]
                                                 if (p.get("doc") or first) == doc["id"]]})
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("seal-request: stamp_history.save failed")


def result_files(user: dict, case: dict) -> list[dict]:
    """用印後的 PDF（每份文件一個）：`[{id, name, path}]`。作廢的只有稽核員拿得到。"""
    if case["state"] == "void" and not user.get("auditor"):
        raise FlowError("VOIDED", 404)
    if case["state"] not in ("done", "void"):
        raise FlowError("NOT_DONE", 404)
    d = S.version_dir(case["id"], int(case["version"]))
    out, used = [], set()
    for doc in documents(case, int(case["version"])):
        p = d / STAMPED_DIR / f"{doc['id']}.pdf"
        if not p.exists() and doc["id"] == LEGACY_DOC_ID and (d / STAMPED_NAME).exists():
            p = d / STAMPED_NAME                      # v1.17.30～33 完成的舊案件
        if not p.exists():
            raise FlowError("NOT_DONE", 404)
        name = f"{Path(doc['name']).stem}_用印.pdf"
        k = 2
        while name in used:                            # 同名文件：檔名加序號，不互相蓋掉
            name = f"{Path(doc['name']).stem}_用印 ({k}).pdf"
            k += 1
        used.add(name)
        out.append({"id": doc["id"], "name": name, "path": p})
    if not out:
        raise FlowError("NOT_DONE", 404)
    return out


def result_path(user: dict, case: dict, doc_id: Optional[str] = None) -> tuple[Path, str]:
    """一份用印後的 PDF（`doc_id` 沒給＝第一份）。"""
    for f in result_files(user, case):
        if doc_id is None or f["id"] == doc_id:
            return f["path"], f["name"]
    raise FlowError("NOT_FOUND", 404)


def note_download(user: dict, case: dict) -> None:
    """第一次由申請者下載的時間（「已通知」與「已下載」分開看）。"""
    if int(user["id"]) == int(case["applicant_id"]) and not case.get("first_download_at"):
        S.update_case(case["id"], case["rev"], first_download_at=time.time())


def void(user: dict, case: dict, reason: str) -> dict:
    """作廢已完成的案件（只有管理員、原因必填）。停止一般取件；已下載出去的收不回。"""
    require_ready()
    if not user["admin"]:
        raise FlowError("NOT_ADMIN", 403)
    reason = (reason or "").strip()
    if not reason:
        raise FlowError("COMMENT_REQUIRED")
    c = S.conn()
    from . import db
    now = time.time()
    with db.tx(c):
        n = c.execute("UPDATE seal_cases SET state='void', void_reason=?, rev=rev+1, updated_at=? WHERE id=? "
                      "AND state='done'", (reason[:1000], now, case["id"])).rowcount
        if not n:
            raise FlowError("BAD_STATE", 409)
        S.add_event(c, case["id"], "void", actor_id=user["id"], actor_label=user["label"], version=case["version"],
                    comment=reason[:1000])
    return S.get_case(case["id"])


def purge_expired(now: Optional[float] = None) -> int:
    """保留期限到期的結束案件（完成、作廢、駁回、撤回）整筆刪除（資料與檔案）。0 天＝不刪。回刪了幾件。"""
    days = seal_policy.retention_days()
    if days <= 0:
        return 0
    cutoff = (now or time.time()) - days * 86400
    rows = S.list_cases("state IN ('done','void','rejected','withdrawn') AND closed_at IS NOT NULL AND closed_at<?",
                        (cutoff,), limit=10000)
    for c in rows:
        S.delete_case(c["id"])
    if rows:
        from . import audit_db
        audit_db.log_event("seal_req_purge", username="system", ip="", target="seal-request",
                           details={"count": len(rows), "retention_days": days,
                                    "numbers": [c["number"] for c in rows][:50]})
    return len(rows)
