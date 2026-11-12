"""File retention settings + background sweeper.

Each retention category has a default in days (-1 = keep forever). The
sweeper runs at startup + every 6 hours, walking each category's storage
location and deleting entries older than the cutoff.

Categories:
  - transit_proof      (data/transit_proof_files/) — 乘車證明的原始檔
  - official_doc_cases (data/official_doc_cases/) — 公文撰擬的歷史案件（已刪除的從刪除那天起算）
  - speech_audio       (data/speech_audio/) — 轉逐字稿上傳的原始錄音（還在排隊或辨識中的不刪）
  - fill_history       (data/fill_history/)
  - stamp_history      (data/stamp_history/)
  - watermark_history  (data/watermark_history/)
  - temp               (data/temp/) — short TTL (hours, not days)
  - jobs               files owned by a job still within jobs_hours — the
                       result file and the temp files its「開啟」reads
                       (mostly in data/temp/, plus anything in data/jobs/);
                       see `job_store.keep_alive_keys` / `job_files.usage`
  - audit              (data/audit.sqlite — DELETE rows by ts)

**這是全站唯一清作業檔案的地方**：`_sweep_temp_dir` 由這裡的 6 小時排程與
`app/main.py` 的 30 分鐘迴圈呼叫（兩條路同一支函式）。`job_manager` 曾經有
一支自己的 `cleanup_expired()`（用另一個期限、還會刪資料庫的作業紀錄），
從來沒有人呼叫，已經拿掉 —— 不要再加回第二條清理路徑。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from . import atomic_json
from typing import Any

from . import audit_db, db, history_manager, sessions

logger = logging.getLogger(__name__)


_DEFAULTS: dict[str, Any] = {
    # 乘車證明的**原始 PDF**（使用者事後點得回去看原件）。
    # 報帳單據的性質跟填寫歷史一樣是「事後可能要翻出來對」，所以同樣預設一年。
    # 這些是**使用者上傳的原始憑證**，含個資 —— 保留期到了就該清掉。
    "transit_proof_days":     365,
    # 公文撰擬的歷史案件（輸入、草稿、版本）。跟填寫歷史同性質（事後要翻出來看），
    # 一樣預設一年；最後修改超過保留期就整個案件刪掉。
    "official_doc_cases_days": 365,
    # 會議錄音轉逐字稿上傳的**原始錄音**（`data/speech_audio/<編號>.bin`）。
    # 原本沒有任何保留期，永遠不刪 —— 錄音是聲音本身，比逐字稿更敏感，
    # 而且一場會議動輒上百 MB。用得到它的只有三件事：語音服務來拉檔（送件當下）、
    # 結果頁播放（跟著逐字稿，逐字稿照「作業結果檔」的保留期清）、
    # 重跑校正（不需要錄音，對方用的是自己那份）。所以預設 30 天已經很寬。
    # 這個欄位 v1.16.71 才加：舊安裝升級後第一次清理就會套用 30 天。
    "speech_audio_days":      30,
    "fill_history_days":      365,
    "stamp_history_days":     365,
    "watermark_history_days": 365,
    "temp_hours":             2,        # data/temp/
    "jobs_hours":             24,       # 作業擁有的檔案（多半在 data/temp/，見 job_files）
    # 工作紀錄（jobs.sqlite 的列，不是結果檔）。結果檔照 jobs_hours 清掉之後，
    # 紀錄仍保留一段時間，讓使用者在「我的工作」看得到做過什麼、管理員查得到
    # 誰在什麼時候轉了什麼。**這張表沒有人清就會無限長大** —— 8000 人規模下
    # 每天約 4 萬筆，一年就是 1,500 萬筆。
    "job_records_days":       30,
    "audit_days":             90,       # audit_events table rows
    "updated_at":             0.0,
}


def _path() -> Path:
    from ..config import settings
    return settings.data_dir / "retention.json"


_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None


def get() -> dict[str, Any]:
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            p = _path()
            if p.exists():
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    merged = json.loads(json.dumps(_DEFAULTS))
                    merged.update({k: v for k, v in raw.items() if k in _DEFAULTS})
                    _CACHE = merged
                except Exception:
                    _CACHE = json.loads(json.dumps(_DEFAULTS))
            else:
                _CACHE = json.loads(json.dumps(_DEFAULTS))
        return json.loads(json.dumps(_CACHE))


def save(new: dict[str, Any]) -> None:
    """Merge + persist. Any int field with value -1 means "no expiry"."""
    global _CACHE
    with _LOCK:
        merged = json.loads(json.dumps(_DEFAULTS))
        for k in _DEFAULTS:
            if k in new:
                if k == "updated_at":
                    continue
                v = new[k]
                if not isinstance(v, (int, float)):
                    raise ValueError(f"{k} 必須是數字")
                merged[k] = int(v)
        merged["updated_at"] = time.time()
        atomic_json.write_json(_path(), merged, mode=0o600)
        _CACHE = merged


# ---------- stat collection (admin "目前佔用空間" display) ----------

def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def _oldest_entry_age_days(p: Path) -> float | None:
    """Walk subdirs of p (history layout), return oldest entry's age in days."""
    if not p.exists():
        return None
    oldest_ts = None
    for d in p.iterdir():
        if not d.is_dir():
            continue
        mf = d / "meta.json"
        if mf.exists():
            try:
                ts = json.loads(mf.read_text(encoding="utf-8")).get("saved_at")
                if ts and (oldest_ts is None or ts < oldest_ts):
                    oldest_ts = ts
            except Exception:
                pass
    if oldest_ts is None:
        return None
    return (time.time() - oldest_ts) / 86400.0


