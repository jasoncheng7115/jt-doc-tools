"""公文撰擬的歷史案件：下載 DI 檔、批次下載、上傳 DI 檔變成案件。

2026-10-10 使用者：「公文撰擬的 歷史案件功能 裡面要提供下載 di 檔功能 還要可以批次下載 或上傳」。

要守住的事：

* **讀別人的檔案，解析器一律保守**：不載外部 DTD、不連網路、不展開任何實體 —— 但真的 DI 檔會在
  內部子集宣告附件（`<!ENTITY … NDATA …>`），所以不能一律拒收實體宣告。檔案有大小上限。
* 讀出來的是**草稿的寫法**：讀 → 產生 → 讀，主旨、段落、條列、正副本、署名都不變；
  公文撰擬沒有位置放的欄位不硬塞，講出來沒匯入哪些。只收函與簽，其他文別講出是哪一種。
* 上傳的案件**擁有者是上傳的人**；第一版的來源是「從 DI 檔匯入」（不是模型產生）；
  全文當重新檢查的依據（不然公文裡每個數字都是「找不到依據」）。
* 下載照**最新那一版**；批次下載任何一件不是你的就**整批 404**（跟單件同一句話）；
  簽辦意見、還沒有草稿的略過並講出幾件。
* 檔案裡的機關代碼要地址簿驗得過才留（同畫面送來的那一條）。
"""
from __future__ import annotations

import io
import json
import socket
import threading
import time
import uuid
import zipfile

import pytest

from app.core import official_doc_di as di
from tests.test_official_doc_di import BOOK, _letter, _sign, book  # noqa: F401
from tests.test_official_doc_sources import clean  # noqa: F401
from tests.test_official_doc_tool import BASE, _run, _user_client, fake_llm  # noqa: F401


def _di(text: str, mode: str) -> bytes:
    return di.build(text, mode)[0]


LETTER_DI = _di(_letter(), "letter")
SIGN_DI = _di(_sign(), "sign")

#: 公文系統匯出的檔案長的樣子（照檔案管理局實作範例的寫法自己寫的，內容是虛構的）：
#: BOM、內部子集宣告附件、文字前後有換行縮排、項次是半形括號、空的段名、空的署名與日期、書函
REAL_LETTER = """\ufeff<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE 函 SYSTEM "104_2_utf8.dtd" [
<!ENTITY ATTCH1 SYSTEM "附件一.pdf" NDATA DI>
<!ENTITY ATTCH2 SYSTEM "附件二.odt" NDATA DI>
<!NOTATION DI SYSTEM "">
]>
<函>
  <發文機關>
    <全銜>嘉禾市東湖區公所</全銜>
    <機關代碼>Q20000000B</機關代碼>
  </發文機關>
  <函類別 代碼="書函"/>
  <地址>嘉禾市東湖路100號</地址>
  <聯絡方式>承辦人：陳○○</聯絡方式>
  <聯絡方式>電話：</聯絡方式>
  <受文者>
    <全銜>嘉禾市政府</全銜><機關代碼>Q1000000</機關代碼>
  </受文者>
  <發文日期><年月日>中華民國115年10月2日</年月日></發文日期>
  <發文字號>
    <字>東區民字</字>
    <文號><年度>115</年度><流水號>0004321</流水號><支號/></文號>
  </發文字號>
  <速別 代碼="速件"/>
  <密等及解密條件或保密期限><密等/><解密條件或保密期限></解密條件或保密期限></密等及解密條件或保密期限>
  <附件>
    <文字>如文</文字>
    <附件檔名 附件名="ATTCH1 ATTCH2"/>
  </附件>
  <主旨>
    <文字>
      檢送本所115年度里民活動成果報告1份，請　鑒核。
    </文字>
  </主旨>
  <段落 段名="說明：">
    <文字>
    </文字>
    <條列 序號="一、">
      <文字>依據　鈞府115年9月1日府民字第1150098765號函辦理。</文字>
      <條列 序號="(一)"><文字>本年度共辦理活動12場。</文字></條列>
      <條列 序號="(二)"><文字>參加人數共計860人次。</文字></條列>
    </條列>
  </段落>
  <段落 段名="">
    <文字>其餘事項依往例辦理。</文字>
  </段落>
  <正本>
    <全銜>嘉禾市政府</全銜>
  </正本>
  <副本>
    <全銜>本所民政課</全銜><含附件>含附件</含附件>
  </副本>
  <署名>區長　林○○</署名>
  <!--書函-->
</函>
""".encode("utf-8")


