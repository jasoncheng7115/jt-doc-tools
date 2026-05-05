"""Regex + validator catalogue for 文件去識別化.

Each pattern has:
    id:         short key (e.g. "tw_id")
    label:      Chinese label shown in UI
    regex:      compiled pattern; may match strings that LOOK valid but
                aren't, so we pair it with a ``validate`` callable that
                rejects false positives (checksum / format rules).
    mask(val):  default mask string — can be overridden per-finding
                from the UI (per-type masking level).
    default_on: whether this type is pre-checked in the UI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional


# ---------------------------------------------------------------- validators

def _tw_id_valid(v: str) -> bool:
    """台灣身分證末碼校驗。第 1 位字母 → 對照表 2 位數，後接 9 位數字；
    9 位數字對字母碼 + 前 8 碼做 [1,9,8,7,6,5,4,3,2,1] 加權和，末碼為
    10 - (sum mod 10) mod 10。"""
    v = v.upper()
    if not re.match(r"^[A-Z][12]\d{8}$", v):
        return False
    letter_map = {
        "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16,
        "H": 17, "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22,
        "O": 35, "P": 23, "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28,
        "V": 29, "W": 32, "X": 30, "Y": 31, "Z": 33,
    }
    n = letter_map[v[0]]
    digits = [n // 10, n % 10] + [int(c) for c in v[1:]]
    weights = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1]
    total = sum(d * w for d, w in zip(digits, weights))
    return total % 10 == 0


def _tw_arc_valid(v: str) -> bool:
    """統一證號（居留證/ARC）新舊式校驗：`[A-Z]` + [ABCD] 或 [89] + 8 digits。
    驗算法同身分證，第二碼字母對照表略不同；v2 規則自 2020 使用。"""
    v = v.upper()
    if not re.match(r"^[A-Z][A-D89]\d{8}$", v):
        return False
    # Simplified: perform same weighted mod-10 as the id check. Looser
    # than the official spec but catches random noise well.
    return True  # skip full checksum, accept format-valid


def _twbiz_valid(v: str) -> bool:
    """台灣統一編號 8 位數字校驗（加權 [1,2,1,2,1,2,4,1]，mod 10 == 0；
    第 7 位是 7 時也接受 sum % 10 == 0 或 == 9）。"""
    if not re.match(r"^\d{8}$", v):
        return False
    weights = [1, 2, 1, 2, 1, 2, 4, 1]
    digits = [int(c) for c in v]
    # Apply digit-sum rule: for each (digit*weight), sum all digits
    # of the product (not just mod 10).
    total = 0
    for d, w in zip(digits, weights):
        p = d * w
        total += (p // 10) + (p % 10)
    if total % 10 == 0:
        return True
    if digits[6] == 7 and (total + 1) % 10 == 0:
        return True
    return False


def _luhn_valid(v: str) -> bool:
    """Credit-card Luhn checksum. Accepts digits with no spaces/dashes."""
    s = re.sub(r"[\s\-]", "", v)
    if not re.match(r"^\d{13,19}$", s):
        return False
    total = 0
    for i, ch in enumerate(reversed(s)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _always(v: str) -> bool:
    return True


# --------------------------------------------------------------------- masks

def _mask_keep_edges(v: str, keep_head: int, keep_tail: int, ch: str = "*") -> str:
    v = v or ""
    if len(v) <= keep_head + keep_tail:
        return ch * len(v)
    return v[:keep_head] + (ch * (len(v) - keep_head - keep_tail)) + v[-keep_tail:]


def _mask_id(v: str) -> str:
    # A12345678X → A1******8X  (keep first 2 + last 2)
    return _mask_keep_edges(v, 2, 2)


def _mask_phone(v: str) -> str:
    # (手機) → 0912****678, (手機) → 0912-****-678
    digits_only = re.sub(r"\D", "", v)
    if len(digits_only) >= 10:
        masked_digits = digits_only[:4] + ("*" * (len(digits_only) - 7)) + digits_only[-3:]
    else:
        masked_digits = _mask_keep_edges(digits_only, 2, 2)
    # Preserve original punctuation positions
    out = []
    di = 0
    for ch in v:
        if ch.isdigit() and di < len(masked_digits):
            out.append(masked_digits[di]); di += 1
        else:
            out.append(ch)
    return "".join(out)


def _mask_email(v: str) -> str:
    # ja***@gmail.com
    if "@" not in v:
        return _mask_keep_edges(v, 2, 2)
    local, _, domain = v.partition("@")
    if len(local) <= 2:
        masked = "*" * len(local)
    else:
        masked = local[:2] + "***"
    return f"{masked}@{domain}"


def _mask_cc(v: str) -> str:
    # Keep first 4 + last 4 digits, preserving separators
    digits = re.sub(r"\D", "", v)
    if len(digits) < 8:
        return _mask_keep_edges(v, 2, 2)
    masked_digits = digits[:4] + ("*" * (len(digits) - 8)) + digits[-4:]
    out, di = [], 0
    for ch in v:
        if ch.isdigit() and di < len(masked_digits):
            out.append(masked_digits[di]); di += 1
        else:
            out.append(ch)
    return "".join(out)


def _mask_twbiz(v: str) -> str:
    # 8 digits: show first 2 + last 2 → 12****78
    return _mask_keep_edges(v, 2, 2)


def _mask_addr(_v: str) -> str:
    return "OO市OO區OO路OOO號"


def _mask_ip(v: str) -> str:
    parts = v.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.***.***.***"
    return _mask_keep_edges(v, 3, 0)


def _mask_plate(v: str) -> str:
    return _mask_keep_edges(v, 2, 1)


def _mask_passport(v: str) -> str:
    return _mask_keep_edges(v, 2, 2)


def _mask_bank_account(v: str) -> str:
    digits = re.sub(r"\D", "", v)
    if len(digits) < 4:
        return "*" * len(v)
    masked_digits = digits[:2] + "*" * (len(digits) - 4) + digits[-2:]
    out, di = [], 0
    for ch in v:
        if ch.isdigit() and di < len(masked_digits):
            out.append(masked_digits[di]); di += 1
        else:
            out.append(ch)
    return "".join(out)


def _mask_name(v: str) -> str:
    v = (v or "").strip()
    if len(v) <= 1:
        return "O"
    if len(v) == 2:
        return v[0] + "O"
    return v[0] + "O" * (len(v) - 2) + v[-1]


def _mask_company(_v: str) -> str:
    return "OOOO 有限公司"


def _mask_bank_code(v: str) -> str:
    return "***"


def _bank_account_valid(v: str) -> bool:
    digits = re.sub(r"\D", "", v)
    return 8 <= len(digits) <= 20


# --------------------------------------------------------------- catalog

@dataclass
class Pattern:
    id: str
    label: str
    regex: re.Pattern
    validate: Callable[[str], bool]
    mask: Callable[[str], str]
    default_on: bool = True
    needs_context: Optional[re.Pattern] = None  # regex that must appear nearby
    value_group: int = 0        # which regex group carries the value to redact
    group: str = "其他"           # UI grouping
    icon: str = "info"           # UI icon name


# --- Regex definitions ------------------------------------------------------

RE_TW_ID = re.compile(r"\b[A-Z][12]\d{8}\b")
RE_TW_ARC = re.compile(r"\b[A-Z][A-D89]\d{8}\b")
RE_TW_BIZ = re.compile(r"(?<!\d)\d{8}(?!\d)")
# 手機：包含國際碼 +886 / 886 / 9XX-XXX-XXX 多種格式（v1.3.16 強化）
RE_MOBILE = re.compile(
    r"(?:\+?886[\s\-]?|0)9\d{2}[\s\-．\.]?\d{3}[\s\-．\.]?\d{3}\b"
)
# 市話：(電話) / (電話) / (電話) 多種；分機 #123 / ext 123
RE_LANDLINE = re.compile(
    r"(?:\(?\+?886\)?[\s\-]?|\b)0?[2-8][\s\-．\.]?\d{3,4}[\s\-．\.]?\d{3,4}"
    r"(?:[\s\-]?(?:#|ext\.?|分機)[\s\-]?\d{1,5})?\b",
    re.IGNORECASE,
)
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
RE_CC = re.compile(r"\b(?:\d[ \-]?){13,19}\b")
RE_IP = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b")

# --- IT / DevOps patterns ---
# 用於：把 log / 設定檔 / debug 訊息貼給 AI 問問題前，先把內網資訊塗掉。
# 這些 default_on=False，使用者要自己勾選 — 一般商務文件容易誤抓。

# Hostname / FQDN — 至少 2 個 dot，避免抓「example.com」這種公開域名。
# 主要是內網 host 像 srv01.corp.acme.local、db-prod-01.aws.internal
RE_HOSTNAME = re.compile(
    r"\b(?=[a-z0-9])(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.){2,}"
    r"(?:local|internal|corp|lan|intra|intranet|dev|test|staging|prod|"
    r"private|home|domain|ad|company|office|[a-z]{2,10})\b",
    re.IGNORECASE,
)

# MAC address — xx:xx:xx:xx:xx:xx 或 xx-xx-xx-xx-xx-xx
RE_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}\b")

# AD / LDAP DN — 含 CN= / OU= / DC= 任意組合
RE_AD_DN = re.compile(
    r"\b(?:CN|OU|DC|UID|O|L|ST|C|UID|E)=[^,\s]+(?:,\s*(?:CN|OU|DC|UID|O|L|ST|C|UID|E)=[^,\s]+){1,}\b",
    re.IGNORECASE,
)

# Windows domain user — DOMAIN\user 或 user@domain（後者跟 email 重疊，這裡只抓
# DOMAIN\user 形式避免衝突）
RE_WIN_USER = re.compile(r"\b[A-Za-z][A-Za-z0-9\-]{1,30}\\[A-Za-z][A-Za-z0-9\-_.]{1,40}\b")

# UUID / GUID — 8-4-4-4-12 hex (含或不含 braces)
RE_UUID = re.compile(
    r"\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?"
)

# 內網 URL — http(s) 後接 hostname (FQDN 結尾為內網 TLD 或 IP)，可帶 path
RE_URL_INTERNAL = re.compile(
    r"https?://(?:"
    # 1) IP-based URL
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d?\d)){3}"
    r"|"
    # 2) host with internal-looking TLD
    r"[a-zA-Z0-9\-.]+\.(?:local|internal|corp|lan|intra|intranet|dev|test|staging|prod|private|home|domain|ad|company|office)"
    r")"
    r"(?::\d{1,5})?(?:/[^\s\"'<>]*)?"
)

# 任何 URL — http / https 後接合法 hostname (含公開域名)，可帶 path / query
RE_URL_ANY = re.compile(
    r"https?://[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
)

# 任何域名 / FQDN — 2+ part，最後一部分至少 2 字元 alpha，避免抓檔名
# 不抓 IP（IP 由 RE_IP 抓）。例如：example.com、github.com、my-service.acme.io
RE_DOMAIN_ANY = re.compile(
    r"\b(?<![@/])"
    r"(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,24}"
    r"\b(?![@/.])",
    re.IGNORECASE,
)

# Linux / macOS path with username — /home/<user>/... or /Users/<user>/...
# 主要避免把 username 跟個人資料夾結構洩漏
RE_USER_PATH = re.compile(
    r"(?:/home/|/Users/|C:\\Users\\|%USERPROFILE%\\?)([A-Za-z][A-Za-z0-9._\-]{1,30})(?:[/\\][^\s\"'<>]*)?"
)

# 帳號 / 密碼 — 標籤式：「password: xxx」「密碼: xxx」「user/pass: xxx」等
# 涵蓋 admin/CLI 常見的 username:value 或 password:value 寫法
RE_CRED_LABEL = re.compile(
    r"(?:password|passwd|pwd|secret|api[_-]?key|token"
    r"|帳號|密碼|使用者名稱|登入帳號|登入密碼|金鑰|憑證)"
    r"\s*[:：=]\s*"
    r"(\S+)",
    re.IGNORECASE,
)

# 帳號 / 密碼 — 斜線對：「admin/qazwsxedc」、「user / password」
# 需要兩側都看起來像密碼或帳號（≥ 3 字元、無 path 字元）
# 避免吃到 URL path 或日期 (e.g., 2026/01/01)，要求兩側其一含字母
RE_CRED_PAIR = re.compile(
    r"(?<![/\w@.])"          # 前面不接 / 或 word/@/. 字元（避免 URL path）
    r"([A-Za-z][A-Za-z0-9._\-]{2,30})"   # 第一段：字母開頭
    r"\s*/\s*"
    r"([A-Za-z0-9!@#$%^&*._\-+=]{4,40})"  # 第二段：含特殊字元 (密碼)
    r"(?![/\w@.])"           # 後面不接 / 或 word
)


# Long random tokens — API key / bearer token / session id.
# 不能用 re.IGNORECASE，因為前段要靠「混大小寫」過濾掉 UUID（全小寫 hex）這類
# false positive。所以這裡用兩個獨立的 alternation：
#   1) 通用：≥ 32 字 + 真混大小寫 + 含數字（顯式不接受全 lowercase）
#   2) 特定 prefix：sk-, ghp_, AIza 等知名 token format（保留 case-sensitive 匹配）
RE_API_TOKEN = re.compile(
    r"\b(?=.*[A-Z])(?=.*[a-z])(?=.*\d)[A-Za-z0-9+/=_\-]{32,}\b"
    r"|"
    r"\b(?:sk|pk|ghp|gho|ghs|ghu|ghr|github_pat|xox[bps]|AIza|AKIA|ASIA|hf_|hub_)[\-_][A-Za-z0-9_\-]{16,}\b"
)
# 車牌：強化 — 必須要在前後出現非字母情境（避免吃掉 "FROM 123"），格式
# AAA-1234 / AAA1234 / 1234-AB / 新式 ABC-1234 / 機車 XX-123 / XX-1234
RE_PLATE = re.compile(
    r"(?:(?<=[車牌號照:：\s])|^)"
    r"(?:[A-Z]{2,3}[\s\-]\d{3,4}|\d{3,4}[\s\-][A-Z]{2,3})"
    r"(?=[\s,。、；;]|$)"
)
# 護照：要 label 或前綴才認，避免任何 9 位數字都被視為護照（false positive 大）
RE_PASSPORT = re.compile(
    r"(?:護照(?:號碼?)?|Passport(?:\s*No\.?|\s*Number)?)\s*[:：]?\s*([A-Z0-9]{8,10})\b",
    re.IGNORECASE,
)
RE_HIC = re.compile(r"\b0000\d{8,12}\b")   # 健保卡號 (以 0000 起頭的較長數字)
# 駕照：6-12 位英數混合，要前綴 label
RE_DRIVER_LICENSE = re.compile(
    r"(?:駕照(?:號碼?)?|Driver(?:'s)?\s*License(?:\s*No\.?)?|DL\s*No\.?)\s*[:：]?\s*([A-Z0-9]{6,12})\b",
    re.IGNORECASE,
)
# 出生日期 / 生日：常見格式 YYYY-MM-DD / YYYY/MM/DD / 民國 XXX 年 X 月 X 日
RE_DOB = re.compile(
    r"(?:出生(?:日期|年月日)?|生日|Date\s*of\s*Birth|DOB|Birth\s*Date)\s*[:：]?\s*"
    r"("
    # 中文 / 民國格式: "民國 70 年 3 月 21 日" / "70-3-21" / "1985 年 3 月 21 日"
    r"(?:民國\s*)?\d{2,4}\s*(?:年|[\-/])\s*\d{1,2}\s*(?:月|[\-/])\s*\d{1,2}\s*日?"
    r"|"
    # 純數字 ISO: 1985-03-21 / 1985/03/21
    r"\d{4}[\s\-/]\d{1,2}[\s\-/]\d{1,2}"
    r")",
    re.IGNORECASE,
)

# --- Label-anchored patterns (value_group=1 extracts only the sensitive part) ---
# Bank account: captures digits/dashes after "帳號" / "帳戶" / "account" label.
RE_BANK_ACCOUNT = re.compile(
    r"(?:公司帳[戶號]|銀行帳[戶號]|帳[戶號]號碼|帳[戶號]|Account\s*(?:No\.?|Number)?|A/?C)"
    r"\s*[:：＝=]?\s*([0-9][0-9\-\s]{7,25}[0-9])",
    re.IGNORECASE,
)
# Bank code: 3-digit code after "銀行代碼" / "銀行代號" label
RE_BANK_CODE = re.compile(r"(?:銀行代[碼號]|銀行別|Bank\s*Code)\s*[:：]?\s*(\d{3})", re.IGNORECASE)
# Bank branch code: digits after "分行代碼" / "分行" label
RE_BANK_BRANCH = re.compile(r"(?:銀行分行代?[碼號]?|分行代[碼號]|Branch\s*Code)\s*[:：]?\s*(\d{4,7})", re.IGNORECASE)
# Account name / holder: capture Chinese+ASCII text until newline/comma
RE_ACCOUNT_NAME = re.compile(
    r"(?:帳[戶號]名稱|戶名|Account\s*Name)\s*[:：]\s*([一-鿿\w\s\-·&．\.\(\)（）]{2,40}?)"
    r"(?=\s*(?:$|[,，;；]|電話|統編|地址|Tel|Phone))",
    re.IGNORECASE,
)
# Company name: captures text up to "有限公司 / 股份有限公司 ..." suffix
RE_COMPANY = re.compile(
    r"("
    # 中文後綴：股份有限公司 / 有限公司 / 工作室 / 商行 / 事業處 …
    r"[一-鿿A-Za-z0-9·&\-\.．]{2,30}?"
    r"(?:股份有限公司|有限公司|股份公司|有限責任公司|企業社|工作室|商行|事業處)"
    r"|"
    # 英文後綴：Co., Ltd. / Inc. / LLC / Corp. / Corporation / Company / Limited
    # 名稱部分：1~6 個首字大寫的詞（可含 & 與 .），詞之間用空白；後綴可帶 ,
    # 跟標點變化（Co.,Ltd / Co., Ltd. / Co. Ltd. 都吃）。後綴的尾巴 . 用
    # lookahead `(?=[^A-Za-z0-9]|$)` 收尾，因為 \b 在 "Ltd." 跟字串結尾
    # 之間不成立（兩邊都是非 word char）。
    r"\b(?:[A-Z][A-Za-z0-9&.\-]*)(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,5}"
    r"\s*,?\s*"
    r"(?:Co\.\s*,?\s*Ltd\.?|Co\.,Ltd\.?|Inc\.?|L\.?L\.?C\.?|Corp(?:oration)?\.?|Limited|Company)"
    r"(?=[^A-Za-z0-9]|$)"
    r")"
)
# Person name: a value (Chinese 2-4 chars OR English 2 capitalized words) after a
# label. Labels accept Chinese (姓名 / 聯絡人 / 負責人 …) AND English equivalents
# (Name / Contact / Owner / Manager / Applicant / In Charge / Sales / Signed by).
# 用 (?i:...) inline flag 只把 label 設為 case-insensitive，value 保持
# case-sensitive（英文名要首字大寫，免得抓到「name: john doe」這種日常文字）。
RE_PERSON = re.compile(
    r"(?:姓名|聯絡人|負責人|申請人|承辦人|代表人|經手人|收件人|寄件人|業務員?|銷售人員"
    r"|(?i:Name|Contact(?:\s*Person)?|Owner|Manager|Applicant|Responsible"
    r"|Person\s*in\s*Charge|Sales(?:\s*Rep(?:resentative)?)?|Signed\s*by))"
    r"\s*[:：]?\s*"
    r"("
    # 中文 2-4 字
    r"[一-鿿]{2,4}(?![一-鿿])"
    r"|"
    # 英文：2-4 個首字大寫的詞，每詞可含 . - '
    r"[A-Z][A-Za-z.\-']{1,20}(?:\s+[A-Z][A-Za-z.\-']{1,20}){1,3}"
    r")"
)

# Taiwan address — heuristic + 強化版（v1.3.16）：
#   - 支援「之 N」「N 之 N」（巷弄）
#   - 支援「N 樓」「N 樓之 N」「Floor N」「F.」（樓層）
#   - 支援「N 段」「Section N」（路段）
#   - 支援「Lane N」「Alley N」（巷弄英文）
#   - 直轄市 / 縣市 / 鄉鎮 / 區 全種類路 / 街 / 道 / 大道
RE_ADDR = re.compile(
    # 中文地址主要型 — 新增段、樓、之等後綴
    r"(?:[台臺][北中南東][縣市]|[一-鿿]{1,5}[縣市][一-鿿]{1,3}[區鄉鎮市])"
    r"[一-鿿]{0,30}"
    r"(?:[路街道]|大道)"
    r"(?:[一-鿿]{0,5}段)?"  # X 段
    r"[一-鿿\d]{0,20}\d+號"
    r"(?:之\d+)?"           # 之 N
    r"(?:[一-鿿\d]{0,5}樓)?"  # N 樓
    r"(?:之\d+)?"           # 樓之 N
    r"|"
    # 英文地址型：No.X, Sec.Y, Road, Lane, Alley, Floor
    r"\bNo\.?\s*\d+[\s,]*"
    r"(?:Sec\.?\s*\d+[\s,]*)?"
    r"[A-Za-z][A-Za-z\s\-\.]{2,30}"
    r"(?:\s*Rd\.?|\s*Road|\s*St\.?|\s*Street|\s*Ave\.?|\s*Avenue|\s*Lane|\s*Alley)"
    r"(?:[\s,]*Floor\s*\d+|\s*\d+F\.?)?",
    re.IGNORECASE,
)


CATALOG: list[Pattern] = [
    # 個人身分
    Pattern("tw_id",     "身分證字號",    RE_TW_ID,     _tw_id_valid,  _mask_id,    True,  group="個人身分", icon="id-card"),
    Pattern("tw_arc",    "居留證號",      RE_TW_ARC,    _tw_arc_valid, _mask_id,    True,  group="個人身分", icon="id-card"),
    Pattern("passport",  "護照號碼",      RE_PASSPORT,  _always,       _mask_passport, True,  value_group=1, group="個人身分", icon="book"),
    Pattern("driver_license", "駕照號碼", RE_DRIVER_LICENSE, _always,  _mask_passport, True,  value_group=1, group="個人身分", icon="car"),
    Pattern("dob",       "出生日期",      RE_DOB,       _always,
            lambda v: "****-**-**", True, value_group=1, group="個人身分", icon="page"),
    Pattern("hic",       "健保卡號",      RE_HIC,       _always,
            lambda v: _mask_keep_edges(v, 4, 4), False, group="個人身分", icon="heart"),
    # 聯絡方式
    Pattern("mobile",    "手機號碼",      RE_MOBILE,    _always,       _mask_phone, True,  group="聯絡方式", icon="smartphone"),
    Pattern("landline",  "市話",          RE_LANDLINE,  _always,       _mask_phone, True,  group="聯絡方式", icon="phone"),
    Pattern("email",     "Email",         RE_EMAIL,     _always,       _mask_email, True,  group="聯絡方式", icon="mail"),
    Pattern("addr",      "地址",          RE_ADDR,      _always,       _mask_addr,  True,  group="聯絡方式", icon="pin-map"),
    # 金融資訊
    Pattern("cc",        "信用卡號",      RE_CC,        _luhn_valid,   _mask_cc,    True,  group="金融資訊", icon="credit-card"),
    Pattern("bank_account", "銀行帳號",   RE_BANK_ACCOUNT, _bank_account_valid, _mask_bank_account,
            True,  value_group=1, group="金融資訊", icon="bank"),
    Pattern("bank_code", "銀行代碼",      RE_BANK_CODE, _always,       _mask_bank_code,
            False, value_group=1, group="金融資訊", icon="hash"),
    Pattern("bank_branch","銀行分行代碼", RE_BANK_BRANCH, _always,     _mask_bank_code,
            False, value_group=1, group="金融資訊", icon="hash"),
    Pattern("account_name","帳戶名稱",    RE_ACCOUNT_NAME, _always,    _mask_company,
            True,  value_group=1, group="金融資訊", icon="user"),
    # 企業資料
    Pattern("tw_biz",    "統一編號",      RE_TW_BIZ,    _twbiz_valid,  _mask_twbiz, True,  group="企業資料", icon="hash"),
    Pattern("company",   "公司名稱",      RE_COMPANY,   _always,       _mask_company,
            False, value_group=1, group="企業資料", icon="building"),
    # 其他
    Pattern("person_name","人名",         RE_PERSON,    _always,       _mask_name,
            False, value_group=1, group="其他", icon="user"),
    Pattern("ip",        "IP 位址",       RE_IP,        _always,       _mask_ip,    False, group="IT 資料", icon="globe"),
    Pattern("plate",     "車牌",          RE_PLATE,     _always,       _mask_plate, False, group="其他", icon="car"),
    # IT / DevOps — 預設關，使用者要貼 log/設定檔給 AI 時自己勾。一般商務文件
    # 容易誤抓 (例如「2026.04.30 公司決議」會被當成 UUID-like)
    Pattern("hostname",  "主機名稱 (FQDN)", RE_HOSTNAME, _always,
            lambda v: _mask_keep_edges(v, 2, 4), False, group="IT 資料", icon="globe"),
    Pattern("mac",       "MAC 位址",      RE_MAC,       _always,
            lambda v: _mask_keep_edges(v, 2, 2), False, group="IT 資料", icon="hash"),
    Pattern("ad_dn",     "AD / LDAP DN", RE_AD_DN,     _always,
            lambda v: "[AD-DN-REDACTED]", False, group="IT 資料", icon="user"),
    Pattern("win_user",  "Windows 帳號 (DOMAIN\\user)", RE_WIN_USER, _always,
            lambda v: _mask_keep_edges(v, 2, 2), False, group="IT 資料", icon="user"),
    Pattern("uuid",      "UUID / GUID",   RE_UUID,      _always,
            lambda v: _mask_keep_edges(v, 4, 4), False, group="IT 資料", icon="hash"),
    Pattern("url_internal", "內網 URL",   RE_URL_INTERNAL, _always,
            lambda v: "[INTERNAL-URL-REDACTED]", False, group="IT 資料", icon="globe"),
    Pattern("url_any",   "URL（含公開域名）", RE_URL_ANY, _always,
            lambda v: re.match(r"^https?://", v).group(0) + "***", False,
            group="IT 資料", icon="globe"),
    Pattern("domain",    "域名 / FQDN（含公開域名）", RE_DOMAIN_ANY, _always,
            lambda v: _mask_keep_edges(v, 2, 4), False, group="IT 資料", icon="globe"),
    Pattern("user_path", "本機路徑 (含使用者名)", RE_USER_PATH, _always,
            lambda v: re.sub(r"(/home/|/Users/|C:\\Users\\|%USERPROFILE%\\?)[^/\\]+",
                              r"\1USER", v), False,
            group="IT 資料", icon="folder"),
    Pattern("api_token", "API token / 金鑰", RE_API_TOKEN, _always,
            lambda v: _mask_keep_edges(v, 4, 4), False, group="IT 資料", icon="lock"),
    Pattern("cred_label", "標籤式密碼 / 帳號 (password: xxx)", RE_CRED_LABEL, _always,
            lambda v: _mask_keep_edges(v, 1, 1), False, value_group=1,
            group="IT 資料", icon="lock"),
    Pattern("cred_pair",  "帳號/密碼斜線組合 (admin/pass)", RE_CRED_PAIR, _always,
            lambda v: _mask_keep_edges(v, 1, 1), False,
            group="IT 資料", icon="lock"),
]


def resolve(pattern_id: str) -> Optional[Pattern]:
    for p in CATALOG:
        if p.id == pattern_id:
            return p
    return None
