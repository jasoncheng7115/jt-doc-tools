"""名片辨識：日文名片的規則（使用者 2026-11-20：加入日文支援）。

只在「辨識語言選日文」或卡面上有假名時才用（`extract.extract_fields(..., lang="ja")`），
放在中文規則之前先處理日文的公司、部門、職稱、姓名；處理不了的行照樣交給後面的通用規則。
繁中名片完全不經過這裡（判斷條件是語言與假名），不影響原本的辨識結果。

日文名片常見的寫法：
* 公司：「株式会社サンプル」「サンプル株式会社」「（株）サンプル」「一般社団法人…」
* 部門＋職稱同一行：「営業本部 第一営業部 部長」「技術部 課長」
* 姓名：「山田 太郎」（姓與名之間空一格）、「佐々木健太郎」；上面常有假名讀音（やまだ たろう / ヤマダ タロウ），
  旁邊或下面常有羅馬拼音（Taro Yamada）
* 郵遞區號「〒100-0005」＋地址「東京都千代田区丸の内1-2-3」
* 電話「03-1234-5678」「TEL 03-…」、手機「090-1234-5678」（11 碼）「携帯」、國碼 +81
"""
from __future__ import annotations

import re
from typing import Callable, Optional

#: 假名（平假名、片假名、長音）。有兩個以上就當成日文名片
KANA = re.compile(r"[ぁ-ゖァ-ヺー]")
#: 日文的漢字類字元（含「々」「〆」「ヶ」）
JA_CHAR = r"[々〆ヶ一-鿿]"

COMPANY_MARK = re.compile(
    r"(株式会社|有限会社|合同会社|合資会社|合名会社|[（(]株[)）]|㈱|[（(]有[)）]|㈲|"
    r"[一~ー－—―‐]般社団法人|[一~ー－—―‐]般財団法人|公益社団法人|公益財団法人|特定非営利活動法人|NPO法人|学校法人|医療法人|社会福祉法人|"
    r"独立行政法人|国立大学法人|宗教法人|弁護士法人|税理士法人|監査法人|司法書士法人|行政書士法人)")
#: 沒有法人格字樣、但這樣結尾的也是機構名
COMPANY_TAIL = re.compile(r"(銀行|信用金庫|信用組合|証券|保険|大学|大学院|病院|医院|クリニック|研究所|事務所|協会|組合|"
                          r"製作所|工業|商事|産業|商会|商店|工務店|出版社|新聞社|放送局|市役所|区役所|町役場|村役場|省|庁)$")
DEPT_TAIL = re.compile(r"(本部|事業部|事業本部|統括部|部|課|室|係|グループ|チーム|センター|支社|支店|営業所|出張所|"
                       r"工場|研究所|研究室|局|本社|オフィス|ユニット|セクション|ディビジョン)$")
TITLES = (
    "代表取締役社長", "代表取締役会長", "代表理事", "代表社員", "代表幹事", "業務執行社員", "代表取締役副社長", "代表取締役専務", "代表取締役", "取締役社長", "取締役副社長",
    "専務取締役", "常務取締役", "取締役", "社外取締役", "監査役", "執行役員", "執行役", "会長", "社長", "副社長", "専務", "常務",
    "本部長", "副本部長", "事業部長", "統括部長", "部長", "副部長", "部長代理", "次長", "課長", "課長代理", "課長補佐", "係長",
    "主任", "主査", "主幹", "参事", "副参事", "室長", "所長", "副所長", "センター長", "支店長", "支社長", "工場長", "店長",
    "マネージャー", "マネジャー", "シニアマネージャー", "ゼネラルマネージャー", "リーダー", "チームリーダー", "グループリーダー",
    "チーフ", "ディレクター", "プロデューサー", "エンジニア", "シニアエンジニア", "デザイナー", "コンサルタント", "アナリスト",
    "スペシャリスト", "アシスタント", "プランナー", "セールス", "担当", "営業担当", "顧問", "相談役", "理事長", "理事", "会頭",
    "教授", "准教授", "助教", "講師", "研究員", "主任研究員", "弁護士", "弁理士", "税理士", "公認会計士", "司法書士", "行政書士",
    "社会保険労務士", "医師", "院長", "看護師", "薬剤師", "代表", "代表者", "社員", "スタッフ", "秘書",
)
_TITLES_BY_LEN = sorted(TITLES, key=len, reverse=True)

