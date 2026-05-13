"""統編資料庫 — SQLite-based 反查 (商業 / 公司統編 → 名稱 / 地址 / 負責人).

設計：
- 路徑：`<data_dir>/vat_db.sqlite`（獨立檔，不混 auth/audit）
- Schema：
    vat_registry (vat PK, name, address, owner, org_type, status, raw)
    vat_meta (key PK, value)  -- last_updated / source / record_count / source_url
- Ingest 流程：
    1. 解析 CSV (handle Big5/UTF-8/UTF-8 BOM 三種編碼)
    2. 寫進 'staging' 表
    3. atomic swap: rename staging → vat_registry，舊表 drop
    避免長時間重建造成 lookup 中斷
- Lookup：lookup_vat(vat) → dict 或 None；O(1)（vat 是 PRIMARY KEY）
- 備援 URLs：每次 update 試多個 source 直到成功

CSV schema 變動時的彈性：
- _COLUMN_ALIASES 把常見的中文 / 英文 column header 都對到 canonical 欄位
- 缺欄位用 None / "" 填，不會炸
"""
from __future__ import annotations

import csv
import io
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..config import settings as app_settings

_DB_NAME = "vat_db.sqlite"
_PROGRESS_NAME = "vat_db_progress.json"
_BATCH_SIZE = 5000
# Progress file stale 後（沒在 progress.updated_at 後這麼久才被讀），視為失效。
# 用來判斷 download_and_ingest_all() crash 後遺留的舊檔。
_PROGRESS_STALE_SEC = 30 * 60

# DB 操作 lock — staging swap 期間擋並行 ingest
_ingest_lock = threading.Lock()
# Lookup 快取（per-process LRU 簡版）
_lookup_cache: dict[str, Optional[dict]] = {}
_LOOKUP_CACHE_MAX = 5000


# ─── 路徑 / 連線 ─────────────────────────────────────────────────────

def _db_path() -> Path:
    return Path(app_settings.data_dir) / _DB_NAME


def _progress_path() -> Path:
    return Path(app_settings.data_dir) / _PROGRESS_NAME


# Progress 寫入 lock — atomic JSON 寫法（tmp + rename）+ 內部更新
# 同時序列化，避免 stage / bytes 欄位不一致。
_progress_lock = threading.Lock()


def _write_progress(**fields) -> None:
    """更新 progress JSON（atomic 寫）。fields 會 merge 進現有 state。
    每次寫入自動更新 updated_at。任何錯誤 swallow — progress 不該擋住主流程。"""
    try:
        with _progress_lock:
            p = _progress_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            current = {}
            try:
                current = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                current = {}
            current.update(fields)
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            tmp = p.with_suffix(p.suffix + f".tmp.{secrets.token_hex(4)}")
            tmp.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
    except Exception:
        pass


