"""AI 用印（MCP）的定位與便利功能（v1.17.54）：依文字定位、角落、頁碼字串、透明度、預覽、常用組合、批次。

驗的是：

* 位置計算（`pdf_stamp.ai_layout`）：`page` 的整數（0 起算）與字串（`all` / `first` / `last` / `2-5`，1 起算）、
  `anchor` 角落＋邊距＋偏移、`anchor_text` 找頁面上的字（左右上下、第幾個）、只給寬度照比例、夾在頁面裡、
  找不到字的頁跳過、每一頁都找不到才錯；寫錯的欄位講清楚是哪一項
* 經 MCP：落點回給 AI；`all` 展開後每一處都算今日上限；`dry_run` 不蓋、不扣用量、不建立待確認、
  預覽只畫框線（沒有章圖）；常用組合存 / 列 / 用 / 刪（每個人自己的、章要能用才存得進去）；
  好幾份檔一次蓋（各自一件、某一份失敗不影響其他）；`opacity` 真的讓章變淡
* 原本的條件一樣都不少：沒開 AI 授權的章、受管制的章，經組合或批次也一樣被拒

資料全部杜撰。
"""
from __future__ import annotations

import json
import math

import fitz
import pytest

from app.tools.pdf_stamp import ai_layout as L
from tests.test_ai_stamp import _set_ai, env  # noqa: F401  (fixture)
from tests.test_mcp_server import _call, _upload_inline

MM = 72 / 25.4
SEAL = "a" * 32


def _doc(pages=3, texts=None, rotate=0):
    """A4 文件；`texts`＝{頁: [(x_mm, y_mm, 字)]}。中文用 PyMuPDF 內建的繁中字型（抽得出文字）。"""
    d = fitz.open()
    for i in range(pages):
        p = d.new_page(width=595.28, height=841.89)
        for x, y, t in (texts or {}).get(i, []):
            p.insert_text((x * MM, y * MM), t, fontsize=12, fontname="china-t")
        if rotate:
            p.set_rotation(rotate)
    return d


def _pdf_bytes(**kw) -> bytes:
    d = _doc(**kw)
    b = d.tobytes()
    d.close()
    return b


PRESET = {SEAL: {"x_mm": 150, "y_mm": 250, "width_mm": 40, "height_mm": 20, "rotation_deg": 0}}


# --------------------------------------------------------------------------- 頁碼

def test_page_specs():
    assert L.parse_pages(0, 5) == [0] and L.parse_pages(4, 5) == [4]
    assert L.parse_pages("all", 3) == [0, 1, 2]
    assert L.parse_pages("first", 3) == [0] and L.parse_pages("last", 3) == [2]
    assert L.parse_pages("2-4", 5) == [1, 2, 3], "字串是 1 起算"
    assert L.parse_pages("1,3,4-5", 5) == [0, 2, 3, 4]
    assert L.parse_pages("3", 5) == [2]
    for bad, n in (("0", 5), ("4-2", 5), ("abc", 5), ("1-", 5)):
        with pytest.raises(L.LayoutError):
            L.parse_pages(bad, n)
    for bad, n in ((5, 5), ("6", 5), ("2-9", 5)):
        with pytest.raises(L.LayoutError) as ei:
            L.parse_pages(bad, n)
        assert ei.value.code == "NO_PAGE", "超出頁數要講出來，不默默略過"


