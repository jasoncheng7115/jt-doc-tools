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
