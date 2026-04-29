"""Tests for the new 文字差異比對 tool — paste-text variant of doc-diff."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_index_renders(client):
    r = client.get("/tools/text-diff/")
    assert r.status_code == 200
    assert "文字差異比對" in r.text
    # Make sure it's truly paste-mode, not file upload
    assert "<textarea" in r.text and "td-paste" in r.text


def test_compare_simple_diff(client):
    r = client.post(
        "/tools/text-diff/compare",
        json={
            "text_a": "alpha\nbeta\ngamma",
            "text_b": "alpha\nBETA-changed\ngamma\ndelta",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    t = body["totals"]
    # one inserted line + one changed line, removed 0
    assert t["added"] == 1 and t["changed"] == 1 and t["removed"] == 0
    # char-level deltas should also be present and non-zero
    assert t["chars_a"] > 0 and t["chars_b"] > 0
    assert t["chars_added"] >= 1 and t["chars_changed"] >= 1
    # Wrapped as one virtual page
    assert body["page_count_a"] == 1 and body["page_count_b"] == 1
    assert len(body["pages"]) == 1


def test_compare_identical_text_zero_diff(client):
    r = client.post(
        "/tools/text-diff/compare",
        json={"text_a": "same text", "text_b": "same text"},
    )
    assert r.status_code == 200
    t = r.json()["totals"]
    assert t["added"] == 0 and t["removed"] == 0 and t["changed"] == 0
    assert t["chars_added"] == 0 and t["chars_removed"] == 0 and t["chars_changed"] == 0


def test_compare_one_side_empty(client):
    r = client.post(
        "/tools/text-diff/compare",
        json={"text_a": "", "text_b": "first line\nsecond line"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["page_count_a"] == 0 and body["page_count_b"] == 1
    t = body["totals"]
    assert t["added"] == 2 and t["chars_added"] >= 11


def test_compare_rejects_non_string(client):
    r = client.post(
        "/tools/text-diff/compare",
        json={"text_a": 123, "text_b": "ok"},
    )
    assert r.status_code == 400


def test_compare_rejects_oversize(client):
    big = "x" * (2 * 1024 * 1024)  # 2 MiB
    r = client.post(
        "/tools/text-diff/compare",
        json={"text_a": big, "text_b": "small"},
    )
    assert r.status_code == 413


def test_api_alias_works(client):
    r = client.post(
        "/tools/text-diff/api/text-diff",
        json={"text_a": "a", "text_b": "b"},
    )
    assert r.status_code == 200
    assert r.json()["totals"]["changed"] == 1


def test_compare_handles_crlf_and_lf_uniformly(client):
    """Mixed line endings shouldn't blow up the line count."""
    r = client.post(
        "/tools/text-diff/compare",
        json={"text_a": "one\r\ntwo\r\nthree", "text_b": "one\ntwo\nthree"},
    )
    assert r.status_code == 200
    t = r.json()["totals"]
    # After universal-newline split, both sides should match exactly
    assert t["added"] == 0 and t["removed"] == 0 and t["changed"] == 0
