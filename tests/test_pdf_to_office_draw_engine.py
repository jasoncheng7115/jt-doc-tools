"""pdf-to-office 第三引擎 draw（版面重現）測試。

分兩層：
* 單元層：自製最小 .odg（不需 soffice）測 ``_build_writer_odt`` 的重組邏輯 —
  真 Writer mimetype、頁數、每頁尺寸、形狀頁面錨定、zip-slip 過濾、空頁、
  安全解析器（XXE）、大小防護。
* 端到端層：需 LibreOffice-draw / OxOffice，跑真 PDF → odt / docx；無則 skip。
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from app.core.office_convert import find_soffice
from app.tools.pdf_to_office.engines import draw_engine as de

WRITER_MIME = "application/vnd.oasis.opendocument.text"


# --------------------------------------------------------------------------
# helper：自製最小 Draw .odg（office:drawing > draw:page > draw:frame）
# --------------------------------------------------------------------------
def _make_odg(path: Path, pages: list[list[tuple]], pics: dict | None = None):
    """pages: 每頁一個 shape list，shape = (x_cm, y_cm, w_cm, h_cm, text)。"""
    ns = (
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
        'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
    )
    body_pages = ""
    for shapes in pages:
        frames = ""
        for (x, y, w, h, t) in shapes:
            frames += (
                '<draw:frame draw:style-name="gr1" svg:x="%.2fcm" svg:y="%.2fcm" '
                'svg:width="%.2fcm" svg:height="%.2fcm">'
                "<draw:text-box><text:p>%s</text:p></draw:text-box></draw:frame>"
                % (x, y, w, h, t)
            )
        body_pages += '<draw:page draw:name="p">%s</draw:page>' % frames
    autostyles = (
        '<office:automatic-styles>'
        '<style:style style:name="gr1" style:family="graphic">'
        '<style:graphic-properties/></style:style>'
        '</office:automatic-styles>'
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content %s>%s'
        '<office:body><office:drawing>%s</office:drawing></office:body>'
        '</office:document-content>' % (ns, autostyles, body_pages)
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.graphics",
                   zipfile.ZIP_STORED)
        z.writestr("content.xml", content)
        for name, data in (pics or {}).items():
            z.writestr(name, data)


# --------------------------------------------------------------------------
# 單元層（不需 soffice）
# --------------------------------------------------------------------------
def test_build_writer_odt_real_writer_mimetype(tmp_path):
    odg = tmp_path / "a.odg"
    _make_odg(odg, [[(2, 2, 5, 1, "hello")]])
    odt = tmp_path / "a.odt"
    n_pages, n_imgs = de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    assert n_pages == 1
    with zipfile.ZipFile(odt) as z:
        # mimetype 必須是真 Writer（非 graphics）且為第一個且不壓縮
        assert z.read("mimetype").decode() == WRITER_MIME
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED


def test_build_writer_odt_multipage(tmp_path):
    odg = tmp_path / "m.odg"
    _make_odg(odg, [[(2, 2, 5, 1, "p1")], [(2, 2, 5, 1, "p2")], [(2, 2, 5, 1, "p3")]])
    odt = tmp_path / "m.odt"
    n_pages, _ = de._build_writer_odt(odg, odt, [(21.0, 29.7)] * 3)
    assert n_pages == 3
    content = zipfile.ZipFile(odt).read("content.xml").decode()
    # 每頁一段落，形狀錨定到對應頁碼
    assert 'text:anchor-type="page"' in content
    assert 'text:anchor-page-number="3"' in content


def test_build_writer_odt_per_page_sizes(tmp_path):
    odg = tmp_path / "s.odg"
    _make_odg(odg, [[(1, 1, 3, 1, "a")], [(1, 1, 3, 1, "b")], [(1, 1, 3, 1, "c")]])
    odt = tmp_path / "s.odt"
    sizes = [(21.0, 29.7), (14.8, 21.0), (29.7, 21.0)]  # A4 / A5 / 橫向
    de._build_writer_odt(odg, odt, sizes)
    styles = zipfile.ZipFile(odt).read("styles.xml").decode()
    # 三種不同尺寸 → 三個 page-layout
    assert styles.count("<style:page-layout ") == 3
    assert "14.800cm" in styles and "29.700cm" in styles
    # ODF 順序：office:styles → automatic-styles → master-styles
    assert styles.index("<office:styles") < styles.index("automatic-styles") \
        < styles.index("master-styles")


def test_build_writer_odt_same_size_dedup(tmp_path):
    odg = tmp_path / "d.odg"
    _make_odg(odg, [[(1, 1, 3, 1, "a")], [(1, 1, 3, 1, "b")]])
    odt = tmp_path / "d.odt"
    de._build_writer_odt(odg, odt, [(21.0, 29.7), (21.0, 29.7)])
    styles = zipfile.ZipFile(odt).read("styles.xml").decode()
    # 同尺寸兩頁 → 只 1 個 page-layout（去重）
    assert styles.count("<style:page-layout ") == 1


def test_build_writer_odt_empty_pages(tmp_path):
    odg = tmp_path / "e.odg"
    _make_odg(odg, [])  # 零頁
    odt = tmp_path / "e.odt"
    n_pages, _ = de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    assert n_pages == 0
    # 仍是合法非空 Writer body（至少一個空段落）
    content = zipfile.ZipFile(odt).read("content.xml").decode()
    assert "<text:p" in content
    assert zipfile.ZipFile(odt).read("mimetype").decode() == WRITER_MIME


def test_build_writer_odt_keeps_images(tmp_path):
    odg = tmp_path / "i.odg"
    _make_odg(odg, [[(2, 2, 3, 3, "x")]], pics={"Pictures/1.png": b"\x89PNG_fake"})
    odt = tmp_path / "i.odt"
    _, n_imgs = de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    assert n_imgs == 1
    with zipfile.ZipFile(odt) as z:
        assert "Pictures/1.png" in z.namelist()
        assert 'full-path="Pictures/1.png"' in z.read("META-INF/manifest.xml").decode()


def test_build_writer_odt_zipslip_pictures_filtered(tmp_path):
    odg = tmp_path / "z.odg"
    _make_odg(odg, [[(1, 1, 2, 2, "x")]],
              pics={"Pictures/../evil.png": b"bad", "Pictures/ok.png": b"good"})
    odt = tmp_path / "z.odt"
    _, n_imgs = de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    assert n_imgs == 1  # 惡意 ../ 名稱被濾掉
    names = zipfile.ZipFile(odt).namelist()
    assert "Pictures/ok.png" in names
    assert not any(".." in n for n in names)


def test_manifest_xml_injection_escaped(tmp_path):
    """Pictures 檔名含 XML 特殊字元 → manifest.xml 仍是合法 XML（防注入破包）。"""
    from lxml import etree

    odg = tmp_path / "inj.odg"
    evil = 'Pictures/a"&<>x.png'  # 全是合法 zip 名，但含 XML 特殊字元
    _make_odg(odg, [[(1, 1, 2, 2, "x")]], pics={evil: b"data"})
    odt = tmp_path / "inj.odt"
    de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    man = zipfile.ZipFile(odt).read("META-INF/manifest.xml")
    # 必須能被 XML parser 解析（沒被 " & < > 破壞）
    root = etree.fromstring(man, de._safe_parser())
    assert root is not None
    # 圖片仍被保留（名稱合法，只是需跳脫）
    assert evil in zipfile.ZipFile(odt).namelist()


def test_control_char_picture_rejected(tmp_path):
    odg = tmp_path / "ctl.odg"
    _make_odg(odg, [[(1, 1, 2, 2, "x")]],
              pics={"Pictures/bad\x01.png": b"x", "Pictures/good.png": b"y"})
    odt = tmp_path / "ctl.odt"
    _, n = de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    assert n == 1  # 含控制字元的被拒
    assert "Pictures/good.png" in zipfile.ZipFile(odt).namelist()


def test_comment_node_in_page_skipped(tmp_path):
    """draw:page 內含 XML 註解節點不得讓轉換爆掉（etree.QName 對 comment 會丟）。"""
    ns = (
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"'
    )
    content = (
        '<?xml version="1.0"?><office:document-content %s>'
        '<office:body><office:drawing><draw:page draw:name="p">'
        '<!-- 一個註解節點 -->'
        '<draw:frame svg:x="1cm" svg:y="1cm" svg:width="3cm" svg:height="1cm">'
        '<draw:text-box><text:p>hi</text:p></draw:text-box></draw:frame>'
        '</draw:page></office:drawing></office:body></office:document-content>' % ns
    )
    odg = tmp_path / "c.odg"
    with zipfile.ZipFile(odg, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.graphics")
        z.writestr("content.xml", content)
    odt = tmp_path / "c.odt"
    n_pages, _ = de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    assert n_pages == 1  # 沒 crash，正常產出
    assert zipfile.ZipFile(odt).read("mimetype").decode() == WRITER_MIME


def test_rotated_page_size_swapped(tmp_path):
    """90/270 旋轉頁 → 視覺尺寸交換寬高。"""
    import fitz

    pdf = tmp_path / "rot.pdf"
    d = fitz.open()
    p = d.new_page(width=595, height=842)  # A4 直向 media box
    p.insert_text((72, 72), "x", fontsize=12)
    p.set_rotation(90)
    d.save(pdf)
    d.close()
    sizes = de._pdf_page_sizes_cm(pdf)
    w, h = sizes[0]
    # 旋轉 90° → 視覺變橫向（寬 > 高）
    assert w > h


def test_atomic_write_no_partial_on_failure(tmp_path, monkeypatch):
    """打包中途失敗 → out_path 不得留下半截檔。"""
    odg = tmp_path / "a.odg"
    _make_odg(odg, [[(1, 1, 2, 1, "x")]])
    odt = tmp_path / "a.odt"
    odt.write_bytes(b"OLD-GOOD")  # 既有檔
    # 讓 os.replace 之前的 zip 寫入拋錯
    import app.tools.pdf_to_office.engines.draw_engine as mod
    orig = mod.zipfile.ZipFile

    class _Boom:
        def __init__(self, *a, **k):
            raise MemoryError("boom")

    monkeypatch.setattr(mod.zipfile, "ZipFile", _Boom)
    with pytest.raises(MemoryError):
        mod._build_writer_odt(odg, odt, [(21.0, 29.7)])
    monkeypatch.setattr(mod.zipfile, "ZipFile", orig)
    # 沒有 .tmp 殘留
    assert not (odt.with_name(odt.name + ".tmp")).exists()


def test_safe_parser_no_xxe(tmp_path):
    """安全解析器不得展開外部實體（XXE）。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    from lxml import etree

    xxe = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE r [<!ENTITY x SYSTEM "file://%s">]>'
        '<r>&x;</r>' % secret
    ).encode()
    root = etree.fromstring(xxe, de._safe_parser())
    # 外部實體不得被解析成檔案內容
    assert "TOPSECRET" not in (root.text or "")


