"""加密紙本備份：離線還原包（一個 HTML 檔，按兩下就能用、不需要連網、不需要 JTDT 主機）。

做法：所有需要的程式（我們的模組、內建的 Shamir 函式庫、jsQR、PDF.js 與它的背景程式）
以 Base64 放進同一個 HTML；打開時先逐一核對 SHA-256，再用 Blob 網址把模組互相接起來
（`file://` 下瀏覽器不准讀同目錄的其他檔案，所以全部內嵌）。Base64 而不是原文：
原始碼裡出現的 `</script` 或 `<!--` 會讓 HTML 解析器提早結束區塊。

不含任何使用者秘密；同一版本、同一語言產出的位元組固定（可以公開 SHA-256 讓人核對）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "static"
VECTORS = Path(__file__).resolve().parent / "vectors-v1.json"

#: 內嵌的檔案：名稱 → (路徑, 種類, 授權)。模組之間的 import 在打開時改成對方的 Blob 網址。
FILES: dict[str, tuple[str, str, str]] = {
    "csprng": ("vendor/shamir-secret-sharing-0.0.3/csprng.js", "module", "Apache-2.0"),
    "shamir": ("vendor/shamir-secret-sharing-0.0.3/index.js", "module", "Apache-2.0"),
    "codec": ("js/paper_backup/pb_codec.mjs", "module", "JTDT"),
    "core": ("js/paper_backup/pb_core.mjs", "module", "JTDT"),
    "scan": ("js/paper_backup/pb_scan.mjs", "module", "JTDT"),
    "testpage": ("js/paper_backup/pb_testpage.mjs", "module", "JTDT"),
    "restore": ("js/paper_backup/pb_restore.mjs", "module", "JTDT"),
    "offline": ("js/paper_backup/pb_offline.mjs", "module", "JTDT"),
    "jsqr": ("vendor/jsqr/jsQR.js", "script", "Apache-2.0"),
    "pdf": ("vendor/pdfjs/build/pdf.mjs", "module", "Apache-2.0"),
    "pdfworker": ("vendor/pdfjs/build/pdf.worker.mjs", "module", "Apache-2.0"),
}

#: 每個模組裡的 import 指到哪一個內嵌檔（打開時字串替換；建包時檢查不能有漏的）
IMPORTS: dict[str, dict[str, str]] = {
    "shamir": {"shamir-secret-sharing/csprng": "csprng"},
    "core": {"../../vendor/shamir-secret-sharing-0.0.3/index.js": "shamir", "./pb_codec.mjs": "codec"},
    "testpage": {"./pb_codec.mjs": "codec", "./pb_core.mjs": "core"},
    "restore": {"./pb_codec.mjs": "codec", "./pb_core.mjs": "core", "./pb_scan.mjs": "scan", "./pb_testpage.mjs": "testpage"},
    "offline": {"./pb_codec.mjs": "codec", "./pb_core.mjs": "core", "./pb_restore.mjs": "restore"},
}
#: 載入順序（被依賴的先）
ORDER = ("csprng", "shamir", "codec", "core", "scan", "testpage", "restore", "pdf", "pdfworker", "offline")

_IMPORT_RE = re.compile(r"""(?:^|\n)\s*(?:import|export)\s[^;]*?\sfrom\s*['"]([^'"]+)['"]|(?:^|\n)\s*import\s*['"]([^'"]+)['"]""")


def static_imports(src: str) -> set[str]:
    """模組裡靜態 import / re-export 的對象（動態 import() 不算）。"""
    return {a or b for a, b in _IMPORT_RE.findall(src)}


def load_files() -> dict[str, dict]:
    out = {}
    for name, (rel, kind, lic) in FILES.items():
        data = (STATIC / rel).read_bytes()
        out[name] = {"path": rel, "kind": kind, "license": lic, "size": len(data),
                     "sha256": hashlib.sha256(data).hexdigest(), "b64": base64.b64encode(data).decode("ascii")}
    return out


def check_imports(files: dict[str, dict]) -> list[str]:
    """回傳沒有對應內嵌檔的 import（空 list 才可以出包）。"""
    bad = []
    for name, f in files.items():
        if f["kind"] != "module" or name in ("pdf", "pdfworker"):
            continue
        src = base64.b64decode(f["b64"]).decode("utf-8")
        for spec in static_imports(src):
            if spec not in IMPORTS.get(name, {}):
                bad.append(f"{name}: {spec}")
    return bad


def self_test_vector() -> dict:
    v = json.loads(VECTORS.read_text(encoding="utf-8"))
    case = v["cases"][0]
    return {"main": case["main"], "shares": [case["shares"][i] for i in case["use_shares"]],
            "content_b64": case["content_b64"]}


def build(templates, request, version: str, lang: str) -> bytes:
    files = load_files()
    bad = check_imports(files)
    if bad:
        raise RuntimeError("offline pack: unmapped imports: " + ", ".join(bad))
    manifest = {name: {k: f[k] for k in ("path", "license", "size", "sha256")} for name, f in files.items()}
    html = templates.env.get_template("paper_backup_offline.html").render(
        request=request, version=version, lang=lang,
        files_json=json.dumps({n: {"sha256": f["sha256"], "kind": f["kind"], "b64": f["b64"]} for n, f in files.items()},
                              separators=(",", ":")),
        imports_json=json.dumps(IMPORTS, separators=(",", ":")),
        order_json=json.dumps(ORDER),
        vector_json=json.dumps(self_test_vector(), separators=(",", ":")),
        manifest=manifest,
    )
    return html.encode("utf-8")
