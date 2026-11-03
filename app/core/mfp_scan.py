"""事務機作業：排隊 → 掃描（eSCL）或機內信箱取件（WebDAV，推測實作）→ 存進送出者的工作區。

* 每件作業一條背景執行緒：先在 `mfp_queue` 拿這台設備的號碼牌（同一台一次只做一件，見那裡的說明），
  輪到才連設備；排隊中可以取消（不會碰到設備），掃描中取消會刪掉設備上的掃描工作。
* 紀錄存在 `mfp.sqlite` 的 `mfp_scans`（誰、哪一台、哪一種作業、設定、每份文件與每個檔案、結果），
  進度另外放記憶體給畫面輪詢。
* 產出存進**送出者自己**的工作區（`workspace.save_file_for_key`，路徑式、不整份讀進記憶體、驗證檔頭與內容）。
  工作區停用或額度不夠時，檔案留在暫存區 `RESULT_TTL` 秒給本人下載（多個檔案打包成 ZIP）。
* 產出格式（使用者 2026-10-14）：掃描可以存 PDF 或 JPEG（每頁一個檔）；信箱取件預設存 PDF，也可以存設備給的
  原始格式（TIFF / JPEG，不轉檔）。轉 PDF 失敗（例如 TIFF 用了不支援的壓縮）就改存原始檔並註明。
* **先暫存、最後合併**（使用者 2026-11-03）：掃描存成 PDF 時可以帶 `hold`，掃完不存進工作區，
  留在暫存區給畫面預覽；接著掃多次之後，`merge()` 照畫面上的頁序合成一份多頁 PDF，走跟一般掃描一樣的
  存檔流程（存進工作區；存不進去就留在暫存區給本人下載），暫存的那幾次一起清掉。平台玻璃一次一頁時特別需要。
* **信箱的帳密只在這條執行緒的記憶體裡**，作業結束就丟掉；不寫進紀錄、資料庫、協定紀錄。
* 服務重啟時還在排隊或執行中的，第一次用到這個模組時標成「中斷」（隊伍只存在記憶體裡）。
"""
from __future__ import annotations

import logging
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import mfp_box, mfp_escl, mfp_output, mfp_queue, mfp_share, mfp_store

logger = logging.getLogger("app.mfp_fetch")

#: 排隊最多等多久（秒）—— 前面的人都在掃很多頁時不要無限等。
MAX_QUEUE_WAIT = 30 * 60
#: 沒存進工作區的檔案在暫存區留多久（秒）。
RESULT_TTL = 24 * 3600
#: 信箱取件的存檔格式：PDF（預設）或設備給的原始格式。
BOX_OUTPUTS = ("pdf", "original")
#: 先暫存的掃描：每人最多暫存幾次（含排隊中），合併一次最多幾頁。
MAX_HELD = 40
MAX_MERGE_PAGES = 300
#: 預覽圖的寬度（像素）：縮圖、放大看。
PREVIEW_WIDTHS = (240, 1200)

MESSAGES: dict[str, str] = {
    "SCAN_QUEUE_TIMEOUT": "排隊太久，已放棄這次作業",
    "SCAN_INTERRUPTED": "服務重新啟動，這次作業沒有完成",
    "SCAN_FAILED": "作業失敗",
    "SCAN_NOT_FOUND": "找不到這次作業",
    "SCAN_NO_FILE": "這次作業沒有可以下載的檔案（已存進工作區，或已超過保留時間）",
    "WS_DISABLED": "工作區功能未啟用，檔案留在暫存區 24 小時，請下載",
    "WS_FAILED": "有檔案存不進工作區（可能超過大小上限或額度），留在暫存區 24 小時，請下載",
    "HELD": "已暫存、還沒存進工作區：可以接著掃，最後按「存成一份 PDF」（暫存 24 小時）",
    "MERGED": "已合併成一份 PDF 存進你的工作區",
    "MERGED_SOURCE": "已合併到另一份 PDF，暫存檔已清掉",
    "DISCARDED": "已清除暫存的掃描檔",
    "HOLD_TOO_MANY": "暫存的掃描太多了（最多 40 次），請先存成 PDF 或清除",
    "MERGE_EMPTY": "沒有要合併的頁面",
    "MERGE_TOO_MANY": "一次最多合併 300 頁",
    "MERGE_SOURCE_GONE": "有暫存的掃描檔已過期或已清除，請重新整理後再試",
    "MERGE_FAILED": "合併成 PDF 時失敗",
    # 工作區不收的格式（例如檔案分享裡的 DocuWorks .xdw）：原本混在 WS_FAILED 裡，畫面說「可能超過大小上限」，
    # 讓人以為是檔案太大（2026-11-02 文章查證時發現）
    "WS_UNSUPPORTED": "有檔案的格式工作區不收（例如 DocuWorks 的 .xdw），沒有轉成 PDF，原樣留在暫存區 24 小時，請下載",
    "BOX_PARTIAL": "部分文件沒有取到（每份文件的原因列在下面）",
    "BOX_ALL_FAILED": "選的文件都沒有取到",
    "BOX_OUTPUT_INVALID": "存檔格式只能是 PDF 或原始格式",
    "PDF_FALLBACK_ORIGINAL": "轉成 PDF 失敗，改存設備給的原始檔",
    "SOURCE_KEPT": "設備上的原件還在",
    "SOURCE_GONE": "取件後設備上已經沒有這份原件（設備或信箱可能設定成取件後刪除）",
    # 取件並刪除（v1.17.0）
    "SOURCE_DELETED": "已從設備刪除原件（確認檔案存好之後才刪）",
    "DELETE_NOT_DONE": "檔案已存好，但設備上的原件沒有刪掉（技術細節裡有設備回的狀態）",
    "DELETE_SKIPPED_NOT_SAVED": "這份沒有存好，所以沒有刪除設備上的原件",
    "SOURCE_UNKNOWN": "取件後沒能確認設備上的原件還在不在",
}

_LOCK = threading.Lock()
#: job_id → {"phase", "pages", "bytes", "ahead", "cancel": Event, "ticket", ...}
_LIVE: dict[str, dict] = {}
_INIT_DONE = False


def _init_once() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    with _LOCK:
        if _INIT_DONE:
            return
        try:
            mfp_store.mark_interrupted_scans()
        except Exception:  # noqa: BLE001 —— 標記失敗不影響新的作業
            logger.warning("mfp：標記中斷的作業失敗")
        _INIT_DONE = True


def _work_root() -> Path:
    from ..config import settings
    return settings.temp_dir / "mfp-scans"


_NAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_KNOWN_EXT = re.compile(r"\.(pdf|jpe?g|tiff?)$", re.I)


def clean_stem(name: str, fallback: str) -> str:
    """使用者填的檔名（或設備上的文件名稱）：去掉路徑、不能用的字元與副檔名，最多 100 字。"""
    s = _NAME_BAD.sub(" ", (name or "").strip()).strip(" .")
    s = _KNOWN_EXT.sub("", s).rstrip(" .")
    return (s or fallback)[:100]


