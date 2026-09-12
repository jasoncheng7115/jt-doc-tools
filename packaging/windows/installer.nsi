; =====================================================================
;  installer.nsi  --  Jason Tools Document Toolbox (jt-doc-tools)
;                     Windows GUI installer (NSIS / MUI2)
; ---------------------------------------------------------------------
;  Thin bootstrapper: ships only the PowerShell core scripts + LICENSE +
;  icon. At runtime install_core.ps1 downloads Python (uv-managed),
;  clones the repo, sets up the venv and registers the WinSW service.
;
;  This file is NEW and self-contained. It does NOT modify or depend on
;  the existing command-line install.ps1 -- the two install paths are
;  intentionally isolated so the GUI installer can never destabilise the
;  curl|iex one-liner.
;
;  Build (on Linux/macOS with NSIS installed):
;    makensis -DVERSION=1.11.68 installer.nsi
;  CI builds this on an Ubuntu runner (see release-windows-installer.yml).
; =====================================================================

Unicode true

!ifndef VERSION
  !define VERSION "0.0.0"
!endif

; **顯示名稱**（會隨語系變）與**路徑用的名字**（永遠不變）要分開。
;
; `APPNAME` 原本同時當四種東西用：精靈標題、「程式和功能」的顯示名稱、
; 開始功能表的資料夾名、捷徑檔名。前兩個隨語系變沒問題（一個是視窗標題、
; 一個是登錄檔的值），**後兩個是路徑** —— 安裝時用語系 A 建了資料夾、
; 解除安裝時系統語系變成 B，`RMDir` 就找不到那個資料夾：解除安裝「成功」，
; 但開始功能表留著一個刪不掉的殘骸。
;
; 所以：路徑一律用 `SHORTNAME`（不變），而**實際建出來的開始功能表路徑寫進
; 登錄檔**，解除安裝時讀回來（見 `SM_FOLDER_VALUE`）。
!define APPNAME      "Jason Tools 文件工具箱 (jt-doc-tools)"   ; 僅作為 LangString 的預設值與舊版相容用
!define SHORTNAME    "jt-doc-tools"
;: 登錄檔裡記「這次安裝實際建的開始功能表資料夾」的值名稱。
!define SM_FOLDER_VALUE "StartMenuFolder"
;: v1.15.30 以前寫死的中文資料夾名 —— 升級上來的安裝要靠它才刪得掉。
!define LEGACY_SM_FOLDER "Jason Tools 文件工具箱 (jt-doc-tools)"
!define PUBLISHER    "Jason Cheng"
!define WEBSITE      "https://jasoncheng7115.github.io/jt-doc-tools/"
!define REPOURL      "https://github.com/jasoncheng7115/jt-doc-tools"
!define ARP_KEY      "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SHORTNAME}"

; NSIS 的 `Name` 吃語言字串（每個語言各有一份），所以標題可以隨語系變。
Name "$(APP_DISPLAY) ${VERSION}"
OutFile "jt-doc-tools-${VERSION}-setup.exe"
InstallDir "$PROGRAMFILES64\${SHORTNAME}"
RequestExecutionLevel admin    ; system-level install (matches install.ps1)
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "Sections.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"

; 解除安裝模式的狀態（同一支執行檔靠 `/uninstall` 分流）
Var SM_DIR      ; 這次安裝實際建的開始功能表資料夾
Var UNMODE
Var UN_PURGE
Var UN_DIR

; ---- branding -------------------------------------------------------
!define MUI_ICON   "assets\jtdt.ico"
!define MUI_UNICON "assets\jtdt.ico"
!define MUI_ABORTWARNING

; ---- install pages --------------------------------------------------
; 解除安裝模式（`setup.exe /uninstall`）走同一支執行檔，安裝那幾頁要跳過。
; **為什麼不用 NSIS 內建的 uninstaller**：`WriteUninstaller` 會產生**第二個
; 執行檔**，那一個也要簽章（SignPath 得做兩段式），而 2026-09-05 實測發現它
; 根本沒被簽，解除安裝時 Windows 跳「發行者不明」。同一支 exe 只要簽一次。
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipWhenUninstalling
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipWhenUninstalling
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipWhenUninstalling
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipWhenUninstalling
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

