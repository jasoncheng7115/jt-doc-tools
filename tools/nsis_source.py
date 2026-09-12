"""NSIS 原始碼的「只看程式、不看註解」抽取 —— 給守門測試用。

**為什麼要收成一份共用**：這個專案的靜態守門一再被**解釋規則的註解**騙到
（v1.15.28 一輪就三次、加上安裝程式這支共四次）。NSIS 的註解有兩種寫法，
而且**行尾註解最容易漏**：

    ; 整行註解
    Var SM_DIR      ; 行尾註解 ← 只跳過「開頭是 ;」的話，這行會被掃進去

還有一個陷阱：**字串裡也可能有分號**（`MessageBox "a;b"`），所以不能直接
`split(";")`。下面用一個小狀態機追引號。
"""
from __future__ import annotations


def strip_comment(line: str) -> str:
    """去掉 NSIS 的行尾註解（`;` 與 `#`），但不動字串裡的分號。"""
    out = []
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            # NSIS 用 `$\"` 跳脫引號
            if i >= 2 and line[i - 2:i] == "$\\":
                out.append(ch)
                i += 1
                continue
            in_str = not in_str
            out.append(ch)
        elif ch in ";#" and not in_str:
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def code_lines(text: str) -> list[tuple[int, str]]:
    """回傳 `(行號, 只剩程式的那一段)`，空行與純註解行直接略過。"""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        code = strip_comment(raw)
        if code.strip():
            out.append((n, code))
    return out


def code_text(text: str) -> str:
    return "\n".join(c for _, c in code_lines(text))
