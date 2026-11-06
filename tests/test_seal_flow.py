"""用印申請第 2 版：流程範本、申請、審核（規格第 4～7、14 節）。

都打真的 API（TestClient、啟用登入），每個角色各自一個登入的客戶端。
"""
from __future__ import annotations

import io
import os
import threading

import fitz
import pytest
from PIL import Image

os.environ.setdefault("JTDT_CSRF_DISABLE", "1")

from app.core import seal_flow as F  # noqa: E402
from app.core import seal_policy, seal_store as S  # noqa: E402

API = "/seal-request/api"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (120, 120), (200, 20, 20, 255)).save(buf, "PNG")
    return buf.getvalue()


def _pdf(pages: int = 2) -> bytes:
    d = fitz.open()
    for i in range(pages):
        d.new_page(width=595, height=842).insert_text((72, 100), f"page {i + 1}", fontsize=20)
    out = d.tobytes()
    d.close()
    return out


@pytest.fixture
def world(admin_session):
    """申請者 A、審核者 R1 / R2 / R3（R2、R3 在群組 G）、保管人 C、無關的人 X、稽核員 AU。"""
    import shutil

    from fastapi.testclient import TestClient

    import app.main as app_main
    from app.core import auth_db, db, group_manager, sessions, user_manager
    from app.core.asset_manager import asset_manager

    seal_policy._path().unlink(missing_ok=True)
    shutil.rmtree(S.cases_root(), ignore_errors=True)
    sc = S.conn()
    with db.tx(sc):
        for t in ("seal_events", "seal_votes", "seal_case_versions", "seal_cases", "seal_flows"):
            sc.execute(f"DELETE FROM {t}")

    admin_c, _, _ = admin_session
    ids, clients = {}, {"admin": admin_c}
    for n in ("A", "R1", "R2", "R3", "C", "X", "AU"):
        uid = user_manager.create_local(f"sf_{n.lower()}", f"sf_{n.lower()}", "UserPass1234")
        ids[n] = uid
        tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
        c = TestClient(app_main.app)
        c.cookies.set(sessions.COOKIE_NAME, tok)
        clients[n] = c
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM subject_roles WHERE subject_type='user' AND subject_key=?", (str(ids["AU"]),))
        conn.execute("INSERT INTO subject_roles(subject_type, subject_key, role_id) VALUES ('user', ?, 'auditor')",
                     (str(ids["AU"]),))
    try:
        from app.core import permissions
        permissions.invalidate_cache() if hasattr(permissions, "invalidate_cache") else None
    except Exception:
        pass
    gid = group_manager.create_local("sf_group")
    group_manager.set_members(gid, [ids["R2"], ids["R3"]])
    stamp = asset_manager.create_from_bytes("sf-company-seal", "stamp", _png())
    other = asset_manager.create_from_bytes("sf-other-seal", "stamp", _png())
    seal_policy.set_enabled(True)
    seal_policy.set_policy(stamp.id, "stamp", {"mode": "approval",
                                                "requesters": [{"type": "user", "key": str(ids["A"])}],
                                                "custodians": [{"type": "user", "key": str(ids["C"])}]})
    seal_policy.set_policy(other.id, "stamp", {"mode": "approval", "requesters": []})
    flow = F.save_flow(None, {"name": "報價用印", "stages": [
        {"name": "部門主管", "rule": "any", "candidates": [{"type": "user", "key": str(ids["R1"])}]},
        {"name": "業務主管", "rule": "n", "n": 2, "candidates": [{"type": "group", "key": str(gid)}]},
    ]})
    w = {"ids": ids, "c": clients, "stamp": stamp, "other": other, "flow": flow, "gid": gid}
    yield w
    for a in (stamp, other):
        seal_policy.forget_asset(a.id)
        asset_manager.delete(a.id)
    seal_policy._path().unlink(missing_ok=True)
    shutil.rmtree(S.cases_root(), ignore_errors=True)


