# 日常維運指南

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
4. `uv sync --no-dev` 同步依賴（不裝開發用的套件群組；之前裝進來的 pytest、bandit 會在這一步移除）
5. 重啟
6. 健康檢查

降版會被拒（避免毀資料）。失敗會自動 rollback。

### ⚠ 2026-09-13 之前安裝的（用 git 裝的）第一次升級要多跑一行

本專案在 2026-09-13 改寫過 git 歷史（移除一筆誤入版控的資料），所有標籤都
指向新的 commit。**本地標籤還停在舊 commit 時，`git fetch --tags` 會以離開碼
1 結束**（`would clobber existing tag`），而舊版的 `jtdt update` 一看到非零就
中止升級，訊息只說 `git fetch failed` —— 看不出原因是標籤。

跑一次這行就永久解決，之後 `jtdt update` 正常：

```bash
# Linux / macOS
sudo git -C /opt/jt-doc-tools fetch --tags --force origin

# Windows（以系統管理員身分執行 PowerShell）
git -C "C:\Program Files\jt-doc-tools" fetch --tags --force origin
```

> **`-C` 是大寫。** 打成小寫的 `git -c /opt/jt-doc-tools/ …` 會回
> `fatal: not a git repository (or any of the parent directories): .git`
> —— 小寫的 `-c` 是**設定參數**（`-c key=value`），git 不會切到那個目錄。
> 那個訊息看起來像「這個安裝不是用 git 裝的」，其實只是打錯一個字母
>（2026-09-14 客戶回報）。

**不受影響的**：用 tarball 安裝（沒有 `.git`）、用 Windows 安裝程式安裝、
或 2026-09-13 之後才安裝的。v1.15.41 起 `jtdt update` 會把分支與標籤分開拉、
標籤一律帶 `--force`，不會再發生。

## 企業 TLS 攔截環境（更新 / 下載出現 CERTIFICATE_VERIFY_FAILED）

公司若有 **TLS 檢查代理 / 防火牆**會把外部 HTTPS 憑證換成自家 CA。徵狀：
`jtdt update`、下載 OCR 語言檔（tessdata）、或 LLM / 遠端 OCR / SSO 連線時出現

```
SSL: CERTIFICATE_VERIFY_FAILED ... Missing Authority Key Identifier
```

原因：企業 CA 裝在 **OS 系統信任庫**，但 Python（程式內建的獨立 Python）用的是
內建的 certifi 憑證庫，**不認**那個企業 CA。

**v1.12.43 起自動處理 — 不需任何設定。** 只要照原本方式安裝 / 更新即可：

```bash
# 既有安裝：直接更新（建議重跑網站一行安裝指令，確保拿到最新 install.sh）
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.sh | sudo bash
# 或
sudo jtdt update
```

install.sh / `jtdt update` / 程式啟動會**自動**：① 讓 uv 用 OS 信任庫
（`UV_NATIVE_TLS` / `UV_SYSTEM_CERTS`）；② 把 `SSL_CERT_FILE` 指到 OS 系統 CA
bundle（`/etc/ssl/certs/ca-certificates.crt` 等）；③ 用 **truststore** 把程式執行時的
Python ssl 接到 OS 原生信任庫。企業 CA 既然已在 OS 信任庫（`apt` / `curl` / `git` 能動
即代表有），上述三層就會自動認得，HTTPS 下載全部成功 —— **客戶端零設定**。

> 註：既有客戶若卡在舊版,**重跑上面的網站一行安裝指令**最保險（會拿到含此修正的新
> install.sh，第一次就成功）；若用 `jtdt update`,因更新當下跑的是舊版邏輯,可能需再跑一次。

**例外 — 萬一企業 CA 還沒進 OS 信任庫**（少數情況，連 `apt`/`curl` 也失敗）：先把 CA
裝進系統信任庫即可（Debian：`sudo cp 企業CA.crt /usr/local/share/ca-certificates/ &&
sudo update-ca-certificates`；Windows 匯入「受信任的根憑證授權單位」；macOS 加入鑰匙圈並信任）。
真的不便處理時，最後手段 `sudo JTDT_TLS_INSECURE=1 jtdt update`（停用驗證，有 MITM 風險，僅信任內網用）。

## 憑證機構金鑰外洩（eMudhra / emSign，2026-10）

