/* Shared vehicle-display helpers for air and ferry trips — the non-train counterpart
 * of wagon_img.js. Used by the public trip page and the live global map; the trips
 * table (dynamic_trips.html) keeps its own inline copy for now.
 *
 * Loading this file more than once on a page is harmless — it only (re)defines
 * idempotent window functions.
 *
 * airframeInfo(trip)
 *   Describe an air trip's aircraft as { code, label }, or null when it has none.
 *   `material_type` holds the ICAO type code itself (e.g. "B738"); the readable name
 *   comes from the `airliners` join as manufacturer + model, which is present for
 *   ~95% of logged types — the rest fall back to showing the raw code.
 *
 *   Note: do NOT reuse the "[[CODE]] Name" string built by dynamic_trips.html's
 *   renderMaterialType. That bracket form is a display-layer encoding invented there
 *   and decoded immediately after, in processHiddenData; it is never stored.
 *
 * airframeImgSrc(base, code)
 *   URL of the side-view shape for an ICAO type code. `base` is the airliners image
 *   directory (no trailing slash).
 *
 * loadAirframe(base, code, onLoad)
 *   Resolve a shape to an <img> element, or to null when no artwork exists for that
 *   type. Only ~106 of the many possible type codes are drawn, so a miss is the
 *   normal case, not an error. Probing with an Image() rather than an XHR means the
 *   hit path costs exactly one request and the browser cache does the rest.
 *   onLoad(img) fires after the image has decoded — hook container relayout here,
 *   since the height it adds is not known until then.
 *
 * fetchAircraftPhoto(reg) / fetchVesselPhoto(endpoint, reg, at)
 *   Resolve a registration to { thumb, link, attr, country } or null; a vessel also
 *   resolves a `name`, since a ship may be logged by name, IMO or MMSI and is always
 *   shown by name — the one it carried at `at`, the trip's own date, so a crossing keeps
 *   the ship as it was rather than as it is now. Both are memoised per registration for the page's lifetime, so
 *   reopening a popup or re-toggling a leg costs nothing and a repeated vehicle is
 *   fetched once.
 *
 *   Call these only in response to a user action (opening a popup / expanding a
 *   leg), never during a bulk render: aircraft photos hit the planespotters API,
 *   and a vessel MISS makes the server run a Google Images search, download the
 *   photo and write it to disk (see get_vessel_picture in src/g_search.py).
 *
 * regCountry(reg, registrations) / loadRegistrations(url)
 *   Aircraft registration prefix → ISO country code, using static/data/registrations.json.
 *   Longest-prefix wins ("G-" before "G"). Ships carry their country in the API
 *   response instead, so they don't need this.
 *
 * photoHTML(photo)
 *   Markup for a resolved photo, reusing the global .vehiclePhotoContainer /
 *   .vehiclePhotoAttr styles from style2.css.
 */
