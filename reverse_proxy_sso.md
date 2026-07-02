# Reverse Proxy SSO（Kerberos / SPNEGO 單一登入）

讓已加入 AD 網域、且已登入 Windows 的使用者，透過前端 Nginx 以 SPNEGO/Kerberos 驗證後，
自動登入 jt-doc-tools —— 不必再輸入帳號密碼。非網域電腦（或未帶 Kerberos 標頭）的使用者，
一律回到既有的 `/login` 頁面，仍可用 Local / LDAP / AD / OIDC / SAML 登入。

這是「附加登入方式」，**不取代**既有認證後端；`jtdt-admin` 本機帳號永遠可從 `/login`
登入，作為 IdP / AD / Kerberos 故障時的 break-glass 後門。

---

## 1. 運作原理

```
瀏覽器（已加入網域，已登入 Windows）
   │  1. 送出請求，瀏覽器自動附上 Kerberos 票證（Negotiate）
   ▼
Nginx（libnginx-mod-http-auth-spnego + keytab）
   │  2. 驗證票證 → 取得使用者主體（例：EXAMPLE\jsmith）
   │  3. proxy_set_header X-Remote-User $remote_user;（一律覆寫）
   ▼
jt-doc-tools（只綁 127.0.0.1）
   │  4. 確認來源 IP 屬「信任的反向代理」
   │  5. 帳號正規化：EXAMPLE\jsmith → jsmith
   │  6. 用既有 LDAP/AD service account 查詢 + 同步 user / group / OU
   │  7. 發既有 JTDT session（沿用 sessions.issue）
   ▼
   套用既有 RBAC / audit / 2FA / workspace 隔離（完全不繞過）
```

- 使用者查詢與同步**沿用「認證設定」裡的 LDAP / AD 連線**（service account、search base、
  filter、群組屬性等）。Reverse Proxy SSO 不另外設定一組目錄連線。
- Session、權限、稽核、2FA 全部沿用既有機制，沒有第二套 cookie、也沒有第二套使用者資料表。
- **auditor 角色仍強制 2FA**：即使經 proxy 帶入帳號，auditor（或任何 `totp_required`）使用者
  仍會被導到 `/2fa-verify` 輸入 TOTP，proxy SSO 無法繞過。

---

## 2. jt-doc-tools 端設定

管理後台 → **SSO 單一登入** → 最下方「Reverse Proxy SSO（Kerberos / SPNEGO）」：

| 設定 | 預設 | 說明 |
|---|---|---|
| 啟用 Reverse Proxy SSO | 關 | 總開關。 |
| 帳號標頭名稱 | `X-Remote-User` | Nginx 傳入帳號的標頭。 |
| 沒有標頭時的行為 | 顯示 /login | 勾選＝fallback 到登入頁；取消勾選＝直接回 401。 |
| 信任的反向代理 IP | `127.0.0.1`、`::1` | 只有「直接連進本服務的來源 IP」在此清單內才採信標頭。可用 CIDR。 |

啟用前提（後台會擋）：
1. 已於「認證設定」啟用認證並建立管理員（保留 break-glass）。
2. 已填妥 LDAP / AD 連線（proxy SSO 靠它查詢並同步網域使用者）。
3. 「信任的反向代理 IP」不可空白。

> **強烈建議**：把 Python 服務綁在 `127.0.0.1`（`jtdt bind 127.0.0.1 8765` 或設
> `JTDT_HOST=127.0.0.1`），讓只有本機 Nginx 連得到，任何人都無法直接對 app 送標頭。

---

## 3. Windows Server 2019 AD — 建立 service account 與 SPN

在網域控制站上（系統管理員 PowerShell）：

```powershell
# 1) 建一個專用服務帳號（密碼不過期；僅供 Kerberos 用）
New-ADUser -Name "svc-jtdt-http" -SamAccountName "svc-jtdt-http" `
  -AccountPassword (Read-Host -AsSecureString "Password") `
  -PasswordNeverExpires $true -Enabled $true

# 2) 綁定 HTTP SPN 到該帳號（doc.example.local 換成你的網站主機名，FQDN）
setspn -S HTTP/doc.example.local svc-jtdt-http

# 確認
setspn -L svc-jtdt-http
```

