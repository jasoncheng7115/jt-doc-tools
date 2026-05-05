# Windows 套件相依檔案

## `winsw.exe` — Windows Service Wrapper（v1.4.44 起改用）

| 項目 | 內容 |
|---|---|
| 來源 | <https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe> |
| 版本 | WinSW 2.12.0（2024 年穩定版，活躍維護中，Jenkins 等專案使用） |
| 授權 | MIT |
| 大小 | 640 KB |
| SHA-256 | `b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f` |
| 執行環境 | .NET Framework 4.6.1+（Win10/11 內建，無需額外安裝） |

### 為什麼從 NSSM 換成 WinSW？

NSSM 2.24 是 2014 年最後一版，10+ 年沒更新；GitHub issues #1 / #3 反映 nssm.cc 不時 503 / 404，且部分 AV / 端點防護把 NSSM 標 PUA。WinSW 對比：

- ✓ 由 GitHub 託管下載（比 nssm.cc 穩定）
- ✓ 活躍維護（v2.12 是 2024 年版本）
- ✓ 配置由 XML 檔，更直觀（NSSM 是 sc.exe registry edit）
- ✓ MIT 授權清楚（NSSM 是 Public Domain，部分企業 IT 不接受）
- ✓ Jenkins / Gradle / 多個大型專案使用（AV 信任度更高）

### 升級相容性

從 NSSM 升級的客戶會在 `jtdt update` 流程中自動偵測舊服務、移除 NSSM、改安裝 WinSW，過程中保留 service name (`jtdtdocsvc`) 與環境變數（JTDT_HOST / JTDT_PORT）。詳見 `app/cli.py:_migrate_nssm_to_winsw`。

### 服務檔案命名規則

WinSW 要求 wrapper exe 與設定 XML 同名：
- `bin/jtdt-svc.exe` ← 從 `packaging/windows/winsw.exe` 複製
- `bin/jtdt-svc.xml` ← 由 `install.ps1` / `_write_winsw_xml()` 動態產生

兩個檔案放一起，`jtdt-svc.exe install` 會自動讀同名 .xml 註冊服務。

### 安全考量

- `install.ps1` 在拷貝前驗證 SHA-256（寫死在 `$WinswBundledSha256`），被改過就拒絕，退到網路下載
- 任何人可獨立校驗：`Get-FileHash -Path winsw.exe -Algorithm SHA256`
- 萬一被 AV 隔離 → fallback 從 GitHub Release 下載（穩定 host）

---

## `nssm.exe` — 已棄用（保留供升級偵測用）

舊客戶從 NSSM 升級時，`jtdt update` 會偵測現有 NSSM 服務並換成 WinSW。這個檔案保留在 repo 中**只用於相容性偵測**，新安裝不再使用。

| 項目 | 內容 |
|---|---|
| 來源 | <https://nssm.cc/release/nssm-2.24.zip> 解壓後 `nssm-2.24/win64/nssm.exe` |
| 版本 | NSSM 2.24 |
| 授權 | Public Domain |
| 大小 | 331 KB |
| SHA-256 | `f689ee9af94b00e9e3f0bb072b34caaf207f32dcb4f5782fc9ca351df9a06c97` |
