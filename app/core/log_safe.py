"""Log-injection defence — strip CR/LF/NUL from user-supplied values
before they end up in `logger.info(... %s ...)` arguments.

Why: an attacker who can put `\\n[CRITICAL] root: backdoor installed`
into a logged username / filename / LDAP DN can fake log lines that
mislead an analyst (or trick a SIEM rule). Closes CodeQL alerts
#21–#29 (`py/log-injection`).

Usage::

    from app.core.log_safe import safe_log

    logger.info("user %s did X", safe_log(username))
    logger.warning("font %s missing", safe_log(font_name, max_len=80))

The helper is intentionally thin — it does NOT JSON-encode, base64,
or otherwise mangle the value beyond what's needed for log safety.
Real attacker payloads still appear in audit log AS-IS but cannot
forge new log lines."""
from __future__ import annotations


# Control chars that break log line boundaries / parsers.
_BAD = str.maketrans({
    "\n": " ",
    "\r": " ",
    "\0": " ",
    "\t": " ",
    "\v": " ",
    "\f": " ",
})


def safe_log(value, max_len: int = 200) -> str:
    """Coerce ``value`` to a single-line, length-bounded str safe for
    `logger.*("... %s ...", safe_log(x))` interpolation.

    - non-str → ``repr(value)`` (so dicts / bytes show with their type)
    - CR/LF/NUL/etc → space
    - longer than ``max_len`` → truncated + '…' suffix
    """
    if not isinstance(value, str):
        s = repr(value)
    else:
        s = value
    s = s.translate(_BAD)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s