def collect_stats() -> dict[str, Any]:
    from ..config import settings as _s
    stats = {}
    for key, sub in [("fill_history", "fill_history"),
                     ("stamp_history", "stamp_history"),
                     ("watermark_history", "watermark_history"),
                     ("transit_proof", "transit_proof_files")]:
        d = _s.data_dir / sub
        stats[key] = {
            "size_mb": _dir_size(d) / 1024 / 1024,
            "oldest_days": _oldest_entry_age_days(d),
        }
    # 「暫存」與「作業結果檔」**依檔案歸誰的保留期分**，不是依目錄分。
    #
    # 原本「作業結果檔」量的是 `data/jobs/` —— 全站沒有任何一支工具把結果放在
    # 那裡，那一列永遠是 0 MB；真正的作業檔案在 `data/temp/`，照作業的保留期
    # 留著，卻被算在「暫存」那一列。認人的規則跟清理程式是同一份
    # （`job_files.usage` → `job_store.keep_alive_keys` / `owns_file`）。
    from . import official_doc_cases
    stats["official_doc_cases"] = official_doc_cases.stats()
    stats["speech_audio"] = _speech_audio_stats()
    from . import job_files
    s = get()
    split = job_files.usage(s["jobs_hours"] * 3600 if s["jobs_hours"] > 0 else 0)
    stats["temp"] = split["temp"]
    stats["jobs"] = split["jobs"]
    stats["audit"] = {
        "size_mb": db.db_size_bytes(audit_db.audit_db_path()) / 1024 / 1024,
        "oldest_days": _audit_oldest_days(),
    }
    stats["job_records"] = _job_records_stats()
    return stats


def _job_records_stats() -> dict[str, Any]:
    """jobs.sqlite 的大小與最舊一筆（給保留設定頁顯示）。"""
    from . import job_store
    try:
        size = db.db_size_bytes(job_store.db_path()) / 1024 / 1024
    except Exception:
        size = 0.0
    oldest = None
    try:
        row = db.fetchone(db.get_conn(job_store.db_path()),
                          "SELECT MIN(created_at) FROM jobs")
        if row and row[0]:
            oldest = (time.time() - float(row[0])) / 86400.0
    except Exception:
        pass
    return {"size_mb": size, "oldest_days": oldest}


def _audit_oldest_days() -> float | None:
    try:
        row = audit_db.conn().execute(
            "SELECT MIN(ts) FROM audit_events").fetchone()
        ts = row[0]
        if not ts:
            return None
        return (time.time() - ts) / 86400.0
    except Exception:
        return None


# ---------- sweepers ----------

