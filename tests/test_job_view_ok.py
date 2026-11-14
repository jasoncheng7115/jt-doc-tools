"""「我的作業」的「開啟」：打得開才給、打不開不給 —— 與管理頁的作業結果檔用量。

## 由來（v1.16.6 查保留期時記下的待辦）

過了作業保留期，資料被清掉了，「我的作業」那一列卻**同時**掛著「開啟」與
「結果已逾期清除」，按下去是 410 對話框。原因是 `my_jobs.html` 只要有
`view_url` 就畫「開啟」，不看資料還在不在。

**不可以拿 `has_result` 代替**：逐句翻譯的結果是 `trd_<作業編號>.json`、沒有設
`result_path`，它的 `has_result` 永遠是 false —— 照那個條件改，逐句翻譯的
「開啟」會整個消失。所以由伺服器算 `view_ok`，認檔案的規則跟清理程式是
**同一份**（`job_store.file_keys` / `owns_file`）。

同一輪另外兩件：

* 管理頁「作業結果檔」那一列量的是 `data/jobs/` —— 全站沒有任何工具把結果
  放在那裡，**永遠是 0 MB**。改成量「保留期內的作業擁有的檔案」，用同一套認法。
* `job_manager.cleanup_expired()` 是死碼（沒有人呼叫），而且它的期限跟管理頁
  的保留期對不上。拿掉，並檢查不會再長出第二條清理路徑。

## 判準

* 逐支工具走過：**資料在 → 打得開；資料清掉 → 打不開**（`?job=` 那種頁面），
  而「開啟」指向自己保存資料的頁面（送件前檢核的案件頁、知識庫管理頁）
  不受暫存區影響。清單由 AST 找出來 —— 新工具加了 `view_url` 卻沒進這張表，
  這裡會紅。
* 前端真的在 node 裡跑那支決定出口的函式，不是比對原始碼字串。
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import time
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MY_JOBS = ROOT / "app" / "web" / "templates" / "my_jobs.html"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """跟 `test_retention_keeps_job_artifacts` 同一個做法：只換 `settings.data_dir`。"""
    from app.config import settings
    from app.core import job_store

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    for sub in ("temp", "jobs", "temp/.owners"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    job_store.init()
    return tmp_path


def _hex() -> str:
    return uuid.uuid4().hex


def _row(*, jid: str | None = None, status: str = "done", view_url: str = "",
         upload_id: str = "", result_path=None, extra: dict | None = None) -> dict:
    """`job_store.list_jobs()` 的一列長什麼樣（`meta` 已經解開）。"""
    meta = dict(extra or {})
    if view_url:
        meta["view_url"] = view_url
    if upload_id:
        meta["upload_id"] = upload_id
    return {"id": jid or _hex(), "status": status, "meta": meta,
            "result_path": str(result_path) if result_path else None}


def _ok(row, statuses=None) -> bool:
    from app.core import job_files
    return job_files.view_ok_map([row], statuses)[row["id"]]


def _touch(p: pathlib.Path, hours_ago: float = 0.0) -> pathlib.Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * 100)
    if hours_ago:
        t = time.time() - hours_ago * 3600
        os.utime(p, (t, t))
    return p


# ---------------- 規則本身 ----------------

def test_translate_doc_is_openable_without_a_result_path(data_dir):
    """**這一條就是「不可以用 `has_result`」的理由。**

    逐句翻譯沒有結果檔（`result_path` 是空的），資料是 `trd_<作業編號>.json`。
    """
    jid = _hex()
    _touch(data_dir / "temp" / f"trd_{jid}.json")
    row = _row(jid=jid, view_url=f"/tools/translate-doc/?job={jid}")
    assert row["result_path"] is None
    assert _ok(row), "逐句翻譯的對照表還在，「開啟」卻被藏起來了"


def test_once_the_data_is_swept_open_is_withdrawn(data_dir):
    """過了保留期、資料被清掉 → 不再給「開啟」（原本按下去是 410）。"""
    jid, uid = _hex(), _hex()
    f = _touch(data_dir / "temp" / f"ms_{uid}_result.json")
    row = _row(jid=jid, view_url=f"/tools/meeting-summary/?job={jid}", upload_id=uid)
    assert _ok(row)
    f.unlink()
    assert not _ok(row), "資料已經清掉了，「開啟」按下去會是 410"


def test_someone_elses_files_do_not_count(data_dir):
    """反向對照：暫存區裡有東西，但不是這件作業的 → 不算。

    沒有這一條的話，把判斷寫成「暫存區不是空的就給」也會過。
    """
    jid, uid = _hex(), _hex()
    _touch(data_dir / "temp" / f"ms_{_hex()}_result.json")
    _touch(data_dir / "temp" / "upload.pdf")
    row = _row(jid=jid, view_url=f"/tools/meeting-summary/?job={jid}", upload_id=uid)
    assert not _ok(row)


def test_an_ownership_record_alone_is_not_data(data_dir):
    """只剩歸屬紀錄（`.owners/<upload_id>.json`）不算「資料還在」。"""
    jid, uid = _hex(), _hex()
    _touch(data_dir / "temp" / ".owners" / f"{uid}.json")
    row = _row(jid=jid, view_url=f"/tools/meeting-summary/?job={jid}", upload_id=uid)
    assert not _ok(row)


def test_a_result_file_anywhere_counts(data_dir):
    """結果檔名不一定帶識別碼，也不一定在暫存區 —— 認 `result_path` 本身。"""
    jid = _hex()
    out = _touch(data_dir / "jobs" / "plain_output.pdf")
    row = _row(jid=jid, view_url=f"/tools/x/?job={jid}", result_path=out)
    assert _ok(row)
    out.unlink()
    assert not _ok(row)


def test_running_jobs_always_offer_a_way_to_watch(data_dir):
    """還在跑 → 「看進度」照樣給（頁面看的是作業狀態，不是暫存檔）。"""
    jid = _hex()
    for st in ("pending", "running"):
        assert _ok(_row(jid=jid, status=st, view_url=f"/tools/x/?job={jid}"))


def test_the_live_status_wins_over_the_database(data_dir):
    """資料庫的狀態會晚一步：記憶體說已經結束，就照結束的規則判斷。"""
    jid = _hex()
    row = _row(jid=jid, status="running", view_url=f"/tools/x/?job={jid}")
    assert _ok(row)
    assert not _ok(row, {jid: "done"})


def test_no_view_url_means_no_open(data_dir):
    jid = _hex()
    _touch(data_dir / "temp" / f"x_{jid}.pdf")
    assert not _ok(_row(jid=jid))


@pytest.mark.parametrize("url", ["/tools/submission-check/case/{case}",
                                 "/admin/knowledge"])
def test_pages_that_keep_their_own_data_stay_openable(data_dir, url):
    """「開啟」指向的頁面不靠這件作業的暫存檔（網址裡沒有它的識別碼）→ 一律給。

    送件前檢核的報告在案件底下（`data/submission_check/`）、知識庫在自己的資料
    庫，**不跟著暫存區的保留期走**。照暫存檔判斷的話，這兩支的「開啟」會在
    完成那一刻就消失（它們從來沒有帶自己識別碼的暫存檔）。
    """
    row = _row(view_url=url.replace("{case}", _hex()),
               extra={"case_id": "c1"})
    assert _ok(row)


def test_when_auth_is_on_the_ownership_record_must_still_be_there(data_dir, monkeypatch):
    """頁面用 `upload_id` 讀資料時會經過歸屬檢查 —— 紀錄沒了，一般使用者是 403。"""
    from app.core import job_files
    jid, uid = _hex(), _hex()
    _touch(data_dir / "temp" / f"ms_{uid}_result.json")
    row = _row(jid=jid, view_url=f"/tools/meeting-summary/?job={jid}", upload_id=uid)

    monkeypatch.setattr(job_files, "_auth_enabled", lambda: True)
    assert not _ok(row), "資料還在但歸屬紀錄沒了，一般使用者按下去會是 403"
    _touch(data_dir / "temp" / ".owners" / f"{uid}.json")
    assert _ok(row)

    # 反向對照：認證關閉時沒有歸屬檢查，紀錄在不在都不影響
    (data_dir / "temp" / ".owners" / f"{uid}.json").unlink()
    monkeypatch.setattr(job_files, "_auth_enabled", lambda: False)
    assert _ok(row)


def test_the_listing_happens_once_per_list(data_dir, monkeypatch):
    """整份清單只列一次暫存區 —— 「我的作業」在跑作業時每 2 秒輪詢一次。"""
    from app.core import job_files
    calls = []
    real = job_files.temp_index

    def _counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(job_files, "temp_index", _counting)
    rows = []
    for _ in range(20):
        jid = _hex()
        rows.append(_row(jid=jid, view_url=f"/tools/x/?job={jid}"))
    job_files.view_ok_map(rows)
    assert len(calls) == 1, f"列了 {len(calls)} 次暫存區"


# ---------------- 跟清理程式是同一份規則 ----------------

def _insert(jid, *, status, finished_hours_ago, view_url, upload_id="",
            result_path=None):
    from app.core import db, job_store
    now = time.time()
    fin = now - finished_hours_ago * 3600
    meta = {"view_url": view_url}
    if upload_id:
        meta["upload_id"] = upload_id
    conn = db.get_conn(job_store.db_path())
    with db.tx(conn):
        conn.execute(
            "INSERT INTO jobs (id, tool_id, status, result_path, meta, "
            "                  created_at, updated_at, finished_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (jid, "meeting-summary", status,
             str(result_path) if result_path else None, json.dumps(meta),
             fin - 60, fin, fin))


def test_open_follows_the_sweeper_both_ways(data_dir):
    """保留期內的作業清理後**還打得開**；過了保留期的清理後**打不開**。

    兩邊用的是同一套認檔案的規則 —— 清理留下來的，開啟鈕一定認得。
    """
    from app.core import job_files, job_store, retention

    kept_j, kept_u = _hex(), _hex()
    old_j, old_u = _hex(), _hex()
    t = data_dir / "temp"
    _touch(t / f"ms_{kept_u}_result.json", hours_ago=3)
    _touch(t / ".owners" / f"{kept_u}.json", hours_ago=3)
    _touch(t / f"ms_{old_u}_result.json", hours_ago=25)
    _touch(t / ".owners" / f"{old_u}.json", hours_ago=25)
    _insert(kept_j, status="done", finished_hours_ago=3,
            view_url=f"/tools/meeting-summary/?job={kept_j}", upload_id=kept_u)
    _insert(old_j, status="done", finished_hours_ago=25,
            view_url=f"/tools/meeting-summary/?job={old_j}", upload_id=old_u)

    rows = job_store.list_jobs(limit=50)
    before = job_files.view_ok_map(rows)
    assert before[kept_j] and before[old_j], "清理之前兩件的資料都還在"

    retention._sweep_temp_dir(2 * 3600, 24 * 3600)

    after = job_files.view_ok_map(job_store.list_jobs(limit=50))
    assert after[kept_j], "保留期內的作業被清理之後「開啟」不見了 —— 兩邊的認法不一致"
    assert not after[old_j], "過了保留期、資料已清掉，「開啟」還掛著（按下去 410）"


def test_there_is_only_one_definition_of_whose_file_it_is():
    """認檔案的規則只有一份（`job_store`）。

    清理、開啟鈕、管理頁用量各寫一份 32 碼的比對的話一定會漂：清理認得、開啟鈕
    認不得，畫面就會在檔案還在時把「開啟」藏起來。
    """
    hex_re = re.compile(r"\[0-9a-f\]\{32\}")
    offenders = [p.name for p in (ROOT / "app" / "core" / "retention.py",
                                  ROOT / "app" / "core" / "job_files.py")
                 if hex_re.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"這幾支自己寫了一份識別碼的比對：{offenders}"
    src = (ROOT / "app" / "core" / "retention.py").read_text(encoding="utf-8")
    assert "job_store.owns_file(" in src, "清理程式沒有走 job_store.owns_file"


# ---------------- 逐支工具：有 view_url 的都要走過 ----------------

def _view_url_exprs(tree: ast.AST) -> list[ast.AST]:
    """寫進**作業 meta** 的 view_url（節點，不是字串）：
    `job.meta["view_url"] = …` 與 `submit(…, meta={"view_url": …})`。

    只認這兩種形狀 —— `/api/jobs` 回應裡也有一個 `"view_url"` 鍵（`app/main.py`），
    那是把 meta 讀出來送給前端，不是設定它。
    """
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and t.slice.value == "view_url"
                        and isinstance(t.value, ast.Attribute) and t.value.attr == "meta"):
                    out.append(n.value)
        if isinstance(n, ast.Call):
            for kw in n.keywords:
                if kw.arg == "meta" and isinstance(kw.value, ast.Dict):
                    for k, v in zip(kw.value.keys, kw.value.values):
                        if isinstance(k, ast.Constant) and k.value == "view_url":
                            out.append(v)
    return out


def _refers_to_the_job(expr: ast.AST) -> bool:
    """這個網址帶不帶作業編號（`job.id`，或還沒 submit 前的 `{job_id}` 佔位字）。"""
    for n in ast.walk(expr):
        if isinstance(n, ast.Attribute) and n.attr == "id" \
                and isinstance(n.value, ast.Name) and n.value.id == "job":
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and "{job_id}" in n.value:
            return True
    return False


def _modules_with_view_url() -> dict[str, list[ast.AST]]:
    found = {}
    for p in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        exprs = _view_url_exprs(tree)
        if exprs:
            found[p.relative_to(ROOT).as_posix()] = exprs
    return found


def _mod(name):
    return importlib.import_module(name)


#: 每一支設 `view_url` 的模組：「開啟」是 `?job=` 那種要從暫存區讀回來的頁面
#: （`job`），還是按自己的資料定址的頁面（`page`）；前者另外給「這件作業的資料
#: 長什麼樣」—— **用工具自己的路徑函式產生**，不是照抄檔名。
#:
#: 新工具加了 `view_url` 卻沒進這張表 → `test_the_table_covers_every_view_url` 紅。
def _translate_doc(jid, uid):
    return {}, [_mod("app.tools.translate_doc.router")._trd_result_path(jid)]


def _meeting_summary(jid, uid):
    return {"upload_id": uid}, [_mod("app.tools.meeting_summary.router")._out_path(uid)]


def _meeting_transcribe(jid, uid):
    return {"upload_id": uid}, [_mod("app.tools.meeting_transcribe.router")._out_path(uid)]


def _doc_translate(jid, uid):
    r = _mod("app.tools.doc_translate.router")
    return {"upload_id": uid}, [r._meta_path(uid), r._out_path(uid, ".docx")]


TABLE = {
    "app/tools/translate_doc/router.py": ("job", "/tools/translate-doc/?job={jid}", _translate_doc),
    "app/tools/meeting_summary/router.py": ("job", "/tools/meeting-summary/?job={jid}", _meeting_summary),
    "app/tools/meeting_transcribe/router.py": ("job", "/tools/meeting-transcribe/?job={jid}", _meeting_transcribe),
    # 公文撰擬的案件自己保存（`official_doc_cases`），「開啟」是 `?case=<案件編號>`
    "app/tools/official_doc/router.py": ("page", "/tools/official-doc/?case={uid}", None),
    "app/tools/doc_translate/router.py": ("job", "/tools/doc-translate/?job={jid}", _doc_translate),
    "app/tools/submission_check/router.py": ("page", "/tools/submission-check/case/{uid}", None),
    "app/core/kb/indexer.py": ("page", "/admin/knowledge", None),
    "app/core/kb/gov.py": ("page", "/admin/knowledge/gov", None),
    # 名片辨識的批次自己保存（`business_cards.sqlite`，保留期限另外設），「開啟」是 `#batch=<批次編號>`
    "app/tools/business_card_reader/pipeline.py": ("page", "/tools/business-card-reader/#batch={uid}", None),
}


def test_the_scan_finds_the_view_url_tools():
    """**先證明掃得到東西**（掃 0 支跟「全部合格」在輸出裡一模一樣）。"""
    found = _modules_with_view_url()
    assert len(found) >= 6, f"只掃到 {sorted(found)}"


def test_the_table_covers_every_view_url():
    found = set(_modules_with_view_url())
    missing = sorted(found - set(TABLE))
    stale = sorted(set(TABLE) - found)
    assert not missing, (
        "這幾支設了 view_url 卻不在表上 —— 加進 TABLE，並確認它的「開啟」在資料"
        f"還在時打得開、清掉之後不再出現：{missing}")
    assert not stale, f"表上這幾支已經沒有 view_url 了：{stale}"


@pytest.mark.parametrize("module", sorted(TABLE))
def test_the_kind_in_the_table_matches_the_code(module):
    """表上寫 `job` 的，程式裡的網址就要帶作業編號；寫 `page` 的就不帶。

    不驗這一條的話，有人把案件頁改成 `?job=`、表上沒跟著改，下面那條會用錯的
    規則驗它而照樣全綠。
    """
    kind = TABLE[module][0]
    exprs = _modules_with_view_url().get(module) or []
    refs = {_refers_to_the_job(e) for e in exprs}
    assert refs == {kind == "job"}, (module, kind, refs)


@pytest.mark.parametrize("module", sorted(TABLE))
def test_every_view_url_tool_opens_while_its_data_exists(data_dir, module):
    kind, url_tpl, make = TABLE[module]
    jid, uid = _hex(), _hex()
    url = url_tpl.replace("{jid}", jid).replace("{uid}", uid)
    if kind == "page":
        row = _row(jid=jid, view_url=url)
        assert _ok(row), f"{module}：自己保存資料的頁面，「開啟」不該跟著暫存區消失"
        return
    meta, files = make(jid, uid)
    for f in files:
        assert pathlib.Path(f).parent == data_dir / "temp", f
        _touch(pathlib.Path(f))
    row = _row(jid=jid, view_url=url, extra=meta)
    assert _ok(row), f"{module}：資料還在，「開啟」卻不見了"
    for f in files:
        pathlib.Path(f).unlink()
    assert not _ok(row), f"{module}：資料已經清掉，「開啟」按下去會是 410"


# ---------------- 「我的作業」的 API ----------------

def test_the_jobs_api_reports_view_ok(auth_off):
    """判準落在 `/api/jobs` 真的送出去的欄位上。"""
    from fastapi.testclient import TestClient
    from app import main as app_main
    from app.config import settings
    from app.core import job_store
    from app.core.job_manager import Job

    job_store.init()
    ip = "192.168.77.%d" % (uuid.uuid4().int % 200 + 20)
    c = TestClient(app_main.app, client=(ip, 4321))
    alive, gone, trd = _hex(), _hex(), _hex()
    uid_alive, uid_gone = _hex(), _hex()
    made = [_touch(settings.temp_dir / f"ms_{uid_alive}_result.json"),
            _touch(settings.temp_dir / f"trd_{trd}.json")]
    try:
        for jid, uid, tool in ((alive, uid_alive, "meeting-summary"),
                               (gone, uid_gone, "meeting-summary"),
                               (trd, "", "translate-doc")):
            j = Job(id=jid, tool_id=tool, status="done",
                    meta={"view_url": f"/tools/{tool}/?job={jid}",
                          **({"upload_id": uid} if uid else {})})
            j.client_ip = ip
            job_store.upsert(j)
        jobs = {j["id"]: j for j in c.get("/api/jobs").json()["jobs"]}
        assert jobs[alive]["view_ok"] is True
        assert jobs[gone]["view_ok"] is False
        assert jobs[trd]["view_ok"] is True
        assert jobs[trd]["has_result"] is False, "前提：逐句翻譯沒有結果檔"
    finally:
        for p in made:
            p.unlink(missing_ok=True)


# ---------------- 前端：真的在 node 裡跑決定出口的函式 ----------------

def _extract_function(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    i = src.index("{", start)
    depth = 0
    for k in range(i, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[start:k + 1]
    raise AssertionError(f"{name} 的大括號沒有配對")


_CASES = {
    # 逐句翻譯：打得開、沒有結果檔 → 只有「開啟」，**不可以**同時說「已逾期清除」
    "trd": {"status": "done", "view_url": "/t/?job=1", "view_ok": True,
            "has_result": False},
    # 過了保留期：資料清掉了 → 不給「開啟」，說「已逾期清除」
    "expired": {"status": "done", "view_url": "/t/?job=1", "view_ok": False,
                "has_result": False},
    # 舊伺服器沒送 view_ok → 不給（寧可少一顆鈕，也不要一顆 410 的鈕）
    "missing": {"status": "done", "view_url": "/t/?job=1", "has_result": False},
    "running": {"status": "running", "view_url": "/t/?job=1", "view_ok": True},
    "file": {"status": "done", "has_result": True},
    "file_saved": {"status": "done", "has_result": True,
                   "workspace": {"saved": True}},
    "saved_only": {"status": "done", "has_result": False,
                   "workspace": {"saved": True}},
    "nothing": {"status": "done", "has_result": False},
    "error": {"status": "error", "view_url": "/t/?job=1", "view_ok": False,
              "has_result": False},
}

_EXPECTED = {
    "trd": ["open"],
    "expired": ["gone"],
    "missing": ["gone"],
    "running": ["open"],
    "file": ["download", "save"],
    "file_saved": ["download"],
    "saved_only": ["workspace"],
    "nothing": ["gone"],
    "error": [],
}


@pytest.fixture(scope="module")
def exits():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    fn = _extract_function(MY_JOBS.read_text(encoding="utf-8"), "exitsFor")
    script = (fn + "\nconst cases = " + json.dumps(_CASES) + ";\n"
              "const out = {};\nfor (const k in cases) out[k] = exitsFor(cases[k]);\n"
              "console.log(JSON.stringify(out));\n")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("case", sorted(_CASES))
def test_my_jobs_offers_the_right_exits(exits, case):
    assert exits[case] == _EXPECTED[case], (case, exits[case])


def test_the_page_uses_that_function_for_every_exit():
    """`render()` 畫按鈕時要照 `exitsFor()` 的結果，不可以自己另外判斷。

    只測 `exitsFor` 的話，`render` 把「開啟」改回 `if (j.view_url)` 照樣全綠。
    """
    src = MY_JOBS.read_text(encoding="utf-8")
    render = _extract_function(src, "render")
    assert "exitsFor(j)" in render
    for exit_name, marker in (("open", "ICON_OPEN"), ("download", "ICON_DOWNLOAD"),
                              ("workspace", "到工作區取用"), ("gone", "結果已逾期清除")):
        i = render.index(marker)
        guard = render.rfind("if (", 0, i)
        line = render[guard:render.index("\n", guard)]
        assert f"exits.includes('{exit_name}')" in line, (
            f"畫「{marker}」之前的判斷不是 exitsFor 的結果：{line.strip()}")
    assert "if (j.view_url)" not in render, "「開啟」又改回只看 view_url 了"


# ---------------- 管理頁：作業結果檔的用量 ----------------

def test_job_files_are_measured_where_they_actually_are(data_dir):
    """「作業結果檔」原本量 `data/jobs/`，永遠是 0 MB。"""
    from app.core import retention

    t = data_dir / "temp"
    jid, uid = _hex(), _hex()
    kept = [_touch(t / f"ms_{uid}_result.json", hours_ago=3),
            _touch(t / ".owners" / f"{uid}.json", hours_ago=3),
            _touch(t / f"trd_{jid}.json", hours_ago=3)]
    legacy = _touch(data_dir / "jobs" / "old_result.pdf", hours_ago=1)
    stray = [_touch(t / f"up_{_hex()}_x.pdf", hours_ago=1),
             _touch(t / "upload.pdf", hours_ago=1),
             _touch(t / ".owners" / f"{_hex()}.json", hours_ago=1)]
    _insert(jid, status="done", finished_hours_ago=3,
            view_url=f"/tools/meeting-summary/?job={jid}", upload_id=uid)

    stats = retention.collect_stats()
    assert stats["jobs"]["files"] == len(kept) + 1, stats["jobs"]
    assert stats["temp"]["files"] == len(stray), stats["temp"]
    assert stats["jobs"]["size_mb"] > 0, "作業結果檔還是 0 MB"
    total = sum(p.stat().st_size for p in kept + stray + [legacy]) / 1024 / 1024
    assert abs(stats["jobs"]["size_mb"] + stats["temp"]["size_mb"] - total) < 1e-9, \
        "兩列加起來要剛好是全部 —— 不可以重複計算或漏算"
    assert stats["jobs"]["oldest_hours"] == pytest.approx(3, abs=0.1)


def test_usage_follows_the_job_retention(data_dir):
    """過了作業保留期的作業，它的檔案就不算作業的了（照暫存的保留期清）。"""
    from app.core import retention

    jid, uid = _hex(), _hex()
    _touch(data_dir / "temp" / f"ms_{uid}_result.json", hours_ago=30)
    _insert(jid, status="done", finished_hours_ago=30,
            view_url=f"/tools/meeting-summary/?job={jid}", upload_id=uid)
    stats = retention.collect_stats()
    assert stats["jobs"]["files"] == 0
    assert stats["temp"]["files"] == 1


def test_the_retention_page_says_what_each_row_counts(admin_session):
    c, *_ = admin_session
    r = c.get("/admin/retention")
    assert r.status_code == 200, r.text[:500]
    html = r.text
    for key in ("temp", "jobs"):
        assert f'data-hint="{key}"' in html, f"「{key}」那一列沒有說明算的是什麼"
    assert "結果檔、「開啟」要讀的資料與歸屬紀錄" in html


# ---------------- 清理只有一條路 ----------------

def test_the_dead_job_manager_cleanup_is_gone():
    """`cleanup_expired()` 用的期限（6 小時）跟管理頁的保留期對不上，而且會刪
    作業紀錄（管理頁是 30 天）—— 接上的話「我的作業」的紀錄 6 小時後就消失。"""
    from app.config import settings
    from app.core.job_manager import JobManager
    assert not hasattr(JobManager, "cleanup_expired")
    assert not hasattr(settings, "job_ttl_seconds")


def _calls(name: str) -> list[str]:
    hits = []
    for p in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                callee = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else "")
                if callee == name:
                    hits.append(f"{p.relative_to(ROOT).as_posix()}:{fn.name}")
                # `asyncio.to_thread(_ret._sweep_temp_dir, …)` —— 當參數傳進去的也算
                for a in n.args:
                    if isinstance(a, ast.Attribute) and a.attr == name:
                        hits.append(f"{p.relative_to(ROOT).as_posix()}:{fn.name}")
    return sorted(set(hits))


def test_job_files_are_cleaned_in_one_place():
    """**清作業檔案的地方要先全部列出來**（CLAUDE.md）—— 只能是這幾個。

    * 暫存區 / 作業結果檔：`retention._sweep_temp_dir`，由 6 小時排程
      （`sweep_all`）與 `main` 的 30 分鐘迴圈呼叫。
    * 作業紀錄：`job_store.delete_older_than`，只由 `retention` 呼叫。
    """
    assert _calls("_sweep_temp_dir") == [
        "app/core/retention.py:sweep_all",
        "app/main.py:_sweep_temp_files_loop",
    ]
    assert _calls("delete_older_than") == ["app/core/retention.py:_sweep_job_records"]
