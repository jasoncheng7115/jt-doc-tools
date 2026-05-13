# 壓力測試（Stress Testing）

驗證「**多人同時用**」吞吐 / 延遲 / 錯誤率，找潛在瓶頸（thread 餓死、connection pool 不足、memory leak、reverse proxy timeout 等）。

## 工具

`tests/stress/run_stress.py` — async httpx，模擬 N 個使用者並行打 API。詳細跑法見 `tests/stress/README.md`。

## 涵蓋的工具場景

混合**輕型**（< 100 ms）與**重型**（0.5-5 s）模擬真實使用情境：

| 工具 | 類型 | 權重 | Endpoint |
|---|---|---|---|
| `wordcount` | 輕 | 3× | `POST /tools/pdf-wordcount/api/pdf-wordcount` |
| `annotations` | 輕 | 3× | `POST /tools/pdf-annotations/api/pdf-annotations` |
| `annot-strip` | 輕 | 3× | `POST /tools/pdf-annotations-strip/api/pdf-annotations-strip` |
| `text-deident` | 輕 | 3× | `POST /tools/text-deident/api/text-deident` |
| `text-diff` | 輕 | 3× | `POST /tools/text-diff/api/text-diff` |
| `extract-text` | 重 | 1× | `POST /tools/pdf-extract-text/extract` |
| `compress-analyze` | 重 | 1× | `POST /tools/pdf-compress/analyze` |

權重 3× / 1× 表示「使用者大多在做小操作，偶爾跑重的」。新增工具改 `tests/stress/run_stress.py` 的 `SCENARIOS`。

## 跑法

```bash
# 1 user 基準（測單請求 latency）
uv run python tests/stress/run_stress.py --users 1 --duration 60

# 階梯式遞增，觀察吞吐曲線斷點
for n in 1 5 10 30 50; do
  echo "=== $n users ==="
  uv run python tests/stress/run_stress.py --users $n --duration 60
done

# 打遠端
uv run python tests/stress/run_stress.py --users 10 --duration 60 \
    --base-url http://192.168.1.30:8765

# auth-on 環境（必須帶 token，否則所有請求 302 → /login）
uv run python tests/stress/run_stress.py --users 10 --duration 60 \
    --base-url https://doc.jason.tools \
    --token jtdt_xxxxxxxxxxxxxx

# CSV 匯出（給 Grafana / Excel 分析每筆請求）
uv run python tests/stress/run_stress.py --users 10 --duration 60 --csv stress.csv
```

## 樣本 PDF

首次跑時用 PyMuPDF 自動生成 `tests/stress/samples/small.pdf`（1 頁 A4）與 `medium.pdf`（10 頁），已 gitignore。

## 驗收門檻（建議）

| 並行 | 吞吐下限 | p95 延遲上限 | 成功率下限 |
|---|---|---|---|
| 1 user | — | < 500 ms | 100% |
| 5 users | > 5 req/s | < 800 ms | 100% |
| 10 users | > 8 req/s | < 1500 ms | ≥ 99% |
| 30 users | > 15 req/s | < 4 s | ≥ 98% |
| 50 users | > 20 req/s | < 8 s | ≥ 95% |

數值依機器配置調整。**最重要：成功率不能掉太多** — 50 users 下 < 95% 表示有 thread 餓死 / connection pool 撐不住 / memory 爆，要找 root cause。

## 排查方向

- **吞吐隨 users 線性增長 → users>20 後不增反減**：CPU / I/O 瓶頸，看 `htop` / Activity Monitor
- **p99 突然爆掉**：某個工具 stall（OCR 載 PyTorch、soffice 啟動）— 看 server log 哪支撞到
- **大量 timeout**：reverse proxy（nginx）的 `proxy_read_timeout` 設定，或 uvicorn workers 不足
- **HTTP 503**：服務暫時無回應（uvicorn worker 全忙），考慮加 workers
- **HTTP 302 redirect 全部成功（吞吐爆表）**：auth-on 環境沒帶 token，全是空 redirect，不是真實壓測 — 帶 `--token` 重跑

## 發版前檢查

- [ ] 1 user 跑過，p95 < 500 ms 100% 成功
- [ ] 5 users 跑過，吞吐有上升、成功率 100%
- [ ] 10 users 跑過，p95 < 1500 ms、成功率 ≥ 99%
- [ ] 30 users 跑過，成功率 ≥ 98%
- [ ] 50 users 跑過，成功率 ≥ 95%
- [ ] 任一階段成功率突降 → 看 server log 找 root cause

## 歷史結果記錄

每次大改版（特別是改 save_queue / pdf-editor flatten / 新增工具）後記錄一次，便於回歸比對。

| 日期 | 版本 | 機器 | 設定 | 摘要 |
|---|---|---|---|---|
| 2026-05-13 | v1.7.50 | mac dev | 1 user / 8s | 139 req/s, p95 20ms, 100% |
| 2026-05-13 | v1.7.50 | mac dev | 5 users / 10s | 193 req/s, p95 65ms, 100% |
