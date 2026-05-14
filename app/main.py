from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from .config import settings
from .core.asset_manager import asset_manager
from .core.job_manager import job_manager
from .logging_setup import get_logger, setup_logging
from .tool_registry import discover_tools, mount_tools

VERSION = "1.8.27"

setup_logging("DEBUG" if settings.debug else "INFO")
logger = get_logger(__name__)

app = FastAPI(title=settings.app_name, version=VERSION)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR.parent / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
    "office-to-pdf":      "convert convert-to-pdf office word excel powerpoint docx xlsx pptx odt ods odp 轉檔 轉成 文書 文件",
    "pdf-extract-images": "extract images pictures jpg png assets 擷取 提取 圖片 影像 抽圖",
    "pdf-to-image":       "convert image images png jpg jpeg raster rasterize export office word excel powerpoint docx xlsx pptx odt ods odp 文書轉圖片 轉圖 轉圖片 轉png 轉成圖片 影像 匯出圖片 Word 轉圖 Excel 轉圖 PPT 轉圖",
    "image-to-pdf":       "image images photos jpg jpeg png gif tiff webp heic combine merge convert scan a4 letter page size rotate reorder 圖片 照片 相片 掃描 轉 PDF 合併 排序 旋轉 頁面大小 A4",
    "pdf-editor":         "editor edit annotate annotation whiteout redact text textbox shape pencil draw highlight sticky note scribus 編輯 編輯器 標註 註記 塗黑 遮蓋 手繪 螢光筆 便箋 文字框 修圖",
    "pdf-extract-text":   "extract text content txt markdown md docx word odt reflow paragraph ocr llm 擷取文字 取出文字 轉文字 轉 word 轉 markdown 段落重排 LLM 重排",
    "pdf-ocr":            "ocr searchable scan image text layer invisible tesseract chi_tra chi_sim eng apple preview live text macos 文字層 補建 掃描 圖檔 變可選 可搜尋 透明文字層 蘋果 預覽程式 LiveText 圖轉文字",
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
    {"icon": "book", "name": "同義詞", "description": "PDF 標籤對應字典",
     "url": "/admin/synonyms",
     "keywords": "synonym synonyms alias dictionary label mapping 字典 同義 詞"},
    {"icon": "page", "name": "表單範本", "description": "已記住的表單版型",
     "url": "/admin/templates",
     "keywords": "template form layout 表單 範本 樣板"},
    {"icon": "gear", "name": "轉檔設定", "description": "LibreOffice / OxOffice 路徑與順序",
     "url": "/admin/conversion",
     "keywords": "conversion office libreoffice oxoffice path 轉檔 路徑"},
    {"icon": "gear", "name": "LLM 設定", "description": "10 個工具的 LLM AI 加值（附加功能，預設關閉）",
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
    {"icon": "eye", "name": "稽核記錄", "description": "登入 / 操作 / 設定變更追蹤",
     "url": "/admin/audit", "requires_auth": True,
     "keywords": "audit log activity history 稽核 記錄 記錄 操作 軌跡"},
    {"icon": "upload", "name": "上傳檔案記錄", "description": "誰上傳了什麼檔案到哪個工具",
     "url": "/admin/uploads", "requires_auth": True,
     "keywords": "uploads files history audit 上傳 檔案 記錄 歷史"},
    {"icon": "gear", "name": "記錄轉送", "description": "將稽核轉發到外部 syslog / CEF / GELF",
     "url": "/admin/log-forward", "requires_auth": True,
     "keywords": "syslog cef gelf forward log siem splunk graylog 轉發"},
    {"icon": "archive", "name": "檔案保留 / 清理", "description": "歷史檔案保留天數與自動清理",
     "url": "/admin/retention",
     "keywords": "retention cleanup history sweep gc gdpr 保留 清理 歷史"},
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


def _nav_tool_groups_visible(request=None):
    """Filter sidebar tool groups by the viewer's permissions.

    Auth OFF → everything. Auth ON → only tools the user is allowed to
    use (matches the backend gate so users don't see tiles that 403)."""
    from .core import auth_settings as _as, permissions as _perm
    if not _as.is_enabled():
        return _NAV_TOOL_GROUPS_ALL
    user = getattr(getattr(request, "state", None), "user", None) if request else None
    if not user:
        return []
    et = _perm.effective_tools(user.get("user_id", 0))
    if et == "ALL":
        return _NAV_TOOL_GROUPS_ALL
    out = []
    for g in _NAV_TOOL_GROUPS_ALL:
        kept = [t for t in g["tools"] if t["id"] in et]
        if kept:
            out.append({"title": g["title"], "tools": kept})
    return out


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

# Platform routes
from .web.router import build_router as _build_web_router  # noqa: E402

app.include_router(_build_web_router(templates, tools, settings.app_name, VERSION))

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

@app.exception_handler(_HTTPException2)
async def _friendly_http_exc(request: Request, exc: _HTTPException2):
    # 只對「瀏覽器導航」改成 HTML；JSON / XHR / API 維持原本行為
    accept = (request.headers.get("Accept") or "").lower()
    is_html_nav = ("text/html" in accept and not _looks_like_xhr(request))
    if not is_html_nav or exc.status_code not in (401, 403, 404):
        # default JSON behaviour
        return _JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    titles = {
        401: ("需要登入", "請先登入後再使用此頁。"),
        403: ("沒有存取權限", str(exc.detail or "您的角色沒有使用此頁的權限。如有需要請聯絡管理員。")),
        404: ("找不到頁面", "請確認網址是否正確，或回首頁重新導覽。"),
    }
    title, msg = titles.get(exc.status_code, ("錯誤", str(exc.detail or "")))
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

_PUBLIC_PREFIXES = ("/static/", "/login", "/logout", "/setup-admin",
                    "/healthz", "/favicon", "/api/", "/branding/",
                    "/2fa-verify")  # 2FA 驗證頁不需要 session（pending 階段）
_PUBLIC_EXACT = {"/login", "/logout", "/setup-admin", "/healthz", "/favicon.ico",
                 "/2fa-verify"}


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
    h.setdefault("Content-Security-Policy", (
        "default-src 'self'; "
        # inline 'unsafe-inline' needed for templates' <style> + event handlers;
        # jsDelivr allowed for Fabric.js (pdf-editor) only.
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
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
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
    )
    if is_https:
        h.setdefault("Strict-Transport-Security",
                     "max-age=15552000; includeSubDomains")
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
            and request.url.path.startswith("/tools/")):
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
async def _auth_gate(request: Request, call_next):
    from .core import auth_settings, sessions, permissions
    if not auth_settings.is_enabled():
        return await call_next(request)
    path = request.url.path
    if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)
    # Need a valid session
    token = request.cookies.get(sessions.COOKIE_NAME, "")
    user = sessions.lookup(token) if token else None
    if not user:
        if _looks_like_xhr(request):
            return _JSONResponse({"error": "unauthorized",
                                  "detail": "請先登入"}, status_code=401)
        # Preserve where the user was trying to go.
        next_q = "?next=" + _qstr(path + ("?" + request.url.query if request.url.query else ""))
        return RedirectResponse("/login" + next_q, status_code=302)
    # Stash user on request.state for downstream handlers (audit context, etc).
    request.state.user = user

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
            ip=request.client.host if request.client else "",
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
    path = request.url.path

    # Only guard explicit API surfaces; never block UI pages, static files,
    # or admin (admin is browser-based and has its own access control).
    is_api = path.startswith("/api/")
    if not is_api or not api_tokens.is_enforced():
        return await call_next(request)

    # Accept: Authorization: Bearer <token>  OR  ?token=<token>
    auth = request.headers.get("Authorization") or ""
    presented = None
    if auth.lower().startswith("bearer "):
        presented = auth.split(None, 1)[1].strip()
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
@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    return job.to_public()


