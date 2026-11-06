"""OCR 引擎抽象層 — 統一 tesseract / easyocr 介面。

`recognize_image(png_bytes, langs)` 回標準化 word list：
    [{"text": "abc", "conf": 87.5, "left": 10, "top": 20, "width": 30, "height": 16}, ...]

支援引擎：
- `tesseract`：傳統 OCR，輕量、CJK 識別率較弱、per-CHAR 級 bbox（CJK 每字一個 word）
- `easyocr`：JaidedAI 泰國公司開源 OCR，中日韓辨識準確度高、per-LINE bbox（一行一個 word）

設定值在 `data/ocr_settings.json` 的 `engine` 欄位（'easyocr' / 'tesseract'）。

Engine 失敗 / 不可用會自動 fallback 到對方（避免 user 看到 import error）。
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from . import atomic_json

log = logging.getLogger(__name__)

# 兩引擎之間的語言碼對應 — tesseract code → easyocr code
# 沒在表內的 lang code 在 easyocr 端會 fallback 到 tesseract
_TESS_TO_EASYOCR = {
    "chi_tra": "ch_tra",
    "chi_sim": "ch_sim",
    "eng": "en",
    "jpn": "ja",
    "kor": "ko",
    "deu": "de",
    "fra": "fr",
    "spa": "es",
    "ita": "it",
    "por": "pt",
    "nld": "nl",
    "rus": "ru",
    "vie": "vi",
    "tha": "th",
    "ind": "id",
    "ara": "ar",
    "heb": "he",
    "hin": "hi",
}

# Lazy-loaded EasyOCR Reader（per-langs cache，避免每次 OCR 都重建模型）
_easyocr_readers: dict[tuple, object] = {}

# 建 Reader 一定要排隊：第一次建的時候 EasyOCR 會下載模型，而它**固定**把壓縮檔
# 下載到 `<模型目錄>/temp.zip`。兩件 OCR 作業同時第一次跑（併行上限預設就是 2），
# 兩邊一起寫同一個 temp.zip → 解壓時 `Bad CRC-32`，整份模型白下載、這一件退回
# Tesseract（Win10 實機在慢網路上重現過：第一件下載 15 分鐘還沒完，第二件又開始下載）。
# 排隊之後第二件等第一件下載完，直接拿快取好的 Reader。
_easyocr_reader_lock = threading.Lock()

# 建不起來的組合記一下，這段時間內不再重試。一份 20 頁的 PDF 每一頁都會來要 Reader，
# 不記的話模型下載失敗時**每一頁都重新下載一次**（約 300 MB，慢網路上一次十幾分鐘）。
# 過了這段時間就再試（網路恢復之後不必重開服務）。
_EASYOCR_RETRY_AFTER_S = 600
_easyocr_failed_at: dict[tuple, float] = {}


def get_default_engine() -> str:
    """讀取 admin 設定的預設 engine。預設 'easyocr'。"""
    p = _ocr_settings_path()
    if not p.exists():
        return "easyocr"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        e = (d.get("engine") or "easyocr").strip().lower()
        return e if e in ("easyocr", "tesseract") else "easyocr"
    except Exception:
        return "easyocr"


def set_default_engine(engine: str) -> bool:
    if engine not in ("easyocr", "tesseract"):
        return False
    p = _ocr_settings_path()
    try:
        d = {}
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                d = {}
        d["engine"] = engine
        atomic_json.write_json(p, d)
        return True
    except Exception as e:
        log.warning("set_default_engine failed: %s", e)
        return False


def _ocr_settings_path() -> Path:
    try:
        from ..config import settings
        return Path(settings.data_dir) / "ocr_settings.json"
    except Exception:
        return Path("data") / "ocr_settings.json"


# ---- Engine availability ----

def is_easyocr_available() -> bool:
    """快速檢查 easyocr 是否可用 — 用 importlib.util.find_spec 只看 package
    是否存在，**不真的 import**（避免載 PyTorch 100+MB 拖慢 admin 頁渲染）。"""
    try:
        import importlib.util
        return importlib.util.find_spec("easyocr") is not None
    except Exception:
        return False


def is_tesseract_available() -> bool:
    """Tesseract 是否可用（binary + pytesseract）。"""
    try:
        from .sys_deps import _find_tesseract_binary, configure_pytesseract
        configure_pytesseract()
        return bool(_find_tesseract_binary())
    except Exception:
        return False


# ---- EasyOCR backend ----

#: EasyOCR 依語言挑的辨識模型檔 —— **照 `easyocr.Reader` 的判斷順序**
#: （easyocr 1.7：泰 → 繁中 → 簡中 → 日 → 韓 …，最後是拉丁字母）。
#: 只列我們的語言對照表會送出去的那幾種；對不上的不做判斷。
_EASYOCR_RECOG_ORDER = (
    ("th", "thai.pth"), ("ch_tra", "chinese.pth"), ("ch_sim", "zh_sim_g2.pth"),
    ("ja", "japanese_g2.pth"), ("ko", "korean_g2.pth"),
)
_EASYOCR_RECOG_SETS = (
    ({"ar", "fa", "ur", "ug"}, "arabic.pth"),
    ({"hi", "mr", "ne"}, "devanagari.pth"),
    ({"ru", "rs_cyrillic", "be", "bg", "uk", "mn"}, "cyrillic_g2.pth"),
)


def _easyocr_recog_file(easy_langs: tuple) -> Optional[str]:
    langs = list(easy_langs)
    if not langs:
        return None
    if langs == ["en"]:
        return "english_g2.pth"
    for code, fn in _EASYOCR_RECOG_ORDER:
        if code in langs:
            return fn
    for codes, fn in _EASYOCR_RECOG_SETS:
        if codes & set(langs):
            return fn
    return "latin_g2.pth"


def easyocr_first_download_pending(tess_langs: str = "") -> bool:
    """本機 EasyOCR **這一次要先下載模型**（每個約數十 MB，放在 GitHub）。

    只給畫面講清楚「正在下載」用：模型是在辨識第一頁的當下才下載，網路慢時
    可以等上好幾分鐘，而原本畫面寫的是「辨識中」—— 看起來跟當掉一樣
    （2026-09-24 Windows 全新安裝實測：到 GitHub 每秒 40 KB，要半小時）。

    看兩個檔：偵測模型（每種語言都要）與**這次語言要用的辨識模型** ——
    只看前者的話，下載到一半被中斷過的機器（偵測模型有了、辨識模型還沒）
    照樣會停在「辨識中」（實機碰到）。

    **不 import easyocr**（那會把 PyTorch 一起載進來）。有設遠端 GPU 服務時不算
    （模型在對方那台）。
    """
    import os
    try:
        if get_default_engine() != "easyocr" or not is_easyocr_available():
            return False
        from . import ocr_remote_settings as _ors
        if _ors.is_enabled_and_configured():
            return False
    except Exception:                                    # noqa: BLE001
        return False
    base = (os.environ.get("EASYOCR_MODULE_PATH") or os.environ.get("MODULE_PATH")
            or os.path.expanduser("~/.EasyOCR"))
    model_dir = Path(base) / "model"
    if not (model_dir / "craft_mlt_25k.pth").exists():
        return True
    recog = _easyocr_recog_file(_map_langs_to_easyocr(tess_langs)) if tess_langs else None
    return bool(recog) and not (model_dir / recog).exists()


def local_easyocr_safe() -> bool:
    """這台機器跑**本機** EasyOCR 會不會直接把行程打掛。

    EasyOCR 底層的 PyTorch 需要 AVX2；缺 AVX2 的 CPU（PVE 的 `x86-64-v2`
    等）在辨識階段會執行非法指令 → SIGILL → **整個服務 core dump**，
    連別人正在跑的作業一起陪葬（v1.12.10 客戶回報）。

    這支只回報安不安全，**不自動改引擎** —— 使用者在 v1.12.12 明確要求
    OCR 引擎維持手動切換、程式只做診斷與指引。要用的是那些「使用者根本
    沒選過引擎」的內部自動退路（例如 PDF 編輯器點到壞 ToUnicode 的文字），
    見 `recognize_image(allow_local_easyocr=...)`。

    遠端 GPU EasyOCR 不受影響（算在對方機器上）。
    """
    try:
        from .sys_deps import probe_cpu_simd
        return bool(probe_cpu_simd().get("ok", True))
    except Exception:
        return True          # 判不出來一律放行，不誤擋


def _get_easyocr_reader(easyocr_langs: tuple) -> Optional[object]:
    """Lazy-create or return cached EasyOCR Reader for given lang tuple.
    First call per lang combo loads model from disk (or downloads ~150MB
    if not yet downloaded) — 5-30s 延遲。"""
    if easyocr_langs in _easyocr_readers:
        return _easyocr_readers[easyocr_langs]
    with _easyocr_reader_lock:
        # 排隊等到的時候，前一個人可能已經建好了
        if easyocr_langs in _easyocr_readers:
            return _easyocr_readers[easyocr_langs]
        failed_at = _easyocr_failed_at.get(easyocr_langs)
        if failed_at is not None and time.monotonic() - failed_at < _EASYOCR_RETRY_AFTER_S:
            return None
        try:
            import easyocr
            # gpu=False 安全預設（CPU 跑 — 有 GPU 客戶可在 admin 開啟）
            # download_enabled=True 第一次自動下模型到 ~/.EasyOCR/model/
            reader = easyocr.Reader(list(easyocr_langs), gpu=False,
                                     verbose=False, download_enabled=True)
        except Exception as e:
            _easyocr_failed_at[easyocr_langs] = time.monotonic()
            log.warning("EasyOCR Reader init failed for %s: %s "
                        "(will not retry for %ds; falling back to tesseract)",
                        easyocr_langs, e, _EASYOCR_RETRY_AFTER_S)
            return None
        _easyocr_failed_at.pop(easyocr_langs, None)
        _easyocr_readers[easyocr_langs] = reader
        log.info("EasyOCR Reader loaded for langs=%s (cached)", easyocr_langs)
        return reader


def _map_langs_to_easyocr(tess_langs: str) -> tuple:
    """tesseract '+' 串連語言碼 → easyocr list（過濾掉 easyocr 不支援的）。"""
    mapped = []
    for code in tess_langs.split("+"):
        code = code.strip()
        if not code:
            continue
        eo = _TESS_TO_EASYOCR.get(code)
        if eo and eo not in mapped:
            mapped.append(eo)
    # EasyOCR 限制：英文以外的語言一定要配 'en'（架構設計）
    if mapped and "en" not in mapped:
        mapped.append("en")
    return tuple(mapped)


# EasyOCR 的去雜點（3×3 中值濾波）只在「真的有雜點、解析度也夠」時做。
# 2026-11-04 實測（兩種字型 × 10 / 13 pt × 150 / 200 / 300 DPI × 乾淨 / 掃描雜點，
# 跟正確答案逐字比）：乾淨的影像濾了一律變差（300 DPI 掉 0.3～3%、150 DPI 小字掉到 26～50%）；
# 有雜點的掃描在 200 DPI 以上多半變好（+1.5～+8%），150 DPI 照樣變差。
# 雜點用「孤立單點」量：八個鄰點都不同色的點，筆畫再細也不會被算到 ——
# 乾淨的影像（含真實掃描檔）≤ 0.004%、有雜點的掃描 ≥ 0.47%，門檻放在中間。
EASYOCR_DENOISE_MIN_SPECKLE = 0.0005
EASYOCR_DENOISE_MIN_SHORT_SIDE = 1600   # 約 A4 200 DPI


def _speckle_ratio(img) -> float:
    """孤立單點（黑底上的白點、白底上的黑點）佔全圖的比例。"""
    import numpy as np
    g = np.asarray(img.convert("L"))
    total = 0
    for mask in (g < 110, g > 190):
        m = np.pad(mask, 1).astype(np.uint8)
        h, w = m.shape
        nb = sum(m[1 + dy:h - 1 + dy, 1 + dx:w - 1 + dx]
                 for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
        total += int((mask & (nb == 0)).sum())
    return total / max(1, g.size)


def easyocr_wants_denoise(png_bytes: bytes) -> bool:
    """這張影像要不要先去雜點再給 EasyOCR。本機與遠端都照這個判斷（遠端用 `preprocess` 欄位帶過去）。

    判斷不了（影像打不開、沒有 numpy）一律不濾 —— 不濾是對乾淨影像最安全的預設。
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes))
        if min(img.size) < EASYOCR_DENOISE_MIN_SHORT_SIDE:
            return False
        return _speckle_ratio(img) >= EASYOCR_DENOISE_MIN_SPECKLE
    except Exception as e:  # noqa: BLE001
        log.info("EasyOCR 去雜點判斷失敗，不濾：%s", e)
        return False


