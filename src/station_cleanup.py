"""Tidying a user's station names: the ends of their trips, grouped by the quai station they
are at, with every name the user has given it, to rename them all to its current one.

Over time one station gathers several names in a user's trips: Photon's ("Oslo Sentral"),
an older OSM name, a translation ("Gare centrale d'Oslo"). A trip's end is its path's first
or last point; the station is the one the trip was saved at (its station key) when that is
near it, else the one of that name among the nearest of the trip's mode within END_RADIUS_M,
else the nearest. Renaming also ties the trips to the station, by its key.
"""

import datetime

from py.utils import get_flag_emoji
from src.pg import pg_session
from src.quai import QUAI_MODES, nearest_stations, place_of, resolve_key, station_by_place

END_RADIUS_M = 400
# How many of the stations nearest an end to choose among by name.
CANDIDATES = 5

# Each trip's two ends, with its name and station key there; points rounded to about 100m, so that the many
# trips from one station take a single lookup.
ENDS_SQL = """
SELECT t.trip_type, e.side, e.label, e.station_key,
       round(ST_Y(e.point)::numeric, 3)::float8 AS lat,
       round(ST_X(e.point)::numeric, 3)::float8 AS lng,
       array_agg(t.trip_id ORDER BY t.trip_id) AS trip_ids
FROM trips t
JOIN paths p ON p.trip_id = t.trip_id
CROSS JOIN LATERAL (VALUES
    ('origin', t.origin_station, t.origin_station_key, ST_StartPoint(ST_GeometryN(p.geom, 1))),
    ('destination', t.destination_station, t.destination_station_key,
     ST_EndPoint(ST_GeometryN(p.geom, ST_NumGeometries(p.geom))))
) AS e(side, label, station_key, point)
WHERE t.user_id = :user_id AND t.trip_type = ANY(:types) AND e.point IS NOT NULL
GROUP BY 1, 2, 3, 4, 5, 6
"""


def station_label_with_flag(station):
    """The name a trip gets for this station, as the station search gives it: flag and name."""
    country = station.get("country")
    flag = get_flag_emoji(country) if country and len(country) == 2 else ""
    return f"{flag} {station['trainlog_name']}".strip()


def station_of_end(label, station_key, nearby):
    """The station a trip's end is at, of the `nearby` ones (nearest first, with distance_m),
    and whether it is named alike: the one the trip was saved at; else by place, its name only
    deciding between stations about as near (station_by_place: Strandkaiterminalen båtkai
    145m off, Strandterminalen 141m)."""
    station = next((s for s in nearby if station_key and s["station_key"] == station_key), None)
    if station is not None:
        return station, True
    return station_by_place(label, nearby)


def station_groups(user_id):
    """The user's stations: [{mode, station_key, name, city, country, lat, lng, names: [{label,
    origin, destination}], tidy}], untidy ones (several names, or one other than the
    station's) first, the most travelled first; and how many trip ends matched no station."""
    with pg_session() as pg:
        ends = pg.execute(ENDS_SQL, {"user_id": user_id, "types": list(QUAI_MODES)}).fetchall()

    by_mode = {}
    for end in ends:
        by_mode.setdefault(QUAI_MODES[end.trip_type], []).append(end)

    groups, unmatched = {}, 0
    for mode, mode_ends in by_mode.items():
        points = sorted({(end.lat, end.lng) for end in mode_ends})
        at = dict(zip(points, nearest_stations(mode, [list(p) for p in points], END_RADIUS_M,
                                               candidates=CANDIDATES)))
        for end in mode_ends:
            nearby = at[(end.lat, end.lng)]
            if not nearby:
                unmatched += len(end.trip_ids)
                continue
            # A trip saved at a station since merged into another is at that one.
            key = end.station_key and resolve_key(mode, end.station_key)
            station, alike = station_of_end(end.label, key, nearby)
            group = groups.get((mode, station["station_key"]))
            if group is None:
                group = groups[(mode, station["station_key"])] = {
                    "mode": mode,
                    "station_key": station["station_key"],
                    "name": station_label_with_flag(station),
                    "city": place_of(station),
                    "country": station.get("country"),
                    "lat": station["lat"],
                    "lng": station["lng"],
                    "names": {},
                }
            name = group["names"].setdefault(
                end.label, {"label": end.label, "origin": [], "destination": [], "alike": False})
            name[end.side].extend(end.trip_ids)
            # A name not alike the station's was put there by place only (a quay that is no
            # stop, Zachariasbryggen): the page leaves it out of a rename unless ticked.
            name["alike"] = name["alike"] or alike

    result = []
    for group in groups.values():
        names = sorted(group["names"].values(),
                       key=lambda n: -(len(n["origin"]) + len(n["destination"])))
        group["names"] = names
        group["tidy"] = [n["label"] for n in names] == [group["name"]]
        group["trips"] = sum(len(n["origin"]) + len(n["destination"]) for n in names)
        result.append(group)
    result.sort(key=lambda g: (g["tidy"], -g["trips"]))
    return result, unmatched


def rename_station(user_id, to, station_key, labels, origin_ids, destination_ids):
    """Renames to `to`, at the station `station_key`, the given ends of the user's trips that
    still bear one of `labels`: their origin for origin_ids, their destination for
    destination_ids. Returns how many changed."""
    now = datetime.datetime.now()
    params = {"user_id": user_id, "to": to, "key": station_key, "labels": labels, "now": now}
    with pg_session() as pg:
        origins = pg.execute(
            """
            UPDATE trips SET origin_station = :to, origin_station_key = :key, last_modified = :now
            WHERE user_id = :user_id AND trip_id = ANY(:ids) AND origin_station = ANY(:labels)
              AND (origin_station, origin_station_key) IS DISTINCT FROM (:to, :key)
            """,
            {**params, "ids": origin_ids},
        ).rowcount
        destinations = pg.execute(
            """
            UPDATE trips SET destination_station = :to, destination_station_key = :key,
                last_modified = :now
            WHERE user_id = :user_id AND trip_id = ANY(:ids) AND destination_station = ANY(:labels)
              AND (destination_station, destination_station_key) IS DISTINCT FROM (:to, :key)
            """,
            {**params, "ids": destination_ids},
        ).rowcount
    return origins + destinations
