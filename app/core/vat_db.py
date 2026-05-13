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
_BATCH_SIZE = 5000

# DB 操作 lock — staging swap 期間擋並行 ingest
_ingest_lock = threading.Lock()
# Lookup 快取（per-process LRU 簡版）
_lookup_cache: dict[str, Optional[dict]] = {}
_LOOKUP_CACHE_MAX = 5000


# ─── 路徑 / 連線 ─────────────────────────────────────────────────────

def _db_path() -> Path:
    return Path(app_settings.data_dir) / _DB_NAME


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
    ],
    "address": [
        "營業地址", "營業所在地", "地址", "公司所在地", "address",
        "Business_Address", "Company_Address", "Company_Location",
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
    """yield dict per row — keys: vat / name / address / owner / org_type / status / raw."""
    text = _decode_csv_bytes(data)
    # 用 io.StringIO 而非 splitlines() — csv module 處理 quoted multi-line cell 比較穩
    reader = csv.reader(io.StringIO(text))
    headers = None
    header_map = None
    for row in reader:
        if not row:
            continue
        if headers is None:
            headers = row
            header_map = _build_header_map(headers)
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

        yield {
            "vat": vat,
            "name": name,
            "address": _get("address") or None,
            "owner": _get("owner") or None,
            "org_type": _get("org_type") or None,
            "status": _get("status") or None,
            "raw": None,  # 不存原始 row（節省空間）
        }


# ─── Ingest ─────────────────────────────────────────────────────────

def ingest_csv(data: bytes, source: str = "manual_upload") -> dict:
    """匯入 CSV bytes 到 vat_registry — 採 staging swap 模式避免中斷 lookup。

    Returns dict: {records: int, source: str, last_updated: str}
    Raises: ValueError (bad CSV format) 或 OSError (DB 無法寫)
    """
    init_db()
    with _ingest_lock:
        conn = _connect()
        try:
            # 1. 創 staging 表
            conn.execute("DROP TABLE IF EXISTS vat_registry_staging")
            conn.execute("""
                CREATE TABLE vat_registry_staging (
                  vat TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  address TEXT,
                  owner TEXT,
                  org_type TEXT,
                  status TEXT,
                  raw TEXT
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
                    ))
                    if len(batch) >= _BATCH_SIZE:
                        cur.executemany(
                            "INSERT OR REPLACE INTO vat_registry_staging "
                            "(vat, name, address, owner, org_type, status, raw) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            batch,
                        )
                        count += len(batch)
                        batch.clear()
                if batch:
                    cur.executemany(
                        "INSERT OR REPLACE INTO vat_registry_staging "
                        "(vat, name, address, owner, org_type, status, raw) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
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
            "SELECT vat, name, address, owner, org_type, status "
            "FROM vat_registry WHERE vat = ?",
            (vat,),
        ).fetchone()
        result = None
        if row:
            result = {
                "vat": row[0], "name": row[1], "address": row[2],
                "owner": row[3], "org_type": row[4], "status": row[5],
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
        return {
            "last_updated": meta.get("last_updated", ""),
            "source": meta.get("source", ""),
            "source_url": meta.get("source_url", ""),
            "record_count": int(meta.get("record_count", "0") or 0),
        }
    finally:
        conn.close()


# ─── Source URLs (備援列表) ──────────────────────────────────────────

# 多備援：按順序試，第一個成功的用。實際 URL 變動時調整這個 list 即可。
# 這些是 2026 年前已知的官方 / 鏡像來源；請定期 verify。
SOURCE_URLS = [
    {
        "name": "財政部 BGMOPEN (zip)",
        "url": "https://service.mof.gov.tw/public/data/statistic/bas/BGMOPEN1.zip",
        "format": "zip",
        "encoding": "utf-8",
    },
    {
        "name": "財政部 BGMOPEN (csv)",
        "url": "https://service.mof.gov.tw/public/data/statistic/bas/BGMOPEN1.csv",
        "format": "csv",
        "encoding": "utf-8",
    },
    {
        "name": "data.gov.tw 9210 (BGMOPEN 鏡像)",
        "url": "https://data.gov.tw/dataset/9210/resource/dataset/9210/file",
        "format": "auto",
        "encoding": "utf-8",
    },
    {
        "name": "經濟部商業司公司登記 (9911)",
        "url": "https://data.gov.tw/dataset/9911/resource/dataset/9911/file",
        "format": "auto",
        "encoding": "utf-8",
    },
]


def download_from_sources(sources: Optional[list[dict]] = None,
                          timeout_sec: int = 600) -> tuple[bytes, dict]:
    """依序試備援 URL，回 (raw_bytes, source_info)。

    raw_bytes 可能是 ZIP 或 CSV — 由 ingest_archive_or_csv() 自動判斷處理。
    """
    import httpx
    sources = sources or SOURCE_URLS
    last_err = None
    for src in sources:
        try:
            with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
                r = client.get(src["url"])
                if r.status_code != 200 or not r.content:
                    last_err = f"{src['name']}: HTTP {r.status_code}"
                    continue
                return r.content, src
        except Exception as e:
            last_err = f"{src['name']}: {e}"
            continue
    raise RuntimeError(f"全部備援來源皆失敗。最後錯誤：{last_err}")


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
