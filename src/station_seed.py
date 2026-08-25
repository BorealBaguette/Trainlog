"""Register stations for the labels people already use, a few at a time.

The registry seeds itself as people log trips, but that only ever reaches places logged
*from now on*. Years of existing trips name stations nobody has picked since the registry
existed, so they sit unresolved — 143,682 distinct spellings at the time of writing, and the
top fifteen alone account for ~68,000 trip endpoints.

This walks that queue from the top, searches each spelling the same way the trip form does,
and registers the match when it is confident. Enrichment then picks the new stations up on
its own.

Started from the admin panel's Seed button, which runs seed_run() in a background thread and
watches it through station_seed_runs.

── Pacing ────────────────────────────────────────────────────────────────────────────────
Two very different services are involved and only one of them is yours:

  Photon    self-hosted, two or three queries per label — the name search, and a reverse
            lookup at the label's own location. Yours to saturate, but it also serves the
            live autocomplete, so the delay keeps this from competing with real users.
  Overpass  public, and the reason for care. Not touched here at all: this only creates rows
            with enriched_at NULL, and the background enricher drains them at its own pace.

So "slowly" is really about Photon and about not resolving thousands of labels in one
transaction. The default of 0.5s between labels is ~2 labels/second, which will not be
noticed alongside normal traffic and gets through the meaningful part of the queue in an
evening.

── Resuming ──────────────────────────────────────────────────────────────────────────────
There is no progress file and none is needed: a label is done when it has been checked
(station_labels.auto_checked_at), and a station is enriched when enriched_at is set. Both
live in the database, so stopping a run and starting another tomorrow simply continues, and
running it twice does no harm. Clearing auto_checked_at re-opens a label.

── What it will not do ───────────────────────────────────────────────────────────────────
Guess. A label is registered only when two independent searches — one on its text, one on
where its trips actually end — land on the same OSM object within 50m, with nothing else as
close. Anything less certain is left for a human in the admin panel. Auto-registering a poor
match would attribute trips to the wrong place and nothing would ever flag it — the whole
point of the unresolved queue is that being unresolved is safe and being wrong is not.
"""

import difflib
import json
import logging
import threading
import time
import unicodedata

from py.utils import getDistance
from src.pg import pg_session
from src.photon import photonRequestLangs
from src.station_search import process_station_results
from src.stations import (
    add_aliases,
    country_from_flag,
    label_location,
    registry_stats,
    resync_station,
    station_bucket,
    strip_flag,
    upsert_station,
)

logger = logging.getLogger(__name__)

# The OSM tags to search per mode, mirroring the tables in templates/new.html. Without them
# a search for "Bern" finds the city rather than the station.
OSM_TAGS = {
    "train": ["railway:halt", "railway:station"],
    "tram": ["railway:tram_stop", "railway:station", "railway:halt"],
    "metro": ["railway:station", "railway:subway_entrance"],
    "funicular": ["railway:halt", "railway:station"],
    "rail": ["railway:halt", "railway:station"],
    "bus": ["amenity:bus_station", "highway:bus_stop"],
    "ferry": ["amenity:ferry_terminal"],
    "helicopter": ["aeroway:helipad", "aeroway:heliport", "aeroway:aerodrome"],
    "aerialway": ["aerialway:station"],
    "ski": ["aerialway:station"],
}

# How alike the label and the candidate's name must be before this registers it unattended.
#
# High on purpose. A human working the queue sees the whole result list and can tell that
# "Bern" means Bern railway station; this sees one string and must not talk itself into a
# match. Below the threshold the label stays in the queue, which costs nothing.
MIN_CONFIDENCE = 0.82

