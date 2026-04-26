from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import APIRouter


@dataclass
class ToolMetadata:
    id: str
    name: str
    description: str
    icon: str = "🛠️"
    category: str = "PDF"
    version: str = "0.1.0"
    enabled: bool = True


@dataclass
class ToolModule:
    metadata: ToolMetadata
    router: APIRouter
    templates_dir: Optional[Path] = None
    assets_used: list[str] = field(default_factory=list)  # e.g. ["stamp"]
