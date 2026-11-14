"""名片辨識：匯入既有聯絡人清單、比對、合併。資料全部是杜撰的。"""
from __future__ import annotations

import csv
import io
import re
import zipfile

import openpyxl
import pytest
from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import Table, TableCell, TableRow
from odf.text import P

from app.tools.business_card_reader import exporter as ex
from app.tools.business_card_reader import importer as im


def _f(key, value, status="auto", **kw):
    d = {"id": f"{key}-{value}"[:32], "key": key, "value": value, "status": status, "reason": "",
         "source": {"method": "ocr"}}
    d.update(kw)
    return d


def _card(cid, fields, notes=None, status="confirmed"):
    return {"id": cid, "status": status, "fields": fields,
            "notes": notes or {"event": "", "source": "", "tags": [], "memo": ""}}


@pytest.fixture
def cards():
    a = _card("a" * 32, [
        _f("name", "王小明"), _f("name_en", "Kevin Sample"), _f("title", "業務經理"),
        _f("department", "業務部"), _f("company", "範例科技股份有限公司"), _f("vat", "04595257"),
        _f("mobile", "0912-345-678"),
        _f("phone", "02-2345-6789", ext="001"), _f("phone", "02-2345-6790", ext="002"),
        _f("fax", "02-2345-6799"),
        _f("email", "kevin@example.com"), _f("email", "sales@example.com"),
        _f("url", "www.example.com"),
        _f("address", "臺北市中正區範例路 1 號", zip="100"),
        _f("im", "kevinw", label="LINE"),
    ], notes={"event": "2026 範例展", "source": "攤位", "tags": ["客戶", "北區"], "memo": "下週回電"})
    b = _card("b" * 32, [_f("name", "陳美麗"), _f("company", "示範貿易有限公司"), _f("phone", "=1+1"),
                         _f("email", "mei@example.org")])
    return [a, b]


