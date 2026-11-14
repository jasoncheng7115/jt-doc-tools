"""名片辨識：一張已拉正的卡面 → OCR → 文字行（含框位）。

* 走共用的 `app/core/ocr_engine.recognize_image`（EasyOCR / Tesseract / 遠端 GPU，跟其他工具同一套設定與退路）。
* **方向**：卡面可能倒放（拍照時拿反）或直式。橫式卡試 0° 與 180°，直式卡試 0° / 90° / 270°，挑「信心×字數」最高的。
  直式卡片本來就可能是直的（直書名片），所以直式不強制轉成橫式。
* 字（word）依垂直重疊併成行；行的框位是卡面座標（拉正後的裁圖），校對畫面用來框出原文。
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


@dataclass
class Line:
    text: str
    box: list[int]                 # [left, top, width, height]，卡面座標
    conf: float                    # 0..100；引擎沒給就是 0
    height: int = 0                # 字高（判斷姓名等大字用）
    words: list[dict] = field(default_factory=list)
    alt: str = ""                  # 信心低的行放大再讀一次的另一種讀法（只給人參考，不自動換）
    alt_conf: float = 0.0

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
    for rot in rots:
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
        if rot == 0 and q >= 30:
            break
    q, rot, words, used, im = best
    lines = group_lines(words)
    if used != "none":
        try:
            _reread_low_lines(lines, im, langs, engine)
        except Exception:                                  # noqa: BLE001 —— 補讀失敗就用第一次的結果
            log.exception("名片 OCR 補讀失敗")
    return {"lines": lines, "rotation": rot, "engine": used, "image": im, "quality": q}


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
    todo = sorted((ln for ln in lines if ln.conf and ln.conf < REREAD_BELOW and len(ln.text.strip()) >= 2),
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