# Abbreviations users write that OSM spells out, or used to.
#
# This is the "names change" problem in miniature. Trips carry 2,734 endpoints labelled
# "Wien Hbf" — a name OSM no longer uses, having renamed it "Wien Hauptbahnhof" — so
# searching the label verbatim today returns *Dresden Hbf* and nothing else. Same for
# "München Hbf" and "Berlin Hbf", together another ~7,000 endpoints.
#
# Each label is therefore searched in both forms and scored against both, which turns a
# systematic miss on the busiest German-speaking stations into an exact match. Kept small and
# unambiguous on purpose: an expansion that is merely usually right would start matching the
# wrong station, and the queue is a safe place for anything uncertain.
ABBREVIATIONS = {
    "hbf": "hauptbahnhof",
    "hb": "hauptbahnhof",
    "bf": "bahnhof",
    "hl.n.": "hlavní nádraží",
    "st.": "station",
}


def expand_abbreviations(label):
    """The label with known abbreviations spelled out, or None if it has none."""
    words = strip_flag(label).split()
    expanded, changed = [], False
    for word in words:
        key = word.lower()
        if key in ABBREVIATIONS:
            expanded.append(ABBREVIATIONS[key])
            changed = True
        else:
            expanded.append(word)
    return " ".join(expanded) if changed else None


def _fold(text):
    return "".join(
        ch
        for ch in unicodedata.normalize("NFD", (text or "").lower())
        if ch.isalnum() and unicodedata.category(ch) != "Mn"
    )


# The separator apply_city_prefix() puts between a city and a station name.
_CITY_SEPARATOR = " - "


def split_city_prefix(name):
    """('Melbourne', 'Richmond') from 'Melbourne - Richmond'. City is None when unprefixed.

    The prefix is added by apply_city_prefix() when the station name alone does not say which
    city it is in, so it is present on some spellings of a station and absent from others.
    """
    stripped = strip_flag(name or "").strip()
    if _CITY_SEPARATOR in stripped:
        city, _, rest = stripped.partition(_CITY_SEPARATOR)
        if city.strip() and rest.strip():
            return city.strip(), rest.strip()
    return None, stripped


def confidence(label, candidate_name):
    """How sure we are that `candidate_name` is what `label` meant, 0..1.

    Scored against the label as written and against its expanded form, whichever agrees
    better: "Wien Hbf" and "Wien Hauptbahnhof" are the same station under two spellings, and
    only the expansion says so.

    ── Why the city prefix is removed before comparing ───────────────────────────────────────
    Comparing the whole strings scores the city twice — once as itself and once as padding —
    and that is enough to pick the wrong station. Measured, and it registered 2,004 endpoints
    against the wrong place:

        label "Melbourne - Richmond" vs "Melbourne - East Richmond"   0.89   <- chosen
        label "Melbourne - Richmond" vs "Richmond"                    0.64   <- correct

    Richmond and East Richmond are different stations 677m apart. The correct candidate lost
    precisely because it carries no prefix — its suburb is Richmond, so apply_city_prefix()
    leaves it alone — while its wrong neighbour carries one that matches the label's. Nine
    shared characters of "melbourne" outweighed the "east" that makes them different places.

    So the city is compared as a city and the station name as a station name. A city stated on
    both sides and disagreeing is disqualifying: "Melbourne - Richmond" is not "Sydney -
    Richmond", however alike the two strings look.
    """
    label_city, label_core = split_city_prefix(label)
    candidate_city, candidate_core = split_city_prefix(candidate_name)

    b = _fold(candidate_core)
    if not b:
        return 0.0

    # Both name a city and they are not the same city: a different place, whatever the rest
    # of the string says. Only checked when both state one — an unprefixed candidate is not
    # claiming to be anywhere, and Photon was queried with the full label anyway.
    if label_city and candidate_city:
        if difflib.SequenceMatcher(
            None, _fold(label_city), _fold(candidate_city)
        ).ratio() < 0.85:
            return 0.0

    best = 0.0
    for form in (label_core, expand_abbreviations(label_core)):
        a = _fold(form) if form else None
        if not a:
            continue
        if a == b:
            return 1.0
        best = max(best, difflib.SequenceMatcher(None, a, b).ratio())
    return best