; finish page: offer to open the web UI
!define MUI_PAGE_CUSTOMFUNCTION_PRE FinishPagePre
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "$(FINISH_OPEN)"
!define MUI_FINISHPAGE_RUN_FUNCTION "OpenWebUI"
!define MUI_FINISHPAGE_LINK "$(FINISH_LINK)"
!define MUI_FINISHPAGE_LINK_LOCATION "${WEBSITE}"
!insertmacro MUI_PAGE_FINISH

; ---- languages (Traditional Chinese first, then English) ------------
!insertmacro MUI_LANGUAGE "TradChinese"
!insertmacro MUI_LANGUAGE "English"

; ---- localized strings ----------------------------------------------
; 產品顯示名稱。**只用於顯示**（視窗標題、「程式和功能」）——
; 路徑請用 `SHORTNAME`，理由見檔案開頭那段說明。
LangString APP_DISPLAY  ${LANG_TRADCHINESE} "Jason Tools 文件工具箱 (jt-doc-tools)"
LangString APP_DISPLAY  ${LANG_ENGLISH}     "Jason Tools Document Toolbox (jt-doc-tools)"
; 開始功能表的資料夾與兩個捷徑的檔名。**這些是路徑**，所以建出來之後要把
; 實際路徑寫進登錄檔，解除安裝才刪得掉（語系換了也一樣）。
LangString SM_FOLDER    ${LANG_TRADCHINESE} "Jason Tools 文件工具箱"
LangString SM_FOLDER    ${LANG_ENGLISH}     "Jason Tools Document Toolbox"
LangString SM_OPEN_LNK  ${LANG_TRADCHINESE} "開啟 jt-doc-tools"
LangString SM_OPEN_LNK  ${LANG_ENGLISH}     "Open jt-doc-tools"
LangString SM_UNINST_LNK ${LANG_TRADCHINESE} "解除安裝 jt-doc-tools"
LangString SM_UNINST_LNK ${LANG_ENGLISH}     "Uninstall jt-doc-tools"
LangString FINISH_OPEN  ${LANG_TRADCHINESE} "開啟 jt-doc-tools 網頁介面"
LangString FINISH_OPEN  ${LANG_ENGLISH}     "Open the jt-doc-tools web interface"
LangString FINISH_LINK  ${LANG_TRADCHINESE} "前往 jt-doc-tools 介紹網站"
LangString FINISH_LINK  ${LANG_ENGLISH}     "Visit the jt-doc-tools website"
LangString UNINST_TOP   ${LANG_TRADCHINESE} "這會移除 $(APP_DISPLAY)。使用者資料（銀行帳號、簽名、歷史記錄）預設保留，可在下一步選擇是否一併刪除。"
LangString UNINST_TOP   ${LANG_ENGLISH}     "This will remove $(APP_DISPLAY). User data (bank accounts, signatures, history) is kept by default; you can choose to delete it next."

LangString DESC_Core    ${LANG_TRADCHINESE} "核心程式與 Python 執行環境（必要）。"
LangString DESC_Core    ${LANG_ENGLISH}     "Core program and Python runtime (required)."
LangString DESC_Ocr     ${LANG_TRADCHINESE} "OCR 文字辨識引擎（PyTorch + EasyOCR + 中文訓練檔，約 700MB）。"
LangString DESC_Ocr     ${LANG_ENGLISH}     "OCR engine (PyTorch + EasyOCR + Chinese data, ~700MB)."
LangString DESC_Office  ${LANG_TRADCHINESE} "Office 文件轉檔引擎（OxOffice，約 600MB）。"
LangString DESC_Office  ${LANG_ENGLISH}     "Office conversion engine (OxOffice, ~600MB)."
LangString DESC_Svc     ${LANG_TRADCHINESE} "註冊 Windows 服務，開機自動啟動。"
LangString DESC_Svc     ${LANG_ENGLISH}     "Register a Windows service that starts automatically at boot."
LangString DESC_Fw      ${LANG_TRADCHINESE} "允許區域網路其他電腦連入（防火牆例外；服務改綁 0.0.0.0）。不需要請取消。"
LangString DESC_Fw      ${LANG_ENGLISH}     "Allow other LAN machines to connect (firewall rule; binds 0.0.0.0). Uncheck if not needed."

