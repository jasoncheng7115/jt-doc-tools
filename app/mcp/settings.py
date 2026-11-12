"""MCP 的設定（`data/mcp_settings.json`）。預設關閉；只有管理員能改。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..config import settings as _app_settings

_lock = threading.Lock()

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    #: 開放給 MCP 的工具（registry 的 MCP 名稱）。None ＝ 用登記表標成「預設開」的那些
    "tools": None,
    #: 每張 Token 每分鐘最多幾次工具呼叫（tools/call）
    "rate_per_minute": 60,
    #: 每張 Token 同時最多幾件在跑
    "max_concurrent": 2,
    #: 傳輸票證綁發出時的來源 IP（代理與 curl 通常在同一台機器）
    "ticket_bind_ip": True,
    #: 站台網址是 HTTP 時也發票證（要管理員明確同意）
    "ticket_allow_http": False,
    #: AI 用印（v1.17.38）：總開關。還要 Token 勾「允許用印」、每顆章開 AI 授權
    "allow_stamp": False,
}

#: 可以調的範圍（超出就夾回來）
_RANGES = {"rate_per_minute": (1, 600), "max_concurrent": (1, 8)}


def _path() -> Path:
    return _app_settings.data_dir / "mcp_settings.json"


def get() -> dict[str, Any]:
    out = dict(DEFAULTS)
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            out.update({k: raw[k] for k in DEFAULTS if k in raw})
    except (OSError, ValueError):
        pass
    out["enabled"] = out.get("enabled") is True
    out["ticket_bind_ip"] = out.get("ticket_bind_ip") is not False
    out["ticket_allow_http"] = out.get("ticket_allow_http") is True
    out["allow_stamp"] = out.get("allow_stamp") is True
    for k, (lo, hi) in _RANGES.items():
        try:
            out[k] = max(lo, min(hi, int(out.get(k))))
        except (TypeError, ValueError):
            out[k] = DEFAULTS[k]
    tools = out.get("tools")
    out["tools"] = [str(t) for t in tools] if isinstance(tools, list) else None
    return out


def save(changes: dict[str, Any]) -> dict[str, Any]:
    """只收認得的欄位；工具清單只留登記表裡有的名稱。回存好之後的設定。"""
    from . import registry
    with _lock:
        cur = get()
        for k in ("enabled", "ticket_bind_ip", "ticket_allow_http", "allow_stamp"):
            if k in changes:
                cur[k] = changes[k] is True
        for k in _RANGES:
            if k in changes:
                cur[k] = changes[k]
        if "tools" in changes:
            want = changes["tools"]
            if want is None:
                cur["tools"] = None
            elif isinstance(want, list):
                cur["tools"] = [t for t in registry.NAMES if t in {str(x) for x in want}]
        from ..core import atomic_json
        _app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        atomic_json.write_json(_path(), cur)
    return get()


def enabled_tools() -> list[str]:
    """管理員開放的工具（MCP 名稱，照登記表順序）。"""
    from . import registry
    want = get()["tools"]
    # AI 用印的工具不在這份清單裡（另有開關與條件，見 server.visible_tools）
    if want is None:
        return [t.name for t in registry.TOOLS if t.default_on and not t.stamp]
    return [n for n in registry.NAMES if n in set(want) and not registry.BY_NAME[n].stamp]