#: 常見的日文姓氏（前 200 名左右；名片上的姓名第一段是這些就有把握是人名）
SURNAMES = set("""
佐藤 鈴木 高橋 田中 伊藤 渡辺 渡邊 山本 中村 小林 加藤 吉田 山田 佐々木 山口 松本 井上 木村 林 斎藤 斉藤 清水 山崎 森 池田
橋本 阿部 石川 山下 中島 石井 小川 前田 岡田 長谷川 藤田 後藤 近藤 村上 遠藤 青木 坂本 斉藤 福田 太田 西村 藤井 金子
岡本 藤原 中野 三浦 原田 中川 松田 竹内 小野 田村 中山 和田 石田 森田 上田 原 柴田 酒井 工藤 横山 宮崎 宮本 内田 高木
安藤 島田 谷口 大野 高田 丸山 今井 河野 藤本 村田 武田 上野 杉山 増田 小山 平野 大塚 千葉 久保 松井 小島 岩崎 桜井
野口 松尾 野村 木下 菊地 佐野 大西 杉本 新井 浜田 菅原 市川 水野 小松 島崎 山内 西田 菊池 西川 五十嵐 北村 安田 中田
川口 平田 川崎 飯田 吉川 本田 久保田 沢田 辻 関 吉村 渡部 岩田 中西 服部 樋口 福島 川上 永井 松岡 田口 山中 森本
土屋 矢野 広瀬 秋山 石原 松下 大橋 松浦 吉岡 小池 馬場 浅野 荒木 大久保 野田 小沢 田辺 川村 星野 黒田 堀 尾崎 望月
永田 熊谷 内藤 松村 西山 大谷 平井 大島 岩本 片山 本間 早川 横田 岡崎 荒井 大石 鎌田 成田 宮田 小田 石橋 篠原 須藤
河合 大塚 高野 小西 宮下 小泉 桑原 若林 古川 堀内 宮川 高山 小倉 上原 細川 前川 角田 平山 中尾 今村 植田 内山 南
北川 大川 富田 稲葉 水谷 竹田 西 宮内 森下 大森 藤岡 坂口 福井 一ノ瀬 堤 奥村 栗原 小原 長田 牧野 有村 白石 早坂
""".split())


def split_camel(text: str) -> str:
    """OCR 把英文字之間的空格弄丟（「SeniorStaff」「TransportCorporation」「NaokiKobayashi」）：小寫接大寫的地方補空格。
    Email、網址、有數字的不動。"""
    if "@" in text or "/" in text or "." in text or re.search(r"\d", text):
        return text                                   # Email、網址、「Co.,Ltd.」這種不拆（「www.ExampleCorp.co.jp」）
    return re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", text)


def has_kana(text: str) -> bool:
    return len(KANA.findall(text or "")) >= 1


def is_japanese_card(lines: list[dict], lang: str) -> bool:
    """選了日文，或卡面上的假名加起來有兩個以上（台灣名片幾乎不會出現假名）。"""
    if lang == "ja":
        return True
    return sum(len(KANA.findall(ln.get("text") or "")) for ln in lines) >= 2


def surname_of(name: str) -> str:
    """名字開頭是哪一個常見姓氏（最長的先比）；不是常見姓氏回空字串。"""
    v = name.replace(" ", "").replace("　", "")
    for n in (3, 2, 1):
        if v[:n] in SURNAMES:
            return v[:n]
    return ""


