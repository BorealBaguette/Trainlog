import json
import logging

from src.pg import pg_session
from src.sql import leaderboards as lb_sql

logger = logging.getLogger(__name__)


def _getLeaderboardUsers(type, User):
    # Filter users with "leaderboard" set to True
    leaderboard_users = User.query.filter_by(leaderboard=True).all()
    user_list = [user.uid for user in leaderboard_users]
    # The frontend (generateLink) compares against usernames, so this must be a list
    # of usernames — not uids, which never matched and made every name clickable.
    non_public_users = [
        user.username for user in leaderboard_users if not user.is_public()
    ]

    if type == "carbon":
        # Create a dictionary of leaderboard users with default values
        user_dict = {
            user.uid: {
                "username": user.username,
                "total_carbon": 0,
                "carbon_per_km": 0,
                "total_distance": 0,
                "trips": 0,
                "last_modified": None,
            }
            for user in leaderboard_users
        }

        # Update the users with carbon data from the trips table
        with pg_session() as pg:
            result = pg.execute(
                lb_sql.carbon_leaderboard(), {"user_ids": user_list}
            ).fetchall()

            for row in result:
                user_id = row[0]
                total_carbon = row[1]
                total_distance = row[2]
                trips = row[3]
                last_modified = row[4].replace(day=1).date() if row[4] else None

                if user_id in user_dict:
                    user_dict[user_id]["total_carbon"] = (
                        float(total_carbon) if total_carbon else 0
                    )
                    user_dict[user_id]["total_distance"] = (
                        float(total_distance) if total_distance else 0
                    )
                    user_dict[user_id]["trips"] = trips
                    if last_modified is not None:
                        user_dict[user_id]["last_modified"] = last_modified

                    # Calculate carbon per km (avoid division by zero)
                    if user_dict[user_id]["total_distance"] > 0:
                        user_dict[user_id]["carbon_per_km"] = user_dict[user_id][
                            "total_carbon"
                        ] / (user_dict[user_id]["total_distance"] / 1000)

        # Sort by total carbon (default view)
        leaderboard_data = sorted(
            user_dict.values(), key=lambda x: x["total_carbon"], reverse=True
        )

        return {
            "leaderboard_data": leaderboard_data,
            "non_public_users": non_public_users,
        }

    if type not in ("train_countries", "world_squares", "country_count", "carbon"):
        # Create a dictionary of leaderboard users with minimal data
        user_dict = {
            user.uid: {
                "username": user.username,
                "trips": 0,
                "length": 0,
                "last_modified": None,
            }
            for user in leaderboard_users
        }

        # Update the users with data from the leaderboard stats query
        with pg_session() as pg:
            result = pg.execute(lb_sql.leaderboard_stats()).fetchall()
            for row in result:
                user_id = row[0]
                trip_type = row[1]
                trips = row[2]
                length = row[3]
                last_modified = row[4].replace(day=1).date() if row[4] else None

                if user_id in user_dict and type == trip_type:
                    user_dict[user_id]["trips"] = trips
                    user_dict[user_id]["length"] = length
                    if last_modified is not None:
                        user_dict[user_id]["last_modified"] = last_modified

        return {
            "leaderboard_data": list(user_dict.values()),
            "non_public_users": non_public_users,
        }

    elif type == "country_count":
        # Create a dictionary of leaderboard users with minimal data
        user_dict = {
            user.uid: {
                "username": user.username,
                "country_count": 0,
                "countries_visited": {},
            }
            for user in leaderboard_users
        }
        # Per user, per country: [km on the ground, km flown]. Flights split
        # their distance between the departure and arrival countries, so they
        # are kept apart and only used to break ties.
        country_km = {uid: {} for uid in user_dict}

        # Update the users with data from the trips table
        with pg_session() as pg:
            result = pg.execute(
                lb_sql.countries_leaderboard(), {"user_ids": user_list}
            ).fetchall()

            for row in result:
                user_id = row[0]
                countries = row[1]
                trip_type = row[2]

                if user_id in user_dict and countries:
                    trip_countries = (
                        json.loads(countries)
                        if isinstance(countries, str)
                        else countries
                    )
                    slot = 1 if trip_type in ("air", "helicopter") else 0

                    # Process countries data - handle both formats
                    for country_code, value in trip_countries.items():
                        if country_code == "UN":
                            continue
                        km = sum(value.values()) if isinstance(value, dict) else value
                        totals = country_km[user_id].setdefault(country_code, [0, 0])
                        totals[slot] += km or 0

        for user_id, user in user_dict.items():
            user["countries_visited"] = [
                country
                for country, km in sorted(
                    # Drop countries with no distance, as the old Counter sum did
                    (
                        (cc, km)
                        for cc, km in country_km[user_id].items()
                        if km[0] + km[1] > 0
                    ),
                    key=lambda x: (x[1][0], x[1][1]),
                    reverse=True,
                )
            ]
            user["country_count"] = len(user["countries_visited"])

        leaderboard_data = sorted(
            user_dict.values(), key=lambda x: x["country_count"], reverse=True
        )
        return {
            "leaderboard_data": leaderboard_data,
            "non_public_users": non_public_users,
        }