# How much better the winner must be than the best *different* station before this registers
# it unattended.
#
# Without this the top two candidates can be near-identical in score and one is chosen by
# floating-point luck — which is how a label ends up on a station nobody can tell it was meant
# to be. Cities repeat station names ("Richmond" exists in Melbourne, Sydney and London), and
# a label that cannot tell them apart belongs in the queue where a human sees the whole list.
AMBIGUITY_MARGIN = 0.05

# Both searches must land on the same OSM object, this close to where the label's trips end.
# The same rule the admin panel uses to collapse its two lists into one answer.
AGREEMENT_M = 50
AGREEMENT_RADIUS_KM = 1


def _by_name(label, station_type):
    """The best station the trip form would offer for this label, or None."""
    query = strip_flag(label).strip()
    if not query:
        return None

    tags = OSM_TAGS.get(station_type)

    # Search both spellings. Photon indexes the name OSM holds *now*, so a label written the
    # way the station used to be called finds nothing — searching the expanded form is what
    # reaches it.
    queries = [query]
    expanded = expand_abbreviations(label)
    if expanded and expanded.lower() != query.lower():
        queries.append(expanded)

    features = []
    for term in queries:
        params = {"q": term, "limit": 10}
        if tags:
            params["osm_tag"] = tags
        responses = photonRequestLangs("/api", params, ("en", "default"), timeout=10)
        if all(r is None for r in responses.values()):
            raise RuntimeError("Photon unavailable")
        features.extend(process_station_results(responses))

    if not features:
        return None

    # Score every result, not just the first: Photon ranks by its own relevance, which is not
    # the same question as "is this the place this label names".
    scored = []
    for feature in features:
        props = feature.get("properties", {})
        scored.append((confidence(label, props.get("name")), feature))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    best_score, best = scored[0]
    if best is None or best_score < MIN_CONFIDENCE:
        return None

    # Is the winner actually distinguishable from the runner-up?
    #
    # The same query returns one station as several OSM objects, so "the runner-up" has to
    # mean a different *station*, not a different object of this one. Photon's results are
    # already deduplicated by process_station_results(), which collapses objects of one
    # station, so anything still carrying a different osm id here is a genuinely different
    # place — and if it scores as well as the winner, neither can be trusted unattended.
    best_key = (best.get("properties", {}).get("osm_type"),
                best.get("properties", {}).get("osm_id"))
    for score, feature in scored[1:]:
        if score < best_score - AMBIGUITY_MARGIN:
            break
        props = feature.get("properties", {})
        if (props.get("osm_type"), props.get("osm_id")) != best_key:
            return None

    # A country stated by the label and contradicted by the candidate is a stronger signal
    # than the name similarity is — same name, wrong country is a different place.
    props = best["properties"]
    label_country = None
    stripped = strip_flag(label)
    if stripped != label and len(label) >= 2:
        label_country = country_from_flag(label)
    if label_country and props.get("countrycode") and props["countrycode"] != label_country:
        return None

    return {"feature": best, "score": best_score}


def _by_location(label, station_type, location):
    """What is actually at the label's own location, nearest first.

    Asked without the label's text, so nothing about how it is spelled can mislead it.
    """
    params = {
        "lat": location["lat"],
        "lon": location["lng"],
        "radius": AGREEMENT_RADIUS_KM,
        "limit": 10,
    }
    tags = OSM_TAGS.get(station_type)
    if tags:
        params["osm_tag"] = tags
    responses = photonRequestLangs("/reverse", params, ("en", "default"), timeout=10)
    if all(r is None for r in responses.values()):
        raise RuntimeError("Photon unavailable")
    return process_station_results(responses)


def _osm_key(feature):
    props = feature.get("properties", {})
    return props.get("osm_type"), props.get("osm_id")


