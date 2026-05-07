"""主機資源 + 使用者檔案統計（admin 系統狀態頁用）。

設計原則：
- psutil 不在的話 → host_stats 回 `{"available": False, "error": "..."}`，
  admin 頁顯示 warning 不掛掉
- 所有 IO / network 數值都是 cumulative counter（自開機以來），UI 自己算
  delta（每次刷新算差除秒數 = rate）
- 使用者檔案統計：合併 temp/.owners 紀錄 + history 三個目錄的 meta.json
  username，一次回所有 user 的 (count, bytes)。**檔案多時 walk 很慢 →
  process-local cache TTL 60 秒**，UI 顯示「資料時間：xxx」讓 admin 知道。
  按「重新統計」按鈕走 force=True 重算。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional


def _try_psutil():
    try:
        import psutil
        return psutil
    except Exception:
        return None


def _safe_disk_usage(path: str) -> Optional[dict]:
    """psutil.disk_usage 在 Windows 拒絕存取的盤符會炸，包起來。"""
    psutil = _try_psutil()
    if not psutil:
        return None
    try:
        u = psutil.disk_usage(path)
        return {
            "path": path,
            "total": u.total,
            "used": u.used,
            "free": u.free,
            "percent": u.percent,
        }
    except Exception:
        return None


def get_host_stats() -> dict:
    """回主機資源 snapshot。psutil 缺失就回 available: False。"""
    psutil = _try_psutil()
    if not psutil:
        return {"available": False,
                "error": "psutil not installed — admin 頁無法顯示主機資源"}
    out: dict = {"available": True, "ts": time.time()}
    # CPU
    try:
        # interval=None → 自上次呼叫的平均（首次呼叫回 0.0），避免阻塞 1 秒
        out["cpu"] = {
            "percent": psutil.cpu_percent(interval=None),
            "count_logical": psutil.cpu_count(logical=True),
            "count_physical": psutil.cpu_count(logical=False),
        }
        try:
            la = psutil.getloadavg()
            out["cpu"]["loadavg"] = list(la)  # (1min, 5min, 15min)
        except (AttributeError, OSError):
            out["cpu"]["loadavg"] = None
    except Exception as e:
        out["cpu"] = {"error": str(e)}
    # RAM
    try:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        out["mem"] = {
            "total": mem.total, "used": mem.used, "available": mem.available,
            "percent": mem.percent,
            "swap_total": swap.total, "swap_used": swap.used, "swap_percent": swap.percent,
        }
    except Exception as e:
        out["mem"] = {"error": str(e)}
    # Disk — DATA_DIR 所在的盤 (最相關) + system root
    try:
        from ..config import settings as _s
        roots = []
        data_disk = _safe_disk_usage(str(_s.data_dir))
        if data_disk:
            data_disk["label"] = "資料目錄 (data)"
            roots.append(data_disk)
        # 系統根（Linux/macOS = /；Windows = C:\）
        sys_root = "C:\\" if os.name == "nt" else "/"
        if sys_root != str(_s.data_dir).split(os.sep)[0] + os.sep:
            sys_disk = _safe_disk_usage(sys_root)
            if sys_disk:
                sys_disk["label"] = f"系統 ({sys_root})"
                roots.append(sys_disk)
        out["disks"] = roots
    except Exception as e:
        out["disks"] = []
        out["disks_error"] = str(e)
    # Disk IO (cumulative counter — UI 算 delta)
    try:
        io = psutil.disk_io_counters()
        if io:
            out["disk_io"] = {"read_bytes": io.read_bytes,
                              "write_bytes": io.write_bytes,
                              "read_count": io.read_count,
                              "write_count": io.write_count}
    except Exception:
        out["disk_io"] = None
    # Network IO (cumulative)
    try:
        net = psutil.net_io_counters()
        out["net_io"] = {"bytes_sent": net.bytes_sent,
                         "bytes_recv": net.bytes_recv,
                         "packets_sent": net.packets_sent,
                         "packets_recv": net.packets_recv}
    except Exception:
        out["net_io"] = None
    # Process — 我們自己佔用多少
    try:
        proc = psutil.Process()
        with proc.oneshot():
            mi = proc.memory_info()
            out["self_proc"] = {
                "pid": proc.pid,
                "rss": mi.rss,
                "vms": mi.vms,
                "threads": proc.num_threads(),
                "cpu_percent": proc.cpu_percent(interval=None),
                "create_time": proc.create_time(),
            }
    except Exception:
        out["self_proc"] = None
    return out


def _dir_size(path: Path) -> tuple[int, int]:
    """回 (檔案數, 總 bytes)，遞迴。symlink 不跟。"""
    n, sz = 0, 0
    if not path.exists():
        return 0, 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    n += 1
                    sz += entry.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return n, sz


def _resolve_username_for_uid(user_id: int) -> str:
    """從 auth.sqlite 找 user_id → username。失敗回 `user#<id>`。"""
    try:
        from . import auth_db
        conn = auth_db.conn()
        row = conn.execute(
            "SELECT username FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        if row:
            return row["username"]
    except Exception:
        pass
    return f"user#{user_id}"


# --- per-user file stats cache --------------------------------------------
# 大量檔案（10 萬+）情況下 walk 一次可能要 30s+。Process-local cache 避免
# 每次 admin 刷新都重 walk。TTL 60 秒。force=True 強制重算。
_USER_STATS_LOCK = threading.RLock()
_USER_STATS_CACHE: Optional[dict] = None
_USER_STATS_CACHE_TTL = 60.0  # seconds
_USER_STATS_COMPUTING = False


def get_user_file_stats(force: bool = False) -> dict:
    """回所有使用者的檔案數 + 容量。

    來源三個地方：
    1. `data/temp/.owners/*.json` — upload_id 屬於哪個 user，sum 對應 temp 內檔
    2. `data/{fill,stamp,watermark}_history/<hid>/` — meta.json 含 username
    3. 公共 / 不可歸屬：剩下 temp / data 雜檔（admin 自己看就好）

    回 dict: {users: [{username, count, bytes}, ...], total_bytes, total_count,
             ts, age_seconds, computing}

    `force=True` 跳過 cache 立即重算（可能阻塞數秒到數十秒，視檔案數）。
    """
    global _USER_STATS_CACHE, _USER_STATS_COMPUTING
    now = time.time()
    if not force:
        with _USER_STATS_LOCK:
            if (_USER_STATS_CACHE is not None
                    and (now - _USER_STATS_CACHE["ts"]) < _USER_STATS_CACHE_TTL):
                # 回 cache + 標註資料年齡
                out = dict(_USER_STATS_CACHE)
                out["age_seconds"] = now - out["ts"]
                out["from_cache"] = True
                out["computing"] = _USER_STATS_COMPUTING
                return out
    # 計算新資料（force 或 cache 過期）
    with _USER_STATS_LOCK:
        _USER_STATS_COMPUTING = True
    try:
        result = _compute_user_file_stats()
    finally:
        with _USER_STATS_LOCK:
            _USER_STATS_COMPUTING = False
            _USER_STATS_CACHE = result
    result = dict(result)
    result["age_seconds"] = 0.0
    result["from_cache"] = False
    result["computing"] = False
    return result


def _compute_user_file_stats() -> dict:
    """實際 walk filesystem 並合併出每位 user 的 (count, bytes)。耗時操作；
    不要直接被 endpoint 呼叫，走上面 cached `get_user_file_stats()`。"""
    from ..config import settings as _s
    users_count: dict[str, int] = {}
    users_bytes: dict[str, int] = {}

    def _add(username: str, count: int, sz: int) -> None:
        users_count[username] = users_count.get(username, 0) + count
        users_bytes[username] = users_bytes.get(username, 0) + sz

    # --- 1) /temp/.owners/<upload_id>.json → 找該 upload 的所有檔
    temp_dir = _s.temp_dir
    owners_dir = temp_dir / ".owners"
    # 預先 build upload_id → file list 對映（避免 N²）— 不論 owners_dir 是否
    # 存在都建，方便算「未追蹤的孤兒檔案」總和
    files_by_uid: dict[str, list[Path]] = {}
    matched_files: set[Path] = set()  # 已歸屬到 user 的檔案，剩下是孤兒
    if temp_dir.exists():
        for f in temp_dir.iterdir():
            if not f.is_file():
                continue
            name = f.name
            # 多種 prefix pattern：直接 uuid_、att_uuid_、meta_uuid_、did_uuid_、
            # hid_uuid_、ext_text_uuid 子目錄、p2i_uuid_、annot_uuid_、
            # strip_uuid_、strip_api_uuid_、flat_uuid_、wc_uuid_、wm_uuid_、
            # nup_uuid_、pe_uuid_、merge_bid_、split_bid_ 等
            parts = name.split("_")
            for i, p in enumerate(parts):
                if len(p) == 32 and all(c in "0123456789abcdef" for c in p):
                    files_by_uid.setdefault(p, []).append(f)
                    break
        # ext_text_<uid>/ 是個目錄
        for d in temp_dir.iterdir():
            if d.is_dir() and d.name.startswith("ext_text_"):
                uid = d.name[len("ext_text_"):]
                if len(uid) == 32:
                    files_by_uid.setdefault(uid, []).append(d)
    if owners_dir.exists():
        for owner_file in owners_dir.iterdir():
            if not owner_file.is_file() or not owner_file.name.endswith(".json"):
                continue
            uid = owner_file.stem
            try:
                meta = json.loads(owner_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            user_id = meta.get("user_id")
            if user_id is None:
                continue
            username = _resolve_username_for_uid(int(user_id))
            count, sz = 0, 0
            for f in files_by_uid.get(uid, []):
                matched_files.add(f)
                if f.is_dir():
                    n2, s2 = _dir_size(f)
                    count += n2; sz += s2
                else:
                    try:
                        sz += f.stat().st_size
                        count += 1
                    except OSError:
                        pass
            if count or sz:
                _add(username, count, sz)
    # --- 1b) 孤兒暫存檔：沒對應 owner 紀錄（其他工具沒呼叫 record(), 或
    # 認證關閉時上傳的, 或 stamp_temp_/wm_temp_ 這種無歸屬資產）
    orphan_count, orphan_bytes = 0, 0
    if temp_dir.exists():
        for f in temp_dir.iterdir():
            if not f.is_file():
                continue
            if f.name == ".owners" or f.parent.name == ".owners":
                continue
            if f in matched_files:
                continue
            try:
                orphan_bytes += f.stat().st_size
                orphan_count += 1
            except OSError:
                pass
        # 也算 ext_text 目錄的孤兒（owner sidecar 過期但目錄還在）
        for d in temp_dir.iterdir():
            if d.is_dir() and d.name.startswith("ext_text_") and d not in matched_files:
                # 該 dir 是否被 matched_files 認養?
                # matched_files 只放 file 不放 dir — 用 uid 對照
                uid = d.name[len("ext_text_"):]
                if uid not in {oid for oid in (
                    f.stem for f in (owners_dir.iterdir() if owners_dir.exists() else [])
                    if f.suffix == ".json"
                )}:
                    n2, s2 = _dir_size(d)
                    orphan_count += n2; orphan_bytes += s2
    if orphan_count or orphan_bytes:
        _add("(未追蹤暫存)", orphan_count, orphan_bytes)

    # 抓近 30 天 audit 上傳活動量（即使 temp file 已清掉、history 沒記錄
    # 也能還原該 user 用過多少）。audit_events 預設保留 90 天，所以最多
    # 看到過去 90 天。size_bytes 從 details_json 抽。
    activity_30d_bytes: dict[str, int] = {}
    activity_30d_count: dict[str, int] = {}
    try:
        from . import audit_db
        cutoff_30d = time.time() - 30 * 86400
        conn = audit_db.conn()
        rows = conn.execute(
            "SELECT username, details_json FROM audit_events "
            "WHERE event_type='tool_invoke' AND ts > ?",
            (cutoff_30d,),
        ).fetchall()
        for row in rows:
            uname = (row["username"] or "").strip() or "(匿名)"
            try:
                d = json.loads(row["details_json"] or "{}")
                sz = int(d.get("size_bytes") or 0)
            except Exception:
                sz = 0
            if sz <= 0:
                continue
            activity_30d_bytes[uname] = activity_30d_bytes.get(uname, 0) + sz
            activity_30d_count[uname] = activity_30d_count.get(uname, 0) + 1
    except Exception:
        pass

    # --- 2) history dirs — meta.json 內 username 欄位
    for sub in ("fill_history", "stamp_history", "watermark_history"):
        d = _s.data_dir / sub
        if not d.exists():
            continue
        try:
            for entry_dir in d.iterdir():
                if not entry_dir.is_dir():
                    continue
                meta_p = entry_dir / "meta.json"
                username = "(匿名)"
                if meta_p.exists():
                    try:
                        meta = json.loads(meta_p.read_text(encoding="utf-8"))
                        u = (meta.get("username") or "").strip()
                        if u:
                            username = u
                    except Exception:
                        pass
                count, sz = _dir_size(entry_dir)
                if count or sz:
                    _add(username, count, sz)
        except OSError:
            pass

    # 排序：bytes 大的在前。同時併入 30 天 activity 資料，即使該 user 目前
    # 沒檔案佔用也會出現在表裡（admin 才看得到「他這個月上傳過多少」）
    all_users = set(users_bytes) | set(activity_30d_bytes)
    rows = sorted(
        [{"username": u,
          "count": users_count.get(u, 0),
          "bytes": users_bytes.get(u, 0),
          "activity_30d_count": activity_30d_count.get(u, 0),
          "activity_30d_bytes": activity_30d_bytes.get(u, 0)}
         for u in all_users],
        key=lambda r: -(r["bytes"] + r["activity_30d_bytes"]),
    )
    return {
        "users": rows,
        "total_bytes": sum(users_bytes.values()),
        "total_count": sum(users_count.values()),
        "activity_30d_total_bytes": sum(activity_30d_bytes.values()),
        "ts": time.time(),
    }
