"""稽核轉送：每個目的地各自一個游標、失敗不前移、不自我餵食（F08，v1.15.28）。

原本的實作用**單一共用游標** `key='forward'`，但模組說明寫著
「Bookmark per destination」與「no events lost or duplicated」——
**註解承諾了實作沒做到的事**。一個目的地送不出去，整體游標照樣前移，
那些事件對它而言永遠不會再送。

而 `audit_forward_failed` 會被當成普通事件撈出來轉送 → 又失敗 → 再寫一筆，
**沒有任何使用者活動也會一直長**。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.core import audit_db, audit_forward as af


@pytest.fixture(autouse=True)
def _clean_state():
    """**自己建的列自己收**（共用測試 DB 的慣例）。

    而且要把每個目的地的起點設在「我的第一筆事件之前」—— 不然前面測試留下
    的稽核事件也會被撈進來，數量就對不上了。
    """
    af._DEST_STATE.clear()
    audit_db.init()
    conn = audit_db.conn()
    conn.execute("DELETE FROM forward_state")
    conn.commit()
    row = conn.execute("SELECT COALESCE(MAX(id), 0) m FROM audit_events").fetchone()
    base = row["m"]
    af._bookmark_set(conn, af._LEGACY_KEY, base)     # 所有目的地從這裡開始
    yield base
    conn.execute("DELETE FROM audit_events WHERE id > ?", (base,))
    conn.execute("DELETE FROM forward_state")
    conn.commit()
    af._DEST_STATE.clear()


def _skip_backoff():
    """跳過退避，但**保留失敗記錄的冷卻**（那是另一件事）。"""
    for st in af._DEST_STATE.values():
        st["until"] = 0.0
        st["delay"] = 0.0


def _seed(n: int, event_type: str = "login_success") -> list[int]:
    conn = audit_db.conn()
    ids = []
    for i in range(n):
        cur = conn.execute(
            "INSERT INTO audit_events(ts, username, ip, event_type, target, "
            "details_json) VALUES (?,?,?,?,?,?)",
            (1_800_000_000.0 + i, "u", "127.0.0.1", event_type, "t", "{}"))
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def _dest(did: str, ok: bool):
    return {"id": did, "name": f"dest-{did}", "enabled": True,
            "format": "gelf", "transport": "udp",
            "host": "127.0.0.1" if ok else "203.0.113.1", "port": 12201}


def test_each_destination_keeps_its_own_bookmark(monkeypatch, _clean_state):
    """好的目的地照樣前進；壞的停在原地，下一輪再試那些事件。"""
    base = _clean_state
    _seed(3)
    good, bad = _dest("good", True), _dest("bad", False)
    monkeypatch.setattr(af, "get", lambda: {"destinations": [good, bad]})

    sent = []

    def fake_send(dest, payload):
        if dest["id"] == "bad":
            raise OSError("connection refused")
        sent.append((dest["id"], payload))

    monkeypatch.setattr(af, "_send", fake_send)
    monkeypatch.setattr(af, "_send_with_retry",
                        lambda dest, payload, attempts=3: fake_send(dest, payload))

    af._drain_once()
    conn = audit_db.conn()
    assert af._bookmark_get(conn, af._dest_key(good)) > base, "好的目的地應該前進"
    assert af._bookmark_get(conn, af._dest_key(bad)) == base, (
        "壞的目的地不可以前移游標 —— 那些事件對它而言就永遠不見了")
    assert len(sent) == 3


def test_a_failed_destination_does_not_starve_the_others(monkeypatch):
    """壞的目的地不可以讓整個迴圈停下來。"""
    _seed(2)
    bad, good = _dest("bad", False), _dest("good", True)
    monkeypatch.setattr(af, "get", lambda: {"destinations": [bad, good]})
    delivered = []
    monkeypatch.setattr(af, "_send_with_retry",
                        lambda dest, payload, attempts=3:
                        (_ for _ in ()).throw(OSError("x"))
                        if dest["id"] == "bad" else delivered.append(dest["id"]))
    af._drain_once()
    assert delivered == ["good", "good"], "壞的排在前面就把好的擋住了"


def test_failure_events_are_never_forwarded(monkeypatch, _clean_state):
    """`audit_forward_failed` 是本機記帳 —— 轉送它會變成自我餵食的迴圈。

    **只數這一輪自己產生的列**：稽核寫入走背景佇列（單一 writer 執行緒），
    前一支測試的失敗事件可能在它的清理跑完之後才落地 —— 數整張表會隨著
    測試順序時紅時綠（實際踩到過：單跑綠、合跑紅）。
    """
    base = _clean_state
    _seed(1)
    bad = _dest("bad", False)
    monkeypatch.setattr(af, "get", lambda: {"destinations": [bad]})
    monkeypatch.setattr(af, "_send_with_retry",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    conn = audit_db.conn()

    for _ in range(3):
        _skip_backoff()                 # 跳過退避，但冷卻要留著
        af._drain_once()

    n = conn.execute("SELECT COUNT(*) c FROM audit_events "
                     "WHERE event_type='audit_forward_failed' AND id > ?",
                     (base,)).fetchone()["c"]
    assert n <= 1, f"連跑三輪產生了 {n} 筆失敗事件（冷卻沒生效）"

    # 就算資料庫裡有失敗事件，也不可以被撈出來送
    rows = conn.execute(
        "SELECT id FROM audit_events WHERE event_type='audit_forward_failed' "
        "AND id > ?", (base,)).fetchall()
    if rows:
        picked = []
        monkeypatch.setattr(af, "_send_with_retry",
                            lambda dest, payload, attempts=3: picked.append(payload))
        _skip_backoff()
        af._bookmark_set(conn, af._dest_key(bad), rows[0]["id"] - 1)
        af._drain_once()
        joined = b"".join(picked)
        assert b"audit_forward_failed" not in joined, "失敗事件被轉送出去了"


def test_a_failing_destination_backs_off(monkeypatch):
    """失敗之後要退避 —— 每輪都重試 3 次加 sleep 會把轉送迴圈拖垮。"""
    _seed(1)
    bad = _dest("bad", False)
    monkeypatch.setattr(af, "get", lambda: {"destinations": [bad]})
    tries = []
    monkeypatch.setattr(af, "_send_with_retry",
                        lambda *a, **k: (tries.append(1),
                                         (_ for _ in ()).throw(OSError("x"))))
    af._drain_once()
    assert len(tries) == 1
    af._drain_once()              # 立刻再跑一次 → 應該被退避擋住
    assert len(tries) == 1, "沒有退避，壞的目的地每輪都在重試"
    st = af._DEST_STATE[af._dest_key(bad)]
    assert st["until"] > 0 and st["delay"] >= af._BACKOFF_START


def test_an_upgrade_does_not_resend_the_whole_history(monkeypatch):
    """舊版只有一個共用游標 —— 升級後要拿它當每個目的地的起點。

    不這樣做的話，第一輪會把整個稽核資料庫重送一遍給每個目的地。
    """
    ids = _seed(5)
    conn = audit_db.conn()
    af._bookmark_set(conn, af._LEGACY_KEY, ids[-1])       # 舊版留下的游標
    d = _dest("new", True)
    assert af._bookmark_get(conn, af._dest_key(d)) == ids[-1], (
        "沒有沿用舊游標 → 升級後會重送整份歷史")


def test_the_docstring_no_longer_promises_what_it_does_not_do():
    src = pathlib.Path(af.__file__).read_text(encoding="utf-8")
    doc = ast.get_docstring(ast.parse(src)) or ""
    assert "no events lost or duplicated under normal operation" not in doc, (
        "說明又寫回那句做不到的保證")
    assert "forward:" in doc or "每個目的地各有自己的游標" in doc


def test_both_bookmark_calls_are_keyed_by_destination():
    """判準走 AST：游標的讀寫都必須帶 key，不可以再有寫死的共用游標。"""
    src = pathlib.Path(af.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for name in ("_bookmark_get", "_bookmark_set"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        args = [a.arg for a in fn.args.args]
        assert "key" in args, f"{name}() 沒有 key 參數（等於還是共用一個游標）"
