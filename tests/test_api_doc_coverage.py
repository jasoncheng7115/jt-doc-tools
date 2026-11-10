"""每個工具的 API 都要在 `github/API.md` 與 `TEST_PLAN.md` §4 出現。

## 為什麼要有這一份

既有的 `test_api_doc_contract.py` 驗的是**文件寫的端點行為對不對**（文件 → 程式）。
它抓不到反方向：**程式有 API 但文件沒寫**。v1.14.20 核對時就是這樣發現
`markdown-to-doc` / `pdf-to-markdown` / `transit-proof` 三支從來沒進過 API.md，
而 TEST_PLAN §4 少了七支（含當輪三支新工具）。

文件漏一支不會有任何錯誤訊息 —— 只有照文件串接的人會發現「你們這支沒有 API」，
而實際上是有的。所以這件事必須由測試守。

## 判定方式

以**實際路由表**為基準（不是靠人維護的清單），比對兩份文件。工具的 API 端點命名
不完全一致（多數是 `/api/<tool-id>`，轉檔類是 `/convert`，`submission-check` 是
`/api/self-entities`），所以只要求「該工具的某個 `/api/` 或 `/convert` 路徑有出現在
文件裡」，不強求端點名稱。
"""
from __future__ import annotations

import pathlib
import re

import pytest

from tools.route_index import iter_routes, assert_sane

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
from tools.repo_paths import public_root as _public_root

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 工具頁面本身沒有對外 API 的例外。加進來要寫清楚為什麼。
#: 沒有「工具前綴底下的 API」但**確實有對外 API** 的工具 —— 要寫下它在哪裡。
_NO_PUBLIC_API: dict[str, str] = {
    # 它的對外 API 是**全站層級**的 `POST /api/convert-to-pdf`（API.md 有寫），
    # 不在 `/tools/office-to-pdf/` 底下 —— 那條路在有這個工具之前就存在了。
    "office-to-pdf": "走全站的 POST /api/convert-to-pdf",
    # 建立與還原刻意只在瀏覽器裡做（主機不碰明文、金鑰與分片；使用者 2026-11-09 選方案 C）——
    # 對外的只有不含秘密的：GET /paper-backup/offline（離線還原包）與 POST /tools/paper-backup/audit。
    "paper-backup": "加解密只在瀏覽器裡；伺服器沒有收秘密的端點（方案 C）",
}


def _tool_api_paths() -> dict[str, set[str]]:
    """每個工具實際存在的對外 API 路徑。"""
    from app.main import app

    out: dict[str, set[str]] = {}
    assert_sane(app)
    for route in iter_routes(app):
        path = getattr(route, "path", "")
        m = re.match(r"^/tools/([a-z0-9-]+)/(api/[a-z0-9-{}_]+|convert)$", path)
        if m:
            out.setdefault(m.group(1), set()).add(path)
    return out


@pytest.fixture(scope="module")
def tool_apis() -> dict[str, set[str]]:
    apis = _tool_api_paths()
    assert len(apis) > 30, f"路由表只掃到 {len(apis)} 個工具，比對基準本身就不對"
    return apis


def test_every_tool_has_a_public_api():
    """**每一支工具都要有對外 API**（本專案的長期規則：功能不可以只有網頁表單）。

    ⚠ 下面那幾條檢查**看不到這件事**：它們走的是「有 `/api/` 路徑的工具」
    這份字典，所以**一支零個 API 的工具根本不會進到字典裡** ——
    它不是不合格，它是不存在。2026-09-23 就這樣漏掉
    「會議錄音轉逐字稿」（v1.15.94 加進來，四個版本之後才被使用者問出來）。

    「掃 0 個檔」跟「掃過都乾淨」在 pytest 的輸出裡長得一模一樣，第 N 次。
    """
    from app.tool_registry import discover_tools

    tools = {t.metadata.id for t in discover_tools()}
    have = set(_tool_api_paths())
    missing = sorted(tools - have - set(_NO_PUBLIC_API))
    assert not missing, (
        f"這幾支工具沒有對外 API：{missing}。"
        "每支工具都要有 `/tools/<id>/api/<something>`（或 `/convert`）——"
        "只有網頁表單的話，外部系統接不上去。"
        "真的不該有的請加進 `_NO_PUBLIC_API` 並寫下理由。"
    )


def _missing_from(text: str, tool_apis: dict[str, set[str]]) -> list[str]:
    missing = []
    for tool, paths in tool_apis.items():
        if tool in _NO_PUBLIC_API:
            continue
        # 路徑含 {entity_id} 這種樣板參數時，比對到參數前為止就好
        if not any(p.split("{")[0] in text for p in paths):
            missing.append(tool)
    return sorted(missing)


