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


# ---- 容器感知 CPU（LXC / Docker）------------------------------------------
# LXC 容器的 /proc/stat 與 /proc/loadavg 沒有 namespace 化，psutil 會讀到
# 「實體主機」的 CPU% 與 load average，導致系統狀態頁顯示的是整台主機的負載，
# 不是這個容器自己的。改從 cgroup 讀容器『自己』的 CPU 用量。
# （記憶體由 LXCFS 虛擬化 /proc/meminfo 已正確、磁碟是容器自己的 rootfs，
#   網路是 namespaced 介面，都不需特別處理。）
_CG_CPU_PREV: dict = {"usage_usec": None, "ts": None}
_IS_CONTAINER_CACHE: Optional[bool] = None


def _is_container() -> bool:
    """偵測是否在 LXC / Docker / podman 容器內。VM 與實機回 False。

    重點：服務以非 root 帳號（jtdt）執行，``/proc/1/environ`` 只有 root 能讀，
    不能只靠它。改用非 root 也讀得到的訊號：docker/podman 標記檔、/proc/mounts
    內的 LXCFS 掛載、以及 ``systemd-detect-virt --container``。結果固定不變 → 快取。"""
    global _IS_CONTAINER_CACHE
    if _IS_CONTAINER_CACHE is not None:
        return _IS_CONTAINER_CACHE
    result = False
    # docker / podman 標記檔（世界可讀）
    if os.path.exists("/run/.containerenv") or os.path.exists("/.dockerenv"):
        result = True
    # LXC：LXCFS 會把 /proc/* 掛成 fuse.lxcfs；/proc/mounts 非 root 可讀
    if not result:
        try:
            with open("/proc/mounts", encoding="utf-8") as f:
                if "lxcfs" in f.read():
                    result = True
        except Exception:
            pass
    # systemd-detect-virt：非 root 也能判斷容器（lxc / docker / nspawn 等）
    if not result:
        try:
            import subprocess
            r = subprocess.run(
                ["systemd-detect-virt", "--container", "--quiet"],
                timeout=2, capture_output=True)
            if r.returncode == 0:
                result = True
        except Exception:
            pass
    # /proc/1/environ container=（需 root，有就當補強）
    if not result:
        try:
            with open("/proc/1/environ", "rb") as f:
                if b"container=" in f.read():
                    result = True
        except Exception:
            pass
    _IS_CONTAINER_CACHE = result
    return result


def _read_cgroup_cpu_usage_usec() -> Optional[int]:
    """容器自開機以來累計 CPU 時間（微秒）。cgroup v2 優先，v1 fallback。"""
    try:  # cgroup v2
        with open("/sys/fs/cgroup/cpu.stat", encoding="utf-8") as f:
            for line in f:
                if line.startswith("usage_usec"):
                    return int(line.split()[1])
    except Exception:
        pass
    try:  # cgroup v1（ns → us）
        with open("/sys/fs/cgroup/cpuacct/cpuacct.usage", encoding="utf-8") as f:
            return int(f.read().strip()) // 1000
    except Exception:
        pass
    return None


def _count_cpuset(spec: str) -> int:
    """'0-3,5' → 5。算 cpuset 列表的核心數。"""
    n = 0
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                n += int(b) - int(a) + 1
            except ValueError:
                pass
        else:
            n += 1
    return n


def _cgroup_ncpu(fallback: Optional[int]) -> int:
    """容器可用的核心數：cpu.max 配額優先，其次 cpuset，最後 fallback。"""
    try:  # cgroup v2 cpu.max: "<quota> <period>" 或 "max <period>"
        with open("/sys/fs/cgroup/cpu.max", encoding="utf-8") as f:
            parts = f.read().split()
        if len(parts) == 2 and parts[0] != "max":
            q, p = int(parts[0]), int(parts[1])
            if p > 0:
                return max(1, round(q / p))
    except Exception:
        pass
    for path in ("/sys/fs/cgroup/cpuset.cpus.effective",
                 "/sys/fs/cgroup/cpuset/cpuset.effective_cpus"):
        try:
            with open(path, encoding="utf-8") as f:
                c = _count_cpuset(f.read().strip())
                if c:
                    return c
        except Exception:
            pass
    return fallback or 1