def find_candidate(label, station_type):
    """What the two searches make of this label. Always returns a dict with a `status`:

      ok           both landed on one OSM object within AGREEMENT_M — safe to register
      far          both agree on the object, but it sits further away than that. Probably the
                   right station with a bad position; an admin should place the pin.
      ambiguous    they disagree, or two different places are equally close
      no_match     the name search found nothing confident
      no_location  the label's trips have no path, so there is nothing to agree with

    The name search answers "what is called this", the location search "what is at the place
    these trips end". The location is what decides; the name only has to not contradict it.
    """
    location = label_location(label, station_type)
    if not location:
        return {"status": "no_location"}

    by_name = _by_name(label, station_type)
    if not by_name:
        return {"status": "no_match"}

    here = {"lat": location["lat"], "lng": location["lng"]}

    def distance(feature):
        coords = feature.get("geometry", {}).get("coordinates")
        if not coords or len(coords) < 2:
            return None
        return getDistance(here, {"lat": coords[1], "lng": coords[0]})

    nearby = _by_location(label, station_type, location)
    named_key = _osm_key(by_name["feature"])
    agreed = named_key in {_osm_key(f) for f in nearby}

    # Everything either search puts within AGREEMENT_M. Two distinct objects that close is a
    # choice, and a choice belongs to a human.
    close = {}
    for feature in [by_name["feature"], *nearby]:
        away = distance(feature)
        if away is not None and away <= AGREEMENT_M:
            close.setdefault(_osm_key(feature), (feature, away))

    if len(close) == 1:
        (winner, away), = close.values()
        if _osm_key(winner) == named_key and agreed:
            return {
                "status": "ok",
                "feature": winner,
                "score": by_name["score"],
                "distance_m": away,
            }
        return {"status": "ambiguous"}

    # Nothing that close. If both searches still picked the same object, the match is likely
    # right and the position is what is wrong — which a human fixes by dragging the marker,
    # not by choosing a different station.
    if not close and agreed:
        return {
            "status": "far",
            "feature": by_name["feature"],
            "score": by_name["score"],
            "distance_m": distance(by_name["feature"]),
        }
    return {"status": "ambiguous"}


def record_check(label_id, status):
    """Remember what this run decided, so the queue can show why a label is still in it."""
    with pg_session() as pg:
        pg.execute(
            "UPDATE station_labels SET auto_checked_at = now(), auto_result = :status"
            " WHERE label_id = :label_id",
            {"status": status, "label_id": label_id},
        )


def pending_labels(limit, pg):
    return pg.execute(
        """
        SELECT label_id, sample_label, station_type, occurrences
        FROM station_labels
        WHERE station_id IS NULL AND occurrences > 0
          -- Already looked at, whatever the verdict. Re-asking Photon the same question on
          -- every run costs queries and changes nothing; clear auto_checked_at to redo one.
          AND auto_checked_at IS NULL
          -- Belt and braces: an untracked mode should never be in this table at all, but a
          -- stale row from before the exclusion must not be auto-registered against a Photon
          -- result — least of all one of the personal modes, whose labels are private.
          AND station_type_tracked(station_type)
        ORDER BY occurrences DESC
        LIMIT :limit
        """,
        {"limit": limit},
    ).fetchall()


