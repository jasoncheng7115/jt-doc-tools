"""用印申請第 3 版：保管人用印、成果、失敗重試、作廢、保存期限、通知（規格第 7～9、14 節）。"""
from __future__ import annotations

import json
import os
import time

import fitz
import pytest

os.environ.setdefault("JTDT_CSRF_DISABLE", "1")

from app.core import seal_flow as F  # noqa: E402
from app.core import seal_policy, seal_store as S  # noqa: E402
from tests.test_seal_flow import API, _act, _draft, _pdf, _submit, world  # noqa: F401,E402


@pytest.fixture
def sent(monkeypatch):
    from app.core import seal_notify
    calls = []
    monkeypatch.setattr(seal_notify, "notify", lambda kind, case, ids, comment="": calls.append((kind, sorted(set(ids)))))
    return calls


def _approved(w) -> str:
    cid = _draft(w)
    _submit(w, cid)
    assert _act(w, "R1", cid, "approve").status_code == 200
    assert _act(w, "R2", cid, "approve").status_code == 200
    assert _act(w, "R3", cid, "approve").status_code == 200
    assert S.get_case(cid)["state"] == "awaiting_stamp"
    return cid


def _plc(w, page=1, **kw):
    p = {"asset_id": w["stamp"].id, "page": page, "x_mm": 150, "y_mm": 240, "width_mm": 40, "height_mm": 40,
         "rotation_deg": 0}
    p.update(kw)
    return p


def _stamp(w, cid, placements=None, exec_id="a" * 32, who="C"):
    return w["c"][who].post(f"{API}/cases/{cid}/stamp",
                            json={"placements": placements if placements is not None else [_plc(w)],
                                  "exec_id": exec_id})


def test_custodian_stamps_and_applicant_downloads(world, sent):
    w = world
    cid = _approved(w)
    assert ("awaiting_stamp", [w["ids"]["C"]]) in sent and ("approved", [w["ids"]["A"]]) in sent
    q = [c["id"] for c in w["c"]["C"].get(f"{API}/cases?box=stamp").json()["cases"]]
    assert cid in q and cid not in [c["id"] for c in w["c"]["R1"].get(f"{API}/cases?box=stamp").json()["cases"]]
    # 保管人看得到章圖；其他人看不到
    img = f"{API}/cases/{cid}/assets/{w['stamp'].id}/image"
    assert w["c"]["C"].get(img).content.startswith(b"\x89PNG")
    for who in ("A", "R1", "X", "AU", "admin"):
        assert w["c"][who].get(img).status_code == 404, who
    # 申請者、無關的人不能用印
    assert _stamp(w, cid, who="A").status_code == 403
    assert _stamp(w, cid, who="X").status_code == 404
    assert _stamp(w, cid, who="R1").status_code == 403, "審核者不是保管人：不能用印"
    r = _stamp(w, cid)
    assert r.status_code == 200, r.text
    c = S.get_case(cid)
    assert c["state"] == "done" and len(c["stamped_sha256"]) == 64 and c["closed_at"]
    assert ("done", [w["ids"]["A"]]) in sent
    assert w["c"]["C"].get(img).status_code == 404, "用印完成後保管人也不再拿得到章圖"
    r = w["c"]["A"].get(f"{API}/cases/{cid}/result")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "attachment" in r.headers["content-disposition"]
    with fitz.open("pdf", r.content) as d:
        assert d.page_count == 2 and len(d[0].get_images()) >= 1, "第 1 頁要有章"
        assert len(d[1].get_images()) == 0, "沒蓋的頁不能有章"
    assert S.get_case(cid)["first_download_at"], "記下申請者第一次下載"
    assert w["c"]["X"].get(f"{API}/cases/{cid}/result").status_code == 404
    acts = [e["action"] for e in S.events(cid)]
    assert acts[-2:] == ["stamp_start", "done"]
    from app.core.history_manager import stamp_history
    assert any((h.get("extra") or {}).get("case") == c["number"] for h in stamp_history.list_all()), "要記進用印歷史"


