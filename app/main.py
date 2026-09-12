from __future__ import annotations

import asyncio as _asyncio
import threading
import time
from pathlib import Path

import jinja2
from starlette.background import BackgroundTask
from fastapi import Depends, FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from .config import settings
from .core import loop_watchdog as _loop_watchdog
from .core.asset_manager import asset_manager
from .web.deps import require_tool as _require_tool
from .core.job_manager import job_manager
from .logging_setup import get_logger, setup_logging
from .tool_registry import discover_tools, mount_tools

VERSION = "1.15.32"

setup_logging("DEBUG" if settings.debug else "INFO")
logger = get_logger(__name__)

# 企業 TLS 攔截環境：讓 Python ssl 改用 OS 原生信任庫（認得企業 CA）,否則所有
# 外部 HTTPS（tessdata 下載 / LLM / 遠端 OCR / SSO）會 CERTIFICATE_VERIFY_FAILED。
from .core.net_ssl import install_os_trust  # noqa: E402
install_os_trust()

# `docs_url` / `redoc_url` 一律關掉。
#
# FastAPI 內建的 Swagger UI / ReDoc 從 cdn.jsdelivr.net 載 JS 與 CSS，又用
# **無 nonce 的 inline script** 啟動 —— 本站的 CSP（`default-src 'self'`、
# script-src / style-src 都移除了 unsafe-inline）三項全擋，結果是**一片純白、
# 沒有任何錯誤訊息**（v1.14.31 對抗式驗證用真瀏覽器實測：bodyText 長度 0，
# console 每頁 5 條 CSP 違規）。留著只是兩個看起來像壞掉的頁面。
#
# 對外的 API 說明走自家那份（`github/API.md` → `docs/api.html`），
# OpenAPI 規格本身仍在 `/openapi.json`。
app = FastAPI(title=settings.app_name, version=VERSION,
              docs_url=None, redoc_url=None)

# CSRF 防護（pure-ASGI）— double-submit token，疊在 SameSite=Lax 之上。
# 加在 @app.middleware 之前 → 位於 auth gate 內層：公開端點（login / setup）
# 仍會被 CSRF 驗證，已認證動作在 auth 通過後驗證；bearer API / SSO 回呼豁免。
from app.core.csrf import CSRFMiddleware  # noqa: E402
app.add_middleware(CSRFMiddleware)


class _UploadSizeLimitMiddleware:
    """**最外層**的上傳大小粗篩（純 ASGI）。

    在此之前大小完全靠反向代理擋 —— 而**直連應用程式埠就沒有任何限制**，
    內網直連又是很常見的部署。沒有帳號的人送一個超大 body 就能吃光資源。

    判準刻意簡單：只看 `Content-Length`，超過就直接回 **413**，
    **body 一個位元組都不讀**。沒有 Content-Length 的（分塊傳輸）交給
    後面的既有防線，這裡不做串流計數 —— 那要攔 receive，複雜度與風險
    都高得多，而分塊上傳在瀏覽器端的檔案上傳並不會發生。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        limit = 0
        try:
            from app.core.upload_settings import max_upload_bytes
            limit = max_upload_bytes()
        except Exception:
            limit = 0          # 讀不到設定就不擋，不可以讓整站上傳不了
        if limit > 0:
            for k, v in scope.get("headers") or []:
                if k == b"content-length":
                    try:
                        if int(v) > limit:
                            import json as _j
                            mb = limit // (1024 * 1024)
                            body = _j.dumps(
                                {"detail": f"檔案太大 —— 這個站台的單次上傳上限是 "
                                           f"{mb} MB。請分批上傳，或請管理員調整。"},
                                ensure_ascii=False).encode("utf-8")
                            await send({"type": "http.response.start",
                                        "status": 413,
                                        "headers": [
                                            (b"content-type",
                                             b"application/json; charset=utf-8"),
                                            (b"content-length",
                                             str(len(body)).encode())]})
                            await send({"type": "http.response.body", "body": body})
                            return
                    except ValueError:
                        pass
                    break
        return await self.app(scope, receive, send)


app.add_middleware(_UploadSizeLimitMiddleware)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR.parent / "static"

class _RevalidatingStatic(StaticFiles):
    """靜態檔一律要求瀏覽器**重新驗證**再用。

    預設的 `StaticFiles` 只送 `ETag` / `Last-Modified`，**不送 `Cache-Control`**。
    沒有這個標頭時瀏覽器會用「啟發式快取」（常見是取檔案年齡的 10% 當有效期）
    —— 於是升級之後，使用者的瀏覽器可能好幾個小時都還在跑**舊的 JS / CSS**，
    症狀是「我明明更新了，修好的東西還是壞的」，而且重新整理也沒用。
    開發時就踩過一次：新加的功能在伺服器上明明有，瀏覽器就是不執行。

    `no-cache` **不是不快取** —— 是「可以存，但每次用之前都要問一下」。
    配合既有的 ETag，沒改過的檔案回 304（不帶內容），成本很低。
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers.setdefault("Cache-Control", "no-cache")
        return resp


app.mount("/static", _RevalidatingStatic(directory=str(STATIC_DIR)),
          name="static")

tools = discover_tools()

# Build a template loader that can resolve:
# - Platform templates (base.html, components/*)
# - Admin templates
# - Each tool's own templates/ folder
loaders = [
    FileSystemLoader(str(BASE_DIR / "web" / "templates")),
    FileSystemLoader(str(BASE_DIR / "admin" / "templates")),
]
for t in tools:
    if t.templates_dir:
        loaders.append(FileSystemLoader(str(t.templates_dir)))

templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
templates.env.loader = ChoiceLoader(loaders)
# app_name 改成可被 admin/branding 自訂（v1.4.68 起）— 用 lazy property
# 包裝讓 Jinja 每次 render 都讀檔，admin 改完立即生效不用 restart
class _DynamicAppName:
    def __str__(self):
        from .core import branding as _br
        return _br.get_site_name(default=settings.app_name)
    # Jinja 比較 / concat 都會 call __str__；Jinja 的 escape 對 str 子類也 OK
    def __html__(self):
        return str(self)


templates.env.globals["app_name"] = _DynamicAppName()
templates.env.globals["version"] = VERSION


def _tpl_localdt(ts) -> str:
    """把 Unix 時間戳格式化成當地時間字串（給模板用）。

    模板不要直接印原始 epoch —— 掃描器會判為資訊洩漏（Timestamp Disclosure），
    而使用者本來也看不懂那串數字。取不到就回 "—"，不可讓整頁因此爆掉。
    """
    try:
        from datetime import datetime
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "—"


