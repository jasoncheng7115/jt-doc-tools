"""Model 特性偵測與 profile cache。

LLM 模型 family 各有特性（thinking 與否、vision 支援、最佳影像大小、停 thinking
方法不同），統一用 profile dict 表達，client 端依 profile 套用對應處理。

偵測順序：
1. Ollama `/api/show` capabilities（最權威 — 抓得到就用）
2. Model 名稱 heuristic fallback（離線 / 非 Ollama 後端用）

Profile cache 存在 `data/llm_model_profiles.json`，避免每次 call 都 round-trip。
Admin 改 model 後可手動 invalidate（`refresh=True`）。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _profile_cache_path() -> Path:
    """Lazy resolve — config 可能在 import 時還沒 ready。"""
    try:
        from ..config import settings
        return Path(settings.data_dir) / "llm_model_profiles.json"
    except Exception:
        return Path("data") / "llm_model_profiles.json"


@dataclass
class ModelProfile:
    """單一 model 的處理 profile。"""
    model: str
    is_thinking: bool = False               # 需要抑制 reasoning trace
    supports_vision: bool = False           # 可吃影像 input
    preferred_image_max: int = 1568         # 影像長邊上限（px），太大送過去白做工
    no_think_marker: str = ""               # user prompt 前綴 marker，如 "/no_think"
    use_chat_template_kwargs: bool = False  # 走 chat_template_kwargs.enable_thinking=False
    source: str = "default"                 # "ollama_show" / "name_heuristic" / "manual"
    fetched_at: float = 0.0
    capabilities_raw: list[str] = field(default_factory=list)


# ---- name heuristic patterns ----
_THINKING_NAME_HINTS = ("qwen3", "qwq", "o1-", "r1", "gemma3", "gemma4", "deepseek-r1")
_VISION_NAME_HINTS = ("vl", "vision", "llava", "minicpm-v", "gemma3", "gemma4", "internvl")
_QWEN_FAMILY_HINTS = ("qwen", "qwq")
_GEMMA_FAMILY_HINTS = ("gemma",)


def _name_lower(model: str) -> str:
    return (model or "").lower()


def _detect_image_max_for(name: str) -> int:
    if "minicpm-v" in name:
        return 448      # MiniCPM-V 1.x 設計上吃 448
    if "llava" in name:
        return 672      # LLaVA 1.5/1.6 預設
    if "internvl" in name:
        return 1024
    return 1568         # gemma vision tile / qwen-vl 都偏好 ~1568


def _fetch_ollama_capabilities(model: str, base_url: str, timeout: float = 8.0) -> Optional[list[str]]:
    """跑 Ollama `/api/show` 抓 capabilities list。
    base_url 必須含 `/v1`（OpenAI-compat 介面），由它推算 native endpoint 位置。
    回 None 表示無法判定（非 Ollama / 連不上 / model 不存在）。"""
    if not base_url or "/v1" not in base_url:
        return None
    try:
        import httpx
    except Exception:
        return None
    ollama_base = base_url.rsplit("/v1", 1)[0]
    try:
        r = httpx.post(
            f"{ollama_base}/api/show",
            json={"name": model},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        if r.status_code != 200:
            log.debug("ollama /api/show %s → HTTP %d", model, r.status_code)
            return None
        data = r.json()
        caps = data.get("capabilities", []) or []
        return list(caps)
    except Exception as e:
        log.debug("ollama /api/show %s failed: %s", model, e)
        return None


def detect_profile(model: str, base_url: str = "") -> ModelProfile:
    """偵測 model profile（不查 cache）。"""
    name = _name_lower(model)
    caps = _fetch_ollama_capabilities(model, base_url)

    if caps is not None:
        is_thinking = "thinking" in caps
        supports_vision = "vision" in caps
        # 名稱再 confirm（capabilities 缺漏時補）
        if not is_thinking and any(t in name for t in _THINKING_NAME_HINTS):
            is_thinking = True
        if not supports_vision and any(t in name for t in _VISION_NAME_HINTS):
            supports_vision = True
        source = f"ollama_show:{','.join(sorted(caps))}"
    else:
        is_thinking = any(t in name for t in _THINKING_NAME_HINTS)
        supports_vision = any(t in name for t in _VISION_NAME_HINTS)
        source = "name_heuristic"
        caps = []

    no_think_marker = "/no_think" if any(h in name for h in _QWEN_FAMILY_HINTS) else ""
    use_chat_template_kwargs = (
        any(h in name for h in _GEMMA_FAMILY_HINTS)
        or any(h in name for h in _QWEN_FAMILY_HINTS)
    )

    return ModelProfile(
        model=model,
        is_thinking=is_thinking,
        supports_vision=supports_vision,
        preferred_image_max=_detect_image_max_for(name),
        no_think_marker=no_think_marker,
        use_chat_template_kwargs=use_chat_template_kwargs,
        source=source,
        fetched_at=time.time(),
        capabilities_raw=caps,
    )


# ---- cache ----

_in_mem_cache: dict[str, ModelProfile] = {}


def _load_disk_cache() -> dict:
    p = _profile_cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("load llm_model_profiles cache failed: %s", e)
        return {}


def _save_disk_cache(cache: dict) -> None:
    p = _profile_cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("save llm_model_profiles cache failed: %s", e)


def _cache_key(model: str, base_url: str) -> str:
    return f"{base_url}|{model}"


def get_profile(model: str, base_url: str = "", refresh: bool = False,
                max_age_seconds: float = 86400.0) -> ModelProfile:
    """取得 model profile。先查 in-memory，再查 disk cache，最後才偵測。

    cache 有效期預設 24hr — 過期會背景重抓但仍回 stale 值（不阻塞 user）。
    refresh=True 強制重抓（admin 換 model 或 manually invalidate 用）。
    """
    if not model:
        return ModelProfile(model="", source="empty")
    key = _cache_key(model, base_url)
    now = time.time()

    if not refresh and key in _in_mem_cache:
        p = _in_mem_cache[key]
        if now - p.fetched_at < max_age_seconds:
            return p

    if not refresh:
        disk = _load_disk_cache()
        d = disk.get(key)
        if d:
            try:
                p = ModelProfile(**d)
                _in_mem_cache[key] = p
                if now - p.fetched_at < max_age_seconds:
                    return p
            except Exception:
                pass

    # 偵測新值並寫回
    p = detect_profile(model, base_url)
    _in_mem_cache[key] = p
    disk = _load_disk_cache()
    disk[key] = asdict(p)
    _save_disk_cache(disk)
    log.info("llm model profile: %s thinking=%s vision=%s img_max=%d source=%s",
             model, p.is_thinking, p.supports_vision, p.preferred_image_max, p.source)
    return p


def invalidate(model: Optional[str] = None, base_url: str = "") -> None:
    """清 cache（admin 改設定時呼叫）。model=None 清全部。"""
    global _in_mem_cache
    if model is None:
        _in_mem_cache = {}
        try:
            _profile_cache_path().unlink(missing_ok=True)
        except Exception:
            pass
        return
    key = _cache_key(model, base_url)
    _in_mem_cache.pop(key, None)
    disk = _load_disk_cache()
    disk.pop(key, None)
    _save_disk_cache(disk)


def list_cached_profiles() -> list[dict]:
    """給 admin UI 列出當前已偵測的 profiles。"""
    disk = _load_disk_cache()
    out = []
    for key, p in disk.items():
        try:
            base, model = key.split("|", 1)
        except ValueError:
            base, model = "", key
        item = dict(p)
        item["_base_url"] = base
        out.append(item)
    return out
