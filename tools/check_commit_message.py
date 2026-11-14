#!/usr/bin/env python3
"""公開 repo 的 commit 訊息檢查 —— 可以當 `commit-msg` hook 用。

**為什麼要做成機械擋的**：使用者 2026-09-13 明講過「不應該有 claude session
在 github」，2026-09-21 再確認一次。但開發工具那一側**每個 session 都會提示
要加 `Claude-Session:` 那一行**，而且措辭是「取代先前的署名指引」——
只靠「記得不要加」的話，遲早有一次會加進去，而那是推上公開 repo 才看得到的。
記了規則沒有檢查，等於沒記。

擋兩類：

1. **Claude session 連結 / 署名行** —— 對外的 repo，那是內部資訊而且沒有意義。
   `Co-Authored-By: Claude …` 是一般的協作署名，**不擋**。
2. **簡體字** —— commit / PR / branch 一律台灣繁體（v1.5.3 踩過，
   當時要請使用者暫時關掉 ruleset 才改得掉）。
3. **真實名片上的內容** —— 開發機上有名片樣本時，交給 `check_card_content.py` 比對
   （姓名、公司、號碼、網域；使用者 2026-11-14：「那都是真實資料，這是鐵則」）。

用法：

    python tools/check_commit_message.py <訊息檔>     # 回 0 才放行

裝成 hook（每次重新 clone 之後跑一次）：

    ln -sf "$(pwd)/tools/check_commit_message.py" <clone>/.git/hooks/commit-msg
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 「Claude 的 session」長什麼樣：網址、或那個署名欄位。
# `Co-Authored-By: Claude …` 不在裡面 —— 那是允許的。
_SESSION = re.compile(
    r"claude\.ai/code/session"      # 連結本身
    r"|^\s*Claude-Session\s*:"      # 署名欄位
    r"|\bsession_[0-9A-Za-z]{16,}\b",  # 裸的 session id
    re.I | re.M,
)

# 只收「繁體有別的寫法」的那些字；跳 / 关 這種兩邊同形或容易誤判的不列。
_SIMPLIFIED = "们时对护页储转实压网络图应备历战场这么没东觉识语汇经济来"


def check(text: str) -> list[str]:
    bad = []
    for m in _SESSION.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        bad.append(f"第 {line} 行有 Claude session 的痕跡：{m.group(0)!r}")
    hits = sorted({c for c in text if c in _SIMPLIFIED})
    if hits:
        bad.append("出現簡體字：" + " ".join(hits) + "（commit 訊息一律台灣繁體）")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import check_card_content as _cc
        tok = _cc.load_tokens()
        if any(tok.values()):
            n = len(_cc.scan_text(text, tok))
            if n:
                bad.append(f"有 {n} 處跟真實名片相同的內容（名片內容一律改成杜撰的值）")
    except ImportError:
        pass
    return bad


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    raw = Path(argv[1]).read_text(encoding="utf-8")
    # git 自己加的註解行不算訊息內容
    text = "\n".join(ln for ln in raw.splitlines() if not ln.startswith("#"))
    bad = check(text)
    if bad:
        print("commit 訊息被擋下來：", file=sys.stderr)
        for b in bad:
            print("  - " + b, file=sys.stderr)
        print("\n這是公開 repo 的訊息。`Co-Authored-By: Claude …` 可以留，"
              "session 連結不可以。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