# ------------------------------------------------------------------ 讀 DI 檔（核心）

def test_reading_our_own_di_gives_back_the_same_draft_and_the_same_di():
    for text, mode in ((_letter(), "letter"), (_sign(), "sign")):
        data = _di(text, mode)
        got = di.read(data)
        assert got["mode"] == mode and got["notes"] == []
        assert got["text"] == text, (mode, got["text"])
        assert _di(got["text"], mode) == data
    # 企業的函（沒有檔號那兩行）：DI 檔照樣一樣
    company = _letter(relation="company", org="範例股份有限公司", receiver="嘉禾市政府",
                      closing="請　查照", signature="範例股份有限公司　負責人　王○○")
    data = _di(company, "letter")
    assert _di(di.read(data)["text"], "letter") == data


def test_a_di_file_from_a_document_system_is_read_and_still_valid():
    got = di.read(REAL_LETTER)
    t = got["text"]
    lines = t.split("\n")
    assert "嘉禾市東湖區公所　函" in lines
    assert "主旨：檢送本所115年度里民活動成果報告1份，請　鑒核。" in lines   # 前後的換行縮排收掉
    assert "（一）本年度共辦理活動12場。" in lines                          # 半形括號 → 草稿的全形
    assert "發文字號：東區民字第1150004321號" in lines
    assert "發文日期：中華民國115年10月2日" in lines and "速別：速件" in lines
    assert "副本：本所民政課（含附件）" in lines and lines[-1] == "區長　林○○"
    assert "其餘事項依往例辦理。" in lines                                  # 沒有段名的段落不丟
    codes = {n["code"]: n["args"] for n in got["notes"]}
    assert codes["letter_kind"] == ["書函"], got["notes"]                 # 書函：講出來
    assert codes["attachment_files"] == ["2"], got["notes"]               # 附件檔不匯入：講出幾個
    assert got["org_codes"] == {"嘉禾市東湖區公所": "Q20000000B", "嘉禾市政府": "Q1000000"}
    again, _ = di.build(t, "letter")
    ok, errs = di.validate(again, "letter")
    assert ok, errs


def test_sign_fields_without_a_place_are_reported_not_dropped_silently():
    data = SIGN_DI.replace("<文號>".encode(), '<速別 代碼="最速件" /><文號>'.encode())
    # 簽的 DTD：速別在文號之後 —— 位置不重要，讀的人只看有沒有內容
    got = di.read(data)
    assert {"code": "unmapped", "args": ["速別"]} in got["notes"], got["notes"]
    assert di.read(SIGN_DI)["notes"] == []          # 空的署名、年月日不算


@pytest.mark.parametrize("data,code", [
    ('<?xml version="1.0"?><便簽><段落><文字>陳閱</文字></段落><署名/><年月日/></便簽>'.encode(), "root"),
    (b"\x89PNG\r\n\x1a\nnot xml", "not_xml"),
    ('<簽><發文機關><全銜>甲</全銜><機關代碼/></發文機關></簽>'.encode(), "no_subject"),
    (b"<" + b"x" * (di.READ_MAX_BYTES + 10) + b"/>", "too_big"),
])
def test_files_that_cannot_be_read_say_why(data, code):
    with pytest.raises(di.DiReadError) as e:
        di.read(data)
    assert e.value.code == code
    if code == "root":
        assert e.value.params == ["便簽"]


def test_text_over_the_limit_is_refused():
    with pytest.raises(di.DiReadError) as e:
        di.read(LETTER_DI, max_chars=50)
    assert e.value.code == "too_long" and e.value.params == ["50"]


