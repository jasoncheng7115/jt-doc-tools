"""名片辨識：「名片總表」版面的匯出與匯入（客戶 2026-11-24 給了範例檔，要能照這個格式匯出、也能匯入）。

版面：第 1 列標題「名片總表」與「更新日期: YYYY.MM.DD」、第 2 列欄名（新增日期、性質、公司全名、Company Name、部門、
Department、姓名、Full Name、職稱、Job Title、電話、傳真、手機、Email、公司網址、地址、Address、取得來源、備註），
黃底是手動輸入的欄、灰底是名片上的；另有「性質清單」與「常用」兩張工作表。同一格裡的多筆值換行。

驗的是：

* 匯出（`exporter.roster_rows` / `export_roster_xlsx`）：欄的對應、多筆換行、分機、郵遞區號、中英文地址分兩欄、
  版面沒有的欄位（統編、通訊帳號）有值才接在最後、每一格都是文字（`=` 開頭不變公式）、只有格式正確的 Email / 網址才有超連結
* 匯入（`importer`）：欄名在第 2 列也找得到、英文欄名對到外文欄位、「性質」→ 標籤、同一格換行的多筆拆開；
  一般清單（欄名在第 1 列、只有英文欄名）不受影響
* 匯出再匯回，欄位一個都不少
* API：批次與名片簿 `layout=roster`、搭配其他格式 400；整合名片總表時照原本的版面寫回、新增的人填今天的日期；
  名片簿「匯入檔案」認得這個版面

資料全部杜撰（王小明、Example Tech、example.com）；客戶給的範例檔內容不進任何測試與文件。
"""
from __future__ import annotations

import io
import time

import openpyxl
import pytest

from app.tools.business_card_reader import exporter as E
from app.tools.business_card_reader import importer as I
from tests.test_business_card_phase2 import API, _batch, anon, ocr  # noqa: F401  (fixture)

HEADERS = [h for h, _k, _w in E.ROSTER_COLUMNS]


def F(key, value, **extra):
    return {"key": key, "value": value, "status": "confirmed", **extra}


def _contact(**over):
    c = {"id": "a", "created_at": time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1)),
         "notes": {"event": "秋季研討會", "source": "朋友介紹", "tags": ["顧問", "銀行"], "memo": "=SUM(A1)"},
         "fields": [F("name", "王小明"), F("name_en", "Ming Wang"), F("company", "範例科技股份有限公司"),
                    F("company_en", "Example Tech Co., Ltd."), F("title", "經理"), F("title_en", "Manager"),
                    F("department", "業務部"), F("department_en", "Sales Dept."),
                    F("phone", "02-2345-6789", ext="101"), F("phone", "02-2345-0000"), F("mobile", "0912-345-678"),
                    F("fax", "02-2345-9999"), F("email", "ming@example.com"), F("url", "https://www.example.com"),
                    F("address", "台北市信義區範例路 1 號", zip="110"), F("address", "No. 1, Example Rd., Taipei"),
                    F("vat", "12345678"), F("im", "ming.wang", label="LINE"),
                    F("email", "old@example.com", status="deleted")]}
    c.update(over)
    return c


def _sheet(data: bytes, name: str = E.ROSTER_SHEET):
    return openpyxl.load_workbook(io.BytesIO(data))[name]


# --------------------------------------------------------------------------- 匯出

