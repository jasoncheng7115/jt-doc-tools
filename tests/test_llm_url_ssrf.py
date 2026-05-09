"""SSRF defence — admin-supplied LLM base URL must reject suspicious schemes
and cloud-metadata hosts. Constructor and admin save endpoint both validate.

Closes CodeQL `Partial server-side request forgery` alert in
``app/core/llm_client.py:_validate_llm_base_url`` (was line 157)."""
from __future__ import annotations

import pytest

from app.core.llm_client import LLMClient, _validate_llm_base_url


# --- happy path ---------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1",
    "https://ollama.example.com/v1",
    "http://127.0.0.1:1234/v1",
    "http://192.168.1.50:11434/v1",
    "http://10.0.0.5:8000/v1",
    "https://dgx-spark.lan:11434/v1",
])
def test_valid_urls_accepted(url):
    out = _validate_llm_base_url(url)
    assert out == url.rstrip("/")
    LLMClient(base_url=url)


def test_trailing_slash_stripped():
    assert _validate_llm_base_url("http://localhost/v1/") == "http://localhost/v1"


# --- attack inputs ------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file://localhost/etc/passwd",
    "gopher://attacker.example.com/",
    "dict://attacker.example.com:11211/",
    "ftp://example.com/",
    "ldap://example.com/",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
])
def test_non_http_schemes_rejected(url):
    with pytest.raises(ValueError, match="scheme"):
        _validate_llm_base_url(url)
    with pytest.raises(ValueError):
        LLMClient(base_url=url)


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",       # AWS / GCP / Azure / OCI / Alibaba
    "http://169.254.169.254:80/computeMetadata/v1/",
    "http://100.100.100.200/latest/meta-data/",       # Alibaba
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://metadata.goog/",
])
def test_cloud_metadata_hosts_rejected(url):
    with pytest.raises(ValueError, match="(blocked|metadata)"):
        _validate_llm_base_url(url)


@pytest.mark.parametrize("url", [
    "",
    "   ",
    None,
    123,
    [],
])
def test_empty_or_non_string_rejected(url):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        _validate_llm_base_url(url)


def test_url_without_host_rejected():
    with pytest.raises(ValueError):
        _validate_llm_base_url("http:///v1")


# --- admin endpoint enforces too --------------------------------------

def test_admin_save_rejects_metadata_host():
    """API endpoint /admin/api/llm/settings must reject SSRF host before
    persisting to disk — defence in depth on top of constructor check."""
    from fastapi.testclient import TestClient
    import os
    os.environ["AUTH_BACKEND"] = "off"
    from app.main import app
    client = TestClient(app)
    r = client.post(
        "/admin/api/llm/settings",
        json={"base_url": "http://169.254.169.254/v1", "enabled": True},
    )
    # Even when auth is off, admin page is reachable; expect 400 with explanation.
    assert r.status_code == 400, r.text
    body = r.json()
    assert body.get("ok") is False
    assert "block" in (body.get("error") or "").lower() or "metadata" in (body.get("error") or "").lower()
