# 壓力測試 — `tests/stress/run_stress.py`

模擬 N 個使用者並行呼叫多個工具的 API，量化「**多人同時用**」的吞吐 / 延遲 / 錯誤率。

## 跑法

```bash
# 1 user / 60s（基準）
uv run python tests/stress/run_stress.py --users 1 --duration 60

# 5 users
uv run python tests/stress/run_stress.py --users 5 --duration 60

# 10 users
uv run python tests/stress/run_stress.py --users 10 --duration 60

# 30 users
uv run python tests/stress/run_stress.py --users 30 --duration 90

# 50 users（觀察極限）
uv run python tests/stress/run_stress.py --users 50 --duration 120

# 打遠端
uv run python tests/stress/run_stress.py --users 10 --duration 60 \
    --base-url http://192.168.1.30:8765

# auth-on 環境
uv run python tests/stress/run_stress.py --users 10 --duration 60 \
    --base-url https://doc.jason.tools --token jtdt_xxxxxxxxxxxxxx

# CSV 匯出（給 Grafana / Excel 分析每筆請求）
uv run python tests/stress/run_stress.py --users 10 --duration 60 --csv stress.csv
```

## 涵蓋的工具

每個 worker 在 duration 秒內輪流呼叫：

| 工具 | Endpoint | Sample |
|---|---|---|
| `wordcount` | `POST /tools/pdf-wordcount/api/pdf-wordcount` | small.pdf (1 頁 A4) |
| `annotations` | `POST /tools/pdf-annotations/api/pdf-annotations` | small.pdf |
| `annot-strip` | `POST /tools/pdf-annotations-strip/api/pdf-annotations-strip` | small.pdf |
| `text-deident` | `POST /tools/text-deident/api/text-deident` | 純文字（電話 / 身分證 / 統編） |
| `text-diff` | `POST /tools/text-diff/api/text-diff` | 純文字 |

**SCENARIOS 在 `run_stress.py` 頂端，要加入更多工具就改這個 list。**

## 樣本 PDF 自動生成

首次跑時用 PyMuPDF 在 `tests/stress/samples/` 自動生成 `small.pdf` / `medium.pdf`，已 gitignore。

## 報告範例

```
=== jt-doc-tools 壓力測試 ===
  Target:   http://127.0.0.1:8765
  Users:    10
  Duration: 60s
  Tools:    5 (wordcount, annotations, annot-strip, text-deident, text-diff)
  ✓ healthz ok

=== 結果（總計 1247 次請求 / 60.2s wall） ===
總體吞吐：20.7 req/s
成功率：  1245/1247 = 99.8%

| 工具          | 次數 | 成功 | 錯誤 | p50 ms | p95 ms | p99 ms | max ms |
|---------------|------|------|------|--------|--------|--------|--------|
| annot-strip   |  250 |  250 |    0 |    180 |    420 |    810 |   1200 |
| annotations   |  249 |  249 |    0 |    160 |    380 |    640 |    920 |
| text-deident  |  250 |  250 |    0 |     12 |     28 |     45 |     80 |
| text-diff     |  249 |  248 |    1 |     18 |     45 |     82 |    150 |
| wordcount     |  249 |  248 |    1 |    220 |    490 |    910 |   1450 |

錯誤分類：
   2× HTTP 503
```

## 驗收門檻（建議）

| 並行 | 吞吐下限 | p95 延遲上限 | 成功率下限 |
|---|---|---|---|
| 1 user | — | < 500ms | 100% |
| 5 users | > 5 req/s | < 800ms | 100% |
| 10 users | > 8 req/s | < 1500ms | ≥ 99% |
| 30 users | > 15 req/s | < 4s | ≥ 98% |
| 50 users | > 20 req/s | < 8s | ≥ 95% |

> 數值依機器配置調整（DGX Spark 跟一般 i5 差距很大）。**最重要的指標是「成功率不能掉太多」**——如果 50 users 下成功率 < 95% 表示有 thread 餓死、connection pool 撐不住、或記憶體爆掉，要找 root cause。

## 排查方向

- **吞吐隨 users 線性增長 → users>20 後不增反減**：CPU / I/O 瓶頸，看 `htop` / Activity Monitor
- **p99 突然爆掉**：某個工具 stall（OCR 載 PyTorch、soffice 啟動）— 看 server log 哪支撞到
- **大量 timeout**：reverse proxy（nginx）的 timeout 設定，或 uvicorn workers 不足
- **HTTP 503**：服務暫時無回應（uvicorn worker 全忙），考慮加 workers