### 產生 keytab（ktpass）

```powershell
ktpass -princ HTTP/doc.example.local@EXAMPLE.LOCAL `
  -mapuser svc-jtdt-http@EXAMPLE.LOCAL `
  -pass * -crypto AES256-SHA1 -ptype KRB5_NT_PRINCIPAL `
  -out C:\jtdt.keytab
```

- `HTTP/doc.example.local`：務必與使用者瀏覽器實際連的主機名一致（含大小寫網域）。
- `-crypto AES256-SHA1`：現代 AD 建議用 AES256；避免 RC4。
- 把 `C:\jtdt.keytab` 安全地複製到 Nginx 主機（見下），複製後刪除來源檔。

---

## 4. Debian 13.x — Nginx + SPNEGO

```bash
sudo apt update
sudo apt install nginx libnginx-mod-http-auth-spnego krb5-user

# keytab 放好、限權（只有 nginx / root 讀得到）
sudo install -o root -g www-data -m 640 jtdt.keytab /etc/nginx/jtdt.keytab
```

`/etc/krb5.conf`（最小範例）：

```ini
[libdefaults]
    default_realm = EXAMPLE.LOCAL
    dns_lookup_kdc = true
    rdns = false

[realms]
    EXAMPLE.LOCAL = {
        kdc = dc01.example.local
        admin_server = dc01.example.local
    }

[domain_realm]
    .example.local = EXAMPLE.LOCAL
    example.local = EXAMPLE.LOCAL
```

### Nginx vhost 範例

```nginx
server {
    listen 443 ssl;
    server_name doc.example.local;

    ssl_certificate     /etc/ssl/certs/doc.example.local.crt;
    ssl_certificate_key /etc/ssl/private/doc.example.local.key;

    client_max_body_size 100M;   # 檔案上傳（必設）
    server_tokens off;

    location / {
        # ---- SPNEGO / Kerberos ----
        auth_gss on;
        auth_gss_realm EXAMPLE.LOCAL;
        auth_gss_keytab /etc/nginx/jtdt.keytab;
        auth_gss_service_name HTTP/doc.example.local;
        # 非網域電腦（沒帶票證）不要被擋死 → 讓 fallback 到 /login
        auth_gss_allow_basic_fallback off;

        # ---- 關鍵資安：一律「覆寫」帳號標頭 ----
        # $remote_user 由上面的 auth_gss 設定；先清掉任何客戶端自帶的同名標頭，
        # 再設成 Kerberos 驗到的帳號。少了這行，攻擊者可自送 X-Remote-User 偽造身分。
        proxy_set_header X-Remote-User $remote_user;

        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 300s;

        # app 只綁 127.0.0.1
        proxy_pass http://127.0.0.1:8765;
    }
}
```

> **注意**：若某些路徑要開放給非網域使用者（例如 `/login`、`/static/`），可用單獨的
> `location` 區塊關掉 `auth_gss`。但因為 app 端本來就把 `/login` 等設為公開、且未帶標頭時
> 會 fallback 到 `/login`，多數情況「全站 auth_gss on + allow_basic_fallback off」即可。

---

## 5. 瀏覽器自動登入設定

使用者的瀏覽器要「信任」這個站台才會自動送 Kerberos 票證（否則會跳帳密框或直接 fallback）。

- **Edge / Chrome（Windows，網域派送最省事）**：群組原則
  `使用者設定 → 系統管理範本 → Microsoft Edge / Google Chrome → HTTP 驗證`
  設定 `AuthServerAllowlist` = `doc.example.local`（可多筆逗號分隔）。
  或本機：`AuthNegotiateDelegateAllowlist` / `AuthServerAllowlist` 登錄機碼。
  IE 模式沿用「網際網路選項 → 安全性 → 近端內部網路 → 站台」加入該主機。

- **Firefox**：`about:config` →
  `network.negotiate-auth.trusted-uris` = `doc.example.local`（或 `https://doc.example.local`）。
  企業可用 `policies.json` 的 `Authentication.SPNEGO` 派送。

