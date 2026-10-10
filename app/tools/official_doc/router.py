"""公文撰擬的端點。

**這支只負責「接成一支工具」** —— 格式、提示、事實檢查全在 `app/core/official_doc.py`
（那支不認識任何 LLM 用戶端，也不認識 HTTP）。這裡做的是：

* 把畫面送來的欄位**照白名單收**：長度超過上限就 400、**不截斷**（截掉的部分可能
  正好是期限或條件，而截斷之後草稿看起來完全正常）；選項不在清單裡也 400。
* 背景作業（同會議摘要：關掉分頁也跑得完、「我的作業」開得回來）。
* **案件自己保存**（歷史案件管理，`case_store`）：輸入、草稿、版本放在
  `data/official_doc_cases/<案件編號>/`，**不跟著暫存的保留期走**；擁有者記在案件裡，
  權限一律走 `_require_case()`。「我的作業」的「開啟」是 `?case=<案件編號>`
  （按案件定址，作業紀錄過期之後也打得開），「歷史案件」頁列出自己寫過的每一份。
* 使用者改過的草稿可以**再檢查一次**（不呼叫模型）、照目前的文字匯出。
* **逐段改寫**（`/rewrite`）：改寫的依據跟整份草稿一樣（使用者給的內容＋確認過的資料），
  回改寫後的文字與檢查結果 —— **不寫進任何地方**，使用者在畫面上看過差異、按「採用」
  才換進草稿，並存成新的一版。
* **版本記錄**（`/revisions`）：每個案件一份清單（第一版是模型產生的）。存新版要帶
  「我是從第幾版改的」（`base_rev`），**不是最新版就 409**，回最新版的內容 —— 兩個分頁
  同時改的時候不可以安靜地蓋掉另一邊（原規格 E02）。

「依修改後的資料重新產生」那條路，瀏覽器會把上一次的資料表送回來 ——
**那是不可信的輸入**，一律先過 `official_doc.sanitize_facts`（標成「原文有」的，
附的原文要真的在使用者的內容裡找得到，不然降成「推論」）。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ...config import settings
from ...core import atomic_json
from ...core import official_doc as od
from ...core import safe_paths as _sp, upload_owner as _uo
from ...core.http_utils import content_disposition
from ...core.job_manager import job_manager
from ...core.llm_settings import llm_settings
from ...logging_setup import get_logger
from ...core import official_doc_cases as _cs
from . import examples as _examples

logger = get_logger(__name__)
router = APIRouter()

TOOL_ID = "official-doc"

#: 使用者改過之後送回來檢查 / 匯出的草稿上限。草稿是我們產生的（一份簽不會超過
#: 幾千字），這個上限只是擋「把整本書貼進來」。
MAX_EDIT_CHARS = 30_000
#: 「從檔案帶入文字」的檔案上限（**只抽文字**，不留檔案）。
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
EXTRACT_EXTS = (".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md")
ENDORSE_FORMATS = ("compact", "list")
EXPORT_FORMATS = ("txt", "odt", "docx", "pdf", "png", "svg", "json", "di")
#: 圖片匯出：跟 PDF 同一個版面（同一條路產生 PDF 再算圖）；多頁時一頁一張、打包成 zip
IMAGE_FORMATS = ("png", "svg")
IMAGE_DPI = 200
IMAGE_MAX_PAGES = 30
#: 匯出的 JSON 帶這個標記（之後要讀回來時才認得出是本工具匯出的）
EXPORT_FORMAT = "jtdt-official-doc"
#: 機關地址簿超過幾天沒更新就提醒（2026-10-09 使用者核可 30 天：官方每天更新，機關改制改名不會天天有）
ORG_STALE_DAYS = 30
#: 「機關名稱 → 機關代碼」一份案件最多記幾筆（發文機關、受文者、正本、副本加起來）
MAX_ORG_CODES = 40
#: 機關代碼的長相：英數 2~32 字（地址簿裡實際是 8~17 字，例如 A15000000E、200000000AUA00000）
_ORG_CODE_RE = re.compile(r"^[0-9A-Za-z]{2,32}$")
#: 正本、副本一格好幾個機關時的分隔（跟頁面上的元件同一組）
_ORG_SEP_RE = re.compile(r"[、，,；;]")
EXPORT_VERSION = 1
#: 歷史案件的 DI 檔（2026-10-10 使用者：「歷史案件功能 裡面要提供下載 di 檔功能 還要可以批次下載 或上傳」）。
#: 批次下載一次最多幾件、上傳一次最多幾份 DI 檔（壓縮檔裡的一份也算一份）。
DI_BATCH_MAX = 100
DI_IMPORT_MAX_FILES = 50
#: 上傳收的檔案：DI 檔本身（公文系統匯出的是 `.di`，有些系統存成 `.xml`）或整包 `.zip`
DI_IMPORT_EXTS = (".di", ".xml", ".zip")
DI_ENTRY_EXTS = (".di", ".xml")
#: 歷史案件表格：可以藏的欄（代號, 標題）與每頁幾件（第一個是預設）
CASE_COLUMNS = (("mode", "文別"), ("rev", "版本"), ("issues", "檢查結果"),
                ("updated", "最後修改"), ("owner", "擁有者"))
CASE_PAGE_SIZES = (20, 50, 100)

#: 模型呼叫失敗（連不上、逾時、對方回錯誤）時給使用者的話。**原因只進記錄** ——
#: 例外字串常帶著 LLM 伺服器的內部位址，不可以原樣回給使用者。
_MODEL_FAILED = ("撰寫草稿時呼叫語言模型失敗，請稍後再試；"
                 "問題持續的話請管理員檢查「LLM 設定」。")
#: 同步 API 那條路的格式錯誤（`DraftError`）—— 用固定的一句，不回例外字串。
_DRAFT_FAILED = ("模型沒有照格式回答（連問兩次都讀不出內容）。"
                 "請再試一次；一直失敗的話請換一個模型，或縮短輸入。")

_FIELD_NAMES = {
    "narrative": "需求敘述", "source": "來文內容", "direction": "辦理方向",
    "unit": "承辦單位", "addressee": "陳核對象", "units": "承辦/協辦單位",
    "internal_deadline": "內部期限",
    "org": "發文機關", "receiver": "受文者", "copies": "正本", "cc": "副本",
    "signature": "署名", "contact": "聯絡資訊", "attachments": "附件", "doc_no": "發文字號",
    "paragraph": "要改寫的段落", "instruction": "改寫要求", "note": "版本說明",
    "name": "案件名稱",
}

#: 改寫方式在畫面上的名稱（鍵是核心的 `REWRITE_KINDS`，**清單只有核心那一份** ——
#: 少一個名稱的話那個方式就不出現在下拉裡，測試會擋）。
REWRITE_LABELS = {
    "shorter": "精簡", "expand": "展開", "list": "改成條列",
    "formal": "更正式", "custom": "自訂",
}
#: 改寫方式排成卡片（2026-10-08 使用者：「改為跟圖 2 一樣的風格配置」，同版面加註那一排）：
#: 每張一個圖示 ＋ 一句講得出「改完差在哪」的說明。少一個的話那張卡片沒有說明，測試會擋。
REWRITE_CARDS = {
    "shorter": ("compress", "刪掉重複與贅字，字數明顯變少"),
    "expand": ("file-text", "補齊省略的主詞與目的"),
    "list": ("list", "拆成幾個要點，前面加一句引言"),
    "formal": ("official-doc", "口語換成公文用語與句型"),
    "custom": ("edit", "自己寫要怎麼改"),
}

#: 每個案件最多留幾版；超過就刪最舊的那幾版，**但第一版（模型產生的）一定留著** ——
#: 那是「模型原本寫了什麼」的唯一證據。
MAX_REVISIONS = 50
#: 畫面 / API 可以送的版本來源。「模型產生」（`ai`）與「重新產生」（`regen`）只有作業完成時
#: 由伺服器寫 —— 收下呼叫端自稱的 `ai` / `regen`，就等於讓人把自己改的字標成「這是模型寫的」。
CLIENT_REVISION_SOURCES = ("edit", "rewrite")
#: 版本說明的上限（自訂改寫時放的是使用者的要求，所以跟要求同一個上限）。
MAX_NOTE_CHARS = od.MAX_INSTRUCTION_CHARS
#: 版本清單是「讀 → 加一筆 → 寫回」，兩個請求同時做會互相蓋掉 —— 整份鎖住
#: （每次只動一個小 JSON，鎖的時間很短；本服務是單一行程，見 OPS.md）。
_REV_LOCK = threading.Lock()

#: 函的聯絡資訊（地址、聯絡人、電話、傳真、電子信箱，一行一項）比一般欄位長。
MAX_CONTACT_CHARS = 500

#: 草稿旁的預覽圖（2026-10-08 使用者：「草稿右邊可以順便產生預覽圖，不用等匯出才能看效果」）。
#: 走**跟匯出 PDF 同一條路**（同樣的範本處理、草稿標示、標題），再用 PyMuPDF 算成圖。
PREVIEW_DPI = 110
#: 每個案件最多留幾份預覽（新的在前）。畫面正在載的那一份一定是最新的幾份之一。
PREVIEW_KEEP = 3
#: 一份預覽最多畫幾頁（草稿上限 3 萬字，正常十頁以內）；超過的不畫，但回報總頁數 ——
#: 畫面上要講出「只畫了前幾頁」，不可以讓人以為那就是全部。
PREVIEW_MAX_PAGES = 30
#: 預覽的編號：內容雜湊的前 24 碼（**刻意不是 32 碼** —— 檔名裡的 32 碼是案件編號，
#: 保留期清理靠它認人，不要多一串長得一樣的）。
_PV_HASH_RE = re.compile(r"[0-9a-f]{24}")
#: 寫預覽檔、清舊的那一段整個鎖住（同一個案件兩個預覽同時寫完時，不會互相刪到）。
_PV_LOCK = threading.Lock()


class ModelFailed(RuntimeError):
    """模型呼叫本身失敗（不是格式不對）—— 訊息是固定的，原因在記錄裡。"""


# ------------------------------------------------------------------ 路徑
#
# 案件本身（輸入、草稿、版本）在 `data/official_doc_cases/<案件編號>/`（`case_store`）。
# v1.16.66 以前的案件還在 `data/temp/od_<案件編號>_*.json` —— **讀**的時候先找新位置、
# 找不到才看暫存（`_stored`）；**寫**一律寫到新位置（`_write_case_file`）。
# 預覽圖與下載用的 `.odt` 仍在暫存（隨時可以重新產生）。

def _legacy_path(case_id: str, suffix: str) -> Path:
    return settings.temp_dir / f"od_{case_id}_{suffix}.json"


#: 案件檔 → 舊的暫存檔名後綴
_CASE_FILES = {"case.json": "case", "result.json": "result", "revisions.json": "revisions"}


def _stored(case_id: str, name: str) -> Path:
    new = _cs.file(case_id, name)
    if new.exists():
        return new
    old = _legacy_path(case_id, _CASE_FILES[name])
    return old if old.exists() else new


def _write_case_file(case_id: str, name: str, data: dict) -> None:
    _cs.ensure(case_id)
    atomic_json.write_json(_cs.file(case_id, name), data, mode=0o600)


def _case_path(case_id: str) -> Path:
    return _stored(case_id, "case.json")


def _result_path(case_id: str) -> Path:
    return _stored(case_id, "result.json")


def _odt_path(case_id: str) -> Path:
    return settings.temp_dir / f"od_{case_id}.odt"


def _pv_manifest(case_id: str, h: str) -> Path:
    """一份預覽（照內容算的雜湊 `h`）的清單；**最後才寫** —— 有它＝每一頁都寫好了。"""
    return settings.temp_dir / f"od_{case_id}_pv_{h}.json"


def _pv_page(case_id: str, h: str, n: int) -> Path:
    return settings.temp_dir / f"od_{case_id}_pv_{h}_p{n}.png"


def _revisions_path(case_id: str) -> Path:
    return _stored(case_id, "revisions.json")


# ------------------------------------------------------------------ 案件的權限

#: 找不到 / 不是你的 / 已刪除 —— **同一句話、同一個狀態碼**：分得出來的話，拿任意編號
#: 打過來就問得出「這個案件存在，只是不是你的」（送件前檢核記過同一件事）。
_NOT_FOUND = "找不到這份草稿（可能已經刪除，或不是你的案件）。"


def _adopt_legacy(case_id: str) -> Optional[dict]:
    """舊的暫存案件（沒有 `meta.json`）第一次被打開時搬到正式位置，之後就出現在歷史案件裡。

    擁有者照**原本的歸屬紀錄**（`upload_owner.owner_of`），不是打開它的人 —— 管理員打開
    別人的舊案件時，不可以把案件變成管理員的。沒有結果檔（已經被清掉）就不搬。"""
    res = _legacy_path(case_id, "result")
    if not res.exists():
        return None
    try:
        out = json.loads(res.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(out, dict):
        return None
    try:
        _cs.ensure(case_id)
        for name, suffix in _CASE_FILES.items():
            src, dst = _legacy_path(case_id, suffix), _cs.file(case_id, name)
            if src.exists() and not dst.exists():
                atomic_json.write_text(dst, src.read_text(encoding="utf-8"), mode=0o600)
        meta = _cs.create(case_id, owner_uid=_uo.owner_of(case_id),
                          mode=str(out.get("mode") or "sign"))
        fields = {"title": str(out.get("title") or "")}
        if isinstance(out.get("created_at"), (int, float)):
            fields["created_at"] = out["created_at"]
        meta = _cs.update(case_id, **fields) or meta
    except (OSError, ValueError) as e:
        logger.warning("official-doc：舊案件搬不過去（%s）", type(e).__name__)
        return None
    return meta


def _require_case(case_id: str, request: Request) -> Optional[dict]:
    """這個請求可不可以碰這個案件；可以就回案件資料（舊的暫存案件搬不過去時回 None）。

    規則跟 `upload_owner` 一致：認證關閉放行；擁有者放行；管理員可以讀別人的
    （`admin_override_allowed` 寫稽核）；其他人一律 404。**已刪除的案件**只有管理員看得到
    （清單上反灰）。舊的暫存案件（還沒有 `meta.json`）照原本的歸屬紀錄判斷，通過之後搬過來。"""
    _sp.require_uuid_hex(case_id, "case_id")
    meta = _cs.load_meta(case_id)
    if meta is None:
        # 舊的暫存案件、或根本沒有這個案件：一律回同一個 404（`upload_owner.require` 是 403，
        # 用它的話「不存在」與「不是你的」就分得出來了）
        if not _uo.check(case_id, request):
            raise HTTPException(404, _NOT_FOUND)
        return _adopt_legacy(case_id)
    deleted = bool(meta.get("deleted_at"))
    if not _uo.auth_enabled():
        if deleted:
            raise HTTPException(404, _NOT_FOUND)
        return meta
    uid = _uo.current_user_id(request)
    owner = meta.get("owner_uid")
    if uid is not None and _uo.is_admin(uid):
        if owner == uid or _uo.admin_override_allowed(
                uid, f"official-doc:{case_id}", owner_id=owner, request=request):
            return meta
    elif uid is not None and owner is not None and owner == uid and not deleted:
        return meta
    raise HTTPException(404, _NOT_FOUND)


def _touch_case(case_id: str, **fields) -> None:
    """案件清單上的資料（標題、版本數…）跟著更新。寫不進去只記錄 —— 草稿本身已經存好了，
    不可以因為清單那一行寫不進去就讓整件作業失敗。"""
    try:
        _cs.update(case_id, **fields)
    except (OSError, ValueError) as e:
        logger.warning("official-doc：案件資料更新失敗（%s）", type(e).__name__)


def _read_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise HTTPException(410, f"{what}已經過期或被清掉了，請重新產生一次。")
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("official-doc：%s 讀不回來（%s）", what, path.name)
        raise HTTPException(500, f"{what}讀不回來，請重新產生一次。") from None
    if not isinstance(got, dict):
        raise HTTPException(500, f"{what}讀不回來，請重新產生一次。")
    return got


def _odx():
    """匯出模組（ODT / DOCX / PDF）。**用到時才載入** —— 匯出出問題不該讓整支工具
    連頁面都打不開。"""
    from ...core import official_doc_odt
    return official_doc_odt


# ------------------------------------------------------------------ 收欄位

async def _json_body(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "請求內容要是 JSON 物件。")
    return body


def _text(body: dict, key: str, limit: int, *, required: bool = False,
          required_msg: str = "") -> str:
    name = _FIELD_NAMES.get(key, key)
    v = body.get(key)
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise HTTPException(400, f"「{name}」要是文字。")
    v = v.replace("\r\n", "\n").replace("\r", "\n").strip()
    if required and not v:
        raise HTTPException(400, required_msg or f"請填寫「{name}」。")
    if len(v) > limit:
        # **不截斷**：截掉的部分可能正好是關鍵條件，截斷之後產出看起來完全正常
        raise HTTPException(
            400, f"「{name}」超過 {limit} 字的上限（目前 {len(v)} 字）。"
                 "系統不會自動截斷，請精簡後再送出。")
    return v


def _choice(body: dict, key: str, allowed, default: str, name: str) -> str:
    v = body.get(key)
    if v is None or v == "":
        return default
    v = str(v)
    if v not in allowed:
        raise HTTPException(400, f"「{name}」只接受：{'、'.join(allowed)}。")
    return v


def _flag(v, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _today_line() -> str:
    d = _dt.date.today()
    return "中華民國" + od.roc_date(d.year, d.month, d.day)


def _parse_inputs(body: dict) -> dict:
    """畫面 / API 送來的欄位 → 建立案件用的 `inputs`（也是之後重新檢查的依據）。"""
    inputs = _parse_mode_inputs(body)
    # 參考知識庫：**預設不查**（使用者勾了才查）。查什麼由伺服器依案件內容決定，
    # 查得到哪些資料集由伺服器依登入的人決定 —— 前端只送一個開關。
    inputs["use_kb"] = _flag(body.get("use_kb"))
    # 參考歷史案件：同樣預設不查。查的是**送出的這個人自己的**案件 —— 誰是「自己」由伺服器認，
    # 前端只送開關（送不了「查誰的」）
    inputs["use_history"] = _flag(body.get("use_history"))
    return inputs


def _parse_mode_inputs(body: dict) -> dict:
    mode = _choice(body, "mode", od.MODES, "sign", "模式")
    length = _choice(body, "length", tuple(od.LENGTHS), "normal", "篇幅")
    if mode == "sign":
        with_date = _flag(body.get("with_date"))
        return {
            "mode": mode,
            "narrative": _text(body, "narrative", od.MAX_NARRATIVE_CHARS, required=True,
                               required_msg="請先用白話寫下這份簽要辦的事（需求敘述）。"),
            "unit": _text(body, "unit", od.MAX_FIELD_CHARS),
            "addressee": _text(body, "addressee", od.MAX_FIELD_CHARS),
            "closing": _choice(body, "closing", tuple(od.SUBJECT_CLOSINGS), "核示", "主旨結語"),
            "length": length,
            "with_date": with_date,
            "date_line": _today_line() if with_date else "",
        }
    if mode == "letter":
        return _parse_letter(body, length)
    return {
        "mode": mode,
        "source": _text(body, "source", od.MAX_SOURCE_CHARS, required=True,
                        required_msg="請貼上來文內容（或你整理的來文大綱）。"),
        "outline": _flag(body.get("outline")),
        # 辦理方向是使用者的決定 —— 沒有就不產生（訊息跟 `run_endorse` 同一句）
        "direction": _text(body, "direction", od.MAX_DIRECTION_CHARS, required=True,
                           required_msg="請先寫下你打算怎麼辦理（辦理方向）。"),
        "units": _text(body, "units", od.MAX_FIELD_CHARS),
        "internal_deadline": _text(body, "internal_deadline", od.MAX_FIELD_CHARS),
        "fmt": _choice(body, "fmt", ENDORSE_FORMATS, "compact", "格式"),
        "length": length,
        "closing": _choice(body, "closing", tuple(od.ENDORSE_CLOSINGS), "陳核", "結尾"),
    }


def _letter_closing(body: dict, relation: str) -> str:
    """函的期望語：**一定要屬於那個行文關係**（對上級寫「請　查照」、對下級寫「請　鑒核」
    都是會被退的錯）。空的＝那個行文關係的預設（清單第一個）；行文關係「不確定」時
    沒有可選的期望語 —— 送了也不收，草稿會標〔待確認：期望語〕。"""
    allowed = tuple(od.LETTER_CLOSINGS.get(relation, ()))
    raw = body.get("closing")
    if not allowed:
        return ""
    if raw is None or raw == "":
        return allowed[0]
    if not isinstance(raw, str) or raw not in allowed:
        rel = od.RELATIONS.get(relation, relation)
        raise HTTPException(400, f"「期望語」要配合行文關係「{rel}」，只接受：{'、'.join(allowed)}。")
    return raw


def _parse_letter(body: dict, length: str) -> dict:
    """函的欄位。`recheck()` 讀的是 `relation` / `receiver` / `org`（與正副本、附件、
    聯絡資訊當依據）—— **鍵名要跟核心對得上**，不然改過草稿之後的重新檢查會當成
    「不確定的行文關係、沒有受文者」來判。

    發文身分（`issuer`）是「企業」時，行文關係**固定**是「企業發給政府機關」（送來的
    `relation` 不看）—— 企業的函沒有上行、下行之分，也不讓前端送一個機關才有的關係進來。"""
    issuer = _choice(body, "issuer", tuple(od.ISSUERS), "agency", "發文身分")
    if body.get("issuer") in (None, "") and body.get("relation") == od.COMPANY_RELATION:
        issuer = "company"          # 只送了行文關係「company」的呼叫端（存下來的舊案件、API）
    if issuer == "company":
        relation = od.COMPANY_RELATION
    else:
        relation = _choice(body, "relation", tuple(k for k in od.RELATIONS if k != od.COMPANY_RELATION),
                           "unknown", "行文關係")
    out = {
        "mode": "letter",
        "issuer": issuer,
        "narrative": _text(body, "narrative", od.MAX_NARRATIVE_CHARS, required=True,
                           required_msg="請先用白話寫下這份函要辦的事（需求敘述）。"),
        "org": _text(body, "org", od.MAX_FIELD_CHARS),
        "receiver": _text(body, "receiver", od.MAX_FIELD_CHARS),
        "relation": relation,
        "closing": _letter_closing(body, relation),
        "speed": _choice(body, "speed", od.LETTER_SPEEDS, od.LETTER_SPEEDS[0], "速別"),
        "copies": _text(body, "copies", od.MAX_FIELD_CHARS),
        "cc": _text(body, "cc", od.MAX_FIELD_CHARS),
        "signature": _text(body, "signature", od.MAX_FIELD_CHARS),
        "attachments": _text(body, "attachments", od.MAX_FIELD_CHARS),
        "doc_no": _text(body, "doc_no", od.MAX_FIELD_CHARS),
        "length": length,
    }
    out.update(_contact_inputs(body, issuer == "company"))
    out["org_codes"] = _org_codes(body, out)
    return out


def _contact_inputs(body: dict, company: bool) -> dict:
    """聯絡資訊：畫面送一格一格的 `contact_fields`（欄位名稱由程式寫，見 `od.CONTACT_FIELDS`）；
    舊的呼叫端（API、升級前的案件）送一行一項的 `contact` 文字，認得的行分進各欄，
    **認不得的行不放進草稿**、列在 `contact_unplaced` 讓呼叫端與畫面講出來。
    `contact` 存的是組好的那幾行（草稿、匯出、DI 檔都用它）。"""
    raw = body.get("contact_fields")
    if raw is not None:
        if not isinstance(raw, dict):
            raise HTTPException(400, "聯絡資訊的格式不正確（contact_fields 要是物件）。")
        fields, unplaced = od.clean_contact_fields(raw, company), []
    else:
        legacy = _text(body, "contact", MAX_CONTACT_CHARS)
        fields, unplaced = od.split_contact(legacy, company)
    return {"contact_fields": fields, "contact": od.contact_text(fields, company),
            "contact_unplaced": unplaced}


def _contact_for_reopen(inputs: dict) -> dict:
    """重新開啟案件時表單要的聯絡資訊：升級前的案件只有一行一項的文字，現場分欄。"""
    if not isinstance(inputs, dict) or inputs.get("mode") != "letter" \
            or isinstance(inputs.get("contact_fields"), dict):
        return inputs
    company = inputs.get("issuer") == "company" or inputs.get("relation") == od.COMPANY_RELATION
    fields, unplaced = od.split_contact(str(inputs.get("contact") or ""), company)
    return {**inputs, "contact_fields": fields, "contact_unplaced": unplaced}


def _org_names(inputs: dict) -> set[str]:
    """這份函裡實際寫到的機關名稱（發文機關、受文者、正本、副本；正副本照「、」分段）。"""
    names: set[str] = set()
    for key in ("org", "receiver"):
        v = str(inputs.get(key) or "").strip()
        if v:
            names.add(v)
    for key in ("copies", "cc"):
        for part in _ORG_SEP_RE.split(str(inputs.get(key) or "")):
            if part.strip():
                names.add(part.strip())
    return names


def _org_codes(body: dict, inputs: dict) -> dict:
    """畫面從地址簿挑的「機關名稱 → 機關代碼」（電子公文 DI 檔要用）。

    格式不對（不是對照、太多筆、代碼長得不像代碼）→ 400；**格式對但驗不過的安靜丟掉**：
    名稱不在這份函裡、或地址簿裡那個代碼不是這個名稱（前端送什麼都不直接信）。
    地址簿讀不到時一筆都不記 —— 驗不了的代碼比沒有代碼糟（交換到別的機關或被退件）。"""
    raw = body.get("org_codes")
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(400, "「org_codes」要是「機關名稱 → 機關代碼」的對照。")
    if len(raw) > MAX_ORG_CODES:
        raise HTTPException(400, f"「org_codes」最多 {MAX_ORG_CODES} 筆。")
    pairs: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise HTTPException(400, "「org_codes」的名稱與代碼都要是文字。")
        k, v = k.strip(), v.strip()
        if not k or len(k) > od.MAX_FIELD_CHARS or not _ORG_CODE_RE.match(v):
            raise HTTPException(400, "「org_codes」裡有名稱或機關代碼的格式不對。")
        pairs[k] = v
    present = _org_names(inputs)
    try:
        from ...core import official_doc_sources as ods
        return {k: v for k, v in pairs.items() if k in present and ods.org_code_matches(k, v)}
    except Exception as e:  # noqa: BLE001 — 驗不了就不記，不讓整份草稿失敗
        logger.warning("official-doc：驗證機關代碼失敗（%s）：%s", type(e).__name__, e)
        return {}


def _parse_facts(body: dict, inputs: dict) -> tuple[Optional[list], Optional[dict]]:
    """「依修改後的資料重新產生」：上一次的資料表 ＋ 使用者改過的值。

    資料表**一律重新判斷狀態**（`sanitize_facts`）—— 不然送一個假的「原文有」
    就能讓檢查把任何數字當成有依據。"""
    facts = None
    raw = body.get("facts")
    if raw not in (None, ""):
        if not isinstance(raw, list):
            raise HTTPException(400, "facts 要是陣列。")
        # 簽與函的原文都是需求敘述；簽辦意見是來文
        src = inputs["source"] if inputs["mode"] == "endorse" else inputs["narrative"]
        facts = od.sanitize_facts(raw, src) or None
    overrides = None
    raw_o = body.get("overrides")
    if raw_o not in (None, ""):
        if not isinstance(raw_o, dict):
            raise HTTPException(400, "overrides 要是物件（{欄位代號: 新的值}）。")
        if len(raw_o) > 40:
            raise HTTPException(400, "overrides 的項目太多。")
        overrides = {}
        for k, v in raw_o.items():
            key = re.sub(r"[^a-z0-9_]", "", str(k))[:20]
            if not key:
                continue
            if v is None:
                v = ""
            if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                raise HTTPException(400, "overrides 的值要是文字。")
            v = str(v).strip()
            if len(v) > od.MAX_FIELD_CHARS:
                raise HTTPException(
                    400, f"資料表的值超過 {od.MAX_FIELD_CHARS} 字的上限（目前 {len(v)} 字）。")
            overrides[key] = v
    return facts, overrides


def _edit_text(body: dict) -> str:
    v = body.get("text")
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise HTTPException(400, "text 要是文字。")
    v = v.replace("\r\n", "\n").replace("\r", "\n")
    if len(v) > MAX_EDIT_CHARS:
        raise HTTPException(400, f"草稿超過 {MAX_EDIT_CHARS} 字的上限（目前 {len(v)} 字）。")
    return v


def _require_llm() -> None:
    if not llm_settings.is_enabled():
        raise HTTPException(503, "LLM 服務未啟用 —— 請先到「LLM 設定」啟用")


# ------------------------------------------------------------------ 產生草稿

def _run_draft(inputs: dict, facts: Optional[list], overrides: Optional[dict],
               ask: Callable[[str], str],
               on_stage: Optional[Callable[[int, str], None]] = None,
               references: Optional[list] = None) -> od.Draft:
    if inputs["mode"] == "sign":
        return od.run_sign(
            inputs["narrative"], ask, unit=inputs["unit"], closing=inputs["closing"],
            addressee=inputs["addressee"], length=inputs["length"],
            date_line=inputs.get("date_line") or "", facts=facts, overrides=overrides,
            references=references, on_stage=on_stage)
    if inputs["mode"] == "letter":
        return od.run_letter(
            inputs["narrative"], ask, org=inputs["org"], receiver=inputs["receiver"],
            relation=inputs["relation"], closing=inputs["closing"], copies=inputs["copies"],
            cc=inputs["cc"], signature=inputs["signature"], contact=inputs["contact"],
            attachments=inputs["attachments"], speed=inputs["speed"], length=inputs["length"],
            doc_no=inputs.get("doc_no") or "",
            facts=facts, overrides=overrides, references=references, on_stage=on_stage)
    return od.run_endorse(
        inputs["source"], inputs["direction"], ask, outline=inputs["outline"],
        fmt=inputs["fmt"], length=inputs["length"], closing=inputs["closing"],
        units=inputs["units"], internal_deadline=inputs["internal_deadline"],
        facts=facts, overrides=overrides, references=references, on_stage=on_stage)


# ------------------------------------------------------------------ 參考知識庫

KB_STAGE = "查詢公文知識庫"
KB_MAX_RESULTS = 6
# 查不到 / 查詢失敗時畫面上講的話（樣板 `tr()` 這幾句）。**失敗不讓整份草稿失敗** ——
# 知識庫是參考，不是必要條件；但要講出來，不然使用者以為草稿有參考過。
KB_NOTES = {
    "none": "公文知識庫裡沒有找到跟這件事相關的資料，這份草稿沒有參考公文知識庫。",
    "failed": "查詢公文知識庫失敗，這份草稿沒有參考公文知識庫（原因已記錄，請洽管理員）。",
}


def _kb_query(inputs: dict) -> str:
    """拿什麼去查：使用者寫的那件事本身（簽 / 函的需求敘述；簽辦意見的辦理方向＋來文開頭）。
    受文者、聯絡資訊這類欄位不放進去 —— 那些查到的會是「別件事寫給同一個機關」的資料。"""
    if inputs["mode"] == "endorse":
        return f"{inputs.get('direction') or ''}\n{(inputs.get('source') or '')[:300]}"
    return str(inputs.get("narrative") or "")


def _kb_lookup(inputs: dict, user_id: Optional[int], k: int = KB_MAX_RESULTS) -> tuple[list, str]:
    """回 `(檢索結果, 說明代碼)`。`user_id` 是**伺服器端**認出的那個人（`/start` 當下取），
    看得到哪些資料集由知識庫自己判斷 —— 這裡不另外過濾。"""
    if not inputs.get("use_kb"):
        return [], ""
    try:
        from ...core import kb
        hits = kb.search(_kb_query(inputs), user_id=user_id, k=k)
    except Exception as e:  # noqa: BLE001 — 知識庫壞了不可以讓整份草稿失敗
        logger.warning("official-doc：查詢知識庫失敗（%s）：%s", type(e).__name__, e)
        return [], "failed"
    return (hits, "") if hits else ([], "none")


# ------------------------------------------------------------------ 參考歷史案件

HISTORY_STAGE = "查詢歷史案件"
HISTORY_NOTES = {
    "none": "你的歷史案件裡沒有找到跟這件事相近的案件，這份草稿沒有參考歷史案件。",
    "failed": "查詢歷史案件失敗，這份草稿沒有參考歷史案件（原因已記錄，請洽管理員）。",
}


def _history_background() -> list[str]:
    """比對時算「詞有多常見」的背景文字：內建範例的需求敘述（程式附的，不是任何人的資料）。"""
    out = []
    for ex in _examples.EXAMPLES:
        f = ex.get("fields") or {}
        out.append(str(f.get("narrative") or "") + "\n" + str(f.get("direction") or "")
                   + "\n" + str(f.get("source") or "")[:300])
    return out


_HISTORY_BG: Optional[list[str]] = None


def _history_lookup(inputs: dict, user_id: Optional[int],
                    exclude: Optional[str] = None) -> tuple[list, str]:
    """回 `(參考資料, 說明代碼)`。**只查 `user_id` 自己的案件**（管理員也一樣），
    `user_id` 是伺服器端認出的那個人；`exclude` 是這一件本身（重新產生時不拿自己當參考）。"""
    global _HISTORY_BG
    if not inputs.get("use_history"):
        return [], ""
    try:
        if _HISTORY_BG is None:
            _HISTORY_BG = _history_background()
        found = _cs.search_own(_kb_query(inputs), owner_uid=user_id, auth_on=_uo.auth_enabled(),
                               exclude=exclude, background=_HISTORY_BG)
    except Exception as e:  # noqa: BLE001 — 歷史案件讀不到不可以讓整份草稿失敗
        logger.warning("official-doc：查詢歷史案件失敗（%s）：%s", type(e).__name__, e)
        return [], "failed"
    refs = [{"purpose": od.PAST_CASE, "title": h["title"] or od.MODE_NAMES.get(h["mode"], ""),
             "text": h["text"], "case_id": h["case_id"], "case_rev": h["rev"],
             "case_updated": h["updated_at"]} for h in found]
    return (refs, "") if refs else ([], "none")


def _refs_lookup(inputs: dict, user_id: Optional[int], exclude: Optional[str] = None,
                 on_stage: Optional[Callable[[str], None]] = None) -> tuple[list, str, str]:
    """知識庫＋歷史案件 → `(參考資料, 知識庫說明, 歷史案件說明)`。

    兩個都勾時，歷史案件先查（只讀幾個小檔），知識庫的名額讓出歷史案件用掉的那幾個 ——
    參考資料總共 `od.MAX_REFS` 段，知識庫照舊排前面（業務依據在前）。"""
    hist, hist_note = [], ""
    if inputs.get("use_history"):
        if on_stage:
            on_stage(HISTORY_STAGE)
        hist, hist_note = _history_lookup(inputs, user_id, exclude)
    hits, kb_note = [], ""
    if inputs.get("use_kb"):
        if on_stage:
            on_stage(KB_STAGE)
        hits, kb_note = _kb_lookup(inputs, user_id, k=max(1, KB_MAX_RESULTS - len(hist)))
    return hits + hist, kb_note, hist_note


def _history_available(user_id: Optional[int]) -> bool:
    """這個人有沒有自己的歷史案件 —— 沒有的話勾選框不出現。"""
    try:
        return _cs.has_own(user_id, _uo.auth_enabled())
    except Exception as e:  # noqa: BLE001
        logger.warning("official-doc：讀歷史案件失敗（%s）：%s", type(e).__name__, e)
        return False


def _kb_available(user_id: Optional[int]) -> bool:
    """這個人有沒有看得到的資料集 —— 沒有的話勾選框不出現（勾了也查不到東西）。"""
    try:
        from ...core import kb
        return bool(kb.list_datasets(user_id=user_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("official-doc：讀知識庫清單失敗（%s）：%s", type(e).__name__, e)
        return False


# ------------------------------------------------------------------ 機關範本與地址簿（政府資料開放）
# 資料**不隨程式散布**：管理員在「公文撰擬設定」按下載才有（`official_doc_sources`）。
# 沒下載時這兩項功能在畫面上整個不出現。

# 只開放**填得對**的範本。同一個資料集裡還有「簽（上行簽）」「書函」…：上行簽的版面
# 不同（實測抬頭會留著範本的示範字），書函是另一種文別（選書函範本、內容卻寫成「函」，
# 文別就錯了）。名稱以資料集裡的寫法為準；改名的話只是不出現，不會套錯。
TEMPLATE_NAMES = {"sign": ("簽",), "letter": ("函",)}
TEMPLATE_FMTS = ("odt", "docx", "pdf")
MAX_TEMPLATE_KEY = 300
MAX_ORG_QUERY = 50
TEMPLATE_GONE = "這份範本目前不能用（可能已被管理員停用、刪除或重新下載），請重新整理頁面後再選。"


def _org_templates() -> dict:
    """每種文別可以套的範本：`{"sign": [{"key", "label", "category"}], ...}`。"""
    try:
        from ...core import official_doc_sources as ods
        rows = ods.list_templates()
    except Exception as e:  # noqa: BLE001 — 範本清單讀不到不可以讓整頁壞掉
        logger.warning("official-doc：讀範本清單失敗（%s）：%s", type(e).__name__, e)
        return {}
    out: dict = {}
    for mode, names in TEMPLATE_NAMES.items():
        for t in rows:
            if t.get("name") in names and t.get("path"):
                out.setdefault(mode, []).append({
                    "key": f"{t['source_id']}|{t['path']}", "label": t["name"],
                    "category": t.get("category") or ""})
    return out


def _resolve_template(key: str, mode: str) -> bytes:
    """畫面送來的範本代碼 → 範本內容。**只收這個文別目前可以套的那幾份**
    （不是「任何下載過的範本」）—— 代碼是使用者送來的，不可以拿來讀別的東西。"""
    from ...core import official_doc_sources as ods
    allowed = {x["key"] for x in _org_templates().get(mode, [])}
    if key not in allowed:
        raise HTTPException(400, TEMPLATE_GONE)
    sid, _, path = key.partition("|")
    data = ods.get_template(sid, path)
    if data is None:
        raise HTTPException(400, TEMPLATE_GONE)
    return data


def _setup_todo(user_id: Optional[int]) -> list[str]:
    """管理員還沒準備的資料（2026-10-08 使用者：「公文撰擬上面，要提醒管理員需要去設定下載匯入
    公文範本資料集」）。這幾份資料不隨程式散布，要管理員自己按下載；沒做的話一般使用者只會覺得
    「範本那一格怎麼沒有」，不知道是缺資料。

    * `templates`：公文範本（公文撰擬設定）；`orgs`：機關地址簿（公文撰擬設定）；
      `gov`：政府公開資料（法規、文書規範）還沒匯入公文知識庫。
    * **只給管理員**（只有管理員做得了這件事）；認證關閉＝單人模式，也給。
    * 只讀狀態、不連外；**公文知識庫的資料庫還不存在時不去開它**（開了就會建出一個空的）。
    """
    if _uo.auth_enabled() and not _uo.is_admin(user_id):
        return []
    todo: list[str] = []
    try:
        from ...core import official_doc_sources as ods
        if not ods.list_templates():
            todo.append("templates")
        if not ods.has_address_book():
            todo.append("orgs")
    except Exception as e:  # noqa: BLE001 — 提醒算不出來不可以讓整頁壞掉
        logger.warning("official-doc：讀資料來源狀態失敗（%s）：%s", type(e).__name__, e)
    try:
        from ...core.kb import gov as _gov, store as _kbs
        imported = _kbs.db_path().exists() and any(
            s.get("dataset_id") for gid in _gov.GROUPS for s in _kbs.gov_items(gid).values())
        if not imported:
            todo.append("gov")
    except Exception as e:  # noqa: BLE001
        logger.warning("official-doc：讀政府公開資料狀態失敗（%s）：%s", type(e).__name__, e)
    return todo


def _page_extras(user_id: Optional[int]) -> dict:
    """工具頁要的、會讀檔的那幾項（一起丟到執行緒）。"""
    try:
        from ...core import official_doc_sources as ods
        orgs_ok = ods.has_address_book()
        attrib = {"templates": ods.attribution(ods.KIND_TEMPLATES),
                  "orgs": ods.attribution(ods.KIND_ADDRESS_BOOK)}
    except Exception as e:  # noqa: BLE001
        logger.warning("official-doc：讀資料來源狀態失敗（%s）：%s", type(e).__name__, e)
        orgs_ok, attrib = False, {"templates": [], "orgs": []}
    return {"kb_available": _kb_available(user_id), "history_available": _history_available(user_id),
            "org_templates": _org_templates(),
            "setup_todo": _setup_todo(user_id), "orgs_info": _orgs_info(user_id),
            "orgs_available": orgs_ok, "attribution": attrib,
            "ref_purposes": _ref_purpose_labels()}


def _orgs_info(user_id: Optional[int]) -> dict:
    """機關地址簿的狀態（欄位下方的說明與第一次使用時的提醒）。只讀狀態、不連外。

    `admin`：看得到「前往公文撰擬設定」連結的人（管理員；認證關閉＝單人模式也算）——
    其他人看到的是「請通知管理員」，因為只有管理員能下載或更新。"""
    admin = (not _uo.auth_enabled()) or _uo.is_admin(user_id)
    try:
        from ...core import official_doc_sources as ods
        info = ods.address_book_info()
    except Exception as e:  # noqa: BLE001 — 狀態讀不到就當成沒有，不讓整頁壞掉
        logger.warning("official-doc：讀地址簿狀態失敗（%s）：%s", type(e).__name__, e)
        info = {"installed": False, "updated_at": None, "count": 0}
    at = info.get("updated_at")
    days = int(max(0.0, time.time() - at) // 86400) if at else None
    return {"installed": bool(info.get("installed")),
            "updated": time.strftime("%Y/%m/%d", time.localtime(at)) if at else "",
            "days": days if days is not None else "",
            "stale": bool(info.get("installed")) and days is not None and days > ORG_STALE_DAYS,
            "count": int(info.get("count") or 0), "admin": admin}


def _di_messages() -> dict:
    """DI 檔注意事項的樣板（前端 `tr(樣板)` 再填參數）。"""
    from ...core import official_doc_di as di
    return dict(di.MESSAGES)


def _di_errors() -> dict:
    """上傳 DI 檔時讀不進來的原因（檔案本身的＋檔案層級的）。"""
    from ...core import official_doc_di as di
    return {**di.READ_ERRORS, **IMPORT_ERRORS}


def _ref_purpose_labels() -> dict:
    """參考資料的用途標籤：**跟知識庫管理頁同一份**（`kb.store.PURPOSES`）——
    管理員在那裡選的是哪個字，使用者在這裡看到的就是哪個字。讀不到才用核心那一份。"""
    try:
        from ...core.kb import store
        return {k: store.PURPOSES.get(k) or v for k, v in od.REF_PURPOSES.items()}
    except Exception:  # noqa: BLE001
        return dict(od.REF_PURPOSES)


def _search_orgs(q: str, limit: int = 10) -> dict:
    """`{"results": [...], "total": 符合的總筆數}`。"""
    try:
        from ...core import official_doc_sources as ods
        return ods.search_orgs_page(q, limit=limit)
    except Exception as e:  # noqa: BLE001 — 查不到就是沒有建議，不是錯誤
        logger.warning("official-doc：查機關名稱失敗（%s）：%s", type(e).__name__, e)
        return {"results": [], "total": 0}


def _search_orgs_exact(q: str, limit: int) -> tuple[dict, str]:
    page = _search_orgs(q, limit)
    try:
        from ...core import official_doc_sources as ods
        return page, ods.exact_org_code(q)
    except Exception as e:  # noqa: BLE001
        logger.warning("official-doc：比對機關全銜失敗（%s）：%s", type(e).__name__, e)
        return page, ""


def _abolished_law_names() -> tuple[list[str], list[str]]:
    """全國法規資料庫（管理員在知識庫「政府公開資料」下載過才有）裡**已廢止**與**現行**的法規名稱。

    沒下載過、或讀不到 → 兩個空清單（這一項就不檢查；**不會**為了這個去連外）。
    同名的法規有現行版本時不算廢止。"""
    try:
        from ...core.kb import gov as _gov
        idx = _gov.merged_index("moj")
    except Exception:  # noqa: BLE001 — 知識庫讀不到不可以讓草稿失敗
        logger.warning("official-doc：讀不到法規清單，略過「已廢止」檢查", exc_info=True)
        return [], []
    cur = {str(e.get("name") or "") for e in idx.values() if not e.get("abolished")} - {""}
    gone = {str(e.get("name") or "") for e in idx.values() if e.get("abolished")} - {""} - cur
    return sorted(gone), sorted(cur)


def _with_abolished(text: str, issues: list) -> list:
    """把「引用了已廢止的法規」接進檢查結果（嚴重度照原本的排法）。"""
    gone, cur = _abolished_law_names()
    extra = od.abolished_law_issues(text, gone, cur) if gone else []
    if not extra:
        return issues
    return sorted(extra + list(issues), key=lambda i: od.SEVERITY_ORDER.get(i.severity, 9))


def _draft_safely(inputs: dict, facts: Optional[list], overrides: Optional[dict],
                  ask: Callable[[str], str],
                  on_stage: Optional[Callable[[int, str], None]] = None,
                  cancelled: Callable[[], bool] = lambda: False,
                  references: Optional[list] = None) -> od.Draft:
    """`DraftError`（模型沒照格式回答）與我們自己的 `ValueError` 原樣往外丟 ——
    那兩種的訊息是寫給使用者看的。**其他一律換成固定的一句**，原因只進記錄。"""
    try:
        draft = _run_draft(inputs, facts, overrides, ask, on_stage, references)
        draft.issues = _with_abolished(draft.text, draft.issues)
        return draft
    except od.DraftError:
        raise
    except Exception as e:  # noqa: BLE001
        if type(e) is ValueError:          # 只有 run_endorse 自己丟的那一種
            raise
        if cancelled():
            raise
        logger.warning("official-doc：撰寫草稿失敗（%s）：%s", type(e).__name__, e)
        raise ModelFailed(_MODEL_FAILED) from None


def _clean_title(s: str, limit: int = 20) -> str:
    """檔名用的標題：拿掉檔名不能用的字元與控制字元。"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip(" .")