def _draft(w, *, assets=True, doc=True) -> str:
    a = w["c"]["A"]
    r = a.post(f"{API}/cases", json={"flow_id": w["flow"]["id"]})
    assert r.status_code == 200, r.text
    cid = r.json()["case"]["id"]
    if doc:
        r = a.post(f"{API}/cases/{cid}/document", files={"file": ("報價單.pdf", _pdf(), "application/pdf")})
        assert r.status_code == 200, r.text
    body = {"subject": "年度維護報價", "purpose": "報價用", "recipient": "示範公司"}
    if assets:
        body["assets"] = [w["stamp"].id]
        body["placements"] = [{"asset_id": w["stamp"].id, "page": 2, "position": "br"}]
    r = a.put(f"{API}/cases/{cid}", json=body)
    assert r.status_code == 200, r.text
    return cid


def _submit(w, cid):
    r = w["c"]["A"].post(f"{API}/cases/{cid}/submit")
    assert r.status_code == 200, r.text
    return r.json()["case"]


def _act(w, who, cid, action, comment=""):
    return w["c"][who].post(f"{API}/cases/{cid}/{action}", json={"comment": comment})


# --------------------------------------------------------------------------- 流程與申請

def test_feature_off_means_404(world):
    world["c"]["A"].get(f"{API}/home")
    for aid in (world["stamp"].id, world["other"].id):
        seal_policy.set_policy(aid, "stamp", {"mode": "direct"})
    seal_policy.set_enabled(False)
    assert world["c"]["A"].get(f"{API}/home").status_code == 404
    assert "尚未啟用" in world["c"]["A"].get("/seal-request").text


def test_only_requestable_controlled_seals_are_offered(world):
    w = world
    assets = w["c"]["A"].get(f"{API}/flows/{w['flow']['id']}/assets").json()["assets"]
    assert [a["id"] for a in assets] == [w["stamp"].id], "只有受管制、而且申請者在可申請對象裡的章"
    assert "file_url" not in assets[0] and "thumb_url" not in assets[0], "申請者拿不到原始印章圖"
    assert w["c"]["X"].get(f"{API}/flows/{w['flow']['id']}/assets").status_code == 200
    cid = _draft(w, assets=False)
    r = w["c"]["A"].put(f"{API}/cases/{cid}", json={"assets": [w["other"].id]})
    assert r.status_code == 400 and r.json()["code"] == "ASSET_NOT_ALLOWED"


def test_happy_path_through_two_stages(world):
    w = world
    cid = _draft(w)
    c = _submit(w, cid)
    assert c["state"] == "reviewing" and c["stage_idx"] == 0 and c["version"] == 1
    assert c["placements"][0]["page"] == 2 and c["placements"][0]["x_mm"] > 100, "右下角的位置換算成 mm"
    # 第 1 關：只有 R1 能審
    assert _act(w, "R2", cid, "approve").status_code == 403
    assert _act(w, "R1", cid, "approve").status_code == 200
    c = S.get_case(cid)
    assert c["state"] == "reviewing" and c["stage_idx"] == 1
    # 第 2 關：至少 2 人；同一人不能投兩次
    assert _act(w, "R2", cid, "approve").status_code == 200
    assert _act(w, "R2", cid, "approve").status_code == 403, "同一個人不能重複計票"
    assert S.get_case(cid)["stage_idx"] == 1
    assert _act(w, "R3", cid, "approve").status_code == 200
    c = S.get_case(cid)
    assert c["state"] == "awaiting_stamp", "全部關卡通過 → 待用印（不是已蓋章）"
    acts = [e["action"] for e in S.events(cid)]
    assert acts == ["submit", "approve", "stage_passed", "approve", "approve", "approved"]
    v = S.get_version(cid, 1)
    assert len(v["doc_sha256"]) == 64 and len(v["assets"][0]["sha256"]) == 64, "核准綁定文件與印章圖的雜湊"