@pytest.mark.parametrize("item, word", [
    ({"asset_id": SEAL, "x_mm": 1, "anchor": "center"}, "擇一"),
    ({"asset_id": SEAL, "anchor": "center", "anchor_text": "x"}, "擇一"),
    ({"asset_id": SEAL, "anchor": "middle"}, "anchor"),
    ({"asset_id": SEAL, "anchor_text": ""}, "anchor_text"),
    ({"asset_id": SEAL, "anchor_text": "x" * 51}, "anchor_text"),
    ({"asset_id": SEAL, "anchor_text": "負責人", "anchor_side": "behind"}, "anchor_side"),
    ({"asset_id": SEAL, "anchor_text": "負責人", "anchor_match": 0}, "anchor_match"),
    ({"asset_id": SEAL, "anchor_side": "right"}, "anchor_text"),
    ({"asset_id": SEAL, "offset_x_mm": 5}, "offset"),
    ({"asset_id": SEAL, "margin_mm": 5}, "margin_mm"),
    ({"asset_id": SEAL, "opacity": 0}, "opacity"),
    ({"asset_id": SEAL, "opacity": 1.5}, "opacity"),
    ({"asset_id": SEAL, "width_mm": float("nan")}, "NaN"),
    ({"asset_id": SEAL, "width_mm": 0}, "width_mm"),
    ({"asset_id": SEAL, "page": -1}, "page"),
    ({"asset_id": SEAL, "page": 1.5}, "page"),
    ({"asset_id": SEAL, "page": True}, "page"),
    ({"asset_id": SEAL, "page": "第二頁"}, "page"),
    ({"asset_id": SEAL, "png_b64": "x"}, "不收"),
    ({"asset_id": "x"}, "asset_id"),
])
def test_validation_names_the_problem(item, word):
    with pytest.raises(L.LayoutError) as ei:
        L.validate([item])
    assert word in ei.value.message


def test_validation_limits():
    with pytest.raises(L.LayoutError):
        L.validate([])
    with pytest.raises(L.LayoutError):
        L.validate([{"asset_id": SEAL}] * (L.MAX_ITEMS + 1))


# --------------------------------------------------------------------------- 位置

def _one(doc, item, preset=PRESET):
    placed, skipped = L.resolve(doc, L.validate([item]), preset)
    return placed, skipped


def test_anchor_corners_and_offsets():
    d = _doc(1)
    W, H = 595.28 / MM, 841.89 / MM
    (p,), _ = _one(d, {"asset_id": SEAL, "anchor": "bottom-right"})
    assert (p["x_mm"], p["y_mm"]) == (round(W - 15 - 40, 2), round(H - 15 - 20, 2))
    (p,), _ = _one(d, {"asset_id": SEAL, "anchor": "top-left", "margin_mm": 5, "offset_x_mm": 3, "offset_y_mm": 4})
    assert (p["x_mm"], p["y_mm"]) == (8, 9)
    (p,), _ = _one(d, {"asset_id": SEAL, "anchor": "center"})
    assert abs(p["x_mm"] - (W - 40) / 2) < 0.01 and abs(p["y_mm"] - (H - 20) / 2) < 0.01
    assert p["how"] == "anchor:center"


def test_width_only_keeps_the_seal_proportions():
    d = _doc(1)
    (p,), _ = _one(d, {"asset_id": SEAL, "width_mm": 20})
    assert (p["width_mm"], p["height_mm"]) == (20, 10), "40×20 的章給寬 20 → 高 10"
    (p,), _ = _one(d, {"asset_id": SEAL, "height_mm": 30})
    assert (p["width_mm"], p["height_mm"]) == (60, 30)
    (p,), _ = _one(d, {"asset_id": SEAL})
    assert (p["x_mm"], p["y_mm"], p["width_mm"], p["height_mm"], p["how"]) == (150, 250, 40, 20, "default")


def test_anchor_text_sides():
    d = _doc(1, texts={0: [(30, 200, "負責人")]})
    hit = d[0].search_for("負責人")[0]
    x0, y0, x1, y1 = (v / MM for v in hit)
    cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
    (p,), _ = _one(d, {"asset_id": SEAL, "anchor_text": "負責人", "width_mm": 20})
    assert abs(p["x_mm"] - (x1 + 2)) < 0.05 and abs(p["y_mm"] + p["height_mm"] / 2 - cy) < 0.05, "預設蓋在右邊、垂直置中"
    assert p["how"] == "anchor_text:right"
    (p,), _ = _one(d, {"asset_id": SEAL, "anchor_text": "負責人", "anchor_side": "above", "gap_mm": 1, "width_mm": 20})
    assert abs(p["y_mm"] + p["height_mm"] - (y0 - 1)) < 0.05 and abs(p["x_mm"] + 10 - cx) < 0.05
    (p,), _ = _one(d, {"asset_id": SEAL, "anchor_text": "負責人", "anchor_side": "over", "width_mm": 20})
    assert abs(p["x_mm"] + 10 - cx) < 0.05 and abs(p["y_mm"] + 5 - cy) < 0.05


