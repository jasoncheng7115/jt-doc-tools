"""用印申請：一件申請多份用印文件（分開蓋、分開下載；使用者 2026-11-09）。

舊資料只有一份 `document.pdf`，要照讀；舊的單一文件 API 要照用。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import time
import zipfile

import fitz
import pytest

os.environ.setdefault("JTDT_CSRF_DISABLE", "1")

from app.core import seal_flow as F  # noqa: E402
from app.core import seal_store as S  # noqa: E402
from tests.test_seal_flow import API, _act, _pdf, world  # noqa: F401,E402
from tests.test_seal_stamp import sent  # noqa: F401,E402


def _add(w, cid, pages=2, name="報價單.pdf"):
    r = w["c"]["A"].post(f"{API}/cases/{cid}/documents", files={"file": (name, _pdf(pages), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["case"]


def _draft2(w) -> tuple[str, list[str]]:
    """兩份文件（2 頁、3 頁），章標在第二份。"""
    a = w["c"]["A"]
    cid = a.post(f"{API}/cases", json={"flow_id": w["flow"]["id"]}).json()["case"]["id"]
    _add(w, cid, 2, "報價單.pdf")
    case = _add(w, cid, 3, "合約.pdf")
    ids = [d["id"] for d in case["docs"]]
    r = a.put(f"{API}/cases/{cid}", json={"subject": "兩份文件", "purpose": "報價與合約", "assets": [w["stamp"].id],
                                          "placements": [{"asset_id": w["stamp"].id, "doc": ids[1], "page": 3,
                                                          "position": "br"}]})
    assert r.status_code == 200, r.text
    return cid, ids


def _approve_all(w, cid):
    assert w["c"]["A"].post(f"{API}/cases/{cid}/submit").status_code == 200
    for who in ("R1", "R2", "R3"):
        assert _act(w, who, cid, "approve").status_code == 200
    assert S.get_case(cid)["state"] == "awaiting_stamp"


def _plc(w, doc, page=1):
    return {"asset_id": w["stamp"].id, "doc": doc, "page": page, "x_mm": 150, "y_mm": 240, "width_mm": 40,
            "height_mm": 40, "rotation_deg": 0}


def _wait_done(cid):
    for _ in range(150):
        if S.get_case(cid)["state"] in ("done", "stamp_failed"):
            break
        time.sleep(0.1)
    return S.get_case(cid)


def test_two_documents_are_listed_frozen_and_placed(world):
    w = world
    cid, ids = _draft2(w)
    case = w["c"]["A"].get(f"{API}/cases/{cid}").json()["case"]
    assert [d["name"] for d in case["docs"]] == ["報價單.pdf", "合約.pdf"]
    assert [d["pages"] for d in case["docs"]] == [2, 3]
    assert case["doc"]["id"] == ids[0], "舊欄位 doc 仍是第一份"
    assert case["placements"][0]["doc"] == ids[1] and case["placements"][0]["page"] == 3
    # 頁碼要在那一份的範圍內（第一份只有 2 頁）
    r = w["c"]["A"].put(f"{API}/cases/{cid}", json={"placements": [
        {"asset_id": w["stamp"].id, "doc": ids[0], "page": 3, "position": "br"}]})
    assert r.status_code == 400 and r.json()["code"] == "BAD_PLACEMENT"
    _approve_all(w, cid)
    v = S.get_version(cid, 1)
    assert [d["id"] for d in v["documents"]] == ids and all(len(d["sha256"]) == 64 for d in v["documents"])
    assert v["doc_name"] == "報價單.pdf" and v["doc_pages"] == 2, "舊欄位記第一份"
    # 每一份的預覽與下載
    for d, n in zip(ids, (2, 3)):
        assert w["c"]["R1"].get(f"{API}/cases/{cid}/documents/{d}/pages/{n}").status_code == 200
        assert w["c"]["R1"].get(f"{API}/cases/{cid}/documents/{d}/pages/{n + 1}").status_code == 404
        r = w["c"]["R1"].get(f"{API}/cases/{cid}/documents/{d}")
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert w["c"]["X"].get(f"{API}/cases/{cid}/documents/{ids[0]}").status_code == 404, "無關的人拿不到"
    assert w["c"]["R1"].get(f"{API}/cases/{cid}/documents/nope/pages/1").status_code == 404
    assert w["c"]["R1"].get(f"{API}/cases/{cid}/documents/..%2f..%2fx").status_code == 404


def test_every_document_must_be_stamped_and_results_come_separately(world, sent):
    w = world
    cid, ids = _draft2(w)
    _approve_all(w, cid)
    c = w["c"]["C"]
    r = c.post(f"{API}/cases/{cid}/stamp", json={"placements": [_plc(w, ids[1], 3)], "exec_id": "b" * 32})
    assert r.status_code == 400 and r.json()["code"] == "DOC_NOT_STAMPED", "兩份都要蓋"
    r = c.post(f"{API}/cases/{cid}/stamp", json={"placements": [_plc(w, ids[0], 1), _plc(w, ids[1], 3)],
                                                  "exec_id": "c" * 32})
    assert r.status_code == 200, r.text
    case = _wait_done(cid)
    assert case["state"] == "done", case.get("stamp_error")
    assert set(json.loads(case["stamped_sha256"])) == set(ids), "多份時存每一份的雜湊"
    files = F.result_files({"auditor": False}, case)
    assert [f["name"] for f in files] == ["報價單_用印.pdf", "合約_用印.pdf"]
    for f, page in zip(files, (0, 2)):
        with fitz.open(str(f["path"])) as d:
            pix = d[page].get_pixmap(dpi=36)
            red = sum(1 for y in range(pix.height) for x in range(pix.width)
                      if (lambda c: c[0] > 150 and c[1] < 90 and c[2] < 90)(pix.pixel(x, y)))
            assert red > 30, f"{f['name']} 第 {page + 1} 頁沒有章"
    a = w["c"]["A"]
    r = a.get(f"{API}/cases/{cid}/result")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == sorted(["報價單_用印.pdf", "合約_用印.pdf"])
    r = a.get(f"{API}/cases/{cid}/result", params={"doc": ids[1]})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert a.get(f"{API}/cases/{cid}/result", params={"doc": "nope"}).status_code == 404
    assert w["c"]["X"].get(f"{API}/cases/{cid}/result").status_code == 404


def test_changing_any_one_document_blocks_stamping(world):
    w = world
    cid, ids = _draft2(w)
    _approve_all(w, cid)
    F.documents(S.get_case(cid), 1)[1]["path"].write_bytes(_pdf(3))
    r = w["c"]["C"].post(f"{API}/cases/{cid}/stamp", json={"placements": [_plc(w, ids[0]), _plc(w, ids[1])],
                                                            "exec_id": "d" * 32})
    assert r.status_code == 409 and r.json()["code"] == "DOC_CHANGED"


def test_removing_a_document_drops_its_placements(world):
    w = world
    cid, ids = _draft2(w)
    a = w["c"]["A"]
    r = a.delete(f"{API}/cases/{cid}/documents/{ids[1]}")
    assert r.status_code == 200
    case = r.json()["case"]
    assert [d["id"] for d in case["docs"]] == [ids[0]] and case["placements"] == []
    assert w["c"]["R1"].delete(f"{API}/cases/{cid}/documents/{ids[0]}").status_code in (403, 404)
    assert a.delete(f"{API}/cases/{cid}/documents/nope").status_code == 404


def test_document_limit(world, monkeypatch):
    w = world
    monkeypatch.setattr(F, "MAX_DOCS", 2)
    cid, _ids = _draft2(w)
    r = w["c"]["A"].post(f"{API}/cases/{cid}/documents", files={"file": ("x.pdf", _pdf(1), "application/pdf")})
    assert r.status_code == 400 and r.json()["code"] == "TOO_MANY_DOCS"


def test_legacy_single_document_case_still_works(world, sent):
    """v1.17.28～34 的案件：編輯中那份只有 document.pdf、沒有 docs 清單。照讀、照蓋、照下載；
    之後再加一份時，原本那份保留成第一份。"""
    w = world
    a = w["c"]["A"]
    cid = a.post(f"{API}/cases", json={"flow_id": w["flow"]["id"]}).json()["case"]["id"]
    case = S.get_case(cid)
    pd = F.pending_dir(case)
    pd.mkdir(parents=True, exist_ok=True)
    (pd / F.DOC_NAME).write_bytes(_pdf(2))
    F._write_meta(pd, {"doc_name": "舊文件.pdf", "pages": 2, "page_sizes": [[210, 297], [210, 297]]})
    case = a.get(f"{API}/cases/{cid}").json()["case"]
    assert [(d["id"], d["name"]) for d in case["docs"]] == [("main", "舊文件.pdf")]
    assert a.get(f"{API}/cases/{cid}/pages/1").status_code == 200, "舊的預覽網址照用"
    # 舊格式的位置（沒有 doc）照收，指向第一份
    r = a.put(f"{API}/cases/{cid}", json={"subject": "舊", "purpose": "舊", "assets": [w["stamp"].id],
                                          "placements": [{"asset_id": w["stamp"].id, "page": 2, "position": "br"}]})
    assert r.status_code == 200 and r.json()["case"]["placements"][0]["doc"] == "main"
    case = _add(w, cid, 1, "新文件.pdf")
    assert [d["name"] for d in case["docs"]] == ["舊文件.pdf", "新文件.pdf"]
    _approve_all(w, cid)
    ids = [d["id"] for d in F.version_docs(S.get_version(cid, 1))]
    assert ids[0] == "main"
    r = w["c"]["C"].post(f"{API}/cases/{cid}/stamp", json={"placements": [_plc(w, "main", 2), _plc(w, ids[1], 1)],
                                                            "exec_id": "e" * 32})
    assert r.status_code == 200, r.text
    assert _wait_done(cid)["state"] == "done"


def test_legacy_done_case_result_is_still_downloadable(world, sent):
    """舊版完成的案件：成果是 `stamped.pdf`、版本沒有 documents 清單。"""
    w = world
    a = w["c"]["A"]
    cid, ids = _draft2(w)
    _approve_all(w, cid)
    case = S.get_case(cid)
    d = S.version_dir(cid, 1)
    # 做成舊版的樣子：只有 document.pdf、版本的 documents 是空的、成果是 stamped.pdf
    shutil.copyfile(F.documents(case, 1)[0]["path"], d / F.DOC_NAME)
    shutil.rmtree(d / F.DOCS_DIR)
    meta = F._read_meta(d)
    meta.pop("docs", None)
    meta.update({"doc_name": "舊.pdf", "pages": 2})
    F._write_meta(d, meta)
    S.conn().execute("UPDATE seal_case_versions SET documents='[]' WHERE case_id=?", (cid,))
    (d / F.STAMPED_NAME).write_bytes(_pdf(2))
    S.conn().execute("UPDATE seal_cases SET state='done' WHERE id=?", (cid,))
    r = a.get(f"{API}/cases/{cid}/result")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_migration_v3_to_v4_keeps_versions(tmp_path):
    """`_m4_documents` 只加欄位：第 4 版之前的版本都還在，documents 預設 `[]`（讀時補成一份）。"""
    import sqlite3

    from app.core import db
    p = tmp_path / "seal_requests.sqlite"
    c = sqlite3.connect(p)
    S._m1_initial(c)
    S._m2_stamping(c)
    S._m3_watermark(c)
    c.execute("INSERT INTO seal_cases(id, number, applicant_id, applicant_label, flow_id, flow_name, subject, state, "
              "stage_idx, version, rev, created_at, updated_at) VALUES "
              "('" + "c" * 32 + "', 'SR-20261109-0009', 7, 'x@local', 1, 'f', '報價', 'done', 0, 1, 3, 1, 1)")
    c.execute("INSERT INTO seal_case_versions(case_id, version, flow_version, doc_name, doc_sha256, doc_pages, assets, "
              "placements, stages, attachments, fields, watermark, created_at) VALUES "
              "('" + "c" * 32 + "', 1, 1, 'a.pdf', 'ab', 2, '[]', '[]', '[]', '[]', '{}', '{}', 1)")
    c.execute("PRAGMA user_version=3")
    c.commit()
    c.close()
    assert db.migrate(p, S._MIGRATIONS) == len(S._MIGRATIONS)
    c = sqlite3.connect(p)
    assert c.execute("SELECT doc_name, doc_pages, documents FROM seal_case_versions").fetchone() == ("a.pdf", 2, "[]")
    c.close()
    assert F.version_docs({"documents": [], "doc_name": "a.pdf", "doc_sha256": "ab", "doc_pages": 2}) == [
        {"id": "main", "name": "a.pdf", "sha256": "ab", "pages": 2}]
