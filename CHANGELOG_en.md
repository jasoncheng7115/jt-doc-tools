[繁體中文](CHANGELOG.md) ｜ **English** ｜ [日本語](CHANGELOG_ja.md)

# Change log (English)

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

> **Scope.** Traditional Chinese is this project's primary language, and
> **[CHANGELOG.md](CHANGELOG.md) is the complete history** (906 releases).
> This English file summarises **recent releases** — enough to see what changed
> and decide whether to upgrade. For anything older, read the Chinese file.

---

## [1.16.77] - 2026-10-10

- **Data protection and compliance page: Taiwan PDPA and GDPR mappings.** New article-by-article mapping for Taiwan's Personal Data Protection Act (rights of the data subject, specific purpose, special categories, deletion, incident notice, security), including the 11 security measures in Article 12 of the Enforcement Rules (7 with what the tool provides, 4 left to your organization), with a note that the amendment promulgated on 11 November 2025 takes effect on a date set by the Executive Yuan. New GDPR mapping (principles, special categories, access, erasure, automated decisions, data protection by design and by default, processors, records of processing, security, breach notification, impact assessment, international transfers) with the official article titles.
- Each card under "What the tool does" now has **Maps to** (article and control numbers that jump to the matching row) and **How to verify** (automated tests you can run yourself, linked to the source repository).
- New **Suggested acceptance tests**: 19 scenarios with pass criteria and the matching tests, plus suggested assessment worksheet columns.
- **Search on the page**: type a control number (for example A.8.15), an article number or a keyword to keep only the matching table rows, list items and cards; sections without matches fold away and matches are highlighted. Full-width characters and spaces do not affect matching; Esc clears the search and keeps your place; `?q=` in the address starts a search.

## [1.16.76] - 2026-10-10

- **Official document drafting: the case history table.** Case names now show the whole subject, cut off with "…" when too long; hover to see the full text. The list used to show the 20-character title used for file names, which looked like the whole subject. Older cases get the full subject the first time they are listed, without changing "Last modified".
- Click a column heading to sort (case, document type, version, check results, last modified, owner); click again to reverse. "Columns shown" hides the columns you do not need. More than 20 cases are split into pages of 20, 50 or 100. The sort order, hidden columns and page size are remembered in your browser.
- "Select all" ticks only the current page; ticks on other pages still count for "Download selected DI files". Search also matches the whole subject.
- The upload button now reads "Upload DI files or a ZIP": a zip of DI files was already accepted, and the button now says so.

## [1.16.75] - 2026-10-10

- **Official document drafting: DI files on the case history page.** Each letter and approval memo has a DI button that downloads the latest version as a DI file (the body file of a Taiwan government electronic document). Tick several cases to download them as one zip (up to 100 at a time); handling notes and cases without a draft are skipped, and the page says how many.
- **Upload DI files**: DI files exported from a document system (.di or .xml, or a whole .zip; up to 50 at a time) each become a case you can open, keep editing and export again. Only official letters and approval memos are accepted. Other letter types are shown in letter format; attachment files and fields with no place in the draft are not imported, and the page says so.
- Imported cases are marked "DI import" in the list and their first version says "Imported from DI"; the checks use the document itself as the source. Agency codes in the file are kept only when they match the agency address book.
- DI files are read without expanding entities, without network access and without loading external DTDs, up to 1 MB per file. Older DI files declared as Big5 are read too.

## [1.16.74] - 2026-10-10

- **The data protection and compliance page has been redesigned**: a one-line summary at the top (your documents stay on your own server) with a note that certifications assess the organization and that the page is not legal advice, and a sticky in-page navigation. New "Where data lives" section with a data-flow diagram, where each kind of data is kept and for how long, and when the tool connects to the outside and what it sends. Card rows for what the tool does and what your organization does, a table mapping Taiwan's Personal Data Protection Act, GDPR, ISO/IEC 27001:2022 and ISO/IEC 42001:2023, and the two ISO mapping tables now say what your organization does for each clause and control. The page is now called "Data protection and compliance".
- Official document drafting: after you click Rewrite, the passage being rewritten stays marked in the draft until you accept or discard the result, and the description above it no longer follows the cursor.
- Official document drafting and the address book test search: agency lists now show how many matched (for example "12 of 165") with a "Show all" button, and main agencies come before internal units such as personnel and accounting offices.
- LLM settings: the embedding model list and the recommendation box now use the full width of the section.

## [1.16.73] - 2026-10-10

- **Official document drafting remembers reference prefixes**: a new button next to the reference number field lists the prefixes each issuing organisation (or company) has used, that is, the part before the serial number. Click one to fill it in, then type the number. Nothing is filled in automatically and the numbers themselves are not stored. The list is kept only in your browser and is updated after a draft is generated successfully.
- Official document drafting: after switching the issuer to a company, the "Previously entered" lists next to the company name and signature still showed the government agency entries. They now switch too.
- Official document drafting: the tool description on the home card and in sidebar search is now one short sentence, about as long as the other tools'. The details are still at the top of the tool page.
- Website: every page's navigation bar now links to the compliance page, including the phone menu. Before, it was reachable only from the audit section on the home page and from the footer.
- Website: on phones, the menu button on the troubleshooting and compliance pages did nothing (the script that opens the menu was missing). Fixed.
- Website: the navigation items are closer together, and the bar switches to the menu button below 1200px (was 1080px), so the English and Japanese bars still fit with the extra item.

## [1.16.72] - 2026-10-10

- **Official document drafting is no longer marked Beta** (sidebar, home card, page title, website and README). The official document knowledge base is still marked Beta.
- **The compliance page now maps to ISO/IEC 27001:2022 and ISO/IEC 42001:2023 clauses and controls**: each section lists the related ISO/IEC 27001:2022 and ISO/IEC 42001:2023 clauses and Annex A controls, each linking to its row in a new "Clause and control mapping" section. That section lists, clause by clause and control by control, which features support it and where in the document they are described, plus the clauses and controls left to your management system. It can be used directly when writing a Statement of Applicability.
- The compliance page also covers a few existing features it did not mention: salted scrypt hashes for local passwords, background jobs queuing by available memory, watermarks and the restricted-use stamp, CodeQL and Dependabot, audit log entries for AI tool use and LLM setting changes, and the evaluation tools for drafting and meeting summaries.
- The website, README and guides always name the edition of the two standards (ISO/IEC 27001:2022, ISO/IEC 42001:2023).
- Screenshots on the compliance page declare their size, so jumping to a section from the contents at the top or from a mapping tag no longer lands in the wrong place while images above are still loading.
- Development: browser tests now give Chromium its own profile directory on every launch and delete it afterwards. Previously Chromium created a temporary profile of about 11 MB that was not removed when tests stopped the browser, and these piled up on the development machine.
- Development: the browser tests for meeting summaries and official document drafting sometimes timed out at the upload step. They put the file in before the page had finished loading, so the upload control was not ready yet and nothing was sent. They now wait for the page to load and check that the file actually went in.

## [1.16.71] - 2026-10-09

- **Repealed law articles are no longer indexed**: articles in the national law database whose content is only "(repealed)" (the article number is kept, the text deleted) are no longer turned into passages. They had nothing to cite yet showed up in search results and took reference slots. The downloaded original still keeps them, and articles that merely mention deletion in their text are indexed as before.
- Laws already imported: with an embedding service set, press "Rebuild index" once on the knowledge base page and they are split again; without one, those articles drop out the next time that law is updated and imported. Searching keeps working in the meantime.
- In the admin sidebar, "Official document drafting settings" now sits directly above the official document knowledge base, so the two settings pages are next to each other (eight items used to separate them).
- Search test on the knowledge base page: the search scope is now a prominent line, "Searching: all N datasets", which turns amber and reads "only the N ticked datasets" once you tick any, with a "Search all" button next to it. It used to be a single grey hint that was easy to miss.
- Search test results mark where the words of your question appear in each passage (yellow). A single character, or a word that appears almost everywhere on its own, is not marked, so passages do not turn entirely yellow; passages found only by vector search may have no marks. What drafting gets from the knowledge base is unchanged.
- **Knowledge base search ranks by the vector results when a vector index exists**: keyword and vector results used to be merged with equal weight. In a test of 42 requests with every top-6 result judged by hand, the merge did worse than vector search alone (nDCG@6 0.787 against 0.885), most of all for long requests, because keyword matching pushed up passages that look alike but are about something else. Now the vector order is used, and keyword results only fill in when vector search finds fewer than 6. Without an embedding service, or when it cannot be reached, search uses keywords alone as before. Looking up an article number (such as Article 25 of a given act) still finds it in the top 6 every time.
- **Select all in the government open data list**: repealed items are now skipped (they would be imported as inactive and never come up in searches) and the page says how many were skipped; you can still tick them yourself. The limit goes from 1,000 to 3,000 items, plus a total of 8 million characters, so the whole "Acts" category of the national law database (1,338 items, about 5.9 million characters) can be selected at once, while the "Regulations" category (about 10,000 items, 24.8 million characters) needs a search or filter first. Over the limit, the page says how many items and characters selecting all would give.
- After "Save selection", if some ticked items have not been imported yet, a prompt explains that they are only imported after "Download and import", and offers to start now or later. A note next to "Download and import" keeps saying how many ticked items are not imported yet and disappears once they are. Before, saving only said "saved", which looked as if the items were already in the knowledge base.
- **Vector search on a large knowledge base**: measured with the whole "Acts" and "Regulations" categories of the national law database imported (about 160,000 passages, 4,096-dimension vectors), one search used to take 7 to 9 seconds with 5.3 GB of memory held by the server; it now takes about 0.1 seconds with 2.7 GB. Every search used to copy the whole set of vectors, and loading kept a second copy; now the score is computed over the whole set directly, and only vectors of active documents are loaded.
- During bulk imports (government open data, several uploaded documents, an index rebuild) the vectors are no longer reloaded after every batch written (15 to 18 seconds each time at that size): they are reloaded once the import ends, or at most every 10 minutes during a long one. Passages added in the meantime are found by keyword search only until then; deactivated documents still never come up.
- **Data sources in Official document settings**: each source card now has three layers: the name with its type and dataset page on one line; the status dot, count and last download time on the same row as "Update" and "Upload file"; and the download URL and delete button folded into "Source settings" (opened automatically after a failed download), with the attribution in small print at the end. "Add source" and "Restore default" moved below the cards, and "Refresh" was removed (the page updates itself during downloads). Each card used to show two tags, a long URL, two lines of attribution and three differently coloured buttons at once.
- **Dataset list in the knowledge base search test**: with many datasets (such as a thousand imported laws) the checkboxes used to spread across the page in uneven rows. The list is now folded away behind "Choose datasets"; when opened it is grouped by category in equal-width columns, long names are shortened (hover for the full name), and the list scrolls within a fixed height. You can filter by name, filter by category, show only ticked items, and tick everything currently listed. Inactive datasets are marked.
- **References in official document drafting**: each item now shows the name and article number, one line with chapter and version, and the first two lines of the text, with "Full text" to expand. The chapter used to appear twice and the article number was missing. A line above explains what each purpose is for: a "Substantive basis" can serve as the basis for the draft, and law names and article numbers in the draft are checked against it; the others only guide format and wording. Each item can be acted on: when the draft mentions the law, it says on which line, and "Show in draft" selects that text in the draft; "Copy" copies the name and article number for citing. This updates after rewrites and edits. The attribution required by the licence is shown once per source at the bottom of the section instead of under every item.
- "Use past cases" in drafting is renamed to make clear it only looks at the user's own cases.
- **Drafting shows when the AI is working**: in the two steps that call the language model (extracting data, writing the draft) the progress text now says what is happening: "sent to the LLM server, waiting for the AI to respond" when the request goes out, "the AI is responding, N characters received" while the reply arrives (the count keeps rising), and "the AI's reply was not in the expected format, asking again" when it has to ask again. Before, the whole step only read "Writing the draft (2/3)", and twenty seconds of that gave no sign the AI was involved. "Checking the draft" compares the draft with the source in code without the AI, so its text is unchanged. On phones and other narrow screens, the progress bar shared by all tools now takes its own line with the status text below it when the text is long (the bar used to be squeezed to a dozen or so pixels).
- **Contact details in a letter are now separate fields**: they used to be one multi-line box where people typed the field names themselves, which went wrong in three ways: writing a variant of the address label (or the spaced-out form common in official documents) was not recognised as the address, so the DI file had an empty address; a line starting with the explanation label or an item number cut the contact block off and turned the following lines into the explanation section; and pressing Enter inside an address made it two contact entries. Address, contact person, phone, fax and email now each have their own optional field (plus company ID for company letters); the system writes the field names and order and leaves out empty ones, and the contact field can be labelled `聯絡人` or `承辦人` in the draft. Contact details remembered in the browser and cases saved before the upgrade are split into the fields when opened; lines that match no field are listed under the fields and left out of the draft. API: new `contact_fields` (one key per field); the old `contact` text is still accepted, recognised lines go into the fields, and unrecognised ones are left out of the draft and listed in `contact_unplaced` in the response.
- **Rewrite a passage**: the Rewrite button names the chosen method. After choosing a method the button switches to the primary colour, pulses once, and a hint says to press it; the hint goes away once it is pressed. Before, choosing a method changed nothing on screen and looked as if the rewrite had already started.
- **Fonts with official templates**: when exporting with the official templates, the "draft" mark, the binding-line characters and the page numbers came out in a sans-serif font unlike the body text. The templates set no default font (the font is set on each body paragraph), and these items are added by the tool. They now use the body font of the template.
- The version column of the cases list shows just the number.
- **Address book trial search**: matching characters are highlighted, following the same matching rules (variant characters, full- and half-width). The organisation lists in drafting highlight matches too.
- **System status, file usage per user**: click a user name to see current usage by category (temporary files, My workspace, official document cases, meeting recordings, form filling / stamp / watermark history) and uploads in the last 30 days by tool. The totals now include My workspace, official document cases and meeting recordings, which were not counted before.
- **API token file permissions**: the file holding API tokens (`data/api_tokens.json`, tokens stored in plain text) was written without an explicit mode, ending up 0644 on Linux and macOS, so other accounts on the same host could read it. It is now always written as 0600 (readable by the service account only), and an existing file is tightened once when the service starts.
- **Compliance support (ISO/IEC 27001 and ISO/IEC 42001)**: new `COMPLIANCE.md` and a "Compliance support" page on the website listing the controls the tool provides (authentication, account lifecycle, access control and separation of duties, isolation between users, audit log, retention, encrypted secrets, isolation of document processing, backup and recovery, web security, security testing, the AI inventory and safeguards against AI errors), each with how an organization uses it and where the records are kept, plus a deployment checklist. Every item was checked against what the code does. The home page and every page footer link to it.
- **File retention, meeting recordings**: original recordings uploaded for transcription (`data/speech_audio/`) had no retention period and stayed on the server indefinitely. "File retention / cleanup" now has a "Meeting recordings" row, 30 days by default, which can be changed or set to `-1` (keep forever); the page shows the space used and the oldest item. A recording whose job is still queued or being transcribed is never deleted (the speech service still has to fetch it). Transcripts and results keep following the "Job result files" retention. **The first cleanup after upgrading deletes recordings older than 30 days**, so change the number first if you need them longer.
- **Website footer**: the "Jason Tools" link in every page footer had been broken since v1.9.102 (the address had been replaced by a placeholder), in all three languages. It points to `https://www.jason.tools` again, and a check now requires every external link on the site to use a real domain.
- **Audit log, admin reading another user's file**: the record written when an administrator opens or downloads someone else's file left the user and IP columns empty (the administrator appeared only as a numeric ID in the details), so filtering the audit log by account did not find it. It now records the administrator's account and source IP.
- **Audit log, downloads and API token calls**: downloading a result left no record at all (tool use records the submit, and a download is a separate request). With authentication on, downloads from My jobs, from a tool's result page and from the workspace now each write a `file_download` record with the account, source IP, tool, file name and size. The same person downloading the same file again within five minutes is recorded once; previews, thumbnails, page images and recording playback are not recorded, so the records that matter are not buried. Auditor views of history files already write `auditor_view` and are not recorded twice.
- Calls made with an API token now say which token was used in the tool-use and download records (`via: api_token` plus the token name, never the token itself), and the account recorded is the token's owner.
- `/api/convert-to-pdf` and `/api/llm-review` were never recorded as tool use; they now write `tool_invoke` with the uploaded file name. Audit forwarding (syslog / CEF / GELF) sends the new records too.

## [1.16.70] - 2026-10-09

- **Knowledge base embeddings now support NVIDIA Nemotron-3-Embed properly**: when a model from that family is selected, the system adds the official `query: ` and `passage: ` prefixes automatically and, on Ollama, loads it with an 8,192-token context (memory use drops from about 28 GB to about 11 GB on a server with a large default context; the vectors are identical). Prefixes still cannot be set by hand; other embedding models are sent as before and existing indexes need no rebuild.
- In a test on the official-document knowledge base (42 questions, 1,503 passages), Nemotron-3-Embed-8B with the official prefixes had the highest recall (88% in the top 6, 80% for long requests), against 71% without them.
- The embedding settings on the LLM settings page now say what is added for the selected model, and recommend an embedding model with its Ollama download command and memory needs. Switching from another model needs one index rebuild.
- API: models listed by `/admin/knowledge/api/embedding/models`, `/admin/knowledge/api/embedding` and the connection test now carry `usage`.

## [1.16.69] - 2026-10-09

- **Official document drafting can use your past cases**: a new "Use my past cases" checkbox finds up to three of **your own** earlier cases similar to this request (latest version of each) and lets the draft follow their wording and structure. They are listed under References with a link back to the case, its date and version; if nothing similar is found, the page says so. Works together with the knowledge base option.
- **Only your own cases are searched**, administrators included; deleted cases, cases without a draft and the case being regenerated are skipped. The checkbox only appears once you have a case of your own.
- Past cases are references for wording only and **never count as a basis**: figures, dates or document numbers copied from an old case are flagged by the check.
- The API takes `use_history` and returns `history_note`.

## [1.16.68] - 2026-10-09

- **Official document drafting can export the electronic document DI file**: memos and letters download as a DI file (the XML body file of a Taiwan government electronic document) that officers import into their agency's own document system before numbering, approving and sending as usual. Every file is checked against the version 104 DTD of the National Archives Administration's records management rules. Handling notes have no DI file.
- **Export preview viewer**: document view, field view and colour-highlighted XML source with line numbers, plus whether the file passed the DTD check and remarks such as agencies without a matching agency code. The download is exactly the previewed file.
- **Agency names are picked from the address book**: the issuing agency, recipient, original and copy fields now show a site-styled list with agency codes; picking records the code, and editing the name drops it. Missing or out-of-date (over 30 days) address books are pointed out, with a one-time reminder.
- Knowledge base: card titles built after page load (the government open data cards) now collapse; table buttons and status got icons; the help text explains that the search runs automatically on your request description.
- LLM settings: the embedding model and API type dropdowns use the site style. Slashes on screen and in the docs are now half-width.

## [1.16.67] - 2026-10-09

- **The e-mail "Send test" in notification settings now sends the real notification layout** (a sample job-completion message with the card, icons and the "My jobs" link, subject marked `[測試]`), so you can see how it looks in your mail client without waiting for a long job. Other channels still get plain text.
- **Official document drafting reminds administrators to download the reference data first**: the office templates, the agency address book and the government open data (laws and writing rules) are not shipped with the program. Administrators now see a list of what is still missing at the top of the page, with links to the settings pages; it disappears once everything is in place. Regular users do not see it. Opening the page no longer creates an empty knowledge-base database.
- The "use a local LLM server" notice at the top of LLM tools had two words run together in English; fixed.
- **Intro site**: more formal section headings and descriptions (why self-host, My workspace, background jobs, LLM, AD / LDAP, meeting recordings, auditor and compliance), a shorter official document drafting description, and fewer em dashes.

---

## [1.16.66] - 2026-10-08