def _counting_server():
    """數有幾條連線進來（外部 DTD、外部實體都不可以去抓）。"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(0.2)
    hits = []
    stop = threading.Event()

    def run():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                hits.append(1)
                c.close()
            except OSError:
                pass

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return srv.getsockname()[1], hits, stop


def test_entities_are_never_expanded_and_nothing_is_fetched(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE", encoding="utf-8")
    port, hits, stop = _counting_server()
    try:
        data = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE 簽 SYSTEM "http://127.0.0.1:{port}/104_5_utf8.dtd" [
<!ENTITY xxe SYSTEM "file://{secret}">
<!ENTITY net SYSTEM "http://127.0.0.1:{port}/e">
<!ENTITY ATTCH1 SYSTEM "a.doc" NDATA DI><!NOTATION DI SYSTEM "">
]>
<簽><發文機關><全銜>資訊室</全銜><機關代碼/></發文機關><文號><年度/><流水號/></文號>
<主旨><文字>前&xxe;中&net;後</文字></主旨><署名/><年月日/></簽>""".encode("utf-8")
        got = di.read(data)
        time.sleep(0.5)
    finally:
        stop.set()
    assert "TOP-SECRET-VALUE" not in got["text"], "外部實體被展開了"
    assert "主旨：前中後" in got["text"].split("\n"), got["text"]
    assert not hits, f"讀 DI 檔時連出去了 {len(hits)} 次（外部 DTD 或外部實體）"


def test_the_parser_never_loads_dtds_or_uses_the_network(monkeypatch):
    """上面那條驗不到「載外部 DTD」：這裡的 libxml2 已經不支援 HTTP，打開了也連不出去 ——
    換一台機器就不一定。所以直接看解析器是怎麼開的。"""
    from lxml import etree
    real, seen = etree.XMLParser, []

    def spy(*a, **kw):
        seen.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(etree, "XMLParser", spy)
    di.read(SIGN_DI)
    assert seen, "沒有走到 lxml 的解析器"
    kw = seen[-1]
    assert kw.get("load_dtd") is False and kw.get("no_network") is True, kw
    assert kw.get("resolve_entities") is False and not kw.get("huge_tree"), kw
    assert not kw.get("dtd_validation") and not kw.get("attribute_defaults"), kw


def test_entity_expansion_bombs_do_not_blow_up():
    ents = '<!ENTITY a "aaaaaaaaaa">' + "".join(
        f'<!ENTITY {n} "{("&" + p + ";") * 10}">' for p, n in zip("abcdefg", "bcdefgh"))
    for where in ("content", "attribute"):
        body = ('<主旨><文字>&h;</文字></主旨>' if where == "content"
                else '<主旨><文字>x</文字></主旨><段落 段名="&h;"/>')
        data = (f'<!DOCTYPE 簽 [{ents}]><簽><發文機關><全銜>甲</全銜><機關代碼/></發文機關>'
                f'<文號><年度/><流水號/></文號>{body}<署名/><年月日/></簽>').encode("utf-8")
        t0 = time.time()
        try:
            got = di.read(data)
            assert len(got["text"]) < 1000, (where, len(got["text"]))
        except di.DiReadError as e:
            assert e.code == "not_xml"
        assert time.time() - t0 < 3, f"{where}：花了 {time.time() - t0:.1f} 秒"


def test_big5_di_files_are_read():
    text = ('<?xml version="1.0" encoding="big5"?><簽><發文機關><全銜>資訊室</全銜><機關代碼/>'
            '</發文機關><文號><年度/><流水號/></文號><主旨><文字>測試臺灣碁</文字></主旨>'
            '<署名/><年月日/></簽>')
    got = di.read(text.encode("cp950"))
    assert "主旨：測試臺灣碁" in got["text"].split("\n")
    assert di.read(b"\xef\xbb\xbf" + text.encode("cp950"))["text"] == got["text"]


# ------------------------------------------------------------------ 上傳 → 案件

def _upload(c, *files):
    return c.post(f"{BASE}/cases/import", files=[("files", f) for f in files])


def _cases():
    from app.core import official_doc_cases
    return official_doc_cases


