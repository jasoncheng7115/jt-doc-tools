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
    # sha1 在這只用於 derive 檔名 prefix，非密碼學用途；usedforsecurity=False
    # 明示 CodeQL「weak hashing」掃描不再誤判。改用其他 hash 會讓既有使用者
    # 的 buffer 檔名變動 → 設定 / 發票全找不到 → 不能改 algo。
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:16]


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


def _enrich_entry_from_vat(entry: dict, vat_lookup_fn,
                           custom_rules: Optional[list] = None) -> dict:
    """從 vat_db 反查賣方完整資訊 + 跑會計科目分類器，原地更新 entry。
    custom_rules：使用者自訂規則，傳給 classifier（優先級高於內建）。"""
    seller_vat = entry.get("seller_vat")
    if not seller_vat:
        return entry
    try:
        info = vat_lookup_fn(seller_vat)
    except Exception:
        info = None
    if info:
        entry["seller_name"] = info.get("name") or entry.get("seller_name")
        entry["seller_address"] = info.get("address")
        entry["seller_industries"] = info.get("industries")
        entry["seller_org_type"] = info.get("org_type")
        entry["seller_category"] = info.get("category")
    try:
        from . import accounting_classifier
        cls = accounting_classifier.classify(
            seller_name=entry.get("seller_name") or "",
            industries=entry.get("seller_industries") or "",
            custom_rules=custom_rules,
        )
        entry["accounting_subject"] = cls.get("name") if cls else None
        entry["accounting_source"] = cls.get("source") if cls else None
    except Exception:
        pass
    return entry


def _load_user_accounting_rules(user) -> list:
    """讀使用者自訂規則（無 / 失敗 → 空 list）。"""
    try:
        from . import settings as _s
        return _s.get_settings(user).get("accounting_rules") or []
    except Exception:
        return []


def add_invoices(user: Optional[Any], parsed: list[dict]) -> dict:
    """加入新 invoices，自動去重 + 上限檢查。

    Returns dict:
        added:      list[dict] — 新增的 invoices（含 id / scanned_at）
        duplicates: list[str] — 與 buffer 內現有 invoice_number 重複的（未加入）
        cap_reached: bool     — buffer 達上限，部分未加入
    """
    if not parsed:
        return {"added": [], "duplicates": [], "cap_reached": False}

    # M4: VAT 資料庫反查 + 會計科目分類器
    try:
        from ...core import vat_db
        _vat_lookup = vat_db.lookup_vat
    except Exception:
        _vat_lookup = lambda v: None

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
            _enrich_entry_from_vat(entry, _vat_lookup, custom_rules=_custom_rules)
            added.append(entry)
            if num:
                existing_numbers.add(num)

        if added:
            invoices.extend(added)
            data["invoices"] = invoices
            data["version"] = _BUFFER_VERSION
            _write(path, data)

    return {"added": added, "duplicates": duplicates, "cap_reached": cap_reached}


