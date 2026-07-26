from datetime import timezone

import dateutil
import polyline
from flexpolyline import decode as decode_flexpolyline

from py.utils import (
    get_flag_emoji,
    getCountryFromCoordinates,
    getDistanceFromPath,
    stringSimmilarity,
)
from src.pg import pg_session


def parse_api_time(time_str):
    """
    Given an ISO8601-like time string (e.g. "2025-01-16T17:37:00+01:00" or "2025-01-20T05:50:00Z"),
    return a tuple of:
       ( local_str, utc_str, dt_utc )
    If time_str is missing or invalid, return ("-1", "-1", None).
    """
    if not time_str:
        return ("-1", "-1", None)

    dt = dateutil.parser.parse(time_str)  # dt now has tzinfo if provided
    local_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    dt_utc = dt.astimezone(timezone.utc)
    utc_str = dt_utc.strftime("%Y-%m-%d %H:%M:%S")
    return (local_str, utc_str, dt_utc)


def _fetch_logo_url(operator_id, start_utc_str, pg):
    """
    Utility to fetch the best logo for an operator, given the start time (UTC).
    `start_utc_str` is a string "YYYY-MM-DD HH:MM:SS", or you can store it as a datetime.
    Returns the best matching URL or None if not found. Runs on the given PG session.
    """
    if not start_utc_str or start_utc_str == "-1":
        # No date => fallback: get a record with `effective_date` IS NULL.
        logo_row = pg.execute(
            """
            SELECT l.logo_url
            FROM operator_logos l
            WHERE l.operator_id = :operator_id
              AND l.effective_date IS NULL
            ORDER BY l.effective_date DESC NULLS LAST
            LIMIT 1
            """,
            {"operator_id": operator_id},
        ).fetchone()
    else:
        logo_row = pg.execute(
            """
            SELECT l.logo_url
            FROM operator_logos l
            WHERE l.operator_id = :operator_id
              AND (l.effective_date <= :start_utc OR l.effective_date IS NULL)
            ORDER BY l.effective_date DESC NULLS LAST
            LIMIT 1
            """,
            {"operator_id": operator_id, "start_utc": start_utc_str},
        ).fetchone()

    return logo_row["logo_url"] if logo_row else None


def build_operator_info(operator_name_api, start_utc_str):
    """
    Attempt to find operator info (short_name, long_name, logo_url) from DB by:
      1) Checking if there's a direct mapping in `here_api_operators` table
         (Used by both HERE and Google in this example).
      2) If not, do a similarity match with the `operators` table (short_name).
      3) Insert a new mapping if found.
    Return a dict with keys: "operator", "operator_name", "logo_url".

    If none found, returns fallback with "operator": <original_name>.
    """
    operator_name_api = operator_name_api.strip() if operator_name_api else ""
    if not operator_name_api:
        return {}

    with pg_session() as pg:
        # 1) Check direct mapping
        mapping = pg.execute(
            """
            SELECT trainlog_operator
            FROM here_api_operators
            WHERE here_operator = :here_operator
            """,
            {"here_operator": operator_name_api},
        ).fetchone()

        if mapping:
            # We have a mapped trainlog operator
            trainlog_op = mapping["trainlog_operator"]
            best_operator = pg.execute(
                """
                SELECT operator_id, short_name, long_name
                FROM operators
                WHERE short_name = :short_name
                """,
                {"short_name": trainlog_op},
            ).fetchone()

            if best_operator:
                logo_url = _fetch_logo_url(
                    best_operator["operator_id"], start_utc_str, pg
                )
                return {
                    "operator": best_operator["short_name"],
                    "operator_name": best_operator["long_name"],
                    "logo_url": logo_url,
                }
            else:
                # The direct mapping references something not in `operators`. Fallback:
                return {"operator": operator_name_api}
        else:
            # 2) Fuzzy match with `operators`
            db_operators = pg.execute(
                "SELECT operator_id, short_name, long_name FROM operators"
            ).fetchall()

            best_operator = None
            best_similarity = -1.0
            for row in db_operators:
                sim = stringSimmilarity(
                    operator_name_api.lower(), row["short_name"].lower()
                )
                if sim > best_similarity:
                    best_similarity = sim
                    best_operator = row

            # Arbitrary threshold for a "good" match
            if best_operator and best_similarity >= 0.5:
                # Insert new mapping
                pg.execute(
                    """
                    INSERT INTO here_api_operators (here_operator, trainlog_operator)
                    VALUES (:here_operator, :trainlog_operator)
                    ON CONFLICT (here_operator) DO NOTHING
                    """,
                    {
                        "here_operator": operator_name_api,
                        "trainlog_operator": best_operator["short_name"],
                    },
                )
                logo_url = _fetch_logo_url(
                    best_operator["operator_id"], start_utc_str, pg
                )
                return {
                    "operator": best_operator["short_name"],
                    "operator_name": best_operator["long_name"],
                    "logo_url": logo_url,
                }
            else:
                # Fallback
                return {"operator": operator_name_api}