def clean_file_name(name: str, fallback: str) -> str:
    """掃描檔名（PDF）：`clean_stem` 再補 `.pdf`。"""
    return clean_stem(name, fallback) + ".pdf"


def _new_live(job_id: str, ticket, cancel: threading.Event) -> None:
    with _LOCK:
        _LIVE[job_id] = {"phase": "queued", "pages": 0, "bytes": 0, "files": 0,
                         "ahead": mfp_queue.position(ticket), "cancel": cancel, "ticket": ticket}


#: 作業的發起人（帳號、來源 IP），只給「作業完成」那筆稽核用；放記憶體、寫完稽核就拿掉
#: （作業紀錄表沒有這兩欄，服務重啟時還在跑的作業本來就會被標成中斷）
_WHO: dict[str, dict] = {}


def _remember_who(job_id: str, user_label: str, client_ip: str) -> None:
    with _LOCK:
        _WHO[job_id] = {"user": user_label or "", "ip": client_ip or ""}


def _finish(job_id: str, *, status: str, **fields) -> None:
    """結束作業：先寫作業紀錄，再寫一筆「作業完成」稽核。

    開始時的稽核（`mfp_scan_start` / `mfp_box_fetch` / `mfp_share_fetch`）只記得到「要做什麼」；
    實際取到哪幾份、存進工作區幾個檔、**從設備上刪掉了哪幾份原件**，要等作業做完才知道。"""
    mfp_store.finish_scan(job_id, status=status, **fields)
    try:
        _audit_done(job_id)
    except Exception:  # noqa: BLE001 —— 稽核寫不進去不可以讓作業變成失敗
        logger.warning("mfp 作業完成稽核寫入失敗：%s", job_id[:8])


def _audit_done(job_id: str) -> None:
    from . import audit_db
    with _LOCK:
        who = _WHO.pop(job_id, {})
    rec = mfp_store.get_scan_any(job_id)
    if not rec:
        return
    items = rec.get("items") or []
    details: dict[str, Any] = {
        "scan": job_id, "kind": rec.get("kind") or "scan", "status": rec.get("status"), "conn_id": rec.get("conn_id"),
        "pages": rec.get("pages"), "error_code": rec.get("error_code"),
    }
    if items:
        details["documents"] = [{k: i.get(k) for k in ("doc", "status", "error_code", "pages", "source", "delete")
                                 if i.get(k) not in (None, "")} for i in items][:50]
        details["saved_files"] = sum(len(i.get("files") or []) if isinstance(i.get("files"), list)
                                     else int(i.get("files") or 0) for i in items)
        # 動到設備的操作單獨列出來，查稽核時不用逐份翻
        details["deleted_on_device"] = [i.get("doc") for i in items if i.get("delete") == "DELETED"]
        refused = [i.get("doc") for i in items if i.get("delete") and i.get("delete") not in ("DELETED",)]
        if refused:
            details["delete_not_done"] = refused
    audit_db.log_event("mfp_job_done", username=who.get("user", ""), ip=who.get("ip", ""),
                       target=(rec.get("device") or "")[:200], details=details)


def start(*, owner: str, user_id: Optional[int], user_label: str, conn: dict, caps: dict,
          opts: dict, device: str, ip: str, scheme: str, port: int, client_ip: str = "") -> dict:
    """排進這台的隊伍並開始背景掃描；回傳作業紀錄。

    設定不在能力表裡丟 `mfp_escl.ScanError("SCAN_OPTION_INVALID")`；隊伍滿了丟 `mfp_queue.QueueError`
    —— 兩種都在排隊之前就檢查完，不會留下半套紀錄。"""
    _init_once()
    _xml, eff = mfp_escl.build_settings(caps, opts)          # 先驗設定（不合法就不排隊）
    # 先暫存只用在存成 PDF（JPEG 照舊每頁一個檔直接存）
    hold = opts.get("hold") is True and eff.get("output") != "jpeg"
    if hold:
        if mfp_store.count_held_or_pending(owner) >= MAX_HELD:
            raise mfp_escl.ScanError("HOLD_TOO_MANY")
        eff = dict(eff, hold=True)
    # 走什麼協定也記下來給畫面顯示（使用者 2026-10-14：「掃描是走什麼協定 也要顯示」）
    via = {"protocol": "eSCL", "scheme": scheme, "port": int(port)}
    eff = dict(eff, via=via)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    fallback = f"scan-{stamp}"
    stem = clean_stem(str(opts.get("file_name") or ""), fallback)
    ext = ".jpg" if eff.get("output") == "jpeg" else ".pdf"
    ticket = mfp_queue.enqueue(device, owner, "scan")
    job_id = secrets.token_hex(16)
    try:
        rec = mfp_store.create_scan(job_id, conn_id=conn["id"], owner=owner, owner_user_id=user_id,
                                    device=device, conn_name=conn.get("display_name") or conn.get("host"),
                                    settings=eff, file_name=stem + ext, kind="scan")
    except BaseException:
        mfp_queue.done(ticket)
        raise
    cancel = threading.Event()
    _new_live(job_id, ticket, cancel)
    _remember_who(job_id, user_label, client_ip)
    th = threading.Thread(target=_run_scan, name=f"mfp-scan-{job_id[:8]}", daemon=True, kwargs=dict(
        job_id=job_id, ticket=ticket, cancel=cancel, user_id=user_id, user_label=user_label,
        host=conn["host"], ip=ip, scheme=scheme, port=port, caps=caps, opts=dict(opts, **eff),
        stem=stem, via=via, hold=hold))
    th.start()
    return rec


def start_box(*, owner: str, user_id: Optional[int], user_label: str, conn: dict, device: str, ip: str,
              scheme: str, port: int, box: int, creds: "mfp_box.Creds", doc_ids: list[str], output: str,
              trust_sha256: Optional[str], allow_http: bool, confirm_delete_after_get: bool,
              delete_after: bool = False, client_ip: str = "") -> dict:
    """排進這台的隊伍並開始背景取件（機內信箱，推測實作）；回傳作業紀錄。

    輸入不對丟 `mfp_box.BoxError`、隊伍滿了丟 `mfp_queue.QueueError` —— 都在排隊之前。"""
    _init_once()
    if output not in BOX_OUTPUTS:
        raise mfp_box.BoxError("BOX_OUTPUT_INVALID")
    ids = [str(d) for d in doc_ids]
    if not ids:
        raise mfp_box.BoxError("NO_DOCUMENTS_SELECTED")
    if len(ids) > mfp_box.MAX_DOCS_PER_JOB:
        raise mfp_box.BoxError("TOO_MANY_DOCUMENTS")
    if any(not re.fullmatch(r"\d{1,9}", d) for d in ids) or len(set(ids)) != len(ids):
        raise mfp_box.BoxError("NO_DOCUMENTS_SELECTED")
    via = {"protocol": "WebDAV", "scheme": scheme, "port": int(port)}
    settings = {"via": via, "box": box, "output": output, "documents": ids, "delete_after": bool(delete_after)}
    ticket = mfp_queue.enqueue(device, owner, "box")
    job_id = secrets.token_hex(16)
    try:
        rec = mfp_store.create_scan(job_id, conn_id=conn["id"], owner=owner, owner_user_id=user_id,
                                    device=device, conn_name=conn.get("display_name") or conn.get("host"),
                                    settings=settings, file_name="", kind="box")
    except BaseException:
        mfp_queue.done(ticket)
        raise
    cancel = threading.Event()
    _new_live(job_id, ticket, cancel)
    _remember_who(job_id, user_label, client_ip)
    th = threading.Thread(target=_run_box, name=f"mfp-box-{job_id[:8]}", daemon=True, kwargs=dict(
        job_id=job_id, ticket=ticket, cancel=cancel, user_id=user_id, user_label=user_label,
        host=conn["host"], ip=ip, scheme=scheme, port=port, box=box, creds=creds, doc_ids=ids,
        output=output, trust=trust_sha256, allow_http=allow_http, confirm=confirm_delete_after_get,
        delete_after=bool(delete_after)))
    th.start()
    return rec


