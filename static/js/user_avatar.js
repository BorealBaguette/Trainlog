// User avatars: a coloured circle with the username's first letter, swapped for
// the user's Discord picture when they turned that on in settings and the viewer
// may see it (see user_avatars in app.py for who may see whose).
//
// Markup: <span class="user-avatar" data-username="alice"></span>, sized with
// --avatar-size (default 32px). Call paintUserAvatars(root, endpoint) once the
// elements are in the page; endpoint is url_for('user_avatars', username=...).

(function () {
  // Same hue per username everywhere, the dashboard's friend badges included
  function usernameColor(name) {
    let hash = 0;
    for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
    const hue = Math.abs(hash) % 360;
    if (document.documentElement.getAttribute('data-bs-theme') === 'dark') {
      return { bg: `hsl(${hue},35%,26%)`, color: `hsl(${hue},70%,82%)` };
    }
    return { bg: `hsl(${hue},70%,90%)`, color: `hsl(${hue},55%,32%)` };
  }

  // One lookup per username per page, however many times it is painted
  const known = {};

  function showPicture(el, src) {
    if (el.querySelector('img.user-avatar-img')) return;
    const img = new Image();
    img.className = 'user-avatar-img';
    img.alt = '';
    img.onload = () => el.prepend(img);
    img.src = src;
  }

  function paintUserAvatars(root, endpoint) {
    const els = (root || document).querySelectorAll('.user-avatar[data-username]');
    const missing = new Set();
    els.forEach(el => {
      const name = el.dataset.username;
      if (!el.dataset.painted) {
        const c = usernameColor(name);
        el.style.background = c.bg;
        el.style.color = c.color;
        el.insertAdjacentText('afterbegin', [...name][0] || '?');
        el.dataset.painted = '1';
      }
      if (!(name in known)) missing.add(name);
      else if (known[name]) showPicture(el, known[name]);
    });
    if (!endpoint || !missing.size) return;

    const params = new URLSearchParams();
    missing.forEach(name => { params.append('u', name); known[name] = null; });
    fetch(`${endpoint}?${params}`)
      .then(r => r.ok ? r.json() : {})
      .then(avatars => {
        Object.assign(known, avatars);
        els.forEach(el => {
          const src = avatars[el.dataset.username];
          if (src) showPicture(el, src);
        });
      })
      .catch(() => {});
  }

  window.usernameColor = usernameColor;
  window.paintUserAvatars = paintUserAvatars;
})();
