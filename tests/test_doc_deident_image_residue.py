"""去識別化必須把**圖片裡的**個資也刪掉（外部稽核 F01，v1.15.28）。

**這個 bug 為什麼可怕**：產出的檔案看起來完全正常 —— 文字抽不到、畫面有黑框
—— 但把頁面上的圖片抽出來重新 OCR，個資完整還原。而受影響的檔案形狀
（掃描圖片 ＋ 隱形 OCR 文字層）**正是我們自己 `pdf-ocr` 產出的東西**。

成因是 `apply_redactions(images=PDF_REDACT_IMAGE_NONE)` —— 那個參數明確要求
「不要動圖片」，而清像素本來就是 PyMuPDF 的**預設值**。也就是特地把安全的
預設關掉（寫法是從 pdf-editor 抄來的，那支工具的目的剛好相反）。

**判準不看 OCR 也成立**：直接檢查那塊區域的像素值。OCR 只當附加驗證
（CI 上不一定有 tesseract）。
"""
from __future__ import annotations

import io
import shutil
import subprocess

import fitz
import numpy as np
import pytest

from app.tools.doc_deident import redact_core

# 頁面座標（pt）與那行字在圖片上的位置
_PAGE_W, _PAGE_H = 595, 842
_SECRET_RECT = fitz.Rect(40, 100, 300, 130)