def build_trip_dict(
    section_type,
    mode,
    line_name,
    path,
    start_local,
    start_utc,
    end_local,
    end_utc,
    operator_dict,
    origin_station,
    destination_station,
    distance,
    trip_duration,
    uid="",
    username="public",
):
    """
    Given the raw data for a single 'section' or 'step', build the standardized trip dict
    that your JS code expects.
    Returns a dict of shape:
      {
        "uid": ...
        "type": ...
        "line_name": ...
        "waypoints": ...
        ...
      }
    """
    # Convert local times into date/time components
    if start_local != "-1":
        start_date, start_time_full = start_local.split(" ")
        start_time = start_time_full[:-3]  # remove seconds
    else:
        start_date, start_time = ("-1", "-1")

    if end_local != "-1":
        _, end_time_full = end_local.split(" ")
        end_time = end_time_full[:-3]
    else:
        end_time = "-1"

    # Identify the trip type (walk, bus, train, ferry, etc.).
    # This can unify Google's vehicle types with HERE's modes.
    if section_type == "pedestrian" or mode == "pedestrian":
        trip_type = "walk"
    elif mode in [
        "regionalTrain",
        "highSpeedTrain",
        "intercityTrain",
        "cityTrain",
        "RAIL",
        "TRAIN",
    ]:
        trip_type = "train"
    elif mode in ["lightRail"]:
        trip_type = "tram"
    elif mode in ["subway"]:
        trip_type = "metro"
    elif mode in ["bus", "busRapid", "BUS", "INTERCITY_BUS"]:
        trip_type = "bus"
    elif mode in ["ferry", "FERRY"]:
        trip_type = "ferry"
    else:
        trip_type = mode or section_type  # fallback

    # Build the final dictionary
    trip_dict = {
        "uid": uid,
        "type": trip_type,
        "line_name": line_name,
        "waypoints": [],  # can fill from intermediate stops, see below
        "origin_coords": path[0] if path else [None, None],
        "destination_coords": path[-1] if path else [None, None],
        # Local datetimes
        "start_datetime": start_local,
        "end_datetime": end_local,
        # Split date/time fields
        "start_date": start_date,
        "start_time": start_time,
        "end_time": end_time,
        # UTC values
        "utc_filtered_start_datetime": start_utc,
        "utc_filtered_end_datetime": end_utc,
        # Station info
        "origin_station": origin_station,
        "destination_station": destination_station,
        # Distance / duration
        "trip_length": distance,
        "trip_duration": ["calc", trip_duration],
        # Operator info
        "operator": operator_dict.get("operator", ""),
        "operator_name": operator_dict.get("operator_name", ""),
        "logo_url": operator_dict.get("logo_url", None),
        # Additional fields
        "username": username,
    }

    return trip_dict


