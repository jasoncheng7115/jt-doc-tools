"""稽核轉送的訊框格式（外部稽核 F07，v1.15.28）。

**這個模組原本一支測試都沒有** —— 所以「GELF TCP 用換行當分隔符」這種
協定層面的錯可以一直活著：socket 不會報錯、我們的日誌也不會有異常，
只有收件端（Graylog）沉默地收不到或解析錯。

Graylog 的規定：**GELF TCP 以 null byte（`\\0`）分隔訊息，且訊息內不可以有
原始換行**。UDP 則是一個封包一則訊息，不加分隔符。
syslog / CEF 用換行（RFC 6587 non-transparent framing）——
那兩種在客戶端是好的，修 GELF 時**不可以順手改掉**。
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from app.core import audit_forward as af

_EVENT = {
    "id": 42,
    "ts": 1_800_000_000.5,
    "username": "王小明@local",
    "ip": "192.0.2.10",
    "event_type": "login_success",
    "target": "",
    "details_json": '{"note": "含中文與\\n換行"}',
}


def _framed(fmt: str, transport: str) -> bytes:
    body = af._FORMATTERS[fmt](_EVENT)
    return af._frame(fmt, transport, body)


def test_gelf_over_tcp_is_null_terminated():
    out = _framed("gelf", "tcp")
    assert out.endswith(b"\x00"), (
        f"GELF TCP 必須用 null byte 分隔，實際結尾是 {out[-1:]!r}")
    assert b"\n" not in out, "GELF 訊息內不可以有原始換行"


def test_gelf_over_udp_has_no_terminator():
    out = _framed("gelf", "udp")
    assert not out.endswith(b"\x00")
    assert not out.endswith(b"\n"), "UDP 一個封包一則訊息，不該補分隔符"
    json.loads(out.decode("utf-8"))          # 整包就是一份合法 JSON


def test_gelf_body_is_valid_json_and_keeps_chinese():
    body = json.loads(_framed("gelf", "udp").decode("utf-8"))
    assert body["version"] == "1.1"
    assert body["_user"] == "王小明@local", "中文不可以被轉成 \\uXXXX 逃脫序列"
    assert body["_event_id"] == 42


@pytest.mark.parametrize("fmt", ["syslog", "cef"])
@pytest.mark.parametrize("transport", ["udp", "tcp"])
def test_syslog_and_cef_still_end_with_a_newline(fmt, transport):
    """回歸：修 GELF 不可以動到這兩種（客戶端目前是好的）。"""
    out = _framed(fmt, transport)
    assert out.endswith(b"\n")
    assert not out.endswith(b"\x00")


def test_cef_has_exactly_one_newline_at_the_end():
    """CEF 的 extension 值裡不可以有換行（規格要求），結尾只能有一個。"""
    out = _framed("cef", "tcp")
    assert out.count(b"\n") == 1


def test_an_unknown_combination_falls_back_to_newline():
    """沒列進表的組合退回換行 —— 不可以變成「沒有分隔符」而把訊息黏在一起。"""
    assert af._frame("syslog", "weird", b"x") == b"x\n"


def test_the_formatters_do_not_frame_themselves():
    """訊框只能有一個地方決定。

    原本三種格式各自在 formatter 裡 `+ "\\n"`，於是「GELF 要 null byte」
    這件事得在三個地方記住。判準走 **AST**：formatter 的函式體裡不可以出現
    含換行 / null 的字面值（用字串比對會掃到說明文字）。
    """
    src = pathlib.Path(af.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("_format_"):
            continue
        # **跳過 docstring** —— 多行說明本身就含換行，而且我們的說明正好在
        # 解釋這條規則（CLAUDE.md 記過：靜態掃描不可以連註解 / 說明一起掃）。
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    if "\n" in sub.value or "\x00" in sub.value:
                        bad.append(f"{node.name}:{sub.lineno}")
    assert not bad, f"formatter 自己加了訊框分隔符（要交給 _frame）：{bad}"


def test_the_drain_loop_frames_before_sending():
    """真正送出去的那條路必須經過 `_frame` —— 不然這些測試等於沒守到東西。"""
    src = pathlib.Path(af.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_frame"]
    assert calls, "送出前沒有呼叫 _frame"
