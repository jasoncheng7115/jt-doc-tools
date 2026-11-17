"""通知設定 —— 管理員的管道憑證（`data/notify_settings.json`）＋ 每位使用者的
收訊偏好（`data/notify_prefs/<key>.json`）。

## 為什麼拆成兩層

憑證（SMTP 帳密、bot token、webhook URL）是**主機層級**的東西，只有管理員該碰；
但「要不要收通知、寄到哪個信箱」是**每個人自己**的事。兩者混在一起會變成：不是
所有人共用一個信箱，就是每個人都得知道 SMTP 密碼。

| 層 | 誰設定 | 內容 |
|---|---|---|
| 管道憑證 | 管理員 | SMTP 主機、bot token、webhook URL |
| 收訊偏好 | 使用者自己 | 開不開、要哪些管道、自己的信箱 / Telegram chat id / LINE user id |

Slack / Teams / Discord / Zulip / Nextcloud 這類**團隊頻道**的目的地本來就在管理員
那層（大家送到同一個頻道）；Email / Telegram / LINE 屬個人管道，目的地在使用者那層。

## 祕密加密

比照 `sso_settings`：用 Fernet 加密，金鑰取自同一份 `data/.session_secret`
（只需保護一份祕密，不是兩份）。`get()` 預設把祕密遮罩後才給 admin UI；服務層要
真值時傳 `reveal=True`。

**Webhook URL 也算祕密** —— Slack / Teams / Discord 的 incoming webhook URL 本身
就等同憑證，任何人拿到就能往那個頻道貼文，所以一樣加密、一樣遮罩。
"""
from __future__ import annotations

import base64
import json
import re
import logging
import threading
import time
from pathlib import Path
from . import atomic_json
from typing import Any, Optional

logger = logging.getLogger("app.notify_settings")

_LOCK = threading.RLock()
_CACHE: Optional[dict] = None

#: admin UI 送回這個值代表「這個祕密沒有改，沿用原本的」
SECRET_KEPT = "__JTDT_SECRET_KEPT__"
_MASK = "••••••••"

#: (管道, 欄位) —— 需要加密存放的欄位。**新增祕密欄位務必加進來**，
#: 漏掉就會以明文躺在 data/notify_settings.json 裡。
SECRET_FIELDS: set[tuple[str, str]] = {
    ("email", "smtp_password"),
    ("telegram", "telegram_token"),
    ("slack", "slack_webhook"),
    ("teams", "teams_webhook"),
    ("discord", "discord_webhook"),
    ("zulip", "zulip_api_key"),
    ("nextcloud", "nextcloud_secret"),
    ("line", "line_token"),
    ("webhook", "webhook_token"),
}

_CHANNEL_DEFAULTS: dict[str, dict] = {
    # smtp_mode：外部 SMTP 帳號（auth）/ 內部轉送主機（relay，依 IP 信任、
    #   免帳密）/ 直接投遞（direct，自己查 MX 送到收件方）。
    #   舊安裝沒有這個欄位 → 讀出來是空字串，一律當 auth 處理。
    "email": {"enabled": False, "smtp_mode": "auth",
              "smtp_host": "", "smtp_port": 587,
              "smtp_tls": "starttls", "smtp_username": "",
              "smtp_password": "", "smtp_from": "", "smtp_helo": ""},
    "telegram": {"enabled": False, "telegram_token": ""},
    "slack": {"enabled": False, "slack_webhook": ""},
    "teams": {"enabled": False, "teams_webhook": ""},
    "discord": {"enabled": False, "discord_webhook": ""},
    "zulip": {"enabled": False, "zulip_site": "", "zulip_bot_email": "",
              "zulip_api_key": "", "zulip_stream": "", "zulip_topic": ""},
    "nextcloud": {"enabled": False, "nextcloud_url": "",
                  "nextcloud_token": "", "nextcloud_secret": ""},
    "line": {"enabled": False, "line_token": ""},
    "webhook": {"enabled": False, "webhook_url": "", "webhook_token": ""},
}

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    # 只有跑超過這麼久的作業才通知 —— 兩秒就跑完的合併不需要打擾任何人，
    # 而通知的價值正是「久到你已經去做別的事了」
    "min_seconds": 60,
    "notify_on": ["done", "error"],
    # 對外可點的站台網址（例如 https://doc.example.com）。填了就一律用它組通知裡
    # 「我的作業」的連結；沒填時用送出作業那一刻使用者瀏覽器所在的網址
    # （job_notify._site_url），兩個都沒有才不放連結 —— 放一個指向 localhost
    # 的連結比沒有連結更糟。
    "site_url": "",
    "channels": {},
}

