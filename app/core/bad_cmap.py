"""共用 bad-CMap 偵測與過濾 helper。

v1.9.38：PDF 字型缺 ToUnicode CMap 或 OCR 過的 PDF 雙層文字常產生亂碼字段
落（Latin Extended block 大量字元 + 控制字元 + PUA）。這些段落讀起來無意
義，混進正常文字裡造成 internal-error 或抽取重複。

本模組提供：
- `is_bad_cmap_text(s)`: 判定整段是否為 bad-CMap 噪音
- `strip_bad_cmap_text(s)`: 移除整段 bad-CMap 段
- `xml_safe(s)`: 移除 XML 不相容的控制字元（給 docx / odt serialization 用）
"""
import re

# Latin Extended-A (0x0100-0x017F) + B (0x0180-0x024F) + IPA Extensions
# (0x0250-0x02AF) + Spacing Modifier Letters (0x02B0-0x02FF) + Combining
# Diacritical Marks (0x0300-0x036F) + Greek (0x0370-0x03FF) — bad CMap 把字
# shift 到這些區域。涵蓋 CRS PDF 內出現的 Greek tonos「ͺ」(U+037A) 等
_LATIN_EXT_RE = re.compile(r"[Ā-Ͽ]")
# Private Use Area
_PUA_RE = re.compile(r"[-]")
# 控制字元（PDF 不該有，除了 \t \n \r）
_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
# XML 不相容字元（XML 1.0 spec）
_XML_INVALID_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


_VOWELS = set("aeiouAEIOU")
_ENG_CONSONANTS = set("bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ")


def is_bad_cmap_text(s: str, min_len: int = 5) -> bool:
    """判定 s 是否為 bad-CMap 噪音。

    判定條件（任一觸發）：
    1. Latin Extended chars (0x0100-0x024F) 比例 > 10% — bad CMap 常 shift 到此區
    2. 控制字元 (< 0x20) + PUA 比例 > 20% — 明顯爛 encoding
    3. ASCII shift 後的偽英文（CRS-type bad CID mapping）— 字串看似英文但
       母音比例極低、且無 CJK / non-letter「正常分隔字元」結構
    """
    if not s or len(s) < min_len:
        return False
    n = len(s)
    lat_ext = len(_LATIN_EXT_RE.findall(s))
    pua = len(_PUA_RE.findall(s))
    ctrl = len(_CTRL_RE.findall(s))
    # 條件 1: Latin Extended 大量出現（正常英文 / 中文不會有）
    if lat_ext >= 5 and lat_ext / n > 0.10:
        return True
    # 條件 2: 控制字 + PUA 比例高
    if (ctrl + pua) / n > 0.20:
        return True
    # 條件 3 (v1.9.47)：ASCII shift 偽英文檢測
    # 真英文 letters 母音比例約 35-45%。bad CID shift 後英文 letter 隨機分布
    # 母音比例 < 15%。判定：letters >= 5 且 vowel / letter < 0.12 → bad
    # 加保護：letters / n > 0.5 確保是「主要 letter 結構」（避免 IS-04-037 誤判）
    vowels = sum(1 for ch in s if ch in _VOWELS)
    consonants = sum(1 for ch in s if ch in _ENG_CONSONANTS)
    letters = vowels + consonants
    cjk = sum(1 for ch in s if 0x3400 <= ord(ch) <= 0x9FFF
                or 0xAC00 <= ord(ch) <= 0xD7AF)
    if letters >= 5 and cjk == 0 and letters / n > 0.50:
        if vowels / letters < 0.12:
            return True
    return False


def strip_bad_cmap_text(s: str) -> str:
    """若 s 整段是 bad-CMap 噪音 → 回空字串；否則回原字串。"""
    return "" if is_bad_cmap_text(s) else s


def xml_safe(s: str) -> str:
    """移除 XML 不相容字元（python-docx / lxml 序列化需要）。"""
    return _XML_INVALID_RE.sub("", s or "")


def clean_pdf_text(s: str) -> str:
    """清乾淨 PDF 抽出的文字：移除 PUA + 控制字元（XML-safe），但保留
    Latin Extended（可能是合法 accented chars 或語言文字）。

    若整段 > 50% 都是 Latin Extended（典型 bad-CMap shift）→ 整段丟掉。
    """
    if not s:
        return ""
    if is_bad_cmap_text(s):
        return ""
    return _XML_INVALID_RE.sub("", _PUA_RE.sub("", s))