def test_content_size_guard(tmp_path, monkeypatch):
    """content.xml 超大 → 拒絕（防資源耗盡）。"""
    odg = tmp_path / "big.odg"
    _make_odg(odg, [[(1, 1, 2, 1, "x")]])
    monkeypatch.setattr(de, "_MAX_CONTENT_BYTES", 10)  # 人為調小
    with pytest.raises(RuntimeError, match="龐大|上限"):
        de._build_writer_odt(odg, tmp_path / "o.odt", [(21.0, 29.7)])


def test_parse_len_cm():
    assert de._parse_len_cm("0.908cm")[0] == pytest.approx(0.908)
    assert de._parse_len_cm("10mm")[0] == pytest.approx(1.0)
    assert de._parse_len_cm("1in")[0] == pytest.approx(2.54)
    assert de._parse_len_cm("28.3465pt")[0] == pytest.approx(1.0, abs=1e-3)
    assert de._parse_len_cm("garbage") is None
    assert de._parse_len_cm("") is None


def test_text_frame_width_padded(tmp_path):
    """含文字的 frame svg:width 應被加寬（防裁尾字），純線條/矩形不動。"""
    odg = tmp_path / "p.odg"
    _make_odg(odg, [[(2, 2, 1.0, 0.4, "1,200")]])
    odt = tmp_path / "p.odt"
    de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    content = zipfile.ZipFile(odt).read("content.xml").decode()
    m = re.search(r'svg:width="([0-9.]+)cm"', content)
    assert m is not None
    # 原 1.0cm → 1.0*1.18+0.15 = 1.33cm 左右
    assert float(m.group(1)) > 1.2


