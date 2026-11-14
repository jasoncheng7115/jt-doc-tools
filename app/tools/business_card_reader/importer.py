"""名片辨識：匯入既有聯絡人清單（CSV / XLSX / ODS，以及本工具匯出的 VCF / JSON），跟名片辨識結果比對、產生合併計畫與合併後的表格。

流程（全部是純函式，不碰網頁與資料庫）：

1. `parse_table`：讀檔 → 欄名＋資料列（每一格都是去掉前後空白的字串）。
2. `auto_map`：依欄名猜每一欄對到哪個欄位（`TARGETS`）；認不得的欄不對應，輸出時原樣保留。
   畫面讓使用者改對應之後，用 `clean_mapping` 驗過再用。
3. `rows_to_contacts`：每一列 → 跟名片聯絡人同形狀的欄位清單（`source.method="import"`）。
4. `merge_plan`：每位名片聯絡人（`excluded` 的略過）找清單裡的同一個人，標出新增 / 相同 / 補值 / 衝突。
   `possible_pairs` 另外列出「可能是同一人但規則判斷不了」的配對，留給之後選用的 LLM 檢查。
5. `build_merged`：照使用者的決定產生合併後的表格（原本的列與欄全部保留、順序不變）；`write_table` 寫成檔案。

規則與取捨：

* **讀檔一律當成不可信任的資料**：大小、列數、欄數先設上限；XLSX / ODS 先過 `zip_guard`（上限比全站通用值嚴，
  見 `MAX_UNPACKED`）；XLSX 用 `data_only=True` 只讀存好的值，**不計算公式**；ODS 的重複列 / 重複欄屬性有上限，
  不會因為「重複 100 萬次的空白列」展開成 100 萬列。任何時候都不把儲存格內容印出或寫進日誌。
* **VCF / JSON**（之前匯出的檔，或手機 / 通訊錄匯出的 vCard）：先讀成聯絡人，再用 `exporter.build_rows`
  排成跟匯出一樣的表格（中文欄名），之後的對應、比對、合併都跟試算表同一條路；下載時可以再存回 VCF / JSON。
* **我們自己匯出的檔要能原樣匯回**：欄名認得 `exporter` 的中英文欄名（`姓名`、`電話 2`、`phone_2`…），
  CSV 匯出時為了防公式而補的前置單引號，匯入時拿掉；「LINE: abc」拆回標籤與值；手機 / 傳真的「ext. 001」拆回分機。
* **電話與分機、地址與郵遞區號依出現順序成對**（第 N 個電話欄配第 N 個分機欄），跟匯出的配置一致。
* **姓名欄位**：清單只有一個姓名欄（沒有外文姓名欄）、或只有「名字 / 姓氏」這類分開的欄時，那幾欄是「彈性姓名欄」：
  內容是英文就當外文姓名，否則當姓名。這樣英文通訊錄（Outlook、Google）跟名片上的中文姓名不會被誤判成衝突。
* **只比對清單有的欄位**：名片有、清單整個沒有那一欄的值（例如清單沒有統編欄）不算差異，合併時也不會替既有的列
  另外加欄；新增的聯絡人才會為清單沒有的欄位加欄（不然資料會不見）。
* **多值欄位**（手機、電話、Email…）：名片多出來的值算「補值」，寫進同類型的下一個空欄，沒有空欄就在最後加欄
  （欄名照匯出的寫法，例如「電話 3」）。手機號碼比對時跟電話欄一起看（Google 通訊錄把手機放在電話欄）。
* **衝突**只發生在兩邊都有值而且不一樣的單值欄位（職稱、公司…）；預設保留清單的值。
"""
from __future__ import annotations

import csv
import difflib
import io
import os
import re
import unicodedata
import zipfile
from datetime import date, datetime, time as dtime
from typing import Any, Iterable, Optional

from app.core import csv_safe as _csv_safe
from app.core import zip_guard

from . import exporter as ex
from .extract import COMPOUND_SURNAMES, SURNAMES, normalize_phone

# --------------------------------------------------------------------------- 上限與訊息

#: 上傳檔案大小上限（解析前先檢查）
MAX_BYTES = 20 * 1024 * 1024
#: 資料列上限（不含標題列；全空白的列不算）
MAX_ROWS = 5000
#: 欄數上限（去掉尾端全空白的欄之後）
MAX_COLS = 100
#: XLSX / ODS 解開後的總量上限。比 `zip_guard` 的全站通用值（1 GB）嚴：5,000 列 × 100 欄的試算表解開後
#: 遠低於這個數字，而 odfpy / openpyxl 會把 XML 讀成物件，記憶體用量是檔案大小的好幾倍。
MAX_UNPACKED = 100 * 1024 * 1024
#: XLSX 一列最多看幾欄（超過的部分不讀；第 101～1024 欄有值就已經會被欄數上限擋下）
_XLSX_SCAN_COLS = 1024
#: XLSX 讀到的原始列（含空白列）上限：只設格式、沒有內容的列也會出現在檔案裡
_RAW_ROW_CAP = MAX_ROWS * 10

MSG_FORMAT = "不支援這種檔案格式，請上傳 CSV、XLSX、ODS、VCF 或 JSON。"
MSG_XLS = "不支援舊版 Excel（.xls），請另存成 XLSX 或 CSV 再上傳。"
MSG_TOO_BIG = "檔案超過 20 MB 上限。"
MSG_TOO_MANY_ROWS = "資料超過 5,000 列上限，請分成幾個檔案再匯入。"
MSG_TOO_MANY_COLS = "欄位超過 100 欄上限。"
MSG_EMPTY = "檔案裡沒有資料。"
MSG_UNREADABLE = "檔案無法讀取，可能已毀損或設了密碼。"
MSG_ENCODING = "無法判斷 CSV 的文字編碼，請另存成 UTF-8 再上傳。"
MSG_ZIP_BOMB = "檔案解開後異常龐大，為了避免耗盡伺服器資源而拒絕處理。"

#: 這個模組會丟出的訊息（給語系檔與畫面翻譯用）
MESSAGES = (MSG_FORMAT, MSG_XLS, MSG_TOO_BIG, MSG_TOO_MANY_ROWS, MSG_TOO_MANY_COLS, MSG_EMPTY, MSG_UNREADABLE,
            MSG_ENCODING, MSG_ZIP_BOMB)


class TableError(ValueError):
    """清單檔讀不進來（格式、大小、列數、空檔、毀損）。訊息是給使用者看的台灣繁中句子，不含儲存格內容。"""


#: 舊名（規格書裡的寫法）
ImportError_ = TableError

# --------------------------------------------------------------------------- 對應目標

#: 單值欄位
_SINGLE = tuple(ex._SINGLE)
#: 多值欄位
_MULTI = tuple(ex._MULTI)
#: 人工補充（活動、來源、標籤、備註）
_NOTE_KEYS = tuple(ex._NOTE_KEYS)
#: 電話類（比對時互相看）
_PHONEISH = ("mobile", "phone", "fax")
#: 主欄位 → 成對的附屬欄位
_SUB_OF = dict(ex._PAIR_OF)                 # {"phone": "ext", "address": "zip"}
#: 姓名拆開的欄位
_NAME_PARTS = ("name.first", "name.middle", "name.last")

#: 欄位可以對應到的目標：匯出的欄位（人工補充寫成 notes.*）＋姓名拆開的欄＋地址的附加部分（城市、國家…）
TARGETS = tuple([("notes." + c) if c in _NOTE_KEYS else c for c in ex.COLUMNS]
                + list(_NAME_PARTS) + ["address.part"])

#: 處理方式
DECISIONS = ("add", "merge", "skip", "keep_list", "use_card")
#: 每一種比對結果的預設處理方式
DEFAULT_DECISION = {"new": "add", "update": "merge", "conflict": "keep_list", "same": "skip"}


def _hnorm(h: Any) -> str:
    """欄名正規化：全形轉半形、不分大小寫、只留英數與中日文字（「公司 / 機關」→「公司機關」，「E-mail」→「email」）。"""
    s = unicodedata.normalize("NFKC", "" if h is None else str(h)).casefold().strip()
    if s.startswith("'"):
        s = s[1:]
    return re.sub(r"[^0-9a-z\u3400-\u9fff\uf900-\ufaff]", "", s)