2026-10-20 公告：印度憑證機構 eMudhra（emSign）的中間憑證私鑰外洩，持有者可以替任意網域簽出有效的憑證，
用來假冒 PyPI、python.org 這類網站。本系統的處理：

| 環節 | 狀況 |
|---|---|
| Python 的憑證包 certifi | v1.17.7 起要求 2026.10.19 以上（已拿掉 emSign） |
| pip | 環境由 uv 建立，**裡面沒有 pip** |
| 程式執行時的 HTTPS（LLM、LDAPS、SSO、事務機等） | 用作業系統的信任庫（truststore），不用內建憑證包 |
| 安裝與 `jtdt update` 下載套件 | uv 改用作業系統的信任庫；每個套件都有 `uv.lock` 的 sha256 雜湊，檔案被換掉一律拒裝 |
| uv 本身 | 0.13.4 起內建憑證拿掉 emSign。v1.17.16 起 `jtdt update` 發現安裝目錄裡的 uv 低於 0.13.4 會自動換新（`jtdt update` 跑的是更新前的版本，所以要跑兩次） |

**你要做的**：

- **更新作業系統的憑證**（本系統信任的就是它）：Debian / Ubuntu `sudo apt update && sudo apt upgrade ca-certificates`；
  RHEL 系 `sudo dnf update ca-certificates`；Windows 執行 Windows Update；macOS 安裝系統更新。
- **用 Docker 的**：重新建置映像（`docker compose build --pull && docker compose up -d`）。Dockerfile 每次建置都會拿最新的 uv 與基底映像。
- 想立刻換 uv、不等下次更新：`sudo /opt/jt-doc-tools/bin/uv self update`（Windows 以系統管理員身分執行
  `"C:\Program Files\jt-doc-tools\bin\uv.exe" self update`）。

## ⚠ 不建議直接對「公開網際網路」開放

> **首選部署方式：只在內網 / VPN 使用，不要把服務放到公開網際網路上。**
>
> **最主要的原因是機密性**：同仁多半會用這套工具處理公司**內部 / 機密文件**（合約、報價、
> 個資、統編資料等）。一旦直接對外開放，等於把這些文件與管理後台一併暴露出去，只要被人
> 猜到網址或攻破認證，內部文件就會外洩。
>
> 另一個原因是**誰連得到**：放在公開網路上，任何人都能試著登入、上傳特製的檔案，去試探解析 PDF / Office / 圖片的元件（MuPDF、LibreOffice、Pillow 等；這類格式本來就複雜，相關元件偶爾會發布安全更新）。放在內網時，連得到的只有同仁，而且都要先登入 —— 同樣的元件，面對的對象差很多。
>
> **建議**：限縮在公司內網或 VPN 後、只給需要的人用。若因業務**必須**對外，這是「風險
> 自負」的選擇，至少要全部做到：
> - 反向代理 + HTTPS（見下）、只綁 `127.0.0.1`、防火牆只放代理
> - 啟用認證 + 強密碼 + **強制 2FA**、限縮角色權限
> - 前面加 **WAF / 速率限制**（登入端點 + 上傳端點）
> - 設 `client_max_body_size` 限縮上傳大小、持續更新相依套件（盯 Dependabot / CodeQL）
> - 考慮把 soffice 轉檔放進沙箱 / 獨立低權限帳號、封鎖對外連線

## 反向代理（HTTPS）— 非本機存取的**強制**安全要求

> ⚠️ **只要不是「本機單人」使用（任何網路 / 多人 / 內網其他電腦 / 對外）→ 一律放在
> nginx（或 Caddy）反向代理 + HTTPS 後面,絕不要把 `:8765` 直接對網路開放。**
>
> 應用程式預設只綁 `127.0.0.1:8765`（純 HTTP、無 TLS）。直接 `jtdt bind 0.0.0.0`
> 對網路開放等於明文傳輸帳號密碼 / 文件內容,且少了 TLS 終結、HSTS、憑證、速率
> 限制等防護。**正確做法**:`:8765` 維持只聽 `127.0.0.1`,由同機 / 內網的 nginx
> 反向代理對外提供 HTTPS。下面是含資安設定的完整範例。

### 不管用哪一套反向代理，都必須做到這 7 點

