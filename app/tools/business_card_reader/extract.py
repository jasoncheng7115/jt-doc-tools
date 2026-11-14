"""名片辨識：OCR 文字行 → 聯絡人欄位（規則擷取，不需要 LLM）。

規格第 3、4 節：電話、分機、Email、網址、統編等優先用規則；姓名、公司、部門、職稱考慮標籤、相鄰文字與版面，
**不把最大字一律當姓名**。統編只收名片上明列的（不依公司名稱猜）。缺值留空，不補猜。
每個欄位都帶來源（哪一行、框位、方法）與狀態：`auto`（辨識值）、`check`（需確認，附原因）。

欄位清單（`FIELD_KEYS`）：
人員 name / name_en / title / department；組織 company / company_en / vat；
聯絡 mobile / phone（可帶分機 ext）/ fax / email / url / address（可帶郵遞區號 zip）/ im（通訊帳號：LINE、QQ、WeChat…）。
多值（電話、Email、地址…）就是同一個 key 多筆，各自保留原標籤（`label`）與原值（`raw`）。
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Iterable, Optional

FIELD_KEYS = ("name", "name_en", "title", "department", "company", "company_en", "vat",
              "mobile", "phone", "fax", "email", "url", "address", "im", "note")
MULTI_KEYS = {"mobile", "phone", "fax", "email", "url", "address", "im"}
#: 信心低於這個（0..100）標需確認
LOW_CONF = 55.0

# --------------------------------------------------------------------------- 關鍵字

TITLE_WORDS = (
    "董事長", "副董事長", "董事", "總裁", "執行長", "總經理", "副總經理", "副總", "協理", "經理", "副理", "襄理", "處長", "副處長",
    "部長", "課長", "組長", "科長", "股長", "主任", "副主任", "主管", "總監", "監事", "顧問", "專員", "專案經理", "業務代表",
    "業務", "工程師", "設計師", "律師", "會計師", "醫師", "護理師", "營養師", "藥師", "教授", "副教授", "助理教授", "講師",
    "研究員", "助理", "秘書", "店長", "社長", "院長", "局長", "署長", "所長", "站長", "隊長", "小隊長", "代表", "負責人",
    "總編輯", "編輯", "記者", "主編", "創辦人", "合夥人", "執行董事", "特助", "幹事", "理事長", "理事", "會長", "副會長",
    "技術員", "技師", "督導", "專案管理師", "業務專員", "採購", "行銷",
)
TITLE_EN = re.compile(
    r"\b(President|Vice President|VP|CEO|CTO|CFO|COO|CIO|Chairman|Chairwoman|Director|Manager|Engineer|Supervisor|"
    r"Specialist|Consultant|Officer|Assistant|Representative|Sales|Designer|Founder|Co-?founder|Partner|Owner|"
    r"Executive|Head|Lead|Leader|Architect|Analyst|Administrator|Coordinator|Professor|Lecturer|Researcher|Editor|"
    r"Reporter|Secretary|Attorney|Lawyer|Accountant|Doctor|Nurse|Technician|Agent|Advisor|Adviser|Associate|"
    r"Principal|Chief|General Manager|Managing Director)\b", re.I)
DEPT_RE = re.compile(r"([一-鿿]{1,10}(?:部|處|課|組|室|中心|事業群|事業部|科|股|辦公室|服務處|分處|營業所))$")
#: 職稱前面常接的職務 / 單位詞（「採購副理」「業務經理」「行銷專員」）——前面那段是這些時不當姓名
ROLE_HEADS = ("採購", "業務", "行銷", "財務", "會計", "人資", "行政", "研發", "技術", "工程", "品保", "品管", "生產", "製造",
              "企劃", "客服", "資訊", "法務", "總務", "營業", "專案", "產品", "銷售", "服務", "設計", "管理", "國際", "海外")
#: 常見姓氏（台灣前幾百大＋常見複姓）。職稱拆出來的「姓名」第一個字不是姓氏時，多半是職稱的一部分
#: （「系統工程師」被拆成「系統」＋「工程師」）。只拿來判斷「像不像姓名」，不用來猜任何人的資料。
SURNAMES = set(
    "陳林黃張李王吳劉蔡楊許鄭謝郭洪曾邱廖賴周徐蘇葉莊呂江何蕭羅高潘簡朱鍾游彭詹胡施沈余盧梁趙顏柯翁魏孫戴范方宋"
    "鄧杜傅侯曹薛丁卓阮馬董温溫唐藍石蔣古紀姚連馮歐程湯黎田康姜白汪鄒尤巫鐘黃涂龔嚴韓袁金童陸夏柳凃邵錢伍倪溫"
    "于譚駱熊任甘秦顧毛章史官萬俞雷粘饒張闕凌崔尹孔辛武辜陶易段龍韋池喬聶鄞褚邢姬牛關俞畢成麥商祝殷包左塗蕭華"
    "孟賀樊房齊景桂苗車殷艾霍葛單裴藺覃岳應卜文常向郝寇耿管盛刁鮑尚喻衛鄔皮曲奚滕甯儲靳房欒戚舒樂闞蒲尉樓計封"
    "邊宮屠焦烏祁靖宗麻全穆亓扶雲閻冉卞房談鞠富谷時冷米花鄺賈岑")
COMPOUND_SURNAMES = ("歐陽", "司馬", "上官", "諸葛", "東方", "皇甫", "司徒", "張簡", "范姜", "周黃", "公孫", "長孫", "慕容", "令狐")


def looks_like_name(v: str) -> bool:
    v = v.replace(" ", "")
    return bool(v) and (v[0] in SURNAMES or v[:2] in COMPOUND_SURNAMES)


def fix_company(c: str) -> str:
    """公司名稱裡 OCR 常認錯的固定字樣：「股份」認成「服竹」「股分」、「有限」認成「有阻」、「公司」認成「公可」；
    名稱前面黏著 Logo 被認成的一兩個英數字（「0k大力…」）也拿掉。只動公司型態與開頭的雜字，名稱本身不猜。"""
    out = re.sub(r"[服股殷般胶投]\s*[份竹分伤彷仿伢傷](?=有限公司$)", "股份", c)
    out = re.sub(r"有[限阻很狠恨]公[司可]$", "有限公司", out)
    out = re.sub(r"公可$", "公司", out)
    # 只拿掉帶小寫字母的雜字（Logo 認錯常是「0k」這種）；「3M」「IBM」「HP」這種大寫品牌留著
    m = re.match(r"^[A-Za-z0-9]{1,3}(?=[一-鿿]{2,})", out)
    if m and re.search(r"[a-z]", m.group(0)) and COMPANY_SUFFIX.search(out[m.end():]):
        out = out[m.end():]
    return out


def _split_name_title(compact: str, titles_by_len) -> Optional[tuple[str, str]]:
    """「王小明業務經理」→（業務經理, 王小明）；「資訊經理王小明」→（資訊經理, 王小明）。姓名那段要 2～4 個漢字而且像姓名。"""
    def ok(n):
        return 2 <= len(n) <= 4 and re.fullmatch(r"[一-鿿]+", n) is not None and looks_like_name(n)
    for tw in titles_by_len:
        if compact.endswith(tw) and len(compact) > len(tw):
            head = compact[: -len(tw)]
            rh = next((r for r in ROLE_HEADS if head.endswith(r) and len(head) > len(r)), "")
            name = head[: len(head) - len(rh)]
            if ok(name):
                return rh + tw, name
        for rh in ("",) + ROLE_HEADS:
            if compact.startswith(rh + tw) and len(compact) > len(rh + tw):
                name = compact[len(rh + tw):]
                if ok(name):
                    return rh + tw, name
    return None


DEPT_EN = re.compile(r"\b(Dept\.?|Department|Division|Div\.|Group|Office|Center|Centre|Team|Unit|Section)\b", re.I)
COMPANY_SUFFIX = re.compile(
    r"(股份有限公司|有限公司|公司|企業社|企業|集團|銀行|醫院|診所|協會|學會|基金會|工會|商會|聯合會|事務所|研究所|大學|學院|"
    r"學校|政府|局|署|處|部|院|館|中心|飯店|酒店|旅館|工廠|商行|商店|門市|直營店|分行|分公司|營業所|工作室|出版社|報社|電台|電視台)")
COMPANY_EN = re.compile(
    r"\b(Co\.?,?\s*Ltd\.?|Company|Corp\.?|Corporation|Inc\.?|Incorporated|Limited|Ltd\.?|LLC|Group|Bank|Hotel|"
    r"University|College|Institute|Association|Foundation|Hospital|Studio|Technologies|Technology|Systems|"
    r"Enterprise|Enterprises|Industries|Industrial|Holdings|International|Agency|Ministry|Bureau|Government|Council|"
    r"Magazine|Media|Press|Publishing|Restaurant|Clinic|Laboratory|Laboratories|Partners|Consulting|Solutions|Services)\b", re.I)

# 電話類標籤（放在號碼前面）
LABELS = [
    ("fax", r"(?:傳真|傳\s*真|FAX|Fax|fax|F\s*a\s*x|F)\b"),
    ("mobile", r"(?:手機|行動電話|行動|Mobile(?:\s*Phone)?|Mob\.?|Cell(?:\s*Phone)?|M|Ｍ)\b"),
    ("phone", r"(?:電話|電\s*話|總機|直撥|直線|專線|市話|客服|服務專線|預約專線|免付費服務專線|免付費|"
              r"Tel\.?|TEL|T\s*e\s*[l|]|Phone|Office|Direct|Hotline|T|Ｔ)\b"),
]
IM_RE = re.compile(r"\b(LINE|Line|line|QQ|Q\s*Q|WeChat|微信|WhatsApp|Skype|Telegram|Facebook|FB|IG|Instagram|Twitter|X)"
                   r"\s*(?:ID)?\s*[:：]\s*(@?[\w.\-]{2,40})")
EXT_RE = re.compile(r"^\s*(?:分機|分\s*機|轉|ext\.?|Ext\.?|EXT\.?|x|X|#|＃)\s*[:：.]?\s*(\d{1,6})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+\s*@\s*[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"(?:(?:https?://|www\.)[^\s，,;；]+|\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\."
                    r"(?:com|net|org|gov|edu|io|co|tw|biz|info|app|dev|shop|store|asia)(?:\.[a-z]{2})?(?:/[^\s，,;；]*)?)", re.I)
#: 不會是姓名的詞（號碼前面的欄位標籤）
NOT_NAME_WORDS = re.compile(r"郵件|信箱|電郵|網址|網站|官網|地址|統編|編號|聯絡|營業|服務|訂位|預約|夜間|日間|門市|總部|免費|顧客|客戶|洽詢|訂購|諮詢")

#: 統編的標籤。**OCR 常認錯標籤本身**（實測：「一」認成破折號 / 注音ㄧ、「統」認成形近字、冒號認成「·」），
#: 標籤一對不上，8 碼數字就被當成電話 → 標籤放寬：「統」＋（任一個字）＋「編」，或「編號」前面一兩個任意字；
#: 分隔符號收冒號、句點、間隔號、破折號。
VAT_LABEL = (r"(?:公司統編|統\s*[^\s\d:：]?\s*[編编](?:\s*[號号])?|[^\s\d:：]{1,2}\s*[編编]\s*[號号]|"
             r"VAT(?:\s*(?:No|ID|Reg(?:istration)?(?:\s*No)?))?\.?|Tax\s*ID(?:\s*No)?\.?|GUI\s*No\.?|U\.?B\.?N\.?|"
             r"Unified\s+Business\s+No\.?|Business\s+(?:ID|No)\.?)")
VAT_SEP = r"\s*[:：.·・•‧;；,，\-－—/／]*\s*"
VAT_RE = re.compile(VAT_LABEL + VAT_SEP + r"([0-9OoIl|]{8})(?!\d)", re.I)
VAT_LABEL_ONLY = re.compile(r"^\s*" + VAT_LABEL + VAT_SEP + r"$", re.I)
_VAT_BEFORE = re.compile(VAT_LABEL + VAT_SEP + r"$", re.I)
ADDR_LABEL = re.compile(r"^\s*(?:地址|住址|公司地址|通訊地址|地\s*址|會址|園址|廠址|門市地址|Add(?:ress)?\.?|ADD|Addr\.?)\s*[:：.]?\s*", re.I)
TW_ADDR = re.compile(r"(?:台|臺)?(?:北|新北|桃園|台中|臺中|台南|臺南|高雄|基隆|新竹|苗栗|彰化|南投|雲林|嘉義|屏東|宜蘭|花蓮|台東|臺東|澎湖|金門|連江)"
                     r"(?:市|縣).{0,40}?(?:路|街|大道|巷|弄|號|樓|村|里)")
#: 英文地址字樣。**縮寫帶句點的不可以接 `\b`**：句點後面接空白時不構成字界，`No.`、`Rd.` 會永遠比對不到
#: （原本就是這樣寫，英文地址只靠 Taiwan / R.O.C. 撐著）→ 改成「後面不是英文字母」。
EN_ADDR = re.compile(r"\b(?:No\.|Rd\.|St\.|Sec\.|Ave\.|Ln\.|Aly\.|Dist\.|Blvd\.|Bldg\.|Fl\.|\d+F\.?|"
                     r"Road|Street|Section|Avenue|Lane|Alley|Floor|Boulevard|Building|District|Township|County|City|"
                     r"Village|Industrial Park|Science Park|Taiwan|R\.?\s?O\.?\s?C\.?|Taipei|Kaohsiung|Taichung|"
                     r"Tainan|Hsinchu|Keelung|Taoyuan)(?![A-Za-z])", re.I)
#: 英文地址第一行要有門牌的樣子（「No. 12」「9F」「2-1」「Sec. 3」「Lane 130」）—— 單純有個數字不算
#: （實測：機關英文名稱裡 OCR 把字母認成數字，被當成地址開頭）
#: OCR 常把 1 認成 l / I / |、0 認成 O —— 門牌數字容許這幾個字（至少要有一個真的數字）
HOUSE_NO = re.compile(r"(?:\bNo\.?\s*[\dlI|]|\b\d[\dOo]*\s*F\b|\b\d+-\d+|\bSec\.?\s*\d|\bLane\s*\d|\bAlley\s*\d|"
                      r"^\s*\d|\b\d{3,6},?\s*Taiwan)", re.I)
ZIP_RE = re.compile(r"^\s*(\d{3}(?:\d{2,3})?)(?=\s*\D)")

# 號碼內常見的 OCR 誤認（只在「看起來是號碼」的片段裡換）
_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1", "i": "1", "S": "5",
                            "B": "8", "Z": "2", "（": "(", "）": ")", "－": "-", "—": "-", "–": "-", "＋": "+",
                            "　": " "})
_NUM_RUN = re.compile(r"[+(]?[0-9OoDlI|iSBZ（）()＋+\-－—–\s.]{7,}[0-9OoDlI|iSBZ]")


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _cw(ch: str) -> float:
    """估字寬（相對值）：中日文全寬、英數約一半、空白更窄。只用來把一行的框切到其中一段。"""
    if ch.isspace():
        return 0.35
    return 1.0 if ord(ch) >= 0x2E80 else 0.56


def _part_box(line: dict, part: str) -> Optional[list]:
    """一行裡其中一段（例如地址後面接著的統編）的大概位置：依字寬比例切行的框。找不到就回 None（用整行）。"""
    text, box = line.get("text") or "", line.get("box")
    part = (part or "").strip()
    if not part or not box or part == text.strip():
        return None
    i = text.find(part)
    if i < 0:
        return None
    total = sum(_cw(c) for c in text) or 1.0
    a = sum(_cw(c) for c in text[:i]) / total
    b = sum(_cw(c) for c in text[:i + len(part)]) / total
    x, y, w, h = box
    pad = h * 0.15
    x0 = max(x, x + w * a - pad)
    x1 = min(x + w, x + w * b + pad)
    return [round(x0, 1), y, round(x1 - x0, 1), h]


def _field(key: str, value: str, *, line: Optional[dict] = None, method: str = "ocr", status: str = "auto",
           reason: str = "", part: str = "", verified: bool = False, **extra) -> dict:
    """`part`：這個值在那一行裡的原文（框只框那一段）；`verified`：值本身已經有檢查（統編檢查碼過了），
    行的辨識信心低也不再標需確認 —— 檢查碼比 OCR 的信心分數可靠。"""
    f = {"id": _id(), "key": key, "value": value.strip(), "status": status, "reason": reason,
         "source": {"method": method}}
    if line is not None:
        f["source"].update({"line": line.get("index"), "box": _part_box(line, part) or line.get("box"),
                            "conf": line.get("conf")})
        if status == "auto" and not verified and float(line.get("conf") or 100) < LOW_CONF:
            f["status"], f["reason"] = "check", "辨識信心低，請對照原圖"
    f.update({k: v for k, v in extra.items() if v not in (None, "")})
    return f


# --------------------------------------------------------------------------- 號碼

def normalize_phone(raw: str) -> str:
    """比對用的正規化：只留數字與開頭的 +；國碼沒印出來就不加（規格：不擅自轉 +886）。"""
    s = raw.translate(_DIGIT_FIX)
    plus = s.strip().startswith("+")
    digits = re.sub(r"\D", "", s)
    return ("+" if plus else "") + digits


def phone_complete(norm: str) -> bool:
    """台灣號碼長度合理嗎（遮掉 / 少幾碼的標需確認）。國際號碼只看長度 8～15。"""
    d = norm.lstrip("+")
    if norm.startswith("+886") or d.startswith("886"):
        rest = d[3:].lstrip("0")
        return 8 <= len(rest) <= 9
    if d.startswith("09"):
        return len(d) == 10
    if d.startswith("0800") or d.startswith("0809"):
        return len(d) == 10
    if d.startswith("0"):
        return 9 <= len(d) <= 10
    if norm.startswith("+"):
        return 8 <= len(d) <= 15
    return 7 <= len(d) <= 8          # 沒有區碼的市話（名片上偶爾只印 8 碼）


def _label_before(text: str, pos: int) -> tuple[Optional[str], str]:
    """號碼前面（同一行、最近的）是什麼標籤。回（種類, 原標籤）。"""
    before = text[max(0, pos - 18):pos]
    best = None
    for kind, pat in LABELS:
        for m in re.finditer(pat + r"\s*[:：.]?\s*$|" + pat + r"\s*[:：]", before):
            # 要「貼著」號碼（中間只剩空白、冒號、括號）
            tail = before[m.end():]
            if tail.strip(" :：.()（）") == "" and (best is None or m.start() > best[2]):
                best = (kind, m.group(0).strip(" :：."), m.start())
    if best:
        return best[0], best[1]
    return None, ""


def find_phones(text: str) -> list[dict]:
    """一行裡所有的號碼：`{kind, raw, norm, ext, label, complete}`。"""
    out = []
    for m in _NUM_RUN.finditer(text):
        raw = m.group(0).strip(" .-")
        # 「電話:(02)…」的左括號有時跟標籤黏在一起、沒被收進來：括號不成對就補回（實拍：顯示成「02)2345-0002」）
        if raw.count(")") > raw.count("(") and text[max(0, m.start() - 1):m.start()] in ("(", "（"):
            raw = "(" + raw
        norm = normalize_phone(raw)
        digits = norm.lstrip("+")
        if len(digits) < 7 or len(digits) > 15:
            continue
        # 排除：統編（8 碼、前面有統編標籤）、郵遞區號＋地址、日期
        if _VAT_BEFORE.search(text[max(0, m.start() - 24):m.start()]):
            continue
        if re.match(r"^\d{4}[./]\d{1,2}[./]\d{1,2}$", raw):
            continue
        kind, label = _label_before(text, m.start())
        ext = ""
        em = EXT_RE.match(text[m.end():])
        if em:
            ext = em.group(1)
        if kind is None:
            kind = "mobile" if digits.startswith("09") or digits.startswith("8869") else "phone"
        elif kind == "phone" and (digits.startswith("09") or digits.startswith("8869")):
            kind = "mobile"
        out.append({"kind": kind, "raw": raw, "norm": norm, "ext": ext, "label": label,
                    "complete": phone_complete(norm), "span": (m.start(), m.end() + (em.end() if em else 0))})
    return out


# --------------------------------------------------------------------------- 統編

def vat_checksum_ok(no: str) -> bool:
    """營利事業統一編號檢查碼（財政部 2023 年起除數 5，相容舊的 10）。"""
    if not re.fullmatch(r"\d{8}", no or ""):
        return False
    w = [1, 2, 1, 2, 1, 2, 4, 1]
    s = 0
    for d, k in zip(no, w):
        p = int(d) * k
        s += p // 10 + p % 10
    if s % 5 == 0:
        return True
    return no[6] == "7" and (s + 1) % 5 == 0


# --------------------------------------------------------------------------- 主流程

def _clean_url(u: str) -> str:
    u = re.sub(r"^http[:;]?[l|/\\]{1,2}", "http://", u, flags=re.I)
    u = re.sub(r"^https[:;]?[l|/\\]{1,2}", "https://", u, flags=re.I)
    u = re.sub(r"^[l|I1]{1,2}(?=www\.)", "", u, flags=re.I)        # 「http://」被認成「ll」後只剩這兩個字
    u = re.sub(r"^www\.", "www.", u, flags=re.I)                     # 「wWW.」「WWW.」→ 小寫（網域不分大小寫）
    return u.rstrip(".,;:)")


#: 網址結尾（OCR 會把 .tw 認成 .fw / .w / .tW）
_TLD = r"(?:com|net|org|gov|edu|idv|io|co|tw|biz|info|app|dev|shop|store|asia|uk|jp|hk|sg|me)"
_WWW_START = re.compile(r"^(?:(ht[it]ps?)\s*[:;]?\s*[/l|\\]{0,2})?([wv]{3,4})(?=[.,\s]|[a-z0-9]|$)", re.I)
_SCHEME_START = re.compile(r"^(ht[it]ps?)\s*[:;]?\s*[/l|\\]{1,2}", re.I)
_LABEL_EMAIL = re.compile(r"(?:E\s*-?\s*mai[l1I]{1,2}|電子郵件|電郵|郵件|信箱)", re.I)
_MAIL_HOSTS = ("gmail.com", "yahoo.com", "yahoo.com.tw", "hotmail.com", "outlook.com", "msn.com", "icloud.com")


def _host_fix(h: str) -> str:
    """網域：小寫、去掉頭尾的雜字、修常見的 OCR 錯（.com.fw / .com.w → .com.tw，「,」→「.」）。"""
    h = h.strip(" .,|;:)(").replace(",", ".").lower()
    h = re.sub(r"\s*\.\s*", ".", h)
    h = re.sub(r"\.(?:fw|w|lw|iw)$", ".tw", h)
    h = re.sub(r"\.com\.(?:fw|w)\b", ".com.tw", h)
    return h


def find_urls(text: str) -> list[tuple[str, str]]:
    """一行裡的網址：回 [(網址, 原文那一段)]。

    OCR 常見的壞法都要接得回來（真實名片實測）：「www.xxx com.tw」中間多空白、「wwwxxx.com.tw」少點、
    「wvww.」、「http:l」「htip:l」、結尾 .com.fw；「@gmail.com」這種 Email 的後半段不是網址。
    """
    out: list[tuple[str, str]] = []
    toks = list(re.finditer(r"\S+", text))
    i = 0
    while i < len(toks):
        t = toks[i].group(0)
        start = toks[i].start()
        prev = text[max(0, start - 2):start].strip()
        if t.startswith("@") or prev.endswith("@"):
            i += 1
            continue
        # 標籤黏在前面（「Website:wvww.…」「:http:www…」）：從冒號 / 豎線後面找網址的開頭
        m = sm = None
        for k in [0] + [n + 1 for n, ch in enumerate(t) if ch in ":：|(（[" and n + 1 < len(t)]:
            m, sm = _WWW_START.match(t[k:]), _SCHEME_START.match(t[k:])
            if m or sm:
                if k:
                    t, start = t[k:], start + k
                break
        if m or (sm and re.search(r"[a-z0-9]\.[a-z]", t[sm.end():], re.I)):
            scheme = (m.group(1) if m else sm.group(1)) or ""
            body = t[(m.end() if m else sm.end()):]
            j = i
            # www 後面被空白拆開的片段接回來，直到網域結尾（最多再接 3 段）
            def _ascii(k):                  # 後面黏著中文（「example.com.w某某店」）：只取前面的英數
                return re.match(r"[\x21-\x7e]*", toks[k].group(0)).group(0)
            while j + 1 < len(toks) and j - i < 3 \
                    and re.fullmatch(r"[.,]?[a-z0-9\-]+(?:[.,][a-z0-9\-]+)*[.,|]?", _ascii(j + 1), re.I) \
                    and re.search(r"[a-z]", _ascii(j + 1), re.I):
                done = re.search(r"\." + _TLD + r"(?:\.[a-z]{1,3})?(?:/\S*)?$", body.rstrip(".,|"), re.I)
                # 已經到網域結尾了：只再接國別碼（「… com tW」）
                if done and not (re.fullmatch(r"[.,]?(?:tw|uk|jp|hk|sg|cn|au|nz)[.,|]?", _ascii(j + 1), re.I)
                                 and re.search(r"\.(?:com|net|org|gov|edu|co|idv)$", body.rstrip(".,|"), re.I)):
                    break
                j += 1
                body = body.rstrip(".,") + "." + _ascii(j).lstrip(".,")
            host, _, path = body.lstrip(".,").partition("/")
            host = _host_fix(host)
            if m:
                host = "www." + host if host else ""
            if host and host not in ("www.",):
                url = host + ("/" + path.rstrip(".,;:)|") if path else "")
                if scheme:
                    url = ("https://" if scheme.lower().endswith("s") else "http://") + url
                out.append((url, text[start:toks[j].end()]))
            i = j + 1
            continue
        dm = re.match(r"^[(\[]?([a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\." + _TLD + r"(?:\.[a-z]{1,3})?)(/\S*)?", t, re.I)
        if dm:
            host = _host_fix(dm.group(1))
            labels = host.split(".")
            generic = re.fullmatch(r"(?:com|net|org|gov|edu|idv)(?:\.[a-z]{2})?|[a-z]{2}", host)
            mailish = any(host.endswith(mh) and host != mh for mh in _MAIL_HOSTS) or host in _MAIL_HOSTS
            if not generic and not mailish and len(labels) >= 2 and not re.fullmatch(r"[\d.]+", host):
                out.append((host + (dm.group(2) or "").rstrip(".,;:)|"), t))
        i += 1
    return out


def _cjk_ratio(s: str) -> float:
    s2 = re.sub(r"\s", "", s)
    if not s2:
        return 0.0
    return sum(1 for ch in s2 if "一" <= ch <= "鿿") / len(s2)


def _strip_label(s: str) -> str:
    return re.sub(r"^\s*[\w一-鿿]{1,6}\s*[:：]\s*", "", s)


def extract_fields(lines: Iterable[dict], qr_texts: Iterable[str] = ()) -> list[dict]:
    """OCR 行（`{"text","box","conf","height"}`）→ 欄位清單（一位聯絡人）。行的 `index` 會自動補上。"""
    lines = [dict(ln, index=i) for i, ln in enumerate(lines)]
    # Email 被拆成兩行（「…kevin.sample」下一行「@example.de」）：接回同一行
    for a, b in zip(lines, lines[1:]):
        ta, tb = (a.get("text") or "").rstrip(), (b.get("text") or "").strip()
        # 前一行最後一段要像 Email 的帳號（英文字開頭；電話號碼不算）
        if re.fullmatch(r"@[\w\-]+(?:\.[\w\-]+)+", tb) and re.search(r"(?:^|[\s:：])(?:e-?mail)?[A-Za-z][\w.+\-]+$", ta, re.I) \
                and "@" not in ta:
            a["text"], b["text"] = ta + tb, ""
    fields: list[dict] = []
    used: set[int] = set()

    vat_label_line: Optional[int] = None
    left_names: list[tuple[dict, str]] = []
    hs = sorted(float(ln.get("height") or 0) for ln in lines)
    med_h = hs[len(hs) // 2] if hs else 0.0                 # 姓名的字通常不比整張卡的一般字小

    def add(key, value, line=None, **kw):
        if value and value.strip():
            if key == "vat" and any(f["key"] == "vat" and f["value"] == value.strip() for f in fields):
                return                                  # 中英兩面各印一次統編：只留一筆
            fields.append(_field(key, value, line=line, **kw))

    for ln in lines:
        t = (ln.get("text") or "").strip()
        if not t:
            continue
        rest = t
        # Email（可能多個；OCR 常在 @ 兩邊多空白）
        for m in EMAIL_RE.finditer(t):
            v = re.sub(r"\s+", "", m.group(0)).lstrip(".-_")
            # 標籤跟 Email 黏在一起（「E-Mailkevin@…」）：去掉標籤（去掉之後 @ 前面還有字才算）
            lab = re.match(r"^(?:e-?mai[l1I]|email)[:：]?(?=[\w.+\-]+@)", v, re.I)
            if lab and not re.match(r"(?:e-?mai[l1I]|email)[:：]?@", v, re.I):
                v = v[lab.end():]
            add("email", v, ln, norm=v.lower(), part=m.group(0))
            rest = rest.replace(m.group(0), " ")
            used.add(ln["index"])
        # 「E-mail:」標籤後面沒有 @（OCR 把 @ 認成別的字）：留著但標需確認
        if "email" not in {f["key"] for f in fields if f["source"].get("line") == ln["index"]}:
            em = re.search(r"(?:E\s*-?\s*mai[l1I]{1,2}|電子郵件|電郵|郵件|信箱)\s*[:：/]?\s*(@?[^\s@]+\.(?:com|net|org|gov|edu|tw|co|io|de|jp|uk)(?:\.[a-z]{2})?)\b", rest, re.I)
            if em:
                add("email", em.group(1), ln, status="check", reason="看起來是 Email 但沒有 @，請對照原圖")
                rest = rest.replace(em.group(0), " ")
                used.add(ln["index"])
        # 統編（標籤在上一行、號碼自己一行：上一行只有標籤）
        if vat_label_line is not None and ln["index"] == vat_label_line + 1:
            bm = re.match(r"^\s*([0-9OoIl|]{8})(?!\d)", rest)
            if bm:
                no = bm.group(1).translate(_DIGIT_FIX)
                ok = vat_checksum_ok(no)
                add("vat", no, ln, status="auto" if ok else "check", reason="" if ok else "統編檢查碼不符，請對照原圖",
                    part=bm.group(1), verified=ok)
                rest = rest.replace(bm.group(0), " ", 1)
                used.add(ln["index"])
        vat_label_line = ln["index"] if VAT_LABEL_ONLY.match(rest) else None
        if vat_label_line is not None:
            used.add(ln["index"])
        vm = VAT_RE.search(rest)
        if vm:
            no = vm.group(1).translate(_DIGIT_FIX)
            ok = vat_checksum_ok(no)
            add("vat", no, ln, status="auto" if ok else "check", reason="" if ok else "統編檢查碼不符，請對照原圖",
                part=vm.group(0), verified=ok)
            rest = rest.replace(vm.group(0), " ")
            used.add(ln["index"])
        # 通訊帳號
        for m in IM_RE.finditer(rest):
            add("im", m.group(2), ln, label=m.group(1), part=m.group(0))
            rest = rest.replace(m.group(0), " ")
            used.add(ln["index"])
        # 電話類
        for ph in find_phones(rest):
            # 沒有電話標籤、8 碼連在一起、不是 0 開頭：台灣名片上的電話一定帶區碼或分隔號，這是統編
            # （標籤被 OCR 認到完全認不出來時的最後一道）。檢查碼不符就標需確認，讓人看原圖決定。
            if not ph["label"] and not ph["ext"] and re.fullmatch(r"[1-9OoIl|][0-9OoIl|]{7}", ph["raw"]) \
                    and not ph["norm"].startswith("0"):
                no = ph["norm"]
                ok = vat_checksum_ok(no)
                add("vat", no, ln, status="auto" if ok else "check",
                    reason="" if ok else "8 碼數字、沒有標籤，可能是統編也可能是電話，請對照原圖",
                    part=ph["raw"], verified=ok)
                used.add(ln["index"])
                continue
            st, rs = ("auto", "") if ph["complete"] else ("check", "號碼位數不對（可能被遮住或辨識漏字），請對照原圖")
            raw = rest[ph["span"][0]:ph["span"][1]].strip()
            add(ph["kind"], ph["raw"], ln, norm=ph["norm"], ext=ph["ext"], label=ph["label"], raw=raw,
                status=st, reason=rs, part=raw)
            used.add(ln["index"])
        # 網址（不是 Email 的一部分）。Email 標籤那一行沒有 www / http 的「xxx.com」是認錯的 Email，不當網址
        email_line = bool(_LABEL_EMAIL.search(t))
        for u, raw in find_urls(rest):
            if email_line and not re.match(r"(?:https?://)?www\.", u):
                continue
            add("url", u, ln, norm=u.lower(), part=raw)
            used.add(ln["index"])
        # 姓名跟手機印在同一行（「王小明 0912-345-678」）：這行已經被號碼用掉，剩下的 2～4 個漢字留給姓名候選
        if ln["index"] in used:
            left = rest
            for ph in find_phones(rest):
                left = left.replace(rest[ph["span"][0]:ph["span"][1]], " ")
            first = min((ph["span"][0] for ph in find_phones(rest)), default=len(rest))
            zh = re.sub(r"[^一-鿿]", "", left[:first])          # 姓名在號碼前面（後面那段多半是標籤或地名）
            # 號碼標籤連前面的修飾詞一起去掉（「聯絡電話」「夜間電話」「服務專線」）
            zh = re.sub(r"[一-鿿]{0,2}(?:電話|專線|手機|傳真|分機|總機|直撥|直線|客服|熱線|市話)|行動|免付費", "", zh)
            if 2 <= len(zh) <= 4 and zh not in TITLE_WORDS and not DEPT_RE.search(zh) and looks_like_name(zh) \
                    and not COMPANY_SUFFIX.search(zh) and not NOT_NAME_WORDS.search(zh) \
                    and float(ln.get("height") or 0) >= med_h:
                left_names.append((ln, zh))

    # 地址。中文地址一行就完整；英文地址常拆兩三行，**而且雙欄名片的英文地址在右欄**，
    # 照上下順序排的行中間會夾著左欄的電話行 → 用版面找延續：左邊對齊、緊接在下面的那一行。
    def _addrish(o) -> bool:
        t = (o.get("text") or "").strip()
        return bool(t) and _cjk_ratio(t) <= 0.3 and not find_phones(t) and not EMAIL_RE.search(t) \
            and (bool(EN_ADDR.search(t)) or (len(t) <= 15 and bool(re.search(r"\d", t))))

    def below(ln):
        """緊接在下面那一行（左邊對齊或一半以上重疊）。同一列有好幾段時挑最寬的那段，
        框在它範圍裡的小碎片（OCR 重複認出的「City」這種）一併跳過。"""
        x, y, w, h = ln["box"]
        cands = []
        for o in lines:
            if o["index"] == ln["index"] or o["index"] in used or o["index"] in addr_done:
                continue
            ox, oy, ow, oh = o["box"]
            # 水平方向：左邊對齊，或兩行有一半以上重疊（第二行常常縮排不同、或置中排）
            overlap = min(x + w, ox + ow) - max(x, ox)
            aligned = abs(ox - x) <= max(12, 0.06 * max(w, ow)) or overlap >= 0.5 * min(w, ow)
            # 垂直方向：起點在這一行的中線以下、不會太遠（高的那一行的框常常蓋到下一行，不可以用「間距 ≥ 0」判斷）
            if aligned and oy > y + 0.45 * min(h, oh) and oy - (y + h) <= 1.5 * max(h, oh):
                cands.append(o)
        if not cands:
            return None
        top = min(c["box"][1] for c in cands)
        same = [c for c in cands if c["box"][1] - top <= 0.5 * c["box"][3]]
        best = max(same, key=lambda c: c["box"][2])
        bx, _, bw, _ = best["box"]
        for c in same:
            if c is not best and c["box"][0] >= bx - 2 and c["box"][0] + c["box"][2] <= bx + bw + 2:
                addr_done.add(c["index"])                   # 被包在裡面的碎片
        return best

    def row_right(ln):
        """同一列、接在右邊的地址片段（「4F. No. 9 Lane 130」｜「Sec. 3,」｜「…Rd.」被 OCR 拆成三段）。"""
        out, cur = [], ln
        while True:
            x, y, w, h = cur["box"]
            nxt = None
            for o in lines:
                if o is cur or o in out or o["index"] in used or o["index"] in addr_done or not _addrish(o):
                    continue
                ox, oy, ow, oh = o["box"]
                vov = min(y + h, oy + oh) - max(y, oy)
                if vov >= 0.5 * min(h, oh) and oh >= 0.6 * h and x + w - 0.3 * h <= ox <= x + w + 2.5 * max(h, oh):
                    if nxt is None or ox < nxt["box"][0]:
                        nxt = o
            if not nxt:
                return out
            out.append(nxt)
            cur = nxt

    addr_done: set[int] = set()
    for ln in lines:
        if ln["index"] in addr_done:
            continue
        t = (ln.get("text") or "").strip()
        is_label = bool(ADDR_LABEL.match(t))
        body = ADDR_LABEL.sub("", t)
        # 地址後面接著電話 / 傳真 / 統編（同一行）：切掉
        # 統編標籤後面常直接接數字（「…12 樓 統一編號04595257」這種），號與數字之間沒有字界 → 統編那段用 VAT_RE 切
        vcut = VAT_RE.search(body)
        if vcut:
            body = body[:vcut.start()]
        # 前後的分隔符號（名片常用 ▪ • · |，OCR 也常認成 ! 或 I）一起去掉，郵遞區號才認得到
        body = re.split(r"\s*(?:電話|TEL|Tel|傳真|FAX|Fax|Phone|統一編號|統編)(?![A-Za-z])", body)[0]
        body = body.strip(" ,，;；:：!！|｜▪■•·・●◆-–—").strip()
        tw = TW_ADDR.search(body)
        en_hits = len(EN_ADDR.findall(body))
        # 已經抓過電話 / Email 的行、或含電話號碼的行：只有明確的中文地址或帶地址標籤才算
        # （實測：電話與英文地址並排的一行被算成第二筆地址）
        if find_phones(body) and not tw and not is_label:
            continue
        # 已經抓過電話 / Email 的行：切掉那段之後剩下的要明顯是地址（中文地址、地址標籤或兩個以上英文地址字樣）
        if ln["index"] in used and not tw and not is_label and en_hits < 2:
            continue
        if EMAIL_RE.search(body) or len(body) < 6:
            continue
        # 英文地址的第一行要有門牌（數字）—— 不然公司名、「District Manager」這種也會被當成地址的開頭
        if not tw and not is_label and (not HOUSE_NO.search(body) or en_hits < 1):
            continue
        parts, boxes = [body], [ln["box"]]
        if not tw:
            # 英文地址：同一列右邊的片段先接上，再往下接最多三行（每一行都要像地址的一部分）
            def take_row(first):
                """接上同一列右邊的片段，回整列合起來的框（往下找下一行要用整列，不是最後一段）。"""
                bx = list(first["box"])
                for o in row_right(first):
                    parts.append((o.get("text") or "").strip())
                    boxes.append(o["box"])
                    used.add(o["index"])
                    addr_done.add(o["index"])
                    ox, oy, ow, oh = o["box"]
                    x1, y1 = max(bx[0] + bx[2], ox + ow), max(bx[1] + bx[3], oy + oh)
                    bx = [min(bx[0], ox), min(bx[1], oy), 0, 0]
                    bx[2], bx[3] = x1 - bx[0], y1 - bx[1]
                return dict(first, box=bx)
            cur = take_row(ln)
            for _ in range(3):
                nxt = below(cur)
                if not nxt:
                    break
                nt = (nxt.get("text") or "").strip()
                if not EN_ADDR.search(nt) or find_phones(nt) or EMAIL_RE.search(nt) or _cjk_ratio(nt) > 0.3:
                    break
                parts.append(nt)
                boxes.append(nxt["box"])
                used.add(nxt["index"])
                addr_done.add(nxt["index"])
                cur = take_row(nxt)
            en_hits = sum(len(EN_ADDR.findall(x)) for x in parts)
        if not (tw or is_label or en_hits >= 2):
            continue
        addr = " ".join(parts)
        zm = ZIP_RE.match(addr)
        zipc = ""
        if zm:
            zipc = zm.group(1)
            addr = addr[zm.end():].strip()
        x0 = min(bx[0] for bx in boxes)
        y0 = min(bx[1] for bx in boxes)
        x1 = max(bx[0] + bx[2] for bx in boxes)
        y1 = max(bx[1] + bx[3] for bx in boxes)
        if len(boxes) == 1:
            # 同一行後面還有電話 / 統編：框只框地址那一段
            sub = _part_box(ln, parts[0])
            if sub:
                x0, y0, x1, y1 = sub[0], sub[1], sub[0] + sub[2], sub[1] + sub[3]
        add("address", addr, dict(ln, box=[x0, y0, x1 - x0, y1 - y0]), zip=zipc)
        used.add(ln["index"])
        addr_done.add(ln["index"])

    # 剩下的行：公司 / 部門 / 職稱 / 姓名
    rest_lines = [ln for ln in lines if ln["index"] not in used and (ln.get("text") or "").strip()]
    name_cands: list[tuple[dict, str]] = list(left_names)
    title_lines: list[dict] = []
    titles_by_len = sorted(TITLE_WORDS, key=len, reverse=True)
    for ln in rest_lines:
        t = re.sub(r"\s+", " ", ln["text"]).strip()
        compact = t.replace(" ", "")
        if len(re.sub(r"[\W_]", "", t)) < 2:
            continue
        # 中文職稱＋英文姓名在同一行（「業務經理 Kevin Sample」或反過來）：中文那段是職稱才拆
        mx = (re.fullmatch(r"([一-鿿]{2,12})\s*([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,2})", t)
              or re.fullmatch(r"([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,2})\s*([一-鿿]{2,12})", t))
        if mx:
            zh, en = (mx.group(1), mx.group(2)) if re.match(r"[一-鿿]", mx.group(1)) else (mx.group(2), mx.group(1))
            if any(zh.endswith(w) for w in titles_by_len) and not TITLE_EN.search(en):
                add("title", zh, ln)
                title_lines.append(ln)
                add("name_en", en, ln, part=en)
                continue
        if _cjk_ratio(t) >= 0.5:
            # 姓名＋職稱（職稱可以帶職務詞：「王小明 業務經理」「資訊經理 王小明」）：拆得出像姓名的那段才拆
            sp = _split_name_title(compact, titles_by_len)
            if sp:
                add("title", sp[0], ln)
                title_lines.append(ln)
                name_cands.append((ln, sp[1]))
                continue
            # 職稱在後（「王小明 業務副理」）或在前（「資訊經理 王小明」）都拆開
            suf = next((w for w in titles_by_len if compact.endswith(w)), None)
            pre = None
            if not suf:
                # 職稱在前：後面必須剛好是 2～4 個字的姓名（「資訊經理 王小明」）；「採購部」不是「採購」＋「部」
                for w in titles_by_len:
                    if w in ROLE_HEADS or not compact.startswith(w):
                        continue
                    rest_ = compact[len(w):]
                    if 2 <= len(rest_) <= 4 and re.fullmatch(r"[一-鿿]+", rest_) and not DEPT_RE.search(rest_):
                        pre = w
                        break
            if COMPANY_SUFFIX.search(compact) and len(compact) >= 4 and not suf and not pre:
                if DEPT_RE.search(compact) and not re.search(r"(公司|集團|銀行|醫院|基金會|協會|大學|政府|飯店)", compact):
                    add("department", compact, ln)
                else:
                    fixed = fix_company(compact)
                    if fixed != compact:
                        add("company", fixed, ln, status="check", raw=compact,
                            reason="公司名稱裡常認錯的字樣已依常見寫法修正，請對照原圖")
                    else:
                        add("company", compact, ln)
                continue
            if suf or pre:
                title = suf or pre
                other = compact[: len(compact) - len(title)] if suf else compact[len(title):]
                # 「採購副理」「業務經理」：前面那段是職務 / 單位 → 整段當職稱。
                # 前面那段是 2～4 個漢字、但第一個字不是姓氏（「系統工程師」的「系統」）也是職稱的一部分
                role_head = bool(suf) and bool(other) and (other in TITLE_WORDS or any(other.endswith(w) for w in ROLE_HEADS)
                                                           or (len(other) <= 4 and re.fullmatch(r"[一-鿿]+", other) is not None
                                                               and not looks_like_name(other) and not DEPT_RE.search(other)))
                if role_head and len(compact) <= 8:
                    add("title", compact, ln)
                    title_lines.append(ln)
                    continue
                dm = DEPT_RE.search(other) if other else None
                if dm and suf:
                    add("department", dm.group(1), ln)
                    other = other[: dm.start()]
                # 職稱前面的單位詞一起算職稱（「資訊經理」「社區經理」）：職稱詞前面最多再帶兩個字
                if pre and len(other) > 4 and any(other.startswith(w) for w in titles_by_len):
                    pass
                add("title", title, ln)
                title_lines.append(ln)
                if 2 <= len(other) <= 4 and re.fullmatch(r"[一-鿿]+", other) and looks_like_name(other):
                    name_cands.append((ln, other))
                elif other and len(other) > 4 and suf and not dm:
                    add("department", other, ln)
                continue
            if DEPT_RE.search(compact):
                add("department", compact, ln)
                continue
            if 2 <= len(compact) <= 4 and re.fullmatch(r"[一-鿿]+", compact):
                name_cands.append((ln, compact))
                continue
            # 職稱詞前面帶單位（「資訊經理」「社區經理」）：整段 ≤ 8 字、以職稱詞結尾
            continue
        # 英文
        if COMPANY_EN.search(t) and len(t) >= 5:
            add("company_en", t, ln)
            continue
        tm = TITLE_EN.search(t)
        if DEPT_EN.search(t) and not tm:
            add("department", t, ln)
            continue
        if tm and len(t.split()) <= 7:
            # 職稱與英文姓名在同一行時拆開，只認兩種明確的排法（實測：職稱跟英文名字連在一起）：
            # 「Manager Jerry Chen」（職稱在句首、後面 2～3 個大寫開頭的字）、「Jerry Chen, Manager」（逗號分開）。
            # 職稱在句尾的「Program Director」「Community Manager」前面那些字是職稱的一部分，不拆。
            split = None
            m1 = re.match(r"^\s*((?:" + TITLE_EN.pattern[3:-3] + r")(?:\s+(?:of|for)\s+[A-Z][\w&]*)?)\s+([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,2})\s*$", t, re.I)
            m2 = re.match(r"^\s*([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,2})\s*[,，|/]\s*(.+)$", t)
            if m1 and not TITLE_EN.search(m1.group(2)) and not DEPT_EN.search(m1.group(2)):
                split = (m1.group(1), m1.group(2))
            elif m2 and TITLE_EN.search(m2.group(2)) and not TITLE_EN.search(m2.group(1)):
                split = (m2.group(2), m2.group(1))
            if split:
                add("title", split[0].strip(), ln)
                title_lines.append(ln)
                name_cands.append((dict(ln), split[1].strip()))
                continue
            add("title", t, ln)
            title_lines.append(ln)
            continue
        words = re.findall(r"[A-Za-z][A-Za-z.'\-]*", t)
        # 英文姓名：2～4 個字、每個字大寫開頭；帶句點的只收縮寫字母（「J.」），「Dept.」這種不是名字
        if (2 <= len(words) <= 4 and len(t) <= 40 and all(w[0].isupper() for w in words) and not re.search(r"\d", t)
                and all(not w.endswith(".") or len(w) == 2 for w in words) and not DEPT_EN.search(t)
                and not re.search(r"\b(Purchasing|Marketing|Sales|Finance|Engineering|Logistics|Operations?)\b", t, re.I)):
            name_cands.append((ln, t))

    # 地名、店名、單位名不是姓名（「範例東區」「西門店」「基隆站」）；出現在公司名、部門、網址、Email 裡的字是品牌，不是姓名
    brand_text = " ".join(f["value"] for f in fields if f["key"] in ("company", "company_en", "department", "url", "email")).lower()
    name_cands = [(ln, v) for ln, v in name_cands
                  if v not in TITLE_WORDS and v not in ROLE_HEADS
                  and not re.search(r"(區|市|縣|鄉|鎮|村|里|店|站|所|處|局|部|課|組|館|廠|路|街|號|樓|門市|分行|中心)$", v)
                  and not re.match(r"^(台|臺)?(北|中|南|東|西)(區|部)", v)
                  and not (len(v) >= 2 and v.replace(" ", "").lower() in brand_text)]

    def score(item):
        ln, _v = item
        h = float(ln.get("height") or 0)
        # 靠近職稱那一行的加分：姓名通常緊鄰職稱（同一列或上下一行）
        near = 0.0
        for tl in title_lines:
            if tl is ln or tl.get("index") == ln.get("index"):
                near = max(near, 1.0)
                continue
            dy = abs((ln["box"][1] + ln["box"][3] / 2) - (tl["box"][1] + tl["box"][3] / 2))
            if dy <= 2.5 * max(ln["box"][3], tl["box"][3]):
                near = max(near, 0.6)
        # 中文候選第一個字不是常見姓氏：大幅降分（多半是口號、品牌、部門名）
        surname = 1.0 if _cjk_ratio(_v) < 0.5 or looks_like_name(_v) else 0.3
        return h * (1.0 + near) * surname

    # 姓名：分數最高的候選（不是最大字就一定是姓名 —— 公司名、Logo、品牌字已經先排除）
    cn = [(ln, v) for ln, v in name_cands if _cjk_ratio(v) >= 0.5]
    en = [(ln, v) for ln, v in name_cands if _cjk_ratio(v) < 0.5]
    if cn:
        ln, v = max(cn, key=score)
        if looks_like_name(v):
            add("name", v, ln)
        else:
            add("name", v, ln, status="check", reason="第一個字不是常見的姓氏，請確認這是不是姓名")
    if en:
        ln, v = max(en, key=score)
        add("name_en", v, ln)

    _reconcile_urls(fields)
    fields.extend(qr_fields(qr_texts))
    _flag_conflicts(fields)
    return fields


def _reconcile_urls(fields: list[dict]) -> None:
    """網址跟同一張名片的 Email 網域對照：OCR 漏掉的結尾（「www.xxx」少了 .com.tw）或連字號
    （「example sample」）用 Email 網域補回；補不回來又沒有網域結尾的，標需確認。"""
    an = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    doms = [f["value"].split("@", 1)[1].lower() for f in fields
            if f["key"] == "email" and "@" in f["value"] and f.get("status") != "deleted"]
    for f in fields:
        if f["key"] != "url" or f["source"].get("method") != "ocr":
            continue
        m = re.match(r"^((?:https?://)?)((?:www\.)?)([^/]+)(/.*)?$", f["value"], re.I)
        if not m:
            continue
        scheme, www, host, path = m.group(1), m.group(2), m.group(3).lower(), m.group(4) or ""
        has_tld = re.search(r"\." + _TLD + r"(?:\.[a-z]{2})?$", host) is not None
        fix = None
        for d in doms:
            d_reg = d.split(".", 1)[1] if d.startswith("mail.") else d
            if an(host) == an(d_reg) and host != d_reg:
                fix = d_reg                                      # 只差點或連字號
            elif not has_tld and len(an(host)) >= 3 and an(d_reg).startswith(an(host)):
                fix = d_reg                                      # 網址被截掉結尾
            if fix:
                break
        if fix:
            f["value"] = scheme + www + fix + path
            f["norm"] = f["value"].lower()
            if not has_tld:
                f["status"], f["reason"] = "check", "網址的結尾依同一張名片的 Email 網域補上，請對照原圖"
        elif not has_tld:
            f["status"], f["reason"] = "check", "網址看起來不完整（沒有 .com / .tw 這類結尾），請對照原圖"
    # 反過來：Email 的 @ 被認成 Q / O（「kevinQexample.com」），而同一張名片的網址就是那個網域 → 補回 @
    hosts = [re.sub(r"^(?:https?://)?(?:www\.)?", "", f["value"].lower()).split("/")[0]
             for f in fields if f["key"] == "url"]
    for f in fields:
        if f["key"] != "email" or "@" in f["value"]:
            continue
        v = f["value"]
        for h in hosts:
            if h and v.lower().endswith(h) and len(v) > len(h) + 1 and v[-len(h) - 1] in "QqOo0©":
                f["value"] = v[:-len(h) - 1] + "@" + h
                f["norm"] = f["value"].lower()
                f["status"], f["reason"] = "check", "Email 的 @ 依同一張名片的網址補上，請對照原圖"
                break


# --------------------------------------------------------------------------- QR

def parse_qr(text: str) -> list[tuple[str, str, dict]]:
    """QR 內容 → `[(key, value, extra)]`。vCard / MECARD / 純網址；其他回空（不猜）。"""
    t = (text or "").strip()
    out: list[tuple[str, str, dict]] = []
    if t.upper().startswith("BEGIN:VCARD"):
        unfolded = re.sub(r"\r?\n[ \t]", "", t)
        for raw in unfolded.splitlines():
            if ":" not in raw:
                continue
            head, val = raw.split(":", 1)
            prop = head.split(";")[0].upper()
            params = head.upper()
            val = val.strip().replace("\\,", ",").replace("\\;", ";").replace("\\n", " ")
            if not val:
                continue
            if prop == "FN":
                out.append(("name" if _cjk_ratio(val) >= 0.5 else "name_en", val, {}))
            elif prop == "ORG":
                org = [x for x in val.split(";") if x]
                if org:
                    out.append(("company" if _cjk_ratio(org[0]) >= 0.5 else "company_en", org[0], {}))
                if len(org) > 1:
                    out.append(("department", org[1], {}))
            elif prop == "TITLE":
                out.append(("title", val, {}))
            elif prop == "TEL":
                kind = "fax" if "FAX" in params else ("mobile" if "CELL" in params else "phone")
                out.append((kind, val, {"norm": normalize_phone(val)}))
            elif prop == "EMAIL":
                out.append(("email", val, {"norm": val.lower()}))
            elif prop == "URL":
                out.append(("url", val, {"norm": val.lower()}))
            elif prop == "ADR":
                parts = [x for x in val.split(";") if x]
                out.append(("address", " ".join(parts), {}))
        return out
    if t.upper().startswith("MECARD:"):
        body = t[7:]
        for part in re.split(r"(?<!\\);", body):
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            k = k.upper()
            v = v.replace("\\;", ";").replace("\\:", ":").strip()
            if not v:
                continue
            if k == "N":
                v = v.replace(",", " ").strip()
                out.append(("name" if _cjk_ratio(v) >= 0.5 else "name_en", v, {}))
            elif k == "TEL":
                out.append(("mobile" if normalize_phone(v).lstrip("+").startswith(("09", "8869")) else "phone", v,
                            {"norm": normalize_phone(v)}))
            elif k == "EMAIL":
                out.append(("email", v, {"norm": v.lower()}))
            elif k == "URL":
                out.append(("url", v, {"norm": v.lower()}))
            elif k == "ADR":
                out.append(("address", v, {}))
            elif k == "ORG":
                out.append(("company" if _cjk_ratio(v) >= 0.5 else "company_en", v, {}))
        return out
    if re.match(r"^(https?://|www\.)\S+$", t, re.I):
        return [("url", t, {"norm": t.lower()})]
    return []


def qr_fields(qr_texts: Iterable[str]) -> list[dict]:
    out = []
    for q in qr_texts or ():
        for key, val, extra in parse_qr(q):
            out.append(_field(key, val, method="qr", **extra))
    return out


def _flag_conflicts(fields: list[dict]) -> None:
    """QR 跟印刷的同一種欄位不一致 → 兩筆都留、都標需確認（規格：並列，交給使用者選）。"""
    by: dict[str, list[dict]] = {}
    for f in fields:
        by.setdefault(f["key"], []).append(f)
    for key, fs in by.items():
        qr = [f for f in fs if f["source"]["method"] == "qr"]
        ocr = [f for f in fs if f["source"]["method"] == "ocr"]
        if not qr or not ocr:
            continue
        nq = {(f.get("norm") or f["value"]).lower() for f in qr}
        no = {(f.get("norm") or f["value"]).lower() for f in ocr}
        if key in MULTI_KEYS and nq & no:
            # 同一筆：留印刷那一筆、QR 那筆移除（避免重複），並記下 QR 也有
            for f in qr:
                if (f.get("norm") or f["value"]).lower() in no:
                    f["duplicate_of_print"] = True
            continue
        if nq != no:
            for f in qr + ocr:
                f["status"] = "check"
                f["reason"] = "QR Code 與印刷的內容不同，請選一個"
    fields[:] = [f for f in fields if not f.get("duplicate_of_print")]


def field_value(fields: list[dict], key: str) -> str:
    for f in fields:
        if f["key"] == key and f.get("status") != "deleted":
            return f["value"]
    return ""


def to_public(fields: list[dict]) -> list[dict[str, Any]]:
    return [{k: v for k, v in f.items() if k != "duplicate_of_print"} for f in fields]
