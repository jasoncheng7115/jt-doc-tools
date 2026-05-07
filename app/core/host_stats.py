"""主機資源 + 使用者檔案統計（admin 系統狀態頁用）。

設計原則：
- psutil 不在的話 → host_stats 回 `{"available": False, "error": "..."}`，
  admin 頁顯示 warning 不掛掉
- 所有 IO / network 數值都是 cumulative counter（自開機以來），UI 自己算
  delta（每次刷新算差除秒數 = rate）
- 使用者檔案統計：合併 temp/.owners 紀錄 + history 三個目錄的 meta.json
  username，一次回所有 user 的 (count, bytes)
"""
from __future__ import annotations

import json
import os
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


def get_user_file_stats() -> dict:
    """回所有使用者的檔案數 + 容量。

    來源三個地方：
    1. `data/temp/.owners/*.json` — upload_id 屬於哪個 user，sum 對應 temp 內檔
    2. `data/{fill,stamp,watermark}_history/<hid>/` — meta.json 含 username
    3. 公共 / 不可歸屬：剩下 temp / data 雜檔（admin 自己看就好）

    回 dict: {users: [{username, count, bytes}, ...], total_bytes, ts}
    """
    from ..config import settings as _s
    users_count: dict[str, int] = {}
    users_bytes: dict[str, int] = {}

    def _add(username: str, count: int, sz: int) -> None:
        users_count[username] = users_count.get(username, 0) + count
        users_bytes[username] = users_bytes.get(username, 0) + sz

    # --- 1) /temp/.owners/<upload_id>.json → 找該 upload 的所有檔
    temp_dir = _s.temp_dir
    owners_dir = temp_dir / ".owners"
    if owners_dir.exists():
        # 預先 build upload_id → file list 對映（避免 N²）
        files_by_uid: dict[str, list[Path]] = {}
        for f in temp_dir.iterdir():
            if not f.is_file():
                continue
            name = f.name
            # 多種 prefix pattern：直接 uuid_、att_uuid_、meta_uuid_、did_uuid_、
            # hid_uuid_、ext_text_uuid 子目錄、p2i_uuid_、annot_uuid_、
            # strip_uuid_、strip_api_uuid_、flat_uuid_ 等
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
        # pdf-editor 用 "pe_" 前綴：pe_<uid>_src.pdf 等
        # （已被上面的 32-hex match 抓到，因為 pe 後面就是 uid）
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

    # 排序：bytes 大的在前
    rows = sorted(
        [{"username": u, "count": users_count[u], "bytes": users_bytes[u]}
         for u in users_bytes],
        key=lambda r: -r["bytes"],
    )
    return {
        "users": rows,
        "total_bytes": sum(users_bytes.values()),
        "total_count": sum(users_count.values()),
        "ts": time.time(),
    }
