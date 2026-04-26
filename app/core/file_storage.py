from __future__ import annotations

import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path


class FileStorage(ABC):
    @abstractmethod
    def save_bytes(self, data: bytes, suffix: str = "") -> str: ...

    @abstractmethod
    def save_file(self, src: Path, suffix: str = "") -> str: ...

    @abstractmethod
    def get_path(self, key: str) -> Path: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalFileStorage(FileStorage):
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _new_key(self, suffix: str = "") -> str:
        if suffix and not suffix.startswith("."):
            suffix = "." + suffix
        return f"{uuid.uuid4().hex}{suffix}"

    def save_bytes(self, data: bytes, suffix: str = "") -> str:
        key = self._new_key(suffix)
        (self.base_dir / key).write_bytes(data)
        return key

    def save_file(self, src: Path, suffix: str = "") -> str:
        if not suffix:
            suffix = Path(src).suffix
        key = self._new_key(suffix)
        shutil.copyfile(src, self.base_dir / key)
        return key

    def get_path(self, key: str) -> Path:
        return self.base_dir / key

    def delete(self, key: str) -> None:
        p = self.base_dir / key
        if p.exists():
            p.unlink()

    def exists(self, key: str) -> bool:
        return (self.base_dir / key).exists()
