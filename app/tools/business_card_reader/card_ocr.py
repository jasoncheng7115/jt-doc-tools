"""名片辨識：一張已拉正的卡面 → OCR → 文字行（含框位）。

* 走共用的 `app/core/ocr_engine.recognize_image`（EasyOCR / Tesseract / 遠端 GPU，跟其他工具同一套設定與退路）。
* **方向**：卡面可能倒放（拍照時拿反）或直式。橫式卡試 0° 與 180°，直式卡試 0° / 90° / 270°，挑「信心×字數」最高的；
  橫的框兩個方向都讀不太出來時再試 90° / 270°（直式名片橫放掃描）。
  直式卡片本來就可能是直的（直書名片），所以直式不強制轉成橫式。
* 字（word）依垂直重疊併成行；行的框位是卡面座標（拉正後的裁圖），校對畫面用來框出原文。
* 直書（日文的縦書き、台灣的直式名片）：直排的欄另外切字、排成橫的一行再讀（`vertical.py`、`_vertical_pass`）。
"""
from __future__ import annotations

import io
import re
import logging
from dataclasses import dataclass, field

from PIL import Image

log = logging.getLogger(__name__)

#: 首版驗收語言：繁中＋英文（規格第 3 節）
DEFAULT_LANGS = "chi_tra+eng"
#: 名片的辨識語言（使用者 2026-11-20：加入日文）。EasyOCR 的日文模型同時認得漢字與假名；
#: 非拉丁語系的模型一次只能載一個，所以是「選一種」而不是全部一起認。
LANGS = {"zh": "chi_tra+eng", "ja": "jpn+eng"}
#: 會有直書（直排）名片的語言：日文的縦書き、台灣的直式名片（2026-11-22 加繁中）
VERTICAL_LANGS = {"jpn", "chi_tra"}


def langs_for(lang: str) -> str:
    """`zh` / `ja` → OCR 語言碼；不認得的當預設（繁中＋英文）。也收已經是語言碼的值（批次裡存的）。"""
    if lang in LANGS:
        return LANGS[lang]
    return lang if lang in LANGS.values() else DEFAULT_LANGS


def lang_of(langs: str) -> str:
    """OCR 語言碼 → `zh` / `ja`（擷取規則用）。"""
    return next((k for k, v in LANGS.items() if v == langs), "zh")


def lang_available(lang: str) -> dict:
    """這台能不能辨識這種語言：EasyOCR（本機或遠端 GPU）認得的都可以（第一次用會下載模型）；
    只有 Tesseract 時要裝了那個語言的訓練檔。回 `{"ok": bool, "via": "easyocr" / "tesseract" / "", "hint", "missing"?}`。"""
    from ...core import ocr_engine
    codes = langs_for(lang).split("+")
    try:
        from ...core import ocr_remote_settings
        remote = ocr_remote_settings.is_enabled_and_configured()
    except Exception:                                  # noqa: BLE001
        remote = False
    if ocr_engine.get_default_engine() == "easyocr" and (remote or ocr_engine.is_easyocr_available()) \
            and all(c in ocr_engine._TESS_TO_EASYOCR for c in codes):
        return {"ok": True, "via": "easyocr", "hint": ""}
    try:
        from ...core import tessdata_manager
        have = tessdata_manager.get_installed_langs() if ocr_engine.is_tesseract_available() else set()
    except Exception:                                  # noqa: BLE001
        have = set()
    missing = [c for c in codes if c not in have]
    if not missing:
        return {"ok": True, "via": "tesseract", "hint": ""}
    return {"ok": False, "via": "", "missing": missing,
            "hint": "這台的 OCR 沒有這個語言的辨識資料：請管理員到「設定 → OCR 引擎」安裝，或改用 EasyOCR"}