- **Fix: "Send to Meeting summary" did nothing when the workspace is turned off.** The job result is carried over instead (same transcript, renamed speakers included); if it really can't be carried, it says so. Tools that load the workspace script themselves no longer show workspace buttons while the workspace is off. Queued or running meeting-summary, transcription and document-translation jobs no longer risk having their input cleaned up before they finish.
- **The workspace stores recordings**, and meeting transcription can load a recording straight from the workspace without uploading it again. Formats are detected from the content; recordings have their own size limit (500 MB by default).
- **Meeting summary**: your own replacements can be sent back to the transcription job as known misheard spellings, so JTLW re-runs only the correction and the transcript download is fixed too.
- **My jobs**: "Open" is no longer shown once the job's data has been cleaned up; the retention page reports the real size of job files.
- **LLM settings**: Test connection shows the model's context length on Ollama and warns below 16K; one click creates a larger-context copy (the original model is untouched). The official document knowledge base's embedding settings moved to this page; by default they use the LLM server set up above (or the server assigned to official document drafting), so only the embedding model has to be entered, and no query or document prefixes are added any more. The whole page now has a single Save button docked at the bottom of the window: it saves the LLM settings and, if changed, the embedding settings, and says so separately if the embedding part could not be saved. The official document knowledge base is marked Beta.
- **The knowledge base is renamed "Official document knowledge base"** (sidebar, pages, the option in official document drafting, notifications and My jobs), since official document drafting is the only tool that uses it. The URL (`/admin/knowledge`), APIs and data are unchanged, and searching the sidebar for the old name still finds it.
- **Official document knowledge base (Beta): import Taiwan government open data** — the national laws and regulations database (one segment per article), NDC administrative rules and the Executive Yuan's document-handling rulings. A single "Download and import" button fetches the latest list and imports the ticked items in one go (if the download fails but the server already has a list, that list is used and the result says so; an offline upload imports the same way). Nothing is downloaded until an admin presses it; each imported version carries its attribution. "Items to import" shows the whole list a page at a time, with a selected-only view, a law/regulation filter, search, and "Select all" / "Clear the selection" for everything in the current view (up to 1,000 items). The page is kept simple: three steps at the top, the blue button first, and address settings, offline upload and attributions folded under "Download sources". Official-document drafts that cite an abolished law get a warning. Keyword-only search now finds segments containing all of the query's common words.
- **Official documents**: times written differently (`20:00` vs `晚上8點`) are no longer flagged; template output for company letters drops agency-only fields, keeps contact lines without a contact frame, and leaves room for the seal; draft and preview headings are aligned; the check / versions / export area is laid out as cards, with page-annotation options as icon cards and a note that the preview above updates immediately (with a "Show preview" button that scrolls to it).
- **Official documents: case history.** Every draft is kept on the server under *Case history* (button under the tool title): search, filter by document type, reopen and keep editing (draft, versions and the original inputs come back), rename or delete. Administrators see everyone's cases, with deleted ones greyed out. Cases no longer disappear with the two-hour temporary files; their retention is a new *Official document cases* row on the retention page (365 days by default). "Open" in My jobs and in notification e-mails now opens the case, so it still works after the job record expires.
- **Endorsement opinions**: dates written as "this year" in a draft are converted to the ROC year (with a reminder to check the year if it is sent after New Year); asking up twice (the body already says to submit for approval and the ending does too) is flagged; `回函` is written as `函復`. A new export option adds a heading and handling-officer block for printing on its own (off by default). The examples loader is easier to spot, and the export table has a divider between download and workspace.
- **Official document knowledge base**: the government-data card on the official document knowledge base page shows how many items can be imported and how many are downloaded and imported, per source (sources whose list has not been downloaded are shown as such, not as 0). The government-data page shows each source's list → selected → imported counts and a coloured box for the last run, uses the site's drag-and-drop upload for offline files, and the address fields no longer overflow. Official document knowledge base jobs are labelled "Official document knowledge base" (not "Official document drafting") in My jobs, notifications and e-mails. The status area on the official document knowledge base page is now three tiles (search method, active passages, embedding service) with a separate note for each thing that needs attention.
- **Picking instead of typing, and clearer lists**: the embedding model is now chosen from the models on the server (on Ollama only embedding models are listed, with dimensions and context length; OpenAI-compatible servers list every model, likely embedding models first; "Enter manually…" appears only when the list cannot be fetched). The rewrite options and the "full incoming document / my own outline" choice in official document drafting are icon cards like the page-annotation options. Group headings in drop-down lists have a full-width tinted bar. Info boxes that start with an icon keep wrapped lines aligned with the first line's text. NDC administrative rules show readable levels (internal agency rules / interpretive rules and discretion standards) instead of the source file's codes, with the legal basis as a tooltip. Official document checks no longer flag the year the program itself filled in (a source saying "this December" becomes `115年12月`, and the `115年` used to be reported as unsupported); wrong years or months are still flagged. The intro site has a new official document drafting screenshot in all three languages. Several tooltips and the PDF editor's font search box showed raw code (`' + tr('…') + '`) instead of text; fixed.
- **Settings backup / import no longer locks out a new host or hands personal data to someone else (issue #55).** Importing a full backup on a fresh host used to switch it to local sign-in with no accounts at all; authentication settings are now not applied when the host has no local administrator, and the page says why. Workspaces, notification preferences, ride-receipt settings and buffers, submission-check entities and API token owners were stored by user number and restored as is, so user 3's data went to whoever is user 3 on the new host. Backups now carry an account map (account names and sources only, no passwords), and import matches by account name; data for accounts that do not exist on the new host is not restored, and token owners without a match are cleared. Roles assigned to individual users and groups are now backed up too (administrator and auditor roles are never granted from a backup). The page lists everything that was not restored. OPS.md has a new "Moving to another host" section for Windows, Linux and macOS: copy the whole data folder, not the program folder, and what to check afterwards.
- **Notification e-mails**: the card shrinks with the reading pane; long file names and error messages wrap instead of widening the message.
- **Linux**: after an OS upgrade replaces the system Python, the service now says what happened and `sudo jtdt update` rebuilds the environment with a private Python 3.12 inside the install directory; new Linux installs use it from the start; healthy environments are left untouched. On Windows, new installs keep Python inside the install directory instead of the installing user's `%APPDATA%` (re-run the installer to move an existing install; data and settings are kept).

## [1.16.65] - 2026-10-08

- **Official document drafting: letter layout.** Letters leave room for the seal: a blank line before the original/copy recipients, and the signature sits about 2.6 cm lower and towards the right. The ending of an internal memo is unchanged.
- Every line between the title and the recipient (address, contact person, phone, e-mail…) goes into the contact block on the right. Previously only a few spellings were recognised; the rest came out as large body text and, with a template, even before the subject.
- **New optional field: reference number** (`doc_no` in the API). Written as given; left blank when empty, and the draft never makes one up.
- **No more blank last page with a template.** Blank lines at the end of a template were pushed onto a new page once the draft grew longer; trailing blank paragraphs are now removed on export. Frames, tables and blank lines inside the text are left alone.
- *Rewrite a paragraph* now sits under the draft and above the preview; on wide screens the draft box is as tall as the preview, so no big empty gap on the left.
- **Fix:** a document number copied from the source was flagged as unsupported, because the words in front of it were counted as part of the number. Only the number and the two characters before it are compared now; a wrong agency code or number is still flagged.
- When a caller asks the model to think, the service log no longer claims that the disable-thinking parameters were sent and ignored.

## [1.16.64] - 2026-10-08

- **Official document drafting: letters from a company to a government agency.** Letters now have an *Issuer* setting: government agency or company. As a company, the draft refers to itself as "this company" and to the agency with the "your agency" form, with no upward or downward relationship (so the relationship field is hidden), and uses a company set of closing phrases. It is still a letter, not a separate document type.
- A company letter's heading is the company name, without the archive number, retention period and classification fields that only agencies use; the company address, business ID number, contact and signature/seal are marked as to be filled in when empty. Company and agency details are remembered separately in the browser.
- The model is told to write requests to the agency (review, approval, acceptance, payment, refunds) as requests, and to keep delivery, receipt check, the company's own testing, agency acceptance and passing acceptance apart. New checks flag the superior-agency form of address, internal-memo wording and agency self-references in a company letter, and claims such as "passed acceptance", "extension approved", "no penalty" or "force majeure" that your text doesn't state.
- **Fixed: writing instructions counted as facts.** A request such as "don't say the plan has been approved" contains the words "has been approved", which used to let a draft saying it had been approved pass the check, for agency memos and letters too. Such instructions no longer count as evidence, and numbers inside them no longer trigger "not mentioned in the draft" hints.
- **16 more examples** (52 in total) for company letters, grouped by issuer and document type in the examples dropdown; loading a letter example also sets the issuer.
- **Page additions when exporting** (below the layout picker): page numbers, a binding line on the left, an original/copy mark and the dispatch method in the top left, the delegated-approval line under the signature, and the recipient's postcode and address for window envelopes. The original/copy mark, dispatch method and recipient address apply to letters only, and the delegated-approval line to agency letters only. They are added to exported files, images and the preview, never to the draft text, and they also work with the government templates.
- **PNG and SVG downloads**, with the same layout as the PDF; several pages come as one image per page in a zip. SVG text is converted to outlines.
- Sentence-final punctuation no longer hangs past the right margin, where Writer often hid it so the sentence looked unfinished.
- When the recipient is filled in, the draft no longer asks for the vendor's name and uses the form of address instead; tax wording is placed after the amount, and download names no longer start with the request phrase.
- The official document settings page lists each data source as a card, with the enable switch beside the name and the buttons in one row.

## [1.16.63] - 2026-10-08

- **Official document drafting writes more like a real document.** The proposal section now says what is being approved, what happens after approval and what still needs confirming: it opens with `擬請同意…` covering exactly what you asked for (asking only for an inspection and a quote never becomes approval to start work), then `奉核後洽請…協助確認…，再依確認結果辦理…`. A proposal that asks for no approval at all is flagged. Things you said still need another unit's confirmation are written as `尚待○○單位確認` instead of a `〔待補〕` placeholder; `〔待補〕` is kept for data that is really missing, and `〔待確認〕` only for contradictions in what you gave. The subject reads `為辦理○○一案，預估所需經費新臺幣○○元（含稅），簽請　核示。` rather than "please let the supervisor agree…", the explanation is grouped into background, what will be done, and budget plus pending items, and common plain words are replaced with formal ones.
- **Known product names get their standard spelling**: `vmware esxi` and `windows server` in your text become VMware ESXi and Windows Server in the draft; URLs, e-mail addresses and file names are left alone.
- **A date whose day of the week is wrong is flagged** (for example a Saturday that is really a Sunday), and is never corrected for you; when no year is given the message says which year it assumed. A "before …" deadline that has already passed is flagged too.
- **Letters (`函`)**: with no recipient filled in, the text no longer contains a `〔待確認：受文者稱謂〕` placeholder; the subject never ends with two closing requests; letters to a company use `貴公司` and to a member of the public `台端`; if your text mentions an attachment but the attachment field is empty you get a reminder (not when you said it is not attached yet); and with no issuing agency filled in, the draft keeps the `本局` or `本所` you wrote.
- **More accurate checks**: `三場` against `3場`, `一個半小時` against `1.5小時` and `三個問題` against `3項問題` are no longer reported as unsupported. A date written as `今年11月6日` was not recognised as a date at all, so the draft's `11月6日` was flagged as missing from your text; fixed. A draft that turns "this year" into the wrong year is now flagged, and a law article you wrote yourself that the draft leaves out is pointed out.
- **Rewriting a passage now has an effect.** The model used to pull the whole request into the selected passage (so "shorten" made it longer) and barely touched passages that were already formal. It now rewrites only the selection; a shortened version that is not shorter is retried and then reported; a result that is almost unchanged suggests using Custom instead; and a list gets the next level of item numbers (`（一）` under `二、`).
- **36 ready-made examples** can be loaded from under the request box, grouped by document type (approval memo, opinion on an incoming document, letter) and topic. Picking one switches to that type and fills in the fields without submitting; if the fields already have text you are asked first.
- **Values you typed before are remembered** for fields such as the drafting unit and the approvers: a button next to each field lists them, and you can pick or delete them. They are stored only in this browser.
- **You can see when it is working**: while generating, regenerating from edited data or rewriting, the button shows a spinner and the draft area is greyed out with a spinner until it finishes.
- **Regenerating saves a new version automatically** in the same draft's version list (earlier versions stay); unsaved edits are saved as a version first.
- **A layout preview next to the draft**: once the draft appears, a preview of the page in the current layout is rendered beside it (below it on narrow screens), with a spinner until the image is ready, and it refreshes when you edit the draft or change the template. Without an Office engine the page says the preview is unavailable (ODT export still works).
- **Steadier with weaker models** (tested with TAIDE 12B): when the model starts repeating garbage inside a field, generation stops at once and is retried with a little temperature; a reply cut off or with an extra bracket keeps the fields it finished (a half-written sentence is dropped rather than put in the draft); examples in the prompt that got copied into drafts (an agency name you never gave, a "tax included" you never wrote) are gone, and the check flags tax wording you did not write. Even so, TAIDE still fails to produce valid output on 1 of the 36 built-in examples and 5 of the 19 evaluation cases, and it computes totals and adds steps nobody asked for (the checks flag these), so **TAIDE is not recommended for official document drafting**; gemma4:26b handles all of them with no fabrication.
- All dropdowns on the page now use the site's own style.
- Fixed: exporting a PDF with the official `簽` template, the next field's white background covered part of some field labels.
- **Official document settings page re-laid out**: names on one line with the type badges below, the credit line and dataset link under each row, result and count merged into one status, and icons on every action button. A new panel, "Where the downloaded data is used", explains that the templates feed the Layout dropdown on export and the address book feeds the agency suggestions for letters, with a button to the tool.

## [1.16.62] - 2026-10-07

- **Official document drafting is now marked Beta** in the sidebar, on its home-page card and next to the page title, so people know the tool is still being tried out and its output needs a careful read. It is only a label: permissions, listing and the API are unchanged.
- **Fixed: rewriting a selected numbered item dropped its number.** Selecting a line such as `一、…` and rewriting it came back without the `一、`. Item numbers and section labels (`一、`, `（二）`, `1.`, `說明：`) are formatting, not content: the selection now skips them, and also skips the closing phrase the program adds (`，簽請　核示。`), so they stay in the draft and only the text itself is rewritten. The rewrite endpoint does the same on the server, putting the number back after the rewrite.
- A selection that spans several items (for example from `一、` into `二、`) is no longer rewritten; the page asks you to rewrite one item at a time, since merging them would lose the later numbers.

## [1.16.61] - 2026-10-07

- **Official document drafting: letters, paragraph rewrites, saved versions, a knowledge base and government templates.** A new mode writes a `函` (a letter to another agency, a unit or a member of the public). You pick the relationship (to a superior, a peer, a subordinate, the public, or not sure), and the program, not the model, chooses the form of address (such as `鈞府`, `貴所`, `台端`) and the closing request (such as `請　查照`, `請　鑒核`); the closing list only offers the ones that fit that relationship. The date, reference number, file number and classification are left blank for the document management system to fill in, and a letter to a superior that uses `貴` (or one to anyone else that uses `鈞`) is flagged. The issuing agency, contact details, signature and copy recipients are remembered in your browser. You can now select a passage in the draft (or put the cursor on a line) and ask the model to shorten it, make it more formal, turn it into a list, or follow your own instruction; the before and after are shown side by side and nothing changes until you accept it. The closing phrase is kept as is and never sent to the model, and a number, date or law that appears only after the rewrite is flagged. Versions are saved on the server: saving, accepting a rewrite and restoring each keep a version, restoring also saves a new one, and when two tabs edit the same draft the second save asks whether to load the latest or save anyway instead of silently overwriting. With "Use the knowledge base" ticked, the tool first searches the new knowledge base set up by the admin and lists what it used under "References" (document, version, page), marking the ones the model says it cited. **Only material marked as a substantive basis counts as support**: format manuals and sample documents are used for wording only, so an amount, date or law copied from them into the draft is still flagged. What you can search depends on who is signed in; if the search fails, the draft is still produced and the page says it did not use the knowledge base. Once the admin has downloaded the government templates, export to ODT, Word or PDF can use them as the layout (one for `簽`, one for `函`); a template that cannot be read falls back to the built-in layout and tells you. Once the agency address book is downloaded, typing two or more characters in the issuing agency or recipient box suggests full agency names. Loading text from a file now uses the same upload area as other tools: drag in a PDF, Word, ODT, RTF or text file, or load one from the workspace. **The tool now works in the English and Japanese interfaces too**: it always produces Traditional Chinese documents in Taiwan's format, and the interface language only changes the labels on screen (greying it out in 1.16.60 was the wrong call). The API (`POST /tools/official-doc/api/official-doc`) accepts `use_kb` and returns `references` and `kb_note`.
- **New admin page: Knowledge base.** A document library tools can use as support. Create datasets (name, category, purpose, which groups can see them), upload PDF, Word, ODT or text files, and the system splits them into passages with page numbers and headings; a document can have several versions with one active at a time. Search is keyword-based by default (no model needed); with an embedding service (Ollama or OpenAI-compatible) it can switch to vector search, and rebuilding the index runs in the background with progress. You can try a search on the page to see which passages and sources come back. Instructions aimed at the AI hidden in a document are removed before anything reaches a tool's prompt.
- **New admin page: Official document settings.** Manages two open government datasets from the National Development Council's archives administration: the `筆硯公文製作系統表單範本` templates and the `公文電子交換系統地址簿` address book, used under the `政府資料開放授權條款－第1版` licence, with the source credited on screen and next to the export options. **They are not shipped with the program and nothing is downloaded until an admin clicks Download.** The built-in URLs can be edited and sources can be added or removed, since datasets move; machines without internet access can upload a file obtained elsewhere. A failed download keeps the previous copy, and URLs pointing to (or redirecting to) internal addresses are refused.
- **LLM settings: a tool can use a different server.** You can set up several LLM servers (address, key and default model each) and send a given tool to one of them, for example when documents may only go to the agency's own server. If that server is missing or misconfigured, the tool fails and says why; it **never falls back to the site-wide server**.
- Sentence-by-sentence translation: when extracting text from a file fails, the error no longer shows internal details (they go to the service log).

## [1.16.60] - 2026-10-07

- **New tool: Official document drafting.** It turns a plain-language request into a `簽` (an internal memo asking a superior to approve something), or, from an incoming document plus how you plan to handle it, drafts the `簽辦意見` (the handling note written on that document). What you get is a draft: the officer in charge checks and edits it before it goes anywhere. The program, not the model, lays out the format: the section names (`主旨` / `說明` / `擬辦`), the item numbering (`一、`, `（一）`, `1、`, `（1）`), the closing phrase of the subject line (such as `簽請　核示。`, selectable), `陳核` / `陳閱`, the `新臺幣` and `臺` spellings and Minguo (Republic of China) years; the model only writes the content. How to handle an incoming document is your decision: without it nothing is drafted, and the tool never decides on your behalf to approve, reject or simply file it. If a proposed step in the draft is one you did not ask for, or one you explicitly ruled out (for example you wrote not to forward it, and the draft proposes forwarding it), that step is flagged. Anything required that you did not give is marked `〔待補：…〕` and anything your text states two different ways is marked `〔待確認：…〕`; the tool neither fills the gap nor picks a side. A fact check compares the draft with what you supplied: amounts (including `萬` conversions and Chinese numerals), quantities, dates (Minguo or Western years), names of laws, article numbers, document reference numbers, and wording that presents something as already settled (such as "already approved", "contract awarded" or "required by law"). Anything it cannot find support for is flagged, not deleted, and you decide whether to keep it. It also points out an amount with no funding source, a number or date from your text that the draft leaves out, and a value you changed in the data table that the draft still shows in its old form. Instructions aimed at the AI hidden in an incoming document are detected and flagged, and that sentence is removed before anything goes to the model (the check does not count it as support either). **The checks cannot judge meaning**: a reversed cause and effect or a wrong conclusion only shows up when a person reads it, so every draft needs a line-by-line read. A data table lists what the model picked out (subject, amount, funding source, deadline and so on), each marked as found in your text, inferred, not provided or conflicting; you can edit it and generate again from the edited values. The draft itself can be edited in place and checked again without calling the model. Export: plain text (ready to paste into a document management system), ODT (built by the program itself, **no Office engine needed**), Word (.docx) and PDF (through the Office engine), and JSON. The page header says "Draft" by default (you can turn it off), and the font is `標楷體` (DFKai-SB); when the server does not have it, the PDF falls back to another regular-script font, then to a Ming (serif) font. It runs as a background job, so you can close the tab and reopen the result from My jobs; there is also an API (`POST /tools/official-doc/api/official-doc`). The tool is offered in the Traditional Chinese interface only, since what it produces follows the format of Taiwan government documents; in the English and Japanese interfaces it is greyed out. The LLM has to be enabled in the admin area. Only `簽` and `簽辦意見` are supported for now: letters (`函`) and other document types, agency templates, a knowledge base, links to document management systems and issuing numbers are not included. Recommended models and measured results are in LLM.md. Tools: 50 → 51; tools with LLM extras: 13 → 14.

## [1.16.59] - 2026-10-06

- **Meeting transcription: a very long "Terms or meeting background" entry no longer stalls the whole site.** When a line in that box had a long run of spaces and no "wrong spelling → right spelling" arrow, checking the line took time that grew with the square of its length (about 5 seconds for 16,000 spaces), and the check ran on the same path that serves every web request, so one submission froze the site. Any length is now checked at once. Three other shapes in the same box behaved the same way and are fixed too: a long run of brackets to the right of an arrow (now reported as too long straight away), tens of thousands of misheard spellings on one line, and tens of thousands of repeated terms (both now report the limit immediately). When deciding whether a piece of text is a sentence, the count of Chinese characters also counted Korean, Yi and private-use characters because the start of one range was mistyped; it now counts CJK ideographs only. When the "thinking" check on the LLM settings page fails, the model name written to the service log no longer carries line breaks.

## [1.16.58] - 2026-10-06

- **Dependency: fsspec upgraded to 2026.9.0.** fsspec comes in with PyTorch (used by the EasyOCR text recognition). Versions before 2026.6.0 have a security advisory (GHSA-27vj-qcqg-25rc: `ReferenceFileSystem` can run arbitrary code when it reads a crafted reference file). This system does not use that feature and never lets users choose what fsspec reads, so it cannot be reached here; it is upgraded anyway so scanners stop flagging it. Only this one package changed. `jtdt update` picks it up; the Windows installer is unaffected.

## [1.16.57] - 2026-10-06

- **Windows installer: shows how much disk space it needs, gives Installed apps a size, and the uninstaller no longer leaves files behind.** The "space required" on the components and folder pages used to read 56.0 KB (only the two scripts inside the installer were counted) when a full install takes about 3 GB. It is now worked out from what each component really installs (core about 2.2 GB including the Python packages and download cache, OCR about 560 MB, the Office engine about 650 MB), so a disk that is too small shows up before you click Install; the sizes in the component descriptions now match. The entry under Settings > Apps > Installed apps had no size: the installer now writes one, and after a `jtdt update` the service keeps it current, so existing installs get it without reinstalling. The uninstaller copies itself to the temp folder and runs from there; that copy was never deleted, so every uninstall left one behind (named without the first part of the version, such as `jtdt-uninstall-.16.55.exe`). It now deletes itself when it finishes, and copies left by earlier versions are cleared at the next install or uninstall. The uninstall progress list had an English line (`Running uninstall core ...`) and an English note about where your data was kept; both now follow the interface language. A fresh install no longer prints a `[!] ... not a git repo` warning in `installer.log` (it read as if something had gone wrong); the warning only appears when the install folder really holds old files. The line at the bottom of the window showed the build tool's name ("Nullsoft Install System v3.09-4"); it now shows the product name and version. These changes are inside the installer, so they show up from the 1.16.57 installer on (except the Installed apps size, which appears after an update); one-line installs and `jtdt update` are unaffected.

- **Notifications: "My jobs" and "My workspace" are now clickable.** The job-finished email said you could download the result from "My jobs", but it was plain text: the link needed the admin to fill in the Site URL in the notification settings first, and without it there was no link (the server does not know which address people come in from). Without a Site URL, the link now uses the address your browser was on when you submitted the job, the address you came in through, so clicking it takes you back to My jobs. A Site URL that is filled in still takes priority. The text version sent to Slack, Zulip, Teams and the other channels now includes the address as well. Only http / https addresses are used; anything carrying a user name and password or otherwise malformed is ignored, and with neither available there is no link, as before.

## [1.16.56] - 2026-10-06

- **Windows: Chinese messages no longer vanish from the service log on machines whose system locale is English.** On Windows set to an English language for non-Unicode programs (typical for English Windows Server), every service log line that contained Chinese failed to write: the line was simply lost, and the error log (`jtdt-svc.err.log`) got a `Logging error` stack trace instead, over a hundred of them per start. Tool registration, slow requests, database backups and retention clean-up all went missing, so problems could not be traced. The service now always writes its log in UTF-8, whatever the locale; it takes effect after an update and restart, with no reinstall. `jtdt logs` on Windows now reads the log files as UTF-8, and the log tail printed when `jtdt update` fails no longer turns into garbled text when the read starts in the middle of a Chinese character. The Windows `installer.log` used to tell you to install EasyOCR by hand right after `[OK] EasyOCR available`: that hint had slipped outside its check because of the batch file's parenthesis rules, so it printed whether or not the install worked; it now only appears when EasyOCR really is missing. The timestamps of the Python package step in the same log showed as `[?? 2026/10/06 ...]` on English-locale machines (the weekday could not be written); they now record the time only.

## [1.16.55] - 2026-10-06

- **Windows installer: you can see what it is doing, and the uninstaller's last page now talks about uninstalling.** The wizard now shows each step as it happens (checking the network, downloading the code, installing Python and its packages, downloading OxOffice, registering the service...). The OxOffice download shows how much is done, the speed and the estimated time left; installing the Python packages shows which one is being downloaded. Before, the window showed the same two lines for the whole 10 to 30 minutes. Chinese, Japanese and English text all display correctly: progress now travels through a Unicode status file instead of the system ANSI code page that used to garble child-process output. The output of the Python package step is now also kept in `installer.log`. After uninstalling, the last page used to say the installation was about to finish, the window title said Setup and "Open the web interface" was ticked; it now says the program was uninstalled and where your data was kept (or that it was deleted), without the checkbox and the website link. The "also delete your data?" question is now asked when the uninstall starts (its title bar used to show the setup title), after the install folder has been checked. These changes are inside the installer, so they show up from the 1.16.55 installer on; one-line installs and `jtdt update` are unaffected.

## [1.16.54] - 2026-10-05

- **Stamps and signatures from the asset library now follow the Stamp and sign permission (issue #54).** With sign-in enabled, people without permission to use Stamp and sign no longer see the library's stamps and signatures under the PDF editor's stamp / signature picker (logos are unchanged); the picker explains why, and Upload a new image still works. Previously an ordinary user (who has no stamping permission by default) could pick the company stamp in the editor and put it on a PDF. Saving checks too: a hand-built request naming a stamp asset is refused with 403 and produces no file. The asset image addresses (`/assets/{id}/file` and `/assets/{id}/thumb`) follow the same rule: a stamp needs Stamp and sign or Seam stamp, a signature needs Stamp and sign, a watermark needs Watermark, and a logo only needs sign-in. They used to be open to anyone signed in, so downloading an image and uploading it again got around the permission. The seam stamp tool only takes stamps from the library: a hand-built request naming a signature, watermark or logo is refused (with only seam stamp permission, a signature could previously be stamped across pages). Stamping with a library stamp or signature in the PDF editor is now saved to the Stamp and signature history on a manual save, like the stamp tool, marked as coming from the editor; autosaves are not recorded, and saving again with nothing changed adds no entry. Nothing changes with sign-in turned off (single-user mode).

## [1.16.53] - 2026-10-05

- **Stamp API: saved to the stamp history, and can use a stamp from the asset library.** Stamps applied through the API are now saved to the Stamp and signature history like the web version (the original and the stamped file, which auditors can review), together with the calling account. Previously there was only an audit entry, with no way to see which document was stamped or what the result looked like. An uploaded stamp image is not stored separately; only its fingerprint is recorded. The new `asset_id` parameter names a stamp, signature or logo from the asset library, so no image upload is needed; when no position is given, the stamp goes where that asset is set up in the library. `stamp_image` is now optional (use one of the two), and placements that each name an asset need no upload either. The audit entry's file name is always the stamped PDF (if the caller sent the stamp image first, it used to record the image's name). The existing way of calling (upload an image, no position) gives exactly the same result as before.

## [1.16.52] - 2026-10-04

- **Meeting summary: the "Business report" theme preview cut off the left of the title.** In the export area's layout theme preview, the title's dark blue band reached out past the left edge of the preview frame and the first character was cut off. In the document that band extends to the page edge, but the preview has no page margins; it now stops at the edge of the preview frame. Exported documents are unchanged (the layout is exactly as before). A new check makes sure nothing in any theme preview reaches outside the frame.

## [1.16.51] - 2026-10-04

- **Meeting transcription: misheard-spelling lines are checked with the speech service's own term-splitting rules.** In a "misheard spelling → correct spelling" line, a right-hand side separated by `|`, `｜`, `／` or a `/` with a space on either side now also counts as several terms and is refused before sending, naming the line (`TCP/IP` is still one term). Previously only enumeration commas, commas, semicolons and a `/` with spaces on both sides were caught here; the rest were only rejected by the speech service after sending. The rule that a misheard spelling must not be a term on the list now compares against the split terms: a list line written as `Proxmox VE / PVE` makes `PVE` a listed term too. Several misheard spellings on the left can also be separated with `|`. A one-character correct spelling is refused.

## [1.16.50] - 2026-10-03

- **Meeting summary: the "Events and impact" card heading had no icon.** On the result page and in the exported HTML, that card's heading had no icon while the other four did. All five card headings now show their icons, and a new check stops a future category from shipping without one.

## [1.16.49] - 2026-10-03

- **Meeting transcription: known misheard spellings are replaced as listed (following the speech service's 2026-10-03 update).** In "Terms or meeting background" you can write a line "misheard spelling → correct spelling" (for example `Proksmox, Proxmux → Proxmox`; the arrow can also be `->` or `=>`), and the speech service replaces them before correction. Misheard words spelled very differently, which correction cannot recognise, can be fixed this way. The correct spelling is also sent as a term, and only the correct spelling goes into the meeting background. A malformed line (two terms right of the arrow, a missing side, a misheard spelling that is another term on the list, or one misheard spelling pointing to two terms) is refused before anything is sent, naming the line. Lines with two or more arrows (such as a written-out process) used to be sent whole as one term; like sentences, they now stay in the meeting background. "Add terms and re-run the correction" accepts the same lines, and its box brings back the arrow lines you wrote (on a re-run the speech service replaces its list with the whole new one). The result page says how many places were replaced, or that the misheard spellings were not sent when the speech service is too old to accept them. API: `terms` takes the same syntax; the response adds `variants` and `variants_sent`, and the number of replacements is in `summary.correction.variant_replacements`.

## [1.16.48] - 2026-10-03

- **Layout themes: six more colour schemes, and the theme list uses this system's own dropdown.** The layout themes shared by the meeting-summary export and Markdown to Office go from 6 to 12: Teal, Indigo modern, Forest green, Coral, Navy and gold, and Magazine. Each was checked in PDF, Word and ODF; themes with a dark table header keep white text on the dark header after conversion to Word or ODF. In the meeting summary, the theme picker is now this system's own dropdown instead of the browser's: each theme shows three colour swatches (headings, accent, table header) and a one-line description. The dropdown has a fixed width, so the "Include the full transcript" box next to it no longer shifts when the theme changes. The theme cards in Markdown to Office show the same swatches.
- **Meeting transcription: heading lines and dates are no longer sent as terms.** In "Terms or meeting background", lines starting with `#` (such as a date heading pasted from notes) and pieces with no letters at all (dates, times, plain numbers) stay in the meeting background and are not sent as terms.

## [1.16.47] - 2026-10-03

- **Meeting transcription: terms are used only in correction (following the speech service's 2026-10-03 update).** Words in "Terms or meeting background" are now used in correction: correct spellings are kept, and misheard spellings that are close are changed to yours; words misheard as something spelled very differently may not be fixed. One term per line works best. Recognition does not use the list: the speech service measured that listed words that do not occur in a meeting get inserted into the transcript anyway, in places that look normal. The hint under the field used to say recognition prefers these words; that is corrected. The result page used to say recognition used "the first 0" terms (the service now reports 0 truthfully); it now says the terms were used only in correction. The transcript JSON also records the recognition model (name, where it ran, device); it is not shown on screen.

## [1.16.46] - 2026-10-03

- **Meeting transcription: the "Terms or meeting background" label ran into its text box in the Chinese interface.** Chinese field labels have a fixed width sized for four to six characters and do not wrap; this one has nine, so the text overlapped the box on its right. The label can now wrap onto two balanced lines next to the multi-line box. The English and Japanese labels already wrapped and are unchanged. A new browser check measures every field label on every tool page so that a longer label cannot run into the control next to it again.

## [1.16.45] - 2026-10-03

- **Meeting summary: add your own replacements.** For words that recognition got wrong and the suggested term fixes do not catch (for example `Groxmoxity` for `Proxmox`, `POWPOYNT` for `PowerPoint`), type "as written → change to" under the meeting background and click Add. The whole transcript is searched first: English whole words, ignoring case (typing `groxmoxity` also finds `Groxmoxity`), with every spelling actually found and how many times; if nothing is found it says so and nothing is added. Ticked rows are applied before the analysis together with the suggestions, the original is kept (replaced segments have a dotted underline; hover to see the original), and the result page and exports list what was replaced. When the same spelling is both suggested and added by you, yours wins. Opening the job again from My jobs brings your rows back in the order you added them, still ticked. The note now reads "These spellings were replaced in the transcript" (your own rows do not necessarily come from the background). Why the mistake happens: it is the speech recognition mishearing; "Terms" only biases recognition and protects correct spellings from correction, it does not fix misheard ones. We have asked the speech service whether correction can use the term list for near misses; until then this is the reliable fix.

## [1.16.44] - 2026-10-03

- **Meeting transcription: long WAV recordings had only a flat line instead of a waveform.** An hour of WAV is over 300 MB; the browser only decoded files under 60 MB, so larger ones showed a plain timeline with no explanation. WAV waveforms are now read on the server (any size, about half a second for an hour, cached), so the browser does not download the whole file. Large files in other formats still get no waveform, but the page now says why; playback and click-to-seek work as before.
- **Meeting transcription: "Terms" is now "Terms or meeting background", and it goes into Meeting summary's background.** Lines with one term each are sent for recognition as before; lines written as sentences (with a full stop or question mark, or very long) are not, because recognition only uses the first few terms and sentences would push real names out. For "Attendees: Wang Xiaoming, Bianca" the label is dropped and the names are sent; a line that is only a label is not sent. When you send the transcript to Meeting summary, the whole text fills the meeting background (unless the box already has text or this transcript had a background last time); you can edit it or click "Remove". A very long line used to be refused; it is now treated as background.
- **Meeting summary: the export buttons line up.** They form a table: buttons in the same column have the same width and line up across both the "Download" and "Save to workspace" cards; the card descriptions fit on one line and the note about re-opening the JSON moved below the buttons. The heading of the suggested term fixes now says the whole transcript is checked (only the first few lines are listed for you to check speakers and line breaks).

## [1.16.43] - 2026-10-03

- **Meeting summary: the background you entered last time comes back for the same transcript.** Each analysis kept its meeting background only in that analysis's result, so uploading the same transcript again started with an empty box. The upload now recognises a transcript you have analysed before, fills in the background you used last time and says when it was from; you can edit it, or click "Remove" so it is not filled in again. If the box already holds other text it is left alone and you get "Use the earlier one" instead. Analysing with an empty box also stops it being filled in next time. "The same transcript" means the same spoken words: speaker names, times and how segments are split do not count, so the JSON sent over from meeting transcription, the plain text saved to the workspace and a copy saved after renaming speakers are all recognised. Each person keeps their own; someone else uploading the same transcript does not get yours, and it is deleted with the account. Up to 200 per person, oldest dropped first.
- **Meeting summary: the result in My jobs and the copy saved to the workspace are now the full version.** When an analysis finishes after you have left the page, the result is saved to the workspace automatically; that copy and the My jobs download used to leave out the transcript, so they opened but citations could not jump to the original text. They are now the same file as "Download JSON" (with the whole transcript and the meeting background); after you rename speakers, the My jobs download has the new names (a copy already saved to the workspace does not change).

## [1.16.42] - 2026-10-03

- **Meeting summary: a previous result loaded from the workspace was read as a transcript.** When an analysis finishes after you have left the page, the result is saved to the workspace as `…-會議摘要.txt` (the workspace used to take only `.txt` / `.md` text names; it now keeps `.json` too, see below). Picking it with "Load from workspace" used to parse it as a transcript, one line of JSON per segment, because only `.json` names were recognised as an exported result. The content is checked now: an exported result opens as the result, with its meeting background. The auto-saved copy has no transcript attached, so citations cannot jump to the original text; the copy from "Download JSON" or "Save to workspace" can.
- **Load from workspace: the picker shows when each file was saved.** Two files with the same name (for example two transcripts of the same meeting) could not be told apart; each card now shows its save time, the same format as on My workspace. This applies to every tool.
- **The workspace keeps JSON files.** A transcript JSON or an exported meeting-summary JSON saved to the workspace used to be renamed to `.txt` while its content was JSON. A file whose name ends in `.json` and whose content really is JSON now keeps its `.json` name. Files already stored as `.txt` are not renamed, and the meeting summary still reads them correctly.
- **Meeting transcription: speaker chips.** The name and the segment count are two buttons now: click the name to rename that speaker (all of their segments), click "N segments" to jump to where that speaker first talks (the row is highlighted, and if the recording is still there the player moves to that point without starting playback).
- **Meeting transcription: "Maybe Wendy" on speakers who introduced themselves.** When an unnamed speaker says something like "Hi, I'm Wendy" or "This is Tom from Contoso" (or the Chinese and Japanese equivalents), their chip offers "Maybe Wendy"; one click applies and saves the name, and hovering shows which segment it came from. It is only a suggestion, never applied by itself: getting it wrong would put one person's words under someone else's name, so ordinary phrases are not taken for names.
- **Meeting transcription: save to the workspace again after renaming.** After one save the button stayed on "Saved to workspace"; renaming a speaker now re-enables it (each save is a new copy). The plain text that is saved or copied now uses the new names instead of S1 and S2.
- **Meeting transcription: the result file records the recognition profile.** The downloaded transcript JSON has a `profile` (id and version, e.g. `meeting.balanced` / `2026.09.1`). The correction model (`summary.correction.model`) and the diarization method (`diarization.engine`) were already recorded; the speech service does not report the name of its speech recognition model.
- **Meeting summary: the export area has two cards, Download and Save to workspace.** Formats are rows inside each card, and every button carries the same icon as its card's title. The two cards sit side by side on wide screens.

## [1.16.41] - 2026-10-02

### Meeting transcription: add terms after the fact and re-run only the correction

- If a name or term turns out to be wrong, "Add terms and re-run the correction" on the result page sends the terms to the speech service (JTLW), which re-runs only the correction, not the recognition. Times, speakers and renamed speakers stay as they are; only the text is replaced by the new correction. Words that recognition misheard completely and that correction cannot recognise still need a new submission.
- To make that possible the speech service now **keeps its copy for up to 24 hours** after the transcript is saved, then is asked to delete it; "No more changes" deletes it at once. The transcript here is not affected. Administrators can shorten the window on the "Speech service (JTLW)" settings page (0 = delete as soon as it is saved, no re-runs), and the new window also applies to transcripts already waiting. Nothing is kept when the recognition mode has no correction step.
- When a re-run is not possible the page says why and what to do (already deleted, less than 15 minutes left, already re-running, no correction this time).
- Public API: new `/tools/meeting-transcribe/retry` and `/tools/meeting-transcribe/done`; the synchronous API response gains `upload_id` and `retry_until`.

### Server error details follow the interface language

- Error details returned by the server were always shown as sent; those that are in the language catalogue are now shown translated in the English and Japanese interfaces.

## [1.16.40] - 2026-10-02

### Meeting summary: a transcript handed over from transcription lost its speakers and times when loaded from the workspace

- "Send to meeting summary" in meeting transcription passes the whole transcript (JSON) through the workspace, and the workspace only keeps `.txt` / `.md` names for text, so it is stored as `…-逐字稿.txt`. Loading that file from the workspace later made the meeting summary split it as plain text: one line of JSON per segment, 0 speakers, no times.
- Transcripts are now recognised by their content: a `.txt` / `.md` file whose content is JSON is read as JSON, so speakers (including renamed ones) and times come back. Files already in the workspace work without being regenerated. Plain text such as `[00:12] Speaker: …` is still read as plain text.

### Meeting summary: the export area is grouped by format

- The export area used to have one row per action (download / save to workspace / other formats), and "other formats" did not say whether it downloaded or saved. It is now three groups, documents (PDF, Word, ODF), web page and plain text (HTML, Markdown) and charts and data (charts PNG, Markdown with images, JSON), and each group says "Download" or "Save to workspace". Download buttons all carry the download icon and save buttons the archive icon.
- The cards fill the width on the right; when it is narrow the group name moves above the buttons, and on wide screens the three groups sit side by side.

## [1.16.39] - 2026-10-02

### Meeting transcription: proper nouns can be given up front

- The options have a new "Proper nouns" box: attendee names, company and product names, jargon, one per line. They are sent to the speech service (JTLW) as a glossary: recognition uses the first few as hints and correction keeps all of them exactly as written, so put the most important ones first. The result page says how many were sent and how many were used for recognition.
- Up to 500 terms of up to 200 characters each; going over names the offending term instead of cutting anything off silently.
- The public API takes a new `terms` parameter; the response gains `terms` and `glossary`.

### Meeting transcription: a reminder when all 8 slots of the new method are used

- When speakers are separated with Nemotron and all 8 slots are used, the result page says to fill in the number of speakers and resend if there are more. Without it, the extra people are silently merged into someone else. Needs speech service interface version 2.7 or later; the response gains `diarize_saturated`.

### Meeting summary: fixing proper nouns in the transcript from the meeting background

- When the meeting background has the right spelling (for example `Bianca`) and the transcript has it wrong (`Bianka`, or a Chinese name written with a same-sounding character), the places that are probably misspelled are listed under the background box with a count and an example. **They are only suggestions and start unticked**; the ticked ones are replaced before the analysis starts.
- Replaced segments are underlined with dots in the transcript and show the original text on hover; the result page and every export say which spellings were replaced. Unticking and analysing again brings the original back.
- The matching rules are fixed and use no AI: in English, one or two letters off, or an extra space or hyphen in the middle; in Chinese, three to eight characters where every character has a matching pronunciation. Differences only in case, singular and plural forms, two-character words and spellings the background itself uses are never suggested.
- Public API: new `/tools/meeting-summary/api/term-suggestions` (suggests only, stores nothing); `/tools/meeting-summary/api/meeting-summary` takes a new `replacements` parameter.

### Dependencies

- New: `pypinyin` (MIT), used to compare Chinese pronunciations. `jtdt update` installs it automatically.

## [1.16.38] - 2026-10-02

### Meeting summary: "Start the analysis" was missing when opened from My jobs

- Opening a finished analysis from My jobs (or from a notification) loaded only the result; the parse section above it, including "Start the analysis", stayed hidden, so changing the meeting background and running again meant uploading the transcript again. The parse section is now shown and the meeting background is restored into its box.

### Meeting summary: an exported .json can be loaded back

- The JSON download now includes the full transcript and a format marker. Upload it back to the meeting summary and the result appears directly, without new model requests; citations still jump to the transcript, and "Start the analysis" is available to run it again.
- A .json exported by an earlier version (without the transcript) also opens, but citations have no transcript to jump to.
- The uploaded content is rebuilt rather than stored as-is: only known fields are kept, strings are length-limited and malformed entries are dropped. A file that cannot be read returns a clear error instead of being treated as a transcript and sent for analysis.

### Meeting summary: exported documents and .json carry the meeting background

- When a meeting background was entered, the exported PDF / Word / ODF / Markdown / HTML has a "Meeting background" section right after the title and before the summary, copied verbatim (line breaks kept, not read as formatting); it is left out when there is none. Files saved to the workspace get it too.
- The result page shows the same section.
- The exported .json already carried the background; loading it back puts the background back in its box.
- The public API (`/tools/meeting-summary/api/meeting-summary`) result has a `context` field; it is absent when no background was sent.
- The response example in the API reference now uses the real field names (`summary.text`, `items.decisions`, `items.actions[].due_text` and so on). The old example used outdated names, so code written against it found nothing.

### Meeting summary: export area laid out again

- On the left, an A4-proportioned colour preview (a thumbnail of one page instead of a small scrolling window); on the right, one row per task: layout theme, download, save to workspace and other formats, with aligned labels and one button style.
- The save-to-workspace buttons keep their labels after saving (each used to turn into "Saved to the workspace", so the four could no longer be told apart).
- The analysis-result card has a gap above it again (it touched the card above while the progress bar was hidden).

## [1.16.37] - 2026-10-02

### Meeting summary: exported minutes reworked — full transcript included, tidy tables and charts

- **PDF / Word / ODF / Markdown exports now include the full transcript** as a table (segment / time / speaker / text), so every "segment N" cited by a decision or action can be checked in the same document. Untick "Include the full transcript" to leave it out.
- Tables are laid out again: full page width, horizontal rules only, centred headers, text aligned to the top. The speaking statistics table follows the same style.
- In `.odt` the table header was dark text on a dark background; it is now white.
- Each chart sits under its own section (topic timeline, share of time per topic, speaking statistics, discussion structure). The heading is in the document, so the chart no longer repeats it.
- The topic timeline chart was missing from exports; it is now included.
- "Share of time per topic" is sorted from largest to smallest: the list top to bottom and the bar left to right, on the page and in exports. Colours still follow the topic, so they match the topic timeline.
- Charts in Word / ODF were placed at full size and ran off the right edge of the page; they now fit the page width.
- English words inside charts are no longer broken in half.
- **New HTML export**: saves the page as shown (cards, charts, transcript). It opens without a connection to the server, and citations still jump to the transcript.
- Exported files can be **saved to the workspace** (PDF / Word / ODF / Markdown).
- The layout theme now has a **colour preview**. The default is back to "Clean" (the option labelled default was not the one selected), and the theme you choose is remembered in this browser.

### Meeting summary: English names in the summary wrongly flagged as unsupported

- When an action's text ended in English and its owner had an English name, the two were joined without a space before checking, so both names were flagged as having no source. Each part is now checked separately.

### Meeting transcription: rename a speaker by clicking it

- Under "N segments, N speakers" the results page lists each speaker with their segment count; click one to rename that speaker in every segment, and the change is saved.
- The correction level is shown in plain words instead of the speech service's code.

## [1.16.36] - 2026-10-02

### Meeting summary: "Meeting background (optional)" now reads as one group

- The heading, explanation and input box sat loose inside the "1. Upload the transcript" card, separated only by a rule,
  so they did not look like one unit. The section now has a light background and a coloured bar on the left, and the
  explanation and input box line up with the heading text; collapsed, the single heading line still looks like one section.
- There is deliberately no four-sided border or shadow, which would make it look like a separate card inside the card.
- On phone-width screens nothing is indented and the input box uses the full width.

## [1.16.35] - 2026-10-02

### Meeting transcription: the more-than-8-speakers wording now matches what the speech service does

- The speech service changed what happens when the new method fills all 8 speaker slots: it used to always fall back to the
  original method; now it re-runs the original method and falls back only if that finds more than 8 speakers, otherwise
  it keeps the new method (at most 8 speakers).
- The page used to say meetings with more than 8 speakers automatically fall back to the original method, which no longer
  always holds when no number is entered. It now says the new method separates at most 8 speakers and asks you to enter
  the number when you are sure there are more than 8; entering a number above 8 always uses the original method.
- The result page's fallback note now says "more than 8" instead of "8 or more"; the API manual describes the
  specified and unspecified cases separately.

## [1.16.34] - 2026-10-02

### When an upload is rejected as too large, the message says which limit did it

- Previously a too-large upload only said "The file is too large (413)", with no way to tell whether the reverse
  proxy in front of the site or this system's own limit rejected it. The message now distinguishes three cases:
  - **The reverse proxy**: says it is not this system's limit and asks an administrator to raise the proxy's upload
    limit (`client_max_body_size` in nginx); the current limit can be measured under "System status → How large an
    upload can be".
  - **This system's per-upload limit**: states the limit in MB and where an administrator changes it.
  - **A feature's own limit**: says it is that feature's limit and includes its explanation.
- Batch watermark uploads always blamed the reverse proxy (even when this system's limit applied) and now use the
  same logic; the PDF editor's separate copy of the error-message handling now uses the shared one.
- Every 413 this system returns carries an `x-jtdt-limit` header (`site` / `tool`), so API callers can tell too.

## [1.16.33] - 2026-10-02

### Security: PyJWT upgraded to 2.15.1 (OIDC single sign-on)

- PyJWT 2.13.0 and earlier have 12 public advisories (1 critical, 5 high) affecting how OIDC sign-in verifies
  identity tokens: HMAC/public-key confusion, following redirects when fetching the key set, fetching the key set
  repeatedly before verification, and malformed tokens crashing the parser. Fixed in 2.14 / 2.15; the minimum is
  now 2.15.1.
- This system already accepts only asymmetric signing algorithms, so tokens can't be downgraded to HMAC, but the
  upgrade is applied anyway. Installations without OIDC sign-in are not affected.
- Existing installations pick it up with `jtdt update`.

## [1.16.32] - 2026-10-01

### Meeting transcription: fallback reasons in your language; a momentary version lookup failure no longer drops back silently

- When Nemotron was requested but the speech service used the original method, the result page now explains why
  based on the speech service's reason code (interface version 2.6 and later), in English and Japanese too.
  Unknown codes show a general explanation plus the speech service's own wording.
- Looking up the speech service version before submitting can be slow or fail while its back end has trouble.
  Previously that moment was treated as an old version, so those jobs silently used the original method; now the
  last version read is reused. The page's lookup now waits up to 10 seconds instead of 5.
- API: responses add `diarize_fallback` (reason code, explanation sentence and the speech service's wording).

## [1.16.31] - 2026-10-01

### Meeting transcription: speakers are now told apart with NVIDIA Nemotron

- When the speech service (JTLW) is on interface version 2.5 or later, jobs ask for NVIDIA Nemotron speaker
  separation. On the same real recognition output with no speaker count given, the speech service measured
  speaker errors dropping from 18.52% to 2.92% on 20 Chinese meetings (correct speaker count 2/20 → 18/20)
  and from 12.31% to 4.65% on 16 English meetings.
- **The speaker-count field now follows the method.** Nemotron treats the number as an upper limit: too high
  has no effect, too low merges different people. The field is now "Maximum number of speakers" and says to
  err on the high side. The original method splits into exactly that many groups, which is the opposite advice;
  older speech services (2.4 and earlier, or when the version can't be read) keep the original method and wording.
- With more than 8 speakers the speech service falls back to the original method; the result page says so and
  shows the reason.
- **Re-transcribing the same recording may give different speaker labels and speaking statistics than before.**
  Existing transcripts are unchanged.
- API: responses add `speaker_engine` (the method requested) and `diarization` (what the speech service actually
  used); the API manual explains what `num_speakers` means under each method.

## [1.16.30] - 2026-09-30

### LLM: a model's "thinking" is turned off through gateways such as LiteLLM too (translation back from 400 minutes to normal)

* With an LLM gateway such as LiteLLM in front of Ollama, models that think before answering (gemma4 and others)
  **were not having their thinking turned off**, so every request first produced thousands of characters of
  reasoning; translating a 415 KB document took 400 minutes. Since v1.16.17 the thinking-off parameters were only
  sent when Ollama could be detected directly, and it cannot be detected through a gateway.
* Now, whatever server or gateway is in front (Ollama, LiteLLM, vLLM, SGLang, llama.cpp server, LM Studio or an
  OpenAI-compatible cloud service), **the thinking-off parameters are sent every time**. If the server rejects one,
  it is removed and the request resent, and that server is remembered so it is not retried. All 13 LLM tools go
  through the same code, so they are all covered.
* "Test connection" on the LLM settings page now also checks whether the selected model **thinks before
  answering**, and if so says so and where to turn it off. If a tool finds the model still thinking, a warning is
  written to the service log.
* Tested against a real LiteLLM 1.103 in front of gemma4: both the `ollama/` and `ollama_chat/` prefixes turn
  thinking off (a translation request went from 53.8 / 28.7 s to 2.4 s), so **nothing extra needs configuring in
  LiteLLM**. LLM.md now covers each kind of server and gateway.

### LLM: the translation concurrency setting says how many requests are really sent at once

* The number of requests actually sent at once is also limited by "Job queue → Concurrent external service calls"
  (default 1), and the smaller of the two applies, so without changing it translation sends one request at a time.
  The settings page now shows the current value and warns when translation concurrency is higher; the job queue page
  has the same note.

## [1.16.29] - 2026-09-29

### Speech service settings: deprecated recognition modes are no longer listed

* The speech service marked one recognition mode as deprecated (it actually processed audio the same way as the
  balanced mode), and the settings page no longer lists deprecated modes in the recognition-mode dropdown.
* Exception: **if the saved setting is the deprecated mode**, it stays in the dropdown with a note saying which mode
  to use instead. Hiding it would silently switch the dropdown to another option, and saving would change the
  setting without anyone noticing.

## [1.16.28] - 2026-09-29

### Security: two code-scanning alerts fixed

* Document to images: finding each page's file on download now uses a fixed pattern instead of building a regular
  expression from the upload ID in the URL. The ID was already validated first, so this was not exploitable; the
  code is simply written the right way now.
* OCR language packs: line breaks are stripped before a failed quality switch is written to the log.
* Saving the speech-service settings page did not write an audit record (every other settings page does). It now
  does, so you can see who changed the recognition mode, addresses and so on, and when. The key and certificate
  themselves are never written to the record, only whether they changed.

### Documentation

* The deployment security notes were rewritten. They used to stress that the file-parsing components are a
  high-risk surface, which read as if even the internal network were dangerous. The real point is **who can reach
  it**: on the public internet anyone can try to sign in or upload files, while on the internal network only
  colleagues can, after signing in. README, INSTALL.md, OPS.md and the website were updated together.
* The website's "No cloud" section was redesigned: six feature cards and a single deployment note.
* The offline installation guide's wording for bringing a Docker image into the internal network was corrected.

## [1.16.27] - 2026-09-29

### Meeting summary: more formal section and chart titles

* The speaker section now has a formal title, "Speaker statistics" (on screen and in exported files); the
  on-screen topic chart is titled "Share of time per topic" (or "Share of segments per topic" without timestamps).
* The titles drawn into the exported charts (which are in Chinese) were changed to the formal terms as well.
* Fixed: when a transcript had no timestamps, the notice said the speaker share and the topic timeline would not
  appear. Both still appear; the speaker statistics simply have no speaking time, and topic length is counted in
  segments. The notice now says so.

## [1.16.26] - 2026-09-28

### Document to images: WebP / JPEG output and a fixed width (issue #53)

* New output formats **WebP** and **JPEG** (it was PNG only). WebP suits web pages: for a 16:9 slide at 1920 px wide,
  a page is about 479 KB as PNG and about 122 KB as WebP. WebP and JPEG have a quality setting (high 90, standard 80,
  small 65).
* New **fixed width** option: every page is scaled to the same width (for example 1920 or 480 px for a website) and
  the height follows the page's proportions, so there is no need to work out which DPI gives that width. All three
  formats can use it.
* **Fixed: choosing 200 DPI or more had no effect.** The conversion borrowed the preview renderer, which caps the
  longest side at 1800 px, so an A4 page came out the same at 200, 300 and 400 DPI, and a 16:9 slide could never reach
  1920 px wide. The chosen DPI is now used, **so files at 200 DPI and above are larger than before** (that is the size
  the option always promised).
* A page over 40 million pixels (or wider or taller than 16383 px in WebP) is output smaller and marked "Reduced"
  under its thumbnail, instead of quietly coming out smaller than requested.
* API: `/tools/pdf-to-image/convert` has three new parameters, `format`, `width` and `quality`; without them the
  output is PNG as before.

### API: tool paths called with a token were redirected to the sign-in page

* Several tool paths that the API manual shows being called with `Authorization: Bearer`
  (`/tools/pdf-to-image/download/…`, `/tools/doc-diff/page-image/…`, `/tools/translate-doc/start` and `/job/…`,
  `/tools/office-convert/formats`) never checked the token on an instance with sign-in enabled; they redirected to
  the sign-in page, so a script got an HTML page back. A Bearer token on a tool path is now verified; requests without
  one (the browser) are unchanged, and the token owner's tool permissions still apply.

## [1.16.25] - 2026-09-27

### Meeting transcription: a brief loss of contact with the speech service no longer fails the job

* While waiting for JTLW to finish, a single failed status check (the speech service restarting for a few seconds,
  or the network dropping briefly) marked the whole job as failed, even though the speech service was still working
  on it, so a long meeting had to be transcribed again.
* Status checks and transcript downloads now retry on connection errors, timeouts and temporary server responses
  (429 / 502 / 503 / 504), and the screen says that JTLW is temporarily unreachable and when it will retry. The job
  gives up only after **5 minutes without contact**, and says why. Other errors (for example the speech service
  reporting that it cannot find the job) are still shown at once, without retrying.
* Stopping the job still works while it is waiting to reconnect.

## [1.16.24] - 2026-09-25

### Windows: repairing the VC++ runtime no longer needs a restart

* v1.16.23 repaired a downgraded VC++ runtime through Microsoft's vc_redist installer, but once that installer
  has reported "restart required" during a boot it refuses to do anything else, so the install could only ask
  you to restart and run `jtdt update`. The runtime packages already installed on the system are now repaired
  directly: no restart and no download. vc_redist is only used if that is not enough. Same for the installer,
  the command-line installer and `jtdt update`.

## [1.16.23] - 2026-09-25

### Windows: after installing OxOffice, Chinese OCR got much worse (EasyOCR could not load)

* The OxOffice 11.0.5 installer replaces the system's Visual C++ runtime with the older copy it bundles (14.29).
  EasyOCR needs 14.40 or later, so OCR fell back to Tesseract (much weaker on Chinese) and the page only said
  that EasyOCR failed and Tesseract was used. The old check only read the registry (which still said the new
  version), so it reported "already current" and repaired nothing. Machines where the v1.16.20 to v1.16.22
  installers put OxOffice, and machines with OxOffice 11.0.5 installed separately, are affected.
* The installer and `jtdt update` now check the actual file versions in System32 and repair a downgraded runtime
  automatically (no restart needed). If a repair already ran during the same boot, they ask you to restart
  Windows and run `jtdt update` again.
* The dependency page flags this on the EasyOCR row.
* `jtdt update` now tells "EasyOCR not installed" apart from "installed but cannot load" (it always said the former).
* Already affected: run `jtdt update` once as administrator.

## [1.16.22] - 2026-09-25

### Windows installer: OxOffice failed on slow networks, and a successful install counted as a failure

* The OxOffice installer is about 400 MB and hosted on GitHub. On some networks the download is slow and can
  end incomplete without any error; installing it then failed with 1625 ("forbidden by system policy"), which
  looks like a permissions problem but was a damaged file. The download is now checked against the expected
  size and its digital signature and retried (up to three times) before falling back to LibreOffice.
* msiexec returning 3010 ("installed, restart suggested") was treated as a failure, so LibreOffice was installed
  on top. It now counts as success (conversion does not need a restart).
* The OxOffice install log is written to `%ProgramData%\jt-doc-tools\Logs\oxoffice-msi.log`.
* These need the v1.16.22 installer; the command-line installer (`install.ps1`) is fixed as well.

### OCR: two jobs running for the first time corrupted the downloaded model

* The first OCR run downloads the recognition models (about 300 MB). If a second job started meanwhile
  (clicking again after waiting too long, or dropping two files at once), both downloaded into the same
  temporary file and overwrote each other; unpacking then failed with `Bad CRC-32`, the download was wasted,
  and that job silently fell back to Tesseract (much weaker on Chinese). Now only one job downloads at a time;
  the others wait and reuse the result.
* When the model download failed, every page tried to download it again. Now it waits 10 minutes before
  retrying (Tesseract is used in the meantime) and then tries again on its own.
* OCR jobs submitted through the API now also say which engine was actually used and whether it fell back
  to Tesseract (only the web page did before).
* Service log: every finished job added about 50 `Registered tool` lines, pushing real errors out of view.
  They are now logged once at startup.

## [1.16.21] - 2026-09-24

### Windows installer: an upgrade keeps your "LAN access" setting

* After the v1.16.20 installer made "LAN access" unticked by default, re-running it to upgrade a machine
  that had LAN access switched it to local-only, so a machine shared by a whole team suddenly stopped
  accepting connections from colleagues. An upgrade now reads the current service settings first and
  ticks the option if it was on (visible on the components page, and you can untick it).
* An upgrade also keeps the existing listening address (for example a single network card) and a custom
  port; before, an upgrade reset the port to 8765.
* Fresh installs still leave it unticked.
* If the v1.16.20 installer already switched your machine to local-only, run the v1.16.21 installer again
  and tick "LAN access" (your data is kept).

## [1.16.20] - 2026-09-24

### Windows: fixes found by running every tool after a fresh install

A fresh install with the Windows installer, then each tool actually used:

* **PDF to office document was slow and its "after" preview was blank.** The preview built its own Office
  profile path, which is not a valid URL on Windows, so every conversion waited for a 180-second timeout:
  three minutes for a one-page PDF. It now uses the shared conversion path and finishes in seconds.
* **The `jtdt-reform` engine could not produce Word files on Windows.** The same cause made its ODT-to-Word
  step time out, and the ODT it fell back to was named `.docx` while the screen said "done", so Word reported
  the file as corrupt. Conversion now works; if it ever does fall back, the file is named `.odt` and the
  message says so.
* **Text extraction's ODT output** could hang on Windows; it now uses the shared conversion path too.
* **Checking the Office version left processes behind.** On Windows the program started to report the
  OxOffice / LibreOffice version never exited, so each check left one more. The version is now read from
  the program file itself.
* **The first OCR run** downloads the recognition models (about 300 MB) while the screen only said
  "Preparing"; on a slow network it looked frozen. It now says it is downloading, and that this happens once.
* Settings -> Apps now shows the installed version rather than the installer's build version.
* The full output of the dependency download during installation is now written to
  `%ProgramData%\jt-doc-tools\Logs\setup-python-sync.log`, so a failed install can be diagnosed.
* The command-line installer (`install.ps1`) never matched the OxOffice installer file and installed
  LibreOffice instead; fixed.

### Windows installer (the v1.16.20 build): an occasional crash at the last step; LAN access now off by default

* About one install in four crashed after everything had been installed: the service was running, but
  Settings -> Apps had no entry and the Start menu had no shortcuts. The NSIS component the installer
  uses corrupts memory when a program writes a lot of output (a known NSIS issue whose fix is not yet
  released). The installer now runs its core without capturing output; the full log is still written to
  `%ProgramData%\jt-doc-tools\Logs\installer.log`. **This fix and the OxOffice auto-install fix live inside
  the installer, so they need the v1.16.20 installer or later**; if an older installer crashes, the service
  still works at `http://127.0.0.1:8765/`.
* "LAN access" is now **unticked by default**. Ticked, it lets other computers on the same network connect,
  and single-computer use has no sign-in. To share with your whole team, tick it during installation; for an
  existing install, run the installer again and tick it (your data is kept).

### API manual: three examples that returned the wrong thing

* The page-number total is `{N}`; the manual said `{total}`, which printed the literal text on every page.
  Both are now accepted.
* OCR returns a job id (it runs in the background), not a PDF.
* PDF to images takes two steps: convert, then download with the returned `upload_id`.

## [1.16.19] - 2026-09-24

### Meeting summary: the layout theme sits above the download buttons; exported tables read better

* The "Layout theme" menu in the meeting summary result was the last item of the download-button row, so with
  several buttons it wrapped onto the next line below them. It now has its own line above the buttons: pick the
  layout, then download.
* In exported PDF, Word and ODF files the table header colour only covered the text rather than the whole cell,
  and the borders were heavy. The header now fills the cell, borders use the theme's light colour, body rows
  get a light alternating background, and rows are no longer overly tall. "Markdown to office document" uses
  the same layout and benefits too.

## [1.16.18] - 2026-09-24

### The left menu's scrollbar can be dragged

When the left menu is taller than the window, a scrollbar appears on its right edge, but it was only
drawn there: pressing it and dragging did nothing and started selecting the text underneath instead,
highlighting the whole menu.

* The scrollbar can now be held and dragged up and down; the distance dragged is converted into how far the
  menu actually scrolls.
* The area that can be pressed is a little wider than the visible bar, so it is easier to hit; the bar gets
  brighter on hover and while dragging.
* Dragging no longer selects the menu text.

## [1.16.17] - 2026-09-24

### LLM: services other than Ollama work, and the API key is stored encrypted

The LLM add-ons were written with Ollama in mind. With other OpenAI-compatible services (vLLM, LM Studio,
cloud services) several things went wrong, two of them in ways that were hard to notice:

* **The per-field LLM check in form auto-fill only called Ollama's own endpoint and sent no API key.**
  With other services no field could be asked, and "could not ask" was treated as "no problem", so the
  result said the whole form was filled correctly. Services other than Ollama now use the standard
  OpenAI-compatible endpoint with the configured key; when no answer at all comes back an error is shown,
  and when some fields could not be asked the result says how many were not checked.
* **Every request carried Ollama-only fields** (`think`, `reasoning_effort` and others used to switch off a
  model's thinking). Services that reject unknown parameters turned every request away. The tool now checks
  whether the service is Ollama first, and only Ollama gets those fields. Models that think may answer a
  little more slowly on other services.
* Streaming responses are read even when there is no space after `data:`. An error sent in the middle of a
  stream now fails that call and the reason goes to the service log, instead of becoming an empty answer.
  The body of 4xx / 5xx responses is logged as well.
* **The LLM API key is stored encrypted**, like the SSO, notification and speech-service secrets: neither the
  settings page nor the API shows the key itself, and only the service account can read the settings file.
  A plaintext key saved by an earlier version is encrypted the first time it is read. Settings backups
  re-key it on import to the new machine.
* "Test connection" only sends the saved key to the address it was saved for.

## [1.16.16] - 2026-09-24

### Meeting transcription: the Taiwanese recognition mode works

The speech service's Taiwanese transcription mode does not separate speakers, but every job asked for
speaker separation. Once an administrator switched the recognition mode to Taiwanese, **every** job was
turned away.

* Before sending a job, the tool checks the list of recognition modes the speech service provides and
  only asks for what the chosen mode can do. The Taiwanese mode no longer gets a speaker-separation
  request, and the number of speakers is not sent.
* The result page says that the recognition mode used this time does not separate speakers, so a
  transcript without speakers doesn't look like a failed separation.
* The settings page marks a recognition mode that does not separate speakers.
* If the list of recognition modes can't be read, the job is sent with the configured steps rather than
  guessing. If it is still turned away, the message explains that the mode can't do one of the steps and
  suggests sending again or picking another mode.
* The API response has a new `diarize_skipped` field.

## [1.16.15] - 2026-09-24

### Meeting transcription: Korean can be chosen as the language

The speech service (JTLW) now supports Korean, so the language menu of the transcription tool has a
"Korean" option. With "Detect automatically", Korean recordings were already recognised; now the
language can be set directly.

* Through the API, `language` accepts `ko`; a regional form such as `ko-KR` is reduced to `ko` before
  it is sent (the same as `en-US` and `ja-JP`).
* If an administrator switches the processing profile to the Taiwanese Hokkien one, that profile does
  not support Korean; the job is turned away at submission with a message asking to choose "Detect
  automatically" or another language.
* Automatic detection still listens only to roughly the first 30 seconds of speech. When the whole
  meeting is known to be in Korean, choose "Korean" directly.

## [1.16.14] - 2026-09-23

### Document translation and PDF compression: one download button when the job finishes

When these two tools finished, the progress bar at the top showed a download button and the result
area below had another one. Both gave the same file with the same name, which made them look like two
different things.

* The one in the result area is gone; downloading always uses the row at the top ("Download / Save to
  workspace / Process another file"), the same place as in every other tool.
* Document translation keeps the "Download the translated file (N pages)" button where the preview
  stops. It only appears when the document has more pages than the preview, so that "the preview shows
  the first pages" is not mistaken for "only the first pages were translated".
* An automatic check now requires any tool that uses the shared progress bar and adds its own download
  button to hide the shared one, so new tools do not end up with two.

## [1.16.13] - 2026-09-23

### The speech service's short name is now written in capitals: `JTLW`

The abbreviation of jt-live-whisper was shown in lower case on screens and in the documentation, which
next to `JTDT` looked like two different kinds of thing. The **text** on the settings page, the
transcription page, error messages, the introduction site, the API manual, the README and the
troubleshooting page now says `JTLW`.

* **URLs, module names, settings file names and page anchors stay lower case** (for example
  `/admin/jtlw`); changing them would break existing bookmarks, API calls and settings imports.
* The English and Japanese interfaces are updated too, and an automatic check now stops the
  abbreviation from drifting back to lower case (identifiers are exempt).

### Introduction site: the GitHub button in the phone menu looked cut off at the top

When the window is narrow and the navigation folds into a menu, the GitHub entry had square top corners
and a top border that differed from the other three sides, so it looked as if its top had been sliced
off. The style tried to draw a separator line above it, but the button already has a border on all four
sides.

* It now matches the language menu above it: same width, height, corner radius and border, with the
  text centred vertically.
* Measured in a browser at phone width with the menu open, in all three languages, and added to the
  existing navigation check (same width, height and radius, and matching top and bottom borders).

## [1.16.12] - 2026-09-23

### Updates no longer print uv's "`UV_NATIVE_TLS` is deprecated" warning

To make uv use the operating system's trust store behind corporate TLS inspection, both the new and the
old environment variables were set (`UV_SYSTEM_CERTS` and `UV_NATIVE_TLS`), assuming uv would ignore
the one it did not know. Newer uv versions do know the old one and **print a deprecation warning every
time**, in every `jtdt update` and install, which reads like the upgrade went wrong.

* uv is now asked whether it supports the new flag, and **only the variable it understands is set**:
  `UV_SYSTEM_CERTS` for newer uv, `UV_NATIVE_TLS` for older uv, and the old one if uv cannot be asked
  (every version understands it). A value you set yourself is left alone.
* Changed in `jtdt update`, the Linux / macOS install script and `setup-python.cmd`, which the Windows
  installer runs. The Windows part was checked on a real `cmd.exe` with a new uv, an old uv and a
  user-set value.
* The update to this version may still show the warning once (the update is run by the old code); it is
  gone from the next update on.
* This feature had no tests since it was added in v1.12.12; it has now, including a run with a real uv
  that checks the warning is gone.

## [1.16.11] - 2026-09-23

### `jtdt update` always ended with "Health check failed", although the service was fine

Since v1.15.11, **the last step of `jtdt update` failed on Linux, Windows and macOS** even though
the service had started normally. The download module the health check uses was never actually
imported (the import line had landed inside a generated script string), so every probe raised an
error that was then treated as "cannot connect".

* The health check now really asks the service.
* **The update to v1.16.11 itself still prints the message once**: the check is run by the version
  you are updating from. If the page opens in a browser, the update succeeded; from the next update
  on it is gone.
* On Windows the version shown in "Programs and Features" is only updated after the health check
  passes, so it stayed at the old number during this period. The next successful update fixes it.
* The same cause also broke a few fallbacks, each of which only prints a warning and does not stop
  the update: installing the VC++ runtime on Windows, the legacy Traditional Chinese OCR language
  download, and the backup WinSW download.

### When the health check fails, the real service log is shown

* **Windows**: it used to look in `<data dir>\logs\jt-doc-tools.log`, a folder that never exists,
  so it always printed "no log at …". The service log is in
  `C:\ProgramData\jt-doc-tools\Logs\`: `jtdt-svc.err.log` (startup messages and tracebacks),
  `jtdt-svc.out.log` (the service's own log) and `jtdt-svc.wrapper.log` (the service wrapper).
* **macOS**: it only read `~/Library/Logs/jt-doc-tools.log` (the service's own log), but uvicorn's
  startup messages and uncaught tracebacks go to **`jt-doc-tools.err`** in the same folder, which is
  exactly what you need when the service does not come up. Both are now shown, `.err` first.
* **Linux** uses `journalctl`, which was already right.
* `jtdt logs` uses the same list. On Windows, logs written in the system code page (cp950 on
  Traditional Chinese systems) are decoded correctly, and only the end of a large log is read.
* The wait for the service to answer is now up to 2 minutes instead of about 15 seconds, with a
  "still waiting" line every 15 seconds. The first start after an update recompiles everything and
  antivirus scans the new files, so 15 seconds was often not enough: on an Apple M2 laptop updated
  from a much older version, the service was still loading its tools when the check started.
* The `DeprecationWarning` about `locale.getdefaultlocale` at the end of an update is gone; Windows
  now asks the system display language to pick the Chinese or English troubleshooting page.
* The health check entry on the troubleshooting page covers this case and lists the log location on
  each platform.

### Four more places used a name that was never defined

A new automatic check over all the code found the same kind of mistake elsewhere:

* **The friendly "please sign in / no permission / page not found" page in the browser came back as a
  500** (since v1.14.16).
* Importing an asset backup that trips the zip bomb check returned 500 instead of 400 (since v1.15.13).
* Invalid VAT database schedule values returned 500 instead of 400 (since v1.14.51).
* Switching the OCR language file variant raised an error instead of reporting failure (since v1.7.5).

Every file under `app/` and `tools/` must now use only names it defines; the check is our own scope
analysis and needs no extra package.

### LLM disabled: greyed out by default, hidden only when you tick the new option

* The LLM settings page has a new "hide when disabled" option, **off by default**: while disabled,
  tools and options stay visible and greyed out, so you can see why they cannot be used. Tick it to
  remove them from the screen. It has no effect while LLM is enabled.
* The three tools that only work with an LLM (sentence translation, document translation, meeting
  summary) are now greyed out with a reason in the sidebar and on the home page when LLM is disabled,
  instead of looking normal until you open them. With the option ticked they disappear from the
  sidebar, the home page and the search.
* The LLM options of form filling and text extraction used to vanish when LLM was disabled; they are
  now greyed out like everywhere else.
* The settings page said AI extras are "hidden automatically" when disabled, which was never true.
  The text now matches the behaviour, and the tool count (three places, three different numbers) is
  computed.
* The home page now uses the same tool list as the sidebar. Before, a tool greyed out in the sidebar
  (for example meeting transcription without a speech service configured) looked normal on the home
  page.

### Other

* Three tooltips set by JavaScript stayed in Chinese in the English and Japanese UI: the sidebar
  search count, "the built-in account's permissions cannot be changed" in the permission matrix,
  and the page thumbnails of the per-page stamp editor. The translation check only recognised a
  string placed right after `=` and missed conditional expressions; it now looks at the whole value.
* The translation key check only recognised `{{ tr('…') }}` and missed `tr('…')|replace(…)`; widened.

## [1.16.10] - 2026-09-23

### Meeting transcription: queueing is visible, and queue time no longer counts towards the limit

Since the speech service upgrade on 2026-09-23, a job waiting in the GPU queue reports how many are
ahead, and recognition and correction progress now moves.

* While queued the page shows "Queued (N ahead)" (the count includes other people's jobs); during
  correction it shows how many batches are done.
* **Time spent queued no longer counts towards the waiting limit**: the limit only starts once the
  job's turn comes (the larger of 15 minutes and half the recording length). A job behind several long
  meetings is no longer cancelled by mistake, and the extra 60-minute queue allowance is gone.
* The queue itself has an overall cap (4 hours), so a stuck GPU server cannot hold a slot forever.
* **How long progress stays still is not used to cancel**: the service explained that the queue marker
  lingers for a few seconds after the job's turn comes, correction only updates per batch, and long
  meetings were not measured, so a stall rule would kill long meetings by mistake.
* Speech services that have not been upgraded (no queue field in the response) keep the old behaviour.
* Also fixed: a few messages sent by the server ("queue is full, trying again in N s", "waited N
  minutes…") always showed in Chinese in the English and Japanese interfaces because the catalogues
  had no entries for them. They are translated now and covered by the translation guard.

### Meeting transcription: Detect automatically only listens to the start

The speech service measured it: Detect automatically **does not decide per segment**. It listens to
about the first 30 seconds of speech and uses that language for the whole recording. A meeting that is
mostly Chinese but where someone speaks English first (20 seconds is enough) is recognised in English
throughout, and the error rate on Chinese sentences goes from 15% to 88%. **Silence, background noise
and music without vocals at the start do not matter** (the parts with no speech are filtered out first),
so there is no need to trim them. Choosing the language is never worse than detecting it (when detection
guesses right, the results are identical).

* The note on the language option now says this, and the "more accurate" wording added in v1.16.9 is
  gone: the real difference is not guessing the whole meeting wrong.
* For meetings that alternate between Chinese and English sentences, pick the language used most; whole
  sentences in the other language are mostly not recognised correctly, while English terms inside
  Chinese sentences are fine.
* The troubleshooting page has a new entry for a Chinese meeting recognised as English throughout, and
  the queueing entry was rewritten for the new behaviour.

### Meeting transcription: the waveform tooltip shows who is speaking

The time label next to the cursor on the waveform now also shows the speaker at that moment (a dot in
the same colour as the transcript, and the renamed name if the speaker was renamed). In a gap between
segments it shows only the time rather than guessing; where two people overlap it shows the one who
started later.

### Meeting summary: renaming a speaker updates every section

Previously only the transcript showed the new name; the action cards still said "Owner: S1", and the
summary and the "Who spoke how much" table kept the old code.

* Cards (owner and text), the summary, chapters, the mind map and "Who spoke how much" now follow the
  rename, and so do the downloads.
* In free text only **code-shaped** names (`S1`, `SPEAKER_00`) are replaced; a real name is only
  replaced where the whole field equals it, and `S12` is never cut down by renaming `S1`.
* "Who spoke how much" is recomputed from the renamed transcript: renaming a single segment moves that
  segment's turns and time, and giving two codes the same name merges them into one person.
* Renaming a single segment leaves the codes in cards and the summary alone, since `S1` there means the
  whole person.

### From meeting transcription to meeting summary: renamed speakers carry over

Speaker names changed in the transcription tool used to turn back into `S1` after sending the
transcript to the meeting summary. They now carry over (including single-segment renames), and the
page scrolls straight to "Start the analysis".

### Meeting summary: the progress bar was full while the summary was still being written

Each of the four stages took a quarter of the bar and "starting item N" was counted as "item N done",
so the bar hit 100% as soon as the summary started. The review pass and the whole "events and impact"
round (as many model calls as extraction) did not report progress either.

* Progress is now split by how many model calls are left, every stage reports, and **the bar does not
  reach 100% before the job is done**.
* The progress text was always Chinese in the English and Japanese interfaces; it is translated now.

### Meeting summary: the preview after upload says clearly that it only lists the first segments

It used to be a line of small grey text, easy to read as "only these segments were loaded". It is now a
highlighted box that says how many segments the transcript has, how many are listed and that the
analysis uses all of them, and the last row of the list repeats how many are not shown.

### Meeting transcription: the synchronous API returned 500 when the speech service refused the request

`POST /tools/meeting-transcribe/api/meeting-transcribe` returned 500 whenever the speech service
rejected a request, while the API manual says 400. A 500 makes callers retry as if the service were
broken. It now answers by cause: 400 when a parameter you sent is rejected, 502 when the speech service
fails the job, 503 when it is not set up or cannot be reached, 504 when it takes too long. The web page
path is not affected. The API manual now also says that Detect automatically only listens to the start.

### Scan cleanup: the preview sometimes did not load, or showed an older result

Every preview of a page was written to the same file. Dragging the corners or rotating fires several
requests; the page cancels the older ones, but the server still finished them and wrote the same file
later, so the newest preview was replaced by an older one (while the status line described the newer
one), or the browser read a half-written file and the right side stayed empty. Each preview now gets its
own file, is swapped in only when complete, and only the latest few are kept.

---

## [1.16.9] - 2026-09-23

### Document translation: spreadsheets saved by Excel came out blank in Excel

The symptom: **the preview looked fine, but the downloaded file opened blank in Excel**
(or Excel first offered to repair it, and the repair removed all the data).

The cause was the step that writes the file back. The XML library we use **renames namespace
prefixes it does not know** (`x14ac` became `ns3`) and **drops declarations the body never
uses** (`xr2`, `xr3`). The XML is still valid, but Excel writes
`mc:Ignorable="x14ac xr xr2 xr3"` at the top of each sheet, which refers to prefixes **by
name**. Once the names no longer match a declaration, Excel treats the whole part as corrupt.

* **The preview cannot show it**: the preview is drawn by OxOffice / LibreOffice, which
  ignore that attribute.
* **Files saved by Word and PowerPoint have the same problem**: the main document carries
  `mc:Ignorable` too, and text boxes use `Requires="wps"`.
* **Why it was never caught**: every spreadsheet sample we had was saved by OxOffice /
  LibreOffice, which do not write that attribute. The new test material is made to look like
  files saved by Excel and Word, and every real sample is run through it as well.

Prefixes and declarations are now written exactly as in the original, and
`standalone="yes"` is kept. **Files already translated are not repaired; translate them again.**

Sentence-by-sentence translation does not accept spreadsheets and is not affected.

### Meeting transcription: choosing Chinese made every job fail

With the language set to Chinese, every job was rejected by the speech service at
submission, showing `invalid_request` for the `language` field. The fault was ours: the
dropdown sent `zh`, while the service expects the BCP-47 code `zh-Hant` (English and
Japanese happened to send `en` / `ja`, so they worked). The API manual already said
`zh-Hant`; only the dropdown on the page was wrong.

* The dropdown now sends `zh-Hant`, and the server also maps common spellings such as
  `zh` and `zh-TW` to it, so API callers sending `zh` are not rejected either.
  **Simplified Chinese (`zh-CN`) is never turned into Traditional.**
* When the service does not accept a language setting, the message now says what to do:
  choose Detect automatically and submit again.
* **Why the tests stayed green**: the fake speech service used in tests never checked the
  language code. It now rejects unknown codes the way the real API does, and every option
  in the dropdown is actually submitted once.
* A note next to the language dropdown: if you know the language used throughout, pick it;
  recognition is more accurate.

### Meeting transcription: a hint when the end of the recording has no text

The speech service side told us that if its server restarts while sending a result, a
transcript that was only half sent can come back as a success (fixed on their side and deployed
on 2026-09-23; the hint now mostly flags recordings that were left running, and the length of
each gap is logged so we can decide later whether to keep it). A result with no segments at all was already treated as a failure; **one missing
its last part** now gets a hint on the result page saying how long the silent tail is.

* **A hint, not a failure**: a recording that was left running, or that ends with applause or
  music, looks the same, and that transcript is complete. The hint states the length of the
  gap, and the user knows what happened at the end of the recording.
* The threshold is 60 seconds: across 25 recordings the service measured, the longest natural
  gap was 41.8 seconds.

### Project site

* The speech section now says your important recordings never leave your network (README too).

---

## [1.16.8] - 2026-09-23

### Speech service error messages: two pointed the wrong way, one gave no next step

While checking our troubleshooting page, the speech service side pointed out two mistakes,
and **the product's own error messages had the same ones**:

* **401 and 403 are now told apart.** One message used to say "check whether the key was
  revoked or mistyped", but a 403 means the key **is correct** and simply lacks permission for
  the action, so that advice finds nothing. A 403 now says to ask their administrator to grant
  the permission; the missing permission is written to the service log.
* **An expired link is not caused by queueing.** They fetch the file as soon as the job
  arrives and never fetch that URL again. The old message said "usually because it waited in
  the queue too long; ask the administrator to extend the link lifetime", which sent people to
  suspect their queue and to change **a setting that does not exist**.
* **Success with no transcript at all** can happen when their server restarts while sending
  the result (fixed on their side, not yet everywhere). This system already treats it as a
  failure and never hands over an empty transcript; the message now says submitting again
  usually works.

None of these messages had a test, and they sat outside the table the translation guard
checks. They now live in the same table, both guards cover them, and behaviour tests were
added (mutation-checked in both directions). The troubleshooting page was updated in all
three languages, with a new entry for the empty-transcript case.

---

## [1.16.7] - 2026-09-23

### Transcription: 60 more minutes of waiting for the queue

Since 2026-09-23 the speech service GPU **handles one job at a time and queues the
rest**, and both while queued and while recognising it reports "running" with no
progress at all, so this system cannot tell "queued" from "stuck". With a three-hour
Chinese meeting ahead in the queue the wait is roughly 18 to 30 minutes, past the old
15-minute floor: **the job was declared timed out, and this system asked them to
cancel** a job that would have succeeded.

* The limit is now the larger of 15 minutes and half the recording length, **plus a
  60-minute allowance for queueing**. A job that is truly stuck is given up an hour
  later; that only means waiting longer, while killing a healthy job wastes all of it.
* The speech service will add a "queued, N ahead" signal; once it does, queueing will
  stop counting against the limit and this allowance goes away.
* Added a test that **actually runs the polling loop** (fake clock, a fake service that
  always reports running with no progress). The limit had no test at all before.
* The troubleshooting page was updated to match.

### CI: the scheduled run failed in the production-dependencies job

The "no internal addresses in the public tree" guard scanned the whole directory, and
that job creates its virtual environment inside the repo with `uv sync`, so addresses in
third-party package sources were reported as ours. It could never fail locally (the
development tree has no virtual environment). It now scans **only files tracked by git**,
which is what actually gets published, falling back to a directory walk only where there
is no `.git`; both directions were checked on a real clone.

### Lighter placeholder text in input fields

The browser's default placeholder colour was nearly as dark as real input, so multi-line
examples (the paste box and meeting background in Meeting summary) looked like content
that had already been filled in. One lighter grey is now used site-wide, guarded by a
real-browser check that it is lighter than the browser default (by luminance, not a
hard-coded colour).

> The first version of that guard passed vacuously: on the admin page it picked the
> first `input[placeholder]`, which was the **sidebar search box** (white on purple,
> already light). It now targets the actual field.

---

## [1.16.6] - 2026-09-23

### "My jobs" said 24 hours; results were actually cleared after 2

Opening a finished job gave **410 file expired**, while the job list said
results are kept for 24 hours. On a production instance, a meeting summary
finished less than five hours earlier still had its job record and a "done"
status, but its result, transcript and ownership record were already gone.

The **two retention periods did not line up**: every tool keeps its job result,
and whatever "Open" reads back, in the temporary area (2 hours), while the job
retention is 24 hours. v1.14.31 made the job-result directory use the job
retention, **but no tool ever wrote its results there**, so that fix only went
halfway. This affected **every background job**, not just meeting summaries.

* When the temporary area is cleaned, files that belong to a job still inside
  its retention (or still running) now follow the job retention: the result,
  temp files named after the job, and the ownership record.
* Files are matched by the job id embedded in their names, **not by each tool
  registering its files**, which the next new tool would forget to do.
* Old temp files that belong to no job are still cleared after 2 hours (there is
  a reverse-control test for that).
* **There were two cleanup paths.** Besides the retention sweep, a 30-minute loop
  had its own fixed 2-hour rule and **ignored the admin's temp retention setting**.
  The first version of this fix only changed the former; checking on production
  after deploying showed a job just over 2 hours old still being cleared by the
  latter. Both now go through the same code, and the setting actually applies.

> Results that were already cleared cannot be recovered; the fix applies from
> the upgrade on.

### Layout: stretched number fields, a status split over two lines, a hard-to-find JTLW name

* Five small number fields (the speech service timeout, three workspace
  settings, the speaker count) stretched across the whole row with their unit
  pushed to the far right. They now share one fixed-width style.
* The user list's status could break into two lines when the table got tight;
  earlier screenshots only ever showed an empty user list. Fixing only that
  column pushed the squeeze into the next one (a role badge split in two), so
  both now stay on one line.
* **JTLW's full name (jt-live-whisper) and its project link** sat in the top
  right corner of the settings page, and were missing from the transcription
  tool altogether. Both now follow the description line, from one shared
  component.

> These are now measured **in a real browser** (field widths, the number of line
> boxes, visible bounding boxes). The first version of the fix still measured
> full width on the admin pages, because a higher-specificity rule was the real
> culprit; the guard also caught itself passing vacuously on a hidden panel.

### Server-side labels in the two meeting tools had never been translated

Caught while taking English screenshots on an instance **with data in it**: the
transcript-shape dropdown, every stage of the transcription job, the node kinds
on the mind map and every failure reason the speech service reports were all
**still Chinese** in the English and Japanese interfaces. What they have in
common is that the text comes from server data, which the guard that scans for
literal `tr('…')` calls in templates cannot see.

* 66 entries added to each catalogue.
* Five sources folded into the "labels computed by the program" guard, each one
  mutation-verified to redden only its own row.
* Job **failure text** now goes through `tr()` as well: like the progress
  message it is Chinese produced on a background thread, and without that layer
  the one line you most need to read stays in Chinese.

> **The report only printed the first four items**, so everything after them
> was invisible: "only 4 left" read as "only 4 were missed" when there were
> actually 54. It now lists every one.

### The Taiwanese term for "speaker", with a guard this time

A correction the day before had been applied by hand; nothing was stopping it
from coming back, and three more places had slipped through. Adding the word to
the banned-terms table immediately caught four more in the documentation.

### Pasted transcripts are identified by a flag, not by their filename

The filename the browser attaches to a pasted transcript **is visible to the
user** (job name, notifications), so it has to follow the interface language.
The server used to compare that filename as a string to decide whether to use it
as the document title; once translated, the comparison never matched and the
title became "Pasted transcript meeting minutes", **with nothing on screen to
show anything was wrong**.

### Intro site: the language picker did not match the GitHub button

They each worked out their own height, 12.2 px apart (30.2 vs 42.4), and were
not vertically aligned either. **No padding numbers were nudged**: a different
language or a font-size change would have knocked it out again. A browser guard
measures all three language editions.

### Speaker-count hint: one meeting became twenty, and the wording stays

It used to cite **one** meeting. Re-measured by the speech service across all 20
meetings of a public corpus, giving the correct count and leaving it unset are
**indistinguishable** (13.24% vs 12.46%; the paired bootstrap 95% confidence
interval crosses zero). So "when in doubt, leave it at 0" stays exactly as it
was, and **was not strengthened** into "letting it decide is better".

> The mechanism is firmer than that figure: across those same 20 meetings the
> best number of clusters was **never larger than the actual number of people**.
> Six people in the room does not mean six groups can be told apart acoustically.

### Documentation

A speech-service integration section in the README and on the intro site (how to
use it, what you get, why the work is split this way), in all three languages;
a Meeting summary screenshot in the showcase; shorter descriptions and the
missing icons for the two new tools; and the new synchronous API documented.

> The API coverage guard built its table from tools that **have** an `/api/`
> path, so a tool with none was invisible to it. It now compares against the
> tool registry.

---

## [1.16.5] - 2026-09-22

### Player polish

Round primary play button (it used to look like the secondary buttons beside
it, despite being the player's main action); the current position is now the
prominent number and the total length the quiet one; and moving the pointer over
the waveform draws a follow line with the time at that spot — **so you know
where a click will take you before you click**.

---

## [1.16.4] - 2026-09-22

### Renaming speakers in Meeting summary too

Same behaviour and the same colour palette as the transcript tool — the same
meeting should not show `S1` in blue in one tool and green in the other.
Clicking a name renames every segment from that speaker by default; unticking
the box changes only that one.

**The stats move with it**: the speaking-share chart is keyed by the speaker
code, so renaming only the transcript would leave `S1` on the chart and a real
name in the text — two sets of names on one screen. **`seq` never changes** —
decisions and action items cite `seq`, and renaming must not invalidate a
single citation.

---

## [1.16.3] - 2026-09-22

### Three fixes in the transcript player

* The play icon now turns into a pause icon. Both icons are rendered and
  toggled through wrapper `<span>`s — **you cannot set `.hidden` on an `<svg>`**
  (`SVGElement` has no such property; this project lost a whole overlay to that
  once).
* **The waveform was tiny.** A normal meeting recording peaks at 0.1–0.3, so
  drawing absolute values gives a thin line down the middle. Peaks are now
  normalised against the loudest point and square-rooted so quiet passages stay
  visible.
* **Clicking a timestamp scrolled to the wrong row.** The code used `offsetTop`,
  which is relative to the nearest *positioned* ancestor — the transcript box
  only has `overflow:auto`, so it was measuring from the top of the whole panel
  and always overshot by the height of everything above it. Now computed from
  two `getBoundingClientRect()` calls plus `scrollTop`, which **depends on no
  CSS at all**.

---

## [1.16.2] - 2026-09-22

### The waveform and player never appeared — `@router.get` does not accept HEAD

The page sent a HEAD request to ask whether the recording still existed, and
only then showed the player. But **FastAPI's `@router.get` registers GET only**
(Starlette's own `Route` adds HEAD; FastAPI does not), so the probe always
returned **405** and the player was never shown — with no error anywhere.

Both sides fixed: the endpoint now answers HEAD (with a guard), and the page no
longer decides visibility from a probe — it attaches the audio and lets the
`<audio>` element's own `error` event speak.

> **Do not let a "let me just check" request decide whether a feature appears.**
> A failed probe and "there is genuinely nothing there" look identical on
> screen, and the probe is one more thing that can break.

---

## [1.16.1] - 2026-09-22

### Listen while you read

Waveform and player in the transcript card: click anywhere on the waveform (or
on a timestamp) to play from there; the segment being played lights up and
scrolls into view; **each speaker gets their own colour**; and clicking a
speaker code renames them — every segment from that speaker by default, or just
the one if you untick the box. There is also a **Save to workspace** button,
which stores plain text carrying `[mm:ss]` and speaker names.

The waveform is computed in the browser, so no ffmpeg is needed on the server.
**Recordings over 60 MB are not decoded** — a three-hour meeting expands to
several gigabytes of PCM and would kill the tab; those fall back to a plain,
still-clickable timeline.

> **Playback goes through the logged-in ownership check, not the signed URL.**
> The signed URL exists so the speech service can fetch the file (short-lived,
> no login); reusing it in the page would either expire or force us to make it
> permanently public.

### Terminology: `語者` became `發言者`

Neither `語者` nor `講者` is how Taiwan refers to the person speaking in a
meeting. Both are now `發言者` — one thing described by two words reads like two
things. Search keywords keep the old forms so existing habits still work.

---

## [1.16.0] - 2026-09-22

### Working through the speech service's integration checklist

**One code-to-message table was only used by one of the two paths.** A rejection
at submit time arrives as an exception; a terminal failure arrives in
`errors[]` — and only the latter consulted the table. Since their v1.7 fetches
the audio at submit time, source-related errors now mostly take the former path:
production showed `JTLW rejected this request: source_not_allowed (field
source.url)` when the table already held "ask your administrator to add the
address to their allow-list". The wording now lives in one place, with a guard.

Also added: a wall-clock ceiling on polling (**there was none** — if they
stalled we would have polled forever), retry on `queue_full` using their
`retry_after_ms`, saying out loud when correction failed and you are looking at
raw recognition output, treating `task_not_requested` as "that layer is absent"
rather than an error, fetching `/result` to show correction statistics, and
sending `correction_level: punctuation_only`.

### Timestamps were lost in the handoff, not in recognition

A report that the transcript "has no time information" — the speech service
thought we were reading only the corrected layer. **It was not that**: all 258
segments stored in production carry start/end times and a speaker. The times
were dropped when we flattened the transcript to plain text on the way to
Meeting summary. Now it sends JSON, and copied plain text carries `[mm:ss]`.

> **Their explanation sounded reasonable but did not match the file in our
> hands.** One line — `sum(1 for x in segs if x.get("start_ms"))` — separates
> "never received" from "received and then dropped". Do not change your own code
> to match someone else's diagnosis.

---

## [1.15.99] - 2026-09-22

### The button did nothing — the progress area was an empty `<div>`

`JobProgress` wires itself to `.job-reset` / `.job-bar-inner` / `.job-status`
inside its root. With a bare `<div>`, `root.querySelector('.job-reset')` returns
`null` and `null.addEventListener` throws — **the rest of the inline script never
runs**, so the "start" button was never given a click handler. Nothing looked
wrong: the upload worked (it runs before the exception) and the options panel
opened. The server log settled it — `POST /upload` present, `POST /start` never.

**The browser boot sweep missed it** because this tool renders only a "configure
it first" stub when the speech service is not set up, and the sweep uses a fresh
throwaway instance that never is. The sweep now seeds that configuration (with a
guard that the seeding still works), plus a static check for any template that
constructs `JobProgress` without the shared component.

---

## [1.15.98] - 2026-09-22

### The API key field showed its example text at the one moment it misleads

We build the `Authorization: Bearer <key>` header ourselves, so the field only
wants the key. Their documentation shows the whole header, though, and copying
from it naturally brings the `Bearer ` prefix along.

Tested against their production API:

| Header we send | Result |
|---|---|
| `Bearer jtlw_…` (correct) | 200 |
| `Bearer Bearer jtlw_…` | **401 "missing or invalid API key"** |
| `Bearer Authorization: Bearer jtlw_…` | **401** (same) |

**The message points the wrong way**: it blames the key, when the key was fine
and only the prefix was extra. The prefix is now stripped on save — and only
when `bearer` is actually followed by a space, so a real key that happens to
start with those letters is not chewed short (which would look like a 401 too).

The example text itself was also wrong. "Leave empty to keep the current key"
was an unconditional placeholder, and a placeholder only shows when the field is
empty — which here means **no key has been saved yet**, so there is nothing to
keep. It now appears only once a key exists; before that the field shows the
shape of a key, with a line below explaining to paste the key alone.

### The speaker-count hint is narrower now: people who spoke enough to be told apart

v1.15.97 changed "attendees" to "people who will speak". The speech service
re-measured on the **full 37 minutes** of a seven-person Chinese meeting (the
earlier figures were from a three-minute excerpt; they corrected them):

| Setting | Speakers confused | Speakers separated |
|---|---:|---:|
| Not specified | 22.90% | **3 / 7** |
| 7 (the right answer) | **30.15%** | 7 / 7 |
| 5 or 6 | **17.8%** | 5–6 / 7 |

**The conclusion did not flip** — giving the correct count is still worse — but
that table alone leads to the wrong decision. Three of the seven spoke for only
28.6, 56.2 and 94.8 seconds; **forcing a cluster for someone whose voice print
is too thin costs you the main speakers instead**.

Leaving it blank has its own cost, and it is the worse one: with no hint,
**four of the seven speakers do not appear in the output at all** — every
sentence they said is filed under someone else. For a citation mechanism that
is not "a few seconds missing", it is "a decision attributed to the wrong
person".

So the hint now asks for the number of people who **spoke enough to be told
apart**, with a line an organiser can actually judge ("leave out anyone who said
only a sentence or two"). **If in doubt, still leave it at 0.**

### Terminology: `運維` is a mainland word; Taiwan says `維運`

Two places said `日常運維` — the `OPS.md` heading and the document index in
README — while `AUTH.md` and two tools already said `維運`. Both spellings were
sitting in the same product. Added to the banned-term list.

### Dependencies: anyio raised to 4.14.2 (one critical, two others)

| Advisory | Severity | What |
|---|---|---|
| GHSA-82r6-8w77-94w6 | **Critical** | `TLSStream` encodes host names with IDNA 2003, so certificate matching can be bypassed |
| GHSA-5p39-cfhj-2xmp | Moderate | Process-pool workers block forever when stderr is never drained |
| GHSA-3w57-8xmc-8v26 | High | `run_process` ignores `extra_groups` and can keep the parent's groups |

All three are fixed in 4.14.2; we were on 4.13.0. anyio is a transitive
dependency (`starlette` / `httpx` / `watchfiles`); we never import it directly.

### Static analysis: three guard regexes were exponential

In `(?:\\.|(?!\1).)*` the two alternatives **overlap** — `\a` can be one
`\\.` or two `.` — so a failing match tries every split:

| Consecutive `\a` | Before | With `[^\\]` |
|---:|---:|---:|
| 18 | 128 ms | 0.01 ms |
| 22 | 1,056 ms | 0.01 ms |
| 24 | **5,393 ms** | 0.01 ms |

The input is our own source, so nobody can reach it — **but a guard that hangs
is as hard to diagnose as a guard that is broken**. All three fixed, plus a
guard so the shape cannot be copy-pasted back in.

Two more hardenings in the same round: the language dropdown on the site now
accepts only a plain `.html` filename in the same directory, and the 308
redirect for renamed tool URLs re-encodes the path.

---

## [1.15.97] - 2026-09-21

### Asking for "attendees" was the wrong question

Meeting recording to transcript used to ask for the number of attendees, and the
hint said that filling in the exact number made things more accurate. **A
measurement disproved that.**

The speech service ran a seven-person Chinese meeting: four of the seven spoke
for less than ten seconds (the quietest said one sentence, 1.5 seconds, nowhere
near enough voice to identify). Telling it the correct figure of seven made the
**speaker error rate worse** — forced into seven clusters, the system can only
split the main speakers' turns to make up the count. Its own
guess of five was the sensible answer.

The field now asks for the number of **people who will speak**, and says plainly
to **leave it at 0 when unsure** (its own guess is usually better than a wrong
hint).

> **A literal guard pins this down**: changing it back would turn no test red,
> and results would get **systematically worse** with the user none the wiser.
> Same family as the IIS installation order in `OPS.md`: only the person
> following the instructions ever hits it.
>
> It is also the same judgement as not deriving the audio address from the
> request host: **an automation that looks obvious is systematically harmful
> when its direction is wrong.** The obvious next step would have been to fill
> the count in from the attendee list, and that would have made things worse.

## [1.15.96] - 2026-09-21

### Speech service settings page: a round of fixes

* The Chinese word used for "audio file" was not the Taiwanese one; corrected in
  39 places and added to the banned-terms list. The check has to **exclude the
  correct longer form**, otherwise fixing it makes the guard fail (the same trap
  as an earlier term whose correct form contains the wrong one).
* **The processing profile was a free-text field** (`meeting.balanced` and the
  like). An administrator had to remember a magic string and only found out at
  submit time if it was wrong. It is now a dropdown **filled from the service**;
  we do not keep a copy of the list (a copy drifts). If the list cannot be read
  the current value is kept and the reason is shown, so it **never turns into an
  empty dropdown** (which would read as "there is nothing to choose").
* **The enable switch now sits on its own** — it decides whether the whole tool
  is greyed out, which is not the same kind of thing as the fields below it.
* **The full name and a project link** now sit to the right of the title; an
  abbreviation does not tell you which service you are configuring.
* **Plain http now raises a warning**: the API key travels in a header and the
  recording goes the same way. Loopback addresses are not flagged (they are a
  security-origin exception, and warning there would just be noise).

* **The audio address is pre-filled with the address you are connected on**
  when it is empty, with a note saying where that came from. It only fills the
  **input**; what gets sent is always the stored value. One deployment can be
  reached both directly on the LAN and through a reverse proxy, so deriving it
  from the request would get submissions from the public name rejected by their
  allow list, and the symptom would be "it works for some people".
* The explanation panel was missing its layout hooks and looked unstyled.

### Self-signed certificates: **paste the certificate**, do not turn verification off

Internal services often use self-signed certificates. The switch to turn
verification off is still there (on by default), but **the right answer is to
paste their certificate**: it affects this one connection and nothing else.

Saving computes the **SHA-256 fingerprint** and shows it, because **an
administrator has to check it against the value they published before trusting
it** — pasting an unchecked certificate hands the question of "who do I trust"
to whoever pasted it. Anything that is not a certificate is rejected at save
time (otherwise it fails at submission with an internal ssl error that gives no
hint about where it went wrong).

> The cost of turning verification off is stated on screen: **anyone can then
> impersonate the service, and every submission hands over the API key.**

## [1.15.95] - 2026-09-21

### Meeting recording to transcript: the progress bar never moved

Two fields from the speech service were wired up wrongly:

* `progress` **is an object, not a number** (it holds `percent`,
  `processed_audio_ms`, `total_audio_ms`). Treated as a number, the type check
  never matched and the bar stayed put.
* The stage lives in `progress.stage`; **there is no top-level `stage` field**
  (the top-level one only appears inside an error object, to say which stage
  failed). Its values are *stage* names (`fetch`, `normalize`, `asr`,
  `diarization`, `correction`, `finalize`), not the task names I assumed, so the
  label always fell back to the generic "processing".

Together the symptom is **"nothing is happening"**: the job was running fine,
the screen just could not show it.

> **My own test could not catch it**: the fake server returned the shape I had
> *guessed* (`progress: 1.0` plus a top-level `stage`), so it agreed with our
> code and stayed green forever. Same family as the previous version's "checking
> a value we computed against a value we computed": **a fake has to follow the
> other side's documented contract, not the shape we imagined.** The fake now
> reports "running" a few times first, which is the only way to see whether the
> bar moves.

### Failure messages now say something a person can act on

With their error-code list in hand, each code is translated rather than printed
raw (`audio_too_long` becomes "the recording is too long; their limit is six
hours, please split it first").

**One of them is especially misleading**: their "cannot reach the source" also
covers **an expired URL**, and what we hand them is a short-lived signed URL.
Taken literally, "file not found" sends the user looking for a file that is
still there, so a 404 on a signed URL is always reported as "the link expired,
most likely after a long queue".

## [1.15.94] - 2026-09-21

### New tool: Meeting recording to transcript (49 tools, now 50)

Audio or video in, a transcript with **timings and speakers** out, ready to hand
to Meeting summary in one click. Recognition, speaker separation and punctuation
repair run on an external speech service (those need a GPU and the audio
context); **summaries, decisions, action items, mind maps and translation all
stay in this system**. That is why these are two tools rather than one: a
customer with no speech service still gets Meeting summary.

**Greyed out until it is set up.** This tool depends on an *external service*,
not a local package, so you cannot tell by looking at the machine whether it is
available; only the settings say. When greyed out it says why and where to go,
and the page itself says the same thing: it must never be an upload box that
looks fine and only fails once you press it.

The lock reason also moved out of the templates. There used to be one reason
only ("wrong interface language") and that sentence was hard-coded in two
templates; adding a second reason would have made both of them say the wrong
thing. The reason now travels with the data.

### What this version ran into

* **`safe_remote_base_url()` drops the path on purpose** — that is how it works
  as an SSRF barrier (the caller appends its own fixed path). The first version
  used the whole address the administrator typed, so requests went to `/jobs`
  instead of `/api/v1/jobs` and came back 404 with nothing but "HTTP 404".
  **Only a real request shows this**: unit tests agree with themselves, because
  the string is one we computed. The fixed prefix is now ours.
* **The admin-page guard matched on the last path segment** — `/admin/jtlw` never
  appeared in the test plan at all; the tail `jtlw` happened to collide. Counting
  them: **4 of 52** admin pages were passing that way, two of them with zero
  occurrences of their full path. The same hole was fixed for the API list in
  v1.15.30 and missed here. The criterion is now the full path.

### Stance detection in Meeting summary is cancelled

Getting "this person opposed that proposal" wrong is not a quality problem, it
is a mistake that harms someone, and the reader has no reason to doubt it: it
looks exactly like a decision card and carries a segment number too. It also has
**no measurable criterion** — two people reading the same passage can label the
stance differently. Every other part of that tool had its criterion before it
had its feature.

## [1.15.93] - 2026-09-21

### Short-lived signed URLs: let an external service fetch one file, without handing over a credential

Some external services only accept a **URL** — the file is not uploaded to them,
they fetch it themselves from an address we provide.

The obvious approach is to issue them an API token. **That road is closed**:
`api_tokens` has no concept of scope, so one token unlocks every `/api/*`
endpoint (jobs, notifications, workspace, every tool). Granting all of that so
someone can fetch one file is not acceptable by this project's own standards.

A signed URL is the other way round: **nothing is handed over**. The URL is the
authorisation, it expires on its own, nothing needs revoking, and a leak is
bounded to that one file until it expires.

Three decisions:

* **The expiry has to be inside the signature** — sign only the file id and
  anyone can extend it indefinitely by editing `exp`.
* **Anything that fails to verify returns 404, not 403** — a 403 tells the caller
  "that id exists", and the id is the thing we did not want to leak. Malformed
  id, wrong signature, expired, file missing: all four must look identical from
  outside.
* **The URL is built from a configured address, never the request's Host** — the
  same service is reachable three ways (direct on the internal network, via the
  reverse-proxied domain, on the test box). Building it from the request Host
  means anyone arriving via the public domain submits a job carrying that domain,
  which the other side's source allowlist correctly rejects — and the symptom is
  "it works for some people and not others". A guard checks the source of that
  function for `request` / `headers` / `url.hostname`.

> This path **needs no change to any gate** (measured): a GET without a bearer
> falls back to session auth, and `/api/` is in the public prefix list; CSRF only
> guards unsafe methods. **POST is not like this** — CSRF is a pure-ASGI
> middleware that runs before route matching, so any POST without a token gets
> 403, including to paths that do not exist. The two paths sit behind completely
> different gates; a conclusion about one does not carry to the other.

## [1.15.92] - 2026-09-21

### Meeting summary gains a fifth category: events and impact

An external review noted that the summary never states the incident itself —
what happened and how much it affected. **That was not a model limitation, it
was our design**: the summary is written **only from verified items** and never
sees the transcript, and "what happened" was not one of the four categories, so
it structurally could not appear.

### How you add it turns out to matter a great deal

| Approach | Recall (4 kinds) | Fabrication (4 kinds) | Events and impact |
|---|---|---|---|
| Baseline (four kinds) | 100% | **5%** | — |
| Five kinds in one call | 88-100% | **16-19%** | 2 (duplicates) |
| Five kinds + explicit routing rules | 100% | **20%** (worse) | — |
| Its own pass | 100% | 5-6% | 19 (half were progress reports) |
| **Its own pass + mechanical boundary** | **100%** | **6%** | **7, every one correct** |

**More rules produced more output, and more noise.** Reading the items showed
why: the model did start noticing facts, but **filed them under decisions**
("the API rate limit is sixty a minute", "they quoted 320,000 a year for
maintenance"). The problem was never that the boundary was unclear — it was
**judging five categories in one call**.

So events and impact **runs as its own pass**: its own prompt, its own review,
while the four-kind prompt and rules are **unchanged to the character**.
**The zero regression is guaranteed by construction, not by tuning.**

The cost was measured: extraction calls double (29 to 61, 147s to 303s for a
160-minute meeting). So there is a switch, and **the trade-off is stated on the
page** — leave it on for incident and status meetings, turn it off for purely
forward-looking planning and the analysis takes about half as long.

### The boundary has to be mechanical, not a matter of feel

The first version said "personal progress reports do not count". **The model
could not tell**, because "the config file has been sent out" genuinely is
something that already happened. It became two mechanical rules:

* Never write "done / sent / reviewed", nor "stuck / waiting / not ready yet".
* Every entry must state **what was affected** or **a specific number** —
  if it can state neither, do not write it.

> **⚠ I opened a hole while closing one**: rewriting the rule replaced the
> example "stuck because permissions were not granted" along with the paragraph
> around it, and that whole class promptly reappeared (4 entries, with
> duplicates). **Do not open one hole to close another** — the restored rule
> sits alongside the new ones rather than replacing them.

> **The first guard had no teeth**: `analyse` has two per-kind loops, and I only
> checked that `MAIN_KINDS` appeared somewhere — changing just one of them back
> to `KINDS` stayed green. The criterion had to become "**no loop may use
> `KINDS`**".

## [1.15.91] - 2026-09-20

### Fixed: mindmap node text was cut off, so it read as if the analysis had lost content

The node label was `text[:60]` — **60 characters, no marker of any kind**. On
screen that produced "…set the environment variable (Environment Variable) to L"
and "…deny them all de", while **the cards and the transcript held the full text
all along**.

The user cannot tell "shortened for display" from "the analysis lost the rest",
and those two are worlds apart in severity.

Three parts to the fix: **an ellipsis** so shortening is visible, **never cut a
Latin identifier in half** (`LOG4J_FORMAT…`, `deployment.yaml`,
`X-Forwarded-Proto` each count as one word), and **the tooltip now shows the full
text** (it used to show the same truncated string).

### The 60-character limit came from a corpus that could never reach it

Of **868** items from earlier runs: median **18** characters, **longest 56** —
**the limit was never once hit**, so the truncation never appeared in any
measurement I had taken.

Running a real committee transcript (54 items): **median 39, p90 = 101, longest
379**.

| Limit | Shown in full |
|---:|---:|
| 60 (before) | **77%** — roughly one in four cut |
| 90 | 87% |
| **120 (now)** | **94%** |
| 160 | 98% |

> **A corpus can be large and still the wrong shape.** The evaluation corpus is
> synthetic short meetings; real items carry English terms and parenthetical
> notes. Same lesson as "the class synthetic samples cannot reach".

> **A guard must not share a definition with the code it guards**: my first
> version used the product's own `_is_token_char` to check "did it cut an
> identifier in half", so a mutation that emptied that definition **was not
> caught at all** — the criterion moved with the mutation and stayed
> self-consistent. The test now carries its own.
>
> The first version of that same test also used
> `original.startswith(what_was_kept)`, which is **toothless**: `LOG4J` is a
> prefix of `LOG4J_FORMAT…`, so **exactly the broken cut would be judged
> correct**.

## [1.15.90] - 2026-09-20

### Fixed: half the meeting summary's "speaking time" figures are estimates, and the page did not say so

Subtitle files (.vtt / .srt) and JSON carry an end time on **every** cue — those
are measured. A plain-text transcript only records when each person *started*,
so the end time is borrowed from the **next** segment's start, which means the
speaking time **includes the pauses in between**.

The two look identical on screen. The estimated kind now says so: the column
header becomes "Speaking time (estimated)" and a line under the table explains
how the figure is derived.

> **The server decides** (from which parser ran); the front end must not guess
> from the segments — "every end time equals the next start" can also be true of
> a genuine subtitle file, so a guessed criterion would lie on some files.
>
> The guard **first proves the premise holds** (plain text really does borrow the
> next start). If the parser ever stops filling it in, the expectation above
> becomes a lie while staying green.

### Empty cards no longer draw a conclusion about the meeting

"This meeting had nothing of this kind" became "**the analysis did not find**
anything of this kind **in the transcript**". Measured recall is 94-100%, not a
guarantee of 100% — the first phrasing draws a conclusion about the meeting, the
second states what we actually know.

## [1.15.89] - 2026-09-20

### Fixed: the submission-check row in "My jobs" had nothing to click

That row has exactly two exits: **download** (`result_path`) and **open**
(`view_url`). Submission check does not produce a file — it produces **a report
filed under a case** — so it legitimately has no `result_path`; but it had no
`view_url` either, so the row said "done" with nothing to click and the user had
to work out for themselves to go back into the tool and find the case.

The case page is already addressed by case id, already polls progress and
already has its own access check, so pointing at it is enough — **no `?job=`
restore work was needed**.

**All 29 job-submitting tools were surveyed**: only three actually offer "open"
(sentence translation, document translation, meeting summary) and all three
restore correctly, so the "open lands on a blank page" bug fixed earlier does
not exist anywhere else. This was the only tool with neither exit.

> **⚠ My first survey was wrong.** I scanned for `"view_url" in source`, and
> **`preview_url` contains `view_url`** — seven tools that merely show a preview
> image were counted as having "open", three of which I briefly took for a bug.
> Substring matching again (this project has been bitten by prefix and substring
> matching several times). The guard now works on the AST, and **one test exists
> purely to check it does not mistake `preview_url` for `view_url`**.

> **The "prove the scan reaches something" check caught me too**: the first AST
> matcher only recognised `_jm.job_manager.submit` (an `Attribute`), while most
> tools do `from … import job_manager` and call it directly (a `Name`) — **it
> reached 3 tools out of 29**. Without that check the guard would have quietly
> examined three tools and stayed green.

> **The criterion has to land on what "My jobs" actually receives**: `view_url`
> is set *after* `submit()`, with a database write between it and the list. So
> alongside the static guard there is one that really submits a job, waits for
> it to land in the database, and confirms the list side can read it.

## [1.15.88] - 2026-09-20

### The workspace now takes plain text (.txt / .md)

Transcripts from the meeting summary tool and output from the list tool could
not be kept in the workspace.

**The test is the content, not the file name.** Every other type the workspace
takes has a clear signal (PDF and PNG by magic bytes, Office files by opening
the zip and inspecting its structure); plain text has neither. Accepting by
extension would let anything through by renaming it to `.txt`, which is exactly
what the type check exists to prevent. The test is now "the whole file decodes
as UTF-8 and contains no control characters other than tab, CR and LF", and the
name only chooses **between .txt and .md**. A PNG renamed to `.txt` is still
detected as a PNG.

**The whole file is checked, not a sample of the first few KB** — a file whose
first 8 KB are clean and whose tail is binary is easy to produce, and sampling
would let it straight in.

> Plain text has no first page to draw, so its thumbnail is the blank
> placeholder — but it has to say so. Falling through to the "treat it as a PDF"
> path makes it look for a `file.pdf` that does not exist, and the error becomes
> "file not found", which reads as if the user's file had been lost.

### Fixed: one more list written twice — the upload accept attribute

The file picker in `my_workspace.html` hardcoded
`application/pdf,image/png,.docx,…` in two places, while the server-side list
gained spreadsheets and presentations back in v1.14.6. The symptom is that
**the file picker filters out files the server would happily accept**: the user
just finds a file "cannot be uploaded", with no error message to go on.

The list is now computed on the server and passed into the template, the same
way `data-ws-exts` already worked. The "PDF / PNG" wording on the page was
reworded so it will not drift again either.

## [1.15.87] - 2026-09-20

### Fixed: the macro hardening around conversions had never taken effect

Conversions handle files uploaded by users, so each one seeds a throwaway
profile with `DisableMacrosExecution`. Measuring it showed every converter also
passed `--safe-mode` — and that flag **resets the user profile at startup**, so
the setting was gone by the time the run finished.

The old comment said "`--safe-mode` has nothing to do with macros". That
sentence is true; what it missed is that safe mode has everything to do with
**the settings we seed**: it wipes the lot.

> The criterion is "is the setting still there after the run", not "did we write
> it out". The file was written every time, so checking the write would never
> have shown this.

Severity, stated plainly: LibreOffice ships with macro security set to High and
headless conversion does not auto-execute macros, so this was **a layer of
defence in depth that was not working**, not an exploitable hole.

The two things `--safe-mode` guarded against are already covered: user
customisations (every call gets a fresh throwaway profile) and the crash
recovery prompt (`--norestore`). With it removed, three real documents (docx and
xlsx) produce identical page counts, page sizes, character counts and per-page
rendered pixel hashes.

### Fixed: exported PDFs were Letter, not A4

Taiwan prints A4. Letter is 18mm shorter and 6mm wider, so content laid out for
A4 shifts. This affected every conversion whose source carries no page size:
Markdown to office document, the meeting summary exports, plain text and CSV.

* **CSS `@page { size: A4 }` is ignored** by LibreOffice's HTML import.
* **`LANG` and `LC_PAPER` do nothing** — `LANG=zh_TW.UTF-8` only changes the
  length unit (in to cm), the paper stays Letter, and most servers have no
  zh_TW locale generated at all.

What works is seeding `ooSetupSystemLocale` in the throwaway profile. **It must
not be applied unconditionally**: it is also the system locale, so it changes
CJK font fallback. On a real vendor form in docx, the leading spaces were then
measured with a different font and the whole label **shifted about 10pt left**
(identical ink and identical glyphs — purely the width of the whitespace). The
form-filling tools are acutely sensitive to coordinate shifts, and their sources
carry a page size already, so they do not need this at all.

So the test is whether the source declares a page size: those that do not (HTML,
plain text, CSV) get ours; those that do keep theirs — an .odt declaring Letter
still comes out Letter, and a real docx renders to the same per-page pixel hash
as before the change.

## [1.15.86] - 2026-09-20

### Fixed: a term that is not Taiwanese usage

`估計` is Mainland-flavoured. Taiwan writes `預估` (an estimated reading time),
`推估` (derived from another number) or `約` (about 800 MB). It is now on the
banned-term list, scoped to text the user can see — `背景估計` in a comment is a
technical term and stays.

Adding the rule immediately found two places: the word-count tool's reading-time
label (**the panel right next to it already used the correct form**, so both
spellings sat on the same screen), and one sentence in the README about how
memory is measured. The job list's `估 800 MB` became `約 800 MB` at the same time —
**its English translation was already "about"**; only the Chinese half was the
exception.

> The general rule: when you ban a term, sweep the **synonyms already in the
> code** in the same pass. Otherwise half the screen is new and half is old, and
> nothing goes red.

## [1.15.85] - 2026-09-20

### Fixed: headings were invisible white text in exported .odt / .docx

The business report theme draws its heading as white text on a dark banner, and
the Office engine's HTML import **keeps the text colour but drops the paragraph
background** — leaving white on white, so the whole line disappears. PDF is
unaffected (it can paint the background).

**"Markdown to office document" had the same bug**, just unreported. Both tools
now share one override: document formats get dark text with a rule under it,
while the PDF keeps its banner.

> The general rule: **never let legibility depend on a background**. A background
> is the first thing lost in a conversion, and when it goes the symptom is
> "nothing is there", not "the colour looks off".

### Meeting summary: a pasted transcript now takes its title from the background

When text is pasted, the filename is one we invent ("pasted transcript.txt"), so
the exported document was titled "pasted transcript — meeting record" — internal
wording on a document meant to be sent out.

The order is now: **the topic written in the meeting background → the filename →
a plain "Meeting record"**. The download filename follows the same source.

**The title is not guessed from the summary**: the summary is a paragraph, and
truncating it cuts mid-sentence and changes with every run. The background field
is where "Topic: …" belongs.

### Other

- The handoff button to "Markdown to office document" was removed (the result
  panel already offers Word and ODF)
- The LLM settings description for Meeting summary now recommends gemma4:26b or
  larger, with qwen3.8:27b as a measured option when video memory is limited

## [1.15.84] - 2026-09-19

### Meeting summary: clicking a block in the distribution jumps to that moment

The whole row shared one segment number (the speaker's first turn), so every
block jumped to the same place — while what the reader sees is a block at a
particular moment. Each block now carries its own segment number; clicking the
empty track still falls back to the row's first. Hovering shows the time and
segment for that block.

### Meeting summary: the longest topic row no longer wraps its figures

The bar competed with the text for width, so on the longest row the bar filled
the space and the percentage and duration wrapped to a second line. The bar is
now drawn inside a fixed-width track — the text always has room, and every row's
proportion is measured against the same track rather than varying with the
length of its label.

## [1.15.83] - 2026-09-19

### Meeting summary: exports to Word and ODF, with a choice of layout theme

Besides PDF, the record now exports as **Word (.docx)** and **ODF (.odt)**, so it
can be edited further or dropped into a company template.

The download row gained a **layout theme** selector with six options (clean,
GitHub style, academic, book, business report, minimal), defaulting to business
report.

All three formats and all six themes come straight from "Markdown to office
document" — the renderer and the theme definitions are shared. Writing a second
layout engine here would produce something worse, and themes added there would
not appear here.

## [1.15.82] - 2026-09-19

### Fixed: the exported charts had not followed the screen

The previous version merged the speaker bar chart into the table and replaced it
with a "speaking distribution" column, **but exports use a separate set of charts
rendered on the server** — so the page showed the distribution while the PDF still
had the old bar chart, with unlabelled speakers still shown as `unknown`. **The
same thing written in two places always drifts**; this time it drifted between
screen and export.

The server now draws "who spoke when" as well (one row per person, a block
wherever they spoke), and the exported table matches the page's ordering and
column names. When only the analysis is available and there are no segments (the
public API), it still falls back to the bar chart.

### Meeting summary: citations show just the number

The cards are narrow and an item often carries three or four citations — in
"segment 10", two of the three words are decoration repeated on every chip. The
full wording stays in the tooltip and the screen-reader label.

## [1.15.81] - 2026-09-19

### Meeting summary: the length bar now sits close to its topic title

A title and the bar beneath it are one thing; the gap between them made them read
as two rows. The gap came from two places — the row's line height (1.5, meant for
body text, which leaves three or four pixels under the title) and the bar's own
top margin. **Adjusting only one of them is not enough.**

## [1.15.80] - 2026-09-19

### Meeting summary: charts no longer run off the page in the exported PDF

Images were placed at their natural pixel size, and the discussion map is 980px
wide — **the right-hand side was cut off**. `max-width: 100%` does nothing (the
Office engine's HTML import ignores that CSS rule; measured, it still used 980),
so the HTML `width` attribute is used instead.

**Constraining the width alone is not enough**: scaled to the page width, the
discussion map is still taller than one page, and the engine does not paginate an
image — it places it and clips whatever does not fit (measured: the image bottom
was 57pt past the page). Tall charts are now sliced so each piece fits a page.

### Meeting summary: chart headings no longer appear twice

The chart draws its own title, and the surrounding Markdown added a heading with
the same text.

## [1.15.79] - 2026-09-19

### Fixed: the meeting summary PDF export had never once worked

Reported as "it looks great on screen, why is the exported PDF so bad". What came
out was three pages of charts with **not a single line of text**, on pages sized
980×2640 — the charts' own dimensions, not paper.

The conversion helper takes the **destination file** as its second argument and
returns `None`. The old code passed a directory and treated the return value as
the produced file, so that check **never once succeeded** and every export fell
back to charts-only. And since returning `None` is not an exception, the `except`
never fired — **nothing was logged, and the download was always 200**.

The Markdown was also being handed straight to the Office engine, which **does not
read Markdown**. It is now rendered to HTML first (sharing the renderer with
"Markdown to office document"), and the export is a document with a title,
summary, decisions, actions, risks, topics and the speaker table.

**Falling back now always leaves a warning** saying which step failed — a
charts-only PDF and a complete one look identical from the outside: a file
downloaded.

### Meeting summary: the document title no longer carries the file extension

### Topics timeline: "chapters" renamed to "topics"

"Chapter" is borrowed from video and books; nobody says it about a meeting. The
card is now "Topics over time" and the chart below it "How long each topic took".

That chart's heading had also **never been translated**: it was written as
`tr(cond ? 'A' : 'B')`, so the key is computed at runtime and the extractor can
never see it. The heading moved to HTML with each branch translated separately —
which is also where a section icon fits.

## [1.15.78] - 2026-09-19

### Fixed: opening a meeting summary from "My jobs" showed nothing

Coming back with a job id in the URL, the job had already finished — the progress
bar said "done" but **everything below it was empty**, as if the result had been
cleared.

Reading the result needs the upload id, which only exists during the upload
itself; on a fresh page it is empty, so that code **silently did nothing**. The
job records that id, and it is now retrieved before reading the result.

### Meeting summary: the speaking distribution is now part of the table

It used to be a chart on the left and a table on the right, which had to line up
row by row — same order, same row height, same starting point. Row height depends
on the font and the browser, so even measured alignment was off by a few pixels,
and "off by a little" is exactly what misalignment looks like.

Merged, the alignment is guaranteed by structure: a row is a row. The
distribution column is positioned in percentages, so it follows the column width
on its own. Clicking a row jumps to where that person first spoke.

## [1.15.77] - 2026-09-19

### Meeting summary: the speaker chart is now "who spoke when"

It used to be a bar chart of who talked most — which **the table beside it
already says**, so drawing it again was the same numbers in another shape.

It is now a timeline with one row per person: wherever they spoke, there is a
block. Three things the table cannot show are visible at a glance — **who
dominated** (most ink in that row; the share is still readable), **the rhythm**
(spread through the meeting or concentrated in one stretch), and **who was absent
from which part** (the gaps).

It works without timestamps too: the horizontal axis becomes position in the
transcript, which is still the progress of the meeting, just a different scale —
and the chart says which one it is using.

### Meeting summary: the numbers to the right of the charts are now aligned

Percentage, time and turn count were one right-aligned string, so the first
column was pushed around by the width of the last (`9.0%` and `13.8%` differ by a
character, which skewed the whole column). Each is now its own right-aligned
column, in tabular figures.

### Meeting summary: the background field is styled

It only had width, height and font size — no border, corners or padding, so it
looked like a raw browser box.

### Meeting summary: unlabelled speakers no longer show as `unknown`

`unknown` is a placeholder the analysis inserts, not a name. The data keeps it
(statistics, citations and exports all key off it); the screen now says
"Unlabelled speaker".

## [1.15.76] - 2026-09-19

### Meeting summary: the four statistics now fill the row evenly

Their width followed their contents, so the four boxes were different sizes,
bunched to the left and did not line up with the full-width fields below them.
They are now an even grid that fills the row and wraps when narrow.

### Meeting summary: the layout hint no longer has nested parentheses

The layout names already contain parentheses, so wrapping them added a second
level. The hint now reads "Detected: …" without the outer pair.

## [1.15.75] - 2026-09-19

### Meeting summary: the speaker table now has grid lines

Each row carries five numbers, and with only a very faint bottom rule and no
vertical lines the eye could not follow across. Horizontal and vertical rules,
alternating row tint, and a row highlight on hover were added.

### Meeting summary: the table's "share" column is now "share by characters"

The chart beside it is based on **speaking time**, while the table's share has
always been based on **character count** — seeing 23.1% and 21.8% next to each
other for the same person looked like one of them was wrong. The column heading
now says which it is.

## [1.15.74] - 2026-09-19

### Fixed: downloads in Meeting summary blocked the whole site

PDF / chart PNG / ZIP are **generated when you press the button** (rasterising
the charts, zipping), and that CPU work ran directly on the web server's event
loop — while one person downloaded, **nobody else could even open the home
page**.

It now runs on a worker thread. The existing "endpoints must not block the event
loop" check cannot see this shape (the heavy work is inside called functions), so
this endpoint is pinned in that test's explicit list.

### Meeting summary: download buttons now say "Generating…"

They were plain links, so nothing on screen changed until the file arrived — it
looked like nothing happened and people pressed again. The button now switches to
"Generating…" with a spinner and blocks repeat clicks until the file is ready.

They also fetch the file themselves now, so server errors are shown as a message
— previously the error page was saved as if it were the file.

### Meeting summary: the chapter timeline spine was broken into segments

The spine is drawn per row and only stretched to the row's *content* box, so the
row's vertical padding left a gap between every pair of rows. The overhang is now
tied to the same variable as the padding, so changing one cannot break the other.

## [1.15.73] - 2026-09-19

### Meeting summary: the upload card now follows the actual flow

The background field used to sit **below** the submit button, so you only saw it
after pressing. The order is now "paste the transcript → background → button",
with the button last.

The button is renamed from "Use this text" to "Parse transcript": what it does is
parse the content into segments and speakers (pressing it shows the segment
count, speaker count and the first few lines), and the label should say so.

## [1.15.72] - 2026-09-19

### Meeting summary: the background field looked like a separate block

It was already inside the "1. Upload transcript" card, but it had its own border
and background, so it read as **a card inside a card**. The box is gone; a single
divider line now separates it — the same visual weight as "or paste the
transcript directly", so it clearly belongs to the same card.

## [1.15.71] - 2026-09-19

### Meeting summary: you can now supply meeting background

There is a new optional field below the upload area (collapsed by default) for
**information that is not in the transcript** — the topic, date and place,
attendees and their roles, terminology, or anything else you want to say.

It makes the analysis more accurate: knowing who manages and who does the work is
what lets "please ask X to handle it" resolve to an owner, and knowing your system
codenames and abbreviations keeps the wording intact.

**The background never becomes a source of items.** What you write there did not
happen in the meeting; if it could produce a decision, this tool's guarantee that
every item traces back to the transcript would be broken — invisibly. Three
defences:

1. The prompt says so, and **our rules bracket the user text on both sides** —
   what you type could read like an instruction, so we must have the last word.
2. **Citation verification**: an item must point back to transcript segments, and
   the background is not part of what is compared.
3. **Anything that closely matches the background and matches it noticeably
   better than the transcript is dropped.**

The third was added while writing the tests — the second turned out not to be
enough on its own. What people put in the background (an agenda, a topic list) is
exactly what the meeting was about, so a decision copied from the agenda still
overlaps the transcript enough to pass the threshold.

> This does not catch a sentence the model builds by blending background and
> transcript wording. The background is still an input to treat with care.

The public API accepts it too (`context`, up to 4000 characters).

## [1.15.70] - 2026-09-19

### Meeting summary: a pass over the charts and layout

- **Chapters are now a timeline**: a spine down the left, a dot per chapter, and
  a length bar next to each title. The old two-column "time | title" table left
  most of the width empty; the same data now also shows which part ran longest.
- **Hovering a bar lights up its legend row and dims the others.**
- **The speaker chart and its table now sit side by side**, stacking when narrow.
- **The post-upload statistics are now tinted blocks** (segments / speakers /
  characters / duration).
- Legend text is one size larger.

### Fixed: chart text was sometimes too large, sometimes too small

Both reports arrived the same day, and **they had the same cause**: the width a
chart was drawn at did not match the width it actually occupied, so CSS scaled
the whole chart — text included.

Too large came from the discussion map being **still hidden when its width was
measured** (measured 0, fell back to a default, then got stretched 1.7x); too
small was the reverse.

Charts are now drawn in real pixels, and **measured again after drawing**: if the
actual width differs by more than 2%, the chart is redrawn once at the real
width. There are many ways to mismeasure (the container still opening, fonts
still loading, a scrollbar taking a few pixels), so rather than plugging each
one, the chart checks itself in the mirror.

### Fixed: hovering the items on the right of the discussion map did nothing

The chapters on the left highlighted; the items on the right did not. Those boxes
have a white background, and the effect was an opacity change — **which is
invisible on a white page**. It now changes brightness, which shows on both white
boxes and coloured bars.

### Fixed: an extra "Download .json" button on the progress bar

The meeting summary result panel already has a full row of download buttons (PDF
/ Markdown / chart PNG / ZIP / JSON / hand off), and the shared progress bar
added another one labelled "Download .json", which looked like something else.
Tools can now tell the shared progress bar not to show that button.

Spacing was also added between the progress bar buttons and the card below them
(this applies to every tool with background jobs).

## [1.15.69] - 2026-09-19

### Fixed: pressing "Stop" did not look like it stopped

After pressing stop the timer did freeze, but **the progress bar stayed where it
was, the status still read "extracting 4/5", and the "you can close this page"
note was still showing** — indistinguishable from a hung job.

Stopping only did two things: stop polling and tell the server. The code that
paints the "stopped" state was **on the polling path** (it ran when the server
reported the job as cancelled), so once polling stopped it could never run.

Pressing stop now immediately sets the status to "Stopped", greys out the bar and
takes down the "you can close this page" note. **The elapsed time is kept** — how
long it ran before stopping is useful information. This is the shared progress
bar, so every tool with background jobs benefits.

### Fixed: the paste box in Meeting summary printed `&#10;` in its hint text

The hint text of the "paste the transcript directly" box showed a literal
`&#10;` instead of a line break.

The hint goes through the translation helper, and **translated text is
auto-escaped** — the `&` became `&amp;`, so the browser displayed the six
characters. All three language catalogues had copied the same markup, so all
three were affected.

## [1.15.68] - 2026-09-19

### Meeting summary: charts are now drawn in the browser and are clickable

The three charts (discussion structure, chapter share, speaker share) used to be
images rendered on the server, so **nothing in them could be clicked** — even
though the whole point of this tool is that every item traces back to the
transcript. They are now drawn in the browser:

- **Click any part of a chart and the transcript jumps to that segment and
  highlights it** (the same logic the citation buttons use)
- They are drawn to the width of their container and redrawn when the window
  resizes, so there is no large empty gap on wide screens
- Exported PNG / PDF and the images embedded in the exported Markdown are still
  rendered on the server

### Meeting summary: the right-hand side of the discussion map is no longer empty

Decisions and action items cluster near the end of a meeting, so out of ten
chapters only three typically have anything attached — leaving the right half of
seven rows blank, which looked like a broken chart. Those chapters genuinely
produced nothing, so rather than hiding them or inventing a node, they are now
drawn as a full-width bar with "no decisions or actions" noted on the right. You
can tell at a glance which topics were discussed without producing an outcome.

### Fixed: chapter share said there were no timestamps when there were

In a plain-text transcript, timestamps are `16:19`-style markers at the start of
a line, and **not every line has one** (the header block, lines like
"(brief silence)", and the final segment never get one). Chapter times were
computed from "the start of the first segment" and "the end of the last segment",
so if a chapter happened to begin or end on such a line, that whole chapter had
no time — and a single chapter without a time made **the entire chart fall back
to segment counts**, displaying "this transcript has no timestamps".

Chapter times are now taken from the earliest and latest timestamps that are
actually present in the chapter.

### Meeting summary: the transcript is fully expanded, card headings stand out

The transcript used to be trapped in its own scrollbar, so jumping to a citation
meant scrolling inside a small window and the page scrollbar never reached the
later content. It is now fully expanded. The headings of the four cards
(decisions, actions, risks, open questions) are larger, have a background tint
and a coloured left edge, and the counts are shown as badges.

### Line endings in Windows batch files now have a guard

`setup-python.cmd` (the batch file that sets up the Python environment during
Windows installation) **must use CRLF line endings**. With LF endings, cmd.exe
fails token by token, so anyone installing from the tarball on a machine
without git gets a failed install. This was fixed in v1.12.8 / v1.12.10 with
three separate safeguards (normalise before running, convert the file to CRLF,
declare the rule in `.gitattributes`) — but **none of them had a test**.

`tests/test_script_line_endings.py` now checks that every line of `.cmd` /
`.bat` ends in CRLF, that `.sh` files contain no CR at all, and that the three
`.gitattributes` rules are still there (that is what protects people who clone).

People who clone never see this class of problem: checkout converts the file
back to CRLF, so it always looks correct on a development machine.

### Other

- The deployment tarball is now built from an allow-list and verified after packing
- Housekeeping in internal tools and guard tests

## [1.15.67] - 2026-09-18

### Fixed: all four download buttons in Extract text failed

If the uploaded filename had a space before the extension (`minutes .pdf`, which
is common when the name is copied from a web page), the TXT / Markdown / Word /
ODT downloads **all failed**, and "Save to workspace" broke with them. The
browser saved the error message as a file; the save dialog showed `txt.json`.

The cause was that the file was **written under the original name but read back
with the surrounding whitespace stripped**, so the two paths did not match. The
message said "expired", which looks like the file was cleaned up rather than a
filename mismatch.

Downloads now also carry a plain ASCII fallback name: when only the UTF-8 name is
sent, some browsers fall back to the last part of the URL.

### Extract text: one-click copy on the preview

It copies what the preview shows (the first 5000 characters); the button says how
much was copied and points to Download TXT for the whole thing.

### Meeting summary: a lot changed in this release

**Transcript layout is detected automatically, and you can override it.** Eight
common layouts are supported: a speaker header on its own line with the text
below, one utterance per line, times at the start or in brackets, bullet-led
lines, half-width colons and so on. The detected layout is shown, and you can
pick a different one when the guess is wrong.

**You can paste a transcript directly** instead of saving it to a file first.

**Very long turns are split.** In committee records one person often speaks for
minutes at a time, which used to be a single segment of twenty thousand
characters: every citation pointed at "segment 1", and clicking it showed a wall
of text, which is no citation at all. Segments are now capped at 400 characters
(measured: a 26,907-character record went from 4 segments to 78).

**Long meetings no longer go wrong.** What was sent to the model used to grow
with the length of a turn, and once it exceeded what the model can read it was
**silently truncated** — it looked successful while only part of the text had
been read. What is sent is now bounded, and the review pass runs in batches so a
failed batch costs only that batch.

**The mind map, speaking share and chapter share are now drawn on the server**,
so the page, the Markdown and the PDF all use the same image. The mind map is
**assembled, not generated**: every node comes from an entry whose citation was
verified, so each box points back to the transcript.

**"Who spoke how much" works without timestamps** (turns and characters); the
whole section used to disappear. Chapters likewise vanished without timings.

**New exports**: PDF (the whole minutes), charts as PNG, and a ZIP with the
Markdown plus images. The Markdown download embeds the images, so they survive
being handed to "Markdown to office document".

**Fixed**: the "Send to Markdown to office document" button did nothing. Every
section and card now has an icon, and the export buttons are spaced away from the
content below them.

### The home page says how many tools there are

"An integrated PDF / Office document platform, **49** tools" — the number is
computed, so it follows along when tools are added.

## [1.15.66] - 2026-09-18

### Fixed: turning on "enforce API tokens" froze the web interface (issue #52)

With enforcement on, the `/api/` requests the web interface makes for itself were
all rejected with 401: progress polling, cancelling a job, notifications, the
inbox, the workspace list. **The pages themselves still opened, and jobs really
did finish in the background**, so it looked like "the progress bar is stuck" or
"the button does nothing", nothing like a settings problem.

The check recognised only a **hard-coded list of paths** (the admin area and two
preview endpoints); everything else counted as an outside API call. It now looks
at whether the request carries a logged-in **session** instead: if it does, the
normal permission checks apply; only requests without one (scripts, `curl`) are
blocked. There is no list to maintain any more, so new endpoints cannot be
missed.

The setting's description was also corrected: **it has no effect while
authentication is off**, because with no identity required anywhere there is no
way to tell the web interface apart from a script, so both are let through. To
actually restrict outside callers, turn authentication on first. The old wording
said calls "are rejected with 401", which was not true in that mode.

## [1.15.65] - 2026-09-18

### New tool: meeting summary

Turns a meeting transcript into a **summary, decisions, action items, risks,
open questions and chapters**.

**Every entry points back to the segment it came from and who said it** — click
it and the transcript jumps to that line. This is not decoration: minutes get
used as the record of what was agreed, and **a decision with no source is worse
than no decision at all**. Anything that cannot be found in the segment it
claims is dropped, and the result page tells you how many were dropped.

Accepted transcripts: subtitles (`.vtt` / `.srt`), transcript JSON, plain text
(`.txt` / `.md`), Word and ODF (`.docx` / `.odt`). For plain text, one utterance
per line; a `Name:` prefix is understood, and so are leading timestamps.

**After the upload and before the analysis starts you see what was parsed** —
segment count, speakers, the first few lines. If the speakers came out wrong you
find out before spending the minutes, not after.

When timestamps are present it also computes the **speaking share** directly from
them (overlapping interjections counted once), not estimated. **Without
timestamps that chart and the chapter timeline simply do not appear**, and the
page says why — a guessed number would be worse than none.

Also: the analysis can be stopped; it finishes even if you close the tab, and
"My jobs" takes you back to it; the Markdown download can be handed straight to
"Markdown to office document" for layout. Requires LLM to be enabled in the
admin area.

Tool count 48 to 49.

## [1.15.64] - 2026-09-18

### New `/readyz`: "the service is alive" and "nothing is missing" are two questions

When a tool failed to load — usually a missing dependency — it left one line in
the log and then quietly disappeared: one fewer entry in the sidebar, 404 on its
URL, and `/healthz` still answering `{"ok":true}`. Administrators had nowhere to
see it.

`GET /readyz` is new (no login required, same as `/healthz`):

- Tools missing → **200 with `degraded: true`**; the remaining tools still work
- Data directory not writable, or the database unreachable → **503**, because
  that is a service that genuinely cannot do its job
- It never returns module names, exception text, or file paths — the endpoint is
  public

**Which tools failed and why** is shown at the top of the admin **System status**
page (administrators only), and takes up no space at all when nothing failed.
After installing the missing package, restart the service for the tool to load.

`/healthz` is unchanged and deliberately ignores tool loading: service managers
use it to decide whether to restart, and restarting in a loop because one tool is
missing is worse than the problem. For the same reason `/readyz` does not return
503 for missing tools — this product runs as a single web process, so marking the
only instance unhealthy would show visitors a site-wide error page.

Monitoring setup is documented in `OPS.md`. This came out of an external source
code audit.

## [1.15.63] - 2026-09-18

### Markdown to office: runs in the background and can be stopped

It used to be a synchronous request — all you could do was watch "converting…"
with no way to stop, and closing the tab threw the work away.

There is now a progress bar and a **Stop** button, the job finishes even if you
close the page, and "My jobs" → **Open** reconnects to it. Stopping takes down the
whole tree of conversion processes (the mechanism added in v1.15.62) rather than
just hiding the progress bar.

Finished jobs now also offer a download in "My jobs", named after the title you
entered.

## [1.15.62] - 2026-09-18

### Fixes a regression from v1.15.61: code blocks ran off the page

To put space between code and its frame, the previous release wrapped code blocks
in a single-cell table. The result was that **long commands stopped wrapping and
the whole block was pushed past the right edge of the page, cutting content off**.

Content being cut off is far worse than text sitting against a frame, so that
approach has been reverted. Three ways of getting the spacing were measured
(`padding`, an outer container, a single-cell table) and the conversion engine
honours none of them. Code blocks are now an indented tinted band with no frame —
with no frame there is nothing for the text to touch, and the indent separates
code from prose clearly.

Table borders are unaffected and remain as added in the previous release.

### Cancelling a job now actually stops the work

The stop button in "My jobs" and on tool pages only changed the status to
"stopped". A conversion spends its minutes waiting on an external program, so the
cancellation checkpoints in our own code were never reached: the screen said
stopped while **the server finished the conversion anyway**, still using CPU and
memory.

Cancelling now stops the entire tree of external programs that job started. This
applies to every tool that shells out (text recognition, all conversions).

Before stopping anything the process identity is verified — process numbers are
reused on a long-running service, and without the check there is a chance of
stopping something unrelated.

## [1.15.61] - 2026-09-18

### Markdown to office: tables now have visible borders, code no longer touches its frame

Converted tables had no borders and awkward column widths, and text in code blocks
sat right against the surrounding frame.

The conversion engine supports only a narrow slice of CSS: `border: 1px solid …`
draws **nothing at all**, and `padding` on a code block is treated as an indent
(frame and text both move right, with no gap between them). Switching to HTML
presentational attributes makes it reliable — table borders now measure 0.75pt
(clearly visible) and code blocks have roughly 6pt between the text and the frame.

The same document also lost a page (27 to 26), and the two overlapping sets of
borders — one from the attributes, one drawn as hairlines from CSS — no longer
fight each other.

## [1.15.60] - 2026-09-18

### Markdown to office: no more near-empty pages in the PDF

Converted PDFs contained pages holding only a line or two, with tables broken so
that a single row sat alone on a page. The cause: the HTML was converted using
**web-view layout** instead of document layout. On the same file that meant
36 pages with 3 near-empty ones; with document layout it is 25 pages with none,
and the median characters per page went from 687 to 1029. Table column widths
are better too.

The other two outputs (.docx / .odt) already used document layout — only the PDF
path had been missed.

### Markdown to office: choose which formats to produce

Every run used to produce PDF **and** DOCX **and** ODT. Each format runs the
conversion engine once, the engine is serialised, and each run is capped at
120 seconds — so on a busy host a few tens of KB of Markdown could take over two
minutes and then fail, while the screen promised "10-30 seconds".

There is now a format picker, defaulting to PDF only. The page also states that
previews are rendered from the PDF, so without PDF there is no preview. The
public API still produces all three when no format is given, so existing calls
are unaffected.

### Markdown to office: syntax highlighting in code blocks

Code blocks are coloured according to the language tag, and inline code keeps its
colour. The **Minimal black & white** theme deliberately stays uncoloured — a
completely neutral look is the whole point of that theme.

### A conversion that timed out did not actually stop

On timeout only the outer launcher was stopped; the process actually parsing the
file survived, kept burning CPU, and never exited on its own. Because conversion
processes are deliberately given low priority (to keep the web UI responsive),
each leftover process made every later conversion slower and more likely to time
out. The whole process tree is now stopped.

This affects all seven conversion paths (office to PDF, office to image, Markdown
to office, and so on).

### The timeout message no longer points in the wrong direction

When a Markdown conversion timed out, the message said the file might be damaged
and suggested asking the sender for a PDF — but the intermediate file on that
path is generated by this tool itself and has nothing to do with what was
uploaded. It now explains that a timeout usually means a busy host or a large
document.

## [1.15.59] - 2026-09-17

### The `jtdt-reform` engine now reports progress page by page, like the other two

In `pdf-to-office`, `pdf2docx-refine` and `jtdt-layout` had reported per page for
a while; **`jtdt-reform` only ever reported twice** — at the start and at the end.
Large files take minutes, during which the screen does not move at all, and
"nothing appears to be happening" is the hardest symptom to diagnose: users
assume it has hung.

Both phases (reading the PDF, writing the document) now report per page. Measured
on a 20-page file: 41 updates, progress 0.07 → 0.95.

> **Progress is a side channel, not the output**: if the reporting callback
> itself raises, the conversion must still succeed. There is a guard that
> deliberately makes the callback blow up.
>
> **Written-down status needs checking too.** Our own notes claimed all three
> engines reported per page; reading the code showed only two did. What is
> written down becomes what the next person believes.

## [1.15.58] - 2026-09-16

### ⚠⚠ Document diff: inserting one page made every page after it look different

Pages were paired **by index** — old page N against new page N. Measured on a
20-page document with one page inserted at position 3: **only 2 of 21 pages
paired up, the other 19 became "whole page removed plus whole page added"**.
The user sees "the entire document changed" when in fact one page was added.

Pairing now uses **the same structure as the line diff, one level up**: a
sequence comparison anchors the unchanged pages one to one, and changed pages
sitting between anchors line up by position. The same document now reports only
the inserted page.

> **No fuzzy similarity is needed** — unchanged pages are byte-identical, so
> they make perfectly good anchors. A similarity score would only add a
> threshold to tune.

> After an insert the two page numbers drift apart, so the heading now says so
> ("old page 4 ↔ new page 5"), and the page view fetches images by the **real**
> page number rather than the row number.

### Teaching a field name now tells you what else that name maps to

The "learn this" button in the form filler writes into the **site-wide** field
name map. It used to just say "learned", so the user had no idea what they had
affected.

> **It was not changed into "refuse on conflict".** Reading how the lookup index
> is built showed the premise was wrong: **one label mapping to several fields is
> deliberate**. Taiwanese forms often have a single cell covering two things at
> once, so two fields each tick their own options from it. Refusing would
> silently break ticking on those forms **and no test would go red**. So the
> consequence is reported, not blocked.

## [1.15.57] - 2026-09-16

### Japanese de-identification gained driver's licence and health insurance numbers

Japanese documents were already covered (My Number and corporate number with
their check digits, phone, postcode, address, name). This release adds the two
remaining categories from the original plan.

**Both are recognised only as "label plus value", and only the value is masked:**

* A **driver's licence number is 12 digits — exactly as long as a My Number** —
  and there is no published check-digit algorithm to validate it. Without the
  label, any 12-digit run would have to be guessed as one or the other and
  **both guesses would be wrong, while the screen says "processed"**.
* **Health insurance numbers vary by insurer**: no nationwide length, no check
  digit. That pattern additionally requires a `番号` field to follow, otherwise
  an ordinary `記号` field in a normal document would match.

> The hyphen inside the value has to use the "any kind of dash" character class:
> what PyMuPDF extracts is a non-breaking hyphen `U+2011`, not `-`. The test
> fixtures hard-code `\xa0` and `\u2011` rather than characters typed by hand,
> because otherwise the test passes while real files match nothing.

> The false-positive corpus is still part of the acceptance: a Japanese document
> full of part numbers, order numbers and ISBNs must produce **zero** sensitive
> hits. Checking only that something is detected would also pass a pattern
> loosened until it matches everything.

### Hidden-content scanning now parses in a separate process (external audit F04)

The audit said "PyMuPDF upstream does not support multithreading". **Taking that
literally and adding locks solves the wrong problem**: we do not share
`Document` objects (every job opens and closes its own), the report itself never
reproduced a crash, and locks scattered across tools can neither be shown to
cover every entry point nor contain a parser crash.

What is worth doing is the **blast radius**: a segfault inside MuPDF's C code
takes down the **whole service process**, and with it every job in flight and
everyone currently using the site. Isolated, only that one request fails.

**Only the hidden-content scanner for now.** The criterion is "least trusted
input, widest damage if it falls over", and that tool exists precisely to answer
"this file might be dangerous, check it". Everything else is unchanged until
this has run in production for a while.

> **`subprocess`, not `multiprocessing`**: spawn makes the child **re-import the
> parent's `__main__`**, and we start the service with `python -m app.main` — so
> every isolated call would rebuild the entire service.

> **⚠ The cost measured while planning was wrong.** It measured "spawn + import
> PyMuPDF + open the file" at 385 ms and **left out our own import chain**. The
> first real measurement was **1.6 seconds**, because importing
> `app.tools.…` triggers the tool package's `__init__.py`, which pulls in the
> web framework and the settings chain (1,375 ms for that line alone).
> Extracting the scanner into a module that **imports only PyMuPDF**, and
> loading it **by file path**, brought the fixed cost down to about **0.5 s**.
> A guard now watches that module's import list.

> **Isolation is a safeguard, not a feature**: if the subprocess cannot start,
> the tool falls back to doing the work in-process. The exception type for a
> broken file is preserved across the process boundary too — losing it would
> turn a 400 ("your file is broken") into a 500 ("the server is broken"), and
> users would retry forever.

> **Both directions are tested**: the same crashing code must kill the parent
> when not isolated and must not when it is. Checking only "it survived with
> isolation" proves nothing — the crash might not have happened at all. A
> further test **posts a real request** to confirm the endpoints actually go
> through isolation: testing only the internal helper stays green even if the
> endpoint is reverted (the same hole the audit's F06 recorded).

## [1.15.56] - 2026-09-16

### The document diff gained a page view that marks the changes on the page itself

Until now there was only the text view: two columns of text showing *what*
changed but not *where on the page* it changed. There is now a toggle:

* **Text view** — exactly as before, untouched.
* **Page view** — the original page rendered on both sides, with the
  differences outlined in place. Red = removed, green = added, yellow = changed.

Both views share the same comparison result, so switching costs nothing.
Office files work too: they are already converted to PDF before comparing, so
the page view shows the **converted** layout.

> **The text comparison itself did not change.** Lines still come from
> `get_text("text")`; coordinates are read separately from `rawdict` and matched
> by "the Nth non-empty line". Across 30 real samples that matched on
> **101 of 101 pages**; a page that does not match gets **no boxes at all** —
> missing boxes only lose a feature, boxes in the wrong place mislead.

> The per-character ranges inside a changed line were **already being computed
> and then thrown away**. Keeping them is what makes "these characters changed"
> markable. Chinese has to be character-level: `get_text("words")` returns a
> whole line as one "word" when there are no spaces, so a one-character edit
> would outline the entire line.

### Rotated pages nearly got every box in the wrong place

Text coordinates and the rendered page are not in the same space. Measuring the
ink coverage inside the box, on the same page at each rotation:

| Rotation | Raw coordinates | Times `page.rotation_matrix` |
|---:|---:|---:|
| 0 | 22.7% | 22.7% |
| 90 | **0.0%** | 22.7% |
| 180 | **0.0%** | 22.6% |
| 270 | 4.6% | 22.6% |

Without the matrix the boxes land on blank paper while the page itself looks
perfectly normal, so nobody would notice. The acceptance criterion is therefore
**ink coverage inside the box**, not "a box was drawn": all four rotations are
checked, and mutation-verified (drop that one line and 90/180/270 fail).

### When there is no text layer, it says so instead of implying "no differences"

Scans, text converted to outlines and PDFs with a broken character map cannot
give coordinates. The page view now says so on that page and points at OCR.
"No boxes" and "this page did not change" look identical, so the sentence
cannot be left out.

### One piece of setup copied eight times; the eighth copy broke a whole test

The headless-browser tests each had their own copy of "find the browser" and
"pick a directory the browser can read". The new copy only compared path
strings instead of reading the file, so it could not tell that Ubuntu's
`/usr/bin/chromium-browser` is a **shell wrapper** around the snap build. The
fixtures were written where the browser could not read them; the upload
"succeeded", the filename even showed up, and only the submit failed — so the
whole test **skipped**, which looks exactly like success in pytest output.

It is now one shared `tools/browser_probe.py`, used by all eight, with a guard
against a ninth copy.

## [1.15.55] - 2026-09-16

### Tool renamed: "Document straightening" is now **Scan cleanup**

The old name had two problems. It **collided conceptually with "Page rotation"**
(both sound like they straighten a page, but that one only turns the whole page
90/180 degrees), and it did not say what the tool actually does: **crop, deskew
and even out the background shading**. The Japanese name had the same fault.

The new name puts it in the same family as "Scan merge". Japanese: `スキャン補正`.

> **The tool id and API path `doc-straighten` did not change** — changing an id
> means moving built-in roles, a database migration and every existing install's
> permissions, whereas this is only a display name. The old name stays in the
> search keywords so anyone who types it still finds the tool.

### The "copy all" buttons in both diff tools were never translated

`Copy all (old)` / `Copy all (new)` and their confirmation toasts showed Chinese
in the English and Japanese UI. They were written as an interpolated template
literal starting with a Jinja icon call, so the sentence as a whole could never
be looked up and nobody had wrapped it. **They only appear once a comparison has
finished**, which is why page-by-page scanning never saw them.

### Missing Office engine returned 500 from the document diff; it is now 503

500 means "the server is broken": users retry and monitoring fills with false
alarms. A missing soffice is a **deployment** problem, and the message should say
what to install. The project already had `OfficeUnavailableError` and a global
handler, but this tool caught it with a bare `except Exception` and wrapped it in
a 500, so **the handler never saw it**.

> The guard actually posts an Office file with soffice made unavailable and
> checks the status code, rather than grepping the source for "503".
> Mutation-verified: revert the fix and it returns 500 and the guard fails.

## [1.15.54] - 2026-09-16

### Uninstalling could leave an undeletable Start Menu folder behind

The folder name is also a path, and the registry only remembers **the one the
last install created**. So "install under language A, install again under
language B, then uninstall" orphans A's folder: it survives the uninstall and
nothing will ever remove it.

**This actually happened on the test machine** (2026-09-16): after uninstalling,
`Jason Tools Document Toolbox` was still there, with two shortcuts pointing at
an install directory that no longer existed.

Both sides are fixed:

* **Uninstall** now tries **every language's folder name** after the one in the
  registry (plus the pre-v1.15.30 hard-coded Chinese name), each behind the same
  "must live under `$SMPROGRAMS\`" check — we do not touch what is not ours.
* **Install** reads back the previously recorded folder and removes it when it
  differs, so reinstalling under another language does not leave two entries
  with no way to tell which one is live.

Verified on the machine: 2 folders before → install the fixed build → uninstall
→ **0**, with all four SQLite files byte-identical in size (user data intact).

> The guard walks the **declared language list**: every declared language must
> have its `SM_FOLDER_xx` *and* appear in the cleanup list — adding a fourth
> language turns it red first. Mutation-verified in five directions.

### Ternary expressions were only half-wrapped: 16 spots showed Chinese in the English / Japanese UI

When wrapping JS strings for translation we deliberately **do not wrap a whole
ternary** (the lookup key would then be computed at run time and never match,
silently). The correct form is to wrap **each branch separately**. The tool
that wraps strings automatically skips ternaries entirely, so "only one half
got wrapped" was a state nobody was watching.

A scan found **16 of them**, every one of which shows Chinese in the English
and Japanese UI: admin save results where only the failure half was
translated, right-click menu headings in `pdf-fill` / `doc-deident`, the
word-count reading time once it goes over an hour, the "N failed" tail in font
management, and the queue message in OCR that only appears after three
seconds.

**Page-by-page scanning cannot see this class** — those strings only appear
after a click, a toggle, or a value crossing a threshold. New guard
`tests/test_ternary_branches_go_through_tr.py`, mutation-verified four ways.

> **A scanner must not pair quotes with a regular expression.** The first
> version used `'[^']*'`; on a line mixing quoted strings with a template
> literal it paired two unrelated quotes and swallowed everything between
> them, giving both a false positive and a miss. Walking the line character by
> character (and splitting template literals, because `${…}` is code, not
> text) found 3 more real misses and removed the false positive.

### Company-profile field labels were untranslated in the English / Japanese UI

The company card on the `pdf-fill` page has **50-odd field labels** that never
had translations. The section headings were worse: the translations were
already in the catalogue, the display side had simply forgotten to call
`tr()`.

The display side now translates; **labels the user renamed are untouched**
(a miss returns the string as-is), and the editable label input on the admin
page is deliberately left alone — translating it would rewrite the user's own
field names on the next save.

### The demo-data seeder kept a second copy of those labels, and it had drifted

`tools/seed_demo_data.py` — which produces the screenshots we publish — kept
its own field-label map. It had drifted from the shipped one in **6 fields**,
two of them into mainland Chinese wording, which is on our own banned list;
the terminology guard simply was not scanning that directory. Both are fixed:
one source of truth, and the guard now covers it.

## [1.15.53] - 2026-09-16

### Scan an instance that actually has data, and tell data from interface

Every per-page scan so far ran against an **empty** instance, so "tables that
only appear once there is data" (the third square in TEST_PLAN §0.6) had never
been looked at. This one seeds demo data first: 71 findings in English, 9 in
Japanese.

**Read one by one, not one of them is a missing translation** — they are all
the user's own data: the field names and values on the company card, group
names and descriptions, the names of stamps, signatures and watermarks, every
cell on the company settings page.

Those are now marked `data-i18n="skip"`. **The company card in the form filler
matters most**: those field names are editable by the administrator *and* are
what the matcher compares against the Chinese labels printed on Taiwanese
vendor forms — translating them would make the matching fail silently.

> **"Lots of findings" is not "lots of missing translations."** Without that
> distinction nobody reads the report next time — and the real misses get
> ignored along with the noise.

## [1.15.52] - 2026-09-16

### Display attributes set from JS need `tr()` too

`title` / `placeholder` / `aria-label` / `alt` come from two places: the
template (`title="…"`, translated at render time — fine) and **JS at runtime**
(`el.title = '…'`). The second is invisible to the template scan, and because
it is not a text node the per-page scan only sees it if you hover.

Four were left, all interpolated template literals (`` el.alt = `第 ${n} 頁` ``)
— the same shape as last version's dialogs. Guarded by
`tests/test_js_set_attributes_go_through_tr.py`.

> The floor is **measured** (10 in practice, half of that as the minimum) —
> a round number would either always hold or fail on every edit.

## [1.15.51] - 2026-09-16

### The result area after you submit was still Chinese in English and Japanese

TEST_PLAN §0.6 says in as many words that the per-page scan **cannot see the
result area after a submit** — and nobody had ever scanned it. This release has
the **screenshot tool** scan it on its way past: it already uploads a real file,
submits it and waits for the result before taking the picture, so **what is on
its screen is exactly the missing square**.

The first run found leftovers in **11 tools** (the watermark preview status
line, the PDF editor's loaded message, the OCR upload message, the per-sentence
translator's loaded message, redaction's summary chips, the before/after titles
and page numbers in PDF-to-Office, the per-page download tooltip…).

Every one of them was the same shape: **the sentence is interpolated**
(`` `第 ${n} 頁` ``), and an interpolated sentence never matches a catalogue
key. Some were not wrapped in `tr()` at all.

> **`tr()` gained a fallback**: on a miss it replaces runs of digits with `{0}`,
> `{1}`… and looks that up, then puts the numbers back. That also makes
> **server-produced job messages** translatable (`完成（3 份）` is built on a
> background thread, where there is no request and so no way to know the
> viewer's language). A second miss returns the string unchanged, so the worst
> case is exactly today's behaviour.
>
> **Its boundary is a test too**: it only handles sentences whose variables are
> all numbers. `已上傳 a.pdf（6379.1 KB）` starts with a filename — that one has
> to be wrapped where the sentence is built.

> **Redaction's pattern names were half-fixed**: v1.15.47 fixed the label on the
> result row and **missed the summary chips**. One family, one sweep — again.

### Some dropdown options must NOT be translated

Whatever you pick in the personal-data stamp's "purpose" list is **printed on
the stamp verbatim**. Translating it would mean picking one thing and printing
another — and it would look completely normal.

That list is marked `data-i18n="skip"`, and **the scanner now honours skip on
`<option>` too** — it did not before, so those entries would have sat in the
report forever as false positives, and a report full of false positives is a
report nobody reads.

> **Marking the `<select>` was not enough**: every dropdown on the site is a
> custom widget, and **the list you actually see is a separate set of nodes**
> outside the native `<select>` — `closest()` never found the marker, so only
> the hidden native options were skipped. The widget now passes `data-i18n`
> through to the wrapper it builds.

Account and group names in the permission matrix are marked as data too (they
are the user's data; Chinese is correct there), while tool names, role names
and the word "group" **should have been translated and were not**.

### Dialogs: a static scan found 15 calls with no `tr()`

TEST_PLAN §0.6 files dialogs under "manual pass only" — but **a static scan
sees every branch**, including the ones only an error reaches. Three shapes:
an interpolated template literal, a plain string nobody wrapped, and — the
nastiest — **a ternary with only one side wrapped**, which looks handled.

The guard's criterion is "strip `tr('…')` out of the first argument; no Chinese
may remain". Checking "does it start with `tr(`" would flag
`cond ? tr(a) : tr(b)` and `err.message || tr(c)`, which are correct.

> **My own comment fooled my own scanner** (again): the note I wrote next to
> the fix quoted the wrong form as an example, and the guard flagged it.
> Strip comments first — and for templates take only the `<script>` blocks,
> since handing a whole HTML file to a JS comment stripper treats prose as code.

## [1.15.50] - 2026-09-16

### Memory admission now books what it just dispatched (audit F06)

The dispatch loop can start several jobs in one pass, and each one reads the
**current** free memory — but the one before it **has not allocated yet**, so
the second sees a stale number and both are admitted. At the default
concurrency of 2 that is one extra job (800 MB); an administrator who raises it
to 4–6 is off by 2.4–4 GB, which is exactly the OOM budget.

A dispatched job now holds a reservation for its estimate, and the reservation
**expires after a settle window (30 s)** — by then its real memory shows up in
the free-memory reading, and counting both would be double counting.

> **"Skip the check when nothing is running" stays as it was**: that is a
> deliberate trade-off (otherwise the queue never unblocks when memory is
> tight), and the code already says so. This only adds the half that was
> missing.

### It now says so when you run it with multiple workers (audit F11)

`OPS.md` has always said not to, but **nothing enforced it** — and the symptoms
(the same job dispatched several times, running jobs turning into "interrupted")
look nothing like a configuration problem.

Startup now logs an ERROR saying **how it worked that out** and **what will go
wrong**. It logs and continues: refusing to start would be worse than the
problem.

> The test was derived from uvicorn's source (it spawns workers with
> `multiprocessing.get_context("spawn")`, so a worker's `parent_process()` is
> not `None`), not guessed. `WEB_CONCURRENCY` is checked too.

### Other

* No source file may contain an invalid escape sequence. Python 3.12 only warns;
  **3.14 makes it a `SyntaxError`**, which would break collection for the whole
  suite. Both real cases were inside explanatory docstrings.
* The two installer guards now share one rule for which languages may
  legitimately contain Han characters.
* The per-page i18n scan gained `--reveal`: it expands panels and sections that
  are **already in the DOM but not displayed** before scanning, covering the
  "you have to open it first" category. Measured: English and Japanese, 82
  pages each, **zero** extra findings. **Nodes created at runtime** (a dialog
  that only exists once you click) are still outside what it can see.
* One label mapping to several canonical keys is **intentional** — Taiwanese
  forms often have one cell covering two things — and now has a guard. I very
  nearly "fixed" it as a defect.

## [1.15.49] - 2026-09-15

### A red frame along all four edges of the corrected page (customer report)

It looked like the corner detection had swallowed some desk. **It was not the
desk — it was a colour we painted ourselves.** Rotation filled the newly
exposed edge with

    cv2.warpAffine(..., borderValue=255)

and OpenCV's `borderValue` is a four-number `Scalar`: writing `255` alone
expands to `(255, 0, 0, 0)` — white on a 1-channel image (correct), **pure red
on a 3-channel one**. When v1.15.47 moved the geometry from grayscale onto the
colour image, that line quietly changed meaning.

Measured on the reported business card: **5,628 pure-red pixels → 0**, and the
mean chroma of the outer two-pixel ring **43.8 → 2.8** (the paper itself is
4.2). The four corners were right all along (ink coverage 1.000, purity 1.000).

The same thread uncovered an older ordering mistake: **flatten the illumination
before rotating, not after.** Rotation pads the corners, and that padding is not
photographed content — yet the background estimator treats it as "this area is
genuinely bright", which drags the gain field down and darkens everything else:
**6.9% → 64.7% dark pixels** on the same photo. The padding used to be red
(luminance 54), which happened to be masked as a dark region, so the wrong order
never showed.

### Dragging the four corners: the image jumped after the first one

The status line sits directly above the "before" image and wraps onto a second
row when the text is long. Every recompute reset it to a short "rendering
preview…", so **the image jumped 40–50 px upwards after each corner**, landing
the remaining three 19% too low.

The resulting quadrilateral was then rejected as implausible (interior-angle
spread 40.4°, limit 40°) and **dropped entirely** — while the screen still said
"using the corners you dragged". The status row now only ever grows, and it
remembers the tallest it has been rather than hard-coding a height (Chinese,
English and Japanese differ, and so does the window width).

### The installer: Japanese, and no more mojibake

**Mojibake** (customer report, Win11 25H2): `winget` writes UTF-8 while NSIS's
`nsExec::ExecToLog` decodes with the **system ANSI code page** — CP950 for
Traditional Chinese, **CP932 for Japanese**, CP1252 for English. All three
garble. That output was never meaningful to the reader, so it now goes to
`installer.log` and the pane keeps only our own one-line status.

**Japanese**: only Traditional Chinese and English language tables were
declared, and NSIS falls back to the **first declared** one, so a Japanese
Windows got a Chinese installer. Japanese now covers the component list, the
finish page, the failure message and **the three uninstall prompts** (that path
returns before the language dialog, so it is the easiest one to miss).

### Other

* The **document straightening** tool is now called `文件擺正` in Chinese; its
  URL and API path (`doc-straighten`) are unchanged.
* The changelog no longer quotes what a person said. The symptom stays — the
  quotation marks and the attribution go.

### Redaction now handles **Japanese documents**

"Japanese" joins the document-language list, with six Japanese-only categories:
**My Number**, **Corporate Number**, **phone**, **postcode**, **address** and
**name**. A Japanese interface now defaults to Japanese documents.

* **Anything with a check digit is verified.** Both My Number and the Corporate
  Number have one — without it every 12- or 13-digit string matches, so part
  numbers and order numbers are flagged in bulk while the screen says "done".
* **A false-positive corpus is part of the acceptance.** A Japanese document
  full of part numbers, order numbers, ISBNs and version strings must produce
  **zero** sensitive hits. Testing only that detection *works* would pass even
  if the patterns matched everything.
* **Replacement values are Japanese and never valid.** A fake number that
  passes its own checksum may belong to a real person (the same reason SSNs use
  the unassigned `9xx` range and the fake IBAN deliberately fails mod-97).
* **Taiwan and English did not regress** — the real corpora were scanned before
  and after and compared.

> **⚠ The separators a PDF gives you are not the ones you typed.** PyMuPDF
> returned a non-breaking space `\xa0` and a **non-breaking hyphen `\u2011`**
> (not `-`) for the very same file — matching on `[ \-]` found 3 of the 7
> categories, **while the screen said "done"**. This project already recorded
> the space half of this ("the whitespace a PDF yields is not an ASCII space");
> this is the hyphen half.
>
> Loosening the separators then introduced one false positive
> (`社内コード：03-1234-5678-X-99` read as a phone number) — the
> "must not be adjacent to a hyphen" half cannot be dropped along with it.
> Both are pinned as acceptance checks, each mutation-verified.

### ⚠ A dozen scanners' `</script>` regexes could skip a whole file

`</script  >` is valid HTML, and a regex that hard-codes `</script>` treats it
as *not yet closed* — **swallowing the rest of the file as script content**, so
that scanner silently stops checking. That is what CodeQL's `py/bad-tag-filter`
was reporting (11 High alerts).

There is now one shared implementation in `tools/source_text` (`</tag\b[^>]*>`),
and all 14 sites use it.

> This project hit the same family in issue #15: a literal `</script>` inside a
> comment closed the tag early and turned a page of JavaScript into plain text.

### PDF editor: your work survives a disconnect or a closed tab

The edit state used to live only in that browser tab. What the server holds is
an **already-flattened PDF**, not the edit state — and temp cleanup removes it
after two hours anyway, so getting it back would not let you move a text box.

Edits are now kept in the browser, and reopening **the same file** offers to
pick up where you left off.

> **It does not follow you to another computer** — the notice says so, because
> everything else this tool produces does live on the server.
>
> **An empty edit state must never overwrite a real draft.** Reopening a file
> snapshots the still-empty canvas, and 1.5 seconds later that snapshot wiped
> the previous draft — **before the user could click "resume"** (measured: one
> object saved, zero read back). Only running it in a real browser shows this;
> static checks see the code and call it present.

### PDF to Word: per-page progress

`Converter.convert()` is a single opaque call, and this tool routinely takes
minutes — the bar simply did not move. It is now split into the four steps that
call already performs, with per-page granularity taken from pdf2docx's **own**
log line rather than its internals. A four-page file now reports ten times.

> If upstream changes that line we **silently fall back** to stage progress —
> which is exactly why there is a check watching the format.
>
> **Broken progress must never fail a conversion**: it is an accessory, not the
> output.

### The site's screenshots are no longer empty states

"User management" and the permission matrix showed "no users or groups yet".
The demo data now has 6 accounts, 3 groups, and **the same account name in both
the local and ldap realms** — which is the whole point of that screenshot. All
of it is invented.

---

## [1.15.48] - 2026-09-14

### The site's top-left title wrapped in English and Japanese

**Measured**: the title needs 166 px in Chinese, 225 in English and **285 in
Japanese** when it is not allowed to wrap; add the nav bar and English needs
**1186**, Japanese **1188** — while the container is **1180**. It was over by a
handful of pixels.

The brand block is now a fixed two-line lockup: the first line is always
`Jason Tools` (identical in every language) and the description sits on the
second line. The three languages now need 919 / 1052 / 1002 px, with well over a
hundred to spare.

> **Not a smaller font, and not a higher breakpoint** — either only pushes the
> problem to the next language. The part whose length varies moved to where
> length no longer matters.

---

## [1.15.47] - 2026-09-14

### ⚠ The site's screenshots had never actually had a document in them

Not because anyone forgot to add one:

* `/usr/bin/chromium-browser` here is the **snap** build, and **it cannot read
  `/opt`** — which is where the sample files lived.
* `DOM.setFileInputFiles` still "succeeds" and the file name still appears on
  screen. **Only the eventual XHR fails, with `network error`, and the server
  never sees a single request.**

So the uploads in that capture had never worked — the English set sitting on a
"Please upload a PDF first" dialog was the same cause. Staging the samples
somewhere the browser can read them fixed every page at once.

> **The family lesson**: "it looks like it worked" (the file name is showing)
> is not the same as "it worked". The check is now *did the server receive the
> request*, plus *is a dialog covering the screenshot* (that gets printed).

### Screenshots: entirely made-up demo data, and the tools are really run

`tools/seed_demo_data.py` creates a demo company (Example Technology Co., Ltd.,
VAT 12345675, 02-1234-5678 …, **not one real value**), a demo seal, and a
synthetic vendor form. The auto-fill screenshot is a form with **18 fields
actually filled in** — nothing has to be blurred out any more.

Each page now has a recipe (which sample, whether to press the button, where to
scroll) and **waits until the result is actually painted** rather than sleeping
for a fixed number of seconds.

### Taiwan-only tools are no longer shown on the English and Japanese site

They are greyed out in those interfaces anyway; showing their screenshots only
suggests they can be used. Which ones to drop comes from the registry's
`ToolMetadata.locales` — not a hand-kept list — and the remaining figures are
renumbered, because 01 / 03 / 04 looks worse than one fewer picture.

### The site's language picker is a dropdown now

The nav bar read `繁體中文English日本語` run together, and the whole bar wrapped
onto two lines. Languages only ever get added, so a dropdown (whose width does
not depend on how many there are) is the right shape.

Three things changed in the nav at once:

* **Items no longer wrap** — wrapping broke them mid-word
  ("Why self- / host").
* **Shorter Japanese labels** (`インストール` → `導入`, and so on).
* **The hamburger breakpoint is measured, not guessed** (`chromium
  --headless`, per language): the bar needs 805 px in Chinese, 875 in
  Japanese, 933 in English; English stops fitting at 1060 and still fits at
  1100 — so **1080 px**. The old 980 was set from Chinese lengths alone.

### Document straightening: output was black and white without asking for it

The photo was a colour business card. The whole pipeline ran on the greyscale
copy (`crop_page(gray)`, `warp_quad(gray, …)`), so colour never survived — while
the checkbox on screen says "convert to black and white (off by default)".
**The interface promised something the code did not do.**

It now **measures on grey and transforms the colour image**. That also exposed
an RGB/BGR mix-up (PyMuPDF hands back RGB, `imencode` wants BGR) which would
have swapped red and blue.

> The check is **saturation**, not channel count: three identical channels
> still look black and white.

### Document straightening: you can set the output size (pixels or mm)

Automatic (whatever the corrected page comes out as) / A4 / A4 landscape / A3 /
Letter / custom width × height with a unit. After a preview the boxes are
**pre-filled with the size actually produced**, and once you edit them they are
left alone.

> **When the ratio does not match, proportions are kept and the page is padded
> with white — never cropped, never stretched.** This tool has said from day
> one that cutting into content is unforgivable while an extra strip of desk is
> merely ugly. Padding is white, not black (black would soak the printer). A
> malformed size is treated as "not set", never a 500.

### Document straightening: a superseded re-render is aborted immediately

There was a token that ignored *late replies*, but the request itself ran to
completion — **the server rendered a page nobody wanted**. It is now aborted
with `AbortController`.

> Being superseded **is not a failure**: saying "preview failed" suggests
> something broke when the next one is simply on its way.

### ⚠ Redaction: a Japanese interface was treated as Taiwan

v1.15.46 fixed the document tool; **the text tool still had its own copy** of
the old "anything but `en` means `zh-Hant`" rule — a Japanese screenshot caught
`4111 1111 1111 1111` being reported as a Taiwanese landline. Both now share
`patterns.default_doc_lang`, with an AST check that they really delegate.

> "**Sweep the whole family at once**" — the same lesson, twice in one day.

Two more from the same round:

* **Category labels were never translated** — that row was Chinese in both the
  English and Japanese interfaces, in both tools.
* **Address masking was hard-coded to a Taiwanese shape** —
  `1842 Maple Street, Springfield, IL 62704` came out as `OO市OO區OO路OOO號`,
  which reads as though the document had a Taiwanese address to begin with.
  Masking is meant to *keep the shape and hide the content*; swapping in
  another country's shape breaks the first half. Chinese addresses keep the old
  form; everything else is masked character by character, separators and length
  intact.

### Also

* "PDF to Word" is no longer labelled **beta**.
* **The upgrade notice now says the `-C` is upper case** (reported by a customer
  on 2026-09-14): typing `git -c /opt/jt-doc-tools/ …` answers
  `fatal: not a git repository (or any of the parent directories): .git`,
  because a lower-case `-c` is the *config* flag and git never changes into that
  directory. **The message reads as though the install were not a git checkout**,
  which sent the customer looking in entirely the wrong place. The caveat is in
  the README, `OPS.md` and on the site, with a check that no copy-and-paste
  block ever contains a lower-case `git -c <path>`.

---

## [1.15.46] - 2026-09-14

### Japanese interface added (site, API guide, troubleshooting and README too)

"Interface language" in the sidebar now offers `日本語`. The catalogue holds
**4,891 entries**, and there are four public documents in Japanese
(`index-ja.html`, `api-ja.html`, `troubleshooting-ja.html`, `README_ja.md`).

**The language switch became a list, not a toggle.** One button was enough for
two languages; with three, a reader standing on the Japanese page had no way
out to the English one. Every page now links to **every language but its own**.

> **The generators and the guards all read the language list from
> `ui_locale.SUPPORTED`.** Eight places used to hard-code `en` — those guards
> would have gone on **quietly checking English only** after a third language
> arrived ("scanned nothing" and "scanned everything and it was clean" look
> identical in pytest output).

**Three things only Japanese exposed** (none of them happen with English):

* **The terminology guard flagged the whole Japanese file.** Japanese `保存`
  and `字体` are correct Japanese, and both are on the Chinese banned-words list
  (Chinese wants `儲存` and `字型`). Nearly every Japanese line has kanji, so
  without an exclusion the report is all false positives — and **once a check
  is noisy, people start ignoring it**. The exclusion keys off the **file
  name** (`README_ja.md`, `index-ja.html`), because the translated files sit
  beside the Chinese ones and a directory rule cannot tell them apart.
* **"A translation must not contain Han characters" does not hold for
  Japanese.** Every Japanese translation contains kanji. The check now looks
  for Chinese words modern Japanese does not use (`這` / `嗎` / `什麼` / `沒有` …),
  which is the signal for "this entry was never translated at all".
  **`的` must not be on that list**: `一般的` and `自動的` are correct Japanese
  (the first version included it and produced three false positives on the
  spot).
* **A Japanese interface does not mean redaction handles Japanese
  documents.** The document language defaulted to "Taiwan unless the interface
  is `en`", so Japanese users silently got the Taiwanese pattern set — and
  Taiwanese landline / address / VAT-number patterns applied to another
  language do not *miss*, they **match the wrong thing**, while the screen
  says "done". Languages with no pattern set of their own now fall back to the
  language-independent group.

### Fixed: dropdown labels never went through translation

Scanning the Japanese interface page by page in a real browser found five
places. **Every one of them was Chinese in the English interface too** — the
two dropdowns on the translation glossary page had been wrong since it shipped
in v1.15.19:

| Where | What |
|---|---|
| Translation glossary | 12 language options |
| System status | Database names (audit log, VAT database …) |
| Document redaction **and** text redaction | Document-language dropdown |
| Document straightening | The resolution hints |
| Login page | Authentication source ("Local accounts") |

> **What they share is that the text comes from server data** — a guard that
> greps templates for a literal `tr('…')` cannot see any of it. The new
> `test_option_labels_go_through_tr` checks the expression inside every
> `<option>` instead. Genuine data (user names, tool ids, a language's own
> name) is exempt **with the reason written down**.
>
> **Sweep the whole family at once**: the two redaction tools each carry a
> copy of the same template and I fixed only one of them first.

### Fixed: one sentence on the site had its clauses swapped (in English too)

The "tools that need an Office engine" paragraph is split by `<b>` tags and
translated segment by segment, which put the verbs the wrong way round:
*"These tools need Word / Excel / PowerPoint / ODF when handling OxOffice or
LibreOffice"*. **Each segment's translation has to read correctly in its own
position.**

Japanese also gained a typographic rule: when an inline tag has Japanese on
**both** sides, the space between them is removed (the Chinese source often
leaves one because the tag contains Latin text), otherwise you get things like
`不要 です`.

### Fixed: the language cookie had no `Secure` flag on HTTPS sites (found by ZAP)

`/ui-locale` used `request.url.scheme == "https"`, and this project turns
uvicorn's `proxy_headers` off — **behind a reverse proxy that value is always
http**, so `jtdt_locale` shipped without `Secure` on an HTTPS site. It now uses
the shared `is_https_request()` (and so does the SSO transaction cookie, which
was looking at the redirect URI we send the IdP, when `Secure` is about **the
browser's leg** of the connection).

> **That helper's docstring already said "every cookie's `secure` must go
> through here" — there was simply no guard.** Almost every regression in this
> project comes back that way. The new `tests/test_cookie_secure_flag.py` walks
> the AST and checks every `set_cookie` / `delete_cookie` (**deleting needs the
> flags too** — `Max-Age=0` does not inherit them), plus a second check that
> the one exempt local variable really is computed from that helper; without
> it, someone hard-coding `is_https = True` would stay green.
>
> **Nothing had ever scanned that path before**: with Japanese added, the
> language switch became a group of links, and ZAP's spider POSTed to
> `/ui-locale` for the first time. **"The scan found nothing" and "there is
> nothing" are different claims** — a scan only proves the parts it reached
> were clean.

### Also

* The screenshot tool and the page-by-page scanner both take `--locale` now,
  and the Japanese site uses screenshots of the **Japanese** interface
  (`screenshots/ja/`).
* `tests/test_i18n_catalog.py` strips comments before harvesting `tr()` keys
  from JavaScript — a comment explaining the rule contained an example call and
  was counted as a real key. That is this project's recurring "the scanner was
  fooled by the very name it checks for"; this time it caught me.

---

## [1.15.45] - 2026-09-14

### Document straightening: every page of every file is now visible

There was only a **number input**: you had to type a page number, could not see
what pages existed or which ones you had adjusted, and once several files were
merged into one PDF there was no way to tell which page came from which file.

There is now a per-page thumbnail strip: click to switch and re-run that page,
with the current page highlighted, "rotated N° / manual corners" marked, and a
separator plus file name at the first page of each source file.

> **Which page came from which file cannot be recovered from the merged PDF** —
> only the upload knows, so `/load` now returns `sources` (pages per input file).
> Thumbnails reuse the existing `/thumb` endpoint (70 dpi, cached on disk) with
> `loading="lazy"`, so a 50-page document does not fire 50 requests at once.

### Document straightening: after rotating, dragging the corners produced garbage

When the status line showed both "rotated 90°" and "using the corners you placed",
the corrected output was wrong. **Two mistakes stacked:**

* the corners the user drags are on the **rotated** image (since v1.15.44 the
  "before" view follows the rotation), but the caller converted 0–1 to pixels
  using the **unrotated** dimensions — which are exactly swapped at 90°;
* and the core then rotated those coordinates **a second time**.

Measured at 90°: output **834×358** (should be 471×629) and **41.2%** dark pixels
— i.e. mostly desk rather than paper (correct value: 1.9%).

> **The fix is not to repair the two conversions, it is to have one coordinate
> system.** `quad` is now always "**normalised 0–1 in the rotated frame**", and
> detection moved inside `straighten_page` (after the rotation), so nothing is
> converted in between; `_rotate_quad` is gone. **With two coordinate systems,
> sooner or later someone converts on the wrong side.**
>
> The same root cause had a second, unnoticed branch: the auto-detected corners
> **returned to the browser** were normalised against the unrotated dimensions
> too, so after rotating, switching to manual placed the handles somewhere
> unrelated — while the screen showed four handles and looked perfectly normal.

> **Two layers of guard:** at the core, the output from "corners in the rotated
> frame" is compared with "detect directly on the rotated image" (size **and**
> dark-pixel ratio); end-to-end, a real browser rotates 90°, drags the corners,
> then **draws the corrected image into a canvas and measures the dark ratio**.
> Checking only where the outline sits is not enough — mutation testing confirmed
> this bug stays green that way.

### Document straightening: switching back to "detect automatically" did not

After switching to "place the four corners yourself", adjusting them and
switching back, the corrected image and the outline both stayed on the manual
version. Two causes stacked:

* switching modes only called `renderQuad()`, which merely shows or hides the
  overlay — **the preview was never re-run**;
* and even re-running would have used the stored manual corners.

> **The mode is a switch, not a delete key.** Automatic mode no longer sends
> those coordinates (both the preview *and* the submit must filter them — filter
> only one and the screen says "automatic" while the delivered file is manual,
> with nothing to show for it), but the coordinates are **kept**, so switching
> back restores the user's work instead of throwing it away.
>
> The test is that the status line after switching back is **identical** to the
> one from the first automatic run; merely checking "did it recalculate" would
> pass even when recalculating with the manual corners.

---

## [1.15.44] - 2026-09-14

### Document straightening: the four corners were **never** joined up

v1.15.43 made the outline thicker and the user reported it still was not there.
The cause had nothing to do with thickness:

* **`SVGElement` has no `hidden` IDL attribute** — the spec defines it on
  `HTMLElement`. `svg.hidden = false` just sets a property nobody reads;
  **the `hidden` attribute itself is untouched.**
* And `platform.css` has `[hidden] { display: none !important; }` — an *author*
  stylesheet with no namespace. The browser's own `html.css` is namespaced to
  HTML, ours is not, so **it hides SVG as well.**

So that quadrilateral has been `display:none` since the first version, with
**no JavaScript error anywhere**: the four drag handles are `<div>`s and worked
fine, so the screen showed dots but never lines — it read as "not built yet"
rather than "broken". Now toggled with `toggleAttribute`, which works for both.

> Two guards: a static one that scans the whole tree for `.hidden` used on an
> `<svg>`, and an end-to-end one that uploads a synthetic photo in a real browser,
> switches to manual mode and **measures whether the outline occupies any space
> on screen**. Checking that the `points` attribute is set would have stayed green.

### Document straightening: new "clean up" option, on by default

A shadow across half the sheet is the most common problem with phone photos.
**The test is text recognition, not whether it looks cleaner:**

| Case | Untouched | Cleaned |
|---|---:|---:|
| Hard one-sided shadow | **0.472** | **0.982** |
| Corner vignetting | 0.884 | 0.986 |
| Diagonal shadow | 0.967 | 0.986 |

> **Binarising is not cleaning up.** On the same material, local thresholding drove
> recognition down to **0.108** — and the version that *looks* cleanest is the worst
> one. It stays a separate, off-by-default option for shrinking files. CLAHE was
> also consistently worse.

> **`divide(image, local max)` cannot be used**: recognition is just as good, but it
> washes large dark areas to pure white — a dark grey photo block went from mean
> 66.3 to **254.9**, i.e. it vanished. Instead a slowly varying, bounded gain field
> is estimated, with unreliable (large dark) regions masked out and filled by
> `inpaint` **from their boundary**. Filling with a wide blur instead smears away the
> shadow's step edge and recognition falls back to 0.472.

> **Perfectly even scans are left bit-for-bit identical** (maximum change: 0 levels),
> which is what makes it safe to default on. The white-point step must be clamped to
> brighten-only; without that clamp a pure white scan is pushed down to 245 and 97%
> of pixels change.

### Document straightening: multiple files at once; rotation applies to the "before" view

* Several uploads are merged into one PDF **in upload order**, each page processed.
* After pressing rotate, the "before" thumbnail on the left stayed unrotated and no
  longer matched the corrected view on the right.

### PDF editor: warns when the file carries a digital signature

Editing and saving a new file **always invalidates the signature** — it covers the
whole file, so any change (even just re-saving) makes readers report "signature
invalid / document has been altered". The editor now says so up front and explains
that keeping the signature means using the original file or having it re-signed.

### Seam stamp: transparent padding around the stamp shrank it

With "stamp width 40 mm" we scaled the *whole image* to 40 mm — and stamps cut out
from a photo usually keep a transparent margin, so the actual ink was only
**26.8 mm** (with 25% padding), and **the same setting produced different sizes for
different source images**. All the user sees is "the stamp got smaller".

Compositing and the reassembled preview now share one `load_stamp()` that trims the
transparent border first, with a stray-speck threshold — a plain alpha bounding box
is anchored by the few semi-transparent dots left behind by background removal and
trims nothing.

> Other points from the same external review already held: the complete stamp is
> rotated before slicing, slice widths use running rounding (no gaps or overlaps),
> there is a "reassembled stamp" preview, there is no artificial jagged/torn edge,
> and alignment uses the `CropBox` rather than the `MediaBox` (now pinned by a test).

### PDF editor: that signature warning had no styling at all

It used `class="warn-box"` and my own comment claimed the class already existed site-wide. It did not — I invented it and then vouched for it. The full suite's `test_template_css_is_effective` caught it. There is now a shared `.warn-box` (the amber warning twin of `.info-box`), both living in `platform.css` so the next tool does not invent a third name.

### Seam stamp: click a preview to see it full size

Both the per-page previews and the reassembled stamp open full size, with arrow-key
paging and Esc to close.

> **Opening is not the same as enlarging.** The first version measured 300×424 on
> screen — the same size as the thumbnail, because the per-page preview is a 78 dpi
> render and showing it at natural size enlarges nothing. The endpoint already had
> `large=1` (150 dpi), so the full-size view now fetches that **on click** (rendering
> one for every page up front brings back the old "90 seconds per preview request"
> problem on a 52-page file). Measured: 645px → **1240px**.

> **No fourteenth copy was written.** A sweep found **13 separate lightbox
> implementations**, each with slightly different keyboard and close behaviour.
> There is now one shared `static/js/lightbox.js` (declarative `data-lightbox`,
> event-delegated so thumbnails added later are covered too) and a guard that stops
> new private copies appearing; the existing 13 are an explicit exemption list which
> is itself checked for staleness.

---

## [1.15.43] - 2026-09-14

### New: an install & upgrade troubleshooting page, linked from every failure

**A single line of error text leaves people stuck.** Most failures here have a
known answer — missing git, corporate TLS, not enough disk, moved tags, a service
holding files open, a reverse proxy hard-coding the protocol — but nothing pointed
at it.

* New page `docs/troubleshooting.html` (in both languages, **every entry is
  something that actually happened**: symptom, cause, what to do) with a clickable
  index at the top.
* `install.sh`, `install.ps1`, the Windows installer's failure dialog and every
  failure path in `jtdt update` now print that page's address.
* **The address follows the operating system's language** — Chinese systems get
  the Chinese page, everything else the English one. CLI text itself stays English.

> The English pages previously linked to the **Chinese** API page — one click and
> the reader was back in Chinese. Internal links are now rewritten for the English
> build, except the language switch itself.

### Document straighten: the four corners are joined by a visible line

`stroke-width: .6` with `vector-effect: non-scaling-stroke` means **0.6 pixels** —
invisible over a photograph, leaving just four dots. It is now drawn twice, white
beneath and blue on top, so it shows on both dark desks and white walls.

---

## [1.15.42] - 2026-09-14

### Document straighten: button placement and the state while recalculating

* **"Straighten" moved below the before/after comparison** — it is pressed after
  looking at the result, so that is where it belongs. The options panel keeps only
  the preview button, which is what produces the comparison.
* **Dragging a corner now puts the right-hand pane into "Recalculating…" with a
  spinner straight away.** It previously kept showing the *previous* result until
  the server replied, which reads as "nothing happened" and invites a second drag.

---

## [1.15.41] - 2026-09-13

### `jtdt update` could be blocked by tags that had moved

Tags move — a release gets re-tagged, or history upstream is rewritten. When the
local tag points at one commit and the remote at another, `git fetch --tags`
without `--force` reports `would clobber existing tag` for each and **exits 1**.
`jtdt update` treated that as a failed fetch, aborted the upgrade and restored the
previous state, saying only `git fetch failed` — so **every git-based install
would stop updating**, with nothing to suggest tags were the cause.

Measured on an untouched machine: `git fetch --tags origin` → **1**;
`git fetch origin` → 0; `git fetch --tags --force origin` → 0.

Branches and tags are now fetched separately: **the branch is required, the tags
are a bonus** — an upgrade only needs `origin/main`. A failed tag fetch prints a
note instead of stopping the upgrade, and tags are always fetched with `--force`.

> An install that is already stuck recovers with one command, after which
> `jtdt update` works again:
>
> ```bash
> git -C <install directory> fetch --tags --force origin
> ```

---

## [1.15.40] - 2026-09-13

### Dragging the four corners: the handles used the box, but the picture is drawn inside it

A user reported that the magnifier's crosshair pointed somewhere other than where
the corner actually landed. The before/after images are sized `height: 46vh` with
`object-fit: contain` so the two columns match in height, which letterboxes a
photo whose aspect ratio differs: measured, a 541×972 box held a picture drawn at
541×766, with 103 px of blank above and below. The handles, the quadrilateral and
the magnifier all worked in fractions of the **box**.

| | before | after |
|---|---|---|
| A handle at 40% height landed at | 37.3% of the image (**23.65 px out**) | 40.0% (0.0 px) |
| Pixel under the magnifier's crosshair | a different point from the handle | exactly the expected one |

This was not only a display problem: the same numbers are what get sent to the
server, so the crop followed the wrong edges.

The overlay now lives in its own layer positioned over the drawn picture using the
`contain` maths, recomputed on load, page change, mode switch and resize;
everything inside keeps working in normalised 0–1 coordinates. The magnifier
samples at 62 px rather than 64 — it is a 128 px border-box with a 2 px border and
the background origin is the padding box, so the crosshair sits at 62.

> No existing gate could see this: the elements are all there, no exception, no
> untranslated text, and a screenshot looks right. A new test drives a real
> browser, drags a handle to (0.30, 0.40) and **measures** where it landed and
> which source pixel the crosshair covers, to the pixel. The sample image is
> deliberately a different aspect ratio from the box — with a matching one there
> would be no letterboxing and the test would pass while verifying nothing.

### Transit receipts: Uber support

**Trip receipts only.** An Uber ride produces two PDFs in Taiwan, and the
e-invoice covers only the booking fee — a few dollars — because taxi rides
themselves are not e-invoiced. Uploading the wrong one would put 10 dollars in
the table instead of the fare, so the page now says which to use.

Extracted: date, pickup and drop-off times, both addresses, the total, plus two
new columns — **plate** and **distance** (hidden by default; distance is a
required field for taxi expenses at many companies).

> **The times must come from the pickup and drop-off pair.** The receipt carries
> four times — request, pickup, drop-off, payment (16:58 / 17:01 / 17:27 / 17:28
> in the sample). Taking the first gives the request time: three minutes out,
> entirely plausible, and nobody would notice. The rule is "the line after the
> time is an address" — only the pickup and drop-off look like that.

### Uber receipts carry no ticket number — a second ride the same day was dropped

Deduplication keyed on the ticket number, which every rail ticket has. Uber
receipts do not, so it fell back to transport + date + route + fare — and a
second ride on the same route at the same price was silently treated as a
duplicate. That is an ordinary commute.

The receipt does carry a unique identifier: the `riders.uber.com/trips/<id>`
link on page one. It is invisible to text extraction — it exists only as a link
annotation — so the extractor now collects link targets as well. If the link is
missing (a reprinted receipt), the departure time separates the rides instead.

### Document straighten: flat scans are no longer "perspective corrected"

Testing had used two phone photographs and synthetic samples. Measuring against
82 real scans and photographs showed the quadrilateral **cutting the header off
scanned documents**. Rather than tune the mask again, the rule now recognises
that such images have no perspective to correct: when the chosen candidate is a
perfect rectangle covering most of the frame, warping can only crop. Genuine
perspective photographs are unaffected.

> **A measurement needs a control too.** An early "darkest N% of the image is
> ink" metric scored two perfectly correct crops at 0.015 and 0.036, because in
> those photographs the desk is darker than the paper. Changing the denominator
> was guesswork; drawing the mask is what located the real problem.

---

## [1.15.39] - 2026-09-13

### A strip of desk survived the straightening — the mask, not the quadrilateral

A user reported that the corrected page still had the desk around it. Drawing the
mask made it clear: **the half of the paper lying in shadow was classified as
desk** (Otsu covered 32% of the frame where the paper occupies 40%), so the
quadrilateral only enclosed the lit half — and the minimum-area rectangle, in
pulling that back in, swallowed a band of desk.

| | old (Otsu + min-area rect) | new (Otsu ∪ chroma + scored candidates) |
|---|---|---|
| photo A | ink 0.738 / purity 0.909 | ink 0.737 / **purity 1.000** |
| photo B | ink 0.747 / purity 0.925 | **ink 1.000 / purity 1.000** |

Three changes: the mask gains a **chroma** layer (paper is neutral in Lab, a
wooden or coloured desk is not — and shadow changes brightness, not hue);
several **candidate** quadrilaterals are generated rather than one; and the
choice is made on two measurable numbers — **ink coverage** (how much of the
writing is enclosed) and **purity** (how much of the enclosure is really paper).
Ink is the hard constraint; purity is maximised under it.

> **The brightness threshold has to be relative to what is definitely paper, not
> to a percentile of the whole frame.** A synthetic heavy-shadow sample exposed
> it: shadowed paper sits at L=118 while the frame's 25th percentile is 122 — a
> large shadow raises the percentile until the rule disqualifies itself. It is
> now 0.45 × the median brightness of the paper; sweeping 0.30/0.40/0.45/0.55/0.65
> shows everything at or below 0.45 scoring perfectly and 0.55 collapsing.

### Two job tests waited on the wrong thing (**test-only change**)

Two consecutive full-suite runs each failed one test that passed on its own, both
with the same shape: wait for `status == "done"`, then read something that only
happens afterwards. The finishing order is deliberate — status, then persist,
then autosave — so under load the read lands in that window. Both now wait for
what they actually verify; a scan found no third instance.

### Editing the public `.gitignore` does nothing — it is regenerated on every sync

While adjusting the sync settings: `github/.gitignore` is written from a heredoc
by the sync script every time, so an edit to the file itself is silently reverted
on the next sync — green before the sync, red after. The rule now lives in the
sync script, with a guard that checks it there rather than in the generated
output.

A related point: `git ls-files` only sees files that are already tracked, so
anything dropped in but not yet committed looks clean to it while
`rsync -a --delete` would carry it into the clone. Checking that a class of file
stays out of the public tree has to look at the filesystem.

### Document straighten interface (reported from screenshots)

The "place the four corners yourself" option was a small checkbox nobody would
notice; it is now a two-card choice using the same pattern as the rest of the
site, as is the resolution setting. Hint text no longer wraps while space
remains beside it.

---

## [1.15.38] - 2026-09-13

### The same root cause, four times: classes that do nothing where they are used

`class="notice"`, `af-field` / `af-note`, `jt-select`, `btn-secondary` — four
separate places where markup referenced a class that **has no effect in that
context**. `.af-field` is only styled inside `.auth-form`; `jt-select` is a hook
for `custom_select.js` and is inert without that script. Nothing errors, nothing
logs, the element is there — the page just looks unstyled.

A new guard, `tests/test_template_css_is_effective.py`, resolves every class
used in a template against the stylesheets **and against the scope it is used
in**, so a selector that can never match is now a failing test rather than
something only a screenshot would reveal.

### A job-autosave test waited on the wrong thing (**test-only change**)

One test failed at the end of the full suite and passed on its own. Reproduced
by running it alongside the browser tests, which load the machine: the output
file existed, `result_path` resolved, the workspace was enabled — and `meta` was
empty. Not a timeout: the read happened too early.

The finishing order in `_run()` is deliberate — status goes to `done` and the
final state is persisted **before** the autosave copies the file and fills in
`meta`. The test waited for the status and read `meta` immediately, landing in
that window; under load the copy takes long enough to hit it every time. It now
waits for `meta["workspace"]` to appear.

### Windows installer: a successful uninstall reported a non-zero exit code

Found while testing the uninstall → fresh install path on a real machine. The
silent uninstall **succeeded completely** — service, registry entry and install
directory removed, firewall rule gone, **user data and all four SQLite files
preserved** — and still exited with **2**, because NSIS's `Quit` defaults to
"aborted by script" after handing off to the copy in `%TEMP%`. A scripted
uninstall (MDM, `Start-Process -Wait`) would call that a failure. Fixed with an
explicit `SetErrorLevel 0`.

> This release is **not tagged**, so the fix ships with the next installer.

### Both installer paths are now verified

Only the upgrade path had been tested before. The other half is now covered:
uninstall (user data verified intact) → **fresh install** — 131 seconds, exit 0,
signature `Valid` with `CN=SignPath Foundation`, service running and set to
automatic, health check `{"ok":true}`, correct version on the page, and the
**existing user data picked up** by the new installation.

### Buttons in one row now agree on their icons

A user pointed out two buttons in a four-button row had no icon. Fifteen more
rows across the site had the same mix. Both the inconsistency and a second,
invisible variant — a button whose icon is silently wiped because JavaScript
overwrites the whole button with `textContent` — are now guarded by
`tests/test_button_icons_are_consistent.py`.

### English documentation had fallen 34 releases behind

`CHANGELOG_en.md` stopped at 1.15.4 — and the three existing guards (file
exists, no Chinese left, language links point both ways) were **all green** for
a document that was a month out of date. Entries for 1.15.7 through 1.15.38 are
now written, and three new guards make it impossible to repeat: the newest
English entry must match the newest Chinese one, `README_en.md` must carry the
same version as `README.md`, and the English site pages are **regenerated and
compared byte for byte** — a criterion that computes itself rather than relying
on somebody remembering to run the generator.

### Document straighten: layout and loading polish

Before/after images no longer flash a broken-image icon while they are being
rendered; the cards use the same field layout as the rest of the site.

---

## [1.15.37] - 2026-09-13

### Windows installer: upgrading an existing installation always failed

The installer ran `uv venv --clear` against a virtual environment that was still
in use by the running service. It **deleted the environment and then failed**,
leaving a machine that could not start (`ModuleNotFoundError: jinja2`), and a
`MessageBox` without `/SD` meant the silent installer then **waited forever for a
click nobody could give**. The service is now stopped and its handles released
before the environment is touched, and every dialog has a silent-mode default.

Verified end to end on a real Windows machine, upgrading an existing install.

### Automatic page-edge detection failed on both real photos

Tested with actual phone photographs: `approxPolyDP` returned five and six
points, so no quadrilateral was found. Replaced with an Otsu brightness mask,
morphological closing, a convex hull and `minAreaRect`, plus a sanity check that
rejects wildly skewed corner sets.

> Two intermediate attempts scored **perfectly on residual angle** while
> cropping away content or framing the desk instead of the paper. A residual
> angle near zero does not mean the right sheet was found — the output has to be
> looked at.

### Document straighten: drag the four corners, rotate individual pages

Manual mode (phase 2): drag each corner with a magnifier under the cursor,
rotate a single page 90°/180°, reset to the original orientation, and apply a
correction to one page, all pages, or all following pages.

---

## [1.15.36] - 2026-09-13

### The new tool's page was dead JavaScript — and every gate was green

The document-straighten template never loaded its `<script src>` dependencies,
so the page threw `ReferenceError` on load: no drag and drop, no file picker,
nothing. Syntax checks, i18n scans and the API tests were all green, because
none of them **opens the page**.

Two new gates close that hole for every current and future tool:

* `tests/test_pages_boot_in_a_browser.py` — loads every page in a real browser
  and fails on any console error or CSP violation. It found a second instance of
  the same bug by itself.
* `tests/test_template_script_deps.py` — every global a template uses must be
  provided by a script that template actually includes.

### Fixes

* The sidebar highlighted two tools at once: the match was a prefix match, so
  `/tools/pdf-annotations` also lit up `/tools/pdf-annotations-flatten`.
* `class="notice"` was not defined anywhere in the stylesheets.
* Terminology: `在線` → `線上` in the Traditional Chinese interface.

---

## [1.15.35] - 2026-09-13

### History ids came straight from the URL without a format check

`/history/<id>` passed the id through to the filesystem layer, where a malformed
value produced a 500 instead of a 404. Ids are now validated against their
actual shape (12 hex characters) before anything is opened.

The test plan gained §4.9 for read-only admin endpoints that still return data.

---

## [1.15.34] - 2026-09-13

### Settings files interrupted mid-write turned silently into defaults

A note in the project file listed "six modules still writing settings
non-atomically". Counting them properly — with an AST pass rather than from
memory — produced **eighteen**, and three of them mattered a great deal:

| File | What a truncated write meant |
|---|---|
| `auth_settings` | zero bytes used to read as "authentication off" |
| **`api_tokens`** | every token gone **and `enforce` back to false — API authentication silently disabled** |
| `asset_manager` | the whole stamp / signature / watermark index disappears |

All thirty call sites now go through one helper (`app/core/atomic_json.py`):
same-directory temporary file → `fsync` → `os.replace` → `fsync` of the
directory. Four deliberate exceptions are documented, with a guard that checks
the exception list has not gone stale.

### Converted files were thrown away, with a message pointing at Java

`soffice` prints warnings (`failed to launch javaldx`) while converting
perfectly well, so its exit code is not a verdict. Only one of seven conversion
paths judged by the output file; the other six checked the return code first and
**discarded a good file**. All seven now require a usable output, and the three
failure modes are reported distinctly: empty output (source may be damaged),
killed by a signal (memory or concurrency, nothing to do with the file), and
everything else (with what soffice actually said).

### Calling the API exactly as documented could still fail

All 84 `curl` examples in `API.md` are now executed by
`tools/api_doc_example_audit.py`. One was genuinely broken:
`/admin/api/llm/test-connection` had no body in the example and no parameter
table, and the endpoint raised on an empty body. Both sides fixed — the endpoint
now falls back to the saved settings.

---

## [1.15.33] - 2026-09-13

### New tool: Document straighten (`doc-straighten`) — 47 tools → 48

Straightens skewed scans and phone photographs, trims black edges and evens out
background shading. **No AI and no GPU**: measured 0.83 s/page at 200 dpi.
A scan tilted 2.3° was estimated at −2.30° (error 0.00°) with 0.10° residual.

> **Pages that already have a text layer and are already straight are copied
> through untouched.** Re-rendering a born-digital PDF would turn selectable
> text into an image that merely looks the same — the document would stop being
> searchable and nobody would notice. Across eight real files, **100% of text
> survived**, and the completion message says how many pages were preserved.

"Convert to black and white" is off by default, and the interface says what it
is for: **smaller files, not better recognition**. Measured: local thresholding
drops OCR similarity from 0.775 to 0.108 on Chinese text.

### CI caught two problems that only exist in the published tree

A test hard-coded `github/OPS.md`, a path that only exists in the development
tree; and the SignPath signing step waited only ten minutes for an approval that
is manual by policy. Both fixed, and a guard now rejects literal `github/` paths.

---

## [1.15.32] - 2026-09-13

### De-identification now supports English documents

Adding English patterns was only half the work. **The Taiwanese patterns applied
to an English document do not miss things — they match the wrong things**:
passport numbers, IBAN fragments and card fragments were all matched as
telephone numbers, and a flight number matched a UK postcode. A false positive
is more dangerous than a miss, because the screen says "done".

Patterns now carry a locale and are selected by the **document's** language
(which is not the interface language — an English contract with a Chinese
interface is common, so the choice is on the page).

Everything with a check digit is verified — IBAN mod-97 above all — and the
replacement values are drawn from ranges that are **never assigned** (SSN 9xx,
555-01xx numbers, IBANs that deliberately fail their checksum), because a fake
number that validates may belong to a real person.

Both de-identification tools are no longer greyed out in the English interface.
The Taiwanese patterns were re-checked against real samples: no regressions.

---

## [1.15.31] - 2026-09-13

### The Windows installer is now English on English Windows

The product name is also a **path** — the Start menu folder and two shortcut
file names. Translating it naively means an installation made in one language
cannot be uninstalled in another: uninstall "succeeds" and leaves a folder
behind. The actual paths created are now recorded in the registry and read back
at uninstall time, with the old Chinese name kept as a fallback for upgrades.

### Services installed by the one-line Linux installer are now hardened

`packaging/jt-doc-tools.service` had five hardening settings; the unit the
installer generated itself had only `User=`. Anyone who installed with the
one-liner never had that protection. Verified with `systemd-run` using the same
settings — `sudo -u` proves nothing here, as it runs outside the namespace.

---

## [1.15.30] - 2026-09-13

### A coverage gate that compared the last path segment was not checking anything

It matched `/api/` endpoints by their final segment, so `list`, `count`,
`assets` and `history` matched something in a four-thousand-line document no
matter what. Eight of 84 endpoints passed without being covered at all; seven of
them appeared nowhere. Full-path matching now, with the mutation verified in
**both** directions — reverting to the old rule has to pass, or the change only
proves the wording moved.

### 79 state-changing admin endpoints had no acceptance criteria

The gate skipped the whole `/admin` prefix. Writes now require acceptance items
(§4.8, grouped by page); reads stay covered by page-level acceptance, and that
trade-off is written into the test itself.

### Further

* `API.md` was missing 16 endpoints.
* CI installed from `requirements.txt` while production uses the lockfile; a new
  test checks the three dependency declarations agree.

---

## [1.15.29] - 2026-09-13

* **Audit forwarding**: one failing destination no longer blocks the others —
  each destination keeps its own cursor and bounded retry queue.
* **PNG export**: pages are written to disk instead of accumulating in memory,
  and the temporary directory is cleaned up after the response.
* **Administrator privacy boundaries** are now one written policy rather than
  two endpoints disagreeing about what an administrator may open.

---

## [1.15.28] - 2026-09-13

### De-identification did not actually remove personal data from scans

Output looked correct in every visible way — text could not be extracted, black
boxes were on the page — but **extracting the page image and running OCR on it
recovered the data in full**.

The cause was `apply_redactions(images=PDF_REDACT_IMAGE_NONE)`: clearing pixels
inside the box is PyMuPDF's *default*, and that line deliberately turned the safe
default off. The pattern had been copied from the PDF editor, where the goal is
the opposite (move text, keep the logo underneath).

> Copying code means asking whether the source tool had the same goal. Here the
> correct value is the exact opposite, and no test went red.

The affected shape — a scanned image with an invisible text layer — is what this
product's own OCR tool produces, so it is a primary case, not an edge case.
Acceptance now inspects the images inside the output and re-runs OCR on them.

Clearing pixels re-encodes images as PNG: a real scan went from 2.3 MB to 5.5 MB.
Re-compressing to JPEG cost 3–4 s per page for 20–30% and a second lossy pass, so
the result page says so plainly and points at the compression tool instead.

### "Restoring previous state" was not true

When `uv sync` failed during an upgrade, that message was printed while the
working tree stayed on the **new** code with partially synced dependencies — and
the service was then started. Recovery now resets the code *and* re-syncs
dependencies, and the message distinguishes three outcomes: fully restored, code
restored but dependencies not synced, and could not restore.

### Further

* GELF over TCP is framed with a null byte, as Graylog requires; syslog and CEF
  keep RFC 6587 framing, pinned by tests.
* Cancelling a queued job released its row but kept its callable alive; one
  place now forgets a job, and a guard stops a fourth cleanup path from
  reintroducing the leak.

---

## [1.15.27] - 2026-09-10

### Parts of the Windows installer stayed Chinese on English Windows

Component names, failure messages and the three uninstall dialogs were
hard-coded. Three things were established by running the executable on real
Windows rather than by reading documentation: NSIS picks the language table from
the **system** locale, not from declaration order; `StrCpy $LANGUAGE` at runtime
cannot change an already-loaded table; and `makensis` does **not** warn when a
string is missing a language — "zero warnings" proves nothing.

---

## [1.15.26] - 2026-09-10

### Our own reverse-proxy example broke a customer: a hard-coded `X-Forwarded-Proto: https`

A customer reported "CSRF token missing or incorrect" as soon as a **remote**
machine uploaded a file, while the server itself was fine. The IIS `web.config`
example in `OPS.md` set the header to a fixed `https`; on an http-only site the
backend then marked cookies `Secure` and the browser dropped them over plain
http. Sign-in broke the same way, with no error shown.

> `http://localhost` is a secure-origin exception, so it accepts `Secure`
> cookies. **Testing a remote user's problem locally cannot reproduce it.**

The example now maps `{HTTPS}` properly, the documentation says the symptom
cannot be reproduced on the server itself, and the backend can explain the
mismatch. It **reports** it and does not relax anything automatically — the
detection headers are attacker-controlled.

---

## [1.15.25] - 2026-09-10

* **A saved SMTP port was overwritten every time the page loaded** (customer
  report). The convenience "fill in the usual port" logic also ran on load, and
  the flag meant to prevent that reset on every page view. The initial call now
  touches nothing, and switching mode only fills a field that is empty or still
  holds another convention value.
* The site-URL field was sized by a rule written for numeric fields.

---

## [1.15.24] - 2026-09-10

### The IIS reverse-proxy prerequisites were in the wrong order (customer report)

`OPS.md` said to install ARR and then URL Rewrite; **ARR depends on URL
Rewrite**. The instructions were correct when written — the Web Platform
Installer used to resolve that automatically, and upstream has since removed it.
The order is fixed, the reason is written down so it does not get "tidied" back,
and a literal test pins it.

---

## [1.15.23] - 2026-09-09

### A customer thought translation stopped at page six

It did not: 179 of 182 segments were translated and all 11 pages of output had
content. The side-by-side preview only renders the first six pages, and that was
said in small grey text nobody reads. The clue was in the customer's own words —
"the total word count is close to the original".

Anything that shows only part of a result now says how much the whole is, **at
the point where scrolling stops**, in a bordered box, with the download button
right there. A guard pins this for both tools that preview partially.

---

## [1.15.22] - 2026-09-08

### One table cell froze an entire translation, forever

A fill-in-the-blank line with sixteen non-breaking spaces made the model unable
to stop. With `stream=True`, an httpx `timeout` applies **per chunk**, so tokens
kept arriving and it never fired — the progress display simply stopped moving,
with no error and no failure. Streaming now has a separate wall-clock limit in
both loops, and the message says the model may be unable to stop rather than
blaming the network.

Documents that came from a PDF now say which engine was used, because the engine
with the best visual fidelity is the worst one to translate from: it pins each
line in place, which splits sentences across lines.

---

## [1.15.21] - 2026-09-08

### Word files containing text boxes were wrecked by translation

The same text was collected **four times** (a 44,900-word document extracted as
180,532 words): paragraphs that contain text boxes also iterate their contents,
and `mc:AlternateContent` stores the same content twice. Writing back put a whole
page into the first text box.

> When walking paragraphs, ask whether a node contains more nodes of its own
> kind — if it does, it is a container, not content. **A word count that does not
> match the original is the signal.**

The legacy VML copy is mirrored after translation, so the delivered file does not
carry a hidden full copy of the original text.

---

## [1.15.20] - 2026-09-07

### The glossary's placeholders were destroyed by the real model

Every test passed — a fake model naturally preserves whatever it is given. On
the production model, **all five sentences fell back** and the glossary did
nothing. The raw reply showed `<0xE2><0x9F><0xAA>1⟫`: a tokenizer that meets a
character outside its vocabulary emits the **literal text of the byte tokens**.

> Anything that depends on a model following instructions has to be tested
> against a real model. **Markers sent to a model must be ASCII** — seven were
> measured; `[[T1]]` was chosen.

The safety net worked so well that the output looked perfect while the feature
was not working at all; only the fallback counter could tell.

---

## [1.15.19] - 2026-09-07

### New: a translation glossary (shared by sentence and document translation)

Company-specific terms need one consistent translation. Putting a table in the
prompt is only a request the model may ignore, and the Traditional Chinese
instructions are already 1,179 characters against a 1,200-character batch limit.

This uses **term protection**, the standard approach in translation tools: the
terms are replaced with placeholders before the request, so the model never sees
them, and the required translation is substituted back afterwards. Deterministic,
and **not one character is added to the prompt**.

---

## [1.15.18] - 2026-09-07

### A scheduled CI run went red where the push run was green (**test-only change**)

The test waited on in-memory job state and then read the database, which is
written afterwards. Reproduced first by delaying the write, which made the old
test fail with exactly the CI message, then fixed to wait for what it actually
verifies.

---

## [1.15.17] - 2026-09-07

* **The pre-upgrade backup could fill the disk — and failed after the service
  was already stopped.** On production, 1.4 GB of a 2.0 GB data directory is a
  government dataset that re-downloads itself. Free space is now checked before
  anything stops, and the skip list has one rule: it must be able to rebuild
  itself. Measured: 1.93 → 0.56 GB per backup.
* **Every HTTP request read a settings file from disk**, on the event loop,
  including static files and health checks. Now cached by mtime and size:
  104 µs → 32 µs, with no restart needed for changes to take effect.

---

## [1.15.16] - 2026-09-07

### `sudo jtdt reset-password` could leave the service unable to write

Anything the CLI creates while running as root is owned by root, and the service
runs as its own account: `attempt to write a readonly database`. The worst case
is the rescue command itself — recovery would lock you out. Ownership is now
restored in one place in the dispatcher rather than at each return point.

### Files the public instructions tell you to run were not in the public tree

The screenshot script and the penetration-test script were missing, so the whole
procedure could not run from a clone. The existing gate only recognised commands
starting with `python …`, and half the document uses `.venv/bin/python …` — nine
of seventeen command lines had never been checked.

---

## [1.15.15] - 2026-09-07

* **`jtdt update` reported "Health check timed out" while the service was fine.**
  `jtdt bind` writes the listen address into the service manager's own
  configuration, and the health check read it from the shell's environment. Any
  installation with a changed port probed the wrong address forever. It now
  reads where the setting actually lives, probes loopback as well, bypasses any
  proxy, and prints what it probed plus the last 20 log lines instead of one
  unhelpful word.
* **`defusedxml` was imported by four modules but never declared.** On a machine
  without it, the tools are skipped silently: the service starts and health
  checks pass, and four tools simply are not there. A new test compares imports
  against the declared dependencies.

---

## [1.15.14] - 2026-09-06

* **Spreadsheet translation previews were blank.** The "fit to one page wide"
  step wrote new attributes after the tag name instead of replacing existing
  ones, producing duplicate attributes — invalid XML, which LibreOffice turns
  into an empty sheet with a **zero exit code**. Modified XML is now re-parsed
  before use, and falls back to the original if it does not load.
* **Translated spreadsheets opened on a blank area**, because the scroll
  position is stored in the file. The view is reset without touching frozen
  panes or a single cell of content.

---

## [1.15.13] - 2026-09-06

Every path that reads a user-supplied zip is now covered by one zip-bomb guard.

> ⚠ The first version of that guard was fake: it looked for the guard's *name* in
> the source, and every place that called it also had a comment mentioning it —
> so removing the import and the call left the test green. It now matches AST
> call nodes.

---

## [1.15.12] - 2026-09-06

* Without CJK fonts, ten tests failed with `TypeError: cannot unpack
  non-iterable NoneType` — nothing that suggests fonts. They now skip honestly,
  and the fonts are installed in CI so the "Chinese really renders" checks still
  run there.
* CI failures now name the failing test.

> ⚠ Hiding half the environment is the same as hiding none: a plugin that removed
> only LibreOffice reported "all green" while CI stayed red, because the runner
> has no CJK fonts either.

---

## [1.15.11] - 2026-09-06

* **Transit certificates keep the original file**, reachable from the record.
* High-speed-rail certificates that carry no train number no longer display `--`.
* Security: uploaded XML is parsed defensively and outbound downloads validated.

---

## [1.15.10] - 2026-09-06

Missing Office engine now returns **503**, not 500 — that is a deployment
problem, not a malformed request, and a 500 makes people retry forever while
monitoring fills with false alarms. One global handler covers every tool.

Tests that need system dependencies now carry skip gates, checked by AST rather
than a regular expression.

---

## [1.15.9] - 2026-09-06

* The first real CI run went red; the dangerous part was **silent loss of
  coverage** rather than the failures themselves.
* Damaged or hostile office documents are rejected **before** they reach soffice.
* Error messages no longer hand raw upstream responses to the user.
* English interface fixes from a page-by-page user review.

---

## [1.15.8] - 2026-09-06

### `tr` shadowed by a variable of the same name — three tools completely broken

A user reported `Upload error: tr is not a function`. `tr` is the front-end
translation function and also the most natural name for a table row:

```js
const tr = document.createElement('tr');   // tr is a DOM element here
inp.placeholder = tr('Subject');            // so this calls an element
```

Sixteen occurrences across three tools and nine admin pages. The worst was
`const tr = { 'host required': tr('Host is required') }` — calling itself from
its own initialiser.

Both existing defences were green: `node --check` only validates syntax, and this
shadowing **is** valid syntax; the i18n scanner checks whether strings are
wrapped, not what `tr` refers to where they are wrapped.

### Further

* Built-in role descriptions were wrong on existing installations.
* A batch of English interface fixes from a user's page-by-page review.

---

## [1.15.7] - 2026-09-05

### English interface: 986 untranslated strings → 0, verified in a real browser

Static scanning cannot see three sources of leftover Chinese: nodes built by
JavaScript, attributes (`title` / `placeholder` / `aria-label`), and server data
inserted into the DOM. A real browser walking every page found 986 strings, over
60% of them in the first two categories. The catalogue went from 3,286 to 4,698
entries.

> ⚠ "The scan found zero" is not "the translation is finished". A user replied
> with a dozen screenshots: dialogs, property panels, job lists, expanded
> dropdowns, error messages, tables that only exist once there is data — **none
> of that exists when the page first loads**. Three methods are needed, and the
> test plan now says so.

### Further

* **Windows: a machine whose installation was interrupted could never install
  again** (`uv venv --clear` added).
* CodeQL: an open redirect in `/ui-locale`, a `</script>` pattern that ignored
  whitespace, an unpinned minimum TLS version, and an unencoded job id.
* CI exists for the first time (`.github/workflows/tests.yml`).
* The English introduction site now uses **screenshots of the English
  interface** — the most direct evidence that English is really supported.

---

## [1.15.4] - 2026-09-05

### torch 2.11 → 2.14 (the setuptools alert can finally move)

The comment in `pyproject.toml` said the setuptools CVE needed torch 2.13 —
**torch 2.13 did drop the `setuptools<82` cap** (2.11 and 2.12 pin `<82`; 2.13
and later ask for `>=77.0.3`). With that gone, setuptools moves to **84** and the
moderate alert goes with it.

**Recognition was verified as identical on two real machines**, not assumed:

| Machine | Configuration | Result |
|---|---|---|
| Production server | Linux / Python 3.10 / `+cu130` (the production configuration) | **line for line identical** to 2.11 |
| Windows test machine | Windows / Python 3.12 / `+cpu` | **line for line identical** to 2.11 |

The test image has six mixed Chinese/English/numeric lines (company ID, invoice
number, amount, address, email, restricted-use wording) — and **even the mistakes
OCR makes are the same** (`AB-` read as `1B-`, `NT$` as `1TT$`, `Xinyi` as
`Yinyi`). That is what shows the model behaviour has not changed; "it runs" would
not.

> ⚠ **torchvision must come from the same index as torch.** PyPI's torchvision
> with a torch from `download.pytorch.org/whl/cpu` gives `RuntimeError: operator
> torchvision::nms does not exist` — **it fails at import, so OCR is completely
> dead**. This project uses the default PyPI index, where both resolve together,
> but anyone installing by hand can hit it.

> ⚠ **OCR cannot be verified on the development machine**: that machine is a QEMU VM whose **CPU has
> no AVX2**, so `readtext` dumps core. A control run showed **the current 2.11
> does exactly the same there** — it is the CPU, not the version. Without that
> control I would have blamed the upgrade.

**Production's torch changes on the next `jtdt update`** (that step runs uv sync:
about 5 GB of downloads and a restart). This release only updates the declaration
and the lockfile.

---

## [1.15.3] - 2026-09-05

### Stamp and seam stamp had identical icons (spotted by a user)

Both sit in the "forms and stamps" group, both used `stamp`, and they are next to
each other in the sidebar — **you had to read the label to tell them apart**. The
seam stamp now has its own icon: **two sheets side by side with a round stamp
straddling the seam between them**, which is exactly what the tool does.

Three more same-group duplicates were cleared at the same time:
`image-to-pdf` / `pdf-to-image` (both `image`), `doc-deident` / `text-deident`
(both `shield`) and `doc-diff` / `text-diff` (both `diff`). The plain-text
variants now use `text` and `columns`, and images-to-PDF uses `layers` (several
stacked into one). **Duplication across groups is left alone** — those are two
different lists and never sit side by side.

Two gates: no two tools in one group may share an icon (mutation test: putting
`stamp` back on the seam stamp turns it red), and every icon name must actually
exist in `icons.html` (**a typo raises no error, it simply shows no icon**, and
only eyes catch that).

### Dialog titles and buttons were still in Chinese

`showConfirm(message, { title: '清空工作區', okText: '清空' })` — the message was
already translated, but **the strings in the options object were not**, so in
English the dialog's title and buttons stayed Chinese. 175 of them are fixed.

The rewrite only touches text **inside a `showConfirm` / `showToast` /
`showModal` call** — `title:` elsewhere is **data, not display text** (a bookmark
is `{title, page, level}`), and translating that would rewrite the user's bookmark
titles, which is corrupting data rather than translating it.

Tooltips (`title=` attributes) were already covered by the 174 display-only
attributes in v1.15.2. The catalogue now holds **3,286 entries**.

---

## [1.15.2] - 2026-09-05

### Stamp and sign / seam stamp are no longer restricted to Chinese

These two were shown only in a Chinese interface, on the grounds that "stamps are
a Chinese documentary convention". **That judgement was wrong**: applying a company
stamp, a signature image or a logo, and stamping across pages so a swap shows, are
done everywhere. The bar for greying a tool out should be "**an English document
goes in, it succeeds, and nothing is found**" (the company ID database, e-invoice
QR codes, the ID-number and Taiwanese address patterns, the Chinese field-label
dictionary) — stamping is not one of those. Tools greyed out in English go from 9
to **7**.

The 246 strings on those two pages are translated as well (the catalogue now holds
**3,208 entries**).

### In English, field labels covered the checkboxes and inputs (reported from two screenshots)

`.form-row label` is a **fixed 96px with nowrap** — sized for **Chinese** (four to
six characters). English runs about 1.7× wider, so "Enable the workspace" and
"Remove the background" **overflowed straight over the controls beside them**.

The fix is scoped with `html[lang]:not([lang="zh-Hant"])`, so **the Chinese layout
does not move by a single pixel**. That is safer than widening the 96px, which
would give Chinese a wider label column for no reason.

60 over-long English strings were shortened at the same time (buttons and field
labels): `Make it the default` → `Set default`; `Counting window (minutes) — only
failures inside it count` → `Counting window (minutes)` (the explanation is
already on the hint line below).

> **No automated test catches this class of problem** — the elements are all there,
> there is no JavaScript error, and no Chinese is left. Only looking at a
> screenshot shows it. That is what `scripts/page_screenshots.py --locale en` is
> for.

### Sentences with variables in JavaScript are translated too

`` `已選：${file.name}` `` cannot simply be wrapped — **once interpolated the key
no longer matches**, so the lookup silently fails. They are parameterised instead:
`tr('…{0}…').replace('{0}', expr)`, and only when the interpolated expression is
simple enough (no quotes, backticks, newlines or nested templates); anything else
is skipped entirely. 97 in this batch.

---

## [1.15.1] - 2026-09-05

### An out-of-range thumbnail page returned 500

`/tools/pdf-rotate/thumb/<id>/0` and `/99` both returned **500**. The page number
is **in the path**, so that is a user asking for a URL that does not exist, not a
broken server — **a 500 makes people think the service is down and keep retrying,
and fills the monitoring with false alarms** (the same principle as v1.14.37's
"a corrupt file is always 400, never 500").

The interesting part: **the range was already checked**. `render_page_png` was
fixed in v1.14.31 for the nastier bug where `page_no=0` used a negative index,
returned the last page, and answered 200 OK. But nothing caught the `ValueError`
it raised, so it surfaced as a 500.

The fix is one **global handler** (a dedicated `PageOutOfRange` → 404), as with
corrupt files — the thirty-odd thumbnail and preview endpoints all have the same
shape, and patching them one at a time means the next new tool is missed again.

The gate (`tests/test_preview_page_range.py`) judges **"not a 5xx" rather than
"must be 404"** (a tool that stops it earlier with a 400 is also right), and it
has **a reverse check**: without one, making the endpoint always return 404 would
pass. Mutation test: removing the handler turns 12 cases red.

### Running a tool for real in the English interface

`temp/i18n-cdp/cdp_en_e2e.py` sends a PDF through the tools **in English** and
looks at the output. Wrapping JS strings deliberately skipped ternaries and string
concatenation (those may hold values that are compared or sent to the server), but
skipping is only an attempt to avoid the problem, not proof of it — a translated
value looks **perfectly normal on screen while the logic quietly breaks, and only
in English**. Measured: word count reports 3 pages / 21 words, page rotation
uploads and returns a 5,132-byte thumbnail, and not one drop-down `value` has
turned into Chinese.

The workspace drop area hint — the last leftover — is translated too.

---

## [1.15.0] - 2026-09-05

> The patch number rolls into a minor at 99 (a project convention, so no 1.14.100).

### Finishing the interface language work

Chinese left across the site in English went from thousands of strings to **202**,
and nearly all of what remains **should not be translated**:

- **The product name** (`Jason Tools 文件工具箱`, 74 occurrences) — it is a brand,
  and an administrator can replace it; translating it automatically would also
  replace a company name somebody had set.
- **Domain data** — the field synonym dictionary (`付款方式, 匯款方式, Style of
  Payment…`), accounting categories, the redaction patterns. **Not one word of
  this may enter the catalogue**: translating it makes auto-fill forms **silently
  stop finding fields**.

What was filled in this round were the sidebar items that kept being missed: the
`aria-label` and button text for **"notifications" and "home"** (71 occurrences
each, the most frequent leftovers on the site), the retention item names, and the
history page title.

### Display-only attributes and the LLM tool labels

`placeholder`, `title`, `alt` and `aria-label` — attributes that are **display
only** — had been missed by the first two passes (which covered text nodes and
`{% with %}` parameters). 174 of them are now translated, so input hints and
button tooltips follow the interface language. The per-tool descriptions in the
administration LLM settings (29 more) are covered too. The catalogue holds
**2,917 entries**.

### Two things fixed along the way

- **`current_locale()`'s documentation was wrong** (it still said "switching is not
  available yet"). It is really the fallback for when there is no request to ask —
  the language lives in a cookie and can only be determined with a `Request`.
  `tool_visible()` must always be given the locale explicitly; falling back means
  treating everything as Chinese, and the nine Chinese-only tools would appear
  usable in English.
- **`test_admin_users_table` did not recognise the `{{ tr('…') }}` wrapper** and
  went red the moment i18n was added, which has nothing to do with the column
  order it exists to protect. **A gate that fails on unrelated changes is as bad as
  one that misses real problems** — nobody believes it the next time it goes red.

---

## [1.14.99] - 2026-09-05

### Interface language, stage B finished: the administration area

Every visible string on the 30 administration pages (1,048 of them) and the
messages inside their `<script>` blocks (118) now go through the translation
layer, along with the 34 descriptions in the "settings" menu. The catalogue holds
**2,711 entries**. Chinese left on the administration pages in English went from
**1,497 to about 200** (what remains comes from Python data — LLM tool names, OCR
language labels — and the product name in the page title).

### ⚠ The safety net could not see the administration area

`tools/i18n_zh_baseline.py` originally covered only tool pages and general pages,
so **a broken administration page was invisible to it**. Extending it ran into two
things that differ on every run, either of which would have made the comparison
permanently red (and therefore worthless):

- **JSON APIs share the administration GET routes** (system status, job queue …)
  and their responses carry timestamps → only `content-type: text/html` is kept.
- **The data directory path is printed on the pages** (export directory, font
  directory), and the baseline script used a fresh `mkdtemp` each time → it now
  uses a fixed `temp/i18n-baseline-data`.

It now covers **81 pages**, including every administration page, and this round
was byte-for-byte identical throughout.

### Chinese keywords still find administration pages in English

The sidebar search matches against `data-name`; replacing it wholesale with
English would mean **a Chinese search no longer finds anything**. Both languages
now go into `data-name` in English, while **the Chinese interface is left exactly
as it was** (otherwise the same string appears twice and the bytes change — the
safety net caught that immediately).

---

## [1.14.98] - 2026-09-05

### Interface language, stage B (part 2): strings inside `<script>` too

The template helper `{{ tr('…') }}` is evaluated **while the server renders the
page**, so button labels, error messages and text inserted into the DOM at runtime
could not use it — 1,311 strings, the bulk of a tool page.

The answer is a `tr()` of the same name on the front end
(`static/js/i18n.js`), with the dictionary served from `GET /i18n/<locale>.js`:

- **Traditional Chinese never loads a dictionary at all** (the template only emits
  that `<script src>` for other languages) and `tr()` returns its argument — no
  cost, no risk.
- The dictionary is around 100 KB and only changes on upgrade, so it carries an
  **ETag**: moving between pages sends one `If-None-Match` and gets a 304
  (`no-cache` does not mean "do not cache", it means "ask before using").
- **Sentences with variables are parameterised** (`tr('Selected: {0}').replace(...)`);
  an interpolated sentence must never be the key, because the key changes with the
  value.

**Only positions that cannot be used as values are wrapped**: `.textContent =`,
`.innerHTML =`, `.title =`, `.placeholder =`, `alert(`, `showToast(`,
`showConfirm(`, `friendlyServerError(…,`. Ternary results and string
concatenation are deliberately left alone — translating a string that is compared
against something, or sent to the server, looks perfectly fine on screen while the
logic quietly breaks, **and only in English**. 356 strings in this batch; the
catalogue now holds **1,507 entries**.

Verification uses a real browser (`temp/i18n-cdp/cdp_i18n_test.py`): in English the
button raises an English message, and in Chinese not one character changed. The
signal has to be something that only appears when the translation really happened —
an untranslated string comes back as Chinese with **no JavaScript error at all**.

### I made the "match translations by index" mistake again

The batch process is: print the untranslated list → write translations in order →
merge back by index. Between those steps I removed two keys that contained Jinja
syntax (`tr('{{ icon(...) }} …')` — the template renders first, so the runtime key
is rendered HTML and never matches), the list was regenerated, the order changed,
and **everything from the 8th entry on was two places out**. Spot-checking caught
it before it shipped.

This is the same fault fixed in v1.14.97 on the introduction site. So
`tools/i18n_merge.py` now exists: **translation batches must be keyed by the
Chinese source string, and a batch whose keys look like indices is refused.**

Three more gates: every JS `tr()` key must be translated; keys must not contain
template syntax; and a translation must keep a trailing colon or ellipsis (`tr('Analysis failed: ') + err`
loses the separator otherwise — and that check also catches whole-batch misalignment).

### Site-wide screenshots can now be taken in English

`scripts/page_screenshots.py --locale en`. English runs about 1.7× wider than
Chinese, and **no automated test catches a broken layout** — only eyes do. All 80
pages were reviewed this round; nothing overflowed or was cut off.

---

## [1.14.97] - 2026-09-04

### Sentences chopped up and headings pasted onto the wrong section (reported from a screenshot)

Every clause of the disclaimer began with a comma, the text under "Terms of use"
belonged to another paragraph, and a table cell read `; JSON:`. A user spotted it
at a glance while every existing gate stayed **green**. Two causes:

**① Sentences split by inline markup were translated piece by piece.** Extraction
worked on text nodes, so `<b>This software is provided AS IS</b>, including but
not limited to…` was two pieces. Chinese reads correctly when the pieces are
concatenated in the original order; **English word order differs**, so the result
was fragments like ", including but not limited to…". Extraction now takes **the
whole block, inline tags included**, so the translation decides where `<b>` goes.

**② Translations were attached to the wrong keys.** An earlier merge matched
translations to keys **by index**, so a change in list order shifted whole runs.
**The existing gate only checked for leftover Chinese — after a shift there is no
Chinese at all, so it passed.**

A nastier variant: **fragments consisting only of punctuation were treated as
translatable**. A lone `—` in a table became a key that matches everywhere, and
another string's translation was pasted where that dash belonged. That is where
`; JSON:` came from.

### Three new gates, all decidable from the text itself

- **Inline tags and links must match exactly** — a translation cut short, or
  pasted onto the wrong key, no longer has the same tags. This caught 8 of my own
  translations that were missing their tails.
- **Compare the Chinese and English pages block by block**: if an English block
  starts with punctuation where the Chinese one does not, fail. The judgement
  lives **on the rendered pages, not in the catalogue** — "starts with
  punctuation" is sometimes correct in the catalogue (the source really is the
  middle of a sentence split by `<code>`), so judging entries individually gives
  false positives, while comparing pages is position against position. Mutation
  test: replacing the translation of one heading turns it red (**the first version
  missed `<div>` and stayed green** until that was added).
- **Pure-punctuation keys are not allowed.**

### Interface language, stage B (continued)

Option labels that come from Python data — fonts, themes, languages, formats — are
now translated too. The catalogue holds **1,284 entries**. The Chinese output is
still byte-for-byte identical.

---

## [1.14.96] - 2026-09-04

### Interface language, stage B (part 1): 38 tool pages in English

After the shell, the inside of the tools. The catalogue grew from 313 to
**1,205 entries**, covering the 38 tools that are usable in an English
interface (the nine Chinese-only tools are greyed out in English, so they are
left for later).

**The Chinese output had to be proved unchanged first.** Wrapping several
hundred lines of templates in `{{ tr('…') }}` is too much to check by eye, and
when it breaks it usually still *looks* right — a lost space, or one extra layer
of escaping. Pixel comparison is both slow and blind to that. So
`tools/i18n_zh_baseline.py` compares the **rendered HTML byte for byte** across
52 pages after every batch (the CSRF token and CSP nonce are normalised first,
otherwise the comparison is always red and the safety net is worthless).

The wrapper is deliberately timid: anything containing `&` (an HTML entity would
be double-escaped), a quote, or template syntax is skipped — better to miss a
string than to break one. A second pass covered `{% block title %}` and
`{% with hint='…' %}`, which are not text nodes.

Chinese left on the tool pages themselves went from **1,555 to about 90**.

---

## [1.14.95] - 2026-09-04

### Test-plan coverage gaps

Comparing the route table, the tool registry and the `tests/` directory against
the plan by program turned up four areas with no acceptance criteria at all.
Each now has a gate, so anything new that is not written into the plan turns red:

| Gap | Size | Why it matters |
|---|---|---|
| **Non-API endpoints** (§4.7) | **267** | every button on screen calls this layer |
| **Test-file index** (§1.99) | 96 of 212 listed | the other 116 run, but what they guard is invisible |
| **CLI commands** (§3.5) | 8 of 26 listed | when the web UI is unreachable, this is the only way in |
| **Schema migrations** (§1.98) | **29**, none listed | losing data on upgrade is the least reversible failure |

The non-API layer matters most: §4 only guaranteed that each tool had one
`/api/` endpoint with acceptance criteria, yet the worst bugs this project has
had all happened in the non-API layer while the `/api/` route was fine —
horizontal privilege escalation in the N-up preview (B could download A's PDF),
90-second per-page previews in the seam stamp, permanently blank workspace
thumbnails, and `/AF` left behind in the "copy without attachments".

### Two gates that were themselves broken

- The published test plan told people to run `python tools/check_*.py`, but
  **`tools/` had never been synced into the repository** — copying the command
  gave "file not found", so those checks were never run. A new gate checks the
  published tree as well as the working tree.
- `check_version_consistency.py` exited 0 when it could not read a source at
  all. Putting a language switch above the README title made the heading reader
  return `None`, and the check silently stopped verifying the README. "Cannot
  read it" now counts as a failure.

### English README and change log

`README_en.md` and this file, each with a language switch on the first line; the
Chinese files keep their names.

`README_en.md` is **generated** the same way the introduction site is
(`github/build-i18n-md.py`, line by line against `docs/i18n/readme.en.json`, code
blocks left untouched) — one document maintained by hand in two places always
drifts, and this project has paid for that several times. This change log is a
**summary**: the Chinese one covers 767 releases over six thousand lines, which is
neither useful nor maintainable to translate in full.

### The README's pytest badge had been stale for many releases

It read **470 passed**; the real figure is 5,951. A new gate
(`test_readme_pytest_badge_is_not_stale`) requires the badge to be **no lower than
the number of `def test_` definitions in `tests/`** — deliberately one-sided, since
parameterisation only ever adds cases, so a badge below the definition count is
certainly stale and can never be a false positive.

---

## [1.14.94] - 2026-09-05

### Word count now accepts office documents

`.doc` / `.docx` / `.odt`, `.xls` / `.xlsx` / `.ods`, `.ppt` / `.pptx` / `.odp` —
**converted to PDF first, then counted**. That way the page count and per-page
figures match what actually prints, and the existing PDF counting path is reused.
Reading the XML directly would be faster but gives no page count, and paragraphs
and line breaks would differ from the laid-out document.

A failed conversion returns **400** (corrupt file, or no Office engine), never 500;
the test is “did we get a usable file”, not soffice's exit code.

### Interface fixes

- The English label `Authentication realm` on the sign-in page was clipped by the
  fixed-width label column — shortened to `Realm`.
- Finished translating the sentences in shared components that inline markup had
  split apart (LLM service notice, missing-Chinese-font warning, the background-job
  “you can close this page” hint).

---

## [1.14.93] - 2026-09-04

### The introduction site and API manual are available in English

`docs/index-en.html` and `docs/api-en.html`, with a language link in the navigation
of each pointing at the other.

**They are generated, not maintained by hand.** The same document kept in two
places always drifts. The Chinese page stays the single source of truth, and
`github/build-i18n-page.py` extracts the translatable strings and produces the
English page from a catalogue.

### Locale-restricted tools are greyed out rather than hidden

Nine tools are built for Chinese / Taiwanese documents and conventions (company ID
lookup, e-invoice processing, travel receipts, pre-submission check, auto-fill
forms, both redaction tools, seam stamp, stamp and sign). In a non-Chinese
interface they are still listed, but greyed out, not clickable, and not pinnable,
with a tooltip explaining why. Seeing that a tool exists and why it cannot be used
is easier to understand than the tool disappearing.

### Interface language (i18n)

The shell is translated: sidebar, search, notifications, sign-in, two-step
verification, first-run setup, home page, my jobs, my workspace, and all 47 tool
names and descriptions. The language is chosen from the account menu or on the
sign-in page, and **only an explicit choice changes it** — browser language is
deliberately ignored, because switching to English also greys out those nine tools,
and nobody should lose access because of a browser setting they never chose.

---

## [1.14.87] - 2026-09-04

### Found the real cause of “20% of batches lose a segment”

Document translation batches were being judged as incomplete and retried — pure
waste, because the translation was there all along:

```
⟦<0xC2⟩5⟧5. New requirement - effective immediately   ← a stray <0xC2> inside the marker
```

`<0xC2>` is what a tokenizer emits, **as literal text**, for a byte that is not a
character. The marker no longer matched, that segment was not parsed, the whole
batch was judged incomplete, and a full generation was thrown away. Stripping
`<0x??>` before parsing fixed it: 3/3 parsed where it had been 0/3.

---

## [1.14.85] - 2026-09-03

### Document translation was dropping text colour

A spreadsheet cell containing “explanatory text + line break + a red italic note”
came back with the note in plain black. The translation had been written into the
paragraph's first text node, collapsing the whole cell to that node's style. There
was no error and the layout was unchanged — only a side-by-side comparison with the
original showed it, and that red “this is a new requirement” note was the point of
the document.

The translation is now written back **line by line where the runs allow it**, so
line-level colour, italics and bold survive.

---

## [1.14.67 – 1.14.83] - 2026-09-03

### New tool: document translation

Translate a whole office document into another language and get **the same format
and layout back** — only the text changes. Nine formats; the older binary formats
(.doc/.xls/.ppt) are converted to the modern one, translated, and converted back.
**PDF is not accepted**: a PDF has no paragraphs, its text is positioned fragments,
and replacing them with translations of a different length is bound to break the
layout.

---

For releases before this, see **[CHANGELOG.md](CHANGELOG.md)** (Traditional Chinese).
