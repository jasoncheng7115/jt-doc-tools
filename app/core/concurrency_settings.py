"""併行度設定 —— 同時可以跑幾個工作、其中幾個可以是 Office 轉檔。

## 兩個獨立的旋鈕，因為瓶頸不一樣

| 設定 | 影響的工作 | 真正的上限來自 |
|---|---|---|
| `max_concurrent_jobs` | 全部（OCR / 壓縮 / 合併 / 分拆…） | CPU 核心數 |
| `max_office_concurrent` | 會起 soffice 的那 13 個工具 | **記憶體** |
| `max_remote_concurrent` | 呼叫外部 LLM / 遠端 GPU OCR 的作業 | **對方機器的容量** |
| `soffice_cpu_percent` | soffice 子行程可用的核心數 | **要留給網頁的餘裕** |

原本兩者都是寫死的：thread pool 是 2，而 `office_convert._soffice_lock` 是一把
process-wide 的鎖 —— 也就是說**不管 worker 開幾個，Office 轉檔實際永遠只跑一
個**。第二個人送 PDF 轉簡報就是乾等前面那份跑完。

## 為什麼那把鎖可以放寬（但不是無條件）

讀 `office_convert` 的註解才知道，鎖的理由是 **macOS 的 Aqua bootstrap 競爭**：

    "Even though each call now has its own profile, two concurrent
     osascript→soffice on macOS still race on the WindowServer/Aqua bootstrap."

每次呼叫**早就已經用獨立的 profile 目錄**（`-env:UserInstallation=` 指到臨時
目錄），所以在 Linux / Windows 上並沒有共用狀態的問題。正式部署都是 Linux，卻
一起被鎖成單工。

→ 改成號誌（semaphore）：Linux / Windows 可調，**macOS 強制 1**（見
`hard_max_office()`，UI 也要照實說明原因，不是留給管理員踩坑）。

## OOM 是鐵則

放寬併行度最直接的風險就是把機器打爆 —— 一個 soffice 轉大檔可以吃到數百 MB。
所以這裡提供三層防護，`job_manager._dispatch()` 與 `office_convert` 都會用到：

1. `suggested_max_office()` / `hard_max_office()` —— 依**實際可用記憶體**算出建議
   值與硬上限，管理員填再大也會被夾住。
2. `estimated_job_mb()` + `reserve_mb()` —— 派送前先估「再開一個要多少」，不夠就
   讓工作**留在佇列裡排隊**，不是硬開。
3. 記憶體讀取走 `host_stats`（容器感知）—— LXC / Docker 內 psutil 讀到的是實體
   主機的數字，拿那個判斷等於沒判斷。
"""
from __future__ import annotations

import json
import logging
import os
import platform
import threading
from pathlib import Path
from . import atomic_json
from typing import Any, Optional

logger = logging.getLogger("app.concurrency")

_LOCK = threading.RLock()
_CACHE: Optional[dict] = None

#: 會起 soffice 的工具。**新增用到 office_convert 的工具要加進來** ——
#: `tests/test_job_concurrency.py` 會跟實際掃描結果比對，漏加會紅。
OFFICE_TOOL_IDS: frozenset[str] = frozenset({
    "doc-deident", "doc-diff", "doc-translate", "markdown-to-doc", "office-to-pdf", "doc-straighten",
    "office-convert",
    "pdf-bookmark", "pdf-border",
    "pdf-extract-text", "pdf-fill", "pdf-nup", "pdf-to-image",
    "pdf-page-size", "pdf-seam-stamp", "pdf-to-office", "pdf-to-slides", "submission-check",
    "text-deident",
    "translate-doc",
    # 字數統計收辦公文件後也會起 soffice（v1.14.94）
    "pdf-wordcount",
    # 匯出 PDF 會走 soffice（Markdown → PDF，跟「Markdown 轉辦公文件」同一條路）
    "meeting-summary",
    # 公文撰擬：匯出 DOCX / PDF 走 soffice（ODT → 轉檔）；「從檔案帶入文字」收 Word / ODF 也會起 soffice
    "official-doc",
})

