# 日常運維指南

## `jtdt` 指令

安裝完成後會在系統 PATH 加入 `jtdt`：

| 指令 | 說明 |
|------|------|
| `jtdt status`        | 顯示版本、服務狀態、安裝路徑 |
| `jtdt start`         | 啟動服務 |
| `jtdt stop`          | 停止服務 |
| `jtdt restart`       | 重啟服務 |
| `jtdt logs -f`       | 即時看 log |
| `jtdt open`          | 用瀏覽器開啟介面 |
| `sudo jtdt update`<sup>†</sup>   | 升級到最新版（會自動備份資料） |
| `sudo jtdt uninstall`<sup>†</sup> | 移除（資料保留），加 `--purge` 連同資料一起刪 |
| `sudo jtdt bind <addr>:<port>`<sup>†</sup> | 改 listen 位置（詳見「監聽位置」） |
| `sudo jtdt auth show`<sup>†</sup> | 顯示認證後端設定 |
| `sudo jtdt auth disable`<sup>†</sup> | 緊急關掉認證 |
| `sudo jtdt auth set-local`<sup>†</sup> | 切回本機帳號模式 |
| `sudo jtdt reset-password <user>`<sup>†</sup> | 重設使用者密碼 |
| `sudo jtdt audit-user create <name>`<sup>†</sup> | 建立稽核員帳號 |

<sup>†</sup> Linux / macOS 用 `sudo`；Windows 沒有 `sudo`，請改成「以系統管理員身分執行 PowerShell」後跑 `jtdt update` / `jtdt uninstall`。

## 升級流程

`jtdt update` 自動：
1. 停服務
2. 備份 `data/` (最近 3 份保留)
3. `git pull` 從 GitHub
4. `uv sync` 同步依賴
5. 重啟
6. 健康檢查

降版會被拒（避免毀資料）。失敗會自動 rollback。

## 反向代理（HTTPS）— 非本機存取的**強制**安全要求

> ⚠️ **只要不是「本機單人」使用（任何網路 / 多人 / 內網其他電腦 / 對外）→ 一律放在
> nginx（或 Caddy）反向代理 + HTTPS 後面,絕不要把 `:8765` 直接對網路開放。**
>
> 應用程式預設只綁 `127.0.0.1:8765`（純 HTTP、無 TLS）。直接 `jtdt bind 0.0.0.0`
> 對網路開放等於明文傳輸帳號密碼 / 文件內容,且少了 TLS 終結、HSTS、憑證、速率
> 限制等防護。**正確做法**:`:8765` 維持只聽 `127.0.0.1`,由同機 / 內網的 nginx
> 反向代理對外提供 HTTPS。下面是含資安設定的完整範例。

### nginx（建議設定）

