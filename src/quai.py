"""Station search through quai (trainlog_quai repo), the station index built from OSM.

quai groups every OSM object of a station into one result per mode, so its answers need
none of the deduplication Photon's do. Results are returned as Photon-shaped features, so
callers and the frontend treat both sources alike.
"""

import copy
import difflib
import logging
import re
import time
import unicodedata

import requests

from py.utils import load_config
from src.pg import pg_session

logger = logging.getLogger(__name__)

# Trip type -> quai mode. Anything else (car, walk, POIs, accommodation...) stays on Photon.
QUAI_MODES = {
    "train": "train",
    "rail": "train",
    "tram": "tram",
    "metro": "metro",
    "bus": "bus",
    "ferry": "ferry",
    "funicular": "funicular",
    "aerialway": "aerialway",
    "ski": "aerialway",
}


def quai_url():
    url = load_config().get("quai", {}).get("url", "https://quai.srv.trainlog.me")
    return url.rstrip("/") if url else None


def _place_name(place):
    """A city's name ({name, name:en} from quai), in English where its own is not in Latin
    script, and in its first language where bilingual ("Ixelles - Elsene" is Ixelles)."""
    place = place or {}
    name = place.get("name")
    if name and any(ord(ch) > 0x24F for ch in name if ch.isalpha()):
        name = place.get("name:en") or name
    return re.split(r"\s+-\s+|\s*/\s*", name)[0] if name else None


def _lift_end_name(name, end):
    """A lift's end where OSM maps no station, in the user's language (quai gives it as
    lift_end, lower or upper): "Tråstølheisen nedre stasjon", "Tråstølheisen, gare amont"."""
    from flask import has_request_context, session

    from src.utils import lang

    texts = lang["en"]
    if has_request_context():
        texts = lang.get((session.get("userinfo") or {}).get("lang"), texts)
    return texts["liftLowerStation" if end == "lower" else "liftUpperStation"].replace("{name}", name)


def place_of(station):
    """Where a station is, as people say it: its town, village or hamlet (Åndalsnes), else its
    municipality (Rauma)."""
    return _place_name(station.get("settlement") or station.get("city"))


def prefix_place_of(station):
    """The place put before a station's name where it needs one: a city places.csv names
    (London, city_override), whose parts are told apart by the hint only ("London - Euston
    Road", shown in Camden Town), else where it is (place_of)."""
    if station.get("city_override") and station.get("city"):
        return _place_name(station["city"])
    return place_of(station)


def station_label(station):
    """The name Trainlog gives a station: the one set for it by hand (apply_overrides), else
    quai's label, after its lift or funicular line if on one, else after its city where the
    name alone does not say where it is ("Royan - Gare"), as Trainlog has always named
    stations; "Lyon Part-Dieu" and "Brussels-Luxembourg" as they are."""
    if (station.get("override") or {}).get("name"):
        return station["override"]["name"]
    label = station["label"]
    if station.get("lift_end"):
        label = _lift_end_name(label, station["lift_end"])
    # A lift's or funicular's station goes by its ski area, else its line, as people know it:
    # "Val Thorens - Péclet", "Fløibanen - Fløyen" rather than "Bergen - Fløyen";
    # "Ulriksbanen øvre stasjon" says it already.
    line = station.get("ski_area") or station.get("line_name")
    if line:
        # Named after the line already, however it is written ("Fløibanen, nedre stasjon",
        # "Ulriksbanen nedre stasjon"): with the " - " the others have, but for a part in
        # brackets ("Voss Gondol (aval)").
        named = re.match(re.escape(line) + r"[\s,:;/–—-]+(?!\()(.+)$", label, re.IGNORECASE)
        if named:
            rest = named.group(1)
            return f"{label[:len(line)]} - {rest[:1].upper()}{rest[1:]}"
        if line.lower() in label.lower():
            return label
        return f"{line} - {label}"
    city = prefix_place_of(station)
    if station.get("needs_place") and city:
        return f"{city} - {station['label']}"
    return station["label"]


