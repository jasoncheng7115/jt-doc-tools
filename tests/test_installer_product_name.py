"""Windows 安裝程式的產品名稱多語系 + Linux 服務的安全強化（第 1 批，v1.15.31）。

**兩件事的共同點是「附了一份東西」不等於「使用者拿到的東西有那些性質」**：

* 安裝程式宣告了英文，但產品名稱、開始功能表、「程式和功能」寫死中文；
* 附了一份有五項硬化設定的 systemd 範本，但**一行安裝產生的 unit 只有
  `User=`**（外部稽核 F02）。

**名字同時是路徑時最容易出事**：安裝時用語系 A 建了開始功能表資料夾，
解除安裝時系統語系變成 B → `RMDir` 找不到 → 解除安裝「成功」但留下殘骸。
所以實際建出來的路徑要寫進登錄檔，解除安裝時讀回來。
"""
from __future__ import annotations

import pathlib
import re

import pytest

from tools.repo_paths import public_root

ROOT = pathlib.Path(__file__).resolve().parents[1]
PUB = public_root(ROOT)
NSI = PUB / "packaging" / "windows" / "installer.nsi"
UNIT = PUB / "packaging" / "jt-doc-tools.service"
INSTALL_SH = PUB / "install.sh"


def _nsi() -> str:
    return NSI.read_text(encoding="utf-8")


def _nsi_code() -> str:
    """只看程式，不看註解 —— **行尾註解也要去掉**（`tools/nsis_source`）。

    這支測試第一版只跳過「開頭是 `;`」的行，於是
    `Var SM_DIR      ; 這次安裝…` 這種行尾註解被判成「寫死的中文」。
    這個專案的靜態守門已經被解釋規則的註解騙過四次，所以邏輯收成一份共用。
    """
    from tools.nsis_source import code_text
    return code_text(_nsi())


# ------------------------------------------------------------- 顯示名稱

def test_the_window_title_uses_a_language_string():
    assert re.search(r'^Name\s+"\$\(APP_DISPLAY\)', _nsi_code(), re.M), (
        "精靈標題還是寫死的 —— NSIS 的 `Name` 吃語言字串")


def test_the_add_remove_programs_name_uses_a_language_string():
    assert re.search(r'"DisplayName"\s+"\$\(APP_DISPLAY\)"', _nsi_code()), (
        "「程式和功能」的顯示名稱還是寫死中文（那是登錄檔的值，可以隨語系變）")


def test_the_display_name_exists_in_both_languages():
    code = _nsi_code()
    for lang in ("TRADCHINESE", "ENGLISH"):
        assert re.search(rf'LangString APP_DISPLAY\s+\$\{{LANG_{lang}\}}', code), \
            f"APP_DISPLAY 少了 {lang}"


# ------------------------------------------------- 名字同時是路徑的那一半

def test_the_start_menu_folder_is_recorded_in_the_registry():
    """解除安裝要**讀安裝當時記下的路徑**，不可以用當下的語系重算。"""
    code = _nsi_code()
    assert re.search(r'WriteRegStr.*"\$\{SM_FOLDER_VALUE\}"\s+"\$SM_DIR"', code), (
        "沒有把實際建出來的開始功能表資料夾寫進登錄檔")
    assert re.search(r'ReadRegStr \$SM_DIR .*"\$\{SM_FOLDER_VALUE\}"', code), (
        "解除安裝沒有從登錄檔讀回那個路徑")


def test_the_uninstaller_does_not_recompute_the_folder_from_the_display_name():
    """`$SMPROGRAMS\\$(SM_FOLDER)` 這種寫法在解除安裝段就是 bug。"""
    code = _nsi_code()
    tail = code.split("Section \"Uninstall\"")[-1] if "Section \"Uninstall\"" in code \
        else code[code.find("un_dir_ok"):]
    assert "$SMPROGRAMS\\$(SM_FOLDER)" not in tail, (
        "解除安裝又用語系重算開始功能表路徑了")


def test_the_recorded_path_is_validated_before_deleting():
    """`RMDir /r` 之前要確認那真的是開始功能表底下的路徑。

    登錄檔的值被改過、或讀出來是空的時候，對著錯誤的路徑遞迴刪除是災難
    （`/instdir=` 被空白截斷成 `C:\\Program` 那次就是這樣）。
    """
    code = _nsi_code()
    assert 'StrCpy $R9 "$SM_DIR" $R8' in code, (
        '刪除前沒有檢查那個路徑是不是在開始功能表底下')
    assert 'DetailPrint "refusing to delete' in code, (
        '路徑可疑時要留下痕跡（那時 $4 記錄檔已經關了，要用 DetailPrint）')