def _set(job_id: str, **kw) -> None:
    with _LOCK:
        live = _LIVE.get(job_id)
        if live is not None:
            live.update(kw)


def _wait(job_id: str, ticket, cancel: threading.Event) -> bool:
    """排隊；輪到回 True。取消或等太久就寫好紀錄、回 False。"""
    ok = mfp_queue.wait_turn(ticket, timeout=MAX_QUEUE_WAIT, on_wait=lambda ahead: _set(job_id, ahead=ahead))
    if not ok:
        code = "SCAN_CANCELLED" if cancel.is_set() else "SCAN_QUEUE_TIMEOUT"
        _finish(job_id, status="cancelled" if cancel.is_set() else "error", error_code=code)
        return False
    _set(job_id, phase="checking", ahead=0)
    mfp_store.update_scan(job_id, status="running", started_at=time.time())
    return True


def _finish_thread(job_id: str, ticket, workdir: Path) -> None:
    mfp_queue.done(ticket)
    with _LOCK:
        _LIVE.pop(job_id, None)
    rec = mfp_store.get_scan_any(job_id)
    if not rec or rec.get("status") != "done" or not rec.get("result_path"):
        shutil.rmtree(workdir, ignore_errors=True)


def _run_scan(*, job_id: str, ticket, cancel: threading.Event, user_id: Optional[int], user_label: str,
              host: str, ip: str, scheme: str, port: int, caps: dict, opts: dict, stem: str, via: dict,
              hold: bool = False) -> None:
    workdir = _work_root() / job_id
    try:
        if not _wait(job_id, ticket, cancel):
            return
        try:
            res = mfp_escl.scan(host, ip, scheme, port, caps, opts, workdir,
                                on_progress=lambda d: _set(job_id, **d), is_cancelled=cancel.is_set)
        except mfp_escl.ScanError as e:
            _finish(job_id, status="cancelled" if e.code == "SCAN_CANCELLED" else "error",
                                  error_code=e.code, error_detail=e.detail)
            return
        _set(job_id, phase="saving", pages=res["pages"], bytes=res["bytes"])
        outs = res.get("files") or [{"path": res["path"], "kind": "pdf", "pages": res["pages"]}]
        multi = len(outs) > 1
        files = [{"path": o["path"], "kind": o["kind"], "pages": o["pages"],
                  "name": stem + (f"-p{i:02d}" if multi else "") + mfp_output.EXT[o["kind"]]}
                 for i, o in enumerate(outs, 1)]
        if hold:
            # 先暫存：不進工作區，檔案留在這件作業的目錄給預覽與之後的合併
            saved = [{"name": f["name"], "kind": f["kind"], "pages": f["pages"],
                      "bytes": Path(f["path"]).stat().st_size, "kept": True,
                      "stored": str(Path(f["path"]).relative_to(workdir))} for f in files]
            note = mfp_store.HELD_NOTE
        else:
            saved, note = _save_files(files, user_id, user_label, job_id, {})
        items = [{"files": saved}]
        first = next((f for f in saved if f.get("workspace_file_id")), None)
        kept = next((f for f in saved if f.get("kept")), None)
        _finish(job_id, status="done", pages=res["pages"], bytes=res["bytes"],
                              workspace_file_id=first["workspace_file_id"] if first else None,
                              workspace_note=note, items=items,
                              result_path=str(workdir / kept["stored"]) if kept else None,
                              settings=dict(res["settings"], via=via), tls_verified=res.get("tls_verified"))
    except Exception as e:  # noqa: BLE001 —— 背景執行緒：任何意外都要落成「失敗」，不可以停在執行中
        logger.exception("mfp 掃描 %s 失敗", job_id[:8])
        try:
            _finish(job_id, status="error", error_code="SCAN_FAILED",
                                  error_detail={"error": e.__class__.__name__})
        except Exception:  # noqa: BLE001
            pass
    finally:
        _finish_thread(job_id, ticket, workdir)


