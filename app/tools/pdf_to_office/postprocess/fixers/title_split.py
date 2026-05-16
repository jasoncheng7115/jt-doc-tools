"""標題 / 段落啟發式拆段 fixer。

對 docx 段落內含「：」「。」「！」「？」中間不換行的長段，看 PDFTruth 內這些標點
位置是否該是段落邊界（PDF 內這位置是新 line + y 距離大）→ 在標點後拆段。

通用情境：表單 / 公文 / 報價 / 申請類 PDF，標題列與欄位列在版面上
明顯分開（不同 y / 字體 / 區塊），但 pdf2docx 把它們黏成同一段。
也涵蓋章節 header（一、二、壹、）被黏在前段尾端的情形。

策略（保守）：
- 段落字數 ≥ 16（命中 _FORM_TITLE_SUFFIX 時不卡長度）
- 含 ≥ 1 個強分隔標點（：。！？）或章節 header 或表單標題尾端欄位
- 拆完不可超過 4 段（避免過度切分）
- 表格內段落不動
"""
from __future__ import annotations

import logging
import re
from copy import deepcopy

log = logging.getLogger(__name__)

# 強分隔標點 — 後面該換段
_STRONG_SEP = re.compile(r"([：。！？!?])")

# 中文 section header 起頭模式 — 在這字前一定該換段
_SECTION_HEAD = re.compile(r"([一二三四五六七八九十]+、|[壹貳參肆伍陸柒捌玖拾]+、)")

# 表單標題尾端緊接「申請日期：/編號：/日期：/簽收日期：」之類欄位 → 強制拆段
# 實務常見「○○申請表申請日期：」「○○單編號：」「訂購單日期：」等被 pdf2docx 黏一起
_FORM_TITLE_SUFFIX = re.compile(
    r"(表|書|單|單據|證|證明|報告|清冊|憑證)"
    r"\s*"
    r"(申請日期|報送日期|簽收日期|簽訂日期|核准日期|訂購日期|日期|編號|文號|字號|序號|統一編號|統編|填表)"
    r"(?=[：:])"
)

# 段落開頭的尺寸量測殘字（圖示位置溢出文字）— 該前剝離
_LEADING_MEASURE = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:cm|mm|公分|公尺|公釐|公里|英吋|英寸|°)\s*"
    r"(?=[一-鿿])",
    re.IGNORECASE,
)


def _is_listy(p) -> bool:
    style_name = (p.style.name if p.style else "") or ""
    return any(k in style_name for k in ("List", "Heading", "Title"))


def _split_at_separators(text: str, max_pieces: int = 4) -> list[str]:
    """在強分隔標點 / 章節起頭前後拆段。回 list of strings。"""
    if not text:
        return [text]
    # 0) 段落開頭剝離殘留尺寸字元（如 "60cm申請人："）
    m_meas = _LEADING_MEASURE.match(text)
    if m_meas:
        text = text[m_meas.end():]
    # 0.5) 在表單標題 + 欄位名稱黏一起時，於欄位名前插斷點
    # e.g. 「○○表申請日期：」/「○○單編號：」→ 強制拆成 ["...表", "申請日期："]
    HARD = "HARDBREAK"
    text2 = _FORM_TITLE_SUFFIX.sub(lambda m: m.group(1) + HARD + m.group(2), text)
    initial_chunks = [c for c in text2.split(HARD) if c.strip()] if HARD in text2 else [text]
    # 1) 對每個 chunk 用 SECTION_HEAD 再拆
    rebuilt: list[str] = []
    for chunk in initial_chunks:
        parts = _SECTION_HEAD.split(chunk)
        # SECTION_HEAD.split 把 marker 跟前後內容夾雜回 — 需重組
        # parts = [前文, marker1, 後文1, marker2, 後文2, ...]
        sub_rebuilt = [parts[0]]
        for i in range(1, len(parts), 2):
            marker = parts[i]
            rest = parts[i + 1] if i + 1 < len(parts) else ""
            sub_rebuilt.append(marker + rest)
        rebuilt.extend(p.strip() for p in sub_rebuilt if p and p.strip())
    # 2) 對每段再用 strong sep 拆 — 但只拆「冒號 : 後接文字 + 可選空白」這種，
    #    避免亂切「申請日期：」變兩段（人家本來就是「: 後填」)
    out = []
    for piece in rebuilt:
        # 在「：」後若文字繼續且長度 ≥ 8 → 拆
        sub = re.split(r"((?<=[：])\s*(?=[一-鿿]))", piece)
        # re.split 含 lookbehind/lookahead 不會包括 marker, 整個重組
        cur = []
        for chunk in sub:
            if chunk:
                cur.append(chunk)
        # 重新排成段落 — 用空白為分界
        if cur and len(cur) > 1:
            joined = "".join(cur)
            sub_pieces = re.split(r"(?<=[：])\s+(?=[一-鿿])", joined)
            sub_pieces = [s.strip() for s in sub_pieces if s.strip()]
            out.extend(sub_pieces)
        else:
            out.append(piece)
    if not out:
        return [text]
    if len(out) > max_pieces:
        return [text]  # 切太多段 — 保守 abort
    return out