def test_upload_creates_a_case_owned_by_the_uploader(admin_session):
    alice = _user_client("odd_alice")
    bob = _user_client("odd_bob")
    r = _upload(alice, ("報表.di", LETTER_DI), ("簽.xml", SIGN_DI))
    assert r.status_code == 200, r.text
    got = r.json()
    assert len(got["imported"]) == 2 and got["failed"] == [], got
    cid = got["imported"][0]["case_id"]
    ids = [x["case_id"] for x in alice.get(f"{BASE}/api/cases").json()["cases"]]
    assert cid in ids
    assert cid not in [x["case_id"] for x in bob.get(f"{BASE}/api/cases").json()["cases"]], \
        "上傳的案件出現在別人的清單上"
    from app.core import user_manager
    meta = _cases().load_meta(cid)
    assert meta["owner_uid"] == user_manager.get_by_username("odd_alice")["id"]
    assert meta["origin"] == "di" and meta["latest_rev"] == 1 and meta["title"]
    row = next(x for x in alice.get(f"{BASE}/api/cases").json()["cases"] if x["case_id"] == cid)
    assert row["imported"] is True and row["di_ok"] is True
    res = alice.get(f"{BASE}/result/{cid}").json()
    assert res["draft"]["text"] == _letter() and res["imported"]["filename"] == "報表.di"
    revs = alice.get(f"{BASE}/revisions/{cid}").json()["revisions"]
    assert [(v["rev"], v["source"]) for v in revs] == [(1, "import")], "第一版的來源要是「從 DI 檔匯入」"
    assert bob.get(f"{BASE}/result/{cid}").status_code == 404


def test_imported_facts_are_their_own_evidence(client, auth_off):
    """全文是重新檢查的依據 —— 不然公文裡每個金額、日期、文號都是「找不到依據」。"""
    got = _upload(client, ("a.di", REAL_LETTER)).json()
    cid = got["imported"][0]["case_id"]
    issues = client.get(f"{BASE}/result/{cid}").json()["draft"]["issues"]
    unsupported = [i for i in issues if i["code"].endswith("_unsupported")]
    assert not unsupported, unsupported
    # 改過再檢查也一樣
    text = client.get(f"{BASE}/result/{cid}").json()["draft"]["text"]
    again = client.post(f"{BASE}/check", json={"case_id": cid, "text": text}).json()["issues"]
    assert not [i for i in again if i["code"].endswith("_unsupported")]


def test_the_relation_comes_from_a_closing_only_one_relation_uses(client, auth_off):
    cid = _upload(client, ("a.di", REAL_LETTER)).json()["imported"][0]["case_id"]
    inp = client.get(f"{BASE}/result/{cid}").json()["inputs"]
    assert inp["relation"] == "up" and inp["closing"] == "請　鑒核"      # 「鑒核」只有上行用
    assert inp["narrative"] == "檢送本所115年度里民活動成果報告1份"           # 主旨去掉期望語
    peer = REAL_LETTER.replace("請　鑒核".encode(), "請　查照".encode())
    cid = _upload(client, ("b.di", peer)).json()["imported"][0]["case_id"]
    assert client.get(f"{BASE}/result/{cid}").json()["inputs"]["relation"] == "unknown", \
        "「查照」上行以外都用 —— 不可以猜"


def test_org_codes_from_the_file_are_kept_only_when_the_address_book_agrees(client, auth_off, book):  # noqa: F811
    cid = _upload(client, ("a.di", REAL_LETTER)).json()["imported"][0]["case_id"]
    codes = client.get(f"{BASE}/result/{cid}").json()["inputs"]["org_codes"]
    assert codes == {"嘉禾市東湖區公所": "Q20000000B", "嘉禾市政府": "Q1000000"}, codes
    wrong = REAL_LETTER.replace(b"Q20000000B", b"Q99999999Z")
    cid = _upload(client, ("b.di", wrong)).json()["imported"][0]["case_id"]
    codes = client.get(f"{BASE}/result/{cid}").json()["inputs"]["org_codes"]
    assert "嘉禾市東湖區公所" not in codes, "地址簿對不上的代碼不可以留著"


