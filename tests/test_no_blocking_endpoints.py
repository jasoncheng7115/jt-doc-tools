"""async 端點裡不可以直接做重活 —— 那會把整站鎖住。

2026-08-26 正式機實測：使用者在跑「文件去識別化」時，全站**兩分鐘完全不回應**。
日誌寫得很清楚：

    事件迴圈被卡住 116.4 秒（執行中 0 個作業、排隊 0 個）
    慢請求 116.9 秒：POST /tools/doc-deident/process

「執行中 0 個作業」是關鍵 —— 它根本沒走背景作業佇列，是**同步的重活直接寫在
async 端點裡**。同一支工具的公開 API 早就包在 `to_thread` 裡了，只有網頁用的
那條漏掉：同一件事兩份實作，只有一份修過。

同一份日誌裡還有 `POST /tools/pdf-editor/save` 卡 6.2 秒，同一個形狀。

v1.14.55 先釘住這兩支，v1.14.56 把其餘的全部處理完 —— **現在全站是 0**。

盤點時要小心兩件事，否則數字會虛胖、虛胖的指標沒人會認真看：
①**巢狀的同步函式不算**（那些是丟給背景作業或 `to_thread` 的閉包，跑在別的
執行緒）；②共用的重活底層（算縮圖、soffice 轉檔）已經有 `_async` 版本，
端點一律用那個，不必自己包。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

#: 已經修好、不可以退步的端點：(檔案, 函式名)
GUARDED = [
    ("app/tools/doc_deident/router.py", "process"),
    ("app/tools/pdf_editor/router.py", "save"),
]

#: 重活**藏在被呼叫的函式裡**，端點自己看不到 —— 這種只能逐支列管。
#: 例如刪帳號：端點只寫 `user_manager.delete(uid)`，但那裡面要動好幾張表、
#: 觸發 CASCADE、還要清工作區的檔案。客戶回報「刪 user 會卡住，多刪幾個
#: 系統就像掛掉」（2026-08-27）。掃描抓不到，所以用清單釘住。
MUST_OFFLOAD = [
    ("app/admin/auth_router.py", "users_delete"),
    ("app/admin/auth_router.py", "users_bulk_delete"),
    # 會議摘要的下載：把圖算成 PNG（PyMuPDF）、打包 ZIP 都是 CPU 工作，
    # 但重活藏在 `mc.to_png()` / `_report_pdf()` 裡面，**靜態掃描看不到**。
    # 留在事件迴圈上的話，有人按下載就會卡住整個網站（2026-09-19 抓到）。
    ("app/tools/meeting_summary/router.py", "download"),
    # 公文撰擬的資料來源：上傳要解 zip 裡每一份 odt、地址簿第一次查詢要讀
    # 整份 JSON（約 5 MB）—— 重活都藏在 `official_doc_sources` 裡，掃描看不到。
    ("app/admin/official_doc_routes.py", "official_doc_upload"),
    ("app/admin/official_doc_routes.py", "official_doc_search_orgs"),
    ("app/admin/official_doc_routes.py", "official_doc_download"),
]

#: v1.14.56 起是 **0**：全站的 async 端點都不會在事件迴圈上做重活了。
#: 保留這個常數是為了讓「又多了一支」的錯誤訊息講得出數字。
KNOWN_REMAINING = 0


def _endpoint_body(path: str, func: str) -> ast.AST:
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == func:
            return node
    raise AssertionError(f"{path} 裡找不到 {func}()")


def _offloads(node: ast.AST) -> bool:
    return _dispatches_elsewhere(node)


HEAVY = {"apply_redactions", "get_pixmap", "insert_pdf", "subset_fonts",
         "convert_to_pdf", "convert_to_docx", "convert_to_odt", "render_page_png",
}


def _dispatches_elsewhere(node: ast.AST) -> bool:
    """這支端點有沒有把重活**交給別的執行緒**跑。

    兩種算數：`to_thread` / `run_in_executor`，或交給背景作業佇列
    （`job_manager`）。

    **不可以只看「重活是不是寫在巢狀函式裡」** —— 包成閉包正是修法本身，
    只看巢不巢狀的話，有人把 `await to_thread(_work)` 改回 `_work()`，
    重活仍然在巢狀函式裡，就抓不到了（2026-08-26 變異驗證時踩到這個盲點）。
    """
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in ("to_thread", "run_in_executor"):
            return True
        if isinstance(n, ast.Name) and n.id == "job_manager":
            return True
        if isinstance(n, ast.Attribute) and n.attr == "job_manager":
            return True
        # 使用者自訂正規式的專用執行緒池（`app/core/user_regex.run`，v1.17.38）
        if (isinstance(n, ast.Attribute) and n.attr == "run" and isinstance(n.value, ast.Name)
                and n.value.id in ("user_regex", "_user_regex")):
            return True
        # 文件去識別化把「有自訂正規式就走專用池、沒有就 to_thread」包成 `_run_scan`
        if isinstance(n, ast.Name) and n.id == "_run_scan":
            return True
    return False


def _heavy_calls(node: ast.AST) -> set:
    """這支端點會做哪些重活（含巢狀函式裡的）。"""
    return {n.attr for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and n.attr in HEAVY}


@pytest.mark.parametrize("path,func", GUARDED)
def test_guarded_endpoints_offload_heavy_work(path, func):
    node = _endpoint_body(path, func)
    heavy = _heavy_calls(node)
    assert heavy, f"{func}() 裡找不到重活 —— 這條測試的前提變了，請重新確認"
    assert _offloads(node), (
        f"{path}:{func}() 直接在 async 端點裡做 {sorted(heavy)} —— "
        "會把事件迴圈鎖住，全站對所有人都不回應。"
        "請包成同步函式再 `await asyncio.to_thread(...)`。"
    )


@pytest.mark.parametrize("path,func", MUST_OFFLOAD)
def test_indirectly_heavy_endpoints_offload(path, func):
    """重活藏在被呼叫的函式裡的端點，一樣要丟出事件迴圈。"""
    node = _endpoint_body(path, func)
    assert _offloads(node), (
        f"{path}:{func}() 沒有把工作丟出事件迴圈。"
        "它呼叫的函式會動資料庫與檔案系統，跑在事件迴圈上會讓全站停止回應。")


def test_no_endpoint_blocks_the_event_loop():
    """全站的 async 端點都不可以在事件迴圈上做重活。

    v1.14.55 剛開始盤點時「42 支」，其中大半是誤判（巢狀的作業函式）；真正
    有問題的是 12 支，加上共用底層改掉的 29 處，v1.14.56 全部處理完 → 0。
    """
    guarded = {(p, f) for p, f in GUARDED}
    offenders = []
    for path in sorted(pathlib.Path("app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            is_endpoint = any(
                isinstance(d, ast.Call)
                and getattr(d.func, "attr", "") in ("post", "get", "put", "delete", "api_route")
                for d in node.decorator_list)
            if not is_endpoint or (str(path), node.name) in guarded:
                continue
            if _heavy_calls(node) and not _offloads(node):
                offenders.append(f"{path}:{node.name}")

    assert len(offenders) <= KNOWN_REMAINING, (
        f"這些端點會在事件迴圈上做重活（全站應該是 {KNOWN_REMAINING} 支）：\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\n算縮圖用 `await pdf_preview.render_page_png_async(...)`，"
          "soffice 轉檔用 `await office_convert.convert_to_pdf_async(...)`；"
          "其他重活包成同步閉包再 `await asyncio.to_thread(_work)`。")
