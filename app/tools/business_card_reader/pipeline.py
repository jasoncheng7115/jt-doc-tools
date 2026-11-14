"""名片辨識的處理流程：收檔 → 頁 → 偵測卡面 → 逐卡辨識（背景）→ 聯絡人。

* **收檔**：JPEG / PNG / HEIC（主機有解碼元件才收）/ PDF（逐頁畫成 200 dpi）。先依 EXIF 轉正，再存成頁影像；
  框座標都以轉正後的頁為準（規格：先校正 EXIF 方向，再保存框座標）。一般照片不轉成 PDF。
* **偵測**：單卡模式每頁一張（找不到邊界就用整張，疑似多卡標 `multi_hint`）；多卡模式每頁找全部的卡，
  零張不當成功（頁標 `zero`，等使用者補框或重拍）。
* **辨識**：背景執行緒池（每台 `WORKERS` 條）。一張卡失敗只標那一張，可單獨重試；改過框的卡才重跑。
  QR Code 用電子發票同一個解碼器；主機沒有解碼元件就略過 QR，不擋辨識。
"""
from __future__ import annotations

import io
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from . import card_ocr, detector, extract, store

log = logging.getLogger(__name__)

WORKERS = 2
#: 一頁解碼後的像素上限（防解壓縮炸彈）；手機照片 4800 萬畫素以內
MAX_PIXELS = 60_000_000
#: 頁影像存檔的長邊（偵測與裁卡用；太大只是浪費）
PAGE_LONG_EDGE = 3600
PDF_DPI = 200

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
PDF_EXTS = {".pdf"}

_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="bizcard")
_lock = threading.Lock()
_running: set[str] = set()
TOOL_ID = "business-card-reader"
#: 上傳時同時找卡面的頁數
DETECT_WORKERS = 4
_inflight: set[str] = set()          # 正在辨識某一張卡的批次（改模式要等這一張做完）
_origins: dict[str, str] = {}


class InputError(Exception):
    pass


def accepted_exts() -> set[str]:
    from ...core.image_utils import heif_available
    exts = set(IMAGE_EXTS | PDF_EXTS)
    if not heif_available():
        exts -= {".heic", ".heif"}
    return exts


def _open_image(data: bytes, name: str) -> Image.Image:
    from ...core.image_utils import HEIF_MISSING_HINT, heif_available
    ext = Path(name).suffix.lower()
    if ext in (".heic", ".heif") and not heif_available():
        raise InputError(HEIF_MISSING_HINT)
    # 不改 Pillow 的全域上限（會影響其他工具）：開檔只讀檔頭，先看尺寸再解碼
    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
    except Exception:
        raise InputError(f"「{name}」不是看得懂的圖片")
    if w * h > MAX_PIXELS:
        raise InputError(f"「{name}」的解析度太大（上限約 {MAX_PIXELS // 1_000_000} 百萬畫素）")
    try:
        im.load()
    except Exception:
        raise InputError(f"「{name}」不是看得懂的圖片")
    im = ImageOps.exif_transpose(im)
    return detector_rgb(im)


def detector_rgb(im: Image.Image) -> Image.Image:
    return Image.fromarray(detector.to_rgb_array(im))


def _shrink(im: Image.Image) -> Image.Image:
    if max(im.size) > PAGE_LONG_EDGE:
        im = im.copy()
        im.thumbnail((PAGE_LONG_EDGE, PAGE_LONG_EDGE), Image.LANCZOS)
    return im


def explode(files: list[tuple[str, bytes]]) -> list[tuple[str, int, Image.Image]]:
    """上傳的檔案 → 頁影像清單 `(檔名, 第幾頁, 影像)`。超過上限直接說明怎麼拆批。"""
    if not files:
        raise InputError("請選擇名片的照片或掃描檔")
    if len(files) > store.MAX_FILES:
        raise InputError(f"一次最多 {store.MAX_FILES} 個檔案，請分批上傳")
    ok = accepted_exts()
    out: list[tuple[str, int, Image.Image]] = []
    for name, data in files:
        ext = Path(name).suffix.lower()
        if ext not in ok:
            if ext in (".heic", ".heif"):
                from ...core.image_utils import HEIF_MISSING_HINT
                raise InputError(HEIF_MISSING_HINT)
            raise InputError(f"「{name}」的格式不支援（可用 JPEG、PNG、HEIC、PDF）")
        if ext in PDF_EXTS or data[:5] == b"%PDF-":
            import fitz
            try:
                doc = fitz.open(stream=data, filetype="pdf")
            except Exception:
                raise InputError(f"「{name}」不是有效的 PDF")
            with doc:
                if doc.needs_pass:
                    raise InputError(f"「{name}」有開啟密碼，請先解除密碼")
                if len(out) + doc.page_count > store.MAX_PAGES:
                    raise InputError(f"一個批次最多 {store.MAX_PAGES} 頁，請分批上傳")
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(dpi=PDF_DPI)
                    if pix.width * pix.height > MAX_PIXELS:
                        raise InputError(f"「{name}」第 {i + 1} 頁太大")
                    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    out.append((name, i + 1, im))
        else:
            out.append((name, 1, _shrink(_open_image(data, name))))
        if len(out) > store.MAX_PAGES:
            raise InputError(f"一個批次最多 {store.MAX_PAGES} 頁，請分批上傳")
    return out


