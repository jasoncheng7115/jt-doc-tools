"""個資限用章 PNG 渲染。

台灣常見「身分證/護照影本送出申請時蓋的紅章」場景:
  「僅供 [用途] 使用，他用無效」+ 日期 + 申請人(可選) + 份數(可選)

樣式:
  - rectangle: 雙線框紅章(傳統印章感,預設)
  - rectangle-single: 單線框
  - diagonal:  對角線斜印(45° 紅字,無邊框)

排版採視覺階層:
  小字「僅供」 → 大字「{purpose}」(粗體) → 小字「使用，他用無效」 → 分隔線 → 小字 footer
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# 本地內建字型(隨工具發布)
_FONT_DIR = Path(__file__).parent / "fonts"
_LXGW_PATH = _FONT_DIR / "LXGWWenKaiTC-Regular.ttf"

# 系統字型 fallback 鏈 — 每項 (path, index, regular_index, bold_index)
# .ttc 是字型集合,index 不同代表不同子字型。Mac 的 Songti.ttc / Heiti Medium.ttc
# 預設 index=0 是 SC 簡中,繁中要指定 TC 變體 (Songti.ttc 的 index 7 是 TC Regular)。
_KAITI_FALLBACK = [
    ("/System/Library/Fonts/Supplemental/BiauKai.ttc", 0, 0),                # macOS 標楷體
    ("C:/Windows/Fonts/DFKai-SB.ttf", 0, 0),                                 # Windows 標楷體
    ("C:/Windows/Fonts/kaiu.ttf", 0, 0),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", 0, 0),
]
_SONG_FALLBACK = [
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 7, 2),                 # idx 7=TC Reg, 2=TC Bold
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 0, 0),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 0, 0),
    ("C:/Windows/Fonts/PMingLiU.ttc", 0, 0),
    ("C:/Windows/Fonts/msyh.ttc", 0, 0),
]
_HEI_FALLBACK = [
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0, 0),                      # idx 0=TC Medium
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0, 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0, 0),
    ("C:/Windows/Fonts/msjh.ttc", 0, 0),
    ("C:/Windows/Fonts/simhei.ttf", 0, 0),
]
_LXGW_FALLBACK = [(str(_LXGW_PATH), 0, 0)] if _LXGW_PATH.exists() else []


# 字型風格映射
FONT_STYLES = {
    "kaiti":  {"name": "標楷體（印章正統）", "fallback": _KAITI_FALLBACK + _SONG_FALLBACK + _HEI_FALLBACK},
    "song":   {"name": "宋體（典雅）",       "fallback": _SONG_FALLBACK + _HEI_FALLBACK},
    "hei":    {"name": "黑體（現代）",       "fallback": _HEI_FALLBACK + _SONG_FALLBACK},
    "lxgw":   {"name": "霞鶩文楷（手寫感）", "fallback": _LXGW_FALLBACK + _KAITI_FALLBACK + _SONG_FALLBACK},
}


def _load_font(style_or_id: str, size_px: int,
                prefer_bold: bool = False) -> ImageFont.FreeTypeFont:
    """載入字型。`style_or_id` 接受兩種值:
    - 4 種 semantic style: kaiti / song / hei / lxgw → 走 font_catalog.best_cjk_path
      + 老舊 hardcoded fallback chain
    - font_catalog id (system:/path/to/font.ttc, custom:xxx.ttf, etc.) → 直接 resolve
    """
    # 1. font_catalog 解析 (system / custom 字型,從設定頁來)
    if style_or_id and (":" in style_or_id) and style_or_id not in FONT_STYLES:
        try:
            from app.core import font_catalog
            entry = font_catalog.resolve_font_id(style_or_id)
            if entry and entry.get("path"):
                idx = int(entry.get("idx") or 0)
                return ImageFont.truetype(entry["path"], size_px, index=idx)
        except Exception:
            pass

    # 2. semantic style → font_catalog.best_cjk_path 自動找系統最佳字型
    if style_or_id in FONT_STYLES:
        try:
            from app.core import font_catalog
            # kaiti / lxgw 用 serif 偏好 (印章字型多偏 serif/楷),hei 用 sans
            style_param = "sans" if style_or_id == "hei" else "serif"
            best = font_catalog.best_cjk_path(style=style_param, cjk="traditional")
            if best:
                path, idx = best
                # kaiti 模式優先抓檔名含 ukai/kai 的字型
                if style_or_id == "kaiti":
                    kai_match = _find_kai_in_catalog()
                    if kai_match:
                        return ImageFont.truetype(kai_match[0], size_px, index=kai_match[1])
                # lxgw 模式優先用內建 LXGW
                if style_or_id == "lxgw" and _LXGW_PATH.exists():
                    return ImageFont.truetype(str(_LXGW_PATH), size_px, index=0)
                return ImageFont.truetype(str(path), size_px, index=idx)
        except Exception:
            pass

    # 3. 舊 hardcoded fallback chain (Mac BiauKai, Songti TC, etc.)
    chain = FONT_STYLES.get(style_or_id, FONT_STYLES["kaiti"])["fallback"]
    for entry in chain:
        path, reg_idx, bold_idx = entry
        if not Path(path).exists():
            continue
        idx = bold_idx if prefer_bold else reg_idx
        try:
            return ImageFont.truetype(path, size_px, index=idx)
        except Exception:
            try:
                return ImageFont.truetype(path, size_px, index=0)
            except Exception:
                continue
    return ImageFont.load_default()


def _find_kai_in_catalog() -> Optional[tuple[str, int]]:
    """從 font_catalog 找楷體類字型。檔名含 ukai / kaiti / biau / dfkai / kaiu。"""
    try:
        from app.core import font_catalog
        kai_keywords = ("ukai", "kaiti", "biau", "dfkai", "kaiu", "cwtexkai")
        for f in font_catalog.list_fonts():
            path = f.get("path")
            if not path:
                continue
            low = Path(path).name.lower()
            if any(kw in low for kw in kai_keywords):
                # 偏好 TW variant
                idx = int(f.get("idx") or 0)
                # ukai.ttc TW 在 index 2
                if "ukai" in low and idx == 0:
                    idx = 2
                return (path, idx)
    except Exception:
        pass
    return None


def _text_size(draw: ImageDraw.ImageDraw, text: str,
                font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    if not text:
        return 0, 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def render_rectangle_stamp(
    purpose: str,
    date_str: str = "",
    applicant: str = "",
    copy_label: str = "",
    color_hex: str = "#c00000",
    font_size_px: int = 64,
    border_style: str = "double",
    font_style: str = "kaiti",
) -> tuple[bytes, int, int]:
    """渲染長方形紅章。

    視覺階層:
        小「僅供」(0.55x)
        大「{purpose}」(1.0x, 粗)  ← 視覺主角
        小「使用，他用無效」(0.55x)
        ──────  分隔線
        小 footer: {date} {applicant} {copy_label} (0.45x)
    """
    purpose = (purpose or "").strip() or "_________"

    # 字級階層
    big_size = font_size_px                          # 主用途
    small_size = max(20, int(font_size_px * 0.55))   # 僅供 / 使用，他用無效
    foot_size = max(18, int(font_size_px * 0.45))    # footer

    font_big = _load_font(font_style, big_size, prefer_bold=True)
    font_small = _load_font(font_style, small_size, prefer_bold=False)
    font_foot = _load_font(font_style, foot_size, prefer_bold=False)

    color = _parse_hex(color_hex)
    tmp = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(tmp)

    # 量測
    w_top, h_top = _text_size(draw, "僅供", font_small)
    w_main, h_main = _text_size(draw, purpose, font_big)
    w_bottom, h_bottom = _text_size(draw, "使用，他用無效", font_small)

    footer_parts = [s for s in (date_str.strip(), applicant.strip(), copy_label.strip()) if s]
    footer = "　".join(footer_parts)
    w_foot, h_foot = (_text_size(draw, footer, font_foot) if footer else (0, 0))

    # 各段間距 (相對 main 字級)
    gap_top_main = int(big_size * 0.15)
    gap_main_bot = int(big_size * 0.10)
    gap_to_foot = int(big_size * 0.30)
    pad_x = max(int(big_size * 0.9), 24)
    pad_y = max(int(big_size * 0.55), 16)

    content_w = max(w_top, w_main, w_bottom, w_foot)
    content_h = h_top + gap_top_main + h_main + gap_main_bot + h_bottom
    if footer:
        content_h += gap_to_foot + h_foot

    img_w = content_w + pad_x * 2
    img_h = content_h + pad_y * 2

    img = Image.new("RGBA", (img_w, img_h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    # 邊框
    if border_style == "double":
        outer_w = max(3, int(big_size * 0.08))
        draw.rectangle([0, 0, img_w - 1, img_h - 1], outline=color, width=outer_w)
        inner_gap = max(5, int(big_size * 0.11))
        inner_w = max(1, int(outer_w * 0.45))
        draw.rectangle(
            [inner_gap, inner_gap, img_w - 1 - inner_gap, img_h - 1 - inner_gap],
            outline=color, width=inner_w,
        )
    elif border_style == "single":
        w = max(2, int(big_size * 0.06))
        draw.rectangle([0, 0, img_w - 1, img_h - 1], outline=color, width=w)

    # 寫字 (置中,逐行下移)
    y = pad_y
    # 「僅供」
    x = (img_w - w_top) // 2
    draw.text((x, y), "僅供", fill=color, font=font_small)
    y += h_top + gap_top_main
    # 主用途 (粗一點 — 用 stroke_width 模擬)
    x = (img_w - w_main) // 2
    stroke_w = max(0, int(big_size * 0.045))
    draw.text((x, y), purpose, fill=color, font=font_big,
              stroke_width=stroke_w, stroke_fill=color)
    y += h_main + gap_main_bot
    # 「使用，他用無效」
    x = (img_w - w_bottom) // 2
    draw.text((x, y), "使用，他用無效", fill=color, font=font_small)
    y += h_bottom

    if footer:
        y += gap_to_foot
        # 分隔線
        sep_pad = int(big_size * 1.2)
        sep_y = y - int(gap_to_foot * 0.5)
        draw.line([(sep_pad, sep_y), (img_w - sep_pad, sep_y)],
                  fill=color, width=max(1, int(big_size * 0.025)))
        x = (img_w - w_foot) // 2
        draw.text((x, y), footer, fill=color, font=font_foot)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), img_w, img_h


def render_diagonal_stamp(
    purpose: str,
    date_str: str = "",
    color_hex: str = "#c00000",
    font_size_px: int = 80,
    font_style: str = "kaiti",
) -> tuple[bytes, int, int]:
    """渲染對角線斜印(45° 紅字,無邊框)。"""
    text = f"僅供 {(purpose or '').strip() or '_________'} 使用 · 他用無效"
    if date_str.strip():
        text += f"  ({date_str.strip()})"

    font = _load_font(font_style, font_size_px, prefer_bold=True)
    tmp = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(tmp)
    tw, th = _text_size(draw, text, font)

    pad = int(font_size_px * 0.4)
    base = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (255, 255, 255, 0))
    draw = ImageDraw.Draw(base)
    color = _parse_hex(color_hex)
    stroke_w = max(0, int(font_size_px * 0.04))
    draw.text((pad, pad), text, fill=color, font=font,
              stroke_width=stroke_w, stroke_fill=color)

    rotated = base.rotate(-20, resample=Image.BICUBIC, expand=True)
    buf = io.BytesIO()
    rotated.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), rotated.width, rotated.height


def _parse_hex(color_hex: str) -> tuple[int, int, int, int]:
    s = color_hex.lstrip("#")
    try:
        if len(s) == 3:
            r, g, b = int(s[0] * 2, 16), int(s[1] * 2, 16), int(s[2] * 2, 16)
            return (r, g, b, 255)
        if len(s) == 6:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)
        if len(s) == 8:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16))
    except Exception:
        pass
    return (192, 0, 0, 255)


# 範本一律用名詞 / 動賓倒裝避免「申請X使用」雙動詞累贅:
#   ✗ 申請信用卡 + 使用 →「僅供申請信用卡使用」(申請、使用兩動詞重複)
#   ✓ 信用卡申辦 + 使用 →「僅供信用卡申辦使用」較通順
PURPOSE_TEMPLATES: list[dict] = [
    {"id": "bank-account",  "label": "銀行開戶"},
    {"id": "credit-card",   "label": "信用卡申辦"},
    {"id": "passport",      "label": "護照申辦"},
    {"id": "visa",          "label": "簽證申辦"},
    {"id": "insurance",     "label": "保險投保"},
    {"id": "securities",    "label": "證券開戶"},
    {"id": "rent",          "label": "房屋租賃"},
    {"id": "tax",           "label": "稅務申報"},
    {"id": "job",           "label": "求職應徵"},
    {"id": "student-loan",  "label": "學貸申辦"},
    {"id": "scholarship",   "label": "獎助學金申請"},
    {"id": "subsidy",       "label": "補助款申辦"},
    {"id": "household",     "label": "戶政事務申辦"},
    {"id": "exam",          "label": "考試報名"},
    {"id": "membership",    "label": "會員申辦"},
]


def font_options() -> list[dict]:
    """UI 字型清單。"""
    return [{"id": k, "name": v["name"]} for k, v in FONT_STYLES.items()]