def test_upload_reports_each_file_and_keeps_going(client, auth_off):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("案件/函.di", LETTER_DI)
        z.writestr("案件/附件.pdf", b"%PDF-1.4")
        z.writestr("案件/簽核.si", b"x")
        z.writestr("__MACOSX/._函.di", b"x")
        z.writestr("案件/便簽.di", '<便簽><段落><文字>x</文字></段落></便簽>'.encode())
    got = _upload(client, ("x.zip", buf.getvalue()), ("y.txt", b"hello"), ("z.di", b"not xml"),
                  ("w.di", SIGN_DI)).json()
    assert sorted(x["filename"] for x in got["imported"]) == ["w.di", "函.di"], got
    bad = {x["filename"]: x["code"] for x in got["failed"]}
    assert bad == {"y.txt": "ext", "z.di": "not_xml", "便簽.di": "root"}, bad
    assert got["skipped"] == 2, "壓縮檔裡不是 DI 檔的（附件、簽核檔）要講出幾個"
    root = next(x for x in got["failed"] if x["code"] == "root")
    assert root["args"] == ["便簽"] and "便簽" in root["error"]


def test_big5_names_inside_a_zip_are_shown_correctly(client, auth_off):
    """Windows 的壓縮工具（與官方的實作範例）寫 Big5 檔名、不標 UTF-8 —— 照樣認得出來。"""
    big5 = "簽稿.di".encode("cp950")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("X" * (len(big5) - 3) + ".di", SIGN_DI)
    raw = buf.getvalue().replace(b"X" * (len(big5) - 3) + b".di", big5)   # 本機與中央目錄兩處
    assert not zipfile.ZipFile(io.BytesIO(raw)).infolist()[0].flag_bits & 0x800
    got = _upload(client, ("x.zip", raw)).json()
    assert [x["filename"] for x in got["imported"]] == ["簽稿.di"], got


def test_upload_limits(client, auth_off, monkeypatch):
    import importlib
    r = importlib.import_module("app.tools.official_doc.router")
    monkeypatch.setattr(r, "DI_IMPORT_MAX_FILES", 2)
    too_many = _upload(client, ("a.di", SIGN_DI), ("b.di", SIGN_DI), ("c.di", SIGN_DI))
    assert too_many.status_code == 400 and "2" in too_many.json()["detail"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(5):
            z.writestr(f"{i}.di", SIGN_DI)
    assert _upload(client, ("x.zip", buf.getvalue())).status_code == 400
    assert _upload(client, ("x.zip", b"PK\x03\x04broken")).json()["failed"][0]["code"] == "bad_zip"


# ------------------------------------------------------------------ 下載、批次下載

def test_download_is_the_latest_revision(client, auth_off):
    cid = _upload(client, ("a.di", LETTER_DI)).json()["imported"][0]["case_id"]
    r = client.get(f"{BASE}/case/{cid}/di")
    assert r.status_code == 200 and r.content == LETTER_DI
    assert r.headers["x-jtdt-di-valid"] == "1" and "attachment" in r.headers["content-disposition"]
    edited = _letter().replace("汰換電腦20台", "汰換電腦25台")
    s = client.post(f"{BASE}/revisions", json={"case_id": cid, "text": edited, "base_rev": 1,
                                               "source": "edit"})
    assert s.status_code == 200, s.text
    r = client.get(f"{BASE}/case/{cid}/di")
    assert "汰換電腦25台".encode() in r.content and "汰換電腦20台".encode() not in r.content


def _zip_names(content: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(content)).namelist()


def test_batch_download_skips_what_has_no_di_and_says_how_many(client, auth_off, fake_llm):  # noqa: F811
    a = _upload(client, ("a.di", LETTER_DI), ("b.di", LETTER_DI), ("c.di", SIGN_DI)).json()["imported"]
    _, endorse, _ = _run(client, mode="endorse")
    ids = [x["case_id"] for x in a] + [endorse]
    r = client.post(f"{BASE}/cases/di", json={"case_ids": ids})
    assert r.status_code == 200, r.text
    assert r.headers["x-jtdt-di-count"] == "3" and r.headers["x-jtdt-di-skipped"] == "1"
    names = _zip_names(r.content)
    assert len(names) == 3 and len(set(names)) == 3, names          # 同一個標題兩件：檔名不撞
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.read(n) for n in names) == sorted([LETTER_DI, LETTER_DI, SIGN_DI])
    only = client.post(f"{BASE}/cases/di", json={"case_ids": [endorse]})
    assert only.status_code == 400 and "簽辦意見" in only.json()["detail"]
    assert client.get(f"{BASE}/case/{endorse}/di").status_code == 400
    for bad in ({}, {"case_ids": []}, {"case_ids": "x"}, {"case_ids": [1]}):
        assert client.post(f"{BASE}/cases/di", json=bad).status_code == 400, bad


