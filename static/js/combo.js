// Searchable combo: flag box + button (closed state, no flag in the text)
// and a filterable list panel (open state, flag per row + real search).
function normalizeSearch(s) {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function initCombo(id, { onApply } = {}) {
  const wrap = document.querySelector(`[data-combo="${id}"]`);
  const btn = document.getElementById(`${id}-btn`);
  const label = document.getElementById(`${id}-label`);
  const icon = document.getElementById(`${id}-icon`);
  const hidden = document.getElementById(id);
  const panel = document.getElementById(`${id}-panel`);
  const search = document.getElementById(`${id}-search`);
  const empty = document.getElementById(`${id}-empty`);
  const items = Array.from(document.getElementById(`${id}-list`).children);
  if (!wrap || !btn || !hidden) return null;

  function apply(item) {
    if (!item) return;
    hidden.value = item.dataset.value;
    label.textContent = item.dataset.label;
    icon.innerHTML = item.dataset.flag || '';
    items.forEach(i => i.classList.toggle('active', i === item));
    if (onApply) onApply(item);
  }

  function select(item) {
    apply(item);
    close();
  }

  function open() {
    panel.classList.add('show');
    search.value = '';
    filter('');
    search.focus();
    document.addEventListener('click', onDocClick);
  }
  function close() {
    panel.classList.remove('show');
    document.removeEventListener('click', onDocClick);
  }
  function onDocClick(e) {
    if (!wrap.contains(e.target)) close();
  }
  function filter(query) {
    const q = normalizeSearch(query.trim());
    let anyVisible = false;
    items.forEach(item => {
      const match = !q || item.dataset.search.includes(q);
      item.style.display = match ? '' : 'none';
      anyVisible = anyVisible || match;
    });
    if (empty) empty.style.display = anyVisible ? 'none' : '';
  }

  btn.addEventListener('click', () => {
    panel.classList.contains('show') ? close() : open();
  });
  search.addEventListener('input', () => filter(search.value));
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { close(); btn.focus(); }
    if (e.key === 'Enter') {
      e.preventDefault();
      select(items.find(i => i.style.display !== 'none'));
    }
  });
  items.forEach(item => item.addEventListener('click', () => select(item)));

  return { apply, items };
}


// Languages, as translations ship them ("🇫🇷 Français"): the flag split out into the icon box
// and the list rows, the button text the name alone. Items with a data-icon (a Font Awesome
// class) and a data-label instead keep them.
function langCombo(id, opts) {
  const combo = initCombo(id, opts);
  if (!combo) return null;
  combo.items.forEach(item => {
    let flag = '', name = item.dataset.label || item.dataset.raw;
    if (item.dataset.icon) {
      flag = `<i class="${item.dataset.icon}"></i>`;
    } else {
      const chars = Array.from(item.dataset.raw);
      if (chars.length && /\p{Regional_Indicator}/u.test(chars[0])) {
        flag = chars.slice(0, 2).join('');
        name = chars.slice(2).join('').trim();
      }
      name = name.charAt(0).toUpperCase() + name.slice(1);
    }
    item.dataset.label = name;
    item.dataset.flag = flag;
    item.dataset.search = normalizeSearch(`${name} ${item.dataset.value}`);
    item.innerHTML = `<span class="settings2-combo-flag">${flag}</span><span>${name}</span>`;
  });
  combo.apply(combo.items.find(i => i.dataset.value === document.getElementById(id).value));
  return combo;
}

// Shared setup for combos whose items already carry a static data-icon
// (FA class) and data-label from the template — just wire up search/flag.
function initStaticCombo(id, opts) {
  const combo = initCombo(id, opts);
  if (!combo) return null;
  combo.items.forEach(item => {
    item.dataset.flag = `<i class="${item.dataset.icon}"></i>`;
    item.dataset.search = normalizeSearch(item.dataset.label);
    item.innerHTML = `<span class="settings2-combo-flag">${item.dataset.flag}</span><span>${item.dataset.label}</span>`;
  });
  combo.apply(combo.items.find(i => i.dataset.value === document.getElementById(id).value));
  return combo;
}

