# 認證 / 角色 / 稽核合規

啟用認證後（單機模式不需要這段）支援職責分離（separation of duties）。

## 角色一覽

| 角色 | 用途 | 工具權限 | 設定權限 | 可看 audit / system-status | **可看 user 隱私 4 頁** | 強制 2FA |
|---|---|---|---|---|---|---|
| `admin` | 系統管理員 | ✓ 全部 | ✓ 全部 | ✓ | **✗（v1.5.0 強化）** | 可選 |
| **`auditor`** 稽核員 | 合規稽核 | ✗ 不可使用任何工具 | ✗ | ✓ 唯讀 | ✓ 唯讀 | **強制** |
| `default-user` 一般使用者 | 日常文書 | ✓（不含表單填寫 / 用印與簽名） | ✗ | ✗ | ✗ | 可選 |
| `clerk` 文管 | 文件管理 | 部分（合併 / 拆分 / 轉檔等） | ✗ | ✗ | ✗ | 可選 |
| `finance` 財務 | 財務 | default + 表單 / 用印 / 浮水印 / 加密 | ✗ | ✗ | ✗ | 可選 |
| `sales` 業務 | 業務 | default + 表單 / 用印 / 浮水印 | ✗ | ✗ | ✗ | 可選 |
| `legal-sec` 法務資安 | 法務資安 | default + 去識別化 / 隱藏掃描 / 加密解密 | ✗ | ✗ | ✗ | 可選 |

**「user 隱私 4 頁」**：上傳檔案記錄、表單填寫歷史、用印簽名歷史、浮水印歷史。這 4 頁含 user 真實上傳 / 填寫的內容，**v1.5.0 起連 admin 都看不到**，只有稽核員可看。

## 內建帳號：`jtdt-admin` 與 `jtdt-auditor`

啟用認證後系統會自動維護兩個內建帳號，**用途分開、不可混用**：

| 項目 | `jtdt-admin` | `jtdt-auditor` |
|---|---|---|
| 何時建立 | 第一次啟用認證時，由 `/setup-admin` 頁面建立 | 啟用認證時自動建立（v1.5.0+） |
| 角色 | `admin`（管理員） | `auditor`（稽核員） |
| 預設密碼 | 您建立時設定 | **無**（NULL）— 必須先 `sudo jtdt reset-password jtdt-auditor` 才能登入 |
| 強制 2FA | 否（可選） | **是**（不可停用） |
| 可使用工具 | ✓ 全部 | ✗ 完全不可 |
| 可改設定 | ✓ 全部 | ✗ 完全不可 |
| 可看 user 隱私 4 頁 | **✗** | ✓ 唯讀 |
| 可被刪除 | ✗ 受 `is_admin_seed` 保護 | ✗ 受 `is_audit_seed` 保護 |
| 可在 `/admin/permissions` 改角色 / 工具 | ✗ 鎖住 | ✗ 鎖住 |
| 用途 | 日常維運：管理 user / 角色 / 認證設定 / 工具設定 / 升級 / 服務 | 合規稽核：查看 user 上傳了什麼 / 處理過什麼歷史 |

**為什麼分兩個內建帳號**：合規規範（ISO 27001 等）要求「管系統的人」與「看記錄的人」分離 — admin 不該偷看 user 隱私資料，稽核員不該動系統設定。任何一方都不該擁有完整存取權，這就是 separation of duties。

## 第一次設定流程（建議）

```bash
# 1. 啟用認證 + 建立 admin（在 web 設定頁 /admin/auth-settings 或 /setup-admin 操作）
#    → jtdt-admin 帳號 + jtdt-auditor 帳號自動建立

# 2. 替 jtdt-auditor 設密碼（admin 也無法看到 jtdt-auditor 的隱私頁面，所以必須先設）
sudo jtdt reset-password jtdt-auditor

# 3. 把上面設的密碼交給合規 / 稽核同仁
#    他們在 /login 登入 → 自動導向 /2fa-verify 顯示 QR
#    用 Authenticator app 掃 QR → 輸 6 碼完成首次設定
```

## 建立額外稽核員（多人合規團隊）

```bash
# 互動 prompt 輸入密碼（推薦）
sudo jtdt audit-user create alice

# 一次給密碼（自動化用）
sudo jtdt audit-user create bob --password 'StrongP@ss123' --display-name '張稽核'
```

新稽核員的密碼產生流程同上：第一次登入後自動走 `/2fa-verify` setup。

## 一般 user / admin 自助啟用 2FA

點 sidebar 上方自己的帳號名稱 → 「我的帳號」modal → **兩步驟驗證 (TOTP)** 區塊：

- **未啟用**：按「啟用 2FA」→ 掃 QR → 輸 6 碼 → 完成
- **已啟用**：可「重新生 secret」（換手機時用）或「停用 2FA」
- **稽核員角色**：強制啟用，看得到「重新生 secret」但停用按鈕被擋

下次登入會在密碼後多一步驗證碼。

## 帳號鎖定 / 解鎖（v1.5.0 加）

連錯密碼 5 次該帳號 + 來源 IP 會鎖 15 分鐘。admin 可在：

- **`/admin/users`** 找到被鎖的 user，每行旁有黃底「解鎖」按鈕
- **`/admin/auth-settings`** 點「清除所有鎖定」一鍵清光（多人撞密碼把整個辦公室 IP 鎖死時用）

## admin 替 user 重設 2FA

user 手機遺失 / 換手機時，admin 在 `/admin/users` 點該 user 旁的「重設 2FA」 → 清掉舊 secret + 撤銷所有 session → user 下次登入會重新看到 QR setup。

## 緊急復原（鎖在外面、忘密碼、認證設錯）

啟用 LDAP / AD 認證或本機認證後若不小心鎖死自己（例：AD 設定錯、admin 忘密碼、伺服器搬遷後 LDAP URI 對不上），全部可在伺服器命令列復原，**不需要重灌、不會掉資料**：

```bash
# 看目前認證狀態
sudo jtdt auth show

# 切回未啟用認證（最快解封；所有 session 失效）
sudo jtdt auth disable
sudo jtdt restart

# 改用本機帳號模式（保留既有使用者）
sudo jtdt auth set-local
sudo jtdt restart

# 重設管理員密碼（互動 prompt 輸入兩次新密碼）
sudo jtdt reset-password jtdt-admin
```

> Windows 沒有 `sudo`，請以「系統管理員身分執行 PowerShell」後直接跑 `jtdt auth disable` / `jtdt reset-password jtdt-admin`。

復原後若要重新啟用 LDAP / AD，可在 web 設定頁重新填一次參數，前述切換**不會清掉**舊的使用者 / 角色 / 權限資料（只清 session，所以舊 cookie 失效）。

## 稽核 log + log forwarding

啟用認證後所有敏感操作（登入、權限變更、工具呼叫含檔名、設定變更）都會記下，預設保留 90 天，可在 `/admin/audit` 查看 + 過濾 + 匯出 CSV。

支援轉發到 SIEM:
- syslog (UDP/TCP)
- CEF (HP/ArcSight)
- GELF (Graylog/JSON)

設定在 `/admin/log-forward`，多 destination 並行，失敗 retry 3 次後寫一筆 `audit_forward_failed` 進本機 audit。
