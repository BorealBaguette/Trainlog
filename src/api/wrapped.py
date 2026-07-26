import logging
from flask import Blueprint, render_template, session
from datetime import datetime

from src.pg import pg_session
from src.sql import wrapped as wrapped_sql
from src.utils import lang, get_user_id, login_required
from src.api.stats import fetch_stats, get_distinct_stat_years

logger = logging.getLogger(__name__)

wrapped_blueprint = Blueprint("wrapped", __name__)

DAY_NAMES = {
    0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
    4: "Thursday", 5: "Friday", 6: "Saturday"
}

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

# Distance comparisons in km.
# `key` maps to a lang JSON key so names can be translated.
DISTANCE_COMPARISONS = [
    {"key": "distComp_english_channel",    "km": 34,     "icon": "fa-solid fa-person-swimming"},
    {"key": "distComp_marathon",           "km": 42.195, "icon": "fa-solid fa-person-running"},
    {"key": "distComp_iss_altitude",       "km": 400,    "icon": "fa-solid fa-satellite"},
    {"key": "distComp_proclaimers",        "km": 804.6,  "icon": "fa-solid fa-shoe-prints"},
    {"key": "distComp_mordor",             "km": 2863,   "icon": "fa-solid fa-ring"},
    {"key": "distComp_moon_circumference", "km": 10921,  "icon": "fa-solid fa-moon"},
    {"key": "distComp_longest_flight",     "km": 15349,  "icon": "fa-solid fa-plane"},
    {"key": "distComp_earth_circumference","km": 40075,  "icon": "fa-solid fa-earth-europe"},
    {"key": "distComp_earth_moon",         "km": 384400, "icon": "fa-solid fa-rocket"},
]

# Duration comparisons in hours
DURATION_COMPARISONS = [
    {"key": "durComp_longest_tennis",   "h": 11,    "icon": "fa-solid fa-baseball"},
    {"key": "durComp_lotr_marathon",    "h": 12,    "icon": "fa-solid fa-film"},
    {"key": "durComp_lindbergh_flight", "h": 33.5,  "icon": "fa-solid fa-plane-departure"},
    {"key": "durComp_orient_express",   "h": 68,    "icon": "fa-solid fa-train"},
    {"key": "durComp_apollo11",         "h": 195,   "icon": "fa-solid fa-user-astronaut"},
    {"key": "durComp_lunar_orbit",      "h": 655,   "icon": "fa-solid fa-meteor"},
    {"key": "durComp_80_days",          "h": 1920,  "icon": "fa-solid fa-hot-air-balloon"},
    {"key": "durComp_bear_hibernation", "h": 3600,  "icon": "fa-solid fa-paw"},
]