設好後，網域內電腦開 `https://doc.example.local` 會直接進站，不再看到登入頁。

---

## 6. 非網域電腦 / fallback 行為

| 情境 | 結果 |
|---|---|
| 網域電腦、瀏覽器信任站台 | 帶 Kerberos 票證 → Nginx 設 `X-Remote-User` → 自動登入 |
| 非網域電腦、或瀏覽器未信任 | 沒有 `X-Remote-User` → app 依「沒有標頭時的行為」處理：<br>• 顯示 `/login`（預設）<br>• 或回 401 |
| 帶了標頭但來源 IP 不在信任清單 | app **忽略**標頭、不登入，並記一筆 `proxy_sso_untrusted_proxy` 稽核 |
| auditor / 強制 2FA 使用者 | 經 proxy 認人後仍導 `/2fa-verify` 輸入 TOTP |
| `jtdt-admin`（本機帳號） | 在 AD 查不到 → 一律走 `/login` 手動登入（break-glass 永遠可用） |

不會發生 redirect loop：登入成功的回應會帶上 session cookie，下一個請求即已有有效 session；
且 proxy 自動登入**只在受保護頁**觸發，永不在 `/login` 上觸發。

---

## 7. Header spoofing 風險與必要防護

`X-Remote-User` 是一個「誰送誰就是誰」的標頭；若攻擊者能直接對 app 送這個標頭，就能冒充任何人。
本系統與部署都必須同時滿足以下三層，缺一不可：

1. **只信任反向代理的來源 IP**：app 只採信「直接連進來的 TCP 來源 IP」在「信任的反向代理 IP」
   清單內的請求。**絕不看 `X-Forwarded-For`**（可偽造）。預設只信任 `127.0.0.1` / `::1`。
2. **app 只綁 127.0.0.1**：讓只有本機 Nginx 連得到 app。外部無法直接對 `:8765` 送標頭。
   （`jtdt bind 127.0.0.1 8765`）
3. **Nginx 一律覆寫標頭**：`proxy_set_header X-Remote-User $remote_user;` 會蓋掉客戶端自帶的
   同名標頭，確保只有 Kerberos 驗到的帳號會傳進 app。

任何「帶標頭但來源不可信」的請求都會被拒絕登入並寫入稽核，方便事後追查。

---

## 8. 稽核事件

| 事件 | 意義 |
|---|---|
| `proxy_sso_login_success` | 經 proxy 成功登入（details 含 DN） |
| `proxy_sso_login_fail` | 標頭可信但查詢 / 同步失敗（或正規化後為空） |
| `proxy_sso_header_missing` | 受保護頁但無標頭（每來源 IP 5 分鐘最多一筆，避免洗版） |
| `proxy_sso_untrusted_proxy` | 標頭來自不可信來源（可能是設定錯誤或偽造嘗試） |

這些事件會一併經既有的 log 轉發（syslog / CEF / GELF）送出。

---

## 9. 疑難排解

- **一直看到 /login，沒自動登入**：多半是瀏覽器沒把站台列入信任清單（第 5 節），或 Nginx 沒設
  `proxy_set_header X-Remote-User`。可先在 app 稽核看有沒有 `proxy_sso_header_missing`。
- **帶了標頭卻登不進、稽核出現 `proxy_sso_untrusted_proxy`**：app 收到的來源 IP 不在信任清單。
  確認 app 綁在 127.0.0.1、Nginx 也從 127.0.0.1 連 app；若中間還有一層代理，把那層的 IP
  加進清單。
- **`proxy_sso_login_fail`**：帳號在 AD 查不到，或 LDAP/AD service account / search base 設錯。
  用「認證設定」頁的 LDAP 測試按鈕先確認 service account 能查到該使用者。
- **Kerberos `Server not found in Kerberos database`**：SPN 沒綁對（第 3 節 `setspn`），或
  keytab 的 principal 與網站主機名不一致。
- **auditor 登入卡在 2FA**：這是預期行為 —— auditor 一律強制 TOTP。
