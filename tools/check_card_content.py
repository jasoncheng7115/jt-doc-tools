#!/usr/bin/env python3
"""名片內容檢查：真實名片上的字不可以出現在要公開的檔案或 commit 訊息裡。

名片辨識的真實樣本（開發機上的 `temp_pdfs/business_cards/`，不進 git）上面是真人的姓名、公司、電話、
統編、Email、網址、地址。測試、更新記錄、文件、commit 訊息一律用杜撰的值 —— 這支檢查從樣本的 OCR 結果
與檔名抽出「只有那張名片才會有的字」，掃公開樹或一段文字。

沒有樣本（例如從 GitHub clone 下來的公開樹）時什麼都不檢查、回 0：那裡本來就沒有樣本可以外流。

用法：

    python tools/check_card_content.py <路徑…>        # 掃檔案或資料夾
    python tools/check_card_content.py --text <檔案>   # 掃一段文字（commit 訊息）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "temp_pdfs" / "business_cards"

#: 名片上常見、本身不指向任何人的字（職稱、標籤、公司型態），不算外流
GENERIC = {
    "統一編號", "電子郵件", "聯絡電話", "行動電話", "傳真號碼", "業務經理", "業務代表", "業務專員", "股份有限公司",
    "有限公司", "免付費服務專線", "服務專線", "預約專線", "夜間電話", "採購副理", "社區經理", "總經理室",
    "Business Card", "Business Cards", "Name Card", "Visiting Card", "Calling Card", "台灣固網",
}
#: 網域裡太常見、不指向特定公司的那一段
GENERIC_LABELS = {"google", "yahoo", "hotmail", "outlook", "gmail", "facebook", "instagram", "twitter", "linkedin",
                  "youtube", "github", "wikipedia", "wikimedia", "example"}
#: 名片上常見的英文字（職稱、部門、公司型態、地址字樣、地名）
GENERIC_EN = set("""
manager general sales senior assistant director chief executive officer president vice department division section
marketing engineering engineer technical technology technologies service services customer business development project
product account accounting finance financial human resources administration admin office branch head deputy special
company corporation limited group international industrial industry science park building floor road street lane alley
avenue boulevard district city county township village taiwan taipei new kaohsiung taichung tainan hsinchu keelung taoyuan
north south east west republic china hong kong japan tel fax mobile phone email mail web website home page address
card cards name visiting calling the and for of co ltd inc corp team center centre research institute university college
school hospital bank hotel restaurant shop store studio design consulting solutions systems partners media press public
relations representative specialist consultant supervisor coordinator executive principal staff member employment station
agency ministry labor bureau police government national regional local agent area region global asia pacific
managing program officer operations operation quality control planning purchasing procurement legal
community owner founder partner associate lead leader coordinator analyst editor reporter writer artist
""".split())
#: 公用的網域與信箱業者（不指向任何人）
GENERIC_DOMAINS = {"gov.tw", "com.tw", "org.tw", "edu.tw", "net.tw", "idv.tw", "gmail.com", "yahoo.com", "yahoo.com.tw",
                   "hotmail.com", "outlook.com", "msn.com", "hinet.net", "msa.hinet.net", "icloud.com", "live.com"}


def _is_date(d: str) -> bool:
    """8 碼的年月日（20260530 這種）不是號碼。"""
    return len(d) == 8 and d[:2] in ("19", "20") and 1 <= int(d[4:6]) <= 12 and 1 <= int(d[6:]) <= 31


TEXT_EXT = {".py", ".md", ".html", ".htm", ".json", ".js", ".txt", ".yaml", ".yml", ".csv", ".css", ".sh", ".toml"}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "temp_pdfs", "docs-share", "temp", "data", ".venv"}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def load_tokens(samples: Path = SAMPLES) -> dict[str, set[str]]:
    """{"num": 號碼（7 碼以上的數字，比對時去掉分隔號）, "cjk": 中文字串, "lat": 英文 / 網域 / Email}。"""
    tok = {"num": set(), "cjk": set(), "lat": set()}
    words = samples / "_eval" / "easyocr_words.json"
    texts: list[str] = []
    if words.is_file():
        for v in json.loads(words.read_text(encoding="utf-8")).values():
            ws = [w.get("text") or "" for w in v.get("words", [])]
            texts += ws
            # Email 被 OCR 拆成兩段（「…帳號」＋「@網域」）：接起來再抽一次
            texts += [a + b for a, b in zip(ws, ws[1:]) if b.startswith("@")]
    for p in (samples / "commons").glob("*") if (samples / "commons").is_dir() else []:
        stem = re.sub(r"[_\-]+", " ", p.stem).strip()
        for m in re.findall(r"(?:[A-Z][a-z]+(?:\s+|$)){2,}", stem + " "):
            m = m.strip()
            if m not in GENERIC and len(m) >= 8:
                tok["lat"].add(m)
        texts.append(stem)
    for t in texts:
        for m in re.findall(r"[+(]?\d[\d\-() .]{5,}\d", t):
            d = _digits(m)
            if len(d) >= 7 and not _is_date(d[-8:]):
                tok["num"].add(d[-8:] if len(d) >= 8 else d)
        for m in re.findall(r"[一-鿿]{4,}", t):
            if m not in GENERIC:
                tok["cjk"].add(m)
        for m in re.findall(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", t):
            if not m.lower().split("@")[1].startswith("example."):
                tok["lat"].add(m.lower())
                local = m.split("@")[0].strip(".-_").lower()
                # 帳號那段（網域換了也一樣是那個人）；service、sales 這種共用信箱不算
                local = re.sub(r"^e-?mail[:：]?", "", local)
                if (re.search(r"[._\d]", local) or len(local) >= 8) and not local.isdigit() and len(local) >= 5:
                    tok["lat"].add(local)
        # 網域裡有辨識度的那一段（「xxxx.com.tw」的 xxxx），6 個字以上才算，避免撞到一般英文字
        for m in re.findall(r"(?:www\.)?([a-z0-9\-]{6,})\.(?:com|net|org|co|gov|edu)(?:\.[a-z]{2})?\b", t, re.I):
            m = re.sub(r"^w{3}", "", m.lower())
            if m not in GENERIC_LABELS and not m.startswith("example"):
                tok["lat"].add("." + m + ".")
        # 兩個以上大寫開頭的英文字連在一起（人名、公司名、路名），至少一個不是常見的商務用字
        for m in re.findall(r"\b[A-Z][a-z]{2,}(?:[ \-][A-Z][a-z]{2,})+\b", t):
            words = re.split(r"[ \-]", m)
            if any(w.lower() not in GENERIC_EN for w in words):
                tok["lat"].add(m)
            # 也收相鄰兩個字（文件裡常只寫名稱的前兩個字）
            for a, b in zip(words, words[1:]):
                if len(a) >= 4 and len(b) >= 4 and len(a) + len(b) >= 9 \
                        and (a.lower() not in GENERIC_EN or b.lower() not in GENERIC_EN):
                    tok["lat"].add(a + " " + b)
        for m in re.findall(r"\b(?:www\.)?[a-z0-9\-]{3,}\.(?:com|net|org|com\.tw|org\.tw|gov\.tw|edu\.tw|tw)\b", t, re.I):
            m = m.lower()
            bare = m[4:] if m.startswith("www.") else m
            if not bare.startswith("example.") and bare not in GENERIC_DOMAINS and not bare.endswith(".hinet.net"):
                tok["lat"].add(m)
    return tok


def scan_text(text: str, tok: dict[str, set[str]]) -> list[str]:
    hits = set()
    nums = {_digits(m) for m in re.findall(r"[+(]?\d[\d\-() .]{5,}\d", text)}
    for d in nums:
        if len(d) >= 7 and (d[-8:] if len(d) >= 8 else d) in tok["num"]:
            hits.add(d)
    for c in tok["cjk"]:
        if c in text:
            hits.add(c)
    low = text.lower()
    for s in tok["lat"]:
        if s.lower() in low:                              # 不分大小寫（註解裡常寫成小寫）
            hits.add(s)
    return sorted(hits)


def iter_files(paths):
    for p in paths:
        p = Path(p)
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and f.suffix.lower() in TEXT_EXT and not (set(f.parts) & SKIP_DIRS):
                    yield f


def scan_paths(paths, tok=None) -> dict[str, list[str]]:
    tok = tok if tok is not None else load_tokens()
    out = {}
    if not any(tok.values()):
        return out
    for f in iter_files(paths):
        try:
            hits = scan_text(f.read_text(encoding="utf-8"), tok)
        except (UnicodeDecodeError, OSError):
            continue
        if hits:
            out[str(f)] = hits
    return out


def main(argv: list[str]) -> int:
    tok = load_tokens()
    if not any(tok.values()):
        return 0                     # 沒有樣本：沒有東西可以外流
    if len(argv) >= 3 and argv[1] == "--text":
        raw = Path(argv[2]).read_text(encoding="utf-8")
        text = "\n".join(ln for ln in raw.splitlines() if not ln.startswith("#"))
        hits = scan_text(text, tok)
        if hits:
            # 不印出命中的字：檢查本身不可以變成外流的管道（輸出會進終端機紀錄、CI 日誌）
            print(f"有 {len(hits)} 處跟真實名片相同的內容，請改成杜撰的值", file=sys.stderr)
            return 1
        return 0
    paths = [a for a in argv[1:] if a != "--show"]
    res = scan_paths(paths or [ROOT / "github"], tok)
    for f, hits in res.items():
        print(f"{f}: {len(hits)} 處跟真實名片相同的內容（用 --show 在本機看是哪些）", file=sys.stderr)
        if "--show" in sys.argv:
            print("   " + "、".join(hits), file=sys.stderr)
    return 1 if res else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
