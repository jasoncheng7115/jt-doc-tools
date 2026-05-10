"""我方資料 — 使用者預先登錄自家公司 / 子公司 / 集團名稱，
每次跨檔檢查時自動排除，並針對打錯（漏字 / 統編錯）反向檢查。

支援多公司（user 可能代多家集團子公司送件、或自由業者代多家客戶整理文件）。

儲存位置：<data>/submission_check/self_entities/<user_id>.json
auth OFF 時：用 'anonymous' 當 key（共用一份）
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from ...config import settings


def _root() -> Path:
    p = settings.data_dir / "submission_check" / "self_entities"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _user_key(user) -> str:
    if not user or not isinstance(user, dict):
        return "anonymous"
    uid = user.get("user_id")
    return str(uid) if uid is not None else "anonymous"


def _path_for(user_key: str) -> Path:
    if not re.match(r"^[a-zA-Z0-9_.-]+$", user_key):
        user_key = "anonymous"
    return _root() / f"{user_key}.json"


def load_entities(user_key: str) -> list[dict]:
    """回 list of entities，每個是 {id, name, tax_id, address, aliases, type, note}。"""
    f = _path_for(user_key)
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "entities" in data:
            return data["entities"]
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_entities(user_key: str, entities: list[dict]) -> None:
    f = _path_for(user_key)
    payload = {"entities": entities, "updated_at": time.time()}
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def add_entity(user_key: str, entity: dict) -> dict:
    """加一筆。entity 必須有 name；其他選填。回新加的 entity（含 generated id）。"""
    name = (entity.get("name") or "").strip()
    if not name:
        raise ValueError("entity.name is required")
    ents = load_entities(user_key)
    import uuid
    new_e = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "tax_id": (entity.get("tax_id") or "").strip(),
        "address": (entity.get("address") or "").strip(),
        "aliases": [a.strip() for a in (entity.get("aliases") or []) if a.strip()],
        "type": entity.get("type") or "company",   # company / agency / school / legal-person / etc
        "note": (entity.get("note") or "").strip(),
        "created_at": time.time(),
    }
    ents.append(new_e)
    save_entities(user_key, ents)
    return new_e


def update_entity(user_key: str, entity_id: str, updates: dict) -> bool:
    ents = load_entities(user_key)
    found = False
    for e in ents:
        if e.get("id") == entity_id:
            for k in ("name", "tax_id", "address", "type", "note"):
                if k in updates:
                    e[k] = (updates[k] or "").strip() if isinstance(updates[k], str) else updates[k]
            if "aliases" in updates:
                e["aliases"] = [a.strip() for a in (updates["aliases"] or []) if a.strip()]
            e["updated_at"] = time.time()
            found = True
            break
    if found:
        save_entities(user_key, ents)
    return found


def delete_entity(user_key: str, entity_id: str) -> bool:
    ents = load_entities(user_key)
    n_before = len(ents)
    ents = [e for e in ents if e.get("id") != entity_id]
    if len(ents) < n_before:
        save_entities(user_key, ents)
        return True
    return False


# ─── 比對 helpers ──────────────────────────────────────────


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).strip()


def is_own_entity(value: str, entities: list[dict]) -> Optional[dict]:
    """檢查 value 是否屬於 user 已登錄的我方實體。
    比對：完全相符 / 子字串 / aliases 命中。
    回 matched entity 或 None。
    """
    if not value:
        return None
    v = _norm(value)
    for e in entities:
        names = [e.get("name", "")] + (e.get("aliases") or [])
        for n in names:
            n_norm = _norm(n)
            if not n_norm:
                continue
            if v == n_norm:
                return e
            if v in n_norm or n_norm in v:
                return e
    return None


def detect_typo_in_own(value: str, entities: list[dict]) -> Optional[dict]:
    """偵測打錯版本：value 跟我方某 entity「相似但不完全相符」的情況。
    用 Levenshtein-ish 簡易判斷。回 {entity, distance, suggestion}。
    """
    if not value:
        return None
    v = _norm(value)
    if len(v) < 4:
        return None
    best = None
    best_dist = 999
    for e in entities:
        names = [e.get("name", "")] + (e.get("aliases") or [])
        for n in names:
            n_norm = _norm(n)
            if not n_norm:
                continue
            # 跳過完全相符或子字串相符（這由 is_own_entity 處理）
            if v == n_norm or v in n_norm or n_norm in v:
                return None  # 是同一個，不算打錯
            # 計算簡單相似度
            d = _edit_distance(v, n_norm)
            if d < best_dist:
                best_dist = d
                best = (e, n)
    # 容忍：差距 < 3 視為打錯（中文一字差也算）
    if best and best_dist <= 3 and best_dist < min(len(v), len(_norm(best[1]))) * 0.4:
        return {"entity": best[0], "matched_alias": best[1], "distance": best_dist,
                "input": value}
    return None


def _edit_distance(a: str, b: str) -> int:
    """簡單 Levenshtein（小字串夠用）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if abs(len(a) - len(b)) > 5:
        return 999  # 差太多直接拒
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            ins = curr[j-1] + 1
            dl = prev[j] + 1
            sub = prev[j-1] + (0 if ca == cb else 1)
            curr[j] = min(ins, dl, sub)
        prev = curr
    return prev[-1]
