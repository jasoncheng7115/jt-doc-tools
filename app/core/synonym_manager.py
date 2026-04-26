"""JSON-backed store for PDF label synonyms.

Decouples the label-to-profile-key mapping from code so users can extend it
through the admin UI or the "learn from this PDF" flow in the pdf-fill tool
without editing Python.

On first run the store seeds itself from :data:`pdf_form_detect.DEFAULT_LABEL_MAP`.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..config import settings


class SynonymManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path = settings.data_dir / "label_synonyms.json"
        if not self._path.exists():
            # Late import avoids circular dependency: pdf_form_detect imports this module.
            from .pdf_form_detect import DEFAULT_LABEL_MAP
            self._write({"synonyms": DEFAULT_LABEL_MAP, "updated_at": time.time()})
        else:
            self._merge_missing_defaults()

    def _merge_missing_defaults(self) -> None:
        """Ensure every canonical key and every code-level default synonym
        is present in the file. User-added synonyms are preserved — we only
        *union*, never delete, so customizations aren't overwritten when
        the code ships new label variants."""
        from .pdf_form_detect import DEFAULT_LABEL_MAP
        with self._lock:
            data = self._read()
            syns = data.setdefault("synonyms", {})
            changed = False
            for k, defaults in DEFAULT_LABEL_MAP.items():
                current = syns.get(k)
                if current is None:
                    syns[k] = list(defaults)
                    changed = True
                    continue
                for s in defaults:
                    if s not in current:
                        current.append(s)
                        changed = True
            if changed:
                data["updated_at"] = time.time()
                self._write(data)

    def _read(self) -> dict:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def get_map(self) -> dict[str, list[str]]:
        with self._lock:
            return dict(self._read().get("synonyms", {}))

    def save_map(self, mapping: dict[str, list[str]]) -> None:
        with self._lock:
            # Preserve insertion order from caller, drop empties.
            clean: dict[str, list[str]] = {}
            for k, syns in mapping.items():
                k = (k or "").strip()
                if not k:
                    continue
                seen: set[str] = set()
                keep: list[str] = []
                for s in syns or []:
                    s = (s or "").strip()
                    if not s or s in seen:
                        continue
                    seen.add(s)
                    keep.append(s)
                clean[k] = keep
            self._write({"synonyms": clean, "updated_at": time.time()})

    def add_synonym(self, canonical_key: str, synonym: str) -> bool:
        """Append ``synonym`` to ``canonical_key``'s list if not already present.

        Returns True when the synonyms file was changed.
        """
        canonical_key = (canonical_key or "").strip()
        synonym = (synonym or "").strip()
        if not canonical_key or not synonym:
            return False
        with self._lock:
            data = self._read()
            syns = data.setdefault("synonyms", {})
            lst = syns.setdefault(canonical_key, [])
            if synonym in lst:
                return False
            lst.append(synonym)
            data["updated_at"] = time.time()
            self._write(data)
            return True

    def reset_to_defaults(self) -> None:
        """Overwrite the store with the module-level defaults (for debugging)."""
        from .pdf_form_detect import DEFAULT_LABEL_MAP
        self._write({"synonyms": DEFAULT_LABEL_MAP, "updated_at": time.time()})


synonym_manager = SynonymManager()
