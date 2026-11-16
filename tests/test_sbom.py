"""SBOM（`sbom.cdx.json`，CycloneDX 1.5）：每一版都要重產（使用者 2026-11-16：「以後每一版都要產 sbom」）。

判準不是「檔案在不在」，而是**內容跟現在的 `uv.lock` / `static/vendor/` 一致**：升了版號、加了相依、
換了前端元件卻沒重產，這裡就紅。授權欄是從當下環境裝好的套件讀的（CI 與開發機裝的不一定一樣），
比對時略過；其他欄位（名稱、版本、purl、雜湊、隨產品安裝與否、相依關係）一律要一樣。
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from tools import make_sbom
from tools.repo_paths import public_root

ROOT = Path(__file__).resolve().parent.parent
SBOM = public_root(ROOT) / "sbom.cdx.json"


@pytest.fixture(scope="module")
def doc():
    assert SBOM.exists(), "沒有 sbom.cdx.json —— 跑 python tools/make_sbom.py"
    return json.loads(SBOM.read_text(encoding="utf-8"))


def _strip(components):
    return [{k: v for k, v in c.items() if k != "licenses"} for c in components]


def test_shape_is_cyclonedx_1_5(doc):
    assert doc["bomFormat"] == "CycloneDX" and doc["specVersion"] == "1.5" and doc["version"] == 1
    assert re.fullmatch(r"urn:uuid:[0-9a-f-]{36}", doc["serialNumber"])
    refs = [c["bom-ref"] for c in doc["components"]]
    assert len(refs) == len(set(refs)), "bom-ref 不可以重複"
    known = set(refs) | {doc["metadata"]["component"]["bom-ref"]}
    for d in doc["dependencies"]:
        assert d["ref"] in known and set(d["dependsOn"]) <= known, d["ref"]
    assert doc["metadata"]["component"]["licenses"] == [{"license": {"id": "AGPL-3.0-or-later"}}]


def test_version_matches_this_release(doc):
    """升版號時沒重產 SBOM → 紅（`tools/check_version_consistency.py` 也會抓）。"""
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', (ROOT / "app/main.py").read_text(encoding="utf-8"), re.M)
    assert doc["metadata"]["component"]["version"] == m.group(1), "版號升了但 SBOM 沒重產：python tools/make_sbom.py"


def test_content_matches_uv_lock_and_vendor(doc):
    fresh = make_sbom.build()
    assert _strip(doc["components"]) == _strip(fresh["components"]), \
        "SBOM 跟現在的 uv.lock / static/vendor 對不上（加了相依或換了元件沒重產）：python tools/make_sbom.py"
    assert doc["dependencies"] == fresh["dependencies"]
    assert doc["serialNumber"] == fresh["serialNumber"]


def test_every_locked_python_package_is_listed(doc):
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    want = {(make_sbom._norm(p["name"]), p["version"]) for p in lock["package"] if p["name"] != "jt-doc-tools"}
    got = {(c["name"], c["version"]) for c in doc["components"] if c["bom-ref"].startswith("pkg:pypi/")}
    assert got == want


def test_scope_separates_shipped_from_dev_only(doc):
    scope = {c["name"]: c["scope"] for c in doc["components"]}
    for name in ("fastapi", "pymupdf", "pillow"):
        assert scope[name] == "required", name
    assert scope["pytest"] == "excluded", "只有測試用的不隨產品安裝"


def test_vendored_frontend_components_have_hashes(doc):
    vend = {c["name"]: c for c in doc["components"] if not c["bom-ref"].startswith("pkg:pypi/")}
    for name in ("pdfjs-dist", "fabric", "marked", "dompurify", "jsqr", "qrcode-generator", "shamir-secret-sharing",
                 "winsw"):
        assert name in vend, name
    for name in ("fabric", "marked", "dompurify", "jsqr", "winsw"):
        assert vend[name]["hashes"][0]["alg"] == "SHA-256" and len(vend[name]["hashes"][0]["content"]) == 64, name
    assert vend["pdfjs-dist"]["version"] and vend["marked"]["version"]


def test_regenerating_is_byte_identical_apart_from_licenses():
    """同一版重產結果固定（沒有時間戳、序號由版本推出）—— 否則上面那條比對永遠會紅。"""
    a, b = make_sbom.render(), make_sbom.render()
    assert a == b
