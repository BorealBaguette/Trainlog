// Enhances a currency <select> (options carry data-country/data-emoji, see
// src/currency.py) into a searchable flag+name combo. The <select> stays in the DOM,
// hidden, as the real form field. Callers toggling visibility must target
// `${selectId}-combo-wrap`, not the select — it's always display:none.
function enhanceCurrencySelect(selectId) {
  const select = document.getElementById(selectId);
  if (!select || select.dataset.comboEnhanced) return;
  select.dataset.comboEnhanced = '1';

  function normalizeSearch(s) {
    return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  }

  let currencyNames = null;
  try {
    currencyNames = new Intl.DisplayNames(
      [typeof langId !== 'undefined' ? langId : 'en'],
      { type: 'currency' }
    );
  } catch (e) {}

  const wrap = document.createElement('div');
  wrap.className = 'currency-combo';
  wrap.id = `${selectId}-combo-wrap`;
  wrap.innerHTML =
    '<button type="button" class="currency-combo-btn">' +
      '<span class="currency-combo-flag-inline"></span>' +
      '<span class="currency-combo-label"></span>' +
    '</button>' +
    '<div class="currency-combo-panel">' +
      '<input type="text" class="currency-combo-search" autocomplete="off">' +
      '<div class="currency-combo-list"></div>' +
      '<div class="currency-combo-empty" style="display:none;">No results</div>' +
    '</div>';

  select.parentNode.insertBefore(wrap, select);
  select.classList.add('currency-combo-native');

  const btn = wrap.querySelector('.currency-combo-btn');
  const flagInline = wrap.querySelector('.currency-combo-flag-inline');
  const label = wrap.querySelector('.currency-combo-label');
  const panel = wrap.querySelector('.currency-combo-panel');
  const search = wrap.querySelector('.currency-combo-search');
  const list = wrap.querySelector('.currency-combo-list');
  const empty = wrap.querySelector('.currency-combo-empty');

  function apply(item) {
    if (!item) return;
    select.value = item.code;
    flagInline.innerHTML = item.emoji;
    label.textContent = item.code; // collapsed button stays compact; full name is the tooltip + list rows
    btn.title = item.labelText;
    items.forEach(i => i.el.classList.toggle('active', i === item));
  }

  function choose(item) {
    apply(item);
    select.dispatchEvent(new Event('change', { bubbles: true }));
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
    items.forEach(i => {
      const match = !q || i.el.dataset.search.includes(q);
      i.el.style.display = match ? '' : 'none';
      anyVisible = anyVisible || match;
    });
    empty.style.display = anyVisible ? 'none' : '';
  }

  const items = [];
  for (const option of select.options) {
    const code = option.value;
    let name = currencyNames ? currencyNames.of(code) : null;
    if (name) name = name.charAt(0).toUpperCase() + name.slice(1);
    const emoji = option.dataset.emoji || (option.dataset.country ? getFlagEmoji(option.dataset.country) : '');
    const labelText = name ? `${name} (${code})` : code;

    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'currency-combo-item';
    el.dataset.search = normalizeSearch(`${labelText} ${code}`);
    el.innerHTML = `<span class="currency-combo-flag">${emoji}</span><span>${labelText}</span>`;
    list.appendChild(el);

    const item = { code, emoji, labelText, el };
    el.addEventListener('click', () => choose(item));
    items.push(item);
  }

  btn.addEventListener('click', () => panel.classList.contains('show') ? close() : open());
  search.addEventListener('input', () => filter(search.value));
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { close(); btn.focus(); }
    if (e.key === 'Enter') {
      e.preventDefault();
      const visible = items.find(i => i.el.style.display !== 'none');
      if (visible) choose(visible);
    }
  });

  apply(items.find(i => i.code === select.value));
}