def station_overrides(stations):
    """The overrides set for these quai stations (station_overrides): {(mode, station_key):
    {name, lat, lng}}. Empty if the database cannot be read: an override is never worth
    failing a search for."""
    keys = [(s["mode"], s["station_key"]) for s in stations if s.get("station_key")]
    if not keys:
        return {}
    try:
        with pg_session() as pg:
            rows = pg.execute(
                """
                SELECT mode, station_key, name, lat, lng, tracks, merged_into FROM station_overrides
                WHERE (mode, station_key) IN (SELECT * FROM unnest(:modes, :keys))
                """,
                {"modes": [k[0] for k in keys], "keys": [k[1] for k in keys]},
            ).fetchall()
    except Exception as e:
        logger.warning(f"Station overrides unavailable: {e}")
        return {}
    return {
        (r.mode, r.station_key): {"name": r.name, "lat": r.lat, "lng": r.lng, "tracks": r.tracks,
                                  "merged_into": r.merged_into}
        for r in rows
    }


def merge_tracks(tracks, overrides):
    """OSM's tracks with those set by hand over them, by track: an override adds a track or
    moves OSM's of that ref (marked override), a hidden one removes OSM's."""
    by_ref = {track_key(t["ref"]): t for t in tracks or []}
    for t in overrides or []:
        key = track_key(t.get("ref"))
        if not key:
            continue
        if t.get("hidden"):
            by_ref.pop(key, None)
        else:
            by_ref[key] = {"ref": t["ref"], "lat": t["lat"], "lng": t["lng"],
                           "on_track": bool(t.get("on_track")), "override": True}
    return sorted(by_ref.values(), key=lambda t: (len(t["ref"]), t["ref"]))


# Merges change seldom and are looked up on every search: kept a minute, and dropped as soon
# as one is set (forget_station_merges).
MERGES_TTL_S = 60
_merges = {"at": None, "map": {}}


def station_merges():
    """{(mode, station_key): station_key it is merged into} (station_overrides.merged_into).
    The last known if the database cannot be read."""
    if _merges["at"] is None or time.monotonic() - _merges["at"] > MERGES_TTL_S:
        try:
            with pg_session() as pg:
                rows = pg.execute(
                    "SELECT mode, station_key, merged_into FROM station_overrides "
                    "WHERE merged_into IS NOT NULL"
                ).fetchall()
            _merges["map"] = {(r.mode, r.station_key): r.merged_into for r in rows}
            _merges["at"] = time.monotonic()
        except Exception as e:
            logger.warning(f"Station merges unavailable: {e}")
    return _merges["map"]


def forget_station_merges():
    _merges["at"] = None


def resolve_key(mode, key):
    """The station a key stands for in Trainlog: the one it is merged into, if any."""
    return station_merges().get((mode, key), key)


# quai's stations by key, for merges: what a merged station is replaced by, and what its
# target gains. Kept ten minutes.
_station_cache = {}
STATION_CACHE_TTL_S = 600


def quai_station(mode, key):
    """quai's station of `mode` with that key (redirects followed), or None."""
    cached = _station_cache.get((mode, key))
    if cached and time.monotonic() - cached[0] < STATION_CACHE_TTL_S:
        return copy.deepcopy(cached[1])
    station = (quai_get(f"station/{mode}/{key}") or {}).get("station")
    if station:
        _station_cache[(mode, key)] = (time.monotonic(), station)
    return copy.deepcopy(station) if station else None


def _merge_stations(stations):
    """In place: each station merged into another (station_merges) becomes that one, keeping
    what the request found about it (distance, tier); and each station others are merged
    into gains their tracks and lines. Positions in the list are kept, so a station can then
    appear twice."""
    merges = station_merges()
    if not merges:
        return
    for station in stations:
        target = merges.get((station.get("mode"), station.get("station_key")))
        found = target and quai_station(station["mode"], target)
        if found:
            kept = {k: station[k] for k in ("distance_m", "distance_km", "tier", "score", "matched")
                    if k in station}
            station.clear()
            station.update(found, **kept)
    into = {}
    for (mode, key), target in merges.items():
        into.setdefault((mode, target), []).append(key)
    for station in stations:
        for key in into.get((station.get("mode"), station.get("station_key")), []):
            merged = quai_station(station["mode"], key)
            if not merged:
                continue
            tracks = {track_key(t["ref"]): t for t in station.get("tracks") or []}
            for t in merged.get("tracks") or []:
                tracks.setdefault(track_key(t["ref"]), t)
            station["tracks"] = sorted(tracks.values(), key=lambda t: (len(t["ref"]), t["ref"]))
            lines = {line.get("ref"): line for line in station.get("lines") or []}
            for line in merged.get("lines") or []:
                lines.setdefault(line.get("ref"), line)
            station["lines"] = list(lines.values())