def test_text_frame_background_transparent(tmp_path):
    """無填色文字框搬到 Writer 要透明（否則白底遮住底下灰底表頭/綠底）；
    有 solid 填色的形狀（背景色塊）維持填色不被誤透明。"""
    # 自製 odg：一個 fill=none 文字框 + 一個 solid 灰底形狀
    ns = (
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"'
    )
    content = (
        '<?xml version="1.0"?><office:document-content %s>'
        '<office:automatic-styles>'
        '<style:style style:name="gtxt" style:family="graphic">'
        '<style:graphic-properties draw:fill="none"/></style:style>'
        '<style:style style:name="gfill" style:family="graphic">'
        '<style:graphic-properties draw:fill="solid" draw:fill-color="#cccccc"/></style:style>'
        '</office:automatic-styles>'
        '<office:body><office:drawing><draw:page draw:name="p">'
        '<draw:rect draw:style-name="gfill" svg:x="1cm" svg:y="1cm" svg:width="8cm" svg:height="1cm"/>'
        '<draw:frame draw:style-name="gtxt" svg:x="1cm" svg:y="1cm" svg:width="3cm" svg:height="1cm">'
        '<draw:text-box><text:p>label</text:p></draw:text-box></draw:frame>'
        '</draw:page></office:drawing></office:body></office:document-content>' % ns
    )
    odg = tmp_path / "bg.odg"
    with zipfile.ZipFile(odg, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.graphics")
        z.writestr("content.xml", content)
    odt = tmp_path / "bg.odt"
    de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    out = zipfile.ZipFile(odt).read("content.xml").decode()
    gtxt = re.search(r'name="gtxt"[^>]*><style:graphic-properties([^>]*)', out).group(1)
    gfill = re.search(r'name="gfill"[^>]*><style:graphic-properties([^>]*)', out).group(1)
    # 無填色文字框 → 100% 透明背景
    assert 'background-transparency="100%"' in gtxt
    assert 'draw:fill="none"' in gtxt
    # solid 填色形狀 → 維持 solid，不被套透明
    assert 'draw:fill="solid"' in gfill
    assert 'background-transparency' not in gfill


def test_classify_cjk_font_maps_by_style():
    """CJK 字型依明/楷/黑風格對應標準台灣名 + generic；Latin 字型不動。"""
    assert de._classify_cjk_font("MingLiU") == ("新細明體", "roman")
    assert de._classify_cjk_font("PMingLiU") == ("新細明體", "roman")
    assert de._classify_cjk_font("Microsoft JhengHei") == ("微軟正黑體", "swiss")
    assert de._classify_cjk_font("DFKai-SB") == ("標楷體", "script")
    assert de._classify_cjk_font("GPDGIG+MingLiU") == ("新細明體", "roman")  # subset prefix
    assert de._classify_cjk_font("新細明體") == ("新細明體", "roman")
    assert de._classify_cjk_font("標楷體") == ("標楷體", "script")


def test_classify_leaves_latin_fonts_untouched():
    """含 sans/serif 的 Latin 字型不可被誤判成中文字型（英文字會變中文）。"""
    for latin in ("Liberation Sans", "Liberation Serif", "DejaVu Sans",
                  "Noto Sans", "Noto Sans CJK SC", "Arial", "Times New Roman",
                  "Helvetica", "OpenSymbol", "Courier New"):
        assert de._classify_cjk_font(latin) == (None, None), latin


def test_remap_fonts_only_cjk_facefaces(tmp_path):
    """_remap_fonts 只改 CJK font-face（補標準名 + generic），Latin font-face 不動。"""
    from lxml import etree

    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
          'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"')
    xml = (
        '<office:document-content %s><office:font-face-decls>'
        '<style:font-face style:name="MingLiU" svg:font-family="MingLiU"/>'
        '<style:font-face style:name="Liberation Sans" svg:font-family="Liberation Sans" '
        'style:font-family-generic="roman"/>'
        '</office:font-face-decls></office:document-content>' % ns
    ) .encode()
    root = etree.fromstring(xml, de._safe_parser())
    changed = de._remap_fonts(root)
    assert changed == 1
    faces = root.find(de._q("office", "font-face-decls"))
    by_name = {f.get(de._q("style", "name")): f for f in faces}
    # MingLiU → 新細明體 + generic roman
    assert by_name["MingLiU"].get(de._q("svg", "font-family")) == "新細明體"
    assert by_name["MingLiU"].get(de._q("style", "font-family-generic")) == "roman"
    # Liberation Sans 保持不動（沒被改成中文字型）
    assert by_name["Liberation Sans"].get(de._q("svg", "font-family")) == "Liberation Sans"


