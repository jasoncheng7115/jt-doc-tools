"""URL-related sanitizers — kept in a separate module so CodeQL's API graph
recognises them via absolute imports (``from app.core.url_safety import X``).

Without this separation the same logic was inlined in `auth_routes.py` and
`llm_client.py` as private `_-prefixed` functions; CodeQL barriers declared
in `.github/codeql/extensions/jt-sanitizers/jt-sanitizers.model.yml` couldn't
find them because same-file private function references don't traverse the
API graph the way absolute imports do.

Two functions:

- :func:`safe_next` — sanitise post-login redirect target (open-redirect).
  Used by `app/web/auth_routes.py`. Returns "/" on rejection.
- :func:`validate_llm_base_url` — validate admin-supplied LLM base URL
  (SSRF). Used by `app/core/llm_client.py:LLMClient.__init__`. Raises
  :class:`ValueError` on rejection.

Both have CodeQL `barrierModel` rows declared with kinds `url-redirection`
and `request-forgery` respectively.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# url-redirection
# ---------------------------------------------------------------------------


def safe_next(target: str) -> str:
    """Sanitise the post-login redirect target so it stays on this site.

    Reject anything that's:
      - empty / non-string
      - contains :// (cross-origin redirect)
      - starts with // (protocol-relative URL — `//evil.com` becomes https URL)
      - contains \\r \\n \\0 \\\\ (CRLF / null injection / Windows path separator)
      - has a non-empty scheme or netloc after parsing (defence in depth)
    Default to '/' on rejection.
    """
    if not isinstance(target, str) or not target:
        return "/"
    s = target
    if any(c in s for c in ("\r", "\n", "\0", "\\")):
        return "/"
    if "://" in s:
        return "/"
    if s.startswith("//"):
        return "/"
    if not s.startswith("/"):
        return "/"
    try:
        u = urlparse(s)
        if u.scheme or u.netloc:
            return "/"
    except Exception:
        return "/"
    return s


# ---------------------------------------------------------------------------
# request-forgery (SSRF) for admin-supplied LLM URLs
# ---------------------------------------------------------------------------

_ALLOWED_LLM_SCHEMES = ("http", "https")
_BLOCKED_LLM_HOSTS = frozenset({
    "169.254.169.254",
    "100.100.100.200",
    "fd00:ec2::254",
    "metadata.google.internal",
    "metadata.goog",
})


def validate_llm_base_url(url: str) -> str:
    """Validate admin-supplied LLM base URL; raise ValueError on suspicious input.

    Returns the URL with trailing slash stripped. Allows http(s) only;
    blocks well-known cloud metadata hosts (AWS / GCP / Azure / OCI / Alibaba).
    Private LAN IPs (10/8, 172.16/12, 192.168/16, 127/8) ARE allowed because
    internal Ollama on LAN/loopback is the deployment norm.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("LLM base_url must be a non-empty string")
    u = url.strip()
    parsed = urlparse(u)
    if parsed.scheme.lower() not in _ALLOWED_LLM_SCHEMES:
        raise ValueError(
            f"LLM base_url scheme must be http or https, got {parsed.scheme!r}"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("LLM base_url must include a host")
    if host in _BLOCKED_LLM_HOSTS:
        raise ValueError(f"LLM base_url host {host!r} is blocked (cloud metadata)")
    return u.rstrip("/")
