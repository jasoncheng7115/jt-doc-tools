"""Tests for app.core.passwords (scrypt hashing + policy).

Test list:
  - hash_password produces scrypt$N=...,r=...,p=...$<salt>$<hash> format
  - hash_password salt is unique each call (no determinism)
  - verify_password returns True for matching pw
  - verify_password returns False for wrong pw
  - verify_password against None still spends CPU (timing parity)
  - verify_password rejects malformed hash strings
  - validate_password: too short / too long / all whitespace / valid
"""
from __future__ import annotations

import time

import pytest

from app.core import passwords as P


def test_hash_format():
    h = P.hash_password("hello-world")
    parts = h.split("$")
    assert parts[0] == "scrypt"
    assert parts[1].startswith("N=")
    # 4 parts total: scheme, params, salt, hash
    assert len(parts) == 4
    assert len(parts[2]) > 0
    assert len(parts[3]) > 0


def test_hash_is_salted():
    h1 = P.hash_password("same")
    h2 = P.hash_password("same")
    assert h1 != h2


def test_verify_correct_pw():
    h = P.hash_password("CorrectHorseBatteryStaple")
    assert P.verify_password("CorrectHorseBatteryStaple", h) is True


def test_verify_wrong_pw():
    h = P.hash_password("real")
    assert P.verify_password("imposter", h) is False


def test_verify_empty_pw():
    h = P.hash_password("real")
    assert P.verify_password("", h) is False


def test_verify_against_none_is_false_but_spends_cpu():
    """User-not-found case: encoded=None should always return False but the
    time taken should be roughly comparable to a real verify (timing-uniform
    defence against username enumeration)."""
    h = P.hash_password("real")
    t0 = time.perf_counter()
    P.verify_password("anything", h)
    real_t = time.perf_counter() - t0

    t0 = time.perf_counter()
    res = P.verify_password("anything", None)
    none_t = time.perf_counter() - t0

    assert res is False
    # within an order of magnitude — exact ratio is timing-fuzzy on CI
    # so we just assert "not zero" (i.e. we didn't shortcut on None).
    assert none_t > real_t * 0.3, (
        f"verify_password against None too fast — leaks user-existence: "
        f"{none_t*1000:.1f}ms vs real {real_t*1000:.1f}ms"
    )


def test_verify_malformed_hash():
    assert P.verify_password("any", "not-a-real-hash") is False
    assert P.verify_password("any", "scrypt$bad-params$xx$yy") is False


def test_validate_password_too_short():
    ok, err = P.validate_password("short")
    assert ok is False
    assert "8" in err


def test_validate_password_too_long():
    ok, err = P.validate_password("A" * 200)
    assert ok is False
    assert "128" in err


def test_validate_password_all_whitespace():
    ok, err = P.validate_password("        ")
    # 8+ chars but all whitespace → reject
    # (note: my impl checks .strip() AFTER length, so 8 chars of whitespace
    # technically passes the length and fails the strip check. But 8 spaces
    # IS exactly 8 chars so strip rejects.)
    assert ok is False


def test_validate_password_valid():
    ok, err = P.validate_password("good_password")
    assert ok is True
    assert err == ""


def test_validate_password_non_string():
    ok, err = P.validate_password(12345)  # type: ignore[arg-type]
    assert ok is False
