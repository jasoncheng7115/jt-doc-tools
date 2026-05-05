#!/usr/bin/env bash
# ==========================================================================
# Jason Tools 文件工具箱 — 一鍵安裝（Linux / macOS）
#
# 用法：  curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.sh | sudo bash
#
# 系統需求：
#   • Linux: Ubuntu 22.04+ / Debian 12+ / Fedora 38+ 等較新發行版
#   • macOS: 12+ (Monterey 以後)
#   • 必須以 root / sudo 執行（system-wide 安裝）
# ==========================================================================
set -euo pipefail

REPO_URL="${JTDT_REPO_URL:-https://github.com/jasoncheng7115/jt-doc-tools}"
REPO_BRANCH="${JTDT_REPO_BRANCH:-main}"

# 服務綁定。預設只綁本機（最安全）。
# 三種覆寫方式（優先順序）：CLI flag > env var > 互動詢問 > 預設
BIND_HOST_DEFAULT="127.0.0.1"
BIND_PORT_DEFAULT="8765"
BIND_HOST="${JTDT_HOST:-}"
BIND_PORT="${JTDT_PORT:-}"
NO_PROMPT=0
HOST_FROM_FLAG=0   # 用 flag 指定的就跳過互動詢問

# CLI 參數解析
while [ $# -gt 0 ]; do
    case "$1" in
        --bind|--host)
            [ -z "${2:-}" ] && { echo "$1 後面要接位址，例：$1 0.0.0.0" >&2; exit 2; }
            BIND_HOST="$2"; HOST_FROM_FLAG=1; shift 2 ;;
        --port)
            [ -z "${2:-}" ] && { echo "$1 後面要接 port，例：$1 8080" >&2; exit 2; }
            BIND_PORT="$2"; shift 2 ;;
        --no-prompt|-y|--yes)
            NO_PROMPT=1; shift ;;
        --help|-h)
            cat <<EOF
用法：sudo bash install.sh [選項]

選項：
  --bind <addr>      服務監聽的位址（預設 127.0.0.1，僅本機）
                     常用：0.0.0.0 = 所有介面（內網可連）
  --port <port>      服務監聽的 port（預設 8765）
  --no-prompt, -y    全程不詢問，使用預設值（curl | bash 一行安裝會自動進入此模式）
  --help, -h         顯示這份說明

也可用環境變數：
  JTDT_HOST=0.0.0.0 sudo bash install.sh
  JTDT_PORT=8080    sudo bash install.sh

範例：
  sudo bash install.sh                              # 互動式安裝
  sudo bash install.sh --bind 0.0.0.0 --port 8080   # 內網部署
  curl -fsSL ... | sudo JTDT_HOST=0.0.0.0 bash      # 一行安裝 + 開放區網
EOF
            exit 0 ;;
        *) shift ;;
    esac
done

# 顏色輸出
C_RST='\033[0m'; C_RED='\033[31m'; C_GRN='\033[32m'; C_YEL='\033[33m'; C_CYN='\033[36m'
log()   { printf "${C_CYN}==>${C_RST} %s\n" "$*"; }
ok()    { printf "${C_GRN}✓${C_RST}  %s\n" "$*"; }
warn()  { printf "${C_YEL}⚠${C_RST}  %s\n" "$*" >&2; }
die()   { printf "${C_RED}✗${C_RST}  %s\n" "$*" >&2; exit 1; }

# --------------------------------------------------------------------- 平台

OS="$(uname -s)"
case "$OS" in
    Linux)   PLATFORM=linux ;;
    Darwin)  PLATFORM=macos ;;
    *)       die "不支援的作業系統：$OS（本程式只支援 Linux 與 macOS，Windows 請改用 install.ps1）" ;;
esac

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64)  ARCH=x86_64 ;;
    arm64|aarch64) ARCH=arm64 ;;
    *)             die "不支援的硬體：$ARCH" ;;
esac

# --------------------------------------------------------------------- 權限

if [ "$(id -u)" -ne 0 ]; then
    die "需要系統管理員權限，請改用：  sudo bash install.sh"
fi

# ----------------------------------------------------------------- 網路檢查
# 沒網路（VPN 沒開、防火牆擋、DNS 壞）就馬上喊，不要等 uv / python / git
# tarball 各自慢慢 timeout 才知道。
log "檢查網路連線 ..."
NET_OK=0
for host in github.com cdn.jsdelivr.net astral.sh; do
    if curl -fsS --max-time 8 -o /dev/null -I "https://$host/" 2>/dev/null; then
        NET_OK=1; break
    fi
done
if [ "$NET_OK" -ne 1 ]; then
    die "連不上網路（github.com / cdn.jsdelivr.net / astral.sh 都不通）。請檢查 VPN / 防火牆 / DNS 後再重跑。"
fi
ok "網路 OK"

# --------------------------------------------------------------------- 路徑

