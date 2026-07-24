"""全站設定匯出 / 匯入 — 以「類別」為單位，匯出與匯入都可逐項勾選。

每個設定類別（category）對應 data/ 內的一組檔案 / 目錄，或（RBAC）auth.sqlite
內的角色/權限資料。匯出時 admin 可勾選要包含哪些類別（預設全含，歷史記錄預設
不含）；匯入時先讀備份檔的 manifest 看裡面有哪些類別，再逐項勾選只還原哪些。

**RBAC 類別**（角色與權限）匯出：roles（含 is_default_for_new）、role_perms、
role_seed_snapshot、以及 OU 層級的權限規則（subject_type='ou'，key 是 DN 字串，
可跨機）。**不含**使用者帳號 / 密碼 hash、也不含綁 user/group id 的個別指派
（那些換機器 id 對不上，搬過去沒意義）。

**永遠不含**：temp/ jobs/ audit.sqlite、auth.sqlite 的 users/密碼。

匯入是「合併」非「整清」：覆寫前先把現有對應檔備份成 `<name>.bak.<ts>`。
匯出檔名：`jtdt-settings-YYYYMMDD-HHMMSS-vX.Y.Z.zip`
"""
from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Optional

from ..config import settings

MANIFEST_NAME = "manifest.json"
RBAC_NAME = "rbac.json"


# ---- Category registry --------------------------------------------------
# kind:
#   "files" — items are single files relative to data_dir
#   "dirs"  — items are directories relative to data_dir (packed recursively)
#   "rbac"  — special: dumped from / merged into auth.sqlite (see _rbac_*)
# default: pre-checked in the export UI (history is opt-in).
CATEGORIES: list[dict] = [
    {"id": "auth", "label": "認證設定", "kind": "files",
     "items": ["auth_settings.json"],
     "desc": "認證後端 / LDAP / AD / OIDC / SAML / Reverse Proxy SSO 設定", "default": True},
    {"id": "rbac", "label": "角色與權限", "kind": "rbac", "items": [],
     "desc": "角色定義、工具權限、新使用者預設角色、OU 權限規則（不含使用者 / 密碼）",
     "default": True},
    {"id": "profile", "label": "公司資料", "kind": "files",
     "items": ["profile.json"], "desc": "office_profile 公司資料 profile", "default": True},
    {"id": "office_profile", "label": "公司資料（附件）", "kind": "dirs",
     "items": ["office_profile"], "desc": "profile 附帶檔案", "default": True},
    {"id": "synonyms", "label": "同義詞對照", "kind": "files",
     "items": ["label_synonyms.json"], "desc": "欄位標籤同義詞", "default": True},
    {"id": "form_templates", "label": "表單範本", "kind": "files",
     "items": ["form_templates.json"], "desc": "pdf-fill 表單範本", "default": True},
    {"id": "api_tokens", "label": "API Token", "kind": "files",
     "items": ["api_tokens.json"], "desc": "對外 API 存取權杖（敏感）",
     "default": True, "sensitive": True},
    {"id": "llm", "label": "LLM 設定", "kind": "files",
     "items": ["llm_settings.json"], "desc": "LLM server / 模型 / 參數", "default": True},
    {"id": "office_paths", "label": "Office 路徑", "kind": "files",
     "items": ["office_paths.json"], "desc": "soffice / OxOffice 執行檔路徑", "default": True},
    {"id": "fonts", "label": "自訂字型", "kind": "files_and_dirs",
     "items": ["font_settings.json"], "dirs": ["fonts"],
     "desc": "上傳的 TTF / OTF 字型 + 設定", "default": True},
    {"id": "assets", "label": "資產（印章 / 簽名 / Logo）", "kind": "dirs",
     "items": ["assets"], "desc": "印章 / 簽名 / logo 圖與 metadata", "default": True},
    {"id": "branding", "label": "品牌 Logo", "kind": "dirs",
     "items": ["branding"], "desc": "企業 logo", "default": True},
    {"id": "history_fill", "label": "表單填寫歷史", "kind": "dirs",
     "items": ["fill_history"], "desc": "使用者填單歷史（量大，搬機通常不需要）",
     "default": False},
    {"id": "history_stamp", "label": "用印簽名歷史", "kind": "dirs",
     "items": ["stamp_history"], "desc": "用印歷史", "default": False},
    {"id": "history_watermark", "label": "浮水印歷史", "kind": "dirs",
     "items": ["watermark_history"], "desc": "浮水印歷史", "default": False},
]