def _odsbytes(rows, repeat_tail=True):
    doc = OpenDocumentSpreadsheet()
    t = Table(name="S")
    for r in rows:
        tr = TableRow()
        for v in r:
            tc = TableCell(valuetype="string")
            tc.addElement(P(text=v))
            tr.addElement(tc)
        if repeat_tail:
            tr.addElement(TableCell(numbercolumnsrepeated="16000"))
        t.addElement(tr)
    if repeat_tail:
        er = TableRow(numberrowsrepeated="1048000")
        er.addElement(TableCell(numbercolumnsrepeated="16384"))
        t.addElement(er)
    doc.spreadsheet.addElement(t)
    buf = io.BytesIO()
    doc.write(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- 讀檔：CSV

def test_csv_utf8_bom_and_plain_utf8():
    text = "姓名,公司,手機\r\n王小明,範例科技股份有限公司,0912-345-678\r\n"
    for data in (b"\xef\xbb\xbf" + text.encode("utf-8"), text.encode("utf-8")):
        h, rows = im.parse_table(data, "list.csv")
        assert h == ["姓名", "公司", "手機"]
        assert rows == [["王小明", "範例科技股份有限公司", "0912-345-678"]]


def test_csv_big5():
    data = "姓名,公司名稱,行動電話\r\n王小明,範例科技股份有限公司,0912-345-678\r\n".encode("cp950")
    h, rows = im.parse_table(data, "舊系統.CSV")
    assert h == ["姓名", "公司名稱", "行動電話"] and rows[0][0] == "王小明"
    assert im.auto_map(h) == {0: "name", 1: "company", 2: "mobile"}


def test_csv_semicolon_and_tab_and_quotes():
    data = '姓名;公司;備註\n王小明;"範例科技; 股份有限公司";"第一行\n第二行"\n'.encode("utf-8")
    h, rows = im.parse_table(data, "a.csv")
    assert h == ["姓名", "公司", "備註"]
    assert rows == [["王小明", "範例科技; 股份有限公司", "第一行\n第二行"]]
    h, rows = im.parse_table("Name\tCompany\nKevin Sample\t範例科技\n".encode("utf-8"), "a.tsv")
    assert h == ["Name", "Company"] and rows == [["Kevin Sample", "範例科技"]]


def test_csv_strips_cells_skips_blank_rows_and_pads():
    data = " 姓名 , 公司 ,手機\n\n,,\n 王小明 ,範例\n".encode("utf-8")
    h, rows = im.parse_table(data, "a.csv")
    assert h == ["姓名", "公司", "手機"] and rows == [["王小明", "範例", ""]]


def test_csv_header_only_is_ok():
    h, rows = im.parse_table("姓名,手機\n".encode("utf-8"), "a.csv")
    assert h == ["姓名", "手機"] and rows == []


# --------------------------------------------------------------------------- 讀檔：自己的匯出原樣匯回

def _expected_target(header):
    for c in ex.COLUMNS:
        if re.fullmatch(re.escape(ex.LABELS_ZH[c]) + r"( \d+)?|" + re.escape(c) + r"(_\d+)?", header):
            return ("notes." + c) if c in ex._NOTE_KEYS else c
    raise AssertionError(header)


@pytest.mark.parametrize("lang", ["zh", "en"])
@pytest.mark.parametrize("fmt", ["csv", "xlsx", "ods"])
def test_own_export_roundtrip_maps_every_column(cards, fmt, lang):
    if fmt == "csv":
        data = ex.export_csv(cards, lang=lang)
    elif fmt == "xlsx":
        data = ex.export_xlsx(cards, lang=lang, table=True)
    else:
        data = ex.export_ods(cards, lang=lang, table=True)
    h, rows = im.parse_table(data, f"contacts.{fmt}")
    eh, erows = ex.build_rows(cards, lang=lang)
    assert h == eh
    assert rows == erows, "CSV 的公式中和單引號要拿掉，其他格式原值"
    m = im.auto_map(h)
    assert set(m) == set(range(len(h))), f"每一欄都要認得：{[x for i, x in enumerate(h) if i not in m]}"
    for i, x in enumerate(h):
        assert m[i] == _expected_target(x), x
    imp = im.rows_to_contacts(h, rows, m)
    a = imp[0]
    vals = lambda k: [f["value"] for f in a["fields"] if f["key"] == k]   # noqa: E731
    assert vals("name") == ["王小明"] and vals("name_en") == ["Kevin Sample"]
    phones = [f for f in a["fields"] if f["key"] == "phone"]
    assert [(f["value"], f.get("ext")) for f in phones] == [("02-2345-6789", "001"), ("02-2345-6790", "002")]
    assert [f["norm"] for f in phones] == ["0223456789", "0223456790"]
    imf = [f for f in a["fields"] if f["key"] == "im"][0]
    assert (imf["label"], imf["value"]) == ("LINE", "kevinw")
    addr = [f for f in a["fields"] if f["key"] == "address"][0]
    assert addr["zip"] == "100"
    assert a["notes"]["tags"] == ["客戶", "北區"] and a["notes"]["memo"] == "下週回電"
    assert all(f["status"] == "confirmed" and f["source"]["method"] == "import" for f in a["fields"])
    assert [f["value"] for f in imp[1]["fields"] if f["key"] == "phone"] == ["=1+1"]
    # 自己的匯出跟自己比：每一位都是「相同」
    plan = im.merge_plan(cards, imp)
    assert [(p["kind"], p["row"]) for p in plan] == [("same", 0), ("same", 1)]


@pytest.mark.parametrize("fmt", ["vcf", "json"])
def test_own_vcf_and_json_export_roundtrip(cards, fmt):
    """之前匯出的 VCF / JSON 要能上傳回來跟這一批合併去重：每一欄都對得上，自己跟自己比每一位都是「相同」。"""
    data = ex.export_vcf(cards) if fmt == "vcf" else ex.export_json(cards)
    h, rows = im.parse_table(data, f"contacts.{fmt}")
    assert im.source_kind(f"contacts.{fmt}") == fmt
    m = im.auto_map(h)
    assert set(m) == set(range(len(h))), f"每一欄都要認得：{[x for i, x in enumerate(h) if i not in m]}"
    imp = im.rows_to_contacts(h, rows, m)
    a = imp[0]
    vals = lambda k: [f["value"] for f in a["fields"] if f["key"] == k]   # noqa: E731
    assert vals("name") == ["王小明"] and vals("name_en") == ["Kevin Sample"]
    assert vals("company") == ["範例科技股份有限公司"] and vals("department") == ["業務部"]
    assert vals("title") == ["業務經理"] and vals("vat") == ["04595257"]
    assert vals("mobile") == ["0912-345-678"] and vals("fax") == ["02-2345-6799"]
    phones = [f for f in a["fields"] if f["key"] == "phone"]
    assert [(f["value"], f.get("ext")) for f in phones] == [("02-2345-6789", "001"), ("02-2345-6790", "002")]
    assert vals("email") == ["kevin@example.com", "sales@example.com"]
    addr = [f for f in a["fields"] if f["key"] == "address"][0]
    assert addr["value"] == "臺北市中正區範例路 1 號" and addr["zip"] == "100"
    imf = [f for f in a["fields"] if f["key"] == "im"][0]
    assert (imf["label"], imf["value"]) == ("LINE", "kevinw")
    assert a["notes"]["event"] == "2026 範例展" and a["notes"]["source"] == "攤位"
    assert a["notes"]["tags"] == ["客戶", "北區"] and a["notes"]["memo"] == "下週回電"
    plan = im.merge_plan(cards, imp)
    assert [(p["kind"], p["row"]) for p in plan] == [("same", 0), ("same", 1)]


def test_vcf_from_other_apps():
    """手機 / 通訊錄匯出的 vCard：2.1 的 quoted-printable、群組前綴、折行、N 欄位、地址各段。資料杜撰。"""
    qp = "=E7=8E=8B=E5=A4=A7=E6=98=8E"          # 王大明
    data = ("BEGIN:VCARD\r\nVERSION:2.1\r\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:" + qp + ";;;;\r\n"
            "TEL;CELL:0933-000-111\r\nTEL;WORK;VOICE:02-8888-0000 ext. 12\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Amy Example\r\nN:Example;Amy;;;\r\n"
            "item1.EMAIL;TYPE=INTERNET:amy@exa\r\n mple.net\r\n"
            "ADR;TYPE=WORK:;;100 Sample Rd.;Springfield;;12345;USA\r\n"
            "NOTE:met at expo\\nsecond line\r\nEND:VCARD\r\n").encode()
    h, rows = im.parse_table(data, "phone.vcf")
    imp = im.rows_to_contacts(h, rows, im.auto_map(h))
    v0 = {f["key"]: f for f in imp[0]["fields"]}
    assert v0["name"]["value"] == "王大明" and v0["mobile"]["value"] == "0933-000-111"
    assert (v0["phone"]["value"], v0["phone"].get("ext")) == ("02-8888-0000", "12")
    v1 = imp[1]
    names = [f["value"] for f in v1["fields"] if f["key"] in ("name", "name_en")]
    assert names == ["Amy Example"]
    assert [f["value"] for f in v1["fields"] if f["key"] == "email"] == ["amy@example.net"]
    addr = [f for f in v1["fields"] if f["key"] == "address"][0]
    assert addr["value"] == "100 Sample Rd. Springfield USA" and addr["zip"] == "12345"
    assert v1["notes"]["memo"] == "met at expo\nsecond line"


def test_vcf_json_limits_and_bad_input():
    one = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:A\r\nEND:VCARD\r\n"
    with pytest.raises(im.TableError, match="5,000"):
        im.parse_table((one * (im.MAX_ROWS + 1)).encode(), "a.vcf")
    with pytest.raises(im.TableError):
        im.parse_table(b"BEGIN:VCARD\r\nEND:VCARD\r\n", "a.vcf")   # 沒有任何欄位＝空的
    with pytest.raises(im.TableError):
        im.parse_table(b"{not json", "a.json")
    with pytest.raises(im.TableError):
        im.parse_table(b'{"contacts": 3}', "a.json")
    with pytest.raises(im.TableError, match="5,000"):
        im.parse_table(('{"contacts": [' + ",".join(['{"name": "A"}'] * (im.MAX_ROWS + 1)) + "]}").encode(), "a.json")


def test_xlsx_numbers_and_formulas_are_not_evaluated():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["姓名", "統編", "公式"])
    ws.append(["王小明", 13579004, "=1+1"])
    buf = io.BytesIO()
    wb.save(buf)
    h, rows = im.parse_table(buf.getvalue(), "a.xlsx")
    assert rows[0][1] == "13579004"
    assert rows[0][2] == "", "沒有存好的計算結果時是空白，不自己算"


