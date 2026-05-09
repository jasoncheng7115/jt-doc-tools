"""Regression tests for pdf_editor._looks_garbled().

Catches the 4 signals:
  a) Suspicious symbols (math operators, technical symbols, dingbats, PUA, ...)
  b) Lots of CJK with zero common Taiwan particle chars
  c) Long single-char run (8+ same letter/digit)
  d) Short cycle (period 2-4) repeating 4+ times — leader dots "....." → "eeoeeoeeo"
"""
import pytest
from app.tools.pdf_editor.router import _looks_garbled


# ---------- normal text — must NOT be flagged ----------

@pytest.mark.parametrize("text", [
    "",
    "登入系統",          # short normal
    "在幾分鐘內完成漏洞修補管理",   # heading
    "Action 1 在幾分鐘內完成漏洞修補管理",  # mixed
    "防火牆配置",
    "公司名稱：Softnext",
    "ABC123",            # short serial
    "v1.5.1",
    "192.168.1.10",      # IP
    "AB1234567890",      # alphanumeric serial — should NOT trigger d)
    "ABABCD",            # period-2 once — must NOT trigger d)
    "ABABABCD",          # period-2 only 3x — at boundary, must not trigger
    "Action 1",
    "目錄",
    "一. Action 1 在幾分鐘內完成漏洞修補管理",
    # 客戶 PDF 踩過：TOC 整行 = heading + leader dots + 頁碼。
    # leader dots「..........」是合法排版元素，不是字型 garbled。
    # 必須 NOT garbled，否則會觸發 OCR fallback 把 dots 認成「eeee」回前端。
    "一 . Action 1 在幾分鐘內完成漏洞修補管理 .......................................................................... 2",
    "目錄..............3",
    "Chapter 1 ............................. 5",
    "----- 分隔線 -----",   # 純標點 cycle，不該 garbled
    "***** important *****",
])
def test_normal_text_not_garbled(text):
    assert _looks_garbled(text) is False, \
        f"false positive: {text!r} flagged as garbled"


# ---------- Signal a) suspicious symbols ----------

@pytest.mark.parametrize("text", [
    "翕⊕ㄱ 戔ㄱ",         # PUA + math operator + bopomofo
    "ABC ⊕ XYZ",         # math operator
    "▢▢▢ 文字",          # geometric shapes
])
def test_suspicious_symbols_garbled(text):
    assert _looks_garbled(text) is True


# ---------- Signal c) single-char run ----------

@pytest.mark.parametrize("text", [
    "eeeeeeee",          # 8 e's
    "00000000",
    ".aaaaaaaaa.",
])
def test_long_run_garbled(text):
    assert _looks_garbled(text) is True


# ---------- Signal d) short cycle repeating ----------
# 客戶 issue (#5 之後 pdf-editor 報的) — leader dots 在 Identity-H 字型
# 的 ToUnicode 變成「eeoeeoeeo...」/「ee®ee®ee®...」週期重複。

@pytest.mark.parametrize("text", [
    "eeoeeoeeoeeo",      # period-3 × 4 — has letters
    "eeoeeoeeoeeoeeoeeoeeoeeoeeoeeoeeoeeo",  # very long
    "ee®ee®ee®ee®",      # period-3 × 4 with letter + non-ASCII
    "abababab",          # period-2 × 4
])
def test_short_cycle_garbled(text):
    assert _looks_garbled(text) is True, \
        f"missed: {text!r} should be flagged"


def test_garbled_in_middle_of_normal():
    """Mixed: normal text followed by REAL letter-cycle garbage — backend
    should flag the WHOLE span as unreliable so OCR fallback kicks in."""
    text = "一. Action 1 在幾分鐘內完成漏洞修補管理 eeoeeoeeoeeoeeoeeoeeo 2"
    assert _looks_garbled(text) is True


def test_leader_dots_not_garbled():
    """合法 TOC 行：含長串 leader dots 不該被 flag (v1.5.2 修正前曾 false-positive
    觸發 OCR 把 dots 認成 'eeee' 回前端)。"""
    cases = [
        "............",
        ".....2",
        "------",
        "******",
        "@@.@@.@@.@@.",   # punctuation cycle, no letters/digits
    ]
    for text in cases:
        assert _looks_garbled(text) is False, \
            f"{text!r} should NOT be flagged (pure punctuation cycle)"