def apply_overrides(stations, follow_merges=True):
    """quai stations as Trainlog takes them, in place: merged into another where set (unless
    follow_merges is False: the station explorer shows a station as it is), and with the
    names and positions set for them by hand."""
    if follow_merges:
        _merge_stations(stations)
    overrides = station_overrides(stations)
    for station in stations:
        override = overrides.get((station.get("mode"), station.get("station_key")))
        if not override:
            continue
        station["override"] = override
        if override["lat"] is not None:
            station["lat"], station["lng"] = override["lat"], override["lng"]
        if override["tracks"]:
            station["tracks"] = merge_tracks(station.get("tracks"), override["tracks"])
    return stations


def _features(stations):
    """quai stations as Photon features, with homonyms told apart by city, else region."""
    apply_overrides(stations)
    # Once merged, a station and the one it is merged into are the same: listed once, where
    # the first of them came.
    seen = set()
    stations = [s for s in stations
                if (s.get("mode"), s.get("station_key")) not in seen
                and not seen.add((s.get("mode"), s.get("station_key")))]
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]},
            "properties": {
                "name": station_label(s),
                "countrycode": s.get("country") or "",
                "city": place_of(s),
                "state": s.get("region"),
                "osm_type": s["osm_type"],
                "osm_id": s["osm_id"],
                "station_key": s["station_key"],
                # How well the name matched (0 exact … 4 fuzzy), for the page to keep quai's
                # order while moving up stations the user knows.
                "tier": s.get("tier"),
                # Rail stations' numbered tracks, and the lines calling there with a stop
                # of each: [{ref, lat, lng, on_track}] (lines also have colour).
                "tracks": s.get("tracks") or [],
                "lines": s.get("lines") or [],
                "source": "quai",
            },
        }
        for s in stations
    ]
    groups = {}
    for feature in features:
        props = feature["properties"]
        groups.setdefault((props["name"], props["countrycode"]), []).append(props)
    for homonyms in groups.values():
        if len(homonyms) < 2:
            continue
        for field in ("city", "state"):
            places = [props.get(field) for props in homonyms]
            if None not in places and len(set(places)) == len(places):
                for props in homonyms:
                    props["name"] += f" ({props[field]})"
                break
        else:
            for i, props in enumerate(homonyms):
                props["homonymy_order"] = f" ({chr(ord('a') + i)})"
    return features


def search_stations(trip_type, q=None, lat=None, lon=None, radius_km=None, limit=10,
                    lang=None, timeout=None):
    """Stations of the trip type's mode named like `q`, or near lat/lon when `q` is None.

    Returns Photon-shaped features, or None if quai does not cover the type or cannot be
    reached.
    """
    mode = QUAI_MODES.get(trip_type)
    # Long enough for a quai answering from disk while it rebuilds (quai.timeout, seconds).
    timeout = timeout or load_config().get("quai", {}).get("timeout", 10)
    url = quai_url()
    if not mode or not url:
        return None
    if q is not None:
        endpoint, params = "search", {"q": q, "mode": mode, "limit": limit}
        if lat is not None and lon is not None:
            params.update(lat=lat, lon=lon)
    else:
        endpoint = "reverse"
        params = {"lat": lat, "lon": lon, "mode": mode, "limit": limit, "radius": radius_km or 1}
    if lang:
        params["lang"] = lang
    try:
        resp = requests.get(f"{url}/{endpoint}", params=params, timeout=timeout)
        resp.raise_for_status()
        return _features(resp.json()["stations"])
    except Exception as e:
        logger.warning(f"quai {endpoint} failed: {e}")
        return None


def nearest_stations(mode, points, radius_m=400, candidates=1, timeout=30):
    """The quai station of `mode` nearest each [lat, lng] within `radius_m`, in the points'
    order (None where none), with Trainlog's overrides applied and its name for each
    ("trainlog_name"). With candidates above 1, that many of the nearest for each point, as a
    list, nearest first. Raises if quai cannot be reached."""
    url = quai_url()
    if not url:
        raise RuntimeError("quai is not configured")
    found = []
    for start in range(0, len(points), 5000):
        body = {"mode": mode, "radius": radius_m, "points": points[start:start + 5000]}
        if candidates > 1:
            body["candidates"] = candidates
        resp = requests.post(f"{url}/nearest", json=body, timeout=timeout)
        resp.raise_for_status()
        found.extend(resp.json()["stations"])
    stations = [s for item in found for s in (item if isinstance(item, list) else [item]) if s]
    apply_overrides(stations)
    for station in stations:
        station["trainlog_name"] = station_label(station)
    return found


