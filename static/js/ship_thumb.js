/*
 * Places the hover preview of a ship photo (.ship-thumb > .ship-thumb-full).
 *
 * The preview used to be an absolutely positioned sibling, which meant it was clipped by
 * whatever scroll container it happened to sit in — a .table-responsive wrapper, a
 * scrollable modal body, DataTables' own wrapper — and in a table row that is exactly
 * where it always sits. So it is fixed instead (style2.css) and put in place here,
 * against the viewport, where no ancestor's overflow can reach it.
 *
 * Above the thumbnail when there is room, below it otherwise, and always kept inside the
 * window. Delegated, so previews rendered after load — the admin register, the backfill
 * page, the vessel field on the trip forms — need nothing of their own.
 */
(function () {
  var MARGIN = 8;   // breathing room against the window edge
  var GAP = 6;      // between the thumbnail and its preview

  function place($thumb, $full) {
    var rect = $thumb[0].getBoundingClientRect();
    var width = $full.outerWidth();
    var height = $full.outerHeight();

    // Left-aligned with the thumbnail, pulled back in when that would overflow the
    // window — the common case for a photo column near the right edge of a table.
    var left = Math.max(MARGIN, Math.min(rect.left, window.innerWidth - width - MARGIN));

    // Above by preference: a preview below the row covers the rows you are comparing
    // against. Below only when there is no room above.
    var top = rect.top - height - GAP;
    if (top < MARGIN) {
      top = rect.bottom + GAP;
      if (top + height > window.innerHeight - MARGIN) {
        top = Math.max(MARGIN, window.innerHeight - height - MARGIN);
      }
    }

    $full.css({ left: Math.round(left) + 'px', top: Math.round(top) + 'px' });
  }

  $(document).on('mouseenter focusin', '.ship-thumb', function () {
    var $thumb = $(this);
    var $full = $thumb.find('.ship-thumb-full').first();
    if (!$full.length) return;

    // Shown before measuring — a hidden element has no size — but hidden from view
    // until it has been put somewhere, or it flashes at the top left of the window.
    $full.css({ display: 'block', visibility: 'hidden' });
    place($thumb, $full);
    $full.css('visibility', '');

    // A lazily loaded preview has no height on the first hover, so the first placement
    // is a guess; correct it as soon as the picture arrives.
    var image = $full[0];
    if (image.tagName === 'IMG' && !image.complete) {
      $full.one('load', function () { place($thumb, $full); });
    }
  });

  $(document).on('mouseleave focusout', '.ship-thumb', function () {
    $(this).find('.ship-thumb-full').first().css({ display: '', left: '', top: '' });
  });
})();