#: 會做 OCR 的工具（本機 EasyOCR / Tesseract）。第一次載入模型要數十秒又吃記憶體，
#: 管理員看得出來才知道那個作業為什麼久。**新增用到 `ocr_engine` 的工具要加進來**。
OCR_TOOL_IDS: frozenset[str] = frozenset({
    "pdf-editor", "pdf-ocr", "submission-check",
    "business-card-reader",   # 名片辨識：逐卡 OCR
})

#: 可能呼叫**外部服務**的工具（LLM 或遠端 GPU OCR）。這一類的瓶頸不在本機 ——
#: 是對方那台機器的容量，所以走 `remote_limit` 另一條號誌。
#: **新增用到 `llm_settings` / `llm_client` / 遠端 OCR 的工具要加進來**。
REMOTE_TOOL_IDS: frozenset[str] = frozenset({
    "meeting-transcribe",   # 送去 jtlw 轉逐字稿 —— 瓶頸在對方那台
    "doc-deident", "doc-diff", "einvoice-scan", "pdf-annotations",
    "pdf-extract-text", "pdf-fill", "pdf-ocr", "pdf-wordcount",
    "submission-check", "text-deident", "translate-doc", "doc-translate",
    "meeting-summary",
    "official-doc",         # 公文撰擬：整理資料 ＋ 撰寫草稿各呼叫一次 LLM
    "business-card-reader",  # 名片辨識：選用的 AI 協助（預設關閉）
})


def resource_tags(tool_id: str) -> list[str]:
    """這個工具會用到哪些**共用資源**（給作業清單標示用）。

    使用者看到「為什麼我的作業排這麼久」時，答案通常是「它跟別人搶同一個東西」。
    三種資源各有自己的併行上限，標出來才知道是卡在哪一個：

    * `office` —— soffice 轉檔，瓶頸是記憶體
    * `ocr` —— 本機 OCR，第一次要載模型
    * `remote` —— 外部 LLM / 遠端 GPU OCR，瓶頸是對方機器

    一個工具可以同時屬於多類（例如 OCR 文字辨識可以走本機也可以走遠端 GPU）。
    """
    tags = []
    if tool_id in OFFICE_TOOL_IDS:
        tags.append("office")
    if tool_id in OCR_TOOL_IDS:
        tags.append("ocr")
    if tool_id in REMOTE_TOOL_IDS:
        tags.append("remote")
    return tags


#: 單一工作的記憶體估計值（MB）。soffice 轉檔實測 300–800MB，大檔更多 → 取
#: 偏保守的高值；估太低的代價是 OOM，估太高只是多排隊一下。
_MB_OFFICE = 800
_MB_OTHER = 250

#: 一定要留給作業系統 / 資料庫 / 反向代理的餘裕（MB）
_DEFAULT_RESERVE_MB = 768

_DEFAULTS: dict[str, Any] = {
    "max_concurrent_jobs": 2,
    "max_office_concurrent": 1,
    # 外部服務（LLM / 遠端 GPU OCR）的同時呼叫上限。**預設 1**：真正的瓶頸在
    # 對方那台機器，本機的記憶體准入檢查擋不到（見 remote_limit 的說明）。
    "max_remote_concurrent": 1,
    # soffice 可以用掉幾成的 CPU。**0 = 自動（總核心數減 1，永遠留一顆給網頁）**，
    # 100 = 不限制。詳見 `cpu_limit` —— 這是「網頁回應優先」的落實方式。
    "soffice_cpu_percent": 0,
    "reserve_mb": _DEFAULT_RESERVE_MB,
}

_ABS_MAX_JOBS = 32


def _path() -> Path:
    from ..config import settings
    return settings.data_dir / "concurrency.json"


# ---------- 記憶體 ----------

