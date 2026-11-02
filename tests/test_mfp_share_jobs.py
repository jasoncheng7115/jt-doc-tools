"""檔案分享取件（實驗功能）—— 路由與背景作業。

* 清單：Canon 有「取件」（WebDAV 要連線測試連得上；SMB 不用）、Konica / SHARP 有、其他廠牌沒有。
* 列檔：回位置、是否共享、資料夾與檔案；設備端錯誤回 422 附協定紀錄、不含密碼；路線不屬於這台廠牌回 400。
* 取件：預設 PDF（TIFF 轉 PDF、PDF 原樣、辦公文件原樣），原始檔不轉；工作區收不下的格式留在暫存區給本人下載；
  每個檔案記原件還在不在；別人拿作業 id 查不到。
* 管理員關掉「使用者自行新增事務機」時，自己的事務機不能取件。
"""
from __future__ import annotations

import time

import pytest

from app.core import mfp_client, mfp_queue, mfp_scan, mfp_settings, mfp_store
from tests.test_mfp_fetch_tool import _fake_report, _user_client, fake_probe  # noqa: F401
from tests.test_mfp_share import FakeCanon, FakeSmbServer, fake_class, tiff_bytes

import importlib

R = importlib.import_module("app.tools.mfp_fetch.router")
S = importlib.import_module("app.core.mfp_share")
API = "/tools/mfp-fetch/api"


@pytest.fixture
def env(monkeypatch, admin_session, fake_probe):
    real = mfp_client.check_address
    monkeypatch.setattr(mfp_client, "check_address", lambda ip: None if ip == "127.0.0.1" else real(ip))
    canon = FakeCanon()
    srv = canon.serve()
    port = srv.server_address[1]
    canon.port = port
    monkeypatch.setattr(mfp_client, "ALLOWED_PORTS", tuple(mfp_client.ALLOWED_PORTS) + (port,))
    smb = FakeSmbServer()
    monkeypatch.setattr(S, "_strict_connection_class", lambda: fake_class(smb))
    mfp_queue._reset_for_tests()
    mfp_scan._reset_for_tests()
    R._recent.clear()
    R._busy.clear()
    a, ua = _user_client(f"share-a-{time.time_ns()}", ["clerk"])
    b, ub = _user_client(f"share-b-{time.time_ns()}", ["clerk"])
    yield {"canon": canon, "smb": smb, "a": a, "ua": ua, "b": b, "ub": ub}
    srv.shutdown()
    mfp_queue._reset_for_tests()
    mfp_settings.save({"allow_user_devices": True})


def _device(uid, vendor, port=None, host="127.0.0.1"):
    rep = _fake_report(host)
    rep["detection"]["vendor"] = vendor
    rep["summary"]["reachable_on"] = [f"http:{port}"] if port else []
    return mfp_store.create(f"u{uid}", uid, host, port, "http" if port else None, vendor, rep)["id"]


