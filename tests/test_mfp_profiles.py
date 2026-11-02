"""事務機取件：型號辨識。

**目前沒有任何型號可以得到「已支援」** —— 規格與使用者規定實機驗證通過之前不可以標支援；
首批四款要「系列＋型號」都對上（ApeosPort C5570 ≠ Apeos C5570）。
頁面內容是**自己編的**（我們沒有實機頁面）；這裡測的是判斷規則，不是宣稱看得懂真機。
"""
from __future__ import annotations

import pytest

from app.core import mfp_profiles as mp


def _page(title="", text="", headers=(), pw=0):
    return {"scheme": "https", "port": 443, "path": "/", "title": title,
            "text_full_for_detection": text, "headers": list(headers), "password_inputs": pw}


@pytest.mark.parametrize("title,family,model", [
    ("FUJIFILM Apeos C5570", "Apeos", "C5570"),
    ("ApeosPort C4570", "ApeosPort", "C4570"),
    ("ApeosPort-V C6680 CentreWare Internet Services", "ApeosPort-V", "C6680"),
])
def test_first_batch_is_recognised_but_never_supported(title, family, model):
    r = mp.detect([_page(title=title)])
    assert (r["family"], r["model"]) == (family, model)
    assert r["status"] == "first_batch_unverified", "未經實機驗證不可以標已支援"
    assert r["vendor"] == "FUJIFILM" and r["evidence"]
    # 依據用代碼（畫面再翻譯），不夾中文標籤
    assert {e["what"] for e in r["evidence"]} <= {"vendor", "model", "ui", "firmware"}
    assert all(e["field"] in ("title", "text", "header") for e in r["evidence"])


def test_series_matters_not_just_the_number():
    r = mp.detect([_page(title="ApeosPort C5570")])
    assert (r["family"], r["model"]) == ("ApeosPort", "C5570")
    assert r["status"] == "same_vendor_unverified", "ApeosPort C5570 不是首批的 Apeos C5570"


def test_generation_suffix_is_kept():
    r = mp.detect([_page(title="ApeosPort-VI C3371")])
    assert r["family"] == "ApeosPort-VI" and r["status"] == "same_vendor_unverified"


def test_only_models_verified_on_a_real_device_are_supported():
    """加進 VERIFIED_PROFILES 之前要有實機取件測試報告（docs-share/mfp-fetch/field-reports/）。
    2026-10-19：Apeos C5571 客戶實機取件成功，使用者同意標已支援。"""
    assert mp.VERIFIED_PROFILES == frozenset({("Apeos", "C5571")})
    r = mp.detect([_page(title="Apeos C5571 - Internet Services")])
    assert r["status"] == "supported" and "實機驗證" in r["message"]


@pytest.mark.parametrize("model", ["C7071", "C6571", "C4571", "C3571", "C3071", "C2571"])
def test_the_same_series_is_marked_with_the_verified_model(model):
    """使用者 2026-10-19「同類 OS 的一併標示」：同系列標已支援，但寫明是以 Apeos C5571 驗證的。"""
    r = mp.detect([_page(title=f"Apeos {model}")])
    assert r["status"] == "supported_same_series" and "C5571" in r["message"]
    assert mp.STATUS_LABELS["supported_same_series"][0] == "已支援"


@pytest.mark.parametrize("title", ["Apeos C5570", "ApeosPort C5571", "ApeosPort-VII C5571"])
def test_other_generations_are_not_the_same_series(title):
    assert mp.detect([_page(title=title)])["status"] != "supported_same_series"


def test_fuji_xerox_is_not_mistaken_for_xerox():
    r = mp.detect([_page(text="Fuji Xerox DocuCentre-VI C3371")])
    assert r["vendor"] == "FUJIFILM" and r["status"] == "same_vendor_unverified"


