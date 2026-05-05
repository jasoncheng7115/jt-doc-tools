"""企業 logo / 識別 — admin 可上傳一張企業 logo 取代預設 sidebar logo、
favicon、首頁 logo。

實作上只存一張原始上傳檔（`data/branding/logo.png`，PNG / JPEG 都正規化成
PNG），各位置（sidebar、favicon、landing）都讀同一張。SVG 不支援，因為
favicon 與 PDF 內嵌情境都需要 raster 後備。

`/branding/logo` 是公開 endpoint（**不**需登入），這樣 base.html 在登入頁
也讀得到企業 logo（避免登入頁顯示預設 logo）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image

from ..config import settings


# 上傳上限：5 MB（一般企業 logo 不會超過 1 MB；給 5 MB 餘裕）
MAX_LOGO_BYTES = 5 * 1024 * 1024
# 上傳的原始圖會 resize 到最長邊不超過這個 px，避免使用者上傳 4K 圖
# 拖累每個頁面載入速度。256 px 對 sidebar / favicon 都綽綽有餘。
MAX_LOGO_DIMENSION = 256
# 允許的 MIME / 檔名 extension（白名單）
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _logo_path() -> Path:
    return settings.branding_dir / "logo.png"


def has_custom_logo() -> bool:
    p = _logo_path()
    return p.exists() and p.stat().st_size > 0


def get_custom_logo_path() -> Optional[Path]:
    """Return the on-disk path to the custom logo, or None if not set."""
    p = _logo_path()
    return p if p.exists() and p.stat().st_size > 0 else None


def save_logo(data: bytes, original_filename: str = "") -> None:
    """Validate + normalize uploaded image to PNG, save to branding_dir.

    Raises ValueError on invalid input — caller maps to HTTP 400.
    """
    if not data:
        raise ValueError("上傳檔為空")
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(
            f"檔案過大（{len(data)/1024/1024:.1f} MB > 上限 "
            f"{MAX_LOGO_BYTES/1024/1024:.0f} MB）")
    ext = Path(original_filename or "").suffix.lower()
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支援的副檔名：{ext}（僅支援 PNG / JPG / WEBP）")
    # Decode + validate via PIL — also rejects malformed images and any
    # non-image payload that slipped through extension check.
    from io import BytesIO
    try:
        with Image.open(BytesIO(data)) as im:
            im.load()
            # Always convert to RGBA so PNG output preserves transparency
            # if the source had it (logo.png with alpha is the common case).
            im = im.convert("RGBA")
            # Down-scale large uploads — keep aspect ratio.
            if max(im.width, im.height) > MAX_LOGO_DIMENSION:
                im.thumbnail((MAX_LOGO_DIMENSION, MAX_LOGO_DIMENSION),
                             Image.Resampling.LANCZOS)
            settings.branding_dir.mkdir(parents=True, exist_ok=True)
            im.save(_logo_path(), "PNG", optimize=True)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"無法解析圖片：{e}")


def reset_logo() -> bool:
    """Remove custom logo, falling back to bundled default. Returns True
    if a custom logo was actually present and removed."""
    p = _logo_path()
    if p.exists():
        p.unlink()
        return True
    return False


def custom_logo_url() -> str:
    """URL used in templates. Returns the public endpoint when a custom
    logo exists, else empty string (templates fall back to the bundled
    default in app/static/images/)."""
    return "/branding/logo" if has_custom_logo() else ""


# ----- 站台名稱 customization (v1.4.68 起) -----
# 客戶可改「Jason Tools 文件工具箱」→「<某某公司> 文件工具箱」之類自家品牌。
# 改完之後出現在 sidebar 上方、瀏覽器頁籤 title、首頁 hero、login 頁。
# Apache 2.0 license 允許 white-label，這只是內建讓不會改 source 的客戶也能改。
_MAX_SITE_NAME_LEN = 60


def _site_name_path() -> Path:
    return settings.branding_dir / "site_name.txt"


def get_site_name(default: str = "") -> str:
    """Return custom site name if set, else `default` (typically settings.app_name).
    讀檔每次 request，不快取 — 改完立即生效，不用 restart。檔案不存在或
    空白回 default。"""
    p = _site_name_path()
    try:
        if p.exists():
            txt = p.read_text(encoding="utf-8").strip()
            if txt:
                return txt[:_MAX_SITE_NAME_LEN]
    except Exception:
        pass
    return default


def set_site_name(name: str) -> None:
    """Save custom site name. Empty / whitespace → reset to default
    (delete the file). Raises ValueError on too-long input."""
    name = (name or "").strip()
    if not name:
        # Reset path
        try:
            _site_name_path().unlink(missing_ok=True)
        except Exception:
            pass
        return
    if len(name) > _MAX_SITE_NAME_LEN:
        raise ValueError(f"站台名稱不得超過 {_MAX_SITE_NAME_LEN} 字元（目前 {len(name)} 字元）")
    settings.branding_dir.mkdir(parents=True, exist_ok=True)
    _site_name_path().write_text(name, encoding="utf-8")


def has_custom_site_name() -> bool:
    return _site_name_path().exists() and _site_name_path().stat().st_size > 0
