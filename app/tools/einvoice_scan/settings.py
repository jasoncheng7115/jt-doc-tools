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
    {"id": "seq",            "label": "序號",     "default_visible": True,  "default_order": 1},
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
    {"id": "amount_untaxed", "label": "銷售額",   "default_visible": True,  "default_order": 5,
     "formats": _AMOUNT_FORMATS},
    {"id": "tax",            "label": "稅額",     "default_visible": True,  "default_order": 6,
     "formats": _AMOUNT_FORMATS},
    {"id": "seller_vat",     "label": "賣方統編", "default_visible": True,  "default_order": 7},
    {"id": "buyer_vat",      "label": "買方統編", "default_visible": True,  "default_order": 8},
    {"id": "random_code",    "label": "隨機碼",   "default_visible": False, "default_order": 9},
    {"id": "scanned_at",     "label": "掃描時間", "default_visible": True,  "default_order": 10,
     "formats": {
         "options": [
             {"id": "iso",       "label": "ISO 8601",     "example": "2026-05-13T14:30:00+08:00"},
             {"id": "local",     "label": "本地時間",     "example": "2026/05/13 14:30:00"},
             {"id": "date_only", "label": "僅日期",       "example": "2026/05/13"},
             {"id": "relative",  "label": "相對時間",     "example": "3 小時前"},
         ],
         "default": "local",
     }},
    {"id": "items",          "label": "品項數",   "default_visible": False, "default_order": 11},
    {"id": "note",           "label": "備註",     "default_visible": False, "default_order": 12},
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


def _default_settings() -> dict:
    return {
        "version": 2,                                       # M3：bump 版本
        "visible_columns": list(DEFAULT_VISIBLE),
        "column_order": list(DEFAULT_ORDER),
        "field_formats": _default_field_formats(),
        "my_company_vat": "",                              # M3.5：報帳檢查用
    }


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
    # 過濾掉未知欄位 ID（避免舊版 / 駭人輸入）；保持原順序
    if isinstance(raw_visible, list):
        out["visible_columns"] = [c for c in raw_visible if c in VALID_FIELD_IDS]
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
    return out


def update_settings(user: Optional[Any], payload: dict) -> dict:
    """更新該 user 的設定；只接受 visible_columns / column_order，其餘 ignore。"""
    if not isinstance(payload, dict):
        raise ValueError("payload 必須是 dict")

    current = get_settings(user)
    new_visible = current["visible_columns"]
    new_order = current["column_order"]
    new_formats = dict(current["field_formats"])
    new_my_vat = current["my_company_vat"]

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

    data = {
        "version": 2,
        "visible_columns": new_visible,
        "column_order": new_order,
        "field_formats": new_formats,
        "my_company_vat": new_my_vat,
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
