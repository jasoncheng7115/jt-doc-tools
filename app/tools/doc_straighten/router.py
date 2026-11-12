"""掃描修正 —— 把拍歪、掃歪的文件裁掉黑邊、拉正、去除不勻的底色。

## 這一版只有自動模式

規劃分兩期（CLAUDE.md 的「待辦規劃【第 4 批】」）：第一期先把自動模式端到端
做出來，**拿真實掃描件實測品質**再決定第二期（使用者自己拉四個點）的細節。
合成樣本表現好不代表真實掃描件也好 —— 表單自動填寫就是因為真實語料缺某種
版型才漏掉一整類 bug。

## 為什麼不叫「校正」

這個專案裡「校正 / 校驗」已經是 LLM 逐欄比對的意思（表單填寫、送件檢核），
拿來當影像工具的名字會讓人以為跟 AI 有關 —— 而這支**完全不用 AI、不用 GPU**。

## 兩段式的背景作業

偵測（逐頁算四個角與歪斜角）與套用是兩件事，中間夾著使用者互動（第二期）。
這一期只有自動模式，所以是一個作業跑完；但**進度要逐頁報**，不然使用者
看不出它卡在第幾頁。
"""
from __future__ import annotations

import asyncio as _asyncio
import os
import re
import uuid
from pathlib import Path
from typing import List

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core import office_convert, pdf_preview, upload_owner as _uo
from ...core.job_manager import job_manager
from ...core.safe_paths import require_uuid_hex
from . import straighten_core as SC

router = APIRouter()

#: 暫存檔前綴。**要能被 `upload_owner.extract_upload_id` 切出 id** ——
#: 前綴裡不可以有底線以外的分隔（`wm_` 那次切錯讓歸屬檢查整個失效，v1.11.80）。
_PREFIX = "ds"

#: 算圖的解析度。200 dpi 是「看得清楚 + 跑得動」的平衡（實測 0.83 秒/頁）；
#: 300 dpi 細節好一點但 1.4 秒/頁、檔案也大一倍。
_DPI_CHOICES = (150, 200, 300)
#: 每個解析度的一句話（給 `option-card` 用）。**下拉選單看不出差別在哪** ——
#: 卡片把取捨直接寫在上面，使用者不必去讀底下那行灰字。
_DPI_NOTES = {150: "快，草稿夠用", 200: "平衡（建議）", 300: "細節好，慢一倍"}


def _src_path(upload_id: str) -> Path:
    return settings.temp_dir / f"{_PREFIX}_{upload_id}.pdf"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "doc_straighten.html",
                                      {"request": request,
                                       "dpi_choices": _DPI_CHOICES,
                                       "dpi_notes": _DPI_NOTES})


async def _to_pdf(data: bytes, filename: str, dst: Path, tag: str) -> None:
    """把一個上傳檔（PDF / 圖片 / 文書檔）轉成 PDF 放到 `dst`。

    `tag` 只是暫存檔名用的識別字，讓多檔同時處理時不會互相蓋掉。
    """
    if not data:
        raise HTTPException(400, "空檔案")
    name = Path(filename or "document").name
    low = name.lower()
    is_pdf = low.endswith(".pdf") or data[:4] == b"%PDF"
    is_image = low.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
                             ".webp", ".heic", ".heif"))
    if not (is_pdf or is_image or office_convert.is_office_file(name)):
        raise HTTPException(400, "只支援 PDF、圖片（手機拍的也可以）與文書檔")

    if is_pdf:
        dst.write_bytes(data)
    elif is_image:
        # 圖片先包成單頁 PDF，後面的流程就只有一條路
        raw = settings.temp_dir / f"{_PREFIX}raw_{tag}{Path(name).suffix}"
        raw.write_bytes(data)
        try:
            # HEIC（iPhone 拍的）要先轉成 PNG —— Pillow 本身不認那個格式，
            # `pillow_heif` 註冊之後才讀得到（`image_utils` 已經處理註冊）。
            from PIL import Image
            from ...core import image_utils as _iu  # noqa: F401 —— 註冊 HEIF
            with Image.open(raw) as img:
                w, h = img.size
            doc = fitz.open()
            # 以 200 dpi 反推頁面點數，讓輸出的紙張大小接近原始拍攝比例
            page = doc.new_page(width=w * 72 / 200, height=h * 72 / 200)
            page.insert_image(page.rect, filename=str(raw))
            doc.save(str(dst))
            doc.close()
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "圖片讀取失敗，可能已毀損或格式不支援")
        finally:
            raw.unlink(missing_ok=True)
    else:
        raw = settings.temp_dir / f"{_PREFIX}raw_{tag}{Path(name).suffix}"
        raw.write_bytes(data)
        try:
            office_convert.convert_to_pdf(raw, dst)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "文書檔轉換失敗，請確認檔案是否完整")
        finally:
            raw.unlink(missing_ok=True)
        if not dst.exists():
            raise HTTPException(400, "文書檔轉換失敗（沒有產出 PDF）")



