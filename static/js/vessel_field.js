/* The ferry registration field.
 *
 * A ship answers to three written identifiers — its name, its 7-digit IMO and its
 * 9-digit MMSI — and `vessels` holds all three on one row (migration 0054). What gets
 * STORED in a trip's `reg` is the number, preferring the IMO: it is unique and
 * permanent, where a name is neither (several ships are called Express, and ships get
 * renamed). What gets DISPLAYED everywhere is the name, resolved at read time.
 *
 * That leaves the edit form as the one place a bare number would face the user, so the
 * field carries a hint line under it naming the ship the number resolves to.
 *
 * initVesselField(input, hint, url)
 *   Wires jQuery UI autocomplete onto `input` (matching name, IMO or MMSI alike, and
 *   folding the ship-type prefix so 'MS Fjordtroll', 'M/S Fjordtroll' and 'Fjordtroll'
 *   are one search) and keeps `hint` in step — on selection, on load, and after a manual
 *   edit. The hint shows the flag, the name and the cached photo, so the ship behind the
 *   number can be checked at a glance. `url` is the /vesselAutocomplete endpoint.
 *
 * Free text stays valid throughout: this only offers the ships already on record, and
 * a reg that resolves to nothing simply gets no hint and is displayed as typed.
 */
(function () {
  function vesselLabel(vessel) {
    var numbers = [
      vessel.imo ? 'IMO ' + vessel.imo : null,
      vessel.mmsi ? 'MMSI ' + vessel.mmsi : null
    ].filter(Boolean).join(' · ');
    var flag = (vessel.country && typeof getFlagEmoji === 'function')
      ? getFlagEmoji(vessel.country) + ' ' : '';
    var name = vessel.name || vessel.value;
    return flag + name + (numbers ? ' — ' + numbers : '');
  }

  // The suggestion the user picks writes its stored form: the IMO where there is one,
  // then the MMSI, and only a name for a ship carrying neither.
  function storedValue(vessel) {
    return vessel.imo || vessel.mmsi || vessel.name || '';
  }

  // Which suggestion (if any) the typed text actually IS, rather than merely starts.
  // The endpoint does prefix and substring matching, so "977" comes back with Megastar
  // attached — that is a suggestion, not a resolution, and must not be labelled as one.
  //
  // The server flags it, rather than this comparing strings: only vessel_resolve() knows
  // that 'MS Fjordtroll' and 'Fjordtroll' are the same ship, and a second opinion here
  // would drift from the one the trip is actually displayed through.
  function exactMatch(vessels) {
    for (var i = 0; i < vessels.length; i++) {
      if (vessels[i].exact) return vessels[i];
    }
    return null;
  }

  function initVesselField(input, hint, url) {
    var $input = $(input);
    var $hint = $(hint);
    if (!$input.length) return;

    /* What the number in the field actually is: the ship's flag, its name, and its
       cached photo as a thumbnail that zooms on hover — enough to tell at a glance that
       the right ship was picked. The photo comes back with the suggestion, so showing it
       costs no extra request; a ship with none simply shows the flag and the name. */
    function setHint(vessel) {
      if (!$hint.length) return;
      if (!vessel || !(vessel.name || vessel.image)) { $hint.empty().hide(); return; }

      $hint.empty();
      if (vessel.country && typeof getTooltipNew === 'function') {
        // The same flag-with-country-tooltip markup the rest of the site uses.
        $hint.append($(getTooltipNew(vessel.country)));
        $hint.find('[data-toggle="tooltip"]').tooltip();
      } else if (vessel.country && typeof getFlagEmoji === 'function') {
        $hint.append(document.createTextNode(getFlagEmoji(vessel.country) + ' '));
      }

      $hint.append($('<span class="vesselHintName"></span>').text(' ' + (vessel.name || '')));

      if (vessel.image) {
        // Shared with the admin register — see .ship-thumb in style2.css.
        var $thumb = $('<span class="ship-thumb"></span>');
        $('<img>').attr({ src: vessel.image, width: 64, height: 40, loading: 'lazy', alt: '' })
          // A cached row can outlive its file on disk (the local cache is absent in
          // dev): drop the thumbnail rather than show a broken image.
          .on('error', function () { $thumb.remove(); })
          .appendTo($thumb);
        $('<img class="ship-thumb-full">').attr({ src: vessel.image, loading: 'lazy', alt: '' })
          .appendTo($thumb);
        $hint.append(' ').append($thumb);
      }

      $hint.show();
    }

    // Name the ship a typed identifier stands for. Called on load and after edits, so
    // a number pasted in by hand is named too, not only one picked from the list.
    function resolve(term) {
      if (!term || term.trim().length < 2) { setHint(null); return; }
      $.getJSON(url, { query: term.trim() })
        .done(function (data) { setHint(exactMatch(data || [])); })
        .fail(function () { setHint(null); });
    }

    $input.autocomplete({
      minLength: 2,
      source: function (request, response) {
        $.getJSON(url, { query: request.term })
          .done(function (data) {
            response($.map(data || [], function (vessel) {
              return {
                label: vesselLabel(vessel),
                value: storedValue(vessel),
                vessel: vessel
              };
            }));
          })
          .fail(function () { response([]); });
      },
      select: function (event, ui) {
        setHint(ui.item.vessel);
      }
    });

    $input.on('change blur', function () { resolve($input.val()); });
    resolve($input.val());
  }

  window.initVesselField = initVesselField;
})();
