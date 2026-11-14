"""PDF → 簡報（.odp / .pptx）版面重現引擎。

與 pdf-to-office 的 jtdt-layout 同源：都先讓 soffice（OxOffice 優先）把 PDF 匯入
成 Draw 文件（``.odg``），再由我們自己重組成目標格式。

為什麼簡報這條路比文書檔單純
------------------------------
文書檔（Writer）要對抗的是「流動排版」—— 每頁一個 master page、分頁符、繞排模式、
行高補償，那一長串都是為了叫 Writer 不要重排我們放好的東西。**Impress 天生就是
「頁面 + 絕對定位形狀」，與 Draw 的模型相同**，一頁直接對應一張投影片，不需要任何
分頁對抗。

soffice 沒有 Draw→Impress 的匯出濾鏡（``--convert-to odp`` 對 .odg 會回
"no export filter"），所以仍得自己組；但改動很少：

1. ``<office:drawing>`` → ``<office:presentation>``
2. 每頁補一組 page-layout + master-page，尺寸取自**原 PDF 該頁**（投影片尺寸因此
   與原稿一致，直向 PDF 也照樣還原，不會被硬塞成 16:9）
3. mimetype 與 manifest 的 media-type 換成 presentation

**關鍵：沿用 odg 自己的 styles.xml**。曾試過把形狀灌進另一份現成 .odp 的骨架，
結果雙方都有名為 ``standard`` 的樣式而互撞，文字框全變成預設的紅底 —— 保留原
styles.xml 就沒有這個問題。

環境需求
--------
需要 office 套件的 **Impress 模組**（OxOffice 的 ``oxoffice-impress`` /
LibreOffice 的 ``libreoffice-impress``）。缺這個模組時 soffice 會回一句很誤導的
「source file could not be loaded」—— 連正常的 .odp 都載不進來，看起來像我們產出的
檔案壞掉。`app/core/sys_deps.py` 因此有獨立的 Impress 檢查項。
"""
from __future__ import annotations

import logging
import os
import zipfile
from pathlib import Path

from lxml import etree

from ....core import office_convert
from ...pdf_to_office.engines.draw_engine import (
    _collapse_image_tiles,
    _dedup_overprint,
    _drop_hairline_strokes,
    _emit_raster_page,
    _page_has_large_image,
    _page_has_transformed_image,
    _pdf_page_sizes_cm,
    _q,
    _RASTER_SHAPE_THRESHOLD,
    _remap_fonts,
    _render_pdf_page_png,
    _safe_parser,
    _text_frame_count,
)

log = logging.getLogger(__name__)

_PRESENTATION_MIME = "application/vnd.oasis.opendocument.presentation"
_GRAPHICS_MIME = "application/vnd.oasis.opendocument.graphics"