; 元件清單的項目名稱。NSIS 的 `Section "名字"` 是編譯期字面值，要多語得在
; .onInit 用 SectionSetText 覆寫（見下方）。原本寫成「中文 / English」並列，
; 兩種語系的使用者都要讀一遍不屬於自己的那半段。
LangString SEC_CORE     ${LANG_TRADCHINESE} "核心程式（必要）"
LangString SEC_CORE     ${LANG_ENGLISH}     "Core program (required)"
LangString SEC_OCR      ${LANG_TRADCHINESE} "OCR 文字辨識引擎"
LangString SEC_OCR      ${LANG_ENGLISH}     "OCR engine"
LangString SEC_OFFICE   ${LANG_TRADCHINESE} "Office 轉檔引擎"
LangString SEC_OFFICE   ${LANG_ENGLISH}     "Office conversion engine"
LangString SEC_SVC      ${LANG_TRADCHINESE} "Windows 服務（開機自動啟動）"
LangString SEC_SVC      ${LANG_ENGLISH}     "Windows service (autostart)"
LangString SEC_FW       ${LANG_TRADCHINESE} "區域網路存取"
LangString SEC_FW       ${LANG_ENGLISH}     "LAN access"

; 對話框。**解除安裝那三句特別重要**：解除安裝走的是 `.onInit` 裡在語言對話框
; 之前就 `Return` 的那條路，原本寫死中文 → 英文使用者要移除程式時看到的是
; 一整段看不懂的中文，而那正是在問「要不要順便刪掉你的資料」。
; NSIS 會依**系統語系**預設 $LANGUAGE（在 zh-TW 機器上實測過：把 English
; 宣告在第一個，解析出來的仍是中文那組），所以不經對話框也會選對。
LangString ERR_INSTALL  ${LANG_TRADCHINESE} "安裝失敗 (install_core.ps1 exit code $1)。$\r$\n請查看 $\"%ProgramData%\${SHORTNAME}\Logs\installer.log$\" 以取得詳情。"
LangString ERR_INSTALL  ${LANG_ENGLISH}     "Installation failed (install_core.ps1 exit code $1).$\r$\nSee $\"%ProgramData%\${SHORTNAME}\Logs\installer.log$\" for details."
LangString UN_ASK_PURGE ${LANG_TRADCHINESE} "是否一併刪除使用者資料（銀行帳號、簽名、歷史記錄）？$\r$\n$\r$\n選「否」會保留資料，下次重新安裝可沿用。"
LangString UN_ASK_PURGE ${LANG_ENGLISH}     "Also delete user data (bank accounts, signatures, history)?$\r$\n$\r$\nChoose No to keep it; a future reinstall will pick it up again."
LangString UN_NO_DIR    ${LANG_TRADCHINESE} "找不到安裝目錄，已中止解除安裝。"
LangString UN_NO_DIR    ${LANG_ENGLISH}     "Installation directory not found; uninstall aborted."
LangString UN_NOT_OURS  ${LANG_TRADCHINESE} "$UN_DIR 看起來不是 ${SHORTNAME} 的安裝目錄，已中止解除安裝。"
LangString UN_NOT_OURS  ${LANG_ENGLISH}     "$UN_DIR does not look like a ${SHORTNAME} installation; uninstall aborted."

; =====================================================================
;  Sections  (all optional sections default to selected = 全勾)
; =====================================================================
Section "Core" SecCore
  SectionIn RO
SectionEnd

Section "OCR" SecOcr
SectionEnd

Section "Office" SecOffice
SectionEnd