def looks_like_ja_name(v: str) -> bool:
    """日文姓名：2～7 個漢字（可含「々」），或「漢字姓＋空白＋名（漢字或假名）」。"""
    t = v.strip().replace("　", " ")
    parts = t.split()
    if any(p in TITLES or DEPT_TAIL.search(p) or COMPANY_MARK.search(p) for p in parts):
        return False                                  # 「理事長 鈴木一郎」「営業部 山田」不是一個姓名
    if len(parts) == 2:
        sur, given = parts
        return (re.fullmatch(JA_CHAR + r"{1,4}", sur) is not None
                and re.fullmatch(r"(?:" + JA_CHAR + r"|[ぁ-ゖァ-ヺー]){1,5}", given) is not None)
    compact = t.replace(" ", "")
    sur = surname_of(compact)
    if not sur:
        return False
    # 漢字的姓＋名（漢字或假名，「石井さくら」）
    return re.fullmatch(r"(?:" + JA_CHAR + r"|[ぁ-ゖァ-ヺー]){1,5}", compact[len(sur):]) is not None


def split_title(text: str) -> tuple[str, str]:
    """「営業部 部長」「技術部課長」→（部門 / 其他, 職稱）；最後面不是職稱回 (text, "")。"""
    t = text.strip()
    for w in _TITLES_BY_LEN:
        if t.endswith(w):
            return t[: len(t) - len(w)].strip(" 　・/／"), w
    return t, ""


#: 一行是「日文＋英文」（「株式会社サンプル Sample Inc.」「営業部 部長 / Sales Manager」「山田 太郎 Taro Yamada」）
_BI = re.compile(r"(.*?(?:" + JA_CHAR + r"|[ぁ-ゖァ-ヺ]).*?)\s*[／/|｜]?\s*[(（]?([A-Za-z][A-Za-z.,'&\- ]*[A-Za-z.&])[)）]?")


def handle_lines(lines: list[dict], used: set, add: Callable, name_cands: list, title_lines: list,
                 en_kind: Optional[Callable] = None) -> None:
    """日文名片的公司、部門、職稱、姓名。處理過的行放進 `used`（後面的通用規則就不再看）。

    `add(key, value, line, **kw)` 是 `extract_fields` 裡加欄位的函式；姓名只放進候選（`name_cands`），
    最後跟其他候選一起依字的大小、離職稱遠近挑。`en_kind(text)` 判斷同一行的英文那一段是什麼
    （`title_en` / `department_en` / `company_en` / `name_en` / None，沿用通用規則的英文判斷）。
    """
    for ln in lines:
        if ln["index"] in used:
            continue
        t = re.sub(r"[ 　]+", " ", (ln.get("text") or "")).strip()
        if not t or not re.search(JA_CHAR + r"|[ぁ-ゖァ-ヺ]", t):
            continue
        # 同一行後面接英文：英文那段照英文規則另外放（外文職稱 / 部門 / 公司 / 姓名），日文那段往下走
        bm = _BI.fullmatch(t)
        if bm and en_kind and len(re.findall(r"[A-Za-z]", bm.group(2))) >= 3:
            en = split_camel(bm.group(2).strip())
            kind = en_kind(en)
            if kind:
                if kind == "name_en":
                    name_cands.append((dict(ln), en))
                else:
                    add(kind, en, ln, part=en)
                t = bm.group(1).strip(" ／/|｜")
        # 只有假名（姓名上面的讀音「やまだ たろう」「ヤマダ タロウ」）：不是任何欄位 ——
        # 但片假名的職稱、部門（「エンジニア」「マーケティンググループ」）也是只有假名，那些要留著
        if re.fullmatch(r"[ぁ-ゖァ-ヺー ・]+", t) and len(t.replace(" ", "")) <= 14 \
                and not split_title(t)[1] and not DEPT_TAIL.search(t) and not COMPANY_MARK.search(t):
            used.add(ln["index"])
            continue
        # 公司：有法人格字樣（株式会社…）或以銀行 / 大学 / 事務所…結尾。同一行後面接部門、職稱的拆開
        cm = COMPANY_MARK.search(t)
        if cm or COMPANY_TAIL.search(t.split(" ")[0] if " " in t else t):
            toks = t.split(" ")
            comp_end = 1
            if cm:
                # 公司名到法人格字樣那一段為止（「サンプル 株式会社」也接起來）
                for k, tok in enumerate(toks):
                    if COMPANY_MARK.search(tok):
                        comp_end = k + 1
                        break
                # 法人格自己一段、名稱在後（「株式会社 サンプル」「一般社団法人 サンプル協会」）：後面那一段也是公司名
                if COMPANY_MARK.fullmatch(toks[comp_end - 1]) and comp_end < len(toks) \
                        and not split_title(toks[comp_end])[1] and not DEPT_TAIL.search(toks[comp_end]):
                    comp_end += 1
            company = " ".join(toks[:comp_end])
            add("company", company, ln, part=" ".join(toks[:comp_end]))
            rest = " ".join(toks[comp_end:]).strip()
            if rest:
                _dept_title(rest, ln, add, title_lines)
            used.add(ln["index"])
            continue
        # 職稱在前、姓名在後（「理事長 鈴木一郎」「部長 山田太郎」）
        pre = next((w for w in _TITLES_BY_LEN if t.startswith(w) and looks_like_ja_name(t[len(w):].strip())
                    and surname_of(t[len(w):].strip())), None)
        if pre:
            add("title", pre, ln, part=pre)
            title_lines.append(ln)
            name_cands.append((ln, t[len(pre):].strip()))
            used.add(ln["index"])
            continue
        # 部門 / 職稱（可能兩個都在同一行，也可能有好幾段部門「営業本部 第一営業部」）
        head, title = split_title(t)
        if title or DEPT_TAIL.search(head.split(" ")[-1] if head else ""):
            # 「部長 山田太郎」「山田 太郎 部長」這種姓名跟職稱同一行的：前後那一段像姓名就當候選
            if title and head and looks_like_ja_name(head) and not DEPT_TAIL.search(head):
                add("title", title, ln, part=title)
                title_lines.append(ln)
                name_cands.append((ln, head))
                used.add(ln["index"])
                continue
            if _dept_title(t, ln, add, title_lines):
                used.add(ln["index"])
            continue
        # 姓名
        if looks_like_ja_name(t):
            name_cands.append((ln, t))
            used.add(ln["index"])