def _subject_tails(mode: str) -> list[str]:
    """主旨結尾由程式加的那一段（簽的結語 / 函的期望語）—— 取標題時要拿掉。長的排前面
    （「請　查照辦理」要比「請　查照」先比）。"""
    if mode == "sign":
        return sorted(od.SUBJECT_CLOSINGS.values(), key=len, reverse=True)
    tails = {f"，{c}。" for cs in od.LETTER_CLOSINGS.values() for c in cs}
    tails.add("，〔待確認：期望語〕。")
    return sorted(tails, key=len, reverse=True)


#: 清單上的主旨整句最多存幾個字（畫面會截斷加「…」，滑鼠移過去看全文）
SUBJECT_MAX_CHARS = 200


def _title_for(mode: str, draft: od.Draft) -> str:
    """簽與函取主旨前 20 字（去掉結語 / 期望語）；簽辦意見、或主旨還沒有內容時用模式名稱。
    這是**檔名**用的；清單上顯示的是整句（`_subject_for`）。"""
    fallback = od.MODE_NAMES.get(mode, "公文")
    subj = _subject_for(mode, draft.text)
    return (_clean_title(subj) or fallback) if subj else fallback


def _subject_for(mode: str, text: str) -> str:
    """主旨整句（去掉結語 / 期望語與起頭語，同 `_title_for` 的規則）；簽辦意見、
    主旨還沒有內容（〔待補〕）時是空字串。"""
    if mode not in ("sign", "letter"):
        return ""
    for b in od.parse_text(text):
        if b["kind"] == "label" and b["label"] == "主旨":
            subj = b["text"]
            for tail in _subject_tails(mode):
                if tail and subj.endswith(tail):
                    subj = subj[:-len(tail)]
                    break
            subj = subj.rstrip("。，, ")
            # 主旨的起頭語與「一案，預估…」不進檔名（「為汰換…一案，預估所需經費…」→「汰換…」）
            subj = re.sub(r"^(?:為|有關|關於)", "", subj).split("一案")[0].rstrip("，, ")
            # 「請　貴公司…」：檔名不要從請對方開始（「請 貴公司針對…」→「針對…」）
            subj = re.sub(r"^請[\s　]*(?:(?:貴|鈞)(?:機關|公司|[^\s　，,]{1,3}?(?=[針於就依提儘配協辦轉查回補派檢])|[^\s　，,])|台端)[\s　]*",
                          "", subj)
            if subj and not subj.startswith("〔"):
                subj = re.sub(r"[\x00-\x1f\x7f]", "", subj)
                # 全形空白是挪抬（「請　貴局」），留著；其他空白收成一個
                return re.sub(r"[^\S\u3000]+", " ", subj).strip()[:SUBJECT_MAX_CHARS]
            break
    return ""


