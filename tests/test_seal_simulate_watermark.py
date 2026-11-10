"""用印申請：沒啟用時先準備設定、流程模擬、流程附加浮水印（v1.17.32，使用者 2026-11-06）。"""
from __future__ import annotations

import io
import os

import fitz
import pytest
from PIL import Image

os.environ.setdefault("JTDT_CSRF_DISABLE", "1")

from app.core import seal_flow as F  # noqa: E402
from app.core import seal_policy, seal_store as S  # noqa: E402
from tests.test_seal_flow import _act, _draft, _submit, world  # noqa: F401,E402
from tests.test_seal_stamp import _approved, _plc, _stamp, sent  # noqa: F401,E402

ADM = "/admin/seal-request/api"


def _flow_body(w, **kw):
    f = S.get_flow(w["flow"]["id"])
    body = {k: f[k] for k in ("name", "description", "enabled", "applicants", "assets", "stages", "watermark")}
    body.update(kw)
    return body


# --------------------------------------------------------------------------- 沒啟用時先準備

def test_prepare_settings_while_disabled(world):
    """沒啟用也能先設保管人、可申請對象、保留期限、流程；只有改成受管制要等啟用。"""
    w = world
    for a in (w["stamp"], w["other"]):
        seal_policy.set_policy(a.id, "stamp", {"mode": "direct"})
    assert seal_policy.set_enabled(False) == {"enabled": False}
    adm = w["c"]["admin"]
    who = [{"type": "user", "key": str(w["ids"]["C"])}]
    r = adm.put(f"{ADM}/assets/{w['stamp'].id}", json={"custodians": who, "requesters": who})
    assert r.status_code == 200, r.text
    assert seal_policy.get_policy(w["stamp"].id)["custodians"] == who
    assert not seal_policy.is_controlled(w["stamp"].id, "stamp"), "準備設定不可以讓章變成受管制"
    r = adm.put(f"{ADM}/assets/{w['stamp'].id}", json={"mode": "approval"})
    assert r.status_code == 409 and r.json()["code"] == "NOT_ENABLED"
    assert not seal_policy.is_controlled(w["stamp"].id, "stamp")
    assert adm.put(f"{ADM}/retention", json={"days": 90}).status_code == 200
    assert seal_policy.retention_days() == 90
    r = adm.post(f"{ADM}/flows", json=_flow_body(w, name="先建好的流程"))
    assert r.status_code == 200, r.text
    assert any(f["name"] == "先建好的流程" for f in S.list_flows())


# --------------------------------------------------------------------------- 模擬

