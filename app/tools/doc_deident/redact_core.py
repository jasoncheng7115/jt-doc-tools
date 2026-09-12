"""去識別化的「真刪除」—— 文字**與圖片像素**都要清掉。

**為什麼要有這支共用模組**（v1.15.28，外部稽核 F01）：

原本網頁與 API 兩條路各有一份一模一樣的 redaction 程式碼，兩邊都寫著
`page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)` ——
**那個參數明確要求「不要動圖片」**，而 `images=PDF_REDACT_IMAGE_PIXELS`
（清掉框內的圖片像素）**本來就是 PyMuPDF 的預設值**。也就是說那行程式碼
是特地把安全的預設關掉。

後果在「掃描圖片 ＋ 隱形 OCR 文字層」的 PDF 上最嚴重 —— **而那正是我們自己
`pdf-ocr` 產出的形狀**：

* 文字層被刪掉 → `get_text()` 抽不到、畫面有黑框 → **看起來完全正常**
* 但頁面上的圖片一個位元都沒動 → 把圖抽出來重新 OCR，個資**完整還原**

**這個參數在不同工具裡的正確值是相反的**，所以不可以互抄：

| 工具 | 目的 | 正確值 |
|---|---|---|
| `pdf-editor` | 把文字移走，**底下的 logo 要留著** | `IMAGE_NONE`（v1.7.29 的兩階段做法） |
| `doc-deident` | **把內容毀掉** | 預設 `IMAGE_PIXELS` |

這段程式碼就是從 pdf-editor 抄過來的（CHANGELOG v1.7.26~7.29 有那段歷史），
抄的時候沒有跟著想「這支工具的目的是相反的」。

## 副作用：檔案會變大 —— 這是刻意接受的，不要用重壓去救

清像素會讓 MuPDF **重新編碼整張圖，而且輸出 PNG**。真實掃描件（有雜訊）實測
**2.3 MB → 5.5 MB（2.4 倍）**。

**試過把原本是 JPEG 的圖再編回 JPEG，結論是不值得**（q=75~85、optimize
開關都實測過）：

| 做法 | 檔案 | 每頁成本 |
|---|---|---|
| 只清像素 | 2.39× | 0.95 s |
| ＋編回 JPEG q85 | 1.86~2.00× | **4.1~4.6 s** |
| ＋編回 JPEG q75 | 1.69× | 4.3 s |

**多花 3 秒/頁只換到 20~30%**，50 頁的掃描件要多等兩分半，而且是第二次
有損壓縮。檔案大小是 **`pdf-compress` 那支工具的職責**（它有 DPI 與品質
選項、也處理過 SMask 透明圖那個雷），這裡只負責把資料真的刪掉 ——
**結果頁要告訴使用者檔案可能變大、要縮小請用哪支工具**。
"""
from __future__ import annotations

import logging

import fitz

log = logging.getLogger(__name__)



def apply_page_redactions(page: fitz.Page, rects: list[fitz.Rect], *,
                          fill: tuple[float, float, float] | None) -> int:
    """把 `rects` 的內容從這一頁真的刪掉（文字、線條**與圖片像素**）。

    `fill` 給顏色就畫上覆蓋色塊（編修模式的黑條）；給 `None` 則不畫
    —— 遮罩 / 替換模式要把新的字貼在原處，畫白底會在有底色或圖片的頁面上
    浮出一塊難看的方塊。

    **注意**：`fill=None` 時圖片像素**照樣會被清掉**（變白）。這是刻意的
    —— 遮罩模式若把 `0912****678` 貼在「原始掃描像素」上面，原始的號碼還在
    圖裡撈得到，那就不叫遮罩了。

    回傳實際套用的區域數（給呼叫端記錄用）。
    """
    if not rects:
        return 0
    for rect in rects:
        if fill is None:
            page.add_redact_annot(rect)
        else:
            page.add_redact_annot(rect, fill=fill)

    # `images` 不傳就是預設的 PDF_REDACT_IMAGE_PIXELS —— **顯式寫出來**，
    # 並且註明不可以改成 IMAGE_NONE（那正是 F01 的成因）。
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
    return len(rects)
