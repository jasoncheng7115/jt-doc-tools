#!/usr/bin/env python3
"""壓力測試 — 模擬 N 個使用者同時呼叫多個 jt-doc-tools 工具。

Usage:
    python tests/stress/run_stress.py --users 5 --duration 60
    python tests/stress/run_stress.py --users 30 --duration 120 --base-url http://192.168.1.30:8765

每個 worker 在 duration 秒內輪流呼叫 SCENARIOS 內的工具 endpoint，
測量 latency / 吞吐 / 錯誤率。輸出 markdown 報告（也可 --csv 匯出）。

預設無認證 (本機 / 測試機)。如要打 auth-on 的環境用 --token <bearer>。

樣本 PDF 用 PyMuPDF on-the-fly 生成（首次跑時建立），在 tests/stress/samples/
下，gitignored。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

# Windows cp950 console 不能印 ✓ ✗ → reconfigure stdout/stderr 為 utf-8
# (Python 3.7+ 支援 reconfigure；exception 容忍即可)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAMPLE_DIR = Path(__file__).parent / "samples"

# 每個 scenario 是 dict: {name, method, path, file_field?, sample_key?, form?, json?, weight?}
# - file_field + sample_key：multipart 上傳（PDF 處理工具）
# - form：multipart 上傳時的額外 form fields
# - json：發 JSON body（純文字 / API endpoints 多半 JSON-only）
# - weight：相對權重（重型工具給 1，輕型工具給更大數字代表更頻繁呼叫）
#
# 輕型工具（< 100 ms / 呼叫）+ 重型工具（0.5-5 s / 呼叫）混合，接近真實多人
# 工作 loading。輕型 weight=3、重型 weight=1，模擬「使用者大多在做小操作」。
SCENARIOS = [
    # ---- 輕型（快） ----
    {"name": "wordcount",     "path": "/tools/pdf-wordcount/api/pdf-wordcount",
     "file_field": "file", "sample_key": "small", "weight": 3},
    {"name": "annotations",   "path": "/tools/pdf-annotations/api/pdf-annotations",
     "file_field": "file", "sample_key": "small", "weight": 3},
    {"name": "annot-strip",   "path": "/tools/pdf-annotations-strip/api/pdf-annotations-strip",
     "file_field": "file", "sample_key": "small", "form": {"mode": "all"}, "weight": 3},
    {"name": "text-deident",  "path": "/tools/text-deident/api/text-deident",
     "json": {"text": "電話 (手機) 身分證 (身分證) 統編 12345678"}, "weight": 3},
    {"name": "text-diff",     "path": "/tools/text-diff/api/text-diff",
     "json": {"text_a": "Hello world\nLine 2", "text_b": "Hello there\nLine 2"}, "weight": 3},
    # ---- 重型（慢，更接近真實工作 loading）----
    {"name": "extract-text",  "path": "/tools/pdf-extract-text/extract",
     "file_field": "file", "sample_key": "medium", "weight": 1},
    {"name": "compress-analyze", "path": "/tools/pdf-compress/analyze",
     "file_field": "file", "sample_key": "medium", "weight": 1},
]


def _scenario_sequence():
    """依 weight 展開成輪流序列，例 weight=3 重複 3 次。"""
    seq = []
    for sc in SCENARIOS:
        seq.extend([sc] * int(sc.get("weight", 1)))
    return seq


def _ensure_sample(key: str) -> Path:
    """需要時用 PyMuPDF 產生樣本 PDF。首次跑時生成 + 緩存。"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    if key == "small":
        path = SAMPLE_DIR / "small.pdf"
    elif key == "medium":
        path = SAMPLE_DIR / "medium.pdf"
    else:
        raise ValueError(f"unknown sample key {key}")
    if path.exists():
        return path
    import fitz
    doc = fitz.open()
    pages = 1 if key == "small" else 10
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_text(
            (72, 100), f"Stress test sample page {i+1}",
            fontsize=14,
        )
        page.insert_text(
            (72, 130),
            "中文測試 ABC 123 — Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 6,
            fontsize=10,
        )
    doc.save(str(path))
    doc.close()
    return path


