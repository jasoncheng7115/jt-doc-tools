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

import json
import threading
import time
from pathlib import Path
from typing import Optional

from ..config import settings


# Defaults. Order matters only for documentation; matching is by key.
DEFAULT_SETTINGS: dict = {
    # Master switch — must be explicitly turned on by an admin.
    "enabled": False,
    # OpenAI-compat backend. Default points at local Ollama; admin can change
    # to any reachable LLM endpoint via /admin/llm-settings.
    "base_url": "http://localhost:11434/v1",
    "api_key": None,                # Ollama doesn't need; reserved for cloud
    # gemma4:26b MoE — validated SOTA on 4-PDF matrix (100% accuracy, 5× faster
    # than qwen3-vl:8b). qwen3-vl:8b kept as low-VRAM fallback (~8GB vs 18GB).
    # Do not drop below qwen3-vl:8b (documented unreliable).
    "model": "gemma4:26b",
    # 各工具個別模型 — admin 在 LLM 設定頁可以為支援 LLM 的工具個別指定模型，
    # 沒指定 / 留空就用上面的預設 model。Key 是 tool_id，value 是模型名稱
    # 字串。範例：{"translate-doc": "qwen3:32b", "pdf-fill": "gemma4:26b"}
    "model_per_tool": {},
    "timeout_seconds": 300,          # single HTTP call ceiling — vision + reasoning easily >120s
    # (default bumped to 300 because qwen3-vl cold start + image processing
    #  + reasoning often exceeds 120s; even with streaming the socket can
    #  stay silent >30-60s while the model "reads" the image)
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
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        # Merge in any new defaults that weren't in older files.
        merged = DEFAULT_SETTINGS.copy()
        merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        # Preserve metadata even though it's not in defaults
        if "updated_at" in data:
            merged["updated_at"] = data["updated_at"]
        return merged

    def _write(self, data: dict) -> None:
        data["updated_at"] = time.time()
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self) -> dict:
        with self._lock:
            return self._read()

    def update(self, changes: dict) -> dict:
        """Update only known keys; ignore unknown ones to avoid junk in file."""
        with self._lock:
            data = self._read()
            for k, v in (changes or {}).items():
                if k in DEFAULT_SETTINGS:
                    data[k] = v
            self._write(data)
            return data

    def is_enabled(self) -> bool:
        return bool(self.get().get("enabled"))

    # ----- per-tool model resolution -----
    # 已知支援 LLM 的工具清單（admin UI 用此清單渲染 per-tool 模型選單）。
    # 加新 LLM-using tool 時要更新這個 list — 避免 UI 漏列。
    KNOWN_LLM_TOOLS: list[dict] = [
        {"id": "translate-doc",    "name": "逐句翻譯",
         "use": "純文字 chat — 中譯英、英譯中等", "kind": "text"},
        {"id": "pdf-extract-text", "name": "擷取文字（LLM 段落重排）",
         "use": "把 PDF 版面切斷的句子重排回來", "kind": "text"},
        {"id": "pdf-fill",         "name": "表單自動填寫（LLM 校驗）",
         "use": "校驗欄位填值正確（看 PNG → 給 yes/no）", "kind": "vision"},
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

    def make_client(self):
        """Construct a configured LLMClient, or return None if disabled.
        Lazy-imports so disabled state never loads httpx-related code paths."""
        s = self.get()
        if not s.get("enabled"):
            return None
        from .llm_client import LLMClient
        return LLMClient(
            base_url=s["base_url"],
            api_key=s.get("api_key") or None,
            timeout=float(s.get("timeout_seconds", 60)),
        )


llm_settings = LLMSettingsManager()
