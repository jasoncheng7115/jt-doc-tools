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

def _find_tesseract_binary() -> str:
    """Find tesseract executable. Tries PATH first, then standard install
    locations on Windows / macOS — handles the very common case where the
    user has installed Tesseract but not added it to PATH (GitHub issue #4).
    Returns absolute path or empty string."""
    binary = shutil.which("tesseract")
    if binary:
        return binary
    # Common Windows install locations (UB-Mannheim installer + winget)
    if _is_windows():
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\UB-Mannheim.TesseractOCR_Microsoft.Winget.Source_8wekyb3d8bbwe\tesseract.exe"),
        ]
    elif _is_macos():
        candidates = [
            "/opt/homebrew/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/opt/local/bin/tesseract",  # MacPorts
        ]
    else:  # Linux
        candidates = [
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
        ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK if not _is_windows() else os.R_OK):
            return c
    return ""


def configure_pytesseract() -> str:
    """Set pytesseract.tesseract_cmd to the resolved binary path so OCR
    works even when the user hasn't added Tesseract to PATH. Idempotent;
    safe to call repeatedly. Returns the path used (or empty if none)."""
    path = _find_tesseract_binary()
    if not path:
        return ""
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = path
    except Exception:
        pass
    return path


def _probe_tesseract() -> dict:
    binary = _find_tesseract_binary()
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


def _probe_python_pkg(import_name: str, heavy: bool = False, dist_name: str = "") -> dict:
    """探 Python package 是否已裝。

    heavy=True：絕不 __import__（如 easyocr → 觸發 PyTorch 載入 ~700MB ~5-15 sec
    讓 admin /sys-deps 整段卡到 fetch timeout，issue #17 客戶踩到）。改用
    importlib.util.find_spec 純檔案系統判定，再用 importlib.metadata.version
    讀 distribution metadata 取版本（不執行 module）。

    dist_name：當 import name 跟 distribution name 不同時指定（如 PIL → Pillow）。
    """
    if heavy:
        import importlib.util as _iu
        try:
            spec = _iu.find_spec(import_name)
            if spec is None:
                return {"installed": False, "version": "", "extra": "未安裝", "ok": False}
            version = ""
            try:
                from importlib.metadata import version as _meta_version, PackageNotFoundError
                try:
                    version = _meta_version(dist_name or import_name)
                except PackageNotFoundError:
                    version = ""
            except Exception:
                pass
            return {"installed": True, "version": str(version), "extra": "", "ok": True}
        except Exception as e:
            return {"installed": False, "version": "", "extra": str(e), "ok": False}
    try:
        mod = __import__(import_name)
        version = getattr(mod, "__version__", "")
        return {"installed": True, "version": str(version), "extra": "", "ok": True}
    except Exception as e:
        return {"installed": False, "version": "", "extra": str(e), "ok": False}


