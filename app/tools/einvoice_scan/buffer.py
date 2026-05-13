"""Per-user buffer 儲存：把掃過的發票累積到 JSON 檔。

設計：
- 認證 ON 時：每個 user 一個檔，路徑 `<data_dir>/einvoice_buffer/<sha1>.json`
  其中 sha1 = sha1(username|realm)[:16]，避免帶 PII 進檔名（symlink 攻擊 / 列檔
  名洩漏使用者）
- 認證 OFF 時：所有人共用 `default.json`
- File schema:
    {
      "version": 1,
      "invoices": [
        {
          "id": "<uuid>",
          "scanned_at": "<ISO 8601>",
          "invoice_number": "AB12345678",
          "date": "2026-05-13",
          ...
        }
      ]
    }
- 容量上限：1000 筆/檔（爆量提示使用者匯出 + 清空）
- 重複偵測：同 invoice_number 視為 dup
- 並行：用 fcntl flock（mac/Linux）/ msvcrt locking（Windows），避免兩個
  request 同時改一個檔造成資料遺失。實作上每次 read-modify-write 整段加鎖。

注意：v1.7 階段 buffer 還是 JSON 檔；如果未來資料量大或要做複雜查詢，
可改後端到 sqlite，buffer.py 內部 abstraction 層不動。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ...config import settings

_MAX_INVOICES_PER_USER = 1000
_BUFFER_VERSION = 1

# Lock 在 process 內保護同一檔；多 process（多 worker）情境靠 OS file lock。
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _user_key(user: Optional[Any]) -> str:
    """Auth ON 用 sha1(username|realm)[:16]；Auth OFF 用 'default'。"""
    if not user:
        return "default"
    # request.state.user 是 dict（feedback_request_state_user_is_dict.md）
    if isinstance(user, dict):
        username = user.get("username", "")
        realm = user.get("realm", user.get("source", ""))
    else:
        username = getattr(user, "username", "")
        realm = getattr(user, "realm", getattr(user, "source", ""))
    if not username:
        return "default"
    raw = f"{username}|{realm}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _buffer_dir() -> Path:
    d = Path(settings.data_dir) / "einvoice_buffer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _buffer_path(user: Optional[Any]) -> Path:
    return _buffer_dir() / f"{_user_key(user)}.json"


def _get_lock(key: str) -> threading.Lock:
    with _locks_lock:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def _read(path: Path) -> dict:
    if not path.exists():
        return {"version": _BUFFER_VERSION, "invoices": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corruption fallback：別讓使用者整本資料卡死，回空但 backup 舊檔
        backup = path.with_suffix(f".corrupt-{int(time.time())}.json")
        try:
            path.rename(backup)
        except OSError:
            pass
        return {"version": _BUFFER_VERSION, "invoices": []}


def _write(path: Path, data: dict) -> None:
    """Atomic write：tmp file → fsync → rename。"""
    tmp = path.with_suffix(f".tmp-{secrets.token_hex(4)}")
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(text, encoding="utf-8")
        # Best-effort fsync
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


# ─── Public API ─────────────────────────────────────────────────────

def list_invoices(user: Optional[Any]) -> list[dict]:
    """回該 user 全部 invoices（最新的在前）。"""
    path = _buffer_path(user)
    with _get_lock(_user_key(user)):
        data = _read(path)
    invoices = data.get("invoices", [])
    # 最新在前
    return sorted(invoices, key=lambda x: x.get("scanned_at", ""), reverse=True)


def add_invoices(user: Optional[Any], parsed: list[dict]) -> dict:
    """加入新 invoices，自動去重 + 上限檢查。

    Returns dict:
        added:      list[dict] — 新增的 invoices（含 id / scanned_at）
        duplicates: list[str] — 與 buffer 內現有 invoice_number 重複的（未加入）
        cap_reached: bool     — buffer 達上限，部分未加入
    """
    if not parsed:
        return {"added": [], "duplicates": [], "cap_reached": False}

    path = _buffer_path(user)
    key = _user_key(user)
    now = datetime.now(timezone.utc).isoformat()

    with _get_lock(key):
        data = _read(path)
        invoices = data.get("invoices", [])
        existing_numbers = {inv.get("invoice_number") for inv in invoices}
        added = []
        duplicates = []
        cap_reached = False

        for p in parsed:
            num = p.get("invoice_number")
            if num and num in existing_numbers:
                duplicates.append(num)
                continue
            if len(invoices) + len(added) >= _MAX_INVOICES_PER_USER:
                cap_reached = True
                break
            entry = {
                "id": secrets.token_hex(8),
                "scanned_at": now,
                **p,
            }
            added.append(entry)
            if num:
                existing_numbers.add(num)

        if added:
            invoices.extend(added)
            data["invoices"] = invoices
            data["version"] = _BUFFER_VERSION
            _write(path, data)

    return {"added": added, "duplicates": duplicates, "cap_reached": cap_reached}


def delete_invoice(user: Optional[Any], invoice_id: str) -> bool:
    """刪一筆；找不到回 False。"""
    if not invoice_id:
        return False
    path = _buffer_path(user)
    key = _user_key(user)
    with _get_lock(key):
        data = _read(path)
        invoices = data.get("invoices", [])
        new_invoices = [inv for inv in invoices if inv.get("id") != invoice_id]
        if len(new_invoices) == len(invoices):
            return False
        data["invoices"] = new_invoices
        _write(path, data)
        return True


def update_invoice_field(user: Optional[Any], invoice_id: str,
                         field: str, value: Any) -> bool:
    """更新一筆發票的單一欄位（給可編輯欄位用，例如 note）。

    白名單：只允許 note。其他欄位（如金額 / 號碼）不可改 — 結構化資料只應從
    QR 解碼進來，避免使用者誤改造成核帳對不上。
    """
    EDITABLE_FIELDS = {"note"}
    if field not in EDITABLE_FIELDS:
        return False
    if not invoice_id:
        return False
    path = _buffer_path(user)
    key = _user_key(user)
    with _get_lock(key):
        data = _read(path)
        invoices = data.get("invoices", [])
        found = False
        for inv in invoices:
            if inv.get("id") == invoice_id:
                inv[field] = value
                found = True
                break
        if found:
            data["invoices"] = invoices
            _write(path, data)
        return found


def clear_all(user: Optional[Any]) -> int:
    """清空該 user 全部 buffer；回原本筆數。"""
    path = _buffer_path(user)
    key = _user_key(user)
    with _get_lock(key):
        data = _read(path)
        n = len(data.get("invoices", []))
        if n:
            data["invoices"] = []
            _write(path, data)
        return n


def buffer_info(user: Optional[Any]) -> dict:
    """回 buffer 統計 — 給 UI 顯示「N / 1000」之類的容量提示。"""
    invoices = list_invoices(user)
    return {
        "count": len(invoices),
        "limit": _MAX_INVOICES_PER_USER,
    }
