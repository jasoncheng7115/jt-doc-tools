# 資安政策 / OWASP Top 10 (2025) 對照

本文件說明 jt-doc-tools 的資安設計、OWASP Top 10 (2025) 各分類覆蓋方式、與漏洞回報管道。

## 漏洞回報

請開 GitHub issue 標 `security` label，或 email 給專案維護者（不要在公開 issue 揭露 PoC 細節）。

> 截至 v1.5.3，本專案無已知未修補的高 / 中危漏洞：
> - **依賴 CVE**：python-multipart / Pillow / Starlette 5 個 High CVE 在 v1.5.3 已 bump 修補，Dependabot alerts 全清。
> - **CodeQL Critical（Partial SSRF）**：v1.5.3 在 `app/core/llm_client.py:_validate_llm_base_url` 加 URL allowlist + 雲端 metadata host 黑名單,並補 27 個 regression test。
> - **CodeQL High（Path Injection）**：v1.5.3 補 12 個 endpoint 的 `upload_owner.require()` ACL（`pdf_pageno/thumb` / `pdf_pages/thumb` / `pdf_extract_images/page-thumb` 等),並寫 `tests/test_path_traversal_audit.py` AST 結構審計避免回歸。

## 設計原則

1. **預設安全（secure by default）**：全新安裝預設 backend=off 為單機模式，但啟用認證後一律 `default-deny`（沒角色 = 看不到任何工具）。
2. **職責分離（separation of duties）**：admin 與 auditor 兩個內建帳號權限完全切開（v1.5.0），admin 看不到 user 隱私資料，auditor 看不到設定區。
3. **最小權限**：6 個內建角色（admin / clerk / finance / sales / legal-sec / default-user / auditor）依工作需要分配工具，可細到每位 user / 群組 / OU 層級。
4. **凡事留痕**：登入 / 登出 / 失敗 / 鎖定 / 設定變更 / 工具呼叫 / 上傳檔名全部寫 audit log，async + WAL 不影響服務效能，可轉送外部 SIEM。
5. **不上雲**：所有檔案處理只發生在客戶自己伺服器，不外送任何資料；唯一可選的外部呼叫是管理員自行設定的 LDAP / AD 認證或 LLM (Ollama，預設關閉)。

## OWASP Top 10 (2025) 對照

> 2025 版主要變動：A02 從 Cryptographic Failures 換成 Security Misconfiguration（升 #2）；A03 改為 **Software Supply Chain Failures**（涵蓋舊版 A06）；A05 Injection 把 XSS 整合進來；新增 **A10 Mishandling of Exceptional Conditions**（取代舊版 SSRF — SSRF 併入 A06 Insecure Design）。

### A01:2025 Broken Access Control —— 未授權存取

- RBAC + `require_admin` / `require_login` / `require_tool` 三層 decorator 守 endpoint
- `upload_owner` ACL：`<temp>/.owners/<id>.json` sidecar 記錄 owner，跨 user 拿到 upload_id 也下載不到別人檔案
- `safe_paths.safe_join()` 強制檔名 ASCII allowlist + `relative_to()` containment check，防 path traversal 與 symlink escape
- 稽核員強制隔離：`effective_tools()` 對 auditor 永遠回 `set()`，群組授權再多也無效（v1.5.0）
- admin 看不到 user 隱私 4 頁（上傳記錄、表單填寫、用印簽名、浮水印歷史），URL 直連也回 403

### A02:2025 Security Misconfiguration —— 設定漏洞

- Security headers middleware：CSP、X-Content-Type-Options、X-Frame-Options、Referrer-Policy、Permissions-Policy、HSTS（HTTPS 才加）
- CSP 規則：`default-src 'self'` + `connect-src 'self'`（阻 SSRF-via-browser）+ `object-src 'none'` + `frame-ancestors 'self'` + `base-uri 'self'` + `form-action 'self'`
- 預設 backend=off（單機模式），啟用認證後一律 default-deny（沒角色 = 看不到任何工具）
- 內建帳號 SEED 保護：`jtdt-admin` / `jtdt-auditor` 不可從 web UI 刪除或改 role
- `jtdt update` 升級時 snapshot `auth_settings.json`，整個流程結束後若被改動會自動還原並警告