def _dept_title(text: str, ln: dict, add: Callable, title_lines: list) -> bool:
    """「営業本部 第一営業部 部長」→ 部門「営業本部 第一営業部」＋職稱「部長」。認得出東西回 True。"""
    head, title = split_title(text)
    got = False
    if head and DEPT_TAIL.search(head):
        add("department", head, ln, part=head)
        got = True
    elif head and title and len(text.replace(" ", "")) <= 10:
        title = text.strip()                          # 「営業部長」「技術担当」：前面那段是職稱的一部分
    if title:
        add("title", title, ln, part=title)
        title_lines.append(ln)
        got = True
    return got


# --------------------------------------------------------------------------- 地址、郵遞區號、電話

#: 都道府縣開頭的地址（後面要接市區町村）
JP_ADDR = re.compile(r"(?:北海道|東京都|京都府|大阪府|" + JA_CHAR + r"{2,3}県)" + r".{0,40}?(?:市|区|町|村|郡)")
#: 郵遞區號。OCR 常把「〒」認成「テ」「〶」「干」（實測：遠端 GPU EasyOCR 日文模型認成「テ」）
JP_ZIP = re.compile(r"[〒〶テ干そ]\s*(\d{3})\s*[-－‐ー]?\s*(\d{4})")
#: 沒有〒、但緊接著都道府縣的「100-0005 東京都…」也是郵遞區號
_BARE_ZIP = re.compile(r"^\s*(\d{3})\s*[-－‐ー]\s*(\d{4})(?=\s*(?:北海道|東京都|京都府|大阪府|" + JA_CHAR + r"{2,3}県))")


def zip_of(text: str) -> tuple[str, str]:
    """「〒100-0005 東京都…」→（"100-0005", 去掉郵遞區號的其餘部分）。沒有回 ("", text)。"""
    m = JP_ZIP.search(text or "") or _BARE_ZIP.match(text or "")
    if not m:
        return "", text
    return m.group(1) + "-" + m.group(2), (text[:m.start()] + " " + text[m.end():]).strip()


