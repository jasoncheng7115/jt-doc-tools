"""事務機掃描（eSCL，也就是 AirPrint / Mopria 掃描）—— 從事務機掃描，直接存進工作區。

使用者 2026-10-14：小型事務機（例如 HP LaserJet MFP M141w）沒有存放掃描檔的信箱，只能由電腦端
發起掃描、檔案直接傳回來 —— 「加一種『從事務機掃描，直接進工作區』的方式」。FUJIFILM 也支援 eSCL。

流程（每一步都只連這一台設備，位址與連接埠已經過 `mfp_client` 的檢查與 DNS 釘選）：

1. `GET /eSCL/ScannerCapabilities` —— 連線測試時就讀，記下這台有哪些來源（平台玻璃 / 自動送紙器 /
   雙面）、色彩、解析度、格式。**掃描設定只接受能力表裡有的值**。
2. `GET /eSCL/ScannerStatus` —— 不是閒置就等一下（可能有人在機器上操作）；送紙器沒紙直接說。
3. `POST /eSCL/ScanJobs` —— 送出由固定欄位組成的 XML（沒有任何使用者字串進到 XML 裡）。
4. `GET <工作>/NextDocument` —— 逐頁取回：503 是還在掃、等一下再取；404 是沒有下一頁。
   每頁檢查檔頭（PDF / JPEG）與大小上限；取消或失敗時 `DELETE <工作>`，不留在設備上。
5. 存成使用者選的格式（使用者 2026-10-14：只能出 JPEG 的設備「可以 pdf 或 jpeg」）：
   * PDF（預設）：設備能出 PDF 就要 PDF（原樣保留）；只能出 JPEG 就把每頁合成一份 PDF。
   * JPEG：設備要能出 JPEG；每頁一個檔，不合成。

**不送任何帳密**。設備要求登入（401 / 403）就停下來講原因 —— 目前不支援。
`Location` 標頭只取路徑，而且路徑必須是 `/eSCL/ScanJobs/<id>`；連線一律沿用原本那台的位址與連接埠
（設備回的網址換成別台主機也不跟過去）。
不送 `Connection: close`：HP 小型機的網頁伺服器看到它會把回應切在 8 KB（見 `mfp_client._http_get`）。
"""
from __future__ import annotations

import http.client
import logging
import re
import ssl
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from . import mfp_client as mc
from . import mfp_output

logger = logging.getLogger("app.mfp_fetch")

USER_AGENT = "jt-doc-tools (mfp-fetch scan)"
CAPS_PATH = "/eSCL/ScannerCapabilities"
STATUS_PATH = "/eSCL/ScannerStatus"
JOBS_PATH = "/eSCL/ScanJobs"
#: 能力表、狀態這類小回應最多讀多少。
MAX_XML = 256 * 1024
#: 單一份文件（一頁或設備一次回的整份）上限。600 dpi 彩色 A4 的 PDF 約 10～30 MB。
MAX_DOC_BYTES = 200 * 1024 * 1024
#: 整次掃描的總量上限。
MAX_TOTAL_BYTES = 400 * 1024 * 1024
MAX_PAGES = 200
#: 等設備變閒置最多幾秒。
STATUS_WAIT = 30.0
#: 等下一頁（設備回 503「還在掃」）最多幾秒。
PAGE_WAIT = 180.0
#: 讀取逾時（設備掃描時可能很久才送出第一個位元組）。
READ_TIMEOUT = 90.0
#: 設備不是閒置時、下一頁還沒好時，隔多久再問一次（秒）。
STATUS_POLL = 2.0
PAGE_POLL = 1.5

NS_SCAN = "http://schemas.hp.com/imaging/escl/2011/05/03"
NS_PWG = "http://www.pwg.org/schemas/2010/12/sm"

