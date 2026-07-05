/*
 * Reusable freehand route editor.
 *
 * Lets the user hand-draw a route on an existing Leaflet map: drag the start/end
 * markers, click the line to insert a waypoint, drag waypoints to shape the route.
 * Produces a compact, WYSIWYG path — one point per user waypoint plus geodesic curve
 * points only on long segments — so the line drawn is exactly the line saved.
 *
 * Used by the standalone freehand page and inline (toggled) by routing.html,
 * compose.html and edit_copy.html.
 *
 * Depends on globals already loaded by the map layouts: L (+ L.Geodesic, L.GeometryUtil),
 * getDistanceFromLocations, and markerIconStart / markerIconEnd from map.js.
 */
(function () {
  var INTERP_THRESHOLD_M = 100000; // segments shorter than this stay straight (2 pts)

  // Wrap a longitude into [-180, 180]. The wrap:false geodesic below produces continuous
  // longitudes that can run past ±180 over the antimeridian; those are invalid to store
  // (geopip rejects them), so every saved point is normalised.
  function normalizeLng(lng) {
    return ((lng + 180) % 360 + 360) % 360 - 180;
  }

  // Keep every waypoint exactly; add geodesic curve points only on long segments, with
  // density scaled to length, so we never store more points than needed.
  function buildSavePath(waypoints) {
    var result = [waypoints[0]];
    for (var i = 1; i < waypoints.length; i++) {
      var a = waypoints[i - 1], b = waypoints[i];
      var dist = getDistanceFromLocations(a, b);
      if (dist > INTERP_THRESHOLD_M) {
        var steps = Math.min(8, Math.max(1, Math.round(Math.log2(dist / INTERP_THRESHOLD_M)) + 1));
        var segPts = new L.Geodesic([a, b], { steps: steps, wrap: false }).getLatLngs()[0];
        for (var j = 1; j < segPts.length; j++) result.push(segPts[j]); // skip a (already added)
      } else {
        result.push(b);
      }
    }
    return result.map(function (p) { return L.latLng(p.lat, normalizeLng(p.lng)); });
  }

  // Split a normalised path into sub-lines at antimeridian crossings so a plain polyline
  // renders correctly (no horizontal jump across the map). No points are added or removed.
  function splitAtDateline(latlngs) {
    var lines = [[]];
    for (var i = 0; i < latlngs.length; i++) {
      if (i > 0 && Math.abs(latlngs[i].lng - latlngs[i - 1].lng) > 180) lines.push([]);
      lines[lines.length - 1].push(latlngs[i]);
    }
    return lines;
  }

  /*
   * createFreehandEditor(map, opts) -> controller
   *
   * opts:
   *   origin:[lat,lng], dest:[lat,lng]   required endpoints
   *   waypoints:[[lat,lng],...]          optional intermediate user points
   *   markerIconStart, markerIconEnd     optional (fall back to map.js globals)
   *   lineClassName                      optional polyline class
   *   fit                                fitBounds on init unless false
   *   onChange(path, distanceMeters)     called after every redraw
   *
   * controller: { getPath(), getWaypoints(), refresh(), destroy() }
   */
  function createFreehandEditor(map, opts) {
    opts = opts || {};
    var iconStart = opts.markerIconStart || (typeof markerIconStart !== 'undefined' ? markerIconStart : undefined);
    var iconEnd = opts.markerIconEnd || (typeof markerIconEnd !== 'undefined' ? markerIconEnd : undefined);
    var lineClassName = opts.lineClassName || 'polyline ferryPolyline pastPolyline';
    var onChange = opts.onChange || function () {};

    // 'points' (L.latLng[]) is the single source of truth for the route.
    var points = [];
    [opts.origin].concat(opts.waypoints || []).concat([opts.dest]).forEach(function (c) {
      if (c) points.push(L.latLng(c[0], c[1]));
    });

    var routeLine = null;
    var hitLine = null; // invisible, much wider twin of routeLine for easy tapping
    var waypointMarkers = [];
    var path = [];

    var origMarker = L.marker(points[0], { icon: iconStart, draggable: true }).addTo(map);
    var destMarker = L.marker(points[points.length - 1], { icon: iconEnd, draggable: true }).addTo(map);
    origMarker.on('dragend', function () { points[0] = origMarker.getLatLng(); refresh(); });
    destMarker.on('dragend', function () { points[points.length - 1] = destMarker.getLatLng(); refresh(); });

    function closestSegmentIndex(clickedPoint) {
      var min = Infinity, idx = -1;
      for (var i = 0; i < points.length - 1; i++) {
        var d = L.GeometryUtil.length([clickedPoint, L.GeometryUtil.closest(map, [points[i], points[i + 1]], clickedPoint)]);
        if (d < min) { min = d; idx = i; }
      }
      return idx;
    }

    function insertWaypoint(e) {
      var i = closestSegmentIndex(e.latlng);
      points.splice(i + 1, 0, e.latlng);
      refresh();
    }

    function refresh() {
      if (routeLine) map.removeLayer(routeLine);
      if (hitLine) map.removeLayer(hitLine);
      waypointMarkers.forEach(function (m) { map.removeLayer(m); });
      waypointMarkers = [];

      // Build the route once: one point per waypoint, curving only the long segments.
      // This exact array is both what we draw and what we save (WYSIWYG).
      path = buildSavePath(points);

      var drawLines = splitAtDateline(path);
      routeLine = L.polyline(drawLines, { className: lineClassName }).addTo(map);
      routeLine.on('click', insertWaypoint);
      // Transparent wide twin drawn on top of the visible line: gives a finger-sized
      // tap target for inserting waypoints, especially on mobile.
      hitLine = L.polyline(drawLines, { weight: 25, opacity: 0, interactive: true }).addTo(map);
      hitLine.on('click', insertWaypoint);

      for (var i = 1; i < points.length - 1; i++) {
        (function (idx) {
          var m = L.marker(points[idx], { draggable: true }).addTo(map);
          waypointMarkers.push(m);
          m.on('dragend', function () { points[idx] = m.getLatLng(); refresh(); });
        })(i);
      }

      var distance = 0;
      for (var k = 1; k < points.length; k++) distance += getDistanceFromLocations(points[k - 1], points[k]);
      onChange(path, distance);
    }

    if (opts.fit !== false) map.fitBounds(new L.LatLngBounds(points), { padding: [50, 50] });
    refresh();

    return {
      // L.latLng[] — JSON-serialises to [{lat,lng},...]; exactly what is drawn.
      getPath: function () { return path; },
      // Intermediate user waypoints (no endpoints), normalised, for the trips.waypoints column.
      getWaypoints: function () {
        return points.slice(1, -1).map(function (p) { return { lat: p.lat, lng: normalizeLng(p.lng) }; });
      },
      refresh: refresh,
      destroy: function () {
        if (routeLine) map.removeLayer(routeLine);
        if (hitLine) map.removeLayer(hitLine);
        waypointMarkers.forEach(function (m) { map.removeLayer(m); });
        waypointMarkers = [];
        map.removeLayer(origMarker);
        map.removeLayer(destMarker);
      }
    };
  }

  /*
   * addFreehandToggle(map, opts) -> control handle
   * Flips between router and freehand. `target` can be either:
   *   - a Leaflet map  -> a floating top-right control (used where the map is always visible,
   *     e.g. compose, whose panel stacks above the map on mobile), or
   *   - a panel DOM element (e.g. the #sidebar) -> a button inserted as its first child, so it
   *     stays reachable on mobile where a full-width sidebar covers the map.
   * opts.onToggle(active) receives the new state. Labels default to English.
   */
  function addFreehandToggle(target, opts) {
    opts = opts || {};
    var labelRouter = opts.labelRouter || 'Router';
    var labelFreehand = opts.labelFreehand || 'Freehand';
    var active = !!opts.active;
    var isMap = target && typeof target.addControl === 'function';

    ensureToggleStyles();

    var btn = document.createElement('button');
    btn.type = 'button';

    function render() {
      // The button advertises the mode you'd switch TO.
      var iconClass = active ? 'fa-solid fa-route' : 'fa-solid fa-pencil';
      var label = active ? labelRouter : labelFreehand;
      btn.className = 'freehand-toggle-btn' +
        (isMap ? ' fh-float' : '') +
        (isMap || opts.compact ? '' : ' fh-in-panel');
      btn.innerHTML = '<i class="fh-ico ' + iconClass + '"></i><span>' + label + '</span>';
      btn.title = label;
    }
    render();

    btn.addEventListener('click', function (e) {
      e.preventDefault();
      active = !active;
      render();
      if (opts.onToggle) opts.onToggle(active);
    });

    var ctl = null;
    if (isMap) {
      var Ctl = L.Control.extend({
        options: { position: opts.position || 'topright' },
        onAdd: function () {
          var wrap = L.DomUtil.create('div', 'freehand-toggle-ctl');
          L.DomEvent.disableClickPropagation(wrap);
          wrap.appendChild(btn);
          return wrap;
        }
      });
      ctl = new Ctl();
      target.addControl(ctl);
    } else {
      // Panel button. Insert as first child; when the panel lives inside a Leaflet control
      // (e.g. the sidebar container), keep clicks from panning/zooming the map underneath.
      if (window.L && L.DomEvent) L.DomEvent.disableClickPropagation(btn);
      target.insertBefore(btn, target.firstChild);
    }

    return {
      setActive: function (v) { active = v; render(); },
      isActive: function () { return active; },
      remove: function () {
        if (ctl) target.removeControl(ctl);
        else if (btn.parentNode) btn.parentNode.removeChild(btn);
      }
    };
  }

  // Inject the toggle button styles once per page.
  function ensureToggleStyles() {
    if (document.getElementById('freehand-toggle-styles')) return;
    var style = document.createElement('style');
    style.id = 'freehand-toggle-styles';
    // Mimics Bootstrap's .btn.btn-outline-secondary so it matches the app's buttons on both
    // the Bootstrap pages (compose/edit) and the non-Bootstrap ones (routing/freehand).
    style.textContent =
      '.freehand-toggle-ctl{border:none!important;background:none!important;box-shadow:none!important;}' +
      '.freehand-toggle-btn{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;cursor:pointer;' +
      'padding:5px 12px;border-radius:.375rem;background:#fff;color:#5c636a;border:1px solid #ced4da;' +
      'text-decoration:none;font:500 14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;' +
      'transition:background .12s,border-color .12s,color .12s;}' +
      '.freehand-toggle-btn:hover{background:#f5f6f8;border-color:#adb5bd;color:#343a40;text-decoration:none;}' +
      '.freehand-toggle-btn:active{background:#e9ecef;}' +
      '.freehand-toggle-btn .fh-ico{font-size:.9em;}' +
      '.freehand-toggle-btn.fh-in-panel{margin:10px;}' +
      '.freehand-toggle-btn.fh-float{box-shadow:0 1px 4px rgba(0,0,0,.25);}';
    document.head.appendChild(style);
  }

  // Inject the button styles up front so hosts that build the toggle markup themselves
  // (e.g. routing.html, which re-injects it into the sidebar on every setContent) get them.
  if (document.head) ensureToggleStyles();

  window.createFreehandEditor = createFreehandEditor;
  window.addFreehandToggle = addFreehandToggle;
})();
