"""測試計畫本身的守門：計畫沒涵蓋到的東西要紅燈。

發版門檻是照 `TEST_PLAN.md` 跑的。**計畫漏了什麼，那塊就等於沒驗過** ——
而且是無聲的：報告看起來仍然全綠。

歷史教訓（2026-08-16 稽核）：必跑指令引用了一個**不存在的測試檔**，照抄下去
直接 file not found，整條資安檢查等於沒跑；24 個工具只有 API 抽測沒有功能
驗收；三個管理頁整頁零驗收。這些都是人工維護清單必然的下場。

所以這裡一律**從程式實算**（路由表、工具註冊表），不寫死期望值 —— 寫死的
期望值自己就是下一個會漂掉的東西。
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
PLAN = ROOT / "TEST_PLAN.md"
PLAN_SEC = ROOT / "TEST_PLAN_SECURITY.md"


def _plan_text() -> str:
    return PLAN.read_text(encoding="utf-8") + "\n" + PLAN_SEC.read_text(encoding="utf-8")


def _routes():
    import app.main as app_main
    assert_sane(app_main.app)
    return list(iter_routes(app_main.app))


def test_every_tool_appears_in_the_plan():
    from app.tool_registry import discover_tools
    text = _plan_text()
    missing = sorted(t.metadata.id for t in discover_tools()
                     if t.metadata.id not in text)
    assert not missing, f"這些工具在測試計畫裡完全沒提到：{missing}"


#: 刻意不寫進測試計畫的 API，**每一條都要有理由**。
#:
#: 這些是管理介面自己用的 XHR 端點：它們的行為由對應管理頁的驗收項涵蓋
#: （`test_admin_pages_appear_in_the_plan` 守頁面本身），逐支再寫一條
#: 「呼叫它會怎樣」只是把同一件事寫兩遍。**不是因為懶得寫。**
_API_EXEMPT: dict[str, str] = {
    "/admin/api/check-latest-version": "管理頁的版本檢查按鈕",
    "/admin/api/ocr-langs/set-engine": "OCR 語言包頁的切換動作",
    "/admin/api/ocr-langs/set-quality": "OCR 語言包頁的切換動作",
    "/admin/api/ocr-langs/switch-active": "OCR 語言包頁的切換動作",
    "/admin/api/upload-limit/probe": "系統狀態頁的「量一下反向代理上限」按鈕",
    "/admin/api/assets": "資產管理頁的清單 XHR",
    "/admin/api/branding": "品牌設定頁的讀取 XHR",
    "/admin/jobs/api/list": "作業管理頁的清單 XHR",
    "/admin/jobs/api/history": "作業管理頁的歷史 XHR",
    "/admin/jobs/api/cancel/{job_id}": "作業管理頁的取消按鈕",
    "/admin/jobs/api/pause": "作業管理頁的暫停派送開關",
    "/admin/jobs/api/concurrency": "作業管理頁的併行數設定",
    "/admin/jobs/api/priority-users": "作業管理頁的優先名單",
    "/admin/jobs/api/user-search": "作業管理頁的使用者搜尋框",
}


def _api_paths() -> set[str]:
    return {r.path for r in _routes() if "/api/" in getattr(r, "path", "")}


def test_every_api_endpoint_appears_in_the_plan():
    """新增 API 卻忘了寫驗收 —— 這條會擋下來。

    **判準是完整路徑**。原本比對的是「`/api/` 之後那一截」，理由寫著
    「避免因為前綴寫法不同而誤判」—— 但那讓 `list` / `count` / `assets` /
    `history` 這種尾段**必然**在四千行的文件裡找得到：實測 84 支 `/api/`
    端點裡有 8 支是這樣「假通過」的，其中 7 支在兩份測試計畫裡完整路徑
    **出現 0 次**（外部稽核之後的實算，v1.15.28）。
    """
    text = _plan_text()
    missing = sorted(p for p in _api_paths()
                     if p not in text and p not in _API_EXEMPT)
    assert not missing, (
        "這些 API 端點在測試計畫裡沒有任何驗收項：\n  "
        + "\n  ".join(missing)
        + "\n請補進 TEST_PLAN.md §4（工具 API）或 §4.6（非工具 API）；"
        "真的不需要逐支驗收的，寫進 `_API_EXEMPT` **並寫下理由**。")


def test_the_exemption_list_has_no_dead_entries():
    """例外清單裡的路徑必須真的存在 —— 端點改名後留下的死條目會讓新端點
    悄悄被豁免（名字剛好撞到的話）。"""
    real = _api_paths()
    dead = sorted(p for p in _API_EXEMPT if p not in real)
    assert not dead, f"例外清單有這些不存在的端點（改名或已移除）：{dead}"


def test_every_exemption_has_a_reason():
    blank = sorted(k for k, v in _API_EXEMPT.items() if not v.strip())
    assert not blank, f"這些例外沒寫理由：{blank}"


def test_admin_pages_appear_in_the_plan():
    """整頁零驗收的管理頁 —— 2026-08-16 稽核抓到過三個。"""
    text = _plan_text()
    pages = sorted({
        r.path for r in _routes()
        if getattr(r, "path", "").startswith("/admin/")
        and "{" not in r.path
        and "GET" in (getattr(r, "methods", None) or set())
        and "/api/" not in r.path
    })
    missing = [p for p in pages
               if p not in text and p.rsplit("/", 1)[-1] not in text]
    assert not missing, (
        "這些管理頁在測試計畫裡沒提到：\n  " + "\n  ".join(missing))


#: 只檢查「**指令**裡引用的檔案」。說明文字裡引用當反例的不算 ——
#: 掃描連說明一起掃會誤報，這個專案已經踩過兩次（migration FK 順序、
#: fail-open 形狀），這裡不再犯第三次。
#: 指令行。**直譯器可以帶路徑** —— 計畫裡有一半的指令寫的是
#: `.venv/bin/python scripts/…`，第一版只認 `python …` 開頭，於是那
#: 一半從來沒有被檢查過（`scripts/` 沒同步進公開樹就是這樣漏掉的）。
_CMD_LINE = re.compile(r"^\s*(?:\$ )?(?:[A-Z_]+=\S+\s+)*"
                       r"(?:uv run |sudo )?[\w./-]*(?:pytest|python3?|bash|sh)\b.*$",
                       re.MULTILINE)
_FILE_REF = re.compile(r"(?:tests|scripts|tools|temp|temp_pdfs)/[\w./-]+"
                       r"\.(?:py|sh|yaml|json)")
#: `-o out.json` / `> out.txt` 後面那個路徑是**產出**不是輸入 —— 它本來就
#: 不該存在，拿它當「引用了不存在的檔案」是誤報。
_OUTPUT_ARG = re.compile(r"(?:-o|--output|-w|>>?)\s+(\S+)")


def _input_refs(line: str) -> list[str]:
    outputs = set(_OUTPUT_ARG.findall(line))
    return [r for r in _FILE_REF.findall(line) if r not in outputs]


def test_commands_in_the_plan_reference_files_that_exist():
    """照著計畫貼上去的指令不可以 file not found。

    只看指令行，不看說明文字（說明裡會引用「當初寫錯的檔名」當反例）。
    """
    bad = []
    for doc in (PLAN, PLAN_SEC):
        text = doc.read_text(encoding="utf-8")
        for line in _CMD_LINE.findall(text):
            for ref in _input_refs(line):
                if not (ROOT / ref).exists():
                    bad.append(f"{doc.name}: {ref}")
    assert not bad, ("計畫的必跑指令引用了不存在的檔案（照抄會直接失敗，"
                     "整條檢查等於沒跑）：\n  " + "\n  ".join(sorted(set(bad))))


def test_the_plan_does_not_hardcode_a_tool_count_that_can_drift():
    """計畫裡寫的工具數要跟實際一致（寫死的數字一定會漂）。"""
    from app.tool_registry import discover_tools
    actual = len(discover_tools())
    text = PLAN.read_text(encoding="utf-8")
    claims = [int(n) for n in re.findall(r"現\s*(\d+)\s*個工具", text)]
    wrong = [n for n in claims if n != actual]
    assert not wrong, f"計畫寫的工具數 {wrong} 與實際 {actual} 不符"


# ---------------------------------------------------------------------------
# 非 API 端點（§4.7）
#
# 2026-09-04 稽核：`test_every_api_endpoint_appears_in_the_plan` 只看 `/api/`，
# 但**畫面上按的每一顆按鈕打的都是非 API 端點**（analyze / preview / thumb /
# download / export / 暫存區 CRUD），267 支一條驗收都沒有。歷來最痛的幾個
# bug 就出在這一層（多頁合併預覽的水平越權、騎縫章預覽 90 秒、工作區縮圖
# 永遠空白、附件「無附件副本」沒清 /AF）—— 而那些工具的 `/api/` 都是好的。
#
# 判準是**字面比對整條路徑**，不是猜「有沒有被測到」。猜涵蓋率那條路
# v1.14.63 試過：寬一點是永遠綠的假測試，嚴一點會把驗得更嚴的工具誤報。
# ---------------------------------------------------------------------------

_SKIP_PREFIXES = ("/static", "/admin", "/openapi", "/docs", "/redoc", "/assets")


def _non_api_paths() -> set[str]:
    from app.tool_registry import discover_tools
    tool_home = set()
    for t in discover_tools():
        tool_home |= {f"/tools/{t.metadata.id}", f"/tools/{t.metadata.id}/"}
    out = set()
    for r in _routes():
        p = getattr(r, "path", "")
        if not p or "/api/" in p or "{rest:path}" in p:
            continue
        if p.startswith(_SKIP_PREFIXES) or p in tool_home:
            continue          # 工具首頁由 test_every_tool_appears_in_the_plan 守
        out.add(p)
    return out


def test_every_non_api_endpoint_appears_in_the_plan():
    text = _plan_text()
    missing = sorted(p for p in _non_api_paths() if p not in text)
    assert not missing, (
        f"這 {len(missing)} 支非 API 端點在測試計畫裡沒有任何驗收項：\n  "
        + "\n  ".join(missing)
        + "\n請補進 TEST_PLAN.md §4.7（每一支都要寫得出「怎麼知道它真的做對了」）。")


# ---------------------------------------------------------------------------
# 測試檔本身（§1.99）
#
# 2026-09-04 稽核：`tests/` 有 212 支，測試計畫只提到 96 支。剩下那 116 支
# **照跑**，但「這支在守什麼」在發版門檻上看不到 —— 要判斷某個功能有沒有
# 被守住，只能自己去翻程式。一覽表由 `tools/build_test_plan_index.py`
# 從每支檔案自己的開頭說明產生（說明跟程式同檔，不會漂）。
# ---------------------------------------------------------------------------

def _admin_write_paths() -> set[str]:
    """管理區「會改狀態」的端點（GET 以外的方法）。"""
    out = set()
    for r in _routes():
        p = getattr(r, "path", "") or ""
        if not p.startswith("/admin") or "/api/" in p or "{rest:path}" in p:
            continue
        methods = (getattr(r, "methods", None) or set()) - {"HEAD", "OPTIONS", "GET"}
        if methods:
            out.add(p)
    return out


def test_every_admin_write_endpoint_appears_in_the_plan():
    """**按下去會改到別人資料**的那些端點要有驗收項。

    涵蓋守門原本把整個 `/admin` 前綴跳過（見 `_SKIP_PREFIXES`，那是為了不讓
    一百多個管理頁子路由淹掉清單）。代價是：實算之後 **103 支會改狀態的管理
    端點裡有 79 支一條驗收都沒有** —— 而那些正是刪使用者、清工作區、匯入設定、
    改權限矩陣這一類（v1.15.30 補進 §4.8）。

    唯讀的管理端點仍然只由對應頁面的驗收涵蓋 —— 那是刻意的取捨：
    「讀」的風險是洩漏，已由 RBAC 與資安計畫守；「寫」的風險是改壞資料。
    """
    text = _plan_text()
    missing = sorted(p for p in _admin_write_paths() if p not in text)
    assert not missing, (
        f"這 {len(missing)} 支管理端點會改狀態，但測試計畫沒有驗收項：\n  "
        + "\n  ".join(missing)
        + "\n請補進 TEST_PLAN.md §4.8（共用判準已經寫在那一節，"
          "只要把端點列進對應的組即可）。")


def test_every_test_file_appears_in_the_plan():
    text = _plan_text()
    missing = sorted(p.name for p in (ROOT / "tests").glob("test_*.py")
                     if p.name not in text)
    assert not missing, (
        f"這 {len(missing)} 支測試檔在測試計畫裡沒出現：{missing[:8]}…\n"
        "跑 `python tools/build_test_plan_index.py` 重建 §1.99 的一覽表。")


def test_the_test_index_is_not_stale():
    """一覽表過期 = 說明跟實際測試對不上，跟沒有一樣。"""
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_test_plan_index.py"), "--check"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# CLI 指令與資料庫遷移（§3.5 / §1.98）
#
# 這兩塊在 2026-09-04 稽核前完全沒有驗收項：
#   * `jtdt` 的 26 個子指令只有 8 個被提到過 —— 而**啟用 LDAP 設定寫錯時，
#     web 上不去，救援全靠 CLI**。
#   * 29 支 schema 遷移一支都沒單獨列 —— 而 v1.12.0 的 `_m8` 就是升級時
#     把 `group_members` 與 `sessions` 清空的那一支。
# ---------------------------------------------------------------------------

def test_every_cli_subcommand_appears_in_the_plan():
    src = (ROOT / "app" / "cli.py").read_text(encoding="utf-8")
    cmds = sorted(set(re.findall(r'add_parser\(\s*"([a-z0-9-]+)"', src)))
    # 巢狀子指令（`jtdt auth set-local`、`jtdt audit-user create`）在計畫裡是寫成
    # 完整那一句，所以判準是「**出現在某一行提到 jtdt 的句子裡**」，
    # 而不是死板地找 `jtdt <cmd>`。
    lines = [ln for ln in _plan_text().splitlines() if "jtdt" in ln]
    missing = [c for c in cmds
               if not any(re.search(r"(?<![\w-])" + re.escape(c) + r"(?![\w-])", ln)
                          for ln in lines)]
    assert not missing, (
        f"這些 jtdt 子指令在測試計畫裡沒有驗收項：{missing}\n請補進 TEST_PLAN.md §3.5。")


def test_every_schema_migration_appears_in_the_plan():
    text = _plan_text()
    missing = []
    for p in (ROOT / "app" / "core").glob("*.py"):
        for name in re.findall(r"^def (_m\d+_\w+)\(", p.read_text(encoding="utf-8"), re.M):
            if name not in text:
                missing.append(f"{p.name}:{name}")
    assert not missing, (
        f"這些 schema 遷移在測試計畫裡沒列到：{missing}\n"
        "請補進 TEST_PLAN.md §1.98（每一支都要有「舊資料升上來」的測試）。")


def test_commands_in_the_plan_also_exist_in_the_published_tree():
    """公開版也要跑得起來。

    上面那條只驗**開發樹**。2026-09-04 稽核發現：公開版的 `TEST_PLAN.md`
    照樣叫人跑 `python tools/check_docs_tool_coverage.py`，但 `tools/`
    **從來沒有同步進 `github/`** —— 照抄指令直接 file not found，那幾條
    檢查在公開版等於沒跑。這正是計畫自己在 §5 警告過的那個坑
    （必跑指令引用不存在的檔案），只是換成從外面看。

    判準：計畫裡指令行引用到的相對路徑，`github/` 底下也要有。
    （`github/` 是 repo 的根，不是子目錄 —— 路徑寫法完全一樣。）
    """
    gh = _public_root(ROOT)
    if not gh.exists():          # 只有開發樹有 github/
        pytest.skip("沒有 github/ 發佈樹")
    bad = []
    for doc in (PLAN, PLAN_SEC):
        for line in _CMD_LINE.findall(doc.read_text(encoding="utf-8")):
            for ref in _input_refs(line):
                if (ROOT / ref).exists() and not (gh / ref).exists():
                    bad.append(ref)
    assert not bad, (
        "這些檔案在開發樹有、公開樹沒有（公開版照抄指令會 file not found）：\n  "
        + "\n  ".join(sorted(set(bad)))
        + "\n請把它加進 sync-to-github.sh 的 ITEMS。")


def test_every_tool_has_its_own_manual_acceptance_block():
    """每一支工具在 §2 都要有**自己的**驗收區塊，而且不只一兩條。

    `test_every_tool_appears_in_the_plan` 只驗「工具 id 在計畫裡出現過」——
    以前有兩段「補列」把十幾支工具擠成一行一條，通得過那條守門，但實際上
    等於**沒有驗收**（一支工具一條，涵蓋不到它自己的主要功能）。

    判準：`#### 名稱 (tool-id)` 這樣的標題要在，底下至少三條 `- [ ]`。
    三條是下限不是目標 —— 少於三條幾乎一定漏掉主要路徑。
    """
    from app.tool_registry import discover_tools
    text = _plan_text()
    start = text.index("## 2. 手動驗收清單")
    end = text.index("## 3. 跨平台檢查")
    blocks: dict[str, int] = {}
    for b in re.split(r"\n#### ", text[start:end])[1:]:
        head = b.split("\n", 1)[0]
        m = re.search(r"\(([a-z0-9-]+)\)", head)
        if m:
            blocks[m.group(1)] = b.count("\n- [ ]")
    no_block = sorted(t.metadata.id for t in discover_tools()
                      if t.metadata.id not in blocks)
    thin = sorted((tid, n) for tid, n in blocks.items() if n < 3)
    assert not no_block, f"這些工具在 §2 沒有自己的驗收區塊：{no_block}"
    assert not thin, f"這些工具的驗收項少於三條：{thin}"


def test_every_html_page_appears_in_the_plan():
    """**非管理區**的頁面也要有驗收 —— 首頁 / 我的作業 / 工作區 / 登入 / 2FA。

    原本只有管理頁那條守門，於是「每一頁都要測」在管理區以外是空的。
    判準同樣是從路由表實算，不寫死清單。
    """
    text = _plan_text()
    pages = sorted({
        r.path for r in _routes()
        if getattr(r, "path", "")
        and not r.path.startswith(("/admin", "/api", "/tools", "/static",
                                   "/assets", "/branding", "/i18n"))
        and "{" not in r.path
        and "GET" in (getattr(r, "methods", None) or set())
        and r.path not in ("/healthz", "/favicon.ico", "/robots.txt",
                           "/openapi.json", "/docs", "/redoc",
                           "/docs/oauth2-redirect")
    })
    missing = [p for p in pages
               if p not in text and p.strip("/").rsplit("/", 1)[-1] not in text]
    assert not missing, (
        "這些頁面在測試計畫裡沒有任何驗收項：\n  " + "\n  ".join(missing)
        + "\n請補進 TEST_PLAN.md §2.7（介面）。")
