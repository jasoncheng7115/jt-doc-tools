# 服務檔範本

這裡放的是 install.sh / install.ps1 安裝時會寫進系統的服務 unit 範本。一般使用者不需要手動處理；如需自行調整可參考。

| 檔案 | 平台 | 安裝位置 |
|------|------|---------|
| `jt-doc-tools.service`        | Linux   | `/etc/systemd/system/` |
| `com.jasontools.doctools.plist` | macOS | `/Library/LaunchDaemons/` |

Windows 採用 NSSM 包裝（不需 unit 檔），由 install.ps1 直接呼叫 `nssm.exe set` 設定。