async def _stash(request: Request, uploads: list[tuple[bytes, str]]) -> dict:
    """收下一批檔案，**每一個都要處理**，依上傳順序併成一份 PDF。

    使用者 2026-09-14 回報：拖兩個檔案進來只取了第一份。拉正的典型情境
    就是「一疊拍好的紙」——每張一個檔案，最後要的是**一份**整齊的 PDF。

    併起來之後，後面的流程（逐頁預覽、逐頁轉向、自己拉四個角、輸出）
    **一行都不用改** —— 它們本來就是以「頁」為單位。
    """
    if not uploads:
        raise HTTPException(400, "沒有檔案")
    upload_id = uuid.uuid4().hex
    _uo.record(upload_id, request)
    dst = _src_path(upload_id)

    parts: list[Path] = []
    #: 每個來源檔各佔幾頁 —— 縮圖列要標出「這一頁是哪個檔案來的」。
    #  併起來之後這件事從 PDF 本身**看不出來**，只有這裡知道。
    sources: list[dict] = []
    try:
        for i, (data, filename) in enumerate(uploads):
            part = (dst if len(uploads) == 1
                    else settings.temp_dir / f"{_PREFIX}p{i}_{upload_id}.pdf")
            await _to_pdf(data, filename, part, f"{upload_id}_{i}")
            parts.append(part)
            with fitz.open(str(part)) as one:
                sources.append({"name": Path(filename or "document").name,
                                "pages": one.page_count})
        if len(parts) > 1:
            merged = fitz.open()
            try:
                for part in parts:
                    with fitz.open(str(part)) as one:
                        merged.insert_pdf(one)
                merged.save(str(dst))
            finally:
                merged.close()
    finally:
        # 併完就把中間檔收掉（單檔時 parts[0] 就是 dst，不可以刪）
        if len(parts) > 1:
            for part in parts:
                part.unlink(missing_ok=True)

    try:
        with fitz.open(str(dst)) as doc:
            n = doc.page_count
    except Exception:  # noqa: BLE001
        dst.unlink(missing_ok=True)
        raise HTTPException(400, "檔案讀取失敗，可能已毀損")
    if n == 0:
        dst.unlink(missing_ok=True)
        raise HTTPException(400, "檔案沒有任何頁面")
    first = Path(uploads[0][1] or "document").stem
    return {"upload_id": upload_id, "pages": n, "name": first,
            "files": len(uploads), "sources": sources}


@router.post("/load")
async def load(request: Request,
               files: List[UploadFile] = File(default=[]),
               file: UploadFile | None = File(default=None)):
    """收多檔 —— **每一個都要處理**（依上傳順序併成一份 PDF）。

    共用的上傳元件送的是 `files`。**`file` 也要收** —— 改成多檔之前這支端點
    收的是單數的 `file`，直接換掉等於把既有的呼叫方式無聲弄壞
    （改完當下就有四支既有測試紅了，那些正是模擬舊呼叫的）。
    """
    got = list(files or [])
    if file is not None:
        got.append(file)
    if not got:
        raise HTTPException(422, "請選擇檔案")
    ups = [(await f.read(), f.filename or "") for f in got]
    return await _stash(request, ups)


