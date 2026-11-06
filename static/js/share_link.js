// Copy a share link without opening it: the ▲ menu beside the trips table's "View on map".
//
// The link is made by POST /public/share (format=json). `show` ('p', 'c', 'pc' or
// 'n') is exactly what it shows: the owner's usual choice comes back as the plain
// key, anything else as a key of its own (src/share_view.py).
(function () {
  function requestShareLink(shareUrl, tripIds, show) {
    const body = new URLSearchParams({ trips: tripIds, format: 'json' });
    if (show) body.append('show', show);
    return fetch(shareUrl, { method: 'POST', body: body })
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(data => new URL(data.url, window.location.origin).href);
  }

  // Copy text that is still on its way. The clipboard call has to be made inside
  // the click itself (Safari refuses one made after the fetch), hence a
  // ClipboardItem fed a promise where the browser has it, writeText otherwise.
  function copyPromisedText(textPromise) {
    if (window.AndroidFunction) {
      return textPromise.then(text => window.AndroidFunction.copyTextToClipboard(text));
    }
    if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      try {
        const blob = textPromise.then(text => new Blob([text], { type: 'text/plain' }));
        return navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })])
          .catch(() => textPromise.then(text => navigator.clipboard.writeText(text)));
      } catch (e) { /* no promise support in ClipboardItem: fall through */ }
    }
    return textPromise.then(text => navigator.clipboard.writeText(text));
  }

  // `before`: a promise to wait for first (trips being made public).
  // `labels`: { copied, copy } — the toast, and the prompt title if copying fails.
  window.copyShareLink = function (shareUrl, tripIds, show, labels, before) {
    const link = Promise.resolve(before).then(() => requestShareLink(shareUrl, tripIds, show));
    return copyPromisedText(link)
      .then(() => showShareToast(labels.copied))
      .catch(() => link.then(url => window.prompt(labels.copy, url), () => {}));
  };

  function showShareToast(text) {
    let toast = document.getElementById('shareToast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'shareToast';
      toast.setAttribute('role', 'status');
      toast.style.cssText = 'position:fixed;left:50%;bottom:90px;transform:translateX(-50%);z-index:2000;' +
        'padding:10px 18px;border-radius:6px;background:#333;color:#fff;font-size:15px;' +
        'box-shadow:0 2px 8px rgba(0,0,0,.3);transition:opacity .3s;pointer-events:none;';
      document.body.appendChild(toast);
    }
    toast.textContent = text;
    toast.style.opacity = '1';
    clearTimeout(toast._hide);
    toast._hide = setTimeout(() => { toast.style.opacity = '0'; }, 2500);
  }
})();