#: 個人管道 —— 目的地**必須**由使用者填，沒填就不送
PERSONAL_CHANNELS = ("email", "telegram", "line")
#: 兩用管道 —— 使用者填了自己的目的地就送私訊，沒填就送管理員設定的團隊頻道。
#: Zulip 與 Nextcloud Talk 不必換憑證就能私訊（見 notify_channels 的說明）；
#: Slack / Teams / Discord 的 incoming webhook URL 天生綁死一個頻道，要私訊得改用
#: bot token，那是另一種憑證型態，因此不在這裡。
DUAL_CHANNELS = ("zulip", "nextcloud")
_PERSONAL_FIELD = {"email": "email_to", "telegram": "telegram_chat_id",
                   "line": "line_to"}
_DUAL_FIELD = {"zulip": "zulip_to", "nextcloud": "nextcloud_to"}

#: **送給本人**的管道 —— 目的地是使用者自己的信箱 / 帳號，別人看不到。
#: 只有這些可以在「使用者從未設定過」時預設開啟；Slack / Teams / Discord /
#: Webhook 的目的地是團隊共用頻道，預設打開就是把檔名公開給全公司。
_PERSONAL_CHANNELS = {"email", "telegram", "line"}


def _path() -> Path:
    from ..config import settings
    return settings.data_dir / "notify_settings.json"


def _prefs_dir() -> Path:
    from ..config import settings
    return settings.data_dir / "notify_prefs"


# ---------- 加密 ----------

def _fernet():
    from cryptography.fernet import Fernet

    from . import auth_settings
    return Fernet(base64.urlsafe_b64encode(auth_settings._ensure_secret()))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("notify_settings: 祕密解密失敗（金鑰換過？）")
        return ""


# ---------- 讀寫 ----------

def _blank() -> dict:
    cfg = json.loads(json.dumps(_DEFAULTS))
    cfg["channels"] = {k: dict(v) for k, v in _CHANNEL_DEFAULTS.items()}
    return cfg


def _load() -> dict:
    cfg = _blank()
    p = _path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("notify_settings.json 讀取失敗，改用預設：%s", e)
            return cfg
        for k in ("enabled", "min_seconds", "notify_on", "site_url"):
            if k in raw:
                cfg[k] = raw[k]
        for ch, defaults in _CHANNEL_DEFAULTS.items():
            got = (raw.get("channels") or {}).get(ch) or {}
            cfg["channels"][ch] = {**defaults,
                                   **{k: got[k] for k in got if k in defaults}}
    return cfg


def get(*, reveal: bool = False) -> dict:
    """讀設定。預設把祕密遮罩（給 admin UI 顯示）；服務層要真值傳 reveal=True。"""
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            _CACHE = _load()
        cfg = json.loads(json.dumps(_CACHE))
    for ch, field in SECRET_FIELDS:
        sec = cfg["channels"].get(ch) or {}
        if not sec.get(field):
            continue
        sec[field] = decrypt_secret(sec[field]) if reveal else _MASK
    return cfg


