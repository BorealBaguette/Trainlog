// A station's tracks and lines, in a dropdown at the end of its field: the new trip and
// edit trip pages. A page sets window.stopSlotOptions = { preferLines, texts: { track, line } }
// and keeps globalStationDict / globalStationTracks / globalStationLines (util.js).
//
// A station from quai comes with its numbered tracks and its lines, each with a point
// where it stops. Picking one at the origin, the destination or a via moves its point
// there, exact when it is a stop position on the track. A track is also saved as the
// platform, and a line at either end fills the line field if it is empty. Picking it again
// goes back to the station's own point. Metro and tram trips offer the station's lines,
// which is how they are known; other trips its tracks, which is what timetables give.
// Each place a choice can be made is a slot: the field, its dropdown, and where the choice
// goes (setPlatform, setExact; shown(on) when the dropdown appears or goes).

function stopSlot(o) {
  return $.extend({ selected: null, lineFilled: null, fillsLine: false }, o);
}

// The station a slot is for: its fixed label where the page keeps one (the edit page, whose
// field holds a name that can be retyped without leaving the station), else the field's.
function stopSlotLabel(slot) {
  return slot.stationLabel || slot.$input.val();
}

// The slot's station entry ([point, label, record?, exact?]): the field's own where it has one
// (a timetable's stop, whose label another stop of the run can share: out and back through
// Kerlaurent), else the one of its label.
function stopSlotEntry(slot) {
  return slot.$input.data('stationEntry') || globalStationDict[stopSlotLabel(slot)];
}

// "Gleis 7", "Voie 7" and "7" are the same track.
function stopKey(ref) {
  return String(ref || '').trim().toLowerCase()
    .replace(/^(voie|gleis|gl\.?|track|platform|quai|binario|v[ií]a|spoor|tor|peron)\s*/, '');
}

// Which of the station's stops to offer: 'line' or 'track', or null. Metro and tram trips
// fall back on tracks; the others never offer lines, which for a train are regional routes,
// not something one boards at.
function stopKind(label) {
  var tracks = (globalStationTracks[label] || []).length, lines = (globalStationLines[label] || []).length;
  if ((window.stopSlotOptions || {}).preferLines) return lines ? 'line' : (tracks ? 'track' : null);
  return tracks ? 'track' : null;
}

// The track (or line) `ref` names among the station's `list`: as written; else, for "6" where
// the station has "6a" and "6b" (one platform face in two halves, Birmingham New Street), the
// halves as one, at their middle, exact if both are on the track; else, for "5-6", the
// platform between those tracks; else, for "6a" where it has only "6", that. Null if none.
function findStop(list, ref) {
  var key = stopKey(ref);
  if (!key || !list) return null;
  var exact = list.find(function (c) { return stopKey(c.ref) === key; });
  if (exact) return exact;
  var parts = list.filter(function (c) {
    var k = stopKey(c.ref);
    return k.length === key.length + 1 && k.indexOf(key) === 0 && /[a-z]$/.test(k);
  });
  if (parts.length) {
    var mean = function (f) { return parts.reduce(function (t, c) { return t + c[f]; }, 0) / parts.length; };
    return { ref: ref, lat: mean('lat'), lng: mean('lng'), parts: parts,
             on_track: parts.every(function (c) { return c.on_track; }) };
  }
  // "5-6" (or "5/6"): the platform between tracks 5 and 6, either side, as the timetable
  // does not say which (Schiphol): between the two, so never exact.
  var pair = /^(\w+)\s*[-\/]\s*(\w+)$/.exec(key);
  if (pair) {
    var sides = [pair[1], pair[2]].map(function (k) {
      return list.find(function (c) { return stopKey(c.ref) === k; });
    }).filter(Boolean);
    if (sides.length === 1) return sides[0];
    if (sides.length === 2) {
      return { ref: ref, lat: (sides[0].lat + sides[1].lat) / 2, lng: (sides[0].lng + sides[1].lng) / 2,
               parts: sides, on_track: false };
    }
  }
  var whole = key.replace(/(\d)[a-z]$/, '$1');
  return whole !== key ? list.find(function (c) { return stopKey(c.ref) === whole; }) || null : null;
}

