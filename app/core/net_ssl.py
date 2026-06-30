"""企業 TLS 攔截環境的 Python 端憑證信任修正。

問題：公司用 TLS 檢查代理把外部 HTTPS 的憑證換成自家 CA 簽的。該 CA 通常裝在
**OS 系統信任庫**（Windows 憑證存放區 / macOS 鑰匙圈 / Linux ca-certificates）,
但 Python（尤其 uv 管理的 standalone Python）用的是內建 certifi 憑證庫,**不認**
那個企業 CA → 下載 tessdata / 任何 HTTPS urlopen 都 `CERTIFICATE_VERIFY_FAILED`
（常見 `Missing Authority Key Identifier`,因為 Python 連企業 CA 都找不到、建不了鏈）。

修法（與 cli.py 的 uv 設定同精神,見 feedback_corp_tls_uv_system_certs）：
  ① 預設用 **truststore** 把 stdlib `ssl` 接到 **OS 原生信任庫** → 企業 CA 自然認得。
     一次 inject,`urllib` / `urlretrieve` / `httpx` 預設 context 全部受惠。
  ② Python 預設 context 也會認 `SSL_CERT_FILE`（指向企業 CA bundle）。
  ③ `JTDT_TLS_INSECURE=1`（最後手段）→ 完全停用憑證驗證。
"""
from __future__ import annotations

import logging
import os
import ssl

log = logging.getLogger(__name__)

_done = False


def _insecure() -> bool:
    return os.environ.get("JTDT_TLS_INSECURE", "").strip().lower() in (
        "1", "true", "yes", "on")


def install_os_trust() -> str:
    """讓 Python stdlib ssl 信任 OS 原生信任庫（含企業 CA）。冪等,可重複呼叫。
    回簡短狀態字串供 log。"""
    global _done
    if _done:
        return "already"
    _done = True

    if _insecure():
        # 最後手段：停用驗證（與 uv 的 JTDT_TLS_INSECURE 一致）。
        ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[attr-defined]
        log.warning("JTDT_TLS_INSECURE 已設 — TLS 憑證驗證已停用（不建議,僅供企業攔截環境暫用）")
        return "insecure"

    try:
        import truststore  # pure-python, 有通用 wheel
        truststore.inject_into_ssl()
        log.info("已將 Python ssl 接到 OS 原生信任庫（truststore）— 企業 CA 可認得")
        return "truststore"
    except Exception as e:  # noqa: BLE001
        # truststore 不在 → 預設 context 仍會認 SSL_CERT_FILE / 系統 OpenSSL 路徑。
        log.info("truststore 不可用（%s）；改用預設信任 + SSL_CERT_FILE", e)
        return "default"
