"""用印申請：功能開關與每顆章的管制設定（`data/seal_request.json`）。

規格 `docs-share/seal-approval/jtdt-seal-approval-spec-v1.md`（內部文件）。第 1 版只做這一層：
每顆印章 / 簽名可以設成「僅限核准的用印申請案件使用」，設了之後所有用印入口一律擋下
（實際擋在 `asset_access.can_use_asset`）。

規則（使用者 2026-11-05）：

* **整個功能預設不啟用**；啟用後才能把章改成受管制。啟用需要先啟用登入（多人審核要分得出人）。
* **還有受管制的章時不准停用**：停用不可以無聲解除管制（公司章突然誰都能蓋）。管理員要先把那幾顆
  改回「直接使用」。
* 既有的章升級後一律「直接使用」，行為跟升級前完全一樣。
* 設定檔讀不出來（毀損、權限錯）時**當成所有印章與簽名都受管制**：讀不到設定就放行，等於檔案一壞
  管制就消失。

沒啟用時也可以先設保管人、可申請對象、保留期限（流程在 `seal_store`）；只有改成「僅限核准案件使用」
要等啟用（使用者 2026-11-06：不然會變成啟用了才發現流程還沒設）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import atomic_json

log = logging.getLogger(__name__)

#: 可以設管制的資產種類。浮水印與 Logo 不是用印（Logo 常用在信頭，不代表核准）。
CONTROLLABLE_TYPES = ("stamp", "signature")
MODES = ("direct", "approval")
#: 主體種類（同權限系統；組織單位的 key 是 DN）。
SUBJECT_TYPES = ("user", "group", "ou")
MAX_SUBJECTS = 500
#: AI 用印（v1.17.38，MCP）：每顆章各自的授權。預設關閉；需要確認預設開（使用者 2026-11-12：印章與簽名都預設要確認）。
AI_DEFAULT: dict[str, Any] = {"enabled": False, "subjects": [], "confirm": True, "daily_limit": 0}
MAX_AI_DAILY = 1000

_LOCK = threading.Lock()


class PolicyError(ValueError):
    """設定不被接受；`code` 給畫面與 API 用。"""

    def __init__(self, code: str, detail: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


MESSAGES = {
    "AUTH_REQUIRED": "要先啟用帳號登入，才能啟用用印申請（多人審核要分得出是誰）",
    "NOT_ENABLED": "用印申請尚未啟用",
    "STILL_CONTROLLED": "還有「僅限核准案件使用」的印章或簽名，請先改回「直接使用」再停用",
    "ASSET_NOT_FOUND": "找不到這個資產",
    "NOT_CONTROLLABLE": "只有印章與簽名可以設定管制",
    "BAD_MODE": "使用模式不正確",
    "BAD_SUBJECT": "人員或群組的格式不正確",
    "TOO_MANY": "人員與群組太多",
    "UNREADABLE": "用印申請的設定檔讀不出來，所有印章與簽名暫時都當成受管制；請洽管理員檢查",
    "BAD_RETENTION": "保留期限要是 0（不自動刪除）到 36500 天之間的整數",
    "BAD_AI": "AI 使用的設定格式不正確",
    "AI_ON_CONTROLLED": "「僅限核准案件使用」的章不能開放 AI 使用",
}


def message(code: str) -> str:
    return MESSAGES.get(code, code)


def _path() -> Path:
    from ..config import settings
    return settings.data_dir / "seal_request.json"


class _Unreadable(Exception):
    pass


def _read() -> dict[str, Any]:
    """讀設定。檔案不在＝全新（未啟用、沒有管制）；檔案在但讀不出來 → `_Unreadable`。"""
    p = _path()
    if not p.exists():
        return {"enabled": False, "assets": {}, "retention_days": 0, "updated_at": 0.0}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise _Unreadable(str(e)) from e
    if not isinstance(raw, dict) or not isinstance(raw.get("assets", {}), dict):
        raise _Unreadable("bad structure")
    rd = raw.get("retention_days")
    return {"enabled": raw.get("enabled") is True,
            "assets": {str(k): v for k, v in (raw.get("assets") or {}).items() if isinstance(v, dict)},
            "retention_days": rd if isinstance(rd, int) and not isinstance(rd, bool) and rd >= 0 else 0,
            "updated_at": float(raw.get("updated_at") or 0.0)}


def _write(data: dict[str, Any]) -> None:
    data["updated_at"] = time.time()
    atomic_json.write_json(_path(), data, mode=0o600)


def readable() -> bool:
    try:
        _read()
        return True
    except _Unreadable:
        return False


def is_enabled() -> bool:
    try:
        return _read()["enabled"]
    except _Unreadable:
        return False


def _clean_policy(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode") if raw.get("mode") in MODES else "direct"

    def subs(v: Any) -> list[dict]:
        out = []
        for s in v if isinstance(v, list) else []:
            if isinstance(s, dict) and s.get("type") in SUBJECT_TYPES and str(s.get("key") or "").strip():
                out.append({"type": s["type"], "key": str(s["key"]).strip()})
        return out

    ai_raw = raw.get("ai") if isinstance(raw.get("ai"), dict) else {}
    try:
        limit = max(0, min(MAX_AI_DAILY, int(ai_raw.get("daily_limit") or 0)))
    except (TypeError, ValueError):
        limit = 0
    ai = {"enabled": ai_raw.get("enabled") is True, "subjects": subs(ai_raw.get("subjects")),
          "confirm": ai_raw.get("confirm") is not False, "daily_limit": limit}
    return {"mode": mode, "requesters": subs(raw.get("requesters")),
            "custodians": subs(raw.get("custodians")), "ai": ai}


def get_policy(asset_id: str) -> dict[str, Any]:
    """這顆章的設定（沒設過＝直接使用、沒有名單）。讀不出來時 `mode` 回 `approval`（見模組說明）。"""
    try:
        data = _read()
    except _Unreadable:
        return {"mode": "approval", "requesters": [], "custodians": [], "ai": dict(AI_DEFAULT),
                "unreadable": True}
    return _clean_policy(data["assets"].get(str(asset_id)))


def is_controlled(asset_id: str, asset_type: str) -> bool:
    """這顆章是不是「僅限核准案件使用」。**跟功能開關無關**：停用前一定要先解除，所以啟用與否不影響判斷。"""
    if asset_type not in CONTROLLABLE_TYPES:
        return False
    try:
        data = _read()
    except _Unreadable:
        log.error("seal_request.json 讀不出來：所有印章與簽名暫時當成受管制")
        return True
    return _clean_policy(data["assets"].get(str(asset_id)))["mode"] == "approval"


def controlled_ids() -> list[str]:
    try:
        data = _read()
    except _Unreadable:
        return []
    return [aid for aid, p in data["assets"].items() if _clean_policy(p)["mode"] == "approval"]


def set_enabled(on: bool) -> dict[str, Any]:
    """啟用 / 停用。啟用要先啟用登入；停用時不可以還有受管制的章。"""
    if not isinstance(on, bool):
        raise PolicyError("BAD_MODE")
    from . import auth_settings
    with _LOCK:
        try:
            data = _read()
        except _Unreadable:
            raise PolicyError("UNREADABLE")
        if on and not auth_settings.is_enabled():
            raise PolicyError("AUTH_REQUIRED")
        if not on:
            still = [aid for aid, p in data["assets"].items() if _clean_policy(p)["mode"] == "approval"]
            if still:
                raise PolicyError("STILL_CONTROLLED", {"assets": still})
        data["enabled"] = on
        _write(data)
        return {"enabled": on}


def _check_subjects(v: Any) -> list[dict]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise PolicyError("BAD_SUBJECT")
    out: list[dict] = []
    for s in v:
        if not isinstance(s, dict):
            raise PolicyError("BAD_SUBJECT")
        st, sk = s.get("type"), str(s.get("key") or "").strip()
        if st not in SUBJECT_TYPES or not sk or len(sk) > 500:
            raise PolicyError("BAD_SUBJECT")
        if st in ("user", "group") and not sk.isdigit():
            raise PolicyError("BAD_SUBJECT")
        item = {"type": st, "key": sk}
        if item not in out:
            out.append(item)
    if len(out) > MAX_SUBJECTS:
        raise PolicyError("TOO_MANY")
    return out


def _check_ai(v: Any, cur: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise PolicyError("BAD_AI")
    out = dict(cur)
    for key in ("enabled", "confirm"):
        if key in v:
            if not isinstance(v[key], bool):
                raise PolicyError("BAD_AI")
            out[key] = v[key]
    if "subjects" in v:
        out["subjects"] = _check_subjects(v["subjects"])
    if "daily_limit" in v:
        n = v["daily_limit"]
        if isinstance(n, bool) or not isinstance(n, int) or not (0 <= n <= MAX_AI_DAILY):
            raise PolicyError("BAD_AI")
        out["daily_limit"] = n
    return out


def ai_policy(asset_id: str) -> dict[str, Any]:
    """這顆章的 AI 授權（讀不出設定檔時當成關閉）。"""
    return dict(get_policy(asset_id).get("ai") or AI_DEFAULT)


def set_policy(asset_id: str, asset_type: str, patch: dict[str, Any]) -> dict[str, Any]:
    """改一顆章的設定。功能沒啟用時不能改成受管制（停用狀態下不可以出現新的管制）。回改完的設定。"""
    if asset_type not in CONTROLLABLE_TYPES:
        raise PolicyError("NOT_CONTROLLABLE")
    if not isinstance(patch, dict):
        raise PolicyError("BAD_MODE")
    with _LOCK:
        try:
            data = _read()
        except _Unreadable:
            raise PolicyError("UNREADABLE")
        # 沒啟用時可以先設保管人與可申請對象（啟用之前就準備好）；只有改成
        # 「僅限核准案件使用」要等啟用 —— 那一改馬上生效，功能沒開時沒有人申請得了，
        # 那顆章就等於誰都不能用。
        if not data["enabled"] and patch.get("mode") == "approval":
            raise PolicyError("NOT_ENABLED")
        cur = _clean_policy(data["assets"].get(str(asset_id)))
        if "mode" in patch:
            if patch["mode"] not in MODES:
                raise PolicyError("BAD_MODE")
            cur["mode"] = patch["mode"]
        if "requesters" in patch:
            cur["requesters"] = _check_subjects(patch["requesters"])
        if "custodians" in patch:
            cur["custodians"] = _check_subjects(patch["custodians"])
        if "ai" in patch:
            cur["ai"] = _check_ai(patch["ai"], cur["ai"])
        if cur["mode"] == "approval" and cur["ai"]["enabled"]:
            # 受管制的章 AI 一律不能用（使用者 2026-11-12）：不可以同時存在，免得之後改回直接使用時無聲開放
            raise PolicyError("AI_ON_CONTROLLED")
        if cur == {"mode": "direct", "requesters": [], "custodians": [], "ai": AI_DEFAULT}:
            data["assets"].pop(str(asset_id), None)
        else:
            data["assets"][str(asset_id)] = cur
        _write(data)
        return dict(cur)


def forget_asset(asset_id: str) -> None:
    """資產刪掉時一併清掉它的設定（不然之後重用同一個編號會帶到舊設定）。"""
    with _LOCK:
        try:
            data = _read()
        except _Unreadable:
            return
        if data["assets"].pop(str(asset_id), None) is not None:
            _write(data)


#: 案件保留期限上限（天）。0 ＝ 不自動刪除（預設；依公司規定由管理員設定）。
MAX_RETENTION_DAYS = 36500


def retention_days() -> int:
    """結束的案件（完成、作廢、駁回、撤回）保留幾天；0 ＝ 不自動刪除。讀不出設定檔時也是 0（不刪）。"""
    try:
        return int(_read()["retention_days"])
    except _Unreadable:
        return 0


def set_retention_days(days: Any) -> int:
    if not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= MAX_RETENTION_DAYS:
        raise PolicyError("BAD_RETENTION")
    with _LOCK:
        try:
            data = _read()
        except _Unreadable:
            raise PolicyError("UNREADABLE")
        data["retention_days"] = days
        _write(data)
        return days