def _probe_pdfjs_vendor() -> dict:
    """Check static/vendor/pdfjs/ has the files we need for the embedded viewer.

    The vendor blob is part of the git repo, so the only failure modes are
    accidental deletion or someone shrinking it in a future cleanup.
    """
    from pathlib import Path
    try:
        from .config import settings  # type: ignore
        root = Path(settings.base_dir if hasattr(settings, "base_dir") else ".")
    except Exception:
        # Fallback — sys_deps is called pre-config in some CLI paths
        root = Path(__file__).resolve().parent.parent.parent
    base = root / "static" / "vendor" / "pdfjs"
    required = [
        base / "build" / "pdf.mjs",
        base / "build" / "pdf.worker.mjs",
        base / "web" / "viewer.html",
        base / "web" / "viewer.mjs",
    ]
    optional_cjk = base / "web" / "cmaps"
    optional_fonts = base / "web" / "standard_fonts"
    missing = [p.name for p in required if not p.exists()]
    if missing:
        return {"installed": False, "version": "", "ok": False,
                "extra": "缺檔：" + ", ".join(missing)}
    # Try to read version hint from pdf.mjs banner (PDF.js writes "// pdf.js v5.x.x")
    version = ""
    try:
        # PDF.js writes its version into pdf.mjs as `const version = "X.Y.Z"`.
        # The assignment can be deep in the bundle (~line 23k for 5.x), so read
        # whole file. Only invoked on admin/sys-deps view — not hot path.
        head = (base / "build" / "pdf.mjs").read_text(encoding="utf-8", errors="ignore")
        import re as _re
        m = _re.search(r"const\s+version\s*=\s*['\"](\d+\.\d+\.\d+)['\"]", head)
        if m:
            version = m.group(1)
    except Exception:
        pass
    extras = []
    if optional_cjk.exists() and any(optional_cjk.iterdir()):
        extras.append("CJK cmaps OK")
    else:
        extras.append("缺 cmaps（中文 PDF 顯示異常）")
    if optional_fonts.exists() and any(optional_fonts.iterdir()):
        extras.append("標準字型 OK")
    else:
        extras.append("缺 standard_fonts")
    return {"installed": True, "version": version, "ok": True,
            "extra": "；".join(extras)}


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


def _find_java_binary() -> str:
    """跨平台找 java executable。先 PATH，再標準安裝位置。
    Windows winget 裝 Eclipse Temurin / Microsoft OpenJDK / Oracle JDK 後
    PATH 可能要重啟 process 才生效（issue #17 同模式：Java 已裝但 service
    process 用舊 env，shutil.which 找不到）。fallback 直接掃常見位置。"""
    binary = shutil.which("java")
    if binary:
        return binary
    if _is_windows():
        import glob as _glob
        prog = os.environ.get("ProgramFiles", r"C:\Program Files")
        prog86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        # Eclipse Temurin (winget EclipseAdoptium.Temurin.*) 預設位置：
        #   C:\Program Files\Eclipse Adoptium\jdk-21.0.11.10-hotspot\bin\java.exe
        #   C:\Program Files\Eclipse Adoptium\jre-21.0.11.10-hotspot\bin\java.exe
        # 各家發行版命名都不同，用 glob 處理版本號 wildcard。
        patterns = [
            rf"{prog}\Eclipse Adoptium\*\bin\java.exe",
            rf"{prog}\Microsoft\jdk-*\bin\java.exe",
            rf"{prog}\Java\*\bin\java.exe",
            rf"{prog}\Zulu\zulu-*\bin\java.exe",
            rf"{prog}\AdoptOpenJDK\*\bin\java.exe",
            rf"{prog}\BellSoft\LibericaJDK-*\bin\java.exe",
            rf"{prog}\Amazon Corretto\*\bin\java.exe",
            rf"{prog86}\Java\*\bin\java.exe",
        ]
        for pat in patterns:
            matches = _glob.glob(pat)
            if matches:
                # 多個版本就挑路徑字串最新（lexicographic 通常 = 最新版）
                return sorted(matches, reverse=True)[0]
    elif _is_macos():
        # macOS 用 /usr/libexec/java_home 處理多版本，但這需要 java 已被
        # /usr/bin/java shim 識別。直接掃 JavaVirtualMachines。
        candidates = [
            "/usr/bin/java",
            "/Library/Internet Plug-Ins/JavaAppletPlugin.plugin/Contents/Home/bin/java",
        ]
        import glob as _glob
        candidates += sorted(
            _glob.glob("/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java"),
            reverse=True,
        )
        for c in candidates:
            if os.path.exists(c):
                return c
    elif _is_linux():
        for c in ["/usr/bin/java", "/usr/lib/jvm/default-java/bin/java"]:
            if os.path.exists(c):
                return c
    return ""