def test_roster_columns_and_cells():
    h, rows = E.roster_rows([_contact()])
    assert h[:19] == HEADERS and h[19:] == ["統一編號", "通訊帳號"], "版面沒有的欄位有值才接在最後"
    r = dict(zip(h, rows[0]))
    assert r["新增日期"] == "2026.11.20"
    assert r["性質"] == "顧問、銀行"
    assert (r["公司全名"], r["Company Name"]) == ("範例科技股份有限公司", "Example Tech Co., Ltd.")
    assert (r["姓名"], r["Full Name"], r["職稱"], r["Job Title"]) == ("王小明", "Ming Wang", "經理", "Manager")
    assert (r["部門"], r["Department"]) == ("業務部", "Sales Dept.")
    assert r["電話"] == "02-2345-6789 ext. 101\n02-2345-0000", "多筆換行、分機接在後面"
    assert r["Email"] == "ming@example.com", "刪掉的欄位不匯出"
    assert r["地址"] == "110 台北市信義區範例路 1 號" and r["Address"] == "No. 1, Example Rd., Taipei", "中文、英文地址各一欄"
    assert r["取得來源"] == "秋季研討會、朋友介紹"
    assert (r["統一編號"], r["通訊帳號"]) == ("12345678", "LINE: ming.wang")
    # 沒有人有統編、通訊帳號：不多出欄
    plain = _contact(fields=[F("name", "陳大文")])
    assert E.roster_rows([plain])[0] == HEADERS


def test_roster_date_ignores_bad_timestamps():
    for bad in (None, "", "x", float("nan"), -5, 9e12):
        assert E.roster_rows([_contact(created_at=bad)])[1][0][0] == ""


def test_roster_xlsx_layout():
    data = E.export_roster_xlsx([_contact()], today="2026.11.24")
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["名片總表", "性質清單", "常用"]
    ws = wb["名片總表"]
    assert ws["A1"].value == "名片總表" and ws["C1"].value == "更新日期: 2026.11.24"
    assert [ws.cell(2, c).value for c in range(1, 20)] == HEADERS
    assert ws.freeze_panes == "A3" and ws.auto_filter.ref == "A2:U3"
    for c in range(1, 20):
        h = ws.cell(2, c)
        want = "FFFFFF00" if h.value in E.ROSTER_MANUAL else "FFCCCCCC"
        assert h.fill.fgColor.rgb == want, h.value
        assert (h.comment is not None) == (h.value in E.ROSTER_MANUAL), "黃底的欄有「可手動輸入」說明"
    assert ws.column_dimensions["N"].width == pytest.approx(31.4)
    # 每一格都是文字：= 開頭不變公式、手機開頭的 0 留著
    memo, mobile = ws["S3"], ws["M3"]
    assert memo.data_type == "s" and memo.value == "=SUM(A1)"
    assert mobile.data_type == "s" and mobile.value == "0912-345-678"
    assert ws["K3"].alignment.wrap_text, "多筆換行要能換行顯示"
    assert ws["N3"].hyperlink.target == "mailto:ming@example.com"
    assert ws["O3"].hyperlink.target == "https://www.example.com"
    tags = [c.value for c in wb["性質清單"]["A"]]
    assert tags[1:4] == ["性質", "顧問", "銀行"]
    fav = wb["常用"]
    assert fav["A1"].value == "常用名單" and [fav.cell(2, c).value for c in range(1, 20)] == HEADERS
    assert fav.max_row == 2, "常用是空白的，給使用者自己挑"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "ftp://example.com", "https://a.example.com\nhttps://b.example.com"])
def test_only_safe_links(url):
    c = _contact(fields=[F("name", "王小明"), F("email", "not an email"), *[F("url", u) for u in url.split("\n")]])
    ws = _sheet(E.export_roster_xlsx([c]))
    assert ws["N3"].hyperlink is None and ws["O3"].hyperlink is None
    assert ws["O3"].value == url, "值照寫，只是不加連結"


def test_empty_export_has_headers_only():
    ws = _sheet(E.export_roster_xlsx([]))
    assert [ws.cell(2, c).value for c in range(1, 20)] == HEADERS and ws.max_row == 2


# --------------------------------------------------------------------------- 匯入

