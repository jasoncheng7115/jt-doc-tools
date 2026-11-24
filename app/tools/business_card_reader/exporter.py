"""名片辨識：聯絡人匯出（CSV / XLSX / ODS / vCard / JSON）。

規則（照規格）：

* **一人一列**。基本欄位順序見 `COLUMNS`；多值欄位（手機、電話＋分機、傳真、Email、網址、地址＋郵遞區號、
  通訊帳號）依「這批聯絡人裡最多的那一位」動態展開欄數，第 3 筆以後也照樣輸出，**絕不截斷**。
  電話第 N 筆跟分機第 N 筆成對（「電話 2」「分機 2」），地址第 N 筆跟郵遞區號第 N 筆成對。
  某個欄位在整批裡都沒有值時仍保留一欄。
* 狀態為 `deleted` 的欄位一律忽略。單值欄位（姓名、公司…）同一個 key 出現多筆時取第一筆有值的。
* 所有值一律當成**不可信任的字串**：CSV 預設中和公式開頭（`app.core.csv_safe`，值會多一個前置單引號，
  這是刻意的）；XLSX 每一格強制存成文字（電話、分機、郵遞區號、統編不會變數字，`=` 開頭不會變公式）；
  ODS 每一格 `office:value-type="string"`；JSON 是機器交換格式，保留原值不做中和。
* 不輸出任何圖片。

設計取捨：

* **vCard 的 N**：不拆中文姓名的姓與名（拆錯比不拆糟），一律寫成 `N:<全名>;;;;`（全名放在姓的位置），
  顯示名稱由 FN 負責。
* **vCard 的分機**：寫在 TEL 的值裡，純文字「02-2345-6789 ext. 001」，不用 `;ext=`（那是 tel URI 的寫法，
  vCard 3.0 的 TEL 值是純文字，多數通訊錄程式不認得 `;ext=`）。
* **表格模式沒有資料列時**：Excel 表格至少要有標題列＋一列資料，所以 0 位聯絡人時 XLSX 與 ODS 都只寫標題、
  **不建表格 / 篩選範圍**（三種格式的列數因此一致，不會多一列空白）。
* **ODS 不凍結標題列**：凍結要寫 settings.xml 的檢視設定，結構複雜、各版本行為不一，先不做；
  篩選按鈕用 `table:database-range` 的 `table:display-filter-buttons="true"`（LibreOffice / OxOffice 的自動篩選）。
"""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from typing import Any, Iterable, Optional

from app.core import csv_safe as _csv_safe

#: 基本欄位（依序）
COLUMNS = ["name", "name_en", "title", "title_en", "department", "department_en", "company", "company_en", "vat",
           "mobile", "phone", "ext", "fax", "email", "url", "address", "zip", "im",
           "event", "source", "tags", "memo"]

#: 中文欄名
LABELS_ZH = {
    "name": "姓名", "name_en": "外文姓名", "title": "職稱", "title_en": "外文職稱", "department": "部門",
    "department_en": "外文部門", "company": "公司 / 機關",
    "company_en": "外文公司名稱", "vat": "統一編號", "mobile": "手機", "phone": "電話", "ext": "分機",
    "fax": "傳真", "email": "Email", "url": "網址", "address": "地址", "zip": "郵遞區號", "im": "通訊帳號",
    "event": "活動", "source": "來源", "tags": "標籤", "memo": "備註",
}

#: 會依最大筆數展開的欄位
_MULTI = ("mobile", "phone", "fax", "email", "url", "address", "im")
#: 跟著主欄位成對展開的附屬欄位：附屬 → 主欄位
_PAIRED = {"ext": "phone", "zip": "address"}
_PAIR_OF = {v: k for k, v in _PAIRED.items()}          # 主欄位 → 附屬
#: 人工輸入的備註（不是名片上的字）
_NOTE_KEYS = ("event", "source", "tags", "memo")
#: 單值欄位
_SINGLE = ("name", "name_en", "title", "title_en", "department", "department_en", "company", "company_en", "vat")