MESSAGES: dict[str, str] = {
    "ESCL_OK": "支援從本站掃描（eSCL）",
    "ESCL_NONE": "這台沒有 eSCL 掃描介面，不能從本站掃描",
    "ESCL_BAD": "eSCL 掃描介面回應的格式不正確",
    "SCAN_NOT_SUPPORTED": "這台事務機不支援從本站掃描（連線測試沒有找到 eSCL 掃描介面）",
    "SCAN_OPTION_INVALID": "掃描設定不在這台事務機支援的範圍內",
    "SCAN_DEVICE_BUSY": "事務機正在處理其他工作（可能有人正在機器上操作），請稍後再試",
    "SCAN_ADF_EMPTY": "自動送紙器裡沒有紙，請放好文件再試",
    "SCAN_AUTH_REQUIRED": "這台事務機要求登入才能掃描，目前還不支援",
    "SCAN_REJECTED": "事務機拒絕了這組掃描設定",
    "SCAN_NO_DOCUMENT": "事務機沒有送回任何頁面（平台玻璃上有放文件嗎？）",
    "SCAN_BAD_DOCUMENT": "事務機送回的不是 PDF 或 JPEG",
    "SCAN_TOO_LARGE": "掃描檔超過大小上限，請降低解析度或改用灰階",
    "SCAN_TOO_MANY_PAGES": "頁數超過一次掃描的上限",
    "SCAN_TIMEOUT": "等事務機掃描逾時",
    "SCAN_CONNECTION_FAILED": "連不上事務機",
    "SCAN_PROTOCOL_ERROR": "事務機的回應不符合 eSCL 的格式",
    "SCAN_CANCELLED": "已取消",
    "SCAN_MERGE_FAILED": "掃描檔合成 PDF 時失敗",
    "HOLD_TOO_MANY": "暫存的掃描太多了（最多 40 次），請先存成 PDF 或清除",
}

#: 介面上的選項 → eSCL 的值。只收這幾個鍵。
SOURCES = {"platen": ("Platen", False), "adf": ("Feeder", False), "adf_duplex": ("Feeder", True)}
COLORS = {"color": "RGB24", "gray": "Grayscale8", "bw": "BlackAndWhite1"}
#: 紙張（單位：1/300 英寸）。`max` ＝ 這個來源的最大範圍。
PAPERS = {"a4": (2480, 3508), "letter": (2550, 3300), "max": None}
FORMATS = {"pdf": "application/pdf", "jpeg": "image/jpeg"}
#: 存檔格式（使用者選）：PDF 一份、或 JPEG 每頁一個檔。
OUTPUTS = ("pdf", "jpeg")
DEFAULT_RESOLUTION = 300


class ScanError(Exception):
    def __init__(self, code: str, detail: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.message = MESSAGES.get(code, code)
        self.detail = detail or {}


# --------------------------------------------------------------------------- 能力表

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, name: str):
    for c in list(el):
        if _local(c.tag) == name:
            return c
    return None


def _children(el, name: str) -> list:
    return [c for c in list(el) if _local(c.tag) == name]


def _iter_named(el, name: str):
    for c in el.iter():
        if _local(c.tag) == name:
            yield c


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _int(el) -> Optional[int]:
    try:
        return int(_text(el))
    except ValueError:
        return None


def _parse_input_caps(caps) -> dict:
    out: dict[str, Any] = {
        "max_width": _int(_child(caps, "MaxWidth")), "max_height": _int(_child(caps, "MaxHeight")),
        "color_modes": [], "formats": [], "resolutions": [], "intents": [],
    }
    for el in _iter_named(caps, "ColorMode"):
        v = _text(el)
        if v and v not in out["color_modes"]:
            out["color_modes"].append(v)
    for name in ("DocumentFormatExt", "DocumentFormat"):
        for el in _iter_named(caps, name):
            v = _text(el).lower()
            if v and v not in out["formats"]:
                out["formats"].append(v)
    for el in _iter_named(caps, "DiscreteResolution"):
        x, y = _int(_child(el, "XResolution")), _int(_child(el, "YResolution"))
        if x and y and x == y and x not in out["resolutions"]:
            out["resolutions"].append(x)
    for el in _iter_named(caps, "ResolutionRange"):
        xr = _child(el, "XResolutionRange")
        if xr is not None:
            lo, hi = _int(_child(xr, "Min")), _int(_child(xr, "Max"))
            if lo and hi:
                for r in (75, 100, 150, 200, 300, 400, 600, 1200):
                    if lo <= r <= hi and r not in out["resolutions"]:
                        out["resolutions"].append(r)
    out["resolutions"].sort()
    for el in _iter_named(caps, "Intent"):
        v = _text(el)
        if v and v not in out["intents"]:
            out["intents"].append(v)
    return out


