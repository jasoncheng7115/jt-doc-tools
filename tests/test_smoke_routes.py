"""Smoke tests: every public page renders 200, no 500s.

Tool list is **discovered from the registry** so newly-added tools are
covered automatically — the missing-tool blind spot caught us on
v1.1.8 (pdf-extract-text 500'd because of a bad llm_settings import that
no test exercised the index page for).
"""
import pytest

import app.main as app_main


# Settings / admin pages that don't require auth (auth_off default for tests).
ADMIN_PAGES = [
    "/admin/assets",
    "/admin/profile",
    "/admin/synonyms",
    "/admin/templates",
    "/admin/conversion",
    "/admin/llm-settings",
    "/admin/api-tokens",
    "/admin/fonts",
    "/admin/auth-settings",
    "/admin/retention",
]

PUBLIC_PAGES = ["/", "/healthz", "/whoami"]

# Discover every registered tool from the live registry. Each tool's index
# page lives at /tools/<id>/ — they all need to render without 500.
TOOL_PATHS = [f"/tools/{t.metadata.id}/" for t in app_main.tools]


@pytest.mark.parametrize("path", PUBLIC_PAGES + ADMIN_PAGES)
def test_public_and_admin_page_renders(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}\n{r.text[:300]}"


@pytest.mark.parametrize("path", TOOL_PATHS)
def test_every_tool_index_renders(client, path):
    """Every /tools/<id>/ index must render without 500 in default state.

    This is what caught (would have caught) v1.1.8's broken
    `llm_settings.is_enabled()` call in pdf-extract-text — that view threw
    AttributeError on first hit because the import was the module, not
    the manager instance."""
    r = client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}\n{r.text[:500]}"


def test_tool_path_list_is_not_empty():
    """Sanity: if registry returns 0 tools, the parametrized test above
    silently passes 0 cases. Guard against that."""
    assert len(TOOL_PATHS) >= 5


def test_pdf_fill_with_cid_query_does_not_500(client):
    """Regression: pdf_fill router uses Optional[str] under
    `from __future__ import annotations`; a missing import would 500."""
    r = client.get("/tools/pdf-fill/?cid=does-not-exist")
    assert r.status_code in (200, 404), r.text[:300]
