"""直書（縦書き / 直排）名片：找出直排的欄、把每一欄的字切開排成橫的一行，再交給 OCR。

EasyOCR（與 Tesseract 的一般模式）只讀橫排：直排的欄會被切成「目」「豊」「9」這種碎片，整張讀不出東西。
做法：

1. **找欄**（不靠 OCR 的字框，直接看影像）：墨水往上下方向黏起來，細長、直立的區塊就是一欄；
   同一欄被全形空白隔開的幾段（「山田　太郎」）依左右重疊接回來。
2. **切字**：直書的字一個佔一格、格子一樣大（等距），所以不用「空白處就切」（「三」「二」字中間就有空白），
   而是找一組等距的切線、讓切線盡量落在空白上。沒有墨水的格子是空白（全形空格）。
3. **排成一行**：每個字照原樣（不轉）由左到右排；直書的「︱」（長音、破折號）轉 90° 變回橫的。
   所有欄疊在同一張圖上只讀一次，讀完依上下位置分回各欄。

結果是一般的 `Line`：框位是那一欄在卡面上的位置、`height` 是字的大小（判斷姓名等大字用），
順序由右到左（直書的閱讀順序）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

#: 一欄至少要有幾個字才算（兩個字的職稱「室長」也要算）
MIN_CHARS = 2
#: 直排的欄至少要多細長（高 / 寬）
MIN_ASPECT = 1.6


@dataclass
class Column:
    box: tuple[int, int, int, int]       # left, top, width, height（卡面座標）
    size: float                          # 字的大小（px）
    cells: list[tuple[int, int]]         # 每一格的上下（卡面座標）；空白格是 (y, y)


def ink_mask(img: Image.Image) -> np.ndarray:
    """墨水遮罩（白底黑字為主；Otsu，背景不均時退回自適應）。"""
    g = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    _t, m = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if m.mean() / 255 > 0.35:            # 深色底或大塊色塊：Otsu 會把底色當墨水
        m = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    return m


def find_columns(img: Image.Image, m: np.ndarray | None = None) -> list[tuple[int, int, int, int]]:
    """直排的欄（left, top, width, height），由右到左。`m`：已經算好的墨水遮罩。"""
    if m is None:
        m = ink_mask(img)
    H, W = m.shape
    # 墨水往上下黏：字與字之間的空白（約 0.1～0.3 字高）要黏得起來，左右不黏
    k = max(5, int(min(W, H) * 0.035))
    closed = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(closed, 8)
    blobs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 6 or h < 10 or area < 30:
            continue
        blobs.append([x, y, w, h])
    # 同一欄被空白隔開的幾段接起來：左右重疊七成以上、寬度差不多、上下距離在兩個字以內
    blobs.sort(key=lambda b: (b[0], b[1]))
    merged: list[list[int]] = []
    for b in sorted(blobs, key=lambda b: b[1]):
        for c in merged:
            ov = min(b[0] + b[2], c[0] + c[2]) - max(b[0], c[0])
            wmax = max(b[2], c[2])
            gap = b[1] - (c[1] + c[3])
            if ov >= 0.7 * min(b[2], c[2]) and min(b[2], c[2]) >= 0.5 * wmax and -0.2 * wmax <= gap <= 3.0 * wmax:
                x0, y0 = min(b[0], c[0]), min(b[1], c[1])
                x1, y1 = max(b[0] + b[2], c[0] + c[2]), max(b[1] + b[3], c[1] + c[3])
                c[:] = [x0, y0, x1 - x0, y1 - y0]
                break
        else:
            merged.append(list(b))
    cand = []
    # 最小的字也有卡片短邊的 1.5% 寬（名片上 6pt 的字約 2.5%）；更細的是底紋、線條、照片裡的紋路
    # （台灣真實樣本實測：8～13 px 寬、卡面 1400 px 的細條被讀成「一一團呈轉圖點一」）
    min_w = max(8.0, 0.015 * min(W, H))
    for x, y, w, h in merged:
        if h < MIN_ASPECT * w or w > 0.25 * W or w < min_w:
            continue
        # 貼著卡片邊緣的是邊框、照片或裁切時帶到的背景：真的字一定離邊緣有留白（台灣真實樣本實測誤觸）
        if x <= 0.02 * W or x + w >= 0.98 * W or y <= 0.005 * H or y + h >= 0.995 * H:
            continue
        if h < MIN_CHARS * w * 0.9:
            continue
        # 橫排文字裡的字母（l、p、j）黏成的細條：左右緊貼著別的墨水。直排的欄跟隔壁欄至少隔半個字
        d = int(min(10, max(5, 0.5 * w)))
        rows = np.zeros(h, bool)
        for xa, xb in ((max(0, x - d), x), (x + w, min(W, x + w + d))):
            if xb > xa:
                rows |= m[y:y + h, xa:xb].any(axis=1)
        if rows.mean() > 0.4:
            continue
        cand.append((int(x), int(y), int(w), int(h)))
    # 大字裡的筆畫（「川」中間那一筆）自己也是細長的一條：被別的欄包住的不算
    cols = [c for c in cand if not any(
        o is not c and o[2] > c[2] and o[0] <= c[0] and c[0] + c[2] <= o[0] + o[2]
        and o[1] <= c[1] + c[3] * 0.5 <= o[1] + o[3] for o in cand)]
    cols.sort(key=lambda c: -(c[0] + c[2] / 2))
    return cols


def _period(prof: np.ndarray, size: float) -> float:
    """欄內墨水分布的週期（字距）：自相關在字寬的 0.9～2.2 倍之間最高的位移。字太少（看不出週期）回 0。"""
    x = prof.astype(float)
    if len(x) < 3 * size:
        return 0.0
    x = x - x.mean()
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    if ac[0] <= 0:
        return 0.0
    a, b = int(size * 0.9), min(len(ac) - 2, int(size * 2.2))
    if b <= a:
        return 0.0
    top = ac[a:b + 1].max()
    if top / ac[0] < 0.15:
        return 0.0
    # 字距的整數倍也是高峰：挑最小的那個（跟最高的差不多高就算）
    for k in range(a, b + 1):
        if ac[k] >= 0.75 * top and ac[k] >= ac[k - 1] and ac[k] >= ac[k + 1]:
            return float(k)
    return float(a + int(np.argmax(ac[a:b + 1])))


def split_cells(mask_col: np.ndarray, size: float) -> list[tuple[int, int]]:
    """一欄（遮罩裁圖）切成等距的格子：找字距 p 與起點，讓切線上的墨水最少。回每格 (上, 下)，相對裁圖。"""
    H = mask_col.shape[0]
    prof = (mask_col > 0).sum(axis=1).astype(float)
    if H < 2 or prof.sum() == 0:
        return []
    rows = np.nonzero(prof)[0]
    top, bot = int(rows[0]), int(rows[-1]) + 1
    span = bot - top
    best = None
    lag = _period(prof[top:bot], size)
    # 一個字大概多高：墨水連續段的中位數（全形數字、英文字母比欄寬高；用欄寬當字高會誤判成「一格兩個字」）
    runs, r = [], 0
    for v in prof[top:bot]:
        if v:
            r += 1
        elif r:
            runs.append(r)
            r = 0
    if r:
        runs.append(r)
    glyph = max(size, float(np.median(runs))) if runs else size
    # 字距：字寬的 0.95～1.6 倍之間，加上週期附近（全形英數的墨水比字身窄很多，字距會超過 1.6 倍）。
    # 週期有時抓到兩倍字距（括號這種墨水少的字），所以只當作多找的範圍；一格塞兩個字由下面的懲罰排除
    step = max(0.5, size * 0.02)
    pitches = set(np.round(np.arange(max(4.0, size * 0.95), size * 1.6 + 0.01, step), 1))
    if lag:
        pitches |= set(np.round(np.arange(lag * 0.9, lag * 1.1 + 0.01, step), 1))
    for p in sorted(pitches):
        n = max(1, int(round(span / p)))
        if abs(n * p - span) > 0.6 * p:          # 格數要剛好把整欄蓋滿（最後一格可以短一點）
            continue
        for off in np.arange(-0.5 * (p - size), 0.5 * (p - size) + 0.01, 1.0) if p > size else [0.0]:
            cuts = [top + off + p * i for i in range(1, n)]
            # 切線落在墨水上的比例（字與字之間是全白的；切到字，即使是「0」中間那種只有兩條細邊，也算）
            cost = 0.0
            for c in cuts:
                ci = int(round(c))
                if 0 <= ci < H and prof[max(0, ci - 1):ci + 2].any():
                    cost += 1
            # 每格的墨水不可以超過一個字高（字距太大 → 一格塞兩個字）
            over = 0.0
            edges = [top + off] + cuts + [top + off + p * n]
            for a, b in zip(edges, edges[1:]):
                seg = prof[max(0, int(a)):max(0, int(b))]
                nz = np.nonzero(seg)[0]
                if len(nz) and nz[-1] - nz[0] > 1.25 * glyph:
                    over += 1
            score = (cost / max(1, len(cuts)) + over) * size + 0.02 * p      # 同分時挑字距小的（不要兩個字一格）
            if best is None or score < best[0]:
                best = (score, p, off, n)
    if best is None:
        return [(top, bot)]
    _s, p, off, n = best
    # 每一刀往附近最近的空白列靠（字距差一點點，切到後面就會累積成半個字）
    edges = [top + off + p * i for i in range(n + 1)]
    for i in range(1, n):
        c = int(round(edges[i]))
        r = int(p * 0.35)
        lo_, hi_ = max(int(edges[i - 1]) + 1, c - r), min(H - 1, c + r)
        blank = [y for y in range(lo_, hi_ + 1) if prof[y] == 0]
        if blank:
            edges[i] = min(blank, key=lambda y: abs(y - c))
            # 後面的切線跟著平移（誤差是累積的）
            shift = edges[i] - c
            for j in range(i + 1, n + 1):
                edges[j] += shift
    cells = []
    for i in range(n):
        a = int(round(edges[i]))
        b = int(round(edges[i + 1]))
        a, b = max(0, a), min(H, b)
        seg = prof[a:b]
        nz = np.nonzero(seg)[0]
        cells.append((a + int(nz[0]), a + int(nz[-1]) + 1) if len(nz) else (a, a))
    return cells


def columns(img: Image.Image) -> list[Column]:
    m = ink_mask(img)
    out = []
    for x, y, w, h in find_columns(img, m):
        sub = m[y:y + h, x:x + w]
        size = float(w)
        cells = [(y + a, y + b) for a, b in split_cells(sub, size)]
        if sum(1 for a, b in cells if b > a) < MIN_CHARS:
            continue
        out.append(Column(box=(x, y, w, h), size=size, cells=cells))
    return out


#: 排成一行時每個字的高度（EasyOCR 在 32～64 px 讀得最好）
STRIP_H = 48


def strip_image(img: Image.Image, col: Column) -> Image.Image:
    """一欄排成橫的一行（白底）。直書的「︱」轉 90° 變橫的；空白格留一個字的空。"""
    x, _y, w, _h = col.box
    s = STRIP_H / max(1.0, col.size)
    pad = int(STRIP_H * 0.25)
    pieces: list[Image.Image | None] = []
    gray = img.convert("L")
    for a, b in col.cells:
        if b <= a:
            pieces.append(None)
            continue
        cell = gray.crop((x, a, x + w, b))
        if is_vertical_bar(np.asarray(cell), col.size):
            cell = cell.rotate(90, expand=True, fillcolor=255)
        nw, nh = max(1, int(cell.width * s)), max(1, int(cell.height * s))
        pieces.append(cell.resize((nw, nh), Image.LANCZOS))
    gap = int(STRIP_H * 0.12)
    width = pad * 2 + sum((p.width if p else STRIP_H) + gap for p in pieces)
    canvas = Image.new("L", (max(width, 10), STRIP_H + pad * 2), 255)
    cx = pad
    for p in pieces:
        if p is None:
            cx += STRIP_H + gap
            continue
        canvas.paste(p, (cx, pad + (STRIP_H - p.height) // 2))
        cx += p.width + gap
    return canvas.convert("RGB")


def is_vertical_bar(cell: np.ndarray, size: float) -> bool:
    """這一格是不是直書的「︱」（長音、破折號），排成橫的時要轉 90°。
    只有細細一條線（1～2 px）；「1」上面有一撇、下面常有襯線（6 px 以上），不算。
    用淺色門檻量：細線在模糊的照片上很淡，而「1」的那一撇與襯線也要算進寬度。"""
    ink = cell < 200
    if not ink.any():
        return False
    ys, xs = np.nonzero(ink)
    iw, ih = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    return bool(iw <= max(2, min(4, 0.22 * size)) and ih > 0.45 * size)


def clean_text(text: str) -> str:
    """全形英數與符號換成半形（直書常用全形數字）；中日文照舊。"""
    out = []
    for ch in text.replace("︱", "-").replace("︲", "-"):
        n = unicodedata.normalize("NFKC", ch)
        out.append(n if n.isascii() and n.strip() else ch)
    text = "".join(out)
    # 直書的破折號轉成橫的之後，OCR 常讀成「一」「ー」「|」；格子之間的空隙讀成空白（「0 8 0」）
    text = re.sub(r"(?<=\d)\s*[-一ー|丨—–_~～]+\s*(?=\d)", "-", text)
    text = re.sub(r"(^|\s)[_~～]+(?=\d)", r"\1", text)              # 「電話 ~03-…」：號碼前面多出來的那一槓
    # 地址的門牌：0 讀成 o / O、數字與「號 / 樓」之間多了空白（「10 o 號 8 樓」）
    text = re.sub(r"(?<=\d)\s*[oO〇]\s*(?=[\d號樓段巷弄之]|$)", "0", text)
    text = re.sub(r"(?<=\d)\s+(?=[號樓段巷弄])|(?<=[號樓段巷弄])\s+(?=\d)", "", text)
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    # 直書的「ＦＡＸ」一個字一格，F 常被讀成「上」「ド」；直書的括號（︵︶）讀成「一」「ヘ」
    text = re.sub(r"^\s*[上ドトF]\s*[AＡ]\s*[XxＸ×]", "FAX", text)
    text = re.sub(r"^\s*[一ヘへ︵(（]?\s*([株有])\s*[一ヘへ︶)）ー~〜ン-]", r"（\1）", text)
    text = re.sub(r"^\s*['`’‘]{1,2}\s*EL(?=\s|\d)", "TEL", text)
    # 郵遞區號自己一欄：〒 常讀成「丁」「T」「7」「テ」，0 / 1 讀成 O / l，中間的「-」不見或變空白
    m = re.fullmatch(r"\s*[〒丁テT7干]\s*([0-9OoDlIi|]{3})\s*[-一ー]?\s*([0-9OoDlIi|]{4})\s*", text)
    if m:
        fix = str.maketrans("OoDlIi|", "0001111")
        text = "〒" + m.group(1).translate(fix) + "-" + m.group(2).translate(fix)
    return text
