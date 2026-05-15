"""PDF 字型清查 — embedded / ToUnicode CMap / CJK / 使用次數。

CMap 異常是中文 PDF 常見問題：賣方系統 export 時沒帶 ToUnicode → PyMuPDF 抽出
PUA 字元當垃圾。提早偵測能讓後續 fixer 知道「這份 PDF 字型不可信」。
"""
from __future__ import annotations

import re
from .models import PDFFontInfo

_CJK_HINT_RE = re.compile(
    r"(MingLiU|PMingLiU|DFKai|BiauKai|JhengHei|YaHei|SimSun|SimHei|"
    r"AdobeSong|AdobeFangsong|AdobeHeiti|NotoSansCJK|NotoSerifCJK|"
    r"SourceHan|HiraKaku|HiraMin|YuMincho|YuGothic|MS-Gothic|MS-Mincho|"
    r"GenYoMin|TW-Sung|TW-Kai|UnGungseo|Malgun)",
    re.IGNORECASE,
)


def _is_cjk_font_name(name: str) -> bool:
    return bool(_CJK_HINT_RE.search(name or ""))


def inspect_fonts(doc) -> list[PDFFontInfo]:
    """掃出文件用到的所有字型 + 嵌入 / CMap / CJK / 用量。

    pymupdf doc.get_page_fonts(pno) 回 (xref, ext, type, basefont, name, encoding, referencer)
    若有 ext != "" → embedded。encoding 含 "Identity" 通常是 CJK CID 字型。
    has_tounicode 用 doc.xref_object(xref) 找 /ToUnicode 子物件。
    """
    seen: dict[str, PDFFontInfo] = {}
    for pno in range(doc.page_count):
        try:
            for finfo in doc.get_page_fonts(pno):
                # 容錯：tuple shape 可能依 PyMuPDF 版本不同
                xref = finfo[0]
                ext = finfo[1] if len(finfo) > 1 else ""
                basefont = finfo[3] if len(finfo) > 3 else ""
                encoding = finfo[5] if len(finfo) > 5 else ""
                name = (basefont or "").lstrip("+")  # subset prefix 去掉
                if not name:
                    name = "(unknown)"
                key = name
                fi = seen.get(key)
                if fi is None:
                    has_tounicode = False
                    try:
                        obj = doc.xref_object(xref) or ""
                        has_tounicode = "/ToUnicode" in obj
                    except Exception:
                        pass
                    is_cjk = _is_cjk_font_name(name) or "Identity" in (encoding or "")
                    fi = PDFFontInfo(
                        name=name,
                        is_embedded=bool(ext),
                        has_tounicode=has_tounicode,
                        is_cjk=is_cjk,
                        usage_count=0,
                    )
                    seen[key] = fi
                fi.usage_count += 1
        except Exception:
            continue
    return list(seen.values())