#: XML 1.0 不允許的控制字元（openpyxl 會丟 IllegalCharacterError，ODS 會產生壞檔）
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


# --------------------------------------------------------------------------- 共用

def _s(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


def _live_fields(contact: dict) -> list[dict]:
    """沒有被刪除的欄位（依原本順序）。"""
    out = []
    for f in contact.get("fields") or []:
        if not isinstance(f, dict) or f.get("status") == "deleted":
            continue
        out.append(f)
    return out


def _values(contact: dict, key: str) -> list[dict]:
    """某個 key 的所有有效欄位（值為空的不算）。"""
    return [f for f in _live_fields(contact) if f.get("key") == key and _s(f.get("value")).strip()]


def _single(contact: dict, key: str) -> str:
    vs = _values(contact, key)
    return _s(vs[0].get("value")) if vs else ""


def _notes(contact: dict) -> dict:
    n = contact.get("notes") or {}
    return n if isinstance(n, dict) else {}


def _tags(contact: dict) -> list[str]:
    t = _notes(contact).get("tags") or []
    if isinstance(t, str):
        t = [t]
    return [_s(x) for x in t if _s(x).strip()]


def _multi_cell(f: dict, key: str) -> str:
    """多值欄位在表格裡的文字。通訊帳號帶標籤時寫成「LINE: abc」；手機 / 傳真有分機時附在後面。"""
    v = _s(f.get("value"))
    if key == "im" and _s(f.get("label")).strip():
        return f"{_s(f.get('label')).strip()}: {v}"
    if key in ("mobile", "fax") and _s(f.get("ext")).strip():
        return f"{v} ext. {_s(f.get('ext')).strip()}"
    return v


def _resolve_columns(columns: Optional[Iterable[str]]) -> list[str]:
    if columns is None:
        return list(COLUMNS)
    cols = []
    for c in columns:
        if c not in COLUMNS:
            raise ValueError(f"不認得的欄位：{c}")
        if c not in cols:
            cols.append(c)
    return cols


def _header(base: str, n: int, lang: str) -> str:
    if lang == "en":
        return base if n == 1 else f"{base}_{n}"
    lab = LABELS_ZH[base]
    return lab if n == 1 else f"{lab} {n}"


def _plan(contacts: list[dict], columns: list[str]) -> list[tuple[str, int]]:
    """欄位配置：[(基本欄位, 第幾筆 1..N), ...]。"""
    counts = {k: max([len(_values(c, k)) for c in contacts] or [0]) for k in _MULTI}
    plan: list[tuple[str, int]] = []
    done: set[str] = set()
    for c in columns:
        if c in done:
            continue
        main = _PAIRED.get(c, c)
        if main in _MULTI:
            sub = _PAIR_OF.get(main)
            with_main = main in columns
            with_sub = sub is not None and sub in columns
            n = max(counts[main], 1)
            for i in range(1, n + 1):
                if with_main:
                    plan.append((main, i))
                if with_sub:
                    plan.append((sub, i))
            done.add(main)
            if sub:
                done.add(sub)
        else:
            plan.append((c, 1))
            done.add(c)
    return plan


def _cell(contact: dict, base: str, i: int, lang: str) -> str:
    if base in _SINGLE:
        return _single(contact, base)
    if base in _MULTI:
        vs = _values(contact, base)
        return _multi_cell(vs[i - 1], base) if i <= len(vs) else ""
    if base in _PAIRED:
        vs = _values(contact, _PAIRED[base])
        return _s(vs[i - 1].get(base)) if i <= len(vs) else ""
    if base == "tags":
        return ("、" if lang != "en" else ", ").join(_tags(contact))
    return _s(_notes(contact).get(base))


def build_rows(contacts, *, columns=None, lang="zh") -> tuple[list[str], list[list[str]]]:
    """（欄名, 資料列）。值是原值（未做公式中和），CSV / XLSX / ODS 各自處理。"""
    contacts = list(contacts or [])
    lang = "en" if lang == "en" else "zh"
    plan = _plan(contacts, _resolve_columns(columns))
    headers = [_header(b, i, lang) for b, i in plan]
    rows = [[_cell(c, b, i, lang) for b, i in plan] for c in contacts]
    return headers, rows


def preview(contacts, *, columns=None, lang="zh", n=5) -> dict:
    """畫面預覽：欄名、前 n 列、總筆數。"""
    headers, rows = build_rows(contacts, columns=columns, lang=lang)
    return {"headers": headers, "rows": rows[:max(0, int(n))], "count": len(rows)}


def _xml_clean(s: str) -> str:
    return _XML_ILLEGAL.sub("", s)


def _disp_width(s: str) -> int:
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _col_widths(headers: list[str], rows: list[list[str]]) -> list[int]:
    out = []
    for ci, h in enumerate(headers):
        longest = _disp_width(h)
        for r in rows:
            for part in r[ci].split("\n"):
                longest = max(longest, _disp_width(part))
        out.append(min(max(longest + 2, 8), 50))
    return out


# --------------------------------------------------------------------------- CSV

#: CSV 可以選的編碼。Big5 用 cp950（Windows 繁中的 Big5，含常用的擴充字）
CSV_ENCODINGS = ("utf-8", "big5")


class EncodingError(ValueError):
    """選了 Big5 但有字 Big5 沒有。`chars` 是那些字（去重、最多 10 個）。不自動換成 ? —— 名字會無聲變錯。"""

    def __init__(self, chars: list[str], total: int):
        super().__init__("big5")
        self.chars, self.total = chars, total


def big5_missing(contacts, *, columns=None, lang="zh") -> list[str]:
    """這些聯絡人的資料裡 Big5 沒有的字（給畫面先講，免得選了才失敗）。"""
    headers, rows = build_rows(contacts, columns=columns, lang=lang)
    bad: list[str] = []
    for cell in [*headers, *(c for r in rows for c in r)]:
        for ch in str(cell):
            if ch not in bad:
                try:
                    ch.encode("cp950")
                except UnicodeEncodeError:
                    bad.append(ch)
    return bad


def export_csv(contacts, *, columns=None, lang="zh", bom=True, safe=True, encoding="utf-8") -> bytes:
    """CSV。`encoding`＝`utf-8`（`bom=True` 時加 BOM，Excel 才認得是 UTF-8）或 `big5`（舊系統、舊版 Excel；不加 BOM）。
    `safe=True` 時 `= + - @`、Tab、CR 開頭（含前置空白後接這些）的值前面補單引號。"""
    if encoding not in CSV_ENCODINGS:
        raise ValueError("encoding")
    headers, rows = build_rows(contacts, columns=columns, lang=lang)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(_csv_safe.row(headers) if safe else headers)
    for r in rows:
        w.writerow(_csv_safe.row(r) if safe else r)
    text = buf.getvalue()
    if encoding == "big5":
        bad = big5_missing(contacts, columns=columns, lang=lang)
        if bad:
            raise EncodingError(bad[:10], len(bad))
        return text.encode("cp950")
    return (("\ufeff" if bom else "") + text).encode("utf-8")


# --------------------------------------------------------------------------- XLSX

#: Excel 表格名稱（必須是合法識別字）
XLSX_TABLE_NAME = "Contacts"
SHEET_NAME = "聯絡人"


def export_xlsx(contacts, *, columns=None, lang="zh", table=False) -> bytes:
    """XLSX。每一格都存成文字（`data_type="s"`、格式 `@`），標題粗體、凍結標題列。

    `table=False`：純資料（沒有篩選）。`table=True`：整個範圍建成 Excel 表格（有篩選與搜尋）；
    0 位聯絡人時只寫標題、不建表格（Excel 表格至少要一列資料）。
    """
    try:
        import openpyxl
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.table import Table, TableStyleInfo
    except ImportError:                                    # pragma: no cover
        raise RuntimeError("openpyxl 未安裝，無法匯出 xlsx")
    headers, rows = build_rows(contacts, columns=columns, lang=lang)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    bold = Font(bold=True)

    def put(r: int, c: int, v: str):
        v = _xml_clean(v)
        cell = ws.cell(row=r, column=c)
        if v == "":
            cell.number_format = "@"
            return cell
        cell.value = v
        cell.data_type = "s"            # 先給值、再強制文字：= 開頭不會變公式、數字不會變數值
        cell.number_format = "@"
        return cell

    for ci, h in enumerate(headers, start=1):
        put(1, ci, h).font = bold
    for ri, r in enumerate(rows, start=2):
        for ci, v in enumerate(r, start=1):
            put(ri, ci, v)
    for ci, wdt in enumerate(_col_widths(headers, rows), start=1):
        ws.column_dimensions[get_column_letter(ci)].width = wdt
    ws.freeze_panes = "A2"
    if table and rows and headers:
        ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
        tab = Table(displayName=XLSX_TABLE_NAME, ref=ref)
        tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=False,
                                            showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        ws.add_table(tab)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# --------------------------------------------------------------------------- 名片總表（中英對照）

#: 「名片總表」版面：公司常見的手工名片清單格式 —— 中英文欄位並列、一人一列、多筆值寫在同一格（換行）。
#: 第 1 列是標題與更新日期、第 2 列是欄名，黃底的欄是手動輸入的（新增日期、性質、取得來源、備註），灰底的欄來自名片。
#: 另外附「性質清單」（性質的值）與空白的「常用」工作表（同樣的欄，給使用者自己挑常用的人）。
#: 匯入時 `importer` 認得這個版面（找第 2 列的欄名、英文欄名對到外文欄位），匯出再匯回不掉資料。
ROSTER_TITLE = "名片總表"
ROSTER_SHEET = "名片總表"
ROSTER_LIST_SHEET = "性質清單"
ROSTER_FAV_SHEET = "常用"
ROSTER_FAV_TITLE = "常用名單"
#: （欄名, 來源, 欄寬）。來源：欄位 key、`added`（新增日期）、`tags`（性質）、`address_zh` / `address_en`（地址依文字分兩欄）、
#: `source`（取得來源＝活動＋來源）、`memo`
ROSTER_COLUMNS = (
    ("新增日期", "added", 10.5), ("性質", "tags", 11.2), ("公司全名", "company", 22.6), ("Company Name", "company_en", 13.9),
    ("部門", "department", 16.0), ("Department", "department_en", 13.6), ("姓名", "name", 13.9),
    ("Full Name", "name_en", 12.8), ("職稱", "title", 14.8), ("Job Title", "title_en", 14.1), ("電話", "phone", 16.0),
    ("傳真", "fax", 17.4), ("手機", "mobile", 17.0), ("Email", "email", 31.4), ("公司網址", "url", 17.0),
    ("地址", "address_zh", 26.1), ("Address", "address_en", 18.6), ("取得來源", "source", 20.2), ("備註", "memo", 24.8),
)
#: 名片上有、但版面沒有的欄位：有人有值才接在最後（不然資料會不見）
ROSTER_EXTRA = (("統一編號", "vat"), ("通訊帳號", "im"))
#: 手動輸入的欄（黃底）
ROSTER_MANUAL = ("新增日期", "性質", "取得來源", "備註")
ROSTER_MANUAL_NOTE = "黃底可手動輸入"
_ROSTER_WIDTH = {h: w for h, _k, w in ROSTER_COLUMNS}
_EMAIL_LINK = re.compile(r"^[^\s@<>\"'()]+@[^\s@<>\"'()]+\.[A-Za-z]{2,24}$")
_URL_LINK = re.compile(r"^(?:https?://|www\.)[^\s<>\"']+$", re.I)


def _roster_date(ts: Any) -> str:
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return ""
    if not (t > 0) or t > 4102444800:          # NaN、負數、2100 年以後都不算
        return ""
    import time as _time
    return _time.strftime("%Y.%m.%d", _time.localtime(t))


def _roster_cell(contact: dict, key: str) -> str:
    if key == "added":
        return _roster_date(contact.get("created_at"))
    if key == "tags":
        return "、".join(_tags(contact))
    if key == "source":
        n = _notes(contact)
        out: list[str] = []
        for k in ("event", "source"):
            v = _s(n.get(k)).strip()
            if v and v not in out:
                out.append(v)
        return "、".join(out)
    if key == "memo":
        return _s(_notes(contact).get("memo"))
    if key in ("address_zh", "address_en"):
        out = []
        for f in _values(contact, "address"):
            v, z = _s(f.get("value")).strip(), _s(f.get("zip")).strip()
            if z and not v.startswith(z):
                v = f"{z} {v}"
            cjk = any(ord(ch) >= 0x2E80 for ch in v)
            if cjk == (key == "address_zh"):
                out.append(v)
        return "\n".join(out)
    if key == "phone":
        out = []
        for f in _values(contact, "phone"):
            v, e = _s(f.get("value")), _s(f.get("ext")).strip()
            out.append(f"{v} ext. {e}" if e else v)
        return "\n".join(out)
    if key in _MULTI:
        return "\n".join(_multi_cell(f, key) for f in _values(contact, key))
    return _single(contact, key)


def roster_rows(contacts) -> tuple[list[str], list[list[str]]]:
    """「名片總表」版面的（欄名, 資料列）。名片有、版面沒有的欄位（統編、通訊帳號）有值才接在最後。"""
    contacts = list(contacts or [])
    cols = [(h, k) for h, k, _w in ROSTER_COLUMNS]
    cols += [(h, k) for h, k in ROSTER_EXTRA if any(_values(c, k) for c in contacts)]
    return [h for h, _k in cols], [[_roster_cell(c, k) for _h, k in cols] for c in contacts]


def is_roster_headers(headers) -> bool:
    """這組欄名是不是「名片總表」版面（大部分的欄名都對得上）。"""
    have = {_s(h).strip().casefold() for h in headers or []}
    hit = sum(1 for h, _k, _w in ROSTER_COLUMNS if h.casefold() in have)
    return hit >= 12 and "公司全名" in have and "company name" in have


def write_roster_xlsx(headers: list[str], rows: list[list[str]], *, today: Optional[str] = None) -> bytes:
    """照「名片總表」版面寫 XLSX（欄名、資料列可以是 `roster_rows` 的結果，也可以是匯入的名片總表整合後的表格）。

    每一格都存成文字（`=` 開頭不會變公式、電話開頭的 0 不會不見）；多筆值換行；只有單一、格式正確的 Email /
    以 http(s):// 或 www. 開頭的網址才加超連結（其他值不加，避免 `javascript:` 之類的連結）。"""
    try:
        import openpyxl
        from openpyxl.comments import Comment
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError:                                    # pragma: no cover
        raise RuntimeError("openpyxl 未安裝，無法匯出 xlsx")
    import time as _time
    headers = [_xml_clean(_s(h)) for h in headers or []]
    width = max([len(headers)] + [len(r) for r in rows or []])
    headers += [""] * (width - len(headers))
    rows = [[_xml_clean(_s(v)) for v in r] + [""] * (width - len(r)) for r in rows or []]
    today = today or _time.strftime("%Y.%m.%d", _time.localtime())
    font = "新細明體"
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    yellow, gray = PatternFill("solid", fgColor="FFFFFF00"), PatternFill("solid", fgColor="FFCCCCCC")
    f_title, f_date = Font(name=font, size=16, bold=True), Font(name=font, size=11, bold=True)
    f_head, f_body = Font(name=font, size=12, bold=True), Font(name=font, size=12)
    f_link = Font(name=font, size=12, color="FF0000FF", underline="single")
    a_head = Alignment(horizontal="center", vertical="center", wrap_text=True)
    a_body = Alignment(vertical="center", wrap_text=True)
    a_center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def text(cell, v: str):
        cell.number_format = "@"
        if v != "":
            cell.value = v
            cell.data_type = "s"            # 先給值、再強制文字
        return cell

    def head_row(ws, r: int):
        for ci, h in enumerate(headers, start=1):
            c = text(ws.cell(row=r, column=ci), h)
            c.font, c.alignment, c.border = f_head, a_head, border
            c.fill = yellow if h in ROSTER_MANUAL else gray
            if h in ROSTER_MANUAL:
                c.comment = Comment(ROSTER_MANUAL_NOTE, "jt-doc-tools")
        ws.row_dimensions[r].height = 36

    widths = _col_widths(headers, rows)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = ROSTER_SHEET
    text(ws["A1"], ROSTER_TITLE).font = f_title
    text(ws["C1"], f"更新日期: {today}").font = f_date
    ws.row_dimensions[1].height = 35.25
    head_row(ws, 2)
    link_cols = {ci: h for ci, h in enumerate(headers, start=1) if h in ("Email", "公司網址")}
    for ri, r in enumerate(rows, start=3):
        for ci, v in enumerate(r, start=1):
            c = text(ws.cell(row=ri, column=ci), v)
            c.font, c.border = f_body, border
            c.alignment = a_center if headers[ci - 1] == "新增日期" else a_body
            if ci in link_cols and v and "\n" not in v:
                if link_cols[ci] == "Email" and _EMAIL_LINK.match(v):
                    c.hyperlink, c.font = "mailto:" + v, f_link
                elif link_cols[ci] == "公司網址" and _URL_LINK.match(v):
                    c.hyperlink, c.font = (v if v.lower().startswith("http") else "https://" + v), f_link
    for ci, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(ci)].width = _ROSTER_WIDTH.get(h) or widths[ci - 1]
    ws.freeze_panes = "A3"
    last = get_column_letter(max(1, width))
    ws.auto_filter.ref = f"A2:{last}{max(2, len(rows) + 2)}"

    # 性質清單：表裡出現過的性質（一格可以有好幾個，用「、」分開）
    tags: list[str] = []
    if "性質" in headers:
        ti = headers.index("性質")
        for r in rows:
            for t in re.split(r"\s*[、,，;；\n]\s*", r[ti]):
                if t and t not in tags:
                    tags.append(t)
    wl = wb.create_sheet(ROSTER_LIST_SHEET)
    c = text(wl["A2"], "性質")
    c.font, c.alignment, c.border, c.fill = f_head, a_head, border, yellow
    c.comment = Comment(ROSTER_MANUAL_NOTE, "jt-doc-tools")
    for i, t in enumerate(tags, start=3):
        c = text(wl.cell(row=i, column=1), t)
        c.font, c.border = f_body, border
    wl.column_dimensions["A"].width = max([11.25] + [_disp_width(t) + 2 for t in tags])
    wl.freeze_panes = "A3"

    # 常用：同樣的欄、空白（使用者自己挑常用的人貼進來）
    wf = wb.create_sheet(ROSTER_FAV_SHEET)
    text(wf["A1"], ROSTER_FAV_TITLE).font = f_title
    wf["C1"] = f"={ROSTER_SHEET}!C1"           # 跟著名片總表的更新日期（這一格是我們寫的公式，不是使用者資料）
    wf["C1"].font = f_date
    wf.row_dimensions[1].height = 35.25
    head_row(wf, 2)
    for ci, h in enumerate(headers, start=1):
        wf.column_dimensions[get_column_letter(ci)].width = _ROSTER_WIDTH.get(h) or widths[ci - 1]
    wf.freeze_panes = "A3"
    wf.auto_filter.ref = f"A2:{last}2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_roster_xlsx(contacts, *, today: Optional[str] = None) -> bytes:
    """「名片總表」版面的 XLSX。"""
    headers, rows = roster_rows(contacts)
    return write_roster_xlsx(headers, rows, today=today)