_CAT_BY_ID = {c["id"]: c for c in CATEGORIES}


def _cat_files(cat: dict) -> list[str]:
    if cat["kind"] in ("files", "files_and_dirs"):
        return list(cat.get("items", []))
    return []


def _cat_dirs(cat: dict) -> list[str]:
    if cat["kind"] == "dirs":
        return list(cat.get("items", []))
    if cat["kind"] == "files_and_dirs":
        return list(cat.get("dirs", []))
    return []


def _dir_stats(p: Path) -> tuple[int, int]:
    total = count = 0
    if p.is_dir():
        for f in p.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
                count += 1
    return count, total


# ---- RBAC dump / merge (auth.sqlite) ------------------------------------

def _rbac_dump() -> dict:
    """Serialise portable RBAC config from auth.sqlite. No users/passwords."""
    from . import auth_db
    conn = auth_db.conn()
    roles = [dict(r) for r in conn.execute(
        "SELECT id, display_name, description, is_builtin, is_protected, "
        "is_default_for_new FROM roles").fetchall()]
    role_perms = [[r["role_id"], r["tool_id"]] for r in conn.execute(
        "SELECT role_id, tool_id FROM role_perms").fetchall()]
    snapshot = [[r["role_id"], r["tool_id"]] for r in conn.execute(
        "SELECT role_id, tool_id FROM role_seed_snapshot").fetchall()]
    # OU-level assignments only (subject_key is a DN string → portable).
    ou_roles = [[r["subject_key"], r["role_id"]] for r in conn.execute(
        "SELECT subject_key, role_id FROM subject_roles WHERE subject_type='ou'"
    ).fetchall()]
    ou_perms = [[r["subject_key"], r["tool_id"]] for r in conn.execute(
        "SELECT subject_key, tool_id FROM subject_perms WHERE subject_type='ou'"
    ).fetchall()]
    return {"roles": roles, "role_perms": role_perms,
            "role_seed_snapshot": snapshot,
            "ou_subject_roles": ou_roles, "ou_subject_perms": ou_perms}


def _rbac_summary() -> dict:
    try:
        d = _rbac_dump()
        return {"roles": len(d["roles"]), "role_perms": len(d["role_perms"]),
                "ou_rules": len(d["ou_subject_roles"]) + len(d["ou_subject_perms"])}
    except Exception:
        return {"roles": 0, "role_perms": 0, "ou_rules": 0}