def _build_impress_odp(odg_path: Path, odp_out: Path,
                       page_sizes: list[tuple[float, float]],
                       pdf_path: Path | None = None) -> tuple[int, int, int]:
    """把 Draw 匯入的 ``.odg`` 重組成合法的 Impress ``.odp``。

    Returns:
        (頁數, 圖片數, 物件數)
    """
    with zipfile.ZipFile(odg_path) as zin:
        names = zin.namelist()
        if "content.xml" not in names or "styles.xml" not in names:
            raise RuntimeError("Draw 匯入結果缺少 content.xml / styles.xml")
        data = {n: zin.read(n) for n in names}

    content = etree.fromstring(data["content.xml"], _safe_parser())
    styles = etree.fromstring(data["styles.xml"], _safe_parser())
    # 三個檔的 office:version 必須一致，否則整份被拒載（文書檔那條路踩過）
    for root in (content, styles):
        root.set(_q("office", "version"), "1.3")

    _remap_fonts(content)          # CJK 字型補標準台灣名 + generic
    # Draw 與 Impress 對「未指定的框線 / 填色」預設不同 → 明確釘住（見該函式說明）
    _pin_root_graphic_defaults(styles)

    body = content.find(_q("office", "body"))
    drawing = body.find(_q("office", "drawing")) if body is not None else None
    if drawing is None:
        raise RuntimeError("Draw 匯入結果沒有 office:drawing")

    autostyles = content.find(_q("office", "automatic-styles"))
    # PDF 匯入常在內容區留下細到看不見的幽靈邊框（實測 0.004cm），Impress 會把它
    # 畫成 1px 可見線，整塊內容被框住 → 關掉（見 _drop_hairline_strokes）。
    _drop_hairline_strokes(autostyles)
    s_auto = styles.find(_q("office", "automatic-styles"))
    if s_auto is None:
        s_auto = etree.SubElement(styles, _q("office", "automatic-styles"))
    masters = styles.find(_q("office", "master-styles"))
    if masters is None:
        masters = etree.SubElement(styles, _q("office", "master-styles"))

    pages = [p for p in drawing if isinstance(p.tag, str)
             and etree.QName(p).localname == "page"]
    if not page_sizes:
        page_sizes = [(25.4, 19.05)] * len(pages)     # 保底 4:3

    pics: dict[str, bytes] = {n: v for n, v in data.items()
                              if n.startswith("Pictures/")}
    n_objs = 0

    presentation = etree.SubElement(body, _q("office", "presentation"))
    for i, page in enumerate(pages, start=1):
        w, h = page_sizes[i - 1] if i - 1 < len(page_sizes) else page_sizes[-1]
        # 每頁一組 page-layout + master-page：投影片尺寸 = 原 PDF 該頁尺寸
        pl = etree.SubElement(s_auto, _q("style", "page-layout"))
        pl.set(_q("style", "name"), "JtSPL%d" % i)
        plp = etree.SubElement(pl, _q("style", "page-layout-properties"))
        plp.set(_q("fo", "page-width"), "%.3fcm" % w)
        plp.set(_q("fo", "page-height"), "%.3fcm" % h)
        for side in ("margin-top", "margin-bottom", "margin-left", "margin-right"):
            plp.set(_q("fo", side), "0cm")
        plp.set(_q("style", "print-orientation"),
                "portrait" if h >= w else "landscape")
        mp = etree.SubElement(masters, _q("style", "master-page"))
        mp.set(_q("style", "name"), "JtSMP%d" % i)
        mp.set(_q("style", "page-layout-name"), "JtSPL%d" % i)

        page.set(_q("draw", "master-page-name"), "JtSMP%d" % i)

        # 設計頁 raster fallback：漸層 / 圖案背景被 Draw 匯入拆成上千個失真色塊
        # （簡報封面特別常見）→ 改嵌原 PDF 整頁圖，文字另外疊在上面保持可選取。
        shape_count = sum(1 for c in page if isinstance(c.tag, str))
        gradient_heavy = (shape_count > _RASTER_SHAPE_THRESHOLD
                          and _text_frame_count(page) < shape_count * 0.10)
        if pdf_path is not None and (
                gradient_heavy
                or _page_has_large_image(page, w, h)
                or _page_has_transformed_image(page)):
            png = _render_pdf_page_png(pdf_path, i - 1, remove_text=True)
            if png is not None:
                _emit_raster_slide(page, autostyles, pics, png, i, w, h)
                n_objs += sum(1 for c in page if isinstance(c.tag, str))
                presentation.append(page)
                continue

        _dedup_overprint(page)      # 疊印假粗體造成的重複文字框
        if pdf_path is not None:
            # 局部破圖：漸層 / 半透明圖被拆成一格格小圖塊 → 換回原 PDF 該區域畫面
            _collapse_image_tiles(page, pdf_path, i - 1, w, h, autostyles, pics)
        for shape in page:
            if isinstance(shape.tag, str):
                n_objs += 1
        presentation.append(page)

    body.remove(drawing)

    data["content.xml"] = etree.tostring(content, xml_declaration=True,
                                         encoding="UTF-8")
    data["styles.xml"] = etree.tostring(styles, xml_declaration=True,
                                        encoding="UTF-8")
    data["mimetype"] = _PRESENTATION_MIME.encode()
    if "META-INF/manifest.xml" in data:
        data["META-INF/manifest.xml"] = (
            data["META-INF/manifest.xml"].decode("utf-8", "replace")
            .replace(_GRAPHICS_MIME, _PRESENTATION_MIME).encode())
    # meta / settings / manifest 是 Draw 匯入原樣帶過來的：新版 LibreOffice（26.2 起）寫的是 1.4，
    # 上面把 content / styles 壓成 1.3 → 版本不一致，整份被拒載（CI 換成 Ubuntu 26.04 才抓到）
    _force_odf_version(data, "1.3")
    for name, blob in pics.items():
        data[name] = blob
    # ODF 規定 zip 內每個檔案都要列在 manifest；raster fallback 新加的整頁圖若沒補
    # 進去，整份文件會被拒載（錯誤訊息只有一句「source file could not be loaded」，
    # 看起來像檔案壞掉，很難追）。
    _sync_manifest(data)

    _atomic_write_odf(odp_out, data)
    return len(pages), len(pics), n_objs