def _run_box(*, job_id: str, ticket, cancel: threading.Event, user_id: Optional[int], user_label: str,
             host: str, ip: str, scheme: str, port: int, box: int, creds: "mfp_box.Creds",
             doc_ids: list[str], output: str, trust: Optional[str], allow_http: bool, confirm: bool,
             delete_after: bool = False) -> None:
    workdir = _work_root() / job_id
    trace: list = []
    try:
        if not _wait(job_id, ticket, cancel):
            return
        try:
            sess = mfp_box.open_box(host, ip, scheme, port, box=box, creds=creds, trust_sha256=trust,
                                    allow_http=allow_http, trace=trace)
        except mfp_box.BoxError as e:
            _finish(job_id, status="error", error_code=e.code,
                                  error_detail=_detail(e.detail, trace))
            return
        finally:
            creds = None            # noqa: F841 —— 帳密只用在開信箱（認證狀態留在 sess 裡）
        tls = sess.tls()
        box_dag = mfp_box._truthy(sess.box_props.get("deleteAfterGet"))
        items: list[dict] = []
        total = 0
        stop: Optional[mfp_box.BoxError] = None
        for n, doc_id in enumerate(doc_ids, 1):
            doc = sess.docs.get(doc_id)
            name = ((doc or {}).get("props") or {}).get("displayname") or ""
            item: dict[str, Any] = {"doc": doc_id, "name": name[:120], "files": []}
            items.append(item)
            if stop is not None:
                item.update(status="skipped", error_code=stop.code)
                continue
            if cancel.is_set():
                stop = mfp_box.BoxError("CANCELLED")
                item.update(status="skipped", error_code="CANCELLED")
                continue
            if doc is None:
                item.update(status="error", error_code="DOC_NOT_FOUND")
                continue
            dag = mfp_box._truthy(doc["props"].get("deleteAfterGet"))
            if (dag or box_dag) and not confirm:
                item.update(status="error", error_code="DELETE_AFTER_GET_UNCONFIRMED")
                continue
            _set(job_id, phase="downloading", doc=n, docs=len(doc_ids))
            try:
                got = mfp_box.download(sess, doc_id, workdir / f"d{n:02d}",
                                       budget_bytes=mfp_box.MAX_TOTAL_BYTES - total,
                                       is_cancelled=cancel.is_set,
                                       on_progress=lambda d: _set(job_id, files=d.get("files", 0)))
            except mfp_box.BoxError as e:
                item.update(status="error", error_code=e.code)
                if e.code in mfp_box.FATAL:
                    stop = e
                continue
            total += sum(f["bytes"] for f in got)
            stem = clean_stem(name, f"box{box}-{doc_id}")
            files, fmt_note = _box_outputs(got, workdir / f"d{n:02d}", stem, output, mfp_box.doc_resolution(doc))
            item.update(status="ok", pages=sum(f.get("pages") or 0 for f in files),
                        sources=[{"name": f["source"], "bytes": f["bytes"], "sha256": f["sha256"],
                                  "kind": f["kind"]} for f in got][:200])
            if fmt_note:
                item["format_note"] = fmt_note
            item["files"] = files
        # 取完再列一次：原件還在不在（設備或信箱可能設定成取件後刪除）
        if any(i.get("status") == "ok" for i in items) and stop is None:
            try:
                sess.list()
                for i in items:
                    if i.get("status") == "ok":
                        i["source"] = "SOURCE_KEPT" if i["doc"] in sess.docs else "SOURCE_GONE"
            except mfp_box.BoxError:
                for i in items:
                    if i.get("status") == "ok":
                        i["source"] = "SOURCE_UNKNOWN"
        _set(job_id, phase="saving")
        all_files = [f for i in items for f in i["files"]]
        saved, note = _save_files(all_files, user_id, user_label, job_id, {"mfp_box": box})
        # 存好的結果（工作區 id、有沒有留在暫存區）放回每份文件底下，順序跟 all_files 一樣
        it = iter(saved)
        for i in items:
            i["files"] = [next(it) for _f in i["files"]]
        if delete_after and stop is None and not cancel.is_set():
            _delete_saved(sess, items, workdir, job_id)
        ok = [i for i in items if i.get("status") == "ok"]
        first = next((f for f in saved if f.get("workspace_file_id")), None)
        kept = next((f for f in saved if f.get("kept")), None)
        fields: dict[str, Any] = dict(
            pages=sum(i.get("pages") or 0 for i in ok), bytes=sum(f.get("bytes") or 0 for f in all_files),
            workspace_file_id=first["workspace_file_id"] if first else None, workspace_note=note,
            items=_slim_items(items), file_name=_job_title(items),
            result_path=str(workdir / kept["stored"]) if kept else None,
            tls_verified=(tls or {}).get("verified") if scheme == "https" else None,
            settings={"via": dict(sess.via(), tls=tls), "box": box, "output": output, "documents": doc_ids,
                      "delete_after": bool(delete_after)})
        if ok:
            fields["error_code"] = "BOX_PARTIAL" if len(ok) < len(items) else None
            fields["error_detail"] = _detail({}, trace) if len(ok) < len(items) else None
            _finish(job_id, status="done", **fields)
        else:
            code = stop.code if stop is not None else "BOX_ALL_FAILED"
            _finish(job_id, status="cancelled" if code == "CANCELLED" else "error",
                                  error_code=code, error_detail=_detail({}, trace), **fields)
    except Exception as e:  # noqa: BLE001
        logger.exception("mfp 信箱取件 %s 失敗", job_id[:8])
        try:
            _finish(job_id, status="error", error_code="SCAN_FAILED",
                                  error_detail=_detail({"error": e.__class__.__name__}, trace))
        except Exception:  # noqa: BLE001
            pass
    finally:
        _finish_thread(job_id, ticket, workdir)


def start_share(*, owner: str, user_id: Optional[int], user_label: str, conn: dict, device: str, ip: str,
                route: str, scheme: Optional[str], port: Optional[int], root: str, files: list,
                output: str, creds: dict, trust_sha256: Optional[str], allow_http: bool,
                allow_plain: bool, client_ip: str = "") -> dict:
    """排進這台的隊伍並開始背景取件（檔案分享，實驗功能）；回傳作業紀錄。輸入不對丟 `mfp_share.ShareError`。"""
    _init_once()
    if output not in BOX_OUTPUTS:
        raise mfp_box.BoxError("BOX_OUTPUT_INVALID")
    if route not in mfp_share.ROUTES:
        raise mfp_share.ShareError("SHARE_ROUTE_INVALID")
    if not isinstance(files, list) or not files:
        raise mfp_share.ShareError("SHARE_NO_FILES_SELECTED")
    if len(files) > mfp_share.MAX_FILES_PER_JOB:
        raise mfp_share.ShareError("SHARE_TOO_MANY_FILES")
    paths = [mfp_share.check_path(f) for f in files]
    if any(not p for p in paths) or len({tuple(p) for p in paths}) != len(paths):
        raise mfp_share.ShareError("SHARE_PATH_INVALID")
    if mfp_share.ROUTES[route]["auth"] != "box" and not mfp_share.valid_segment(root):
        raise mfp_share.ShareError("SHARE_ROOT_INVALID")
    if mfp_share.ROUTES[route]["transport"] == "soap" and mfp_share.ROUTES[route].get("target") == "fixed":
        from . import mfp_soap
        scheme = mfp_share.ROUTES[route]["scheme"]
        port = mfp_soap.kyocera_target(scheme)
    via = {"protocol": mfp_share.protocol(route), "scheme": scheme or "smb", "port": int(port or mfp_share.SMB_PORT)}
    settings = {"via": via, "route": route, "root": root, "output": output,
                "files": ["/".join(p) for p in paths][:mfp_share.MAX_FILES_PER_JOB]}
    ticket = mfp_queue.enqueue(device, owner, "share")
    job_id = secrets.token_hex(16)
    try:
        rec = mfp_store.create_scan(job_id, conn_id=conn["id"], owner=owner, owner_user_id=user_id,
                                    device=device, conn_name=conn.get("display_name") or conn.get("host"),
                                    settings=settings, file_name="", kind="share")
    except BaseException:
        mfp_queue.done(ticket)
        raise
    cancel = threading.Event()
    _new_live(job_id, ticket, cancel)
    _remember_who(job_id, user_label, client_ip)
    th = threading.Thread(target=_run_share, name=f"mfp-share-{job_id[:8]}", daemon=True, kwargs=dict(
        job_id=job_id, ticket=ticket, cancel=cancel, user_id=user_id, user_label=user_label,
        host=conn["host"], ip=ip, route=route, scheme=scheme, port=port, root=root, paths=paths,
        output=output, creds=dict(creds), trust=trust_sha256, allow_http=allow_http, allow_plain=allow_plain))
    th.start()
    return rec