def test_applicant_cannot_approve_or_count_as_candidate(world):
    w = world
    ids = w["ids"]
    flow = F.save_flow(None, {"name": "自己審", "stages": [
        {"name": "只有申請者", "rule": "any", "candidates": [{"type": "user", "key": str(ids["A"])}]}]})
    r = w["c"]["A"].post(f"{API}/cases", json={"flow_id": flow["id"]})
    cid = r.json()["case"]["id"]
    w["c"]["A"].post(f"{API}/cases/{cid}/document", files={"file": ("a.pdf", _pdf(), "application/pdf")})
    w["c"]["A"].put(f"{API}/cases/{cid}", json={"subject": "s", "purpose": "p", "assets": [w["stamp"].id]})
    r = w["c"]["A"].post(f"{API}/cases/{cid}/submit")
    assert r.status_code == 409 and r.json()["code"] == "INSUFFICIENT_APPROVERS", "申請者不算進候選人"
    # 申請者同時在第 1 關的候選名單裡：照樣不能審自己的
    F.save_flow(w["flow"]["id"], {"name": "報價用印", "stages": [
        {"name": "部門主管", "rule": "any", "candidates": [{"type": "user", "key": str(ids["R1"])},
                                                          {"type": "user", "key": str(ids["A"])}]}]})
    cid2 = _draft(w)
    _submit(w, cid2)
    r = _act(w, "A", cid2, "approve")
    assert r.status_code == 403 and r.json()["code"] == "SELF_APPROVAL"


def test_not_enough_approvers_blocks_submit(world):
    w = world
    F.save_flow(w["flow"]["id"], {"name": "報價用印", "stages": [
        {"name": "三人會簽", "rule": "n", "n": 3, "candidates": [{"type": "group", "key": str(w["gid"])}]}]})
    cid = _draft(w)
    r = w["c"]["A"].post(f"{API}/cases/{cid}/submit")
    assert r.status_code == 409 and r.json()["code"] == "INSUFFICIENT_APPROVERS"
    assert r.json()["detail"] == {"stage": 1, "name": "三人會簽", "need": 3, "have": 2}
    assert S.get_case(cid)["state"] == "draft"


def test_all_rule_gets_stuck_when_a_member_leaves_and_admin_reassigns(world):
    w = world
    from app.core import user_manager
    F.save_flow(w["flow"]["id"], {"name": "報價用印", "stages": [
        {"name": "全員", "rule": "all", "candidates": [{"type": "group", "key": str(w["gid"])}]}]})
    cid = _draft(w)
    _submit(w, cid)
    assert _act(w, "R2", cid, "approve").status_code == 200
    user_manager.update(w["ids"]["R3"], enabled=False)
    st = F.stage_status(S.get_case(cid))
    assert st["stuck"] is True, "全員核准裡有人停用 → 卡住，不可以被跳過"
    assert S.get_case(cid)["state"] == "reviewing"
    r = w["c"]["admin"].post(f"/admin/seal-request/api/cases/{cid}/reassign",
                             json={"candidates": [{"type": "user", "key": str(w["ids"]["R1"])}], "reason": "R3 離職"})
    assert r.status_code == 200, r.text
    assert _act(w, "R1", cid, "approve").status_code == 200
    assert S.get_case(cid)["state"] == "awaiting_stamp"
    ev = [e for e in S.events(cid) if e["action"] == "reassign"]
    assert ev and ev[0]["comment"] == "R3 離職"
    r = w["c"]["admin"].post(f"/admin/seal-request/api/cases/{cid}/reassign",
                             json={"candidates": [{"type": "user", "key": "1"}], "reason": ""})
    assert r.status_code in (400, 409)