def zip_at(text: str, start: int, end: int) -> bool:
    """號碼那一段其實是日本的郵遞區號嗎：前面是〒（或它的誤認），或「123-4567」後面緊接著都道府縣。"""
    if re.search(r"[〒〶テ干]\s*$", text[max(0, start - 3):start]):
        return True
    return bool(re.fullmatch(r"\s*\d{3}\s*[-－‐ー]\s*\d{4}\s*", text[start:end])
                and re.match(r"\s*(?:北海道|東京都|京都府|大阪府|" + JA_CHAR + r"{2,3}県)", text[end:]))


#: 日本網域少了點（實測：「@examplecoyjp」，「.co.jp」的點不見、還多認出一個 y）
_JP_DOMAIN = re.compile(r"(@[a-z0-9\-]+?)\.?(co|ne|or|ac|go|ed|ad|lg)[.,y]?(jp)\b", re.I)


#: 郵遞區號自己一行，OCR 常把「〒」認成 7 / 1 / T / テ、4 認成 +（實測）
ZIP_LINE = re.compile(r"^[〒〶テ干そ7T1lI|]?\s*([0-9+の川】lIOo]{3})\s*[-－‐ー]\s*([0-9+の川】lIOo]{4})$")
#: 郵遞區號裡 OCR 認錯的數字（實測：0 →「の」、1 →「川」「】」、4 →「+」）
ZIP_DIGIT_FIX = str.maketrans({"+": "4", "の": "0", "川": "1", "】": "1", "l": "1", "I": "1", "O": "0", "o": "0"})
_EMAIL_LABEL = re.compile(r"^(.*?(?:E\s*-?\s*mail|Email|Mail|メール|Ｅメール)\s*[:：]?\s*)", re.I)
_TLDS = ("jp", "com", "net", "org", "info", "biz", "io", "co", "tw", "asia")


def _domain(chunks: list[str]) -> str:
    """網域的幾段（OCR 把點認成空白、冒號、逗號）接回來；只有一段又以 jp 結尾的補點（「examplejp」）。"""
    d = ".".join(c.lower() for c in chunks if c)
    if "." not in d:
        m = _JP_DOMAIN.fullmatch("@" + d)
        if m:
            return m.group(1)[1:] + "." + m.group(2).lower() + ".jp"
        if d.endswith("jp") and len(d) > 4:
            return d[:-2] + ".jp"
    m = _JP_DOMAIN.fullmatch("@" + d.replace(".jp", "jp"))
    if m and d.count(".") < 2:
        return m.group(1)[1:].rstrip(".") + "." + m.group(2).lower() + ".jp"
    return d


def repair_email_line(text: str) -> tuple[str, bool]:
    """日文名片的 Email：英文模型常把點讀成空白（「misaki ito@example jp」）、日文模型把點弄丟（「examplecojp」）。
    @ 前後被空白 / 冒號拆開的幾段接回來。回（修好的文字, 有沒有改）。"""
    if "@" not in (text or ""):
        return text, False
    i = text.index("@")
    left, right = text[:i], text[i + 1:]
    lab = _EMAIL_LABEL.match(left)
    head, local = (lab.group(1), left[lab.end():]) if lab else ("", left)
    lm = re.search(r"((?:[A-Za-z0-9._%+\-]+[ ]){0,2}[A-Za-z0-9._%+\-]+)\s*$", local)
    if not lm:
        return text, False
    head += local[:lm.start()]
    local_fixed = re.sub(r"\s+", ".", lm.group(1).strip())
    right = re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z])", ".", right)      # 網域不會有底線：是點認錯
    rm = re.match(r"\s*([A-Za-z][A-Za-z0-9\-]*(?:\s*[.,:;]?\s*[A-Za-z][A-Za-z0-9\-]*){0,4})", right)
    if not rm:
        return text, False
    chunks = re.split(r"\s*[.,:;]\s*|\s+", rm.group(1).strip())
    # 最後一段要是網域結尾；後面多吃進來的（「… jp TEL」）退回去
    while len(chunks) > 1 and chunks[-1].lower() not in _TLDS and not chunks[-1].lower().endswith("jp"):
        chunks.pop()
    domain = _domain(chunks)
    consumed = rm.group(1)
    if len(chunks) < len(re.split(r"\s*[.,:;]\s*|\s+", consumed.strip())):
        # 只用到前面幾段：從原文裡找到最後一段的結尾
        pos = 0
        for c in chunks:
            pos = right.index(c, pos) + len(c)
        consumed_len = pos
    else:
        consumed_len = rm.end()
    fixed = head + local_fixed + "@" + domain + right[consumed_len:]
    return fixed, fixed != text


