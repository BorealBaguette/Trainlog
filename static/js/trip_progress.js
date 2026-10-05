// Where a trip in progress is right now, from its timetable.
//
// The old estimate was (now - start) / (end - start), applied to the whole path (or to
// its number of points): a train that waits ten minutes at a junction, or crawls through
// a city, was always drawn out of place. A trip saved with timetable stops (waypoints[].stop,
// from the MOTIS form) knows when it is at each of them, so the position is worked out
// between the two stops around "now" instead: the clock gives how far along that leg we
// are, the route gives how far that is in metres. A trip without stops is the same
// calculation with just its two ends, still by distance rather than by point count.
//
//   TripProgress.position(trip, coords, { latFirst })
//     -> { index, point, progress } | null
//
// `coords` is the path as drawn ([lng, lat] pairs, or [lat, lng] with latFirst); `point` is
// the interpolated position, in that same order; `index` is the last vertex at or before
// it (past = coords.slice(0, index + 1) + point, future = point + coords.slice(index + 1));
// `progress` is the share of the route covered, 0..1. null when the trip has no usable times.
(function (root) {
  // A stop further than this from the route is not on it (the route was edited after the
  // stops were saved): it is ignored rather than allowed to pull the position around.
  var MAX_STOP_OFFSET_M = 2000;

  function toMs(value) {
    if (!value) return NaN;
    // "2026-10-05 13:38:00" (the API's UTC datetimes) or ISO with Z, both UTC.
    var s = String(value).replace(' ', 'T');
    if (!/(Z|[+-]\d\d:?\d\d)$/.test(s)) s += 'Z';
    return new Date(s).getTime();
  }

  function haversine(a, b) { // [lat, lng] pairs, metres
    var R = 6371000, rad = Math.PI / 180;
    var dLat = (b[0] - a[0]) * rad, dLng = (b[1] - a[1]) * rad;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function stopsOf(trip) {
    var list = trip.stops;
    if (!list && trip.waypoints) {
      try { list = JSON.parse(trip.waypoints); } catch (e) { list = null; }
    }
    if (!Array.isArray(list)) return [];
    return list.filter(function (w) {
      var s = w && w.stop;
      return s && (s.arr || s.dep || s.arr_rt || s.dep_rt);
    });
  }

  // Cumulative distance along the route, in metres, one entry per vertex.
  function cumulative(coords, latFirst) {
    var cum = new Array(coords.length), total = 0;
    cum[0] = 0;
    for (var i = 1; i < coords.length; i++) {
      var a = coords[i - 1], b = coords[i];
      total += latFirst ? haversine(a, b) : haversine([a[1], a[0]], [b[1], b[0]]);
      cum[i] = total;
    }
    return cum;
  }

  // The vertex at or after `from` closest to the stop, with how far off it is.
  function nearestVertex(coords, latFirst, from, lat, lng) {
    var best = -1, bestD = Infinity, k = Math.cos(lat * Math.PI / 180);
    for (var i = from; i < coords.length; i++) {
      var vLat = latFirst ? coords[i][0] : coords[i][1];
      var vLng = latFirst ? coords[i][1] : coords[i][0];
      var dy = vLat - lat, dx = (vLng - lng) * k;
      var d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = i; }
    }
    if (best < 0) return null;
    var v = coords[best];
    return { index: best, offset: haversine(latFirst ? v : [v[1], v[0]], [lat, lng]) };
  }

  // A stop's real arrival and departure, ms: the live time where there is one; the scheduled
  // one carries the trip's own delay, growing from the departure's to the arrival's along
  // the way. null when it has no time at all.
  function stopTimes(s, n, count, depDelay, arrDelay) {
    var share = (n + 1) / (count + 1);
    var shift = depDelay + (arrDelay - depDelay) * share;
    var arr = s.arr_rt ? toMs(s.arr_rt) : toMs(s.arr) + shift;
    var dep = s.dep_rt ? toMs(s.dep_rt) : toMs(s.dep) + shift;
    if (isNaN(arr)) arr = dep;
    if (isNaN(dep)) dep = arr;
    return isNaN(arr) ? null : { arr: arr, dep: dep };
  }

  // The trip's named, timed stops: [{ name, lat, lng, arr, dep, tz, ... }], times in ms with
  // the delays included, the same as the position is worked out from.
  function timedStops(trip) {
    var depDelay = Number(trip.departure_delay || 0) * 1000;
    var arrDelay = Number(trip.arrival_delay || 0) * 1000;
    var stops = stopsOf(trip), out = [];
    stops.forEach(function (wp, n) {
      var s = wp.stop;
      var lat = wp.lat != null ? wp.lat : s.lat, lng = wp.lng != null ? wp.lng : s.lng;
      var times = stopTimes(s, n, stops.length, depDelay, arrDelay);
      if (!s.name || !times || lat == null || lng == null) return;
      out.push({
        name: s.name, lat: lat, lng: lng, arr: times.arr, dep: times.dep, tz: s.tz || null,
        // as timetabled, and the platform and country, for showing what changed
        arrSched: toMs(s.arr), depSched: toMs(s.dep),
        platform: s.platform || null, platformRt: s.platform_rt || null, cc: s.cc || null
      });
    });
    return out;
  }

  // Time-ordered [time, distance] pairs: the departure, each stop's arrival and departure
  // (the vehicle stands still between them), the arrival.
  function buildAnchors(trip, coords, latFirst) {
    var t0 = toMs(trip.utc_start_datetime || trip.utc_filtered_start_datetime) +
             Number(trip.departure_delay || 0) * 1000;
    var t1 = toMs(trip.utc_end_datetime || trip.utc_filtered_end_datetime) +
             Number(trip.arrival_delay || 0) * 1000;
    if (isNaN(t0) || isNaN(t1) || t1 <= t0) return null;

    var cum = cumulative(coords, latFirst);
    var total = cum[cum.length - 1];
    var anchors = [[t0, 0]];

    var stops = stopsOf(trip), from = 0;
    var depDelay = Number(trip.departure_delay || 0) * 1000;
    var arrDelay = Number(trip.arrival_delay || 0) * 1000;
    stops.forEach(function (wp, n) {
      var s = wp.stop;
      var lat = wp.lat != null ? wp.lat : s.lat, lng = wp.lng != null ? wp.lng : s.lng;
      if (lat == null || lng == null) return;
      var hit = nearestVertex(coords, latFirst, from, lat, lng);
      if (!hit || hit.offset > MAX_STOP_OFFSET_M) return;
      from = hit.index; // the stops come in route order: later ones can't be behind this one

      var times = stopTimes(s, n, stops.length, depDelay, arrDelay);
      if (!times) return;
      var arr = times.arr, dep = times.dep;

      anchors.push([arr, cum[hit.index]]);
      if (dep > arr) anchors.push([dep, cum[hit.index]]);
    });
    anchors.push([t1, total]);

    // Bad data must not make the vehicle go back in time or along the route.
    for (var i = 1; i < anchors.length; i++) {
      anchors[i][0] = Math.min(Math.max(anchors[i][0], anchors[i - 1][0]), t1);
      anchors[i][1] = Math.max(anchors[i][1], anchors[i - 1][1]);
    }
    return { anchors: anchors, cum: cum, total: total };
  }

  // Cached per trip: the live pages ask every few seconds, and the anchors only change with
  // the route or the times.
  function anchorsFor(trip, coords, latFirst) {
    var key = [coords.length, !!latFirst, trip.utc_start_datetime, trip.utc_end_datetime,
               trip.departure_delay, trip.arrival_delay, trip.waypoints ? trip.waypoints.length : 0].join('|');
    var cached = trip.__progressAnchors;
    if (cached && cached.key === key) return cached.value;
    var value = buildAnchors(trip, coords, latFirst);
    try {
      Object.defineProperty(trip, '__progressAnchors', { value: { key: key, value: value }, writable: true, configurable: true });
    } catch (e) { /* frozen object: just not cached */ }
    return value;
  }

  function position(trip, coords, opts) {
    if (!trip || !coords || !coords.length) return null;
    if (coords.length === 1) return { index: 0, point: coords[0], progress: 0 };
    var latFirst = !!(opts && opts.latFirst);
    var built = anchorsFor(trip, coords, latFirst);
    if (!built || !(built.total > 0)) return null;

    var now = (opts && opts.now != null) ? opts.now : Date.now();
    var anchors = built.anchors, target;
    if (now <= anchors[0][0]) target = 0;
    else if (now >= anchors[anchors.length - 1][0]) target = built.total;
    else {
      for (var i = 1; i < anchors.length; i++) {
        if (now <= anchors[i][0]) {
          var a = anchors[i - 1], b = anchors[i];
          target = b[0] === a[0] ? b[1] : a[1] + (b[1] - a[1]) * (now - a[0]) / (b[0] - a[0]);
          break;
        }
      }
    }

    // Last vertex at or before the target distance, then the point between it and the next.
    var cum = built.cum, lo = 0, hi = cum.length - 1;
    while (lo < hi) {
      var mid = (lo + hi + 1) >> 1;
      if (cum[mid] <= target) lo = mid; else hi = mid - 1;
    }
    var point = coords[lo];
    if (lo < coords.length - 1 && cum[lo + 1] > cum[lo]) {
      var f = (target - cum[lo]) / (cum[lo + 1] - cum[lo]), p = coords[lo], q = coords[lo + 1];
      point = [p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f];
    }
    return { index: lo, point: point, progress: target / built.total };
  }

  root.TripProgress = { position: position, timedStops: timedStops };
})(window);