def quai_get(path, params=None, timeout=5):
    """quai's own JSON for `path`, or None if it cannot be reached."""
    url = quai_url()
    if not url:
        return None
    try:
        resp = requests.get(f"{url}/{path.lstrip('/')}", params=params, timeout=timeout)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"quai {path} failed: {e}")
        return None


TRACK_WORDS = re.compile(
    r"^(voie|gleis|gl\.?|track|platform|quai|binario|v[ií]a|spoor|tor|peron)\s*", re.IGNORECASE
)


def track_key(ref):
    """"Gleis 7", "Voie 7" and "7" are the same track. Mirrors stopKey() in new.html."""
    return TRACK_WORDS.sub("", str(ref or "").strip().lower())


def _fold(text):
    """Lower case, no accents, punctuation as spaces: how stop names are compared."""
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def name_likeness(name, station):
    """How much a name ("Bergen - Strandkaiterminalen båtkai", Entur's "Bryggen") is this
    station's, 0 to 1: 1 where one of its names is it; 0.95 where one contains it or is
    contained in it, a little less, so that the station of that very name wins ("Lausanne" is
    Lausanne, not Lausanne-Flon nearer the timetable's point); else how alike the closest
    of them is spelt."""
    wanted = _fold(name)
    if not wanted:
        return 0
    best = 0
    for candidate in [station.get("label"), station.get("trainlog_name"), station.get("name"),
                      station.get("latin"), *(station.get("names") or {}).values()]:
        have = _fold(candidate)
        if not have:
            continue
        if have == wanted:
            return 1
        if f" {wanted} " in f" {have} " or f" {have} " in f" {wanted} ":
            best = max(best, 0.95)
            continue
        best = max(best, difflib.SequenceMatcher(None, wanted, have).ratio())
    return best


# How alike a name must be spelt to be a station's ("Strandkaiterminalen båtkai" and
# "Strandkaiterminalen, båt Askøy" are; "Zachariasbryggen" and "Strandterminalen" are not).
NAME_LIKENESS = 0.75


# Stations about as near as the nearest, between which a name may decide: within this many
# times its distance, or this many metres further. Beyond, the nearest is the one, whatever
# the names say: "Lausanne" on Lausanne's platform 1 is not Lausanne-Flon, 400m off, which
# a name containing it would have made "alike".
NEAR_TIE_FACTOR = 1.5
NEAR_TIE_M = 30
# Beyond this, a station is only the one if named as the stop, not merely alike: where the
# right station is missing from quai (as Lausanne's was), Lausanne-Flon 439m off is no answer
# for "Lausanne". And a timetable's stop takes no station at all that far and not so named.
FAR_M = 150


def station_by_place(name, nearby):
    """Of `nearby` stations (nearest first, with distance_m), the one a stop or trip end is at,
    and whether it is named alike: the nearest, unless another about as near (NEAR_TIE_*) is
    named more like it (Bryggen's point 38m from Bryggen and 39m from another stop;
    Strandterminalen 141m off and Strandkaiterminalen 145m). (None, False) if none."""
    if not nearby:
        return None, False
    nearest = nearby[0]
    limit = max(nearest.get("distance_m", 0) * NEAR_TIE_FACTOR, nearest.get("distance_m", 0) + NEAR_TIE_M)
    close = [s for s in nearby if s.get("distance_m", 0) <= limit]
    if not name:
        return nearest, False
    def alike(s, likeness):
        return likeness >= NAME_LIKENESS and (likeness == 1 or s.get("distance_m", 0) <= FAR_M)

    likeness, _, station = max(((name_likeness(name, s), -i, s) for i, s in enumerate(close)),
                               key=lambda t: t[:2])
    if alike(station, likeness):
        return station, True
    return nearest, alike(nearest, name_likeness(name, nearest))


# Modes whose stops are placed by direction (stops_by_direction): where a line's two
# directions stop on tracks or kerbs a few metres apart. Not trains, which go by the platform
# the timetable gives (tracks), nor ferries or lifts.
SNAP_MODES = ("tram", "metro", "bus")


