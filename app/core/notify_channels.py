"""通知管道發送器：Email / Telegram / Slack / Teams / Discord / Zulip /
Nextcloud Talk / LINE / 通用 Webhook。

做法沿用 jt-ipam 的 `notify_channels.py`（同一位作者的另一個專案，已在正式環境
跑過），差別是**這裡是同步的** —— 作業完成的通知是在背景執行緒裡送出，硬要在
執行緒內起 event loop 只會讓錯誤更難查。多管道之間用小型執行緒池並行，最壞情況
是「最慢的單一管道」而不是各管道相加。

## 信任模型（與 SMTP 主機相同）

目標端點由**管理員**設定，因此：

* **不套 SSRF 白名單** —— 自架的 Nextcloud Talk / Zulip 幾乎都在內網，套白名單
  等於讓這兩個管道不能用。能設定這裡的人本來就有主機權限。
* `follow_redirects=False` —— 避免用重導把訊息帶去別的地方。
* 逾時必填，且不重試 —— 通知是附屬品，不該把作業執行緒卡住。

## 失敗處理

每個 `send_*` 成功回 None、失敗丟例外（測試按鈕要看得到原因）。
`broadcast()` 逐管道 best-effort：單一管道失敗不影響其他管道，更不影響作業本身。

## LINE

**LINE Notify 已於 2025-03-31 停止服務**，因此這裡用的是 Messaging API 的
push message —— 需要 LINE 官方帳號、channel access token，以及收訊者的 user id
（不是 LINE ID）。門檻比其他管道高，UI 要寫清楚前置需求。
"""
from __future__ import annotations

import re as _re

import hashlib
import hmac
import json
import logging
import secrets as _secrets
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

logger = logging.getLogger("app.notify")

#: webhook 型管道（email 走 SMTP，另外處理）
WEBHOOK_CHANNELS = ("telegram", "slack", "teams", "discord", "zulip",
                    "nextcloud", "line", "webhook")

ALL_CHANNELS = ("email",) + WEBHOOK_CHANNELS

#: 顯示名稱與前置需求（給 admin UI 用）
#: **對真實服務端到端驗證過的是 Email、Zulip、Telegram**（Email：實機 SMTP 寄出、收件者確認收到；
#: Zulip：v1.17.0 對 Zulip 12 站台私訊 Bot 綁定、管理頁傳送測試，收件者確認收到；
#: Telegram：v1.17.1 使用者實測送得到）。
#: 其餘管道有單元測試（把網路呼叫換掉、驗載荷格式），但沒有人真的接上 Slack /
#: Teams / Telegram … 實際收過訊息 —— 所以標為開發階段，讓管理員知道這件事，
#: 而不是設定下去之後才發現收不到。
#:
#: 驗證過一個就把它從這裡拿掉（並在 CHANGELOG 註明是用哪個服務驗的）。
DEV_CHANNELS: frozenset[str] = frozenset({
    "slack", "teams", "discord", "nextcloud",
    "line", "webhook",
})


def verified_labels() -> str:
    """已經實機驗證過的管道名稱（「Email、Zulip、Telegram」）。畫面上那句說明由這裡產生，
    **不在兩個頁面各寫一次**（原本是寫死的字串，改一邊就會漏一邊）。"""
    return "、".join(CHANNEL_INFO[c]["label"] for c in ALL_CHANNELS if c not in DEV_CHANNELS)

