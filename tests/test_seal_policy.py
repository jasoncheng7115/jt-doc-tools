"""用印申請第 1 版：功能開關、每顆章的管制，以及所有用印入口一致擋下受管制的章。

規格 `docs-share/seal-approval/jtdt-seal-approval-spec-v1.md`（內部）。使用者 2026-11-05 定的規則：
功能預設不啟用；啟用要先啟用登入；還有受管制的章時不准停用；既有章升級後照舊；
設定檔讀不出來時當成全部受管制（fail-secure）。
"""
from __future__ import annotations

import io
import json
import os

import fitz
import pytest
from PIL import Image

os.environ.setdefault("JTDT_CSRF_DISABLE", "1")

from app.core import seal_policy  # noqa: E402


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (120, 120), (200, 20, 20, 255)).save(buf, "PNG")
    return buf.getvalue()


def _pdf(pages: int = 1) -> bytes:
    d = fitz.open()
    for _ in range(pages):
        d.new_page(width=595, height=842)
    out = d.tobytes()
    d.close()
    return out


@pytest.fixture
def clean_policy():
    p = seal_policy._path()
    p.unlink(missing_ok=True)
    yield p
    p.unlink(missing_ok=True)


@pytest.fixture
def assets(clean_policy):
    from app.core.asset_manager import asset_manager
    made = {k: asset_manager.create_from_bytes(f"sp-{k}", t, _png())
            for k, t in (("ctl", "stamp"), ("free", "stamp"), ("sig", "signature"), ("logo", "logo"))}
    yield made
    for a in made.values():
        asset_manager.delete(a.id)


def _enable_with_auth(admin_session):
    seal_policy.set_enabled(True)


# --------------------------------------------------------------------------- 政策本身

def test_defaults_are_off_and_direct(clean_policy, assets):
    assert seal_policy.is_enabled() is False
    for a in assets.values():
        assert seal_policy.is_controlled(a.id, a.type) is False
        assert seal_policy.get_policy(a.id)["mode"] == "direct"


def test_cannot_set_policy_while_disabled(clean_policy, assets):
    with pytest.raises(seal_policy.PolicyError) as e:
        seal_policy.set_policy(assets["ctl"].id, "stamp", {"mode": "approval"})
    assert e.value.code == "NOT_ENABLED"


def test_enable_requires_login(auth_off, clean_policy):
    with pytest.raises(seal_policy.PolicyError) as e:
        seal_policy.set_enabled(True)
    assert e.value.code == "AUTH_REQUIRED"
    assert seal_policy.is_enabled() is False


def test_cannot_disable_while_a_seal_is_controlled(admin_session, assets):
    _enable_with_auth(admin_session)
    seal_policy.set_policy(assets["ctl"].id, "stamp", {"mode": "approval"})
    assert seal_policy.is_controlled(assets["ctl"].id, "stamp")
    with pytest.raises(seal_policy.PolicyError) as e:
        seal_policy.set_enabled(False)
    assert e.value.code == "STILL_CONTROLLED" and e.value.detail["assets"] == [assets["ctl"].id]
    seal_policy.set_policy(assets["ctl"].id, "stamp", {"mode": "direct"})
    seal_policy.set_enabled(False)
    assert seal_policy.is_enabled() is False


def test_only_stamps_and_signatures_are_controllable(admin_session, assets):
    _enable_with_auth(admin_session)
    seal_policy.set_policy(assets["sig"].id, "signature", {"mode": "approval"})
    assert seal_policy.is_controlled(assets["sig"].id, "signature")
    for t in ("logo", "watermark"):
        with pytest.raises(seal_policy.PolicyError) as e:
            seal_policy.set_policy(assets["logo"].id, t, {"mode": "approval"})
        assert e.value.code == "NOT_CONTROLLABLE"


@pytest.mark.parametrize("bad", [
    {"mode": "approve"}, {"custodians": "x"}, {"custodians": [{"type": "role", "key": "1"}]},
    {"custodians": [{"type": "user", "key": "abc"}]}, {"requesters": [{"type": "group", "key": ""}]},
])
def test_bad_input_is_rejected(admin_session, assets, bad):
    _enable_with_auth(admin_session)
    with pytest.raises(seal_policy.PolicyError):
        seal_policy.set_policy(assets["ctl"].id, "stamp", bad)
    assert seal_policy.get_policy(assets["ctl"].id)["mode"] == "direct", "錯的輸入不可以改到設定"