#: 叫模型時的進度文字（`job.message`）。使用者要看得出**資料送到 LLM 伺服器了、AI 正在回覆**
#: —— 原本整段只顯示「撰寫草稿（2/3）」，二十秒裡看起來跟程式自己在跑沒有兩樣。
#: 前端 `tr()` 的數字退路會把數字換成 `{0}` `{1}`… 再查譯文，所以字數不可以帶千分位。
#: 「檢查草稿」那一段不叫模型（程式比對原文），不會出現這幾句。
LLM_MESSAGES = {
    "wait": "{stage}：已送到 LLM 伺服器，等待 AI 回應（{i}/{n}）",
    "stream": "{stage}：AI 正在回覆，已收到 {chars} 字（{i}/{n}）",
    "retry": "{stage}：AI 回覆的格式不對，重新詢問（{i}/{n}）",
}


def llm_message(kind: str, stage: str, i: int, n: int, chars: int = 0) -> str:
    return LLM_MESSAGES[kind].format(stage=stage, i=i, n=n, chars=int(chars))


def _ask_for(job, on_status: Optional[Callable[[str, int], None]] = None) -> Callable[[str], str]:
    """送給 `official_doc` 的 `ask`。**取消要真的停下來**：被取消就丟例外，
    中斷整條管線（`official_doc` 不知道作業被取消了，但它每一次都會回來問）。

    `on_status(種類, 已收到字數)`：送出時 `wait`、重問時 `retry`、AI 回覆中 `stream`
    （給進度文字用，見 `LLM_MESSAGES`）。"""
    client = llm_settings.make_client(TOOL_ID)
    if client is None:
        raise RuntimeError("LLM 服務未啟用")
    model = llm_settings.get_model_for(TOOL_ID)

    def _status(kind: str, chars: int = 0) -> None:
        if on_status is None:
            return
        try:
            on_status(kind, chars)
        except Exception:  # noqa: BLE001 — 進度文字壞了不可以讓草稿產不出來
            logger.debug("official-doc：進度文字更新失敗", exc_info=True)

    def _stream(n: int) -> None:
        _status("stream", n)

    def ask(prompt: str) -> str:
        if getattr(job, "cancelled", False):
            raise RuntimeError("已取消")
        _status("wait")
        # 按停止或模型打轉時不必等生成跑完（打轉的模型會一路寫到輸出上限）
        out = client.text_query(prompt, model=model, think=False, max_tokens=od.MAX_OUTPUT_TOKENS,
                                stop_when=lambda t: bool(getattr(job, "cancelled", False))
                                or od.is_runaway(t), on_progress=_stream)
        if getattr(job, "cancelled", False):
            raise RuntimeError("已取消")
        return out

    def retry(prompt: str) -> str:
        # 格式不對而重問：換一點溫度（溫度 0 打轉時，重問同一個提示多半一模一樣）
        if getattr(job, "cancelled", False):
            raise RuntimeError("已取消")
        _status("retry")
        return client.text_query(prompt, model=model, think=False, max_tokens=od.MAX_OUTPUT_TOKENS,
                                 temperature=od.RETRY_TEMPERATURE,
                                 stop_when=lambda t: bool(getattr(job, "cancelled", False))
                                 or od.is_runaway(t), on_progress=_stream)

    ask.retry = retry
    return ask


