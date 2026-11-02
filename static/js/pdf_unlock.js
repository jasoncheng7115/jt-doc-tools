// 有開啟密碼的 PDF：上傳前先問密碼，解開後換成解開的那份。
//
// 使用者 2026-11-02：拉進一份設了開啟密碼的 PDF，用印頁什麼反應都沒有。每一支吃 PDF 的工具
// 都是同一個形狀（工具端打不開檔，畫面沒接住錯誤），所以收在這裡做一次：
//   window.unlockPdfFiles(files, toolId) → Promise<File[]>
//     * 不是 PDF、沒有 `/Encrypt` 的檔原樣回傳（只讀檔頭檔尾，不上傳）；
//     * 有 `/Encrypt` 的才送去 `/pdf-password/check` 問「真的要密碼嗎」——
//       只限制列印 / 編輯的 PDF（只有擁有者密碼）打得開，不攔；
//     * 要密碼：有「PDF 密碼解除」權限 → 輸入密碼，伺服器解開後換成解開的檔；
//       沒有權限 → 說明要先解除密碼。兩種都附「PDF 密碼解除」工具的連結。
//     * 使用者取消或解不開的那份**不回傳**（呼叫端不往下送）。
// 共用上傳元件（file_upload.js）會自動呼叫；自己寫上傳框的工具要自己呼叫一次。
(function () {
  const SNIFF_BYTES = 256 * 1024;
  const MAX_TRIES = 5;

  function isPdf(f) {
    return !!f && (/\.pdf$/i.test(f.name || '') || f.type === 'application/pdf');
  }

  // 加密的 PDF 在 trailer（或交叉參照串流）裡一定有 /Encrypt。trailer 在檔尾；
  // 線性化的檔第一頁那份 trailer 在檔頭 —— 兩頭各讀一段就夠。
  async function hasEncryptMarker(file) {
    const n = file.size || 0;
    const parts = [file.slice(0, Math.min(n, SNIFF_BYTES))];
    if (n > SNIFF_BYTES) parts.push(file.slice(Math.max(SNIFF_BYTES, n - SNIFF_BYTES)));
    for (const p of parts) {
      const text = new TextDecoder('latin1').decode(await p.arrayBuffer());
      if (text.indexOf('/Encrypt') >= 0) return true;
    }
    return false;
  }

  async function check(file) {
    const fd = new FormData();
    fd.append('file', file, file.name || 'document.pdf');
    try {
      const r = await fetch('/pdf-password/check', { method: 'POST', body: fd });
      return r.ok ? await r.json() : null;
    } catch (_e) { return null; }
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function toolLink(info) {
    return '<a href="' + esc(info.tool_url || '/tools/pdf-decrypt/') + '" target="_blank" rel="noopener">'
      + esc(tr('PDF 密碼解除')) + '</a>';
  }

  async function unlockOne(file, info, toolId) {
    const name = '<b>' + esc(file.name) + '</b>';
    if (!info.can_unlock) {
      await window.showAlert(
        tr('「{0}」有開啟密碼，這裡打不開。請先到「{1}」解除密碼，再上傳解除後的檔案。')
          .replace('{0}', name).replace('{1}', toolLink(info)),
        { title: tr('這份 PDF 有密碼保護'), kind: 'warn', html: true });
      return null;
    }
    let msg = tr('「{0}」有開啟密碼。輸入密碼後會先解開再繼續處理；也可以先到「{1}」解除密碼。')
      .replace('{0}', name).replace('{1}', toolLink(info));
    for (let i = 0; i < MAX_TRIES; i++) {
      const pw = await window.showPrompt(msg, {
        title: tr('這份 PDF 有密碼保護'), html: true, inputType: 'password',
        placeholder: tr('PDF 開啟密碼'), okText: tr('解開'), cancelText: tr('取消'),
      });
      if (pw === null || pw === undefined) return null;
      const fd = new FormData();
      fd.append('file', file, file.name || 'document.pdf');
      fd.append('password', pw);
      if (toolId) fd.append('from_tool', toolId);
      let r;
      try {
        r = await fetch(info.unlock_url || '/tools/pdf-decrypt/unlock', { method: 'POST', body: fd });
      } catch (e) {
        await window.showAlert(tr('解開失敗：') + (e.message || ''), { kind: 'error' });
        return null;
      }
      if (r.ok) {
        const blob = await r.blob();
        return new File([blob], file.name, { type: 'application/pdf', lastModified: file.lastModified });
      }
      if (r.status === 400) {
        msg = tr('密碼不正確，請再試一次。') + '<br>' + tr('「{0}」有開啟密碼。輸入密碼後會先解開再繼續處理；也可以先到「{1}」解除密碼。')
          .replace('{0}', name).replace('{1}', toolLink(info));
        continue;
      }
      await window.showAlert(await window.friendlyServerError(r, tr('解開失敗')), { kind: 'error' });
      return null;
    }
    await window.showAlert(tr('密碼錯誤太多次，請確認密碼後重新上傳。'), { kind: 'warn' });
    return null;
  }

  async function unlockPdfFiles(files, toolId) {
    const out = [];
    for (const f of Array.from(files || [])) {
      if (!isPdf(f) || !(await hasEncryptMarker(f))) { out.push(f); continue; }
      const info = await check(f);
      if (!info || !info.needs_password) { out.push(f); continue; }   // 只有擁有者密碼 / 檢查失敗：照常
      const opened = await unlockOne(f, info, toolId);
      if (opened) out.push(opened);
    }
    return out;
  }

  window.unlockPdfFiles = unlockPdfFiles;
})();
