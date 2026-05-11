"""OpenAI-compat HTTP client for vision LLM (gemma4, gemma3, etc.).

This module is part of the **附加功能** (add-on) LLM review feature. It is
imported lazily — never from core code paths — so missing / misconfigured
LLM backends never break the core PDF tools.

Usage::

    from app.core.llm_settings import llm_settings
    client = llm_settings.make_client()
    if client:
        result = client.test_connection()

Backend compatibility (all are OpenAI-compat HTTP):
- Ollama  (http://localhost:11434/v1)
- vLLM    (http://localhost:8000/v1)
- LM Studio  (http://localhost:1234/v1)
- jan.ai  (http://localhost:1337/v1)
- DGX Spark + Ollama (http://<lan-ip>:11434/v1)  ← deployment 預設場景
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx


# v1.5.8: SSRF validator 搬到 app.core.url_safety 以絕對 import 路徑走,
# 讓 CodeQL MaD barrierModel 認得（同檔 private function 不被 API graph 抓到）。
# 維持 _validate_llm_base_url 別名給 backward-compat;`__init__` 走別名 OK,
# regression tests `tests/test_llm_url_ssrf.py` 也走別名。
from app.core.url_safety import validate_llm_base_url as _validate_llm_base_url  # noqa: F401


@dataclass
class ModelInfo:
    """Single model entry from /v1/models. ``size_bytes`` is best-effort
    (not all backends report it; Ollama does)."""
    id: str
    owned_by: str = ""
    size_bytes: int = 0

    @property
    def looks_vision(self) -> bool:
        """Heuristic: model id mentions vision / multimodal naming."""
        n = self.id.lower()
        return any(t in n for t in (
            "vl", "vision", "llava", "minicpm-v", "gemma3", "gemma4",
        ))


@dataclass
class ConnectionResult:
    ok: bool
    latency_ms: int = 0
    models: list[ModelInfo] = field(default_factory=list)
    error: Optional[str] = None


class LLMError(Exception):
    """Raised when the LLM backend returns malformed / unexpected response."""


def _extract_json_from_response(content: str) -> dict:
    """Find + parse a JSON object inside free-form LLM output.

    The LLM is asked to output JSON, but when we don't force json_object
    mode (we don't, because it hurts reasoning) the model may wrap the JSON
    in prose ("好的，以下是我的分析：{...}") or markdown fences (```json...```).
    We try, in order:
    1. Parse the entire content as JSON
    2. Strip ```json ... ``` fence and parse
    3. Find the first balanced {...} block via bracket counting and parse

    Raises ``LLMError`` when none of the above yields valid JSON.
    """
    s = (content or "").strip()
    if not s:
        raise LLMError("empty response")

    # 1. whole thing is JSON?
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 2. markdown fence?
    if "```" in s:
        # Extract content between first ``` and last ```
        lines = s.splitlines()
        in_block = False
        block: list[str] = []
        for ln in lines:
            if ln.strip().startswith("```"):
                if in_block:
                    break
                in_block = True
                continue
            if in_block:
                block.append(ln)
        if block:
            try:
                return json.loads("\n".join(block).strip())
            except json.JSONDecodeError:
                pass

    # 3. Find the first balanced { ... } block. Naive bracket counting works
    # because JSON strings with embedded { } are rare in typical model output,
    # and we're just looking for the outermost object.
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = s[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # keep scanning — maybe there's another {...}
                    start = -1
                    continue

    raise LLMError(f"no parseable JSON in response: {s[:300]!r}")


class LLMClient:
    """Thin OpenAI-compat client. Stateless — safe to construct per-request."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
    ):
        # v1.5.8: 用絕對 import 形式 call,讓 CodeQL barrierModel 認得
        from app.core.url_safety import validate_llm_base_url
        self.base_url = validate_llm_base_url(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # ----- /v1/models ----------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        """GET {base_url}/models — return list of available models.

        Handles two response shapes:
        - OpenAI standard: ``{"data": [{"id":..., "owned_by":...}]}``
        - Ollama-extended: same, plus ``"size"`` field per model
        """
        r = httpx.get(
            f"{self.base_url}/models",
            headers=self._headers(),
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        items = []
        if isinstance(data, dict):
            items = data.get("data", []) or []
        out: list[ModelInfo] = []
        for m in items:
            if not isinstance(m, dict):
                continue
            out.append(ModelInfo(
                id=str(m.get("id", "")),
                owned_by=str(m.get("owned_by", "")),
                size_bytes=int(m.get("size") or 0),
            ))
        return out

    def test_connection(self) -> ConnectionResult:
        """Round-trip check: tries to fetch model list, measures latency.
        Never raises — returns ConnectionResult with ``ok=False`` on failure."""
        try:
            t0 = time.monotonic()
            models = self.list_models()
            latency = int((time.monotonic() - t0) * 1000)
            return ConnectionResult(ok=True, latency_ms=latency, models=models)
        except httpx.ConnectError as e:
            return ConnectionResult(ok=False, error=f"連線失敗：{e}")
        except httpx.TimeoutException:
            return ConnectionResult(ok=False, error=f"逾時 ({self.timeout:.0f}s)")
        except httpx.HTTPStatusError as e:
            return ConnectionResult(
                ok=False,
                error=f"HTTP {e.response.status_code}：{e.response.text[:200]}",
            )
        except Exception as e:  # noqa: BLE001
            return ConnectionResult(ok=False, error=f"未預期錯誤：{type(e).__name__}: {e}")

    # ----- /v1/chat/completions ------------------------------------------

    def text_query(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        think: bool = False,
        system: str | None = None,
    ) -> str:
        """Send a plain-text prompt (no images), return the raw model
        output. Used by features like paragraph reflow that expect prose
        back, not JSON.

        ``think=False`` (default) tries to suppress chain-of-thought on
        models that support it. This is a best-effort belt-and-braces:

        - Qwen3 / QwQ honour a literal ``/no_think`` marker in the user
          message, so we prepend it.
        - Ollama's OpenAI-compat endpoint accepts a top-level ``think``
          field (ignored by vanilla OpenAI) — we set it explicitly.
        - Ollama also accepts ``options.think`` and ``chat_template_kwargs``.
        - For models without a native toggle, we add a system message
          spelling out "no reasoning, output only the answer".

        Any backend that doesn't recognise these just ignores them.
        """
        messages: list[dict] = []
        if not think:
            default_sys = (
                "Respond with ONLY the requested output. No reasoning "
                "traces, no <think> tags, no prefaces, no explanations. "
                "Output the final answer directly."
            )
            messages.append({
                "role": "system",
                "content": (system + "\n\n" + default_sys) if system else default_sys,
            })
            # Qwen3 / QwQ family — literal marker disables reasoning
            user_prompt = "/no_think\n\n" + prompt
        else:
            if system:
                messages.append({"role": "system", "content": system})
            user_prompt = prompt
        messages.append({"role": "user", "content": user_prompt})

        payload: dict = {
            "model": model,
            "temperature": temperature,
            "stream": True,
            "messages": messages,
        }
        if not think:
            # Ollama-specific knobs (extras ignored by other backends).
            payload["think"] = False
            payload["options"] = {"think": False}
            payload["chat_template_kwargs"] = {"enable_thinking": False}
            # Some OpenAI reasoning models honour reasoning_effort
            payload["reasoning_effort"] = "none"
        if max_tokens:
            payload["max_tokens"] = max_tokens
        parts: list[str] = []
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_line = line[6:].strip()
                    if data_line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_line)
                    except json.JSONDecodeError:
                        continue
                    try:
                        delta = chunk["choices"][0].get("delta", {}).get("content", "")
                    except (KeyError, IndexError, TypeError):
                        delta = ""
                    if delta:
                        parts.append(delta)
        except httpx.HTTPStatusError:
            raise
        return "".join(parts).strip()

    def vision_query(
        self,
        png_bytes,  # bytes OR list[bytes] — single or multiple images
        prompt: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        parse_json: bool = True,
        think: bool = False,
    ):
        """parse_json=True (預設，向後相容): 回 dict（會強行 JSON parse，
        失敗會 raise LLMError）。
        parse_json=False: 回 raw str 純文字，由 caller 自行處理。
        """
        """Send one-or-more images + a text prompt, expect JSON response.

        ``png_bytes`` may be a single ``bytes`` blob or a ``list[bytes]``;
        when a list, multiple ``image_url`` parts are sent in order so the
        prompt can refer to "first image" vs "second image" (used by the
        review feature to send before / after of the same PDF page).

        Forces ``response_format=json_object`` so the model returns parseable
        JSON. Most modern vision models honour this; if a particular backend
        ignores it the parser strips ```json fences before parsing.
        """
        # Normalize to list
        imgs = png_bytes if isinstance(png_bytes, list) else [png_bytes]
        content: list[dict] = []
        for png in imgs:
            b64 = base64.b64encode(png).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })

        # Model profile：依 model family 套用 thinking 抑制方式
        from .llm_model_profile import get_profile as _get_profile
        profile = _get_profile(model, self.base_url)
        # think=False AND model 真的是 thinking model → 才套用抑制
        # （非 thinking model 加 /no_think marker 是無害但無意義噪音）
        suppress_thinking = (not think) and profile.is_thinking

        text_prompt = prompt
        if suppress_thinking and profile.no_think_marker:
            text_prompt = profile.no_think_marker + "\n\n" + prompt
        content.append({"type": "text", "text": text_prompt})

        # IMPORTANT design notes:
        # 1. We DON'T set response_format=json_object. For many vision models
        #    that constraint makes the model much less thorough. We let it
        #    output prose + extract JSON ourselves.
        # 2. We USE stream=True so the HTTP socket stays active via chunked
        #    SSE events. Non-streaming requests hold the socket silent for
        #    the entire generation, which trips httpx's read_timeout on
        #    anything longer than ~120s (vision reasoning often is).
        # 對 thinking model 加 system 指令再次強調不要 reasoning trace
        messages: list[dict] = []
        if suppress_thinking:
            messages.append({
                "role": "system",
                "content": (
                    "Respond with ONLY the requested output. No reasoning "
                    "traces, no <think> tags, no prefaces, no explanations. "
                    "Output the final answer directly."
                ),
            })
        messages.append({"role": "user", "content": content})
        payload = {
            "model": model,
            "temperature": temperature,
            "stream": True,
            "messages": messages,
        }
        if max_tokens is not None and max_tokens > 0:
            payload["max_tokens"] = int(max_tokens)
        # Ollama-specific knobs to disable thinking — 只對 thinking model 套用
        if suppress_thinking:
            payload["think"] = False
            payload.setdefault("options", {})["think"] = False
            if profile.use_chat_template_kwargs:
                payload["chat_template_kwargs"] = {"enable_thinking": False}
        parts: list[str] = []
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_line = line[6:].strip()
                    if data_line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_line)
                    except json.JSONDecodeError:
                        continue
                    try:
                        delta = chunk["choices"][0].get("delta", {}).get("content", "")
                    except (KeyError, IndexError, TypeError):
                        delta = ""
                    if delta:
                        parts.append(delta)
        except httpx.HTTPStatusError:
            raise
        full_content = "".join(parts)
        # Diagnostic log: how many SSE chunks did we get? Helps catch
        # cases where Ollama opens the stream but never sends any deltas
        # (model crashed / refused content / vision encoding hung).
        import logging as _lg
        _llog = _lg.getLogger("app.llm.client")
        _llog.info(
            "vision_query: model=%s stream chunks=%d chars=%d",
            model, len(parts), len(full_content),
        )

        # === Fallback A：streaming 0 chunks → 改用 non-streaming OpenAI-compat 重試 ===
        if not full_content.strip():
            _llog.warning("vision_query stream returned empty, retrying non-stream OpenAI-compat...")
            payload_ns = dict(payload)
            payload_ns["stream"] = False
            try:
                with httpx.Client(timeout=self.timeout) as cli:
                    r2 = cli.post(
                        f"{self.base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload_ns,
                    )
                    r2.raise_for_status()
                    data = r2.json()
                    full_content = (
                        data.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                    ) or ""
                    _llog.info("vision_query: non-stream fallback chars=%d", len(full_content))
            except Exception as e:
                _llog.warning("vision_query non-stream fallback failed: %s", e)

        # === Fallback B：OpenAI-compat 都拿不到 → 改用 Ollama 原生 /api/chat ===
        # Ollama OpenAI-compat 的 /v1/chat/completions 對 vision input 有時不傳
        # delta（GPU 確實有跑、但 SSE 內容為空）。原生 /api/chat 用 messages[].images
        # 欄位處理影像，多數 vision 模型在這條路上正常。
        if not full_content.strip() and "/v1" in self.base_url:
            ollama_base = self.base_url.rsplit("/v1", 1)[0]
            _llog.warning("vision_query OpenAI-compat empty, trying Ollama native /api/chat at %s", ollama_base)
            try:
                # 把第一張 png 轉 base64（Ollama 接 list[str]）
                first_png = imgs[0] if isinstance(imgs, list) else imgs
                img_b64 = base64.b64encode(first_png).decode("ascii")
                native_msgs: list[dict] = []
                if suppress_thinking:
                    native_msgs.append({
                        "role": "system",
                        "content": ("Respond with ONLY the requested output. "
                                    "No reasoning traces, no <think> tags."),
                    })
                native_user_prompt = (
                    profile.no_think_marker + "\n\n" + prompt
                    if suppress_thinking and profile.no_think_marker
                    else prompt
                )
                native_msgs.append({
                    "role": "user",
                    "content": native_user_prompt,
                    "images": [img_b64],
                })
                native_payload = {
                    "model": model,
                    "messages": native_msgs,
                    "stream": False,
                    "options": {"temperature": float(temperature)},
                }
                if suppress_thinking:
                    native_payload["think"] = False
                    native_payload["options"]["think"] = False
                if max_tokens is not None and max_tokens > 0:
                    native_payload["options"]["num_predict"] = int(max_tokens)
                with httpx.Client(timeout=self.timeout) as cli:
                    r3 = cli.post(f"{ollama_base}/api/chat",
                                  headers={"Content-Type": "application/json"},
                                  json=native_payload)
                    r3.raise_for_status()
                    raw_body = r3.text
                    _llog.info(
                        "vision_query Ollama native: HTTP %d body_bytes=%d body_repr=%r",
                        r3.status_code, len(raw_body), raw_body[:500],
                    )
                    data3 = r3.json()
                    msg = data3.get("message", {})
                    full_content = msg.get("content", "") or ""
                    _llog.info(
                        "vision_query: Ollama native /api/chat content_chars=%d msg_keys=%s done_reason=%s",
                        len(full_content), list(msg.keys()), data3.get("done_reason", "?"),
                    )
            except Exception as e:
                _llog.warning("vision_query Ollama native /api/chat also failed: %s", e)

        if not full_content.strip():
            raise LLMError(
                f"Model '{model}' 對影像 input 沒有回傳任何內容（OpenAI-compat 與 Ollama 原生 /api/chat 都拿不到）。"
                f"請確認該模型真的支援 vision — 跑 `ollama show {model}` 看 capability 欄位。"
                f"若無 vision 能力請改用其他 model（qwen2.5vl:7b、minicpm-v、llava 等）。"
            )
        if not parse_json:
            return full_content
        return _extract_json_from_response(full_content)
