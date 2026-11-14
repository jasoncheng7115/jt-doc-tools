"""JSON-backed store for LLM 校驗附加功能 settings.

This is the **only** place core code should touch when checking whether the
LLM add-on is enabled. Everything else (admin pages, review loop) goes
through ``llm_settings.make_client()`` so a disabled / missing LLM never
breaks core flow.

Design notes:
- Singleton at module level (matches synonym_manager / asset_manager pattern)
- ``DEFAULT_SETTINGS["enabled"] = False`` — explicit opt-in
- New defaults auto-merge into existing files on read (no manual migration)
"""
from __future__ import annotations

import copy
import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from . import atomic_json, secret_box
from .url_safety import validate_llm_base_url
from ..config import settings

logger = logging.getLogger("app.llm.settings")

#: 管理頁 / API 看到的金鑰欄位：已設定就是這個字串，**不回傳金鑰本身**。
#: 存檔時原樣送回＝「不要動」（同語音服務設定的做法）。
SECRET_KEPT = "__KEPT__"


def encrypt_secret(plaintext: str) -> str:
    """設定匯入 / 匯出換金鑰時用（`settings_export._rekey_specs`）。"""
    return secret_box.encrypt(plaintext)


def decrypt_secret(ciphertext: str) -> str:
    return secret_box.decrypt(ciphertext, label="llm_settings.api_key_enc")


# ---------------------------------------------------------------- 其他 LLM 伺服器
#: 伺服器編號：12 個十六進位字元。頁面上新增的那一列由前端產生（這樣還沒存檔的伺服器
#: 也能馬上出現在各工具的下拉裡），格式不對就由這裡另外產生一個。
_SERVER_ID_RE = re.compile(r"[0-9a-f]{12}")
MAX_SERVERS = 20
SERVER_TIMEOUT_RANGE = (10, 1800)
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


class LLMServerUnavailable(RuntimeError):
    """工具指定的「另一台 LLM 伺服器」不存在或設定有誤。

    **絕不退回全站伺服器、也不改用別台**：管理員把某支工具指到另一台，多半是因為
    資料只能送去那一台（例如公文只能在機關自己的伺服器上跑）。指定的那台出問題時
    偷偷改送全站那台，等於把資料送到管理員明確不要的地方，而且畫面上看不出來。

    訊息是固定的一句（可翻譯）；是哪一支工具、哪一台伺服器寫在服務記錄。
    呼叫端沒有接住的話，`app/main.py` 的全域處理器回 503（部署設定的問題，不是使用者送錯東西）。
    """

    MESSAGE = ("這支工具指定的 LLM 伺服器已不存在或設定有誤；不會改用全站或其他伺服器。"
               "請管理員到「LLM 設定」重新指定。")

    def __init__(self, tool_id: str = "", server_id: str = "") -> None:
        super().__init__(self.MESSAGE)
        self.tool_id = tool_id
        self.server_id = server_id


#: 存檔時伺服器清單不合格的原因：代碼 → 給畫面的訊息（固定字串，可翻譯）。
#: 管理頁的 API 照代碼回訊息，**不把例外字串直接回給畫面**。
SERVER_ERRORS: dict[str, str] = {
    "server_list": "其他 LLM 伺服器的資料格式不對。",
    "server_too_many": "其他 LLM 伺服器最多 20 台。",
    "server_name": "每一台 LLM 伺服器都要有名稱。",
    "server_dup_name": "兩台 LLM 伺服器的名稱相同，請改成不同的名稱（各工具的下拉是用名稱分辨的）。",
    "server_url": "LLM 伺服器的位址不合格：必須是 http(s):// 開頭、包含主機名稱，而且不可以是雲端中繼資料位址。",
    "server_in_use": "有工具指定的 LLM 伺服器不在清單裡（可能被刪掉了）。請先把那些工具改成別的伺服器再儲存。",
}


