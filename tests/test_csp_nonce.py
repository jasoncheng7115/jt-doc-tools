"""CSP nonce 靜態回歸測試（Phase 1：script-src 移除 'unsafe-inline'）。

確保：
  ① 每個可執行的 inline <script>（無 src）都帶 nonce — 否則 strict CSP 會擋掉。
  ② <script nonce="{{ ... }}"> 不可被包在 {% raw %} 內 — 否則 Jinja 不渲染 nonce，
     輸出字面 {{ }}（無效 nonce）→ CSP 擋掉整段（v1.12.28 踩過 markdown-to-doc
     / pdf-to-markdown / admin_ocr_langs）。
  ③ 模板無 inline 事件處理器（onclick= 等，strict CSP 不涵蓋，須 addEventListener）。
"""
import re
from pathlib import Path

import pytest

_TPL_DIRS = ["app/web/templates", "app/admin/templates"]
_ROOT = Path(__file__).resolve().parent.parent
_TPL_DIRS += [str(p.relative_to(_ROOT)) for p in (_ROOT / "app/tools").glob("*/templates")]

_HTML = [p for d in _TPL_DIRS for p in (_ROOT / d).rglob("*.html")]

_SCRIPT_OPEN = re.compile(r"<script\b([^>]*)>")
_RAW_BLOCK = re.compile(r"\{%-?\s*raw\s*-?%\}(.*?)\{%-?\s*endraw\s*-?%\}", re.S)
_INLINE_HANDLER = re.compile(
    r"\bon(click|submit|change|input|load|error|keyup|keydown|"
    r"mouseover|mouseout|focus|blur|dragover|drop|paste)\s*=", re.I)


@pytest.mark.parametrize("path", _HTML, ids=lambda p: str(p.relative_to(_ROOT)))
def test_inline_scripts_have_nonce(path):
    """可執行 inline <script>（無 src、非 JSON 資料）必須帶 nonce。"""
    s = path.read_text(encoding="utf-8")
    for m in _SCRIPT_OPEN.finditer(s):
        attrs = m.group(1)
        if "src=" in attrs:
            continue
        if 'type="application/json"' in attrs or "type='application/json'" in attrs:
            continue
        assert "nonce=" in attrs, (
            f"{path.name}: inline <script{attrs}> 缺 nonce → strict CSP 會擋")


@pytest.mark.parametrize("path", _HTML, ids=lambda p: str(p.relative_to(_ROOT)))
def test_no_nonce_script_inside_raw(path):
    """<script nonce=...> 不可在 {% raw %} 內（Jinja 不渲染 → 無效 nonce）。"""
    s = path.read_text(encoding="utf-8")
    for raw in _RAW_BLOCK.finditer(s):
        assert "<script nonce" not in raw.group(1), (
            f"{path.name}: <script nonce> 被包在 {{% raw %}} 內 → nonce 不會渲染")


@pytest.mark.parametrize("path", _HTML, ids=lambda p: str(p.relative_to(_ROOT)))
def test_no_inline_event_handlers(path):
    """模板不可有 inline 事件處理器（strict CSP 不涵蓋，須改 addEventListener）。"""
    s = path.read_text(encoding="utf-8")
    # 排除 .onclick= 之類的 JS 屬性指派（不是 HTML 屬性）
    hits = [m.group(0) for m in _INLINE_HANDLER.finditer(s)
            if not s[max(0, m.start() - 1)] in (".", "_")]
    assert not hits, f"{path.name}: 殘留 inline 事件處理器 {hits[:3]} → 改 addEventListener"
