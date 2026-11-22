"""Linux 服務的安全強化：一行安裝產生的 systemd unit 要跟附帶的範本有同一組限制（外部稽核 F02，v1.15.31）。

**附了一份硬化範本 ≠ 一行安裝裝出來的服務受保護**：當時 `install.sh` 產生的 unit 只有 `User=`，
範本卻有五項。這裡比對兩邊：每一項都要在、值要一樣（`ReadWritePaths` 一邊是變數、一邊是預設路徑，只比「有」）。
`install.sh` 的註解指向這一支（原本寫在 `test_installer_product_name.py` 裡，2026-11-22 搬過來）。
"""
from __future__ import annotations

import pathlib
import re

import pytest

from tools.repo_paths import public_root

ROOT = pathlib.Path(__file__).resolve().parents[1]
PUB = public_root(ROOT)
UNIT = PUB / "packaging" / "jt-doc-tools.service"
INSTALL_SH = PUB / "install.sh"


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


def _directives(text: str) -> dict[str, str]:
    """`[Service]` 那一段的指令（後面寫的蓋前面的；註解不算）。"""
    out: dict[str, str] = {}
    for m in re.finditer(r"^\s*([A-Za-z]+)=(.*)$", text, re.M):
        out[m.group(1)] = m.group(2).strip()
    return out


def _generated_unit(sh: str) -> str:
    """`install.sh` 寫 unit 的那一段 heredoc（`[Unit]` 到 `WantedBy=`）。"""
    m = re.search(r"^\[Unit\]$.*?^WantedBy=.*?$", sh, re.M | re.S)
    assert m, "install.sh 裡找不到產生 systemd unit 的那一段"
    return m.group(0)


@pytest.mark.parametrize("directive", [d for d in _HARDENING if d != "ReadWritePaths"])
def test_the_two_units_use_the_same_values(directive):
    """不只要有，值也要一樣（`ProtectSystem=full` 跟 `strict` 差很多：full 不保護 /etc 以外的路徑）。"""
    tpl = _directives(UNIT.read_text(encoding="utf-8"))
    gen = _directives(_generated_unit(INSTALL_SH.read_text(encoding="utf-8")))
    assert gen.get(directive) == tpl.get(directive), (directive, gen.get(directive), tpl.get(directive))


def test_the_install_script_comment_points_here():
    """`install.sh` 註解寫著「檢查 tests/test_service_unit_hardening.py」—— 那支測試要真的存在、而且就是這一支。"""
    sh = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(r"tests/(test_\w+\.py)", sh)
    assert m and (ROOT / "tests" / m.group(1)).is_file() and m.group(1) == pathlib.Path(__file__).name
