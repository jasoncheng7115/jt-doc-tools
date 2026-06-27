"""資產縮圖必須載入得到 — 防「import 後 file_key/thumb_key 與磁碟檔名不一致
→ /assets/{id}/thumb 404 → 印章/簽名 picker 破圖」回歸（2026-06-27 客戶回報）。

根因：admin 資產匯入把檔案以 `{new_id}.png` 寫入磁碟,但登錄 `new_meta=dict(a)`
沿用了原始的 file_key/thumb_key（不同 uuid）→ 縮圖路由用 file_key 找檔 → 404。

修法：① import 同步 file_key/thumb_key = {new_id}.png；② asset_manager
file_path/thumb_path 找不到時退回 `{id}.png`（防舊資料殘留）。
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image


def _png(w=120, h=60, fill=(0, 0, 200, 255)) -> bytes:
    buf = BytesIO()
    Image.new("RGBA", (w, h), fill).save(buf, format="PNG")
    return buf.getvalue()


def _upload(client, name, typ="signature"):
    r = client.post(
        "/admin/assets/upload",
        data={"name": name, "type": typ, "remove_bg": "false"},
        files={"file": ("s.png", _png(), "image/png")},
        follow_redirects=False,
    )
    assert r.status_code in (200, 303), r.text
    aid = [a["id"] for a in client.get("/admin/api/assets").json()["assets"]
           if a["name"] == name]
    assert aid, "asset not created"
    return aid[0]


def test_every_asset_thumb_and_file_resolve(client):
    """所有已登錄資產的 /assets/{id}/thumb 與 /file 都要 200（不可破圖）。"""
    _upload(client, "_t_sig_resolve", "signature")
    assets = client.get("/admin/api/assets").json()["assets"]
    assert assets
    for a in assets:
        aid = a["id"]
        rt = client.get(f"/assets/{aid}/thumb")
        rf = client.get(f"/assets/{aid}/file")
        assert rt.status_code == 200, f"{a['name']} thumb 破圖 → {rt.status_code}"
        assert rf.status_code == 200, f"{a['name']} file 破圖 → {rf.status_code}"


def test_import_merge_keeps_thumbnails_loadable(client):
    """匯出→合併匯入（會重新分配 id）後,新資產縮圖仍載入得到（核心 bug 場景）。"""
    _upload(client, "_t_imp_sig", "signature")
    # 匯出 ZIP
    exp = client.get("/admin/assets/export")
    assert exp.status_code == 200
    zip_bytes = exp.content
    # 合併匯入（id 撞既有 → 重新分配 new_id,檔案以 new_id 寫入）
    imp = client.post(
        "/admin/assets/import",
        data={"mode": "merge"},
        files={"file": ("assets.zip", zip_bytes, "application/zip")},
        follow_redirects=False,
    )
    assert imp.status_code == 200, imp.text
    # 匯入後每個資產的 file_key 都要 = {id}.png 且縮圖端點 200
    for a in client.get("/admin/api/assets").json()["assets"]:
        aid = a["id"]
        rt = client.get(f"/assets/{aid}/thumb")
        assert rt.status_code == 200, f"匯入後 {a['name']} 縮圖 404 破圖"


def test_file_path_falls_back_to_id_when_key_stale(tmp_path, monkeypatch):
    """file_key/thumb_key 指向不存在的檔時,退回 {id}.png（防舊殘留資料）。"""
    from app.core import asset_manager as am

    mgr = am.AssetManager()
    files_dir = mgr._files_dir
    # 寫入以 id 命名的實體檔,但 Asset 的 key 指向不同 uuid（模擬舊 import 殘留）
    aid = "deadbeefdeadbeefdeadbeefdeadbeef"
    (files_dir / f"{aid}.png").write_bytes(_png())
    (files_dir / f"{aid}_thumb.png").write_bytes(_png(40, 20))
    asset = am.Asset(id=aid, name="x", type="signature",
                     file_key="some-other-uuid.png",
                     thumb_key="some-other-uuid_thumb.png")
    assert mgr.file_path(asset).exists()
    assert mgr.thumb_path(asset).exists()
    assert mgr.file_path(asset).name == f"{aid}.png"