def _reset_progress(stage: str = "starting") -> None:
    """全量覆寫 progress（用於下載開始）。"""
    try:
        with _progress_lock:
            p = _progress_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + f".tmp.{secrets.token_hex(4)}")
            tmp.write_text(json.dumps({
                "stage": stage,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
    except Exception:
        pass


def read_progress() -> dict:
    """讀目前 progress；不存在 / 過期 → 回 {stage: 'idle'}。"""
    p = _progress_path()
    if not p.exists():
        return {"stage": "idle"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"stage": "idle"}
    # 過期（沒在更新 stage in [downloading|parsing|writing] 還超過 stale 時限）
    # 把 stale 視為 idle，避免上次 crash 殘留誤導
    ts = data.get("updated_at", "")
    try:
        last = datetime.fromisoformat(ts)
        # 與 now 比較需 tz aware
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last).total_seconds()
        if age > _PROGRESS_STALE_SEC and data.get("stage") not in ("done", "error", "idle"):
            return {"stage": "idle", "stale": True}
    except Exception:
        pass
    return data


def _connect() -> sqlite3.Connection:
    """每次取一個新連線（SQLite 用 thread-local，避免跨 thread 共用 cursor）。"""
    conn = sqlite3.connect(str(_db_path()), isolation_level=None, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS vat_registry (
      vat        TEXT PRIMARY KEY,
      name       TEXT NOT NULL,
      address    TEXT,
      owner      TEXT,
      org_type   TEXT,
      status     TEXT,
      raw        TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_vat_name ON vat_registry(name);

    CREATE TABLE IF NOT EXISTS vat_meta (
      key   TEXT PRIMARY KEY,
      value TEXT
    );
    """)
    # Migration: add category + industries columns to existing tables (idempotent)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(vat_registry)").fetchall()}
        if "category" not in cols:
            conn.execute("ALTER TABLE vat_registry ADD COLUMN category TEXT")
        if "industries" not in cols:
            conn.execute("ALTER TABLE vat_registry ADD COLUMN industries TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_vat_category ON vat_registry(category)")
    except Exception:
        pass


# 來源 → 類別 mapping (中文 label，給 admin UI 顯示用)
CATEGORY_MAIN = "企業"
CATEGORY_SUPPLEMENTS = {
    # 對應 SUPPLEMENT_URLS 內每個 name 前綴
    "行政院所屬各機關統編": "中央政府機關",
    "地方政府各機關統編": "地方政府機關",
    "全國各級學校統編": "學校",
}


def _category_for_source(source_name: str) -> str:
    """從 source name 推斷 category。沒對應上回「未分類」。"""
    if not source_name:
        return "未分類"
    for prefix, cat in CATEGORY_SUPPLEMENTS.items():
        if source_name.startswith(prefix):
            return cat
    return CATEGORY_MAIN


def init_db() -> None:
    """確保 schema 存在 — 在 app 啟動或第一次操作時呼叫。"""
    Path(app_settings.data_dir).mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        _init_schema(conn)
    finally:
        conn.close()


# ─── CSV 欄位 alias ─────────────────────────────────────────────────

# Canonical column → 可能出現的 CSV header（中英都接）
_COLUMN_ALIASES = {
    "vat": [
        "統一編號", "統編", "公司統編", "vat", "VAT", "Business ID",
        "Business_Accounting_NO", "Statement_Number",
    ],
    "name": [
        "營業人名稱", "商業名稱", "公司名稱", "name", "Business_Name",
        "Company_Name",
        # 補充來源用詞
        "機關單位名稱",   # 行政院 / 地方政府機關 CSV
        "單位名稱",       # 學校 CSV (BGMOPEN99X)
        "機關名稱",
    ],
    "address": [
        "營業地址", "營業所在地", "地址", "公司所在地", "address",
        "Business_Address", "Company_Address", "Company_Location",
        # 學校 CSV 只給縣市，當地址用比沒有強
        "機關所在縣市",
    ],
    "owner": [
        "負責人姓名", "負責人", "代表人姓名", "代表人", "Owner_Name",
        "Responsible_Name",
    ],
    "org_type": [
        "組織別名稱", "組織別", "Organization_Type",
    ],
    "status": [
        "營業狀況", "狀態", "公司狀況", "Status",
    ],
}


def _build_header_map(headers: list[str]) -> dict[str, int]:
    """從 CSV header list 找出 canonical 欄位對應的 index；找不到的 = -1。"""
    out: dict[str, int] = {}
    norm = [h.strip() for h in headers]
    for canonical, aliases in _COLUMN_ALIASES.items():
        idx = -1
        for alias in aliases:
            for i, h in enumerate(norm):
                if h == alias or h.lower() == alias.lower():
                    idx = i
                    break
            if idx >= 0:
                break
        out[canonical] = idx
    return out


def _find_industry_indices(headers: list[str]) -> list[int]:
    """BGMOPEN 主檔 header pattern：
       ...,行業代號,名稱,行業代號1,名稱1,行業代號2,名稱2,行業代號3,名稱3
    每個「行業代號X」後面緊跟著「名稱X」（X 可空 / 1 / 2 / 3）。
    回傳所有「名稱X」對應的 index list。
    補充來源（行政院 / 地方政府 / 學校）的「機關單位名稱」/「單位名稱」不會被誤認，
    因為這個 helper 只配對「行業代號N」之後緊跟著的「名稱N」。
    """
    norm = [h.strip() for h in headers]
    out = []
    for i, h in enumerate(norm):
        if h.startswith("行業代號") and i + 1 < len(norm):
            next_h = norm[i + 1]
            # 後綴必須相同（行業代號 ↔ 名稱 / 行業代號1 ↔ 名稱1）
            suffix = h[len("行業代號"):]
            expected = "名稱" + suffix
            if next_h == expected:
                out.append(i + 1)
    return out


# ─── CSV 解析 ───────────────────────────────────────────────────────

def _decode_csv_bytes(data: bytes) -> str:
    """偵測編碼：BOM / UTF-8 / Big5 三種優先序。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # 政府開放資料常見 Big5
        return data.decode("big5", errors="replace")


def parse_csv_to_records(data: bytes) -> Iterable[dict]:
    """yield dict per row — keys: vat / name / address / owner / org_type /
    status / industries / raw."""
    text = _decode_csv_bytes(data)
    # 用 io.StringIO 而非 splitlines() — csv module 處理 quoted multi-line cell 比較穩
    reader = csv.reader(io.StringIO(text))
    headers = None
    header_map = None
    industry_idxs: list[int] = []
    for row in reader:
        if not row:
            continue
        if headers is None:
            headers = row
            header_map = _build_header_map(headers)
            industry_idxs = _find_industry_indices(headers)
            if header_map["vat"] < 0 or header_map["name"] < 0:
                raise ValueError(
                    f"CSV header 找不到「統一編號」或「名稱」欄位。"
                    f"偵測到的 headers：{headers[:6]}"
                )
            continue

        # 跳過明顯無效列
        if header_map["vat"] >= len(row):
            continue
        vat = (row[header_map["vat"]] or "").strip()
        if not vat or len(vat) != 8 or not vat.isdigit():
            continue
        name = (row[header_map["name"]] if header_map["name"] < len(row) else "").strip()
        if not name:
            continue

        def _get(canonical: str) -> str:
            i = header_map.get(canonical, -1)
            if i < 0 or i >= len(row):
                return ""
            return (row[i] or "").strip()

        # 抽取所有非空行業名稱，去重保留順序
        industries = []
        seen_ind = set()
        for i in industry_idxs:
            if i < len(row):
                v = (row[i] or "").strip()
                if v and v not in seen_ind:
                    industries.append(v)
                    seen_ind.add(v)
        industries_str = " / ".join(industries) if industries else None

        yield {
            "vat": vat,
            "name": name,
            "address": _get("address") or None,
            "owner": _get("owner") or None,
            "org_type": _get("org_type") or None,
            "status": _get("status") or None,
            "industries": industries_str,
            "raw": None,  # 不存原始 row（節省空間）
        }


# ─── Ingest ─────────────────────────────────────────────────────────

def ingest_csv(data: bytes, source: str = "manual_upload",
               category: str = None) -> dict:
    """匯入 CSV bytes 到 vat_registry — 採 staging swap 模式避免中斷 lookup。

    category: 標註該批資料的分類（企業 / 中央政府機關 / 地方政府機關 / 學校）。
        None 時依 source 名稱自動推斷。

    Returns dict: {records: int, source: str, last_updated: str}
    Raises: ValueError (bad CSV format) 或 OSError (DB 無法寫)
    """
    if category is None:
        category = _category_for_source(source)
    init_db()
    with _ingest_lock:
        conn = _connect()
        try:
            # 1. 創 staging 表 (含 category 欄位)
            conn.execute("DROP TABLE IF EXISTS vat_registry_staging")
            conn.execute("""
                CREATE TABLE vat_registry_staging (
                  vat TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  address TEXT,
                  owner TEXT,
                  org_type TEXT,
                  status TEXT,
                  raw TEXT,
                  category TEXT,
                  industries TEXT
                )
            """)

            # 2. 批次寫入
            count = 0
            batch = []
            cur = conn.cursor()
            cur.execute("BEGIN")
            try:
                for rec in parse_csv_to_records(data):
                    batch.append((
                        rec["vat"], rec["name"], rec["address"],
                        rec["owner"], rec["org_type"], rec["status"], rec["raw"],
                        category, rec.get("industries"),
                    ))
                    if len(batch) >= _BATCH_SIZE:
                        cur.executemany(
                            "INSERT OR REPLACE INTO vat_registry_staging "
                            "(vat, name, address, owner, org_type, status, raw, category, industries) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            batch,
                        )
                        count += len(batch)
                        batch.clear()
                if batch:
                    cur.executemany(
                        "INSERT OR REPLACE INTO vat_registry_staging "
                        "(vat, name, address, owner, org_type, status, raw, category, industries) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                    count += len(batch)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                conn.execute("DROP TABLE IF EXISTS vat_registry_staging")
                raise

            if count == 0:
                conn.execute("DROP TABLE IF EXISTS vat_registry_staging")
                raise ValueError("CSV 解析後沒有任何有效資料")

            # 3. Atomic swap
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("DROP TABLE IF EXISTS vat_registry")
            conn.execute("ALTER TABLE vat_registry_staging RENAME TO vat_registry")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_vat_name ON vat_registry(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_vat_category ON vat_registry(category)")
            conn.execute(
                "INSERT OR REPLACE INTO vat_meta (key, value) VALUES (?, ?)",
                ("last_updated", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO vat_meta (key, value) VALUES (?, ?)",
                ("source", source),
            )
            conn.execute(
                "INSERT OR REPLACE INTO vat_meta (key, value) VALUES (?, ?)",
                ("record_count", str(count)),
            )

            # 4. Invalidate cache
            _lookup_cache.clear()
            return {
                "records": count,
                "source": source,
                "last_updated": now,
            }
        finally:
            conn.close()


def clear_db() -> None:
    """刪除所有資料 + meta（保留 schema）。"""
    init_db()
    conn = _connect()
    try:
        conn.execute("DELETE FROM vat_registry")
        conn.execute("DELETE FROM vat_meta")
        _lookup_cache.clear()
    finally:
        conn.close()


# ─── Lookup ────────────────────────────────────────────────────────

def lookup_vat(vat: str) -> Optional[dict]:
    """O(1) 反查 — 找不到回 None。"""
    if not vat or not isinstance(vat, str):
        return None
    vat = vat.strip()
    if len(vat) != 8 or not vat.isdigit():
        return None

    if vat in _lookup_cache:
        return _lookup_cache[vat]

    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT vat, name, address, owner, org_type, status, category, industries "
            "FROM vat_registry WHERE vat = ?",
            (vat,),
        ).fetchone()
        result = None
        if row:
            result = {
                "vat": row[0], "name": row[1], "address": row[2],
                "owner": row[3], "org_type": row[4], "status": row[5],
                "category": row[6], "industries": row[7],
            }
        # Cache (含 None 結果，避免重打 DB)
        if len(_lookup_cache) >= _LOOKUP_CACHE_MAX:
            # Drop ~10% 簡單 LRU 替換
            for k in list(_lookup_cache.keys())[:_LOOKUP_CACHE_MAX // 10]:
                _lookup_cache.pop(k, None)
        _lookup_cache[vat] = result
        return result
    finally:
        conn.close()


def search_companies(query: str, field: str = "any", limit: int = 50,
                     categories: Optional[list[str]] = None) -> list[dict]:
    """模糊搜尋。query 用 SQL LIKE %query%。
    field: 'name' | 'address' | 'owner' | 'industries' | 'any'（搜全部）
    categories: 篩選類別 list（例 ['企業', '學校']）；None / 空 = 全部不篩
    limit: 1 ~ 500
    回傳 list of dict，最多 limit 筆。"""
    if not query or not isinstance(query, str):
        return []
    q = query.strip()
    if len(q) < 2:
        return []
    limit = max(1, min(int(limit) if isinstance(limit, int) else 50, 500))
    q_esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{q_esc}%"
    init_db()
    conn = _connect()
    try:
        valid_fields = {"name", "address", "owner", "industries"}
        if field == "any":
            field_where = ("(name LIKE ? ESCAPE '\\' OR address LIKE ? ESCAPE '\\' "
                           "OR owner LIKE ? ESCAPE '\\' OR industries LIKE ? ESCAPE '\\')")
            params: list = [pattern, pattern, pattern, pattern]
        elif field in valid_fields:
            field_where = f"{field} LIKE ? ESCAPE '\\'"
            params = [pattern]
        else:
            raise ValueError(f"未知欄位：{field}")

        # 類別篩選（IN clause）
        cat_where = ""
        if categories:
            if not isinstance(categories, list):
                raise ValueError("categories 必須是 list")
            cats = [c for c in categories if isinstance(c, str) and c]
            if cats:
                placeholders = ",".join("?" * len(cats))
                cat_where = f" AND category IN ({placeholders})"
                params.extend(cats)

        params.append(limit)
        rows = conn.execute(
            "SELECT vat, name, address, owner, org_type, status, category, industries "
            f"FROM vat_registry WHERE {field_where}{cat_where} "
            "ORDER BY name LIMIT ?",
            params,
        ).fetchall()
        return [{
            "vat": r[0], "name": r[1], "address": r[2],
            "owner": r[3], "org_type": r[4], "status": r[5],
            "category": r[6], "industries": r[7],
        } for r in rows]
    finally:
        conn.close()


def get_meta() -> dict:
    """回 last_updated / record_count / source 等資訊（給 admin 頁顯示）。"""
    init_db()
    conn = _connect()
    try:
        meta = {row[0]: row[1] for row in
                conn.execute("SELECT key, value FROM vat_meta").fetchall()}
        # record_count 從 meta 取（ingest 時寫的）；若沒有就 COUNT
        if "record_count" not in meta:
            n = conn.execute("SELECT COUNT(*) FROM vat_registry").fetchone()[0]
            meta["record_count"] = str(n)
        last_result = None
        try:
            if meta.get("last_result_json"):
                last_result = json.loads(meta["last_result_json"])
        except Exception:
            pass
        return {
            "last_updated": meta.get("last_updated", ""),
            "source": meta.get("source", ""),
            "source_url": meta.get("source_url", ""),
            "record_count": int(meta.get("record_count", "0") or 0),
            "last_result": last_result,
        }
    finally:
        conn.close()


# ─── 排程設定 + 自動排程器 ────────────────────────────────────────────

_DEFAULT_SCHEDULE = {
    "enabled": False,
    "weekday": 6,    # Sunday (Python weekday(): 0=Mon, 6=Sun)
    "hour": 3,       # 03:00 (24h)
}


def get_schedule() -> dict:
    """讀目前排程設定 + 上次運行狀態。"""
    init_db()
    conn = _connect()
    try:
        rows = {r[0]: r[1] for r in conn.execute(
            "SELECT key, value FROM vat_meta WHERE key LIKE 'schedule_%'").fetchall()}
        return {
            "enabled": rows.get("schedule_enabled", "0") == "1",
            "weekday": int(rows.get("schedule_weekday", _DEFAULT_SCHEDULE["weekday"]) or 0),
            "hour": int(rows.get("schedule_hour", _DEFAULT_SCHEDULE["hour"]) or 0),
            "last_run_at": rows.get("schedule_last_run_at", ""),
            "last_run_status": rows.get("schedule_last_run_status", ""),
            "last_run_error": rows.get("schedule_last_run_error", ""),
        }
    finally:
        conn.close()


def set_schedule(enabled: bool, weekday: int, hour: int) -> None:
    """更新排程設定。weekday 0-6 (週一=0)，hour 0-23。"""
    if not isinstance(weekday, int) or not (0 <= weekday <= 6):
        raise ValueError("weekday 必須在 0-6 (週一=0)")
    if not isinstance(hour, int) or not (0 <= hour <= 23):
        raise ValueError("hour 必須在 0-23")
    init_db()
    conn = _connect()
    try:
        for k, v in [("schedule_enabled", "1" if enabled else "0"),
                     ("schedule_weekday", str(weekday)),
                     ("schedule_hour", str(hour))]:
            conn.execute(
                "INSERT OR REPLACE INTO vat_meta (key, value) VALUES (?, ?)",
                (k, v),
            )
    finally:
        conn.close()


def _record_schedule_run(status: str, error: str = "") -> None:
    """記錄排程運行狀態到 vat_meta（給 admin UI 顯示用）。"""
    init_db()
    conn = _connect()
    try:
        ts = datetime.now(timezone.utc).isoformat()
        # 只在 running 開始 / 結束時更新 last_run_at；error 保留訊息便於故障排除
        if status == "running":
            conn.execute(
                "INSERT OR REPLACE INTO vat_meta (key, value) VALUES (?, ?)",
                ("schedule_last_run_started_at", ts),
            )
        for k, v in [
            ("schedule_last_run_at", ts),
            ("schedule_last_run_status", status),
            ("schedule_last_run_error", error or ""),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO vat_meta (key, value) VALUES (?, ?)",
                (k, v),
            )
    finally:
        conn.close()


_scheduler_thread: Optional[threading.Thread] = None
_scheduler_stop = threading.Event()
_scheduler_lock = threading.Lock()

# 手動 / 排程觸發下載共用的執行緒，避免同時跑兩個重複下載。
# 跑 download_and_ingest_all 約 5-30 分鐘，絕不可阻塞 event loop。
_download_thread: Optional[threading.Thread] = None
_download_lock = threading.Lock()


def is_download_running() -> bool:
    """目前是否有下載任務正在跑（手動 or 排程）。"""
    with _download_lock:
        return _download_thread is not None and _download_thread.is_alive()


def trigger_download_async() -> str:
    """啟動背景下載 thread。若已在跑回 'already_running'，否則 'started'。
    Web endpoint 用這個，不會阻塞 event loop。"""
    global _download_thread
    with _download_lock:
        if _download_thread is not None and _download_thread.is_alive():
            return "already_running"
        _download_thread = threading.Thread(
            target=_run_download_safe,
            name="vat-db-download", daemon=True,
        )
        _download_thread.start()
        return "started"


def _run_download_safe() -> None:
    """Thread target — 包 download_and_ingest_all 不讓 exception 漏出 thread。
    錯誤透過 _write_progress(stage='error') 通報前端。"""
    import logging
    try:
        download_and_ingest_all()
    except Exception:
        logging.getLogger("vat_db.download").exception(
            "download_and_ingest_all in background thread crashed")
_SCHED_TICK_SEC = 300  # 每 5 分鐘 check 一次（hour-precision 觸發只要 < 1 hr 即可）


def _should_run_now(sch: dict) -> bool:
    """判斷現在是不是該觸發排程下載。
    - 必須 enabled
    - 必須是 schedule_weekday（local time）
    - now.hour 必須 >= schedule_hour
    - 距離上次成功（或開始）運行至少 6 天（避免同週重複跑）
    """
    if not sch.get("enabled"):
        return False
    now = datetime.now()
    if now.weekday() != sch.get("weekday"):
        return False
    if now.hour < sch.get("hour", 99):
        return False
    last_run = sch.get("last_run_at", "")
    if last_run:
        try:
            last_dt = datetime.fromisoformat(last_run)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            age_sec = (now_utc - last_dt).total_seconds()
            if age_sec < 6 * 86400:
                return False  # 還沒超過 6 天，避免同週重複觸發
        except Exception:
            pass
    return True


def _scheduler_loop() -> None:
    import logging
    log = logging.getLogger("vat_db.scheduler")
    log.info("vat-db scheduler started (tick interval: %s sec)", _SCHED_TICK_SEC)
    while not _scheduler_stop.is_set():
        try:
            sch = get_schedule()
            if _should_run_now(sch):
                log.info("vat-db scheduler triggering weekly download "
                         "(weekday=%s hour=%s)", sch["weekday"], sch["hour"])
                _record_schedule_run("running")
                try:
                    result = download_and_ingest_all()
                    _record_schedule_run("ok")
                    log.info("vat-db scheduled download OK: %s records total",
                             result.get("records", 0))
                except Exception as e:
                    _record_schedule_run("error", error=str(e))
                    log.exception("vat-db scheduled download failed")
        except Exception:
            log.exception("vat-db scheduler tick failed")
        if _scheduler_stop.wait(_SCHED_TICK_SEC):
            break
    log.info("vat-db scheduler stopped")


def start_scheduler() -> None:
    """啟動 vat-db 排程 thread (在 app startup 呼叫)。"""
    global _scheduler_thread
    with _scheduler_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return
        _scheduler_stop.clear()
        _scheduler_thread = threading.Thread(
            target=_scheduler_loop, name="vat-db-scheduler", daemon=True,
        )
        _scheduler_thread.start()


def stop_scheduler() -> None:
    _scheduler_stop.set()
    if _scheduler_thread is not None:
        _scheduler_thread.join(timeout=5)


def get_category_stats() -> list[dict]:
    """回每個 category 的筆數（含 NULL → 未分類）。
    順序固定：企業 / 中央政府機關 / 地方政府機關 / 學校 / 未分類。
    UI 用來顯示「資料庫組成」。"""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT COALESCE(category, '未分類') AS c, COUNT(*) "
            "FROM vat_registry GROUP BY c"
        ).fetchall()
        counts = {r[0]: r[1] for r in rows}
        order = ["企業", "中央政府機關", "地方政府機關", "學校", "未分類"]
        out = []
        for cat in order:
            if cat in counts:
                out.append({"category": cat, "count": counts[cat]})
        # 沒列在 order 內的其他 category 加在尾
        for cat, n in counts.items():
            if cat not in order:
                out.append({"category": cat, "count": n})
        return out
    finally:
        conn.close()


def save_last_result(result: dict) -> None:
    """把 download_and_ingest_all() 的結果存到 vat_meta，給頁面永久顯示。"""
    init_db()
    conn = _connect()
    try:
        # 簡化欄位（不存原始 source_used dict 內整段）
        slim = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "records": result.get("records", 0),
            "main_records": result.get("main_records", 0),
            "main_source_name": (result.get("source_used") or {}).get("name", ""),
            "supplements": [
                {"name": s.get("name", ""), "added": s.get("added", 0),
                 "error": s.get("error", "")}
                for s in (result.get("supplements") or [])
            ],
        }
        conn.execute(
            "INSERT OR REPLACE INTO vat_meta (key, value) VALUES (?, ?)",
            ("last_result_json", json.dumps(slim, ensure_ascii=False)),
        )
    finally:
        conn.close()


# ─── Source URLs (備援列表) ──────────────────────────────────────────

# 主來源：data.gov.tw 9400「全國營業(稅籍)登記資料集」(BGMOPEN1)。
# 由財政部財政資訊中心每日更新，含 ~3M 筆營業人。zip 比 csv 小 ~4x，優先用。
# 注意：dataset id 9210 已被政府重新分配給「紅外線彩色衛星雲圖」，請勿使用。
SOURCE_URLS = [
    {
        "name": "全國營業(稅籍)登記資料集 BGMOPEN1 (zip)",
        "url": "https://eip.fia.gov.tw/data/BGMOPEN1.zip",
        "format": "zip",
        "encoding": "utf-8",
    },
    {
        "name": "全國營業(稅籍)登記資料集 BGMOPEN1 (csv)",
        "url": "https://eip.fia.gov.tw/data/BGMOPEN1.csv",
        "format": "csv",
        "encoding": "utf-8",
    },
]

# 補充來源：政府機關 + 學校統編，BGMOPEN 主資料集不含。
# 主檔匯入後再依序 download + INSERT OR IGNORE 補進 vat_registry，
# 不替換主檔；任何單一來源失敗只 warn 不中斷。
SUPPLEMENT_URLS = [
    {
        "name": "行政院所屬各機關統編 (44806)",
        "url": "https://www.fia.gov.tw/download/9bc4de1485014443b518beb37d8f35fe",
        "format": "csv",
        "encoding": "utf-8",
    },
    {
        "name": "地方政府各機關統編 (166161)",
        "url": "https://www.fia.gov.tw/download/2d35e0525c484964a84798baf39c72d2",
        "format": "csv",
        "encoding": "utf-8",
    },
    {
        "name": "全國各級學校統編 BGMOPEN99X (75136)",
        "url": "https://eip.fia.gov.tw/data/BGMOPEN99X.csv",
        "format": "csv",
        "encoding": "utf-8",
    },
]


def download_from_sources(sources: Optional[list[dict]] = None,
                          timeout_sec: int = 600,
                          progress_stage: str = "downloading_main") -> tuple[bytes, dict]:
    """依序試備援 URL，回 (raw_bytes, source_info)。
    使用 httpx.stream() 邊下載邊 emit progress (bytes_received / total)。

    raw_bytes 可能是 ZIP 或 CSV — 由 ingest_archive_or_csv() 自動判斷處理。
    """
    import httpx
    sources = sources or SOURCE_URLS
    last_err = None
    headers = {"User-Agent": "Mozilla/5.0 (jt-doc-tools vat-db)"}
    for src in sources:
        try:
            _write_progress(
                stage=progress_stage,
                source_name=src["name"],
                bytes_received=0,
                bytes_total=0,
            )
            with httpx.Client(
                timeout=timeout_sec, follow_redirects=True, headers=headers,
            ) as client:
                with client.stream("GET", src["url"]) as r:
                    if r.status_code != 200:
                        last_err = f"{src['name']}: HTTP {r.status_code}"
                        continue
                    total = int(r.headers.get("content-length", 0) or 0)
                    chunks = []
                    received = 0
                    last_emit = 0  # 上次 emit progress 時的 received
                    for chunk in r.iter_bytes(chunk_size=128 * 1024):
                        chunks.append(chunk)
                        received += len(chunk)
                        # 每 ~512KB 才 emit 一次，避免 IO 負擔
                        if received - last_emit >= 512 * 1024:
                            _write_progress(
                                stage=progress_stage,
                                source_name=src["name"],
                                bytes_received=received,
                                bytes_total=total,
                            )
                            last_emit = received
                    data = b"".join(chunks)
                    if not data:
                        last_err = f"{src['name']}: empty body"
                        continue
                    _write_progress(
                        stage=progress_stage,
                        source_name=src["name"],
                        bytes_received=received,
                        bytes_total=total or received,
                    )
                    return data, src
        except Exception as e:
            last_err = f"{src['name']}: {e}"
            continue
    raise RuntimeError(f"全部備援來源皆失敗。最後錯誤：{last_err}")


def append_csv(data: bytes, source: str, category: str = None) -> dict:
    """把 CSV 補進 vat_registry（INSERT OR IGNORE，不蓋主檔資料）。
    用於匯入政府機關 / 學校統編等 BGMOPEN 不含的補充來源。

    category: 標註該批資料分類；None 依 source 自動推斷。

    Returns: {added: int, source: str}
    """
    if category is None:
        category = _category_for_source(source)
    init_db()
    with _ingest_lock:
        conn = _connect()
        try:
            count = 0
            batch = []
            cur = conn.cursor()
            cur.execute("BEGIN")
            try:
                for rec in parse_csv_to_records(data):
                    batch.append((
                        rec["vat"], rec["name"], rec["address"],
                        rec["owner"], rec["org_type"], rec["status"], rec["raw"],
                        category, rec.get("industries"),
                    ))
                    if len(batch) >= _BATCH_SIZE:
                        cur.executemany(
                            "INSERT INTO vat_registry "
                            "(vat, name, address, owner, org_type, status, raw, category, industries) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(vat) DO UPDATE SET category = excluded.category",
                            batch,
                        )
                        count += len(batch)
                        batch.clear()
                if batch:
                    cur.executemany(
                        "INSERT INTO vat_registry "
                        "(vat, name, address, owner, org_type, status, raw, category, industries) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(vat) DO UPDATE SET category = excluded.category",
                        batch,
                    )
                    count += len(batch)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            _lookup_cache.clear()
            # 更新 record_count meta
            n = conn.execute("SELECT COUNT(*) FROM vat_registry").fetchone()[0]
            conn.execute(
                "INSERT OR REPLACE INTO vat_meta (key, value) VALUES (?, ?)",
                ("record_count", str(n)),
            )
            return {"added": count, "source": source, "total": n}
        finally:
            conn.close()


def download_and_ingest_all(timeout_sec: int = 600) -> dict:
    """完整流程：下載主檔 → ingest → 依序補充政府機關 / 學校。
    主檔失敗整個 abort；補充失敗只 warn 不阻擋。
    全程透過 _write_progress() emit 進度給 /admin/vat-db/progress endpoint。

    Returns: {records: int, source_used: dict, supplements: [{name, added, error?}, ...]}
    """
    _reset_progress("starting")
    try:
        main_bytes, main_src = download_from_sources(progress_stage="downloading_main")
        _write_progress(stage="parsing_main", source_name=main_src["name"])
        main_result = ingest_archive_or_csv(main_bytes, source=main_src["name"])

        supp_results = []
        n_supp = len(SUPPLEMENT_URLS)
        for idx, src in enumerate(SUPPLEMENT_URLS, start=1):
            try:
                _write_progress(
                    stage="downloading_supplement",
                    source_name=src["name"],
                    supplement_index=idx,
                    supplement_total=n_supp,
                    bytes_received=0, bytes_total=0,
                )
                import httpx
                headers = {"User-Agent": "Mozilla/5.0 (jt-doc-tools vat-db)"}
                with httpx.Client(
                    timeout=timeout_sec, follow_redirects=True, headers=headers,
                ) as client:
                    with client.stream("GET", src["url"]) as r:
                        if r.status_code != 200:
                            supp_results.append({
                                "name": src["name"], "added": 0,
                                "error": f"HTTP {r.status_code}",
                            })
                            continue
                        total = int(r.headers.get("content-length", 0) or 0)
                        chunks = []
                        received = 0
                        for chunk in r.iter_bytes(chunk_size=64 * 1024):
                            chunks.append(chunk)
                            received += len(chunk)
                            if total and received % (256 * 1024) < 65 * 1024:
                                _write_progress(
                                    stage="downloading_supplement",
                                    source_name=src["name"],
                                    supplement_index=idx,
                                    supplement_total=n_supp,
                                    bytes_received=received,
                                    bytes_total=total,
                                )
                        data = b"".join(chunks)
                if not data:
                    supp_results.append({
                        "name": src["name"], "added": 0, "error": "empty body",
                    })
                    continue
                _write_progress(
                    stage="parsing_supplement",
                    source_name=src["name"],
                    supplement_index=idx,
                    supplement_total=n_supp,
                )
                r2 = append_csv(data, source=src["name"])
                supp_results.append({"name": src["name"], "added": r2.get("added", 0)})
            except Exception as e:
                supp_results.append({"name": src["name"], "added": 0, "error": str(e)})

        # 重讀 meta 取最終 total
        final_meta = get_meta()
        result = {
            "records": final_meta["record_count"],
            "main_records": main_result["records"],
            "source_used": main_src,
            "supplements": supp_results,
        }
        _write_progress(stage="done", **{
            "records": result["records"],
            "main_records": result["main_records"],
            "source_used_name": main_src["name"],
            "supplements_summary": [
                {"name": s["name"], "added": s.get("added", 0),
                 "error": s.get("error", "")}
                for s in supp_results
            ],
        })
        # 把結果存到 vat_meta，下次開頁面也看得到
        try:
            save_last_result(result)
        except Exception:
            pass
        return result
    except Exception as e:
        _write_progress(stage="error", error=str(e))
        raise


def ingest_archive_or_csv(data: bytes, source: str) -> dict:
    """自動判斷 ZIP / CSV 並 ingest。"""
    # ZIP magic number = PK\x03\x04
    if data.startswith(b"PK\x03\x04"):
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                raise ValueError("ZIP 檔案內找不到 .csv")
            # 取最大那個（通常主要資料）
            csv_names.sort(key=lambda n: -z.getinfo(n).file_size)
            csv_data = z.read(csv_names[0])
        return ingest_csv(csv_data, source=source)
    # 直接當 CSV
    return ingest_csv(data, source=source)