def repair_url_line(text: str) -> tuple[str, bool]:
    """網址的「//」與點被認成別的（「https: |Iwww example or:jp」）：接回「https://www.example.or.jp」。"""
    text = re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z])", ".", text or "")
    m = re.search(r"(h[it]{1,3}ps?)\s*[:;](?:\s*[|Il/\\({\[]){0,3}\s*([wvy][wvy]{2})?\s*[.,:]?\s*"
                  r"([A-Za-z][A-Za-z0-9\-]*(?:\s*[.,:;]?\s*[A-Za-z][A-Za-z0-9\-]*){0,4})", text, re.I)
    if not m:
        return text, False
    chunks = re.split(r"\s*[.,:;]\s*|\s+", m.group(3).strip())
    while len(chunks) > 1 and chunks[-1].lower() not in _TLDS and not chunks[-1].lower().endswith("jp"):
        chunks.pop()
    if len(chunks) < 2 and not chunks[0].lower().endswith("jp"):
        return text, False
    scheme = "https" if "s" in m.group(1).lower() else "http"
    url = scheme + "://" + ("www." if m.group(2) else "") + _domain(chunks)
    fixed = text[:m.start()] + url
    return fixed, fixed != text


def repair_jp_email(text: str) -> tuple[str, bool]:
    """Email 網域是日本的（.co.jp / .ne.jp…）但點不見了：補回來。回（修好的文字, 有沒有改）。"""
    if "@" not in (text or ""):
        return text, False
    out = text
    for m in list(_JP_DOMAIN.finditer(text)):
        whole = m.group(0)
        fixed = f"{m.group(1)}.{m.group(2).lower()}.jp"
        if whole.lower() != fixed.lower():
            out = out.replace(whole, fixed, 1)
    return out, out != text


def is_jp_mobile(digits: str) -> bool:
    """日本的手機：070 / 080 / 090 開頭 11 碼，或國碼 81 後面接 70 / 80 / 90。"""
    d = digits.lstrip("+")
    return (len(d) == 11 and d[:3] in ("070", "080", "090")) or (d.startswith("81") and d[2:4] in ("70", "80", "90"))


def jp_phone_complete(norm: str) -> Optional[bool]:
    """日本號碼的長度對不對；不是日本號碼回 None（交給原本的台灣規則）。"""
    d = norm.lstrip("+")
    if norm.startswith("+81") or (d.startswith("81") and len(d) in (11, 12)):
        return 9 <= len(d) - 2 <= 10
    if len(d) == 11 and d[:3] in ("070", "080", "090", "050"):
        return True
    return None


# --------------------------------------------------------------------------- 形近字
#: 夾在兩個片假名中間的漢字多半是認錯的片假名（「ナ三キ」→「ナミキ」、「テ二ス」→「テニス」）
_KANA_LOOKALIKE = {"三": "ミ", "二": "ニ", "口": "ロ", "力": "カ", "工": "エ", "夕": "タ", "卜": "ト", "八": "ハ"}
_KANA_SANDWICH = re.compile(r"(?<=[ァ-ヺ])([" + "".join(_KANA_LOOKALIKE) + r"])(?=[ァ-ヺー])")
_IPPAN = re.compile(r"(?<![一-鿿])[~〜ー－—―‐-](?=般(?:社団|財団)法人)")
_ADDR_TE = re.compile(r"(?<=[一-鿿])て(?=\s*-\s*\d)")


def fix_lookalikes(text: str) -> str:
    """OCR 的形近字：片假名中間的漢字、「~般社団法人」的「一」、地址門牌的 7 認成「て」。"""
    text = _KANA_SANDWICH.sub(lambda m: _KANA_LOOKALIKE[m.group(1)], text)
    text = _IPPAN.sub("一", text)
    if JP_ADDR.search(text):
        text = _ADDR_TE.sub("7", text)
    return text
