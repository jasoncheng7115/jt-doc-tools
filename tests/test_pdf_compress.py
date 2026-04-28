"""Tests for the pdf-compress tool, focused on transparency preservation."""
from __future__ import annotations

import io

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.tools.pdf_compress.router import _image_downsample_and_recompress


def _pdf_with_transparent_png() -> bytes:
    """Build a PDF whose page contains a PNG that has an alpha channel.

    PyMuPDF stores the alpha as a separate SMask xref linked to the base
    image's xref. This is exactly the structure that broke v1.1.58 — the
    base xref's pixmap had `pix.alpha == 0`, the recompressor treated it
    as opaque, JPEG-encoded it, and transparent regions came out black.
    """
    # 64x64 fully transparent PNG
    from PIL import Image
    rgba = Image.new("RGBA", (64, 64), (255, 0, 0, 0))  # red with alpha=0
    # add a small opaque dot so the image isn't trivially skipped
    for y in range(20, 30):
        for x in range(20, 30):
            rgba.putpixel((x, y), (0, 255, 0, 255))
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_image(fitz.Rect(50, 50, 150, 150), stream=png_bytes)
    out = io.BytesIO()
    doc.save(out)
    doc.close()
    return out.getvalue()


@pytest.fixture
def client():
    return TestClient(app)


def test_transparent_image_preserved_after_compression():
    """Regression: PDF with a transparent PNG must keep alpha after compress."""
    pdf = _pdf_with_transparent_png()
    doc = fitz.open(stream=pdf, filetype="pdf")
    # locate the SMask-bearing image
    smask_xrefs = []
    for img in doc[0].get_images(full=True):
        info = doc.extract_image(img[0]) or {}
        if info.get("smask"):
            smask_xrefs.append(img[0])
    assert smask_xrefs, "test fixture should have an image with a soft mask"

    stats = _image_downsample_and_recompress(
        doc, max_dpi=150, jpeg_quality=80,
    )
    # The fix: SMask-bearing images are skipped, not silently flattened.
    assert stats.get("skipped_smask", 0) >= 1
    # Sanity: those images must NOT have been recompressed (which would
    # have lost their alpha channel).
    for xref in smask_xrefs:
        info_after = doc.extract_image(xref) or {}
        assert info_after.get("smask"), \
            f"xref {xref} lost its smask — transparency would be black"
    doc.close()


def test_analyze_endpoint_accepts_transparent_pdf(client):
    """The /analyze endpoint should not blow up on PDFs containing soft-mask images."""
    pdf = _pdf_with_transparent_png()
    r = client.post(
        "/tools/pdf-compress/analyze",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("upload_id")


def test_opaque_image_still_recompresses():
    """Sanity: opaque images are still subject to recompression."""
    from PIL import Image
    img = Image.new("RGB", (200, 200), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    doc = fitz.open()
    p = doc.new_page(width=300, height=300)
    p.insert_image(fitz.Rect(0, 0, 300, 300), stream=png)
    stats = _image_downsample_and_recompress(
        doc, max_dpi=72, jpeg_quality=60,
    )
    # No smask in this fixture
    assert stats.get("skipped_smask", 0) == 0
    # And we did recompress something
    assert stats.get("images_recompressed", 0) >= 1
    doc.close()
