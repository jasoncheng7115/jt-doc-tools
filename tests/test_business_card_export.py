"""名片辨識：匯出（CSV / XLSX / ODS / vCard / JSON）。資料全部是杜撰的。"""
from __future__ import annotations

import csv
import glob
import io
import json
import os
import shutil
import subprocess
import zipfile

import openpyxl
import pytest
from odf.namespaces import OFFICENS, TABLENS
from odf.opendocument import load as odf_load
from odf.table import DatabaseRange, Table, TableCell, TableRow

from app.tools.business_card_reader import exporter as ex


def _f(key, value, status="auto", **kw):
    d = {"id": f"{key}-{value}"[:32], "key": key, "value": value, "status": status, "reason": "",
         "source": {"method": "ocr", "line": 0, "box": [1, 2, 3, 4], "conf": 90}}
    d.update(kw)
    return d


def _contact(cid, fields, notes=None, status="confirmed"):
    return {"id": cid, "status": status, "fields": fields,
            "notes": notes or {"event": "", "source": "", "tags": [], "memo": ""},
            "created_at": 1.0, "updated_at": 2.0}


@pytest.fixture
def contacts():
    a = _contact("a" * 32, [
        _f("name", "王小明"), _f("name_en", "Kevin Sample"), _f("title", "業務經理"),
        _f("department", "業務部"), _f("company", "範例科技股份有限公司"), _f("vat", "04595257"),
        _f("mobile", "0912-345-678", norm="0912345678"),
        _f("phone", "02-2345-6789", ext="001", label="Tel"),
        _f("phone", "+886-2-2345-6789", ext="002"),
        _f("email", "kevin@example.com"), _f("email", "sales@example.com"), _f("email", "a.wang@example.org"),
        _f("email", "gone@example.com", status="deleted"),
        _f("address", "臺北市中正區範例路 1 號", zip="100"),
        _f("im", "kevinw", label="LINE"),
    ], notes={"event": "2026 範例展", "source": "攤位", "tags": ["客戶", "北區"], "memo": "下週回電"})
    b = _contact("b" * 32, [
        _f("name", "陳美麗"), _f("company", "公司, \"引號\" 有限公司"),
        _f("phone", "=1+1"), _f("email", "@x"), _f("url", "-2"),
        _f("title", " =SUM(A1)"), _f("address", "第一行\n第二行"),
        _f("department", "+886"),
    ])
    return [a, b]


# --------------------------------------------------------------------------- 欄位配置

def test_dynamic_multi_columns(contacts):
    h, rows = ex.build_rows(contacts)
    assert [x for x in h if x.startswith("Email")] == ["Email", "Email 2", "Email 3"]
    i = h.index("電話")
    assert h[i:i + 4] == ["電話", "分機", "電話 2", "分機 2"]
    assert rows[0][h.index("分機")] == "001" and rows[0][h.index("分機 2")] == "002"
    assert rows[0][h.index("Email 3")] == "a.wang@example.org"
    assert "gone@example.com" not in sum(rows, [])
    assert rows[0][h.index("通訊帳號")] == "LINE: kevinw"
    assert rows[0][h.index("郵遞區號")] == "100"
    assert rows[0][h.index("標籤")] == "客戶、北區"
    # 整批都沒有值的多值欄位仍留一欄
    assert h.count("傳真") == 1


def test_never_truncates_many_values():
    c = _contact("c" * 32, [_f("email", f"u{i}@example.com") for i in range(7)])
    h, rows = ex.build_rows([c])
    assert h.count("Email 7") == 1
    assert "u6@example.com" in rows[0]


def test_columns_select_and_english(contacts):
    h, _ = ex.build_rows(contacts, columns=["email", "name"], lang="en")
    assert h == ["email", "email_2", "email_3", "name"]
    with pytest.raises(ValueError):
        ex.build_rows(contacts, columns=["nope"])
    h, _ = ex.build_rows(contacts, lang="en")
    assert h[:3] == ["name", "name_en", "title"]
    assert len(set(h)) == len(h)


def test_preview(contacts):
    p = ex.preview(contacts * 4, n=3)
    assert p["count"] == 8 and len(p["rows"]) == 3 and p["headers"][0] == "姓名"


# --------------------------------------------------------------------------- CSV

def _read_csv(b: bytes):
    return list(csv.reader(io.StringIO(b.decode("utf-8-sig"), newline="")))


def test_csv_bom(contacts):
    assert ex.export_csv(contacts).startswith(b"\xef\xbb\xbf")
    assert not ex.export_csv(contacts, bom=False).startswith(b"\xef\xbb\xbf")


def test_csv_quoting_roundtrip(contacts):
    rows = _read_csv(ex.export_csv(contacts))
    h = rows[0]
    assert rows[2][h.index("公司 / 機關")] == "公司, \"引號\" 有限公司"
    assert rows[2][h.index("地址")] == "第一行\n第二行"
    assert len(rows) == 3


