"""Generic field-label detector for flat (no-widget) PDF forms.

Scans every text span on every page, normalizes each span (strip + collapse
internal whitespace), and matches it against ``LABEL_MAP`` to produce a list
of :class:`DetectedField` records. Each record carries the page index, the
matched profile key, and an anchor point + font size so an overlay layer can
draw the value text in roughly the right place.

The matcher is intentionally string-based rather than layout-based so the
same code handles forms of arbitrary layout — adding support for a new
vendor's form usually means adding label synonyms here, not writing per-form
code.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import fitz  # PyMuPDF

from . import pdf_layout


# Canonical profile_key -> list of label synonyms that should map to it.
# Synonyms are matched after normalization (whitespace collapsed). Order
# matters only for documentation; matching is by exact set membership.
DEFAULT_LABEL_MAP: dict[str, list[str]] = {
    # 公司基本
    "company_name": ["公司全名", "廠商全名", "廠商名稱", "公司名稱", "公司中文名稱",
                      "廠商中文名稱", "供應商名稱", "廠商全銜", "公司全銜",
                      "供應商全銜", "供應商名稱全銜",
                      # New synonyms harvested from real vendor forms (廠商資料表 corpus).
                      "公司中文全名", "廠商全名 / supplier full name",
                      "公司/團體/姓名 全銜", "公司/團體/個人 名稱",
                      "立同意書人", "立書人", "立同意書人名稱",
                      "客戶名稱", "客戶名稱(中文)", "客戶中文名稱",
                      "Company Name", "Name of Company",
                      "Supplier Name", "Supplier's Name", "Supplier Full Name",
                      "Vendor Name", "Customer Name",
                      # Bilingual sub-row labels under a parent like
                      # "公司名稱 / Company Name" — see _normalize for why
                      # the bare parenthesised forms must survive.
                      "(中)", "(中文)", "(Chinese)", "(中文名稱)"],
    "short_name": ["簡稱", "公司簡稱", "廠商簡稱",
                    "廠商簡稱 / supplier abbreviation",
                    "(字母名簡稱)", "Short Name", "Abbreviation"],
    "english_name": ["英文全名", "英文名稱", "公司英文名稱", "公司英文全名",
                      "廠商英文名稱", "客戶英文名稱", "客戶名稱(英文)",
                      "English Name", "Company English Name",
                      "Name of Company (English)",
                      # Bilingual sub-row markers, see company_name above.
                      "(英)", "(英文)", "(English)", "(英文名稱)"],
    "english_short_name": ["公司英文簡稱", "英文簡稱", "English Short Name",
                            "Short Name (English)", "English Abbreviation"],
    "tax_id": ["統一編號", "統編", "統編/稅號", "稅號",
                "營利事業統一編號", "營業人統一編號", "公司統一編號",
                "營利事業(身份證)統一編號", "匯撥戶統編",
                "Tax ID", "Tax ID No", "Tax No", "Business ID", "Business ID No",
                "VAT number", "VAT No", "VAT No.",
                "Company I.D. in R.O.C.", "Company ID", "Uniform No", "Uniform Number"],
    # 身分證字號 — for sole-proprietor / 個人 vendors, often offered as an
    # alternative to 統一編號 on the same row ("統一編號 或 身分證字號").
    "id_card_no": ["身分證字號", "身份證字號", "身分證號碼", "身份證號碼",
                    "身份證統一編號", "ID Card No", "Identity Card No",
                    "National ID"],
    "registration_no": ["登記字號", "登記號碼", "公司登記字號", "公司執照登記字號",
                         "營利事業登記字號", "商業登記字號", "執照字號",
                         "商業登記證明", "登記證號", "登記證號 / Register No.",
                         "公司營業證號", "Register No", "Registration No"],
    "founded_date": ["成立日期", "設立日期", "核准設立日期", "開業日期",
                      "成立時間", "設立時間",
                      "創立日期", "創立年月日", "創立年月", "創立日期 (Founded Date)",
                      "公司成立日期", "公司成立",
                      "Founded", "Founded Date", "Founded in", "Date of Establishment",
                      "Year of Established"],
    "duns": ["D-U-N-S", "DUNS", "D-U-N-S 編號", "DUNS 編號", "D-U-N-S Number",
              "DUNS Number", "D-U-N-S No", "D-U-N-S No.",
              "鄧白氏", "鄧白氏編碼", "鄧白氏編號", "鄧白氏碼",
              "鄧白氏環球編碼", "鄧百氏"],
    "main_items": ["主要營業項目", "主要產品", "主要產品/服務", "主要服務", "營業項目",
                    "經營項目", "主要業務", "產品/服務", "公司經營產品", "經營產品",
                    "主要產品(服務)", "Main Products", "Products / Services"],
    "capital": ["資本額", "公司資本額", "實收資本額", "登記資本額", "資本總額",
                 "註冊資本額", "註冊資本", "法定資本",
                 "Capital", "Paid-in Capital", "Authorized Capital", "Net Capital"],
    "employees": ["員工人數", "員工數", "員工總數", "員工總人數", "總人數", "人數",
                   "目前員工大約人數", "員工大約人數",
                   "Employees", "Employee Count", "Total Employee", "Number of Employees",
                   "No of Employees", "Total Staff Members"],
    "revenue": ["營業額", "年營業額", "年營業總額", "去年度營業額", "營收金額",
                 "Revenue", "Annual Revenue", "Annual Turnover", "Turnover"],
    "business_type": ["經營型態", "公司型態", "組織型態", "組織類型", "事業型態",
                       "公司類型", "營業型態", "營業型態及規模",
                       "Business Type", "Organization Type", "Business Model",
                       "Type of Organization"],
    # 廠商類別 — supplier category (原廠/代理商/經銷商/貿易商/原物料供應商 …),
    # distinct from 經營型態 which is about org form (有限公司/股份公司/獨資).
    "industry_role": ["廠商類別", "廠商分類", "廠商分類 / supplier type",
                       "供應商類別", "供應商分類", "Supplier Type", "Supplier Category",
                       "Vendor Type", "Vendor Category"],
    # 國家別 — primarily for the company's country, used on cross-border forms.
    "country": ["國家", "國家別", "國別", "Country", "Country/Region"],

    # 代表人
    "owner": ["負責人", "代表人", "代表人姓名", "負責人姓名", "公司負責人",
               "公司負責人姓名", "法定代表人", "法定代理人",
               "Owner", "Chairman", "President", "Person in Charge"],
    "owner_title_zh": ["代表人職稱", "負責人職稱", "負責人職位", "職稱", "職位",
                        "現任職務", "現在職務", "部門/職稱",
                        "Position", "Job Title"],
    "owner_en": ["代表人英文姓名", "負責人英文姓名",
                  "Representative", "Representative Name", "Owner Name (English)"],
    "owner_title_en": ["代表人英文職稱", "負責人英文職稱", "Title"],

    # 地址 / 聯絡
    "address": ["營業地址", "廠商地址", "地址", "公司地址", "公司中文地址",
                 "營業登記地址", "登記地址",
                 "公司登記地址", "聯絡地址", "連絡地址", "通訊地址",
                 "稅籍地址", "稅籍登記地址",
                 "公司/團體/個人 地址",
                 "收款公司地址",
                 "Address", "Company Address", "Registered Address", "Contact Address",
                 "Office Address"],
    "english_address": ["英文地址", "公司英文地址", "Address (English)", "English Address",
                         "Company Address (English)"],
    "invoice_address": ["發票地址", "寄件地址", "發票寄送地址", "對帳單地址",
                         "帳單地址", "發票章地址", "發票寄發地址",
                         "Invoice Address", "Billing Address", "Mailing Address"],
    # 工廠地址 — distinct from 公司地址 for manufacturers.
    "factory_address": ["工廠地址", "製造廠地址", "廠房地址", "Factory Address",
                         "Plant Address", "Manufacturing Address"],
    "zip_code": ["郵遞區號", "郵碼", "Zip Code", "Postal Code", "ZIP", "Postcode"],
    "phone": ["電話", "公司電話", "聯絡電話", "連絡電話", "聯繫電話", "電話號碼",
               "Tel", "TEL", "Phone", "Telephone", "Telephone #", "Tel No", "Tel #",
               "TEL NO", "TEL NO."],
    "fax": ["傳真", "傳真號碼", "傳真電話", "公司傳真",
             "Fax", "FAX", "Fax No", "Fax #", "FAX NO"],
    "extension": ["分機", "分機號碼", "Ext", "Ext.", "Extension"],
    "mobile": ["手機", "手機號碼", "行動電話", "聯絡人行動電話",
                "Mobile", "Mobile Phone", "Cell", "Cell Phone"],
    "email": ["e-mail", "email", "E-mail", "Email", "EMAIL", "聯絡人郵件", "聯絡人 email",
               "聯絡 E-Mail", "聯絡E-Mail", "聯絡E-mail", "聯絡Email",
               "聯絡E-MAIL", "聯絡人電子郵件", "聯絡人電子郵件帳號",
               "連絡 E-Mail", "連絡E-Mail", "連絡E-mail", "連絡Email", "連絡e-mail",
               "聯絡 e-mail",
               "電子郵件信箱", "電子郵件帳號", "電子郵件地址",
               "Email Address", "E-mail Address", "E-Mail Address", "E-Mail add", "E-Mail#",
               "郵件", "信箱", "電子郵件", "電子信箱"],
    "company_email": ["公司信箱", "公司 email", "公司郵件", "公司電子信箱",
                       "公司電子郵件", "公司E-mail", "公司E-MAIL",
                       "公司E-MAIL 帳號", "公司 E-Mail",
                       "Company Email", "Company E-mail"],
    "company_website": ["公司網站", "網站", "網址", "公司網址",
                         "Website", "Web Site", "URL", "Http", "Http#", "Homepage"],
    "contact": ["聯絡人", "連絡人", "聯絡人1", "聯絡人2", "連絡人(一)", "連絡人(二)",
                 "連絡人一", "連絡人二", "聯絡人姓名", "連絡人姓名", "連絡人性名",
                 "經辦聯絡人", "經辦人", "填表人", "填寫人",
                 "姓名",
                 "Contact", "Contact Person", "Contact Name", "Filling Person",
                 "Key Officers"],
    "sales_contact": ["業務聯絡", "業務連絡", "業務聯絡(一)", "業務聯絡(二)",
                       "業務連絡(一)", "業務窗口", "業務聯絡人", "業務代表",
                       "業務助理聯絡人", "業務窗口聯絡人",
                       "(供應商)業務聯絡人", "(供應商)業務聯絡人-1",
                       "(供應商)業務聯絡人-2",
                       "Sales Contact", "Sales Rep", "Sales", "Sales Person"],
    "primary_contact": ["聯絡窗口", "連絡窗口", "聯絡窗口(一)", "聯絡窗口(二)",
                         "連絡窗口(一)", "連絡窗口(二)", "主要聯絡人", "主要連絡人",
                         "Main Contact", "Primary Contact", "Window Contact"],
    "accounting_contact": ["會計出納", "會計聯絡", "會計連絡", "會計窗口",
                            "會計聯絡人", "會計連絡人", "會計人員",
                            "出納聯絡人", "出納人員",
                            "財會聯絡", "財會連絡", "財會連絡人", "財會聯絡人",
                            "財會連絡人(三)", "財會連絡人三",
                            "財務聯絡人", "財務連絡人", "財務窗口", "財務人員",
                            "帳務聯絡", "帳務聯絡人", "帳款聯絡人",
                            "對帳聯絡人", "對帳連絡人",
                            "付款聯絡人", "付款連絡人",
                            "會計e-mail", "會計E-mail", "會計email",
                            "Finance Contact", "Accounting", "Accounting Contact",
                            "Accountant"],

    # 交易 / 發票
    "payment_method": ["付款方式", "匯款方式", "交易方式",
                        "Style of Payment", "Payment Method", "Remit By",
                        "Method of Payment"],
    "payment_terms": ["交易方式", "付款條件", "信用條件", "交易條件", "付款要求",
                       "月結方式", "付款週期", "結帳方式", "結帳日期",
                       "Payment Terms", "Payment Term", "Credit Terms"],
    "payment_location": ["要求付款地點", "付款地點", "收款地點", "匯款地點",
                          "Payment Location", "Place of Payment"],
    "currency": ["交易幣別", "幣別", "付款幣別", "訂貨貨幣", "結算幣別", "帳號貨幣",
                  "Currency", "Currency of Payment", "Invoice Currency",
                  "Account Currency"],
    # tax_type and invoice_type often share the same parent label on
    # Taiwanese vendor forms — the same row of boxes carries both questions.
    # Listing the shared labels under both keys lets the multi-key detector
    # emit a field entry for each, so the two profile values get ticked
    # independently.
    "tax_type": ["稅金計算", "稅別", "稅別碼", "課稅別", "稅金",
                  "憑證來源", "憑證種類",
                  "Tax Type", "Invoice Tax Code",
                  "發票聯數", "發票聯數 & 種類", "發票聯數及種類"],
    "vat_status": ["營業稅", "增值稅", "營業稅/增值稅", "VAT", "營業稅(VAT)", "是否課稅"],
    "vat_rate": ["稅率", "Tax Rate", "VAT Rate"],
    "invoice_title": ["發票抬頭", "**發票抬頭", "抬頭", "Invoice Title", "Invoice To"],
    "invoice_type": ["發票種類", "發票格式", "發票類別", "發票聯數",
                      "發票聯數 & 種類", "發票聯數及種類",
                      "稅別", "Invoice Type", "Invoice Format"],
    # Buyer's monthly cutoff date — distinct from payment_terms.
    "closing_date": ["結帳日", "關帳日", "關帳日期", "對帳截止日", "Cut-off Date",
                      "Closing Date"],

    # 國內銀行
    "bank_name": ["受款銀行", "銀行名稱", "銀行全名", "收款銀行", "銀行/分行名稱",
                   "銀行及分行名稱", "銀行(分行)名稱", "銀行及分行",
                   "往來銀行", "往來銀行-1",
                   "往來銀行(一)", "往來銀行-一", "主要往來銀行",
                   "本公司往來銀行", "本公司銀行", "我方往來銀行",
                   "金融機構名稱", "金融機構", "解款行",
                   "匯款銀行", "匯撥銀行", "通匯銀行",
                   "行名", "開戶銀行", "Beneficiary Bank", "Bank Name", "Bank"],
    "bank_branch": ["分行", "支局", "支行", "銀行分行", "分行名稱", "分行/支局",
                     "分行別", "分支單位", "分號名稱",
                     "Branch", "Branch Name"],
    "bank_branch_code": ["分行代碼", "分行代號", "解款行代號", "解款行代碼",
                          # 「解放行代號」是實際表單上的誤植（款→放）。表單在外面
                          # 印好幾年不會改，認得它比要求對方改表單實際。
                          "解放行代號", "解付行代號", "匯款行代號",
                          "ATM通匯金融代號",
                          "Branch Code"],
    "bank_code": ["銀行代碼", "銀行代號", "金資代號", "金融機構代碼", "金融機構代號",
                   "總行代號", "總行碼",
                   "Bank Code", "Bank ID"],
    "bank_address": ["銀行地址", "收款銀行地址", "Bank Address", "Beneficiary Bank Address"],
    "bank_account_name": ["銀行戶名", "戶名", "帳戶名稱", "帳戶戶名",
                           "開戶全名", "開戶名稱", "開戶戶名", "開戶人姓名",
                           "帳號戶名", "受款人戶名", "收款人戶名",
                           "收款人名稱", "收款戶名",
                           "匯款戶名", "匯撥戶名", "匯入戶名",
                           "Account Name", "Beneficiary Name", "Account Holder"],
    "bank_account_no": ["銀行帳號", "帳號", "收款銀行帳號", "收款帳號", "受款帳號",
                         "受款帳戶", "銀行帳號/IBAN No.", "IBAN No",
                         "甲/乙存帳號", "甲乙存帳號", "甲存帳號", "乙存帳號",
                         "活存帳號", "支存帳號", "存款帳號",
                         "匯款帳號", "匯入帳號", "收款帳號",
                         "Account No", "Account No.", "Account Number", "Bank Account No",
                         "Bank Account Number"],
    # 存款種類 (支存/活存/活儲/綜存) — needed to disambiguate 甲存/乙存 accounts.
    "account_type": ["存款種類", "帳戶種類", "存款類別",
                      "Account Type", "Type of Account"],
    # 銀行所在國別 — only relevant for foreign-currency remittance forms.
    "bank_country": ["受款地區國別", "受款銀行國別", "銀行國家", "銀行國別",
                      "Bank Country", "Country of Bank"],

    # 外幣 / 國際匯款
    "trade_terms": ["貿易條件", "貿易條件 Incoterms", "Incoterms",
                     "Trade Terms", "Trade Term"],
    "payee_en": ["Payee", "Beneficiary", "Payee/Beneficiary", "Beneficiary Name"],
    "payee_address_en": ["Complete Address", "Beneficiary Address",
                          "Beneficiary Company Address"],
    "foreign_account_no": ["外幣帳戶", "Bank Account No", "Bank Account Number",
                            "Foreign Account No"],
    "beneficiary_bank": ["Beneficiary Bank"],
    "beneficiary_bank_address": ["Bank Address"],
    "swift_code": ["Swift Code", "SWIFT", "Swift", "SWIFT Code/ ABA NO.",
                    "SWIFT Code / ABA NO.", "SWIFT/ABA", "SWIFT CODE",
                    "收款銀行代碼"],

    # 表單動作 / 申請類型 — common 新增/修改/變更 checkbox row at the top
    # of 匯款同意書 / ○○資料表. Detecting it lets us default-tick "新增"
    # when first filling for a new vendor.
    "form_action": ["申請類別", "申請類型", "申請項目", "資料異動",
                     "本次申請", "Form Action", "Application Type"],
    # 填表/簽署日期 — for auto-stamping today's date.
    "signing_date": ["填表日期", "填寫日期", "申請日期", "簽署日期", "簽訂日期",
                      "簽章日期", "日期", "Date", "Form Date", "Filling Date",
                      "Submission Date"],

    # per-vendor
    "vendor_code": ["廠商代號", "廠商編號", "供應商代碼", "供應商編號",
                     "客戶代號", "客戶編號", "ERP編號", "SAP編號", "SAP 編號",
                     "Site Code", "Vendor Code", "Vendor No", "Vendor Number",
                     "Supplier Code", "Supplier Number", "Customer Code", "ID No"],
}

# Backward-compatible alias: callers that imported LABEL_MAP still work, but
# the authoritative source at runtime is :mod:`synonym_manager` (user-editable).
LABEL_MAP = DEFAULT_LABEL_MAP


def _active_label_map() -> dict[str, list[str]]:
    """Prefer the user-editable synonym store; fall back to DEFAULT_LABEL_MAP."""
    try:
        from .synonym_manager import synonym_manager
        return synonym_manager.get_map() or DEFAULT_LABEL_MAP
    except Exception:
        return DEFAULT_LABEL_MAP


def _build_synonym_index(label_map: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return a map from normalized synonym → *list* of canonical keys.

    Some shared labels (e.g. "發票聯數 & 種類" covering both 發票種類 and
    稅別 on Taiwanese forms) legitimately belong to multiple canonical keys,
    so a single label match can emit more than one DetectedField — one per
    key — letting each profile value try to tick its own option.
    """
    out: dict[str, list[str]] = {}
    for key, syns in label_map.items():
        for s in syns:
            out.setdefault(_normalize(s), []).append(key)
    return out