def _mem() -> Optional[dict]:
    """容器感知的記憶體資訊（bytes）。取不到回 None。"""
    try:
        from . import host_stats
        stats = host_stats.get_host_stats()
        if not stats.get("available"):
            return None
        mem = stats.get("memory") or stats.get("mem")
        if isinstance(mem, dict) and mem.get("total"):
            return mem
    except Exception as e:  # noqa: BLE001
        logger.debug("concurrency: 讀取記憶體失敗：%s", e)
    return None


def available_mb() -> Optional[int]:
    m = _mem()
    if not m:
        return None
    avail = m.get("available")
    if avail is None:
        total, used = m.get("total") or 0, m.get("used") or 0
        avail = max(0, total - used)
    return int(avail / 1024 / 1024)


def total_mb() -> Optional[int]:
    m = _mem()
    return int((m.get("total") or 0) / 1024 / 1024) if m else None


def cpu_snapshot() -> dict:
    """CPU 使用率與核心數（容器感知）。

    走 `host_stats` 而不是直接 psutil —— LXC / Docker 內 `/proc/stat` 沒有
    namespace 化，psutil 讀到的是**實體主機**的使用率，拿那個判斷等於沒判斷
    （host_stats 在容器內改讀 cgroup 的 cpu.stat）。
    """
    try:
        from . import host_stats
        st = host_stats.get_host_stats()
        if not st.get("available"):
            return {}
        c = st.get("cpu") or {}
        return {"percent": c.get("percent"),
                "count": c.get("count_logical") or c.get("count_physical"),
                "in_container": bool(c.get("in_container")),
                "loadavg": c.get("loadavg")}
    except Exception as e:  # noqa: BLE001
        logger.debug("concurrency: 讀取 CPU 失敗：%s", e)
        return {}


def reserve_mb() -> int:
    return int(get().get("reserve_mb") or _DEFAULT_RESERVE_MB)


def estimated_job_mb(tool_id: str) -> int:
    return _MB_OFFICE if tool_id in OFFICE_TOOL_IDS else _MB_OTHER


def is_macos() -> bool:
    return platform.system() == "Darwin"