def convert_here_response_to_trips(here_json):
    """
    Convert HERE's JSON response into the array of 'trip' objects your JS code expects.
    """
    trips = []

    if "routes" not in here_json or not here_json["routes"]:
        return trips

    route = here_json["routes"][0]
    sections = route.get("sections", [])

    # We'll loop over each "section"
    for section in sections:
        polyline_str = section.get("polyline")
        path = []
        distance = 0
        if polyline_str:
            decoded = decode_flexpolyline(polyline_str)  # e.g. [(lat, lon, ...), ...]
            path = [[lat, lon] for (lat, lon, *_) in decoded]
            dist_list = getDistanceFromPath(path)
            distance = dist_list[-1] if dist_list else 0

        departure_time_str = section.get("departure", {}).get("time")
        arrival_time_str = section.get("arrival", {}).get("time")
        (start_local, start_utc, start_dt) = parse_api_time(departure_time_str)
        (end_local, end_utc, end_dt) = parse_api_time(arrival_time_str)
        trip_duration = int((end_dt - start_dt).total_seconds())

        operator_name_api = section.get("agency", {}).get("name", "")
        line_name = section.get("transport", {}).get("name", "")
        section_type = section.get("type", "")
        mode = section.get("transport", {}).get("mode", "")

        # Special fix: known big operators
        if operator_name_api in ["Havila", "Hurtigruten"]:
            mode = "ferry"

        # Build station names with flag emojis
        if path:
            origin_coords = path[0]
            destination_coords = path[-1]
            origin_country = getCountryFromCoordinates(*origin_coords).get(
                "countryCode", ""
            )
            destination_country = getCountryFromCoordinates(*destination_coords).get(
                "countryCode", ""
            )
        else:
            origin_country = ""
            destination_country = ""

        origin_flag_emoji = get_flag_emoji(origin_country)
        destination_flag_emoji = get_flag_emoji(destination_country)

        origin_station_name = (
            section.get("departure", {})
            .get("place", {})
            .get("name", here_json.get("origin_name", ""))
        )
        destination_station_name = (
            section.get("arrival", {})
            .get("place", {})
            .get("name", here_json.get("destination_name", ""))
        )

        origin_station = f"{origin_flag_emoji} {origin_station_name}"
        destination_station = f"{destination_flag_emoji} {destination_station_name}"

        # Get operator info from DB
        operator_dict = build_operator_info(operator_name_api, start_utc)

        # Build the standardized trip dict
        trip_dict = build_trip_dict(
            section_type=section_type,
            mode=mode,
            line_name=line_name,
            path=path,
            start_local=start_local,
            start_utc=start_utc,
            end_local=end_local,
            end_utc=end_utc,
            operator_dict=operator_dict,
            origin_station=origin_station,
            destination_station=destination_station,
            distance=distance,
            trip_duration=trip_duration,
        )

        # Build intermediate stops => waypoints
        waypoints_list = []
        for stop in section.get("intermediateStops", []):
            dep_place = stop.get("departure", {}).get("place", {})
            lat = dep_place.get("location", {}).get("lat")
            lng = dep_place.get("location", {}).get("lng")
            if lat is not None and lng is not None:
                waypoints_list.append({"lat": lat, "lng": lng})
        trip_dict["waypoints"] = waypoints_list

        # Optionally skip “walk” sections, if you only want real transit, e.g.:
        # if trip_dict["type"] == "walk":
        #     continue

        trips.append({"trip": trip_dict, "path": path, "time": "plannedFuture"})

    return trips