def _container_cpu_percent(fallback_ncpu: Optional[int]) -> Optional[float]:
    """以 cgroup 累計用量算容器自己的 CPU%（占可用核心的百分比）。
    首次呼叫無前一筆樣本 → 回 0.0（與 psutil interval=None 行為一致）。"""
    usage = _read_cgroup_cpu_usage_usec()
    now = time.time()
    prev = _CG_CPU_PREV
    pct: Optional[float] = 0.0
    if usage is not None and prev["usage_usec"] is not None and prev["ts"] is not None:
        dt_us = (now - prev["ts"]) * 1_000_000.0
        du = usage - prev["usage_usec"]
        if dt_us > 0 and du >= 0:
            cores_used = du / dt_us
            ncpu = _cgroup_ncpu(fallback_ncpu) or 1
            pct = max(0.0, min(100.0, cores_used / ncpu * 100.0))
    if usage is not None:
        _CG_CPU_PREV["usage_usec"] = usage
        _CG_CPU_PREV["ts"] = now
    return pct if usage is not None else None


def _container_disk_io() -> Optional[dict]:
    """容器自己的 block I/O 累計量（cgroup）。容器內 psutil 讀 /proc/diskstats
    是實體主機的，必須改讀 cgroup io.stat（v2）/ blkio（v1）。"""
    try:  # cgroup v2 io.stat：每行 "maj:min rbytes=.. wbytes=.. rios=.. wios=.."
        rb = wb = rios = wios = 0
        found = False
        with open("/sys/fs/cgroup/io.stat", encoding="utf-8") as f:
            for line in f:
                for tok in line.split()[1:]:
                    if tok.startswith("rbytes="):
                        rb += int(tok[7:]); found = True
                    elif tok.startswith("wbytes="):
                        wb += int(tok[7:])
                    elif tok.startswith("rios="):
                        rios += int(tok[5:])
                    elif tok.startswith("wios="):
                        wios += int(tok[5:])
        if found:
            return {"read_bytes": rb, "write_bytes": wb,
                    "read_count": rios, "write_count": wios}
    except Exception:
        pass
    try:  # cgroup v1 blkio：行 "maj:min Read <bytes>" / "Write <bytes>"
        rb = wb = 0
        found = False
        with open("/sys/fs/cgroup/blkio/blkio.throttle.io_service_bytes",
                  encoding="utf-8") as f:
            for line in f:
                t = line.split()
                if len(t) == 3 and t[1] == "Read":
                    rb += int(t[2]); found = True
                elif len(t) == 3 and t[1] == "Write":
                    wb += int(t[2])
        if found:
            return {"read_bytes": rb, "write_bytes": wb,
                    "read_count": 0, "write_count": 0}
    except Exception:
        pass
    return None