if [ "$PLATFORM" = "linux" ]; then
    INSTALL_DIR=/opt/jt-doc-tools
    DATA_DIR=/var/lib/jt-doc-tools/data
    LOG_DIR=/var/log
    SVC_FILE=/etc/systemd/system/jt-doc-tools.service
    SVC_USER=jtdt
else
    # macOS: 程式裝在 /usr/local，但「服務」是 .app (放 /Applications)。
    # 不用 LaunchAgent — OxOffice/LibreOffice 的 macOS build 只有 AquaSal
    # plugin (沒 svp/headless)，強制需要 WindowServer 連線。LaunchAgent
    # 在 gui/$uid 仍拿不到 WindowServer，subprocess 會 SIGABRT。
    # .app 透過 LaunchServices 啟動，能拿到完整 Aqua context。
    INSTALL_DIR=/usr/local/jt-doc-tools
    REAL_USER="${SUDO_USER:-}"
    # 直接以 root 身分執行（沒走 sudo），也試著找出當前 GUI console user。
    # macOS 的 .app + LaunchServices 必需以該 GUI user 身分擁有，否則
    # AquaSal 抓不到 WindowServer，soffice 子行程會 SIGABRT。
    if [ -z "$REAL_USER" ] || [ "$REAL_USER" = "root" ]; then
        # 從 /dev/console 取得目前登入桌面的使用者
        CONSOLE_USER=$(stat -f "%Su" /dev/console 2>/dev/null || true)
        if [ -n "$CONSOLE_USER" ] && [ "$CONSOLE_USER" != "root" ]; then
            warn "偵測到 root 直接執行，將以桌面登入的 user『$CONSOLE_USER』做為 .app 擁有者。"
            warn "（建議下次改用：sudo bash install.sh，避免猜測使用者）"
            REAL_USER="$CONSOLE_USER"
        else
            die "macOS 找不到非 root 桌面使用者。請以一般 user + sudo 執行：sudo bash install.sh
   原因：.app 需 GUI user 擁有，否則 OxOffice/LibreOffice 子行程會缺少 Aqua context。"
        fi
    fi
    REAL_HOME=$(eval echo "~$REAL_USER")
    REAL_UID=$(id -u "$REAL_USER")
    DATA_DIR="$REAL_HOME/Library/Application Support/jt-doc-tools/data"
    LOG_DIR="$REAL_HOME/Library/Logs"
    APP_DIR="/Applications/Jason Tools 文件工具箱.app"
    SVC_USER="$REAL_USER"
fi

CLI_LINK=/usr/local/bin/jtdt

# --------------------------------------------------------------------- 互動詢問

# 詢問監聽位址。優先順序：CLI flag > env var > 互動 (TTY) > 預設
ask_bind_host() {
    # 已經透過 flag/env 指定了 → 不問
    [ -n "$BIND_HOST" ] && return 0
    # --no-prompt → 直接用預設
    [ "$NO_PROMPT" -eq 1 ] && { BIND_HOST="$BIND_HOST_DEFAULT"; return 0; }
    # 沒 TTY（curl | bash 走這條）→ 預設並提示
    if [ ! -r /dev/tty ]; then
        BIND_HOST="$BIND_HOST_DEFAULT"
        warn "非互動模式，預設只綁 ${BIND_HOST}（僅本機）。要從區網連請改用："
        warn "  curl ... | sudo JTDT_HOST=0.0.0.0 bash"
        return 0
    fi
    # 互動詢問
    printf "\n${C_CYN}==>${C_RST} 服務監聽的網路介面：\n"
    printf "  1) ${C_GRN}127.0.0.1${C_RST}    僅本機（預設，最安全）\n"
    printf "  2) ${C_GRN}0.0.0.0${C_RST}      區網所有介面（內網部署用，要設防火牆）\n"
    printf "  3) 自訂位址\n"
    local ans
    printf "請選擇 [1/2/3] (預設 1)： "
    read -r ans </dev/tty
    case "${ans:-1}" in
        1|"")  BIND_HOST="127.0.0.1" ;;
        2)     BIND_HOST="0.0.0.0" ;;
        3)     printf "請輸入位址： "; read -r BIND_HOST </dev/tty
               [ -z "$BIND_HOST" ] && BIND_HOST="$BIND_HOST_DEFAULT" ;;
        *)     warn "未識別的選項「$ans」，使用預設 ${BIND_HOST_DEFAULT}"
               BIND_HOST="$BIND_HOST_DEFAULT" ;;
    esac
}

ask_bind_host
[ -z "$BIND_PORT" ] && BIND_PORT="$BIND_PORT_DEFAULT"

# 給後面 templates 用的「對外可顯示」URL：
# - 0.0.0.0 顯示給人看會誤導（它是 listen address，不是可連的 URL），
#   改顯示機器的主要 IP 或回 127.0.0.1
ADVERTISED_HOST="$BIND_HOST"
if [ "$BIND_HOST" = "0.0.0.0" ]; then
    # 拿一個對外可用的 IP（hostname -I 在 macOS 沒有，就 fallback）
    ADVERTISED_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [ -z "$ADVERTISED_HOST" ] && ADVERTISED_HOST="$(ipconfig getifaddr en0 2>/dev/null || echo 127.0.0.1)"
