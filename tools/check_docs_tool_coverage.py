#!/usr/bin/env python3
"""發版前檢查：文件是否涵蓋所有工具，以及「需 Office 引擎」標記是否正確。

為什麼需要這支：加新工具時要同步的文件位置很分散（README 功能清單、介紹站
feat-list、工具總數、需 Office 引擎的扳手標記…），**靠記得一定會漏**。實際發生過：

* `PDF 轉 Markdown`、`Markdown 轉文書` 從 v1.11.30 / v1.11.31 加入後，README 與
  介紹站**都沒有它們**，使用者從文件上根本不知道有這兩個工具（過了很久才發現）。
* 「需 Office 引擎」的扳手標記漏了 4 個工具（多頁合併 / 文字去識別化 / 逐句翻譯 /
  Markdown 轉文書），因為只看 `convert_to_pdf` 這類函式名，漏了 `convert_to_text`。

用法：
    python tools/check_docs_tool_coverage.py          # 有問題回非 0
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
from tools.repo_paths import public_root as _public_root

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INDEX_HTML = _public_root(ROOT) / "docs" / "index.html"
README = _public_root(ROOT) / "README.md"

# 文件為了版面精簡，常把數個工具寫成一列（「擷取文字 / 圖片 / 附件」）或用不同
# 措辭（工具叫「PDF 密碼保護」，README 寫「PDF 加密 / 解密」）。這裡列出這些工具
# 可接受的替代字串 —— **只在措辭不同時才加**；新工具沒寫進文件仍會被抓出來。
ALIASES = {
    "pdf-decrypt":            ["密碼保護 / 解除", "加密 / 解密", "解除"],
    "pdf-encrypt":            ["密碼保護", "加密"],
    "pdf-pageno":             ["頁碼"],
    "pdf-rotate":             ["頁面轉向", "旋轉"],
    "pdf-pages":              ["頁面整理"],
    "pdf-annotations-flatten": ["平面化"],
    "pdf-annotations-strip":  ["註解整理 / 清除", "清除"],
    "pdf-attachments":        ["附件"],
    "pdf-extract-images":     ["擷取文字 / 圖片", "擷取圖片"],
    "pdf-compress":           ["壓縮"],
    "pdf-editor":             ["編輯器"],
    "pdf-to-image":           ["辦公文件轉 PDF / 圖片", "辦公文件轉圖片"],
    "pdf-split":              ["頁面分拆", "分拆"],
    "pdf-merge":              ["檔案合併"],
    "pdf-nup":                ["多頁合併"],
}


def load_tools() -> list:
    from app.tool_registry import discover_tools
    return discover_tools()


#: 程式裡用得到 office_convert、但**一般使用不需要** Office 引擎的工具（介紹站不標扳手）。每一項都要寫理由；
#: 「會不會起 soffice」仍以程式為準（`concurrency_settings.OFFICE_TOOL_IDS` 照樣要列）。
OFFICE_OPTIONAL = {
    "pdf-stamp": "只有 AI 用印（MCP）收到文書檔時才先轉 PDF；網頁與一般 API 只收 PDF",
}


def office_dependent_tool_ids(pkg_to_id: dict[str, str]) -> set[str]:
    """實際會用到 office_convert 的工具（掃 import 與呼叫，不靠人工清單）。

    pkg_to_id：套件目錄名 → 工具 id。**不能用目錄名直接推 id** —— 例如
    `app/tools/pdf_diff/` 的工具 id 其實是 `doc-diff`（v1.1.61 改過名）。
    """
    # **`convert_[a-z_]+` 不是 `convert_to_[a-z]+`** —— v1.14.34 加的
    # `convert_with_filter` 就落在原本那個式子的範圍外，於是新工具完全
    # 偵測不到（掃出 17 個、實際 18 個）。這種漏法是無聲的：清單只要求
    # 「掃到的都要有標記」，掃不到就永遠不會紅。
    funcs = re.findall(r"^def (convert_[a-z_]+|find_soffice|detect_engine)",
                       (ROOT / "app" / "core" / "office_convert.py").read_text(),
                       re.M)
    pat = re.compile("|".join(funcs))
    out = set()
    for pkg in (ROOT / "app" / "tools").iterdir():
        if not pkg.is_dir() or pkg.name.startswith("_"):
            continue
        for py in pkg.rglob("*.py"):
            try:
                src = py.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "office_convert" in src and pat.search(src):
                tid = pkg_to_id.get(pkg.name)
                if tid:
                    out.add(tid)
                break
    return out


def page_entries() -> list[tuple[str, bool]]:
    """介紹站功能清單 → [(名稱, 是否有扳手標記)]。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    out = []
    for raw in re.findall(r'<span class="fn">(.*?)</span>', html, re.S):
        name = re.sub(r"<[^>]+>", "", raw).strip()
        out.append((name, "ofc-mark" in raw))
    return out