### A03:2025 Software Supply Chain Failures —— 依賴 / 供應鏈

- 全部依賴在 `pyproject.toml` 標明確版本範圍，`uv.lock` 鎖死可重現安裝
- GitHub Dependabot 每週一台北 09:00 自動掃 CVE,發 PR 升級版本
- `install.ps1` 對 `WinSW.exe` 做 SHA256 pinning（與 `app/cli.py` 兩處 source code 同步）
- 一行安裝指令全走 HTTPS（GitHub raw + `cdn.jsdelivr.net`）
- `install.sh` / `install.ps1` 開頭 preflight 三個 host（`github.com` / `cdn.jsdelivr.net` / `astral.sh`），不通 8 秒內 fail-fast

### A04:2025 Cryptographic Failures —— 弱加密 / 明文密碼

- 密碼用 stdlib `scrypt`（`N=2^17, r=8, p=1, 32 byte salt`）
- Session cookie：`HttpOnly` + `SameSite=Lax` + `Secure`（HTTPS 自動加）
- session token 在 DB 內存 sha256 hash,外洩也無法倒推原 token
- `auth_settings.json`（含 LDAP service password）chmod `0o600`
- TOTP secret 用 `pyotp.random_base32()` 產 32-char base32

### A05:2025 Injection（含 XSS）—— SQL / 模板 / XSS 注入

- 全部 SQL 走 sqlite3 `?` 參數綁定,zero string concatenation with user input
- Jinja2 預設 autoescape（FastAPI Jinja2Templates 預設開啟）
- 動態 JS render 額外跑一次 `escapeHtml()`
- 唯一兩處 f-string in `execute()` 是組固定欄位的 `WHERE` 子句,user 值仍透過 `params` tuple 綁定
- regression test：`test_a03_xss_in_login_username_escaped` 等

### A06:2025 Insecure Design（含 SSRF）—— 設計缺陷 / SSRF

- 職責分離：admin / auditor 兩個內建帳號權限完全切開
- 內建 SEED 帳號（`jtdt-admin` / `jtdt-auditor`）權限固定不可改
- 啟動時 `enforce_auditor_isolation` 自動修正 dirty DB,避免升級殘留錯誤狀態
- 三平台行為一致（Linux / macOS / Windows）
- **無任何 endpoint 接收 user 提供的 URL 並向外發 request**（SSRF 防護）
- 唯一外部呼叫是 admin 自行設定的 LDAP server（預設關閉）+ LLM/Ollama（預設關閉）
- regression test 掃 source code 確認無新進 SSRF 寫法

### A07:2025 Authentication Failures —— 認證薄弱

- 密碼 min 8 / max 256 chars 驗證
- 失敗 5 次自動鎖 15 分鐘（per-user + per-IP 雙計數）
- TOTP 2FA（RFC 6238,`pyotp`）；稽核員強制啟用,admin 可開「全員強制 2FA」
- session 失效時間可設（預設 7 天,「remember me」勾選後 30 天）
- 改密碼或重置 TOTP 會撤銷該 user 所有現有 session
- admin 可在 web UI 解鎖個別 user,或一鍵清除所有鎖定

### A08:2025 Software & Data Integrity Failures —— 程式 / 資料完整性

- `install.ps1` 對 `WinSW.exe` 做 SHA256 pinning
- 不執行 user 提供的 code（無 `eval` / `pickle` / `exec` on user data）
- 沒用任何未簽章的第三方 binary
- SQL migration 全部前進不破壞（v6 / v7 都是 `ALTER TABLE ADD COLUMN ... DEFAULT`）
- audit log 結構不可從 web UI 刪除

### A09:2025 Logging & Alerting Failures —— 看不到攻擊

- `audit_db`（SQLite WAL + async writer queue）記下每個關鍵 event：
  - 認證類：login / logout / lockout / 密碼變更 / 2FA 變更
  - 權限類：role_update / user_create / user_delete / pwd_reset / 2fa_reset / lockouts_clear_all / audit_seed_create
  - 行為類：tool_invoke / file_upload / settings_change / auditor_view
  - 異常類：log forwarding 失敗也寫一筆
