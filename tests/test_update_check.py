"""定期檢查新版＋「有新版」提示（使用者 2026-11-17）。

* 相依套件檢查頁「每天自動檢查新版」，**預設關閉**；打開後背景每天檢查一次。
* 有新版時：首頁提示條（可按「這一版不再提醒」）＋側欄版本號旁的小標籤，**只給管理員**（單機模式＝開網頁的人）；
  並用各管理員自己的通知管道通知一次，同一版不重複寄。
* 連不上 GitHub 只記在狀態裡。測試一律把網路換成假的，不連 GitHub。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.core import update_check as uc

_REAL_NOTIFY = uc._notify_admins     # autouse fixture 會把它換掉；這支要測真的那個


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    uc._path().unlink(missing_ok=True)
    uc._CACHE.update(data=None, mtime=None)
    monkeypatch.setattr(uc, "start_scheduler", lambda: None)      # 不在測試裡起背景執行緒
    sent = []
    monkeypatch.setattr(uc, "_notify_admins", lambda latest: sent.append(latest))
    monkeypatch.setattr(uc, "_spawn", lambda fn, *args: fn(*args))   # 通知改成當場執行，好檢查
    yield sent
    uc._path().unlink(missing_ok=True)
    uc._CACHE.update(data=None, mtime=None)


def _bump(v: str, n: int = 1) -> str:
    a, b, c = (int(x) for x in v.split("."))
    return f"{a}.{b}.{c + n}"


def _fake_latest(monkeypatch, value):
    def f(timeout=8):
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(uc, "fetch_latest", f)


def _user_client(role_ids):
    from app.core import sessions, user_manager
    uid = user_manager.create_local(f"uc-{'-'.join(role_ids) or 'plain'}-{time.time_ns() % 10**6}", "x", "UserPass1234",
                                    roles=role_ids)
    tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, tok)
    return c


def test_off_by_default_and_toggle_is_admin_only(admin_session):
    c, *_ = admin_session
    st = c.get("/admin/api/update-check").json()
    assert st["enabled"] is False, "預設不啟用"
    assert c.put("/admin/api/update-check", json={"enabled": "yes"}).status_code == 400
    assert c.put("/admin/api/update-check", json={"enabled": True}).json()["enabled"] is True
    assert uc.get()["enabled"] is True
    user = _user_client([])
    assert user.put("/admin/api/update-check", json={"enabled": False}).status_code in (401, 403)
    assert uc.get()["enabled"] is True
    from app.core import audit_db
    rows = audit_db.conn().execute(
        "SELECT details_json FROM audit_events WHERE event_type='settings_change' AND target='update_check'").fetchall()
    assert rows, "開關要寫稽核"


def test_new_version_notifies_once_per_version(monkeypatch, _clean_state):
    sent = _clean_state
    newer = _bump(uc.current_version())
    _fake_latest(monkeypatch, newer)
    st = uc.check_now()
    assert st["is_outdated"] and st["latest"] == newer and st["last_ok"] is True
    uc.check_now()
    assert sent == [newer], "同一版只通知一次"
    newer2 = _bump(uc.current_version(), 2)
    _fake_latest(monkeypatch, newer2)
    uc.check_now()
    assert sent == [newer, newer2], "出下一版再通知一次"
    _fake_latest(monkeypatch, uc.current_version())
    uc.check_now()
    assert len(sent) == 2


def test_manual_check_does_not_notify_and_failures_are_quiet(monkeypatch, _clean_state):
    sent = _clean_state
    newer = _bump(uc.current_version())
    _fake_latest(monkeypatch, newer)
    uc.check_now(notify=False)
    assert sent == [] and uc.status()["is_outdated"]
    _fake_latest(monkeypatch, RuntimeError("無法連到 GitHub（網路 / DNS 問題）"))
    st = uc.check_now()
    assert st["last_ok"] is False and "GitHub" in st["last_error"]
    assert st["latest"] == newer, "連不上時保留上一次知道的版本"
    assert sent == []


def test_daily_schedule_is_due_only_when_enabled():
    assert uc._due(time.time()) is False
    uc.set_enabled(True)
    assert uc._due(time.time()) is True
    d = uc.get()
    d["last_check_at"] = time.time() - 3600
    uc._save(d)
    assert uc._due(time.time()) is False, "一天一次"
    d["last_check_at"] = time.time() - uc.INTERVAL - 1
    uc._save(d)
    assert uc._due(time.time()) is True


def test_banner_and_sidebar_label_only_for_admins(admin_session, monkeypatch):
    c, *_ = admin_session
    newer = _bump(uc.current_version())
    _fake_latest(monkeypatch, newer)
    uc.check_now(notify=False)
    home = c.get("/").text
    assert 'id="updBanner"' in home and f'data-version="{newer}"' in home
    assert 'class="brand-update"' in c.get("/my-jobs").text, "每一頁的側欄都有小標籤"
    for roles in ([], ["auditor"]):
        cl = _user_client(roles)
        assert 'id="updBanner"' not in cl.get("/").text, roles
        assert 'class="brand-update"' not in cl.get("/my-jobs").text, roles
        assert cl.post("/api/update-notice/dismiss", json={"version": newer}).status_code == 403, roles


def test_dismiss_hides_the_banner_until_the_next_version(admin_session, monkeypatch):
    c, *_ = admin_session
    newer = _bump(uc.current_version())
    _fake_latest(monkeypatch, newer)
    uc.check_now(notify=False)
    assert c.post("/api/update-notice/dismiss", json={"version": "x"}).status_code == 400
    assert c.post("/api/update-notice/dismiss", json={"version": newer}).status_code == 200
    page = c.get("/").text
    assert 'id="updBanner"' not in page, "這一版不再提醒"
    assert 'class="brand-update"' in page, "側欄小標籤照樣在"
    _fake_latest(monkeypatch, _bump(uc.current_version(), 2))
    uc.check_now(notify=False)
    assert 'id="updBanner"' in c.get("/").text, "出下一版要再提醒"


def test_nothing_shown_when_not_newer(admin_session, monkeypatch):
    c, *_ = admin_session
    _fake_latest(monkeypatch, uc.current_version())
    uc.check_now(notify=False)
    page = c.get("/").text
    assert 'id="updBanner"' not in page and 'class="brand-update"' not in page


def test_single_user_mode_shows_it_to_whoever_opens_the_page(auth_off, client, monkeypatch):
    _fake_latest(monkeypatch, _bump(uc.current_version()))
    uc.check_now(notify=False)
    assert 'id="updBanner"' in client.get("/").text


def test_notifies_only_admins_through_their_own_channels(admin_session, monkeypatch):
    """真的走 `_notify_admins`（只把送出那一步換掉）：管理員收到、一般使用者沒有。"""
    from app.core import notify_channels, notify_settings, user_manager, workspace
    _c, admin_name, _ = admin_session
    _user_client([])
    got = []
    monkeypatch.setattr(notify_settings, "resolve_for_user", lambda key: (["email"], {"key": key}))
    monkeypatch.setattr(notify_channels, "broadcast",
                        lambda cfg, ch, subject, text: got.append((cfg["key"], subject, text)) or {})
    newer = _bump(uc.current_version())
    _REAL_NOTIFY(newer)
    admin_id = next(u["id"] for u in user_manager.list_users() if u["username"] == admin_name)
    assert [k for k, *_ in got] == [workspace.key_for_user_id(admin_id)], got
    assert newer in got[0][1] and "jtdt update" in got[0][2]


def test_sys_deps_page_has_the_switch(admin_session):
    c, *_ = admin_session
    html = c.get("/admin/sys-deps").text
    assert 'id="verAuto"' in html and 'id="verAutoLast"' in html
    assert "api.github.com" not in html, "實際讀的是 raw.githubusercontent.com"
