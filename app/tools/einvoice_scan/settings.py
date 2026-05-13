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

# 完整欄位定義 — M2 用 id / label / default_visible / default_order；
# M3 會擴充 formats。順序就是新使用者預設的 column_order。
FIELD_DEFINITIONS = [
    {"id": "seq",            "label": "序號",     "default_visible": True,  "default_order": 1},
    {"id": "invoice_number", "label": "發票號碼", "default_visible": True,  "default_order": 2},
    {"id": "date",           "label": "開立日期", "default_visible": True,  "default_order": 3},
    {"id": "amount_total",   "label": "總計金額", "default_visible": True,  "default_order": 4},
    {"id": "amount_untaxed", "label": "銷售額",   "default_visible": True,  "default_order": 5},
    {"id": "tax",            "label": "稅額",     "default_visible": True,  "default_order": 6},
    {"id": "seller_vat",     "label": "賣方統編", "default_visible": True,  "default_order": 7},
    {"id": "buyer_vat",      "label": "買方統編", "default_visible": True,  "default_order": 8},
    {"id": "random_code",    "label": "隨機碼",   "default_visible": False, "default_order": 9},
    {"id": "scanned_at",     "label": "掃描時間", "default_visible": True,  "default_order": 10},
    {"id": "note",           "label": "備註",     "default_visible": False, "default_order": 11},
]

VALID_FIELD_IDS = {f["id"] for f in FIELD_DEFINITIONS}

DEFAULT_VISIBLE = [f["id"] for f in FIELD_DEFINITIONS if f["default_visible"]]
DEFAULT_ORDER = [f["id"] for f in FIELD_DEFINITIONS]


def _settings_dir() -> Path:
    d = Path(app_settings.data_dir) / "einvoice_settings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_path(user: Optional[Any]) -> Path:
    return _settings_dir() / f"{buffer._user_key(user)}.json"


def _default_settings() -> dict:
    return {
        "version": 1,
        "visible_columns": list(DEFAULT_VISIBLE),
        "column_order": list(DEFAULT_ORDER),
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
    return out


def update_settings(user: Optional[Any], payload: dict) -> dict:
    """更新該 user 的設定；只接受 visible_columns / column_order，其餘 ignore。"""
    if not isinstance(payload, dict):
        raise ValueError("payload 必須是 dict")

    current = get_settings(user)
    new_visible = current["visible_columns"]
    new_order = current["column_order"]

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

    data = {
        "version": 1,
        "visible_columns": new_visible,
        "column_order": new_order,
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