CHANNEL_INFO: dict[str, dict] = {
    "email":     {"label": "Email", "needs": "SMTP 主機"},
    "telegram":  {"label": "Telegram", "needs": "Bot token + Chat ID"},
    "slack":     {"label": "Slack", "needs": "Incoming Webhook URL"},
    "teams":     {"label": "Microsoft Teams", "needs": "Workflows 或舊版連接器的 Webhook URL"},
    "discord":   {"label": "Discord", "needs": "Webhook URL"},
    "zulip":     {"label": "Zulip", "needs": "站台網址 + Bot Email + API Key；團隊頻道（channel）選填，沒填就私訊到使用者帳號的信箱"},
    "nextcloud": {"label": "Nextcloud Talk", "needs": "站台網址 + 對話 token + Bot 密鑰（使用者可改填自己的一對一對話 token）"},
    "line":      {"label": "LINE", "needs": "官方帳號的 Channel Access Token + 收訊者 User ID"
                                            "（LINE Notify 已於 2025 年停止服務）"},
    "webhook":   {"label": "通用 Webhook", "needs": "URL（可選 Bearer token）"},
}

_TIMEOUT = 12.0


def _rest(subject: str, text: str | None) -> str:
    """內文第一行就是主旨時拿掉那一行（很多通知的內文本來就以主旨開頭；聊天類通道又會把主旨接在前面，
    在 Telegram 等就看到同一行出現兩次）。"""
    t = text or ""
    first, _, rest = t.partition("\n")
    return rest.lstrip("\n") if first.strip() == (subject or "").strip() else t


def _msg(subject: str, text: str | None) -> str:
    body = _rest(subject, text)
    return f"{subject}\n{body}" if body else subject


def _post(url: str, *, json: dict | None = None, data: dict | None = None,
          headers: dict | None = None, auth: tuple[str, str] | None = None) -> None:
    import httpx
    # follow_redirects=False：避免以重導把訊息帶往設定以外的位置
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as c:
        r = c.post(url, json=json, data=data, headers=headers, auth=auth)
        if r.status_code >= 300:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")


# ---------- 各管道 ----------

def send_telegram(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    token, chat = cfg.get("telegram_token"), cfg.get("telegram_chat_id")
    if not (token and chat):
        raise RuntimeError("尚未設定 Telegram bot token / chat id")
    _post(f"https://api.telegram.org/bot{token}/sendMessage",
          json={"chat_id": chat, "text": _msg(subject, text),
                "disable_web_page_preview": True})


def send_slack(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    url = cfg.get("slack_webhook")
    if not url:
        raise RuntimeError("尚未設定 Slack webhook URL")
    body = _rest(subject, text)
    _post(url, json={"text": f"*{subject}*\n{body}" if body else f"*{subject}*"})


def send_teams(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    url = cfg.get("teams_webhook")
    if not url:
        raise RuntimeError("尚未設定 Teams webhook URL")
    rest = _rest(subject, text)
    body = f"**{subject}**\n\n{rest}" if rest else f"**{subject}**"
    try:
        # 舊版 Office 365 連接器：純 text
        _post(url, json={"text": body})
    except Exception:
        # 新版 Teams「Workflows」的 incoming webhook 需要 Adaptive Card
        # （微軟已淘汰舊連接器）—— 兩種格式都試，管理員不必自己分辨用的是哪種
        _post(url, json={
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard", "version": "1.4",
                    "body": [{"type": "TextBlock", "text": body, "wrap": True}],
                },
            }],
        })