def create(owner: str, owner_label: str, mode: str, files: list[tuple[str, bytes]], notes: dict,
           langs: str = card_ocr.DEFAULT_LANGS, origin: str = "", request=None) -> str:
    """建立批次、存頁影像、偵測卡面。單卡模式偵測完直接開始辨識；多卡模式停在「確認框位」。"""
    pages = explode(files)
    bid = store.create_batch(owner, owner_label, mode, notes, langs)
    set_origin(bid, origin)
    try:
        src = store.batch_dir(bid) / "src"
        src.mkdir(parents=True, exist_ok=True)
        pids = [store.add_page(bid, i, name, page_no, im.width, im.height) for i, (name, page_no, im) in enumerate(pages)]

        def work(k):                                   # 存頁影像＋找卡面（不碰資料庫，可以並行）
            im = pages[k][2]
            im.save(store.page_image_path(bid, pids[k]), "JPEG", quality=92)
            return _detect_items(im, mode)
        # 好幾頁一起找（OpenCV / numpy 大多不佔 GIL）：50 張照片原本要等將近 1 分鐘，可能撞到反向代理的逾時
        with ThreadPoolExecutor(max_workers=max(1, min(DETECT_WORKERS, len(pages)))) as ex:
            results = list(ex.map(work, range(len(pages))))
        total = 0
        for pid, (items, status, hint) in zip(pids, results):
            total += len(items)
            if total > store.MAX_CARDS:
                raise InputError(f"一個批次最多 {store.MAX_CARDS} 張卡面，請分批上傳")
            store.set_page(pid, status=status, multi_hint=hint)
            store.replace_page_cards(bid, pid, items)
        store.set_batch(owner, bid, status="frames" if mode == "multi" else "recognizing")
    except Exception:
        store.delete_batch(owner, bid)
        raise
    if mode == "single":
        start(owner, bid, request=request)
    return bid


def _detect_items(im: Image.Image, mode: str) -> tuple[list[dict], str, int]:
    """找卡面（純計算，不碰資料庫）：回（卡面框, 頁狀態, 像不像一張照片多張名片）。"""
    if mode == "single":
        card, multi = detector.detect_single(im)
        return ([{"quad": card.quad, "source": "whole" if "whole" in card.flags else "auto", "flags": card.flags}],
                "detected", 1 if multi else 0)
    found = detector.detect_cards(im)
    return [{"quad": c.quad, "source": "auto", "flags": c.flags} for c in found], ("detected" if found else "zero"), 0


def _detect(im: Image.Image, mode: str, pid: str) -> list[dict]:
    items, status, hint = _detect_items(im, mode)
    store.set_page(pid, status=status, multi_hint=hint)
    return items


class BusyError(Exception):
    pass


