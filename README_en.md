[繁體中文](README.md) ｜ **English** ｜ [日本語](README_ja.md)

# Jason Tools Document Toolbox v1.16.77

> ### ⚠ Installed with git before 2026-09-13? Run one command before this upgrade
>
> The project's git history was rewritten on 2026-09-13 (to remove a piece of data that should never have been committed), so every tag now points at a
> new commit. **An older `jtdt update` fails at the `git fetch --tags` step with
> `would clobber existing tag`**, reporting only `git fetch failed` —
> nothing points at the tags as the cause. Run this once and it is fixed for good:
>
> ```bash
> # Linux / macOS
> sudo git -C /opt/jt-doc-tools fetch --tags --force origin
> # Windows (run PowerShell as Administrator)
> git -C "C:\Program Files\jt-doc-tools" fetch --tags --force origin
> ```
>
> **That `-C` is upper case.** Typing `git -c /opt/jt-doc-tools/ …` instead gives you
> 「`fatal: not a git repository (or any of the parent directories): .git`」
> — a lower-case `-c` is the **config** flag (`-c key=value`), so git never changes into that directory
> and looks for `.git` where you are standing instead. The message **reads as though the install were not a git checkout,
> when in fact one letter was mistyped** (reported by a customer on 2026-09-14).
>
> `jtdt update` then works as usual. **Unaffected**: tarball installs, the Windows installer,
> and anything installed after 2026-09-13. Fixed from v1.15.43 onwards.

> An integrated PDF / Office document platform. 51 tools cover **form filling and stamping**, **watermarks**, **merge / split / rotate / reorder**, **format conversion**, **scan stitching**, **de-identification**, **word counts**, **annotation handling**, **diffing**, **sentence translation**, **list processing**, **e-invoice processing**, **VAT lookup**, **a page editor**, **encryption / decryption** and more.
>
> Enterprise features: **local / LDAP / AD multi-realm authentication**, **single sign-on** (OIDC + SAML, ready for M365 / Google / Keycloak), **RBAC roles and permissions**, **audit log**, **SIEM forwarding** (syslog / CEF / GELF), **font management**, **user workspace**, **background jobs with completion notices** and a **REST API**.
>
> **No cloud; your data stays with you.** Runs on Linux, macOS and Windows, either standalone or as an internal server for a whole team.

