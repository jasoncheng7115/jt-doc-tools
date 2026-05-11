"""Tesseract trained data 管理 — admin 可下載 / 移除 .traineddata 檔。

從 GitHub `tesseract-ocr/tessdata_fast` 抓 .traineddata，寫進 tesseract
binary 同層的 tessdata 目錄。chi_tra 自動下載機制（cli.py）共用此模組。

Security:
- LANG_CATALOG 是白名單，只能裝清單內的 lang code（避免任意檔名 path traversal）
- 下載完驗檔大小 > 1MB 才視為成功
- 寫入路徑用 safe_paths.safe_join 二次檢查（不過 lang code 正規化已能擋）
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 共用語言 catalog — pdf-ocr router + admin/ocr-langs 都引用同一份
LANG_CATALOG: list[dict] = [
    # CJK 群（最常用）
    {"code": "chi_tra", "name": "繁體中文", "group": "cjk",     "hint": "台灣 / 香港",      "size_mb": 12},
    {"code": "chi_sim", "name": "簡體中文", "group": "cjk",     "hint": "中國大陸 / 新加坡", "size_mb": 12},
    {"code": "jpn",     "name": "日文",     "group": "cjk",     "hint": "日本",             "size_mb": 13},
    {"code": "kor",     "name": "韓文",     "group": "cjk",     "hint": "韓國",             "size_mb": 12},
    # 西方語言
    {"code": "eng",     "name": "英文",     "group": "western", "hint": "拉丁字母通用",    "size_mb": 4},
    {"code": "deu",     "name": "德文",     "group": "western", "hint": "德國 / 奧地利",    "size_mb": 8},
    {"code": "fra",     "name": "法文",     "group": "western", "hint": "法國 / 比利時",    "size_mb": 7},
    {"code": "spa",     "name": "西班牙文", "group": "western", "hint": "西班牙 / 拉美",    "size_mb": 9},
    {"code": "ita",     "name": "義大利文", "group": "western", "hint": "義大利",           "size_mb": 8},
    {"code": "por",     "name": "葡萄牙文", "group": "western", "hint": "巴西 / 葡萄牙",    "size_mb": 8},
    {"code": "nld",     "name": "荷蘭文",   "group": "western", "hint": "荷蘭 / 比利時",    "size_mb": 7},
    {"code": "rus",     "name": "俄文",     "group": "western", "hint": "俄羅斯",           "size_mb": 8},
    # 東南亞 / 其他
    {"code": "vie",     "name": "越南文",   "group": "other",   "hint": "越南",             "size_mb": 6},
    {"code": "tha",     "name": "泰文",     "group": "other",   "hint": "泰國",             "size_mb": 4},
    {"code": "ind",     "name": "印尼文",   "group": "other",   "hint": "印尼 / 馬來",      "size_mb": 5},
    {"code": "ara",     "name": "阿拉伯文", "group": "other",   "hint": "中東",             "size_mb": 5},
    {"code": "heb",     "name": "希伯來文", "group": "other",   "hint": "以色列",           "size_mb": 4},
    {"code": "hin",     "name": "印地文",   "group": "other",   "hint": "印度",             "size_mb": 6},
]

_LANG_CODE_RE = re.compile(r"^[a-z]{2,4}(_[a-z]{2,4})?$")
# 兩種品質變體 — fast 小快、best 大準
_TESSDATA_URLS = {
    "fast": "https://github.com/tesseract-ocr/tessdata_fast/raw/main",
    "best": "https://github.com/tesseract-ocr/tessdata_best/raw/main",
}
# 一般語言（fast / best 都有）的 best 變體大小估計倍數（fast × this）
_BEST_SIZE_MULTIPLIER = {
    "chi_tra": 4.2, "chi_sim": 4.0, "jpn": 3.5, "kor": 3.5,
    "eng": 3.0, "deu": 2.5, "fra": 2.5, "spa": 2.5, "ita": 2.5,
    "por": 2.5, "nld": 2.5, "rus": 2.5,
    "vie": 3.0, "tha": 3.0, "ind": 2.5, "ara": 2.5, "heb": 2.5, "hin": 2.5,
}
# 兩變體的檔案命名 convention（除 active 主檔 chi_tra.traineddata 外）
def _variant_path(tessdata: Path, code: str, variant: str) -> Path:
    """e.g. chi_tra.fast.traineddata, chi_tra.best.traineddata"""
    return tessdata / f"{code}.{variant}.traineddata"

def _active_path(tessdata: Path, code: str) -> Path:
    """e.g. chi_tra.traineddata (tesseract 真正讀的檔)"""
    return tessdata / f"{code}.traineddata"

# Backward-compat shim — 仍然有 code 引用 _TESSDATA_BASE_URL（fast）
_TESSDATA_BASE_URL = _TESSDATA_URLS["fast"]


def is_valid_lang_code(code: str) -> bool:
    """白名單檢查：必為 catalog 內 code，且 regex 過得了（防 path traversal）。"""
    if not code or not _LANG_CODE_RE.match(code):
        return False
    return any(item["code"] == code for item in LANG_CATALOG)


def _resolve_tesseract_binary() -> Optional[str]:
    """找 tesseract binary 路徑。重用 sys_deps 邏輯。"""
    try:
        from app.core.sys_deps import _find_tesseract_binary
        b = _find_tesseract_binary()
        if b:
            return b
    except Exception:
        pass
    return shutil.which("tesseract")


def get_tessdata_dir() -> Optional[Path]:
    """找 tessdata 目錄。最可靠的方式：跑 `tesseract --list-langs` 解析它
    自己印出來的路徑（stdout 第一行通常是 "List of available languages
    in \"/path/to/tessdata/\"" — 各 OS / 版本一致）。"""
    binary = _resolve_tesseract_binary()
    if not binary:
        return None
    # 1) 跑 tesseract 自己問
    try:
        out = subprocess.run(
            [binary, "--list-langs"], capture_output=True, text=True, timeout=5,
        )
        for line in (out.stdout or "").splitlines() + (out.stderr or "").splitlines():
            m = re.search(r'"([^"]+tessdata[^"]*)"', line)
            if m:
                p = Path(m.group(1).rstrip("/\\"))
                if p.is_dir():
                    return p
    except Exception:
        pass
    # 2) Fallback：標準路徑探測
    cand_paths = [
        Path(binary).parent / "tessdata",
        Path("/usr/local/share/tessdata"),
        Path("/usr/share/tessdata"),
        Path("/opt/homebrew/share/tessdata"),
    ]
    for p in cand_paths:
        if p.is_dir():
            return p
    # 3) Linux apt：/usr/share/tesseract-ocr/<ver>/tessdata
    for base in ("/usr/share/tesseract-ocr", "/usr/local/share/tesseract-ocr"):
        bp = Path(base)
        if bp.is_dir():
            for sub in bp.iterdir():
                if sub.is_dir() and (sub / "tessdata").is_dir():
                    return sub / "tessdata"
    return None


def get_installed_langs() -> set[str]:
    """跑 tesseract --list-langs 回實際裝的語言（過濾 osd / 空字串 / 變體 alias）。

    過濾規則：
    - osd（OSD 是 tesseract 內建的方向偵測，不是真語言）
    - 空字串、`List of...` 標頭
    - **變體 alias**：`<code>.fast` / `<code>.best`（這些是我們存的變體檔，
      tesseract 把檔名當 lang code 列出，但對 user 而言它們就是 chi_tra 的變體）
    """
    binary = _resolve_tesseract_binary()
    if not binary:
        return set()
    try:
        out = subprocess.run(
            [binary, "--list-langs"], capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return set()
    langs = set()
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of") or line == "osd":
            continue
        # 變體 alias：chi_tra.fast / chi_tra.best 等 — 不是獨立語言，跳過
        if line.endswith(".fast") or line.endswith(".best"):
            continue
        langs.add(line)
    return langs


@dataclass
class InstallResult:
    ok: bool
    code: str
    bytes_written: int = 0
    error: str = ""
    path: str = ""
    hint: Optional[dict] = None  # 平台 sudo 指令對話用


def can_write_tessdata() -> bool:
    """檢查 service user 對 tessdata 目錄是否有寫入權限。"""
    import os
    td = get_tessdata_dir()
    if not td or not td.is_dir():
        return False
    return os.access(str(td), os.W_OK)


def platform_install_hint(code: str) -> dict:
    """權限不足時，依平台給 admin 結構化指令建議。
    指令會**同時下 fast + best 兩變體**，符合 v1.7.2 設計（雙變體共存）。
    Returns {platform, message, methods: [{name, command, note}]}"""
    import platform
    sys_name = platform.system().lower()
    td = get_tessdata_dir()
    td_str = str(td) if td else "<tessdata>"
    fast_url = f"{_TESSDATA_URLS['fast']}/{code}.traineddata"
    best_url = f"{_TESSDATA_URLS['best']}/{code}.traineddata"

    if "linux" in sys_name:
        apt_pkg = code.replace("_", "-")  # chi_tra -> chi-tra
        # 一行 chained curl，下完兩變體 + 把 best 設為 active
        chained = (
            f"sudo curl -L -o {td_str}/{code}.fast.traineddata {fast_url} && "
            f"sudo curl -L -o {td_str}/{code}.best.traineddata {best_url} && "
            f"sudo cp {td_str}/{code}.best.traineddata {td_str}/{code}.traineddata"
        )
        return {
            "platform": "linux",
            "message": f"jt-doc-tools service 帳號 (jtdt) 對系統 tessdata 目錄 {td_str} 無寫入權限（Linux apt 安裝的正常狀況），請以 root / sudo 執行下面任一指令：",
            "methods": [
                {
                    "name": "方法 1：下載兩變體（推薦，fast + best 都有）",
                    "command": chained,
                    "note": "fast 變體（檔小快）+ best 變體（檔大準）都下載，預設 active 用 best",
                },
                {
                    "name": "方法 2：apt 套件（簡單但只有 fast 變體）",
                    "command": f"sudo apt install -y tesseract-ocr-{apt_pkg}",
                    "note": "走系統套件管理；只下 fast 版本，best 變體仍需另外下載",
                },
            ],
        }
    if "darwin" in sys_name:
        chained = (
            f"sudo curl -L -o {td_str}/{code}.fast.traineddata {fast_url} && "
            f"sudo curl -L -o {td_str}/{code}.best.traineddata {best_url} && "
            f"sudo cp {td_str}/{code}.best.traineddata {td_str}/{code}.traineddata"
        )
        return {
            "platform": "darwin",
            "message": f"jt-doc-tools service 對 {td_str} 無寫入權限，請開 Terminal 執行：",
            "methods": [
                {
                    "name": "方法 1：下載兩變體（fast + best）",
                    "command": chained,
                    "note": "下載後 OCR 工具自動偵測，不用重啟服務；預設 active = best",
                },
            ],
        }
    if "windows" in sys_name:
        chained = (
            f"Invoke-WebRequest '{fast_url}' -OutFile '{td_str}\\{code}.fast.traineddata' -UseBasicParsing; "
            f"Invoke-WebRequest '{best_url}' -OutFile '{td_str}\\{code}.best.traineddata' -UseBasicParsing; "
            f"Copy-Item '{td_str}\\{code}.best.traineddata' '{td_str}\\{code}.traineddata' -Force"
        )
        return {
            "platform": "windows",
            "message": f"jt-doc-tools service 對 {td_str} 無寫入權限，請「以系統管理員身分執行 PowerShell」後跑：",
            "methods": [
                {
                    "name": "方法 1：PowerShell 下載兩變體",
                    "command": chained,
                    "note": "PowerShell 5.1+ 內建；fast + best 都下，預設 active = best",
                },
            ],
        }
    return {
        "platform": "other",
        "message": f"沒有寫入權限。請以系統管理員身分把以下兩檔放至 {td_str}/： "
                   f"{code}.fast.traineddata（{fast_url}）+ {code}.best.traineddata（{best_url}）",
        "methods": [],
    }


def _download_variant(code: str, variant: str, tessdata: Path) -> tuple[bool, int, str]:
    """單純下載某個 variant 到 <tessdata>/<code>.<variant>.traineddata。
    回 (ok, bytes_written, error)。已存在 + size 正常 → 視為成功不重下。"""
    dst = _variant_path(tessdata, code, variant)
    if dst.exists() and dst.stat().st_size > 1_000_000:
        return True, dst.stat().st_size, ""
    base = _TESSDATA_URLS.get(variant)
    if not base:
        return False, 0, f"未知 variant: {variant}"
    url = f"{base}/{code}.traineddata"
    try:
        import urllib.request
        tmp = dst.with_suffix(".traineddata.part")
        urllib.request.urlretrieve(url, str(tmp))
        if not tmp.exists() or tmp.stat().st_size < 1_000_000:
            tmp.unlink(missing_ok=True)
            return False, 0, f"下載 {variant} 不完整（< 1 MB）"
        tmp.replace(dst)
        return True, dst.stat().st_size, ""
    except Exception as e:
        return False, 0, str(e)


def _set_active_variant(code: str, variant: str, tessdata: Path) -> bool:
    """把 <code>.<variant>.traineddata 內容複製到 <code>.traineddata（active 檔）。
    tesseract 統一用 lang code 找 active 檔，這層讓我們可隨時 swap variant。"""
    src = _variant_path(tessdata, code, variant)
    dst = _active_path(tessdata, code)
    if not src.exists():
        return False
    try:
        import shutil
        shutil.copy2(str(src), str(dst))
        return True
    except Exception as e:
        log.warning("set active variant %s/%s failed: %s", code, variant, e)
        return False


def detect_variant_of_active(code: str, tessdata: Path) -> str:
    """偵測現存 <code>.traineddata 是 fast 還是 best 變體。
    用檔案 size 對照 fast/best 變體實際 size 判斷。
    回 'fast' / 'best' / 'unknown'（找不到對應變體 / 單一檔 size 模糊）。"""
    active = _active_path(tessdata, code)
    if not active.exists():
        return "missing"
    asize = active.stat().st_size
    fast = _variant_path(tessdata, code, "fast")
    best = _variant_path(tessdata, code, "best")
    if fast.exists() and abs(fast.stat().st_size - asize) < 1024:
        return "fast"
    if best.exists() and abs(best.stat().st_size - asize) < 1024:
        return "best"
    # 沒 variant 檔（舊客戶單一檔狀態）— 用 size 推
    # tessdata_fast chi_tra ~12MB；best ~50MB；中間粗略 25MB 切分
    catalog_item = next((i for i in LANG_CATALOG if i["code"] == code), None)
    if catalog_item:
        fast_size_mb = catalog_item.get("size_mb", 10)
        best_size_mb = fast_size_mb * _BEST_SIZE_MULTIPLIER.get(code, 3.0)
        cutoff_mb = (fast_size_mb + best_size_mb) / 2
        return "best" if asize / 1024 / 1024 > cutoff_mb else "fast"
    return "unknown"


def install_lang(code: str, variant: Optional[str] = None,
                  download_both: bool = True) -> InstallResult:
    """安裝語言訓練檔。預設 (download_both=True) 同時下 fast + best 兩變體，
    並把 active 設為「OCR 設定的預設 quality」（admin 可改、預設 best）。

    variant: 'fast' / 'best' — 指定只下載某一變體（download_both 設 False 時用）。
    """
    if not is_valid_lang_code(code):
        return InstallResult(False, code, error="不支援的語言碼（不在白名單）")
    tessdata = get_tessdata_dir()
    if not tessdata:
        return InstallResult(False, code, error="找不到 tessdata 目錄（tesseract 未安裝？）")
    if not tessdata.is_dir():
        return InstallResult(False, code, error=f"tessdata 不是目錄：{tessdata}")
    if not can_write_tessdata():
        h = platform_install_hint(code)
        return InstallResult(False, code, error=h["message"], hint=h)

    target_variants = ["fast", "best"] if download_both else [variant or "best"]
    total_bytes = 0
    errors: list[str] = []
    for v in target_variants:
        try:
            ok, size, err = _download_variant(code, v, tessdata)
            if ok:
                total_bytes += size
            else:
                errors.append(f"{v}: {err}")
        except PermissionError:
            h = platform_install_hint(code)
            return InstallResult(False, code, error=h["message"], hint=h)

    if total_bytes == 0:
        return InstallResult(False, code, error="; ".join(errors) or "下載失敗")

    # 設 active variant 為 OCR 設定的預設品質
    default_quality = get_default_quality()
    # 若預設變體沒下到，退到另一個下到的
    if not _variant_path(tessdata, code, default_quality).exists():
        for alt in target_variants:
            if _variant_path(tessdata, code, alt).exists():
                default_quality = alt
                break
    _set_active_variant(code, default_quality, tessdata)

    note = f"已下載 {', '.join(target_variants)}；active = {default_quality}"
    if errors:
        note += f"；部分失敗: {'; '.join(errors)}"
    return InstallResult(True, code, bytes_written=total_bytes,
                         path=str(_active_path(tessdata, code)),
                         error=note if errors else "")


def switch_active_quality(code: str, quality: str) -> InstallResult:
    """切換語言的 active 變體（fast ↔ best）。對應變體必須已下載。"""
    if not is_valid_lang_code(code):
        return InstallResult(False, code, error="不支援的語言碼")
    if quality not in _TESSDATA_URLS:
        return InstallResult(False, code, error="quality 必須是 'fast' 或 'best'")
    tessdata = get_tessdata_dir()
    if not tessdata:
        return InstallResult(False, code, error="找不到 tessdata 目錄")
    if not _variant_path(tessdata, code, quality).exists():
        return InstallResult(False, code,
                             error=f"{quality} 變體尚未下載，請先安裝（會同時下 fast + best）")
    if _set_active_variant(code, quality, tessdata):
        return InstallResult(True, code, path=str(_active_path(tessdata, code)))
    return InstallResult(False, code, error="切換失敗（檔案複製錯誤）")


# ---- OCR 設定（預設 quality）----
def _ocr_settings_path() -> Path:
    try:
        from ..config import settings
        return Path(settings.data_dir) / "ocr_settings.json"
    except Exception:
        return Path("data") / "ocr_settings.json"


def get_default_quality() -> str:
    """OCR 預設 quality（fast / best）。預設 'best'。"""
    p = _ocr_settings_path()
    if not p.exists():
        return "best"
    try:
        import json as _j
        d = _j.loads(p.read_text(encoding="utf-8"))
        q = (d.get("quality_default") or "best").strip().lower()
        return q if q in _TESSDATA_URLS else "best"
    except Exception:
        return "best"


def set_default_quality(quality: str) -> bool:
    """admin 改預設 quality。檔不存在自動建立。
    額外動作：若 admin 切了 quality，把所有「兩變體都已下載」的語言的 active
    variant 自動改成新預設（user 期望「設定就生效」）。"""
    if quality not in _TESSDATA_URLS:
        return False
    p = _ocr_settings_path()
    try:
        import json as _j
        d = {}
        if p.exists():
            try:
                d = _j.loads(p.read_text(encoding="utf-8"))
            except Exception:
                d = {}
        d["quality_default"] = quality
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_j.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("set_default_quality save failed: %s", e)
        return False
    # 自動套到所有兩變體都有的 lang
    tessdata = get_tessdata_dir()
    if tessdata and tessdata.is_dir() and can_write_tessdata():
        installed = get_installed_langs()
        for code in installed:
            if not is_valid_lang_code(code):
                continue
            if _variant_path(tessdata, code, quality).exists():
                _set_active_variant(code, quality, tessdata)
    return True


def platform_uninstall_hint(code: str) -> dict:
    """權限不足時的移除指令（同 platform_install_hint 風格）。"""
    import platform
    sys_name = platform.system().lower()
    td = get_tessdata_dir()
    td_str = str(td) if td else "<tessdata>"
    if "linux" in sys_name or "darwin" in sys_name:
        rm_cmd = (
            f"sudo rm -f {td_str}/{code}.traineddata "
            f"{td_str}/{code}.fast.traineddata {td_str}/{code}.best.traineddata"
        )
        return {
            "platform": "linux" if "linux" in sys_name else "darwin",
            "message": f"jt-doc-tools service 帳號對 {td_str} 無刪除權限，請以 root / sudo 執行：",
            "methods": [{
                "name": "刪除三個檔（active + fast + best 變體）",
                "command": rm_cmd,
                "note": "三個檔可能不全存在，rm -f 會略過不存在的",
            }],
        }
    if "windows" in sys_name:
        rm_cmd = (
            f"Remove-Item -Force '{td_str}\\{code}.traineddata',"
            f"'{td_str}\\{code}.fast.traineddata','{td_str}\\{code}.best.traineddata' "
            f"-ErrorAction SilentlyContinue"
        )
        return {
            "platform": "windows",
            "message": f"jt-doc-tools service 對 {td_str} 無刪除權限，請「以系統管理員身分執行 PowerShell」後跑：",
            "methods": [{
                "name": "PowerShell 刪除三個檔",
                "command": rm_cmd,
                "note": "active + fast + best 變體一次清",
            }],
        }
    return {"platform": "other",
            "message": f"請手動從 {td_str}/ 刪除 {code}.traineddata、{code}.fast.traineddata、{code}.best.traineddata",
            "methods": []}


def uninstall_lang(code: str) -> InstallResult:
    """刪除 tessdata 內 <code> 的所有檔案（active + fast + best 變體）。
    eng / chi_tra 不可刪（核心語言）。權限不足會回 hint dict 給 admin 對話框。"""
    if not is_valid_lang_code(code):
        return InstallResult(False, code, error="不支援的語言碼")
    if code in ("eng", "chi_tra"):
        return InstallResult(False, code, error="核心語言不可移除（eng / chi_tra）")
    tessdata = get_tessdata_dir()
    if not tessdata:
        return InstallResult(False, code, error="找不到 tessdata 目錄")
    # 預檢權限 — 寫不了就直接給 admin 指令
    if not can_write_tessdata():
        h = platform_uninstall_hint(code)
        return InstallResult(False, code, error=h["message"], hint=h)
    targets = [
        _active_path(tessdata, code),
        _variant_path(tessdata, code, "fast"),
        _variant_path(tessdata, code, "best"),
    ]
    removed = []
    errors = []
    for f in targets:
        if not f.exists():
            continue
        try:
            f.unlink()
            removed.append(f.name)
        except PermissionError as e:
            errors.append(f"{f.name}: {e}")
        except Exception as e:
            errors.append(f"{f.name}: {e}")
    if not removed and not errors:
        return InstallResult(True, code, error="檔案不存在（已是未安裝狀態）")
    if errors and not removed:
        h = platform_uninstall_hint(code)
        return InstallResult(False, code, error="; ".join(errors), hint=h)
    note = f"已刪除 {len(removed)} 個檔（{', '.join(removed)})"
    if errors:
        note += f"；部分失敗: {'; '.join(errors)}"
    return InstallResult(True, code, error=note if errors else "", path=str(targets[0]))


def catalog_with_status() -> list[dict]:
    """回 catalog 每筆附上 installed + 變體 (fast / best) 細節。"""
    installed = get_installed_langs()
    tessdata = get_tessdata_dir()
    out = []
    for item in LANG_CATALOG:
        d = dict(item)
        code = item["code"]
        d["installed"] = code in installed
        d["actual_size_mb"] = 0
        d["fast_installed"] = False
        d["best_installed"] = False
        d["fast_size_mb"] = 0
        d["best_size_mb"] = 0
        d["active_variant"] = "missing"
        if tessdata:
            fast_p = _variant_path(tessdata, code, "fast")
            best_p = _variant_path(tessdata, code, "best")
            if fast_p.exists():
                d["fast_installed"] = True
                d["fast_size_mb"] = round(fast_p.stat().st_size / 1024 / 1024, 1)
            if best_p.exists():
                d["best_installed"] = True
                d["best_size_mb"] = round(best_p.stat().st_size / 1024 / 1024, 1)
            if d["installed"]:
                act = _active_path(tessdata, code)
                if act.exists():
                    d["actual_size_mb"] = round(act.stat().st_size / 1024 / 1024, 1)
                d["active_variant"] = detect_variant_of_active(code, tessdata)
        # 預估 best size（用於 UI 顯示「會多下 ~XMB」）
        d["est_best_size_mb"] = round(item["size_mb"] * _BEST_SIZE_MULTIPLIER.get(code, 3.0), 0)
        out.append(d)
    return out