def test_anchor_text_match_and_missing_pages():
    d = _doc(3, texts={1: [(30, 100, "立書人"), (30, 200, "立書人")]})
    placed, skipped = _one(d, {"asset_id": SEAL, "page": "all", "anchor_text": "立書人", "anchor_match": "last"})
    assert [p["page"] for p in placed] == [1] and skipped == [{"item": 0, "page": 0}, {"item": 0, "page": 2}]
    first = d[1].search_for("立書人")
    assert placed[0]["y_mm"] > first[0].y1 / MM, "last＝最下面那一個"
    placed, _ = _one(d, {"asset_id": SEAL, "page": 1, "anchor_text": "立書人", "anchor_match": 1})
    assert placed[0]["y_mm"] < 150
    with pytest.raises(L.LayoutError) as ei:
        _one(d, {"asset_id": SEAL, "page": "all", "anchor_text": "公司章"})
    assert ei.value.code == "ANCHOR_NOT_FOUND" and "公司章" in ei.value.message


def test_anchor_text_with_spaces_between_characters():
    d = _doc(1, texts={0: [(30, 100, "負責人")]})
    placed, _ = _one(d, {"asset_id": SEAL, "anchor_text": "負 責 人"})
    assert placed, "AI 多打了空白也找得到"


def test_clamped_inside_the_page():
    d = _doc(1, texts={0: [(180, 100, "負責人")]})
    (p,), _ = _one(d, {"asset_id": SEAL, "anchor_text": "負責人", "width_mm": 40})
    W = 595.28 / MM
    assert p["clamped"] and p["x_mm"] + p["width_mm"] <= W + 0.01, "超出右邊時夾回頁面裡"


def test_rotated_page_uses_displayed_coordinates():
    d = _doc(1, texts={0: [(30, 100, "負責人")]}, rotate=90)
    (p,), _ = _one(d, {"asset_id": SEAL, "anchor_text": "負責人", "width_mm": 20})
    W, H = d[0].rect.width / MM, d[0].rect.height / MM
    assert W > H, "旋轉 90 度的 A4 顯示成橫的"
    hit = d[0].search_for("負責人")[0] * d[0].rotation_matrix
    assert abs(p["x_mm"] - (hit.x1 / MM + 2)) < 0.05, "位置以顯示的頁面算（跟網頁用印工具一致）"


def test_rotated_page_stamp_lands_next_to_the_text(tmp_path):
    """真的蓋下去：旋轉 90 度的頁，章在畫面上要在「負責人」的右邊（不是跑到別的地方）。"""
    from PIL import Image
    from app.core import pdf_utils
    src, dst, png = tmp_path / "r.pdf", tmp_path / "o.pdf", tmp_path / "s.png"
    src.write_bytes(_pdf_bytes(pages=1, texts={0: [(30, 100, "負責人")]}, rotate=90))
    Image.new("RGBA", (40, 20), (200, 20, 20, 255)).save(png)
    with fitz.open(str(src)) as d:
        (p,), _ = _one(d, {"asset_id": SEAL, "anchor_text": "負責人", "width_mm": 20})
    pdf_utils.stamp_pdf(src, dst, png, p["x_mm"], p["y_mm"], p["width_mm"], p["height_mm"], pages=[0])
    with fitz.open(str(dst)) as d:
        page = d[0]
        img = page.get_image_rects(page.get_images()[0][0])[0] * page.rotation_matrix
        txt = page.search_for("負責人")[0] * page.rotation_matrix
    assert abs(img.x0 - (txt.x1 + 2 * MM)) < 1.5, (img, txt)
    assert img.y0 < (txt.y0 + txt.y1) / 2 < img.y1, "垂直方向蓋在那行字上"