_EXT_OK = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def _share_outputs(path: Path, src_name: str, output: str, workdir: Path) -> tuple[list[dict], Optional[str]]:
    """一個取回的檔案 → 要存的檔案。PDF（預設）：TIFF / JPEG 轉 PDF、PDF 照舊、其他格式原樣；原始格式：一律原樣。"""
    kind = mfp_output.sniff(path) or "other"
    stem = clean_stem(Path(src_name).stem if "." in src_name else src_name, "file")
    ext = Path(src_name).suffix.lower()
    if not _EXT_OK.match(ext or "."):
        ext = ""
    if kind in mfp_output.EXT:
        try:
            info = mfp_output.inspect(path, kind)
        except mfp_output.OutputError:
            raise mfp_share.ShareError("DOC_INVALID", {"kind": kind}) from None
        pages = info["pages"]
    else:
        pages = None
    if output == "pdf" and kind in ("tiff", "jpeg"):
        out = workdir / "document.pdf"
        try:
            n = mfp_output.to_pdf([(path, kind)], out, info.get("dpi") or 0)
            path.unlink(missing_ok=True)
            return [{"path": out, "kind": "pdf", "pages": n, "name": stem + ".pdf"}], None
        except mfp_output.OutputError:
            out.unlink(missing_ok=True)
            note = "PDF_FALLBACK_ORIGINAL"
    else:
        note = None
    name_ext = mfp_output.EXT.get(kind) or ext or ""
    return [{"path": path, "kind": kind, "pages": pages, "name": stem + name_ext}], note


def _share_parts_outputs(parts: list[Path], src_name: str, output: str,
                         workdir: Path) -> tuple[list[dict], Optional[str]]:
    """一份文件分成好幾個部分取回（KYOCERA 多次回應、RICOH 多個內容元素）→ 要存的檔案。

    每個部分都要是認得的格式（PDF / JPEG / TIFF）：不認得的不猜怎麼接，整份算失敗。PDF 照頁序合成一份；
    原始格式各存一個檔、加頁序編號（跟信箱取件同一套 `_box_outputs`）。"""
    got = []
    for part in parts:
        kind = mfp_output.sniff(part)
        if kind not in mfp_output.EXT:
            raise mfp_share.ShareError("DOC_INVALID", {"reason": "unknown part format", "parts": len(parts)})
        try:
            info = mfp_output.inspect(part, kind)
        except mfp_output.OutputError:
            raise mfp_share.ShareError("DOC_INVALID", {"kind": kind}) from None
        got.append({"path": part, "kind": kind, "pages": info["pages"]})
    stem = clean_stem(Path(src_name).stem if "." in src_name else src_name, "file")
    return _box_outputs(got, workdir, stem, output, 0)


def _run_share(*, job_id: str, ticket, cancel: threading.Event, user_id: Optional[int], user_label: str,
               host: str, ip: str, route: str, scheme: Optional[str], port: Optional[int], root: str,
               paths: list[list[str]], output: str, creds: dict, trust: Optional[str], allow_http: bool,
               allow_plain: bool) -> None:
    workdir = _work_root() / job_id
    trace: list = []
    sess = None
    try:
        if not _wait(job_id, ticket, cancel):
            return
        try:
            sess = mfp_share.open_session(route, host=host, ip=ip, scheme=scheme, port=port,
                                          user=creds.get("user", ""), password=creds.get("password", ""),
                                          box=creds.get("box"), box_password=creds.get("box_password", ""),
                                          trust_sha256=trust, allow_http=allow_http, allow_plain=allow_plain,
                                          trace=trace, auth_mode=creds.get("auth_mode") or "auto",
                                          user_code=creds.get("user_code", ""),
                                          folder_password=creds.get("folder_password", ""))
            if mfp_share.ROUTES[route]["auth"] == "box":
                root = mfp_share.konica_box_share(sess, creds.get("box") or "")
        except mfp_box.BoxError as e:
            _finish(job_id, status="error", error_code=e.code, error_detail=_detail(e.detail, trace))
            return
        finally:
            creds = {}              # noqa: F841 —— 帳密只用在開連線
        items: list[dict] = []
        total = 0
        stop: Optional[mfp_box.BoxError] = None
        for n, segs in enumerate(paths, 1):
            item: dict[str, Any] = {"doc": "/".join(segs)[:300], "name": segs[-1][:120], "files": []}
            items.append(item)
            if stop is not None or cancel.is_set():
                item.update(status="skipped", error_code=stop.code if stop else "CANCELLED")
                stop = stop or mfp_box.BoxError("CANCELLED")
                continue
            _set(job_id, phase="downloading", doc=n, docs=len(paths))
            d = workdir / f"f{n:03d}"
            d.mkdir(parents=True, exist_ok=True)
            dest = d / "source.bin"
            try:
                left = min(mfp_share.MAX_FILE_BYTES, mfp_share.MAX_TOTAL_BYTES - total)
                if left <= 0:
                    raise mfp_share.ShareError("SHARE_FILE_TOO_LARGE")
                got = sess.download(root, segs, dest, left)
                total += got["bytes"]
                name = mfp_share.display_name(sess, root, segs)
                item.update(doc="/".join(segs[:-1] + [name])[:300], name=name[:120])
                # 設備上的文件名稱可以有斜線（KYOCERA / RICOH）：先換掉，不然取檔名時只剩最後一段
                fname = re.sub(r"\s*[\\/]+\s*", " ", name).strip() or "file"
                if got.get("parts"):
                    files, note = _share_parts_outputs(got["parts"], fname, output, d)
                else:
                    files, note = _share_outputs(dest, fname, output, d)
            except mfp_box.BoxError as e:
                dest.unlink(missing_ok=True)
                item.update(status="error", error_code=e.code)
                if e.code in mfp_share.FATAL:
                    stop = e
                continue
            item.update(status="ok", pages=sum(f.get("pages") or 0 for f in files),
                        sources=[{"name": item["name"], "bytes": got["bytes"], "sha256": got["sha256"]}])
            if note:
                item["format_note"] = note
            item["files"] = files
        # 取完再看一次：來源還在不在（檔案分享不會因為讀取而刪除，但設備或其他人可能已經移走）
        ok_items = [(i, s) for i, s in zip(items, paths) if i.get("status") == "ok"]
        if ok_items and stop is None:
            for i, segs in ok_items:
                try:
                    names = {e["name"] for e in sess.list(root, segs[:-1]) if not e["dir"]}
                    i["source"] = "SOURCE_KEPT" if segs[-1] in names else "SOURCE_GONE"
                except mfp_box.BoxError:
                    i["source"] = "SOURCE_UNKNOWN"
        _set(job_id, phase="saving")
        all_files = [f for i in items for f in i["files"]]
        saved, wnote = _save_files(all_files, user_id, user_label, job_id, {"mfp_route": route})
        it = iter(saved)
        for i in items:
            i["files"] = [next(it) for _f in i["files"]]
        ok = [i for i in items if i.get("status") == "ok"]
        first = next((f for f in saved if f.get("workspace_file_id")), None)
        kept = next((f for f in saved if f.get("kept")), None)
        tls = sess.tls() if sess else None
        fields: dict[str, Any] = dict(
            pages=sum(i.get("pages") or 0 for i in ok), bytes=sum(f.get("bytes") or 0 for f in all_files),
            workspace_file_id=first["workspace_file_id"] if first else None, workspace_note=wnote,
            items=_slim_items(items), file_name=_job_title(items),
            result_path=str(workdir / kept["stored"]) if kept else None,
            tls_verified=(tls or {}).get("verified") if (scheme == "https") else None,
            settings={"via": dict(sess.via(), tls=tls), "route": route, "root": root, "output": output,
                      "files": ["/".join(p) for p in paths]})
        if ok:
            fields["error_code"] = "BOX_PARTIAL" if len(ok) < len(items) else None
            fields["error_detail"] = _detail({}, trace) if len(ok) < len(items) else None
            _finish(job_id, status="done", **fields)
        else:
            code = stop.code if stop is not None else "BOX_ALL_FAILED"
            _finish(job_id, status="cancelled" if code == "CANCELLED" else "error",
                                  error_code=code, error_detail=_detail({}, trace), **fields)
    except Exception as e:  # noqa: BLE001
        logger.exception("mfp 檔案分享取件 %s 失敗", job_id[:8])
        try:
            _finish(job_id, status="error", error_code="SCAN_FAILED",
                                  error_detail=_detail({"error": e.__class__.__name__}, trace))
        except Exception:  # noqa: BLE001
            pass
    finally:
        if sess is not None:
            sess.close()
        _finish_thread(job_id, ticket, workdir)


