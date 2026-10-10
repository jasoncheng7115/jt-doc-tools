"""`docs-share/` 的內部往來文件不可以出現在公開版（使用者 2026-09-17 指示）。

那個目錄裡是**跟外部團隊往來的溝通文件**：對方尚未定案的設計與答覆、
我們自己的評估與取捨，以及整合測試環境、內網位址這一類部署細節。

**為什麼需要這一條**：`/opt/jt-doc-tools/.gitignore` 裡雖然有 `docs-share/`，
但**開發樹根本不是 git 工作區** —— 公開的路徑是
`sync-to-github.sh` 的 ITEMS → `github/` → rsync → clone → `git add -A`。
也就是說 `docs-share/` 目前沒外流的唯一理由是**它沒被列進 ITEMS**，
而「逐項列的清單一定會漏下一個」是本專案已經踩過的形狀。

**刻意不擋對方的名字**：等整合出貨，README／API 手冊／安裝說明本來就會
寫到它。擋字會讓這條檢查在那一天變成錯的，而**錯的檢查會被停掉**。
所以判準放在「**那些往來文件本身**」上：目錄、內容雜湊、
以及只有書信才會有的標題形狀。（名字與完整由來記在開發樹的 CLAUDE.md。）
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from tools.repo_paths import public_root

ROOT = Path(__file__).resolve().parents[1]
PUB = public_root(ROOT)
PRIVATE_DIR = ROOT / "docs-share"

#: 只有「一邊寫給另一邊」的書信才會有的標題形狀。產品文件不會這樣寫。
#:
#: **對方的名字不寫在原始碼裡，從 `docs-share/` 的子目錄名推出來** ——
#: 這樣名字不會留在公開樹上（`tests/` 是會同步出去的），
#: 換／多一個合作對象時也不必有人記得回來改這一行。
#:
#: **不要改成「`JTDT` 一側接箭頭、另一側是任意短名字」** —— 試過了，
#: 當場三條誤報：`nginx_jtdt 900s → jtdt`、`啟用認證 → jtdt-auditor`、
#: `/login itself → jtdt-admin`。`jtdt` 是我們自己的產品名，
#: 在路徑與帳號名裡到處都是，泛化的代價是誤報，而**誤報一多這份檢查
#: 就會被當雜訊忽略**（本專案的通則）。


def _counterparty_tokens() -> list[str]:
    """從 `docs-share/` 的子目錄名推出合作對象（`<對象>-integration` → `<對象>`）。"""
    if not PRIVATE_DIR.is_dir():
        return []
    out = []
    for d in PRIVATE_DIR.iterdir():
        if not d.is_dir():
            continue
        name = re.sub(r"[-_](integration|integrations)$", "", d.name)
        if re.fullmatch(r"[A-Za-z][\w.-]{1,19}", name):
            out.append(name)
    return sorted(set(out))


#: **判準要到「標題的分隔」為止，不可以只看兩個名字夾一個箭頭。**
#: 整合出貨之後，產品文件本來就會畫方向 —— 介紹站的流程圖有
#: `JTDT → JTLW` 這種方向標籤（v1.16.12 加的），只看箭頭的話那八格全被判成書信。
#: 往來書信的標題一律是「兩個名字 ＋ 標題分隔（`：` `｜` `:` `|`）或『介面』」：
#: `JTDT → <對象>｜2026-09-23（v2.5）`、`JTDT ↔ <對象> 介面：…`、`〈<對象> ↔ JTDT 介面回覆〉`。
#: 中間容許粗體記號與空白（`**JTDT → <對象>**：`）。
_TITLE_TAIL = r"(?:\*\*)?\s*(?:[：:｜|]|介面)"


def _letter_title_re(tokens: list[str]) -> "re.Pattern[str] | None":
    if not tokens:
        return None
    alt = "|".join(re.escape(x) for x in tokens)
    return re.compile(
        rf"(?i)(?:\bJTDT\b\s*(?:→|->|↔)\s*(?:{alt})\b{_TITLE_TAIL}"
        rf"|\b(?:{alt})\b\s*(?:→|->|↔)\s*\bJTDT\b{_TITLE_TAIL})")


_SKIP_DIRS = {".git", "node_modules", "__pycache__", "vendor", ".venv"}


def _public_files() -> list[Path]:
    out = []
    for p in PUB.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def _text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def test_the_scan_actually_reaches_the_public_tree():
    """「掃 0 個檔」跟「掃過都乾淨」在 pytest 輸出裡長得一模一樣。"""
    files = _public_files()
    assert len(files) > 200, f"公開樹只掃到 {len(files)} 個檔，這條檢查等於沒有執行"


def test_no_private_correspondence_directory_in_the_public_tree():
    bad = [p.relative_to(PUB).as_posix() for p in _public_files()
           if "docs-share" in p.relative_to(PUB).parts]
    assert not bad, f"往來文件的目錄被同步進公開樹了：{bad}"


_SYNC_SCRIPT = ROOT / "sync-to-github.sh"


@pytest.mark.skipif(not _SYNC_SCRIPT.is_file(),
                    reason="標準 clone 沒有同步腳本（它本來就不公開）")
def test_the_sync_script_does_not_publish_the_private_directory():
    """判準放在**唯一來源**上：公開什麼由 `sync-to-github.sh` 的 ITEMS 決定。

    **公開樹上跳過**：同步腳本只存在於開發樹，clone 下來的樹根本無從發佈任何東西。
    原本直接 `read_text()` → 在 clone 上炸 `FileNotFoundError`
    （跟「寫死 `github/` 那一層」同一個家族，只是方向相反）。
    跳過的代價由下面那條擋著：開發樹上它**必須真的跑到**。
    """
    script = _SYNC_SCRIPT.read_text(encoding="utf-8")
    items = re.search(r"^ITEMS=\((.*?)^\)", script, re.S | re.M)
    assert items, "找不到 sync-to-github.sh 的 ITEMS 清單"
    listed = [ln.strip() for ln in items.group(1).splitlines()
              if ln.strip() and not ln.strip().startswith("#")]
    assert "docs-share" not in listed, (
        "docs-share 被列進同步清單了 —— 那是內部往來文件，不可以公開")


@pytest.mark.skipif(not PRIVATE_DIR.is_dir(), reason="沒有 docs-share/")
def test_no_public_file_has_the_same_content_as_a_private_note():
    """比**內容**不是比檔名 —— 換個檔名複製過去一樣擋得到，而且零誤報。"""
    private = {}
    for p in PRIVATE_DIR.rglob("*"):
        if p.is_file():
            private[hashlib.sha256(p.read_bytes()).hexdigest()] = p.name
    assert private, "docs-share/ 是空的，這條驗不到東西"

    bad = []
    for p in _public_files():
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h in private:
            bad.append(f"{p.relative_to(PUB).as_posix()}（＝{private[h]}）")
    assert not bad, f"公開樹裡有跟往來文件內容完全相同的檔案：{bad}"


def test_no_public_file_carries_a_letter_heading():
    """擋「改了檔名、也改了幾個字，但整封信貼過去」那一種。

    自動檢查**說明**這條規則時會引用那個形狀（use vs mention，本專案踩過很多次），
    所以排除掉正在講這件事的測試檔本身。
    """
    tokens = _counterparty_tokens()
    pattern = _letter_title_re(tokens)
    if pattern is None:
        pytest.skip("沒有 docs-share/，推不出對象名（公開 clone 本來就沒有東西可外流）")

    mine = Path(__file__).name
    bad = []
    for p in _public_files():
        if p.name == mine:
            continue
        for i, line in enumerate(_text(p).splitlines(), 1):
            if pattern.search(line):
                bad.append(f"{p.relative_to(PUB).as_posix()}:{i}")
    assert not bad, f"公開樹裡有往來書信的標題：{bad}"


@pytest.mark.parametrize("line", [
    "# JTDT → acme：第二輪（回覆確認）",
    "# JTDT → ACME｜2026-09-23（v2.5）",
    "# JTDT ↔ acme 介面：要一起談定的事",
    "來源：對方交來的〈ACME ↔ JTDT 介面回覆〉v0.5",
    "**JTDT → acme**：用什麼身分？",
    "acme -> JTDT | v1.2",
])
def test_letter_headings_are_caught(line):
    """收窄之後，書信標題的每一種寫法仍然要抓得到（對象名用虛構的 `acme`）。"""
    assert _letter_title_re(["acme"]).search(line), line


@pytest.mark.parametrize("line", [
    '<span class="spf-dir">JTDT → ACME</span><div class="spf-line"></div>',
    '<span class="spf-dir">ACME → JTDT</span>',
    "箭頭是資料的流向：JTDT → ACME 送件，ACME → JTDT 回傳逐字稿",
])
def test_direction_labels_in_product_docs_are_not_letters(line):
    """反向對照：產品文件本來就會畫方向（介紹站的流程圖）。
    只看「兩個名字夾一個箭頭」的話，這些全部會被判成書信 —— 誤報一多這份檢查就會被停掉。"""
    assert not _letter_title_re(["acme"]).search(line), line


def test_every_real_letter_heading_still_matches():
    """**判準收窄之後，拿真的往來文件驗一次**：`docs-share/` 裡每一個含兩個名字與箭頭的
    Markdown 標題都要被抓到。不然收窄可能剛好漏掉某一種真實的寫法，而這裡永遠綠。"""
    tokens = _counterparty_tokens()
    pattern = _letter_title_re(tokens)
    if pattern is None:
        pytest.skip("沒有 docs-share/（公開 clone）")
    alt = "|".join(re.escape(x) for x in tokens)
    pair = re.compile(rf"(?i)\b(?:JTDT|{alt})\b\s*(?:→|->|↔)\s*\b(?:JTDT|{alt})\b")
    headings = []
    # 素材只取往來文件（`<對象>-integration/`）：`claude-notes/` 是開發筆記，
    # 裡面講介紹站流程圖的標題（「JTDT ↔ 對象 流程圖」）是在描述產品，不是書信
    letters = [p for d in PRIVATE_DIR.glob("*-integration") if d.is_dir() for p in d.rglob("*.md")]
    for p in letters:
        for line in _text(p).splitlines():
            if line.startswith("#") and pair.search(line):
                headings.append(line)
    assert len(headings) >= 3, f"只找到 {len(headings)} 個書信標題 —— 素材的範圍大概錯了"
    missed = [h for h in headings if not pattern.search(h)]
    assert not missed, f"這些真的書信標題抓不到：{missed[:5]}"


def test_the_letter_heading_check_knows_who_the_counterparty_is():
    """先證明它推得出對象名 —— 推不出來的話上一條是空跑，而空跑也是綠的。"""
    if not PRIVATE_DIR.is_dir():
        pytest.skip("沒有 docs-share/")
    tokens = _counterparty_tokens()
    assert tokens, (
        f"從 {PRIVATE_DIR} 的子目錄推不出任何合作對象名，"
        "上一條檢查等於沒有執行。目錄命名慣例是 `<對象>-integration/`。")
    pattern = _letter_title_re(tokens)
    assert pattern is not None
    assert pattern.search(f"# JTDT \u2192 {tokens[0]}\uff1a\u6e2c\u8a66"), "組出來的式子配不到書信標題"
    for benign in ("nginx_jtdt 900s \u2192 jtdt \u2192 nginx_llm",
                   "#### 6.13.1 \u555f\u7528\u8a8d\u8b49 \u2192 jtdt-auditor \u81ea\u52d5\u5efa",
                   "# /login itself \u2192 jtdt-admin break-glass"):
        assert not pattern.search(benign), f"誤報：{benign}"


def test_that_guard_really_runs_in_the_development_tree():
    """「整份 skip」跟「整份通過」在 pytest 輸出裡長得一模一樣。

    上面那條在 clone 上跳過是對的，但**開發樹上跳過就等於沒有檢查** ——
    判準走 `public_root()`（它回的不是自己 ＝ 開發樹），那時同步腳本一定要在。
    **不可以自己寫死那一層的名字** —— 隔壁 `test_public_tree_paths.py`
    擋的正是那個寫法，我寫這條時當場被它抓到。
    """
    if PUB == ROOT:          # `public_root()` 回自己 ＝ 這是標準 clone
        pytest.skip("這裡不是開發樹")
    assert _SYNC_SCRIPT.is_file(), (
        "開發樹上找不到 sync-to-github.sh —— 上面那條檢查會被整條跳過，"
        "而 pytest 的輸出看起來跟通過一樣")
