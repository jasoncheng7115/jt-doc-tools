"""從**字形本身**還原 PDF 文字 —— 對付壞掉的 ToUnicode 對照表。

## 這是在解什麼

有些 PDF 的 ToUnicode 對照表是壞的：把每個字碼都對應到同一個符號（常見是
圓點 `•`），或對應到一堆罕用字。**畫面完全正常** —— 因為畫面是照字形畫的，
跟對照表無關；但任何「抽文字」的動作拿到的都不是原文。

客戶回報（2026-08-26）：在 PDF 編輯器點文件上原本的中文要修改，文字框裡
整排變成 `••••••`。

## 為什麼不是用 OCR

編輯器本來有一條 OCR 退路（給「字形真的認不出來」的掃描件用）。但這個情況
**資訊完全沒有遺失**：PDF 記著每個字用了哪個字形編號，而內嵌字型檔裡就有
字形編號 ↔ Unicode 的對照表。反查回去是**精確**的，不是猜的。

OCR 相比之下：會認錯字、要幾秒鐘、要下載模型，而且本機 EasyOCR 在缺 AVX2
的機器上會把整個服務打掛。能精確反查就不該用 OCR。

## 做法

1. `page.get_texttrace()` —— 每個字給 `(ucs, gid, ...)`，`ucs` 是（壞掉的）
   對照表結果，`gid` 是**真正畫出來的字形編號**。
2. `doc.extract_font(xref)` 把內嵌字型抽出來，讀它自己的 `cmap`
   （Unicode → 字形名），反過來得到 字形編號 → Unicode。
3. 兩者一接，字就回來了。

字型被子集化過也沒關係 —— 子集化通常會保留 cmap。真的沒有 cmap（或整份是
CID-keyed 沒有可用字形名）時回空字串，讓呼叫端自己決定要不要退回 OCR。
"""
from __future__ import annotations

import hashlib
import io
import logging
from typing import Optional

log = logging.getLogger(__name__)

# 字型檔不小（CJK 動輒數百 KB），同一份文件連點好幾個字很常見 → 快取
# 反查表。鍵用字型內容的指紋，不用 xref（換文件就不會撞在一起）。
_gid_map_cache: dict[str, dict] = {}
_CACHE_MAX = 24


def _font_fingerprint(buf: bytes) -> str:
    """快取鍵用的指紋。用 sha256 純粹是為了不讓源碼掃描把 sha1 標成告警
    （這裡沒有任何安全用途），只讀前 4 KB 讓大字型也很快。"""
    h = hashlib.sha256()
    h.update(str(len(buf)).encode("ascii"))
    h.update(buf[:4096])
    return h.hexdigest()


def _uni_rank(uni: int) -> tuple[int, int]:
    """一形多碼時挑哪個字碼：部首、相容漢字排最後，其餘取最小。"""
    late = (0x2E80 <= uni <= 0x2FDF            # 部首補充、康熙部首
            or 0xF900 <= uni <= 0xFAFF         # 相容漢字
            or 0x2F800 <= uni <= 0x2FA1F)      # 相容漢字補充
    return (1 if late else 0, uni)


def _gid_to_unicode(buf: bytes) -> dict:
    """字形編號 → Unicode 字碼。讀不出來回空 dict。"""
    key = _font_fingerprint(buf)
    cached = _gid_map_cache.get(key)
    if cached is not None:
        return cached
    table: dict = {}
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(io.BytesIO(buf), fontNumber=0, lazy=True)
        order = font.getGlyphOrder()
        # cmap 是 Unicode → 字形名；反過來建 字形名 → Unicode。
        # 一個字形可能對應多個字碼（異體字），取最小的那個（通常是本體）——
        # **但部首與相容漢字排最後**：微軟正黑體的「方」與康熙部首「⽅」
        # （U+2F45）是同一個字形，部首的字碼比較小，照「取最小」會把正常的
        # 「方」換成部首。字面一樣，搜尋、複製、比對全都對不上（2026-11-06 客戶表單）。
        name_to_uni: dict = {}
        for uni, gname in font.getBestCmap().items():
            if gname not in name_to_uni or _uni_rank(uni) < _uni_rank(name_to_uni[gname]):
                name_to_uni[gname] = uni
        for gid, gname in enumerate(order):
            uni = name_to_uni.get(gname)
            if uni is not None:
                table[gid] = uni
    except Exception as e:
        log.debug("讀不出內嵌字型的對照表：%s", e)
        table = {}
    if len(_gid_map_cache) >= _CACHE_MAX:
        _gid_map_cache.clear()
    _gid_map_cache[key] = table
    return table


