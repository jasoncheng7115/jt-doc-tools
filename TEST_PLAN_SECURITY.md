# jt-doc-tools 資安測試計畫

從 `TEST_PLAN.md` 拆出來的獨立計畫。理由：資安項目的**執行方式**與功能驗收不同 ——
它需要「啟用認證 + 兩個以上帳號 + 一個攻擊者視角」，而且判定標準是「拿不到」而不是
「功能正常」。混在功能清單裡會被當成一般項目快速帶過。

主計畫見 `TEST_PLAN.md`；發版前兩份都要跑完。

---

## 0. 這份計畫的原則

1. **先重現、再修、再用同一組攻擊確認關閉。** 沒有重現過的「修好了」不算。
2. **跳過不等於通過。** 探測腳本若因為參數寫錯而沒打到端點，必須顯示為失敗或
   警告，不可印出綠燈。每個探測都要有**反向對照**（同一組請求由擁有者送出必須
   成功）—— 一個驗不出東西的探測比沒有探測更糟，它會給出假的安心。
3. **靜態掃描保證「沒有人被忘記」，動態測試保證「做的是對的事」。** 兩種都要，
   缺一不可：靜態看不出 fail-open，動態看不出「新加的端點沒人測」。
4. **回「找不到」不回「沒有權限」。** 後者等於告訴對方「這份東西存在，只是你不能
   看」。判定時兩者都接受，但也要確認內容真的沒回去。
5. **同一個判斷只能有一份實作。** 歸屬判斷散在兩處時，修一份不會讓另一份跟著好，
   而且兩邊看起來都「有做檢查」—— v1.14.6 的兩個無主漏洞就是同一個 bug 的兩份拷貝。
6. **不在客戶 / 正式機上做破壞性測試。** 這份計畫的攻擊測試一律在本機臨時實例
   （臨時 data dir + 臨時埠）進行。

---

## 1. 自動化（pytest）—— 每次發版必跑

```bash
.venv/bin/python -m pytest -q \
    tests/test_owasp_top10.py \
    tests/test_llm_url_ssrf.py \
    tests/test_path_traversal_audit.py \
    tests/test_redos_ad_dn.py \
    tests/test_authz_boundaries.py \
    tests/test_auth_modes_matrix.py \
    tests/test_id_from_body_acl.py \
    tests/test_job_id_acl.py \
    tests/test_job_api_acl.py \
    tests/test_submission_check_acl.py \
    tests/test_preview_acl_failopen.py \
    tests/test_stamp_watermark_preview_acl.py \
    tests/test_safe_paths_and_owner.py \
    tests/test_auditor_readonly.py \
    tests/test_roles_rbac.py \
    tests/test_api_gate_and_csrf_edges.py \
    tests/test_csrf.py \
    tests/test_csp_nonce.py \
    tests/test_csv_injection.py \
    tests/test_upload_validation_parity.py \
    tests/test_cookie_flags_on_delete.py \
    tests/test_error_message_scrub.py \
    tests/test_forwarded_proto.py \
    tests/test_broken_input_no_500.py \
    tests/test_mcp_server.py \
    tests/test_mcp_cross_user.py \
    tests/test_mcp_admin.py \
    tests/test_api_token_owner.py
```

各檔的分工：