def test_expansion_cap():
    # 5 頁 × 41 項「all」＝ 205 處，超過 200
    with pytest.raises(L.LayoutError) as ei:
        L.resolve(_doc(5), L.validate([{"asset_id": SEAL, "page": "all"}] * 41), PRESET)
    assert "200" in ei.value.message


def test_preview_draws_outlines_not_seals(tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(_pdf_bytes(pages=2))
    before = src.read_bytes()
    placed, _ = L.resolve(fitz.open(str(src)), L.validate([{"asset_id": SEAL, "page": "last"}]), PRESET)
    png, pg = L.preview_png(src, placed, {SEAL: "測試章"})
    assert png.startswith(b"\x89PNG") and pg == 1
    assert src.read_bytes() == before, "預覽不改原檔"
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png)).convert("RGB")
    sx = img.width / (595.28 / MM)
    px = img.getpixel((int((150 + 1) * sx), int((250 + 10) * sx)))
    assert px[0] > px[1] + 20, "框裡有淡紅色的底（畫的是框，不是章圖）"


# --------------------------------------------------------------------------- 經 MCP

def _run(e, params, files):
    return _call(e["c"], e["tok"], "jtdt_run", {"tool": "pdf_stamp", "files": files, "params": params})


def _ws_path(e, fid):
    from app.core import workspace as ws
    from app.mcp import registry, runner
    return runner.resolve_inputs(registry.BY_NAME["pdf_compress"], [fid], ws.key_for_user_id(e["uid"]))[0]["path"]


def test_anchor_text_through_mcp_returns_the_placements(env):  # noqa: F811
    e = env
    fid = _upload_inline(e["c"], e["tok"], _pdf_bytes(pages=2, texts={1: [(30, 200, "負責人")]}), "證明書.pdf")
    err, res = _run(e, {"placements_json": json.dumps([{"asset_id": e["seal"].id, "page": "last",
                                                         "anchor_text": "負責人", "width_mm": 20}])}, [fid])
    assert not err and res["status"] == "done", res
    (p,) = res["placements"]
    assert p["page"] == 1 and p["page_number"] == 2 and p["how"] == "anchor_text:right" and p["width_mm"] == 20
    with fitz.open(_ws_path(e, res["files"][0]["file_id"])) as d:
        assert not d[0].get_images() and d[1].get_images()


def test_all_pages_count_each_placement(env):  # noqa: F811
    from app.core import ai_stamp
    e = env
    fid = _upload_inline(e["c"], e["tok"], _pdf_bytes(pages=3), "c.pdf")
    err, res = _run(e, {"placements_json": json.dumps([{"asset_id": e["seal"].id, "page": "all",
                                                         "anchor": "bottom-right", "width_mm": 15}])}, [fid])
    assert not err and res["status"] == "done", res
    assert [p["page"] for p in res["placements"]] == [0, 1, 2]
    assert ai_stamp.used_today(e["uid"], e["seal"].id) == 3, "今日上限算的是展開後的每一處"
    _set_ai(e, confirm=False, daily_limit=4, subjects=[{"type": "user", "key": str(e["uid"])}], enabled=True)
    err, res = _run(e, {"placements_json": json.dumps([{"asset_id": e["seal"].id, "page": "all"}])}, [fid])
    assert err and "上限" in res, "剩 1 次，3 頁全蓋要被擋"


def test_dry_run_neither_stamps_nor_counts(env):  # noqa: F811
    from app.core import ai_stamp
    e = env
    _set_ai(e, confirm=True, subjects=[{"type": "user", "key": str(e["uid"])}], enabled=True)
    fid = _upload_inline(e["c"], e["tok"], _pdf_bytes(pages=2, texts={0: [(30, 200, "立書人")]}), "d.pdf")
    err, res = _run(e, {"placements_json": json.dumps([{"asset_id": e["seal"].id, "anchor_text": "立書人"}]),
                        "dry_run": True}, [fid])
    assert not err and res["status"] == "dry_run", res
    assert res["placements"][0]["how"] == "anchor_text:right" and len(res["preview_files"]) == 1
    assert ai_stamp.used_today(e["uid"], e["seal"].id) == 0, "預覽不扣用量"
    assert ai_stamp.list_all(user_id=e["uid"]) == [], "預覽不建立用印紀錄、不等確認"
    assert res["preview_files"][0]["name"].endswith(".png")
    # 預覽也一樣要能用那顆章
    _set_ai(e, enabled=False)
    err, res = _run(e, {"placements_json": json.dumps([{"asset_id": e["seal"].id}]), "dry_run": True}, [fid])
    assert err and "ASSET_NOT_ALLOWED" in res