def send_discord(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    url = cfg.get("discord_webhook")
    if not url:
        raise RuntimeError("尚未設定 Discord webhook URL")
    _post(url, json={"embeds": [{
        "title": subject[:256],
        "description": _rest(subject, text)[:4000],
        "color": 0x2563EB,
    }]})


def zulip_recipient(cfg: dict[str, Any]) -> str:
    """私訊對象：使用者編號（`zulip_user_id`，有填就優先）→ `zulip_to`（email，或數字的使用者編號）。"""
    uid = str(cfg.get("zulip_user_id") or "").strip()
    if uid.isdigit():
        return uid
    return str(cfg.get("zulip_to") or "").strip()


def send_zulip(cfg: dict[str, Any], subject: str, text: str | None) -> int:
    """Zulip：有私訊對象就送**私訊**，否則送到管理員設定的團隊頻道（channel）。回傳 Zulip 的訊息編號。

    私訊對象（`zulip_recipient`）：使用者編號優先；沒有就用 email（使用者自己填的，或帳號上的信箱）。
    Zulip 的 email 隱私設定會讓機器人只看到 `user9@<站台>` 這種代用地址 —— 那時真正的信箱對不上，
    要用使用者編號（Zulip 維護人員 2026-10-18：不要要求使用者公開 email）。

    Zulip 是唯一不用換憑證就能私訊的管道 —— 同一支 `/api/v1/messages`，把 `type` 改掉即可。
    私訊用 `direct`（Zulip 7.0 起；`private` 已列為淘汰），`to` 是 JSON 編碼的陣列（使用者編號是整數）。
    頻道訊息維持 `stream`：Zulip 9.0 把 stream 改名 channel、加了 `type: "channel"`，但 `stream` 沒有淘汰、
    舊版也收 —— 畫面上叫 channel。

    **送出後核對 Zulip 的回應**：要 `result == "success"` 而且有訊息編號，不是看到 HTTP 200 就算成功。
    """
    site = (cfg.get("zulip_site") or "").rstrip("/")
    email, api_key = cfg.get("zulip_bot_email"), cfg.get("zulip_api_key")
    if not (site and email and api_key):
        raise RuntimeError("尚未設定 Zulip 站台、Bot Email 或 API Key")
    rest = _rest(subject, text)
    body = f"**{subject}**\n{rest}" if rest else f"**{subject}**"
    to_me = zulip_recipient(cfg)
    if to_me:
        target = int(to_me) if to_me.isdigit() else to_me
        payload = {"type": "direct", "to": json.dumps([target]), "content": body}
    else:
        stream = cfg.get("zulip_stream")
        if not stream:
            raise RuntimeError("沒有收訊對象：沒有設定團隊頻道（channel），也沒有可以私訊的 email（帳號上沒有信箱）")
        payload = {"type": "stream", "to": stream,
                   "topic": cfg.get("zulip_topic") or "jt-doc-tools",
                   "content": body}
    import httpx
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as c:
            r = c.post(f"{site}/api/v1/messages", auth=(email, api_key), data=payload)
    except httpx.HTTPError as e:
        raise RuntimeError(f"連不上 Zulip 站台（{type(e).__name__}）") from None
    try:
        res = r.json()
    except ValueError:
        res = None
    if not isinstance(res, dict):
        raise RuntimeError(f"Zulip 回應不是 JSON（HTTP {r.status_code}）")
    if r.status_code >= 300 or res.get("result") != "success":
        raise RuntimeError(_zulip_error_text(r.status_code, res, to_me))
    mid = res.get("id")
    if not isinstance(mid, int):
        raise RuntimeError(f"Zulip 回應沒有訊息編號（HTTP {r.status_code}，result={str(res.get('result'))[:20]}）")
    return mid


_EMAIL_RE = _re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+)")


def _mask(text: str) -> str:
    """錯誤訊息裡的 email 遮成 `j***@網域`；斜線、換行換掉（`safe_user_error` 看到就整句不顯示）。"""
    t = _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    return _re.sub(r"[\r\n/\\]+", " ", t).strip()


def _zulip_error_text(status: int, res: dict, to_me: str) -> str:
    """保留 HTTP 狀態碼與 Zulip 的 result / code / msg（遮罩後），不把各種錯誤都講成「找不到使用者」。

    要過 `safe_user_error`（200 字內、沒有斜線與換行），不然畫面上只剩「傳送失敗」。只有 email 被拒
    （`Invalid email`）時另外補一句原因：Zulip 不公開 email 時機器人看不到真正的信箱，要改填使用者編號。"""
    code = _mask(str(res.get("code") or ""))[:40]
    msg = _mask(str(res.get("msg") or ""))[:110]
    head = f"Zulip 回應 HTTP {status}" + (f"（{code}）" if code else "") + f"：{msg or str(res.get('result'))[:20]}"
    if "Invalid email" in str(res.get("msg") or "") and to_me and not to_me.isdigit():
        head += "；Zulip 不公開 email 時請改填使用者編號"
    return head[:195]