def _delete_saved(sess, items: list[dict], workdir: Path, job_id: str) -> None:
    """「取件並刪除」：只刪**檔案都存好**的那幾份（存進工作區，或留在暫存區給本人下載），刪完再列一次信箱確認。

    存好的判準：這份的每個檔案都有工作區 id 或暫存區檔名、而且暫存的那個檔案真的在。任何一個沒存好就不刪。"""
    _set(job_id, phase="deleting")
    asked = []
    for i in items:
        if i.get("status") != "ok":
            continue
        files = i.get("files") or []
        saved_ok = bool(files) and all(
            f.get("workspace_file_id") or (f.get("kept") and f.get("stored") and (workdir / f["stored"]).is_file())
            for f in files)
        if not saved_ok:
            i["delete"] = "DELETE_SKIPPED_NOT_SAVED"
            continue
        try:
            res = mfp_box.delete_doc(sess, i["doc"])
        except mfp_box.BoxError as e:
            res = {"code": e.code, "status": None}
        i["delete"] = res["code"]
        if res.get("status") is not None:
            i["delete_status"] = res["status"]
        asked.append(i)
    if not asked:
        return
    try:
        sess.list()
    except mfp_box.BoxError:
        for i in asked:
            i["source"] = "SOURCE_UNKNOWN"
        return
    for i in asked:
        gone = i["doc"] not in sess.docs
        i["source"] = "SOURCE_DELETED" if gone else "DELETE_NOT_DONE"


def _detail(detail: dict, trace: list) -> dict:
    """錯誤細節＋協定紀錄（已不含帳密：請求層只記方法、路徑、狀態碼、認證方案與 realm）。"""
    out = dict(detail or {})
    if trace:
        out["trace"] = trace[-mfp_box.MAX_TRACE:]
    return out


def _slim_items(items: list[dict]) -> list[dict]:
    """存進資料庫的版本：檔案只留名稱、大小、頁數、工作區 id、暫存檔名（不留本機路徑）。"""
    out = []
    for i in items:
        d = {k: v for k, v in i.items() if k != "files"}
        d["files"] = [{k: f.get(k) for k in ("name", "kind", "bytes", "pages", "workspace_file_id", "kept",
                                             "stored")} for f in i.get("files") or []]
        out.append(d)
    return out


def _job_title(items: list[dict]) -> str:
    names = [i.get("name") or f"#{i['doc']}" for i in items]
    return "、".join(names[:3])[:200] + ("…" if len(names) > 3 else "")


def _box_outputs(got: list[dict], workdir: Path, stem: str, output: str,
                 resolution: int) -> tuple[list[dict], Optional[str]]:
    """一份文件取回的檔案 → 要存的檔案。PDF（預設）合成一份；原始格式照設備給的，多個檔加頁序編號。"""
    if output == "pdf":
        out = workdir / "document.pdf"
        try:
            pages = mfp_output.to_pdf([(Path(f["path"]), f["kind"]) for f in got], out, resolution)
            for f in got:
                if Path(f["path"]) != out:
                    Path(f["path"]).unlink(missing_ok=True)
            return [{"path": out, "kind": "pdf", "pages": pages, "name": stem + ".pdf"}], None
        except mfp_output.OutputError:
            out.unlink(missing_ok=True)
            note = "PDF_FALLBACK_ORIGINAL"
    else:
        note = None
    multi = len(got) > 1
    return [{"path": Path(f["path"]), "kind": f["kind"], "pages": f.get("pages"),
             "name": stem + (f"-p{i:03d}" if multi else "") + mfp_output.EXT[f["kind"]]}
            for i, f in enumerate(got, 1)], note


def _save_files(files: list[dict], user_id: Optional[int], user_label: str, job_id: str,
                origin: dict) -> tuple[list[dict], Optional[str]]:
    """每個檔案存進工作區；存不進去的留在暫存區（`kept`）。回傳（檔案清單、沒存進去的原因代碼）。"""
    from . import workspace as ws
    note: Optional[str] = None
    out: list[dict] = []
    key = None
    for f in files:
        p = Path(f["path"])
        rec = {"name": f["name"], "kind": f["kind"], "pages": f.get("pages"),
               "bytes": p.stat().st_size if p.exists() else 0, "path": p}
        if note != "WS_DISABLED":
            try:
                key = key or ws.key_for_user_id(user_id)
                # 認得的格式（PDF / JPEG / TIFF）要對得上；其他格式（檔案分享裡的辦公文件等）交給工作區自己認
                expect = {mfp_output.EXT[f["kind"]]} if f["kind"] in mfp_output.EXT else None
                meta = ws.save_file_for_key(key, p, f["name"], source_tool="mfp-fetch", user_label=user_label,
                                            verify=True, expect=expect, origin=dict(origin, mfp_scan_id=job_id))
                rec["workspace_file_id"] = str(meta.get("file_id"))
                p.unlink(missing_ok=True)
            except ws.WorkspaceDisabled:
                note = "WS_DISABLED"
            except ws.UnsupportedType as e:
                logger.info("mfp 作業 %s 有檔案的格式工作區不收：%s", job_id[:8], e)
                note = note or "WS_UNSUPPORTED"
            except ws.WorkspaceError as e:
                logger.info("mfp 作業 %s 有檔案存不進工作區：%s", job_id[:8], e)
                note = note or "WS_FAILED"
        if not rec.get("workspace_file_id"):
            rec["kept"] = True
            rec["stored"] = str(p.relative_to(_work_root() / job_id))
        out.append(rec)
    for r in out:
        r.pop("path", None)
    return out, note