@router.get("/thumb/{upload_id}/{page}")
async def thumb(upload_id: str, page: int, request: Request, rotate: int = 0):
    """「修正前」的縮圖。

    `rotate` **一定要吃** —— 使用者在這一頁按了轉向之後，左邊沒跟著轉的話
    兩張圖對不起來；更要緊的是**四個角的手柄是在「轉向後」的座標系**
    （`straighten_page` 先轉再抓角），左邊不轉就會拉不準
    （使用者 2026-09-14 回報）。
    """
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    rot = _clamp_rotate(rotate)
    src = _src_path(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    # 快取鍵要帶角度，否則轉過一次之後永遠拿到第一次那張
    out = settings.temp_dir / f"{_PREFIX}th_{upload_id}_{page}_{rot}.png"
    if not out.exists():
        base = settings.temp_dir / f"{_PREFIX}th_{upload_id}_{page}_0.png"
        if not base.exists():
            await pdf_preview.render_page_png_async(src, base, page - 1, dpi=70)
        if rot == 0:
            out = base
        else:
            def _rot():
                import cv2
                img = cv2.imread(str(base), cv2.IMREAD_UNCHANGED)
                code = {90: cv2.ROTATE_90_CLOCKWISE,
                        180: cv2.ROTATE_180,
                        270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rot]
                cv2.imwrite(str(out), cv2.rotate(img, code))
            await _asyncio.to_thread(_rot)
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "no-store"})


@router.post("/preview")
async def preview(request: Request, upload_id: str = Form(...),
                  page: int = Form(1), dpi: int = Form(200),
                  binarize: bool = Form(False),
                  detect_quad: bool = Form(True),
                  enhance: bool = Form(True),
                  rotate: int = Form(0),
                  quad: str = Form("")):
    """單頁的「修正後」預覽。

    **預覽跟產出走同一段程式**（`straighten_core.straighten_page`）——
    前端模擬的預覽遲早會跟實際輸出對不起來，而歪斜這種東西「差一點」
    使用者一眼就看得出來。
    """
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src_path(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")

    def _work():
        import cv2
        import numpy as np
        with fitz.open(str(src)) as doc:
            if page < 1 or page > doc.page_count:
                raise HTTPException(404, "頁碼超出範圍")
            pix = doc[page - 1].get_pixmap(dpi=_clamp_dpi(dpi), alpha=False)
            arr = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, pix.n)
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if pix.n >= 3 \
                else arr[:, :, 0]
        # **座標一路都是正規化 0~1、轉向後的** —— 換算與自動偵測都在
        # `straighten_page` 裡面做（那裡才知道轉向後的長寬）。
        # 這裡自己換算過一次，結果是用**未轉**的長寬去乘，轉 90° 時整組跑掉
        # （2026-09-14 使用者回報）。
        user_quad = _parse_quad(quad)
        # 彩色一起送（見 `_paper_mask`）—— 陰影裡的紙只有靠色度才救得回來。
        fixed, res = SC.straighten_page(gray, quad=user_quad,
                                        rgb=arr if pix.n >= 3 else None,
                                        detect_quad=detect_quad,
                                        do_binarize=binarize,
                                        dpi=_clamp_dpi(dpi), page_no=page,
                                        rotate_deg=_clamp_rotate(rotate),
                                        enhance=enhance)
        # **`imencode` 吃 BGR，PyMuPDF 給的是 RGB** —— 不轉的話預覽的紅藍
        # 會對調（而產出是對的，於是「預覽跟產出不一樣」）。
        small = cv2.resize(fixed, None, fx=0.45, fy=0.45,
                           interpolation=cv2.INTER_AREA)
        png = cv2.imencode(".png", cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
                           if small.ndim == 3 else small)[1]
        # **每一次預覽一個檔名，而且先寫暫存檔再換名**（v1.16.10，CI 抓到的時好時壞）。
        # 原本同一頁固定寫同一個檔：拖曳 / 轉向會連發好幾個請求，前端取消舊的，
        # 但**伺服器那邊照樣算完、晚一點寫回同一個檔** —— 蓋掉最新那張
        # （畫面顯示的是舊的結果，狀態列卻寫著新的），或讓瀏覽器讀到寫一半的檔
        # （圖載不出來，右邊一直是空的）。
        ver = uuid.uuid4().hex[:12]
        out = _pv_path(upload_id, page, ver)
        tmp = out.with_name(out.name + ".part")
        tmp.write_bytes(png.tobytes())
        os.replace(tmp, out)
        _prune_previews(upload_id, page, keep=out)
        # 自動抓到的四角也回給前端**當作拖曳的起點** —— 使用者只要修不滿意的
        # 那幾個角，不必四個重拉。`res.quad` 已經是正規化、轉向後的座標，
        # **這裡不可以再自己換算一次**（原本用未轉的長寬換算，轉 90° 之後
        # 畫面上的手柄會落在完全不相干的位置）。
        auto = res.quad
        return {"url": f"/tools/doc-straighten/preview-img/{upload_id}/{page}?v={ver}",
                "angle": res.angle, "residual": res.residual,
                "quad_found": res.quad_found, "ms": res.ms,
                "rotate": res.rotate_deg, "quad": auto,
                # 修正後**實際**的像素大小 —— 畫面拿它預先填好「輸出尺寸」，
                # 使用者要改再改（使用者 2026-09-14：「預先自動抓 但 user
                # 可以自己改」）。
                "width": res.width, "height": res.height, "dpi": _clamp_dpi(dpi)}

    return await _asyncio.to_thread(_work)