def change_mode(owner: str, bid: str, mode: str, page_ids: Optional[list[str]] = None,
                wait: float = 60.0, request=None) -> list[str]:
    """上傳後改收件模式，不用重新上傳（使用者 2026-11-13：一張圖片多張名片但忘了選模式）。

    `page_ids` 只改這幾頁（預設整批）：其他頁的框與辨識結果原封不動。改到的頁重新偵測卡面，
    那幾頁原本的辨識結果與人工修改會清掉（畫面上先問過）。
    改成「一張圖片多張名片」→ 停在確認範圍；改成「每張圖片一張名片」→ 直接辨識。
    背景還在辨識時先讓它停下（改狀態後跑到下一張就會停），等手上那一張做完才動框，免得寫進已刪的卡。
    """
    if mode not in ("single", "multi"):
        raise InputError("mode 只能是 single 或 multi")
    store.get_batch(owner, bid)
    pages = store.pages(bid)
    known = {p["id"] for p in pages}
    if page_ids:
        if not isinstance(page_ids, list) or any(not isinstance(x, str) or x not in known for x in page_ids):
            raise InputError("pages 裡有不屬於這個批次的頁")
        target = [p for p in pages if p["id"] in set(page_ids)]
    else:
        target = pages
    store.set_batch(owner, bid, mode=mode, status="frames" if mode == "multi" else "recognizing")
    end = time.time() + wait
    while True:
        with _lock:
            running = bid in _inflight                 # 只等「手上那一張」；排隊中的作業之後看到狀態就會停
        if not running:
            break
        if time.time() > end:
            raise BusyError("還有一張名片正在辨識，請稍候再試")
        time.sleep(0.2)
    store.reset_stuck_batch(bid)
    for p in target:
        im = Image.open(store.page_image_path(bid, p["id"]))
        im.load()
        store.replace_page_cards(bid, p["id"], _detect(im.convert("RGB"), mode, p["id"]))
    if mode == "single":
        start(owner, bid, request=request)
    return [p["id"] for p in target]


def start(owner: str, bid: str, request=None) -> int:
    """把這批待辨識的卡排進背景（已經在跑的不重複排）。回排了幾張。

    從畫面 / API 來的（有 `request`）走全站的作業系統：出現在「我的作業」、有進度、可以取消、完成通知照通知設定、
    跟其他 OCR 工具共用同時處理的上限。服務啟動時恢復未完成的（沒有 `request`）用本工具自己的背景執行緒。
    """
    b = store.get_batch(owner, bid)
    todo = store.pending_cards(bid)
    if todo:
        store.set_batch(owner, bid, status="recognizing")
    elif b["status"] in ("frames", "recognizing"):
        store.set_batch(owner, bid, status="review")      # 框都沒改、沒有要辨識的：直接到校對
        return 0
    with _lock:
        if bid in _running:
            return len(todo)
        _running.add(bid)
    if request is None:
        _pool.submit(_run_batch, owner, bid)
        return len(todo)
    try:
        from ...core import job_manager as jm
        jm.job_manager.submit(TOOL_ID, lambda job: _run_batch(owner, bid, job=job), request=request, meta={
            "batch": bid, "filename": f"名片辨識（{len(todo)} 張）",
            "view_url": f"/tools/business-card-reader/#batch={bid}"})
    except Exception:                                  # noqa: BLE001 —— 作業系統排不進去就用自己的執行緒，不可以卡住
        log.exception("名片辨識：排進作業系統失敗，改用背景執行緒")
        _pool.submit(_run_batch, owner, bid)
    return len(todo)


def set_origin(bid: str, origin: str) -> None:
    """送出辨識的那個瀏覽器所在的網址（完成通知裡「回到名片辨識頁」的連結；只存在記憶體）。"""
    if origin:
        with _lock:
            _origins[bid] = origin[:300]


def _run_batch(owner: str, bid: str, job=None) -> None:
    t0 = time.time()
    processed: list[str] = []
    try:
        total = max(1, len(store.pending_cards(bid)))
        while True:
            todo = store.pending_cards(bid)
            if not todo:
                break
            stopped = False
            for cid in todo:
                if store.get_batch(owner, bid)["status"] == "frames":
                    stopped = True                    # 改成多張模式：停下來等使用者確認範圍
                    break
                if job is not None and job.cancelled:
                    stopped = True                    # 在「我的作業」按了取消：還沒辨識的留著，之後可以再按辨識
                    store.set_batch(owner, bid, status="review")
                    break
                if store.claim_card(cid):
                    with _lock:
                        _inflight.add(bid)
                    try:
                        _recognize(owner, bid, cid)
                    finally:
                        with _lock:
                            _inflight.discard(bid)
                    processed.append(cid)
                    if job is not None:
                        job.progress = min(0.99, len(processed) / total)
                        job.message = f"已辨識 {len(processed)} / {total} 張"
            if stopped:
                return
        try:
            if store.get_batch(owner, bid)["status"] != "frames":
                store.set_batch(owner, bid, status="review")
        except store.NotFound:
            processed = []                            # 辨識中被刪掉：什麼都不留，也不通知
        if processed and job is None:                 # 走作業系統的由它照通知設定通知，不重複
            _notify_done(owner, bid, processed, time.time() - t0)
    except store.NotFound:
        pass
    except Exception:                                 # noqa: BLE001
        log.exception("名片批次辨識失敗")
    finally:
        with _lock:
            _running.discard(bid)
            _origins.pop(bid, None)