def test_same_click_stamps_once(world, sent, monkeypatch):
    w = world
    cid = _approved(w)
    from app.tools.pdf_stamp import service
    calls = []
    real = service.stamp
    monkeypatch.setattr(service, "stamp", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    assert _stamp(w, cid, exec_id="b" * 32).status_code == 200
    assert _stamp(w, cid, exec_id="b" * 32).status_code == 200, "重送同一次按下：不出錯"
    assert len(calls) == 1, "同一次按下只蓋一次"
    assert _stamp(w, cid, exec_id="c" * 32).status_code == 409, "已經完成，新的按下也不能再蓋"
    assert [e["action"] for e in S.events(cid)].count("stamp_start") == 1


@pytest.mark.parametrize("bad, code", [
    ([], "BAD_PLACEMENT"),
    ([{"asset_id": "nope", "page": 1, "x_mm": 10, "y_mm": 10, "width_mm": 40, "height_mm": 40}], "BAD_PLACEMENT"),
    ([{"page": 9}], "BAD_PLACEMENT"),
    ([{"width_mm": 1}], "BAD_PLACEMENT"),
    ([{"x_mm": 5000}], "BAD_PLACEMENT"),
])
def test_bad_placements(world, bad, code):
    w = world
    cid = _approved(w)
    placements = [b if "asset_id" in b else _plc(w, **b) for b in bad]
    r = _stamp(w, cid, placements)
    assert r.status_code == 400 and r.json()["code"] == code, (bad, r.text)
    assert S.get_case(cid)["state"] == "awaiting_stamp"


def test_other_seal_cannot_be_swapped_in(world):
    """換成沒核准的章（就算保管人也管那顆章）：擋下。"""
    w = world
    seal_policy.set_policy(w["other"].id, "stamp", {"custodians": [{"type": "user", "key": str(w["ids"]["C"])}]})
    cid = _approved(w)
    r = _stamp(w, cid, [_plc(w, asset_id=w["other"].id)])
    assert r.status_code == 400 and r.json()["code"] == "BAD_PLACEMENT"


def test_changed_document_or_seal_blocks_stamping(world):
    w = world
    cid = _approved(w)
    d = S.version_dir(cid, 1) / F.DOC_NAME
    d.write_bytes(_pdf(3))
    r = _stamp(w, cid)
    assert r.status_code == 409 and r.json()["code"] == "DOC_CHANGED"
    cid2 = _approved(w)
    from app.core.asset_manager import asset_manager
    p = asset_manager.file_path(w["stamp"])
    p.write_bytes(p.read_bytes() + b"x")
    r = _stamp(w, cid2)
    assert r.status_code == 409 and r.json()["code"] == "ASSET_CHANGED", "核准後換了章圖：不能用舊的核准蓋"


def test_custodian_return_and_resubmit(world, sent):
    w = world
    cid = _approved(w)
    assert _act(w, "C", cid, "custodian-return").status_code == 400, "原因必填"
    assert _act(w, "R1", cid, "custodian-return", "x").status_code == 403, "不是保管人不能退回"
    assert _act(w, "C", cid, "custodian-return", "位置標錯").status_code == 200
    assert S.get_case(cid)["state"] == "returned" and ("returned", [w["ids"]["A"]]) in sent
    c = _submit(w, cid)
    assert c["version"] == 2 and c["state"] == "reviewing"


def test_withdraw_until_stamping_only(world):
    w = world
    cid = _approved(w)
    assert _act(w, "A", cid, "withdraw").status_code == 200, "待用印還可以撤回"
    assert _stamp(w, cid).status_code == 409
    cid2 = _approved(w)
    assert _stamp(w, cid2).status_code == 200
    assert _act(w, "A", cid2, "withdraw").status_code == 409, "用印之後不能撤回"


def test_failure_then_retry(world, sent, monkeypatch):
    w = world
    cid = _approved(w)
    from app.tools.pdf_stamp import service
    real = service.stamp

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(service, "stamp", boom)
    r = _stamp(w, cid, exec_id="d" * 32)
    assert r.status_code == 200 and r.json()["case"]["state"] == "stamp_failed"
    assert "disk full" in S.get_case(cid)["stamp_error"]
    assert ("stamp_failed", [w["ids"]["C"]]) in sent
    assert w["c"]["A"].get(f"{API}/cases/{cid}/result").status_code == 404, "失敗不可以有成果"
    assert not (S.version_dir(cid, 1) / F.STAMPED_NAME).exists()
    monkeypatch.setattr(service, "stamp", real)
    assert _stamp(w, cid, exec_id="e" * 32).json()["case"]["state"] == "done", "保留核准，可以重試"


def test_stale_stamping_is_recovered(world):
    w = world
    cid = _approved(w)
    c = S.get_case(cid)
    S.update_case(cid, c["rev"], state="stamping", stamp_started_at=time.time() - 3600, stamp_exec="f" * 32)
    r = w["c"]["C"].get(f"{API}/cases/{cid}")
    assert r.json()["case"]["state"] == "stamp_failed", "卡在用印中太久 → 失敗、可以重試"
    assert _stamp(w, cid, exec_id="0" * 32).json()["case"]["state"] == "done"


def test_void_by_admin(world):
    w = world
    cid = _approved(w)
    _stamp(w, cid)
    assert w["c"]["C"].post(f"/admin/seal-request/api/cases/{cid}/void", json={"reason": "x"}).status_code in (401, 403)
    assert w["c"]["admin"].post(f"/admin/seal-request/api/cases/{cid}/void", json={"reason": ""}).status_code == 400
    assert w["c"]["admin"].post(f"/admin/seal-request/api/cases/{cid}/void", json={"reason": "客戶取消"}).status_code == 200
    c = S.get_case(cid)
    assert c["state"] == "void" and c["void_reason"] == "客戶取消"
    assert w["c"]["A"].get(f"{API}/cases/{cid}/result").status_code == 404, "作廢後一般取件停止"
    assert w["c"]["AU"].get(f"{API}/cases/{cid}/result").status_code == 200, "稽核員照樣看得到"


def test_retention_purge(world):
    w = world
    cid = _approved(w)
    _stamp(w, cid)
    assert F.purge_expired() == 0, "預設 0 天＝不自動刪除"
    r = w["c"]["admin"].put("/admin/seal-request/api/retention", json={"days": 30})
    assert r.status_code == 200 and r.json()["retention_days"] == 30
    assert w["c"]["admin"].put("/admin/seal-request/api/retention", json={"days": -1}).status_code == 400
    assert F.purge_expired() == 0, "還沒到期"
    open_cid = _draft(w)
    assert F.purge_expired(now=time.time() + 31 * 86400) == 1
    assert S.get_case(cid) is None and not S.case_dir(cid).exists()
    assert S.get_case(open_cid) is not None, "沒結束的案件不刪"


def test_save_result_to_workspace(world, monkeypatch):
    from app.core import workspace
    monkeypatch.setattr(workspace, "is_enabled", lambda: True)
    w = world
    cid = _approved(w)
    _stamp(w, cid)
    r = w["c"]["A"].post(f"{API}/cases/{cid}/result/workspace")
    assert r.status_code == 200 and r.json()["file_id"], r.text
    assert w["c"]["C"].post(f"{API}/cases/{cid}/result/workspace").status_code == 403, "只有申請者存到自己的工作區"
    assert S.get_case(cid)["state"] == "done"


def test_controlled_seal_stays_blocked_elsewhere_after_stamping(world):
    w = world
    cid = _approved(w)
    _stamp(w, cid)
    assert w["c"]["C"].get(f"/assets/{w['stamp'].id}/file").status_code == 403, \
        "保管人在一般工具裡照樣不能直接用受管制的章"


def test_migration_from_v1_keeps_cases(tmp_path):
    """`_m2_stamping`（v1.17.30）只在 `seal_cases` 加欄位：第 2 版的案件升上來都還在、新欄位是預設值
    （全新資料庫升級測不到「搬丟資料」）。"""
    import sqlite3

    from app.core import db
    p = tmp_path / "seal_requests.sqlite"
    c = sqlite3.connect(p)
    S._m1_initial(c)
    c.execute("INSERT INTO seal_cases(id, number, applicant_id, applicant_label, flow_id, flow_name, subject, state, "
              "stage_idx, version, rev, created_at, updated_at) VALUES "
              "('" + "a" * 32 + "', 'SR-20261106-0001', 7, 'x@local', 1, 'f', '報價', 'awaiting_stamp', 0, 1, 3, 1, 1)")
    c.execute("PRAGMA user_version=1")
    c.commit()
    c.close()
    assert db.migrate(p, S._MIGRATIONS) == len(S._MIGRATIONS)
    c = sqlite3.connect(p)
    row = c.execute("SELECT number, subject, state, final_placements, stamp_exec, stamped_sha256, void_reason "
                    "FROM seal_cases").fetchone()
    assert row == ("SR-20261106-0001", "報價", "awaiting_stamp", "[]", "", "", ""), row
    c.close()