def test_csv_formula_neutralised(contacts):
    rows = _read_csv(ex.export_csv(contacts, safe=True))
    h = rows[0]
    r = rows[2]
    assert r[h.index("電話")] == "'=1+1"
    assert r[h.index("Email")] == "'@x"
    assert r[h.index("網址")] == "'-2"
    assert r[h.index("職稱")] == "' =SUM(A1)"
    assert r[h.index("部門")] == "'+886"
    assert rows[1][h.index("電話 2")] == "'+886-2-2345-6789"
    raw = _read_csv(ex.export_csv(contacts, safe=False))
    assert raw[2][h.index("電話")] == "=1+1"
    assert raw[1][h.index("電話 2")] == "+886-2-2345-6789"
    assert "gone@example.com" not in ex.export_csv(contacts).decode("utf-8")


# --------------------------------------------------------------------------- XLSX

def _xlsx(b: bytes):
    return openpyxl.load_workbook(io.BytesIO(b)).active


def test_xlsx_cells_are_text(contacts):
    ws = _xlsx(ex.export_xlsx(contacts))
    h = [c.value for c in ws[1]]
    assert h == ex.build_rows(contacts)[0]
    assert ws.freeze_panes == "A2"
    assert ws["A1"].font.bold
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if c.value is not None:
                assert c.data_type == "s", (c.coordinate, c.value)
                assert isinstance(c.value, str)
    assert ws.cell(row=2, column=h.index("統一編號") + 1).value == "04595257"
    assert ws.cell(row=2, column=h.index("分機") + 1).value == "001"
    assert ws.cell(row=2, column=h.index("郵遞區號") + 1).value == "100"
    f = ws.cell(row=3, column=h.index("電話") + 1)
    assert f.value == "=1+1" and f.data_type == "s"


def test_xlsx_formula_not_stored(contacts):
    b = ex.export_xlsx(contacts)
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "<f>" not in sheet and "<f " not in sheet


def test_xlsx_plain_vs_table(contacts):
    ws = _xlsx(ex.export_xlsx(contacts, table=False))
    assert len(ws.tables) == 0 and not ws.auto_filter.ref
    ws = _xlsx(ex.export_xlsx(contacts, table=True))
    assert len(ws.tables) == 1
    t = list(ws.tables.values())[0]
    ncol = len(ex.build_rows(contacts)[0])
    assert t.ref == f"A1:{openpyxl.utils.get_column_letter(ncol)}3"
    assert t.displayName == "Contacts"


@pytest.mark.parametrize("table", [False, True])
def test_xlsx_zero_contacts(table):
    ws = _xlsx(ex.export_xlsx([], table=table))
    assert ws.max_row == 1 and ws["A1"].value == "姓名"
    assert len(ws.tables) == 0


# --------------------------------------------------------------------------- ODS

def _ods_rows(b: bytes):
    doc = odf_load(io.BytesIO(b))
    t = doc.spreadsheet.getElementsByType(Table)[0]
    out = []
    for tr in t.getElementsByType(TableRow):
        cells = []
        for tc in tr.getElementsByType(TableCell):
            cells.append((tc.getAttrNS(OFFICENS, "value-type"), str(tc)))
        out.append(cells)
    return doc, out


def test_ods_structure(contacts, tmp_path):
    b = ex.export_ods(contacts)
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        assert z.read("mimetype") == b"application/vnd.oasis.opendocument.spreadsheet"
    doc, rows = _ods_rows(b)
    ws = _xlsx(ex.export_xlsx(contacts))
    assert len(rows) == ws.max_row
    assert all(len(r) == ws.max_column for r in rows)
    assert [v for _, v in rows[0]] == [c.value for c in ws[1]]
    assert all(vt == "string" for r in rows for vt, _ in r)
    h = [v for _, v in rows[0]]
    assert rows[1][h.index("統一編號")][1] == "04595257"
    assert rows[2][h.index("電話")][1] == "=1+1"
    assert not doc.spreadsheet.getElementsByType(DatabaseRange)


def test_ods_table_mode(contacts):
    doc, _ = _ods_rows(ex.export_ods(contacts, table=True))
    drs = doc.spreadsheet.getElementsByType(DatabaseRange)
    assert len(drs) == 1
    assert drs[0].getAttrNS(TABLENS, "display-filter-buttons") == "true"
    doc, rows = _ods_rows(ex.export_ods([], table=True))
    assert len(rows) == 1 and not doc.spreadsheet.getElementsByType(DatabaseRange)


def test_same_rows_and_headers_across_formats(contacts):
    csv_rows = _read_csv(ex.export_csv(contacts))
    ws = _xlsx(ex.export_xlsx(contacts, table=True))
    _, ods = _ods_rows(ex.export_ods(contacts, table=True))
    assert len(csv_rows) == ws.max_row == len(ods)
    assert csv_rows[0] == [c.value for c in ws[1]] == [v for _, v in ods[0]]


def _find_soffice():
    for p in [shutil.which("soffice"), *glob.glob("/opt/oxoffice*/program/soffice")]:
        if p and os.path.exists(p):
            return p
    return None