def _scanned_pdf(tmp_path, *, jpeg: bool = True):
    """做一份「掃描圖片 ＋ 隱形 OCR 文字層」的 PDF —— pdf-ocr 的產出形狀。"""
    cv2 = pytest.importorskip("cv2")
    h, w = 1684, 1190                      # A4 @ ~144 dpi
    img = np.full((h, w), 250, np.uint8)
    cv2.putText(img, "SSN 123-45-6789", (70, 225),
                cv2.FONT_HERSHEY_SIMPLEX, 1.6, 20, 4)
    # 真實掃描件有紙張紋理與雜訊 —— 這件事會決定 PNG 與 JPEG 的大小關係，
    # 用平滑的合成圖會測不到「清像素之後檔案膨脹」那個副作用。
    rng = np.random.default_rng(11)
    img = np.clip(img.astype(np.int16) + rng.normal(0, 10, (h, w)),
                  0, 255).astype(np.uint8)
    src = tmp_path / ("scan.jpg" if jpeg else "scan.png")
    if jpeg:
        cv2.imwrite(str(src), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    else:
        cv2.imwrite(str(src), img)

    doc = fitz.open()
    page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
    page.insert_image(fitz.Rect(0, 0, _PAGE_W, _PAGE_H), filename=str(src))
    # render_mode=3 → 看不見但抽得到（OCR 文字層就是這樣做的）
    page.insert_text((44, 122), "SSN 123-45-6789", fontsize=14, render_mode=3)
    out = tmp_path / "scanned.pdf"
    doc.save(str(out))
    doc.close()
    return out


def _region_pixels(pdf_path, rect):
    """把產出頁面上的圖片抽出來，回傳 `rect` 對應那塊的灰階像素。"""
    cv2 = pytest.importorskip("cv2")
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    out = []
    for info in page.get_images(full=True):
        meta = doc.extract_image(info[0]) or {}
        raw = meta.get("image")
        if not raw:
            continue
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        if arr is None:
            continue
        sx, sy = arr.shape[1] / _PAGE_W, arr.shape[0] / _PAGE_H
        roi = arr[int(rect.y0 * sy):int(rect.y1 * sy),
                  int(rect.x0 * sx):int(rect.x1 * sx)]
        if roi.size:
            out.append(roi)
    doc.close()
    return out


def _redact(pdf_path, out_path, *, fill=(0, 0, 0)):
    doc = fitz.open(str(pdf_path))
    redact_core.apply_page_redactions(doc[0], [_SECRET_RECT], fill=fill)
    doc.save(str(out_path), garbage=3, deflate=True)
    doc.close()


def test_the_pixels_under_the_black_bar_are_actually_gone(tmp_path):
    """這是 F01 的核心判準：圖片裡那塊必須沒有墨水。"""
    src = _scanned_pdf(tmp_path)
    before = _region_pixels(src, _SECRET_RECT)
    assert before and before[0].min() < 120, "測試素材本身要有黑字，否則測不到東西"

    out = tmp_path / "redacted.pdf"
    _redact(src, out)
    after = _region_pixels(out, _SECRET_RECT)
    assert after, "產出頁面上應該還有圖片（不可以整張刪掉，掃描件會整頁空白）"
    worst = min(int(r.min()) for r in after)
    assert worst >= 200, (
        f"圖片裡還留著墨水（最暗 {worst}）—— 個資可以從圖片撈回來")


def test_masking_mode_also_clears_the_image(tmp_path):
    """遮罩 / 替換模式也要清 —— 把假值貼在原始像素上面不叫遮罩。"""
    src = _scanned_pdf(tmp_path)
    out = tmp_path / "masked.pdf"
    _redact(src, out, fill=None)          # 遮罩模式不畫色塊
    after = _region_pixels(out, _SECRET_RECT)
    worst = min(int(r.min()) for r in after)
    assert worst >= 200, f"遮罩模式沒有清掉圖片像素（最暗 {worst}）"


def test_text_layer_is_removed_too(tmp_path):
    src = _scanned_pdf(tmp_path)
    out = tmp_path / "redacted.pdf"
    _redact(src, out)
    doc = fitz.open(str(out))
    assert "123-45-6789" not in doc[0].get_text()
    doc.close()


def test_the_rest_of_the_page_is_left_alone(tmp_path):
    """只清選取範圍 —— 其他地方的內容不可以跟著消失。"""
    src = _scanned_pdf(tmp_path)
    out = tmp_path / "redacted.pdf"
    _redact(src, out)
    # 頁面下半部沒有被選取，應該維持原本的紙張底色（不是全白也不是全黑）
    rest = _region_pixels(out, fitz.Rect(40, 400, 300, 500))
    assert rest, "圖片不可以被整張移除"
    assert 200 <= rest[0].mean() <= 255


def test_the_ui_warns_that_the_file_may_grow(tmp_path):
    """清像素會讓圖片重新編碼、檔案變大（實測 2.4 倍）。

    我們刻意**不做**重壓（4 秒/頁只換到 20~30%，見 `redact_core` 的說明），
    所以**介面必須講出來**，否則使用者會回報「檔案怎麼變大了」。
    順便指路到 `pdf-compress` —— 那支才是處理檔案大小的工具。
    """
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    tpl = (root / "app" / "tools" / "doc_deident" / "templates"
           / "doc_deident.html").read_text(encoding="utf-8")
    # **註解要先去掉**：這條守門第一版被寫在旁邊、解釋這件事的 Jinja 註解
    # 騙過去了（把提醒整行刪掉還是綠的）。CLAUDE.md 記過同一個坑。
    visible = re.sub(r"\{#.*?#\}", "", tpl, flags=re.S)
    visible = re.sub(r"<!--.*?-->", "", visible, flags=re.S)
    assert "檔案可能變大" in visible, "結果頁沒有提醒檔案可能變大"
    assert "PDF 壓縮" in visible, "沒有告訴使用者要縮小檔案該用哪支工具"
    assert 'id="ddSizeNote"' in visible, "提醒的元素不見了（JS 要靠 id 控制顯示）"


def test_a_lossless_source_is_never_recompressed_to_jpeg(tmp_path):
    """無損來源不可以被偷偷轉成 JPEG。

    我們完全不做重壓，所以這條現在必然成立 —— 它守的是「日後有人為了縮檔案
    加重壓時，別忘了無損來源不能降畫質、帶透明遮罩的不能轉 JPEG」。
    """
    src = _scanned_pdf(tmp_path, jpeg=False)
    out = tmp_path / "png_src.pdf"
    _redact(src, out)
    doc = fitz.open(str(out))
    exts = [((doc.extract_image(i[0]) or {}).get("ext") or "").lower()
            for i in doc[0].get_images(full=True)]
    doc.close()
    assert "jpeg" not in exts and "jpg" not in exts, (
        f"無損來源被轉成 JPEG 了：{exts}")


def test_no_caller_reenables_the_unsafe_image_option():
    """守住成因本身：`IMAGE_NONE` 是「不要動圖片」，去識別化不可以用。

    **判準走 AST 不走字串** —— 這支測試第一版用字串比對，結果被
    `redact_core.py` 裡**解釋這條規則的說明文字**判成違規（CLAUDE.md 記過
    同一個坑兩次：掃描器被它要檢查的那個名字騙到）。
    """
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    bad = []
    for name in ("router.py", "redact_core.py"):
        path = root / "app" / "tools" / "doc_deident" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # `fitz.PDF_REDACT_IMAGE_NONE` → Attribute；裸名 → Name
            if isinstance(node, ast.Attribute) and node.attr == "PDF_REDACT_IMAGE_NONE":
                bad.append(f"{name}:{node.lineno}")
            elif isinstance(node, ast.Name) and node.id == "PDF_REDACT_IMAGE_NONE":
                bad.append(f"{name}:{node.lineno}")
    assert not bad, f"又把圖片 redaction 關掉了（F01 的成因）：{bad}"


@pytest.mark.skipif(shutil.which("tesseract") is None,
                    reason="需要 tesseract 才能驗「抽出來的圖 OCR 不到個資」")
def test_the_extracted_image_cannot_be_ocred_back(tmp_path):
    """附加驗證：照稽核報告的做法，把產出的圖再 OCR 一次。"""
    cv2 = pytest.importorskip("cv2")
    src = _scanned_pdf(tmp_path)
    out = tmp_path / "redacted.pdf"
    _redact(src, out)
    doc = fitz.open(str(out))
    texts = []
    for i, info in enumerate(doc[0].get_images(full=True)):
        raw = (doc.extract_image(info[0]) or {}).get("image")
        if not raw:
            continue
        f = tmp_path / f"img{i}.png"
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        cv2.imwrite(str(f), arr)
        texts.append(subprocess.run(["tesseract", str(f), "-", "--psm", "6"],
                                    capture_output=True, text=True,
                                    timeout=180).stdout)
    doc.close()
    joined = "".join(texts).replace(" ", "")
    for frag in ("123-45-6789", "12345-6789", "123456789"):
        assert frag not in joined, f"抽出來的圖 OCR 還撈得到 {frag}：{joined!r}"