def _issue_counts(issues: list[dict]) -> dict:
    out = {"error": 0, "todo": 0, "hint": 0}
    for i in issues:
        sev = i.get("severity")
        if sev in out:
            out[sev] += 1
    return out


def _append_regen_revision(case_id: str, text: str, created_at: float) -> Optional[int]:
    """重新產生的草稿存成**下一版**（來源 `regen`，parent＝原本的最新版）。回新版號；
    跟最新版一字不差就不另存（回 None，同 `/revisions` 的規則）。

    ⚠ 要在**覆寫結果之前**呼叫：版本清單不見時 `_load_revisions` 會從結果補第一版 ——
    先覆寫的話，補出來的第一版就是新的草稿，模型原本寫的那一份就不見了。"""
    with _REV_LOCK:
        revs = _load_revisions(case_id)
        latest = revs[-1]
        if text == (latest.get("text") or ""):
            return None
        new = _rev_record(latest["rev"] + 1, latest["rev"], "regen", text, created_at=created_at)
        revs.append(new)
        # 超過上限：刪最舊的那幾版，**第一版（模型產生的）一定留著**
        while len(revs) > MAX_REVISIONS:
            del revs[1]
        _write_revisions(case_id, revs)
        return new["rev"]


def _run_job(job, case_id: str, inputs: dict, facts: Optional[list],
             overrides: Optional[dict], user_id: Optional[int] = None,
             regen: bool = False) -> None:
    n = len(od.STAGES)
    # 現在在第幾段（叫模型時的進度文字要帶著段名）；已經有整理好的資料就從撰寫草稿開始
    cur = {"i": 0 if facts is None else 1}

    # 進度要說得出**在做什麼**（整理資料 / 撰寫草稿 / 檢查），不是只有百分比
    def on_stage(i: int, name: str) -> None:
        cur["i"] = i
        job.progress = round(max(0.05, i / n), 3)
        job.message = f"{name}（{i + 1}/{n}）"

    def on_llm(kind: str, chars: int) -> None:
        i = cur["i"]
        job.message = llm_message(kind, od.STAGES[i], i + 1, n, chars)

    ask = _ask_for(job, on_llm)

    def on_lookup(name: str) -> None:
        job.message = name

    hits, kb_note, history_note = _refs_lookup(inputs, user_id, exclude=case_id, on_stage=on_lookup)
    job.message = od.STAGES[0] if facts is None else od.STAGES[1]
    draft = _draft_safely(inputs, facts, overrides, ask, on_stage,
                          cancelled=lambda: bool(getattr(job, "cancelled", False)),
                          references=hits)
    title = _title_for(inputs["mode"], draft)
    pub = draft.to_public()
    out = {"case_id": case_id, "mode": inputs["mode"], "inputs": inputs,
           "title": title, "draft": pub, "kb_note": kb_note, "history_note": history_note,
           "created_at": time.time()}
    if regen:
        # 依修改後的資料重新產生（同一個案件）：新的草稿存成下一版 ——
        # **先接版本、再覆寫結果**（理由見 `_append_regen_revision`）。輸入也換成這一次的。
        # 作業沒有成功的話走不到這裡：原本的結果、輸入、版本都原封不動。
        try:
            job.meta["regen_rev"] = _append_regen_revision(case_id, pub["text"],
                                                           out["created_at"])
        except (OSError, HTTPException) as e:
            # 清單壞掉（不覆寫它 —— 見 `_load_revisions`）或寫不進去：草稿照樣交出去
            logger.warning("official-doc：重新產生的版本存不進去（%s）", type(e).__name__)
        _write_case_file(case_id, "case.json", {
            "case_id": case_id, "mode": inputs["mode"], "inputs": inputs,
            "created_at": out["created_at"]})
    _write_case_file(case_id, "result.json", out)
    if not regen:
        # 版本記錄的第一版就是模型產生的這一份（寫失敗的話第一次讀清單時會從結果補上）
        try:
            _init_revisions(case_id, pub["text"], out["created_at"])
        except OSError as e:
            logger.warning("official-doc：寫第一版失敗（%s）：%s", type(e).__name__, e)
    path = _odt_path(case_id)
    path.write_bytes(_odx().build_odt(draft.text, title=title))

    # **`result_path` 一定要設，而且要是 `Path` 不是字串** —— `Job.to_public()` 會對它
    # 呼叫 `.exists()`，放字串的話 `/api/jobs/{id}` 每次都 500，而作業本身完全正常。
    # 沒設的話「我的作業」顯示已完成卻沒有下載鈕，自動存入工作區也不認得那份產出。
    job.result_path = path
    job.result_filename = f"{title}-草稿.odt"
    job.meta["case_id"] = case_id
    job.meta["issues"] = _issue_counts(pub["issues"])
    _touch_case(case_id, title=title, subject=_subject_for(inputs["mode"], pub["text"]),
                mode=inputs["mode"], issues=job.meta["issues"])
    job.progress = 1.0
    job.message = "完成"


# ------------------------------------------------------------------ 逐段改寫：找「游標所在那一段」

def _protected_tails() -> list[str]:
    """一段的結尾由**程式**加的那一截（簽的主旨結語、函的期望語、簽辦意見的陳核）。
    改寫游標所在那一段時不送給模型、換回來時原樣留著 —— 結語是使用者在表單上選的，
    讓模型改寫等於讓它替使用者換一個結語。長的排前面（「請　查照辦理」要比「請　查照」先比）。"""
    tails = set(_subject_tails("sign")) | set(_subject_tails("letter"))
    tails |= {f"，{v}。" for v in od.ENDORSE_CLOSINGS.values() if v}
    tails.discard("。")          # 只有句號的不算（那就是一般的句尾）
    return sorted((t for t in tails if t), key=len, reverse=True)


def _segment_rules() -> dict:
    """前端找「游標所在那一段」用的規則 —— **跟核心 `parse_text` 是同一組正規式**
    （從核心取、以字串送進頁面），前端不另寫一份：兩份一定會漂，漂了之後游標放在
    「受文者：」那一行也會被當成內文送去改寫。

    規則由核心公開提供（`segment_patterns()`，含 JS 與 Python `\\d` 的差異處理）；
    `test_segment_rules_compile_in_js_and_agree_with_parse_text` 驗兩邊的判斷一致。"""
    return {**od.segment_patterns(), "tails": _protected_tails()}


def _case_sources(out: dict) -> list[str]:
    """改寫的依據 —— **跟重新檢查（`recheck`）用的是同一批**：使用者給的內容
    ＋資料表裡「原文有」或「已確認」的值。不是改寫前的那段文字（那段本身可能就有問題）。"""
    inputs = out.get("inputs") or {}
    facts = (out.get("draft") or {}).get("facts") or []
    keys = ("narrative", "source", "direction", "units", "internal_deadline",
            "receiver", "org", "copies", "cc", "attachments", "contact", "doc_no")
    refs = (out.get("draft") or {}).get("references") or []
    return (od.trusted_sources(facts, *(str(inputs.get(k) or "") for k in keys))
            + od.reference_sources(refs))