def test_subjects_are_stored_and_deduplicated(admin_session, assets):
    _enable_with_auth(admin_session)
    p = seal_policy.set_policy(assets["ctl"].id, "stamp", {
        "custodians": [{"type": "user", "key": "7"}, {"type": "user", "key": "7"},
                       {"type": "ou", "key": "OU=Admin,DC=example,DC=com"}],
        "requesters": [{"type": "group", "key": "3"}]})
    assert p["custodians"] == [{"type": "user", "key": "7"}, {"type": "ou", "key": "OU=Admin,DC=example,DC=com"}]
    assert p["requesters"] == [{"type": "group", "key": "3"}]
    assert seal_policy.is_controlled(assets["ctl"].id, "stamp") is False, "只設名單不會自動變成管制"


def test_unreadable_file_fails_secure(clean_policy, assets):
    clean_policy.write_text("{not json", encoding="utf-8")
    assert seal_policy.is_controlled(assets["free"].id, "stamp") is True
    assert seal_policy.is_controlled(assets["sig"].id, "signature") is True
    assert seal_policy.is_controlled(assets["logo"].id, "logo") is False
    assert seal_policy.readable() is False
    with pytest.raises(seal_policy.PolicyError) as e:
        seal_policy.set_enabled(False)
    assert e.value.code == "UNREADABLE"


def test_deleting_an_asset_forgets_its_policy(admin_session, assets):
    _enable_with_auth(admin_session)
    a = assets["ctl"]
    seal_policy.set_policy(a.id, "stamp", {"mode": "approval"})
    c, _, _ = admin_session
    assert c.post(f"/admin/assets/{a.id}/delete").status_code == 200
    data = json.loads(seal_policy._path().read_text(encoding="utf-8"))
    assert a.id not in data["assets"]


# --------------------------------------------------------------------------- 所有入口

@pytest.fixture
def controlled(admin_session, assets):
    _enable_with_auth(admin_session)
    seal_policy.set_policy(assets["ctl"].id, "stamp", {"mode": "approval"})
    c, _, _ = admin_session
    return c, assets


def test_asset_image_urls_are_blocked(controlled):
    c, a = controlled
    for kind in ("file", "thumb"):
        r = c.get(f"/assets/{a['ctl'].id}/{kind}")
        assert r.status_code == 403 and "用印申請" in r.json()["detail"], kind
        assert c.get(f"/assets/{a['free'].id}/{kind}").status_code == 200, "沒管制的章照舊"


def test_pdf_editor_does_not_list_or_save_controlled(controlled):
    c, a = controlled
    ids = [x["id"] for x in c.get("/tools/pdf-editor/assets").json()["assets"]]
    assert a["ctl"].id not in ids and a["free"].id in ids
    up = c.post("/tools/pdf-editor/load", files={"file": ("a.pdf", _pdf(), "application/pdf")}).json()["upload_id"]
    obj = {"id": "i1", "type": "image", "x": 100, "y": 100, "w": 80, "h": 80, "asset_id": a["ctl"].id}
    r = c.post("/tools/pdf-editor/save", json={"upload_id": up, "pages": [{"page": 0, "objects": [obj]}]})
    assert r.status_code == 403
    obj["asset_id"] = a["free"].id
    assert c.post("/tools/pdf-editor/save",
                  json={"upload_id": up, "pages": [{"page": 0, "objects": [obj]}]}).status_code == 200


def test_stamp_tool_page_and_api_block_controlled(controlled):
    c, a = controlled
    page = c.get("/tools/pdf-stamp/").text
    assert a["ctl"].id not in page and a["free"].id in page
    r = c.post("/tools/pdf-stamp/api/pdf-stamp", data={"asset_id": a["ctl"].id},
               files={"file": ("a.pdf", _pdf(), "application/pdf")})
    assert r.status_code == 403, r.text[:200]
    placements = json.dumps([{"page": 0, "asset_id": a["ctl"].id, "x_mm": 20, "y_mm": 20,
                              "width_mm": 30, "height_mm": 30}])
    r = c.post("/tools/pdf-stamp/api/pdf-stamp", data={"asset_id": a["free"].id, "placements_json": placements},
               files={"file": ("a.pdf", _pdf(), "application/pdf")})
    assert r.status_code == 403, "每頁位置裡指定受管制的章也要擋"
    r = c.post("/tools/pdf-stamp/api/pdf-stamp", data={"asset_id": a["free"].id},
               files={"file": ("a.pdf", _pdf(), "application/pdf")})
    assert r.status_code == 200 and r.content.startswith(b"%PDF"), "沒管制的章照舊能蓋"