def test_ods_repeated_rows_and_columns_are_capped():
    data = _odsbytes([["姓名", "Email"], ["王小明", "kevin@example.com"]])
    h, rows = im.parse_table(data, "a.ods")
    assert h == ["姓名", "Email"] and rows == [["王小明", "kevin@example.com"]]


def test_ods_repeated_nonempty_columns_and_rows():
    doc = OpenDocumentSpreadsheet()
    t = Table(name="S")
    tr = TableRow()
    for v in ("標籤", "電話"):
        c = TableCell(valuetype="string")
        c.addElement(P(text=v))
        tr.addElement(c)
    t.addElement(tr)
    tr = TableRow(numberrowsrepeated="3")
    c = TableCell(valuetype="string", numbercolumnsrepeated="2")
    c.addElement(P(text="x"))
    tr.addElement(c)
    t.addElement(tr)
    doc.spreadsheet.addElement(t)
    buf = io.BytesIO()
    doc.write(buf)
    h, rows = im.parse_table(buf.getvalue(), "a.ods")
    assert rows == [["x", "x"]] * 3
    # 有值的列重複超過上限 → 擋下
    doc = OpenDocumentSpreadsheet()
    t = Table(name="S")
    tr = TableRow(numberrowsrepeated="999999")
    c = TableCell(valuetype="string")
    c.addElement(P(text="x"))
    tr.addElement(c)
    t.addElement(tr)
    doc.spreadsheet.addElement(t)
    buf = io.BytesIO()
    doc.write(buf)
    with pytest.raises(im.TableError, match="5,000"):
        im.parse_table(buf.getvalue(), "a.ods")


