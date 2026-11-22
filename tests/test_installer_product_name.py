"""Windows 安裝程式的產品名稱多語系 + Linux 服務的安全強化（第 1 批，v1.15.31）。

**兩件事的共同點是「附了一份東西」不等於「使用者拿到的東西有那些性質」**：

* 安裝程式宣告了英文，但產品名稱、開始功能表、「程式和功能」寫死中文；
* 附了一份有五項硬化設定的 systemd 範本，但**一行安裝產生的 unit 只有
  `User=`**（外部稽核 F02；那一半的檢查 2026-11-22 搬到 `test_service_unit_hardening.py`）。

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


def _nsi() -> str:
    return NSI.read_text(encoding="utf-8")


def _nsi_code() -> str:
    """只看程式，不看註解 —— **行尾註解也要去掉**（`tools/nsis_source`）。

    這支測試第一版只跳過「開頭是 `;`」的行，於是
    `Var SM_DIR      ; 這次安裝…` 這種行尾註解被判成「寫死的中文」。
    這個專案的靜態檢查已經被解釋規則的註解騙過四次，所以邏輯收成一份共用。
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
    # **語言清單要從宣告實算** —— 寫死的話，加第三種語言之後這條會
    # 「照樣全綠，只是沒在驗那個語言」（本專案在 i18n 上踩過八次）。
    from tools.nsis_source import declared_languages
    langs = declared_languages(_nsi())
    assert len(langs) >= 2, f"只看到 {langs}，語言宣告大概解析錯了"
    for lang in sorted(langs):
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
    assert 'DetailPrint "$(UN_SM_REFUSED)"' in code, (
        '路徑可疑時要留下痕跡（那時 $4 記錄檔已經關了，要用 DetailPrint）')
    # v1.16.57 起這一行跟著介面語言（原本寫死英文）—— 每一種語言都要講出是哪個路徑
    refused = [ln for ln in code.splitlines() if ln.startswith("LangString UN_SM_REFUSED")]
    assert refused and all("$SM_DIR" in ln for ln in refused), (
        "UN_SM_REFUSED 每一種語言都要帶出那個被擋下來的路徑（$SM_DIR）")


def test_every_language_folder_name_is_cleaned_up_on_uninstall():
    """解除安裝要試著刪掉**每一種語言**的開始功能表資料夾，加上舊的中文名。

    ## 為什麼

    資料夾名同時是路徑，而登錄檔只記得住「最後一次安裝建的那一個」。
    先用語言 A 裝、再用語言 B 裝、然後解除安裝 —— **A 的資料夾就變成刪不掉
    的孤兒**。2026-09-16 在 Windows 實機上真的發生了：英文的
    `Jason Tools Document Toolbox` 留在開始功能表裡。

    **判準走宣告的語言清單**（不要寫死語言數）：每宣告一種語言，就要有對應的
    `SM_FOLDER_xx` 而且出現在清理清單裡 —— 加第四種語言時這條會先紅。
    """
    from tools.nsis_source import declared_languages
    code = _nsi_code()
    raw = _nsi()

    suffix = {"TRADCHINESE": "ZH", "ENGLISH": "EN", "JAPANESE": "JA"}
    langs = declared_languages(raw)
    assert langs, "解析不到語言宣告"

    missing = []
    for lang in sorted(langs):
        sfx = suffix.get(lang)
        assert sfx, f"新語言 {lang} 還沒有對應的 SM_FOLDER_ 後綴，補在這裡"
        if f"!define SM_FOLDER_{sfx}" not in code:
            missing.append(f"少了 !define SM_FOLDER_{sfx}")
        elif f"_RmSmFolder \"${{SM_FOLDER_{sfx}}}\"" not in code:
            missing.append(f"解除安裝沒有清理 SM_FOLDER_{sfx}")
    assert "LEGACY_SM_FOLDER" in code
    if '_RmSmFolder "${LEGACY_SM_FOLDER}"' not in code:
        missing.append("解除安裝沒有清理舊版寫死的中文資料夾")
    assert not missing, "\n".join(missing)


def test_the_installer_also_removes_the_previously_recorded_folder():
    """安裝時如果登錄檔記的是**別的**資料夾，要先清掉它。

    只靠解除安裝清是不夠的：換語言重裝之後，兩個資料夾會同時掛在開始功能表
    上，而使用者不會知道哪一個是活的。
    """
    code = _nsi_code()
    assert 'ReadRegStr $R0 HKLM "${ARP_KEY}" "${SM_FOLDER_VALUE}"' in code, (
        "安裝時沒有讀回上一次記下的資料夾")
    assert 'RMDir /r "$R0"' in code, "讀了卻沒有清掉舊的那一個"


def test_paths_and_identity_still_use_the_fixed_short_name():
    """安裝目錄、服務名、登錄檔鍵路徑一律用 `SHORTNAME` —— 那是身分。"""
    code = _nsi_code()
    assert 'InstallDir "$PROGRAMFILES64\\${SHORTNAME}"' in code
    assert '${ARP_KEY}      "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${SHORTNAME}"' \
        in code.replace("!define ", "${") or "Uninstall\\${SHORTNAME}" in code


def test_no_hard_coded_chinese_is_left_outside_language_strings():
    """這一批做完，`_DEFERRED` 應該是空的。

    判準依「這一行屬於哪個語言表」分流，**不是一律看有沒有漢字** ——
    日文的譯文本來就有漢字。邏輯收在 `tools.nsis_source`，
    與 `test_installer_languages.py` **共用同一份**（v1.15.49 加日文時
    只改了其中一支，另一支當場紅 —— 同一個家族要一次掃完）。
    """
    from tools.nsis_source import (CJK_IS_NATIVE, code_lines,
                                   langstring_language,
                                   looks_like_untranslated_chinese)
    cjk = re.compile(r"[　-〿一-鿿＀-￯]")
    bad = []
    for n, ln in code_lines(_nsi()):
        lang = langstring_language(ln)
        if lang in CJK_IS_NATIVE:
            if looks_like_untranslated_chinese(ln, lang):
                bad.append(f"{n}: {lang} 的條目裡留著中文 —— {ln.strip()[:50]}")
            continue
        if not cjk.search(ln):
            continue
        # 這幾個 `!define` 是**名字同時是路徑**的那一類（開始功能表資料夾）：
        # 它們必須是固定的字面值，解除安裝才刪得掉。顯示用的字走 LangString。
        if any(k in ln for k in ("LEGACY_SM_FOLDER", "!define APPNAME",
                                 "!define SM_FOLDER_")):
            continue
        bad.append(f"{n}: {ln.strip()[:60]}")
    assert not bad, "安裝程式裡還有寫死的中文：\n" + "\n".join(bad)
