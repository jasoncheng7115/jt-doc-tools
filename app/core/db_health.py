"""SQLite 完整性檢查、熱備份與復原。

## 為什麼需要

WAL + `synchronous=NORMAL` 擋得住**程式當掉**（已提交的交易不會壞），但擋不住
磁碟故障、檔案系統問題、或把資料目錄放在網路磁碟上（SQLite 對 NFS / SMB 的
鎖定支援一向不可靠）。真的壞掉時，原本的行為是啟動時噴一個 `sqlite3.
DatabaseError: database disk image is malformed` —— 管理員完全不知道發生什麼事，
更不知道還有沒有救。

## 三層防護

1. **啟動時 quick_check**：便宜（毫秒級），壞了就在記錄裡講清楚是哪一個檔、
   建議怎麼做，而不是讓例外往上冒。
2. **熱備份**：`VACUUM INTO` 產生一份乾淨的副本，**不需停機、不鎖住寫入**，
   而且順便重整碎片。比直接複製檔案安全 —— 直接複製 `.sqlite` 而不管 `-wal`
   會拿到一份缺最近交易的檔案。
3. **CLI 復原**：`jtdt db-check` / `db-backup` / `db-restore`。**必須能離線跑**
   —— `auth.sqlite` 壞掉時網頁根本上不去，只剩命令列這條路。

## 重要性分級

`auth.sqlite`（帳號 / 密碼 / 權限）壞掉等於全員鎖在外面，且**密碼無法從別處
重建** → 預設就備份。`vat_db.sqlite` 有 170 萬筆但可以重新下載，而且動輒
GB 級，備份它只會塞爆磁碟 → 不備份。
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("app.db_health")

#: 要納入健康檢查與備份的資料庫。`backup=False` 的仍會做完整性檢查，只是壞了
#: 重建即可，不值得為它花磁碟空間。
#:
#: 影響說明有中英兩份：網頁是中文，**CLI 一律英文 ASCII** —— 純文字終端機 /
#: 精簡容器 / Windows 主控台不一定渲染得出中文，而 CLI 正是資料庫壞掉、網頁上
#: 不去時唯一的管道（見 feedback_cli_help_english_only）。
MANAGED: tuple[dict, ...] = (
    {"file": "auth.sqlite", "label": "使用者與權限", "backup": True,
     "impact": "所有人都無法登入；角色權限可從設定備份還原，但帳號與密碼不行",
     "impact_en": "nobody can log in; roles/permissions can come from a "
                  "settings backup but accounts and passwords cannot"},
    {"file": "audit.sqlite", "label": "稽核記錄", "backup": True,
     "impact": "稽核軌跡遺失且無法重建（法遵風險）",
     "impact_en": "audit trail lost and not reconstructable (compliance risk)"},
    {"file": "jobs.sqlite", "label": "工作紀錄", "backup": False,
     "impact": "背景工作的歷史遺失；重建後照常運作",
     "impact_en": "background job history lost; rebuilds automatically"},
    {"file": "sso_store.sqlite", "label": "SSO 執行期狀態", "backup": False,
     "impact": "SAML 重放快取重置；重建後照常運作",
     "impact_en": "SAML replay cache reset; rebuilds automatically"},
    {"file": "mfp.sqlite", "label": "事務機連線", "backup": True,
     "impact": "使用者新增的事務機與連線測試記錄遺失，需要重新新增",
     "impact_en": "the multifunction printers users added and their connection tests "
                  "are lost; they have to be added again"},
    {"file": "seal_requests.sqlite", "label": "用印申請", "backup": True,
     "impact": "用印申請的流程、案件與核准紀錄遺失（案件檔案在 seal_cases 目錄，不受影響但對不上案件）",
     "impact_en": "seal request flows, requests and approval records are lost (the files in seal_cases "
                  "remain but can no longer be matched to requests)"},
    {"file": "ai_stamp.sqlite", "label": "AI 用印", "backup": True,
     "impact": "AI 用印的待確認清單與紀錄遺失（已核准的檔案在工作區，不受影響）",
     "impact_en": "the AI stamping queue and records are lost (approved files are in the workspaces)"},
    {"file": "vat_db.sqlite", "label": "統編資料庫", "backup": False,
     "impact": "統一編號查詢失效；可從官方來源重新下載（檔案大，不納入備份）",
     "impact_en": "VAT lookup unavailable; re-downloadable from the official "
                  "source (large file, intentionally not backed up)"},
)

BACKUP_DIR_NAME = "db_backups"

#: 每個資料庫保留幾份備份
KEEP_BACKUPS = 7

#: 啟動時自動檢查的大小上限。超過的**跳過不檢查**（會標示為 skipped，不是
#: 「正常」）。實測正式機的統編資料庫有 1.4 GB，冷快取下 `quick_check` 要把整個
#: 檔案讀一遍 —— 部署後實測讓啟動時間從 7 秒變成 61 秒，等於每次 `jtdt update`
#: 多出一分鐘的停機。超過門檻的都是「壞了可以重新下載」那一類，值得用
#: `jtdt db-check` 或管理頁手動檢查，不值得每次啟動都卡住服務。
_STARTUP_MAX_BYTES = 256 * 1024 * 1024


def _data_dir() -> Path:
    from ..config import settings
    return Path(settings.data_dir)


def backup_dir() -> Path:
    return _data_dir() / BACKUP_DIR_NAME


# ---------- 完整性檢查 ----------

def check_one(path: Path, thorough: bool = False) -> dict:
    """檢查單一資料庫。

    `quick_check` 比 `integrity_check` 快很多且抓得到絕大多數毀損 —— 啟動時用
    它；`thorough=True` 走完整的 `integrity_check`，給 CLI 手動檢查用。
    """
    out: dict = {"file": path.name, "path": str(path), "exists": path.exists(),
                 "size_bytes": 0, "ok": True, "detail": ""}
    if not path.exists():
        out["detail"] = "（尚未建立）"
        return out
    try:
        out["size_bytes"] = path.stat().st_size
    except OSError:
        pass
    pragma = "integrity_check" if thorough else "quick_check"
    conn = None
    try:
        # 唯讀開啟：檢查本身不該改動檔案，也不該在壞檔上觸發回復動作
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        rows = conn.execute(f"PRAGMA {pragma}").fetchall()
        msgs = [" ".join(str(r[0]).split()) for r in rows
                if r and r[0] != "ok"]
        out["ok"] = not msgs
        # `integrity_check` 的訊息帶換行，直接印會把 CLI 表格與網頁排版打散
        out["detail"] = "正常" if not msgs else "；".join(msgs[:5])
    except sqlite3.DatabaseError as e:
        out["ok"] = False
        out["detail"] = "無法開啟：" + " ".join(str(e).split())
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["detail"] = f"檢查失敗：{e}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return out


def check_all(thorough: bool = False,
              max_bytes: Optional[int] = _STARTUP_MAX_BYTES) -> list[dict]:
    """檢查所有納管的資料庫。

    **預設會跳過大檔**（`max_bytes`，None = 不跳過）。這不是可有可無的最佳化：
    正式機的統編資料庫有 1.4 GB，`quick_check` 得把整個檔案讀一遍 —— 實測
    **58 秒**。管理頁每次載入都打這支，結果就是點「系統狀態」之後整頁像卡住
    （瀏覽器對同一站台的連線數有限，其他請求全排在後面）。

    要真的檢查大檔就傳 `max_bytes=None`（管理頁的「完整檢查」按鈕會這樣做，
    並且事先告知會花一點時間）。
    """
    d = _data_dir()
    out = []
    for m in MANAGED:
        path = d / m["file"]
        size = path.stat().st_size if path.exists() else 0
        if max_bytes is not None and size > max_bytes:
            r = {"file": m["file"], "path": str(path), "exists": True,
                 "size_bytes": size, "ok": True, "skipped": True,
                 "detail": f"檔案較大（{size / 1048576:.0f} MB），未自動檢查"}
        else:
            r = check_one(path, thorough=thorough)
            r["skipped"] = False
        r.update({"label": m["label"], "backed_up": m["backup"],
                  "impact": m["impact"], "impact_en": m["impact_en"]})
        r["backups"] = len(list_backups(m["file"]))
        out.append(r)
    return out


def startup_check(max_bytes: Optional[int] = _STARTUP_MAX_BYTES) -> list[dict]:
    """啟動時的自動檢查。壞掉時把「是什麼、有多嚴重、怎麼救」寫進記錄。

    **不丟例外** —— 一個壞掉的稽核資料庫不該讓整個服務起不來；但要吵到管理員
    看得到，而不是等他自己發現。大檔一律跳過（見 `check_all`）。
    """
    results = check_all(thorough=False, max_bytes=max_bytes)
    for r in results:
        if r["exists"] and not r["ok"]:
            logger.error(
                "資料庫毀損：%s（%s）— %s。影響：%s。"
                "可用 `jtdt db-restore %s` 從備份還原（現有備份 %d 份），"
                "或 `jtdt db-check --thorough` 看詳細狀況。",
                r["file"], r["label"], r["detail"], r["impact"],
                r["file"], r["backups"])
    return results


def startup_check_async(on_done=None) -> None:
    """在背景執行緒跑啟動檢查。

    就算跳過了大檔，剩下的檢查仍要讀磁碟 —— 放在背景才能保證**服務立刻可用**，
    而不是讓使用者在升級後盯著一個連不上的網頁。
    """
    import threading

    def _run() -> None:
        try:
            rows = startup_check()
            if on_done:
                on_done(rows)
        except Exception:  # noqa: BLE001
            logger.exception("啟動時的資料庫檢查失敗")

    t = threading.Thread(target=_run, name="db-health-check", daemon=True)
    t.start()


# ---------- 熱備份 ----------

def backup_one(path: Path, dest_dir: Optional[Path] = None) -> Optional[Path]:
    """用 `VACUUM INTO` 產生一份乾淨副本。

    為什麼不直接複製檔案：WAL 模式下最近的交易還在 `-wal` 裡，只複製主檔會拿到
    一份**缺最新資料**的備份；而複製到一半正好有人在寫，還可能拿到不一致的頁面。
    `VACUUM INTO` 由 SQLite 自己保證一致性，不需停機也不擋寫入。
    """
    if not path.exists():
        return None
    dest_dir = dest_dir or backup_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{path.stem}.{stamp}.sqlite"
    # 先寫暫存檔再改名，理由有兩個：
    #  ① `VACUUM INTO` 的目標**不可已存在**。時間戳是秒級，同一秒內備份兩次
    #     （測試、或管理員連按兩下）就會撞名而失敗。
    #  ② 失敗時要清掉半成品，但**絕不能刪到既有的備份** —— 第一版就是直接
    #     `dest.unlink()`，撞名失敗後把上一份好的備份也刪了，等於把唯一的救命
    #     索自己剪斷（寫測試時當場踩到）。
    tmp = dest_dir / f".{path.stem}.{stamp}.{os.getpid()}.tmp"
    conn = None
    try:
        tmp.unlink(missing_ok=True)
        conn = sqlite3.connect(str(path), timeout=30)
        conn.execute("VACUUM INTO ?", (str(tmp),))
        conn.close()
        conn = None
        n = 0
        while dest.exists():          # 同秒內第二份 → 加序號，不覆蓋
            n += 1
            dest = dest_dir / f"{path.stem}.{stamp}-{n}.sqlite"
        tmp.replace(dest)
        return dest
    except (sqlite3.Error, OSError) as e:
        logger.warning("備份 %s 失敗：%s", path.name, e)
        try:
            tmp.unlink(missing_ok=True)   # 只刪自己的暫存檔
        except OSError:
            pass
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def list_backups(db_name: str) -> list[Path]:
    """某個資料庫現有的備份，新的排前面。"""
    d = backup_dir()
    if not d.is_dir():
        return []
    stem = Path(db_name).stem
    return sorted(d.glob(f"{stem}.*.sqlite"), reverse=True)


def _rotate(db_name: str, keep: int = KEEP_BACKUPS) -> int:
    removed = 0
    for old in list_backups(db_name)[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def backup_all(keep: int = KEEP_BACKUPS) -> dict:
    """備份所有標記為需備份的資料庫，並輪替舊檔。

    **壞掉的不備份** —— 否則輪替幾輪之後，好的備份會被壞的擠掉，等於把唯一的
    救命索自己剪斷。
    """
    d = _data_dir()
    report: dict = {"created": [], "skipped": [], "removed": 0}
    for m in MANAGED:
        if not m["backup"]:
            continue
        name = m["file"]
        src = d / name
        if not src.exists():
            continue
        chk = check_one(src)
        if not chk["ok"]:
            logger.error("略過備份 %s：資料庫已毀損（%s）—— 不可用壞檔覆蓋既有備份",
                         name, chk["detail"])
            report["skipped"].append({"file": name, "reason": chk["detail"]})
            continue
        made = backup_one(src)
        if made:
            report["created"].append(made.name)
            report["removed"] += _rotate(name, keep)
    if report["created"] or report["skipped"]:
        logger.info("資料庫備份：%s", report)
    return report


# ---------- 復原 ----------

def restore(db_name: str, backup_path: Optional[Path] = None) -> dict:
    """把備份還原成正式檔。

    先把現況另存為 `.corrupt.<時間>`（就算壞了也別直接丟掉 —— 有時還能用
    `.recover` 撈出部分資料），再放上備份。還原前會驗證備份本身是好的。
    """
    d = _data_dir()
    target = d / db_name
    if backup_path is None:
        cands = list_backups(db_name)
        if not cands:
            return {"ok": False, "error": f"找不到 {db_name} 的備份"}
        backup_path = cands[0]
    backup_path = Path(backup_path)
    if not backup_path.exists():
        return {"ok": False, "error": f"備份不存在：{backup_path}"}
    chk = check_one(backup_path, thorough=True)
    if not chk["ok"]:
        return {"ok": False,
                "error": f"備份本身也毀損（{chk['detail']}），已中止還原"}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    moved = None
    try:
        if target.exists():
            moved = target.with_suffix(f".sqlite.corrupt.{stamp}")
            shutil.move(str(target), str(moved))
        # WAL / SHM 是舊檔的殘留，留著會和還原後的主檔對不起來
        for side in ("-wal", "-shm"):
            p = Path(str(target) + side)
            if p.exists():
                p.unlink()
        shutil.copy2(str(backup_path), str(target))
    except OSError as e:
        return {"ok": False, "error": f"還原失敗：{e}"}
    return {"ok": True, "restored_from": str(backup_path),
            "previous_saved_as": str(moved) if moved else None,
            "note": "請重新啟動服務讓新的資料庫生效"}