Section "Service" SecSvc
SectionEnd

Section "LAN" SecFw
SectionEnd

; Hidden section that performs the actual install once component choices
; are known. The '-' prefix hides it from the components list.
Section "-DoInstall"
  ${If} $UNMODE == "1"
    Return
  ${EndIf}
  SetDetailsPrint both
  ; System-level install: shortcuts go to the All Users start menu, not the
  ; current (elevating) user's. Without this, $SMPROGRAMS = the running user's
  ; profile and the shortcut is invisible to everyone else on the machine.
  SetShellVarContext all

  ; Force 64-bit registry/FS views (we are an x64-only product).
  ${If} ${RunningX64}
    SetRegView 64
  ${Else}
    MessageBox MB_ICONSTOP "32-bit Windows is not supported."
    Abort
  ${EndIf}

  ; Extract the install core to the (temp) plugins dir.
  ; NOTE: uninstall_core.ps1 is deliberately extracted AFTER install_core runs.
  ; install_core's Fetch-Code wipes every non-bin file in $INSTDIR before the
  ; git clone, so anything written here first would be deleted. We drop the
  ; uninstall core in afterwards so the installer is self-contained and does not
  ; depend on the public repo shipping these scripts.
  SetOutPath "$PLUGINSDIR"
  File "install_core.ps1"

  ; ---- build the PowerShell switch string from component selections ----
  StrCpy $0 ""
  ${If} ${SectionIsSelected} ${SecOcr}
    StrCpy $0 "$0 -InstallOcr"
  ${EndIf}
  ${If} ${SectionIsSelected} ${SecOffice}
    StrCpy $0 "$0 -InstallOffice"
  ${EndIf}
  ${If} ${SectionIsSelected} ${SecSvc}
    StrCpy $0 "$0 -InstallService"
  ${EndIf}
  ${If} ${SectionIsSelected} ${SecFw}
    StrCpy $0 "$0 -InstallFirewall"
  ${EndIf}

  ; Prefer the 64-bit PowerShell via Sysnative. The NSIS installer is always a
  ; 32-bit process, so plain powershell.exe is the WOW64 build whose registry /
  ; Program Files views are redirected -- that broke Office/VC-redist detection.
  ; Sysnative resolves to the native System32 from a 32-bit process (x64 + ARM64).
  StrCpy $2 "powershell.exe"
  IfFileExists "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe" 0 +2
    StrCpy $2 "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"

  DetailPrint "Running installer core (downloads Python + source, please wait) ..."
  nsExec::ExecToLog '"$2" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\install_core.ps1" -InstallDir "$INSTDIR"$0'
  Pop $1
  ${If} $1 != 0
    MessageBox MB_ICONSTOP "$(ERR_INSTALL)"
    Abort
  ${EndIf}

  ; ---- drop the uninstall core in now (after Fetch-Code's wipe) ----------
  SetOutPath "$INSTDIR\packaging\windows"
  File "uninstall_core.ps1"

  ; ---- 解除安裝用的是**這一支 setup.exe 自己**（不再產生第二個執行檔）----
  ; 這樣只有一個二進位檔要簽章。原本的 `WriteUninstaller` 產出的 uninstall.exe
  ; 沒有被 SignPath 簽到，解除安裝時 Windows 會跳「發行者不明」。
  ; **不可以用 `CopyFiles`** —— 它走 shell 的 SHFileOperation，安裝程式在
  ; session 0（服務工作階段、無桌面）跑的時候會直接卡住不返回（2026-09-05
  ; 實測：安裝核心已經跑完，NSIS 行程還掛在那裡不結束）。用純 Win32 的
  ; CopyFile 就沒有這個問題。
  System::Call 'kernel32::CopyFile(t "$EXEPATH", t "$INSTDIR\${SHORTNAME}-setup.exe", i 0) i .r0'

  ; 從舊版（會產生 uninstall.exe 的那種）升上來時，把那支殘留的清掉 ——
  ; 它已經沒有人指向它了，留著只是一支沒簽章、按了會出事的執行檔。
  Delete "$INSTDIR\uninstall.exe"

  ; 登錄檔的**值**（不是鍵路徑）→ 可以隨語系變，不影響升級或解除安裝的定位。
  WriteRegStr   HKLM "${ARP_KEY}" "DisplayName"     "$(APP_DISPLAY)"
  WriteRegStr   HKLM "${ARP_KEY}" "DisplayVersion"  "${VERSION}"
  WriteRegStr   HKLM "${ARP_KEY}" "Publisher"       "${PUBLISHER}"
  WriteRegStr   HKLM "${ARP_KEY}" "URLInfoAbout"    "${WEBSITE}"
  WriteRegStr   HKLM "${ARP_KEY}" "HelpLink"        "${REPOURL}"
  WriteRegStr   HKLM "${ARP_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKLM "${ARP_KEY}" "DisplayIcon"     "$INSTDIR\${SHORTNAME}-setup.exe"
  WriteRegStr   HKLM "${ARP_KEY}" "UninstallString" "$\"$INSTDIR\${SHORTNAME}-setup.exe$\" /uninstall"
  ; 無介面解除安裝：`/S` 交給同一支處理，它自己會先複製到 %TEMP% 再回頭刪目錄。
  WriteRegStr   HKLM "${ARP_KEY}" "QuietUninstallString" "$\"$INSTDIR\${SHORTNAME}-setup.exe$\" /S /uninstall"
  WriteRegDWORD HKLM "${ARP_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${ARP_KEY}" "NoRepair" 1

  ; Start menu shortcut (browser link to the local UI).
  ; **把實際建出來的資料夾寫進登錄檔** —— 解除安裝時讀它，不要重算。
  ; 重算的話，安裝與解除安裝的語系只要不同就刪不掉（那正是這批要修的事）。
  StrCpy $SM_DIR "$SMPROGRAMS\$(SM_FOLDER)"
  CreateDirectory "$SM_DIR"
  CreateShortcut  "$SM_DIR\$(SM_OPEN_LNK).lnk" "http://127.0.0.1:8765/" "" "$INSTDIR\packaging\windows\assets\jtdt.ico"
  CreateShortcut  "$SM_DIR\$(SM_UNINST_LNK).lnk" "$INSTDIR\${SHORTNAME}-setup.exe" "/uninstall"
  WriteRegStr   HKLM "${ARP_KEY}" "${SM_FOLDER_VALUE}" "$SM_DIR"
SectionEnd

; ---- component descriptions ----------------------------------------
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCore}   "$(DESC_Core)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecOcr}    "$(DESC_Ocr)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecOffice} "$(DESC_Office)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecSvc}    "$(DESC_Svc)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecFw}     "$(DESC_Fw)"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function OpenWebUI
  ExecShell "open" "http://127.0.0.1:8765/"