無論 nginx / Caddy / Apache / HAProxy / Traefik / IIS / F5，共通要求一致：

1. **掛根路徑 `/`**（不能 `/jtdt/`）— 所有頁面用絕對路徑。
2. **body 上限 ≥ 100 MB** — 上傳大檔需要。
3. **傳 `X-Forwarded-Proto`，而且值要跟著瀏覽器實際用的協定走** — 後端據此設 Secure cookie + HSTS（SSL 在代理卸載時尤其必要）。
   > ⚠ **站台只有 http 卻寫死 `https` 會讓整套 cookie 失效**：後端會在
   > `jtdt_csrf` / `jtdt_session` 加上 `Secure`，而**瀏覽器在明文連線下會直接
   > 丟掉 Secure cookie** → 上傳一律「CSRF token 遺失或不正確」，啟用認證的話
   > 則是登入後又被踢回登入頁。
   >
   > **最會誤導人的地方**：`http://localhost` 是瀏覽器的安全來源例外，**照樣
   > 收 Secure cookie** —— 所以在伺服器本機上怎麼測都正常，只有遠端會壞
   > （客戶回報過：「本機 8765 或 localhost:80 都不會有問題，只要在遠端電腦
   > 就會」）。v1.15.26 起後端偵測得到這個情況，會把原因寫進錯誤訊息與日誌。
4. **傳 `X-Forwarded-For`（真實用戶端 IP）** — 稽核 / 歷史顯示來源 IP 靠它。**並且要覆寫進站的 XFF**（見下方防偽造）。
5. **read / send 逾時 ≥ 900s** — LLM 工具（翻譯 / OCR 校驗 / pdf-fill 視覺校驗）單筆推理常 5–15 分鐘，預設 60s 會 504。
6. **安全標頭由後端 app 統一設定**（CSP / HSTS / X-Frame-Options…）— 代理**不要**再加一次，否則重複標頭（ZAP「Multiple Header Entries」）。
7. **後端只聽 `127.0.0.1:8765`** — 對外只由代理提供 HTTPS，並隱藏代理版本號。
   代理的設定**擋不住別人直接連 8765**，這件事要靠服務本身的綁定：
   - 確認：執行 `jtdt status`，`url` 那一行要是 `http://127.0.0.1:8765/`；再從另一台電腦開 `http://<伺服器 IP>:8765/`，要連不上。
   - 改回只綁本機：Linux / macOS `sudo jtdt bind 127.0.0.1`；Windows 以系統管理員身分執行 `jtdt bind 127.0.0.1`（會自動重啟服務）。
   - 安裝程式預設就只綁本機；只有自己執行過 `jtdt bind 0.0.0.0` 這類指令才會對區網開放。

> **防 `X-Forwarded-For` 偽造（稽核 IP 正確性）**：稽核 / 歷史記錄的來源 IP 取 `X-Forwarded-For` 最左側。若代理是用「附加」模式（nginx `$proxy_add_x_forwarded_for`、HAProxy `option forwardfor`…），惡意的內部使用者可先自己帶一個假 XFF，就能把稽核記到假 IP。要杜絕，讓代理**先移除進站的 `X-Forwarded-For`，再由代理自己填真實用戶端**（各範例已標註）。這是**選用的硬化**，不影響一般顯示；跟「反向代理下稽核 IP 變 127.0.0.1」的修正（v1.12.65）無關，該問題只要升級即解。

### nginx

`$proxy_add_x_forwarded_for` 會沿用進站 XFF 再附加；要防偽造改用 `proxy_set_header X-Forwarded-For $remote_addr;`（覆寫成真實 peer）。

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
        # Caddy 預設就會帶 X-Forwarded-For / -Proto / -Host（真實用戶端）
        # 防偽造：只信任前一跳的 XFF（Caddy 會覆寫非信任來源的）
        # trusted_proxies static 127.0.0.1/32
    }
    request_body {
        max_size 100MB
    }
}
```

### Apache httpd（mod_proxy）

需啟用模組：`proxy`、`proxy_http`、`ssl`、`headers`。

```apache
<VirtualHost *:443>
    ServerName docs.example.com
    ServerTokens Prod
    ServerSignature Off

    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/docs.example.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/docs.example.com/privkey.pem

    # 上傳大檔（100 MB，單位 bytes）
    LimitRequestBody 104857600

    ProxyPreserveHost On
    ProxyRequests Off

    # SSL 在此卸載 → 告知後端原始協定（後端據此設 Secure cookie + HSTS）
    RequestHeader set X-Forwarded-Proto "https"
    # 防偽造：清掉進站帶的 XFF，mod_proxy 會自動補上真實用戶端 IP
    RequestHeader unset X-Forwarded-For early

    # LLM 長工作：拉長逾時（秒）
    ProxyPass        / http://127.0.0.1:8765/ timeout=900 connectiontimeout=60
    ProxyPassReverse / http://127.0.0.1:8765/
