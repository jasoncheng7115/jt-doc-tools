"""圖片位置 / 大小校正 fixer。

用 PDFTruth.images（含 SHA1 content hash + bbox + 像素尺寸）跟 docx 內嵌圖片配對。

Sprint 2 簡化版：
- 對 docx 每張內嵌圖，看 size 是否跟 PDF 對應圖差太多 → 調整為 PDF 真值大小
- 「漏抓 / 多餘 圖片」標警告但不自動補刪（風險高）

完整版 (Sprint 3)：用 imagehash perceptual hash 處理重複編碼但內容相同的圖。
這裡先做 SHA1 content hash 配對 — pdf2docx 抽出的圖通常 byte-for-byte 跟 PDF 一致。
"""
from __future__ import annotations

import hashlib
import logging
from io import BytesIO

from docx.oxml.ns import qn
from docx.shared import Emu

log = logging.getLogger(__name__)

EMU_PER_PT = 12700


def _get_docx_inline_images(docx_doc) -> list[dict]:
    """收集 docx 所有 inline drawings 的 (run, drawing_element, ext, blob_hash, size_emu)。"""
    out = []
    image_part_map = {}
    try:
        # part.rels — image relationships
        for rel in docx_doc.part.rels.values():
            if "image" in (rel.reltype or "").lower():
                target = rel.target_part
                try:
                    blob = target.blob
                    h = hashlib.sha1(blob, usedforsecurity=False).hexdigest()[:16]
                    image_part_map[rel.rId] = h
                except Exception:
                    pass
    except Exception as e:
        log.debug("collect image parts failed: %s", e)

    def _walk(paras):
        for p in paras:
            for r in p.runs:
                for drw in r._element.findall(qn("w:drawing")):
                    # blip ref
                    blip = drw.find(".//" + qn("a:blip"))
                    rid = blip.get(qn("r:embed")) if blip is not None else None
                    h = image_part_map.get(rid, "")
                    # extents
                    ext = drw.find(".//" + qn("a:ext"))
                    cx = int(ext.get("cx")) if (ext is not None and ext.get("cx")) else 0
                    cy = int(ext.get("cy")) if (ext is not None and ext.get("cy")) else 0
                    out.append({"run": r, "drawing": drw, "ext": ext,
                                "rid": rid, "hash": h, "cx": cx, "cy": cy})

    _walk(docx_doc.paragraphs)
    for tbl in docx_doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                _walk(cell.paragraphs)
    return out


def fix_image_position_fix(docx_doc, pdf_truth, alignment) -> dict:
    if pdf_truth is None:
        return {"fixer": "image_position_fix", "adjusted": 0, "skipped_no_pdftruth": True}

    pdf_imgs_by_hash: dict[str, list] = {}
    for p in pdf_truth.pages:
        for im in p.images:
            if im.image_hash:
                pdf_imgs_by_hash.setdefault(im.image_hash, []).append(im)

    docx_imgs = _get_docx_inline_images(docx_doc)
    if not docx_imgs or not pdf_imgs_by_hash:
        return {"fixer": "image_position_fix", "adjusted": 0,
                "docx_images": len(docx_imgs), "pdf_images": sum(len(v) for v in pdf_imgs_by_hash.values())}

    adjusted = 0
    matched = 0
    for di in docx_imgs:
        if not di["hash"]:
            continue
        candidates = pdf_imgs_by_hash.get(di["hash"]) or []
        if not candidates:
            continue
        matched += 1
        pdf_im = candidates[0]
        x0, y0, x1, y1 = pdf_im.bbox
        pdf_w_pt = max(0.0, x1 - x0)
        pdf_h_pt = max(0.0, y1 - y0)
        if pdf_w_pt <= 0 or pdf_h_pt <= 0:
            continue
        target_cx = int(pdf_w_pt * EMU_PER_PT)
        target_cy = int(pdf_h_pt * EMU_PER_PT)
        # 差異 > 10% 才調整
        if di["cx"] > 0 and abs(di["cx"] - target_cx) / di["cx"] > 0.10:
            di["ext"].set("cx", str(target_cx))
            adjusted += 1
        if di["cy"] > 0 and abs(di["cy"] - target_cy) / di["cy"] > 0.10:
            di["ext"].set("cy", str(target_cy))

    return {
        "fixer": "image_position_fix",
        "adjusted": adjusted,
        "matched_by_hash": matched,
        "docx_images": len(docx_imgs),
        "pdf_images_unique_hash": len(pdf_imgs_by_hash),
    }
