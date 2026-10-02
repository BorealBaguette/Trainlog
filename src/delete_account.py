import logging

from src.pg import pg_session
from src.sql.trips import delete_user_trips_query
from src.users import Friendship, User, authDb

logger = logging.getLogger(__name__)


def delete_account_data(user: User) -> None:
    """Permanently erase every row owned by this account, in both the Postgres
    app database and the SQLite auth database, then delete the account itself.

    Most PG tables only carry a soft user_id/username reference (users live in
    the SQLite auth DB, so no cross-database FK/cascade is possible — see
    migration comments in src/sql/migrations/0047 and 0048). trips' and plans'
    own cascades already take care of their dependents (trip_operators,
    trip_announcements -> trip_announcement_posts, freehand_backup,
    live_flight_tracks, plan_trips, plan_costs), so those aren't repeated here.
    """
    user_id = user.uid
    username = user.username

    with pg_session() as pg:
        trip_ids = [
            row["uid"]
            for row in pg.execute(
                "SELECT trip_id AS uid FROM trips WHERE user_id = :user_id",
                {"user_id": user_id},
            ).fetchall()
        ]
        tag_ids = [
            row["uid"]
            for row in pg.execute(
                "SELECT uid FROM tags WHERE username = :username",
                {"username": username},
            ).fetchall()
        ]

        if trip_ids or tag_ids:
            pg.execute(
                """
                DELETE FROM tags_associations
                WHERE trip_id = ANY(:trip_ids) OR tag_id = ANY(:tag_ids)
                """,
                {
                    "trip_ids": [int(i) for i in trip_ids],
                    "tag_ids": [int(i) for i in tag_ids],
                },
            )
        if tag_ids:
            pg.execute(
                "DELETE FROM tag_members WHERE tag_id = ANY(:tag_ids)",
                {"tag_ids": [int(i) for i in tag_ids]},
            )
        pg.execute(
            "DELETE FROM tag_members WHERE username = :username",
            {"username": username},
        )
        pg.execute("DELETE FROM tags WHERE username = :username", {"username": username})

        if trip_ids:
            pg.execute(
                "DELETE FROM paths WHERE trip_id = ANY(:trip_ids)",
                {"trip_ids": [int(i) for i in trip_ids]},
            )
        pg.execute(delete_user_trips_query(), {"user_id": user_id})

        pg.execute("DELETE FROM tickets WHERE username = :username", {"username": username})
        pg.execute("DELETE FROM gpx WHERE username = :username", {"username": username})
        pg.execute("DELETE FROM plans WHERE user_id = :user_id", {"user_id": user_id})
        pg.execute(
            "DELETE FROM trainsets WHERE username = :username", {"username": username}
        )
        pg.execute(
            "DELETE FROM news_visits WHERE username = :username", {"username": username}
        )
        pg.execute(
            "DELETE FROM news_reactions WHERE username = :username",
            {"username": username},
        )
        pg.execute(
            "DELETE FROM news_views WHERE username = :username", {"username": username}
        )
        pg.execute(
            "DELETE FROM user_discord_webhooks WHERE user_id = :user_id",
            {"user_id": user_id},
        )

    Friendship.query.filter(
        (Friendship.user_id == user_id) | (Friendship.friend_id == user_id)
    ).delete(synchronize_session=False)
    authDb.session.delete(user)
    authDb.session.commit()

    logger.info(f"Deleted account and all data for user {username!r} (uid={user_id})")