fi

# --------------------------------------------------------------------- 工具

require() {
    command -v "$1" >/dev/null 2>&1 || die "缺少必要指令：$1，請先安裝。"
}

require curl
require uname
require id

# git 不是強制必要（沒有就用 tarball），但有的話比較好
HAS_GIT=0
if command -v git >/dev/null 2>&1; then HAS_GIT=1; fi

# --------------------------------------------------------------------- Office

detect_office() {
    # 優先順序：OxOffice (台灣 OSSII fork) > LibreOffice。
    # 注意 OxOffice 的 `soffice --version` 字串仍會顯示 "LibreOffice X.Y.Z"
    # （fork 沒改）— 所以判斷一個 binary 是不是 OxOffice 不能看 --version，
    # 要看路徑（/opt/oxoffice/, /Applications/OxOffice.app）或專屬 binary
    # `oxoffice`。
    DETECTED_OFFICE=""

    # OxOffice 優先
    if [ -d "/Applications/OxOffice.app" ]; then
        DETECTED_OFFICE="OxOffice"
        local v
        v="$(defaults read /Applications/OxOffice.app/Contents/Info.plist CFBundleShortVersionString 2>/dev/null)"
        [ -n "$v" ] && DETECTED_OFFICE="OxOffice $v"
        return 0
    fi
    if command -v oxoffice >/dev/null 2>&1; then
        local v
        v="$(oxoffice --version 2>/dev/null | head -1)"
        DETECTED_OFFICE="${v:-OxOffice}"
        return 0
    fi
    if [ -x "/opt/oxoffice/program/soffice" ]; then
        local v
        v="$(/opt/oxoffice/program/soffice --version 2>/dev/null | head -1 | sed 's/LibreOffice/OxOffice/')"
        DETECTED_OFFICE="${v:-OxOffice}"
        return 0
    fi

    # LibreOffice
    if [ -d "/Applications/LibreOffice.app" ]; then
        DETECTED_OFFICE="LibreOffice"
        local v
        v="$(defaults read /Applications/LibreOffice.app/Contents/Info.plist CFBundleShortVersionString 2>/dev/null)"
        [ -n "$v" ] && DETECTED_OFFICE="LibreOffice $v"
        return 0
    fi
    local bin
    for bin in soffice libreoffice; do
        if command -v "$bin" >/dev/null 2>&1; then
            local v
            v="$("$bin" --version 2>/dev/null | head -1)"
            DETECTED_OFFICE="${v:-$bin}"
            return 0
        fi
    done
    [ -x "/usr/bin/soffice" ] && { DETECTED_OFFICE="$(/usr/bin/soffice --version 2>/dev/null | head -1 || echo soffice)"; return 0; }
    [ -x "/usr/local/bin/soffice" ] && { DETECTED_OFFICE="$(/usr/local/bin/soffice --version 2>/dev/null | head -1 || echo soffice)"; return 0; }
    return 1
}

install_oxoffice_x11_runtime_libs_linux() {
    # OxOffice / LibreOffice oosplash + cairo + GTK 啟動時 dlopen 整套 lib，
    # 即使是 --headless 模式也會。Debian / Ubuntu minimal / server 安裝缺很多，
    # apt 預設又被 --no-install-recommends 削減，會導致 office-to-pdf 一執行就掛
    # （錯誤訊息類似「libX11-xcb.so.1: cannot open shared object file」）。
    # 一次裝齊比客戶踩一個補一個好；列表須跟 app/core/sys_deps.py:_OXOFFICE_X11_LIBS
    # 同步維護。
    [ "$PLATFORM" = "linux" ] || return 0
    local pkgs="libxinerama1 libxrandr2 libxcursor1 libxi6 libxtst6 \
                libsm6 libxext6 libxrender1 \
                libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 \
                libxkbcommon0 \
                libdbus-1-3 libcups2 \
                libfontconfig1 libfreetype6 libcairo2 \
                libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
                libnss3 \
                default-jre-headless"
    log "安裝 OxOffice / LibreOffice 執行時依賴 (X11 / 字型 / Java JRE) ..."
    if command -v apt-get >/dev/null 2>&1; then
        # shellcheck disable=SC2086
        DEBIAN_FRONTEND=noninteractive apt-get install -y $pkgs \
            || warn "OxOffice 執行時依賴部分安裝失敗（office-to-pdf 可能無法啟動）"
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y \
            libXinerama libXrandr libXcursor libXi libXtst \
            libSM libXext libXrender \
            libX11-xcb libXcomposite libXdamage libXfixes \
            libxkbcommon \
            dbus-libs cups-libs \
            fontconfig freetype cairo \
            pango gdk-pixbuf2 nss \
            java-21-openjdk-headless 2>/dev/null \
            || dnf install -y \
                libXinerama libXrandr libXcursor libXi libXtst \
                libSM libXext libXrender \
                libX11-xcb libXcomposite libXdamage libXfixes \
                libxkbcommon \
                dbus-libs cups-libs \
                fontconfig freetype cairo \
                pango gdk-pixbuf2 nss \
                java-17-openjdk-headless \
            || warn "OxOffice 執行時依賴部分安裝失敗（office-to-pdf 可能無法啟動）"
    fi
}