# --------------------------------------------------------------------------- 先暫存、最後合併

def _held_pages(owner: str, job_id: str) -> Optional[list[tuple[Path, str, int]]]:
    """暫存掃描的每一頁：`[(檔案, kind, 檔案裡的第幾頁)]`；不是本人的暫存掃描或已過期回 None。"""
    rec = mfp_store.get_scan(owner, job_id)
    if (not rec or (rec.get("kind") or "scan") != "scan" or rec.get("status") != "done"
            or rec.get("workspace_note") != mfp_store.HELD_NOTE):
        return None
    files = result_files(owner, job_id)
    if not files:
        return None
    out: list[tuple[Path, str, int]] = []
    for path, _name in files:
        kind = mfp_output.sniff(path)
        if kind == "pdf":
            n = mfp_output.inspect(path, "pdf")["pages"]
            out += [(path, "pdf", i) for i in range(n)]
        elif kind == "jpeg":
            out.append((path, "jpeg", 0))
    return out or None


def list_held(owner: str, conn_id: Optional[int] = None) -> list[dict]:
    """畫面重新整理後把暫存的掃描接回來：還在保留期、檔案還在的。"""
    out = []
    for r in mfp_store.list_held(owner, conn_id):
        if result_files(owner, r["id"]):
            out.append(public(r))
    return out


def preview(owner: str, job_id: str, page: int, width: int) -> Optional[Path]:
    """暫存掃描第 `page` 頁（從 0 起算）的預覽 PNG（快取在作業目錄裡）。不是本人的、沒有這頁回 None。"""
    if width not in PREVIEW_WIDTHS:
        return None
    pages = _held_pages(owner, job_id)
    if not pages or not (0 <= page < len(pages)):
        return None
    path, kind, idx = pages[page]
    out = path.parent / f"preview-{page:04d}-{width}.png"
    if out.is_file():
        return out
    tmp = out.with_suffix(".part")
    if kind == "pdf":
        import fitz
        with fitz.open(str(path), filetype="pdf") as d:
            pg = d[idx]
            zoom = width / max(1.0, pg.rect.width)
            pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).save(str(tmp), output="png")
    else:
        from PIL import Image
        with Image.open(path) as im:
            im.draft("RGB", (width, width * 2))
            im = im.convert("RGB")
            im.thumbnail((width, width * 4))
            im.save(tmp, "PNG")
    tmp.replace(out)
    return out


def _release(owner: str, job_ids: list[str], note: str) -> int:
    """暫存的掃描清掉檔案、紀錄標成合併或清除（之後不會再出現在暫存區，也沒有檔案可下載）。"""
    n = 0
    for jid in dict.fromkeys(job_ids):
        rec = mfp_store.get_scan(owner, jid)
        if not rec or rec.get("workspace_note") != mfp_store.HELD_NOTE:
            continue
        shutil.rmtree(_work_root() / jid, ignore_errors=True)
        items = [dict(i, files=[{k: v for k, v in f.items() if k not in ("kept", "stored")}
                                for f in i.get("files") or []]) for i in rec.get("items") or []]
        mfp_store.update_scan(jid, workspace_note=note, result_path=None, items=items)
        n += 1
    return n


def discard(owner: str, job_ids: list[str]) -> int:
    """清除暫存的掃描（畫面的「全部清除」）。回傳清了幾次。"""
    return _release(owner, [str(j) for j in job_ids if re.fullmatch(r"[0-9a-f]{32}", str(j))], "DISCARDED")


class MergeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.message = MESSAGES.get(code, code)


def merge(*, owner: str, user_id: Optional[int], user_label: str, pages: list, file_name: str,
          release: list[str]) -> dict:
    """暫存的掃描照 `pages`（`[[作業 id, 第幾頁], …]`，畫面上的順序）合成一份 PDF，存進工作區
    （存不進去就留在暫存區給本人下載）。成功後 `release` 裡的暫存掃描一起清掉。回傳新的作業紀錄。"""
    _init_once()
    if not isinstance(pages, list) or not pages:
        raise MergeError("MERGE_EMPTY")
    if len(pages) > MAX_MERGE_PAGES:
        raise MergeError("MERGE_TOO_MANY")
    cache: dict[str, list[tuple[Path, str, int]]] = {}
    picks: list[tuple[Path, str, int, int]] = []
    for item in pages:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise MergeError("MERGE_EMPTY")
        jid, n = str(item[0]), item[1]
        if not re.fullmatch(r"[0-9a-f]{32}", jid) or not isinstance(n, int) or isinstance(n, bool):
            raise MergeError("MERGE_SOURCE_GONE")
        if jid not in cache:
            got = _held_pages(owner, jid)
            if not got:
                raise MergeError("MERGE_SOURCE_GONE")
            cache[jid] = got
        if not 0 <= n < len(cache[jid]):
            raise MergeError("MERGE_SOURCE_GONE")
        path, kind, idx = cache[jid][n]
        res = int(((mfp_store.get_scan(owner, jid) or {}).get("settings") or {}).get("resolution") or 0)
        picks.append((path, kind, idx, res))
    first = mfp_store.get_scan(owner, str(pages[0][0])) or {}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = clean_stem(file_name or "", f"scan-{stamp}")
    job_id = secrets.token_hex(16)
    sources = list(dict.fromkeys(str(x[0]) for x in pages))
    settings = dict(first.get("settings") or {}, hold=False, merged_from=len(sources))
    mfp_store.create_scan(job_id, conn_id=int(first.get("conn_id") or 0), owner=owner, owner_user_id=user_id,
                          device=first.get("device") or "", conn_name=first.get("conn_name") or "",
                          settings=settings, file_name=stem + ".pdf", kind="scan")
    workdir = _work_root() / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "merged.pdf"
    try:
        import fitz
        doc = fitz.open()
        opened: dict[Path, Any] = {}
        try:
            for path, kind, idx, res in picks:
                if kind == "pdf":
                    if path not in opened:
                        opened[path] = fitz.open(str(path), filetype="pdf")
                    doc.insert_pdf(opened[path], from_page=idx, to_page=idx)
                else:
                    one = workdir / f"jpeg-{len(doc):04d}.pdf"
                    mfp_output.to_pdf([(path, "jpeg")], one, res)
                    with fitz.open(str(one), filetype="pdf") as src:
                        doc.insert_pdf(src)
                    one.unlink(missing_ok=True)
            doc.save(str(out), garbage=3, deflate=True)
            n_pages = doc.page_count
        finally:
            for d in opened.values():
                d.close()
            doc.close()
    except Exception as e:  # noqa: BLE001 —— PyMuPDF / Pillow 的各種例外
        logger.info("mfp 合併 %s 失敗：%s", job_id[:8], e.__class__.__name__)
        shutil.rmtree(workdir, ignore_errors=True)
        mfp_store.finish_scan(job_id, status="error", error_code="MERGE_FAILED",
                              error_detail={"error": e.__class__.__name__})
        raise MergeError("MERGE_FAILED") from None
    size = out.stat().st_size
    files = [{"path": out, "kind": "pdf", "pages": n_pages, "name": stem + ".pdf"}]
    saved, note = _save_files(files, user_id, user_label, job_id, {"merged_from": len(sources)})
    kept = next((f for f in saved if f.get("kept")), None)
    first_ws = next((f for f in saved if f.get("workspace_file_id")), None)
    mfp_store.finish_scan(job_id, status="done", pages=n_pages, bytes=size,
                          workspace_file_id=first_ws["workspace_file_id"] if first_ws else None,
                          workspace_note=note or "MERGED", items=[{"files": saved}],
                          result_path=str(workdir / kept["stored"]) if kept else None)
    if not kept:
        shutil.rmtree(workdir, ignore_errors=True)
    _release(owner, list(release or []) + sources, "MERGED_SOURCE")
    return mfp_store.get_scan(owner, job_id) or {}


