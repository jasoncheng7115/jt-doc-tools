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


def _parse_llm_grounded_response(text: str, img_w: int, img_h: int) -> list[dict]:
    """解析「LLM 完整辨識」回的 JSON,容忍各種封裝格式。

    可接受的回應形式（依優先序試）:
      1. 純 JSON array: [{"text": "...", "bbox": [x0,y0,x1,y1]}, ...]
      2. 包在 ```json ... ``` fence 內
      3. 物件 wrapper: {"items": [...]} 或 {"words": [...]}
      4. qwen-vl 原生 grounding 格式: 「文字<box>x0 y0 x1 y1</box>」

    bbox 可接受:
      - [x0, y0, x1, y1] (左上 / 右下)
      - [x, y, w, h] (左上 + 寬高，用啟發判定:第 3 / 4 值 < 第 1 / 2 的兩倍就當寬高)
      - "x0 y0 x1 y1" 字串

    座標假設是縮圖（送進 LLM 的影像）的像素值。回的每個 item:
      {"text": str, "bbox": (x0,y0,x1,y1)}  — 已過濾無效項
    """
    import json
    import re as _re
    if not text or not text.strip():
        return []
    body = text.strip()

    # qwen2.5-VL 自然語言格式（實測穩定）:
    #   "文字內容" - (x1, y1, x2, y2)
    # 或: "文字內容" – (x1, y1, x2, y2)  /  "文字內容": (x1, y1, x2, y2)
    # 引號可為 " " 「 」 ' ', dash 可為 - – — :, 座標是絕對像素值
    plain_pattern = _re.compile(
        r'["「“‘]([^"」”’\n]+?)["」”’]'
        r'\s*[-–—:：~]?\s*'
        r'\(\s*(\d+(?:\.\d+)?)\s*[,，]\s*(\d+(?:\.\d+)?)\s*[,，]\s*'
        r'(\d+(?:\.\d+)?)\s*[,，]\s*(\d+(?:\.\d+)?)\s*\)'
    )
    plain_hits = plain_pattern.findall(body)
    if plain_hits:
        # Qwen2.5-VL 對 plain-text grounding prompt 會用我們告訴它的影像尺寸回絕對像素，
        # 不像原生 token 那樣強制 0-1000 正規化。直接當絕對像素處理。
        items = []
        for t, x0s, y0s, x1s, y1s in plain_hits:
            t = t.strip()
            if not t:
                continue
            x0, y0, x1, y1 = float(x0s), float(y0s), float(x1s), float(y1s)
            # 若給的是 (x, y, w, h) 而非 (x0, y0, x1, y1) — 第 3 / 4 比第 1 / 2 小很多時當寬高
            if x1 < x0 and y1 < y0:
                x1, y1 = x0 + x1, y0 + y1
            elif x1 <= x0 or y1 <= y0:
                continue
            # clamp 到影像範圍內(模型偶爾會超出 1-2 px)
            if img_w > 0 and img_h > 0:
                x0 = max(0, min(x0, img_w))
                y0 = max(0, min(y0, img_h))
                x1 = max(0, min(x1, img_w))
                y1 = max(0, min(y1, img_h))
                if x1 - x0 < 1 or y1 - y0 < 1:
                    continue
            items.append({"text": t, "bbox": (x0, y0, x1, y1)})
        if items:
            return items

    # qwen2 / qwen3-VL 原生 token 格式:
    #   <|object_ref_start|>文字<|object_ref_end|><|box_start|>(x1,y1),(x2,y2)<|box_end|>
    # 座標通常是 0~1000 的正規化（相對影像 w / h）。若超過 1000 則當作絕對像素。
    qwen3_pattern = _re.compile(
        r"<\|object_ref_start\|>(?P<text>.*?)<\|object_ref_end\|>\s*"
        r"<\|box_start\|>(?P<box>.*?)<\|box_end\|>",
        _re.DOTALL,
    )
    qwen3_hits = qwen3_pattern.findall(body)
    if qwen3_hits:
        items = []
        for t, coords in qwen3_hits:
            t = t.strip().strip("，,。.;；:：")
            if not t:
                continue
            nums = _re.findall(r"-?\d+(?:\.\d+)?", coords)
            if len(nums) >= 4:
                x0, y0, x1, y1 = map(float, nums[:4])
                # 偵測是否 0-1000 正規化：四個數都 <= 1000 且至少一個 > image dim → 正規化
                vals = (x0, y0, x1, y1)
                if max(vals) <= 1000 and img_w > 0 and img_h > 0:
                    # 假設正規化（最常見情況）。若 image 本身就 > 1000 像素則此判斷會誤殺，
                    # 但我們的縮圖預設最大長邊 1568；max 1000 + img>1000 表示更可能是 normalized
                    x0 = x0 * img_w / 1000.0
                    x1 = x1 * img_w / 1000.0
                    y0 = y0 * img_h / 1000.0
                    y1 = y1 * img_h / 1000.0
                items.append({"text": t, "bbox": (x0, y0, x1, y1)})
        if items:
            return items

    # 較舊的 qwen-vl <box>x0 y0 x1 y1</box> 格式
    qwen_pattern = _re.compile(r'(?P<text>[^<>\n]+?)\s*<\s*box\s*>\s*([\d.,\s]+)\s*<\s*/\s*box\s*>',
                                _re.IGNORECASE)
    qwen_hits = qwen_pattern.findall(body)
    if qwen_hits:
        items = []
        for t, coords in qwen_hits:
            t = t.strip().strip("，,。.;；:：")
            if not t:
                continue
            nums = _re.findall(r"-?\d+(?:\.\d+)?", coords)
            if len(nums) >= 4:
                x0, y0, x1, y1 = map(float, nums[:4])
                # 若 x1 / y1 像寬高，轉成右下
                if x1 < x0 * 2 and y1 < y0 * 2 and x1 < img_w / 2 and y1 < img_h / 2:
                    x1, y1 = x0 + x1, y0 + y1
                items.append({"text": t, "bbox": (x0, y0, x1, y1)})
        if items:
            return items

    # JSON 路徑（含 fence 處理）
    # 移除 ```json ... ``` 或 ``` ... ``` 包裝
    body = _re.sub(r"^```(?:json)?\s*", "", body, flags=_re.IGNORECASE)
    body = _re.sub(r"\s*```\s*$", "", body)
    # 找第一個 [ 或 { 起、最後一個 ] 或 } 止
    first_bracket = min((p for p in (body.find("["), body.find("{")) if p >= 0), default=-1)
    last_bracket = max(body.rfind("]"), body.rfind("}"))
    if first_bracket < 0 or last_bracket <= first_bracket:
        return []
    body = body[first_bracket:last_bracket+1]
    try:
        data = json.loads(body)
    except Exception:
        return []
    # 取 list
    if isinstance(data, dict):
        for key in ("items", "words", "regions", "results", "data"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            return []
    if not isinstance(data, list):
        return []

    items = []
    for it in data:
        if not isinstance(it, dict):
            continue
        # text 欄位
        t = it.get("text") or it.get("content") or it.get("value") or ""
        if not isinstance(t, str) or not t.strip():
            continue
        # bbox 欄位
        bbox_raw = it.get("bbox") or it.get("box") or it.get("rect") or it.get("position")
        if bbox_raw is None:
            # 也接受 x, y, w, h 各自欄位
            if all(k in it for k in ("x", "y", "w", "h")):
                bbox_raw = [it["x"], it["y"], it["w"], it["h"]]
            elif all(k in it for k in ("x0", "y0", "x1", "y1")):
                bbox_raw = [it["x0"], it["y0"], it["x1"], it["y1"]]
            else:
                continue
        # 解析 bbox 成 4 個 float
        if isinstance(bbox_raw, str):
            nums = _re.findall(r"-?\d+(?:\.\d+)?", bbox_raw)
            if len(nums) < 4:
                continue
            bbox_raw = [float(n) for n in nums[:4]]
        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
            continue
        try:
            x0, y0, x1, y1 = float(bbox_raw[0]), float(bbox_raw[1]), float(bbox_raw[2]), float(bbox_raw[3])
        except Exception:
            continue
        # 若第 3 / 4 是寬高（不是右下座標），轉成 x1 / y1
        if x1 <= 0 or y1 <= 0:
            continue
        # 啟發：右下小於左上 → 應該是寬高
        if x1 < x0 or y1 < y0:
            x1, y1 = x0 + x1, y0 + y1
        # clamp 到影像範圍內
        if img_w > 0 and img_h > 0:
            x0 = max(0, min(x0, img_w))
            y0 = max(0, min(y0, img_h))
            x1 = max(0, min(x1, img_w))
            y1 = max(0, min(y1, img_h))
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        items.append({"text": t.strip(), "bbox": (x0, y0, x1, y1)})
    return items


def add_llm_text_overlay_on_page(page: "fitz.Page", llm_text: str) -> int:
    """LLM 直接辨識用 — 把 LLM 回的文字以**透明文字層**覆蓋到頁面上(不是頁外)，
    這樣使用者用滑鼠拖選頁面任何區域，都能選到 OCR 出來的文字並複製。

    跟 `add_llm_search_layer_offpage` 的差別：
    - off-page 把文字放在 page rect 外 → 只能 Cmd+F 搜尋，拖選不到
    - on-page 把文字鋪在頁面內 → 拖選有 hit
    沒有 word 級 bbox 所以拖選不會精準到字，但整頁的內容可以一次拖選複製。

    回插入的字數(實際字元數，不是「1=成功」)。
    """
    if not llm_text or not llm_text.strip():
        return 0
    text = llm_text.strip()
    try:
        # 留 20pt 邊距，避免文字層蓋到頁緣截斷
        rect = fitz.Rect(20, 20,
                         page.rect.width - 20, page.rect.height - 20)
        # 由大到小試字級，確保所有字都塞得進可見頁面區
        for size in (10, 8, 6, 4, 3, 2, 1):
            ret = page.insert_textbox(
                rect, text,
                fontname="china-t",
                fontsize=size,
                color=(0, 0, 0),
                render_mode=3,  # 透明（不顯示但可選取 / 可搜尋）
                align=0,
            )
            if ret >= 0:
                return len(text)
        # 最小字級仍塞不下 → 強制塞入(會被裁，但至少有部分可選)
        page.insert_textbox(rect, text, fontname="china-t",
                             fontsize=1, color=(0, 0, 0), render_mode=3, align=0)
        return len(text)
    except Exception as e:
        log.warning("add_llm_text_overlay_on_page failed: %s", e)
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
    llm_direct_ocr: Optional[Callable[[bytes], str]] = None,
    llm_direct_model_name: str = "",
    llm_align_ocr: Optional[Callable[[bytes], str]] = None,
    llm_align_model_name: str = "",
    llm_full_ocr: Optional[Callable[[bytes], str]] = None,
    llm_full_model_name: str = "",
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
    llm_direct_used = False
    llm_align_used = False
    llm_full_used = False
    # 累計各 LLM 路徑總耗時(秒) — 顯示「LLM 共用了 N 秒」給 user
    llm_full_total_s = 0.0
    llm_align_total_s = 0.0
    llm_direct_total_s = 0.0
    # OCR 引擎使用統計(顯示「跑遠端 GPU / 本機 CPU」+ 總耗時)
    ocr_engine_pages: dict[str, int] = {}  # engine_used -> page count
    ocr_engine_total_s = 0.0
    ocr_remote_url = ""
    # admin 設定的 engine（給完成訊息對照是否退回到別的）
    ocr_chosen_engine = ""
    ocr_remote_on = False
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
            # === LLM 完整辨識模式（grounded OCR：LLM 同時給文字 + 座標 JSON）===
            # 適合 qwen-vl / internvl 等對 grounding 訓練充分的模型。失敗（JSON 解析錯 /
            # bbox 不合理 / 無回應）→ fall through 到 LLM 直接辨識 或 原 OCR 引擎。
            did_llm_full = False
            if llm_full_ocr is not None:
                mtag = f" {llm_full_model_name}" if llm_full_model_name else ""
                small_png = _shrink_png_for_vision(png, max_long_side=llm_vision_image_max)
                # 取縮圖實際尺寸（給座標換算用）
                try:
                    from PIL import Image as _PILImg
                    import io as _io
                    _img = _PILImg.open(_io.BytesIO(small_png))
                    small_w, small_h = _img.size
                except Exception:
                    small_w = small_h = 0
                _emit(cp, f"LLM 完整辨識中{mtag}（影像 {len(small_png)//1024}KB，{small_w}×{small_h}）…")
                fstage: dict = {"used": False, "text": "", "note": "", "n_items": 0, "elapsed_s": 0.0}
                import time as _t_full
                _t_full_start = _t_full.time()
                try:
                    raw_resp = llm_full_ocr(small_png) or ""
                    _t_full_elapsed = _t_full.time() - _t_full_start
                    fstage["elapsed_s"] = round(_t_full_elapsed, 1)
                    llm_full_total_s += _t_full_elapsed
                    items = _parse_llm_grounded_response(raw_resp, small_w, small_h)
                    log.info("llm-full page %d: %d chars response → %d items parsed in %.1fs; sample=%r",
                             pno, len(raw_resp), len(items), _t_full_elapsed, raw_resp[:400])
                    if items and small_w > 0 and small_h > 0:
                        # 把 LLM 給的 bbox(縮圖像素座標)換算成「渲染影像 pixel 空間 @ dpi」,
                        # 讓 add_text_layer_to_page 用一致的 px_to_pt = 72 / dpi 轉成 PDF pt。
                        # 注意:不可直接乘 page.rect.width / small_w 給出 PDF pt — add_text_layer
                        # 還會再乘 72/dpi 造成 dpi 倍縮放 bug(highlight 變很小擠左上)。
                        try:
                            from PIL import Image as _PILImg2
                            import io as _io2
                            _renderedimg = _PILImg2.open(_io2.BytesIO(png))
                            rendered_w, rendered_h = _renderedimg.size
                        except Exception:
                            # fallback: 假設用 dpi 渲染，從 page.rect 算回
                            rendered_w = int(page.rect.width * dpi / 72.0)
                            rendered_h = int(page.rect.height * dpi / 72.0)
                        sx = rendered_w / small_w
                        sy = rendered_h / small_h
                        words = []
                        for it in items:
                            x0, y0, x1, y1 = it["bbox"]
                            left = float(x0) * sx
                            top = float(y0) * sy
                            width = float(x1 - x0) * sx
                            height = float(y1 - y0) * sy
                            if width <= 0 or height <= 0:
                                continue
                            words.append({
                                "text": it["text"],
                                "left": left, "top": top,
                                "width": width, "height": height,
                                "conf": 95,
                            })
                        if words:
                            added = add_text_layer_to_page(page, words, dpi=dpi)
                            if added > 0:
                                pages_ocrd += 1
                                words_inserted += added
                                llm_full_used = True
                                fstage["used"] = True
                                fstage["n_items"] = len(words)
                                fstage["text"] = "\n".join(w["text"] for w in words)
                                fstage["note"] = f"成功（{added} 個 word 直接寫入，使用 LLM 座標；LLM 用時 {fstage['elapsed_s']}s）"
                                _emit(cp, f"LLM 完整辨識完成（{added} 字 + 座標，{fstage['elapsed_s']}s）")
                                did_llm_full = True
                            else:
                                fstage["note"] = "Word 全數無效，退回 LLM 直接辨識"
                        else:
                            fstage["note"] = "解析出 0 個有效 word，退回 LLM 直接辨識"
                    else:
                        fstage["note"] = "JSON 解析失敗或無 word，退回 LLM 直接辨識"
                except Exception as e:
                    _t_full_elapsed = _t_full.time() - _t_full_start
                    fstage["elapsed_s"] = round(_t_full_elapsed, 1)
                    llm_full_total_s += _t_full_elapsed
                    fstage["note"] = f"LLM 完整辨識失敗（{e}, {fstage['elapsed_s']}s），退回 LLM 直接辨識"
                    log.warning("llm-full page %d failed in %.1fs: %s", pno, _t_full_elapsed, e)
                stage_results.append({"page": cp, "llm_full": fstage})
                if did_llm_full:
                    continue
                _emit(cp, fstage["note"])
                # 若有 llm_direct_ocr，自然 fall through 走那條；否則繼續往下走原 OCR
            # === LLM 直接辨識模式：跳過 EasyOCR/Tesseract，直接送 vision LLM ===
            # 沒有 word 級 bbox，直接拿純文字 → 用 off-page invisible text layer 收容
            # （可搜尋、可滑鼠選取複製整段，但位置不對應原版面）。
            # **失敗時(LLM 連不上/超時/回空白) 自動退回原 OCR 引擎路徑**,確保本頁仍有文字層。
            did_llm_direct = False
            llm_direct_fallback_note = ""
            if llm_direct_ocr is not None:
                mtag = f" {llm_direct_model_name}" if llm_direct_model_name else ""
                _emit(cp, f"LLM 直接辨識中{mtag}（影像 {len(png)//1024}KB）…")
                small_png = _shrink_png_for_vision(png, max_long_side=llm_vision_image_max)
                dstage: dict = {"used": False, "text": "", "note": ""}
                try:
                    text = llm_direct_ocr(small_png) or ""
                    text = text.strip()
                    if text:
                        chars_added = add_llm_text_overlay_on_page(page, text)
                        if chars_added > 0:
                            pages_ocrd += 1
                            words_inserted += chars_added
                            llm_direct_used = True
                            dstage["used"] = True
                            dstage["text"] = text
                            dstage["note"] = f"成功（{chars_added} 字寫入透明文字層）"
                            _emit(cp, f"LLM 直接辨識完成（{chars_added} 字）")
                            did_llm_direct = True
                        else:
                            dstage["note"] = "LLM 回覆有文字但寫入搜尋層失敗"
                            llm_direct_fallback_note = "LLM 寫入失敗，退回 OCR 引擎"
                    else:
                        dstage["note"] = "LLM 回覆空白"
                        llm_direct_fallback_note = "LLM 回覆空白，退回 OCR 引擎"
                except Exception as e:
                    dstage["note"] = f"LLM 直接辨識呼叫失敗：{e}"
                    log.warning("llm-direct page %d failed: %s", pno, e)
                    llm_direct_fallback_note = f"LLM 失敗({e}),退回 OCR 引擎"
                stage_results.append({"page": cp, "llm_direct": dstage})
                if did_llm_direct:
                    continue
                # fall through 到下面的 EasyOCR/Tesseract
                if llm_direct_fallback_note:
                    _emit(cp, llm_direct_fallback_note)
            # 用抽象 OCR engine：依 admin 設定(預設 easyocr),失敗自動 fallback tesseract。
            # 若 admin 已設定 + 啟用「外部 GPU OCR Server」會優先打遠端，失敗自動退本機。
            from app.core import ocr_engine as _oe
            from app.core import ocr_remote_settings as _ors_check
            chosen_engine = _oe.get_default_engine()
            remote_on = chosen_engine == "easyocr" and _ors_check.is_enabled_and_configured()
            # 「使用者意圖」包含 remote — 失敗退回 local 也算 fallback
            ocr_chosen_engine = "easyocr-remote" if remote_on else chosen_engine
            ocr_remote_on = remote_on
            engine_label = f"{chosen_engine}-remote(GPU)" if remote_on else chosen_engine
            _emit(cp, f"OCR 辨識中({engine_label} {langs})…")
            import time as _t_ocr
            _t_ocr_start = _t_ocr.time()
            words, engine_used = _oe.recognize_image(png, langs, preprocess=True)
            _t_ocr_elapsed = _t_ocr.time() - _t_ocr_start
            ocr_engine_total_s += _t_ocr_elapsed
            ocr_engine_pages[engine_used] = ocr_engine_pages.get(engine_used, 0) + 1
            if engine_used == "easyocr-remote":
                ocr_remote_url = _ors_check.get().get("url", "")
            # 「意圖」engine — remote_on 時意圖是 easyocr-remote (GPU)
            intended = "easyocr-remote" if remote_on else chosen_engine
            if engine_used != intended and words:
                if engine_used == "easyocr-remote":
                    _emit(cp, f"OCR 完成 (遠端 GPU EasyOCR @ {_ors_check.get().get('url', '')})")
                elif intended == "easyocr-remote":
                    # 選用 GPU remote 但失敗 → 退回本機 (CPU)
                    _emit(cp, f"OCR 完成（遠端 GPU EasyOCR 失敗，改用本機 {engine_used} (CPU)）")
                else:
                    _emit(cp, f"OCR 完成（{chosen_engine} 失敗，改用 {engine_used}）")
            elif engine_used == "easyocr-remote" and words:
                _emit(cp, f"OCR 完成 (遠端 GPU EasyOCR @ {_ors_check.get().get('url', '')})")
            if not words:
                _emit(cp, "未辨識到文字，略過")
                stage_results.append({
                    "page": cp,
                    "ocr_raw": {"text": "", "word_count": 0, "note": "tesseract 未辨識到任何文字"},
                })
                continue
            # 記錄 tesseract 原始文字（給前端 collapsible 顯示）
            ocr_raw_text = " ".join(w["text"] for w in words)
            ocr_raw_note = f"engine={engine_used}"
            if engine_used == "easyocr-remote":
                ocr_raw_note += f" @ {_ors_check.get().get('url','')}"
            page_stages: dict = {
                "page": cp,
                "ocr_raw": {"text": ocr_raw_text, "word_count": len(words), "note": ocr_raw_note},
            }
            # === LLM 對位辨識（hybrid）===
            # LLM 對影像獨立做一次 OCR（從零識別，不看 OCR 結果），把回的文字
            # 透過行對齊映射到 EasyOCR 的 bbox 上。對齊失敗 → 保留 OCR 原字
            # （避免把 LLM 腦補的文字塞到精準格子裡）。
            if llm_align_ocr:
                amodel_tag = f" {llm_align_model_name}" if llm_align_model_name else ""
                small_png = _shrink_png_for_vision(png, max_long_side=llm_vision_image_max)
                _emit(cp, f"LLM 對位辨識中{amodel_tag}（影像 {len(small_png)//1024}KB）…")
                astage: dict = {"used": False, "text": "", "note": "", "fallback_to_ocr": False}
                try:
                    llm_fresh = llm_align_ocr(small_png) or ""
                    llm_fresh = llm_fresh.strip()
                    astage["text"] = llm_fresh
                    if llm_fresh:
                        aligned = _align_llm_per_line(llm_fresh, words)
                        if aligned is not None:
                            for new_w in aligned:
                                for orig in words:
                                    if (orig["left"], orig["top"]) == (new_w["left"], new_w["top"]):
                                        orig["text"] = new_w["text"]
                                        break
                            astage["used"] = True
                            astage["note"] = f"成功對位（{len(words)} bbox 套用 LLM 文字）"
                            llm_align_used = True
                            _emit(cp, "LLM 對位辨識完成（行對齊）")
                        else:
                            astage["note"] = "LLM 行數 / 字數對不上，保留 EasyOCR 原字（避免腦補）"
                            astage["fallback_to_ocr"] = True
                            _emit(cp, "LLM 行數對不上，保留 EasyOCR 原字")
                    else:
                        astage["note"] = "LLM 回空白，保留 EasyOCR 原字"
                        astage["fallback_to_ocr"] = True
                except Exception as e:
                    astage["note"] = f"LLM 失敗（{e}）,保留 EasyOCR 原字"
                    astage["fallback_to_ocr"] = True
                    log.warning("llm-align page %d failed: %s", pno, e)
                    _emit(cp, f"LLM 對位辨識失敗，保留 EasyOCR 原字: {e}")
                page_stages["llm_align"] = astage
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
        "llm_direct_used": llm_direct_used,
        "llm_align_used": llm_align_used,
        "llm_full_used": llm_full_used,
        "llm_full_total_s": round(llm_full_total_s, 1),
        "llm_align_total_s": round(llm_align_total_s, 1),
        "llm_direct_total_s": round(llm_direct_total_s, 1),
        "ocr_engine_pages": ocr_engine_pages,
        "ocr_engine_total_s": round(ocr_engine_total_s, 1),
        "ocr_remote_url": ocr_remote_url,
        "ocr_chosen_engine": ocr_chosen_engine,
        "ocr_remote_on": ocr_remote_on,
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
