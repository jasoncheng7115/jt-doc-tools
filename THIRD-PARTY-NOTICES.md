# 第三方套件授權聲明

Jason Tools 文件工具箱 使用以下第三方開源套件。本程式遵守各套件的授權條款，將授權與聲明完整保留如下。

> 若您要重新散布本程式或衍生作品，請保留本檔案。

---

## Python 執行環境與工具

### uv  · MIT License OR Apache-2.0
- 專案：https://github.com/astral-sh/uv
- 用途：管理獨立 Python 與 Python 相依套件
- 授權全文：https://github.com/astral-sh/uv/blob/main/LICENSE-MIT、https://github.com/astral-sh/uv/blob/main/LICENSE-APACHE

### Python (CPython)  · Python Software Foundation License
- 專案：https://www.python.org/
- 用途：執行環境（透過 uv 安裝獨立版本）
- 授權全文：https://docs.python.org/3/license.html

---

## Python 套件

### FastAPI  · MIT License
- 專案：https://github.com/fastapi/fastapi
- 用途：Web 框架
- Copyright © 2018 Sebastián Ramírez

### Uvicorn  · BSD-3-Clause License
- 專案：https://github.com/encode/uvicorn
- 用途：ASGI 伺服器
- Copyright © 2017-present, [Encode OSS Ltd](https://www.encode.io/)

### Starlette  · BSD-3-Clause License
- 專案：https://github.com/encode/starlette
- 用途：FastAPI 底層 ASGI 工具集
- Copyright © 2018, [Encode OSS Ltd](https://www.encode.io/)

### Pydantic / pydantic-settings  · MIT License
- 專案：https://github.com/pydantic/pydantic、https://github.com/pydantic/pydantic-settings
- 用途：資料驗證 / 設定管理
- Copyright © 2017 to present Pydantic Services Inc.

### Jinja2  · BSD-3-Clause License
- 專案：https://github.com/pallets/jinja
- 用途：HTML 範本引擎
- Copyright © 2007 Pallets

### python-multipart  · Apache-2.0 License
- 專案：https://github.com/Kludex/python-multipart
- 用途：FastAPI 上傳檔案解析
- Copyright © Andrew Dunham

### PyMuPDF (fitz)  · GNU AGPL v3
- 專案：https://github.com/pymupdf/PyMuPDF
- 用途：PDF 讀取、編輯、imposition、redaction 等核心功能
- Copyright © Artifex Software, Inc.
- ⚠️ **使用 AGPL 授權**：若您將本程式提供給他人作為網路服務（含內網），需將完整原始碼依 AGPL 條款公開。本專案以 Apache-2.0 釋出，這條約束**只擴及 PyMuPDF 部分**；如有商業閉源需求，請洽 Artifex 取得商業授權，或改用其他 PDF 引擎。

### Pillow  · MIT-CMU License (HPND)
- 專案：https://github.com/python-pillow/Pillow
- 用途：影像處理（縮圖 / 去背 / 裁剪）
- Copyright © 1997-2011 by Secret Labs AB / Copyright © 1995-2011 by Fredrik Lundh and contributors / Copyright © 2010 Alex Clark and contributors

### pdfplumber  · MIT License
- 專案：https://github.com/jsvine/pdfplumber
- 用途：PDF 表格與文字位置抽取
- Copyright © 2016 Jeremy Singer-Vine

### python-docx  · MIT License
- 專案：https://github.com/python-openxml/python-docx
- 用途：產生 / 讀取 Word .docx 檔
- Copyright © 2013 Steve Canny, https://github.com/scanny

### odfpy  · GNU LGPL v2.1+ / Apache-2.0
- 專案：https://github.com/eea/odfpy
- 用途：產生 ODF（OpenDocument）檔
- Copyright © Søren Roug

### pyzipper  · MIT License
- 專案：https://github.com/danifus/pyzipper
- 用途：AES 加密 ZIP（已下架功能保留程式碼）
- Copyright © Daniel Hillier

### httpx  · BSD-3-Clause License
- 專案：https://github.com/encode/httpx
- 用途：HTTP client（呼叫 Ollama 等 LLM）
- Copyright © 2019, [Encode OSS Ltd](https://www.encode.io/)

### pdf2docx  · MIT License
- 專案：https://github.com/ArtifexSoftware/pdf2docx
- 用途：pdf-to-office 主轉檔引擎（PDF → docx）
- Copyright © Artifex Software, Inc.
- **注意**：上游於 2026 釋出 0.5.13 後停止維護。本程式鎖死於 0.5.13；必要時 jasoncheng7115/jt-doc-tools 將自行 fork 維護（已規劃 jasoncheng7115/jt-pdf2docx 倉庫）。

### rapidfuzz  · MIT License
- 專案：https://github.com/rapidfuzz/RapidFuzz
- 用途：pdf-to-office 的 docx ↔ PDFTruth 模糊比對引擎（取代 difflib，速度快 10×+）
- Copyright © Max Bachmann

### pyzbar  · MIT License
- 專案：https://github.com/NaturalHistoryMuseum/pyzbar
- 用途：einvoice-scan QR Code 解碼 Python wrapper（Linux/macOS 需另裝 zbar shared lib）
- Copyright © 2017, NaturalHistoryMuseum
- 系統相依：zbar (LGPL-2.1-or-later)，由 OS 套件管理員安裝

---

## 系統服務工具

### WinSW (Windows Service Wrapper)  · MIT License
- 專案：https://github.com/winsw/winsw
- 用途：Windows 安裝時包成 Windows Service（v1.4.44 起取代 NSSM）
- 作者：Kohsuke Kawaguchi 等；MIT 授權
- 自 v1.4.44 起，bundled 在 `packaging/windows/winsw.exe`（WinSW v2.12.0 NET461，
  SHA-256 `b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f`），
  優先使用 bundled，網路下載 fallback 走 GitHub Releases。`install.ps1`
  在使用前會驗證 SHA-256。詳見 `packaging/windows/README.md`。

### NSSM (the Non-Sucking Service Manager)  · Public Domain — 已棄用 (v1.4.43 之前使用)
- 專案：https://nssm.cc/
- 舊安裝（v1.4.43-）的服務 wrapper；v1.4.44 起客戶 `jtdt update` 流程會
  自動移轉到 WinSW（service name / env vars 都保留）。`packaging/windows/nssm.exe`
  保留供舊安裝偵測用，新安裝不再使用。換掉的原因：NSSM 2014 後無更新、
  nssm.cc 不時 503/404、部分 AV 標 PUA（GitHub issues #1 / #3）。

---

## Office 引擎（執行時相依，未隨程式散布）

本程式**不打包** Office 引擎，僅在使用者本機呼叫。授權聲明僅供參考。

### OxOffice  · Mozilla Public License 2.0 (MPL-2.0)
- 專案：https://github.com/OSSII/OxOffice
- 用途：Office 文件轉 PDF（優先選用，台灣 OSSII 維護的 LibreOffice fork）
- 由安裝腳本自動下載官方 release，使用者本機獨立執行

### LibreOffice  · Mozilla Public License 2.0 (MPL-2.0)
- 專案：https://www.libreoffice.org/
- 用途：Office 引擎 fallback
- 由系統套件管理工具（apt / dnf / winget / brew）安裝，使用者本機獨立執行

---

## 字型

### Noto Sans TC / Noto Serif TC  · SIL Open Font License 1.1
- 專案：https://fonts.google.com/noto
- 用途：PDF 編輯器與其他工具的內建中文字型
- Copyright © Google LLC

---

## 前端套件（瀏覽器端）

### PDF.js  · Apache-2.0 License
- 專案：https://github.com/mozilla/pdf.js
- 用途：PDF 編輯器的背景頁面渲染、`pdf-ocr` 完成後內嵌結果預覽 viewer
- 版本：v5.7.284（legacy build），完整 vendored 在 `static/vendor/pdfjs/`
- Copyright © Mozilla Foundation
- 授權全文：`static/vendor/pdfjs/LICENSE`

### Fabric.js  · MIT License
- 專案：https://github.com/fabricjs/fabric.js
- 用途：PDF 編輯器的物件疊加層 (canvas)
- Copyright © 2008-2015 Printio (Juriy Zaytsev, Maxim Chernyak)

---

## 圖示

### Iconoir 風格內嵌 SVG
- 啟發來源：https://iconoir.com/ （MIT License）
- 本程式內 `app/web/templates/components/icons.html` 的 SVG 為自行手繪，靈感參考 Iconoir 風格

---

## 完整授權全文

各套件的完整授權全文請見其 GitHub repo 內的 `LICENSE` 檔。如有疑問或補充，請開 issue：
https://github.com/jasoncheng7115/jt-doc-tools/issues