def _build_synonyms() -> dict[str, str]:
    syn: dict[str, str] = {}
    # 我們自己的匯出欄名（中文與英文）
    for c in ex.COLUMNS:
        t = ("notes." + c) if c in _NOTE_KEYS else c
        syn[_hnorm(ex.LABELS_ZH[c])] = t
        syn[_hnorm(c)] = t
    groups = {
        "name": ["姓名", "聯絡人", "聯絡人姓名", "中文姓名", "中文名", "中文名字", "全名", "人名", "客戶姓名", "name",
                 "fullname", "displayname", "contactname", "contact", "名字?"],
        "name_en": ["外文姓名", "英文姓名", "英文名", "英文名字", "外文名", "englishname", "nameen", "nameenglish"],
        "name.first": ["firstname", "givenname", "名"],
        "name.middle": ["middlename", "additionalname", "中間名"],
        "name.last": ["lastname", "familyname", "surname", "姓氏", "姓"],
        "title": ["職稱", "職位", "職務", "頭銜", "jobtitle", "position", "organizationtitle", "orgtitle"],
        "department": ["部門", "部門名稱", "單位", "department", "dept", "organizationdepartment", "division"],
        "company": ["公司", "公司名稱", "機關", "機關名稱", "公司機關", "廠商名稱", "客戶名稱", "組織", "服務單位", "單位名稱",
                    "company", "companyname", "organization", "organisation", "organizationname", "org"],
        "company_en": ["外文公司名稱", "英文公司名稱", "公司英文名稱", "companyen", "englishcompanyname",
                       "companyenglishname"],
        "vat": ["統一編號", "統編", "公司統編", "營利事業統一編號", "vat", "vatnumber", "vatno", "taxid", "taxidnumber",
                "ubn"],
        "mobile": ["手機", "手機號碼", "行動電話", "行動", "mobile", "mobilephone", "cellphone", "cell", "carphone",
                   "行動電話號碼"],
        "phone": ["電話", "公司電話", "辦公室電話", "聯絡電話", "市話", "商務電話", "住家電話", "總機", "電話號碼", "phone",
                  "telephone", "tel", "businessphone", "homephone", "otherphone", "primaryphone", "companymainphone",
                  "workphone", "officephone", "phonevalue"],
        "ext": ["分機", "分機號碼", "ext", "extension"],
        "fax": ["傳真", "傳真號碼", "商務傳真", "住家傳真", "fax", "faxnumber", "businessfax", "homefax", "otherfax"],
        "email": ["email", "電子郵件", "電子郵件地址", "電郵", "信箱", "電子信箱", "emailaddress", "mail", "emailvalue"],
        "url": ["網址", "網站", "網頁", "公司網址", "官網", "url", "website", "web", "webpage", "homepage",
                "websitevalue", "businesswebpage"],
        "address": ["地址", "公司地址", "通訊地址", "聯絡地址", "住址", "營業地址", "商務街", "住家街", "其他街", "address",
                    "businessstreet", "homestreet", "otherstreet", "street", "addressformatted",
                    "mailingaddress", "businessaddress", "workaddress"],
        "zip": ["郵遞區號", "郵區", "商務郵遞區號", "住家郵遞區號", "zip", "zipcode", "postalcode", "postcode",
                "businesspostalcode", "homepostalcode", "otherpostalcode", "addresspostalcode"],
        "address.part": ["businessstreet2", "businessstreet3", "businesscity", "businessstate", "businesscountryregion",
                         "businesspobox", "homestreet2", "homestreet3", "homecity", "homestate", "homecountryregion",
                         "homepobox", "otherstreet2", "otherstreet3", "othercity", "otherstate", "othercountryregion",
                         "addressstreet", "addresscity", "addressregion", "addresscountry", "addresspobox", "addressextendedaddress",
                         "city", "state", "country", "countryregion", "商務市鎮", "商務縣市", "商務國家地區",
                         "住家市鎮", "住家縣市", "住家國家地區", "城市", "縣市", "國家"],
        "im": ["通訊帳號", "社群帳號", "即時通訊", "line", "lineid", "wechat", "skype", "im", "imaddress"],
        "notes.event": ["活動", "活動名稱", "展覽", "場合", "event"],
        "notes.source": ["來源", "取得來源", "名片來源", "source"],
        "notes.tags": ["標籤", "分類", "類別", "群組", "tags", "tag", "labels", "categories", "groupmembership"],
        "notes.memo": ["備註", "附註", "註記", "說明", "memo", "notes", "note", "remark", "remarks", "comment",
                       "comments"],
    }
    for t, words in groups.items():
        for w in words:
            if w.endswith("?"):
                syn[_hnorm(w[:-1])] = "?first"     # 「名字」：有「姓氏」欄時是名字，沒有就是全名
            else:
                syn[_hnorm(w)] = t
    syn["title"] = "?title"                        # Outlook 的 Title 是稱謂（Mr.），有 Job Title 時不用
    return syn


_SYN = _build_synonyms()


def auto_map(headers: Iterable[Any]) -> dict[int, str]:
    """欄位索引 → 對應目標（`TARGETS` 之一）。認不得的欄不放進結果。

    先比整個欄名，比不到再去掉數字比一次（「電話 2」「E-mail 2 Address」「Phone 1 - Value」）。
    有直接的姓名欄時，「名字 / 姓氏」那幾欄不再對應（舊版 Google 通訊錄三種都有，內容重複）。
    """
    out: dict[int, str] = {}
    for i, h in enumerate(headers or []):
        n = _hnorm(h)
        if not n:
            continue
        t = _SYN.get(n)
        if t is None:
            n2 = re.sub(r"\d+", "", n)
            if n2 and n2 != n:
                t = _SYN.get(n2)
        if t:
            out[i] = t
    vals = set(out.values())
    for i, t in list(out.items()):
        if t == "?title":
            if "title" in vals:
                del out[i]
            else:
                out[i] = "title"
        elif t == "?first":
            out[i] = "name.first" if "name.last" in vals else "name"
    if "name" in out.values():
        for i, t in list(out.items()):
            if t in _NAME_PARTS:
                del out[i]
    # 只有地址的附加部分（例如 Google 只匯出「Address 1 - Street」）：第一個當地址欄
    parts = sorted(i for i, t in out.items() if t == "address.part")
    if parts and "address" not in out.values():
        out[parts[0]] = "address"
    return out


def clean_mapping(mapping: Optional[dict], ncols: int) -> dict[int, str]:
    """驗過的對應：索引轉成整數（JSON 的鍵是字串）、超出範圍或不認得的目標丟掉。畫面送回來的對應一律先過這裡。"""
    out: dict[int, str] = {}
    for k, v in (mapping or {}).items():
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= i < ncols and isinstance(v, str) and v in TARGETS:
            out[i] = v
    return out


# --------------------------------------------------------------------------- 讀檔