# --------------------------------------------------------------------------- 欄名對應

def test_outlook_headers():
    h = ["First Name", "Middle Name", "Last Name", "Title", "Company", "Department", "Job Title",
         "Business Street", "Business City", "Business Postal Code", "Business Country/Region",
         "Business Fax", "Business Phone", "Business Phone 2", "Mobile Phone", "E-mail Address",
         "E-mail Display Name", "E-mail 2 Address", "Web Page", "Notes", "Categories", "Birthday"]
    m = im.auto_map(h)
    named = {h[i]: t for i, t in m.items()}
    assert named == {
        "First Name": "name.first", "Middle Name": "name.middle", "Last Name": "name.last",
        "Company": "company", "Department": "department", "Job Title": "title",
        "Business Street": "address", "Business City": "address.part", "Business Postal Code": "zip",
        "Business Country/Region": "address.part", "Business Fax": "fax", "Business Phone": "phone",
        "Business Phone 2": "phone", "Mobile Phone": "mobile", "E-mail Address": "email",
        "E-mail 2 Address": "email", "Web Page": "url", "Notes": "notes.memo", "Categories": "notes.tags",
    }, "Outlook 的 Title 是稱謂，有 Job Title 時不對應"
    row = ["Kevin", "", "Sample", "Mr.", "範例科技股份有限公司", "業務部", "業務經理", "No. 1, Sample Rd.", "Taipei",
           "100", "Taiwan", "", "02-2345-6789", "", "+886-912-345-678", "Kevin@Example.com", "Kevin", "",
           "https://www.example.com/", "", "客戶;北區", "2000-01-01"]
    c = im.rows_to_contacts(h, [row], m)[0]
    kv = {(f["key"], f["value"]) for f in c["fields"]}
    assert ("name_en", "Kevin Sample") in kv and ("title", "業務經理") in kv
    assert ("address", "No. 1, Sample Rd. Taipei Taiwan") in kv
    assert [f["norm"] for f in c["fields"] if f["key"] == "email"] == ["kevin@example.com"]
    assert c["notes"]["tags"] == ["客戶", "北區"]
    assert c["name_slot"] == "name_en"
    # 中文姓名放在名字 / 姓氏欄：合起來是「姓＋名」
    row2 = ["小明", "", "王"] + [""] * (len(h) - 3)
    c2 = im.rows_to_contacts(h, [row2], m)[0]
    assert [(f["key"], f["value"]) for f in c2["fields"]] == [("name", "王小明")]


def test_google_headers():
    h = ["Name", "Given Name", "Family Name", "E-mail 1 - Type", "E-mail 1 - Value", "Phone 1 - Type",
         "Phone 1 - Value", "Organization 1 - Name", "Organization 1 - Title", "Organization 1 - Department",
         "Address 1 - Formatted", "Address 1 - Postal Code", "Website 1 - Value", "Labels", "Group Membership"]
    m = im.auto_map(h)
    named = {h[i]: t for i, t in m.items()}
    assert named == {
        "Name": "name", "E-mail 1 - Value": "email", "Phone 1 - Value": "phone",
        "Organization 1 - Name": "company", "Organization 1 - Title": "title",
        "Organization 1 - Department": "department", "Address 1 - Formatted": "address",
        "Address 1 - Postal Code": "zip", "Website 1 - Value": "url", "Labels": "notes.tags",
        "Group Membership": "notes.tags",
    }, "有 Name 時「名字 / 姓氏」不重複對應"


def test_chinese_synonyms():
    h = ["聯絡人", "英文姓名", "公司名稱", "職稱", "部門", "行動電話", "公司電話", "分機", "傳真", "電子郵件", "統編",
         "網站", "通訊地址", "郵遞區號", "LINE", "活動", "來源", "分類", "附註", "不認得的欄"]
    m = im.auto_map(h)
    assert [m.get(i) for i in range(len(h))] == [
        "name", "name_en", "company", "title", "department", "mobile", "phone", "ext", "fax", "email", "vat",
        "url", "address", "zip", "im", "notes.event", "notes.source", "notes.tags", "notes.memo", None]
    # 「名字」：有姓氏欄才是名字，沒有就是全名
    assert im.auto_map(["名字", "姓氏"]) == {0: "name.first", 1: "name.last"}
    assert im.auto_map(["名字", "手機"]) == {0: "name", 1: "mobile"}


