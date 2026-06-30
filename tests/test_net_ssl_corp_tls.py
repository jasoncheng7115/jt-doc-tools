"""企業 TLS 攔截環境的 Python 端信任修正（2026-06-30 客戶回報）。

客戶用 TLS 檢查代理換憑證 → tessdata 下載 urlretrieve 走 Python 內建 certifi
不認企業 CA → CERTIFICATE_VERIFY_FAILED。修法：`net_ssl.install_os_trust()` 用
truststore 接 OS 原生信任庫；`JTDT_TLS_INSECURE=1` 最後手段停用驗證。
"""
from __future__ import annotations

import importlib

import app.core.net_ssl as net_ssl


def _fresh():
    importlib.reload(net_ssl)
    return net_ssl


def test_install_uses_truststore_and_is_idempotent(monkeypatch):
    monkeypatch.delenv("JTDT_TLS_INSECURE", raising=False)
    n = _fresh()
    first = n.install_os_trust()
    assert first in ("truststore", "default")  # truststore 裝了就是 truststore
    assert n.install_os_trust() == "already"   # 冪等


def test_insecure_env_disables_verification(monkeypatch):
    monkeypatch.setenv("JTDT_TLS_INSECURE", "1")
    n = _fresh()
    assert n.install_os_trust() == "insecure"


def test_tessdata_download_calls_install_os_trust():
    """tessdata 下載路徑有接上 install_os_trust（防回歸:企業 CA 環境下載失敗）。"""
    import inspect
    from app.core import tessdata_manager
    src = inspect.getsource(tessdata_manager._download_variant)
    assert "install_os_trust" in src
