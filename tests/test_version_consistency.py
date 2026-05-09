"""Release-time version consistency — every source agrees on `app/main.py:VERSION`.

Fails CI when README / CHANGELOG / pyproject / uv.lock disagrees with the
canonical version. Prevents the v1.5.3 incident where README said v1.5.3
while pyproject momentarily still said 1.4.82, breaking pip metadata."""
from __future__ import annotations

import sys
from pathlib import Path

# Make tools/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import check_version_consistency as cvc


def test_all_version_sources_agree():
    ok, versions, canonical = cvc.check()
    if not ok:
        diffs = {k: v for k, v in versions.items() if v is not None and v != canonical}
        msg = f"Version mismatch (canonical={canonical}):\n"
        for label, v in diffs.items():
            msg += f"  {label} = {v}\n"
        msg += "Run `python tools/check_version_consistency.py` for full table."
        assert False, msg
