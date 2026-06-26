"""CSRF middleware（app/core/csrf.py）單元測試 —— 直接以 ASGI 呼叫 middleware，
不依賴 conftest 的 JTDT_CSRF_DISABLE 旗標（測試前清掉），驗證真實驗證邏輯。"""
import asyncio

import pytest

from app.core import csrf


@pytest.fixture(autouse=True)
def _enable_csrf(monkeypatch):
    # 本模組要測真實驗證 → 移除 conftest 的全域跳過旗標
    monkeypatch.delenv("JTDT_CSRF_DISABLE", raising=False)


async def _dummy_app(scope, receive, send):
    body = b""
    while True:
        m = await receive()
        body += m.get("body", b"")
        if not m.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok:" + body})


def _scope(method="POST", path="/tools/x/search", headers=None):
    hs = [(k.lower().encode("latin-1"), v.encode("latin-1"))
          for k, v in (headers or {}).items()]
    return {"type": "http", "method": method, "path": path, "scheme": "http",
            "headers": hs}


def _run(scope, body=b""):
    mw = csrf.CSRFMiddleware(_dummy_app)
    sent = []
    msgs = iter([{"type": "http.request", "body": body, "more_body": False}])

    async def receive():
        try:
            return next(msgs)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)

    asyncio.run(mw(scope, receive, send))
    return scope, sent


def _status(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            return m["status"]
    return None


def _set_cookie(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            for k, v in m.get("headers", []):
                if k == b"set-cookie":
                    return v.decode("latin-1")
    return None


# ---- helper 單元 ----

def test_token_unique_and_valid():
    a, b = csrf.new_token(), csrf.new_token()
    assert a != b and len(a) > 20
    assert csrf._valid(a, a) and not csrf._valid(a, b)
    assert not csrf._valid("", "x") and not csrf._valid("x", None)


def test_form_token_parse():
    assert csrf._form_token(b"a=1&csrf_token=abc&b=2") == "abc"
    assert csrf._form_token(b"a=1") is None


def test_cookie_token_and_exempt():
    sc = _scope(headers={"cookie": "foo=1; jtdt_csrf=TOK; bar=2"})
    assert csrf._cookie_token(sc) == "TOK"
    assert csrf._is_exempt(_scope(headers={"authorization": "Bearer xyz"}))
    assert csrf._is_exempt(_scope(path="/auth/saml/acs"))
    assert not csrf._is_exempt(_scope(path="/tools/x"))


# ---- 整合：middleware 行為 ----

def test_get_sets_cookie_and_state():
    scope, sent = _run(_scope(method="GET", path="/"))
    assert _status(sent) == 200
    assert scope["state"]["csrf_token"]              # 曝露給模板
    assert "jtdt_csrf=" in (_set_cookie(sent) or "")  # 回應補設 cookie


def test_post_without_token_rejected():
    # 帶 cookie 但無 header / 無欄位 → 403
    sc = _scope(headers={"cookie": "jtdt_csrf=TOK",
                         "content-type": "application/json"})
    assert _status(_run(sc, b'{"q":"x"}')[1]) == 403


def test_post_valid_header_passes():
    sc = _scope(headers={"cookie": "jtdt_csrf=TOK", "x-csrf-token": "TOK",
                         "content-type": "application/json"})
    assert _status(_run(sc, b'{"q":"x"}')[1]) == 200


def test_post_mismatched_header_rejected():
    sc = _scope(headers={"cookie": "jtdt_csrf=TOK", "x-csrf-token": "WRONG"})
    assert _status(_run(sc, b"")[1]) == 403


def test_post_form_urlencoded_valid_field_passes():
    sc = _scope(headers={"cookie": "jtdt_csrf=TOK",
                         "content-type": "application/x-www-form-urlencoded"})
    scope, sent = _run(sc, b"username=a&csrf_token=TOK")
    assert _status(sent) == 200
    # body 有被 replay 給下游 handler
    assert any(m.get("body", b"").startswith(b"ok:username=a")
               for m in sent if m["type"] == "http.response.body")


def test_post_form_urlencoded_wrong_field_rejected():
    sc = _scope(headers={"cookie": "jtdt_csrf=TOK",
                         "content-type": "application/x-www-form-urlencoded"})
    assert _status(_run(sc, b"username=a&csrf_token=BAD")[1]) == 403


def test_bearer_exempt():
    sc = _scope(headers={"authorization": "Bearer abc",
                         "content-type": "application/json"})
    assert _status(_run(sc, b'{"q":"x"}')[1]) == 200


def test_saml_acs_exempt():
    sc = _scope(path="/auth/saml/acs",
                headers={"content-type": "application/x-www-form-urlencoded"})
    assert _status(_run(sc, b"SAMLResponse=xxx")[1]) == 200


def test_multipart_without_header_rejected():
    sc = _scope(headers={"cookie": "jtdt_csrf=TOK",
                         "content-type": "multipart/form-data; boundary=x"})
    assert _status(_run(sc, b"--x--")[1]) == 403


def test_disable_flag_bypasses(monkeypatch):
    monkeypatch.setenv("JTDT_CSRF_DISABLE", "1")
    sc = _scope(headers={"cookie": "jtdt_csrf=TOK",
                         "content-type": "application/json"})
    assert _status(_run(sc, b'{"q":"x"}')[1]) == 200   # 旗標開 → 不驗證