async def _one_request(client, base_url, scenario, token):
    name = scenario["name"]
    path = scenario["path"]
    url = base_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    files = None
    data = None
    json_body = None
    if scenario.get("file_field") and scenario.get("sample_key"):
        sample_path = _ensure_sample(scenario["sample_key"])
        with sample_path.open("rb") as f:
            content = f.read()
        files = {scenario["file_field"]: (sample_path.name, content, "application/pdf")}
        data = scenario.get("form") or None
    elif scenario.get("json"):
        json_body = scenario["json"]
    else:
        data = scenario.get("form") or None

    t0 = time.perf_counter()
    err = ""
    status = 0
    try:
        if files:
            resp = await client.post(url, files=files, data=data, headers=headers, timeout=120.0)
        elif json_body is not None:
            resp = await client.post(url, json=json_body, headers=headers, timeout=120.0)
        else:
            resp = await client.post(url, data=data, headers=headers, timeout=120.0)
        status = resp.status_code
        if status >= 400:
            err = f"HTTP {status}"
    except httpx.TimeoutException:
        err = "timeout"
    except Exception as e:
        err = type(e).__name__ + ": " + str(e)[:60]
    elapsed = time.perf_counter() - t0
    # 3xx redirect 通常表示「未登入被導去 /login」(auth-on 環境沒帶 token)，
    # 不算成功 — 否則整段壓測都是空 redirect 不是真實工具呼叫。
    is_redirect_to_login = (300 <= status < 400)
    if is_redirect_to_login and not err:
        err = f"HTTP {status} (redirect, 多半是 auth 沒帶 token)"
    return {"tool": name, "ok": (status >= 200 and status < 300),
            "elapsed_ms": round(elapsed * 1000, 1), "status": status, "err": err}


async def _worker(wid, client, base_url, stop_at, results, token, sequence):
    n = 0
    while time.time() < stop_at:
        sc = sequence[(wid + n) % len(sequence)]   # 不同 worker 起點錯開避免同步
        r = await _one_request(client, base_url, sc, token)
        r["worker"] = wid
        results.append(r)
        n += 1


async def _login(client, base_url, login_str):
    """user[:pass[@realm]] → POST /login → session cookie 設進 client cookie jar。
    回傳 True 表示登入成功；False 表示失敗。"""
    realm = ""
    if "@" in login_str:
        login_str, realm = login_str.rsplit("@", 1)
    if ":" in login_str:
        user, pw = login_str.split(":", 1)
    else:
        user, pw = login_str, ""
    print(f"  Login as:  {user}{'@' + realm if realm else ''}", flush=True)
    r = await client.post(
        base_url + "/login",
        data={"username": user, "password": pw, "realm": realm, "next": "/"},
        timeout=15.0,
        follow_redirects=False,
    )
    # 成功登入回 302 redirect 到 next，session cookie 在 Set-Cookie 標頭。
    # 失敗回 200 含 login form。
    if r.status_code == 302 and any(c.name == "session" for c in client.cookies.jar):
        print(f"  ✓ login ok (got session cookie)")
        return True
    print(f"  ✗ login failed: HTTP {r.status_code}", flush=True)
    return False