def _rbac_merge(data: dict) -> dict:
    """Merge an RBAC dump into auth.sqlite. Upserts role definitions + perms +
    snapshot + OU rules. Custom roles are created; built-in role metadata is
    updated but is_builtin/is_protected are preserved from the imported flag.
    Enforces the single is_default_for_new invariant. Returns a small summary.
    """
    from . import auth_db, db
    # Roles that a crafted import backup must NEVER be able to hand out — a
    # confused-deputy admin importing an attacker's "backup" could otherwise
    # escalate: make `admin` the new-user default, or grant admin to a whole
    # OU. Mirror roles._INELIGIBLE_DEFAULT_ROLES.
    _INELIGIBLE_ROLES = {"admin", "auditor"}
    conn = auth_db.conn()
    roles = data.get("roles") or []
    role_perms = data.get("role_perms") or []
    snapshot = data.get("role_seed_snapshot") or []
    ou_roles = data.get("ou_subject_roles") or []
    ou_perms = data.get("ou_subject_perms") or []
    now = time.time()
    imported_default = None
    with db.tx(conn):
        for r in roles:
            rid = r.get("id")
            if not rid:
                continue
            exists = conn.execute("SELECT 1 FROM roles WHERE id=?", (rid,)).fetchone()
            if exists:
                conn.execute(
                    "UPDATE roles SET display_name=?, description=? WHERE id=?",
                    (r.get("display_name") or rid, r.get("description") or "", rid))
            else:
                # An imported (new) role is always a CUSTOM role: force
                # is_builtin=0 / is_protected=0 so a backup can't plant an
                # undeletable "protected/builtin" fake role.
                conn.execute(
                    "INSERT INTO roles(id, display_name, description, is_builtin, "
                    "is_protected, is_default_for_new, created_at) "
                    "VALUES (?,?,?,0,0,0,?)",
                    (rid, r.get("display_name") or rid, r.get("description") or "", now))
            # Never let an import set admin/auditor as the new-user default
            # (that would bypass roles.set_default_role_id's guard and make
            # every JIT-provisioned user an admin).
            if r.get("is_default_for_new") and rid not in _INELIGIBLE_ROLES:
                imported_default = rid
        # Replace role_perms for the imported roles only.
        imported_role_ids = {r.get("id") for r in roles if r.get("id")}
        for rid in imported_role_ids:
            conn.execute("DELETE FROM role_perms WHERE role_id=?", (rid,))
            conn.execute("DELETE FROM role_seed_snapshot WHERE role_id=?", (rid,))
        for rid, tool in role_perms:
            if rid in imported_role_ids:
                conn.execute("INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
                             "VALUES (?,?)", (rid, tool))
        for rid, tool in snapshot:
            if rid in imported_role_ids:
                conn.execute("INSERT OR IGNORE INTO role_seed_snapshot(role_id, "
                             "tool_id) VALUES (?,?)", (rid, tool))
        for key, rid in ou_roles:
            # Block importing an OU→admin/auditor grant (e.g. granting admin to
            # the domain root DN = escalate the whole directory).
            if rid in _INELIGIBLE_ROLES:
                continue
            conn.execute("INSERT OR IGNORE INTO subject_roles(subject_type, "
                         "subject_key, role_id) VALUES ('ou', ?, ?)", (key, rid))
        for key, tool in ou_perms:
            conn.execute("INSERT OR IGNORE INTO subject_perms(subject_type, "
                         "subject_key, tool_id) VALUES ('ou', ?, ?)", (key, tool))
        if imported_default:
            conn.execute("UPDATE roles SET is_default_for_new=0 "
                         "WHERE is_default_for_new=1")
            conn.execute("UPDATE roles SET is_default_for_new=1 WHERE id=?",
                         (imported_default,))
    try:
        from . import permissions as _perm
        _perm.invalidate_cache()
    except Exception:
        pass
    return {"roles": len(roles), "role_perms": len(role_perms),
            "ou_rules": len(ou_roles) + len(ou_perms)}


# ---- public API ---------------------------------------------------------

def list_categories() -> list[dict]:
    """For the export UI: every category with presence + size, so admin can
    tick which to include (default-checked flag included)."""
    out = []
    for c in CATEGORIES:
        present = False
        size = 0
        count = 0
        if c["kind"] == "rbac":
            s = _rbac_summary()
            present = s["roles"] > 0
            count = s["roles"]
            detail = f"{s['roles']} 個角色 / {s['role_perms']} 項權限 / {s['ou_rules']} 條 OU 規則"
        else:
            for fn in _cat_files(c):
                p = settings.data_dir / fn
                if p.is_file():
                    present = True
                    size += p.stat().st_size
                    count += 1
            for dn in _cat_dirs(c):
                p = settings.data_dir / dn
                if p.is_dir():
                    fc, fs = _dir_stats(p)
                    if fc:
                        present = True
                    size += fs
                    count += fc
            detail = f"{count} 個檔案・{size/1024:.1f} KB" if present else "（無）"
        out.append({
            "id": c["id"], "label": c["label"], "desc": c["desc"],
            "kind": c["kind"], "default": c.get("default", True),
            "sensitive": c.get("sensitive", False),
            "present": present, "count": count, "size": size, "detail": detail,
        })
    return out