def test_seam_stamp_page_and_preview_block_controlled(controlled):
    c, a = controlled
    assert a["ctl"].id not in c.get("/tools/pdf-seam-stamp/").text
    up = c.post("/tools/pdf-seam-stamp/load", files={"file": ("a.pdf", _pdf(2), "application/pdf")})
    assert up.status_code == 200, up.text[:200]
    uid = up.json()["upload_id"]
    r = c.post("/tools/pdf-seam-stamp/stamp-preview", data={"upload_id": uid, "source": "asset", "asset_id": a["ctl"].id})
    assert r.status_code == 403
    r = c.post("/tools/pdf-seam-stamp/stamp-preview", data={"upload_id": uid, "source": "asset", "asset_id": a["free"].id})
    assert r.status_code == 200


def test_direct_mode_behaves_as_before(admin_session, assets):
    """功能啟用了、但章沒改成管制：所有入口跟升級前一樣。"""
    _enable_with_auth(admin_session)
    c, _, _ = admin_session
    for a in (assets["ctl"], assets["free"]):
        assert c.get(f"/assets/{a.id}/file").status_code == 200
        assert a.id in c.get("/tools/pdf-stamp/").text


# --------------------------------------------------------------------------- 管理頁 API

def test_admin_api_flow_and_audit(admin_session, assets):
    import time

    from app.core import audit_db
    start = time.time() - 1
    c, _, _ = admin_session
    st = c.get("/admin/seal-request/api/state").json()
    assert st["enabled"] is False and st["auth_enabled"] is True
    r = c.put(f"/admin/seal-request/api/assets/{assets['ctl'].id}", json={"mode": "approval"})
    assert r.status_code == 409 and r.json()["code"] == "NOT_ENABLED"
    assert c.put("/admin/seal-request/api/enabled", json={"enabled": True}).json()["enabled"] is True
    r = c.put(f"/admin/seal-request/api/assets/{assets['ctl'].id}", json={"mode": "approval"})
    assert r.status_code == 200
    row = next(x for x in r.json()["assets"] if x["id"] == assets["ctl"].id)
    assert row["mode"] == "approval"
    assert {x["type"] for x in r.json()["assets"]} <= {"stamp", "signature"}, "Logo / 浮水印不出現在這頁"
    r = c.put("/admin/seal-request/api/enabled", json={"enabled": False})
    assert r.status_code == 409 and r.json()["code"] == "STILL_CONTROLLED"
    assert c.put("/admin/seal-request/api/enabled", json={"enabled": "yes"}).status_code == 400
    assert c.put("/admin/seal-request/api/assets/nope", json={"mode": "direct"}).status_code == 404
    want = {"seal_asset_policy_change", "seal_request_toggle"}
    seen: set = set()
    for _ in range(100):          # 稽核是背景寫入：等到真的寫進去
        seen = {r[0] for r in audit_db.conn().execute(
            "SELECT event_type FROM audit_events WHERE ts>=? AND event_type IN (?,?)", (start, *want))}
        if seen == want:
            break
        time.sleep(0.05)
    assert seen == want


def test_admin_api_requires_admin(admin_session, assets):
    from fastapi.testclient import TestClient

    import app.main as app_main
    from app.core import sessions, user_manager
    uid = user_manager.create_local("sp_user", "sp_user", "UserPass1234")
    tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    u = TestClient(app_main.app)
    u.cookies.set(sessions.COOKIE_NAME, tok)
    assert u.get("/admin/seal-request/api/state", headers={"Accept": "application/json"}).status_code in (401, 403)
    assert u.put("/admin/seal-request/api/enabled", json={"enabled": True}).status_code in (401, 403)
    assert seal_policy.is_enabled() is False


def test_settings_backup_includes_the_policy():
    from app.core import settings_export
    items = [i for c in settings_export.CATEGORIES for i in c.get("items", [])]
    assert "seal_request.json" in items, "沒放進設定備份，搬機還原後管制會消失"