async def run(args):
    base_url = args.base_url.rstrip("/")
    print(f"\n=== jt-doc-tools 壓力測試 ===")
    print(f"  Target:   {base_url}")
    print(f"  Users:    {args.users}")
    print(f"  Duration: {args.duration}s")
    print(f"  Tools:    {len(SCENARIOS)} ({', '.join(s['name'] for s in SCENARIOS)})")
    print(f"  Pre-warm samples...", flush=True)
    for sc in SCENARIOS:
        if sc.get("sample_key"):
            _ensure_sample(sc["sample_key"])

    # health check
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(base_url + "/healthz", timeout=5.0)
            if r.status_code != 200:
                print(f"  ⚠ /healthz returned {r.status_code}")
            else:
                print(f"  ✓ healthz ok")
    except Exception as e:
        print(f"  ✗ cannot reach {base_url}: {e}", file=sys.stderr)
        sys.exit(2)

    results: list[dict] = []
    stop_at = time.time() + args.duration
    sequence = _scenario_sequence()
    limits = httpx.Limits(max_connections=args.users * 2, max_keepalive_connections=args.users)
    async with httpx.AsyncClient(limits=limits, follow_redirects=False) as client:
        if args.login:
            ok = await _login(client, base_url, args.login)
            if not ok:
                print(f"  ✗ login 失敗，無法繼續壓測", file=sys.stderr)
                sys.exit(3)
        t0 = time.perf_counter()
        tasks = [_worker(i, client, base_url, stop_at, results, args.token, sequence)
                 for i in range(args.users)]
        await asyncio.gather(*tasks)
        wall = time.perf_counter() - t0

    _report(results, wall, args)


def _percentile(values, pct):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _report(results, wall, args):
    print(f"\n=== 結果（總計 {len(results)} 次請求 / {wall:.1f}s wall） ===")
    if not results:
        print("沒有請求成功完成 — 服務可能掛掉")
        return

    by_tool = defaultdict(list)
    for r in results:
        by_tool[r["tool"]].append(r)

    print(f"\n總體吞吐：{len(results)/wall:.1f} req/s")
    total_ok = sum(1 for r in results if r["ok"])
    print(f"成功率：  {total_ok}/{len(results)} = {100*total_ok/len(results):.1f}%")

    print(f"\n| 工具          | 次數 | 成功 | 錯誤 | p50 ms | p95 ms | p99 ms | max ms |")
    print(f"|---------------|------|------|------|--------|--------|--------|--------|")
    for tool, rows in sorted(by_tool.items()):
        ok = [r["elapsed_ms"] for r in rows if r["ok"]]
        bad = sum(1 for r in rows if not r["ok"])
        if ok:
            p50 = _percentile(ok, 50)
            p95 = _percentile(ok, 95)
            p99 = _percentile(ok, 99)
            mx = max(ok)
        else:
            p50 = p95 = p99 = mx = 0
        print(f"| {tool:13s} | {len(rows):4d} | {len(ok):4d} | {bad:4d} | {p50:6.0f} | {p95:6.0f} | {p99:6.0f} | {mx:6.0f} |")

    # Error breakdown
    errs = defaultdict(int)
    for r in results:
        if not r["ok"]:
            errs[r["err"] or f"HTTP {r['status']}"] += 1
    if errs:
        print("\n錯誤分類：")
        for k, v in sorted(errs.items(), key=lambda x: -x[1]):
            print(f"  {v:4d}× {k}")

    if args.csv:
        out = Path(args.csv)
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["tool", "worker", "ok", "elapsed_ms", "status", "err"])
            w.writeheader()
            w.writerows(results)
        print(f"\nCSV 匯出：{out}")


def main():
    ap = argparse.ArgumentParser(description="jt-doc-tools 壓力測試")
    ap.add_argument("--base-url", default="http://127.0.0.1:8765",
                    help="目標 base URL（預設 http://127.0.0.1:8765）")
    ap.add_argument("--users", type=int, default=5,
                    help="並行使用者數（建議跑 1 / 5 / 10 / 30 / 50）")
    ap.add_argument("--duration", type=int, default=60,
                    help="持續秒數（預設 60s）")
    ap.add_argument("--token", default="", help="Bearer token（API token，目前僅 admin/api 端點 accept）")
    ap.add_argument("--login", default="", help="登入帳密 user[:pass[@realm]]，跑前先 POST /login 換 session cookie，給 auth-on 環境用")
    ap.add_argument("--csv", default="", help="CSV 匯出路徑（每筆請求一列）")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
