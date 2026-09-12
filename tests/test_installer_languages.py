"""Windows 安裝程式在英文 Windows 上要顯示英文（v1.15.27）。

**為什麼要有這支測試**：`installer.nsi` 宣告了繁中與英文兩種語言，但有一批
字串是**寫死中文**的 —— 元件清單、安裝失敗訊息，以及最糟的**解除安裝的三個
對話框**（那條路徑在語言對話框之前就 `Return`，英文使用者被問「要不要順便
刪掉你的資料」時看到的是一整段看不懂的中文）。

**為什麼不能靠 makensis**：實測過了 —— 拿掉某個 LangString 的英文條目，
`makensis -V4` **一句警告都不出**。「編譯沒警告」對這件事完全沒有保證。

**為什麼不能靠執行期驗證**：NSIS 在啟動時就依系統語系選定語言表，
`StrCpy $LANGUAGE` 之後改不動（在 zh-TW 的 Windows 上實測：強制 1033 之後
取出來的仍是中文）。所以除非真的有一台英文 Windows，否則看不到英文畫面 ——
判準只好放在原始碼上。
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.repo_paths import public_root

NSI = public_root(Path(__file__).resolve().parents[1]) / "packaging" / "windows" / "installer.nsi"

_CJK = re.compile(r"[　-〿一-鿿＀-￯]")

# **刻意保留的兩個中文字面值**（v1.15.31 之後只剩這兩個）：
#
#   1. `!define APPNAME` —— 只當 LangString 的預設值與舊版相容用，
#      實際顯示走 `$(APP_DISPLAY)`。
#   2. `!define LEGACY_SM_FOLDER` —— v1.15.30 以前的安裝把開始功能表資料夾
#      寫死成這個中文名。**解除安裝必須認得它**，不然從舊版升級上來的人會
#      留下一個刪不掉的資料夾。這是「舊資料的形狀」，不是顯示文字。
#
# 名字同時是路徑的那幾處（開始功能表資料夾、兩個捷徑檔名）已經改成
# 「安裝時記進登錄檔、解除安裝時讀回來」，所以不再需要寫死中文。
_DEFERRED = (
    '!define APPNAME',
    '!define LEGACY_SM_FOLDER',
)


def _lines() -> list[tuple[int, str]]:
    return list(enumerate(NSI.read_text(encoding="utf-8").splitlines(), 1))


def _code_lines() -> list[tuple[int, str]]:
    """去掉註解 —— 註解裡寫中文是對的，掃進去就是誤報。

    **行尾註解與字串裡的分號都要處理**（`MessageBox "a;b"`）—— 原本這裡用
    `split(" ;")`，對 `Var X ; 中文` 這種沒有空格差異的寫法不夠穩。
    邏輯已收進 `tools/nsis_source`，兩支安裝程式的守門共用同一份。
    """
    from tools.nsis_source import code_lines as _cl
    return _cl(NSI.read_text(encoding="utf-8"))


def test_every_langstring_covers_every_declared_language():
    text = NSI.read_text(encoding="utf-8")
    langs = set(re.findall(r'!insertmacro\s+MUI_LANGUAGE\s+"(\w+)"', text))
    assert langs >= {"TradChinese", "English"}, f"宣告的語言：{langs}"
    want = {lang.upper() for lang in langs}

    seen: dict[str, set[str]] = {}
    for _, ln in _code_lines():
        m = re.match(r'\s*LangString\s+(\w+)\s+\$\{LANG_(\w+)\}', ln)
        if m:
            seen.setdefault(m.group(1), set()).add(m.group(2).upper())

    assert seen, "一條 LangString 都沒解析到，掃描器壞了"
    missing = {name: sorted(want - got) for name, got in seen.items() if want - got}
    assert not missing, f"這些字串少了某個語言的版本：{missing}"


def test_no_message_box_hard_codes_chinese():
    """對話框寫死中文 = 英文 Windows 上看到看不懂的字。"""
    bad = [f"{n}: {ln.strip()[:70]}" for n, ln in _code_lines()
           if "MessageBox" in ln and _CJK.search(ln)]
    assert not bad, "MessageBox 要走 $(LangString)：\n" + "\n".join(bad)


def test_component_names_are_not_hard_coded_chinese():
    """`Section "名字"` 是編譯期字面值 → 要用 SectionSetText 在執行期填。"""
    bad = [f"{n}: {ln.strip()[:70]}" for n, ln in _code_lines()
           if re.match(r'\s*Section\s+"', ln) and _CJK.search(ln)]
    assert not bad, "元件名稱要走 SectionSetText + LangString：\n" + "\n".join(bad)


def test_the_deferred_places_are_the_only_hard_coded_chinese_left():
    """剩下的中文只能是那兩處**路徑**；新增第三處就要紅。

    這條的用意是**不讓下一個人再寫死一句中文**，而不是要求現在就全部改完。
    """
    bad = []
    for n, ln in _code_lines():
        if not _CJK.search(ln):
            continue
        if re.match(r'\s*LangString\s+\w+\s+\$\{LANG_TRADCHINESE\}', ln):
            continue          # 繁中的譯文本來就是中文
        if any(k in ln for k in _DEFERRED):
            continue
        bad.append(f"{n}: {ln.strip()[:80]}")
    assert not bad, ("安裝程式裡又出現寫死的中文（英文 Windows 會看到）：\n"
                     + "\n".join(bad))


def test_the_uninstall_prompts_go_through_langstrings():
    """解除安裝那條路不經語言對話框 —— 但它照樣依系統語系選表，所以
    只要走 LangString 就會是英文。這裡釘住那三句真的有被翻譯。"""
    text = NSI.read_text(encoding="utf-8")
    for name in ("UN_ASK_PURGE", "UN_NO_DIR", "UN_NOT_OURS"):
        assert f"$({name})" in text, f"{name} 沒有被用到"
        assert re.search(rf'LangString\s+{name}\s+\$\{{LANG_ENGLISH\}}', text), \
            f"{name} 沒有英文版本"
