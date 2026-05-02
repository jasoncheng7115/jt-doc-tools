"""System dependency inventory for the admin status page.

Each entry describes ONE external (non-Python-pkg) dependency the app uses,
its current presence + version on this machine, why it matters, and how to
install it on each platform.

Add new entries here when introducing any new system dependency. The
``jtdt update`` flow's ``_print_system_deps_summary`` and the admin
``/admin/sys-deps`` page both render from this single source of truth.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _platform_key() -> str:
    if _is_linux():
        return "linux"
    if _is_macos():
        return "macos"
    if _is_windows():
        return "windows"
    return "unknown"


def _run_capture(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except Exception:
        return -1, "", ""


# ---- per-dep probes ---------------------------------------------------------

def _probe_tesseract() -> dict:
    binary = shutil.which("tesseract")
    if not binary:
        return {
            "installed": False,
            "version": "",
            "extra": "",
            "binary": "",
        }
    rc, out, err = _run_capture([binary, "--version"], timeout=3)
    blob = (out or err or "").splitlines()
    version = ""
    if blob:
        first = blob[0].strip()
        # "tesseract 4.1.1" or "tesseract v5.3.0"
        parts = first.split()
        if len(parts) >= 2:
            version = parts[1].lstrip("v")
    rc2, langs_out, _ = _run_capture([binary, "--list-langs"], timeout=3)
    langs = []
    if rc2 == 0:
        for line in (langs_out or "").splitlines()[1:]:
            line = line.strip()
            if line:
                langs.append(line)
    has_chi_tra = "chi_tra" in langs
    has_eng = "eng" in langs
    return {
        "installed": True,
        "version": version,
        "extra": ("缺繁中訓練檔 chi_tra" if not has_chi_tra
                  else ("缺英文訓練檔 eng" if not has_eng
                        else "完整可用")),
        "binary": binary,
        "ok": has_chi_tra and has_eng,
        "langs": langs,
    }


def _probe_office() -> dict:
    candidates = []
    if _is_linux():
        candidates = [
            "/opt/oxoffice/program/soffice",
            "/usr/bin/soffice",
            "/usr/bin/libreoffice",
        ]
    elif _is_macos():
        candidates = [
            "/Applications/OxOffice.app/Contents/MacOS/soffice",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]
    elif _is_windows():
        prog = os.environ.get("ProgramFiles", r"C:\Program Files")
        prog86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates = [
            rf"{prog}\OxOffice\program\soffice.exe",
            rf"{prog}\LibreOffice\program\soffice.exe",
            rf"{prog86}\LibreOffice\program\soffice.exe",
        ]
    binary = next((c for c in candidates if Path(c).exists()), "")
    if not binary:
        binary = shutil.which("soffice") or shutil.which("libreoffice") or ""
    if not binary:
        return {"installed": False, "version": "", "extra": "", "binary": "", "ok": False}
    flavor = "OxOffice" if "oxoffice" in binary.lower() or "OxOffice" in binary else "LibreOffice"
    rc, out, _ = _run_capture([binary, "--version"], timeout=5)
    version = (out or "").strip().splitlines()[0] if out else ""
    return {
        "installed": True,
        "version": version,
        "extra": f"類型：{flavor}",
        "binary": binary,
        "ok": True,
        "flavor": flavor,
    }


def _probe_python_pkg(import_name: str) -> dict:
    try:
        mod = __import__(import_name)
        version = getattr(mod, "__version__", "")
        return {"installed": True, "version": str(version), "extra": "", "ok": True}
    except Exception as e:
        return {"installed": False, "version": "", "extra": str(e), "ok": False}


def _probe_cjk_fonts() -> dict:
    """Look for at least one CJK font file in standard locations."""
    candidates = []
    if _is_linux():
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        ]
    elif _is_macos():
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/Library/Fonts/Songti.ttc",
        ]
    elif _is_windows():
        candidates = [
            r"C:\Windows\Fonts\msjh.ttc",  # Microsoft JhengHei
            r"C:\Windows\Fonts\mingliu.ttc",
            r"C:\Windows\Fonts\msyh.ttc",
        ]
    found = [c for c in candidates if Path(c).exists()]
    return {
        "installed": bool(found),
        "version": f"{len(found)} 個 CJK 字型檔" if found else "",
        "extra": "" if found else "建議安裝 Noto CJK 或系統內建 CJK 字型",
        "binary": found[0] if found else "",
        "ok": bool(found),
    }


# ---- registry ---------------------------------------------------------------

# Each entry is the single source of truth used by both the admin page and
# `jtdt update` summary. To add a new dep, append here AND add the
# install-time logic in install.sh / install.ps1 / cli._ensure_*.
_DEPS = [
    {
        "key": "tesseract",
        "label": "Tesseract OCR",
        "category": "OCR",
        "impact": "pdf-editor 在原 PDF 字型缺/壞 ToUnicode CMap 時，自動 OCR 辨識既有文字。沒裝就退到「請手動重打」。",
        "soft": True,
        "probe": _probe_tesseract,
        "install_cmd": {
            "linux": "sudo apt install tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-eng",
            "macos": "brew install tesseract tesseract-lang",
            "windows": "winget install UB-Mannheim.TesseractOCR  或下載 https://github.com/UB-Mannheim/tesseract/wiki",
        },
    },
    {
        "key": "office",
        "label": "Office 引擎 (OxOffice / LibreOffice)",
        "category": "文書轉檔",
        "impact": "office-to-pdf、pdf-to-office、合併等需要 Office 解析 docx/xlsx/odt 的工具。",
        "soft": False,
        "probe": _probe_office,
        "install_cmd": {
            "linux": "sudo apt install libreoffice fonts-noto-cjk  （建議改裝 OxOffice：https://github.com/OSSII/OxOffice/releases）",
            "macos": "brew install --cask libreoffice  （建議改裝 OxOffice）",
            "windows": "winget install TheDocumentFoundation.LibreOffice  （建議改裝 OxOffice）",
        },
    },
    {
        "key": "cjk-fonts",
        "label": "CJK 中文字型",
        "category": "字型",
        "impact": "PDF 文字插入、浮水印、用印需要正確中文 glyph 渲染。沒有 CJK 字型則中文顯示成豆腐方框。",
        "soft": True,
        "probe": _probe_cjk_fonts,
        "install_cmd": {
            "linux": "sudo apt install fonts-noto-cjk",
            "macos": "macOS 內建 PingFang，正常情況不需安裝",
            "windows": "Windows 內建 微軟正黑體 / 新細明體，正常情況不需安裝",
        },
    },
    {
        "key": "pytesseract",
        "label": "pytesseract (Python wrapper)",
        "category": "OCR",
        "impact": "tesseract 的 Python 包裝，沒裝會導致 OCR 路徑直接 disabled。",
        "soft": True,
        "probe": lambda: _probe_python_pkg("pytesseract"),
        "install_cmd": {
            "linux": f"{shutil.which('uv') or 'uv'} pip install pytesseract  （或 pip install pytesseract）",
            "macos": "uv pip install pytesseract",
            "windows": "uv pip install pytesseract",
        },
    },
    {
        "key": "PIL",
        "label": "Pillow (PIL)",
        "category": "影像",
        "impact": "PDF→影像、影像處理、OCR 前處理。核心套件；缺則大量功能無法運作。",
        "soft": False,
        "probe": lambda: _probe_python_pkg("PIL"),
        "install_cmd": {
            "linux": "uv sync  （正常會自動裝起來）",
            "macos": "uv sync",
            "windows": "uv sync",
        },
    },
]


def collect_sys_deps() -> list[dict]:
    """Return current status of all registered system deps for the admin
    page / JSON API. Each entry merges the registry metadata with probe
    results — never throws even if probe crashes.
    """
    plat = _platform_key()
    out = []
    for dep in _DEPS:
        try:
            probe = dep["probe"]()
        except Exception as e:
            probe = {"installed": False, "version": "", "extra": f"probe error: {e}", "ok": False}
        ok = bool(probe.get("ok", probe.get("installed")))
        out.append({
            "key": dep["key"],
            "label": dep["label"],
            "category": dep["category"],
            "impact": dep["impact"],
            "soft": dep["soft"],
            "installed": bool(probe.get("installed")),
            "ok": ok,
            "version": probe.get("version", ""),
            "extra": probe.get("extra", ""),
            "binary": probe.get("binary", ""),
            "install_cmd": dep["install_cmd"].get(plat, ""),
            "platform": plat,
        })
    return out