def _split_paragraph_at_linebreaks(p, parent) -> int:
    """段落內含 \\n 的段（多行擠在同一個 docx paragraph）— 拆成多段。
    回新增的段數。pdf2docx 對 PDF block 含多行卻沒展開時常見此情形（如公司
    名 + 地址 + 電話 三行黏在一段、標題 + 副標題黏一段）。"""
    from docx.oxml.ns import qn
    text = p.text or ""
    if "\n" not in text:
        return 0
    pieces = [pi.strip() for pi in text.split("\n")]
    pieces = [pi for pi in pieces if pi]
    if len(pieces) < 2:
        return 0
    # 第一段塞回原 paragraph (壓在第一個 run)
    runs = p._element.findall(qn("w:r"))
    for ri, r in enumerate(runs):
        for t in r.findall(qn("w:t")):
            if ri == 0:
                t.text = pieces[0]
                t.set(qn("xml:space"), "preserve")
            else:
                t.text = ""
    for r in runs[1:]:
        p._element.remove(r)
    # 移除任何 w:br 元素（換行符 XML），免得殘留視覺斷行
    for br in p._element.findall(".//" + qn("w:br")):
        br.getparent().remove(br)
    # 後續 N-1 段在 p 後 insert
    from copy import deepcopy
    idx = list(parent).index(p._element)
    inserted = 0
    for i, piece in enumerate(pieces[1:], start=1):
        new_elem = deepcopy(p._element)
        new_runs = new_elem.findall(qn("w:r"))
        for ri, r in enumerate(new_runs):
            for t in r.findall(qn("w:t")):
                if ri == 0:
                    t.text = piece
                    t.set(qn("xml:space"), "preserve")
                else:
                    t.text = ""
        for r in new_runs[1:]:
            new_elem.remove(r)
        parent.insert(idx + i, new_elem)
        inserted += 1
    return inserted


def fix_title_split(docx_doc, pdf_truth, alignment) -> dict:
    """掃 docx 段落，對長段+含強分隔標點的拆段。"""
    split_count = 0
    pieces_inserted = 0
    measure_stripped = 0
    linebreak_split = 0
    body_paras = list(docx_doc.paragraphs)
    # === 0) 段內 \n 拆段（先做，後續 fixer 看到的就是已拆好）===
    for p in list(body_paras):
        if _is_listy(p):
            continue
        parent = p._element.getparent()
        if parent is None:
            continue
        n = _split_paragraph_at_linebreaks(p, parent)
        if n > 0:
            linebreak_split += n
    # 重新讀 paragraphs（剛才 insert 了新元素）
    body_paras = list(docx_doc.paragraphs)
    for di, p in enumerate(body_paras):
        if _is_listy(p):
            continue
        text = (p.text or "").strip()
        if not text:
            continue
        # 段落開頭尺寸殘字 (e.g. "60cm申請人") 即使段不分也剝
        m_meas = _LEADING_MEASURE.match(text)
        if m_meas:
            new_text = text[m_meas.end():]
            from docx.oxml.ns import qn as _qn
            runs0 = p._element.findall(_qn("w:r"))
            for ri, r in enumerate(runs0):
                for t in r.findall(_qn("w:t")):
                    if ri == 0:
                        t.text = new_text
                        t.set(_qn("xml:space"), "preserve")
                    else:
                        t.text = ""
            for r in runs0[1:]:
                p._element.remove(r)
            text = new_text
            measure_stripped += 1
        # 表單標題尾端欄位 pattern 命中時不卡長度（短標題「○○表\n\t申請日期:」也要拆）
        has_form_tail = bool(_FORM_TITLE_SUFFIX.search(text))
        if not has_form_tail and len(text) < 16:
            continue
        # 必須含強分隔 + section header pattern + 表單標題尾端欄位
        if not (_STRONG_SEP.search(text) or _SECTION_HEAD.search(text) or has_form_tail):
            continue
        pieces = _split_at_separators(text)
        if len(pieces) < 2:
            continue
        # 健全檢查
        merged_len = sum(len(pi) for pi in pieces)
        if abs(merged_len - len(text.replace(" ", ""))) / max(1, len(text)) > 0.30:
            continue
        # 拆 — 第 1 段塞回 p；2..N 段在 p 後 insert
        from docx.oxml.ns import qn
        runs = p._element.findall(qn("w:r"))
        for ri, r in enumerate(runs):
            for t in r.findall(qn("w:t")):
                if ri == 0:
                    t.text = pieces[0]
                    t.set(qn("xml:space"), "preserve")
                else:
                    t.text = ""
        for r in runs[1:]:
            p._element.remove(r)
        # insert pieces[1..]
        parent = p._element.getparent()
        idx = list(parent).index(p._element)
        for i, piece in enumerate(pieces[1:], start=1):
            new_elem = deepcopy(p._element)
            for r in new_elem.findall(qn("w:r")):
                for t in r.findall(qn("w:t")):
                    t.text = piece
                    t.set(qn("xml:space"), "preserve")
                    break
                # 移除多餘 t
                ts = r.findall(qn("w:t"))
                for extra in ts[1:]:
                    r.remove(extra)
                break
            extra_runs = new_elem.findall(qn("w:r"))[1:]
            for r in extra_runs:
                new_elem.remove(r)
            parent.insert(idx + i, new_elem)
            pieces_inserted += 1
        split_count += 1
    return {
        "fixer": "title_split",
        "split": split_count,
        "pieces_inserted": pieces_inserted,
        "measure_stripped": measure_stripped,
        "linebreak_split": linebreak_split,
    }
