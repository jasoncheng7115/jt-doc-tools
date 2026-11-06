"""「id 由使用者傳入」的端點一律要有 ACL —— 靜態全面掃描。

## 由來（我自己漏掉的一整類）

第一輪滲透測試我只打了「id 在**路徑**上」的端點（`/api/jobs/{id}`），13 項全過就
以為沒問題。實際上洩漏發生在另一個形狀：**id 從 request body 傳進來**
（`upload_id: str = Form(...)`、`(await request.json()).get("upload_id")`）。
pdf-compress 的 `/submit` 就是這樣被拿到別人的檔案的 —— 那次真的把另一個使用者
PDF 裡的內容抽了出來。

逐一手動打端點會漏，因為端點會一直增加。所以改成**靜態掃描整個 app**：任何吃
使用者提供的 id 的處理函式，都必須呼叫某個已知的 ACL 檢查。

## 這份的限制（要老實講）

靜態掃描只確認「有呼叫」，不保證那個呼叫是對的（例如 fail-open 的
`if uid: require(...)` 在這裡看起來是有做的）。ACL 的**語意**由
`test_preview_acl_failopen.py`、`test_job_id_acl.py`、
`test_submission_check_acl.py` 這幾份實際打端點驗證。兩種都需要：靜態掃描保證
「沒有人被忘記」，動態測試保證「做的是對的事」。
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest


#: 使用者可控的 id 參數名。看到這些名字就要求同一個函式裡有 ACL 檢查。
ID_NAMES = frozenset({
    "upload_id", "batch_id", "case_id", "job_id", "uid", "id",
    "asset_id", "entity_id", "file_id", "template_id", "token_id",
})

#: 已知的 ACL 檢查。用正規表示式而不是固定字串 —— 各檔案 import 的別名不同
#: （`_uo.require`、`_uo2.require`、`upload_owner.require`），寫死名單會漏。
#: **`require_uuid_hex` 不算**：那只驗格式，不驗歸屬。
ACL_PATTERNS = (
    r"\b[A-Za-z_][A-Za-z_0-9]*(?:uo|owner)[A-Za-z_0-9]*\.(?:require|check)\(",
    r"\brequire_by_filename\(",
    r"\b_require_access\(",        # pdf-annotations 系列
    r"\b_require_own_upload\(",    # pdf-nup（id 藏在 pydantic 模型欄位裡）
    r"\b_check_case_acl\(",        # submission-check
    r"\b_require_case\b",          # 公文撰擬（案件；多半寫成 to_thread(_require_case, …)）
    r"\b_job_access\(",            # 作業 API / pdf-to-office 預覽與報告
    # 用印申請：`_case_for` 呼叫 `seal_flow.can_view`（申請者 / 審核者 / 保管人 / 稽核員），
    # 狀態轉換的端點經 `_case_action` 走同一個 `_case_for`；行為由 test_seal_flow 的「誰看得到什麼」驗
    r"\b_case_for\(", r"\b_case_action\(",
    r"\brequire_admin\b", r"\b_require_admin\b",
    r"\b_require_tool\(",
    r"Depends\(require_login\)",   # router 參數層級的登入閘
    r"Depends\(require_admin\)",
)

#: 掃描略過的目錄。**只能放「整個目錄都由 router 層級 dependency 保護」的**，
#: 而且要有對應的執行期測試證明（見 test_admin_router_level_gate_is_real）。
SKIP_DIRS = (
    "app/admin/",   # APIRouter(dependencies=[Depends(require_admin)])，
                    # auth_router 併入其中所以一併繼承
)

#: 例外清單。**每一項都要寫明為什麼安全**，不可以只是「先讓測試綠燈」。
EXEMPT: dict[tuple[str, str], str] = {
    ("app/web/speech_routes.py", "speech_audio"):
        "**存取控制就是簽章本身** —— 網址帶 `exp` 與 `sig`，簽章綁住"
        "「檔案 id ＋ 到期時間」，驗不過一律當成找不到（404）。"
        "這條路沒有『當前使用者』可以比對：拉檔的是外部服務，不帶 session "
        "也不帶 token（見 `app/core/signed_url.py` 的說明）。"
        "換句話說**知道 id 本身沒有用**，這比事後比對歸屬更強。",
    ("app/web/workspace_routes.py", "workspace_file"):
        "工作區的路徑由 `_entry_dir(request, file_id)` 從**當前使用者**的目錄解析，"
        "結構上就到不了別人的檔案（比事後檢查更強）",
    ("app/web/workspace_routes.py", "workspace_thumb"): "同上",
    ("app/web/workspace_routes.py", "workspace_delete"): "同上",
    ("app/web/workspace_routes.py", "workspace_rename"): "同上",
    ("app/web/workspace_routes.py", "workspace_save"):
        "job_id 走 job_manager 的歸屬判斷後才存；存入位置固定為當前使用者的目錄",
    ("app/tools/pdf_watermark/router.py", "preview_watermarked"):
        "asset_id 指的是管理員維護的**共用**資產庫（浮水印圖 / 標誌），"
        "不是使用者私有資料；資產本身的存取由 /assets 路由的 require_login 把關",
    ("app/tools/pdf_watermark/router.py", "submit"): "同上（asset_id 為共用資產）",
    ("app/tools/pdf_watermark/router.py", "batch_create"): "同上（asset_id 為共用資產）",
    ("app/tools/submission_check/router.py", "api_update_self_entity"):
        "自家實體清單以 `_user_key(user)` 分檔，entity_id 只在該使用者的檔案內查找",
    ("app/tools/submission_check/router.py", "api_delete_self_entity"): "同上",
    ("app/tools/pdf_stamp/router.py", "tool_preview"):
        "未實作的佔位端點，主體只有 `raise HTTPException(404)`",
    ("app/tools/pdf_stamp/router.py", "api_pdf_stamp"):
        "asset_id 是管理員維護的**共用**資產庫（印章 / 簽名 / Logo），網頁版有用印權限的人"
        "本來就能挑任何一顆（`/submit` 的 stamp_id 同一批）；整支端點由用印工具權限把關，"
        "型別限印章 / 簽名 / Logo（`_resolve_stamp_source`）",
}


def _has_acl(body: str) -> bool:
    return any(re.search(pat, body) for pat in ACL_PATTERNS)


def _body_model_names() -> frozenset[str]:
    """所有 pydantic BaseModel 的類別名 —— 用來認出「id 藏在模型欄位裡」的端點。"""
    found = set()
    for f in sorted(pathlib.Path("app").rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                    ast.unparse(b).endswith("BaseModel") for b in node.bases):
                found.add(node.name)
    return frozenset(found)


_BODY_MODELS = _body_model_names()


def _write_endpoints() -> list[tuple[str, str, list[str], str]]:
    """回 [(檔案, 函式名, 可疑 id 參數, 函式原始碼)]。

    掃 POST / PUT / DELETE / PATCH（寫入）與 GET（讀取也會外洩）。
    """
    out = []
    for f in sorted(pathlib.Path("app").rglob("*.py")):
        if any(f.as_posix().startswith(d) for d in SKIP_DIRS):
            continue
        text = f.read_text(encoding="utf-8")
        if "router." not in text and "@app." not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover
            continue
        lines = text.split("\n")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            decos = [ast.unparse(d) for d in node.decorator_list]
            if not any(k in d for d in decos
                       for k in (".post(", ".put(", ".delete(", ".patch(",
                                 ".get(", ".api_route(")):
                continue
            body = "\n".join(lines[node.lineno - 1:(node.end_lineno or node.lineno)])
            names = sorted({a.arg for a in
                            list(node.args.args) + list(node.args.kwonlyargs)
                            if a.arg in ID_NAMES})
            # JSON body 取 id 的形式（參數列看不到）
            for n in ID_NAMES:
                if f'body.get("{n}")' in body or f"body.get('{n}')" in body:
                    if n not in names:
                        names.append(n)
            # **pydantic 模型當內文**：`async def preview(opts: NupOptions)` ——
            # 參數叫 `opts`，id 是模型上的欄位。這一整類原本掃不到，pdf-nup 的
            # `/preview` 就是這樣漏掉的（B 拿 A 的 upload_id 就能取回 A 的 PDF
            # 算成的圖）。改為**看函式內文有沒有用到 `<參數>.<id 名>`**。
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                if a.arg in ID_NAMES or a.arg in ("self", "request"):
                    continue
                ann = ast.unparse(a.annotation) if a.annotation else ""
                if ann in _BODY_MODELS:
                    for n in ID_NAMES:
                        if f"{a.arg}.{n}" in body and n not in names:
                            names.append(n)
            if names:
                out.append((f.as_posix(), node.name, names, body))
    return out


def test_scan_actually_finds_endpoints():
    """掃描本身要有效 —— 抓到 0 個代表掃描壞了，不是代表沒問題。"""
    eps = _write_endpoints()
    assert len(eps) >= 30, f"只掃到 {len(eps)} 個端點，掃描邏輯可能失效"


def test_every_user_supplied_id_endpoint_has_an_acl_check():
    """每個吃使用者 id 的端點都要有 ACL 呼叫。"""
    missing = []
    for path, fn, names, body in _write_endpoints():
        if (path, fn) in EXEMPT:
            continue
        if _has_acl(body):
            continue
        missing.append(f"{path}::{fn}({','.join(names)})")
    assert not missing, (
        "這些端點吃使用者提供的 id 但看不到任何 ACL 檢查：\n  "
        + "\n  ".join(missing)
        + "\n（真的安全就加進 EXEMPT 並寫明理由）")


def test_exempt_entries_are_still_real():
    """例外清單裡的函式若已改名 / 刪除，要清掉 —— 否則會遮住真正的漏洞。"""
    eps = {(p, f) for p, f, _, _ in _write_endpoints()}
    stale = [k for k in EXEMPT if k not in eps]
    assert not stale, f"EXEMPT 裡這些已經不存在了，請移除：{stale}"


@pytest.mark.parametrize("tool", ["pdf_compress", "pdf_fill", "pdf_attachments",
                                  "pdf_metadata", "pdf_hidden_scan",
                                  "doc_deident", "pdf_extract_text",
                                  "pdf_extract_images"])
def test_json_body_id_endpoints_are_covered(tool):
    """釘住幾個真的踩過的檔案（pdf-compress 那次是實際洩漏，不是理論）。"""
    hits = [(p, f, n, b) for p, f, n, b in _write_endpoints()
            if f"/{tool}/" in p]
    assert hits, f"{tool} 沒被掃到，掃描邏輯可能改壞了"
    for p, f, n, b in hits:
        if (p, f) in EXEMPT:
            continue
        assert _has_acl(b), f"{p}::{f} 缺 ACL"


# ---------- admin 目錄用「router 層級 dependency」保護，這裡實測證明 ----------

ADMIN_PROBES = (
    ("POST", "/admin/users/update"),
    ("POST", "/admin/users/delete"),
    ("POST", "/admin/assets/set-default"),
    ("POST", "/admin/jobs/api/cancel"),
    ("GET", "/admin/api/sys-deps"),
)


@pytest.mark.parametrize("method,path", ADMIN_PROBES)
def test_admin_router_level_gate_is_real(method, path, auth_off):
    """靜態掃描略過 `app/admin/`，前提是那裡真的有 router 層級的 admin 閘。

    這條就是那個前提的證明 —— 一般使用者打 admin 端點必須被拒。光靠讀
    `APIRouter(dependencies=[...])` 推論不夠：`auth_router` 是另一個
    `APIRouter()`，要靠「併入有 dependency 的父 router」才會繼承，那是
    FastAPI 的行為，值得實測而不是假設。
    """
    from fastapi.testclient import TestClient
    from app.core import auth_settings, permissions, roles, sessions, user_manager
    import app.main as app_main

    pw = "TestAdmin1234"
    auth_settings.enable_local_with_admin(
        admin_username="jtdt-admin", admin_display_name="管理員",
        admin_password=pw, admin_password_confirm=pw, actor_ip="127.0.0.1")
    roles.seed_builtin_roles()
    uid = user_manager.create_local("plainuser", "一般人", "UserPass1234")
    permissions.set_subject_roles("user", str(uid), ["default-user"])
    tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, tok)

    r = c.request(method, path, data={"uid": "1", "job_id": "x"},
                  follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403, 404), (
        f"一般使用者打 {method} {path} 得到 {r.status_code} —— "
        f"admin 的 router 層級閘沒有生效，靜態掃描略過 app/admin/ 的前提不成立")


# ---------- fail-open 形狀：有呼叫 ACL，但條件不成立時整個跳過 ----------

#: 這些條件是 fail-closed 的正當寫法，不算問題：
#: - `if not <acl>(...): raise`      → ACL 出現在條件式裡，那就是檢查本身
#: - `if <未啟用認證>: return`        → 單人模式直通是設計
_OK_COND = re.compile(r"^not |auth_enabled|is_enabled|_auth_|admin")


def _conditional_acl_sites() -> list[tuple[str, str, str]]:
    """找「ACL 呼叫被包在 if 主體裡」的地方。

    ACL 出現在 **if 的條件式**裡（`if not check(...): raise`）不算 —— 那是檢查
    本身。只有出現在**主體**裡才可疑：條件不成立時就完全沒檢查。
    """
    out = []
    for f in sorted(pathlib.Path("app").rglob("*.py")):
        if any(f.as_posix().startswith(d) for d in SKIP_DIRS):
            continue
        text = f.read_text(encoding="utf-8")
        if not _has_acl(text):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.If):
                    continue
                test_src = ast.unparse(node.test)
                if _has_acl(test_src):
                    continue                     # ACL 就是條件本身
                body_src = "\n".join(ast.unparse(b) for b in node.body)
                if not _has_acl(body_src):
                    continue
                if _OK_COND.search(test_src):
                    continue
                out.append((f.as_posix(), fn.name, test_src[:70]))
    return out


#: 例外：條件不成立的那條路徑另有更強的保護，每筆都要寫明。
FAILOPEN_EXEMPT: dict[tuple[str, str], str] = {
    ("app/tools/submission_check/router.py", "upload_files"):
        "`if case_id:` 的另一條路徑是**建立新案件**（擁有者就是當下這個人），"
        "沒有既有資料可被越權存取",
    ("app/web/workspace_routes.py", "workspace_save"):
        "`if job_id:` 的另一條路徑是 `elif file is not None:`（直接上傳新檔），"
        "存的是呼叫者自己送上來的內容，沒有既有資料可被越權存取",
    ("app/web/workspace_routes.py", "build_router"):
        "同上 —— 這是包住 workspace_save 的工廠函式，AST 掃描會一併掃到外層",
    ("app/tools/official_doc/router.py", "_require_case"):
        "`if meta is None:` 是 v1.16.66 以前還在暫存區的舊案件（沒有 meta.json），走原本的歸屬紀錄"
        "（`upload_owner.check`，不通過一律 404）；另一條路徑是有 meta.json 的案件，同一支函式接著"
        "依擁有者 / 管理員判斷、其餘一律 404 —— 兩條都檢查，沒有一條是直接放行"
        "（`tests/test_official_doc_cases.py` 驗別人、不存在、已刪除都是同一個 404）",
}


def test_no_conditional_acl_that_can_be_skipped():
    """ACL 不可以寫成「條件成立才檢查」。

    真的踩過三次：
      * `if uid: require(uid, request)` —— 檔名認不出 upload_id 就不檢查
      * `rest = filename[4:].split("_", 1)[0]; if rest: ...` —— 切出空字串就不檢查
      * pdf-watermark 曾用「先切掉 wm_ 前綴」個案補救，下一個工具照樣中
    正確做法是把「認不出就拒絕」放進共用 helper（`require_by_filename`），
    呼叫端無條件呼叫。
    """
    bad = [f"{p}::{fn}  ← if {cond}"
           for p, fn, cond in _conditional_acl_sites()
           if (p, fn) not in FAILOPEN_EXEMPT]
    assert not bad, (
        "這些地方的 ACL 可能被跳過（條件不成立時完全不檢查）：\n  "
        + "\n  ".join(bad)
        + "\n（另一條路徑真的安全就加進 FAILOPEN_EXEMPT 並寫明理由）")


def test_failopen_exempt_entries_are_still_real():
    sites = {(p, fn) for p, fn, _ in _conditional_acl_sites()}
    stale = [k for k in FAILOPEN_EXEMPT if k not in sites]
    assert not stale, f"FAILOPEN_EXEMPT 裡這些已經不存在了，請移除：{stale}"


def test_acl_exceptions_are_never_swallowed():
    """ACL 呼叫不可以被 `try/except: pass` 包住 —— 那等於沒有檢查。"""
    bad = []
    for f in sorted(pathlib.Path("app").rglob("*.py")):
        if any(f.as_posix().startswith(d) for d in SKIP_DIRS):
            continue
        text = f.read_text(encoding="utf-8")
        if not _has_acl(text):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            body_src = "\n".join(ast.unparse(b) for b in node.body)
            if not _has_acl(body_src):
                continue
            for h in node.handlers:
                hs = "\n".join(ast.unparse(b) for b in h.body)
                # 例外：把錯誤轉成錯誤回應（用印申請的 `_err` / `_flow_err` 一律用例外帶的 4xx 狀態碼回）
                # 不是吞掉 —— 呼叫端拿到的就是 403 / 404。其他任何 `return` 照舊算吞掉。
                if hs.strip().startswith(("return _err(e)", "return _flow_err(e)")):
                    continue
                if hs.strip() in ("pass", "...") or hs.strip().startswith("return"):
                    bad.append(f"{f}: except {ast.unparse(h.type) if h.type else ''}")
    assert not bad, f"ACL 的例外被吞掉（等於沒檢查）：{bad}"


# ---------- 歸屬判斷只能有一份實作 ----------

#: 允許自己實作歸屬判斷的檔案（**判斷本身就住在這裡**）
_OWNERSHIP_HOME = (
    "app/main.py",                                  # _job_access（作業）
    "app/core/upload_owner.py",                     # check / require（上傳檔）
    "app/core/job_store.py", "app/core/job_manager.py",
    "app/core/workspace.py",                        # 以使用者目錄分隔
    "app/tools/submission_check/router.py",         # _check_case_acl（案件）
    "app/tools/submission_check/case_manager.py",
    # 公文撰擬案件：單一案件的讀寫一律走 router 的 `_require_case()`；這支是案件庫自己
    # 「只列我的」那一層（清單與「參考我的歷史案件」），比 `_require_case` 更嚴（管理員也
    # 只看得到自己的），認不出是誰時回空的，不會退回「全部」。
    "app/core/official_doc_cases.py",
)

_HANDROLLED = re.compile(
    r"owner_id is not None|owner_uid is not None|!=\s*job\.owner_id|"
    r"cur\s*!=\s*\w*owner")


def test_ownership_check_is_not_reimplemented():
    """歸屬判斷不可以在各處各寫一份。

    真的踩過兩次，而且是**同一個 bug 的兩份拷貝**：
      * `/api/jobs/*` 的 `_job_access` 修好「無主作業任何人可讀」之後，
        `/workspace/save` 仍然是開的 —— 它有自己一份 `if job.owner_id is not None:`
      * 送件檢核的案件 ACL 與 `delete_case` 各寫一份，兩份都有無主 fail-open

    修一份不會讓另一份跟著好，而且從程式碼上看兩邊都「有做檢查」。
    """
    bad = []
    for f in sorted(pathlib.Path("app").rglob("*.py")):
        if f.as_posix() in _OWNERSHIP_HOME:
            continue
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.split("\n"), 1):
            if line.lstrip().startswith("#"):
                continue                    # 註解裡提到不算
            if _HANDROLLED.search(line):
                bad.append(f"{f}:{i}  {line.strip()[:70]}")
    assert not bad, (
        "這些地方自己實作了歸屬判斷，請改呼叫共用的檢查："
        "\n  " + "\n  ".join(bad))