| 檔案 | 守住什麼 |
|---|---|
| `test_id_from_body_acl.py` | **靜態掃描**：任何吃使用者提供的 id 的端點都要有權限檢查。例外清單每筆都要寫理由，並偵測「例外項已改名 / 刪除」 |
| `test_job_id_acl.py` / `test_job_api_acl.py` | 作業（含無主作業）不可被別人讀 / 取消 / 下載；pdf-to-office 的報告與前後預覽同樣要驗 |
| `test_submission_check_acl.py` | 案件 ACL：無主案件僅管理員、稽核員唯讀、管理員 / 稽核員判定必須真的有效 |
| `test_cookie_flags_on_delete.py` | **外掃 v1.14.29**：刪除 cookie 的回應也要帶安全旗標；靜態擋「新寫的 delete_cookie 沒帶旗標」 |
| `test_error_message_scrub.py` | **外掃 v1.14.29/31**：錯誤訊息不得反射使用者原字串（檔名含 `<script>` 等一律轉全形） |
| `test_forwarded_proto.py` | **外掃 v1.14.29**：多層代理 `X-Forwarded-Proto` 逗號串的 HTTPS 判斷（Secure 旗標與 HSTS 要同一個答案） |
| `test_broken_input_no_500.py` | 毀損檔案一律 400 不可 500（全工具端點自動列舉，2026-08-16 掃出 28 處） |
| `test_preview_acl_failopen.py` | 預覽端點認不出 upload_id 時**拒絕**（不可 no-op） |
| `test_auditor_readonly.py` | 稽核員唯讀：讀得到但不可刪紀錄、不可觸發備份輪替 |
| `test_roles_rbac.py` | 內建角色名實相符、工具 id 存在、升級步驟編號連續 |
| `test_api_gate_and_csrf_edges.py` | API token 閘不可誤擋管理區；CSRF 豁免不可只看標頭 |
| `test_api_token_on_tool_paths.py` | 手冊教人帶 token 呼叫的工具路徑（`/download`、`page-image`…）在**啟用認證**時也要驗 token；錯的 token 401、沒帶的照舊導登入、token 不繞過工具權限 |
| `test_jtlw_settings_audit.py` | 語音服務設定存檔要留 `settings_change` 稽核紀錄；**送進來的金鑰字串絕不出現在紀錄裡**（紀錄會轉送到 SIEM） |
| `test_auth_modes_matrix.py` | 認證開 / 關兩種模式的行為都要對（很容易只顧一邊） |
| `test_csv_injection.py` | 匯出的 CSV / xlsx 不可被試算表當公式執行；含「所有 xlsx 寫入都要走 helper」的靜態檢查 |
| `test_upload_validation_parity.py` | 壞檔要回 400 不是 500；網頁介面與對外 API 判定一致；不可把伺服器回應塞進 innerHTML |
| `test_mcp_server.py` | MCP 入口（`/mcp`）：預設關閉、沒 Token 401、Token 沒勾「允許 MCP」或沒有擁有者 403、Origin 不是站台網址 403、限流；工具權限跟著 Token 的擁有者（MCP 這層漏了原本的 API 也擋）；票證只認標頭、綁 IP、過期與雜湊不符擋下 |
| `test_mcp_cross_user.py` | **跨使用者**：B 的 Token 拿 A 的檔案編號、作業編號叫 `jtdt_run` / `job_status` / `workspace_download_url` / `workspace_read_text` 一律失敗，錯誤跟「編號不存在」一樣；清單只有自己的；下載票證網址帶別人的編號也只換到自己那份、用滿次數就失效 |
| `test_mcp_admin.py` | 設定 → MCP：只有管理員、Token 只顯示遮罩、稽核不記 Token、範例不洩漏內網位址 |

---

## 2. 滲透測試腳本（每次發版跑一次）

```bash
# 1) 起一個乾淨的本機實例（**不要**打客戶機 / 正式機）
rm -rf /tmp/pt-data && mkdir -p /tmp/pt-data
JTDT_DATA_DIR=/tmp/pt-data .venv/bin/python tools/setup_pentest_users.py
JTDT_DATA_DIR=/tmp/pt-data .venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8794 &

# 2) 打
JTDT_DATA_DIR=/tmp/pt-data .venv/bin/python tools/pentest.py \
    http://127.0.0.1:8794
```

涵蓋的類別（缺一類就會漏掉一整批端點）：

| # | 類別 | 為什麼需要單獨一類 |
|---|---|---|
| 1 | 未登入存取受保護路徑 | 最基本 |
| 2 | 垂直越權（一般使用者碰管理功能） | 讀與寫要分開驗（曾出現「稽核員可刪、管理員被擋」） |
| 3 | 認證繞過技巧 | Host 標頭污染（`request.url.path` 可被污染）+ 路徑變形 |
| 4 | 水平越權：**id 在網址路徑上** | `/api/jobs/{id}` 這一類 |
| 4b | 水平越權：**id 在請求內容裡** | 第一輪就是漏掉這一類 —— 13 項全過卻仍有實際洩漏 |
| 4c | **相鄰端點** | 同一支 router 裡「隔壁有驗、自己沒驗」（實例：預覽有驗、報告沒驗） |
| 5 | id 列舉與回應碼一致性 | 不存在與沒權限要無法區分 |
| 6 | 路徑穿越 | 檔名 / id 拼路徑處 |
| 7 | CSRF | 不帶 token 的寫入要被拒 |

**判讀**：每一項都要是 `[OK]`。出現「（… 跳過）」就要先修腳本再重跑 ——
跳過的項目沒有被驗證過。

---

## 3. 源碼掃描

```bash
# **先確認工具在**：`uv sync` 會移除任何沒宣告在 pyproject 的套件 ——
# bandit 曾因此被清掉，而門檻腳本只印出一段空白，看起來像「跑過沒問題」
# （2026-08-25 實際踩到）。現已宣告在 `[dependency-groups] dev`。
.venv/bin/python -m bandit --version || echo "bandit 不在 → 這項門檻等於沒跑"
.venv/bin/python -m bandit -r app/ -f json -o temp/sec-audit/bandit.json -q
```