def _easyocr_image(png_bytes: bytes, preprocess: bool):
    """EasyOCR 吃的影像：RGB；`preprocess` 時先做 3×3 中值濾波去掉掃描雜點。

    **跟遠端 GPU 那支（`ocr_remote_deploy/server_template.py`）做的一模一樣** ——
    同一個引擎、同一份檔案，走本機或走遠端結果不可以不同。要不要濾由
    `easyocr_wants_denoise()` 決定，`recognize_image` 對兩條路傳同一個值。
    """
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    if preprocess:
        from PIL import ImageFilter
        img = img.filter(ImageFilter.MedianFilter(size=3))
    return img


def _easyocr_recognize(png_bytes: bytes, langs: str, preprocess: bool = True) -> list[dict]:
    """跑 EasyOCR 對 PNG 抽 word data。
    回 standardized format：[{text, conf, left, top, width, height}, ...]"""
    eo_langs = _map_langs_to_easyocr(langs)
    if not eo_langs:
        return []
    reader = _get_easyocr_reader(eo_langs)
    if reader is None:
        return []
    try:
        import numpy as np
        # easyocr.readtext 接受 numpy array 或 path；用 numpy 避免 round-trip
        arr = np.asarray(_easyocr_image(png_bytes, preprocess))
        results = reader.readtext(arr, detail=1, paragraph=False)
    except Exception as e:
        log.warning("EasyOCR readtext failed: %s", e)
        return []

    out = []
    for item in results:
        if not item or len(item) < 3:
            continue
        bbox, text, conf = item[0], item[1], item[2]
        text = (text or "").strip()
        if not text:
            continue
        # EasyOCR bbox = 4 corner points [(x,y), ...]
        try:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            left = int(min(xs))
            top = int(min(ys))
            width = int(max(xs) - left)
            height = int(max(ys) - top)
        except Exception:
            continue
        out.append({
            "text": text,
            "conf": float(conf) * 100.0,  # easyocr 0..1, normalize to tesseract scale 0..100
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        })
    return out