def _sweep_temp_dir(temp_seconds: int, jobs_seconds: int) -> int:
    """清掉過期的暫存檔與作業結果檔。

    **兩個目錄要用各自的保留期**。第一版只收一個秒數、對 `temp` 與 `jobs`
    用同一個 cutoff，而傳進來的是 `temp_hours` —— 於是 `jobs_hours`
    **整個設定從來沒有被任何程式讀過**（v1.14.31 對抗式驗證：設
    `temp_hours=1、jobs_hours=48`，47 小時前的作業結果照樣被刪）。

    後果是使用者與管理員看到的保留期是假的：管理頁顯示「作業結果檔 24 小時」、
    「我的作業」也顯示 24 小時，實際 2 小時就清掉了。這正是「壞掉了很難發現」
    的典型 —— 沒有錯誤訊息，只有使用者回頭找不到自己的檔案。
    """
    from ..config import settings as _s
    n = 0

    # **v1.14.31 只修了一半**：那次讓 `jobs/` 改用 `jobs_hours`，但全站沒有任何
    # 一支工具把結果放進 `jobs/` —— 結果檔、「開啟」要讀的資料、歸屬紀錄全部
    # 在 `temp/`，照樣 2 小時就清掉，「我的作業」上的 24 小時仍然是假的
    # （v1.16.6 使用者回報：已完成的會議摘要按「開啟」得到 410）。
    #
    # 修法不是把 27 支工具的輸出路徑都搬家，而是**在這裡認出「還在作業保留期內
    # 的作業」的東西**，讓它們照作業的保留期走（見 `job_store.keep_alive_keys`）。
    keep_ids: set[str] = set()
    keep_names: set[str] = set()
    if temp_seconds > 0:
        from . import job_store
        since = time.time() - jobs_seconds if jobs_seconds > 0 else 0.0
        keep_ids, keep_names = job_store.keep_alive_keys(since)

    def _kept(p: Path) -> bool:
        # 認人的規則只有一份（`job_store.owns_file`）——「我的作業」的開啟鈕與
        # 管理頁的用量也用它，清理認得的檔案那兩邊一定也認得。
        from . import job_store
        return job_store.owns_file(p.name, keep_ids, keep_names)

    for sub, seconds in (("temp", temp_seconds), ("jobs", jobs_seconds)):
        if seconds <= 0:            # 0 或負數 = 永久保留
            continue
        cutoff = time.time() - seconds
        d = _s.data_dir / sub
        if not d.exists():
            continue
        protect = sub == "temp"
        for child in d.iterdir():
            # `.owners/` is a special dir for upload-owner ACL sidecars
            # — sweep individual records inside it (the dir itself stays
            # fresh as long as new uploads are happening).
            if child.is_dir() and child.name == ".owners":
                speech = _s.data_dir / "speech_audio"
                for owner_file in child.iterdir():
                    try:
                        if protect and _kept(owner_file):
                            continue
                        # 會議錄音留 30 天，它的歸屬紀錄要跟著錄音留著 —— 清掉的話本人
                        # 回頭播放、看波形都會被擋，管理頁也把它算成「(匿名)」。
                        if (speech / f"{owner_file.stem}.bin").exists():
                            continue
                        if owner_file.stat().st_mtime < cutoff:
                            owner_file.unlink(missing_ok=True)
                            n += 1
                    except OSError:
                        pass
                continue
            try:
                if protect and _kept(child):
                    continue
                if child.stat().st_mtime < cutoff:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                    n += 1
            except OSError:
                pass
    return n


def _sweep_audit(days: int) -> int:
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    conn = audit_db.conn()
    with db.tx(conn):
        cur = conn.execute("DELETE FROM audit_events WHERE ts < ?", (cutoff,))
    return cur.rowcount


_DB_BACKUP_INTERVAL = 20 * 3600      # 每天一份（略小於 24h，避開排程漂移）


def _maybe_backup_dbs() -> dict:
    """距離上一份備份超過一天才做，否則跳過（排程每 6 小時跑一次）。"""
    from . import db_health
    newest = 0.0
    for m in db_health.MANAGED:
        if not m["backup"]:
            continue
        for b in db_health.list_backups(m["file"])[:1]:
            try:
                newest = max(newest, b.stat().st_mtime)
            except OSError:
                pass
    if newest and (time.time() - newest) < _DB_BACKUP_INTERVAL:
        return {"skipped": "備份仍在有效期內"}
    return db_health.backup_all()