# --------------------------------------------------------------------------- ODS

def _ods_range(sheet: str, ncols: int, nrows: int) -> str:
    from openpyxl.utils import get_column_letter
    q = "'" + sheet.replace("'", "''") + "'"
    return f"{q}.A1:{q}.{get_column_letter(ncols)}{nrows}"


def export_ods(contacts, *, columns=None, lang="zh", table=False) -> bytes:
    """原生 OpenDocument 試算表（odfpy，不是改副檔名的 xlsx）。欄位配置與 XLSX 相同。

    每一格 `office:value-type="string"`。`table=True` 時加 `table:database-range`（顯示篩選按鈕）；
    0 位聯絡人時不加。不凍結標題列（見模組說明）。
    """
    try:
        from odf import teletype
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.style import Style, TableColumnProperties, TextProperties
        from odf.table import DatabaseRange, DatabaseRanges, Table, TableCell, TableColumn, TableRow
        from odf.text import P
    except ImportError:                                    # pragma: no cover
        raise RuntimeError("odfpy 未安裝，無法匯出 ods")
    headers, rows = build_rows(contacts, columns=columns, lang=lang)
    doc = OpenDocumentSpreadsheet()
    hdr = Style(name="BcHeader", family="table-cell")
    hdr.addElement(TextProperties(fontweight="bold"))
    doc.automaticstyles.addElement(hdr)

    sheet = Table(name=SHEET_NAME)
    for ci, wdt in enumerate(_col_widths(headers, rows)):
        cs = Style(name=f"BcCol{ci + 1}", family="table-column")
        cs.addElement(TableColumnProperties(columnwidth=f"{wdt * 0.21:.2f}cm"))
        doc.automaticstyles.addElement(cs)
        sheet.addElement(TableColumn(stylename=cs))

    def cell(v: str, style=None):
        kw = {"valuetype": "string"}
        if style is not None:
            kw["stylename"] = style
        tc = TableCell(**kw)
        p = P()
        teletype.addTextToElement(p, _xml_clean(v))
        tc.addElement(p)
        return tc

    tr = TableRow()
    for h in headers:
        tr.addElement(cell(h, hdr))
    sheet.addElement(tr)
    for r in rows:
        tr = TableRow()
        for v in r:
            tr.addElement(cell(v))
        sheet.addElement(tr)
    doc.spreadsheet.addElement(sheet)
    if table and rows and headers:
        drs = DatabaseRanges()
        drs.addElement(DatabaseRange(name="Contacts",
                                     targetrangeaddress=_ods_range(SHEET_NAME, len(headers), len(rows) + 1),
                                     displayfilterbuttons="true", containsheader="true"))
        doc.spreadsheet.addElement(drs)
    buf = io.BytesIO()
    doc.write(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- vCard

def _vesc(s: str) -> str:
    """RFC 2426 文字值跳脫：\\ , ; 與換行。"""
    s = _s(s).replace("\r\n", "\n").replace("\r", "\n")
    return (s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """超過 75 個位元組就折行（CRLF＋一個空白），不切斷 UTF-8 多位元組字元。"""
    out, cur, size = [], [], 0
    limit = 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            out.append("".join(cur))
            cur, size = [ch], n
            limit = 74                      # 後續行開頭的空白佔一個位元組
        else:
            cur.append(ch)
            size += n
    out.append("".join(cur))
    return "\r\n ".join(out)


def _tel_value(f: dict) -> str:
    v = _s(f.get("value")).strip()
    ext = _s(f.get("ext")).strip()
    return f"{v} ext. {ext}" if ext else v


def export_vcf(contacts) -> bytes:
    """vCard 3.0（UTF-8、CRLF）。呼叫端已經過濾好要匯出的聯絡人，這裡照給的全部輸出。"""
    lines: list[str] = []
    for c in contacts or []:
        name, name_en, company = _single(c, "name"), _single(c, "name_en"), _single(c, "company")
        fn = name or name_en or company
        lines.append("BEGIN:VCARD")
        lines.append("VERSION:3.0")
        lines.append("FN:" + _vesc(fn))
        lines.append("N:" + _vesc(name or name_en) + ";;;;")
        if name and name_en:
            lines.append("X-JTDT-NAME-EN:" + _vesc(name_en))
        # 只有外文的（英文名片）：通訊錄標準欄位用外文的值，另外記在 X-JTDT-*-EN（讀回來時認得是外文欄位）
        company_en, dept_en = _single(c, "company_en"), _single(c, "department_en")
        title, title_en = _single(c, "title"), _single(c, "title_en")
        dept = _single(c, "department") or dept_en
        org = company or company_en
        if org or dept:
            lines.append("ORG:" + _vesc(org) + (";" + _vesc(dept) if dept else ""))
        if company_en:
            lines.append("X-JTDT-COMPANY-EN:" + _vesc(company_en))
        if dept_en:
            lines.append("X-JTDT-DEPARTMENT-EN:" + _vesc(dept_en))
        if title or title_en:
            lines.append("TITLE:" + _vesc(title or title_en))
        if title_en:
            lines.append("X-JTDT-TITLE-EN:" + _vesc(title_en))
        for key, typ in (("mobile", "CELL"), ("phone", "WORK,VOICE"), ("fax", "WORK,FAX")):
            for f in _values(c, key):
                lines.append(f"TEL;TYPE={typ}:" + _vesc(_tel_value(f)))
        for f in _values(c, "email"):
            lines.append("EMAIL;TYPE=INTERNET:" + _vesc(_s(f.get("value")).strip()))
        for f in _values(c, "url"):
            lines.append("URL:" + _vesc(_s(f.get("value")).strip()))
        for f in _values(c, "address"):
            lines.append("ADR;TYPE=WORK:;;" + _vesc(_s(f.get("value"))) + ";;;" + _vesc(_s(f.get("zip"))) + ";")
        for f in _values(c, "im"):
            lab = _s(f.get("label")).strip()
            lines.append("X-JTDT-IM:" + _vesc((lab + ": " if lab else "") + _s(f.get("value"))))
        if _single(c, "vat"):
            lines.append("X-JTDT-VAT:" + _vesc(_single(c, "vat")))
        # NOTE 只放人工輸入的備註，不放名片上的字
        n = _notes(c)
        parts = []
        if _s(n.get("event")).strip():
            parts.append("活動：" + _s(n.get("event")).strip())
        if _s(n.get("source")).strip():
            parts.append("來源：" + _s(n.get("source")).strip())
        if _tags(c):
            parts.append("標籤：" + "、".join(_tags(c)))
        if _s(n.get("memo")).strip():
            parts.append(_s(n.get("memo")).strip())
        if parts:
            lines.append("NOTE:" + _vesc("\n".join(parts)))
        lines.append("END:VCARD")
    return "".join(_fold(ln) + "\r\n" for ln in lines).encode("utf-8")


# --------------------------------------------------------------------------- JSON

_PLURAL = {"mobile": "mobiles", "phone": "phones", "fax": "faxes", "email": "emails",
           "url": "urls", "address": "addresses", "im": "ims"}


def _item(f: dict, key: str, include_source: bool) -> dict:
    d = {"kind": key, "value": _s(f.get("value"))}
    for k in ("ext", "label", "norm", "raw", "zip"):
        if _s(f.get(k)):
            d[k] = _s(f.get(k))
    d["status"] = _s(f.get("status"))
    if include_source and isinstance(f.get("source"), dict):
        d["source"] = f["source"]
    return d


def export_json(contacts, *, include_source=False) -> bytes:
    """JSON（UTF-8、不轉義非 ASCII）：保留多值與型別，值是原值。來源資訊（框位等）只在 `include_source=True` 時輸出。"""
    out = []
    for c in contacts or []:
        d: dict[str, Any] = {"id": _s(c.get("id")), "status": _s(c.get("status"))}
        for k in _SINGLE:
            d[k] = _single(c, k)
        d["note"] = _single(c, "note")
        for k in _MULTI:
            d[_PLURAL[k]] = [_item(f, k, include_source) for f in _values(c, k)]
        n = _notes(c)
        d["notes"] = {"event": _s(n.get("event")), "source": _s(n.get("source")),
                      "tags": _tags(c), "memo": _s(n.get("memo"))}
        d["created_at"] = c.get("created_at")
        d["updated_at"] = c.get("updated_at")
        if include_source:
            d["fields"] = [{"id": _s(f.get("id")), "key": _s(f.get("key")), "value": _s(f.get("value")),
                            "status": _s(f.get("status")), "source": f.get("source")}
                           for f in _live_fields(c)]
        out.append(d)
    return json.dumps({"contacts": out}, ensure_ascii=False, indent=2).encode("utf-8")