# Every dash / hyphen / minus variant we've seen in PDFs — these all collapse
# to nothing during normalisation so "e-mail", "E–Mail", "聯絡E－Mail",
# "E‑Mail" (non-breaking hyphen) all compare equal to "email".
_DASH_CHARS = "-－–—−‐‑‒―﹣"


# Simplified↔Traditional folds that forms mix interchangeably — fold to the
# simplified character at compare time so e.g. "營業地址" (trad) matches
# "营业地址" (simp) and "應稅內含" matches "應稅内含" (note 內 vs 内).
_CJK_FOLD = str.maketrans({
    "內": "内", "臺": "台", "戶": "户", "來": "来", "會": "会",
    "發": "发", "電": "电", "號": "号", "業": "业", "產": "产",
    "廠": "厂", "幣": "币", "稅": "税", "於": "于", "務": "务",
})


def _split_multi_colon_span(
    text: str, bbox: tuple[float, float, float, float],
    char_boxes: Optional[list[tuple[float, float, float, float]]] = None,
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Split 'A:   B:   C:' into per-label sub-spans.

    PyMuPDF sometimes merges a row of whitespace-separated labels into a
    single span. Without this split, the matcher can't see 銀行名稱 and
    銀行代號 as independent labels. We re-emit each non-empty chunk ending
    in a colon as its own candidate, with the bbox proportionally sliced
    from the parent span's x-range.

    有逐字座標（`char_boxes`，跟 `text` 一字對一格）時用實際位置：中文字與
    半形空白寬度差一倍以上，等寬估算會讓後面的標籤框偏掉，值寫上去就壓到
    「人:」「話:」這類標籤尾巴。
    """
    # Find each chunk: some chars, ending in a colon. Minimum 2 chars of
    # actual label text so we don't split on stray lone colons.
    matches = list(re.finditer(r"([^\s:：][^:：]{1,20}[:：])", text))
    if len(matches) < 2:
        return []
    x0, y0, x1, y1 = bbox
    n = len(text)
    if n == 0:
        return []
    char_w = (x1 - x0) / n
    out: list[tuple[str, tuple[float, float, float, float]]] = []
    exact = bool(char_boxes) and len(char_boxes) == n
    for m in matches:
        sub = m.group(0)
        if exact:
            sub_x0 = char_boxes[m.start()][0]
            sub_x1 = char_boxes[m.end() - 1][2]
        else:
            sub_x0 = x0 + m.start() * char_w
            sub_x1 = x0 + m.end() * char_w
        out.append((sub, (sub_x0, y0, sub_x1, y1)))
    return out


#: 中文欄位 → 英文版欄位。標籤結尾註明「(英文)」時改填英文版。
_ENGLISH_OF = {"address": "english_address", "company_name": "english_name",
               "owner": "owner_en"}
_ENGLISH_NOTE = re.compile(r"[\(（]\s*(英文|英|english|eng\.?|en)\s*[\)）]\s*[:：]?\s*$", re.I)


def _says_english(label: str) -> bool:
    """標籤結尾是不是「(英文)」這類註記。

    `_normalize` 會拿掉結尾的括號註記（「負責人(中文)」要對到「負責人」），
    於是「公司地址(英文)」也變成「公司地址」，英文欄被填進中文地址。
    """
    return bool(_ENGLISH_NOTE.search(unicodedata.normalize("NFKC", label or "")))


def _english_keys(keys: list[str]) -> list[str]:
    """換成英文版欄位；本來就是英文欄位的保留；沒有英文版的丟掉（不把中文寫進英文欄）。"""
    out = []
    for k in keys:
        if k in _ENGLISH_OF:
            out.append(_ENGLISH_OF[k])
        elif k in _ENGLISH_OF.values() or k.endswith("_en"):
            out.append(k)
    return out


def _normalize(s: str) -> str:
    """Collapse whitespace, strip leading marker asterisks and trailing
    colons, lowercase ASCII, drop dash variants, fold common
    simplified/traditional character pairs.

    Forms commonly print labels with letter-spacing as actual space chars
    ("聯 絡 人") which should match "聯絡人". Leading "**" / "＊＊" marker
    asterisks (used to flag required fields) are stripped. Trailing colons
    are stripped. Case-folding lets "SWIFT Code" match "Swift Code". All
    dash variants collapse so "聯絡E－Mail" ≡ "email". CJK folding makes
    "內" ≡ "内" so templates from either script variant still match.
    """
    # NFKC folds CJK Compatibility Ideographs (e.g. 立=U+F9F7, 數=U+F969) to
    # their canonical CJK Unified Ideograph codepoints so visually-identical
    # characters compare equal. Some PDFs — especially older ones exported
    # from Traditional Chinese Windows — embed compat codepoints that would
    # otherwise silently fail to match synonyms typed in normal Unicode.
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s.strip())
    s = s.lstrip("*＊")
    # Strip leading numbering like "1." / "2、" / "(3)" used as bullet prefix.
    s = re.sub(r"^[\(（]?\d+[\)）]?[.、．。)]\s*", "", s)
    s = s.rstrip(":：")
    # Strip trailing "(xxx)" / "（xxx）" annotation — "公司名稱(全名)"
    # should match "公司名稱"; "負責人(中文)" match "負責人"; etc.
    # BUT: don't strip when the parens content IS the entire string (e.g.
    # "(中)" / "(English)") — those are stand-alone sub-row labels we need
    # to keep so they can be matched against synonyms like "(中)" → company_name.
    stripped = re.sub(r"[\(（][^\(（\)）]{1,10}[\)）]$", "", s)
    if stripped:
        s = stripped
    for dch in _DASH_CHARS:
        s = s.replace(dch, "")
    # Full-width "／" and half-width "/" collapse — forms mix them
    # ("主要產品/服務" vs "主要產品／服務").
    s = s.replace("／", "/")
    return s.translate(_CJK_FOLD).lower()


@dataclass
class DetectedField:
    page: int               # 0-indexed
    profile_key: str        # canonical key (e.g. "tax_id")
    label_text: str         # the original label text as printed
    label_rect: tuple[float, float, float, float]  # x0, y0, x1, y1 in pt
    font_size: float        # taken from the matched span
    value_anchor: tuple[float, float]  # legacy: (x_pt, y_baseline_pt)
    placement: str = "right"  # "right" (label: value) or "below" (label \n value)
    value_slot: Optional[tuple[float, float, float, float]] = None
    slot_kind: str = "unbounded"  # "inline" | "right-adj" | "below-adj" | "unbounded"
    slot_occupied: bool = False    # True when the target slot already has text


@dataclass
class PageInfo:
    width_pt: float
    height_pt: float


# 用印 / 簽章區的**標籤**，用來排除「要蓋實體章、手寫簽名」的那一段。
_SEAL_MARKERS = ("公司章", "簽章", "蓋章", "印鑑", "用印", "填表人", "授權人",
                 "負責人簽章", "核准", "審核", "主管簽章")
# 一句話裡才會出現的東西 —— 標籤不會有句讀、也不會有條列編號。
_SENTENCE_CHARS = "。，、；？！,;?!"


def _is_seal_marker(text: str) -> bool:
    """這段文字是「用印區的標籤」，還是「只是提到蓋章的說明句」？

    兩者一定要分開：用印區的判定會把**那一行以下的欄位整批排除**，而很多表單
    會在欄位**上方**的填表說明裡提到蓋章，例如
    「(四).必須蓋上　貴寶號及負責人印鑑章，以示同意。」——
    那句話一旦被當成用印區的起點，整張表的欄位就全部被排除，
    畫面上是「一欄都沒抓到」，而且沒有任何錯誤訊息（客戶實際回報過）。

    判準用的是標籤與句子的本質差異，不是位置（有些表單的用印格就在頁面中段）：
    標籤**短**、而且**沒有句讀**。
    """
    t = (text or "").strip().rstrip(":：").strip()
    if not t:
        return False
    if not any(m in t for m in _SEAL_MARKERS):
        return False
    if any(c in t for c in _SENTENCE_CHARS):
        return False        # 有句讀 → 是說明句
    # 「負責人印鑑」5 字、「公司大小章」5 字；說明句一律比這長很多。
    return len(t) <= 12


#: `郵遞區號(        )` —— 標籤後面跟著一對只有空白的括號。
#: 那對括號**就是那個欄位要填的位置**，不是註記也不是已填資料。
_LABEL_WITH_EMPTY_PARENS = re.compile(
    r"^(?P<lab>[^()（）]{2,12}?)[（(](?P<inner>[\s\u3000_\-－—]*)[)）]$"
)


def _paren_value_slot(
    text: str, bbox: tuple[float, float, float, float],
    char_boxes: Optional[list[tuple[float, float, float, float]]] = None,
) -> Optional[tuple[str, tuple[float, float, float, float]]]:
    """把 `標籤(     )` 拆成（標籤文字, 括號內部的位置）。

    這種寫法在表單裡很常見（`郵遞區號(    )`、`(  )分行`），而括號中間那段
    空白就是要人填字的地方。原本這種 span 只會被當成「值格裡的子標籤」跳過，
    於是那個欄位**永遠不會被填**（客戶回報：郵遞區號沒抓到）。

    括號內部的 x 範圍用等寬估算 —— 表單這種場合都是等寬中文字，誤差在
    一兩個字元內，而括號內部本來就留得比實際字數寬。
    """
    m = _LABEL_WITH_EMPTY_PARENS.match((text or "").strip())
    if not m:
        return None
    lab = m.group("lab").strip()
    if not lab:
        return None
    x0, y0, x1, y1 = bbox
    stripped = text.strip()
    n = len(stripped)
    if n <= 0 or x1 <= x0:
        return None
    inner_start = m.start("inner")
    inner_end = m.end("inner")
    if inner_end - inner_start < 2:      # 括號中間沒空間，不是填寫位置
        return None
    if char_boxes and len(char_boxes) == n:
        # **有逐字座標就用逐字座標。** 等寬估算在這裡是錯的：`郵遞區號(   )`
        # 前面是全形中文、中間是半形空白，估出來的括號內部會偏左一大截，
        # 值就直接寫在「郵遞區號(」上面（實測真的疊上去了）。
        sx0 = char_boxes[inner_start][0]
        sx1 = char_boxes[inner_end - 1][2]
    else:
        char_w = (x1 - x0) / n
        sx0 = x0 + char_w * inner_start
        sx1 = x0 + char_w * inner_end
    if sx1 - sx0 < 6:
        return None
    return lab, (sx0, y0, sx1, y1)


def detect_fields(
    pdf_path: Path,
    label_map: Optional[dict[str, list[str]]] = None,
    label_to_key: Optional[dict[str, list[str]]] = None,
    gap_pt: float = 4.0,
    min_right_room_pt: float = 40.0,
) -> tuple[list[DetectedField], list[PageInfo]]:
    """Scan ``pdf_path`` and return all label spans matching ``label_map``.

    Either ``label_map`` (canonical -> synonyms) or ``label_to_key`` (already
    normalized synonym -> canonical) may be supplied; if both are None, the
    module-level ``LABEL_MAP`` is used.

    Each label may match more than once — e.g. "電話" appearing on multiple
    pages — and every occurrence is reported separately. Callers decide how
    to handle duplicates (typically: fill them all with the same value).

    Placement strategy is auto-detected per label: if the next non-trivial
    text span on the same row sits less than ``min_right_room_pt`` to the
    right of the label, the value is positioned BELOW the label instead of
    next to it. This handles forms where labels are arranged as a header row
    over their value cells.
    """
    if label_to_key is None:
        label_to_key = _build_synonym_index(label_map or _active_label_map())

    detected: list[DetectedField] = []
    pages: list[PageInfo] = []

    # First pass: collect all text spans per page so we can do layout reasoning
    # in the second pass without re-traversing the dict.
    # Span tuple: (text, bbox, size, color_int)
    page_spans: list[list[tuple[str, tuple[float, float, float, float], float, int]]] = []
    page_lines: list[tuple[list[pdf_layout.HLine], list[pdf_layout.VLine]]] = []
    # Every printed checkbox option text on each page — slot-occupancy must
    # ignore these (they're sub-labels next to □ glyphs, not pre-filled data).
    page_checkbox_texts: list[set[str]] = []
    # 每頁：span 的 bbox（取整）→ 該 span 的逐字座標
    page_char_boxes: list[dict] = []
    # 每頁：以空白切開的字詞框（同一列裡「標籤：　　　發票章：」常是同一個 span）
    page_words: list[list[tuple[float, float, float, float]]] = []
    # pdfplumber-based cells (primary); fall back to line-based when empty.
    page_cells_pp = pdf_layout.extract_cells_pdfplumber(pdf_path)

    with fitz.open(str(pdf_path)) as doc:
        for pno in range(doc.page_count):
            page = doc[pno]
            pages.append(PageInfo(width_pt=page.rect.width, height_pt=page.rect.height))
            spans: list[tuple[str, tuple[float, float, float, float], float]] = []
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        t = span.get("text", "")
                        if t.strip():
                            spans.append(
                                (t, tuple(span.get("bbox") or (0, 0, 0, 0)),
                                 span.get("size", 10.0), int(span.get("color", 0)))
                            )
            page_spans.append(spans)
            # 逐字座標（`rawdict`）—— `_paren_value_slot` 要拿它算括號內部的
            # 位置。等寬估算在中文與半形空白混排時會偏掉一大截。
            _chars: dict[tuple[int, int, int, int], list[tuple[float, float, float, float]]] = {}
            for _blk in page.get_text("rawdict").get("blocks", []):
                if _blk.get("type") != 0:
                    continue
                for _ln in _blk.get("lines", []):
                    for _sp in _ln.get("spans", []):
                        _bb = tuple(round(v) for v in (_sp.get("bbox") or (0, 0, 0, 0)))
                        _chars[_bb] = [tuple(c.get("bbox")) for c in _sp.get("chars", [])]
            page_char_boxes.append(_chars)
            page_words.append([tuple(w[:4]) for w in page.get_text("words")])
            page_lines.append(pdf_layout.extract_lines(page))
            # Extract checkbox option texts for occupancy filtering.
            from . import pdf_checkbox
            cb_texts = {
                _normalize(o.text) for o in pdf_checkbox._page_checkboxes(page, pno)
            }
            page_checkbox_texts.append(cb_texts)

        for pno in range(doc.page_count):
            spans = page_spans[pno]
            h_lines, v_lines = page_lines[pno]
            text_dict = doc[pno].get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    line_text = "".join(s.get("text", "") for s in line.get("spans", []))
                    candidates: list[tuple[str, tuple[float, float, float, float], float]] = []
                    if line_text.strip():
                        bbox = tuple(line.get("bbox") or (0, 0, 0, 0))
                        size = max((s.get("size", 0) for s in line.get("spans", [])), default=10.0)
                        candidates.append((line_text, bbox, size))
                    for span in line.get("spans", []):
                        t = span.get("text", "")
                        if t.strip() and t != line_text:
                            candidates.append((t, tuple(span.get("bbox") or (0, 0, 0, 0)), span.get("size", 10.0)))
                        # Split merged multi-label spans like "銀行名稱：    銀行代號："
                        # — PyMuPDF sometimes emits these as a single span when
                        # the form designer used whitespace instead of column
                        # separators, hiding each sub-label from the matcher.
                        _sbb = tuple(span.get("bbox") or (0, 0, 0, 0))
                        for sub_t, sub_b in _split_multi_colon_span(
                            t, _sbb, page_char_boxes[pno].get(tuple(round(v) for v in _sbb))
                        ):
                            if sub_t != t:
                                candidates.append((sub_t, sub_b, span.get("size", 10.0)))

                    cells_pp = page_cells_pp[pno] if pno < len(page_cells_pp) else []
                    for text, bbox, size, *_ in candidates:
                        norm = _normalize(text)
                        if not norm:
                            continue
                        # `郵遞區號(     )` —— 括號中間就是要填的位置，
                        # 不必再往右找格子（往右是地址的位置）。
                        _paren = _paren_value_slot(
                            text, bbox,
                            page_char_boxes[pno].get(tuple(round(v) for v in bbox)))
                        if _paren is not None:
                            _lab, _pslot = _paren
                            _pkeys = label_to_key.get(_normalize(_lab))
                            if _pkeys:
                                for _k in _pkeys:
                                    detected.append(DetectedField(
                                        page=pno, profile_key=_k, label_text=_lab,
                                        label_rect=bbox, font_size=size,
                                        value_anchor=(_pslot[0], bbox[3] - 0.2 * size),
                                        placement="right",
                                        value_slot=_pslot, slot_kind="in-paren",
                                    ))
                                continue
                        keys = label_to_key.get(norm)
                        if not keys and "/" in norm:
                            # Compound label like "公司名稱/個人姓名" or
                            # "統編/身分證字號" — try each "/"-separated
                            # part. The first part wins (typically the
                            # primary/preferred field for a company), so
                            # the value goes there.
                            for part in norm.split("/"):
                                part = part.strip()
                                if not part:
                                    continue
                                k = label_to_key.get(part)
                                if k:
                                    keys = k
                                    break
                        if keys and _says_english(text):
                            keys = _english_keys(keys)
                        if not keys:
                            continue
                        x0, y0, x1, y1 = bbox
                        baseline = y1 - 0.2 * size
                        cell = None
                        slot = None
                        slot_kind = "unbounded"
                        # **後置標籤要最先判斷。** 下面那套（相鄰格 / inline）
                        # 一律把值放到標籤右邊，`＿＿＿分行` 這種版面就會落在
                        # 標籤後面那一小段（實測只有 38pt，「崇德分行」被擠成
                        # 「崇德分」再折行壓到下一列）。
                        # 走到這裡就代表這是單位詞，值一定在左邊。
                        if pdf_layout.is_suffix_label(text):
                            _cell = (pdf_layout.find_cell_containing(
                                (x0, y0, x1, y1), cells_pp) if cells_pp else None)
                            _left = pdf_layout._slot_left_of(
                                (x0, y0, x1, y1), _cell, v_lines, 3.0,
                                [s[1] for s in page_spans[pno]],
                            )
                            if _left is not None:
                                slot, slot_kind = _left, "left-of-suffix"
                        if slot is None and cells_pp:
                            cell_pp = pdf_layout.find_cell_containing(
                                (x0, y0, x1, y1), cells_pp
                            )
                            if cell_pp is not None:
                                # Prefer pdfplumber cell chain.
                                cell = cell_pp
                                adj = pdf_layout.find_cell_right_of(cell_pp, cells_pp)
                                if adj is not None:
                                    slot = (adj[0] + 3, adj[1], adj[2] - 3, adj[3])
                                    slot_kind = "right-adj"
                                else:
                                    # Prefer inline when the label sits in a
                                    # wide full-row cell with plenty of room
                                    # to its right — "公司經營產品:" with an
                                    # empty half-cell to its right shouldn't
                                    # push the value into the next row.
                                    label_w = max(1.0, x1 - x0)
                                    room_after = cell_pp[2] - x1
                                    if room_after > 100 and room_after >= label_w * 3:
                                        slot = (x1 + 3, cell_pp[1], cell_pp[2] - 3, cell_pp[3])
                                        slot_kind = "inline"
                                    else:
                                        below = pdf_layout.find_cell_below_of(cell_pp, cells_pp)
                                        if below is not None:
                                            slot = (below[0] + 3, below[1], below[2] - 3, below[3])
                                            slot_kind = "below-adj"
                                        elif room_after > 30:
                                            slot = (x1 + 3, cell_pp[1], cell_pp[2] - 3, cell_pp[3])
                                            slot_kind = "inline"
                        if slot is None:
                            cell = pdf_layout.find_enclosing_cell(
                                (x0, y0, x1, y1), h_lines, v_lines
                            )
                            slot, slot_kind = pdf_layout.compute_value_slot(
                                (x0, y0, x1, y1), cell, h_lines, v_lines,
                                label_text=text,
                                row_texts=[s[1] for s in page_spans[pno]],
                            )
                        # **值不可以落在直書標籤欄**。「匯 款 資 料」那種欄位
                        # 很窄（實測約 64pt），值塞進去只會疊在別的標籤上 ——
                        # 使用者看到的是兩串字疊在一起，完全讀不出來。
                        # 寧可不填也不要疊：留白至少看得出「這裡沒填」。
                        #
                        # **這裡不能直接把 slot 設成 None** —— 下面第一件事
                        # 就是 `anchor = (slot[0], baseline)`，設成 None 會
                        # 當場 `TypeError: 'NoneType' object is not
                        # subscriptable`，而且是在 `detect_fields` 裡面，
                        # 整份表單一欄都偵測不出來（使用者拿到 500）。
                        # v1.14.31 對抗式驗證用一個很常見的版型重現：一列格子
                        # 裡「分行」被框成 60~70pt 的小格，左界取到直線之後
                        # 寬度落在 24~80 之間就會踩到。
                        #
                        # 做法跟底下那個「值蓋到別的標籤」的檢查一致：
                        # **anchor 先算好再把 slot 拿掉**（`DetectedField` 本來
                        # 就允許 `value_slot=None`）。
                        _rejected_narrow = bool(
                            slot and slot_kind != "below-adj"
                            and slot[2] - slot[0] < pdf_layout.MIN_VALUE_SLOT_WIDTH
                            and slot[0] < x0)
                        # Guard against slots that accidentally extend above
                        # the label's own row (pdfplumber can merge visually
                        # adjacent cells) — clamp the slot top to the label's
                        # top so text doesn't spill into the row above.
                        if slot and slot_kind != "below-adj" and slot[1] < y0 - 2:
                            slot = (slot[0], y0 - 2, slot[2], slot[3])
                            # For forms that use plain underlines instead of
                            # table cells (common in older / government
                            # layouts), look for a horizontal rule just below
                            # the label and place the value above it.
                            if slot_kind == "unbounded":
                                ul = pdf_layout.find_underline_below(
                                    (x0, y0, x1, y1), h_lines
                                )
                                if ul is not None:
                                    ul_y, ul_x0, ul_x1 = ul
                                    slot = (
                                        max(ul_x0, x1 + 3),
                                        max(y0 - size * 0.2, ul_y - size * 1.4),
                                        ul_x1 - 1,
                                        ul_y - 1,
                                    )
                                    slot_kind = "underline"
                        anchor = (slot[0], baseline) if slot else None
                        if _rejected_narrow:
                            slot, slot_kind = None, None
                        placement_legacy = "below" if slot_kind == "below-adj" else "right"
                        for key in keys:
                            detected.append(
                                DetectedField(
                                    page=pno,
                                    profile_key=key,
                                    label_text=text,
                                    label_rect=(x0, y0, x1, y1),
                                    font_size=size,
                                    value_anchor=anchor,
                                    placement=placement_legacy,
                                    value_slot=slot,
                                    slot_kind=slot_kind,
                                )
                            )

    # **沒有格線的表：值只能用標籤自己那一列。**
    #
    # 整張表只有一個外框、裡面一條橫線都沒有時，標籤所在的「格子」就是整個
    # 外框，inline 的值格會從這一列一路延伸到下一條畫出來的橫線（實測跨了
    # 二十幾列）。填寫時值放在格子垂直置中的位置，就掉到下面幾列，壓在別的
    # 標籤上；那些標籤沒對應到資料欄位，下面「不可以蓋到別的標籤」的檢查也看
    # 不到它們。
    #
    # 判準：值格的垂直範圍裡，標籤那一列以外還有別的文字（落在整個格子的寬度
    # 內，包含標籤欄），就代表這一格其實是好幾列。這時上下界收到相鄰列的文字
    # 為止，右界收到同一列下一段文字或穿過這一列的直線為止（例如章框）。
    # right-adj：右邊那一格跨了好幾列標籤（章框切出來的格子）就改用這一列；
    # unbounded：固定寬度一樣停在同一列下一段字前面。below-adj、子標籤、括號
    # 另有專門的規則，不動。
    def _row_right(d, sx0: float, right: float) -> float:
        """同一列（標籤那一條帶）往右遇到的第一段字或直線。"""
        lx0, ly0, lx1, ly1 = d.label_rect
        cy = (ly0 + ly1) / 2
        for bx0, by0, bx1, by1 in page_words[d.page]:
            if bx0 <= lx1 + 1:
                continue
            if by0 - 1 <= cy <= by1 + 1 and bx0 - 3 < right:
                right = bx0 - 3               # 同一列下一段文字
        for vx, vy0, vy1 in page_lines[d.page][1]:
            if vy0 - 1 <= cy <= vy1 + 1 and sx0 + 30 < vx and vx - 3 < right:
                right = vx - 3                # 穿過這一列的直線（章框）
        return right

    def _other_rows(d, x0: float, x1: float, y0: float, y1: float) -> tuple[float, float, bool]:
        """[y0, y1] 範圍、x 在 [x0, x1] 內，標籤那一列以外還有沒有別的字；回（上界, 下界, 有沒有）。"""
        lx0, ly0, lx1, ly1 = d.label_rect
        # 同一個欄位的另一行標籤（「(中)」「(Chinese)」分兩行印在同一格）不算別列
        same = {g.label_rect for g in detected if g.page == d.page and g.profile_key == d.profile_key}
        top, bottom, hit = y0, y1, False
        for _t, (bx0, by0, bx1, by1), _sz, _c in page_spans[d.page]:
            if not (_t or "").strip() or (bx0, by0, bx1, by1) in same:
                continue
            if any(abs(bx0 - r[0]) < 1 and abs(by0 - r[1]) < 1 for r in same):
                continue
            if bx1 <= x0 or bx0 >= x1:
                continue
            if by0 >= ly1 - 1 and by0 < bottom:
                bottom, hit = by0, True       # 下面一列
            elif by1 <= ly0 + 1 and by1 > top:
                top, hit = by1, True          # 上面一列
        return top, bottom, hit

    for d in detected:
        if d.value_slot is None or d.slot_kind not in ("inline", "right-adj", "unbounded"):
            continue
        sx0, sy0, sx1, sy1 = d.value_slot
        lx0, ly0, lx1, ly1 = d.label_rect
        lh = ly1 - ly0
        if d.slot_kind == "unbounded":
            # 偵測端給的固定寬度：一樣不可以跨過同一列下一段字
            # 縮完還放得下才縮；放不下就維持原樣（逐格分寫的數字框等版型靠這個寬度）
            right = _row_right(d, sx0, sx1)
            if right < sx1 and right - sx0 >= pdf_layout.MIN_VALUE_SLOT_WIDTH:
                d.value_slot = (sx0, sy0, right, sy1)
            continue
        if sy1 - sy0 <= lh * 1.8:
            continue
        if d.slot_kind == "inline" and sx1 - sx0 < pdf_layout.MIN_VALUE_SLOT_WIDTH:
            # 本來就窄的格子不動：收矮之後會躲過「蓋到別的標籤」那道檢查，值反而被塞進窄格折行
            continue
        if d.slot_kind == "right-adj":
            # 右邊那一格跨了好幾列標籤（例如章框切出來的格子）：那不是這一列的值格。
            # 只看標籤欄（右邊那一格左緣以左）有沒有別列的字。
            top, bottom, hit = _other_rows(d, lx0, sx0 - 1, sy0, sy1)
            if not hit:
                continue
            new_x0, new_x1 = lx1 + 3, sx0 - 6
            if new_x1 - new_x0 < pdf_layout.MIN_VALUE_SLOT_WIDTH:
                continue
            top = max(top, ly0 - 2)
            bottom = min(bottom - 1, ly1 + lh)
            d.value_slot = (new_x0, top, _row_right(d, new_x0, new_x1), bottom)
            d.slot_kind = "inline"
            if d.value_anchor is not None:
                d.value_anchor = (new_x0, d.value_anchor[1])
            continue
        top, bottom, hit = _other_rows(d, lx0, sx1, sy0, sy1)
        if not hit:
            continue                          # 格子裡只有這一列：照舊
        top = max(top, ly0 - 2)
        bottom = min(bottom - 1, ly1 + lh)
        right = _row_right(d, sx0, sx1)
        if right - sx0 < pdf_layout.MIN_VALUE_SLOT_WIDTH:
            right = sx1                       # 收窄後放不下：只收上下
        d.value_slot = (sx0, top, right, bottom)

    # **標籤後面單獨印的冒號**：「公司名稱」與「：」常是兩段字，值格從標籤右緣
    # 起算就會壓在冒號上。
    for d in detected:
        if d.value_slot is None or d.slot_kind not in ("inline", "unbounded"):
            continue
        sx0, sy0, sx1, sy1 = d.value_slot
        lx0, ly0, lx1, ly1 = d.label_rect
        for _t, (bx0, by0, bx1, by1), _sz, _c in page_spans[d.page]:
            if (_t or "").strip(" 　:：") or not (_t or "").strip():
                continue
            if lx1 - 1 <= bx0 <= lx1 + 2 and min(ly1, by1) - max(ly0, by0) > 1 and bx1 + 3 > sx0:
                if sx1 - (bx1 + 3) >= 24:
                    sx0 = bx1 + 3
                    d.value_slot = (sx0, sy0, sx1, sy1)
                    if d.value_anchor is not None:
                        d.value_anchor = (sx0, d.value_anchor[1])

    # **值的起點要推到「印在值格裡的子標籤」後面。**
    #
    # `郵遞區號(    )` 這種子標籤是真的印在紙上的字。值從格子左緣開始寫就會壓
    # 在它上面（實測重疊 95pt）—— **疊字比沒填更糟**。
    #
    # 這一段必須排在下面那個「值不可以蓋到別的標籤」之前：那對括號本身也是一個
    # 欄位（見 `_paren_value_slot`），先推開才不會讓整欄被判成「蓋到別的標籤」
    # 而丟掉位置。
    for d in detected:
        if d.value_slot is None or d.slot_kind == "in-paren":
            continue
        sx0, sy0, sx1, sy1 = d.value_slot
        for text, (bx0, by0, bx1, by1), _size, _c in page_spans[d.page]:
            t_s = (text or "").strip()
            if not t_s or _paren_value_slot(t_s, (bx0, by0, bx1, by1)) is None:
                continue
            if min(sy1, by1) - max(sy0, by0) <= 1:
                continue
            if bx0 > sx0 + (sx1 - sx0) * 0.5 or bx1 + 4 >= sx1 - 10:
                continue
            if bx1 + 4 > sx0:
                sx0 = bx1 + 4
                d.value_slot = (sx0, sy0, sx1, sy1)
                if d.value_anchor is not None:
                    d.value_anchor = (sx0, d.value_anchor[1])

    # **值的位置不可以壓在別的標籤上。**
    #
    # 表格最左邊常有直書的分區標籤（「匯 款 資 料」），很窄。值一旦被算到
    # 那裡，印出來就是兩串字疊在一起，完全讀不出來 —— 而且使用者第一眼
    # 看到的是「工具把字寫爛了」，不是「這欄沒填」。
    #
    # 寬度門檻擋不住這種情況（那一欄本來就窄，而郵遞區號、分機這種正常欄位
    # 也窄），真正的不變量是**值不該蓋到任何標籤**。蓋到就放棄這個位置 ——
    # 留白至少看得出「這裡沒填」，比疊字好。
    _label_boxes = [(d.page, d.label_rect) for d in detected]
    for d in detected:
        if d.value_slot is None:
            continue
        sx0, sy0, sx1, sy1 = d.value_slot
        for pg, (bx0, by0, bx1, by1) in _label_boxes:
            if pg != d.page or (bx0, by0, bx1, by1) == d.label_rect:
                continue
            ox = min(sx1, bx1) - max(sx0, bx0)
            oy = min(sy1, by1) - max(sy0, by0)
            if ox <= 1 or oy <= 1:
                continue
            # 只有「蓋掉標籤一大半」才算真的疊到 —— 相鄰格邊緣擦到不算
            if ox * oy > (bx1 - bx0) * (by1 - by0) * 0.5:
                d.value_slot = None
                d.slot_kind = None
                break

    # Occupancy check: if the target slot already contains non-trivial text
    # (i.e. the form is pre-filled or has example text like "月結30 天"),
    # mark it so the filler can skip writing and avoid double-overlay.
    for d in detected:
        if d.value_slot is None:
            continue
        sx0, sy0, sx1, sy1 = d.value_slot
        lx0, ly0, lx1, ly1 = d.label_rect
        cb_texts = page_checkbox_texts[d.page] if d.page < len(page_checkbox_texts) else set()
        for text, (bx0, by0, bx1, by1), _size, color in page_spans[d.page]:
            if not text.strip():
                continue
            # Ignore the label itself
            if abs(bx0 - lx0) < 0.5 and abs(by0 - ly0) < 0.5:
                continue
            # Span must lie substantially inside the slot
            ov_x = max(0.0, min(sx1, bx1) - max(sx0, bx0))
            ov_y = max(0.0, min(sy1, by1) - max(sy0, by0))
            span_w = max(1.0, bx1 - bx0)
            span_h = max(1.0, by1 - by0)
            if ov_x / span_w < 0.5 or ov_y / span_h < 0.5:
                continue
            t_stripped = text.strip()
            # Ignore text that contains checkbox glyphs — it's a row of form
            # options (e.g. "□支存 □活存 _____"), not pre-filled data.
            if any(c in "□☐☑✓" for c in t_stripped):
                continue
            # Placeholder / hint text — form designers often put red
            # prompts like "★請在此說明…" or "請填寫…" inside the value
            # area. Don't treat those as pre-filled data.
            if t_stripped.startswith("★") or t_stripped.startswith("☆"):
                continue
            if t_stripped.startswith("請") or t_stripped.startswith("此為") or t_stripped.startswith("本"):
                continue
            # Coloured text (red/blue/grey) is almost always a hint/annotation
            # printed by the form designer, not pre-filled data.
            if color > 0 and color != 0x000000:
                continue
            # Text that is itself a printed checkbox option label ("同登記地址",
            # "支存", etc.). Those sit next to a □ glyph — not pre-filled data.
            if _normalize(t_stripped) in cb_texts:
                continue
            # Text that matches ANOTHER field's label — on multi-column
            # layouts the adjacent cell picked for a label can extend into
            # the next column's label (e.g. 分行's slot catching "銀行帳號"
            # label text). That's another form label, not pre-filled data.
            if _normalize(t_stripped) in label_to_key:
                continue
            # Any text wrapped entirely in parentheses is an *annotation*
            # labelling the row (e.g. "(中)", "(Chinese)", "(R.O.C.)",
            # "(Only for T/T)") — never a pre-filled value.
            if (
                (t_stripped.startswith("(") and t_stripped.endswith(")")) or
                (t_stripped.startswith("（") and t_stripped.endswith("）"))
            ):
                continue
            # Text ending with a colon — "郵遞區號:", "Phone:", "電話：" —
            # is a sub-field label inside a merged cell, not pre-filled data.
            if t_stripped.endswith(":") or t_stripped.endswith("："):
                continue
            # 「標籤詞＋空括號」也是子標籤，例如 `郵遞區號(        )`：
            # 那對括號是要人填東西的位置，不是已經填好的資料。
            # 判成「已填」的話整欄會被跳過 —— 客戶那份表單的通訊地址與發票
            # 地址就是這樣被略過的，而且畫面上只顯示「未填」，看不出原因。
            #
            # **但也不能就這樣寫上去** —— 那個子標籤是真的印在紙上的字，
            # 值從格子左緣開始寫就會壓在它上面（實測重疊 95pt）。
            # 疊字比沒填更糟：使用者看到的是兩串字疊在一起。
            # 正確做法是把值的起點推到子標籤後面。
            _bare = re.sub(r"[（(][\s_－—\-]*[)）]", "", t_stripped).strip()
            if _bare != t_stripped and _normalize(_bare) in label_to_key:
                continue
            # Strip common noise and drop anything that's still effectively
            # empty or just a single stray character (form artifacts).
            import re as _re
            cleaned = _re.sub(r"[\s()（）:：、,.．·]+", "", t_stripped)
            if len(cleaned) <= 3:
                continue
            d.slot_occupied = True
            break

    # Second pass: if a field's "right-adj" slot already contains another
    # matched label (typical in grid rows like "統一編號 | 負責人" where each
    # column is its own cell with inline label+value), switch that field to
    # inline placement inside its own cell so the value doesn't spill into
    # the neighbouring field's territory.
    #
    # We also look at raw spans ending in a colon ("公司中文地址:",
    # "公司英文地址:") even when their labels aren't in LABEL_MAP — those
    # are neighbour-column labels too, and the value for *this* field must
    # not overflow into the adjacent cell they occupy.
    # Snapshot original slot state so flipping one field doesn't hide the
    # sibling-conflict from its row-mate processed later in the loop.
    _orig_slot = {id(d): (d.slot_kind, d.value_slot) for d in detected}
    for d in detected:
        if d.slot_kind not in ("right-adj", "below-adj") or d.value_slot is None:
            continue
        sx0, sy0, sx1, sy1 = d.value_slot
        conflicting = False
        for o in detected:
            if o is d or o.page != d.page:
                continue
            ox0, oy0, ox1, oy1 = o.label_rect
            if sx0 - 2 <= ox0 and ox1 <= sx1 + 2 and sy0 - 2 <= oy0 and oy1 <= sy1 + 2:
                conflicting = True
                break
        if not conflicting:
            # Any other colon-ending label span inside the slot also counts.
            lx0, ly0, lx1, ly1 = d.label_rect
            for text, (bx0, by0, bx1, by1), _size, _color in page_spans[d.page]:
                t = text.strip()
                if not (t.endswith(":") or t.endswith("：")):
                    continue
                # Skip the label itself.
                if abs(bx0 - lx0) < 0.5 and abs(by0 - ly0) < 0.5:
                    continue
                # Skip parenthesised in-cell annotations like "(分機:" /
                # "(only for T/T):" — those are decorative tags inside a
                # value cell, NOT a sibling column label, so we shouldn't
                # collapse the slot to the label cell. Treat them as
                # neutral; the universal x-clamp will trim slot.x1 to fit.
                if t.startswith("(") or t.startswith("（"):
                    continue
                # Tiny colon-ending fragments like "機:" / "分:" are
                # mid-cell decoration (e.g. the second half of "(分機:"
                # split across spans), not a real column label.
                if len(t) <= 3:
                    continue
                if sx0 - 2 <= bx0 and bx1 <= sx1 + 2 and sy0 - 2 <= by0 and by1 <= sy1 + 2:
                    conflicting = True
                    break
        if not conflicting:
            # Sibling same-row label: two labels share the same row (same y)
            # and claim overlapping slots (e.g. "分行名稱：" + "分行代號：" split
            # from a single multi-colon span — both get the same below-adj
            # slot pointing down the merged cell). Both must flip to inline
            # within their own cell, else only one survives dedup.
            lx0, ly0, lx1, ly1 = d.label_rect
            for o in detected:
                if o is d or o.page != d.page:
                    continue
                # Skip same-key duplicates — these are line_text vs span
                # emissions of the SAME label, not true row siblings.
                if o.profile_key == d.profile_key:
                    continue
                # Skip shared-label multi-key emissions (e.g. 發票聯數 →
                # both tax_type and invoice_type). Same label_rect =
                # same printed label, not a neighbouring sibling column.
                if o.label_rect == d.label_rect:
                    continue
                orig = _orig_slot.get(id(o))
                if not orig or orig[1] is None:
                    continue
                ox0, oy0, ox1, oy1 = o.label_rect
                # Same row?
                row_h = max(1.0, ly1 - ly0)
                y_overlap = max(0.0, min(ly1, oy1) - max(ly0, oy0))
                if y_overlap < row_h * 0.5:
                    continue
                # Overlapping slot (compared against ORIGINAL slot state)?
                osx0, osy0, osx1, osy1 = orig[1]
                ix = max(0.0, min(sx1, osx1) - max(sx0, osx0))
                iy = max(0.0, min(sy1, osy1) - max(sy0, osy0))
                if ix * iy > 0:
                    conflicting = True
                    break
        if not conflicting:
            continue
        # Prefer pdfplumber's cell container (respects horizontal separators
        # that extract_lines may miss); fall back to line-based if absent.
        cell = None
        cells_pp = page_cells_pp[d.page] if d.page < len(page_cells_pp) else []
        if cells_pp:
            cell = pdf_layout.find_cell_containing(d.label_rect, cells_pp)
        if cell is None:
            h_lines, v_lines = page_lines[d.page]
            cell = pdf_layout.find_enclosing_cell(d.label_rect, h_lines, v_lines)
        if cell is None:
            continue
        cx0, cy0, cx1, cy1 = cell
        lx0, ly0, lx1, ly1 = d.label_rect
        inline_room = cx1 - lx1
        # Header-above-value table layout: if a value cell sits directly
        # below this label cell AND is noticeably wider than the remaining
        # inline room, prefer below-adj. Catches Swift Code | 銀行名稱 |
        # 分行 | 銀行帳號 rows where each label has a dedicated tall value
        # cell beneath it.
        cells_pp2 = page_cells_pp[d.page] if d.page < len(page_cells_pp) else []
        below = pdf_layout.find_cell_below_of(cell, cells_pp2) if cells_pp2 else None
        if below is not None:
            below_w = below[2] - below[0]
            below_h = below[3] - below[1]
            cell_w = cx1 - cx0
            cell_h = cy1 - cy0
            # Conditions for a header-above-value table column:
            # 1. Below cell is taller than the label cell (value row, not
            #    just the next label row).
            # 2. Below cell has the SAME x-range as the label cell — a
            #    dedicated per-column value cell. A below cell that is much
            #    wider (or shifted) means a merged row, not a value column.
            width_ratio = (
                min(below_w, cell_w) / max(below_w, cell_w)
                if max(below_w, cell_w) > 0 else 0.0
            )
            x_aligned = abs(below[0] - cx0) < 2 and abs(below[2] - cx1) < 2
            # Guard: never redirect into a cell that is itself the label
            # cell of ANOTHER detected field — that's another column header,
            # not our value cell (e.g. 發票聯數 label's "below" would be the
            # Swift Code header cell in the table beneath).
            below_contains_other_label = False
            for o in detected:
                if o is d or o.page != d.page:
                    continue
                ox0, oy0, ox1, oy1 = o.label_rect
                if (below[0] - 2 <= ox0 and ox1 <= below[2] + 2
                        and below[1] - 2 <= oy0 and oy1 <= below[3] + 2):
                    below_contains_other_label = True
                    break
            if (not below_contains_other_label and below_h >= cell_h * 1.2
                    and width_ratio >= 0.9 and x_aligned
                    and below_w > max(inline_room * 2, 60)):
                d.value_slot = (below[0] + 3, below[1], below[2] - 3, below[3])
                d.slot_kind = "below-adj"
                continue
        if inline_room < 30:
            # Fallback: no usable below cell but no inline room either.
            if below is not None:
                d.value_slot = (below[0] + 3, below[1], below[2] - 3, below[3])
                d.slot_kind = "below-adj"
            continue
        # Clamp the right edge of the slot so it doesn't spill into the next
        # sibling label's cell on the same row. Without this clamp the value
        # of 銀行名稱 would overflow into 銀行代號's territory, tripping the
        # UI's overlap warning.
        right_bound = cx1 - 3
        for o in detected:
            if o is d or o.page != d.page:
                continue
            ox0, oy0, ox1, oy1 = o.label_rect
            # Same row only.
            row_h = max(1.0, ly1 - ly0)
            y_overlap = max(0.0, min(ly1, oy1) - max(ly0, oy0))
            if y_overlap < row_h * 0.5:
                continue
            # Sibling must be to our right.
            if ox0 <= lx1 + 2:
                continue
            right_bound = min(right_bound, ox0 - 3)
        d.value_slot = (lx1 + 3, cy0, max(lx1 + 10, right_bound), cy1)
        d.slot_kind = "inline"

    # De-duplicate: bilingual forms print the Chinese and English of the
    # same label on adjacent lines. Because each line resolves through
    # find_enclosing_cell independently, the two DetectedFields can end up
    # with *different* value slots (e.g. one short inline slot picked from
    # the label-only cell, one wide adj-right slot) — rendering both would
    # stack the long value in a 20pt-wide strip as well as in the real
    # value cell.  Group by (page, profile_key, row band) and keep only
    # the entry with the widest slot per group.
    # Dedupe by slot overlap instead of coarse centre-bucket: two slots that
    # overlap ≥ 60% of the smaller area count as the same target. Bucket
    # rounding missed cases where slots were a couple of pts apart and fell
    # into neighbouring buckets (e.g. tax_id produced twice at nearly-same
    # coords but buckets 600 vs 610).
    def _overlaps(a, b) -> bool:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
        iy = max(0.0, min(ay1, by1) - max(ay0, by0))
        inter = ix * iy
        if inter <= 0:
            return False
        area_a = max(1.0, (ax1 - ax0) * (ay1 - ay0))
        area_b = max(1.0, (bx1 - bx0) * (by1 - by0))
        return inter / min(area_a, area_b) >= 0.6

    # Drop umbrella labels — section headings like "受款銀行" that repeat a
    # profile_key already covered by more specific sub-labels ("銀行名稱",
    # "銀行代號", "分行名稱", "分行代號"). The umbrella would otherwise
    # overwrite a wide merged-cell slot with a single value, clobbering the
    # sub-labels' own targeted slots.
    _UMBRELLA_LABELS = {
        _normalize(s) for s in (
            "受款銀行", "收款銀行", "匯款銀行", "銀行資訊", "銀行資料",
            "受款帳戶", "收款帳戶", "Beneficiary Bank",
        )
    }
    umbrella_ids: set[int] = set()
    for d in detected:
        if _normalize(d.label_text) not in _UMBRELLA_LABELS:
            continue
        # Is there a more specific sibling for this profile_key on the page?
        for o in detected:
            if o is d or o.page != d.page or o.profile_key != d.profile_key:
                continue
            if _normalize(o.label_text) in _UMBRELLA_LABELS:
                continue
            umbrella_ids.add(id(d))
            break
    detected = [d for d in detected if id(d) not in umbrella_ids]

    # Same-label duplicates: the candidate list includes both the line and
    # each span, so a single-span label is emitted twice. Collapse pairs
    # with the same profile_key whose label_rects are essentially the same
    # (≥70% overlap). Keep the wider slot — the tight inline one is usually
    # the accidental second emission landing on the label's own cell.
    def _label_overlap(a, b) -> float:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
        iy = max(0.0, min(ay1, by1) - max(ay0, by0))
        inter = ix * iy
        if inter <= 0:
            return 0.0
        area_a = max(1.0, (ax1 - ax0) * (ay1 - ay0))
        area_b = max(1.0, (bx1 - bx0) * (by1 - by0))
        return inter / min(area_a, area_b)

    drop_ids: set[int] = set()
    for i, d in enumerate(detected):
        if id(d) in drop_ids or d.value_slot is None:
            continue
        for o in detected[i + 1:]:
            if id(o) in drop_ids or o.value_slot is None:
                continue
            if o.page != d.page or o.profile_key != d.profile_key:
                continue
            if _label_overlap(d.label_rect, o.label_rect) < 0.7:
                continue
            d_w = d.value_slot[2] - d.value_slot[0]
            o_w = o.value_slot[2] - o.value_slot[0]
            if d_w >= o_w:
                drop_ids.add(id(o))
            else:
                drop_ids.add(id(d))
                break
    detected = [d for d in detected if id(d) not in drop_ids]

    no_slot: list[DetectedField] = [d for d in detected if d.value_slot is None]
    with_slot: list[DetectedField] = [d for d in detected if d.value_slot is not None]
    # Group per (page, profile_key); within a group, merge overlapping slots.
    from collections import defaultdict as _dd
    buckets: dict[tuple[int, str], list[list[DetectedField]]] = _dd(list)
    for d in with_slot:
        groups = buckets[(d.page, d.profile_key)]
        placed = False
        for g in groups:
            if any(_overlaps(d.value_slot, x.value_slot) for x in g):
                g.append(d)
                placed = True
                break
        if not placed:
            groups.append([d])
    unique = list(no_slot)
    for group_list in buckets.values():
        for g in group_list:
            best = max(g, key=lambda x: (x.value_slot[2] - x.value_slot[0]))
            unique.append(best)
    # Keep original reading order for stable downstream behaviour.
    unique.sort(key=lambda d: (d.page, d.label_rect[1], d.label_rect[0]))
    detected = unique

    # Second pass: if the SAME slot has been claimed by two *different*
    # profile_keys (bilingual labels like "收款銀行 / Beneficiary Bank" that
    # map to bank_name AND beneficiary_bank respectively), keep only the
    # one whose label is Chinese. Forms designed for Taiwan typically use
    # the Chinese label as the authoritative semantic. The multi-key
    # behaviour we want to preserve (e.g. 發票聯數 → both invoice_type and
    # tax_type) still works because those share a SINGLE shared label, not
    # a bilingual pair — so this filter leaves them alone.
    slot_groups: dict[tuple[int, int, int], list[DetectedField]] = _dd(list)
    for d in detected:
        if d.value_slot is None:
            continue
        cx = round((d.value_slot[0] + d.value_slot[2]) / 20) * 10
        cy = round((d.value_slot[1] + d.value_slot[3]) / 20) * 10
        slot_groups[(d.page, cx, cy)].append(d)

    keep_ids: set[int] = set()
    for group in slot_groups.values():
        keys = {x.profile_key for x in group}
        if len(keys) <= 1:
            for x in group:
                keep_ids.add(id(x))
            continue
        # Check if *all* entries share the same label text — that is the
        # intentional multi-key case (e.g. 發票聯數 → invoice_type + tax_type).
        label_texts = {x.label_text for x in group}
        if len(label_texts) == 1:
            for x in group:
                keep_ids.add(id(x))
            continue
        # Bilingual mix: prefer the entry whose label has CJK characters.
        zh_entry = next(
            (x for x in group if any("一" <= c <= "鿿" for c in x.label_text)),
            None,
        )
        chosen_key = zh_entry.profile_key if zh_entry else group[0].profile_key
        for x in group:
            if x.profile_key == chosen_key:
                keep_ids.add(id(x))
    detected = [d for d in detected if d.value_slot is None or id(d) in keep_ids]

    # If a below-adj field shares its label cell with another detected
    # field's label, both belong to the same merged row — switch the
    # below-adj field to inline-in-cell with the sibling's right edge as
    # its right boundary. Catches "分行名稱：…  分行代號：…" where one
    # half got inline preference but the other half stayed below-adj.
    for d in detected:
        if d.slot_kind != "below-adj" or d.value_slot is None:
            continue
        cells_pp_d = page_cells_pp[d.page] if d.page < len(page_cells_pp) else []
        if not cells_pp_d:
            continue
        cell = pdf_layout.find_cell_containing(d.label_rect, cells_pp_d)
        if cell is None:
            continue
        cx0, cy0, cx1, cy1 = cell
        # Other detected labels in same cell.
        sib_xs: list[float] = []
        for o in detected:
            if o is d or o.page != d.page or o.value_slot is None:
                continue
            if o.label_rect == d.label_rect:
                continue
            ox0, oy0, ox1, oy1 = o.label_rect
            if cx0 - 0.5 <= ox0 and ox1 <= cx1 + 0.5 and cy0 - 0.5 <= oy0 and oy1 <= cy1 + 0.5:
                sib_xs.append(ox0)
        if not sib_xs:
            continue
        lx0, ly0, lx1, ly1 = d.label_rect
        # Right boundary = next sibling to the right (or cell edge).
        rights = [x for x in sib_xs if x > lx1 + 2]
        right_bound = min(rights) - 3 if rights else cx1 - 3
        if right_bound - (lx1 + 3) > 10:
            d.value_slot = (lx1 + 3, cy0, right_bound, cy1)
            d.slot_kind = "inline"

    # Post-detection: if a field's slot contains another detected field's
    # label (e.g. 發票聯數's below-adj landed on the Swift Code header row),
    # the slot is targeting the wrong cell. Flip it back to inline inside
    # the label's own cell so the filler doesn't overwrite the sibling
    # column's label area.
    for d in detected:
        if d.value_slot is None:
            continue
        sx0, sy0, sx1, sy1 = d.value_slot
        clash = False
        for o in detected:
            if o is d or o.page != d.page:
                continue
            # Skip same-key duplicates (line vs span emissions).
            if o.profile_key == d.profile_key and o.label_rect == d.label_rect:
                continue
            ox0, oy0, ox1, oy1 = o.label_rect
            if (sx0 - 2 <= ox0 and ox1 <= sx1 + 2
                    and sy0 - 2 <= oy0 and oy1 <= sy1 + 2):
                clash = True
                break
        if not clash:
            continue
        # Revert to inline-in-own-cell if we can.
        cells_pp = page_cells_pp[d.page] if d.page < len(page_cells_pp) else []
        cell = pdf_layout.find_cell_containing(d.label_rect, cells_pp) if cells_pp else None
        if cell is None:
            d.value_slot = None
            d.slot_kind = "unbounded"
            continue
        lx0, ly0, lx1, ly1 = d.label_rect
        cx0, cy0, cx1, cy1 = cell
        if cx1 - lx1 > 10:
            d.value_slot = (lx1 + 3, cy0, cx1 - 3, cy1)
            d.slot_kind = "inline"
        else:
            # No inline room and no safe below cell — mark unbounded so the
            # filler defers to checkbox detection (common for 發票聯數 rows).
            d.value_slot = None
            d.slot_kind = "unbounded"

    # Universal horizontal clamp: any slot's right edge must stop before the
    # next same-row sibling label — prevents values from spilling into the
    # adjacent column in forms where pdfplumber didn't find a cell boundary
    # (unbounded slots) or where the right-adj cell itself is a wide merged
    # cell that crosses column boundaries (Swift Code | 銀行名稱 | 分行).
    # Also clamps to the page's right margin so unbounded slots can't run
    # off the visible page.
    for d in detected:
        if d.value_slot is None:
            continue
        sx0, sy0, sx1, sy1 = d.value_slot
        lx0, ly0, lx1, ly1 = d.label_rect
        row_h = max(1.0, ly1 - ly0)
        right_bound = sx1
        # Page right edge — leave a small margin to match the form's own
        # printed margin.
        if d.page < len(pages):
            right_bound = min(right_bound, pages[d.page].width_pt - 10)
        # If the label was wrapped in parentheses like "(銀行代號：" + ")",
        # the closing paren sits between label.x1 and the value area. Push
        # the slot's left edge past any such trailing punctuation so the
        # value text doesn't render on top of the printed character. We
        # identify by character (")" / "）") rather than position so this
        # works on any form regardless of layout.
        TRAIL_CHARS = (")", "）", "]", "］")
        for text, (bx0, by0, bx1, by1), _size, _color in page_spans[d.page]:
            t = text.strip()
            if t not in TRAIL_CHARS:
                continue
            # Same row & between label end and current slot start.
            y_overlap = max(0.0, min(ly1, by1) - max(ly0, by0))
            if y_overlap < row_h * 0.5:
                continue
            if bx0 < lx1 - 1 or bx0 > sx1 - 1:
                continue
            # Push slot start to just past the closing paren.
            new_x0 = bx1 + 2
            if new_x0 > sx0:
                sx0 = new_x0
        for o in detected:
            if o is d or o.page != d.page:
                continue
            ox0, oy0, ox1, oy1 = o.label_rect
            # Same row?
            y_overlap = max(0.0, min(ly1, oy1) - max(ly0, oy0))
            if y_overlap < row_h * 0.5:
                continue
            # Sibling must start to our right.
            if ox0 <= lx1 + 2:
                continue
            # 已經被推到後面的子標籤不算右界 —— 值的起點就在它右邊了，
            # 再拿它當右界會把位置夾成 10pt（實測地址欄整條被壓成一小段）。
            if ox1 <= sx0 + 1:
                continue
            right_bound = min(right_bound, ox0 - 3)
        # Apply both adjustments at once (slot.x0 may have moved right).
        if sx0 != d.value_slot[0] or right_bound < sx1:
            new_right = max(sx0 + 10, min(right_bound, sx1))
            d.value_slot = (sx0, sy0, new_right, sy1)

    # Drop anything inside a page's signature / seal zone — "公司章",
    # "簽章", "蓋章", "印鑑", "填表人" — those rows are for a physical
    # stamp or handwritten signature, not auto-fill. Exclude both the row
    # containing the marker and everything below it on that page.
    for pno, spans in enumerate(page_spans):
        seal_y: Optional[float] = None
        for text, (bx0, by0, bx1, by1), _size, _color in spans:
            t = text.strip()
            if not t:
                continue
            # Strip trailing colon for comparison.
            if _is_seal_marker(t):
                if seal_y is None or by0 < seal_y:
                    seal_y = by0
        if seal_y is None:
            continue
        # Keep a small tolerance above the marker so same-row sibling labels
        # (like "負責人" next to "公司章") are also excluded.
        cutoff = seal_y - 2.0
        detected = [
            d for d in detected
            if not (d.page == pno and d.label_rect[1] >= cutoff)
        ]

    return detected, pages


def _choose_placement(
    label_bbox: tuple[float, float, float, float],
    label_size: float,
    label_text: str,
    other_spans: list[tuple[str, tuple[float, float, float, float], float]],
    min_right_room_pt: float,
    gap_pt: float,
    baseline: float,
) -> tuple[str, tuple[float, float]]:
    """Decide whether to place the value to the RIGHT of the label or BELOW it.

    Strategy: look at every other text span on the same page; if the closest
    one occupying the label's row (y-overlap) sits within ``min_right_room_pt``
    to the right of the label, there's no room on the row → use BELOW.
    Otherwise default to RIGHT.
    """
    x0, y0, x1, y1 = label_bbox
    label_h = max(1.0, y1 - y0)

    nearest_right_dx: Optional[float] = None
    for text, (sx0, sy0, sx1, sy1), _size in other_spans:
        # Skip the label itself (same bbox)
        if abs(sx0 - x0) < 0.5 and abs(sy0 - y0) < 0.5 and text == label_text:
            continue
        # Same-row test: vertical overlap > ~40% of label height
        overlap = max(0.0, min(y1, sy1) - max(y0, sy0))
        if overlap < label_h * 0.4:
            continue
        # Must be to the right
        if sx0 <= x1:
            continue
        dx = sx0 - x1
        if nearest_right_dx is None or dx < nearest_right_dx:
            nearest_right_dx = dx

    if nearest_right_dx is not None and nearest_right_dx < min_right_room_pt:
        # Crowded on the right — put value below the label, left-aligned.
        line_h = label_size * 1.4
        return "below", (x0, y1 + line_h * 0.85)
    return "right", (x1 + gap_pt, baseline)


def find_unmatched_candidates(pdf_path: Path) -> list[dict]:
    """Return short text spans that *look* like field labels but did NOT
    match the current synonym map.

    Feeds the "未對應的標籤" list on the pdf-fill preview, where the user
    picks a canonical key and the UI writes the pairing back into the
    synonym store — progressively teaching the system new vendor forms.
    """
    label_to_key = _build_synonym_index(_active_label_map())
    out: list[dict] = []
    seen: set[str] = set()
    with fitz.open(str(pdf_path)) as doc:
        for pno in range(doc.page_count):
            page = doc[pno]
            td = page.get_text("dict")
            for block in td.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        t = (span.get("text") or "").strip()
                        n = _normalize(t)
                        if not n or n in seen:
                            continue
                        if not (2 <= len(n) <= 12):
                            continue
                        if n in label_to_key:
                            continue
                        # Need some CJK or letter content — skip pure punct/digits.
                        if not any(
                            c.isalpha() or "一" <= c <= "鿿" for c in n
                        ):
                            continue
                        seen.add(n)
                        out.append({"text": t, "normalized": n, "page": pno})
    return out


def summarize(detected: Iterable[DetectedField]) -> dict[str, int]:
    """Return ``{profile_key: occurrence_count}`` — useful for quick reporting."""
    out: dict[str, int] = {}
    for d in detected:
        out[d.profile_key] = out.get(d.profile_key, 0) + 1
    return out
