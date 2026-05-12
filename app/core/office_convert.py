"""Convert Office documents (.docx/.doc/.xlsx/.xls/.odt/.ods/.pptx…) to PDF.

Delegates to a headless LibreOffice (or its drop-in fork OxOffice, which
ships on many Mac setups). We search a few common install paths and the
``PATH``; if none is found, :func:`convert_to_pdf` raises so the caller can
surface a clear error.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional


OFFICE_EXTENSIONS = {
    ".doc", ".docx", ".odt", ".rtf",
    ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp",
    ".txt", ".csv",
}


def is_office_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in OFFICE_EXTENSIONS


def find_soffice() -> Optional[str]:
    """Locate a headless office binary; returns an executable path or None.

    Order: user-customisable paths from :mod:`conv_settings` (custom first,
    then built-ins in the user's saved order, including Windows defaults),
    then a final ``PATH`` fallback via ``shutil.which``.
    """
    from .conv_settings import conv_settings
    for p in conv_settings.get_executable_paths():
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return (
        shutil.which("soffice")
        or shutil.which("libreoffice")
        or shutil.which("soffice.exe")
        or shutil.which("libreoffice.exe")
    )


def detect_engine() -> str:
    """Return a human-readable engine label: 'OxOffice', 'LibreOffice',
    or '(未安裝)'. Decides by path — anything containing 'oxoffice' (any
    case) is OxOffice, otherwise LibreOffice. Cheap path-string check
    (no subprocess) — safe to call from request handlers."""
    p = find_soffice()
    if not p:
        return "(未安裝)"
    return "OxOffice" if "oxoffice" in p.lower() else "LibreOffice"


# Serialise concurrent office conversions.
#
# LibreOffice / OxOffice use a single user profile dir (our own, see below).
# Two simultaneous headless instances sharing that profile will race on the
# internal lockfile and at least one will fail — even with --nolockcheck.
# A process-wide threading Lock is sufficient for the typical small-team
# deployment (< 10 concurrent users rarely hit this), with near-zero
# overhead when only one user is converting at a time.
_soffice_lock = threading.Lock()


def _build_soffice_cmd(soffice: str, args: list[str]) -> tuple[list, dict]:
    """Build subprocess.Popen kwargs for cross-platform soffice invocation.

    Returns (argv, popen_kwargs). popen_kwargs may include `creationflags`
    (Windows) or wrap the cmd with osascript (macOS).
    """
    import sys as _sys
    import os as _os
    import shlex as _shlex
    kwargs: dict = {}
    if _sys.platform == "darwin":
        # macOS: 直接 fork+exec soffice 會 SIGABRT (拿不到 WindowServer)，
        # `open -W -a` 又會被當 GUI app 啟動而忽略 --headless。改用 osascript
        # 的 `do shell script` — 它在 user 的 Aqua context 跑，spawn 出來的
        # shell 子行程能繼承 GUI session 連線。
        quoted = " ".join(_shlex.quote(x) for x in [soffice] + args)
        escaped = quoted.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", f'do shell script "{escaped}"'], kwargs
    if _sys.platform.startswith("win"):
        # Windows: 在 Service (Session 0) 跑時，soffice 預設會嘗試 attach console
        # → 卡住。CREATE_NO_WINDOW 強制 detached console。
        # 另外一些 LocalSystem service env 缺 TEMP/TMP → 給乾淨的 env 帶 .venv
        # 的 PATH 與我們可寫的 TEMP，避免 soffice 跑去寫系統路徑被擋。
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
        # 繼承 env 但確保 TEMP 是 writable（service-isolated session 的
        # %TEMP% 預設是 C:\Windows\Temp，理論上 writable，但保險起見明確設）
        env = dict(_os.environ)
        env.setdefault("TEMP", env.get("TMP") or _os.environ.get("TEMP", ""))
        env.setdefault("TMP", env["TEMP"])
        kwargs["env"] = env
    return [soffice] + args, kwargs


def _profile_uri(profile_path: Path) -> str:
    """Build a valid `file://` URI for the soffice -env:UserInstallation arg.

    Bug fix (issue #5, v1.5.1): on Windows we used to build
    `file://C:\\Users\\...\\profile` by string concat. That's a malformed URI
    (Windows file URIs need three slashes + forward slashes:
    `file:///C:/Users/.../profile`). soffice silently fell back to the
    LocalSystem default profile → first-time setup hung in Session 0
    → all conversions timeout at 60s.

    Path.as_uri() does the right thing on all platforms.
    """
    return profile_path.resolve().as_uri()


def convert_to_pdf(src: Path, dst_pdf: Path, timeout: float = 60.0) -> None:
    """Run soffice headless to convert ``src`` into ``dst_pdf``.

    Uses a *fresh* per-call user-profile directory (``-env:UserInstallation``)
    inside the same tempdir as the output. This serves two purposes:

    1. Avoids touching the user's real LibreOffice/OxOffice profile (otherwise
       opening the GUI while/after we've run headless leaves it locked/empty).
    2. Discards any crash/recovery state between calls — a *shared* profile
       accumulates "文件復原" prompts on macOS that block subsequent headless
       runs forever, even with --headless --norestore.

    Concurrency: serialised via a process-wide lock (see _soffice_lock).
    Multiple simultaneous calls queue up rather than interleave (one soffice
    process per host at a time keeps things predictable).
    """
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError(
            "找不到 LibreOffice / OxOffice。請安裝其中一個，或先自行轉成 PDF 上傳。"
        )

    with tempfile.TemporaryDirectory() as td:
        # Fresh per-call profile dir. A *shared* profile accumulates crash/recovery
        # state across calls — on macOS that pops the "文件復原" dialog and blocks
        # the headless run forever. Throwing the profile away each call avoids the
        # entire problem (cost is ~200ms first-run init, acceptable).
        profile_path = Path(td) / "profile"
        soffice_args = [
            f"-env:UserInstallation={_profile_uri(profile_path)}",
            "--safe-mode",       # skip user customisations + recovery prompt
            "--headless",
            "--norestore",
            "--nologo",
            "--nolockcheck",
            "--nodefault",
            "--nofirststartwizard",
            "--convert-to", "pdf",
            "--outdir", td,
            str(src),
        ]
        cmd, popen_kwargs = _build_soffice_cmd(soffice, soffice_args)
        # Serialise: at most one soffice at a time. Even though each call now
        # has its own profile, two concurrent osascript→soffice on macOS still
        # race on the WindowServer/Aqua bootstrap. Cheap to lock; ~no overhead
        # in the common single-user case.
        with _soffice_lock:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **popen_kwargs,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Hung parsing the file — force-kill so it doesn't leave a
                # zombie soffice holding the profile lock.
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise RuntimeError(
                    f"office 轉 PDF 卡住（超過 {int(timeout)} 秒）。這份檔案可能已毀損或"
                    f"含有 LibreOffice/OxOffice 無法解析的內容。請用 Word/Pages 另存"
                    f"一份乾淨的版本再試，或直接請對方提供 PDF 版。"
                )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"office 轉 PDF 失敗：{stderr.decode('utf-8', 'replace') or stdout.decode('utf-8', 'replace')}"
                )
        produced = Path(td) / (src.stem + ".pdf")
        if not produced.exists():
            raise RuntimeError("轉檔成功但找不到輸出檔")
        dst_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), str(dst_pdf))


def convert_to_docx(src: Path, dst_docx: Path, timeout: float = 60.0) -> None:
    """Run soffice headless to convert ``src`` (e.g. legacy .doc) into modern .docx.

    Same lock / profile / safety pattern as convert_to_pdf — see that function's
    docstring for rationale on the per-call profile + global lock.
    """
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError(
            "找不到 LibreOffice / OxOffice。請先安裝其中一個，或自行在 Word 內另存為 .docx 後上傳。"
        )

    with tempfile.TemporaryDirectory() as td:
        profile_path = Path(td) / "profile"
        soffice_args = [
            f"-env:UserInstallation={_profile_uri(profile_path)}",
            "--safe-mode", "--headless", "--norestore", "--nologo",
            "--nolockcheck", "--nodefault", "--nofirststartwizard",
            "--convert-to", "docx",
            "--outdir", td,
            str(src),
        ]
        cmd, popen_kwargs = _build_soffice_cmd(soffice, soffice_args)
        with _soffice_lock:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     **popen_kwargs)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try: proc.communicate(timeout=5)
                except Exception: pass
                raise RuntimeError(
                    f"office 轉 .docx 卡住（超過 {int(timeout)} 秒）。檔案可能已毀損或含 LibreOffice 無法解析的內容。"
                )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"office 轉 .docx 失敗：{stderr.decode('utf-8', 'replace') or stdout.decode('utf-8', 'replace')}"
                )
        produced = Path(td) / (src.stem + ".docx")
        if not produced.exists():
            raise RuntimeError("轉檔成功但找不到輸出 .docx")
        dst_docx.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), str(dst_docx))


def convert_to_text(src: Path, timeout: float = 60.0) -> str:
    """Run soffice headless to convert ``src`` into UTF-8 plain text.

    Equivalent to opening the file in OxOffice/LibreOffice and choosing
    "File → Save As → Text (UTF-8)" — gives the same paragraph layout
    you'd get from manually copy-pasting from the rendered document.
    Use this for translate-doc / wordcount where preserving paragraph
    structure matters more than perfect formatting.

    Returns the decoded text. Raises RuntimeError if soffice missing or
    conversion fails.
    """
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError(
            "找不到 LibreOffice / OxOffice — Office / ODF 檔案需先轉成 TXT 才能翻譯。"
        )
    with tempfile.TemporaryDirectory() as td:
        profile_path = Path(td) / "profile"
        soffice_args = [
            f"-env:UserInstallation={_profile_uri(profile_path)}",
            "--safe-mode",
            "--headless",
            "--norestore",
            "--nologo",
            "--nolockcheck",
            "--nodefault",
            "--nofirststartwizard",
            "--convert-to", "txt:Text (encoded):UTF8",
            "--outdir", td,
            str(src),
        ]
        cmd, popen_kwargs = _build_soffice_cmd(soffice, soffice_args)
        with _soffice_lock:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **popen_kwargs,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise RuntimeError(
                    f"office 轉文字卡住（超過 {int(timeout)} 秒）。"
                    "這份檔案可能已毀損或含有 LibreOffice/OxOffice 無法解析的內容。"
                )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"office 轉文字失敗：{stderr.decode('utf-8', 'replace') or stdout.decode('utf-8', 'replace')}"
                )
        produced = Path(td) / (src.stem + ".txt")
        if not produced.exists():
            raise RuntimeError("轉檔成功但找不到輸出 .txt")
        # soffice writes UTF-8 (BOM-stripped); be tolerant of encoding hiccups.
        try:
            return produced.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return produced.read_bytes().decode("utf-8", errors="replace")