> **門檻工具不在，就要大聲失敗**。這一條適用於整份計畫：bandit、chromium
> （可見元素回歸）、測試用 LDAP 目錄（四種登入驗證）—— 任何一項的工具缺席時，
> 腳本必須印出「這一項門檻等於沒跑」，不可安靜跳過。少跑一項比跑出紅字危險，
> 因為報告看起來仍然是綠的。

判讀方式：只看 HIGH / MEDIUM（v1.14.48 為 0 個 HIGH、37 個 MEDIUM，**全部屬於下表的已知非問題**）。
歷次增量都逐一判讀過：v1.14.6 是 30；v1.14.47 的 +3 是 B608 的 `IN ({placeholders})` 形狀（佔位符由
`?` 的個數組出來）；v1.14.48 的 +4 全在 `auth_db._m23`（OU key 正規化遷移）—— 表名與欄名來自函式內
**寫死的常數 tuple**（`subject_roles`/`role_id`、`subject_perms`/`tool_id`），值一律走參數化。
新增的 MEDIUM 要逐一判斷；判定為非問題就補進下表並寫明理由，不可只是忽略。

已知的**非問題**（複審時不必重開）：

| 規則 | 為什麼不是問題 |
|---|---|
| B608（SQL 字串組合） | 組出來的都是常數片段，值一律走參數化（`?`）；`vat_db` 已於 v1.12.25 改成全常數 query |
| B310（urlopen） | 目標 URL 來自管理員設定，且已過 `url_safety` 檢查 |
| B314（ElementTree） | Python 3.12 的 expat 放大限制擋住 billion-laughs；不展開外部實體，無 XXE |
| B104（綁定 0.0.0.0） | 兩處都不是真的在綁：`auth_router:113` 是**拒絕**把 `0.0.0.0/0` 當成信任的反向代理（那個字串出現在黑名單裡）；`server_template.py` 是外接 OCR 伺服器的預設值，本來就要對外提供服務 |

GitHub 端（推上去 5–15 分鐘後看）：

- Dependabot：Open alert 數應持平或下降
- CodeQL：新警告當天處理或記入 CHANGELOG「已知議題」
- **已決定 dismiss 的不要再去「修」**：匯出目錄的 6 個 path-injection（管理員本來就
  有主機檔案系統權限，且允許指定 `/mnt/backup` 是合理部署；v1.12.99 試過黑名單式
  硬化反而從 5 個變 6 個）

---

## 4. OWASP ZAP DAST（每次發版必跑，三個目標）

```bash
python tools/run_zap_scan.py            # 三個目標一次跑完並印出判讀
python tools/run_zap_scan.py --only auth   # 只跑已登入那個
```

> **⚠ 這一條斷過一次：v1.15.8 ~ v1.15.39 整整 13 個版本沒跑**，最後一份報告
> 停在 2026-09-05。原因不是忘記，是**每次都要手刻 plan 檔與已登入實例**
> —— 要重建才能做的事就是會斷掉的事。所以現在收成一支腳本：它自己建
> 拋棄式實例、發 session cookie、產生三份 plan、跑完之後**印出每個目標的
> 網址數與 High/Medium/Low**。
>
> **目標位址放 `temp/zap/targets.json`**（`temp/` 不進公開樹），格式：
> `{"doc": ["https://網址", "說明"], "30": ["http://位址:8765", "說明"]}`。
> 沒有那個檔就只跑 `auth` —— **腳本本身不可以寫死內網位址**，`tools/` 會
> 同步進公開樹。

另外兩個目標（要在 `targets.json` 裡設好）：

1. **經反向代理的正式路徑**（`https://doc.jason.tools`）—— 才驗得到 nginx 的標頭、
   HSTS、TLS 設定
2. **直連**（`http://<內部測試機>:8765`）—— 才驗得到後端自己送的標頭

**通過標準：High / Medium / Low 全部為 0。** Info 屬勸告性可留（例如 `no-store`
會觸發的「Re-examine Cache-control」）。報告存 `temp/zap/<YYYYMMDD-NN>/`（不上
GitHub）。

### 4.1 第三個目標：已登入狀態的掃描（v1.14.6 起）

**啟用認證之後，上面兩個目標的爬蟲只看得到登入頁 —— 各 12 個網址。** 那只驗到公開
表面，管理頁與工具頁完全沒被掃到。所以另加一個帶登入狀態的掃描：

