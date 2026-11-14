"""名片內容不可以公開（使用者 2026-11-14：「那都是真實資料，這是鐵則」）。

名片辨識的真實樣本（開發機上的 `temp_pdfs/business_cards/`，不進 git）是真人的名片。測試、更新記錄、文件、
commit 訊息一律用杜撰的值。這裡驗三件事：公開樹沒有樣本上的字、檢查本身有牙齒、名片相關的檔只用杜撰的號碼與網域。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_card_content as CC  # noqa: E402

PUBLIC = [ROOT / d for d in ("app", "tests", "tools", "static", "github") if (ROOT / d).is_dir()]

#: 測試與文件裡可以用的杜撰統編（有效檢查碼、不在任何真實樣本上）
FICTIONAL_VAT = {"04595257", "13579004"}


def test_public_tree_has_no_real_card_content():
    tok = CC.load_tokens()
    if not any(tok.values()):
        pytest.skip("沒有名片樣本（公開樹 clone 下來就是這樣）")
    res = CC.scan_paths(PUBLIC, tok)
    # 不把命中的字印進測試輸出（CI 日誌也是公開的）：只列檔名與處數
    assert not res, "這些檔案有跟真實名片相同的內容：" + "；".join(f"{f}（{len(h)} 處）" for f, h in res.items())


def test_the_scanner_has_teeth(tmp_path):
    """反向對照：樣本上的號碼、中文、網域、Email、檔名裡的英文名都要抓得到，換了分隔號也一樣。"""
    s = tmp_path / "samples"
    (s / "_eval").mkdir(parents=True)
    (s / "commons").mkdir()
    (s / "commons" / "Zyx_Qwerty_Sample_card.jpg").write_bytes(b"")
    (s / "_eval" / "easyocr_words.json").write_text(
        '{"a": {"words": [{"text": "電話：(02)2468-1357"}, {"text": "虛構測試科技公司"}, '
        '{"text": "www.zyxqw-test.com.tw"}, {"text": "zq@zyxqw-test.com.tw"}, {"text": "統一編號"}, '
        '{"text": "E-Mailqwer.tyuiop"}, {"text": "@zz-sample.de"}, {"text": "Qwertz Yuiopa Technic"}, '
        '{"text": "Sales Manager"}]}}', encoding="utf-8")
    tok = CC.load_tokens(s)
    for text in ("聯絡 02-2468-1357", "虛構測試科技公司", "見 WWW.ZYXQW-TEST.COM.TW", "zq@zyxqw-test.com.tw",
                 "Zyx Qwerty Sample",
                 "註解裡寫了「…qwer.tyuiop」",            # 被拆成兩段的 Email 帳號，就算後面沒有 @ 也算
                 "qwertz yuiopa",                      # 公司名寫成小寫
                 "另一個網址 www.zyxqw-test.net"):        # 網域裡有辨識度的那一段
        assert CC.scan_text(text, tok), f"沒抓到：{text}"
    for text in ("統一編號：04595257", "www.example.com", "電話 02-2345-6789", "2026/05/30", "Sales Manager",
                 "kevin.sample@example.com"):
        assert not CC.scan_text(text, tok), f"誤報：{text}"


def test_commit_messages_are_checked_too(tmp_path, monkeypatch):
    import check_commit_message as CM
    fake = {"num": {"24681357"}, "cjk": set(), "lat": set()}
    monkeypatch.setattr(CC, "load_tokens", lambda *a, **k: fake)
    assert CM.check("v1.17.41：修正 02-2468-1357 被當成統編")
    assert not CM.check("v1.17.41：統編不再被當成電話")


def test_business_card_files_use_only_fictional_values():
    """名片相關的測試與文件：統編只用杜撰的、Email 與網址只用 example 網域。"""
    files = [*ROOT.glob("tests/test_business_card*.py"), ROOT / "tests/bc_synth.py",
             *(ROOT / "app/tools/business_card_reader").rglob("*.py"),
             *(ROOT / "app/tools/business_card_reader").rglob("*.html")]
    for gh in ("github/CHANGELOG.md", "github/CHANGELOG_en.md", "github/CHANGELOG_ja.md", "github/API.md"):
        if (ROOT / gh).is_file():
            files.append(ROOT / gh)
    from app.tools.business_card_reader.extract import vat_checksum_ok
    for f in files:
        text = f.read_text(encoding="utf-8")
        if f.name.startswith("CHANGELOG") or f.name == "API.md":
            # 只看名片辨識那一段
            text = "\n".join(seg for seg in re.split(r"\n(?=## |### )", text) if "名片" in seg or "business card" in seg.lower()
                             or "名刺" in seg)
        for m in re.finditer(r"(?<!\d)(\d{8})(?!\d)", text):
            n = m.group(1)
            near = text[max(0, m.start() - 30):m.start()]
            if vat_checksum_ok(n) and re.search(r"統|VAT|Tax|vat", near):
                assert n in FICTIONAL_VAT, f"{f.name}：統編 {n} 不在杜撰清單裡"
        for m in re.finditer(r"[\w.+\-]+@([\w\-]+(?:\.[\w\-]+)+)", text):
            dom = m.group(1).lower()
            fictional = (dom.startswith("example.") or re.search(r"(^|\.)example\.(com|org|net)$", dom)
                         or dom.rsplit(".", 1)[-1] in ("example", "test", "invalid"))
            assert fictional, f"{f.name}：Email 網域 {dom} 不是保留給範例用的網域"