def save(new: dict) -> dict:
    """存設定。祕密欄位為 SECRET_KEPT 或遮罩字串時代表「沒改」，沿用原值。"""
    global _CACHE
    with _LOCK:
        cur = _CACHE if _CACHE is not None else _load()
        cfg = json.loads(json.dumps(cur))
        if "enabled" in new:
            cfg["enabled"] = bool(new["enabled"])
        if "min_seconds" in new:
            cfg["min_seconds"] = max(0, min(int(new["min_seconds"] or 0), 86400))
        if isinstance(new.get("notify_on"), list):
            cfg["notify_on"] = [s for s in new["notify_on"]
                                if s in ("done", "error")]
        if "site_url" in new:
            # 只收 http(s) 的絕對網址。這個值會變成通知信裡的可點連結，
            # 沒有把關的話等於讓管理員（或任何能改設定的人）在信裡放任意連結。
            v = _strip_ctrl(str(new["site_url"] or ""))[:300]
            cfg["site_url"] = v if v.startswith(("http://", "https://")) else ""
        for ch, defaults in _CHANNEL_DEFAULTS.items():
            incoming = (new.get("channels") or {}).get(ch)
            if not isinstance(incoming, dict):
                continue
            tgt = cfg["channels"].setdefault(ch, dict(defaults))
            for field in defaults:
                if field not in incoming:
                    continue
                val = incoming[field]
                if (ch, field) in SECRET_FIELDS:
                    # 沒改就別動 —— UI 顯示的是遮罩，原樣送回來不該把祕密洗掉
                    if val in (SECRET_KEPT, _MASK, None):
                        continue
                    tgt[field] = encrypt_secret(str(val)) if val else ""
                elif field == "enabled":
                    tgt[field] = bool(val)
                elif field == "smtp_port":
                    tgt[field] = max(1, min(int(val or 587), 65535))
                elif field == "smtp_mode":
                    # 白名單 —— 沒擋的話可以塞任意字串進設定檔，而寄送端是
                    # 用字串比對決定走哪條路，未知值會安靜落到預設分支。
                    v = str(val or "auth").lower()
                    tgt[field] = v if v in ("auth", "relay", "direct") else "auth"
                elif field == "smtp_helo":
                    # HELO 會原樣寫進 SMTP 指令 —— 控制字元與空白一律去掉，
                    # 否則等於讓能改設定的人往協議裡插東西。
                    tgt[field] = _strip_ctrl(str(val or "")).replace(" ", "")[:255]
                else:
                    tgt[field] = str(val or "")
        cfg["updated_at"] = time.time()
        atomic_json.write_json(_path(), cfg, mode=0o600)   # 內含憑證
        _CACHE = cfg
    return get()


def invalidate_cache() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None


def is_enabled() -> bool:
    return bool(get().get("enabled"))


def enabled_channels() -> list[str]:
    """管理員已啟用且設定完整的管道。"""
    cfg = get(reveal=True)
    if not cfg.get("enabled"):
        return []
    out = []
    for ch, c in (cfg.get("channels") or {}).items():
        if c.get("enabled") and _configured(ch, c):
            out.append(ch)
    return out


def _configured(ch: str, c: dict) -> bool:
    """該管道的必填欄位是否齊全（個人管道的目的地由使用者提供，不算在內）。"""
    need = {
        "email": ["smtp_host"],
        "telegram": ["telegram_token"],
        "slack": ["slack_webhook"],
        "teams": ["teams_webhook"],
        "discord": ["discord_webhook"],
        # 頻道不列為必填 —— 管理員可以只提供憑證，讓每個人各自收私訊
        "zulip": ["zulip_site", "zulip_bot_email", "zulip_api_key"],
        "nextcloud": ["nextcloud_url", "nextcloud_token", "nextcloud_secret"],
        "line": ["line_token"],
        "webhook": ["webhook_url"],
    }.get(ch, [])
    return all(c.get(f) for f in need)


# ---------- 每位使用者的收訊偏好 ----------

_PREF_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "channels": [],
    #: 使用者有沒有自己選過管道（不是「偏好檔存不存在」—— 那個檔案會因為
    #: 點開通知而被建立）。見 has_chosen_channels。
    "channels_set": False,
    "email_to": "",
    "telegram_chat_id": "",
    "line_to": "",
    "zulip_to": "",          # 自己的 Zulip email → 收私訊（留空＝用帳號的信箱；帳號沒有信箱才走團隊頻道）
    # Zulip 使用者編號（recipient_user_id）。有填就**優先**用它私訊：Zulip 的 email 隱私設定會讓機器人只看到
    # `user9@<站台>` 這種代用地址，用真正的信箱對不上（Zulip 維護人員 2026-10-18 要求）
    "zulip_user_id": "",
    # 私訊 Bot 綁定的結果（`zulip_link`）：{"site", "user_id", "name", "linked_at"}。只由綁定流程寫，
    # `save_prefs` 不收這個欄位（使用者不可以自己填別人的編號）
    "zulip_link": None,
    "nextcloud_to": "",      # 自己的一對一對話 token（留空＝走群組對話）
    # 站內通知「上次查看」的時間戳 —— 未讀數由它與作業的完成時間推導，
    # 不逐筆記已讀狀態（見 /api/my/inbox）
    "inbox_seen_at": 0.0,
    # 首頁「有新版」提示條按了「這一版不再提醒」的版本（`update_check`）；出下一版才會再出現
    "update_dismissed": "",
}


def _pref_path(key: str) -> Path:
    from .safe_paths import sanitize_filename
    return _prefs_dir() / sanitize_filename(f"{key}.json")