_PV_VER = re.compile(r"^[0-9a-f]{12}$")
#: 同一頁留幾張預覽。拖曳時一秒可能連發好幾個請求，每一個都寫一張 ——
#: 只留最新的幾張；**不可以只留一張**：被取消的舊請求可能在新的之後才寫完，
#: 只留一張的話它會把畫面正要載入的那張刪掉。
_PV_KEEP = 6


def _pv_path(upload_id: str, page: int, ver: str | None = None) -> Path:
    suffix = f"_{ver}" if ver else ""
    return settings.temp_dir / f"{_PREFIX}pv_{upload_id}_{int(page)}{suffix}.png"


def _prune_previews(upload_id: str, page: int, keep: Path) -> None:
    try:
        olds = sorted(settings.temp_dir.glob(f"{_PREFIX}pv_{upload_id}_{int(page)}_*.png"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for p in olds[_PV_KEEP:]:
        if p != keep:
            try:
                p.unlink()
            except OSError:
                pass


@router.get("/preview-img/{upload_id}/{page}")
async def preview_img(upload_id: str, page: int, request: Request, v: str = ""):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    if v and not _PV_VER.match(v):
        raise HTTPException(404, "預覽不存在（請重新產生）")
    # 沒帶 `v` 的是舊版網址（升級前開著的分頁）—— 照舊找固定檔名
    out = _pv_path(upload_id, page, v or None)
    if not out.exists():
        raise HTTPException(404, "預覽不存在（請重新產生）")
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "no-store"})



def _clamp_rotate(deg) -> int:
    """只收 0 / 90 / 180 / 270。**其餘一律當 0**，不要丟例外 ——
    這是畫面上按鈕送來的值，送壞了不該讓整個預覽失敗。"""
    try:
        d = int(deg) % 360
    except (TypeError, ValueError):
        return 0
    return d if d in (0, 90, 180, 270) else 0


def _parse_quad(raw: str):
    """解析前端送來的四個角：`"x1,y1,x2,y2,x3,y3,x4,y4"`，**正規化 0~1**。

    格式不對就回 `None`（退回自動偵測）—— 這是使用者拖出來的東西，
    不該因為少一個數字就整個失敗。範圍稍微超出一點（拖到圖外）放行並夾住，
    `quad_is_sane()` 還會再擋一次自交 / 過小。
    """
    if not raw:
        return None
    try:
        nums = [float(x) for x in raw.replace(" ", "").split(",") if x != ""]
    except ValueError:
        return None
    if len(nums) != 8:
        return None
    pts = [(min(1.0, max(0.0, nums[i])), min(1.0, max(0.0, nums[i + 1])))
           for i in range(0, 8, 2)]
    return pts