def test_presets_save_list_use_delete(env):  # noqa: F811
    from app.core import ai_stamp
    e = env
    items = [{"asset_id": e["seal"].id, "page": "last", "anchor": "bottom-right", "width_mm": 20}]
    err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_preset_save",
                                                     "params": {"name": "供貨證明", "placements": json.dumps(items)}})
    assert not err and res["result"]["name"] == "供貨證明", res
    err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_preset_list"})
    assert not err and [p["name"] for p in res["result"]["presets"]] == ["供貨證明"]
    fid = _upload_inline(e["c"], e["tok"], _pdf_bytes(pages=2), "p.pdf")
    err, res = _run(e, {"preset": "供貨證明"}, [fid])
    assert not err and res["status"] == "done" and res["placements"][0]["page"] == 1, res
    # 別人看不到我的組合
    assert ai_stamp.list_presets(e["other"]) == []
    # 不能用的章存不進去；名稱要合法；兩個參數擇一
    err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_preset_save", "params": {
        "name": "簽名", "placements": json.dumps([{"asset_id": e["sig"].id}])}})
    assert err and "ASSET_NOT_ALLOWED" in res
    err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_preset_save", "params": {
        "name": "<b>", "placements": json.dumps(items)}})
    assert err and "PRESET_NAME" in res
    err, res = _run(e, {"preset": "供貨證明", "placements_json": json.dumps(items)}, [fid])
    assert err and "擇一" in res
    err, res = _run(e, {"preset": "沒有這個"}, [fid])
    assert err and "PRESET_NOT_FOUND" in res
    # 組合裡的章後來被收回 AI 授權：用的時候照樣被擋
    _set_ai(e, enabled=False)
    err, res = _run(e, {"preset": "供貨證明"}, [fid])
    assert err and "ASSET_NOT_ALLOWED" in res
    _set_ai(e, enabled=True, subjects=[{"type": "user", "key": str(e["uid"])}], confirm=False)
    err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_preset_delete", "params": {"name": "供貨證明"}})
    assert not err and res["result"] == {"ok": True}
    assert ai_stamp.list_presets(e["uid"]) == []


def test_presets_on_the_web_page(env):  # noqa: F811
    from app.core import ai_stamp, sessions
    e = env
    ai_stamp.save_preset(e["uid"], "回簽", [{"asset_id": e["seal"].id, "page": 0, "anchor_text": "負責人"}])
    tok, _ = sessions.issue(e["uid"], remember=False, ip="127.0.0.1", ua="pytest")
    c = e["c"]
    c.cookies.set(sessions.COOKIE_NAME, tok)
    try:
        r = c.get("/ai-stamp/api/presets")
        assert r.status_code == 200 and r.json()["presets"][0]["name"] == "回簽"
        assert r.json()["names"][e["seal"].id] == e["seal"].name
        assert c.get("/ai-stamp").status_code == 200
        # 帶 Token 的（AI）不能用網頁的刪除
        r = c.post("/ai-stamp/api/presets/delete", json={"name": "回簽"},
                   headers={"Authorization": f"Bearer {e['tok']}"})
        assert r.status_code == 403
        r = c.post("/ai-stamp/api/presets/delete", json={"name": "回簽"})
        assert r.status_code == 200 and ai_stamp.list_presets(e["uid"]) == []
    finally:
        c.cookies.clear()