def send_nextcloud(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    """Nextcloud Talk bot：對話 token + bot 密鑰（HMAC-SHA256 簽 random+message）。

    **一對一對話本身也有 token** —— 使用者填了自己那個就是私訊，沒填就送到管理員
    設定的群組對話。憑證（bot 密鑰）完全不用換。
    """
    site = (cfg.get("nextcloud_url") or "").rstrip("/")
    secret = cfg.get("nextcloud_secret")
    # 使用者自己的對話 token 優先
    token = (cfg.get("nextcloud_to") or "").strip() or cfg.get("nextcloud_token")
    if not (site and token and secret):
        raise RuntimeError("尚未設定 Nextcloud 站台 / 對話 token / bot 密鑰")
    message = _msg(subject, text)
    rnd = _secrets.token_hex(32)
    sig = hmac.new(secret.encode(), (rnd + message).encode(),
                   hashlib.sha256).hexdigest()
    # **token 會進網址的路徑段，而它是一般使用者填得到的欄位**
    # （`nextcloud_to`）。不做處理的話使用者可以填 `../../..` 把請求改寫成
    # 打同一台 Nextcloud 的別的端點，而且帶著 bot 的簽章標頭
    # （v1.14.31 對抗式驗證實測）。Talk 的 token 本來就是英數字。
    if not _re.fullmatch(r"[A-Za-z0-9_-]{1,64}", token or ""):
        raise RuntimeError("Nextcloud 對話 token 格式不正確（只能是英數字）")
    _post(f"{site}/ocs/v2.php/apps/spreed/api/v1/bot/{token}/message",
          json={"message": message},
          headers={
              "OCS-APIRequest": "true",
              "Content-Type": "application/json",
              "Accept": "application/json",
              "X-Nextcloud-Talk-Bot-Random": rnd,
              "X-Nextcloud-Talk-Bot-Signature": sig,
          })


def send_line(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    """LINE Messaging API 的 push message。

    **不是 LINE Notify** —— 那個服務已於 2025-03-31 終止。這裡需要官方帳號的
    channel access token 與收訊者的 user id（`U` 開頭的那串，不是 LINE ID）。
    """
    token = cfg.get("line_token")
    to = cfg.get("line_to")
    if not (token and to):
        raise RuntimeError("尚未設定 LINE channel access token / 收訊者 user id")
    _post("https://api.line.me/v2/bot/message/push",
          headers={"Authorization": f"Bearer {token}"},
          json={"to": to,
                "messages": [{"type": "text", "text": _msg(subject, text)[:4900]}]})


def send_webhook(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    """通用 webhook：POST JSON 到自訂 URL。方便串 n8n / 自寫端點。"""
    url = cfg.get("webhook_url")
    if not url:
        raise RuntimeError("尚未設定 Webhook URL")
    headers = {}
    tok = cfg.get("webhook_token")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    _post(url, json={"app": "jt-doc-tools", "subject": subject,
                     "text": text or ""}, headers=headers or None)


def send_email(cfg: dict[str, Any], subject: str, text: str | None,
               html: str | None = None,
               images: dict[str, bytes] | None = None) -> None:
    """SMTP 寄信。用標準函式庫的 smtplib（不引入新相依）。

    有 `html` 時送 `multipart/alternative`：讀信軟體挑得到哪個就顯示哪個。
    純文字版**一定要留** —— 命令列讀信、無障礙輔助、以及把信轉成摘要的服務
    都靠它，而且有些企業信件閘道會直接丟掉只有 HTML 的信。
    """
    import smtplib
    from email.message import EmailMessage

    to = cfg.get("email_to")
    if not to:
        raise RuntimeError("沒有收件者信箱")
    # 舊安裝沒有 smtp_mode 這個欄位 → 空字串一律當 auth，行為不變。
    send_mode = (cfg.get("smtp_mode") or "auth").lower()
    host = cfg.get("smtp_host")
    if send_mode != "direct" and not host:
        raise RuntimeError("尚未設定 SMTP 主機")
    port = int(cfg.get("smtp_port") or (25 if send_mode != "auth" else 587))
    mode = (cfg.get("smtp_tls") or "starttls").lower()
    sender = cfg.get("smtp_from") or cfg.get("smtp_username") or "jt-doc-tools"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text or subject)
    if html:
        # 順序有意義：`add_alternative` 後加的擺在後面，讀信軟體優先顯示最後一個。
        msg.add_alternative(html, subtype="html")
        if images:
            # 內嵌圖片要掛在**那一段 HTML 底下**（變成 multipart/related），
            # 不是掛在最外層 —— 掛錯地方的話 `cid:` 參照不到，圖會變成附件。
            part = msg.get_payload()[-1]
            for cid, data in images.items():
                if not data:
                    continue
                part.add_related(data, maintype="image", subtype="png",
                                 cid=f"<{cid}>")

    if send_mode == "direct":
        _smtp_direct_send(msg, to, cfg)
        return

    _smtp_deliver(msg, host, port, mode, cfg)


def _mx_hosts(domain: str) -> list[str]:
    """查一個網域的 MX 主機，依優先序排好。

    沒有 MX 紀錄時回網域本身 —— 這是 RFC 5321 的「隱含 MX」規則
    （很多小網域只有 A 紀錄）。查不到就丟例外讓上層報錯。
    """
    import dns.resolver

    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=_TIMEOUT)
    except Exception:
        # 沒有 MX（或查詢失敗）→ 退回網域本身，由連線階段決定成不成功
        return [domain]
    hosts = sorted(((int(r.preference), str(r.exchange).rstrip("."))
                    for r in answers), key=lambda x: x[0])
    return [h for _pref, h in hosts if h] or [domain]


def _smtp_direct_send(msg, to: str, cfg: dict) -> None:
    """jtdt 自己當 MTA，查 MX 直接送到收件方的伺服器。

    用在「對方的 mail server 依 IP 信任我們」的環境。與 relay 模式的差別是
    **不經過任何中繼主機** —— 收件人是哪個網域就直接連那個網域的 MX。

    **這條路有幾件事程式做不到，部署時要自己顧好**：
      * 反向 DNS（PTR）與 SPF 要設對，否則對方多半當垃圾信擋掉
      * 對外的 25 埠要通（很多 ISP 直接封鎖）
      * **沒有重試佇列**：對方暫時拒收（4xx）就是這封通知沒送到。
        通知在本專案是附屬品（送失敗只記錄、絕不影響作業本身），為了它
        另外做一套持久化佇列與退信處理不成比例 —— 需要保證送達請改用
        relay 模式，交給真正的 mail server 去重試。

    多個收件者分屬不同網域時，**逐一網域分開送** —— 一個網域失敗不影響其他。
    """
    import smtplib

    helo = (cfg.get("smtp_helo") or "").strip() or None
    port = int(cfg.get("smtp_port") or 25)

    recipients = [a.strip() for a in str(to).replace(";", ",").split(",") if a.strip()]
    by_domain: dict[str, list[str]] = {}
    for addr in recipients:
        if "@" not in addr:
            raise RuntimeError(f"收件信箱格式不對：{addr}")
        by_domain.setdefault(addr.rsplit("@", 1)[1].lower(), []).append(addr)

    errors: list[str] = []
    for domain, addrs in by_domain.items():
        last_err: Exception | None = None
        for host in _mx_hosts(domain):
            try:
                client = smtplib.SMTP(host, port, timeout=_TIMEOUT,
                                      local_hostname=helo)
                try:
                    client.ehlo()
                    # 對方支援就順手加密（機會性 TLS）—— 失敗不算錯，
                    # 直接投遞的對象是誰事先不知道，不能硬性要求。
                    try:
                        if client.has_extn("starttls"):
                            client.starttls()
                            client.ehlo()
                    except Exception:  # noqa: BLE001
                        pass
                    client.send_message(msg, to_addrs=addrs)
                finally:
                    try:
                        client.quit()
                    except Exception:  # noqa: BLE001
                        pass
                last_err = None
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        if last_err is not None:
            errors.append(f"{domain}: {type(last_err).__name__}")
    if errors:
        raise RuntimeError("直接投遞失敗（" + "；".join(errors) + "）")


def _smtp_deliver(msg, host: str, port: int, mode: str, cfg: dict) -> None:
    """連到一台 SMTP 主機把信送出去。

    `relay` 與 `auth` 走的是同一條路 —— 差別只在**有沒有帳號**：
    relay 是對方的 mail server 依來源 IP 信任我們，所以不需要認證。
    程式本來就是「帳號留空就不呼叫 login()」，所以 relay 不必特別處理，
    要做的是讓介面講清楚、預設埠給 25、以及可以指定 HELO 名稱。
    """
    import smtplib

    helo = (cfg.get("smtp_helo") or "").strip() or None
    if mode == "ssl":
        client = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT, local_hostname=helo)
    else:
        client = smtplib.SMTP(host, port, timeout=_TIMEOUT, local_hostname=helo)
    try:
        client.ehlo()
        if mode == "starttls":
            client.starttls()
            client.ehlo()
        user, pw = cfg.get("smtp_username"), cfg.get("smtp_password")
        if user:
            client.login(user, pw or "")
        client.send_message(msg)
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001
            pass