def has_chosen_channels(key: str) -> bool:
    """這位使用者有沒有**自己選過**要收哪些管道。

    用來分辨兩種完全不同的狀態：
      * 從未選過 → 依管理員開好的管道自動收（見 `resolve_for_user`）
      * 選過、但一個都沒勾 → 他明確表示不要收，尊重他
    兩者的 `channels` 都是空陣列，光看值分不出來。

    **不可以用「偏好檔存不存在」來判斷** —— 那個檔案會因為別的原因被建立：
    使用者只是點開站內通知（寫入 `inbox_seen_at`）就會產生它。實測就是這樣：
    帳號有信箱、管理員也開好了 Email，卻因為檔案已存在而被當成「他選擇不收」。
    """
    return bool(get_prefs(key).get("channels_set"))


def get_prefs(key: str) -> dict:
    out = dict(_PREF_DEFAULTS)
    p = _pref_path(key)
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                out.update({k: raw[k] for k in raw if k in _PREF_DEFAULTS})
        except (OSError, ValueError):
            pass
    out["channels"] = [c for c in (out.get("channels") or [])
                       if c in _CHANNEL_DEFAULTS]
    return out


def _strip_ctrl(v: str) -> str:
    """移除控制字元（換行 / 歸位 / NUL 等）。"""
    return "".join(ch for ch in v if ch == "\t" or ord(ch) >= 0x20).strip()


def save_prefs(key: str, new: dict) -> dict:
    cur = get_prefs(key)
    if "enabled" in new:
        cur["enabled"] = bool(new["enabled"])
    if isinstance(new.get("channels"), list):
        cur["channels"] = [c for c in new["channels"] if c in _CHANNEL_DEFAULTS]
        # 記住「他做過選擇」。之後即使一個都沒勾也不會被當成「還沒設定」而
        # 自動打開（見 has_chosen_channels）。
        cur["channels_set"] = True
    # 刻意**不收** email_to：通知信箱只認帳號上的那一個（見 resolve_for_user）。
    # 擋在這裡而不是只把 UI 藏起來 —— 否則自己組請求還是改得到。
    if "zulip_user_id" in new:
        uid = str(new["zulip_user_id"] or "").strip()
        cur["zulip_user_id"] = uid if uid.isdigit() and len(uid) <= 12 else ""
    for f in ("telegram_chat_id", "line_to", "zulip_to", "nextcloud_to"):
        if f in new:
            # 去掉控制字元再存。`email_to` 會被放進郵件標頭，含換行的值會讓
            # Python 的 email 模組在寄送當下丟 ValueError —— 那不是漏洞（標頭
            # 注入被擋住了），但使用者只會看到「通知都沒收到」而找不到原因。
            # 在存檔時就清乾淨，他填的東西才會真的能用。
            cur[f] = _strip_ctrl(str(new[f] or ""))[:200]
    if "update_dismissed" in new:
        v = str(new["update_dismissed"] or "")
        cur["update_dismissed"] = v if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", v) else ""
    if "inbox_seen_at" in new:
        try:
            cur["inbox_seen_at"] = float(new["inbox_seen_at"] or 0)
        except (TypeError, ValueError):
            pass
    d = _prefs_dir()
    d.mkdir(parents=True, exist_ok=True)
    atomic_json.write_json(_pref_path(key), cur)
    return cur


def get_zulip_link(key: str) -> Optional[dict]:
    """這個使用者綁定的 Zulip 帳號（沒有回 None）。"""
    link = get_prefs(key).get("zulip_link")
    return link if isinstance(link, dict) and link.get("user_id") else None


def save_zulip_link(key: str, link: Optional[dict]) -> None:
    """只給綁定流程（`zulip_link.confirm`）與解除綁定用 —— 不經過 `save_prefs` 的欄位白名單。"""
    cur = get_prefs(key)
    cur["zulip_link"] = link
    d = _prefs_dir()
    d.mkdir(parents=True, exist_ok=True)
    atomic_json.write_json(_pref_path(key), cur)


def _account_email(key: str) -> str:
    """帳號上的信箱。

    key 沿用工作區的鍵：認證開啟時是 `u<使用者 id>`（見
    `workspace.key_for_user_id`），未啟用認證時是單一共用鍵 —— 那時沒有「帳號」
    這個概念，自然也沒有帳號信箱。
    """
    if not key or not key.startswith("u") or not key[1:].isdigit():
        return ""
    uid = int(key[1:])
    try:
        from . import auth_db
        row = auth_db.conn().execute(
            "SELECT email FROM users WHERE id=?", (uid,)).fetchone()
        return (row["email"] or "") if row else ""
    except Exception:  # noqa: BLE001 — 取不到就當沒有
        return ""


