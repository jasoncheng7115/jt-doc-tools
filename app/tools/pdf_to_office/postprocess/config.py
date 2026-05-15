"""pdf-to-office 後處理閾值集中管理。Sprint 1 只放實際用到的部分。"""
from __future__ import annotations

# CJK 字型對應：(eastAsia 字型, ASCII fallback 字型)
# 視為等寬（monospace）字型的 PDF 字型名 hints — 保留不換成 proportional fallback。
# 命中後 font_normalize 會用 Courier New (Word 內建等寬) + 等寬中文，而不是新細明體。
MONOSPACE_HINTS = (
    "Courier", "Mono", "Consolas", "Menlo", "SFMono", "SF-Mono",
    "JetBrains", "FiraCode", "Fira-Code", "Inconsolata", "Hack",
    "RobotoMono", "Roboto-Mono", "SourceCodePro", "Source-Code-Pro",
    "DejaVuSansMono", "DejaVu-Sans-Mono", "LiberationMono", "UbuntuMono",
    "PTMono", "PT-Mono", "Anonymous", "Inconsolata", "OperatorMono",
    "Cousine", "Andale", "Lucida-Console", "LucidaConsole",
)
# 等寬字型對應（CJK eastAsia, ASCII）— Word 通用 + 中文系統等寬
MONOSPACE_FALLBACK = ("PingFang TC Mono", "Courier New")  # macOS 沒等寬中文就退 PingFang


FONT_MAPPING: dict[str, tuple[str, str]] = {
    # 繁體中文
    "MingLiU": ("新細明體", "Times New Roman"),
    "PMingLiU": ("新細明體", "Times New Roman"),
    "PMingLiU-ExtB": ("新細明體", "Times New Roman"),
    "DFKai-SB": ("標楷體", "Times New Roman"),
    "BiauKai": ("標楷體", "Times New Roman"),
    "Microsoft JhengHei": ("微軟正黑體", "Arial"),
    "MJhengHei": ("微軟正黑體", "Arial"),
    "MJhengHeiUI": ("微軟正黑體", "Arial"),
    "PingFangTC": ("PingFang TC", "Helvetica"),
    "PingFangTC-Regular": ("PingFang TC", "Helvetica"),
    "PingFangTC-Semibold": ("PingFang TC", "Helvetica"),
    # 簡體中文（也常見於繁中文件）
    "SimSun": ("新細明體", "Times New Roman"),
    "SimHei": ("微軟正黑體", "Arial"),
    "Microsoft YaHei": ("微軟正黑體", "Arial"),
    # Adobe CJK
    "AdobeSongStd-Light": ("新細明體", "Times New Roman"),
    "AdobeFangsongStd": ("標楷體", "Times New Roman"),
    "AdobeHeitiStd": ("微軟正黑體", "Arial"),
    "AdobeMingStd-Light": ("新細明體", "Times New Roman"),
    # 思源 / Noto
    "NotoSansCJK": ("思源黑體", "Arial"),
    "NotoSerifCJK": ("思源宋體", "Times New Roman"),
    "NotoSansCJKtc": ("思源黑體", "Arial"),
    "NotoSerifCJKtc": ("思源宋體", "Times New Roman"),
    "SourceHanSans": ("思源黑體", "Arial"),
    "SourceHanSerif": ("思源宋體", "Times New Roman"),
}


# 段落合併判斷用
PARAGRAPH_MERGE = {
    "min_paragraph_chars_to_consider": 5,
    "font_size_tolerance_pt": 0.5,
    "y_distance_ratio_same_para_max": 1.2,
    "y_distance_ratio_diff_para_min": 1.5,
    "sentence_end_chars": "。．.！!？?：:；;",
}


# 雜訊清理
CLEANUP = {
    "compress_empty_paragraphs": True,
    "max_consecutive_empty": 1,
    "remove_tiny_images": True,
    "tiny_image_threshold_pt": 10.0,
    "remove_hallucinated_paragraphs": False,  # Sprint 1 保守不開
}


# Fallback 字型
FALLBACK_CJK_FONT = "新細明體"
FALLBACK_ASCII_FONT = "Times New Roman"