#: 每一頁的字形資料（反查表 + texttrace）。不快取的話每個 span 都要重解一次
#: 整頁 —— 一頁 55 個 span 就是 55 次，擷取整份文件會慢得離譜。
#:
#: **快取掛在文件物件上，不要用模組層級的字典搭配 `id(doc)` 當鍵。**
#: 文件是每個請求開一份、用完就關；`id()` 在物件被回收後**會被重複使用**，
#: 下一份文件很可能拿到同一個 id → 讀到上一份文件的字型表 → **反查出別份
#: 文件的字**，而且完全無聲（2026-08-27 完整測試抓到：單跑全綠、合跑失敗，
#: 而且每次失敗的項目還不一樣）。掛在文件上就跟著文件一起消失。
_PAGE_CACHE_ATTR = "_jtdt_glyph_page_cache"


def _page_data(doc, page):
    cache = getattr(doc, _PAGE_CACHE_ATTR, None)
    if cache is None:
        cache = {}
        try:
            setattr(doc, _PAGE_CACHE_ATTR, cache)
        except Exception:
            cache = None          # 掛不上就不快取，寧可慢也不要錯
    pno = getattr(page, "number", -1)
    if cache is not None and pno in cache:
        return cache[pno]
    tables = _page_font_tables(doc, page)
    try:
        trace = page.get_texttrace()
    except Exception as e:
        log.debug("get_texttrace 失敗：%s", e)
        trace = []
    if cache is not None:
        cache[pno] = (tables, trace)
    return tables, trace


def _page_font_tables(doc, page) -> list[dict]:
    """這一頁所有內嵌字型的反查表。"""
    out = []
    for f in page.get_fonts(full=False):
        xref = f[0]
        try:
            _name, _ext, _subtype, buf = doc.extract_font(xref)
        except Exception:
            continue
        if not buf:
            continue
        table = _gid_to_unicode(buf)
        if table:
            out.append(table)
    return out


def recover_text_in_bbox(page, bbox, doc=None) -> str:
    """把 bbox 範圍內的文字從字形反查回來。查不到回空字串。

    `page` 是 PyMuPDF 的 Page；`bbox` 是 (x0, y0, x1, y1)（PDF 點）。
    """
    try:
        import fitz
    except Exception:
        return ""
    doc = doc if doc is not None else page.parent
    try:
        rect = fitz.Rect(*[float(v) for v in bbox])
    except Exception:
        return ""
    tables, trace = _page_data(doc, page)
    if not tables or not trace:
        return ""

    picked = []
    for item in trace:
        if item.get("type") not in (None, 0):     # 0 = 一般文字（非裁切路徑）
            continue
        for ch in item.get("chars") or ():
            try:
                gid = ch[1]
                origin = ch[2]
            except Exception:
                continue
            # 用字的原點（基線起點）判斷在不在框內；用整個字框會讓上下行
            # 的字互相沾到。垂直方向稍微放寬，基線本來就貼著框底。
            #
            # **水平方向必須是半開區間**：一個字的原點就是它的左緣，而
            # 下一個字的原點正好落在這個框的右緣 —— 用閉區間會把隔壁的字
            # 一起抓進來（實測 `□` 變成 `□主`、`□刪除` 尾巴多一個字元）。
            ox, oy = float(origin[0]), float(origin[1])
            if not (rect.x0 - 0.5 <= ox < rect.x1 - 0.5):
                continue
            if not (rect.y0 - 2 <= oy <= rect.y1 + 2):
                continue
            uni = None
            for table in tables:
                uni = table.get(gid)
                if uni is not None:
                    break
            # 反查到控制字元表示這張表本身不對勁（實測踩到 NUL）——
            # 當成查不到，讓整段放棄比塞一個看不見的字進去安全。
            if uni is not None and uni < 0x20:
                uni = None
            picked.append((item.get("seqno", 0), ox, uni))

    if not picked:
        return ""
    picked.sort(key=lambda t: (t[0], t[1]))
    # 有任何一個字查不到就整段放棄 —— 半段正確、半段問號比整段失敗更糟，
    # 使用者會以為那就是原文（缺字方框那次的教訓）。
    if any(uni is None for _seq, _x, uni in picked):
        return ""
    return "".join(chr(uni) for _seq, _x, uni in picked)