def seed_run(limit, delay, min_occurrences, dry_run, progress, should_stop=None):
    """Work the queue, reporting as it goes. Returns the final totals.

    `progress(line, totals)` is called once per label with a line for a human and the running
    counts; `should_stop()` is asked after each one, so a run can be ended without losing
    anything — a label is finished when its auto_checked_at is set, not when the run is.
    """
    totals = {
        "total": 0,
        "attempted": 0,
        "registered": 0,
        "skipped": 0,
        "failed": 0,
        "endpoints_gained": 0,
    }

    with pg_session() as pg:
        rows = [
            r for r in pending_labels(limit, pg) if r["occurrences"] >= min_occurrences
        ]
    totals["total"] = len(rows)

    def report(line):
        totals["attempted"] += 1
        progress(line, totals)

    for row in rows:
        label, station_type = row["sample_label"], row["station_type"]
        try:
            candidate = find_candidate(label, station_bucket(station_type))
        except Exception as e:
            totals["failed"] += 1
            report(f"!  {label}: {e}")
            # A Photon failure is not this label's fault; stop rather than burn through the
            # rest of the queue marking everything unmatched.
            break

        if candidate["status"] != "ok":
            totals["skipped"] += 1
            note = candidate["status"]
            if candidate.get("distance_m") is not None:
                note += f", {candidate['distance_m']:,.0f}m"
            if not dry_run:
                record_check(row["label_id"], candidate["status"])
            report(f"-  {row['occurrences']:,}  {label}  ({note})")
        else:
            props = candidate["feature"]["properties"]
            coords = candidate["feature"]["geometry"]["coordinates"]
            line = (
                f"{row['occurrences']:,}  {label}  ->  {props.get('name')}  "
                f"[{candidate['score']:.2f}, {candidate['distance_m']:,.0f}m]"
            )

            if dry_run:
                totals["registered"] += 1
                report(f"~  {line}")
            else:
                station_id = upsert_station(
                    station_type=station_type,
                    name_intl=props.get("name"),
                    name_local=props.get("name_local"),
                    osm_type=props.get("osm_type"),
                    osm_id=props.get("osm_id"),
                    country_code=props.get("countrycode"),
                    lat=coords[1],
                    lng=coords[0],
                )
                if station_id is None:
                    totals["skipped"] += 1
                    report(f"!  {row['occurrences']:,}  {label}  (could not register)")
                else:
                    # The label as people write it becomes a spelling of this station — that
                    # is what resolves their trips, and it is often not the station's own name.
                    add_aliases(station_id, [(strip_flag(label), "alias", None)])
                    resync_station(station_id)

                    # Registering a station is not the same as resolving the spelling: if
                    # another station already answers to it, it stays ambiguous and resolves
                    # to neither. Record what is true rather than what was attempted.
                    with pg_session() as pg:
                        attached = pg.execute(
                            "SELECT station_id FROM station_labels WHERE label_id = :id",
                            {"id": row["label_id"]},
                        ).scalar()
                    record_check(row["label_id"], "ok" if attached else "not_attached")
                    if attached:
                        totals["registered"] += 1
                        totals["endpoints_gained"] += row["occurrences"]
                        report(f"ok {line}")
                    else:
                        totals["skipped"] += 1
                        report(
                            f"!  {row['occurrences']:,}  {label}  (registered {station_id}, "
                            f"but the spelling still does not resolve)"
                        )

        if should_stop and should_stop():
            break
        time.sleep(delay)

    return totals


# ── Running one from the admin panel ──────────────────────────────────────────────────────
#
# The console version prints; this one writes the same lines to station_seed_runs, because the
# browser polling for them may be talking to a different gunicorn worker than the one doing
# the work. See migration 0067.

# How much of the log to keep. Enough to see what the run has been doing, not so much that a
# 5,000-label run carries its whole history in one row that is rewritten twice a second.
LOG_LINES = 200

# A running row whose heartbeat is older than this belongs to a worker that is gone.
STALE_AFTER_S = 120


