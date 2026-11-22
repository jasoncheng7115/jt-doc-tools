"""Endpoints for the Markdown → PDF / DOCX / ODT tool.

Pipeline: markdown text → HTML (markdown-it-py) → wrap with theme CSS →
soffice headless → PDF / DOCX / ODT. PDF pages are also rendered to PNG via
PyMuPDF for the preview gallery.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core import atomic_json as _aj
from ...core import job_manager as _jm
from ...core import office_convert as _oc
from ...core import upload_owner as _uo
from ...core.safe_paths import require_uuid_hex
from . import themes

log = logging.getLogger("app.markdown_to_doc")

router = APIRouter()

_MAX_MARKDOWN_BYTES = 5 * 1024 * 1024   # 5 MB raw markdown

#: 可以輸出的格式。**一次全轉很浪費** —— 三次 soffice 被鎖序列化，
#: 每次上限 120 秒，最壞要 6 分鐘，而使用者可能只想要其中一種
#: （使用者 2026-09-18 回報：正式機轉一份 43 KB 的 Markdown 卡超過兩分鐘）。
_FORMATS = ("pdf", "docx", "odt")
_PREVIEW_DPI = 96                        # preview PNG resolution
_PREVIEW_MAX_PAGES = 50                  # cap to avoid huge memory hits


def _work_dir(uid: str) -> Path:
    require_uuid_hex(uid, "upload_id")
    d = settings.temp_dir / f"md2doc_{uid}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem if filename else "document"
    safe = re.sub(r"[^\w一-鿿\-]+", "_", stem)
    safe = safe.strip("_") or "document"
    return safe[:80]


def _esc_attr(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _make_highlighter(theme_id: str):
    """回一個給 markdown-it 用的語法上色函式（沒有語言或主題不上色時回 None）。

    **用內嵌樣式（`noclasses=True`）不用 class** —— 產出的 HTML 要交給
    soffice 轉成 PDF / DOCX / ODT，而**內嵌樣式是唯一確定會被帶過去的**。
    靠 class ＋ `<style>` 的話，換一個 soffice 版本就可能整片變黑白，
    而且**畫面上看起來完全正常**（HTML 預覽是對的，只有轉出來的檔案沒顏色）。

    **上色失敗絕不可以讓轉檔失敗** —— 它是附屬品不是產出
    （同 pdf2docx 逐頁進度那條）。認不得的語言就原樣輸出。
    """
    style = themes.code_style(theme_id)
    if not style:
        return None

    def _hl(code: str, lang: str, _attrs: str) -> str:
        if not lang:
            return ""          # 回空字串 = 交給 markdown-it 用預設處理
        try:
            from pygments import highlight as _pyg_highlight
            from pygments.formatters import HtmlFormatter
            from pygments.lexers import get_lexer_by_name
            from pygments.util import ClassNotFound
            try:
                lexer = get_lexer_by_name(lang, stripall=False)
            except ClassNotFound:
                return ""
            # **`nowrap=True` 是必要的，不是風格選擇。**
            # 預設會包一層 `<div class="highlight"><pre>…</pre></div>`，
            # 而 markdown-it 看到回傳值不是 `<pre` 開頭就**再包一層
            # `<pre><code>`** —— 變成 `<pre>` 巢在 `<pre>` 裡面。
            # 那是不合法的 HTML，soffice 剖析時會把內層的顏色整個丟掉：
            # **HTML 預覽有顏色、轉出來的 PDF 沒有**，而且完全不會報錯。
            fmt = HtmlFormatter(style=style, noclasses=True, nowrap=True)
            return _pyg_highlight(code, lexer, fmt)
        except Exception:                                # noqa: BLE001
            log.warning("語法上色失敗（語言 %s），改用未上色的區塊", lang,
                        exc_info=True)
            return ""

    return _hl


def _render_md_html(md_text: str, theme_id: str, title: str, font_id: str = "default",
                    table_opts: dict | None = None) -> str:
    """Convert markdown text to a full HTML document with theme CSS applied.

    font_id 為 'default' 時用主題內建字型；其他字型 ID 會 append 覆蓋 body CSS。

    `table_opts`（選用，給會議記錄這類**我們自己產生**的表格用）：
    `width`（例如 "100%"）、`cellpadding`、`rules`（"rows" ＝ 只畫橫線）、
    `align_th`（表頭跟欄位同向對齊）、`valign`（內文格的垂直對齊）。**不給就跟原本一模一樣** ——
    「Markdown 轉辦公文件」的表格是使用者自己的內容，不替他改樣子。
    """
    from markdown_it import MarkdownIt
    md = (
        MarkdownIt("commonmark", {"breaks": False, "linkify": True,
                                  "html": False,
                                  "highlight": _make_highlighter(theme_id)})
        .enable("table")
        .enable("strikethrough")
    )
    # **行內 `code` 的顏色改用內嵌樣式** —— 放 CSS 規則的話，soffice 會把
    # 整個 `<code>` 當成一段字元樣式、丟掉裡面的語法上色 span（實測）。
    _ink = themes.inline_code_color(theme_id)

    def _code_inline(self, tokens, idx, options, env):
        import html as _html
        return (f'<code style="color: {_ink}">'
                f'{_html.escape(tokens[idx].content)}</code>')

    md.add_render_rule("code_inline", _code_inline)

    # ---- 表格框線與 cell 內距：**用 HTML 的表現屬性，不用 CSS** ----
    #
    # soffice 的 HTML 匯入基本上是 HTML 4 時代的實作。實測（都在真的 PDF 上量）：
    #
    # | 寫法 | 結果 |
    # |---|---|
    # | `td { border: 1px solid #ccc }` | **完全沒畫** |
    # | `border-width` / `-style` / `-color` 拆開寫 | 有時候畫、有時候不畫（跟順序與同規則裡有沒有 `padding` 有關）|
    # | **`<table border="1" cellpadding="5">`** | **穩定畫得出來**，0.75pt，看得見 |
    #
    # 所以框線與內距一律走屬性。CSS 留著給瀏覽器預覽用，兩者不衝突。
    #
    # **表頭底色、框線顏色、隔行底色也一樣**（`themes.TABLE_STYLES`）：
    # `th { background }` 在 soffice 只塗在文字後面、不填滿整格；
    # `tbody tr:nth-child(even) td` 完全沒套上；框線用預設的灰色太重。
    _ts = themes.table_style(theme_id)

    _to = table_opts or {}

    def _table_open(self, tokens, idx, options, env):
        extra = ""
        if _to.get("width"):
            extra += f' width="{_to["width"]}"'
        if _to.get("rules"):
            # `frame="hsides"`：外框只留上下兩條，跟「只畫橫線」一致
            extra += f' rules="{_to["rules"]}" frame="hsides"'
        return (f'<table border="1" cellpadding="{int(_to.get("cellpadding", 5))}" '
                f'cellspacing="0" bordercolor="{_ts["border"]}"{extra}>')

    def _align_attr(tok):
        # markdown-it 把 `---:` 寫成 `style="text-align:right"`；soffice 對表頭
        # 不理那個 style（表頭一律置中），所以另外補 HTML 的 `align` 屬性。
        st = tok.attrGet("style") or ""
        for a in ("right", "center", "left"):
            if f"text-align:{a}" in st.replace(" ", ""):
                return a
        return "left"

    def _th_open(self, tokens, idx, options, env):
        if _ts.get("head_bg"):
            tokens[idx].attrSet("bgcolor", _ts["head_bg"])
        if _to.get("align_th"):
            tokens[idx].attrSet("align", _align_attr(tokens[idx]))
        return self.renderToken(tokens, idx, options, env)

    def _tbody_open(self, tokens, idx, options, env):
        env["_jt_row"] = 0
        return self.renderToken(tokens, idx, options, env)

    def _td_open(self, tokens, idx, options, env):
        # 隔行上色要設在**每一格**上（`<tr bgcolor>` soffice 不一定認）
        if _ts.get("zebra") and env.get("_jt_row", 0) % 2 == 0:
            tokens[idx].attrSet("bgcolor", _ts["zebra"])
        if _to.get("align_th"):
            # 內文格也補 `align`：瀏覽器的主題預覽在 CSP 底下不吃行內 `style`，
            # 不補的話預覽裡數字欄是靠左的，跟實際檔案不一樣
            tokens[idx].attrSet("align", _align_attr(tokens[idx]))
        if _to.get("valign"):
            # 多行的列（逐字稿）段號與發言者要靠上，才對得到內容的第一行。
            # CSS 的 `vertical-align` soffice 不理，要用 HTML 屬性。
            tokens[idx].attrSet("valign", _to["valign"])
        return self.renderToken(tokens, idx, options, env)

    def _tr_open(self, tokens, idx, options, env):
        if "_jt_row" in env:
            env["_jt_row"] += 1
        return self.renderToken(tokens, idx, options, env)

    def _thead_open(self, tokens, idx, options, env):
        env.pop("_jt_row", None)            # 表頭那一列不算進隔行
        return self.renderToken(tokens, idx, options, env)

    md.add_render_rule("table_open", _table_open)
    md.add_render_rule("th_open", _th_open)
    md.add_render_rule("thead_open", _thead_open)
    md.add_render_rule("tbody_open", _tbody_open)
    md.add_render_rule("tr_open", _tr_open)
    md.add_render_rule("td_open", _td_open)

    body_html = md.render(md_text or "")
    theme = themes.get_theme(theme_id)
    font_css = themes.font_css_override(font_id or "default")
    # Light HTML escape on title (used in <title>)
    safe_title = (title or "Document").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
{theme["css"]}
{font_css}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


def _render_pdf_previews(pdf_path: Path, out_dir: Path) -> list[Path]:
    """Render each PDF page as a PNG into out_dir. Returns list of PNG paths."""
    import fitz
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    with fitz.open(str(pdf_path)) as doc:
        n = min(doc.page_count, _PREVIEW_MAX_PAGES)
        zoom = _PREVIEW_DPI / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i in range(n):
            pix = doc[i].get_pixmap(matrix=mat, alpha=False)
            out = out_dir / f"page_{i + 1:03d}.png"
            pix.save(str(out))
            pages.append(out)
    return pages


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "markdown_to_doc.html", {
        "request": request,
        "themes": themes.theme_options(),
        "fonts": themes.font_options(),
    })


@router.post("/convert")
async def convert(
    request: Request,
    text: str = Form(""),
    file: Optional[UploadFile] = File(None),
    theme: str = Form("classic"),
    font: str = Form("default"),
    title: str = Form(""),
    formats: str = Form(""),
):
    """Convert markdown (from text body or uploaded file) to the requested formats.

    `formats` 是逗號分隔的 `pdf` / `docx` / `odt`。

    **空的時候維持三種全轉** —— 那是既有 API 呼叫者看到的行為，
    悄悄改掉等於改公開契約（本專案在 `/load` 的 `file`→`files` 上踩過）。
    網頁端一律明確送出選擇。

    Returns JSON with upload_id, page count, preview URLs, download URLs."""
    md_text = text or ""
    file_stem = "document"
    if file is not None and file.filename:
        data = await file.read()
        if data:
            try:
                md_text = data.decode("utf-8", errors="replace")
            except Exception:
                raise HTTPException(400, "檔案不是 UTF-8 文字")
            file_stem = _safe_stem(file.filename)
    md_bytes = md_text.encode("utf-8")
    if not md_bytes.strip():
        raise HTTPException(400, "請貼上 Markdown 內容或上傳 .md 檔")
    if len(md_bytes) > _MAX_MARKDOWN_BYTES:
        raise HTTPException(400, f"Markdown 超過上限 {_MAX_MARKDOWN_BYTES // 1024 // 1024} MB")
    if theme not in themes.THEMES:
        theme = "classic"
    stem = _safe_stem(title) if title else file_stem
    if not stem:
        stem = "document"

    want = {f.strip().lower() for f in formats.split(",") if f.strip()}
    bad = want - set(_FORMATS)
    if bad:
        raise HTTPException(400, f"不支援的輸出格式：{'、'.join(sorted(bad))}")
    if not want:
        want = set(_FORMATS)          # 沒指定＝照舊全轉（API 相容）

    uid = uuid.uuid4().hex
    _uo.record(uid, request)
    wdir = _work_dir(uid)

    # 保留原例外物件：缺 Office 引擎要回 503 不是 500，壓成字串就分辨不出來了
    exc_objs: dict = {}
    # **.docx 要先轉 .odt 當中介**（HTML 直轉 docx 的濾鏡鏈常失敗），
    # 所以只勾 docx 時 odt 仍然會被產生 —— 但不會出現在下載清單裡。
    need_odt = ("odt" in want) or ("docx" in want)

    def _job(job):
        """背景作業。**要能被取消**（使用者 2026-09-18 要求）。

        原本是同步請求：三種格式各跑一次轉檔引擎、每次上限 120 秒，
        使用者只能盯著「轉換中…」等，關掉分頁就白做。
        改成背景作業之後：有進度、可以取消、關掉分頁也跑得完。
        """
        steps = len(want) + (1 if need_odt and "odt" not in want else 0) + 1
        done_n = [0]

        def step(msg: str) -> None:
            job.progress = min(0.95, done_n[0] / max(steps, 1))
            job.message = msg
            done_n[0] += 1
        # 1. markdown → HTML with theme
        step("產生版面…")
        html = _render_md_html(md_text, theme, stem, font)
        html_path = wdir / f"{stem}.html"
        html_path.write_text(html, encoding="utf-8")
        # **`.odt` / `.docx` 要另一份 HTML** —— soffice 的 HTML 匯入保留文字
        # 顏色但**丟掉段落底色**，於是「白字＋深色橫幅」的標題在文件格式裡
        # 變成白字白底、整個看不見（2026-09-19 回報）。PDF 沒這問題，
        # 所以只有文件格式疊那一層覆寫，橫幅在 PDF 上維持原樣。
        html_office_path = wdir / f"{stem}.office.html"
        html_office_path.write_text(
            html.replace("</style>", themes.office_safe_css() + "</style>", 1),
            encoding="utf-8")
        # 2. HTML → PDF + DOCX + ODT via soffice
        pdf_path = wdir / f"{stem}.pdf"
        docx_path = wdir / f"{stem}.docx"
        odt_path = wdir / f"{stem}.odt"
        errors: dict[str, str] = {}
        if "pdf" in want:
            step("轉 PDF…")
            try:
                _oc.convert_to_pdf(html_path, pdf_path, timeout=120.0)
            except Exception as e:
                errors["pdf"] = str(e)
                exc_objs["pdf"] = e
                log.exception("md→pdf failed")
        # HTML → ODT 直轉 OK,DOCX 直轉 soffice filter chain 常失敗 →
        # 先 HTML → ODT,再 ODT → DOCX 兩段轉檔保險
        if need_odt:
            step("轉 ODT…" if "odt" in want else "準備中介檔…")
            try:
                _oc.convert_to_odt(html_office_path, odt_path, timeout=120.0)
            except Exception as e:
                errors["odt"] = str(e)
                exc_objs["odt"] = e
                log.exception("md→odt failed")
        if "docx" in want:
          step("轉 DOCX…")
          try:
            if odt_path.exists():
                _oc.convert_to_docx(odt_path, docx_path, timeout=120.0)
            else:
                # fallback:沒 ODT 中介，直接從 HTML 試一次
                _oc.convert_to_docx(html_office_path, docx_path, timeout=120.0)
          except Exception as e:
            errors["docx"] = str(e)
            exc_objs["docx"] = e
            log.exception("md→docx failed")
        # 3. Render PDF preview pages
        # 預覽是從 PDF 算出來的 —— 沒有勾 PDF 就沒有預覽。
        # **畫面上要講出這件事**，不然使用者會以為預覽壞了。
        previews: list[Path] = []
        if pdf_path.exists():
            step("產生預覽…")
            try:
                previews = _render_pdf_previews(pdf_path, wdir / "previews")
            except Exception as e:
                log.exception("preview render failed")
                errors["preview"] = str(e)
        produced = {"pdf": pdf_path.exists(), "docx": docx_path.exists(),
                    "odt": odt_path.exists()}
        if not any(produced[k] for k in want):
            # **缺 Office 引擎是部署問題不是使用者的錯**：訊息要說得出裝什麼。
            from ...core.office_convert import (OfficeUnavailableError,
                                                OfficeSourceError)
            for k in ("pdf", "odt", "docx"):
                e = exc_objs.get(k)
                if isinstance(e, (OfficeUnavailableError, OfficeSourceError)):
                    raise e
            first = next((errors[k] for k in ("pdf", "odt", "docx")
                          if k in errors), "沒有產生任何檔案")
            # **訊息不可以指錯方向。** 共用的逾時訊息寫的是「這份檔案可能已毀損…
            # 請對方提供 PDF 版」—— 那是給「使用者上傳 Office 檔」那條路的。
            # 這裡的中繼檔是**我們自己產的**，那句話對使用者沒有意義，
            # 而他會照著做（同 `jtdt update` 印「已回復先前狀態」那次）。
            if "卡住（超過" in first:
                raise RuntimeError(
                    "轉換逾時（超過 120 秒）。這通常是主機忙碌或文件較大造成的，"
                    "不是你的 Markdown 有問題。"
                    "可以只勾選需要的格式再試一次，或稍後再試。")
            raise RuntimeError(f"轉檔失敗：{first}")

        payload = {
            "ok": True,
            "upload_id": uid,
            "stem": stem,
            "theme": theme,
            "page_count": len(previews),
            "char_count": len(md_text),
            "preview_urls": [
                f"/tools/markdown-to-doc/preview/{uid}/{i + 1}"
                for i in range(len(previews))
            ],
            "downloads": {
                # **只列使用者要的** —— odt 可能只是 docx 的中介產物。
                "pdf":  f"/tools/markdown-to-doc/download/{uid}/pdf"
                        if ("pdf" in want and produced["pdf"]) else None,
                "docx": f"/tools/markdown-to-doc/download/{uid}/docx"
                        if ("docx" in want and produced["docx"]) else None,
                "odt":  f"/tools/markdown-to-doc/download/{uid}/odt"
                        if ("odt" in want and produced["odt"]) else None,
            },
            "formats": sorted(want),
            "errors": errors or None,
        }
        # **結果放檔案不放 `job.meta`** —— meta 有大小上限，超過會把大的欄位
        # 丟掉（多半就是預覽圖清單），而那正是畫面要用的東西。
        _aj.write_json(wdir / "result.json", payload)

        # **一定要設 `result_path`** —— 沒設的話「我的作業」會顯示已完成
        # 卻沒有下載鈕，自動存入工作區與保留期清理也都不認得這份產出。
        for k in ("pdf", "docx", "odt"):
            q = {"pdf": pdf_path, "docx": docx_path, "odt": odt_path}[k]
            if k in want and q.exists():
                job.result_path = q
                # 下載時看到的檔名。不設的話會退回內部檔名，
                # 使用者存下來是一串看不懂的東西。
                job.result_filename = f"{stem}.{k}"
                break
        job.progress = 1.0
        job.message = f"完成 — {len(previews)} 頁預覽，{'、'.join(sorted(want)).upper()}"
        return payload

    job = _jm.job_manager.submit("markdown-to-doc", _job,
                                 meta={"upload_id": uid, "stem": stem,
                                       "formats": sorted(want)},
                                 request=request)
    # 「我的作業」的「開啟」：頁面讀 `?job=`（追進度）與 `uid`（讀完整結果）—— 頁面早就會接，原本這裡沒寫，按鈕不會出現
    job.meta["view_url"] = f"/tools/markdown-to-doc/?job={job.id}&uid={uid}"
    return {"job_id": job.id, "upload_id": uid}


@router.get("/result/{upload_id}")
async def result(request: Request, upload_id: str):
    """轉檔完成後的完整結果（預覽網址、下載連結、統計）。

    放在獨立端點而不是 `job.meta`：meta 有大小上限，
    **超過會把大的欄位丟掉**，而預覽圖清單正好是最大的那一個。
    """
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    f = _work_dir(upload_id) / "result.json"
    if not f.exists():
        raise HTTPException(404, "結果不存在或已過期")
    import json as _json
    return _json.loads(f.read_text(encoding="utf-8"))


@router.get("/preview/{upload_id}/{page}")
async def preview(request: Request, upload_id: str, page: int):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    wdir = _work_dir(upload_id)
    if page < 1 or page > _PREVIEW_MAX_PAGES:
        raise HTTPException(400, "page 超出範圍")
    png = wdir / "previews" / f"page_{page:03d}.png"
    if not png.exists():
        raise HTTPException(404, "預覽圖不存在")
    return FileResponse(str(png), media_type="image/png")


@router.get("/download/{upload_id}/{fmt}")
async def download(request: Request, upload_id: str, fmt: str):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    if fmt not in ("pdf", "docx", "odt"):
        raise HTTPException(400, "format 必須是 pdf / docx / odt")
    wdir = _work_dir(upload_id)
    media = {
        "pdf":  "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "odt":  "application/vnd.oasis.opendocument.text",
    }[fmt]
    candidates = list(wdir.glob(f"*.{fmt}"))
    if not candidates:
        raise HTTPException(404, "輸出檔不存在或已過期")
    out = candidates[0]
    return FileResponse(str(out), media_type=media, filename=out.name)


# ---- public API (single-shot) -----------------------------------------

@router.post("/api/markdown-to-doc", include_in_schema=True)
async def api_markdown_to_doc(
    request: Request,
    text: str = Form(""),
    file: Optional[UploadFile] = File(None),
    theme: str = Form("classic"),
    font: str = Form("default"),
    title: str = Form(""),
    format: str = Form("pdf"),
):
    """Programmatic endpoint. Returns the converted file directly (not JSON)."""
    if format not in ("pdf", "docx", "odt"):
        raise HTTPException(400, "format 必須是 pdf / docx / odt")
    md_text = text or ""
    file_stem = "document"
    if file is not None and file.filename:
        data = await file.read()
        if data:
            md_text = data.decode("utf-8", errors="replace")
            file_stem = _safe_stem(file.filename)
    if not md_text.strip():
        raise HTTPException(400, "請提供 text 或 file")
    if len(md_text.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        raise HTTPException(400, "Markdown 過大")
    if theme not in themes.THEMES:
        theme = "classic"
    stem = _safe_stem(title) if title else file_stem

    uid = uuid.uuid4().hex
    wdir = _work_dir(uid)

    def _do():
        html = _render_md_html(md_text, theme, stem, font)
        html_path = wdir / f"{stem}.html"
        html_path.write_text(html, encoding="utf-8")
        target = wdir / f"{stem}.{format}"
        if format == "pdf":
            _oc.convert_to_pdf(html_path, target, timeout=120.0)
        elif format == "docx":
            _oc.convert_to_docx(html_path, target, timeout=120.0)
        else:
            _oc.convert_to_odt(html_path, target, timeout=120.0)
        return target

    target = await asyncio.to_thread(_do)
    media = {
        "pdf":  "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "odt":  "application/vnd.oasis.opendocument.text",
    }[format]
    return FileResponse(str(target), media_type=media, filename=target.name)