</VirtualHost>
```

### HAProxy

```haproxy
defaults
    mode http
    timeout connect 60s
    timeout client  900s        # LLM 長工作
    timeout server  900s

frontend fe_docs
    bind :443 ssl crt /etc/haproxy/certs/docs.example.com.pem
    # 防偽造：先刪進站 XFF，再由 option forwardfor 填真實用戶端
    http-request del-header X-Forwarded-For
    option forwardfor
    http-request set-header X-Forwarded-Proto https
    default_backend be_jtdt

backend be_jtdt
    server jtdt 127.0.0.1:8765
```

HAProxy 以串流轉發，body 大小預設不受限；逾時務必拉到 900s。

### Traefik（v2 / v3，file provider）

```yaml
# 動態設定（file provider）
http:
  routers:
    jtdt:
      rule: "Host(`docs.example.com`)"
      entryPoints: ["websecure"]
      tls: { certResolver: "le" }
      service: "jtdt"
  services:
    jtdt:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8765/"
        responseForwarding:
          flushInterval: "-1"     # streaming 友善（翻譯逐句吐）
```

```yaml
# 靜態設定（traefik.yml）：逾時 + 防 XFF 偽造
entryPoints:
  websecure:
    address: ":443"
    forwardedHeaders:
      trustedIPs: ["127.0.0.1/32"]   # 只有這些來源的 XFF 才採信；其餘一律覆寫
    transport:
      respondingTimeouts:
        readTimeout: "900s"
        writeTimeout: "900s"
serversTransport:
  forwardingTimeouts:
    dialTimeout: "60s"
    responseHeaderTimeout: "900s"
```

Traefik 會自動帶 `X-Forwarded-For` / `-Proto`；body 以串流轉發不受限。

### IIS（URL Rewrite + ARR，Windows）

**順序不能顛倒 —— 先裝 URL Rewrite，再裝 ARR。** ARR 相依於 URL Rewrite：
反過來裝的話 ARR 會裝不起來，或是裝完之後「Server Proxy Settings」根本
出不來（客戶回報過）。

1. **URL Rewrite**（先）—— <https://www.iis.net/downloads/microsoft/url-rewrite>
2. **Application Request Routing (ARR)**（後）—— <https://www.iis.net/downloads/microsoft/application-request-routing>
3. IIS 管理員 → 伺服器節點 → **Application Request Routing Cache** →
   右側「Server Proxy Settings」→ 勾 **Enable proxy** → 套用
4. **確認 jt-doc-tools 只綁本機**：以系統管理員身分開 PowerShell 執行 `jtdt status`，`url` 那一行要是 `http://127.0.0.1:8765/`
   （不是的話執行 `jtdt bind 127.0.0.1`）。

> [!CAUTION]
> **下面這份 `web.config` 只負責把 IIS 收到的請求轉給 `127.0.0.1:8765`，擋不住別人直接連 8765。**
> 服務若綁在 `0.0.0.0` 或區網 IP，使用者打 `http://<伺服器 IP>:8765/` 就能繞過 IIS（也就繞過 HTTPS）。
> 請照上面第 4 步確認服務只綁本機，並從另一台電腦開 `http://<伺服器 IP>:8765/` 確認連不上。
> 服務因故必須綁區網位址時，至少用 Windows 防火牆擋掉 8765 的輸入連線（本機的 IIS 走回送介面，不受影響）：
> `New-NetFirewallRule -DisplayName "jt-doc-tools block 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Block`