# ---- Tesseract backend ----

def _tesseract_recognize(png_bytes: bytes, langs: str, preprocess: bool = True) -> list[dict]:
    """委派給既有 pdf_ocr.ocr_core._tesseract_image_to_data。"""
    try:
        from app.tools.pdf_ocr.ocr_core import _tesseract_image_to_data
        return _tesseract_image_to_data(png_bytes, langs, preprocess=preprocess)
    except Exception as e:
        log.warning("tesseract recognize failed: %s", e)
        return []


# ---- Public dispatcher ----

def recognize_text(png_bytes: bytes, langs: str,
                    engine: Optional[str] = None,
                    preprocess: bool = True,
                    allow_local_easyocr: bool = True) -> tuple[str, str]:
    """簡化介面 — 對 caller 只關心「該圖的文字內容」（不要 bbox / conf）的場景。
    內部呼叫 recognize_image，把 words join 成單字串。
    回 (text, engine_used)。"""
    words, used = recognize_image(png_bytes, langs, engine=engine, preprocess=preprocess,
                                  allow_local_easyocr=allow_local_easyocr)
    if not words:
        return "", used
    return " ".join(w.get("text", "").strip() for w in words if w.get("text", "").strip()), used


def _remote_easyocr_recognize(png_bytes: bytes, langs: str,
                                preprocess: bool = True) -> list[dict]:
    """呼叫遠端 GPU EasyOCR server。失敗 raise(讓 caller fallback 本機)。
    langs 進來是 tesseract code (chi_tra+eng),要轉成 easyocr code (ch_tra+en)。"""
    from . import ocr_remote_settings as _ors
    import httpx
    d = _ors.get()
    url = (d.get("url") or "").rstrip("/")
    token = d.get("token") or ""
    timeout_s = d.get("timeout_s") or 60
    if not url or not token:
        raise RuntimeError("remote OCR url/token not configured")

    # 把 tesseract lang code 轉 easyocr code(chi_tra->ch_tra, eng->en, ...)
    raw_codes = [c.strip() for c in langs.replace(",", "+").split("+") if c.strip()]
    easy_codes = [_TESS_TO_EASYOCR.get(c, c) for c in raw_codes]
    easy_langs = "+".join(easy_codes)

    log.info("remote OCR -> %s (langs=%s -> %s, %d KB)", url, langs, easy_langs, len(png_bytes) // 1024)
    files = {"image": ("page.png", png_bytes, "image/png")}
    data = {"langs": easy_langs, "preprocess": "true" if preprocess else "false"}
    headers = {"Authorization": f"Bearer {token}"}
    # 外部 GPU 的同時呼叫上限（預設 1）—— jt-ocr-server 是一張 GPU 載一個模型，
    # 同時打多個請求過去會互相搶顯示記憶體。見 remote_limit 的說明。
    from . import remote_limit
    with remote_limit.slot(), httpx.Client(timeout=float(timeout_s)) as cli:
        r = cli.post(f"{url}/ocr", files=files, data=data, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"remote OCR HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
    raw_words = body.get("words") or []
    words: list[dict] = []
    for w in raw_words:
        bbox = w.get("bbox") or []
        if len(bbox) < 4:
            continue
        x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
        words.append({
            "text": str(w.get("text") or ""),
            "left": float(x0),
            "top": float(y0),
            "width": float(x1 - x0),
            "height": float(y1 - y0),
            "conf": int(round(float(w.get("conf") or 0.95) * 100)),
        })
    log.info("remote OCR <- %d words, %.2fs (device=%s)",
             len(words), body.get("elapsed_s", 0), body.get("device", "?"))
    return words


def recognize_image(png_bytes: bytes, langs: str,
                     engine: Optional[str] = None,
                     preprocess: bool = True,
                     allow_local_easyocr: bool = True) -> tuple[list[dict], str]:
    """主要入口 — 跑 OCR 拿 standardized words，自動 fallback。

    engine: 'easyocr' / 'tesseract' / None（用 admin 預設值）
    allow_local_easyocr: 允不允許呼叫**本機** EasyOCR。預設 True（所有既有
      呼叫者行為不變）。缺 AVX2 的 CPU 上本機 EasyOCR 會 SIGILL 打掛整個
      服務，所以「使用者沒選過引擎」的內部自動退路要傳
      `allow_local_easyocr=local_easyocr_safe()`。遠端 GPU 不受此限。
    preprocess: 是否做影像預處理 —— Tesseract：灰階＋拉對比；EasyOCR（本機與遠端相同）：影像有雜點且解析度夠時才做 3×3 中值濾波

    遠端 GPU EasyOCR server: 若 admin 已設定 + enabled,優先使用,失敗自動退本機。

    回 (words_list, engine_used) — engine_used 紀錄實際用的 engine（可能是 fallback 後的）
    """
    chosen = engine or get_default_engine()
    # EasyOCR 的去雜點：呼叫端要前處理，也要影像真的有雜點才做（理由見 EASYOCR_DENOISE_*）
    _denoise: list = []

    def eo_pre() -> bool:
        if not _denoise:
            _denoise.append(bool(preprocess) and easyocr_wants_denoise(png_bytes))
        return _denoise[0]

    # 遠端 GPU EasyOCR — 優先使用,失敗 fallback 本機
    if chosen == "easyocr":
        try:
            from . import ocr_remote_settings as _ors
            if _ors.is_enabled_and_configured():
                try:
                    words = _remote_easyocr_recognize(png_bytes, langs, preprocess=eo_pre())
                    if words:
                        return words, "easyocr-remote"
                    log.info("remote EasyOCR returned 0 words, trying local")
                except Exception as e:
                    log.warning("remote EasyOCR failed (%s) — falling back to local", e)
        except Exception as e:
            log.warning("remote OCR settings check failed: %s", e)

    if chosen == "easyocr":
        if is_easyocr_available() and allow_local_easyocr:
            words = _easyocr_recognize(png_bytes, langs, preprocess=eo_pre())
            if words:
                return words, "easyocr"
            log.info("EasyOCR returned 0 words, trying tesseract fallback")
        else:
            log.info("EasyOCR not available, falling back to tesseract")
        # Fallback to tesseract
        words = _tesseract_recognize(png_bytes, langs, preprocess=preprocess)
        return words, "tesseract" if words else "none"

    # chosen == 'tesseract'
    if is_tesseract_available():
        words = _tesseract_recognize(png_bytes, langs, preprocess=preprocess)
        if words:
            return words, "tesseract"
        log.info("Tesseract returned 0 words, trying easyocr fallback")
    if is_easyocr_available() and allow_local_easyocr:
        words = _easyocr_recognize(png_bytes, langs, preprocess=eo_pre())
        return words, "easyocr" if words else "none"
    return [], "none"