def start_seed_run(app, username, limit, delay, min_occurrences, dry_run):
    """Begin a pass in a background thread. Returns the run as /seed reports it."""
    # Also clears a run whose worker restarted mid-pass, which would otherwise claim to be
    # running forever and block this one.
    run_status()

    with pg_session() as pg:
        # One run at a time, enforced in the insert rather than by reading first: two passes
        # over the same queue would ask Photon for everything twice.
        run_id = pg.execute(
            # CAST() rather than ::jsonb: SQLAlchemy does not recognise a bind parameter
            # followed by a colon, so ":params::jsonb" reaches Postgres with the ":params"
            # still in it.
            "INSERT INTO station_seed_runs (started_by, params)"
            " SELECT :user, CAST(:params AS jsonb)"
            " WHERE NOT EXISTS (SELECT 1 FROM station_seed_runs WHERE state = 'running')"
            " RETURNING run_id",
            {
                "user": username,
                "params": json.dumps(
                    {
                        "limit": limit,
                        "delay": delay,
                        "min_occurrences": min_occurrences,
                        "dry_run": dry_run,
                    }
                ),
            },
        ).scalar()
    if run_id is None:
        return {"success": False, "error": "a seeding run is already going", **run_status()}

    lines = []

    def progress(line, totals):
        lines.append(line)
        del lines[:-LOG_LINES]
        _write_progress(run_id, lines, totals)

    def should_stop():
        with pg_session() as pg:
            return bool(
                pg.execute(
                    "SELECT stop_requested FROM station_seed_runs WHERE run_id = :id",
                    {"id": run_id},
                ).scalar()
            )

    def work():
        try:
            with app.app_context():
                totals = seed_run(
                    limit, delay, min_occurrences, dry_run, progress, should_stop
                )
            _finish(run_id, totals, "stopped" if should_stop() else "done")
        except Exception as e:
            logger.warning(f"Station seeding run {run_id} failed: {e}")
            _finish(run_id, None, "failed", error=str(e))

    threading.Thread(target=work, daemon=True, name=f"station-seed-{run_id}").start()
    return {"success": True, **run_status()}


def _write_progress(run_id, lines, totals):
    with pg_session() as pg:
        pg.execute(
            """
            UPDATE station_seed_runs
               SET updated_at = now(), log = CAST(:log AS jsonb),
                   total = :total, attempted = :attempted, registered = :registered,
                   skipped = :skipped, failed = :failed,
                   endpoints_gained = :endpoints_gained
             WHERE run_id = :id
            """,
            {"log": json.dumps(lines), "id": run_id, **totals},
        )


def _finish(run_id, totals, state, error=None):
    with pg_session() as pg:
        pg.execute(
            """
            UPDATE station_seed_runs
               SET state = :state, error = :error, finished_at = now(), updated_at = now(),
                   total = COALESCE(:total, total),
                   attempted = COALESCE(:attempted, attempted),
                   registered = COALESCE(:registered, registered),
                   skipped = COALESCE(:skipped, skipped),
                   failed = COALESCE(:failed, failed),
                   endpoints_gained = COALESCE(:endpoints_gained, endpoints_gained)
             WHERE run_id = :id
            """,
            {
                "id": run_id,
                "state": state,
                "error": error,
                **(totals or dict.fromkeys(
                    ["total", "attempted", "registered", "skipped", "failed",
                     "endpoints_gained"], None
                )),
            },
        )


def latest_run():
    with pg_session() as pg:
        row = pg.execute(
            """
            SELECT *, extract(epoch FROM now() - updated_at)::float AS since_update
            FROM station_seed_runs ORDER BY run_id DESC LIMIT 1
            """
        ).fetchone()
    return dict(row._mapping) if row else None


def request_stop():
    """Ask the current run to finish after the label it is on."""
    with pg_session() as pg:
        pg.execute(
            "UPDATE station_seed_runs SET stop_requested = TRUE WHERE state = 'running'"
        )
    return {"success": True}


def run_status():
    """The last run, as the panel shows it, plus the registry totals it is moving."""
    run = latest_run()
    if run and run["state"] == "running" and run["since_update"] > STALE_AFTER_S:
        # Its worker restarted mid-pass. Nothing was lost — every label it finished is
        # recorded — but the row would otherwise claim to be running forever and block the
        # next run from starting.
        _finish(run["run_id"], None, "failed", error="interrupted by a restart")
        run = latest_run()
    return {"run": run, "stats": registry_stats()}
