"""Register stations for the unresolved labels of existing trips.

New trips seed the registry as they are logged; this covers the years of trips before it.
It walks the unresolved queue from the most used label down, and registers a match only
when two independent searches agree: one on the label's text, one at where its trips
actually end. Anything less certain stays in the queue for a human, because an unresolved
label is harmless and a wrong one silently misattributes trips.

Only Photon (self-hosted) is queried; new stations are left for the background enricher,
so Overpass is never touched from here. Progress lives in station_labels.auto_checked_at,
so a stopped run simply continues next time.

Started from the admin panel's Seed button, which runs seed_run() in a background thread.
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

# Without them a search for "Bern" finds the city rather than the station.
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

# How alike the label and the candidate's name must be to register it unattended. High
# on purpose: below it the label stays in the queue, which costs nothing.
MIN_CONFIDENCE = 0.82

# Abbreviations users write that OSM spells out: labels still say "Wien Hbf" while OSM
# now says "Wien Hauptbahnhof". Kept to expansions that are never wrong.
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


_CITY_SEPARATOR = " - "


def split_city_prefix(name):
    """('Melbourne', 'Richmond') from 'Melbourne - Richmond'; city is None when unprefixed."""
    stripped = strip_flag(name or "").strip()
    if _CITY_SEPARATOR in stripped:
        city, _, rest = stripped.partition(_CITY_SEPARATOR)
        if city.strip() and rest.strip():
            return city.strip(), rest.strip()
    return None, stripped


def confidence(label, candidate_name):
    """How sure we are that `candidate_name` is what `label` meant, 0..1.

    The city prefix is compared separately from the station name: compared as one string, a
    shared city outweighs the difference between "Richmond" and "East Richmond". Two cities
    that disagree disqualify the candidate.
    """
    label_city, label_core = split_city_prefix(label)
    candidate_city, candidate_core = split_city_prefix(candidate_name)

    b = _fold(candidate_core)
    if not b:
        return 0.0

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


# How much the winner must beat the best different station by. Closer than this, the
# choice belongs to a human.
AMBIGUITY_MARGIN = 0.05

AGREEMENT_M = 50
AGREEMENT_RADIUS_KM = 1


def _by_name(label, station_type):
    """The best station the trip form would offer for this label, or None."""
    query = strip_flag(label).strip()
    if not query:
        return None

    tags = OSM_TAGS.get(station_type)

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

    # Photon's ranking is relevance, not "is this the station the label names".
    scored = []
    for feature in features:
        props = feature.get("properties", {})
        scored.append((confidence(label, props.get("name")), feature))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    best_score, best = scored[0]
    if best is None or best_score < MIN_CONFIDENCE:
        return None

    # Results are already deduplicated per station, so any other object scoring as well
    # is a different station.
    best_key = (best.get("properties", {}).get("osm_type"),
                best.get("properties", {}).get("osm_id"))
    for score, feature in scored[1:]:
        if score < best_score - AMBIGUITY_MARGIN:
            break
        props = feature.get("properties", {})
        if (props.get("osm_type"), props.get("osm_id")) != best_key:
            return None

    # Same name, different country is a different place.
    props = best["properties"]
    label_country = None
    stripped = strip_flag(label)
    if stripped != label and len(label) >= 2:
        label_country = country_from_flag(label)
    if label_country and props.get("countrycode") and props["countrycode"] != label_country:
        return None

    return {"feature": best, "score": best_score}


def _by_location(label, station_type, location):
    """What is at the label's own location, nearest first, searched without its text."""
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
    """What the two searches make of this label. Returns a dict with a `status`:

      ok           both landed on one OSM object within AGREEMENT_M: safe to register
      far          both agree on the object, but it is further away than that
      ambiguous    they disagree, or two places are equally close
      no_match     the name search found nothing confident
      no_location  the label's trips have no path to compare with
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

    # Two distinct objects this close is a choice for a human.
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

    # Both searches agree but nothing is close: the station is probably right and its
    # position wrong, which a human fixes by moving the pin.
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
          -- Clear auto_checked_at to re-check a label.
          AND auto_checked_at IS NULL
          AND station_type_tracked(station_type)
        ORDER BY occurrences DESC
        LIMIT :limit
        """,
        {"limit": limit},
    ).fetchall()


def seed_run(limit, delay, min_occurrences, dry_run, progress, should_stop=None):
    """Work the queue, reporting as it goes. Returns the final totals.

    `progress(line, totals)` is called once per label; `should_stop()` is asked after each.
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
            # A Photon outage would otherwise mark the rest of the queue as unmatched.
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
                    # The label as written becomes a spelling of the station, which resolves the trips.
                    add_aliases(station_id, [(strip_flag(label), "alias", None)])
                    resync_station(station_id)

                    # Another station may already hold this spelling; record the real outcome.
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


# ── Running one from the admin panel ─────────────────────────────────────────────────

LOG_LINES = 200

# A running row whose heartbeat is older than this belongs to a worker that is gone.
STALE_AFTER_S = 120


def start_seed_run(app, username, limit, delay, min_occurrences, dry_run):
    """Begin a pass in a background thread. Returns the run as /seed reports it."""
    # Also clears a run whose worker restarted mid-pass, which would otherwise block this one.
    run_status()

    with pg_session() as pg:
        # One run at a time, enforced by the insert itself.
        run_id = pg.execute(
            # CAST() rather than ::jsonb, which SQLAlchemy's parameter parser mangles.
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
        # Its worker restarted mid-pass; every finished label is already recorded.
        _finish(run["run_id"], None, "failed", error="interrupted by a restart")
        run = latest_run()
    return {"run": run, "stats": registry_stats()}