def _sweep_job_records(days: int) -> int:
    """清掉舊的工作紀錄列（結果檔另由 _sweep_temp_dir 依 jobs_hours 處理）。

    只清已結束的 —— 正在跑或排隊中的不管多舊都不能刪掉，否則使用者的工作會
    在進行中從清單上消失。
    """
    if days <= 0:
        return 0
    from . import job_store
    return job_store.delete_older_than(time.time() - days * 86400)


def _sweep_transit_proof(days: int) -> int:
    """清掉過期的**乘車證明原始檔**（每位使用者一個目錄）。

    `days <= 0` = 永久保留。判準用檔案自己的 mtime —— 上傳當下寫進去，
    之後不會再動。清完把空目錄一併移除，不要留一堆空殼。
    """
    if days <= 0:
        return 0
    from ..config import settings as _s
    root = _s.data_dir / "transit_proof_files"
    if not root.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for user_dir in root.iterdir():
        if not user_dir.is_dir():
            continue
        for f in user_dir.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        try:
            next(user_dir.iterdir())
        except StopIteration:
            try:
                user_dir.rmdir()
            except OSError:
                pass
        except OSError:
            pass
    return removed


def _sweep_speech_audio(days: int) -> int:
    """清掉超過保留期的**會議錄音**（`data/speech_audio/`）。

    `days <= 0` = 永久保留。判準用檔案的 mtime（上傳或從工作區接過來的那一刻寫進去，
    之後不再動）。**還在排隊或辨識中的作業的錄音一律不刪**：語音服務是排到才來拉檔，
    刪掉的話那一件會變成「來源連不上」。只認 `<32 碼十六進位>.bin`（與寫到一半的
    `.part`），目錄裡別的東西不碰。
    """
    if days <= 0:
        return 0
    from ..config import settings as _s
    root = _s.data_dir / "speech_audio"
    if not root.is_dir():
        return 0
    try:
        from . import job_store
        active, _ = job_store.keep_alive_keys(time.time())
    except Exception:
        logger.exception("speech audio sweep: 讀不到進行中的作業，這一輪不刪錄音")
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for f in root.iterdir():
        stem = f.name.split(".", 1)[0]
        if not (len(stem) == 32 and all(c in "0123456789abcdef" for c in stem)):
            continue
        if f.name not in (stem + ".bin", stem + ".bin.part") or stem in active:
            continue
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _restore_speech_owners() -> int:
    """替還在的會議錄音補回遺失的歸屬紀錄（從作業紀錄找送件的人）。回傳補了幾筆。

    v1.17.20 之前，歸屬紀錄跟著暫存區清（最多留作業保留期），錄音卻留 30 天 ——
    之前上傳的錄音已經沒有紀錄了，這裡補回來；之後的由清理程式直接保留。
    沒有啟用登入時不需要歸屬紀錄（`upload_owner.record` 也不寫），不補。
    """
    from ..config import settings as _s
    from . import job_store, upload_owner
    if not upload_owner.auth_enabled():
        return 0
    speech = _s.data_dir / "speech_audio"
    owners = _s.temp_dir / ".owners"
    if not speech.exists():
        return 0
    missing = [f.stem for f in speech.iterdir()
               if f.is_file() and f.suffix == ".bin" and not (owners / f"{f.stem}.json").exists()]
    if not missing:
        return 0
    found = job_store.owners_for_uploads(missing)
    n = 0
    for uid, owner_id in found.items():
        try:
            owners.mkdir(parents=True, exist_ok=True)
            atomic_json.write_json(owners / f"{uid}.json",
                                   {"user_id": owner_id, "ts": time.time(), "restored_from": "job"})
            n += 1
        except OSError:
            logger.warning("speech audio owner restore failed for %s", uid)
    if n:
        logger.info("restored %d speech audio owner record(s) from job records", n)
    return n


def _speech_audio_stats() -> dict[str, Any]:
    from ..config import settings as _s
    root = _s.data_dir / "speech_audio"
    size, n, oldest = 0, 0, None
    if root.is_dir():
        for f in root.iterdir():
            try:
                if not f.is_file():
                    continue
                st = f.stat()
            except OSError:
                continue
            size += st.st_size
            n += 1
            if oldest is None or st.st_mtime < oldest:
                oldest = st.st_mtime
    return {"size_mb": size / 1024 / 1024, "files": n,
            "oldest_days": (time.time() - oldest) / 86400.0 if oldest else None}


