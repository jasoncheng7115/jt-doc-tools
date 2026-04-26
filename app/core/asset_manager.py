from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Optional

from ..config import settings
from . import image_utils

AssetType = Literal["stamp", "watermark", "signature", "logo"]


@dataclass
class PositionPreset:
    x_mm: float = 140.0
    y_mm: float = 240.0
    width_mm: float = 40.0
    height_mm: float = 40.0
    paper_w_mm: float = 210.0
    paper_h_mm: float = 297.0
    lock_aspect: bool = True
    rotation_deg: float = 0.0  # clockwise, degrees; simulates hand-stamp tilt


@dataclass
class Asset:
    id: str
    name: str
    type: AssetType
    file_key: str
    thumb_key: str
    preset: PositionPreset = field(default_factory=PositionPreset)
    is_default: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["preset"] = asdict(self.preset)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Asset":
        p = d.get("preset") or {}
        return Asset(
            id=d["id"],
            name=d["name"],
            type=d["type"],
            file_key=d["file_key"],
            thumb_key=d["thumb_key"],
            preset=PositionPreset(**p),
            is_default=d.get("is_default", False),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )


class AssetManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._meta_path: Path = settings.assets_meta_path
        self._files_dir: Path = settings.assets_files_dir
        self._files_dir.mkdir(parents=True, exist_ok=True)
        if not self._meta_path.exists():
            self._write({"assets": []})

    def _read(self) -> dict:
        if not self._meta_path.exists():
            return {"assets": []}
        return json.loads(self._meta_path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self._meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- CRUD ----
    def list(self, type: Optional[AssetType] = None) -> list[Asset]:
        with self._lock:
            items = [Asset.from_dict(a) for a in self._read().get("assets", [])]
            if type:
                items = [a for a in items if a.type == type]
            items.sort(key=lambda a: (not a.is_default, -a.updated_at))
            return items

    def get(self, asset_id: str) -> Optional[Asset]:
        for a in self.list():
            if a.id == asset_id:
                return a
        return None

    def get_default(self, type: AssetType) -> Optional[Asset]:
        for a in self.list(type):
            if a.is_default:
                return a
        items = self.list(type)
        return items[0] if items else None

    def create_from_bytes(
        self,
        name: str,
        type: AssetType,
        png_bytes: bytes,
        remove_bg: bool = False,
    ) -> Asset:
        with self._lock:
            asset_id = uuid.uuid4().hex
            file_key = f"{asset_id}.png"
            thumb_key = f"{asset_id}_thumb.png"
            file_path = self._files_dir / file_key
            file_path.write_bytes(png_bytes)
            # Re-save as clean RGBA PNG
            image_utils.ensure_rgba_png(file_path, file_path)
            if remove_bg:
                image_utils.remove_white_background(file_path, file_path)
            image_utils.make_thumbnail_png(file_path, self._files_dir / thumb_key, 256)

            w_mm, h_mm = image_utils.image_natural_size_mm(file_path)
            # Preserve aspect ratio; clamp oversized images to ~40mm wide default.
            aspect = (w_mm / h_mm) if h_mm > 0 else 1.0
            target_w = 40.0
            if w_mm <= 0 or h_mm <= 0:
                w_mm, h_mm = target_w, target_w
            elif w_mm > 120 or h_mm > 120:
                w_mm = target_w
                h_mm = target_w / aspect
            preset = PositionPreset(
                x_mm=140.0,
                y_mm=240.0,
                width_mm=round(w_mm, 1),
                height_mm=round(h_mm, 1),
            )
            asset = Asset(
                id=asset_id,
                name=name,
                type=type,
                file_key=file_key,
                thumb_key=thumb_key,
                preset=preset,
            )
            data = self._read()
            data["assets"].append(asset.to_dict())
            self._write(data)
            return asset

    def update(self, asset_id: str, **changes) -> Optional[Asset]:
        with self._lock:
            data = self._read()
            for raw in data["assets"]:
                if raw["id"] != asset_id:
                    continue
                asset = Asset.from_dict(raw)
                if "name" in changes and changes["name"]:
                    asset.name = changes["name"]
                if "type" in changes and changes["type"]:
                    asset.type = changes["type"]
                if "preset" in changes and changes["preset"]:
                    p = changes["preset"]
                    if isinstance(p, PositionPreset):
                        asset.preset = p
                    else:
                        asset.preset = PositionPreset(**p)
                asset.updated_at = time.time()
                raw.update(asset.to_dict())
                self._write(data)
                return asset
            return None

    def crop(
        self, asset_id: str, x: float, y: float, w: float, h: float
    ) -> Optional[Asset]:
        """Crop the asset PNG to the given fractional rect (0..1 coords).

        Regenerates the thumbnail and keeps the existing preset (sizes stay
        in mm, so the image's natural dimensions shift but printed size is
        preserved).
        """
        from PIL import Image
        with self._lock:
            data = self._read()
            for raw in data["assets"]:
                if raw["id"] != asset_id:
                    continue
                asset = Asset.from_dict(raw)
                file_path = self._files_dir / asset.file_key
                if not file_path.exists():
                    return None
                with Image.open(file_path) as im:
                    if im.mode != "RGBA":
                        im = im.convert("RGBA")
                    W, H = im.size
                    # Clamp fractions, then convert to pixel bounds.
                    x = max(0.0, min(1.0, x))
                    y = max(0.0, min(1.0, y))
                    w = max(0.0, min(1.0 - x, w))
                    h = max(0.0, min(1.0 - y, h))
                    px0 = int(round(x * W))
                    py0 = int(round(y * H))
                    px1 = int(round((x + w) * W))
                    py1 = int(round((y + h) * H))
                    if px1 - px0 < 2 or py1 - py0 < 2:
                        return None
                    cropped = im.crop((px0, py0, px1, py1))
                    cropped.save(file_path, format="PNG")
                image_utils.make_thumbnail_png(
                    file_path, self._files_dir / asset.thumb_key, 256
                )
                # Reset the preset's printed size to the cropped image's
                # natural aspect so the "位置與預覽" editor doesn't stretch
                # the stamp. Keep the image's longer edge close to the
                # existing preset's longer edge so the on-page scale stays
                # similar (user typically crops off whitespace, not content).
                w_mm_nat, h_mm_nat = image_utils.image_natural_size_mm(file_path)
                nat_aspect = (w_mm_nat / h_mm_nat) if h_mm_nat > 0 else 1.0
                cur_long = max(asset.preset.width_mm, asset.preset.height_mm) or 40.0
                if nat_aspect >= 1.0:
                    new_w = cur_long
                    new_h = cur_long / nat_aspect
                else:
                    new_h = cur_long
                    new_w = cur_long * nat_aspect
                asset.preset.width_mm = round(new_w, 1)
                asset.preset.height_mm = round(new_h, 1)
                asset.updated_at = time.time()
                raw.update(asset.to_dict())
                self._write(data)
                return asset
            return None

    def match_preset_aspect(self, asset_id: str) -> Optional[Asset]:
        """Reset the preset's width/height to match the image's natural aspect.

        Keeps the longer side at the current preset's longer side so the
        on-page scale stays close to what the user configured before.
        """
        with self._lock:
            data = self._read()
            for raw in data["assets"]:
                if raw["id"] != asset_id:
                    continue
                asset = Asset.from_dict(raw)
                file_path = self._files_dir / asset.file_key
                if not file_path.exists():
                    return None
                w_nat, h_nat = image_utils.image_natural_size_mm(file_path)
                if h_nat <= 0 or w_nat <= 0:
                    return None
                aspect = w_nat / h_nat
                cur_long = max(asset.preset.width_mm, asset.preset.height_mm) or 40.0
                if aspect >= 1.0:
                    asset.preset.width_mm = round(cur_long, 1)
                    asset.preset.height_mm = round(cur_long / aspect, 1)
                else:
                    asset.preset.height_mm = round(cur_long, 1)
                    asset.preset.width_mm = round(cur_long * aspect, 1)
                asset.updated_at = time.time()
                raw.update(asset.to_dict())
                self._write(data)
                return asset
            return None

    def set_default(self, asset_id: str) -> Optional[Asset]:
        with self._lock:
            data = self._read()
            target = None
            for raw in data["assets"]:
                if raw["id"] == asset_id:
                    target = raw
                    break
            if not target:
                return None
            for raw in data["assets"]:
                if raw["type"] == target["type"]:
                    raw["is_default"] = raw["id"] == asset_id
            self._write(data)
            return Asset.from_dict(target)

    def delete(self, asset_id: str) -> bool:
        with self._lock:
            data = self._read()
            new_list = []
            removed = None
            for raw in data["assets"]:
                if raw["id"] == asset_id:
                    removed = raw
                    continue
                new_list.append(raw)
            if not removed:
                return False
            data["assets"] = new_list
            self._write(data)
            for k in ("file_key", "thumb_key"):
                p = self._files_dir / removed[k]
                if p.exists():
                    p.unlink()
            return True

    def file_path(self, asset: Asset) -> Path:
        return self._files_dir / asset.file_key

    def thumb_path(self, asset: Asset) -> Path:
        return self._files_dir / asset.thumb_key


asset_manager = AssetManager()
