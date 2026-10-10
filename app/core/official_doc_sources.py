"""公文撰擬的官方資料來源：公文範本（.odt）與機關地址簿。

## 這支在做什麼

「公文撰擬」要能用**政府開放資料**的兩樣東西：

* **筆硯公文製作系統表單範本**（國家發展委員會檔案管理局，data.gov.tw 30943）
  —— 一個 zip，裡面 88 份 `.odt`（函、簽、書函、開會通知單…）。
* **公文電子交換系統地址簿**（同機關，data.gov.tw 7617，每日更新）
  —— 約 4 萬個機關的代碼與全銜，受文者 / 副本的機關名稱用它查。

這支管：來源設定（網址可以改、可以增刪）、下載 / 手動上傳、驗證、存放、
狀態，以及給工具用的四個查詢介面（`list_templates` / `get_template` /
`search_orgs` / `attribution`）。

## 為什麼預設不下載

使用者 2026-10-07 決定：「預設沒下，要管理員去按」。很多機關的伺服器**不能
連外**，啟動時自己去連會在記錄裡留一串失敗、而且讓人以為系統在偷偷傳東西。
所以這支**沒有任何排程、import 時也不碰網路**；只有管理員按「下載／更新」
（`start_download`）才會連出去。不能連外的機關用「上傳檔案」把同一個檔案
放進來，走**同一套驗證**。

## 網址可以改

資料集會搬家（網址有中文、檔名改過不只一次）。內建的兩個來源網址可以改，
也可以刪；「還原預設」把內建的放回來，**不動自訂的**。管理員填的網址是
SSRF 的入口，所以下載一律走 `safe_fetch.fetch_public()`（只准公開網路、
每次重新導向都重驗、連線時直接連驗過的 IP）。

## 授權與顯名

內建兩個來源都是「政府資料開放授權條款－第1版」：可以重製、改作、散布、商業
使用，**條件是顯名**（提供機關、資料名稱、授權條款版本）。`attribution()`
回傳的文字就是要附在畫面與匯出上的那一段。

## 存放（`data/official_doc/sources/<id>/`）

* `current/` —— 目前在用的那一份。範本解成 `templates/0001.odt…`（**檔名是
  我們編的**，zip 裡的路徑從來不會變成檔案系統路徑），對照在 `index.json`；
  地址簿存成 `orgs.json`（只留機關代碼與名稱）。
* 下載 / 上傳先寫進 `.staging-*`，**全部驗過才換上去**；失敗就把暫存刪掉，
  `current/` 一個位元組都不動（畫面講出失敗原因，舊資料照常可用）。
* `status.json` —— 最後一次嘗試與最後一次成功的紀錄。
* 這些都**可以重新下載**，所以不進設定備份（見
  `tools/check_settings_export_coverage.py` 的豁免）；**來源設定**
  （`official_doc_sources.json`，自訂的網址）才進。

## 地址簿怎麼查（量過才決定）

4 萬筆、全部名稱合計約 51 萬字。兩種做法在開發機上量（2026-10-07）：

| | 單純比對 | 常駐記憶體 | 準備 |
|---|---|---|---|
| **記憶體索引（採用）** | 5–13 ms | 約 6 MB | 第一次查詢讀檔 0.3 秒 |
| SQLite FTS5 逐字 | 1–6 ms | 0 | 每次下載建索引 1.7 秒 |

整支 `search_orgs()`（含代碼開頭比對與排序）拿正式的 40,093 筆實測
15–25 ms。差距是十幾毫秒，而記憶體索引是**真的逐字子字串比對**（英數也一樣；FTS5
的 unicode61 對英數是整詞，打 `A15` 查不到 `A15000000E`）、沒有索引版本要維護。
「台」「臺」與全形半形由 `cjk_fts.normalize()` 統一（跟知識庫同一套）。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from . import atomic_json, cjk_fts, safe_fetch, zip_guard
from .log_safe import safe_log
from ..config import settings
from ..logging_setup import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------- 常數

KIND_TEMPLATES = "templates_zip"
KIND_ADDRESS_BOOK = "address_book"
KINDS = (KIND_TEMPLATES, KIND_ADDRESS_BOOK)
#: 類型的顯示名稱（畫面用 `tr()` 包；英日譯文在語系檔）。
KIND_LABELS = {
    KIND_TEMPLATES: "公文範本（zip，內含 .odt）",
    KIND_ADDRESS_BOOK: "機關地址簿（JSON 或 CSV）",
}

#: 下載 / 上傳的大小上限。實際檔案：範本約 540 KB、地址簿約 5 MB。
MAX_BYTES = {KIND_TEMPLATES: 20 * 1024 * 1024, KIND_ADDRESS_BOOK: 50 * 1024 * 1024}
#: 範本 zip 解開後的總量上限 —— 88 份 odt 實際合計約 300 KB。
#: 傳給 `zip_guard` 的值比全站預設（1 GB）小得多：這裡的內容會整份寫進資料目錄。
TEMPLATES_MAX_UNCOMPRESSED = 200 * 1024 * 1024
ODT_MAX_BYTES = 20 * 1024 * 1024
ODT_MAX_UNCOMPRESSED = 50 * 1024 * 1024
MAX_ZIP_MEMBERS = 5000
MAX_TEMPLATES = 2000
MAX_ORGS = 500_000
MAX_SOURCES = 20

#: 連線（每次讀取）逾時與整次下載的上限。政府網站有時很慢（實測過 30 KB/s），
#: 5 MB 的地址簿要將近三分鐘，所以整次給 15 分鐘。
FETCH_TIMEOUT_S = 60.0
FETCH_DEADLINE_S = 900.0

LICENSE_OGDL_V1 = "政府資料開放授權條款－第1版"
LICENSE_OGDL_URL = "https://data.gov.tw/license"
_ARCHIVES = "國家發展委員會檔案管理局"

#: 內建來源。網址照官方原文、中文已編碼（`safe_fetch.normalize_public_url()`
#: 也會編，寫成編好的形式是為了讓設定檔與畫面上看到的一致）。
BUILTIN_SOURCES: tuple[dict, ...] = (
    {
        "id": "archives-templates",
        "name": "筆硯公文製作系統表單範本",
        "kind": KIND_TEMPLATES,
        "url": ("https://www.archives.gov.tw/opendata/"
                "Web%E7%89%88%E5%85%AC%E6%96%87%E8%A3%BD%E4%BD%9C%E7%B3%BB%E7%B5%B1"
                "%E8%A1%A8%E5%96%AE%E7%AF%84%E6%9C%AC.zip"),
        "dataset_url": "https://data.gov.tw/dataset/30943",
        "publisher": _ARCHIVES,
        "license": LICENSE_OGDL_V1,
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "archives-address-book",
        "name": "公文電子交換系統地址簿",
        "kind": KIND_ADDRESS_BOOK,
        "url": ("https://www.good.nat.gov.tw/regcenter/pub/addressbook/file/"
                "all_active_utf8.json"),
        "dataset_url": "https://data.gov.tw/dataset/7617",
        "publisher": _ARCHIVES,
        "license": LICENSE_OGDL_V1,
        "builtin": True,
        "enabled": True,
    },
)
_BUILTIN_BY_ID = {b["id"]: b for b in BUILTIN_SOURCES}
#: 內建來源可以改的欄位。名稱 / 提供機關 / 授權是**顯名的事實**，不給改；
#: 資料集搬家時改網址與資料集網頁就夠了。
_BUILTIN_EDITABLE = ("url", "dataset_url", "enabled")

_SID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
_FILE_RE = re.compile(r"^\d{4,6}\.odt$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

#: 伺服器端產生、會顯示在畫面上的固定訊息。畫面用 `tr()` 包（英日譯文在
#: 語系檔）—— 帶變數的部分用 `{0}`，前端的 `tr()` 會把數字換回去。
MESSAGES = {
    "not_found": "找不到這個資料來源",
    "busy": "這個來源正在下載或匯入中，請稍候再試",
    "too_many": "資料來源最多 {0} 個",
    "kind_bad": "資料類型不正確",
    "name_bad": "請填名稱（最多 80 字）",
    "unexpected": "處理失敗（未預期的錯誤，詳細原因已寫入服務記錄）",
}


class SourceError(ValueError):
    """設定或操作不合法；訊息是給管理員看的中文（我們自己寫的）。"""


class SourceNotFound(SourceError):
    pass


class SourceBusy(SourceError):
    pass


class SourceDataError(SourceError):
    """下載 / 上傳的檔案內容不合格（不是 zip、沒有可用的範本、不是地址簿…）。"""


# ---------------------------------------------------------------- 路徑

def _config_path() -> Path:
    return settings.data_dir / "official_doc_sources.json"


def _data_root() -> Path:
    return settings.data_dir / "official_doc"


def _sources_root() -> Path:
    return _data_root() / "sources"


def valid_source_id(sid: Any) -> bool:
    return isinstance(sid, str) and bool(_SID_RE.fullmatch(sid))


def _source_dir(sid: str) -> Path:
    """**只收驗過格式的 id** —— id 直接組進路徑。"""
    if not valid_source_id(sid):
        raise SourceNotFound(MESSAGES["not_found"])
    return _sources_root() / sid


def storage_dir() -> Path:
    """下載的檔案放在哪裡（給管理頁顯示）。"""
    return _sources_root()


# ---------------------------------------------------------------- 設定

_CFG_LOCK = threading.RLock()


def _clean_text(v: Any, max_len: int) -> str:
    s = _CTRL_RE.sub(" ", str(v or "")).strip()
    return re.sub(r"\s{2,}", " ", s)[:max_len]


def _clean_url(v: Any, *, required: bool) -> str:
    s = str(v or "").strip()
    if not s:
        if required:
            raise SourceError("請填下載網址")
        return ""
    try:
        return safe_fetch.normalize_public_url(s)
    except (safe_fetch.BlockedDestination, safe_fetch.UnsafeUrlScheme) as e:
        raise SourceError(f"網址不正確：{e}") from None


def _stored_record(item: Any) -> Optional[dict]:
    """設定檔裡的一筆 → 正規化的來源；不合法的回 `None`（丟掉、記一行）。"""
    if not isinstance(item, dict):
        return None
    sid = item.get("id")
    if not valid_source_id(sid):
        return None
    if sid in _BUILTIN_BY_ID:
        rec = dict(_BUILTIN_BY_ID[sid])
        for k in ("url", "dataset_url"):
            if item.get(k):
                try:
                    rec[k] = _clean_url(item.get(k), required=False) or rec[k]
                except SourceError:
                    pass
        if "enabled" in item:
            rec["enabled"] = bool(item.get("enabled"))
        return rec
    kind = item.get("kind")
    if kind not in KINDS:
        return None
    try:
        url = _clean_url(item.get("url"), required=True)
        dataset_url = _clean_url(item.get("dataset_url"), required=False)
    except SourceError:
        return None
    name = _clean_text(item.get("name"), 80)
    if not name:
        return None
    return {
        "id": sid, "name": name, "kind": kind, "url": url,
        "dataset_url": dataset_url,
        "publisher": _clean_text(item.get("publisher"), 80),
        "license": _clean_text(item.get("license"), 120),
        "builtin": False, "enabled": bool(item.get("enabled", True)),
    }


def list_sources() -> list[dict]:
    """目前的來源設定（內建在前、照設定檔的順序）。

    設定檔不存在 ＝ 從來沒改過 → 內建的兩個。**刪掉的內建來源不會自己回來**
    （設定檔存在就以它為準），要按「還原預設」。
    """
    with _CFG_LOCK:
        p = _config_path()
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return [dict(b) for b in BUILTIN_SOURCES]
        except (OSError, ValueError) as e:
            logger.warning("official doc sources config unreadable, using defaults: %s",
                           safe_log(repr(e)))
            return [dict(b) for b in BUILTIN_SOURCES]
        items = obj.get("sources") if isinstance(obj, dict) else None
        out: list[dict] = []
        seen: set[str] = set()
        for item in items if isinstance(items, list) else []:
            rec = _stored_record(item)
            if rec and rec["id"] not in seen:
                seen.add(rec["id"])
                out.append(rec)
        return out


def _save_sources(sources: list[dict]) -> None:
    keep = ("id", "name", "kind", "url", "dataset_url", "publisher", "license",
            "builtin", "enabled")
    atomic_json.write_json(_config_path(), {
        "version": 1,
        "sources": [{k: s.get(k) for k in keep} for s in sources],
    })


def get_source(sid: str) -> Optional[dict]:
    if not valid_source_id(sid):
        return None
    for s in list_sources():
        if s["id"] == sid:
            return s
    return None


def add_source(fields: dict) -> dict:
    """新增自訂來源。類型建立後不能改（兩種資料格式完全不同）。"""
    if not isinstance(fields, dict):
        raise SourceError("資料格式不正確")
    kind = fields.get("kind")
    if kind not in KINDS:
        raise SourceError(MESSAGES["kind_bad"])
    name = _clean_text(fields.get("name"), 80)
    if not name:
        raise SourceError(MESSAGES["name_bad"])
    rec = {
        "id": "custom-" + secrets.token_hex(4),
        "name": name, "kind": kind,
        "url": _clean_url(fields.get("url"), required=True),
        "dataset_url": _clean_url(fields.get("dataset_url"), required=False),
        "publisher": _clean_text(fields.get("publisher"), 80),
        "license": _clean_text(fields.get("license"), 120),
        "builtin": False,
        "enabled": bool(fields.get("enabled", True)),
    }
    with _CFG_LOCK:
        sources = list_sources()
        if len(sources) >= MAX_SOURCES:
            raise SourceError(MESSAGES["too_many"].replace("{0}", str(MAX_SOURCES)))
        sources.append(rec)
        _save_sources(sources)
    return rec


def update_source(sid: str, fields: dict) -> dict:
    """改一個來源。內建來源只改得動網址、資料集網頁與啟用。"""
    if not isinstance(fields, dict):
        raise SourceError("資料格式不正確")
    with _CFG_LOCK:
        sources = list_sources()
        for s in sources:
            if s["id"] != sid:
                continue
            allowed = _BUILTIN_EDITABLE if s.get("builtin") else (
                "name", "url", "dataset_url", "publisher", "license", "enabled")
            for k in allowed:
                if k not in fields:
                    continue
                if k == "url":
                    s["url"] = _clean_url(fields["url"], required=True)
                elif k == "dataset_url":
                    s["dataset_url"] = _clean_url(fields["dataset_url"], required=False)
                elif k == "enabled":
                    s["enabled"] = bool(fields["enabled"])
                elif k == "name":
                    name = _clean_text(fields["name"], 80)
                    if not name:
                        raise SourceError(MESSAGES["name_bad"])
                    s["name"] = name
                elif k == "publisher":
                    s["publisher"] = _clean_text(fields["publisher"], 80)
                elif k == "license":
                    s["license"] = _clean_text(fields["license"], 120)
            _save_sources(sources)
            return dict(s)
    raise SourceNotFound(MESSAGES["not_found"])


def delete_source(sid: str) -> None:
    """刪掉一個來源，**連同已下載的資料**。下載中不可以刪。"""
    with _RUN_LOCK:
        if sid in _RUNNING:
            raise SourceBusy(MESSAGES["busy"])
        with _CFG_LOCK:
            sources = list_sources()
            gone = [s for s in sources if s["id"] == sid]
            if not gone:
                raise SourceNotFound(MESSAGES["not_found"])
            _save_sources([s for s in sources if s["id"] != sid])
        # 路徑一律用**設定檔裡**的 id 組（使用者送來的字串只拿來比對）
        real_id = gone[0]["id"]
        with _IO_LOCK:
            shutil.rmtree(_source_dir(real_id), ignore_errors=True)
            _forget_caches(real_id)


def restore_defaults() -> list[dict]:
    """把內建的來源放回來（網址、資料集網頁、啟用都回到預設）。

    **不動自訂的來源**，也不刪任何已下載的資料 —— 內建來源先前下載過的話，
    資料照常可用。
    """
    with _CFG_LOCK:
        sources = list_sources()
        custom = [s for s in sources if s["id"] not in _BUILTIN_BY_ID]
        restored = [dict(b) for b in BUILTIN_SOURCES]
        out = restored + custom
        _save_sources(out)
        return out


def kind_options() -> list[dict]:
    return [{"id": k, "label": KIND_LABELS[k],
             "max_mb": MAX_BYTES[k] // (1024 * 1024)} for k in KINDS]


# ---------------------------------------------------------------- 狀態

_RUN_LOCK = threading.Lock()
#: 正在下載 / 匯入的來源 → 進度。**只在記憶體裡**：服務重啟時進行中的下載
#: 本來就沒了，持久化的話畫面會永遠停在「下載中」。
_RUNNING: dict[str, dict] = {}
_IO_LOCK = threading.RLock()


def _status_path(sid: str) -> Path:
    return _source_dir(sid) / "status.json"


def _read_status(sid: str) -> dict:
    try:
        obj = json.loads(_status_path(sid).read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError, SourceError):
        return {}


def _record_attempt(sid: str, *, ok: bool, origin: str, message: str,
                    info: Optional[dict] = None) -> None:
    if get_source(sid) is None:     # 下載途中被刪掉了 —— 不要把資料夾建回來
        return
    st = _read_status(sid)
    now = time.time()
    st["last_attempt"] = {"at": now, "ok": bool(ok), "origin": origin,
                          "message": message}
    if ok and info is not None:
        st["last_ok"] = {"at": now, "origin": origin, **info}
    atomic_json.write_json(_status_path(sid), st)


def _installed_index(sid: str) -> Optional[dict]:
    p = _source_dir(sid) / "current" / "index.json"
    return _cached_json(("index", sid), p)


def attribution_text(src: dict) -> str:
    """一個來源的顯名文字（政府資料開放授權條款要求：提供機關、資料名稱、
    授權條款版本）。自訂來源沒填授權時就只寫出處。"""
    parts = []
    pub = src.get("publisher") or ""
    name = src.get("name") or ""
    parts.append(f"{pub}「{name}」" if pub else f"「{name}」")
    if src.get("dataset_url"):
        parts.append(f"（{src['dataset_url']}）")
    lic = src.get("license") or ""
    if lic == LICENSE_OGDL_V1:
        parts.append(f"，依「{lic}」（{LICENSE_OGDL_URL}）使用。")
    elif lic:
        parts.append(f"，依「{lic}」使用。")
    else:
        parts.append("。")
    return "".join(parts)


def status() -> dict:
    """管理頁要的全部狀態。"""
    rows = []
    for s in list_sources():
        sid = s["id"]
        idx = _installed_index(sid)
        st = _read_status(sid)
        with _RUN_LOCK:
            run = dict(_RUNNING[sid]) if sid in _RUNNING else None
        rows.append({
            **s,
            "kind_label": KIND_LABELS.get(s["kind"], s["kind"]),
            "installed": idx is not None,
            "count": (idx or {}).get("count", 0),
            "skipped_count": len((idx or {}).get("skipped") or []),
            "installed_at": (idx or {}).get("installed_at"),
            "running": run is not None,
            "progress": run,
            "last_attempt": st.get("last_attempt"),
            "last_ok": st.get("last_ok"),
            "attribution": attribution_text(s),
            "max_mb": MAX_BYTES[s["kind"]] // (1024 * 1024),
        })
    return {"sources": rows, "storage_dir": str(storage_dir()),
            "license_url": LICENSE_OGDL_URL}


# ---------------------------------------------------------------- 下載 / 上傳

def start_download(sid: str) -> None:
    """在背景下載一個來源（立刻回來；畫面輪詢 `status()` 看進度）。"""
    src = get_source(sid)
    if src is None:
        raise SourceNotFound(MESSAGES["not_found"])
    with _RUN_LOCK:
        if sid in _RUNNING:
            raise SourceBusy(MESSAGES["busy"])
        _RUNNING[sid] = {"started_at": time.time(), "received": 0, "total": None,
                         "stage": "download"}
    t = threading.Thread(target=_download_worker, args=(dict(src),),
                         name=f"official-doc-dl-{sid}", daemon=True)
    try:
        t.start()
    except Exception:
        with _RUN_LOCK:
            _RUNNING.pop(sid, None)
        raise


def download_now(sid: str) -> dict:
    """同步版（測試與排查用）：下載、驗證、換上去，回結果。"""
    src = get_source(sid)
    if src is None:
        raise SourceNotFound(MESSAGES["not_found"])
    with _RUN_LOCK:
        if sid in _RUNNING:
            raise SourceBusy(MESSAGES["busy"])
        _RUNNING[sid] = {"started_at": time.time(), "received": 0, "total": None,
                         "stage": "download"}
    return _download_worker(dict(src))


def _download_worker(src: dict) -> dict:
    sid = src["id"]
    try:
        def _progress(received: int, total: Optional[int]) -> None:
            with _RUN_LOCK:
                if sid in _RUNNING:
                    _RUNNING[sid].update(received=received, total=total)

        try:
            res = safe_fetch.fetch_public(
                src["url"], max_bytes=MAX_BYTES[src["kind"]],
                timeout=FETCH_TIMEOUT_S, deadline_s=FETCH_DEADLINE_S,
                progress=_progress)
            with _RUN_LOCK:
                if sid in _RUNNING:
                    _RUNNING[sid]["stage"] = "install"
            info = _install(src, res.data, origin="download", final_url=res.final_url)
        except Exception as e:      # noqa: BLE001 — 每一種失敗都要寫進狀態
            msg = _friendly(e)
            logger.warning("official doc source %s download failed: %s",
                           safe_log(sid), safe_log(repr(e), max_len=400))
            _record_attempt(sid, ok=False, origin="download", message=msg)
            return {"ok": False, "message": msg}
        msg = _ok_message(src, info)
        _record_attempt(sid, ok=True, origin="download", message=msg, info=info)
        logger.info("official doc source %s downloaded: %s items",
                    safe_log(sid), info.get("count"))
        return {"ok": True, "message": msg, **info}
    finally:
        with _RUN_LOCK:
            _RUNNING.pop(sid, None)


def install_upload(sid: str, data: bytes, filename: str = "") -> dict:
    """管理員手動上傳同一種檔案（不能連外的機關用）。**跟下載同一套驗證**。

    驗不過丟 `SourceDataError`（訊息給人看），舊資料保留。
    """
    src = get_source(sid)
    if src is None:
        raise SourceNotFound(MESSAGES["not_found"])
    with _RUN_LOCK:
        if sid in _RUNNING:
            raise SourceBusy(MESSAGES["busy"])
        _RUNNING[sid] = {"started_at": time.time(), "received": len(data or b""),
                         "total": len(data or b""), "stage": "install"}
    try:
        try:
            info = _install(src, data or b"", origin="upload",
                            filename=_clean_text(os.path.basename(
                                str(filename or "").replace("\\", "/")), 120))
        except Exception as e:      # noqa: BLE001
            msg = _friendly(e)
            logger.warning("official doc source %s upload rejected: %s",
                           safe_log(sid), safe_log(repr(e), max_len=400))
            _record_attempt(src["id"], ok=False, origin="upload", message=msg)
            raise SourceDataError(msg) from None
        msg = _ok_message(src, info)
        _record_attempt(src["id"], ok=True, origin="upload", message=msg, info=info)
        return {"ok": True, "message": msg, **info}
    finally:
        with _RUN_LOCK:
            _RUNNING.pop(sid, None)


def _friendly(e: BaseException) -> str:
    if isinstance(e, (SourceError, safe_fetch.BlockedDestination,
                      safe_fetch.UnsafeUrlScheme, safe_fetch.FetchFailed)):
        return str(e)
    return MESSAGES["unexpected"]


def _ok_message(src: dict, info: dict) -> str:
    if src["kind"] == KIND_TEMPLATES:
        msg = f"已取得 {info.get('count', 0)} 份範本"
        if info.get("skipped"):
            msg += f"，略過 {len(info['skipped'])} 個檔案"
        return msg
    return f"已取得 {info.get('count', 0)} 個機關"


def _install(src: dict, raw: bytes, *, origin: str, final_url: str = "",
             filename: str = "") -> dict:
    """驗證 → 寫進暫存 → **全部成功才換上去**。任何一步失敗，`current/` 不動。"""
    kind = src["kind"]
    if not raw:
        raise SourceDataError("檔案是空的")
    if len(raw) > MAX_BYTES[kind]:
        raise SourceDataError(
            f"檔案太大（上限 {MAX_BYTES[kind] // (1024 * 1024)} MB）")
    sdir = _source_dir(src["id"])
    sdir.mkdir(parents=True, exist_ok=True)
    _sweep_leftovers(sdir)
    staging = sdir / f".staging-{secrets.token_hex(6)}"
    staging.mkdir()
    try:
        if kind == KIND_TEMPLATES:
            info = _build_templates(raw, staging)
        else:
            info = _build_address_book(raw, staging)
        info.update({
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "origin": origin,
            "installed_at": time.time(),
        })
        if final_url:
            info["final_url"] = final_url
        if filename:
            info["filename"] = filename
        atomic_json.write_json(staging / "index.json", info)
        with _IO_LOCK:
            cur = sdir / "current"
            old = None
            if cur.exists():
                old = sdir / f".old-{secrets.token_hex(6)}"
                os.replace(cur, old)
            try:
                os.replace(staging, cur)
            except OSError:
                if old is not None:
                    os.replace(old, cur)
                raise
            _forget_caches(src["id"])
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    summary = {k: info[k] for k in ("count", "sha256", "size", "origin")}
    summary["skipped"] = list(info.get("skipped") or [])[:50]
    for k in ("final_url", "filename"):
        if info.get(k):
            summary[k] = info[k]
    return summary


def _sweep_leftovers(sdir: Path) -> None:
    """上一次被中斷時留下的暫存（服務在換檔途中被殺掉）。"""
    for p in sdir.glob(".staging-*"):
        shutil.rmtree(p, ignore_errors=True)
    for p in sdir.glob(".old-*"):
        shutil.rmtree(p, ignore_errors=True)


# ---------------------------------------------------------------- 範本 zip

def _decode_member_name(info: zipfile.ZipInfo) -> str:
    """zip 裡的檔名要自己解碼。

    沒設 UTF-8 旗標（0x800）的檔名，Python 一律用 cp437 解 —— 而官方那份是
    **Big5**，cp437 解出來是「ñ@»δñ╜ñσ¬φ│µ」這種東西。還原成原始位元組再依序
    試 UTF-8（有些工具寫 UTF-8 卻不設旗標）、cp950、big5hkscs。
    """
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "cp950", "big5hkscs"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def _member_parts(name: str) -> Optional[list[str]]:
    """zip 成員路徑 → 各層名稱。路徑不安全（`..`、空的）回 `None`。

    這些名稱**只拿來顯示與比對**，永遠不會變成檔案系統的路徑。
    """
    s = _CTRL_RE.sub("", name.replace("\\", "/"))
    parts = [p.strip() for p in s.split("/") if p.strip() not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return parts


def _odt_problem(data: bytes) -> Optional[str]:
    """這份 .odt 能不能用；不能用回原因。"""
    if not data.startswith(b"PK"):
        return "不是 ODF 文件"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            zip_guard.check(z, max_uncompressed=ODT_MAX_UNCOMPRESSED)
            names = set(z.namelist())
            if "content.xml" not in names:
                return "缺少 content.xml"
            if "mimetype" in names:
                mt = z.read("mimetype")[:200].strip()
                if not mt.startswith(b"application/vnd.oasis.opendocument.text"):
                    return "不是文書文件（odt）"
            if z.testzip() is not None:
                return "檔案毀損（CRC 不符）"
    except zip_guard.ZipBombError:
        return "解開後異常龐大"
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError,
            ValueError, NotImplementedError):
        return "檔案毀損"
    return None


def _build_templates(raw: bytes, staging: Path) -> dict:
    if not raw.startswith(b"PK"):
        raise SourceDataError("不是 zip 檔（公文範本來源要的是內含 .odt 的 zip）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
        raise SourceDataError("zip 檔毀損，打不開") from None
    tdir = staging / "templates"
    tdir.mkdir()
    entries: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    with zf:
        try:
            zip_guard.check(zf, max_uncompressed=TEMPLATES_MAX_UNCOMPRESSED)
        except zip_guard.ZipBombError as e:
            raise SourceDataError(str(e)) from None
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_MEMBERS:
            raise SourceDataError(f"zip 裡的檔案太多（超過 {MAX_ZIP_MEMBERS} 個）")
        for info in infos:
            if info.is_dir():
                continue
            name = _decode_member_name(info)
            parts = _member_parts(name)
            shown = _clean_text(name, 200)
            if parts is None:
                skipped.append({"path": shown, "reason": "路徑不安全"})
                continue
            path = "/".join(parts)
            if not parts[-1].lower().endswith(".odt"):
                skipped.append({"path": path, "reason": "不是 .odt"})
                continue
            if path in seen:
                skipped.append({"path": path, "reason": "重複"})
                continue
            if info.file_size > ODT_MAX_BYTES:
                skipped.append({"path": path, "reason": "檔案太大"})
                continue
            if len(entries) >= MAX_TEMPLATES:
                skipped.append({"path": path, "reason": "範本數量超過上限"})
                continue
            try:
                with zf.open(info) as fh:
                    data = fh.read(ODT_MAX_BYTES + 1)
            except (zipfile.BadZipFile, OSError, EOFError, ValueError,
                    NotImplementedError, RuntimeError):
                skipped.append({"path": path, "reason": "檔案毀損"})
                continue
            if len(data) > ODT_MAX_BYTES:
                skipped.append({"path": path, "reason": "檔案太大"})
                continue
            problem = _odt_problem(data)
            if problem:
                skipped.append({"path": path, "reason": problem})
                continue
            seen.add(path)
            fname = f"{len(entries) + 1:04d}.odt"
            (tdir / fname).write_bytes(data)
            stem = parts[-1][:-4].strip() or parts[-1]
            entries.append({
                "category": parts[-2] if len(parts) >= 2 else "",
                "name": stem,
                "path": path,
                "file": fname,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            })
    if not entries:
        raise SourceDataError(
            f"zip 裡沒有任何可以用的 .odt 範本（略過 {len(skipped)} 個檔案）")
    return {"kind": KIND_TEMPLATES, "count": len(entries), "templates": entries,
            "skipped": skipped}


# ---------------------------------------------------------------- 地址簿

_NAME_KEYS = ("orgname", "org_name", "name", "機關名稱", "機關全銜", "全銜")
_ID_KEYS = ("orgid", "org_id", "id", "機關代碼", "代碼")


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise SourceDataError("看不出文字編碼（地址簿要是 UTF-8 或 Big5 的 JSON / CSV）")


def _pick(d: dict, keys: tuple[str, ...]) -> Any:
    low = {str(k).strip().lower(): v for k, v in d.items()}
    for k in keys:
        if k in low:
            return low[k]
    return None


def _rows_from_json(text: str) -> list[tuple[Any, Any]]:
    try:
        obj = json.loads(text)
    except ValueError:
        raise SourceDataError("JSON 格式不正確") from None
    rows = None
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                rows = v
                break
    if not isinstance(rows, list):
        raise SourceDataError("看不出是地址簿：JSON 裡找不到機關清單")
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append((_pick(r, _ID_KEYS), _pick(r, _NAME_KEYS)))
        else:
            out.append((None, None))
    return out


def _rows_from_csv(text: str) -> list[tuple[Any, Any]]:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise SourceDataError("檔案是空的") from None
    except csv.Error:
        raise SourceDataError("CSV 格式不正確") from None
    low = [h.strip().lower() for h in header]
    name_i = next((low.index(k) for k in _NAME_KEYS if k in low), None)
    id_i = next((low.index(k) for k in _ID_KEYS if k in low), None)
    if name_i is None:
        raise SourceDataError("看不出是地址簿：找不到機關名稱欄位（ORGNAME）")
    out = []
    try:
        for row in reader:
            if not row:
                continue
            out.append((row[id_i] if id_i is not None and id_i < len(row) else None,
                        row[name_i] if name_i < len(row) else None))
    except csv.Error:
        raise SourceDataError("CSV 格式不正確") from None
    return out


def _build_address_book(raw: bytes, staging: Path) -> dict:
    if raw.startswith(b"PK"):
        raise SourceDataError("這是 zip 檔，不是地址簿（要 JSON 或 CSV）")
    text = _decode_text(raw)
    head = text.lstrip()[:1]
    rows = _rows_from_json(text) if head in ("[", "{") else _rows_from_csv(text)
    if len(rows) > MAX_ORGS:
        raise SourceDataError(f"機關數量超過上限（{MAX_ORGS} 筆）")
    orgs: list[list[str]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    no_name = 0
    for oid, oname in rows:
        name = _clean_text(oname, 200) if isinstance(oname, (str, int)) else ""
        if not name:
            no_name += 1
            continue
        org_id = _clean_text(oid, 40) if isinstance(oid, (str, int)) else ""
        if org_id:
            if org_id in seen_ids:
                continue
            seen_ids.add(org_id)
        elif name in seen_names:
            continue
        seen_names.add(name)
        orgs.append([org_id, name])
    if not orgs:
        raise SourceDataError("看不出是地址簿：沒有任何一筆有機關名稱")
    atomic_json.write_json(staging / "orgs.json",
                           {"count": len(orgs), "orgs": orgs}, indent=0)
    skipped = ([{"path": "", "reason": f"{no_name} 筆沒有機關名稱"}] if no_name else [])
    return {"kind": KIND_ADDRESS_BOOK, "count": len(orgs), "skipped": skipped}


# ---------------------------------------------------------------- 快取

#: 讀過的 JSON（範本索引、地址簿）→ 依檔案的 inode / mtime / 大小失效。
#: 換上新資料時整個資料夾換掉，三者一定會變。
_CACHE: dict[tuple, tuple] = {}
_CACHE_LOCK = threading.Lock()


def _stamp(p: Path) -> Optional[tuple]:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _cached_json(key: tuple, p: Path) -> Optional[dict]:
    stamp = _stamp(p)
    if stamp is None:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
        return None
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    with _CACHE_LOCK:
        _CACHE[key] = (stamp, obj)
    return obj


def _forget_caches(sid: str) -> None:
    with _CACHE_LOCK:
        for k in [k for k in _CACHE if len(k) > 1 and k[1] == sid]:
            _CACHE.pop(k, None)


def _org_index(sid: str) -> Optional[tuple]:
    """地址簿的搜尋索引：(代碼, 名稱, 正規化名稱, 正規化代碼) 四個平行串列。"""
    p = _source_dir(sid) / "current" / "orgs.json"
    stamp = _stamp(p)
    key = ("orgidx", sid)
    if stamp is None:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
        return None
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        orgs = obj.get("orgs") if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None
    if not isinstance(orgs, list):
        return None
    ids, names, norms, nids = [], [], [], []
    for row in orgs:
        if not (isinstance(row, list) and len(row) == 2):
            continue
        oid, name = str(row[0] or ""), str(row[1] or "")
        if not name:
            continue
        ids.append(oid)
        names.append(name)
        norms.append(cjk_fts.normalize(name))
        nids.append(oid.lower())
    idx = (ids, names, norms, nids)
    with _CACHE_LOCK:
        _CACHE[key] = (stamp, idx)
    return idx


# ---------------------------------------------------------------- 給工具用的介面

def _enabled_installed(kind: str) -> list[dict]:
    return [s for s in list_sources()
            if s["kind"] == kind and s.get("enabled")
            and _installed_index(s["id"]) is not None]


def list_templates() -> list[dict]:
    """已下載、啟用中的範本：`[{"source_id","category","name","path"}]`。"""
    out: list[dict] = []
    for s in _enabled_installed(KIND_TEMPLATES):
        idx = _installed_index(s["id"]) or {}
        for e in idx.get("templates") or []:
            if isinstance(e, dict):
                out.append({"source_id": s["id"], "category": e.get("category", ""),
                            "name": e.get("name", ""), "path": e.get("path", "")})
    return out


def templates_of(sid: str) -> dict:
    """管理頁「列出範本」用：不看啟用與否（管理員要看得到下載了什麼）。"""
    src = get_source(sid)
    if src is None:
        raise SourceNotFound(MESSAGES["not_found"])
    idx = _installed_index(src["id"]) or {}
    return {
        "templates": [{"category": e.get("category", ""), "name": e.get("name", ""),
                       "path": e.get("path", ""), "size": e.get("size", 0)}
                      for e in idx.get("templates") or [] if isinstance(e, dict)],
        "skipped": list(idx.get("skipped") or []),
    }


def get_template(source_id: str, name: str) -> Optional[bytes]:
    """取一份範本的 .odt。`name` 可以是名稱（「函」）或 zip 裡的路徑
    （「一般公文表單/函.odt」）；名稱重複時取索引裡的第一份。

    來源不存在、沒下載、停用中、或找不到 → `None`。
    """
    if not valid_source_id(source_id) or not isinstance(name, str) or not name:
        return None
    src = get_source(source_id)
    if src is None or src["kind"] != KIND_TEMPLATES or not src.get("enabled"):
        return None
    real_id = src["id"]       # 路徑用設定檔裡的 id 組
    idx = _installed_index(real_id) or {}
    entries = [e for e in idx.get("templates") or [] if isinstance(e, dict)]
    hit = next((e for e in entries if e.get("path") == name), None) or \
        next((e for e in entries if e.get("name") == name), None)
    if hit is None:
        return None
    fname = str(hit.get("file") or "")
    if not _FILE_RE.fullmatch(fname):       # 檔名是我們編的；不符就是被動過
        return None
    with _IO_LOCK:
        try:
            return (_source_dir(real_id) / "current" / "templates" / fname).read_bytes()
        except OSError:
            return None


def match_marks(text: str, terms: list[str]) -> list[list[int]]:
    """`text` 裡符合 `terms`（已經 `cjk_fts.normalize` 過）的位置，回原文的字元位置
    `[[開始, 結束), …]`（以 code point 計，畫面要用 `Array.from` 切）。

    逐字正規化再比對，所以「台」對得到「臺」、全形對得到半形；重疊或相鄰的併成一段。
    給畫面標出符合處用（2026-10-09 使用者：「搜尋有符合的 該字串要高亮」）。"""
    if not text or not terms:
        return []
    norm, owner = [], []
    for i, ch in enumerate(text):
        n = cjk_fts.normalize(ch)
        norm.append(n)
        owner.extend([i] * len(n))
    joined = "".join(norm)
    spans: list[list[int]] = []
    for term in terms:
        if not term:
            continue
        start = joined.find(term)
        while start >= 0:
            end = start + len(term)
            spans.append([owner[start], owner[end - 1] + 1])
            start = joined.find(term, start + 1)
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


#: 「全部顯示」一次最多列幾筆。正式的地址簿約 4 萬筆，查一兩個字（「股份」「協會」）就是好幾千筆 ——
#: 全部畫出來沒有人看得完，所以有上限，超過時畫面講出總共幾筆、請使用者多打幾個字。
ORG_SEARCH_MAX = 2000


def search_orgs(q: str, limit: int = 20) -> list[dict]:
    """查機關（逐字比對；啟用中的地址簿才查）：`[{"orgId","orgName"}]`。總筆數見 `search_orgs_page`。"""
    return search_orgs_page(q, limit)["results"]


def search_orgs_page(q: str, limit: int = 20) -> dict:
    """查機關：`{"results": [{"orgId","orgName","nameMarks","idMarks"}], "total": 符合的總筆數}`。

    * 空白分開的幾個詞要**全部**出現（AND）。
    * 「台」「臺」、全形半形視為相同（`cjk_fts.normalize`）。
    * 只打一個詞時也比對機關代碼的開頭（`A15` → `A15000000E`）。
    * 每一筆另附 `nameMarks` / `idMarks`：符合處的字元位置（畫面標亮用，見 `match_marks`）。
    * 排序：名稱**完全相同** → **主機關**（代碼 10 碼）→ 內部單位（主機關代碼後面再接 7 碼的
      人事室、會計室…）；同一級裡以查詢開頭的在前、名稱短的在前、再依代碼長度。
      主機關排在內部單位前面是 2026-10-10 加的：原本「以查詢開頭」優先，查「財政」時
      財政部底下的人事處、會計處…把前 20 筆占滿，臺北市政府財政局排到第 305 筆（正式地址簿實測），
      查「數位」時臺中市政府數位發展局排在第 68 筆；改了之後各是第 57、37 筆 ——
      還是在 20 筆之外，所以畫面另外講出總筆數、可以「全部顯示」。
      把公司排到機關後面也試過，查「中華電信」時公司本身被一堆托育中心擠下去，沒有採用。
    """
    if not isinstance(q, str):
        return {"results": [], "total": 0}
    terms = [cjk_fts.normalize(t) for t in q.split() if t.strip()]
    terms = [t for t in terms if t]
    if not terms:
        return {"results": [], "total": 0}
    try:
        limit = max(1, min(int(limit), ORG_SEARCH_MAX))
    except (TypeError, ValueError):
        limit = 20
    joined = "".join(terms)
    first = terms[0]
    single = len(terms) == 1
    found: list[tuple] = []
    seen: set[str] = set()
    for s in _enabled_installed(KIND_ADDRESS_BOOK):
        idx = _org_index(s["id"])
        if not idx:
            continue
        ids, names, norms, nids = idx
        hits = [i for i, n in enumerate(norms) if first in n]
        for t in terms[1:]:
            hits = [i for i in hits if t in norms[i]]
        if single:
            extra = [i for i, d in enumerate(nids) if d and d.startswith(first)]
            if extra:
                hits = sorted(set(hits) | set(extra))
        for i in hits:
            key = ids[i] or ("name:" + names[i])
            if key in seen:
                continue
            seen.add(key)
            n = norms[i]
            exact = 0 if (n == joined or nids[i] == first) else 1
            sub = 1 if len(ids[i] or "") > 10 else 0
            prefix = 0 if n.startswith(first) else 1
            found.append((exact, sub, prefix, len(n), len(ids[i]), n, ids[i], names[i]))
    found.sort()
    out = []
    for r in found[:limit]:
        oid, name = r[6], r[7]
        item = {"orgId": oid, "orgName": name, "nameMarks": match_marks(name, terms)}
        # 只打一個詞、比對到代碼開頭時，代碼前面那一段也標出來
        item["idMarks"] = (match_marks(oid, [first])[:1]
                           if single and cjk_fts.normalize(oid or "").startswith(first) else [])
        out.append(item)
    return {"results": out, "total": len(found)}


def _org_maps(sid: str) -> Optional[tuple[dict, dict]]:
    """(正規化名稱 → 代碼集合, 正規化代碼 → 正規化名稱)，跟著 `_org_index` 一起失效。"""
    idx = _org_index(sid)
    if not idx:
        return None
    key = ("orgmap", sid)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] is idx:
            return hit[1]
    ids, _names, norms, nids = idx
    by_name: dict[str, set] = {}
    by_code: dict[str, str] = {}
    for oid, norm, nid in zip(ids, norms, nids):
        if not oid:
            continue
        by_name.setdefault(norm, set()).add(oid)
        by_code.setdefault(nid, norm)
    maps = (by_name, by_code)
    with _CACHE_LOCK:
        _CACHE[key] = (idx, maps)
    return maps


def exact_org_code(name: str) -> str:
    """名稱**完全相同**（「台」「臺」、全形半形視為相同）而且只對到**一個**代碼 → 那個代碼；
    找不到、同名有好幾個、地址簿沒下載 → 空字串。

    電子公文的機關代碼填錯，公文會交換到別的機關或被退件 —— **比空著糟**，所以不猜：
    只認名稱一字不差的那一筆，名稱的一部分或相近的都不算。"""
    if not isinstance(name, str):
        return ""
    norm = cjk_fts.normalize(name.strip())
    if not norm:
        return ""
    codes: set = set()
    for s in _enabled_installed(KIND_ADDRESS_BOOK):
        maps = _org_maps(s["id"])
        if maps:
            codes |= maps[0].get(norm, set())
    return next(iter(codes)) if len(codes) == 1 else ""


def org_code_matches(name: str, code: str) -> bool:
    """這個代碼在地址簿裡**就是這個名稱**（畫面送來的「名稱 → 代碼」存進案件之前驗一次，
    不讓前端送一個對不上的代碼進來）。"""
    if not isinstance(name, str) or not isinstance(code, str):
        return False
    norm, nid = cjk_fts.normalize(name.strip()), code.strip().lower()
    if not norm or not nid:
        return False
    for s in _enabled_installed(KIND_ADDRESS_BOOK):
        maps = _org_maps(s["id"])
        if maps and maps[1].get(nid) == norm:
            return True
    return False


def address_book_info() -> dict:
    """工具頁的地址簿提醒用：`{"installed", "updated_at", "count"}`。只讀狀態、不連外。

    `updated_at` 是最後一次**成功**取得的時間（狀態檔讀不到就用 `orgs.json` 的修改時間）；
    有好幾個啟用中的地址簿時取最新的那個 —— 提醒的是「手上有沒有夠新的資料可以查」。"""
    srcs = _enabled_installed(KIND_ADDRESS_BOOK)
    if not srcs:
        return {"installed": False, "updated_at": None, "count": 0}
    stamps: list[float] = []
    count = 0
    for s in srcs:
        at = (_read_status(s["id"]).get("last_ok") or {}).get("at")
        if not isinstance(at, (int, float)) or at <= 0:
            try:
                at = (_source_dir(s["id"]) / "current" / "orgs.json").stat().st_mtime
            except (OSError, SourceError):
                at = None
        if at:
            stamps.append(float(at))
        idx = _org_index(s["id"])
        count += len(idx[0]) if idx else 0
    return {"installed": True, "updated_at": max(stamps) if stamps else None, "count": count}


def attribution(kind: Optional[str] = None) -> list[str]:
    """目前**有下載而且啟用**的來源的顯名文字（畫面與匯出要附）。給 `kind` 就只列那一種
    （畫面上用到範本的地方只列範本的出處，用到地址簿的地方只列地址簿的）。"""
    return [attribution_text(s) for s in list_sources()
            if s.get("enabled") and _installed_index(s["id"]) is not None
            and (kind is None or s["kind"] == kind)]


def has_address_book() -> bool:
    """有沒有下載好、啟用中的地址簿（沒有的話工具頁不出現「查機關名稱」）。"""
    return bool(_enabled_installed(KIND_ADDRESS_BOOK))
