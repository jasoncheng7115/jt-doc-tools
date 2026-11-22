"""「我的作業」的「開啟」：每一個背景作業，要嘛回得到結果畫面，要嘛寫明為什麼只有下載（2026-11-22）。

判準（使用者 2026-09-19 交代、記在記憶）：**結果頁有沒有下載以外的東西**。產出就是一個檔案的（合併、加密、轉檔），
「我的作業」上的下載就是全部；結果是一個畫面的（壓縮前後大小、文字辨識的全文與預覽、轉換的對照預覽、
騎縫章的亂數種子），關掉分頁就回不去了 —— 那種要有 `view_url`，頁面也要接得回來。

這一支是結構檢查：

* 每一個 `job_manager.submit(...)`，後面緊接著寫了 `job.meta["view_url"]`，或者「作業類型＋送出的端點」列在
  `DOWNLOAD_ONLY` 而且寫了理由。新工具忘了想這件事就會紅。
* 清單裡的每一項都還真的存在（不讓清單越放越舊）。
* `view_url` 指回工具頁 `?job=` 的，那個頁面要真的會接（`resumeFromUrl()` 或自己讀 `job` 參數）。

**畫面真的接得回來**由 `tests/test_my_jobs_open_e2e.py` 在真的瀏覽器裡驗 —— 只看這一支的話，讀了 `?job=` 卻沒接上照樣全綠。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "app" / "tools"

_FILE_ONLY = "結果就是一個檔案：「我的作業」的下載就是全部，回到頁面也只會看到同一顆下載"
_API_ONLY = "頁面不走這支（只給 API / 腳本用），頁面上的結果不是由這件作業畫的"

#: 「作業類型 送出的端點」→ 為什麼不需要「開啟」
DOWNLOAD_ONLY: dict[str, str] = {
    "doc-straighten /submit": _FILE_ONLY,
    "image-to-pdf /generate": _FILE_ONLY,
    "office-convert /submit": _FILE_ONLY,
    "office-convert /convert": _FILE_ONLY,
    "office-to-pdf /submit": _FILE_ONLY,
    "pdf-border /submit": _FILE_ONLY,
    "pdf-decrypt /submit": _FILE_ONLY + "（密碼錯而略過的檔案寫在作業訊息裡，「我的作業」看得到）",
    "pdf-encrypt /submit": _FILE_ONLY,
    "pdf-merge /submit": _FILE_ONLY,
    "pdf-pageno /submit": _FILE_ONLY,
    "pdf-pages /submit": _FILE_ONLY,
    "pdf-pages /submit-from-upload": _FILE_ONLY,
    "pdf-split /submit": _FILE_ONLY,
    "pdf-stamp /submit": _FILE_ONLY,
    "pdf-watermark /submit": _FILE_ONLY,
    "pdf-watermark /batch/{batch_id}/process": _FILE_ONLY,
    "scan-merge /generate": _FILE_ONLY,
    "pdf-extract-images /submit": _API_ONLY + "（頁面是同步抽圖、勾選後打包）",
    "pdf-rotate /submit": _API_ONLY + "（頁面是同步產檔）",
    "pdf-to-office /convert": _API_ONLY + "（對外 API 沒有完整的診斷報告，頁面接回來會缺東西；頁面送出的那一處有「開啟」）",
    "pdf-fill /submit": _API_ONLY + "（頁面上的填寫不走這支；填寫歷史頁 `/tools/pdf-fill/history` 已停用，"
                        "沒有別的畫面可以指過去，結果就是填好的表單檔）",
    "pdf-fill-llm /llm-review-start": "LLM 欄位檢查是填寫流程中間的一步：要接回整個填寫畫面（預覽、套用建議）才有意義，"
                                      "關掉之後重按一次「AI 檢查」即可",
}


def _submits() -> list[tuple[str, str, Path, int, bool]]:
    """(作業類型, 送出的端點, 檔案, 行號, 後面有沒有寫 view_url)。"""
    out = []
    for p in sorted(TOOLS.rglob("*.py")):
        s = p.read_text(encoding="utf-8")
        for m in re.finditer(r"job_manager\.submit\(\s*[\"']([a-z0-9-]+)[\"']", s):
            tail = s[m.end():m.end() + 1500]
            r = tail.find("\n    return")
            seg = tail if r < 0 else tail[:r + 200]
            eps = re.findall(r'@router\.(?:post|get|put)\("([^"]+)"', s[:m.start()])
            out.append((m.group(1), eps[-1] if eps else "?", p, s.count("\n", 0, m.start()) + 1,
                        'view_url"]' in seg))
    return out


def test_the_scan_finds_the_submits():
    rows = _submits()
    assert len(rows) >= 30, f"掃到的送出處太少（{len(rows)}）—— 掃描的寫法可能跟不上程式了"


@pytest.mark.parametrize("row", _submits(), ids=lambda r: f"{r[0]} {r[1]}")
def test_every_job_has_a_way_back_or_a_reason(row):
    job_type, endpoint, path, line, has_view = row
    key = f"{job_type} {endpoint}"
    if has_view:
        assert key not in DOWNLOAD_ONLY, f"{key} 已經有「開啟」了，從 DOWNLOAD_ONLY 拿掉"
        return
    assert key in DOWNLOAD_ONLY, (
        f"{path.relative_to(ROOT)}:{line} 的作業（{key}）沒有 view_url：結果如果是一個畫面，"
        "送出之後寫 `job.meta[\"view_url\"]`、頁面呼叫 `resumeFromUrl()`；只有下載的話加進 DOWNLOAD_ONLY 並寫理由")
    assert len(DOWNLOAD_ONLY[key]) >= 10


def test_the_list_has_no_stale_entries():
    keys = {f"{t} {e}" for t, e, *_ in _submits()}
    stale = sorted(set(DOWNLOAD_ONLY) - keys)
    assert not stale, f"這幾項已經不存在（改名或刪了），從 DOWNLOAD_ONLY 拿掉：{stale}"


def _view_urls() -> list[tuple[str, Path]]:
    out = []
    for p in sorted(TOOLS.rglob("*.py")):
        s = p.read_text(encoding="utf-8")
        for m in re.finditer(r'view_url"\]\s*=\s*f?"(/tools/([a-z0-9-]+)/\?job=)', s):
            out.append((m.group(2), p))
    return out


@pytest.mark.parametrize("tool", sorted({t for t, _ in _view_urls()}))
def test_pages_that_get_a_job_link_pick_it_up(tool):
    """`view_url` 指回 `/tools/<工具>/?job=` 的，頁面要會接（不然「開啟」打開是一頁空白的工具頁）。"""
    tpl_dir = TOOLS / tool.replace("-", "_") / "templates"
    text = "\n".join(t.read_text(encoding="utf-8") for t in tpl_dir.glob("*.html"))
    assert (re.search(r"\.resumeFromUrl\(", text) or re.search(r"""get\(\s*['"]job['"]\s*\)""", text)), \
        f"{tool} 的頁面沒有讀 `?job=`"


def test_resume_validates_the_job_id():
    """`resumeFromUrl()` 先驗作業編號的格式才接進 API 路徑（網址是使用者改得到的）。"""
    js = (ROOT / "static" / "js" / "job_progress.js").read_text(encoding="utf-8")
    body = js[js.index("resumeFromUrl("):js.index("track(jobId) {")]
    assert "[a-f0-9]{32}" in body