@pytest.mark.parametrize("text,vendor", [
    ("Canon imageRUNNER ADVANCE Remote UI", "Canon"),
    ("RICOH Web Image Monitor", "Ricoh"),
    ("KONICA MINOLTA bizhub", "Konica Minolta"),
    ("Xerox VersaLink C405", "Xerox"),
])
def test_other_vendors_are_marked_with_a_reason(text, vendor):
    """Canon / Konica Minolta / SHARP 有檔案分享取件（v1.16.90，實驗功能）；其他廠牌是「不支援取件」。"""
    r = mp.detect([_page(text=text)])
    want = "experimental_vendor" if vendor in mp.EXPERIMENTAL_VENDORS else "unsupported_vendor"
    assert r["vendor"] == vendor and r["status"] == want
    assert "取件" in r["message"]


def test_experimental_vendors_match_the_share_routes():
    from app.core import mfp_share
    assert mp.EXPERIMENTAL_VENDORS == set(mfp_share.VENDOR_ROUTES)


def test_a_login_page_says_login_is_needed():
    r = mp.detect([_page(title="Login", pw=1)])
    assert r["status"] == "login_required"


def test_nothing_recognisable_is_unknown_not_unsupported():
    r = mp.detect([_page(title="Welcome", text="Hello")])
    assert r["status"] == "unknown"


def test_no_pages_means_unreachable():
    assert mp.detect([])["status"] == "unreachable"


def test_firmware_is_picked_up_when_labelled():
    r = mp.detect([_page(title="Apeos C5571", text="Firmware Version: 72.18.5")])
    assert r["firmware"] == "72.18.5"


def test_every_status_has_a_message():
    for k, v in mp.STATUS_MESSAGES.items():
        assert v and isinstance(v, str), k


# --------------------------------------------------------------------- 其他廠牌的型號（只顯示）
# 使用者 2026-10-14：Epson L3150 標題就寫著「L3150 Series」卻顯示「未辨識」；「抓型號要多考量不同廠牌型號，
# 不能只用我這邊的兩台為準」。下面的標題是**照各家產品線的命名規則杜撰的樣本**（網頁介面常見的寫法），
# 每一家至少兩種，另外有「不可以當成型號」的反例。相容性判斷不變（都是不支援）。