(function () {
  function airframeInfo(trip) {
    if (!trip) return null;
    var code = (trip.material_type || '').trim();
    if (!code) return null;
    var name = [trip.manufacturer, trip.model]
      .filter(function (p) { return p; })
      .join(' ')
      .trim();
    return { code: code, label: name || code };
  }

  function airframeImgSrc(base, code) {
    // material_type is free text, not a validated code — roughly 4.5% of logged
    // values are hand-typed ("Boeing 737-800") and may contain spaces or slashes.
    // Those simply 404 into the error handler, but they must not corrupt the path.
    return base + '/' + encodeURIComponent(code) + '.png';
  }

  function loadAirframe(base, code, onLoad) {
    if (!code) return null;
    var img = new Image();
    img.className = 'airframe';
    img.height = 50;
    img.addEventListener('load', function () {
      if (onLoad) onLoad(img);
    });
    // No artwork for this type — drop the element so it never renders as a broken
    // image. Callers append eagerly and rely on this self-removal.
    img.addEventListener('error', function () {
      if (img.parentNode) img.parentNode.removeChild(img);
      if (onLoad) onLoad(null);
    });
    img.src = airframeImgSrc(base, code);
    return img;
  }

  /* ── photos ─────────────────────────────────────────────────────────────── */
  // reg -> Promise<photo|null>. Caching the PROMISE (not the result) also collapses
  // concurrent lookups of the same registration into a single request.
  var photoCache = {};

  function escAttr(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fetchAircraftPhoto(reg) {
    if (!reg) return Promise.resolve(null);
    var key = 'air:' + reg;
    if (!photoCache[key]) {
      photoCache[key] = fetch('https://api.planespotters.net/pub/photos/reg/' + encodeURIComponent(reg))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          var p = d && d.photos && d.photos[0];
          if (!p) return null;
          return {
            thumb: p.thumbnail_large && p.thumbnail_large.src,
            link: p.link,
            attr: '©' + (p.photographer || '') + '/planespotters.net',
            country: ''
          };
        })
        // A third-party outage must not break the rest of the panel.
        .catch(function () { return null; });
    }
    return photoCache[key];
  }

  function fetchVesselPhoto(endpoint, reg, at) {
    if (!reg) return Promise.resolve(null);
    // `at` is part of the key: one ship answers with different names on different dates
    // once it has been renamed, so two trips on the same hull are two lookups.
    var key = 'ship:' + reg + '@' + (at || '');
    if (!photoCache[key]) {
      photoCache[key] = fetch(endpoint + '?vesselName=' + encodeURIComponent(reg)
                              + (at ? '&at=' + encodeURIComponent(at) : ''))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          // The endpoint answers `null` when it finds nothing — guard the shape
          // before reading it, which is what makes the trips table throw today.
          if (!d || !d.image) return null;
          return {
            thumb: d.image,
            link: d.link || '',
            // The credit comes with the photo now that they arrive from more than one
            // place: vesselfinder, Wikimedia Commons (whose licence requires naming the
            // author) or an admin's own upload, which needs none.
            attr: d.attribution || '',
            country: d.country || '',
            // The ship's name, whichever of name/IMO/MMSI was searched for. Empty
            // when no name is on record; callers keep showing what was typed.
            name: d.name || ''
          };
        })
        .catch(function () { return null; });
    }
    return photoCache[key];
  }

  var registrationsPromise = null;
  function loadRegistrations(url) {
    if (!registrationsPromise) {
      registrationsPromise = fetch(url)
        .then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; });
    }
    return registrationsPromise;
  }

  function regCountry(reg, registrations) {
    if (!reg || !registrations) return '';
    for (var i = reg.length; i > 0; i--) {
      var candidate = reg.slice(0, i);
      if (candidate in registrations) return registrations[candidate].iso;
    }
    return '';
  }

  function photoHTML(photo) {
    if (!photo || !photo.thumb) return '';
    // A photo may need a credit without having a page to link to, and an admin's own
    // upload needs none at all — so the credit hangs off the text, not the link.
    var credit = '';
    if (photo.attr) {
      credit = photo.link
        ? '<a href="' + escAttr(photo.link) + '" target="_blank" rel="noopener" class="vehiclePhotoAttr">' + escAttr(photo.attr) + '</a>'
        : '<span class="vehiclePhotoAttr">' + escAttr(photo.attr) + '</span>';
    }
    return '<div class="vehiclePhotoContainer"><img src="' + escAttr(photo.thumb) + '" alt="">' + credit + '</div>';
  }

  window.airframeInfo    = airframeInfo;
  window.airframeImgSrc  = airframeImgSrc;
  window.loadAirframe    = loadAirframe;
  window.fetchAircraftPhoto = fetchAircraftPhoto;
  window.fetchVesselPhoto   = fetchVesselPhoto;
  window.loadRegistrations  = loadRegistrations;
  window.regCountry         = regCountry;
  window.photoHTML          = photoHTML;
})();