def _wait(c, sid, until=("done", "error", "cancelled"), timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        s = c.get(f"{API}/scans/{sid}").json()["scan"]
        if s["status"] in until:
            return s
        time.sleep(0.05)
    raise AssertionError(f"作業沒有到 {until}：{s}")


@pytest.fixture
def ws_on(monkeypatch):
    from app.core import workspace
    monkeypatch.setattr(workspace, "is_enabled", lambda: True)
    return workspace


def _row(c, cid):
    return next(x for x in c.get(f"{API}/connections").json()["connections"] if x["id"] == cid)


def test_which_devices_get_share_retrieval(env):
    e = env
    canon = _device(e["ua"], "Canon", e["canon"].port)
    konica = _device(e["ua"], "Konica Minolta", host="127.0.0.2")
    hp = _device(e["ua"], "HP", host="127.0.0.3")
    canon_no_web = _device(e["ua"], "Canon", host="127.0.0.4")
    assert _row(e["a"], canon)["share"] == {"routes": ["canon_webdav", "canon_smb"]}
    assert _row(e["a"], konica)["share"] == {"routes": ["konica_smb"]}
    assert _row(e["a"], hp)["share"] is None
    assert _row(e["a"], canon_no_web)["share"] == {"routes": ["canon_smb"]}, "連線測試沒連上網頁就只剩 SMB"
    r = e["a"].post(f"{API}/connections/{hp}/share/list", json={"route": "canon_smb", "allow_plain": True})
    assert r.status_code == 400 and r.json()["code"] == "SHARE_ROUTE_INVALID"


def test_canon_webdav_list_and_fetch_into_workspace(env, ws_on):
    import fitz
    e = env
    cid = _device(e["ua"], "Canon", e["canon"].port)
    body = {"route": "canon_webdav", "allow_http": True}
    r = e["a"].post(f"{API}/connections/{cid}/share/list", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["root"] == "share" and j["shared"] is True and [x["id"] for x in j["roots"]] == ["share", "users"]
    assert [x["name"] for x in j["entries"]] == ["掃描", "報價.pdf"] and j["via"]["protocol"] == "WebDAV"
    r = e["a"].post(f"{API}/connections/{cid}/share/fetch", json=dict(
        body, root="share", files=[["掃描", "合約.tif"], ["報價.pdf"]]))
    assert r.status_code == 202, r.text
    s = _wait(e["a"], r.json()["scan"]["id"])
    assert s["status"] == "done" and s["kind"] == "share", s
    assert [f["name"] for f in s["files"]] == ["合約.pdf", "報價.pdf"]
    assert all(i["source"] == "SOURCE_KEPT" for i in s["items"])
    fid = s["files"][0]["workspace_file_id"]
    meta = ws_on.find_for_key(f"u{e['ua']}", fid)
    with fitz.open(ws_on._root() / f"u{e['ua']}" / fid / f"file{meta['ext']}") as d:
        assert d.page_count == 2
    assert {q["method"] for q in e["canon"].requests} <= {"PROPFIND", "GET"}
    assert e["b"].get(f"{API}/scans/{s['id']}").status_code == 404


def test_original_keeps_tiff_and_other_formats_are_kept_as_is(env, ws_on):
    e = env
    e["canon"].tree["share"]["note.bin"] = b"\x00\x01binary"
    cid = _device(e["ua"], "Canon", e["canon"].port)
    r = e["a"].post(f"{API}/connections/{cid}/share/fetch", json={
        "route": "canon_webdav", "allow_http": True, "root": "share", "output": "original",
        "files": [["掃描", "合約.tif"], ["note.bin"]]})
    s = _wait(e["a"], r.json()["scan"]["id"])
    assert s["status"] == "done", s
    names = [f["name"] for f in s["files"]]
    assert names == ["合約.tif", "note.bin"]
    assert s["files"][0]["workspace_file_id"], "TIFF 進工作區"
    assert not s["files"][1]["workspace_file_id"] and s["files"][1]["has_file"], "工作區收不下的留給本人下載"
    # 格式不收要說是格式，不是「可能超過大小上限或額度」（2026-11-02）
    assert s["workspace_note"] == "WS_UNSUPPORTED"


def test_konica_box_over_smb(env, ws_on):
    e = env
    e["smb"].shares = {"000000012_Scan": {"a.tif": tiff_bytes(1)}, "IPC$": None}
    e["smb"].accounts[("12", "")] = "1234"
    cid = _device(e["ua"], "Konica Minolta", host="127.0.0.1")
    body = {"route": "konica_smb", "box": "12", "box_password": "1234"}
    r = e["a"].post(f"{API}/connections/{cid}/share/list", json=body)
    assert r.status_code == 422 and r.json()["code"] == "SMB_PLAIN_NEEDS_CONSENT", "SMB 不加密要先同意"
    body["allow_plain"] = True
    j = e["a"].post(f"{API}/connections/{cid}/share/list", json=body).json()
    assert j["ok"] and j["root"] == "000000012_Scan" and j["shared"] is True and j["via"]["protocol"] == "SMB"
    s = _wait(e["a"], e["a"].post(f"{API}/connections/{cid}/share/fetch",
                                   json=dict(body, files=[["a.tif"]])).json()["scan"]["id"])
    assert s["status"] == "done" and [f["name"] for f in s["files"]] == ["a.pdf"], s
    assert ("WRITE",) not in e["smb"].calls


def test_wrong_password_is_422_with_trace_and_no_secret(env):
    e = env
    e["canon"].user, e["canon"].password = "clerk", "right"
    cid = _device(e["ua"], "Canon", e["canon"].port)
    r = e["a"].post(f"{API}/connections/{cid}/share/list", json={
        "route": "canon_webdav", "allow_http": True, "user": "clerk", "password": "S3cret-Wrong"})
    assert r.status_code == 422 and r.json()["code"] == "AUTH_FAILED" and r.json()["trace"]
    assert "S3cret" not in r.text


@pytest.mark.parametrize("body,code", [
    ({"files": []}, "SHARE_NO_FILES_SELECTED"),
    ({"files": [["a"]] * 51}, "SHARE_TOO_MANY_FILES"),
    ({"files": [[".."]]}, "SHARE_PATH_INVALID"),
    ({"files": "a.pdf"}, "SHARE_NO_FILES_SELECTED"),
    ({"files": [["a.pdf"], ["a.pdf"]]}, "SHARE_PATH_INVALID"),
    ({"files": [["a.pdf"]], "root": "../etc"}, "SHARE_ROOT_INVALID"),
    ({"files": [["a.pdf"]], "output": "docx"}, "BOX_OUTPUT_INVALID"),
])
def test_bad_fetch_requests_are_refused_before_queueing(env, body, code):
    e = env
    cid = _device(e["ua"], "Canon", e["canon"].port)
    r = e["a"].post(f"{API}/connections/{cid}/share/fetch",
                    json={"route": "canon_webdav", "allow_http": True, "root": "share", **body})
    assert r.status_code == 422 and r.json()["code"] == code, r.text
    assert not e["canon"].requests


def test_paused_devices_cannot_fetch(env):
    e = env
    cid = _device(e["ua"], "Canon", e["canon"].port)
    mfp_settings.save({"allow_user_devices": False})
    for url in ("share/list", "share/fetch"):
        r = e["a"].post(f"{API}/connections/{cid}/{url}", json={"route": "canon_webdav", "allow_http": True})
        assert r.status_code == 403 and r.json()["code"] == "USER_DEVICES_DISABLED"
    assert not e["canon"].requests
