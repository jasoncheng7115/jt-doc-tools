"""名片辨識：找出照片 / 掃描頁上的名片（四角框）並拉正。純函式，可測。

兩種輸入（規格第 2 節）：
* **每張圖片一張名片**：`detect_single()` 找最大的那張卡；找不到邊界就保留整張圖（不自行拆成多筆）；
  看起來有好幾張時回 `multi_hint`，畫面提示改用多張模式。
* **每張圖片多張名片**：`detect_cards()` 找出每一張的四角框（可以斜放、可以直式），依「上到下、左到右」編號。

做法：先估背景（影像外圈的顏色），把「跟背景差很多」的地方當前景，再補上邊緣（白卡放白底時顏色差不多，
只能靠卡片邊緣的那條線）；前景遮罩做開閉運算後找輪廓，每個輪廓用多邊形近似取四角（拍照有透視時是任意四邊形），
近似不出四角就用最小外接旋轉矩形。依面積與長寬比過濾（名片約 1.4～2.0，直式取倒數），太大或太小的不算。

**不猜**：兩張貼在一起的卡偵測成一個框時不自動切開（規格：使用者可以拆開誤連框）；零張就回空清單，不當成功。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

#: 名片長寬比（長邊 / 短邊）。90×54＝1.67、91×55＝1.65、85.6×54＝1.59、正方卡片少見但有（不收，免得把大頭貼當卡）
MIN_RATIO, MAX_RATIO = 1.25, 2.2
#: 一張卡面積佔整張圖的比例範圍（多卡）；太小多半是 Logo、太大是整張背景
MIN_AREA_FRAC, MAX_AREA_FRAC = 0.012, 0.92
#: 偵測在這個長邊上做（快、雜訊少）；座標再放大回原圖
WORK_EDGE = 1000


@dataclass
class Card:
    """一張卡的四角（原圖像素座標，順序：左上、右上、右下、左下）。"""
    quad: list[list[float]]
    score: float = 0.0
    source: str = "auto"            # auto / manual
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"quad": [[round(x, 1), round(y, 1)] for x, y in self.quad], "score": round(self.score, 3),
                "source": self.source, "flags": list(self.flags)}


def to_rgb_array(img: Image.Image) -> np.ndarray:
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return np.asarray(img, dtype=np.uint8)


def order_quad(pts) -> np.ndarray:
    """排成左上、右上、右下、左下（以質心角度排序，不受斜放影響）。"""
    p = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    c = p.mean(axis=0)
    ang = np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0])
    p = p[np.argsort(ang)]                       # -pi..pi：左上通常最先（角度約 -135°）
    # 讓「x+y 最小」的點當左上
    start = int(np.argmin(p.sum(axis=1)))
    return np.roll(p, -start, axis=0)


def quad_size(q) -> tuple[float, float]:
    q = np.asarray(q, dtype=np.float32)
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    return float(w), float(h)


def _ratio_ok(w: float, h: float) -> bool:
    if w <= 0 or h <= 0:
        return False
    r = max(w, h) / min(w, h)
    return MIN_RATIO <= r <= MAX_RATIO


def _background_distance(work: np.ndarray) -> np.ndarray:
    """每個像素跟背景（外圈中位數顏色，Lab 空間）的距離。"""
    lab = cv2.cvtColor(work, cv2.COLOR_RGB2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    b = max(4, int(min(h, w) * 0.03))
    border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3),
                             lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    return np.linalg.norm(lab - bg, axis=2)


def _clean(mask: np.ndarray, k: int) -> np.ndarray:
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=2)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8), iterations=1)


def _masks(work: np.ndarray) -> list[np.ndarray]:
    """幾種前景遮罩，各自產候選（不同背景適合不同的遮罩，最後靠評分挑）。

    * 跟背景的色差：深色 / 彩色桌面上的卡
    * 亮度 Otsu：一般白卡放深色桌面
    * 中性色＋夠亮（掃描修正 `_paper_mask` 的做法）：卡的一角落在陰影裡時 Otsu 會切掉那一角，影子不改色相
    * 邊緣圍出來的區塊：白卡放白桌，顏色幾乎一樣，只剩卡緣那條線
    """
    h, w = work.shape[:2]
    k = max(3, int(min(h, w) * 0.008) | 1)
    gray = cv2.cvtColor(work, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    out = []
    d8 = np.clip(_background_distance(work) * 2, 0, 255).astype(np.uint8)
    otsu, _ = cv2.threshold(cv2.GaussianBlur(d8, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(_clean(((d8 >= max(24.0, float(otsu))).astype(np.uint8) * 255), k))
    _t, m = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(_clean(m, k))
    lab = cv2.cvtColor(cv2.GaussianBlur(work, (7, 7), 0), cv2.COLOR_RGB2LAB)
    L, A, B = cv2.split(lab)
    chroma = np.hypot(A.astype(np.float32) - 128.0, B.astype(np.float32) - 128.0)
    ref = float(np.median(L[m > 0])) if (m > 0).any() else 255.0
    out.append(_clean(((chroma < 12) & (L > 0.45 * ref)).astype(np.uint8) * 255, k))
    edges = cv2.Canny(blur, 30, 90)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(closed)
    cv2.drawContours(filled, cnts, -1, 255, thickness=cv2.FILLED)
    out.append(_clean(filled, k))
    # 同樣的遮罩再做一次重的開運算：把卡跟桌面紋理、卡跟卡之間細細的連接處斷開（實拍：紙箱紋理連到卡緣、
    # 兩張卡在角落碰在一起）。斷開後輪廓會內縮一點，邊緣支持度會把不貼邊的候選淘汰掉。
    big = np.ones((k * 3, k * 3), np.uint8)
    out.extend(cv2.morphologyEx(m_, cv2.MORPH_OPEN, big) for m_ in out[:2])
    return out


def _quad_candidates(c: np.ndarray) -> list[np.ndarray]:
    """一個輪廓產幾個候選四邊形（多邊形近似掃 epsilon、最小外接旋轉矩形）—— 同掃描修正的做法。"""
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    out = []
    for pct in (x / 1000.0 for x in range(8, 121, 4)):
        ap = cv2.approxPolyDP(hull, pct * peri, True)
        if len(ap) == 4:
            out.append(ap.reshape(4, 2).astype(np.float32))
            break
    out.append(cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32))
    return out


def _gradient(work: np.ndarray) -> np.ndarray:
    g = cv2.GaussianBlur(cv2.cvtColor(work, cv2.COLOR_RGB2GRAY), (3, 3), 0).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    return cv2.dilate(mag, np.ones((5, 5), np.uint8))       # 容許框跟真正的邊差 2 個像素


def _side_support(grad: np.ndarray, q: np.ndarray, thr: float) -> list[float]:
    """四條邊各有多少比例落在明顯的亮度變化上（貼著照片邊緣的那條邊回 None＝無從判斷）。"""
    h, w = grad.shape
    out = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        if _on_border(a, b, w, h):
            out.append(None)
            continue
        n = max(8, int(np.linalg.norm(b - a) / 3))
        t = np.linspace(0.06, 0.94, n)                 # 角落常是圓角或陰影：兩頭不算
        xs = np.clip((a[0] + (b[0] - a[0]) * t).astype(int), 0, w - 1)
        ys = np.clip((a[1] + (b[1] - a[1]) * t).astype(int), 0, h - 1)
        out.append(float((grad[ys, xs] >= thr).mean()))
    return out


def _on_border(a, b, w, h, m: float = 4.0) -> bool:
    return ((a[0] <= m and b[0] <= m) or (a[1] <= m and b[1] <= m)
            or (a[0] >= w - 1 - m and b[0] >= w - 1 - m) or (a[1] >= h - 1 - m and b[1] >= h - 1 - m))


def quad_angle_range(q) -> float:
    """四個內角的極差（度）；卡片斜拍仍接近矩形，框到桌面的歪四邊形會很大（掃描修正同一條判準）。"""
    o = order_quad(q)
    ang = []
    for i in range(4):
        a, b, c = o[(i - 1) % 4], o[i], o[(i + 1) % 4]
        v1, v2 = a - b, c - b
        n = float(np.linalg.norm(v1) * np.linalg.norm(v2))
        if n < 1e-6:
            return 360.0
        ang.append(np.degrees(np.arccos(max(-1.0, min(1.0, float(np.dot(v1, v2) / n))))))
    return float(max(ang) - min(ang))


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    ua = abs(cv2.contourArea(a.astype(np.float32))) + abs(cv2.contourArea(b.astype(np.float32))) - inter
    return float(inter / ua) if ua > 0 else 0.0


def _candidates(work: np.ndarray, min_area_frac: float) -> list[tuple[float, np.ndarray, list[str]]]:
    """全部遮罩的全部輪廓 → 通過形狀檢查的候選 `(分數, 四角, flags)`。"""
    h, w = work.shape[:2]
    total = float(h * w)
    grad = _gradient(work)
    thr = max(18.0, float(np.percentile(grad, 70)))
    out = []
    for mask in _masks(work):
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:40]:
            ca = cv2.contourArea(c)
            if ca < min_area_frac * total or ca > MAX_AREA_FRAC * total:
                continue
            for q in _quad_candidates(c):
                q = order_quad(q)
                qa = abs(cv2.contourArea(q.reshape(-1, 1, 2)))
                if qa < min_area_frac * total or qa > MAX_AREA_FRAC * total:
                    continue
                if quad_angle_range(q) > 40:
                    continue
                fill = min(ca, qa) / max(ca, qa)
                if fill < 0.80:
                    continue
                # 相貼的卡片會連成一塊：先沿框內貫穿的直線拆開，拆出來的每一塊再各自檢查
                for piece in _split_touching(work, q):
                    pw, ph = quad_size(piece)
                    if not _ratio_ok(pw, ph):
                        continue
                    pa = abs(cv2.contourArea(piece.reshape(-1, 1, 2)))
                    if pa < min_area_frac * total:
                        continue
                    sup = _side_support(grad, piece, thr)
                    known = [x for x in sup if x is not None]
                    if len(known) < 2:                  # 四邊有三邊貼著照片邊緣：等於整張圖，不算偵測
                        continue
                    if min(known) < 0.45:
                        continue
                    flags = ["edge"] if len(known) < 4 else []
                    if piece is not q:
                        flags.append("split")
                    score = 0.6 * float(np.mean(known)) + 0.4 * fill
                    out.append((score, piece, flags))
    return out


#: 拆開相貼卡片的那條線：框內這一欄（或列）上，有邊緣的像素要佔幾成
SPLIT_LINE_FRAC = 0.72


def _split_touching(work: np.ndarray, q: np.ndarray, depth: int = 0) -> list[np.ndarray]:
    """框內有一條「從頭到尾都有明顯邊緣」的直線，而且拆開後兩邊都像名片 → 拆開；否則原樣回傳。

    卡片相貼的交界一定是一條貫穿的直線（兩張卡的邊）；文字、Logo、色塊的邊不會從頭貫穿到尾，
    就算剛好有（例如卡面上的色帶分界），拆出來的比例也多半不像名片，會被比例檢查擋掉。
    """
    qw, qh = quad_size(q)
    if depth > 3 or min(qw, qh) < 30:
        return [q]
    W, H = int(round(qw)), int(round(qh))
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], np.float32)
    m = cv2.getPerspectiveTransform(q.astype(np.float32), dst)
    rect = cv2.warpPerspective(cv2.cvtColor(work, cv2.COLOR_RGB2GRAY), m, (W, H), flags=cv2.INTER_LINEAR)
    g = cv2.GaussianBlur(rect, (3, 3), 0).astype(np.float32)
    best = None
    for axis in (0, 1):                                  # 0：直線（左右拆）；1：橫線（上下拆）
        gg = g if axis == 0 else g.T                     # 一律當成「找直的那條線」
        n_rows, n = gg.shape
        lo, hi = max(6, int(n * 0.2)), min(n - 6, int(n * 0.8))
        if hi <= lo:
            continue
        # 交界有兩種樣子：
        # * 脊線（細縫、陰影、紙邊）：線上跟兩側的平均差很多，兩側彼此接近 —— 最可靠；
        # * 一階跳變（白卡緊貼藍卡）：兩側差很多。卡面上的色塊分界也長這樣（實拍：左藍右白的卡面），
        #   所以跳變打折、脊線優先；色塊分界切出來的比例多半不像名片，會被下面的比例檢查擋掉。
        # 一次算完所有候選的直線（原本逐欄迴圈，一張照片要 1 秒；向量化後算出來的值完全一樣）
        ridge = np.zeros(n, np.float32)
        idx = np.arange(lo, hi)
        L = gg[:, idx - 5]
        R = gg[:, idx + 5]
        mid = (L + R) / 2
        dev = np.maximum.reduce([np.abs(gg[:, idx + d] - mid) for d in (-1, 0, 1)])
        half = np.abs(L - R)
        r = ((dev - half / 2) >= 18).mean(axis=0)
        st = (half >= 40).mean(axis=0)
        ridge[lo:hi] = np.maximum(r, 0.85 * st)
        i = lo + int(np.argmax(ridge[lo:hi]))
        if ridge[i] < SPLIT_LINE_FRAC:
            continue
        a_len, b_len = i, n - i
        other = n_rows
        # 兩邊都要像名片，或是「沿同一方向還能再拆」的長條（三張並排時先拆出一張、剩兩張連著）
        if not all(_ratio_ok(x, other) or (x / max(other, 1) > MAX_RATIO * 0.55 and x > other)
                   for x in (a_len, b_len)):
            continue
        if best is None or ridge[i] > best[0]:
            best = (ridge[i], axis, i)
    if best is None:
        return [q]
    _f, axis, i = best
    inv = np.linalg.inv(m)
    if axis == 0:
        halves = [np.array([[0, 0], [i, 0], [i, H - 1], [0, H - 1]], np.float32),
                  np.array([[i, 0], [W - 1, 0], [W - 1, H - 1], [i, H - 1]], np.float32)]
    else:
        halves = [np.array([[0, 0], [W - 1, 0], [W - 1, i], [0, i]], np.float32),
                  np.array([[0, i], [W - 1, i], [W - 1, H - 1], [0, H - 1]], np.float32)]
    out = []
    for h_ in halves:
        back = cv2.perspectiveTransform(h_.reshape(-1, 1, 2), inv).reshape(4, 2)
        out.extend(_split_touching(work, order_quad(back), depth + 1))
    return out


def _pick(cands: list[tuple[float, np.ndarray, list[str]]]) -> list[tuple[float, np.ndarray, list[str]]]:
    """同一張卡會被好幾種遮罩各抓一次：重疊的留分數最高的。"""
    keep: list[tuple[float, np.ndarray, list[str]]] = []
    for c in sorted(cands, key=lambda x: x[0], reverse=True):
        dup = [i for i, k in enumerate(keep) if _iou(c[1], k[1]) >= 0.5]
        if dup:
            # 幾乎同一張：卡面上的色帶、印刷框線也是一條很強的直線，會產生「內縮一截」的候選。
            # 卡片真正的邊在最外圈 → 外面那個的分數沒有差太多就換成外面那個（合成：上方色帶被當成上緣，差 8%）
            i = dup[0]
            k = keep[i]
            area_c = abs(cv2.contourArea(c[1].reshape(-1, 1, 2)))
            area_k = abs(cv2.contourArea(k[1].reshape(-1, 1, 2)))
            # 外圈要四條邊都看得到邊緣才換：貼著照片邊緣的邊無從驗證，分數會虛高（實拍：換成外圈後框進一大片桌面）
            if (area_c > area_k * 1.02 and c[0] >= k[0] - 0.08 and _contains(c[1], k[1], min_ratio=1.02)
                    and "edge" not in c[2]):
                keep[i] = c
            continue
        # 跟已選定的卡重疊超過三成 → 不收。實拍的名片極少互相蓋住；這種框多半是兩張卡上「同色的那一半」
        # 連起來剛好像一張直式卡（實拍：兩張左藍右白的卡上下疊放，右半白色連成一個直式框）
        area = abs(cv2.contourArea(c[1].reshape(-1, 1, 2)))
        overlap = sum(cv2.intersectConvexConvex(c[1].astype(np.float32), k[1].astype(np.float32))[0] for k in keep)
        if area > 0 and overlap / area > 0.3:
            continue
        keep.append(c)
    return keep


def _work(img: Image.Image) -> tuple[np.ndarray, float]:
    rgb = to_rgb_array(img)
    H, W = rgb.shape[:2]
    scale = min(1.0, WORK_EDGE / max(H, W))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (max(1, int(W * scale)), max(1, int(H * scale))), interpolation=cv2.INTER_AREA)
    return rgb, scale


def detect_cards(img: Image.Image, *, min_area_frac: float = MIN_AREA_FRAC) -> list[Card]:
    """多卡模式：回每一張卡的四角框（原圖座標），依上到下、左到右排序。零張回空清單。"""
    work, scale = _work(img)
    if min(work.shape[:2]) < 20:
        return []
    picked = _pick(_candidates(work, min_area_frac))
    # 一個框幾乎完全包住另一個框 → 外框多半是兩張相貼或卡＋陰影，留內框（使用者可以再補）
    final = []
    for s, q, f in picked:
        inner = [k for k in picked if k[1] is not q and _contains(q, k[1])]
        if inner:
            continue
        final.append(Card(quad=(q / scale).tolist(), score=s, flags=f))
    return sort_cards(final)


def _contains(outer: np.ndarray, inner: np.ndarray, min_ratio: float = 1.3) -> bool:
    inter, _ = cv2.intersectConvexConvex(outer.astype(np.float32), inner.astype(np.float32))
    ia = abs(cv2.contourArea(inner.astype(np.float32)))
    oa = abs(cv2.contourArea(outer.astype(np.float32)))
    return ia > 0 and inter / ia > 0.9 and oa > ia * min_ratio


def sort_cards(cards: list[Card]) -> list[Card]:
    """上到下、左到右：同一列（中心 y 差不到半張卡高）再依 x 排。"""
    if not cards:
        return cards
    centers = [np.mean(np.asarray(c.quad), axis=0) for c in cards]
    hs = [min(quad_size(c.quad)) for c in cards]
    row_tol = 0.5 * float(np.median(hs))
    order = sorted(range(len(cards)), key=lambda i: centers[i][1])
    rows: list[list[int]] = []
    for i in order:
        if rows and abs(centers[i][1] - np.mean([centers[j][1] for j in rows[-1]])) <= row_tol:
            rows[-1].append(i)
        else:
            rows.append([i])
    out = []
    for r in rows:
        out.extend(cards[i] for i in sorted(r, key=lambda i: centers[i][0]))
    return out


def detect_single(img: Image.Image) -> tuple[Card, bool]:
    """單卡模式：回（這張卡，是不是看起來有好幾張）。找不到有把握的邊界 → 整張圖當卡面（`flags` 帶 `whole`），
    不切到字（同掃描修正：沒把握就不裁）。"""
    rgb = to_rgb_array(img)
    H, W = rgb.shape[:2]
    whole = Card(quad=[[0, 0], [W, 0], [W, H], [0, H]], score=0.0, flags=["whole"])
    # 「看起來有好幾張」要用一般的大小下限找：一張照片平放 6 張時每張只佔 7% 左右，
    # 原本用 8% 的下限一張都找不到 → 提示永遠不出現（使用者 2026-11-13：模式忘了選）
    cards = detect_cards(img)
    if not cards:
        return whole, False
    area = lambda c: abs(cv2.contourArea(np.asarray(c.quad, np.float32).reshape(-1, 1, 2)))
    big = max(cards, key=area)
    # 只算有把握的卡：分數低的多半是卡面上的色塊或照片裡的別的東西（真實照片：餐廳名片上的三個色塊 0.8、
    # 人物照 0.66；真的多張都在 0.88 以上）。佔畫面一成以上的大卡分數門檻放寬一點（兩張大卡並排的老名片 0.84）
    sure = lambda c: c.score >= 0.85 or (c.score >= 0.8 and area(c) >= 0.1 * W * H)
    multi = sum(1 for c in cards if area(c) >= 0.4 * area(big) and sure(c)) >= 2
    if area(big) < 0.08 * W * H:
        return whole, multi                  # 單卡模式只裁「夠大」的那一張；太小的不裁（可能只是局部）
    return big, multi


def warp_card(img: Image.Image, quad, *, rotate: int = 0, long_edge: int = 1400) -> Image.Image:
    """依四角框拉正成矩形（橫式卡長邊在寬；直式卡保持直式），`rotate` 是使用者另外指定的 0 / 90 / 180 / 270。"""
    rgb = to_rgb_array(img)
    q = order_quad(quad)
    w, h = quad_size(q)
    if w < 2 or h < 2:
        raise ValueError("框太小")
    s = long_edge / max(w, h)
    ow, oh = max(2, int(round(w * s))), max(2, int(round(h * s)))
    dst = np.array([[0, 0], [ow - 1, 0], [ow - 1, oh - 1], [0, oh - 1]], np.float32)
    m = cv2.getPerspectiveTransform(q.astype(np.float32), dst)
    out = cv2.warpPerspective(rgb, m, (ow, oh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    pil = Image.fromarray(out)
    rot = int(rotate) % 360
    if rot:
        pil = pil.rotate(-rot, expand=True)      # 順時針
    return pil


def clamp_quad(quad, width: int, height: int) -> list[list[float]]:
    """使用者送來的四角：數值檢查並夾在影像範圍內。"""
    import math
    pts = list(quad or [])
    if len(pts) != 4:
        raise ValueError("四角框要有 4 個點")
    out = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError("每個點要是 [x, y]")
        x, y = float(p[0]), float(p[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("座標不可以是 NaN 或無限大")
        out.append([min(max(x, 0.0), float(width)), min(max(y, 0.0), float(height))])
    w, h = quad_size(order_quad(out))
    if w < 20 or h < 20:
        raise ValueError("框太小")
    return order_quad(out).tolist()


def blur_score(img: Image.Image) -> float:
    """清晰度（拉普拉斯變異數）；太低標「模糊，待確認」。"""
    g = cv2.cvtColor(to_rgb_array(img), cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def glare_ratio(img: Image.Image) -> float:
    """過曝（反光）比例：幾乎純白而且成塊的像素。"""
    g = cv2.cvtColor(to_rgb_array(img), cv2.COLOR_RGB2GRAY)
    hot = (g >= 252).astype(np.uint8)
    hot = cv2.morphologyEx(hot, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return float(hot.mean())