FunctionEnd

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "32-bit Windows is not supported."
    Abort
  ${EndIf}
  SetRegView 64

  ; ---- `/uninstall` → 走解除安裝流程（同一支執行檔）--------------------
  StrCpy $UNMODE "0"
  StrCpy $UN_PURGE "0"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/uninstall" $R1
  ${IfNot} ${Errors}
    StrCpy $UNMODE "1"
  ${EndIf}

  ${If} $UNMODE == "1"
    ; 安裝目錄：`/instdir=` 指定（從 %TEMP% 重跑時），否則就是自己所在的目錄
    ClearErrors
    ${GetOptions} $R0 "/instdir=" $R2
    ${If} ${Errors}
      StrCpy $UN_DIR "$EXEDIR"
    ${Else}
      StrCpy $UN_DIR $R2
    ${EndIf}

    ; **不能站在要刪的目錄裡刪自己**。還沒搬到 %TEMP% 的話，先複製過去再重跑
    ; ——這正是 NSIS 內建 uninstaller 用 `_?=` 在做的事，我們自己做一次。
    ClearErrors
    ${GetOptions} $R0 "/fromtemp" $R3
    ${If} ${Errors}
      StrCpy $R4 "$TEMP\jtdt-uninstall-$${VERSION}.exe"
      System::Call 'kernel32::CopyFile(t "$EXEPATH", t "$R4", i 0) i .r0'   ; 同上：不走 shell
      IfFileExists "$R4" 0 un_no_copy
        ${If} ${Silent}
          Exec '"$R4" /S /uninstall /fromtemp /instdir="$UN_DIR"'
        ${Else}
          Exec '"$R4" /uninstall /fromtemp /instdir="$UN_DIR"'
        ${EndIf}
        Quit
      un_no_copy:
    ${EndIf}

    ; 要不要一併刪掉使用者資料。`/SD IDNO`：無介面模式預設**保留**。
    MessageBox MB_YESNO|MB_ICONQUESTION \
      "$(UN_ASK_PURGE)" \
      /SD IDNO IDYES un_purge_yes IDNO un_purge_done
    un_purge_yes:
      StrCpy $UN_PURGE "1"
    un_purge_done:
    Return          ; 解除安裝不需要選語言 / 元件
  ${EndIf}

  !insertmacro MUI_LANGDLL_DISPLAY

  ; 語言確定之後才填元件名稱（使用者在對話框改過語言也算數）
  SectionSetText ${SecCore}   "$(SEC_CORE)"
  SectionSetText ${SecOcr}    "$(SEC_OCR)"
  SectionSetText ${SecOffice} "$(SEC_OFFICE)"
  SectionSetText ${SecSvc}    "$(SEC_SVC)"
  SectionSetText ${SecFw}     "$(SEC_FW)"