> ARR 的官方下載頁在「Installing ARR 3.0 manually」那一段就寫著：
> *"ARR depends on URL Rewrite. Ensure URL Rewrite is installed prior to
> installing ARR."*
>
> 以前那頁有 Web Platform Installer 的連結，裝 ARR 時會自動把相依的
> URL Rewrite 依正確順序一起裝；**現在那個連結已經被拿掉，只剩 x86 / x64
> 手動安裝檔 —— 所以順序得自己顧。**

裝完之後，站台放這份 `web.config`（註解刻意用英文：用記事本等編輯器以非 UTF-8 開啟或存檔時，中文註解會變成亂碼）：

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <rewrite>
      <!-- Send the protocol the browser really used (http or https).
           Do NOT hard-code https: on an http-only site the backend would mark
           cookies Secure, browsers drop them, and remote uploads fail with a
           CSRF token error. -->
      <rewriteMaps>
        <rewriteMap name="MapProto" defaultValue="http">
          <add key="on" value="https" />
          <add key="off" value="http" />
        </rewriteMap>
      </rewriteMaps>
      <rules>
        <rule name="jtdt-reverse-proxy" stopProcessing="true">
          <match url="(.*)" />
          <action type="Rewrite" url="http://127.0.0.1:8765/{R:1}" />
          <serverVariables>
            <set name="HTTP_X_FORWARDED_PROTO" value="{MapProto:{HTTPS}}" />
          </serverVariables>
        </rule>
      </rules>
    </rewrite>
    <security>
      <requestFiltering>
        <!-- Allow uploads up to 100 MB (bytes) -->
        <requestLimits maxAllowedContentLength="104857600" />
      </requestFiltering>
    </security>
  </system.webServer>