def public(rec: dict) -> dict:
    """給畫面與 API 的作業狀態（含排隊位置與即時進度）。"""
    out = {k: rec.get(k) for k in ("id", "conn_id", "conn_name", "status", "settings", "pages", "bytes",
                                   "file_name", "workspace_file_id", "workspace_note", "error_code",
                                   "error_detail", "created_at", "started_at", "finished_at", "tls_verified")}
    out["kind"] = rec.get("kind") or "scan"
    fresh = (time.time() - (rec.get("finished_at") or 0)) < RESULT_TTL
    items = rec.get("items") or []
    files = []
    for n, i in enumerate(items):
        for f in i.get("files") or []:
            # `item`：這個檔案是第幾份文件取出來的（畫面把那份文件的狀況寫在同一列，不另外再列一次）
            files.append({"name": f.get("name"), "kind": f.get("kind"), "bytes": f.get("bytes"),
                          "pages": f.get("pages"), "workspace_file_id": f.get("workspace_file_id"),
                          "has_file": bool(f.get("kept")) and fresh, "item": n})
    out["files"] = files
    out["items"] = [{k: v for k, v in i.items() if k not in ("files", "sources")}
                    | {"files": len(i.get("files") or [])} for i in items]
    for i in out["items"]:
        code = i.get("error_code")
        if code:
            i["error_message"] = mfp_share.MESSAGES.get(code) or mfp_box.MESSAGES.get(code) or MESSAGES.get(code) or code
        if i.get("format_note"):
            i["format_message"] = MESSAGES.get(i["format_note"])
        if i.get("source"):
            i["source_message"] = MESSAGES.get(i["source"])
        if i.get("delete") == "DELETE_SKIPPED_NOT_SAVED":
            i["source_message"] = MESSAGES["DELETE_SKIPPED_NOT_SAVED"]
    has = any(f["has_file"] for f in files)
    if not items and rec.get("result_path"):           # 舊版的掃描紀錄（沒有 items）
        has = Path(rec["result_path"]).exists() and fresh
    out["has_file"] = has
    with _LOCK:
        live = _LIVE.get(rec["id"])
        if live:
            out.update({"phase": live.get("phase"), "ahead": live.get("ahead"),
                        "live_pages": live.get("pages"), "live_bytes": live.get("bytes"),
                        "live_doc": live.get("doc"), "live_docs": live.get("docs"),
                        "live_files": live.get("files")})
    code = out.get("error_code")
    out["error_message"] = (mfp_escl.MESSAGES.get(code) or mfp_share.MESSAGES.get(code) or mfp_box.MESSAGES.get(code)
                            or mfp_queue.MESSAGES.get(code) or MESSAGES.get(code) or code) if code else None
    note = out.get("workspace_note")
    out["workspace_message"] = MESSAGES.get(note) if note else None
    return out


def cancel(owner: str, job_id: str) -> Optional[dict]:
    rec = mfp_store.get_scan(owner, job_id)
    if not rec:
        return None
    with _LOCK:
        live = _LIVE.get(job_id)
    if live and rec["status"] in ("queued", "running"):
        live["cancel"].set()
        mfp_queue.cancel(live["ticket"])
    return mfp_store.get_scan(owner, job_id)


def result_files(owner: str, job_id: str) -> list[tuple[Path, str]]:
    """還在暫存區、可以下載的檔案（本人的、沒過期的）：`[(路徑, 檔名)]`。"""
    rec = mfp_store.get_scan(owner, job_id)
    if not rec or time.time() - (rec.get("finished_at") or 0) >= RESULT_TTL:
        return []
    root = (_work_root() / job_id).resolve()
    out: list[tuple[Path, str]] = []
    items = rec.get("items") or []
    cands: list[tuple[str, str]] = [(f.get("stored") or "", f.get("name") or "")
                                    for i in items for f in i.get("files") or [] if f.get("kept")]
    if not items and rec.get("result_path"):            # 舊版的掃描紀錄
        cands = [(str(rec["result_path"]), rec.get("file_name") or "scan.pdf")]
    for stored, name in cands:
        p = Path(stored)
        p = p if p.is_absolute() else root / p
        try:
            p.resolve().relative_to(_work_root().resolve())   # 紀錄被竄改也不會指到暫存區以外
        except (ValueError, OSError):
            continue
        if p.is_file():
            out.append((p, name or p.name))
    return out


def result_file(owner: str, job_id: str) -> Optional[tuple[Path, str]]:
    """只有一個檔案時的捷徑（舊的呼叫端）。"""
    got = result_files(owner, job_id)
    return got[0] if got else None


def bundle(owner: str, job_id: str) -> Optional[Path]:
    """多個檔案打包成 ZIP（不壓縮：圖檔與 PDF 本來就壓過了），放在作業的暫存目錄裡、下次直接用。"""
    import zipfile
    got = result_files(owner, job_id)
    if len(got) < 2:
        return None
    z = _work_root() / job_id / "bundle.zip"
    if z.is_file():
        return z
    part = z.with_suffix(".part")
    names: set[str] = set()
    with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_STORED) as zf:
        for p, name in got:
            n = name
            k = 2
            while n in names:
                stem, dot, ext = name.rpartition(".")
                n = f"{stem}-{k}.{ext}" if dot else f"{name}-{k}"
                k += 1
            names.add(n)
            zf.write(p, arcname=n)
    part.replace(z)
    return z


def prune(max_age: float = RESULT_TTL) -> int:
    """清掉超過保留時間的暫存檔（排程清理會呼叫；列清單時也順手）。"""
    root = _work_root()
    if not root.is_dir():
        return 0
    n = 0
    now = time.time()
    with _LOCK:
        live = set(_LIVE)
    for d in root.iterdir():
        if d.name in live or not d.is_dir():
            continue
        try:
            if now - d.stat().st_mtime > max_age:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        except OSError:
            pass
    return n


def _reset_for_tests() -> None:
    global _INIT_DONE
    with _LOCK:
        _LIVE.clear()
    _INIT_DONE = False
