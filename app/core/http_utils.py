"""HTTP helpers — small, dependency-free utilities for FastAPI/Starlette.

Currently just :func:`content_disposition` for handling CJK filenames in
attachment headers.
"""
from __future__ import annotations

import re
from urllib.parse import quote


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Build a `Content-Disposition` header value safe for any filename.

    HTTP headers are encoded as latin-1 by Starlette, so a raw CJK filename
    in ``filename="..."`` raises UnicodeEncodeError. RFC 5987 lets us add
    ``filename*=UTF-8''<percent-encoded>`` which modern browsers honour.

    We emit BOTH parameters: an ASCII-safe ``filename=`` for ancient clients
    plus the percent-encoded ``filename*=`` for the rest.

    Starlette's :class:`FileResponse(filename=...)` already does this dance
    internally — only use this helper when manually constructing headers
    for :class:`Response` / :class:`StreamingResponse`.
    """
    ascii_safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename).strip("_") or "download"
    encoded = quote(filename, safe="")
    return f'{disposition}; filename="{ascii_safe}"; filename*=UTF-8\'\'{encoded}'