def convert_google_response_to_trips(google_json):
    """
    Convert Google's v2:computeRoutes JSON response into the same array of 'trip' objects.
    """
    trips = []
    if "routes" not in google_json or not google_json["routes"]:
        return trips

    for route in google_json["routes"]:
        for leg in route["legs"]:
            steps = leg.get("steps", [])
            for step in steps:
                # If there's no transitDetails, it might be a walking or driving step.
                # Skip if you only want actual transit segments.
                transit_details = step.get("transitDetails")
                if not transit_details:
                    continue

                polyline_str = step.get("polyline").get("encodedPolyline")
                path = []
                distance = 0
                if polyline_str:
                    decoded = polyline.decode(polyline_str)
                    path = [[lat, lon] for (lat, lon, *_) in decoded]
                    dist_list = getDistanceFromPath(path)
                    distance = dist_list[-1] if dist_list else 0

                stop_details = transit_details.get("stopDetails", {})
                departure_time_str = stop_details.get("departureTime")
                arrival_time_str = stop_details.get("arrivalTime")

                (start_local, start_utc, start_dt) = parse_api_time(departure_time_str)
                (end_local, end_utc, end_dt) = parse_api_time(arrival_time_str)
                trip_duration = int((end_dt - start_dt).total_seconds())

                # Operator / line info
                transit_line = transit_details.get("transitLine", {})
                agencies = transit_line.get("agencies", [])
                operator_name_api = agencies[0].get("name", "") if agencies else ""
                line_name = transit_line.get("nameShort", "")
                vehicle = transit_line.get("vehicle", {})
                type = vehicle.get("type", "")  # e.g. "BUS", "FERRY", "TRAIN"

                if type in (
                    "HEAVY_RAIL",
                    "COMMUTER_TRAIN",
                    "FUNICULAR",
                    "HIGH_SPEED_TRAIN",
                    "LONG_DISTANCE_TRAIN",
                ):
                    mode = "train"
                elif type in ("METRO_RAIL", "SUBWAY"):
                    mode = "metro"
                elif type in ("MONORAIL", "TRAM"):
                    mode = "tram"
                elif type in ("BUS", "INTERCITY_BUS", "TROLLEYBUS", "SHARE_TAXI"):
                    mode = "bus"
                elif type in ("CABLE_CAR", "GONDOLA_LIFT"):
                    mode = "aerialway"
                elif type in ("FERRY"):
                    mode = "ferry"
                else:
                    mode = "car"
                # Build approximate path from departure coords to arrival coords
                dep_stop = stop_details.get("departureStop", {})
                arr_stop = stop_details.get("arrivalStop", {})
                dep_loc = dep_stop.get("location", {}).get("latLng", {})
                arr_loc = arr_stop.get("location", {}).get("latLng", {})

                origin_coords = [dep_loc.get("latitude"), dep_loc.get("longitude")]
                destination_coords = [arr_loc.get("latitude"), arr_loc.get("longitude")]
                if not path:
                    path = []
                    distance = 0
                    if all(origin_coords) and all(destination_coords):
                        # If we have valid lat/lon, approximate path
                        path = [origin_coords, destination_coords]
                        dist_list = getDistanceFromPath(path)
                        distance = dist_list[-1] if dist_list else 0

                # Station flags
                origin_country = ""
                destination_country = ""
                if path:
                    origin_country = getCountryFromCoordinates(*origin_coords).get(
                        "countryCode", ""
                    )
                    destination_country = getCountryFromCoordinates(
                        *destination_coords
                    ).get("countryCode", "")
                origin_flag_emoji = get_flag_emoji(origin_country)
                destination_flag_emoji = get_flag_emoji(destination_country)

                origin_station_name = dep_stop.get(
                    "name", google_json.get("origin_name", "")
                )
                destination_station_name = arr_stop.get(
                    "name", google_json.get("destination_name", "")
                )

                origin_station = f"{origin_flag_emoji} {origin_station_name}"
                destination_station = (
                    f"{destination_flag_emoji} {destination_station_name}"
                )

                # Operator DB info
                operator_dict = build_operator_info(operator_name_api, start_utc)

                # Build final trip dictionary
                trip_dict = build_trip_dict(
                    section_type="",  # Google doesn't provide a direct "sectionType" like HERE
                    mode=mode,
                    line_name=line_name,
                    path=path,
                    start_local=start_local,
                    start_utc=start_utc,
                    end_local=end_local,
                    end_utc=end_utc,
                    operator_dict=operator_dict,
                    origin_station=origin_station,
                    destination_station=destination_station,
                    distance=distance,
                    trip_duration=trip_duration,
                )

                trip_dict["waypoints"] = []  # or populate if needed

                trips.append({"trip": trip_dict, "path": path, "time": "plannedFuture"})

    return trips