```bash
# 1) 拋棄式實例（**絕不可**對正式機做這件事 —— 帶管理員身分的爬蟲會去點各種
#    設定與刪除端點，那是破壞性的）
rm -rf /tmp/ztdata && mkdir -p /tmp/ztdata
JTDT_DATA_DIR=/tmp/ztdata .venv/bin/python tools/setup_pentest_users.py
JTDT_DATA_DIR=/tmp/ztdata .venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8795 &

# 2) 發一個管理員 session，寫進 plan 的 replacer 規則
#    （**掃描前重發** —— 舊 cookie 失效時爬蟲會靜靜地退回只爬登入頁，
#      症狀就是「只找到 12 個網址」而不是任何錯誤訊息）
# 3) 跑 plan-auth.yaml
/snap/bin/zaproxy -cmd -autorun temp/zap/<日期-NN>/plan-auth.yaml
```

**判讀時先看爬到幾個網址**：應為數百個（v1.14.6 實測 393）。若是 12 個，代表
cookie 沒生效，這次掃描等於沒做 —— 不可當成通過。

`plan-auth.yaml` 的 replacer 規則 `matchType` 要寫 `req_header`（不是
`request_header`，後者會讓整個計畫失敗），且不要加 `enabled` 欄位（會警告）。

**context 一定要 `excludePaths` 掉 `/logout` 與 `/auth/*/sls`**（2026-08-24 實測）：
爬蟲會點到登出連結，把 replacer 灌進去的 session 作廢，之後整段掃描退回未登入 ——
**症狀不是報錯，是涵蓋的路徑悄悄變少**，而風險等級照樣全 0，看起來一切正常。
那一次少掃了 3 條需登入的路徑（其中一條正是當輪改動的端點）。
**判讀要看爬蟲自己的統計，不要看「告警涵蓋路徑」**（後者是從告警實例反推的，
會隨資料量浮動而低估 —— 2026-08-24 連兩輪都因此誤以為少掃了一條）。依序看：

1. ZAP log 的 `Job spider found N URLs` —— 掉到十幾個 = cookie 沒生效
2. 拋棄式實例的稽核記錄裡 `logout` / `login_locked` 應為 **0** ——
   非 0 代表爬蟲把自己登出了，後半段是未登入狀態在掃（無聲：風險仍全 0）
3. High / Medium / Low 全 0

---

## 5. 手動：兩個帳號互相攻擊（抽查，10 分鐘）

自動化測到的是已知形狀，這一段是找**新形狀**。用瀏覽器開兩個無痕視窗分別登入
A / B，然後：

- [ ] A 上傳檔案取得任何 URL（作業、預覽、下載、報告、縮圖）→ 貼到 B 的視窗
- [ ] A 的網址裡有 id 的地方，把 id 換成 B 的 → 應該拿不到
- [ ] 開 DevTools 看 A 的每一個 XHR，把**請求內容裡**帶 id 的那幾個重放成 B 的身分
- [ ] B 用一般使用者身分直接輸入 `/admin/...` 的各頁網址
- [ ] 稽核員帳號：確認看得到稽核 / 歷史，但每一個刪除 / 儲存按鈕都被拒
- [ ] A 登出後，用剛剛那些 URL 再試一次（session 失效要立刻生效）

發現任何一項成功 → 停下來寫成一個 pytest 再修（不可只手動修掉）。

---

## 6. 歷史案例（每次發版必過）