def _clamp_dpi(dpi: int) -> int:
    """夾在允許的範圍內 —— 前端的下拉只是提示，API 呼叫者不受它拘束。

    給到 1200 dpi 會讓一頁算圖吃掉幾百 MB 記憶體。
    """
    try:
        d = int(dpi)
    except Exception:  # noqa: BLE001
        return 200
    return min(_DPI_CHOICES[-1], max(_DPI_CHOICES[0], d))


#: 輸出尺寸的單位。`px` 直接就是像素；`mm` 依 dpi 換算成像素。
_SIZE_UNITS = ("px", "mm")
#: 一張 A4 在 600 dpi 是 4960×7016 —— 再大就只是把檔案撐爆，
#: 而且 `fit_to` 會把整張圖重新取樣，記憶體跟著翻倍。
_MAX_OUT_PX = 12000


def _parse_out_size(w: str, h: str, unit: str, dpi: int):
    """畫面上填的輸出尺寸 → 像素。空的（或不合法）就回 `None` ＝ 不指定。

    **不合法一律當成沒填**（回 `None`），不要丟例外 —— 使用者打錯一個數字
    不該看到 500（本專案「不合法一律當成找不到」那條的同一個道理）。
    """
    unit = unit if unit in _SIZE_UNITS else "px"
    try:
        fw, fh = float(w), float(h)
    except (TypeError, ValueError):
        return None
    if not (fw > 0 and fh > 0):
        return None
    if unit == "mm":
        pw, ph = SC.mm_to_px(fw, dpi), SC.mm_to_px(fh, dpi)
    else:
        pw, ph = int(round(fw)), int(round(fh))
    if not (0 < pw <= _MAX_OUT_PX and 0 < ph <= _MAX_OUT_PX):
        return None
    return pw, ph


def _run_job(src: Path, out: Path, *, dpi: int, binarize: bool,
             detect_quad: bool, stem: str, enhance: bool = True,
             out_size=None, overrides: dict | None = None):
    def run(job):
        job.message = "修正中…"

        def progress(done: int, total: int):
            job.progress = (done / max(1, total)) * 0.97
            job.message = f"修正中… {done}/{total} 頁"

        results = SC.straighten_pdf(src, out, dpi=dpi, do_binarize=binarize,
                                    detect_quad=detect_quad, enhance=enhance,
                                    out_size=out_size, overrides=overrides,
                                    progress=progress,
                                    cancelled=lambda: job.cancelled)
        job.result_path = out
        job.result_filename = f"{stem}_straightened.pdf"
        done = [r for r in results if not r.skipped]
        skipped = len(results) - len(done)
        worst = max((abs(r.residual) for r in done), default=0.0)
        # **摘要放 meta** —— `Job.to_public()` 只送 meta，放 job.result 畫面看不到
        job.meta["pages"] = len(results)
        job.meta["worst_residual"] = round(worst, 2)
        job.meta["quad_pages"] = sum(1 for r in results if r.quad_found)
        job.meta["kept_pages"] = skipped
        # 使用者自己動過的頁數 —— 完成訊息要講出來，不然他不確定有沒有吃到
        job.meta["manual_pages"] = len(overrides or {})
        job.progress = 1.0
        # **原樣保留幾頁一定要講出來** —— 使用者丟一份原生 PDF 進來，
        # 看到「完成」卻什麼都沒變的話會以為工具壞了。
        msg = f"完成（{len(results)} 頁"
        if done:
            msg += f"，處理 {len(done)} 頁、殘留歪斜最大 {worst:.2f}°"
        if skipped:
            msg += f"；{skipped} 頁本來就是正的且有文字層，原樣保留（文字不會變成圖片）"
        if overrides:
            msg += f"；{len(overrides)} 頁套用了你自己調的轉向 / 四個角"
        job.message = msg + "）"
    return run