def _tpl_localiso(ts) -> str:
    """Unix 時間戳 → ISO 8601（含時區偏移）。空值回空字串。

    給「需要讓瀏覽器自己格式化 / 排序」的欄位用。ISO 字串照字典序排就是時間序，
    所以不必為了排序而把裸 epoch 放進頁面（那會被掃描器判為資訊洩漏）。
    """
    if not ts:
        return ""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(
            timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


templates.env.filters["localdt"] = _tpl_localdt
templates.env.filters["localiso"] = _tpl_localiso


def _tpl_ucfirst(s: str) -> str:
    """導覽名稱一律**首字大寫** —— 語系檔的鍵是中文原文，同一個鍵在句子中間
    也會被用到（「一個 key 可以對應多個 synonyms」），那裡小寫才對，
    所以不能把譯文本身改成大寫。側欄是標題語境，在**渲染時**補這一層。

    **對中文是無操作**（中文沒有大小寫），所以繁中輸出位元組完全不變。
    """
    return s[:1].upper() + s[1:] if s else s


templates.env.filters["ucfirst"] = _tpl_ucfirst


def _tpl_current_user(request) -> dict | None:
    """Jinja global: safely return the logged-in user from request.state.
    Returns None when auth is OFF (so templates can hide login UI)."""
    return getattr(getattr(request, "state", None), "user", None)


templates.env.globals["current_user"] = _tpl_current_user


def _tpl_is_admin(request) -> bool:
    """Jinja global: True 當下使用者是 admin（或 auth OFF 也視為 admin）。
    範本用此 gate 「LLM 設定」「資產管理」等只有管理員能進的連結，
    避免一般使用者點下去吃 403 錯誤頁。"""
    user = _tpl_current_user(request)
    if user is None:
        # auth off — anyone is "admin" (single-user mode)
        from .core import auth_settings as _as
        return not _as.is_enabled()
    if user.get("source") == "off":
        return True
    try:
        from .core import permissions as _perm
        return bool(_perm.is_admin(user.get("user_id", 0)))
    except Exception:
        return False


def _tpl_is_auditor(request) -> bool:
    """Jinja global: True 當下使用者具 auditor 角色。Used for nav filtering
    so auditors only see audit / history / uploads / system-status admin
    pages. v1.4.99 起。"""
    user = _tpl_current_user(request)
    if user is None or user.get("source") == "off":
        return False
    try:
        from .core import permissions as _perm
        return bool(_perm.is_auditor(user.get("user_id", 0)))
    except Exception:
        return False


templates.env.globals["is_admin"] = _tpl_is_admin
templates.env.globals["is_auditor"] = _tpl_is_auditor


def _tpl_net_exposed() -> bool:
    """Jinja global: True 當服務綁在非 loopback 位址（0.0.0.0 / 區網 IP），代表
    「已對網路開放」。用來對 admin 顯示外洩風險提醒（本工具處理公司內部文件）。"""
    try:
        h = (settings.host or "").strip().lower()
        return h not in ("127.0.0.1", "localhost", "::1", "")
    except Exception:
        return False


templates.env.globals["net_exposed"] = _tpl_net_exposed
templates.env.globals["bind_host"] = lambda: settings.host


def _tpl_workspace_enabled(request=None) -> bool:
    """Jinja global: True when the user-workspace feature is enabled by the
    admin. Templates gate the 「存至工作區」/「從工作區載入」buttons + the
    「我的工作區」nav item on this so a disabled feature is fully invisible."""
    try:
        from .core import workspace as _ws
        return _ws.is_enabled()
    except Exception:
        return False


templates.env.globals["workspace_enabled"] = _tpl_workspace_enabled


def _tpl_workspace_extensions() -> str:
    """Jinja global：工作區收得下的副檔名，空白分隔（例：``pdf png docx …``）。

    **前端不可以自己抄一份這個清單。** 原本 `job_progress.js` 裡寫死
    `pdf|png|docx|odt`，而伺服器端早在 v1.14.6 就把 `.xlsx` / `.ods` /
    `.pptx` / `.odp` 加進來了（就是為了讓「PDF 轉簡報」的產出存得進去）
    —— 兩邊沒有一起改，結果是**伺服器收得下的檔案，畫面上那顆「存至
    工作區」卻不出現**，而且完全沒有錯誤訊息可循。
    """
    try:
        from .core import workspace as _ws
        return " ".join(sorted({e.lstrip(".") for e in _ws.ALLOWED.values()}))
    except Exception:
        return ""


templates.env.globals["workspace_extensions"] = _tpl_workspace_extensions


def _tpl_cjk_font_status() -> dict:
    """Jinja global：本機有沒有可用的中文字型（給工具頁的提示用）。

    **偵測失敗時一律回「有」** —— 這個提示只是善意提醒，判斷不出來時安靜
    比誤報好：跳一個「找不到中文字型」給一台其實裝好字型的機器，使用者會
    去追一個不存在的問題，而工具本身根本沒事。
    """
    try:
        from .core import font_health
        return font_health.cjk_status()
    except Exception:  # noqa: BLE001
        return {"ok": True, "font": "", "install_cmd": ""}


templates.env.globals["cjk_font_status"] = _tpl_cjk_font_status


def _tpl_workspace_count(request=None) -> int:
    """Jinja global: number of files in the current user's workspace, rendered
    server-side into the sidebar badge (no extra fetch; shows in every nav
    state). 0 when disabled / not applicable."""
    try:
        from .core import workspace as _ws
        if not _ws.is_enabled() or request is None:
            return 0
        return _ws.count_files(request)
    except Exception:
        return 0


templates.env.globals["workspace_count"] = _tpl_workspace_count
# Per-tool English keyword aliases — typed in the sidebar search to find a
# tool by its English term (e.g. "stamp" → PDF 蓋章).
_TOOL_ALIASES = {
    "pdf-fill":           "form fill auto autofill 自動填寫 表單 廠商 申請書 vendor application 填寫",
    "pdf-stamp":          "stamp seal chop sign signature logo 印章 蓋章 簽名 簽章 Logo",
    "pdf-watermark":      "watermark mark draft confidential overlay tile 浮水印 機密 標記 防偽",
    "pdf-merge":          "merge combine join concat append 合併 串接 結合",
    "pdf-split":          "split extract divide separate cut 分拆 切割 拆分 分割",
    "pdf-rotate":         "rotate orient orientation flip turn 轉向 旋轉 翻轉",
    "pdf-pages":          "pages reorder rearrange remove delete drop manage 頁面 排序 重排 整理 刪除",
    "pdf-pageno":         "page number numbering numbers footer header 頁碼 頁數 編號 注頁碼",
    "pdf-border":         "border frame outline slide 框線 加框 邊框 外框 頁框 投影片外框 線框 圓角 雙框 陰影 獎狀 證書",
    "office-to-pdf":      "convert convert-to-pdf office word excel powerpoint docx xlsx pptx odt ods odp 轉檔 轉成 文書 文件 辦公文件轉PDF 辦公文件轉 PDF 辦公文件 文書轉PDF 文書轉 PDF",
    "office-convert":     "convert format interconvert docx doc odt rtf txt xlsx xls ods csv pptx ppt odp 格式互轉 格式轉換 互轉 轉格式 轉檔 文書檔互轉 試算表互轉 簡報互轉 docx轉doc odt轉doc xlsx轉xls pptx轉odp 另存新檔 換格式 舊版格式 相容",
    "pdf-extract-images": "extract images pictures jpg png assets 擷取 提取 圖片 影像 抽圖",
    "pdf-to-image":       "convert image images png jpg jpeg raster rasterize export office word excel powerpoint docx xlsx pptx odt ods odp 文書轉圖片 轉圖 轉圖片 轉png 轉成圖片 影像 匯出圖片 Word 轉圖 Excel 轉圖 PPT 轉圖 辦公文件轉圖片 辦公文件",
    "image-to-pdf":       "image images photos jpg jpeg png gif tiff webp heic combine merge convert scan a4 letter page size rotate reorder 圖片 照片 相片 掃描 轉 PDF 合併 排序 旋轉 頁面大小 A4",
    "scan-merge":         "scan merge combine composite id card front back two sided id-card overlay position place a4 white background crop detect color photo png jpg pdf 掃描 拼合 合併 疊合 證件 身分證 身份證 正面 反面 正反面 雙面 護照 駕照 健保卡 名片 同一張 白底 A4 位置 自動偵測 彩色 去背 淨白 拖曳",
    "pdf-editor":         "editor edit annotate annotation whiteout redact text textbox shape pencil draw highlight sticky note scribus 編輯 編輯器 標註 註記 塗黑 遮蓋 手繪 螢光筆 便箋 文字框 修圖",
    "pdf-extract-text":   "extract text content txt markdown md docx word odt reflow paragraph ocr llm 擷取文字 取出文字 轉文字 轉 word 轉 markdown 段落重排 LLM 重排",
    "pdf-ocr":            "ocr searchable scan image text layer invisible tesseract chi_tra chi_sim eng apple preview live text macos 文字層 補建 掃描 圖檔 變可選 可搜尋 透明文字層 蘋果 預覽程式 LiveText 圖轉文字",
    "doc-translate":      "document translation translate whole file docx odt xlsx ods pptx odp word excel powerpoint keep layout same format 文件翻譯 整份翻譯 翻譯文件 保留排版 原格式 文書檔 試算表 簡報 翻譯後下載 llm ai 機器翻譯 中翻英 英翻中 日文 韓文",
    "translate-doc":      "translate translation translator sentence by sentence parallel side by side bilingual english chinese japanese korean traditional simplified llm ai ollama 翻譯 逐句 並排 對照 中英 中翻英 英翻中 機器翻譯 LLM 大語言模型 句子 中翻日 日翻中",
    "pdf-compress":       "compress compression shrink reduce size optimize optimise slim jpeg dpi downsample ghostscript gs subset font 壓縮 縮小 瘦身 減肥 檔案小 降解析度 去重複 Ghostscript",
    "doc-deident":        "deident deidentify de-identification redact redaction mask masking anonymize anonymise pii personal data privacy gdpr 個資 個人資料 去識別化 敏感資料 編修 不可逆遮蔽 資料遮罩 遮蔽 塗黑 脫敏 脫敏化 匿名 身分證 手機 Email 統編 信用卡 車牌 地址",
    "text-deident":       "text deident deidentify redact mask anonymize fake substitute replace paste txt md docx odt 文字 文本 純文字 貼上 去識別化 敏感資料 編修 遮罩 替換假資料 假資料 假名 替代 PII 個資 隱私 身分證 手機 Email",
    "pdf-encrypt":        "encrypt password protect lock aes permission restrict owner user 加密 密碼 保護 權限 禁列印 禁複製 禁編輯 AES",
    "pdf-decrypt":        "decrypt unlock remove password 解鎖 解密 解除 密碼 移除密碼",
    "pdf-metadata":       "metadata xmp author title strip clean remove producer creator 中繼資料 中繼 修訂歷史 去識別 metadata 清除 作者 標題 標籤 XMP",
    "pdf-hidden-scan":    "hidden content javascript js embedded launch uri whitetext offpage scan remove 隱藏 掃描 JavaScript 嵌入檔 白字 頁面外 外部連結 啟動 風險 資安",
    "pdf-attachments":    "attachment attachments embedded file extract pdf paperclip 附件 嵌入檔 萃取 取出 EmbeddedFiles",
    "pdf-wordcount":      "wordcount word count words chars characters letter 字數 統計 字元 字數統計 統計圖表 chart histogram frequency 高頻詞 頻率 段落 句子 paragraph sentence 閱讀時間 reading time stats statistics analytics",
    "pdf-annotations":    "annotations annotation comments comment markup highlight underline strikeout sticky-note review todo extract export 註解 批註 標註 螢光筆 底線 刪除線 文字註解 圖章 自由文字 手繪 審閱 待辦 校稿 合約 修訂",
    "pdf-annotations-strip":   "strip remove delete clean annotations comments markup 註解 批註 移除 刪除 清除 清除註解 移除註解 校稿後清除",
    "pdf-annotations-flatten": "flatten bake burn annotations comments markup permanent lock final 平面化 扁平化 燒入 鎖定 定稿 收件方無法移除",
    "doc-diff":           "diff compare comparison difference changes contract audit review pdf word excel powerpoint odf odt ods odp office document 差異 比對 比較 合約審閱 變更 改版 文件 PDF Word Excel PowerPoint ODF",
    "text-diff":          "text diff compare paste plain text snippet log code clipboard quick 文字 文本 差異 比對 比較 純文字 貼上 片段 紀錄 程式碼 剪貼簿",
    "aes-zip":            "zip aes encrypt archive password email attachment winzip 7zip keka archive utility 加密 壓縮檔 密碼保護 AES 寄信 附件 打包",
    "pdf-nup":            "nup n-up multiple pages per sheet imposition 2up 4up 6up 8up tile tiled layout grid imposition 多頁合併 多合一 拼貼 省紙 N合一 2合1 4合1 講義 草稿 版面",
    "text-list":          "list lines line sort dedup deduplicate unique uniq filter grep head tail shuffle randomize trim case lower upper title prefix suffix paste txt csv xlsx ods docx odt pdf 清單 列表 行 排序 去重 去重複 重複 篩選 過濾 取頭 取尾 洗牌 隨機 大小寫 前綴 後綴 處理 整理 唯一 unique",
    "einvoice-scan":      "einvoice e-invoice invoice scan qr qrcode receipt taiwan taipei vat tax 電子發票 發票 掃描 QR Code 條碼 二維碼 統編 統一發票 載具 銷售額 稅額 買方 賣方 報帳 記帳 對帳",
    "vat-lookup":         "vat lookup company business taiwan 統編 統一編號 查詢 反查 公司 行號 政府機關 學校 行業 名稱 地址 BGMOPEN 賣方 買方",
    "pdf-to-office":      "pdf2docx pdf-to-docx pdf-to-word pdf-to-odt convert reverse word document docx odt openoffice libreoffice oxoffice pdf word 轉 word 轉檔 反轉 反向 轉文書 轉成 word 轉成 odt PDF轉文書檔 PDF轉Word PDF轉ODT 文字方塊 可編輯",
    "pdf-to-slides":      "pdf2pptx pdf-to-pptx pdf-to-odp pdf-to-slides pptx odp powerpoint impress keynote slide slides presentation deck convert reverse PDF轉簡報 PDF轉PPT PDF轉PPTX PDF轉ODP 簡報 投影片 簡報檔 轉簡報 轉投影片 轉成 ppt 轉成 pptx 版面重現 一頁一張投影片 可編輯 PDF轉簡報檔 PDF 轉簡報檔",
    "pdf-to-markdown":    "pdf2md pdf-to-md pdf-to-markdown markdown md llm rag pymupdf4llm convert structured text 轉 markdown 轉成 markdown 結構化 標題層級 表格 餵 LLM RAG 預處理 chunking 文件轉換",
    "markdown-to-doc":    "md2doc md-to-doc markdown-to-doc markdown to pdf docx odt office convert export theme style commonmark gfm Markdown 轉 PDF 轉 Word 轉文書 轉換 主題 配色 渲染 報告 文件 預覽 Markdown轉辦公文件 辦公文件",
    "pdf-page-size":      "page size paper resize normalize unify uniform a4 a3 a5 b4 b5 letter legal tabloid mixed landscape portrait orientation scale fit crop margin 頁面尺寸 紙張 尺寸統一 統一尺寸 大小 A4 A3 混合尺寸 混排 直向 橫向 方向 縮放 留白 裁切 填滿 送印 裝訂 圖說 標案 工程",
    "pdf-seam-stamp":     "seam seal perforation straddle chop stamp across pages tamper evident contract tender anti swap sliced rotate jitter random 騎縫章 騎縫印 騎縫 跨頁章 防抽換 防換頁 抽換 合約 標案 投標 用印 蓋章 切片 分片 側邊 對開 亂數 角度",
    "pdf-bookmark":       "bookmark bookmarks outline toc table of contents index navigation nested level chapter merge combine tender annual report 書籤 大綱 目錄 導覽 索引 章節 層級 巢狀 合併 串接 標案 投標 年報 結案報告 目錄頁 可點 跳頁 加書籤 建目錄",
    "submission-check":   "submission check submit review verify validate checklist pre-flight preflight completeness case bid tender application 送件 送審 投標 標案 申請 檢核 檢查 核對 清單 缺件 漏件 齊備 完整性 驗收 案件 送出前",
    "transit-proof":      "transit proof commute travel receipt ticket hsr tra mrt bus taxi easycard ipass reimbursement expense 乘車 乘車證明 交通 車票 高鐵 台鐵 捷運 公車 計程車 悠遊卡 一卡通 差旅 出差 報帳 核銷 憑證 整理",
}
# Per-tool color class. Both home page and sidebar use the same palette
# classes, so a given tool always shows the same colored tile regardless
# of ordering. Palette has 14 slots (see .tile-color-N in platform.css);
# we assign each tool its hashed preferred index, then walk forward to
# resolve collisions so every tool ends up with a distinct color as long
# as the tool count ≤ palette size.
_TOOL_COLORS = 14

def _preferred_color_index(tool_id: str) -> int:
    h = 0
    for c in tool_id:
        h = (h * 131 + ord(c)) & 0xFFFFFFFF
    return h % _TOOL_COLORS

def _assign_unique_colors(ids: list[str]) -> dict[str, int]:
    taken: set[int] = set()
    out: dict[str, int] = {}
    # Sort so assignment is deterministic across restarts and not
    # dependent on tool-registry iteration order.
    for tid in sorted(ids):
        idx = _preferred_color_index(tid)
        for _ in range(_TOOL_COLORS):
            if idx not in taken:
                break
            idx = (idx + 1) % _TOOL_COLORS
        taken.add(idx)
        out[tid] = idx
    return out

_tool_color_map = _assign_unique_colors([t.metadata.id for t in tools])

def _tpl_tool_tiles() -> list[dict]:
    """每個工具的圖示名稱與配色索引，給「作業清單」在每一列前面畫工具圖示用。

    圖示的 SVG 定義只在 `components/icons.html` 那個 macro 裡（單一事實來源），
    所以由樣板先把用得到的圖示畫進隱藏區，JS 再複製對應的節點 —— 不要在 JS 裡
    另外抄一份 path，那遲早會跟 macro 長得不一樣。
    """
    return [{"id": t.metadata.id, "name": t.metadata.name,
             "icon": getattr(t.metadata, "icon", "tool") or "tool",
             "color": _tool_color_map.get(t.metadata.id, 0)}
            for t in tools]


templates.env.globals["tool_tiles"] = _tpl_tool_tiles

_nav_tool_items = [
    {
        "id": t.metadata.id,
        "name": t.metadata.name,
        "description": t.metadata.description,
        "icon": t.metadata.icon,
        "category": t.metadata.category or "其他",
        "url": f"/tools/{t.metadata.id}/",
        "keywords": _TOOL_ALIASES.get(t.metadata.id, ""),
        "color": _tool_color_map.get(t.metadata.id, 0),
        # 這支工具只在哪些介面語言底下列出來（空的＝全部）
        "locales": getattr(t.metadata, "locales", ()),
    }
    for t in tools
]
templates.env.globals["nav_tools"] = _nav_tool_items
# Group sidebar tools by category, preserving first-seen order. Each entry:
# {title: str, items: [...]}. Used by base.html to render one collapsible
# group per category instead of a single big "工具" list.
_grouped: dict[str, list] = {}
for _item in _nav_tool_items:
    _grouped.setdefault(_item["category"], []).append(_item)
templates.env.globals["nav_tool_groups"] = [
    # Use ``tools`` (not ``items``) — Jinja2 attribute access on a dict
    # resolves ``g.items`` to the built-in dict.items method, breaking
    # ``g.items|length``.
    {"title": title, "tools": tools_in} for title, tools_in in _grouped.items()
]
templates.env.globals["nav_settings"] = [
    {"icon": "gear", "name": "相依套件檢查", "description": "OCR 引擎 / Office / 字型 等系統套件狀態",
     "url": "/admin/sys-deps",
     "keywords": "system dependency dependencies check status tesseract ocr office libreoffice oxoffice 系統 依賴 相依 套件 檢查 狀態"},
    {"icon": "eye", "name": "系統狀態", "description": "CPU / RAM / 磁碟 / 網路 / 各使用者檔案用量",
     "url": "/admin/system-status",
     "keywords": "system status cpu ram memory disk network io usage user storage 系統 狀態 主機 資源 監控 用量 容量 監測"},
    {"icon": "image", "name": "資產管理", "description": "上傳/編輯印章、簽名、Logo",
     "url": "/admin/assets",
     "keywords": "asset assets stamp signature logo image upload 圖片 印章 簽名 浮水印"},
    {"icon": "image", "name": "企業 Logo / 識別", "description": "更換 topbar、favicon、首頁 logo",
     "url": "/admin/branding",
     "keywords": "branding logo favicon enterprise corporate identity custom 企業 識別 標誌 標識 公司"},
    {"icon": "archive", "name": "設定備份 / 匯入", "description": "全站 admin 設定打包匯出 / 匯入",
     "url": "/admin/settings-export",
     "keywords": "backup export import zip migrate move config settings restore 備份 匯出 匯入 還原 搬遷"},
    {"icon": "building", "name": "公司資料", "description": "管理多公司基本資料",
     "url": "/admin/profile",
     "keywords": "company profile vendor info 廠商 公司"},
    {"icon": "book", "name": "翻譯對照字典", "description": "單位內部專有名詞怎麼翻（逐句翻譯 / 文件翻譯共用）",
     "url": "/admin/translation-glossary",
     "keywords": "glossary terminology term dictionary translate translation "
                 "brand proper noun 字典 對照 術語 專有名詞 翻譯 品牌 不要翻"},
    {"icon": "book", "name": "同義詞", "description": "PDF 標籤對應字典",
     "url": "/admin/synonyms",
     "keywords": "synonym synonyms alias dictionary label mapping 字典 同義 詞"},
    {"icon": "page", "name": "表單範本", "description": "已記住的表單版型",
     "url": "/admin/templates",
     "keywords": "template form layout 表單 範本 樣板"},
    {"icon": "gear", "name": "轉檔引擎設定", "description": "LibreOffice / OxOffice 路徑與順序",
     "url": "/admin/conversion",
     "keywords": "conversion office libreoffice oxoffice path engine 轉檔 引擎 路徑"},
    {"icon": "gear", "name": "LLM 設定", "description": "12 個工具的 LLM AI 加值（附加功能，預設關閉）",
     "url": "/admin/llm-settings",
     "keywords": "llm ai ollama qwen vision review 校驗 模型 大語言模型"},
    {"icon": "gear", "name": "API Token", "description": "對外呼叫 /api/* 的認證 token",
     "url": "/admin/api-tokens",
     "keywords": "api token bearer auth authentication 認證 令牌"},
    {"icon": "text", "name": "字型管理", "description": "PDF 編輯器可用字型（系統 + 開源 CJK + 內建）",
     "url": "/admin/fonts",
     "keywords": "font fonts cjk chinese 字型 字體 中文字型 台灣字型 noto"},
    {"icon": "text", "name": "OCR 引擎", "description": "EasyOCR (主) + Tesseract (備) 雙 OCR 引擎切換與訓練檔管理",
     "url": "/admin/ocr-langs",
     "keywords": "ocr engine easyocr tesseract traineddata trained data lang language chi_tra chi_sim jpn kor 引擎 訓練檔 語言 安裝 中文 日文 韓文 繁中 簡中"},
    {"icon": "id-card", "name": "統編資料庫", "description": "公司 / 政府機關 / 學校統編反查（財政部 BGMOPEN + 補充來源）",
     "url": "/admin/vat-db",
     "keywords": "vat tax id business registry company taiwan einvoice einvoice-scan bgmopen vat-lookup 統編 統一編號 公司 商業 登記 反查 賣方 發票 財政部 行政院 地方政府 機關 學校"},
    # ---- v1.1.0 auth / perm / audit pages ----
    # 認證設定 always visible — that's where admin enables auth in the first place.
    {"icon": "lock", "name": "認證設定", "description": "啟用本機 / LDAP / AD 認證",
     "url": "/admin/auth-settings",
     "keywords": "auth authentication ldap ad active directory login 認證 登入"},
    {"icon": "lock", "name": "SSO 單一登入", "description": "OIDC / SAML 附加登入（M365 / Google / Keycloak）",
     "url": "/admin/sso", "requires_auth": True,
     "keywords": "sso single sign-on oidc openid connect saml oauth entra azure microsoft 365 google keycloak okta authentik federation 單一登入 聯合身分 第三方登入"},
    # `requires_auth=True` items are filtered out from the sidebar nav when
    # auth is off (see _nav_settings_visible). The endpoints still exist;
    # admin can hit them directly via URL — but in the off state they're
    # functionally meaningless (no users / sessions to manage).
    {"icon": "id-card", "name": "使用者管理", "description": "建立 / 編輯 / 停用使用者",
     "url": "/admin/users", "requires_auth": True,
     "keywords": "user users account 使用者 帳號 帳戶"},
    {"icon": "building", "name": "群組管理", "description": "本機群組與成員",
     "url": "/admin/groups", "requires_auth": True,
     "keywords": "group groups team 群組 團隊"},
    {"icon": "shield", "name": "角色管理", "description": "工具權限角色定義",
     "url": "/admin/roles", "requires_auth": True,
     "keywords": "role roles rbac permission 角色 權限"},
    {"icon": "gear", "name": "權限矩陣", "description": "使用者 / 群組 × 工具 對應",
     "url": "/admin/permissions", "requires_auth": True,
     "keywords": "permission permissions matrix grant 權限 矩陣"},
    {"icon": "building", "name": "目錄瀏覽", "description": "AD/LDAP OU 樹 → 挑 OU 指派權限",
     "url": "/admin/directory", "requires_auth": True,
     "keywords": "directory ou tree ldap ad treeview 目錄 樹 組織單位 挑選 使用者"},
    {"icon": "eye", "name": "稽核記錄", "description": "登入 / 操作 / 設定變更追蹤",
     "url": "/admin/audit", "requires_auth": True,
     "keywords": "audit log activity history 稽核 記錄 記錄 操作 軌跡"},
    {"icon": "upload", "name": "上傳檔案記錄", "description": "誰上傳了什麼檔案到哪個工具",
     "url": "/admin/uploads", "requires_auth": True,
     "keywords": "uploads files history audit 上傳 檔案 記錄 歷史"},
    {"icon": "gear", "name": "記錄轉送", "description": "將稽核轉發到外部 syslog / CEF / GELF",
     "url": "/admin/log-forward", "requires_auth": True,
     "keywords": "syslog cef gelf forward log siem splunk graylog 轉發"},
    {"icon": "mail", "name": "作業完成通知", "description": "完成時通知送出者：站內 / Email / 通訊軟體",
     "url": "/admin/notify",
     "keywords": "notify notification email smtp mail teams slack telegram discord zulip nextcloud talk line webhook 通知 提醒 完成 郵件 信件 訊息 推播"},
    {"icon": "archive", "name": "檔案保留 / 清理", "description": "歷史檔案保留天數與自動清理",
     "url": "/admin/retention",
     "keywords": "retention cleanup history sweep gc gdpr 保留 清理 歷史"},
    {"icon": "clock", "name": "背景作業與併行度", "description": "背景作業監控、同時處理數、Office 轉檔併行上限",
     "url": "/admin/jobs",
     "keywords": "job jobs queue concurrency worker parallel pause cancel task background soffice memory oom 作業 工作 佇列 排隊 併行 同時 平行 暫停 取消 背景 記憶體 轉檔"},
    {"icon": "archive", "name": "工作區設定", "description": "啟用/停用使用者工作區、每人容量額度、保留時數",
     "url": "/admin/workspace",
     "keywords": "workspace quota storage user files save load disk 工作區 容量 額度 儲存 空間 啟用 停用 保留"},
    # History pages are admin-only and only meaningful when auth is on
    # (per spec: when auth off the pages don't appear at all).
    {"icon": "page", "name": "表單填寫歷史", "description": "已填表的歷史記錄",
     "url": "/admin/history/fill", "requires_auth": True,
     "keywords": "history fill 歷史 表單"},
    {"icon": "page", "name": "用印簽名歷史", "description": "蓋章 / 簽名 的歷史記錄",
     "url": "/admin/history/stamp", "requires_auth": True,
     "keywords": "history stamp 歷史 用印 簽名"},
    {"icon": "page", "name": "浮水印歷史", "description": "浮水印的歷史記錄",
     "url": "/admin/history/watermark", "requires_auth": True,
     "keywords": "history watermark 歷史 浮水印"},
]


# Stash the master list so the filter callable can read it without
# self-referencing the override below.
_NAV_SETTINGS_ALL = list(templates.env.globals["nav_settings"])
_NAV_TOOL_GROUPS_ALL = list(templates.env.globals["nav_tool_groups"])


def _nav_settings_visible(request=None):
    """Filter nav_settings based on auth state and viewer's role.

    v1.5.0+ 強職責分離：admin 看不到 user 隱私資料的 4 頁（fill/stamp/
    watermark history + uploads），那 4 頁只開給 auditor。設計理由：
    admin 雖管系統，但合規上不該偷看 user 真實檔案內容。

    - Auth OFF (單機模式): 只顯示不需 requires_auth 的項目
    - Auth ON, admin: 看全部 admin 頁，**不含** auditor-exclusive 4 頁
    - Auth ON, auditor: 只看到 audit / history / uploads / system-status
    - Auth ON, 其他: 看不到 admin 區
    """
    from .core import auth_settings as _as, permissions as _perm
    from .web.deps import _AUDITOR_EXCLUSIVE_PREFIXES, _AUDITOR_SHARED_PREFIXES
    enabled = _as.is_enabled()
    if not enabled:
        return [item for item in _NAV_SETTINGS_ALL
                if not item.get("requires_auth")]
    user = getattr(getattr(request, "state", None), "user", None) if request else None
    if not user:
        return []
    uid = user.get("user_id", 0)
    if _perm.is_admin(uid):
        # admin 看到的清單去掉 AUDITOR_EXCLUSIVE 4 頁
        return [item for item in _NAV_SETTINGS_ALL
                if not any(item["url"].startswith(p)
                           for p in _AUDITOR_EXCLUSIVE_PREFIXES)]
    if _perm.is_auditor(uid):
        # 稽核員只看到 SHARED + EXCLUSIVE 兩組
        allowed = _AUDITOR_SHARED_PREFIXES + _AUDITOR_EXCLUSIVE_PREFIXES
        return [item for item in _NAV_SETTINGS_ALL
                if any(item["url"].startswith(p) for p in allowed)]
    return []


def _nav_groups_for_locale(request=None):
    """依介面語言把「只在中文環境成立」的工具**標成停用**（`ToolMetadata.locales`）。

    **不是藏起來，是反灰點不下去**（使用者要求）—— 看得到有這支工具、也看得到
    它為什麼用不了，比整支消失好懂。繁體中文底下沒有任何一支會被標記。
    """
    from .core import ui_locale as _loc
    lang = _loc.resolve(request)
    if lang == _loc.DEFAULT_LOCALE:
        return _NAV_TOOL_GROUPS_ALL          # 最常見的路徑，不多做事
    out = []
    for g in _NAV_TOOL_GROUPS_ALL:
        items = []
        for t in g["tools"]:
            if _loc.tool_visible(t.get("locales"), lang):
                items.append(t)
            else:
                items.append({**t, "locked": True})
        out.append({"title": g["title"], "tools": items})
    return out


def _nav_tool_groups_visible(request=None):
    """Filter sidebar tool groups by the viewer's permissions **and** the UI language.

    Auth OFF → everything. Auth ON → only tools the user is allowed to
    use (matches the backend gate so users don't see tiles that 403)."""
    from .core import auth_settings as _as, permissions as _perm
    groups = _nav_groups_for_locale(request)
    if not _as.is_enabled():
        return groups
    user = getattr(getattr(request, "state", None), "user", None) if request else None
    if not user:
        return []
    et = _perm.effective_tools(user.get("user_id", 0))
    if et == "ALL":
        return groups
    out = []
    for g in groups:
        kept = [t for t in g["tools"] if t["id"] in et]
        if kept:
            out.append({"title": g["title"], "tools": kept})
    return out


@jinja2.pass_context
def _tpl_t(ctx, text: str) -> str:
    """樣板裡的取字函式：`{{ tr('我的作業') }}`。

    **key 就是繁體中文原文** —— 查不到翻譯時原樣回傳，畫面自動回退成中文。
    詳見 `app/core/i18n.py` 的說明（為什麼不用符號 key）。
    """
    from .core import i18n as _i18n, ui_locale as _loc
    return _i18n.translate(text, _loc.resolve(ctx.get("request")))


# **名字不能叫 `t`** —— 側欄那些 `{% for t in g.tools %}` 會把它蓋掉，
# 迴圈裡呼叫就變成「'dict' object is not callable」（實際踩到，整頁 500）。
templates.env.globals["tr"] = _tpl_t


def _tpl_ui_locale(request=None) -> str:
    """樣板裡問「現在是哪個介面語言」。**目前只有繁體中文有語系檔**，
    切成英文時字串仍然回退繁中（見 `ui_locale.resolve`）。"""
    from .core import ui_locale as _loc
    return _loc.resolve(request)


templates.env.globals["ui_locale"] = _tpl_ui_locale

# Override the static globals with callables that re-evaluate per request.
templates.env.globals["nav_settings"] = _nav_settings_visible
templates.env.globals["nav_tool_groups"] = _nav_tool_groups_visible

# Make templates, asset manager, job manager available to routers via app state
app.state.templates = templates
app.state.asset_manager = asset_manager
app.state.job_manager = job_manager

# Auth routes (login / logout / setup-admin) — always public
from .web.auth_routes import build_router as _build_auth_router  # noqa: E402

app.include_router(_build_auth_router(templates))

# SSO login routes (OIDC + SAML) — public, additional login method alongside
# the password backend. No-op endpoints when SSO is disabled in settings.
from .web.sso_routes import build_router as _build_sso_router  # noqa: E402

app.include_router(_build_sso_router(templates))

# Platform routes
from .web.router import build_router as _build_web_router  # noqa: E402

app.include_router(_build_web_router(templates, tools, settings.app_name, VERSION))

# User workspace (我的工作區) — page + save/list/serve/delete endpoints.
from .web.workspace_routes import build_router as _build_workspace_router  # noqa: E402

app.include_router(_build_workspace_router(templates))

# Shared asset images (logo / stamp / signature / watermark) — login-gated,
# read-only. Lets non-admin users see asset thumbnails / previews in the
# stamp / watermark / editor tools without exposing admin asset management.
from .web.asset_routes import build_router as _build_asset_router  # noqa: E402

app.include_router(_build_asset_router())

# Admin routes
from .admin.router import build_router as _build_admin_router  # noqa: E402

_admin = _build_admin_router(templates)

# Auth-related admin routes (auth-settings, users, groups, roles, permissions)
from .admin.auth_router import build_auth_router as _build_admin_auth_router  # noqa: E402

_admin.include_router(_build_admin_auth_router(templates))
app.include_router(_admin, prefix="/admin", tags=["admin"])

# Tool routes
mount_tools(app, tools)


# ---- Public branding endpoint (custom enterprise logo) ----
# 一定要在 auth gate _PUBLIC_PREFIXES 內，登入頁也能顯示自訂 logo。
from fastapi.responses import FileResponse as _FileResponse  # noqa: E402

@app.get("/branding/logo")
async def _branding_logo():
    from fastapi import HTTPException as _HTTPException
    from .core import branding as _branding
    p = _branding.get_custom_logo_path()
    if not p:
        raise _HTTPException(404, "no custom logo set")
    return _FileResponse(str(p), media_type="image/png",
                         headers={"Cache-Control": "no-cache"})


def _branding_logo_url() -> str:
    """Jinja global. 用法：`<img src="{{ branding_logo_url() or '預設 url' }}" />`"""
    from .core import branding as _branding
    return _branding.custom_logo_url()


templates.env.globals["branding_logo_url"] = _branding_logo_url


# ---- Friendly HTML error pages for 401/403/404 (browser navigations only) ----
# FastAPI 預設把 HTTPException 渲成 JSON `{"detail": "..."}`，瀏覽器 user 點到
# /admin/llm-settings 卻沒 admin 權限會看到光禿禿的一行 JSON，看起來像系統壞掉。
# 這個 handler 只攔 HTML navigation request（Accept: text/html），其他（API
# / fetch / curl）維持 JSON 行為不變。
from fastapi.exceptions import HTTPException as _HTTPException2  # noqa: E402

#: 錯誤訊息裡的危險字元 → **全形對應字元**。
#:
#: 一開始全部換成底線，結果把合法訊息也毀了 —— 全站有 14 句訊息本身就含這些
#: 字元（「image > 50MB」「1 <= cols*rows <= 64」「engine must be 'easyocr'」），
#: 洗成底線之後使用者看到的是「image ＿ 50MB」，等於為了防禦把訊息廢掉。
#:
#: 換成全形字元同時滿足兩件事：**讀起來一模一樣**，而且在 HTML 裡完全無害
#: （`＜` 不會開一個標籤、`＂` 不會跳脫屬性）。控制字元沒有全形對應，直接拿掉。
_UNSAFE_IN_MESSAGE = str.maketrans({
    "<": "＜", ">": "＞", '"': "＂", "'": "＇", "&": "＆", "`": "｀",
    "\r": " ", "\n": " ", "\t": " ", "\x00": "",
})


def scrub_error_detail(detail):
    """把錯誤訊息裡使用者可控的危險字元洗掉。

    **為什麼要在出口做，而不是逐一改呼叫端**：全站有 38 處把使用者送的檔名
    回填進錯誤訊息（`f"只支援 PDF：{f.filename}"` 這種形狀），散在 20 個檔案。
    逐一改一定會漏，而且下次有人新寫一支工具又會長回來。

    這跟 422 handler 是同一個判準（見下方那段說明）：載體是 JSON、有 nosniff、
    CSP 也擋著，所以**不可利用** —— 但「不可利用」不等於「應該回填」。使用者
    需要知道的是「哪個檔案不合格」，不需要把他送的位元組原樣拿回去。

    保留檔名的可辨識性（使用者上傳多個檔時要分得出是哪一個），只把 HTML 有
    意義的字元與控制字元換掉。
    """
    if isinstance(detail, str):
        return detail.translate(_UNSAFE_IN_MESSAGE)[:500]
    if isinstance(detail, dict):
        return {k: scrub_error_detail(v) for k, v in detail.items()}
    if isinstance(detail, (list, tuple)):
        return [scrub_error_detail(v) for v in detail]
    return detail


@app.exception_handler(_HTTPException2)
async def _friendly_http_exc(request: Request, exc: _HTTPException2):
    # 只對「瀏覽器導航」改成 HTML；JSON / XHR / API 維持原本行為
    accept = (request.headers.get("Accept") or "").lower()
    is_html_nav = ("text/html" in accept and not _looks_like_xhr(request))
    if not is_html_nav or exc.status_code not in (401, 403, 404):
        # default JSON behaviour
        return _JSONResponse({"detail": scrub_error_detail(exc.detail)},
                             status_code=exc.status_code,
                             headers={"X-Content-Type-Options": "nosniff"})
    titles = {
        401: ("需要登入", "請先登入後再使用此頁。"),
        403: ("沒有存取權限", str(exc.detail or "您的角色沒有使用此頁的權限。如有需要請聯絡管理員。")),
        404: ("找不到頁面", "請確認網址是否正確，或回首頁重新導覽。"),
    }
    title, msg = titles.get(exc.status_code, ("錯誤", str(exc.detail or "")))
    # `detail` 會被直接寫進 HTML。目前所有 401/403 的 detail 都是常數字串，但
    # 只要有一處寫成 f-string 帶進使用者輸入，這裡就是反射型 XSS —— 而打中的是
    # 正在看錯誤頁的**管理員** session。一律跳脫，不要靠「呼叫端都很小心」。
    title, msg = html_mod.escape(title), html_mod.escape(msg)
    # 401 多一個「去登入」按鈕；其他都給「回首頁」按鈕
    extra_btn = ""
    if exc.status_code == 401:
        extra_btn = '<a class="err-btn err-btn-primary" href="/login">去登入</a>'
    html = f"""<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{exc.status_code} {title} · {settings.app_name}</title>
<link rel="stylesheet" href="/static/css/platform.css">
<style>
  body {{ display:flex; align-items:center; justify-content:center; min-height:100vh;
         margin:0; background:#0f172a; font-family:-apple-system, 'PingFang TC',
         'Microsoft JhengHei', sans-serif; }}
  .err-card {{ background:#fff; border-radius:12px; padding:36px 40px; max-width:480px;
              text-align:center; box-shadow:0 12px 40px rgba(0,0,0,.4); }}
  .err-code {{ font-size:64px; font-weight:700; color:#cbd5e1; line-height:1; margin-bottom:8px; }}
  .err-title {{ font-size:20px; color:#0f172a; margin:8px 0 12px; }}
  .err-msg {{ color:#475569; font-size:14px; line-height:1.6; margin-bottom:24px; }}
  .err-btn {{ display:inline-block; padding:10px 22px; border-radius:6px; font-size:14px;
            font-weight:500; text-decoration:none; margin:0 4px; transition:background .12s; }}
  .err-btn-primary {{ background:#3b82f6; color:#fff; }}
  .err-btn-primary:hover {{ background:#2563eb; }}
  .err-btn-secondary {{ background:#f1f5f9; color:#1e293b; }}
  .err-btn-secondary:hover {{ background:#e2e8f0; }}
</style></head><body>
<div class="err-card">
  <div class="err-code">{exc.status_code}</div>
  <h1 class="err-title">{title}</h1>
  <p class="err-msg">{msg}</p>
  {extra_btn}
  <a class="err-btn err-btn-secondary" href="/">回首頁</a>
</div></body></html>"""
    return HTMLResponse(html, status_code=exc.status_code)


# Need HTMLResponse import for the handler above
from fastapi.responses import HTMLResponse  # noqa: E402

# 全域 JSON 解析錯誤處理：端點用 `await request.json()` 收到非 JSON / 壞掉的
# body 時，json.JSONDecodeError 原本會冒成 500（不雅且像伺服器出錯）。統一改回
# 400「Invalid JSON body」（客戶端錯誤）。涵蓋全部 ~80 個 request.json() 呼叫點，
# 不需逐一加 try/except。UI 一律送合法 JSON → 正常流程不受影響。
import hashlib as _hashlib  # noqa: E402
import json as _json  # noqa: E402
from fastapi.exceptions import (  # noqa: E402
    RequestValidationError as _RequestValidationError,
)

@app.exception_handler(_json.JSONDecodeError)
async def _json_decode_exc(request: Request, exc: _json.JSONDecodeError):
    return _JSONResponse({"detail": "Invalid JSON body"}, status_code=400)


# 毀損的 PDF / 假副檔名的檔案不是伺服器錯誤，是**客戶端給錯檔**。
#
# 全站有二十幾個端點在 `fitz.open()` 之後才發現檔案打不開 ——
# `fitz.FileDataError` 原本一路冒成 500「Internal Server Error」，使用者
# 只看到一句英文不知道該怎麼辦（2026-08-16 全端點壞輸入掃描抓到 28 處）。
# 跟上面的 JSONDecodeError 同一個做法：一個處理器涵蓋所有呼叫點，統一回
# 400 與可行動的中文訊息，不必逐一改 router。
import fitz as _fitz  # noqa: E402


@app.exception_handler(_fitz.FileDataError)
@app.exception_handler(_fitz.EmptyFileError)
async def _broken_pdf_exc(request: Request, exc: Exception):
    return _JSONResponse(
        {"detail": "檔案打不開 —— 可能已毀損、內容不完整，或副檔名與實際格式"
                   "不符。請確認來源檔可以正常開啟後再上傳一次。"},
        status_code=400)


# 辦公文件的**來源檔本身**讀不出來（毀損 / 被截斷 / 副檔名被改過 / zip 炸彈）。
# soffice 碰到這種檔案會**回傳 0 卻不產出任何檔案**，端點原本只能回一句
# 「轉檔成功但找不到輸出 .txt」—— 自相矛盾，而且對使用者毫無幫助
# （2026-09-06 使用者上傳一份被截斷的 docx 時回報）。
# `office_convert.ensure_readable()` 在送進 soffice **之前**就判斷得出來，
# 這裡統一轉成 400 —— 跟毀損 PDF 同一個做法，新工具自動涵蓋。
from .core.office_convert import (  # noqa: E402
    OfficeSourceError as _OfficeSourceError,
    OfficeUnavailableError as _OfficeUnavailableError,
)


@app.exception_handler(_OfficeSourceError)
async def _bad_office_source_exc(request: Request, exc: Exception):
    return _JSONResponse({"detail": str(exc)}, status_code=400)


# 這台機器沒有裝 Office 引擎 —— **部署層面**的問題，不是使用者送錯東西。
# 回 503（服務暫時無法提供）而不是 500：500 會讓人以為整個服務掛了而一直重試。
# 2026-09-06 CI 抓到 —— runner 上沒有 LibreOffice，Markdown 轉辦公文件回 500。
@app.exception_handler(_OfficeUnavailableError)
async def _office_unavailable_exc(request: Request, exc: Exception):
    return _JSONResponse({"detail": str(exc)}, status_code=503)


# 縮圖 / 預覽端點的頁碼在**路徑上**，所以「第 0 頁」「第 99 頁」這種是
# **使用者送錯網址**，不是伺服器壞掉。`render_page_png` 早就擋住了範圍
# （v1.14.31 修的「page_no=0 用負索引回最後一頁還 200 OK」），但它丟的
# `ValueError` 沒人接，一路冒成 500 —— 500 會讓人以為服務掛了而一直重試，
# 監控端也全是假警報（同上面毀損檔案那條）。
#
# 跟毀損檔案一樣用**一個全域處理器**涵蓋所有呼叫點：全站三十幾支縮圖 /
# 預覽端點形狀相同，逐支改的話下一支新工具又會漏。
from .core.pdf_preview import PageOutOfRange as _PageOutOfRange  # noqa: E402


@app.exception_handler(_PageOutOfRange)
async def _page_out_of_range_exc(request: Request, exc: Exception):
    return _JSONResponse({"detail": str(exc) or "頁碼超出範圍。"}, status_code=404)


# 參數驗證失敗（422）**不要把使用者送的原字串回填到回應裡**。
#
# FastAPI 預設的 422 會帶 `"input": "<使用者原文>"`。那串東西是攻擊者可控的，
# 掃描器因此把它判成反射型 XSS（外部掃描 2026-08-13 的 High）。
#
# 實際上不可利用 —— 載體是 `application/json`、又有 `X-Content-Type-Options:
# nosniff`，瀏覽器不會當 HTML 解析；就算落到 HTML 脈絡，CSP 的 script-src
# 沒有 unsafe-inline，注入的 inline script 缺 nonce 也執行不了。
#
# 但「不可利用」不等於「應該回填」：把原文吐回去本來就沒有必要（欄位名與
# 錯誤原因才是使用者需要的），拿掉它同時消掉整個反射面，成本近乎零。
# 這裡一次覆蓋全站所有端點的驗證錯誤，不必逐一改 route 簽章。
@app.exception_handler(_RequestValidationError)
async def _validation_exc(request: Request, exc: _RequestValidationError):
    safe = [
        {"loc": [str(x) for x in e.get("loc", [])],
         "msg": str(e.get("msg", ""))[:200],
         "type": str(e.get("type", ""))}
        for e in (exc.errors() or [])[:20]
    ]
    return _JSONResponse(
        {"detail": safe},
        status_code=422,
        headers={"X-Content-Type-Options": "nosniff"},
    )


# ---- Auth gate middleware ----
# When auth backend != 'off', every non-public request must carry a valid
# session cookie. Public paths (always reachable):
#   - /login, /logout, /setup-admin (auth UI itself)
#   - /static/*, /favicon.ico, /healthz (assets + health probe)
#   - /api/* (handled by the token gate below — has its own auth model)
# Unauthenticated browser request to a UI page → 302 /login?next=<path>.
# Unauthenticated direct hit to a JSON endpoint → 401 JSON (don't redirect
# an XHR to a login HTML page; that would just look like garbage).
from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402
from urllib.parse import quote as _qstr  # noqa: E402

#: **前綴**比對的公開路徑。只放「底下真的整片都公開」的東西。
#: 這裡的每一條都會讓 `/xxx*` 全部跳過認證閘 —— 新增前先問「有沒有可能有人
#: 之後加一條 `/login-admin` 之類的路由」。
_PUBLIC_PREFIXES = (
                    "/i18n/",         # 介面語系字典（純靜態字串，無使用者資料）
                    "/static/",
                    "/setup-admin",   # 需前綴：還有 /setup-admin/reuse-existing
                                      # （三個處理函式都自己再擋 is_enabled()）
                    "/api/",          # 見下方說明：整片略過認證閘，各端點自己擋
                    "/branding/",
                    "/auth/oidc/", "/auth/saml/")  # SSO 登入 / 回呼（尚未有 session）
#: **完全相等**才算公開。單一頁面一律放這裡，不要放進上面的前綴表。
#:
#: 原本 `/login` `/logout` `/healthz` `/favicon` `/2fa-verify` 同時列在兩邊，
#: 前綴那份先命中，於是這個集合**整個是死的** —— 讀的人以為 `/login-xyz`
#: 不公開，其實是公開的。目前沒有那種路由所以不成問題，但下一條新路由就會踩到。
@app.post("/ui-locale")
async def set_ui_locale(request: Request):
    """切換介面語言。**用 POST**（會改變狀態，不能用 GET 連結）。

    語言記在 cookie 而不是帳號設定：單機模式（未啟用認證）根本沒有「使用者」
    這回事，而語言偏好在那種環境同樣要能記住。之後要再疊「跟著帳號走」都可以。
    """
    from .core import ui_locale as _loc
    from .core.url_safety import safe_next
    form = await request.form()
    lang = _loc.normalise(str(form.get("locale") or "")) or _loc.DEFAULT_LOCALE
    # 只回自己站內 —— 從表單來的網址不可以直接拿去重導（open redirect）。
    # **自己寫一份判斷是錯的**：原本只擋 `//`，但瀏覽器會把 `/\evil.com` 的反斜線
    # 正規化成斜線 → 變成協定相對網址，照樣被導到站外。登入的 `next` 早就有一份
    # 擋得住的實作（含百分比解碼、反斜線、CRLF），共用它就好。
    back = safe_next(str(form.get("next") or "/"))
    resp = RedirectResponse(back, status_code=303)
    resp.set_cookie(_loc.COOKIE_NAME, lang, max_age=_loc.COOKIE_MAX_AGE,
                    httponly=True, samesite="lax",
                    secure=(request.url.scheme == "https"))
    return resp


@app.get("/i18n/{locale}.js")
async def ui_locale_dictionary(locale: str, request: Request):
    """前端字典。`static/js/i18n.js` 的 `tr()` 讀這裡設的 `window.__I18N__`。

    **為什麼是獨立的 JS 檔而不是塞進 HTML**：字典有一千多條（~90 KB），
    塞進每一頁的 HTML 等於每次載入都重傳一次；獨立檔案瀏覽器可以快取。

    **繁體中文根本不會載這支** —— 樣板只在切到別的語言時才輸出這個
    `<script src>`，中文底下 `tr()` 原樣回傳，零成本零風險。
    """
    from .core import i18n as _i18n, ui_locale as _loc
    lang = _loc.normalise(locale)
    if not lang or lang == _loc.DEFAULT_LOCALE:
        # 預設語言沒有字典（也不該有人來要）—— 回空的比 404 好，
        # 因為 404 會在主控台留下紅字，看起來像壞了。
        body = "window.__I18N__={};"
    else:
        body = ("window.__I18N__="
                + _json.dumps(_i18n.catalog(lang), ensure_ascii=False) + ";")
    # 字典有 100 KB 左右，而它只在升級時才會變 —— 給 ETag 讓瀏覽器換頁時
    # 只送一次 If-None-Match 拿 304，不要每頁重下一份。
    # `no-cache` 不是「不快取」，是「用之前先問一下」（同 `_RevalidatingStatic`）。
    etag = '"%s"' % _hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/javascript", headers=headers)


_PUBLIC_EXACT = {"/login", "/logout", "/healthz", "/favicon.ico", "/2fa-verify", "/ui-locale"}


def _looks_like_xhr(request: Request) -> bool:
    """Heuristic: XHR / fetch from a script vs. a top-level browser nav."""
    accept = (request.headers.get("Accept") or "").lower()
    xrw = (request.headers.get("X-Requested-With") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return True
    if xrw == "xmlhttprequest":
        return True
    return False


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Add baseline browser-side defence headers on every response.

    OWASP A05 (Security Misconfiguration) coverage:
    - X-Content-Type-Options: nosniff — stops MIME sniffing letting
      uploaded SVG/HTML execute as the wrong type
    - X-Frame-Options: SAMEORIGIN — clickjacking defence (older browsers)
    - Content-Security-Policy: see CSP_DIRECTIVES below
    - Referrer-Policy: strict-origin-when-cross-origin — don't leak full
      URLs (which contain upload_id UUIDs) to external sites
    - Permissions-Policy: deny features we don't use; reduces fingerprinting
      and stops a XSS from accessing camera/mic/geolocation
    - Strict-Transport-Security: only when request comes via HTTPS so we
      don't lock plain-HTTP intranet installs out for a year
    """
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    # CSP — permissive enough to allow inline <style> / event handlers
    # used by templates + the one external CDN (Fabric.js for pdf-editor),
    # tight enough to block exfil to attacker hosts (XSS data exfil) and
    # plugin objects.
    # script-src 用 per-request nonce（CSRF middleware 設於 request.state）→ 移除
    # 'unsafe-inline'，注入的 inline <script> 不帶 nonce 無法執行（強化 XSS 防護）。
    # 模板的 90 個 inline <script> 已加 nonce，inline 事件處理器已改 addEventListener。
    # 萬一 nonce 缺失（理論上不會）退回 'unsafe-inline' 以免整站 JS 失效。
    # style-src 也 nonce 化（Phase 2）：84 個 <style> 區塊加 nonce、1371 個 inline
    # style 屬性改 data-s（CSS class）、動態的改 data-style + CSSOM 套用 →
    # 移除 style-src 'unsafe-inline'（跨瀏覽器,不依賴 style-src-attr）。
    _nonce = getattr(getattr(request, "state", None), "csp_nonce", "") or ""
    _script_src = (f"script-src 'self' 'nonce-{_nonce}'; " if _nonce
                   else "script-src 'self' 'unsafe-inline'; ")
    _style_src = (f"style-src 'self' 'nonce-{_nonce}'; " if _nonce
                  else "style-src 'self' 'unsafe-inline'; ")
    h.setdefault("Content-Security-Policy", (
        "default-src 'self'; "
        + _script_src + _style_src +
        # data: for QR PNGs (TOTP setup) + base64 thumbs; blob: for PDF.js
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        # XHR/fetch only to self → blocks browser-driven SSRF / data exfil
        "connect-src 'self'; "
        # blocks plugins (Flash / PDF readers as objects)
        "object-src 'none'; "
        # clickjacking defence (modern equivalent of X-Frame-Options)
        "frame-ancestors 'self'; "
        # blocks <base href="evil"> injection
        "base-uri 'self'; "
        # forms only post to self
        "form-action 'self'"
    ))
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy",
                 "camera=(), microphone=(), geolocation=(), interest-cohort=()")
    # 動態 HTML 頁不快取（可能含敏感內容；清 ZAP「Re-examine Cache-control」Info）。
    # 靜態資產（CSS/JS/字型/圖）content-type 非 text/html → 不受影響，維持可快取。
    if "text/html" in h.get("content-type", ""):
        h.setdefault("Cache-Control", "no-store")
    # 走共用 helper —— `X-Forwarded-Proto` 可能是逗號串（多層代理）。
    # 自己寫一份的話會出現「cookie 帶了 Secure 但同一份回應沒有 HSTS」
    # 這種互相矛盾的狀態（v1.14.31 對抗式驗證實測）。
    from .core.client_ip import forwarded_scheme
    is_https = forwarded_scheme(request) == "https"
    if is_https:
        # 1 年（反向代理建議由「app 或 nginx 擇一」設 HSTS,避免重複標頭;
        # 見 OPS.md / docs 反向代理章節）
        h.setdefault("Strict-Transport-Security",
                     "max-age=31536000; includeSubDomains")
    return response


@app.middleware("http")
async def _capture_upload_filename(request: Request, call_next):
    """Sniff multipart bodies for `filename="..."` so audit / log forwarding
    can record what file was uploaded — without each tool having to add
    `request.state.upload_filename = file.filename` manually.

    Trade-off: we read the full body into memory (then replay it via
    `_receive` for FastAPI's normal parsing). The handler was going to do
    `await file.read()` anyway so net memory cost is just the raw bytes
    held twice for ~one tick. Skip if content-length > 500 MB to avoid
    surprising RAM growth on absurd uploads."""
    ctype = (request.headers.get("content-type") or "").lower()
    if (request.method in ("POST", "PUT")
            and ctype.startswith("multipart/form-data")
            and (request.scope.get("path") or request.url.path).startswith("/tools/")):
        clen_str = request.headers.get("content-length") or "0"
        try:
            clen = int(clen_str)
        except ValueError:
            clen = 0
        if 0 < clen < 500 * 1024 * 1024:
            body = await request.body()
            # Scan first 16 KB for filename(s); plenty of room for the
            # multipart preamble without choking on huge bodies.
            import re as _re
            names = _re.findall(rb'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]*)"?', body[:16384])
            if names:
                try:
                    decoded = [n.decode("utf-8", errors="replace") for n in names if n]
                    if decoded:
                        request.state.upload_filename = decoded[0]
                        if len(decoded) > 1:
                            request.state.upload_filenames = decoded
                            request.state.upload_count = len(decoded)
                except Exception:
                    pass

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            # Replay the body for the rest of the middleware chain + handler.
            request._receive = receive
    return await call_next(request)


@app.middleware("http")
async def _slow_request_log(request: Request, call_next):
    """記錄慢請求。

    原本的 access log 只有「請求發生了」，沒有耗時 —— 2026-07-30 正式機整站卡住
    時，只能靠人工把時間戳相減才發現輪詢從 2 秒變成最長 226 秒才回應。加上耗時
    之後，這種情況在 log 裡是一眼看得出來的。

    **只記超過門檻的**：每個請求都記會把 log 淹掉，而正常請求的耗時沒有價值。
    """
    import time as _t
    t0 = _t.perf_counter()
    try:
        return await call_next(request)
    finally:
        dt = _t.perf_counter() - t0
        if dt >= _loop_watchdog.SLOW_REQUEST_SECONDS:
            path = request.scope.get("path") or ""
            busy = ""
            try:
                st = job_manager.stats()
                busy = (f"（執行中 {st.get('running')}、排隊 {st.get('queued')}）")
            except Exception:  # noqa: BLE001
                pass
            logger.warning("慢請求 %.1f 秒：%s %s%s",
                           dt, request.scope.get("method", "?"), path, busy)


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    from .core import auth_settings, sessions, permissions
    from .core.client_ip import real_client_ip
    from .core.job_manager import set_current_actor
    if not auth_settings.is_enabled():
        # 認證關閉時仍要記來源 IP —— 管理區的工作監控要看得出是誰送的
        set_current_actor(None, real_client_ip(request))
        return await call_next(request)
    # Use the RAW ASGI scope path, not request.url.path: request.url is rebuilt
    # from the Host header, which a crafted "Host: x/../" can poison so url.path
    # diverges from the path actually routed — bypassing these prefix-based
    # gates (Starlette BADHOST, CVE-2026-48710). scope["path"] is Host-independent.
    path = request.scope.get("path") or request.url.path
    if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)
    # Bearer-token middleware (executed earlier in the chain) may have already
    # validated an API token and stashed user on request.state.user. In that
    # case skip the cookie session check — bearer token is a valid auth path.
    bearer_user = getattr(request.state, "user", None)
    if bearer_user:
        user = bearer_user
    else:
        # Need a valid session cookie
        token = request.cookies.get(sessions.COOKIE_NAME, "")
        user = sessions.lookup(token) if token else None
        if user:
            # 記下「這個 session 還活著」—— 管理員才答得出「現在有誰登入著」。
            # 內部有節流（>60 秒才真的寫 DB），不會變成每請求一次寫入。
            sessions.touch(token)
        if not user:
            # Reverse-proxy (Kerberos/SPNEGO) SSO: a trusted upstream proxy may
            # have asserted the domain user via a header. Try that before
            # falling back to /login. Only reached for PROTECTED paths (public
            # prefixes already returned above), so proxy SSO never runs on
            # /login itself → jtdt-admin break-glass stays reachable + no
            # redirect loop (the success redirect carries a session cookie, so
            # the follow-up request skips this branch entirely).
            from .core import proxy_sso
            if proxy_sso.is_enabled():
                status, puser = proxy_sso.resolve_user(request)
                if status == proxy_sso.OK and puser:
                    from .web.auth_routes import complete_login
                    ua = request.headers.get("User-Agent", "")
                    # OK path means the proxy was verified trusted, so its
                    # X-Forwarded-For names the real workstation → record that
                    # as the session IP (consistent with password login).
                    cip = proxy_sso._real_client_ip(request)
                    nxt = path + ("?" + request.url.query if request.url.query else "")
                    # 2FA-aware: auditor / totp_required users are still routed
                    # through /2fa-verify — proxy SSO cannot bypass forced 2FA.
                    return complete_login(request, puser, remember=False,
                                          ip=cip, ua=ua, next_url=nxt)
                # Header absent / untrusted / lookup failed. When fallback is
                # OFF, a request without a valid trusted header gets a hard 401
                # instead of the /login form.
                if not proxy_sso.fallback_login_enabled():
                    return _JSONResponse({"error": "unauthorized",
                                          "detail": "需經企業入口 (Kerberos SSO) 登入"},
                                         status_code=401)
                # else fall through to the normal /login redirect below.
            if _looks_like_xhr(request):
                return _JSONResponse({"error": "unauthorized",
                                      "detail": "請先登入"}, status_code=401)
            # Preserve where the user was trying to go.
            next_q = "?next=" + _qstr(path + ("?" + request.url.query if request.url.query else ""))
            return RedirectResponse("/login" + next_q, status_code=302)
        # Stash user on request.state for downstream handlers (audit context, etc).
        request.state.user = user

    # 讓這個請求裡送出的背景工作知道歸屬。放在 contextvar 而不是要求 25 個工具
    # 各自把 request 傳進 job_manager.submit()：那樣新工具很容易忘記傳，而少了
    # 歸屬的作業就不會出現在「我的作業」裡（使用者會以為作業丟了）。
    set_current_actor(user, real_client_ip(request))

    # Per-tool permission gating: any path under /tools/<tool_id>/... requires
    # the user to have that tool granted (via roles or direct grant). admin
    # role short-circuits to ALL.
    tool_id = ""
    if path.startswith("/tools/"):
        rest = path[len("/tools/"):]
        tool_id = rest.split("/", 1)[0] if rest else ""
        if tool_id and not permissions.user_can_use_tool(user["user_id"], tool_id):
            if _looks_like_xhr(request):
                return _JSONResponse({"error": "forbidden",
                                      "detail": "您沒有使用此工具的權限"},
                                     status_code=403)
            return _JSONResponse(
                {"error": "forbidden",
                 "detail": f"您沒有使用「{tool_id}」的權限，請聯絡管理員"},
                status_code=403,
            )

    # Audit POST/PUT/DELETE actions on tools — done AFTER the handler runs
    # so handlers can stash uploaded filenames on `request.state.upload_*`
    # for us to pick up. Middleware can't peek into a multipart body without
    # consuming/replaying the stream, so this opt-in handler-side annotation
    # is the simplest reliable way to get the actual filename into audit.
    response = await call_next(request)
    if tool_id and request.method in ("POST", "PUT", "DELETE"):
        from .core import audit_db as _ad
        from .core import client_ip as _cip
        rest = path[len("/tools/"):]
        action = rest.split("/", 2)[1] if "/" in rest else "(root)"
        clen = request.headers.get("content-length") or "0"
        ctype = (request.headers.get("content-type") or "").split(";", 1)[0]
        details = {
            "method": request.method,
            "action": action,                    # extract / merge / save / …
            "path": path,
            "size_bytes": int(clen) if clen.isdigit() else 0,
            "content_type": ctype,
            "status": getattr(response, "status_code", 0),
        }
        # Optional handler-supplied details (filename, file count, etc.).
        # Set in tool routes via:  request.state.upload_filename = file.filename
        for k in ("upload_filename", "upload_filenames", "upload_count"):
            v = getattr(request.state, k, None)
            if v is not None:
                # Trim the audit key prefix for readability in the JSON dump.
                details[k.replace("upload_", "")] = v
        _ad.log_event(
            "tool_invoke",
            username=user["username"],
            ip=_cip.real_client_ip(request),
            target=tool_id,
            details=details,
        )

    return response


# ---- API token enforcement middleware ----
# Gates /api/* endpoints (and, when enforce is ON, some /tools/*/submit).
# When `enforce=false` (grace period), this does nothing and web UI works
# as before; when enforce=true, requests missing / wrong Bearer token → 401.

@app.middleware("http")
async def _api_token_gate(request: Request, call_next):
    from .core.api_tokens import api_tokens
    # Raw scope path — Host-header-independent (see _auth_gate / BADHOST note).
    path = request.scope.get("path") or request.url.path

    # Only guard explicit API surfaces; never block UI pages, static files,
    # or admin (admin is browser-based and has its own access control).
    # Cover both root-level `/api/*` and tool-prefixed `/tools/<x>/api/*`
    # + `/tools/<x>/convert` — both是外部 API 介面。
    # pdf-to-office 的轉換前後對照縮圖 / 改善報告同時被「瀏覽器（session）」與
    # 「API 呼叫者（Bearer token）」使用，屬雙重存取路徑：帶 Bearer 就驗 token，
    # 沒帶就落回 session auth_gate（避免在 enforce 開時誤擋瀏覽器預覽）。
    # `/admin/**` 屬「雙重存取路徑」：
    #   * API.md 有記載可用 `Authorization: Bearer ADMIN_TOKEN` 呼叫
    #     （/admin/api/llm/settings、/admin/api/sys-deps …）→ 帶 token 就要驗。
    #   * 同一批路徑同時是管理區網頁自己用的（session cookie，沒有 Bearer）。
    #     管理區有 22 個路徑含 `/api/`（含 `/admin/jobs/api/list`），如果把它們
    #     當成純 API，「API token 強制驗證」一打開，管理員用瀏覽器開管理頁就會
    #     拿到「需要有效的 API token」401 —— 管理區直接壞掉，而訊息看起來像設定
    #     有問題，不像這個判斷寫錯。
    # 所以：**沒帶 token 就落回 session + require_admin**，帶了就驗。
    # 不可以只擋、也不可以只放 —— 兩條路徑都是真的有人在用。
    is_admin_ui = path.startswith("/admin/") or path == "/admin"
    is_dual_access = (
        "/pdf-to-office/preview/" in path
        or "/pdf-to-office/report/" in path
        or is_admin_ui
    )
    is_api = (
        path.startswith("/api/")
        or "/api/" in path  # tool-prefixed e.g. /tools/pdf-rotate/api/pdf-rotate
        or path.endswith("/convert")  # pdf-to-image / pdf-to-office
        or is_dual_access
    )
    if not is_api:
        return await call_next(request)
    enforce = api_tokens.is_enforced()
    # 若提供 Authorization: Bearer，無論 enforce 是否開都驗 token；驗過即視為已認證
    # （讓 auth_gate 不再因為缺 session cookie 而 redirect 到 /login）。
    auth_hdr = request.headers.get("Authorization") or ""
    presented = None
    if auth_hdr.lower().startswith("bearer "):
        parts = auth_hdr.split(None, 1)
        presented = parts[1].strip() if len(parts) > 1 else None
    if not presented:
        presented = request.query_params.get("token")
    has_bearer_attempt = bool(presented)
    if not has_bearer_attempt and (not enforce or is_dual_access):
        # 沒帶 token 時：enforce 關 → 維持舊行為（API 公開）；
        # 雙重存取路徑 → 落回 session auth_gate 由瀏覽器 cookie 把關。
        return await call_next(request)

    # Accept: Authorization: Bearer <token>  OR  ?token=<token>
    auth = request.headers.get("Authorization") or ""
    presented = None
    if auth.lower().startswith("bearer "):
        parts = auth.split(None, 1)
        presented = parts[1].strip() if len(parts) > 1 else None
    if not presented:
        presented = request.query_params.get("token")

    token_row = api_tokens.lookup(presented)
    if token_row is None:
        return _JSONResponse(
            {"error": "unauthorized",
             "detail": "需要有效的 API token（Authorization: Bearer ...）"},
            status_code=401,
        )
    # When auth is on, attach the token's owning user to request.state.user
    # so downstream perm checks (if any) are scoped to that user. Tokens
    # without an owner are treated as having no permission when auth is on
    # (admin must assign — fail closed).
    from .core import auth_settings
    if auth_settings.is_enabled():
        owner_id = token_row.get("owner_user_id")
        if not owner_id:
            return _JSONResponse(
                {"error": "forbidden",
                 "detail": "API token 尚未指派持有者；請至 admin → API Token 設定"},
                status_code=403,
            )
        from .core import auth_db
        urow = auth_db.conn().execute(
            "SELECT id, username, display_name, source, enabled FROM users WHERE id=?",
            (owner_id,),
        ).fetchone()
        if not urow or not urow["enabled"]:
            return _JSONResponse(
                {"error": "forbidden",
                 "detail": "Token 持有者帳號已停用或不存在"},
                status_code=403,
            )
        request.state.user = {
            "user_id": urow["id"], "username": urow["username"],
            "display_name": urow["display_name"] or urow["username"],
            "source": urow["source"],
        }
    return await call_next(request)


# ---- Legacy redirects (renamed tools) ----
@app.api_route("/tools/pdf-diff", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
@app.api_route("/tools/pdf-diff/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
@app.api_route("/tools/pdf-diff/{rest:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def _redirect_pdf_diff(rest: str = ""):
    """`pdf-diff` was renamed to `doc-diff` in v1.1.61 (now also handles
    Office / ODF). Catch every sub-path and method so:
      - bookmarks (GET /tools/pdf-diff/) still land on the new page,
      - legacy API callers (POST /tools/pdf-diff/compare) keep working.

    Use 308 — unlike 301/302, RFC 7538 says 308 MUST preserve method + body
    so a POST stays a POST instead of becoming a GET on the new URL."""
    target = "/tools/doc-diff/" + rest if rest else "/tools/doc-diff/"
    return RedirectResponse(target, status_code=308)


# ---- Shared API: job status + result download ----
# /api/* bypasses the cookie auth gate (it has its own token model), so these
# job endpoints must enforce per-job ownership THEMSELVES — otherwise any
# caller who knows/guesses a job_id could read/cancel another user's job
# result. See _job_access below.
def _job_access(job, request) -> bool:
    """`/api/jobs/*` 的存取控制。

    * 認證關閉 → 開放（單機模式沒有帳號可比對；`/my-jobs` 頁面已照實說明
      「知道連結的人都能下載」，不要讓使用者誤以為清單按來源電腦分開等於隔離）。
    * 認證開啟 → 必須是作業的擁有者；未登入一律拒絕。
    * **沒有擁有者的作業（`owner_id is None`）只有管理員能碰。**

    最後那條是修掉一個實際存在的漏洞：原本的邏輯是「無主作業由第一個登入者
    認領」，但那行 `job.owner_id = uid` 改的是 `job_manager.get()` 從資料庫
    重建出來的**臨時物件**，從來沒有寫回資料庫 —— 於是每個登入者查都「認領」
    一次，結果是**任何登入者都拿得到無主作業**。註解與實際行為不符，是最難
    發現的一類 bug（實測比對才抓到）。

    無主作業的合法存取者無從判斷（可能是認證啟用前送出的），因此改為 fail-secure：
    一般使用者拒絕、管理員放行（支援與排除問題需要）。這些作業本來也會在保留
    期限內被清掉。

    回 404 而不是 403 —— 不對非擁有者確認這個 id 是否存在。
    """
    try:
        from .core import auth_settings as _as, permissions as _perm, \
            sessions as _se
        if not _as.is_enabled():
            return True
        u = getattr(request.state, "user", None)
        if not u:
            tok = request.cookies.get(_se.COOKIE_NAME, "")
            u = _se.lookup(tok) if tok else None
        uid = u.get("user_id") if u else None
        if uid is None:
            return False
        if job.owner_id is None:
            return bool(_perm.is_admin(int(uid)))
        if int(job.owner_id) == int(uid):
            return True
        # 別人的作業 —— 管理員能不能看，由**同一份政策**決定（外部稽核 F10：
        # 原本這裡嚴格比對擁有者，而上傳檔那條路管理員直接放行，同一份文件
        # 走哪條路決定看不看得到）。
        if _perm.is_admin(int(uid)):
            from .core import upload_owner as _uo
            return _uo.admin_override_allowed(
                int(uid), f"job:{job.id}", owner_id=job.owner_id, request=request)
        return False
    except Exception:
        return False


@app.get("/my-jobs", response_class=HTMLResponse)
async def my_jobs_page(request: Request):
    """「我的作業」—— 轉換在背景跑，關掉頁面後回來這裡找結果。"""
    return templates.TemplateResponse(request, "my_jobs.html",
                                      {"request": request})


@app.get("/api/my/inbox")
async def api_my_inbox(request: Request, limit: int = 10):
    """站內通知：我最近完成的作業 + 未讀數。

    **刻意不另外開一張通知表** —— 這裡的「通知」就是「我的作業結束了」，
    jobs.sqlite 已經有全部資訊。另存一份就得雙寫，兩邊一旦不同步就會出現
    「通知說完成了，作業清單裡卻沒有」；而且還要另外養一套保留期與清理排程。
    改用「上次查看時間」推導未讀，作業被清掉時通知自動跟著消失，不會留下
    點了 404 的項目。
    """
    from .core import job_store, notify_settings as _ns
    scope = _job_scope(request)
    if scope["mode"] == "user" and scope["owner_id"] is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    kw = ({"owner_id": scope["owner_id"]} if scope["mode"] == "user"
          else {"client_ip": scope["client_ip"]})
    rows = job_store.list_jobs(limit=max(1, min(int(limit or 10), 50)), **kw)
    seen = float(_ns.get_prefs(_notify_key(request)).get("inbox_seen_at") or 0)
    tool_names = {t.metadata.id: t.metadata.name for t in tools}
    items, unread = [], 0
    for r in rows:
        if r["status"] not in ("done", "error", "cancelled", "interrupted"):
            continue
        fin = float(r["finished_at"] or r["updated_at"] or 0)
        is_new = fin > seen
        if is_new:
            unread += 1
        items.append({
            "id": r["id"],
            "tool_id": r["tool_id"],
            "tool_name": tool_names.get(r["tool_id"], r["tool_id"]),
            "filename": _job_display_name(r.get("meta") or {},
                                          r["result_filename"]),
            "status": r["status"],
            "finished_at": fin,
            "elapsed": round(max(0.0, fin - r["created_at"]), 1),
            "unread": is_new,
            "has_result": bool(r["result_path"]
                               and Path(r["result_path"]).exists()),
        })
    return {"unread": unread, "items": items}


@app.post("/api/my/inbox/seen")
async def api_my_inbox_seen(request: Request):
    """全部標示為已讀 —— 只記一個時間戳，不逐筆寫狀態。"""
    from .core import notify_settings as _ns
    scope = _job_scope(request)
    if scope["mode"] == "user" and scope["owner_id"] is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    _ns.save_prefs(_notify_key(request), {"inbox_seen_at": time.time()})
    return {"ok": True}


def _notify_key(request) -> str:
    """這位使用者的通知偏好儲存鍵（沿用工作區的鍵，兩者對象一致）。"""
    from .core import workspace as _ws
    scope = _job_scope(request)
    if scope["mode"] == "user":
        return _ws.key_for_user_id(scope["owner_id"])
    return _ws.key_for_user_id(None)


@app.get("/api/my/notify")
async def api_my_notify(request: Request):
    """我的通知偏好 + 管理員開了哪些管道。"""
    from .core import notify_channels as _nc, notify_settings as _ns
    scope = _job_scope(request)
    if scope["mode"] == "user" and scope["owner_id"] is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    avail = _ns.enabled_channels()
    return {
        "system_enabled": _ns.is_enabled(),
        "available": [{"id": c, "label": _nc.CHANNEL_INFO[c]["label"],
                       # 尚未對真實服務驗證過的管道 —— 使用者也該知道，
                       # 不然勾了收不到會以為是自己設定錯
                       "dev": c in _nc.DEV_CHANNELS,
                       # personal＝必須填目的地；dual＝填了就私訊、沒填走團隊頻道
                       "personal": c in _ns.PERSONAL_CHANNELS,
                       "dual": c in _ns.DUAL_CHANNELS}
                      for c in avail],
        "prefs": _ns.get_prefs(_notify_key(request)),
        # 「以目前設定實際會收到哪些管道」。
        #
        # **這個欄位一定要有** —— 畫面上那句「目前不會收到任何通知」就是靠它
        # 判斷要不要顯示。少了它，前端拿到 undefined，警語會**永遠顯示**：
        # 使用者勾了 Email 又按儲存，警語還杵在那裡，只會以為沒存成功
        #（實際回報過）。
        #
        # 算法在 resolve_for_user：含「從未設定 → 預設全收」與「個人管道沒有
        # 可送達位址 → 跳過」兩條規則，不要在前端重算一次。
        "usable_channels": _ns.resolve_for_user(_notify_key(request))[0],
        # 帳號上的信箱（AD / LDAP 的 mail、SSO 的 email claim，或本人 / 管理員
        # 填的）。UI 用它告訴使用者「不填也會寄到這裡」—— 接目錄的環境本來就
        # 不該再要求每個人手動輸入一次自己的信箱。
        "account_email": _ns._account_email(_notify_key(request)),
        # 本機帳號可自己改（側欄「我的帳號」）；目錄帳號要洽管理員。
        # UI 要據此指路，不然使用者只會看到「沒有信箱」而不知道去哪設定。
        "email_editable": (getattr(request.state, "user", None) or {}
                           ).get("source") == "local",
    }


@app.post("/api/my/notify")
async def api_my_notify_save(request: Request):
    from .core import notify_settings as _ns
    scope = _job_scope(request)
    if scope["mode"] == "user" and scope["owner_id"] is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    # 使用者只能改自己的偏好；管道憑證在 admin 那層，這裡碰不到
    return {"ok": True, "prefs": _ns.save_prefs(_notify_key(request), body)}


def _safe_view_url(v) -> str:
    """作業的「回到頁面」連結。只允許本站的相對路徑。

    這個值來自建立作業時寫進 meta 的字串。今天全部由我們自己的程式寫入，但
    meta 是個開放的欄位 —— 一旦哪天有使用者輸入流進去，沒有這道檢查就會變成
    開放重導向 / `javascript:` 連結。共用 `safe_next` 的判斷（拒協定相對、
    CRLF、百分比編碼變形）。
    """
    if not isinstance(v, str) or not v:
        return ""
    from .core.url_safety import safe_next
    out = safe_next(v)
    return "" if out == "/" and v != "/" else out


def _job_display_name(meta: dict, result_filename: str | None) -> str:
    """作業清單上顯示的名稱。

    各工具塞進 job meta 的欄位長得不一樣（25 個呼叫點裡只有 5 個帶 filename，
    其餘是 count / upload_id / asset_id …），所以名稱在這裡統一推導 —— 與其
    要求每個工具都記得傳，不如集中處理，新工具也自動受惠。
    """
    name = meta.get("filename")
    if name:
        return str(name)
    if result_filename:
        return str(result_filename)
    n = meta.get("count")
    if isinstance(n, int) and n > 0:
        return f"{n} 個檔案"
    return ""


def _job_scope(request) -> dict:
    """「我的作業」的歸屬範圍。

    認證開啟 → 綁使用者帳號。認證關閉 → 沒有「我」這個概念，退而用**來源 IP**：
    單機自用只有 127.0.0.1（完全正確），辦公室內網至少不同電腦看不到彼此的。
    NAT 後面會混在一起 —— UI 有標明，不可當成真正的權限（認證關閉時本來就沒有
    權限可言，這只是避免把別人的作業攤在同一個列表上）。
    """
    from .core import auth_settings as _as, sessions as _se
    from .core.client_ip import real_client_ip
    if not _as.is_enabled():
        return {"mode": "ip", "client_ip": real_client_ip(request)}
    u = getattr(request.state, "user", None)
    if not u:
        tok = request.cookies.get(_se.COOKIE_NAME, "")
        u = _se.lookup(tok) if tok else None
    uid = u.get("user_id") if u else None
    return {"mode": "user", "owner_id": int(uid) if uid is not None else None}


@app.get("/api/jobs")
async def api_job_list(request: Request, active: bool = False,
                       limit: int = 50, offset: int = 0):
    """列出「我的作業」—— 讓使用者關掉頁面之後還找得回自己的轉換結果。

    在這之前 job_id 只活在該分頁的 JS 變數裡，關掉分頁就等於結果檔遺失（檔案還
    在 temp，但沒有任何人知道它的 id）。
    """
    from .core import job_store
    scope = _job_scope(request)
    if scope["mode"] == "user" and scope["owner_id"] is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    kw = ({"owner_id": scope["owner_id"]} if scope["mode"] == "user"
          else {"client_ip": scope["client_ip"]})
    rows = job_store.list_jobs(active_only=bool(active), limit=limit,
                               offset=offset, **kw)
    tool_names = {t.metadata.id: t.metadata.name for t in tools}
    # 疊上記憶體中的即時狀態：進度刻意不寫 DB（高頻寫入），只從 DB 讀的話
    # 進度條永遠是 0
    live = job_manager.live_snapshot()
    qpos = job_manager.queue_positions()
    out = []
    for r in rows:
        meta = r.get("meta") or {}
        lv = live.get(r["id"]) or {}
        out.append({
            "id": r["id"],
            "tool_id": r["tool_id"],
            "tool_name": tool_names.get(r["tool_id"], r["tool_id"]),
            "status": lv.get("status", r["status"]),
            "progress": lv.get("progress", r["progress"]),
            "message": lv.get("message") or r["message"],
            "queue_pos": qpos.get(r["id"]),
            # 這件作業有沒有插隊（管理員指定的優先派送名單）
            "priority": bool(lv.get("priority")),
            "priority_rank": lv.get("priority_rank"),
            "error": r["error"],
            "filename": _job_display_name(meta, r["result_filename"]),
            "result_filename": r["result_filename"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "elapsed": round(max(0.0, (r["finished_at"] or time.time())
                                 - r["created_at"]), 1),
            # 結果檔可能已被 TTL 清掉 —— 一定要實際看檔案在不在，不能只看狀態，
            # 否則使用者會點到一個 404 的下載鈕。
            "has_result": bool(r["result_path"]
                               and Path(r["result_path"]).exists()),
            # 自動存入工作區的結果（成功 / 失敗原因）—— 失敗要看得見，
            # 無聲失敗會讓使用者以為存好了，隔天檔案卻不在
            "workspace": meta.get("workspace"),
            # 有些工具的產出不是一個檔案，而是一頁對照表（逐句翻譯）——
            # 那種作業要能點回原本那一頁看結果，而不是給一顆下載鈕。
            "view_url": _safe_view_url(meta.get("view_url")),
        })
    # 工作區停用時不會自動存 → 告訴使用者結果還剩多久，讓他知道要在什麼時候
    # 之前取走（而不是隔天回來才發現不見了）
    from .core import retention as _ret, workspace as _ws
    return {"jobs": out, "scope": scope["mode"],
            "workspace_enabled": _ws.is_enabled(),
            "result_ttl_hours": int(_ret.get().get("jobs_hours") or 0),
            "total": job_store.count_jobs(**kw),
            "active": job_store.count_jobs(active_only=True, **kw)}


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str, request: Request):
    job = job_manager.get(job_id)
    # Same 404 for "not found" and "not your job" — don't confirm existence to
    # non-owners (no enumeration).
    if not job or not _job_access(job, request):
        return JSONResponse({"error": "job not found"}, status_code=404)
    # 有人在輪詢 = 頁面還開著。「完成後自動存入工作區」只在使用者離開後才需要。
    job_manager.mark_polled(job_id)
    return job.to_public()


@app.post("/api/jobs/{job_id}/cancel")
async def api_job_cancel(job_id: str, request: Request):
    """停止執行中的 job（標記取消，背景執行緒在下一 checkpoint 中止並丟棄結果）。"""
    job = job_manager.get(job_id)
    if not job or not _job_access(job, request):
        return JSONResponse({"error": "job not found"}, status_code=404)
    ok = job_manager.cancel(job_id)
    return {"ok": ok, "status": job.status}



# `_PUBLIC_PREFIXES` 含 "/api/"，所以根層級的 /api/* **完全跳過 `_auth_gate`**
# （沒有 session 檢查、也沒有 per-tool 權限檢查）；而 API token 的 enforce 預設是
# False。兩者相乘的結果是這幾個端點在認證開啟的站台上仍然免認證 —— 實測未登入
# 者只要從公開的 /login 頁拿一個 CSRF token 就能呼叫（v1.14.6 資安稽核確認）。
# /api/jobs 與 /api/my/* 沒事是因為它們各自在 handler 內做了歸屬檢查；這幾個沒有。
# 修法不是動 `_PUBLIC_PREFIXES`（會破壞 bearer token 流程），而是照同樣的模式在
# handler 上掛 `require_tool`，順便讓工具權限也生效（原本連授權都繞過了）。
@app.get("/api/vat-lookup/{vat}")
async def api_vat_lookup(vat: str,
                         _u: dict = Depends(_require_tool("vat-lookup"))):
    """反查統編 → 公司資料 (M4)。

    來源：本地 vat_db.sqlite（admin 預先匯入）。
    Public endpoint — 任何登入使用者都能用（不像 admin 才能改資料）。
    """
    from .core import vat_db
    if not vat or len(vat) != 8 or not vat.isdigit():
        raise _HTTPException2(400, "vat 必須是 8 位數字")
    result = vat_db.lookup_vat(vat)
    if not result:
        raise _HTTPException2(404, "查無此統編")
    return result


@app.post("/api/convert-to-pdf")
async def api_convert_to_pdf(file: UploadFile = File(...),
                             _u: dict = Depends(_require_tool("office-to-pdf"))):
    """Convert one Office file to PDF, return the PDF bytes inline.

    Used by the PDF-only tools' frontends to offer "auto-convert this Word
    file to PDF first?" without each tool reimplementing the LibreOffice
    plumbing.
    """
    from .core import office_convert
    from .core.http_utils import content_disposition
    from fastapi import File, UploadFile  # noqa: F401  (force import)
    name = file.filename or "input"
    out_name = Path(name).stem + ".pdf"
    if name.lower().endswith(".pdf"):
        # No-op: pass through.
        data = await file.read()
        return Response(content=data, media_type="application/pdf",
                        headers={"Content-Disposition": content_disposition(out_name, "inline")})
    if not office_convert.is_office_file(name):
        return JSONResponse({"error": f"不支援的檔案格式：{name}"}, status_code=400)
    data = await file.read()
    if not data:
        return JSONResponse({"error": "empty file"}, status_code=400)
    import uuid as _uuid
    src = settings.temp_dir / f"conv_{_uuid.uuid4().hex}_{Path(name).name}"
    out = settings.temp_dir / (src.stem + ".pdf")
    try:
        src.write_bytes(data)
        await office_convert.convert_to_pdf_async(src, out)
        pdf_bytes = out.read_bytes()
    finally:
        for fp in (src, out):
            try: fp.unlink()
            except OSError: pass
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(out_name, "inline")},
    )


@app.get("/api/jobs/{job_id}/download")
@app.get("/api/jobs/{job_id}/download/{_filename}")
async def api_job_download(job_id: str, request: Request, _filename: str | None = None):
    """Download a job's result. Enforces per-job ownership (see _job_access).

    The optional trailing filename segment is only so the browser saves with a
    sane extension; the file served is ALWAYS the owning job's own result_path.

    (Security note: an earlier version fell back to globbing every user's
    `*_work/` dir for a file matching the basename — that let an unauthenticated
    caller read any user's output by guessing a common name like `output.pdf`.
    Removed. If in-memory job state was lost on restart, we now 404 and the
    user re-runs, rather than risk cross-user disclosure.)
    """
    job = job_manager.get(job_id)
    if not job or not _job_access(job, request):
        return JSONResponse({"error": "no result"}, status_code=404)
    if job.result_path and job.result_path.exists():
        return FileResponse(
            path=str(job.result_path),
            filename=job.result_filename or job.result_path.name,
            media_type="application/octet-stream",
        )
    return JSONResponse({"error": "no result"}, status_code=404)


@app.post("/api/llm-review")
async def api_llm_review(
    file: UploadFile = File(...),
    _u: dict = Depends(_require_tool("pdf-fill")),
    company_id: str = "",
    max_rounds: int = 0,
):
    """Standalone LLM review API: detect+fill the PDF, then run LLM review.
    Returns the review result + summary of placements. Useful for external
    integrations / scripted workflows.

    Failure modes (returned in body, HTTP 200 unless input invalid):
    - LLM disabled                → {"error": "LLM 未啟用"}
    - LLM connection / model fail → {"error": "...", "review": {...errors...}}
    """
    from .core.llm_settings import llm_settings
    from .core.llm_review import review, filled_from_placements
    from .core.profile_manager import profile_manager
    from .tools.pdf_fill import service as fill_service
    import uuid as _uuid

    if not llm_settings.is_enabled():
        return JSONResponse({"error": "LLM 未啟用，請至 /admin/llm-settings 啟用"}, status_code=400)

    data = await file.read()
    if not data:
        return JSONResponse({"error": "empty file"}, status_code=400)

    name = file.filename or "input.pdf"
    if not name.lower().endswith(".pdf"):
        return JSONResponse({"error": "只支援 PDF"}, status_code=400)

    upload_id = _uuid.uuid4().hex
    src = settings.temp_dir / f"llmrev_{upload_id}.pdf"
    dst = settings.temp_dir / f"llmrev_{upload_id}_filled.pdf"
    src.write_bytes(data)

    # The whole fill + review pipeline is blocking (soffice + sync httpx);
    # run it in a thread so the FastAPI event loop stays responsive for
    # other users while a long LLM call is in flight.
    import asyncio as _asyncio

    def _run() -> dict:
        profile = profile_manager.get(company_id or None)
        report = fill_service.fill_pdf(src, dst, profile["fields"])
        filled = filled_from_placements(report.placements, profile.get("labels"))
        result = review(
            src, filled, page_index=0,
            max_rounds=max_rounds or None,
            profile_keys=list(profile["fields"].keys()),
        )
        return {
            "fill_report": {
                "detected": report.detected_count,
                "filled": report.filled_count,
            },
            "review": result.to_dict(),
        }

    try:
        return await _asyncio.to_thread(_run)
    except Exception:  # noqa: BLE001
        # v1.5.4 CodeQL py/stack-trace-exposure: 不漏 stack trace 給 user
        logger.exception("LLM review job failed")
        return JSONResponse(
            {"error": "LLM 處理失敗,請查看 server log"}, status_code=500)
    finally:
        for fp in (src, dst):
            try: fp.unlink()
            except OSError: pass


@app.get("/api/jobs/{job_id}/download-png")
async def api_job_download_png(job_id: str, request: Request):
    """Render the job's result PDF to PNG(s); zip when multi-page or batch.

    Single-PDF + single-page → PNG. Multi-page PDF → ZIP of PNGs (page_001.png,
    page_002.png …). If the result was already a ZIP of PDFs, all PDFs are
    extracted and rendered to PNGs in the same ZIP.
    """
    def _work():
        # 這裡算的是預覽圖（純 CPU）。直接跑在事件迴圈上會讓**全站**
        # 在這段時間都不回應 —— 頁數多或 DPI 高時特別明顯。包成閉包
        # 丟到執行緒之後，慢的只有按下去的那個人自己。
        import io
        import shutil
        import tempfile
        import zipfile as _zip
        import fitz

        job = job_manager.get(job_id)
        if not job or not _job_access(job, request):
            return JSONResponse({"error": "no result"}, status_code=404)
        if not job.result_path or not job.result_path.exists():
            return JSONResponse({"error": "no result"}, status_code=404)
        src = job.result_path
        base_name = (job.result_filename or src.name)
        # **要落在 settings.temp_dir 底下**：系統暫存目錄沒有人替我們清，
        # 而我們自己的清理迴圈只掃 `settings.temp_dir`（而且只刪檔案、跳過
        # 目錄）→ 原本 `mkdtemp(prefix="job_png_")` 建在 /tmp 的那些資料夾
        # 永遠留著（外部稽核 F09）。
        settings.temp_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="job_png_", dir=str(settings.temp_dir)))

        pdfs: list[tuple[str, Path]] = []  # (label_stem, pdf_path)
        if src.suffix.lower() == ".zip":
            with _zip.ZipFile(src) as zf:
                for info in zf.infolist():
                    if info.filename.lower().endswith(".pdf"):
                        out = tmp / Path(info.filename).name
                        out.write_bytes(zf.read(info))
                        pdfs.append((Path(info.filename).stem, out))
        else:
            pdfs.append((Path(base_name).stem, src))

        # **逐頁算、逐頁寫進磁碟上的 zip**。原本是把每頁的 PNG bytes 收成一個
        # list，再做一份 BytesIO 的 zip，再寫一份到磁碟 —— 一份大文件同時持有
        # 三份資料（外部稽核 F09）。
        #
        # 先數一次頁數（**開檔不算圖**，很便宜）決定要出單張還是 zip；
        # 真正算圖時**每份 PDF 只開一次**，不要為了省記憶體改成每頁開一次
        # （那會把 200 頁的文件變成開 200 次檔）。
        page_counts: list[tuple[str, Path, int]] = []
        total = 0
        for stem, pdf in pdfs:
            with fitz.open(str(pdf)) as doc:
                page_counts.append((stem, pdf, doc.page_count))
                total += doc.page_count
        if total == 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return JSONResponse({"error": "no pages"}, status_code=400)

        try:
            if total == 1:
                stem, pdf, _ = page_counts[0]
                out = _served_tmp_path(".png")
                with fitz.open(str(pdf)) as doc:
                    out.write_bytes(
                        doc[0].get_pixmap(dpi=150, alpha=False).tobytes("png"))
                return FileResponse(
                    path=str(out), filename=f"{stem}.png",
                    media_type="image/png",
                    background=BackgroundTask(_unlink_quietly, out),
                )
            zip_path = _served_tmp_path(".zip")
            with _zip.ZipFile(zip_path, "w", _zip.ZIP_DEFLATED) as zf:
                for stem, pdf, count in page_counts:
                    with fitz.open(str(pdf)) as doc:
                        for i in range(count):
                            pix = doc[i].get_pixmap(dpi=150, alpha=False)
                            zf.writestr(f"{stem}_p{i + 1:03d}.png",
                                        pix.tobytes("png"))
                            del pix          # 不要讓上一頁的緩衝撐到下一頁
            return FileResponse(
                path=str(zip_path),
                filename=Path(base_name).stem + ".png.zip",
                media_type="application/zip",
                background=BackgroundTask(_unlink_quietly, zip_path),
            )
        finally:
            # 解出來的中間 PDF 這時已經用不到了 —— 立刻收掉。
            # （產出本身放在受管理的暫存目錄，串流結束後由 background task 刪，
            #   萬一行程中途死掉還有 2 小時的暫存清理當補救。）
            shutil.rmtree(tmp, ignore_errors=True)

    def _work_limited():
        # 幾十頁的算圖不可以無限併行（見 `_PNG_EXPORT_LIMIT`）。
        with _png_export_sem:
            return _work()

    return await _asyncio.to_thread(_work_limited)


#: PNG 匯出是「一次算幾十頁圖」的重活，而這條路**不經過作業佇列的准入判斷**
#: （它是下載端點，不是背景作業）。不設上限的話，幾個人同時按下載就能把
#: 記憶體與 CPU 吃光（外部稽核 F09）。
#: 這不是完整的准入控制，是一道便宜且有效的閘門。
_PNG_EXPORT_LIMIT = 2
_png_export_sem = threading.Semaphore(_PNG_EXPORT_LIMIT)


def _served_tmp_path(suffix: str) -> Path:
    """要回傳給使用者的暫存檔路徑 —— **平鋪在 `settings.temp_dir` 裡**。

    清理迴圈只掃這個目錄、而且**只刪檔案不刪目錄**，所以產出不可以藏在
    子資料夾裡（不然沒有人會清它）。檔名用 UUID，與既有上傳檔的慣例一致。
    """
    import uuid as _uuid
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    return settings.temp_dir / f"job_png_{_uuid.uuid4().hex}{suffix}"


def _unlink_quietly(path: Path) -> None:
    """串流結束後刪掉暫存產出（刪不掉也不要吵 —— 還有清理迴圈當補救）。"""
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


async def _sweep_temp_files_loop():
    """Periodically delete files in temp_dir whose mtime is older than
    ``temp_ttl_seconds``. Each user's upload is keyed by a UUID filename,
    so this is safe across concurrent users — a file currently being
    read stays valid even after unlink on POSIX. We skip files modified
    within the TTL window to avoid deleting in-progress uploads."""
    import asyncio
    import time as _time
    interval = max(60, int(settings.cleanup_interval_seconds))
    ttl = max(300, int(settings.temp_ttl_seconds))
    while True:
        try:
            cutoff = _time.time() - ttl
            removed = 0
            for p in settings.temp_dir.iterdir():
                try:
                    if not p.is_file():
                        continue
                    if p.stat().st_mtime < cutoff:
                        p.unlink(missing_ok=True)
                        removed += 1
                except Exception:
                    continue
            if removed:
                logger.info("temp sweep: removed %d stale file(s)", removed)
        except Exception as e:  # pragma: no cover
            logger.warning("temp sweep failed: %s", e)
        await asyncio.sleep(interval)


@app.on_event("startup")
async def _startup():
    import asyncio
    logger.info("%s starting on %s:%s", settings.app_name, settings.host, settings.port)
    logger.info("Loaded %d tool(s): %s", len(tools), [t.metadata.id for t in tools])
    # Initialise auth + audit DBs (idempotent; applies pending migrations).
    # We do this even when auth is disabled so that turning auth on later
    # has the schema ready.
    try:
        from .core import auth_db, audit_db, audit_forward, auth_settings, roles
        auth_db.init()
        audit_db.init()
        # 工作紀錄 DB：建表，並把上次行程遺留的「進行中」標成已中斷 —— 那些執行
        # 緒已經不存在了，繼續顯示轉換中只會讓使用者等一個永遠不會完成的工作。
        from .core import concurrency_settings, db_health, job_store
        job_store.init()
        # 每分鐘記一筆資源使用率，管理頁才畫得出「何時是高峰」的歷史圖
        job_store.start_sampler()
        # 事件迴圈延遲監看 —— 「整站卡住但 CPU 看起來還有餘裕」只能靠這個量出來
        _loop_watchdog.start()
        # ---- 讓「轉檔進行中」不影響網頁操作（第二道措施）----
        # 背景作業跑在執行緒裡，而有些工具（jtdt-layout 的版面重組尤其明顯）做的
        # 是大量**純 Python** 的運算 —— 那種執行緒會長時間握著 GIL，同一個行程裡
        # 的 asyncio 事件迴圈就搶不到執行機會，網頁因此卡住。
        #
        # `setswitchinterval` 是 GIL 的強制切換間隔，預設 5 毫秒。調小到 1 毫秒
        # 讓事件迴圈的等待上限縮短為原本的五分之一。代價是切換次數變多（CPU 密集
        # 工作大約慢個百分之幾），但**網頁的回應性遠比轉檔快幾秒重要**。
        #
        # 這不是萬靈丹：真正的根治是把重運算搬到子行程（soffice 本來就是，且已
        # 降優先權 —— 見 office_convert._lower_priority）。兩者搭配才夠。
        import sys as _sys
        try:
            _sys.setswitchinterval(0.001)
            logger.info("GIL 切換間隔設為 1 ms（讓轉檔不影響網頁回應）")
        except (AttributeError, ValueError):
            pass
        # 資料庫完整性檢查。壞掉不丟例外（一個壞掉的稽核 DB 不該讓服務起不來），
        # 但要在記錄裡講清楚是哪個檔、影響什麼、怎麼還原 —— 原本的行為是噴一個
        # 看不懂的 sqlite 例外，管理員完全不知道發生什麼事。
        # 在背景跑：正式機的統編資料庫有 1.4 GB，冷快取下讀完要一分鐘 —— 同步
        # 檢查會讓每次 `jtdt update` 多出一分鐘連不上的空窗（部署到正式機才實測
        # 出來，啟動 7 秒變 61 秒）。
        def _on_db_checked(rows):
            for _r in rows:
                if _r["exists"] and not _r["ok"]:
                    try:
                        audit_db.log_event("db_corruption", username="system",
                                           target=_r["file"],
                                           details={"detail": _r["detail"],
                                                    "impact": _r["impact"]})
                    except Exception:  # noqa: BLE001
                        pass
        db_health.startup_check_async(_on_db_checked)
        # 依設定（並依實際記憶體夾住上限）套用併行度
        concurrency_settings.apply()
        # Force-create the session secret on first boot so admin enabling
        # auth later doesn't see a missing-secret race.
        auth_settings.get_session_secret()
        # Seed the 7 built-in roles (idempotent — won't overwrite admin's
        # customisations from earlier runs).
        roles.seed_builtin_roles()
        # Auto-create the built-in jtdt-auditor user if missing (v1.5.0+).
        # Skip on auth=off — we don't want to invent an account before
        # the customer has even set up authentication.
        try:
            if auth_settings.is_enabled():
                created = roles.seed_default_auditor_user()
                if created:
                    logger.info(
                        "Created built-in audit user 'jtdt-auditor' (no password "
                        "yet); admin must run `sudo jtdt reset-password "
                        "jtdt-auditor` before first use.")
        except Exception:
            logger.exception("seed_default_auditor_user failed")
        # Enforce separation of duties on every startup — auditor users
        # must only have the auditor role, no direct tool grants, and
        # totp_required=1. Catches DB drift introduced by older versions
        # or manual edits.
        try:
            if auth_settings.is_enabled():
                summary = roles.enforce_auditor_isolation()
                if summary["users_cleaned"]:
                    logger.warning(
                        "auditor isolation cleanup: %d user(s) had non-auditor "
                        "roles or direct tool grants — removed. Details in "
                        "audit log (event_type=auditor_isolation_cleanup).",
                        summary["users_cleaned"],
                    )
        except Exception:
            logger.exception("enforce_auditor_isolation failed")
        # Start the audit-forward background worker (no-op when no
        # destinations configured).
        audit_forward.start_worker()
        # Start retention sweeper (runs once now + every 6h).
        from .core import retention as _retention
        _retention.start_scheduler()
        # Start scheduled settings-export (checks hourly, default OFF).
        try:
            from .core import scheduled_export as _sched_export
            _sched_export.start_scheduler()
        except Exception:
            logger.exception("scheduled_export scheduler start failed")
        # Start vat-db schedule (weekly auto-update, default OFF).
        try:
            from .core import vat_db as _vatdb
            _vatdb.start_scheduler()
            # 既有站台升版 / 重新安裝 / 重啟後，若已有統編資料但 FTS 名稱搜尋
            # 索引尚未建（舊版資料庫），背景就地重建（不必重新下載）。非阻塞。
            _vatdb.maybe_build_fts_background()
        except Exception:
            logger.exception("vat_db scheduler start failed")
        # Start directory (AD/LDAP) sync — mirrors groups + caches member counts
        # so 群組管理 reads the local cache instead of one live LDAP query per
        # row. Runs shortly after boot + every N hours (default 6h; only active
        # on ldap/ad backend).
        try:
            from .core import directory_sync as _dirsync
            _dirsync.start_scheduler()
        except Exception:
            logger.exception("directory_sync scheduler start failed")
    except Exception as exc:
        logger.exception("auth/audit init failed: %s", exc)
    # Background sweeper for ephemeral uploads
    asyncio.create_task(_sweep_temp_files_loop())
    # Configure pytesseract — finds tesseract.exe in standard install paths
    # (Windows: C:\Program Files\Tesseract-OCR\) so OCR works even when
    # user hasn't added it to PATH (GitHub issue #4).
    try:
        from .core import sys_deps as _sd
        _sd.configure_pytesseract()
    except Exception:
        pass


def run():
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        # proxy_headers=False (uvicorn default is True): with it ON, uvicorn's
        # ProxyHeaders middleware REWRITES scope["client"] from X-Forwarded-For
        # whenever the TCP peer is in forwarded_allow_ips (default 127.0.0.1).
        # That would make request.client.host an XFF-derived (client-supplied)
        # value — silently breaking Reverse Proxy SSO's trust gate, which MUST
        # see the true TCP peer (the reverse proxy) to decide trust. We keep it
        # OFF and read X-Forwarded-Proto / -Host / -For as raw headers ourselves
        # where we deliberately want the client's view (is_https, audit IP).
        proxy_headers=False,
    )


if __name__ == "__main__":
    run()
