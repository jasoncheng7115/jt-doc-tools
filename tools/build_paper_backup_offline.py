#!/usr/bin/env python3
"""把「加密紙本備份」的離線還原程式產生到公開 repo 的 `paper-backup-offline/`。

工具頁本來就能下載離線還原程式；這裡另外放一份在 GitHub 上，主機不在了、
或保管人手上沒有帳號時，也拿得到同一份程式（用指令或瀏覽器下載，見該資料夾的 README）。

產出（檔名固定，網址不會因為版本改變）：

* `jtdt-paper-backup-offline-<語言>.html`：每個介面語言一份
* `SHA256SUMS`：`sha256sum -c` 的格式
* `pack.json`：產生時的版本與每個檔的 SHA-256

**只有內容變了才需要重產**：檢查（`--check`、`tests/test_paper_backup_offline_published.py`）
用 `pack.json` 記的版本重產一次、逐位元組比對，模組、樣板或文字改了才會不一樣。
單純升版號不用重產，免得每一版都在 repo 裡多一份十幾 MB 的檔。

    .venv/bin/python tools/build_paper_backup_offline.py           # 用目前版本重產
    .venv/bin/python tools/build_paper_backup_offline.py --check   # 跟 repo 裡那份不一樣就回 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.repo_paths import public_root  # noqa: E402

DIR_NAME = "paper-backup-offline"


def out_dir() -> Path:
    return public_root() / DIR_NAME


def file_name(lang: str) -> str:
    return f"jtdt-paper-backup-offline-{lang}.html"


def build_all(version: str) -> dict[str, bytes]:
    """{檔名: 內容}，每個介面語言一份；同一版本產出的位元組固定。"""
    from app.core import ui_locale
    from app.main import templates
    from app.tools.paper_backup import offline
    out = {}
    for lang in ui_locale.SUPPORTED:
        req = SimpleNamespace(cookies={ui_locale.COOKIE_NAME: lang})
        out[file_name(lang)] = offline.build(templates, req, version, lang)
    return out


def sums_text(files: dict[str, bytes]) -> str:
    return "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in files.items())


def pack_json(version: str, files: dict[str, bytes]) -> str:
    meta = {"version": version,
            "files": {n: {"size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in files.items()}}
    return json.dumps(meta, ensure_ascii=False, indent=2) + "\n"


def recorded_version(d: Path | None = None) -> str | None:
    p = (d or out_dir()) / "pack.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return None


def differences(d: Path | None = None) -> list[str]:
    """repo 裡那份跟「用記錄的版本重產」比，不一樣的檔名（空 list＝一致）。"""
    d = d or out_dir()
    ver = recorded_version(d)
    if not ver:
        return ["pack.json"]
    files = build_all(ver)
    want = {**files, "SHA256SUMS": sums_text(files).encode("utf-8"),
            "pack.json": pack_json(ver, files).encode("utf-8")}
    bad = []
    for name, data in want.items():
        p = d / name
        if not p.is_file() or p.read_bytes() != data:
            bad.append(name)
    return bad


def write(version: str, d: Path | None = None) -> list[Path]:
    d = d or out_dir()
    d.mkdir(parents=True, exist_ok=True)
    files = build_all(version)
    written = []
    for name, data in files.items():
        (d / name).write_bytes(data)
        written.append(d / name)
    (d / "SHA256SUMS").write_text(sums_text(files), encoding="utf-8", newline="\n")
    (d / "pack.json").write_text(pack_json(version, files), encoding="utf-8", newline="\n")
    return written + [d / "SHA256SUMS", d / "pack.json"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the paper-backup offline restore packs into the public tree.")
    ap.add_argument("--check", action="store_true", help="exit 1 if the published packs are stale")
    args = ap.parse_args()
    # 載入 app 不可以碰到真正的資料目錄
    os.environ.setdefault("JTDT_DATA_DIR", tempfile.mkdtemp(prefix="jtdt_pbpack_"))
    if args.check:
        bad = differences()
        if bad:
            print("stale: " + ", ".join(bad))
            print("run: .venv/bin/python tools/build_paper_backup_offline.py")
            return 1
        print(f"ok: {out_dir()} (built from {recorded_version()})")
        return 0
    from app.main import VERSION
    for p in write(VERSION):
        print(f"wrote {p} ({p.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
