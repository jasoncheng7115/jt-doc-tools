#!/usr/bin/env python3
"""Release-time version-consistency check.

Asserts every place that records a version number agrees with the canonical
source (`app/main.py:VERSION`). Run automatically by:

  - `tests/test_version_consistency.py` (pytest, CI-gated)
  - `jtdt update` flow (future) before tagging a release

Sources audited:
    1. app/main.py            VERSION = "X.Y.Z"
    2. pyproject.toml         [project] version = "X.Y.Z"
    3. uv.lock                jt-doc-tools entry version
    4. github/README.md       First H1 line `# ... vX.Y.Z`
    5. github/CHANGELOG.md    First `## [X.Y.Z] - YYYY-MM-DD` heading
    6. github/sbom.cdx.json   CycloneDX `metadata.component.version`（`tools/make_sbom.py` 重產）

Exit 0 if all match, exit 1 with a clear diff table otherwise.

Why this matters: in v1.5.3 the README/CHANGELOG bump and the pyproject
bump were done in different commits; for a brief window the README said
v1.5.3 while pyproject still said 1.4.82, breaking pip / uv install
metadata + Dependabot scope determination + support burden. This script
makes the inconsistency a hard fail before push."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
# 開發樹的公開檔案在 `github/` 裡，clone 下來則直接在根目錄 —— 寫死 `github/`
# 的話在公開版永遠 file not found（外部評估 2026-09-05 實測固定紅）。
sys.path.insert(0, str(ROOT))
from tools.repo_paths import public_root  # noqa: E402
PUB = public_root(ROOT)


def read_main_version() -> str:
    txt = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', txt, re.MULTILINE)
    if not m:
        raise RuntimeError("VERSION line not found in app/main.py")
    return m.group(1)


def read_pyproject_version() -> str:
    txt = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.MULTILINE)
    if not m:
        raise RuntimeError("version line not found in pyproject.toml")
    return m.group(1)


def read_uv_lock_version() -> Optional[str]:
    f = ROOT / "uv.lock"
    if not f.exists():
        return None
    txt = f.read_text(encoding="utf-8")
    # Find the [[package]] block whose name = "jt-doc-tools".
    for block in re.split(r"(?=\[\[package\]\])", txt):
        if 'name = "jt-doc-tools"' in block:
            m = re.search(r'^version\s*=\s*"([^"]+)"', block, re.MULTILINE)
            if m:
                return m.group(1)
    return None


def read_readme_version() -> Optional[str]:
    f = PUB / "README.md"
    if not f.exists():
        return None
    # Format: "# Jason Tools 文件工具箱 v1.5.3".  Look for the H1 rather than
    # line 1: v1.14.95 put a language switch (zh-Hant / English) above the
    # title, and reading line 1 silently returned None -- the check kept
    # exiting 0 while no longer verifying the README at all.
    for ln in f.read_text(encoding="utf-8").splitlines():
        if ln.startswith("# "):
            m = re.search(r"v(\d+\.\d+\.\d+)", ln)
            return m.group(1) if m else None
    return None


def read_changelog_version() -> Optional[str]:
    f = PUB / "CHANGELOG.md"
    if not f.exists():
        return None
    for ln in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"##\s*\[(\d+\.\d+\.\d+)\]", ln)
        if m:
            return m.group(1)
    return None


def read_sbom_version() -> Optional[str]:
    """`sbom.cdx.json`（CycloneDX）的產品版本 —— 每一版都要重產（`python tools/make_sbom.py`）。"""
    import json
    f = PUB / "sbom.cdx.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))["metadata"]["component"]["version"]
    except (ValueError, KeyError, TypeError):
        return None


SOURCES = [
    ("app/main.py:VERSION",        read_main_version),
    ("pyproject.toml:version",     read_pyproject_version),
    ("uv.lock:jt-doc-tools",       read_uv_lock_version),
    ("README.md heading",          read_readme_version),
    ("CHANGELOG.md latest",        read_changelog_version),
    ("sbom.cdx.json component",    read_sbom_version),
]


def collect() -> dict[str, Optional[str]]:
    return {label: fn() for label, fn in SOURCES}


def check() -> tuple[bool, dict[str, Optional[str]], str]:
    """Returns (ok, versions_seen, canonical_version)."""
    versions = collect()
    canonical = versions["app/main.py:VERSION"]
    # A source that reads back as None used to be silently skipped, so the
    # check exited 0 while no longer verifying that file at all (hit in
    # v1.14.95: a language-switch line above the README title made the
    # heading reader return None).  Treat "cannot read it" as a failure --
    # every file listed here exists in this repo.
    mismatches = {k: v for k, v in versions.items() if v != canonical}
    return (not mismatches), versions, canonical


def main() -> int:
    ok, versions, canonical = check()
    if ok:
        print(f"✓ All version sources agree on {canonical}")
        for label, v in versions.items():
            mark = " " if v == canonical else "?"
            print(f"  {mark} {label:40s} = {v}")
        return 0
    print(f"✗ Version mismatch — canonical is {canonical} (from app/main.py)")
    print()
    print(f"  {'Source':40s}  {'Version':10s}  {'Status':10s}")
    print(f"  {'-' * 40}  {'-' * 10}  {'-' * 10}")
    for label, v in versions.items():
        status = "OK" if v == canonical else ("MISMATCH" if v else "missing")
        print(f"  {label:40s}  {v or '(none)':10s}  {status}")
    print()
    print("Fix: bump every mismatched source to match app/main.py:VERSION,")
    print("then re-run `python tools/check_version_consistency.py` until clean.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
