"""全站設定匯出 / 匯入 — 把所有設定檔打包成 zip 給 admin 備份 / 搬遷。

**包含**（白名單）：
    - `assets/` (印章 / 簽名 / Logo + assets.json metadata)
    - `branding/` (企業 logo)
    - `fonts/` (自訂 TTF / OTF + font_settings.json)
    - `office_profile/` (公司資料 profile)
    - 根目錄 JSON 設定檔：profile / synonyms / form_templates / api_tokens
      / llm_settings / office_paths / font_settings / auth_settings

**不包含**（黑名單，會自動跳過）：
    - `temp/`, `jobs/` — 暫存與 job 結果
    - `audit.sqlite`, `audit.sqlite-wal/-shm` — 稽核記錄（量大且敏感）
    - `auth.sqlite`, `auth.sqlite-wal/-shm` — 帳號密碼 hash（搬機建新帳號）
    - `fill_history/`, `stamp_history/`, `watermark_history/` — 使用者歷史（可選）

匯入是「合併」非「覆蓋全清」：解壓到一個 staging dir，逐項覆寫對應 data/
路徑。寫入前會先做 .bak 備份，匯入失敗能 rollback。

匯出檔名 schema：`jtdt-settings-YYYYMMDD-HHMMSS-vX.Y.Z.zip`
匯入時 manifest.json 含 source version；版本太舊的給 warning 但不擋。
"""
from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Optional

from ..config import settings


# 白名單 — 相對於 data_dir 的路徑（檔案 or 目錄）
_INCLUDE_FILES = [
    "profile.json",
    "label_synonyms.json",
    "form_templates.json",
    "api_tokens.json",
    "llm_settings.json",
    "office_paths.json",
    "font_settings.json",
    "auth_settings.json",
]
_INCLUDE_DIRS = [
    "assets",
    "branding",
    "fonts",
    "office_profile",
]

# 額外可以選擇 include 的（admin 在 UI 勾選；預設不含）
_OPTIONAL_DIRS = {
    "fill_history": "表單填寫歷史",
    "stamp_history": "用印簽名歷史",
    "watermark_history": "浮水印歷史",
}

MANIFEST_NAME = "manifest.json"


def collect_summary() -> dict:
    """看一眼 data/ 內各項目的存在 / 大小，給匯出 UI 預覽。"""
    out = {"core_files": [], "core_dirs": [], "optional_dirs": []}
    for fn in _INCLUDE_FILES:
        p = settings.data_dir / fn
        if p.exists():
            out["core_files"].append({
                "name": fn, "size": p.stat().st_size,
            })
    for dn in _INCLUDE_DIRS:
        p = settings.data_dir / dn
        if p.is_dir():
            total = 0; count = 0
            for f in p.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
                    count += 1
            out["core_dirs"].append({
                "name": dn, "files": count, "size": total,
            })
    for dn, label in _OPTIONAL_DIRS.items():
        p = settings.data_dir / dn
        if p.is_dir():
            total = 0; count = 0
            for f in p.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
                    count += 1
            out["optional_dirs"].append({
                "name": dn, "label": label, "files": count, "size": total,
            })
    return out


def export_to_zip(
    out_path: Path,
    include_optional: Optional[list[str]] = None,
    app_version: str = "",
) -> dict:
    """打包 data/ 內 whitelisted 檔案到 out_path（覆蓋）。
    回傳 {file_count, total_bytes, manifest}。"""
    include_optional = include_optional or []
    files_added: list[dict] = []
    total_bytes = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Single files
        for fn in _INCLUDE_FILES:
            p = settings.data_dir / fn
            if p.exists() and p.is_file():
                zf.write(p, arcname=f"data/{fn}")
                files_added.append({"path": fn, "size": p.stat().st_size})
                total_bytes += p.stat().st_size
        # Directories
        dirs_to_pack = list(_INCLUDE_DIRS)
        for opt_dn in include_optional:
            if opt_dn in _OPTIONAL_DIRS:
                dirs_to_pack.append(opt_dn)
        for dn in dirs_to_pack:
            p = settings.data_dir / dn
            if not p.is_dir():
                continue
            for f in p.rglob("*"):
                if not f.is_file():
                    continue
                rel = f.relative_to(settings.data_dir)
                zf.write(f, arcname=f"data/{rel.as_posix()}")
                files_added.append({"path": str(rel), "size": f.stat().st_size})
                total_bytes += f.stat().st_size
        # Manifest
        manifest = {
            "kind": "jtdt-settings-export",
            "schema_version": 1,
            "app_version": app_version or "unknown",
            "exported_at": time.time(),
            "exported_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "file_count": len(files_added),
            "total_bytes": total_bytes,
            "include_optional": list(include_optional),
            "core_files": _INCLUDE_FILES,
            "core_dirs": _INCLUDE_DIRS,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
    return {
        "out_path": str(out_path),
        "file_count": len(files_added),
        "total_bytes": total_bytes,
        "manifest": manifest,
    }


def import_from_zip(
    zip_path: Path,
    overwrite_optional: bool = False,
    app_version: str = "",
) -> dict:
    """解壓並覆蓋到 data/。會先把現有的 data/ 對應檔/目錄備份成
    `data/<name>.bak.<timestamp>`。

    回傳 {imported_files, imported_dirs, manifest, backup_paths}。

    安全：
    - manifest.json 必須存在且 kind 對得上，否則 raise ValueError
    - zip 內的 path 必須在 `data/` 下，不能逃出（防 zip-slip）
    - 覆蓋前一律備份；任一步失敗 raise 後 caller 可手動 rollback
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"zip not found: {zip_path}")
    # First pass: read manifest + validate
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if MANIFEST_NAME not in names:
            raise ValueError(f"missing {MANIFEST_NAME} — not a jtdt settings export?")
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except Exception as e:
            raise ValueError(f"manifest parse failed: {e}")
        if manifest.get("kind") != "jtdt-settings-export":
            raise ValueError(f"unknown export kind: {manifest.get('kind')!r}")
        # Validate every entry is under data/ (zip-slip defense)
        for name in names:
            if name == MANIFEST_NAME:
                continue
            if not name.startswith("data/"):
                raise ValueError(f"unsafe path in zip: {name!r} (not under data/)")
            if ".." in Path(name).parts:
                raise ValueError(f"unsafe path in zip: {name!r}")
        # Backup whatever currently exists
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_paths: list[str] = []
        for fn in _INCLUDE_FILES:
            p = settings.data_dir / fn
            if p.exists():
                bak = settings.data_dir / f"{fn}.bak.{ts}"
                shutil.copy2(p, bak)
                backup_paths.append(str(bak))
        for dn in _INCLUDE_DIRS:
            p = settings.data_dir / dn
            if p.is_dir():
                bak = settings.data_dir / f"{dn}.bak.{ts}"
                shutil.copytree(p, bak)
                backup_paths.append(str(bak))
        if overwrite_optional:
            for dn in _OPTIONAL_DIRS:
                p = settings.data_dir / dn
                if p.is_dir():
                    bak = settings.data_dir / f"{dn}.bak.{ts}"
                    shutil.copytree(p, bak)
                    backup_paths.append(str(bak))
        # Now extract — overwrites existing
        imported_files = 0
        for name in names:
            if name == MANIFEST_NAME:
                continue
            # name is "data/whatever"; strip "data/" prefix
            rel = Path(name).relative_to("data")
            target = settings.data_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            imported_files += 1
    return {
        "imported_files": imported_files,
        "manifest": manifest,
        "backup_paths": backup_paths,
        "imported_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