def test_clean_mapping_drops_bad_entries():
    assert im.clean_mapping({"0": "name", "1": "bogus", "5": "email", "x": "name", 2: "notes.tags"}, 3) == \
        {0: "name", 2: "notes.tags"}


# --------------------------------------------------------------------------- 上限與錯誤

def test_row_limit():
    head = "姓名\n"
    ok = (head + "王小明\n" * im.MAX_ROWS).encode("utf-8")
    assert len(im.parse_table(ok, "a.csv")[1]) == im.MAX_ROWS
    with pytest.raises(im.TableError, match="5,000"):
        im.parse_table((head + "王小明\n" * (im.MAX_ROWS + 1)).encode("utf-8"), "a.csv")


def test_column_limit():
    ok = ",".join(f"c{i}" for i in range(im.MAX_COLS)) + "\n"
    assert len(im.parse_table(ok.encode(), "a.csv")[0]) == im.MAX_COLS
    bad = ",".join(f"c{i}" for i in range(im.MAX_COLS + 1)) + "\n"
    with pytest.raises(im.TableError, match="100"):
        im.parse_table(bad.encode(), "a.csv")


def test_size_limit_checked_before_parsing():
    with pytest.raises(im.TableError, match="20 MB"):
        im.parse_table(b"a" * (im.MAX_BYTES + 1), "a.csv")


@pytest.mark.parametrize("data", [b"", b"\n\n,,,\n", b"\xef\xbb\xbf"])
def test_empty(data):
    with pytest.raises(im.TableError, match="沒有資料"):
        im.parse_table(data, "a.csv")


@pytest.mark.parametrize("name,msg", [("a.pdf", "VCF 或 JSON"), ("a", "VCF 或 JSON"), ("a.xls", "XLS")])
def test_wrong_format(name, msg):
    with pytest.raises(im.TableError, match=msg):
        im.parse_table(b"abc", name)


