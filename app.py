# To not automatically lint the file:
# ruff: noqa

# Standard Library Imports
import calendar
import csv
import logging.config
import os
import pathlib
import re
import secrets
import traceback
import urllib
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, UTC
from decimal import Decimal
from glob import glob
from io import BytesIO, StringIO
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

import distinctipy
import geojson
import git
import gpxpy

# Third-Party Imports
import polyline
import pytz
import requests
import sqlalchemy
from flag import flag
from flask import (
    Flask,
    Markup,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    render_template_string,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
    g
)
from flask_caching import Cache
from flask_compress import Compress
from flaskext.autoversion import Autoversion
from PIL import Image
from requests.adapters import HTTPAdapter, Retry
from scgraph.geographs.marnet import marnet_geograph
from sqlalchemy import and_, case, func, or_
from sqlalchemy_utils import database_exists
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import HTTPException, MethodNotAllowed, NotFound
from werkzeug.routing import RequestRedirect
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import sqlite3
from urllib.parse import unquote

# Set the working directory to the app root
# this must be before we try to read the config, or any file
appPath = os.path.realpath(__file__).rsplit("/", 1)[0]
os.chdir(appPath)

# set up logging before local modules are imported
os.makedirs("logs", exist_ok=True)  # logging.conf's bmcFileHandler writes here
logging.config.fileConfig("logging.conf", disable_existing_loggers=False)
logger = logging.getLogger(__name__)


# Local Application/Library Specific Imports
from py import geopip_country
from py.coverage import (
    get_coverage_file_path,
    get_coverage_geojson_dict,
    has_coverage_file,
)
from src.currency import get_available_currencies, get_exchange_rate
from scripts.backfill_vessels import apply_plan as backfill_apply_plan
from scripts.backfill_vessels import build_plan as backfill_build_plan
from src.g_search import (
    fetch_commons_picture,
    fetch_picture_for_registration,
    find_vessel_ids,
    get_vessel_picture,
    search_commons_images,
)
from src.wikidata import search_ships as wikidata_search_ships
from py.image_generator import generate_image
from src.sql.leaderboards import get_leaderboard_countries_query
from src.sql.percents import upsert_percent_query
from src.sql.stations import (
    get_airports_query,
    get_manual_stations_query,
    get_train_stations_query,
)
from src.sql.tags import get_tags_query
from src.sql.tickets import get_ticket_query, get_tickets_query
from src.sql.trips import (
    delete_user_trips_query,
    get_duplicate_query,
    get_dynamic_user_trips_query,
    get_material_types_query,
    get_number_stations_query,
    get_operators_query,
    get_trip_query,
    get_trips_by_ids_query,
    get_trips_country_query,
    get_unique_user_trips_query,
    get_updated_user_trips_query,
    get_user_lines_query,
    get_user_trips_query,
)
from py.motis import (
    handle_search_form,
    handle_search_params,
)
from py.svg import generate_sprite
from py.track import CustomMatomo
from src.transit_routing import (
    convert_google_response_to_trips,
    convert_here_response_to_trips,
)
from py.gps_cleaner import clean_gps_route
from src.update_currency import run_currency_update
from py.utils import (
    get_all_countries,
    get_flag_emoji,
    getCountriesFromPath,
    getCountryFromCoordinates,
    getDistance,
    getDistanceFromPath,
    getIp,
    getIpDetails,
    getRequestData,
    hex_to_rgb,
    interpolate_great_circle,
    interpolate_points_if_gaps,
    interpolate_track_if_gaps,
    load_config,
    remove_diacritics,
    rgb_to_hex,
    stringSimmilarity,
    unicodedata,
    validate_png_file,
    time_ago
)
from src.api.admin import (
    admin_blueprint,
    operators_api_blueprint,
    trainsets_admin_blueprint,
    wagons_admin_blueprint,
)
from src.api.feature_requests import feature_requests_blueprint
from src.api.vagonweb import vagonweb_blueprint
from src.api.leaderboards import _getLeaderboardUsers
from src.api.news import news_blueprint
from src.api.finance import finance_blueprint
from src.api.bmc import bmc_blueprint, reconcile_pending_events
from src.api.discord_oauth import discord_oauth_blueprint
from src.discord_bot import sync_discord_tier
from src.api.carbon import carbon_blueprint
from src.api.wrapped import wrapped_blueprint, DISTANCE_COMPARISONS, DURATION_COMPARISONS
from src.api.stats import stats_blueprint, fetch_stats, get_distinct_stat_years
from src.api.ai import ai_blueprint
from src.api.mcp import blueprint as mcp_blueprint
from src.api.trainset import public_trainset_info, trainset_blueprint
from src.api.dashboard import dashboard_blueprint
from src.api.timeline import timeline_blueprint
from src import visualisations as viz_module
from src.api.trips import trips_blueprint
from src.api.live_tracks import get_live_tracks, live_tracks_blueprint
from src.consts import DbNames, TripTypes
from src.global_map import (
    available_bins,
    build_all_async,
    build_status,
    get_cache_path,
)
from src.operators import find_operator_ids, get_trip_operator_logos
from src.pg import setup_db, pg_session
from src.search_terms import like_pattern
from src.trip_search import base_type_filter, filter_conditions, parse_filters
from src.suspicious_activity import (
    check_denied_login,
    log_denied_login,
    log_suspicious_activity,
)
from src.utils import (
    getNameFromPath,
    processDates,
    getUser,
    get_user_id,
    get_username,
    has_current_trip,
    lang,
    owner,
    owner_required,
    readLang,
    sendOwnerEmail,
    sendEmail,
    getLocalDatetime,
    getUtcDatetime,
    login_required,
    admin_required,
    public_required,
    translator_required,
    check_and_increment_fr24_usage,
    fr24_usage,
    get_default_trip_visibility,
    current_user_is_friend_with,
    external_url,
)
from src.trips import (
    Trip,
    create_trip,
    duplicate_trip,
    update_trip,
    delete_trip,
    update_trip_type,
    attach_ticket_to_trips,
    bulk_edit_trips,
    bulk_change_type,
    bulk_set_power_type,
    change_trips_visibility,
    delete_ticket_from_db,
    get_current_trip_id,
)
from src.paths import Path, coords_to_ewkt, fetch_path, geom_geojson_to_coords
from src.trips.freehand_transform import (
    apply_to_trip,
    purge_expired_backups,
    revert_trip,
)
from src.trips.split_trip import get_split_data, split_trip
from src.sql.plans import (
    insert_plan_query,
    get_plan_query,
    get_user_plans_query,
    update_plan_query,
    archive_plan_query,
    delete_plan_query,
    get_plan_trips_query,
    update_plan_trip_query,
    delete_plan_trip_query,
    reorder_plan_trip_query,
    insert_plan_cost_query,
    get_plan_costs_query,
    update_plan_cost_query,
    delete_plan_cost_query,
    set_plan_trip_cost_query,
)
from src.plans.process_plan_dates import RELATIVE_REF_DATE, process_plan_dates
from src.plans.plan_trip import PlanTrip
from src.plans.create_plan_trip import create_plan_trip
from src.plans.update_plan_trip_full import update_plan_trip_full
from src.plans.delete_plan import delete_plan, delete_plan_trip
from src.plans.duplicate_plan import duplicate_plan
from src.plans.validate_plan import validate_plan
from src.plans.import_trips import import_trips_to_plan
from src.carbon import *
from src.users import User, Friendship, authDb
from src.email_parser import start_email_listener
from src.photon import photonInstances, photonRequest, photonRequestSingle
from src.routing import forward_routing_core
from src.gpx_import import (
    GpxIngestError,
    build_trip_payload,
    clean_tasker_gpx_files,
    getAddressFromCoords,
    ingest_gpx_files,
    parse_gpx_files,
    parse_trip_params,
)
from src.error_reporter import report_error

app = Flask(__name__)
start_email_listener(app)

app.config['DEBUG'] = True
Compress(app)
app.autoversion = True
Autoversion(app)
app.url_map.strict_slashes = False

app.register_blueprint(admin_blueprint, url_prefix="/admin")

app.register_blueprint(operators_api_blueprint, url_prefix="/api/admin/operators")
app.register_blueprint(wagons_admin_blueprint, url_prefix="/api/admin/wagons")
app.register_blueprint(trainsets_admin_blueprint, url_prefix="/api/admin/trainsets")
app.register_blueprint(vagonweb_blueprint, url_prefix="/api/admin/vagonweb")
app.register_blueprint(feature_requests_blueprint)
app.register_blueprint(finance_blueprint)
app.register_blueprint(bmc_blueprint)
app.register_blueprint(discord_oauth_blueprint)
app.register_blueprint(news_blueprint)
app.register_blueprint(carbon_blueprint)
app.register_blueprint(stats_blueprint)
app.register_blueprint(wrapped_blueprint)
app.register_blueprint(ai_blueprint)
app.register_blueprint(mcp_blueprint)
app.register_blueprint(trainset_blueprint)
app.register_blueprint(dashboard_blueprint)
app.register_blueprint(timeline_blueprint)
app.register_blueprint(trips_blueprint)
app.register_blueprint(live_tracks_blueprint)

app.config["CACHE_TYPE"] = "SimpleCache"
app.config["CACHE_DEFAULT_TIMEOUT"] = 864000
cache = Cache(app)

matomo_config = load_config().get("matomo")

if matomo_config:
    matomo_url = matomo_config.get("url")
    id_site = matomo_config.get("id_site")
    token_auth = matomo_config.get("token_auth")

    if matomo_url and id_site and token_auth:
        matomo = CustomMatomo(
            app,
            matomo_url=matomo_url,
            id_site=id_site,
            token_auth=token_auth,
            ignored_routes=["/static/<path:filename>"],
        )


def getLoggedUserCurrency():
    # Cached on g: formatTrip calls this once per trip, and the answer can't
    # change within a request.
    currency = getattr(g, "_user_currency", None)
    if currency is None:
        user = getUser()
        if user == "public":
            currency = "EUR"
        else:
            currency = User.query.filter_by(username=user).first().user_currency
        g._user_currency = currency
    return currency


def generate_distinct_color(existing_hex_colors):
    # Convert existing hex colors to RGB
    existing_rgb_colors = [hex_to_rgb(color) for color in existing_hex_colors]

    # Generate one new distinct color
    new_rgb_color = distinctipy.get_colors(
        1, exclude_colors=existing_rgb_colors, pastel_factor=0.5
    )[0]

    # Convert the RGB color back to hex and return
    return rgb_to_hex(new_rgb_color)


r = git.repo.Repo("./")
latest_commit = r.head.commit

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///{db}".format(db=DbNames.AUTH_DB.value)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


# SECRET_KEY required for session, flash and Flask Sqlalchemy to work
SECRET_FILE_PATH = pathlib.Path(".flask_secret")
try:
    with SECRET_FILE_PATH.open("r") as secret_file:
        app.secret_key = secret_file.read()
except FileNotFoundError:
    # Let's create a cryptographically secure code in that file
    with SECRET_FILE_PATH.open("w") as secret_file:
        app.secret_key = secrets.token_hex(32)
        secret_file.write(app.secret_key)

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)


authDb.init_app(app)

def fetch_and_filter_flights(flight_filter_key, flight_filter_value, target_date):
    from_iso = f"{target_date - timedelta(days=1)}T12:00:00"
    to_iso = f"{target_date + timedelta(days=1)}T14:00:00"
    config = load_config()
    headers = {
        "Accept": "application/json",
        "Accept-Version": "v1",
        "Authorization": f"Bearer {config['FR24']['token_auth']}",
    }
    try:
        response = requests.get(
            "https://fr24api.flightradar24.com/api/flight-summary/light",
            headers=headers,
            params={
                flight_filter_key: flight_filter_value,
                "flight_datetime_from": from_iso,
                "flight_datetime_to": to_iso,
            },
            timeout=25,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        return {"error": "Failed to fetch data from FR24 API", "details": str(e)}, 502
    flights = response.json().get("data", [])
    filtered = []
    with pg_session() as pg:
        for f in flights:
            orig_icao = f.get("orig_icao")
            dest_icao = f.get("dest_icao")
            diverted_icao = f.get("destination_icao_actual")
            takeoff_str = f.get("datetime_takeoff")
            first_seen_str = f.get("first_seen")
            landing_str = f.get("datetime_landed")
            last_seen_str = f.get("last_seen")
            
            if orig_icao and (takeoff_str or first_seen_str):
                orig_coords = pg.execute(
                    "SELECT latitude, longitude FROM airports WHERE ident = :icao",
                    {"icao": orig_icao},
                ).fetchone()
                if orig_coords:
                    try:
                        # Use takeoff time if available, otherwise fall back to first_seen
                        departure_str = takeoff_str if takeoff_str else first_seen_str
                        utc_departure = datetime.fromisoformat(
                            departure_str.replace("Z", "+00:00")
                        )
                        local_departure = getLocalDatetime(
                            orig_coords[0], orig_coords[1], utc_departure
                        )
                        if local_departure.date() == target_date:
                            # Set the appropriate field based on what we used
                            if takeoff_str:
                                f["datetime_takeoff_local"] = local_departure.isoformat()
                            else:
                                f["datetime_takeoff_local"] = local_departure.isoformat()
                                f["_used_first_seen_for_takeoff"] = True  # Optional flag for debugging

                            if diverted_icao and (landing_str or last_seen_str):
                                dest_coords = pg.execute(
                                    "SELECT latitude, longitude FROM airports WHERE ident = :icao",
                                    {"icao": diverted_icao},
                                ).fetchone()
                                if dest_coords:
                                    # Use landing time if available, otherwise fall back to last_seen
                                    arrival_str = landing_str if landing_str else last_seen_str
                                    utc_landing = datetime.fromisoformat(
                                        arrival_str.replace("Z", "+00:00")
                                    )
                                    local_landing = getLocalDatetime(
                                        dest_coords[0], dest_coords[1], utc_landing
                                    )
                                    f["datetime_landed_local"] = (
                                        local_landing.isoformat()
                                    )
                                    # Optional flag for debugging
                                    if not landing_str:
                                        f["_used_last_seen_for_landing"] = True
                            
                            elif dest_icao and (landing_str or last_seen_str):
                                dest_coords = pg.execute(
                                    "SELECT latitude, longitude FROM airports WHERE ident = :icao",
                                    {"icao": dest_icao},
                                ).fetchone()
                                if dest_coords:
                                    # Use landing time if available, otherwise fall back to last_seen
                                    arrival_str = landing_str if landing_str else last_seen_str
                                    utc_landing = datetime.fromisoformat(
                                        arrival_str.replace("Z", "+00:00")
                                    )
                                    local_landing = getLocalDatetime(
                                        dest_coords[0], dest_coords[1], utc_landing
                                    )
                                    f["datetime_landed_local"] = (
                                        local_landing.isoformat()
                                    )
                                    # Optional flag for debugging
                                    if not landing_str:
                                        f["_used_last_seen_for_landing"] = True
                            filtered.append(f)
                    except Exception:
                        pass
    return {"data": filtered}, 200


@app.route("/api/u/<username>/flight_summary")
@login_required
def flight_summary(username):
    raw_flight_number = request.args.get("flight_number", "")
    date_str = request.args.get("date")

    flight_number = raw_flight_number.strip().replace(" ", "").upper()

    if not re.fullmatch(r"[A-Z0-9]{2,3}\d{1,4}", flight_number):
        return jsonify({"error": "Invalid flight number format."}), 400

    if not flight_number or not date_str:
        return jsonify(
            {"error": "Missing required parameters: flight_number and date"}
        ), 400

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    result, status = fetch_and_filter_flights("flights", flight_number, target_date)
    return jsonify(result), status


@app.route("/api/u/<username>/flight_summary_reg")
@login_required
def flight_summary_reg(username):
    registration = request.args.get("registration", "").strip().upper()
    date_str = request.args.get("date")

    if not registration or not re.fullmatch(r"[A-Z0-9\-]+", registration):
        return jsonify({"error": "Invalid or missing registration format."}), 400

    if not date_str:
        return jsonify({"error": "Missing required parameter: date"}), 400

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    result, status = fetch_and_filter_flights(
        "registrations", registration, target_date
    )
    return jsonify(result), status


def _fr24_epoch(ts):
    """Normalise an FR24 track timestamp (ISO 8601 string or epoch number) to
    epoch seconds. Returns None when missing/unparseable."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return int(ts)
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


@app.route("/api/u/<username>/flight_tracks/<fr24_id>")
@login_required
def flight_tracks(username, fr24_id):
    if not check_and_increment_fr24_usage(username=getUser()):
        return jsonify({"error": "Monthly FR24 API usage limit (5) reached."}), 429
    config = load_config()
    token = config["FR24"]["token_auth"]

    headers = {
        "Accept": "application/json",
        "Accept-Version": "v1",
        "Authorization": f"Bearer {token}",
    }

    url = "https://fr24api.flightradar24.com/api/flight-tracks"

    try:
        response = requests.get(
            url, headers=headers, params={"flight_id": fr24_id}, timeout=25
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return jsonify(
            {"error": "Failed to fetch track data from FR24 API", "details": str(e)}
        ), 502

    # Extract lat/lon plus altitude (Z) and timestamp (T) for the 3D track.
    if not data or "tracks" not in data[0]:
        return jsonify({"error": "No track data found"}), 404

    # FR24 altitude is in feet -> store metres; timestamp -> epoch seconds.
    enriched = [
        [
            track["lat"],
            track["lon"],
            round((track.get("alt") or 0) * 0.3048, 1),
            _fr24_epoch(track.get("timestamp")),
        ]
        for track in data[0]["tracks"]
        if "lat" in track and "lon" in track
    ]
    # Interpolate all four components together so the arrays stay aligned with
    # the (identical) interpolation the 2D path used previously.
    enriched = interpolate_track_if_gaps(enriched, 50)

    coordinates = [[p[0], p[1]] for p in enriched]
    altitude = [p[2] for p in enriched]
    timestamps = [p[3] for p in enriched]

    return jsonify(
        {"coordinates": coordinates, "altitude": altitude, "timestamps": timestamps}
    )


def getLangDropdown(user):
    langs = []
    langs.append(
        {"code": user.lang, "name": lang[session["userinfo"]["lang"]][user.lang]}
    )
    for code in readLang().keys():
        if code != user.lang:
            langs.append({"code": code, "name": lang[code][code]})
    return langs


def changeLang(langToSet, session=False):
    available_languages = []
    languages = readLang()
    for language in languages:
        available_languages.append(
            {"id": language, "name": languages[language][language]}
        )

    session["userinfo"] = {}
    session["userinfo"]["logged_in_user"] = getUser()
    session["userinfo"]["is_owner"] = True if getUser() == owner else False
    user = User.query.filter_by(username=session.get("logged_in")).first()
    session["userinfo"]["user_id"] = user.uid if user and user.uid else False
    session["userinfo"]["is_alpha"] = True if user and user.alpha else False
    session["userinfo"]["is_premium"] = True if user and user.premium else False
    session["userinfo"]["is_admin"] = True if user and user.admin else False
    session["userinfo"]["is_translator"] = True if user and user.translator else False
    session["userinfo"]["is_feature_admin"] = True if user and user.feature_admin else False
    session["userinfo"]["available_languages"] = available_languages
    session["userinfo"]["lang"] = langToSet


def get_country_codes_from_files(immediate_only=False):
    country_codes = {}
    path = "country_percent/countries/processed/"

    # fmt: off
    continent_mapping = {
        "EU": [
            "AL", "AD", "AT", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE",
            "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT", "XK", "LV", "LI", "LT",
            "LU", "MT", "MD", "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "RU",
            "SM", "RS", "SK", "SI", "ES", "SE", "CH", "UA", "GB", "VA", "IM", "GG",
        ],
        "AF": [
            "DZ", "AO", "BJ", "BW", "BF", "BI", "CM", "CV", "CF", "TD", "KM", "CG",
            "CD", "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET", "GA", "GM", "GH", "GN",
            "GW", "KE", "LS", "LR", "LY", "MG", "MW", "ML", "MR", "MU", "MA", "MZ",
            "NA", "NE", "NG", "RE", "RW", "ST", "SN", "SC", "SL", "SO", "ZA", "SS",
            "SD", "TZ", "TG", "TN", "UG", "EH", "ZM", "ZW",
        ],
        "AS": [
            "AF", "AM", "AZ", "BH", "BD", "BT", "BN", "KH", "CN", "CY", "GE", "IN",
            "ID", "IR", "IQ", "IL", "JP", "JO", "KZ", "KW", "KG", "LA", "LB", "MY",
            "MV", "MN", "MM", "NP", "KP", "OM", "PK", "PS", "PH", "QA", "SA", "SG",
            "KR", "LK", "SY", "TJ", "TH", "TR", "TM", "AE", "UZ", "VN", "YE", "TW",
            "HK"
        ],
        "NA": ["CA", "US", "MX", "CU", "KN", "PR", "GP", "MQ"],
        "CA": ["BZ", "CR", "SV", "GT", "HN", "NI", "PA"],
        "SA": ["AR", "BO", "BR", "CL", "CO", "EC", "GY", "PY", "PE", "SR", "UY", "VE"],
        "OC": [
            "AU", "FJ", "KI", "MH", "FM", "NR", "NZ", "PW", "PG", "SB", "TO", "TV",
            "VU", "WS",
        ],
    }
    # fmt: on

    # Invert the continent_mapping dictionary
    country_to_continent = {
        cc: continent
        for continent, country_codes in continent_mapping.items()
        for cc in country_codes
    }

    def add_to_country_codes(name):
        if "-" in name:
            cc = name.split("-")[0].upper()
            continent = "Region_" + cc
            if not immediate_only:
                add_to_country_codes(cc) # also add full country if subdivisions exist
        else:
            cc = name.upper()
            continent = country_to_continent.get(cc, "Unknown")
        if continent not in country_codes:
            country_codes[continent] = []

        if name not in country_codes[continent]:
            country_codes[continent].append(name)

    # Iterate over all files in the directory to collect country codes
    for filename in os.listdir(path):
        if filename.endswith(".geojson"):
            # Extract country code from filename
            name = filename.replace(".geojson", "")
            add_to_country_codes(name)

    # Sort each list of country codes
    for continent, codes in country_codes.items():
        codes.sort()

    # Sort the continents
    def sort_key(item):
        key, _ = item
        if "Region" in key:
            # Extract the part after "Region_" and use it for sorting
            return (1, key.split("Region_")[1])
        else:
            # Non-region keys are sorted normally and prioritized
            return (0, key)

    sorted_country_codes = dict(sorted(country_codes.items(), key=sort_key))
    return sorted_country_codes


app.jinja_env.globals.update(get_country_codes_from_files=get_country_codes_from_files)


@app.route("/api/localtime", methods=["GET"])
def get_local_time():
    try:
        lat = float(request.args.get("lat"))
        lng = float(request.args.get("lng"))
        utc_str = request.args.get("utc")

        if not utc_str:
            return jsonify({"error": "Missing 'utc' datetime parameter"}), 400

        try:
            dateTime = datetime.fromisoformat(
                utc_str.replace("Z", "+00:00")
            ).astimezone(pytz.utc)
        except ValueError:
            return jsonify(
                {
                    "error": "Invalid 'utc' datetime format. Use ISO 8601 like 'YYYY-MM-DDTHH:MM:SSZ'."
                }
            ), 400

        local_dt = getLocalDatetime(lat, lng, dateTime)
        return jsonify({"local_datetime": local_dt.isoformat()})

    except (TypeError, ValueError):
        return jsonify(
            {"error": "Invalid or missing parameters: 'lat', 'lng', 'utc'."}
        ), 400


def starts_with_flag_emoji(s):
    pattern = r"^[\U0001F1E6-\U0001F1FF][\U0001F1E6-\U0001F1FF]"
    return bool(re.match(pattern, s))


def saveTripToDb(username, newTrip, newPath, trip_type="train", altitude=None, timestamps=None):
    newPath[0]["lat"] = float(newPath[0]["lat"])
    newPath[0]["lng"] = float(newPath[0]["lng"])
    newPath[-1]["lat"] = float(newPath[-1]["lat"])
    newPath[-1]["lng"] = float(newPath[-1]["lng"])
    if not starts_with_flag_emoji(newTrip["originStation"][1]):
        origin_country = getCountryFromCoordinates(newPath[0]["lat"], newPath[0]["lng"])
        newTrip["originStation"][1] = (
            f"{get_flag_emoji(origin_country['countryCode'])} {newTrip['originStation'][1]}"
        )
        if not newTrip.get("originManualLat"):
            newTrip["originManualLat"] = newPath[0]["lat"]
        if not newTrip.get("originManualLng"):
            newTrip["originManualLng"] = newPath[0]["lng"]

    if not starts_with_flag_emoji(newTrip["destinationStation"][1]):
        destination_country = getCountryFromCoordinates(
            newPath[-1]["lat"], newPath[-1]["lng"]
        )
        newTrip["destinationStation"][1] = (
            f"{get_flag_emoji(destination_country['countryCode'])} {newTrip['destinationStation'][1]}"
        )
        if not newTrip.get("destinationManualLat"):
            newTrip["destinationManualLat"] = newPath[-1]["lat"]
        if not newTrip.get("destinationManualLng"):
            newTrip["destinationManualLng"] = newPath[-1]["lng"]

    now = datetime.now()
    manDuration, start_datetime, end_datetime, utc_start_datetime, utc_end_datetime = (
        processDates(newTrip, newPath)
    )

    if "reg" not in newTrip.keys():
        newTrip["reg"] = ""
    if "seat" not in newTrip.keys():
        newTrip["seat"] = ""
    if "material_type" not in newTrip.keys():
        newTrip["material_type"] = ""
    if "material_type_advanced" not in newTrip.keys():
        newTrip["material_type_advanced"] = ""
    if "waypoints" not in newTrip.keys():
        newTrip["waypoints"] = ""
    if "notes" not in newTrip.keys():
        newTrip["notes"] = ""
    if "ticket_id" not in newTrip.keys():
        newTrip["ticket_id"] = ""

    if trip_type in ("air", "helicopter"):
        countries = {}
        countries[getCountryFromCoordinates(**newPath[0])["countryCode"]] = (
            newTrip["trip_length"] / 2
        )
        countries[getCountryFromCoordinates(**newPath[-1])["countryCode"]] = (
            newTrip["trip_length"] / 2
        )
        countries = json.dumps(countries)
    else:
        countries = getCountriesFromPath(newPath, newTrip["type"], newTrip.get("details", None), newTrip.get("powerType", None))

    if "originManualToggle" in newTrip.keys():
        saveManualStation(
            name=newTrip["originStation"][1],
            creator=username,
            lat=newTrip["originManualLat"],
            lng=newTrip["originManualLng"],
            station_type=trip_type,
        )
    if "destinationManualToggle" in newTrip.keys():
        saveManualStation(
            name=newTrip["destinationStation"][1],
            creator=username,
            lat=newTrip["destinationManualLat"],
            lng=newTrip["destinationManualLng"],
            station_type=trip_type,
        )

    user_id = User.query.filter_by(username=username).first().uid

    trip = Trip(
        username=username,
        user_id=user_id,
        origin_station=sanitize_param(newTrip["originStation"][1]),
        destination_station=sanitize_param(newTrip["destinationStation"][1]),
        start_datetime=start_datetime if start_datetime not in [-1, 1] else None,
        utc_start_datetime=utc_start_datetime,
        end_datetime=end_datetime if end_datetime not in [-1, 1] else None,
        utc_end_datetime=utc_end_datetime,
        trip_length=sanitize_param(newTrip["trip_length"]),
        estimated_trip_duration=sanitize_param(newTrip["estimated_trip_duration"]),
        manual_trip_duration=manDuration,
        operator=sanitize_param(newTrip["operator"]),
        countries=sanitize_param(countries),
        line_name=sanitize_param(newTrip["lineName"]),
        created=now,
        last_modified=now,
        type=sanitize_param(trip_type),
        seat=sanitize_param(newTrip["seat"]),
        seat_car=parse_seat_car(newTrip.get("seat_car")),
        material_type=sanitize_param(newTrip["material_type"]),
        material_type_advanced=sanitize_param(newTrip["material_type_advanced"]),
        reg=sanitize_param(newTrip["reg"]),
        waypoints=sanitize_param(newTrip["waypoints"]),
        notes=sanitize_param(newTrip["notes"]),
        price=sanitize_param(newTrip["price"]),
        currency=sanitize_param(newTrip["currency"]),
        purchasing_date=sanitize_param(newTrip["purchasing_date"]),
        ticket_id=sanitize_param(newTrip["ticket_id"]),
        is_project=start_datetime == 1 or end_datetime == 1,
        path=newPath,
        visibility=sanitize_param(newTrip.get("visibility", get_default_trip_visibility(trip_type))),
        departure_delay=sanitize_param(newTrip.get("departure_delay")),
        arrival_delay=sanitize_param(newTrip.get("arrival_delay")),
        power_type=newTrip.get("powerType"),
        co2_override=float(newTrip["co2Override"]) if newTrip.get("co2Override") else None,
        altitude=altitude,
        timestamps=timestamps,
        route_source=newTrip.get("route_source") or "router",
    )

    create_trip(trip)
    tag_ids = [int(t) for t in (newTrip.get("tag_ids") or []) if str(t).isdigit()]
    if tag_ids:
        with pg_session() as pg:
            # Only tags the user owns or is an accepted member of
            tag_ids = [t for t in tag_ids if get_tag_role(pg, t, username) is not None]
            for tag_id in tag_ids:
                pg.execute(
                    "INSERT INTO tags_associations (tag_id, trip_id)"
                    " VALUES (:tag_id, :trip_id) ON CONFLICT DO NOTHING",
                    {"tag_id": tag_id, "trip_id": trip.trip_id},
                )
    return trip


def savePlanTripToDb(username, newTrip, newPath, plan, trip_type="train"):
    """Like saveTripToDb but writes a plan_trip (parallel world) instead of a trip.
    Reuses the same flag/country enrichment; timing goes through process_plan_dates."""
    newPath[0]["lat"] = float(newPath[0]["lat"])
    newPath[0]["lng"] = float(newPath[0]["lng"])
    newPath[-1]["lat"] = float(newPath[-1]["lat"])
    newPath[-1]["lng"] = float(newPath[-1]["lng"])
    if not starts_with_flag_emoji(newTrip["originStation"][1]):
        origin_country = getCountryFromCoordinates(newPath[0]["lat"], newPath[0]["lng"])
        newTrip["originStation"][1] = (
            f"{get_flag_emoji(origin_country['countryCode'])} {newTrip['originStation'][1]}"
        )
    if not starts_with_flag_emoji(newTrip["destinationStation"][1]):
        destination_country = getCountryFromCoordinates(
            newPath[-1]["lat"], newPath[-1]["lng"]
        )
        newTrip["destinationStation"][1] = (
            f"{get_flag_emoji(destination_country['countryCode'])} {newTrip['destinationStation'][1]}"
        )

    now = datetime.now()
    timing = process_plan_dates(newTrip, newPath)

    for k in ("reg", "seat", "material_type", "material_type_advanced", "waypoints", "notes"):
        newTrip.setdefault(k, "")

    if trip_type in ("air", "helicopter"):
        countries = {}
        countries[getCountryFromCoordinates(**newPath[0])["countryCode"]] = (
            newTrip["trip_length"] / 2
        )
        countries[getCountryFromCoordinates(**newPath[-1])["countryCode"]] = (
            newTrip["trip_length"] / 2
        )
        countries = json.dumps(countries)
    else:
        countries = getCountriesFromPath(
            newPath, newTrip["type"], newTrip.get("details", None), newTrip.get("powerType", None)
        )

    # `details` arrives already parsed (newTrip is JSON-decoded from the form), so it's a
    # dict here — not a string. The new router (graphhopper) populates it with the
    # electrification split, which is why this only bit when that router was used.
    _details = newTrip.get("details")
    details_parsed = json.loads(_details) if isinstance(_details, str) else _details
    power_type = newTrip.get("powerType") or (
        details_parsed.get("powerType") if details_parsed else None
    )
    co2_override = float(newTrip["co2Override"]) if newTrip.get("co2Override") else None

    plan_trip = PlanTrip(
        plan_id=plan["uid"],
        user_id=plan["user_id"],
        origin_station=sanitize_param(newTrip["originStation"][1]),
        destination_station=sanitize_param(newTrip["destinationStation"][1]),
        trip_type=sanitize_param(trip_type),
        operator=sanitize_param(newTrip.get("operator")),
        line_name=sanitize_param(newTrip.get("lineName")),
        material_type=sanitize_param(newTrip["material_type"]),
        material_type_advanced=sanitize_param(newTrip["material_type_advanced"]),
        reg=sanitize_param(newTrip["reg"]),
        seat=sanitize_param(newTrip["seat"]),
        notes=sanitize_param(newTrip["notes"]),
        trip_length=sanitize_param(newTrip.get("trip_length")),
        estimated_trip_duration=sanitize_param(newTrip.get("estimated_trip_duration")),
        countries=sanitize_param(countries),
        price=sanitize_param(newTrip.get("price")),
        currency=sanitize_param(newTrip.get("currency")),
        purchase_date=sanitize_param(newTrip.get("purchasing_date")),
        booked=newTrip.get("booked") in ("true", "on", "1", True),
        waypoints=sanitize_param(newTrip["waypoints"]),
        visibility=sanitize_param(
            newTrip.get("visibility", get_default_trip_visibility(trip_type))
        ),
        path=newPath,
        timing=timing,
        power_type=power_type,
        co2_override=co2_override,
        created=now,
        last_modified=now,
    )
    create_plan_trip(plan_trip)
    return plan_trip


def get_owned_plan(plan_uuid, username):
    """Fetch a plan row (mapping) by uuid and verify the logged-in user owns it."""
    with pg_session() as pg:
        row = pg.execute(get_plan_query(), {"uuid": plan_uuid}).fetchone()
    if row is None:
        abort(404)
    plan = dict(row._mapping)
    user = User.query.filter_by(username=username).first()
    if user is None or plan["user_id"] != user.uid:
        abort(403)
    return plan


def hasPrivateTrips(username):
    with pg_session() as pg:
        return pg.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM trips
                WHERE visibility = 'private'
                AND user_id = :user_id
            ) AS has_uncommon_trips;
        """,
            {"user_id": get_user_id(username)},
        ).scalar() is True


def get_ticket_cached(ticket_id):
    """Ticket row by uid, cached for the request: many trips share one ticket, and
    get_ticket.sql counts the ticket's trips, so per-trip lookups repeat real work."""
    cache = getattr(g, "_ticket_cache", None)
    if cache is None:
        cache = g._ticket_cache = {}
    if ticket_id not in cache:
        with pg_session() as pg:
            cache[ticket_id] = pg.execute(
                get_ticket_query(), {"uid": ticket_id}
            ).fetchall()[0]
    return cache[ticket_id]


def formatTrip(trip, public=False):
    if trip["start_datetime"] not in (1, -1) and trip["end_datetime"] not in (
        1,
        -1,
    ):
        if trip["type"] in ("poi", "accommodation", "restaurant"):
            trip["destination_station"] = ""
        start_datetime = datetime.strptime(trip["start_datetime"], "%Y-%m-%d %H:%M:%S")
        end_datetime = datetime.strptime(trip["end_datetime"], "%Y-%m-%d %H:%M:%S")
        start_date = start_datetime.date()
        end_date = end_datetime.date()
        if start_datetime.second == 0 and end_datetime.second == 0:
            start_time = start_datetime.strftime("%H:%M")
            end_time = end_datetime.strftime("%H:%M")

            if trip["utc_start_datetime"] is None:
                trip_duration = [
                    "calc",
                    (end_datetime - start_datetime).total_seconds(),
                ]
            else:
                utc_start_datetime = datetime.strptime(
                    trip["utc_start_datetime"], "%Y-%m-%d %H:%M:%S"
                )
                utc_end_datetime = datetime.strptime(
                    trip["utc_end_datetime"], "%Y-%m-%d %H:%M:%S"
                )
                trip_duration = [
                    "calc",
                    (utc_end_datetime - utc_start_datetime).total_seconds(),
                ]

            if end_date != start_date:
                days_diff = end_date - start_date
                end_time += "(+{})".format(days_diff.days)
        else:
            start_time = end_time = ""
            if trip["manual_trip_duration"] is not None:
                trip_duration = ["man", trip["manual_trip_duration"]]
            elif trip["estimated_trip_duration"] is not None:
                trip_duration = ["est", trip["estimated_trip_duration"]]

        start_date = start_date.strftime("%Y-%m-%d")
    else:
        start_date = start_time = end_time = ""
        if trip["manual_trip_duration"] is not None:
            trip_duration = ["man", trip["manual_trip_duration"]]
        elif trip["estimated_trip_duration"] is not None:
            trip_duration = ["est", trip["estimated_trip_duration"]]
        else:
            start_date = start_time = end_time = ""
            trip_duration = ["", ""]
    trip["user_currency"] = getLoggedUserCurrency()
    if trip.get("price") not in (None, ""):
        trip["price_in_user_currency"] = get_exchange_rate(
            base_currency=trip["currency"],
            target_currency=trip["user_currency"],
            date=trip["purchasing_date"],
            price=trip["price"],
        )

    if trip["ticket_id"] not in (None, ""):
        ticket = get_ticket_cached(trip["ticket_id"])
        trip["ticket"] = ticket["name"]
        trip["ticket_price"] = ticket["price"] / ticket["trip_count"]
        trip["ticket_currency"] = ticket["currency"]
        trip["ticket_price_in_user_currency"] = get_exchange_rate(
            price=trip["ticket_price"],
            base_currency=trip["ticket_currency"],
            target_currency=trip["user_currency"],
            date=ticket["purchasing_date"],
        )

    if trip["operator"] is None or trip["operator"] == "":
        trip["operator"] = ""

    if trip["line_name"] is None or trip["line_name"] == "":
        trip["line_name"] = ""

    trip["start_date"] = start_date
    trip["start_time"] = start_time
    trip["end_time"] = end_time
    trip["trip_duration"] = trip_duration
    return trip


def user_exists(username):
    user = User.query.filter_by(username=username).first()
    return user is not None

def saveManualStation(creator, name, lat, lng, station_type):
    # Manual stations are now scoped per-user (filtered by creator), so there is
    # no longer any privacy concern with persisting them for every trip type
    # (car, walk, cycle, ...).
    with pg_session() as pg:
        pg.execute(
            "INSERT INTO manual_stations (creator, name, lat, lng, station_type)"
            " VALUES (:creator, :name, :lat, :lng, :station_type)",
            {
                "creator": creator,
                "name": name,
                "lat": lat,
                "lng": lng,
                "station_type": station_type,
            },
        )


def airlineLogoProcess(newTrip):
    if "operatorLogoURL" in newTrip.keys():
        logo_path = "static/images/operator_logos/" + newTrip["operator"] + ".png"
        if not os.path.exists(logo_path):
            base_url = "https://api-ninjas.com/images/airline_logos/"
            url = base_url + newTrip["operatorLogoURL"].split("/")[-1]
            response = requests.get(url)
            with open(logo_path, "wb") as f:
                f.write(response.content)


def resolveSnippets(langName):
    lang = readLang()[langName]
    resolvedSnippets = {}
    for snippet_path in glob("snippets/*.html"):
        with open(snippet_path, "r", encoding="utf-8") as snippet:
            resolvedSnippets[getNameFromPath(snippet_path)] = render_template_string(
                snippet.read(),
                **lang[session["userinfo"]["lang"]],
                **session["userinfo"],
            )
    return resolvedSnippets


def create_authDb():
    """# Execute this first time to create a new db in the current directory."""
    config = load_config()
    user_data = config["owner"]

    hashed_pass = generate_password_hash(user_data["password"], "scrypt")

    authDb.create_all()
    new_user = User(
        username=user_data["username"],
        email=user_data["email"],
        pass_hash=hashed_pass,
        admin=True,
    )
    authDb.session.add(new_user)
    authDb.session.commit()


@app.before_request
def before_request():
    allowed_hosts = [
        "127.0.0.1:5000",
        "localhost:5000",
        "trainlog.me",
        "www.trainlog.me",
        "dev.trainlog.me",
        "192.168.32.214:5000",
    ]
    if request.host not in allowed_hosts:
        log_suspicious_activity(
            request.url,
            "invalid_host",
            request.host,
            getIp(request),
            getRequestData(request),
        )
        return "", 406
    endpoint = request.endpoint
    if endpoint:
        # Get the URL rule associated with the current endpoint
        rule = app.url_map._rules_by_endpoint.get(endpoint)
        if rule:
            url_rule = rule[0].rule
            # Check if the URL rule contains <username>
            if "<username>" in url_rule:
                username = request.view_args.get("username")
                if username and not user_exists(username):
                    log_suspicious_activity(
                        request.url,
                        "nonexistent_user",
                        request.host,
                        getIp(request),
                        getRequestData(request),
                    )
                    abort(404)
        else:
            log_suspicious_activity(
                request.url,
                "nonexistent_rule",
                request.host,
                getIp(request),
                getRequestData(request),
            )
            abort(404)
    else:
        log_suspicious_activity(
            request.url,
            "nonexistent_endpoint",
            request.host,
            getIp(request),
            getRequestData(request),
        )
        abort(404)

    # Default language
    language = "en"

    # List of supported languages based on language files
    lang_files = os.listdir("lang")  # List all files in the 'lang' directory
    supported_languages = [
        file.split(".")[0] for file in lang_files if file.endswith(".json")
    ]

    # Check if language is set in session
    if "userinfo" in session and "lang" in session["userinfo"]:
        language = session["userinfo"]["lang"]
        # Temp fix for pt to pt-PT
        if language == "pt":
            session["userinfo"]["lang"] = "pt-PT"
            language = "pt-PT"
    else:
        # Get the list of accepted languages from the request
        accepted_languages = [lang[0] for lang in request.accept_languages]

        for lang in accepted_languages:
            if lang in supported_languages:
                language = lang
                break
            short_lang = lang.split("-")[0]
            if short_lang in supported_languages:
                language = short_lang
                break

    changeLang(language, session)


# Canonical trip-type display order, grouped as in the navbar "New" menu.
# Templates draw a divider whenever the group index changes.
TRIP_TYPE_GROUPS = [
    ["train", "tram", "metro", "funicular", "rail"],
    ["air", "bus", "ferry", "helicopter", "aerialway"],
    ["walk", "cycle", "ski", "scooter", "car", "other"],
    ["accommodation", "poi", "restaurant"],
]
TRIP_TYPE_GROUP_INDEX = {
    t: i for i, group in enumerate(TRIP_TYPE_GROUPS) for t in group
}
TRIP_TYPE_SORT_KEY = {
    t: i for i, t in enumerate(t for group in TRIP_TYPE_GROUPS for t in group)
}


def order_trip_types(types):
    """Sort trip types into the canonical grouped order; unknown types last."""
    return sorted(
        types, key=lambda t: TRIP_TYPE_SORT_KEY.get(t, len(TRIP_TYPE_SORT_KEY))
    )


@app.context_processor
def inject_distinct_types():
    # 1) If we’re rendering an error page, don’t touch the DB
    if getattr(g, "suppress_context_queries", False):
        return {"distinctTypes": {}}

    # 2) Safe session lookups
    userinfo = session.get("userinfo") or {}
    username = userinfo.get("logged_in_user")
    lang_code = userinfo.get("lang", "en")
    if not username:
        return {"distinctTypes": {}}

    # 3) If we already computed it during this request, reuse it
    if hasattr(g, "distinct_types_ctx"):
        return {"distinctTypes": g.distinct_types_ctx}

    # 4) Icon mapping
    icon_map = {
        "train": "fa-solid fa-train",
        "tram": "fa-solid fa-train-tram",
        "metro": "fa-solid fa-train-subway",
        "air": "fa-solid fa-plane-up",
        "bus": "fa-solid fa-bus",
        "ferry": "fa-solid fa-ship",
        "helicopter": "fa-solid fa-helicopter",
        "aerialway": "fa-solid fa-cable-car",
        "walk": "fa-solid fa-person-hiking",
        "cycle": "fa-solid fa-bicycle",
        "car": "fa-solid fa-car-side",
        "scooter": "bi bi-scooter",
        "funicular": "fa-solid fa-mountain",
        "rail": "fa-solid fa-dumbbell",
        "ski": "fa-solid fa-person-skiing",
        "other": "fa-solid fa-circle-question",
    }

    # 5) Query, but fail soft if DB is locked (or anything else goes wrong)
    try:
        with pg_session() as pg:
            rows = pg.execute(
                """
                SELECT DISTINCT trip_type AS type
                FROM trips
                WHERE user_id = :user_id
                  AND trip_type NOT IN ('poi', 'accommodation', 'restaurant')
                """,
                {"user_id": get_user_id(username)},
            ).fetchall()
    except Exception as err:
        logger.exception("Context processor failed: inject_distinct_types")
        g.distinct_types_ctx = {}  # cache the empty fallback to avoid retries
        return {"distinctTypes": {}}

    # 6) Build the dict with localized labels, in canonical grouped order
    lang_dict = lang.get(lang_code, {})
    types = {
        t: {
            "label": lang_dict.get(t, t),
            "icon": icon_map.get(t, "fa-solid fa-question"),
            "group": TRIP_TYPE_GROUP_INDEX.get(t, len(TRIP_TYPE_GROUPS)),
        }
        for t in order_trip_types(r[0] for r in rows)
    }

    g.distinct_types_ctx = types
    return {"distinctTypes": types}

@app.route("/robots.txt")
def robots_txt():
    return send_from_directory(app.static_folder, "robots.txt")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static/favicon"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@app.route("/apple-touch-<icon_name>.png")
def apple_touch_icon(icon_name):
    return send_from_directory(
        os.path.join(app.root_path, "static/images"),
        "logo_square.png",
        mimetype="image/png",
    )


@app.route("/u/<username>/new/auto")
@login_required
def new_auto(username):
    return render_template(
        "new_auto.html",
        title="new_trip",
        vehicle_type="car",
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
        currencyOptions=get_available_currencies(),
        user_currency=getLoggedUserCurrency(),
    )


def get_new_trip_types(user_lang):
    """Ordered {type: {label, group}} of the vehicle types the new-trip form supports.

    Used to populate the in-form type switcher (click the header icon to swap
    type, e.g. follow a train trip with a bus trip). Same grouped order as the
    navbar "New" menu; only types that ``new()`` can actually render are listed
    (``other`` has no form branch).
    """
    return {
        t: {"label": user_lang[t], "group": group_index}
        for group_index, group in enumerate(TRIP_TYPE_GROUPS)
        for t in group
        if t != "other"
    }


@app.route("/u/<username>/compose")
@login_required
def compose_default(username):
    """Bare /compose — the form needs a type, and trains are what most people log.

    Any query string (``?plan=``, the save-and-continue prefill) is carried over, and
    the type switcher in the header changes it from there.
    """
    return redirect(
        url_for("compose", username=username, vehicle_type="train", **request.args)
    )


@app.route("/u/<username>/compose/<vehicle_type>")
@login_required
def compose(username, vehicle_type):
    return new(username, vehicle_type, template="compose.html")


@app.route("/u/<username>/new/<vehicle_type>")
@login_required
def new(username, vehicle_type, template="new.html"):
    if vehicle_type == "train":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripTrain"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originStation"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originStationName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationStation"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationStationName"
        ]

    elif vehicle_type == "tram":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripTram"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originStation"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originStationName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationStation"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationStationName"
        ]

    elif vehicle_type == "metro":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripMetro"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originStation"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originStationName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationStation"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationStationName"
        ]

    elif vehicle_type == "bus":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripBus"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originBusStation"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originBusStationName"]
        destination_terminal = lang[session["userinfo"]["lang"]][
            "destinationBusStation"
        ]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationBusStationName"
        ]

    elif vehicle_type == "ferry":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripFerry"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originFerryTerminal"]
        origin_terminal_name = lang[session["userinfo"]["lang"]][
            "originFerryTerminalName"
        ]
        destination_terminal = lang[session["userinfo"]["lang"]][
            "destinationFerryTerminal"
        ]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationFerryTerminalName"
        ]

    elif vehicle_type == "accommodation":
        new_trip = lang[session["userinfo"]["lang"]]["newAccommodation"]
        origin_terminal = lang[session["userinfo"]["lang"]]["searchAccommodation"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["accommodationName"]
        manual_origin = lang[session["userinfo"]["lang"]]["manualAccommodation"]
        destination_terminal = ""
        destination_terminal_name = ""

    elif vehicle_type == "poi":
        new_trip = lang[session["userinfo"]["lang"]]["newPoi"]
        origin_terminal = lang[session["userinfo"]["lang"]]["searchPoi"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["poiName"]
        manual_origin = lang[session["userinfo"]["lang"]]["manualPoi"]
        destination_terminal = ""
        destination_terminal_name = ""

    elif vehicle_type == "restaurant":
        new_trip = lang[session["userinfo"]["lang"]]["newRestaurant"]
        origin_terminal = lang[session["userinfo"]["lang"]]["searchRestaurant"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["restaurantName"]
        manual_origin = lang[session["userinfo"]["lang"]]["manualRestaurant"]
        destination_terminal = ""
        destination_terminal_name = ""

    elif vehicle_type == "helicopter":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripHelicopter"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originHelipad"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originHelipadName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationHelipad"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationHelipadName"
        ]

    elif vehicle_type == "air":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripAir"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originAirport"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originAirport"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationAirport"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationAirport"
        ]

    elif vehicle_type == "car":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripCar"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originCar"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originCarName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationCar"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationCarName"
        ]

    elif vehicle_type == "walk":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripWalk"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originWalk"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originWalkName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationWalk"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationWalkName"
        ]

    elif vehicle_type == "cycle":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripBike"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originBike"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originBikeName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationBike"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationBikeName"
        ]

    elif vehicle_type == "aerialway":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]]["newTripAerialway"]
        origin_terminal = lang[session["userinfo"]["lang"]]["originAerialway"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originAerialwayName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationAerialway"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationAerialwayName"
        ]

    elif vehicle_type == "funicular":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]].get("newTripFunicular", "New Trip - Funicular")
        origin_terminal = lang[session["userinfo"]["lang"]]["originStation"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originStationName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationStation"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationStationName"
        ]

    elif vehicle_type == "rail":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]].get("newTripRail", "New Trip - Rail")
        origin_terminal = lang[session["userinfo"]["lang"]]["originStation"]
        origin_terminal_name = lang[session["userinfo"]["lang"]]["originStationName"]
        destination_terminal = lang[session["userinfo"]["lang"]]["destinationStation"]
        destination_terminal_name = lang[session["userinfo"]["lang"]][
            "destinationStationName"
        ]

    elif vehicle_type == "scooter":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]].get("newTripEScooter", "New Trip - E-Scooter")
        origin_terminal = lang[session["userinfo"]["lang"]].get("originEScooter", lang[session["userinfo"]["lang"]]["originBike"])
        origin_terminal_name = lang[session["userinfo"]["lang"]].get("originEScooterName", lang[session["userinfo"]["lang"]]["originBikeName"])
        destination_terminal = lang[session["userinfo"]["lang"]].get("destinationEScooter", lang[session["userinfo"]["lang"]]["destinationBike"])
        destination_terminal_name = lang[session["userinfo"]["lang"]].get(
            "destinationEScooterName", lang[session["userinfo"]["lang"]]["destinationBikeName"]
        )

    elif vehicle_type == "ski":
        manual_origin = lang[session["userinfo"]["lang"]]["manOrigin"]
        new_trip = lang[session["userinfo"]["lang"]].get("newTripSki", "New Trip - Ski")
        origin_terminal = lang[session["userinfo"]["lang"]].get("originSki", lang[session["userinfo"]["lang"]]["originAerialway"])
        origin_terminal_name = lang[session["userinfo"]["lang"]].get("originSkiName", lang[session["userinfo"]["lang"]]["originAerialwayName"])
        destination_terminal = lang[session["userinfo"]["lang"]].get("destinationSki", lang[session["userinfo"]["lang"]]["destinationAerialway"])
        destination_terminal_name = lang[session["userinfo"]["lang"]].get(
            "destinationSkiName", lang[session["userinfo"]["lang"]]["destinationAerialwayName"]
        )

    # When a plan is being built, look up its name for the builder info bar.
    plan_uuid = request.args.get("plan")
    plan_name = None
    if plan_uuid:
        with pg_session() as pg:
            prow = pg.execute(get_plan_query(), {"uuid": plan_uuid}).fetchone()
        # Only surface the name to the plan's owner (authoring is owner-only anyway).
        plan_name = (
            prow._mapping["name"]
            if prow and prow._mapping["user_id"] == session["userinfo"]["user_id"]
            else None
        )

    return render_template(
        template,
        title=new_trip,
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
        vehicle_type=vehicle_type,
        newTrip=new_trip,
        originTerminal=origin_terminal,
        originTerminalName=origin_terminal_name,
        destinationTerminal=destination_terminal,
        destinationTerminalName=destination_terminal_name,
        trip_visibility=get_default_trip_visibility(vehicle_type),
        manualOrigin=manual_origin,
        currencyOptions=get_available_currencies(),
        user_currency=getLoggedUserCurrency(),
        fr24_calls=fr24_usage(username) if vehicle_type == "air" else None,
        # When building a trip for a plan, this enables the relative day+time mode
        # and routes the save to savePlanTrip (the builder is otherwise identical).
        plan_uuid=plan_uuid,
        plan_name=plan_name,
        new_trip_types=get_new_trip_types(lang[session["userinfo"]["lang"]]),
    )


@app.route("/u/<username>/new_tag")
@login_required
def new_tag(username):
    # Tag creation now lives on the unified tag_list page
    return redirect(url_for("tag_list", username=username))


@app.route("/u/<username>/submit_tag", methods=["POST"])
@login_required
def submit_tag(username):
    # Extract data from form
    tag_name = request.form["name"]
    tag_colour = request.form["colour"]
    tag_uuid = str(uuid.uuid4())
    tag_type = request.form["type"]

    with pg_session() as pg:
        pg.execute(
            "INSERT INTO tags (username, name, colour, uuid, type)"
            " VALUES (:username, :name, :colour, :uuid, :type)",
            {
                "username": username,
                "name": tag_name,
                "colour": tag_colour,
                "uuid": tag_uuid,
                "type": tag_type,
            },
        )

    return redirect(url_for("tag_list", username=username))


@app.route("/u/<username>/quick_create_tag", methods=["POST"])
@login_required
def quick_create_tag(username):
    """Minimal tag creation from the trip form's tag field: name only,
    type defaults to voyage, colour auto-chosen. Idempotent on name."""
    name = (request.get_json().get("name") or "").strip()
    if not name:
        return jsonify({"error": "Invalid input"}), 400

    with pg_session() as pg:
        existing = pg.execute(
            "SELECT uid, uuid, name, colour, type FROM tags"
            " WHERE username = :username AND name = :name",
            {"username": username, "name": name},
        ).fetchone()
        if existing:
            return jsonify(tag=dict(existing._mapping))

        colours = pg.execute(
            "SELECT colour FROM tags WHERE username = :username",
            {"username": username},
        ).fetchall()
        row = pg.execute(
            "INSERT INTO tags (username, name, colour, uuid, type)"
            " VALUES (:username, :name, :colour, :uuid, 'voyage')"
            " RETURNING uid, uuid, name, colour, type",
            {
                "username": username,
                "name": name,
                "colour": generate_distinct_color([c[0] for c in colours]),
                "uuid": str(uuid.uuid4()),
            },
        ).fetchone()
    return jsonify(tag=dict(row._mapping))


def get_tag_role(pg, tag_id, username):
    """'owner' | 'member' (accepted) | None for username on tag_id."""
    row = pg.execute(
        "SELECT username FROM tags WHERE uid = :uid", {"uid": tag_id}
    ).fetchone()
    if row is None:
        return None
    if row["username"] == username:
        return "owner"
    member = pg.execute(
        "SELECT 1 FROM tag_members"
        " WHERE tag_id = :uid AND username = :username AND status = 'accepted'",
        {"uid": tag_id, "username": username},
    ).fetchone()
    return "member" if member else None


def filter_owned_trip_ids(pg, trip_ids, username):
    """Subset of trip_ids that belong to username."""
    rows = pg.execute(
        "SELECT trip_id FROM trips WHERE trip_id = ANY(:ids) AND user_id = :user_id",
        {"ids": [int(t) for t in trip_ids], "user_id": get_user_id(username)},
    ).fetchall()
    return {row["trip_id"] for row in rows}


@app.route("/u/<username>/attach_tag", methods=["POST"])
@login_required
def attach_tag(username):
    data = request.json
    tag_id = data.get("tag_id")
    trip_ids = data.get("trip_ids")

    if not tag_id or not trip_ids:
        return jsonify({"error": "Invalid input"}), 400

    with pg_session() as pg:
        if get_tag_role(pg, tag_id, username) is None:
            abort(401)
        # Owners and members alike may only attach their own trips
        owned = filter_owned_trip_ids(pg, trip_ids, username)
        if any(int(t) not in owned for t in trip_ids):
            return jsonify({"error": "Invalid input"}), 400
        for trip_id in trip_ids:
            pg.execute(
                """
                    INSERT INTO tags_associations (tag_id, trip_id)
                    VALUES (:tag_id, :trip_id)
                    ON CONFLICT (tag_id, trip_id) DO NOTHING
                """,
                {"tag_id": tag_id, "trip_id": trip_id},
            )
    return ""


@app.route("/u/<username>/detach_tag", methods=["POST"])
@login_required
def detach_tag(username):
    data = request.json
    tag_id = data.get("tag_id")
    trip_ids = data.get("trip_ids")

    if not tag_id or not trip_ids:
        return jsonify({"error": "Invalid input"}), 400

    with pg_session() as pg:
        role = get_tag_role(pg, tag_id, username)
        if role is None:
            abort(401)
        if role != "owner":
            # Members may only detach their own trips; the owner may curate any
            owned = filter_owned_trip_ids(pg, trip_ids, username)
            trip_ids = [t for t in trip_ids if int(t) in owned]
        for trip_id in trip_ids:
            pg.execute(
                """
                    DELETE FROM tags_associations
                    WHERE tag_id = :tag_id AND trip_id = :trip_id
                """,
                {"tag_id": tag_id, "trip_id": trip_id},
            )
    return ""


def detach_member_trips(pg, tag_id, member_username):
    """Remove a member's own trips from a tag (on leave / removal)."""
    pg.execute(
        """
            DELETE FROM tags_associations ta
            USING trips t
            WHERE ta.tag_id = :tag_id
              AND ta.trip_id = t.trip_id
              AND t.user_id = :user_id
        """,
        {"tag_id": tag_id, "user_id": get_user_id(member_username)},
    )


@app.route("/u/<username>/tag/<int:tag_id>/invite", methods=["POST"])
@login_required
def invite_to_tag(username, tag_id):
    invitees = [u.strip() for u in request.form.getlist("friend_username") if u.strip()]
    userLang = lang[session["userinfo"]["lang"]]

    with pg_session() as pg:
        if get_tag_role(pg, tag_id, username) != "owner":
            abort(401)

        owner_uid = User.query.filter_by(username=username).first().uid
        friend_usernames = {
            row.username
            for row in authDb.session.query(User.username)
            .join(Friendship, User.uid == Friendship.friend_id)
            .filter(Friendship.user_id == owner_uid, Friendship.accepted != None)  # noqa: E711
            .all()
        }
        if not invitees or any(invitee not in friend_usernames for invitee in invitees):
            flash(userLang["tagInviteNotFriend"], "danger")
            return redirect(url_for("tag_list", username=username))

        existing = {
            row.username
            for row in pg.execute(
                "SELECT username FROM tag_members WHERE tag_id = :tag_id",
                {"tag_id": tag_id},
            ).fetchall()
        }
        to_invite = [invitee for invitee in invitees if invitee not in existing]

        for invitee in to_invite:
            pg.execute(
                "INSERT INTO tag_members (tag_id, username)"
                " VALUES (:tag_id, :username) ON CONFLICT DO NOTHING",
                {"tag_id": tag_id, "username": invitee},
            )

    if to_invite:
        flash(userLang["tagInviteSent"], "success")
    else:
        flash(userLang["tagInviteExists"], "info")
    return redirect(url_for("tag_list", username=username))


@app.route("/u/<username>/tag/<int:tag_id>/respond", methods=["POST"])
@login_required
def respond_to_tag_invite(username, tag_id):
    action = request.form.get("action")
    userLang = lang[session["userinfo"]["lang"]]

    with pg_session() as pg:
        pending = pg.execute(
            "SELECT 1 FROM tag_members"
            " WHERE tag_id = :tag_id AND username = :username AND status = 'pending'",
            {"tag_id": tag_id, "username": username},
        ).fetchone()
        if pending is None:
            flash(userLang["tagInviteNotFound"], "danger")
            return redirect(url_for("tag_list", username=username))

        if action == "accept":
            pg.execute(
                "UPDATE tag_members"
                " SET status = 'accepted', responded_at = CURRENT_TIMESTAMP"
                " WHERE tag_id = :tag_id AND username = :username",
                {"tag_id": tag_id, "username": username},
            )
            flash(userLang["tagInviteAccepted"], "success")
        else:
            pg.execute(
                "DELETE FROM tag_members"
                " WHERE tag_id = :tag_id AND username = :username",
                {"tag_id": tag_id, "username": username},
            )
            flash(userLang["tagInviteDeclined"], "success")

    return redirect(url_for("tag_list", username=username))


@app.route("/u/<username>/tag/<int:tag_id>/leave", methods=["POST"])
@login_required
def leave_tag(username, tag_id):
    userLang = lang[session["userinfo"]["lang"]]

    with pg_session() as pg:
        if get_tag_role(pg, tag_id, username) != "member":
            abort(401)
        pg.execute(
            "DELETE FROM tag_members WHERE tag_id = :tag_id AND username = :username",
            {"tag_id": tag_id, "username": username},
        )
        detach_member_trips(pg, tag_id, username)

    flash(userLang["tagLeft"], "success")
    return redirect(url_for("tag_list", username=username))


@app.route("/u/<username>/tag/<int:tag_id>/remove_member", methods=["POST"])
@login_required
def remove_tag_member(username, tag_id):
    member = (request.form.get("member_username") or "").strip()
    userLang = lang[session["userinfo"]["lang"]]

    with pg_session() as pg:
        if get_tag_role(pg, tag_id, username) != "owner":
            abort(401)
        deleted = pg.execute(
            "DELETE FROM tag_members"
            " WHERE tag_id = :tag_id AND username = :username RETURNING status",
            {"tag_id": tag_id, "username": member},
        ).fetchone()
        if deleted is None:
            flash(userLang["tagInviteNotFound"], "danger")
            return redirect(url_for("tag_list", username=username))
        if deleted["status"] == "accepted":
            detach_member_trips(pg, tag_id, member)

    flash(userLang["tagMemberRemoved"], "success")
    return redirect(url_for("tag_list", username=username))


@app.route("/u/<username>/new_ticket")
@login_required
def new_ticket(username):
    return render_template(
        "new_ticket.html",
        title=lang[session["userinfo"]["lang"]]["new_ticket"],
        country_list=get_all_countries(),
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
        currencyOptions=get_available_currencies(),
        user_currency=getLoggedUserCurrency(),
    )


@app.route("/u/<username>/handle_gpx_upload/<source>", methods=["POST"])
@login_required
def handle_gpx_upload(username, source):
    files = request.files.getlist("gpx_files")
    notes = request.form.get("notes", "")

    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    try:
        ingest_gpx_files(username, source, files, notes)
    except GpxIngestError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"message": "Files processed successfully"}), 200


@app.route("/api/gps/upload", methods=["GET", "POST"])
@app.route("/api/gps/upload/<trip_type>", methods=["GET", "POST"])
@app.route("/api/gps/upload/<trip_type>/<routing>", methods=["GET", "POST"])
@app.route("/api/gps/<token>/upload", methods=["GET", "POST"])
@app.route("/api/gps/<token>/upload/<trip_type>", methods=["GET", "POST"])
@app.route("/api/gps/<token>/upload/<trip_type>/<routing>", methods=["GET", "POST"])
def gps_logger_upload(token=None, trip_type=None, routing=None):
    """Token-authenticated GPX ingest for the mendhak GPSLogger app (via Tasker).

    The phone records offline and auto-sends recorded .gpx files on WiFi. No
    browser session exists, so auth is the per-user gps_token, accepted either in
    the path (/api/gps/<token>/upload) or as a query/form param (?key=<token>).

    URL shapes:
      - /upload                  -> stage to the `gpx` table for review (list_gpx)
      - /upload/<trip_type>      -> import directly as a finished trip
      - /upload/<trip_type>/routing -> import directly, snapped with smart routing
    Trip attributes can be preset via query params (operator, material_type,
    electric, line_name, seat, reg, ...); see parse_trip_params.

    GET is accepted so GPSLogger's URL validation / connectivity checks don't 404.
    """
    token = (
        token
        or request.args.get("key")
        or request.args.get("token")
        or request.form.get("key")
    )
    user = User.query.filter_by(gps_token=token).first() if token else None
    if not user:
        abort(401)

    if routing is not None and routing.lower() not in ("routing", "route"):
        abort(404)
    use_routing = routing is not None or str(
        request.values.get("routing", "")
    ).lower() in ("1", "true", "yes", "routing")

    if trip_type is not None and trip_type not in {t.value for t in TripTypes}:
        return (f"Unknown trip type: {trip_type}", 400)

    files = (
        request.files.getlist("gpx_files")
        or request.files.getlist("file")
        # Accept whatever field name the client (e.g. Tasker) uses for the upload.
        or list(request.files.values())
    )
    # Fall back to a raw GPX request body (Tasker/GPSLogger can POST the file body).
    if not files:
        body = request.get_data()
        if body and (b"<gpx" in body[:1024].lower() or b"<trk" in body[:1024].lower()):
            files = [FileStorage(stream=BytesIO(body), filename="gpslogger.gpx")]

    if files:
        cleaned_files = clean_tasker_gpx_files(files)
        try:
            if trip_type is None:
                # Default: stage for manual review/finalize via list_gpx.
                ingest_gpx_files(user.username, source="gpslogger", files=cleaned_files)
                return ("OK", 200)

            # Direct import: each file becomes a finished trip, prefilled from
            # the URL params, optionally snapped to the network with routing.
            params = parse_trip_params(request.args)
            rows = parse_gpx_files(cleaned_files, source="gpslogger", username=user.username)
            for row in rows:
                newTrip, path, altitude, timestamps = build_trip_payload(
                    row, trip_type, params, use_routing, request
                )
                saveTripToDb(
                    username=user.username,
                    newTrip=newTrip,
                    newPath=path,
                    trip_type=trip_type,
                    altitude=altitude,
                    timestamps=timestamps,
                )
            return (f"OK (imported {len(rows)} trip(s))", 200)
        except GpxIngestError as e:
            app.logger.exception("GPX ingest failed")
            return (str(e), 400)

    # No file (e.g. a bare GET ping / URL-validation check). Dump the *entire*
    # request so the exact client format can be inspected, and return 200 so the
    # uploader (Tasker/GPSLogger) marks it successful.
    body = request.get_data(as_text=True) or ""
    app.logger.info(
        "gps_logger_upload RAW DUMP from %s\n"
        "  method=%s\n"
        "  url=%s\n"
        "  query_string=%s\n"
        "  args=%s\n"
        "  form=%s\n"
        "  files=%s\n"
        "  content_type=%s content_length=%s\n"
        "  headers=%s\n"
        "  body=%s",
        user.username,
        request.method,
        request.url,
        request.query_string.decode("utf-8", "replace"),
        dict(request.args),
        dict(request.form),
        list(request.files.keys()),
        request.content_type,
        request.content_length,
        dict(request.headers),
        body[:2000],
    )
    return ("OK (no GPX received)", 200)

@app.route("/u/<username>/upload_gpx")
@login_required
def upload_gpx(username):
    user_lang = lang[session["userinfo"]["lang"]]
    trip_types = {
        "train": user_lang["train"],
        "tram": user_lang["tram"],
        "metro": user_lang["metro"],
        "funicular": user_lang["funicular"],
        "rail": user_lang["rail"],
        "bus": user_lang["bus"],
        "ferry": user_lang["ferry"],
        "car": user_lang["car"],
        "cycle": user_lang["cycle"],
        "scooter": user_lang["scooter"],
        "walk": user_lang["walk"],
        "aerialway": user_lang["aerialway"],
        "ski": user_lang["ski"],
        "other": user_lang["other"],
        "air": user_lang["air"],
        "helicopter": user_lang["helicopter"],
    }
    return render_template(
        "upload_gpx.html",
        title=user_lang["upload_gpx_files"],
        username=username,
        trip_types=trip_types,
        l=user_lang,
        **user_lang,
        **session["userinfo"],
    )


@app.route("/u/<username>/update_gpx", methods=["POST"])
@login_required
def update_gpx(username):
    data = request.json
    gpx_id = data.get("gpx_id")
    if not gpx_id:
        return jsonify({"error": "Invalid request"}), 400

    origin = data.get("origin")
    destination = data.get("destination")
    start_time = data.get("start_time")  # "YYYY-MM-DD HH:MM" or None
    end_time = data.get("end_time")

    with pg_session() as pg:
        row = pg.execute(
            "SELECT duration FROM gpx WHERE uid = :uid AND username = :username",
            {"uid": gpx_id, "username": username},
        ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        new_duration = None
        if (
            row["duration"] is None
            and origin
            and destination
            and start_time
            and end_time
        ):
            try:
                s = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
                e = datetime.strptime(end_time, "%Y-%m-%d %H:%M")
                new_duration = int((e - s).total_seconds())
            except ValueError:
                pass

        if new_duration is not None:
            pg.execute(
                """
                UPDATE gpx
                   SET origin      = :origin,
                       destination = :destination,
                       start_time  = :start_time,
                       end_time    = :end_time,
                       duration    = :duration
                 WHERE uid = :uid
                   AND username = :username
            """,
                {
                    "origin": origin,
                    "destination": destination,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration": new_duration,
                    "uid": gpx_id,
                    "username": username,
                },
            )
        else:
            pg.execute(
                """
                UPDATE gpx
                   SET origin      = :origin,
                       destination = :destination,
                       start_time  = :start_time,
                       end_time    = :end_time
                 WHERE uid = :uid
                   AND username = :username
            """,
                {
                    "origin": origin,
                    "destination": destination,
                    "start_time": start_time,
                    "end_time": end_time,
                    "uid": gpx_id,
                    "username": username,
                },
            )

    return jsonify({"success": True})


@app.route("/u/<username>/list_gpx", methods=["GET"])
@login_required
def list_gpx(username):
    with pg_session() as pg:
        gpx_files = [
            dict(row._mapping)
            for row in pg.execute(
                """
                SELECT *
                FROM gpx
                WHERE username = :username
                ORDER BY start_time DESC
            """,
                {"username": username},
            ).fetchall()
        ]

    trip_types = {
        "train": lang[session["userinfo"]["lang"]]["train"],
        "tram": lang[session["userinfo"]["lang"]]["tram"],
        "metro": lang[session["userinfo"]["lang"]]["metro"],
        "funicular": lang[session["userinfo"]["lang"]]["funicular"],
        "rail": lang[session["userinfo"]["lang"]]["rail"],
        "bus": lang[session["userinfo"]["lang"]]["bus"],
        "ferry": lang[session["userinfo"]["lang"]]["ferry"],
        "car": lang[session["userinfo"]["lang"]]["car"],
        "cycle": lang[session["userinfo"]["lang"]]["cycle"],
        "scooter": lang[session["userinfo"]["lang"]]["scooter"],
        "walk": lang[session["userinfo"]["lang"]]["walk"],
        "aerialway": lang[session["userinfo"]["lang"]]["aerialway"],
        "ski": lang[session["userinfo"]["lang"]]["ski"],
        "other": lang[session["userinfo"]["lang"]]["other"],
        "air": lang[session["userinfo"]["lang"]]["air"],
        "helicopter": lang[session["userinfo"]["lang"]]["helicopter"],
    }

    # Pass the GPX files to the template
    return render_template(
        "list_gpx.html",
        title=lang[session["userinfo"]["lang"]]["manage_gpx_files"],
        trip_types=trip_types,
        username=username,
        gpxList=gpx_files,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/delete_gpx/<gpx_id>", methods=["POST"])
@login_required
def delete_gpx(username, gpx_id):
    with pg_session() as pg:
        pg.execute(
            """
            DELETE FROM gpx
            WHERE uid = :uid AND username = :username
        """,
            {"uid": gpx_id, "username": username},
        )

    return redirect(url_for("list_gpx", username=username))


@app.route("/u/<username>/save_trip_from_gpx/<gpx_id>", methods=["POST"])
@login_required
def saveTripFromGPX(username, gpx_id):
    request_data = request.get_json()
    trip_type = request_data.get("type", "train")
    use_routing = request_data.get("use_routing", False)

    # Retrieve the GPX data from the database
    with pg_session() as pg:
        gpx = pg.execute(
            "SELECT * FROM gpx WHERE uid = :uid AND username = :username",
            {"uid": gpx_id, "username": username},
        ).fetchone()

    if not gpx:
        return jsonify(
            {"error": "GPX file not found or does not belong to the user."}
        ), 404

    row = dict(gpx._mapping)
    raw_count = len(json.loads(row["path"]))
    newTrip, path, altitude, timestamps = build_trip_payload(
        row, trip_type, parse_trip_params(request.args), use_routing, request
    )

    # Delete the GPX file after saving as a trip
    with pg_session() as pg:
        pg.execute(
            "DELETE FROM gpx WHERE uid = :uid AND username = :username",
            {"uid": gpx_id, "username": username},
        )

    newTrip["route_source"] = "gpx_routed" if use_routing else "gpx"
    saveTripToDb(
        username=username,
        newTrip=newTrip,
        newPath=path,
        trip_type=trip_type,
        altitude=altitude,
        timestamps=timestamps,
    )

    return jsonify({
        "success": True,
        "message": f"Trip saved with {'smart routing' if use_routing else 'original path'}",
        "points_processed": raw_count,
        "final_points": len(path),
    }), 200


@app.route("/u/<username>/preview_smart_routing/<gpx_id>/<trip_type>", methods=["POST", "GET"])
@login_required  
def previewSmartRouting(username, gpx_id, trip_type):
    """
    Preview smart routing results without saving the trip
    GET: Shows interactive map with original vs cleaned route
    POST: Returns JSON data for API usage
    """
   
    # Retrieve GPX data
    with pg_session() as pg:
        gpx = pg.execute(
            "SELECT * FROM gpx WHERE uid = :uid AND username = :username",
            {"uid": gpx_id, "username": username},
        ).fetchone()
    
    if not gpx:
        if request.method == "GET":
            return render_template('error.html', error="GPX file not found"), 404
        return jsonify({"error": "GPX file not found"}), 404

    # Convert to waypoints format
    raw_waypoints = [
        {"lat": point[0], "lng": point[1]} for point in json.loads(gpx["path"])
    ]
   
    # Clean the GPS route with smart routing
    cleaning_result = clean_gps_route(
        raw_waypoints=raw_waypoints,
        forwardRouting=lambda path, routingType, options=None: forward_routing_core(routingType=routingType, path=path, flask_request=request, extra_args=options),
        trip_type=trip_type,
        deviation_threshold=800,       # Kept: Now defines the "validation corridor" width
        max_search_points=75
    )
   
    if request.method == "POST":
        # Return JSON for API usage
        if cleaning_result["success"]:
            return jsonify({
                "success": True,
                "original_points": len(raw_waypoints),
                "cleaned_points": len(cleaning_result["path"]),
                "waypoints_count": len(cleaning_result["waypoints"]),
                "compression_ratio": cleaning_result["compression_ratio"],
                "reroute_count": cleaning_result["reroute_count"],
                "distance": cleaning_result["distance"],
                "duration": cleaning_result["duration"],
                "preview_path": cleaning_result["path"][:100]  # First 100 points for preview
            })
        else:
            return jsonify({
                "success": False,
                "error": cleaning_result["error"],
                "fallback_points": len(raw_waypoints)
            })
    
    # GET request: Show interactive map
    return render_template('preview_route.html',
                         gpx=gpx,
                         trip_type=trip_type,
                         raw_waypoints=json.dumps(raw_waypoints),
                         cleaning_result=json.dumps(cleaning_result),
                         success=cleaning_result["success"])


def parse_maprika_filename(filename):
    """
    Parse Maprika GPX filename format: @2026-01-17 08.40, Skiing @ Serre Chevalier opensnowmap.gpx
    Returns: dict with 'date', 'time', 'name' or None if not Maprika format
    """
    import re
    # Pattern: @YYYY-MM-DD HH.MM, Name.gpx
    pattern = r'^@(\d{4}-\d{2}-\d{2})\s+(\d{2}\.\d{2}),\s+(.+)\.gpx$'
    match = re.match(pattern, filename)
    if match:
        date_str = match.group(1)
        time_str = match.group(2).replace('.', ':')  # Convert 08.40 to 08:40
        name = match.group(3)
        return {
            'date': date_str,
            'time': time_str,
            'name': name,
            'datetime': f"{date_str} {time_str}"
        }
    return None


@app.route("/u/<username>/upload_gpx_advanced")
@login_required
def upload_gpx_advanced(username):
    return redirect(url_for("upload_gpx", username=username))


@app.route("/u/<username>/parse_gpx_advanced", methods=["POST"])
@login_required
def parse_gpx_advanced(username):
    """Parse multiple GPX files and return trip data, including Maprika segments"""
    files = request.files.getlist("files")

    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    trips = []

    for file in files:
        if not file.filename.endswith(".gpx"):
            continue

        try:
            # Read file content
            file_content = file.read()
            file.seek(0)

            # Parse Maprika filename if applicable
            maprika_data = parse_maprika_filename(file.filename)

            # Parse GPX content
            gpx = gpxpy.parse(file)

            # Collect all track points
            all_points = []
            if gpx.tracks:
                for track in gpx.tracks:
                    for segment in track.segments:
                        all_points.extend(segment.points)

            if not all_points:
                continue

            # Try to parse Maprika segments
            maprika_segments = []
            try:
                root = ET.fromstring(file_content)
                # Find maprika:log section
                namespaces = {'maprika': 'http://www.maprika.com/gpx'}
                log = root.find('.//maprika:log', namespaces)

                if log is not None:
                    for seg in log.findall('maprika:segment', namespaces):
                        seg_type = int(seg.get('type', 0))
                        seg_name = seg.get('name', '')
                        start_time_unix = int(seg.get('startTime', 0))
                        moving_time = int(seg.get('movingTime', 0))
                        index_begin = int(seg.get('indexBegin', 0))
                        index_end = int(seg.get('indexEnd', 0))

                        # Map Maprika types to Trainlog types
                        # 1=chairlift, 7=gondola, 8=bus, 9=ski lift
                        # 3=easy run, 4=medium run, 5=difficult run
                        # 2=unknown/walking, 6=break
                        if seg_type in [1, 7, 9]:  # Lifts and gondolas
                            trip_type = 'aerialway'
                        elif seg_type == 8:  # Bus
                            trip_type = 'bus'
                        elif seg_type in [3, 4, 5]:  # Ski runs
                            trip_type = 'ski'
                        elif seg_type == 2:  # Unknown/walking
                            trip_type = 'walk'
                        elif seg_type == 6:  # Break
                            continue  # Skip breaks
                        else:
                            trip_type = 'other'

                        maprika_segments.append({
                            'name': seg_name,
                            'type': trip_type,
                            'start_time_unix': start_time_unix,
                            'duration': moving_time,
                            'index_begin': index_begin,
                            'index_end': index_end
                        })
            except Exception as e:
                logger.info(f"No Maprika segments found in {file.filename}: {str(e)}")

            # If Maprika segments found, create trips from segments
            if maprika_segments:
                for seg in maprika_segments:
                    try:
                        # Extract points for this segment
                        seg_points = all_points[seg['index_begin']:seg['index_end'] + 1]

                        if not seg_points or len(seg_points) < 2:
                            continue

                        # Calculate distance
                        distance = 0
                        for i in range(1, len(seg_points)):
                            distance += getDistance(
                                {"lat": seg_points[i - 1].latitude, "lng": seg_points[i - 1].longitude},
                                {"lat": seg_points[i].latitude, "lng": seg_points[i].longitude},
                            )

                        # Get start and end points
                        start_point = seg_points[0]
                        end_point = seg_points[-1]

                        # Geocode
                        origin = getAddressFromCoords(lat=start_point.latitude, lng=start_point.longitude)
                        destination = getAddressFromCoords(lat=end_point.latitude, lng=end_point.longitude)

                        # Convert Unix timestamp to datetime
                        from datetime import datetime
                        start_datetime = datetime.fromtimestamp(seg['start_time_unix'], UTC)
                        end_datetime = datetime.fromtimestamp(seg['start_time_unix'] + seg['duration'], UTC)

                        # Convert to local time
                        start_time_local = getLocalDatetime(start_point.latitude, start_point.longitude, start_datetime)
                        end_time_local = getLocalDatetime(end_point.latitude, end_point.longitude, end_datetime)

                        formatted_start_time = start_time_local.strftime("%Y-%m-%d %H:%M")
                        formatted_end_time = end_time_local.strftime("%Y-%m-%d %H:%M")

                        # Generate path
                        path = [[point.latitude, point.longitude] for point in seg_points]

                        # Build trip data
                        trip_data = {
                            'origin': origin,
                            'destination': destination,
                            'start_time': formatted_start_time,
                            'end_time': formatted_end_time,
                            'duration': seg['duration'],
                            'distance': int(distance),
                            'path': path,
                            'maprika_name': seg['name'] or (maprika_data['name'] if maprika_data else None),
                            'gpx_name': seg['name'],
                            'notes': f"Segment from {file.filename}",
                            'suggested_type': seg['type'],
                            'filename': file.filename
                        }

                        trips.append(trip_data)
                    except Exception as e:
                        logger.error(f"Error processing segment in {file.filename}: {str(e)}")
                        continue
            else:
                # No Maprika segments - treat as single trip (original behavior)
                points = all_points
                start_time = None
                end_time = None
                distance = 0

                if points[0].time and points[-1].time:
                    start_time = points[0].time
                    end_time = points[-1].time

                # Calculate distance
                for i in range(1, len(points)):
                    distance += getDistance(
                        {"lat": points[i - 1].latitude, "lng": points[i - 1].longitude},
                        {"lat": points[i].latitude, "lng": points[i].longitude},
                    )

                # Extract start and end points
                start_point = points[0]
                end_point = points[-1]

                # Geocode
                origin = getAddressFromCoords(lat=start_point.latitude, lng=start_point.longitude)
                destination = getAddressFromCoords(lat=end_point.latitude, lng=end_point.longitude)

                # Calculate duration
                duration = 0
                formatted_start_time = None
                formatted_end_time = None

                if start_time and end_time:
                    duration = int((end_time - start_time).total_seconds())
                    start_time_local = getLocalDatetime(start_point.latitude, start_point.longitude, start_time)
                    end_time_local = getLocalDatetime(end_point.latitude, end_point.longitude, end_time)
                    formatted_start_time = start_time_local.strftime("%Y-%m-%d %H:%M")
                    formatted_end_time = end_time_local.strftime("%Y-%m-%d %H:%M")
                elif maprika_data:
                    formatted_start_time = maprika_data['datetime']

                # Generate path
                path = [[point.latitude, point.longitude] for point in points]

                # Determine suggested trip type
                suggested_type = "other"
                if maprika_data and maprika_data['name']:
                    name_lower = maprika_data['name'].lower()
                    if 'ski' in name_lower or 'skiing' in name_lower:
                        suggested_type = 'ski'
                    elif 'cycle' in name_lower or 'bike' in name_lower:
                        suggested_type = 'cycle'
                    elif 'walk' in name_lower or 'hike' in name_lower:
                        suggested_type = 'walk'

                # Build trip data
                trip_data = {
                    'origin': origin,
                    'destination': destination,
                    'start_time': formatted_start_time,
                    'end_time': formatted_end_time,
                    'duration': duration,
                    'distance': int(distance),
                    'path': path,
                    'maprika_name': maprika_data['name'] if maprika_data else None,
                    'gpx_name': gpx.tracks[0].name if gpx.tracks else None,
                    'notes': gpx.tracks[0].description if gpx.tracks and gpx.tracks[0].description else '',
                    'suggested_type': suggested_type,
                    'filename': file.filename
                }

                trips.append(trip_data)

        except Exception as e:
            logger.error(f"Error parsing {file.filename}: {str(e)}")
            traceback.print_exc()
            continue

    return jsonify({"trips": trips}), 200


@app.route("/u/<username>/parse_gpx_stream", methods=["POST"])
@login_required
def parse_gpx_stream(username):
    """Stream GPX parsing progress using Server-Sent Events"""
    files = request.files.getlist("files")

    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    # Read all file contents upfront before generator starts
    file_data = []
    for file in files:
        if file.filename.endswith(".gpx"):
            file_data.append({
                'filename': file.filename,
                'content': file.read()
            })

    def generate():
        """Generator that yields trip data as SSE"""
        all_trips = []
        total_segments = 0
        processed_segments = 0

        # First pass: count total segments
        for file_info in file_data:
            try:
                root = ET.fromstring(file_info['content'])
                namespaces = {'maprika': 'http://www.maprika.com/gpx'}
                log = root.find('.//maprika:log', namespaces)

                if log is not None:
                    segments = log.findall('maprika:segment', namespaces)
                    # Don't count type 6 (breaks)
                    total_segments += sum(1 for seg in segments if int(seg.get('type', 0)) != 6)
                else:
                    total_segments += 1  # Non-Maprika file = 1 trip
            except:
                pass

        # Send total count
        yield f"data: {json.dumps({'type': 'total', 'count': total_segments})}\n\n"

        # Second pass: process and stream each segment
        for file_info in file_data:
            try:
                from io import BytesIO
                file_content = file_info['content']
                filename = file_info['filename']

                maprika_data = parse_maprika_filename(filename)
                gpx = gpxpy.parse(BytesIO(file_content))

                all_points = []
                if gpx.tracks:
                    for track in gpx.tracks:
                        for segment in track.segments:
                            all_points.extend(segment.points)

                if not all_points:
                    continue

                # Parse Maprika segments
                maprika_segments = []
                try:
                    root = ET.fromstring(file_content)
                    namespaces = {'maprika': 'http://www.maprika.com/gpx'}
                    log = root.find('.//maprika:log', namespaces)

                    if log is not None:
                        for seg in log.findall('maprika:segment', namespaces):
                            seg_type = int(seg.get('type', 0))
                            if seg_type == 6:  # Skip breaks
                                continue

                            seg_name = seg.get('name', '')
                            start_time_unix = int(seg.get('startTime', 0))
                            moving_time = int(seg.get('movingTime', 0))
                            index_begin = int(seg.get('indexBegin', 0))
                            index_end = int(seg.get('indexEnd', 0))

                            if seg_type in [1, 7, 9]:
                                trip_type = 'aerialway'
                            elif seg_type == 8:
                                trip_type = 'bus'
                            elif seg_type in [3, 4, 5]:
                                trip_type = 'ski'
                            elif seg_type == 2:
                                trip_type = 'walk'
                            else:
                                trip_type = 'other'

                            maprika_segments.append({
                                'name': seg_name,
                                'type': trip_type,
                                'start_time_unix': start_time_unix,
                                'duration': moving_time,
                                'index_begin': index_begin,
                                'index_end': index_end
                            })
                except Exception as e:
                    logger.info(f"No Maprika segments: {str(e)}")

                # Process each segment and stream
                if maprika_segments:
                    for seg in maprika_segments:
                        try:
                            processed_segments += 1
                            seg_points = all_points[seg['index_begin']:seg['index_end'] + 1]

                            if not seg_points or len(seg_points) < 2:
                                continue

                            # Send progress update
                            yield f"data: {json.dumps({'type': 'progress', 'current': processed_segments, 'total': total_segments, 'name': seg['name'] or 'Unknown'})}\n\n"

                            # Calculate distance
                            distance = 0
                            for i in range(1, len(seg_points)):
                                distance += getDistance(
                                    {"lat": seg_points[i - 1].latitude, "lng": seg_points[i - 1].longitude},
                                    {"lat": seg_points[i].latitude, "lng": seg_points[i].longitude},
                                )

                            start_point = seg_points[0]
                            end_point = seg_points[-1]

                            # Geocode (the slow part - but we're showing progress!)
                            origin = getAddressFromCoords(lat=start_point.latitude, lng=start_point.longitude)
                            destination = getAddressFromCoords(lat=end_point.latitude, lng=end_point.longitude)

                            from datetime import datetime
                            start_datetime = datetime.fromtimestamp(seg['start_time_unix'], UTC)
                            end_datetime = datetime.fromtimestamp(seg['start_time_unix'] + seg['duration'], UTC)
                            start_time_local = getLocalDatetime(start_point.latitude, start_point.longitude, start_datetime)
                            end_time_local = getLocalDatetime(end_point.latitude, end_point.longitude, end_datetime)

                            formatted_start_time = start_time_local.strftime("%Y-%m-%d %H:%M")
                            formatted_end_time = end_time_local.strftime("%Y-%m-%d %H:%M")

                            path = [[point.latitude, point.longitude] for point in seg_points]

                            trip_data = {
                                'origin': origin,
                                'destination': destination,
                                'start_time': formatted_start_time,
                                'end_time': formatted_end_time,
                                'duration': seg['duration'],
                                'distance': int(distance),
                                'path': path,
                                'maprika_name': seg['name'] or (maprika_data['name'] if maprika_data else None),
                                'gpx_name': seg['name'],
                                'notes': f"Segment from {filename}",
                                'suggested_type': seg['type'],
                                'filename': filename
                            }

                            all_trips.append(trip_data)

                            # Stream the completed trip
                            yield f"data: {json.dumps({'type': 'trip', 'trip': trip_data})}\n\n"

                        except Exception as e:
                            logger.error(f"Error processing segment: {str(e)}")
                            continue
                else:
                    # Process as single trip (non-Maprika)
                    processed_segments += 1
                    yield f"data: {json.dumps({'type': 'progress', 'current': processed_segments, 'total': total_segments, 'name': filename})}\n\n"
                    # ... (similar processing for non-Maprika files)

            except Exception as e:
                logger.error(f"Error parsing {filename}: {str(e)}")
                continue

        # Send completion
        yield f"data: {json.dumps({'type': 'done', 'count': len(all_trips)})}\n\n"

    return app.response_class(generate(), mimetype='text/event-stream')


@app.route("/u/<username>/save_gpx_advanced", methods=["POST"])
@login_required
def save_gpx_advanced(username):
    """Save selected trips from batch GPX upload"""
    data = request.get_json()
    trips = data.get("trips", [])

    if not trips:
        return jsonify({"error": "No trips provided"}), 400

    saved_count = 0

    for trip in trips:
        try:
            trip_type = trip.get("type", "other")
            origin = trip.get("origin")
            destination = trip.get("destination")
            start_time = trip.get("start_time")
            end_time = trip.get("end_time")
            duration = trip.get("duration", 0)
            distance = trip.get("distance", 0)
            path_raw = trip.get("path", [])
            notes = trip.get("notes", "")

            # Convert path from [[lat, lng], ...] to [{"lat": lat, "lng": lng}, ...]
            path = [{"lat": point[0], "lng": point[1]} for point in path_raw]

            # Add Maprika name to notes if present
            if trip.get("maprika_name"):
                notes = f"{trip['maprika_name']}\n{notes}".strip()

            # Determine precision
            precision = "preciseDates" if start_time else "unknown"

            # Convert datetime format if needed
            if start_time and start_time != -1:
                start_time = start_time.replace(" ", "T")
            else:
                start_time = -1

            if end_time and end_time != -1:
                end_time = end_time.replace(" ", "T")
            else:
                end_time = -1

            # Build trip structure
            newTrip = {
                "type": trip_type,
                "originStation": [None, origin],
                "destinationStation": [None, destination],
                "newTripStart": start_time,
                "newTripEnd": end_time,
                "trip_length": distance,
                "estimated_trip_duration": duration,
                "operator": "",
                "lineName": "",
                "price": None,
                "currency": None,
                "purchasing_date": None,
                "precision": precision,
                "notes": notes,
                "onlyDateDuration": "",
                "unknownType": "past",
                "waypoints": json.dumps([]),
                "route_source": "gpx",
            }

            # Save trip
            saveTripToDb(username=username, newTrip=newTrip, newPath=path, trip_type=trip_type)
            saved_count += 1

        except Exception as e:
            logger.error(f"Error saving trip: {str(e)}")
            continue

    return jsonify({"success": True, "count": saved_count}), 200


@app.route("/u/<username>/submit_ticket", methods=["POST"])
@login_required
def submit_ticket(username):
    name = request.form["name"]
    price = request.form["price"]
    currency = request.form["currency"]
    purchasing_date = request.form["purchasing_date"]
    notes = request.form.get("notes", "")
    active_countries = request.form.getlist("active_countries[]")
    active_countries_str = ",".join(active_countries) if active_countries else None

    if not name or not username or not price:
        flash("Name, Username, and Price are required!")
        return redirect(url_for("new_ticket"))

    with pg_session() as pg:
        pg.execute(
            "INSERT INTO tickets (name, username, price, currency, purchasing_date, notes, active_countries)"
            " VALUES (:name, :username, :price, :currency, :purchasing_date, :notes, :active_countries)",
            {
                "name": name,
                "username": username,
                "price": float(price),
                "currency": currency,
                "purchasing_date": purchasing_date,
                "notes": notes,
                "active_countries": active_countries_str,
            },
        )
    return redirect(url_for("ticket_list", username=username))


@app.route("/u/<username>/edit_ticket", methods=["POST"])
@login_required
def edit_ticket(username):
    ticket_id = request.form["ticket_id"]
    name = request.form["name"]
    price = request.form["price"]
    currency = request.form["currency"]
    purchasing_date = request.form["purchasing_date"]
    notes = request.form.get("notes", "")
    active_countries = request.form.getlist("active_countries[]")
    active_countries_str = ",".join(active_countries) if active_countries else None

    if not name or not price or not purchasing_date:
        return jsonify(
            success=False, error="Name, Price, and Purchasing Date are required."
        )

    with pg_session() as pg:
        pg.execute(
            "UPDATE tickets SET name = :name, price = :price, currency = :currency,"
            " purchasing_date = :purchasing_date, notes = :notes,"
            " active_countries = :active_countries"
            " WHERE uid = :uid AND username = :username",
            {
                "name": name,
                "price": float(price),
                "currency": currency,
                "purchasing_date": purchasing_date,
                "notes": notes,
                "active_countries": active_countries_str,
                "uid": ticket_id,
                "username": username,
            },
        )

    return jsonify(success=True)


def convert_to_user_currency(amount, base_currency, target_currency, date):
    if amount is None or amount == "":
        return ""
    return get_exchange_rate(
        price=amount,
        base_currency=base_currency,
        target_currency=target_currency,
        date=date,
    )
    
@app.route("/convert_currency", methods=["POST"])
def convert_currency():
    data = request.get_json()
    amount = data.get("amount")
    base_currency = data.get("base_currency")
    target_currency = data.get("target_currency")
    date = data.get("date")

    converted_amount = convert_to_user_currency(amount, base_currency, target_currency, date)
    return jsonify({"converted_amount": converted_amount})


@app.route("/u/<username>/ticket_list")
@login_required
def ticket_list(username):
    with pg_session() as pg:
        tickets = [
            dict(row._mapping)
            for row in pg.execute(
                get_tickets_query(), {"username": username}
            ).fetchall()
        ]

    result = []
    user_currency = getLoggedUserCurrency()

    for ticket in tickets:
        # Avoid colons and ampersand to avoid breaking datatables
        end_ticket = {
            k: (
                v.replace(":", "∶").replace("&", "＆")
                if isinstance(v, str) and k in ("notes", "name")
                else v
            )
            for k, v in ticket.items()
        }
        end_ticket["user_currency"] = user_currency

        # Convert basic price
        end_ticket["price_in_user_currency"] = convert_to_user_currency(
            ticket["price"],
            ticket["currency"],
            user_currency,
            ticket["purchasing_date"],
        )

        if ticket["trip_count"] > 0:
            # Calculate country-specific price_per_km if active_countries is set
            if ticket["active_countries"]:
                with pg_session() as pg:
                    trips = pg.execute(
                        "SELECT trip_id AS uid, countries FROM trips WHERE ticket_id = :tid",
                        {"tid": ticket["uid"]},
                    ).fetchall()

                active_countries = set(ticket["active_countries"].split(","))
                trips_in_active_countries = []
                total_distance = 0

                for trip in trips:
                    countries_data = json.loads(trip["countries"])
                    
                    # Check if any active country is in this trip
                    has_active_country = any(
                        country in active_countries
                        for country in countries_data.keys()
                    )
                    
                    if has_active_country:
                        trips_in_active_countries.append(trip)
                        
                        # Calculate distance based on format
                        for country, value in countries_data.items():
                            if country in active_countries:
                                if isinstance(value, dict):
                                    # New format: {FR: {elec: 50, nonelec: 50}}
                                    total_distance += sum(value.values())
                                else:
                                    # Old format: {FR: 100}
                                    total_distance += value

                end_ticket["trip_count"] = len(trips_in_active_countries)
                end_ticket["trip_ids"] = ",".join(
                    [str(trip["uid"]) for trip in trips_in_active_countries]
                )
                if len(trips_in_active_countries) > 0:
                    end_ticket["price_per_trip"] = ticket["price"] / len(
                        trips_in_active_countries
                    )
                    end_ticket["price_per_trip_in_user_currency"] = (
                        convert_to_user_currency(
                            end_ticket["price_per_trip"],
                            ticket["currency"],
                            user_currency,
                            ticket["purchasing_date"],
                        )
                    )
                else:
                    end_ticket["price_per_trip"] = ""
                    end_ticket["price_per_trip_in_user_currency"] = ""

                if total_distance > 0:
                    end_ticket["price_per_km"] = ticket["price"] / (
                        total_distance / 1000
                    )
                    end_ticket["price_per_km_in_user_currency"] = (
                        convert_to_user_currency(
                            end_ticket["price_per_km"],
                            ticket["currency"],
                            user_currency,
                            ticket["purchasing_date"],
                        )
                    )
                else:
                    end_ticket["price_per_km"] = ""
                    end_ticket["price_per_km_in_user_currency"] = ""
            else:
                end_ticket["price_per_trip_in_user_currency"] = (
                    convert_to_user_currency(
                        ticket["price_per_trip"],
                        ticket["currency"],
                        user_currency,
                        ticket["purchasing_date"],
                    )
                )
                # Use SQL-calculated price_per_km when no countries specified
                end_ticket["price_per_km_in_user_currency"] = convert_to_user_currency(
                    ticket["price_per_km"],
                    ticket["currency"],
                    user_currency,
                    ticket["purchasing_date"],
                )
        else:
            end_ticket["price_per_trip_in_user_currency"] = ""
            end_ticket["price_per_km_in_user_currency"] = ""

        result.append(end_ticket)

    return render_template(
        "ticket_list.html",
        title=lang[session["userinfo"]["lang"]]["ticket_list"],
        tickets=result,
        username=username,
        country_list=get_all_countries(),
        currencyOptions=get_available_currencies(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/tag_list")
@login_required
def tag_list(username):
    with pg_session() as pg:
        rows = pg.execute(
            """
            WITH UTC_Filtered AS (
                SELECT *,
                    COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
                    COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime
                FROM trips
            )
            SELECT t.*,
                   (t.username = :username) AS is_owner,
                   EXISTS (
                       SELECT 1 FROM tag_members tm
                       WHERE tm.tag_id = t.uid AND tm.status = 'accepted'
                   ) AS is_shared,
                   MAX(uf.utc_filtered_end_datetime) AS latest_trip_end,
                   COUNT(DISTINCT ta.trip_id) AS trip_count,
                   SUM(
                       CASE
                           WHEN uf.utc_filtered_start_datetime IS NOT NULL
                                AND uf.utc_filtered_end_datetime IS NOT NULL
                           THEN EXTRACT(EPOCH FROM (uf.utc_filtered_end_datetime - uf.utc_filtered_start_datetime))
                           WHEN uf.manual_trip_duration IS NOT NULL
                           THEN uf.manual_trip_duration
                           ELSE uf.estimated_trip_duration
                       END
                   ) AS total_trip_duration,
                   SUM(uf.trip_length) AS total_trip_length
            FROM tags t
            LEFT JOIN tags_associations ta ON t.uid = ta.tag_id
            LEFT JOIN UTC_Filtered uf ON ta.trip_id = uf.trip_id
            WHERE t.username = :username
               OR t.uid IN (
                    SELECT tag_id FROM tag_members
                    WHERE username = :username AND status = 'accepted'
               )
            GROUP BY t.uid
            ORDER BY
                CASE
                    WHEN MAX(uf.utc_filtered_end_datetime) IS NULL AND COUNT(ta.trip_id) = 0 THEN 1
                    WHEN MAX(uf.utc_filtered_end_datetime) IS NULL THEN 3
                    ELSE 2
                END,
                MAX(uf.utc_filtered_end_datetime) DESC NULLS LAST
            """,
            {"username": username},
        ).fetchall()
        tags = [dict(tag._mapping) for tag in rows]

        member_rows = pg.execute(
            """
            SELECT tm.tag_id, tm.username, tm.status
            FROM tag_members tm
            JOIN tags t ON tm.tag_id = t.uid
            WHERE t.username = :username
               OR t.uid IN (
                    SELECT tag_id FROM tag_members
                    WHERE username = :username AND status = 'accepted'
               )
            ORDER BY tm.status, tm.username
            """,
            {"username": username},
        ).fetchall()
        members_by_tag = {}
        for row in member_rows:
            members_by_tag.setdefault(row["tag_id"], []).append(dict(row._mapping))

        pending_invites = [
            dict(row._mapping)
            for row in pg.execute(
                """
                SELECT tm.tag_id, t.name, t.colour, t.username AS owner
                FROM tag_members tm
                JOIN tags t ON tm.tag_id = t.uid
                WHERE tm.username = :username AND tm.status = 'pending'
                """,
                {"username": username},
            ).fetchall()
        ]

        own_colours = pg.execute(
            "SELECT colour FROM tags WHERE username = :username", {"username": username}
        ).fetchall()

    user_uid = User.query.filter_by(username=username).first().uid
    friend_usernames = [
        row.username
        for row in authDb.session.query(User.username)
        .join(Friendship, User.uid == Friendship.friend_id)
        .filter(Friendship.user_id == user_uid, Friendship.accepted != None)  # noqa: E711
        .order_by(User.username)
        .all()
    ]

    return render_template(
        "tag_list.html",
        title=lang[session["userinfo"]["lang"]]["manage_tags"],
        tagsList=tags,
        suggested_colour=generate_distinct_color([row[0] for row in own_colours]),
        members_by_tag=members_by_tag,
        pending_invites=pending_invites,
        friend_usernames=friend_usernames,
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/delete_tag/<tag_id>", methods=["POST"])
@login_required
def delete_tag(username, tag_id):
    with pg_session() as pg:
        owner_row = pg.execute(
            "SELECT username FROM tags WHERE uid = :uid", {"uid": tag_id}
        ).fetchone()
        if owner_row is None or owner_row["username"] != username:
            abort(401)
        else:
            pg.execute("DELETE FROM tags WHERE uid = :uid", {"uid": tag_id})
            pg.execute(
                "DELETE FROM tags_associations WHERE tag_id = :uid", {"uid": tag_id}
            )
            pg.execute(
                "DELETE FROM tag_members WHERE tag_id = :uid", {"uid": tag_id}
            )
    return redirect(url_for("tag_list", username=username))


@app.route("/u/<username>/update_tag/<tag_id>", methods=["POST"])
@login_required
def update_tag(username, tag_id):
    tag_name = request.form["name"]
    tag_colour = request.form["colour"]
    tag_type = request.form["type"]
    with pg_session() as pg:
        owner_row = pg.execute(
            "SELECT username FROM tags WHERE uid = :uid", {"uid": tag_id}
        ).fetchone()
        if owner_row is None or owner_row["username"] != username:
            abort(401)
        else:
            pg.execute(
                "UPDATE tags SET name = :name, colour = :colour, type = :type WHERE uid = :uid",
                {"name": tag_name, "colour": tag_colour, "type": tag_type, "uid": tag_id},
            )
    return redirect(url_for("tag_list", username=username))


@app.route("/u/<username>/get_all_tickets")
@login_required
def get_all_tickets(username):
    with pg_session() as pg:
        tickets = pg.execute(get_tickets_query(), {"username": username}).fetchall()
    return jsonify(tickets=[dict(ticket._mapping) for ticket in tickets])


@app.route("/u/<username>/get_all_tags")
@login_required
def get_all_tags(username):
    with pg_session() as pg:
        tags = pg.execute(get_tags_query(), {"username": username}).fetchall()
    return jsonify(tags=[dict(tag._mapping) for tag in tags])


@app.route("/u/<username>/delete_ticket/<ticket_id>")
@login_required
def delete_ticket(username, ticket_id):
    success, error = delete_ticket_from_db(username, ticket_id)
    if success:
        return jsonify({"success": True}), 200
    else:
        logger.exception(error)
        return jsonify({"error": "An error occurred while deleting the ticket"}), 500


@app.route("/u/<username>/attachSelected")
@login_required
def attachSelected(username):
    trip_ids = request.args.get("trips")
    ticket_id = request.args.get("ticket_id")

    if not trip_ids or not ticket_id:
        return jsonify({"error": "Missing parameters"}), 400

    trip_id_list = trip_ids.split(",")
    success, error = attach_ticket_to_trips(username, ticket_id, trip_id_list)

    if success:
        return redirect(url_for("ticket_list", username=username))
    else:
        logger.exception(error)
        return jsonify({"error": "An error occurred while attaching the ticket"}), 500

@app.route("/u/<username>/bulkChangeVisibility")
@login_required
def bulkChangeVisibility(username):
    trip_ids = request.args.get("trips")
    visibility = request.args.get("visibility")

    if not trip_ids or visibility not in ("public", "friends", "private"):
        return jsonify({"error": "Missing parameters"}), 400

    trip_id_list = trip_ids.split(",")
    success, error = change_trips_visibility(username, visibility, trip_id_list)

    if success:
        return jsonify({"success": 1}), 200
    else:
        logger.exception(error)
        return jsonify({"error": "An error occured while changing the visibility"}), 500


@app.route("/u/<username>/bulkEditTrips", methods=["POST"])
@login_required
def bulkEditTrips(username):
    data = request.get_json()
    if not data or "trip_ids" not in data or "fields" not in data:
        return jsonify({"error": "Missing parameters"}), 400

    trip_ids = data["trip_ids"]
    fields = data.get("fields", {})
    notes_append = data.get("notes_append", False)
    time_offset_minutes = int(data.get("time_offset_minutes", 0) or 0)

    if not trip_ids or not isinstance(trip_ids, list):
        return jsonify({"error": "Invalid trip_ids"}), 400

    success, error = bulk_edit_trips(username, trip_ids, fields, notes_append, time_offset_minutes)

    if success:
        return jsonify({"success": 1}), 200
    else:
        logger.exception(error)
        return jsonify({"error": "An error occurred while editing trips"}), 500


@app.route("/u/<username>/toggle_ticket_active/<ticket_id>")
@login_required
def toggle_ticket_active(username, ticket_id):
    try:
        with pg_session() as pg:
            pg.execute(
                "UPDATE tickets SET active = NOT active WHERE username = :username AND uid = :uid",
                {"username": username, "uid": ticket_id},
            )

        # If no exceptions, return success
        return jsonify({"success": True}), 200
    except Exception as e:
        # Return an error message
        print(e)
        return jsonify({"error": "An error occurred while toggling the ticket"}), 500


@app.route("/u/<username>/new_flight")
@login_required
def new_flight(username):
    # Flights are now handled by the unified `new` form as the "air" vehicle type.
    return redirect(url_for("new", username=username, vehicle_type="air"))


@app.route("/u/<username>/routing", methods=['GET', 'POST'])
@login_required
def routing(username):
    trip_data = None
    from_app = request.args.get('fromApp') == 'true'
    if request.method == 'POST':
        trip_data = request.form.get('trip_data') or request.get_json()
        if isinstance(trip_data, dict):
            trip_data = json.dumps(trip_data)
    
    user_obj = User.query.filter_by(username=username).first()
    colorblind = getattr(user_obj, "colorblind", False) if user_obj else False

    return render_template(
        "routing.html",
        title=lang[session["userinfo"]["lang"]]["routeTrip"],
        username=username,
        trip_data=trip_data,
        colorblind=colorblind,
        from_app=from_app,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )

@app.route("/u/<username>/air_routing/<type>", methods=['GET', 'POST'])
@login_required
def air_routing(username, type):
    trip_data = None
    from_app = request.args.get('fromApp') == 'true'
    if request.method == 'POST':
        trip_data = request.form.get('trip_data') or request.get_json()
        if isinstance(trip_data, dict):
            trip_data = json.dumps(trip_data)
    return render_template(
        "air_routing.html",
        title=lang[session["userinfo"]["lang"]]["routeTrip"],
        type=type,
        username=username,
        trip_data=trip_data,
        from_app=from_app,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/ship_routing")
@login_required
def ship_routing(username):
    user_obj = User.query.filter_by(username=username).first()
    colorblind = getattr(user_obj, "colorblind", False) if user_obj else False
    return render_template(
        "ship_routing.html",
        username=username,
        colorblind=colorblind,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/signup/", methods=["GET", "POST"])
def signup():
    """
    Implements signup functionality. Allows username and password for new user.
    Hashes password with salt using werkzeug.security.
    Stores username and hashed password inside database.

    Username should to be unique else raises sqlalchemy.exc.IntegrityError.
    """

    unauthorised_usernames = [
        "getPublicTrips",
        "airportAutocomplete",
        "countries",
        "editStation",
        "signup",
        "remove_admin",
        "getAdminUsers",
        "dashboard",
        "leaderboard",
        "stations",
        "about",
        "privacy",
        "getLeaderboardUsers",
        "make_admin",
        "deleteManual",
        "deleteUser",
        "forwardRouting",
        "getMultiTrips",
        "static",
        "trainStationAutocomplete",
        "tile",
        "password_reset",
        "editManual",
        "public",
        "getPublicStats",
        "getVesselPhoto",
        "stations-data",
        "stationAutocomplete",
        "admin",
        "login",
        "getCountry",
        "removePolygons",
        "getAirliners",
        "password_reset_request",
        "getGeojson",
    ]

    if request.method == "POST":
        captcha_solution = request.form.get("frc-captcha-solution")
        if not captcha_solution:
            log_suspicious_activity(
                request.url,
                "no_captcha",
                request.method,
                getIp(request),
                getRequestData(request),
            )
            flash(lang[session["userinfo"]["lang"]]["captchaFailed"])
            return redirect(url_for("signup"))

        # Verify the CAPTCHA with FriendlyCaptcha
        captcha_verification = requests.post(
            "https://api.friendlycaptcha.com/api/v1/siteverify",
            data={
                "solution": captcha_solution,
                "secret": load_config()["friendlyCaptcha"]["APIKey"],
            },
        )

        if (
            captcha_verification.status_code != 200
            or not captcha_verification.json().get("success", False)
        ):
            log_suspicious_activity(
                request.url,
                "captcha_failed",
                request.method,
                getIp(request),
                getRequestData(request),
            )
            flash(lang[session["userinfo"]["lang"]]["captchaFailed"])
            return redirect(url_for("signup"))

        username = request.form["username"]
        password = request.form["password"]
        email = request.form["email"]

        # Regular expression for validating an Email
        regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"

        if not (username and password and email):
            flash(lang[session["userinfo"]["lang"]]["signupCantEmpty"])
            return redirect(url_for("signup"))
        elif not re.match(regex, email):
            flash(lang[session["userinfo"]["lang"]]["invalidEmail"])
            return redirect(url_for("signup"))
        elif any(c in username for c in ('@', '.', '<', '>')):
            flash(lang[session["userinfo"]["lang"]]["usernameNoEmail"])
            return redirect(url_for("signup"))
        elif username in unauthorised_usernames:
            flash(
                lang[session["userinfo"]["lang"]]["usernameNotAvailable"].format(
                    u=username, a=email
                )
            )
            return redirect(url_for("signup"))
        else:
            username = username.strip()
            password = password.strip()
            email = email.strip()

        # Returns salted pwd hash in format : method$salt$hashedvalue
        hashed_pwd = generate_password_hash(password, "scrypt")

        new_user = User(
            username=username,
            pass_hash=hashed_pwd,
            email=email,
            lang=lang[session["userinfo"]["lang"]]["langId"],
        )
        authDb.session.add(new_user)

        try:
            authDb.session.commit()
            ip_details = getIpDetails(getIp(request))
            location = f"{ip_details['city']}, {get_flag_emoji(ip_details['country'])}"
            sendOwnerEmail(
                "Nouvel Utilisateur",
                "Nom d'utilisateur : {} <br> Localisation (ip) : {} <br> Email :{} <br> Locale: {} <br> Langue assignée: {}".format(
                    username,
                    location,
                    email,
                    request.accept_languages,
                    lang[session["userinfo"]["lang"]][session["userinfo"]["lang"]],
                ),
            )

            # Log the user in by setting the session variables
            session[username] = True
            session["logged_in"] = username
            session["logged_in_user_id"] = new_user.uid

            # Redirect to the 'about' page after successful signup and login
            return redirect(url_for("about"))

        except sqlalchemy.exc.IntegrityError as e:
            print(e)
            flash(
                lang[session["userinfo"]["lang"]]["usernameNotAvailable"].format(
                    u=username, a=email
                )
            )
            return redirect(url_for("signup"))

    return render_template(
        "signup.html",
        title=lang[session["userinfo"]["lang"]]["signup"],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


def update_user_count():
    # Update Active Users Count
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(days=1)
    active_users_count = User.query.filter(
        User.last_login >= twenty_four_hours_ago
    ).count()
    today = datetime.now(UTC).date()
    with pg_session() as pg:
        result = pg.execute(
            "SELECT number FROM daily_active_users WHERE date = :date",
            {"date": today},
        ).fetchone()
        if result:
            current_count = result[0]
            if active_users_count > current_count:
                pg.execute(
                    "UPDATE daily_active_users SET number = :number WHERE date = :date",
                    {"number": active_users_count, "date": today},
                )
        else:
            pg.execute(
                "INSERT INTO daily_active_users (date, number) VALUES (:date, :number)",
                {"date": today, "number": active_users_count},
            )


@app.route("/", methods=["GET", "POST"])
def landing():
    username = session.get("logged_in")
    force_landing = "force_landing" in request.args

    update_user_count()

    # If the user is logged in and not forcing the landing page
    if username and not force_landing:
        user = User.query.filter_by(username=username).first()
        if user:
            # Redirect to the user's default landing page
            if user.default_landing == "dashboard":
                return redirect(url_for("user_dashboard", username=username))
            elif user.default_landing == "trips":
                return redirect(
                    url_for("dynamic_trips", username=username, time="trips")
                )
            elif user.default_landing == "projects":
                return redirect(
                    url_for("dynamic_trips", username=username, time="projects")
                )
            else:  # Default to map (includes legacy "map" and "new_map" values)
                return redirect(url_for("user_home", username=username))

    # If the user is not logged in or is forcing the landing page
    return render_template(
        "landing.html", **lang[session["userinfo"]["lang"]], **session["userinfo"]
    )


@app.route("/login/", methods=["GET", "POST"])
def login():
    """
    Provides login functionality:
    - Renders the login form on a GET request.
    - Validates username and password on a POST request.
    - Verifies hashed password against the database.
    - Updates legacy hashed passwords to use 'scrypt'.
    - Redirects authenticated users to the home page, else shows an error.
    - Supports raw login for API clients by passing ?raw=1
    """

    # Check if this is a raw request (no redirect)
    raw = request.args.get("raw") == "1"

    # Check if the user is already logged in
    if request.method == "GET":
        username = session.get("logged_in")
        user_id = session.get("logged_in_user_id")
        if username and user_id and session.get(username):
            return "" if raw else redirect(url_for("user_home", username=username))

    # Handle POST request for login
    elif request.method == "POST":
        # Safely get form data to avoid KeyError
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        # Ensure the next URL starts with / to avoid sending to external websites. 
        next_page = n if (n := request.form.get("next", "").strip()).startswith("/") else url_for("landing", username=username)

        # Check if username and password are provided
        if not username or not password:
            flash(lang[session["userinfo"]["lang"]]["credentialsCantEmpty"])
            log_denied_login(
                "missing_credentials", username, getRequestData(request), getIp(request)
            )
            return ("Missing credentials", 400) if raw else redirect(url_for("login"))

        # Check for denied login attempts (e.g., rate limiting)
        if not check_denied_login(getIp(request), username):
            logger.warning(f"Denying login for {username} after too many attempts")
            flash(lang[session["userinfo"]["lang"]]["tooManyErrors"])
            log_denied_login(
                "too_many_requests", username, getRequestData(request), getIp(request)
            )
            return ("Too many attempts", 429) if raw else redirect(url_for("login"))

        # Fetch the user by username or email
        user = User.query.filter(
            (User.username == username) | (User.email == username)
        ).first()

        if user:
            # Use the username from the database for consistency
            username = user.username

        # Verify the user's password
        if user and check_password_hash(user.pass_hash, password):
            # Update password to use 'scrypt' if needed
            if not user.pass_hash.startswith("scrypt"):
                user.pass_hash = generate_password_hash(password, method="scrypt")
                authDb.session.commit()

            # Set session for authenticated user
            session[username] = True
            session["logged_in"] = username
            session["logged_in_user_id"] = user.uid
            session.permanent = (
                True  # Extend session validity based on app configuration
            )
            changeLang(user.lang, session)

            return ("Success", 200) if raw else redirect(next_page)
        else:
            # Log denied login attempts
            if user is None:
                log_denied_login(
                    "non-existent_user",
                    username,
                    getRequestData(request),
                    getIp(request),
                )
            else:
                log_denied_login(
                    "wrong_password", username, getRequestData(request), getIp(request)
                )

            flash(lang[session["userinfo"]["lang"]]["invalidCredentials"])
            return ("Invalid credentials", 401) if raw else redirect(url_for("login"))

    # Handle GET request: Render login form
    return render_template(
        "login_form.html",
        title=lang[session["userinfo"]["lang"]]["login"],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>")
@login_required
def user_home(username):
    """
    Home page for validated users.

    """
    user = User.query.filter_by(username=username).first()
    return render_template(
        "new_map.html",
        title=lang[session["userinfo"]["lang"]]["map"],
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id(username)),
        tileserver=user.tileserver,
        globe=user.globe,
        public=False,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/leaflet")
@login_required
def user_home_leaflet(username):
    """
    Classic Leaflet map for validated users.
    """
    return render_template(
        "map.html",
        title=lang[session["userinfo"]["lang"]]["map"],
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id()),
        public=False,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )

# 1. SEARCH PAGE - Shows the search form
@app.route("/u/<username>/motis")
@login_required
def motis_search(username):
    """
    Display the MOTIS search form
    """
    return render_template(
        "motis_search.html",  # This is your first artifact
        title="Plan Journey",
        username=username,
        nav="bootstrap/navigation.html",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )

# 2. RESULTS PAGE - Shows routing results and handles search
@app.route("/u/<username>/motis/results", methods=["GET", "POST"])
@login_required 
def motis_results(username):
    """
    Handle search and display results
    """
    if request.method == "POST":
        # Handle form submission from search page
        return handle_search_form(username)
    else:
        # Handle direct GET requests (from URL parameters)
        return handle_search_params(username, forwardRouting, lang)



@app.route("/getVectorStyle/<language>/<style>.json")
def vector_style(language, style):
    json_path = os.path.join(
        app.static_folder, "styles/vector_maps/{style}.json".format(style=style)
    )

    with open(json_path, "r", encoding="utf-8") as f:
        file_contents = f.read()
        # Match the incoming request's scheme rather than hardcoding https: in
        # local dev (plain http) a forced https sprite URL is a different
        # origin from the page itself — nothing listens there, so the sprite
        # fetch just times out/CORS-fails. In production this still resolves
        # to https, since the site itself is only ever served over https.
        file_contents = file_contents.replace(
            "{{mapPinUrl}}",
            url_for(
                "static", filename="styles/vector_maps", _scheme=request.scheme, _external=True
            ),
        )
        template_url = "https://tiles.trainlog.me/tile/streets-v2+landcover-v1.1+hillshade-v1/{x}/{y}/{z}/{language}"
        final_url = template_url.replace("{language}", language)
        file_contents = file_contents.replace("{{tileServerUrl}}", final_url)
        file_contents = file_contents.replace("{{language}}", language)
        vectorStyle = json.loads(file_contents)

    # Return as proper JSON response
    return jsonify(vectorStyle)


@app.route("/getORMStyle/<style>.json")
def orm_style(style):
    allowed = {"standard", "speed", "signals", "electrification", "track", "operator"}
    if style not in allowed:
        return ("Not found", 404)
    resp = requests.get(f"https://openrailwaymap.app/style/{style}.json", timeout=10)
    return jsonify(resp.json())


@app.route("/u/<username>/new_map")
@login_required
def new_map(username):
    """
    New General map (WebGl)

    """
    user = User.query.filter_by(username=username).first()
    return render_template(
        "new_map.html",
        title=lang[session["userinfo"]["lang"]]["map"],
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id(username)),
        tileserver=user.tileserver,
        globe=user.globe,
        public=False,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/countries/<cc>")
def pCountries(cc):
    username = session.get("logged_in")
    if session.get(username):
        return redirect(url_for("countries", username=username, cc=cc))
    else:
        return redirect(url_for("login"))


@app.route("/u/<username>/countries/<cc>")
@app.route("/public/<username>/countries/<cc>")
@public_required
def countries(username, cc):
    """ """
    # If the username doesn't match the logged-in user and the accessed URL doesn't have 'public', redirect to the public URL
    if username != getUser() and "/public/" not in request.path:
        public_url = url_for("countries", username=username, cc=cc)
        return redirect(public_url)

    # to be removed eventually when all cc in db are uppercase, but added for safety as some old entries have lowercase cc
    if cc != cc.upper():
        return redirect(url_for("countries", username=username, cc=cc.upper()))

    if username == getUser():
        nav = "bootstrap/navigation.html"
    else:
        nav = "bootstrap/public_nav.html"

    if not has_coverage_file(cc):
        abort(410)

    user_obj = User.query.filter_by(username=username).first()
    colorblind = getattr(user_obj, "colorblind", False) if user_obj else False

    return render_template(
        "countries.html",
        title=lang[session["userinfo"]["lang"]]["map"],
        username=username,
        colorblind=colorblind,
        nav=nav,
        cc=cc,
        isCurrent=has_current_trip(get_user_id(username)),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/countryGeoJSON/<cc>")
@public_required
def getCountryGeoJSON(username, cc):
    def midpoint(point1, point2):
        return ((point1[0] + point2[0]) / 2, (point1[1] + point2[1]) / 2)

    def getPolygonFromCoordinates(cc, lat, lng):
        return geopip_country.search(cc=cc, lat=lat, lng=lng)

    start_time = datetime.now()
    # Prepare the parameters
    if "-" in cc:
        params = {"user_id": get_user_id(username), "country": "%" + cc.split("-")[0] + "%"}
    else:
        params = {"user_id": get_user_id(username), "country": "%" + cc + "%"}

    with pg_session() as pg:
        idList = [
            row["uid"] for row in pg.execute(get_trips_country_query(), params).fetchall()
        ]

    with pg_session() as pg:
        pathResult = pg.execute(
            get_user_lines_query(), {"ids": [int(i) for i in idList]}
        ).fetchall()

    # Extract unique nodes
    unique_nodes = set()
    for path in pathResult:
        nodes = json.loads(path[1])
        for i in range(len(nodes) - 1):
            start = (nodes[i][0], nodes[i][1])
            end = (nodes[i + 1][0], nodes[i + 1][1])
            mid = midpoint(start, end)
            unique_nodes.add(mid)
        unique_nodes.update((node[0], node[1]) for node in nodes)

    exclude_ids = list(
        dict.fromkeys(
            [
                coord["id"]
                for lat, lng in unique_nodes
                if (coord := getPolygonFromCoordinates(cc, lat, lng)) is not None
            ]
        )
    )

    geojson_data = get_coverage_geojson_dict(cc)
    # Initialize the total area
    traveled_area = 0

    for feature in geojson_data["features"]:
        feature_id = feature["properties"].get("id")
        feature_area = feature["properties"].get("area_m2", 0)

        if feature_id in exclude_ids:
            feature["properties"]["traveled"] = True
            traveled_area += feature_area
        else:
            feature["properties"]["traveled"] = False

    # Compare total_area with the global total_area_m2
    total_area = geojson_data["total_area_m2"]
    percent = math.ceil(min((traveled_area / total_area) * 100, 100))
    with pg_session() as pg:
        pg.execute(
            upsert_percent_query(), {"username": username, "cc": cc, "percent": percent}
        )
    end_time = datetime.now()  # End the timer
    render_time = end_time - start_time  # Calculate the difference
    print(render_time)
    return jsonify([percent, geojson_data])


@app.route("/admin/editCountries/<cc>")
@admin_required
def editCountries(cc):
    """ """
    if not has_coverage_file(cc, immediate_only=True):
        abort(410)

    return render_template(
        "admin/country_edit.html",
        title="Edit " + cc,
        username=getUser(),
        nav="bootstrap/navigation.html",
        cc=cc,
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/editCountriesList")
@admin_required
def editCountriesList():
    """ """
    return render_template(
        "admin/edit_coverage_list.html",
        title="Edit List",
        username=getUser(),
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/getGeojson/<cc>", methods=["GET"])
def get_full_geojson(cc):
    return jsonify(get_coverage_geojson_dict(cc))


@app.route("/processQueue/<cc>", methods=["POST"])
@admin_required
def process_queue(cc):
    try:
        operations = request.json
        
        if not operations or len(operations) == 0:
            return jsonify({"success": False, "message": "No operations to process"})
        
        # Load the current GeoJSON data
        file_path = get_coverage_file_path(cc)
        geojson_data = get_coverage_geojson_dict(cc, immediate_only=True)
        
        print(f"Processing {len(operations)} operations for {cc}")
        
        # Process each operation in the queue
        for i, operation in enumerate(operations):
            operation_type = operation["type"]
            polygon_ids = operation["polygonIds"]
            
            print(f"Operation {i+1}: {operation_type} on polygons {polygon_ids}")
            
            if operation_type == "delete":
                # Find polygons to delete and calculate area to subtract
                total_area_to_subtract = 0
                remaining_features = []
                
                for feature in geojson_data["features"]:
                    if feature["properties"]["id"] in polygon_ids:
                        total_area_to_subtract += feature["properties"]["area_m2"]
                        print(f"  Deleting polygon {feature['properties']['id']} with area {feature['properties']['area_m2']}")
                    else:
                        remaining_features.append(feature)
                
                # Update the GeoJSON data
                geojson_data["features"] = remaining_features
                geojson_data["total_area_m2"] -= total_area_to_subtract
                
            elif operation_type == "merge":
                if len(polygon_ids) != 2:
                    return jsonify({
                        "success": False, 
                        "message": f"Merge operation requires exactly 2 polygons, got {len(polygon_ids)}"
                    })
                
                # Find the polygons to merge
                polygons_to_merge = []
                remaining_features = []
                
                for feature in geojson_data["features"]:
                    if feature["properties"]["id"] in polygon_ids:
                        polygons_to_merge.append(feature)
                    else:
                        remaining_features.append(feature)
                
                if len(polygons_to_merge) != 2:
                    return jsonify({
                        "success": False, 
                        "message": f"Could not find both polygons to merge (found {len(polygons_to_merge)})"
                    })
                
                print(f"  Merging polygons {polygon_ids}")
                
                # Check if polygons are contiguous
                def polygons_are_contiguous(poly1, poly2, tolerance=0.0001):
                    def get_all_coordinates(poly):
                        coords = []
                        if poly["geometry"]["type"] == "Polygon":
                            for ring in poly["geometry"]["coordinates"]:
                                coords.extend(ring)
                        elif poly["geometry"]["type"] == "MultiPolygon":
                            for polygon in poly["geometry"]["coordinates"]:
                                for ring in polygon:
                                    coords.extend(ring)
                        return coords
                    
                    def distance(coord1, coord2):
                        return ((coord1[0] - coord2[0]) ** 2 + (coord1[1] - coord2[1]) ** 2) ** 0.5
                    
                    coords1 = get_all_coordinates(poly1)
                    coords2 = get_all_coordinates(poly2)
                    
                    # Check if any coordinates are within tolerance
                    for c1 in coords1:
                        for c2 in coords2:
                            if distance(c1, c2) <= tolerance:
                                return True
                    
                    # Check if any line segments are close
                    for i in range(len(coords1) - 1):
                        line1_start = coords1[i]
                        line1_end = coords1[i + 1]
                        
                        for j in range(len(coords2) - 1):
                            line2_start = coords2[j]
                            line2_end = coords2[j + 1]
                            
                            if (distance(line1_start, line2_start) <= tolerance and 
                                distance(line1_end, line2_end) <= tolerance) or \
                               (distance(line1_start, line2_end) <= tolerance and 
                                distance(line1_end, line2_start) <= tolerance):
                                return True
                    
                    return False
                
                if not polygons_are_contiguous(polygons_to_merge[0], polygons_to_merge[1]):
                    return jsonify({
                        "success": False, 
                        "message": "Selected polygons are not contiguous and cannot be merged"
                    })
                
                # Perform geometric union using Shapely
                shapely_polygons = []
                total_area = 0
                
                for poly in polygons_to_merge:
                    shapely_poly = shape(poly["geometry"])
                    shapely_polygons.append(shapely_poly)
                    total_area += poly["properties"]["area_m2"]
                
                # Create the merged geometry
                merged_geometry = unary_union(shapely_polygons)
                merged_area = merged_geometry.area
                
                # Handle potential overlap in area calculation
                if abs(merged_area - sum(poly["properties"]["area_m2"] for poly in polygons_to_merge)) > 0.000001:
                    overlap_ratio = merged_area / sum(shapely_poly.area for shapely_poly in shapely_polygons)
                    actual_area = total_area * overlap_ratio
                else:
                    actual_area = total_area
                
                # Create the new merged polygon
                merged_polygon = {
                    "type": "Feature",
                    "geometry": mapping(merged_geometry),
                    "properties": {
                        "id": min(polygon_ids),  # Use the smaller ID
                        "area_m2": actual_area
                    }
                }
                
                print(f"  Created merged polygon with ID {merged_polygon['properties']['id']} and area {actual_area}")
                
                # Add merged polygon to remaining features
                remaining_features.append(merged_polygon)
                geojson_data["features"] = remaining_features
        
        # Write the updated data back to the file
        with open(file_path, "w") as file:
            json.dump(geojson_data, file)

        # Remove the old data from cache
        geopip_country.invalidate_cache(cc)
        
        print(f"Successfully processed {len(operations)} operations")
        return jsonify({
            "success": True, 
            "message": f"Successfully processed {len(operations)} operation{'s' if len(operations) > 1 else ''}"
        })
    
    except Exception as e:
        print(f"Error processing queue: {str(e)}")
        return jsonify({
            "success": False, 
            "message": f"Error processing operations: {str(e)}"
        })


@app.route("/about")
def about():
    return render_template(
        "about.html",
        username=getUser(),
        nav="bootstrap/navigation.html",
        title=lang[session["userinfo"]["lang"]]["about"],
        translations=lang[session["userinfo"]["lang"]],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/edit_translations/<langid>", methods=["GET", "POST"])
@translator_required
def edit_translations(langid):
    file_path = os.path.join("lang", f"{langid}.json")
    en_file_path = os.path.join("lang", "en.json")
    log_file_path = os.path.join("logs/translations", f"{langid}.log")

    # Ensure the language file exists
    if not os.path.exists(file_path):
        flash(f"Language file {langid}.json not found.", "danger")
        return redirect(url_for("dashboard"))

    # Ensure logs directory exists
    os.makedirs("logs/translations", exist_ok=True)

    # Load English translations as a reference
    with open(en_file_path, "r", encoding="utf-8") as en_file:
        en_translations = json.load(en_file)

    # Load current translations
    with open(file_path, "r", encoding="utf-8") as file:
        translations = json.load(file)

    # Initialize session tracking if not present
    if "saved_keys" not in session:
        session["saved_keys"] = []

    if request.method == "POST":
        # Handle JSON request sent by AJAX
        if request.is_json:
            data = request.get_json()  # Get the changed bits
            saved_keys = []

            # Update only the changed translations
            for key, value in data.items():
                if translations.get(key) != value:  # If there's a change
                    old_value = translations.get(key)
                    translations[key] = value
                    saved_keys.append(key)

                    # Log the change
                    log_entry = (
                        f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] "
                        f'User: {getUser()}, Key: {key}, "{old_value}" -> "{value}"\n'
                    )
                    with open(log_file_path, "a", encoding="utf-8") as log_file:
                        log_file.write(log_entry)

            # Save the updated translations back to the JSON file
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(translations, file, ensure_ascii=False, indent=4)

            # Update session with saved keys
            session["saved_keys"] = saved_keys

            # Return a JSON response
            return jsonify(
                {
                    "message": f"Translations for {langid} updated successfully!",
                    "status": "success",
                }
            )

        # Handle standard form submission fallback (if any)
        updated_translations = {}
        saved_keys = []

        for key in request.form:
            old_value = translations.get(key)
            new_value = request.form[key]
            updated_translations[key] = new_value
            if old_value != new_value:
                saved_keys.append(key)

                # Log the change
                username = session["userinfo"].get("username", "unknown_user")
                log_entry = f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] User: {username}, Key: {key}, Old: {old_value}, New: {new_value}\n"
                with open(log_file_path, "a", encoding="utf-8") as log_file:
                    log_file.write(log_entry)

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(updated_translations, file, ensure_ascii=False, indent=4)

        session["saved_keys"] = saved_keys
        flash(f"Translations for {langid} updated successfully!", "success")
        return redirect(url_for("edit_translations", langid=langid))
    lang = readLang()

    # Render the template with saved keys
    response = render_template(
        "admin/edit_translations.html",
        translations=translations,
        langid=langid,
        en_translations=en_translations,
        saved_keys=session.get("saved_keys", []),
        username=getUser(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )

    # Clear saved keys after rendering the template
    session["saved_keys"] = []

    return response


@app.route("/public/<username>")
@public_required
def public(username):
    return public_maplibre(username)


@app.route("/public/<username>/leaflet")
@public_required
def public_leaflet(username):
    """
    Public home (Leaflet fallback)
    """
    return render_template(
        "map.html",
        nav="bootstrap/public_nav.html",
        title=lang[session["userinfo"]["lang"]]["map"],
        username=username,
        public=True,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/public/<username>/map")
@app.route("/public/<username>/new_map")
@public_required
def public_maplibre(username):
    """
    Public home (MapLibre)
    """
    user = User.query.filter_by(username=getUser()).first()
    tileserver = user.tileserver if user else "default"
    globe = user.globe if user else False

    return render_template(
        "new_map.html",
        nav="bootstrap/public_nav.html",
        title=lang[session["userinfo"]["lang"]]["map"],
        username=username,
        public=True,
        tileserver=tileserver,
        globe=globe,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/borked_trips")
@app.route("/admin/borked_trips/u/<username>")
@owner_required
def borked_trips(username=None):
    # Get trips (filtered by user if provided)
    with pg_session() as pg:
        if username:
            trips = pg.execute(
                "SELECT trip_id AS uid, user_id, created FROM trips WHERE user_id = :uid",
                {"uid": get_user_id(username)},
            ).fetchall()
        else:
            trips = pg.execute(
                "SELECT trip_id AS uid, user_id, created FROM trips"
            ).fetchall()
    trips = [dict(r._mapping) for r in trips]

    if not trips:
        return jsonify(
            {"missing_trips": [], "count": 0}
            if username
            else {"borked_trips_by_user": {}, "total_count": 0}
        )

    # Get existing paths in batches to avoid SQL variable limit
    all_uids = [row["uid"] for row in trips]
    path_uids = set()
    batch_size = 999  # SQLite limit is 999 variables

    with pg_session() as pg:
        for i in range(0, len(all_uids), batch_size):
            batch = all_uids[i : i + batch_size]
            path_uids.update(
                row[0]
                for row in pg.execute(
                    "SELECT DISTINCT trip_id FROM paths WHERE trip_id = ANY(:ids)",
                    {"ids": [int(u) for u in batch]},
                ).fetchall()
            )

    if username:
        # Single user mode
        missing = [
            {"uid": row["uid"], "created": row["created"]}
            for row in trips
            if row["uid"] not in path_uids
        ]
        return jsonify({"missing_trips": missing, "count": len(missing)})

    # Global mode
    username_cache = {}
    result = {}
    for row in trips:
        if row["uid"] not in path_uids:
            user = username_cache.get(row["user_id"])
            if user is None:
                user = get_username(row["user_id"])
                username_cache[row["user_id"]] = user
            if user not in result:
                result[user] = []
            result[user].append({"uid": row["uid"], "created": row["created"]})

    # Format response
    formatted = {
        user: {"missing_trips": trips, "count": len(trips)}
        for user, trips in result.items()
    }

    return jsonify(
        {
            "borked_trips_by_user": formatted,
            "total_count": sum(len(trips) for trips in result.values()),
            "affected_users": len(result),
        }
    )

@app.route("/admin/realign_flight_tracks", methods=["GET"])
@owner_required
def realign_flight_tracks():
    """Owner-only: find (and, with ?apply=1, repair) paths whose geometry vertex
    count no longer matches their altitude/timestamp arrays — a legacy FR24 import
    artifact. Returns JSON; defaults to a dry run so it is safe to hit first."""
    from scripts.realign_flight_tracks import find_and_fix

    apply = request.args.get("apply") in ("1", "true", "yes")
    return jsonify(find_and_fix(apply=apply))


@app.route("/admin/add_dummy_path/<trip_id>", methods=["GET"])
@owner_required
def add_dummy_path(trip_id):
    with pg_session() as pg:
        # Check if trip already has a path
        existing = pg.execute(
            "SELECT COUNT(*) FROM paths WHERE trip_id = :tid", {"tid": trip_id}
        ).scalar()

        if existing > 0:
            return jsonify({
                "success": False,
                "message": "Trip already has a path"
            }), 400

        # Insert dummy path
        pg.execute(
            "INSERT INTO paths (trip_id, geom) VALUES (:tid, ST_GeomFromEWKT(:ewkt))",
            {"tid": trip_id, "ewkt": coords_to_ewkt([[0, 0], [1, 1]])},
        )

        return jsonify({
            "success": True,
            "message": f"Dummy path added to trip {trip_id}"
        })


# ── Freehandify: turn legacy over-sampled routes into editable freehand ones ──
# The old freehand endpoint stored the raw mouse track (thousands of points) with no
# waypoints and route_source 'router', so those trips can't be reopened in the freehand
# canvas. These routes simplify the dense path to a few waypoints (Douglas-Peucker) and
# rebuild the drawn line with the exact logic of static/js/freehand.js (buildSavePath),
# writing waypoints + rebuilt geometry + route_source='freehand'. The first run snapshots
# the original into freehand_backup, so the change is reversible and can be re-run at a
# different tolerance from the full-detail original. trip_length and carbon are left
# untouched (the simplified line's length is within ~1% of the original).

def _parse_trip_ids(trip_ids):
    """Comma-separated ids -> [int]. Returns None if any token isn't an integer."""
    try:
        return [int(t) for t in trip_ids.split(",") if t.strip()]
    except ValueError:
        return None


def _run_freehandify(ids, epsilon, restrict_user_id=None):
    """Apply/revert helpers share this loop. When restrict_user_id is set, trips not
    owned by that user are skipped (never touched)."""
    results = []
    with pg_session() as pg:
        purge_expired_backups(pg)  # drop undo snapshots past their TTL
        for trip_id in ids:
            if restrict_user_id is not None:
                row = pg.execute(
                    "SELECT user_id FROM trips WHERE trip_id = :t", {"t": trip_id}
                ).fetchone()
                if row is None:
                    results.append({"trip_id": trip_id, "status": "skipped", "reason": "not found"})
                    continue
                if row["user_id"] != restrict_user_id:
                    results.append({"trip_id": trip_id, "status": "skipped", "reason": "not owned"})
                    continue
            results.append(apply_to_trip(pg, trip_id, epsilon))
    return results


def _run_revert(ids, restrict_user_id=None):
    results = []
    with pg_session() as pg:
        purge_expired_backups(pg)  # drop undo snapshots past their TTL
        for trip_id in ids:
            if restrict_user_id is not None:
                row = pg.execute(
                    "SELECT user_id FROM trips WHERE trip_id = :t", {"t": trip_id}
                ).fetchone()
                if row is None:
                    results.append({"trip_id": trip_id, "status": "skipped", "reason": "not found"})
                    continue
                if row["user_id"] != restrict_user_id:
                    results.append({"trip_id": trip_id, "status": "skipped", "reason": "not owned"})
                    continue
            results.append(revert_trip(pg, trip_id))
    return results


@app.route("/admin/freehandify/<trip_ids>", methods=["GET"])
@owner_required
def freehandify_trips_admin(trip_ids):
    """Owner-only: freehandify any trips. See _run_freehandify. `?epsilon` (metres,
    default 50) tunes simplification — larger means fewer waypoints."""
    ids = _parse_trip_ids(trip_ids)
    if ids is None:
        return jsonify({"success": False, "message": "trip_ids must be integers"}), 400
    try:
        epsilon = float(request.args.get("epsilon", 50))
    except ValueError:
        return jsonify({"success": False, "message": "epsilon must be a number"}), 400
    return jsonify({"success": True, "epsilon": epsilon, "results": _run_freehandify(ids, epsilon)})


@app.route("/admin/freehandify/revert/<trip_ids>", methods=["GET"])
@owner_required
def freehandify_revert_admin(trip_ids):
    """Owner-only: restore trips' original routes from the freehand_backup snapshot."""
    ids = _parse_trip_ids(trip_ids)
    if ids is None:
        return jsonify({"success": False, "message": "trip_ids must be integers"}), 400
    return jsonify({"success": True, "results": _run_revert(ids)})


@app.route("/u/<username>/freehandify/<trip_ids>", methods=["GET"])
@login_required
def freehandify_trips_user(username, trip_ids):
    """Freehandify the caller's own trips (login_required guarantees username is the
    logged-in user); trips owned by anyone else are skipped. `?epsilon` (metres,
    default 50) tunes simplification — larger means fewer waypoints."""
    ids = _parse_trip_ids(trip_ids)
    if ids is None:
        return jsonify({"success": False, "message": "trip_ids must be integers"}), 400
    try:
        epsilon = float(request.args.get("epsilon", 50))
    except ValueError:
        return jsonify({"success": False, "message": "epsilon must be a number"}), 400
    results = _run_freehandify(ids, epsilon, restrict_user_id=get_user_id(username))
    return jsonify({"success": True, "epsilon": epsilon, "results": results})


@app.route("/u/<username>/freehandify/revert/<trip_ids>", methods=["GET"])
@login_required
def freehandify_revert_user(username, trip_ids):
    """Restore the caller's own trips from their freehand_backup snapshot; trips owned
    by anyone else are skipped."""
    ids = _parse_trip_ids(trip_ids)
    if ids is None:
        return jsonify({"success": False, "message": "trip_ids must be integers"}), 400
    results = _run_revert(ids, restrict_user_id=get_user_id(username))
    return jsonify({"success": True, "results": results})


# ── Split a trip in two at a chosen path node (keeps the 3D flight track) ──
# The GPS track (geom + altitude + timestamps) is sliced at a node the user clicks on
# the map; leg 1 reuses the original trip, leg 2 is created. See src/trips/split_trip.py.

@app.route("/u/<username>/split_trip/<int:trip_id>", methods=["GET"])
@login_required
def split_trip_page(username, trip_id):
    with pg_session() as pg:
        data = get_split_data(pg, trip_id)
    if data is None:
        abort(404)
    if data["trip"]["user_id"] != get_user_id(username):
        abort(401)
    return render_template(
        "split_trip.html",
        title="Split trip",
        username=username,
        trip_id=trip_id,
        trip=data["trip"],
        nodes=data["nodes"],
        country_list=get_all_countries(),
        colorblind=False,
    )


@app.route("/u/<username>/split_trip/<int:trip_id>", methods=["POST"])
@login_required
def split_trip_action(username, trip_id):
    data = request.get_json(silent=True) or {}
    try:
        split_index = int(data.get("split_index"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "split_index must be an integer"}), 400
    mid_station = data.get("mid_station", "")
    try:
        result = split_trip(trip_id, split_index, mid_station, get_user_id(username))
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    return jsonify({"success": True, **result})


def listOperatorsLogos(tripType=None):
    """
    Return list of available logos for operators from the database.
    If a tripType is provided, it will filter logos based on that type.
    """
    logo_types = {
        "operator": "Operator",
        "accommodation": "Accommodation",
        "car": "Car",
        "poi": "Point of Interest",
    }

    # Default to fetching all logo types if no tripType is specified
    selected_types = logo_types.keys() if tripType is None else [tripType]

    logoURLs = {}

    with pg_session() as pg:
        for logo_type in selected_types:
            # Keyed by every alias, not just short_name, so a user typing CFF or FFS
            # is offered the SBB logo just as one typing SBB is. The stats chart and
            # the trip-form autocomplete both look names up in this dict directly.
            #
            # DISTINCT ON picks the current logo. Operators can have several, one per
            # era (SNCF has seven, 1937 to 2011), and neither the stats chart nor the
            # autocomplete has a date to choose by — without this the last row to
            # arrive won, so the chart showed an arbitrary historical logo.
            rows = pg.execute(
                """
                SELECT DISTINCT ON (a.alias) a.alias, l.logo_url
                FROM operators o
                JOIN operator_aliases a ON a.operator_id = o.operator_id
                JOIN operator_logos l ON o.operator_id = l.operator_id
                WHERE o.operator_type = :logo_type
                ORDER BY a.alias, l.effective_date DESC NULLS LAST, l.uid DESC
            """,
                {"logo_type": logo_type},
            ).fetchall()

            for row in rows:
                logoURLs[row["alias"]] = row["logo_url"]

    return logoURLs


def render_public_trip_page(
    tripIds=None,
    tagId=None,
    ticketId=None,
    template="public/public_trip.html",
    owner_only=False,
):
    
    user_obj = None
    colorblind = False
    username = getUser()
    if username:
        user_obj = User.query.filter_by(username=username).first()
        colorblind = getattr(user_obj, "colorblind", False) if user_obj else False

    tag_type = None
    tag_name = None
    countries = []
    length = 0

    if tripIds is None and tagId is not None:
        with pg_session() as pg:
            result = pg.execute(
                """
                SELECT string_agg(tags_associations.trip_id::text, ',') AS trip_ids,
                       tags.type AS type, tags.name AS name
                FROM tags_associations
                LEFT JOIN tags ON tags.uid = tags_associations.tag_id
                WHERE tags.uuid = :uuid
                GROUP BY tags.type, tags.name
                """,
                {"uuid": tagId},
            ).fetchone()
            tripIds = result["trip_ids"] if result else None
            tag_type = result["type"] if result else None
            tag_name = result["name"] if result else None
            # Shared tags mix several users' trips: the static itinerary page
            # assumes a single owner, so those always open the multiTrip view.
            shared = pg.execute(
                """
                SELECT 1 FROM tag_members tm
                JOIN tags ON tags.uid = tm.tag_id
                WHERE tags.uuid = :uuid AND tm.status = 'accepted'
                LIMIT 1
                """,
                {"uuid": tagId},
            ).fetchone()
        if shared:
            return redirect(url_for("multi_trip", tagUuid=tagId))
    elif tripIds is None and ticketId is not None:
        with pg_session() as pg:
            result = pg.execute(
                """
                SELECT string_agg(trips.trip_id::text, ',') AS trip_ids,
                       tickets.name AS ticket_name
                FROM trips
                LEFT JOIN tickets ON trips.ticket_id = tickets.uid
                WHERE tickets.uid = :uid
                GROUP BY tickets.name
                """,
                {"uid": ticketId},
            ).fetchone()
            tripIds = result["trip_ids"] if result else None
            tag_name = result["ticket_name"] if result else None

    if not tripIds:
        abort(410)

    # The page shell only needs visibility screening, per-trip countries/length
    # for the OG tags and the sorted id list — fetch just that in one set-based
    # query instead of the full get_trip_pg machinery once per trip.
    requested_ids = [int(t) for t in tripIds.split(",")]
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT trip_id, user_id, visibility, countries, trip_length,
                   origin_station, destination_station, is_project,
                   COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime
            FROM trips
            WHERE trip_id = ANY(:ids)
            """,
            {"ids": requested_ids},
        ).fetchall()
    rows_by_id = {row["trip_id"]: row for row in rows}
    if len(rows_by_id) < len(set(requested_ids)):
        abort(410)

    usernames = {
        user_id: get_username(user_id)
        for user_id in {row["user_id"] for row in rows}
    }
    # Legacy multi-owner tags (from before attach_tag checked ownership) are as
    # broken on this single-owner page as shared tags — send them along too.
    if tagId is not None and len(set(usernames.values())) > 1:
        return redirect(url_for("multi_trip", tagUuid=tagId))
    users_by_name = {
        username: User.query.filter_by(username=username).first()
        for username in set(usernames.values())
    }

    # friendship answers per owner (positive AND negative), so each owner is
    # queried at most once
    friends_cache = {}
    trip_list = []
    num_hidden_trips = 0
    for trip_id in requested_ids:
        row = rows_by_id[trip_id]
        username = usernames[row["user_id"]]
        user = users_by_name[username]

        if not session.get(username):
            if row["visibility"] == "private":
                num_hidden_trips += 1
                continue
            if row["visibility"] == "friends":
                if username not in friends_cache:
                    friends_cache[username] = current_user_is_friend_with(username)
                if not friends_cache[username]:
                    num_hidden_trips += 1
                    continue

        row_countries = row["countries"]
        if isinstance(row_countries, str):
            row_countries = json.loads(row_countries)
        for country in (row_countries or {}).keys():
            if country not in countries:
                countries.append(country)
        length += row["trip_length"] or 0

        dt = row["utc_filtered_start_datetime"]
        trip_list.append(
            {
                "uid": trip_id,
                "username": username,
                "utc_filtered_start_datetime": _fmt_legacy_dt(dt)
                if dt is not None
                else (1 if row["is_project"] else -1),
                "origin_station": row["origin_station"],
                "destination_station": row["destination_station"],
            }
        )

        if (
            not session.get(user.username)
            and not user.is_public_trips()
            and not session.get(owner)
        ):
            abort(401)

    if not trip_list and num_hidden_trips > 0: # all requested trips are hidden
        abort(401)

    def _trip_sort_key(trip):
        # Dated trips store utc_filtered_start_datetime as a "YYYY-MM-DD HH:MM:SS"
        # string; non-dated trips use the sentinel ints -1 (past) and 1 (future).
        # Return a (group, value) tuple so str and int are never compared directly
        # (which would raise TypeError when mixing dated and non-dated trips).
        # Order: past non-dated, then dated chronologically, then future non-dated.
        dt = trip["utc_filtered_start_datetime"]
        if isinstance(dt, str):
            return (1, dt)
        return (0 if dt == -1 else 2, "")

    try:
        trip_list_sorted = sorted(trip_list, key=_trip_sort_key)
    except Exception:
        abort(500)

    # Open Graph info
    og = {}
    if tag_name:
        displayCountries = " ".join([get_flag_emoji(c) for c in countries])
        og["title"] = tag_name
        og["description"] = f"{round(length / 1000)} km in {displayCountries}"
    elif trip_list_sorted[0]["utc_filtered_start_datetime"] not in (1, -1):
        og["title"] = (
            f"Trainlog trip starting on {datetime.strptime(trip_list_sorted[0]['utc_filtered_start_datetime'], '%Y-%m-%d %H:%M:%S').strftime('%d %B %Y')}"
        )
        og["description"] = (
            f"From {trip_list_sorted[0]['origin_station']} to {trip_list_sorted[-1]['destination_station']}"
        )
    else:
        og["title"] = "Trainlog trip"
        og["description"] = (
            f"From {trip_list_sorted[0]['origin_station']} to {trip_list_sorted[-1]['destination_station']}"
        )

    user = User.query.filter_by(username=getUser()).first()
    if user is None:
        tileserver = "default"
        globe = False
    else:
        tileserver = user.tileserver
        globe = user.globe

    # The poster is a personal keepsake, not a share view: it is offered, and served,
    # only when every trip on the page belongs to the viewer (the site owner, as
    # everywhere else on this page, sees it regardless).
    own_trips = bool(session.get(owner)) or (
        user is not None
        and all(trip["username"] == user.username for trip in trip_list_sorted)
    )
    if owner_only and not own_trips:
        abort(401)

    return render_template(
        template,
        own_trips=own_trips,
        logosList=listOperatorsLogos(),
        tripIds=",".join(str(trip["uid"]) for trip in trip_list_sorted),
        title=lang[session["userinfo"]["lang"]]["sharedLink"],
        collection_voyage=tag_type,
        tag_description=tag_name,
        tag_uuid=tagId,
        special_og=True,
        tileserver=tileserver,
        globe=globe,
        og=og,
        num_hidden_trips=num_hidden_trips,
        username=user.username if user is not None else None,
        show_ride_along=user is not None and user.username != trip_list_sorted[0]["username"],
        colorblind = colorblind,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/public/leaflet/trip/<tripIds>")
@app.route("/public/leaflet/tag/<tagId>")
@app.route("/public/leaflet/ticket/<ticketId>")
def public_trip_leaflet(tripIds=None, tagId=None, ticketId=None):
    return render_public_trip_page(tripIds, tagId, ticketId)


@app.route("/public/new/trip/<tripIds>")
@app.route("/public/new/tag/<tagId>")
@app.route("/public/new/ticket/<ticketId>")
def public_trip_legacy(tripIds=None, tagId=None, ticketId=None):
    if tripIds:
        return redirect(url_for("public_trip", tripIds=tripIds), 301)
    if tagId:
        return redirect(url_for("public_trip", tagId=tagId), 301)
    return redirect(url_for("public_trip", ticketId=request.view_args.get("ticketId")), 301)


@app.route("/public/trip/<tripIds>")
@app.route("/public/tag/<tagId>")
@app.route("/public/ticket/<ticketId>")
def public_trip(tripIds=None, tagId=None, ticketId=None):
    colorblind = False
    username = getUser()
    if username:
        user_obj = User.query.filter_by(username=username).first()
        colorblind = getattr(user_obj, "colorblind", False) if user_obj else False
    return render_public_trip_page(
        tripIds, tagId, ticketId, template="public/new_trip.html"
    )


@app.route("/public/trip/<tripIds>/poster")
@app.route("/public/tag/<tagId>/poster")
@app.route("/public/ticket/<ticketId>/poster")
def public_trip_poster(tripIds=None, tagId=None, ticketId=None):
    return render_public_trip_page(
        tripIds, tagId, ticketId, template="public/trip_poster.html", owner_only=True
    )


@app.route("/public/multiTrip/<tripIds>")
@app.route("/public/multiTrip/tag/<tagUuid>")
def multi_trip(tripIds=None, tagUuid=None):
    """
    Public Trip
    """
    tag_name = None
    if tripIds is None:
        with pg_session() as pg:
            result = pg.execute(
                """
                SELECT string_agg(tags_associations.trip_id::text, ',') AS trip_ids,
                       tags.name AS name
                FROM tags_associations
                LEFT JOIN tags ON tags.uid = tags_associations.tag_id
                WHERE tags.uuid = :uuid
                GROUP BY tags.name
                """,
                {"uuid": tagUuid},
            ).fetchone()
            tripIds = result["trip_ids"] if result else None
            tag_name = result["name"] if result else None
        if not tripIds:
            abort(410)

    # Same batched screening as the tag page: one set-based query, private/
    # friends trips drop out of the embedded id list (getMultiTrips would filter
    # them anyway), owner-level sharing checked once per owner of a visible trip.
    requested_ids = [int(t) for t in tripIds.split(",")]
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT trip_id, user_id, visibility FROM trips WHERE trip_id = ANY(:ids)",
            {"ids": requested_ids},
        ).fetchall()
    rows_by_id = {row["trip_id"]: row for row in rows}
    if len(rows_by_id) < len(set(requested_ids)):
        abort(410)

    usernames = {
        user_id: get_username(user_id)
        for user_id in {row["user_id"] for row in rows}
    }
    users_by_name = {
        username: User.query.filter_by(username=username).first()
        for username in set(usernames.values())
    }

    friends_cache = {}
    visible_ids = []
    for trip_id in requested_ids:
        row = rows_by_id[trip_id]
        username = usernames[row["user_id"]]
        user = users_by_name[username]

        if not session.get(username):
            if row["visibility"] == "private":
                continue
            if row["visibility"] == "friends":
                if username not in friends_cache:
                    friends_cache[username] = current_user_is_friend_with(username)
                if not friends_cache[username]:
                    continue

        if (
            not session.get(user.username)
            and not user.is_public_trips()
            and not session.get(owner)
        ):
            abort(401)
        visible_ids.append(trip_id)

    if not visible_ids:
        abort(401)

    return render_template(
        "public/multi_trip.html",
        title=tag_name or lang[session["userinfo"]["lang"]]["sharedLink"],
        tripIds=",".join(str(trip_id) for trip_id in visible_ids),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


def convert_path_to_format(path, output_format, altitude=None, timestamps=None):
    """
    Convert the path data to the specified format (GPX or GeoJSON).

    `altitude` (metres) and `timestamps` (epoch seconds) are optional per-vertex
    arrays parallel to the path; when present, each GPX trackpoint gets an <ele>
    and/or an ISO-8601 UTC <time>, so a track imported with them round-trips.
    """
    # Load path data from JSON
    coordinates = json.loads(path)
    altitude = altitude if isinstance(altitude, list) else []
    timestamps = timestamps if isinstance(timestamps, list) else []

    if output_format == "gpx":
        # Create the GPX root element
        gpx = ET.Element("gpx", version="1.1", creator="Trainlog.me")

        # Create a GPX 'trk' (track) element
        trk = ET.SubElement(gpx, "trk")
        trk_name = ET.SubElement(trk, "name")
        trk_name.text = "Trip Path"

        # Create a 'trkseg' (track segment) and add 'trkpt' (track points) elements
        trkseg = ET.SubElement(trk, "trkseg")

        for i, point in enumerate(coordinates):
            trkpt = ET.SubElement(
                trkseg, "trkpt", lat=str(point[0]), lon=str(point[1])
            )
            ele = altitude[i] if i < len(altitude) else None
            if ele is not None:
                ET.SubElement(trkpt, "ele").text = str(ele)
            ts = timestamps[i] if i < len(timestamps) else None
            if ts is not None:
                ET.SubElement(trkpt, "time").text = datetime.utcfromtimestamp(
                    int(ts)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Convert the ElementTree to a string in GPX format
        output = ET.tostring(gpx, encoding="utf-8", method="xml").decode("utf-8")

    elif output_format == "geojson":
        # Convert to GeoJSON LineString format
        # GeoJSON requires coordinates in [lon, lat] format
        geojson_line = geojson.LineString(
            [(point[1], point[0]) for point in coordinates]
        )

        # Create the FeatureCollection containing the LineString
        feature = geojson.Feature(geometry=geojson_line)
        feature_collection = geojson.FeatureCollection([feature])

        # Convert FeatureCollection to GeoJSON string
        output = geojson.dumps(feature_collection)

    else:
        raise ValueError("Unsupported format")

    return output


def sanitize_filename(filename):
    """
    Sanitize the filename by keeping only alphanumerical characters and
    accentuated letters. Replace any other characters with underscores.
    """
    # Normalize Unicode to decompose characters
    normalized = unicodedata.normalize("NFKC", filename)
    # Replace invalid characters with an underscore
    sanitized = re.sub(r"[^\w\s\-\.À-ÿ]", "", normalized, flags=re.UNICODE)
    # Optionally replace spaces with underscores
    return sanitized.strip()


@app.route("/gpx/<trip_ids>", endpoint="download_gpx")
@app.route("/geojson/<trip_ids>", endpoint="download_geojson")
def download_path(trip_ids):
    """
    Download one or more paths in the specified format (GPX or GeoJSON) for
    the given trip_ids (comma-separated).
    """

    # Determine requested format based on the path
    if request.path.startswith("/gpx"):
        format_type = "gpx"
        file_extension = "gpx"
        mimetype = "application/gpx+xml"
    elif request.path.startswith("/geojson"):
        format_type = "geojson"
        file_extension = "geojson"
        mimetype = "application/geo+json"
    else:
        abort(400, description="Unsupported format")

    # Split the incoming <trip_ids> on commas
    trip_id_list = trip_ids.split(",")

    # Prepare a list to store (trip_id, generated_file_data) tuples
    files_to_zip = []

    for trip_id in trip_id_list:
        trip_id = trip_id.strip()  # Just to be safe

        # 1) Check if the trip exists + permission logic
        trip = get_trip_pg(trip_id)
        if trip is not None:
            user = User.query.filter_by(username=trip["username"]).first()
            # Verify that either user session is valid or the user has public trips
            if (
                not session.get(user.username)
                and not user.is_public_trips()
                and not session.get(owner)
            ):
                abort(401, description=f"Unauthorized for trip_id={trip_id}")
        else:
            abort(410, description=f"Trip with id={trip_id} is gone")

        # 2) Retrieve the path (and any 3D track) from the database
        with pg_session() as pg:
            path = pg.execute(
                get_user_lines_query(), {"ids": [int(trip_id)]}
            ).fetchone()
            track = pg.execute(
                "SELECT altitude, timestamps FROM paths WHERE trip_id = :id",
                {"id": int(trip_id)},
            ).fetchone()

        if path is None:
            abort(404, description=f"Path not found for trip_id={trip_id}")

        # 3) Convert the path to the requested format (with ele/time when present)
        output_data = convert_path_to_format(
            path["path"],
            format_type,
            altitude=track["altitude"] if track else None,
            timestamps=track["timestamps"] if track else None,
        )

        # 4) Store (trip_id, file contents) for later use
        files_to_zip.append(
            (trip_id, trip["origin_station"], trip["destination_station"], output_data)
        )

    if len(files_to_zip) == 1:
        # files_to_zip[0] is assumed to be a tuple like:
        # (trip_id, origin, destination, file_contents)
        single_id, single_origin, single_destination, single_data = files_to_zip[0]

        output_io = BytesIO()
        output_io.write(single_data.encode("utf-8"))
        output_io.seek(0)

        return send_file(
            output_io,
            as_attachment=True,
            download_name=sanitize_filename(
                f"{single_origin} -{single_destination}-{single_id}.{file_extension}"
            ),
            mimetype=mimetype,
        )

    # Otherwise, zip up all files.
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for trip_id, origin, destination, data in files_to_zip:
            filename = sanitize_filename(
                f"{origin} -{destination}-{trip_id}.{file_extension}"
            )
            zf.writestr(filename, data)

    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        as_attachment=True,
        download_name=f"Trainlog_{format_type}_export_{datetime.now().strftime('%Y-%m-%d')}.zip",
        mimetype="application/zip",
    )


@app.route("/u/<username>/current")
@login_required
def current(username):
    """
    Current trip
    """
    user_obj = User.query.filter_by(username=username).first()
    colorblind = getattr(user_obj, "colorblind", False) if user_obj else False
    return render_template(
        "current.html",
        title=lang[session["userinfo"]["lang"]]["current"],
        username=username,
        colorblind=colorblind,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )

@app.route("/u/<username>/getStats/<tripType>", methods=["GET"])
@app.route("/u/<username>/getStats/<year>/<tripType>", methods=["GET"])
@public_required
def get_stats_api(username, tripType, year=None):
    """JSON API endpoint for fetching stats (both trips and km)"""
    stats = fetch_stats(username, tripType, year)
    return jsonify(stats)


@app.route("/admin/getStats/<tripType>", methods=["GET"])
@app.route("/admin/getStats/<year>/<tripType>", methods=["GET"])
@owner_required
def get_admin_stats_api(tripType, year=None):
    """JSON API endpoint for fetching admin stats (both trips and km)"""
    stats = fetch_stats(None, tripType, year)
    return jsonify(stats)

# The stats page has three dimensions and they are equally important, so all
# three sit in the path, always in the same order: year, trip type, metric. The
# year that means "every year" is the literal segment "all" rather than a missing
# one — an omitted segment is what made the shorter forms ambiguous, since
# /stats/2024/train and /stats/train/km are both two segments and Flask cannot
# tell which is which.
#
# The shorter forms stay registered: they are what existing links and bookmarks
# use. They resolve to the same page, and the client rewrites the address to the
# full form once it has loaded.
STATS_METRICS = ("trips", "km", "duration", "carbon", "delay")


def stats_path_args(year, metric):
    """Normalise the year and metric segments of a stats URL."""
    return (
        None if year in (None, "all") else year,
        metric if metric in STATS_METRICS else "trips",
    )


@app.route("/public/<username>/stats/<year>/<tripType>/<metric>")
@app.route("/public/<username>/stats/<year>/<tripType>")
@app.route("/public/<username>/stats/<tripType>")
@app.route("/public/<username>/stats")
@public_required
def public_stats(username, tripType=None, year=None, metric=None):
    year, metric = stats_path_args(year, metric)
    if tripType in ('poi', 'accommodation', 'restaurant', 'walk', 'cycle', 'car'):
        abort(401)
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT DISTINCT trip_type FROM trips WHERE user_id = :user_id AND trip_type NOT IN ('poi', 'accommodation', 'restaurant', 'walk', 'cycle', 'car')",
            {"user_id": get_user_id(username)},
        ).fetchall()
    types = {
        t: {
            "label": lang[session["userinfo"]["lang"]][t],
            "group": TRIP_TYPE_GROUP_INDEX.get(t, len(TRIP_TYPE_GROUPS)),
        }
        for t in order_trip_types(row[0] for row in rows)
    }

    if tripType is None:
        return redirect(
            url_for(
                "public_stats",
                username=username,
                tripType="train",
                year=year or "all",
                metric=metric,
            )
        )
    distinctStatYears = get_distinct_stat_years(username, tripType)
    if year is not None and year not in distinctStatYears:
        return redirect(
            url_for(
                "public_stats",
                username=username,
                tripType=tripType,
                year="all",
                metric=metric,
            )
        )

    return render_template(
        "stats.html",
        nav="bootstrap/public_nav.html",
        isCurrent=has_current_trip(get_user_id(username)),
        is_public=True,
        title=lang[session["userinfo"]["lang"]]["stats"],
        username=username,
        statYear=year,
        statMetric=metric,
        logosList=listOperatorsLogos(),
        tripType=tripType,
        publicDistinctTypes=types,
        distinctStatYears=distinctStatYears,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/stats/<year>/<tripType>/<metric>")
@app.route("/admin/stats/<tripType>")
@app.route("/admin/stats/<year>/<tripType>")
@app.route("/admin/stats")
@owner_required
def admin_stats(tripType=None, year=None, metric=None):
    year, metric = stats_path_args(year, metric)
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT DISTINCT trip_type FROM trips WHERE trip_type NOT IN ('poi', 'accommodation', 'restaurant')"
        ).fetchall()
    types = {
        row[0]: lang[session["userinfo"]["lang"]][row[0]] for row in rows
    }

    if tripType is None:
        return redirect(
            url_for(
                "admin_stats", tripType="train", year=year or "all", metric=metric
            )
        )

    distinctStatYears = get_distinct_stat_years(None, tripType)  # Pass None for admin
    if year is not None and year not in distinctStatYears:
        return redirect(
            url_for("admin_stats", tripType=tripType, year="all", metric=metric)
        )

    return render_template(
        "stats.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        statYear=year,
        statMetric=metric,
        logosList=listOperatorsLogos(),
        tripType=tripType,
        admin=True,
        distinctStatYears=distinctStatYears,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/stats/<year>/<tripType>/<metric>")
@app.route("/u/<username>/stats/<year>/<tripType>")
@app.route("/u/<username>/stats/<tripType>")
@app.route("/u/<username>/stats")
@login_required
def stats(username, tripType=None, year=None, metric=None):
    year, metric = stats_path_args(year, metric)
    if tripType is None:
        return redirect(
            url_for(
                "stats",
                username=username,
                tripType="train",
                year=year or "all",
                metric=metric,
            )
        )
    distinctStatYears = get_distinct_stat_years(username, tripType)
    if year is not None and year not in distinctStatYears:
        return redirect(
            url_for(
                "stats",
                username=username,
                tripType=tripType,
                year="all",
                metric=metric,
            )
        )

    return render_template(
        "stats.html",
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id(username)),
        is_public=False,
        title=lang[session["userinfo"]["lang"]]["stats"],
        username=username,
        statYear=year,
        statMetric=metric,
        logosList=listOperatorsLogos(),
        tripType=tripType,
        distinctStatYears=distinctStatYears,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/privacy", defaults={"override_lang": None})
@app.route("/privacy/<override_lang>")
def privacy(override_lang):
    """
    Privacy Policy
    """
    user_lang = session["userinfo"]["lang"]
    chosen_lang = override_lang if override_lang in lang else user_lang

    return render_template(
        "privacy.html",
        title=lang[chosen_lang]["privacy_title"],
        nav="bootstrap/nav.html",
        **lang[chosen_lang],
        **session["userinfo"],
    )


@app.route("/u/<username>/logout")
def logout(username):
    """Logout user and redirect to login page with success message."""
    session.pop(username, None)
    session.pop("logged_in", None)
    flash(lang[session["userinfo"]["lang"]]["successLoggedOut"])
    return redirect(url_for("login"))


@app.route("/u/<username>/saveTrip", methods=["GET", "POST"])
def saveTrip(username):
    if not (session.get(username) or session.get(owner)):
        abort(401)
    if request.method == "POST":
        jsonPath = request.form["jsonPath"]
        newPath = json.loads(jsonPath)
        jsonNewTrip = request.form["newTrip"]
        newTrip = json.loads(jsonNewTrip)
        trip = saveTripToDb(
            username=username,
            newTrip=newTrip,
            newPath=newPath,
            trip_type=newTrip["type"],
        )
        if request.form.get("fromApp") == "true":
            return jsonify({
                "newTrip": trip.to_dict(),
            }), 200

    return ""


@app.route("/u/<username>/savePlanTrip", methods=["POST"])
def savePlanTrip(username):
    if not (session.get(username) or session.get(owner)):
        abort(401)
    newPath = json.loads(request.form["jsonPath"])
    newTrip = json.loads(request.form["newTrip"])
    plan = get_owned_plan(newTrip.get("plan_uuid"), username)
    savePlanTripToDb(username, newTrip, newPath, plan, trip_type=newTrip["type"])
    return ""


# ---------------------------------------------------------------------------
# Plans: parallel-world itineraries (separate tables; never in stats/the trips
# list). Authored with the normal trip builder, viewed/shared with the
# itinerary+map (new_trip.html), validatable into real trips.
# ---------------------------------------------------------------------------

def _plan_chrono_key(pt):
    """Chronological anchor key for a plan-trip, or None when the leg is unanchored
    (no day and no date) and therefore freely reorderable. Relative (Day N) legs sort
    before absolute dated ones (mirrors the historical ordering); within a day,
    untimed legs come after timed ones. Arrival day/time is the final tie-break so an
    overnight leg (it arrives on a later day) always sinks to the end of its departure
    day — nothing can depart on that day after you've already travelled into the next."""
    if pt["start_day"] is not None:  # relative leg
        st = pt["start_time"]
        ed = pt["end_day"] or pt["start_day"]
        return (0, pt["start_day"], (1,) if st is None else (0, st), ed)
    if pt["start_datetime"] is not None:  # absolute dated leg (precise / onlyDate)
        return (1, pt["start_datetime"], pt["end_datetime"] or pt["start_datetime"])
    return None  # undated (unknown) -> no anchor


def _plan_display_order(rows):
    """Display order for plan legs. `rows` arrive in user order (sort_order, uid).
    Anchored legs are re-sorted chronologically among themselves — their relative
    order is dictated by their day/time, not by the arrows — while unanchored legs
    keep exactly the slot the user put them in. The sort is stable, so legs with an
    identical key (e.g. two untimed legs on the same day) still follow the user
    order and thus stay swappable."""
    keyed = [(_plan_chrono_key(r._mapping), r) for r in rows]
    anchored_slots = [i for i, (k, _) in enumerate(keyed) if k is not None]
    anchored_sorted = sorted(
        (keyed[i] for i in anchored_slots), key=lambda kr: kr[0]
    )
    ordered = list(keyed)
    for slot, kr in zip(anchored_slots, anchored_sorted):
        ordered[slot] = kr
    return ordered


def build_plan_trip_list(plan_uuid):
    """(tripList, priceDict) in the SAME shape as processPublicTrips, built from
    plan_trips so new_trip.html renders a plan unchanged. Reuses formatTrip."""
    user_currency = getLoggedUserCurrency()
    empty = {"total_price": 0, "user_currency": user_currency, "total_carbon": 0, "total_distance": 0}
    with pg_session() as pg:
        plan = pg.execute(get_plan_query(), {"uuid": plan_uuid}).fetchone()
        if plan is None:
            return [], empty
        plan_uid = plan._mapping["uid"]
        rows = pg.execute(get_plan_trips_query(), {"plan_id": plan_uid}).fetchall()
        cost_rows = pg.execute(get_plan_costs_query(), {"plan_id": plan_uid}).fetchall()

    # Shared costs -> per-leg "ticket" fields, so a leg on a cost renders with the
    # existing ticket UI (name + per-leg share). Each cost is converted once; the
    # cost's full price is added to the total exactly once (total_shared).
    today = datetime.now().date()
    cost_by_id = {}
    total_shared = 0.0
    for c in cost_rows:
        cm = c._mapping
        if cm["price"] in (None, ""):
            continue
        cur = cm["currency"] or user_currency
        full = get_exchange_rate(base_currency=cur, target_currency=user_currency, date=today, price=float(cm["price"]))
        full = full if full is not None else float(cm["price"])
        total_shared += full
        per_leg_orig = float(cm["price_per_leg"]) if cm["price_per_leg"] else 0.0
        per_leg = per_leg_orig
        if per_leg_orig:
            conv = get_exchange_rate(base_currency=cur, target_currency=user_currency, date=today, price=per_leg_orig)
            per_leg = conv if conv is not None else per_leg_orig
        cost_by_id[cm["uid"]] = {
            "name": cm["name"], "currency": cur,
            "per_leg": round(per_leg, 2), "per_leg_orig": round(per_leg_orig, 2),
        }

    tripList = []
    total_price = total_carbon = total_distance = 0
    prev_end_pos = None  # arrival position of the previous timed leg, for connection checks
    ordered = _plan_display_order(rows)
    for idx, (chrono_key, r) in enumerate(ordered):
        pt = r._mapping
        coords = geom_geojson_to_coords(pt["geojson"])
        # Impossible connection: this timed leg departs before the previous one arrives.
        # Positions are timezone-correct and anchor-independent (see _plan_leg_positions),
        # so neither a timezone boundary nor anchor_date drift can fabricate a conflict.
        cur_start_pos, cur_end_pos = _plan_leg_positions(pt, coords)
        impossible = (
            prev_end_pos is not None
            and cur_start_pos is not None
            and cur_start_pos < prev_end_pos
        )
        if cur_end_pos is not None:
            prev_end_pos = cur_end_pos
        sdt = _fmt_legacy_dt(pt["start_datetime"]) if pt["start_datetime"] else None
        edt = _fmt_legacy_dt(pt["end_datetime"]) if pt["end_datetime"] else None
        usdt = _fmt_legacy_dt(pt["utc_start_datetime"]) if pt["utc_start_datetime"] else None
        uedt = _fmt_legacy_dt(pt["utc_end_datetime"]) if pt["utc_end_datetime"] else None
        if sdt is None:  # no date -> future sentinel (formatTrip handles 1/-1)
            sdt = edt = 1
        trip = {
            "uid": pt["uid"],
            "username": None,
            "type": pt["trip_type"],
            "origin_station": pt["origin_station"],
            "destination_station": pt["destination_station"],
            "start_datetime": sdt,
            "end_datetime": edt,
            "utc_start_datetime": usdt,
            "utc_end_datetime": uedt,
            "manual_trip_duration": pt["manual_trip_duration"],
            "estimated_trip_duration": pt["estimated_trip_duration"],
            "trip_length": pt["trip_length"] or 0,
            "operator": pt["operator"] or "",
            "line_name": pt["line_name"] or "",
            "material_type": pt["material_type"],
            "material_type_advanced": pt["material_type_advanced"],
            "seat": pt["seat"],
            "reg": pt["reg"],
            "notes": pt["notes"],
            "price": pt["price"],
            "currency": pt["currency"],
            "purchasing_date": pt["purchase_date"],
            "ticket_id": None,
            "visibility": pt["visibility"],
            "departure_delay": None,
            "arrival_delay": None,
            "logo_url": pt["logo_url"],
            "operator_name": pt["operator"] or "",
        }
        trip = formatTrip(trip)
        trip["utc_filtered_start_datetime"] = usdt if usdt is not None else sdt
        trip["utc_filtered_end_datetime"] = uedt if uedt is not None else edt
        # Plan legs are hypothetical -> always the coloured "planned" style, never
        # "past"/"current" (lockTime stops the client re-deriving it) and never the
        # near-invisible white "future" (that style only exists to declutter the
        # global map, which plans never appear on).
        trip["time"] = "plannedFuture"
        trip["day_number"] = pt["start_day"]
        trip["end_day_number"] = pt["end_day"]
        trip["weekdays"] = pt["weekdays"]
        trip["cost_id"] = pt["cost_id"]
        # A leg on a shared cost renders like a ticketed trip (reuse the ticket UI).
        cinfo = cost_by_id.get(pt["cost_id"])
        if cinfo:
            trip["ticket"] = cinfo["name"]
            trip["ticket_price_in_user_currency"] = cinfo["per_leg"]
            trip["ticket_price"] = cinfo["per_leg_orig"]
            trip["ticket_currency"] = cinfo["currency"]
            trip["user_currency"] = user_currency
        trip["carbon_footprint"] = (
            round(float(pt["carbon"]), 6) if pt["carbon"] is not None else 0
        )
        total_carbon += trip["carbon_footprint"]
        if (pt["trip_length"] or 0) > 0:
            total_distance += pt["trip_length"] / 1000
        if trip.get("price_in_user_currency") is not None:
            trip["user_currency"] = user_currency
            total_price += trip["price_in_user_currency"]
        # Weekday check: with Day 1 previewed at the plan's anchor_date, does this
        # leg's day fall on a weekday its service actually runs on? (weekdays is a
        # bitmask, bit 0 = Monday; NULL = runs daily.)
        weekday_mismatch = False
        if pt["weekdays"] is not None and pt["start_day"] is not None:
            leg_date = plan._mapping["anchor_date"] + timedelta(days=pt["start_day"] - 1)
            weekday_mismatch = not (pt["weekdays"] >> leg_date.weekday()) & 1
        # A leg can trade places with a neighbour when at least one of the two is
        # unanchored (no day/date), or when their anchors tie (e.g. two untimed legs
        # on the same day) — otherwise the chronology dictates the order and the
        # move buttons would be no-ops (the template hides them).
        can_swap = lambda a, b: a is None or b is None or a == b
        tripList.append(
            {"time": trip["time"], "trip": trip, "path": coords, "altitude": None,
             "timestamps": None, "lockTime": True, "impossible": impossible,
             "weekday_mismatch": weekday_mismatch,
             "can_up": idx > 0 and can_swap(chrono_key, ordered[idx - 1][0]),
             "can_down": idx < len(ordered) - 1 and can_swap(chrono_key, ordered[idx + 1][0]),
             # UTC-epoch departure/arrival used by compute_plan_stats for the span
             # (timezone-correct, anchor-independent — see _plan_leg_positions).
             "pos_start": cur_start_pos, "pos_end": cur_end_pos}
        )

    priceDict = {
        "total_price": total_price + total_shared,
        "user_currency": user_currency,
        "total_carbon": round(total_carbon, 6),
        "total_distance": round(total_distance, 2),
    }
    return tripList, priceDict


def _render_plan_view(plan, username, controls):
    """`username` is the viewer; `plan_author` below is the user who created the plan
    (not the site owner, which `owner` means everywhere else in this module)."""
    user = User.query.filter_by(username=username).first() if username else None
    author_username = get_username(plan["user_id"])
    data_url = (
        url_for("get_plan_trips_json", username=author_username, plan_uuid=plan["uuid"])
        if controls
        else url_for("public_plan_data", plan_uuid=plan["uuid"])
    )
    # Side-panel "save a copy" action, mirroring Ride along on trips/tags: only on the
    # read-only share view, and only for a logged-in viewer who is not the plan's author
    # (they would just be forking their own plan, which the management view already does).
    copy_url = (
        url_for("copy_plan_route", username=username, plan_uuid=plan["uuid"])
        # getUser() yields "public" for a visitor who is not logged in.
        if not controls and username not in (None, "public") and username != author_username
        else None
    )
    return render_template(
        "public/new_trip.html",
        logosList=listOperatorsLogos(),
        tripIds="",
        title=plan["name"],
        collection_voyage="voyage",
        tag_description=plan["name"],
        special_og=False,
        tileserver=user.tileserver if user else "default",
        globe=user.globe if user else False,
        og={},
        num_hidden_trips=0,
        colorblind=getattr(user, "colorblind", False) if user else False,
        planDataUrl=data_url,
        planControls=controls,
        relativeDates=True,
        plan=plan,
        plan_author=author_username,
        plan_copy_url=copy_url,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/plans")
@login_required
def plan_list(username):
    user = User.query.filter_by(username=username).first()
    with pg_session() as pg:
        rows = pg.execute(get_user_plans_query(), {"user_id": user.uid}).fetchall()
    plans = [dict(r._mapping) for r in rows]
    return render_template(
        "plans/plan_list.html",
        title="Plans",
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id(username)),
        user_plans=plans,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/plans/new", methods=["POST"])
@login_required
def submit_plan(username):
    user = User.query.filter_by(username=username).first()
    now = datetime.now()
    plan_uuid = str(uuid.uuid4())
    with pg_session() as pg:
        pg.execute(
            insert_plan_query(),
            {
                "uuid": plan_uuid,
                "user_id": user.uid,
                "name": sanitize_param(request.form.get("name") or "Plan"),
                "description": sanitize_param(request.form.get("description")),
                "anchor_date": request.form.get("anchor_date") or now.date(),
                "created": now,
                "last_modified": now,
            },
        )
    return redirect(url_for("plan_view", username=username, plan_uuid=plan_uuid))


def _fmt_dhm(seconds):
    """Seconds -> compact 'Xd Yh Zm' / 'Yh Zm' / 'Zm' string."""
    try:
        seconds = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        return ""
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


def _plan_leg_positions(pt, coords):
    """(departure, arrival) UTC-epoch positions for a plan-trip, used only to order and
    compare consecutive legs (span + impossible-connection checks).

    Relative ("Day N") legs are resolved on the fixed reference date (RELATIVE_REF_DATE)
    and converted to UTC from the path endpoints, so the comparison is correct even when
    consecutive legs sit in different timezones — and it never touches the stored
    datetimes, so anchor_date drift on legacy data can't fabricate false conflicts.
    Absolute (precise) legs use their stored UTC instants. `coords` is [[lat,lng],...].
    Returns (None, None) for untimed legs."""
    # Seconds since the Unix epoch, from a naive UTC datetime. (Not datetime.timestamp(),
    # which would reinterpret the naive value in the server's local timezone.)
    epoch = lambda dt: (dt - datetime(1970, 1, 1)).total_seconds() if dt is not None else None
    sd = pt["start_day"]
    if sd is not None:  # relative leg
        st, et = pt["start_time"], pt["end_time"]
        if st is None:  # untimed -> no concrete connection to check
            return None, None
        ed = pt["end_day"] or sd
        et = et if et is not None else st
        local_s = datetime.combine(RELATIVE_REF_DATE + timedelta(days=sd - 1), st)
        local_e = datetime.combine(RELATIVE_REF_DATE + timedelta(days=ed - 1), et)
        if coords:
            us = getUtcDatetime(lat=coords[0][0], lng=coords[0][1], dateTime=local_s)
            ue = getUtcDatetime(lat=coords[-1][0], lng=coords[-1][1], dateTime=local_e)
        else:  # no geometry to resolve a timezone -> compare the local clocks as-is
            us, ue = local_s, local_e
        return epoch(us), epoch(ue)
    return epoch(pt["utc_start_datetime"]), epoch(pt["utc_end_datetime"])  # absolute leg


def compute_plan_stats(trip_list, costs=None):
    """Totals + per-type breakdown (count/duration/distance/price) from a plan trip
    list. Prices are summed in the viewer's currency (already converted by formatTrip).
    `costs` are the plan's shared costs (rental car, ...) — counted once each in the
    PRIX total, on top of the per-leg prices.

    `span` is the end-to-end length of the whole trip — first departure to last
    arrival — i.e. "is this a 3-day or a 5-hour trip", independent of how much of that
    time is spent actually moving. It reads the per-leg positions attached by
    build_plan_trip_list (timezone-correct, anchor-independent)."""
    # Stays/stops aren't travelling and shouldn't inflate the trip's duration or span.
    NON_TRAVEL = ("poi", "accommodation", "restaurant")
    total_duration = total_distance = total_price = 0.0
    user_currency = getLoggedUserCurrency()
    per_type = {}
    pos_starts, pos_ends = [], []  # UTC-epoch departure/arrival (timed legs)
    day_starts, day_ends = [], []  # Day-N offsets (relative legs, for the day-only span)
    for item in trip_list:
        t = item["trip"]
        ty = t.get("type") or "other"
        travels = ty not in NON_TRAVEL
        dur = (t.get("trip_duration") or [None, 0])[1]
        dur = float(dur) if dur not in (None, "") else 0.0
        dist = float(t.get("trip_length") or 0)
        price = t.get("price_in_user_currency")
        price = float(price) if price not in (None, "") else 0.0
        if t.get("user_currency"):
            user_currency = t["user_currency"]
        if travels:
            # Timezone-correct, anchor-independent positions (see _plan_leg_positions);
            # day numbers feed the fallback when the plan is day-only (no clock times).
            ps, pe = item.get("pos_start"), item.get("pos_end")
            if ps is not None and pe is not None:
                pos_starts.append(ps)
                pos_ends.append(pe)
            dn = t.get("day_number")
            if dn is not None:
                day_starts.append(dn)
                day_ends.append(t.get("end_day_number") or dn)
            total_duration += dur
        total_distance += dist
        total_price += price
        agg = per_type.setdefault(ty, {"count": 0, "duration": 0.0, "distance": 0.0, "price": 0.0})
        agg["count"] += 1
        if travels:
            agg["duration"] += dur
        agg["distance"] += dist
        agg["price"] += price
    per_type_rows = sorted(
        ({"type": k, **v, "duration_h": (_fmt_dhm(v["duration"]) if v["duration"] else ""),
          "distance_km": round(v["distance"] / 1000),
          "price_str": (f"{round(v['price'])} {user_currency}" if v["price"] else "")}
         for k, v in per_type.items()),
        key=lambda r: r["duration"], reverse=True,
    )
    # Shared costs (rental car, ...): one price each, on top of the per-leg prices.
    total_shared = 0.0
    today = datetime.now().date()
    for c in costs or []:
        cp = c.get("price")
        if cp in (None, ""):
            continue
        converted = get_exchange_rate(
            base_currency=c.get("currency") or user_currency,
            target_currency=user_currency,
            date=today,
            price=float(cp),
        )
        total_shared += converted if converted is not None else float(cp)
    total_price += total_shared

    # Span: first departure to last arrival. Timed legs give a precise elapsed time
    # (positions are timezone-correct UTC epochs); a day-only plan has no clock times,
    # so fall back to the inclusive day count (Day 1 -> Day 2 is a 2-day trip).
    if pos_starts and pos_ends:
        span_seconds = max(pos_ends) - min(pos_starts)
    elif day_starts:
        span_seconds = (max(day_ends) - min(day_starts) + 1) * 86400.0
    else:
        span_seconds = 0.0
    return {
        "count": len(trip_list),
        "total_duration": total_duration,
        "total_duration_h": _fmt_dhm(total_duration),
        "total_distance_km": round(total_distance / 1000),
        "total_price": round(total_price),
        "shared_cost": round(total_shared),
        "has_shared": total_shared > 0,
        "has_price": total_price > 0,
        "user_currency": user_currency,
        "span_h": _fmt_dhm(span_seconds) if span_seconds > 0 else "",
        "span_seconds": span_seconds,
        "has_span": span_seconds > 0,
        "per_type": per_type_rows,
    }


@app.route("/u/<username>/plan/<plan_uuid>")
@login_required
def plan_view(username, plan_uuid):
    plan = get_owned_plan(plan_uuid, username)
    trip_list, _ = build_plan_trip_list(plan_uuid)
    # add a per-leg formatted duration for the management list (stays/stops have no
    # travel duration -> leave it blank rather than showing "0m")
    for item in trip_list:
        t = item["trip"]
        d = (t.get("trip_duration") or [None, 0])[1]
        static = t.get("type") in ("poi", "accommodation", "restaurant")
        t["duration_h"] = "" if static else _fmt_dhm(d)
        t["duration_seconds"] = 0 if static else (float(d) if d not in (None, "") else 0)
    with pg_session() as pg:
        plan_costs = [
            dict(r._mapping)
            for r in pg.execute(get_plan_costs_query(), {"plan_id": plan["uid"]}).fetchall()
        ]
    stats = compute_plan_stats(trip_list, costs=plan_costs)
    # The anchor date / Day-1 prompt only matter when some legs are relative (Day N).
    # A fully precise-dated plan needs neither.
    plan_has_relative = any(
        item["trip"].get("day_number") is not None for item in trip_list
    )
    # Localised vehicle-type names for the add-trip dropdown / breakdown (the lang
    # keys are the type ids themselves: train -> "Train", poi -> "Activity", ...).
    L = lang[session["userinfo"]["lang"]]
    type_labels = {
        vt: L.get(vt, vt)
        for vt in [
            "train", "bus", "tram", "metro", "air", "helicopter", "ferry", "car",
            "walk", "cycle", "aerialway", "funicular", "rail", "scooter", "ski",
            "accommodation", "poi", "restaurant", "other",
        ]
    }
    return render_template(
        "plans/plan.html",
        title=plan["name"],
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id(username)),
        plan=plan,
        plan_trips=trip_list,
        plan_stats=stats,
        plan_costs=plan_costs,
        plan_has_relative=plan_has_relative,
        type_labels=type_labels,
        currencyOptions=get_available_currencies(),
        user_currency=getLoggedUserCurrency(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/plan/<plan_uuid>/getPlanTrips")
@login_required
def get_plan_trips_json(username, plan_uuid):
    get_owned_plan(plan_uuid, username)
    tripList, priceDict = build_plan_trip_list(plan_uuid)
    return jsonify([tripList, priceDict])


@app.route("/u/<username>/plan/<plan_uuid>/update", methods=["POST"])
@login_required
def update_plan_route(username, plan_uuid):
    plan = get_owned_plan(plan_uuid, username)
    with pg_session() as pg:
        pg.execute(
            update_plan_query(),
            {
                "uid": plan["uid"],
                "user_id": plan["user_id"],
                "name": sanitize_param(request.form.get("name") or plan["name"]),
                "description": sanitize_param(request.form.get("description")),
                "anchor_date": request.form.get("anchor_date") or plan["anchor_date"],
                "last_modified": datetime.now(),
            },
        )
    return redirect(url_for("plan_view", username=username, plan_uuid=plan_uuid))


@app.route("/u/<username>/plan/<plan_uuid>/archive", methods=["POST"])
@login_required
def archive_plan_route(username, plan_uuid):
    plan = get_owned_plan(plan_uuid, username)
    archived = request.form.get("archived", "true") == "true"
    with pg_session() as pg:
        pg.execute(
            archive_plan_query(),
            {
                "uid": plan["uid"],
                "user_id": plan["user_id"],
                "archived": archived,
                "last_modified": datetime.now(),
            },
        )
    return redirect(url_for("plan_list", username=username))


@app.route("/u/<username>/plan/<plan_uuid>/delete", methods=["POST"])
@login_required
def delete_plan_route(username, plan_uuid):
    plan = get_owned_plan(plan_uuid, username)
    delete_plan(plan["uid"], plan["user_id"])
    return redirect(url_for("plan_list", username=username))


@app.route("/u/<username>/plan/<plan_uuid>/duplicate", methods=["POST"])
@login_required
def duplicate_plan_route(username, plan_uuid):
    """Fork a plan (with all its legs and shared costs) into a new draft."""
    plan = get_owned_plan(plan_uuid, username)
    suffix = lang[session["userinfo"]["lang"]].get("copySuffix", "(copy)")
    new_uuid = duplicate_plan(
        plan["uuid"], plan["user_id"], name=f"{plan['name']} {suffix}"
    )
    if new_uuid is None:
        abort(404)
    return redirect(url_for("plan_view", username=username, plan_uuid=new_uuid))


@app.route("/u/<username>/plan/<plan_uuid>/trip/<int:plan_trip_uid>/delete", methods=["POST"])
@login_required
def delete_plan_trip_route(username, plan_uuid, plan_trip_uid):
    plan = get_owned_plan(plan_uuid, username)
    delete_plan_trip(plan_trip_uid, plan["user_id"])
    return redirect(url_for("plan_view", username=username, plan_uuid=plan_uuid))


@app.route("/u/<username>/plan/<plan_uuid>/trip/<int:plan_trip_uid>/edit", methods=["POST"])
@login_required
def update_plan_trip_route(username, plan_uuid, plan_trip_uid):
    """Edit a plan-trip's timing + light metadata (keeps the existing path). UTC is
    recomputed from the stored geom endpoints so cross-timezone durations stay right."""
    plan = get_owned_plan(plan_uuid, username)
    with pg_session() as pg:
        row = pg.execute(
            "SELECT visibility, ST_AsGeoJSON(geom) AS geojson FROM plan_trips"
            " WHERE uid = :uid AND plan_id = :pid",
            {"uid": plan_trip_uid, "pid": plan["uid"]},
        ).fetchone()
    if row is None:
        abort(404)
    coords = geom_geojson_to_coords(row._mapping["geojson"])
    path = [{"lat": c[0], "lng": c[1]} for c in coords] or [
        {"lat": 0.0, "lng": 0.0}, {"lat": 0.0, "lng": 0.0}
    ]
    form = {
        "precision": request.form.get("precision", "relative"),
        "planStartDay": request.form.get("planStartDay"),
        "planStartTime": request.form.get("planStartTime"),
        "planEndDay": request.form.get("planEndDay"),
        "planEndTime": request.form.get("planEndTime"),
        "planWeekdays": request.form.get("planWeekdays"),
        "newTripStart": request.form.get("newTripStart"),
        "newTripEnd": request.form.get("newTripEnd"),
        "onlyDate": request.form.get("onlyDate"),
        "unknownType": request.form.get("unknownType"),
        "onlyDateDuration": request.form.get("onlyDateDuration", ""),
    }
    timing = process_plan_dates(form, path)
    now = datetime.now()
    with pg_session() as pg:
        pg.execute(
            update_plan_trip_query(),
            {
                "uid": plan_trip_uid,
                "user_id": plan["user_id"],
                "timing_mode": timing["timing_mode"],
                "start_day": timing["start_day"],
                "end_day": timing["end_day"],
                "start_time": timing["start_time"],
                "end_time": timing["end_time"],
                "weekdays": timing["weekdays"],
                "start_datetime": timing["start_datetime"],
                "end_datetime": timing["end_datetime"],
                "utc_start_datetime": timing["utc_start_datetime"],
                "utc_end_datetime": timing["utc_end_datetime"],
                "manual_trip_duration": timing["manual_trip_duration"],
                "operator": sanitize_param(request.form.get("operator")),
                "line_name": sanitize_param(request.form.get("line_name")),
                "notes": sanitize_param(request.form.get("notes")),
                "visibility": sanitize_param(
                    request.form.get("visibility") or row._mapping["visibility"]
                ),
                "last_modified": now,
            },
        )
    return redirect(url_for("plan_view", username=username, plan_uuid=plan_uuid))


def _get_plan_trip_row(plan, plan_trip_uid):
    """Fetch a single plan_trip row (with geojson) for the owning plan, or 404."""
    with pg_session() as pg:
        row = pg.execute(
            "SELECT *, ST_AsGeoJSON(geom) AS geojson FROM plan_trips"
            " WHERE uid = :uid AND plan_id = :pid",
            {"uid": plan_trip_uid, "pid": plan["uid"]},
        ).fetchone()
    if row is None:
        abort(404)
    return dict(row._mapping)


@app.route("/u/<username>/plan/<plan_uuid>/trip/<int:plan_trip_uid>/editor")
@login_required
def plan_trip_editor(username, plan_uuid, plan_trip_uid):
    """Open the full trip editor (edit_copy) for a plan-trip. Relative legs are edited
    as Day N + time (prefilled from their durable day/time columns); precise legs use
    the stored absolute datetimes. anchor_date is irrelevant here — it only matters at
    save-as-trips time (validate_plan)."""
    plan = get_owned_plan(plan_uuid, username)
    pt = _get_plan_trip_row(plan, plan_trip_uid)

    coords = geom_geojson_to_coords(pt["geojson"])  # [[lat,lng],...]
    # Routing waypoints are just the endpoints (+ any stored waypoints), NOT every geom
    # vertex — mirrors the normal trip editor (see the edit route's wplist logic).
    wplist = [coords[0], coords[-1]] if coords else [[0, 0], [0, 0]]
    if pt["waypoints"]:
        wp = [[p["lat"], p["lng"]] for p in json.loads(pt["waypoints"])]
        wplist = [coords[0]] + wp + [coords[-1]]

    sdt, edt = pt["start_datetime"], pt["end_datetime"]
    start_str = sdt.strftime("%Y-%m-%d %H:%M:%S") if sdt else ""
    end_str = edt.strftime("%Y-%m-%d %H:%M:%S") if edt else ""
    # Map the plan-trip timing onto edit_copy's precision model. Relative legs keep
    # the "Day N + time" editor (prefilled below); precise legs use the date pickers.
    plan_start_day = plan_end_day = plan_start_time = plan_end_time = None
    if pt["timing_mode"] == "relative":
        precision = "relative"
        plan_start_day = pt["start_day"] or 1
        plan_end_day = pt["end_day"] or plan_start_day
        plan_start_time = pt["start_time"].strftime("%H:%M") if pt["start_time"] else ""
        plan_end_time = pt["end_time"].strftime("%H:%M") if pt["end_time"] else ""
    elif sdt is None:
        precision = "unknown"
    elif sdt.second == 1:
        precision = "onlyDate"
    else:
        precision = "precise"

    if pt["manual_trip_duration"] is not None:
        h, rem = divmod(int(pt["manual_trip_duration"]), 3600)
        m = rem // 60
    else:
        h = lang[session["userinfo"]["lang"]]["hours"]
        m = lang[session["userinfo"]["lang"]]["minutes"]

    price = pt["price"]
    if price not in (None, ""):
        price = price if price % 1 != 0 else int(price)
    else:
        price = ""

    pdate = pt["purchase_date"]
    # Default the purchase date to today when none is stored, so editing never leaves
    # a price without a date.
    purchasing_date = (
        pdate.strftime("%Y-%m-%d")
        if isinstance(pdate, (datetime, date))
        else date.today().strftime("%Y-%m-%d")
    )

    user_obj = User.query.filter_by(username=username).first()
    colorblind = getattr(user_obj, "colorblind", False) if user_obj else False

    return render_template(
        "edit_copy.html",
        title=lang[session["userinfo"]["lang"]]["edit"],
        start_datetime=start_str,
        end_datetime=end_str,
        currencyOptions=get_available_currencies(),
        unknownType=None,
        precision=precision,
        tripId=plan_trip_uid,
        origin=pt["origin_station"],
        destination=pt["destination_station"],
        trip=pt,
        fr24_calls=fr24_usage(username),
        edit_copy_type="edit",
        country_list=get_all_countries(),
        username=username,
        tripOperator=pt["operator"] or "",
        tripHours=h or "",
        tripMinutes=m or "",
        tripLineName=pt["line_name"] or "",
        tripVisibility=pt["visibility"] or "",
        tripMaterialType=pt["material_type"] or "",
        tripMaterialTypeAdvanced=pt["material_type_advanced"] or "",
        tripSeat=pt["seat"] or "",
        tripReg=pt["reg"] or "",
        tripPrice=price if price is not None else "",
        tripCurrency=pt["currency"] or "",
        tripPurchasingDate=purchasing_date,
        tripBooked=pt["booked"],
        tripType=pt["trip_type"],
        tripTicketId="",
        wplist=wplist,
        tripNotes=pt["notes"] or "",
        colorblind=colorblind,
        tripDepartureDelay="",
        tripArrivalDelay="",
        tripPowerType=pt["power_type"],
        tripCo2Override=pt["co2_override"],
        # plan context: switches the save target + redirect inside edit_copy.html,
        # and (for relative legs) prefills the Day N + time editor.
        plan_uuid=plan_uuid,
        plan_trip_uid=plan_trip_uid,
        planStartDay=plan_start_day,
        planEndDay=plan_end_day,
        planStartTime=plan_start_time,
        planEndTime=plan_end_time,
        planWeekdays=pt["weekdays"] or 0,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/plan/<plan_uuid>/trip/<int:plan_trip_uid>/edit_full", methods=["POST"])
@login_required
def update_plan_trip_full_route(username, plan_uuid, plan_trip_uid):
    """Save a plan-trip edited in the rich editor: route geometry + all metadata +
    timing. Relative legs keep their day-offset semantics (Day N + time), stored
    independently of the plan's anchor_date (see process_plan_dates)."""
    plan = get_owned_plan(plan_uuid, username)
    pt = _get_plan_trip_row(plan, plan_trip_uid)
    formData = dict(request.form)

    # Path: edited on the map, otherwise keep the stored geometry.
    if formData.get("path"):
        path = [{"lat": c["lat"], "lng": c["lng"]} for c in json.loads(formData["path"])]
    else:
        coords = geom_geojson_to_coords(pt["geojson"])
        path = [{"lat": c[0], "lng": c[1]} for c in coords]
    if not path:
        path = [{"lat": 0.0, "lng": 0.0}, {"lat": 0.0, "lng": 0.0}]

    # The editor exposes the timing modes natively (relative Day N + time, precise,
    # onlyDate, unknown), so honour whatever was chosen.
    timing = process_plan_dates(formData, path)

    # Distance/duration/countries: recompute when the route was (re-)drawn, else keep.
    details_parsed = json.loads(formData["details"]) if formData.get("details") else None
    power_type = formData.get("powerType") or (
        details_parsed.get("powerType") if details_parsed else None
    )
    co2_override = float(formData["co2Override"]) if formData.get("co2Override") else None
    trip_type = pt["trip_type"]
    if "estimated_trip_duration" in formData and "trip_length" in formData:
        trip_length = sanitize_param(formData["trip_length"])
        estimated_trip_duration = sanitize_param(formData["estimated_trip_duration"])
        if trip_type in ("air", "helicopter"):
            c = {}
            c[getCountryFromCoordinates(**path[0])["countryCode"]] = float(trip_length or 0) / 2
            c[getCountryFromCoordinates(**path[-1])["countryCode"]] = float(trip_length or 0) / 2
            countries = json.dumps(c)
        else:
            countries = getCountriesFromPath(path, trip_type, details_parsed, power_type)
    elif power_type in ("electric", "thermic", "manual"):
        # Power changed without re-routing: recompute the electrified split from the
        # existing path (deterministic for explicit power types — see updateTrip).
        trip_length = pt["trip_length"]
        estimated_trip_duration = pt["estimated_trip_duration"]
        countries = getCountriesFromPath(path, trip_type, None, power_type)
    else:
        trip_length = pt["trip_length"]
        estimated_trip_duration = pt["estimated_trip_duration"]
        countries = pt["countries"]

    has_price = formData.get("price") not in (None, "")
    now = datetime.now()
    plan_trip = PlanTrip(
        plan_id=plan["uid"],
        user_id=plan["user_id"],
        origin_station=sanitize_param(formData.get("origin_station")) or pt["origin_station"],
        destination_station=sanitize_param(formData.get("destination_station")) or pt["destination_station"],
        trip_type=trip_type,
        operator=sanitize_param(formData.get("operator")),
        line_name=sanitize_param(formData.get("lineName")),
        material_type=sanitize_param(formData.get("material_type")),
        material_type_advanced=sanitize_param(formData.get("material_type_advanced")),
        reg=sanitize_param(formData.get("reg")),
        seat=sanitize_param(formData.get("seat")),
        notes=sanitize_param(formData.get("notes")),
        trip_length=trip_length,
        estimated_trip_duration=estimated_trip_duration,
        countries=countries,
        price=sanitize_param(formData.get("price")),
        currency=sanitize_param(formData.get("currency")) if has_price else None,
        purchase_date=sanitize_param(formData.get("purchasing_date")) if has_price else None,
        booked=has_price and formData.get("booked") in ("true", "on", "1"),
        waypoints=sanitize_param(formData.get("waypoints")) or pt["waypoints"],
        visibility=sanitize_param(formData.get("visibility")) or pt["visibility"],
        path=path,
        timing=timing,
        power_type=power_type,
        co2_override=co2_override,
        sort_order=pt["sort_order"],
        created=pt["created"],
        last_modified=now,
    )
    update_plan_trip_full(plan_trip_uid, plan_trip)
    # edit_copy posts via AJAX and redirects client-side.
    return ""


@app.route("/u/<username>/plan/<plan_uuid>/reorder", methods=["POST"])
@login_required
def reorder_plan_trips_route(username, plan_uuid):
    plan = get_owned_plan(plan_uuid, username)
    order = (request.get_json() or {}).get("order", [])
    with pg_session() as pg:
        for i, uid in enumerate(order):
            pg.execute(
                reorder_plan_trip_query(),
                {"uid": int(uid), "plan_id": plan["uid"], "user_id": plan["user_id"], "sort_order": i},
            )
    return ("", 204)


@app.route("/u/<username>/plan/<plan_uuid>/cost", methods=["POST"])
@login_required
def submit_plan_cost_route(username, plan_uuid):
    """Create a shared cost on the plan (rental car, rail pass, ...)."""
    plan = get_owned_plan(plan_uuid, username)
    name = sanitize_param(request.form.get("name"))
    price = request.form.get("price")
    if not name or not price:
        abort(400)
    now = datetime.now()
    with pg_session() as pg:
        pg.execute(
            insert_plan_cost_query(),
            {
                "plan_id": plan["uid"],
                "name": name,
                "price": float(price),
                "currency": sanitize_param(request.form.get("currency")) or getLoggedUserCurrency(),
                "notes": sanitize_param(request.form.get("notes")),
                "created": now,
                "last_modified": now,
            },
        )
    return redirect(url_for("plan_view", username=username, plan_uuid=plan_uuid))


@app.route("/u/<username>/plan/<plan_uuid>/cost/<int:cost_uid>/edit", methods=["POST"])
@login_required
def update_plan_cost_route(username, plan_uuid, cost_uid):
    plan = get_owned_plan(plan_uuid, username)
    name = sanitize_param(request.form.get("name"))
    price = request.form.get("price")
    if not name or not price:
        abort(400)
    with pg_session() as pg:
        pg.execute(
            update_plan_cost_query(),
            {
                "uid": cost_uid,
                "plan_id": plan["uid"],
                "name": name,
                "price": float(price),
                "currency": sanitize_param(request.form.get("currency")) or getLoggedUserCurrency(),
                "notes": sanitize_param(request.form.get("notes")),
                "last_modified": datetime.now(),
            },
        )
    return redirect(url_for("plan_view", username=username, plan_uuid=plan_uuid))


@app.route("/u/<username>/plan/<plan_uuid>/cost/<int:cost_uid>/delete", methods=["POST"])
@login_required
def delete_plan_cost_route(username, plan_uuid, cost_uid):
    plan = get_owned_plan(plan_uuid, username)
    with pg_session() as pg:
        pg.execute(delete_plan_cost_query(), {"uid": cost_uid, "plan_id": plan["uid"]})
    return redirect(url_for("plan_view", username=username, plan_uuid=plan_uuid))


@app.route("/u/<username>/plan/<plan_uuid>/trip/<int:plan_trip_uid>/cost", methods=["POST"])
@login_required
def set_plan_trip_cost_route(username, plan_uuid, plan_trip_uid):
    """Attach/detach a leg to a shared cost (the per-leg dropdown). cost_id "" -> NULL."""
    plan = get_owned_plan(plan_uuid, username)
    cost_id = (request.get_json() or {}).get("cost_id")
    cost_id = int(cost_id) if cost_id not in (None, "", "none") else None
    with pg_session() as pg:
        pg.execute(
            set_plan_trip_cost_query(),
            {"uid": plan_trip_uid, "plan_id": plan["uid"], "cost_id": cost_id,
             "last_modified": datetime.now()},
        )
    return ("", 204)


@app.route("/u/<username>/plan/<plan_uuid>/import_trips", methods=["POST"])
@login_required
def import_trips_to_plan_route(username, plan_uuid):
    """Move existing real trips (comma/space separated ids) into the plan."""
    plan = get_owned_plan(plan_uuid, username)
    ids = [int(x) for x in re.findall(r"\d+", request.form.get("trip_ids", ""))]
    if ids:
        import_trips_to_plan(plan, ids)
    return redirect(url_for("plan_view", username=username, plan_uuid=plan_uuid))


@app.route("/u/<username>/plan/<plan_uuid>/validate", methods=["POST"])
@login_required
def validate_plan_route(username, plan_uuid):
    plan = get_owned_plan(plan_uuid, username)
    start_date_str = request.form.get("start_date")
    # A start date is only needed to anchor relative (Day N) legs; a precise-dated
    # plan can be logged as-is, so fall back to the stored anchor date when omitted.
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    else:
        start_date = plan["anchor_date"]
    tag_uuid = validate_plan(plan, start_date)
    # Land on the new tag grouping the validated trips; fall back to the plan list
    # for an empty plan (no trips -> no tag).
    if tag_uuid:
        return redirect(url_for("public_trip", tagId=tag_uuid))
    return redirect(url_for("plan_list", username=username))


def _plan_public_or_403(plan_uuid):
    """Fetch a plan by uuid for public viewing: owner always; otherwise the owner
    must have public trips. Returns the plan dict or aborts."""
    with pg_session() as pg:
        row = pg.execute(get_plan_query(), {"uuid": plan_uuid}).fetchone()
    if row is None:
        abort(410)
    plan = dict(row._mapping)
    owner_user = User.query.filter_by(uid=plan["user_id"]).first()
    if owner_user is None:
        abort(410)
    if (
        not session.get(owner_user.username)
        and not owner_user.is_public_trips()
        and not session.get(owner)
    ):
        abort(401)
    return plan


@app.route("/public/plan/<plan_uuid>")
def public_plan(plan_uuid):
    plan = _plan_public_or_403(plan_uuid)
    # Read-only preview for everyone, owner included: all editing lives on the plan
    # management page, so the map/share view stays an uncluttered visualisation
    # (no controls bar to crowd small screens).
    return _render_plan_view(plan, getUser(), controls=False)


@app.route("/public/plan/<plan_uuid>/getPlanTrips")
def public_plan_data(plan_uuid):
    _plan_public_or_403(plan_uuid)
    tripList, priceDict = build_plan_trip_list(plan_uuid)
    return jsonify([tripList, priceDict])


@app.route("/u/<username>/plans/copy/<plan_uuid>", methods=["POST"])
@login_required
def copy_plan_route(username, plan_uuid):
    """Save a shared plan into `username`'s own plans. `username` is the user doing the
    copying (enforced by login_required, so it is the logged-in user); the source plan
    may have been created by a different user, and read access is checked exactly as
    the public plan view checks it. The copy is an independent plan — later edits on
    either side do not propagate."""
    plan = _plan_public_or_403(plan_uuid)
    user = User.query.filter_by(username=username).first()
    suffix = lang[session["userinfo"]["lang"]]["copySuffix"]
    new_uuid = duplicate_plan(
        plan["uuid"],
        user.uid,
        name=f"{plan['name']} {suffix}",
        require_same_user=False,
    )
    if new_uuid is None:
        abort(404)
    return redirect(url_for("plan_view", username=username, plan_uuid=new_uuid))


@app.route("/u/<username>/plan/<plan_uuid>/copy_to_user", methods=["POST"])
@owner_required
def copy_plan_to_user_route(username, plan_uuid):
    """Site-owner tool: copy any plan into any account. `username` is the plan's author
    (whose management page the action was triggered from) and the recipient comes from
    the `target` form field; regular users copy shared plans through copy_plan_route
    instead. The copy keeps the original name — it lands in an account that has no other
    copy of it — and is independent of the source plan."""
    with pg_session() as pg:
        row = pg.execute(get_plan_query(), {"uuid": plan_uuid}).fetchone()
    if row is None:
        abort(410)
    plan = dict(row._mapping)
    target_username = (request.form.get("target") or "").strip()
    target = User.query.filter_by(username=target_username).first()
    if target is None:
        abort(404)
    new_uuid = duplicate_plan(
        plan["uuid"], target.uid, name=plan["name"], require_same_user=False
    )
    if new_uuid is None:
        abort(404)
    logger.info(
        f"owner copied plan {plan_uuid} (by {username}) to {target_username} -> {new_uuid}"
    )
    # Land on the copy in the recipient's account, so the result is visible immediately.
    return redirect(url_for("plan_view", username=target_username, plan_uuid=new_uuid))


@app.route("/u/<username>/scottySaveTrip", methods=["GET", "POST"])
def scottySaveTrip(username):
    if not (session.get(username) or session.get(owner)):
        abort(401)
    routerPolyline = None

    if request.method == "POST":
        # Parse inputs
        routerPolylineStr = request.form.get("routerPolyline", None)
        if routerPolylineStr:
            routerPolyline = [
                {"lat": node[0], "lng": node[1]}
                for node in json.loads(routerPolylineStr)
            ]

        jsonPath = request.form["jsonPath"]
        waypoints = json.loads(jsonPath)  # Decode JSON string to list of waypoints
        jsonNewTrip = request.form["newTrip"]
        newTrip = json.loads(jsonNewTrip)

        # Extract trip type
        trip_type = newTrip.get("type")
        if not trip_type:
            return "Error: Trip type is required.", 400

        # Build path for the router
        if trip_type in [
            "train",
            "metro",
            "tram",
            "ferry",
            "aerialway",
            "bus",
            "car",
            "walk",
            "cycle",
        ]:
            # Convert waypoints to OSRM path format (e.g., "lng1,lat1;lng2,lat2;...")
            path = ";".join(f"{wp['lng']},{wp['lat']}" for wp in waypoints)

            # Determine the routing type and forward the request
            router_path = (
                f"route/v1/{'driving' if trip_type == 'bus' else trip_type}/{path}"
            )
            response = forwardRouting(router_path, trip_type)

            # Parse the router response (if necessary for your DB structure)
            if hasattr(response, 'text'):
                routing_result = json.loads(response.text)
            elif hasattr(response, 'json'):
                routing_result = response.json
            else:
                routing_result = json.loads(response)

            # If the router returns an error, handle it
            if routing_result["code"] != "Ok":
                newTrip["notes"] = "Automatically routed, Saved with errors"
                saveTripToDb(
                    username=username,
                    newTrip=newTrip,
                    newPath=waypoints if not routerPolyline else routerPolyline,
                    trip_type=trip_type,
                )
                print(routing_result)
                return "Error in routing", 500

            else:
                newPath = [
                    {"lat": coord[0], "lng": coord[1]}
                    for coord in polyline.decode(
                        routing_result["routes"][0]["geometry"]
                    )
                ]
                newTrip["trip_length"] = routing_result["routes"][0]["distance"]
                newTrip["estimated_trip_duration"] = routing_result["routes"][0][
                    "duration"
                ]
                # Save the trip to the database
                newTrip["notes"] = "Automatically routed"
                saveTripToDb(
                    username=username,
                    newTrip=newTrip,
                    newPath=newPath,  # Use the routing response as the new path
                    trip_type=trip_type,
                )

                return "Trip saved successfully", 200
        else:
            return f"Unsupported trip type: {trip_type}", 400


@app.route("/u/<username>/saveFlight/<type>", methods=["GET", "POST"])
@login_required
def saveFlight(username, type):
    if request.method == "POST":
        jsonPath = request.form["jsonPath"]
        newPath = json.loads(jsonPath)
        jsonNewTrip = request.form["newTrip"]
        newTrip = json.loads(jsonNewTrip)
        airlineLogoProcess(newTrip)
        # TODO : Fix visibility for flights
        newTrip["visibility"] = "public"

        # Building a flight for a plan -> write to plan_trips (parallel world), reusing
        # savePlanTripToDb's air branch. The 3D track isn't kept for plans.
        plan_uuid = newTrip.get("plan_uuid")
        if plan_uuid:
            plan = get_owned_plan(plan_uuid, username)
            savePlanTripToDb(
                username=username, newTrip=newTrip, newPath=newPath, plan=plan, trip_type=type
            )
            return ""

        # Optional 3D track (altitude in metres, timestamps in epoch seconds),
        # each parallel to newPath. Passed through as JSON strings (or None).
        altitude = request.form.get("altitude") or None
        timestamps = request.form.get("timestamps") or None
        # FR24 imports carry a real 3D track (altitude/timestamps); a bare geodesic does not.
        newTrip.setdefault("route_source", "fr24" if altitude else "router")
        trip = saveTripToDb(
            username=username,
            newTrip=newTrip,
            newPath=newPath,
            trip_type=type,
            altitude=altitude,
            timestamps=timestamps,
        )
        if request.form.get("fromApp") == "true":
            return jsonify({
                "newTrip": trip.to_dict(),
            }), 200

    return ""


@app.route("/u/<username>/deleteTrip", methods=["GET", "POST"])
@login_required
def deleteTrip(username):
    if request.method == "POST":
        data = json.loads(request.form["tripId"])
        tripIds = data if isinstance(data, list) else [data]
        for id in tripIds:
            delete_trip(id, username)

    return ""


@app.route("/u/<username>/updateTrip", methods=["GET", "POST"])
@login_required
def updateTrip(username):
    if request.method == "POST":
        formData = dict(request.form)
        trip_id = formData["trip_id"]

        check_current_user_owns_trip(trip_id)

        new_trip = update_trip_values_from_form_data(trip_id, formData)
        update_trip(trip_id, new_trip, formData)
    return ""


@app.route("/u/<username>/copyTrip", methods=["GET", "POST"])
@login_required
def copyTrip(username):
    if request.method == "POST":
        formData = dict(request.form)
        trip_id = formData["trip_id"]

        check_current_user_owns_trip(trip_id)

        new_trip_id = duplicate_trip(trip_id)
        new_trip = update_trip_values_from_form_data(new_trip_id, formData)

        update_trip(new_trip_id, new_trip, formData)
        return jsonify(new_trip_id)
    return ""


def check_current_user_owns_trip(trip_id):
    """
    Ensures that a given trip belongs to the currently logged in user
    """
    with pg_session() as pg:
        row = pg.execute(
            "SELECT user_id FROM trips WHERE trip_id = :trip_id", {"trip_id": trip_id}
        ).fetchone()

    if row is None:
        abort(404)  # Trip does not exist
    trip_username = get_username(row["user_id"])
    if getUser() not in (trip_username, owner):
        logger.error(
            f"User {getUser()} tried to access trip {trip_id} owned by {trip_username}"
        )
        abort(404)  # Trip does not belong to the user

def get_trip(trip_id):
    trip = get_trip_pg(trip_id)

    with pg_session() as pg:
        pathResult = pg.execute(
            get_user_lines_query(), {"ids": [int(trip_id)]}
        ).fetchone()
    path = json.loads(pathResult["path"])

    return Trip(
        trip_id=trip_id,
        username=sanitize_param(trip["username"]),
        user_id=get_user_id(trip["username"]),
        origin_station=sanitize_param(trip["origin_station"]),
        destination_station=sanitize_param(trip["destination_station"]),
        start_datetime=sanitize_param(trip["start_datetime"])
        if trip["start_datetime"] not in [-1, 1]
        else None,
        utc_start_datetime=sanitize_param(trip["utc_start_datetime"]),
        end_datetime=sanitize_param(trip["end_datetime"])
        if trip["end_datetime"] not in [-1, 1]
        else None,
        utc_end_datetime=sanitize_param(trip["utc_end_datetime"]),
        trip_length=sanitize_param(trip["trip_length"]),
        estimated_trip_duration=sanitize_param(trip["estimated_trip_duration"]),
        manual_trip_duration=sanitize_param(trip["manual_trip_duration"]),
        operator=sanitize_param(trip["operator"]),
        countries=sanitize_param(trip["countries"]),
        line_name=sanitize_param(trip["line_name"]),
        created=sanitize_param(trip["created"]),
        last_modified=sanitize_param(trip["last_modified"]),
        type=sanitize_param(trip["type"]),
        seat=sanitize_param(trip["seat"]),
        seat_car=trip.get("seat_car"),
        material_type=sanitize_param(trip["material_type"]),
        material_type_advanced=sanitize_param(trip.get("material_type_advanced")),
        reg=sanitize_param(trip["reg"]),
        waypoints=sanitize_param(trip["waypoints"]),
        notes=sanitize_param(trip["notes"]),
        price=sanitize_param(trip["price"]),
        currency=sanitize_param(trip["currency"]),
        purchasing_date=sanitize_param(trip["purchasing_date"]),
        ticket_id=sanitize_param(trip["ticket_id"]),
        is_project=trip["start_datetime"] == 1 or trip["end_datetime"] == 1,
        path=path,
        departure_delay=trip.get("departure_delay"),
        arrival_delay=trip.get("arrival_delay"),
        route_source=trip.get("route_source") or "router",
    )


def sanitize_param(param):
    return param if param != "" else None


def parse_seat_car(param):
    """Form value for the marked car (a stringified array index) -> int or None."""
    param = sanitize_param(param)
    return int(param) if param is not None else None


def update_trip_values_from_form_data(trip_id, formData, update_created_ts=False):
    with pg_session() as pg:
        pathResult = pg.execute(
            get_user_lines_query(), {"ids": [int(trip_id)]}
        ).fetchone()

    if "path" in formData.keys():
        path = [[coord["lat"], coord["lng"]] for coord in json.loads(formData["path"])]
    else:
        path = json.loads(pathResult["path"])

    limits = [
        {
            "lat": path[0][0],
            "lng": path[0][1],
        },
        {
            "lat": path[-1][0],
            "lng": path[-1][1],
        },
    ]
    (
        manual_trip_duration,
        start_datetime,
        end_datetime,
        utc_start_datetime,
        utc_end_datetime,
    ) = processDates(formData, limits)

    original_trip = get_trip(trip_id)

    # powerType lives on the main form (captured by serializeArray); keep the
    # details JSON as a fallback for routes that still send it via the map modal.
    details_parsed = json.loads(formData["details"]) if formData.get("details") else None
    power_type = formData.get("powerType") or (details_parsed.get("powerType") if details_parsed else None)
    co2_override = float(formData["co2Override"]) if formData.get("co2Override") else None

    if "estimated_trip_duration" in formData and "trip_length" in formData:
        countries = getCountriesFromPath(
            [
                {"lat": coord[0], "lng": coord[1]} for coord in path],
                formData["type"],
                details_parsed,
                power_type,
        )
        estimated_trip_duration = sanitize_param(formData["estimated_trip_duration"])
        trip_length = sanitize_param(formData["trip_length"])
    elif power_type in ("electric", "thermic", "manual"):
        # Power changed on the main form without re-routing: recompute the
        # elec/nonelec split from the existing path. Deterministic for explicit
        # power types (all-electric / all-thermic), so no OSM routing data is
        # needed. 'auto' is excluded so OSM-derived splits aren't discarded.
        countries = getCountriesFromPath(
            [{"lat": coord[0], "lng": coord[1]} for coord in path],
            formData["type"],
            None,
            power_type,
        )
        estimated_trip_duration = original_trip.estimated_trip_duration
        trip_length = original_trip.trip_length
    else:
        countries = original_trip.countries
        estimated_trip_duration = original_trip.estimated_trip_duration
        trip_length = original_trip.trip_length

    created = datetime.now() if update_created_ts else original_trip.created

    if "visibility" in formData:
        visibility = sanitize_param(formData["visibility"])
    else:
        visibility = None

    trip = Trip(
        username=getUser(),
        user_id=get_user_id(getUser()),
        origin_station=sanitize_param(formData["origin_station"]),
        destination_station=sanitize_param(formData["destination_station"]),
        start_datetime=start_datetime if start_datetime not in [-1, 1] else None,
        utc_start_datetime=utc_start_datetime,
        end_datetime=end_datetime if end_datetime not in [-1, 1] else None,
        utc_end_datetime=utc_end_datetime,
        trip_length=trip_length,
        estimated_trip_duration=estimated_trip_duration,
        manual_trip_duration=manual_trip_duration,
        operator=sanitize_param(formData["operator"]),
        countries=countries,
        line_name=sanitize_param(formData["lineName"]),
        created=created,
        last_modified=datetime.now(),
        type=original_trip.type,
        seat=sanitize_param(formData["seat"]),
        seat_car=parse_seat_car(formData.get("seat_car")),
        material_type=sanitize_param(formData["material_type"]),
        material_type_advanced=sanitize_param(formData.get("material_type_advanced")),
        reg=sanitize_param(formData["reg"]),
        waypoints=sanitize_param(formData.get("waypoints", original_trip.waypoints)),
        notes=sanitize_param(formData["notes"]),
        price=sanitize_param(formData["price"]),
        currency=sanitize_param(formData.get("currency"))
        if formData["price"] != ""
        else None,
        purchasing_date=sanitize_param(formData.get("purchasing_date"))
        if formData["price"] != ""
        else None,
        ticket_id=sanitize_param(formData.get("ticket_id")),
        is_project=start_datetime == 1 or end_datetime == 1,
        path=path,
        visibility=visibility if visibility != "" else None,
        departure_delay=sanitize_param(formData.get("departure_delay")),
        arrival_delay=sanitize_param(formData.get("arrival_delay")),
        power_type=power_type,
        co2_override=co2_override,
        # Re-drawing/importing sends a fresh source; plain metadata edits keep the stored one.
        route_source=formData.get("route_source") or original_trip.route_source,
    )

    # Fresh 3D flight track when the route was (re-)imported from FR24; absent on
    # plain metadata edits, where update_trip's COALESCE preserves the stored one.
    trip.altitude = formData.get("altitude") or None
    trip.timestamps = formData.get("timestamps") or None

    return trip


@app.route(
    "/u/<username>/hereRouteDisplay/<origin_name>/<destination_name>/<origin>/<destination>/<startDatetime>",
    methods=["GET"],
)
def here_route_display(
    username, origin, destination, startDatetime, origin_name, destination_name
):
    # 1) Grab the ?modes= from the querystring if present
    modes = request.args.get("modes", "")  # e.g. "bus,subway" or "-bus,-subway"

    # 2) Call HERE API
    api_key = load_config()["here"]["APIKey"]
    base_url = "https://transit.router.hereapi.com/v8/routes"
    params = {
        "origin": origin,
        "destination": destination,
        "return": "intermediate,polyline",
        "departureTime": startDatetime + ":00",
        "apiKey": api_key,
    }

    # If user specified modes (include or exclude), add them
    if modes:
        params["modes"] = modes
    try:
        r = requests.get(base_url, params=params)
        r.raise_for_status()
        data = r.json()
        data["origin_name"] = origin_name
        data["destination_name"] = destination_name
        if data.get("notices") and data.get("notices")[0].get("code") in [
            "noCoverage",
            "noStationsFound",
            "noRouteFound",
        ]:
            return data.get("notices") and data.get("notices")[0].get("title"), 500

    except requests.RequestException as e:
        return f"Error fetching from HERE: {e}", 500

    # 3) Decode & Transform the data into your 'trips' structure
    trips = convert_here_response_to_trips(data)

    sortedTripList = sorted(
        trips,
        key=lambda d: d["trip"]["utc_filtered_start_datetime"],
        reverse=True,
    )

    # 4) Pass trips into the template as JSON
    trips_json = json.dumps(sortedTripList)

    # 5) Checks if there's a user for colorblind information
    user_obj = None
    colorblind = False
    if "userinfo" in session and session["userinfo"]:
        try:
            user_obj = User.query.filter_by(username=username).first()
            colorblind = getattr(user_obj, "colorblind", False) if user_obj else False
        except Exception:
            colorblind = False

    return render_template(
        "here_routing.html",  # see below
        trips_json=trips_json,
        username=username,
        colorblind=colorblind,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route(
    "/u/<username>/googleRouteDisplay/<origin_name>/<destination_name>/<origin>/<destination>/<startDatetime>",
    methods=["GET"],
)
def google_route_display(
    username, origin, destination, startDatetime, origin_name, destination_name
):
    """
    Display transit routes using Google Directions 'v2:computeRoutes' API.
    """
    # 2) Prepare call to Google Directions API
    config = load_config()
    api_key = config["google"]["transitKey"]  #
    base_url = "https://routes.googleapis.com/directions/v2:computeRoutes"

    origin_lat, origin_lng = origin.split(",")
    dest_lat, dest_lng = destination.split(",")

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.legs.steps.transitDetails,routes.legs.steps.polyline",
    }

    # Construct the request payload
    data = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": float(origin_lat),
                    "longitude": float(origin_lng),
                }
            }
        },
        "destination": {
            "location": {
                "latLng": {"latitude": float(dest_lat), "longitude": float(dest_lng)}
            }
        },
        "travelMode": "TRANSIT",
        "computeAlternativeRoutes": False,
        "departureTime": f"{startDatetime}:00Z",  # e.g. "2025-01-20T05:50:00Z"
        "transitPreferences": {"routingPreference": "FEWER_TRANSFERS"},
    }

    try:
        r = requests.post(base_url, headers=headers, data=json.dumps(data))
        r.raise_for_status()
        google_data = r.json()
    except requests.RequestException as e:
        return f"Error fetching from Google: {e}", 500

    # Attach origin/destination names if needed for your templating or trip-conversion function
    google_data["origin_name"] = origin_name
    google_data["destination_name"] = destination_name

    # 3) Decode & Transform the data into your 'trips' structure
    trips = convert_google_response_to_trips(google_data)

    # Sort by your chosen key (here, reversed by departure time)
    sortedTripList = sorted(
        trips, key=lambda d: d["trip"]["utc_filtered_start_datetime"], reverse=True
    )

    # 4) Pass trips into the template as JSON
    trips_json = json.dumps(sortedTripList)

    # You can reuse "here_routing.html" or create a new template "google_routing.html"
    return render_template(
        "here_routing.html",
        trips_json=trips_json,
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )

@app.route("/forwardRouting/<routingType>/<path:path>")
def forwardRouting(path, routingType):
    return forward_routing_core(routingType=routingType, path=path, flask_request=request)


@app.route("/router_status/single")
def router_status_single():
    url = request.args.get("url")
    profile = request.args.get("profile", "driving")

    # Map profile to dummy query (avoid 'driving' for cycle/walk)
    if profile == "driving":
        dummy_profile = "driving"
    elif profile in {"cycling", "bike"}:
        dummy_profile = "bike"
    elif profile in {"foot", "walking", "walk"}:
        dummy_profile = "foot"
    elif profile == "ferry":
        dummy_profile = "driving"  # or whatever the OSRM instance uses
    elif profile == "train":
        dummy_profile = "driving"
    elif profile == "bus":
        dummy_profile = "driving"
    elif profile == "aerialway":
        dummy_profile = "driving"
    else:
        dummy_profile = "driving"

    # Most OSRM endpoints use /route/v1/<profile>/<coords>
    try:
        health_resp = requests.get(f"{url}/health", timeout=3)
        if health_resp.status_code == 200:
            return jsonify({"status": "OK", "message": "healthy"})
    except Exception:
        pass
    try:
        # Some endpoints might need https (if the url isn't already)
        dummy = requests.get(f"{url}/route/v1/{dummy_profile}/0,0.1;0,0.1", timeout=3)
        if dummy.status_code == 200:
            j = dummy.json()
            if "routes" in j:
                return jsonify({"status": "OK", "message": "responding"})
            else:
                return jsonify({"status": "DOWN", "message": "no routes key"})
        else:
            return jsonify({"status": "DOWN", "message": f"HTTP {dummy.status_code}"})
    except Exception as e:
        return jsonify({"status": "DOWN", "message": str(e)})


@app.route("/photon_status/<instance>")
def router_status_photon(instance):
    if instance not in photonInstances:
        return jsonify({"status": "ERROR", "message": "Unknown instance"}), 400
    try:
        data = photonRequestSingle(instance, "/status", params={}, timeout=3)
        # Extract and prettify import_date
        import_date = data.get("import_date")
        if import_date:
            # Photon format: "2025-05-18T06:35:14Z"
            try:
                dt = datetime.strptime(import_date, "%Y-%m-%dT%H:%M:%SZ")
                pretty = dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                pretty = import_date.replace("T", " ").replace("Z", " UTC")
        else:
            pretty = None
        # Include the human date as 'last_updated'
        return jsonify(
            {
                "status": data.get("status", "UNKNOWN"),
                "import_date": import_date,
                "last_updated": pretty,
            }
        )
    except Exception as e:
        return jsonify({"status": "DOWN", "message": str(e)}), 500


@app.route("/api/airportAutocomplete/<searchPattern>")
def airportAutocomplete(searchPattern):
    with pg_session() as pg:
        airports = [
            dict(airport._mapping)
            for airport in pg.execute(
                get_airports_query(), {"searchPattern": "%" + searchPattern + "%"}
            ).fetchall()
        ]
    return jsonify(airports)


@app.route("/trainStationAutocomplete")
def trainStationAutocomplete():
    searchPattern = request.args.get("q")
    params = {
        "searchPatternStart": searchPattern + "%",
        "searchPatternAnywhere": "%" + searchPattern + "%",
    }
    with pg_session() as pg:
        trainStations = [
            dict(trainStation._mapping)
            for trainStation in pg.execute(get_train_stations_query(), params).fetchall()
        ]
    return jsonify(trainStations)


@app.route("/placeAutocomplete")
def placeAutocomplete():
    nominatim_url = "https://nominatim.openstreetmap.org/search"
    args = request.query_string.decode("utf-8")  # e.g., q=Berlin&limit=5 ...
    headers = {"User-Agent": "Trainlog/1.0 (admin@trainlog.me)"}

    # Append format=jsonv2 & addressdetails=1 to get JSON + address details
    full_url = f"{nominatim_url}?{args}&format=jsonv2&addressdetails=1"
    data = requests.get(full_url, headers=headers).json()

    features = []
    # We'll track unique names to avoid duplicates
    seen_names = set()

    for item in data:
        lat = item.get("lat")
        lon = item.get("lon")
        if not lat or not lon:
            continue

        # Pull address details
        address = item.get("address", {})
        house_number = address.get("house_number", "")
        road = address.get("road", "")

        # For "city", also check "town", "village", "hamlet" if not present
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("hamlet")
        )

        # Only proceed if we have a city
        if city:
            country_code = address.get("country_code")
            if country_code:
                country_code = country_code.upper()

            # Build a short name: "123 Some Street, City"
            parts = []
            if house_number:
                parts.append(house_number)
            if road:
                parts.append(road)
            short_street = " ".join(parts).strip()

            # Final name: "123 Some Street, City" or just "City"
            name = f"{short_street}, {city}" if short_street else city

            # Ensure no duplicates based on the name
            if name in seen_names:
                continue  # skip this entry if the name is a duplicate

            # Mark this name as seen
            seen_names.add(name)

            feature = {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                "properties": {"name": name, "countrycode": country_code or ""},
            }
            features.append(feature)

    response_json = {"features": features}
    return jsonify(response_json)


@app.route("/stationAutocomplete")
def stationAutocomplete():
    # Check if this is a reverse geocoding request
    params = request.args.to_dict(flat=False)
    is_reverse = params.get("lat") and params.get("lon")
    endpoint = "/reverse" if is_reverse else "/api"

    params.setdefault("lang", ["en"])

    responseJson = photonRequest(endpoint, params=params, timeout=2)

    if responseJson is None:
        return "Photon Error", 500
    
    homonymy_filter = {}
    for index, result in enumerate(responseJson["features"]):
        props = result["properties"]
        # Special country handling
        special_countries = ["CN", "FI"]
        if props.get("countrycode") in special_countries:
            lon, lat = result["geometry"]["coordinates"]
            manual_country = getCountryFromCoordinates(lat, lon)
            props["countrycode"] = manual_country["countryCode"]
        country_code = props.get("countrycode", "unknown")
        # Add city name if not similar to name
        city = props.get("city")
        if city and stringSimmilarity(city.lower(), props["name"].lower()) < 50:
            district = props.get("district")
            locality = props.get("locality")
            if (
                (
                    district
                    and stringSimmilarity(district.lower(), props["name"].lower()) < 50
                )
                or (
                    locality
                    and stringSimmilarity(locality.lower(), props["name"].lower()) < 50
                )
                or (not district and not locality)
            ):
                props["name"] = f"{city} - {props['name']}"
        # Homonymy by name and country
        key = (props["name"], country_code)
        if key in homonymy_filter:
            homonymy_filter[key]["count"] += 1
            homonymy_filter[key]["states"].append(props.get("state"))
        else:
            homonymy_filter[key] = {"count": 1, "states": [props.get("state")]}
        responseJson["features"][index]["properties"] = props
    # Resolve homonyms
    for (name, country), details in homonymy_filter.items():
        if details["count"] > 1:
            unique_states = set(details["states"])
            if len(unique_states) == details["count"] and None not in unique_states:
                # Add state to name if states are unique
                for result in responseJson["features"]:
                    props = result["properties"]
                    if props["name"] == name and props.get("countrycode") == country:
                        props["name"] += f" ({props['state']})"
            else:
                # Use alphabetical order suffix
                suffix = ord("a")
                for result in responseJson["features"]:
                    props = result["properties"]
                    if props["name"] == name and props.get("countrycode") == country:
                        props["homonymy_order"] = f" ({chr(suffix)})"
                        suffix += 1
    return jsonify(responseJson)

@app.route("/u/<username>/getManAndOps/<station_type>", methods=["GET", "POST"])
@login_required
def getManAndOps(username, station_type):
    manualStations = {}
    visitedStations = {}
    with pg_session() as pg:
        for station in pg.execute(
            get_manual_stations_query(),
            {"station_type": station_type, "creator": username},
        ).fetchall():
            manualStations[station["name"]] = [
                [station["lat"], station["lng"]],
                station["name"],
            ]
    user_id = get_user_id(username)
    with pg_session() as pg:
        for station in pg.execute(
            get_number_stations_query(),
            {"trip_type": station_type, "user_id": user_id},
        ).fetchall():
            visitedStations[station["station"]] = station["total_occurrences"]
    tripType = station_type
    if tripType not in ["accommodation", "poi", "car"]:
        tripType = "operator"
    with pg_session() as pg:
        # Fetching operators from the database
        operators_from_db = [
            str(operator["operator"]).strip()
            for operator in pg.execute(
                get_operators_query(), {"user_id": user_id}
            ).fetchall()
        ]
    with pg_session() as pg:
        # Fetching material types for station_type from the database
        material_types_from_db = [
            str(material_type["material_type"]).strip()
            for material_type in pg.execute(
                get_material_types_query(),
                {"trip_type": station_type, "user_id": user_id},
            ).fetchall()
        ]

    # Getting the list of operators from the logos function
    operators_logos = listOperatorsLogos(tripType)

    # Combining and removing duplicates
    all_operators = list(
        dict.fromkeys(operators_from_db + list(operators_logos.keys()))
    )
    all_operators = [op for op in all_operators if op and op.strip()]

    # Creating the result dictionary with the logos or null values
    result = {
        operator: operators_logos.get(operator, None) for operator in all_operators
    }

    material_types = {material_type: None for material_type in material_types_from_db if material_type}

    hints, canonical = operator_autocomplete_meta(tripType)
    manAndOps = {
        "operators": result,
        "operatorHints": hints,
        # Every alias -> its operator's short_name, so the autocomplete can collapse
        # an operator's spellings to a single suggestion (see operatorPillsInput).
        "operatorCanonical": canonical,
        "manualStations": manualStations,
        "materialTypes": material_types,
        "visitedStations": visitedStations,
    }
    return jsonify(manAndOps)


def operator_autocomplete_meta(operator_type):
    """Per-spelling metadata for the operator autocomplete, in one pass over aliases.

    Returns (hints, canonical):

    - hints: {alias -> muted subtitle}. A suggestion on its own does not say what it
      maps to ("CFF" gives no clue it is the Chemins de fer fédéraux suisses); the hint
      is whichever of the operator's names the spelling is *not*. Only entries that add
      something are included, so for the majority where long_name == short_name the map
      stays small.
    - canonical: {alias -> short_name}, every spelling included. Lets the autocomplete
      show one suggestion per operator (its short_name) even when several of its
      spellings match the term, instead of listing ETHIAD, "Ethiad airways", etc.
      separately.
    """
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT a.alias, o.short_name, o.long_name
            FROM operator_aliases a
            JOIN operators o ON o.operator_id = a.operator_id
            WHERE o.operator_type = :operator_type
            """,
            {"operator_type": operator_type},
        ).fetchall()

    hints = {}
    canonical = {}
    for alias, short_name, long_name in rows:
        canonical[alias] = short_name
        has_long = long_name and long_name != short_name
        if alias == short_name:
            # Canonical spelling: the long name is the only thing left to add.
            hint = long_name if has_long else None
        elif alias == long_name:
            hint = short_name
        else:
            # An alias: say which operator it resolves to, with its long name when
            # that adds anything.
            hint = f"{short_name} — {long_name}" if has_long else short_name
        if hint:
            hints[alias] = hint
    return hints, canonical


# ── /admin user table ──────────────────────────────────────────────────────
# The table is server-side processed, so *every* page/sort/search re-enters
# getAdminUsersData. Assembling the snapshot means loading every User row plus a
# full trips aggregate, which is far too slow to redo per keystroke — so the
# assembled rows are cached for a short TTL and invalidated explicitly by the
# mutations reachable from that page (role toggle, rename, delete).
_ADMIN_USERS_CACHE_TTL = 60  # seconds
_admin_users_cache = {"built_at": None, "rows": None}

ADMIN_PREMIUM_TIER_LABELS = {
    "trainlogger": "Trainlogger",
    "first_class": "1st Class Logger",
    "rail_baron": "Rail Baron",
}

ADMIN_SHARE_LABELS = {0: "Private", 1: "Link shared", 2: "Public"}

# The Premium column sorts by what the membership is worth, not by the label's
# spelling: none < manual grant < the BMC tiers in ascending price order.
ADMIN_PREMIUM_RANKS = {
    None: 0,
    "manual": 1,
    "trainlogger": 2,
    "first_class": 3,
    "rail_baron": 4,
}

# Language names are rendered client-side (getLangTooltip), so the server needs
# its own copy to make "german", "swedish"… searchable alongside the raw code.
ADMIN_LANG_NAMES = {
    "en": "English", "zh": "Chinese", "nl": "Dutch", "de": "German",
    "fr": "French", "fi": "Finnish", "es": "Spanish", "it": "Italian",
    "no": "Norwegian", "sv": "Swedish", "cs": "Czech", "pl": "Polish",
    "tr": "Turkish", "hu": "Hungarian", "da": "Danish", "hr": "Croatian",
    "et": "Estonian", "ja": "Japanese", "ru": "Russian", "uk": "Ukrainian",
    "sv-FI": "Finland Swedish", "gsw": "Swiss German", "ko": "Korean",
    "pt-BR": "Brazilian Portuguese", "pt-PT": "Portuguese",
}


def invalidate_admin_users_cache():
    """Drop the cached /admin snapshot so the next request rebuilds it."""
    _admin_users_cache["built_at"] = None
    _admin_users_cache["rows"] = None


def _admin_search_haystack(user):
    """Every piece of info the admin table shows for this user, as one lowercase
    blob. Search terms are matched against it at word boundaries (see
    _filter_admin_users), so "act" finds "active" but "active" doesn't find
    "inactive". Never add a token that *contains* another meaningful term
    (e.g. "nopremium"), or negation and plain search both misfire."""
    parts = [
        user["username"],
        user.get("email") or "",
        user.get("lang") or "",
        ADMIN_LANG_NAMES.get(user.get("lang"), ""),
        str(user["uid"]),
        "active" if user["active"] else "inactive",
        ADMIN_SHARE_LABELS.get(user.get("share_level"), "unknown"),
        str(user["trips"]),
        str(round((user["length"] or 0) / 1000)),
    ]

    if user.get("discord_id"):
        parts += ["discord", user.get("discord_username") or "", str(user["discord_id"])]
    for role, label in (
        ("admin", "admin"),
        ("alpha", "alpha"),
        ("translator", "translator"),
        ("feature_admin", "feature_admin featureadmin"),
        ("leaderboard", "leaderboard"),
    ):
        if user.get(role):
            parts.append(label)

    if user.get("premium"):
        parts.append("premium")
        if user.get("bmc_supporter_id"):
            parts += [
                "automated bmc supporter",
                str(user["bmc_supporter_id"]),
                user.get("premium_tier") or "",
                ADMIN_PREMIUM_TIER_LABELS.get(user.get("premium_tier"), ""),
            ]
        else:
            parts.append("manual")
    if user.get("premium_cancel_at"):
        parts.append("cancelled canceled cancels cancellation")
        parts.append(user["premium_cancel_at"][:10])
    if user.get("premium_stale"):
        parts.append("stale expired overdue")

    # Dates are still datetimes here (the row is serialised after this runs), so
    # "2026-07" matches a last login or a signup month.
    for key in ("last_login", "creation_date"):
        value = user.get(key)
        if value:
            parts.append(
                value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else str(value)[:10]
            )

    return " ".join(p for p in parts if p).lower()


def _build_admin_user_rows():
    """One full snapshot of the /admin table: every user enriched with their trip
    totals, activity flag and search blob. Everything the endpoints need is
    derived here so the per-request path is filter + sort + slice only."""
    now = datetime.now()
    active_cutoff = now - timedelta(days=90)
    # premium_cancel_at comes back naive from SQLite (tzinfo isn't preserved on
    # the round trip) even though it was written as a UTC value — compare
    # against a naive-but-UTC "now" to match, or this raises TypeError.
    utc_now_naive = datetime.now(UTC).replace(tzinfo=None)

    rows = []
    by_uid = {}
    for user in User.query.all():
        row = user.toDict()
        # Owner-only view: not exposed via toDict() elsewhere. premium_tier +
        # bmc_supporter_id together tell manual (/toggle_role) grants apart from
        # BMC-automated ones — a manual grant never gets a supporter_id pinned.
        row["premium_tier"] = user.premium_tier
        row["bmc_supporter_id"] = user.bmc_supporter_id
        row["premium_cancel_at"] = (
            user.premium_cancel_at.isoformat() if user.premium_cancel_at else None
        )
        row["premium_stale"] = bool(
            user.premium_cancel_at and user.premium_cancel_at <= utc_now_naive
        )
        row["trips"] = 0
        row["length"] = 0
        rows.append(row)
        # trips.user_id is User.uid — resolving it per row via get_username()
        # was one SQLite query per user with trips.
        by_uid[user.uid] = row

    with pg_session() as pg:
        trip_rows = pg.execute(
            "SELECT user_id, count(*) AS trips, sum(trip_length) AS length,"
            " max(last_modified) AS last_modified FROM trips GROUP BY user_id"
        ).fetchall()
    for trip_row in trip_rows:
        row = by_uid.get(trip_row["user_id"])
        if row is None:
            continue
        row["trips"] = trip_row["trips"]
        row["length"] = trip_row["length"] or 0
        if row["last_login"] is None:
            row["last_login"] = trip_row["last_modified"]

    for row in rows:
        row["active"] = bool(
            row["trips"] > 0
            and row["last_login"]
            and row["last_login"] > active_cutoff
            and row["username"] not in ("demo", "test")
        )
        row["active_today"] = bool(
            row["last_login"] and row["last_login"] > now - timedelta(days=1)
        )
        days_since_creation = (now - row["creation_date"]).days or 1
        row["trips_per_day"] = round(row["trips"] / days_since_creation, 2)
        # Sortable stand-ins for the two rendered-only columns, so ordering by
        # them means something (Premium sorts manual/tier, Status by activity).
        row["premium_label"] = (
            ""
            if not row["premium"]
            else (
                "Manual"
                if not row["bmc_supporter_id"]
                else ADMIN_PREMIUM_TIER_LABELS.get(
                    row["premium_tier"], row["premium_tier"] or "unknown tier"
                )
            )
        )
        # An unknown/new tier sorts above the ones we know, so it can't hide at
        # the bottom of the list unnoticed.
        row["premium_rank"] = (
            0
            if not row["premium"]
            else ADMIN_PREMIUM_RANKS.get(
                "manual" if not row["bmc_supporter_id"] else row["premium_tier"],
                len(ADMIN_PREMIUM_RANKS),
            )
        )
        row["share_label"] = ADMIN_SHARE_LABELS.get(row["share_level"], "Unknown")
        row["_search"] = _admin_search_haystack(row)
        # Serialised last, so everything above works with real datetimes.
        if row["last_login"]:
            row["last_login"] = row["last_login"].isoformat()
        if row["creation_date"]:
            row["creation_date"] = row["creation_date"].isoformat()

    return rows


def get_admin_user_rows():
    cached = _admin_users_cache
    if (
        cached["rows"] is not None
        and cached["built_at"] is not None
        and (datetime.now() - cached["built_at"]).total_seconds() < _ADMIN_USERS_CACHE_TTL
    ):
        return cached["rows"]
    rows = _build_admin_user_rows()
    cached["rows"] = rows
    cached["built_at"] = datetime.now()
    return rows


def _filter_admin_users(rows, search_value):
    """Space-separated terms, ANDed; a leading "-" negates a term. Terms match at
    word boundaries against the row's search blob, so they behave as prefixes
    ("prem" → premium) without bleeding across words ("active" ≠ "inactive")."""
    terms = [t for t in search_value.lower().split() if t and t != "-"]
    if not terms:
        return rows

    positives, negatives = [], []
    for term in terms:
        target = negatives if term.startswith("-") else positives
        target.append(re.compile(r"\b" + re.escape(term.lstrip("-"))))

    return [
        row
        for row in rows
        if all(p.search(row["_search"]) for p in positives)
        and not any(n.search(row["_search"]) for n in negatives)
    ]


# Columns the table is allowed to order by, keyed on the DataTables `data` name
# the client sends for the ordered column (so adding/moving a column can't
# silently sort by the wrong field, as a positional map did).
ADMIN_SORT_FIELDS = {
    "username": ("username", ""),
    "email": ("email", ""),
    "lang": ("lang", ""),
    "active": ("active", False),
    "share_label": ("share_label", ""),
    "trips": ("trips", 0),
    "length": ("length", 0),
    "trips_per_day": ("trips_per_day", 0),
    "last_login": ("last_login", ""),
    "creation_date": ("creation_date", ""),
    # The Premium column is sent as `premium_label` by the client but ordered on
    # the tier rank behind it (see ADMIN_PREMIUM_RANKS).
    "premium_label": ("premium_rank", 0),
}


@app.route("/getAdminUsersData", methods=["POST"])
@owner_required
def getAdminUsersData():
    """
    Server-side processing endpoint for DataTables.
    Returns paginated user data based on DataTables parameters.
    """
    draw = int(request.form.get("draw", 1))
    start = int(request.form.get("start", 0))
    length = int(request.form.get("length", 10))
    search_value = request.form.get("search[value]", "")
    show_inactive = request.form.get("showInactive", "false") == "true"

    order_column = request.form.get("order[0][column]")
    order_dir = request.form.get("order[0][dir]", "asc")
    sort_field = (
        request.form.get(f"columns[{order_column}][data]", "")
        if order_column is not None
        else ""
    )

    all_rows = get_admin_user_rows()
    users_list = (
        all_rows if show_inactive else [row for row in all_rows if row["active"]]
    )
    records_total = len(users_list)

    users_list = _filter_admin_users(users_list, search_value)

    if sort_field in ADMIN_SORT_FIELDS:
        key, default = ADMIN_SORT_FIELDS[sort_field]
        users_list = sorted(
            users_list,
            key=lambda row: (
                (row.get(key) or default).lower()
                if isinstance(default, str)
                else (row.get(key) if row.get(key) is not None else default)
            ),
            reverse=order_dir == "desc",
        )

    total_filtered = len(users_list)

    if length != -1:  # -1 means show all
        users_list = users_list[start : start + length]

    return jsonify(
        {
            "draw": draw,
            "recordsTotal": records_total,
            "recordsFiltered": total_filtered,
            # The search blob is a server-side index, not display data.
            "data": [
                {k: v for k, v in row.items() if k != "_search"} for row in users_list
            ],
        }
    )


@app.route("/getAdminStats", methods=["GET"])
@owner_required
def getAdminStats():
    """
    Returns aggregated statistics without user data.
    This is called once on page load to populate the summary stats.
    """
    rows = get_admin_user_rows()

    active_users = 0
    active_today = 0
    total_trips = 0
    total_km = 0
    total_langs = {}
    active_langs = {}

    for user in rows:
        total_langs[user["lang"]] = total_langs.get(user["lang"], 0) + 1
        if user["active"]:
            active_users += 1
            active_langs[user["lang"]] = active_langs.get(user["lang"], 0) + 1
        if user["trips"] and user["active_today"]:
            active_today += 1
        total_trips += user["trips"]
        total_km += user["length"]

    return jsonify(
        {
            "stats": {
                "total_users": len(rows),
                "active_users": active_users,
                "active_today": active_today,
                "total_trips": total_trips,
                "total_km": total_km,
                "langs": {"total": total_langs, "active": active_langs},
            }
        }
    )


@app.route("/getLeaderboardUsers/<type>", methods=["GET"])
def getLeaderboardUsers(type):
    if type in ("train_countries", "world_squares"):
        leaderboard_users = User.query.filter_by(leaderboard=True).all()
        user_list = [user.username for user in leaderboard_users]
        non_public_users = [
            username
            for username in user_list
            if not User.query.filter_by(username=username).first().is_public()
        ]
       
        countries_dict = {}
        with pg_session() as pg:
            for item in pg.execute(
                get_leaderboard_countries_query(
                    equals="=" if type == "world_squares" else "!=",
                ),
                {"usernames": user_list},
            ).fetchall():
                if item["cc"] not in countries_dict:
                    countries_dict[item["cc"]] = {}
                if item["percent"] not in countries_dict[item["cc"]]:
                    countries_dict[item["cc"]][item["percent"]] = []
                countries_dict[item["cc"]][item["percent"]].append(item["username"])
       
        leaderboard_data = []
        for country, percentages in countries_dict.items():
            users_percents = []
            for percent, users in percentages.items():
                users_percents.append({"percent": percent, "usernames": users})
            leaderboard_data.append({"cc": country, "data": users_percents})
        return jsonify(
            {"leaderboard_data": leaderboard_data, "non_public_users": non_public_users}
        )
   
    # For all other types, use the helper function with PostgreSQL
    result = _getLeaderboardUsers(type, User)
    return jsonify(result)

@app.route("/deleteUser/<int:uid>", methods=["POST"])
@owner_required
def delete_user(uid):
    """
    Deletes a user based on their unique user ID (uid).
    """
    user = User.query.get(uid)
    if not user:
        return ""

    user_id = get_user_id(user.username)
    try:
        with pg_session() as pg:
            idList = [
                row["uid"]
                for row in pg.execute(
                    "SELECT trip_id AS uid FROM trips WHERE user_id = :user_id",
                    {"user_id": user_id},
                ).fetchall()
            ]

        with pg_session() as pg:
            if idList:
                pg.execute(
                    "DELETE FROM paths WHERE trip_id = ANY(:ids)",
                    {"ids": [int(i) for i in idList]},
                )
            pg.execute(delete_user_trips_query(), {"user_id": user_id})
            pg.execute(
                "DELETE FROM tag_members WHERE username = :username",
                {"username": user.username},
            )
        authDb.session.delete(user)
        authDb.session.commit()
        invalidate_admin_users_cache()
    except Exception as e:
        print(e)

    return ""


@app.route("/admin/rename_user/<int:uid>", methods=["POST"])
@owner_required
def rename_user(uid):
    data = request.get_json()
    new_username = (data.get("new_username") or "").strip()

    if not new_username:
        return jsonify(success=False, error="Username cannot be empty"), 400

    user = User.query.get(uid)
    if not user:
        return jsonify(success=False, error="User not found"), 404

    if user.username == new_username:
        return jsonify(success=False, error="New username is the same as current"), 400

    if User.query.filter_by(username=new_username).first():
        return jsonify(success=False, error="Username already taken"), 409

    old_username = user.username
    # All username-keyed tables now live in PostgreSQL.
    pg_tables = [
        "percents",
        "fr24_usage",
        "ai_usage",
        "trainsets",
        "tickets",
        "tags",
        "tag_members",
        "gpx",
    ]

    try:
        user.username = new_username
        authDb.session.commit()

        with pg_session() as pg:
            for table in pg_tables:
                pg.execute(
                    f"UPDATE {table} SET username = :new WHERE username = :old",
                    {"new": new_username, "old": old_username},
                )
    except Exception as e:
        print(e)
        return jsonify(success=False, error=str(e)), 500

    invalidate_admin_users_cache()
    return jsonify(success=True)


def fetchTripsPaths(username, lastLocal, public):
    tripList = []
    now = datetime.now()

    user_id = get_user_id(username)
    with pg_session() as pg:
        idList = [
            row["uid"]
            for row in pg.execute(
                "SELECT trip_id AS uid FROM trips WHERE user_id = :user_id",
                {"user_id": user_id},
            ).fetchall()
        ]

        trips = pg.execute(
            get_unique_user_trips_query(),
            {
                "user_id": user_id,
                "lastLocal": lastLocal,
                "public": public,
                "friend": int(current_user_is_friend_with(username)),
            },
        ).fetchall()

    trips.reverse()

    for trip in trips:
        # The path geometry comes back on the same row (geojson column) now that
        # paths share the PG DB, so no second getUserLines round-trip is needed.
        path = geom_geojson_to_coords(trip._mapping.get("geojson"))
        # adapt_pg_trip_row applies legacy names (trip_id->uid, trip_type->type)
        # and the 1/-1 date sentinels the map frontend relies on.
        trip = adapt_pg_trip_row(trip._mapping, username)
        trip.pop("geojson", None)
        trip.pop("past")
        trip.pop("plannedFuture")
        trip.pop("current")
        trip.pop("future")

        tripList.append({"trip": trip, "path": path})

    print(datetime.now() - now)
    lastLocal = datetime.strftime(datetime.now(), "%Y-%m-%dT%H:%M:%S.%f")
    return {"trips": tripList, "lastLocal": lastLocal, "idList": idList}


# Register visualisation blueprint here — after fetchTripsPaths is defined.
viz_module.register(app, fetchTripsPaths)


@app.route("/public/<username>/getTripsPaths/<lastLocal>", methods=["GET", "POST"])
@public_required  # Public access check
def public_getTripsPaths(username, lastLocal):
    result = fetchTripsPaths(username, lastLocal, public=1)
    return jsonify(result)


@app.route("/u/<username>/getTripsPaths/<lastLocal>", methods=["GET", "POST"])
@login_required  # Login access check
def get_trip_paths(username, lastLocal):
    result = fetchTripsPaths(username, lastLocal, public=0)
    return jsonify(result)


def fetchUpdatedTrips(username, lastLocal, public):
    tripList = []

    user_id = get_user_id(username)
    with pg_session() as pg:
        idList = [
            row["uid"]
            for row in pg.execute(
                "SELECT trip_id AS uid FROM trips WHERE user_id = :user_id",
                {"user_id": user_id},
            ).fetchall()
        ]

        trips = pg.execute(
            get_updated_user_trips_query(),
            {
                "user_id": user_id,
                "lastLocal": lastLocal,
                "public": public,
                "friend": int(current_user_is_friend_with(username)),
            },
        ).fetchall()

    for trip in trips:
        path = geom_geojson_to_coords(trip._mapping.get("geojson"))
        trip = adapt_pg_trip_row(trip._mapping, username)
        trip.pop("geojson", None)
        trip.pop("planned_future", None)
        tripList.append({"trip": trip, "path": path})

    lastLocal = datetime.strftime(datetime.now(), "%Y-%m-%dT%H:%M:%S.%f")
    return {"trips": tripList, "lastLocal": lastLocal, "idList": idList}


@app.route("/u/<username>/getUpdatedTrips/<lastLocal>", methods=["GET", "POST"])
@login_required
def get_updated_trips(username, lastLocal):
    result = fetchUpdatedTrips(username, lastLocal, public=0)
    return jsonify(result)


@app.route("/u/<username>/getCurrentTrip", methods=["GET", "POST"])
@login_required
def get_current_trip_path(username):
    trip_id = get_current_trip_id()
    if trip_id is None:
        return jsonify([])

    trip_ids = [trip_id]

    trip_list = []

    with pg_session() as pg:
        pathResult = pg.execute(
            get_user_lines_query(), {"ids": [int(i) for i in trip_ids]}
        ).fetchall()
    paths = {}
    for path in pathResult:
        paths[path["trip_id"]] = path["path"]

    for tripId in trip_ids:
        trip = formatTrip(get_trip_pg(tripId))
        user = User.query.filter_by(username=trip["username"]).first()
        if not session.get(user.username) and not user.is_public():
            abort(401)
        trip_list.append(
            {
                "time": trip["time"],
                "trip": dict(trip),
                "path": json.loads(paths[trip["uid"]]),
                "distances": getDistanceFromPath(json.loads(paths[trip["uid"]])),
            }
        )
    sorted_trip_list = sorted(trip_list, key=lambda d: d["trip"]["uid"], reverse=True)
    sorted_trip_list = sorted(
        sorted_trip_list, key=lambda d: d["trip"]["start_datetime"], reverse=True
    )
    return jsonify(sorted_trip_list)


def processPublicTrips(tripIds):
    user_currency = getLoggedUserCurrency()

    # The ids come straight from the client, so screen them with the same
    # per-trip rules as render_public_trip_page — the owner sees everything,
    # `private` trips are dropped, `friends` trips require friendship — in one
    # set-based query instead of a get_trip_pg round-trip per id. Unknown ids
    # simply drop out here.
    with pg_session() as pg:
        vis_rows = pg.execute(
            "SELECT trip_id, user_id, visibility FROM trips WHERE trip_id = ANY(:ids)",
            {"ids": [int(t) for t in tripIds.split(",")]},
        ).fetchall()

    usernames = {
        user_id: get_username(user_id)
        for user_id in {row["user_id"] for row in vis_rows}
    }
    users = {
        username: User.query.filter_by(username=username).first()
        for username in usernames.values()
    }
    friend_cache = {}
    allowed_ids = []
    allowed_owners = set()
    for row in vis_rows:
        username = usernames[row["user_id"]]
        if not session.get(username):
            if row["visibility"] == "private":
                continue
            if row["visibility"] == "friends":
                if username not in friend_cache:
                    friend_cache[username] = current_user_is_friend_with(username)
                if not friend_cache[username]:
                    continue
        allowed_ids.append(row["trip_id"])
        allowed_owners.add(username)

    if not allowed_ids:
        abort(401)

    # User-level sharing setting, once per owner instead of once per trip.
    for username in allowed_owners:
        user = users[username]
        if (
            not session.get(user.username)
            and not user.is_public_trips()
            and not session.get(owner)
        ):
            abort(401)

    tripIds = [str(trip_id) for trip_id in allowed_ids]

    # Fetch stored carbon values from PG in one query
    with pg_session() as pg:
        pg_rows = pg.execute(
            "SELECT trip_id, carbon FROM trips WHERE trip_id = ANY(:ids)",
            {"ids": [int(t) for t in tripIds]},
        ).fetchall()
    pg_carbon = {row["trip_id"]: row["carbon"] for row in pg_rows}

    tripList = []

    with pg_session() as pg:
        pathResult = pg.execute(
            get_user_lines_query(), {"ids": [int(i) for i in tripIds]}
        ).fetchall()
        # 3D flight track (altitude/timestamps), fetched separately so the
        # heavily-shared get_user_lines query keeps its exact contract.
        trackResult = pg.execute(
            "SELECT trip_id, altitude, timestamps FROM paths WHERE trip_id = ANY(:ids)",
            {"ids": [int(i) for i in tripIds]},
        ).fetchall()
    paths = {}
    for path in pathResult:
        paths[path["trip_id"]] = path["path"]
    altitudes = {row["trip_id"]: row["altitude"] for row in trackResult}
    timestamps = {row["trip_id"]: row["timestamps"] for row in trackResult}

    total_price = 0
    total_carbon = 0
    total_distance = 0

    trips_by_id = get_trips_pg(tripIds)
    for tripId in tripIds:
        trip = formatTrip(trips_by_id[int(tripId)])

        # Process multi operator logos. One query for the whole trip, resolved through
        # operator_aliases, instead of two per operator matched on exact short_name.
        if "," in str(trip["operator"]):
            operator_logos = get_trip_operator_logos(
                tripId, trip["utc_filtered_start_datetime"]
            )

            # An empty list would read as "logos present" downstream and render
            # neither logo nor operator text, so only take over when there is
            # something to show. Entries without a logo are kept — the templates
            # fall back to the operator's name.
            if operator_logos:
                trip["multi_operators"] = operator_logos

                # Remove operator_name and logo_url from trip if they exist
                trip.pop("operator_name", None)
                trip.pop("logo_url", None)

        # Process pricing
        if trip["ticket_id"] not in (None, ""):
            ticket = get_ticket_cached(trip["ticket_id"])
            trip["ticket"] = ticket["name"]
            trip["ticket_price"] = ticket["price"] / ticket["trip_count"]
            trip["ticket_currency"] = ticket["currency"]
            trip["ticket_price_in_user_currency"] = get_exchange_rate(
                price=trip["ticket_price"],
                base_currency=trip["ticket_currency"],
                target_currency=user_currency,
                date=ticket["purchasing_date"],
            )
            if trip["ticket_price_in_user_currency"] is not None:
                total_price += trip["ticket_price_in_user_currency"]

        if trip["price"] not in (None, ""):
            trip["price_in_user_currency"] = get_exchange_rate(
                price=trip["price"],
                base_currency=trip["currency"],
                target_currency=user_currency,
                date=trip["purchasing_date"],
            )
            trip["user_currency"] = user_currency
            if trip["price_in_user_currency"] is not None:
                total_price += trip["price_in_user_currency"]

        # Use stored carbon from PG if available, otherwise recalculate (legacy trips)
        stored_carbon = pg_carbon.get(trip["uid"])
        path_data = json.loads(paths[trip["uid"]]) if trip["uid"] in paths else []
        if stored_carbon is not None:
            trip_carbon = float(stored_carbon)
        else:
            trip_carbon = calculate_carbon_footprint_for_trip(trip, path_data)
        trip["carbon_footprint"] = round(trip_carbon, 6)
        
        # Add to totals
        total_carbon += trip_carbon
        if trip.get('trip_length', 0) > 0:
            total_distance += trip['trip_length'] / 1000  # Convert to km

        user = users[trip["username"]]
        # 3D altitude track is a premium feature, opt-in per owner. Gate it on the
        # owner's CURRENT premium status so revoking premium hides it immediately.
        # Altitude is stored for any type (e.g. GPX with <ele>), but only flights
        # render it for now.
        is_flight = trip.get("type") in ("air", "helicopter")
        show_3d = bool(
            is_flight
            and getattr(user, "premium", False)
            and getattr(user, "flight_3d", False)
        )
        if trip.get("material_type_advanced"):
            with pg_session() as pg:
                trip["trainset"] = public_trainset_info(
                    pg, trip["material_type_advanced"], trip["username"]
                )
        tripList.append(
            {
                "time": trip["time"],
                "trip": dict(trip),
                "path": path_data,
                "altitude": altitudes.get(trip["uid"]) if show_3d else None,
                "timestamps": timestamps.get(trip["uid"]) if show_3d else None,
            }
        )
    
    def _pub_trip_sort_key(d):
        # Dated trips store utc_filtered_start_datetime as a "YYYY-MM-DD HH:MM:SS"
        # string; non-dated trips use the sentinel ints -1 (past) and 1 (future).
        # Return a (group, value) tuple so str and int are never compared directly
        # (which would raise TypeError when mixing dated and non-dated trips).
        dt = d["trip"]["utc_filtered_start_datetime"]
        if isinstance(dt, str):
            return (1, dt)
        return (0 if dt == -1 else 2, "")

    sortedTripList = sorted(tripList, key=lambda d: d["trip"]["uid"], reverse=True)
    sortedTripList = sorted(sortedTripList, key=_pub_trip_sort_key, reverse=True)
    
    priceDict = {
        "total_price": total_price, 
        "user_currency": user_currency,
        "total_carbon": round(total_carbon, 6),
        "total_distance": round(total_distance, 2)
    }
    
    return sortedTripList, priceDict


@app.route("/getPublicTrips", methods=["POST"])
def getPublicTrips():
    data = request.get_json()
    tripIds = data.get("tripIds")
    sortedTripList, priceDict = processPublicTrips(tripIds)
    for trip in sortedTripList:
        trip["trip"].pop("username")
    return jsonify([sortedTripList, priceDict])


@app.route("/u/<username>/toType/<tripType>/<tripIds>", methods=["GET"])
@login_required
def changeTripType(username, tripType, tripIds):
    # make sure the user owns all the listed trips
    trip_ids = [int(id) for id in tripIds.split(",")]
    for trip in trip_ids:
        check_current_user_owns_trip(trip)

    new_type = TripTypes.from_str(tripType)

    # Fetch trips and verify permissions
    trips, _ = processPublicTrips(tripIds)
    if not trips:
        return jsonify({"error": "No trips found to update."}), 400

    # Check if all trips can be changed to the requested type
    for trip in trips:
        current_type = TripTypes.from_str(trip["trip"].get("type", ""))
        if not TripTypes.can_transform(current_type, new_type):
            return jsonify(
                {
                    "error": f"Cannot change trip type from '{current_type}' to '{tripType}'."
                }
            ), 400

    try:
        # Update each trip's type
        for trip in trips:
            update_trip_type(trip["trip"]["uid"], new_type)

        return jsonify(
            {"message": "Trip types updated successfully", "updated_type": tripType}
        )
    except Exception as e:
        logger.exception(e)
        return jsonify({"error": str(e)}), 500


@app.route("/u/<username>/bulkChangeType", methods=["POST"])
@login_required
def bulkChangeType(username):
    data = request.get_json()
    if not data or "trip_ids" not in data or "new_type" not in data:
        return jsonify({"error": "Missing parameters"}), 400
    try:
        new_type = TripTypes.from_str(data["new_type"])
    except ValueError:
        return jsonify({"error": "Invalid type"}), 400

    trip_ids = data["trip_ids"]
    # Validate all trips are in the same transformable group
    with pg_session() as pg:
        current_types = [
            TripTypes.from_str(r["type"])
            for r in pg.execute(
                "SELECT DISTINCT trip_type AS type FROM trips WHERE user_id = :user_id AND trip_id = ANY(:ids)",
                {"user_id": get_user_id(username), "ids": [int(t) for t in trip_ids]},
            ).fetchall()
        ]
    for ct in current_types:
        if not TripTypes.can_transform(ct, new_type):
            return jsonify({"error": f"Cannot change from '{ct}' to '{new_type}'"}), 400

    success, error = bulk_change_type(username, trip_ids, new_type)
    if success:
        return jsonify({"success": 1}), 200
    return jsonify({"error": error}), 500


@app.route("/u/<username>/bulkSetPowerType", methods=["POST"])
@login_required
def bulkSetPowerType(username):
    data = request.get_json()
    if not data or "trip_ids" not in data or "power_type" not in data:
        return jsonify({"error": "Missing parameters"}), 400
    power_type = data["power_type"]
    if power_type not in ("electric", "thermic", "manual", "auto"):
        return jsonify({"error": "Invalid power_type"}), 400
    success, error = bulk_set_power_type(username, data["trip_ids"], power_type)
    if success:
        return jsonify({"success": 1}), 200
    return jsonify({"error": error}), 500


@app.route("/u/<username>/merge/<tripIds>", methods=["GET", "POST"])
@login_required
def mergeTrips(username, tripIds):
    # Process and sort the trips (includes permission checks)
    sortedTripList, priceDict = processPublicTrips(tripIds)
    sortedTripList.reverse()

    if not sortedTripList:
        return jsonify({"error": "No trips found to merge."}), 400

    # Merge all paths together
    merged_path = []
    for idx, trip_item in enumerate(sortedTripList):
        print(trip_item["trip"]["origin_station"])
        trip_path = trip_item["path"]
        if idx == 0:
            merged_path = trip_path.copy()
        else:
            # If the last point of the merged path equals the first point of the new path,
            # skip the duplicate so that paths join nicely.
            if merged_path and trip_path and merged_path[-1] == trip_path[0]:
                merged_path.extend(trip_path[1:])
            else:
                merged_path.extend(trip_path)

    # Transform merged_path into a list of dicts with keys "lat" and "lng"
    final_path = []
    for point in merged_path:
        # If point is already a dict with the expected keys, use it directly.
        if isinstance(point, dict) and "lat" in point and "lng" in point:
            final_path.append({"lat": float(point["lat"]), "lng": float(point["lng"])})
        # Otherwise assume point is a list/tuple [lat, lng]
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            final_path.append({"lat": float(point[0]), "lng": float(point[1])})
        else:
            # Skip or handle unexpected point format if needed.
            continue

    # Create the new merged trip details.
    first_trip = sortedTripList[0]["trip"]
    last_trip = sortedTripList[-1]["trip"]

    newTrip = {}
    # Use the origin from the first trip and the destination from the last trip.
    newTrip["originStation"] = [None, first_trip.get("origin_station", "")]
    newTrip["destinationStation"] = [None, last_trip.get("destination_station", "")]

    if first_trip.get("start_datetime") not in (-1, 1):
        newTrip["newTripStart"] = first_trip.get("start_datetime").replace(" ", "T")[
            :-3
        ]
        newTrip["newTripEnd"] = last_trip.get("end_datetime").replace(" ", "T")[:-3]
        newTrip["precision"] = "preciseDates"
        newTrip["unknownType"] = ""
    elif first_trip.get("start_datetime") == -1:
        newTrip["precision"] = "unknown"
        newTrip["unknownType"] = "past"
    else:
        newTrip["precision"] = "unknown"
        newTrip["unknownType"] = "future"

    tripType = newTrip["type"] = first_trip.get("type", "train")

    # Combine operators from each trip (distinct values, comma separated)
    operators = [
        trip_item["trip"].get("operator", "")
        for trip_item in sortedTripList
        if trip_item["trip"].get("operator")
    ]
    newTrip["operator"] = ", ".join(sorted(set(operators))) if operators else ""

    # Combine line names from the trips
    line_names = [
        trip_item["trip"].get("line_name", "")
        for trip_item in sortedTripList
        if trip_item["trip"].get("line_name")
    ]
    newTrip["lineName"] = ", ".join(sorted(set(line_names))) if line_names else ""

    # Sum up trip lengths and estimated durations
    newTrip["trip_length"] = sum(
        float(trip_item["trip"].get("trip_length", 0)) for trip_item in sortedTripList
    )
    newTrip["estimated_trip_duration"] = sum(
        float(trip_item["trip"].get("estimated_trip_duration", 0))
        for trip_item in sortedTripList
    )

    #get new visibility
    visibility = "public"
    for item in sortedTripList:
        if item["trip"]["visibility"] == "friends":
            visibility = "friends"
        if item["trip"]["visibility"] == 'private':
            visibility = "private"
            break

    # Use the purchasing_date from the first trip (or adjust as needed)
    newTrip["purchasing_date"] = first_trip.get("purchasing_date")

    # Merge prices using the already computed total price from priceDict
    newTrip["price"] = priceDict.get("total_price", 0)
    newTrip["currency"] = priceDict.get("user_currency", "USD")

    # Set ticket_id to empty since individual ticket info may not apply
    newTrip["ticket_id"] = ""

    # Set defaults for other required fields
    newTrip["reg"] = ""
    newTrip["seat"] = ""
    newTrip["material_type"] = ""
    newTrip["material_type_advanced"] = ""
    newTrip["waypoints"] = ""
    newTrip["notes"] = ""
    newTrip["onlyDateDuration"] = ""
    newTrip["originManualLat"] = None
    newTrip["originManualLng"] = None
    newTrip["destinationManualLat"] = None
    newTrip["destinationManualLng"] = None
    newTrip["visibility"] = visibility

    try:
        saveTripToDb(username, newTrip, final_path, tripType)
        return redirect(
            url_for("dynamic_trips", username=username, time="trips"), code=301
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/getMultiTrips", methods=["POST"])
def getMultiTrips():
    # Ids come in the JSON body (like getPublicTrips) so long lists don't hit
    # URL length limits.
    tripIds = request.get_json().get("tripIds")
    sortedTripList, priceDict = processPublicTrips(tripIds)
    userList = set()
    anonymous = {}
    users_cache = {}
    for trip in sortedTripList:
        name = trip["trip"]["username"]
        user = users_cache.get(name)
        if user is None:
            user = users_cache[name] = User.query.filter_by(username=name).first()
        if (
            not session.get(user.username)
            and not user.is_public()
            and not session.get(owner)
        ):
            if trip["trip"]["username"] not in anonymous.keys():
                anonymous[trip["trip"]["username"]] = f"Anon {len(anonymous) + 1}"
            trip["trip"]["username"] = anonymous[trip["trip"]["username"]]
        userList.add(trip["trip"]["username"])
    colours = {
        key: value
        for key, value in zip(
            userList, distinctipy.get_colors(len(userList), pastel_factor=0.3)
        )
    }
    return jsonify(sortedTripList, priceDict, colours)


def _fmt_legacy_dt(value):
    """Format a datetime/date as SQLite stored it: "YYYY-MM-DD HH:MM:SS" with the
    year always zero-padded to 4 digits (strftime("%Y") does not pad years < 1000
    on glibc, which would mangle the handful of trips with corrupt ancient years).
    """
    if isinstance(value, datetime):
        return (
            f"{value.year:04d}-{value.month:02d}-{value.day:02d} "
            f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}"
        )
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"


def adapt_pg_trip_row(mapping, username):
    """Convert a PostgreSQL `trips` query row (a SQLAlchemy row mapping) into the
    legacy SQLite `trip` dict shape expected by formatTrip, the templates and the
    DataTables frontend: legacy column names and the 1 (project) / -1 (unknown
    date) integer datetime sentinels.
    """
    d = dict(mapping)
    is_project = bool(d.pop("is_project", False))

    # PG -> legacy column names
    if "trip_id" in d:
        d["uid"] = d.pop("trip_id")
    if "trip_type" in d:
        d["type"] = d.pop("trip_type")
    if "purchase_date" in d:
        purchase_date = d.pop("purchase_date")
        # Conceptually a date; PG stores it as TIMESTAMP. Format date-only so the
        # edit page <input type="date"> populates (a full datetime won't bind).
        if isinstance(purchase_date, (datetime, date)):
            purchase_date = purchase_date.strftime("%Y-%m-%d")
        d["purchasing_date"] = purchase_date
    d.pop("user_id", None)
    d["username"] = username

    # start/end (and their utc_filtered variants) use 1/-1 sentinels when NULL;
    # real values become SQLite-style "YYYY-MM-DD HH:MM:SS" strings.
    for field in (
        "start_datetime",
        "end_datetime",
        "utc_filtered_start_datetime",
        "utc_filtered_end_datetime",
    ):
        if field in d:
            value = d[field]
            if value is None:
                d[field] = 1 if is_project else -1
            elif isinstance(value, (datetime, date)):
                d[field] = _fmt_legacy_dt(value)

    # tags: psycopg2 returns json as a parsed list; the frontend JSON.parse()s it,
    # so re-serialise to a string (None when there are no tags), matching SQLite.
    tags = d.get("tags")
    if isinstance(tags, (list, dict)):
        d["tags"] = json.dumps(tags) if tags else None

    # Coerce remaining DB-native types that don't JSON-serialise / that downstream
    # code expects as primitives.
    for key, value in list(d.items()):
        if isinstance(value, (datetime, date)):
            d[key] = _fmt_legacy_dt(value)
        elif isinstance(value, time):
            d[key] = value.strftime("%H:%M:%S")
        elif isinstance(value, Decimal):
            d[key] = float(value)

    return d


def get_trip_pg(trip_id):
    """Fetch a single trip from PostgreSQL in the legacy SQLite `trip` dict shape
    (with `time`/`operator_name`/`logo_url`), or None if it doesn't exist."""
    with pg_session() as pg:
        row = pg.execute(get_trip_query(), {"trip_id": trip_id}).fetchone()
    if row is None:
        return None
    username = get_username(row._mapping["user_id"])
    return adapt_pg_trip_row(row._mapping, username)


def get_trips_pg(trip_ids):
    """Fetch many trips in one query, each in the legacy `trip` dict shape.
    Returns {trip_id: trip}; ids that don't exist are simply absent."""
    ids = [int(t) for t in trip_ids]
    if not ids:
        return {}
    with pg_session() as pg:
        rows = pg.execute(get_trips_by_ids_query(), {"ids": ids}).fetchall()
    usernames = {}
    trips = {}
    for row in rows:
        mapping = row._mapping
        user_id = mapping["user_id"]
        if user_id not in usernames:
            usernames[user_id] = get_username(user_id)
        trips[mapping["trip_id"]] = adapt_pg_trip_row(mapping, usernames[user_id])
    return trips


def getTrips(username, projects):
    tripList = []
    user_id = get_user_id(username)
    # Fetch inside the session, then close it before formatTrip (which opens its
    # own pg sessions for tickets/exchange rates — they must not be nested).
    with pg_session() as pg:
        rows = pg.execute(get_user_trips_query(), {"user_id": user_id}).fetchall()
    trips = [adapt_pg_trip_row(row._mapping, username) for row in rows]
    if projects:
        trips.reverse()
    for trip in trips:
        trip = formatTrip(trip)
        if (projects and (trip["future"] == 1 or trip["plannedFuture"] == 1)) or (
            not projects and trip["past"] == 1
        ):
            tripList.append(trip)

    return json.dumps(tripList)


trip_column_names = [
    "type",
    "origin_station", 
    "destination_station",
    "start_datetime",
    "start_time",
    "end_time",
    "trip_duration_seconds",
    "trip_length",
    "trip_speed",
    "operator",
    "line_name",
    "countries",
    "visibility",
    "price",
    "material_type",
    "reg",
    "seat",
    "notes"
]

SORT_FIELD_EXPRS = {
    "temporal":              "temporal",
    "start_time":            "start_time",
    "departure_delay":       "COALESCE(departure_delay, 0)",
    "end_datetime":          "end_datetime",
    "end_time":              "end_time",
    "actual_arrival":        "(utc_filtered_end_datetime + COALESCE(arrival_delay, 0) * interval '1 second')",
    "arrival_delay":         "COALESCE(arrival_delay, 0)",
    "added_duration":        "COALESCE(arrival_delay, 0) - COALESCE(departure_delay, 0)",
    "trip_duration_seconds": "trip_duration_seconds",
    "actual_duration":       "trip_duration_seconds + COALESCE(arrival_delay, 0) - COALESCE(departure_delay, 0)",
    "trip_length":           "trip_length",
    "trip_speed":            "trip_speed",
    "actual_speed":          "trip_length / NULLIF(trip_duration_seconds + COALESCE(arrival_delay, 0) - COALESCE(departure_delay, 0), 0)",
    "origin_station":        "LOWER(CASE WHEN ascii(origin_station) BETWEEN 127462 AND 127487 THEN substring(origin_station FROM 4) ELSE origin_station END)",
    "destination_station":   "LOWER(CASE WHEN ascii(destination_station) BETWEEN 127462 AND 127487 THEN substring(destination_station FROM 4) ELSE destination_station END)",
    "type":                  "LOWER(type)",
    # Sort on the operator as displayed (resolved through the aliases), falling back
    # to the raw text when it names no known operator — otherwise a trip logged 'cff'
    # shows as SBB but sorts under C. Empty text is folded to NULL so operatorless
    # trips group with the NULLs at one end instead of straddling both.
    "operator":              "LOWER(NULLIF(COALESCE(operator_name, operator), ''))",
    "line_name":             "LOWER(line_name)",
    "price":                 "price",
}

# The instant a trip is ordered on under the default "temporal" sort. Also the
# expression the now-anchor counts against, so the boundary it finds is exactly the
# point the ordering puts it at.
TEMPORAL_SORT_EXPR = (
    "(utc_filtered_start_datetime + COALESCE(departure_delay, 0) * interval '1 second')"
)

def get_trips_api_internal(username, is_public=False):
    # Retrieve parameters from DataTables request
    start = request.form.get("start", type=int, default=0)
    length = request.form.get("length", type=int, default=10)
    # DataTables sends length=-1 for "All". SQLite read that as "no limit"; PG
    # rejects negative LIMITs, so pass NULL (LIMIT ALL) instead.
    if length is not None and length < 0:
        length = None
    search_value = request.form.get("search[value]", default="")
    draw = request.form.get("draw", type=int, default=1)
    past = int(request.args.get("projects") == "False")
    filter_types = request.form.get("filterTypes", type=int, default=0)
    # Past page's "show upcoming" toggle: fold dated future trips into the past
    # listing (they sort on top with the default temporal desc order).
    include_planned = int(
        past == 1 and request.form.get("includeFuture", type=int, default=0) == 1
    )

    is_friend = current_user_is_friend_with(username)

    # Sorting parameters
    sort_column = request.form.get("order[0][column]", type=int, default=3)
    sort_direction = request.form.get("order[0][dir]", default="desc" if past == 1 else "asc")

    sort_column_name = (
        trip_column_names[sort_column]
        if 0 <= sort_column < len(trip_column_names)
        else "default_column_name"
    )

    # Custom sort field from sort modal (overrides column-based sort)
    custom_sort_field = request.form.get("sort_field")
    if custom_sort_field and custom_sort_field in SORT_FIELD_EXPRS:
        sort_column_name = SORT_FIELD_EXPRS[custom_sort_field]
        sort_direction = request.form.get("sort_dir", sort_direction)

    # Now-anchor: with upcoming trips folded in, the descending timeline runs
    # [far future … now … oldest past], so its first page is the most distant plan —
    # useless as a landing page. When the client asks for it (first draw only, no
    # explicit ?page=), the offset is moved so that "now" sits mid-page instead.
    # Only for the temporal descending order, where "above/below now" means anything.
    anchor_now = (
        request.form.get("anchorNow", type=int, default=0) == 1
        and include_planned == 1
        and sort_column_name == "temporal"
        and sort_direction == "desc"
        # "All rows" has no page to centre.
        and length is not None
    )

    # Negative global terms (smart-search "!term"): trips that match NONE of these
    # in any field. Sent by the frontend as a JSON list.
    try:
        global_not_terms = [
            t for t in json.loads(request.form.get("search_not", "[]")) if t
        ]
    except (ValueError, TypeError):
        global_not_terms = []

    # Field filters ("from:Oslo", "duration>2h"): a list rather than one value per
    # column, so a field can carry several terms. They are ANDed, the alternatives
    # inside one term ORed.
    filters = parse_filters(request.form.get("filters"), is_public=is_public)

    additional_conditions, search_params = filter_conditions(filters)
    search_params.update(
        {
            "username": username,
            "past": past,
            "include_planned": include_planned,
        }
    )

    base_type = base_type_filter(filters, {t.value for t in TripTypes})
    if base_type:
        search_params["base_type"] = base_type

    # Global free-text search across every field. Appended to the outer query only
    # when there is something to match, so the common empty-search case lets Postgres
    # elide the airliners join (count query) and avoids the tickets join entirely.
    # Columns are referenced at the FilteredTrips level; ticket name and tags are
    # correlated EXISTS subqueries (the CTE no longer joins tickets).
    def _global_search_predicate(param, operator_ids=None, vessel_ids=None):
        like = (
            "remove_diacritics(LOWER({col})) LIKE remove_diacritics(LOWER(:" + param + "))"
        )
        global_search_columns = [
            "origin_station",
            "destination_station",
            "COALESCE(operator, '')",
            "COALESCE(countries, '')",
            "COALESCE(line_name, '')",
            "COALESCE(CAST(start_datetime AS text), '')",
            "COALESCE(CAST(end_datetime AS text), '')",
            "type",
            "COALESCE(notes, '')",
            "COALESCE(reg, '')",
            "COALESCE(material_type, '')",
            "COALESCE(material_type_advanced, '')",
            "COALESCE(iata, '')",
            "COALESCE(manufacturer, '')",
            "COALESCE(model, '')",
        ]
        terms = [like.format(col=col) for col in global_search_columns]
        terms.append(
            "EXISTS (SELECT 1 FROM tickets tk WHERE tk.uid = FilteredTrips.ticket_id"
            f" AND remove_diacritics(LOWER(COALESCE(tk.name, ''))) LIKE remove_diacritics(LOWER(:{param})))"
        )
        terms.append(
            "EXISTS (SELECT 1 FROM tags_associations fta JOIN tags ft ON fta.tag_id = ft.uid"
            f" WHERE fta.trip_id = FilteredTrips.uid AND remove_diacritics(LOWER(ft.name)) LIKE remove_diacritics(LOWER(:{param})))"
        )
        # Trips whose operator matches under another spelling — searching "SBB" finds
        # one logged as CFF. Only added when the term actually names a known operator.
        if operator_ids:
            terms.append(
                "EXISTS (SELECT 1 FROM trip_operators tvg"
                f" WHERE tvg.trip_id = FilteredTrips.uid AND tvg.operator_id = ANY(:{param}_operator_ids))"
            )
        # Same idea for ships, which answer to a name, an IMO and an MMSI alike: a
        # trip logged as '9773064' displays as Megastar, so searching either has to
        # find it. Scoped to ferries — the lookup is per row, and only there is `reg`
        # a ship — and only added when the term actually names a known vessel.
        if vessel_ids:
            terms.append(
                f"(type = 'ferry' AND vessel_resolve(reg) = ANY(:{param}_vessel_ids))"
            )
        return "(" + " OR ".join(terms) + ")"

    if search_value:
        search_params["search"] = like_pattern(search_value)
        global_operator_ids = find_operator_ids(search_value)
        if global_operator_ids:
            search_params["search_operator_ids"] = global_operator_ids
        with pg_session() as pg:
            global_vessel_ids = find_vessel_ids(pg, search_value)
        if global_vessel_ids:
            search_params["search_vessel_ids"] = global_vessel_ids
        additional_conditions.append(
            _global_search_predicate("search", global_operator_ids, global_vessel_ids)
        )

    # Negative global terms ("!term"): keep only trips where NO field matches the
    # term. COALESCE(..., FALSE) so a trip with all-NULL fields still passes the NOT.
    for idx, neg_term in enumerate(global_not_terms):
        neg_param = f"search_not_{idx}"
        search_params[neg_param] = like_pattern(neg_term)
        # Exclude by alias too, so "!SBB" also drops trips logged as CFF — otherwise
        # a negative filter would leave behind the spellings it looks equivalent to.
        neg_operator_ids = find_operator_ids(neg_term)
        if neg_operator_ids:
            search_params[f"{neg_param}_operator_ids"] = neg_operator_ids
        with pg_session() as pg:
            neg_vessel_ids = find_vessel_ids(pg, neg_term)
        if neg_vessel_ids:
            search_params[f"{neg_param}_vessel_ids"] = neg_vessel_ids
        additional_conditions.append(
            "NOT COALESCE("
            f"{_global_search_predicate(neg_param, neg_operator_ids, neg_vessel_ids)}"
            ", FALSE)"
        )

    # Build the queries
    cte = get_dynamic_user_trips_query(base_type_filter=bool(base_type))
    # The anchor rides along with the count that is run anyway: how many of the
    # matching rows sit above now, which — the order being descending on the same
    # expression — is the rank of the first past row.
    count_columns = "COUNT(*)"
    if anchor_now:
        count_columns += f", COUNT(*) FILTER (WHERE {TEMPORAL_SORT_EXPR} > NOW())"
    base_count_query = cte + f"SELECT {count_columns} FROM FilteredTrips"
    base_data_query = cte + "SELECT * FROM FilteredTrips"
    
    # Add type filtering if needed
    if is_public and is_friend:
        base_count_query += " WHERE (visibility = 'public' OR visibility = 'friends' OR (visibility IS NULL AND type IN ('train', 'bus', 'air', 'ferry', 'helicopter', 'aerialway', 'tram', 'metro')))"
        base_data_query += " WHERE (visibility = 'public' OR visibility = 'friends' OR (visibility IS NULL AND type IN ('train', 'bus', 'air', 'ferry', 'helicopter', 'aerialway', 'tram', 'metro')))"

        # Add column-specific conditions
        if additional_conditions:
            base_count_query += " AND " + " AND ".join(additional_conditions)
            base_data_query += " AND " + " AND ".join(additional_conditions)
    elif is_public:
        base_count_query += " WHERE (visibility = 'public' OR (visibility IS NULL AND type IN ('train', 'bus', 'air', 'ferry', 'helicopter', 'aerialway', 'tram', 'metro')))"
        base_data_query += " WHERE (visibility = 'public' OR (visibility IS NULL AND type IN ('train', 'bus', 'air', 'ferry', 'helicopter', 'aerialway', 'tram', 'metro')))"
        
        # Add column-specific conditions
        if additional_conditions:
            base_count_query += " AND " + " AND ".join(additional_conditions)
            base_data_query += " AND " + " AND ".join(additional_conditions)
    else:
        # Add type filter and column-specific conditions
        all_conditions = []
        if filter_types == 1:
            all_conditions.append("(visibility IS NULL OR visibility != 'private')")
        all_conditions.extend(additional_conditions)
        if all_conditions:
            base_count_query += " WHERE " + " AND ".join(all_conditions)
            base_data_query += " WHERE " + " AND ".join(all_conditions)

    count_query = base_count_query

    # Ensure the sort direction is safe
    if sort_direction not in ["asc", "desc"]:
        sort_direction = "asc"

    # Match SQLite's NULL ordering (nulls first when ascending, last when descending).
    nulls = "NULLS FIRST" if sort_direction == "asc" else "NULLS LAST"

    # Add sorting to data query. Project trips (NULL date + is_project) are grouped
    # to one end via the leading boolean key, matching the old "= 1" SQLite sort.
    if sort_column_name == "temporal":
        data_query = base_data_query + (
            f" ORDER BY (utc_filtered_start_datetime IS NULL AND is_project) {sort_direction},"
            f" {TEMPORAL_SORT_EXPR} {sort_direction} {nulls},"
            f" uid {sort_direction} LIMIT :limit OFFSET :offset"
        )
    elif sort_column_name == "end_datetime":
        data_query = base_data_query + (
            f" ORDER BY (utc_filtered_end_datetime IS NULL AND is_project) {sort_direction},"
            f" utc_filtered_end_datetime {sort_direction} {nulls},"
            f" uid {sort_direction} LIMIT :limit OFFSET :offset"
        )
    elif sort_column_name == "price":
        ticket_share_sql = "(SELECT t.price / NULLIF((SELECT COUNT(*) FROM trips t2 WHERE t2.ticket_id = t.uid), 0) FROM tickets t WHERE t.uid = ticket_id)"
        ticket_currency_sql = "(SELECT t.currency FROM tickets t WHERE t.uid = ticket_id)"
        ticket_date_sql = "(SELECT t.purchasing_date::date FROM tickets t WHERE t.uid = ticket_id)"
        price_expr = (
            f"CASE WHEN price IS NULL AND ticket_id IS NULL THEN NULL "
            f"ELSE COALESCE(price_to_eur(price, currency, purchasing_date::date), 0)"
            f"   + COALESCE(price_to_eur({ticket_share_sql}, {ticket_currency_sql}, {ticket_date_sql}), 0) "
            f"END"
        )
        data_query = base_data_query + (
            f" ORDER BY {price_expr} {sort_direction} NULLS LAST,"
            f" uid {sort_direction} LIMIT :limit OFFSET :offset"
        )
    else:
        # uid breaks ties so paging stays stable: sorts like operator or type have
        # large groups of equal (often NULL) values, and without it PG is free to
        # return them in a different order for each page's query, which makes rows
        # repeat across pages and others vanish.
        data_query = base_data_query + (
            f" ORDER BY {sort_column_name} {sort_direction} {nulls},"
            f" uid {sort_direction} LIMIT :limit OFFSET :offset"
        )

    search_params["user_id"] = get_user_id(username)

    with pg_session() as pg:
        # Fetch filtered count
        count_row = pg.execute(count_query, search_params).fetchone()
        records_filtered = count_row[0]

        # Centre the requested page on now: half a page of upcoming trips above the
        # boundary, the rest of the page below it. The offset stops being a multiple
        # of the page length, which DataTables displays happily; the next page click
        # snaps back to its own grid.
        if anchor_now:
            now_offset = count_row[1]
            start = max(0, now_offset - length // 2)

        # Fetch the actual page data
        search_params.update({
            "limit": length,
            "offset": start
        })
        rows = pg.execute(data_query, search_params).fetchall()

    # Convert PG rows back to the legacy trip dict shape (sentinels, legacy names).
    trip_dicts = [adapt_pg_trip_row(row._mapping, username) for row in rows]

    air_trip_uids = [
        trip["uid"] for trip in trip_dicts if trip["type"] in ("air", "helicopter")
    ]
    direct_flight_map = {}

    if air_trip_uids:
        with pg_session() as pg:
            for row in pg.execute(
                "SELECT trip_id, ST_NumPoints(geom) AS n FROM paths WHERE trip_id = ANY(:ids)",
                {"ids": [int(u) for u in air_trip_uids]},
            ).fetchall():
                # A 2-point route is a direct/geodesic flight line.
                direct_flight_map[row["trip_id"]] = row["n"] == 2

    # Add is_geodesic flag to each trip
    for trip in trip_dicts:
        if trip["type"] in ("air", "helicopter"):
            trip["is_geodesic"] = direct_flight_map.get(trip["uid"], False)
        else:
            trip["is_geodesic"] = None

    # Ships are shown by the name they carried on the trip's own date — `reg` holds the
    # hull, the name comes from the registration in force then (migration 0056).
    # Resolved here, for the page's ferry rows only, rather than as a column on the CTE:
    # the CTE is evaluated over every trip that passes the filters, and this is needed
    # for the twenty-odd actually being drawn. Keyed by trip rather than by reg, since
    # two crossings of one ship years apart can legitimately answer differently.
    ferry_uids = [
        int(trip["uid"])
        for trip in trip_dicts
        if trip["type"] == "ferry" and (trip.get("reg") or "").strip()
    ]
    if ferry_uids:
        with pg_session() as pg:
            names = {
                row["trip_id"]: row["name"]
                for row in pg.execute(
                    "SELECT trip_id, NULLIF(btrim(r.name), '') AS name"
                    " FROM trips"
                    " LEFT JOIN vessel_registrations r"
                    "   ON r.uid = vessel_identity("
                    "        vessel_resolve(trips.reg),"
                    "        COALESCE(trips.utc_start_datetime, trips.start_datetime))"
                    " WHERE trip_id = ANY(:ids)",
                    {"ids": ferry_uids},
                ).fetchall()
            }
        for trip in trip_dicts:
            if trip["type"] == "ferry":
                trip["vessel_name"] = names.get(trip["uid"])

    # If public, remove price information
    if is_public:
        for trip in trip_dicts:
            trip.pop("price", None)

    # Format trips for display
    trip_list = [formatTrip(trip) for trip in trip_dicts]

    # Attach carbon_footprint: prefer the stored value (now selected inline from
    # trips.carbon), fall back to inline calculation.
    for trip in trip_list:
        stored = trip.get("carbon")
        if stored is not None:
            trip["carbon_footprint"] = round(float(stored), 6)
        else:
            trip["carbon_footprint"] = round(
                calculate_carbon_footprint_for_trip(trip, []), 6
            )

    # Return the JSON for DataTables
    response = {
        "draw": draw,
        "recordsTotal": records_filtered,
        "recordsFiltered": records_filtered,
        "data": trip_list,
    }
    # Tell the client where the anchored page actually starts, so its paginator and
    # "showing x to y" agree with the rows it was handed.
    if anchor_now:
        response["start"] = start
    return jsonify(response)


@app.route("/u/<username>/get_trips_api", methods=["POST"])
@login_required
def get_trips_api(username):
    return get_trips_api_internal(username, is_public=False)


@app.route("/u/<username>/get_trips_api_public", methods=["POST"])
@public_required
def get_trips_api_public(username):
    return get_trips_api_internal(username, is_public=True)


@app.route("/admin")
@owner_required
def admin():
    """
    Admin page
    """
    return render_template(
        "admin/admin.html",
        title="Admin",
        username=getUser(),
        langs=json.dumps(list(readLang().keys())),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/getLastCurrencyDate")
@owner_required
def getLastCurrencyDate():
    with pg_session() as pg:
        last_rate_date = pg.execute(
            "SELECT rate_date FROM exchanges ORDER BY rate_date DESC LIMIT 1;"
        ).fetchone()

        if last_rate_date is not None:
            return jsonify(str(last_rate_date[0]))
        else:
            return "None"


@app.route("/toggle_role/<int:uid>/<role>/<action>", methods=["POST", "GET"])
@owner_required
def toggle_role(uid, role, action):
    # Define a set of allowed roles to prevent arbitrary field manipulation
    allowed_roles = {"admin", "alpha", "translator", "premium", "feature_admin"}

    # Validate the role and action
    if role not in allowed_roles:
        return jsonify(success=False, error="Invalid role"), 400

    if action not in ["make", "remove"]:
        return jsonify(success=False, error="Invalid action"), 400

    user = User.query.filter_by(uid=uid).first()

    if not user:
        return jsonify(success=False, error="User not found"), 404

    # Set the role to True for 'make' or False for 'remove'
    setattr(user, role, action == "make")

    if role == "premium":
        # Manual grants are intentionally never assigned a tier and never touch
        # Discord — tier assignment and role sync are BMC-webhook-only concepts,
        # so a manual grant stays "Manual" until/unless a real webhook confirms
        # an actual tier for this user (see apply_membership_status).
        user.premium_tier = None
        # A manual action (either direction) supersedes any pending BMC
        # cancellation flag — the owner reviewing it here is the resolution.
        user.premium_cancel_at = None

    authDb.session.commit()
    invalidate_admin_users_cache()

    if role == "premium" and action == "remove":
        # Asymmetric on purpose: a manual grant doesn't touch Discord (no
        # confirmed tier to base a role on), but an explicit manual revoke
        # should still strip whatever tier role they currently hold — leaving
        # it in place would mean Trainlog says "not premium" while Discord
        # still shows a paid tier.
        sync_discord_tier(user, None)

    return jsonify(success=True)


@app.route("/u/<username>/settings", methods=["GET", "POST"])
@login_required
def user_settings(username):
    """
    User settings
    """
    user = User.query.filter_by(username=username).first()

    if request.method == "POST":
        params = {}

        params["share_level"] = request.form["share_level"]
        params["leaderboard"] = "leaderboard" in request.form
        params["friend_search"] = "friend_search" in request.form
        params["appear_on_global"] = "appear_on_global" in request.form
        params["colorblind"] = "colorblind" in request.form
        params["lang"] = request.form["lang"]
        params["user_currency"] = request.form["user_currency"]
        params["default_landing"] = request.form["default_landing"]
        params["tileserver"] = request.form["tileserver"]
        params["globe"] = "globe" in request.form
        # Premium-only toggle: only honour it for premium users so a crafted POST
        # can't enable it without premium.
        params["flight_3d"] = ("flight_3d" in request.form) and bool(user.premium)
        params["live_tracking"] = ("live_tracking" in request.form) and bool(user.premium)

        for param in params:
            if getattr(user, param) != params[param]:
                setattr(user, param, params[param])
                if param == "lang":
                    changeLang(params[param], session)

        authDb.session.commit()

    langs = getLangDropdown(user)

    share_level = user.share_level
    leaderboard_checked = "checked" if user.leaderboard else ""
    friend_search_checked = "checked" if user.friend_search else ""
    appear_on_global_checked = "checked" if user.appear_on_global else ""
    colorblind_checked = "checked" if user.colorblind else ""
    flight_3d_checked = "checked" if user.flight_3d else ""
    live_tracking_checked = "checked" if user.live_tracking else ""

    return render_template(
        "user_settings.html",
        currencyOptions=get_available_currencies(),
        title=lang[session["userinfo"]["lang"]]["user_settings"],
        username=username,
        langs=langs,
        share_level=share_level,
        leaderboard_checked=leaderboard_checked,
        friend_search_checked=friend_search_checked,
        appear_on_global_checked=appear_on_global_checked,
        colorblind_checked=colorblind_checked,
        flight_3d_checked=flight_3d_checked,
        live_tracking_checked=live_tracking_checked,
        user_currency=user.user_currency,
        default_landing=user.default_landing,
        user_tileserver=user.tileserver,
        user_globe=user.globe,
        discord_id=user.discord_id,
        user_email=user.email,
        pending_email=user.pending_email,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/gps", methods=["GET"])
@login_required
def gps_settings(username):
    """Legacy alias for the API/integrations page (kept so old shared links work)."""
    return redirect(url_for("api_settings", username=username))


@app.route("/u/<username>/api", methods=["GET"])
@login_required
def api_settings(username):
    """
    Standalone API/integrations page: GPSLogger upload URL + MCP server URL.

    Deliberately not linked from the settings UI: it is reachable only by
    knowing the URL, so the page can be shared with someone else who is
    setting up GPSLogger to log trips for this user.
    """
    user = User.query.filter_by(username=username).first()
    if not user.gps_token:
        user.gps_token = secrets.token_urlsafe(32)
        authDb.session.commit()
    gps_upload_url = external_url("gps_logger_upload", token=user.gps_token)

    # MCP server access is premium-only. Mint the token lazily for premium users so
    # the connection URL can be shown on this page.
    mcp_url = None
    if user.premium:
        if not user.mcp_token:
            user.mcp_token = secrets.token_urlsafe(32)
            authDb.session.commit()
        mcp_url = external_url("mcp.handle") + f"?api_key={user.mcp_token}"

    return render_template(
        "api_settings.html",
        title=lang[session["userinfo"]["lang"]]["gpsLoggingTitle"],
        username=username,
        gps_upload_url=gps_upload_url,
        mcp_url=mcp_url,
        mcp_premium=user.premium,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/gps_token/regenerate", methods=["POST"])
@login_required
def regenerate_gps_token(username):
    """Invalidate the old GPSLogger upload URL by minting a fresh token."""
    user = User.query.filter_by(username=username).first()
    user.gps_token = secrets.token_urlsafe(32)
    authDb.session.commit()
    return redirect(url_for("api_settings", username=username))


@app.route("/u/<username>/mcp", methods=["GET"])
@login_required
def mcp_settings(username):
    """Return this user's MCP connection details (mints the token on first use).

    The token authenticates an external AI to the MCP server (/mcp), letting it
    list, create and delete this user's trips. Keep it secret; rotate via
    /u/<username>/mcp_token/regenerate.
    """
    user = User.query.filter_by(username=username).first()
    if not user.premium:
        abort(403)
    if not user.mcp_token:
        user.mcp_token = secrets.token_urlsafe(32)
        authDb.session.commit()
    mcp_url = external_url("mcp.handle") + f"?api_key={user.mcp_token}"
    return jsonify({
        "url": mcp_url,
        "token": user.mcp_token,
        "config": {
            "mcpServers": {
                "trainlog": {"type": "streamable-http", "url": mcp_url}
            }
        },
    })


@app.route("/u/<username>/mcp_token/regenerate", methods=["POST"])
@login_required
def regenerate_mcp_token(username):
    """Revoke the old MCP token by minting a fresh one (premium-only)."""
    user = User.query.filter_by(username=username).first()
    if not user.premium:
        abort(403)
    user.mcp_token = secrets.token_urlsafe(32)
    authDb.session.commit()
    return redirect(url_for("api_settings", username=username))


@app.route("/u/<username>/change_email", methods=["POST"])
@login_required
def request_email_change(username):
    """Request a change of the account's login email. A confirmation link is
    emailed to the new address rather than trusting it immediately, so a user
    can't take over an inbox they don't control. Confirming also re-matches
    any Buy Me a Coffee membership webhook that arrived for that email before
    it was linked to this account (see reconcile_pending_events)."""
    user = User.query.filter_by(username=username).first()
    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("Please enter an email address", "error")
        return redirect(url_for("user_settings", username=username))
    if email == user.email:
        flash("That is already your email", "error")
        return redirect(url_for("user_settings", username=username))
    if User.query.filter_by(email=email).first():
        flash("That email is already in use", "error")
        return redirect(url_for("user_settings", username=username))

    token = secrets.token_hex(32)
    user.pending_email = email
    user.email_verify_token = token
    authDb.session.commit()

    link = url_for("confirm_email_change", token=token, _external=True)
    sendEmail(
        email,
        "Confirm your new email - Trainlog",
        f'Click to confirm this is your new Trainlog email: <a href="{link}">{link}</a>',
    )
    flash("Check your inbox to confirm your new email", "success")
    return redirect(url_for("user_settings", username=username))


@app.route("/confirm_email/<token>", methods=["GET"])
def confirm_email_change(token):
    user = User.query.filter_by(email_verify_token=token).first()
    if not user:
        flash("Invalid or expired confirmation link", "error")
        return redirect(url_for("login"))

    if User.query.filter_by(email=user.pending_email).first():
        flash("That email is already in use", "error")
        return redirect(url_for("login"))

    user.email = user.pending_email
    user.pending_email = None
    user.email_verify_token = None
    authDb.session.commit()

    reconcile_pending_events(user)

    flash("Email updated", "success")
    return redirect(url_for("user_settings", username=user.username))


@app.route("/u/<username>/settings_app", methods=["GET", "POST"])
@login_required
def user_settings_app(username):
    """
    User settings API
    """
    user = User.query.filter_by(username=username).first()

    if request.method == "POST":
        data = request.get_json(silent=True) or {}

        allowed = {
            "share_level", 
            "leaderboard", 
            "friend_search", 
            "appear_on_global", 
            "colorblind",
            "user_currency"
        }
        changed = {}

        for k in allowed:
            if k in data:
                v = data[k]
                if k == "share_level":
                    v = int(v)
                else:
                    v = bool(v)

                if getattr(user, k) != v:
                    setattr(user, k, v)
                    changed[k] = v
        
        authDb.session.commit()

    langs = getLangDropdown(user)

    return jsonify({
        "username": user.username,
        "currencyOptions": get_available_currencies(),
        "langs": langs,
        "share_level": user.share_level,
        "leaderboard": user.leaderboard,
        "friend_search": user.friend_search,
        "appear_on_global": user.appear_on_global,
        "colorblind": user.colorblind,
        "user_currency": user.user_currency,
    }), 200


@app.route("/u/<username>/dynamic/<time>")
def redirect_dynamic_trips(username, time):
    return redirect(url_for("dynamic_trips", username=username, time=time), code=301)


@app.route("/u/<username>/<time>")
@login_required
def dynamic_trips(username, time=None):
    """
    Trips table, without projects

    """
    if time not in ("projects", "trips"):
        abort(404)
    projects = time == "projects"

    return render_template(
        "dynamic_trips.html",
        title=lang[session["userinfo"]["lang"]]["trips"],
        username=username,
        privateButtons=True,
        hasPrice=True,
        hasPrivateTrips=hasPrivateTrips(username),
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id(username)),
        isPublic=False,
        projects=projects,
        trip_column_names=trip_column_names,
        country_list=get_all_countries(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/public/<username>/<time>")
@public_required
def public_trips(username, time=None):
    """
    Trips table, without projects

    """
    projects = time == "projects"

    return render_template(
        "dynamic_trips.html",
        title=lang[session["userinfo"]["lang"]]["trips"],
        username=username,
        privateButtons=True,
        hasPrice=True,
        hasPrivateTrips=hasPrivateTrips(username),
        nav="bootstrap/public_nav.html",
        isCurrent=has_current_trip(get_user_id(username)),
        isPublic=True,
        projects=projects,
        trip_column_names=trip_column_names,
        country_list=get_all_countries(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )



@app.route("/u/<username>/<edit_copy_type>/<tripId>")
def edit_copy_trip(username, tripId, edit_copy_type):
    """
    Edit or copy trip details
    """
    user_obj = User.query.filter_by(username=username).first()
    colorblind = getattr(user_obj, "colorblind", False) if user_obj else False

    if "edit" in request.path:
        edit_copy_type = "edit"
    elif "copy" in request.path:
        edit_copy_type = "copy"
        
    from_app = request.args.get('fromApp') == 'true'

    trip = get_trip_pg(tripId)
    with pg_session() as pg:
        path = json.loads(
            list(
                pg.execute(
                    get_user_lines_query(), {"ids": [int(tripId)]}
                ).fetchone()
            )[1]
        )
    user = User.query.filter_by(username=trip["username"]).first()
    if not (session.get(user.username) or session.get(owner)):
        abort(401)
    trip = get_trip_pg(tripId)
    origin = trip["origin_station"]
    destination = trip["destination_station"]
    tripOperator = trip["operator"]
    tripLineName = trip["line_name"]
    tripVisibility = trip["visibility"]
    tripMaterialType = trip["material_type"]
    tripMaterialTypeAdvanced = trip["material_type_advanced"] if trip["material_type_advanced"] else ""
    tripSeat = trip["seat"]
    tripSeatCar = trip["seat_car"]
    tripReg = trip["reg"]
    tripType = trip["type"]
    tripNotes = trip["notes"]
    tripTicketId = trip["ticket_id"]
    tripPrice = (
        (trip["price"] if trip["price"] % 1 != 0 else int(trip["price"]))
        if trip["price"] not in [None, ""]
        else None
    )
    tripCurrency = trip["currency"]
    # Default the purchase date to today when none is stored, so editing never leaves
    # a price without a date.
    tripPurchasingDate = trip["purchasing_date"] or date.today().strftime("%Y-%m-%d")
    unknownType = None

    wplist = [path[0], path[-1]]
    if trip["waypoints"]:
        waypoints_coords = [
            [point["lat"], point["lng"]] for point in json.loads(trip["waypoints"])
        ]
        wplist = [path[0]] + waypoints_coords + [path[-1]]

    if trip["start_datetime"] in (1, -1):
        precision = "unknown"
        if trip["start_datetime"] == 1:
            unknownType = "future"
        else:
            unknownType = "past"
    elif (
        datetime.strptime(trip["start_datetime"], "%Y-%m-%d %H:%M:%S").strftime("%-S")
        == "1"
    ):
        precision = "onlyDate"
    else:
        precision = "precise"

    if trip["manual_trip_duration"] is not None:
        div = divmod(trip["manual_trip_duration"], 3600)
        tripHours = div[0]
        tripMinutes = divmod(div[1], 60)[0]
    else:
        tripHours = lang[session["userinfo"]["lang"]]["hours"]
        tripMinutes = lang[session["userinfo"]["lang"]]["minutes"]

    if edit_copy_type == "copy":
        tripDepartureDelay = ""
        tripArrivalDelay = ""
    else:
        tripDepartureDelay = int(trip["departure_delay"] / 60) if trip["departure_delay"] is not None else ""
        tripArrivalDelay = int(trip["arrival_delay"] / 60) if trip["arrival_delay"] is not None else ""
        
    context = {
        "title": lang[session["userinfo"]["lang"]][edit_copy_type],
        "start_datetime": trip["start_datetime"],
        "end_datetime": trip["end_datetime"],
        "currencyOptions": get_available_currencies(),
        "unknownType": unknownType,
        "precision": precision,
        "tripId": tripId,
        "origin": origin,
        "destination": destination,
        "trip": trip,
        "fr24_calls": fr24_usage(username),
        "edit_copy_type": edit_copy_type,
        "country_list": get_all_countries(),
        "username": username,
        "tripOperator": tripOperator or "",
        "tripHours": tripHours or "",
        "tripMinutes": tripMinutes or "",
        "tripLineName": tripLineName or "",
        "tripVisibility": tripVisibility or "",
        "tripMaterialType": tripMaterialType or "",
        "tripMaterialTypeAdvanced": tripMaterialTypeAdvanced or "",
        "tripSeat": tripSeat or "",
        "tripSeatCar": tripSeatCar if tripSeatCar is not None else "",
        "tripReg": tripReg or "",
        "tripPrice": tripPrice if tripPrice is not None else "",
        "tripCurrency": tripCurrency or "",
        "tripPurchasingDate": tripPurchasingDate or "",
        "tripType": tripType,
        "tripTicketId": tripTicketId or "",
        "wplist": wplist,
        "route_source": trip.get("route_source") or "router",
        "tripNotes": tripNotes or "",
        "colorblind": colorblind,
        "tripDepartureDelay": tripDepartureDelay,
        "tripArrivalDelay": tripArrivalDelay,
        "tripPowerType": trip.get("power_type"),
        "tripCo2Override": trip.get("co2_override"),
    }

    if from_app:
        return jsonify(context), 200
    
    # Only for the website
    context.update(lang[session["userinfo"]["lang"]])
    context.update(session["userinfo"])

    return render_template("edit_copy.html", **context)


@app.route("/u/<username>/export")
@login_required
def export(username):
    requestedTrips = request.args.get("trips", default=None)

    si = StringIO()
    cw = csv.writer(si)
    user_id = get_user_id(username)
    with pg_session() as pg:
        if requestedTrips is None:
            rows = pg.execute(
                "SELECT * FROM trips WHERE user_id = :uid", {"uid": user_id}
            ).fetchall()
        else:
            ids = [int(t) for t in requestedTrips.split(",")]
            rows = pg.execute(
                "SELECT * FROM trips WHERE user_id = :uid AND trip_id = ANY(:ids)",
                {"uid": user_id, "ids": ids},
            ).fetchall()
    trips = [adapt_pg_trip_row(row._mapping, username) for row in rows]

    tripIds = [trip["uid"] for trip in trips]
    paths = {}
    if tripIds:
        with pg_session() as pg:
            for path in pg.execute(
                get_user_lines_query(), {"ids": [int(i) for i in tripIds]}
            ).fetchall():
                paths[path["trip_id"]] = path["path"]

    columns = [k for k in trips[0].keys() if k != "ticket_id"] if trips else []
    cw.writerow(columns + ["path"])
    processedRows = []
    for trip in trips:
        rowP = [
            json.dumps(trip[k]) if k == "waypoints" else trip[k] for k in columns
        ]
        encoded = (
            polyline.encode(json.loads(paths[trip["uid"]]))
            if paths.get(trip["uid"])
            else ""
        )
        rowP.append(encoded)
        processedRows.append(rowP)
    cw.writerows(processedRows)
    response = make_response(si.getvalue())
    response.headers["Content-Disposition"] = (
        "attachment; filename=trainlog_{}_{}.csv".format(
            username, datetime.strftime(datetime.now(), "%Y-%m-%d_%H%M%S")
        )
    )
    response.headers["Content-type"] = "text/csv"

    return response


@app.route("/api/airlines")
def proxy_airlines():
    config = load_config()
    ninjas_api_key = config.get("api_ninjas", {}).get("api_key", "")
    fr24_token = config.get("FR24", {}).get("token_auth", "")

    name = request.args.get("name", "")
    icao = request.args.get("icao", "")
    if not icao and not name:
        return jsonify([])

    # Try API Ninjas first
    try:
        ninjas_response = requests.get(
            "https://api.api-ninjas.com/v1/airlines",
            params={"icao": icao} if icao else {"name": name},
            headers={"X-Api-Key": ninjas_api_key},
            timeout=5,
        )

        if ninjas_response.status_code == 200:
            data = ninjas_response.json()
            if data:  # Non-empty result
                return jsonify(data), 200
    except Exception as e:
        print(f"API Ninjas failed: {e}")

    # Fallback to FR24 (only works with ICAO)
    if icao:
        try:
            fr24_url = (
                f"https://fr24api.flightradar24.com/api/static/airlines/{icao}/light"
            )
            fr24_response = requests.get(
                fr24_url,
                headers={
                    "Accept": "application/json",
                    "Accept-Version": "v1",
                    "Authorization": f"Bearer {fr24_token}",
                },
                timeout=5,
            )

            if fr24_response.status_code == 200:
                fr24_data = fr24_response.json()
                return jsonify([fr24_data]), 200  # Wrap in list for consistency
            else:
                print(f"FR24 returned status: {fr24_response.status_code}")
        except Exception as e:
            print(f"FR24 fallback failed: {e}")

    return jsonify([]), 200


def _parse_mfr24_row(line, api_key):
    """Parse one MFR24 CSV data line.

    Returns (newTrip, newPath, display) on success, raises ValueError on failure.
    All external calls (airport DB lookup, airline API) happen here so that the
    subsequent save step is guaranteed to succeed.
    """
    data = line.split(",")
    newTrip = {}
    newPath = []

    try:
        newTrip["material_type"] = data[8].rsplit("(")[1].rsplit(")")[0]
    except (IndexError, ValueError):
        newTrip["material_type"] = ""
    newTrip["seat"] = data[10].strip() if len(data) > 10 else ""
    newTrip["reg"] = data[9].strip() if len(data) > 9 else ""
    newTrip["notes"] = data[14].strip() if len(data) > 14 else ""
    newTrip["price"] = newTrip["currency"] = newTrip["purchasing_date"] = None

    date_str = data[0].strip()
    dep_time = data[4].strip()
    arr_time = data[5].strip()
    dur_str = data[6].strip()

    if "-" not in date_str:
        date_str += "-01-01"
        dep_time = arr_time = "00:00:01"
        newTrip["precision"] = "onlyDate"
        newTrip["onlyDate"] = date_str
        try:
            h, m, s = map(int, dur_str.split(":"))
            newTrip["onlyDateDuration"] = h * 3600 + m * 60 + s
        except (ValueError, AttributeError):
            newTrip["onlyDateDuration"] = 0
    else:
        if dep_time == "00:00:00":
            dep_time = arr_time = "00:00:01"
            newTrip["precision"] = "onlyDate"
            newTrip["onlyDate"] = date_str
            try:
                h, m, s = map(int, dur_str.split(":"))
                newTrip["onlyDateDuration"] = h * 3600 + m * 60 + s
            except (ValueError, AttributeError):
                newTrip["onlyDateDuration"] = 0
        else:
            newTrip["newTripStart"] = (date_str + "T" + dep_time)[:16]
            end_dt_str = (date_str + "T" + arr_time)[:16]
            if (
                datetime.strptime(arr_time, "%H:%M:%S")
                - datetime.strptime(dep_time, "%H:%M:%S")
                < timedelta(0)
            ):
                end_dt_str = datetime.strftime(
                    datetime.strptime(end_dt_str, "%Y-%m-%dT%H:%M") + timedelta(days=1),
                    "%Y-%m-%dT%H:%M",
                )
            newTrip["newTripEnd"] = end_dt_str
            newTrip["precision"] = "preciseDates"

    newTrip["lineName"] = data[1].strip()
    origIata = data[2].rsplit("(")[-1].split("/")[0]
    destIata = data[3].rsplit("(")[-1].split("/")[0]

    try:
        newTrip["estimated_trip_duration"] = (
            datetime.strptime(dur_str, "%H:%M:%S") - datetime(1900, 1, 1)
        ).total_seconds()
    except ValueError:
        newTrip["estimated_trip_duration"] = 0

    for iata, key in ((origIata, "originStation"), (destIata, "destinationStation")):
        with pg_session() as pg:
            row = pg.execute(
                "SELECT * FROM airports WHERE iata = :searchPattern",
                {"searchPattern": iata},
            ).fetchone()
        if row is None:
            raise ValueError(f"Airport not found: {iata}")
        airport = dict(row._mapping)
        newTrip[key] = [
            [airport["latitude"], airport["longitude"]],
            "{} {} ({})".format(flag(airport["iso_country"]), airport["name"], airport["iata"]),
        ]
        newPath.append({"lat": airport["latitude"], "lng": airport["longitude"]})

    raw_airline = data[7].strip('"')
    airline_name = raw_airline.rsplit(" ", 1)[0]
    icao = raw_airline.rsplit("/", 1)[1].replace(")", "") if "/" in raw_airline else ""

    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=1, status_forcelist=[502, 503, 504])))
    api_url = "https://api.api-ninjas.com/v1/airlines?icao={}".format(icao)
    response = s.get(api_url, headers={"X-Api-Key": api_key}, timeout=10)

    if response.status_code == requests.codes.ok and response.json():
        operatorAPI = response.json()[0]
        newTrip["operator"] = operatorAPI["name"]
        if "logo_url" in operatorAPI:
            newTrip["operatorLogoURL"] = operatorAPI["logo_url"]
    else:
        newTrip["operator"] = airline_name

    newTrip["trip_length"] = getDistance(newPath[0], newPath[1])
    newTrip["unknownType"] = "past"
    newTrip["waypoints"] = json.dumps([])

    display = {
        "origin": newTrip["originStation"][1],
        "destination": newTrip["destinationStation"][1],
        "type": "plane",
        "date": date_str,
        "distance": str(round(newTrip["trip_length"] / 1000, 1)) + " km",
        "operator": newTrip.get("operator", ""),
        "line_name": newTrip["lineName"],
    }
    return newTrip, newPath, display


def _save_mfr24_trip(username, newTrip, newPath):
    """Persist a pre-parsed MFR24 trip.  All data is already resolved so this
    only touches the local DB and is guaranteed not to call external services."""
    options = {
        "orig": newTrip["originStation"][1],
        "dest": newTrip["destinationStation"][1],
        "user_id": get_user_id(username),
    }
    if newTrip["precision"] != "onlyDate":
        options["start_datetime"] = datetime.strftime(
            datetime.strptime(newTrip["newTripStart"], "%Y-%m-%dT%H:%M"),
            "%Y-%m-%d %H:%M:%S",
        )
        options["end_datetime"] = datetime.strftime(
            datetime.strptime(newTrip["newTripEnd"], "%Y-%m-%dT%H:%M"),
            "%Y-%m-%d %H:%M:%S",
        )
    else:
        options["start_datetime"] = options["end_datetime"] = datetime.strftime(
            datetime.strptime(newTrip["onlyDate"], "%Y-%m-%d") + timedelta(seconds=1),
            "%Y-%m-%d %H:%M:%S",
        )

    limits = [
        {"lat": newPath[0]["lat"], "lng": newPath[0]["lng"]},
        {"lat": newPath[-1]["lat"], "lng": newPath[-1]["lng"]},
    ]
    manual_trip_duration, start_datetime, end_datetime, utc_start_datetime, utc_end_datetime = (
        processDates(newTrip, limits)
    )
    countries = getCountriesFromPath(newPath, "air")
    now = datetime.now()

    with pg_session() as pg:
        sqlite_trip = pg.execute(get_duplicate_query(), options).fetchone()

    if sqlite_trip is not None:
        trip = get_trip(sqlite_trip["uid"])
        # _update_trip_in_sqlite (called by update_trip) reads snake_case keys from formData
        newTrip["origin_station"] = newTrip["originStation"][1]
        newTrip["destination_station"] = newTrip["destinationStation"][1]
        newTrip["type"] = "air"
        trip.origin_station = sanitize_param(newTrip["originStation"][1])
        trip.destination_station = sanitize_param(newTrip["destinationStation"][1])
        trip.start_datetime = start_datetime
        trip.utc_start_datetime = utc_start_datetime
        trip.end_datetime = end_datetime
        trip.utc_end_datetime = utc_end_datetime
        trip.trip_length = sanitize_param(newTrip["trip_length"])
        trip.estimated_trip_duration = sanitize_param(newTrip["estimated_trip_duration"])
        trip.manual_trip_duration = manual_trip_duration
        trip.operator = sanitize_param(newTrip["operator"])
        trip.countries = sanitize_param(countries)
        trip.line_name = sanitize_param(newTrip["lineName"])
        trip.last_modified = now
        trip.seat = sanitize_param(newTrip["seat"])
        trip.material_type = sanitize_param(newTrip["material_type"])
        trip.material_type_advanced = sanitize_param(newTrip.get("material_type_advanced"))
        trip.reg = sanitize_param(newTrip["reg"])
        trip.waypoints = None
        trip.notes = sanitize_param(newTrip["notes"])
        trip.price = sanitize_param(newTrip["price"])
        trip.currency = sanitize_param(newTrip["currency"])
        trip.purchasing_date = sanitize_param(newTrip["purchasing_date"])
        trip.ticket_id = None
        trip.is_project = options["start_datetime"] == 1 or end_datetime == 1
        trip.path = newPath
        update_trip(trip.trip_id, trip, newTrip)
    else:
        trip = Trip(
            username=username,
            user_id=get_user_id(username),
            origin_station=sanitize_param(newTrip["originStation"][1]),
            destination_station=sanitize_param(newTrip["destinationStation"][1]),
            start_datetime=start_datetime,
            utc_start_datetime=utc_start_datetime,
            end_datetime=end_datetime,
            utc_end_datetime=utc_end_datetime,
            trip_length=sanitize_param(newTrip["trip_length"]),
            estimated_trip_duration=sanitize_param(newTrip["estimated_trip_duration"]),
            manual_trip_duration=manual_trip_duration,
            operator=sanitize_param(newTrip["operator"]),
            countries=sanitize_param(countries),
            line_name=sanitize_param(newTrip["lineName"]),
            created=now,
            last_modified=now,
            type="air",
            seat=sanitize_param(newTrip["seat"]),
            material_type=sanitize_param(newTrip["material_type"]),
            material_type_advanced=sanitize_param(newTrip.get("material_type_advanced")),
            reg=sanitize_param(newTrip["reg"]),
            waypoints=None,
            notes=sanitize_param(newTrip["notes"]),
            price=sanitize_param(newTrip["price"]),
            currency=sanitize_param(newTrip["currency"]),
            purchasing_date=sanitize_param(newTrip["purchasing_date"]),
            ticket_id=None,
            is_project=options["start_datetime"] == 1 or end_datetime == 1,
            path=newPath,
            departure_delay=sanitize_param(newTrip.get("departure_delay")),
            arrival_delay=sanitize_param(newTrip.get("arrival_delay")),
        )
        create_trip(trip)

    airlineLogoProcess(newTrip)


@app.route("/u/<username>/parseMFR24", methods=["POST"])
@login_required
def parse_mfr24(username):
    """Stream MFR24 CSV parsing progress via Server-Sent Events.

    Events:
      {"type": "total",    "count": N}
      {"type": "progress", "current": i, "total": N}
      {"type": "result",   "result": {ok, display, save_data?, error?}}
      {"type": "done"}
    """
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    content = file.read().decode("utf-8", errors="replace")
    data_lines = [l for l in content.splitlines()[2:] if l.strip()]  # skip 2 header rows
    api_key = load_config().get("api_ninjas", {}).get("api_key", "")

    def generate():
        total = len(data_lines)
        yield f"data: {json.dumps({'type': 'total', 'count': total})}\n\n"

        for i, line in enumerate(data_lines):
            yield f"data: {json.dumps({'type': 'progress', 'current': i + 1, 'total': total})}\n\n"
            try:
                newTrip, newPath, display = _parse_mfr24_row(line, api_key)
                result = {
                    "ok": True,
                    "display": display,
                    "save_data": {"newTrip": newTrip, "newPath": newPath},
                }
            except Exception as e:
                parts = line.split(",")
                result = {
                    "ok": False,
                    "error": str(e),
                    "display": {
                        "origin":      parts[2].strip() if len(parts) > 2 else "?",
                        "destination": parts[3].strip() if len(parts) > 3 else "?",
                        "type":        "plane",
                        "date":        parts[0].strip() if parts else "?",
                        "distance":    "",
                        "operator":    "",
                        "line_name":   parts[1].strip() if len(parts) > 1 else "",
                    },
                }
            yield f"data: {json.dumps({'type': 'result', 'result': result})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return app.response_class(generate(), mimetype="text/event-stream")


@app.route("/u/<username>/saveParsedMFR24", methods=["POST"])
@login_required
def save_parsed_mfr24(username):
    """Save pre-parsed MFR24 trips (returned by parseMFR24) to the database."""
    data = request.get_json()
    trips = data.get("trips", [])
    saved = 0
    for t in trips:
        try:
            _save_mfr24_trip(username, t["newTrip"], t["newPath"])
            saved += 1
        except Exception as e:
            logger.error(f"Error saving parsed MFR24 trip: {e}")
    return jsonify({"saved": saved})


@app.route("/u/<username>/processMFR24", methods=["POST"])
@login_required
def processMFR24(username):
    if request.form:
        line = list(request.form.to_dict().items())[0][0]
        api_key = load_config().get("api_ninjas", {}).get("api_key", "")
        newTrip, newPath, _ = _parse_mfr24_row(line, api_key)
        _save_mfr24_trip(username, newTrip, newPath)
    return ""


@app.route("/getCountry", methods=["GET"])
def getCountry():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    return jsonify(getCountryFromCoordinates(lat, lng))


@app.route("/u/<username>/import", methods=["POST"])
@login_required
def importAll(username):
    if getUser() not in (username, owner):
        abort(403)

    data = list(request.form.to_dict().items())[0][0]

    csv.field_size_limit(10 * 1024 * 1024)  # 10 MB — accommodates long polyline paths
    try:
        csvfile = StringIO(data)
        reader = csv.DictReader(csvfile)
        preprocessed_rows = (
            {k: (v if v != "" else None) for k, v in row.items()} for row in reader
        )
        dataDict = list(preprocessed_rows)[0]
    except Exception as e:
        return jsonify({"error": str(e)}), 422

    now = datetime.now()

    # Handle special cases
    if dataDict.get("uid"):
        dataDict.pop("uid")

    # Legacy export compatibility: operator used URL-encoding in older CSVs.
    if dataDict.get("operator") is not None:
        dataDict["operator"] = unquote(dataDict["operator"])

    # Legacy export compatibility: line_name used URL-encoding in older CSVs.
    if dataDict.get("line_name") is not None:
        dataDict["line_name"] = unquote(dataDict["line_name"])

    if dataDict.get("countries") is not None:
        dataDict["countries"] = (
            dataDict["countries"].replace(' "', ', "').replace(",,", ",")
        )

    if dataDict.get("waypoints") is not None:
        dataDict["waypoints"] = json.loads(dataDict["waypoints"])

    # Legacy export compatibility: operator commas were encoded as "&&".
    if dataDict.get("operator") is not None:
        dataDict["operator"] = dataDict["operator"].replace("&&", ",")

    dataDict["created"] = now
    dataDict["last_modified"] = now
    dataDict["username"] = username
    user_id = User.query.filter_by(username=username).first().uid
    dataDict["user_id"] = user_id
    dataDict["ticket_id"] = ""
    # Remove path from main dict
    if dataDict.get("path"):
        rawPath = dataDict.pop("path")

    decodedPath = polyline.decode(rawPath)
    tmp_path = [{"lat": node[0], "lng": node[1]} for node in decodedPath]

    path = Path(path=tmp_path, trip_id=None)

    dataDict["precision"] = detect_precision(
        dataDict["start_datetime"], dataDict["end_datetime"]
    )
    if dataDict["precision"] == "unknown":
        dataDict["unknownType"] = (
            "future"
            if dataDict["start_datetime"] in [1, "1"]
            or dataDict["end_datetime"] in [1, "1"]
            else "past"
        )
    elif dataDict["precision"] == "preciseDates":
        dataDict["newTripStart"] = datetime.strftime(
            datetime.strptime(dataDict["start_datetime"], "%Y-%m-%d %H:%M:%S"),
            "%Y-%m-%dT%H:%M",
        )
        dataDict["newTripEnd"] = datetime.strftime(
            datetime.strptime(dataDict["end_datetime"], "%Y-%m-%d %H:%M:%S"),
            "%Y-%m-%dT%H:%M",
        )
    elif dataDict["precision"] == "onlyDate":
        # processDates() reads onlyDate/onlyDateDuration for date-only trips; the CSV
        # import never set them (KeyError -> 500). Take the date (YYYY-MM-DD, whether
        # the CSV had a bare date or the 00:00:01 marker) and carry the manual duration.
        dataDict["onlyDate"] = dataDict["start_datetime"][:10]
        dataDict["onlyDateDuration"] = dataDict.get("manual_trip_duration")
    else:
        dataDict["unknownType"] = None

    manDuration, start_datetime, end_datetime, utc_start_datetime, utc_end_datetime = (
        processDates(dataDict, tmp_path)
    )
    dataDict["is_project"] = start_datetime in [1, "1"] or end_datetime in [1, "1"]
    if start_datetime in [-1, 1, "-1", "1"]:
        start_datetime = None
    if end_datetime in [-1, 1, "-1", "1"]:
        end_datetime = None

    if dataDict.get("visibility") in ("public", "friends", "private"):
        visibility = dataDict["visibility"]
    else:
        visibility = get_default_trip_visibility(sanitize_param(dataDict["type"]))

    trip = Trip(
        trip_id=None,
        username=sanitize_param(dataDict["username"]),
        user_id=dataDict["user_id"],
        origin_station=sanitize_param(dataDict["origin_station"]),
        destination_station=sanitize_param(dataDict["destination_station"]),
        start_datetime=sanitize_param(start_datetime),
        end_datetime=sanitize_param(end_datetime),
        trip_length=sanitize_param(dataDict["trip_length"]),
        estimated_trip_duration=sanitize_param(dataDict["estimated_trip_duration"]),
        operator=sanitize_param(dataDict["operator"]),
        countries=sanitize_param(dataDict["countries"]),
        manual_trip_duration=manDuration,
        utc_start_datetime=utc_start_datetime,
        utc_end_datetime=utc_end_datetime,
        created=sanitize_param(dataDict["created"]),
        last_modified=sanitize_param(dataDict["last_modified"]),
        line_name=sanitize_param(dataDict["line_name"]),
        type=sanitize_param(dataDict["type"]),
        material_type=sanitize_param(dataDict["material_type"]),
        material_type_advanced=sanitize_param(dataDict.get("material_type_advanced")),
        seat=sanitize_param(dataDict["seat"]),
        reg=sanitize_param(dataDict["reg"]),
        waypoints=sanitize_param(dataDict["waypoints"]),
        notes=sanitize_param(dataDict["notes"]),
        price=sanitize_param(dataDict["price"]),
        currency=sanitize_param(dataDict["currency"]),
        purchasing_date=sanitize_param(dataDict["purchasing_date"]),
        ticket_id=sanitize_param(dataDict["ticket_id"]),
        is_project=dataDict["start_datetime"] == 1 or dataDict["end_datetime"] == 1,
        visibility=visibility,
        path=path,
        departure_delay=sanitize_param(dataDict.get("departure_delay")),
        arrival_delay=sanitize_param(dataDict.get("arrival_delay")),
    )

    try:
        create_trip(trip)
    except Exception as e:
        # Return an appropriate error response
        logger.exception(e)
        return jsonify({"error": "Failed to import data"}), 500

    return jsonify({"message": "Data imported successfully"}), 200


def detect_precision(start_date, end_date):
    if (
        start_date is None
        or start_date in ["", "1", 1, "-1", -1]
        or end_date is None
        or end_date in ["", "1", 1, "-1", -1]
    ):
        return "unknown"

    try:
        s = datetime.strptime(start_date, "%Y-%m-%d %H:%M:%S")
        e = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S")
        # Date-only trips are materialised at 00:00:01 (the app's marker); every real
        # time uses 00 seconds. Treat that marker as onlyDate so exporting then
        # re-importing a date-only trip stays date-only (instead of becoming precise).
        if s.time() == time(0, 0, 1) and e.time() == time(0, 0, 1):
            return "onlyDate"
        return "preciseDates"
    except ValueError:
        pass

    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")
    return "onlyDate"


@app.route("/admin/manual")
@owner_required
def adminManual():
    with pg_session() as pg:
        stationsList = [
            dict(row._mapping)
            for row in pg.execute("SELECT * FROM manual_stations").fetchall()
        ]
    return render_template(
        "admin/manual.html",
        stationsList=stationsList,
        username=getUser(),
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


def _clean_vessel_number(value, digits, label):
    """A submitted IMO or MMSI, or None when the field was left empty.

    Raises ValueError with a message for the admin when it is neither — the DB has the
    same check, but a constraint violation reaches the browser as a 500.
    """
    value = (value or "").strip()
    if not value:
        return None
    if not (value.isdigit() and len(value) == digits):
        raise ValueError(f"{label} must be exactly {digits} digits")
    return value


# The register itself, as one query (see _register_rows).
SHIP_REGISTER_SQL = """
            -- How many logged trips each hull actually accounts for. Resolved
            -- through vessel_resolve, so trips logged under an old name count
            -- towards the same hull; the register is sorted by it, which puts the
            -- ships worth curating at the top.
            WITH trip_counts AS (
                SELECT vessel_resolve(reg) AS vessel_id,
                       COUNT(*) AS trips,
                       -- How many people, not just how many crossings: forty trips by
                       -- one commuter and forty by forty travellers are different
                       -- ships to be curating.
                       COUNT(DISTINCT user_id) AS users,
                       -- Who mostly runs her. Decoration, but a useful one: the
                       -- operator is often what identifies a small ferry, where the
                       -- name is generic and the hull has no IMO. mode() ignores
                       -- NULLs and NULLIF keeps blanks from winning; the first name
                       -- only, since a ferry is rarely a multi-operator trip.
                       MODE() WITHIN GROUP (
                           ORDER BY NULLIF(btrim(split_part(operator, ',', 1)), '')
                       ) AS operator
                FROM trips
                WHERE trip_type = 'ferry' AND reg IS NOT NULL AND btrim(reg) <> ''
                GROUP BY 1
            )
            SELECT v.uid, v.imo, v.trainlog_id,
                   -- The hull as it is NOW. Not columns of the hull — it has none of
                   -- these — but the most recent registration's, so a row can be
                   -- recognised at a glance. The history is under Periods.
                   r.name, r.country_code,
                   -- Every other name it has carried. Shown as "ex …" and, because
                   -- DataTables searches the text of a row, that is also what makes
                   -- a hull findable by a name it no longer goes by.
                   ARRAY(
                       SELECT a.name FROM vessel_registrations a
                       WHERE a.vessel_id = v.uid
                         AND a.name IS NOT NULL
                         AND a.uid IS DISTINCT FROM r.uid
                       ORDER BY a.effective_date DESC NULLS LAST, a.uid DESC
                   ) AS former_names,
                   p.local_image_path,
                   COALESCE(c.trips, 0) AS trips,
                   COALESCE(c.users, 0) AS users,
                   c.operator,
                   o.short_name AS operator_name,
                   o.logo_url AS operator_logo,
                   (SELECT COUNT(*) FROM vessel_registrations a WHERE a.vessel_id = v.uid)
                       AS registrations
            FROM vessels v
            LEFT JOIN vessel_registrations r ON r.uid = vessel_identity(v.uid, NULL)
            LEFT JOIN trip_counts c ON c.vessel_id = v.uid
            -- That operator name resolved through operator_aliases, exactly as a
            -- trip resolves its own (see get_trip.sql), so a ferry logged as SNCM
            -- picks up the logo held under Corsica Linea. The current logo: this is
            -- a register of ships, not a history of liveries.
            LEFT JOIN LATERAL (
                SELECT op.short_name,
                       (SELECT l.logo_url FROM operator_logos l
                        WHERE l.operator_id = op.operator_id
                        ORDER BY l.effective_date DESC NULLS LAST, l.uid DESC
                        LIMIT 1) AS logo_url
                FROM operator_aliases a
                JOIN operators op ON op.operator_id = a.operator_id
                WHERE a.normalized = operator_normalize(c.operator)
                ORDER BY (a.operator_type = 'operator') DESC, a.operator_id
                LIMIT 1
            ) o ON TRUE
            LEFT JOIN LATERAL (
                SELECT local_image_path
                FROM ship_pictures
                WHERE registration_id = r.uid AND local_image_path IS NOT NULL
                ORDER BY fetch_date DESC NULLS LAST, uid DESC
                LIMIT 1
            ) p ON TRUE
            -- One row or all of them: the same query serves the page and the single-row
            -- refresh the table does over ajax after an edit, so a row that comes back can
            -- never disagree with the one it replaces. CAST, because a bind parameter that
            -- is only ever compared to NULL has no type psycopg2 can infer.
            WHERE (CAST(:vessel_id AS INTEGER) IS NULL OR v.uid = CAST(:vessel_id AS INTEGER))
            ORDER BY COALESCE(c.trips, 0) DESC, r.name NULLS LAST, v.uid
            
"""


def _register_rows(pg, vessel_id=None):
    """The register's rows — all of them, or the one hull asked for."""
    return [
        # The flag is rendered here rather than in the template: get_flag_emoji is a
        # plain helper, not a Jinja global, and every other page that shows a flag
        # server-side does the same.
        dict(
            row._mapping,
            flag=get_flag_emoji(row["country_code"]) if row["country_code"] else "",
            unregistered=False,
        )
        for row in pg.execute(
            SHIP_REGISTER_SQL, {"vessel_id": vessel_id}
        ).fetchall()
    ]


@app.route("/admin/ships", methods=["GET", "POST"])
@admin_required
def ships():
    """
    The vessel register, one row per HULL.

    A hull is permanent and carries only its numbers: the IMO, or the synthetic
    trainlog_id where it has none. Its MMSI, flag and photo all belong to a registration —
    the hull under one identity, from a date — and are edited through the Periods view,
    because a ship that has been renamed has no single one of any of them.

    The current name is the exception: this form edits it directly, because a hull with no
    IMO would otherwise open a form with nothing in it at all.
    """
    if request.method == "POST":
        vessel_id = (request.form.get("vessel_id") or "").strip()
        name = (request.form.get("name") or "").strip() or None

        try:
            imo = _clean_vessel_number(request.form.get("imo"), 7, "IMO")
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        if not (imo or name):
            return jsonify(
                {"success": False, "error": "Give at least a name or an IMO"}
            ), 400

        with pg_session() as pg:
            # An IMO identifies one hull, so it cannot sit on two. A hull with no IMO is
            # perfectly legal — it is identified by its trainlog_id instead.
            if imo:
                clash = pg.execute(
                    """
                    SELECT v.uid, r.name FROM vessels v
                    LEFT JOIN vessel_registrations r ON r.uid = vessel_identity(v.uid, NULL)
                    WHERE v.imo = :imo
                      AND v.uid <> COALESCE(CAST(NULLIF(:vessel_id, '') AS INTEGER), -1)
                    LIMIT 1
                    """,
                    {"imo": imo, "vessel_id": vessel_id},
                ).fetchone()
                if clash:
                    return jsonify(
                        {
                            "success": False,
                            "error": f"IMO {imo} is already hull #{clash['uid']}"
                            f" ({clash['name'] or 'unnamed'})",
                        }
                    ), 409

            if vessel_id:
                uid = int(vessel_id)
                pg.execute(
                    "UPDATE vessels SET imo = :imo, updated_on = CURRENT_TIMESTAMP"
                    " WHERE uid = :uid",
                    {"imo": imo, "uid": uid},
                )
            else:
                uid = pg.execute(
                    "INSERT INTO vessels (imo) VALUES (:imo) RETURNING uid", {"imo": imo}
                ).scalar()

            # The name belongs to a registration. This form edits the current one — for
            # almost every ship the only one — so that a hull with no IMO is not an empty
            # form with nothing to identify it by. A hull that has no registration yet
            # (known only by its number) gets its first.
            if name:
                registration_id = pg.execute(
                    "SELECT vessel_identity(:uid, NULL)", {"uid": uid}
                ).scalar()
                if registration_id:
                    pg.execute(
                        "UPDATE vessel_registrations SET name = :name,"
                        " updated_on = CURRENT_TIMESTAMP WHERE uid = :uid",
                        {"name": name, "uid": registration_id},
                    )
                else:
                    pg.execute(
                        "INSERT INTO vessel_registrations (vessel_id, name)"
                        " VALUES (:vessel_id, :name)",
                        {"vessel_id": uid, "name": name},
                    )

        # The uid, so the caller can fetch back the row it has just changed (or created)
        # instead of reloading the page.
        return jsonify({"success": True, "vessel_id": uid})

    with pg_session() as pg:
        shipList = _register_rows(pg)

        # What people have logged that the register does not know: a ferry trip whose
        # reg resolves to no hull at all. Almost always a name typed by hand. These join
        # the register in ONE table — an unregistered ship is a ship, it just has no row
        # yet, and keeping it in a list of its own meant scrolling between two tables to
        # see whether a name was already held under another spelling.
        unlinked = [
            dict(row._mapping, unregistered=True)
            for row in pg.execute(
                """
                -- Grouped by the NORMALISED name, so "MS Pont-Aven", "Pont Aven" and
                -- "pont-aven" are one ship and one decision, exactly as vessel_resolve
                -- would treat them once the row exists. The spelling offered back is the
                -- commonest one; the others are shown so a mis-grouping is visible.
                WITH unlinked AS (
                    SELECT vessel_normalize(reg) AS name_key,
                           MODE() WITHIN GROUP (ORDER BY btrim(reg)) AS reg,
                           COUNT(*) AS trips,
                           COUNT(DISTINCT user_id) AS users,
                           ARRAY_AGG(DISTINCT btrim(reg)) AS spellings,
                           -- Who runs her, as in the register above: on a ship with
                           -- nothing but a generic name, the operator is what says which
                           -- ship it is.
                           MODE() WITHIN GROUP (
                               ORDER BY NULLIF(btrim(split_part(operator, ',', 1)), '')
                           ) AS operator
                    FROM trips
                    WHERE trip_type = 'ferry'
                      AND reg IS NOT NULL AND btrim(reg) <> ''
                      -- Punctuation only normalises to nothing, and a registration must
                      -- have a name or an MMSI — there is nothing here to create.
                      AND vessel_normalize(reg) IS NOT NULL
                      AND vessel_resolve(reg) IS NULL
                    GROUP BY 1
                )
                SELECT u.*, o.short_name AS operator_name, o.logo_url AS operator_logo
                FROM unlinked u
                LEFT JOIN LATERAL (
                    SELECT op.short_name,
                           (SELECT l.logo_url FROM operator_logos l
                            WHERE l.operator_id = op.operator_id
                            ORDER BY l.effective_date DESC NULLS LAST, l.uid DESC
                            LIMIT 1) AS logo_url
                    FROM operator_aliases a
                    JOIN operators op ON op.operator_id = a.operator_id
                    WHERE a.normalized = operator_normalize(u.operator)
                    ORDER BY (a.operator_type = 'operator') DESC, a.operator_id
                    LIMIT 1
                ) o ON TRUE
                ORDER BY u.trips DESC, u.reg
                """
            ).fetchall()
        ]

    # One list, one table. The unregistered rows carry the register's own keys — the
    # logged text stands in for the name, and everything a hull has and they have not is
    # simply absent — so the template renders both from the same loop and DataTables
    # sorts and searches across the two.
    shipList = shipList + [
        dict(row, name=row["reg"], registrations=0, uid=None, imo=None,
             trainlog_id=None, flag="", former_names=[], local_image_path=None)
        for row in unlinked
    ]
    shipList.sort(key=lambda s: (-(s["trips"] or 0), (s["name"] or "").lower()))

    return render_template(
        "admin/ships.html",
        shipList=shipList,
        # The one-click cleanup below acts on exactly these, and the button says how many.
        unusedCount=sum(1 for s in shipList if not s["unregistered"] and not s["trips"]),
        unregisteredCount=sum(1 for s in shipList if s["unregistered"]),
        # For the flag picker in the Periods form, same list every other country select
        # on the site is built from.
        country_list=get_all_countries(),
        username=getUser(),
        nav="bootstrap/navigation.html",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/ships/backfill", methods=["GET", "POST"])
@admin_required
def backfill_ships():
    """
    Fill the register's gaps from Wikidata, on demand.

    GET is the page: a worklist of what Wikidata could add, in three piles — fields it
    can fill in on a number match, photos it can license properly, and names only a human
    can resolve. It is a page rather than an overlay because working through it is a
    session, not a glance.

    POST is its two calls. With no plan it builds one (the slow half — several SPARQL
    round trips, tens of seconds) and writes nothing; carrying a plan back writes exactly
    it, with no network at all. So what gets written is what was on screen rather than
    whatever a second query happens to return, and the page can hand back a subset when
    the admin unticks a row.

    Safe to re-run: only NULLs are ever filled, so a name or number curated here always
    survives. Every item is re-checked against the register at write time, since a plan
    can be minutes old.
    """
    if request.method == "GET":
        return render_template(
            "admin/ships_backfill.html",
            username=getUser(),
            nav="bootstrap/navigation.html",
            **lang[session["userinfo"]["lang"]],
            **session["userinfo"],
        )

    raw_plan = request.form.get("plan")

    try:
        if raw_plan:
            written = backfill_apply_plan(json.loads(raw_plan))
            result = {**written, "suggestions": [], "photos": [], "applied": True}
        else:
            result = {
                **backfill_build_plan(report_names=True),
                "skipped": [],
                "applied": False,
            }
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": f"Malformed plan: {exc}"}), 400
    except Exception as exc:
        logger.exception("Vessel backfill failed")
        return jsonify({"success": False, "error": str(exc)}), 502

    return jsonify({"success": True, **result})


@app.route("/admin/ships/delete", methods=["POST"])
@admin_required
def delete_ship():
    vessel_id = request.form.get("vessel_id")

    with pg_session() as pg:
        # Cached photos go with it (ON DELETE CASCADE); the files stay on disk, as
        # they always have.
        pg.execute(
            "DELETE FROM vessels WHERE uid = :uid",
            {"uid": int(vessel_id)},
        )

    return jsonify({"success": True})


@app.route("/admin/ships/<int:vessel_id>/row")
@admin_required
def ship_row(vessel_id):
    """
    One hull's row of the register, as the HTML the table holds.

    What the page fetches after an edit instead of reloading itself: the register is a
    table of several hundred rows, most of them with a photo, and re-fetching all of it
    to show that one name changed is the reload this replaces. Rendered from the same
    partial the table is built from, so the row that comes back cannot drift from the
    one it replaces.
    """
    with pg_session() as pg:
        rows = _register_rows(pg, vessel_id)

    if not rows:
        return "", 404
    return render_template("admin/ship_row.html", ship=rows[0])


@app.route("/admin/ships/lookup")
@admin_required
def ship_lookup():
    """
    Hulls matching a few typed characters, for picking one to merge into.

    Deliberately not the register query: this is a chooser, so it wants a name, a number
    and a photo for twenty rows, not trip counts and operator logos for eight hundred.
    Searches every name a hull has carried, since the row being merged away is often the
    one holding the spelling being searched for.
    """
    query = (request.args.get("q") or "").strip()
    exclude = request.args.get("exclude") or 0
    if not query:
        return jsonify({"success": True, "results": []})

    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT v.uid, v.imo, v.trainlog_id, r.name, p.local_image_path
            FROM vessels v
            LEFT JOIN vessel_registrations r ON r.uid = vessel_identity(v.uid, NULL)
            LEFT JOIN LATERAL (
                SELECT local_image_path
                FROM ship_pictures
                WHERE registration_id = r.uid AND local_image_path IS NOT NULL
                ORDER BY fetch_date DESC NULLS LAST, uid DESC
                LIMIT 1
            ) p ON TRUE
            WHERE v.uid <> CAST(:exclude AS INTEGER)
              AND (v.imo LIKE :like
                   OR v.trainlog_id ILIKE :like
                   OR EXISTS (SELECT 1 FROM vessel_registrations a
                              WHERE a.vessel_id = v.uid AND a.name ILIKE :like))
            ORDER BY r.name NULLS LAST, v.uid
            LIMIT 20
            """,
            {"like": f"%{query}%", "exclude": exclude},
        ).fetchall()

    return jsonify(
        {
            "success": True,
            "results": [
                {
                    "uid": row["uid"],
                    "imo": row["imo"],
                    "trainlog_id": row["trainlog_id"],
                    "name": row["name"],
                    "image": (
                        f"/static/images/ship_pictures/{row['local_image_path']}"
                        if row["local_image_path"]
                        else None
                    ),
                }
                for row in rows
            ],
        }
    )


@app.route("/admin/ships/merge", methods=["POST"])
@admin_required
def merge_ships():
    """
    Fold one hull into another: the trips move, and the hull merged away is deleted.

    The same ship reaches the register more than once — logged as "John Paul II", as
    "St John Paul Ii" and as "MV Saint John Paul II", each spelling resolving to nothing
    and each registered in its turn. They are one hull with three rows, and the only way
    back is to say which row is the ship.

    What moves is the trips, and a photo where the survivor has none. What does NOT move
    is the loser's registrations: they are dated periods of a hull, and grafting an
    undated one onto another hull makes it the newest — which would rename the survivor
    to the misspelling being merged away. They go with the row (ON DELETE CASCADE).
    """
    source_id = (request.form.get("source_id") or "").strip()
    target_id = (request.form.get("target_id") or "").strip()
    if not (source_id and target_id):
        return jsonify({"success": False, "error": "Two hulls are needed"}), 400
    if source_id == target_id:
        return jsonify({"success": False, "error": "That is the same hull"}), 400

    with pg_session() as pg:
        hulls = {
            row["uid"]: row
            for row in pg.execute(
                """
                SELECT v.uid, v.imo, v.trainlog_id,
                       vessel_identity(v.uid, NULL) AS registration_id
                FROM vessels v WHERE v.uid IN (:source, :target)
                """,
                {"source": int(source_id), "target": int(target_id)},
            ).fetchall()
        }
        source = hulls.get(int(source_id))
        target = hulls.get(int(target_id))
        if not (source and target):
            return jsonify({"success": False, "error": "No such hull"}), 400

        # Resolved to ids first: after the UPDATE these trips resolve to the target, and
        # after the DELETE the source is gone — neither can be used to find them again.
        trip_ids = [
            row[0]
            for row in pg.execute(
                """
                SELECT trip_id FROM trips
                WHERE trip_type = 'ferry'
                  AND reg IS NOT NULL AND btrim(reg) <> ''
                  AND vessel_resolve(reg) = :source
                """,
                {"source": source["uid"]},
            ).fetchall()
        ]
        if trip_ids:
            pg.execute(
                "UPDATE trips SET reg = :key WHERE trip_id = ANY(:ids)",
                {"key": target["imo"] or target["trainlog_id"], "ids": trip_ids},
            )

        # A photo is worth keeping: it is the same ship, and the row being merged away
        # may be the one somebody found a picture for. Only where the survivor has none,
        # so a curated photo is never displaced by an older one.
        if source["registration_id"] and target["registration_id"]:
            pg.execute(
                """
                UPDATE ship_pictures SET registration_id = :target
                WHERE registration_id = :source
                  AND NOT EXISTS (
                      SELECT 1 FROM ship_pictures q
                      WHERE q.registration_id = :target AND q.local_image_path IS NOT NULL
                  )
                """,
                {"source": source["registration_id"], "target": target["registration_id"]},
            )

        # Registrations and any photo left on them go with it (ON DELETE CASCADE); the
        # image files stay on disk, as they do for a plain delete.
        pg.execute("DELETE FROM vessels WHERE uid = :uid", {"uid": source["uid"]})

    return jsonify({"success": True, "vessel_id": target["uid"], "trips": len(trip_ids)})


@app.route("/admin/ships/prune", methods=["POST"])
@admin_required
def prune_ships():
    """
    Drop every hull no trip resolves to.

    A hull earns its row by being sailed on. One with no trip at all is nearly always a
    mistake — a typo'd IMO, a duplicate created before the name resolved, a row from a
    trip since deleted or re-typed — and it costs the register nothing to be rid of it,
    because registering the ship again is the same three fields.

    Photos go with it (ON DELETE CASCADE on ship_pictures); the files stay on disk, as
    they do for a single delete. Note this reaches a hull an admin added by hand and has
    not logged a trip on yet, which is the one case where it is not a mistake — hence a
    button that says how many, and not something that runs on its own.
    """
    with pg_session() as pg:
        deleted = pg.execute(
            """
            -- The used set is resolved ONCE, over the ferry trips, rather than per hull:
            -- vessel_resolve is four index lookups a call, and asking it per vessel per
            -- trip is the shape that took the periods endpoint to a minute (0056).
            WITH used AS (
                SELECT DISTINCT vessel_resolve(reg) AS uid
                FROM trips
                WHERE trip_type = 'ferry' AND reg IS NOT NULL AND btrim(reg) <> ''
            )
            DELETE FROM vessels
            WHERE uid NOT IN (SELECT uid FROM used WHERE uid IS NOT NULL)
            RETURNING uid
            """
        ).fetchall()

    # The uids, not just the count: the page takes those rows out of the table itself
    # rather than reloading to find them gone.
    return jsonify({"success": True, "deleted": [row[0] for row in deleted]})


@app.route("/admin/ships/wikidata_search")
@admin_required
def ship_wikidata_search():
    """
    Ships on Wikidata matching a name, with their numbers and their photo.

    The counterpart to the backfill, which goes the other way: it matches a hull we hold
    by its IMO or MMSI, and can do nothing for a ship logged as a bare name. This finds
    the item by name so the numbers can be filled in from it — and once there is a
    number, everything else in the register works.

    Candidates only, as with the Commons search: several real ships share a name, and
    Wikidata cannot tell which one somebody sailed on.
    """
    try:
        results = wikidata_search_ships(request.args.get("q"))
    except Exception as exc:
        logger.exception("Wikidata ship search failed")
        return jsonify({"success": False, "error": str(exc)}), 502
    return jsonify({"success": True, "results": results})


def _unresolved_trips(pg, reg):
    """
    The ferry trips logged under this text that still resolve to no hull.

    Read BEFORE anything is written, in both the register and the merge paths: once a row
    exists that resolves the name, "trips that resolve to nothing" no longer describes
    them, and a name two hulls share would be settled by vessel_resolve's own tie-break
    (lowest uid) rather than by the admin who is looking at it.
    """
    return [
        row[0]
        for row in pg.execute(
            """
            SELECT trip_id FROM trips
            WHERE trip_type = 'ferry'
              AND reg IS NOT NULL AND btrim(reg) <> ''
              AND vessel_normalize(reg) = vessel_normalize(:reg)
              AND vessel_resolve(reg) IS NULL
            """,
            {"reg": reg},
        ).fetchall()
    ]


@app.route("/admin/ships/adopt", methods=["POST"])
@admin_required
def adopt_ship():
    """
    Turn a name people have been logging into a hull of its own.

    The register only knows a ferry trip's `reg` when something resolves it; everything
    else is text somebody typed, and that text is all this has to go on. So: create the
    hull (with an IMO if the admin knows one, otherwise its trainlog_id alone), give it
    its first registration under the name as logged, and re-point the trips at the hull's
    key — the same rewrite migration 0056 did, and what makes a later rename reach every
    trip at once instead of orphaning them.

    The trips are chosen BEFORE the row exists, while they still resolve to nothing. Two
    hulls may end up sharing a name, and vessel_resolve settles that by uid; picking the
    trips afterwards would quietly hand them to whichever hull won.

    `merge_into` registers nothing and moves the trips onto a hull already held. That is
    the answer when the number given belongs to one — "IMO 9107796, Polarlys" logged as
    text, against a Polarlys the register has had all along. The clash is reported with
    the hull it clashed with, so the page can offer the merge in place of the refusal:
    the two are the same ship, and a second row for her is exactly what should not be
    created.
    """
    reg = (request.form.get("reg") or "").strip()
    name = (request.form.get("name") or "").strip() or reg
    country_code = (request.form.get("country_code") or "").strip() or None
    merge_into = (request.form.get("merge_into") or "").strip()

    if not reg:
        return jsonify({"success": False, "error": "No reg given"}), 400

    try:
        imo = _clean_vessel_number(request.form.get("imo"), 7, "IMO")
        mmsi = _clean_vessel_number(request.form.get("mmsi"), 9, "MMSI")
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    with pg_session() as pg:
        # Merging: the hull exists and is not touched. Its name, numbers and periods are
        # what is already known about the ship, and they outrank a form filled in from
        # the text of a trip — all that is missing is the trips.
        if merge_into:
            hull = pg.execute(
                """
                SELECT v.uid, v.imo, v.trainlog_id, r.name
                FROM vessels v
                LEFT JOIN vessel_registrations r ON r.uid = vessel_identity(v.uid, NULL)
                WHERE v.uid = :uid
                """,
                {"uid": int(merge_into)},
            ).fetchone()
            if not hull:
                return jsonify({"success": False, "error": "No such hull"}), 400

            trip_ids = _unresolved_trips(pg, reg)
            if trip_ids:
                pg.execute(
                    "UPDATE trips SET reg = :key WHERE trip_id = ANY(:ids)",
                    {"key": hull["imo"] or hull["trainlog_id"], "ids": trip_ids},
                )
            return jsonify(
                {
                    "success": True,
                    "merged": True,
                    "vessel_id": hull["uid"],
                    "name": hull["name"] or reg,
                    "trips": len(trip_ids),
                }
            )

        if imo:
            clash = pg.execute(
                """
                SELECT v.uid, r.name
                FROM vessels v
                LEFT JOIN vessel_registrations r ON r.uid = vessel_identity(v.uid, NULL)
                WHERE v.imo = :imo
                """,
                {"imo": imo},
            ).fetchone()
            if clash:
                return jsonify(
                    {
                        "success": False,
                        "error": f"IMO {imo} is already hull #{clash['uid']}"
                        f" ({clash['name'] or 'unnamed'})",
                        # What makes the merge offerable rather than just refused.
                        "vessel_id": clash["uid"],
                        "label": clash["name"] or imo,
                    }
                ), 409
        if mmsi:
            clash = pg.execute(
                """
                SELECT r.vessel_id, c.name
                FROM vessel_registrations r
                LEFT JOIN vessel_registrations c
                       ON c.uid = vessel_identity(r.vessel_id, NULL)
                WHERE r.mmsi = :mmsi
                """,
                {"mmsi": mmsi},
            ).fetchone()
            if clash:
                return jsonify(
                    {
                        "success": False,
                        "error": f"MMSI {mmsi} belongs to hull #{clash['vessel_id']}"
                        f" ({clash['name'] or 'unnamed'})",
                        "vessel_id": clash["vessel_id"],
                        "label": clash["name"] or mmsi,
                    }
                ), 409

        trip_ids = _unresolved_trips(pg, reg)

        hull = pg.execute(
            "INSERT INTO vessels (imo) VALUES (:imo) RETURNING uid, trainlog_id",
            {"imo": imo},
        ).fetchone()

        registration_id = pg.execute(
            """
            INSERT INTO vessel_registrations (vessel_id, name, mmsi, country_code)
            VALUES (:vessel_id, :name, :mmsi, :country_code)
            RETURNING uid
            """,
            {
                "vessel_id": hull["uid"],
                "name": name,
                "mmsi": mmsi,
                "country_code": country_code,
            },
        ).scalar()

        if trip_ids:
            pg.execute(
                "UPDATE trips SET reg = :key WHERE trip_id = ANY(:ids)",
                {"key": imo or hull["trainlog_id"], "ids": trip_ids},
            )

        # The photo of the Wikidata item the admin picked, if they picked one. Fetched
        # here rather than left for a second click, because the identification has just
        # been made and this is the picture of the ship that was identified — with an
        # author and a licence, which the image search cannot give.
        photo = None
        image_url = (request.form.get("commons_image") or "").strip()
        if image_url:
            try:
                photo = fetch_commons_picture(pg, registration_id, image_url)
            except Exception:
                # The hull is registered either way; a photo that failed to download is
                # one button press away in the Periods view.
                logger.exception("Commons photo import failed for new hull %s", hull["uid"])

    return jsonify(
        {
            "success": True,
            "vessel_id": hull["uid"],
            "registration_id": registration_id,
            "trainlog_id": hull["trainlog_id"],
            "name": name,
            "trips": len(trip_ids),
            "photo": bool(photo),
        }
    )


@app.route("/admin/ships/commons_search")
@admin_required
def ship_commons_search():
    """
    Candidate photos from Wikimedia Commons for a free-text query.

    The lookup for a ship known only by name: /registrations/fetch_photo goes through
    Wikidata, which matches on numbers alone, and a hull with neither an IMO nor an MMSI
    gets nothing from it. Search results cannot be trusted to be the right ship, so they
    are returned to be looked at and picked from — never stored here.
    """
    try:
        results = search_commons_images(request.args.get("q"))
    except Exception as exc:
        logger.exception("Commons search failed")
        return jsonify({"success": False, "error": str(exc)}), 502
    return jsonify({"success": True, "results": results})


@app.route("/admin/ships/registrations/commons_photo", methods=["POST"])
@admin_required
def use_commons_search_photo():
    """Store one chosen Commons file against one registration, with its attribution."""
    registration_id = request.form.get("registration_id")
    title = (request.form.get("title") or "").strip()
    if not (registration_id and title):
        return jsonify({"success": False, "error": "Nothing chosen"}), 400

    try:
        with pg_session() as pg:
            stored = fetch_commons_picture(pg, int(registration_id), title)
    except Exception as exc:
        logger.exception("Commons photo import failed")
        return jsonify({"success": False, "error": str(exc)}), 502

    if not stored:
        # fetch_commons_picture refuses a file whose licence it cannot read, which is
        # the case worth naming: nothing is wrong with the network.
        return jsonify(
            {"success": False, "error": "Could not read that file's licence — not stored"}
        ), 400

    return jsonify({"success": True, "image": f"/static/images/ship_pictures/{stored}"})


def _photo_credit_fields():
    """The author and licence submitted alongside a photo, as bind params.

    Both are optional and both are free text: a licence is whatever the file says it is
    ("CC BY-SA 4.0", "Public domain"), and there is no list to constrain it to.
    """
    return {
        "author": (request.form.get("photo_author") or "").strip() or None,
        "license": (request.form.get("photo_license") or "").strip() or None,
    }


@app.route("/admin/ships/<int:vessel_id>/registrations")
@admin_required
def ship_registrations(vessel_id):
    """
    The identities one hull has carried, newest first.

    A ship is a hull plus a sequence of registrations — each an MMSI, a name and a flag,
    from a date (migration 0056). IMO 8601915 is the shape of it: Amorella under the
    Finnish flag from 1988, Mega Victoria under the Italian one from 2022. A crossing in
    2008 has to read Amorella, and does, because the name is resolved at the trip's date.
    """
    with pg_session() as pg:
        rows = pg.execute(
            """
            -- Each of this hull's trips assigned to the period its date falls in, once
            -- for the whole answer. As a correlated subquery per period it re-resolved
            -- every ferry trip once per row, which is how this endpoint came to take a
            -- minute (see the resolver's note in migration 0056).
            WITH hull_trips AS (
                SELECT vessel_identity(
                           :vessel_id,
                           COALESCE(t.utc_start_datetime, t.start_datetime)
                       ) AS registration_id
                FROM trips t
                WHERE t.trip_type = 'ferry'
                  AND t.reg IS NOT NULL AND btrim(t.reg) <> ''
                  AND vessel_resolve(t.reg) = :vessel_id
            ),
            period_trips AS (
                SELECT registration_id, COUNT(*) AS trips
                FROM hull_trips GROUP BY registration_id
            )
            SELECT r.uid, r.mmsi, r.name, r.country_code, r.effective_date,
                   p.local_image_path,
                   -- Where the photo came from and under what licence (migration 0057).
                   -- Shown, not just stored: a CC BY-SA file must be credited to be shown
                   -- at all, so an admin has to be able to see that the credit is there
                   -- and to put it right when it is not.
                   p.uid AS picture_id, p.source, p.author, p.license, p.referrer_url,
                   (r.uid = vessel_identity(r.vessel_id, NULL)) AS is_current,
                   COALESCE(pt.trips, 0) AS trips
            FROM vessel_registrations r
            LEFT JOIN period_trips pt ON pt.registration_id = r.uid
            LEFT JOIN LATERAL (
                SELECT uid, local_image_path, source, author, license, referrer_url
                FROM ship_pictures
                WHERE registration_id = r.uid AND local_image_path IS NOT NULL
                ORDER BY fetch_date DESC NULLS LAST, uid DESC
                LIMIT 1
            ) p ON TRUE
            WHERE r.vessel_id = :vessel_id
            ORDER BY r.effective_date DESC NULLS LAST, r.uid DESC
            """,
            {"vessel_id": vessel_id},
        ).fetchall()

    return jsonify(
        [
            {
                "uid": row["uid"],
                "mmsi": row["mmsi"],
                "name": row["name"],
                "country": row["country_code"],
                # Date only: a registration takes effect on a day, not at a time.
                "effective_date": (
                    row["effective_date"].strftime("%Y-%m-%d")
                    if row["effective_date"]
                    else None
                ),
                "image": (
                    f"/static/images/ship_pictures/{row['local_image_path']}"
                    if row["local_image_path"]
                    else None
                ),
                "picture_id": row["picture_id"],
                "source": row["source"],
                "author": row["author"],
                "license": row["license"],
                "referrer_url": row["referrer_url"],
                "is_current": bool(row["is_current"]),
                "trips": row["trips"],
            }
            for row in rows
        ]
    )


@app.route("/admin/ships/registrations", methods=["POST"])
@admin_required
def save_ship_registration():
    """
    Add or edit one registration of a hull.

    An MMSI is deliberately allowed to repeat across the periods of one hull — a ship
    renamed under the same flag keeps its number (migration 0056). It is refused when it
    belongs to a DIFFERENT hull, which is a typo rather than a history.
    """
    vessel_id = (request.form.get("vessel_id") or "").strip()
    registration_id = (request.form.get("registration_id") or "").strip()
    name = (request.form.get("name") or "").strip() or None
    country_code = (request.form.get("country_code") or "").strip() or None
    effective_date = (request.form.get("effective_date") or "").strip() or None
    file = request.files.get("ship_picture")

    try:
        mmsi = _clean_vessel_number(request.form.get("mmsi"), 9, "MMSI")
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    if not (name or mmsi):
        return jsonify(
            {"success": False, "error": "Give at least a name or an MMSI"}
        ), 400

    with pg_session() as pg:
        if registration_id and not vessel_id:
            vessel_id = pg.execute(
                "SELECT vessel_id FROM vessel_registrations WHERE uid = :uid",
                {"uid": int(registration_id)},
            ).scalar()
        if not vessel_id:
            return jsonify({"success": False, "error": "Unknown vessel"}), 400
        vessel_id = int(vessel_id)

        if mmsi:
            clash = pg.execute(
                """
                SELECT v.imo, v.trainlog_id, r.name
                FROM vessel_registrations r
                JOIN vessels v ON v.uid = r.vessel_id
                WHERE r.mmsi = :mmsi AND r.vessel_id <> :vessel_id
                LIMIT 1
                """,
                {"mmsi": mmsi, "vessel_id": vessel_id},
            ).fetchone()
            if clash:
                return jsonify(
                    {
                        "success": False,
                        "error": f"MMSI {mmsi} belongs to another hull"
                        f" ({clash['imo'] or clash['trainlog_id']}"
                        f" — {clash['name'] or 'unnamed'})",
                    }
                ), 409

        params = {
            "vessel_id": vessel_id,
            "mmsi": mmsi,
            "name": name,
            "country_code": country_code,
            "effective_date": effective_date,
        }
        if registration_id:
            pg.execute(
                """
                UPDATE vessel_registrations
                SET mmsi = :mmsi, name = :name, country_code = :country_code,
                    effective_date = CAST(:effective_date AS timestamp),
                    updated_on = CURRENT_TIMESTAMP
                WHERE uid = :uid
                """,
                {**params, "uid": int(registration_id)},
            )
            uid = int(registration_id)
        else:
            uid = pg.execute(
                """
                INSERT INTO vessel_registrations
                    (vessel_id, mmsi, name, country_code, effective_date)
                VALUES (:vessel_id, :mmsi, :name, :country_code,
                        CAST(:effective_date AS timestamp))
                RETURNING uid
                """,
                params,
            ).scalar()

        # A photo shows one name on one hull, so it belongs to the period being edited
        # rather than to the ship in general.
        if file and file.filename:
            label = name or mmsi or str(uid)
            filename = (
                f"{country_code or 'XX'}_{label}_{uid}.jpg".replace(" ", "_")
                .replace("/", "")
            )
            file.save(os.path.join("static/images/ship_pictures", filename))
            pg.execute(
                """
                INSERT INTO ship_pictures
                    (registration_id, vessel_name, country_code, local_image_path,
                     source, author, license)
                VALUES (:registration_id, :vessel_name, :country_code, :local_image_path,
                        'upload', :author, :license)
                """,
                {
                    "registration_id": uid,
                    "vessel_name": name,
                    "country_code": country_code,
                    "local_image_path": filename,
                    # An upload is also how somebody else's photo gets in. Given an
                    # author, the display credits it; left empty it is treated as the
                    # admin's own and needs none.
                    **_photo_credit_fields(),
                },
            )

    return jsonify({"success": True, "uid": uid})


@app.route("/admin/ships/backfill/photo", methods=["POST"])
@admin_required
def use_commons_photo():
    """
    Replace one registration's photo with the freely-licensed one from Wikimedia Commons.

    Offered rather than applied, because it is a visible change and the Commons file may
    be older or show the ship in another livery. What it buys is a licence: the search
    photos are copyrighted stills credited to whoever hosted them, where a Commons file
    carries an author and a licence that permits the use.

    The old row is left in place — it simply stops being the newest, which is the one
    every reader picks.
    """
    registration_id = request.form.get("registration_id")
    image_url = (request.form.get("image") or "").strip() or None
    if not registration_id or not image_url:
        return jsonify({"success": False, "error": "Nothing to fetch"}), 400

    try:
        with pg_session() as pg:
            stored = fetch_commons_picture(pg, int(registration_id), image_url)
    except Exception as exc:
        logger.exception("Commons photo import failed")
        return jsonify({"success": False, "error": str(exc)}), 502

    if not stored:
        return jsonify(
            {"success": False, "error": "No usable licence on that file"}
        ), 422

    return jsonify(
        {"success": True, "image": f"/static/images/ship_pictures/{stored}"}
    )


@app.route("/admin/ships/backfill/confirm", methods=["POST"])
@admin_required
def confirm_backfill_suggestion():
    """
    Accept one name-match suggestion from the backfill preview.

    The backfill never writes these itself: a ship's name is not unique, so matching one
    against Wikidata's label finds the right hull most of the time and the wrong one the
    rest. Confirming is therefore a human act — but a human act should be one click, not
    a form to retype, which is what this is.

    A candidate with no IMO and no MMSI is still assignable — plenty of small ferries
    have neither, and the register can hold a ship known only by name and flag. It gets a
    trainlog_id like any other hull.

    Creates the hull and its first registration, and then REWRITES the trips: every
    ferry trip whose reg named this ship now holds the hull key instead — the IMO, or the
    synthetic trainlog_id where there is none. That is the same rewrite migration 0056
    ran for the ships it already knew, and it is the point of the exercise: those trips
    stop depending on a spelling and start resolving to a hull.

    The numbers are refused if they belong to something else already, since that is the
    case where the match was wrong.
    """
    name = (request.form.get("name") or "").strip() or None
    # The candidate's country of registry, when Wikidata knows it. It is the flag the
    # created registration starts with; an admin can correct it under Periods.
    country_code = (request.form.get("country_code") or "").strip() or None
    # And its photo on Wikimedia Commons, if it has one — fetched with its author and
    # licence, since that is the condition of showing it at all.
    image_url = (request.form.get("image") or "").strip() or None
    try:
        imo = _clean_vessel_number(request.form.get("imo"), 7, "IMO")
        mmsi = _clean_vessel_number(request.form.get("mmsi"), 9, "MMSI")
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    if not name:
        return jsonify({"success": False, "error": "No name given"}), 400

    with pg_session() as pg:
        # If the name already resolves, the register has moved on since the preview.
        if pg.execute("SELECT vessel_resolve(:name)", {"name": name}).scalar():
            return jsonify(
                {"success": False, "error": f"{name} already names a ship"}
            ), 409

        if imo and pg.execute(
            "SELECT 1 FROM vessels WHERE imo = :imo", {"imo": imo}
        ).fetchone():
            return jsonify(
                {"success": False, "error": f"IMO {imo} is already another hull"}
            ), 409
        if mmsi and pg.execute(
            "SELECT 1 FROM vessel_registrations WHERE mmsi = :mmsi", {"mmsi": mmsi}
        ).fetchone():
            return jsonify(
                {"success": False, "error": f"MMSI {mmsi} is already another ship"}
            ), 409

        # The Wikidata item the admin picked. Worth keeping: with several namesakes the
        # choice is a judgement, and this is the only record of which one was made.
        wikidata = (request.form.get("wikidata") or "").strip() or None
        vessel_id = pg.execute(
            "INSERT INTO vessels (imo, notes) VALUES (:imo, :notes) RETURNING uid",
            {"imo": imo, "notes": f"wikidata:{wikidata}" if wikidata else None},
        ).scalar()
        registration_id = pg.execute(
            "INSERT INTO vessel_registrations (vessel_id, name, mmsi, country_code)"
            " VALUES (:vessel_id, :name, :mmsi, :country_code) RETURNING uid",
            {
                "vessel_id": vessel_id,
                "name": name,
                "mmsi": mmsi,
                "country_code": country_code,
            },
        ).scalar()
        if image_url:
            try:
                fetch_commons_picture(pg, registration_id, image_url)
            except Exception:
                # A ship created without its photo is still a ship created; the picture
                # can be fetched or uploaded from the Periods view afterwards.
                logger.exception("Commons photo import failed for %s", image_url)

        # The trips that named it can now point at the hull, exactly as the migration
        # did for the ships it already knew.
        trips = pg.execute(
            """
            UPDATE trips SET reg = COALESCE(v.imo, v.trainlog_id)
            FROM vessels v
            WHERE v.uid = :vessel_id
              AND trips.trip_type = 'ferry'
              AND trips.reg IS NOT NULL AND btrim(trips.reg) <> ''
              AND vessel_resolve(trips.reg) = v.uid
              AND btrim(trips.reg) <> COALESCE(v.imo, v.trainlog_id)
            """,
            {"vessel_id": vessel_id},
        ).rowcount

    return jsonify({"success": True, "vessel_id": vessel_id, "trips": trips})


@app.route("/admin/ships/registrations/upload_photo", methods=["POST"])
@admin_required
def upload_ship_registration_photo():
    """
    Attach a photo to one registration, straight from the row.

    The period form can do this too, but only as part of an edit; wanting to add a
    picture and nothing else is the common case and should not require filling a form
    in. Everything else about the registration is left alone.
    """
    registration_id = request.form.get("registration_id")
    file = request.files.get("ship_picture")

    if not registration_id or not (file and file.filename):
        return jsonify({"success": False, "error": "No photo given"}), 400

    with pg_session() as pg:
        registration = pg.execute(
            "SELECT uid, name, mmsi, country_code FROM vessel_registrations WHERE uid = :uid",
            {"uid": int(registration_id)},
        ).fetchone()
        if not registration:
            return jsonify({"success": False, "error": "Unknown registration"}), 404

        label = registration["name"] or registration["mmsi"] or str(registration["uid"])
        filename = (
            f"{registration['country_code'] or 'XX'}_{label}_{registration['uid']}.jpg"
            .replace(" ", "_")
            .replace("/", "")
        )
        file.save(os.path.join("static/images/ship_pictures", filename))
        pg.execute(
            """
            INSERT INTO ship_pictures
                (registration_id, vessel_name, country_code, local_image_path,
                 source, author, license)
            VALUES (:registration_id, :vessel_name, :country_code, :local_image_path,
                    'upload', :author, :license)
            """,
            {
                "registration_id": registration["uid"],
                "vessel_name": registration["name"],
                "country_code": registration["country_code"],
                "local_image_path": filename,
                **_photo_credit_fields(),
            },
        )

    return jsonify(
        {"success": True, "image": f"/static/images/ship_pictures/{filename}"}
    )


@app.route("/admin/ships/registrations/photo_credit", methods=["POST"])
@admin_required
def edit_ship_photo_credit():
    """
    Correct the provenance of a photo already on file.

    The credit under a picture is drawn from its row (migration 0057), so a wrong or
    missing one can only be fixed here. It matters beyond tidiness: a CC BY-SA file shown
    without its author and licence is a licence breach, and an upload wrongly marked as a
    search result is credited to Vesselfinder for a photo Vesselfinder never took.
    """
    picture_id = request.form.get("picture_id")
    source = (request.form.get("source") or "").strip() or None
    if not picture_id:
        return jsonify({"success": False, "error": "No photo given"}), 400
    if source not in (None, "upload", "wikimedia", "vesselfinder"):
        return jsonify({"success": False, "error": f"Unknown source {source}"}), 400

    with pg_session() as pg:
        updated = pg.execute(
            """
            UPDATE ship_pictures
            SET source = :source, author = :author, license = :license
            WHERE uid = :uid
            """,
            {"uid": int(picture_id), "source": source, **_photo_credit_fields()},
        ).rowcount

    if not updated:
        return jsonify({"success": False, "error": "Unknown photo"}), 404
    return jsonify({"success": True})


@app.route("/admin/ships/registrations/fetch_photo", methods=["POST"])
@admin_required
def fetch_ship_registration_photo():
    """
    Look a photo up for one registration, on demand.

    Same Google Images search /getVesselPhoto runs on a cache miss, but aimed at a
    period: it searches that period's own name and files what it finds against it, so a
    picture of the ship under its former name is stored against the former name. Behind a
    button because it is a paid external search, and only offered where there is no photo.
    """
    registration_id = request.form.get("registration_id")
    if not registration_id:
        return jsonify({"success": False, "error": "No registration given"}), 400

    try:
        with pg_session() as pg:
            photo = fetch_picture_for_registration(pg, int(registration_id))
    except Exception as exc:
        logger.exception("Vessel photo lookup failed")
        return jsonify({"success": False, "error": str(exc)}), 502

    if not photo:
        return jsonify({"success": True, "image": None, "error": "Nothing found"})

    return jsonify({"success": True, **photo})


@app.route("/admin/ships/registrations/delete", methods=["POST"])
@admin_required
def delete_ship_registration():
    """
    Drop one registration. The hull stays, along with any other periods it has.

    Trips are unaffected — they hold the hull key, not this row — but the ones whose date
    fell in this period will now resolve to whichever period covers them instead.
    """
    registration_id = request.form.get("registration_id")

    with pg_session() as pg:
        pg.execute(
            "DELETE FROM vessel_registrations WHERE uid = :uid",
            {"uid": int(registration_id)},
        )

    return jsonify({"success": True})


def _parse_at(value):
    """
    The moment to resolve a vessel at, or None for "now".

    Callers hand this straight from a trip, and a trip's date is not always a date: the
    legacy shape carries 1 (project) and -1 (unknown) as sentinels, and a form field can
    be half-typed. Anything that is not a real timestamp means "no date to go on", which
    is exactly what None already means here — a 500 would be the wrong answer to a trip
    that simply has no date.
    """
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _vessel_normalize(pg, text_value):
    """A written name folded to its comparison key — prefix, case and punctuation
    dropped. The DB function is the single definition of that folding (migration 0055),
    so the query side asks it rather than reimplementing it in Python."""
    return pg.execute(
        "SELECT COALESCE(vessel_normalize(:value), '')", {"value": text_value}
    ).scalar()


# Undecorated, like /api/airportAutocomplete and /getAirliners: the vessel register is
# reference data, not anybody's trips. (login_required could not be used here anyway —
# it resolves the caller from a `username` view argument this route has no reason for.)
@app.route("/vesselAutocomplete")
def vesselAutocomplete():
    """
    Vessel suggestions for the ferry `reg` field, matched on name, IMO or MMSI alike.

    `value` is what a trip stores: the hull key — the IMO, or the synthetic trainlog_id
    for a ship that has none. Never a name and never an MMSI, both of which change hands
    when a ship is sold; the hull does not. The name shown for a trip is resolved back
    out of that key at the trip's own date (migration 0056).
    """
    query = (request.args.get("query") or "").strip()
    # The trip's date, when the form has one: the suggestion then names the ship as it
    # was then, which is what the trip will display.
    at = _parse_at(request.args.get("at"))
    if len(query) < 2:
        return jsonify([])

    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT * FROM (
                -- One row per hull, matched against EVERY name it has carried: a ship
                -- searched for as Amorella must be findable even though it now sails as
                -- Mega Victoria. DISTINCT ON collapses a hull that matched on several of
                -- its periods, keeping the closest match.
                SELECT DISTINCT ON (v.uid)
                       v.uid,
                       COALESCE(v.imo, v.trainlog_id) AS hull_key,
                       v.imo,
                       r.mmsi,
                       r.name AS matched_name,
                       r.name_key AS matched_key,
                       cur.name AS current_name,
                       -- The identity in force at :at — the trip's own date, when the
                       -- form knows it. That is the name and flag the trip will show, so
                       -- it is what the suggestion has to offer.
                       COALESCE(per.name, cur.name) AS name,
                       COALESCE(per.country_code, cur.country_code) AS country_code,
                       p.local_image_path,
                       (v.uid = vessel_resolve(:query)) AS is_exact
                FROM vessels v
                JOIN vessel_registrations r ON r.vessel_id = v.uid
                -- What the ship is called now, and what it was called at :at.
                LEFT JOIN vessel_registrations cur ON cur.uid = vessel_identity(v.uid, NULL)
                LEFT JOIN vessel_registrations per
                       ON per.uid = vessel_identity(v.uid, CAST(:at AS timestamp))
                -- A photo of the matched period for preference, so the picture answers
                -- the name that was typed; any of the hull's rather than none.
                LEFT JOIN LATERAL (
                    SELECT sp.local_image_path
                    FROM ship_pictures sp
                    JOIN vessel_registrations a ON a.uid = sp.registration_id
                    WHERE a.vessel_id = v.uid AND sp.local_image_path IS NOT NULL
                    -- The period being offered first, then the one that matched the
                    -- text, then any: a picture of the right ship beats none.
                    ORDER BY (a.uid = COALESCE(per.uid, cur.uid)) DESC,
                             (a.uid = r.uid) DESC,
                             sp.fetch_date DESC NULLS LAST, sp.uid DESC
                    LIMIT 1
                ) p ON TRUE
                WHERE r.name ILIKE :contains
                   -- Matched on the folded key as well, so the ship-type prefix and the
                   -- punctuation stop mattering: 'MS Fjordtroll', 'M/S Fjordtroll' and
                   -- 'Fjordtroll' are one search (vessel_normalize, migration 0055).
                   OR (:normalized <> '' AND r.name_key LIKE '%' || :normalized || '%')
                   OR v.imo LIKE :starts
                   OR r.mmsi LIKE :starts
                   -- Whatever the text resolves to EXACTLY, which is how a reg already
                   -- stored on a trip names its ship. It matters for the synthetic
                   -- trainlog_id: a ship with no IMO has one in every trip that logged
                   -- it, and without this the edit form could not name the ship it was
                   -- showing. Note this matches only on a complete key — typing 'TL00'
                   -- resolves to nothing — so the id is still never *suggested*, which
                   -- is what would invite people to type it.
                   OR v.uid = vessel_resolve(:query)
                ORDER BY v.uid,
                         (r.name_key LIKE :normalized || '%') DESC NULLS LAST,
                         (r.uid = cur.uid) DESC,
                         r.uid
            ) q
            ORDER BY (q.matched_key LIKE :normalized || '%') DESC NULLS LAST,
                     q.matched_name NULLS LAST, q.uid
            LIMIT 15
            """,
            {
                "contains": f"%{query}%",
                "starts": f"{query}%",
                # Folded here rather than in SQL so the empty result (a query of pure
                # punctuation) can be guarded above instead of matching everything.
                "normalized": _vessel_normalize(pg, query),
                "query": query,
                "at": at,
            },
        ).fetchall()

    return jsonify(
        [
            {
                # What a trip stores: the hull key, so the trip survives the ship being
                # renamed or re-flagged (migration 0056).
                "value": row["hull_key"],
                # The name for the trip's date, the one that matched the text, and the
                # one the ship goes by now. They differ once a ship has been renamed, and
                # the field says so rather than silently answering a search for Amorella
                # with "Mega Victoria".
                "name": row["name"],
                "matched_name": row["matched_name"],
                "current_name": row["current_name"],
                "imo": row["imo"],
                "mmsi": row["mmsi"],
                "country": row["country_code"],
                "image": (
                    f"/static/images/ship_pictures/{row['local_image_path']}"
                    if row["local_image_path"]
                    else None
                ),
                "exact": bool(row["is_exact"]),
            }
            for row in rows
        ]
    )


@app.route("/getAirliners")
def getAirliners():
    with pg_session() as pg:
        airliners = [
            dict(row._mapping)
            for row in pg.execute("SELECT * FROM airliners").fetchall()
        ]
    return jsonify(airliners)


@app.route("/admin/airliners/delete", methods=["POST"])
@admin_required
def delete_airliner():
    iata = request.form.get("iata")

    with pg_session() as pg:
        pg.execute("DELETE FROM airliners WHERE iata = :iata", {"iata": iata})

    return jsonify({"success": True})


@app.route("/admin/airliners", methods=["GET", "POST"])
@admin_required
def airliners():
    if request.method == "POST":
        original_iata = request.form.get("original_iata")
        iata = request.form.get("iata")
        manufacturer = request.form.get("manufacturer")
        model = request.form.get("model")

        with pg_session() as pg:
            if original_iata:  # If original_iata is set, it's an update
                pg.execute(
                    """
                    UPDATE airliners
                    SET iata = :iata, manufacturer = :manufacturer, model = :model
                    WHERE iata = :original_iata
                    """,
                    {
                        "iata": iata,
                        "manufacturer": manufacturer,
                        "model": model,
                        "original_iata": original_iata,
                    },
                )
            else:  # Otherwise, it's an insert
                pg.execute(
                    """
                    INSERT INTO airliners (iata, manufacturer, model)
                    VALUES (:iata, :manufacturer, :model)
                    """,
                    {"iata": iata, "manufacturer": manufacturer, "model": model},
                )

        return jsonify({"success": True})

    with pg_session() as pg:
        airlinerList = [
            dict(row._mapping)
            for row in pg.execute("SELECT * FROM airliners").fetchall()
        ]

    return render_template(
        "admin/airliners.html",
        airlinerList=airlinerList,
        username=getUser(),
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/importFlight")
def importFlight(username):
    return render_template(
        "import_flight.html",
        nav="bootstrap/navigation.html",
        username=username,
        title="Import Flight",
    )


@app.route("/u/<username>/upload_image", methods=["POST"])
def upload_image(username):
    if "image" not in request.files:
        return redirect(request.url)

    file = request.files["image"]
    if file.filename == "":
        return redirect(request.url)

    if file:
        # Convert the file to a BytesIO object and read it using OpenCV
        in_memory_file = BytesIO()
        file.save(in_memory_file)
    # return redirect(url_for("new_flight", username=username, **readBarcode(data)))
    return redirect(url_for("new_flight", username=username))


@app.route("/deleteManual/<int:id>", methods=["POST"])
@owner_required
def deleteManual(id):
    with pg_session() as pg:
        pg.execute("DELETE FROM manual_stations WHERE uid = :uid", {"uid": id})
    return redirect(url_for("adminManual"))


@app.route("/u/<username>/manualStations")
@login_required
def userManualStations(username):
    with pg_session() as pg:
        stationsList = [
            dict(row._mapping)
            for row in pg.execute(
                "SELECT * FROM manual_stations WHERE creator = :creator"
                " ORDER BY station_type, name",
                {"creator": username},
            ).fetchall()
        ]
    return render_template(
        "manual_stations.html",
        stationsList=stationsList,
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/manualStations/<int:id>/update", methods=["POST"])
@login_required
def updateUserManualStation(username, id):
    with pg_session() as pg:
        owner_row = pg.execute(
            "SELECT creator FROM manual_stations WHERE uid = :uid", {"uid": id}
        ).fetchone()
        if owner_row is None or owner_row["creator"] != username:
            abort(401)
        pg.execute(
            """
            UPDATE manual_stations
            SET name = :name, lat = :lat, lng = :lng
            WHERE uid = :uid
            """,
            {
                "name": request.form.get("name"),
                "lat": request.form.get("lat"),
                "lng": request.form.get("lng"),
                "uid": id,
            },
        )
    return redirect(url_for("userManualStations", username=username))


@app.route("/u/<username>/manualStations/<int:id>/delete", methods=["POST"])
@login_required
def deleteUserManualStation(username, id):
    with pg_session() as pg:
        owner_row = pg.execute(
            "SELECT creator FROM manual_stations WHERE uid = :uid", {"uid": id}
        ).fetchone()
        if owner_row is None or owner_row["creator"] != username:
            abort(401)
        pg.execute("DELETE FROM manual_stations WHERE uid = :uid", {"uid": id})
    return redirect(url_for("userManualStations", username=username))


@app.route("/editStation/<int:id>", methods=["GET", "POST"])
@admin_required
def editStation(id):
    if request.method == "POST":
        action = request.form.get("action")

        if action == "delete":
            # Delete the station
            with pg_session() as pg:
                pg.execute("DELETE FROM train_stations WHERE id = :id", {"id": id})
            return redirect(url_for("stations"))
        else:
            # Update the station details
            with pg_session() as pg:
                pg.execute(
                    """
                    UPDATE train_stations
                    SET name=:name, latin_name=:latin_name, city=:city, latin_city=:latin_city,
                        country_code=:country_code, latitude=:latitude, longitude=:longitude,
                        processed_name=:processed_name
                    WHERE id=:id
                """,
                    {
                        "name": request.form.get("name"),
                        "latin_name": request.form.get("latin_name"),
                        "city": request.form.get("city"),
                        "latin_city": request.form.get("latin_city"),
                        "country_code": request.form.get("country_code"),
                        "latitude": request.form.get("latitude"),
                        "longitude": request.form.get("longitude"),
                        "processed_name": request.form.get("processed_name"),
                        "id": id,
                    },
                )
            return redirect(url_for("stations"))
    else:
        # Fetch the station details
        with pg_session() as pg:
            station = pg.execute(
                "SELECT * FROM train_stations WHERE id = :id", {"id": id}
            ).fetchone()
        return render_template(
            "admin/edit_station.html",
            station=station,
            username=getUser(),
            nav="bootstrap/navigation.html",
            isCurrent=has_current_trip(get_user_id()),
            **lang[session["userinfo"]["lang"]],
            **session["userinfo"],
        )


@app.route("/stations-data")
@admin_required
def stations_data():
    draw = request.args.get("draw", default=1, type=int)
    start = request.args.get("start", default=0, type=int)
    length = request.args.get("length", default=10, type=int)
    search_value = request.args.get("search[value]", default="", type=str)
    order_column = request.args.get("order[0][column]", type=int)
    order_dir = request.args.get("order[0][dir]", type=str)

    columns = [
        "name",
        "latin_name",
        "city",
        "latin_city",
        "country_code",
        "latitude",
        "longitude",
        "processed_name",
    ]

    # Construct the ORDER BY clause
    order_by_clause = ""
    if order_column is not None and order_dir in ["asc", "desc"]:
        order_by_clause = f"ORDER BY {columns[order_column]} {order_dir}"

    # Construct the WHERE clause for search
    where_clause = ""
    params = {}
    if search_value:
        search_terms = [f"{col} LIKE :search" for col in columns]
        where_clause = f"WHERE {' OR '.join(search_terms)}"
        params["search"] = f"%{search_value}%"

    # Count total records
    with pg_session() as pg:
        total_records = pg.execute("SELECT COUNT(id) FROM train_stations").scalar()

        # Fetch filtered records
        query = (
            f"SELECT * FROM train_stations {where_clause} {order_by_clause} "
            "LIMIT :limit OFFSET :offset"
        )
        stations = pg.execute(
            query, {**params, "limit": length, "offset": start}
        ).fetchall()

        # Count filtered records
        filtered_records = pg.execute(
            f"SELECT COUNT(id) FROM train_stations {where_clause}", params
        ).scalar()

    data = []
    for station in stations:
        data.append(
            {
                "name": station["name"],
                "latin_name": station["latin_name"],
                "city": station["city"],
                "latin_city": station["latin_city"],
                "country_code": station["country_code"],
                "latitude": station["latitude"],
                "longitude": station["longitude"],
                "processed_name": station["processed_name"],
                "actions": f'<a href={url_for("editStation", id=station["id"])} class="btn btn-primary">Edit</a>',
            }
        )

    response = {
        "draw": draw,
        "recordsTotal": total_records,
        "recordsFiltered": filtered_records,
        "data": data,
    }

    return jsonify(response)


@app.route("/stations", methods=["GET"])
@admin_required
def stations():
    return render_template(
        "admin/stations.html",
        username=getUser(),
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/editManual/<int:id>", methods=["GET", "POST"])
@owner_required
def editManual(id):
    with pg_session() as pg:
        if request.method == "POST":
            new_data = {
                "name": request.form.get("name"),
                "lat": request.form.get("lat"),
                "lng": request.form.get("lng"),
                "creator": request.form.get("creator"),
                "station_type": request.form.get("station_type"),
                "id": id,
            }
            pg.execute(
                """
                UPDATE manual_stations
                SET name=:name, lat=:lat, lng=:lng, creator=:creator, station_type=:station_type
                WHERE uid=:id
            """,
                new_data,
            )
            return redirect(url_for("adminManual"))
        else:
            station = pg.execute(
                "SELECT * FROM manual_stations WHERE uid = :uid", {"uid": id}
            ).fetchone()
            return render_template(
                "admin/edit_manual.html",
                station=station,
                username=session.get("logged_in"),
                nav="bootstrap/navigation.html",
                isCurrent=has_current_trip(get_user_id()),
                **lang[session["userinfo"]["lang"]],
                **session["userinfo"],
            )


@app.errorhandler(405)
def handle_405(e):
    logger.error(e)
    log_suspicious_activity(
        request.url,
        "method_not_allowed",
        request.method,
        getIp(request),
        getRequestData(request),
    )
    return "", 405


@app.errorhandler(401)
@app.errorhandler(404)
@app.errorhandler(410)
@app.errorhandler(416)
@app.errorhandler(500)
@app.errorhandler(sqlite3.OperationalError)   # handle db errors
@app.errorhandler(Exception)                  # catch-all
def handle_error(e):
    # Let context processors know to skip DB work
    g.suppress_context_queries = True

    # Decide error code + log
    if isinstance(e, HTTPException):
        error_code = e.code
        user = getUser()
        if 400 <= error_code < 500:
            # Short description for client errors
            short_desc = e.name or "Client Error"
            logger.warning(
                "%s %s (URL: %s, User: %s)",
                error_code, short_desc, request.url, user
            )
        else:
            # Server-side HTTP errors
            logger.error(
                "%s %s (URL: %s, User: %s)",
                error_code, e.name or "Server Error", request.url, user
            )
    elif isinstance(e, sqlite3.OperationalError):
        logger.exception("Unhandled sqlite error", exc_info=e)
        # use 503 for "database is locked", otherwise generic 500
        error_code = 503 if "database is locked" in str(e).lower() else 500
    else:
        logger.exception("Unhandled exception", exc_info=e)
        error_code = 500

    # Report server errors (>=500) to the error collector
    if error_code >= 500:
        report_error(
            subject=f"Error {error_code}: {e}",
            message=traceback.format_exc(),
            url=request.url or "",
            user=str(getUser() or ""),
        )

    # Safe language/session lookups
    userinfo = session.get("userinfo", {}) or {}
    lang_code = userinfo.get("lang", "en")
    lang_dict = lang.get(lang_code, {})

    # Unified language keys
    title_key = f"error{error_code}Title"
    body_key  = f"error{error_code}Body"

    template_data = {
        "errorTitle":  lang_dict.get(title_key, "Error"),
        "errorHeader": lang_dict.get(title_key, "Error"),
        "errorImagePath": url_for("static", filename=f"images/errors/{error_code}.png"),
        "errorBody":   lang_dict.get(body_key, "An error occurred."),
    }

    nav = "bootstrap/no_user_nav.html" if getUser() == "public" else "bootstrap/navigation.html"

    return (
        render_template(
            "errors.html",
            nav=nav,
            username=getUser(),
            **template_data,
            **lang_dict,
            **userinfo,
        ),
        error_code,
    )


@app.route("/<int:error_code>")
def error_route(error_code):
    # Create a new HTTPException instance with the captured error code
    exception = HTTPException()
    exception.code = error_code
    return handle_error(exception)


@app.route("/leaderboard", defaults={"type": "all"})
@app.route("/leaderboard/<type>")
def leaderboard(type):
    if getUser() == "public":
        nav = "bootstrap/no_user_nav.html"
    else:
        nav = "bootstrap/navigation.html"

    if type == "train_countries":
        template = "leaderboard_train_countries.html"
    elif type == "countries":
        template = "leaderboard_countries.html"
    elif type == "world_squares":
        template = "leaderboard_world_squares.html"
    elif type == "carbon":
        template = "leaderboard_carbon.html"
    else:
        template = "leaderboard.html"

    return render_template(
        template,
        nav=nav,
        username=getUser(),
        title=lang[session["userinfo"]["lang"]]["leaderboard"],
        type=type,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/getPublicStats")
def getPublicStats():
    with pg_session() as pg:
        row = pg.execute(
            "SELECT COUNT(trip_id) AS trips, CAST(SUM(trip_length) / 1000 AS INT) AS km FROM trips"
        ).fetchone()
    stats = dict(row._mapping)
    stats["users"] = len(User.query.all())
    return jsonify(stats)


@app.route("/getVesselPhoto")
def getVesselPhoto():
    """
    A ship's photo and identity, as of `at` — the date of the trip asking.

    Without `at` the answer is the ship as it is now, which is right for the live map and
    wrong for a trip in the past: a 2017 crossing must read the name and flag the ship
    carried in 2017 (migration 0056).
    """
    vesselName = request.args.get("vesselName")
    at = _parse_at(request.args.get("at"))
    with pg_session() as pg:
        result = get_vessel_picture(vesselName, pg, at)
    return jsonify(result)


@app.route("/password_reset_request", methods=["GET", "POST"])
def password_reset_request():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        user = User.query.filter_by(email=email).first()
        if user:
            reset_token = secrets.token_hex(32)
            user.reset_token = reset_token
            authDb.session.commit()
            link = url_for("password_reset", token=reset_token, _external=True)
            emailBody = "{} : <a href={}>{}</a>".format(
                lang[session["userinfo"]["lang"]]["passwordResetLinkText"], link, link
            )
            sendEmail(
                email,
                lang[session["userinfo"]["lang"]]["passwordRequestEmailSubject"],
                emailBody,
            )
            flash(lang[session["userinfo"]["lang"]]["passwordRequested"])
        else:
            flash(lang[session["userinfo"]["lang"]]["noAccountWithEmail"])
            return redirect(url_for("password_reset_request"))
    return render_template(
        "password_reset_request.html",
        title=lang[session["userinfo"]["lang"]]["resetPassword"],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/password_reset/<token>", methods=["GET", "POST"])
def password_reset(token):
    user = User.query.filter_by(reset_token=token).first()
    if not user:
        flash(lang[session["userinfo"]["lang"]]["invalidOrExpiredToken"])

    if request.method == "POST":
        password = request.form["password"].strip()
        user.pass_hash = generate_password_hash(password, "scrypt")
        user.reset_token = None
        authDb.session.commit()
        flash(
            lang[session["userinfo"]["lang"]]["passwordUpdated"]
            + Markup(
                " <a href={}>{}</a>".format(
                    url_for("login"), lang[session["userinfo"]["lang"]]["login"]
                )
            )
        )

    return render_template(
        "password_reset.html",
        title=lang[session["userinfo"]["lang"]]["enterNewPassword"],
        token=token,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


def week_to_date(year_week_str, day_of_week=0):
    """
    Convert a year-week string to a date object representing a specific day of that week.
    year_week_str: A string in the format 'YYYY-WW'.
    day_of_week: The day of the week you want (0 for Monday, 1 for Tuesday, ..., 6 for Sunday).
                 Default is 0 (Monday).
    """
    year, week = map(int, year_week_str.split("-"))
    first_day_of_year = datetime(year, 1, 1)
    # Days to add to get to the first Monday of the year
    days_to_first_monday = (7 - first_day_of_year.weekday()) % 7
    first_monday_of_year = first_day_of_year + timedelta(days=days_to_first_monday)
    # Calculate the specific day in the specified week
    specific_day = first_monday_of_year + timedelta(weeks=week - 1, days=day_of_week)
    return specific_day


@app.route("/admin/trip_growth")
@owner_required
def admin_trip_growth():
    group_by = request.args.get("group_by", "month")
    today = datetime.today()

    # Define the date format based on the interval
    date_format = {"year": "%Y", "month": "%Y-%m", "week": "%Y-%W", "day": "%Y-%m-%d"}

    # PG to_char format matching group_by
    group_by_format = {
        "year": "YYYY",
        "month": "YYYY-MM",
        "week": "IYYY-IW",
        "day": "YYYY-MM-DD",
    }[group_by]

    with pg_session() as pg:
        # Fetch and count all trips by type, grouping "poi", "restaurant", and "accommodation" under "poi"
        trip_type_counts = pg.execute("""
            SELECT
                CASE
                    WHEN trip_type IN ('poi', 'restaurant', 'accommodation') THEN 'poi'
                    ELSE trip_type
                END as grouped_type,
                COUNT(*) as count
            FROM
                trips
            GROUP BY
                grouped_type
            ORDER BY
                count DESC;
        """).fetchall()
        trip_types = [row[0] for row in trip_type_counts]  # Sorted types

        # Fetch trips data grouped by the selected interval and type
        trip_results = pg.execute(f"""
            SELECT
                to_char(created, '{group_by_format}') as date,
                CASE
                    WHEN trip_type IN ('poi', 'restaurant', 'accommodation') THEN 'poi'
                    ELSE trip_type
                END as grouped_type,
                COUNT(*) as count
            FROM
                trips
            WHERE created IS NOT NULL
            GROUP BY
                date, grouped_type
            ORDER BY
                date;
        """).fetchall()

        # Fetch trips with 'None' created date and count them by grouped type
        trips_with_no_date = pg.execute("""
            SELECT
                CASE
                    WHEN trip_type IN ('poi', 'restaurant', 'accommodation') THEN 'poi'
                    ELSE trip_type
                END as grouped_type,
                COUNT(*) as count
            FROM
                trips
            WHERE created IS NULL
            GROUP BY
                grouped_type;
        """).fetchall()

    # Initialize a dictionary to hold data by date
    trip_data = {}
    for date, grouped_type, count in trip_results:
        if date not in trip_data:
            trip_data[date] = {}
        trip_data[date][grouped_type] = count

    # Get start and end dates from the data, handling each group_by option correctly
    if trip_data:
        first_key = list(trip_data.keys())[0]
        last_key = list(trip_data.keys())[-1]

        if group_by == "year":
            # Parse years directly from the keys
            start_date = datetime.strptime(first_key, "%Y")
            end_date = datetime.strptime(last_key, "%Y")
            # Adjust to cover the full year range
            start_date = datetime(start_date.year, 1, 1)
            end_date = datetime(end_date.year, 12, 31)

        elif group_by == "month":
            # Parse years and months directly
            start_date = datetime.strptime(first_key, "%Y-%m")
            end_date = datetime.strptime(last_key, "%Y-%m")
            # Adjust to cover the full month range
            start_date = datetime(start_date.year, start_date.month, 1)
            end_date = (
                datetime(end_date.year, end_date.month, 1) + timedelta(days=31)
            ).replace(day=1) - timedelta(days=1)

        elif group_by == "week":
            # Parse ISO year and week number
            first_year, first_week_number = map(int, first_key.split("-"))
            last_year, last_week_number = map(int, last_key.split("-"))
            # Start at the Monday of the first ISO week
            start_date = datetime.strptime(
                f"{first_year} {first_week_number} 1", "%G %V %u"
            )
            # End at the Sunday of the last ISO week
            end_date = datetime.strptime(
                f"{last_year} {last_week_number} 7", "%G %V %u"
            )

        else:  # group_by == "day"
            # Directly parse the days
            start_date = datetime.strptime(first_key, "%Y-%m-%d")
            end_date = datetime.strptime(last_key, "%Y-%m-%d")
    else:
        start_date = today
        end_date = today

    # Create a dictionary to include all required date intervals and initialize counts
    date_dict = {}
    current_date = start_date
    while current_date <= end_date:
        date_key = current_date.strftime(date_format[group_by])
        # Initialize each date with zero counts for all dynamic trip types
        date_dict[date_key] = {t: 0 for t in trip_types}
        if date_key in trip_data:
            date_dict[date_key].update(trip_data[date_key])

        # Increment the date based on the interval
        if group_by == "year":
            current_date = datetime(current_date.year + 1, 1, 1)
        elif group_by == "month":
            if current_date.month == 12:
                current_date = datetime(current_date.year + 1, 1, 1)
            else:
                current_date = datetime(current_date.year, current_date.month + 1, 1)
        elif group_by == "week":
            current_date += timedelta(weeks=1)
        else:  # day
            current_date += timedelta(days=1)

    # Assign trips with 'None' created dates to the earliest date
    earliest_date_key = list(date_dict.keys())[0]
    for grouped_type, count in trips_with_no_date:
        if grouped_type in date_dict[earliest_date_key]:
            date_dict[earliest_date_key][grouped_type] += count

    labels = list(date_dict.keys())
    data_points = {
        t: [date_dict[date].get(t, 0) for date in labels] for t in trip_types
    }

    interval_name = {
        "day": "Daily",
        "week": "Weekly",
        "year": "Yearly",
        "month": "Monthly",
    }

    return render_template(
        "admin/trip_growth.html",
        labels=labels,
        data_points=data_points,
        trip_types=trip_types,
        username=getUser(),
        interval=interval_name[group_by],
        title="Trip Growth",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/user_growth")
@owner_required
def admin_user_growth():
    group_by = request.args.get("group_by", "month")
    today = datetime.today()

    # Define the date format and group function
    date_format = {"year": "%Y", "month": "%Y-%m", "week": "%Y-%W", "day": "%Y-%m-%d"}
    group_func = {
        "year": sqlalchemy.func.strftime("%Y", User.creation_date),
        "month": sqlalchemy.func.strftime("%Y-%m", User.creation_date),
        "week": sqlalchemy.func.strftime("%Y-%W", User.creation_date),
        "day": sqlalchemy.func.date(User.creation_date),
    }

    with pg_session() as pg:
        users_with_trips = [
            r[0] for r in pg.execute("SELECT DISTINCT user_id FROM trips").fetchall()
        ]

    results = (
        User.query.with_entities(
            group_func[group_by].label("date"),
            func.count(
                case(
                    [
                        (
                            and_(
                                User.last_login >= today - timedelta(days=90),
                                User.uid.in_(users_with_trips),
                            ),
                            1,
                        )
                    ],
                    else_=None,
                )
            ).label("active_users"),
            func.count(
                case(
                    [
                        (
                            or_(
                                User.last_login.is_(None),
                                User.last_login < today - timedelta(days=90),
                                User.uid.notin_(users_with_trips),
                                User.username == "demo",
                                User.username == "test",
                            ),
                            1,
                        )
                    ],
                    else_=None,
                )
            ).label("inactive_users"),
        )
        .group_by("date")
        .order_by("date")
        .all()
    )

    if group_by == "week":
        results = [
            {
                "date": week_to_date(row.date),
                "inactive_users": row.inactive_users,
                "active_users": row.active_users,
            }
            for row in results
        ]
    else:
        results = [
            {
                "date": datetime.strptime(row.date, date_format[group_by]),
                "inactive_users": row.inactive_users,
                "active_users": row.active_users,
            }
            for row in results
        ]

    # Find the date range
    start_date = results[0]["date"] if results else datetime.today().date()
    end_date = results[-1]["date"] if results else datetime.today().date()

    # Initialize date_dict
    date_dict = {}
    current_date = start_date

    while current_date <= end_date:
        date_key = current_date.strftime(date_format[group_by])
        date_dict[date_key] = (0, 0, 0)  # active_users, inactive_users, extrapolated
        if group_by == "year":
            current_date = datetime(current_date.year + 1, 1, 1)
        elif group_by == "month":
            if current_date.month == 12:
                current_date = datetime(current_date.year + 1, 1, 1)
            else:
                current_date = datetime(current_date.year, current_date.month + 1, 1)
        elif group_by == "week":
            current_date += timedelta(weeks=1)
        else:  # day
            current_date += timedelta(days=1)

    # Update date_dict with actual user counts
    for row in results:
        date_key = row["date"].strftime(date_format[group_by])
        date_dict[date_key] = (row["active_users"], row["inactive_users"], 0)

    # Calculate extrapolated values for the last period
    last_period_key = list(date_dict.keys())[-1]
    if group_by == "week":
        year, week = map(int, last_period_key.split("-"))
        start_of_week = datetime.strptime(f"{year}-W{week}-1", "%Y-W%W-%w")
        print(start_of_week)
        past_days_in_period = (today - start_of_week).days + 1
        print(past_days_in_period)
        total_days_in_period = 7
    elif group_by == "month":
        past_days_in_period = (
            today - datetime.strptime(last_period_key, date_format[group_by])
        ).days + 1
        total_days_in_period = calendar.monthrange(today.year, today.month)[1]
    elif group_by == "year":
        past_days_in_period = (
            today - datetime.strptime(last_period_key, date_format[group_by])
        ).days + 1
        total_days_in_period = 366 if calendar.isleap(today.year) else 365
    else:  # day
        past_days_in_period = 1
        total_days_in_period = 1

    active_users, inactive_users, _ = date_dict[last_period_key]
    total_users = active_users + inactive_users
    extrapolated_users = (
        int((total_users / past_days_in_period) * total_days_in_period) - total_users
    )

    date_dict[last_period_key] = (active_users, inactive_users, extrapolated_users)

    labels = list(date_dict.keys())
    data_points = list(date_dict.values())

    interval_name = {
        "day": "Daily",
        "week": "Weekly",
        "year": "Yearly",
        "month": "Monthly",
    }

    return render_template(
        "admin/user_growth.html",
        labels=labels,
        data_points=data_points,
        username=getUser(),
        interval=interval_name[group_by],
        title="User growth",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )



@app.route("/u/<username>/friends")
@login_required
def friends(username):
    user_id = User.query.filter_by(username=username).first().uid

    outgoing_requests = (
        authDb.session.query(User.uid, User.username)
        .join(Friendship, User.uid == Friendship.friend_id)
        .filter(Friendship.user_id == user_id, Friendship.accepted.is_(None))
        .all()
    )
    current_friends = (
        authDb.session.query(User.uid, User.username)
        .join(Friendship, User.uid == Friendship.friend_id)
        .filter(Friendship.user_id == user_id, Friendship.accepted != None)  # noqa: E711
        .all()
    )
    incoming_requests = (
        authDb.session.query(User.uid, User.username)
        .join(Friendship, User.uid == Friendship.user_id)
        .filter(Friendship.friend_id == user_id, Friendship.accepted.is_(None))
        .all()
    )
    unavailable_users = outgoing_requests + current_friends + incoming_requests
    available_users = [
        (user.uid, user.username)
        for user in User.query.filter_by(friend_search=True).all()
        if user.username != username
        and (user.uid, user.username) not in unavailable_users
    ]

    return render_template(
        "friends.html",
        nav="bootstrap/navigation.html",
        available_users=available_users,
        outgoing_requests=outgoing_requests,
        incoming_requests=incoming_requests,
        current_friends=current_friends,
        username=username,
        title=lang[session["userinfo"]["lang"]]["friends"],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/u/<username>/cancelFriendship/<int:friendId>", methods=["GET"])
@login_required
def cancelFriendship(username, friendId):
    user_id = User.query.filter_by(username=username).first().uid

    # Look for all existing friendships and friend requests, regardless of who initiated it
    friendships = Friendship.query.filter(
        ((Friendship.user_id == user_id) & (Friendship.friend_id == friendId))
        | ((Friendship.user_id == friendId) & (Friendship.friend_id == user_id))
    ).all()

    if not friendships:
        flash(lang[session["userinfo"]["lang"]]["friendNoFriendshipFound"], "danger")
        return redirect(url_for("friends", username=username))

    # If any friendships or requests exist, delete them all
    for friendship in friendships:
        authDb.session.delete(friendship)
    authDb.session.commit()

    if len(friendships) > 1:
        flash(lang[session["userinfo"]["lang"]]["friendFriendshipCanceled"], "success")
    else:
        flash(lang[session["userinfo"]["lang"]]["friendRequestCanceled"], "success")

    return redirect(url_for("friends", username=username))


@app.route("/u/<username>/acceptFriendship/<int:friendId>", methods=["GET"])
@login_required
def acceptFriendship(username, friendId):
    user_id = User.query.filter_by(username=username).first().uid

    # Look for the existing friendship request directed to the user
    friendship = Friendship.query.filter(
        (Friendship.user_id == friendId)
        & (Friendship.friend_id == user_id)
        & (Friendship.accepted == None)  # Ensure it's a pending request  # noqa: E711
    ).first()

    if not friendship:
        flash(lang[session["userinfo"]["lang"]]["friendNoFriendshipFound"], "danger")
        return redirect(url_for("friends", username=username))

    # If a pending friendship request exists, accept it by setting the current date in the accepted column
    friendship.accepted = datetime.now(UTC)

    # Create the reciprocal friendship record
    reciprocal_friendship = Friendship(
        user_id=user_id,  # The current user becomes the 'user_id'
        friend_id=friendId,  # The friend becomes the 'friend_id'
        accepted=datetime.now(UTC),  # Set the accepted date to now
    )
    authDb.session.add(reciprocal_friendship)

    authDb.session.commit()

    flash(
        lang[session["userinfo"]["lang"]]["friendFriendshipAccepted"], "success"
    )  # Ensure you have defined this message
    return redirect(url_for("friends", username=username))


@app.route("/u/<username>/requestFriend/<friendId>", methods=["GET"])
@login_required
def requestFriend(username, friendId):
    user = User.query.filter_by(username=username).first()
    try:
        friendId = int(friendId)
    except ValueError:
        flash(lang[session["userinfo"]["lang"]]["friendInvalidId"], "danger")
        return redirect(url_for("friends", username=username))

    if user.uid == friendId:
        flash(lang[session["userinfo"]["lang"]]["friendRequestToSelf"], "warning")
        return redirect(url_for("friends", username=username))

    friend = User.query.filter_by(uid=friendId).first()
    if not friend:
        flash(lang[session["userinfo"]["lang"]]["friendTargetNotExist"], "danger")
        return redirect(url_for("friends", username=username))

    if not friend.friend_search:
        flash(lang[session["userinfo"]["lang"]]["friendNotAuthorized"], "danger")
        return redirect(url_for("friends", username=username))

    existing_request = Friendship.query.filter(
        ((Friendship.user_id == user.uid) & (Friendship.friend_id == friendId))
        | ((Friendship.user_id == friendId) & (Friendship.friend_id == user.uid))
    ).first()

    if existing_request:
        message_key = (
            "friendAlreadyFriends"
            if existing_request.accepted
            else "friendRequestPending"
        )
        flash(lang[session["userinfo"]["lang"]][message_key], "info")
        return redirect(url_for("friends", username=username))

    new_request = Friendship(user_id=user.uid, friend_id=friendId)
    authDb.session.add(new_request)
    authDb.session.commit()

    flash(lang[session["userinfo"]["lang"]]["friendRequestSent"], "success")
    return redirect(url_for("friends", username=username))


def getFriendsRequestsNumber():
    user = User.query.filter_by(username=getUser()).first()
    if user is None:
        return ""
    user_id = user.uid
    incoming_requests = (
        authDb.session.query(User.uid, User.username)
        .join(Friendship, User.uid == Friendship.user_id)
        .filter(Friendship.friend_id == user_id, Friendship.accepted.is_(None))
        .all()
    )
    if len(incoming_requests) == 0:
        return ""
    elif len(incoming_requests) < 10:
        return f'<i class="incoming-request-number bi bi-{len(incoming_requests)}-circle-fill"></i>'
    else:
        return '<i class="incoming-request-number bi bi-plus-circle-fill"></i>'


def getTagInvitesNumber():
    username = getUser()
    if not username:
        return ""
    with pg_session() as pg:
        count = pg.execute(
            "SELECT COUNT(*) AS n FROM tag_members"
            " WHERE username = :username AND status = 'pending'",
            {"username": username},
        ).fetchone()["n"]
    if count == 0:
        return ""
    elif count < 10:
        return f'<i class="incoming-request-number bi bi-{count}-circle-fill"></i>'
    else:
        return '<i class="incoming-request-number bi bi-plus-circle-fill"></i>'


def getStalePremiumCount():
    """Owner-only: count of premium users whose BMC cancellation was flagged
    (see flag_pending_cancellation) and whose paid-through period has already
    passed — i.e. overdue for a manual /toggle_role revoke."""
    if not session.get("userinfo", {}).get("is_owner"):
        return ""
    # Same naive-vs-aware caveat as row["premium_stale"] in getAdminUsersData —
    # premium_cancel_at round-trips through SQLite as naive-but-UTC-valued.
    count = User.query.filter(
        User.premium_cancel_at.isnot(None),
        User.premium_cancel_at <= datetime.now(UTC).replace(tzinfo=None),
    ).count()
    if count == 0:
        return ""
    elif count < 10:
        return f'<i class="incoming-request-number bi bi-{count}-circle-fill"></i>'
    else:
        return '<i class="incoming-request-number bi bi-plus-circle-fill"></i>'


app.jinja_env.globals.update(getFriendsRequestsNumber=getFriendsRequestsNumber)
app.jinja_env.globals.update(getTagInvitesNumber=getTagInvitesNumber)
app.jinja_env.globals.update(getStalePremiumCount=getStalePremiumCount)


@app.route("/admin/refreshCurrency", methods=["GET"])
@owner_required
def refreshCurrency():
    return run_currency_update()


@app.route("/ship_route", methods=["POST"])
def calculate_route():
    data = request.json
    waypoints = data["waypoints"]  # Array of waypoints

    print(waypoints)

    # Calculate the shortest path for each segment
    route_segments = []
    total_length = 0

    for i in range(len(waypoints) - 1):
        output = marnet_geograph.get_shortest_path(
            origin_node={"latitude": waypoints[i][0], "longitude": waypoints[i][1]},
            destination_node={
                "latitude": waypoints[i + 1][0],
                "longitude": waypoints[i + 1][1],
            },
            output_units="m",
        )
        total_length += output["length"]
        route_segments.extend(output["coordinate_path"])

    # Remove duplicates from the route segments
    route_segments = [
        route_segments[i]
        for i in range(len(route_segments))
        if i == 0 or route_segments[i] != route_segments[i - 1]
    ]

    return jsonify(route=route_segments, length=total_length)


@app.route("/resize_image/<int:max_width>/<int:target_height>")
def resize_image(max_width, target_height):
    static_dir = os.path.abspath("static")
    image_path = os.path.abspath(os.path.join("static", request.args.get("image_path", "").replace("%26", "&")))
    if not image_path.startswith(static_dir + os.sep):
        return ("Forbidden", 403)

    # Create the resized images directory if it doesn't exist
    resized_dir = os.path.join("static/images/resized", f"{max_width}x{target_height}")
    if not os.path.exists(resized_dir):
        os.makedirs(resized_dir)

    # Generate the path for the resized image
    resized_image_path = os.path.join(resized_dir, os.path.basename(image_path))

    # Check if the resized image already exists and is up-to-date
    if os.path.exists(resized_image_path):
        original_mtime = os.path.getmtime(image_path)
        resized_mtime = os.path.getmtime(resized_image_path)
        if resized_mtime >= original_mtime:
            return send_file(resized_image_path, mimetype="image/png")

    # Resize the image
    with Image.open(image_path) as img:
        original_width, original_height = img.size
        aspect_ratio = original_width / original_height

        # Calculate the new dimensions maintaining the aspect ratio
        new_height = target_height
        new_width = int(new_height * aspect_ratio)

        if new_width > max_width:
            new_width = max_width
            new_height = int(new_width / aspect_ratio)

        # Resize the image
        img = img.resize((new_width, new_height), Image.LANCZOS)

        # Create a transparent background canvas of the target size
        canvas = Image.new("RGBA", (max_width, target_height), (255, 255, 255, 0))

        # Calculate the position to paste the resized image onto the canvas (aligned to the right)
        paste_x = max_width - new_width
        paste_y = (target_height - new_height) // 2

        # Paste the resized image onto the canvas
        canvas.paste(img, (paste_x, paste_y))

        # Save the resized image to the resized images directory
        canvas.save(resized_image_path, "PNG")

        # Also save to an in-memory file for immediate return
        img_io = BytesIO()
        canvas.save(img_io, "PNG")
        img_io.seek(0)

        return send_file(img_io, mimetype="image/png")


@app.route("/u/<username>/visited_squares")
@public_required
def visited_squares(username):
    """Render the template for the visited squares map."""
    if username == getUser():
        nav = "bootstrap/navigation.html"
    else:
        nav = "bootstrap/public_nav.html"

    return render_template(
        "visited_squares.html",
        title="Visited Squares Map",
        username=username,
        nav=nav,
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


@app.route("/u/<username>/visited_squares_data")
@public_required
def visited_squares_data(username):
    """Fetch the GeoJSON data for the visited squares."""
    geojson_data, grid_geojson, land_percentage, air_percentage = generate_visited_squares_geojson(username)
    response = {
        "geojson": geojson_data,
        "grid_geojson": grid_geojson,
        "land_percentage": land_percentage,
        "air_percentage": air_percentage,
    }
    return jsonify(response)  # Return the GeoJSON data and percentage as JSON


@app.route("/admin/active_users")
@admin_required
def active_users():
    with pg_session() as pg:
        rows = pg.execute("""
            SELECT date, number
            FROM daily_active_users
            ORDER BY date
        """).fetchall()

    labels = [str(row[0]) for row in rows]
    values = [row[1] for row in rows]

    # Moving average (10-day)
    def moving_average(data, window=20):
        result = []
        for i in range(len(data)):
            start = max(0, i - window + 1)
            window_data = data[start : i + 1]
            avg = sum(window_data) / len(window_data)
            result.append(round(avg, 2))
        return result

    trendline = moving_average(values, window=20)

    # Average growth based on first and last of trendline
    if len(trendline) >= 2:
        days_span = len(trendline) - 1
        growth = (trendline[-1] - trendline[0]) / days_span
        average_growth = round(growth, 2)
    else:
        average_growth = 0.0

    return render_template(
        "admin/active_users.html",
        labels=labels,
        values=values,
        trendline=trendline,
        average_growth=average_growth,
        username=getUser(),
        title="Active Users",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


def generate_visited_squares_geojson(username):
    land_squares = set()
    air_squares = set()
    visited_squares = {}

    with pg_session() as pg:
        trips = pg.execute(
            """
                SELECT trip_id AS uid, trip_type AS type
                FROM trips
                WHERE user_id = :user_id
                  AND NOT is_project
                  AND (
                        COALESCE(utc_start_datetime, start_datetime) IS NULL
                        OR NOW() > COALESCE(utc_start_datetime, start_datetime)
                      )
            """,
            {"user_id": get_user_id(username)},
        ).fetchall()

        for trip in trips:
            trip_id = trip["uid"]
            trip_type = trip["type"]

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
                            visited_squares[square] = "air"
                            air_squares.add(square)
                    else:
                        if visited_squares.get(square) != "stopped":
                            visited_squares[square] = "passed"
                        land_squares.add(square)
                        air_squares.discard(square)

                    # Override with "stopped" on first/last point
                    if i == 0 or i == len(coordinates) - 1:
                        visited_squares[square] = "stopped"
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
                                visited_squares[inter_square] = "air"
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

    # --- VISITED FEATURES ---
    visited_features = []
    for square, status in visited_squares.items():
        lat, lon = square
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [lon, lat],
                    [lon + 1, lat],
                    [lon + 1, lat + 1],
                    [lon, lat + 1],
                    [lon, lat],
                ]],
            },
            "properties": {"status": status},
        }
        visited_features.append(feature)

    visited_geojson = {
        "type": "FeatureCollection",
        "features": visited_features
    }

    # --- FULL GRID FEATURES (ALL WORLD SQUARES) ---
    grid_features = []

    for lat in range(-90, 90):
        for lon in range(-180, 180):
            grid_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [lon, lat],
                        [lon + 1, lat],
                        [lon + 1, lat + 1],
                        [lon, lat + 1],
                        [lon, lat],
                    ]],
                },
                "properties": {}
            })

    grid_geojson = {
        "type": "FeatureCollection",
        "features": grid_features
    }

    return visited_geojson, grid_geojson, land_percentage, air_percentage

@app.route("/tile/<style>/<x>/<y>/<z>/")
@app.route("/tile/<style>/<x>/<y>/<z>/<r>")
def tiles(style, x, y, z, r="@1x"):
    # Create a unique cache key based on the request parameters
    cache_key = f"{style}_{x}_{y}_{z}_{r}"

    # Try to get the response from cache
    cached_response = cache.get(cache_key)
    if cached_response:
        return cached_response, 200, {"Content-Type": "image/png"}

    config = load_config()
    jawg_key = config.get("jawg", {}).get("api_key", "")
    thunderforest_key = config.get("thunderforest", {}).get("api_key", "")

    # Build URLs with keys (may be empty, which is fine)
    jawg_url = (
        f"https://tile.jawg.io/{style}/{z}/{x}/{y}{r}.png?access-token={jawg_key}"
    )
    thunderforest_url = f"https://tile.thunderforest.com/transport/{z}/{x}/{y}.png?apikey={thunderforest_key}"

    style_map = {
        "jawg-streets": jawg_url,
        "jawg-lagoon": jawg_url,
        "jawg-sunny": jawg_url,
        "jawg-light": jawg_url,
        "jawg-terrain": jawg_url,
        "jawg-dark": jawg_url,
        "thunderforest-transport": thunderforest_url,
    }

    # Fallback for unknown style
    api_url = style_map.get(style)
    if not api_url:
        return "Unknown style", 400

    # Fetch from external API
    response = requests.get(api_url)

    if response.status_code == 200:
        cache.set(cache_key, response.content)
        return response.content, 200, {"Content-Type": "image/png"}
    else:
        return f"Tile not found for style {style}", 404


@app.route("/flag_sprite.png")
def serve_flag_sprite():
    return generate_sprite(app.static_folder)


def get_flag_positions():
    try:
        positions_path = os.path.join(
            app.static_folder, "images/flags/sprite/positions.json"
        )
        with open(positions_path, "r") as file:
            data = json.load(file)
        return data
    except Exception:
        return ""


# Register the function as a Jinja2 global
app.jinja_env.globals.update(getFlagPositions=get_flag_positions)


@app.route("/generate-png/<filename>")
@owner_required
def generate_png(filename):
    try:
        # Generate the image using the separate function
        img_io = generate_image(filename)

        # Return the PNG image as a response
        return send_file(img_io, mimetype="image/png")

    except FileNotFoundError:
        abort(404, description="GeoJSON file not found.")
    except Exception as e:
        abort(500, description=str(e))


# Path to the folder where logo images will be saved
LOGO_UPLOAD_FOLDER = "static/images/operator_logos/new"
REL_LOGO_UPLOAD_FOLDER = "images/operator_logos/new"
ALLOWED_EXTENSIONS = {"png"}


# Utility function to check if the uploaded file is allowed
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# Ensure the upload folder exists
os.makedirs(LOGO_UPLOAD_FOLDER, exist_ok=True)


@app.route("/admin/operators", methods=["GET"])
@admin_required
def show_operators():
    return render_template(
        "admin/operators.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )

@app.route("/migrate-logos")
@owner_required
def migrate_logos():
    """
    Migrate logos from the old system to the new system and insert them into the database with the logo type.
    """
    logo_types = ["operator", "accommodation", "car", "poi"]
    logoURLs = {}

    for logo_type in logo_types:
        # Search for logos in the main directory
        for logo in map(
            os.path.basename, glob(f"static/images/{logo_type}_logos/*.png")
        ):
            logo_name = os.path.splitext(logo)[0]
            logo_name = logo_name.encode("utf-8", "surrogateescape").decode(
                "utf-8", "surrogatepass"
            )
            logoURLs[(logo_name, logo_type)] = f"images/{logo_type}_logos/{logo}"

        # Search for admin_uploaded logos if not already there
        admin_uploaded_path = f"static/images/{logo_type}_logos/admin_uploaded/*.png"
        for logo in map(os.path.basename, glob(admin_uploaded_path)):
            logo_name = os.path.splitext(logo)[0]
            logo_name = logo_name.encode("utf-8", "surrogateescape").decode(
                "utf-8", "surrogatepass"
            )
            if (logo_name, logo_type) not in logoURLs.keys():
                logoURLs[(logo_name, logo_type)] = (
                    f"images/{logo_type}_logos/admin_uploaded/{logo}"
                )

    # Insert each operator and its corresponding logo into the new system
    with pg_session() as pg:
        for (name, logo_type), logo_url in logoURLs.items():
            # Insert into operators table (operator_type stores logo_type)
            operator_id = pg.execute(
                """
                INSERT INTO operators (short_name, long_name, operator_type)
                VALUES (:name, :name, :logo_type)
                RETURNING operator_id
            """,
                {"name": name, "logo_type": logo_type},
            ).fetchone()[0]

            # Insert into operator_logos table
            pg.execute(
                """
                INSERT INTO operator_logos (operator_id, logo_url, effective_date)
                VALUES (:operator_id, :logo_url, NULL)
            """,
                {"operator_id": operator_id, "logo_url": logo_url},
            )

    return "Logos and types migrated successfully"


@app.route("/u/<username>/tll")
@login_required
def trainloglogger(username):
    return render_template(
        "trainloglogger.html",
        title="Trainlog Logger",
        username=username,
        nav="bootstrap/navigation.html",
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


@app.route("/u/<username>/getBounds")
@app.route("/u/<username>/getBounds/<year>")
@login_required
def get_bounds(username, year=None):
    def get_location(lat, lon):
        responseJson = photonRequest(
            "/reverse",
            {"lon": lon, "lat": lat, "lang": "en"},
        )

        if responseJson is not None and responseJson["features"] is not None:
            properties = {}
            if responseJson["features"] != []:
                properties = responseJson["features"][0]["properties"]

            # Extract relevant location details
            country = properties.get("country", "Unknown")
            city = properties.get("city", None)
            county = properties.get("county", None)
            state = properties.get("state", None)
            country_code = properties.get("countrycode", None)

            # Add flag to the country
            flag_country = (
                f"{get_flag_emoji(country_code)} {country}"
                if country_code
                else country
            )

            # Build preferred location string
            if city:
                location = f"{city}, {flag_country}"
            elif county and state:
                location = f"{county}, {state}, {flag_country}"
            elif county or state:
                location = f"{county or state}, {flag_country}"
            else:
                location = flag_country

            # Add OpenStreetMap link
            osm_link = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}"
            return {"location": location, "osm_link": osm_link}
        else:
            return {"location": "Unknown", "osm_link": None}

    # Dictionary to store boundary values
    bounds = {
        "north": {"coordinates": None, "place": None, "trip_id": None},
        "west": {"coordinates": None, "place": None, "trip_id": None},
        "south": {"coordinates": None, "place": None, "trip_id": None},
        "east": {"coordinates": None, "place": None, "trip_id": None},
    }

    with pg_session() as pg:
        # Fetch all (completed) trip IDs for the user
        rows = pg.execute(
            """
            WITH base AS (
                SELECT trips.*,
                    COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime
                FROM trips
                WHERE user_id = :user_id
            )
            SELECT trip_id AS uid, trip_type AS type FROM base
            WHERE NOT is_project
              AND (utc_filtered_start_datetime IS NULL OR NOW() > utc_filtered_start_datetime)
              AND (:year IS NULL OR to_char(utc_filtered_start_datetime, 'YYYY') = :year)
            """,
            {"user_id": get_user_id(username), "year": year},
        ).fetchall()
        trip_ids_with_type = dict((row[0], row[1]) for row in rows)

    if not trip_ids_with_type:
        return jsonify({"error": "No trips found for this user"}), 404

    with pg_session() as pg:
        # Fetch all paths associated with the user's trips
        paths = pg.execute(
            get_user_lines_query(),
            {"ids": [int(t) for t in trip_ids_with_type.keys()]},
        ).fetchall()

    if not paths:
        return jsonify({"error": "No paths found for this user's trips"}), 404

    # Process each path to update the boundary values
    for trip_id, path_row in paths:
        path = json.loads(path_row)  # path is a list of lists with coordinates
        if trip_ids_with_type[trip_id] == "air":
            path = [path[0], path[-1]]  # Only consider start and end points for flights

        for coord in path:
            lat, lon = coord
            # Update bounds with coordinates, place information, and trip_id
            if (
                bounds["north"]["coordinates"] is None
                or lat > bounds["north"]["coordinates"][0]
            ):
                bounds["north"]["coordinates"] = (lat, lon)
                bounds["north"]["trip_id"] = trip_id
            if (
                bounds["west"]["coordinates"] is None
                or lon < bounds["west"]["coordinates"][1]
            ):
                bounds["west"]["coordinates"] = (lat, lon)
                bounds["west"]["trip_id"] = trip_id
            if (
                bounds["south"]["coordinates"] is None
                or lat < bounds["south"]["coordinates"][0]
            ):
                bounds["south"]["coordinates"] = (lat, lon)
                bounds["south"]["trip_id"] = trip_id
            if (
                bounds["east"]["coordinates"] is None
                or lon > bounds["east"]["coordinates"][1]
            ):
                bounds["east"]["coordinates"] = (lat, lon)
                bounds["east"]["trip_id"] = trip_id

    # Fetch place names for each boundary using the stored coordinates
    for direction in bounds:
        coords = bounds[direction]["coordinates"]
        if coords:
            lat, lon = coords
            bounds[direction]["place"] = get_location(lat, lon)

    # Final response
    return jsonify(bounds), 200


@app.route("/u/<username>/bounds")
@app.route("/u/<username>/bounds/<year>")
@login_required
def user_bounds(username, year=None):
    distinctStatYears = get_distinct_stat_years(username, "combined")
    if year is not None and year not in distinctStatYears:
        return redirect(url_for("user_bounds", username=username))

    return render_template(
        "bounds.html",
        title=lang[session["userinfo"]["lang"]]["travel_bounds_header"],
        username=username,
        boundsYear=year,
        distinctStatYears=distinctStatYears,
        translations=lang[session["userinfo"]["lang"]],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/status")
def router_status():
    latest_commit_hex = latest_commit.hexsha
    latest_commit_dt = latest_commit.committed_datetime

    return render_template(
        "status.html",
        title=lang[session["userinfo"]["lang"]]["router_status"],
        username=getUser(),
        translations=lang[session["userinfo"]["lang"]],
        photon_instances=photonInstances,
        latest_commit_hex=latest_commit_hex,
        latest_commit_hex_short=latest_commit_hex[:7],
        latest_commit_display=latest_commit_dt.strftime("%Y-%m-%d %H:%M UTC"),
        latest_commit_ago=time_ago(latest_commit_dt),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


def get_current_trips_data(public_only=True):
    """
    Get current trips data, optionally filtered by public visibility.
    
    Args:
        public_only (bool): If True, only return trips from public users
    
    Returns:
        list: List of trip data with paths and distances
    """
    # 1. Get all trips that are currently in progress
    with pg_session() as pg:
        rows = pg.execute("""
            SELECT trips.*, airliners.manufacturer, airliners.model,
                   v.country_code AS vessel_country, v.vessel_name
            FROM trips
            -- Air trips store the ICAO type code in material_type; airliners carries
            -- the readable manufacturer/model shown in the popup.
            LEFT JOIN airliners ON trips.material_type = airliners.iata
            -- A ship's flag and name live in the register, so surface them here or the
            -- flag could not appear until the photo had been fetched. These trips are
            -- in progress, so the identity in force is the current one (migration 0056).
            LEFT JOIN LATERAL (
                SELECT r.country_code, NULLIF(btrim(r.name), '') AS vessel_name
                FROM vessel_registrations r
                WHERE trips.trip_type = 'ferry'
                  AND r.uid = vessel_identity(vessel_resolve(trips.reg), NULL)
            ) v ON TRUE
            WHERE (utc_start_datetime + COALESCE(departure_delay, 0) * interval '1 second') <= NOW()
              AND (utc_end_datetime + COALESCE(arrival_delay, 0) * interval '1 second') >= NOW()
              AND (visibility = 'public' OR (visibility IS NULL AND trip_type NOT IN ('poi', 'accommodation', 'restaurant', 'walk', 'cycle', 'car')))
        """).fetchall()

    _uname_cache = {}

    def _uname(uid):
        if uid not in _uname_cache:
            _uname_cache[uid] = get_username(uid)
        return _uname_cache[uid]

    trips = [adapt_pg_trip_row(r._mapping, _uname(r._mapping["user_id"])) for r in rows]
    
    if not trips:
        return []
    
    # 2. Filter trips based on visibility requirements
    if public_only:
        # Collect usernames
        usernames = {trip["username"] for trip in trips}
        # Batch fetch public users via SQLAlchemy
        public_users = {
            u.username
            for u in User.query.filter(
                User.username.in_(usernames), User.appear_on_global
            ).all()
        }
        # Filter trips to only public users
        filtered_trips = [trip for trip in trips if trip["username"] in public_users]
    else:
        # Return all trips (for owner/admin access)
        filtered_trips = trips
    
    if not filtered_trips:
        return []
    
    trip_ids = [trip["uid"] for trip in filtered_trips]
    
    # 3. Get paths
    with pg_session() as pg:
        pathResult = pg.execute(
            get_user_lines_query(), {"ids": [int(i) for i in trip_ids]}
        ).fetchall()

    paths = {path["trip_id"]: path["path"] for path in pathResult}

    # Every trip here is in progress by definition, so this is the natural home for live
    # flight tracks: any flight whose owner opted in gets its real flown-so-far path
    # instead of a straight geodesic. Failures are swallowed because a live overlay must
    # never take down the global map. Cost is bounded inside get_live_tracks by a
    # per-trip, globally shared refresh floor.
    try:
        live_tracks = get_live_tracks(trip_ids)
    except Exception:
        app.logger.exception("Live track lookup failed for the current-trips map")
        live_tracks = {}

    result = []
    with pg_session() as pg:
        for trip in filtered_trips:
            path = json.loads(paths.get(trip["uid"], "[]"))
            trip = dict(trip)
            live = live_tracks.get(trip["uid"])
            if live and live.get("path"):
                # Keep the logged endpoints so the client can bridge the track back to
                # the real airports: the not-yet-flown remainder at the end, and at the
                # start the gap left because FR24's public track usually begins at the
                # first airborne contact rather than on the runway.
                trip["live_origin"] = path[0] if path else None
                trip["live_destination"] = path[-1] if path else None
                path = live["path"]
                trip["live_tracked"] = True
            if trip.get("material_type_advanced"):
                trip["trainset"] = public_trainset_info(
                    pg, trip["material_type_advanced"], trip["username"]
                )
            result.append(
                {
                    "username": trip["username"],
                    "trip": trip,
                    "path": path,
                    "distances": getDistanceFromPath(path),
                }
            )

    return result


@app.route("/public/current_trips")
def get_public_current_trips():
    """Get all currently active trips from public users."""
    result = get_current_trips_data(public_only=True)
    return jsonify(result)


@app.route("/admin/current_trips")
@owner_required
def get_all_current_trips():
    """Get all currently active trips (admin/owner access required)."""
    result = get_current_trips_data(public_only=False)
    return jsonify(result)


@app.route("/bestagons")
def bestagons_map():
    """
    Bestagons: experimental deck.gl hexagon view of every trip in the database,
    split into per-trip-type datasets (each hexagon needs >= 3 distinct users).
    """
    username = getUser()
    return render_template(
        "public/bestagons.html",
        username=username,
        points_endpoint=url_for("bestagons_points"),
        datasets=available_bins(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
        title="Bestagons",
    )


@app.route("/admin/rebuild_bestagons", methods=["POST"])
@owner_required
def rebuild_bestagons():
    """Start a background rebuild of all bestagons bins (owner only)."""
    return jsonify({"started": build_all_async()})


@app.route("/admin/bestagons_status")
@owner_required
def bestagons_status():
    return jsonify(build_status())


@app.route("/api/bestagons/points.bin")
def bestagons_points():
    """Serve a pre-aggregated bestagons dataset (?set=land|rail|road|air|type_*).

    Serve-only: building is expensive (minutes) and goes through the admin
    "Rebuild Bestagons" button (or owner ?refresh=1, which starts it async).
    """
    if request.args.get("refresh") == "1" and session.get("userinfo", {}).get("is_owner"):
        build_all_async()
        return jsonify({"building": True}), 202

    path = get_cache_path(name=request.args.get("set", "land"))
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        abort(404)
    return send_file(
        path,
        mimetype="application/octet-stream",
        conditional=True,
        last_modified=os.path.getmtime(path),
    )



@app.route("/live_map")
def live_map():
    """
    Shows the global map of all public users currently traveling (MapLibre)
    """
    username = getUser()
    user = User.query.filter_by(username=username).first() if username else None
    return render_template(
        "public/current_global_maplibre.html",
        username=username,
        logosList=listOperatorsLogos(),
        translations=lang[session["userinfo"]["lang"]],
        api_endpoint=url_for("get_public_current_trips"),
        tileserver=user.tileserver if user else "osm",
        globe=user.globe if user else False,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
        title=lang[session["userinfo"]["lang"]]["live_map"],
    )

@app.route("/live_map/leaflet")
def live_map_leaflet():
    """
    Leaflet fallback for the live map
    """
    username = getUser()
    user = User.query.filter_by(username=username).first() if username else None
    return render_template(
        "public/current_global.html",
        username=username,
        logosList=listOperatorsLogos(),
        translations=lang[session["userinfo"]["lang"]],
        api_endpoint=url_for("get_public_current_trips"),
        tileserver=user.tileserver if user else "osm",
        globe=user.globe if user else False,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
        title=lang[session["userinfo"]["lang"]]["live_map"],
    )


@app.route("/admin/live_map")
@owner_required
def admin_live_map():
    """
    Shows the global map of ALL users currently traveling (admin/owner access)
    """
    username = getUser()
    user = User.query.filter_by(username=username).first() if username else None
    return render_template(
        "public/current_global.html",
        title=f"Admin {lang[session['userinfo']['lang']]['live_map']}",
        username=username,
        logosList=listOperatorsLogos(),
        translations=lang[session["userinfo"]["lang"]],
        api_endpoint=url_for("get_all_current_trips"),
        tileserver=user.tileserver if user else "osm",
        globe=user.globe if user else False,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )

@app.route("/api/user_completion/u/<username>")
@login_required
def user_completion(username):
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT cc, percent
            FROM percents
            WHERE percent > 0 AND username = :username
            ORDER BY cc, percent DESC
            """,
            {"username": username},
        ).fetchall()

    countries = []
    regions = []

    for row in rows:
        entry = {"cc": row["cc"], "percent": row["percent"]}
        if len(row["cc"]) == 2:
            countries.append(entry)
        else:
            regions.append(entry)

    return jsonify({"countries": countries, "regions": regions})

@app.route('/sitemap.xml', methods=['GET'])
def sitemap():
    # Only list the routes you want
    pages = [
        url_for('landing', _external=True),
        url_for('login', _external=True),
        url_for('signup', _external=True),
        url_for('privacy', _external=True),
    ]

    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

    for page in pages:
        xml.append("  <url>")
        xml.append(f"    <loc>{page}</loc>")
        xml.append("  </url>")

    xml.append("</urlset>")
    sitemap_xml = "\n".join(xml)

    response = make_response(sitemap_xml)
    response.headers['Content-Type'] = 'application/xml'
    return response

@app.route("/public/video/<tripIds>")
def video(tripIds):
    if User.query.filter_by(username=getUser()).first().alpha:
        return render_template(
            "video.html",
            tripIds=tripIds,
            **lang[session["userinfo"]["lang"]],
            **session["userinfo"],
        )
    else:
        abort(401)

@app.route("/u/<username>/dashboard")
@login_required
def user_dashboard(username):
    import json
    return render_template(
        "dashboard.html",
        title=lang[session["userinfo"]["lang"]]["user_dashboard"],
        username=username,
        nav="bootstrap/navigation.html",
        dist_comps_json=json.dumps(DISTANCE_COMPARISONS),
        dur_comps_json=json.dumps(DURATION_COMPARISONS),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )

@app.route('/debug/routes')
def list_routes():
    routes = set()
    for rule in app.url_map.iter_rules():
        path = rule.rule
        # Extract first level path segment
        parts = path.strip('/').split('/')
        if parts and parts[0]:
            # Remove parameter markers like <username>
            first_segment = parts[0].split('<')[0].split('>')[0]
            if first_segment:
                routes.add(first_segment)
    
    return '<br>'.join(sorted(routes))

@app.get("/flags/<code>.svg")
def get_flag(code):
    w = request.args.get("w")
    h = request.args.get("h")
    svg = open(f"static/images/flags/{code}.svg").read()
    svg = re.sub(r'<svg([^>]*)>',
                 rf'<svg\1 width="{w}" height="{h}">', svg)

    resp = make_response(svg)
    resp.mimetype = "image/svg+xml"
    return resp


@app.route("/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def user_shortcut(subpath):
    user = getUser()
    qs = request.query_string.decode("latin-1")
    if user != "public":
        target = f"/u/{user}/{subpath}"
        adapter = app.url_map.bind(request.host)
        try:
            endpoint, _ = adapter.match(target, method=request.method)
            if endpoint == "user_shortcut":
                abort(404)
        except RequestRedirect:
            pass
        except (NotFound, MethodNotAllowed):
            abort(404)
        if qs:
            target = f"{target}?{qs}"
        return redirect(target, 307)
    next_url = f"/{subpath}"
    if qs:
        next_url = f"{next_url}?{qs}"
    return redirect(url_for("login") + "?" + urllib.parse.urlencode({"next": next_url}), 302)


def ensure_auth_db_columns():
    """Idempotently add columns to the existing auth.db `user` table.

    authDb.create_all() creates missing tables but never alters existing ones,
    so new columns on the User model need a manual ALTER on the SQLite file."""
    existing = {
        row[1]
        for row in authDb.session.execute(sqlalchemy.text("PRAGMA table_info(user)"))
    }
    if "gps_token" not in existing:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE user ADD COLUMN gps_token VARCHAR(100) DEFAULT ''")
        )
        authDb.session.commit()
    if "mcp_token" not in existing:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE user ADD COLUMN mcp_token VARCHAR(100) DEFAULT ''")
        )
        authDb.session.commit()
    if "live_tracking" not in existing:
        authDb.session.execute(
            sqlalchemy.text(
                "ALTER TABLE user ADD COLUMN live_tracking BOOLEAN NOT NULL DEFAULT 0"
            )
        )
        authDb.session.commit()
    if "discord_id" not in existing:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE user ADD COLUMN discord_id VARCHAR(30)")
        )
        authDb.session.commit()
    if "discord_username" not in existing:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE user ADD COLUMN discord_username VARCHAR(50)")
        )
        authDb.session.commit()
    if "pending_email" not in existing:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE user ADD COLUMN pending_email VARCHAR(100)")
        )
        authDb.session.commit()
    if "email_verify_token" not in existing:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE user ADD COLUMN email_verify_token VARCHAR(100)")
        )
        authDb.session.commit()
    if "premium_tier" not in existing:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE user ADD COLUMN premium_tier VARCHAR(20)")
        )
        authDb.session.commit()
    if "bmc_supporter_id" not in existing:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE user ADD COLUMN bmc_supporter_id VARCHAR(30)")
        )
        authDb.session.commit()
    if "premium_cancel_at" not in existing:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE user ADD COLUMN premium_cancel_at DATETIME")
        )
        authDb.session.commit()

    # Same idempotent-ALTER treatment for pending_bmc_event: create_all() created
    # the table before `tier` existed on the model, so it needs a manual ALTER too.
    existing_pending_bmc_event = {
        row[1]
        for row in authDb.session.execute(
            sqlalchemy.text("PRAGMA table_info(pending_bmc_event)")
        )
    }
    if existing_pending_bmc_event and "tier" not in existing_pending_bmc_event:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE pending_bmc_event ADD COLUMN tier VARCHAR(20)")
        )
        authDb.session.commit()
    if existing_pending_bmc_event and "claim_token" not in existing_pending_bmc_event:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE pending_bmc_event ADD COLUMN claim_token VARCHAR(64)")
        )
        authDb.session.commit()
    if existing_pending_bmc_event and "supporter_id" not in existing_pending_bmc_event:
        authDb.session.execute(
            sqlalchemy.text("ALTER TABLE pending_bmc_event ADD COLUMN supporter_id VARCHAR(30)")
        )
        authDb.session.commit()


with app.app_context():
    if not database_exists(authDb.get_engine().url):
        create_authDb()
    authDb.create_all()
    ensure_auth_db_columns()

setup_db()
