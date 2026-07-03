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
    # Defence in depth: also re-check the once-percent-decoded form so encoded
    # slash/backslash tricks (`/%2f%2fevil.com` → `///evil.com`, `/%5cevil`) and
    # encoded CR/LF/NUL can't slip a protocol-relative / injection past the raw
    # checks below. (A single leading '/' Location is same-origin in browsers,
    # so these weren't exploitable, but we reject them anyway.)
    from urllib.parse import unquote
    for candidate in (s, unquote(s)):
        if any(c in candidate for c in ("\r", "\n", "\0", "\\")):
            return "/"
        if "://" in candidate:
            return "/"
        if candidate.startswith("//"):
            return "/"
        if not candidate.startswith("/"):
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


import re as _re

# Hostname / IP-literal allowlist (letters / digits / . - : [ ]).
_HOST_RE = _re.compile(r"[A-Za-z0-9.\-:\[\]]+")


def safe_remote_base_url(url: str) -> str:
    """Validate an admin-supplied internal-service URL (e.g. remote OCR server)
    and return ONLY a clean ``scheme://host[:port]`` base — no user-controlled
    path / query / fragment / credentials. Raises :class:`ValueError` on
    rejection. The caller appends fixed paths (``/healthz`` …), so the returned
    value breaks the SSRF taint flow from the original URL.

    Blocks well-known cloud-metadata hosts; private LAN IPs are allowed because
    internal services on the LAN / loopback are the deployment norm. Declared as
    a ``request-forgery`` barrier in the CodeQL sanitizer model.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string")
    parsed = urlparse(url.strip())
    if parsed.scheme not in _ALLOWED_LLM_SCHEMES:
        raise ValueError("only http / https URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URL must not embed credentials")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("URL is missing a hostname")
    if host in _BLOCKED_LLM_HOSTS or host == "metadata":
        raise ValueError("connecting to cloud-metadata endpoints is blocked")
    if not _HOST_RE.fullmatch(host):
        raise ValueError("URL hostname contains illegal characters")
    port = parsed.port  # raises ValueError on out-of-range automatically
    clean = f"{parsed.scheme}://{host}"
    if port is not None:
        if not (1 <= port <= 65535):
            raise ValueError("port out of range")
        clean += f":{port}"
    return clean