def _sim(w, body=None, applicant="A", assets=None):
    payload = {"flow": body or _flow_body(w), "applicant": w["ids"][applicant]}
    if assets is not None:
        payload["assets"] = assets
    r = w["c"]["admin"].post(f"{ADM}/simulate", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _codes(res, level=None):
    return [it["code"] for it in res["items"] if level is None or it["level"] == level]


def test_simulate_ok_path_matches_submit_and_writes_nothing(world, sent):
    w = world
    before = (len(S.list_flows()), S.conn().execute("SELECT COUNT(*) FROM seal_cases").fetchone()[0])
    res = _sim(w, assets=[w["stamp"].id])
    assert res["ok"], res["items"]
    assert "SIM_OK" in _codes(res, "ok")
    assert [s["ok"] for s in res["stages"]] == [True, True]
    assert res["stages"][1]["need"] == 2 and len(res["stages"][1]["eligible"]) == 2
    assert res["common_custodians"] == ["sf_c@local"]
    after = (len(S.list_flows()), S.conn().execute("SELECT COUNT(*) FROM seal_cases").fetchone()[0])
    assert before == after, "模擬不可以建立任何東西"
    assert sent == [], "模擬不可以通知任何人"
    # 照模擬說可以，真的送出也要過
    _submit(w, _draft(w))


def test_simulate_lists_every_problem(world):
    """一次列出全部問題，而且跟送出時同一套判斷：人數不足、不是可申請對象、沒有保管人。"""
    w = world
    body = _flow_body(w)
    body["stages"][1] = {"name": "業務主管", "rule": "n", "n": 3, "candidates": body["stages"][1]["candidates"]}
    res = _sim(w, body=body, applicant="X", assets=[w["stamp"].id, w["other"].id])
    assert not res["ok"]
    errs = _codes(res, "error")
    assert "SIM_STAGE_SHORT" in errs and "SIM_NOT_REQUESTER" in errs and "SIM_NO_CUSTODIAN" in errs
    assert "SIM_NO_REQUESTERS" in errs, "other 沒設可申請對象"
    short = next(it for it in res["items"] if it["code"] == "SIM_STAGE_SHORT")
    assert short["detail"] == {"stage": 2, "name": "業務主管", "need": 3, "have": 2}
    # 同樣的流程真的送出會被擋在同一關
    with pytest.raises(F.FlowError) as e:
        F._freeze_stages(F.clean_flow(body), w["ids"]["A"])
    assert e.value.code == "INSUFFICIENT_APPROVERS" and e.value.detail["stage"] == 2


def test_simulate_excludes_the_applicant(world):
    """申請者本人在審核者名單裡：不算他，人數因此不夠要被抓出來。"""
    w = world
    body = _flow_body(w)
    body["stages"][0] = {"name": "部門主管", "rule": "any", "candidates": [{"type": "user", "key": str(w["ids"]["A"])}]}
    res = _sim(w, body=body, assets=[w["stamp"].id])
    assert not res["ok"] and res["stages"][0]["eligible"] == [] and res["stages"][0]["excluded_self"]
    assert "SIM_STAGE_ONLY_SELF" in _codes(res, "error"), "只有申請者本人時要講清楚原因"


def test_simulate_explains_self_only_custodian_and_missing_requesters(world):
    """管理員把自己設成唯一的保管人，又拿自己模擬：要說「保管人只有申請者本人」；
    章沒設可申請對象：要說「沒有設定＝沒有人能申請」（不是籠統的「不是可申請對象」）。"""
    w = world
    me = [{"type": "user", "key": str(w["ids"]["A"])}]
    seal_policy.set_policy(w["stamp"].id, "stamp", {"custodians": me})
    res = _sim(w, assets=[w["stamp"].id])
    assert "SIM_CUSTODIAN_IS_SELF" in _codes(res, "error")
    res = _sim(w, assets=[w["other"].id])          # other：沒有設定可申請對象
    assert "SIM_NO_REQUESTERS" in _codes(res, "error") and "SIM_NOT_REQUESTER" not in _codes(res)


def test_simulate_requires_admin(world):
    w = world
    r = w["c"]["A"].post(f"{ADM}/simulate", json={"flow": _flow_body(w), "applicant": w["ids"]["A"]})
    assert r.status_code in (302, 303, 401, 403), r.status_code


def test_simulate_bad_input(world):
    w = world
    adm = w["c"]["admin"]
    assert adm.post(f"{ADM}/simulate", json={"flow": {"name": ""}, "applicant": w["ids"]["A"]}).status_code == 400
    assert adm.post(f"{ADM}/simulate", json={"flow": _flow_body(w), "applicant": "1"}).status_code == 400
    assert adm.post(f"{ADM}/simulate", json={"flow": _flow_body(w), "applicant": 987654}).status_code == 404


# --------------------------------------------------------------------------- 浮水印

def _wm_asset():
    from app.core.asset_manager import asset_manager
    buf = io.BytesIO()
    Image.new("RGBA", (200, 80), (0, 0, 200, 160)).save(buf, "PNG")
    return asset_manager.create_from_bytes("sf-watermark", "watermark", buf.getvalue())


@pytest.mark.parametrize("wm", [
    {"enabled": True, "source": "text", "text": ""},
    {"enabled": True, "source": "text", "text": "x" * 101},
    {"enabled": True, "source": "text", "text": "ok", "color": "red"},
    {"enabled": True, "source": "text", "text": "ok", "opacity": 0},
    {"enabled": True, "source": "text", "text": "ok", "rotation_deg": 999},
    {"enabled": True, "source": "asset", "asset_id": "nope"},
    {"enabled": True, "source": "pdf"},
    "on",
])
def test_bad_watermark_is_rejected(world, wm):
    with pytest.raises(F.FlowError) as e:
        F.clean_flow(_flow_body(world, watermark=wm))
    assert e.value.code in ("BAD_WATERMARK", "BAD_FLOW")


def test_watermark_off_is_empty_and_stamp_asset_not_allowed(world):
    w = world
    assert F.clean_flow(_flow_body(w, watermark={"enabled": False, "text": "x"}))["watermark"] == {}
    with pytest.raises(F.FlowError):
        F.clean_flow(_flow_body(w, watermark={"enabled": True, "source": "asset", "asset_id": w["stamp"].id}))


def test_text_watermark_is_frozen_and_applied_under_the_seal(world, sent):
    """浮水印跟著流程在送出時凍結；用印時先上浮水印再蓋章，之後改流程不影響這件。"""
    w = world
    wm = {"enabled": True, "source": "text", "text": "僅供 {編號} 使用", "opacity": 0.3, "rotation_deg": 30,
          "size_mm": 60, "gap_mm": 30, "color": "#0000cc", "text_size_pt": 30}
    F.save_flow(w["flow"]["id"], _flow_body(w, watermark=wm))
    cid = _approved(w)
    ver = S.get_version(cid, S.get_case(cid)["version"])
    assert ver["watermark"]["text"] == "僅供 {編號} 使用"
    F.save_flow(w["flow"]["id"], _flow_body(w, watermark={}))        # 送出後改流程
    assert _stamp(w, cid, [_plc(w, page=1)]).status_code == 200
    import time
    for _ in range(100):
        if S.get_case(cid)["state"] in ("done", "stamp_failed"):
            break
        time.sleep(0.1)
    case = S.get_case(cid)
    assert case["state"] == "done", case.get("stamp_error")
    out = F.result_files({"auditor": False}, case)[0]["path"]
    with fitz.open(str(out)) as d:
        # 看畫素，不看 get_images（上完浮水印的頁面可能共用資源，列出來的圖不等於畫在這一頁的圖）
        def rgb(pg):
            pix = d[pg].get_pixmap(dpi=36)
            return [pix.pixel(x, y) for y in range(pix.height) for x in range(pix.width)]
        red = [sum(1 for c in rgb(i) if c[0] > 150 and c[1] < 90 and c[2] < 90) for i in range(2)]
        blue = [sum(1 for c in rgb(i) if c[2] > 150 and c[0] < 200 and c[1] < 200 and c[2] - c[0] > 40) for i in range(2)]
    assert blue[0] > 20 and blue[1] > 20, ("每一頁都要有浮水印", blue)
    assert red[0] > 50 and red[1] == 0, ("章只蓋在第 1 頁", red)
    # 章在浮水印上面：章的中心是純章色
    with fitz.open(str(out)) as d:
        pix = d[0].get_pixmap(dpi=72)
        cx, cy = int((150 + 20) / 25.4 * 72), int((240 + 20) / 25.4 * 72)
        c = pix.pixel(cx, cy)
    assert c[0] > 150 and c[2] < 90, c


def test_asset_watermark_and_missing_asset_fails_clearly(world, sent):
    from app.core.asset_manager import asset_manager
    w = world
    a = _wm_asset()
    try:
        F.save_flow(w["flow"]["id"], _flow_body(w, watermark={"enabled": True, "source": "asset", "asset_id": a.id}))
        res = _sim(w, assets=[w["stamp"].id])
        assert "SIM_WATERMARK_ASSET" in _codes(res, "info")
        cid = _approved(w)
    finally:
        asset_manager.delete(a.id)
    assert _stamp(w, cid, [_plc(w, page=1)]).status_code == 200
    import time
    for _ in range(100):
        if S.get_case(cid)["state"] in ("done", "stamp_failed"):
            break
        time.sleep(0.1)
    case = S.get_case(cid)
    assert case["state"] == "stamp_failed" and "WATERMARK_MISSING" in case["stamp_error"]


def test_watermark_text_tokens():
    wm = {"text": "{編號} / {日期}"}
    got = F.watermark_text(wm, {"number": "SR-20261106-0001"}, when=0)
    assert got.startswith("SR-20261106-0001 / ") and got.count("/") == 3
    assert F.watermark_text({"text": "{number} {date}"}, {"number": "N1"}, when=0) == "N1 " + got.split(" / ")[1]


def test_migration_v2_to_v3_keeps_flows_and_versions(tmp_path):
    """`_m3_watermark` 只加欄位：第 3 版的流程與版本升上來都還在，浮水印預設 `{}`。"""
    import sqlite3

    from app.core import db
    p = tmp_path / "seal_requests.sqlite"
    c = sqlite3.connect(p)
    S._m1_initial(c)
    S._m2_stamping(c)
    c.execute("INSERT INTO seal_flows(name, description, enabled, version, applicants, assets, stages, created_at, "
              "updated_at) VALUES ('報價用印', '', 1, 3, '[]', '[]', '[{\"name\":\"主管\"}]', 1, 1)")
    c.execute("INSERT INTO seal_cases(id, number, applicant_id, applicant_label, flow_id, flow_name, subject, state, "
              "stage_idx, version, rev, created_at, updated_at) VALUES "
              "('" + "b" * 32 + "', 'SR-20261106-0002', 7, 'x@local', 1, '報價用印', '報價', 'done', 0, 1, 3, 1, 1)")
    c.execute("INSERT INTO seal_case_versions(case_id, version, flow_version, doc_name, doc_sha256, doc_pages, assets, "
              "placements, stages, attachments, fields, created_at) VALUES "
              "('" + "b" * 32 + "', 1, 3, 'a.pdf', 'x', 2, '[]', '[]', '[]', '[]', '{}', 1)")
    c.execute("PRAGMA user_version=2")
    c.commit()
    c.close()
    assert db.migrate(p, S._MIGRATIONS) == len(S._MIGRATIONS)
    c = sqlite3.connect(p)
    assert c.execute("SELECT name, version, watermark FROM seal_flows").fetchone() == ("報價用印", 3, "{}")
    assert c.execute("SELECT doc_name, doc_pages, watermark FROM seal_case_versions").fetchone() == ("a.pdf", 2, "{}")
    c.close()


def test_every_simulation_item_carries_a_message_and_the_page_knows_every_code(world):
    """伺服器附上說明（頁面是舊的、認不得新代碼時也有字），頁面與伺服器的代碼清單一致。"""
    import re
    from pathlib import Path
    w = world
    body = _flow_body(w)
    body["stages"][1] = {"name": "業務主管", "rule": "n", "n": 3, "candidates": body["stages"][1]["candidates"]}
    res = _sim(w, body=body, applicant="X", assets=[w["stamp"].id, w["other"].id])
    for it in res["items"]:
        assert it["message"] and "{" not in it["message"], it
    page = (Path(__file__).resolve().parents[1] / "app/admin/templates/admin_seal_request.html").read_text(encoding="utf-8")
    codes = set(re.findall(r"    (SIM_[A-Z_]+): tr\(", page))
    assert codes == set(F.SIM_MESSAGES), (set(F.SIM_MESSAGES) ^ codes)


def test_directory_account_that_never_signed_in_is_not_called_disabled(world):
    """目錄同步進來、還沒登入過的帳號是「未啟用」不是「停用」：模擬時說清楚；
    當審核者時算進人數（使用者 2026-11-06：要算），並提醒要登入一次才能審核。"""
    from app.core import auth_db, db
    w = world
    c = auth_db.conn()
    with db.tx(c):
        for n in ("A", "R1"):
            c.execute("UPDATE users SET source='ldap', enabled=0, last_login_at=NULL WHERE id=?", (w["ids"][n],))
    res = _sim(w, assets=[w["stamp"].id])
    codes = _codes(res)
    assert "SIM_APPLICANT_NOT_ACTIVATED" in codes and "SIM_APPLICANT_DISABLED" not in codes
    assert "SIM_NOT_REQUESTER" not in codes
    assert "SIM_STAGE_SHORT" not in codes, "沒登入過的審核者要算進人數"
    pend = next(it for it in res["items"] if it["code"] == "SIM_STAGE_PENDING")
    assert pend["detail"]["n"] == 1 and pend["level"] == "warn"


def test_pending_directory_reviewers_count_at_submit_but_disabled_ones_do_not(world):
    """實際送出：沒登入過的目錄帳號算進去；AD 端停用、管理員停用、已經不在目錄裡的不算。"""
    from app.core import auth_db, db
    w = world
    r1 = w["ids"]["R1"]
    flow = F.clean_flow(_flow_body(w))
    c = auth_db.conn()

    def set_r1(**cols):
        with db.tx(c):
            c.execute("UPDATE users SET source='ldap', enabled=0, last_login_at=NULL, dir_disabled=0, "
                      "admin_disabled=0 WHERE id=?", (r1,))
            for k, v in cols.items():
                c.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, r1))

    set_r1()
    assert r1 in F.eligible_users(flow["stages"][0]["candidates"], exclude=w["ids"]["A"])
    F._freeze_stages(flow, w["ids"]["A"])                     # 不會人數不足
    for cols in ({"dir_disabled": 1}, {"admin_disabled": 1}):
        set_r1(**cols)
        assert r1 not in F.eligible_users(flow["stages"][0]["candidates"]), cols
        with pytest.raises(F.FlowError) as e:
            F._freeze_stages(flow, w["ids"]["A"])
        assert e.value.code == "INSUFFICIENT_APPROVERS"
    # 本機帳號被停用：一樣不算
    with db.tx(c):
        c.execute("UPDATE users SET source='local', enabled=0, dir_disabled=0, admin_disabled=0 WHERE id=?", (r1,))
    assert r1 not in F.eligible_users(flow["stages"][0]["candidates"])