def _cg_read_int(path: str) -> Optional[int]:
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _cg_read_str(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _container_mem(psutil_mod) -> Optional[dict]:
    """容器自己的記憶體用量（cgroup），與 Proxmox 顯示一致（含 cache）。
    容器內 psutil 走 LXCFS /proc/meminfo 算的 used 排除 cache，會比 cgroup
    memory.current（Proxmox 用的值）低，導致數字對不上。"""
    # cgroup v2
    current = _cg_read_int("/sys/fs/cgroup/memory.current")
    if current is not None:
        # 扣掉可回收的檔案快取（inactive_file），得到 working set，與 Proxmox
        # 對容器顯示的記憶體用量一致；直接用 memory.current 會把 cache 也算進去
        # 而偏高。
        inactive = 0
        try:
            with open("/sys/fs/cgroup/memory.stat", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("inactive_file "):
                        inactive = int(line.split()[1]); break
        except Exception:
            pass
        used = max(0, current - inactive)
        lim = _cg_read_str("/sys/fs/cgroup/memory.max")
        total = int(lim) if (lim and lim != "max") else psutil_mod.virtual_memory().total
        sw_used = _cg_read_int("/sys/fs/cgroup/memory.swap.current") or 0
        sw_lim = _cg_read_str("/sys/fs/cgroup/memory.swap.max")
        sw_total = int(sw_lim) if (sw_lim and sw_lim != "max") else 0
        return {
            "total": total, "used": used, "available": max(0, total - used),
            "percent": round(used / total * 100, 1) if total else 0.0,
            "swap_total": sw_total, "swap_used": sw_used,
            "swap_percent": round(sw_used / sw_total * 100, 1) if sw_total else 0.0,
        }
    # cgroup v1
    usage = _cg_read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if usage is not None:
        inactive = 0
        try:
            with open("/sys/fs/cgroup/memory/memory.stat", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("total_inactive_file "):
                        inactive = int(line.split()[1]); break
        except Exception:
            pass
        used = max(0, usage - inactive)
        lim = _cg_read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        total = lim if (lim and lim < (1 << 62)) else psutil_mod.virtual_memory().total
        memsw = _cg_read_int("/sys/fs/cgroup/memory/memory.memsw.usage_in_bytes")
        memsw_lim = _cg_read_int("/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes")
        sw_used = max(0, (memsw or used) - used)
        sw_total = max(0, (memsw_lim - total)) if (memsw_lim and memsw_lim < (1 << 62)) else 0
        return {
            "total": total, "used": used, "available": max(0, total - used),
            "percent": round(used / total * 100, 1) if total else 0.0,
            "swap_total": sw_total, "swap_used": sw_used,
            "swap_percent": round(sw_used / sw_total * 100, 1) if sw_total else 0.0,
        }
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
    in_container = _is_container()
    # CPU
    try:
        # 實機 / VM：/proc 是自己的，psutil 正確 → 直接用。
        # LXC / Docker 容器：/proc/stat 與 /proc/loadavg 沒 namespace 化，會讀到
        # 實體主機數值 → 改從 cgroup 算容器自己的 CPU%，且不顯示實體主機 loadavg。
        host_percent = psutil.cpu_percent(interval=None)  # 仍呼叫以維持 psutil 內部狀態
        if in_container:
            cg_pct = _container_cpu_percent(psutil.cpu_count(logical=True))
            ncpu = _cgroup_ncpu(psutil.cpu_count(logical=True))
            out["cpu"] = {
                "percent": cg_pct if cg_pct is not None else host_percent,
                "count_logical": ncpu,
                "count_physical": ncpu,
                "loadavg": None,        # 實體主機 loadavg 對容器無意義 → 不顯示
                "in_container": True,
                "source": "cgroup" if cg_pct is not None else "psutil",
            }
        else:
            out["cpu"] = {
                "percent": host_percent,
                "count_logical": psutil.cpu_count(logical=True),
                "count_physical": psutil.cpu_count(logical=False),
                "in_container": False,
            }
            try:
                out["cpu"]["loadavg"] = list(psutil.getloadavg())  # (1,5,15 min)
            except (AttributeError, OSError):
                out["cpu"]["loadavg"] = None
    except Exception as e:
        out["cpu"] = {"error": str(e)}
    # RAM — 容器內改讀 cgroup（與 Proxmox 顯示一致，含 cache）；實機 / VM 用 psutil
    try:
        cm = _container_mem(psutil) if in_container else None
        if cm is not None:
            out["mem"] = cm
        else:
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
            # 標清楚這是「磁碟空間」(資料目錄所在的整顆盤)，不是資料目錄本身大小，
            # 避免誤會成 jt-doc-tools 吃了那麼多空間。
            data_disk["label"] = "磁碟空間（資料目錄所在）"
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
    # 容器內 psutil 讀 /proc/diskstats 是實體主機的（會看到 TB 級累計）→ 改讀
    # cgroup io.stat 的本容器 I/O；實機 / VM 用 psutil。
    try:
        cio = _container_disk_io() if in_container else None
        if cio is not None:
            out["disk_io"] = cio
        else:
            io = psutil.disk_io_counters()
            if io:
                out["disk_io"] = {"read_bytes": io.read_bytes,
                                  "write_bytes": io.write_bytes,
                                  "read_count": io.read_count,
                                  "write_count": io.write_count}
            else:
                out["disk_io"] = None
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
