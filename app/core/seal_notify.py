"""用印申請的通知：輪到誰審、被退回、待用印、完成、用印失敗。

沿用站台的通知設定與每個人自己選的管道（Email、Zulip、Telegram…）：只寄案件連結，
**不夾帶文件**；連結要登入、要通過案件權限才打得開。通知失敗只記錄，**絕不影響案件本身**，
也不會重新產檔。在背景執行緒送，不讓使用者的請求等 SMTP。
"""
from __future__ import annotations

import logging
import threading
from typing import Iterable

logger = logging.getLogger(__name__)

SUBJECTS = {
    "review": "用印申請待你審核：{number} {subject}",
    "returned": "用印申請被退回補件：{number} {subject}",
    "rejected": "用印申請被駁回：{number} {subject}",
    "awaiting_stamp": "用印申請已核准，待你用印：{number} {subject}",
    "approved": "用印申請已核准，等待保管人用印：{number} {subject}",
    "done": "用印完成：{number} {subject}",
    "stamp_failed": "用印失敗，請重試：{number} {subject}",
}


def _link(case_id: str) -> str:
    try:
        from . import notify_settings
        base = (notify_settings.get().get("site_url") or "").strip()
    except Exception:  # noqa: BLE001
        base = ""
    return (base.rstrip("/") + f"/seal-request#case={case_id}") if base else ""


def _send(user_ids: list[int], subject: str, text: str) -> None:
    from . import notify_channels, notify_settings, workspace
    for uid in user_ids:
        try:
            channels, merged = notify_settings.resolve_for_user(workspace.key_for_user_id(uid))
            if not channels:
                continue
            failed = {c: e for c, e in notify_channels.broadcast(merged, channels, subject, text).items() if e}
            if failed:
                logger.warning("用印申請通知部分失敗（使用者 %s）：%s", uid, failed)
        except Exception as e:  # noqa: BLE001
            logger.warning("用印申請通知失敗（使用者 %s）：%s", uid, e)


def notify(kind: str, case: dict, user_ids: Iterable[int], comment: str = "") -> None:
    """送通知（背景執行緒）。`kind` 見 `SUBJECTS`。"""
    ids = sorted({int(u) for u in user_ids if u is not None})
    if not ids or kind not in SUBJECTS:
        return
    subject = SUBJECTS[kind].format(number=case.get("number", ""), subject=(case.get("subject") or "")[:60])
    lines = [subject, f"流程：{case.get('flow_name', '')}", f"申請人：{case.get('applicant_label', '')}"]
    if comment:
        lines.append(f"意見：{comment[:500]}")
    link = _link(case.get("id", ""))
    lines.append(f"開啟案件：{link}" if link else "請登入本站，在側欄的「用印申請」查看。")
    text = "\n".join(lines)
    threading.Thread(target=_send, args=(ids, subject, text), daemon=True, name="seal-notify").start()
