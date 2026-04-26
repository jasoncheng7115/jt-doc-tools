"""User-editable order/extension of the LibreOffice / OxOffice search path.

Built-in candidates are baked in (cross-platform: macOS / Linux / Windows)
and cannot be deleted, but the user may reorder them and prepend custom
paths. The merged list is consumed by :func:`office_convert.find_soffice`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

from ..config import settings


# id → label mapping for built-ins. ids are stable so reorder/persist works.
BUILTIN_PATHS: list[dict[str, str]] = [
    # macOS
    {"id": "macos-oxoffice",  "path": "/Applications/OxOffice.app/Contents/MacOS/soffice"},
    {"id": "macos-libreoffice", "path": "/Applications/LibreOffice.app/Contents/MacOS/soffice"},
    {"id": "mac-brew",        "path": "/opt/homebrew/bin/soffice"},
    {"id": "mac-local",       "path": "/usr/local/bin/soffice"},
    # Linux — OxOffice 優先，再回到系統 LibreOffice
    {"id": "linux-oxoffice-bin",  "path": "/usr/bin/oxoffice"},
    {"id": "linux-oxoffice-prog", "path": "/opt/oxoffice/program/soffice"},
    {"id": "linux-soffice",       "path": "/usr/bin/soffice"},
    {"id": "linux-libreoffice",   "path": "/usr/bin/libreoffice"},
    # Windows — OxOffice 優先
    {"id": "win-oxoffice",        "path": r"C:\Program Files\OxOffice\program\soffice.exe"},
    {"id": "win-oxoffice-x86",    "path": r"C:\Program Files (x86)\OxOffice\program\soffice.exe"},
    {"id": "win-pf-soffice",      "path": r"C:\Program Files\LibreOffice\program\soffice.exe"},
    {"id": "win-pfx86-soffice",   "path": r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"},
]


def _probe_version(path: str) -> str:
    """Run ``<path> --version`` with a short timeout and pick out the version
    text. Returns "" on any failure (used purely for display in the UI)."""
    try:
        proc = subprocess.run(
            [path, "--version"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=4.0,
        )
        out = proc.stdout.decode("utf-8", "replace").strip()
        # Take the first line, strip the long git hash that LibreOffice
        # appends so the UI stays compact.
        line = out.splitlines()[0] if out else ""
        line = re.sub(r"\s+[0-9a-f]{20,}.*$", "", line).strip()
        return line
    except Exception:
        return ""


class _ConvSettings:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path = settings.data_dir / "office_paths.json"
        # Cache version probe results across requests — re-checking via
        # subprocess on every page load makes /admin/conversion sluggish
        # because soffice's --version takes 100–500 ms each.
        self._version_cache: dict[str, str] = {}
        if not self._path.exists():
            self._write(self._initial())

    @staticmethod
    def _initial() -> dict:
        return {
            # Persisted order of built-in ids; default = file order.
            "builtin_order": [b["id"] for b in BUILTIN_PATHS],
            # User-added entries: free-form string paths.
            "custom": [],
        }

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return self._initial()

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ---- public API ----
    def list_paths(self) -> list[dict]:
        """Return the resolved ordered list (custom first, then built-in by saved order).

        Each entry: {id, path, builtin: bool, exists: bool, executable: bool}.
        """
        with self._lock:
            data = self._read()
            by_id = {b["id"]: b for b in BUILTIN_PATHS}
            order = [bid for bid in data.get("builtin_order", []) if bid in by_id]
            # Append any built-ins missing from the saved order at the end.
            for b in BUILTIN_PATHS:
                if b["id"] not in order:
                    order.append(b["id"])
            out: list[dict] = []
            def _entry(eid: str, p: str, builtin: bool) -> dict:
                exists = os.path.exists(p)
                executable = os.access(p, os.X_OK) if exists else False
                version = ""
                if exists and executable:
                    if p in self._version_cache:
                        version = self._version_cache[p]
                    else:
                        version = _probe_version(p)
                        self._version_cache[p] = version
                return {
                    "id": eid, "path": p, "builtin": builtin,
                    "exists": exists, "executable": executable,
                    "version": version,
                }
            for cp in data.get("custom", []):
                p = str(cp).strip()
                if not p: continue
                out.append(_entry(f"custom:{p}", p, False))
            for bid in order:
                out.append(_entry(bid, by_id[bid]["path"], True))
            return out

    def get_executable_paths(self) -> list[str]:
        """Just the path strings, ordered by user preference (custom first)."""
        return [r["path"] for r in self.list_paths()]

    def save_order(self, ordered_ids: list[str], custom: list[str]) -> None:
        with self._lock:
            data = self._read()
            valid_builtin = {b["id"] for b in BUILTIN_PATHS}
            data["builtin_order"] = [i for i in ordered_ids if i in valid_builtin]
            data["custom"] = [c.strip() for c in custom if c and c.strip()]
            self._write(data)
            # Drop cached versions for paths that have been removed/renamed
            # so the next list_paths re-probes from a clean state.
            self._version_cache.clear()

    def find_first_usable(self) -> Optional[str]:
        """Return the first path that exists and is executable; else None."""
        for r in self.list_paths():
            if r["exists"] and r["executable"]:
                return r["path"]
        return None


conv_settings = _ConvSettings()