def parse_capabilities(data: bytes) -> dict:
    """eSCL 的能力表 → 簡化過的字典。格式不對丟 `ScanError("ESCL_BAD")`。

    用 defusedxml 剖析：來源是區網設備，回應可能被竄改 —— 外部實體、大量展開的實體一律拒絕。"""
    from defusedxml.ElementTree import fromstring
    from defusedxml.common import DefusedXmlException
    if not data or len(data) > MAX_XML:
        raise ScanError("ESCL_BAD", {"bytes": len(data or b"")})
    try:
        root = fromstring(data)
    except (DefusedXmlException, SyntaxError, ValueError) as e:
        raise ScanError("ESCL_BAD", {"error": e.__class__.__name__}) from None
    if _local(root.tag) != "ScannerCapabilities":
        raise ScanError("ESCL_BAD", {"root": _local(root.tag)[:40]})
    out: dict[str, Any] = {
        "version": _text(_child(root, "Version"))[:10],
        "make_and_model": _text(_child(root, "MakeAndModel"))[:120],
        "manufacturer": _text(_child(root, "Manufacturer"))[:60],
        "sources": {},
    }
    platen = _child(root, "Platen")
    if platen is not None:
        caps = _child(platen, "PlatenInputCaps")
        if caps is not None:
            out["sources"]["platen"] = _parse_input_caps(caps)
    adf = _child(root, "Adf")
    if adf is not None:
        simplex = _child(adf, "AdfSimplexInputCaps")
        if simplex is not None:
            out["sources"]["adf"] = _parse_input_caps(simplex)
        duplex = _child(adf, "AdfDuplexInputCaps")
        if duplex is not None:
            out["sources"]["adf_duplex"] = _parse_input_caps(duplex)
    if not out["sources"]:
        raise ScanError("ESCL_BAD", {"reason": "no input source"})
    return out


def summarize(caps: dict) -> dict:
    """畫面與清單用：每個來源能選什麼（只列本站支援的選項）。

    `formats` 是設備能出的格式；`outputs` 是畫面上能選的存檔格式（PDF 一定可以：設備只能出 JPEG 時
    由本站合成；JPEG 要設備能出 JPEG）。"""
    srcs = {}
    for key, c in (caps.get("sources") or {}).items():
        colors = [k for k, v in COLORS.items() if v in c.get("color_modes", [])]
        fmts = [k for k, v in FORMATS.items() if v in c.get("formats", [])]
        res = [r for r in c.get("resolutions", []) if 75 <= r <= 1200]
        if colors and fmts and res:
            srcs[key] = {"colors": colors, "formats": fmts, "resolutions": res,
                         "outputs": ["pdf"] + (["jpeg"] if "jpeg" in fmts else []),
                         "max_width": c.get("max_width"), "max_height": c.get("max_height")}
    return {"model": caps.get("make_and_model") or "", "sources": srcs}


# --------------------------------------------------------------------------- 掃描設定

