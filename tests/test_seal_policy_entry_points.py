"""結構檢查：讀資產庫給使用者用的每一個函式，都要經過用印申請的管制判斷。

「僅限核准案件使用」的章只要有一個入口沒擋，整個管制就是假的（規格第 3 節）。入口今天是用印工具、
騎縫章、PDF 編輯器、資產圖檔網址；以後新增工具讀資產庫時忘了接，這支會紅。

判準：`app/` 底下（管理員的資產庫管理除外）每一個呼叫 `asset_manager.get / list / get_default` 的
函式（最內層），同一個函式裡要有 `can_use_asset`、`is_controlled_asset` 或 `usable` 其中之一；
真的不需要的寫進 `ALLOWED` 並註明理由。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARDS = {"can_use_asset", "is_controlled_asset", "usable"}
READS = {"get", "list", "get_default"}

#: (檔案, 函式) → 為什麼不用擋。
ALLOWED = {
    ("app/tools/pdf_stamp/router.py", "submit"):
        "主章已在 `_resolve_stamp_source` 擋過，這裡只是再取一次同一顆章的名稱寫歷史",
    ("app/tools/pdf_watermark/router.py", "*"): "浮水印不是用印，不在管制範圍（seal_policy.CONTROLLABLE_TYPES）",
    # 用印申請本身（核准流程）：只讀章的名稱、尺寸、圖檔雜湊，不把圖交給使用者
    ("app/core/seal_flow.py", "clean_flow"): "流程範本檢查章存不存在",
    ("app/core/seal_flow.py", "requestable_assets"): "列出可以申請的受管制章（只給名稱與尺寸）",
    ("app/core/seal_flow.py", "_clean_placements"): "用章的尺寸算示意框",
    ("app/core/seal_flow.py", "submit"): "送出時記下章圖的雜湊（核准綁定那一版的圖）",
    ("app/web/seal_request_routes.py", "_public_case"): "案件頁顯示章的名稱",
    ("app/core/seal_flow.py", "_verify_hashes"): "用印前核對章圖雜湊跟核准時一樣",
    ("app/core/seal_flow.py", "run_stamp"): "核准案件的用印本身（start_stamp 已驗保管人、核准的章、雜湊）",
    ("app/core/seal_flow.py", "_stamp_one"): "run_stamp 拆出來的單份文件用印（同上）",
    ("app/core/seal_flow.py", "clean_watermark"): "流程的浮水印只收種類是浮水印的資產（不是印章或簽名）",
    ("app/core/seal_flow.py", "simulate"): "管理員模擬：只讀章的名稱與管制設定，不碰圖檔",
    ("app/web/seal_request_routes.py", "seal_image"):
        "章圖只給這件案件的保管人、只在待用印 / 用印中 / 用印失敗，且只限核准的章（test_seal_stamp 驗）",
}
SKIP_FILES = {"app/core/asset_manager.py", "app/core/asset_access.py", "app/core/seal_policy.py"}
SKIP_DIRS = ("app/admin/",)   # 管理員的資產庫管理：上傳、維護印章本身


def _reads_assets(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in READS
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "asset_manager"):
            return True
    return False


def _has_guard(node: ast.AST) -> bool:
    for n in ast.walk(node):
        name = n.attr if isinstance(n, ast.Attribute) else n.id if isinstance(n, ast.Name) else None
        if name in GUARDS:
            return True
    return False


def _innermost_readers(tree: ast.AST):
    """回傳「自己直接讀資產庫」的函式（讀的動作不在更內層的函式裡）。"""
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for f in funcs:
        inner = [n for n in ast.walk(f) if n is not f and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        own = [s for s in f.body]
        # 把內層函式的節點拿掉，只看這一層自己的程式
        inner_ids = {id(x) for i in inner for x in ast.walk(i)}
        if any(id(x) not in inner_ids and isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
               and x.func.attr in READS and isinstance(x.func.value, ast.Name) and x.func.value.id == "asset_manager"
               for s in own for x in ast.walk(s)):
            yield f


def test_every_asset_reader_is_guarded():
    missing = []
    for p in sorted((ROOT / "app").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES or rel.startswith(SKIP_DIRS):
            continue
        src = p.read_text(encoding="utf-8")
        if "asset_manager" not in src:
            continue
        tree = ast.parse(src)
        for f in _innermost_readers(tree):
            if (rel, f.name) in ALLOWED or (rel, "*") in ALLOWED:
                continue
            if not _has_guard(f):
                missing.append(f"{rel}:{f.lineno} {f.name}")
    assert not missing, ("這些地方讀了資產庫卻沒有經過用印申請的管制判斷（can_use_asset / "
                         "is_controlled_asset / usable）：\n" + "\n".join(missing))


def test_the_check_has_teeth(tmp_path):
    """反向對照：一個讀了資產庫卻沒擋的函式要被抓到。"""
    bad = "def page(request):\n    return asset_manager.list(type='stamp')\n"
    good = "def page(request):\n    return asset_access.usable(request, asset_manager.list(type='stamp'))\n"
    assert [f.name for f in _innermost_readers(ast.parse(bad)) if not _has_guard(f)] == ["page"]
    assert [f.name for f in _innermost_readers(ast.parse(good)) if not _has_guard(f)] == []
