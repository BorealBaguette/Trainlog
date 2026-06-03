"""
Country timeline.

Builds a per-user timeline of which country they were in over time (derived
from trip origin/destination station country codes), infers continuous
residence periods, and computes days-abroad statistics per year.

Endpoints:
    GET /timeline                  → redirect to the logged-in user's timeline
    GET /u/<username>/timeline      → private timeline (login required)
    GET /public/<username>/timeline → public timeline
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

import distinctipy
from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from py.utils import rgb_to_hex
from src.utils import (
    get_user_id,
    getUser,
    has_current_trip,
    lang,
    login_required,
    mainConn,
    managed_cursor,
    public_required,
)

timeline_blueprint = Blueprint("timeline", __name__)


def getTimelineData(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            WITH UTC_Filtered AS (
                SELECT *,
                    CASE
                        WHEN utc_start_datetime IS NOT NULL AND utc_start_datetime NOT IN (-1, 1)
                        THEN utc_start_datetime
                        ELSE start_datetime
                    END AS utc_filtered_start_datetime,
                    CASE
                        WHEN utc_end_datetime IS NOT NULL AND utc_end_datetime NOT IN (-1, 1)
                        THEN utc_end_datetime
                        ELSE end_datetime
                    END AS utc_filtered_end_datetime
                FROM trip
            )
            SELECT origin_station, destination_station, utc_filtered_start_datetime, utc_filtered_end_datetime
            FROM UTC_Filtered
            WHERE username = :username
              AND utc_filtered_start_datetime NOT IN (-1, 1)
              AND utc_filtered_end_datetime NOT IN (-1, 1)
              AND origin_station IS NOT NULL
              AND destination_station IS NOT NULL
            ORDER BY utc_filtered_start_datetime
        """,
            {"username": username},
        )

        trips = cursor.fetchall()

    if not trips:
        return render_template("timeline.html", username=username, country_blocks=[])

    flag_set = set()
    trip_data = []

    MAX_YEAR = 2100
    for row in trips:
        start_datetime = datetime.fromisoformat(row["utc_filtered_start_datetime"])
        end_datetime = datetime.fromisoformat(row["utc_filtered_end_datetime"])
        if start_datetime.year > MAX_YEAR or end_datetime.year > MAX_YEAR:
            continue
        origin_flag = row["origin_station"][:2]
        dest_flag = row["destination_station"][:2]
        flag_set.update([origin_flag, dest_flag])
        trip_data.append(
            {
                "start": start_datetime,
                "end": end_datetime,
                "origin_flag": origin_flag,
                "dest_flag": dest_flag,
            }
        )

    # Sort flags so each run is consistent
    sorted_flags = sorted(flag_set)

    # Load national colours
    national_colours_path = os.path.join(
        "static", "data", "national-colours.json"
    )
    with open(national_colours_path) as fp:
        national_colours = json.load(fp)

    def emoji_to_cc(emoji):
        """Reverse of get_flag_emoji: flag emoji → 2-letter country code."""
        try:
            return "".join(chr(ord(c) - 127397) for c in emoji)
        except Exception:
            return ""

    # Flags without a national colour entry get generated colours
    unknown_flags = [f for f in sorted_flags if emoji_to_cc(f) not in national_colours]
    generated_colors = distinctipy.get_colors(max(len(unknown_flags), 1), pastel_factor=0.7)
    generated_map = dict(zip(unknown_flags, [rgb_to_hex(c) for c in generated_colors]))

    flag_colors = {
        f: national_colours[emoji_to_cc(f)][0] if emoji_to_cc(f) in national_colours else generated_map[f]
        for f in sorted_flags
    }

    def flag_border_color(cc, primary):
        """Use 2nd national colour as border when primary is white, else primary."""
        if primary.upper() == "#FFFFFF":
            colors = national_colours.get(cc, [])
            return colors[1] if len(colors) > 1 else "#555555"
        return primary

    flag_border_colors = {
        f: flag_border_color(emoji_to_cc(f), flag_colors[f])
        for f in sorted_flags
    }

    # Build blocks using BOTH departure and arrival observations.
    # Departures: (trip.start, origin_flag) — where the person was at that moment.
    # Arrivals:   (trip.end,   dest_flag)   — where the person arrived.
    # For ties (same timestamp), arrivals take precedence (sort key 1 > 0).
    raw_events = []
    for trip in trip_data:
        raw_events.append((trip["start"], 0, trip["origin_flag"]))  # 0 = departure
        raw_events.append((trip["end"],   1, trip["dest_flag"]))    # 1 = arrival
    raw_events.sort(key=lambda x: (x[0], x[1]))

    # Deduplicate: keep last flag at each timestamp (arrivals win over departures)
    seen: dict = {}
    for ts, _, flag in raw_events:
        seen[ts] = flag
    observations = sorted(seen.items())   # [(datetime, flag), ...]

    if not observations:
        return [], {}, {}, {}

    raw_blocks = []
    block_start, current_flag = observations[0]

    for ts, flag in observations[1:]:
        if flag != current_flag:
            raw_blocks.append({"flag": current_flag, "start": block_start, "end": ts})
            block_start = ts
            current_flag = flag
        # same country → extend silently

    timeline_end = max(datetime.now(), observations[-1][0])
    raw_blocks.append({"flag": current_flag, "start": block_start, "end": timeline_end})

    # Merge consecutive blocks with the same country (can still happen after dedup)
    merged = []
    for b in raw_blocks:
        if merged and merged[-1]["flag"] == b["flag"]:
            merged[-1]["end"] = b["end"]
        else:
            merged.append(dict(b))

    blocks = [
        {
            "flag": b["flag"],
            "start": b["start"].isoformat(),
            "end": b["end"].isoformat(),
            "color": flag_colors[b["flag"]],
            "borderColor": flag_border_colors[b["flag"]],
        }
        for b in merged
    ]

    def daterange(start: datetime, end: datetime):
        current = start
        while current < end:
            yield current
            current += timedelta(days=1)

    # Step 1: track time per country per year
    country_time_by_year = defaultdict(
        lambda: defaultdict(float)
    )  # {year: {flag: seconds}}

    for block in blocks:
        start = datetime.fromisoformat(block["start"])
        end = datetime.fromisoformat(block["end"])
        flag = block["flag"]

        for single_day in daterange(start, end):
            year = single_day.year
            day_start = datetime.combine(single_day.date(), datetime.min.time())
            day_end = day_start + timedelta(days=1)

            overlap_start = max(start, day_start)
            overlap_end = min(end, day_end)

            seconds = (overlap_end - overlap_start).total_seconds()
            country_time_by_year[year][flag] += seconds

    # Step 2: build a continuous residence timeline.
    #
    # Approach: scan the full block list globally (not year-by-year).
    # Starting from the first country, a residence only ends when a *different*
    # country accumulates a qualifying contiguous stay (≥ MIN_STAY_DAYS with
    # gaps ≤ MAX_GAP_DAYS bridged, and ≥ 50% density within that span).
    # Until that threshold is crossed the original residence continues,
    # even across years with no trips at all.
    MIN_STAY_DAYS = 45
    MAX_GAP_DAYS  = timedelta(days=21)

    # Build per-flag interval lists from the already-merged trip blocks
    all_intervals: dict = defaultdict(list)
    for b in blocks:
        s = datetime.fromisoformat(b["start"])
        e = datetime.fromisoformat(b["end"])
        all_intervals[b["flag"]].append((s, e))

    def first_qualifying_streak(flag, after):
        """Return the start of the first qualifying streak for *flag* after *after*,
        or None if none exists."""
        intervals = sorted((s, e) for s, e in all_intervals[flag] if e > after)
        if not intervals:
            return None
        merged = []
        for s, e in intervals:
            if merged and s - merged[-1][1] <= MAX_GAP_DAYS:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append([s, e])
        for span_s, span_e in merged:
            if (span_e - span_s).days < MIN_STAY_DAYS:
                continue
            span_secs = (span_e - span_s).total_seconds()
            present_secs = sum(
                (min(e, span_e) - max(s, span_s)).total_seconds()
                for s, e in intervals if s < span_e and e > span_s
            )
            if present_secs / span_secs >= 0.5:
                return span_s
        return None

    # Walk forward: find the earliest qualifying streak in any non-current country
    timeline_start = datetime.fromisoformat(blocks[0]["start"])
    current_flag   = blocks[0]["flag"]
    res_periods    = []   # [(flag, start, end)]
    res_start      = timeline_start

    while True:
        earliest = None   # (streak_start, flag)
        for flag in all_intervals:
            if flag == current_flag:
                continue
            t = first_qualifying_streak(flag, after=res_start)
            if t is not None and (earliest is None or t < earliest[0]):
                earliest = (t, flag)

        if earliest:
            change_t, new_flag = earliest
            res_periods.append((current_flag, res_start, change_t))
            current_flag = new_flag
            res_start    = change_t
        else:
            break

    res_periods.append((current_flag, res_start, timeline_end))

    # ── Derive per-year data from the continuous residence periods ────
    residence_flags_by_year: dict   = {}
    residence_country_by_year: dict = {}
    for year in country_time_by_year:
        year_start = datetime(year, 1, 1)
        year_end   = datetime(year + 1, 1, 1)
        year_flags = list(dict.fromkeys(
            flag for flag, rs, re in res_periods
            if rs < year_end and re > year_start
        ))
        if not year_flags:
            year_flags = [max(country_time_by_year[year], key=country_time_by_year[year].get)]
        residence_flags_by_year[year]   = year_flags
        residence_country_by_year[year] = year_flags[0]

    # ── Residence blocks for the canvas (already continuous) ─────────
    residence_blocks = [
        {
            "flag":        flag,
            "start":       rs.isoformat(),
            "end":         re.isoformat(),
            "color":       flag_colors[flag],
            "borderColor": flag_border_colors[flag],
        }
        for flag, rs, re in res_periods
    ]

    # Step 3: abroad = total time minus ALL residence countries (co-residences don't count as abroad)
    seconds_abroad_by_year = {}
    for year, time_by_country in country_time_by_year.items():
        res_flags = set(residence_flags_by_year[year])
        total = sum(time_by_country.values())
        residence_time = sum(time_by_country.get(f, 0) for f in res_flags)
        seconds_abroad_by_year[year] = total - residence_time

    # Step 4: convert to days
    days_abroad_by_year = {
        year: round(seconds / 86400, 1)
        for year, seconds in seconds_abroad_by_year.items()
    }
    return blocks, residence_blocks, days_abroad_by_year, residence_country_by_year, residence_flags_by_year