def build_settings(caps: dict, opts: dict) -> tuple[bytes, dict]:
    """使用者選的設定 → eSCL 的 ScanSettings XML。每個值都要在這台的能力表裡，否則 `SCAN_OPTION_INVALID`。

    XML 只由固定字串與整數組成 —— 使用者輸入不會原樣進到 XML。"""
    src_key = str(opts.get("source") or "platen")
    if src_key not in SOURCES or src_key not in (caps.get("sources") or {}):
        raise ScanError("SCAN_OPTION_INVALID", {"field": "source"})
    c = caps["sources"][src_key]
    color_key = str(opts.get("color") or "color")
    if color_key not in COLORS or COLORS[color_key] not in c.get("color_modes", []):
        raise ScanError("SCAN_OPTION_INVALID", {"field": "color"})
    try:
        res = int(opts.get("resolution") or DEFAULT_RESOLUTION)
    except (TypeError, ValueError):
        raise ScanError("SCAN_OPTION_INVALID", {"field": "resolution"}) from None
    if res not in c.get("resolutions", []):
        raise ScanError("SCAN_OPTION_INVALID", {"field": "resolution"})
    paper = str(opts.get("paper") or "a4")
    if paper not in PAPERS:
        raise ScanError("SCAN_OPTION_INVALID", {"field": "paper"})
    output = str(opts.get("format") or "pdf")
    if output not in OUTPUTS:
        raise ScanError("SCAN_OPTION_INVALID", {"field": "format"})
    if output == "pdf":
        # 存 PDF：設備能出 PDF 就要 PDF；只能出 JPEG 就要 JPEG、之後合成
        fmt_key = "pdf" if FORMATS["pdf"] in c.get("formats", []) else "jpeg"
    else:
        fmt_key = "jpeg"
    if FORMATS[fmt_key] not in c.get("formats", []):
        raise ScanError("SCAN_OPTION_INVALID", {"field": "format"})
    mw, mh = c.get("max_width") or 2550, c.get("max_height") or 3508
    w, h = PAPERS[paper] or (mw, mh)
    w, h = min(int(w), int(mw)), min(int(h), int(mh))
    intents = c.get("intents") or []
    intent = "Document" if (not intents or "Document" in intents) else intents[0]
    if not re.fullmatch(r"[A-Za-z]{1,32}", intent):
        intent = "Document"
    version = caps.get("version") or ""
    if not re.fullmatch(r"\d\.\d{1,2}", version):
        version = "2.0"
    source, duplex = SOURCES[src_key]
    mime = FORMATS[fmt_key]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<scan:ScanSettings xmlns:scan="{NS_SCAN}" xmlns:pwg="{NS_PWG}">'
        f'<pwg:Version>{version}</pwg:Version>'
        f'<scan:Intent>{intent}</scan:Intent>'
        '<pwg:ScanRegions><pwg:ScanRegion>'
        '<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>'
        f'<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>'
        f'<pwg:Width>{w}</pwg:Width><pwg:Height>{h}</pwg:Height>'
        '</pwg:ScanRegion></pwg:ScanRegions>'
        f'<pwg:InputSource>{source}</pwg:InputSource>'
        + ('<scan:Duplex>true</scan:Duplex>' if duplex else '')
        + f'<scan:ColorMode>{COLORS[color_key]}</scan:ColorMode>'
        f'<scan:XResolution>{res}</scan:XResolution><scan:YResolution>{res}</scan:YResolution>'
        f'<pwg:DocumentFormat>{mime}</pwg:DocumentFormat>'
        f'<scan:DocumentFormatExt>{mime}</scan:DocumentFormatExt>'
        '</scan:ScanSettings>')
    eff = {"source": src_key, "color": color_key, "resolution": res, "paper": paper,
           "format": fmt_key, "output": output, "width": w, "height": h, "intent": intent}
    return xml.encode("utf-8"), eff


# --------------------------------------------------------------------------- 連線

