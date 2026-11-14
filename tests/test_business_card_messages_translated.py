"""名片辨識伺服器端的訊息（API 錯誤、欄位「需確認」的原因）英文、日文介面都要有翻譯。

API 錯誤由 router 的 `_LocalizedRoute` 依介面語言翻（`i18n.translate_message`：「…」與數字換成 {0}…）；
欄位原因由畫面 `tr()` 翻。這裡從程式碼把每一句抓出來換成 key，要求語系檔都有 —— 之後新加的訊息漏翻會紅。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "app/tools/business_card_reader"
_CALLS = {"HTTPException", "InputError", "LimitError", "BusyError", "BookError", "AIError"}


def _key(parts) -> str:
    """f-string 的片段 → 語系檔的 key：「…」裡的變數 →「{n}」，其他變數 → {n}。"""
    out, n = "", 0
    for kind, v in parts:
        if kind == "s":
            out += v
        else:
            out += "{%d}" % n
            n += 1
    return out


def server_messages() -> set[str]:
    msgs = set()
    for f in [*PKG.glob("*.py"), ROOT / "app/admin/business_card_admin.py"]:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            cands = list(node.args) if name in _CALLS else []
            cands += [k.value for k in node.keywords if k.arg == "reason"]
            for a in cands:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    s = a.value
                elif isinstance(a, ast.JoinedStr):
                    s = _key([("s", v.value) if isinstance(v, ast.Constant) else ("v", None) for v in a.values])
                else:
                    continue
                if re.search(r"[一-鿿]", s):
                    msgs.add(s)
        # 欄位原因也常寫成變數（st, rs = ("check", "…")）：抓 extract.py 裡所有「請對照原圖 / 請確認」的字串
        if f.name in ("extract.py", "ai.py"):
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r"請對照原圖|請確認|請選一個|AI 建議", node.value):
                    msgs.add(node.value)
    from app.core.image_utils import HEIF_MISSING_HINT
    from app.tools.business_card_reader import importer
    msgs.add(HEIF_MISSING_HINT)
    msgs |= set(importer.MESSAGES)
    return msgs


def test_every_server_message_has_english_and_japanese():
    for lang in ("en", "ja"):
        cat = json.loads((ROOT / f"app/i18n/{lang}.json").read_text(encoding="utf-8"))
        missing = sorted(m for m in server_messages() if m not in cat)
        assert not missing, f"[{lang}] 名片辨識的伺服器訊息沒有翻譯：{missing}"


def test_translate_message_fills_quoted_names_and_numbers_back():
    from app.core import i18n
    cat = i18n.catalog("en")
    key = "「{0}」超過 {1} MB"
    assert key in cat
    out = i18n.translate_message("「範例 名片.jpg」超過 40 MB", "en")
    assert "範例 名片.jpg" in out and "40" in out and "超過" not in out
    assert i18n.translate_message("「範例.jpg」超過 40 MB", "zh-TW") == "「範例.jpg」超過 40 MB"
    assert i18n.translate_message("完全沒有這一句 42", "en") == "完全沒有這一句 42", "查不到就原樣"
    # 字裡的數字（Big5、UTF-8）不當成變數
    out = i18n.translate_message("有 3 個字 Big5 沒有（例如：「们」），存成 Big5 會變成問號；請改用 UTF-8", "en")
    assert "Big5" in out and "们" in out and "沒有" not in out


def test_api_errors_follow_the_interface_language(auth_off):
    from fastapi.testclient import TestClient
    from app import main as app_main
    from app.core import ui_locale
    with TestClient(app_main.app) as c:
        zh = c.get("/tools/business-card-reader/api/batches/" + "a" * 32)
        c.cookies.set(ui_locale.COOKIE_NAME, "en")
        en = c.get("/tools/business-card-reader/api/batches/" + "a" * 32)
        bad = c.post("/tools/business-card-reader/api/batches", data={"mode": "x"},
                     files=[("files", ("範例.jpg", b"not an image", "image/jpeg"))])
    assert zh.status_code == en.status_code == 404
    assert zh.json()["detail"] == "找不到這個名片批次"
    assert not re.search(r"[一-鿿]", en.json()["detail"]), en.json()
    assert bad.status_code == 400 and not re.search(r"[一-鿿]", bad.json()["detail"]), bad.json()