def _pin_root_graphic_defaults(styles) -> int:
    """把「沒有父樣式的根 graphic 樣式」缺少的 stroke / fill 明確釘成 none。

    為什麼要這樣做：ODF 的 `standard` 等根樣式常常**不指定** ``draw:stroke`` /
    ``draw:fill``，交給應用程式的內建預設。**Draw 的預設是沒有框線也沒有填色，
    Impress 的預設卻是藍底加框線** —— 同一份內容換個模組開就多出方框（實測：把
    一個沒指定框線的文字方塊放進 Impress，直接被塗成藍底黑框；使用者回報的簡報
    多出灰色方框也是這個原因）。

    只補「完全沒指定」的屬性，已明確寫了 stroke/fill 的樣式一律不動 —— 那些是
    真正要畫的框線與填色（表格框線、色塊都靠它）。
    """
    os_ = styles.find(_q("office", "styles"))
    if os_ is None:
        return 0
    n = 0
    for st in os_.findall(_q("style", "style")):
        if st.get(_q("style", "family")) != "graphic":
            continue
        if st.get(_q("style", "parent-style-name")):
            continue                     # 有父樣式 → 沿著鏈子繼承，不必補
        gp = st.find(_q("style", "graphic-properties"))
        if gp is None:
            gp = etree.SubElement(st, _q("style", "graphic-properties"))
        for attr in ("stroke", "fill"):
            if gp.get(_q("draw", attr)) is None:
                gp.set(_q("draw", attr), "none")
                n += 1
    return n