# ------------------------------------------------------------------ 版本記錄

def _rev_record(rev: int, parent: Optional[int], source: str, text: str, *,
                note: str = "", kind: str = "", restored_from: Optional[int] = None,
                created_at: Optional[float] = None) -> dict:
    r = {"rev": rev, "parent": parent, "source": source, "text": text,
         "created_at": created_at if created_at is not None else time.time(), "note": note}
    if kind:
        r["kind"] = kind
    if restored_from is not None:
        r["restored_from"] = restored_from
    return r


def _rev_meta(r: dict) -> dict:
    """清單用的那一份（不含全文 —— 五十版的全文一次送太大，要看哪一版再單獨拿）。"""
    out = {k: r.get(k) for k in ("rev", "parent", "source", "created_at", "note")}
    for k in ("kind", "restored_from"):
        if r.get(k) is not None and r.get(k) != "":
            out[k] = r[k]
    out["chars"] = len(r.get("text") or "")
    return out


def _write_revisions(case_id: str, revs: list[dict]) -> None:
    _write_case_file(case_id, "revisions.json", {"case_id": case_id, "revisions": revs})
    _touch_case(case_id, revisions=len(revs), latest_rev=revs[-1]["rev"] if revs else 0)


def _init_revisions(case_id: str, text: str, created_at: float) -> None:
    """作業完成時寫第一版（模型產生的那一份）。"""
    with _REV_LOCK:
        _write_revisions(case_id, [_rev_record(1, None, "ai", text, created_at=created_at)])


def _load_revisions(case_id: str) -> list[dict]:
    """讀版本清單；**呼叫端要拿著 `_REV_LOCK`**。

    沒有清單（這個功能之前產生的案件、或作業完成時寫失敗）就從結果補第一版。
    **清單壞掉的話不補** —— 補了就是用一份只有第一版的清單蓋掉使用者存過的版本。"""
    path = _revisions_path(case_id)
    if path.exists():
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            got = None
        revs = got.get("revisions") if isinstance(got, dict) else None
        if not (isinstance(revs, list) and revs
                and all(isinstance(r, dict) and isinstance(r.get("rev"), int) for r in revs)):
            logger.warning("official-doc：版本記錄讀不回來（%s）", path.name)
            raise HTTPException(500, "版本記錄讀不回來。草稿本身還在，可以先匯出儲存。")
        return revs
    out = _read_json(_result_path(case_id), "這份草稿")
    text = str((out.get("draft") or {}).get("text") or "")
    created = out.get("created_at")
    revs = [_rev_record(1, None, "ai", text,
                        created_at=created if isinstance(created, (int, float)) else None)]
    _write_revisions(case_id, revs)
    return revs


def _int_field(body: dict, key: str, name: str, *, required: bool) -> Optional[int]:
    v = body.get(key)
    if v is None or v == "":
        if required:
            raise HTTPException(400, f"缺少「{name}」。")
        return None
    # bool 是 int 的子類別 —— `true` 不可以被當成第 1 版
    if isinstance(v, bool) or not isinstance(v, int):
        raise HTTPException(400, f"「{name}」要是整數。")
    if v < 1:
        raise HTTPException(400, f"「{name}」要是正整數。")
    return v


# ------------------------------------------------------------------ 端點

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    extras = await asyncio.to_thread(_page_extras, _uo.current_user_id(request))
    return templates.TemplateResponse(request, "official_doc.html", {
        **extras,
        "kb_notes": KB_NOTES,
        "history_notes": HISTORY_NOTES,
        "di_notes": _di_messages(),
        "request": request,
        "llm_enabled": llm_settings.is_enabled(),
        "llm_model": llm_settings.get_model_for(TOOL_ID),
        # 這支工具**實際**送去的那一台（管理員可以替每支工具指定另一台伺服器）
        "llm_url": llm_settings.base_url_for(TOOL_ID),
        "max_narrative": od.MAX_NARRATIVE_CHARS,
        "max_source": od.MAX_SOURCE_CHARS,
        "max_direction": od.MAX_DIRECTION_CHARS,
        "max_field": od.MAX_FIELD_CHARS,
        "max_edit": MAX_EDIT_CHARS,
        "contact_max": od.CONTACT_FIELD_MAX,
        "contact_person_labels": od.CONTACT_PERSON_LABELS,
        # 舊的一行一項資料（瀏覽器裡記的）分欄用：欄位名稱 → 鍵（跟伺服器的 `split_contact` 同一份）
        "contact_aliases": od.contact_aliases(),
        "accept": ",".join(EXTRACT_EXTS),
        # 上傳區下面那一行「收得進來的格式」—— 跟 `accept` 同一份清單，不在樣板另寫
        "extract_exts_label": " ".join(EXTRACT_EXTS),
        # 函的選項**只有一份：核心那一份**。行文關係與速別直接畫成下拉；期望語要依行文關係
        # 過濾，整份以 JSON 送進頁面（`data-closings`）—— 前端不另寫一份清單。
        # 行文關係的下拉只列機關的（「企業發給政府機關」由發文身分決定，不在這裡選）
        "relations": [(k, v) for k, v in od.RELATIONS.items() if k != od.COMPANY_RELATION],
        "issuers": list(od.ISSUERS.items()),
        "letter_closings": {k: list(v) for k, v in od.LETTER_CLOSINGS.items()},
        "letter_speeds": list(od.LETTER_SPEEDS),
        # 版面加註的選項 —— 清單只有一份（匯出那支的）
        "copy_marks": list(_odx().COPY_MARKS),
        "send_methods": list(_odx().SEND_METHODS),
        "delegate_suggestions": list(_odx().DELEGATE_SUGGESTIONS),
        "max_delegate": _odx().MAX_DELEGATE_CHARS,
        "max_address": _odx().MAX_ADDRESS_CHARS,
        "rewrite_kinds": [(k, REWRITE_LABELS[k]) + REWRITE_CARDS[k] for k in od.REWRITE_KINDS],
        "max_rewrite": od.MAX_REWRITE_CHARS,
        "max_instruction": od.MAX_INSTRUCTION_CHARS,
        "max_revisions": MAX_REVISIONS,
        "segment_rules": _segment_rules(),
        # 需求敘述下面「載入範例」那個下拉（文字是輸入資料、不翻譯；名稱走 tr()）
        "examples": _examples.EXAMPLES,
        "example_groups": _examples.grouped(),
    })


@router.get("/salutation")
async def salutation(receiver: str = "", relation: str = "unknown", org: str = ""):
    """畫面上即時顯示「草稿會怎麼稱呼對方、怎麼自稱」—— 規則只有核心那一份
    （`official_doc.salutation` / `self_term`），前端不另寫。純計算、不碰檔案。"""
    if relation not in od.RELATIONS:
        raise HTTPException(400, f"「行文關係」只接受：{'、'.join(od.RELATIONS)}。")
    for name, v in (("受文者", receiver), ("發文機關", org)):
        if len(v) > od.MAX_FIELD_CHARS:
            raise HTTPException(400, f"「{name}」超過 {od.MAX_FIELD_CHARS} 字的上限。")
    return {"term": od.salutation(receiver, relation), "self": od.self_term(org, relation)}


@router.get("/orgs")
async def orgs(q: str = "", limit: int = 10):
    """機關名稱的輸入建議（公文電子交換系統地址簿）。沒下載地址簿時回空清單。

    `exact`：查詢字串**就是**某個機關的全銜（台臺、全形半形視為相同）而且只有一個代碼時，那個代碼 ——
    使用者自己打完整名稱、沒從清單挑時，畫面用它記代碼。同名好幾個就是空的（不猜）。"""
    q = q.strip()
    if len(q) > MAX_ORG_QUERY:
        raise HTTPException(400, f"查詢字串超過 {MAX_ORG_QUERY} 字的上限。")
    if not q:
        return {"orgs": [], "exact": "", "total": 0}
    from ...core.official_doc_sources import ORG_SEARCH_MAX
    limit = max(1, min(int(limit), ORG_SEARCH_MAX))
    page, exact = await asyncio.to_thread(_search_orgs_exact, q, limit)
    # `total`：符合的總筆數。清單只列前幾筆時畫面講出「12 / 165 筆」並給「全部顯示」——
    # 不講的話，排在後面的機關看起來像是地址簿裡沒有。
    return {"orgs": [{"name": r.get("orgName") or "", "id": r.get("orgId") or "",
                      "marks": r.get("nameMarks") or []} for r in page["results"]],
            "exact": exact, "total": page["total"], "max": ORG_SEARCH_MAX}


@router.post("/extract-text")
async def extract_text(file: UploadFile = File(...)):
    """「從檔案帶入文字」：只抽文字回給畫面，**檔案不留**。"""
    name = file.filename or ""
    if not name.lower().endswith(EXTRACT_EXTS):
        raise HTTPException(400, "只收 PDF、Word（.docx / .doc）、ODF（.odt）、RTF、"
                                 "純文字（.txt / .md）。")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(400, "檔案是空的。")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案超過這項功能的 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限。")
    # 抽字跟「逐句翻譯」同一支（PDF 字形反查、Office 走 soffice）—— 不在這裡另寫一份
    from ..translate_doc.router import _extract_text_from_file
    text = await asyncio.to_thread(_extract_text_from_file, name, data)
    text = (text or "").strip()
    return {"text": text, "chars": len(text), "filename": name}


@router.post("/start")
async def start(request: Request):
    body = await _json_body(request)
    inputs = _parse_inputs(body)
    facts, overrides = _parse_facts(body, inputs)
    _require_llm()

    # 沒帶案件編號：**新的案件**，擁有者就是送出的這個人。
    # 帶著編號（「依修改後的資料重新產生」）：**接在同一個案件後面**，新的草稿存成下一版
    # （版本清單不會重新從第 1 版開始）。編號是使用者送來的 —— 驗格式、驗歸屬，
    # **不可以 `record`**（那等於讓人把別人的案件登記成自己的）；文別要跟原本的一樣。
    existing = str(body.get("case_id") or "").strip()
    if not existing:
        case_id = uuid.uuid4().hex
    else:
        await asyncio.to_thread(_require_case, existing, request)
        prev = await asyncio.to_thread(_read_json, _result_path(existing), "這份草稿")
        if str(prev.get("mode") or "") != inputs["mode"]:
            raise HTTPException(400, "重新產生要跟原本的草稿同一種文別。")
        case_id = existing
    # 查知識庫要用「送出的這個人」看得到的資料集 —— 在請求當下由伺服器認出，帶進背景作業
    uid = _uo.current_user_id(request)

    def _save() -> None:
        # 擁有者記在案件裡（**只在建立時**記 —— `create` 不覆寫既有的 `meta.json`）
        _cs.create(case_id, owner_uid=uid if _uo.auth_enabled() else None,
                   mode=inputs["mode"])
        _write_case_file(case_id, "case.json", {
            "case_id": case_id, "mode": inputs["mode"], "inputs": inputs,
            "created_at": time.time()})

    # 既有案件的輸入**等作業成功才換**（`_run_job`）—— 失敗或停止時原本那一份要照常能用
    if not existing:
        await asyncio.to_thread(_save)

    def run(job) -> None:
        _run_job(job, case_id, inputs, facts, overrides, uid, regen=bool(existing))

    job = job_manager.submit(
        TOOL_ID, run,
        # 案件自己保存（`case_store`），不靠暫存的保留期 —— 所以**不放 `upload_id`**：
        # 放了的話「我的作業」會把「開啟」當成要從暫存區讀回來的那一種，暫存清掉就藏起來。
        meta={"filename": od.MODE_NAMES[inputs["mode"]], "mode": inputs["mode"],
              "case_id": case_id},
        request=request,
    )
    # 「開啟」按案件定址：作業紀錄過期之後照樣打得開（頁面再從案件找出還在跑的那件作業）
    job.meta["view_url"] = f"/tools/{TOOL_ID}/?case={case_id}"
    await asyncio.to_thread(_touch_case, case_id, job_id=job.id)
    return {"job_id": job.id, "case_id": case_id,
            "contact_unplaced": inputs.get("contact_unplaced") or []}


@router.get("/result/{case_id}")
async def result(case_id: str, request: Request):
    await asyncio.to_thread(_require_case, case_id, request)
    out = await asyncio.to_thread(_read_json, _result_path(case_id), "這份草稿")
    if isinstance(out, dict) and isinstance(out.get("inputs"), dict):
        out = {**out, "inputs": _contact_for_reopen(out["inputs"])}
    return out


# ------------------------------------------------------------------ 歷史案件

def _see_all(request: Request) -> tuple[Optional[int], bool]:
    """(這個人, 看不看得到每個人的案件)。認證關閉＝單人模式，看得到全部；管理員看得到全部。"""
    uid = _uo.current_user_id(request)
    if not _uo.auth_enabled():
        return uid, True
    return uid, _uo.is_admin(uid)


def _owner_label(uid) -> str:
    if uid is None:
        return ""
    try:
        from ...core import sessions as _sess, user_manager as _um
        return _sess.user_label(_um.get_by_id(int(uid))) or f"#{uid}"
    except Exception:  # noqa: BLE001 —— 帳號被刪了也不可以讓清單壞掉
        return f"#{uid}"


def _case_row(meta: dict, *, me: Optional[int], show_owner: bool) -> dict:
    """清單的一列（頁面與 JSON 同一份）。"""
    mode = str(meta.get("mode") or "sign")
    name = str(meta.get("name") or "")
    title = str(meta.get("title") or "")
    issues = meta.get("issues") if isinstance(meta.get("issues"), dict) else {}
    row = {
        "case_id": str(meta.get("case_id") or ""),
        "name": name, "title": title, "subject": str(meta.get("subject") or ""),
        "mode": mode, "mode_name": od.MODE_NAMES.get(mode, mode),
        "latest_rev": int(meta.get("latest_rev") or 0),
        "issues": {k: int(issues.get(k) or 0) for k in ("error", "todo", "hint")},
        "created_at": float(meta.get("created_at") or 0),
        "updated_at": float(meta.get("updated_at") or 0),
        "deleted": bool(meta.get("deleted_at")),
        "deleted_at": float(meta.get("deleted_at") or 0) or None,
        "deleted_by": str(meta.get("deleted_by") or ""),
        "mine": me is not None and meta.get("owner_uid") == me,
        "imported": meta.get("origin") == "di",
    }
    # 下載得到 DI 檔嗎：簽與函、已經有草稿、沒刪除（簽辦意見沒有 DI 檔）
    row["di_ok"] = mode in ("sign", "letter") and row["latest_rev"] > 0 and not row["deleted"]
    if show_owner:
        row["owner"] = _owner_label(meta.get("owner_uid"))
    return row


