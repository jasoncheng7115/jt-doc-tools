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
import re
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
    version_line = (out or "").strip().splitlines()[0] if out else ""
    # Strip the long build hash that OxOffice / LibreOffice append after the
    # version, e.g. "OxOffice 11.0.4.1 855623c6c181122c9b97d204c8c74172e167cf75"
    # → "OxOffice 11.0.4.1". Hash is noise for users; if they need it, the
    # binary path is shown and they can re-run --version manually.
    import re as _re
    version = _re.sub(r"\s+[0-9a-f]{20,}.*$", "", version_line)
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


# OxOffice / LibreOffice oosplash + cairo + GTK 啟動時 dlopen 的全套 lib。
# 每加一個都是因為某客戶踩到「.so.X: cannot open shared object file」死掉。
# 一次裝齊比客戶踩一個補一個好 — Debian / Ubuntu minimal / server 鏡像
# 經常少裝這些（apt 預設 --no-install-recommends 又會省掉更多）。
# 順序按「漏裝最常見」由上往下排。
_OXOFFICE_X11_LIBS = [
    # (soname, apt-pkg)
    # 核心 X11 client lib — oosplash 必呼叫
    ("libXinerama.so.1", "libxinerama1"),
    ("libXrandr.so.2", "libxrandr2"),
    ("libXcursor.so.1", "libxcursor1"),
    ("libXi.so.6", "libxi6"),
    ("libXtst.so.6", "libxtst6"),
    ("libSM.so.6", "libsm6"),
    ("libXext.so.6", "libxext6"),
    ("libXrender.so.1", "libxrender1"),
    # X11 extensions — OxOffice 11+ 新依賴（客戶 v1.4.39 踩到 libX11-xcb）
    ("libX11-xcb.so.1", "libx11-xcb1"),
    ("libXcomposite.so.1", "libxcomposite1"),
    ("libXdamage.so.1", "libxdamage1"),
    ("libXfixes.so.3", "libxfixes3"),
    # Keyboard input — OxOffice 11 起改用 xkbcommon
    ("libxkbcommon.so.0", "libxkbcommon0"),
    # 系統服務（cups 列印對話、dbus IPC）
    ("libdbus-1.so.3", "libdbus-1-3"),
    ("libcups.so.2", "libcups2"),
    # 字型/圖形（多半已在系統，但 minimal 鏡像有時也缺）
    ("libfontconfig.so.1", "libfontconfig1"),
    ("libfreetype.so.6", "libfreetype6"),
    ("libcairo.so.2", "libcairo2"),
    ("libpango-1.0.so.0", "libpango-1.0-0"),
    ("libpangocairo-1.0.so.0", "libpangocairo-1.0-0"),
    ("libgdk_pixbuf-2.0.so.0", "libgdk-pixbuf-2.0-0"),
    # NSS — OxOffice 加密元件 / 數位簽章用
    ("libnss3.so", "libnss3"),
]


def _probe_oxoffice_x11_libs() -> dict:
    """OxOffice / LibreOffice oosplash dlopens these X11 libs at startup even
    in headless mode. Debian/Ubuntu minimal doesn't preinstall them; missing
    libs cause office-to-pdf / pdf-to-image / doc-diff to die with
    `libXinerama.so.1: cannot open shared object file: No such file or
    directory`."""
    if not _is_linux():
        return {"installed": True, "version": "n/a (Linux only)", "extra": "",
                "ok": True, "binary": ""}
    search_paths = [
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/usr/lib/aarch64-linux-gnu"),
        Path("/usr/lib64"),
        Path("/usr/lib"),
        Path("/lib/x86_64-linux-gnu"),
        Path("/lib/aarch64-linux-gnu"),
    ]
    rc, ldconfig_out, _ = _run_capture(["ldconfig", "-p"], timeout=3)
    ldconfig_index = ldconfig_out if rc == 0 else ""
    missing: list[tuple[str, str]] = []
    for soname, pkg in _OXOFFICE_X11_LIBS:
        found = any((sp / soname).exists() for sp in search_paths)
        if not found and ldconfig_index:
            found = soname in ldconfig_index
        if not found:
            missing.append((soname, pkg))
    if missing:
        return {
            "installed": False,
            "version": f"missing {len(missing)}/{len(_OXOFFICE_X11_LIBS)}",
            "extra": "缺：" + ", ".join(p for _, p in missing),
            "ok": False,
            "missing_pkgs": [p for _, p in missing],
            "binary": "",
        }
    return {
        "installed": True,
        "version": f"完整（{len(_OXOFFICE_X11_LIBS)} 個）",
        "extra": "",
        "ok": True,
        "binary": "",
    }


