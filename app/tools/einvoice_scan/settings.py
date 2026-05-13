"""Per-user 設定儲存（visible_columns / column_order）。

設計：
- 跟 buffer 一樣 per-user 一個 JSON 檔，路徑
  `<data_dir>/einvoice_settings/<sha1>.json`，user_key 算法與 buffer 共用
- M2 階段 schema 只有 visible_columns + column_order；M3 會加 field_formats
- 缺欄位 / 缺檔 → 回 DEFAULT_SETTINGS（不報錯）
- 恢復預設 = 把該 user 的檔刪掉

FIELD_DEFINITIONS 雖然完整版要等 M3，但 M2 階段先 inline 一份精簡定義
（id / label / default_visible / default_order）— 給 settings 驗證 + 前端
seed 列表用。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Optional

from ...config import settings as app_settings
from . import buffer  # 重用 _user_key + _get_lock pattern

# 完整欄位定義 — id / label / default_visible / default_order + 可選 formats。
# M3：加 formats 區塊，前後端共用此定義（前端透過 GET /settings 取得）。
# 順序就是新使用者預設的 column_order。
#
# formats 結構：
#   {
#     "options": [{"id": str, "label": str, "example": str}, ...],
#     "default": str,
#     "group": str  # 共用 group 的欄位走同一個設定（amount group：total/untaxed/tax）
#   }
# 沒 formats 鍵 = 不可格式化（例如序號 / 統編 / 隨機碼 / 備註）
_AMOUNT_FORMATS = {
    "options": [
        {"id": "plain",    "label": "純數字",       "example": "1050"},
        {"id": "comma",    "label": "千分位",       "example": "1,050"},
        {"id": "currency", "label": "含幣別",       "example": "NT$ 1,050"},
    ],
    "default": "comma",
    "group": "amount",
}

FIELD_DEFINITIONS = [
    {"id": "seq",            "label": "序號",     "default_visible": False, "default_order": 1},
    {"id": "invoice_number", "label": "發票號碼", "default_visible": True,  "default_order": 2,
     "formats": {
         "options": [
             {"id": "compact", "label": "AB12345678",  "example": "AB12345678"},
             {"id": "dash",    "label": "AB-12345678", "example": "AB-12345678"},
             {"id": "space",   "label": "AB 12345678", "example": "AB 12345678"},
         ],
         "default": "dash",
     }},
    {"id": "date",           "label": "開立日期", "default_visible": True,  "default_order": 3,
     "formats": {
         "options": [
             {"id": "iso",         "label": "ISO",                "example": "2026-05-13"},
             {"id": "slash",       "label": "西元 / 斜線",        "example": "2026/05/13"},
             {"id": "chinese",     "label": "西元 / 中文",        "example": "2026年05月13日"},
             {"id": "roc",         "label": "民國 / 斜線",        "example": "115/05/13"},
             {"id": "roc_chinese", "label": "民國 / 中文",        "example": "民國115年05月13日"},
         ],
         "default": "slash",
     }},
    {"id": "amount_total",   "label": "總計金額", "default_visible": True,  "default_order": 4,
     "formats": _AMOUNT_FORMATS},
    {"id": "amount_untaxed", "label": "銷售額",   "default_visible": False, "default_order": 5,
     "formats": _AMOUNT_FORMATS},
    {"id": "tax",            "label": "稅額",     "default_visible": False, "default_order": 6,
     "formats": _AMOUNT_FORMATS},
    {"id": "seller_vat",     "label": "賣方統編", "default_visible": True,  "default_order": 7},
    {"id": "seller_name",    "label": "賣方名稱", "default_visible": True,  "default_order": 8},  # M4: 統編資料庫反查
    {"id": "buyer_vat",      "label": "買方統編", "default_visible": True,  "default_order": 9},
    {"id": "random_code",    "label": "隨機碼",   "default_visible": False, "default_order": 10},
    {"id": "scanned_at",     "label": "掃描時間", "default_visible": False, "default_order": 11,
     "formats": {
         "options": [
             {"id": "iso",       "label": "ISO 8601",     "example": "2026-05-13T14:30:00+08:00"},
             {"id": "local",     "label": "本地時間",     "example": "2026/05/13 14:30:00"},
             {"id": "date_only", "label": "僅日期",       "example": "2026/05/13"},
             {"id": "relative",  "label": "相對時間",     "example": "3 小時前"},
         ],
         "default": "local",
     }},
    {"id": "accounting_subject", "label": "科目",  "default_visible": True,  "default_order": 12},
    {"id": "note",           "label": "備註",     "default_visible": False, "default_order": 13},
]

VALID_FIELD_IDS = {f["id"] for f in FIELD_DEFINITIONS}

DEFAULT_VISIBLE = [f["id"] for f in FIELD_DEFINITIONS if f["default_visible"]]
DEFAULT_ORDER = [f["id"] for f in FIELD_DEFINITIONS]


def _default_field_formats() -> dict:
    """各 format-capable 欄位的預設格式 ID（給 settings 預設值用）。"""
    out = {}
    for f in FIELD_DEFINITIONS:
        if "formats" in f:
            out[f["id"]] = f["formats"]["default"]
    return out


def _valid_format_for_field(field_id: str, format_id: str) -> bool:
    for f in FIELD_DEFINITIONS:
        if f["id"] == field_id:
            fmts = f.get("formats")
            if not fmts:
                return False
            return any(o["id"] == format_id for o in fmts["options"])
    return False


def _settings_dir() -> Path:
    d = Path(app_settings.data_dir) / "einvoice_settings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_path(user: Optional[Any]) -> Path:
    return _settings_dir() / f"{buffer._user_key(user)}.json"


# Settings schema 當前版號。每次有不向下相容的「預設變動」就 bump，
# get_settings() 會 migrate 舊版資料到當前版本。
_SETTINGS_VERSION = 5


def _default_settings() -> dict:
    return {
        "version": _SETTINGS_VERSION,
        "visible_columns": list(DEFAULT_VISIBLE),
        "column_order": list(DEFAULT_ORDER),
        "field_formats": _default_field_formats(),
        "my_company_vat": "",                              # M3.5：報帳檢查用
        "accounting_rules": [],                            # M4+：使用者自訂會計科目規則
        "export_labels": {},                               # M5：匯出時自訂欄位標題
        # 當期發票檢查：預設關閉；mode='auto' 用系統算「最近一期」；
        # mode='custom' 用使用者指定 start/end
        "period_check": {
            "enabled": False,
            "mode": "auto",          # 'auto' or 'custom'
            "custom_start": "",      # ISO YYYY-MM-DD
            "custom_end": "",        # ISO YYYY-MM-DD
        },
    }


def _validate_period_check(raw) -> dict:
    default = {"enabled": False, "mode": "auto",
               "custom_start": "", "custom_end": ""}
    if not isinstance(raw, dict):
        return default
    out = dict(default)
    out["enabled"] = bool(raw.get("enabled"))
    mode = raw.get("mode")
    out["mode"] = mode if mode in ("auto", "custom") else "auto"
    for k in ("custom_start", "custom_end"):
        v = raw.get(k)
        if isinstance(v, str):
            v = v.strip()
            # 只接受 YYYY-MM-DD
            import re as _re
            if not v or _re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                out[k] = v
    return out


def _validate_export_labels(raw) -> dict:
    """驗證 export_labels: dict[field_id -> str]，最多 64 字。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or k not in VALID_FIELD_IDS:
            continue
        if not isinstance(v, str):
            continue
        v = v.strip()[:64]
        if v:
            out[k] = v
    return out


