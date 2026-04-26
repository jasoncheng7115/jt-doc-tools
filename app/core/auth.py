"""Unified authentication entry point.

Picks the right backend (local / ldap / ad) per current auth_settings.
Both backends raise the same `AuthError` so the route layer can show one
"帳號或密碼錯誤" message regardless of which backend rejected.

Local accounts ALWAYS work as a rescue path even when backend=ldap/ad —
the bootstrap admin must remain accessible if the directory goes down.
The login form lets the user pick which realm to authenticate against;
default is the primary backend, but `realm='local'` always tries local.
"""
from __future__ import annotations

from . import auth_settings


class AuthError(Exception):
    pass


def available_realms() -> list[dict]:
    """Return list of realm choices to show on the login form.

    Always includes 'local'. Adds 'ldap' or 'ad' when one is configured
    as the primary backend (since service account credentials are required
    to query the directory and we don't want to expose another realm by
    accident).
    """
    realms = [{"id": "local", "label": "本機帳號"}]
    backend = auth_settings.get_backend()
    if backend == "ldap":
        realms.insert(0, {"id": "ldap", "label": "LDAP"})
    elif backend == "ad":
        realms.insert(0, {"id": "ad", "label": "Active Directory"})
    return realms


def default_realm() -> str:
    """The realm pre-selected on the login form."""
    backend = auth_settings.get_backend()
    if backend in ("ldap", "ad"):
        return backend
    return "local"


def authenticate(username: str, password: str, *,
                 ip: str = "", realm: str = "") -> dict:
    """Authenticate against the chosen realm.

    - realm='' or 'auto' → use the primary backend
    - realm='local'      → always try local (rescue path; works even when
                            primary backend is LDAP/AD)
    - realm='ldap'/'ad'  → must match primary backend, else rejected
    """
    backend = auth_settings.get_backend()
    if backend == "off":
        raise AuthError("認證未啟用")

    chosen = (realm or "").lower() or "auto"
    if chosen == "auto":
        chosen = backend

    if chosen == "local":
        from . import auth_local
        try:
            return auth_local.authenticate(username, password, ip=ip)
        except auth_local.AuthError as e:
            raise AuthError(str(e))

    if chosen in ("ldap", "ad"):
        # Refuse to use an LDAP realm when the backend isn't ldap/ad —
        # otherwise an attacker could bypass local-only setups.
        if backend not in ("ldap", "ad"):
            raise AuthError("此認證領域未啟用")
        from . import auth_ldap
        try:
            return auth_ldap.authenticate(username, password, ip=ip)
        except auth_ldap.AuthError as e:
            raise AuthError(str(e))

    raise AuthError("無效的認證領域")
