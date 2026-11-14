"""管理區不可把例外原文吐到畫面上（CodeQL py/stack-trace-exposure）。

## 由來

CodeQL 長期掛著 6 個「Information exposure through an exception」。
我們的筆記寫「逐一核實過，`except` 只收 `ValueError`、內容是自撰的驗證訊息」
—— **那句話是錯的**：實際用 `ast` 統計，`auth_router.py` 有 30 處把例外訊息
回給使用者，其中 **8 處是 `except Exception`**，形狀是
`raise HTTPException(500, f"查詢失敗：{type(e).__name__}: {e}")` ——
會把 ldap3 的原文（可能含 DN、伺服器位址）送到畫面上，而畫面會被截圖進工單。

專案在 v1.12.86 就立過規矩：**畫面給通用訊息、細節只進日誌**，還為此寫了
`log_safe.safe_user_error()`。這 8 處只是漏用。

測試清單：
  1. 管理區的 `except Exception` 不可把例外原文放進回應（**自動列舉**，
     新端點自動被涵蓋）
  2. `except ValueError` 允許（那是我們自撰的驗證訊息）—— 但要真的有這種用法，
     否則第 1 條可能是空跑
  3. 通知測試按鈕仍要講原因，但走 `safe_user_error`（webhook URL 是密鑰）
"""
from __future__ import annotations

import re
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADMIN = ROOT / "app" / "admin"
WEB = ROOT / "app" / "web"

#: 這些例外類別是**我們自己的**，訊息由我們撰寫（「帳號不能空白」這類），
#: 顯示給管理員是刻意的設計，不在此限。
OWN_EXCEPTIONS = {"ValueError", "auth_ldap.AuthError", "auth_settings.BootstrapError",
                  "(_auth.AuthError, auth_local.AuthError)", "AuthError",
                  # 知識庫（v1.16.61）：`KBError` 是 `ValueError` 的子類別，每一處 raise 都是
                  # 固定句子（欄位名也是我們寫死的）；`EmbedError` 也一樣 —— 連不上 / 逾時 /
                  # HTTP 錯誤都換成固定句子，只帶 HTTP 狀態碼，不帶對方回的內容或位址
                  "store.KBNotFound", "store.KBError", "embed.EmbedError",
                  # 知識庫「政府公開資料」（v1.16.66）：`GovError` 與子類別每一處 raise 都是
                  # 我們寫的句子；包進來的 `PackageError` / `safe_fetch` / `zip_guard` /
                  # `ExtractError` 也全是固定句子（只帶管理員自己填的主機名與上限數字），
                  # 索引失敗的代碼另外換成句子（`gov._INDEX_ERRORS`）。原因細節走 logger。
                  "gov.GovNotFound", "gov.GovBusy", "gov.GovError", "gov.NeedsConfirm",
                  # 名片簿（v1.17.41）：`BookError` 每一處 raise 都是我們寫的句子（只帶我們自己的上限數字，
                  # 例如「成員最多 200 個」），不帶使用者輸入或名片內容
                  "books.BookError"}


def _leaky_handlers(path: Path) -> list[tuple[int, str]]:
    """回傳 (行號, 例外型別)：把例外原文放進回應、且型別不是我們自己的。"""
    src = path.read_text(encoding="utf-8")
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        typ = ast.unparse(node.type) if node.type else "（裸 except）"
        if typ in OWN_EXCEPTIONS:
            continue
        body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
        # 只看「回給使用者」的路徑：HTTPException / JSONResponse / return dict
        if not any(k in body for k in ("HTTPException", "JSONResponse", "return {")):
            continue
        # 例外原文有沒有被塞進去（safe_user_error 已經過濾過，不算）
        raw = ("{e}" in body or "str(e)" in body) and "safe_user_error" not in body \
            and "_safe_user_error" not in body
        if raw:
            out.append((node.lineno, typ))
    return out


#: 不在此限的檔案，每一筆都要寫理由（沒有理由的例外清單，下次就會被拿來塞東西）。
SCAN_EXEMPT = {
    # 這支不是本站的程式 —— 它是外接 GPU OCR 伺服器（jt-ocr-server）的樣板，
    # 以 heredoc 內嵌進安裝腳本，跑在**另一台機器**上，呼叫者是我們自己的
    # 服務而不是瀏覽器。它的錯誤訊息（"image decode failed: ..."）正是排除
    # 問題時唯一的線索，遮掉只會讓遠端 OCR 更難查。
    "app/admin/ocr_remote_deploy/server_template.py",
}


def test_admin_routes_do_not_leak_exception_text():
    leaks = []
    files = [f for f in (list(ADMIN.rglob("*.py")) + list(WEB.rglob("*.py")))
             if f.relative_to(ROOT).as_posix() not in SCAN_EXEMPT]
    for f in files:
        for lineno, typ in _leaky_handlers(f):
            leaks.append(f"{f.relative_to(ROOT)}:{lineno}（except {typ}）")
    assert all((ROOT / x).exists() for x in SCAN_EXEMPT), \
        "豁免清單裡有檔案已經改名或刪除 —— 清單本身漂掉了"
    assert not leaks, (
        "這些地方把例外原文回給使用者了：\n  " + "\n  ".join(leaks) +
        "\n畫面給通用訊息、細節走 logger.exception + log_safe（見 v1.12.86 的規矩）")


def test_the_scan_actually_finds_something():
    """反向對照：把一個假的洩漏形狀丟進掃描器，必須抓得到。

    不驗這件事的話，只要掃描條件寫錯（例如 unparse 的字串對不上），
    上一條就會**永遠是綠的**。
    """
    import tempfile
    src = '''
def f():
    try:
        pass
    except Exception as e:
        raise HTTPException(500, f"查詢失敗：{e}")
'''
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        p = Path(fh.name)
    try:
        assert _leaky_handlers(p), "掃描器抓不到明顯的洩漏形狀"
    finally:
        p.unlink()


def test_own_validation_messages_are_still_shown():
    """`except ValueError` 的自撰訊息要留著 —— 管理員需要看到「帳號不能空白」
    這類訊息，全部改成通用訊息反而讓管理區變難用。"""
    src = (ADMIN / "auth_router.py").read_text(encoding="utf-8")
    n = sum(1 for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.ExceptHandler) and node.type is not None
            and ast.unparse(node.type) == "ValueError"
            and "str(e)" in ast.unparse(ast.Module(body=node.body, type_ignores=[])))
    assert n >= 10, f"自撰驗證訊息被改掉太多（只剩 {n} 處）"


def test_notify_test_still_explains_but_scrubs():
    """通知測試按鈕的用途就是講原因 —— 但不可原樣吐出例外訊息：
    Slack / Teams 的 webhook URL 本身就是密鑰。"""
    src = (ADMIN / "auth_router.py").read_text(encoding="utf-8")
    i = src.index("async def notify_test")
    # 取整個函式（到下一個同層的 def / 裝飾器），不用固定長度 —— v1.17.0 加了 Zulip 測試收訊者，
    # 函式變長，固定 3000 字的視窗看不到後面的 safe_user_error
    m = re.compile(r"\n    (?:@|async def |def )").search(src, i + 10)
    seg = src[i:m.start() if m else len(src)]
    assert "_safe_user_error" in seg, "通知測試沒有走 safe_user_error"
    assert 'f"{type(e).__name__}: {e}"' not in seg, "仍在原樣回傳例外訊息"