def test_office_version_normalized_to_1_3(tmp_path):
    """content.xml 的 office:version 必須正規化成 1.3，與自組的 styles.xml /
    manifest 一致 —— 否則新版 LibreOffice(26.2+，產 odg 標 1.4) 會因版本不一致
    整份拒絕載入（source file could not be loaded）。"""
    # 自製一個 office:version="1.4" 的 odg（模擬新版 LibreOffice 產出）
    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
          'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"')
    content = (
        '<?xml version="1.0"?><office:document-content %s office:version="1.4">'
        '<office:body><office:drawing><draw:page draw:name="p">'
        '<draw:frame svg:x="1cm" svg:y="1cm" svg:width="3cm" svg:height="1cm">'
        '<draw:text-box><text:p>x</text:p></draw:text-box></draw:frame>'
        '</draw:page></office:drawing></office:body></office:document-content>' % ns
    )
    odg = tmp_path / "v14.odg"
    with zipfile.ZipFile(odg, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.graphics")
        z.writestr("content.xml", content)
    odt = tmp_path / "v14.odt"
    de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    c = zipfile.ZipFile(odt).read("content.xml").decode()
    st = zipfile.ZipFile(odt).read("styles.xml").decode()
    man = zipfile.ZipFile(odt).read("META-INF/manifest.xml").decode()
    assert 'office:version="1.3"' in c
    assert 'office:version="1.4"' not in c
    # 三者一致
    assert 'office:version="1.3"' in st
    assert 'manifest:version="1.3"' in man


def test_dedup_overprint_removes_dups_keeps_legit(tmp_path):
    """疊印去重：移除重複/被覆蓋框（假粗體），但保留正常重複內容（如 ○○○）。"""
    from lxml import etree

    def frame(x, y, w, h, text):
        return (
            '<draw:frame draw:style-name="g" svg:x="%scm" svg:y="%scm" '
            'svg:width="%scm" svg:height="%scm"><draw:text-box><text:p>%s</text:p>'
            '</draw:text-box></draw:frame>' % (x, y, w, h, text)
        )
    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
          'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"')
    shapes = (
        # 疊印：同位置「彰化」×3 → 應留 1
        frame(2, 1, 1, 0.5, "彰化") + frame(2.01, 1.01, 1, 0.5, "彰化")
        + frame(2, 1, 1, 0.5, "彰化")
        # 「鄉鄉」重疊「鄉（」→「鄉鄉」應被移除（字由鄰框保留）
        + frame(6, 1, 1, 0.5, "鄉（") + frame(6.01, 1.01, 0.5, 0.5, "鄉鄉")
        # 正常：○○○ 單獨無重疊 → 必須保留
        + frame(2, 10, 2, 0.5, "○○○")
    )
    content = ('<?xml version="1.0"?><office:document-content %s>'
               '<office:body><office:drawing><draw:page draw:name="p">%s'
               '</draw:page></office:drawing></office:body>'
               '</office:document-content>' % (ns, shapes))
    odg = tmp_path / "op.odg"
    with zipfile.ZipFile(odg, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.graphics")
        z.writestr("content.xml", content)
    odt = tmp_path / "op.odt"
    de._build_writer_odt(odg, odt, [(21.0, 29.7)])
    c = zipfile.ZipFile(odt).read("content.xml").decode()
    assert c.count("彰化") == 1          # 疊印 3→1
    assert "鄉鄉" not in c               # 純重複框移除
    assert "鄉（" in c                    # 鄰框保留（含 鄉）
    assert "○○○" in c                    # 正常重複內容保留（無重疊鄰框）


def test_missing_content_xml(tmp_path):
    bad = tmp_path / "bad.odg"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("mimetype", "x")
    with pytest.raises(RuntimeError, match="content.xml"):
        de._build_writer_odt(bad, tmp_path / "o.odt", [(21.0, 29.7)])


# --------------------------------------------------------------------------
# 端到端層（需 LibreOffice-draw / OxOffice）
# --------------------------------------------------------------------------
def _draw_capable() -> bool:
    if not find_soffice():
        return False
    try:
        import tempfile

        import fitz

        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "t.pdf"
            d = fitz.open()
            d.new_page().insert_text((72, 72), "probe", fontsize=12)
            d.save(pdf)
            d.close()
            odg = Path(td) / "t.odg"
            from app.core import office_convert

            office_convert.convert_to_odg(pdf, odg, timeout=60)
            return odg.exists()
    except Exception:
        return False


_DRAW_OK = _draw_capable()
_gate = pytest.mark.skipif(not _DRAW_OK, reason="需 LibreOffice-draw / OxOffice")


def _make_pdf(path: Path, pages=1, text="hello 測試"):
    import fitz

    d = fitz.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"{text} {i+1}", fontsize=14,
                                 fontname="china-t")
    d.save(path)
    d.close()