FunctionEnd

; =====================================================================
;  解除安裝 —— **同一支執行檔**，用 `/uninstall` 分流
;
;  原本是 NSIS 的 `WriteUninstaller`，那會產生**第二個執行檔**。兩個問題：
;    1. 兩個都要簽章（SignPath 要做兩段式：先產 uninstaller → 簽 → 再包進
;       installer → 簽）。2026-09-05 實測發現 uninstall.exe 根本是 NotSigned，
;       解除安裝時 Windows 跳「發行者不明」。
;    2. 多一個要維護、要驗證的產物。
;
;  改成同一支之後只剩一個二進位檔要簽。做法跟 Chrome / VS Code 一樣：把
;  setup.exe 複製進 $INSTDIR，`UninstallString` 指向它加 `/uninstall`。
; =====================================================================

;; 解除安裝模式時跳過安裝用的頁面。
Function SkipWhenUninstalling
  ${If} $UNMODE == "1"
    Abort
  ${EndIf}
FunctionEnd

;; 完成頁：解除安裝模式不要顯示「開啟網頁介面」。
Function FinishPagePre
  ${If} $UNMODE == "1"
    ; 服務已經移除了，開網頁只會得到連不上
    SendMessage $mui.FinishPage.Run ${BM_SETCHECK} 0 0
    ShowWindow $mui.FinishPage.Run 0
    ShowWindow $mui.FinishPage.Link 0
  ${EndIf}
FunctionEnd

