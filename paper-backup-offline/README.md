# 加密紙本備份：離線還原程式

[English](#english) | [日本語](#日本語)

「加密紙本備份」印出來的主文件與金鑰分片，用這裡的離線還原程式就能還原：一個 HTML 檔，**用 Chrome 打開就能用，不需連網，也不需要原本那台 Jason Tools 文件工具箱主機**。打開時會先核對內嵌程式的 SHA-256，對不上就不執行；另附自我測試。

建議下載一份，跟紙本一起存放（例如放在 USB 隨身碟），或交給每一位分片保管人各一份。

| 檔案 | 介面語言 |
|---|---|
| `jtdt-paper-backup-offline-zh-Hant.html` | 繁體中文 |
| `jtdt-paper-backup-offline-en.html` | English |
| `jtdt-paper-backup-offline-ja.html` | 日本語 |
| `SHA256SUMS` | 三個檔的 SHA-256 |
| `pack.json` | 產生這份程式的版本與 SHA-256 |

## 用瀏覽器下載

1. 打開 <https://github.com/jasoncheng7115/jt-doc-tools/tree/main/paper-backup-offline>
2. 點要的語言（例如 `jtdt-paper-backup-offline-zh-Hant.html`）
3. 按右上角的「Download raw file」（下載圖示）

不要直接開 `raw.githubusercontent.com` 的網址來用：瀏覽器只會顯示原始碼文字。要用那個網址的話，請按右鍵「另存連結」存成 `.html` 檔。

## 用指令下載

**Linux / macOS**

```bash
base=https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/paper-backup-offline
for f in jtdt-paper-backup-offline-zh-Hant.html jtdt-paper-backup-offline-en.html jtdt-paper-backup-offline-ja.html SHA256SUMS; do
  curl -fLO "$base/$f"
done
sha256sum -c SHA256SUMS          # macOS 改用：shasum -a 256 -c SHA256SUMS
```

三行都顯示 `OK` 才算下載完整。只要一種語言時，`for` 那行只留那個檔與 `SHA256SUMS`，核對時改用 `sha256sum -c --ignore-missing SHA256SUMS`。

**Windows（PowerShell）**

```powershell
$base = "https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/paper-backup-offline"
$f = "jtdt-paper-backup-offline-zh-Hant.html"
Invoke-WebRequest "$base/$f" -OutFile $f -UseBasicParsing -TimeoutSec 60
Invoke-WebRequest "$base/SHA256SUMS" -OutFile SHA256SUMS -UseBasicParsing -TimeoutSec 60
(Get-FileHash $f -Algorithm SHA256).Hash.ToLower()
Select-String -Path SHA256SUMS -Pattern $f
```

最後兩行印出來的雜湊要一模一樣。

## 使用方式

1. 在 Chrome 打開下載的 HTML 檔（按兩下，或拖進 Chrome 視窗）。
2. 想先確認這份程式正常，按頁面下方的「執行自我測試」，看到「自我測試通過」即可。
3. 把主文件與分片的掃描 PDF 或照片拖進來（PNG、JPG 也可以），可以一次多個、不分順序；手機拍的照片請先傳到電腦。QR 掃不出來的分片，照紙上印的文字備援碼手動輸入。
4. 主文件區塊收齊、分片夠門檻後按「還原」。解密只在這台電腦的瀏覽器裡進行，不會送出去。

整個過程不需連網，可以先拔掉網路線再打開。

## 版本與相容性

- 這裡固定放最新的一份，網址不會因為版本改變；產生的版本記在 `pack.json`。
- 新版離線還原程式一定能還原舊版建立的備份（格式第 1 版）。
- 有帳號的人也可以從工具頁下載離線還原程式；那一份檔名帶版本號，SHA-256 以工具頁顯示的為準。

---

## English

The offline restore tool for **Encrypted Paper Backup**: a single HTML file that **works when opened in Chrome, with no network and without the original Jason Tools server**. On start it checks the SHA-256 of its embedded code and refuses to run if it does not match; a self-test is included. Keep a copy with the paper (for example on a USB drive), or give one to each share holder.

**Download in a browser**: open <https://github.com/jasoncheng7115/jt-doc-tools/tree/main/paper-backup-offline>, click the file for your language (`jtdt-paper-backup-offline-en.html`), then click "Download raw file". Opening the `raw.githubusercontent.com` address directly only shows the source text; use "Save link as" instead.

**Download with a command (Linux / macOS)**

```bash
base=https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/paper-backup-offline
curl -fLO "$base/jtdt-paper-backup-offline-en.html"
curl -fLO "$base/SHA256SUMS"
sha256sum -c --ignore-missing SHA256SUMS     # macOS: shasum -a 256 -c --ignore-missing SHA256SUMS
```

**Windows (PowerShell)**

```powershell
$base = "https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/paper-backup-offline"
$f = "jtdt-paper-backup-offline-en.html"
Invoke-WebRequest "$base/$f" -OutFile $f -UseBasicParsing -TimeoutSec 60
Invoke-WebRequest "$base/SHA256SUMS" -OutFile SHA256SUMS -UseBasicParsing -TimeoutSec 60
(Get-FileHash $f -Algorithm SHA256).Hash.ToLower()
Select-String -Path SHA256SUMS -Pattern $f
```

The two hashes printed at the end must match.

**Use**: open the file in Chrome (the "Run self-test" button checks the tool itself). Drop in scanned PDFs or photos (PNG, JPG) of the main document and the shares, several at once and in any order; type in the backup code for a share whose QR cannot be read. Click "Restore" once all main-document blocks and enough shares are in. Decryption happens only in this browser; nothing is sent anywhere. The latest pack is always at the same address; the version it was built from is in `pack.json`. Newer packs always restore backups made by older versions (format version 1).

---

## 日本語

**暗号化紙バックアップ**のオフライン復元ツールです。1 つの HTML ファイルで、**Chrome で開けばネットワークも元の Jason Tools サーバーも不要**です。起動時に埋め込みコードの SHA-256 を照合し、一致しなければ実行しません。セルフテストも付いています。紙と一緒に（USB メモリなどで）保管するか、各分割鍵の保管者に 1 部ずつ渡してください。

**ブラウザでダウンロード**：<https://github.com/jasoncheng7115/jt-doc-tools/tree/main/paper-backup-offline> を開き、`jtdt-paper-backup-offline-ja.html` をクリックして「Download raw file」を押します。`raw.githubusercontent.com` のアドレスを直接開くとソースが表示されるだけなので、右クリックの「名前を付けてリンク先を保存」を使ってください。

**コマンドでダウンロード（Linux / macOS）**

```bash
base=https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/paper-backup-offline
curl -fLO "$base/jtdt-paper-backup-offline-ja.html"
curl -fLO "$base/SHA256SUMS"
sha256sum -c --ignore-missing SHA256SUMS     # macOS：shasum -a 256 -c --ignore-missing SHA256SUMS
```

**Windows（PowerShell）**

```powershell
$base = "https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/paper-backup-offline"
$f = "jtdt-paper-backup-offline-ja.html"
Invoke-WebRequest "$base/$f" -OutFile $f -UseBasicParsing -TimeoutSec 60
Invoke-WebRequest "$base/SHA256SUMS" -OutFile SHA256SUMS -UseBasicParsing -TimeoutSec 60
(Get-FileHash $f -Algorithm SHA256).Hash.ToLower()
Select-String -Path SHA256SUMS -Pattern $f
```

最後に表示される 2 つのハッシュが一致すれば正常です。

**使い方**：Chrome でファイルを開きます（「セルフテストを実行」でツール自体を確認できます）。主文書と分割鍵をスキャンした PDF や写真（PNG、JPG）をドロップします。複数まとめて、順不同で構いません。QR が読めない分割鍵は、紙に印刷されたバックアップコードを入力します。主文書のブロックがそろい、分割鍵がしきい値に達したら「復元」を押します。復号はこのブラウザ内だけで行われ、どこにも送信されません。最新版は常に同じアドレスにあり、生成したバージョンは `pack.json` に記録されています。新しい版は旧版で作成したバックアップを必ず復元できます（フォーマット第 1 版）。