def _with_subject(meta: dict) -> dict:
    """v1.16.76 以前的案件沒有存主旨整句：第一次列出來時從草稿算一次存回去
    （**不動「最後修改」** —— 補一個欄位不是使用者改了案件）。算不出來存空字串，下次不再算。"""
    if "subject" in meta:
        return meta
    cid = str(meta.get("case_id") or "")
    subj = ""
    try:
        out = json.loads(_result_path(cid).read_text(encoding="utf-8"))
        if isinstance(out, dict):
            subj = _subject_for(str(out.get("mode") or meta.get("mode") or ""),
                                str((out.get("draft") or {}).get("text") or ""))
    except (OSError, ValueError, TypeError, AttributeError):
        subj = ""
    try:
        _cs.update(cid, touch=False, subject=subj)
    except (OSError, ValueError) as e:
        logger.warning("official-doc：補主旨失敗（%s）", type(e).__name__)
    return {**meta, "subject": subj}


def _list_rows(request: Request, q: str, mode: str) -> tuple[list[dict], bool]:
    uid, see_all = _see_all(request)
    auth_on = _uo.auth_enabled()
    metas = _cs.list_cases(owner_uid=uid, see_all=see_all,
                           include_deleted=see_all and auth_on)
    q = " ".join(str(q or "").split()).lower()[:100]
    rows = []
    for m in metas:
        if mode and str(m.get("mode") or "") != mode:
            continue
        m = _with_subject(m)
        if q:
            blob = " ".join(str(m.get(k) or "") for k in ("name", "title", "subject", "case_id")).lower()
            if q not in blob:
                continue
        rows.append(_case_row(m, me=uid, show_owner=see_all and auth_on))
    return rows, see_all and auth_on


@router.get("/cases", response_class=HTMLResponse)
async def cases_page(request: Request, q: str = "", mode: str = ""):
    """歷史案件：自己寫過的每一份（管理員看得到每個人的，含已刪除的）。"""
    mode = mode if mode in od.MODE_NAMES else ""
    rows, show_owner = await asyncio.to_thread(_list_rows, request, q, mode)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "official_doc_cases.html", {
        "request": request, "cases": rows, "q": q, "mode_filter": mode,
        "modes": list(od.MODE_NAMES.items()), "show_owner": show_owner,
        "max_name": _cs.MAX_NAME_CHARS, "limit": _cs.LIST_LIMIT,
        # DI 檔的注意事項與讀不進來的原因：樣板（前端 `tr(樣板)` 再填參數）
        "di_notes": _di_messages(), "di_errors": _di_errors(),
        "batch_max": DI_BATCH_MAX, "import_max": DI_IMPORT_MAX_FILES,
        "import_exts": list(DI_IMPORT_EXTS),
        # 「顯示欄位」可以藏的欄（案件名稱與動作一定在）；擁有者只有管理員看得到
        "col_options": [(k, v) for k, v in CASE_COLUMNS if k != "owner" or show_owner],
        "page_sizes": CASE_PAGE_SIZES,
    })


@router.get("/api/cases")
async def cases_api(request: Request, q: str = "", mode: str = ""):
    mode = mode if mode in od.MODE_NAMES else ""
    rows, show_owner = await asyncio.to_thread(_list_rows, request, q, mode)
    return {"cases": rows, "show_owner": show_owner, "limit": _cs.LIST_LIMIT}


@router.get("/case/{case_id}")
async def case_info(case_id: str, request: Request):
    """重新打開一個案件（`?case=`）：案件資料＋最近一件作業的狀態 —— 還在跑的話頁面接著追。"""
    meta = await asyncio.to_thread(_require_case, case_id, request)
    uid, _ = _see_all(request)
    row = _case_row(meta or {"case_id": case_id}, me=uid, show_owner=False)
    row["has_result"] = await asyncio.to_thread(lambda: _result_path(case_id).exists())
    job = None
    jid = str((meta or {}).get("job_id") or "")
    if jid:
        j = job_manager.get(jid)
        if j is not None and j.tool_id == TOOL_ID:
            job = {"id": j.id, "status": j.status}
    row["job"] = job
    return row


@router.post("/case/{case_id}/rename")
async def case_rename(case_id: str, request: Request):
    meta = await asyncio.to_thread(_require_case, case_id, request)
    if meta is None:
        raise HTTPException(404, _NOT_FOUND)
    body = await _json_body(request)
    name = _text(body, "name", _cs.MAX_NAME_CHARS)
    name = " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", name).split())
    got = await asyncio.to_thread(_cs.rename, case_id, name)
    uid, _ = _see_all(request)
    return _case_row(got or meta, me=uid, show_owner=False)


@router.delete("/case/{case_id}")
async def case_delete(case_id: str, request: Request):
    """刪除（軟刪除）：本人的清單上不再出現、也打不開；管理員的清單上反灰、寫著誰刪的。
    真的從磁碟移除由保留期清理做。"""
    meta = await asyncio.to_thread(_require_case, case_id, request)
    if meta is None:
        raise HTTPException(404, _NOT_FOUND)
    from ...core import sessions as _sess
    by = _sess.user_label(getattr(request.state, "user", None)) or "anonymous"
    await asyncio.to_thread(_cs.soft_delete, case_id, by)
    try:
        from ...core import audit_db, client_ip
        audit_db.log_event("official_doc_case_delete", username=by,
                           ip=client_ip.real_client_ip(request),
                           target=f"official-doc:{case_id}",
                           details={"owner_id": meta.get("owner_uid")})
    except Exception:  # noqa: BLE001 —— 稽核寫不進去不可以讓刪除失敗（已經刪了）
        logger.warning("official-doc：刪除案件的稽核寫不進去")
    return {"ok": True, "case_id": case_id}


# ------------------------------------------------------------------ 歷史案件的 DI 檔
#
# 下載：照**最新那一版**（承辦人改過、存過的那一份）產生，跟匯出同一支產生器（`_di_build`）。
# 批次下載：勾好幾件打包成 zip；**任何一件不是你的就整批 404**（跟單件同一句話 —— 部分成功的話，
# 回來的件數就問得出哪些編號是別人的）。簽辦意見、還沒有草稿的略過並講出幾件。
# 上傳：DI 檔（或整包 zip）→ 一件一件的歷史案件，擁有者是上傳的人。

def _case_label(meta: Optional[dict]) -> str:
    meta = meta or {}
    return str(meta.get("name") or meta.get("title") or
               od.MODE_NAMES.get(str(meta.get("mode") or ""), "公文"))


def _case_di(case_id: str) -> tuple[bytes, list, bool]:
    """案件最新那一版 → DI 檔（位元組, 注意事項, 驗得過 DTD 嗎）。阻塞呼叫。"""
    text, _rev = _cs.latest_text(case_id)
    if not text.strip():
        raise HTTPException(410, "這份草稿已經過期或被清掉了，請重新產生一次。")
    data, notes, ok, _errs, _mode = _di_build(case_id, text)
    return data, notes, ok


@router.get("/case/{case_id}/di")
async def case_di(case_id: str, request: Request):
    from ...core import official_doc_di as di
    meta = await asyncio.to_thread(_require_case, case_id, request)
    if meta is None:
        raise HTTPException(404, _NOT_FOUND)
    try:
        data, notes, ok = await asyncio.to_thread(_case_di, case_id)
    except di.DiNotApplicable as e:
        raise HTTPException(400, str(e))
    name = f"{_clean_title(_case_label(meta), 40) or '公文'}.di"
    return Response(content=data, media_type=di.MEDIA_TYPE + "; charset=utf-8", headers={
        "Content-Disposition": content_disposition(name),
        "X-Jtdt-Di-Notes": str(len(notes)), "X-Jtdt-Di-Valid": "1" if ok else "0"})


@router.post("/cases/di")
async def cases_di_zip(request: Request):
    """勾選的案件打包成 zip（一件一個 DI 檔）。`{"case_ids": [...]}`。"""
    from ...core import official_doc_di as di
    body = await _json_body(request)
    raw = body.get("case_ids")
    if not isinstance(raw, list) or not raw or not all(isinstance(x, str) for x in raw):
        raise HTTPException(400, "請先勾選要下載的案件。")
    ids = list(dict.fromkeys(x.strip() for x in raw))
    if len(ids) > DI_BATCH_MAX:
        raise HTTPException(400, f"一次最多下載 {DI_BATCH_MAX} 件。")
    metas = []
    for cid in ids:
        meta = await asyncio.to_thread(_require_case, cid, request)
        if meta is None:
            raise HTTPException(404, _NOT_FOUND)
        metas.append((cid, meta))

    def _work() -> tuple[bytes, int, int]:
        buf, used, done, skipped = io.BytesIO(), set(), 0, 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for cid, meta in metas:
                try:
                    data, _notes, _ok = _case_di(cid)
                except (di.DiNotApplicable, HTTPException):
                    skipped += 1
                    continue
                base = _clean_title(_case_label(meta), 40) or "公文"
                name = f"{base}.di"
                if name in used:
                    name = f"{base}-{cid[:8]}.di"
                used.add(name)
                z.writestr(name, data)
                done += 1
        return buf.getvalue(), done, skipped

    data, done, skipped = await asyncio.to_thread(_work)
    if not done:
        raise HTTPException(400, "選取的案件都沒有 DI 檔（簽辦意見沒有 DI 檔，還沒有草稿的也沒有）。")
    stamp = time.strftime("%Y%m%d-%H%M")
    return Response(content=data, media_type="application/zip", headers={
        "Content-Disposition": content_disposition(f"公文DI檔-{stamp}.zip"),
        "X-Jtdt-Di-Count": str(done), "X-Jtdt-Di-Skipped": str(skipped)})