重點：① `server_tokens off`（不洩版本）② 傳 `X-Forwarded-Proto`（後端據此設 Secure
cookie + HSTS）③ 安全標頭（CSP/HSTS/X-Frame-Options…）由**後端 app 統一設定**,nginx
**不要**再 `add_header` 一次（否則重複標頭）。

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name docs.example.com;

    ssl_certificate     /etc/letsencrypt/live/docs.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/docs.example.com/privkey.pem;

    # 不要外洩 nginx 版本號（ZAP「Server Leaks Version Information」）
    server_tokens off;

    # HSTS：jt-doc-tools 應用層已自動設 Strict-Transport-Security（會依
    # X-Forwarded-Proto 判斷 https）。**不要在 nginx 再 add_header 一次**，
    # 否則回應會出現兩個 HSTS 標頭（ZAP「Strict-Transport-Security Multiple
    # Header Entries」）。要在 nginx 統一管也行，但只能擇一來源。

    # 必設：上傳大檔需要
    client_max_body_size 100M;

    # 必設：LLM 工具（翻譯 / OCR 校驗 / 視覺校驗）單筆推理可能 5-15 分鐘
    # 預設 60s 會 504；建議 ≥ 900s（並跟 admin → LLM 設定的 timeout 對齊）
    proxy_read_timeout    900s;
    proxy_send_timeout    900s;
    proxy_connect_timeout 60s;

    # 翻譯回應慢慢吐 — 關 buffering 讓 client 即時看到進度
    proxy_buffering       off;

    location / {
        proxy_pass http://127.0.0.1:8765/;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Caddy

```caddyfile
docs.example.com {
    reverse_proxy 127.0.0.1:8765 {
        flush_interval -1
        transport http {
            read_timeout 900s
            write_timeout 900s
        }
    }
    request_body {
        max_size 100MB
    }
}
```

### 反向代理避坑

1. **`client_max_body_size 100M`**：上傳大檔必設
2. **必須掛根路徑** `/`（不能 `/jtdt/`）— 所有頁面用絕對路徑
3. **`proxy_read_timeout 900s`** + **`proxy_send_timeout 900s`** 一起設 — LLM 工具（翻譯 / OCR 校驗 / pdf-fill LLM review）單筆 LLM 呼叫常 5-15 分鐘。`300s` 都不夠用。
4. **`proxy_buffering off`** — 翻譯 / 校驗 streaming 友善，不會卡住等整個 response
5. WebSocket 暫時沒用，不需特別 headers

#### 504 Gateway Timeout 排錯流程

如果使用者翻譯 / OCR 校驗看到 504：

```bash
# 1. 是不是 jt-doc-tools 的 nginx 自己 timeout？
sudo grep "upstream timed out" /var/log/nginx/error.log | tail -5

# 2. 看當前設定值（必須 ≥ 900s）
sudo grep -E "proxy_read_timeout|proxy_send_timeout" /etc/nginx/sites-enabled/

# 3. 設不夠 → 加 / 改成 900s，reload
sudo nginx -t && sudo nginx -s reload
```

**多層反向代理情境**（例：你有獨立 LLM proxy 在前，jt-doc-tools 在後）：**每一層 nginx 都要設**（一層用 60s 預設整鏈就斷），且建議從外到內遞增（client → nginx_jtdt 900s → jtdt → nginx_llm 900s → LLM backend）。

**admin → LLM 設定**內也要把「Timeout（秒）」設 ≥ 900（預設 600，舊版 300）。jtdt 自己的 httpx timeout 短於 nginx 反而會先斬。

## 監聽位置

預設 `127.0.0.1:8765`（只本機）。要改：

```bash
# Linux/macOS
sudo jtdt bind 0.0.0.0:8765      # 監聽所有介面（任何 IP 都可連）
sudo jtdt bind 192.168.1.10:8080 # 只監聽特定 IP + 改 port

# Windows (以系統管理員身分執行 PowerShell)
jtdt bind 0.0.0.0:8765
```

`jtdt bind` 自動寫服務設定 + 重啟服務。

## 備份 / 還原

`data/` 目錄含所有設定 + 上傳記錄 + 簽章 / 印章 / 浮水印 asset + audit log。手動備份：

```bash
# Linux
sudo tar -czf jtdt-backup-$(date +%Y%m%d).tgz -C /var/lib jt-doc-tools/data

# 還原
sudo tar -xzf jtdt-backup-20260509.tgz -C /var/lib
sudo chown -R jtdt:jtdt /var/lib/jt-doc-tools
sudo jtdt restart
```

`jtdt update` 升級時自動 snapshot，最近 3 份保留在 `data/.backup-YYYYMMDD-HHMMSS/`。

## 排程清理

啟用認證後可在 `/admin/retention` 設定每類資料保留天數：

| 項目 | 預設 | 路徑 |
|---|---|---|
| 表單填寫歷史 | 365 天 | `data/fill_history/` |
| 用印簽名歷史 | 365 天 | `data/stamp_history/` |
| 浮水印歷史 | 365 天 | `data/watermark_history/` |
| 暫存上傳 / 工作檔 | 2 小時 | `data/temp/` |
| 稽核記錄 | 90 天 | `data/audit.sqlite` |
| Job 結果 | 24 小時 | `data/jobs/` |

排程：啟動時跑一次 + 每 6 小時跑一次。`-1` = 永久保留。
