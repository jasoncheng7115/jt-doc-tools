"""Open-redirect regression — closes CodeQL alerts #14 / #15
(`URL redirection from remote source` in app/web/auth_routes.py).

`safe_next` is the post-login redirect sanitiser. Anything that could
let an attacker craft a login URL like `/login?next=//evil.com` and have
the user land on an attacker-controlled site after successful login must
be rejected with default fallback '/'."""
from __future__ import annotations

import pytest

from app.core.url_safety import safe_next


@pytest.mark.parametrize("target", [
    "/",
    "/dashboard",
    "/tools/pdf-fill",
    "/admin/users",
    "/foo/bar?q=1",
    "/path#frag",
    "/with%20space",
])
def test_safe_relative_paths_pass_through(target):
    assert safe_next(target) == target


@pytest.mark.parametrize("target", [
    None, "", "   ",
    # Cross-origin
    "http://evil.com", "https://evil.com/path",
    "//evil.com", "//evil.com/path",
    "ftp://evil.com",
    # Protocol-relative variants
    "/\\\\evil.com",          # backslash bypass
    "//\\evil.com",
    # CRLF / null injection
    "/path\r\nLocation: http://evil.com",
    "/path\nX-Header: x",
    "/path\0",
    # Non-leading-slash
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "evil.com",
    "//evil.com\\foo",
    # Whitespace-prefix (some browsers tolerate)
    " //evil.com",
    "\t/foo",
])
def test_attack_inputs_rejected(target):
    """Anything risky → '/' fallback."""
    assert safe_next(target) == "/"


def test_non_string_rejected():
    assert safe_next(123) == "/"  # type: ignore[arg-type]
    assert safe_next(["/foo"]) == "/"  # type: ignore[arg-type]
