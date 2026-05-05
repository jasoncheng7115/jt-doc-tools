# 服務檔範本

這裡放的是 install.sh / install.ps1 安裝時會寫進系統的服務 unit 範本。一般使用者不需要手動處理；如需自行調整可參考。

| 檔案 | 平台 | 安裝位置 |
|------|------|---------|
| `jt-doc-tools.service`        | Linux   | `/etc/systemd/system/` |
| `windows/winsw.exe`           | Windows | `C:\Program Files\jt-doc-tools\bin\jtdt-svc.exe`（重新命名） + 動態產生的 `jtdt-svc.xml`；service name `jt-doc-tools` |

macOS 不用 LaunchDaemon — 改裝 `/Applications/Jason Tools 文件工具箱.app`，由 LaunchServices 啟動 + 登入項目自動執行。原因見 `feedback_macos_aqua_subprocess.md`：OxOffice/LibreOffice 子行程需 GUI Aqua context，LaunchDaemon 在 daemon session 拿不到。

Windows 自 v1.4.44 起改用 WinSW（之前是 NSSM）。原因：NSSM 2014 後無更新、nssm.cc 不時 503/404、AV 標 PUA。WinSW 配置由 `bin/jtdt-svc.xml` 管理，舊客戶升級時自動移轉（service name + env 變數都保留）。
