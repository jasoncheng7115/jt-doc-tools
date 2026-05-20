"""共用 PUA 字元 → 標準 Unicode 對照表。

v1.9.42：PDF 表單常用 Wingdings / Symbol / FontAwesome 等字型的 PUA
（Private Use Area, U+E000–U+F8FF）字元來畫 checkbox / 箭頭 / icon。
原本「PUA 一律 strip」會把這些表單結構符號整個丟掉（402386974
機密等級 row case：原 "□一般 ■限閱 □密 □機密" → 結果只剩 "一般 ■限閱 密 機密"）。

策略：已知 Wingdings/Symbol codepoint → 換成標準 Unicode 對應符號；
未知 PUA 仍 strip（FontAwesome icon 等無對應普通 Unicode 字）。
"""

# Wingdings codepoint → Unicode（Adobe Symbol font glyph 對照）
# 範圍 0xF020–0xF0FF 對應 Wingdings 0x20–0xFF 的 glyph
PUA_MAP: dict[str, str] = {
    "": " ",       # space
    "": "✏",       # 編輯筆
    "": "✂",       # 剪刀
    "": "✁",       # 剪刀
    "": "✆",       # 電話
    "": "✉",       # 信
    "": "🕮",       # 書
    "": "🕮",       # 書
    "": "📁",      # 資料夾
    "": "📂",      # 資料夾開啟
    "": "📺",      # 螢幕
    "": "🖱",      # 滑鼠
    "": "?",       # 問號
    "": "👤",      # 人
    "": "⌚",       # 手錶
    "": "→",       # 箭頭
    "": "🎨",      # 畫筆
    "": "🔔",      # 鈴
    "": "❶",       # 編號 1
    "": "❷",       # 編號 2
    "": "•",       # 圓點
    "": "■",       # 黑色方塊
    "": "□",       # 空心方塊
    "": "❑",       # 大空心方塊
    "": "★",       # 實心星
    "": "☆",       # 空心星
    "": "•",       # 圓點
    "": "❑",       # checkbox-like
    "": "☐",       # ★ Wingdings A3 = empty checkbox（最常見）
    "": "☑",       # Wingdings A4 = checked
    "": "☒",       # Wingdings A5 = cross
    "": "✓",       # Wingdings A6 = check
    "": "✘",       # cross
    "": "☐",       # empty
    "": "☑",       # checked
    "": "▽",       # triangle
    "": "✶",       # star
    "": "✦",       # filled star
    "": "←",       # arrow left
    "": "→",       # arrow right
    "": "↑",       # arrow up
    "": "↓",       # arrow down
    "": "✦",       # filled star
    "": "←",
    "": "→",
    "": "✅",       # checked
    "": "✓",
    "": "✗",
    "": "☐",
    "": "☑",       # Wingdings FE = checked box
    "": "☒",
}


def replace_pua_chars(s: str) -> str:
    """把已知的 PUA codepoint 換成標準 Unicode；未知 PUA strip。

    對含 CJK / ASCII 的正常文字，只動 PUA codepoint，其它字保留。
    """
    if not s:
        return ""
    has_pua = False
    for ch in s:
        if 0xE000 <= ord(ch) <= 0xF8FF:
            has_pua = True
            break
    if not has_pua:
        return s
    out = []
    for ch in s:
        if 0xE000 <= ord(ch) <= 0xF8FF:
            if ch in PUA_MAP:
                out.append(PUA_MAP[ch])
            # else: drop unknown PUA（如 FontAwesome icons）
        else:
            out.append(ch)
    return "".join(out)