def _template_file(rows, *, title=True) -> bytes:
    """仿客戶範例檔的形狀：第 1 列標題、第 2 列欄名、性質清單與常用兩張工作表（內容杜撰）。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "名片總表"
    if title:
        ws["A1"], ws["C1"] = "名片總表", "更新日期: 2026.11.18"
    head = 2 if title else 1
    for i, h in enumerate(HEADERS, start=1):
        ws.cell(head, i, h)
    start = head + 1
    for ri, r in enumerate(rows, start=start):
        for ci, v in enumerate(r, start=1):
            ws.cell(ri, ci, v)
    wb.create_sheet("性質清單")["A2"] = "性質"
    fav = wb.create_sheet("常用")
    fav["A3"] = "=名片總表!A3"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ROW = ["2026.11.11", "顧問", "範例科技股份有限公司", "Example Tech Co., Ltd.", "總經理室", "President Office", "王小明",
       "Ming Wang", "副總", "Vice President", "(02)2345-6789", "(02)2345-0000", "0912-345-678", "ming@example.com",
       "https://www.example.com/", "台北市信義區範例路 1 號", "No. 1, Example Rd., Taipei", "秋季研討會", "素食"]


def test_import_template_file():
    h, rows = I.parse_table(_template_file([ROW, ["", "銀行", "範例銀行"]]), "名片總表.xlsx")
    assert h == HEADERS, "欄名在第 2 列，第 1 列是標題"
    assert len(rows) == 2
    m = {h[i]: t for i, t in I.auto_map(h).items()}
    assert m == {"性質": "notes.tags", "公司全名": "company", "Company Name": "company_en", "部門": "department",
                 "Department": "department_en", "姓名": "name", "Full Name": "name_en", "職稱": "title",
                 "Job Title": "title_en", "電話": "phone", "傳真": "fax", "手機": "mobile", "Email": "email",
                 "公司網址": "url", "地址": "address", "Address": "address", "取得來源": "notes.source",
                 "備註": "notes.memo"}, "新增日期不對應（保留原樣）"
    ct = I.rows_to_contacts(h, rows, I.auto_map(h))
    got = {(f["key"], f["value"]) for f in ct[0]["fields"]}
    assert {("company_en", "Example Tech Co., Ltd."), ("name_en", "Ming Wang"), ("title_en", "Vice President"),
            ("department_en", "President Office"), ("address", "No. 1, Example Rd., Taipei")} <= got
    assert ct[0]["notes"]["tags"] == ["顧問"] and ct[0]["notes"]["source"] == "秋季研討會"
    assert {(f["key"], f["value"]) for f in ct[1]["fields"]} == {("company", "範例銀行")}, "只有公司的列也讀得到"


def test_round_trip_keeps_every_field():
    src = _contact()
    h, rows = I.parse_table(E.export_roster_xlsx([src]), "x.xlsx")
    ct = I.rows_to_contacts(h, rows, I.auto_map(h))[0]
    live = [f for f in src["fields"] if f.get("status") != "deleted"]
    want = sorted((f["key"], f["value"] if f["key"] != "address" or not f.get("zip") else f"{f['zip']} {f['value']}",
                   f.get("ext", "")) for f in live)
    got = sorted((f["key"], f["value"], f.get("ext", "")) for f in ct["fields"])
    assert got == want
    assert ct["notes"]["tags"] == ["顧問", "銀行"] and ct["notes"]["memo"] == "=SUM(A1)"


def test_multi_values_in_one_cell_are_split():
    row = ["", "", "範例科技", "", "", "", "王小明", "", "", "", "02-1111-2222\n02-3333-4444", "", "", "a@example.com\nb@example.com",
           "https://a.example.com\nhttps://b.example.com", "", "", "", ""]
    h, rows = I.parse_table(_template_file([row]), "x.xlsx")
    ct = I.rows_to_contacts(h, rows, I.auto_map(h))[0]
    by = lambda k: [f["value"] for f in ct["fields"] if f["key"] == k]      # noqa: E731
    assert by("phone") == ["02-1111-2222", "02-3333-4444"]
    assert by("email") == ["a@example.com", "b@example.com"]
    assert by("url") == ["https://a.example.com", "https://b.example.com"]


def test_ordinary_lists_are_unchanged():
    # 欄名在第 1 列：照舊
    h, rows = I.parse_table(b"\xef\xbb\xbf\xe5\xa7\x93\xe5\x90\x8d,Email\nKevin,k@example.com\n", "a.csv")
    assert h == ["姓名", "Email"] and len(rows) == 1
    # 只有英文欄名（Outlook / Google）：Name / Company / Job Title 照原本對應，不會變成外文欄位
    m = I.auto_map(["Name", "Company", "Job Title", "Department"])
    assert m == {0: "name", 1: "company", 2: "title", 3: "department"}
    # 第 1 列是欄名、資料列裡剛好有像欄名的字：不換
    assert I._header_index([["姓名", "電話", "Email"], ["電話", "手機", "傳真"]]) == 0
    # 標題列＋欄名列
    assert I._header_index([["名片總表", "", "更新日期: 2026.11.18"], HEADERS, ROW]) == 1


def test_fill_added_date_only_for_new_rows():
    rows = [["2026.11.01", "a"], ["", "b"], ["", "c"], ["2026.10.01", "d"]]
    I.fill_added_date(["新增日期", "姓名"], rows, 2, today="2026.11.24")
    assert [r[0] for r in rows] == ["2026.11.01", "", "2026.11.24", "2026.10.01"]
    I.fill_added_date(["姓名"], rows, 0, today="x")         # 沒有新增日期欄：不動


# --------------------------------------------------------------------------- API

@pytest.fixture
def client(anon):  # noqa: F811
    """匿名（未啟用登入）的批次同時最多 20 個：整批跑時前面的測試會留下批次，先清掉、做完也清掉自己的。"""
    def clear():
        for it in anon.get(f"{API}/batches").json().get("items", []):
            anon.delete(f"{API}/batches/{it['id']}")
    clear()
    yield anon
    clear()


def _confirm_all(c, b):
    for ct in b["contacts"]:
        c.put(f"{API}/batches/{b['id']}/contacts/{ct['id']}", json={"status": "confirmed"})


def test_batch_export_roster(client, ocr):  # noqa: F811
    c = client
    b = _batch(c, 2)
    _confirm_all(c, b)
    r = c.post(f"{API}/batches/{b['id']}/export", json={"format": "xlsx", "layout": "roster", "scope": "all"})
    assert r.status_code == 200, r.text
    ws = _sheet(r.content)
    assert ws["A1"].value == "名片總表" and [ws.cell(2, i).value for i in range(1, 20)] == HEADERS
    names = {ws.cell(i, 7).value for i in range(3, ws.max_row + 1)}
    assert "王小明" in names
    assert ws["A3"].value == time.strftime("%Y.%m.%d"), "新增日期＝辨識那天"
    for bad in ({"format": "csv", "layout": "roster"}, {"format": "ods", "layout": "roster"},
                {"format": "xlsx", "layout": "fancy"}):
        assert c.post(f"{API}/batches/{b['id']}/export", json={**bad, "scope": "all"}).status_code == 400, bad


def test_list_merge_keeps_the_roster_layout(client, ocr):  # noqa: F811
    c = client
    b = _batch(c, 3)
    _confirm_all(c, b)
    row = list(ROW)
    row[13], row[12] = "kevin@example.com", ""                   # 名片上的王小明（同 Email），清單缺手機
    up = c.post(f"{API}/batches/{b['id']}/list-merge",
                files={"file": ("名片總表.xlsx", _template_file([row]), "application/octet-stream")}).json()
    assert up["rows"] == 1, up
    tok = up["token"]
    r = c.post(f"{API}/batches/{b['id']}/list-merge/{tok}/download",
               json={"mapping": up["mapping"], "scope": "all", "format": "xlsx", "table": True})
    assert r.status_code == 200, r.text
    ws = _sheet(r.content)
    assert ws["A1"].value == "名片總表", "上傳的是名片總表：照原本的版面寫回"
    assert [ws.cell(2, i).value for i in range(1, 20)] == HEADERS
    assert ws["A3"].value == "2026.11.11" and ws["G3"].value == "王小明", "原本的列不動"
    assert ws["M3"].value == "0912-345-678", "清單缺的手機補上"
    new_rows = [i for i in range(4, ws.max_row + 1) if ws.cell(i, 7).value or ws.cell(i, 8).value]
    assert new_rows, "新的人接在後面"
    assert all(ws.cell(i, 1).value == time.strftime("%Y.%m.%d") for i in new_rows), "新增的人填上今天的新增日期"


def test_list_merge_other_list_to_roster(client, ocr):  # noqa: F811
    c = client
    b = _batch(c, 1)
    _confirm_all(c, b)
    wb = openpyxl.Workbook()
    wb.active.append(["姓名", "公司", "Email"])
    wb.active.append(["林小華", "範例貿易有限公司", "hua@example.net"])
    buf = io.BytesIO()
    wb.save(buf)
    up = c.post(f"{API}/batches/{b['id']}/list-merge", files={"file": ("a.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    r = c.post(f"{API}/batches/{b['id']}/list-merge/{up['token']}/download",
               json={"scope": "all", "format": "xlsx", "layout": "roster"})
    assert r.status_code == 200, r.text
    ws = _sheet(r.content)
    assert [ws.cell(2, i).value for i in range(1, 20)] == HEADERS
    assert "林小華" in {ws.cell(i, 7).value for i in range(3, ws.max_row + 1)}
    # 一般版面照舊
    r = c.post(f"{API}/batches/{b['id']}/list-merge/{up['token']}/download", json={"scope": "all", "format": "xlsx"})
    assert openpyxl.load_workbook(io.BytesIO(r.content)).active["A1"].value == "姓名"


# --------------------------------------------------------------------------- 名片簿：匯出與匯入檔案

from tests.test_business_card_books import (_batch as _book_batch, _personal, _save_all_new,  # noqa: E402,F401
                                            fake_ocr, people)


def test_book_export_and_import_roster(people):  # noqa: F811
    clients, _bid, _ids, _ = people
    alice = clients["alice"]
    pa = _personal(alice)
    _save_all_new(alice, _book_batch(alice), pa["id"])
    r = alice.post(f"{API}/books/{pa['id']}/export", json={"format": "xlsx", "layout": "roster"})
    assert r.status_code == 200, r.text
    ws = _sheet(r.content)
    assert [ws.cell(2, i).value for i in range(1, 20)] == HEADERS
    assert ws["A3"].value == time.strftime("%Y.%m.%d"), "新增日期＝存進名片簿那天"
    assert alice.post(f"{API}/books/{pa['id']}/export", json={"format": "csv", "layout": "roster"}).status_code == 400
    # 名片簿「匯入檔案」認得名片總表：標題列不當欄名、英文欄名對到外文欄位、已有的人合併
    row = list(ROW)
    row[6], row[12], row[13] = "林小華", "0933-555-777", "hua@example.net"
    r = alice.post(f"{API}/books/{pa['id']}/import",
                   files={"file": ("名片總表.xlsx", _template_file([row]), "application/octet-stream")})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["rows"] == 1 and [it["kind"] for it in up["items"]] == ["new"], up
    assert alice.post(f"{API}/books/{pa['id']}/import/{up['token']}/apply", json={}).status_code == 200
    lst = alice.get(f"{API}/books/{pa['id']}/people", params={"q": "林小華"}).json()["items"]
    p = alice.get(f"{API}/books/{pa['id']}/people/{lst[0]['id']}").json()
    vals = {(f["key"], f["value"]) for f in p["fields"]}
    assert {("company_en", "Example Tech Co., Ltd."), ("title_en", "Vice President"), ("name", "林小華")} <= vals
    assert (p["notes"] or {}).get("tags") == ["顧問"]
