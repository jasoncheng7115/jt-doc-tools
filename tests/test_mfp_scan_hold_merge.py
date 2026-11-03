"""從事務機掃描：先暫存、預覽，接著掃多次，最後合成一份多頁 PDF。

使用者 2026-11-03：「按下開始掃描時不要一次存一個檔，掃完就先預覽（但也先暫存）；然後可以接著掃多張後
看到多張，尤其是平台掃描；等最後一張 ok 再有一個按鈕存成一個多頁的 pdf 檔」。

驗：
* `hold` 的掃描不進工作區、標「暫存」，本人看得到預覽、別人看不到（預覽、暫存清單、合併都拿不到）；
* 合併照給的頁序（可以跳過、調換）合成一份 PDF 存進工作區；來源暫存一起清掉（檔案、預覽都沒了）；
* 工作區停用時合併結果留在暫存區給本人下載；
* 清除暫存；暫存次數有上限；暫存中的紀錄不會被「每人最多 50 筆」擠掉；
* 存成 JPEG 不暫存（照舊直接存）。
"""
from __future__ import annotations

import io
from pathlib import Path

import fitz
import pytest

from app.core import mfp_escl, mfp_scan, mfp_store
from tests.test_mfp_fetch_tool import fake_probe  # noqa: F401
from tests.test_mfp_shared_and_scan import (  # noqa: F401
    API, _own_device, _wait_scan, escl_probe, people,
)


@pytest.fixture
def labeled_scan(monkeypatch):
    """每次掃描產出 2 頁，頁面上寫著「第幾次掃描-第幾頁」，合併後讀得出順序。"""
    n = {"i": 0}

    def scan(host, ip, scheme, port, caps, opts, workdir, on_progress=None, is_cancelled=None):
        n["i"] += 1
        k = n["i"]
        Path(workdir).mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        for p in (1, 2):
            pg = doc.new_page(width=595, height=842)
            pg.insert_text((72, 100), f"S{k}-P{p}", fontsize=40)
        out = Path(workdir) / "scan.pdf"
        doc.save(str(out))
        doc.close()
        return {"path": out, "pages": 2, "bytes": out.stat().st_size,
                "settings": {"source": "platen", "resolution": 300}, "tls_verified": None}

    monkeypatch.setattr(mfp_escl, "scan", scan)
    return n


@pytest.fixture
def ws_on(monkeypatch):
    from app.core import workspace
    monkeypatch.setattr(workspace, "is_enabled", lambda: True)
    return workspace


def _held_scan(c, cid, **extra):
    r = c.post(f"{API}/connections/{cid}/scan", json=dict({"source": "platen", "hold": True}, **extra))
    assert r.status_code == 202, r.text
    return _wait_scan(c, r.json()["scan"]["id"])


def _texts(pdf: bytes) -> list[str]:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        return [p.get_text().strip() for p in d]


def test_held_scan_is_not_saved_and_only_the_owner_can_preview(people, labeled_scan, ws_on):
    p = people
    cid = _own_device(p["a"])
    s = _held_scan(p["a"], cid)
    assert s["status"] == "done" and s["workspace_note"] == "HELD", s
    assert not s["workspace_file_id"], "先暫存：不可以已經存進工作區"
    assert s["has_file"] and "暫存" in s["workspace_message"]
    held = p["a"].get(f"{API}/scans/held", params={"conn_id": cid}).json()["scans"]
    assert [h["id"] for h in held] == [s["id"]]
    for page in (0, 1):
        r = p["a"].get(f"{API}/scans/{s['id']}/preview/{page}")
        assert r.status_code == 200 and r.content.startswith(b"\x89PNG"), page
        assert "no-store" in r.headers.get("cache-control", "")
    assert p["a"].get(f"{API}/scans/{s['id']}/preview/2").status_code == 404, "沒有第 3 頁"
    assert p["a"].get(f"{API}/scans/{s['id']}/preview/0", params={"w": 5000}).status_code == 404
    # 別人：預覽、暫存清單、合併都拿不到
    assert p["b"].get(f"{API}/scans/{s['id']}/preview/0").status_code == 404
    assert p["b"].get(f"{API}/scans/held").json()["scans"] == []
    r = p["b"].post(f"{API}/scans/merge", json={"pages": [[s["id"], 0]]})
    assert r.status_code == 404 and r.json()["code"] == "MERGE_SOURCE_GONE"
    assert p["a"].get(f"{API}/scans/{s['id']}/preview/0").status_code == 200, "別人試過之後我的還在"


def test_merge_follows_the_page_order_and_cleans_up(people, labeled_scan, ws_on):
    p = people
    cid = _own_device(p["a"])
    s1 = _held_scan(p["a"], cid)
    s2 = _held_scan(p["a"], cid)
    p["a"].get(f"{API}/scans/{s1['id']}/preview/0")              # 產生過預覽快取，也要一起清掉
    order = [[s2["id"], 1], [s1["id"], 0], [s2["id"], 0]]        # 調換順序、刪掉 S1-P2
    r = p["a"].post(f"{API}/scans/merge", json={"pages": order, "file_name": "../合約"})
    assert r.status_code == 200, r.text
    m = r.json()["scan"]
    assert m["status"] == "done" and m["pages"] == 3 and m["workspace_file_id"], m
    assert m["workspace_note"] == "MERGED" and "工作區" in m["workspace_message"]
    assert m["file_name"] == "合約.pdf", "檔名不可以帶路徑"
    meta = ws_on.find_for_key(f"u{p['ua']}", m["workspace_file_id"])
    data = (ws_on._root() / f"u{p['ua']}" / m["workspace_file_id"] / f"file{meta['ext']}").read_bytes()
    assert _texts(data) == ["S2-P2", "S1-P1", "S2-P1"]
    # 來源暫存清掉：不再列在暫存區、預覽與檔案都沒了
    assert p["a"].get(f"{API}/scans/held").json()["scans"] == []
    for s in (s1, s2):
        st = p["a"].get(f"{API}/scans/{s['id']}").json()["scan"]
        assert st["workspace_note"] == "MERGED_SOURCE" and st["has_file"] is False, st
        assert p["a"].get(f"{API}/scans/{s['id']}/preview/0").status_code == 404
        assert not (mfp_scan._work_root() / s["id"]).exists()
    # 再合併一次（來源已經用掉）
    r = p["a"].post(f"{API}/scans/merge", json={"pages": order})
    assert r.status_code == 404 and r.json()["code"] == "MERGE_SOURCE_GONE"


