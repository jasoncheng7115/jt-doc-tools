"""Admin API regression tests."""
from __future__ import annotations


def test_conv_settings_save_and_read(client):
    """Save → reload → restore. Backup the file so this test doesn't wipe
    the user's actual saved order."""
    from pathlib import Path
    import json
    p = Path("data/office_paths.json")
    backup = p.read_text(encoding="utf-8") if p.exists() else None
    try:
        r = client.get("/admin/conversion")
        assert r.status_code == 200
        r = client.post("/admin/conversion/save", json={
            "builtin_order": ["macos-libreoffice", "macos-oxoffice"],
            "custom": ["/usr/local/custom/soffice"],
        })
        assert r.status_code == 200
        r = client.get("/admin/conversion")
        assert "/usr/local/custom/soffice" in r.text
    finally:
        if backup is not None:
            p.write_text(backup, encoding="utf-8")
        elif p.exists():
            p.unlink()


def test_profile_create_activate_delete(client):
    r = client.post("/admin/profile/create", json={"name": "_TestCo"})
    assert r.status_code == 200
    cid = r.json()["id"]
    # Activate
    r = client.post(f"/admin/profile/{cid}/activate")
    assert r.status_code == 200
    # And the pdf-fill page accepts the new cid (regression for the
    # Optional[str] forward-ref bug).
    r = client.get(f"/tools/pdf-fill/?cid={cid}")
    assert r.status_code == 200
    # Delete
    r = client.post(f"/admin/profile/{cid}/delete")
    assert r.status_code == 200


def test_synonyms_save_round_trip(client):
    """Save → reload → restore. Backup the user's synonyms file so this test
    doesn't permanently overwrite the (large) production map with a 1-key
    stub."""
    from app.core.synonym_manager import synonym_manager
    backup = synonym_manager.get_map()
    try:
        r = client.post("/admin/synonyms/save", json={
            "rows": [
                {"key": "company_name", "synonyms": ["公司全名", "廠商名稱", "Company Name"]},
            ]
        })
        assert r.status_code == 200
        r = client.get("/admin/synonyms")
        assert r.status_code == 200
    finally:
        synonym_manager.save_map(backup)


def test_asset_upload_rejects_unsupported_type(client):
    """A non-image / non-PDF file returns a friendly 400, not a 500 deep in
    PIL (regression guard for the asset upload)."""
    junk = b"PK\x03\x04 this is a zip, not an image or pdf"
    r = client.post(
        "/admin/assets/upload",
        data={"name": "x", "type": "stamp", "remove_bg": "false"},
        files={"file": ("x.png", junk, "image/png")},
    )
    assert r.status_code == 400


def test_asset_upload_pdf_renders_to_image(client):
    """A PDF asset is auto-rendered (first page) to PNG and accepted."""
    import fitz
    doc = fitz.open(); doc.new_page(width=120, height=120)
    pdf = doc.tobytes(); doc.close()
    r = client.post(
        "/admin/assets/upload",
        data={"name": "_pdfstamp", "type": "stamp", "remove_bg": "false"},
        files={"file": ("stamp.pdf", pdf, "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code in (303, 302, 200)


def test_asset_upload_accepts_png(client):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buf, format="PNG")
    r = client.post(
        "/admin/assets/upload",
        data={"name": "_pngtest", "type": "stamp", "remove_bg": "false"},
        files={"file": ("ok.png", buf.getvalue(), "image/png")},
        follow_redirects=False,
    )
    assert r.status_code in (303, 302)  # redirect to edit page on success