def export_to_zip(out_path: Path, selected_ids: Optional[list[str]] = None,
                  app_version: str = "") -> dict:
    """Pack the SELECTED categories into out_path. If selected_ids is None,
    include every default-on category (back-compat convenience)."""
    if selected_ids is None:
        selected_ids = [c["id"] for c in CATEGORIES if c.get("default", True)]
    selected = [c for c in CATEGORIES if c["id"] in set(selected_ids)]
    entries_by_cat: dict[str, list[str]] = {}
    files_added = 0
    total_bytes = 0
    # out_path 來自 admin 設定的匯出目錄;.resolve() 正規化（消 .. 跳脫）防禦性硬化。
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for c in selected:
            names: list[str] = []
            if c["kind"] == "rbac":
                blob = json.dumps(_rbac_dump(), ensure_ascii=False, indent=2)
                zf.writestr(RBAC_NAME, blob)
                names.append(RBAC_NAME)
                files_added += 1
                total_bytes += len(blob.encode("utf-8"))
            else:
                for fn in _cat_files(c):
                    p = settings.data_dir / fn
                    if p.is_file():
                        arc = f"data/{fn}"
                        zf.write(p, arcname=arc)
                        names.append(arc)
                        files_added += 1
                        total_bytes += p.stat().st_size
                for dn in _cat_dirs(c):
                    p = settings.data_dir / dn
                    if not p.is_dir():
                        continue
                    for f in p.rglob("*"):
                        if not f.is_file():
                            continue
                        rel = f.relative_to(settings.data_dir)
                        arc = f"data/{rel.as_posix()}"
                        zf.write(f, arcname=arc)
                        names.append(arc)
                        files_added += 1
                        total_bytes += f.stat().st_size
            if names:
                entries_by_cat[c["id"]] = names
        manifest = {
            "kind": "jtdt-settings-export",
            "schema_version": 2,
            "app_version": app_version or "unknown",
            "exported_at": time.time(),
            "exported_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "file_count": files_added,
            "total_bytes": total_bytes,
            "categories": [
                {"id": c["id"], "label": c["label"]}
                for c in selected if c["id"] in entries_by_cat
            ],
            "entries_by_category": entries_by_cat,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
    return {"out_path": str(out_path), "file_count": files_added,
            "total_bytes": total_bytes, "manifest": manifest}


def read_manifest(zip_path: Path) -> dict:
    """Read + validate a backup zip's manifest so the import UI can show which
    categories are inside. Raises ValueError if not a valid jtdt export."""
    if not zip_path.exists():
        raise FileNotFoundError(f"zip not found: {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if MANIFEST_NAME not in names:
            raise ValueError("missing manifest — not a jtdt settings export?")
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        if manifest.get("kind") != "jtdt-settings-export":
            raise ValueError(f"unknown export kind: {manifest.get('kind')!r}")
    # Present categories (schema v2). Fall back for v1 (no per-category map).
    cats = manifest.get("categories")
    if not cats:
        cats = [{"id": "legacy", "label": "（舊版備份，整包匯入）"}]
    # decorate with label from registry if available
    for c in cats:
        reg = _CAT_BY_ID.get(c["id"])
        if reg:
            c["desc"] = reg["desc"]
    manifest["categories"] = cats
    return manifest


def import_from_zip(zip_path: Path, selected_ids: Optional[list[str]] = None,
                    app_version: str = "") -> dict:
    """Restore only the SELECTED categories from a backup zip. Backs up any
    existing target file/dir to `<name>.bak.<ts>` first. selected_ids=None
    restores everything in the zip (back-compat)."""
    if not zip_path.exists():
        raise FileNotFoundError(f"zip not found: {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if MANIFEST_NAME not in names:
            raise ValueError("missing manifest — not a jtdt settings export?")
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        if manifest.get("kind") != "jtdt-settings-export":
            raise ValueError(f"unknown export kind: {manifest.get('kind')!r}")
        # zip-slip defence + decompression-bomb cap: only manifest, rbac.json,
        # and data/ entries allowed; reject archives that expand beyond sane
        # limits (a small crafted zip can inflate to GBs → OOM/disk fill).
        _MAX_TOTAL = 2 * 1024 * 1024 * 1024   # 2 GiB total uncompressed
        _MAX_FILE = 512 * 1024 * 1024         # 512 MiB per member
        total_uncompressed = 0
        for info in zf.infolist():
            name = info.filename
            if name in (MANIFEST_NAME, RBAC_NAME):
                continue
            if not name.startswith("data/") or ".." in Path(name).parts:
                raise ValueError(f"unsafe path in zip: {name!r}")
            if info.file_size > _MAX_FILE:
                raise ValueError("備份檔內有過大的檔案（疑似解壓縮炸彈），已中止")
            total_uncompressed += info.file_size
            if total_uncompressed > _MAX_TOTAL:
                raise ValueError("備份檔解壓後過大（疑似解壓縮炸彈），已中止")

        entries_by_cat = manifest.get("entries_by_category") or {}
        is_legacy = not entries_by_cat
        if selected_ids is None:
            wanted_entries = set(n for n in names if n not in (MANIFEST_NAME,))
            do_rbac = RBAC_NAME in names
        elif is_legacy:
            # v1 backup has no category map — restore all its data/ entries.
            wanted_entries = set(n for n in names if n.startswith("data/"))
            do_rbac = RBAC_NAME in names and "rbac" in selected_ids
        else:
            wanted_entries = set()
            for cid in selected_ids:
                for n in (entries_by_cat.get(cid) or []):
                    if n != RBAC_NAME:
                        wanted_entries.add(n)
            do_rbac = ("rbac" in selected_ids) and (RBAC_NAME in names)

        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_paths: list[str] = []
        imported_files = 0
        restored_cats: list[str] = []

        # Back up + extract file/dir entries.
        # Back up each distinct top-level target once.
        backed_up: set[str] = set()
        for name in sorted(wanted_entries):
            rel = Path(name).relative_to("data")
            target = settings.data_dir / rel
            top = rel.parts[0] if rel.parts else ""
            if top and top not in backed_up:
                backed_up.add(top)
                src_top = settings.data_dir / top
                if src_top.exists():
                    bak = settings.data_dir / f"{top}.bak.{ts}"
                    if src_top.is_dir():
                        shutil.copytree(src_top, bak, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src_top, bak)
                    backup_paths.append(str(bak))
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf2, zf2.open(name) as src, \
                    open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            imported_files += 1

        rbac_result = None
        if do_rbac:
            with zipfile.ZipFile(zip_path, "r") as zf2:
                blob = zf2.read(RBAC_NAME).decode("utf-8")
            rbac_result = _rbac_merge(json.loads(blob))
            restored_cats.append("rbac")

    # Which categories were actually restored (for the response).
    if not is_legacy:
        for cid in (selected_ids if selected_ids is not None
                    else list(entries_by_cat.keys())):
            if cid != "rbac" and entries_by_cat.get(cid):
                restored_cats.append(cid)

    return {"imported_files": imported_files, "manifest": manifest,
            "backup_paths": backup_paths, "restored_categories": restored_cats,
            "rbac": rbac_result,
            "imported_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S")}
