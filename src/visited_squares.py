"""The 1°×1° squares of the world a user's trips went through, for the world squares map."""

from src.pg import pg_session
from src.sql.percents import upsert_percent_query
from src.utils import get_user_id


def _square_polygon(lat, lon):
    return {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon + 1, lat], [lon + 1, lat + 1], [lon, lat + 1], [lon, lat]]],
    }


# One row per trip and square its path vertices fall in, oldest trips first so a
# square keeps the year it was first reached. Snapping to square centres merges each
# run of vertices inside one square; the origin sits just under .5 so a vertex on a
# whole degree lands in the square FLOOR gives it, and the ends are taken unsnapped
# because a path inside a single square snaps to nothing.
SQUARES_QUERY = """
    WITH t AS (
        SELECT trip_id, trip_type,
               EXTRACT(YEAR FROM COALESCE(start_datetime, utc_start_datetime))::int AS year,
               ROW_NUMBER() OVER (
                   ORDER BY COALESCE(utc_start_datetime, start_datetime) NULLS LAST, trip_id
               ) AS ord
        FROM trips
        WHERE user_id = :user_id
          AND NOT is_project
          AND (
                COALESCE(utc_start_datetime, start_datetime) IS NULL
                OR NOW() > COALESCE(utc_start_datetime, start_datetime)
              )
    )
    SELECT t.ord, t.trip_type AS type, t.year,
           FLOOR(ST_Y(s.point))::int AS lat, FLOOR(ST_X(s.point))::int AS lon,
           BOOL_OR(s.stopped) AS stopped
    FROM t
    JOIN paths p USING (trip_id)
    CROSS JOIN LATERAL (
        SELECT (ST_DumpPoints(ST_SnapToGrid(
                   CASE
                       WHEN t.trip_type IN ('air', 'helicopter') AND ST_NPoints(p.geom) > 2
                       THEN ST_Segmentize(p.geom::geography, 50000)::geometry
                       ELSE p.geom
                   END,
                   0.4999999, 0.4999999, 1, 1
               ))).geom AS point, FALSE AS stopped
        UNION ALL SELECT COALESCE(ST_StartPoint(p.geom), p.geom), TRUE
        UNION ALL SELECT COALESCE(ST_EndPoint(p.geom), p.geom), TRUE
    ) s
    GROUP BY t.ord, t.trip_type, t.year, 4, 5
    ORDER BY t.ord
"""


def generate_visited_squares_geojson(username):
    visited_squares = {}
    first_year = {}

    with pg_session() as pg:
        rows = pg.execute(SQUARES_QUERY, {"user_id": get_user_id(username)}).fetchall()

        for row in rows:
            square = (row["lat"], row["lon"])
            if row["stopped"]:
                visited_squares[square] = "stopped"
            elif row["type"] in ("air", "helicopter"):
                visited_squares.setdefault(square, "air")
            elif visited_squares.get(square) != "stopped":
                visited_squares[square] = "passed"
            first_year.setdefault(square, row["year"])

        air_count = sum(1 for status in visited_squares.values() if status == "air")
        total_squares = 180 * 360  # entire world grid
        land_percentage = ((len(visited_squares) - air_count) / total_squares) * 100
        air_percentage = (air_count / total_squares) * 100

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

    return visited_geojson, land_percentage, air_percentage