def resolve_for_user(key: str) -> tuple[list[str], dict]:
    """算出「這位使用者實際會收到哪些管道」＋ 合併後的設定（含真值祕密）。

    管理員的憑證 + 使用者的目的地。個人管道（Email / Telegram / LINE）沒填目的地
    就不會送 —— 這不是錯誤，只是他還沒填。
    """
    cfg = get(reveal=True)
    prefs = get_prefs(key)
    if not cfg.get("enabled") or not prefs.get("enabled"):
        return [], {}
    avail = set(enabled_channels())
    if has_chosen_channels(key):
        chosen = [c for c in prefs["channels"] if c in avail]
    else:
        # **從未自己設定過 → 預設就收**。
        #
        # 原本預設是空陣列，於是實際情況變成：管理員開了通知、設好 SMTP、
        # 信箱也從目錄同步進來了，使用者卻**什麼都收不到** —— 因為還缺一個
        # 沒人知道要去勾的核取方塊，而且畫面上沒有任何地方說「你不會收到」。
        # 這是實際回報的問題（跑完 2 分 40 秒的轉檔沒收到信）。
        #
        # 管理員是刻意開啟這個功能的，信箱也來自公司目錄 —— 寄一封「你的檔案
        # 好了」是預期中的行為。不想收的人取消勾選即可（存過之後就以他的選擇為準）。
        #
        # **但只限「送給本人」的管道**。上面那段理由講的是 Email（個人信箱），
        # 而 Slack / Teams / Discord 這類的目的地是**團隊共用頻道** —— 預設
        # 打開等於把「[完成] 文件去識別化：2026年度_資遣名單_王小明等12人.pdf」
        # 這種標題貼到全公司看得到的地方，而使用者根本不知道有這回事
        # （v1.14.31 對抗式驗證實測）。共用頻道一律要本人明確勾選。
        chosen = sorted(avail & _PERSONAL_CHANNELS)
    merged: dict[str, Any] = {}
    for ch in chosen:
        merged.update(cfg["channels"].get(ch) or {})
    # 個人管道的目的地來自使用者
    # 收件信箱**只認帳號上的那一個**。
    #
    # 原本還接受「使用者在通知設定自己填一個覆寫」，但那讓同一件事有兩個欄位、
    # 兩個地方 —— 使用者不知道哪個才算數，管理員也無從得知通知實際寄去哪。
    # 現在只有一個來源：
    #   * 目錄帳號（AD / LDAP / SSO）→ 由來源同步，使用者與管理員都不改
    #   * 本機帳號 → 本人在「我的帳號」自己維護（或管理員代填）
    # 沒有信箱就不寄 —— 那不是錯誤，只是還沒設定。
    merged["email_to"] = _account_email(key) or ""
    merged["telegram_chat_id"] = prefs.get("telegram_chat_id") or ""
    merged["line_to"] = prefs.get("line_to") or ""
    # 兩用管道：填了就私訊。Zulip 沒填就私訊到**帳號上的信箱**（使用者 2026-10-18：預設要抓帳號本身的，
    # 使用者可以改）；帳號沒有信箱才沿用管理員的團隊頻道
    merged["zulip_to"] = prefs.get("zulip_to") or merged["email_to"] or ""
    merged["zulip_user_id"] = prefs.get("zulip_user_id") or ""
    # 綁定過的 Zulip 帳號最優先（站台要是同一個 —— 管理員換了站台，舊的綁定就不能用）
    link = prefs.get("zulip_link") or None
    z_site = str(((cfg.get("channels") or {}).get("zulip") or {}).get("zulip_site") or "").rstrip("/")
    if isinstance(link, dict) and link.get("user_id") and str(link.get("site") or "").rstrip("/") == z_site:
        merged["zulip_user_id"] = str(int(link["user_id"]))
    merged["nextcloud_to"] = prefs.get("nextcloud_to") or ""
    usable = []
    for ch in chosen:
        field = _PERSONAL_FIELD.get(ch)
        if field and not merged.get(field):
            continue          # 個人管道但沒填目的地 → 跳過
        usable.append(ch)
    return usable, merged