install_oxoffice_linux() {
    log "嘗試從 GitHub 下載並安裝 OxOffice ..."
    local tmp; tmp="$(mktemp -d)"
    local api="https://api.github.com/repos/OSSII/OxOffice/releases/latest"

    # OSSII 把 Linux 套件做成 zip 打包（裡面有 ~30 個 .deb / .rpm），
    # asset 名長相是 `OxOffice-11.0.4-deb.zip` / `OxOffice-11.0.4-rpm.zip`。
    # 我們先抓 asset list 找對的 zip，下載後解壓，再用該 distro 的套件管理器
    # 把資料夾裡所有 .deb / .rpm 一次裝上。
    local pkg_kind=""
    local installer=""
    if command -v apt-get >/dev/null 2>&1; then
        pkg_kind="deb"
        installer="apt-get"
    elif command -v dnf >/dev/null 2>&1; then
        pkg_kind="rpm"
        installer="dnf"
    else
        warn "未偵測到 apt 或 dnf，無法自動安裝 OxOffice"
        return 1
    fi

    local url
    url="$(curl -fsSL "$api" \
        | grep browser_download_url \
        | grep -iE "OxOffice[^\"]*-${pkg_kind}\\.zip" \
        | head -1 | sed -E 's/.*"(https[^"]+)".*/\1/')"
    if [ -z "$url" ]; then
        warn "OxOffice latest release 找不到 ${pkg_kind} zip asset"
        return 1
    fi
    log "下載 $url（約 350-400MB，請稍候）"
    curl -fLo "$tmp/oxoffice-pack.zip" "$url" || return 1

    log "解壓套件 ..."
    if ! command -v unzip >/dev/null 2>&1; then
        log "  安裝 unzip ..."
        if [ "$installer" = "apt-get" ]; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y unzip || return 1
        else
            dnf install -y unzip || return 1
        fi
    fi
    unzip -q -o "$tmp/oxoffice-pack.zip" -d "$tmp/oxoffice/" || return 1

    log "安裝 ${pkg_kind} 套件（一次性裝 30+ 個套件，需要一兩分鐘）..."
    # 各釋出可能直接放在 root，也可能在子目錄；都搜一下
    local pkgs
    pkgs="$(find "$tmp/oxoffice/" -name "*.${pkg_kind}" -type f)"
    if [ -z "$pkgs" ]; then
        warn "解壓後找不到 .${pkg_kind} 檔"
        return 1
    fi
    # shellcheck disable=SC2086
    if [ "$installer" = "apt-get" ]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $pkgs || return 1
    else
        dnf install -y $pkgs || return 1
    fi
    rm -rf "$tmp"
    return 0
}

install_libreoffice_linux() {
    log "改用 LibreOffice ..."
    if command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y libreoffice fonts-noto-cjk
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y libreoffice google-noto-cjk-fonts
    else
        return 1
    fi
}