def _parse_overrides(raw: str, page_count: int) -> dict:
    """解析逐頁覆寫：`{"3": {"rotate": 90, "quad": [[x,y] × 4]}, ...}`。

    **壞掉的項目個別丟掉，不要整批失敗** —— 這是使用者在畫面上調了半天的
    東西，其中一頁的資料有問題不該讓整份工作送不出去。頁碼是 1-based
    （畫面上看到的那個數字），超出範圍的直接忽略。
    """
    if not raw:
        return {}
    import json
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[int, dict] = {}
    for k, v in data.items():
        try:
            page = int(k)
        except (TypeError, ValueError):
            continue
        if not (1 <= page <= page_count) or not isinstance(v, dict):
            continue
        item: dict = {}
        rot = _clamp_rotate(v.get("rotate", 0))
        if rot:
            item["rotate"] = rot
        q = v.get("quad")
        if isinstance(q, list) and len(q) == 4 and all(
                isinstance(pt, (list, tuple)) and len(pt) == 2 for pt in q):
            try:
                item["quad"] = [[min(1.0, max(0.0, float(x))),
                                 min(1.0, max(0.0, float(y)))] for x, y in q]
            except (TypeError, ValueError):
                pass
        if item:
            out[page] = item
    return out

@router.post("/submit")
async def submit(request: Request, upload_id: str = Form(...),
                 dpi: int = Form(200), binarize: bool = Form(False),
                 detect_quad: bool = Form(True), enhance: bool = Form(True),
                 out_name: str = Form(""),
                 size_w: str = Form(""), size_h: str = Form(""),
                 size_unit: str = Form("px"),
                 overrides: str = Form("")):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src_path(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    with fitz.open(str(src)) as doc:
        page_count = doc.page_count
    stem = Path(out_name or "document").stem or "document"
    out = settings.temp_dir / f"{_PREFIX}out_{upload_id}.pdf"
    ov = _parse_overrides(overrides, page_count)
    job = job_manager.submit(
        "doc-straighten",
        _run_job(src, out, dpi=_clamp_dpi(dpi), binarize=binarize,
                 detect_quad=detect_quad, stem=stem, enhance=enhance,
                 out_size=_parse_out_size(size_w, size_h, size_unit,
                                          _clamp_dpi(dpi)),
                 overrides=ov),
        request=request,
        meta={"filename": f"{stem}.pdf", "count": page_count})
    return {"job_id": job.id}


@router.post("/api/doc-straighten", include_in_schema=True)
async def api_doc_straighten(request: Request, file: UploadFile = File(...),
                             dpi: int = Form(200), binarize: bool = Form(False),
                             detect_quad: bool = Form(True),
                             enhance: bool = Form(True),
                             size_w: str = Form(""), size_h: str = Form(""),
                             size_unit: str = Form("px")):
    """一次呼叫：上傳 → 拉正 → 直接回 PDF（同步，適合小檔）。

    `size_w` / `size_h` / `size_unit`（px / mm）：輸出每頁的尺寸，跟網頁一樣；沒填或不合法 ＝ 照拉正後的大小。
    """
    info = await _stash(request, [(await file.read(), file.filename or "")])
    upload_id = info["upload_id"]
    src = _src_path(upload_id)
    out = settings.temp_dir / f"{_PREFIX}out_{upload_id}.pdf"

    def _work():
        results = SC.straighten_pdf(src, out, dpi=_clamp_dpi(dpi),
                                    do_binarize=binarize,
                                    detect_quad=detect_quad, enhance=enhance,
                                    out_size=_parse_out_size(size_w, size_h, size_unit, _clamp_dpi(dpi)))
        worst = max((abs(r.residual) for r in results), default=0.0)
        return results, worst

    results, worst = await _asyncio.to_thread(_work)
    # `FileResponse(filename=...)` 自己會處理 RFC 5987（中文檔名）——
    # `content_disposition()` 回的是**字串**，只在手動組標頭時才用得上。
    return FileResponse(
        str(out), media_type="application/pdf",
        filename=f"{info['name']}_straightened.pdf",
        headers={"X-Straighten-Pages": str(len(results)),
                 "X-Straighten-Worst-Residual": f"{worst:.2f}"})
