"""全站設定匯出 / 匯入 — 以「類別」為單位，匯出與匯入都可逐項勾選。

每個設定類別（category）對應 data/ 內的一組檔案 / 目錄，或（RBAC）auth.sqlite
內的角色/權限資料。匯出時 admin 可勾選要包含哪些類別（預設全含，歷史記錄預設
不含）；匯入時先讀備份檔的 manifest 看裡面有哪些類別，再逐項勾選只還原哪些。

**RBAC 類別**（角色與權限）匯出：roles（含 is_default_for_new）、role_perms、
role_seed_snapshot、以及 OU 層級的權限規則（subject_type='ou'，key 是 DN 字串，
可跨機）。**不含**使用者帳號 / 密碼 hash、也不含綁 user/group id 的個別指派
（那些換機器 id 對不上，搬過去沒意義）。

**永遠不含**：temp/ jobs/ audit.sqlite、auth.sqlite 的 users/密碼、以及
`.session_secret`（那是 session 簽章金鑰，外流等於可偽造登入）。

**加新設定檔就要加分類** —— 漏加不會有任何錯誤訊息，只會在客戶搬機還原後才發現
設定不見了。`tools/check_settings_export_coverage.py` 會掃描程式碼中所有 data_dir
下的設定檔引用並比對本清單，發版前必跑。

匯入是「合併」非「整清」：覆寫前先把現有對應檔備份成 `<name>.bak.<ts>`。
匯出檔名：`jtdt-settings-YYYYMMDD-HHMMSS-vX.Y.Z.zip`
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Optional

from . import atomic_json, safe_paths
from ..config import settings

MANIFEST_NAME = "manifest.json"
RBAC_NAME = "rbac.json"
#: 帳號對照（v1.16.66）：備份裡的使用者 / 群組編號 → 帳號名稱與來源。不含密碼、兩步驟驗證、信箱。
IDENTITY_NAME = "identity.json"
#: zip 根目錄裡不是 `data/` 的那幾個（解壓時另外處理）
_ROOT_NAMES = (MANIFEST_NAME, RBAC_NAME, IDENTITY_NAME)


# ---- Category registry --------------------------------------------------
# kind:
#   "files" — items are single files relative to data_dir
#   "dirs"  — items are directories relative to data_dir (packed recursively)
#   "rbac"  — special: dumped from / merged into auth.sqlite (see _rbac_*)
# default: pre-checked in the export UI (history is opt-in).
CATEGORIES: list[dict] = [
    {"id": "auth", "label": "認證設定", "kind": "files",
     "items": ["auth_settings.json"],
     "desc": "認證後端 / LDAP / AD 連線與對應設定（SSO 另見「SSO 單一登入」）",
     "default": True},
    {"id": "sso", "label": "SSO 單一登入", "kind": "files",
     "items": ["sso_settings.json"], "rekey": "sso",
     "desc": "OIDC / SAML 設定（含用戶端密鑰、SP 私鑰 — 敏感）",
     "default": True, "sensitive": True},
    {"id": "jtlw", "label": "語音服務（JTLW）", "kind": "files",
     "items": ["jtlw_settings.json"], "rekey": "jtlw",
     "desc": "會議錄音送去轉逐字稿的送件位址與 API 金鑰（含金鑰 — 敏感）",
     "default": True, "sensitive": True},
    {"id": "notify", "label": "通知設定", "kind": "files",
     "items": ["notify_settings.json"], "rekey": "notify",
     "desc": "作業完成通知的管道憑證（SMTP 帳密、bot token、webhook URL — 敏感）",
     "default": True, "sensitive": True},
    {"id": "mcp", "label": "MCP（AI 連接）", "kind": "files",
     "items": ["mcp_settings.json"],
     "desc": "MCP 總開關、開放的工具、限流與傳輸票證設定（哪幾張 Token 允許 MCP 記在 API Token 裡）",
     "default": True},
    {"id": "notify_prefs", "label": "個人收訊偏好", "kind": "dirs",
     "items": ["notify_prefs"],
     "desc": "各使用者的通知開關、信箱、Telegram / LINE 收訊者",
     "default": True},
    {"id": "directory", "label": "目錄同步 / 過濾", "kind": "files",
     "items": ["directory_sync.json", "dir_filter.json"],
     "desc": "AD / LDAP 目錄同步排程與瀏覽過濾條件", "default": True},
    {"id": "log_forward", "label": "記錄轉送", "kind": "files",
     "items": ["log_forwarders.json"],
     "desc": "稽核記錄轉送目的地（syslog / CEF / GELF）", "default": True},
    {"id": "concurrency", "label": "併行度設定", "kind": "files",
     "items": ["concurrency.json", "job_priority.json", "upload_settings.json"],
     "desc": "同時可處理的工作數、Office 轉檔同時數、外部服務同時呼叫數、轉檔 CPU 上限、記憶體保留量、優先派送名單、全域上傳大小上限",
     "default": True},
    {"id": "retention", "label": "檔案保留 / 清理", "kind": "files",
     "items": ["retention.json"],
     "desc": "各類資料的保留天數與自動清理排程", "default": True},
    {"id": "scheduled_export", "label": "排程備份設定", "kind": "files",
     "items": ["scheduled_export.json"],
     "desc": "設定備份的排程與匯出目錄", "default": True},
    {"id": "rbac", "label": "角色與權限", "kind": "rbac", "items": [],
     "desc": "角色定義、工具權限、新使用者預設角色、OU 權限規則（不含使用者 / 密碼）",
     "default": True},
    {"id": "profile", "label": "公司資料", "kind": "files",
     "items": ["profile.json"], "desc": "office_profile 公司資料 profile", "default": True},
    {"id": "office_profile", "label": "公司資料（附件）", "kind": "dirs",
     "items": ["office_profile"], "desc": "profile 附帶檔案", "default": True},
    {"id": "synonyms", "label": "同義詞對照", "kind": "files",
     "items": ["label_synonyms.json"], "desc": "欄位標籤同義詞", "default": True},
    {"id": "translation_glossary", "label": "翻譯對照字典", "kind": "files",
     "items": ["translation_glossary.json"],
     "desc": "逐句翻譯 / 文件翻譯的專有名詞對照", "default": True},
    # 事務機掃描取件的管理員設定（v1.16.89）。事務機本身（`mfp.sqlite`）是個人資料，不在這裡（見豁免清單）。
    {"id": "mfp", "label": "事務機掃描取件", "kind": "files",
     "items": ["mfp_settings.json"],
     "desc": "是否允許使用者自行新增事務機", "default": True},
    # 只有「從哪裡下載」的設定；下載下來的範本與地址簿在 `official_doc/`，
    # 可以重新下載，不進備份（見 check_settings_export_coverage 的豁免）。
    # 用印申請：功能開關與每顆章的管制（v1.17.28）。章的 id 跟資產庫一起還原才對得上。
    {"id": "seal_request", "label": "用印申請", "kind": "files",
     "items": ["seal_request.json"],
     "desc": "功能開關、哪些印章僅限核准案件使用、可申請對象與保管人", "default": True},
    {"id": "official_doc_sources", "label": "公文撰擬資料來源", "kind": "files",
     "items": ["official_doc_sources.json"],
     "desc": "公文範本與機關地址簿的下載網址與自訂來源（下載下來的資料不備份，還原後再按一次下載）",
     "default": True},
    {"id": "form_templates", "label": "表單範本", "kind": "files",
     "items": ["form_templates.json"], "desc": "pdf-fill 表單範本", "default": True},
    {"id": "api_tokens", "label": "API Token", "kind": "files",
     "items": ["api_tokens.json"], "rekey": "api_tokens", "desc": "對外 API 存取權杖（敏感）",
     "default": True, "sensitive": True},
    {"id": "llm", "label": "LLM 設定", "kind": "files",
     "items": ["llm_settings.json"], "rekey": "llm",
     "desc": "LLM server / 模型 / 參數（含 API 金鑰，敏感）", "default": True, "sensitive": True},
    # 只有 embedding 的連線設定；知識庫本身（原檔、切段、向量）在 `knowledge/`，
    # 不進設定備份（見 tools/check_settings_export_coverage.py 的豁免理由）。
    {"id": "knowledge", "label": "公文知識庫 embedding 設定", "kind": "files",
     "items": ["knowledge_settings.json"], "rekey": "knowledge",
     "desc": "公文知識庫向量檢索用的嵌入服務位址、模型、前綴（含 API 金鑰，敏感）。"
             "公文知識庫的文件不在備份裡，搬機後請重新上傳",
     "default": True, "sensitive": True},
    {"id": "ocr", "label": "OCR 設定", "kind": "files",
     "items": ["ocr_settings.json", "ocr_remote.json"],
     "desc": "預設 OCR 引擎；遠端 GPU OCR 伺服器位址與存取權杖（敏感）",
     "default": True, "sensitive": True},
    {"id": "office_paths", "label": "Office 路徑", "kind": "files",
     "items": ["office_paths.json"], "desc": "soffice / OxOffice 執行檔路徑", "default": True},
    {"id": "fonts", "label": "自訂字型", "kind": "files_and_dirs",
     "items": ["font_settings.json"], "dirs": ["fonts"],
     "desc": "上傳的 TTF / OTF 字型 + 設定", "default": True},
    {"id": "assets", "label": "資產（印章 / 簽名 / Logo）", "kind": "dirs",
     "items": ["assets"], "desc": "印章 / 簽名 / logo 圖與 metadata", "default": True},
    {"id": "branding", "label": "品牌 Logo", "kind": "dirs",
     "items": ["branding"], "desc": "企業 logo", "default": True},
    {"id": "scan_prefs", "label": "掃描工具欄位偏好", "kind": "dirs",
     "items": ["transit_proof_settings", "einvoice_settings"],
     "desc": "乘車證明 / 電子發票的欄位顯示、排序、匯出標籤（各使用者一份）",
     "default": True},
    {"id": "scan_buffers", "label": "掃描暫存資料", "kind": "dirs",
     "items": ["transit_proof_buffer", "einvoice_buffer"],
     "desc": "使用者掃到一半、尚未匯出的乘車證明 / 發票資料（量大，搬機通常不需要）",
     "default": False},
    {"id": "submission_check", "label": "送件檢查（自家實體）", "kind": "dirs",
     "items": ["submission_check/self_entities"],
     "desc": "自家公司實體清單（個別 case 屬工作資料，不隨設定備份）",
     "default": True},
    {"id": "workspace", "label": "使用者工作區", "kind": "files_and_dirs",
     "items": ["workspace.json"], "dirs": ["workspace"],
     "desc": "使用者存放的檔案與 metadata（量大，搬機通常不需要）",
     "default": False},
    {"id": "history_fill", "label": "表單填寫歷史", "kind": "dirs",
     "items": ["fill_history"], "desc": "使用者填單歷史（量大，搬機通常不需要）",
     "default": False},
    {"id": "history_stamp", "label": "用印簽名歷史", "kind": "dirs",
     "items": ["stamp_history"], "desc": "用印歷史", "default": False},
    {"id": "history_watermark", "label": "浮水印歷史", "kind": "dirs",
     "items": ["watermark_history"], "desc": "浮水印歷史", "default": False},
]

_CAT_BY_ID = {c["id"]: c for c in CATEGORIES}


def _cat_files(cat: dict) -> list[str]:
    if cat["kind"] in ("files", "files_and_dirs"):
        return list(cat.get("items", []))
    return []


def _cat_dirs(cat: dict) -> list[str]:
    if cat["kind"] == "dirs":
        return list(cat.get("items", []))
    if cat["kind"] == "files_and_dirs":
        return list(cat.get("dirs", []))
    return []


# ---- SSO 祕密重新加密 ----------------------------------------------------
# sso_settings.json 內的 client secret / SP 私鑰是用**本機** data/.session_secret
# 當金鑰做 Fernet 加密的。直接把檔案複製到另一台機器，密文解不開 —— 設定看起來
# 都還在，SSO 卻會在還原後無聲失效。而 .session_secret 同時是 session 簽章金鑰，
# 放進備份檔等於把登入偽造能力一起送出去 → 只能「匯出時解密、匯入時用目標機器的
# 金鑰重新加密」。備份檔內因此是明文，與既有的 LDAP 服務帳號密碼 / API token
# 同級（該分類標記 sensitive）。
_SSO_PLAINTEXT_KEY = "_jtdt_secrets_plaintext"


def _kb_embed():
    """知識庫的 embedding 設定模組（`encrypt_secret` / `decrypt_secret` 在那裡）。"""
    from .kb import embed as _kbe
    return _kbe


def _rekey_specs() -> dict:
    """需要「匯出解密、匯入重新加密」的檔案 → (模組, 取得該檔祕密欄位的函式)。

    兩個檔案都用同一把 Fernet 金鑰（`data/.session_secret`），所以跨機都會遇到
    一樣的問題。共用同一套處理，新增這類檔案時只要在這裡加一列。
    """
    from . import notify_settings as _ns, sso_settings as _sso
    from . import jtlw_settings as _jl
    from . import llm_settings as _ll
    from . import api_tokens as _at
    return {
        # api_tokens.json：每一張 Token 的密文（data["tokens"][i]["token_enc"]，v1.16.78 起）；
        # 比對用的雜湊與金鑰無關，搬過去照樣能用。舊版備份裡是明文 `token` 欄位、沒有標記 ——
        # 原樣寫回，由 api_tokens 第一次讀到時轉成雜湊＋密文。
        "api_tokens.json": (_at, lambda d: [
            (t, "token_enc") for t in (d.get("tokens") or []) if isinstance(t, dict)]),
        # llm_settings.json：`api_key_enc` 在最上層（v1.16.17 起加密存放），
        # 「其他 LLM 伺服器」每一台各有一把（data["servers"][i]["api_key_enc"]）
        "llm_settings.json": (_ll, lambda d: [(d, "api_key_enc")] + [
            (srv, "api_key_enc") for srv in (d.get("servers") or [])
            if isinstance(srv, dict)]),
        # knowledge_settings.json：設定中的那一份與「使用中的索引」那一份各有一把
        "knowledge_settings.json": (_kb_embed(), lambda d: [
            (d.get(sec), "api_key_enc") for sec in ("embed", "active")
            if isinstance(d.get(sec), dict)]),
        # jtlw_settings.json：祕密就在最上層（`api_key_enc` / `webhook_secret_enc`）
        "jtlw_settings.json": (_jl, lambda d: [(d, f) for f in _jl._SECRET_FIELDS]),
        # sso_settings.json：祕密在 data["oidc"]["client_secret_enc"] 這種兩層結構
        "sso_settings.json": (_sso, lambda d: [
            (d.get(sec), fld) for sec, fld in _sso.SECRET_FIELDS
            if isinstance(d.get(sec), dict)]),
        # notify_settings.json：祕密在 data["channels"][管道][欄位]
        "notify_settings.json": (_ns, lambda d: [
            ((d.get("channels") or {}).get(ch), fld)
            for ch, fld in _ns.SECRET_FIELDS
            if isinstance((d.get("channels") or {}).get(ch), dict)]),
    }


def _rekey_export_blob(name: str) -> Optional[str]:
    """回傳「祕密已解密」的設定檔內容；檔案不存在回 None。"""
    p = settings.data_dir / name
    if not p.is_file():
        return None
    mod, locate = _rekey_specs()[name]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for holder, field in locate(data):
        if holder and holder.get(field):
            holder[field] = mod.decrypt_secret(holder[field])
    data[_SSO_PLAINTEXT_KEY] = True
    return json.dumps(data, ensure_ascii=False, indent=2)


def _rekey_after_import(target: Path) -> None:
    """把剛還原的設定檔內明文祕密改用**本機**金鑰加密。

    沒有標記的（v1.14.5 以前匯出的備份）代表裡面是**別台機器的密文**，本機解不開
    → 原樣留著，由管理員重新輸入祕密即可，不要試圖解密（會變成空字串，設定看似
    存在卻是壞的）。
    """
    spec = _rekey_specs().get(target.name)
    if not spec:
        return
    mod, locate = spec
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or not data.pop(_SSO_PLAINTEXT_KEY, False):
        return
    for holder, field in locate(data):
        if holder and holder.get(field):
            holder[field] = mod.encrypt_secret(holder[field])
    atomic_json.write_json(target, data, mode=0o600)


# 舊名保留，既有測試與呼叫端仍可用
def _sso_export_blob() -> Optional[str]:
    return _rekey_export_blob("sso_settings.json")


def _sso_rekey_after_import(target: Path) -> None:
    _rekey_after_import(target)


def _dir_stats(p: Path) -> tuple[int, int]:
    total = count = 0
    if p.is_dir():
        for f in p.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
                count += 1
    return count, total


# ---- 帳號對照：用使用者編號存的個人資料（v1.16.66）---------------------------
#
# 工作區、通知偏好、乘車證明的欄位設定與暫存、送件前檢核的「我方資料」、API Token 的擁有者
# 都是用**使用者編號**認人。編號是每一台自己給的（本機帳號建立的順序、AD 帳號第一次登入的順序），
# 原本匯入時原樣寫回 —— A 台 3 號的工作區到了 B 台就變成 B 台 3 號那個人的（issue #55 實測）。
#
# 現在備份附一份「編號 → 帳號名稱與來源」（`identity.json`），匯入時換成這一台同一個帳號的編號；
# 這一台沒有那個帳號就**不還原**並列出來（不可以交給剛好同編號的另一個人）。
# 舊版備份沒有這份對照：個人資料一律不還原（分不出是誰的）。認證關閉時的共用資料
# （`__single__` / `default` / `anonymous`）不屬於任何人，照常還原。

#: 匯入時「沒有還原」的原因（樣板＋參數：畫面 `tr(樣板)` 再填參數，英日介面翻得動；
#: 參數也逐一 `tr()`，「工作區」這類名稱有譯文、帳號名稱原樣）。
SKIP_MESSAGES = {
    "auth_locked": "認證設定：這台還沒有可以登入的本機管理員，套用的話沒有人登得進來，所以沒有套用。"
                   "請先在這台啟用認證、建立管理員，再匯入一次認證設定；整台搬家請改用複製整個資料夾的方式"
                   "（見 OPS.md「搬到另一台主機」）",
    "legacy": "{0}：舊版備份沒有帳號對照，分不出是誰的，沒有還原",
    "no_user": "{0}：這台沒有帳號「{1}」，沒有還原",
    "unknown_item": "{0}：認不得的項目 {1}，沒有還原",
    "no_owner": "{0}：備份裡找不到這份資料的主人，沒有還原",
    "token_owner": "API Token「{0}」：這台沒有擁有者「{1}」，已匯入但沒有權限，請在「API Token」頁重新指定擁有者",
    "rbac_no_identity": "{0}的角色指派：備份沒有帳號對照，沒有套用",
    "rbac_role_blocked": "{0}「{1}」的「{2}」角色不從備份匯入，請在這台自己指派",
    "rbac_missing": "{0}的角色指派：這台沒有 {1}，那幾筆沒有套用",
}
#: 參數裡會出現的名稱（語系檔要有譯文）
SKIP_LABELS = ("工作區", "通知偏好", "送件前檢核我方資料", "乘車證明欄位設定", "乘車證明暫存資料",
               "使用者", "群組")


def _note(code: str, *args) -> dict:
    tpl = SKIP_MESSAGES[code]
    text = tpl
    for i, a in enumerate(args):
        text = text.replace("{%d}" % i, str(a))
    return {"code": code, "template": tpl, "args": [str(a) for a in args], "text": text}


def _identity_dump() -> dict:
    try:
        from . import auth_db
        conn = auth_db.conn()
        users = {str(r["id"]): {"username": r["username"], "source": r["source"]}
                 for r in conn.execute("SELECT id, username, source FROM users").fetchall()}
        groups = {str(r["id"]): {"name": r["name"], "source": r["source"]}
                  for r in conn.execute("SELECT id, name, source FROM groups").fetchall()}
    except Exception:  # noqa: BLE001 —— 沒有帳號資料庫（從沒開過認證）＝沒有人
        users, groups = {}, {}
    return {"version": 1, "users": users, "groups": groups}


class _IdentityMap:
    """備份裡的編號 → 這一台的編號（用帳號名稱＋來源對；群組用名稱＋來源）。"""

    def __init__(self, blob: Optional[dict]):
        self.present = isinstance(blob, dict)
        self.users: dict[str, int] = {}
        self.groups: dict[str, int] = {}
        self.user_label: dict[str, str] = {}
        self.group_label: dict[str, str] = {}
        if not self.present:
            return
        try:
            from . import auth_db
            conn = auth_db.conn()
            here_u = {(r["username"], r["source"]): int(r["id"]) for r in
                      conn.execute("SELECT id, username, source FROM users").fetchall()}
            here_g = {(r["name"], r["source"]): int(r["id"]) for r in
                      conn.execute("SELECT id, name, source FROM groups").fetchall()}
        except Exception:  # noqa: BLE001 —— 這台沒有帳號資料庫（從沒開過認證）＝沒有人
            here_u, here_g = {}, {}
        for old, ident in (blob.get("users") or {}).items():
            if not isinstance(ident, dict):
                continue
            name, src = str(ident.get("username") or ""), str(ident.get("source") or "")
            self.user_label[str(old)] = f"{name}@{src}" if src else name
            if (name, src) in here_u:
                self.users[str(old)] = here_u[(name, src)]
        for old, ident in (blob.get("groups") or {}).items():
            if not isinstance(ident, dict):
                continue
            name, src = str(ident.get("name") or ""), str(ident.get("source") or "")
            self.group_label[str(old)] = f"{name}@{src}" if src else name
            if (name, src) in here_g:
                self.groups[str(old)] = here_g[(name, src)]
        # 乘車證明用 blake2b(str(使用者編號)) 當檔名
        self.user_hash = {_uid_hash(old): old for old in self.user_label}

    def label(self, old: str) -> str:
        return self.user_label.get(str(old)) or f"#{old}"


def _uid_hash(uid) -> str:
    """跟 `transit_proof` 的 `_user_key()` 同一個算法（檔名用，不是密碼學用途）。"""
    return hashlib.blake2b(str(uid).encode("utf-8"), digest_size=16).hexdigest()


#: 認證關閉時共用的那一份（不屬於任何人，照常還原）
_SHARED_KEYS = {"__single__", "default", "anonymous"}
_U_KEY = re.compile(r"^u(\d+)$")
_DIGITS = re.compile(r"^\d+$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _remap_entry(name: str, idm: _IdentityMap) -> tuple[Optional[str], Optional[dict]]:
    """一個 `data/...` 項目要寫到哪裡：回 (新的路徑, None) 或 (None, 沒有還原的原因)。
    不是個人資料的原樣回。"""
    parts = name.split("/")
    if len(parts) < 3:
        return name, None
    top = parts[1]

    def _owner(old: str, what: str):
        if not idm.present:
            return None, _note("legacy", what)
        new = idm.users.get(old)
        if new is None:
            return None, _note("no_user", what, idm.label(old))
        return new, None

    if top in ("workspace", "notify_prefs") and len(parts) >= 3:
        key = parts[2][:-5] if (top == "notify_prefs" and parts[2].endswith(".json")) else parts[2]
        if key in _SHARED_KEYS or (top == "workspace" and len(parts) == 3):
            return name, None
        m = _U_KEY.match(key)
        if not m:
            return None, _note("unknown_item", "工作區" if top == "workspace" else "通知偏好", parts[2])
        new, why = _owner(m.group(1), "工作區" if top == "workspace" else "通知偏好")
        if new is None:
            return None, why
        parts[2] = f"u{new}" + (".json" if top == "notify_prefs" else "")
        return "/".join(parts), None
    if top == "submission_check" and len(parts) >= 4 and parts[2] == "self_entities":
        stem = parts[3][:-5] if parts[3].endswith(".json") else parts[3]
        if stem in _SHARED_KEYS:
            return name, None
        if not _DIGITS.match(stem):
            return None, _note("unknown_item", "送件前檢核我方資料", parts[3])
        new, why = _owner(stem, "送件前檢核我方資料")
        if new is None:
            return None, why
        parts[3] = f"{new}.json"
        return "/".join(parts), None
    if top in ("transit_proof_settings", "transit_proof_buffer"):
        stem = parts[2].split(".", 1)[0]
        rest = parts[2][len(stem):]
        what = "乘車證明欄位設定" if top == "transit_proof_settings" else "乘車證明暫存資料"
        if stem in _SHARED_KEYS:
            return name, None
        if not _HEX32.match(stem):
            return None, _note("unknown_item", what, parts[2])
        if not idm.present:
            return None, _note("legacy", what)
        old = idm.user_hash.get(stem)
        if old is None:
            return None, _note("no_owner", what)
        new, why = _owner(old, what)
        if new is None:
            return None, why
        parts[2] = _uid_hash(new) + rest
        return "/".join(parts), None
    return name, None


def _remap_token_owners(path: Path, idm: _IdentityMap) -> list[dict]:
    """API Token 的擁有者換成這一台的編號；這台沒有那個帳號的，擁有者清空
    （認證開啟時＝沒有權限，要管理員重新指定）—— 不可以留著原本的編號
    （會變成這台同編號那個人的權限）。回講給管理員看的幾行。"""
    notes: list[dict] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return notes
    # 舊版備份裡的 Token 是明文欄位：匯入當下就換成雜湊＋密文，不要等服務下一次讀到
    from . import api_tokens as _at
    changed = _at._convert(data) if isinstance(data, dict) else False
    for t in data.get("tokens") or []:
        if not isinstance(t, dict) or t.get("owner_user_id") is None:
            continue
        old = str(t["owner_user_id"])
        new = idm.users.get(old) if idm.present else None
        if new is None:
            t["owner_user_id"] = None
            who = idm.label(old) if idm.present else f"#{old}"
            notes.append(_note("token_owner", t.get("label") or "?", who))
            changed = True
        elif new != t["owner_user_id"]:
            t["owner_user_id"] = new
            changed = True
    if changed:
        atomic_json.write_json(path, data, mode=0o600)
    return notes


def _has_local_admin() -> bool:
    """這一台有沒有可以登入的**本機**管理員 —— 任何認證方式下都能用「本機」登入，
    是救援管道。"""
    try:
        from . import auth_db, permissions
        rows = auth_db.conn().execute(
            "SELECT id FROM users WHERE source='local' AND enabled=1").fetchall()
        return any(permissions.effective_tools(int(r["id"])) == "ALL" for r in rows)
    except Exception:  # noqa: BLE001
        return False


# ---- RBAC dump / merge (auth.sqlite) ------------------------------------

def _rbac_dump() -> dict:
    """Serialise portable RBAC config from auth.sqlite. No users/passwords."""
    from . import auth_db
    conn = auth_db.conn()
    roles = [dict(r) for r in conn.execute(
        "SELECT id, display_name, description, is_builtin, is_protected, "
        "is_default_for_new FROM roles").fetchall()]
    role_perms = [[r["role_id"], r["tool_id"]] for r in conn.execute(
        "SELECT role_id, tool_id FROM role_perms").fetchall()]
    snapshot = [[r["role_id"], r["tool_id"]] for r in conn.execute(
        "SELECT role_id, tool_id FROM role_seed_snapshot").fetchall()]
    # OU-level assignments only (subject_key is a DN string → portable).
    ou_roles = [[r["subject_key"], r["role_id"]] for r in conn.execute(
        "SELECT subject_key, role_id FROM subject_roles WHERE subject_type='ou'"
    ).fetchall()]
    ou_perms = [[r["subject_key"], r["tool_id"]] for r in conn.execute(
        "SELECT subject_key, tool_id FROM subject_perms WHERE subject_type='ou'"
    ).fetchall()]
    # 個人與群組的指派（v1.16.66）：鍵是**這一台的編號**，匯入時靠 `identity.json`
    # 換成那一台同一個帳號 / 群組的編號（見 `_rbac_merge`）
    def _pairs(table: str, kind: str, col: str) -> list:
        return [[r["subject_key"], r[col]] for r in conn.execute(
            f"SELECT subject_key, {col} FROM {table} WHERE subject_type=?", (kind,)).fetchall()]
    return {"roles": roles, "role_perms": role_perms,
            "role_seed_snapshot": snapshot,
            "ou_subject_roles": ou_roles, "ou_subject_perms": ou_perms,
            "user_subject_roles": _pairs("subject_roles", "user", "role_id"),
            "group_subject_roles": _pairs("subject_roles", "group", "role_id"),
            "user_subject_perms": _pairs("subject_perms", "user", "tool_id"),
            "group_subject_perms": _pairs("subject_perms", "group", "tool_id")}


def _rbac_summary() -> dict:
    try:
        d = _rbac_dump()
        return {"roles": len(d["roles"]), "role_perms": len(d["role_perms"]),
                "ou_rules": len(d["ou_subject_roles"]) + len(d["ou_subject_perms"])}
    except Exception:
        return {"roles": 0, "role_perms": 0, "ou_rules": 0}


def _rbac_merge(data: dict, idm: Optional["_IdentityMap"] = None) -> dict:
    """Merge an RBAC dump into auth.sqlite. Upserts role definitions + perms +
    snapshot + OU rules. Custom roles are created; built-in role metadata is
    updated but is_builtin/is_protected are preserved from the imported flag.
    Enforces the single is_default_for_new invariant. Returns a small summary.
    """
    from . import auth_db, db
    # Roles that a crafted import backup must NEVER be able to hand out — a
    # confused-deputy admin importing an attacker's "backup" could otherwise
    # escalate: make `admin` the new-user default, or grant admin to a whole
    # OU. Mirror roles._INELIGIBLE_DEFAULT_ROLES.
    _INELIGIBLE_ROLES = {"admin", "auditor"}
    conn = auth_db.conn()
    roles = data.get("roles") or []
    role_perms = data.get("role_perms") or []
    snapshot = data.get("role_seed_snapshot") or []
    ou_roles = data.get("ou_subject_roles") or []
    ou_perms = data.get("ou_subject_perms") or []
    notes: list[dict] = []
    # 個人與群組的指派：換成這一台的編號；這台沒有那個帳號 / 群組就不套用（講出來）。
    # **管理員與稽核員不從備份給**（同 OU 那條的理由：匯入一份別人做的「備份」不可以讓誰變成管理員）。
    subj_roles: list[tuple[str, str, str]] = []
    subj_perms: list[tuple[str, str, str]] = []
    for kind, rkey, pkey in (("user", "user_subject_roles", "user_subject_perms"),
                             ("group", "group_subject_roles", "group_subject_perms")):
        rows_r, rows_p = data.get(rkey) or [], data.get(pkey) or []
        if not (rows_r or rows_p):
            continue
        what = "使用者" if kind == "user" else "群組"
        if idm is None or not idm.present:
            notes.append(_note("rbac_no_identity", what))
            continue
        table = idm.users if kind == "user" else idm.groups
        labels = idm.user_label if kind == "user" else idm.group_label
        missing: set[str] = set()
        for old, rid in rows_r:
            new = table.get(str(old))
            if new is None:
                missing.add(labels.get(str(old)) or f"#{old}")
            elif rid in _INELIGIBLE_ROLES:
                notes.append(_note("rbac_role_blocked", what, labels.get(str(old)) or old, rid))
            else:
                subj_roles.append((kind, str(new), rid))
        for old, tool in rows_p:
            new = table.get(str(old))
            if new is None:
                missing.add(labels.get(str(old)) or f"#{old}")
            else:
                subj_perms.append((kind, str(new), tool))
        if missing:
            notes.append(_note("rbac_missing", what, "、".join(sorted(missing))))
    now = time.time()
    imported_default = None
    with db.tx(conn):
        for r in roles:
            rid = r.get("id")
            if not rid:
                continue
            exists = conn.execute("SELECT 1 FROM roles WHERE id=?", (rid,)).fetchone()
            if exists:
                conn.execute(
                    "UPDATE roles SET display_name=?, description=? WHERE id=?",
                    (r.get("display_name") or rid, r.get("description") or "", rid))
            else:
                # An imported (new) role is always a CUSTOM role: force
                # is_builtin=0 / is_protected=0 so a backup can't plant an
                # undeletable "protected/builtin" fake role.
                conn.execute(
                    "INSERT INTO roles(id, display_name, description, is_builtin, "
                    "is_protected, is_default_for_new, created_at) "
                    "VALUES (?,?,?,0,0,0,?)",
                    (rid, r.get("display_name") or rid, r.get("description") or "", now))
            # Never let an import set admin/auditor as the new-user default
            # (that would bypass roles.set_default_role_id's guard and make
            # every JIT-provisioned user an admin).
            if r.get("is_default_for_new") and rid not in _INELIGIBLE_ROLES:
                imported_default = rid
        # Replace role_perms for the imported roles only.
        imported_role_ids = {r.get("id") for r in roles if r.get("id")}
        for rid in imported_role_ids:
            conn.execute("DELETE FROM role_perms WHERE role_id=?", (rid,))
            conn.execute("DELETE FROM role_seed_snapshot WHERE role_id=?", (rid,))
        for rid, tool in role_perms:
            if rid in imported_role_ids:
                conn.execute("INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
                             "VALUES (?,?)", (rid, tool))
        for rid, tool in snapshot:
            if rid in imported_role_ids:
                conn.execute("INSERT OR IGNORE INTO role_seed_snapshot(role_id, "
                             "tool_id) VALUES (?,?)", (rid, tool))
        for key, rid in ou_roles:
            # Block importing an OU→admin/auditor grant (e.g. granting admin to
            # the domain root DN = escalate the whole directory).
            if rid in _INELIGIBLE_ROLES:
                continue
            conn.execute("INSERT OR IGNORE INTO subject_roles(subject_type, "
                         "subject_key, role_id) VALUES ('ou', ?, ?)", (key, rid))
        for key, tool in ou_perms:
            conn.execute("INSERT OR IGNORE INTO subject_perms(subject_type, "
                         "subject_key, tool_id) VALUES ('ou', ?, ?)", (key, tool))
        for kind, key, rid in subj_roles:
            if conn.execute("SELECT 1 FROM roles WHERE id=?", (rid,)).fetchone():
                conn.execute("INSERT OR IGNORE INTO subject_roles(subject_type, "
                             "subject_key, role_id) VALUES (?, ?, ?)", (kind, key, rid))
        for kind, key, tool in subj_perms:
            conn.execute("INSERT OR IGNORE INTO subject_perms(subject_type, "
                         "subject_key, tool_id) VALUES (?, ?, ?)", (kind, key, tool))
        if imported_default:
            conn.execute("UPDATE roles SET is_default_for_new=0 "
                         "WHERE is_default_for_new=1")
            conn.execute("UPDATE roles SET is_default_for_new=1 WHERE id=?",
                         (imported_default,))
    try:
        from . import permissions as _perm
        _perm.invalidate_cache()
    except Exception:
        pass
    return {"roles": len(roles), "role_perms": len(role_perms),
            "ou_rules": len(ou_roles) + len(ou_perms),
            "subject_rules": len(subj_roles) + len(subj_perms), "notes": notes}


# ---- public API ---------------------------------------------------------

def list_categories() -> list[dict]:
    """For the export UI: every category with presence + size, so admin can
    tick which to include (default-checked flag included)."""
    out = []
    for c in CATEGORIES:
        present = False
        size = 0
        count = 0
        if c["kind"] == "rbac":
            s = _rbac_summary()
            present = s["roles"] > 0
            count = s["roles"]
            detail = f"{s['roles']} 個角色 / {s['role_perms']} 項權限 / {s['ou_rules']} 條 OU 規則"
        else:
            for fn in _cat_files(c):
                p = settings.data_dir / fn
                if p.is_file():
                    present = True
                    size += p.stat().st_size
                    count += 1
            for dn in _cat_dirs(c):
                p = settings.data_dir / dn
                if p.is_dir():
                    fc, fs = _dir_stats(p)
                    if fc:
                        present = True
                    size += fs
                    count += fc
            detail = f"{count} 個檔案・{size/1024:.1f} KB" if present else "（無）"
        # `detail` 是**組好的中文字串**，沒辦法當翻譯的鍵（數字每次都不一樣）。
        # 另外附上組成的零件，樣板才有辦法用 `tr()` 各自翻再拼起來。
        parts = ({"kind": "rbac", "roles": s["roles"], "perms": s["role_perms"],
                  "ou": s["ou_rules"]} if c["kind"] == "rbac"
                 else {"kind": "files" if present else "none",
                       "count": count, "kb": round(size / 1024, 1)})
        out.append({
            "detail_parts": parts,
            "id": c["id"], "label": c["label"], "desc": c["desc"],
            "kind": c["kind"], "default": c.get("default", True),
            "sensitive": c.get("sensitive", False),
            "present": present, "count": count, "size": size, "detail": detail,
        })
    return out


def export_to_zip(out_path: Path, selected_ids: Optional[list[str]] = None,
                  app_version: str = "") -> dict:
    """Pack the SELECTED categories into out_path. If selected_ids is None,
    include every default-on category (back-compat convenience)."""
    if selected_ids is None:
        selected_ids = [c["id"] for c in CATEGORIES if c.get("default", True)]
    selected = [c for c in CATEGORIES if c["id"] in set(selected_ids)]
    entries_by_cat: dict[str, list[str]] = {}
    files_added = 0
    total_bytes = 0
    # out_path 來自 admin 設定的匯出目錄。目錄本身經 safe_output_dir 驗證（絕對路徑、
    # 非系統目錄），檔名則是本程式產生的固定樣板（jtdt-settings-<時間>-v<版本>.zip），
    # 不含使用者輸入。
    out_dir = safe_paths.safe_output_dir(Path(out_path).parent)
    out_path = out_dir / safe_paths.sanitize_filename(Path(out_path).name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for c in selected:
            names: list[str] = []
            if c["kind"] == "rbac":
                blob = json.dumps(_rbac_dump(), ensure_ascii=False, indent=2)
                zf.writestr(RBAC_NAME, blob)
                names.append(RBAC_NAME)
                files_added += 1
                total_bytes += len(blob.encode("utf-8"))
            else:
                for fn in _cat_files(c):
                    p = settings.data_dir / fn
                    if not p.is_file():
                        continue
                    arc = f"data/{fn}"
                    if c.get("rekey"):
                        # 密文換明文後才寫進備份（見 _rekey_export_blob）
                        blob = _rekey_export_blob(fn)
                        if blob is None:
                            continue
                        zf.writestr(arc, blob)
                        total_bytes += len(blob.encode("utf-8"))
                    else:
                        zf.write(p, arcname=arc)
                        total_bytes += p.stat().st_size
                    names.append(arc)
                    files_added += 1
                for dn in _cat_dirs(c):
                    p = settings.data_dir / dn
                    if not p.is_dir():
                        continue
                    for f in p.rglob("*"):
                        if not f.is_file():
                            continue
                        rel = f.relative_to(settings.data_dir)
                        arc = f"data/{rel.as_posix()}"
                        zf.write(f, arcname=arc)
                        names.append(arc)
                        files_added += 1
                        total_bytes += f.stat().st_size
            if names:
                entries_by_cat[c["id"]] = names
        # 帳號對照：匯入時把用使用者編號存的個人資料換成那一台的編號（見 `_remap_entry`）
        zf.writestr(IDENTITY_NAME, json.dumps(_identity_dump(), ensure_ascii=False, indent=2))
        manifest = {
            "kind": "jtdt-settings-export",
            "schema_version": 2,
            "app_version": app_version or "unknown",
            "exported_at": time.time(),
            "exported_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "file_count": files_added,
            "total_bytes": total_bytes,
            "categories": [
                {"id": c["id"], "label": c["label"]}
                for c in selected if c["id"] in entries_by_cat
            ],
            "entries_by_category": entries_by_cat,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
    return {"out_path": str(out_path), "file_count": files_added,
            "total_bytes": total_bytes, "manifest": manifest}


def read_manifest(zip_path: Path) -> dict:
    """Read + validate a backup zip's manifest so the import UI can show which
    categories are inside. Raises ValueError if not a valid jtdt export."""
    if not zip_path.exists():
        raise FileNotFoundError(f"zip not found: {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if MANIFEST_NAME not in names:
            raise ValueError("missing manifest — not a jtdt settings export?")
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        if manifest.get("kind") != "jtdt-settings-export":
            raise ValueError(f"unknown export kind: {manifest.get('kind')!r}")
    # Present categories (schema v2). Fall back for v1 (no per-category map).
    cats = manifest.get("categories")
    if not cats:
        cats = [{"id": "legacy", "label": "（舊版備份，整包匯入）"}]
    # decorate with label from registry if available
    for c in cats:
        reg = _CAT_BY_ID.get(c["id"])
        if reg:
            c["desc"] = reg["desc"]
    manifest["categories"] = cats
    return manifest


def import_from_zip(zip_path: Path, selected_ids: Optional[list[str]] = None,
                    app_version: str = "") -> dict:
    """Restore only the SELECTED categories from a backup zip. Backs up any
    existing target file/dir to `<name>.bak.<ts>` first. selected_ids=None
    restores everything in the zip (back-compat)."""
    if not zip_path.exists():
        raise FileNotFoundError(f"zip not found: {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if MANIFEST_NAME not in names:
            raise ValueError("missing manifest — not a jtdt settings export?")
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        if manifest.get("kind") != "jtdt-settings-export":
            raise ValueError(f"unknown export kind: {manifest.get('kind')!r}")
        # zip-slip defence + decompression-bomb cap: only manifest, rbac.json,
        # and data/ entries allowed; reject archives that expand beyond sane
        # limits (a small crafted zip can inflate to GBs → OOM/disk fill).
        _MAX_TOTAL = 2 * 1024 * 1024 * 1024   # 2 GiB total uncompressed
        _MAX_FILE = 512 * 1024 * 1024         # 512 MiB per member
        total_uncompressed = 0
        for info in zf.infolist():
            name = info.filename
            if name in _ROOT_NAMES:
                continue
            if not name.startswith("data/") or ".." in Path(name).parts:
                raise ValueError(f"unsafe path in zip: {name!r}")
            if info.file_size > _MAX_FILE:
                raise ValueError("備份檔內有過大的檔案（疑似解壓縮炸彈），已中止")
            total_uncompressed += info.file_size
            if total_uncompressed > _MAX_TOTAL:
                raise ValueError("備份檔解壓後過大（疑似解壓縮炸彈），已中止")

        entries_by_cat = manifest.get("entries_by_category") or {}
        is_legacy = not entries_by_cat
        if selected_ids is None:
            # **`rbac.json` 不可以混進來**。它在 zip 的根目錄（不是 `data/`
            # 底下），而下面解壓時每個項目都會做 `relative_to("data")` ——
            # 混進來就是 `ValueError`，於是「全部還原」這條路**一定失敗**
            # （v1.14.31 對抗式驗證：自己匯出的檔自己匯不回去）。
            # 另外兩條分支都正確排除了它，只有這條漏掉；RBAC 由 `do_rbac`
            # 那段單獨處理。
            wanted_entries = set(n for n in names if n not in _ROOT_NAMES)
            do_rbac = RBAC_NAME in names
        elif is_legacy:
            # v1 backup has no category map — restore all its data/ entries.
            wanted_entries = set(n for n in names if n.startswith("data/"))
            do_rbac = RBAC_NAME in names and "rbac" in selected_ids
        else:
            wanted_entries = set()
            for cid in selected_ids:
                for n in (entries_by_cat.get(cid) or []):
                    if n != RBAC_NAME:
                        wanted_entries.add(n)
            do_rbac = ("rbac" in selected_ids) and (RBAC_NAME in names)

        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_paths: list[str] = []
        imported_files = 0
        restored_cats: list[str] = []
        # 沒有還原的項目與原因（畫面上逐條列出；`SKIP_MESSAGES` 的樣板＋參數）
        skipped: list[dict] = []
        identity = None
        if IDENTITY_NAME in names:
            try:
                identity = json.loads(zf.read(IDENTITY_NAME).decode("utf-8"))
            except ValueError:
                identity = None
        idm = _IdentityMap(identity)

        # **不可以把這台鎖在門外**（issue #55 實測）：匯入的認證設定會開啟認證，這台卻沒有
        # 可以登入的本機管理員（全新安裝、帳號不在設定備份裡）→ 網頁上沒有人登得進來。
        # 這一項不套用，講出原因；這台原本的認證設定不動。
        auth_entry = "data/auth_settings.json"
        if auth_entry in wanted_entries:
            try:
                new_backend = json.loads(zf.read(auth_entry).decode("utf-8")).get("backend")
            except ValueError:
                new_backend = None    # 壞掉的交給下面的 JSON 檢查擋
            if new_backend and new_backend != "off" and not _has_local_admin():
                wanted_entries.discard(auth_entry)
                skipped.append(_note("auth_locked"))

        # 用使用者編號存的個人資料：換成這一台同一個帳號的編號，對不上的不還原
        remapped: dict[str, str] = {}
        per_reason: dict[str, dict] = {}
        for n in sorted(wanted_entries):
            new_name, why = _remap_entry(n, idm)
            if new_name is None:
                k = why["text"]
                if k in per_reason:
                    per_reason[k]["count"] += 1
                else:
                    per_reason[k] = dict(why, count=1)
            else:
                remapped[n] = new_name
        skipped.extend(per_reason.values())
        wanted_entries = set(remapped)

        # Back up + extract file/dir entries.
        # Back up each distinct top-level target once.
        backed_up: set[str] = set()
        for name in sorted(wanted_entries):
            rel = Path(remapped.get(name, name)).relative_to("data")
            target = settings.data_dir / rel
            top = rel.parts[0] if rel.parts else ""
            if top and top not in backed_up:
                backed_up.add(top)
                src_top = settings.data_dir / top
                if src_top.exists():
                    bak = settings.data_dir / f"{top}.bak.{ts}"
                    if src_top.is_dir():
                        shutil.copytree(src_top, bak, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src_top, bak)
                    backup_paths.append(str(bak))
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf2, zf2.open(name) as src, \
                    open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            # **`.json` 要先確認解析得過再收下**。原本是原樣寫入，於是一份
            # 不完整的備份可以把 `auth_settings.json` 換成壞掉的內容 ——
            # 而壞掉的認證設定曾經等於「全站認證關閉」（v1.14.31 對抗式驗證）。
            # 讀取端現在會 fail-secure，但讓壞資料落地本身就不該發生：
            # 寫壞了就還原剛剛備份的那一份，並讓整批匯入失敗。
            if target.suffix.lower() == ".json":
                try:
                    json.loads(target.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    target.unlink(missing_ok=True)
                    raise ValueError(
                        f"備份檔內的 {name} 不是合法的 JSON，已中止匯入：{exc}")
            if Path(name).name in _rekey_specs():
                _rekey_after_import(target)
            if name == "data/api_tokens.json":
                skipped.extend(_remap_token_owners(target, idm))
            imported_files += 1

        rbac_result = None
        if do_rbac:
            with zipfile.ZipFile(zip_path, "r") as zf2:
                blob = zf2.read(RBAC_NAME).decode("utf-8")
            rbac_result = _rbac_merge(json.loads(blob), idm)
            skipped.extend(rbac_result.pop("notes", []))
            restored_cats.append("rbac")

    # Which categories were actually restored (for the response).
    if not is_legacy:
        for cid in (selected_ids if selected_ids is not None
                    else list(entries_by_cat.keys())):
            # 至少寫回一個檔案才算還原（認證設定被擋下、個人資料全部對不上時不算）
            if cid != "rbac" and any(n in wanted_entries for n in (entries_by_cat.get(cid) or [])):
                restored_cats.append(cid)

    return {"imported_files": imported_files, "manifest": manifest,
            "backup_paths": backup_paths, "restored_categories": restored_cats,
            "rbac": rbac_result, "skipped": skipped,
            "imported_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S")}