def _probe_java_runtime() -> dict:
    """Detect a Java Runtime — needed by OxOffice/LibreOffice for some
    legacy doc/odf operations. Tries `java -version` (writes to stderr)
    and parses the version line."""
    java_bin = _find_java_binary()
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
        "label": "OxOffice / LibreOffice X11 函式庫",
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
        "impact": "PDF 文字插入、浮水印、用印需要正確中文 glyph 渲染。沒有 CJK 字型則中文顯示成空白方框 (缺字)。",
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
        "impact": "tesseract 的 Python 包裝（fallback OCR 引擎用 + pdf-editor 文字回復）。沒裝且 EasyOCR 也沒時 OCR 完全 disabled。",
        "impact_en": "Thin Python wrapper around tesseract (fallback OCR engine + pdf-editor text recovery).",
        "soft": True,
        "probe": lambda: _probe_python_pkg("pytesseract"),
        "install_cmd": {
            "linux": f"{shutil.which('uv') or 'uv'} pip install pytesseract  (or: pip install pytesseract)",
            "macos": "uv pip install pytesseract",
            "windows": "uv pip install pytesseract",
        },
    },
    {
        "key": "easyocr",
        "label": "EasyOCR (主 OCR 引擎)",
        "category": "OCR",
        "impact": "v1.7.2 起的主 OCR 引擎，中日韓辨識準確度明顯優於 tesseract（per-line bbox + LSTM-based）。沒裝會自動降回 tesseract。重型依賴：pulls in PyTorch (~700MB)。",
        "impact_en": "Primary OCR engine since v1.7.2 (CJK accuracy >> tesseract). Falls back to tesseract if missing.",
        "soft": True,
        "probe": lambda: _probe_python_pkg("easyocr", heavy=True),
        "install_cmd": {
            "linux": f"{shutil.which('uv') or 'uv'} pip install easyocr  (auto-installs PyTorch ~700MB)",
            "macos": "uv pip install easyocr",
            "windows": "uv pip install easyocr",
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
    {
        "key": "pdfjs",
        "label": "PDF.js Viewer (vendored)",
        "category": "前端資源",
        "impact": "pdf-ocr 完成後內嵌 PDF viewer 預覽結果（可直接拖選文字驗證）。隨原始碼附帶，不需另外安裝；缺則 viewer iframe 載不到（OCR 結果仍可下載）。",
        "impact_en": "Embedded PDF viewer used by pdf-ocr to preview results (text selection verification). Bundled with source; missing = viewer iframe broken (download still works).",
        "soft": True,
        "probe": _probe_pdfjs_vendor,
        "install_cmd": {
            "linux": "git pull (vendored under static/vendor/pdfjs/)",
            "macos": "git pull (vendored under static/vendor/pdfjs/)",
            "windows": "git pull (vendored under static/vendor/pdfjs/)",
        },
    },
]


def collect_sys_deps(lang: str = "zh") -> list[dict]:
    """Return current status of all registered system deps for the admin
    page / JSON API. ``lang='en'`` swaps impact text to English (used by the
    CLI summary because Windows console can't always render CJK reliably).
    Never throws even if probe crashes.

    v1.7.47：probes 改用 ThreadPoolExecutor 平行跑，總時間 = max(probe time)
    而非 sum。office (5s) + java (5s) + tesseract (3s) + ... 之前要 13-23s
    （issue #17 客戶 Win11 上 fetch timeout 看到「Failed to fetch」），
    平行後 ~5s。順序維持原 _DEPS 列表順序給 UI 一致。
    """
    plat = _platform_key()
    from concurrent.futures import ThreadPoolExecutor

    def _run_one(dep):
        try:
            return dep["probe"]()
        except Exception as e:
            return {"installed": False, "version": "", "extra": f"probe error: {e}", "ok": False}

    out = []
    # 8 個 worker 對應 ~9 個 deps；I/O bound (subprocess.run / file checks) 走
    # threading 沒 GIL 問題。
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="sysdep-probe") as ex:
        results = list(ex.map(_run_one, _DEPS))
    for dep, probe in zip(_DEPS, results):
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