class _Device:
    """一台設備的連線設定（位址已檢查、已解析）。每個請求開一條新連線，讀完自己關。"""

    def __init__(self, host: str, ip: str, scheme: str, port: int):
        self.host, self.ip, self.scheme, self.port = host, ip, scheme, int(port)
        self.host_hdr = mc._host_header(host, scheme, self.port)
        self.sni = None if mc._is_ip(host) else host
        self.tls_verified: Optional[bool] = None

    def _open(self, timeout: float):
        try:
            sock = mc._connect(self.ip, self.port, min(mc.CONNECT_TIMEOUT, timeout))
        except OSError as e:
            raise ScanError("SCAN_CONNECTION_FAILED", {"error": e.__class__.__name__}) from None
        if self.scheme != "https":
            return sock
        raw = sock
        if self.tls_verified is not False:
            try:
                s = mc._verified_context().wrap_socket(raw, server_hostname=self.sni or self.host)
                self.tls_verified = True
                return s
            except ssl.SSLCertVerificationError:
                raw.close()
                self.tls_verified = False      # 事務機幾乎都是自簽憑證；不送帳密，只取掃描檔
                try:
                    raw = mc._connect(self.ip, self.port, min(mc.CONNECT_TIMEOUT, timeout))
                except OSError as e:
                    raise ScanError("SCAN_CONNECTION_FAILED", {"error": e.__class__.__name__}) from None
            except (ssl.SSLError, OSError) as e:
                raw.close()
                raise ScanError("SCAN_CONNECTION_FAILED", {"error": e.__class__.__name__}) from None
        try:
            return mc._unverified_context().wrap_socket(raw, server_hostname=self.sni)
        except (ssl.SSLError, OSError) as e:
            raw.close()
            raise ScanError("SCAN_CONNECTION_FAILED", {"error": e.__class__.__name__}) from None

    def request(self, method: str, path: str, *, body: Optional[bytes] = None,
                content_type: str = "", to_file: Optional[Path] = None,
                max_bytes: int = MAX_XML, timeout: float = READ_TIMEOUT) -> dict:
        """送一個請求。`to_file` 有給就把內容串流寫進檔案（超過 `max_bytes` 丟 `SCAN_TOO_LARGE`）。"""
        sock = self._open(timeout)
        try:
            sock.settimeout(timeout)
            head = (f"{method} {path} HTTP/1.1\r\nHost: {self.host_hdr}\r\nUser-Agent: {USER_AGENT}\r\n"
                    "Accept: */*\r\n")
            if body is not None:
                head += f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
            sock.sendall((head + "\r\n").encode("ascii") + (body or b""))
            resp = http.client.HTTPResponse(sock, method=method)
            resp.begin()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            out: dict[str, Any] = {"status": resp.status, "headers": headers}
            if method == "HEAD" or resp.status in (204, 304):
                return out
            if to_file is not None and resp.status == 200:
                n = 0
                with open(to_file, "wb") as f:
                    while True:
                        try:
                            chunk = resp.read(64 * 1024)
                        except http.client.IncompleteRead:
                            raise ScanError("SCAN_PROTOCOL_ERROR", {"reason": "incomplete"}) from None
                        if not chunk:
                            break
                        n += len(chunk)
                        if n > max_bytes:
                            raise ScanError("SCAN_TOO_LARGE", {"limit_mb": max_bytes // 1024 // 1024})
                        f.write(chunk)
                out["bytes"] = n
                return out
            # 不是要存檔的回應（狀態、錯誤頁、503）只讀一小段：設備附上很大的內容也不會吃掉記憶體
            cap = MAX_XML if to_file is not None else max_bytes
            try:
                data = resp.read(cap + 1)
            except http.client.IncompleteRead as e:
                data = bytes(e.partial or b"")
            out["body"] = data[:cap]
            return out
        except ScanError:
            raise
        except (OSError, http.client.HTTPException) as e:
            code = "SCAN_TIMEOUT" if isinstance(e, TimeoutError) else "SCAN_CONNECTION_FAILED"
            raise ScanError(code, {"error": e.__class__.__name__, "path": path}) from None
        finally:
            sock.close()


def _check_target(ip: str, scheme: str, port: int) -> None:
    """多一層：連線前再驗一次位址與連接埠（呼叫端已經驗過；掃描會對設備送 POST，不只是讀）。"""
    pairs = mc.parse_port_scheme(port, scheme)
    if pairs != [(scheme, int(port))]:
        raise mc.PolicyError("PORT_NOT_ALLOWED", {"input": port})
    code = mc.check_address(ip)
    if code:
        raise mc.PolicyError(code, {"ip": ip})


def fetch_capabilities(host: str, ip: str, scheme: str, port: int, timeout: float = 10.0) -> dict:
    """讀能力表（連線測試時呼叫）。沒有 eSCL → `ScanError("ESCL_NONE")`。"""
    _check_target(ip, scheme, port)
    dev = _Device(host, ip, scheme, port)
    r = dev.request("GET", CAPS_PATH, timeout=timeout)
    ctype = r["headers"].get("content-type", "").lower()
    if r["status"] != 200 or ("xml" not in ctype and not (r.get("body") or b"").lstrip().startswith(b"<?xml")):
        raise ScanError("ESCL_NONE", {"status": r["status"]})
    caps = parse_capabilities(r.get("body") or b"")
    caps["tls_verified"] = dev.tls_verified
    return caps


# --------------------------------------------------------------------------- 掃描

_JOB_PATH_RE = re.compile(r"^/eSCL/ScanJobs/[A-Za-z0-9._~-]{1,128}$")


def _job_path(location: str, dev: _Device) -> str:
    """`Location` 只取路徑；主機名稱若有寫，必須是同一台（名稱或 IP）。"""
    if not location:
        raise ScanError("SCAN_PROTOCOL_ERROR", {"reason": "no location"})
    u = urlsplit(location.strip())
    host = (u.hostname or "").lower().strip("[]")
    if host and host not in (dev.host.lower(), dev.ip):
        raise ScanError("SCAN_PROTOCOL_ERROR", {"reason": "location host"})
    path = u.path.rstrip("/")
    if not _JOB_PATH_RE.match(path):
        raise ScanError("SCAN_PROTOCOL_ERROR", {"reason": "location path"})
    return path


def _scanner_state(dev: _Device) -> tuple[str, str]:
    r = dev.request("GET", STATUS_PATH, timeout=15.0)
    if r["status"] in (401, 403):
        raise ScanError("SCAN_AUTH_REQUIRED", {"status": r["status"]})
    if r["status"] != 200:
        raise ScanError("SCAN_PROTOCOL_ERROR", {"status": r["status"], "path": STATUS_PATH})
    from defusedxml.ElementTree import fromstring
    from defusedxml.common import DefusedXmlException
    try:
        root = fromstring(r.get("body") or b"")
    except (DefusedXmlException, SyntaxError, ValueError):
        raise ScanError("SCAN_PROTOCOL_ERROR", {"reason": "status xml"}) from None
    state = _text(_child(root, "State"))
    adf = _text(_child(root, "AdfState"))
    return state, adf


def _wait_idle(dev: _Device, source: str, is_cancelled: Callable[[], bool]) -> None:
    deadline = time.monotonic() + STATUS_WAIT
    while True:
        state, adf = _scanner_state(dev)
        if source != "platen" and adf and "Empty" in adf:
            raise ScanError("SCAN_ADF_EMPTY", {"adf_state": adf[:40]})
        if not state or state == "Idle":
            return
        if is_cancelled():
            raise ScanError("SCAN_CANCELLED")
        if time.monotonic() > deadline:
            raise ScanError("SCAN_DEVICE_BUSY", {"state": state[:40]})
        time.sleep(STATUS_POLL)


def _sniff(path: Path) -> Optional[str]:
    return mfp_output.sniff(path)


def scan(host: str, ip: str, scheme: str, port: int, caps: dict, opts: dict, workdir: Path,
         on_progress: Optional[Callable[[dict], None]] = None,
         is_cancelled: Optional[Callable[[], bool]] = None) -> dict:
    """掃描一次，回傳 `{"files": [{"path", "kind", "pages"}], "pages", "bytes", "settings", "tls_verified"}`
    （檔案在 workdir 裡：存 PDF 時是合成好的一份 —— 另外放在 `path`，存 JPEG 時每頁一個）。

    失敗丟 `ScanError`；設備上建立的掃描工作會在失敗或取消時刪掉。"""
    is_cancelled = is_cancelled or (lambda: False)
    notify = on_progress or (lambda _d: None)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    _check_target(ip, scheme, port)
    xml, eff = build_settings(caps, opts)
    dev = _Device(host, ip, scheme, port)
    notify({"phase": "checking"})
    _wait_idle(dev, eff["source"], is_cancelled)
    if is_cancelled():
        raise ScanError("SCAN_CANCELLED")
    r = dev.request("POST", JOBS_PATH, body=xml, content_type="text/xml", timeout=30.0)
    st = r["status"]
    if st in (401, 403):
        raise ScanError("SCAN_AUTH_REQUIRED", {"status": st})
    if st in (409, 503):
        raise ScanError("SCAN_DEVICE_BUSY", {"status": st})
    if st not in (200, 201):
        raise ScanError("SCAN_REJECTED", {"status": st})
    job = _job_path(r["headers"].get("location", ""), dev)
    files: list[tuple[Path, str]] = []
    total = 0
    try:
        page_deadline = time.monotonic() + PAGE_WAIT
        while True:
            if is_cancelled():
                raise ScanError("SCAN_CANCELLED")
            n = len(files) + 1
            if n > MAX_PAGES:
                raise ScanError("SCAN_TOO_MANY_PAGES", {"limit": MAX_PAGES})
            notify({"phase": "scanning", "pages": len(files), "bytes": total})
            part = workdir / f"part{n:04d}.bin"
            r = dev.request("GET", job + "/NextDocument", to_file=part,
                            max_bytes=min(MAX_DOC_BYTES, MAX_TOTAL_BYTES - total))
            st = r["status"]
            if st == 200:
                kind = _sniff(part)
                if kind not in ("pdf", "jpeg"):
                    part.unlink(missing_ok=True)
                    raise ScanError("SCAN_BAD_DOCUMENT", {"content_type": r["headers"].get("content-type", "")[:60]})
                total += r.get("bytes", 0)
                files.append((part, kind))
                page_deadline = time.monotonic() + PAGE_WAIT
                if eff["source"] == "platen":
                    # 平台玻璃只有一頁；再問一次讓設備把工作收尾（應該回 404），結果不影響這次掃描
                    try:
                        dev.request("GET", job + "/NextDocument", timeout=15.0, max_bytes=1024)
                    except ScanError:
                        pass
                    break
                continue
            part.unlink(missing_ok=True)
            if st == 404:
                break
            if st in (503, 409):
                if time.monotonic() > page_deadline:
                    raise ScanError("SCAN_TIMEOUT", {"pages": len(files)})
                time.sleep(PAGE_POLL)
                continue
            if st in (401, 403):
                raise ScanError("SCAN_AUTH_REQUIRED", {"status": st})
            raise ScanError("SCAN_PROTOCOL_ERROR", {"status": st, "path": "NextDocument"})
        if not files:
            raise ScanError("SCAN_NO_DOCUMENT")
    except ScanError:
        try:
            dev.request("DELETE", job, timeout=10.0, max_bytes=1024)
        except ScanError:
            pass
        for p in workdir.glob("part*.bin"):      # 含寫到一半就超過上限、被中斷的那一份
            p.unlink(missing_ok=True)
        raise
    if eff.get("output") == "jpeg" and all(k == "jpeg" for _p, k in files):
        outs = []
        for i, (p, _k) in enumerate(files, 1):
            try:
                mfp_output.inspect(p, "jpeg")
            except mfp_output.OutputError:
                for q, _q in files:
                    q.unlink(missing_ok=True)
                raise ScanError("SCAN_BAD_DOCUMENT", {"reason": "jpeg"}) from None
            final = workdir / f"page{i:04d}.jpg"
            p.rename(final)
            outs.append({"path": final, "kind": "jpeg", "pages": 1})
        return {"files": outs, "pages": len(outs), "bytes": total, "settings": eff,
                "tls_verified": dev.tls_verified, "documents": len(files)}
    notify({"phase": "merging", "pages": len(files), "bytes": total})
    out = workdir / "scan.pdf"
    pages = merge_to_pdf(files, out, eff["resolution"])
    for p, _k in files:
        if p != out:
            p.unlink(missing_ok=True)
    return {"path": out, "files": [{"path": out, "kind": "pdf", "pages": pages}], "pages": pages,
            "bytes": out.stat().st_size, "settings": eff, "tls_verified": dev.tls_verified,
            "documents": len(files)}


def merge_to_pdf(files: list[tuple[Path, str]], out: Path, resolution: int) -> int:
    """設備回的檔案（PDF 或 JPEG）合成一份 PDF，回傳頁數。只有一份 PDF 時原樣保留（不重新編碼）。

    實作在 `mfp_output.to_pdf`（信箱取件也用）；這裡把錯誤換成掃描的錯誤代碼。"""
    try:
        return mfp_output.to_pdf(files, out, resolution or DEFAULT_RESOLUTION)
    except mfp_output.OutputError as e:
        if e.detail.get("reason") == "no page":
            raise ScanError("SCAN_NO_DOCUMENT") from None
        if e.code == "FILE_INVALID":
            raise ScanError("SCAN_BAD_DOCUMENT", {"reason": "empty pdf"}) from None
        raise ScanError("SCAN_MERGE_FAILED", e.detail) from None