def covered(tid: str, name: str, haystack: str) -> bool:
    """該工具是否出現在文件中（接受 ALIASES 列出的替代措辭）。"""
    base = name.replace("（Beta）", "").strip()
    if base and base in haystack:
        return True
    return any(a in haystack for a in ALIASES.get(tid, []))


def main() -> int:
    tools = load_tools()
    ids = {t.metadata.id: t.metadata.name for t in tools}
    # 套件目錄名 → 工具 id。**不能用 router.__module__**（那是 APIRouter 實例，
    # 回傳 fastapi.routing），也不能用 id 直接換底線（`doc-diff` 的目錄是
    # `pdf_diff`，v1.1.61 改名時沒改目錄）→ 用 templates_dir 的上層目錄名。
    pkg_to_id: dict[str, str] = {}
    for t in tools:
        td = getattr(t, "templates_dir", None)
        pkg = Path(td).parent.name if td else t.metadata.id.replace("-", "_")
        pkg_to_id[pkg] = t.metadata.id
    entries = page_entries()
    page_text = " | ".join(n for n, _ in entries)
    marked = [n for n, m in entries if m]
    marked_text = " | ".join(marked)
    need_office = (office_dependent_tool_ids(pkg_to_id) & set(ids)) - set(OFFICE_OPTIONAL)
    readme = README.read_text(encoding="utf-8")
    problems = []

    # ① 介紹站是否涵蓋所有工具
    for tid, name in sorted(ids.items()):
        if not covered(tid, name, page_text):
            problems.append(f"介紹站功能清單缺少工具：{name}（{tid}）")

    # ② README 是否涵蓋所有工具
    for tid, name in sorted(ids.items()):
        if not covered(tid, name, readme):
            problems.append(f"README 缺少工具：{name}（{tid}）")

    # ③ 需 Office 的工具是否都有扳手標記
    for tid in sorted(need_office):
        if not covered(tid, ids[tid], marked_text):
            problems.append(f"需 Office 但介紹站沒標扳手：{ids[tid]}（{tid}）")

    # ④ 工具總數是否與文件一致
    n = len(ids)
    for label, path in (("README", README), ("介紹站", INDEX_HTML)):
        text = path.read_text(encoding="utf-8")
        nums = {int(x) for x in re.findall(r"(\d+)\s*(?:個)?工具", text)}
        # LLM 區塊的「N 個工具」是另一個數字（支援 LLM 的工具數）→ 只要總數有出現即可
        if n not in nums:
            problems.append(f"{label} 的工具總數沒有出現 {n}（找到：{sorted(nums)}）")

    print(f"工具 {n} 個｜需 Office {len(need_office)} 個｜介紹站標扳手 {len(marked)} 個")
    if not problems:
        print("✓ 文件涵蓋與 Office 標記皆正確")
        return 0
    print(f"\n✗ 發現 {len(problems)} 個問題：")
    for p in problems:
        print("  -", p)
    print("\n修法：把缺的工具補進 github/README.md 的功能總覽與 "
          "github/docs/index.html 的 feat-list；需 Office 的工具要加 "
          "ofc-mark 扳手 <span>。文件用不同措辭的請加進本檔的 ALIASES。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
