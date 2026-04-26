"""Generic company-profile store.

A profile is a flat dict[str, str]: arbitrary key → value. Keys typically come
from `pdf_form_detect.LABEL_MAP` so detected PDF labels can be auto-filled,
but users may add any key they need (e.g. for a one-off form).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from ..config import settings


# Default field *schema* shown when a new profile is first created.
# Tuple format: (key, label_zh, default_value).
# IMPORTANT: Default values MUST be empty strings — real data lives in
# ``data/profile.json`` on the user's own machine. Do not commit user data
# here: anyone who gets a copy of this tool would inherit it.
DEFAULT_FIELDS: list[tuple[str, str, str]] = [
    # --- 公司基本 ---
    ("company_name", "公司全名", ""),
    ("short_name", "公司簡稱", ""),
    ("english_name", "公司英文全名", ""),
    ("english_short_name", "公司英文簡稱", ""),
    ("tax_id", "統一編號", ""),
    ("id_card_no", "負責人身分證字號", ""),
    ("registration_no", "登記字號", ""),
    ("founded_date", "成立日期", ""),
    ("duns", "D-U-N-S 編號", ""),
    ("main_items", "主要營業項目", ""),
    ("capital", "資本額", ""),
    ("employees", "員工人數", ""),
    ("revenue", "營業額", ""),
    ("business_type", "經營型態", ""),
    ("industry_role", "廠商類別", ""),
    ("country", "國家別", ""),

    # --- 代表人 ---
    ("owner", "負責人", ""),
    ("owner_title_zh", "代表人職稱", ""),
    ("owner_en", "代表人英文姓名", ""),
    ("owner_title_en", "代表人英文職稱", ""),

    # --- 地址 / 聯絡 ---
    ("address", "公司地址", ""),
    ("english_address", "英文地址", ""),
    ("invoice_address", "發票地址", ""),
    ("factory_address", "工廠地址", ""),
    ("zip_code", "郵遞區號", ""),
    ("phone", "公司電話", ""),
    ("fax", "傳真", ""),
    ("extension", "分機", ""),
    ("mobile", "手機", ""),
    ("email", "聯絡人郵件", ""),
    ("company_email", "公司信箱", ""),
    ("company_website", "公司網站", ""),
    ("contact", "聯絡人", ""),
    ("sales_contact", "業務聯絡", ""),
    ("primary_contact", "聯絡窗口", ""),
    ("accounting_contact", "會計出納", ""),

    # --- 國內銀行 ---
    ("bank_name", "銀行名稱", ""),
    ("bank_code", "銀行代碼", ""),
    ("bank_branch", "銀行分行", ""),
    ("bank_branch_code", "分行代碼", ""),
    ("bank_address", "銀行地址", ""),
    ("bank_account_name", "銀行戶名", ""),
    ("bank_account_no", "銀行帳號", ""),
    ("account_type", "存款種類", ""),
    ("bank_country", "銀行國別", ""),

    # --- 交易 / 發票 ---
    ("payment_method", "付款方式", ""),
    ("payment_terms", "付款條件", ""),
    ("payment_location", "要求付款地點", ""),
    ("currency", "交易幣別", ""),
    ("tax_type", "稅金計算", ""),
    ("vat_status", "營業稅", ""),
    ("vat_rate", "稅率(%)", ""),
    ("invoice_title", "發票抬頭", ""),
    ("invoice_type", "發票種類", ""),
    ("closing_date", "結帳日", ""),

    # --- 外幣 / 國際匯款 ---
    ("trade_terms", "貿易條件 Incoterms", ""),
    ("payee_en", "Payee / Beneficiary", ""),
    ("payee_address_en", "Beneficiary Address", ""),
    ("foreign_account_no", "外幣帳戶", ""),
    ("beneficiary_bank", "Beneficiary Bank", ""),
    ("beneficiary_bank_address", "Bank Address", ""),
    ("swift_code", "Swift Code", ""),

    # --- 其他 (per-vendor 欄位) ---
    ("vendor_code", "廠商代號", ""),
    ("form_action", "申請類別", ""),
    ("signing_date", "填表日期", ""),
]


# Sections for display grouping. Keys listed here appear under their section
# on the tool/admin page; any extra user-defined keys land under "其他".
SECTIONS: list[tuple[str, list[str]]] = [
    ("公司基本", ["company_name", "short_name", "english_name", "english_short_name",
                   "tax_id", "id_card_no",
                   "registration_no", "founded_date", "duns", "main_items",
                   "capital", "employees", "revenue", "business_type",
                   "industry_role", "country"]),
    ("代表人", ["owner", "owner_title_zh", "owner_en", "owner_title_en"]),
    ("地址 / 聯絡", ["address", "english_address", "invoice_address", "factory_address",
                     "zip_code",
                     "phone", "fax", "extension", "mobile", "email", "company_email",
                     "company_website", "contact",
                     "sales_contact", "primary_contact", "accounting_contact"]),
    ("國內銀行", ["bank_name", "bank_code", "bank_branch", "bank_branch_code",
                   "bank_address", "bank_account_name", "bank_account_no",
                   "account_type", "bank_country"]),
    ("交易 / 發票", ["payment_method", "payment_terms", "payment_location",
                      "currency", "tax_type",
                      "vat_status", "vat_rate", "invoice_title", "invoice_type",
                      "closing_date"]),
    ("國際匯款", ["trade_terms", "payee_en", "payee_address_en", "foreign_account_no",
                   "beneficiary_bank", "beneficiary_bank_address", "swift_code"]),
    ("其他", ["vendor_code", "form_action", "signing_date"]),
]


class ProfileManager:
    """Multi-company profile store.

    File layout::

      {
        "companies": { "<id>": { id, name, fields, labels, updated_at } },
        "active_id": "<id>",
        "updated_at": ...
      }

    There's always at least one company; the "active" one is used by
    pdf-fill by default (the UI can override per upload). Legacy
    single-company files (``{fields, labels}``) are migrated into a
    "default" company automatically on first read.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path = settings.data_dir / "profile.json"
        if not self._path.exists():
            self._write(self._initial_data())
        else:
            self._maybe_migrate()

    @staticmethod
    def _initial_data() -> dict:
        cid = "default"
        return {
            "companies": {
                cid: {
                    "id": cid,
                    "name": "節省工具箱有限公司",
                    "fields": {k: v for k, _, v in DEFAULT_FIELDS},
                    "labels": {k: lbl for k, lbl, _ in DEFAULT_FIELDS},
                    "updated_at": time.time(),
                }
            },
            "active_id": cid,
            "updated_at": time.time(),
        }

    def _maybe_migrate(self) -> None:
        data = self._read_raw()
        if "companies" in data:
            return
        cid = "default"
        name = (data.get("fields") or {}).get("company_name") or "公司 1"
        self._write({
            "companies": {
                cid: {
                    "id": cid,
                    "name": name,
                    "fields": data.get("fields", {}),
                    "labels": data.get("labels", {}),
                    "updated_at": data.get("updated_at", time.time()),
                }
            },
            "active_id": cid,
            "updated_at": time.time(),
        })

    def _read_raw(self) -> dict:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _read(self) -> dict:
        if not self._path.exists():
            return self._initial_data()
        return self._read_raw()

    def _write(self, data: dict) -> None:
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ---- Companies ----
    def list_companies(self) -> list[dict]:
        data = self._read()
        active = data.get("active_id")
        items = [
            {"id": c["id"], "name": c.get("name", c["id"]),
             "is_active": c["id"] == active,
             "updated_at": c.get("updated_at", 0)}
            for c in data.get("companies", {}).values()
        ]
        items.sort(key=lambda x: (not x["is_active"], x["name"]))
        return items

    def active_id(self) -> str:
        data = self._read()
        cid = data.get("active_id")
        if cid and cid in data.get("companies", {}):
            return cid
        # Fall back to any
        for k in data.get("companies", {}).keys():
            return k
        # Empty file — shouldn't happen but reset defensively.
        self._write(self._initial_data())
        return "default"

    def set_active(self, cid: str) -> bool:
        with self._lock:
            data = self._read()
            if cid not in data.get("companies", {}):
                return False
            data["active_id"] = cid
            data["updated_at"] = time.time()
            self._write(data)
            return True

    def create(self, name: str, copy_from_id: Optional[str] = None) -> dict:
        with self._lock:
            data = self._read()
            cid = uuid.uuid4().hex[:10]
            if copy_from_id and copy_from_id in data["companies"]:
                src = data["companies"][copy_from_id]
                fields = dict(src.get("fields", {}))
                labels = dict(src.get("labels", {}))
            else:
                fields = {k: v for k, _, v in DEFAULT_FIELDS}
                labels = {k: lbl for k, lbl, _ in DEFAULT_FIELDS}
            company = {
                "id": cid,
                "name": name or "新公司",
                "fields": fields,
                "labels": labels,
                "updated_at": time.time(),
            }
            data["companies"][cid] = company
            data["updated_at"] = time.time()
            self._write(data)
            return company

    def delete(self, cid: str) -> bool:
        with self._lock:
            data = self._read()
            if cid not in data.get("companies", {}):
                return False
            if len(data["companies"]) <= 1:
                return False
            del data["companies"][cid]
            if data.get("active_id") == cid:
                data["active_id"] = next(iter(data["companies"].keys()))
            data["updated_at"] = time.time()
            self._write(data)
            return True

    # ---- Company data ----
    def get(self, cid: Optional[str] = None) -> dict:
        with self._lock:
            data = self._read()
            cid = cid or self.active_id()
            company = data["companies"].get(cid) or next(
                iter(data["companies"].values()), {}
            )
            company.setdefault("fields", {})
            company.setdefault("labels", {})
            return company

    def get_field(self, key: str, cid: Optional[str] = None) -> Optional[str]:
        return self.get(cid)["fields"].get(key)

    def save(
        self,
        cid: str,
        name: str,
        fields: dict[str, str],
        labels: dict[str, str],
    ) -> None:
        with self._lock:
            data = self._read()
            company = data["companies"].setdefault(cid, {"id": cid})
            company["id"] = cid
            company["name"] = (name or company.get("name") or cid).strip()
            company["fields"] = {k: (v or "") for k, v in fields.items()}
            company["labels"] = {k: (labels.get(k) or k) for k in fields.keys()}
            company["updated_at"] = time.time()
            data["updated_at"] = time.time()
            self._write(data)

    def known_keys(self, cid: Optional[str] = None) -> list[str]:
        return list(self.get(cid)["fields"].keys())

    def get_sectioned(self, cid: Optional[str] = None) -> list[dict]:
        company = self.get(cid)
        fields: dict[str, str] = company["fields"]
        labels: dict[str, str] = company["labels"]
        seen: set[str] = set()
        out: list[dict] = []
        for title, keys in SECTIONS:
            rows = []
            for k in keys:
                if k in fields:
                    rows.append({"key": k, "label": labels.get(k, k), "value": fields[k]})
                    seen.add(k)
            if rows:
                out.append({"title": title, "rows": rows})
        extras = [
            {"key": k, "label": labels.get(k, k), "value": fields[k]}
            for k in fields.keys() if k not in seen
        ]
        if extras:
            out.append({"title": "其他", "rows": extras})
        return out

    def get_sections_for_edit(self, cid: Optional[str] = None) -> list[dict]:
        """Like :meth:`get_sectioned` but includes *every* row — even empty
        values — so the admin edit page can render all fields regardless of
        whether they've been filled yet."""
        company = self.get(cid)
        fields: dict[str, str] = company["fields"]
        labels: dict[str, str] = company["labels"]
        seen: set[str] = set()
        out: list[dict] = []
        for title, keys in SECTIONS:
            rows = []
            for k in keys:
                if k in fields:
                    rows.append({"key": k, "label": labels.get(k, k), "value": fields[k]})
                    seen.add(k)
            if rows:
                out.append({"title": title, "rows": rows})
        extras = [
            {"key": k, "label": labels.get(k, k), "value": fields[k]}
            for k in fields.keys() if k not in seen
        ]
        out.append({"title": "其他 / 自訂欄位", "rows": extras})
        return out


profile_manager = ProfileManager()
