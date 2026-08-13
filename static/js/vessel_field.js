/* The ferry registration field.
 *
 * A ship answers to three written identifiers — its name, its 7-digit IMO and its
 * 9-digit MMSI — and `vessels` holds all three on one row (migration 0054). What gets
 * STORED in a trip's `reg` is the hull key — the ship's IMO, or a synthetic id where it
 * has none — on a hidden input, so the visible field can show the ship's name instead. A name is neither unique nor permanent (several ships are called Express,
 * and ships get renamed), and an MMSI is reissued when a ship changes flag. What gets
 * DISPLAYED is the name the ship carried on the trip's own date, resolved at read time.
 *
 * That leaves the edit form as the one place a bare number would face the user, so the
 * field carries a hint line under it naming the ship the number resolves to.
 *
 * initVesselField(input, hint, url)
 *   Wires jQuery UI autocomplete onto `input` (matching name, IMO or MMSI alike, and
 *   folding the ship-type prefix so 'MS Fjordtroll', 'M/S Fjordtroll' and 'Fjordtroll'
 *   are one search) and keeps `hint` in step — on selection, on load, and after a manual
 *   edit. The hint shows the flag, the name AS OF THE TRIP'S DATE and the cached photo,
 *   so the ship behind the number can be checked at a glance. `url` is the /vesselAutocomplete endpoint.
 *
 * Free text stays valid throughout: this only offers the ships already on record, and
 * a reg that resolves to nothing simply gets no hint and is displayed as typed.
 */
(function () {
  // What the ship was called on the trip's date — the name the trip will display — with
  // the name it goes by now appended when they differ. Searching matches every name a
  // hull has carried, so a search for "Amorella" against a 2017 trip must answer
  // "Amorella", and against a 2023 one "Mega Victoria (was Amorella)".
  function vesselNames(vessel) {
    var name = vessel.name || vessel.matched_name || vessel.value;
    var also = null;
    if (vessel.current_name && vessel.current_name !== name) {
      also = 'now ' + vessel.current_name;          // the trip predates a rename
    } else if (vessel.matched_name && vessel.matched_name !== name) {
      also = 'was ' + vessel.matched_name;          // searched for by a former name
    }
    return { name: name, also: also };
  }

  function vesselLabel(vessel) {
    var numbers = [
      vessel.imo ? 'IMO ' + vessel.imo : null,
      vessel.mmsi ? 'MMSI ' + vessel.mmsi : null
    ].filter(Boolean).join(' · ');
    var flag = (vessel.country && typeof getFlagEmoji === 'function')
      ? getFlagEmoji(vessel.country) + ' ' : '';
    var names = vesselNames(vessel);
    return flag + names.name + (names.also ? ' (' + names.also + ')' : '')
         + (numbers ? ' — ' + numbers : '');
  }

  /* The date the trip is for, if the form has one yet. Both the new and the edit page
     offer a precise start datetime and a date-only field, depending on the precision
     chosen; whichever is filled in is the moment to resolve the ship at. */
  function tripDate() {
    var candidates = ['#newTripStartDate', '#newTripStartOnlyDate', '#onlyDate'];
    for (var i = 0; i < candidates.length; i++) {
      var $el = $(candidates[i]);
      if ($el.length && $el.val()) return $el.val();
    }
    return '';
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

    /* The field SHOWS the ship's name and the form POSTS the hull key. A key is an
       internal handle — 'TL0000110' means nothing to anybody — and it was the last place
       one still faced the user, on the edit page of a trip that had been migrated.
       Moving the input's `name` onto a hidden sibling separates the two: both forms
       submit through $('form').serializeArray(), which takes hidden inputs and skips
       inputs without a name, so the trip keeps storing exactly what it stored before.
       If this script never runs the visible input keeps its own name and behaves as it
       always did. */
    var $key = $('<input type="hidden">')
      .attr('name', $input.attr('name') || 'reg')
      .val($input.val());
    $input.after($key).removeAttr('name');

    // Free text is stored as typed; only a ship we recognise is stored by its key.
    function setStored(value) { $key.val(value == null ? '' : value); }

    // Show the ship, store the key. Called on selection and whenever typed text turns
    // out to name a ship exactly.
    function showVessel(vessel) {
      setHint(vessel);
      if (!vessel) return;
      setStored(vessel.value);
      $input.val(vesselNames(vessel).name);
    }

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

      var names = vesselNames(vessel);
      $hint.append($('<span class="vesselHintName"></span>').text(' ' + names.name));
      if (names.also) {
        $hint.append($('<span class="text-muted"></span>').text(' (' + names.also + ')'));
      }

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
      if (!term || term.trim().length < 2) {
        setHint(null);
        setStored(term);            // free text, including an empty field
        return;
      }
      $.getJSON(url, { query: term.trim(), at: tripDate() })
        .done(function (data) {
          var vessel = exactMatch(data || []);
          // Typing a name or a number in full is the same act as picking it from the
          // list; anything else is free text and is stored as written.
          if (vessel) showVessel(vessel); else { setHint(null); setStored(term); }
        })
        .fail(function () { setHint(null); setStored(term); });
    }

    $input.autocomplete({
      minLength: 2,
      source: function (request, response) {
        $.getJSON(url, { query: request.term, at: tripDate() })
          .done(function (data) {
            response($.map(data || [], function (vessel) {
              return {
                label: vesselLabel(vessel),
                // What lands in the visible field: the name. The key goes to the
                // hidden input in showVessel().
                value: vesselNames(vessel).name,
                vessel: vessel
              };
            }));
          })
          .fail(function () { response([]); });
      },
      select: function (event, ui) {
        showVessel(ui.item.vessel);
        // jQuery UI would write ui.item.value into the field next; showVessel has
        // already put the ship's name there, and they are the same string.
      }
    });

    // What the user typed is what gets resolved...
    $input.on('change blur', function () { resolve($input.val()); });

    // ...but everywhere else the stored key is the thing to ask about: it is canonical,
    // where the visible text is only a name, and after a rename the two differ. Changing
    // the trip's date can change which name that key answers to, so the field follows.
    function reresolve() { resolve($key.val() || $input.val()); }
    $(document).on('change', '#newTripStartDate, #newTripStartOnlyDate, #onlyDate', reresolve);
    reresolve();
  }

  window.initVesselField = initVesselField;
})();
