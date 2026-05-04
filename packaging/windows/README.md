# Windows 套件相依檔案

## `nssm.exe` — Windows Service Wrapper

| 項目 | 內容 |
|---|---|
| 來源 | <https://nssm.cc/release/nssm-2.24.zip> 解壓後 `nssm-2.24/win64/nssm.exe` |
| 版本 | NSSM 2.24（最後穩定版，2014 年起無更新但功能完整、廣泛使用） |
| 授權 | Public Domain — 允許自由 redistribute |
| 大小 | 331 KB |
| SHA-256 | `f689ee9af94b00e9e3f0bb072b34caaf207f32dcb4f5782fc9ca351df9a06c97` |

### 為什麼 bundle 在 repo 內？

之前 `install.ps1` 從 `nssm.cc` 下載，但 GitHub user issue #1 反映 `nssm.cc` 偶爾整天 503，公司防火牆也常擋外部 .exe 下載。bundle 進來確保**離線環境 / 受限網路**也能正常安裝。

### 安全考量

- `install.ps1` 在拷貝前會**驗證 SHA-256**（寫死在 `$NssmBundledSha256` 變數），如果被改過就拒絕使用，退到網路下載。
- 任何人可獨立校驗：
  ```powershell
  Get-FileHash -Path nssm.exe -Algorithm SHA256
  ```
  應與上表 SHA-256 一致。
- 萬一公司端點防護（CrowdStrike / SentinelOne 等）誤判 NSSM 為 PUA 把 bundled copy 隔離 → install.ps1 自動退到網路下載；網路也被擋 → 印明確訊息請 IT 加白名單或手動下載。

### AV 誤判參考

NSSM 偶爾被部分啟發式引擎標為 PUA（Service Wrapper 類工具的通病），但下列主流 AV 都認可：Microsoft Defender、Symantec、Trend Micro、Kaspersky、Bitdefender。VirusTotal 通常 70 個引擎內 0-3 個 flag。
