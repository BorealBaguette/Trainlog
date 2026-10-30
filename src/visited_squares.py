"""The 1°×1° squares of the world a user's trips went through, for the world squares map."""

import math

from py.utils import interpolate_great_circle
from src.paths import fetch_path
from src.pg import pg_session
from src.sql.percents import upsert_percent_query
from src.utils import get_user_id


def _square_polygon(lat, lon):
    return {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon + 1, lat], [lon + 1, lat + 1], [lon, lat + 1], [lon, lat]]],
    }


def generate_visited_squares_geojson(username):
    land_squares = set()
    air_squares = set()
    visited_squares = {}
    # Oldest trips first, so a square keeps the year it was first reached
    first_year = {}

    with pg_session() as pg:
        trips = pg.execute(
            """
                SELECT trip_id AS uid, trip_type AS type,
                       EXTRACT(YEAR FROM COALESCE(start_datetime, utc_start_datetime))::int AS year
                FROM trips
                WHERE user_id = :user_id
                  AND NOT is_project
                  AND (
                        COALESCE(utc_start_datetime, start_datetime) IS NULL
                        OR NOW() > COALESCE(utc_start_datetime, start_datetime)
                      )
                ORDER BY COALESCE(utc_start_datetime, start_datetime) NULLS LAST
            """,
            {"user_id": get_user_id(username)},
        ).fetchall()

        for trip in trips:
            trip_id = trip["uid"]
            trip_type = trip["type"]

            def mark(square, status):
                visited_squares[square] = status
                first_year.setdefault(square, trip["year"])

            paths = [fetch_path(pg, trip_id)]

            for coordinates in paths:
                for i in range(len(coordinates)):
                    lat, lon = coordinates[i]
                    square = (math.floor(lat), math.floor(lon))

                    if trip_type in ("air", "helicopter"):
                        if (
                            visited_squares.get(square) not in ("stopped", "passed")
                            and square not in land_squares
                        ):
                            mark(square, "air")
                            air_squares.add(square)
                    else:
                        if visited_squares.get(square) != "stopped":
                            mark(square, "passed")
                        land_squares.add(square)
                        air_squares.discard(square)

                    # Override with "stopped" on first/last point
                    if i == 0 or i == len(coordinates) - 1:
                        mark(square, "stopped")
                        land_squares.add(square)
                        air_squares.discard(square)

                    # Interpolate between points (only for air trips)
                    if (
                        trip_type in ("air", "helicopter")
                        and len(coordinates) > 2
                        and i < len(coordinates) - 1
                    ):
                        next_lat, next_lon = coordinates[i + 1]
                        intermediates = interpolate_great_circle(
                            (lat, lon), (next_lat, next_lon), max_distance_km=50
                        )

                        for inter_lat, inter_lon in intermediates:
                            inter_square = (
                                math.floor(inter_lat),
                                math.floor(inter_lon),
                            )

                            if (
                                visited_squares.get(inter_square)
                                not in ("stopped", "passed")
                                and inter_square not in land_squares
                            ):
                                mark(inter_square, "air")
                                air_squares.add(inter_square)

    total_squares = 180 * 360  # entire world grid
    land_percentage = (len(land_squares) / total_squares) * 100
    air_percentage = (len(air_squares) / total_squares) * 100

    with pg_session() as pg:
        pg.execute(
            upsert_percent_query(),
            {
                "username": username,
                "cc": "world_squares",
                "percent": round(land_percentage, 2),
            },
        )

    visited_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": _square_polygon(lat, lon),
                "properties": {"status": status, "year": first_year[(lat, lon)]},
            }
            for (lat, lon), status in visited_squares.items()
        ],
    }

    grid_geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": _square_polygon(lat, lon), "properties": {}}
            for lat in range(-90, 90)
            for lon in range(-180, 180)
        ],
    }

    return visited_geojson, grid_geojson, land_percentage, air_percentage