def test_batch_stamps_each_file_separately(env):  # noqa: F811
    from app.core import ai_stamp
    e = env
    f1 = _upload_inline(e["c"], e["tok"], _pdf_bytes(pages=1), "一.pdf")
    f2 = _upload_inline(e["c"], e["tok"], _pdf_bytes(pages=1), "二.pdf")
    place = json.dumps([{"asset_id": e["seal"].id, "anchor": "bottom-right"}])
    err, res = _run(e, {"placements_json": place}, [f1, f2])
    assert not err and res["batch"] and [r["status"] for r in res["results"]] == ["done", "done"], res
    assert len(res["files"]) == 2 and {r["source"] for r in res["results"]} == {"一.pdf", "二.pdf"}
    assert len(ai_stamp.list_all(user_id=e["uid"])) == 2, "每一份是獨立的一件"
    _set_ai(e, confirm=False, daily_limit=3, subjects=[{"type": "user", "key": str(e["uid"])}], enabled=True)
    err, res = _run(e, {"placements_json": place}, [f1, f2])
    assert not err and [r["status"] for r in res["results"]] == ["done", "error"], "上限用完：已蓋好的不受影響"


def test_opacity_makes_the_seal_lighter(env):  # noqa: F811
    e = env
    fid = _upload_inline(e["c"], e["tok"], _pdf_bytes(pages=1), "o.pdf")
    err, res = _run(e, {"placements_json": json.dumps([{"asset_id": e["seal"].id, "opacity": 0.4}])}, [fid])
    assert not err, res
    with fitz.open(_ws_path(e, res["files"][0]["file_id"])) as d:
        xref = d[0].get_images()[0][0]
        smask = d.extract_image(xref).get("smask")
        assert smask, "淡化之後要有透明度遮罩"
        alpha = fitz.Pixmap(d, smask)
        assert max(alpha.samples) <= math.ceil(255 * 0.4) + 1


def test_office_files_are_converted_first(env):  # noqa: F811
    from app.core import office_convert
    if not office_convert.find_soffice():
        pytest.skip("沒有 Office 引擎")
    import io

    import docx
    e = env
    d = docx.Document()
    d.add_paragraph("供貨證明")
    d.add_paragraph("立書人：範例科技股份有限公司")
    buf = io.BytesIO()
    d.save(buf)
    fid = _upload_inline(e["c"], e["tok"], buf.getvalue(), "證明書.docx")
    err, res = _run(e, {"placements_json": json.dumps([{"asset_id": e["seal"].id, "anchor_text": "立書人",
                                                         "width_mm": 30}])}, [fid])
    assert not err and res["status"] == "done", res
    assert res["files"][0]["name"].endswith(".pdf") and res["placements"][0]["how"] == "anchor_text:right"


def test_presets_migration_keeps_existing_records(tmp_path, monkeypatch):
    """`_m2_presets`（§1.98）：只新增常用組合的表，第 1 版的用印紀錄與用量都還在。"""
    import time as _t

    from app.config import settings
    from app.core import ai_stamp, db
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    p = ai_stamp.db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    db.migrate(p, ai_stamp._MIGRATIONS[:1])
    c = db.get_conn(p)
    with c:
        c.execute("INSERT INTO ai_stamp_requests(id, created_at, user_id, tool, status) VALUES (?,?,?,?,?)",
                  ("b" * 32, _t.time(), 7, "pdf-stamp", "done"))
        c.execute("INSERT INTO ai_stamp_usage(user_id, asset_id, day, count) VALUES (7, ?, '2026-11-24', 3)", ("c" * 32,))
    ai_stamp._INITED.discard(str(p))
    conn = ai_stamp.conn()
    assert conn.execute("SELECT count(*) FROM ai_stamp_requests").fetchone()[0] == 1
    assert conn.execute("SELECT count FROM ai_stamp_usage").fetchone()[0] == 3
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(ai_stamp._MIGRATIONS)
    ai_stamp.save_preset(7, "回簽", [{"asset_id": "c" * 32, "page": 0}])
    assert [x["name"] for x in ai_stamp.list_presets(7)] == ["回簽"]
