"""加密紙本備份：放在公開 repo `paper-backup-offline/` 的離線還原程式。

主機不在了也要拿得到還原程式，所以 repo 裡固定放一份（說明怎麼用指令或網址下載）。
判準不是「有沒有記得重產」：用 `pack.json` 記的版本重產一次、逐位元組比對 ——
模組、樣板、文字任何一處改了而沒重產，這裡就紅。
"""
from __future__ import annotations

import hashlib
import json
import re

from tools import build_paper_backup_offline as B


def test_published_packs_match_a_fresh_build():
    bad = B.differences()
    assert not bad, f"repo 裡的離線還原程式過期了：{bad}；請跑 .venv/bin/python tools/build_paper_backup_offline.py"


def test_one_pack_per_ui_language_and_sums_cover_them():
    from app.core import ui_locale
    d = B.out_dir()
    names = [B.file_name(lang) for lang in ui_locale.SUPPORTED]
    sums = {}
    for line in (d / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    assert sorted(sums) == sorted(names)
    meta = json.loads((d / "pack.json").read_text(encoding="utf-8"))
    for n in names:
        data = (d / n).read_bytes()
        assert hashlib.sha256(data).hexdigest() == sums[n] == meta["files"][n]["sha256"], n
        assert f"JTDT {meta['version']}".encode() in data           # 頁面上寫的版本跟 pack.json 一致
    assert re.fullmatch(r"\d+\.\d+\.\d+", meta["version"])


def test_readme_names_and_urls_point_at_real_files():
    from app.core import ui_locale
    d = B.out_dir()
    text = (d / "README.md").read_text(encoding="utf-8")
    base = "https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/" + B.DIR_NAME
    assert text.count(f"base={base}\n") == 3 and text.count(f'$base = "{base}"') == 3
    assert "https://github.com/jasoncheng7115/jt-doc-tools/tree/main/" + B.DIR_NAME in text
    for name in set(re.findall(r"jtdt-paper-backup-offline-[A-Za-z-]+\.html", text)):
        assert (d / name).is_file(), name
    for lang in ui_locale.SUPPORTED:
        assert B.file_name(lang) in text, lang


def test_public_docs_link_to_the_folder_not_to_releases():
    from tools.repo_paths import public_root
    root = public_root()
    link = "https://github.com/jasoncheng7115/jt-doc-tools/tree/main/" + B.DIR_NAME
    for rel in ("README.md", "COMPLIANCE.md", "docs/index.html"):
        text = (root / rel).read_text(encoding="utf-8")
        assert link in text, rel
    readme = (root / "README.md").read_text(encoding="utf-8")
    section = readme.split("## 加密紙本備份", 1)[1].split("\n## ", 1)[0]
    assert "/releases" not in section
