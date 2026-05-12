"""Inline-JS syntax check for every Jinja2 template (v1.7.14).

Why: Python's pytest collection catches Python SyntaxErrors immediately, but
HTML templates with `<script>...</script>` blocks have no such guard. v1.7.14
慘案: an extra `}` in `pdf_editor.html` shipped to .30 / .154 → console
"Unexpected token 'finally'" → savePdf undefined → drag-drop dead.

Approach: extract every inline `<script>` block (NOT `<script src=...>`) from
every `*.html` under `app/`, hand to `node --check`. The check is fast
(<200 ms total) and catches syntax errors without needing a real browser.

Skipped when `node` is not installed (CI / minimal dev shells). On the
maintainer's box it MUST exist; this test is the second line of defence.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_GLOBS = ("app/**/templates/**/*.html",)

# Inline <script> with no src= attribute. Greedy across newlines.
_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
# Tag a Jinja directive — used to skip mostly-Jinja blocks; node can't parse Jinja.
_JINJA_RE = re.compile(r"\{%[-]?\s*\w|\{\{")


def _node_available() -> bool:
    return shutil.which("node") is not None


def _collect_templates() -> list[Path]:
    out: list[Path] = []
    for pattern in TEMPLATE_GLOBS:
        out.extend(REPO_ROOT.glob(pattern))
    return sorted(out)


@pytest.mark.skipif(not _node_available(), reason="node not installed; cannot syntax-check inline JS")
@pytest.mark.parametrize("template", _collect_templates(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_inline_js_syntactically_valid(template: Path, tmp_path: Path):
    text = template.read_text(encoding="utf-8")
    blocks = _SCRIPT_RE.findall(text)
    if not blocks:
        return  # no inline JS, nothing to check
    # Concat all inline scripts so reused vars across blocks don't trip the
    # checker. Wrap each block in `{}` so top-level `let`/`const` from one
    # block don't shadow another (templates often re-declare via guards).
    js_parts = []
    for i, blk in enumerate(blocks):
        # Substitute Jinja expressions / blocks with safe placeholders so
        # node sees valid JS literals. Common patterns:
        #   {{ var }}            → null
        #   {{ var|tojson }}     → null
        #   {% if x %}...{% endif %} → strip control tags, keep body
        #   {% for x in ys %}    → strip
        s = blk
        # Strip Jinja control tags, preserve body
        s = re.sub(r"\{%[-]?\s*(?:end\w+|else|elif[^%]*)\s*[-]?%\}", "", s)
        s = re.sub(r"\{%[-]?\s*\w[^%]*[-]?%\}", "", s)
        # Replace Jinja expressions with `null` (valid JS literal)
        s = re.sub(r"\{\{[^}]*\}\}", "null", s)
        js_parts.append(f"// --- block {i} from {template.name} ---\n{{\n{s}\n}}")
    js = "\n".join(js_parts)
    js_file = tmp_path / "_check.js"
    js_file.write_text(js, encoding="utf-8")
    proc = subprocess.run(
        ["node", "--check", str(js_file)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        # Surface the relevant error tail; node prints filename:line so user
        # can map back via the `// --- block N from ...` markers in js_file.
        raise AssertionError(
            f"Inline JS syntax error in {template.relative_to(REPO_ROOT)}:\n"
            f"{proc.stderr or proc.stdout}\n"
            f"(temp file: {js_file})"
        )