#: 擷取失敗時常見的「佔位字元」—— 壞掉的 ToUnicode 常把所有字碼都映到同一個
#: 符號。真正的點引導符（`……`）也長這樣，所以**不能只看字元**，見下。
_PLACEHOLDER_CHARS = set("•·●○◦∙⋅*?？_□■▪▫◻◼ .．。﹒")


def looks_like_placeholder(text: str, bbox, font_size: float) -> bool:
    """擷取出來的是**佔位字元、但畫面上其實是真的字**嗎。

    客戶回報（2026-08-26）：在 PDF 編輯器點「選既有物件」要改文件上原本的
    中文，文字框裡卻整排變成 `••••••`。

    根因：那份 PDF 的 ToUnicode 對照表壞掉，**把每個字碼都映到同一個圓點**。
    畫面是用字形畫的所以完全正常，但抽出來的字串是圓點 —— 而既有的
    `_looks_garbled` 只認「數學符號 / 方框 / 罕用漢字」那幾類，圓點
    （U+2022）、間隔號、星號、問號都**不在名單裡**，於是被當成可靠的擷取結果
    原樣送到畫面上。

    **不可以只看「字元是不是圓點」** —— 表單裡真的有點引導符（`目錄………12`），
    v1.6.10 起那些是刻意讓使用者選得到的。兩者的差別在**寬度**：

    * 真的點引導符：每個點約 0.2–0.35 個字寬（字型的 period 前進寬度）
    * 壞掉的 CJK 擷取：每個「點」其實是一個中文字，佔滿整個字寬

    實測重現檔：4 個 `•` 佔 56pt、字級 14 → 每字 14.0pt（1.0 字寬），
    而同樣字級的真點引導符每點約 3–4pt。門檻取 0.6 字寬，兩者差很遠。
    """
    s = (text or "").strip()
    # 單一字元的訊號太弱（一個全形問號、一個句號都可能是真的內容），
    # 而擷取整段壞掉時一定不只一個字。寧可漏判也不要誤判。
    if len(s) < 2 or font_size <= 0:
        return False
    def _is_placeholder(ch: str) -> bool:
        # 私人使用區（PUA）也算 —— 壞掉的對照表很常映到那一區，而且那些
        # 字碼本來就沒有標準意義，抽出來給使用者看沒有任何用處。
        return ch in _PLACEHOLDER_CHARS or 0xE000 <= ord(ch) <= 0xF8FF

    if any(not _is_placeholder(ch) for ch in s):
        return False          # 混有真正的字 → 不是整段擷取失敗
    try:
        width = float(bbox[2]) - float(bbox[0])
    except Exception:
        return False
    per_char = width / max(1, len(s))
    return per_char >= font_size * 0.6


def _is_suspicious(ch: str) -> bool:
    """中文文件裡不該出現的字碼區段 —— 壞掉的對照表最常 shift 到這些地方。

    這不是「一看到就判定壞掉」，只是**允許去對照字型**的門檻（見
    `repair_span_text` 的第三條）。單獨拿它當判準會誤傷正常內容。
    """
    cp = ord(ch)
    return (0x0100 <= cp <= 0x024F        # 拉丁擴充 A/B（最常見的 shift 目標）
            or 0xE000 <= cp <= 0xF8FF     # 私人使用區
            or 0x2200 <= cp <= 0x23FF     # 數學運算子 / 技術符號
            or 0x2500 <= cp <= 0x27BF     # 製表 / 幾何 / 雜項符號 / 裝飾符號
            or 0x3100 <= cp <= 0x318F     # 注音 / 韓文相容字母
            or ch in "\u2022\u00b7")       # 圓點、間隔號