| 版本 | 問題 | 重現方式 |
|---|---|---|
| v1.14.6 | pdf-compress `/submit` 吃請求內容裡的 `upload_id`，B 可取得 A 的 PDF 內容 | 滲透測試 §4b |
| v1.14.6 | 三個根層級 `/api/*` 端點未經認證即可呼叫（CSRF token 從公開登入頁就拿得到） | 滲透測試 §1 |
| v1.14.6 | 無主作業任何登入者可讀 | `test_job_id_acl.py` |
| v1.14.6 | 無主案件任何登入者可讀 / 改 / 刪 | `test_submission_check_acl.py` |
| v1.14.6 | 送件檢核的管理員 / 稽核員判定永遠不成立（讀不存在的欄位 / import 不存在的模組） | `test_submission_check_acl.py` |
| v1.14.6 | 預覽 ACL 在認不出 upload_id 時整個跳過 | `test_preview_acl_failopen.py` |
| v1.14.6 | pdf-to-office 的改善報告沒有驗歸屬（隔壁的預覽有驗） | 滲透測試 §4c |
| v1.14.6 | 稽核員可刪歷史紀錄、可輪替掉資料庫備份；管理員反而被擋 | `test_auditor_readonly.py` |
| v1.14.6 | 「法務資安」角色實際等於「一般使用者」 | `test_roles_rbac.py` |
| v1.16.26 | 帶 token 呼叫 `/tools/…/download` 等工具路徑，啟用認證時 token 沒被驗、直接 302 到登入頁（手冊實跑在認證關閉的實例上，看不到） | `test_api_token_on_tool_paths.py` |
| v1.16.54 | 沒有用印權限的一般使用者在 PDF 編輯器挑得到資產庫的公司印章並蓋進 PDF；印章圖檔網址登入即可下載（issue #54） | `test_asset_access_by_permission.py` |
| v1.14.6 | API token 強制驗證開啟時管理區全壞（判斷用「路徑含 /api/」） | `test_api_gate_and_csrf_edges.py` |
| v1.14.6 | 另外三個工具的預覽端點切出空 id 就跳過檢查（doc-deident / pdf-editor / pdf-to-image） | `test_preview_acl_failopen.py`（prefix 那組） |
| v1.14.6 | `/workspace/save` 有自己一份歸屬判斷 → 無主作業可被任何登入者存走 | `test_job_id_acl.py::test_workspace_save_denies_ownerless_job` |
| v1.16.81 | 管理員查看公文案件清單（所有人的標題與主旨）不留紀錄；知識庫查詢不留紀錄 | `test_official_doc_cases.py::test_admin_viewing_everyones_case_list_is_audited`、`…::test_drafting_with_the_knowledge_base_is_audited_without_the_query` |
| v1.16.80 | 管理員 / 稽核員打開別人的送件前檢核案件不留稽核紀錄；管理員開啟查不到擁有者的暫存檔也不留 | `test_submission_check_acl.py::test_privileged_read_of_someone_elses_case_is_audited`、`test_admin_privacy_boundary.py` |
| v1.14.6 | 送件檢核用 403 / 404 區分「不是你的」與「不存在」（id 查詢介面） | `test_submission_check_acl.py::test_non_owner_response_is_indistinguishable_from_not_found` |
| v1.14.6 | 匯出的 CSV / xlsx 可被試算表當公式執行（註解作者來自對方的 PDF） | `test_csv_injection.py` |
| v1.14.6 | 四個工具的網頁介面對非 PDF 回 500（對外 API 有驗、網頁介面沒有） | `test_upload_validation_parity.py` |
| v1.14.6 | 十處把伺服器回應直接塞進 innerHTML | `test_upload_validation_parity.py::test_no_template_injects_raw_server_text_into_innerhtml` |
| v1.11.81 | Host 標頭污染 `request.url.path` 可繞過工具權限閘 | 滲透測試 §3 |
| v1.12.52 | 群組成員數用 `innerHTML` 塞 DOM 文字（CodeQL High） | 源碼掃描 |
| v1.12.33-34 | `csrf.js` 沒包相對 URL fetch 與 XMLHttpRequest → 所有上傳在正式環境 403 | **headless 實測上傳**（conftest 關閉 CSRF，測不到） |
| v1.4.83 | 任一登入者拿到別人的 `upload_id` 即可下載對方 PDF | `test_safe_paths_and_owner.py`（原名 test_upload_owner_acl，改名後這裡漏同步 —— 2026-08-16 稽核抓到，照抄舊指令會直接 file not found）|

### v1.14.17 — 多頁合併的水平越權（每次發版必過）

自動化：`tests/test_authz_boundaries.py`（`test_nup_preview_does_not_leak_another_users_pdf`
與 `test_nup_rejects_path_traversal_in_upload_id`）、`tests/test_id_from_body_acl.py`
（靜態掃描已擴充到 pydantic 模型欄位）。

- [ ] 兩個帳號 A / B。A 在「多頁合併」上傳檔案取得上傳編號
- [ ] B 用 A 的編號送 `POST /tools/pdf-nup/preview` → **403 / 404**，不可以拿到圖
- [ ] B 用 A 的編號送 `POST /tools/pdf-nup/generate` → **403 / 404**
- [ ] A 自己送同樣的請求 → **200 且拿得到 PNG**（不可以為了擋別人把工具擋死）
- [ ] `upload_id` 帶 `../` → 400 / 403 / 404
- [ ] **未啟用認證時工具照常可用**（單機模式不受影響）

> 這一類的通則：**id 從 JSON 內文的 pydantic 模型欄位進來時最容易漏**，因為函式
> 參數看起來只有一個 `opts`。新增這種形狀的端點時，處理函式一定要收 `Request`
> 並呼叫歸屬檢查。

### v1.14.17 — 升級遷移、角色補發、垂直越權收斂（每次發版必過）

自動化：`tests/test_migration_fk_cascade.py`（9）、`tests/test_seed_bootstrap_gap.py`（4）、
`tests/test_tool_search_keywords.py`（45）、`tests/test_authz_boundaries.py`（16）。