@app.get("/api/vat-lookup/{vat}")
async def api_vat_lookup(vat: str):
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
async def api_convert_to_pdf(file: UploadFile = File(...)):
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
        office_convert.convert_to_pdf(src, out)
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
async def api_job_download(job_id: str):
    job = job_manager.get(job_id)
    if not job or not job.result_path or not job.result_path.exists():
        return JSONResponse({"error": "no result"}, status_code=404)
    return FileResponse(
        path=str(job.result_path),
        filename=job.result_filename or job.result_path.name,
        media_type="application/octet-stream",
    )


@app.post("/api/llm-review")
async def api_llm_review(
    file: UploadFile = File(...),
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
async def api_job_download_png(job_id: str):
    """Render the job's result PDF to PNG(s); zip when multi-page or batch.

    Single-PDF + single-page → PNG. Multi-page PDF → ZIP of PNGs (page_001.png,
    page_002.png …). If the result was already a ZIP of PDFs, all PDFs are
    extracted and rendered to PNGs in the same ZIP.
    """
    import io
    import shutil
    import tempfile
    import zipfile as _zip
    import fitz

    job = job_manager.get(job_id)
    if not job or not job.result_path or not job.result_path.exists():
        return JSONResponse({"error": "no result"}, status_code=404)
    src = job.result_path
    base_name = (job.result_filename or src.name)
    tmp = Path(tempfile.mkdtemp(prefix="job_png_"))

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

    pngs: list[tuple[str, bytes]] = []
    for stem, pdf in pdfs:
        with fitz.open(str(pdf)) as doc:
            for i in range(doc.page_count):
                pix = doc[i].get_pixmap(dpi=150, alpha=False)
                name = f"{stem}_p{i + 1:03d}.png" if doc.page_count > 1 or len(pdfs) > 1 else f"{stem}.png"
                pngs.append((name, pix.tobytes("png")))

    try:
        if len(pngs) == 1:
            name, data = pngs[0]
            out = tmp / name
            out.write_bytes(data)
            return FileResponse(
                path=str(out), filename=name,
                media_type="image/png",
                background=None,
            )
        zip_buf = io.BytesIO()
        with _zip.ZipFile(zip_buf, "w", _zip.ZIP_DEFLATED) as zf:
            for name, data in pngs:
                zf.writestr(name, data)
        zip_path = tmp / (Path(base_name).stem + ".png.zip")
        zip_path.write_bytes(zip_buf.getvalue())
        return FileResponse(
            path=str(zip_path), filename=zip_path.name,
            media_type="application/zip",
        )
    finally:
        # Clean tmp later — FileResponse needs the file alive while streamed.
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
        # Start vat-db schedule (weekly auto-update, default OFF).
        try:
            from .core import vat_db as _vatdb
            _vatdb.start_scheduler()
        except Exception:
            logger.exception("vat_db scheduler start failed")
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
    )


if __name__ == "__main__":
    run()