def repair_span_text(page, span: dict, doc=None) -> Optional[str]:
    """這個 span 的擷取結果不可信的話，用字形反查還原原文。

    回傳：
      * 還原後的字串 —— 擷取壞掉、而且反查成功
      * `""` —— 擷取壞掉、但反查也沒轍（呼叫端應該把這個 span 丟掉，
        塞一串圓點或亂碼給使用者比缺一段更糟）
      * `None` —— 擷取本來就是好的，照原樣用

    「擷取壞掉」有兩種樣態：
      ① 字形被映成亂七八糟的符號（`is_bad_cmap_text` 認得的那種）
      ② 全部被映成同一個佔位字元（圓點 / 星號…）—— 從字元本身看不出來，
         要靠「每字寬度」判斷（客戶 2026-08-26 回報的就是這種）

    擷取文字、字數統計、逐句翻譯、去識別化走的是同一種壞掉的 PDF，所以這支
    放在 core，不要各自實作一份。
    """
    text = (span or {}).get("text") or ""
    if not text.strip():
        return None
    bbox = (span or {}).get("bbox") or ()
    size = float((span or {}).get("size") or 0)

    from .bad_cmap import is_bad_cmap_text
    broken = is_bad_cmap_text(text)
    if not broken and len(bbox) == 4:
        broken = looks_like_placeholder(text, bbox, size)

    if not broken:
        # ③**拿字型自己的對照表當真值去驗**（v1.14.57，客戶實際檔案）。
        #
        # 前兩條都是「這串字看起來像不像壞掉」的猜測，而猜測一定有門檻：
        # 客戶那份的每個 span 只有 5~10 個字，湊不到「拉丁擴充字 ≥ 5 個」
        # 的門檻，於是整份亂碼被當成可靠結果直接送到使用者面前。
        #
        # 但字型檔裡就有正確答案 —— 直接對照就好，不必猜：
        #   擷取結果含有「中文文件不該出現的字」**而且**反查得出不同的結果
        #   → 擷取壞了，用反查的結果。
        #
        # **兩個條件缺一不可**。只看「反查結果不一致」的話，正常樣本有 23%
        # 的 span 會對不起來（連字、空白、一形多碼），照那樣改會把正確的
        # 文字弄壞 —— 那比原本的 bug 更嚴重。加上「擷取結果本身可疑」之後，
        # 29 份真實樣本實測**誤判 0**，客戶那份 55 個 span 抓到 28 個。
        if len(bbox) != 4 or not any(_is_suspicious(c) for c in text):
            return None
        try:
            fixed = recover_text_in_bbox(page, bbox, doc=doc)
        except Exception:
            log.debug("字形反查失敗", exc_info=True)
            return None
        if fixed and fixed.strip() != text.strip():
            return fixed
        return None

    if len(bbox) != 4:
        return ""
    try:
        return recover_text_in_bbox(page, bbox, doc=doc) or ""
    except Exception:
        log.debug("字形反查失敗", exc_info=True)
        return ""


def page_text_repaired(page, doc=None) -> Optional[str]:
    """整頁的文字，壞掉的部分用字形反查還原。頁面本來就正常時回 `None`。

    回 `None` 是刻意的：呼叫端該走原本的 `page.get_text("text")`，那條路的
    斷行與空白處理跟這裡不完全一樣（字數統計、逐句翻譯都吃那個結果）。
    **正常的 PDF 一個位元都不該因為這個功能而改變** —— 這種修法最怕的是
    「為了救 1% 的壞檔，把 99% 的好檔弄出細微差異」。

    判準是「這一頁**大部分**的字都擷取壞掉」。零星幾個壞 span 不算，那多半是
    圖示字型之類的正常東西，交給既有的 `bad_cmap` 過濾就好。
    """
    try:
        blocks = (page.get_text("dict") or {}).get("blocks", [])
    except Exception:
        return None

    from .bad_cmap import is_bad_cmap_text

    total = broken = 0
    spans_by_line = []
    for block in blocks:
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if not spans:
                continue
            spans_by_line.append(spans)
            for s in spans:
                text = s["text"]
                total += len(text)
                bbox = s.get("bbox") or ()
                if is_bad_cmap_text(text) or (
                        len(bbox) == 4
                        and looks_like_placeholder(text, bbox, float(s.get("size") or 0))):
                    broken += len(text)

    if total == 0 or broken < total * 0.5:
        return None

    out_lines = []
    for spans in spans_by_line:
        parts = []
        for s in spans:
            fixed = repair_span_text(page, s, doc=doc)
            parts.append(s["text"] if fixed is None else fixed)
        line_text = "".join(parts).strip()
        if line_text:
            out_lines.append(line_text)
    return "\n".join(out_lines)
