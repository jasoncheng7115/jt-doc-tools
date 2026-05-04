"""Endpoints for PDF 密碼解除."""
from __future__ import annotations

import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import List

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...config import settings
from ...core.job_manager import job_manager


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_decrypt.html", {"request": request})


@router.post("/submit")
async def submit(
    file: List[UploadFile] = File(...),
    password: str = Form(""),
    use_filename_as_password: str = Form(""),  # "1" / "true" / "on" → 開
    thsr_mode: str = Form(""),                  # "1" → 啟用台灣高鐵模式
    thsr_date_from: str = Form(""),             # YYYY-MM-DD
    thsr_date_to: str = Form(""),               # YYYY-MM-DD
    thsr_date_format: str = Form("YYYYMMDD"),   # 嘗試密碼的日期格式
):
    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")
    use_fname_pw = str(use_filename_as_password).lower() in ("1", "true", "on", "yes")
    use_thsr = str(thsr_mode).lower() in ("1", "true", "on", "yes")

    # 高鐵模式：產生日期範圍內每一天的 YYYYMMDD 字串作為密碼候選。
    # THSR 票 PDF 的開啟密碼 = 出發日期，格式如 "20240315"。
    # 範圍上限 90 天（防呆，避免 user 不小心填一年逐日試 365 次 × 多檔）。
    thsr_pw_candidates: list[str] = []
    if use_thsr:
        from datetime import date as _date, timedelta as _td
        try:
            d_from = _date.fromisoformat(thsr_date_from) if thsr_date_from else None
            d_to = _date.fromisoformat(thsr_date_to) if thsr_date_to else None
        except ValueError:
            raise HTTPException(400, "高鐵模式：日期格式錯誤（需 YYYY-MM-DD）")
        if not d_from or not d_to:
            raise HTTPException(400, "高鐵模式：請選擇起迄日期")
        if d_from > d_to:
            d_from, d_to = d_to, d_from
        if (d_to - d_from).days > 100:
            raise HTTPException(400, "高鐵模式：日期範圍最多約 3 個月")
        # 把 user 給的日期格式 token 轉成 strftime pattern。預設 YYYYMMDD。
        # 接受 user 自訂任意 token 組合（例如 `YYYY/MM/DD`、`DD-MM-YY` 等）。
        fmt = (thsr_date_format or "YYYYMMDD")
        # 安全：只准許日期 token 與常見分隔；避免 user 注入 strftime 怪 token
        if not re.fullmatch(r"[YyMmDd\-/._ ]+", fmt):
            raise HTTPException(400, "高鐵模式：日期格式只能含 Y M D 與 - / . _ 空白")
        # token 替換順序：先 4-char Y → 2-char Y，避免吃錯
        strftime_fmt = (fmt
                        .replace("YYYY", "%Y").replace("YY", "%y")
                        .replace("MM", "%m").replace("DD", "%d"))
        d = d_from
        while d <= d_to:
            thsr_pw_candidates.append(d.strftime(strftime_fmt))
            d += _td(days=1)

    bid = uuid.uuid4().hex
    bdir = settings.temp_dir / f"dec_{bid}"
    bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
        data = await f.read()
        if not data:
            raise HTTPException(400, f"空檔：{f.filename}")
        sp = bdir / f"{i:03d}_{Path(f.filename).name}"
        sp.write_bytes(data)
        saved.append((sp, f.filename))

    def run(job):
        outs: list[Path] = []
        bad: list[str] = []
        for fi, (sp, orig) in enumerate(saved):
            job.message = f"解除 {orig}"
            job.progress = (fi / len(saved)) * 0.95
            # 候選密碼順序：
            #   1. 高鐵模式日期（每天 YYYYMMDD）— 通常 30-60 個，PyMuPDF
            #      authenticate 是微秒級無感
            #   2. 「檔名為密碼」— 每個檔取自己的主檔名
            #   3. manual `password` 欄位
            # 用第一個成功的；輸出檔名根據哪個 candidate 成功而異。
            pw_candidates: list[tuple[str, str]] = []  # (candidate, source_tag)
            if use_thsr:
                for d in thsr_pw_candidates:
                    pw_candidates.append((d, "thsr"))
            if use_fname_pw:
                pw_candidates.append((Path(orig).stem, "fname"))
            if password:
                pw_candidates.append((password, "manual"))
            with fitz.open(str(sp)) as doc:
                used_pw = None; used_src = None
                if doc.needs_pass:
                    for pw, src in pw_candidates:
                        if pw and doc.authenticate(pw):
                            used_pw = pw; used_src = src; break
                    if used_pw is None:
                        bad.append(orig)
                        continue
                # 輸出檔名規則：
                #   - 高鐵模式成功 → 用日期作為檔名（user 規格）
                #   - 其他 → 維持 `<原檔名>_decrypted.pdf`
                if used_src == "thsr":
                    # 用 used_pw 當檔名 — 但日期格式可能含 / 或 . 等檔名禁字，
                    # sanitize 成 _。例：「2024/03/15」→「2024_03_15.pdf」
                    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", used_pw)
                    op = bdir / f"{safe_name}.pdf"
                    n_dup = 1
                    while op.exists():
                        n_dup += 1
                        op = bdir / f"{safe_name}_{n_dup}.pdf"
                else:
                    op = bdir / f"{Path(orig).stem}_decrypted.pdf"
                # Saving with encryption=NONE strips all protection.
                doc.save(str(op), encryption=fitz.PDF_ENCRYPT_NONE,
                         garbage=3, deflate=True)
                outs.append(op)
        if bad and not outs:
            raise RuntimeError(f"密碼不正確：{', '.join(bad)}")
        if bad:
            job.message = f"完成，但以下檔案密碼錯誤已跳過：{', '.join(bad)}"
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            zname = f"decrypted_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs: zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0
        if not job.message or "已跳過" not in job.message:
            job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("pdf-decrypt", run, meta={"count": len(saved)})
    return {"job_id": job.id}
