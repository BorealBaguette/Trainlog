// A trip's stops as a folded timeline, shared by the trips list (renderStops) and the
// public trip page's itinerary. Its texts come from the page: STOPS_TEXT
// (includes/trip_stops_text.html); its look from styles/trip_stops.css.

function renderDelayBadge(delaySecs) {
    if (delaySecs === null || delaySecs === undefined || delaySecs === '') return '';
    const minutes = Math.round(delaySecs / 60);
    const sign = minutes > 0 ? '+' : '';
    const cls = minutes > 0 ? 'delay-late' : minutes < 0 ? 'delay-early' : 'delay-ontime';
    return ` <span class="delay-badge ${cls}">${sign}${minutes}'</span>`;
}

// A flight's runway stops (src/runway_stops.py), parsed: { takeoff, landing }, each the
// stop or undefined.
function runwayStops(waypoints) {
    var list;
    try { list = typeof waypoints === 'string' ? JSON.parse(waypoints) : waypoints; } catch (e) { return {}; }
    var out = {};
    (list || []).forEach(function (wp) {
        var st = wp && wp.stop;
        if (st && (st.kind === 'takeoff' || st.kind === 'landing')) out[st.kind] = st;
    });
    return out;
}
// Whether a flight has milestones of the user's (boarding, pushback…: src/runway_stops.py)
function hasMilestones(waypoints) {
    var list;
    try { list = typeof waypoints === 'string' ? JSON.parse(waypoints) : waypoints; } catch (e) { return false; }
    return (list || []).some(function (wp) { return wp && wp.stop && wp.stop.kind === 'milestone'; });
}
// Seconds in the air, take-off to landing, or null.
function airborneSeconds(row) {
    var r = runwayStops(row.waypoints);
    var secs = r.takeoff && r.landing ? (Date.parse(r.landing.arr_rt) - Date.parse(r.takeoff.dep_rt)) / 1000 : NaN;
    return secs > 0 ? secs : null;
}
// Seconds since take-off of a flight still in the air, or null. Taken off, not landed,
// and for no longer than its scheduled duration plus an hour: a flight whose tracking was
// lost keeps its take-off without ever landing.
function flyingSeconds(row) {
    var r = runwayStops(row.waypoints);
    if (!r.takeoff || r.landing) return null;
    var since = (Date.now() - Date.parse(r.takeoff.dep_rt)) / 1000;
    var scheduled = row.trip_duration ? row.trip_duration[1] : row.manual_trip_duration || row.estimated_trip_duration || 0;
    return since > 0 && since < scheduled + 3600 ? since : null;
}
function escapeStopText(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}
function stopClock(iso, tz) {
    if (!iso) return '';
    try {
        return new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: tz || undefined })
            .format(new Date(iso));
    } catch (e) { return ''; }
}
function bareTrack(t) {
    return String(t || '').replace(/^(gl\.?|gleis|voie|quai|track|platform|pl\.?|spor|spår|bstg\.?|bin\.?)\s*/i, '').trim();
}
// "Pl. 9", in red only when it really moved from the scheduled one
function stopPlatformHtml(platform, live, type) {
    var shown = bareTrack(live || platform);
    if (!shown) return '';
    var pattern = STOPS_TEXT[type === 'helicopter' ? 'air' : type] || STOPS_TEXT.track;
    var text = escapeStopText(pattern.replace('{track}', shown));
    var moved = live && platform && bareTrack(live) !== bareTrack(platform);
    return moved ? '<span class="delay-late">' + text + '</span>' : text;
}
// The leading flag of a station label ("🇩🇪 Berlin Hbf" → "🇩🇪")
function labelFlag(label) {
    var m = /^(\uD83C[\uDDE6-\uDDFF]){2}/.exec(label || '');
    return m ? m[0] : '';
}
// The stops of a trip picked from a timetable (its waypoints' `stop` records: name,
// country, scheduled and live UTC times, time zone, platforms; see routing.js), as a
// folded line in the detail row: "12 stops", opening onto a timeline from the origin to
// the destination. Read like the row itself: the scheduled time (both, when it waits),
// a delay badge, the platform; a flag only where the country changes. Nothing for trips
// without stops, so the line isn't there at all.
// `compact`: without the two ends, for a page that shows them beside the timeline already
// (the trip page's itinerary).
function tripStopsHtml(data, row, compact) {
    if (!data) return '';
    var waypoints;
    try { waypoints = typeof data === 'string' ? JSON.parse(data) : data; } catch (e) { return ''; }
    // `_wp`: the stop's place among the waypoints, as the trip page's map knows it
    var stops = (waypoints || []).map(function (wp, i) { return wp && wp.stop ? Object.assign({ _wp: i }, wp.stop) : null; })
        .filter(Boolean);
    if (!stops.length) return '';

    function line(kind, name, time, platform, st) {
        // Compact: the routing page's timeline, a name and, small at its end, when and where.
        // A stop carries its place and its times (ms, as it ran) for the page to follow it.
        if (compact) {
            var at = function (f) { var ms = Date.parse(st && (st[f + '_rt'] || st[f])); return isNaN(ms) ? '' : ms; };
            return '<li class="ts-' + kind + '"' + (st ? ' data-wp="' + st._wp + '" data-arr="' + at('arr') +
                '" data-dep="' + at('dep') + '"' : '') + '><span class="ts-name">' + name + '</span><small class="ts-time">' +
                [time, platform].filter(Boolean).join(' · ') + '</small></li>';
        }
        return '<tr class="ts-' + kind + '"><td class="ts-mark"><span></span></td>' +
            '<td class="ts-name">' + name + '</td><td class="ts-time">' + time + '</td>' +
            '<td class="ts-platform">' + platform + '</td></tr>';
    }
    function badge(secs) { return secs ? renderDelayBadge(secs) : ''; }
    // A flight's timeline is in real times (its runway stops): its ends read the same,
    // the scheduled time struck beside the real one, rather than a delay to add up.
    var realTimes = stops.some(function (st) { return st.kind === 'takeoff' || st.kind === 'landing'; });
    // Taken off, not landed yet: the flight is in the air, its arrival still only planned.
    var since = flyingSeconds(Object.assign({}, row, { waypoints: waypoints }));
    var flying = since !== null;
    function endTime(time, secs) {
        if (!realTimes || !secs || !time) return escapeStopText(time || '') + badge(secs);
        return '<s class="text-muted">' + escapeStopText(time) + '</s> <span class="' +
            (secs > 0 ? 'delay-late' : 'delay-early') + '">' + escapeStopText(addSecsToDisplayTime(time, secs)) + '</span>';
    }

    // The trip's ends as local wall-clock ms (the delays in), to tell a milestone before the
    // departure or after the arrival: the list's rows have their local times only.
    function wall(date, time, secs) {
        var m = /^(\d{4})-(\d\d)-(\d\d)$/.exec(date || ''), t = /^(\d\d):(\d\d)/.exec(time || '');
        return m && t ? Date.UTC(+m[1], m[2] - 1, +m[3], +t[1], +t[2]) + (Number(secs) || 0) * 1000 : NaN;
    }
    var startWall = wall(row.start_date, row.start_time, row.departure_delay);
    var endWall = wall(row.start_date, row.end_time, row.arrival_delay);
    if (endWall < startWall - 6 * 3600e3) endWall += 864e5;   // landed the next day
    function stopWall(iso, tz) {
        try {
            var p = {};
            new Intl.DateTimeFormat('en-CA', { timeZone: tz || undefined, year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(iso))
                .forEach(function (x) { p[x.type] = x.value; });
            return Date.UTC(+p.year, p.month - 1, +p.day, +p.hour, +p.minute);
        } catch (e) { return NaN; }
    }
    var before = [], after = [];
    // A flight's rows (runway times, transponder, milestones) in the order they happened:
    // a milestone between the transponder going on and the take-off goes between them.
    var timeline = [];
    // `side`: the airport whose time zone its time is in ('dep' or 'arr'), to be told apart
    // from that end: before the departure above it (boarding, the transponder going on at
    // the gate), after the arrival below it.
    function onTimeline(at, html, tz, side) {
        timeline.push({ at: Date.parse(at), wall: stopWall(at, tz), side: side, html: html, n: timeline.length });
    }

    var lastFlag = labelFlag(row.origin_station);
    var rows = compact ? [] : [line('end', escapeStopText(row.origin_station),
        endTime(row.start_time, row.departure_delay), stopPlatformHtml(row.departure_platform_planned || row.departure_platform, row.departure_platform_planned ? row.departure_platform : null, row.type))];
    stops.forEach(function (st) {
        var flag = st.cc && st.cc !== 'UN' && typeof getFlagEmoji === 'function' ? getFlagEmoji(st.cc) : '';
        var showFlag = flag && flag !== lastFlag;
        if (flag) lastFlag = flag;
        var arr = stopClock(st.arr, st.tz), dep = stopClock(st.dep, st.tz);
        var time = arr && dep && arr !== dep ? arr + '–' + dep : (dep || arr);
        var name = escapeStopText((showFlag ? flag + ' ' : '') + (st.name || ''));
        // A runway stop has no timetable: the time it happened, with when the transponder
        // was first (last) heard on its own row, and the time between them. No flag: it is
        // at the airport whose flag the end beside it carries.
        if (st.kind === 'takeoff' || st.kind === 'landing') {
            runwayLines(st).forEach(function (r) { onTimeline(r.at, r.html, st.tz, st.kind === 'takeoff' ? 'dep' : 'arr'); });
            return;
        }
        // A milestone the user typed in: when it happened (from–to, for a span), and how long
        if (st.kind === 'milestone') {
            var from = stopClock(st.arr_rt, st.tz), to = stopClock(st.dep_rt, st.tz);
            var span = st.arr_rt && st.dep_rt ? (Date.parse(st.dep_rt) - Date.parse(st.arr_rt)) / 1000 : 0;
            var html = line('stop ts-milestone', '<i class="fa-regular ts-runway-icon fa-clock"></i>' + escapeStopText(st.name || '') +
                (span > 0 ? '<span class="ts-seen"><span class="ts-sep">· </span>' + escapeStopText(secondsToDhm(span, STOPS_TEXT.lang)) + '</span>' : ''),
                escapeStopText(from + (to && to !== from ? '–' + to : '')), '', st);
            onTimeline(st.arr_rt, html, st.tz, st.place === 'arrival' ? 'arr' : 'dep');
            return;
        }
        // The delay where it leaves (or arrives, at a stop it doesn't leave)
        var rt = st.dep_rt || st.arr_rt, sched = st.dep_rt ? st.dep : st.arr;
        var delay = rt && sched ? (Date.parse(rt) - Date.parse(sched)) / 1000 : 0;
        rows.push(line('stop', name,
            escapeStopText(time) + badge(delay), stopPlatformHtml(st.platform, st.platform_rt, row.type), st));
    });
    if (flying) {
        // Where it is now: after the take-off, before what is still to come
        onTimeline(new Date().toISOString(), line('stop ts-airborne', '<i class="fa-solid ts-runway-icon fa-plane"></i>' +
            escapeStopText(STOPS_TEXT.airborne + ' ' + secondsToDhm(since, STOPS_TEXT.lang)), '', ''), null, null);
    }
    timeline.sort(function (a, b) {
        return isNaN(a.at) || isNaN(b.at) || a.at === b.at ? a.n - b.n : a.at - b.at;
    }).forEach(function (x) {
        if (!compact && x.side === 'dep' && x.wall < startWall) before.push(x.html);
        else if (!compact && x.side === 'arr' && x.wall > endWall) after.push(x.html);
        else rows.push(x.html);
    });
    if (!compact) rows.push(line(flying ? 'end ts-pending' : 'end', escapeStopText(row.destination_station),
        endTime(row.end_time, row.arrival_delay), stopPlatformHtml(row.arrival_platform_planned || row.arrival_platform, row.arrival_platform_planned ? row.arrival_platform : null, row.type)));
    rows = before.concat(rows, after);

    function runwayLines(st) {
        var takeoff = st.kind === 'takeoff', runway = takeoff ? st.dep_rt : st.arr_rt;
        var seen = takeoff ? st.arr_rt : st.dep_rt;
        function mins(from, to) { return secondsToDhm((Date.parse(to) - Date.parse(from)) / 1000, STOPS_TEXT.lang); }
        // `planned`: its scheduled time, when one was typed in the stops dialog
        function stop(icon, label, when, note, planned, estimated) {
            // With its stop, at the time of this row, so the trip page follows it too
            var at = Object.assign({}, st, { arr_rt: when, dep_rt: when });
            return line('stop', icon + escapeStopText(label) +
                (note ? '<span class="ts-seen"><span class="ts-sep">· </span>' + escapeStopText(note) + '</span>' : ''),
                (estimated ? '~' : '') + escapeStopText(stopClock(when, st.tz)) +
                    (planned && when ? badge((Date.parse(when) - Date.parse(planned)) / 1000) : ''), '', at);
        }
        var other = runwayStops(data)[takeoff ? 'landing' : 'takeoff'];
        var main = stop('<i class="fa-solid ts-runway-icon ' + (takeoff ? 'fa-plane-departure' : 'fa-plane-arrival') + '"></i>',
            STOPS_TEXT[st.kind], runway,
            takeoff ? (seen ? STOPS_TEXT.taxi + ' ' + mins(seen, runway) : '')
                    : (other && other.dep_rt ? STOPS_TEXT.airborne + ' ' + mins(other.dep_rt, runway) : ''),
            takeoff ? st.dep : st.arr, st.estimated);
        main = { at: runway, html: main };
        if (!seen) return [main];
        var transponder = stop(takeoff
                ? '<i class="fa-solid ts-runway-icon fa-tower-broadcast"></i>'
                : '<span class="ts-runway-icon ts-icon-off"><i class="fa-solid fa-tower-broadcast"></i><i class="fa-solid fa-slash"></i></span>',
            takeoff ? STOPS_TEXT.transponderOn : STOPS_TEXT.transponderOff, seen,
            takeoff ? '' : STOPS_TEXT.taxi + ' ' + mins(runway, seen),
            takeoff ? st.arr : st.dep);
        transponder = { at: seen, html: transponder };
        return takeoff ? [transponder, main] : [main, transponder];
    }

    var onlyRunway = stops.every(function (st) { return st.kind === 'takeoff' || st.kind === 'landing'; });
    var flightOnly = stops.every(function (st) { return st.kind === 'takeoff' || st.kind === 'landing' || st.kind === 'milestone'; });
    var count = onlyRunway ? STOPS_TEXT.runway
        : flightOnly ? STOPS_TEXT.timeline
        : typeof pluralize === 'function' ? pluralize(STOPS_TEXT.count, stops.length) : String(stops.length);
    return '<details class="trip-stops"><summary><span class="ts-toggle">' + escapeStopText(count) + '</span></summary>' +
        (compact ? '<ol class="ts-list">' + rows.join('') + '</ol>'
                 : '<table class="trip-stops-table"><tbody>' + rows.join('') + '</tbody></table>') + '</details>';
}

// An FR24 import's take-off and landing before the trip is saved (new and compose), as
// the trips list will show them: open, between the form's ends. `row` as tripStopsHtml's.
function fr24StopsPreviewHtml(stops, row) {
    return '<div class="ts-preview">' + tripStopsHtml(stops, Object.assign({ type: 'air' }, row))
        .replace('<details class="trip-stops">', '<details class="trip-stops" open>') + '</div>';
}

function addSecsToDisplayTime(timeStr, secs) {
    if (!secs) return timeStr;
    var dayOffset = 0;
    var baseTime = timeStr.replace(/\(\+(\d+)\)/, function(m, d) { dayOffset = parseInt(d); return ''; }).trim();
    var parts = baseTime.split(':');
    if (parts.length < 2) return timeStr;
    var totalMins = parseInt(parts[0]) * 60 + parseInt(parts[1]) + Math.round(secs / 60);
    dayOffset += Math.floor(totalMins / (24 * 60));
    totalMins = ((totalMins % (24 * 60)) + 24 * 60) % (24 * 60);
    var h = Math.floor(totalMins / 60), m = totalMins % 60;
    var result = (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m;
    if (dayOffset > 0) result += '(+' + dayOffset + ')';
    return result;
}