class LLMSettingsError(ValueError):
    """存檔被擋下的原因。`code` 是 `SERVER_ERRORS` 的鍵；`tools` 是受影響的工具 id。"""

    def __init__(self, code: str, tools: Optional[list] = None) -> None:
        super().__init__(code)
        self.code = code
        self.tools = list(tools or [])

    @property
    def message(self) -> str:
        return SERVER_ERRORS.get(self.code, SERVER_ERRORS["server_list"])


# Defaults. Order matters only for documentation; matching is by key.
DEFAULT_SETTINGS: dict = {
    # Master switch — must be explicitly turned on by an admin.
    "enabled": False,
    #: **停用時一併隱藏**（v1.16.11，使用者 2026-09-23 定案）。
    #: 預設 False ＝ 停用時**反灰**：看得到這些功能、也看得到為什麼不能用
    #: （跟語音服務沒設定好時同一條規則）。管理員另外勾這個才隱藏：
    #: 各工具的 LLM 加值選項不顯示、只靠 LLM 的工具從側欄 / 首頁 / 搜尋拿掉。
    #: **只在停用時有作用** —— 啟用時勾著也不影響任何東西。
    #: **不可以把預設改成 True**：停用不代表要隱藏（使用者原話）。
    "hide_when_disabled": False,
    # OpenAI-compat backend. Default points at local Ollama; admin can change
    # to any reachable LLM endpoint via /admin/llm-settings.
    "base_url": "http://localhost:11434/v1",
    #: **加密存放**（v1.16.17）。原本是明文 `api_key`、檔案權限 644，還原樣填回設定頁的
    #: `value=` —— 語音服務 / SSO / 通知三組早就加密、只顯示「已設定」，只有這組沒跟上。
    #: 對外（`get()`、管理頁、API）一律只看得到 `api_key`＝`SECRET_KEPT` 或空字串；
    #: 要真正的金鑰走 `api_key()`。舊檔的明文 `api_key` 第一次讀取時就搬進來。
    #: Ollama 不需要金鑰；雲端的 OpenAI 相容服務才需要。
    "api_key_enc": "",
    # gemma4:26b MoE — validated SOTA on 4-PDF matrix (100% accuracy, ~11s avg).
    "model": "gemma4:26b",
    # 各工具個別模型 — admin 在 LLM 設定頁可以為支援 LLM 的工具個別指定模型，
    # 沒指定 / 留空就用上面的預設 model。Key 是 tool_id，value 是模型名稱
    # 字串。範例：{"translate-doc": "gemma4:26b", "pdf-fill": "gemma4:26b"}
    "model_per_tool": {},
    #: **其他 LLM 伺服器**（全站那一台以外的）。每一台：
    #: `{"id", "name", "base_url", "api_key_enc", "timeout_seconds"}`。
    #: 金鑰一樣加密存放，對外只看得到 `api_key`＝`SECRET_KEPT` 或空字串。
    "servers": [],
    #: 各工具用哪一台：`{tool_id: server_id}`。沒有列在這裡＝沿用全站伺服器。
    #: 指定的那一台不見了 / 設定壞掉 → 那支工具**明確失敗**，不退回全站（見 `LLMServerUnavailable`）。
    "server_per_tool": {},
    # 翻譯並行數 — 逐句翻譯每句一個 prompt 序列送 LLM 太慢；並行能 4-8 倍速。
    # 過高會壓垮本機 Ollama 或讓 GPU OOM；admin 自己依 LLM server 體質設。
    "translate_concurrency": 4,
    # 逐句翻譯（UI）一次最多處理幾句。UI 是逐句並發呼叫，不會單一 request
    # timeout，所以可放大；上限主要是防呆（避免使用者誤丟超大檔讓瀏覽器跑數小時）。
    # 公開同步 API /api/translate-doc 另有較低的固定上限（單一 request 會 timeout）。
    "translate_max_sentences": 20000,
    # 逐句翻譯對照表每頁顯示幾列。句數一大時全部塞進 DOM 會讓瀏覽器卡頓 /
    # 吃記憶體，所以前端分頁、一次只 render 一頁。admin 依機器體質調整。
    "translate_page_size": 200,
    # ---- 文件翻譯（doc-translate）----
    #: 一次請求最多合併幾段 / 多少字。合併是為了攤掉指令的成本（翻成繁中時
    #: 光是台灣用語對照表就佔 1,250 字元）。調大 → 請求更少但單次更久、
    #: 模型也更容易漏段（漏了就整批退回逐段翻，反而變慢）。
    # v1.14.82：翻譯單位從「段」改成「行」之後，10 這個值等於把同樣的內容
    # 拆成 4 倍的請求（一格 4 行的文件實測慢很多）。真正該限制的是**字數**，
    # 段數只是防呆 —— 實測 10 → 40 段每段耗時不變。
    "doctr_batch_segments": 40,
    "doctr_batch_chars": 1200,
    #: 單一檔案的段落上限。實測每段約 0.5~0.6 秒（gemma4:26b、並行 4），
    #: 2 萬段大約 3 小時 —— 背景作業跑得完，而且中途可以按停止。
    "doctr_max_units": 20000,
    "timeout_seconds": 600,          # single HTTP call ceiling — 翻譯 / vision / reasoning 可能 5-10 分鐘
    # 預設拉到 600s（v1.8.58 起，舊 300s）— 客戶實測 gemma 大模型推理單筆 8m+，
    # 加上 reverse proxy 多層 timeout 任一斬掉就 504。要再長就 admin UI 改。
    # nginx 端要同步設 proxy_read_timeout 900s（比這個寬一點當 buffer）。
    # 看 OPS.md「504 Gateway Timeout 排錯流程」。
    "default_review_rounds": 2,      # 1-5
    "confidence_threshold": 0.6,     # corrections below this are shown as low-confidence suggestions
    "consecutive_required": 2,       # same correction must appear N rounds in a row
    "overall_timeout_seconds": 180,  # whole review loop deadline (safety valve)
    "debug_log": False,              # save sent PNG / response JSON for troubleshooting
}


class LLMSettingsManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path = settings.data_dir / "llm_settings.json"
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write(DEFAULT_SETTINGS.copy())

    def _read(self) -> dict:
        """讀檔（**內部用，含密文**）。對外請用 `get()`。"""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        # Merge in any new defaults that weren't in older files.
        # 深拷貝：預設值裡有清單與字典，淺拷貝的話改到的會是 DEFAULT_SETTINGS 本身
        merged = copy.deepcopy(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        # Preserve metadata even though it's not in defaults
        if "updated_at" in data:
            merged["updated_at"] = data["updated_at"]
        # 舊版存的明文金鑰：**讀到就搬**，不要等管理員下次按儲存 ——
        # 沒人去動 LLM 設定頁的話，明文會一直躺在那裡。
        legacy = data.get("api_key")
        if isinstance(legacy, str) and legacy.strip() and not merged.get("api_key_enc"):
            merged["api_key_enc"] = encrypt_secret(legacy.strip())
            self._write(dict(merged))
        elif "api_key" in data:
            self._write(dict(merged))            # 空的舊欄位也順手拿掉
        return merged

    def _write(self, data: dict) -> None:
        data["updated_at"] = time.time()
        # 0600：裡面有密文，但也不必讓其他帳號讀得到（同語音服務設定）
        atomic_json.write_json(self._path, data, mode=0o600)

    @staticmethod
    def _servers_of(data: dict) -> list[dict]:
        """設定檔裡的伺服器清單（只留形狀正確的；檔案被手動改壞時不要讓整頁掛掉）。"""
        out = []
        for srv in data.get("servers") or []:
            if isinstance(srv, dict) and isinstance(srv.get("id"), str):
                out.append(srv)
        return out

    @classmethod
    def _public_server(cls, srv: dict) -> dict:
        out = {k: v for k, v in srv.items() if k != "api_key_enc"}
        out["api_key"] = SECRET_KEPT if srv.get("api_key_enc") else ""
        return out

    @classmethod
    def _public(cls, data: dict) -> dict:
        """給畫面與 API 看的版本：金鑰只說「有沒有」，不給本身（每一台伺服器也一樣）。"""
        out = dict(data)
        out["api_key"] = SECRET_KEPT if out.pop("api_key_enc", "") else ""
        out["servers"] = [cls._public_server(x) for x in cls._servers_of(data)]
        spt = data.get("server_per_tool")
        out["server_per_tool"] = dict(spt) if isinstance(spt, dict) else {}
        return out

    def get(self) -> dict:
        with self._lock:
            return self._public(self._read())

    def api_key(self) -> Optional[str]:
        """真正的金鑰（解密後）。沒設定回 None —— 空的 `Bearer` 會被判成認證失敗。"""
        with self._lock:
            return decrypt_secret(self._read().get("api_key_enc", "")) or None

    def update(self, changes: dict, *, report: Optional[dict] = None) -> dict:
        """Update only known keys; ignore unknown ones to avoid junk in file.

        `api_key`：`SECRET_KEPT` ＝ 不動、非空字串 ＝ 換成這把（加密後存）、
        空字串 / None ＝ 清掉。金鑰欄位對 Ollama 是選填的，所以清空欄位就是移除。

        `servers`：**整份清單**（不在清單裡的伺服器就是刪掉）；每一台的 `api_key`
        規則同上。`server_per_tool`：`{tool_id: server_id}`，空值＝沿用全站伺服器。
        有工具指到不在清單裡的伺服器 → `LLMSettingsError("server_in_use")`，**整筆不存**。

        `report`：給呼叫端寫稽核紀錄用 —— 只記金鑰「不變 / 已更新 / 已清除」，不記金鑰本身。
        """
        with self._lock:
            data = self._read()
            changes = dict(changes or {})
            rep = report if report is not None else {}
            if "api_key" in changes:
                val = changes.pop("api_key")
                had = bool(data.get("api_key_enc"))
                if val == SECRET_KEPT:
                    rep["api_key_status"] = "不變"
                elif isinstance(val, str) and val.strip():
                    data["api_key_enc"] = encrypt_secret(val.strip())
                    rep["api_key_status"] = "已更新"
                else:
                    data["api_key_enc"] = ""
                    rep["api_key_status"] = "已清除" if had else "未設定"
            changes.pop("api_key_enc", None)     # 密文只由這裡產生，不收外面送來的
            touched_servers = "servers" in changes or "server_per_tool" in changes
            if "servers" in changes:
                data["servers"] = self._merge_servers(
                    self._servers_of(data), changes.pop("servers"), rep)
            if "server_per_tool" in changes:
                data["server_per_tool"] = self._clean_server_per_tool(
                    changes.pop("server_per_tool"))
            if touched_servers:
                ids = {x["id"] for x in self._servers_of(data)}
                spt = data.get("server_per_tool") or {}
                missing = sorted(t for t, sid in spt.items() if sid not in ids)
                if missing:
                    raise LLMSettingsError("server_in_use", missing)
            for k, v in changes.items():
                if k in DEFAULT_SETTINGS:
                    data[k] = v
            self._write(data)
            return self._public(data)

    @staticmethod
    def _new_server_id(taken: set) -> str:
        while True:
            sid = secrets.token_hex(6)
            if sid not in taken:
                return sid

    def _merge_servers(self, old: list[dict], incoming, rep: dict) -> list[dict]:
        """把頁面送來的整份伺服器清單併進設定（金鑰照 `api_key` 的規則處理）。"""
        if not isinstance(incoming, list):
            raise LLMSettingsError("server_list")
        items = [x for x in incoming if isinstance(x, dict)]
        if len(items) > MAX_SERVERS:
            raise LLMSettingsError("server_too_many")
        old_by_id = {x["id"]: x for x in old}
        out: list[dict] = []
        taken: set = set()
        names: set = set()
        statuses: list[dict] = []
        lo, hi = SERVER_TIMEOUT_RANGE
        for item in items:
            sid = str(item.get("id") or "").strip().lower()
            if not _SERVER_ID_RE.fullmatch(sid) or sid in taken:
                sid = self._new_server_id(taken | set(old_by_id))
            taken.add(sid)
            prev = old_by_id.get(sid) or {}
            name = _CTRL_RE.sub(" ", str(item.get("name") or "")).strip()[:60]
            if not name:
                raise LLMSettingsError("server_name")
            if name.casefold() in names:
                raise LLMSettingsError("server_dup_name")
            names.add(name.casefold())
            try:
                base = validate_llm_base_url(str(item.get("base_url") or ""))
            except ValueError:
                raise LLMSettingsError("server_url") from None
            try:
                timeout = int(float(item.get("timeout_seconds")
                                    or prev.get("timeout_seconds") or 600))
            except (TypeError, ValueError):
                timeout = int(prev.get("timeout_seconds") or 600)
            timeout = max(lo, min(hi, timeout))
            val = item.get("api_key")
            had = bool(prev.get("api_key_enc"))
            if val == SECRET_KEPT:
                enc = prev.get("api_key_enc", "")
                status = "不變" if had else "未設定"
            elif isinstance(val, str) and val.strip():
                enc = encrypt_secret(val.strip())
                status = "已更新"
            else:
                enc = ""
                status = "已清除" if had else "未設定"
            out.append({"id": sid, "name": name, "base_url": base,
                        "api_key_enc": enc, "timeout_seconds": timeout})
            statuses.append({"id": sid, "name": name, "base_url": base,
                             "api_key_status": status})
        rep["servers"] = statuses
        removed = [{"id": x["id"], "name": x.get("name", "")}
                   for x in old if x["id"] not in taken]
        if removed:
            rep["servers_removed"] = removed
        return out

    def _clean_server_per_tool(self, value) -> dict:
        """只收認得的工具 id、格式正確的伺服器編號；空值＝沿用全站（不存）。"""
        if not isinstance(value, dict):
            return {}
        known = {t["id"] for t in self.KNOWN_LLM_TOOLS}
        out = {}
        for tool, sid in value.items():
            sid = str(sid or "").strip().lower()
            if tool in known and sid and _SERVER_ID_RE.fullmatch(sid):
                out[tool] = sid
        return out

    # ----- 其他 LLM 伺服器 -----
    def servers(self) -> list[dict]:
        """對外的伺服器清單（金鑰只說有沒有）。"""
        with self._lock:
            return [self._public_server(x) for x in self._servers_of(self._read())]

    def _server_raw(self, data: dict, server_id: str) -> Optional[dict]:
        for srv in self._servers_of(data):
            if srv["id"] == server_id:
                return srv
        return None

    def server(self, server_id: str) -> Optional[dict]:
        with self._lock:
            srv = self._server_raw(self._read(), str(server_id or ""))
            return self._public_server(srv) if srv else None

    def server_api_key(self, server_id: str) -> Optional[str]:
        """某一台伺服器真正的金鑰（解密後）；沒有這台或沒設定回 None。"""
        with self._lock:
            srv = self._server_raw(self._read(), str(server_id or ""))
            if not srv:
                return None
            return secret_box.decrypt(srv.get("api_key_enc", ""),
                                      label="llm_settings.servers.api_key_enc") or None

    def server_for(self, tool_id: Optional[str]) -> str:
        """這支工具指定的伺服器編號；沿用全站回空字串。"""
        if not tool_id:
            return ""
        spt = self.get().get("server_per_tool") or {}
        return str(spt.get(tool_id) or "")

    def base_url_for(self, tool_id: Optional[str]) -> str:
        """這支工具實際會送去的位址（給畫面顯示、模型能力查詢用）。

        指定的伺服器不見了回空字串 —— **不回全站的位址**，不然畫面會說它送去全站那一台。
        """
        with self._lock:
            data = self._read()
            sid = str((data.get("server_per_tool") or {}).get(tool_id) or "") if tool_id else ""
            if not sid:
                return data.get("base_url") or ""
            srv = self._server_raw(data, sid)
            return (srv or {}).get("base_url") or ""

    def server_problem(self, tool_id: Optional[str]) -> Optional[str]:
        """這支工具指定的伺服器有問題時回那句固定訊息；沒問題（或沿用全站）回 None。

        給「呼叫端會把例外吞掉、改成跳過」的地方用（例如送件前檢核的 LLM 層），
        讓畫面講得出是設定的問題，而不是安靜地顯示「LLM 未設定」。
        """
        with self._lock:
            data = self._read()
            sid = str((data.get("server_per_tool") or {}).get(tool_id) or "") if tool_id else ""
            if not sid:
                return None
            srv = self._server_raw(data, sid)
            if not srv:
                return LLMServerUnavailable.MESSAGE
            try:
                validate_llm_base_url(srv.get("base_url") or "")
            except ValueError:
                return LLMServerUnavailable.MESSAGE
            return None

    def is_enabled(self) -> bool:
        return bool(self.get().get("enabled"))

    def is_hidden(self) -> bool:
        """LLM 相關的介面要不要**整個不顯示**：停用 **而且** 管理員勾了「停用時一併隱藏」。

        只影響畫面；API 照舊回「LLM 未啟用」（隱藏不是權限）。
        """
        s = self.get()
        return (not s.get("enabled")) and bool(s.get("hide_when_disabled"))

    # ----- per-tool model resolution -----
    # 已知支援 LLM 的工具清單（admin UI 用此清單渲染 per-tool 模型選單）。
    # 加新 LLM-using tool 時要更新這個 list — 避免 UI 漏列。
    KNOWN_LLM_TOOLS: list[dict] = [
        {"id": "translate-doc",    "name": "逐句翻譯",
         "use": "純文字 chat — 中譯英、英譯中等", "kind": "text"},
        {"id": "doc-translate",    "name": "文件翻譯（整份辦公文件）",
         "use": "整份 Word / Excel / PowerPoint 翻成另一種語言，產出同格式的檔案。"
                "這支很吃量（一份文件動輒幾十次請求）。"
                "Gemma 4 要看的是「每個 token 實際算幾個參數」不是總參數："
                "gemma4:26b 是 MoE（每 token 只啟用約 4B），吞吐量最好；"
                "gemma4:12b 是 dense，12B 全都要算，反而比 26b 慢 —— "
                "記憶體不夠時才選它。", "kind": "text"},
        {"id": "pdf-extract-text", "name": "擷取文字（LLM 段落重排）",
         "use": "把 PDF 版面切斷的句子重排回來", "kind": "text"},
        {"id": "pdf-fill",         "name": "表單自動填寫（LLM 校驗）",
         "use": "校驗欄位填值正確（看 PNG → 給 yes/no）", "kind": "vision"},
        {"id": "doc-deident",      "name": "文件去識別化（LLM 補偵測）",
         "use": "regex 抓不到的人名 / 職稱 / 客戶代號等 context-sensitive 案例", "kind": "text"},
        {"id": "text-deident",     "name": "文字去識別化（LLM 補偵測）",
         "use": "純文字版的 doc-deident，貼上 / 上傳文字檔做去識別化", "kind": "text"},
        {"id": "meeting-summary",  "name": "會議摘要",
         "use": "會議逐字稿 → 摘要 / 決議 / 待辦 / 風險 / 議題，每一條都要附段號。"
                "這支要的是「照格式回答而且不要編」，不是文筆。"
                "建議用 gemma4:26b 或參數量更高的模型；"
                "顯示記憶體有限時 qwen3.8:27b 是實測可用的選擇"
                "（兩者都跑過 160 分鐘的語料各三次：抓到率 100% / 94%，"
                "兩個都沒有編造任何東西，差別是風格不是安全性）。"
                "一場兩小時的會議約 30~60 次請求（視窗數 ＋ 複審 ＋ 議題 ＋ 摘要），"
                "所以吞吐量也要一起看。", "kind": "text"},
        {"id": "official-doc",     "name": "公文撰擬",
         # 建議模型照 `tools/official_doc_eval/` 的實測寫（2026-10-07，14 個合成案例）
         "use": "把白話需求寫成「簽」、依來文與辦理方向擬「簽辦意見」。"
                "格式由程式排，模型只寫內容；要的是照格式回答、不編造金額日期法規，不是文筆。"
                "建議模型（14 個案例實測）：gemma4:26b（建議；編造 0、每件約 6 秒）；"
                "qwen3.8:27b 也可以（編造 0、照寫最完整，但慢一倍）。"
                "TAIDE 12B（Gemma-3-TAIDE-12b-Chat）不建議：會自己補原文沒有的理由、改掉辦理方向、"
                "偶爾回不出格式，每件約 24 秒。"
                "一份草稿約 2~4 次請求。",
         "kind": "text"},
        {"id": "pdf-wordcount",    "name": "字數統計（LLM 摘要 / 關鍵字）",
         "use": "依文章內容生成 3-5 句摘要 + TOP 10 關鍵概念", "kind": "text"},
        {"id": "pdf-annotations",  "name": "註解整理（LLM 自動分組）",
         "use": "把多筆審閱意見自動分『重大 / 一般 / 提問』三類", "kind": "text"},
        {"id": "doc-diff",         "name": "文件差異比對（LLM 變動摘要）",
         "use": "比對行差異後，告訴使用者主要修改了哪幾條條款 / 段落", "kind": "text"},
        {"id": "submission-check", "name": "送件前檢核（LLM 變體合併 / 範本痕跡）",
         "use": "公司命名變體合併（○○ ↔ (brand)）+ 漏改範本進階推論", "kind": "text"},
        {"id": "pdf-ocr", "name": "OCR 文字辨識（LLM 文字校正）",
         "use": "純文字校正：抓 typo / 字元誤判（0/O、1/l、CJK 偏旁混淆），不看影像", "kind": "text"},
        {"id": "pdf-ocr-vision", "name": "OCR 文字辨識（LLM 視覺校對 / 直接 / 對位 / 完整辨識共用）",
         "use": "視覺校對：直接看頁面影像對照 OCR 結果，能修文字脫漏 / 排版亂；直接 / 對位 / 完整辨識也走此設定。完整辨識建議用 qwen2.5vl:7b（grounding 能給座標、無 thinking mode、約 9GB VRAM）；qwen3-vl 因 thinking mode 在 Ollama 整合不穩、gemma4:26b 無 grounding 能力不可用於完整辨識", "kind": "vision"},
        {"id": "einvoice-scan", "name": "電子發票處理（LLM 判讀會計科目）",
         "use": "批次依賣方統編 / 名稱 / 行業，判斷對應會計科目（油料費 / 餐費 / 郵電費 等）", "kind": "text"},
        {"id": "business-card-reader", "name": "名片辨識（AI 協助，選用）",
         "use": "整合名片清單時判斷兩筆是不是同一人；校對時依 OCR 文字建議欄位歸類（只送那一張的文字，建議一律要人確認）", "kind": "text"},
        {"id": "business-card-reader-vision", "name": "名片辨識（AI 看圖協助，選用）",
         "use": "校對時把那一張裁好的名片圖送給視覺模型建議欄位（要另外在「設定 → 名片簿」選看圖模式）", "kind": "vision"},
    ]

    def get_model_for(self, tool_id: str) -> str:
        """Return the model name to use for ``tool_id``. Falls back to the
        global default model if no per-tool override is set or value is
        empty/blank. Use this everywhere instead of reading ``s["model"]``
        directly so per-tool config is honoured uniformly."""
        s = self.get()
        per_tool = s.get("model_per_tool") or {}
        v = (per_tool.get(tool_id) or "").strip()
        if v:
            return v
        return s.get("model") or "gemma4:26b"

    def make_client(self, tool_id: Optional[str] = None, *,
                    timeout: Optional[float] = None):
        """這支工具要用的 LLMClient；LLM 停用時回 None。

        * 沒給 `tool_id`、或那支工具沒指定伺服器 → 全站伺服器（跟以前一樣）。
        * 指定了另一台 → 用那一台（位址、金鑰、逾時都是那一台的）。
        * **指定的那台不存在 / 位址不合格 → 丟 `LLMServerUnavailable`**，絕不退回全站
          或別台。回 None 的話呼叫端會說「LLM 未啟用」—— 那是錯的方向（管理員會去開
          一個早就開著的開關），而且有幾支工具拿到 None 會安靜地跳過。
        * `timeout`：呼叫端自己要的較短逾時（OCR、送件前檢核）；沒給用那台伺服器的設定。

        作業開始時呼叫一次、整件作業都用同一個 client —— 管理員中途改設定，
        已經在跑的作業不會換伺服器。Lazy import：停用時不載入 httpx 那一套。
        """
        with self._lock:
            s = self._read()
            if not s.get("enabled"):
                return None
            sid = str((s.get("server_per_tool") or {}).get(tool_id) or "") if tool_id else ""
            srv = self._server_raw(s, sid) if sid else None
            global_key = self.api_key() if not sid else None
            srv_key = (secret_box.decrypt(srv.get("api_key_enc", ""),
                                          label="llm_settings.servers.api_key_enc") or None
                       if srv else None)
        from .llm_client import LLMClient
        if sid:
            if srv is None:
                logger.warning("工具 %r 指定的 LLM 伺服器 %r 已不存在；不改用全站或其他伺服器",
                               str(tool_id)[:80], sid[:40])
                raise LLMServerUnavailable(str(tool_id or ""), sid)
            try:
                return LLMClient(
                    base_url=srv.get("base_url") or "",
                    api_key=srv_key,
                    timeout=float(timeout or srv.get("timeout_seconds") or 600),
                )
            except ValueError:
                logger.warning("工具 %r 指定的 LLM 伺服器 %r 位址不合格；不改用全站或其他伺服器",
                               str(tool_id)[:80], sid[:40])
                raise LLMServerUnavailable(str(tool_id or ""), sid) from None
        return LLMClient(
            base_url=s["base_url"],
            api_key=global_key,
            timeout=float(timeout or s.get("timeout_seconds", 60)),
        )

    def make_server_client(self, server_id: str, *, timeout: Optional[float] = None):
        """管理頁列某一台伺服器的模型用（不看 LLM 是否啟用 —— 管理員在設定時就要看得到清單）。
        沒有這台回 None；位址不合格丟 ValueError（同 `LLMClient`）。"""
        with self._lock:
            srv = self._server_raw(self._read(), str(server_id or ""))
            if not srv:
                return None
            key = secret_box.decrypt(srv.get("api_key_enc", ""),
                                     label="llm_settings.servers.api_key_enc") or None
        from .llm_client import LLMClient
        return LLMClient(base_url=srv.get("base_url") or "", api_key=key,
                         timeout=float(timeout or srv.get("timeout_seconds") or 600))


llm_settings = LLMSettingsManager()
