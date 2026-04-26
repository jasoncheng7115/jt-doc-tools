"""Per-form templates: remember the exact fill positions for a specific PDF
layout so future uploads of the same form get pixel-perfect results.

Workflow:
  1. User uploads a PDF. We compute a stable *fingerprint* from its text
     labels and look it up. If a template exists we apply it directly —
     fields and checkboxes at saved positions, no heuristic detection.
  2. Otherwise we fall back to auto-detection. The UI shows a "save as
     template" button which calls :meth:`create_from_detection` to freeze
     the auto-detected positions into a new template.
  3. Future uploads of the same form → hit step 1.

The fingerprint is stable across blank / pre-filled versions because we
only consider *short labels* (≤20 chars) which are part of the form
template rather than user-entered values.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from ..config import settings


@dataclass
class TemplateField:
    profile_key: str
    page: int
    slot: tuple[float, float, float, float]
    base_font_size: float = 11.0


@dataclass
class TemplateCheckbox:
    profile_key: str
    option_text: str                          # the printed label next to □
    page: int
    box: tuple[float, float, float, float]    # bbox of the □ glyph
    size: float = 10.0


@dataclass
class FormTemplate:
    id: str
    name: str
    fingerprint: str
    page_count: int
    fields: list[dict] = field(default_factory=list)
    checkboxes: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def compute_fingerprint(pdf_path: Path) -> str:
    """SHA-256 of the sorted set of short text spans on page 1.

    We exclude pure-digit spans and very long strings (>20 chars) so that
    prefilled values don't pollute the fingerprint — labels of a given
    form stay identical across blank and filled-in copies.
    """
    spans: list[str] = []
    try:
        with fitz.open(str(pdf_path)) as doc:
            if doc.page_count == 0:
                return ""
            page = doc[0]
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        t = (span.get("text") or "").strip()
                        if 2 <= len(t) <= 20 and not t.isdigit():
                            spans.append(t)
    except Exception:
        return ""
    items = sorted(set(spans))[:60]
    return hashlib.sha256("|".join(items).encode("utf-8")).hexdigest()


class TemplateManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path = settings.data_dir / "form_templates.json"
        if not self._path.exists():
            self._write({"templates": {}, "updated_at": time.time()})

    def _read(self) -> dict:
        if not self._path.exists():
            return {"templates": {}, "updated_at": time.time()}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def list_all(self) -> list[dict]:
        with self._lock:
            data = self._read()
            items = list(data.get("templates", {}).values())
            items.sort(key=lambda t: -t.get("updated_at", 0))
            return items

    def get(self, tid: str) -> Optional[dict]:
        return self._read().get("templates", {}).get(tid)

    def get_by_fingerprint(self, fp: str) -> Optional[dict]:
        if not fp:
            return None
        for t in self._read().get("templates", {}).values():
            if t.get("fingerprint") == fp:
                return t
        return None

    def save(
        self,
        name: str,
        fingerprint: str,
        page_count: int,
        fields: list[dict],
        checkboxes: list[dict],
        tid: Optional[str] = None,
    ) -> dict:
        with self._lock:
            data = self._read()
            templates = data.setdefault("templates", {})
            if tid and tid in templates:
                t = templates[tid]
            else:
                tid = tid or uuid.uuid4().hex[:12]
                t = {"id": tid, "created_at": time.time()}
                templates[tid] = t
            t["id"] = tid
            t["name"] = name or "未命名範本"
            t["fingerprint"] = fingerprint
            t["page_count"] = page_count
            t["fields"] = fields
            t["checkboxes"] = checkboxes
            t["updated_at"] = time.time()
            data["updated_at"] = time.time()
            self._write(data)
            return t

    def delete(self, tid: str) -> bool:
        with self._lock:
            data = self._read()
            templates = data.get("templates", {})
            if tid not in templates:
                return False
            del templates[tid]
            data["updated_at"] = time.time()
            self._write(data)
            return True

    def rename(self, tid: str, name: str) -> bool:
        with self._lock:
            data = self._read()
            t = data.get("templates", {}).get(tid)
            if not t:
                return False
            t["name"] = name.strip() or t["name"]
            t["updated_at"] = time.time()
            data["updated_at"] = time.time()
            self._write(data)
            return True


template_manager = TemplateManager()
