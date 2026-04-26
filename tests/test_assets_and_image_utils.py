"""Asset upload + crop + match-aspect + remove-bg auto-crop."""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image


def _png_bytes(w: int, h: int, fill=(255, 0, 0, 255)) -> bytes:
    im = Image.new("RGBA", (w, h), fill)
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_upload_and_crop_and_match_aspect(client):
    # Upload a 200x100 PNG as a stamp asset
    png = _png_bytes(200, 100)
    r = client.post(
        "/admin/assets/upload",
        data={"name": "_test_stamp", "type": "stamp", "remove_bg": "false"},
        files={"file": ("t.png", png, "image/png")},
        follow_redirects=False,
    )
    assert r.status_code in (200, 303)
    # Find this asset id
    r = client.get("/admin/api/assets")
    ids = [a["id"] for a in r.json()["assets"] if a["name"] == "_test_stamp"]
    assert ids, "asset not created"
    asset_id = ids[0]

    try:
        # Match-aspect endpoint should return preset matching 2:1 ratio
        r = client.post(f"/admin/assets/{asset_id}/match-aspect")
        assert r.status_code == 200
        preset = r.json()["asset"]["preset"]
        assert abs((preset["width_mm"] / preset["height_mm"]) - 2.0) < 0.05

        # Crop the right half (x=0.5, w=0.5, full height) → resulting image
        # becomes 100x100 → preset aspect should land on ~1:1.
        r = client.post(
            f"/admin/assets/{asset_id}/crop",
            json={"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0},
        )
        assert r.status_code == 200
        preset = r.json()["asset"]["preset"]
        assert abs((preset["width_mm"] / preset["height_mm"]) - 1.0) < 0.1
    finally:
        client.post(f"/admin/assets/{asset_id}/delete")


def test_remove_white_background_auto_crops_canvas():
    """Drawing a red square on a 400x400 white canvas, then remove-bg with
    auto-crop, should yield an image close to the inked region size."""
    from app.core.image_utils import remove_white_background
    import tempfile, pathlib
    from PIL import ImageDraw

    im = Image.new("RGB", (400, 400), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((150, 150, 250, 250), fill=(0, 0, 0))   # 100x100 black square
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        im.save(f, format="PNG"); src = pathlib.Path(f.name)
    dst = pathlib.Path(str(src) + ".out.png")
    try:
        remove_white_background(src, dst)
        with Image.open(dst) as out:
            w, h = out.size
        # The cropped canvas should be much smaller than 400 — within ~10%
        # of the 100x100 inked region (with the small `pad` margin).
        assert 90 <= w <= 130
        assert 90 <= h <= 130
    finally:
        src.unlink(missing_ok=True); dst.unlink(missing_ok=True)