- [ ] **升級不可以清空群組成員**：拿一份 v2 之前的舊資料庫（或用測試裡的模擬），
      升級後群組成員關係與 session 都還在
- [ ] **舊安裝升級後每支工具都有人拿得到**：特別確認「乘車證明整理」與「頁面加框」
      在既有客戶的一般使用者角色裡看得到
- [ ] 每支工具都搜尋得到（中英文各試一次）
- [ ] 非 admin 打 `/tools/submission-check/admin-stats` → 403；admin → 200
- [ ] 未登入打 `/login` `/healthz` 進得去；打 `/login-xxx` 這類不存在的路徑
      **不可以**被當成公開而略過認證
- [ ] 401 / 403 錯誤頁的訊息不會把輸入原樣渲染成 HTML

### v1.14.29 — 外部弱點掃描的四項（每次發版必過）

> 第三方弱點掃描工具對 v1.14.28 的完整掃描（公開文件不點名產品，與 CHANGELOG 同一決定）。程式面三項已修，憑證屬維運。

- [ ] **登出的 `Set-Cookie` 帶 `HttpOnly` 與 `SameSite`**
      （`Max-Age=0` 的刪除回應**不會沿用**建立當時的旗標，要再寫一次）
- [ ] 反向代理是 HTTPS 時（`X-Forwarded-Proto: https`）刪除回應**帶 `Secure`**
- [ ] **純 HTTP 時不可以帶 `Secure`** —— 加了瀏覽器會忽略那筆刪除，
      使用者按了登出卻沒真的登出
- [ ] 二階段驗證的待驗證 cookie **建立時**就要有 `Secure`（裝的是待驗證權杖）
- [ ] 多層代理的 `X-Forwarded-Proto: https, http` 要判成 HTTPS（取最外層那段）
- [ ] 參數驗證失敗（422）的回應**不含使用者送來的原字串**，且帶 `nosniff`
- [ ] 管理區稽核頁四種攻擊字串：**帶 admin session 用瀏覽器實跑**，
      注入元素 0、alert 0（只看回應文字不夠，要看瀏覽器有沒有真的執行）
- [ ] TLS 憑證效期 —— **維運事項**，續期後以
      `openssl s_client ... | openssl x509 -noout -dates` 確認
- [ ] **頁面不印裸 epoch 時間戳**（掃描報告的 Timestamp Disclosure，Low）——
      清單 / 稽核頁的時間一律經格式化再輸出（`app/main.py` 與
      `app/core/roles.py` 內有註解說明）；新頁面要放時間就抽查一次
      回應內文 `grep -E '1[0-9]{9}'` 不可命中

### v1.14.31 — 對抗式驗證的一輪（每次發版必過）

> 對每一項近期改動預設「它還有漏」，實際構造輸入去重現。
> 全部有對應的 pytest，這裡列的是**發版前要親眼確認的那幾條**。

**反射面（外部掃描判 High 的那個形狀，只修了一半）**

- [ ] 上傳一個檔名是 `<script>alert(1)</script>.txt` 的檔到任一支只收 PDF 的
      工具 → 回應的 `detail` **不可以**原樣含 `<` `>` `"` `'`
      （`app/main.py:scrub_error_detail`，換成全形對應字元）
- [ ] 同時確認正常訊息沒被洗壞：`image > 50MB`、`1 <= cols*rows <= 64`、
      `engine must be 'easyocr'` 讀起來要跟原本一樣

**協定判斷（多層代理）**

- [ ] `X-Forwarded-Proto: https, http` → 回應**要有** HSTS
      （不是只有 cookie 帶 Secure —— 那會是同一份回應裡兩個矛盾的答案）
- [ ] `X-Forwarded-Proto: http, https` → **不可以**有 HSTS（最外層才算）
- [ ] SSO 的對外網址不可以長出 `'https, http://主機名'`（會讓登入全掛）

**字型（v1.14.19 慘案的防線）**

- [ ] 把 `retain_gids` 暫時改成 `False` → `tests/test_cjk_font_renders.py`
      **必須變紅**（改回來）。這道保險曾經存在一整個版本卻從來沒有生效，
      因為它取樣到的永遠是 ASCII 標點
- [ ] 表單填一段含**零寬空格**（U+200B）的公司名 → 產出**不可以**變成十幾 MB
- [ ] 逐句翻譯的對照 PDF：內嵌字型要是 **TC**（不是第 0 套的日文），
      檔案不可以是十幾 MB

**服務阻斷**

- [ ] 上傳一張 6000×6000 的全白 PNG 當騎縫章 → 處理時間**秒級**，
      期間 `/healthz` 不可以被卡住（第一版是 27 秒、全站一起等）