def _notify_done(owner: str, bid: str, cids: list[str], elapsed: float) -> None:
    from . import notify
    try:
        store.get_batch(owner, bid)
        status = {c["id"]: c["status"] for c in store.cards(bid)}
    except store.NotFound:
        return
    failed = sum(1 for c in cids if status.get(c) == "failed")
    with _lock:
        origin = _origins.get(bid, "")
    notify.batch_finished(owner, len(cids) - failed, failed, elapsed, origin)


def _recognize(owner: str, bid: str, cid: str) -> None:
    try:
        card = store.get_card(owner, bid, cid)
    except store.NotFound:
        return
    t0 = time.monotonic()
    try:
        page = Image.open(store.page_image_path(bid, card["page_id"]))
        page.load()
        crop = detector.warp_card(page, card["quad"], rotate=card["rotate"])
        t_ocr = time.monotonic()
        res = card_ocr.ocr_card(crop)
        ocr_secs = time.monotonic() - t_ocr
        img = res["image"]
        qr = _qr(img)
        flags = list(card["flags"])
        if detector.blur_score(img) < 40:
            flags.append("blurry")
        if detector.glare_ratio(img) > 0.02:
            flags.append("glare")
        out = store.card_image_path(bid, cid)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "JPEG", quality=90)
        lines = [ln.to_dict() for ln in res["lines"]]
        fields = extract.extract_fields(lines, qr)
        if not lines and not qr:
            raise RuntimeError("這張卡沒有辨識出任何文字")
        store.set_card(cid, status="done", error="", ocr={
            "lines": lines, "engine": res["engine"], "rotation": res["rotation"], "quality": res["quality"],
            "qr": qr, "flags": flags, "size": list(img.size),
            # 耗時（秒）：`ocr_secs` 只算文字辨識（含低信心行重讀），`secs` 是這張卡從拉正到擷取完的全部時間
            "ocr_secs": round(ocr_secs, 2), "secs": round(time.monotonic() - t0, 2)})
        # 整批的人工補充（活動 / 來源 / 標籤 / 備註）＋這張卡自己的備註 → 這位聯絡人的備註（不是名片原文）
        bn = dict(store.get_batch(owner, bid)["notes"] or {})
        if card["memo"]:
            bn["memo"] = (bn.get("memo", "") + "\n" + card["memo"]).strip()
        store.upsert_card_contact(bid, cid, extract.to_public(fields), card["idx"], notes=bn)
    except store.NotFound:
        return
    except Exception as e:                            # noqa: BLE001 —— 一張失敗不可以拖垮整批
        log.warning("名片辨識失敗（卡面 %s）：%s", cid, type(e).__name__)
        msg = str(e) if isinstance(e, (RuntimeError, InputError)) else "辨識失敗，可以重試或調整框位"
        try:
            store.set_card(cid, status="failed", error=msg[:200])
        except Exception:                             # noqa: BLE001
            pass


def _qr(img: Image.Image) -> list[str]:
    try:
        from ..einvoice_scan import qr_decoder
        if not qr_decoder.is_qr_backend_available():
            return []
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return [q for q in qr_decoder.decode_image(buf.getvalue())[:5] if len(q) < 4000]
    except Exception:                                 # noqa: BLE001 —— QR 只是加分，失敗不影響辨識
        return []


def retry(owner: str, bid: str, cid: str, request=None) -> None:
    card = store.get_card(owner, bid, cid)
    if card["status"] in ("ocr",):
        return
    store.set_card(cid, status="pending", error="")
    start(owner, bid, request=request)


def resume_all() -> int:
    """服務啟動時：卡在「辨識中」太久的回到待辨識，有待辨識的批次重新排進背景。"""
    n = 0
    try:
        store.reset_stuck(older_than=0)
        for bid, owner in store.iter_owner_batches():
            if store.pending_cards(bid):
                b = store.get_batch(owner, bid)
                if b["status"] in ("recognizing", "review"):
                    start(owner, bid)
                    n += 1
    except Exception:                                 # noqa: BLE001
        log.exception("名片辨識：恢復背景工作失敗")
    return n


def wait_idle(timeout: float = 60.0) -> bool:
    """測試用：等背景辨識全部做完。"""
    import time
    end = time.time() + timeout
    while time.time() < end:
        with _lock:
            if not _running:
                return True
        time.sleep(0.05)
    return False
