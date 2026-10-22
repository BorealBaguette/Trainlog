"""
Country timeline.

Builds a per-user timeline of which country they were in over time (derived
from trip origin/destination station country codes), infers continuous
residence periods, and computes days-abroad statistics per year.

Endpoints:
    GET /timeline                  → redirect to the logged-in user's timeline
    GET /u/<username>/timeline      → private timeline (login required)
    GET /public/<username>/timeline → public timeline
    GET /u/<username>/timeline/schengen       → Schengen 90/180 counter
    GET /public/<username>/timeline/schengen  → public Schengen counter
"""

import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import distinctipy
from flask import (
    Blueprint,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from py.utils import get_all_countries, rgb_to_hex
from src.pg import pg_session
from src.utils import (
    get_user_id,
    getUser,
    has_current_trip,
    lang,
    login_required,
    public_required,
)

timeline_blueprint = Blueprint("timeline", __name__)


def getTimelineData(username):
    with pg_session() as pg:
        trips = pg.execute(
            """
            WITH base AS (
                -- Actual times: a delayed departure must sort after whatever
                -- got the user to it (e.g. the airport transit)
                SELECT trip_id, origin_station, destination_station,
                    start_datetime + make_interval(secs => COALESCE(departure_delay, 0)) AS local_start,
                    end_datetime + make_interval(secs => COALESCE(arrival_delay, 0)) AS local_end,
                    COALESCE(utc_start_datetime, start_datetime)
                        + make_interval(secs => COALESCE(departure_delay, 0)) AS utc_filtered_start_datetime,
                    -- Never before the actual departure (departure delay entered alone)
                    GREATEST(
                        COALESCE(utc_end_datetime, end_datetime)
                            + make_interval(secs => COALESCE(arrival_delay, 0)),
                        COALESCE(utc_start_datetime, start_datetime)
                            + make_interval(secs => COALESCE(departure_delay, 0))
                    ) AS utc_filtered_end_datetime
                FROM trips
                WHERE user_id = :user_id
            )
            SELECT trip_id, origin_station, destination_station, local_start, local_end,
                utc_filtered_start_datetime, utc_filtered_end_datetime
            FROM base
            WHERE utc_filtered_start_datetime IS NOT NULL
              AND utc_filtered_end_datetime IS NOT NULL
              AND origin_station IS NOT NULL
              AND destination_station IS NOT NULL
            ORDER BY utc_filtered_start_datetime
        """,
            {"user_id": get_user_id(username)},
        ).fetchall()

    if not trips:
        return [], [], {}, {}, {}, []

    flag_set = set()
    trip_data = []

    MAX_YEAR = 2100
    for row in trips:
        start_datetime = row["utc_filtered_start_datetime"]
        end_datetime = row["utc_filtered_end_datetime"]
        if start_datetime.year > MAX_YEAR or end_datetime.year > MAX_YEAR:
            continue
        origin_flag = row["origin_station"][:2]
        dest_flag = row["destination_station"][:2]
        flag_set.update([origin_flag, dest_flag])
        trip_data.append(
            {
                "id": row["trip_id"],
                "start": start_datetime,
                "end": end_datetime,
                "local_start": row["local_start"],
                "local_end": row["local_end"],
                "origin_flag": origin_flag,
                "dest_flag": dest_flag,
            }
        )

    gaps = find_gaps(
        (t["id"], t["origin_flag"], t["dest_flag"], t["local_start"].date(), t["local_end"].date())
        for t in trip_data
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
        return [], [], {}, {}, {}, []

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
    return blocks, residence_blocks, days_abroad_by_year, residence_country_by_year, residence_flags_by_year, gaps


UNKNOWN_FLAG = "\U0001F1FA\U0001F1F3"  # 🇺🇳, stations no country polygon matched


def find_gaps(trips, relevant=lambda arrived, arrived_on, departed, departed_on: True):
    """Places where a trip departs from another country than the previous
    trip arrived in: likely a missing trip. *trips* are (id, origin flag,
    destination flag, start date, end date) in travel order; newest gap first.
    Plans are expected to be patchy, so gaps before a future trip are left out.
    """
    today = date.today()
    gaps = []
    prev = None
    for trip in trips:
        if prev is not None:
            prev_id, _, arrived, _, arrived_on = prev
            trip_id, departed, _, departed_on, _ = trip
            if (
                departed_on <= today
                and arrived != departed
                and UNKNOWN_FLAG not in (arrived, departed)
                and relevant(arrived, arrived_on, departed, departed_on)
            ):
                gaps.append({
                    "arrived": arrived,
                    "arrived_on": arrived_on,
                    "departed": departed,
                    "departed_on": departed_on,
                    "trip_ids": [prev_id, trip_id],
                })
        prev = trip
    return gaps[::-1]


# ── Schengen 90/180 ──────────────────────────────────────────────────────────
#
# Date each country (by station country code) started applying Schengen, so
# past stays are judged by the membership of the day. Codes follow
# static/data/countries-filtered.geojson, which splits off territories:
# IC (Canaries) / EA (Ceuta, Melilla) / AX (Åland) are in; RE, GF, GP, SJ, FO,
# GL… are not. MC, SM and VA have open borders with FR/IT and count de facto.
# AD, CY and IE are outside.
_ORIGINAL = date(1995, 3, 26)
_NORDIC = date(2001, 3, 25)
_EU2004 = date(2007, 12, 21)
SCHENGEN_SINCE = {
    "BE": _ORIGINAL, "DE": _ORIGINAL, "ES": _ORIGINAL, "FR": _ORIGINAL,
    "LU": _ORIGINAL, "NL": _ORIGINAL, "PT": _ORIGINAL, "MC": _ORIGINAL,
    "IC": _ORIGINAL, "EA": _ORIGINAL,
    "IT": date(1997, 10, 26), "SM": date(1997, 10, 26), "VA": date(1997, 10, 26),
    "AT": date(1997, 12, 1),
    "GR": date(2000, 3, 26),
    "DK": _NORDIC, "FI": _NORDIC, "IS": _NORDIC, "NO": _NORDIC, "SE": _NORDIC,
    "AX": _NORDIC,
    "CZ": _EU2004, "EE": _EU2004, "HU": _EU2004, "LT": _EU2004, "LV": _EU2004,
    "MT": _EU2004, "PL": _EU2004, "SI": _EU2004, "SK": _EU2004,
    "CH": date(2008, 12, 12),
    "LI": date(2011, 12, 19),
    "HR": date(2023, 1, 1),
    "BG": date(2024, 3, 31), "RO": date(2024, 3, 31),
}
SCHENGEN_MAX_DAYS = 90
SCHENGEN_WINDOW = 180


# Citizens of these countries aren't held to the 90/180 rule: (since, until)
# by citizenship. EU/EEA accession or the EU–Swiss agreement give free
# movement; the UK left at the end of the Brexit transition. The microstates
# have open borders with their neighbours and their citizens are exempt from
# the EES, so their days are never counted.
_EU1958 = date(1958, 1, 1)
_EU1973 = date(1973, 1, 1)
_EU1995 = date(1995, 1, 1)
_EU2004 = date(2004, 5, 1)
FREE_MOVEMENT = {
    "BE": (_EU1958, None), "DE": (_EU1958, None), "FR": (_EU1958, None),
    "IT": (_EU1958, None), "LU": (_EU1958, None), "NL": (_EU1958, None),
    "DK": (_EU1973, None), "IE": (_EU1973, None),
    "GB": (_EU1973, date(2021, 1, 1)),
    "GR": (date(1981, 1, 1), None),
    "ES": (date(1986, 1, 1), None), "PT": (date(1986, 1, 1), None),
    "IS": (date(1994, 1, 1), None), "NO": (date(1994, 1, 1), None),
    "AT": (_EU1995, None), "FI": (_EU1995, None), "SE": (_EU1995, None),
    "LI": (date(1995, 5, 1), None),
    "CH": (date(2002, 6, 1), None),
    "CY": (_EU2004, None), "CZ": (_EU2004, None), "EE": (_EU2004, None),
    "HU": (_EU2004, None), "LT": (_EU2004, None), "LV": (_EU2004, None),
    "MT": (_EU2004, None), "PL": (_EU2004, None), "SI": (_EU2004, None),
    "SK": (_EU2004, None),
    "BG": (date(2007, 1, 1), None), "RO": (date(2007, 1, 1), None),
    "HR": (date(2013, 7, 1), None),
    "AD": (date.min, None), "MC": (date.min, None), "SM": (date.min, None),
    "VA": (date.min, None),
}

# Dependent territories, mapped to the state whose citizenship their residents
# hold: folds them into that state when guessing citizenship from residence,
# and keeps them out of the citizenship picker
SOVEREIGN = {
    "RE": "FR", "GF": "FR", "GP": "FR", "MQ": "FR", "YT": "FR", "PM": "FR",
    "MF": "FR", "BL": "FR", "NC": "FR", "PF": "FR", "WF": "FR", "TF": "FR",
    "CP": "FR",
    "IC": "ES", "EA": "ES", "AX": "FI", "SJ": "NO", "BV": "NO",
    "GL": "DK", "FO": "DK",
    "AW": "NL", "CW": "NL", "SX": "NL", "BQ": "NL",
    "GI": "GB", "JE": "GB", "GG": "GB", "IM": "GB", "AI": "GB", "BM": "GB",
    "VG": "GB", "KY": "GB", "FK": "GB", "MS": "GB", "SH": "GB", "TC": "GB",
    "IO": "GB", "GS": "GB", "PN": "GB",
    "AS": "US", "GU": "US", "MP": "US", "PR": "US", "VI": "US", "UM": "US",
    "CX": "AU", "CC": "AU", "NF": "AU", "HM": "AU",
    "CK": "NZ", "NU": "NZ", "TK": "NZ",
}
# Neither a state nor anyone's territory of citizenship
NO_CITIZENSHIP = {"AQ", "EH"}


def _flag_to_cc(flag):
    return "".join(chr(ord(c) - 127397) for c in flag)


def _in_schengen(flag, day):
    since = SCHENGEN_SINCE.get(_flag_to_cc(flag))
    return since is not None and day >= since


def infer_citizenship(residence_blocks):
    """Country code with the most total residence time, or None."""
    totals = defaultdict(float)
    for b in residence_blocks:
        cc = _flag_to_cc(b["flag"])
        span = datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])
        totals[SOVEREIGN.get(cc, cc)] += span.total_seconds()
    return max(totals, key=totals.get) if totals else None