def sweep_all() -> dict[str, Any]:
    """Run every sweeper once, return a report dict."""
    s = get()
    report: dict[str, Any] = {}
    report["fill"] = history_manager.history_manager.sweep_older_than(
        s["fill_history_days"] * 86400 if s["fill_history_days"] > 0 else 0)
    report["stamp"] = history_manager.stamp_history.sweep_older_than(
        s["stamp_history_days"] * 86400 if s["stamp_history_days"] > 0 else 0)
    report["watermark"] = history_manager.watermark_history.sweep_older_than(
        s["watermark_history_days"] * 86400 if s["watermark_history_days"] > 0 else 0)
    # 先替會議錄音補回遺失的歸屬紀錄，清暫存區時才認得要保留它們
    try:
        report["speech_owners_restored"] = _restore_speech_owners()
    except Exception:
        logger.exception("speech audio owner restore failed")
    # temp_hours is in HOURS not days
    report["temp"] = _sweep_temp_dir(
        s["temp_hours"] * 3600 if s["temp_hours"] > 0 else 0,
        s["jobs_hours"] * 3600 if s["jobs_hours"] > 0 else 0)
    # Workspace has its own settings file (data/workspace.json); retention is
    # in hours, -1 = keep forever.
    try:
        from . import workspace as _ws
        ws_hours = int(_ws.get_settings().get("retention_hours", -1))
        report["workspace"] = _ws.sweep_older_than(
            ws_hours * 3600 if ws_hours > 0 else 0)
    except Exception:
        logger.exception("workspace sweep failed")
    report["transit_proof"] = _sweep_transit_proof(s["transit_proof_days"])
    # AI 用印：24 小時沒確認的作廢、刪檔（查詢時也會跑；這裡保證沒人看也會清）
    try:
        from . import ai_stamp
        report["ai_stamp_expired"] = ai_stamp.expire_due()
    except Exception:
        logger.exception("ai stamp expiry failed")
    try:
        from . import official_doc_cases
        report["official_doc_cases"] = official_doc_cases.purge_older_than(
            s["official_doc_cases_days"])
    except Exception:
        logger.exception("official-doc cases sweep failed")
    # 用印申請：保存期限由管理員依規定設定（設定 → 用印申請），0 ＝ 不自動刪除
    try:
        from . import seal_flow
        report["seal_requests"] = seal_flow.purge_expired()
    except Exception:
        logger.exception("seal-request purge failed")
    report["speech_audio"] = _sweep_speech_audio(s["speech_audio_days"])
    report["audit"] = _sweep_audit(s["audit_days"])
    report["job_records"] = _sweep_job_records(s["job_records_days"])
    # 資料庫熱備份。掛在既有的 6 小時排程上（而不是另開一個排程執行緒），並用
    # 時間戳自己節流成每天一份 —— 備份是**遇到磁碟毀損時唯一的救命索**，寧可
    # 跟著清理一起跑也不要另外多一個可能沒起來的背景執行緒。
    try:
        report["db_backup"] = _maybe_backup_dbs()
    except Exception:
        logger.exception("database backup failed")
    # Expired sessions
    report["sessions"] = sessions.cleanup_expired()
    logger.info("retention sweep report: %s", report)
    return report


# ---------- background scheduler ----------

_SCHED_THREAD: threading.Thread | None = None
_SCHED_STOP = threading.Event()
_INTERVAL = 6 * 3600   # every 6h


def start_scheduler() -> None:
    global _SCHED_THREAD
    with _LOCK:
        if _SCHED_THREAD is not None and _SCHED_THREAD.is_alive():
            return
        _SCHED_STOP.clear()
        _SCHED_THREAD = threading.Thread(
            target=_loop, name="retention-sweeper", daemon=True,
        )
        _SCHED_THREAD.start()


def stop_scheduler() -> None:
    _SCHED_STOP.set()
    if _SCHED_THREAD is not None:
        _SCHED_THREAD.join(timeout=5)


def _loop() -> None:
    # Run once immediately on startup
    try:
        sweep_all()
    except Exception:
        logger.exception("initial sweep failed")
    while not _SCHED_STOP.is_set():
        if _SCHED_STOP.wait(_INTERVAL):
            break
        try:
            sweep_all()
        except Exception:
            logger.exception("scheduled sweep failed")