@pytest.mark.parametrize("title,headers,vendor,model", [
    # Epson：EcoTank、WorkForce、Expression、商用彩噴
    ("L3150 Series", [("Server", "EPSON_Linux UPnP/1.0 Epson UPnP SDK/1.0")], "Epson", "L3150 Series"),
    ("WF-C5790 Series", [("Server", "EPSON_Linux")], "Epson", "WF-C5790 Series"),
    ("EPSON ET-4850 Series", [], "Epson", "ET-4850 Series"),
    ("EPSON AM-C4000", [], "Epson", "AM-C4000"),
    # HP：LaserJet Pro / Color / Enterprise、OfficeJet、Smart Tank
    ("HP LaserJet MFP M141w 192.168.77.5", [], "HP", "HP LaserJet MFP M141w"),
    ("HP Color LaserJet Pro MFP M479fdw", [], "HP", "HP Color LaserJet Pro MFP M479fdw"),
    ("HP LaserJet MFP E52645", [], "HP", "HP LaserJet MFP E52645"),
    ("HP OfficeJet Pro 9010 series", [], "HP", "HP OfficeJet Pro 9010 series"),
    ("HP Smart Tank 7300 series", [], "HP", "HP Smart Tank 7300 series"),
    # Canon：imageRUNNER ADVANCE、iR-ADV、imageCLASS、MF 系列
    ("Remote UI: Portal: iR-ADV C3530 III", [], "Canon", "iR-ADV C3530 III"),
    ("Canon imageRUNNER ADVANCE DX C3826", [], "Canon", "imageRUNNER ADVANCE DX C3826"),
    ("Canon MF743C/MF745C Remote UI", [], "Canon", "MF743C"),
    # Ricoh：IM、MP、SP、M
    ("Web Image Monitor - RICOH IM C3000", [], "Ricoh", "RICOH IM C3000"),
    ("RICOH MP C3004ex", [], "Ricoh", "RICOH MP C3004ex"),
    ("RICOH M C250FW", [], "Ricoh", "RICOH M C250FW"),
    # Konica Minolta、Kyocera、Sharp、Toshiba、Xerox
    ("bizhub C300i", [], "Konica Minolta", "bizhub C300i"),
    ("KONICA MINOLTA bizhub 4752", [], "Konica Minolta", "bizhub 4752"),
    # DEVELOP（ineo / ineo+）＝Konica Minolta 集團品牌：歸到 Konica Minolta（沿用公用信箱 SMB），型號帶上 DEVELOP
    ("DEVELOP ineo+ 450i", [], "Konica Minolta", "DEVELOP ineo+ 450i"),
    ("ineo 4020i", [], "Konica Minolta", "DEVELOP ineo 4020i"),
    ("DEVELOP ineo+ 3350 Web Connection", [], "Konica Minolta", "DEVELOP ineo+ 3350"),
    ("Command Center RX - TASKalfa 3253ci", [], "Kyocera", "TASKalfa 3253ci"),
    ("KYOCERA ECOSYS MA4000cifx", [], "Kyocera", "ECOSYS MA4000cifx"),
    ("SHARP MX-3071", [], "Sharp", "MX-3071"),
    ("SHARP BP-50C26", [], "Sharp", "BP-50C26"),
    ("TOSHIBA e-STUDIO2515AC TopAccess", [], "Toshiba", "e-STUDIO2515AC"),
    ("Xerox AltaLink C8130", [], "Xerox", "Xerox AltaLink C8130"),
    ("Xerox B225 Multifunction Printer", [], "Xerox", "Xerox B225"),
    # Brother、Lexmark、Samsung、OKI、Panasonic、Dell、Pantum
    ("Brother MFC-L2750DW series", [], "Brother", "MFC-L2750DW series"),
    ("Brother DCP-T720DW", [], "Brother", "DCP-T720DW"),
    ("Lexmark MX521de", [], "Lexmark", "Lexmark MX521de"),
    ("Lexmark CX725", [], "Lexmark", "Lexmark CX725"),
    ("SyncThru Web Service SL-M2070", [], "Samsung", "SL-M2070"),
    ("OKI MC573", [], "OKI", "OKI MC573"),
    ("Panasonic DP-MB545", [], "Panasonic", "DP-MB545"),
    ("Dell C2665dnf Color Laser", [], "Dell", "Dell C2665dnf"),
    ("Pantum M6550NW", [], "Pantum", "Pantum M6550NW"),
])
def test_other_vendors_models_are_read_from_their_naming(title, headers, vendor, model):
    r = mp.detect([_page(title=title, headers=headers)])
    assert (r["vendor"], r["model"]) == (vendor, model), r
    assert r["status"] == ("experimental_vendor" if vendor in mp.EXPERIMENTAL_VENDORS else "unsupported_vendor")
    assert r["family"] is None
    assert any(e["what"] == "model" for e in r["evidence"]), "型號要附上依據"


@pytest.mark.parametrize("title,text", [
    ("EPSON", "Firmware Version 2.10.3 · 192.168.77.5 · Copyright 2023 Seiko Epson Corporation"),
    ("HP Embedded Web Server", "Updated 2026-10-14 · IPv4 192.168.77.9"),
    ("Brother", "Main Firmware Version 1.25"),
    ("RICOH", "Version 2024 · 10.0.0.5"),
])
def test_ip_version_and_dates_are_not_models(title, text):
    r = mp.detect([_page(title=title, text=text)])
    assert r["vendor"] and r["model"] is None, r


def test_the_title_wins_over_page_text():
    r = mp.detect([_page(title="L3150 Series", text="Supports ET-2810 ink", headers=[("Server", "EPSON")])])
    assert r["model"] == "L3150 Series"


def test_escl_model_is_preferred_for_other_vendors():
    """eSCL 的 MakeAndModel 是標準欄位，頁面上的寫法各家不同 —— 有的話用它。"""
    r = mp.detect([_page(title="HP Embedded Web Server")], escl_model="HP LaserJet MFP M141w")
    assert r["vendor"] == "HP" and r["model"] == "HP LaserJet MFP M141w"
    assert any(e["field"] == "escl" for e in r["evidence"])