Section "-DoUninstall"
  ${If} $UNMODE != "1"
    Return
  ${EndIf}
  ; **拿一個半截的路徑去 RMDir /r 是災難**（`C:\Program Files\x` 被空白截斷成
  ; `C:\Program`）。動手刪之前先確認那真的是我們的安裝目錄。
  ${If} $UN_DIR == ""
    MessageBox MB_ICONSTOP "$(UN_NO_DIR)"
    Abort
  ${EndIf}
  IfFileExists "$UN_DIR\packaging\windows\uninstall_core.ps1" un_dir_ok 0
  IfFileExists "$UN_DIR\${SHORTNAME}-setup.exe" un_dir_ok 0
    MessageBox MB_ICONSTOP "$(UN_NOT_OURS)"
    Abort
  un_dir_ok:
  SetDetailsPrint both
  SetRegView 64
  SetShellVarContext all   ; 要跟安裝時同一個情境，捷徑才刪得掉

  ; --- 診斷用的麵包屑：記錄解除安裝實際做了什麼 ---
  CreateDirectory "$APPDATA\${SHORTNAME}\Logs"
  FileOpen $4 "$APPDATA\${SHORTNAME}\Logs\nsis-uninstall.marker" w
  FileWrite $4 "uninstall section reached$\r$\nUN_DIR=$UN_DIR$\r$\n"

  StrCpy $0 ""
  ${If} $UN_PURGE == "1"
    StrCpy $0 " -PurgeData"
  ${EndIf}

  ; 64 位元的 PowerShell 走 Sysnative（理由同安裝那段：這支是 32 位元行程，
  ; 直接叫 powershell.exe 拿到的是 WOW64 那個，登錄檔與服務的視圖是被轉向的）
  StrCpy $2 "powershell.exe"
  IfFileExists "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe" 0 +2
    StrCpy $2 "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  FileWrite $4 "psexe=$2$\r$\n"

  IfFileExists "$UN_DIR\packaging\windows\uninstall_core.ps1" core_found core_missing
  core_found:
    FileWrite $4 "uninstall_core.ps1 found, running ...$\r$\n"
    DetailPrint "Running uninstall core ..."
    nsExec::ExecToLog '"$2" -NoProfile -ExecutionPolicy Bypass -File "$UN_DIR\packaging\windows\uninstall_core.ps1" -InstallDir "$UN_DIR"$0'
    Pop $1
    FileWrite $4 "nsExec exit=$1$\r$\n"
    Goto skip_uncore
  core_missing:
    FileWrite $4 "uninstall_core.ps1 NOT FOUND at $UN_DIR\packaging\windows$\r$\n"
  skip_uncore:
  FileClose $4

  ; 移除程式檔。/REBOOTOK：鎖住的檔案排到下次開機刪（例如 .venv 裡還被短暫持有的）
  RMDir /r /REBOOTOK "$UN_DIR"

  ; 開始功能表：**用安裝當時記下的路徑**，不要用現在的語系重算。
  ;
  ; **動手 `RMDir /r` 之前要確認那真的是我們建的東西**：登錄檔的值被人改過、
  ; 或讀出來是空字串時，`RMDir /r` 對著錯誤的路徑跑會刪掉不該刪的
  ; （`/instdir=` 被空白截斷成 `C:\Program` 那次就是這樣）。
  ; 判準：必須以 `$SMPROGRAMS\` 開頭。
  ReadRegStr $SM_DIR HKLM "${ARP_KEY}" "${SM_FOLDER_VALUE}"
  ${If} $SM_DIR != ""
    StrLen $R8 "$SMPROGRAMS"
    StrCpy $R9 "$SM_DIR" $R8
    ${If} $R9 == "$SMPROGRAMS"
      RMDir /r "$SM_DIR"
    ${Else}
      ; 這裡已經 `FileClose $4` 了，不要寫那個記錄檔（會無聲失敗）。
      DetailPrint "refusing to delete suspicious start menu path: $SM_DIR"
    ${EndIf}
  ${EndIf}
  ; 退路：v1.15.30 以前的安裝沒有記這個值，資料夾名是寫死的中文。
  ; **這條不可以省** —— 不然從舊版升級上來的人會留下一個刪不掉的資料夾。
  RMDir /r "$SMPROGRAMS\${LEGACY_SM_FOLDER}"
  DeleteRegKey HKLM "${ARP_KEY}"

  ; 我們是從 %TEMP% 的副本跑的，所以 $UN_DIR 可以整個刪掉；萬一還有殘留
  ; （檔案被鎖），排一個脫離的 cmd 等我們結束後再清。
  IfFileExists "$UN_DIR\*.*" 0 +2
    Exec 'cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "$UN_DIR"'

  ${If} $UN_PURGE == "1"
    DetailPrint "User data purged."
  ${Else}
    DetailPrint "User data kept at %ProgramData%\${SHORTNAME}."
  ${EndIf}
SectionEnd