def _zip_entry_name(info: zipfile.ZipInfo) -> str:
    """壓縮檔裡的檔名：沒有標 UTF-8 的多半是 Big5（Windows 的壓縮工具、官方的實作範例都是）。"""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("cp950")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _di_items(uploads: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], list[dict], int]:
    """上傳的檔案 → (要匯入的 DI 檔, 一開始就收不了的, 壓縮檔裡略過的其他檔案數)。阻塞呼叫。"""
    from ...core import official_doc_di as di
    from ...core import zip_guard
    items: list[tuple[str, bytes]] = []
    failed: list[dict] = []
    skipped = 0
    for name, data in uploads:
        if name.lower().endswith(DI_ENTRY_EXTS):
            items.append((name, data))
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            zip_guard.check(zf)
        except (zipfile.BadZipFile, zip_guard.ZipBombError, ValueError):
            failed.append(_import_failure(name, "bad_zip"))
            continue
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                entry = _zip_entry_name(info)
                if info.filename.startswith("__MACOSX/") or entry.startswith("._"):
                    continue
                if not entry.lower().endswith(DI_ENTRY_EXTS):
                    skipped += 1
                    continue
                if len(items) > DI_IMPORT_MAX_FILES:
                    break          # 已經超過上限（呼叫端會回 400）—— 不必再解開更多
                if info.file_size > di.READ_MAX_BYTES:
                    failed.append(_import_failure(entry, "too_big", di.READ_MAX_BYTES // 1024))
                    continue
                try:
                    with zf.open(info) as f:
                        items.append((entry, f.read(di.READ_MAX_BYTES + 1)))
                except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
                    # 加密的、壓縮方式不支援的、內容對不上的
                    failed.append(_import_failure(entry, "not_xml"))
    return items, failed, skipped


#: 上傳失敗的原因：DI 檔本身讀不進來的在 `official_doc_di.READ_ERRORS`，這裡是檔案層級的
IMPORT_ERRORS = {
    "ext": "只收 DI 檔（.di / .xml）或整包 .zip。",
    "bad_zip": "壓縮檔讀不開（毀損、加密，或解開後大得不合理）。",
    "save_failed": "存不進去，請稍後再試一次。",
}


def _import_failure(name: str, code: str, *params) -> dict:
    from ...core import official_doc_di as di
    tpl = IMPORT_ERRORS.get(code) or di.READ_ERRORS.get(code) or code
    args = [str(p) for p in params]
    return {"filename": name, "code": code, "args": args, "error": tpl.format(*args)}


def _relation_from_subject(subject: str) -> tuple[str, str]:
    """匯入的函：主旨結尾的期望語**只屬於一種行文關係**時，照它填行文關係與期望語
    （「請　鑒核」只有上行用；「請　查照」上下平行都用 → 不猜，留「不確定」）。"""
    s = re.sub(r"[\s　]", "", subject or "").rstrip("。.")
    hits: dict[str, str] = {}
    for rel, closings in od.LETTER_CLOSINGS.items():
        if rel in ("unknown", od.COMPANY_RELATION):
            continue
        for c in closings:
            if s.endswith(c.replace("　", "")):
                hits.setdefault(rel, c)
    return next(iter(hits.items())) if len(hits) == 1 else ("unknown", "")


def _import_di_case(filename: str, data: bytes, uid: Optional[int]) -> dict:
    """一份 DI 檔 → 一件歷史案件。讀不進來丟 `DiReadError`。阻塞呼叫。

    * **擁有者是上傳的人**（認證關閉時沒有擁有者，同新建的案件）。
    * 輸入（`inputs`）照畫面送的格式組、走同一支 `_parse_mode_inputs`，重新打開時表單填得回去：
      需求敘述放主旨（「參考我的歷史案件」比對的就是它 —— 放全文的話每一份都像）；
      **全文放在 `source`**，重新檢查（`recheck`）時當依據 —— 不然公文裡的每個數字都是「找不到依據」。
    * 檔案裡寫的機關代碼要**地址簿驗得過**才留（`_org_codes`，同畫面送來的那一條）。
    * 第一版的來源是 `import`（不是 `ai`：這份不是模型寫的）。"""
    from ...core import official_doc_di as di
    got = di.read(data, max_chars=MAX_EDIT_CHARS)
    mode, text, f = got["mode"], got["text"], got["fields"]

    def cut(v, n: int = od.MAX_FIELD_CHARS) -> str:
        return str(v or "").strip()[:n].strip()

    first = next((b.get("text") for b in od.parse_text(text)
                  if b.get("kind") in ("label", "item", "para") and b.get("text")), "")
    subject = got["subject"]
    for tail in _subject_tails(mode):     # 結語 / 期望語不是需求（`_title_for` 同一份清單）
        if tail and subject.endswith(tail):
            subject = subject[:-len(tail)]
            break
    narrative = (cut(subject, od.MAX_NARRATIVE_CHARS) or cut(first, od.MAX_NARRATIVE_CHARS)
                 or cut(f"從 DI 檔匯入（{filename}）", od.MAX_NARRATIVE_CHARS))
    body: dict = {"mode": mode, "narrative": narrative, "length": "normal"}
    if mode == "letter":
        relation, closing = _relation_from_subject(got["subject"])
        body.update(issuer="agency", relation=relation, closing=closing, org=cut(f["org"]),
                    receiver=cut(f["receiver"]), copies=cut(f["copies"]), cc=cut(f["cc"]),
                    signature=cut(f["signature"]), attachments=cut(f["attachments"]),
                    doc_no=cut(f["doc_no"]), contact=cut(f["contact"], MAX_CONTACT_CHARS),
                    org_codes={k: v for k, v in got["org_codes"].items()
                               if _ORG_CODE_RE.match(v) and 0 < len(k) <= od.MAX_FIELD_CHARS})
        if f["speed"] in od.LETTER_SPEEDS:
            body["speed"] = f["speed"]
    else:
        body.update(unit=cut(f["unit"]), addressee=cut(f["addressee"]),
                    with_date=bool(f["date_line"]))
    inputs = _parse_mode_inputs(body)
    if mode == "sign":
        inputs["date_line"] = cut(f["date_line"])
    inputs.update(use_kb=False, use_history=False, source=text[:od.MAX_SOURCE_CHARS], origin="di")
    issues = _with_abolished(text, od.recheck(mode, text, [], inputs, []))
    draft = od.Draft(mode=mode, text=text, facts=[], issues=issues)
    title = _title_for(mode, draft)
    pub = draft.to_public()
    now = time.time()
    case_id = uuid.uuid4().hex
    _cs.create(case_id, owner_uid=uid if _uo.auth_enabled() else None, mode=mode)
    _write_case_file(case_id, "case.json", {"case_id": case_id, "mode": mode, "inputs": inputs,
                                            "created_at": now})
    _write_case_file(case_id, "result.json", {
        "case_id": case_id, "mode": mode, "inputs": inputs, "title": title, "draft": pub,
        "kb_note": "", "history_note": "", "created_at": now,
        "imported": {"filename": filename, "notes": got["notes"], "at": now}})
    with _REV_LOCK:
        _write_revisions(case_id, [_rev_record(1, None, "import", text, created_at=now,
                                               note=cut(filename, MAX_NOTE_CHARS))])
    counts = _issue_counts(pub["issues"])
    _touch_case(case_id, title=title, subject=_subject_for(mode, text), mode=mode,
                issues=counts, origin="di")
    return {"filename": filename, "case_id": case_id, "title": title, "mode": mode,
            "mode_name": od.MODE_NAMES.get(mode, mode), "notes": got["notes"], "issues": counts}


@router.post("/cases/import")
async def cases_import(request: Request, files: list[UploadFile] = File(...)):
    """上傳 DI 檔（可以一次好幾份，或整包 zip）→ 每一份一件歷史案件。

    回 `{imported: [...], failed: [{filename, code, args, error}], skipped}`：讀不進來的一份不影響
    其他份；壓縮檔裡不是 DI 檔的（附件、簽核檔…）略過並講出幾個。"""
    from ...core import official_doc_di as di
    if len(files) > DI_IMPORT_MAX_FILES:
        raise HTTPException(400, f"一次最多上傳 {DI_IMPORT_MAX_FILES} 份 DI 檔。")
    uploads: list[tuple[str, bytes]] = []
    failed: list[dict] = []
    total = 0
    for up in files:
        name = (up.filename or "").replace("\\", "/").rsplit("/", 1)[-1][:200] or "?"
        data = await up.read(MAX_UPLOAD_BYTES + 1)
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"上傳的檔案加起來超過這項功能的 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限。")
        if not name.lower().endswith(DI_IMPORT_EXTS):
            failed.append(_import_failure(name, "ext"))
            continue
        if not data:
            failed.append(_import_failure(name, "not_xml"))
            continue
        uploads.append((name, data))
    items, bad, skipped = await asyncio.to_thread(_di_items, uploads)
    failed += bad
    if len(items) > DI_IMPORT_MAX_FILES:
        raise HTTPException(400, f"一次最多上傳 {DI_IMPORT_MAX_FILES} 份 DI 檔。")
    if not items and not failed:
        raise HTTPException(400, "沒有找到 DI 檔（.di / .xml）。")
    uid = _uo.current_user_id(request)
    imported: list[dict] = []
    for name, data in items:
        try:
            imported.append(await asyncio.to_thread(_import_di_case, name, data, uid))
        except di.DiReadError as e:
            failed.append(_import_failure(name, e.code, *e.params))
        except (OSError, ValueError, HTTPException) as e:
            logger.warning("official-doc：匯入 DI 檔失敗（%s）", type(e).__name__)
            failed.append(_import_failure(name, "save_failed"))
    return {"imported": imported, "failed": failed, "skipped": skipped}


@router.post("/check")
async def check(request: Request):
    """使用者改過草稿之後重新檢查 —— **不呼叫模型**，依據是建立案件時存下來的內容。"""
    body = await _json_body(request)
    case_id = str(body.get("case_id") or "").strip()
    await asyncio.to_thread(_require_case, case_id, request)
    text = _edit_text(body)

    def _work() -> list[dict]:
        out = _read_json(_result_path(case_id), "這份草稿")
        facts = (out.get("draft") or {}).get("facts") or []
        issues = od.recheck(str(out.get("mode") or "sign"), text, facts,
                            out.get("inputs") or {},
                            (out.get("draft") or {}).get("references") or [])
        return [i.to_dict() for i in _with_abolished(text, issues)]

    return {"issues": await asyncio.to_thread(_work)}


def _office_bytes(case_id: str, text: str, fmt: str, title: str, draft_mark: bool,
                  tpl_key: str, extras=None) -> tuple[bytes, str, str]:
    """ODT / DOCX / PDF 的位元組、media type、範本套用狀況（`none` / `applied` / `fallback`）。
    **匯出與預覽圖共用這一支** —— 預覽圖看到的版面就是匯出拿到的版面。阻塞呼叫（PDF / DOCX
    會起 soffice），async 端點要丟執行緒。"""
    odx = _odx()
    extras = extras if extras is not None else odx.NO_EXTRAS
    if not tpl_key:
        return (*odx.export(text, fmt, title=title, draft_mark=draft_mark, extras=extras), "none")
    out = _read_json(_result_path(case_id), "這份草稿")
    tpl = _resolve_template(tpl_key, str(out.get("mode") or ""))
    try:
        return (*odx.export(text, fmt, title=title, draft_mark=draft_mark,
                            template=tpl, extras=extras), "applied")
    except odx.TemplateError as e:
        # 範本讀不懂（資料集改版、版面變了）：**照樣交出檔案**，用內建版面，
        # 而且講出來 —— 不可以讓使用者以為套到範本了
        logger.warning("official-doc：範本套不上（%s），改用內建版面", e)
        return (*odx.export(text, fmt, title=title, draft_mark=draft_mark, extras=extras), "fallback")


def _endorse_source(out: dict) -> str:
    """簽辦意見抬頭的「來文：」—— 來文機關與日期字號（整理資料表裡有的才寫；
    沒有就空著，印出來留給承辦人手寫，不寫〔待補〕）。"""
    facts = (out.get("draft") or {}).get("facts") or []
    vals = {f.get("key"): str(f.get("value") or "").strip() for f in facts if isinstance(f, dict)
            and f.get("status") != "missing"}
    parts = [v for v in (vals.get("sender"), vals.get("doc_ref")) if v and "〔" not in v]
    return re.sub(r"\s+", " ", " ".join(parts))[:120]


def _export_extras(body: dict, case_id: str):
    """匯出 / 預覽的「版面加註」（`extras`）。不合規定 → 400。

    **依文別過濾**：正本／副本、發文方式、受文者地址只有函才有；分層負責決行只有機關發的函
    （企業沒有分層負責）—— 簽送來這些一律不用，不讓它出現在簽上。"""
    odx = _odx()
    try:
        ex = odx.page_extras(body.get("extras"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if ex == odx.NO_EXTRAS:
        return ex
    out = _read_json(_result_path(case_id), "這份草稿")
    mode = str(out.get("mode") or "")
    relation = str((out.get("inputs") or {}).get("relation") or "")
    if mode == "endorse":
        # 簽辦意見單獨列印的抬頭與承辦人欄：「來文：」那一行由這份案件整理出來的資料填
        # （畫面送來的字不收 —— 那一行印在文件上，應該跟草稿用同一份依據）
        return odx.PageExtras(page_numbers=ex.page_numbers, binding_line=ex.binding_line,
                              endorse_frame=ex.endorse_frame,
                              endorse_source=_endorse_source(out) if ex.endorse_frame else "")
    if mode != "letter":
        return odx.PageExtras(page_numbers=ex.page_numbers, binding_line=ex.binding_line)
    if relation == od.COMPANY_RELATION and ex.delegate:
        return odx.PageExtras(**{**ex.__dict__, "delegate": ""})
    return ex


def _images(pdf: bytes, fmt: str, base: str) -> tuple[bytes, str, str]:
    """PDF → PNG / SVG。一頁：那一張；多頁：一頁一張打包成 zip。回 (位元組, media type, 檔名)。
    SVG 的字轉成外框（`text_as_path`）—— 對方電腦沒有標楷體也長得一樣。"""
    import fitz
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        n = min(doc.page_count, IMAGE_MAX_PAGES)
        if fmt == "png":
            pages = [doc[i].get_pixmap(dpi=IMAGE_DPI).tobytes("png") for i in range(n)]
            media = "image/png"
        else:
            pages = [doc[i].get_svg_image(text_as_path=True).encode("utf-8") for i in range(n)]
            media = "image/svg+xml"
    finally:
        doc.close()
    if len(pages) == 1:
        return pages[0], media, f"{base}.{fmt}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, data in enumerate(pages, 1):
            z.writestr(f"{base}-第{i}頁.{fmt}", data)
    return buf.getvalue(), "application/zip", f"{base}-{fmt}.zip"


def _di_build(case_id: str, text: str) -> tuple[bytes, list, bool, list, str]:
    """草稿文字 → DI 檔：(位元組, 注意事項, 驗得過 DTD 嗎, 前幾條錯誤, 文別)。阻塞呼叫。

    機關代碼先用案件存著的（使用者從地址簿挑的），沒有才照名稱查地址簿（名稱完全相同而且只有一筆）。
    簽辦意見、找不到主旨 → `DiNotApplicable`（呼叫端回 400）。驗不過 DTD 是**我們的錯**（產生器照
    DTD 的順序組），照樣交檔、記警告、預覽畫面講出來。"""
    from ...core import official_doc_di as di
    out = _read_json(_result_path(case_id), "這份草稿")
    mode = str(out.get("mode") or "")
    codes = (out.get("inputs") or {}).get("org_codes") or {}
    try:
        from ...core import official_doc_sources as ods
        book, lookup = ods.has_address_book(), ods.exact_org_code
    except Exception as e:  # noqa: BLE001 — 地址簿讀不到就是沒有代碼
        logger.warning("official-doc：讀地址簿失敗（%s）：%s", type(e).__name__, e)
        book, lookup = False, None
    data, notes = di.build(text, mode, org_codes=codes if isinstance(codes, dict) else {},
                           lookup=lookup, book_available=book)
    ok, errors = di.validate(data, mode)
    if not ok:
        logger.warning("official-doc：DI 檔沒通過 DTD 檢查（%s）：%s", case_id, "; ".join(errors)[:300])
    return data, notes, ok, errors, mode


@router.post("/export")
async def export(request: Request):
    """照**目前的文字**（使用者可能改過）匯出。純文字只有草稿本身，不夾任何說明。

    **一律要帶案件編號、而且一律驗歸屬** —— 不只 JSON（那個格式會讀存著的資料表）。
    寫成「只有 JSON 才驗」的話，ACL 就變成「條件成立才檢查」的形狀
    （`test_id_from_body_acl.py` 記過三次這種洞）；綁在案件上也讓這支端點
    不會變成「把任意文字轉成 PDF」的通用服務。畫面上一定有案件才會出現匯出鈕。
    """
    body = await _json_body(request)
    case_id = str(body.get("case_id") or "").strip()
    await asyncio.to_thread(_require_case, case_id, request)
    fmt = str(body.get("fmt") or "").strip().lower()
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(400, f"fmt 只接受：{' / '.join(EXPORT_FORMATS)}。")
    text = _edit_text(body)
    if not text.strip():
        raise HTTPException(400, "沒有可以匯出的內容。")
    title = _clean_title(str(body.get("title") or ""), 40) or "公文"
    draft_mark = _flag(body.get("draft_mark"), default=True)
    name = f"{title}-草稿.{fmt}"
    tpl_key = str(body.get("template") or "").strip()
    if len(tpl_key) > MAX_TEMPLATE_KEY:
        raise HTTPException(400, TEMPLATE_GONE)
    extras = await asyncio.to_thread(_export_extras, body, case_id)
    headers = {"Content-Disposition": content_disposition(name)}

    if fmt == "txt":
        data, media = text.encode("utf-8"), "text/plain; charset=utf-8"
    elif fmt == "di":
        from ...core import official_doc_di as di
        try:
            data, notes, ok, _errs, _mode = await asyncio.to_thread(_di_build, case_id, text)
        except di.DiNotApplicable as e:
            raise HTTPException(400, str(e))
        media = di.MEDIA_TYPE + "; charset=utf-8"
        headers["Content-Disposition"] = content_disposition(f"{title}-草稿.di")
        headers["X-Jtdt-Di-Notes"] = str(len(notes))
        headers["X-Jtdt-Di-Valid"] = "1" if ok else "0"
    elif fmt == "json":
        def _build_json() -> bytes:
            out = _read_json(_result_path(case_id), "這份草稿")
            draft = dict(out.get("draft") or {})
            facts = draft.get("facts") or []
            issues = _with_abolished(text, od.recheck(str(out.get("mode") or "sign"), text, facts,
                                                      out.get("inputs") or {},
                                                      draft.get("references") or []))
            draft["text"] = text
            draft["issues"] = [i.to_dict() for i in issues]
            with _REV_LOCK:
                revs = _load_revisions(case_id)
            exp = ({"format": EXPORT_FORMAT, "format_version": EXPORT_VERSION}
                   | out | {"draft": draft, "revisions": revs, "exported_at": time.time()})
            return json.dumps(exp, ensure_ascii=False, indent=2).encode("utf-8")

        data, media = await asyncio.to_thread(_build_json), "application/json"
    elif fmt in IMAGE_FORMATS:
        def _build_images() -> tuple[bytes, str, str, str]:
            pdf, _m, applied = _office_bytes(case_id, text, "pdf", title, draft_mark, tpl_key, extras)
            return (*_images(pdf, fmt, f"{title}-草稿"), applied)

        data, media, fname, applied = await asyncio.to_thread(_build_images)
        headers["Content-Disposition"] = content_disposition(fname)
        headers["X-Jtdt-Template"] = applied
    else:
        data, media, applied = await asyncio.to_thread(
            _office_bytes, case_id, text, fmt, title, draft_mark, tpl_key, extras)
        headers["X-Jtdt-Template"] = applied
    return Response(content=data, media_type=media, headers=headers)


@router.post("/di-preview")
async def di_preview(request: Request):
    """匯出預覽：照**目前的文字**產生 DI 檔，回內容（XML）、注意事項與 DTD 檢查結果給檢視器。
    跟 `/export` 同一支產生器、同一套歸屬檢查 —— 預覽看到的就是下載拿到的那一份。"""
    from ...core import official_doc_di as di
    body = await _json_body(request)
    case_id = str(body.get("case_id") or "").strip()
    await asyncio.to_thread(_require_case, case_id, request)
    text = _edit_text(body)
    if not text.strip():
        raise HTTPException(400, "沒有可以匯出的內容。")
    title = _clean_title(str(body.get("title") or ""), 40) or "公文"
    try:
        data, notes, ok, errors, mode = await asyncio.to_thread(_di_build, case_id, text)
    except di.DiNotApplicable as e:
        raise HTTPException(400, str(e))
    return {"filename": f"{title}-草稿.di", "mode": mode, "root": di.ROOTS.get(mode, ""),
            "dtd": di.DTD_FILES.get(mode, ""), "xml": data.decode("utf-8"),
            "notes": notes, "valid": ok, "errors": errors}


def _write_atomic(path: Path, data: bytes) -> None:
    """先寫同目錄的暫存檔再換名 —— 瀏覽器不會讀到寫一半的圖。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _pv_read_manifest(case_id: str, h: str) -> Optional[dict]:
    try:
        got = json.loads(_pv_manifest(case_id, h).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or not isinstance(got.get("pages"), int):
        return None
    return got


def _pv_prune(case_id: str) -> None:
    """每個案件只留最新的 `PREVIEW_KEEP` 份預覽。**呼叫端要拿著 `_PV_LOCK`**。"""
    mans = sorted(settings.temp_dir.glob(f"od_{case_id}_pv_*.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for man in mans[PREVIEW_KEEP:]:
        h = man.name[len(f"od_{case_id}_pv_"):-len(".json")]
        if not _PV_HASH_RE.fullmatch(h):
            continue
        try:
            man.unlink()
        except OSError:
            pass
        for png in settings.temp_dir.glob(f"od_{case_id}_pv_{h}_p*.png"):
            try:
                png.unlink()
            except OSError:
                pass


def _pv_render(pdf: bytes) -> tuple[list[bytes], int]:
    import fitz
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        total = doc.page_count
        pages = [doc[i].get_pixmap(dpi=PREVIEW_DPI).tobytes("png")
                 for i in range(min(total, PREVIEW_MAX_PAGES))]
    finally:
        doc.close()
    return pages, total


@router.post("/preview")
async def preview(request: Request):
    """草稿旁的預覽圖：`{case_id, text, title?, draft_mark?, template?}` →
    `{hash, pages: [網址…], total, template}`。

    走**跟匯出 PDF 同一條路**（`_office_bytes`：同樣的範本處理與退回、草稿標示、標題），
    再算成 PNG。同一份內容（文字、範本、文別、標題、草稿標示）算出同一個 `hash`，
    **第二次直接拿存好的圖，不再起 soffice**。找不到 Office 引擎是 503（全域處理器），
    畫面上講一句平靜的說明，不跳對話框。"""
    body = await _json_body(request)
    case_id = str(body.get("case_id") or "").strip()
    await asyncio.to_thread(_require_case, case_id, request)
    text = _edit_text(body)
    if not text.strip():
        raise HTTPException(400, "沒有可以預覽的內容。")
    title = _clean_title(str(body.get("title") or ""), 40) or "公文"
    draft_mark = _flag(body.get("draft_mark"), default=True)
    tpl_key = str(body.get("template") or "").strip()
    if len(tpl_key) > MAX_TEMPLATE_KEY:
        raise HTTPException(400, TEMPLATE_GONE)

    extras = await asyncio.to_thread(_export_extras, body, case_id)

    def _work() -> tuple[str, int, int, str]:
        out = _read_json(_result_path(case_id), "這份草稿")
        mode = str(out.get("mode") or "")
        key = json.dumps([text, tpl_key, mode, title, draft_mark, extras.cache_key()],
                         ensure_ascii=False)
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        with _PV_LOCK:
            man = _pv_read_manifest(case_id, h)
            if man and all(_pv_page(case_id, h, n).exists() for n in range(1, man["pages"] + 1)):
                try:
                    os.utime(_pv_manifest(case_id, h))     # 剛用過：清舊的時候排在前面
                except OSError:
                    pass
                return h, man["pages"], int(man.get("total") or man["pages"]), str(man.get("template") or "none")
        data, _media, applied = _office_bytes(case_id, text, "pdf", title, draft_mark, tpl_key, extras)
        pages, total = _pv_render(data)
        with _PV_LOCK:
            for n, png in enumerate(pages, 1):
                _write_atomic(_pv_page(case_id, h, n), png)
            # 清單最後寫：有清單＝每一頁都在
            _write_atomic(_pv_manifest(case_id, h), json.dumps(
                {"pages": len(pages), "total": total, "template": applied}).encode("utf-8"))
            _pv_prune(case_id)
        return h, len(pages), total, applied

    h, n, total, applied = await asyncio.to_thread(_work)
    base = f"/tools/{TOOL_ID}/preview/{case_id}/{h}"
    return {"hash": h, "pages": [f"{base}/{i}" for i in range(1, n + 1)], "total": total,
            "template": applied}


@router.get("/preview/{case_id}/{h}/{n}")
async def preview_page(case_id: str, h: str, n: int, request: Request):
    """一頁預覽圖。先驗編號格式與歸屬，**之後才讀檔**。"""
    _sp.require_uuid_hex(case_id, "case_id")
    if not _PV_HASH_RE.fullmatch(h or ""):
        raise HTTPException(400, "預覽編號不對。")
    if n < 1 or n > PREVIEW_MAX_PAGES:
        raise HTTPException(404, "沒有這一頁。")
    await asyncio.to_thread(_require_case, case_id, request)

    def _work() -> Path:
        man = _pv_read_manifest(case_id, h)
        if not man or n > man["pages"]:
            raise HTTPException(404, "這份預覽已經換新或被清掉了。")
        path = _pv_page(case_id, h, n)
        if not path.exists():
            raise HTTPException(404, "這份預覽已經換新或被清掉了。")
        return path

    path = await asyncio.to_thread(_work)
    # 網址帶著內容雜湊（同一個網址永遠是同一張圖），讓瀏覽器自己留一陣子
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=600"})


@router.post("/rewrite")
async def rewrite(request: Request):
    """改寫草稿裡的一段，回 `{text, issues}`。**不改任何東西** —— 採用與否由使用者決定，
    採用之後畫面另外存成新的一版（`/revisions`）。

    依據跟整份草稿一樣（`_case_sources`）；改寫後出現使用者沒給的數字、日期、法規，
    或原本那段的數字改寫後不見了，都會在 `issues` 裡。"""
    body = await _json_body(request)
    case_id = str(body.get("case_id") or "").strip()
    await asyncio.to_thread(_require_case, case_id, request)
    # 先擋輸入、再問模型 —— 送錯的請求不該讓模型白跑一次。**不截斷**（核心那一層
    # 對「要求」是安靜截斷的，所以上限一定要在這裡先擋）。
    paragraph = _text(body, "paragraph", od.MAX_REWRITE_CHARS, required=True,
                      required_msg="沒有要改寫的內容。")
    kind = _choice(body, "kind", tuple(od.REWRITE_KINDS), "shorter", "改寫方式")
    instruction = _text(body, "instruction", od.MAX_INSTRUCTION_CHARS)
    if kind == "custom" and not instruction:
        raise HTTPException(400, "請寫下要怎麼改。")
    _require_llm()
    ask = _plain_ask()

    def _work() -> dict:
        out = _read_json(_result_path(case_id), "這份草稿")
        return od.rewrite_paragraph(paragraph, ask, kind=kind, instruction=instruction,
                                    sources=_case_sources(out),
                                    mode=str(out.get("mode") or "sign"))

    try:
        got = await asyncio.to_thread(_work)
    except HTTPException:
        raise
    except od.DraftError:
        raise HTTPException(502, _DRAFT_FAILED) from None
    except Exception as e:  # noqa: BLE001
        # 只有核心自己丟的 `ValueError` 是寫給使用者看的（「這一段超過…字」）；
        # 子類別（例如 JSON 解析錯誤）可能帶著別的東西 —— 一律當成模型呼叫失敗
        if type(e) is ValueError:
            raise HTTPException(400, str(e)) from None
        logger.warning("official-doc：改寫失敗（%s）：%s", type(e).__name__, e)
        raise HTTPException(502, _MODEL_FAILED) from None
    return {"text": got["text"], "issues": got["issues"]}


@router.get("/revisions/{case_id}")
async def revisions(case_id: str, request: Request):
    """版本清單（不含全文）；`latest` 是最新那一版的版號 —— 存新版時要帶它（`base_rev`）。"""
    await asyncio.to_thread(_require_case, case_id, request)

    def _work() -> dict:
        with _REV_LOCK:
            revs = _load_revisions(case_id)
        return {"revisions": [_rev_meta(r) for r in revs], "latest": revs[-1]["rev"],
                "max": MAX_REVISIONS}

    return await asyncio.to_thread(_work)


@router.get("/revisions/{case_id}/{rev}")
async def revision(case_id: str, rev: int, request: Request):
    """某一版的全文（看這一版、還原成這一版用）。"""
    await asyncio.to_thread(_require_case, case_id, request)

    def _work() -> dict:
        with _REV_LOCK:
            revs = _load_revisions(case_id)
        for r in revs:
            if r.get("rev") == rev:
                return _rev_meta(r) | {"text": r.get("text") or ""}
        raise HTTPException(404, f"第 {rev} 版不在版本清單裡（可能超過保留的版數被清掉了）。")

    return await asyncio.to_thread(_work)


@router.post("/revisions")
async def save_revision(request: Request):
    """存成新的一版。`{case_id, text, base_rev, source, note, kind?, restored_from?}`

    * `base_rev` **一定要是目前的最新版**，不是就回 **409** 與最新版的內容（`latest`）——
      兩個分頁同時改，後存的那一邊不可以安靜地蓋掉先存的（原規格 E02）。畫面上讓使用者
      選「載入最新版」或「以最新版為基礎另存一版」，兩份內容都留得住。
    * 還原也是存成新的一版（`restored_from` 記著是從哪一版還原的），不刪後面的版本。
    * 內容跟最新版完全相同就不另存（回 `unchanged: true`）。
    * 每案最多 `MAX_REVISIONS` 版；超過就刪最舊的，但第一版一定留著。
    """
    body = await _json_body(request)
    case_id = str(body.get("case_id") or "").strip()
    await asyncio.to_thread(_require_case, case_id, request)
    text = _edit_text(body)
    if not text.strip():
        raise HTTPException(400, "草稿是空的，沒有可以儲存的內容。")
    base_rev = _int_field(body, "base_rev", "目前的版本號", required=True)
    source = _choice(body, "source", CLIENT_REVISION_SOURCES, "edit", "版本來源")
    note = _text(body, "note", MAX_NOTE_CHARS)
    note = re.sub(r"[\x00-\x1f\x7f]+", " ", note).strip()
    kind = ""
    if source == "rewrite":
        kind = _choice(body, "kind", tuple(od.REWRITE_KINDS), "", "改寫方式")
        if not kind:
            raise HTTPException(400, "逐段改寫的版本要寫明改寫方式（kind）。")
    restored_from = _int_field(body, "restored_from", "還原自第幾版", required=False)

    def _work():
        with _REV_LOCK:
            revs = _load_revisions(case_id)
            latest = revs[-1]
            if base_rev != latest["rev"]:
                return 409, latest, revs
            if restored_from is not None and not any(r["rev"] == restored_from for r in revs):
                raise HTTPException(400, f"第 {restored_from} 版不在版本清單裡。")
            if text == (latest.get("text") or ""):
                return 200, latest, revs
            new = _rev_record(latest["rev"] + 1, latest["rev"], source, text, note=note,
                              kind=kind, restored_from=restored_from)
            revs.append(new)
            # 超過上限：刪最舊的那幾版，**第一版（模型產生的）一定留著**
            while len(revs) > MAX_REVISIONS:
                del revs[1]
            _write_revisions(case_id, revs)
            return 201, new, revs

    status, rec, revs = await asyncio.to_thread(_work)
    listing = [_rev_meta(r) for r in revs]
    if status == 409:
        return JSONResponse(status_code=409, content={
            "detail": f"另一個分頁已經存過新版本（第 {rec['rev']} 版）。",
            "code": "revision_conflict",
            "latest": _rev_meta(rec) | {"text": rec.get("text") or ""},
            "revisions": listing})
    return {"rev": rec["rev"], "unchanged": status == 200, "revision": _rev_meta(rec),
            "revisions": listing, "latest": revs[-1]["rev"]}


# ------------------------------------------------------------------ 公開 API

def _plain_ask() -> Callable[[str], str]:
    client = llm_settings.make_client(TOOL_ID)
    if client is None:
        raise HTTPException(503, "LLM 服務未啟用")
    model = llm_settings.get_model_for(TOOL_ID)

    def ask(prompt: str) -> str:
        return client.text_query(prompt, model=model, think=False,
                                 max_tokens=od.MAX_OUTPUT_TOKENS, stop_when=od.is_runaway)

    def retry(prompt: str) -> str:
        return client.text_query(prompt, model=model, think=False, max_tokens=od.MAX_OUTPUT_TOKENS,
                                 temperature=od.RETRY_TEMPERATURE, stop_when=od.is_runaway)

    ask.retry = retry
    return ask


@router.post("/api/official-doc")
async def api_official_doc(request: Request):
    """一次做完：欄位同 `/start`，回 `{mode, text, facts, issues, llm_calls}`。

    **這是同步的**（一份約 2~4 次模型呼叫）—— 要背景處理請走網頁那條路
    （`/start` → 作業編號）。模型的工作丟到執行緒裡跑，不卡住整個網站。
    """
    body = await _json_body(request)
    inputs = _parse_inputs(body)
    facts, overrides = _parse_facts(body, inputs)
    _require_llm()
    ask = _plain_ask()
    hits, kb_note, history_note = await asyncio.to_thread(
        _refs_lookup, inputs, _uo.current_user_id(request))
    try:
        draft = await asyncio.to_thread(_draft_safely, inputs, facts, overrides, ask,
                                        None, lambda: False, hits)
    except od.DraftError:
        raise HTTPException(502, _DRAFT_FAILED) from None
    except ModelFailed:
        raise HTTPException(502, _MODEL_FAILED) from None
    except ValueError:
        # 只有「沒有辦理方向」會走到這裡，而 `_parse_inputs` 早就擋過 —— 保險用
        raise HTTPException(400, "請先寫下你打算怎麼辦理（辦理方向）。") from None
    pub = draft.to_public()
    return {"mode": pub["mode"], "text": pub["text"], "facts": pub["facts"],
            "issues": pub["issues"], "llm_calls": pub["llm_calls"],
            "references": pub["references"], "kb_note": kb_note, "history_note": history_note,
            "contact_unplaced": inputs.get("contact_unplaced") or []}