</configuration>
```

- ARR 啟用 proxy 後會自動帶 `X-Forwarded-For`（真實用戶端）。
- 要設 `HTTP_X_FORWARDED_PROTO`：URL Rewrite →「View Server Variables」→ 把它加進允許清單，上面 `<set>` 才生效。
- **`{HTTPS}` 是 IIS 內建變數**（`ON` / `OFF`），上面的 `rewriteMap` 把它翻成
  `https` / `http`；同一份 `web.config` 給 http 與 https 綁定都對。
  **改成寫死 `https` 之前先想清楚**：站台若有 http 的綁定，那些連線的 cookie
  會全部失效，而且**在伺服器本機測不出來**（見上方共通要求第 3 點）。
- **逾時（LLM）**：ARR → Proxy →「Time-out (seconds)」設 `900`（預設 120）。
- **streaming**：ARR →「Response buffer threshold」設 `0`。

### F5 BIG-IP（LTM）

在 Virtual Server（listen `:443`）掛上 Client SSL profile（TLS 終結）、HTTP profile、與這條 iRule；Pool member 指向後端 `127.0.0.1:8765`（或內部伺服器 `IP:8765`）：

```tcl
# iRule：附加到 HTTPS Virtual Server
when HTTP_REQUEST {
    # SSL 已在 F5 卸載 → 告知後端原始協定
    HTTP::header replace X-Forwarded-Proto "https"
    # 防偽造：清掉進站 XFF，交給 HTTP profile 的「Insert X-Forwarded-For」重填
    HTTP::header remove X-Forwarded-For
}
```

GUI / tmsh 對應設定：
- **HTTP profile**：「Insert X-Forwarded-For」= **Enabled**（F5 自動帶真實用戶端 IP）。
- **Client SSL profile**：掛憑證做 TLS 終結。
- **TCP profile**：「Idle Timeout」拉高到 `900`（LLM 長工作，預設 300）。
- F5 預設不限 body 大小、以串流轉發。

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

## ⚠ 部署形態：目前**只支援單節點**

外部評估（2026-09-05）點出這件事沒有寫在文件裡 —— 而它會讓人把服務部署成
一個**看起來能跑、實際會壞資料**的樣子。

現行設計是刻意的單節點：

| 元件 | 現況 | 開多份會怎樣 |
|---|---|---|
| Web | 單一 uvicorn process | 多 worker 各有一份作業佇列，作業會重複派送 |
| 作業佇列 | 在記憶體裡 | 重啟以外的 worker 看不到彼此的佇列 |
| Office 轉檔鎖 | process 內的號誌 | 跨 process 失效 → soffice 同時多份、記憶體爆掉 |
| 資料庫 | SQLite（WAL） | 多節點共用同一份 = 檔案鎖競爭與毀損風險 |
| 工作區 / 暫存 | 本機檔案系統 | 另一個節點看不到，下載會 404 |

所以：

- **不要**把 uvicorn 開成多 worker（`--workers 2` 以上）。
  真的被開成多 worker 時，**啟動記錄會有一行 ERROR**（`app.single_process`），
  裡面寫著是怎麼判斷的以及會發生什麼事 —— 服務照樣起得來，但那些症狀
  （作業重複派送、正在跑的作業變成「已中斷」）就是從這裡來的。
- **不要**讓兩台機器掛同一個資料目錄（NFS / SMB 共用尤其危險）。
- 要更多吞吐量，先加 CPU / 記憶體並調整「併行度設定」（管理區 → 作業佇列），
  那是設計上支援的擴充方式。

真的需要多節點時要換的東西（目前沒做，也沒有時程）：外部佇列（Redis /
RabbitMQ）、PostgreSQL、物件儲存、以及分散式鎖。

## 監控：`/healthz` 與 `/readyz` 是兩件事

兩支都**不需要登入**（給監控系統用）。

| 端點 | 回答什麼 | 什麼時候會是非 200 |
|---|---|---|
| `GET /healthz` | **這個行程還活著嗎** | 只有真的沒在服務時（連不上） |
| `GET /readyz` | **這個行程實際上有沒有少東西** | 資料目錄寫不進去、或資料庫開不起來 → **503** |

`/readyz` 的回應：

```json
{
  "ok": true,
  "degraded": false,
  "tools": { "loaded": 48, "failed": 0 },
  "checks": { "data_dir_writable": true, "database": true }
}
```

**`degraded: true` 要告警，但不要因此重啟服務。** 那代表有工具沒有載入
（多半是缺相依套件）—— 其餘的工具照常可用。
**哪幾支、為什麼**在管理區 → **系統狀態**頁最上面（需要管理員）。
補裝套件之後要**重新啟動服務**才會掛上來。

> **為什麼工具缺了不讓 `/readyz` 回 503**：本專案是單一 web 行程（見上一節），
> 把唯一的實例判成不健康，反向代理給使用者的是整站錯誤頁 —— 比「少一支工具」
> 嚴重得多。同理 `/healthz` **刻意不看**工具載入狀況：服務管理員拿它決定要不要
> 重啟，因為少一支工具而一直重啟比問題本身更糟。
>
> **`/readyz` 不會吐出模組名稱或例外訊息** —— 它是公開的。細節只在管理區。

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

## 搬到另一台主機

舊主機要另作他用、換新硬體、換作業系統時，**複製整個資料夾**：帳號與密碼、權限、印章與簽名、各種歷史紀錄、
工作區、公文知識庫、公文撰擬的歷史案件、稽核記錄都在裡面，搬過去就是原本那一套。

> **不要用管理區的「設定備份 / 匯入」搬整台。** 它只帶設定，不含本機帳號與密碼、稽核記錄、作業紀錄、
> 公文知識庫的文件、公文撰擬的歷史案件、乘車證明的原始檔。全新的主機匯入之後，認證設定會因為沒有管理員帳號而不套用；
> 工作區、通知偏好這類個人資料只還原給新主機上同名的帳號。
>
> **也不要複製程式資料夾**（`C:\Program Files\jt-doc-tools`、`/opt/jt-doc-tools`、`/usr/local/jt-doc-tools`）：
> 那裡只有程式和 Python 環境，資料不在那裡；新主機用安裝程式裝一份就好。

| OS | 要複製的資料夾 |
|---|---|
| Windows | `C:\ProgramData\jt-doc-tools\Data` |
| Linux | `/var/lib/jt-doc-tools/data` |
| macOS | `~/Library/Application Support/jt-doc-tools/data`（安裝時那個帳號的家目錄） |

### 步驟

1. **新主機先安裝**（[INSTALL.md](INSTALL.md) 的一行安裝指令，或 Windows 安裝程式）。新主機的版本要**跟舊主機一樣或比較新**：
   安裝程式抓的是最新版；資料是舊版的沒關係，第一次啟動時會自動升級資料庫。反過來（新主機比舊主機舊）不行。
2. **兩台都停服務。** 服務執行中複製，資料庫可能不完整。
3. **把舊主機的資料夾整個複製到新主機的同一個位置**，取代新主機剛裝好的那一份。整個資料夾都要，包括
   `.session_secret`（加密存放的金鑰、API Token 的對照都要靠它解開）。
4. **啟動新主機的服務**，打開 `http://新主機:埠/readyz` 確認回 200。