@dataclass
class Line:
    text: str
    box: list[int]                 # [left, top, width, height]，卡面座標
    conf: float                    # 0..100；引擎沒給就是 0
    height: int = 0                # 字高（判斷姓名等大字用）
    words: list[dict] = field(default_factory=list)
    alt: str = ""                  # 信心低的行放大再讀一次的另一種讀法（只給人參考，不自動換）
    alt_conf: float = 0.0
    vertical: bool = False         # 直書的一欄（已經排成橫的讀過；框是整欄，不可以再拿原圖的那一塊重讀）

    def to_dict(self) -> dict:
        d = {"text": self.text, "box": self.box, "conf": round(self.conf, 1), "height": self.height}
        if self.alt:
            d["alt"], d["alt_conf"] = self.alt, round(self.alt_conf, 1)
        return d


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x3000 <= o <= 0x30FF


def group_lines(words: list[dict]) -> list[Line]:
    """把字依垂直位置併成行；同一行內依 x 排序。中文字之間不加空白、英文字之間加一個空白。"""
    ws = [w for w in words if (w.get("text") or "").strip()]
    ws.sort(key=lambda w: (w["top"] + w["height"] / 2, w["left"]))
    rows: list[list[dict]] = []
    # 字高差多少就不併在同一段：自己用座標組的行 1.45 倍；引擎（Tesseract）判定的同一行，中文字框本來就高低不一，放寬到 1.9 倍
    height_ratio = 1.45
    # 引擎自己有分行（Tesseract 的區塊-段落-行）就照它的：中文字的框高低不一，靠座標併行會拆散同一行
    if ws and all(w.get("line") for w in ws):
        by: dict[str, list[dict]] = {}
        for w in ws:
            by.setdefault(w["line"], []).append(w)
        rows = list(by.values())
        ws = []
        height_ratio = 1.9
    for w in ws:
        placed = False
        for r in rows:
            top = min(x["top"] for x in r)
            bot = max(x["top"] + x["height"] for x in r)
            h = bot - top
            # 垂直方向跟這一行重疊超過較矮那一方的一半，而且水平不重疊
            ov = min(bot, w["top"] + w["height"]) - max(top, w["top"])
            if ov >= 0.5 * min(h, w["height"]) and not any(
                    w["left"] < x["left"] + x["width"] and x["left"] < w["left"] + w["width"] for x in r):
                r.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    lines = []
    for r in rows:
        r.sort(key=lambda w: w["left"])
        # 同一列上相距很遠的兩段（例如左邊姓名、右邊電話）拆成兩行，免得值串在一起
        segs: list[list[dict]] = [[r[0]]]
        for w in r[1:]:
            prev = segs[-1][-1]
            gap = w["left"] - (prev["left"] + prev["width"])
            hh = max(prev["height"], w["height"], 1)
            lo = max(1, min(prev["height"], w["height"]))
            # 字高差很多的不併在同一行：名片上「小字職稱＋大字姓名」常排在同一列（實測：職稱跟姓名黏成一欄）
            if gap > 2.5 * hh or hh / lo > height_ratio:
                segs.append([w])
            else:
                segs[-1].append(w)
        for seg in segs:
            text = ""
            for w in seg:
                t = w["text"].strip()
                if text and not (_is_cjk(text[-1]) and _is_cjk(t[0])):
                    text += " "
                text += t
            left = min(w["left"] for w in seg)
            top = min(w["top"] for w in seg)
            right = max(w["left"] + w["width"] for w in seg)
            bottom = max(w["top"] + w["height"] for w in seg)
            confs = [float(w.get("conf") or 0) for w in seg]
            heights = sorted(w["height"] for w in seg)
            lines.append(Line(text=text, box=[left, top, right - left, bottom - top],
                              conf=sum(confs) / len(confs), height=heights[len(heights) // 2], words=seg))
    lines.sort(key=lambda ln: (ln.box[1], ln.box[0]))
    return lines


def _quality(words: list[dict]) -> float:
    """方向挑選用：信心×可讀字數（中英數字），拿反的那一面幾乎都是亂碼、信心低。"""
    total = 0.0
    for w in words:
        t = w.get("text") or ""
        good = sum(1 for ch in t if ch.isalnum() or _is_cjk(ch))
        total += good * max(0.0, float(w.get("conf") or 0)) / 100.0
    return total


def ocr_card(img: Image.Image, *, langs: str = DEFAULT_LANGS, engine: str | None = None,
             try_rotations: bool = True) -> dict:
    """回 `{"lines", "rotation", "engine", "image"}`；`image` 是轉好方向的卡面（之後的框位都以它為準）。"""
    from ...core import ocr_engine
    w, h = img.size
    rots = ([0, 180] if w >= h else [0, 90, 270]) if try_rotations else [0]
    best = None
    tried = 0
    while tried < len(rots):
        rot = rots[tried]
        tried += 1
        im = img.rotate(-rot, expand=True) if rot else img
        try:
            words, used = ocr_engine.recognize_image(
                _png(im), langs, engine=engine,
                allow_local_easyocr=ocr_engine.local_easyocr_safe())
        except Exception:                                  # noqa: BLE001 —— 一張失敗不可以拖垮整批
            log.exception("名片 OCR 失敗")
            words, used = [], "none"
        q = _quality(words)
        if best is None or q > best[0]:
            best = (q, rot, words, used, im)
        # 第一個方向就很好（大部分卡片沒拿反）：不用再試，省一半時間
        if (rot == 0 or (rot == 90 and w >= h)) and q >= 30:       # 橫放的直式名片 90° 就讀得好也不用再試 270°
            break
        # 橫的框 0° 與 180° 都讀不太出來：可能是直式名片橫放（掃描時常見）→ 再試 90° 與 270°
        if try_rotations and tried == len(rots) and w >= h and 90 not in rots and best[0] < 30:
            rots = rots + [90, 270]
    q, rot, words, used, im = best
    lines = group_lines(words)
    if used != "none" and VERTICAL_LANGS & set(langs.split("+")):
        try:
            lines = _vertical_pass(lines, im, langs, engine)
        except Exception:                                  # noqa: BLE001 —— 直書那一輪失敗就用一般讀法
            log.exception("名片 OCR 直書補讀失敗")
    if used != "none" and "jpn" in langs.split("+"):
        try:
            _latin_pass(lines, im, engine)
        except Exception:                                  # noqa: BLE001 —— 英文那一輪失敗就用日文模型讀的
            log.exception("名片 OCR 英文補讀失敗")
    if used != "none":
        try:
            _reread_low_lines(lines, im, langs, engine)
        except Exception:                                  # noqa: BLE001 —— 補讀失敗就用第一次的結果
            log.exception("名片 OCR 補讀失敗")
    return {"lines": lines, "rotation": rot, "engine": used, "image": im, "quality": q}


def _looks_like_texture(text: str) -> bool:
    """底紋、照片、密集線條被讀成的字：EasyOCR 常讀成一串「圖」（「回圖圖」「主者主圖圖圖」「圖圖啞」，
    台灣真實樣本實測）。一欄裡「圖」佔中日文字三分之一以上就當作不是字；比例沒那麼高的（「國國圖中」）
    靠的是「貼著卡片邊緣」那一道。"""
    cjk = [ch for ch in text if _is_cjk(ch)]
    return bool(cjk) and cjk.count("圖") * 3 >= len(cjk)


def _vertical_pass(lines: list[Line], im: Image.Image, langs: str, engine) -> list[Line]:
    """直書（縦書き）名片：找出直排的欄、排成橫的一行再讀（見 `vertical.py`）。
    至少兩欄讀出中日文字才算直書名片；被那些欄蓋住的原本的行（直排被切成的碎片）換掉。
    直欄排在前面、由右到左（直書的閱讀順序），橫排的行（Email、網址）照原本的順序接在後面。"""
    from . import vertical
    # 橫排的字上下疊起來也像一條直欄（Email 與網址的字母、橫式名片上「公司／部門／姓名」幾行）：
    # 跟「讀得好的橫排行」重疊的不算。直式名片上原本的讀法只有一兩個字的碎片，不會擋到真的直欄
    # 證據用「單一個字框」：真的橫排字 EasyOCR 會整段框成一個（「有限会社アオバ」）；直書被切成的碎片一框只有
    # 一兩個字 —— 相鄰幾欄同一高度的碎字會被併成一「行」，用行來看會誤擋真的直欄
    wide = [ln.box for ln in lines if ln.box[2] >= 4 * max(1, ln.box[3]) and len(ln.text.strip()) >= 8]
    wide += [[w["left"], w["top"], w["width"], w["height"]] for ln in lines for w in ln.words
             if sum(1 for ch in (w.get("text") or "") if _is_cjk(ch)) >= 3 and float(w.get("conf") or 0) >= 50
             and w["width"] >= w["height"]]
    cols = [c for c in vertical.columns(im) if not any(
        min(c.box[0] + c.box[2], x + w) > max(c.box[0], x) and min(c.box[1] + c.box[3], y + h) > max(c.box[1], y)
        for x, y, w, h in wide)]
    if len(cols) < 2:
        return lines
    read = _read_strips(im, cols, langs, engine)
    # 讀出來信心極低的欄不採用（底紋讀成的亂碼常在 10 以下）。門檻不可以拉高：「携帯」常被讀錯，
    # 整欄平均會掉到 20～40，拉到 40 時合成直式卡的手機欄有五張整欄不見（貼著邊緣的誤觸另外擋）
    keep = [(c, t, cf) for c, t, cf in read if cf >= 20 and not _looks_like_texture(t)
            and (sum(1 for ch in t if _is_cjk(ch)) >= 2 or len(re.sub(r"\W", "", t)) >= 4)]
    if sum(1 for _c, t, _cf in keep if sum(1 for ch in t if _is_cjk(ch)) >= 2) < 2:
        return lines
    def covered(ln: Line) -> bool:
        # 碰到任何一欄就換掉：直排被切成的碎片常常橫跨好幾欄（「童馨」是職稱與讀音兩欄的字拼起來的）。
        # 但讀得好的橫排行一律留著（直書名片上原本的讀法只有一兩個字的碎片，到不了這個門檻）
        x, y, w, h = ln.box
        if w >= h and ln.conf >= 60 and len(re.sub(r"\s", "", ln.text)) >= 3:
            return False
        for c, _t, _cf in keep:
            cx, cy, cw, ch = c.box
            m = c.size * 0.5
            if min(x + w, cx + cw + m) > max(x, cx - m) and min(y + h, cy + ch + m) > max(y, cy - m):
                return True
        return False
    # 直書的讀法要明顯比被換掉的原讀法好才換（信心 × 可讀字數）
    qual = lambda t, cf: sum(1 for ch in t if ch.isalnum() or _is_cjk(ch)) * max(0.0, cf) / 100.0
    q_new = sum(qual(t, cf) for _c, t, cf in keep)
    q_old = sum(qual(ln.text, ln.conf) for ln in lines if covered(ln))
    if q_new <= 1.3 * q_old:
        return lines
    out = [Line(text=t, box=list(c.box), conf=cf, height=int(round(c.size)), vertical=True) for c, t, cf in keep]
    return out + [ln for ln in lines if not covered(ln)]


def _read_strips(im: Image.Image, cols, langs: str, engine) -> list[tuple]:
    """各欄排成的一行疊成一張圖讀一次，依上下位置分回各欄。回 [(欄, 文字, 信心)]（讀不到的欄不列）。"""
    from ...core import ocr_engine
    from . import vertical
    strips = [vertical.strip_image(im, c) for c in cols]
    gap = 40
    cw = max(s.width for s in strips) + 40
    canvas = Image.new("RGB", (cw, sum(s.height for s in strips) + gap * (len(strips) + 1)), (255, 255, 255))
    spans, y = [], gap
    for c, s in zip(cols, strips):
        canvas.paste(s, (20, y))
        spans.append((c, y, y + s.height))
        y += s.height + gap
    words, _used = ocr_engine.recognize_image(_png(canvas), langs, engine=engine,
                                              allow_local_easyocr=ocr_engine.local_easyocr_safe())
    out = []
    for c, y0, y1 in spans:
        got = sorted((w for w in words if (w.get("text") or "").strip()
                      and y0 <= w["top"] + w["height"] / 2 <= y1), key=lambda w: w["left"])
        if not got:
            continue
        text = ""
        for w in got:
            t = w["text"].strip()
            text += ("" if not text or (_is_cjk(text[-1]) and _is_cjk(t[0])) else " ") + t
        conf = sum(float(w.get("conf") or 0) for w in got) / len(got)
        out.append((c, vertical.clean_text(text), conf))
    return out


def _latin_pass(lines: list[Line], im: Image.Image, engine) -> int:
    """日文名片：整張再用英文模型讀一次，英數那幾行（英文姓名、Email、網址、電話）換成英文模型的讀法。

    實測（遠端 GPU EasyOCR）：日文模型讀英數很差 —— 空格全不見（「NaokiKobayashi」）、M 認成「NI」、
    m 認成「nn」、Email 與網址的點不見（「examplejp」）。英文模型讀同一行幾乎都對。只換「沒有漢字、假名」的行，
    日文的行照樣用日文模型讀的。回換掉的行數。
    """
    from ...core import ocr_engine
    words, _used = ocr_engine.recognize_image(_png(im), "eng", engine=engine,
                                              allow_local_easyocr=ocr_engine.local_easyocr_safe())
    return merge_latin(lines, group_lines(words))


def merge_latin(lines: list[Line], en_lines: list[Line]) -> int:
    """`lines`（日文模型）裡英數的行，換成位置重疊的 `en_lines`（英文模型）的讀法。"""
    n = 0
    for ln in lines:
        if ln.vertical or sum(1 for ch in ln.text if _is_cjk(ch)) > 1:
            continue
        if not re.search(r"[A-Za-z@]", ln.text) or len(ln.text.strip()) < 3:
            continue
        x, y, w, h = ln.box
        hits = []
        for e in en_lines:
            ex, ey, ew, eh = e.box
            vo = min(y + h, ey + eh) - max(y, ey)
            ho = min(x + w, ex + ew) - max(x, ex)
            if vo >= 0.5 * min(h, eh) and ho > 0.3 * min(w, ew):
                hits.append(e)
        if not hits:
            continue
        hits.sort(key=lambda e: e.box[0])
        text = " ".join(e.text for e in hits).strip()
        if not text or text == ln.text or any(_is_cjk(ch) for ch in text):
            continue
        conf = sum(e.conf for e in hits) / len(hits)
        # 網址與 Email：英文模型信心常只有 20～35（點與「//」讀不準），但字母幾乎都對；日文模型讀的通常更糟
        if conf < (20 if re.search(r"https?|www|@", text, re.I) else 40):
            continue
        ln.text, ln.conf = text, conf              # 日文模型的讀法通常是錯的，不放進「另一種讀法」
        n += 1
    return n


#: 信心低於這個的行再讀一次（跟擷取那邊標「辨識信心低」的門檻一致）
REREAD_BELOW = 55.0
#: 一張卡最多補讀幾行（OCR 一次要時間；信心低的行通常不多）
REREAD_MAX = 8


def _reread_low_lines(lines: list[Line], im: Image.Image, langs: str, engine) -> int:
    """信心低的行：把那幾行裁出來放大（字高約 80 px）再讀一次，**當作另一種讀法給人參考**（`alt`），不自動換。

    整張卡一起讀時，小字、書法字、印得淡的字常被認錯（「疊」認成形近字），單獨放大那一行常常看得比較清楚。
    但真實名片實測（183 張、581 行信心低）自動換的話好壞參半：會換對字，也會插進雜字、把對的字改錯、把三個字的
    姓名讀成兩個字 —— 換錯了「信心低」的標記還會跟著消失。所以只放在校對畫面上，按一下才套用。
    **幾行疊成一張圖只多讀一次**（一張卡多一次 OCR），讀完依上下位置分回各行。回有另一種讀法的行數。
    """
    from ...core import ocr_engine
    todo = sorted((ln for ln in lines if not ln.vertical and ln.conf and ln.conf < REREAD_BELOW
                   and len(ln.text.strip()) >= 2),
                  key=lambda ln: ln.conf)[:REREAD_MAX]
    if not todo:
        return 0
    W, H = im.size
    crops: list[tuple[Line, Image.Image]] = []
    for ln in todo:
        x, y, w, h = ln.box
        pad = max(4, int(h * 0.25))
        box = (max(0, x - pad), max(0, y - pad), min(W, x + w + pad), min(H, y + h + pad))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            continue
        crop = im.crop(box).convert("RGB")
        scale = max(1.0, min(4.0, 80.0 / max(1, h)))
        if scale > 1.05:
            crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
        crops.append((ln, crop, (y - box[1]) * scale, h * scale))   # 那一行在裁圖裡的上緣與高度
    if not crops:
        return 0
    gap = 60
    cw = max(c.width for _, c, _, _ in crops) + 40
    ch = sum(c.height for _, c, _, _ in crops) + gap * (len(crops) + 1)
    canvas = Image.new("RGB", (cw, ch), (255, 255, 255))
    spans = []
    y = gap
    for ln, c, top, hh in crops:
        canvas.paste(c, (20, y))
        spans.append((ln, y + top, y + top + hh))         # 只收那一行本身的範圍（上下留白裡是鄰行的字）
        y += c.height + gap
    try:
        words, _used = ocr_engine.recognize_image(_png(canvas), langs, engine=engine,
                                                  allow_local_easyocr=ocr_engine.local_easyocr_safe())
    except Exception:                                      # noqa: BLE001
        return 0
    changed = 0
    for ln, y0, y1 in spans:
        hh = y1 - y0
        got = [w for w in words if (w.get("text") or "").strip()
               and y0 - 0.3 * hh <= w["top"] + w["height"] / 2 <= y1 + 0.3 * hh]
        if not got:
            continue
        # 一張裁圖就是一行：照左到右接起來（再分行會把同一行的字排亂）
        got.sort(key=lambda w: w["left"])
        text = ""
        for w in got:
            t = w["text"].strip()
            text += ("" if not text or (_is_cjk(text[-1]) and _is_cjk(t[0])) else " ") + t
        confs = [float(w.get("conf") or 0) for w in got]
        conf = sum(confs) / len(confs) if confs else 0.0
        old = ln.text.strip()
        # 只接受「換字」不接受「少字」：真實名片實測補讀有時把三個字的姓名讀成兩個字（信心反而更高）。
        # 中文字數不可以變少、整體字數至少九成；變長太多多半是讀到旁邊的東西
        nn = lambda x: len(re.sub(r"\s", "", x))
        cjk = lambda x: sum(1 for c in x if _is_cjk(c))
        if not text or cjk(text) < cjk(old) or nn(text) < 0.9 * nn(old) or nn(text) > 1.6 * nn(old) + 1:
            continue
        # 只差標點或空白：沒有好處，而且常常是把網址的點、地址的逗號弄丟
        core = lambda x: re.sub(r"[\W_]", "", x)
        if core(text) == core(old):
            continue
        if conf >= ln.conf + 8:
            ln.alt, ln.alt_conf = text, conf
            changed += 1
    return changed
