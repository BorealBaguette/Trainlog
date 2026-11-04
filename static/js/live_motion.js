// Motion on the maps of trips in progress (the live map, a trip page): moving dashes on the
// part still to travel, with a look of their own per type, and pins that move with the clock
// between two data refreshes instead of jumping once a minute.
//
//   LiveMotion.dashArray(type, ts)    -> line-dasharray for that type at rAF time `ts`, for a
//                                        line drawn from the destination back to the vehicle
//   LiveMotion.dashLines(items, map, ts) -> the same dashes as geometry, true to scale zoomed
//                                        in, for several routes at once
//   LiveMotion.routeInView(coords, map) -> whether a route may be on screen
//   LiveMotion.overlay(map)           -> a canvas over the map to draw dashLines' output on
//   LiveMotion.moveInterval(map, ms)  -> how often (ms) a pin moving at `ms` m/s needs moving
//   LiveMotion.speed(type)            -> a typical top speed for the type, m/s
//   LiveMotion.startGlide(pin, from)  -> slide `pin` from `from` instead of jumping
//   LiveMotion.glide(pin, target)     -> where to draw it on the way to `target`
//   LiveMotion.reduced                -> prefers-reduced-motion: no animation at all
(function (root) {
  var reduced = !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);

  // Dash and gap in line widths, butt-capped; `run` is how fast the pattern moves towards
  // the destination, in line widths per second. Long carriages for a train, short ones for a
  // bus, a wake of specks behind a ferry, footsteps for walking. `ms` is a typical top speed;
  // `length`, in metres, the vehicle's own, which the dashes grow to when zoomed in (dashLines).
  var STYLES = {
    train:     { dash: 4,   gap: 3,   run: 10, ms: 80,  length: 150 },
    rail:      { dash: 4,   gap: 3,   run: 10, ms: 80,  length: 150 },
    metro:     { dash: 3.5, gap: 2,   run: 8,  ms: 25,  length: 100 },
    tram:      { dash: 2.5, gap: 2,   run: 7,  ms: 20,  length: 35 },
    funicular: { dash: 1.2, gap: 1.8, run: 4,  ms: 10,  length: 20 },
    aerialway: { dash: 1.2, gap: 1.8, run: 4,  ms: 10,  length: 5 },
    bus:       { dash: 1.5, gap: 2,   run: 6,  ms: 30,  length: 12 },
    car:       { dash: 1.5, gap: 2.5, run: 7,  ms: 35,  length: 4.5 },
    air:       { dash: 3,   gap: 5,   run: 14, ms: 250 },
    ferry:     { dash: 1,   gap: 2,   run: 3,  ms: 15,  length: 150 },
    walk:      { dash: 0.5, gap: 1.5, run: 2,  ms: 2 },
    ski:       { dash: 0.5, gap: 1.5, run: 4,  ms: 15 },
    cycle:     { dash: 0.8, gap: 1.6, run: 4,  ms: 8 },
    scooter:   { dash: 0.8, gap: 1.6, run: 4,  ms: 8 }
  };
  var DEFAULT = { dash: 3, gap: 4, run: 10, ms: 30 };

  function style(type) {
    return STYLES[type === 'helicopter' ? 'air' : type] || DEFAULT;
  }

  // The [dash, gap] cycle started `o` into it, as a dash array; the zero-length entries keep
  // it in phase. The dashes are drawn on the part still to travel *reversed*, from the
  // destination back to the vehicle: a pattern runs from the start of its line, and starting
  // it at the vehicle would make it jump every time the line is cut shorter there. So a
  // growing `o` is what moves them towards the destination.
  function phase(s, o) {
    var r = function (v) { return Math.round(v * 100) / 100; };
    if (o < s.dash) return [r(s.dash - o), s.gap, r(o), 0];
    var p = o - s.dash;
    return [0, r(s.gap - p), s.dash, r(p)];
  }

  // A fixed set of phases per type, a quarter of a line width apart, stepped through by the
  // clock. The layer needs 'line-dasharray-transition': { duration: 0 } (TRANSITION): a dash
  // array can't be blended, so during a transition the old one stays up, and with a new
  // phase every few frames the dashes lag and stutter.
  // Fixed, because MapLibre rasterises every distinct dash array into its line atlas and
  // never frees it: phases taken straight from the clock fill it within seconds, after which
  // the dashes flicker and vanish. For the same reason a dash array can't grow with the zoom
  // (it is sized in line widths, and halves at every whole zoom level): dashLines() can.
  var STEP = 0.25, sequences = {};
  function sequence(type) {
    var key = type === 'helicopter' ? 'air' : type;
    if (sequences[key]) return sequences[key];
    var s = style(key), len = s.dash + s.gap, n = Math.max(4, Math.round(len / STEP));
    var seq = [];
    for (var k = 0; k < n; k++) seq.push(phase(s, k * len / n));
    return (sequences[key] = { seq: seq, stepMs: 1000 * (len / n) / s.run });
  }

  function dashArray(type, ts) {
    var q = sequence(type);
    return q.seq[reduced ? 0 : Math.floor(ts / q.stepMs) % q.seq.length];
  }

  function metresPerPixel(lat, zoom) {
    return 40075016.686 * Math.cos(lat * Math.PI / 180) / (512 * Math.pow(2, zoom));
  }

  function haversine(a, b) { // [lng, lat] pairs, metres
    var R = 6371000, rad = Math.PI / 180;
    var dLat = (b[1] - a[1]) * rad, dLng = (b[0] - a[0]) * rad;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  // Between a and b as the map draws the segment (in Web Mercator, see trip_progress.js)
  function mercY(lat) { return Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)); }
  function mercLat(y) { return (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180 / Math.PI; }
  function between(a, b, f) {
    return [a[0] + (b[0] - a[0]) * f, mercLat(mercY(a[1]) + (mercY(b[1]) - mercY(a[1])) * f)];
  }

  // Cumulative distance along a route, metres; cached as the dashes ask every frame.
  var cumulatives = new WeakMap();
  function cumulative(coords) {
    var cum = cumulatives.get(coords);
    if (cum) return cum;
    cum = [0];
    for (var i = 1; i < coords.length; i++) cum.push(cum[i - 1] + haversine(coords[i - 1], coords[i]));
    cumulatives.set(coords, cum);
    return cum;
  }

  // The share [t0, t1] of segment a-b inside the box (lng, Mercator y), or null.
  function clip(a, b, box) {
    var x0 = a[0], y0 = mercY(a[1]), dx = b[0] - x0, dy = mercY(b[1]) - y0, t0 = 0, t1 = 1;
    var p = [-dx, dx, -dy, dy], q = [x0 - box[0], box[2] - x0, y0 - box[1], box[3] - y0];
    for (var k = 0; k < 4; k++) {
      if (p[k] === 0) { if (q[k] < 0) return null; continue; }
      var r = q[k] / p[k];
      if (p[k] < 0) { if (r > t1) return null; if (r > t0) t0 = r; }
      else { if (r < t0) return null; if (r < t1) t1 = r; }
    }
    return [t0, t1];
  }

  // The dashes on the parts of routes still to travel, as one MultiLineString per item:
  //   { key, coords, from, type, speed, state, width }
  // `coords` [lng, lat]; `from` ({ index, point }) the vehicle, between coords[index] and the
  // next; `speed` its own, m/s; `state` ({}) carries the phase from one frame to the next, as
  // a share of the cycle so that zooming doesn't make it jump; `width` the line's, px.
  //
  // Zoomed out they look and move like the dash arrays; zoomed in they become trains (buses,
  // trams…) of their real length, HEADWAY_S apart and coming along at the vehicle's speed,
  // whichever is larger. Only what is on screen is drawn, so it is cheap to redo every frame.
  //
  // Where routes share a stretch (two trains on one line, two people on the same train), two
  // patterns out of step would make a mess of it: each stretch only shows the dashes of the
  // nearest vehicle along the way to it, ties going to the smaller key. By distance, not by
  // time: two timetables' average speeds would hand the stretch back and forth. Worked out on
  // a grid of CELL_PX cells, sampled every SAMPLE_PX along each route. Two copies of one line
  // are never quite on top of each other, so a route misses some of the other's cells: every
  // sample lost is widened by LOST_PX along the route, or what slips through shows as specks.
  // A sample a few cells off is compared as carried along its own route to the point tested:
  // taken as it is, both vehicles lose wherever they are a few cells apart, and the gaps move
  // with the zoom.
  var HEADWAY_S = 10, CELL_PX = 6, SAMPLE_PX = 2, REACH_CELLS = 2, LOST_PX = 12;
  function dashLines(items, map, ts) {
    var zoom = map.getZoom(), world = 512 * Math.pow(2, zoom);
    var bounds = map.getBounds(), padX = (bounds.getEast() - bounds.getWest()) * 0.2;
    var yS = mercY(Math.max(bounds.getSouth(), -85)), yN = mercY(Math.min(bounds.getNorth(), 85));
    var padY = (yN - yS) * 0.2;
    var box = [bounds.getWest() - padX, yS - padY, bounds.getEast() + padX, yN + padY];

    function pixel(pt) {
      return [(pt[0] + 180) / 360 * world, (0.5 - mercY(pt[1]) / (2 * Math.PI)) * world];
    }
    // cell -> { key: { dist, x, y, dx, dy } }: each route's nearest sample there, with its direction
    var cells = new Map();
    var cellKey = function (cx, cy) { return cx * 1e8 + cy; };
    var mpp = metresPerPixel(bounds.getCenter().lat, zoom);
    // Whether `a` (distance, key) is nearer than `b`: within a cell of each other is a tie
    var tie = CELL_PX * mpp;
    var before = function (ad, ak, bd, bk) { return ad < bd - tie || (Math.abs(ad - bd) <= tie && ak < bk); };

    // Each visible piece of the route ahead: fn(a, b, d0, seg, t0, t1) with d0 how far the
    // vehicle is from a, seg the length a-b, [t0, t1] the share of it in view.
    // Each sample of the route ahead: fn(px, dir, d) with px its world pixel, dir the route's
    // unit direction there, d how far the vehicle is from it
    function eachSample(it, fn) {
      eachVisible(it, function (a, b, d0, seg, t0, t1) {
        var pa = pixel(a), pb = pixel(b), vx = pb[0] - pa[0], vy = pb[1] - pa[1];
        var n = Math.hypot(vx, vy) || 1, dir = [vx / n, vy / n];
        for (var pos = t0 * seg; pos <= t1 * seg; pos += it._p.step) {
          var f = pos / seg;
          fn([pa[0] + vx * f, pa[1] + vy * f], dir, d0 + pos);
        }
      });
    }
    function eachVisible(it, fn) {
      var coords = it.coords, from = it.from, cum = cumulative(coords);
      var origin = cum[from.index] + haversine(coords[from.index], from.point);
      for (var i = from.index; i < coords.length - 1; i++) {
        var a = i === from.index ? from.point : coords[i], b = coords[i + 1];
        var start = i === from.index ? origin : cum[i], seg = cum[i + 1] - start;
        var t = seg > 0 && clip(a, b, box);
        if (t) fn(a, b, start - origin, seg, t[0], t[1]);
      }
    }

    items.forEach(function (it) {
      var s = style(it.type), state = it.state, width = it.width || 3;
      var speed = it.speed > 0 ? it.speed : s.ms;
      var mpp = metresPerPixel(it.from.point[1], zoom), unit = width * mpp;
      var dash = Math.max(s.dash * unit, s.length || 0);
      var gap = Math.max(s.gap * unit, s.length ? speed * HEADWAY_S : 0);
      var run = Math.max(s.run * unit, s.length ? speed : 0);
      it._p = { dash: dash, len: dash + gap, speed: speed, step: SAMPLE_PX * mpp };
      if (state.at != null && !reduced) {
        state.frac = ((state.frac || 0) + Math.min(ts - state.at, 100) / 1000 * run / it._p.len) % 1;
      }
      state.at = ts;
      it._p.shift = (state.frac || 0) * it._p.len;
    });

    // Who gets where first
    if (items.length > 1) items.forEach(function (it) {
      var key = String(it.key);
      eachSample(it, function (px, dir, dist) {
        var k = cellKey(Math.floor(px[0] / CELL_PX), Math.floor(px[1] / CELL_PX));
        var cell = cells.get(k);
        if (!cell) cells.set(k, cell = {});
        if (!cell[key] || dist < cell[key].dist) cell[key] = { dist: dist, x: px[0], y: px[1], dx: dir[0], dy: dir[1] };
      });
    });
    function mine(px, dist, key) {
      var cx = Math.floor(px[0] / CELL_PX), cy = Math.floor(px[1] / CELL_PX);
      for (var dx = -REACH_CELLS; dx <= REACH_CELLS; dx++) for (var dy = -REACH_CELLS; dy <= REACH_CELLS; dy++) {
        var cell = cells.get(cellKey(cx + dx, cy + dy));
        if (!cell) continue;
        for (var other in cell) {
          if (other === key) continue;
          var e = cell[other];
          var at = e.dist + ((px[0] - e.x) * e.dx + (px[1] - e.y) * e.dy) * mpp;
          // behind the other vehicle: not its way
          if (at >= 0 && before(at, other, dist, key)) return false;
        }
      }
      return true;
    }

    // The stretches, as [from, to] distances from the vehicle, that are someone else's
    function lostStretches(it) {
      var p = it._p, key = String(it.key), widen = LOST_PX / SAMPLE_PX * p.step, lost = [];
      if (!cells.size) return lost;
      eachSample(it, function (px, dir, d) {
        if (mine(px, d, key)) return;
        var last = lost[lost.length - 1];
        if (last && d - widen <= last[1]) last[1] = d + widen;
        else lost.push([d - widen, d + widen]);
      });
      return lost;
    }

    return items.map(function (it) {
      var p = it._p, lines = [], lost = lostStretches(it), li = 0;
      // Adds the dash from d1 to d2 (distances from the vehicle) on a-b, less what's lost
      function addDash(a, b, d0, seg, d1, d2) {
        while (li < lost.length && lost[li][1] <= d1) li++;
        for (var k = li; d1 < d2; k++) {
          var cut = k < lost.length ? lost[k] : [Infinity, Infinity];
          var end = Math.min(d2, cut[0]);
          if (end > d1) lines.push([between(a, b, (d1 - d0) / seg), between(a, b, (end - d0) / seg)]);
          if (cut[0] >= d2) break;
          d1 = Math.max(d1, cut[1]);
        }
      }
      eachVisible(it, function (a, b, d0, seg, t0, t1) {
        var pos = t0 * seg, stop = t1 * seg;
        while (pos < stop) {
          // Where along the cycle this point is; the pattern moves on, towards the destination
          var ph = (((d0 + pos - p.shift) % p.len) + p.len) % p.len;
          var inDash = ph < p.dash;
          var next = Math.min(stop, pos + Math.max(inDash ? p.dash - ph : p.len - ph, 1e-6));
          if (inDash) addDash(a, b, d0, seg, d0 + pos, d0 + next);
          pos = next;
        }
      });
      return { type: 'MultiLineString', coordinates: lines };
    });
  }

  // Somewhere to draw what dashLines returns: a canvas of its own laid over the map, redrawn
  // straight after each frame of it. Not a GeoJSON source: one changed every frame is re-tiled
  // every frame, and while zooming out the map shows its last tiles until new ones are ready,
  // old dashes and all. Above the trip lines, below the pins (markers come after it).
  //   var o = overlay(map); o.draw([{ geometry, width }])
  function overlay(map) {
    var gl = map.getCanvas(), canvas = document.createElement('canvas');
    canvas.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none';
    gl.parentNode.insertBefore(canvas, gl.nextSibling);
    var ctx = canvas.getContext('2d'), drawn = false;
    function fit() {
      var r = root.devicePixelRatio || 1, w = gl.clientWidth, h = gl.clientHeight;
      canvas.width = Math.round(w * r);
      canvas.height = Math.round(h * r);
      canvas.style.width = w + 'px';
      canvas.style.height = h + 'px';
    }
    fit();
    map.on('resize', fit);
    return {
      draw: function (list) {
        if (!drawn && !list.length) return;
        var r = canvas.width / (gl.clientWidth || 1);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.setTransform(r, 0, 0, r, 0, 0);
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.9)';
        ctx.lineCap = 'butt';
        ctx.lineJoin = 'round';
        list.forEach(function (item) {
          ctx.lineWidth = item.width || 3;
          ctx.beginPath();
          item.geometry.coordinates.forEach(function (line) {
            line.forEach(function (c, i) {
              var p = map.project(c);
              if (i) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y);
            });
          });
          ctx.stroke();
        });
        drawn = list.length > 0;
      }
    };
  }

  // Whether any of `coords` ([lng, lat]) may be in view: its bounding box against the view's
  function routeInView(coords, map) {
    var bbox = coords.__bbox;
    if (!bbox) {
      bbox = [Infinity, Infinity, -Infinity, -Infinity];
      coords.forEach(function (c) {
        bbox[0] = Math.min(bbox[0], c[0]); bbox[1] = Math.min(bbox[1], c[1]);
        bbox[2] = Math.max(bbox[2], c[0]); bbox[3] = Math.max(bbox[3], c[1]);
      });
      try { Object.defineProperty(coords, '__bbox', { value: bbox }); } catch (e) { /* not cached */ }
    }
    var b = map.getBounds();
    return !(bbox[2] < b.getWest() || bbox[0] > b.getEast() || bbox[3] < b.getSouth() || bbox[1] > b.getNorth());
  }

  // A pin is worth moving once it has covered about half a pixel: seldom when the whole
  // world is in view, every frame in a city.
  function moveInterval(map, speed) {
    var mpp = metresPerPixel(map.getCenter().lat, map.getZoom());
    return Math.min(5000, mpp / Math.max(speed, 1) * 500);
  }

  // Fresh data can put a vehicle somewhere else than where the clock had it (a live fix, a
  // new delay): it slides there rather than jumps.
  var GLIDE_MS = 1500;
  function startGlide(pin, from) {
    if (reduced || !from) return;
    pin.glideFrom = from;
    pin.glideAt = Date.now();
  }
  function glide(pin, target) {
    if (!pin.glideFrom) return target;
    var k = (Date.now() - pin.glideAt) / GLIDE_MS;
    if (k >= 1) { pin.glideFrom = null; return target; }
    k = k * k * (3 - 2 * k);
    var a = pin.glideFrom;
    return [a[0] + (target[0] - a[0]) * k, a[1] + (target[1] - a[1]) * k];
  }

  root.LiveMotion = {
    reduced: reduced,
    dashArray: dashArray,
    dashLines: dashLines,
    routeInView: routeInView,
    overlay: overlay,
    moveInterval: moveInterval,
    speed: function (type) { return style(type).ms; },
    TRANSITION: { duration: 0, delay: 0 },
    startGlide: startGlide,
    glide: glide,
    GLIDE_MS: GLIDE_MS
  };
})(window);