**表單自動填寫（最要緊的工具）**

- [ ] 跑 `temp_pdfs/_regress/run_fill_regress.py --compare <基準>`：
      **不可以有任何一份變差**
- [ ] 一列格子裡「分行」被框成 60~70pt 的小格 → **不可以 500**
- [ ] 「分行：」（有冒號）那一欄要填得到（值在冒號右邊）
- [ ] 值填進去之後**不可以壓在原本就印在紙上的字上面** ——
      判準用「實際畫出來的字」，不是 slot 方框

**檔案保留**

- [ ] 設「暫存 1 小時、作業結果 48 小時」→ 47 小時前的作業結果**要還在**
      （`jobs_hours` 曾經是完全沒被讀過的死設定）

**匯出**

- [ ] 逐句翻譯 / 電子發票 / 乘車證明的 CSV：以 `=` `+` `-` `@` 開頭的內容
      要被中和（欄位標題也算 —— 那是使用者自己可設定的）

**通知**

- [ ] 失敗通知的內文**不可以有伺服器路徑**（只留檔名）
- [ ] 從未設定過偏好的使用者：預設**不可以**送到 Slack / Discord 這種
      團隊共用頻道

**前端（要用真瀏覽器，不能只看原始碼）**

- [ ] 逐頁掃 CSP 違規 → 0 條（`temp/seam-ui/cdp_csp_violations.py`）
- [ ] 沒有重複 id、沒有 JS 例外（`temp/seam-ui/cdp_frontend_fixes.py`）

### v1.17.6 — MCP（AI 連接）的資安檢查（每次發版必過）

MCP 讓 Claude Code、Codex CLI、OpenCode 這類 AI 客戶端用 API Token 呼叫本站的工具，是另一個對外入口。
自動化：`tests/test_mcp_server.py`、`tests/test_mcp_cross_user.py`、`tests/test_mcp_admin.py`、`tests/test_mcp_tools_batch2.py`、`tests/test_mcp_tools_batch3.py`、`tests/test_mcp_tools_batch4.py`、`tests/test_ai_stamp.py`。