def stops_by_direction(mode, keys, timeout=5):
    """Where each station of a journey (quai keys, in the order travelled, None for none) is
    stopped at going that way: [[lat, lng] or None, ...], from OSM's route relations (quai's
    /directions). All None if quai cannot be reached."""
    if not any(keys):
        return [None] * len(keys)
    try:
        resp = requests.post(f"{quai_url()}/directions", json={"mode": mode, "keys": keys},
                             timeout=timeout)
        resp.raise_for_status()
        return resp.json()["positions"]
    except Exception as e:
        logger.warning(f"quai directions failed: {e}")
        return [None] * len(keys)


def stop_point_id(transitous_id):
    """The official id of a timetable's stop point in Transitous's id for it, as OSM tags it
    (ref:IFOPT): Germany's DHID "de:05315:16101:7:72" in DELFI's "de-DELFI_de:05315:16101:7:72",
    Switzerland's SLOID "ch:1:sloid:1120:0:668579". None for ids that are not one."""
    if not transitous_id or "_" not in transitous_id:
        return None
    point = transitous_id.split("_", 1)[1]
    # country:...:... with at least three parts, the shape of IFOPT ids.
    return point if re.match(r"^[a-z]{2}:[^:]+:[^:]+", point) else None


def quay_of(stations, point_id):
    """The station and its object carrying the stop-point id `point_id` (ref:IFOPT), from
    /reverse's quays: a stop position on the track rather than a platform beside it, where
    both carry it. (None, None) if none."""
    best = (None, None)
    for station in stations:
        for quay in station.get("quays") or []:
            if point_id in (quay.get("ids") or []):
                if quay.get("on_track"):
                    return station, quay
                best = best if best[1] else (station, quay)
    return best


def stations_at(trip_type, stops, radius_km=0.5):
    """The station at each timetable stop {lat, lng, platform?, name?, key?, id?}: of the trip
    type's mode within `radius_km`, the one holding the stop point its Transitous id names
    (stop_point_id: then its very platform, `platform` its own track, and point, `snap`), the one of that station key (a trip's saved end), else the nearest
    named alike, else the nearest, as {station, station_key, alike, tracks, lines, track, snap},
    or None. `track` is the stop's platform among the station's tracks, or None. `snap`, for
    stops given in the order travelled, is where the line stops there going that way
    (stops_by_direction), [lat, lng], or None. By name first, as points are rough: Bryggen's is 38m from Bryggen and
    39m from another stop.
    """
    mode = QUAI_MODES.get(trip_type)
    found = []
    for stop in stops:
        station = None
        if mode and stop.get("lat") is not None and stop.get("lng") is not None:
            data = quai_get("reverse", {"lat": stop["lat"], "lon": stop["lng"], "mode": mode,
                                        "radius": radius_km, "limit": 5, "quays": 1}, timeout=2)
            nearby = (data or {}).get("stations") or []
            # The station holding the stop point the timetable names; else that of the key;
            # else by place, a name only deciding between stations about as near
            # (station_by_place).
            point_station, quay = quay_of(nearby, stop_point_id(stop.get("id")))
            nearest = point_station or next(
                (s for s in nearby if stop.get("key") and s["station_key"] == stop["key"]), None)
            alike = nearest
            if nearest is None:
                nearest, named = station_by_place(stop.get("name"), nearby)
                alike = nearest if named else None
                if not named and nearest and nearest.get("distance_m", 0) > FAR_M:
                    nearest = None   # far, and not named as the stop: not its station
            if nearest:
                apply_overrides([nearest])
                ref = track_key(stop.get("platform"))
                tracks = nearest.get("tracks") or []
                station = {
                    "station": station_label(nearest),
                    "station_key": nearest["station_key"],
                    "alike": alike is not None,
                    "tracks": tracks,
                    "lines": nearest.get("lines") or [],
                    "track": next((t for t in tracks if ref and track_key(t["ref"]) == ref), None),
                    "snap": None,
                    "platform": None,
                }
                if quay and point_station is nearest:
                    # The very platform: its own track, not the timetable's numbering, and
                    # its point (on the track, for a stop position).
                    own = ((quay.get("ref") or "").split(";") or [""])[0].strip() or None
                    station["platform"] = own
                    station["track"] = next((t for t in tracks if own and track_key(t["ref"]) == track_key(own)),
                                            station["track"])
                    station["snap"] = [quay["lat"], quay["lng"]]
                    station["quay"] = True
        found.append(station)
    if mode in SNAP_MODES:
        keys = [station and station["station_key"] for station in found]
        for station, position in zip(found, stops_by_direction(mode, keys)):
            if station and position and not station.get("quay"):
                station["snap"] = position
    return found