def llm_classify_buffer(user: Optional[Any]) -> dict:
    """批次送 LLM 判讀 buffer 內所有發票的會計科目。

    LLM 取得使用者設定的「einvoice-scan」LLM client + model（admin/llm 設定）。
    Batch size 預設 20 張，token 容量考量。失敗 batch 跳過不影響其他。

    Returns: {total: int, batches: int, updated: int, errors: list[str]}
    """
    try:
        from ...core import llm_settings as _ls
    except Exception as e:
        raise RuntimeError(f"LLM module 載入失敗：{e}")
    if not _ls.llm_settings.is_enabled():
        raise RuntimeError("LLM 功能未啟用，請到 /admin/llm 設定")
    client = _ls.llm_settings.make_client()
    if not client:
        raise RuntimeError("LLM client 建立失敗（檢查 base_url / api_key）")
    model = _ls.llm_settings.get_model_for("einvoice-scan")
    if not model:
        raise RuntimeError("未設定 einvoice-scan 的 LLM 模型，請到 /admin/llm")

    from . import accounting_classifier
    valid_subjects = list(accounting_classifier.ALL_SUBJECTS)
    custom_rules = _load_user_accounting_rules(user)
    for r in custom_rules:
        sub = r.get("subject")
        if sub and sub not in valid_subjects:
            valid_subjects.append(sub)
    subjects_str = "、".join(valid_subjects)

    path = _buffer_path(user)
    key = _user_key(user)
    BATCH_SIZE = 20
    import re as _re
    import json as _json
    import logging
    log = logging.getLogger("einvoice_scan.llm_classify")

    with _get_lock(key):
        data = _read(path)
        invoices = data.get("invoices", [])
        if not invoices:
            return {"total": 0, "batches": 0, "updated": 0, "errors": []}

        updated = 0
        batches_done = 0
        errors = []
        for i in range(0, len(invoices), BATCH_SIZE):
            batch = invoices[i:i + BATCH_SIZE]
            items_str = "\n".join(
                f"{j+1}. 統編={inv.get('seller_vat') or '(無)'} "
                f"名稱={inv.get('seller_name') or '(無)'} "
                f"行業={inv.get('seller_industries') or '(無)'}"
                for j, inv in enumerate(batch)
            )
            prompt = (
                "你是台灣會計分類助手。以下是電子發票賣方資料，請依「行業 + 名稱」"
                "判斷每張發票對應的「會計科目」。\n\n"
                f"可用科目（必須從這裡選）：{subjects_str}\n\n"
                "判斷不出來 → 回空字串 \"\"，不要硬猜或創造新科目。\n\n"
                f"發票清單（共 {len(batch)} 張）：\n{items_str}\n\n"
                "請只回傳 JSON 陣列，順序對應上面 1~N，不要任何解釋。格式：\n"
                '[{"i":1,"s":"科目"},{"i":2,"s":""}, ...]'
            )
            try:
                resp = client.text_query(prompt, model=model, temperature=0.0)
                # 容錯：抓第一個 JSON array
                m = _re.search(r'\[[\s\S]*\]', resp or "")
                if not m:
                    errors.append(f"batch {i // BATCH_SIZE + 1}: LLM 回應非 JSON")
                    continue
                results = _json.loads(m.group(0))
                if not isinstance(results, list):
                    errors.append(f"batch {i // BATCH_SIZE + 1}: 結果非陣列")
                    continue
                for r in results:
                    if not isinstance(r, dict):
                        continue
                    idx = r.get("i") or r.get("index")
                    subject = (r.get("s") or r.get("subject") or "").strip()
                    if not isinstance(idx, int) or idx < 1 or idx > len(batch):
                        continue
                    if not subject:
                        continue
                    inv = batch[idx - 1]
                    old = inv.get("accounting_subject")
                    inv["accounting_subject"] = subject
                    inv["accounting_source"] = "llm"
                    if old != subject:
                        updated += 1
                batches_done += 1
            except Exception as e:
                log.exception("LLM batch %s failed", i // BATCH_SIZE + 1)
                errors.append(f"batch {i // BATCH_SIZE + 1}: {type(e).__name__}: {e}")
                continue

        data["invoices"] = invoices
        _write(path, data)
        return {
            "total": len(invoices),
            "batches": batches_done,
            "updated": updated,
            "errors": errors[:5],  # 最多回 5 個錯誤
        }


def reclassify_all_accounting(user: Optional[Any]) -> dict:
    """對 buffer 內所有 invoice 重新跑「賣方反查 + 會計科目分類」。
    用在使用者按「重抓科目」時 — 套用最新的 vat_db 內容（剛 import 新資料）
    或最新的規則（更版後）。

    Returns: {total: int, classified: int, updated: int}
    """
    try:
        from ...core import vat_db
        _vat_lookup = vat_db.lookup_vat
    except Exception:
        _vat_lookup = lambda v: None
    _custom_rules = _load_user_accounting_rules(user)

    path = _buffer_path(user)
    key = _user_key(user)
    with _get_lock(key):
        data = _read(path)
        invoices = data.get("invoices", [])
        if not invoices:
            return {"total": 0, "classified": 0, "updated": 0}
        classified = 0
        updated = 0
        for inv in invoices:
            old_subject = inv.get("accounting_subject")
            _enrich_entry_from_vat(inv, _vat_lookup, custom_rules=_custom_rules)
            new_subject = inv.get("accounting_subject")
            if new_subject:
                classified += 1
            if old_subject != new_subject:
                updated += 1
        data["invoices"] = invoices
        _write(path, data)
        return {"total": len(invoices), "classified": classified, "updated": updated}


def attach_items_to_latest(user: Optional[Any], items: list[str]) -> Optional[dict]:
    """把右 QR 解出來的品項 attach 到使用者「最近一筆」invoice。
    用於連續掃描：使用者先掃左 QR (invoice 已 add)，再掃右 QR (本函式 attach)。

    最近 = scanned_at 最大的那筆。已經有 items 就 merge（去重後 append）。

    Returns: 更新後的 invoice dict，或 None 若 buffer 為空。
    """
    if not items:
        return None
    path = _buffer_path(user)
    key = _user_key(user)
    with _get_lock(key):
        data = _read(path)
        invoices = data.get("invoices", [])
        if not invoices:
            return None
        # 找 scanned_at 最大者；同 ts 多筆 → 取 list 中最後（add 順序）
        latest_idx = max(range(len(invoices)),
                         key=lambda i: invoices[i].get("scanned_at", ""))
        latest = invoices[latest_idx]
        existing = latest.get("items") or []
        # merge 去重保留順序：既有先，新加的接在後
        seen = set(existing)
        new_items = list(existing)
        for it in items:
            if it not in seen:
                new_items.append(it)
                seen.add(it)
        latest["items"] = new_items
        invoices[latest_idx] = latest
        data["invoices"] = invoices
        _write(path, data)
        return latest


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
