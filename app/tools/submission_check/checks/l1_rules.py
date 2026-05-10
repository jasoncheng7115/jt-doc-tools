"""L1 規則檢查 — 100% deterministic、不需 OCR / LLM。

每檔回傳一份 findings list，每筆 finding：
  {
    "layer": "L1",
    "severity": "fail" | "warn" | "info",
    "category": "metadata-leak" | "js" | "open-action" | "embed" | "uri" |
                "form-blank" | "incremental" | "duplicate-hash" | ...
    "title": str,            # 簡短中文標題
    "detail": str,           # 詳細說明
    "page": int | None,      # PDF 頁碼 (1-based)
    "evidence": dict,        # 額外證據（值依 category 而定）
  }
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional


# 既有 hidden-scan 的雷達 — 我們重用它，不重複造輪子。
def _scan_pdf(path: Path) -> list[dict]:
    """對單份 PDF 做 L1 規則掃描。"""
    findings: list[dict] = []
    try:
        import fitz
    except ImportError:
        return findings

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        findings.append({
            "layer": "L1", "severity": "fail",
            "category": "open-fail",
            "title": "PDF 無法開啟",
            "detail": f"檔案損毀或受密碼保護：{e}",
            "page": None, "evidence": {},
        })
        return findings

    try:
        # === 結構面：metadata 殘留 ===
        meta = doc.metadata or {}
        suspect_meta_fields = {}
        for k in ("author", "title", "subject", "creator", "producer", "keywords"):
            v = (meta.get(k) or "").strip()
            if not v:
                continue
            # 檢查是否含可疑字樣
            if any(s in v.lower() for s in ("user", "users", "/users/", "c:\\", "d:\\", "documents")):
                suspect_meta_fields[k] = v
            elif re.search(r"\b\d{8}\b", v):  # 統編
                suspect_meta_fields[k] = v
            elif re.search(r"(股份有限公司|有限公司|企業社|商號|行號)", v):
                suspect_meta_fields[k] = v
        if suspect_meta_fields:
            findings.append({
                "layer": "L1", "severity": "warn",
                "category": "metadata-leak",
                "title": "PDF metadata 含可疑殘留",
                "detail": f"以下欄位可能洩漏先前作業者 / 公司資訊：{list(suspect_meta_fields.keys())}",
                "page": None,
                "evidence": {"metadata": suspect_meta_fields},
            })

        # === 結構面：JS / OpenAction ===
        try:
            cat = doc.pdf_catalog()
            cat_obj = doc.xref_object(cat, compressed=False) if cat else ""
            if "/JavaScript" in cat_obj or "/JS" in cat_obj:
                findings.append({
                    "layer": "L1", "severity": "fail",
                    "category": "js",
                    "title": "PDF 含 JavaScript",
                    "detail": "Catalog 內含 JavaScript 動作，送件前應清除（可一鍵跑 pdf-hidden-scan）。",
                    "page": None, "evidence": {},
                })
            if "/OpenAction" in cat_obj:
                findings.append({
                    "layer": "L1", "severity": "warn",
                    "category": "open-action",
                    "title": "PDF 含 OpenAction（開檔即執行）",
                    "detail": "開檔即執行的動作可能讓對方收到時被誤觸。",
                    "page": None, "evidence": {},
                })
        except Exception:
            pass

        # === 結構面：嵌入檔 ===
        try:
            for name in (doc.embfile_names() or []):
                findings.append({
                    "layer": "L1", "severity": "warn",
                    "category": "embed",
                    "title": f"PDF 含嵌入檔：{name}",
                    "detail": "PDF 內嵌入的檔案可能含未預期的內容，請確認是否要送出。",
                    "page": None, "evidence": {"name": name},
                })
        except Exception:
            pass

        # === 結構面：incremental update（修訂歷史） ===
        try:
            # PyMuPDF 0.22+ 有 doc.is_dirty / doc.has_history; fallback 用 xref 大小判斷
            xref_count = doc.xref_length()
            page_count = doc.page_count
            if xref_count > page_count * 50 and page_count > 0:
                # heuristic: xref 異常多 → 可能有 incremental 殘留
                findings.append({
                    "layer": "L1", "severity": "info",
                    "category": "incremental",
                    "title": "PDF 物件數異常多（可能含修訂歷史）",
                    "detail": f"xref entries={xref_count}, pages={page_count}；建議送件前用 pdf-metadata 重存乾淨版。",
                    "page": None, "evidence": {"xref_count": xref_count},
                })
        except Exception:
            pass

        # === 結構面：表單空白 widget ===
        for pno in range(doc.page_count):
            page = doc[pno]
            try:
                for w in (page.widgets() or []):
                    val = (w.field_value or "").strip()
                    name = (w.field_name or "").strip()
                    # 必填欄位（看 field_flags）— 簡化：所有欄位空白都標 info
                    if not val and name:
                        findings.append({
                            "layer": "L1", "severity": "info",
                            "category": "form-blank",
                            "title": f"表單欄位「{name}」未填",
                            "detail": "PDF 表單欄位空白，請確認是否需要填寫。",
                            "page": pno + 1,
                            "evidence": {"field_name": name},
                        })
            except Exception:
                pass

    finally:
        doc.close()

    return findings


def _scan_docx(path: Path) -> list[dict]:
    """對 .docx 做 L1 規則掃描 — track changes / comments / hidden / metadata。"""
    findings: list[dict] = []
    try:
        from zipfile import ZipFile
        import xml.etree.ElementTree as ET
    except ImportError:
        return findings

    NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
          "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
          "dc": "http://purl.org/dc/elements/1.1/"}

    try:
        with ZipFile(path) as z:
            names = z.namelist()
            # core.xml metadata
            if "docProps/core.xml" in names:
                try:
                    root = ET.fromstring(z.read("docProps/core.xml"))
                    creator = (root.findtext("dc:creator", "", NS) or "").strip()
                    last_mod_by = (root.findtext("cp:lastModifiedBy", "", NS) or "").strip()
                    if creator or last_mod_by:
                        findings.append({
                            "layer": "L1", "severity": "warn",
                            "category": "metadata-leak",
                            "title": "DOCX metadata 含作者資訊",
                            "detail": f"creator={creator!r}, lastModifiedBy={last_mod_by!r}",
                            "page": None,
                            "evidence": {"creator": creator, "last_modified_by": last_mod_by},
                        })
                except Exception:
                    pass
            # track changes
            if "word/document.xml" in names:
                try:
                    body = z.read("word/document.xml").decode("utf-8", errors="replace")
                    if "<w:ins " in body or "<w:del " in body:
                        findings.append({
                            "layer": "L1", "severity": "warn",
                            "category": "track-changes",
                            "title": "DOCX 含追蹤修訂",
                            "detail": "文件內仍有未接受 / 未拒絕的追蹤修訂，可能洩漏編輯過程。",
                            "page": None, "evidence": {},
                        })
                    if "<w:commentReference" in body or "<w:commentRangeStart" in body:
                        findings.append({
                            "layer": "L1", "severity": "warn",
                            "category": "comments",
                            "title": "DOCX 含註解（comments）",
                            "detail": "文件內仍有審閱註解，請確認是否要外送。",
                            "page": None, "evidence": {},
                        })
                except Exception:
                    pass
            # macro
            if any(n.startswith("word/vbaProject") for n in names):
                findings.append({
                    "layer": "L1", "severity": "fail",
                    "category": "macro",
                    "title": "DOCX 含 VBA macro",
                    "detail": "送件前應移除 macro。",
                    "page": None, "evidence": {},
                })
    except Exception as e:
        findings.append({
            "layer": "L1", "severity": "fail",
            "category": "open-fail",
            "title": "DOCX 無法解析",
            "detail": str(e),
            "page": None, "evidence": {},
        })

    return findings


def _validate_tax_id(s: str) -> bool:
    """台灣統編 8 碼校驗。"""
    if not s.isdigit() or len(s) != 8:
        return False
    weights = [1, 2, 1, 2, 1, 2, 4, 1]
    total = 0
    for i, ch in enumerate(s):
        prod = int(ch) * weights[i]
        total += (prod // 10) + (prod % 10)
    if s[6] == "7":
        return total % 10 == 0 or (total + 1) % 10 == 0
    return total % 10 == 0


def extract_tax_ids(text: str) -> list[tuple[str, bool]]:
    """從文字抽 8 碼數字 + 校驗，回 [(統編, 通過校驗?)]。"""
    results = []
    seen = set()
    for m in re.finditer(r"\b\d{8}\b", text):
        v = m.group(0)
        if v in seen:
            continue
        seen.add(v)
        results.append((v, _validate_tax_id(v)))
    return results


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_file(path: Path, mime_hint: str = "") -> list[dict]:
    """L1 主 entry — 依檔案類型派發。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf" or "pdf" in mime_hint:
        return _scan_pdf(path)
    if suffix in (".docx", ".doc") or "word" in mime_hint:
        return _scan_docx(path)
    # JPG / PNG 等圖片：L1 沒太多可查（EXIF 之類後續再加）
    return []


def cross_file_duplicate_hash(files: list[dict]) -> list[dict]:
    """跨檔 hash 重複檢查。
    `files` 是 case.files list，每項要有 sha256。
    """
    findings: list[dict] = []
    by_hash: dict[str, list[str]] = {}
    for f in files:
        h = f.get("sha256")
        if not h:
            continue
        by_hash.setdefault(h, []).append(f.get("name", "?"))
    for h, names in by_hash.items():
        if len(names) > 1:
            findings.append({
                "layer": "L1", "severity": "warn",
                "category": "duplicate-hash",
                "title": f"檔案內容完全重複（{len(names)} 份）",
                "detail": f"以下檔案 SHA-256 相同，可能誤上傳：{', '.join(names)}",
                "page": None,
                "evidence": {"sha256": h, "files": names},
            })
    return findings
