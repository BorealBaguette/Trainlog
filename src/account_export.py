"""Everything a GDPR-style "all my data" export needs beyond the trips CSV
(which already exists as /u/<username>/export and stays in app.py, since it
depends on adapt_pg_trip_row's legacy row shaping). This module covers every
other table keyed by user_id/username — see src/delete_account.py, which
deletes exactly the same set of rows.
"""

import csv
import io
import json

import polyline

from src.pg import pg_session
from src.users import Friendship, User


def _csv_text(rows: list[dict]) -> str:
    """Rows to CSV text, columns taken from the first row (empty file if none)."""
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _rows(pg, query: str, params: dict) -> list[dict]:
    return [dict(row._mapping) for row in pg.execute(query, params).fetchall()]


def _encode_geojson_path(geojson_text: str | None) -> str:
    """A geometry's dumped [[lat,lng],...] JSON to the same encoded polyline
    string trips.csv already uses for its `path` column — compact and
    consistent, rather than a verbose WKT/GeoJSON dump."""
    if not geojson_text:
        return ""
    return polyline.encode(json.loads(geojson_text))


def build_account_data_csvs(user: User) -> dict[str, str]:
    """Every table a self-service export/delete touches, other than trips/paths
    (see /u/<username>/export for those). Returns {filename: csv_text}."""
    user_id = user.uid
    username = user.username
    files: dict[str, str] = {}

    with pg_session() as pg:
        tag_ids = [
            row["uid"]
            for row in pg.execute(
                "SELECT uid FROM tags WHERE username = :username",
                {"username": username},
            ).fetchall()
        ]
        trip_ids = [
            row["uid"]
            for row in pg.execute(
                "SELECT trip_id AS uid FROM trips WHERE user_id = :user_id",
                {"user_id": user_id},
            ).fetchall()
        ]

        files["plans.csv"] = _csv_text(
            _rows(
                pg,
                """
                SELECT uid, uuid, name, description, anchor_date, archived,
                       visibility, validated_tag_uuid, created, last_modified
                FROM plans WHERE user_id = :user_id
                """,
                {"user_id": user_id},
            )
        )
        plan_trip_rows = _rows(
            pg,
            """
            SELECT uid, plan_id, sort_order, timing_mode, start_day, end_day,
                   start_time, end_time, start_datetime, end_datetime,
                   utc_start_datetime, utc_end_datetime, estimated_trip_duration,
                   manual_trip_duration, origin_station, destination_station,
                   trip_type, operator, line_name, material_type,
                   material_type_advanced, reg, seat, notes, trip_length,
                   countries, price, currency, purchase_date, visibility,
                   carbon, power_type, co2_override, validated_trip_id,
                   created, last_modified,
                   (
                       SELECT json_agg(json_build_array(ST_Y(dp.geom), ST_X(dp.geom)) ORDER BY dp.path)
                       FROM ST_DumpPoints(plan_trips.geom) AS dp
                   )::text AS path
            FROM plan_trips WHERE user_id = :user_id
            """,
            {"user_id": user_id},
        )
        for row in plan_trip_rows:
            row["path"] = _encode_geojson_path(row.get("path"))
        files["plan_trips.csv"] = _csv_text(plan_trip_rows)
        files["plan_costs.csv"] = _csv_text(
            _rows(
                pg,
                """
                SELECT pc.uid, pc.plan_id, pc.name, pc.price, pc.currency, pc.notes,
                       pc.ticket_id, pc.created, pc.last_modified
                FROM plan_costs pc
                JOIN plans p ON p.uid = pc.plan_id
                WHERE p.user_id = :user_id
                """,
                {"user_id": user_id},
            )
        )
        files["tags.csv"] = _csv_text(
            _rows(
                pg,
                "SELECT uid, uuid, name, colour, type FROM tags WHERE username = :username",
                {"username": username},
            )
        )
        files["tag_members.csv"] = _csv_text(
            _rows(
                pg,
                """
                SELECT tag_id, username, status, invited_at, responded_at
                FROM tag_members
                WHERE tag_id = ANY(:tag_ids) OR username = :username
                """,
                {"tag_ids": [int(i) for i in tag_ids], "username": username},
            )
        )
        files["tags_associations.csv"] = _csv_text(
            _rows(
                pg,
                """
                SELECT tag_id, trip_id FROM tags_associations
                WHERE tag_id = ANY(:tag_ids) OR trip_id = ANY(:trip_ids)
                """,
                {
                    "tag_ids": [int(i) for i in tag_ids],
                    "trip_ids": [int(i) for i in trip_ids],
                },
            )
        )
        files["tickets.csv"] = _csv_text(
            _rows(
                pg,
                "SELECT * FROM tickets WHERE username = :username",
                {"username": username},
            )
        )
        gpx_rows = _rows(pg, "SELECT * FROM gpx WHERE username = :username", {"username": username})
        for row in gpx_rows:
            row["path"] = _encode_geojson_path(row.get("path"))
        files["gpx.csv"] = _csv_text(gpx_rows)

        files["trainsets.csv"] = _csv_text(
            _rows(
                pg,
                "SELECT id, name, is_admin, units_json, created_at, updated_at "
                "FROM trainsets WHERE username = :username",
                {"username": username},
            )
        )
        files["discord_webhooks.csv"] = _csv_text(
            _rows(
                pg,
                "SELECT id, label, url, enabled, autopost "
                "FROM user_discord_webhooks WHERE user_id = :user_id",
                {"user_id": user_id},
            )
        )

    friendships = Friendship.query.filter(
        (Friendship.user_id == user_id) | (Friendship.friend_id == user_id)
    ).all()
    friend_rows = []
    for f in friendships:
        other_id = f.friend_id if f.user_id == user_id else f.user_id
        other = User.query.get(other_id)
        friend_rows.append(
            {
                "friend_username": other.username if other else f"(deleted user {other_id})",
                "direction": "sent" if f.user_id == user_id else "received",
                "status": "accepted" if f.accepted else "pending",
                "created_at": f.created_at,
                "accepted_at": f.accepted,
            }
        )
    files["friends.csv"] = _csv_text(friend_rows)

    # Excludes pass_hash and the raw secret tokens (gps_token, mcp_token,
    # reset_token, email_verify_token) — an export file can end up copied
    # somewhere less secure than the account itself, so live credentials
    # aren't handed out through it; whether each is set is still shown.
    files["account.csv"] = _csv_text(
        [
            {
                "username": user.username,
                "email": user.email,
                "pending_email": user.pending_email,
                "lang": user.lang,
                "share_level": user.share_level,
                "leaderboard": user.leaderboard,
                "friend_search": user.friend_search,
                "appear_on_global": user.appear_on_global,
                "colorblind": user.colorblind,
                "user_currency": user.user_currency,
                "default_landing": user.default_landing,
                "tileserver": user.tileserver,
                "globe": user.globe,
                "creation_date": user.creation_date,
                "last_login": user.last_login,
                "admin": user.admin,
                "alpha": user.alpha,
                "translator": user.translator,
                "feature_admin": user.feature_admin,
                "premium": user.premium,
                "premium_tier": user.premium_tier,
                "flight_3d": user.flight_3d,
                "live_tracking": user.live_tracking,
                "discord_id": user.discord_id,
                "discord_username": user.discord_username,
                "discord_autopost": user.discord_autopost,
                "discord_main_enabled": user.discord_main_enabled,
                "has_gps_token": bool(user.gps_token),
                "has_mcp_token": bool(user.mcp_token),
            }
        ]
    )

    return files