def getSchengenData(username, residence_blocks=(), ignore_residence=False, citizenship=None):
    """Days spent in the Schengen area, by local calendar day.

    Any day touched counts in full (entry and exit days included). Presence
    between trips follows the country timeline: the user stays where a trip
    arrived until the next departure. After the last trip they are assumed to
    stay put, which drives the "must leave by" projection.

    Days while resident in a Schengen country don't count (see below), using
    the residence periods inferred for the country timeline, unless
    *ignore_residence* (the user says that inference is wrong). Neither do
    days on which *citizenship* (a country code) gave free movement.
    """
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT trip_id, origin_station, destination_station,
                start_datetime + make_interval(secs => COALESCE(departure_delay, 0)) AS start_datetime,
                end_datetime + make_interval(secs => COALESCE(arrival_delay, 0)) AS end_datetime
            FROM trips
            WHERE user_id = :user_id
              AND start_datetime IS NOT NULL
              AND end_datetime IS NOT NULL
              AND origin_station IS NOT NULL
              AND destination_station IS NOT NULL
              AND start_datetime < '2100-01-01'
              AND end_datetime < '2100-01-01'
            -- Actual (delayed) times, so a late flight sorts after the transit to it
            ORDER BY COALESCE(utc_start_datetime, start_datetime)
                         + make_interval(secs => COALESCE(departure_delay, 0)),
                     COALESCE(utc_end_datetime, end_datetime)
                         + make_interval(secs => COALESCE(arrival_delay, 0))
        """,
            {"user_id": get_user_id(username)},
        ).fetchall()

    today = date.today()
    if not rows:
        return None

    trips = [
        (
            row["origin_station"][:2],
            row["destination_station"][:2],
            row["start_datetime"].date(),
            row["end_datetime"].date(),
        )
        for row in rows
    ]
    trip_ids = [row["trip_id"] for row in rows]

    last_end = max(t[3] for t in trips)
    known_end = max(today, last_end)
    horizon = known_end + timedelta(days=SCHENGEN_WINDOW)

    days = defaultdict(list)  # {date: [flags in order seen]}

    def mark(flag, d0, d1):
        d = d0
        while d <= d1:
            if _in_schengen(flag, d) and flag not in days[d]:
                days[d].append(flag)
            d += timedelta(days=1)

    for i, (origin, dest, start, end) in enumerate(trips):
        mark(origin, start, start)
        mark(dest, end, end)
        # Multi-day trips within the area (night trains, ferries) count throughout
        if _in_schengen(origin, start) and _in_schengen(dest, end):
            mark(origin, start, end)

        if i + 1 < len(trips):
            next_start = trips[i + 1][2]
            if next_start >= end:
                mark(dest, end, next_start)
        else:
            mark(dest, end, horizon)

    if not days:
        return None

    first_day = min(days)
    n = (horizon - first_day).days + 1

    def rolling_counter(counted):
        """Rolling 180-day count for every day from the first Schengen day to the horizon."""
        prefix = [0] * (n + 1)
        for k in range(n):
            prefix[k + 1] = prefix[k] + (1 if counted.get(first_day + timedelta(days=k)) else 0)

        def count_on(d):
            k = (d - first_day).days
            if k < 0:
                return 0
            k = min(k, n - 1)
            return prefix[k + 1] - prefix[max(0, k + 1 - SCHENGEN_WINDOW)]
        return count_on

    count_on = rolling_counter(days)

    # Residence: inferred residence flips after any 45-day stay, so a visitor's
    # long trip can look like residence. Only a Schengen residence spell of at
    # least 180 days that breaks the 90/180 limit anyway (which no legal short
    # stay can) is taken as real residence; its days are dropped. The ongoing
    # spell extends to the horizon so the projection doesn't count it either.
    resident_days = {}  # {date: residence flag}
    for i, b in enumerate(residence_blocks):
        d = max(datetime.fromisoformat(b["start"]).date(), first_day)
        end = horizon if i == len(residence_blocks) - 1 else datetime.fromisoformat(b["end"]).date()
        while d <= end:
            if _in_schengen(b["flag"], d):
                resident_days[d] = b["flag"]
            d += timedelta(days=1)

    residences = []
    for d in sorted(resident_days):
        if residences and residences[-1]["end"] == d - timedelta(days=1):
            residences[-1]["end"] = d
        else:
            residences.append({"start": d, "end": d, "flags": []})
        if resident_days[d] not in residences[-1]["flags"]:
            residences[-1]["flags"].append(resident_days[d])

    residences = [
        r for r in residences
        if (min(r["end"], today) - r["start"]).days + 1 >= SCHENGEN_WINDOW
        and any(
            count_on(r["start"] + timedelta(days=k)) > SCHENGEN_MAX_DAYS
            for k in range((min(r["end"], known_end) - r["start"]).days + 1)
        )
    ]
    residence_detected = bool(residences)
    if ignore_residence:
        residences = []
    resident_now = any(r["start"] <= today <= r["end"] for r in residences)
    for r in residences:
        d = r["start"]
        while d <= r["end"]:
            days.pop(d, None)
            d += timedelta(days=1)

    since, until = FREE_MOVEMENT.get(citizenship, (None, None))

    def exempt(d):
        return since is not None and d >= since and (until is None or d < until)

    def counts(d):
        """Whether a day in Schengen on *d* would count (not exempt, not resident)."""
        return not exempt(d) and not any(r["start"] <= d <= r["end"] for r in residences)

    counted = {d: flags for d, flags in days.items() if not exempt(d)}
    count_on = rolling_counter(counted)

    used = count_on(today)

    # First projected day over the limit → the day before is the last legal one
    must_leave_by = None
    beyond_trips = False
    d = today
    while d <= horizon:
        if count_on(d) > SCHENGEN_MAX_DAYS:
            must_leave_by = d - timedelta(days=1)
            beyond_trips = d > last_end
            break
        d += timedelta(days=1)

    # Next day on which the count drops
    next_freed = None
    d = today + timedelta(days=1)
    while d <= horizon:
        if count_on(d) < count_on(d - timedelta(days=1)):
            next_freed = d
            break
        d += timedelta(days=1)

    # Outside the area today: how long a stay entered today could last,
    # counting only past days (planned trips ignored)
    max_stay_until = None
    if not days.get(today) and not resident_now and not exempt(today):
        past = count_on(today - timedelta(days=1))
        k = 0
        while k < SCHENGEN_MAX_DAYS:
            day = today + timedelta(days=k)
            drop_day = day - timedelta(days=SCHENGEN_WINDOW)
            if drop_day >= first_day and counted.get(drop_day):
                past -= 1
            if past + k + 1 > SCHENGEN_MAX_DAYS:
                break
            k += 1
        if k:
            max_stay_until = today + timedelta(days=k - 1)

    # Stays: runs of consecutive Schengen days, cut at the last known day and
    # where free movement starts or ends (free-movement runs aren't listed)
    stays = []
    run = None
    d = first_day
    while d <= known_end:
        if run is not None and (not days.get(d) or run["exempt"] != exempt(d)):
            stays.append(run)
            run = None
        if days.get(d):
            if run is None:
                run = {"entry": d, "exit": d, "flags": [], "peak": 0, "exempt": exempt(d)}
            run["exit"] = d
            run["flags"].extend(f for f in days[d] if f not in run["flags"])
            if not run["exempt"]:
                run["peak"] = max(run["peak"], count_on(d))
        d += timedelta(days=1)
    if run is not None:
        run["open"] = bool(days.get(known_end + timedelta(days=1)))
        stays.append(run)

    for r in residences:
        stays.append({
            "entry": r["start"],
            "exit": min(r["end"], known_end),
            "flags": r["flags"],
            "peak": 0,
            "open": r["end"] > known_end,
            "resident": True,
        })
    stays.sort(key=lambda s: s["entry"], reverse=True)

    stays_out = [
        {
            "entry": s["entry"].isoformat(),
            "exit": s["exit"].isoformat(),
            "days": (s["exit"] - s["entry"]).days + 1,
            "flags": s["flags"],
            "peak": s["peak"],
            "planned": s["entry"] > today,
            "open": s.get("open", False),
            "resident": s.get("resident", False),
        }
        for s in stays
        if not s.get("exempt")
    ]

    return {
        "today": today.isoformat(),
        "window_start": (today - timedelta(days=SCHENGEN_WINDOW - 1)).isoformat(),
        "used": used,
        "remaining": max(0, SCHENGEN_MAX_DAYS - used),
        "in_now": bool(days.get(today)),
        "resident_now": resident_now,
        "residence_detected": residence_detected,
        "exempt_now": exempt(today),
        "must_leave_by": must_leave_by.isoformat() if must_leave_by else None,
        "beyond_trips": beyond_trips,
        "last_flag": trips[-1][1],
        "next_freed": next_freed.isoformat() if next_freed else None,
        "max_stay_until": max_stay_until.isoformat() if max_stay_until else None,
        "peak_ever": max((s["peak"] for s in stays), default=0),
        "stays": stays_out,
        "gaps": find_gaps(
            ((trip_id, *trip) for trip_id, trip in zip(trip_ids, trips)),
            # Only across the Schengen border, and where it can change the count
            lambda arrived, arrived_on, departed, departed_on: (
                _in_schengen(arrived, arrived_on) != _in_schengen(departed, departed_on)
                and (counts(arrived_on) or counts(departed_on))
            ),
        ),
    }


def _resides_in_schengen(residence_blocks):
    """Whether the inferred residence right now is a Schengen country."""
    now = datetime.now()
    current = [b for b in residence_blocks if datetime.fromisoformat(b["start"]) <= now]
    return bool(current) and _in_schengen(current[-1]["flag"], now.date())


SCHENGEN_RESIDENCE_COOKIE = "schengenIgnoreResidence"
SCHENGEN_CITIZENSHIP_COOKIE = "schengenCitizenship"


def _render_schengen(username, nav):
    # ?residence=ignore / ?residence=auto flips the viewer's preference, kept in a cookie
    choice = request.args.get("residence")
    if choice in ("ignore", "auto"):
        ignore_residence = choice == "ignore"
    else:
        ignore_residence = request.cookies.get(SCHENGEN_RESIDENCE_COOKIE) == "1"

    # ?citizenship=XX picks one, ?citizenship=auto goes back to the guess
    countries = {
        cc: flag for cc, flag in get_all_countries().items()
        if cc not in SOVEREIGN and cc not in NO_CITIZENSHIP
    }
    citizenship_choice = request.args.get("citizenship")
    if citizenship_choice not in countries and citizenship_choice != "auto":
        citizenship_choice = None
    if citizenship_choice:
        chosen_citizenship = None if citizenship_choice == "auto" else citizenship_choice
    else:
        chosen_citizenship = request.cookies.get(SCHENGEN_CITIZENSHIP_COOKIE)
        if chosen_citizenship not in countries:
            chosen_citizenship = None

    residence_blocks = getTimelineData(username)[1]
    inferred_citizenship = infer_citizenship(residence_blocks)
    citizenship = chosen_citizenship or inferred_citizenship
    response = make_response(render_template(
        "timeline_schengen.html",
        username=username,
        schengen=getSchengenData(username, residence_blocks, ignore_residence, citizenship),
        ignore_residence=ignore_residence,
        countries=sorted(countries.items()),
        chosen_citizenship=chosen_citizenship,
        inferred_citizenship_flag=countries.get(inferred_citizenship, ""),
        max_days=SCHENGEN_MAX_DAYS,
        nav=nav,
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    ))
    if choice == "ignore":
        response.set_cookie(SCHENGEN_RESIDENCE_COOKIE, "1", max_age=10 * 365 * 86400, samesite="Lax")
    elif choice == "auto":
        response.delete_cookie(SCHENGEN_RESIDENCE_COOKIE)
    if citizenship_choice == "auto":
        response.delete_cookie(SCHENGEN_CITIZENSHIP_COOKIE)
    elif citizenship_choice:
        response.set_cookie(SCHENGEN_CITIZENSHIP_COOKIE, citizenship_choice, max_age=10 * 365 * 86400, samesite="Lax")
    return response


@timeline_blueprint.route("/timeline")
def my_timeline():
    if not session.get("logged_in") or not session.get("logged_in_user_id"):
        return redirect(url_for("login", next=request.path))
    return redirect(url_for("timeline.timeline", username=getUser()))


@timeline_blueprint.route("/u/<username>/timeline")
@login_required
def timeline(username):
    blocks, residence_blocks, days_abroad_by_year, residence_country_by_year, residence_flags_by_year, gaps = getTimelineData(username)
    # Pass to the template
    return render_template(
        "timeline.html",
        username=username,
        country_blocks=blocks,
        days_abroad_by_year=days_abroad_by_year,
        residence_country_by_year=residence_country_by_year,
        residence_flags_by_year=residence_flags_by_year,
        residence_blocks=residence_blocks,
        show_schengen_tab=not _resides_in_schengen(residence_blocks),
        gaps=gaps,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@timeline_blueprint.route("/public/<username>/timeline")
@public_required
def p_timeline(username):
    blocks, residence_blocks, days_abroad_by_year, residence_country_by_year, residence_flags_by_year, gaps = getTimelineData(username)
    # Pass to the template
    return render_template(
        "timeline.html",
        username=username,
        country_blocks=blocks,
        days_abroad_by_year=days_abroad_by_year,
        residence_country_by_year=residence_country_by_year,
        residence_flags_by_year=residence_flags_by_year,
        residence_blocks=residence_blocks,
        show_schengen_tab=not _resides_in_schengen(residence_blocks),
        gaps=gaps,
        nav="bootstrap/public_nav.html",
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@timeline_blueprint.route("/u/<username>/timeline/schengen")
@login_required
def timeline_schengen(username):
    return _render_schengen(username, "bootstrap/navigation.html")


@timeline_blueprint.route("/public/<username>/timeline/schengen")
@public_required
def p_timeline_schengen(username):
    return _render_schengen(username, "bootstrap/public_nav.html")