def _kind(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in (".csv", ".tsv", ".txt"):
        return "csv"
    if ext == ".xlsx":
        return "xlsx"
    if ext == ".ods":
        return "ods"
    if ext in (".vcf", ".vcard"):
        return "vcf"
    if ext == ".json":
        return "json"
    if ext == ".xls":
        raise TableError(MSG_XLS)
    raise TableError(MSG_FORMAT)


def _cell_str(v: Any) -> str:
    """儲存格的值 → 字串。整數值的浮點數不留「.0」；日期只留到分。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):        # NaN、無限大
            return ""
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d") if v.time() == dtime(0, 0) else v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, (date, dtime)):
        return v.isoformat()
    s = v if isinstance(v, str) else str(v)
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


class _Rows:
    """收集非空白的列，邊收邊檢查列數與欄數（超過就立刻停，不先全部讀完）。"""

    def __init__(self):
        self.rows: list[list[str]] = []

    def add(self, cells: list[str]) -> None:
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            return
        if len(cells) > MAX_COLS:
            raise TableError(MSG_TOO_MANY_COLS)
        self.rows.append(cells)
        if len(self.rows) > MAX_ROWS + 1:                  # 加上標題列
            raise TableError(MSG_TOO_MANY_ROWS)


def _decode(data: bytes) -> str:
    try:
        if data.startswith(b"\xef\xbb\xbf"):
            return data[3:].decode("utf-8")
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
    except UnicodeDecodeError:
        raise TableError(MSG_ENCODING)
    for enc in ("utf-8", "cp950"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise TableError(MSG_ENCODING)


def _sniff(text: str) -> str:
    """看第一列（引號外）哪個分隔符號最多：逗號、分號、Tab；一樣多時逗號優先。"""
    counts = {",": 0, ";": 0, "\t": 0}
    quoted = False
    for ch in text[:65536]:
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            if ch in "\r\n":
                break
            if ch in counts:
                counts[ch] += 1
    best = max(counts.values())
    if best == 0:
        return ","
    return next(d for d in (",", ";", "\t") if counts[d] == best)


_CSV_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _unneutralize(v: str) -> str:
    """拿掉匯出時為了防公式補的前置單引號（只在後面接著公式開頭時才拿）。"""
    if len(v) > 1 and v[0] == "'":
        rest = v[1:]
        head = rest.lstrip(" 　\n\x0b\x0c")
        if rest[0] in _CSV_TRIGGERS or (head and head[0] in _CSV_TRIGGERS):
            return rest
    return v


def _read_csv(data: bytes) -> list[list[str]]:
    text = _decode(data)
    delim = _sniff(text)
    out = _Rows()
    try:
        for row in csv.reader(io.StringIO(text, newline=""), delimiter=delim):
            out.add([_cell_str(_unneutralize(c)) for c in row])
    except csv.Error:
        raise TableError(MSG_UNREADABLE)
    return out.rows


def _guard_zip(data: bytes) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
        raise TableError(MSG_UNREADABLE)
    with zf:
        try:
            zip_guard.check(zf, max_uncompressed=MAX_UNPACKED)
        except zip_guard.ZipBombError:
            raise TableError(MSG_ZIP_BOMB)


def _read_xlsx(data: bytes) -> list[list[str]]:
    _guard_zip(data)
    try:
        import openpyxl
    except ImportError:                                    # pragma: no cover
        raise RuntimeError("openpyxl 未安裝，無法讀取 xlsx")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise TableError(MSG_UNREADABLE)
    out = _Rows()
    try:
        if not wb.worksheets:
            raise TableError(MSG_EMPTY)
        ws = wb.worksheets[0]
        try:
            ws.reset_dimensions()          # 檔案裡記的範圍可能是錯的：改成每列讀到最後一個有內容的格
        except AttributeError:                             # pragma: no cover
            pass
        raw = 0
        for row in ws.iter_rows(values_only=True):
            raw += 1
            if raw > _RAW_ROW_CAP:
                raise TableError(MSG_TOO_MANY_ROWS)
            out.add([_cell_str(v) for v in row[:_XLSX_SCAN_COLS]])
    except TableError:
        raise
    except Exception:
        raise TableError(MSG_UNREADABLE)
    finally:
        try:
            wb.close()
        except Exception:                                  # pragma: no cover
            pass
    return out.rows


def _read_ods(data: bytes) -> list[list[str]]:
    _guard_zip(data)
    try:
        from odf import teletype
        from odf.namespaces import OFFICENS, TABLENS, TEXTNS
        from odf.opendocument import load as odf_load
        from odf.table import Table
    except ImportError:                                    # pragma: no cover
        raise RuntimeError("odfpy 未安裝，無法讀取 ods")
    try:
        doc = odf_load(io.BytesIO(data))
        sheet = doc.spreadsheet
        tables = sheet.getElementsByType(Table) if sheet is not None else []
    except Exception:
        raise TableError(MSG_UNREADABLE)
    if sheet is None:
        raise TableError(MSG_UNREADABLE)
    if not tables:
        raise TableError(MSG_EMPTY)

    row_q = (TABLENS, "table-row")
    group_q = {(TABLENS, "table-header-rows"), (TABLENS, "table-rows"), (TABLENS, "table-row-group")}
    cell_q = {(TABLENS, "table-cell"), (TABLENS, "covered-table-cell")}
    p_q = (TEXTNS, "p")

    def rep(node, attr: str) -> int:
        try:
            return max(1, int(node.getAttrNS(TABLENS, attr) or 1))
        except (TypeError, ValueError):
            return 1

    def rows_of(node):
        for ch in node.childNodes:
            q = getattr(ch, "qname", None)
            if q == row_q:
                yield ch
            elif q in group_q:
                yield from rows_of(ch)

    def text_of(cell) -> str:
        if cell.qname != (TABLENS, "table-cell"):
            return ""                       # 被合併儲存格蓋住的格當空白
        ps = [teletype.extractText(ch) for ch in cell.childNodes if getattr(ch, "qname", None) == p_q]
        if ps:
            return _cell_str("\n".join(ps))
        for a in ("value", "date-value", "time-value", "boolean-value", "string-value"):
            v = cell.getAttrNS(OFFICENS, a)
            if v:
                return _cell_str(v)
        return ""

    out = _Rows()
    try:
        for row in rows_of(tables[0]):
            cells: list[str] = []
            pending = 0                     # 還沒遇到下一個有值的格之前，先記著前面有幾個空白格
            for cell in row.childNodes:
                if getattr(cell, "qname", None) not in cell_q:
                    continue
                n = rep(cell, "number-columns-repeated")
                v = text_of(cell)
                if not v:
                    pending += n            # 只是計數，不展開（尾端「重複 16,384 次」的空白格不會真的建出來）
                    continue
                if len(cells) + pending + n > MAX_COLS:
                    raise TableError(MSG_TOO_MANY_COLS)
                cells.extend([""] * pending)
                pending = 0
                cells.extend([v] * n)
            if not any(cells):
                continue                    # 空白列（不管重複幾次）直接略過
            for _ in range(min(rep(row, "number-rows-repeated"), MAX_ROWS + 2)):
                out.add(list(cells))
    except TableError:
        raise
    except Exception:
        raise TableError(MSG_UNREADABLE)
    return out.rows


def source_kind(filename: str) -> str:
    """檔案種類（csv / xlsx / ods / vcf / json）；不支援的丟 `TableError`。畫面用來預設下載的格式。"""
    return _kind(filename)


def _f(key: str, value: str, **extra) -> dict:
    d = {"key": key, "value": value, "status": "confirmed"}
    d.update({k: v for k, v in extra.items() if v})
    return d


def _vunesc(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nx = s[i + 1]
            out.append("\n" if nx in "nN" else nx)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _vsplit(s: str, sep: str = ";") -> list[str]:
    """依沒有跳脫的分隔符號切（N / ORG / ADR 是結構化的值），每一段再解跳脫。"""
    parts, cur, i = [], [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur.append(s[i:i + 2])
            i += 2
            continue
        if ch == sep:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return [_vunesc(x).strip() for x in parts]


def _qp_decode(v: str, charset: str) -> str:
    import quopri
    raw = quopri.decodestring(v.encode("latin-1", "replace"))
    for enc in (charset or "utf-8", "utf-8", "cp950"):
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


_NOTE_PREFIX = (("活動：", "event"), ("來源：", "source"), ("標籤：", "tags"))


def _note_to_notes(text: str) -> dict:
    """本工具匯出的 NOTE（「活動：…」「來源：…」「標籤：a、b」＋備註）拆回去；其他 vCard 的 NOTE 整段當備註。"""
    notes: dict[str, Any] = {}
    memo = []
    for ln in (text or "").split("\n"):
        s = ln.strip()
        for pre, key in _NOTE_PREFIX:
            if s.startswith(pre):
                val = s[len(pre):].strip()
                notes[key] = [x.strip() for x in re.split(r"[、,，]", val) if x.strip()] if key == "tags" else val
                break
        else:
            if s:
                memo.append(s)
    if memo:
        notes["memo"] = "\n".join(memo)
    return notes


def _read_vcf(data: bytes) -> list[dict]:
    """vCard 2.1 / 3.0 / 4.0 → 聯絡人（跟名片聯絡人同形狀的 `fields`＋`notes`）。認得本工具的 `X-JTDT-*`。"""
    text = _decode(data).replace("\r\n", "\n").replace("\r", "\n")
    # 折行：下一行開頭是空白或 Tab 就接回去；2.1 的 quoted-printable 以「=」結尾表示接下一行
    raw = text.split("\n")
    lines: list[str] = []
    for ln in raw:
        if lines and ln[:1] in (" ", "\t"):
            lines[-1] += ln[1:]
        elif lines and lines[-1].endswith("=") and "QUOTED-PRINTABLE" in lines[-1].split(":", 1)[0].upper():
            lines[-1] = lines[-1][:-1] + ln
        else:
            lines.append(ln)
    out: list[dict] = []
    cur: Optional[dict] = None
    for ln in lines:
        if ":" not in ln:
            continue
        head, value = ln.split(":", 1)
        parts = head.split(";")
        name = parts[0].split(".")[-1].strip().upper()
        params = [x.strip().upper() for x in parts[1:]]
        if name == "BEGIN" and value.strip().upper() == "VCARD":
            cur = {"fn": "", "n": "", "fields": [], "notes": {}}
            continue
        if cur is None:
            continue
        if name == "END":
            if len(out) >= MAX_ROWS:
                raise TableError(MSG_TOO_MANY_ROWS)
            fields = cur["fields"]
            have = {f["key"] for f in fields}
            nm = cur["fn"] or cur["n"]
            company = next((f["value"] for f in fields if f["key"] == "company"), "")
            name_en = next((f["value"] for f in fields if f["key"] == "name_en"), "")
            if nm and "name" not in have and nm != name_en and not (nm == company and not cur["n"]):
                # 跟試算表的「彈性姓名欄」同一個規則：只有英文的名字放外文姓名（名片上只有英文名時匯出的 VCF 就是這樣）
                fields.insert(0, _f("name_en" if (_is_latin(nm) and not name_en) else "name", nm))
            if fields or cur["notes"]:
                out.append({"fields": fields, "notes": cur["notes"], "status": "confirmed"})
            cur = None
            continue
        charset = next((p.split("=", 1)[1] for p in params if p.startswith("CHARSET=")), "")
        if any(p in ("ENCODING=QUOTED-PRINTABLE", "QUOTED-PRINTABLE") for p in params):
            value = _qp_decode(value, charset.lower())
        types = set()
        for p in params:
            v = p.split("=", 1)[1] if p.startswith("TYPE=") else ("" if "=" in p else p)
            types.update(x.strip().strip('"') for x in v.split(",") if x.strip())
        add = cur["fields"].append
        if name == "FN":
            cur["fn"] = _vunesc(value).strip()
        elif name == "N":
            seg = _vsplit(value) + [""] * 5
            fam, giv, mid = seg[0], seg[1], seg[2]
            if any(_is_cjk(x) for x in (fam, giv)):
                cur["n"] = (fam + giv).strip()
            else:
                cur["n"] = " ".join(x for x in (giv, mid, fam) if x)
        elif name == "ORG":
            seg = _vsplit(value)
            if seg and seg[0]:
                add(_f("company", seg[0]))
            if len(seg) > 1 and seg[1]:
                add(_f("department", seg[1]))
        elif name == "TITLE" and _vunesc(value).strip():
            add(_f("title", _vunesc(value).strip()))
        elif name == "TEL":
            v, ext = _split_ext(_vunesc(value).replace("tel:", ""))
            key = "fax" if "FAX" in types else ("mobile" if "CELL" in types else "phone")
            if v:
                add(_f(key, v, ext=ext))
        elif name == "EMAIL" and _vunesc(value).strip():
            add(_f("email", _vunesc(value).strip()))
        elif name == "URL" and _vunesc(value).strip():
            add(_f("url", _vunesc(value).strip()))
        elif name == "ADR":
            seg = _vsplit(value) + [""] * 7
            pobox, extd, street, city, region, postal, country = seg[:7]
            body = " ".join(x for x in (street, extd, city, region, country, pobox) if x)
            if body:
                add(_f("address", body, zip=postal))
        elif name in ("X-JTDT-NAME-EN",) and _vunesc(value).strip():
            add(_f("name_en", _vunesc(value).strip()))
        elif name == "X-JTDT-COMPANY-EN" and _vunesc(value).strip():
            add(_f("company_en", _vunesc(value).strip()))
        elif name == "X-JTDT-VAT" and _vunesc(value).strip():
            add(_f("vat", _vunesc(value).strip()))
        elif name in ("X-JTDT-IM", "IMPP", "X-SOCIALPROFILE") and _vunesc(value).strip():
            v = _vunesc(value).strip()
            m = re.match(r"^([A-Za-z][\w .-]{0,20}):\s*(.+)$", v) if name == "X-JTDT-IM" else None
            add(_f("im", m.group(2), label=m.group(1)) if m else _f("im", v))
        elif name == "NOTE":
            cur["notes"] = _note_to_notes(_vunesc(value))
    return out


def _read_json(data: bytes) -> list[dict]:
    """本工具匯出的 JSON（`{"contacts": [...]}`，也收直接是陣列的）→ 聯絡人。"""
    import json
    try:
        doc = json.loads(_decode(data))
    except ValueError:
        raise TableError(MSG_UNREADABLE)
    items = doc.get("contacts") if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        raise TableError(MSG_FORMAT)
    if len(items) > MAX_ROWS:
        raise TableError(MSG_TOO_MANY_ROWS)
    plural = {v: k for k, v in ex._PLURAL.items()}
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        fields: list[dict] = []
        for k in ex._SINGLE:
            v = ex._s(it.get(k)).strip()
            if v:
                fields.append(_f(k, v))
        for pk, key in plural.items():
            vals = it.get(pk)
            if not isinstance(vals, list):
                continue
            for x in vals:
                if isinstance(x, dict) and ex._s(x.get("value")).strip():
                    fields.append(_f(key, ex._s(x.get("value")).strip(), ext=ex._s(x.get("ext")).strip(),
                                     zip=ex._s(x.get("zip")).strip(), label=ex._s(x.get("label")).strip()))
                elif isinstance(x, str) and x.strip():
                    fields.append(_f(key, x.strip()))
        n = it.get("notes") if isinstance(it.get("notes"), dict) else {}
        tags = n.get("tags") or []
        notes = {"event": ex._s(n.get("event")), "source": ex._s(n.get("source")),
                 "tags": [ex._s(x) for x in tags if ex._s(x).strip()] if isinstance(tags, list) else [],
                 "memo": ex._s(n.get("memo"))}
        if fields or any(notes.values()):
            out.append({"fields": fields, "notes": notes, "status": "confirmed"})
    return out


def parse_table(data: bytes, filename: str) -> tuple[list[str], list[list[str]]]:
    """讀清單檔 → （欄名, 資料列）。第一個非空白的列當欄名；全空白的列略過；每一列補齊到同樣欄數。

    只有欄名、沒有資料列也可以（回空的資料列）。讀不進來丟 `TableError`。
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TableError(MSG_UNREADABLE)
    if len(data) > MAX_BYTES:
        raise TableError(MSG_TOO_BIG)
    kind = _kind(filename)
    if not data:
        raise TableError(MSG_EMPTY)
    data = bytes(data)
    if kind in ("vcf", "json"):
        contacts = _read_vcf(data) if kind == "vcf" else _read_json(data)
        if not contacts:
            raise TableError(MSG_EMPTY)
        if len(contacts) > MAX_ROWS:
            raise TableError(MSG_TOO_MANY_ROWS)
        headers, rows = ex.build_rows(contacts, lang="zh")
        if len(headers) > MAX_COLS:
            raise TableError(MSG_TOO_MANY_COLS)
        return headers, rows
    if kind == "csv":
        rows = _read_csv(data)
    elif kind == "xlsx":
        rows = _read_xlsx(data)
    else:
        rows = _read_ods(data)
    if not rows:
        raise TableError(MSG_EMPTY)
    width = max(len(r) for r in rows)
    if width > MAX_COLS:
        raise TableError(MSG_TOO_MANY_COLS)
    if len(rows) - 1 > MAX_ROWS:
        raise TableError(MSG_TOO_MANY_ROWS)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return rows[0], rows[1:]


# --------------------------------------------------------------------------- 欄位配置

class _Layout:
    """欄位配置：哪一欄對到什麼、電話配哪個分機欄、地址的附加部分接在哪個地址欄、彈性姓名欄是哪幾欄。"""

    def __init__(self, mapping: dict[int, str]):
        self.map = dict(mapping)
        self.pair: dict[int, int] = {}       # 主欄 → 附屬欄（電話 → 分機、地址 → 郵遞區號）
        for main, sub in _SUB_OF.items():
            for m, s in zip(self.cols(main), self.cols(sub)):
                self.pair[m] = s
        self.parts: dict[int, list[int]] = {}    # 地址欄 → 附加部分的欄
        addr = self.cols("address")
        for p in self.cols("address.part"):
            before = [a for a in addr if a < p]
            if before or addr:
                self.parts.setdefault(before[-1] if before else addr[0], []).append(p)
        self.name_parts = {t: self.cols(t)[0] for t in _NAME_PARTS if self.cols(t)}
        if self.name_parts:
            self.slot_kind: Optional[str] = "parts"
            self.slot_cols = sorted(self.name_parts.values())
        elif self.cols("name") and not self.cols("name_en"):
            self.slot_kind = "single"
            self.slot_cols = [self.cols("name")[0]]
        else:
            self.slot_kind, self.slot_cols = None, []

    def cols(self, target: str) -> list[int]:
        return sorted(c for c, t in self.map.items() if t == target)

    def fixed(self, key: str) -> list[int]:
        """固定對到這個欄位的欄（彈性姓名欄不算）。"""
        return [c for c in self.cols(key) if c not in self.slot_cols]

    def keys(self) -> set[str]:
        """有欄位可以寫的目標（給比對判斷「清單有沒有這一欄」）。"""
        out = set()
        for c, t in self.map.items():
            if c in self.slot_cols or t in _NAME_PARTS or t == "address.part":
                continue
            if t in ("ext", "zip"):
                continue                     # 附屬欄看配對（欄位的 source.pair）
            out.add(t)
        return out


def _is_cjk(s: str) -> bool:
    return any(ord(ch) >= 0x2E80 for ch in s)


def _is_latin(s: str) -> bool:
    return bool(s) and not _is_cjk(s) and any(ch.isalpha() for ch in s)


def _combine_name(first: str, middle: str, last: str) -> str:
    parts = [p for p in (first, middle, last) if p]
    if not parts:
        return ""
    if _is_cjk("".join(parts)):
        return "".join(p for p in (last, middle, first) if p)
    return " ".join(parts)


def _split_name(v: str) -> tuple[str, str]:
    """（名, 姓）。英文名最後一段當姓；中文名認得姓氏才拆，認不得整個放在「名」。"""
    v = v.strip()
    if _is_latin(v):
        toks = v.split()
        return (" ".join(toks[:-1]), toks[-1]) if len(toks) >= 2 else (v, "")
    c = v.replace(" ", "")
    if len(c) > 2 and c[:2] in COMPOUND_SURNAMES:
        return c[2:], c[:2]
    if 2 <= len(c) <= 4 and c[0] in SURNAMES:
        return c[1:], c[0]
    return v, ""


_EXT_RE = re.compile(r"^(.*?\d.*?)\s*(?:ext\.?|分機|轉|#|＃|x)\s*[:：.]?\s*(\d{1,6})$", re.I)
_IM_RE = re.compile(r"^([A-Za-z][A-Za-z ]{0,15}|微信)\s*[:：]\s*(.+)$")


def _split_ext(v: str) -> tuple[str, str]:
    m = _EXT_RE.match(v.strip())
    return (m.group(1).strip(), m.group(2)) if m else (v.strip(), "")


def _split_phones(v: str) -> list[str]:
    return [p for p in re.split(r"\s*[\n;；,，、]\s*|\s+/\s+", v) if p.strip()]


def _split_emails(v: str) -> list[str]:
    return [p for p in re.split(r"[\s;,，；、]+", v) if p.strip()]


def _split_tags(v: str) -> list[str]:
    return [t for t in (x.strip() for x in re.split(r"\s*(?::::|[,，、;；\n])\s*", v)) if t]


def _field(row: int, col: int, key: str, value: str, j: int = 0, **extra) -> dict:
    f = {"id": f"r{row}c{col}" + (f"-{j}" if j else ""), "key": key, "value": value, "status": "confirmed",
         "reason": "", "source": {"method": "import", "row": row, "col": col}}
    for k, v in extra.items():
        if k == "pair":
            if v is not None:
                f["source"]["pair"] = v
        elif k == "slot":
            if v:
                f["source"]["slot"] = True
        elif v not in (None, ""):
            f[k] = v
    return f


def _cell(cells: list[str], c: Optional[int]) -> str:
    if c is None or c >= len(cells):
        return ""
    v = cells[c]
    return v.strip() if isinstance(v, str) else _cell_str(v)


def _slot_value(cells: list[str], lay: _Layout) -> str:
    if lay.slot_kind == "single":
        return _cell(cells, lay.slot_cols[0])
    if lay.slot_kind == "parts":
        g = lambda t: _cell(cells, lay.name_parts.get(t))     # noqa: E731
        return _combine_name(g("name.first"), g("name.middle"), g("name.last"))
    return ""


def _read_row(cells: list[str], i: int, lay: _Layout) -> dict:
    fields: list[dict] = []
    notes: dict[str, Any] = {"event": "", "source": "", "tags": [], "memo": ""}
    slot = None
    if lay.slot_kind:
        sv = _slot_value(cells, lay)
        if sv:
            slot = "name_en" if _is_latin(sv) else "name"
            fields.append(_field(i, lay.slot_cols[0], slot, sv, slot=True))
        else:
            slot = "free"
    for c in sorted(lay.map):
        t = lay.map[c]
        if c in lay.slot_cols or t in _NAME_PARTS or t in ("ext", "zip", "address.part"):
            continue
        v = _cell(cells, c)
        if t == "address":
            v = " ".join(x for x in [v] + [_cell(cells, p) for p in lay.parts.get(c, [])] if x)
        if not v:
            continue
        if t.startswith("notes."):
            k = t[6:]
            if k == "tags":
                notes["tags"] += [x for x in _split_tags(v) if x not in notes["tags"]]
            elif k == "memo":
                notes["memo"] = (notes["memo"] + "\n" + v) if notes["memo"] else v
            elif not notes[k]:
                notes[k] = v
            continue
        pc = lay.pair.get(c)
        if t in _PHONEISH:
            for j, p in enumerate(_split_phones(v)):
                val, ext = _split_ext(p)
                if j == 0 and not ext and pc is not None:
                    ext = _cell(cells, pc)
                fields.append(_field(i, c, t, val, j, ext=ext, norm=normalize_phone(val),
                                     pair=pc if j == 0 else None))
        elif t == "email":
            for j, p in enumerate(_split_emails(v)):
                fields.append(_field(i, c, t, p, j, norm=p.strip().lower()))
        elif t == "im":
            m = _IM_RE.match(v)
            label, val = (m.group(1).strip(), m.group(2).strip()) if m else ("", v)
            fields.append(_field(i, c, t, val, label=label))
        elif t == "address":
            fields.append(_field(i, c, t, v, zip=_cell(cells, pc), pair=pc))
        else:
            fields.append(_field(i, c, t, v))
    keys = lay.keys()
    return {"id": f"r{i}", "row": i, "status": "confirmed", "fields": fields, "notes": notes,
            "keys": sorted(keys), "name_slot": slot}


def rows_to_contacts(headers: list[str], rows: list[list[str]], mapping: dict) -> list[dict]:
    """每一列 → `{"id", "row", "status", "fields", "notes", "keys", "name_slot"}`。

    `fields` 跟名片聯絡人同形狀（狀態 `confirmed`、`source={"method": "import", "row", "col"}`；電話類帶 `norm`
    與 `ext`，Email 的 `norm` 是小寫，地址帶 `zip`，通訊帳號帶 `label`）。`keys` 是清單有欄位可以寫的目標；
    `name_slot` 是彈性姓名欄這一列存的是 `name` / `name_en`，空白時是 `free`，沒有彈性姓名欄時是 None。
    """
    ncols = max([len(headers or [])] + [len(r) for r in rows or []])
    lay = _Layout(clean_mapping(mapping, ncols))
    return [_read_row(list(r), i, lay) for i, r in enumerate(rows or [])]


# --------------------------------------------------------------------------- 比對用的正規化

def _tkey(s: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", ex._s(s))).casefold()


def _company_key(s: Any) -> str:
    return re.sub(r"[()（）.,，、·・\-]", "", _tkey(s).replace("臺", "台"))


def _phone_key(v: Any) -> str:
    """電話比對鍵：+886 / 00886 換成 0（`normalize_phone` 本身不轉國碼）。"""
    d = normalize_phone(ex._s(v))
    for pre in ("+886", "00886"):
        if d.startswith(pre):
            return "0" + d[len(pre):].lstrip("0")
    if d.startswith("886") and len(d) >= 11:
        return "0" + d[3:].lstrip("0")
    return d


def _url_key(v: Any) -> str:
    s = _tkey(v)
    s = re.sub(r"^[a-z]+://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.rstrip("/")


def _addr_key(v: Any) -> str:
    s = re.sub(r"[,，、.。()（）\-]", "", _tkey(v).replace("臺", "台"))
    return re.sub(r"^\d{3,6}", "", s)


def _mkey(key: str, v: Any) -> str:
    if key in _PHONEISH:
        return _phone_key(v)
    if key == "email":
        return ex._s(v).strip().lower()
    if key == "url":
        return _url_key(v)
    if key == "address":
        return _addr_key(v)
    return _tkey(v)


def _skey(key: str, v: Any) -> str:
    if key == "vat":
        return re.sub(r"\D", "", ex._s(v))
    if key in ("company", "company_en"):
        return _company_key(v)
    return _tkey(v)


def _first(contact: dict, key: str) -> Optional[dict]:
    vs = ex._values(contact, key)
    return vs[0] if vs else None


def _ident(contact: dict) -> dict:
    emails = {_mkey("email", f.get("value")) for f in ex._values(contact, "email")}
    mobiles = set()
    for k in _PHONEISH:
        for f in ex._values(contact, k):
            pk = _phone_key(f.get("value"))
            if pk and (k == "mobile" or pk.startswith("09")):
                mobiles.add(pk)
    comp = {_company_key(ex._single(contact, k)) for k in ("company", "company_en")} - {""}
    return {"emails": emails - {""}, "mobiles": mobiles, "name": _tkey(ex._single(contact, "name")),
            "name_en": _tkey(ex._single(contact, "name_en")), "company": comp}


def _find(ci: dict, ids: list[dict]) -> tuple[list[int], Optional[str]]:
    """依序：Email → 手機 → 姓名＋公司 → 外文姓名＋公司；回第一條有符合的規則的**所有**符合列（由 `merge_plan` 挑）。

    同一條規則有好幾列符合（清單裡本來就有重複的人，或之前匯出時同一人的正反面是兩列）時，不可以一律取最前面那列
    —— 第二張名片會被併進第一列、它自己那列反而被當成清單裡別的人留著，合併完同一個人還是兩份。
    """
    rules = (
        ("email", lambda d: bool(ci["emails"] & d["emails"])),
        ("mobile", lambda d: bool(ci["mobiles"] & d["mobiles"])),
        ("name_company", lambda d: bool(ci["name"] and ci["company"] and d["name"] == ci["name"]
                                        and ci["company"] & d["company"])),
        ("name_en_company", lambda d: bool(ci["name_en"] and ci["company"] and d["name_en"] == ci["name_en"]
                                           and ci["company"] & d["company"])),
    )
    for how, ok in rules:
        hits = [pos for pos, d in enumerate(ids) if ok(d)]
        if hits:
            return hits, how
    return [], None


def _compare(card: dict, imp: dict) -> list[dict]:
    """名片 vs 清單的一列：只看清單有欄位的部分。差異帶 `_` 開頭的內部資訊（寫回時用），對外前會拿掉。"""
    keys = set(imp.get("keys") or [])
    slot = imp.get("name_slot")
    if slot == "free":
        slot = "name" if ex._single(card, "name").strip() else "name_en"
    fields = [f for f in imp.get("fields") or [] if isinstance(f, dict)]
    diffs: list[dict] = []

    for k in _SINGLE:
        if not (k in keys or slot == k):
            continue
        cv = ex._single(card, k).strip()
        if not cv:
            continue
        lf = next((f for f in fields if f.get("key") == k and ex._s(f.get("value")).strip()), None)
        if lf is None:
            diffs.append({"key": k, "card": cv, "list": "", "kind": "add"})
        elif _skey(k, cv) != _skey(k, lf.get("value")):
            diffs.append({"key": k, "card": cv, "list": ex._s(lf.get("value")), "kind": "conflict",
                          "_col": lf["source"]["col"], "_slot": bool(lf["source"].get("slot"))})

    for k in _MULTI:
        if not (k in keys or (k == "mobile" and "phone" in keys)):
            continue
        pool_keys = _PHONEISH if k in _PHONEISH else (k,)
        pool = [f for f in fields if f.get("key") in pool_keys]
        sub = _SUB_OF.get(k)
        for f in ex._values(card, k):
            mk = _mkey(k, f.get("value"))
            match = next((x for x in pool if _mkey(k, x.get("value")) == mk), None)
            if match is None:
                diffs.append({"key": k, "card": ex._multi_cell(f, k), "list": "", "kind": "add", "_field": f})
                continue
            pc = match["source"].get("pair")
            if sub and pc is not None and match.get("key") == k:
                cs, ls = ex._s(f.get(sub)).strip(), ex._s(match.get(sub)).strip()
                if cs and not ls:
                    diffs.append({"key": sub, "card": cs, "list": "", "kind": "add", "value": ex._s(f.get("value")),
                                  "_col": pc})
                elif cs and ls and _tkey(cs) != _tkey(ls):
                    diffs.append({"key": sub, "card": cs, "list": ls, "kind": "conflict",
                                  "value": ex._s(f.get("value")), "_col": pc})

    cn, ln = ex._notes(card), imp.get("notes") or {}
    for k in ("event", "source", "memo"):
        if "notes." + k not in keys:
            continue
        cv, lv = ex._s(cn.get(k)).strip(), ex._s(ln.get(k)).strip()
        if not cv:
            continue
        if not lv:
            diffs.append({"key": k, "card": cv, "list": "", "kind": "add"})
        elif k == "memo":
            if _tkey(cv) not in _tkey(lv):
                diffs.append({"key": k, "card": cv, "list": lv, "kind": "add"})     # 備註接在後面，不算衝突
        elif _tkey(cv) != _tkey(lv):
            diffs.append({"key": k, "card": cv, "list": lv, "kind": "conflict"})
    if "notes.tags" in keys:
        have = {_tkey(t) for t in ln.get("tags") or []}
        missing = [t for t in ex._tags(card) if _tkey(t) not in have]
        if missing:
            diffs.append({"key": "tags", "card": "、".join(missing), "list": "、".join(ln.get("tags") or []),
                          "kind": "add", "_tags": missing})
    return diffs


def _public_diff(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def merge_plan(card_contacts: Iterable[dict], imported: list[dict]) -> list[dict]:
    """合併計畫。名片聯絡人（`excluded` 略過）依原本順序各一項，後面接著沒有配到任何名片的清單列：

    `{"kind": "new" | "same" | "update" | "conflict" | "untouched", "contact_id", "row", "match", "diffs"}`

    * `new`：清單裡找不到，`row` 是 None。
    * `same`：清單有的欄位都一樣。`update`：名片補上清單空著的值或多出來的多值。`conflict`：兩邊都有值但不一樣。
    * `untouched`：清單裡沒有配到名片的列（`contact_id` 是 None），輸出時原樣保留。
    * `match`：配對依據 `email` / `mobile` / `name_company` / `name_en_company`。
    * `diffs`：`[{"key", "card", "list", "kind": "add" | "conflict"}]`；`key` 是匯出的欄位名稱
      （`exporter.LABELS_ZH` 的鍵），分機 / 郵遞區號的差異另外帶 `value`（是哪一支電話 / 哪個地址）。

    多張名片配到同一列時都算配對（同一人的兩張名片），合併時依序寫入；好幾列都符合時挑最像的、沒被配走的那一列。
    """
    imported = [r for r in imported or [] if isinstance(r, dict)]
    ids = [_ident(r) for r in imported]
    items: list[dict] = []
    matched: set[int] = set()
    for card in card_contacts or []:
        if not isinstance(card, dict) or card.get("status") == "excluded":
            continue
        hits, how = _find(_ident(card), ids)
        if not hits:
            items.append({"kind": "new", "contact_id": card.get("id"), "row": None, "match": None, "diffs": []})
            continue
        # 好幾列都符合時：衝突最少 → 差異最少 → 還沒被別張名片配走 → 最前面
        scored = []
        for p in hits:
            ds = _compare(card, imported[p])
            scored.append((sum(d["kind"] == "conflict" for d in ds), len(ds), p in matched, p, ds))
        _, _, _, pos, diffs = min(scored, key=lambda s: s[:4])
        imp = imported[pos]
        kind = ("conflict" if any(d["kind"] == "conflict" for d in diffs) else "update" if diffs else "same")
        items.append({"kind": kind, "contact_id": card.get("id"), "row": imp.get("row"), "match": how,
                      "diffs": [_public_diff(d) for d in diffs]})
        matched.add(pos)
    for pos, imp in enumerate(imported):
        if pos not in matched:
            items.append({"kind": "untouched", "contact_id": None, "row": imp.get("row"), "match": None,
                          "diffs": []})
    return items


#: 名字相似度門檻（difflib）
SIMILAR_RATIO = 0.85


def possible_pairs(card_contacts: Iterable[dict], imported: list[dict], *, limit: int = 200,
                   per_card: int = 5) -> list[dict]:
    """可能是同一人、但規則判斷不了的配對（給之後選用的 LLM 檢查；這裡只配對，不呼叫 LLM）。

    * `same_name`：姓名（或外文姓名）完全相同，但公司不同或缺少、也沒有共同的 Email / 手機。
    * `similar_name`：名字相似度 ≥ `SIMILAR_RATIO` 而且公司相同。

    已經靠 Email / 手機配到的名片不列；已經配到的那一列也不重複列。回 `[{"contact_id", "row", "reason", "score"}]`。
    """
    cards = [c for c in card_contacts or [] if isinstance(c, dict) and c.get("status") != "excluded"]
    imported = [r for r in imported or [] if isinstance(r, dict)]
    plan = {it["contact_id"]: it for it in merge_plan(cards, imported) if it["contact_id"] is not None}
    ids = [_ident(r) for r in imported]
    out: list[dict] = []
    for card in cards:
        it = plan.get(card.get("id")) or {}
        if it.get("match") in ("email", "mobile"):
            continue
        ci = _ident(card)
        names = [(ci["name"], "name"), (ci["name_en"], "name_en")]
        n = 0
        for pos, d in enumerate(ids):
            row = imported[pos].get("row")
            if it.get("row") is not None and it.get("row") == row:
                continue
            reason, score = None, 0.0
            for nm, k in names:
                if nm and nm == d[k]:
                    reason, score = "same_name", 1.0
                    break
            if reason is None and ci["company"] & d["company"]:
                for nm, k in names:
                    if nm and d[k]:
                        r = difflib.SequenceMatcher(None, nm, d[k]).ratio()
                        if r >= SIMILAR_RATIO and r > score:
                            reason, score = "similar_name", r
            if reason:
                out.append({"contact_id": card.get("id"), "row": row, "reason": reason, "score": round(score, 3)})
                n += 1
                if len(out) >= limit:
                    return out
                if n >= per_card:
                    break
    return out


# --------------------------------------------------------------------------- 合併

def _header_lang(headers: list[str]) -> str:
    """清單欄名有中文就用中文欄名加欄，全是英文就用匯出英文版的欄名（`phone_3`）。"""
    hs = [ex._s(h) for h in headers or []]
    if any(_is_cjk(h) for h in hs):
        return "zh"
    return "en" if any(h.strip() for h in hs) else "zh"


class _Builder:
    def __init__(self, headers: list[str], rows: list[list[str]], lay: _Layout, lang: str):
        self.headers, self.rows, self.lay, self.lang = headers, rows, lay, lang

    # ---- 欄
    def _unique(self, base: str, n: int) -> str:
        h = ex._header(base, n, self.lang)
        while h in self.headers:
            n += 1
            h = ex._header(base, n, self.lang)
        return h

    def add_col(self, target: str, header: Optional[str] = None) -> int:
        if header is None:
            base = target[6:] if target.startswith("notes.") else target
            header = self._unique(base, len(self.lay.fixed(target)) + 1)
        self.headers.append(header)
        for r in self.rows:
            r.append("")
        c = len(self.headers) - 1
        self.lay.map[c] = target
        return c

    def add_sub_col(self, main_col: int, sub: str) -> int:
        main = self.lay.map[main_col]
        n = self.lay.cols(main).index(main_col) + 1
        c = self.add_col(sub, self._unique(sub, n))
        self.lay.pair[main_col] = c
        return c

    # ---- 格
    def get(self, r: int, c: Optional[int]) -> str:
        return _cell(self.rows[r], c)

    def put(self, r: int, c: int, v: str) -> None:
        self.rows[r][c] = v

    def read(self, r: int) -> dict:
        return _read_row(self.rows[r], r, self.lay)

    def slot_free(self, r: int) -> bool:
        return bool(self.lay.slot_kind) and not any(self.get(r, c) for c in self.lay.slot_cols)

    def put_slot(self, r: int, value: str) -> None:
        lay = self.lay
        if lay.slot_kind == "single":
            self.put(r, lay.slot_cols[0], value)
            return
        for c in lay.slot_cols:
            self.put(r, c, "")
        first, last = _split_name(value)
        fc, lc = lay.name_parts.get("name.first"), lay.name_parts.get("name.last")
        if fc is None or lc is None:
            self.put(r, fc if fc is not None else (lc if lc is not None else lay.slot_cols[0]), value)
        else:
            self.put(r, fc, first)
            self.put(r, lc, last)

    def put_single(self, r: int, key: str, value: str) -> None:
        """單值欄位寫進空的位置：固定欄 → 空著的彈性姓名欄 → 加欄。"""
        cols = self.lay.fixed(key)
        empty = [c for c in cols if not self.get(r, c)]
        if empty:
            self.put(r, empty[0], value)
        elif cols:
            return                                           # 已經有值（不覆寫）
        elif key in ("name", "name_en") and self.slot_free(r):
            self.put_slot(r, value)
        else:
            self.put(r, self.add_col(key), value)

    def put_note(self, r: int, key: str, value: str, *, append: bool = False) -> None:
        cols = self.lay.cols("notes." + key)
        c = cols[0] if cols else self.add_col("notes." + key)
        old = self.get(r, c)
        if old and append:
            sep = "\n" if key == "memo" else ("、" if self.lang != "en" else ", ")
            self.put(r, c, old + sep + value)
        elif not old or not append:
            self.put(r, c, value)

    def _addr_empty(self, r: int, c: int) -> bool:
        return not self.get(r, c) and not any(self.get(r, p) for p in self.lay.parts.get(c, []))

    def put_multi(self, r: int, key: str, f: dict) -> None:
        """多值寫進同類型的下一個空欄；沒有空欄就加欄（帶分機 / 郵遞區號時連附屬欄一起加）。"""
        target = key if self.lay.cols(key) else ("phone" if key == "mobile" and self.lay.cols("phone") else key)
        sub = _SUB_OF.get(key) if target == key else None
        subval = ex._s(f.get(sub)).strip() if sub else ""
        value = ex._multi_cell(f, key)
        for c in self.lay.cols(target):
            empty = self._addr_empty(r, c) if target == "address" else not self.get(r, c)
            if not empty:
                continue
            pc = self.lay.pair.get(c)
            if subval and pc is None:
                value = f"{value} ext. {subval}" if sub == "ext" else f"{subval} {value}"
            self.put(r, c, value)
            if subval and pc is not None and not self.get(r, pc):
                self.put(r, pc, subval)
            return
        c = self.add_col(target)
        self.put(r, c, value)
        if subval:
            self.put(r, self.add_sub_col(c, sub), subval)

    # ---- 動作
    def merge(self, r: int, card: dict, overwrite: bool) -> None:
        for d in _compare(card, self.read(r)):
            k, kind = d["key"], d["kind"]
            if kind == "conflict" and not overwrite:
                continue
            if k in _SINGLE:
                if kind == "add":
                    self.put_single(r, k, d["card"])
                elif d.get("_slot"):
                    self.put_slot(r, d["card"])
                else:
                    self.put(r, d["_col"], d["card"])
            elif k in _MULTI:
                self.put_multi(r, k, d["_field"])
            elif k in ("ext", "zip"):
                if kind == "conflict" or not self.get(r, d["_col"]):
                    self.put(r, d["_col"], d["card"])
            elif k == "tags":
                self.put_note(r, "tags", ("、" if self.lang != "en" else ", ").join(d["_tags"]), append=True)
            elif k == "memo":
                self.put_note(r, "memo", d["card"], append=True)
            else:                                            # event / source
                cols = self.lay.cols("notes." + k)
                c = next((x for x in cols if self.get(r, x)), cols[0]) if cols else self.add_col("notes." + k)
                self.put(r, c, d["card"])

    def append(self, card: dict) -> None:
        self.rows.append([""] * len(self.headers))
        r = len(self.rows) - 1
        for k in _SINGLE:
            v = ex._single(card, k).strip()
            if v:
                self.put_single(r, k, v)
        for k in _MULTI:
            for f in ex._values(card, k):
                self.put_multi(r, k, f)
        n = ex._notes(card)
        for k in ("event", "source", "memo"):
            v = ex._s(n.get(k)).strip()
            if v:
                self.put_note(r, k, v)
        tags = ex._tags(card)
        if tags:
            self.put_note(r, "tags", ("、" if self.lang != "en" else ", ").join(tags))


def build_merged(headers: list[str], rows: list[list[str]], mapping: dict, card_contacts: Iterable[dict],
                 plan: list[dict], decisions: Optional[dict] = None) -> tuple[list[str], list[list[str]]]:
    """合併後的（欄名, 資料列）。原本的列與欄全部保留、順序不變；新增的聯絡人接在最後。

    `decisions`：`{contact_id: "add" | "merge" | "skip" | "keep_list" | "use_card"}`，沒給的照 `DEFAULT_DECISION`。

    * `merge` / `keep_list`：清單空著的格用名片補上；兩邊不一樣時保留清單的值。
    * `use_card`：補空格之外，兩邊不一樣時改成名片的值。
    * `add`：不管有沒有配到，都當成新的一列接在最後。`skip`：不動。
    * 新增的一列只寫進對應到的欄；清單沒有的欄位在最後加欄（欄名照匯出的寫法）。

    值是原值，CSV 的公式中和由 `write_table` 處理。
    """
    decisions = decisions or {}
    ncols = max([len(headers or [])] + [len(r) for r in rows or []])
    out_h = [ex._s(h) for h in headers or []] + [""] * (ncols - len(headers or []))
    out_rows = [[ex._s(v) for v in r] + [""] * (ncols - len(r)) for r in rows or []]
    lay = _Layout(clean_mapping(mapping, ncols))
    b = _Builder(out_h, out_rows, lay, _header_lang(out_h))
    cards = {c.get("id"): c for c in card_contacts or [] if isinstance(c, dict)}
    n_orig = len(out_rows)
    for it in plan or []:
        kind = it.get("kind")
        if kind not in DEFAULT_DECISION:
            continue
        card = cards.get(it.get("contact_id"))
        if card is None or card.get("status") == "excluded":
            continue
        d = decisions.get(it.get("contact_id")) or DEFAULT_DECISION[kind]
        if d not in DECISIONS:
            raise ValueError(f"不認得的處理方式：{d}")
        if d == "skip":
            continue
        row = it.get("row")
        if kind == "new" or row is None or d == "add":
            b.append(card)
            continue
        if not isinstance(row, int) or not 0 <= row < n_orig:
            raise ValueError("合併計畫的列號不正確")
        b.merge(row, card, overwrite=(d == "use_card"))
    return b.headers, b.rows


# --------------------------------------------------------------------------- 寫檔

def _unique_headers(headers: list[str], lang: str) -> list[str]:
    """表格模式要求欄名不重複、不空白（Excel 表格的限制）。"""
    out, seen = [], set()
    for i, h in enumerate(headers):
        h = ex._s(h).strip() or (f"欄 {i + 1}" if lang != "en" else f"Column {i + 1}")
        base, n = h, 2
        while h in seen:
            h = f"{base} ({n})"
            n += 1
        seen.add(h)
        out.append(h)
    return out


def _big5_missing(cells: Iterable[str]) -> list[str]:
    bad: list[str] = []
    for cell in cells:
        for ch in ex._s(cell):
            if ch not in bad:
                try:
                    ch.encode("cp950")
                except UnicodeEncodeError:
                    bad.append(ch)
    return bad


def write_table(headers: list[str], rows: list[list[str]], fmt: str, table: bool = False, bom: bool = True,
                encoding: str = "utf-8", safe: bool = True) -> bytes:
    """把（欄名, 資料列）寫成 CSV / XLSX / ODS，規則跟 `exporter` 一致：

    * CSV：`safe=True` 時用 `csv_safe` 中和公式開頭；`encoding` 是 `utf-8`（`bom=True` 加 BOM）或 `big5`，
      Big5 沒有的字丟 `exporter.EncodingError`（`chars` 最多 10 個、`total` 是總數），不自動換成問號。
    * XLSX：每一格存成文字；`table=True` 建成 Excel 表格（欄名先改成不重複）。
    * ODS：每一格 `office:value-type="string"`；`table=True` 加篩選範圍。
    """
    headers = [ex._s(h) for h in headers or []]
    width = max([len(headers)] + [len(r) for r in rows or []])
    headers = headers + [""] * (width - len(headers))
    rows = [[ex._s(v) for v in r] + [""] * (width - len(r)) for r in rows or []]
    if fmt == "csv":
        if encoding not in ex.CSV_ENCODINGS:
            raise ValueError("encoding")
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        for r in [headers, *rows]:
            w.writerow(_csv_safe.row(r) if safe else r)
        text = buf.getvalue()
        if encoding == "big5":
            bad = _big5_missing([*headers, *(c for r in rows for c in r)])
            if bad:
                raise ex.EncodingError(bad[:10], len(bad))
            return text.encode("cp950")
        return (("﻿" if bom else "") + text).encode("utf-8")
    if table:
        headers = _unique_headers(headers, _header_lang(headers))
    if fmt == "xlsx":
        return _write_xlsx(headers, rows, table)
    if fmt == "ods":
        return _write_ods(headers, rows, table)
    raise ValueError("fmt")


def _write_xlsx(headers: list[str], rows: list[list[str]], table: bool) -> bytes:
    try:
        import openpyxl
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.table import Table, TableStyleInfo
    except ImportError:                                    # pragma: no cover
        raise RuntimeError("openpyxl 未安裝，無法匯出 xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = ex.SHEET_NAME
    bold = Font(bold=True)

    def put(r: int, c: int, v: str):
        v = ex._xml_clean(v)
        cell = ws.cell(row=r, column=c)
        if v != "":
            cell.value = v
            cell.data_type = "s"            # 先給值、再強制文字：= 開頭不會變公式、數字不會變數值
        cell.number_format = "@"
        return cell

    for ci, h in enumerate(headers, start=1):
        put(1, ci, h).font = bold
    for ri, r in enumerate(rows, start=2):
        for ci, v in enumerate(r, start=1):
            put(ri, ci, v)
    for ci, wdt in enumerate(ex._col_widths(headers, rows), start=1):
        ws.column_dimensions[get_column_letter(ci)].width = wdt
    ws.freeze_panes = "A2"
    if table and rows and headers:
        tab = Table(displayName=ex.XLSX_TABLE_NAME, ref=f"A1:{get_column_letter(len(headers))}{len(rows) + 1}")
        tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False,
                                            showRowStripes=True, showColumnStripes=False)
        ws.add_table(tab)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _write_ods(headers: list[str], rows: list[list[str]], table: bool) -> bytes:
    try:
        from odf import teletype
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.style import Style, TableColumnProperties, TextProperties
        from odf.table import DatabaseRange, DatabaseRanges, Table, TableCell, TableColumn, TableRow
        from odf.text import P
    except ImportError:                                    # pragma: no cover
        raise RuntimeError("odfpy 未安裝，無法匯出 ods")
    doc = OpenDocumentSpreadsheet()
    hdr = Style(name="BcHeader", family="table-cell")
    hdr.addElement(TextProperties(fontweight="bold"))
    doc.automaticstyles.addElement(hdr)
    sheet = Table(name=ex.SHEET_NAME)
    for ci, wdt in enumerate(ex._col_widths(headers, rows)):
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
        teletype.addTextToElement(p, ex._xml_clean(v))
        tc.addElement(p)
        return tc

    for i, r in enumerate([headers, *rows]):
        tr = TableRow()
        for v in r:
            tr.addElement(cell(v, hdr if i == 0 else None))
        sheet.addElement(tr)
    doc.spreadsheet.addElement(sheet)
    if table and rows and headers:
        drs = DatabaseRanges()
        drs.addElement(DatabaseRange(name="Contacts",
                                     targetrangeaddress=ex._ods_range(ex.SHEET_NAME, len(headers), len(rows) + 1),
                                     displayfilterbuttons="true", containsheader="true"))
        doc.spreadsheet.addElement(drs)
    buf = io.BytesIO()
    doc.write(buf)
    return buf.getvalue()