def _validate_accounting_rules(raw) -> list[dict]:
    """驗證 + 清理 accounting_rules list。
    每筆規則格式：{keywords: list[str], subject: str, match: 'industries'|'name'|'any'}
    無效項目（缺欄位 / 型別錯）整筆丟掉。"""
    if not isinstance(raw, list):
        return []
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        kws = r.get("keywords") or []
        sub = r.get("subject") or ""
        match = r.get("match") or "any"
        if not isinstance(kws, list) or not isinstance(sub, str):
            continue
        # keywords 必須是 list of non-empty str
        clean_kws = [k.strip() for k in kws
                     if isinstance(k, str) and k.strip()]
        sub = sub.strip()
        if not clean_kws or not sub:
            continue
        if match not in ("industries", "name", "any"):
            match = "any"
        # 限制：每條規則最多 20 個 keyword、subject 最多 30 字、kw 最多 50 字
        clean_kws = clean_kws[:20]
        clean_kws = [k[:50] for k in clean_kws]
        sub = sub[:30]
        out.append({"keywords": clean_kws, "subject": sub, "match": match})
        # 整個 list 上限 200 條（避免 DoS）
        if len(out) >= 200:
            break
    return out


def get_settings(user: Optional[Any]) -> dict:
    """取得該 user 的設定；缺檔 / 毀損 / 缺欄位都 fallback 到預設。"""
    path = _settings_path(user)
    key = buffer._user_key(user)
    with buffer._get_lock(f"settings:{key}"):
        if not path.exists():
            return _default_settings()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _default_settings()

    out = _default_settings()
    raw_visible = data.get("visible_columns")
    raw_order = data.get("column_order")
    raw_formats = data.get("field_formats")
    raw_my_vat = data.get("my_company_vat")
    raw_version = data.get("version", 1)
    # 過濾掉未知欄位 ID（避免舊版 / 駭人輸入）；保持原順序
    if isinstance(raw_visible, list):
        out["visible_columns"] = [c for c in raw_visible if c in VALID_FIELD_IDS]
    # Migration v2 → v3: 銷售額 / 稅額 / 掃描時間 預設改為隱藏。
    # 舊使用者的 visible_columns 內如果還有這幾個，視為預設值而非主動勾選 → 移除。
    # 主動勾選的不會被誤改：v3 之後 user 任何 visible_columns 變更都會留 version=3。
    _do_migration_write = False
    if isinstance(raw_version, int) and raw_version < 3:
        # v2 → v3：銷售額 / 稅額 / 掃描時間 預設改為隱藏
        _v3_newly_hidden = {"amount_untaxed", "tax", "scanned_at"}
        out["visible_columns"] = [c for c in out["visible_columns"]
                                  if c not in _v3_newly_hidden]
        _do_migration_write = True
    if isinstance(raw_version, int) and raw_version < 4:
        # v3 → v4：新欄位 accounting_subject 預設啟用，自動加入 visible_columns
        if "accounting_subject" not in out["visible_columns"]:
            # 插在 buyer_vat 之後（如果有），不然加在尾巴
            try:
                idx = out["visible_columns"].index("buyer_vat") + 1
                out["visible_columns"].insert(idx, "accounting_subject")
            except ValueError:
                out["visible_columns"].append("accounting_subject")
        _do_migration_write = True
    if isinstance(raw_version, int) and raw_version < 5:
        # v4 → v5：序號 預設改為隱藏
        out["visible_columns"] = [c for c in out["visible_columns"] if c != "seq"]
        _do_migration_write = True
    if isinstance(raw_order, list):
        seen = set()
        cleaned = []
        for c in raw_order:
            if c in VALID_FIELD_IDS and c not in seen:
                cleaned.append(c)
                seen.add(c)
        # 任何 default 裡有但 user 沒列的（新版加的欄位）放最後，避免漏顯示
        for c in DEFAULT_ORDER:
            if c not in seen:
                cleaned.append(c)
        out["column_order"] = cleaned
    if isinstance(raw_formats, dict):
        # 驗證 format ID 屬於該欄位的選項；不認的整個 entry 丟掉，回 default
        cleaned_fmts = dict(out["field_formats"])
        for fid, val in raw_formats.items():
            if isinstance(val, str) and _valid_format_for_field(fid, val):
                cleaned_fmts[fid] = val
        out["field_formats"] = cleaned_fmts
    if isinstance(raw_my_vat, str):
        # 統編格式：8 位數字（or 空字串 = 未設定）
        v = raw_my_vat.strip()
        if v == "" or (len(v) == 8 and v.isdigit()):
            out["my_company_vat"] = v
    # 自訂會計科目規則
    raw_rules = data.get("accounting_rules")
    if raw_rules is not None:
        out["accounting_rules"] = _validate_accounting_rules(raw_rules)
    # 匯出欄位標題
    raw_export_labels = data.get("export_labels")
    if raw_export_labels is not None:
        out["export_labels"] = _validate_export_labels(raw_export_labels)
    # 當期發票檢查
    raw_period = data.get("period_check")
    if raw_period is not None:
        out["period_check"] = _validate_period_check(raw_period)
    # 安全網：如果 visible_columns 不知為何變成空 list（之前某版 bug 把使用者
    # 設定洗成空），自動還原為預設可見欄位，避免整張表格空白。
    if not out["visible_columns"]:
        out["visible_columns"] = list(DEFAULT_VISIBLE)
        _do_migration_write = True
    # 觸發 migration write-back（舊版 → 當前版，避免下次再 migrate）
    if _do_migration_write:
        try:
            _write_settings_atomic(path, key, out)
        except Exception:
            pass  # 寫失敗不致命；下次 load 會再嘗試
    return out


