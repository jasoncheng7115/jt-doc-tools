"""名片辨識跟站內其他工具一樣該有的東西：保留期限（管理員設定）、刪除帳號一併清、存到工作區、
用手機接續（QR）、完成通知（不含名片內容）。資料全部杜撰。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.tools.business_card_reader import notify, pipeline, store

TPL = Path(__file__).resolve().parents[1] / "app/tools/business_card_reader/templates/business_card_reader.html"


@pytest.fixture(autouse=True)
def _fresh_local_batches():
    """這裡的批次都掛在未啟用登入的 `local` 底下；同一個資料目錄裡前面的測試檔也用 `local` 建了批次，
    整批跑時會碰到「同時最多 20 個批次」的上限 —— 每支測試開始前先清掉。"""
    store.purge_owner("local")
    yield


def _batch(owner="local", age_days=0.0, status="review"):
    bid = store.create_batch(owner, "", "single", {}, "chi_tra+eng")
    store.batch_dir(bid).mkdir(parents=True, exist_ok=True)
    (store.batch_dir(bid) / "x.jpg").write_bytes(b"\xff\xd8" + b"0" * 2000)
    c = store.conn()
    with c:
        c.execute("UPDATE bc_batches SET created_at=?, status=? WHERE id=?",
                  (time.time() - age_days * 86400, status, bid))
    return bid


def _exists(bid):
    return store.conn().execute("SELECT 1 FROM bc_batches WHERE id=?", (bid,)).fetchone() is not None


# --------------------------------------------------------------------------- 保留期限

def test_retention_default_and_admin_setting_drive_expiry(auth_off):
    from app.core import retention
    assert retention._DEFAULTS["business_cards_days"] == 30
    old, new = _batch(age_days=11), _batch(age_days=9)
    assert store.expire_due(days=10) == 1
    assert not _exists(old) and _exists(new)
    assert not store.batch_dir(old).exists(), "原圖、裁圖要一起刪"


def test_zero_days_keeps_everything_and_running_batches_are_left_alone(auth_off):
    keep = _batch(age_days=400)
    busy = _batch(age_days=400, status="recognizing")
    assert store.expire_due(days=0) == 0 and store.expire_due(days=-1) == 0
    assert _exists(keep)
    store.expire_due(days=30)
    assert not _exists(keep)
    assert _exists(busy), "辨識中的那一批不可以被清掉（下一輪再清）"
    store.delete_batch("local", busy)


def test_sweep_all_uses_the_saved_setting(auth_off, monkeypatch):
    from app.core import retention
    s = retention.get()
    s["business_cards_days"] = 5
    monkeypatch.setattr(retention, "get", lambda: s)
    old, new = _batch(age_days=6), _batch(age_days=4)
    report = retention.sweep_all()
    assert report["business_cards_expired"] >= 1
    assert not _exists(old) and _exists(new)
    store.delete_batch("local", new)


def test_expiry_shown_follows_current_setting(auth_off, monkeypatch):
    from app.core import retention
    s = retention.get()
    s["business_cards_days"] = 7
    monkeypatch.setattr(retention, "get", lambda: s)
    assert store.keep_days() == 7
    assert store.expires_at_of(1000.0) == 1000.0 + 7 * 86400
    s["business_cards_days"] = 0
    assert store.expires_at_of(1000.0) is None


def test_stats_and_admin_page_row(admin_session):
    from app.core import retention
    bid = _batch()
    st = retention.collect_stats()["business_cards"]
    assert st["files"] >= 1 and st["size_mb"] > 0
    c, *_ = admin_session
    r = c.get("/admin/retention")
    assert r.status_code == 200
    assert 'data-key="business_cards_days"' in r.text, "保留設定頁沒有名片辨識這一列，管理員改不到"
    store.delete_batch("local", bid)


# --------------------------------------------------------------------------- 刪除帳號

def test_deleting_the_account_deletes_their_batches(auth_off):
    from app.core import user_manager
    uid = user_manager.create_local("bc_purge_user", "名片測試", "Passw0rd!xyz", roles=[])
    mine = _batch(owner=f"u{uid}")
    other = _batch(owner="u999999")
    user_manager.delete(uid)
    assert not _exists(mine) and not store.batch_dir(mine).exists()
    assert _exists(other), "別人的批次不可以被一起刪"
    store.delete_batch("u999999", other)


# --------------------------------------------------------------------------- 用手機接續

def test_handoff_qr_is_a_png_of_this_page(auth_off):
    with TestClient(app_main.app) as c:
        r = c.get("/tools/business-card-reader/handoff-qr",
                  headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "cards.example.com"})
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.headers["X-Handoff-Url"] == "https://cards.example.com/tools/business-card-reader/"
    assert "no-store" in r.headers["Cache-Control"]


def test_handoff_ignores_a_strange_scheme(auth_off):
    from app.core import handoff_qr
    with TestClient(app_main.app) as c:
        r = c.get("/tools/business-card-reader/handoff-qr", headers={"X-Forwarded-Proto": "javascript"})
    assert r.headers["X-Handoff-Url"].startswith("http://")
    with pytest.raises(ValueError):
        handoff_qr.public_url(None, "/../admin")


def test_handoff_needs_login_when_auth_is_on(admin_session):
    c, *_ = admin_session
    with TestClient(app_main.app) as anon:
        r = anon.get("/tools/business-card-reader/handoff-qr", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


# --------------------------------------------------------------------------- 畫面

def test_page_has_handoff_and_workspace_save():
    t = TPL.read_text(encoding="utf-8")
    assert 'id="bcHandoff"' in t and "/tools/business-card-reader/handoff-qr" in t
    assert 'id="bcWsSave"' in t and "saveToWorkspace({ blob }" in t
    # 工作區不收 CSV / VCF（會被改名成 .txt）：只對這三種存
    assert "const WS_FMTS = ['xlsx', 'ods', 'json'];" in t
    assert "visibilitychange" in t, "切回分頁要重讀批次（手機那邊送出的）"


# --------------------------------------------------------------------------- 完成通知

@pytest.fixture
def channels(monkeypatch):
    from app.core import notify_channels, notify_settings
    sent = []
    cfg = {"enabled": True, "min_seconds": 60, "notify_on": ["done", "failed"]}
    monkeypatch.setattr(notify_settings, "get", lambda **kw: cfg)
    monkeypatch.setattr(notify_settings, "resolve_for_user", lambda key: (["email"], {"key": key}))
    monkeypatch.setattr(notify_channels, "broadcast",
                        lambda merged, ch, subject, text, html, images: sent.append((merged, subject, text)) or {"email": None})
    return sent, cfg


def test_notification_has_counts_and_link_but_no_card_content(auth_off, channels):
    sent, _ = channels
    notify.batch_finished("local", 11, 1, 125.0, "https://cards.example.com")
    assert len(sent) == 1
    _, subject, text = sent[0]
    assert subject == "[完成] 名片辨識：12 張名片"
    assert "1 張辨識失敗" in text and "2 分 5 秒" in text
    assert "https://cards.example.com/tools/business-card-reader/" in text
    for bad in ("王小明", "0912", "@", "範例科技"):
        assert bad not in text.replace("https://cards.example.com", "")


def test_no_notification_for_short_runs_or_when_disabled(auth_off, channels):
    sent, cfg = channels
    notify.batch_finished("local", 3, 0, 20.0, "")
    assert sent == [], "短於門檻不打擾"
    cfg["enabled"] = False
    notify.batch_finished("local", 3, 0, 300.0, "")
    assert sent == []
    cfg["enabled"] = True
    cfg["notify_on"] = ["failed"]
    notify.batch_finished("local", 3, 0, 300.0, "")
    assert sent == [], "只通知失敗時，成功的不送"
    notify.batch_finished("local", 0, 3, 300.0, "")
    assert sent and sent[0][1].startswith("[失敗]")


def test_notification_goes_to_the_owner(admin_session, channels):
    sent, _ = channels
    notify.batch_finished("u42", 5, 0, 300.0, "")
    assert sent[0][0] == {"key": "u42"}
    sent.clear()
    notify.batch_finished("local", 5, 0, 300.0, "")
    assert sent == [], "啟用登入時沒有歸屬的批次沒有人可以通知"


def test_pipeline_notifies_once_after_the_batch(auth_off, monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "batch_finished", lambda *a: calls.append(a))
    from app.tools.business_card_reader import card_ocr
    from tests import bc_synth as S
    monkeypatch.setattr(card_ocr, "ocr_card", lambda img, **kw: {
        "lines": [card_ocr.Line(text="Kevin Sample", box=[10, 10, 300, 40], conf=95, height=40)],
        "rotation": 0, "engine": "fake", "image": img, "quality": 99})
    bid = pipeline.create("local", "", "single", [("a.jpg", S.jpeg(S.scene_single()[0])),
                                                  ("b.jpg", S.jpeg(S.scene_single(seed=9)[0]))], {},
                          origin="https://cards.example.com")
    pipeline.wait_idle(60)
    assert len(calls) == 1
    owner, done, failed, elapsed, origin = calls[0]
    assert (owner, done, failed, origin) == ("local", 2, 0, "https://cards.example.com")
    store.delete_batch("local", bid)


def test_admin_page_uses_the_site_dropdown_and_a_one_line_checkbox():
    """設定 → 名片簿：下拉要是本站樣式（要載 custom_select.js），AI 協助的勾選框跟文字同一行
    （使用者 2026-11-14 截圖：勾選框被撐寬、文字跑到中間，下拉是瀏覽器原生的）。"""
    t = (Path(__file__).resolve().parents[1] / "app/admin/templates/admin_business_cards.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/custom_select.js"></script>' in t
    assert ".bca-row input { width: 160px; }" not in t, "寬度只給數字欄，不可以套到勾選框"
    assert ".bca-row label.bca-check input { width: auto;" in t
