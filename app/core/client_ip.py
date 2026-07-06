"""Canonical end-user IP resolution for AUDIT / HISTORY / DISPLAY.

Single source of truth so every place that records "who did this from where"
agrees. Honours ``X-Forwarded-For`` (left-most hop = original client) set by a
trusted reverse proxy, falling back to the transport peer.

Why this module exists: uvicorn is started with ``proxy_headers=False`` (so the
proxy-SSO trust decision can rely on the *real* transport peer, not a spoofable
header — see ``proxy_sso``). That means ``request.client.host`` is the nginx
peer (``127.0.0.1``) under a reverse proxy, NOT the workstation. Audit/history
must therefore read XFF themselves. Regression history: v1.12.61 turned off
proxy_headers and every site still using ``request.client.host`` started logging
``127.0.0.1`` (fixed in v1.12.65 by routing them through here).

SECURITY: never use this for a trust/authorisation decision. When the app is
reachable without going through the proxy, a client can forge X-Forwarded-For.
For trust decisions use the raw ``request.client.host`` (``proxy_sso._client_ip``).
Operators must configure the reverse proxy to strip inbound XFF and set its own.
"""
from __future__ import annotations


def real_client_ip(request) -> str:
    """Best-effort end-user IP for audit / history / display (max 64 chars)."""
    try:
        xff = request.headers.get("X-Forwarded-For", "") or ""
    except Exception:
        xff = ""
    if xff:
        # left-most is the original client (per convention)
        return xff.split(",", 1)[0].strip()[:64]
    try:
        return (request.client.host if getattr(request, "client", None) else "")[:64]
    except Exception:
        return ""