def test_merge_without_a_workspace_waits_for_download(people, labeled_scan, monkeypatch):
    from app.core import workspace
    monkeypatch.setattr(workspace, "is_enabled", lambda: False)
    p = people
    cid = _own_device(p["a"])
    s = _held_scan(p["a"], cid)
    m = p["a"].post(f"{API}/scans/merge", json={"pages": [[s["id"], 1], [s["id"], 0]]}).json()["scan"]
    assert m["status"] == "done" and not m["workspace_file_id"] and m["has_file"], m
    r = p["a"].get(f"{API}/scans/{m['id']}/download")
    assert r.status_code == 200 and _texts(r.content) == ["S1-P2", "S1-P1"]


def test_bad_merge_requests(people, labeled_scan, ws_on):
    p = people
    cid = _own_device(p["a"])
    s = _held_scan(p["a"], cid)
    for body, code in (({"pages": []}, "MERGE_EMPTY"), ({"pages": "x"}, "MERGE_EMPTY"),
                       ({"pages": [[s["id"], 9]]}, "MERGE_SOURCE_GONE"),
                       ({"pages": [["../../etc", 0]]}, "MERGE_SOURCE_GONE"),
                       ({"pages": [[s["id"], True]]}, "MERGE_SOURCE_GONE")):
        r = p["a"].post(f"{API}/scans/merge", json=body)
        assert r.status_code in (400, 404) and r.json()["code"] == code, (body, r.text)
    big = [[s["id"], 0]] * (mfp_scan.MAX_MERGE_PAGES + 1)
    assert p["a"].post(f"{API}/scans/merge", json={"pages": big}).json()["code"] == "MERGE_TOO_MANY"
    assert p["a"].get(f"{API}/scans/{s['id']}/preview/0").status_code == 200, "失敗的合併不可以動到暫存"


def test_discard_clears_held_scans(people, labeled_scan, ws_on):
    p = people
    cid = _own_device(p["a"])
    s = _held_scan(p["a"], cid)
    assert p["b"].post(f"{API}/scans/discard", json={"scans": [s["id"]]}).json()["discarded"] == 0
    assert p["a"].post(f"{API}/scans/discard", json={"scans": [s["id"], "nope"]}).json()["discarded"] == 1
    assert p["a"].get(f"{API}/scans/held").json()["scans"] == []
    assert not (mfp_scan._work_root() / s["id"]).exists()
    assert p["a"].get(f"{API}/scans/{s['id']}").json()["scan"]["workspace_note"] == "DISCARDED"


def test_jpeg_output_is_saved_directly(people, monkeypatch, ws_on):
    """存成 JPEG 照舊每頁直接存（先暫存只用在 PDF）。"""
    p = people
    from PIL import Image

    def scan(host, ip, scheme, port, caps, opts, workdir, on_progress=None, is_cancelled=None):
        Path(workdir).mkdir(parents=True, exist_ok=True)
        out = Path(workdir) / "page0001.jpg"
        Image.new("RGB", (200, 280), "white").save(out, "JPEG")
        return {"files": [{"path": out, "kind": "jpeg", "pages": 1}], "pages": 1,
                "bytes": out.stat().st_size, "settings": {"output": "jpeg"}, "tls_verified": None}

    monkeypatch.setattr(mfp_escl, "scan", scan)
    monkeypatch.setattr(mfp_escl, "build_settings", lambda caps, opts: ("<x/>", {"output": "jpeg"}))
    cid = _own_device(p["a"])
    s = _held_scan(p["a"], cid, format="jpeg")
    assert s["workspace_note"] != "HELD" and s["workspace_file_id"], s
    assert "hold" not in (s["settings"] or {})


def test_too_many_held_scans_are_refused(people, labeled_scan, ws_on, monkeypatch):
    monkeypatch.setattr(mfp_scan, "MAX_HELD", 1)
    p = people
    cid = _own_device(p["a"])
    _held_scan(p["a"], cid)
    r = p["a"].post(f"{API}/connections/{cid}/scan", json={"source": "platen", "hold": True})
    assert r.status_code == 400 and r.json()["code"] == "HOLD_TOO_MANY", r.text


def test_held_scans_are_not_pushed_out_by_the_record_limit(people, labeled_scan, ws_on, monkeypatch):
    monkeypatch.setattr(mfp_store, "MAX_SCANS_PER_OWNER", 2)
    p = people
    cid = _own_device(p["a"])
    s = _held_scan(p["a"], cid)
    for _ in range(3):
        r = p["a"].post(f"{API}/connections/{cid}/scan", json={"source": "platen"})
        _wait_scan(p["a"], r.json()["scan"]["id"])
    held = p["a"].get(f"{API}/scans/held").json()["scans"]
    assert [h["id"] for h in held] == [s["id"]], "暫存中的掃描不可以被筆數上限擠掉"
    assert p["a"].get(f"{API}/scans/{s['id']}/preview/0").status_code == 200