def _force_odf_version(data: dict, version: str) -> None:
    """把 meta.xml / settings.xml 的 `office:version` 與 manifest 的 `manifest:version` 統一成 `version`。

    ODF 規定同一份文件裡這幾處版本要一致，不一致時 LibreOffice 整份拒載，錯誤只有一句
    「source file could not be loaded」。content / styles 由呼叫端處理。
    """
    office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    mns = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
    for name in ("meta.xml", "settings.xml"):
        if name not in data:
            continue
        try:
            root = etree.fromstring(data[name], _safe_parser())
        except etree.XMLSyntaxError:
            continue
        root.set("{%s}version" % office, version)
        data[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    key = "META-INF/manifest.xml"
    if key in data:
        try:
            root = etree.fromstring(data[key], _safe_parser())
        except etree.XMLSyntaxError:
            return
        attr = "{%s}version" % mns
        root.set(attr, version)
        for e in root:
            if isinstance(e.tag, str) and e.get(attr) is not None:
                e.set(attr, version)
        data[key] = etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _sync_manifest(data: dict) -> None:
    """把 zip 內所有檔案補進 manifest（缺項會讓 Impress 整份拒載）。"""
    key = "META-INF/manifest.xml"
    if key not in data:
        return
    try:
        root = etree.fromstring(data[key], _safe_parser())
    except etree.XMLSyntaxError:
        return
    mns = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
    listed = {e.get("{%s}full-path" % mns) for e in root}
    for name in data:
        if name in ("mimetype", key) or name.endswith("/") or name in listed:
            continue
        low = name.lower()
        if low.endswith(".png"):
            mt = "image/png"
        elif low.endswith((".jpg", ".jpeg")):
            mt = "image/jpeg"
        elif low.endswith(".xml"):
            mt = "text/xml"
        else:
            continue                      # 型別不明就不亂列
        e = etree.SubElement(root, "{%s}file-entry" % mns)
        e.set("{%s}full-path" % mns, name)
        e.set("{%s}media-type" % mns, mt)
    data[key] = etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _emit_raster_slide(page, autostyles, pics: dict, png: bytes,
                       i: int, w: float, h: float) -> None:
    """把整頁 PNG 當背景鋪滿該張投影片，原有的文字框留在上層（可選取可編輯）。"""
    name = "Pictures/jtslide%d.png" % i
    pics[name] = png
    if autostyles is not None:
        gp = etree.SubElement(autostyles, _q("style", "style"))
        gp.set(_q("style", "name"), "JtSGr%d" % i)
        gp.set(_q("style", "family"), "graphic")
        props = etree.SubElement(gp, _q("style", "graphic-properties"))
        props.set(_q("draw", "stroke"), "none")
        props.set(_q("draw", "fill"), "none")
    frame = etree.Element(_q("draw", "frame"))
    frame.set(_q("draw", "style-name"), "JtSGr%d" % i)
    frame.set(_q("svg", "x"), "0cm")
    frame.set(_q("svg", "y"), "0cm")
    frame.set(_q("svg", "width"), "%.3fcm" % w)
    frame.set(_q("svg", "height"), "%.3fcm" % h)
    frame.set(_q("draw", "z-index"), "0")       # 最底層
    img = etree.SubElement(frame, _q("draw", "image"))
    img.set("{http://www.w3.org/1999/xlink}href", name)
    img.set("{http://www.w3.org/1999/xlink}type", "simple")
    img.set("{http://www.w3.org/1999/xlink}show", "embed")
    img.set("{http://www.w3.org/1999/xlink}actuate", "onLoad")
    page.insert(0, frame)
    # 非文字形狀已含在背景圖裡 → 移除，避免失真色塊蓋住背景
    for shape in list(page):
        if shape is frame or not isinstance(shape.tag, str):
            continue
        tb = next(shape.iter(_q("draw", "text-box")), None)
        if tb is None or not "".join(tb.itertext()).strip():
            page.remove(shape)


def _atomic_write_odf(out_path: Path, data: dict) -> None:
    """原子寫出 ODF zip；mimetype 必須是第一個項目且不壓縮（否則部分程式認不得）。"""
    import shutil
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=out_path.suffix, dir=str(out_path.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("mimetype", data["mimetype"], zipfile.ZIP_STORED)
            for name, blob in data.items():
                if name == "mimetype" or name.endswith("/"):
                    continue
                zf.writestr(name, blob)
        shutil.move(tmp, out_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def convert_pdf_to_slides(pdf_path: Path, out_path: Path,
                          output_format: str = "odp",
                          timeout: float = 180.0,
                          progress_cb=None) -> dict:
    """PDF → 版面重現的 .odp / .pptx。

    Returns:
        dict: {"ok", "pages", "images", "objects", "engine", "error"}
    """
    pdf_path, out_path = Path(pdf_path), Path(out_path)
    work = out_path.parent
    work.mkdir(parents=True, exist_ok=True)
    tag = "odp" if output_format == "odp" else "pptx"
    odg = work / (pdf_path.stem + ".slides.%s.odg" % tag)
    odp = out_path if output_format == "odp" else work / (
        pdf_path.stem + ".slides.%s.odp" % tag)

    def _tick(msg: str, frac: float) -> None:
        if progress_cb:
            try:
                progress_cb(msg, frac)
            except Exception:  # noqa: BLE001 — 進度回報不可影響轉檔
                pass

    try:
        _tick("匯入 PDF 版面…", 0.15)
        try:
            office_convert.convert_to_odg(pdf_path, odg, timeout=timeout)
        except RuntimeError as e:
            msg = str(e)
            if "找不到輸出" in msg:
                msg = ("無法匯入此 PDF（可能已加密需密碼、不是有效 PDF，或含 "
                       "OxOffice / LibreOffice 無法解析的內容）。請先解除密碼再試。")
            return {"ok": False, "pages": 0, "images": 0, "objects": 0,
                    "engine": "jtdt-layout", "error": msg}

        page_sizes = _pdf_page_sizes_cm(pdf_path)
        _tick("重組投影片（共 %d 張）…" % len(page_sizes), 0.5)
        n_pages, n_imgs, n_objs = _build_impress_odp(odg, odp, page_sizes,
                                                     pdf_path=pdf_path)

        if output_format != "odp":
            _tick("產生 PowerPoint 檔（%d 張 / %d 個物件）…" % (n_pages, n_objs), 0.75)
            office_convert.convert_to_pptx(odp, out_path, timeout=timeout)
            if not out_path.exists():
                return {"ok": False, "pages": n_pages, "images": n_imgs,
                        "objects": n_objs, "engine": "jtdt-layout",
                        "error": "Impress .odp → .pptx 失敗"}
        return {"ok": True, "pages": n_pages, "images": n_imgs,
                "objects": n_objs, "engine": "jtdt-layout", "error": ""}
    except Exception as e:  # noqa: BLE001 — 統一回報，不讓引擎例外炸掉 job
        log.warning("slides engine 失敗 %s: %s", pdf_path.name, e)
        return {"ok": False, "pages": 0, "images": 0, "objects": 0,
                "engine": "jtdt-layout", "error": str(e)}
    finally:
        for tmp in (odg, odp):
            try:
                if tmp.exists() and tmp != out_path:
                    tmp.unlink()
            except OSError:
                pass
