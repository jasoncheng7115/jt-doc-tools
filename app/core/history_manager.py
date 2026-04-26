"""Persistent history of tool outputs (pdf-fill / pdf-stamp / pdf-watermark).

Each tool gets its own subdir under ``data/<subdir>/`` (e.g. fill_history,
stamp_history, watermark_history). Each entry has its own UUID dir
containing original, output, optional preview, and meta.json. The shape
is identical across tools so the admin viewer can iterate them uniformly.

`username` field added to meta in v1.1.0 — when auth is on, it identifies
who created the entry; otherwise empty string.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from ..config import settings


class HistoryManager:
    def __init__(self, subdir: str = "fill_history",
                 output_filename: str = "filled.pdf") -> None:
        self._lock = threading.RLock()
        self._root: Path = settings.data_dir / subdir
        self._root.mkdir(parents=True, exist_ok=True)
        self._output_filename = output_filename

    def _entry_dir(self, hid: str) -> Path:
        return self._root / hid

    def save(
        self,
        original_path: Path,
        filled_path: Path,
        preview_path: Optional[Path],
        original_filename: str,
        template_id: Optional[str] = None,
        template_name: Optional[str] = None,
        company_id: Optional[str] = None,
        report: Optional[dict] = None,
        username: str = "",
        extra: Optional[dict] = None,
    ) -> dict:
        """Copy the source/output/preview into a new history entry and return
        its metadata record. ``username`` is the actor (when auth is on);
        ``extra`` carries tool-specific metadata (e.g. asset_id for stamp)."""
        with self._lock:
            hid = uuid.uuid4().hex[:12]
            d = self._entry_dir(hid)
            d.mkdir(parents=True, exist_ok=True)
            if original_path.exists():
                shutil.copy2(str(original_path), str(d / "original.pdf"))
            if filled_path.exists():
                shutil.copy2(str(filled_path), str(d / self._output_filename))
            if preview_path and preview_path.exists():
                shutil.copy2(str(preview_path), str(d / "preview.png"))
            meta = {
                "id": hid,
                "filename": original_filename,
                "saved_at": time.time(),
                "username": username,
                "template_id": template_id,
                "template_name": template_name,
                "company_id": company_id,
                "report": report or {},
            }
            if extra:
                meta["extra"] = extra
            (d / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return meta

    def list_all(self) -> list[dict]:
        with self._lock:
            out: list[dict] = []
            for d in self._root.iterdir():
                if not d.is_dir():
                    continue
                mf = d / "meta.json"
                if not mf.exists():
                    continue
                try:
                    meta = json.loads(mf.read_text(encoding="utf-8"))
                except Exception:
                    continue
                meta["has_filled"] = (d / self._output_filename).exists()
                meta["has_original"] = (d / "original.pdf").exists()
                meta["has_preview"] = (d / "preview.png").exists()
                out.append(meta)
            out.sort(key=lambda m: -m.get("saved_at", 0))
            return out

    def get(self, hid: str) -> Optional[dict]:
        mf = self._entry_dir(hid) / "meta.json"
        if not mf.exists():
            return None
        try:
            return json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            return None

    def file(self, hid: str, kind: str) -> Optional[Path]:
        """``kind`` in {'original', 'filled' (= output), 'preview'}."""
        mapping = {
            "original": "original.pdf",
            "filled": self._output_filename,
            "output": self._output_filename,
            "preview": "preview.png",
        }
        name = mapping.get(kind)
        if not name:
            return None
        p = self._entry_dir(hid) / name
        return p if p.exists() else None

    def delete(self, hid: str) -> bool:
        with self._lock:
            d = self._entry_dir(hid)
            if not d.exists():
                return False
            shutil.rmtree(str(d), ignore_errors=True)
            return True

    def sweep_older_than(self, max_age_seconds: int) -> int:
        """Delete entries older than the cutoff. Returns count removed.
        Pass max_age_seconds <= 0 for "no expiry"."""
        if max_age_seconds <= 0:
            return 0
        cutoff = time.time() - max_age_seconds
        n = 0
        with self._lock:
            for d in self._root.iterdir():
                if not d.is_dir():
                    continue
                mf = d / "meta.json"
                if not mf.exists():
                    # no meta = orphan, sweep
                    shutil.rmtree(str(d), ignore_errors=True)
                    n += 1
                    continue
                try:
                    meta = json.loads(mf.read_text(encoding="utf-8"))
                    if meta.get("saved_at", 0) < cutoff:
                        shutil.rmtree(str(d), ignore_errors=True)
                        n += 1
                except Exception:
                    continue
        return n

    @property
    def root(self) -> Path:
        return self._root


# Tool-specific singletons
history_manager = HistoryManager("fill_history", "filled.pdf")
stamp_history = HistoryManager("stamp_history", "stamped.pdf")
watermark_history = HistoryManager("watermark_history", "watermarked.pdf")