def test_concurrent_approvals_advance_once(world):
    """「任一人」那一關兩個人同時按：關卡只前進一次。"""
    w = world
    ids = w["ids"]
    F.save_flow(w["flow"]["id"], {"name": "報價用印", "stages": [
        {"name": "任一", "rule": "any", "candidates": [{"type": "group", "key": str(w["gid"])}]},
        {"name": "第二關", "rule": "any", "candidates": [{"type": "user", "key": str(ids["R1"])}]}]})
    cid = _draft(w)
    _submit(w, cid)
    case = S.get_case(cid)
    results = []

    def go(who):
        user = {"id": ids[who], "label": who, "admin": False, "auditor": False}
        try:
            F.approve(user, case)
            results.append("ok")
        except F.FlowError as e:
            results.append(e.code)

    ts = [threading.Thread(target=go, args=(n,)) for n in ("R2", "R3")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["BAD_STATE", "ok"] or results.count("ok") == 1, results
    c = S.get_case(cid)
    assert c["stage_idx"] == 1 and c["state"] == "reviewing"
    assert [e["action"] for e in S.events(cid)].count("stage_passed") == 1


def test_return_and_resubmit_starts_over_with_a_new_version(world):
    w = world
    cid = _draft(w)
    _submit(w, cid)
    assert _act(w, "R1", cid, "return").status_code == 400, "退回要填原因"
    assert _act(w, "R1", cid, "return", "金額要更正").status_code == 200
    assert S.get_case(cid)["state"] == "returned"
    a = w["c"]["A"]
    r = a.post(f"{API}/cases/{cid}/document", files={"file": ("報價單v2.pdf", _pdf(3), "application/pdf")})
    assert r.status_code == 200
    c = _submit(w, cid)
    assert c["version"] == 2 and c["stage_idx"] == 0 and c["state"] == "reviewing", "重送從第一關、新版本"
    assert S.get_version(cid, 1)["doc_pages"] == 2 and S.get_version(cid, 2)["doc_pages"] == 3, "舊版本保留"
    assert F.can_review(w["ids"]["R1"], S.get_case(cid)), "舊版本的動作不算，第一關要重審"


def test_reject_and_withdraw(world):
    w = world
    cid = _draft(w)
    _submit(w, cid)
    assert _act(w, "R1", cid, "reject", "不同意").status_code == 200
    c = S.get_case(cid)
    assert c["state"] == "rejected" and c["closed_at"]
    assert _act(w, "A", cid, "withdraw").status_code == 409, "結束的案件不能撤回"
    cid2 = _draft(w)
    _submit(w, cid2)
    assert _act(w, "R1", cid2, "withdraw").status_code in (403, 404), "只有申請者能撤回"
    assert _act(w, "A", cid2, "withdraw").status_code == 200
    assert S.get_case(cid2)["state"] == "withdrawn"
    assert _act(w, "R1", cid2, "approve").status_code == 409, "撤回後已經沒有審核關卡"


def test_who_can_see_what(world):
    w = world
    cid = _draft(w)
    # 草稿：只有申請者
    assert w["c"]["R1"].get(f"{API}/cases/{cid}").status_code == 404
    _submit(w, cid)
    with_docs = {"A", "R1", "R2", "C", "AU"}
    for who in ("A", "R1", "R2", "R3", "C", "X", "AU", "admin"):
        c = w["c"][who]
        detail = c.get(f"{API}/cases/{cid}").status_code
        doc = c.get(f"{API}/cases/{cid}/document").status_code
        png = c.get(f"{API}/cases/{cid}/pages/1").status_code
        if who == "X":
            assert (detail, doc, png) == (404, 404, 404), "無關的人一律找不到"
        elif who == "admin":
            assert detail == 200 and doc == 404 and png == 404, "管理員只看得到案件資料，看不到文件"
        elif who in with_docs or who == "R3":
            assert (detail, doc, png) == (200, 200, 200), who
    r = w["c"]["A"].get(f"{API}/cases/{cid}/document")
    assert r.content.startswith(b"%PDF") and "attachment" in r.headers.get("content-disposition", "")
    assert w["c"]["X"].get(f"{API}/cases?box=all").status_code == 404
    assert w["c"]["AU"].get(f"{API}/cases?box=all").status_code == 200


def test_review_queue_lists_only_my_turn(world):
    w = world
    cid = _draft(w)
    _submit(w, cid)
    ids = lambda who: [c["id"] for c in w["c"][who].get(f"{API}/cases?box=review").json()["cases"]]
    assert cid in ids("R1") and cid not in ids("R2") and cid not in ids("A")
    _act(w, "R1", cid, "approve")
    assert cid not in ids("R1") and cid in ids("R2")
    assert cid in [c["id"] for c in w["c"]["R1"].get(f"{API}/cases?box=handled").json()["cases"]]


def test_document_checks(world, monkeypatch):
    w = world
    a = w["c"]["A"]
    cid = a.post(f"{API}/cases", json={"flow_id": w["flow"]["id"]}).json()["case"]["id"]
    d = fitz.open()
    d.new_page()
    enc = d.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    r = a.post(f"{API}/cases/{cid}/document", files={"file": ("e.pdf", enc, "application/pdf")})
    assert r.status_code == 400 and r.json()["code"] == "ENCRYPTED"
    from app.core import pdf_signatures
    monkeypatch.setattr(pdf_signatures, "signed_field_count", lambda doc: 1)
    r = a.post(f"{API}/cases/{cid}/document", files={"file": ("s.pdf", _pdf(), "application/pdf")})
    assert r.status_code == 400 and r.json()["code"] == "SIGNED"
    monkeypatch.undo()
    r = a.post(f"{API}/cases/{cid}/document", files={"file": ("x.bin", b"\x00\x01junk", "application/octet-stream")})
    assert r.status_code == 400 and r.json()["code"] == "NOT_PDF"
    r = a.post(f"{API}/cases/{cid}/submit")
    assert r.status_code == 400 and r.json()["code"] == "FIELD_REQUIRED"


def test_placements_and_attachments(world):
    w = world
    cid = _draft(w)
    a = w["c"]["A"]
    for bad in ([{"asset_id": w["stamp"].id, "page": 9, "position": "br"}],
                [{"asset_id": w["stamp"].id, "page": 1, "position": "nowhere"}],
                [{"asset_id": w["other"].id, "page": 1, "position": "br"}]):
        r = a.put(f"{API}/cases/{cid}", json={"placements": bad})
        assert r.status_code == 400 and r.json()["code"] == "BAD_PLACEMENT", bad
    r = a.post(f"{API}/cases/{cid}/attachments", files={"file": ("估價明細.xlsx", b"PK\x03\x04data", "application/octet-stream")})
    assert r.status_code == 200
    att = r.json()["attachments"][0]
    _submit(w, cid)
    r = w["c"]["R1"].get(f"{API}/cases/{cid}/attachments/{att['id']}", params={"version": 1})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/octet-stream")
    assert w["c"]["X"].get(f"{API}/cases/{cid}/attachments/{att['id']}").status_code == 404
    assert a.post(f"{API}/cases/{cid}/attachments", files={"file": ("b", b"x", "text/plain")}).status_code == 409, \
        "送出後不能再改附件"


def test_admin_flow_api_validates_and_audits(world):
    import time

    from app.core import audit_db
    start = time.time() - 1
    c = world["c"]["admin"]
    assert c.post("/admin/seal-request/api/flows", json={"name": "", "stages": []}).status_code == 400
    assert c.post("/admin/seal-request/api/flows", json={"name": "x", "stages": [
        {"name": "a", "rule": "n", "n": 0, "candidates": [{"type": "user", "key": "1"}]}]}).status_code == 400
    r = c.post("/admin/seal-request/api/flows", json={"name": "合約用印", "stages": [
        {"name": "法務", "rule": "any", "candidates": [{"type": "user", "key": str(world["ids"]["R1"])}]}]})
    assert r.status_code == 200 and any(f["name"] == "合約用印" for f in r.json()["flows"])
    assert world["c"]["R1"].post("/admin/seal-request/api/flows", json={}).status_code in (401, 403)
    seen = set()
    for _ in range(100):
        seen = {r[0] for r in audit_db.conn().execute(
            "SELECT event_type FROM audit_events WHERE ts>=? AND event_type='seal_flow_change'", (start,))}
        if seen:
            break
        time.sleep(0.05)
    assert seen == {"seal_flow_change"}