def get_wrapped_data(username, year, trip_type="combined"):
    """Fetch all data needed for the wrapped page."""
    user_id = get_user_id(username)
    prev_year = str(int(year) - 1)
    
    wrapped = {
        "year": year,
        "username": username,
        "trip_type": trip_type,
    }
    
    with pg_session() as pg:
        # Get totals for the year
        totals = pg.execute(
            wrapped_sql.totals(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        if totals:
            wrapped["total_trips"] = int(totals.total_trips or 0)
            wrapped["total_km"] = int((totals.total_km or 0) / 1000)
            wrapped["total_duration"] = int(totals.total_duration or 0)
            wrapped["total_co2"] = int(totals.total_co2 or 0)
        else:
            wrapped["total_trips"] = 0
            wrapped["total_km"] = 0
            wrapped["total_duration"] = 0
            wrapped["total_co2"] = 0
        
        # Get previous year for comparison
        prev_totals = pg.execute(
            wrapped_sql.previous_year_totals(),
            {"user_id": user_id, "tripType": trip_type, "prev_year": prev_year}
        ).fetchone()
        
        if prev_totals and prev_totals.total_trips > 0:
            wrapped["prev_trips"] = int(prev_totals.total_trips)
            wrapped["prev_km"] = int((prev_totals.total_km or 0) / 1000)
            wrapped["trips_change"] = round(
                ((wrapped["total_trips"] - wrapped["prev_trips"]) / wrapped["prev_trips"]) * 100
            )
            wrapped["km_change"] = round(
                ((wrapped["total_km"] - wrapped["prev_km"]) / wrapped["prev_km"]) * 100
            ) if wrapped["prev_km"] > 0 else None
        else:
            wrapped["prev_trips"] = 0
            wrapped["prev_km"] = 0
            wrapped["trips_change"] = None
            wrapped["km_change"] = None
        
        # Get longest trip
        longest = pg.execute(
            wrapped_sql.longest_trip(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        if longest and longest.trip_length:
            wrapped["longest_trip"] = {
                "origin": longest.origin_station or "Unknown",
                "destination": longest.destination_station or "Unknown",
                "km": int(longest.trip_length / 1000),
                "duration": int(longest.trip_duration or 0),
            }
        else:
            wrapped["longest_trip"] = None
        
        # Get fastest trip
        fastest = pg.execute(
            wrapped_sql.fastest_trip(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        if fastest and fastest.avg_speed_kmh:
            wrapped["fastest_trip"] = {
                "origin": fastest.origin_station or "Unknown",
                "destination": fastest.destination_station or "Unknown",
                "km": int(fastest.trip_length / 1000),
                "speed": int(fastest.avg_speed_kmh),
            }
        else:
            wrapped["fastest_trip"] = None
        
        # Get busiest month
        busiest = pg.execute(
            wrapped_sql.monthly_breakdown(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        if busiest and busiest.month:
            wrapped["busiest_month"] = {
                "month": busiest.month,
                "trips": int(busiest.trips),
            }
        else:
            wrapped["busiest_month"] = None
        
        # Get favorite day of week
        day_result = pg.execute(
            wrapped_sql.day_of_week(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        if day_result and day_result.day_of_week is not None:
            wrapped["favorite_day"] = {
                "day": day_result.day_of_week,
                "trips": int(day_result.trips),
            }
        else:
            wrapped["favorite_day"] = None
        
        # Get time of day breakdown
        time_results = pg.execute(
            wrapped_sql.time_of_day(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchall()
        
        if time_results:
            time_breakdown = {row.time_category: int(row.trips) for row in time_results}
            total_time_trips = sum(time_breakdown.values())
            wrapped["time_of_day"] = {
                "breakdown": time_breakdown,
                "favorite": max(time_breakdown, key=time_breakdown.get) if time_breakdown else None,
                "favorite_percent": round((max(time_breakdown.values()) / total_time_trips) * 100) if total_time_trips > 0 else 0
            }
        else:
            wrapped["time_of_day"] = None
        
        # Get first and last trip
        first_last = pg.execute(
            wrapped_sql.first_last_trip(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchall()
        
        wrapped["first_trip"] = None
        wrapped["last_trip"] = None
        for row in first_last:
            trip_data = {
                "origin": row.origin_station or "Unknown",
                "destination": row.destination_station or "Unknown",
                "date": row.filtered_datetime,
            }
            if row.trip_type == "first":
                wrapped["first_trip"] = trip_data
            else:
                wrapped["last_trip"] = trip_data
        
        # Get unique stations count
        stations_result = pg.execute(
            wrapped_sql.unique_stations(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        wrapped["unique_stations"] = int(stations_result.unique_stations) if stations_result else 0
        
        # Get longest streak
        streak_result = pg.execute(
            wrapped_sql.streak(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        if streak_result and streak_result.streak_length > 1:
            wrapped["streak"] = {
                "days": int(streak_result.streak_length),
                "start": streak_result.streak_start,
                "end": streak_result.streak_end,
            }
        else:
            wrapped["streak"] = None
        
        # Get averages
        avg_result = pg.execute(
            wrapped_sql.averages(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        if avg_result:
            wrapped["avg_trip_km"] = int((avg_result.avg_trip_length or 0) / 1000)
            wrapped["avg_trip_duration"] = int(avg_result.avg_trip_duration or 0)
            wrapped["days_traveled"] = int(avg_result.days_traveled or 0)
        else:
            wrapped["avg_trip_km"] = 0
            wrapped["avg_trip_duration"] = 0
            wrapped["days_traveled"] = 0
        
        # Get percentile ranking
        percentile_result = pg.execute(
            wrapped_sql.percentile(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        print(percentile_result)
        if percentile_result and percentile_result.total_users > 1:
            km_percentile = float(percentile_result.km_percentile)
            trips_percentile = float(percentile_result.trips_percentile)

            wrapped["percentile"] = {
                "km": round(km_percentile, 2) if km_percentile > 98 else int(round(km_percentile)),
                "trips": round(trips_percentile, 2) if trips_percentile > 98 else int(round(trips_percentile)),
                "total_users": int(percentile_result.total_users),
            }
        else:
            wrapped["percentile"] = None
    
        # Get countries data directly
        countries_result = pg.execute(
            wrapped_sql.countries(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchall()
        
        wrapped["top_countries"] = []
        for row in countries_result[:5]:
            wrapped["top_countries"].append({
                "code": row.country_code,
                "km": int(row.total_km / 1000),
                "trips": int(row.trips)
            })
        wrapped["country_count"] = len(countries_result)
        
        # Get border crossings
        crossings_result = pg.execute(
            wrapped_sql.border_crossings(),
            {"user_id": user_id, "tripType": trip_type, "year": year}
        ).fetchone()
        
        if crossings_result and crossings_result.total_border_crossings:
            wrapped["border_crossings"] = int(crossings_result.total_border_crossings)
        else:
            wrapped["border_crossings"] = 0
    
    # Use existing fetch_stats for operators, routes, material
    stats = fetch_stats(username, trip_type, year)
    
    # Top 5 operators
    operators = stats.get("operators", [])
    wrapped["top_operators"] = [
        {"name": op["operator"], "trips": int(op.get("pastTrips", 0))}
        for op in operators[:5]
        if op.get("operator")
    ]
    
    # Top 3 routes
    routes = stats.get("routes", [])
    wrapped["top_routes"] = []
    for r in routes[:3]:
        try:
            import json
            route_parts = json.loads(r["route"])
            route_str = " → ".join(route_parts)
        except:
            route_str = r.get("route", "Unknown")
        wrapped["top_routes"].append({
            "name": route_str,
            "count": int(r.get("count", 0))
        })
    
    # Top 3 material/train types
    material = stats.get("material", [])
    wrapped["top_material"] = [
        {"name": m["material"], "trips": int(m.get("pastTrips", 0))}
        for m in material[:3]
        if m.get("material")
    ]
    
    # Fun calculations
    wrapped["duration_hours"] = round(wrapped["total_duration"] / 3600)
    wrapped["duration_days"] = round(wrapped["total_duration"] / 86400, 1)
    
    
    # Calculate what percent of the year they traveled
    if wrapped["days_traveled"] > 0:
        wrapped["year_percent"] = round((wrapped["total_duration"] / (365 * 86400)) * 100, 1)
    else:
        wrapped["year_percent"] = 0
    
    return wrapped


@wrapped_blueprint.route("/u/<username>/wrapped")
@wrapped_blueprint.route("/u/<username>/wrapped/<year>")
@login_required
def wrapped(username, year=None):
    """Render the wrapped page for a user."""
    if year is None:
        # "Wrapped" year: switches on Nov 26 (Dec → next year's wrapped)
        now = datetime.now()
        year = str(now.year if (now.month, now.day) >= (11, 26) else now.year - 1)
    
    # Check if year has data
    available_years = get_distinct_stat_years(username, "combined")

    
    data = get_wrapped_data(username, year, "combined")
    
    userinfo = session.get("userinfo", {})
    lang_dict = lang.get(userinfo.get("lang", "en"), {})
    
    return render_template(
        "wrapped.html",
        data=data,
        available_years=available_years,
        **lang_dict,
        **userinfo,
    )