- 預設保留 90 天
- 可轉送 syslog / CEF / GELF 給 SIEM（Splunk / Graylog / ArcSight）

### A10:2025 Mishandling of Exceptional Conditions —— 例外處理不當

- 所有外部 I/O（subprocess / 網路 / 檔案）有 timeout 並 catch
- 錯誤訊息不洩漏 stack trace 給 user（只給友善中文訊息,stack trace 寫 server log）
- FastAPI exception handler 統一 wrap 4xx / 5xx
- audit log 失敗不阻塞 request（best-effort,避免 logging 故障 DoS 主流程）
- `jtdt update` / `install` 任一步失敗自動 rollback + 還原服務
- 缺 LibreOffice / Tesseract / 字型時有友善提示,不會 crash

## 自動化驗證

### 本機 / CI

每次發版前必跑：

```bash
uv run pytest tests/test_owasp_top10.py
```

15 個 OWASP regression case 全數綠燈才能發版。完整測試清單見 `TEST_PLAN.md` §6.13 與 §7。

### GitHub 平台層原生掃描

| 工具 | 偵測內容 | 啟用方式 |
|---|---|---|
| **Dependabot alerts + updates** | 已知 CVE 在 Python 依賴 + GitHub Actions | repo Settings → Code security → 開啟「Dependabot alerts」+「Dependabot security updates」。`.github/dependabot.yml` 定義每週一台北時間 09:00 自動掃 |
| **CodeQL code scanning** | SAST：SQL injection / XSS / path traversal / command injection / SSRF / insecure deserialization 等 50+ 規則 | `.github/workflows/codeql.yml` 已建好；每次 push to main、PR、每週一台北時間 09:00 自動跑。掃 Python + JavaScript |
| **Secret scanning + push protection** | AWS / GitHub / SSH key 等 200+ token pattern；commit 推到 GitHub 前就擋 | repo Settings → Code security → 開啟「Secret scanning」+「Push protection」（public repo 免費） |
| **Private vulnerability reporting** | 安全研究員私下回報管道（不公開揭露 PoC） | repo Settings → Code security → 開啟「Private vulnerability reporting」。後續 SECURITY.md 上端會多一顆「Report a vulnerability」按鈕 |

掃描結果會出現在 repo 的 **Security** tab。CodeQL alerts 直接標 source line，open 一條 alert 等同 open 一個漏洞 ticket。

## 額外資安特性

- **緊急復原 CLI**（防鎖死）：`jtdt auth show / disable / set-local`、`jtdt reset-password <user>`、`jtdt audit-user create`，全部離線可跑、不需服務啟動，且 admin（含 LDAP user）忘記密碼或設錯設定不會掉資料
- **`jtdt update` 防呆**：snapshot auth_settings.json bytes，整個升級流程結束後若被改就自動還原 + 警告
- **schema migrations 全部前進不破壞**：v6 / v7 都是 ALTER TABLE ADD COLUMN with DEFAULT，既有資料不受影響
- **admin 看不到 user 隱私 4 頁**（v1.5.0）：上傳檔案記錄、表單填寫 / 用印簽名 / 浮水印歷史只開給 auditor 角色 — 即使 admin 用 URL 直連也回 403
- **內建帳號保護**：`jtdt-admin` (`is_admin_seed=1`) 與 `jtdt-auditor` (`is_audit_seed=1`) 不可從 web UI 刪除、不可從權限矩陣改 role / tool；只能透過 CLI 重設密碼
- **稽核員行為留痕**：每次 view 寫 `auditor_view` event（path + method + IP）；UI 沒提供刪除 audit_events 的端點，稽核員自己也刪不掉自己的紀錄
- **upload_owner ACL**：14 個處理 user 上傳的工具全裝 sidecar JSON 檢查（`<temp>/.owners/<upload_id>.json`），跨 user 拿到 upload_id（從 URL / log / 截圖洩漏）也下載不到別人檔案