@timeline_blueprint.route("/timeline")
def my_timeline():
    if not session.get("logged_in") or not session.get("logged_in_user_id"):
        return redirect(url_for("login", next=request.path))
    return redirect(url_for("timeline.timeline", username=getUser()))


@timeline_blueprint.route("/u/<username>/timeline")
@login_required
def timeline(username):
    blocks, residence_blocks, days_abroad_by_year, residence_country_by_year, residence_flags_by_year = getTimelineData(username)
    # Pass to the template
    return render_template(
        "timeline.html",
        username=username,
        country_blocks=blocks,
        days_abroad_by_year=days_abroad_by_year,
        residence_country_by_year=residence_country_by_year,
        residence_flags_by_year=residence_flags_by_year,
        residence_blocks=residence_blocks,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@timeline_blueprint.route("/public/<username>/timeline")
@public_required
def p_timeline(username):
    blocks, residence_blocks, days_abroad_by_year, residence_country_by_year, residence_flags_by_year = getTimelineData(username)
    # Pass to the template
    return render_template(
        "timeline.html",
        username=username,
        country_blocks=blocks,
        days_abroad_by_year=days_abroad_by_year,
        residence_country_by_year=residence_country_by_year,
        residence_flags_by_year=residence_flags_by_year,
        residence_blocks=residence_blocks,
        nav="bootstrap/public_nav.html",
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )
