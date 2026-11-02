"""事務機掃描取件：型號辨識與相容性狀態。

**這裡的辨識只是「從網頁上看到什麼」**，不代表支援：
規格書（docs-share/mfp-fetch/）規定未經實機驗證的型號一律不可以標「已支援」，
使用者也交代實機實測通過之前整支工具標 Beta。只有在實機上通過取件測試的型號才在 `VERIFIED_PROFILES`
（2026-10-19：Apeos C5571 客戶實機取件成功，使用者同意標已支援）。

**同系列**（`SAME_SERIES`）：使用者 2026-10-19「同類 OS 的一併標示」—— 跟已驗證型號同一個系列（原廠同一份參考手冊、
同一套驅動與韌體更新服務）的型號標 `supported_same_series`，畫面寫明「以哪一款實機驗證」，不假裝每一款都測過。

辨識依據一律附在結果裡（哪個頁面、哪個欄位、摘錄），讓看報告的人知道判斷從哪來；
單一個 Server 標頭或標題不足以確認完整型號，所以也記下比對到幾個依據。
"""
from __future__ import annotations

import functools
import re
from typing import Any, Optional

#: 規格書列的首批四款（**廠牌系列＋型號都要對上**，不能只比對 C5570 這種尾碼 ——
#: ApeosPort C5570 與 Apeos C5570 是不同系列）。
FIRST_BATCH: frozenset[tuple[str, str]] = frozenset({
    ("Apeos", "C5571"),
    ("Apeos", "C5570"),
    ("ApeosPort", "C4570"),
    ("ApeosPort-V", "C6680"),
})

#: 有取件方式（實驗功能）的其他廠牌 —— 跟 `mfp_share.VENDOR_ROUTES` 一致（測試釘住）。
#: Canon / Konica Minolta / SHARP 是檔案分享（原廠手冊）；KYOCERA / RICOH 是直接取件（原廠工具的分析，v1.16.95）。
EXPERIMENTAL_VENDORS: frozenset[str] = frozenset({"Canon", "Konica Minolta", "Sharp", "Kyocera", "Ricoh"})

#: 已在實機上通過取件測試的（系列, 型號）。**目前是空的。**
VERIFIED_PROFILES: frozenset[tuple[str, str]] = frozenset({
    ("Apeos", "C5571"),          # 2026-10-19 客戶實機：信箱列文件、取件（TIFF → PDF）成功，報告在 docs-share/mfp-fetch/field-reports/
})

#: FUJIFILM 以外、已在實機上通過取件的型號（廠牌 → 型號，比對時不分大小寫、去掉前面的廠牌字樣）。
#: 2026-10-21 客戶實機：RICOH IM C3510 帳號密碼登入、文件伺服器（共用資料夾與設備資料夾、資料夾密碼）列出與取件成功；
#: 同一位客戶另外在 IM C2000 / C2010 / C2510 / C3500 也測過取件正常。使用者同意標「已支援」；其他型號仍是實驗功能。
VERIFIED_OTHER: dict[str, frozenset[str]] = {
    "Ricoh": frozenset({"IM C2000", "IM C2010", "IM C2510", "IM C3500", "IM C3510"}),
}


def _norm_model(vendor: str, model: str) -> str:
    m = re.sub(r"\s+", " ", (model or "").strip()).upper()
    v = (vendor or "").strip().upper()
    if v and m.startswith(v + " "):
        m = m[len(v) + 1:]
    return m


def verified_other(vendor: Optional[str], model: Optional[str]) -> bool:
    """FUJIFILM 以外的這台（廠牌＋型號）是不是已在實機上驗證過取件。"""
    want = {_norm_model(vendor or "", x) for x in VERIFIED_OTHER.get(vendor or "", ())}
    return bool(model) and _norm_model(vendor or "", model) in want


def effective_status(vendor: Optional[str], model: Optional[str], status: Optional[str]) -> Optional[str]:
    """存起來的辨識結果套上目前的已驗證清單：之前判成「實驗功能」、現在已驗證的型號改成「已支援」，
    不必請使用者重新測試（2026-10-21 RICOH IM C3510）。"""
    if status == "experimental_vendor" and verified_other(vendor, model):
        return "supported"
    return status


#: 同系列 → 以哪一款實機驗證。Apeos C7071 / C6571 / C5571 / C4571 / C3571 / C3071 / C2571 是同一個系列
#: （原廠同一份參考手冊；C3071 是海外型號、C2571 是日本型號）。前一代（C5570 等）不算。
SAME_SERIES: dict[tuple[str, str], tuple[str, str]] = {
    ("Apeos", m): ("Apeos", "C5571") for m in ("C7071", "C6571", "C4571", "C3571", "C3071", "C2571")
}

#: 實機測試紀錄（工具頁「已驗證支援清單」顯示這份；使用者 2026-10-19 要求）。**只寫真的測過的**，
#: 驗證到哪一步就寫到哪一步：`verified`＝這項功能整條在實機上走過；`same_series`＝同系列、以另一款實機驗證；
#: `partial`＝部分步驟驗證過（`note` 寫清楚哪些還沒驗）。客戶型號、日期以外的細節（客戶名稱、位址）不寫這裡。
FIELD_TESTS: tuple[dict, ...] = (
    {"vendor": "FUJIFILM", "models": ("Apeos C5571",), "feature": "機內信箱取件", "result": "verified",
     "note": "列出文件、有密碼的信箱、取件（TIFF 轉 PDF）、取件並刪除", "date": "2026-10-20"},
    {"vendor": "FUJIFILM", "models": ("Apeos C7071", "Apeos C6571", "Apeos C4571", "Apeos C3571", "Apeos C3071",
                                      "Apeos C2571"),
     "feature": "機內信箱取件", "result": "same_series", "note": "同系列，以 Apeos C5571 實機驗證", "date": ""},
    {"vendor": "RICOH", "models": ("IM C3510",), "feature": "直接取件（文件伺服器）", "result": "verified",
     "note": "帳號密碼登入；共用資料夾與設備資料夾（含資料夾密碼）列出文件、取件", "date": "2026-10-21"},
    {"vendor": "RICOH", "models": ("IM C2000", "IM C2010", "IM C2510", "IM C3500"), "feature": "直接取件（文件伺服器）",
     "result": "verified", "note": "列出文件、取件", "date": "2026-10-21"},
    {"vendor": "HP", "models": ("LaserJet MFP M141w",), "feature": "eSCL 掃描", "result": "verified",
     "note": "平台玻璃掃描存進工作區", "date": "2026-10-14"},
)
FIELD_RESULT_LABELS: dict[str, str] = {"verified": "已驗證", "same_series": "同系列", "partial": "部分驗證"}

#: 狀態代碼 → 固定訊息（翻譯檔有英日文）。
STATUS_MESSAGES: dict[str, str] = {
    "supported": "已支援（已通過實機驗證）",
    "supported_same_series": "已支援（同系列，以 Apeos C5571 實機驗證）",
    "first_batch_unverified": "首批規劃的型號，尚未完成實機驗證",
    "same_vendor_unverified": "同廠牌，尚未驗證是否支援",
    "experimental_vendor": "實驗功能：這個廠牌的取件依原廠手冊或原廠工具的分析寫成，尚未在實機上驗證",
    "unsupported_vendor": "不支援取件：這個廠牌目前沒有取件方式（支援 eSCL 的話仍可以從本站掃描）",
    #: 畫面用：廠牌沒有取件方式、但這台有 eSCL 能掃描（原本一律顯示紅色「不支援取件」，像整台不能用）
    "scan_only": "可以從本站掃描（eSCL）；這個廠牌目前沒有取件方式",
    "login_required": "需要登入後才能辨識型號",
    "unknown": "無法辨識型號",
    "unreachable": "連不上設備，無法辨識",
}

#: 畫面上的「相容性」：短的狀態放進膠囊標籤，說明另外一行。整句塞進膠囊會折成兩行、變成一顆橢圓
#: （使用者 2026-10-14 指出）。`STATUS_MESSAGES` 是完整的一句，API、診斷報告、服務紀錄檔照舊用那一份。
STATUS_LABELS: dict[str, tuple[str, str]] = {
    "supported": ("已支援", "已通過實機驗證"),
    "supported_same_series": ("已支援", "同系列，以 Apeos C5571 實機驗證"),
    "first_batch_unverified": ("尚未驗證", "首批規劃的型號，尚未完成實機驗證"),
    "same_vendor_unverified": ("尚未驗證", "同廠牌，尚未驗證是否支援"),
    "experimental_vendor": ("實驗功能", "取件依原廠手冊或原廠工具的分析寫成，尚未實機驗證"),
    "unsupported_vendor": ("不支援取件", "這個廠牌目前沒有取件方式；支援 eSCL 的話仍可以從本站掃描"),
    "scan_only": ("可掃描", "可以從本站掃描（eSCL）；這個廠牌沒有取件方式"),
    "login_required": ("需要登入", "登入後才能辨識型號"),
    "unknown": ("無法辨識", "首頁上找不到廠牌或型號"),
    "unreachable": ("連不上", ""),
}

_FUJI_VENDOR = re.compile(
    r"FUJI\s?FILM|Fuji\s?Xerox|FUJI\s?XEROX|富士フイルム|富士ゼロックス|富士軟片|富士全錄", re.I)
#: 系列名稱＋型號。世代（-V、-VI、-VII）要留著：ApeosPort-V C6680 與 ApeosPort C6680 不同。
_FUJI_MODEL = re.compile(
    r"\b(ApeosPort(?:-(?:VII|VI|V|IV))?|ApeosPrint|ApeosPro|Apeos|DocuCentre(?:-(?:VII|VI|V|IV))?|DocuPrint)"
    r"[\s\-]*([A-Z]{0,2}\d{3,4}[A-Z]{0,2})\b")
_FUJI_CWIS = re.compile(r"CentreWare\s+Internet\s+Services|Internet\s+Services", re.I)
_FIRMWARE = re.compile(
    r"(?:Controller\s*(?:ROM|Version)|Firmware\s*Version|ファームウェア(?:バージョン)?|コントローラー?\s*ROM|韌體版本)"
    r"\s*[:：]?\s*([0-9][0-9A-Za-z.\-]{1,30})", re.I)

#: 其他廠牌的特徵（判斷「不是 FUJIFILM」與顯示廠牌）。**順序有意義**：FUJI 先判（避免 Fuji Xerox 被當成
#: Xerox），特徵越明確的越前面。「Embedded Web Server」HP、Xerox、Lexmark 都在用，不可以當成 HP 的特徵。
_OTHER_VENDORS: tuple[tuple[str, re.Pattern], ...] = (
    ("Xerox", re.compile(r"\bXerox\b|AltaLink|VersaLink|WorkCentre|PrimeLink|CentreWare", re.I)),
    ("Canon", re.compile(r"\bCanon\b|imageRUNNER|imageCLASS|i-SENSYS|Remote\s*UI|\biR-ADV\b|PIXMA|MAXIFY", re.I)),
    ("Ricoh", re.compile(r"\bRICOH\b|Web\s*Image\s*Monitor|\bSavin\b|\bLanier\b", re.I)),
    # DEVELOP（ineo / ineo+）是 Konica Minolta 集團的品牌，軟體大多共用（使用者 2026-10-15 實測、2026-11-02 決定歸到這一家）：
    # 取件沿用 Konica Minolta 的公用信箱（SMB，實驗功能），型號顯示時帶上 DEVELOP。
    # **DEVELOP 只認全大寫**（品牌寫法）—— 網頁上的英文單字 develop / Developer 不可以算。
    ("Konica Minolta", re.compile(r"KONICA|MINOLTA|bizhub|PageScope|OpenAPI\s*Web|(?-i:\bDEVELOP\b)|\bineo\+?(?=[\s\d]|$)", re.I)),
    # `KM-MFP-http`：KYOCERA Command Center RX 的網頁伺服器（Server 標頭）。客戶實機 ECOSYS M6535cidn 的首頁沒有任何廠牌字樣、
    # 型號是 JavaScript 之後才填，只認得出這個標頭 —— 原本「未辨識」，直接取件也不出現
    ("Kyocera", re.compile(r"KYOCERA|TASKalfa|ECOSYS|Command\s*Center|Triumph-Adler|\bUTAX\b|\bKM-MFP-http\b", re.I)),
    ("Sharp", re.compile(r"\bSHARP\b", re.I)),
    ("Toshiba", re.compile(r"TOSHIBA|e-STUDIO|TopAccess", re.I)),
    ("Lexmark", re.compile(r"\bLexmark\b", re.I)),
    ("Samsung", re.compile(r"\bSamsung\b|SyncThru", re.I)),
    ("HP", re.compile(r"\bHP\b|Hewlett|LaserJet|OfficeJet|DeskJet|PageWide", re.I)),
    # 標頭常寫成「EPSON_Linux」—— 底線是單字字元，\b 認不到；改成前後不是英文字母
    ("Epson", re.compile(r"(?<![A-Za-z])EPSON(?![A-Za-z])|EpsonNet", re.I)),
    ("Brother", re.compile(r"\bBrother\b|BRAdmin", re.I)),
    ("OKI", re.compile(r"\bOKI\b|OKI\s*Data", re.I)),
    ("Panasonic", re.compile(r"\bPanasonic\b", re.I)),
    ("Dell", re.compile(r"\bDell\b", re.I)),
    ("Pantum", re.compile(r"\bPantum\b", re.I)),
)