def test_the_legacy_chinese_folder_is_still_cleaned_up():
    """v1.15.30 以前的安裝沒有記那個值，資料夾名是寫死的中文。

    **這條退路不可以省** —— 不然從舊版升級上來的人會留下刪不掉的資料夾。
    """
    code = _nsi_code()
    assert "LEGACY_SM_FOLDER" in code
    assert re.search(r'RMDir /r "\$SMPROGRAMS\\\$\{LEGACY_SM_FOLDER\}"', code), (
        "沒有清理舊版寫死的中文資料夾")


def test_paths_and_identity_still_use_the_fixed_short_name():
    """安裝目錄、服務名、登錄檔鍵路徑一律用 `SHORTNAME` —— 那是身分。"""
    code = _nsi_code()
    assert 'InstallDir "$PROGRAMFILES64\\${SHORTNAME}"' in code
    assert '${ARP_KEY}      "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${SHORTNAME}"' \
        in code.replace("!define ", "${") or "Uninstall\\${SHORTNAME}" in code


def test_no_hard_coded_chinese_is_left_outside_language_strings():
    """這一批做完，`_DEFERRED` 應該是空的。"""
    from tools.nsis_source import code_lines
    cjk = re.compile(r"[　-〿一-鿿＀-￯]")
    bad = []
    for n, ln in code_lines(_nsi()):
        if not cjk.search(ln):
            continue
        if re.match(r'\s*LangString\s+\w+\s+\$\{LANG_TRADCHINESE\}', ln):
            continue
        if "LEGACY_SM_FOLDER" in ln or "!define APPNAME" in ln:
            continue        # 舊版相容用的字面值，刻意保留
        bad.append(f"{n}: {ln.strip()[:60]}")
    assert not bad, "安裝程式裡還有寫死的中文：\n" + "\n".join(bad)


# ------------------------------------------------------------- F02 硬化

_HARDENING = ("NoNewPrivileges", "PrivateTmp", "ProtectSystem",
              "ProtectHome", "ReadWritePaths")


def _has_directive(text: str, name: str) -> bool:
    """那一行**真的是指令賦值**，不是註解裡提到名字。

    這條測試第一版用 `name in text`，而我在 install.sh 裡寫的說明剛好列了
    每個指令的用途（「NoNewPrivileges — 不能再取得更高權限」）——
    於是把五個指令全部刪掉，測試照樣綠燈。**這是同一個坑第五次。**
    """
    return re.search(rf"^\s*{re.escape(name)}=", text, re.M) is not None


@pytest.mark.parametrize("directive", _HARDENING)
def test_the_generated_unit_has_the_same_hardening_as_the_template(directive):
    """**附一份硬化範本 ≠ 一行安裝裝出來的服務受保護**（外部稽核 F02）。"""
    tpl = UNIT.read_text(encoding="utf-8")
    sh = INSTALL_SH.read_text(encoding="utf-8")
    assert _has_directive(tpl, directive), f"範本自己少了 {directive}"
    assert _has_directive(sh, directive), (
        f"install.sh 產生的 unit 少了 {directive} —— "
        "客戶用一行安裝裝出來的服務沒有這層保護")


def test_the_writable_path_is_the_data_dir_only():
    """可寫路徑只能是資料目錄 —— 不可以把安裝目錄也開放寫入。

    服務帳號能改程式 = 解析器被攻破後可以改我們自己的程式碼。
    """
    sh = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(r"^ReadWritePaths=(.+)$", sh, re.M)
    assert m, "install.sh 沒有 ReadWritePaths"
    assert "$DATA_DIR" in m.group(1)
    assert "INSTALL_DIR" not in m.group(1), "安裝目錄被開放寫入了"


def test_the_external_export_path_caveat_is_documented():
    """`ProtectSystem=strict` 會擋掉管理員設在資料目錄外的匯出路徑。

    這是**已知且刻意**的取捨（CLAUDE.md 記過：要維持可指定 /mnt/backup），
    所以產生出來的 unit 裡要寫著「怎麼加」，不然管理員只會看到
    Read-only file system 而查不出原因。
    """
    sh = INSTALL_SH.read_text(encoding="utf-8")
    assert "ReadWritePaths=/mnt/backup" in sh or "ReadWritePaths=那個路徑" in sh, (
        "沒有告訴管理員外部匯出路徑要自己加 ReadWritePaths")
    tpl = UNIT.read_text(encoding="utf-8")
    assert "Read-only file system" in tpl or "ReadWritePaths=那個路徑" in tpl
