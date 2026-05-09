"""ReDoS regression for RE_AD_DN — closes CodeQL alert #13
(`Inefficient regular expression` in app/tools/doc_deident/patterns.py:311).

Pre-fix the regex had nested unbounded quantifiers and a duplicate UID
literal in the alternation; an attacker could craft input that triggered
catastrophic backtracking. Post-fix every quantifier has a hard upper
bound, so worst-case match time is bounded."""
from __future__ import annotations

import time

from app.tools.doc_deident.patterns import RE_AD_DN


def test_normal_dn_matches():
    txt = "CN=Jason,OU=Sales,DC=example,DC=local"
    assert RE_AD_DN.search(txt) is not None


def test_long_chain_matches():
    """Up to 30 components allowed (real-world DNs rarely exceed 10)."""
    parts = [f"OU=Dept{i}" for i in range(20)]
    txt = "CN=user," + ",".join(parts) + ",DC=corp,DC=local"
    assert RE_AD_DN.search(txt) is not None


def test_redos_attack_input_finishes_quickly():
    """The pre-fix regex could spin for seconds on a crafted string with
    repeated near-matches that fail at the trailing \\b boundary.

    Build: many `OU=A,` repeated, ending with a non-word char that breaks
    the \\b expectation — old regex would try every possible split."""
    attack = "CN=" + ",".join(["OU=A"] * 200) + "@@@"
    t0 = time.perf_counter()
    RE_AD_DN.search(attack)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, (
        f"ReDoS regression: AD_DN regex took {elapsed:.2f}s on attack input"
    )


def test_no_match_on_random_text():
    txt = "Hello world, this is just text with no DN syntax."
    assert RE_AD_DN.search(txt) is None


def test_match_does_not_consume_unrelated_suffix():
    """RDN value bounded to 128 chars per component, so very long
    junk-after-equals doesn't escape the bound."""
    long_junk = "X" * 5000
    txt = f"CN={long_junk},DC=example"
    m = RE_AD_DN.search(txt)
    # Match should be capped at 128 char per component, not consume all 5000.
    if m:
        assert len(m.group(0)) < 200, "RDN value exceeded length cap"