def hard_max_office() -> int:
    """Office 轉檔同時數的硬上限 —— 管理員填再大也不會超過。

    macOS 一律 1（Aqua bootstrap 競爭，見模組說明）。其餘平台依總記憶體推算：
    扣掉保留量後，每 `_MB_OFFICE` 允許一個。
    """
    if is_macos():
        return 1
    total = total_mb()
    if not total:
        return 2          # 讀不到記憶體 → 保守放行到 2
    usable = max(0, total - _DEFAULT_RESERVE_MB)
    return max(1, min(int(usable // _MB_OFFICE), 8))


def suggested_max_office() -> int:
    """建議值：硬上限與 CPU 核心數取小，再保守下修一級。"""
    hard = hard_max_office()
    cpus = os.cpu_count() or 2
    return max(1, min(hard, max(1, cpus // 2)))


def hard_max_jobs() -> int:
    total = total_mb()
    cpus = os.cpu_count() or 2
    by_cpu = max(2, cpus * 2)
    if not total:
        return min(by_cpu, _ABS_MAX_JOBS)
    usable = max(0, total - _DEFAULT_RESERVE_MB)
    by_mem = max(1, int(usable // _MB_OTHER))
    return max(1, min(by_cpu, by_mem, _ABS_MAX_JOBS))


# ---------- 讀寫 ----------

def get() -> dict:
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return dict(_CACHE)
        cfg = dict(_DEFAULTS)
        p = _path()
        if p.is_file():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cfg.update({k: raw[k] for k in raw if k in _DEFAULTS})
            except (OSError, ValueError) as e:
                logger.warning("concurrency.json 讀取失敗，改用預設值：%s", e)
        _CACHE = cfg
        return dict(cfg)


def save(new: dict) -> dict:
    """存檔並立即套用。所有數值都會被夾在安全範圍內（防手滑打爆機器）。"""
    global _CACHE
    cfg = get()
    if "max_concurrent_jobs" in new:
        cfg["max_concurrent_jobs"] = _clamp(new["max_concurrent_jobs"],
                                            1, hard_max_jobs())
    if "max_office_concurrent" in new:
        cfg["max_office_concurrent"] = _clamp(new["max_office_concurrent"],
                                              1, hard_max_office())
    if "max_remote_concurrent" in new:
        cfg["max_remote_concurrent"] = _clamp(new["max_remote_concurrent"], 1, 16)
    if "soffice_cpu_percent" in new:
        # 0 = 自動，其餘夾在 10–100。允許 0 所以下限單獨處理。
        raw = new["soffice_cpu_percent"]
        try:
            pct = int(raw)
        except (TypeError, ValueError):
            pct = 0
        cfg["soffice_cpu_percent"] = 0 if pct <= 0 else max(10, min(pct, 100))
    if "reserve_mb" in new:
        cfg["reserve_mb"] = _clamp(new["reserve_mb"], 128, 8192)
    with _LOCK:
        atomic_json.write_json(_path(), cfg)
        _CACHE = cfg
    apply()
    return dict(cfg)


def _clamp(v: Any, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = lo
    return max(lo, min(n, hi))


def invalidate_cache() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None


def apply() -> None:
    """把目前設定推到 job_manager 與 office_convert。啟動時與存檔後呼叫。"""
    cfg = get()
    try:
        from .job_manager import job_manager
        job_manager.set_max_concurrent(cfg["max_concurrent_jobs"])
    except Exception as e:  # noqa: BLE001
        logger.warning("套用工作併行度失敗：%s", e)
    try:
        from . import office_convert
        office_convert.set_office_concurrency(
            min(int(cfg["max_office_concurrent"]), hard_max_office()))
    except Exception as e:  # noqa: BLE001
        logger.warning("套用 Office 併行度失敗：%s", e)
    try:
        from . import remote_limit
        remote_limit.set_limit(int(cfg["max_remote_concurrent"]))
    except Exception as e:  # noqa: BLE001
        logger.warning("套用外部服務併行度失敗：%s", e)


def describe() -> dict:
    """給 admin UI：目前值 + 上限 + 建議 + 記憶體現況 + macOS 說明。"""
    cfg = get()
    return {
        **cfg,
        "hard_max_jobs": hard_max_jobs(),
        "hard_max_office": hard_max_office(),
        "suggested_office": suggested_max_office(),
        "office_locked_reason": (
            "macOS 上多個 soffice 會在 Aqua / WindowServer 啟動時互相競爭，"
            "因此固定為 1。Linux / Windows 沒有這個限制。"
            if is_macos() else ""),
        "total_mb": total_mb(),
        "available_mb": available_mb(),
        "estimate_office_mb": _MB_OFFICE,
        "estimate_other_mb": _MB_OTHER,
        "cpu_count": os.cpu_count() or 0,
        "office_tools": sorted(OFFICE_TOOL_IDS),
        "remote_services": _remote_services(),
        "cpu_limit": _cpu_limit_describe(),
    }


def _cpu_limit_describe() -> dict:
    try:
        from . import cpu_limit
        return cpu_limit.describe()
    except Exception as e:  # noqa: BLE001
        logger.debug("讀取 CPU 限制狀態失敗：%s", e)
        return {}


def _remote_services() -> list[str]:
    """目前實際會走外部服務的功能（給 UI 說明用）。"""
    out = []
    try:
        from .llm_settings import llm_settings as _l
        s = _l.get_settings()
        if s.get("enabled"):
            out.append(f"LLM（{s.get('model') or '未設定模型'}）")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import ocr_remote_settings
        r = ocr_remote_settings.get()
        if r.get("enabled"):
            out.append("遠端 GPU OCR")
    except Exception:  # noqa: BLE001
        pass
    return out