install_oxoffice_macos() {
    log "嘗試從 GitHub 下載並安裝 OxOffice ..."
    local tmp; tmp="$(mktemp -d)"
    local api="https://api.github.com/repos/OSSII/OxOffice/releases/latest"
    local arch_tag="x86_64"
    [ "$ARCH" = "arm64" ] && arch_tag="arm64\|aarch64"
    local url
    url="$(curl -fsSL "$api" | grep browser_download_url | grep -iE '\.dmg' | grep -iE "macos|darwin" | head -1 | sed -E 's/.*"(https[^"]+)".*/\1/')"
    [ -n "$url" ] || return 1
    log "下載 $url"
    curl -fLo "$tmp/oxoffice.dmg" "$url" || return 1
    local mnt
    mnt="$(hdiutil attach "$tmp/oxoffice.dmg" -nobrowse -noverify -noautoopen | tail -1 | awk '{print $3}')"
    [ -d "$mnt" ] || return 1
    if [ -d "$mnt/OxOffice.app" ]; then
        cp -R "$mnt/OxOffice.app" /Applications/
    fi
    hdiutil detach "$mnt" -quiet || true
    [ -d "/Applications/OxOffice.app" ] || return 1
    return 0
}

install_libreoffice_macos() {
    log "改用 LibreOffice ..."
    if command -v brew >/dev/null 2>&1; then
        brew install --cask libreoffice
    else
        warn "未安裝 Homebrew，無法自動安裝 LibreOffice"
        return 1
    fi
}

ensure_office() {
    # OxOffice 優先策略：
    #   - 已裝 OxOffice → 直接用
    #   - 已裝 LibreOffice 但沒 OxOffice → 試裝 OxOffice（OSSII 台灣 fork，
    #     CJK 支援更好），失敗就保留現有 LibreOffice
    #   - 兩者都沒裝 → OxOffice → LibreOffice
    detect_office && local PRE="$DETECTED_OFFICE" || local PRE=""

    if [ -n "$PRE" ] && echo "$PRE" | grep -qi "OxOffice"; then
        ok "已偵測到 Office 引擎：$PRE"
        return 0
    fi

    if [ -n "$PRE" ]; then
        log "已偵測到 $PRE，但 OxOffice (OSSII 台灣 fork) CJK 支援更好，嘗試補裝 ..."
    else
        log "未偵測到任何 Office 引擎"
    fi

    local ox_ok=0
    if [ "$PLATFORM" = "linux" ]; then
        install_oxoffice_linux && ox_ok=1
    else
        install_oxoffice_macos && ox_ok=1
    fi

    if [ $ox_ok -eq 1 ] && detect_office; then
        ok "OxOffice 安裝完成：$DETECTED_OFFICE"
        return 0
    fi

    # OxOffice 失敗
    if [ -n "$PRE" ]; then
        warn "OxOffice 安裝失敗，繼續用既有的 $PRE"
        return 0
    fi

    log "OxOffice 安裝失敗，改用 LibreOffice ..."
    if [ "$PLATFORM" = "linux" ]; then
        install_libreoffice_linux
    else
        install_libreoffice_macos
    fi

    if ! detect_office; then
        echo
        warn "OxOffice 與 LibreOffice 都自動安裝失敗。"
        warn "請手動安裝後再重跑安裝腳本："
        warn "  • OxOffice：    https://github.com/OSSII/OxOffice/releases"
        warn "  • LibreOffice： https://www.libreoffice.org/download/"
        exit 1
    fi
    ok "Office 引擎安裝完成：$DETECTED_OFFICE"
}

# --------------------------------------------------------------------- tesseract

# Tesseract OCR — 軟依賴。pdf-editor 在原 PDF 字型缺/壞 ToUnicode CMap 時
# 用 OCR 從 bbox 影像重建文字（例：登入系統 → 翕⊕ㄱ → OCR → 登入系統）。
# 沒裝就退到「請使用者手動重打」訊息，本體運作不受影響 — 因此這個函式
# 任何錯誤都只 warn 不 die，絕不阻擋安裝流程。
install_tesseract() {
    if command -v tesseract >/dev/null 2>&1; then
        local langs
        langs="$(tesseract --list-langs 2>/dev/null | tail -n +2 | tr '\n' ' ')"
        if echo "$langs" | grep -q "chi_tra"; then
            ok "tesseract 已存在，繁中訓練檔已就緒"
            return 0
        fi
        log "tesseract 已存在但缺繁中訓練檔，補裝 chi_tra ..."
    else
        log "安裝 tesseract OCR（pdf-editor 文字辨識；不安裝也可運作但失去 OCR 能力）..."
    fi
    if [ "$PLATFORM" = "linux" ]; then
        if command -v apt-get >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y \
                tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-eng \
                || { warn "tesseract 自動安裝失敗 — pdf-editor OCR 功能會停用，其餘功能正常"; return 0; }
        elif command -v dnf >/dev/null 2>&1; then
            dnf install -y tesseract tesseract-langpack-chi_tra tesseract-langpack-eng \
                || { warn "tesseract 自動安裝失敗 — pdf-editor OCR 功能會停用，其餘功能正常"; return 0; }
        else
            warn "未支援的 Linux 發行版，請手動裝 tesseract + chi_tra"
            return 0
        fi
    elif [ "$PLATFORM" = "macos" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install tesseract tesseract-lang \
                || { warn "tesseract 自動安裝失敗 — pdf-editor OCR 功能會停用，其餘功能正常"; return 0; }
        else
            warn "未安裝 Homebrew，請手動 brew install tesseract tesseract-lang"
            return 0
        fi
    fi
    if command -v tesseract >/dev/null 2>&1 \
       && tesseract --list-langs 2>/dev/null | grep -q "chi_tra"; then
        ok "tesseract + 繁中訓練檔安裝完成"
    else
        warn "tesseract 安裝後仍偵測不到 chi_tra — pdf-editor OCR 功能將停用"
    fi
    return 0
}

# --------------------------------------------------------------------- uv

install_uv() {
    if [ -x "$INSTALL_DIR/bin/uv" ]; then
        ok "uv 已存在於 $INSTALL_DIR/bin/uv"
        return 0
    fi
    log "下載 uv (Astral Python 工具鏈) ..."
    mkdir -p "$INSTALL_DIR/bin"
    # uv 官方安裝腳本，限定安裝路徑避免污染使用者環境
    curl -LsSf https://astral.sh/uv/install.sh | \
        env UV_INSTALL_DIR="$INSTALL_DIR/bin" UV_NO_MODIFY_PATH=1 sh >/dev/null
    [ -x "$INSTALL_DIR/bin/uv" ] || die "uv 安裝失敗"
    ok "uv 安裝在 $INSTALL_DIR/bin/uv"
}

# --------------------------------------------------------------------- 程式碼

fetch_code() {
    if [ -d "$INSTALL_DIR/.git" ]; then
        log "已存在安裝，更新 git 內容 ..."
        # 安裝後 install dir chown 給 SVC_USER (jtdt)，root 跑 git 會撞
        # 「dubious ownership in repository」(git 2.35.2+ 安全機制)。
        # 把 install dir 加進系統級 safe.directory 白名單，永久解決，
        # 後續 `sudo jtdt update` 即使是舊版 cli.py 也能正常 git pull。
        if [ $HAS_GIT -eq 1 ]; then
            git config --system --add safe.directory "$INSTALL_DIR" 2>/dev/null \
                || git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null \
                || true
        fi
        (cd "$INSTALL_DIR" && git fetch --depth=1 origin "$REPO_BRANCH" && git reset --hard "origin/$REPO_BRANCH")
        return 0
    fi
    if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
        die "$INSTALL_DIR 已存在但不是 git repo，請先備份/移除再重跑"
    fi
    mkdir -p "$INSTALL_DIR"
    if [ $HAS_GIT -eq 1 ]; then
        log "從 $REPO_URL clone 程式碼 ..."
        # 預先設 safe.directory，新 install 下次 sudo jtdt update 也能用
        git config --system --add safe.directory "$INSTALL_DIR" 2>/dev/null \
            || git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null \
            || true
        git clone --depth=1 --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
    else
        log "git 未安裝，改用 tarball 下載 ..."
        local tmp; tmp="$(mktemp -d)"
        curl -fL "$REPO_URL/archive/refs/heads/${REPO_BRANCH}.tar.gz" -o "$tmp/src.tgz"
        tar -xzf "$tmp/src.tgz" -C "$tmp"
        # 把第一層解出來的內容搬進 INSTALL_DIR
        local extracted
        extracted="$(ls -d "$tmp"/*/ | head -1)"
        cp -a "$extracted." "$INSTALL_DIR/"
        rm -rf "$tmp"
    fi
}

setup_python() {
    log "建立獨立 Python 環境並安裝依賴 (uv sync) ..."
    cd "$INSTALL_DIR"
    # 注意：絕不能用 --frozen — 那會盲信 uv.lock，若 lockfile 漏了某個 dep
    # （v1.1.66 之前的 uv.lock 漏 ldap3 就是這樣），uv 會「成功」回傳但實際
    # 少裝套件，最後使用者啟認證後 ldap3 import 失敗無法登入。
    # 一律走完整 reconcile，與 pyproject.toml 比對後實際補裝。
    "$INSTALL_DIR/bin/uv" sync || die "uv sync 失敗"
    [ -x "$INSTALL_DIR/.venv/bin/python" ] || die "Python venv 建立失敗"
    log "驗證關鍵依賴可正常 import ..."
    "$INSTALL_DIR/.venv/bin/python" -c \
        "import fastapi, fitz, ldap3, PIL, pdfplumber, docx, odf, pyzipper, httpx" \
        || die "依賴 import 失敗 — 安裝不完整，請查看上方錯誤"
    ok "Python 環境就緒：$INSTALL_DIR/.venv"
}

# --------------------------------------------------------------------- 資料

prepare_data() {
    log "準備資料目錄 $DATA_DIR ..."
    mkdir -p "$DATA_DIR"
    mkdir -p "$LOG_DIR"
    # 把 repo 裡的種子 data 複製過去（只在 DATA_DIR 不存在或為空時）
    if [ -d "$INSTALL_DIR/data" ] && [ -z "$(ls -A "$DATA_DIR" 2>/dev/null)" ]; then
        cp -a "$INSTALL_DIR/data/." "$DATA_DIR/"
    fi
    if [ "$PLATFORM" = "linux" ]; then
        id "$SVC_USER" >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d "$DATA_DIR" "$SVC_USER"
        chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR" "$INSTALL_DIR"
    else
        # macOS: data + log 是該 user 的，要 chown 給他（不是 root）
        # 還有 LaunchAgents 目錄
        mkdir -p "$REAL_HOME/Library/LaunchAgents"
        chown "$REAL_USER" "$REAL_HOME/Library/LaunchAgents"
        chown -R "$REAL_USER" "$DATA_DIR" "$LOG_DIR"
        # 上層的 "Application Support/jt-doc-tools/" 也要他擁有
        chown "$REAL_USER" "$(dirname "$DATA_DIR")"
    fi
}

# --------------------------------------------------------------------- 服務

install_service_linux() {
    log "安裝 systemd 服務 $SVC_FILE ..."
    cat > "$SVC_FILE" <<EOF
[Unit]
Description=Jason Tools 文件工具箱
After=network.target

[Service]
Type=simple
User=$SVC_USER
WorkingDirectory=$INSTALL_DIR
Environment=JTDT_DATA_DIR=$DATA_DIR
Environment=JTDT_HOST=$BIND_HOST
Environment=JTDT_PORT=$BIND_PORT
ExecStart=$INSTALL_DIR/.venv/bin/python -m app.main
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable jt-doc-tools
    systemctl restart jt-doc-tools
}

disable_office_restore_dialog() {
    # 防止 OxOffice / LibreOffice 在背景跑時跳「上次未正常關閉，要重開視窗？」
    # 這 dialog 會卡住 osascript do-shell-script，導致轉檔超時。
    log "停用 OxOffice / LibreOffice 的「視窗復原」對話框 ..."
    for bid in tw.com.oxoffice org.libreoffice.script; do
        sudo -u "$REAL_USER" defaults write "$bid" ApplePersistenceIgnoreState -bool YES 2>/dev/null || true
        sudo -u "$REAL_USER" defaults write "$bid" NSQuitAlwaysKeepsWindows -bool NO 2>/dev/null || true
    done
    # 清掉前次殘留的 saved state
    sudo -u "$REAL_USER" rm -rf \
        "$REAL_HOME/Library/Saved Application State/tw.com.oxoffice.savedState" \
        "$REAL_HOME/Library/Saved Application State/org.libreoffice.script.savedState" \
        2>/dev/null || true
}

install_service_macos() {
    disable_office_restore_dialog
    log "建立 macOS 應用程式 $APP_DIR ..."
    rm -rf "$APP_DIR"
    mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

    # 從 app/main.py 動態讀版本號（單一真相：main.py:VERSION），避免 plist
    # 寫死後常忘記改。
    local APP_VERSION
    APP_VERSION="$(awk -F'"' '/^VERSION = /{print $2; exit}' "$INSTALL_DIR/app/main.py" 2>/dev/null)"
    [ -z "$APP_VERSION" ] && APP_VERSION="1.0.0"

    # Info.plist
    cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIdentifier</key><string>com.jasontools.doctools</string>
  <key>CFBundleName</key><string>Jason Tools 文件工具箱</string>
  <key>CFBundleDisplayName</key><string>Jason Tools 文件工具箱</string>
  <key>CFBundleShortVersionString</key><string>$APP_VERSION</string>
  <key>CFBundleVersion</key><string>$APP_VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleSignature</key><string>????</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <!-- LSUIElement=true → 不在 Dock 顯示 icon。launcher exec python 後
       這個 .app process 會持續執行（=就是 FastAPI 服務），不需要 dock entry。
       注意：不能用 LSBackgroundOnly，那會讓子行程拿不到 WindowServer 連線，
       導致 soffice 無法啟動 (AquaSal 必須有 GUI session)。 -->
  <key>LSUIElement</key><true/>
</dict>
</plist>
EOF

    # Launcher script: ensure service running + open browser.
    #
    # CRITICAL: We do NOT background/nohup/disown the python process — instead
    # we exec it as the .app's foreground process. Reason: child processes of
    # this .app spawn soffice via osascript, which requires a full Aqua/
    # WindowServer bootstrap inherited from the parent. nohup+disown reparents
    # python to launchd PID 1 and severs that bootstrap chain → grandchild
    # soffice gets a degraded GUI context and crashes inside NSApplicationMain
    # (AquaSal). With LSUIElement=true, "foreground" doesn't mean a Dock icon.
    cat > "$APP_DIR/Contents/MacOS/launcher" <<EOF
#!/bin/sh
INSTALL_DIR=$INSTALL_DIR
DATA_DIR="$DATA_DIR"
LOG_DIR="$LOG_DIR"
# Browser URL always uses 127.0.0.1 (we're on the same Mac as the service);
# BIND_HOST may be 0.0.0.0 but localhost still routes there.
URL="http://127.0.0.1:$BIND_PORT/"

mkdir -p "\$LOG_DIR"

# Service already up — just open the browser and exit.
if /usr/bin/curl -s -f "\${URL}healthz" >/dev/null 2>&1; then
    /usr/bin/open "\$URL"
    exit 0
fi

# Wait for the (about-to-be-exec'd) service to come up, then open browser.
# Runs in a backgrounded subshell so the curl loop doesn't block the exec
# below. This subshell is short-lived (≤60s) and inherits our env, which
# is fine — it never spawns soffice.
(
    for i in \$(seq 1 60); do
        /bin/sleep 1
        if /usr/bin/curl -s -f "\${URL}healthz" >/dev/null 2>&1; then
            /usr/bin/open "\$URL"
            exit 0
        fi
    done
) &

# Replace this shell with python so the .app process IS the FastAPI service.
# Aqua bootstrap chain stays intact for any subprocess.Popen(soffice/osascript)
# we make later.
exec env JTDT_DATA_DIR="\$DATA_DIR" JTDT_HOST=$BIND_HOST JTDT_PORT=$BIND_PORT \\
    "\$INSTALL_DIR/.venv/bin/python" -m app.main \\
    >> "\$LOG_DIR/jt-doc-tools.log" 2>> "\$LOG_DIR/jt-doc-tools.err"
EOF
    chmod +x "$APP_DIR/Contents/MacOS/launcher"

    # 把 logo 複製成 icon（簡單的 PNG，macOS 也接受 .icns 但 PNG fallback OK）
    if [ -f "$INSTALL_DIR/static/images/logo-on-light.png" ]; then
        cp "$INSTALL_DIR/static/images/logo-on-light.png" "$APP_DIR/Contents/Resources/AppIcon.png" 2>/dev/null || true
    fi

    chown -R "$REAL_USER" "$APP_DIR"

    # 註冊為登入項目，讓使用者下次登入自動啟動。
    # `make login item` 回傳新建立的 reference，會印到 stdout (舊 macOS 印
    # 完整描述、新 macOS 會印 "login item UNKNOWN")。我們不在乎這個輸出，
    # 一律丟掉避免雜訊。
    log "加入登入項目（自動啟動）..."
    sudo -u "$REAL_USER" osascript >/dev/null 2>&1 <<EOF || warn "登入項目註冊失敗（可能需要在「系統設定 → 一般 → 登入項目」手動加入）"
tell application "System Events"
    if not (exists login item "Jason Tools 文件工具箱") then
        make login item at end with properties {path:"$APP_DIR", hidden:true}
    end if
end tell
EOF

    # 立刻啟動一次（透過 open 走 LaunchServices，建立 Aqua context）
    log "啟動 .app（透過 LaunchServices 取得 GUI session）..."
    sudo -u "$REAL_USER" /usr/bin/open -a "$APP_DIR" 2>&1 || true
}

install_service() {
    if [ "$PLATFORM" = "linux" ]; then
        install_service_linux
    else
        install_service_macos
    fi
    ok "服務已啟用，開機自動啟動"
}

# --------------------------------------------------------------------- jtdt CLI

install_cli() {
    log "建立 $CLI_LINK 指令 ..."
    # `cd` 進 install dir 是必要的：`python -m app.cli` 會把 cwd 加進
    # sys.path[0]，如果使用者從別處（例如 /tmp/foo）跑 jtdt 而那裡剛好也有
    # `app/cli.py`，會載到錯的模組，`_install_root()` 也會回到錯的路徑。
    printf '#!/bin/sh\ncd "%s" && exec "%s/.venv/bin/python" -m app.cli "$@"\n' \
        "$INSTALL_DIR" "$INSTALL_DIR" > "$CLI_LINK"
    chmod 755 "$CLI_LINK"
    ok "jtdt 指令安裝在 $CLI_LINK"
}

# --------------------------------------------------------------------- 健康檢查

health_check() {
    log "等待服務啟動 ..."
    # Always probe via 127.0.0.1 — even if BIND_HOST=0.0.0.0, localhost
    # still routes to the listener.
    local url="http://127.0.0.1:$BIND_PORT/healthz"
    local i=0
    while [ $i -lt 30 ]; do
        if curl -fsS "$url" >/dev/null 2>&1; then
            ok "服務已上線：http://${ADVERTISED_HOST}:$BIND_PORT/"
            return 0
        fi
        i=$((i+1))
        sleep 1
    done
    warn "30 秒內未通過健康檢查，請執行：jtdt logs"
    return 1
}

# --------------------------------------------------------------------- 主流程

main() {
    echo
    log "Jason Tools 文件工具箱 — 系統安裝"
    log "平台：$PLATFORM ($ARCH)"
    log "程式：$INSTALL_DIR"
    log "資料：$DATA_DIR"
    echo

    install_oxoffice_x11_runtime_libs_linux
    ensure_office
    install_tesseract
    fetch_code
    install_uv
    setup_python
    prepare_data
    install_service
    install_cli
    health_check

    echo
    ok "安裝完成！"
    echo
    echo "  介面：    http://${ADVERTISED_HOST}:${BIND_PORT}/"
    if [ "$BIND_HOST" = "0.0.0.0" ] || [ "$BIND_HOST" = "::" ]; then
        echo "  ⚠  服務開放給所有網路介面，請設好防火牆 / 反向代理"
    fi
    echo "  狀態：    jtdt status"
    echo "  記錄：    jtdt logs -f"
    echo "  升級：    sudo jtdt update"
    echo "  解除：    sudo jtdt uninstall    （加 --purge 連同資料一起刪）"
    echo
}

main "$@"