#: 各廠牌的型號寫法（只拿來**顯示**，相容性判斷不變）。認出廠牌之後才找，標題優先。
#: 原本只認 FUJIFILM：Epson L3150 首頁標題寫著「L3150 Series」也顯示「未辨識」（2026-10-14 使用者回報）。
#: 規則照各家產品線的命名寫，**不可以只照手上這幾台調**（使用者 2026-10-14）—— 每一家的寫法在
#: `tests/test_mfp_profiles.py` 都有樣本（照命名規則杜撰的標題），也有「不可以當成型號」的反例。
_OTHER_MODELS: dict[str, re.Pattern] = {
    "Epson": re.compile(r"\b((?:L|ET|WF|XP|EW|PX|EP|LX|DS|ES|FF|AM|ST)-?[A-Z]{0,2}\d{3,5}[A-Z]{0,4}(?:\s+Series)?)\b"),
    "HP": re.compile(r"\b((?:HP\s+)?(?:Color\s+)?(?:LaserJet|OfficeJet|DeskJet|ENVY|PageWide|Smart\s+Tank|Neverstop|"
                     r"Ink\s+Tank|DesignJet|ScanJet)(?:\s+(?:Pro|Managed|Enterprise|Flow|MFP|Tank|Plus|Wireless|Laser))*"
                     r"\s+(?:MFP\s+)?[A-Z]{0,3}\d{2,5}[a-z]{0,4}(?:\s+(?:MFP|series))?)\b", re.I),
    "Canon": re.compile(r"\b((?:imageRUNNER(?:\s+ADVANCE)?(?:\s+DX)?|iR-ADV|imageCLASS|imagePRESS|i-SENSYS|MAXIFY|PIXMA|"
                        r"Satera|imagePROGRAF)\s?[A-Z]{0,3}\s?\d{3,5}[A-Za-z]{0,4}(?:\s+(?:II|III|IV))?|MF\d{3,4}[A-Za-z]{0,4})\b"),
    "Ricoh": re.compile(r"\b((?:RICOH\s+)?(?:IM|MP|SP|M|P)\s?C?\s?\d{3,5}[A-Za-z]{0,5})\b"),
    "Konica Minolta": re.compile(r"\b(bizhub\s+(?:PRESS\s+|PRO\s+)?C?\d{3,5}[A-Za-z]{0,4}"
                                 r"|(?:DEVELOP\s+)?ineo\+?\s?C?\d{3,5}[A-Za-z]{0,4})(?![\w+])", re.I),
    "Kyocera": re.compile(r"\b((?:TASKalfa|ECOSYS)\s+(?:MA|PA|M|P)?\d{3,5}[a-z]{0,6})\b", re.I),
    # MX-3071、MX-C507F、BP-50C26（新款是「兩位數＋字母＋數字」）
    "Sharp": re.compile(r"\b((?:MX|BP|AR|DX)-[A-Z]?\d{2,5}[A-Z]{0,2}\d{0,3}[A-Z]{0,3})\b", re.I),
    "Toshiba": re.compile(r"\b(e-STUDIO\s?\d{3,5}[A-Z]{0,4})\b", re.I),
    "Xerox": re.compile(r"\b((?:Xerox\s+)?(?:AltaLink|VersaLink|WorkCentre|Phaser|PrimeLink)\s+[A-Z]?\d{3,5}[A-Za-z]{0,3}"
                        r"|Xerox\s+[BC]\d{3,4}[A-Za-z]{0,3})\b", re.I),
    "Lexmark": re.compile(r"\b((?:Lexmark\s+)?(?:MX|CX|MS|CS|MB|MC|XC|XM|CX)\d{3,4}[a-z]{0,4})\b", re.I),
    "Samsung": re.compile(r"\b((?:SL|SCX|CLX|ML|SF|CLP)-[A-Z]?\d{3,5}[A-Z]{0,4})\b", re.I),
    "Brother": re.compile(r"\b((?:MFC|DCP|HL|ADS|PJ)-[A-Z]{0,2}\d{3,5}[A-Z]{0,4}(?:\s+series)?)\b", re.I),
    "OKI": re.compile(r"\b((?:OKI\s+)?(?:MC|MB|ES|C|B)\d{3,4}[a-z]{0,4})\b", re.I),
    "Panasonic": re.compile(r"\b((?:KX|DP)-[A-Z]{0,4}\d{3,5}[A-Z]{0,3})\b", re.I),
    "Dell": re.compile(r"\b((?:Dell\s+)?[BCEHS]\d{3,4}[a-z]{0,4})\b", re.I),
    "Pantum": re.compile(r"\b((?:Pantum\s+)?(?:CM|CP|BM|BP|M|P)\d{3,4}[A-Z]{0,4})\b", re.I),
}
#: 型號裡不可以有的東西：IP、版本號（帶點）、年份與日期。
_NOT_MODEL = re.compile(r"\d+\.\d+|\b(?:19|20)\d{2}\b(?![A-Za-z])")


def _other_model(vendor: str, srcs: list[tuple[dict, str]]) -> Optional[tuple[str, dict, str, re.Match]]:
    rx = _OTHER_MODELS.get(vendor)
    if rx is None:
        return None
    ordered = [x for x in srcs if x[0].get("field") == "title"] + [x for x in srcs if x[0].get("field") != "title"]
    for where, text in ordered:
        for m in rx.finditer(text):
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            if _NOT_MODEL.search(val):
                continue
            return val[:60], where, text, m
    return None


def _excerpt(text: str, m: re.Match, width: int = 60) -> str:
    a, b = max(0, m.start() - width // 2), min(len(text), m.end() + width // 2)
    return re.sub(r"\s+", " ", text[a:b]).strip()[:120]


def _sources(pages: list[dict]) -> list[tuple[dict, str]]:
    """（依據位置, 文字）。位置用代碼（`field`：title / text / header），畫面再翻譯。
    標題與可見文字之外，也看 Server 標頭。"""
    out: list[tuple[dict, str]] = []
    for p in pages:
        page = f"{p.get('scheme', '')}:{p.get('port', '')}{p.get('path', '/')}"
        if p.get("title"):
            out.append(({"page": page, "field": "title"}, p["title"]))
        if p.get("text_full_for_detection"):
            out.append(({"page": page, "field": "text"}, p["text_full_for_detection"]))
        for k, v in p.get("headers") or []:
            if k.lower() in ("server", "x-powered-by"):
                out.append(({"page": page, "field": "header", "header": k}, v))
    return out


@functools.lru_cache(maxsize=1)
def rules_signature() -> str:
    """辨識規則的指紋：廠牌 / 型號 / 韌體的比對規則、已驗證與同系列清單。改了任何一條就會變 ——
    不靠人記得升版號。連線測試把它記在報告裡；清單用它判斷「這台是用舊規則判斷的」（`rules_stale`）。"""
    import hashlib
    parts = [_FUJI_VENDOR.pattern, _FUJI_MODEL.pattern, _FUJI_CWIS.pattern, _FIRMWARE.pattern, _NOT_MODEL.pattern]
    parts += [f"{n}={rx.pattern}" for n, rx in _OTHER_VENDORS]
    parts += [f"{n}={rx.pattern}" for n, rx in sorted(_OTHER_MODELS.items())]
    parts += sorted(map(str, VERIFIED_PROFILES)) + sorted(map(str, SAME_SERIES)) + sorted(map(str, FIRST_BATCH))
    parts += sorted(EXPERIMENTAL_VENDORS)
    parts += sorted(f"{v}={m}" for v, ms in VERIFIED_OTHER.items() for m in ms)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


#: 這些結果在規則更新後可能變成認得出來（認得出來的不打擾）
RECHECK_STATUSES = frozenset({"unknown", "unsupported_vendor", "login_required"})


def detect(pages: list[dict], escl_model: str = "") -> dict[str, Any]:
    """從連線測試收集到的頁面判斷廠牌、型號、韌體與相容性狀態。

    `escl_model`：eSCL 能力表回報的型號（`MakeAndModel`，標準欄位）。FUJIFILM 以外的廠牌優先用它；
    頁面上的寫法各家不同，標準欄位比較可靠。"""
    res: dict[str, Any] = {"vendor": None, "family": None, "model": None, "firmware": None,
                           "status": "unknown", "evidence": [], "evidence_count": 0}
    if not pages:
        res["status"] = "unreachable"
        res["message"] = STATUS_MESSAGES["unreachable"]
        return res
    srcs = _sources(pages)
    # eSCL 的 MakeAndModel 也是依據（放在最後：網頁上有就先用網頁的）。客戶實機 2026-10-16：
    # Apeos C5571 的首頁上沒有廠牌或型號，只有 eSCL 回報 `Apeos C5571` —— 原本 FUJIFILM 只看網頁，
    # 結果「未辨識」，連機內信箱取件都不出現
    if escl_model:
        srcs = srcs + [({"page": "eSCL", "field": "escl"}, escl_model[:200])]
    ev: list[dict] = []
    fuji = False
    for where, text in srcs:
        m = _FUJI_VENDOR.search(text)
        if m:
            fuji = True
            ev.append({"what": "vendor", **where, "excerpt": _excerpt(text, m)})
            break
    model: Optional[tuple[str, str]] = None
    for where, text in srcs:
        m = _FUJI_MODEL.search(text)
        if m:
            model = (m.group(1), m.group(2).upper())
            ev.append({"what": "model", **where, "excerpt": _excerpt(text, m)})
            break
    if not fuji:
        for where, text in srcs:
            m = _FUJI_CWIS.search(text)
            if m and model:
                ev.append({"what": "ui", **where, "excerpt": _excerpt(text, m)})
                break
    for where, text in srcs:
        m = _FIRMWARE.search(text)
        if m:
            res["firmware"] = m.group(1)
            ev.append({"what": "firmware", **where, "excerpt": _excerpt(text, m)})
            break
    if fuji or model:
        res["vendor"] = "FUJIFILM"
        if model:
            res["family"], res["model"] = model
        if model and model in VERIFIED_PROFILES:
            res["status"] = "supported"
        elif model and model in SAME_SERIES:
            res["status"] = "supported_same_series"
        elif model and model in FIRST_BATCH:
            res["status"] = "first_batch_unverified"
        elif model:
            res["status"] = "same_vendor_unverified"
        elif any(p.get("password_inputs") for p in pages):
            res["status"] = "login_required"
        else:
            res["status"] = "same_vendor_unverified"
    else:
        for name, rx in _OTHER_VENDORS:
            hit = next(((w, t, m) for w, t in srcs for m in [rx.search(t)] if m), None)
            if hit:
                res["vendor"] = name
                # Canon / Konica Minolta / SHARP / KYOCERA / RICOH 有取件方式（實驗功能，`mfp_share.VENDOR_ROUTES`）
                res["status"] = "experimental_vendor" if name in EXPERIMENTAL_VENDORS else "unsupported_vendor"
                ev.append({"what": "vendor", **hit[0], "excerpt": _excerpt(hit[1], hit[2])})
                if escl_model:
                    res["model"] = escl_model[:60]
                    ev.append({"what": "model", "page": "eSCL", "field": "escl", "excerpt": escl_model[:120]})
                else:
                    got = _other_model(name, srcs)
                    if got:
                        res["model"] = got[0]
                        # DEVELOP 的 ineo 系列：型號前面補上品牌，畫面看得出不是 bizhub
                        if name == "Konica Minolta" and re.match(r"ineo", got[0], re.I):
                            res["model"] = "DEVELOP " + got[0]
                        ev.append({"what": "model", **got[1], "excerpt": _excerpt(got[2], got[3])})
                break
        else:
            if any(p.get("password_inputs") for p in pages):
                res["status"] = "login_required"
    res["status"] = effective_status(res["vendor"], res["model"], res["status"])
    res["evidence"] = ev
    res["evidence_count"] = len(ev)
    res["message"] = STATUS_MESSAGES[res["status"]]
    return res