def _write_settings_atomic(path: Path, key: str, data: dict) -> None:
    """Atomic write helper — 給 migration / update_settings 共用。"""
    with buffer._get_lock(f"settings:{key}"):
        tmp = path.with_suffix(f".tmp-{secrets.token_hex(4)}")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                with tmp.open("rb") as f:
                    os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
            tmp.replace(path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def update_settings(user: Optional[Any], payload: dict) -> dict:
    """更新該 user 的設定；只接受 visible_columns / column_order，其餘 ignore。"""
    if not isinstance(payload, dict):
        raise ValueError("payload 必須是 dict")

    current = get_settings(user)
    new_visible = current["visible_columns"]
    new_order = current["column_order"]
    new_formats = dict(current["field_formats"])
    new_my_vat = current["my_company_vat"]
    new_rules = list(current.get("accounting_rules") or [])
    new_export_labels = dict(current.get("export_labels") or {})
    new_period_check = dict(current.get("period_check") or {
        "enabled": False, "mode": "auto", "custom_start": "", "custom_end": "",
    })

    if "visible_columns" in payload:
        v = payload["visible_columns"]
        if not isinstance(v, list):
            raise ValueError("visible_columns 必須是陣列")
        new_visible = [c for c in v if c in VALID_FIELD_IDS]

    if "column_order" in payload:
        o = payload["column_order"]
        if not isinstance(o, list):
            raise ValueError("column_order 必須是陣列")
        seen = set()
        cleaned = []
        for c in o:
            if c in VALID_FIELD_IDS and c not in seen:
                cleaned.append(c)
                seen.add(c)
        # 補齊任何 user 沒列的欄位 ID 放最後（避免列表「忘了」某欄位）
        for c in DEFAULT_ORDER:
            if c not in seen:
                cleaned.append(c)
        new_order = cleaned

    if "field_formats" in payload:
        ff = payload["field_formats"]
        if not isinstance(ff, dict):
            raise ValueError("field_formats 必須是 dict")
        for fid, val in ff.items():
            if isinstance(val, str) and _valid_format_for_field(fid, val):
                new_formats[fid] = val
            # 不認的 silent ignore（不報錯，避免前端版本不齊就掛）

    if "my_company_vat" in payload:
        v = payload["my_company_vat"]
        if not isinstance(v, str):
            raise ValueError("my_company_vat 必須是字串")
        v = v.strip()
        if v != "" and (len(v) != 8 or not v.isdigit()):
            raise ValueError("my_company_vat 必須是 8 位數字或空字串")
        new_my_vat = v

    if "accounting_rules" in payload:
        new_rules = _validate_accounting_rules(payload["accounting_rules"])

    if "export_labels" in payload:
        new_export_labels = _validate_export_labels(payload["export_labels"])

    if "period_check" in payload:
        new_period_check = _validate_period_check(payload["period_check"])

    data = {
        "version": _SETTINGS_VERSION,
        "visible_columns": new_visible,
        "column_order": new_order,
        "field_formats": new_formats,
        "my_company_vat": new_my_vat,
        "accounting_rules": new_rules,
        "export_labels": new_export_labels,
        "period_check": new_period_check,
    }
    path = _settings_path(user)
    key = buffer._user_key(user)
    with buffer._get_lock(f"settings:{key}"):
        # Atomic write 同 buffer 模式
        tmp = path.with_suffix(f".tmp-{secrets.token_hex(4)}")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                with tmp.open("rb") as f:
                    os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
            tmp.replace(path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return data


def reset_settings(user: Optional[Any]) -> dict:
    """恢復預設 — 直接刪掉 user settings 檔，下次 get 會回預設。"""
    path = _settings_path(user)
    key = buffer._user_key(user)
    with buffer._get_lock(f"settings:{key}"):
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    return _default_settings()