Windows（以系統管理員身分執行 PowerShell）：

```powershell
# 兩台都先停
net stop jt-doc-tools
# 把舊主機的 C:\ProgramData\jt-doc-tools\Data 整個複製到新主機同一個位置
# （隨身碟、共用資料夾都可以；用 robocopy 的話：robocopy 來源 C:\ProgramData\jt-doc-tools\Data /MIR）
# 新主機
net start jt-doc-tools
```

Linux：

```bash
# 舊主機
sudo jtdt stop
sudo tar -czf jtdt-data.tgz -C /var/lib/jt-doc-tools data
# 把 jtdt-data.tgz 傳到新主機（scp 等）之後，在新主機：
sudo jtdt stop
sudo mv /var/lib/jt-doc-tools/data /var/lib/jt-doc-tools/data.fresh   # 剛裝好的那一份，確認沒問題再刪
sudo tar -xzf jtdt-data.tgz -C /var/lib/jt-doc-tools
sudo chown -R jtdt:jtdt /var/lib/jt-doc-tools                         # 服務帳號要讀得到
sudo jtdt start
```

macOS：

```bash
# 舊主機
sudo jtdt stop
tar -czf jtdt-data.tgz -C "$HOME/Library/Application Support/jt-doc-tools" data
# 新主機（同一個帳號登入）
sudo jtdt stop
mv "$HOME/Library/Application Support/jt-doc-tools/data" "$HOME/Library/Application Support/jt-doc-tools/data.fresh"
tar -xzf jtdt-data.tgz -C "$HOME/Library/Application Support/jt-doc-tools"
sudo jtdt start
```

跨作業系統（例如 Windows → Linux）也可以：資料庫與設定檔都是通用格式。只有下面「自訂的路徑」那幾項要照新主機改。

### 搬完要檢查的

- **監聽位址與連接埠**：記在服務設定裡，不在資料夾裡。舊主機有開放區網連線的話，新主機要再設一次：
  Windows 重跑安裝程式勾「區域網路存取」（會一併開防火牆）；Linux / macOS 用 `sudo jtdt bind 0.0.0.0:8765`，防火牆自己開。
- **反向代理**改指到新主機；**SSO**：網址變了的話，IdP 上登記的回呼網址也要改；**AD / LDAP**：新主機要連得到網域控制站。
- **通知設定的「站台網址」**：通知信裡的連結用它，換網址了要改。
- **自訂的路徑**：Office 引擎路徑（管理區「轉檔引擎設定」）、排程備份的輸出資料夾，在新主機上要存在。
- **外部服務**：LLM 伺服器、遠端 OCR、語音服務（JTLW）、嵌入服務，新主機要連得到；有用 IP 白名單的，把新主機加進去。
- 統編資料庫、公文範本與機關地址簿已經在資料夾裡，不用重新下載。
- **確認新主機沒問題之前，舊主機的資料不要刪；兩台不要同時對外服務**（同一份資料兩邊各自改，之後合不回來）。

## 同一台主機換資料目錄的位置

例如原本的磁碟快滿了，要把資料搬到另一顆比較大的磁碟。管理區的「系統狀態 → 磁碟空間（資料目錄所在）」
顯示的就是資料目錄目前在哪一顆磁碟。

**網頁上沒有可以改資料目錄的地方**：它是安裝時寫進服務設定的環境變數 `JTDT_DATA_DIR`。

| OS | 預設位置 | 記在哪裡 |
|---|---|---|
| Linux | `/var/lib/jt-doc-tools/data` | systemd 服務設定 |
| Windows | `C:\ProgramData\jt-doc-tools\Data` | `C:\Program Files\jt-doc-tools\bin\jtdt-svc.xml` |
| macOS | `~/Library/Application Support/jt-doc-tools/data` | 應用程式裡的 launcher |

### 建議做法：原路徑不變，把新位置接到原路徑

服務設定完全不用改，之後重跑安裝程式、`jtdt update` 都不受影響。

Linux：把新磁碟**掛載到原本的路徑**。

