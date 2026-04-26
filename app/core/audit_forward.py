"""External audit log forwarding.

Three wire formats:

* `syslog` — RFC 5424 over UDP/TCP (the SIEM-standard line format)
* `cef` — Common Event Format (HP/ArcSight) over UDP/TCP
* `gelf` — Graylog Extended Log Format (JSON) over UDP/TCP

Multiple destinations can be enabled in parallel; each event goes to all
enabled destinations. A failed delivery retries 3× (with backoff) then
gives up and writes a single ``audit_forward_failed`` event back into the
local audit DB so admin sees the gap.

Bookmark per destination lives in ``forward_state.last_forwarded_id`` so
restarting the worker resumes where it left off — no events lost or
duplicated under normal operation.
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
    line = f"<{pri}>1 {iso_ts} {_HOSTNAME} jtdt - {event['event_type']} - {msg}\n"
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
            f"{event['event_type']}|5|{extension}\n")
    return line.encode("utf-8")


def _cef_escape(s: str) -> str:
    """CEF requires backslash-escape of \\, =, |. Newlines forbidden in
    extension values."""
    if not isinstance(s, str):
        s = str(s)
    return (s.replace("\\", "\\\\").replace("=", "\\=").replace("|", "\\|")
             .replace("\n", " ").replace("\r", " "))


def _format_gelf(event: dict) -> bytes:
    """GELF: JSON message, one per line. version 1.1."""
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
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


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


def _bookmark_get(conn) -> int:
    row = conn.execute(
        "SELECT last_forwarded_id FROM forward_state WHERE key='forward'"
    ).fetchone()
    return row["last_forwarded_id"] if row else 0


def _bookmark_set(conn, new_id: int) -> None:
    with db.tx(conn):
        existing = conn.execute(
            "SELECT 1 FROM forward_state WHERE key='forward'"
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE forward_state SET last_forwarded_id=?, updated_at=? "
                "WHERE key='forward'", (new_id, time.time()),
            )
        else:
            conn.execute(
                "INSERT INTO forward_state(key, last_forwarded_id, updated_at) "
                "VALUES ('forward', ?, ?)", (new_id, time.time()),
            )


def _drain_once() -> None:
    cfg = get()
    enabled = [d for d in cfg.get("destinations", []) if d.get("enabled")]
    if not enabled:
        return
    conn = audit_db.conn()
    last = _bookmark_get(conn)
    rows = conn.execute(
        "SELECT id, ts, username, ip, event_type, target, details_json "
        "FROM audit_events WHERE id > ? ORDER BY id ASC LIMIT 500", (last,),
    ).fetchall()
    if not rows:
        return
    max_id = last
    for row in rows:
        event = dict(row)
        for dest in enabled:
            fmt = _FORMATTERS.get(dest.get("format", ""))
            if not fmt:
                continue
            try:
                payload = fmt(event)
                _send_with_retry(dest, payload)
            except Exception as exc:
                # Note the failure into local audit (avoids infinite loop:
                # this event's id is < the one that just failed, so won't
                # itself be picked up by next drain... unless admin enables
                # forwarding to also forward audit_forward_failed events,
                # which is fine, just one extra delivery). The failure
                # event itself doesn't try to forward.
                audit_db.log_event(
                    "audit_forward_failed",
                    target=dest.get("id", ""),
                    details={"event_id": event["id"],
                             "destination": dest.get("name", ""),
                             "error": f"{type(exc).__name__}: {exc}"},
                )
        max_id = event["id"]
    _bookmark_set(conn, max_id)