Full introduction site: <https://jasoncheng7115.github.io/jt-doc-tools/index-en.html>

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![CodeQL](https://github.com/jasoncheng7115/jt-doc-tools/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/jasoncheng7115/jt-doc-tools/actions/workflows/codeql.yml)
[![OWASP Top 10 (2025)](https://img.shields.io/badge/OWASP%20Top%2010%20(2025)-A01--A10%20covered-success?logo=owasp)](SECURITY.md)
[![Tests](https://img.shields.io/badge/pytest-9225%20passed-brightgreen?logo=pytest)](tests/)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-success?logo=dependabot)](.github/dependabot.yml)
[![Python](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white)](pyproject.toml)
[![Platforms](https://img.shields.io/badge/platforms-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](INSTALL.md)

---

## One-line install

### System requirements

| Item | Minimum | Recommended |
|---|---|---|
| Operating system | Ubuntu 20.04+ / Debian 11+ / macOS 12+ / Windows 10 1809+ | any current release |
| Disk | **12 GB** for the machine / VM / container (minimum) | **20 GB+** (leaving room for the data directory) |
| Memory | 2 GB free | 4 GB+ |
| CPU | x86_64 / arm64 (Apple Silicon and Windows 11 ARM both work) | 4 cores+ |
| Network | Internet access to GitHub / PyPI during installation (offline afterwards) |; |
| Python | 3.10+ (the install script sets up a uv-managed Python for you) |; |

> **Why 12 GB and not the 5; 8 GB it looks like it needs**:
> - **Base OS**: a minimal Debian / Ubuntu is about 1.5; 2 GB; other distributions or a desktop are larger.
> - **Install-time peak of about 6; 8 GB**: cached.deb packages (~1 GB, OxOffice / LibreOffice dependencies) plus the uv wheel cache (~1; 2 GB; PyTorch alone is 700 MB) plus unpacking. The install script runs `apt-get clean` and `uv cache clean` to release it afterwards, but **during the peak** that space really is needed.
> - **About 3 GB once installed**: the Python environment (~1.5 GB including PyTorch / EasyOCR, the main OCR engine) plus tesseract trained data (~80 MB: chi_tra fast+best plus eng) plus OxOffice/LibreOffice (~1 GB). EasyOCR downloads another ~150 MB of models the first time OCR runs.
> - **The data directory grows**: uploaded files, audit records and history accumulate. If disk is tight, install elsewhere with `JTDT_DATA_DIR=/mnt/big-disk/jtdt curl ... | sudo -E bash`.
>
> **Sizing a container or VM**: 12 GB is the floor that will succeed (2 GB OS + 8 GB peak + 2 GB headroom); give it at least 20 GB for real use so it does not fill up three months later. **An 8 GB container will not fit** (a customer has already hit this).

### The command

**Linux / macOS**:
```bash
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.sh | sudo bash
```

**Windows 10 / 11; double-click installer (recommended, no PowerShell needed)**:

Download from [GitHub Releases](https://github.com/jasoncheng7115/jt-doc-tools/releases/latest)
`jt-doc-tools-x.y.z-setup.exe` and double-click it. The wizard is in Traditional Chinese and includes an uninstaller.

> **An older version number in the filename does not matter.** The installer is only a bootstrapper (about 6 MB);
> the code itself is **downloaded from GitHub at install time**, so whichever .exe you have,
> you end up on the latest version. The installer itself is only re-released when the install flow changes.

**Windows 10 / 11; one PowerShell command** (run as Administrator):
```powershell
$f="$env:TEMP\jtdt-install.ps1"; try { Invoke-WebRequest 'https://cdn.jsdelivr.net/gh/jasoncheng7115/jt-doc-tools@main/install.ps1' -OutFile $f -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop; powershell -NoProfile -ExecutionPolicy Bypass -File $f } catch { Write-Host "[X] 下載安裝腳本失敗：$($_.Exception.Message)" -ForegroundColor Red }; Read-Host '按 Enter 關閉'
```

When it finishes, open **<http://127.0.0.1:8765/>** in a browser.

> Installation takes about 5; 15 minutes (PyTorch's 700 MB is the bulk of it, depending on your connection). On a slow link, run it inside `screen` or `tmux` so a dropout does not interrupt it.

Detailed installation notes are in **[INSTALL.md](INSTALL.md)** (required tools, platform differences, uninstalling).

> **Code signing**: Free code signing on Windows provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org).

---

## The 51 tools at a glance

### Forms and stamps
- **Auto-fill forms**; field detection plus template values
- **Stamp and sign**; drag a stamp or signature into place
- **Watermark**; text or image watermarks, batches supported

### Editing
- **Page editor**: text boxes, shapes, whiteout, highlighter, signatures, annotations, genuine object deletion
- **Organise / rotate pages, page numbers, N-up**
- **Page borders** [needs OxOffice/LibreOffice]: a border on every page: width, colour, style, rounded corners, double lines and shadow, inset from the edge or flush with the content, selected pages and skip-first-page; especially for slides. Accepts PDF and office files (office input needs the engine).
- **Scan cleanup** [needs OxOffice/LibreOffice]: crops the black edges off a photographed or crookedly scanned page, straightens it and evens out uneven background; for phone photos it finds the four corners of the sheet and corrects the perspective, reporting the correction angle and the residual skew page by page. Takes PDFs, images and office documents (the engine is only needed for office input).
- **Seam stamp** [needs OxOffice/LibreOffice]: one stamp split across consecutive pages, so **a swapped or missing page is obvious** (that slice no longer lines up); side seams and spreads, a configurable page span, and fixed or random position and angle. The stamp can come from the asset library, your own upload, or be generated from text (office input needs the engine).
- **Unify page size** [needs OxOffice/LibreOffice]: bring mixed page sizes onto one paper size (A4 / A3 / custom): scale to fit, centre without scaling, or crop to fill; mixed orientations rotate automatically and **the content stays vector, so text is still selectable** (not turned into images). Tenders often mix A3 drawings with A4 text; unify them before printing and binding (office input needs the engine).
- **Merge files / split pages**
- **Bookmarks and contents** [needs OxOffice/LibreOffice]; add bookmarks (the reader's side navigation) and a clickable contents page; **select several files and they are joined with each filename as a top-level bookmark**, with each document's own bookmarks one level down; invaluable for tenders and annual reports that combine a dozen files into hundreds of pages. Headings can also be detected automatically or a ready-made contents list pasted in (office input needs the engine).
- **Scan merge**: drop in several scans, detect the areas with content, keep the original colour and compose them onto one white A4 sheet at their original positions; built for both sides of an ID, with drag adjustment and automatic whitening of light grey backgrounds

### Content
- **Extract text / images / attachments**; with optional LLM paragraph re-flow
- **Word count** [needs OxOffice/LibreOffice]: tables, charts and an LLM summary; accepts PDF, office and plain-text files
- **Meeting summary** [needs OxOffice/LibreOffice] — turns a transcript (.vtt / .srt / .json / .txt / .docx / .odt) into a summary, decisions, action items, risks and chapters; **every entry points back to the segment it came from and who said it**, and clicking it jumps there. When timestamps are present it also works out the speaking share. Export to PDF / Word / ODF / Markdown / chart PNG / JSON
- **Meeting recording to transcript** — turns a recording or video into a transcript **with timings and speakers**, ready to hand to Meeting summary in one click; recognition runs on the speech service (JTLW), so **it only appears once an administrator has set it up**
- **Official document drafting** [needs OxOffice/LibreOffice]: turns a plain-language request into an approval memo (`簽`: subject, explanation, proposed action) or a letter (`函`: forms of address and closing phrases follow the relationship between sender and recipient), or drafts the handling note (`簽辦意見`) for an incoming document from how you plan to handle it; **the program lays out the format and the LLM writes the content**. Amounts, dates, laws, article numbers, document numbers and claims such as "already approved" are compared with what you provided: anything without support is flagged, and anything missing is marked as to be filled in. You can rewrite a paragraph, keep versions, reopen earlier drafts in **Case history**, and use the **official document knowledge base** set up by the administrator (only "substantive basis" material counts as a basis); exports can use the official document templates from the government open data platform (once the administrator has downloaded them). **The checks cannot verify meaning** (causes, conclusions), so always review a draft before sending it. Output is always a Traditional Chinese Taiwan official document, and the English / Japanese interface works too. Exports plain text / ODT (no engine needed) / Word / PDF (Word and PDF need the engine), and can also export the electronic document DI file (XML, for importing into an agency's document system; agency codes for the recipient and others come from the address book).
- **Annotation report / removal / flattening**
- **OCR**: run OCR on scanned PDFs and images so the text becomes searchable and selectable (the same idea as Live Text in macOS Preview); two engines (**EasyOCR** by default, strong on Chinese, Japanese and Korean; **Tesseract** as a fallback), with optional LLM typo correction. **An external GPU recognition server is supported** (DGX Spark / H100 / 4090 …): download `install.sh` from the admin interface to deploy it, taking a page from 8; 15 seconds on CPU down to 0.3; 0.8 seconds on GPU (**more than 10× faster**).
- **Pre-submission check**: batch verification: page size, embedded fonts, complete fields, leftover personal data, hidden content
- **List tools**; paste text or upload.txt /.csv /.xlsx /.docx /.pdf and treat each line as an item: sort, deduplicate, filter, take the first or last lines, change case and more, combining steps; copy the result or download.txt /.csv /.xlsx
- **e-Invoice processing**: scan a Taiwan e-invoice QR code for the invoice number, date, amount and tax ID, filling in the seller, industry and accounting category (rules plus optional LLM); expense and period checks included, exporting.xlsx /.ods /.csv /.json /.xml /.txt /.md (with a configurable title)
- **Transit receipts** — drop in a batch of Taiwan Railway purchase certificates, THSR electronic ticket certificates or Uber trip receipts; date, transport, route and fare are collected into a table with configurable columns, exportable as .xlsx / .ods / .csv / .json / .xml / .txt / .md for expense claims
- **Company ID lookup**: look up an 8-digit tax ID, or search company, agency and school names, addresses and industries with highlighted matches; category filters, batch lookup and CSV export

### Conversion [needs OxOffice/LibreOffice]
- **Office to PDF**; batch convert office documents to PDF
- **Office format conversion**; convert within the same kind: documents (.odt /.docx /.doc /.rtf /.txt), spreadsheets (.ods /.xlsx /.xls /.csv) and presentations (.odp /.pptx /.ppt); `.docx` / `.xlsx` / `.pptx` can also target a specific version (Word 2007 or Word 2010; 365, for example)
- **Document to images**: each page of a PDF or office document to PNG / WebP / JPEG, by DPI or at a fixed width; multiple pages are bundled into a ZIP
- **Images to PDF**
- **PDF to Markdown**: convert a PDF into structured Markdown, keeping headings, tables and bold; handy for LLM and RAG pipelines
- **Markdown to office document** [needs OxOffice/LibreOffice]; paste or drop Markdown, apply a theme and export PDF or a word processing file (.docx /.odt), with a preview of every page
- **PDF to Word**; convert a PDF back into a word processing file (.docx /.odt) with three engines: pdf2docx (classic and stable), our own jtdt-reform (geometric rules rebuilding editable body text) and our own jtdt-layout (most faithful to the original: page-anchored text frames keeping position, images and rules almost 1:1)
- **PDF to slides**; convert a PDF into PowerPoint (.pptx) or an OpenDocument presentation (.odp), **one page per slide**, keeping the original slide size (portrait PDFs are reproduced too); uses the jtdt-layout reproduction engine

### Security
- **Document redaction / text redaction**: 14+ kinds of sensitive data: ID numbers, phone numbers, bank accounts, tax IDs, AD DNs and more.
  Three ways to handle them: **redaction** (blacked out and genuinely deleted), **masking** (`0912****678`) and **replacement** (a value that looks normal but is not real; good for test systems and reports shared outside)
- **PDF encryption / decryption**
- **Metadata clearing**
- **Hidden content scan**
- **Document compare / text compare**
- **Sentence translation**
- **Document translation** [needs OxOffice/LibreOffice]; translate a whole office document into another language, producing a file with **the same format and layout** (only the text changes). Supports.doc /.docx /.odt,.xls /.xlsx /.ods,.ppt /.pptx /.odp, and shows a preview of the first six pages
- **Compression**

> Tools marked [needs OxOffice/LibreOffice] use OxOffice or LibreOffice (OxOffice first; the Taiwan-localised fork maintained by OSSII, with better CJK support). The other 28 tools only handle PDF, plain text and images and need no Office engine. The install script sets it up for you.

---

## User workspace (optional, administrators can turn it off)

Files your tools produce (PDF / PNG, Word / Excel / PowerPoint, OpenDocument, plain text) can be parked on the server and handed between tools, without downloading and re-uploading in between.

- **Save to workspace**; keep a tool's PDF / PNG / Word / ODT output on the server in one click, isolated per account and visible only to you.
- **Load from workspace**; pull it back into any tool's upload area (OCR → stamp → redaction …) without hunting for the file.
- **The workspace page**: thumbnail previews (the first PDF page rendered), a usage bar and retention period, download / rename / delete, multi-select bulk delete, and drag-and-drop upload.
- **Administrator control**: enable or disable the whole feature (disabled means completely hidden), set per-person quotas, per-file limits and retention hours, and clear a user's usage.
- **Isolation and safety**; each person sees only their own files; with authentication off it becomes one shared workspace; retention is enforced by a scheduled cleanup.

Enabled by default; an administrator can turn it off at any time under “Settings → workspace settings”.

---

## Background jobs and completion notices

Slow work (conversion, OCR, sentence translation, compressing large files …) is handed to the server when you submit it, so **you can close the tab** instead of watching a progress bar.

- **Submit and it runs in the background** — 31 tools go through the job system, including PDF to office / slides, format conversion, OCR, sentence translation, office to PDF, compression, merge, split, watermarks, stamps, seam stamps and pre-submission checks.
- **My jobs**: progress, queue position, elapsed time and the download are all on one page, and a running job can be cancelled. Tools whose output is not a single file (sentence translation, say) take you back to the original page to keep reading the comparison.
- **Nothing vanishes on restart**; job state lives in the database; anything unfinished when the service restarts is marked as interrupted rather than silently disappearing.
- **It will not take the machine down**; memory is estimated before dispatch, and a job that does not fit waits in the queue; the concurrency limits for jobs and Office conversion are both adjustable in the admin area.
- **Web responses come first**; conversion processes run at a lower priority with a limited number of cores (one is kept for the web interface by default), so the interface stays responsive while a large file converts.
- **Saved to the workspace automatically**; only if you have already left the page, so it does not duplicate your own “save to workspace”.
- **Priority dispatch**; an administrator can nominate a few users (senior staff, or time-critical work) whose jobs go straight to the front of the queue. **Running jobs are never interrupted**: the effect is “you are next”, not “you go now”; people on the list still queue among themselves in order, and a job still waits if memory is short.
- **You can see what it is waiting for**: the job list shows which shared resources it needs (Office conversion / OCR / an external service), each with its own concurrency limit, so “why is this taking so long” has a visible answer.

Administrators also get these under “Settings → background jobs and concurrency”:

- **A site-wide job list**: who submitted it, which tool, which file, queue position and how long it has run, filterable to running jobs; every one can be cancelled.
- **Pause dispatch**; let current jobs finish without starting new ones before maintenance. (A running conversion is a separate child process and cannot be frozen, only cancelled; the interface says so plainly.)
- **Performance and history**: running and queued counts, Office conversion usage, CPU and memory, with a historical trend one click away. Memory is measured from the **actual child processes** (soffice is what uses it, not our threads); if it cannot be measured, an estimate is shown and labelled as such.
- **Every concurrency limit is adjustable**: jobs at once, Office conversions at once, external service calls at once, conversion CPU limit and reserved memory. **However large you set them, actual free memory still caps them.**

### Completion notices

When a job ends (successfully or not) you are told. The message contains **only the tool name, file name and status; never the file contents**.

- **In-app**; the sidebar's notification button shows the unread count and recently finished jobs; it works with authentication off too.
- **Email**: an HTML notice carrying the site logo and the tool's icon (images are embedded, so no outbound connection is needed), with a plain-text alternative. The address comes from the account's email field; LDAP / AD / SSO accounts sync it from the source automatically (the attribute is configurable, for example `mail` / `mailPrimaryAddress`), and local accounts can edit it in the account card.
- **Chat and webhooks**: Telegram, Slack, Microsoft Teams, Discord, Zulip, Nextcloud Talk, LINE and a generic webhook. **These channels are currently marked as in development**: the payload format has unit tests but has not been verified end to end against the real services; only email has actually been sent and confirmed received.
- **Each person chooses**; users pick which channels they want; administrators fill in the connection settings (SMTP, bot tokens and so on).

Administrators adjust these under “Settings → notifications” and “Settings → background jobs and concurrency”; “Settings → job management” shows every job on the site.

---

## AD / LDAP directory management

Connecting is only the start: what actually goes wrong is the primary DC restarting, permissions hanging off a primary group and never taking effect, leavers staying in the system, and accounts being reused.

- **Failover across several domain controllers**: the server field accepts several hosts (comma or newline separated), tried in order; when the primary DC is restarting, sign-in moves to the next one instead of locking the whole company out. Connections and queries time out, so an unreachable DC gives a clear message within seconds. A DC that comes back rejoins the rotation automatically.
- **AD primary groups (primaryGroupID) count too**: an AD primary group **does not appear in `memberOf`**: make a group the primary group, hang permissions on it, and not one member gets them, with no hint why on the user's “Member Of” tab. They are now included in permission
- **Nested groups inherit**; assign permissions to a parent department group and members of its child groups get them too (the group tree is drawn on screen already, so not inheriting would be more misleading than saying it is unsupported).
- **Leavers and disabled accounts are visible**; the user list gains “gone from the directory” and “disabled in AD” views and badges. The judgement is **based only on a full scan**: a name-filtered sync sees just part of the directory, and using it as the baseline would mark the whole organisation as leavers, so no conclusion is drawn until a full scan has run. Local and SSO
- **Password expiry warning**; shows how many days remain (already expired is marked separately). The date is the value AD computes itself, so people under a fine-grained password policy (PSO) or with “password never expires” are correct; deriving it from the domain default would be wrong.
- **Bulk disable, with a safety valve**; disable an entire view in one click, after being told how many people would actually change; it can also run automatically after each sync (**off by default**). **If a single run would touch more than 20% of the directory accounts it stops entirely and changes nothing**; an expired service-account password or a changed search
- **Who is signed in right now**: the user list shows how many people are online (one person with several browsers counts once); each account shows its current devices (browser / OS, source address, last activity) and can be signed out individually or entirely, with the action recorded in the audit log. If an account may have leaked, you can kick every device without changing the password first.
- **Assign permissions before they sign in**; click any user in the directory browser to assign roles directly, so a new joiner can work on day one; each row under “member of” can be given group permissions on the spot. Previously you could only assign to a whole OU.
- **See at a glance what someone can use**: while editing a user, expand the “effective permissions” panel: which tools they end up with, and which rule granted each one (direct role / group including nested / OU). Read it straight out for an audit response or a handover.

---

## LLM AI extras (optional, off by default)

Connect an OpenAI-compatible backend (local Ollama, an LLM gateway such as LiteLLM, vLLM, LM Studio or a DGX Spark) and **14 tools** gain smart options automatically:

| Tool | What the LLM does | Mode |
|---|---|---|
| Sentence translation | keeps the layout and uses domain vocabulary | text |
| Document translation | Translates a whole office document into another language, producing a file with the same format and layout | text |
| Extract text | rejoins sentences that a two-column PDF cut apart | text |
| OCR | fixes OCR typos (applied only when the word count matches, to prevent hallucinated edits) | text |
| Auto-fill forms | after filling, the LLM inspects the PNG for misplaced or truncated values | **vision** |
| Pre-submission check | meaning checks plus visual inspection of the PNG (covering what regex and structural checks miss) | text + **vision** |
| Document redaction | customer codes, manager names and internal numbers that regex misses | text |
| Text redaction | the same, for plain-text input | text |
| Word count | adds a 3; 5 sentence summary and the top 10 keywords | text |
| Meeting summary | Transcript to summary / decisions / action items / risks / chapters, each with a segment number you can check | text |
| Official document drafting | Plain-language request to an approval memo or letter, incoming document to a handling note, paragraph rewriting, case history; can use the official document knowledge base; the program lays out the format and checks the facts | text |
| Annotation report | sorts many review comments into “major / normal / question” | text |
| Document compare | beyond the line diff, a plain-language summary of which clauses changed | text |
| e-Invoice processing | when rules do not match an item, the LLM decides the accounting category | text |

**The core tools do not depend on an LLM at all**; without one configured everything works as before. See **[LLM.md](LLM.md)**.

---

## Speech service integration (optional, off by default)

Turn a **meeting recording** into minutes you can actually use, and **your important recordings never leave your network**.

The recording goes to **[JTLW](https://jasoncheng7115.github.io/jt-live-whisper/)** (jt-live-whisper,
which you can host on your own network) for recognition and speaker separation; **the summary, decisions, action items, mind map and translation all stay in this system**.

### How to use it

1. Admin area → Speech service (JTLW): fill in the endpoint and API key (for a self-signed certificate, just paste the certificate in;
   **do not turn verification off**). Until it is set up, Meeting recording to transcript stays greyed out.
2. Upload the recording or video, then **hand it to Meeting summary in one click**.
3. Or go through the API: `POST /tools/meeting-transcribe/api/meeting-transcribe`.

### What you get

| | |
|---|---|
| Speed | A 28.3-minute meeting takes about **112 seconds** (15x faster than real time, measured) |
| Transcript | Carries **timings and speakers**, so you can listen and read together: click the waveform to play from there, and the matching line lights up as it plays |
| Speakers | Click a code to rename it; by default every line from that speaker changes together |
| Accuracy | Measured against the reference answers of a public corpus (AISHELL-4): **our whole pipeline and the speech service's own figure are 0.12 of a percentage point apart** |

### Why the work is split this way

- **The audio stays on your network.** JTLW is self-hosted, so recordings never have to go to any cloud service.
- **Recordings are handed over through short-lived signed URLs**: no account or credential is given to the other side, and the link expires by itself.
- **The summary and decisions stay in this system**, using the same settings and audit trail as the other 13 LLM-assisted tools.
- **Citations are bound to segment numbers, not to text**: after a re-run of the correction step or a glossary update, decisions and action items that were already
  produced still point back to the same piece of audio.

Leave it unset and nothing changes: **the other 50 tools are entirely unaffected**.

---

## Documentation

| Document | Contents |
|---|---|
| **[INSTALL.md](INSTALL.md)** | Detailed installation on all three platforms, required tools, install locations, system requirements, uninstalling |
| **[OPS.md](OPS.md)** | Day-to-day operations: the `jtdt` command, upgrades, reverse proxies (nginx/Caddy), listen address, backup and restore, moving to another host, scheduled cleanup |
| **[AUTH.md](AUTH.md)** | Authentication / RBAC / built-in accounts (jtdt-admin / jtdt-auditor) / 2FA / SSO (OIDC+SAML) / Reverse Proxy SSO (Kerberos) / account lockout / emergency recovery |
| **[reverse_proxy_sso.md](reverse_proxy_sso.md)** | Reverse Proxy SSO (Kerberos / SPNEGO) end-to-end deployment: AD service account, setspn, ktpass / keytab, nginx configuration, automatic browser sign-in, header spoofing protection |
| **[API.md](API.md)** ([web version](https://jasoncheng7115.github.io/jt-doc-tools/api-en.html)) | REST API: bearer tokens, the endpoint list, upload and response formats, error codes, curl / Python examples, the job flow |
| **[LLM.md](LLM.md)** | Optional LLM features (off by default): how 14 tools use an LLM, examples, supported LLM servers and gateways (Ollama / LiteLLM / vLLM…), turning thinking off and concurrency |
| **[SECURITY.md](SECURITY.md)** | Security policy, OWASP Top 10 (2025) mapping, vulnerability reporting, GitHub native scan integration |
| **[COMPLIANCE.md](COMPLIANCE.md)** ([web version](https://jasoncheng7115.github.io/jt-doc-tools/compliance-en.html)) | Data protection and compliance: where documents stay, when the tool connects to the outside, the controls it provides and what your organization does, plus mappings to Taiwan's Personal Data Protection Act, GDPR, ISO/IEC 27001:2022 and ISO/IEC 42001:2023 |
| **[CHANGELOG.md](CHANGELOG.md)** | Full change log |
| **[TEST_PLAN.md](TEST_PLAN.md)** | Test checklist and pre-release checks |
| **[OFFLINE.md](OFFLINE.md)** | Air-gapped / offline installation (moving a Docker image, using a company PyPI proxy) |
| **[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)** | Third-party licence notices |

---

## Privacy and security notes

- **⚠ Not recommended for direct exposure to the public internet**: colleagues will mostly use it for company **internal and confidential documents** (contracts, quotations, personal data, tax records). On the public internet anyone can try to sign in or upload crafted files, and a leaked account or address means a **risk of data leakage**; on the internal network only colleagues can reach it, and they have to sign in first. **Use it on the internal network or over VPN**; if business needs force exposure, that is at your own risk, and at the very least use a reverse proxy, HTTPS, authentication, enforced two-factor authentication, a WAF or rate limiting, and keep dependencies up to date. See [OPS.md](OPS.md).
- **⚠ Anything beyond local access goes through a reverse proxy with HTTPS**: unless it is one person on this machine (several people, other computers on the internal network, or external access), **put it behind an nginx (or Caddy) reverse proxy with HTTPS and never expose `:8765` directly to the network**. By default the app only binds `127.0.0.1:8765` (plain HTTP, no TLS), so exposing it directly sends passwords and documents in clear text. See below and [OPS.md](OPS.md) for the right setup.
- **No cloud; your data stays with you**; every file is processed on your own server
- **A separate data directory**; not mixed in with the user's own files, and it does not roam on Windows
- **Authentication is off by default** (single-machine mode); a fresh install works as before; enable it for a team or an internal deployment
- **Audit log + SIEM forwarding**; with authentication on, every sensitive action is recorded and can be forwarded in real time
- **Optional LLM verification**; off by default, enabled only when you point it at your own Ollama or local LLM; nothing goes to a cloud

### Reverse proxy (nginx) security settings

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name docs.example.com;

    ssl_certificate     /etc/letsencrypt/live/docs.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/docs.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    server_tokens off;            # 不洩 nginx 版本（ZAP「Server Leaks Version」）
    client_max_body_size 300M;    # 必設：上傳大檔
    proxy_read_timeout 900s;      # 必設：LLM 工具單筆推理可能數分鐘
    proxy_send_timeout 900s;
    proxy_buffering off;

    location / {
        proxy_pass http://127.0.0.1:8765/;          # 後端只聽 127.0.0.1
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;  # 必設：後端據此設 Secure cookie + HSTS
    }
}
```

Security headers (CSP / HSTS / X-Frame-Options / X-Content-Type-Options / Referrer-Policy) are set
**by the backend automatically** (HSTS decided from `X-Forwarded-Proto`) → **do not `add_header` them again in
nginx**, or you get duplicates. The three common pitfalls (must be mounted at the root path, `client_max_body_size`,
timeouts), seven shared requirements, guarding against `X-Forwarded-For` spoofing, and full examples for **nginx / Caddy / Apache / HAProxy /
Traefik / IIS (ARR) / F5 BIG-IP** are all in [OPS.md](OPS.md).

See [SECURITY.md](SECURITY.md).

---

## Development

```bash
# Clone repo
git clone https://github.com/jasoncheng7115/jt-doc-tools
cd jt-doc-tools

# 用 uv 建環境(不修改系統 Python)
uv sync

# 跑測試
uv run pytest

# 開發模式(自動 reload)
JTDT_DEBUG=true uv run python -m app.main
```

---

## Licence

**GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later)**; see [LICENSE](LICENSE).
Third-party licences are in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

### What this means for you

- **Using it yourself (an internal deployment for colleagues)**: completely free, with no extra obligation. You may modify it too.
- **You modified it and offer it as a network service to others** (AGPL section 13, **including internal users**):
  you must make your modified source available to those users. Without modifications there is nothing to do; point them at this project's
  GitHub repository.
- **Distributing it closed-source or inside your own commercial product**: AGPL does not allow that.

AGPL rather than a permissive licence because the core PDF engine, **PyMuPDF, is itself AGPL**
(dual-licensed by Artifex), and this program uses it in the same process.

## Disclaimer

This software is provided “AS IS”, **without warranty of any kind, express or implied**, including but not limited to merchantability, fitness for a particular purpose and non-infringement.

- Users **assume** the entire risk of using this software
- For any **direct, indirect, incidental, consequential or punitive damages** (including data loss, business interruption, loss of revenue or damage to reputation) caused by this software, the authors and contributors **accept no liability**
- When handling personal data or sensitive business documents, users must **ensure for themselves** that they comply with local data protection law, company security policy and related regulations
- The LLM / AI verification features are **optional and off by default**; if you enable them against an external model provider, the data transfer risk is yours
- Output from this software (auto-filled forms, redaction, OCR, LLM proofreading) is **for assistance only**; final correctness is the user's to confirm, and important documents should always be checked against the original
- This software has **no affiliation with, sponsorship from or endorsement by** Adobe, Microsoft, OSSII, The Document Foundation or any other third party

Continuing to use it means accepting the terms above.

---

## Links and author

- **Introduction site**: <https://jasoncheng7115.github.io/jt-doc-tools/index-en.html>
- **Source repository**: <https://github.com/jasoncheng7115/jt-doc-tools>
- **Report an issue**: <https://github.com/jasoncheng7115/jt-doc-tools/issues>

**Jason Cheng** (Jason Tools)

