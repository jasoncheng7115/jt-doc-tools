"""三份相依宣告必須互相對得上（外部稽核 F12，v1.15.30）。

我們有三份宣告，**三條不同的安裝路徑各讀其中一份**：

| 檔案 | 誰讀它 |
|---|---|
| `pyproject.toml` | `uv sync`（`jtdt update` 走這條）|
| `uv.lock` | 同上 —— 決定**實際裝到哪個版本** |
| `requirements.txt` | **CI** 與沒有 uv 的環境（`pip install -r`）|

**CI 綠不代表正式機會好**：CI 用 `pip install -r requirements.txt`（版本範圍），
正式更新走 `uv sync`（lockfile 的固定版本）—— 兩條路解析出來的完整相依不一定
相同（CLAUDE.md 記過：uv.lock 鎖 1.3.1、CI 解析到 1.6.0）。

這支測試不需要 uv 執行檔，純比對檔案內容，所以在開發機與 CI 上都會跑。
"""
from __future__ import annotations

import pathlib
import re
import tomllib

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _pyproject_deps() -> dict[str, Requirement]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out = {}
    for raw in data["project"]["dependencies"]:
        r = Requirement(raw)
        out[r.name.lower().replace("_", "-")] = r
    return out


def _requirements() -> dict[str, Requirement]:
    out = {}
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # **行內註解要先拿掉** —— pip 支援 `pkg>=1,<2   # 說明`，而
        # `packaging.Requirement` 不支援。這條測試第一次跑就因此誤報
        # 「pillow-heif 不在 requirements.txt」（它其實在第 24 行）。
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line:
            continue
        try:
            r = Requirement(line)
        except Exception:            # noqa: BLE001 — 例如 `--index-url` 這種行
            continue
        out[r.name.lower().replace("_", "-")] = r
    return out


def _locked() -> dict[str, str]:
    data = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {p["name"].lower().replace("_", "-"): p.get("version", "")
            for p in data.get("package", [])}


def test_every_declared_dependency_is_also_in_requirements():
    """`pyproject.toml` 加了新相依，`requirements.txt` 一定要跟上。

    漏了的話：**CI 與 pip 安裝的機器上少那個套件**，而工具註冊表只記一行
    ERROR 然後跳過 —— 服務照常起、healthz 照樣 200，使用者只發現「工具不見了」
    （`defusedxml` 就是這樣漏了好幾版）。
    """
    py, req = _pyproject_deps(), _requirements()
    missing = sorted(set(py) - set(req))
    assert not missing, (
        f"`pyproject.toml` 有、`requirements.txt` 沒有：{missing}\n"
        "加新相依要改六處（見 CLAUDE.md 的 SOP）。")


def test_requirements_has_no_packages_we_no_longer_declare():
    """反向：移除相依之後 `requirements.txt` 留著孤兒，CI 會繼續裝一個
    正式機不裝的套件 —— 於是 CI 測得到的東西正式機沒有。"""
    py, req = _pyproject_deps(), _requirements()
    # pip 沒有 extras 語法的等價寫法時，requirements 可能多列傳遞相依；
    # 只擋「明顯是我們自己曾經宣告過又拿掉」的情況：兩邊名字都有版本範圍。
    extra = sorted(set(req) - set(py))
    allowed: set[str] = set()      # 目前沒有合理的孤兒；有的話寫進來並註明理由
    assert not (set(extra) - allowed), (
        f"`requirements.txt` 有、`pyproject.toml` 沒有：{extra}\n"
        "要嘛補回 pyproject（正式機才會裝），要嘛從 requirements 移除。")


def test_the_locked_version_satisfies_both_declarations():
    """lockfile 實際鎖的版本必須同時落在兩份宣告的範圍內。

    不然「CI 裝到的」與「正式機裝到的」是不同版本 —— 那正是 CI 綠、
    正式機炸的典型成因。
    """
    py, req, lock = _pyproject_deps(), _requirements(), _locked()
    bad = []
    for name, r in py.items():
        v = lock.get(name)
        if not v:
            bad.append(f"{name}: uv.lock 裡沒有這個套件")
            continue
        try:
            ver = Version(v)
        except Exception:            # noqa: BLE001
            continue
        if r.specifier and not r.specifier.contains(ver, prereleases=True):
            bad.append(f"{name}: lock 鎖 {v}，但 pyproject 要求 {r.specifier}")
        rr = req.get(name)
        if rr and rr.specifier and not rr.specifier.contains(ver, prereleases=True):
            bad.append(f"{name}: lock 鎖 {v}，但 requirements.txt 要求 {rr.specifier}")
    assert not bad, "相依宣告互相矛盾：\n  " + "\n  ".join(bad)


def test_ci_and_production_install_the_same_way_or_say_why():
    """CI 用哪一份宣告要寫清楚。

    目前 CI 走 `pip install -r requirements.txt`（快、不必裝 uv），
    而正式機走 `uv sync`。上面三條測試是為了讓這個差異**不會變成版本漂移**；
    這一條只是確認 CI 的檔案裡有寫下這件事，下一個人才知道為什麼要有這些
    對照檢查。
    """
    from tools.repo_paths import public_root
    wf = public_root(ROOT) / ".github" / "workflows" / "tests.yml"
    if not wf.exists():
        pytest.skip("公開樹沒有 CI 設定")
    text = wf.read_text(encoding="utf-8")
    assert "requirements.txt" in text
    assert "uv sync" in text or "uv.lock" in text or "lockfile" in text, (
        "CI 設定裡沒有任何一句說明「這裡用 requirements.txt、正式機用 uv sync」"
        "—— 請在安裝相依那一步加上註解，並指向 "
        "tests/test_dependency_declarations_agree.py")