def _probe_java_runtime() -> dict:
    """Detect a Java Runtime — needed by OxOffice/LibreOffice for some
    legacy doc/odf operations. Tries `java -version` (writes to stderr)
    and parses the version line."""
    java_bin = shutil.which("java")
    if not java_bin:
        return {
            "installed": False, "version": "", "extra": "找不到 java 執行檔",
            "ok": False, "binary": "",
        }
    try:
        proc = subprocess.run(
            [java_bin, "-version"],
            capture_output=True, text=True, timeout=5,
        )
        # `java -version` writes to STDERR, e.g. `openjdk version "17.0.10" ...`
        out = (proc.stderr or proc.stdout or "").strip().splitlines()
        first = out[0] if out else ""
        m = re.search(r'version\s+"([^"]+)"', first)
        ver = m.group(1) if m else first
        return {
            "installed": True, "version": ver, "extra": "",
            "ok": True, "binary": java_bin,
        }
    except Exception as e:
        return {
            "installed": False, "version": "", "extra": f"java -version 失敗: {e}",
            "ok": False, "binary": java_bin,
        }


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
        "impact_en": "pdf-editor uses OCR to recover text when the original PDF font has missing/broken ToUnicode CMap. Without tesseract, falls back to manual retype.",
        "soft": True,
        "probe": _probe_tesseract,
        "install_cmd": {
            "linux": "sudo apt install tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-eng",
            "macos": "brew install tesseract tesseract-lang",
            "windows": "winget install UB-Mannheim.TesseractOCR  (or download https://github.com/UB-Mannheim/tesseract/wiki)",
        },
    },
    {
        "key": "office",
        "label": "Office engine (OxOffice / LibreOffice)",
        "category": "文書轉檔",
        "impact": "office-to-pdf、pdf-to-office、合併等需要 Office 解析 docx/xlsx/odt 的工具。",
        "impact_en": "Required by office-to-pdf, pdf-to-office, and any tool that needs to parse docx/xlsx/odt.",
        "soft": False,
        "probe": _probe_office,
        "install_cmd": {
            "linux": "sudo apt install libreoffice fonts-noto-cjk  (recommended: install OxOffice from https://github.com/OSSII/OxOffice/releases)",
            "macos": "brew install --cask libreoffice  (recommended: OxOffice)",
            "windows": "winget install TheDocumentFoundation.LibreOffice  (recommended: OxOffice)",
        },
    },
    {
        "key": "oxoffice-x11-libs",
        "label": "OxOffice / LibreOffice 執行時依賴 X11 lib",
        "category": "文書轉檔",
        "impact": "OxOffice 與 LibreOffice 的 oosplash 啟動時會 dlopen libXinerama / libXrandr / libXcursor 等 X11 client lib（即使 --headless 模式也一樣）。Debian / Ubuntu 的 minimal / server 安裝沒有這些 lib，缺的話 office-to-pdf、pdf-to-image、文件差異比對等需轉檔的工具會失敗，錯誤訊息類似「libXinerama.so.1: cannot open shared object file: No such file or directory」。",
        "impact_en": "OxOffice and LibreOffice oosplash dlopens X11 client libs (libXinerama / libXrandr / libXcursor / ...) at startup even in --headless mode. Debian/Ubuntu minimal/server installs lack these libs; missing => office-to-pdf, pdf-to-image, doc-diff fail with 'libXinerama.so.1: cannot open shared object file: No such file or directory'.",
        "soft": False,
        "probe": _probe_oxoffice_x11_libs,
        "install_cmd": {
            "linux": "sudo apt install libxinerama1 libxrandr2 libxcursor1 libxi6 libxtst6 libsm6 libxext6 libxrender1 libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libdbus-1-3 libcups2 libfontconfig1 libfreetype6 libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libnss3",
            "macos": "n/a (macOS uses Aqua, not X11)",
            "windows": "n/a (Windows uses GDI, not X11)",
        },
    },
    {
        "key": "java-runtime",
        "label": "Java Runtime (OxOffice / LibreOffice 部分匯入需要)",
        "category": "文書轉檔",
        "impact": "OxOffice / LibreOffice 在處理含 macro 的舊 .doc / .xls 或部分 ODF 公式時會呼叫 javaldx 確認 JRE 路徑；找不到 JRE 會直接 abort，office-to-pdf 報「javaldx: Could not find a Java Runtime Environment!」。Debian/Ubuntu minimal 沒預裝 Java。",
        "impact_en": "OxOffice/LibreOffice calls javaldx for legacy .doc/.xls macros and some ODF formulas; missing JRE aborts conversion with 'javaldx: Could not find a Java Runtime Environment!'.",
        "soft": False,
        "probe": lambda: _probe_java_runtime(),
        "install_cmd": {
            "linux": "sudo apt install default-jre-headless",
            "macos": "brew install temurin   (or system Java already present)",
            "windows": "winget install EclipseAdoptium.Temurin.21.JRE",
        },
    },
    {
        "key": "cjk-fonts",
        "label": "CJK fonts",
        "category": "字型",
        "impact": "PDF 文字插入、浮水印、用印需要正確中文 glyph 渲染。沒有 CJK 字型則中文顯示成豆腐方框。",
        "impact_en": "Needed to render Chinese glyphs in PDF text, watermark, stamp output. Without CJK fonts, Chinese shows as tofu boxes.",
        "soft": True,
        "probe": _probe_cjk_fonts,
        "install_cmd": {
            "linux": "sudo apt install fonts-noto-cjk",
            "macos": "Built-in PingFang on macOS; usually no install needed",
            "windows": "Built-in Microsoft JhengHei on Windows; usually no install needed",
        },
    },
    {
        "key": "pytesseract",
        "label": "pytesseract (Python wrapper)",
        "category": "OCR",
        "impact": "tesseract 的 Python 包裝，沒裝會導致 OCR 路徑直接 disabled。",
        "impact_en": "Thin Python wrapper around tesseract. Missing => OCR code path is disabled.",
        "soft": True,
        "probe": lambda: _probe_python_pkg("pytesseract"),
        "install_cmd": {
            "linux": f"{shutil.which('uv') or 'uv'} pip install pytesseract  (or: pip install pytesseract)",
            "macos": "uv pip install pytesseract",
            "windows": "uv pip install pytesseract",
        },
    },
    {
        "key": "PIL",
        "label": "Pillow (PIL)",
        "category": "影像",
        "impact": "PDF→影像、影像處理、OCR 前處理。核心套件；缺則大量功能無法運作。",
        "impact_en": "Imaging core: PDF→image, image processing, OCR preprocessing. Many features break without it.",
        "soft": False,
        "probe": lambda: _probe_python_pkg("PIL"),
        "install_cmd": {
            "linux": "uv sync  (normally auto-installed)",
            "macos": "uv sync",
            "windows": "uv sync",
        },
    },
]


def collect_sys_deps(lang: str = "zh") -> list[dict]:
    """Return current status of all registered system deps for the admin
    page / JSON API. ``lang='en'`` swaps impact text to English (used by the
    CLI summary because Windows console can't always render CJK reliably).
    Never throws even if probe crashes.
    """
    plat = _platform_key()
    out = []
    for dep in _DEPS:
        try:
            probe = dep["probe"]()
        except Exception as e:
            probe = {"installed": False, "version": "", "extra": f"probe error: {e}", "ok": False}
        ok = bool(probe.get("ok", probe.get("installed")))
        impact = dep.get("impact_en") if lang == "en" else dep["impact"]
        out.append({
            "key": dep["key"],
            "label": dep["label"],
            "category": dep["category"],
            "impact": impact or dep["impact"],
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
