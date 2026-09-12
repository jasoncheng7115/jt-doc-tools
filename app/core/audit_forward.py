"""External audit log forwarding.

Three wire formats:

* `syslog` — RFC 5424 over UDP/TCP (the SIEM-standard line format)
* `cef` — Common Event Format (HP/ArcSight) over UDP/TCP
* `gelf` — Graylog Extended Log Format (JSON) over UDP/TCP

Multiple destinations can be enabled in parallel; each event goes to all
enabled destinations. A failed delivery retries 3× (with backoff).

**每個目的地各有自己的游標**（`forward_state` 的 key 是 `forward:<目的地 id>`）
—— 一個目的地送不出去，只有它自己的游標停在那裡，其他目的地照常前進，
而那些沒送成功的事件**下一輪會重送**。

> **原本不是這樣**（外部稽核 F08）：這段說明寫著「Bookmark per destination」
> 與「no events lost or duplicated」，但實作用的是**單一共用的
> `key='forward'`** —— 任何一個目的地失敗，整體游標照樣前移，那些事件對它
> 而言**永遠不會再送**，而且只有一筆 `audit_forward_failed` 留在本機。
> **註解承諾了實作沒做到的事**，這比沒有註解更糟。

**失敗的目的地會退避**（60 秒起、指數成長、上限 5 分鐘）：不退避的話，
一個掛掉的目的地會讓每一輪都在那裡重試 3 次加 sleep，把整個轉送迴圈拖垮。

**`audit_forward_failed` 本身不轉送**：它是本機的記帳。原本會被當成普通事件
撈出來轉送 → 又失敗 → 再產生一筆，**沒有任何使用者活動也會一直長**
（稽核實測連續三輪各多一筆）。同一個目的地的失敗事件也**有冷卻時間**，
不會每輪都寫一筆。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import audit_db, db

logger = logging.getLogger(__name__)


def _settings_path() -> Path:
    from ..config import settings
    return settings.data_dir / "log_forwarders.json"


_DEFAULTS: dict[str, Any] = {
    "destinations": [],   # list of dicts (see schema below)
    "updated_at": 0.0,
}

# Destination schema:
# {
#   "id": "stable-id",
#   "name": "human label",
#   "format": "syslog" | "cef" | "gelf",
#   "transport": "udp" | "tcp",
#   "host": "logserver.example.com",
#   "port": 514,
#   "enabled": true,
# }


_LOCK = threading.Lock()
_CACHE: Optional[dict[str, Any]] = None


def get() -> dict[str, Any]:
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            p = _settings_path()
            if p.exists():
                try:
                    _CACHE = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    _CACHE = json.loads(json.dumps(_DEFAULTS))
            else:
                _CACHE = json.loads(json.dumps(_DEFAULTS))
        return json.loads(json.dumps(_CACHE))


def save(new: dict[str, Any]) -> None:
    global _CACHE
    with _LOCK:
        new["updated_at"] = time.time()
        p = _settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(p)
        _CACHE = new


# ---------- formatters ----------

_HOSTNAME = socket.gethostname()


def _format_syslog(event: dict) -> bytes:
    """RFC 5424: <pri>1 ts hostname app procid msgid sd msg
    PRI = facility * 8 + severity. We use facility=16 (local0), severity=5
    (notice) → 16*8+5 = 133. SD field is "-" (no structured data)."""
    iso_ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(event["ts"]))
    pri = 16 * 8 + 5
    msg = json.dumps({
        "user": event["username"], "ip": event["ip"],
        "event": event["event_type"], "target": event["target"],
        "details": _safe_loads(event["details_json"]),
    }, ensure_ascii=False, separators=(",", ":"))
    line = f"<{pri}>1 {iso_ts} {_HOSTNAME} jtdt - {event['event_type']} - {msg}"
    return line.encode("utf-8")


def _format_cef(event: dict) -> bytes:
    """ArcSight CEF: CEF:0|Vendor|Product|Version|EventClassID|Name|Severity|Extension"""
    # Severity: 5 = Medium (we don't have a clean per-event severity model)
    extension = " ".join([
        f"src={_cef_escape(event['ip'])}",
        f"suser={_cef_escape(event['username'])}",
        f"act={_cef_escape(event['event_type'])}",
        f"target={_cef_escape(event['target'])}",
        f"msg={_cef_escape(event['details_json'])}",
        f"rt={int(event['ts'] * 1000)}",
    ])
    line = (f"CEF:0|JasonTools|jt-doc-tools|1|{event['event_type']}|"
            f"{event['event_type']}|5|{extension}")
    return line.encode("utf-8")


def _cef_escape(s: str) -> str:
    """CEF requires backslash-escape of \\, =, |. Newlines forbidden in
    extension values."""
    if not isinstance(s, str):
        s = str(s)
    return (s.replace("\\", "\\\\").replace("=", "\\=").replace("|", "\\|")
             .replace("\n", " ").replace("\r", " "))


def _format_gelf(event: dict) -> bytes:
    """GELF 1.1 的訊息本體（**不含訊框分隔符**，那是 `_frame()` 的事）。

    **分隔符不可以用換行**：Graylog 的 GELF TCP 規定訊息之間以 null byte
    （`\0`）分隔，而且訊息內不可以有原始換行。原本這裡直接 `+ "\n"`，
    在 TCP 上等於送出不合規的訊框（外部稽核 F07）。JSON 序列化會把字串裡的
    換行轉成 `\n` 兩個字元，所以本體天生就沒有原始換行。
    """
    payload = {
        "version": "1.1",
        "host": _HOSTNAME,
        "short_message": event["event_type"],
        "full_message": event["details_json"],
        "timestamp": event["ts"],
        "level": 5,                  # syslog notice
        "_user": event["username"],
        "_ip": event["ip"],
        "_target": event["target"],
        "_event_id": event["id"],
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


#: 每種「格式 × 傳輸」的訊框分隔符。
#:
#: **GELF TCP 一定是 null byte** —— Graylog 的規定，用換行送出去的訊框不合規
#: （外部稽核 F07；原本三種格式都在自己的 formatter 裡 `+ "\n"`）。
#: GELF UDP 一個封包就是一則訊息，**不加任何分隔符**。
#: syslog / CEF 維持換行（RFC 6587 的 non-transparent framing），
#: **刻意不動** —— 那兩種目前在客戶端是好的，不要在修 GELF 時順手改掉。
_TERMINATOR: dict[tuple[str, str], bytes] = {
    ("gelf", "tcp"): b"\0",
    ("gelf", "udp"): b"",
}
_DEFAULT_TERMINATOR = b"\n"


def _frame(fmt_name: str, transport: str, body: bytes) -> bytes:
    """把訊息本體包成該格式 / 傳輸該有的訊框。"""
    term = _TERMINATOR.get((fmt_name.lower(), transport.lower()),
                           _DEFAULT_TERMINATOR)
    return body + term


_FORMATTERS = {
    "syslog": _format_syslog,
    "cef": _format_cef,
    "gelf": _format_gelf,
}


def _safe_loads(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return s


# ---------- transport ----------

def _send(dest: dict, payload: bytes) -> None:
    """Send raw bytes to destination. Caller handles retry on exception."""
    host = dest["host"]
    port = int(dest["port"])
    transport = dest.get("transport", "udp").lower()
    if transport == "udp":
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2.0)
            s.sendto(payload, (host, port))
    elif transport == "tcp":
        with socket.create_connection((host, port), timeout=5.0) as s:
            s.sendall(payload)
    else:
        raise ValueError(f"unsupported transport: {transport}")


def _send_with_retry(dest: dict, payload: bytes, attempts: int = 3) -> None:
    last_exc: Optional[Exception] = None
    for i in range(attempts):
        try:
            _send(dest, payload)
            return
        except Exception as e:
            last_exc = e
            time.sleep(0.5 * (i + 1))   # 0.5s, 1s, 1.5s
    raise last_exc or RuntimeError("send failed without exception (?)")


# ---------- worker ----------

_WORKER_THREAD: Optional[threading.Thread] = None
_WORKER_STOP = threading.Event()
_POLL_INTERVAL = 5.0


def start_worker() -> None:
    """Start the forwarding worker thread (idempotent)."""
    global _WORKER_THREAD
    with _LOCK:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            return
        _WORKER_STOP.clear()
        _WORKER_THREAD = threading.Thread(
            target=_worker_loop, name="audit-forward", daemon=True,
        )
        _WORKER_THREAD.start()


def stop_worker() -> None:
    _WORKER_STOP.set()
    if _WORKER_THREAD is not None:
        _WORKER_THREAD.join(timeout=5)


def _worker_loop() -> None:
    while not _WORKER_STOP.is_set():
        try:
            _drain_once()
        except Exception:
            logger.exception("audit-forward loop error")
        # Sleep but wake quickly on stop signal
        _WORKER_STOP.wait(_POLL_INTERVAL)


#: 舊版的單一共用游標。升級後**要拿它當每個目的地的起點**，否則第一輪會把
#: 整個稽核資料庫重送一遍給每個目的地。
_LEGACY_KEY = "forward"

#: 失敗退避：60 秒起、每次加倍、上限 5 分鐘。存在記憶體裡就好 —— 重啟後
#: 重試一次沒有壞處，而寫進資料庫會讓「目的地修好了」這件事更難被發現。
_BACKOFF_START = 60.0
_BACKOFF_MAX = 300.0
#: 同一個目的地多久才再寫一筆 `audit_forward_failed`（避免灌爆本機稽核）。
_FAIL_LOG_COOLDOWN = 300.0

#: dest_id -> {"until": 下次可嘗試的時間, "delay": 目前的退避秒數,
#:             "logged_at": 上次寫失敗記錄的時間}
_DEST_STATE: dict[str, dict] = {}


def _dest_key(dest: dict) -> str:
    """這個目的地的游標 key。沒有 id 的舊設定退回用「名稱 + 主機 + 埠」。"""
    did = str(dest.get("id") or "").strip()
    if not did:
        did = f"{dest.get('name', '')}@{dest.get('host', '')}:{dest.get('port', '')}"
    return f"forward:{did}"


def _bookmark_get(conn, key: str) -> int:
    row = conn.execute(
        "SELECT last_forwarded_id FROM forward_state WHERE key=?", (key,)
    ).fetchone()
    if row:
        return row["last_forwarded_id"]
    # 這個目的地還沒有自己的游標 → 用舊的共用游標當起點（見 _LEGACY_KEY）
    legacy = conn.execute(
        "SELECT last_forwarded_id FROM forward_state WHERE key=?", (_LEGACY_KEY,)
    ).fetchone()
    return legacy["last_forwarded_id"] if legacy else 0


def _bookmark_set(conn, key: str, new_id: int) -> None:
    with db.tx(conn):
        conn.execute(
            "INSERT INTO forward_state(key, last_forwarded_id, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "last_forwarded_id=excluded.last_forwarded_id, "
            "updated_at=excluded.updated_at",
            (key, new_id, time.time()),
        )


def _note_failure(dest: dict, event_id: int, exc: Exception) -> None:
    """記一筆失敗，但**同一個目的地有冷卻時間**。

    原本每一筆送不出去的事件都寫一筆 `audit_forward_failed`，而那些失敗事件
    本身又會被撈出來轉送、再失敗、再產生一筆 —— **沒有任何使用者活動，本機
    稽核也會一直長**。現在失敗事件不轉送（見 `_drain_for`），而且同一個目的地
    在冷卻時間內只記一筆。
    """
    st = _DEST_STATE.setdefault(dest_id := _dest_key(dest), {})
    now = time.time()
    if now - float(st.get("logged_at") or 0) < _FAIL_LOG_COOLDOWN:
        return
    st["logged_at"] = now
    audit_db.log_event(
        "audit_forward_failed",
        target=str(dest.get("id") or ""),
        details={"event_id": event_id,
                 "destination": dest.get("name", ""),
                 "bookmark_key": dest_id,
                 "error": f"{type(exc).__name__}: {exc}"},
    )


def _drain_for(conn, dest: dict) -> None:
    """把這個目的地還沒送出去的事件送完；送不掉就**不前移它的游標**。"""
    key = _dest_key(dest)
    st = _DEST_STATE.setdefault(key, {})
    now = time.time()
    if now < float(st.get("until") or 0):
        return                      # 還在退避中
    fmt = _FORMATTERS.get(dest.get("format", ""))
    if not fmt:
        return

    last = _bookmark_get(conn, key)
    rows = conn.execute(
        "SELECT id, ts, username, ip, event_type, target, details_json "
        "FROM audit_events WHERE id > ? AND event_type <> ? "
        "ORDER BY id ASC LIMIT 500",
        (last, "audit_forward_failed"),
    ).fetchall()
    if not rows:
        return

    sent_upto = last
    for row in rows:
        event = dict(row)
        try:
            payload = _frame(dest.get("format", ""),
                             dest.get("transport", "udp"), fmt(event))
            _send_with_retry(dest, payload)
        except Exception as exc:
            # 這一筆送不掉 → 游標停在**前一筆**，下一輪從這裡再試。
            _note_failure(dest, event["id"], exc)
            delay = min(_BACKOFF_MAX,
                        float(st.get("delay") or 0) * 2 or _BACKOFF_START)
            st["delay"] = delay
            st["until"] = time.time() + delay
            break
        sent_upto = event["id"]
    else:
        st["delay"] = 0.0           # 整批都送成功 → 退避歸零
        st["until"] = 0.0

    if sent_upto > last:
        _bookmark_set(conn, key, sent_upto)


def _drain_once() -> None:
    cfg = get()
    enabled = [d for d in cfg.get("destinations", []) if d.get("enabled")]
    if not enabled:
        return
    conn = audit_db.conn()
    for dest in enabled:
        # **一個目的地掛掉不可以影響其他目的地** —— 每個都有自己的游標、
        # 自己的退避（外部稽核 F08）。
        try:
            _drain_for(conn, dest)
        except Exception:
            logger.exception("audit-forward drain failed for one destination")