def test_unreadable_xlsx_and_ods_and_encoding():
    for name in ("a.xlsx", "a.ods"):
        with pytest.raises(im.TableError, match="無法讀取"):
            im.parse_table(b"not a zip at all", name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    for name in ("a.xlsx", "a.ods"):
        with pytest.raises(im.TableError, match="無法讀取"):
            im.parse_table(buf.getvalue(), name)
    with pytest.raises(im.TableError, match="編碼"):
        im.parse_table(b"\xff\xff\xfa\x80\x81", "a.csv")


def test_table_error_is_value_error():
    assert issubclass(im.TableError, ValueError)
    assert all("一" <= m[0] <= "鿿" for m in im.MESSAGES)


def test_zip_bomb_like_xlsx_is_refused(monkeypatch, cards):
    monkeypatch.setattr(im, "MAX_UNPACKED", 1024 * 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("xl/worksheets/sheet1.xml", b"\0" * (2 * 1024 * 1024))
    assert len(buf.getvalue()) < 64 * 1024
    for name in ("bomb.xlsx", "bomb.ods"):
        with pytest.raises(im.TableError, match="異常龐大"):
            im.parse_table(buf.getvalue(), name)
    # 正常的檔案在同樣的上限下照樣讀得進來
    assert im.parse_table(ex.export_xlsx(cards), "ok.xlsx")[1]


# --------------------------------------------------------------------------- 合併計畫

H = ["姓名", "公司", "職稱", "Email", "手機", "電話", "備註"]


def _imported(rows, headers=H):
    return im.rows_to_contacts(headers, rows, im.auto_map(headers))


def _c(cid, **kw):
    fields = []
    for k, v in kw.items():
        for x in (v if isinstance(v, list) else [v]):
            fields.append(_f(k, x))
    return _card(cid, fields)


def test_plan_kinds():
    rows = [
        ["王小明", "範例科技股份有限公司", "業務經理", "kevin@example.com", "0912-345-678", "", ""],
        ["陳美麗", "示範貿易有限公司", "", "mei@example.org", "", "", ""],
        ["林大同", "範例科技股份有限公司", "工程師", "", "0922-000-111", "", ""],
        ["張未配", "另一家有限公司", "", "", "", "", ""],
    ]
    cs = [
        _c("same", name="王小明", company="範例科技股份有限公司", title="業務經理", email="KEVIN@example.com",
           mobile="+886 912 345 678"),
        _c("upd", name="陳美麗", company="示範貿易有限公司", title="經理", email="mei@example.org",
           phone="02-2345-6789"),
        _c("conf", name="林大同", company="範例科技股份有限公司", title="資深工程師", mobile="0922000111"),
        _c("new", name="吳新人", company="範例科技股份有限公司", email="new@example.com"),
        _card("excl", [_f("name", "王小明"), _f("email", "kevin@example.com")], status="excluded"),
    ]
    plan = im.merge_plan(cs, _imported(rows))
    got = [(p["kind"], p["contact_id"], p["row"], p["match"]) for p in plan]
    assert got == [("same", "same", 0, "email"), ("update", "upd", 1, "email"), ("conflict", "conf", 2, "mobile"),
                   ("new", "new", None, None), ("untouched", None, 3, None)], "excluded 的名片略過"
    upd = plan[1]["diffs"]
    assert {(d["key"], d["card"], d["list"], d["kind"]) for d in upd} == {
        ("title", "經理", "", "add"), ("phone", "02-2345-6789", "", "add")}
    conf = plan[2]["diffs"]
    assert conf == [{"key": "title", "card": "資深工程師", "list": "工程師", "kind": "conflict"}]
    assert all(not k.startswith("_") for p in plan for d in p["diffs"] for k in d)


def test_plan_only_compares_columns_the_list_has():
    h = ["姓名", "Email"]
    imp = _imported([["王小明", "kevin@example.com"]], h)
    c = _c("x", name="王小明", email="kevin@example.com", vat="04595257", title="業務經理", phone="02-2345-6789")
    assert im.merge_plan([c], imp)[0]["kind"] == "same"


def test_match_priority_email_over_mobile_over_name_company():
    rows = [
        ["王小明", "範例科技股份有限公司", "", "", "", "", ""],
        ["王二", "範例科技", "", "", "0912-345-678", "", ""],
        ["王三", "範例科技", "", "kevin@example.com", "", "", ""],
    ]
    imp = _imported(rows)
    c1 = _c("c1", name="王小明", company="範例科技股份有限公司", mobile="0912345678", email="kevin@example.com")
    c2 = _c("c2", name="王小明", company="範例科技 股份有限公司", mobile="0912345678")
    c3 = _c("c3", name="王 小明", company="範例科技股份有限公司")
    c4 = _c("c4", name_en="Kevin Sample", company="範例科技股份有限公司")
    plan = im.merge_plan([c1, c2, c3, c4], imp)
    assert [(p["contact_id"], p["row"], p["match"]) for p in plan[:4]] == [
        ("c1", 2, "email"), ("c2", 1, "mobile"), ("c3", 0, "name_company"), ("c4", None, None)]


def test_name_en_plus_company_and_flexible_name_column():
    h = ["Name", "Company", "E-mail"]
    imp = _imported([["Kevin Sample", "範例科技股份有限公司", ""]], h)
    assert imp[0]["name_slot"] == "name_en"
    c = _c("c", name="王小明", name_en="Kevin Sample", company="範例科技股份有限公司")
    p = im.merge_plan([c], imp)[0]
    assert (p["kind"], p["match"]) == ("same", "name_en_company"), "英文通訊錄的英文名不跟中文姓名衝突"


def test_ext_and_zip_diffs():
    h = ["姓名", "Email", "電話", "分機", "地址", "郵遞區號"]
    imp = _imported([["王小明", "kevin@example.com", "02-2345-6789", "", "臺北市中正區範例路 1 號", ""]], h)
    c = _card("c", [_f("name", "王小明"), _f("email", "kevin@example.com"),
                    _f("phone", "(02) 2345-6789", ext="001"), _f("address", "台北市中正區範例路1號", zip="100")])
    p = im.merge_plan([c], imp)[0]
    assert p["kind"] == "update"
    assert {(d["key"], d["card"], d["value"]) for d in p["diffs"]} == {
        ("ext", "001", "(02) 2345-6789"), ("zip", "100", "台北市中正區範例路1號")}


def test_possible_pairs():
    h = ["姓名", "公司", "Email"]
    imp = _imported([["王小明", "另一家有限公司", ""], ["Kevin Samples", "範例科技股份有限公司", ""],
                     ["陳美麗", "示範貿易有限公司", "mei@example.org"]], h)
    c1 = _c("c1", name="王小明", company="範例科技股份有限公司")
    c2 = _c("c2", name_en="Kevin Sample", company="範例科技股份有限公司")
    c3 = _c("c3", name="陳美麗", email="mei@example.org")
    pairs = im.possible_pairs([c1, c2, c3], imp)
    assert [(p["contact_id"], p["row"], p["reason"]) for p in pairs] == [
        ("c1", 0, "same_name"), ("c2", 1, "similar_name")], "靠 Email 配到的不列"
    assert pairs[1]["score"] >= im.SIMILAR_RATIO


# --------------------------------------------------------------------------- 合併後的表格

def test_build_merged_keeps_rows_fills_blanks_appends_new():
    h = ["編號", "姓名", "公司", "職稱", "Email", "電話", "自訂欄"]
    rows = [["1", "張未配", "另一家有限公司", "", "", "", "保留"],
            ["2", "王小明", "範例科技股份有限公司", "", "kevin@example.com", "02-2345-6789", "x"],
            ["3", "林大同", "範例科技股份有限公司", "工程師", "", "0922-000-111", ""]]
    m = im.auto_map(h)
    assert 0 not in m and 6 not in m
    cs = [
        _card("w", [_f("name", "王小明"), _f("email", "kevin@example.com"), _f("title", "業務經理"),
                    _f("phone", "02-2345-6789"), _f("phone", "02-2345-6790", ext="002"), _f("mobile", "0912-345-678"),
                    _f("vat", "04595257")]),
        _card("l", [_f("name", "林大同"), _f("company", "範例科技股份有限公司"), _f("title", "資深工程師"),
                    _f("email", "lin@example.com")]),
        _card("n", [_f("name", "吳新人"), _f("company", "範例科技股份有限公司"), _f("vat", "13579004"),
                    _f("email", "new@example.com")], notes={"event": "", "source": "", "tags": ["新"], "memo": ""}),
    ]
    imp = im.rows_to_contacts(h, rows, m)
    plan = im.merge_plan(cs, imp)
    assert [p["kind"] for p in plan] == ["update", "conflict", "new", "untouched"]
    oh, orows = im.build_merged(h, rows, m, cs, plan, {})
    assert oh[:7] == h, "原本的欄位與順序不變"
    # 手機（清單沒有手機欄，寫進電話欄）先、電話後；帶分機的那支連分機欄一起加
    assert oh[7:] == ["電話 2", "電話 3", "分機 3", "統一編號", "標籤"]
    assert len(orows) == 4 and [r[0] for r in orows[:3]] == ["1", "2", "3"]
    assert orows[0][:7] == rows[0], "沒配到的列原樣保留"
    w = dict(zip(oh, orows[1]))
    assert w["職稱"] == "業務經理" and w["自訂欄"] == "x"
    assert (w["電話 2"], w["電話 3"], w["分機 3"]) == ("0912-345-678", "02-2345-6790", "002")
    assert w["統一編號"] == "", "清單本來沒有的單值欄位不替既有的列加值"
    lin = dict(zip(oh, orows[2]))
    assert lin["職稱"] == "工程師", "衝突預設保留清單的值"
    assert lin["Email"] == "lin@example.com", "衝突的人空著的格照樣補"
    new = dict(zip(oh, orows[3]))
    assert (new["姓名"], new["公司"], new["Email"], new["統一編號"], new["標籤"], new["編號"]) == \
        ("吳新人", "範例科技股份有限公司", "new@example.com", "13579004", "新", "")
    assert all(len(r) == len(oh) for r in orows)


def test_build_merged_respects_decisions():
    h = ["姓名", "公司", "職稱", "Email"]
    rows = [["林大同", "範例科技股份有限公司", "工程師", ""]]
    m = im.auto_map(h)
    cs = [_c("l", name="林大同", company="範例科技股份有限公司", title="資深工程師", email="lin@example.com"),
          _c("n", name="吳新人", email="new@example.com")]
    plan = im.merge_plan(cs, im.rows_to_contacts(h, rows, m))
    _, r1 = im.build_merged(h, rows, m, cs, plan, {"l": "use_card", "n": "skip"})
    assert r1 == [["林大同", "範例科技股份有限公司", "資深工程師", "lin@example.com"]]
    _, r2 = im.build_merged(h, rows, m, cs, plan, {"l": "skip", "n": "add"})
    assert r2 == [rows[0], ["吳新人", "", "", "new@example.com"]]
    _, r3 = im.build_merged(h, rows, m, cs, plan, {"l": "add"})
    assert len(r3) == 3 and r3[1][0] == "林大同", "add：就算配到也另外新增一列"
    _, r4 = im.build_merged(h, rows, m, cs, plan, {"l": "keep_list"})
    assert r4[0] == ["林大同", "範例科技股份有限公司", "工程師", "lin@example.com"]
    with pytest.raises(ValueError):
        im.build_merged(h, rows, m, cs, plan, {"l": "delete"})
    assert rows == [["林大同", "範例科技股份有限公司", "工程師", ""]], "不改呼叫端的資料"


def test_build_merged_outlook_name_parts_and_english_headers():
    h = ["First Name", "Last Name", "E-mail Address"]
    rows = [["Kevin", "Sample", "kevin@example.com"]]
    m = im.auto_map(h)
    cs = [_c("k", name="王小明", name_en="Kevin Sample", email=["kevin@example.com", "sales@example.com"]),
          _c("n", name="吳新人", email="new@example.com")]
    plan = im.merge_plan(cs, im.rows_to_contacts(h, rows, m))
    assert plan[0]["kind"] == "update"
    oh, orows = im.build_merged(h, rows, m, cs, plan)
    assert oh == ["First Name", "Last Name", "E-mail Address", "email_2"], "英文清單加欄用匯出英文版的欄名"
    assert orows[0] == ["Kevin", "Sample", "kevin@example.com", "sales@example.com"]
    assert orows[1] == ["新人", "吳", "new@example.com", ""], "中文姓名認得姓氏就拆成姓與名"
    # 合併後再匯入：姓名合回來
    again = im.rows_to_contacts(oh, orows, im.auto_map(oh))
    assert [f["value"] for f in again[1]["fields"] if f["key"] == "name"] == ["吳新人"]


def test_build_merged_multiple_cards_into_one_row_and_new_columns_for_missing_fields():
    h = ["姓名", "Email"]
    rows = [["王小明", "kevin@example.com"]]
    m = im.auto_map(h)
    cs = [_c("a", name="王小明", email=["kevin@example.com", "a@example.com"]),
          _c("b", name="王小明", email=["kevin@example.com", "b@example.com"]),
          _card("n", [_f("name", "吳新人"), _f("phone", "02-2345-6789", ext="9"),
                      _f("address", "臺北市中正區範例路 1 號", zip="100"), _f("im", "wu", label="LINE")])]
    plan = im.merge_plan(cs, im.rows_to_contacts(h, rows, m))
    oh, orows = im.build_merged(h, rows, m, cs, plan)
    assert oh == ["姓名", "Email", "Email 2", "Email 3", "電話", "分機", "地址", "郵遞區號", "通訊帳號"]
    assert orows[0][:4] == ["王小明", "kevin@example.com", "a@example.com", "b@example.com"]
    assert orows[1][4:] == ["02-2345-6789", "9", "臺北市中正區範例路 1 號", "100", "LINE: wu"]


# --------------------------------------------------------------------------- 寫檔

def test_write_csv_neutralises_formulas_and_roundtrips():
    h = ["姓名", "電話"]
    rows = [["=1+1", "+886-2-2345-6789"], ["-2", "@x"], [" =SUM(A1)", "02-2345-6789"]]
    data = im.write_table(h, rows, "csv")
    assert data.startswith(b"\xef\xbb\xbf")
    got = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert got[1] == ["'=1+1", "'+886-2-2345-6789"] and got[2] == ["'-2", "'@x"]
    assert got[3] == ["' =SUM(A1)", "02-2345-6789"]
    assert im.write_table(h, rows, "csv", bom=False)[:3] != b"\xef\xbb\xbf"
    # 匯回來拿掉單引號
    _, back = im.parse_table(data, "x.csv")
    assert back[:2] == [["=1+1", "+886-2-2345-6789"], ["-2", "@x"]]


def test_write_csv_big5():
    data = im.write_table(["姓名"], [["王小明"]], "csv", encoding="big5")
    assert data.decode("cp950").startswith("姓名")
    with pytest.raises(ex.EncodingError) as e:
        im.write_table(["姓名"], [["王小明😀"]], "csv", encoding="big5")
    assert e.value.total == 1 and e.value.chars == ["😀"]
    with pytest.raises(ValueError):
        im.write_table(["a"], [], "csv", encoding="latin-1")
    with pytest.raises(ValueError):
        im.write_table(["a"], [], "pdf")


@pytest.mark.parametrize("fmt", ["xlsx", "ods"])
def test_write_xlsx_ods_text_cells_and_roundtrip(fmt):
    h = ["姓名", "", "姓名", "統編"]
    rows = [["王小明", "x", "=1+1", "04595257"]]
    data = im.write_table(h, rows, fmt, table=True)
    oh, orows = im.parse_table(data, f"o.{fmt}")
    assert oh == ["姓名", "欄 2", "姓名 (2)", "統編"], "表格模式欄名不重複、不空白"
    assert orows == rows
    if fmt == "xlsx":
        ws = openpyxl.load_workbook(io.BytesIO(data)).active
        assert ws["C2"].data_type == "s" and ws["D2"].data_type == "s"
        assert ws.tables
    plain = im.write_table(h, rows, fmt)
    assert im.parse_table(plain, f"p.{fmt}")[0] == h[:1] + [""] + h[2:]
