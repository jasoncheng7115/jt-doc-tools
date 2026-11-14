"""名片辨識完成的通知：跟作業完成通知同一套設定與管道（`notify_settings` / `notify_channels`）。

辨識批次不是「作業」（不進「我的作業」），所以自己組訊息；規則照作業通知：
通知沒開、狀態不在通知清單、耗時短於門檻（使用者還盯著畫面）都不送。
**訊息不含名片內容**（姓名、公司、電話一概不放）—— 通知會送到外部服務，只有張數、耗時與回到工具頁的連結。
絕不丟例外：通知失敗不可以讓辨識看起來像失敗。
"""
from __future__ import annotations

import logging

log = logging.getLogger("app.business_card.notify")

TOOL_PATH = "/tools/business-card-reader/"


def _fmt(sec: float) -> str:
    from ...core.job_notify import _fmt_elapsed
    return _fmt_elapsed(sec)


def _user_id(owner: str):
    if owner.startswith("u") and owner[1:].isdigit():
        return int(owner[1:])
    return None


def build(done: int, failed: int, elapsed: float, url: str) -> tuple[str, str, bool]:
    """回 (標題, 內文, 是否算「完成」)。全部失敗才算失敗。"""
    ok = done > 0 or failed == 0
    subject = f"[{'完成' if ok else '失敗'}] 名片辨識：{done + failed} 張名片"
    lines = ["工具：名片辨識", f"名片：{done + failed} 張" + (f"（{failed} 張辨識失敗）" if failed else ""),
             f"耗時：{_fmt(elapsed)}"]
    lines.append("到名片辨識頁校對與匯出" + (f"：{url}" if url else "。"))
    return subject, "\n".join(lines), ok


def batch_finished(owner: str, done: int, failed: int, elapsed: float, origin: str = "") -> None:
    try:
        _send(owner, done, failed, elapsed, origin)
    except Exception as e:  # noqa: BLE001
        log.warning("名片辨識完成通知失敗：%s", e.__class__.__name__)


def _send(owner: str, done: int, failed: int, elapsed: float, origin: str) -> None:
    from ...core import notify_channels, notify_settings, workspace
    from ...core.job_notify import _site_url

    cfg = notify_settings.get()
    if not cfg.get("enabled") or done + failed == 0:
        return
    min_sec = int(cfg.get("min_seconds") or 0)
    if min_sec and elapsed < min_sec:
        return

    class _J:                                         # _site_url 只讀 meta["origin"]
        meta = {"origin": origin or ""}

    url = _site_url(TOOL_PATH, _J())
    subject, text, ok = build(done, failed, elapsed, url)
    if ("done" if ok else "failed") not in (cfg.get("notify_on") or []):
        return
    try:
        key = workspace.key_for_user_id(_user_id(owner))
    except Exception:  # noqa: BLE001 — 啟用登入但沒有歸屬：沒有人可通知
        return
    channels, merged = notify_settings.resolve_for_user(key)
    if not channels:
        return
    results = notify_channels.broadcast(merged, channels, subject, text, "", {})
    failed_ch = {c: e for c, e in results.items() if e}
    if failed_ch:
        log.warning("名片辨識完成通知部分失敗：%s", ",".join(failed_ch))
