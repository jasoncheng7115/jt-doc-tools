"""ODT → docx 轉換 helper（用 LibreOffice / OxOffice headless）。

ODT-first 路線下，docx output 不再由 jtdt-reform 直寫 OOXML，而是先產 ODT
再用 soffice 引擎自動轉成 docx — 由 LO 引擎自己保證 OOXML 兼容性，避開直
寫 OOXML 的所有 quirks。

外部入口：
- convert_odt_to_docx(odt_path, docx_path) -> dict {ok, error}
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def _find_soffice() -> str | None:
    """找 soffice binary — 優先 OxOffice，其次 LibreOffice，最後 PATH。

    v1.8.94：Linux LibreOffice 7.3 對 odfpy 產的 ODT 載入失敗
    （連 .txt 也回「source file could not be loaded」），改成 OxOffice 路徑優先。
    """
    candidates = [
        # OxOffice 各平台優先
        "/opt/oxoffice/program/soffice",  # Linux OxOffice deb
        "/Applications/OxOffice.app/Contents/MacOS/soffice",  # macOS
        "C:\\Program Files\\OxOffice\\program\\soffice.exe",  # Windows
        # LibreOffice fallback
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/opt/libreoffice/program/soffice",
        shutil.which("soffice"),
        shutil.which("libreoffice"),
    ]
    return next((c for c in candidates if c and Path(c).exists()), None)


def convert_odt_to_docx(odt_path: Path, docx_path: Path,
                          timeout_sec: float = 180.0) -> dict:
    """把 ODT 轉成 docx。

    args:
        odt_path: 輸入 .odt
        docx_path: 輸出 .docx
        timeout_sec: soffice 子行程上限

    回 {ok: bool, error: str (若失敗)}
    """
    odt_path = Path(odt_path)
    docx_path = Path(docx_path)
    if not odt_path.exists():
        return {"ok": False, "error": f"odt 不存在: {odt_path}"}

    soffice = _find_soffice()
    if not soffice:
        return {"ok": False, "error": "找不到 soffice — 需安裝 OxOffice 或 LibreOffice"}

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = docx_path.parent
    profile_dir = work_dir / "_so_odt2docx_profile"
    profile_dir.mkdir(exist_ok=True)

    try:
        r = subprocess.run(
            [soffice, "--headless",
             f"-env:UserInstallation=file://{profile_dir}",
             "--convert-to", "docx",
             "--outdir", str(work_dir),
             str(odt_path)],
            capture_output=True, timeout=timeout_sec, text=True,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"soffice 轉檔超過 {timeout_sec}s"}
    except Exception as e:
        return {"ok": False, "error": f"soffice 執行失敗: {e}"}

    # soffice 輸出檔名 = odt_path.stem + ".docx"
    produced = work_dir / (odt_path.stem + ".docx")
    if not produced.exists():
        return {"ok": False,
                "error": f"soffice 無輸出 (rc={r.returncode}, stderr={r.stderr[:200]})"}
    if produced != docx_path:
        try:
            shutil.move(str(produced), str(docx_path))
        except Exception as e:
            return {"ok": False, "error": f"搬移輸出失敗: {e}"}
    return {"ok": True}