def test_every_tool_api_is_in_api_md(tool_apis):
    """API.md 漏寫 = 照文件串接的人會以為這支沒有 API。"""
    text = (_public_root(ROOT) / "API.md").read_text(encoding="utf-8")
    missing = _missing_from(text, tool_apis)
    assert not missing, (
        f"這些工具有 API 但 github/API.md 沒寫：{missing}\n"
        "補完後記得重跑 `python3 github/build-api-page.py` 同步 api.html")


def test_every_tool_api_is_in_test_plan(tool_apis):
    """TEST_PLAN §4 漏列 = 發版抽測永遠不會測到那支。"""
    text = (ROOT / "TEST_PLAN.md").read_text(encoding="utf-8")
    missing = _missing_from(text, tool_apis)
    assert not missing, f"這些工具有 API 但 TEST_PLAN.md §4 沒列：{missing}"


def test_api_md_does_not_promise_endpoints_that_do_not_exist(tool_apis):
    """反過來：文件寫了但程式沒有 —— 照著呼叫會 404。"""
    text = (_public_root(ROOT) / "API.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"/tools/([a-z0-9-]+)/(?:api/|convert)", text))
    ghost = sorted(documented - set(tool_apis))
    assert not ghost, f"API.md 寫了這些工具的端點但實際不存在：{ghost}"


def test_tool_count_in_test_plan_is_current(tool_apis):
    """§4 標題那個數字會過期（v1.14.20 時還停在 37，實際 45）。"""
    from app.tool_registry import discover_tools

    actual = len(discover_tools())
    text = (ROOT / "TEST_PLAN.md").read_text(encoding="utf-8")
    m = re.search(r"## 4\. API 覆蓋檢查.*?現 (\d+) 個工具", text)
    assert m, "TEST_PLAN §4 標題格式變了，這條檢查要跟著改"
    assert int(m.group(1)) == actual, (
        f"TEST_PLAN §4 標題寫 {m.group(1)} 個工具，實際 {actual} 個")


# ---------------------------------------------------------------------------
# 全路由對照（v1.15.30）
#
# 上面那幾條的守備範圍是**每支工具至少一個** `/api/` 或 `/convert`。
# 所以「非工具」的 API 完全不在它眼裡 —— 實算之後，作業佇列、通知、收件匣、
# 管理介面的 XHR 共 16 支從來沒被檢查過有沒有寫進文件。
#
# 判準：**兩邊都把路徑參數正規化成 `{}` 再比**。路由叫 `{_filename}`、
# 文件寫 `{filename}` 是常見的，不正規化就會變成假缺口（我第一次量就是這樣
# 誤報的）。
# ---------------------------------------------------------------------------

_PARAM = re.compile(r"\{[^}]*\}")


def _norm(path: str) -> str:
    return _PARAM.sub("{}", path)


def _all_api_routes() -> set[str]:
    import app.main as app_main
    from tools.route_index import iter_routes
    # 不可以直接讀 app.routes：新版 FastAPI 把 include_router 包成 `_IncludedRouter`，
    # 直接讀只看得到頂層幾條 —— 這條檢查會**安靜地少驗**大部分端點（CI 用新版時就是這樣）
    routes = {r.path for r in iter_routes(app_main.app) if "/api/" in getattr(r, "path", "")}
    assert len(routes) > 100, f"只列到 {len(routes)} 支 /api/ 端點 —— 路由沒有攤開"
    return routes


def test_every_api_route_is_mentioned_in_api_md():
    """新增 API 卻沒寫進手冊 —— 使用者不會知道它存在。"""
    doc = _norm((_public_root(ROOT) / "API.md").read_text(encoding="utf-8"))
    missing = sorted(p for p in _all_api_routes() if _norm(p) not in doc)
    assert not missing, (
        f"這 {len(missing)} 支 API 端點在 API.md 裡完全沒提到：\n  "
        + "\n  ".join(missing)
        + "\n對外穩定的請寫進對應章節；管理介面自己用的 XHR 請列進 §11 "
          "那張「不保證相容」的表。")


def test_api_md_and_api_html_are_in_sync():
    """`api.html` 是從 `API.md` 生成的 —— 改了 md 沒重跑生成器，網頁版就停在
    舊內容（既有慣例，v1.9.x 起）。抽查幾個章節標題有沒有同時存在。"""
    pub = _public_root(ROOT)
    md = (pub / "API.md").read_text(encoding="utf-8")
    html_path = pub / "docs" / "api.html"
    if not html_path.exists():
        pytest.skip("公開樹沒有 docs/api.html")
    html = html_path.read_text(encoding="utf-8")
    heads = re.findall(r"^## (\d+[a-z]?\. .+)$", md, re.M)
    assert heads, "API.md 的章節標題格式變了"
    missing = [h for h in heads if h.split(". ", 1)[-1].strip() not in html]
    assert not missing, (
        f"api.html 沒有這些章節：{missing}\n"
        "跑 `python3 github/build-api-page.py` 重新生成。")
