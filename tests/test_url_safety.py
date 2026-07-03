"""safe_next open-redirect sanitizer — including the encoded-slash hardening."""
from __future__ import annotations

import pytest

from app.core.url_safety import safe_next


@pytest.mark.parametrize("good", ["/", "/tools/pdf-merge/", "/admin/users?x=1", "/a/b/c"])
def test_safe_next_allows_internal(good):
    assert safe_next(good) == good


@pytest.mark.parametrize("bad", [
    "https://evil.com", "//evil.com", "///evil.com", "http:evil.com",
    "/\\evil.com", "\\/\\/evil.com", "  //evil.com", "",
    "/%2f%2fevil.com",   # encoded protocol-relative — decode-once hardening
    "/%5cevil.com",      # encoded backslash
    "/%2F%2Fevil.com",
])
def test_safe_next_rejects_external(bad):
    assert safe_next(bad) == "/"
