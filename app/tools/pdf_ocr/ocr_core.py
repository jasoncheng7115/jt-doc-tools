"""PDF OCR 核心 — 對 PDF 每頁跑 tesseract，回傳 word-level bbox+text，
然後用 PyMuPDF 寫透明文字層回原 PDF。

PDF 「透明文字層」原理：
- PyMuPDF page.insert_text 預設 render_mode=0（fill 可見）
- render_mode=3 = invisible — 文字被「畫」在頁面但 fill / stroke 都關
  → 視覺看不到，但 PDF reader 仍能命中（cmd+F 搜尋、滑鼠選取、文字抽取）
- 同 macOS Preview Live Text、Adobe 「Make Searchable PDF」做的事

OCR 信心 < 30 的 word 跳過（太可能是雜訊）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import fitz

log = logging.getLogger(__name__)

DEFAULT_LANGS = "chi_tra+chi_sim+eng"
DEFAULT_DPI = 300
MIN_CONF = 0   # tesseract conf 0-100；低 conf 仍保留比丟掉好（深色背景 / 模糊字
                 # 常 conf 10-25，丟了 user 就選不到。低 conf 文字搜尋稍差但 bbox 仍對）


def _tesseract_image_to_data(img_bytes: bytes, langs: str, preprocess: bool = True):
    """跑 tesseract 對單張圖回 word-level data。
    回 list of dicts: [{text, conf, left, top, width, height}, ...]
    用 image_to_data 拿到 bbox（image_to_string 沒 bbox）。

    preprocess=True 時對影像做 grayscale + autocontrast + UnsharpMask + Otsu
    二值化（顯著提升掃描頁辨識率，失敗自動 fallback 原圖）。
    """
    try:
        from app.core.sys_deps import configure_pytesseract
        configure_pytesseract()
    except Exception:
        pass
    if preprocess:
        img_bytes = _preprocess_image_for_ocr(img_bytes)
    try:
        import pytesseract
        from pytesseract import Output
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        data = pytesseract.image_to_data(img, lang=langs, output_type=Output.DICT)
    except Exception as e:
        log.warning("tesseract image_to_data failed: %s", e)
        return []

    n = len(data.get("text", []))
    out = []
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = 0
        if conf < MIN_CONF:
            continue
        out.append({
            "text": text,
            "conf": conf,
            "left": int(data["left"][i]),
            "top": int(data["top"][i]),
            "width": int(data["width"][i]),
            "height": int(data["height"][i]),
        })
    return out


def add_text_layer_to_page(page: "fitz.Page", words: list[dict],
                             dpi: int = DEFAULT_DPI) -> int:
    """把 OCR 出來的 words list 以透明文字寫進 page。

    word bbox 是「影像座標」(px @ dpi)，轉 PDF pt: pt = px * 72 / dpi。

    兩種 word 形態自動偵測：
    - **單字元** (tesseract per-CJK-char 或 short word)：用 insert_text() 點
      座標 + height-based font_size — 精準對齊單字 bbox
    - **多字元寬 bbox** (EasyOCR per-LINE)：用 insert_textbox() 把文字塞入
      bbox 內 — 字級自動 shrink to fit，避免 line text 渲染超出 bbox 右側
      導致 cmd-drag 選不到尾段字（v1.7.2 EasyOCR 整合 bug fix）

    回 inserted word count。
    """
    if not words:
        return 0
    px_to_pt = 72.0 / dpi
    n = 0
    for w in words:
        text = w["text"]
        if not text:
            continue
        x_pt = w["left"] * px_to_pt
        y_top_pt = w["top"] * px_to_pt
        w_pt = w["width"] * px_to_pt
        h_pt = w["height"] * px_to_pt

        # 偵測 line-level（多字 + 寬 bbox）vs char-level
        is_line = len(text) >= 3 and w_pt > h_pt * 2.0

        if is_line:
            # Line-level: 把每個字平均分散到 bbox 寬度，**每字一個 insert_text**。
            # CJK 一字 = 一個 em-square；bbox_width / n_chars = char_pitch；
            # font_size = char_pitch 讓字滿格沒間隙 → PDF reader 對連續字
            # union 出來的 highlight rect = bbox 寬度，跟 visible text 對齊。
            #
            # 解決原本 insert_textbox shrink-to-fit 導致 highlight 比 visible
            # 短的 bug — macOS Preview 那種「拖到哪選到哪」的精準度。
            #
            # 左右 padding 各 2pt — EasyOCR bbox 邊界常切掉句號 / 括號，
            # 拖選有點容忍度。
            pad = 2.0
            n_chars = len(text)
            if n_chars == 0:
                continue
            avail_w = w_pt + 2 * pad
            char_pitch = avail_w / n_chars
            # 字級 = char_pitch (讓字滿格)；上限 1.4x 行高避免極端 bbox
            font_size = max(4.0, min(char_pitch, h_pt * 1.4))
            baseline_y = y_top_pt + h_pt * 0.85
            x_start = max(0.0, x_pt - pad)
            placed_any = False
            for i, ch in enumerate(text):
                x_at = x_start + i * char_pitch
                try:
                    page.insert_text(
                        fitz.Point(x_at, baseline_y), ch,
                        fontname="china-t", fontsize=font_size,
                        color=(0, 0, 0), render_mode=3,
                    )
                    placed_any = True
                except Exception:
                    try:
                        page.insert_text(
                            fitz.Point(x_at, baseline_y), ch,
                            fontname="helv", fontsize=font_size,
                            color=(0, 0, 0), render_mode=3,
                        )
                        placed_any = True
                    except Exception:
                        continue
            if placed_any:
                n += 1
        else:
            # Char-level: 用 insert_text 點對齊（tesseract 慣例）
            baseline_y = y_top_pt + h_pt * 0.85
            font_size = max(4.0, h_pt * 0.9)
            try:
                page.insert_text(
                    fitz.Point(x_pt, baseline_y), text,
                    fontname="china-t", fontsize=font_size,
                    color=(0, 0, 0), render_mode=3,
                )
                n += 1
            except Exception:
                try:
                    page.insert_text(
                        fitz.Point(x_pt, baseline_y), text,
                        fontname="helv", fontsize=font_size,
                        color=(0, 0, 0), render_mode=3,
                    )
                    n += 1
                except Exception:
                    continue
    return n


def _group_words_into_lines(words: list[dict]) -> list[list[dict]]:
    """把 tesseract word list 依 top 座標 cluster 成「行」。
    同行容差 = 平均字高 * 0.6。"""
    if not words:
        return []
    sorted_w = sorted(words, key=lambda w: (w["top"], w["left"]))
    avg_h = sum(w["height"] for w in sorted_w) / len(sorted_w)
    threshold = max(avg_h * 0.6, 4)
    lines: list[list[dict]] = []
    cur: list[dict] = []
    cur_top = None
    for w in sorted_w:
        if cur_top is None or abs(w["top"] - cur_top) <= threshold:
            cur.append(w)
            if cur_top is None:
                cur_top = w["top"]
        else:
            if cur:
                lines.append(cur)
            cur = [w]
            cur_top = w["top"]
    if cur:
        lines.append(cur)
    return lines


def add_llm_search_layer_offpage(page: "fitz.Page", llm_text: str) -> int:
    """把 LLM 校正後文字寫到 page rect **外**（visible 區域之下），讓
    visible 區域的拖選 / cmd-drag 不會碰到（避免 Layer 1 / Layer 2 interleave
    成 garbage），但 Cmd+F 仍能透過 PDF 文字串流找到。

    位置：x=0, y = page_height + 10pt 起算，往下展開最多 10cm 高度。
    PDF reader 視 page rect 之外為「可索引但不可見」內容。回 1 / 0 表是否插入。
    """
    if not llm_text or not llm_text.strip():
        return 0
    try:
        rect = fitz.Rect(0, page.rect.height + 10,
                         page.rect.width, page.rect.height + 300)
        for size_factor in (4, 3, 2, 1):
            ret = page.insert_textbox(
                rect, llm_text,
                fontname="china-t",
                fontsize=size_factor,
                color=(0, 0, 0),
                render_mode=3,
                align=0,
            )
            if ret >= 0:
                return 1
        # 都塞不下 — 把 rect 拉得再大一點再試
        bigger = fitz.Rect(0, page.rect.height + 10,
                            page.rect.width, page.rect.height + 1500)
        page.insert_textbox(bigger, llm_text, fontname="china-t",
                             fontsize=2, color=(0, 0, 0), render_mode=3, align=0)
        return 1
    except Exception as e:
        log.warning("add_llm_search_layer_offpage failed: %s", e)
        return 0


def page_has_text_layer(page: "fitz.Page") -> bool:
    """檢查頁面是否已有實質文字層（避免重複 OCR）。"""
    try:
        txt = page.get_text() or ""
        return len(txt.strip()) > 30
    except Exception:
        return False


PRODUCER_TAG = "jt-doc-tools pdf-ocr"
MARKER_KEYWORD = "jtdt-pdf-ocr"  # 唯一 marker 字串，可用 cmd+F 搜到
LLM_VISION_MAX_LONG_SIDE = 1568  # vision 模型多數內部會縮到 ~1024-1568px；
                                   # 我們先在 client 端縮，避免送 8MP 大圖白做工 + 推理變慢


def _align_llm_per_line(llm_text: str, words: list[dict]) -> Optional[list[dict]]:
    """**每行對齊** LLM 校正回 tesseract bbox。

    流程：
    1. tesseract words → group by Y → tess_lines（保留每行的 word bbox）
    2. LLM 文字依 \\n 切 → llm_lines
    3. **以 index 配對行**（tess_line[i] ↔ llm_line[i]）
       — 行數差異 > 30% 視為不可對齊（LLM 加太多結構行）→ 回 None 不套用
    4. 行內：按 **CJK char count** 比例分散 LLM chars 到該行的 word bboxes
       — 對 CJK 文件特別有效（tesseract 1 char per word，LLM 字數通常接近）
       — 行內 char count 差異 > 30% 該行保留 tesseract 原文不套用
    5. 即使 LLM 修了字，position 永遠在「同一視覺行」上，不會跨段錯位

    回 list[word dict] 或 None（無法對齊就不套用）。
    """
    if not llm_text or not words:
        return None
    tess_lines = _group_words_into_lines(words)
    llm_lines = [ln.strip() for ln in llm_text.split("\n") if ln.strip()]
    n_t = len(tess_lines)
    n_l = len(llm_lines)
    if n_t == 0:
        return None
    # 行數差異容差：±30% 或 ±2 行（取大）
    line_diff = abs(n_t - n_l)
    line_tol = max(2, int(n_t * 0.3))
    if line_diff > line_tol:
        return None  # LLM 重組行結構過多，無法可靠對齊

    # 配對到 min(n_t, n_l) 為止
    n_paired = min(n_t, n_l)
    out_words: list[dict] = []
    n_lines_aligned = 0
    n_lines_kept = 0

    for line_idx in range(n_t):
        tline = tess_lines[line_idx]
        if line_idx >= n_paired:
            # tesseract 多出的行 LLM 沒對應 → 保留原文
            out_words.extend(tline)
            n_lines_kept += 1
            continue

        llm_line = llm_lines[line_idx]
        llm_chars = [c for c in llm_line if not c.isspace()]
        n_llm_c = len(llm_chars)
        tess_total_c = sum(len(w["text"]) for w in tline)

        if tess_total_c == 0 or n_llm_c == 0:
            out_words.extend(tline)
            n_lines_kept += 1
            continue
        # 行內 char 數差異 > 30% 該行保留原文（不冒險）
        char_diff = abs(n_llm_c - tess_total_c)
        char_tol = max(2, int(tess_total_c * 0.3))
        if char_diff > char_tol:
            out_words.extend(tline)
            n_lines_kept += 1
            continue

        # 按 word 內字數 + 比例分散 LLM chars 給每個 word slot
        ratio = n_llm_c / tess_total_c
        char_idx = 0
        for tw in tline:
            n_chars_for_word = max(1, int(round(len(tw["text"]) * ratio)))
            chunk = "".join(llm_chars[char_idx:char_idx + n_chars_for_word])
            new_w = dict(tw)
            new_w["text"] = chunk if chunk else tw["text"]
            out_words.append(new_w)
            char_idx += n_chars_for_word
        n_lines_aligned += 1

    # 全頁有效對齊 < 30% 認為失敗（LLM 太多行 mismatch）
    if n_lines_aligned < n_t * 0.3:
        return None
    log.info("LLM line-aligned: %d/%d lines applied (%d kept original)",
             n_lines_aligned, n_t, n_lines_kept)
    return out_words


def _fit_llm_words(cleaned_words: list[str], orig_words: list[dict],
                    raw_cleaned: str = "") -> tuple[bool, list[str], str]:
    """把 LLM 校正後 word list 對應回 tesseract 的 N 個 bbox slot。

    **Strict 1:1 only** — 字數不等就拒絕套用，保留原 tesseract 文字。

    試過比例 word 對應 → 中段內容錯位（拖選漏字）；
    試過比例 char 對應 → bbox 對到 LLM 重排版後完全不同位置的內容（拖選一行複製到另一段）；
    試過 dual layer → 拖選 garbage interleave。

    結論：LLM 重排版時無法可靠 mapping 回 tesseract bbox。妥協做法：
      • LLM 純 typo 校正（字數一致）→ 套用，bbox + content 都精準
      • LLM 重排版 / 加結構 → 不寫進 PDF text layer，避免 user 拖選看到錯內容
      • LLM 校正內容**仍在 stage 詳情顯示**讓 user 比對 / 手動採用

    這保證 PDF 拖選 / Cmd+F 結果都對應到 visible text 位置。
    """
    n_orig = len(orig_words)
    n_clean = len(cleaned_words)
    if n_orig == 0:
        return False, [], "OCR 沒有 bbox slot"
    if n_clean == n_orig:
        return True, cleaned_words, ""
    return False, [], ""


def _preprocess_image_for_ocr(png_bytes: bytes) -> bytes:
    """OCR 前影像預處理 — 提升 tesseract 對掃描頁的識別率。

    流程（已實測最穩定組合）：
    1. 灰階化（彩色背景的字 grayscale 後對比更穩）
    2. autocontrast（線性拉伸 1% 端點，提亮淡色文字）

    曾測試 UnsharpMask（半徑 1.2 percent 180）— **顯著傷害 OCR 結果**
    （tesseract 對銳化過頭的字邊緣解析失敗，實測 742→0 字）→ 移除。
    曾測試 Otsu 二值化（需 numpy）— venv 通常沒 numpy → 同樣移除。
    這兩個 step 經實測弊大於利，乾淨流程更可靠。

    All-PIL 實作，不引新 dep。失敗 graceful 回原 PNG bytes。
    """
    try:
        from PIL import Image, ImageOps
        import io
    except Exception:
        return png_bytes
    try:
        img = Image.open(io.BytesIO(png_bytes))
        # 1. 灰階
        img = img.convert("L")
        # 2. autocontrast — cutoff=1 拉伸 [1%,99%] 像素到 [0,255]
        img = ImageOps.autocontrast(img, cutoff=1)
        # 輸出
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception as e:
        log.warning("preprocess image for OCR failed: %s — using original", e)
        return png_bytes


def _shrink_png_for_vision(png_bytes: bytes, max_long_side: int = LLM_VISION_MAX_LONG_SIDE) -> bytes:
    """把 PNG 縮到長邊不超過 max_long_side，再重新 PNG 編碼回傳。
    用來餵 vision LLM — OCR 用的高解析原圖不動。
    若已小於上限、PIL re-encode 反而變大、或縮放失敗，都回傳原 bytes。"""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(png_bytes))
        w, h = img.size
        long_side = max(w, h)
        if long_side <= max_long_side:
            return png_bytes
        scale = max_long_side / long_side
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        img = img.resize((new_w, new_h), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        new_bytes = out.getvalue()
        # 防呆：PIL 重編碼有時對小圖反而變大，原檔比較好就用原檔
        if len(new_bytes) >= len(png_bytes):
            return png_bytes
        return new_bytes
    except Exception as e:
        log.warning("shrink PNG for vision failed: %s", e)
        return png_bytes


def _tesseract_version() -> str:
    try:
        from app.core.sys_deps import configure_pytesseract
        configure_pytesseract()
        import pytesseract
        v = pytesseract.get_tesseract_version()
        return str(v).split()[0] if v else "unknown"
    except Exception:
        return "unknown"


def ocr_pdf_to_searchable(
    src_pdf: Path, dst_pdf: Path, *,
    langs: str = DEFAULT_LANGS,
    dpi: int = DEFAULT_DPI,
    skip_pages_with_text: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    llm_postprocess: Optional[Callable[[str], str]] = None,
    llm_model_name: str = "",
    llm_vision_postprocess: Optional[Callable[[bytes, str], str]] = None,
    llm_vision_model_name: str = "",
    llm_vision_image_max: int = LLM_VISION_MAX_LONG_SIDE,  # 由 caller 依 profile 傳
    app_version: str = "",
) -> dict:
    """主 entry — 開 src_pdf，逐頁 OCR + 加文字層，存到 dst_pdf。

    LLM 後處理順序（如果有設）：先 vision（看圖修正），再 text（純文字校字）。
    兩個都會嚴格保持 word 數量一致；不一致則退回前一階段的結果。

    回 {pages_total, pages_ocrd, pages_skipped, words_inserted,
        llm_used, llm_vision_used, producer, tesseract_version, marker}。
    """
    doc = fitz.open(str(src_pdf))
    pages_total = doc.page_count
    pages_ocrd = 0
    pages_skipped = 0
    words_inserted = 0
    llm_used = False
    llm_vision_used = False
    # 每頁各階段拿到的文字 — 給前端顯示「展開看每段成效」用
    stage_results: list[dict] = []

    def _emit(cur_page: int, stage: str):
        """每階段都發進度訊息。cur_page 用 0-based pno+1。
        多頁才加「頁 N / M」前綴；單頁 PDF 加了徒增雜訊。"""
        if not progress_cb:
            return
        prefix = f"頁 {cur_page} / {pages_total} · " if pages_total > 1 else ""
        progress_cb(cur_page, pages_total, f"{prefix}{stage}")

    try:
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for pno in range(pages_total):
            page = doc[pno]
            cp = pno + 1
            _emit(cp, "檢查頁面…")
            if skip_pages_with_text and page_has_text_layer(page):
                pages_skipped += 1
                _emit(cp, "已有文字層，略過")
                continue
            _emit(cp, f"渲染影像 ({dpi} DPI)…")
            try:
                pix = page.get_pixmap(matrix=mat)
                png = pix.tobytes("png")
            except Exception as e:
                log.warning("render page %d failed: %s", pno, e)
                _emit(cp, "渲染失敗，略過")
                continue
            # 用抽象 OCR engine：依 admin 設定（預設 easyocr），失敗自動 fallback tesseract
            from app.core import ocr_engine as _oe
            chosen_engine = _oe.get_default_engine()
            _emit(cp, f"OCR 辨識中（{chosen_engine} {langs}）…")
            words, engine_used = _oe.recognize_image(png, langs, preprocess=True)
            if engine_used != chosen_engine and words:
                _emit(cp, f"OCR 完成（{chosen_engine} 失敗，自動切 {engine_used}）")
            if not words:
                _emit(cp, "未辨識到文字，略過")
                stage_results.append({
                    "page": cp,
                    "ocr_raw": {"text": "", "word_count": 0, "note": "tesseract 未辨識到任何文字"},
                })
                continue
            # 記錄 tesseract 原始文字（給前端 collapsible 顯示）
            ocr_raw_text = " ".join(w["text"] for w in words)
            page_stages: dict = {
                "page": cp,
                "ocr_raw": {"text": ocr_raw_text, "word_count": len(words)},
            }
            # === LLM 視覺校對（先做）===
            # 看 PNG 影像對照 OCR 結果，能修純文字看不出來的字元錯誤。
            # 影像先縮到長邊 1568px，避免送 8MP 大圖白做工。
            if llm_vision_postprocess:
                vmodel_tag = f" {llm_vision_model_name}" if llm_vision_model_name else ""
                # 縮圖到該 model profile 偏好的長邊（minicpm-v=448 / llava=672 /
                # internvl=1024 / 其他預設 1568px）
                small_png = _shrink_png_for_vision(png, max_long_side=llm_vision_image_max)
                shrink_note = (f"影像 {len(png)//1024}KB → {len(small_png)//1024}KB"
                               if len(small_png) < len(png)
                               else f"影像 {len(png)//1024}KB")
                _emit(cp, f"LLM 視覺校對中{vmodel_tag}，{shrink_note} + {len(words)} 字…")
                vstage: dict = {"used": False, "text": "", "note": ""}
                try:
                    cleaned = llm_vision_postprocess(small_png, ocr_raw_text)
                    vstage["text"] = cleaned or ""
                    if cleaned and cleaned.strip():
                        # ① 優先：行對齊（保 Y 位置 + 限制 X 漂移在同一行內）
                        aligned = _align_llm_per_line(cleaned, words)
                        if aligned is not None:
                            for new_w in aligned:
                                # 找對應的原 word 物件 update text（保 bbox 不動）
                                for orig in words:
                                    if (orig["left"], orig["top"]) == (new_w["left"], new_w["top"]):
                                        orig["text"] = new_w["text"]
                                        break
                            llm_vision_used = True
                            vstage["used"] = True
                            vstage["note"] = f"成功（行對齊，{len(words)} bbox 套用 LLM 校正）"
                            _emit(cp, "LLM 視覺校對完成（行對齊）")
                        else:
                            # ② Fallback：strict 1:1 word match（純 typo 校正）
                            cleaned_words = cleaned.split()
                            ok, used_words, fitnote = _fit_llm_words(cleaned_words, words, raw_cleaned=cleaned)
                            if ok:
                                for i, cw in enumerate(used_words):
                                    words[i]["text"] = cw
                                llm_vision_used = True
                                vstage["used"] = True
                                vstage["note"] = f"成功，套用到文字層（strict 1:1，{len(used_words)} 字）"
                                _emit(cp, "LLM 視覺校對完成（strict）")
                            else:
                                vstage["note"] = (
                                    f"行結構與 OCR 差距大（LLM {len(cleaned.splitlines())} 行 / "
                                    f"OCR {len(_group_words_into_lines(words))} 行），"
                                    "保留 tesseract 原文，LLM 校正可在下方比對"
                                )
                                _emit(cp, "LLM 校正無法對齊，保留原文")
                    else:
                        vstage["note"] = "LLM 無回傳內容，保留原文"
                        _emit(cp, vstage["note"])
                except Exception as e:
                    log.warning("LLM vision postprocess failed for page %d: %s", pno, e)
                    vstage["note"] = f"呼叫失敗：{e}"
                    _emit(cp, "LLM 視覺校對失敗，保留原文")
                page_stages["llm_vision"] = vstage
            # === LLM 文字校正（後做）===
            # 純文字 typo / 字元誤判清理。輸入是當前 words（可能已被視覺校對更新）。
            if llm_postprocess:
                model_tag = f" {llm_model_name}" if llm_model_name else ""
                cur_text = " ".join(w["text"] for w in words)
                _emit(cp, f"LLM 文字校正中{model_tag}，送 {len(words)} 字…")
                tstage: dict = {"used": False, "text": "", "note": ""}
                try:
                    cleaned = llm_postprocess(cur_text)
                    tstage["text"] = cleaned or ""
                    if cleaned and cleaned.strip():
                        # ① 行對齊優先
                        aligned = _align_llm_per_line(cleaned, words)
                        if aligned is not None:
                            for new_w in aligned:
                                for orig in words:
                                    if (orig["left"], orig["top"]) == (new_w["left"], new_w["top"]):
                                        orig["text"] = new_w["text"]
                                        break
                            llm_used = True
                            tstage["used"] = True
                            tstage["note"] = f"成功（行對齊，{len(words)} bbox 套用 LLM 校正）"
                            _emit(cp, "LLM 文字校正完成（行對齊）")
                        else:
                            # ② Strict 1:1 fallback
                            cleaned_words = cleaned.split()
                            ok, used_words, fitnote = _fit_llm_words(cleaned_words, words, raw_cleaned=cleaned)
                            if ok:
                                for i, cw in enumerate(used_words):
                                    words[i]["text"] = cw
                                llm_used = True
                                tstage["used"] = True
                                tstage["note"] = f"成功（strict 1:1，{len(used_words)} 字）"
                                _emit(cp, "LLM 文字校正完成（strict）")
                            else:
                                tstage["note"] = "行結構與 OCR 差距大，保留 tesseract 原文，LLM 校正可在下方比對"
                                _emit(cp, "LLM 校正無法對齊，保留原文")
                    else:
                        tstage["note"] = "LLM 無回傳內容，保留原文"
                        _emit(cp, tstage["note"])
                except Exception as e:
                    log.warning("LLM postprocess failed for page %d: %s", pno, e)
                    tstage["note"] = f"呼叫失敗：{e}"
                    _emit(cp, "LLM 文字校正失敗，保留原文")
                page_stages["llm_text"] = tstage
            # 最終套用到文字層的文字（可能是 OCR 原文 / vision 校對後 / text 校正後）
            page_stages["final_text"] = " ".join(w["text"] for w in words)

            # 寫文字層 — 單層 per-bbox：words[i].text 已在前面 LLM stage 被
            # 替換成 LLM 校正版（用比例對應），bbox 仍是 tesseract 原座標。
            # content + position 都 100% 在 PDF 裡，無 dual-layer interleave 問題。
            _emit(cp, f"寫入透明文字層（{len(words)} 字）…")
            n = add_text_layer_to_page(page, words, dpi=dpi)
            words_inserted += n

            stage_results.append(page_stages)
            pages_ocrd += 1
            # 在處理過的頁尾插一個透明 marker word，讓使用者 cmd+F
            # 搜「jtdt-pdf-ocr」就能驗證這頁的文字層是這支工具產生的
            try:
                pr = page.rect
                page.insert_text(
                    fitz.Point(2, pr.height - 2),
                    MARKER_KEYWORD,
                    fontname="helv", fontsize=4,
                    color=(0, 0, 0), render_mode=3,
                )
            except Exception:
                pass

        # 在 PDF metadata 蓋章 — Preview cmd+I 可看到 Producer 欄位
        producer = f"{PRODUCER_TAG} v{app_version}".strip() if app_version else PRODUCER_TAG
        tess_v = _tesseract_version()
        try:
            md = doc.metadata or {}
            md.update({
                "producer": producer,
                "creator": producer,
                "keywords": (md.get("keywords") or "") + f" OCR:tesseract-{tess_v} langs:{langs}",
            })
            doc.set_metadata(md)
        except Exception:
            pass

        if progress_cb:
            progress_cb(pages_total, pages_total, "輸出 PDF（壓縮 / 蓋章 metadata）…")
        dst_pdf.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(dst_pdf), garbage=3, deflate=True)
    finally:
        doc.close()

    return {
        "pages_total": pages_total,
        "pages_ocrd": pages_ocrd,
        "pages_skipped": pages_skipped,
        "words_inserted": words_inserted,
        "llm_used": llm_used,
        "llm_vision_used": llm_vision_used,
        "producer": producer,
        "tesseract_version": tess_v,
        "marker": MARKER_KEYWORD,
        "stage_results": stage_results,
    }


def is_tesseract_available() -> bool:
    import shutil
    try:
        from app.core.sys_deps import configure_pytesseract
        if configure_pytesseract():
            return True
    except Exception:
        pass
    return bool(shutil.which("tesseract"))


def get_active_langs(wanted: str = DEFAULT_LANGS) -> str:
    """過濾掉沒裝的語言。"""
    try:
        from app.core.sys_deps import configure_pytesseract
        configure_pytesseract()
        import pytesseract
        installed = set(pytesseract.get_languages(config="") or [])
    except Exception:
        return wanted
    parts = [p for p in wanted.split("+") if p in installed]
    return "+".join(parts) if parts else "eng"


def get_installed_langs() -> list[str]:
    """回傳本機 tesseract 已安裝的語言碼列表（過濾掉 osd / 空字串）。"""
    try:
        from app.core.sys_deps import configure_pytesseract
        configure_pytesseract()
        import pytesseract
        langs = pytesseract.get_languages(config="") or []
    except Exception:
        return []
    return sorted([l for l in langs if l and l != "osd"])