def test_batch_download_has_a_cap(client, auth_off, monkeypatch):
    import importlib
    r = importlib.import_module("app.tools.official_doc.router")
    monkeypatch.setattr(r, "DI_BATCH_MAX", 2)
    ids = [x["case_id"] for x in _upload(client, ("a.di", SIGN_DI), ("b.di", SIGN_DI),
                                         ("c.di", SIGN_DI)).json()["imported"]]
    got = client.post(f"{BASE}/cases/di", json={"case_ids": ids})
    assert got.status_code == 400 and "2" in got.json()["detail"]
    # 同一件送兩次算一件
    assert client.post(f"{BASE}/cases/di", json={"case_ids": [ids[0], ids[0]]}).status_code == 200


def test_someone_elses_case_is_404_alone_and_in_a_batch(admin_session):
    admin, _, _ = admin_session
    alice = _user_client("odd_owner")
    eve = _user_client("odd_eve")
    cid = _upload(alice, ("a.di", LETTER_DI)).json()["imported"][0]["case_id"]
    mine = _upload(eve, ("b.di", SIGN_DI)).json()["imported"][0]["case_id"]
    r = eve.get(f"{BASE}/case/{cid}/di")
    assert r.status_code == 404
    assert r.json() == eve.get(f"{BASE}/case/{uuid.uuid4().hex}/di").json(), \
        "「不是你的」跟「不存在」要同一句話"
    b = eve.post(f"{BASE}/cases/di", json={"case_ids": [mine, cid]})
    assert b.status_code == 404, "批次裡夾一件別人的：整批 404（部分成功的話，件數就問得出哪件是別人的）"
    assert eve.post(f"{BASE}/cases/di", json={"case_ids": [mine]}).status_code == 200
    # 管理員可以下載別人的，寫稽核
    assert admin.post(f"{BASE}/cases/di", json={"case_ids": [cid]}).status_code == 200
    from app.core import audit_db
    rows = audit_db.conn().execute(
        "SELECT target FROM audit_events WHERE event_type='admin_file_override'").fetchall()
    assert f"official-doc:{cid}" in [x[0] for x in rows]


def test_a_deleted_case_cannot_be_downloaded_by_its_owner(admin_session):
    alice = _user_client("odd_deleter")
    cid = _upload(alice, ("a.di", LETTER_DI)).json()["imported"][0]["case_id"]
    assert alice.delete(f"{BASE}/case/{cid}").status_code == 200
    assert alice.get(f"{BASE}/case/{cid}/di").status_code == 404
    assert alice.post(f"{BASE}/cases/di", json={"case_ids": [cid]}).status_code == 404


# ------------------------------------------------------------------ 頁面

def test_cases_page_has_the_di_controls(client, auth_off, fake_llm):  # noqa: F811
    cid = _upload(client, ("a.di", LETTER_DI)).json()["imported"][0]["case_id"]
    _, endorse, _ = _run(client, mode="endorse")
    html = client.get(f"{BASE}/cases").text
    assert 'id="odcUpBtn"' in html and 'id="odcFile"' in html and 'accept=".di,.xml,.zip"' in html
    assert 'id="odcBatch"' in html and 'id="odcAll"' in html
    import re
    row = re.search(r'<tr data-case-id="%s".*?</tr>' % cid, html, re.S).group(0)
    assert "data-odc-di" in row and "data-odc-pick" in row and "odc-imp" in row
    assert re.search(r"data-odc-pick[^>]*>", row).group(0).count("disabled") == 0
    erow = re.search(r'<tr data-case-id="%s".*?</tr>' % endorse, html, re.S).group(0)
    assert "data-odc-di" not in erow, "簽辦意見沒有 DI 檔，不可以有下載鈕"
    assert "disabled" in re.search(r"data-odc-pick[^>]*>", erow).group(0)