@_gate
def test_e2e_draw_odt_real_writer(tmp_path):
    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf, pages=2)
    out = tmp_path / "out.odt"
    r = de.convert_via_draw(pdf, out, "odt", timeout=90)
    assert r["ok"], r["error"]
    assert r["pages"] == 2
    assert out.exists()
    assert zipfile.ZipFile(out).read("mimetype").decode() == WRITER_MIME


@_gate
def test_e2e_draw_docx(tmp_path):
    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf, pages=1)
    out = tmp_path / "out.docx"
    r = de.convert_via_draw(pdf, out, "docx", timeout=90)
    assert r["ok"], r["error"]
    assert out.exists()
    # docx 是有效 zip（含 word/document.xml）
    assert "word/document.xml" in zipfile.ZipFile(out).namelist()


@_gate
def test_e2e_corrupt_pdf_graceful(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\n not a real pdf \n%%EOF")
    out = tmp_path / "out.odt"
    r = de.convert_via_draw(bad, out, "odt", timeout=60)
    # 不得 crash；ok 任一皆可，但若失敗要有清楚錯誤
    assert isinstance(r["ok"], bool)
    if not r["ok"]:
        assert r["error"]


@_gate
def test_e2e_rendered_page_count_matches(tmp_path):
    """關鍵：3 個同尺寸頁必須 render 成 3 頁（驗證 master-page-name 對重複頁型
    仍強制換頁的假設；否則同尺寸頁會塌成 1 頁 + 文字方塊重疊）。"""
    import fitz

    pdf = tmp_path / "3p.pdf"
    d = fitz.open()
    for i in range(3):
        d.new_page(width=595, height=842).insert_text(
            (72, 72), f"page {i+1}", fontsize=14)
    d.save(pdf)
    d.close()
    odt = tmp_path / "3p.odt"
    r = de.convert_via_draw(pdf, odt, "odt", timeout=90)
    assert r["ok"] and r["pages"] == 3
    # render odt → pdf，實際頁數必須是 3（不是塌成 1）
    from app.core import office_convert

    rendered = tmp_path / "3p_render.pdf"
    office_convert.convert_to_pdf(odt, rendered, timeout=90)
    with fitz.open(rendered) as rd:
        assert rd.page_count == 3, f"期望 3 頁，實際 {rd.page_count}"


@_gate
def test_e2e_mixed_page_sizes_rendered(tmp_path):
    """混合頁尺寸（A4 直 / A4 橫）render 後每頁尺寸正確。"""
    import fitz

    pdf = tmp_path / "mix.pdf"
    d = fitz.open()
    d.new_page(width=595, height=842).insert_text((72, 72), "portrait", fontsize=12)
    d.new_page(width=842, height=595).insert_text((72, 72), "landscape", fontsize=12)
    d.save(pdf)
    d.close()
    odt = tmp_path / "mix.odt"
    r = de.convert_via_draw(pdf, odt, "odt", timeout=90)
    assert r["ok"] and r["pages"] == 2
    from app.core import office_convert

    rendered = tmp_path / "mix_render.pdf"
    office_convert.convert_to_pdf(odt, rendered, timeout=90)
    with fitz.open(rendered) as rd:
        assert rd.page_count == 2
        p1, p2 = rd[0].rect, rd[1].rect
        assert p1.height > p1.width       # 第 1 頁直向
        assert p2.width > p2.height       # 第 2 頁橫向


@_gate
def test_e2e_service_dispatch_draw(tmp_path):
    from app.tools.pdf_to_office.service import convert_pdf_to_office

    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf, pages=1)
    res = convert_pdf_to_office(pdf, tmp_path / "work", "odt", engine="jtdt-layout")
    assert res.ok, res.error
    assert res.engine_used == "jtdt-layout"
    assert res.output_path and res.output_path.exists()
