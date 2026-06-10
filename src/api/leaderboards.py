import logging
import json
from collections import Counter
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
        user_dict = {user.uid: {
            "username": user.username,
            "total_carbon": 0,
            "carbon_per_km": 0,
            "total_distance": 0,
            "trips": 0,
            "last_modified": None
        } for user in leaderboard_users}
        
        # Update the users with carbon data from the trips table
        with pg_session() as pg:
            result = pg.execute(
                lb_sql.carbon_leaderboard(),
                {"user_ids": user_list}
            ).fetchall()
            
            for row in result:
                user_id = row[0]
                total_carbon = row[1]
                total_distance = row[2]
                trips = row[3]
                last_modified = row[4].replace(day=1).date() if row[4] else None
                
                if user_id in user_dict:
                    user_dict[user_id]["total_carbon"] = float(total_carbon) if total_carbon else 0
                    user_dict[user_id]["total_distance"] = float(total_distance) if total_distance else 0
                    user_dict[user_id]["trips"] = trips
                    if last_modified is not None:
                        user_dict[user_id]["last_modified"] = last_modified
                    
                    # Calculate carbon per km (avoid division by zero)
                    if user_dict[user_id]["total_distance"] > 0:
                        user_dict[user_id]["carbon_per_km"] = (
                            user_dict[user_id]["total_carbon"] / (user_dict[user_id]["total_distance"] / 1000)
                        )
        
        # Sort by total carbon (default view)
        leaderboard_data = sorted(
            user_dict.values(), 
            key=lambda x: x["total_carbon"], 
            reverse=True
        )
        
        return {
            "leaderboard_data": leaderboard_data,
            "non_public_users": non_public_users,
        }
    
    if type not in ("train_countries", "world_squares", "country_count", "carbon"):
        # Create a dictionary of leaderboard users with minimal data
        user_dict = {user.uid: {
            "username": user.username,
            "trips": 0,
            "length": 0,
            "last_modified": None
        } for user in leaderboard_users}
        
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
        user_dict = {user.uid: {
            "username": user.username,
            "country_count": 0,
            "countries_visited": {}
        } for user in leaderboard_users}
        
        # Update the users with data from the trips table
        with pg_session() as pg:
            result = pg.execute(
                lb_sql.countries_leaderboard(),
                {"user_ids": user_list}
            ).fetchall()
           
            for row in result:
                user_id = row[0]
                countries = row[1]
               
                if user_id in user_dict:
                    trip_countries = json.loads(countries) if isinstance(countries, str) else countries
                    
                    # Process countries data - handle both formats
                    normalized_countries = {}
                    for country_code, value in trip_countries.items():
                        if isinstance(value, dict):
                            total_trips = sum(value.values())
                            normalized_countries[country_code] = total_trips
                        else:
                            normalized_countries[country_code] = value
                    
                    user_dict[user_id]["countries_visited"] = dict(
                        Counter(user_dict[user_id]["countries_visited"])
                        + Counter(normalized_countries)
                    )
                    user_dict[user_id]["countries_visited"].pop("UN", None)
        
        for user in user_dict.values():
            user["countries_visited"] = [
                country
                for country, trips in sorted(
                    user["countries_visited"].items(), key=lambda x: x[1], reverse=True
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
       
        leaderboard_data = []
        for country, percentages in countries_dict.items():
            users_percents = []
            for percent, users in percentages.items():
                users_percents.append({"percent": percent, "user_ids": users})
            leaderboard_data.append({"cc": country, "data": users_percents})
        return {
            "leaderboard_data": leaderboard_data,
            "non_public_users": non_public_users
        }