- [ ] **預設關閉**：全新安裝 `/mcp` 回 403；打開總開關後，Token 也要逐張勾「允許 MCP」才能用
- [ ] **沒開認證也要 Token**（不因為站台沒開登入就放行）；開了認證時 Token 一定要有擁有者，沒有擁有者的 Token 403
- [ ] **綁定使用者只有管理員能做**（`/admin/api/tokens/owner`）：使用者不存在或停用 400、非管理員擋下、稽核 `api_token_change` 記名稱不記 Token（`tests/test_api_token_owner.py`）
- [ ] **權限跟著擁有者**：沒有某支工具權限的人，工具清單看不到、`jtdt_run` 叫不動；拿掉 MCP 這層的過濾，原本的 API 一樣回 403
- [ ] **跨使用者**：兩個帳號各一張 Token，B 拿 A 的檔案編號 / 作業編號全部失敗，錯誤訊息跟不存在的編號一樣
- [ ] **票證**：只認 `X-JTDT-Ticket` 標頭（網址帶票證不行）、綁來源 IP、15 分鐘過期、上傳成功才算用掉、比申報大 413、雜湊不符 422；下載票證綁檔案，用滿次數失效
- [ ] **Origin**：瀏覽器帶來的 `Origin` 不是站台網址一律 403（防 DNS rebinding 讓網頁替使用者呼叫）
- [ ] **限流與同時數**：超過每分鐘次數回錯誤、同一張 Token 同時跑的作業有上限
- [ ] **高風險工具預設不開放**：`pdf_decrypt`（解除密碼）要管理員另外勾
- [ ] **經 MCP 一律不用 LLM**（v1.17.37）：有 LLM 選項的工具，MCP 送出的請求不帶開啟 LLM 的欄位；送件前檢核強制 `allow_llm=false`，站台有 LLM 也不呼叫（`tests/test_mcp_tools_batch3.py`，變異驗證過）
- [ ] **JSON 參數先檢查**：`ops`、`per_page`、`bookmarks` 這類 JSON 字串參數在 MCP 端就檢查格式與型別（陣列 / 物件），不合格不送到原本的 API
- [ ] **背景作業的報告網址只收同一支工具底下的路徑**：`meta.result_url` 不是 `/tools/<這支工具>/` 開頭、含 `..` 或怪字元就不去讀
- [ ] **用印、騎縫章、加密紙本備份、事務機不經 MCP**：歸類表是「不開放」或「另有規則」；用印與騎縫章只能走 AI 用印（下一項）
- [ ] **AI 用印**（v1.17.38）：預設關閉；未啟用登入、總開關關、Token 沒勾「用印」或沒綁使用者、沒有工具權限、章沒開 AI 使用 / 不在對象 / 受管制，任何一項不符都 403 而且工具清單看不到；只收資產庫裡經 AI 授權的章（不收上傳圖、png_b64、文字章，不回章圖）；需要確認時核准只收網頁登入（帶 Token 403），核准前 AI 拿不到檔案、24 小時作廢刪檔；每日上限；Token 改綁或關 MCP 收回「用印」；原本的用印 API 經 MCP 403；稽核不含章圖（`tests/test_ai_stamp.py`，四處變異驗證）
- [ ] **允許 MCP 的 Token 只能經 MCP 使用**（v1.17.38）：AI 客戶端讀得到自己設定裡的 Token；拿它直接呼叫任何一般 API（含偽造 `X-JTDT-Via-MCP`）一律 403 `MCP_TOKEN_REST_DENIED`，否則 MCP 那一層的限制（AI 用印、編輯器不收圖片、不開放的工具）全部可以繞過；沒勾 MCP 的 Token 照常（`tests/test_ai_stamp_hardening.py`，變異驗證）
- [ ] **AI 用印的時間差與資源**（v1.17.38）：核准時重新檢查條件（收回授權 → `void` 刪檔）；每日上限算處數、檢查與扣用量同一個 BEGIN IMMEDIATE 交易、失敗退回；每人待確認 20 件 / 500 MB；暫存檔用完刪、不登記上傳者；位置不收 NaN / 無限大；通知不帶 AI 決定的檔名；紀錄 CSV 中和公式（含前置空白）
- [ ] **使用者自訂正規式（ReDoS）**（v1.17.38）：清單處理、文字 / 文件去識別化拒絕「無上限重複裡面又有無上限重複」；其餘放專用 2 條執行緒池、滿了回 503 不排隊，卡住的正規式拖不垮其他工具（Python `re` 沒有逾時，殘餘風險：池子被佔住時這三支工具的自訂正規式暫時不能用）
- [ ] **PDF 編輯器經 MCP 不收圖片**（v1.17.37）：資產庫的印章、簽名不可以從編輯器繞過 AI 用印的規則 —— MCP 端拒絕 `image` / `asset_id` / `existing_src`，原本的 API 認得「經 MCP」（程序內密值，外部偽造不了）再擋一次；兩層各自變異驗證（`tests/test_mcp_tools_batch4.py`）
- [ ] **表單自動填寫**：公司清單只有編號與名稱；指定不存在的公司回 400，不會把別家公司的資料填進去；MCP 預設不開放
- [ ] **稽核不記密碼與 Token**：`mcp_call` 的參數裡 `user_pw` / `password` 等不出現在紀錄；只記 Token 名稱
- [ ] **檔案傳輸用 HTTPS**：站台網址是 `http://` 時票證預設不發（管理員另外允許才行）

> 這一類的通則：**MCP 不可以比原本的 API 寬**。新增 MCP 工具一律走原本的 API 執行，
> 權限、檔案擁有者、稽核都沿用；不要在 MCP 這層另寫一套檢查。

### v1.16.54 — 資產庫的印章與簽名照用印權限給（每次發版必過）

自動化：`tests/test_asset_access_by_permission.py`（13，12 個變異各自紅）、
`tests/test_asset_image_acl.py`。規則只寫在 `app/core/asset_access.py` 一份。

- [ ] 一般使用者（沒有「用印與簽名」）：`GET /tools/pdf-editor/assets` 只有 Logo、`restricted` 為真
- [ ] 同一個人 `GET /assets/<印章或簽名>/file` 與 `/thumb` → **403**（不可以下載下來再用「上傳新圖片」貼回去）
- [ ] 同一個人 `POST /tools/pdf-editor/save` 帶印章的 `asset_id` → **403**，暫存目錄裡沒有產出檔
- [ ] 只有「文管」角色的人拿浮水印圖 → 403；Logo → 200
- [ ] 有權限的人 → 200，而且編輯器手動存檔寫進用印簽名歷史
- [ ] 騎縫章的 `/stamp-preview`、`/preview`、`/submit` 帶簽名 / 浮水印 / Logo 的 `asset_id` → **400**（騎縫章只收印章；只有騎縫章權限的人不可以拿它把簽名蓋出去）
- [ ] **未啟用認證時全部照舊**

> 這一類的通則：**藏起清單不等於擋住** —— 存檔端點與圖檔網址都是可以自己組請求打的。
> 新增「會用到資產庫圖」的工具時，三個地方都要走 `asset_access.can_use()`。
