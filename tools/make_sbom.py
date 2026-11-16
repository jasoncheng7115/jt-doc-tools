#!/usr/bin/env python3
"""產生 jt-doc-tools 的 SBOM（CycloneDX 1.5，`sbom.cdx.json`，放在公開樹根目錄）。

內容：

* **Python 套件**：讀 `uv.lock`（安裝時照它裝，版本是確切的）。從專案本身的 `dependencies` 走得到的是
  `required`；只有開發 / 測試群組（`dev-dependencies`）才用到的標成 `excluded`（不隨產品安裝）。
  授權從目前環境裝好的套件中繼資料讀（`License-Expression` → `License` → 分類器），讀不到就不寫。
* **前端元件**：`static/vendor/` 裡隨產品一起發的 JavaScript（pdf.js、Fabric.js、marked、DOMPurify、jsQR、
  qrcode-generator、shamir-secret-sharing），每一個帶檔案的 SHA-256。
* **Windows 服務包裝**：WinSW（`packaging/windows/winsw.exe`，雜湊與 `app/cli.py` 寫死的那一個相同）。
* **不隨產品發、安裝時另外裝的**（OxOffice / LibreOffice、Tesseract）只寫在 `metadata.properties`。

**輸出是固定的**：不寫時間、序號由版本推出來 —— 同一版重產逐位元組相同，`tests/test_sbom.py` 會比對。

    python tools/make_sbom.py            # 寫到公開樹根目錄的 sbom.cdx.json
    python tools/make_sbom.py --stdout   # 印出來
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tomllib
import uuid
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.repo_paths import public_root  # noqa: E402

PROJECT = "jt-doc-tools"


def _version() -> str:
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', (ROOT / "app" / "main.py").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("找不到 app/main.py 的 VERSION")
    return m.group(1)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _license(name: str) -> str | None:
    """已安裝的套件中繼資料裡的授權（SPDX 運算式優先）；讀不到回 None。"""
    try:
        md = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return None
    expr = (md.get("License-Expression") or "").strip()
    if expr:
        return expr
    lic = (md.get("License") or "").strip()
    if lic and len(lic) <= 200 and "\n" not in lic:
        return lic
    for c in md.get_all("Classifier") or []:
        if c.startswith("License :: OSI Approved :: "):
            return c.split(" :: ")[-1]
    return None


def _lic_entry(lic: str) -> dict:
    # 一個 SPDX ID（字母、數字、. -）用 id，其他（運算式或自由文字）用 expression / name
    if re.fullmatch(r"[A-Za-z0-9.+-]+", lic):
        return {"license": {"id": lic}} if lic in _SPDX_COMMON else {"license": {"name": lic}}
    if re.fullmatch(r"[A-Za-z0-9.+ ()-]+", lic) and re.search(r"\b(AND|OR|WITH)\b", lic):
        return {"expression": lic}
    return {"license": {"name": lic[:120]}}


_SPDX_COMMON = {
    "MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "MPL-2.0", "PSF-2.0", "LGPL-2.1-only",
    "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "GPL-2.0-only", "GPL-2.0-or-later",
    "GPL-3.0-only", "GPL-3.0-or-later", "AGPL-3.0-only", "AGPL-3.0-or-later", "Unlicense", "Zlib",
    "Python-2.0", "HPND", "0BSD", "CC0-1.0", "BSL-1.0",
}


def python_components() -> tuple[list[dict], list[dict], str]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    pkgs = lock.get("package", [])
    root = next(p for p in pkgs if _norm(p["name"]) == PROJECT)
    by_name: dict[str, list[dict]] = {}
    for p in pkgs:
        by_name.setdefault(_norm(p["name"]), []).append(p)

    def ref(p: dict) -> str:
        return f"pkg:pypi/{_norm(p['name'])}@{p['version']}"

    def targets(dep: dict) -> list[dict]:
        cands = by_name.get(_norm(dep["name"]), [])
        if "version" in dep:
            cands = [c for c in cands if c["version"] == dep["version"]] or cands
        return cands

    # 從專案本身的 dependencies 走得到的＝隨產品安裝
    required: set[str] = set()
    stack = [d for d in root.get("dependencies", [])]
    while stack:
        d = stack.pop()
        for p in targets(d):
            r = ref(p)
            if r in required:
                continue
            required.add(r)
            stack.extend(p.get("dependencies", []))
            for extra_deps in (p.get("optional-dependencies") or {}).values():
                stack.extend(extra_deps)

    comps, deps = [], []
    for p in sorted(pkgs, key=lambda x: (_norm(x["name"]), x["version"])):
        if p is root:
            continue
        r = ref(p)
        c = {"type": "library", "bom-ref": r, "name": _norm(p["name"]), "version": p["version"], "purl": r,
             "scope": "required" if r in required else "excluded"}
        lic = _license(p["name"])
        if lic:
            c["licenses"] = [_lic_entry(lic)]
        sdist = (p.get("sdist") or {}).get("hash", "")
        if sdist.startswith("sha256:"):
            c["hashes"] = [{"alg": "SHA-256", "content": sdist.split(":", 1)[1]}]
            c["properties"] = [{"name": "jt-doc-tools:hash-of", "value": "sdist（uv.lock 另外鎖住每一個 wheel 的雜湊）"}]
        comps.append(c)
        on = sorted({ref(t) for d in p.get("dependencies", []) for t in targets(d)})
        deps.append({"ref": r, "dependsOn": on})
    root_on = sorted({ref(t) for d in root.get("dependencies", []) for t in targets(d)})
    return comps, deps, ",".join(root_on)


def _vendor(name, version, lic, path, purl=None, site=None) -> dict:
    files = [path] if path.is_file() else sorted(f for f in path.rglob("*") if f.is_file())
    files = [f for f in files if f.suffix in (".js", ".mjs", ".css", ".exe")]
    c = {"type": "library", "bom-ref": purl or f"jt-doc-tools-vendor:{name}", "name": name, "scope": "required",
         "licenses": [{"license": {"id": lic}} if lic in _SPDX_COMMON else {"expression": lic}]}
    if version:
        c["version"] = version
    if purl:
        c["purl"] = purl
    if len(files) == 1:
        c["hashes"] = [{"alg": "SHA-256", "content": _sha256(files[0])}]
    c["properties"] = [{"name": "jt-doc-tools:location", "value": str((files[0] if len(files) == 1 else path).relative_to(ROOT))}]
    if site:
        c["externalReferences"] = [{"type": "website", "url": site}]
    return c


def _head_version(p: Path, pat: str) -> str | None:
    m = re.search(pat, p.read_text(encoding="utf-8", errors="replace"))
    return m.group(1) if m else None


def vendor_components() -> list[dict]:
    v = ROOT / "static" / "vendor"
    out = []
    pdfjs_ver = _head_version(v / "pdfjs" / "build" / "pdf.mjs", r'const version = "([0-9.]+)"') \
        if (v / "pdfjs" / "build" / "pdf.mjs").exists() else None
    if pdfjs_ver:
        out.append(_vendor("pdfjs-dist", pdfjs_ver, "Apache-2.0", v / "pdfjs", f"pkg:npm/pdfjs-dist@{pdfjs_ver}",
                           "https://github.com/mozilla/pdf.js"))
    fab = _head_version(v / "fabric" / "fabric.min.js", r'version:"([0-9.]+)"')
    out.append(_vendor("fabric", fab, "MIT", v / "fabric" / "fabric.min.js", fab and f"pkg:npm/fabric@{fab}",
                       "https://github.com/fabricjs/fabric.js"))
    mk = _head_version(v / "marked" / "marked.min.js", r"marked v([0-9.]+)")
    out.append(_vendor("marked", mk, "MIT", v / "marked" / "marked.min.js", mk and f"pkg:npm/marked@{mk}",
                       "https://github.com/markedjs/marked"))
    pur = _head_version(v / "marked" / "purify.min.js", r"DOMPurify ([0-9.]+)")
    out.append(_vendor("dompurify", pur, "MPL-2.0 OR Apache-2.0", v / "marked" / "purify.min.js",
                       pur and f"pkg:npm/dompurify@{pur}", "https://github.com/cure53/DOMPurify"))
    # jsQR 的檔案裡沒有版本號，靠雜湊辨識
    out.append(_vendor("jsqr", None, "Apache-2.0", v / "jsqr" / "jsQR.js", None, "https://github.com/cozmo/jsQR"))
    pb = json.loads((v / "paper-backup-vendor.json").read_text(encoding="utf-8"))
    for p in pb.get("packages", []):
        d = v / f"{p['name']}-{p['version']}"
        c = _vendor(p["name"], p["version"], p["license"], d, f"pkg:npm/{p['name']}@{p['version']}", p.get("source"))
        c["hashes"] = [{"alg": "SHA-256", "content": h} for f, h in sorted(p.get("files", {}).items())
                       if f.endswith((".js", ".mjs"))]
        out.append(c)
    m = re.search(r'_WINSW_BUNDLED_SHA256 = \(\s*"([0-9a-f]{64})"', (ROOT / "app" / "cli.py").read_text(encoding="utf-8"))
    if m:
        out.append({"type": "application", "bom-ref": "pkg:github/winsw/winsw@v2.12.0", "name": "winsw",
                    "version": "2.12.0", "purl": "pkg:github/winsw/winsw@v2.12.0", "scope": "required",
                    "licenses": [{"license": {"id": "MIT"}}],
                    "hashes": [{"alg": "SHA-256", "content": m.group(1)}],
                    "properties": [{"name": "jt-doc-tools:location",
                                    "value": "packaging/windows/winsw.exe（只有 Windows 安裝）"}]})
    return out


def build() -> dict:
    ver = _version()
    py, deps, root_on = python_components()
    vend = vendor_components()
    me = f"pkg:github/jasoncheng7115/jt-doc-tools@{ver}"
    deps.insert(0, {"ref": me, "dependsOn": sorted(root_on.split(",") + [c["bom-ref"] for c in vend])})
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/jasoncheng7115/jt-doc-tools@{ver}")),
        "version": 1,
        "metadata": {
            "component": {"type": "application", "bom-ref": me, "name": PROJECT, "version": ver, "purl": me,
                          "licenses": [{"license": {"id": "AGPL-3.0-or-later"}}],
                          "externalReferences": [{"type": "vcs", "url": "https://github.com/jasoncheng7115/jt-doc-tools"}]},
            "tools": {"components": [{"type": "application", "name": "tools/make_sbom.py"}]},
            "properties": [
                {"name": "jt-doc-tools:python", "value": "CPython 3.11+（Linux 安裝用安裝目錄裡的 3.12）"},
                {"name": "jt-doc-tools:not-bundled",
                 "value": "OxOffice 或 LibreOffice、Tesseract OCR：安裝時另外安裝（系統套件），不包含在本清單"},
                {"name": "jt-doc-tools:source-of-truth",
                 "value": "uv.lock（Python 套件的確切版本與每個檔案的雜湊）、static/vendor/（前端元件）"},
            ],
        },
        "components": py + vend,
        "dependencies": deps,
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--stdout" in argv:
        sys.stdout.write(text)
        return 0
    dst = public_root(ROOT) / "sbom.cdx.json"
    dst.write_text(text, encoding="utf-8")
    n = json.loads(text)["components"]
    print(f"{dst}：{len(n)} 個元件（Python {sum(1 for c in n if c['bom-ref'].startswith('pkg:pypi'))}、"
          f"其他 {sum(1 for c in n if not c['bom-ref'].startswith('pkg:pypi'))}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