// The dropdown at the end of the field: the chosen track or line on its button (else an
// icon), the station's others in its menu. Hidden when the station has none. `label` is
// the station's, when the field does not hold it yet (an autocomplete pick).
function renderStops(slot, label) {
  label = label || stopSlotLabel(slot);
  var kind = stopKind(label);
  if (!(stopSlotEntry(slot) || globalStationDict[label]) || !kind) { hideStops(slot); return; }
  var texts = (window.stopSlotOptions || {}).texts || {};
  var title = kind === 'track' ? texts.track : texts.line;
  var list = (kind === 'track' ? globalStationTracks : globalStationLines)[label];
  var chosen = findStop(list, slot.selected);
  var $chips = $('<div class="track-menu-chips">');
  // In the order they are numbered: 1a, 1b, 3, … 10, 12; "A" before "B".
  list.slice().sort(function (a, b) {
    return String(a.ref).localeCompare(String(b.ref), undefined, { numeric: true, sensitivity: 'base' });
  }).forEach(function (c) {
    var active = !!chosen && (chosen === c || (chosen.parts || []).indexOf(c) !== -1);
    var $chip = $('<button type="button" class="track-chip">').text(c.ref).toggleClass('active', active)
      .on('click', function () { pickStop(slot, kind, active ? '' : c.ref); });
    if (c.colour) {
      $chip.css(active ? { background: c.colour, borderColor: c.colour, color: '#fff' }
                       : { borderColor: c.colour, boxShadow: 'inset 3px 0 0 ' + c.colour });
    }
    $chips.append($chip);
  });
  slot.$dropdown.find('.track-menu').empty()
    .append($('<div class="track-menu-title">').text(title), $chips);
  // The button says how good the point is: blue on the track (exact), grey on a platform
  // beside it, and light grey for a timetable's platform the station does not map, which
  // leaves the point at the station.
  var shown = chosen ? chosen.ref : slot.selected || '';
  var $toggle = slot.$dropdown.find('.track-toggle').empty().attr('title', title)
    .toggleClass('exact', !!(chosen && chosen.on_track))
    .toggleClass('placed', !!(chosen && !chosen.on_track))
    .toggleClass('unmapped', !chosen && !!shown);
  $toggle.append($('<i>').addClass(kind === 'track' ? 'bi bi-signpost-split' : 'bi bi-diagram-3'));
  // A line's colour as a dot, so that its number keeps the colour saying how exact it is.
  if (chosen && chosen.colour) $toggle.append($('<span class="track-dot">').css('background', chosen.colour));
  if (shown) $toggle.append($('<span>').text(shown));
  slot.$dropdown.prop('hidden', false);
  slot.shown(true);
}

function hideStops(slot) {
  slot.$dropdown.prop('hidden', true);
  slot.shown(false);
}

// Picks the track or line `ref` ('' unpicks it). A track the station does not have is
// still saved as the platform, with the station's own point.
function pickStop(slot, kind, ref) {
  var label = stopSlotLabel(slot);
  var entry = stopSlotEntry(slot);
  var choice = ref ? findStop((kind === 'track' ? globalStationTracks : globalStationLines)[label], ref) : null;
  if (entry) {
    if (!entry.stationPoint) entry.stationPoint = entry[0];
    entry[0] = choice ? [choice.lat, choice.lng] : entry.stationPoint;
    // A page whose route is already drawn redraws it from there (the edit page).
    if (slot.moved) slot.moved(entry[0], !!(choice && choice.on_track));
  }
  slot.setExact(!!(choice && choice.on_track));
  slot.selected = ref || null;
  if (kind === 'track') {
    slot.setPlatform(ref || '');
  } else if (slot.fillsLine) {
    if (slot.lineFilled && $('#lineName').val() === slot.lineFilled) $('#lineName').val('');
    slot.lineFilled = null;
    if (choice && !$('#lineName').val()) $('#lineName').val(slot.lineFilled = choice.ref);
  }
  renderStops(slot, label);
}
// A new or retyped station: whatever was picked at the old one no longer applies.
function forgetStop(slot) {
  if (slot.lineFilled && $('#lineName').val() === slot.lineFilled) $('#lineName').val('');
  slot.lineFilled = slot.selected = null;
  slot.setPlatform('');
  slot.setExact(false);
}

function watchStopSlot(slot) {
  slot.$input.on('autocompleteselect', function (e, ui) {
    forgetStop(slot);
    renderStops(slot, ui.item.value);
  });
  slot.$input.on('input', function () {
    forgetStop(slot);
    hideStops(slot);
  });
}
