// The tickets of a trip, picked in a modal (templates/ticket_picker.html), kept in the order they
// were picked: their names are shown in it.
//
// opts:
//   url        the user's tickets (tickets.get_all_tickets)
//   staticUrl  prefix of the logos' paths
//   input      the hidden input holding the ids, comma separated (none in bulk)
//   field      where the picked tickets show as pills, opening the modal (none in bulk)
//   selected   the ids picked to begin with
//   context    returns the trip's {date, operators}: tickets valid that day come first, even no
//              longer active, and `onSuggest` is told of the one run by the trip's operator if
//              only one is (else null), for the page to offer it. Nothing is picked for the
//              user: a trip outside the ticket's zone would be picked wrong.
//   onSubmit   bulk: called with the ids picked and whether they replace the trips' own
//   title      the modal's, when not the form's
//   lang       the reader's language, for dates and prices
//   texts      {add, none}
//
// Returns {open, ids, has, toggle, refresh}.
function ticketPicker(opts) {
  const $modal = $('#ticketPickerModal');
  const $list = $('#ticketPickerList');
  const $search = $('#ticketPickerSearch');
  const $input = opts.input ? $(opts.input) : null;
  const $field = opts.field ? $(opts.field) : null;
  let tickets = [];
  let picked = (opts.selected || []).map(Number).filter(Boolean);
  let showAll = false;

  // A clipping card with uses left covers any day, or any within its dates if it has some;
  // a single ticket for a given day, that day.
  function covers(ticket, date) {
    if (ticket.uses != null && ticket.used >= ticket.uses) return false;
    const from = ticket.valid_from || ticket.valid_on, until = ticket.valid_until || ticket.valid_on;
    if (!(from || until)) return ticket.uses != null && ticket.active;
    return Boolean(date && (!from || date >= from) && (!until || date <= until));
  }

  // Not valid yet, so not active, but no more past than an active one.
  function isUpcoming(ticket) {
    if (ticket.uses != null && ticket.used >= ticket.uses) return false;
    const now = new Date();
    const today = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0')
      + '-' + String(now.getDate()).padStart(2, '0');
    const from = ticket.valid_from || ticket.valid_on;
    return Boolean(from && from > today);
  }

  function runsOn(ticket, operators) {
    return [ticket.operator, ...String(ticket.secondary_operators || '').split(',')]
      .map(normalizeOperatorName).some(name => name && operators.includes(name));
  }

  function byId(uid) {
    return tickets.find(t => t.uid === uid);
  }

  function logo(ticket, cls) {
    return ticket && ticket.logo
      ? $('<img>').addClass(cls).attr({ src: opts.staticUrl + ticket.logo, alt: '' })
      : $('<i class="bi bi-ticket-perforated text-muted"></i>').addClass(cls);
  }

  function formatDay(day) {
    return new Date(day + 'T00:00:00').toLocaleDateString(opts.lang,
      { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function validity(ticket) {
    if (ticket.valid_from && ticket.valid_until && ticket.valid_from !== ticket.valid_until) {
      return formatDay(ticket.valid_from) + ' → ' + formatDay(ticket.valid_until);
    }
    return formatDay(ticket.valid_from || ticket.valid_until || ticket.valid_on || ticket.purchasing_date);
  }

  // The picked tickets in the form, each removable; the field opens the modal.
  function renderField() {
    if ($input) $input.val(picked.join(','));
    if (!$field) return;
    $field.empty();
    picked.forEach(uid => {
      const ticket = byId(uid);
      const $pill = $('<span class="ticket-pill"></span>')
        .append(logo(ticket, 'ticket-pill-logo'))
        .append($('<span class="ticket-pill-name"></span>').text(ticket ? ticketLabel(ticket) : '#' + uid));
      $pill.append($('<button type="button" class="ticket-pill-x" aria-label="×">&times;</button>')
        .on('click', e => { e.stopPropagation(); toggle(uid); }));
      $field.append($pill);
    });
    $field.append($('<span class="ticket-picker-add"></span>')
      .append('<i class="bi bi-plus-lg"></i> ').append(document.createTextNode(opts.texts.add)));
  }

  function row(ticket, suggested) {
    const index = picked.indexOf(ticket.uid);
    const $row = $('<label class="list-group-item d-flex align-items-center gap-3 ticket-picker-row"></label>')
      .toggleClass('active-pick', index !== -1)
      .attr('data-search', normalizeForSearch([ticket.name, ticket.operator, ticket.product, ticket.secondary_operators].join(' ')));
    $row.append($('<input type="checkbox" class="form-check-input mt-0">').prop('checked', index !== -1)
      .on('change', () => toggle(ticket.uid)));
    $row.append($('<span class="ticket-picker-logo"></span>').append(logo(ticket)));
    const $text = $('<span class="flex-grow-1" style="min-width: 0;"></span>')
      .append($('<span class="d-block text-truncate fw-medium"></span>').text(ticketLabel(ticket)))
      .append($('<span class="d-block small text-muted"></span>').text(
        validity(ticket) + (ticket.price != null ? ' · ' + formatCurrency(opts.lang, ticket.price, ticket.currency) : '')));
    $row.append($text);
    if (suggested) $row.append('<i class="bi bi-star-fill text-warning" aria-hidden="true"></i>');
    return $row;
  }

  // Tickets valid on the trip's day first, then those active or picked; the rest on asking,
  // or when searched for.
  function renderList() {
    const ctx = opts.context ? opts.context() : null;
    const operators = ctx ? String(ctx.operators || '').split(',').map(normalizeOperatorName).filter(Boolean) : [];
    const covering = ctx ? tickets.filter(t => covers(t, ctx.date)) : [];
    const fitting = covering.filter(t => runsOn(t, operators));
    const query = normalizeForSearch($search.val() || '');
    const shown = new Set();
    $list.empty();

    function section(title, items) {
      const rows = items.filter(t => !shown.has(t.uid)
        && (!query || normalizeForSearch([t.name, t.operator, t.product, t.secondary_operators].join(' ')).includes(query)));
      if (!rows.length) return;
      if (title) $list.append($('<div class="ticket-picker-section"></div>').text(title));
      const $group = $('<div class="list-group mb-2"></div>');
      rows.forEach(t => { shown.add(t.uid); $group.append(row(t, fitting.length === 1 && fitting[0] === t)); });
      $list.append($group);
    }

    section(covering.length ? $('#ticketPickerModal').data('thatDay') : null, covering);
    section($('#ticketPickerModal').data('current'),
      tickets.filter(t => t.active || isUpcoming(t) || picked.includes(t.uid)));
    if (showAll || query) section($('#ticketPickerModal').data('past'), tickets);
    $('#ticketPickerShowAll').toggle(!showAll && !query && tickets.some(t => !shown.has(t.uid)));
    if (!shown.size) $list.append($('<p class="text-muted small mb-0"></p>').text(opts.texts.none));
    if (opts.onSuggest) opts.onSuggest(fitting.length === 1 ? fitting[0] : null);
  }

  function toggle(uid) {
    uid = Number(uid);
    const index = picked.indexOf(uid);
    if (index === -1) picked.push(uid); else picked.splice(index, 1);
    renderField();
    if ($modal.is(':visible')) renderList();
    if ($input) $input[0].dispatchEvent(new Event('change', { bubbles: true }));
  }

  function open() {
    // One modal for the page: the picker opening it takes it over.
    $modal.data('picker', api);
    showAll = false;
    // In bulk, each time from nothing: the trips picked have tickets of their own.
    if (opts.onSubmit) picked = [];
    $search.val('');
    $modal.toggleClass('ticket-picker-bulk', Boolean(opts.onSubmit));
    $('#ticketPickerTitle').text(opts.title || $modal.data('title'));
    $('#ticketPickerReplace').prop('checked', false);
    renderList();
    $modal.modal('show');
  }

  const api = {
    open: open,
    ids: () => picked.slice(),
    has: uid => picked.includes(Number(uid)),
    toggle: toggle,
    refresh: () => { renderField(); renderList(); },
    _renderList: renderList,
    _showAll: () => { showAll = true; renderList(); },
    _submit: () => opts.onSubmit && opts.onSubmit(picked.slice(), $('#ticketPickerReplace').prop('checked')),
  };

  if ($field) $field.on('click', open);
  renderField();
  fetch(opts.url)
    .then(response => response.json())
    .then(data => { tickets = data.tickets; renderField(); renderList(); })
    .catch(error => console.error('Error:', error));
  return api;
}

// The modal's own controls act for the picker that opened it.
$(function () {
  const current = () => $('#ticketPickerModal').data('picker');
  $('#ticketPickerSearch').on('input', () => current() && current()._renderList());
  $('#ticketPickerShowAll').on('click', () => current() && current()._showAll());
  $('#ticketPickerSubmit').on('click', () => current() && current()._submit());
});