def test_escl_model_does_not_change_fujifilm_identification():
    r = mp.detect([_page(title="Apeos C5571 - Internet Services")], escl_model="FUJIFILM Apeos C5571")
    assert (r["family"], r["model"], r["status"]) == ("Apeos", "C5571", "supported")


def test_embedded_web_server_alone_is_not_hp():
    """「Embedded Web Server」HP、Xerox、Lexmark 都在用。"""
    assert mp.detect([_page(title="Lexmark Embedded Web Server MX521de")])["vendor"] == "Lexmark"
    assert mp.detect([_page(title="Xerox Embedded Web Server")])["vendor"] == "Xerox"


@pytest.mark.parametrize("escl", ["Apeos C5571", "FUJIFILM Apeos C5571", "ApeosPort-V C6680"])
def test_fujifilm_recognised_from_escl_when_the_home_page_says_nothing(escl):
    """客戶實機（2026-10-16）：Apeos C5571 的首頁上找不到廠牌或型號（登入頁 / 腳本畫出來的），
    eSCL 卻回報 `Apeos C5571` —— 原本 FUJIFILM 只看網頁，結果廠牌「未辨識」、相容性「無法辨識」，
    清單上也就沒有機內信箱取件。eSCL 回報的型號也要當成辨識依據。"""
    r = mp.detect([_page(title="", text="Loading…")], escl_model=escl)
    fam, model = escl.replace("FUJIFILM ", "").split(" ")
    assert (r["vendor"], r["family"], r["model"]) == ("FUJIFILM", fam, model), r
    want = ("supported" if (fam, model) in mp.VERIFIED_PROFILES
            else "supported_same_series" if (fam, model) in mp.SAME_SERIES
            else "first_batch_unverified" if (fam, model) in mp.FIRST_BATCH else "same_vendor_unverified")
    assert r["status"] == want
    assert any(e["field"] == "escl" for e in r["evidence"])


def test_other_vendors_also_recognised_from_escl_alone():
    r = mp.detect([_page(title="")], escl_model="KYOCERA TASKalfa 3554ci")
    assert r["vendor"] == "Kyocera" and r["status"] == "experimental_vendor" and r["model"] == "KYOCERA TASKalfa 3554ci"


def test_kyocera_is_recognised_from_the_command_center_server_header():
    """KYOCERA Command Center RX：首頁只有空殼（`var PrinterModel = ""`，之後才由 JavaScript 填）、
    轉到 `/startwlm/Start_Wlm.htm`，畫面上沒有任何廠牌字樣，只有 `Server: KM-MFP-http/V0.0.1`（客戶實機的回應格式）。"""
    r = mp.detect([_page(title="", text="", headers=[("Server", "KM-MFP-http/V0.0.1"), ("Content-Type", "text/html")])])
    assert r["vendor"] == "Kyocera" and r["status"] == "experimental_vendor", r
    assert any(e.get("field") == "header" for e in r["evidence"]), "依據要寫是從標頭認出來的"
    # 反例：別家的伺服器名稱不可以被當成 KYOCERA
    for other in ("KM-httpd/1.0", "Web-Server/3.0", "KMMFP"):
        assert mp.detect([_page(headers=[("Server", other)])])["vendor"] != "Kyocera", other


@pytest.mark.parametrize("title,text", [
    ("Welcome", "Please develop your documents before scanning"),   # 英文單字 develop
    ("Gemineo", ""),                                                 # 字裡剛好有 ineo
    ("Status", "Developer options"),
])
def test_the_word_develop_is_not_the_develop_brand(title, text):
    """DEVELOP 只認全大寫的品牌寫法與 ineo 系列；一般英文單字不可以讓設備被當成 Konica Minolta。"""
    assert mp.detect([_page(title=title, text=text)])["vendor"] != "Konica Minolta"


def test_develop_from_escl_gets_the_konica_share_route():
    from app.core import mfp_share
    r = mp.detect([_page(title="Web Connection")], escl_model="DEVELOP ineo+ 450i")
    assert r["vendor"] == "Konica Minolta" and r["status"] == "experimental_vendor"
    assert mfp_share.VENDOR_ROUTES.get(r["vendor"]), "DEVELOP 要沿用 Konica Minolta 的取件路線"