@pytest.mark.skipif(_find_soffice() is None, reason="沒有 soffice")
def test_ods_opens_in_office(contacts, tmp_path):
    src = tmp_path / "contacts.ods"
    src.write_bytes(ex.export_ods(contacts, table=True))
    prof = tmp_path / "profile"
    try:
        subprocess.run([_find_soffice(), f"-env:UserInstallation={prof.as_uri()}", "--headless",
                        "--convert-to", "csv:Text - txt - csv (StarCalc):44,34,76", "--outdir", str(tmp_path),
                        str(src)], check=True, timeout=120, capture_output=True)
    except subprocess.TimeoutExpired:
        pytest.skip("soffice 逾時")
    out = tmp_path / "contacts.csv"
    rows = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8"), newline="")))
    assert rows[0] == ex.build_rows(contacts)[0]
    assert rows[1][0] == "王小明"
    assert rows[1][rows[0].index("統一編號")] == "04595257"


# --------------------------------------------------------------------------- vCard

def _unfold(b: bytes) -> list[str]:
    return b.decode("utf-8").replace("\r\n ", "").split("\r\n")


def test_vcf_basic(contacts):
    b = ex.export_vcf(contacts)
    text = b.decode("utf-8")
    assert "\n" not in text.replace("\r\n", "")
    assert text.count("BEGIN:VCARD") == 2 and text.count("END:VCARD") == 2
    lines = _unfold(b)
    assert "FN:王小明" in lines
    assert "N:王小明;;;;" in lines
    assert "VERSION:3.0" in lines
    assert "TEL;TYPE=CELL:0912-345-678" in lines
    assert "TEL;TYPE=WORK,VOICE:02-2345-6789 ext. 001" in lines
    assert "TEL;TYPE=WORK,VOICE:+886-2-2345-6789 ext. 002" in lines
    first = lines[:lines.index("END:VCARD")]
    assert sum(1 for x in first if x.startswith("EMAIL;TYPE=INTERNET:")) == 3
    assert "ADR;TYPE=WORK:;;臺北市中正區範例路 1 號;;;100;" in lines
    assert "ORG:範例科技股份有限公司;業務部" in lines
    assert not any("gone@example.com" in x for x in lines)
    # 跳脫：逗號、分號、換行
    assert 'ORG:公司\\, "引號" 有限公司;+886' in lines
    assert "ADR;TYPE=WORK:;;第一行\\n第二行;;;;" in lines
    assert any(x.startswith("NOTE:") and "下週回電" in x for x in lines)


def test_vcf_escape_semicolon_and_folding():
    addr = "臺北市中正區範例路一段一百號；" * 8 + "A;B,C"
    c = _contact("d" * 32, [_f("name", "林;小,華"), _f("address", addr)])
    b = ex.export_vcf([c])
    raw_lines = b.decode("utf-8").split("\r\n")
    assert all(len(x.encode("utf-8")) <= 75 for x in raw_lines)
    assert any(x.startswith(" ") for x in raw_lines)
    lines = _unfold(b)
    assert "FN:林\\;小\\,華" in lines
    esc = addr.replace(";", "\\;").replace(",", "\\,")
    assert f"ADR;TYPE=WORK:;;{esc};;;;" in lines
    b.decode("utf-8")       # 折行沒有切斷多位元組字元


def test_vcf_fn_fallback():
    c = _contact("e" * 32, [_f("company", "範例科技股份有限公司")])
    assert "FN:範例科技股份有限公司" in _unfold(ex.export_vcf([c]))


# --------------------------------------------------------------------------- JSON

def test_json_raw_values(contacts):
    b = ex.export_json(contacts)
    assert "王小明".encode("utf-8") in b
    d = json.loads(b)
    a, bb = d["contacts"]
    assert a["name"] == "王小明" and a["vat"] == "04595257"
    assert [e["value"] for e in a["emails"]] == ["kevin@example.com", "sales@example.com", "a.wang@example.org"]
    assert a["phones"][0] == {"kind": "phone", "value": "02-2345-6789", "ext": "001", "label": "Tel",
                              "status": "auto"}
    assert a["phones"][1]["value"] == "+886-2-2345-6789"
    assert a["addresses"][0]["zip"] == "100"
    assert a["ims"][0]["label"] == "LINE"
    assert a["notes"]["tags"] == ["客戶", "北區"]
    assert bb["phones"][0]["value"] == "=1+1"
    assert "fields" not in a
    assert all("source" not in it for c in d["contacts"] for k in ("phones", "emails", "addresses", "ims")
               for it in c[k])
    assert "box" not in b.decode("utf-8")


def test_json_include_source(contacts):
    d = json.loads(ex.export_json(contacts, include_source=True))
    a = d["contacts"][0]
    assert a["emails"][0]["source"]["box"] == [1, 2, 3, 4]
    assert all(f["status"] != "deleted" for f in a["fields"])