_SENDERS: dict[str, Callable[[dict, str, Optional[str]], None]] = {
    "email": send_email,
    "telegram": send_telegram,
    "slack": send_slack,
    "teams": send_teams,
    "discord": send_discord,
    "zulip": send_zulip,
    "nextcloud": send_nextcloud,
    "line": send_line,
    "webhook": send_webhook,
}


def send_one(cfg: dict[str, Any], channel: str, subject: str,
             text: str | None, html: str | None = None,
             images: dict[str, bytes] | None = None) -> None:
    """送單一管道（測試按鈕用）。失敗丟例外，讓管理員看得到原因。

    Email 給了 `html` 就寄 HTML 版（跟真的通知信同一個版面），其他管道照樣只收純文字。"""
    fn = _SENDERS.get(channel)
    if fn is None:
        raise RuntimeError(f"未知的通知管道：{channel}")
    if channel == "email" and html:
        fn(cfg, subject, text, html, images)
    else:
        fn(cfg, subject, text)


def broadcast(cfg: dict[str, Any], channels: list[str], subject: str,
              text: str | None = None, html: str | None = None,
              images: dict[str, bytes] | None = None) -> dict[str, str]:
    """送到多個管道，回 {管道: "" 或錯誤訊息}。

    並行送出 —— 最壞情況是最慢的單一管道（約一個逾時），而不是各管道相加。
    任何管道失敗都只記錄，不往外丟：通知是附屬品，**絕不能影響作業本身**。
    """
    channels = [c for c in channels if c in _SENDERS]
    if not channels:
        return {}
    results: dict[str, str] = {}

    def _one(ch: str) -> tuple[str, str]:
        try:
            # 只有 Email 吃得下 HTML；其餘管道（Slack / Telegram …）各有自己的
            # 格式，硬塞 HTML 進去會變成一堆標籤。
            if ch == "email" and html:
                _SENDERS[ch](cfg, subject, text, html, images)
            else:
                _SENDERS[ch](cfg, subject, text)
            return ch, ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("通知管道 %s 失敗：%s: %s", ch, type(exc).__name__, exc)
            return ch, f"{type(exc).__name__}: {exc}"[:200]

    with ThreadPoolExecutor(max_workers=min(len(channels), 8),
                            thread_name_prefix="notify") as ex:
        for ch, err in ex.map(_one, channels):
            results[ch] = err
    return results