```bash
sudo jtdt stop
sudo mv /var/lib/jt-doc-tools/data /var/lib/jt-doc-tools/data.old
sudo mkdir /var/lib/jt-doc-tools/data
sudo mount /dev/新磁碟 /var/lib/jt-doc-tools/data            # 並寫進 /etc/fstab，開機才會自動掛上
sudo rsync -aHAX /var/lib/jt-doc-tools/data.old/ /var/lib/jt-doc-tools/data/
sudo chown -R jtdt:jtdt /var/lib/jt-doc-tools/data
sudo jtdt start
```

Windows（以系統管理員身分執行 PowerShell）：在原路徑建**目錄連接**（junction），指到新位置。

```powershell
net stop jt-doc-tools
robocopy C:\ProgramData\jt-doc-tools\Data D:\jtdt-data /MIR /COPYALL /DCOPY:DAT
Rename-Item C:\ProgramData\jt-doc-tools\Data Data.old
cmd /c mklink /J C:\ProgramData\jt-doc-tools\Data D:\jtdt-data
net start jt-doc-tools
```

### 另一種做法：改服務設定指到新路徑

需要 **v1.17.20 以上**。之前的版本 `jtdt` 指令只看環境變數、不讀服務設定，改完之後
`sudo jtdt reset-password`、`db-check`、`db-restore`、`update` 都會去動**舊的預設目錄**：指令顯示成功，服務用的資料卻沒有變。
舊版本要先升級，或每次執行 `jtdt` 指令前自己 `export JTDT_DATA_DIR=新路徑`。

Linux：用 systemd 的覆寫設定（`systemctl edit`），重跑 install.sh 也不會被蓋掉。

```bash
sudo jtdt stop
sudo rsync -aHAX /var/lib/jt-doc-tools/data/ /新路徑/
sudo chown -R jtdt:jtdt /新路徑
sudo systemctl edit jt-doc-tools
#   在編輯器裡加入下面三行後存檔：
#   [Service]
#   Environment=JTDT_DATA_DIR=/新路徑
#   ReadWritePaths=/新路徑
sudo systemctl daemon-reload
sudo jtdt start
```

`ReadWritePaths` 這一行不能省：比較新的安裝把服務設成整個檔案系統唯讀、只有資料目錄可寫，少了它服務寫不進新路徑。

Windows：停服務、用上面的 `robocopy` 把資料複製到新位置，再把 `C:\Program Files\jt-doc-tools\bin\jtdt-svc.xml` 裡
`<env name="JTDT_DATA_DIR" value="…"/>` 的 `value` 改成新路徑，然後 `net start jt-doc-tools`。
**重跑安裝程式（含網站的一行安裝指令）會用預設路徑重新產生這個檔**，之後要再改一次；所以 Windows 建議用上面的目錄連接。

### 搬完要確認的

- 管理區「系統狀態」的「磁碟空間（資料目錄所在）」顯示的是新的路徑與磁碟；帳號、設定、歷史紀錄、統編資料庫都還在。
- `sudo jtdt db-check`（Windows 不用 sudo）檢查的是新路徑上的資料庫。
- 確認幾天沒問題之後，再刪掉舊的資料（`data.old`）。

## 排程清理

啟用認證後可在 `/admin/retention` 設定每類資料保留天數：

| 項目 | 預設 | 路徑 |
|---|---|---|
| 表單填寫歷史 | 365 天 | `data/fill_history/` |
| 用印簽名歷史 | 365 天 | `data/stamp_history/` |
| 浮水印歷史 | 365 天 | `data/watermark_history/` |
| 乘車證明原始檔 | 365 天 | `data/transit_proof_files/` |
| 公文撰擬案件 | 365 天 | `data/official_doc_cases/` |
| 會議錄音（還在排隊或辨識中的不刪） | 30 天 | `data/speech_audio/` |
| 暫存上傳 / 工作檔 | 2 小時 | `data/temp/` |
| 作業結果檔（結果與「開啟」要讀的資料） | 24 小時 | 多半在 `data/temp/` |
| 作業紀錄（我的作業） | 30 天 | `data/jobs.sqlite` |
| 稽核記錄 | 90 天 | `data/audit.sqlite` |

排程：啟動時跑一次 + 每 6 小時跑一次。`-1` = 永久保留。
