# To not automatically lint the file:
# ruff: noqa

# Standard Library Imports
import calendar
import csv
import json
import logging
import logging.config
import math
import os
import pathlib
import re
import secrets
import smtplib
import traceback
import unicodedata as ud
import urllib
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from functools import wraps
from glob import glob
from inspect import getcallargs
from io import BytesIO, StringIO

import distinctipy
import flask_monitoringdashboard as dashboard
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
)
from flask_caching import Cache
from flask_compress import Compress
from flask_sqlalchemy import SQLAlchemy
from flaskext.autoversion import Autoversion
from geopy.geocoders import Nominatim
from PIL import Image
from requests.adapters import HTTPAdapter, Retry
from scgraph.geographs.marnet import marnet_geograph
from sqlalchemy import and_, case, func, or_
from sqlalchemy_utils import database_exists
from timezonefinder import TimezoneFinder
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# Set the working directory to the app root
# this must be before we try to read the config, or any file
appPath = os.path.realpath(__file__).rsplit("/", 1)[0]
os.chdir(appPath)

# set up logging before local modules are imported
logging.config.fileConfig("logging.conf", disable_existing_loggers=False)
logger = logging.getLogger(__name__)


# Local Application/Library Specific Imports
from py import geopip_country
from py.currency import get_available_currencies, get_exchange_rate
from py.db_init import init_data, init_main
from py.finances import get_finances
from py.g_search import get_vessel_picture
from py.image_generator import generate_image
from py.sql import (
    adminStats,
    countriesLeaderboard,
    deletePathQuery,
    deleteTripQuery,
    deleteUserPath,
    deleteUserTrips,
    distinctStatYears,
    getAirports,
    getCurrentTrip,
    getDuplicate,
    getDynamicUserTrips,
    getLeaderboardCountries,
    getManualStationsQuery,
    getNumberStations,
    getOperators,
    getTags,
    getTicket,
    getTickets,
    getTrainStations,
    getTrip,
    getTripsCountry,
    getUniqueUserTrips,
    getUserLines,
    getUserTrips,
    initPath,
    leaderboardStats,
    newStatsOperatorKm,
    newStatsOperatorTrips,
    publicStats,
    saveQuery,
    statsCountries,
    statsMaterialKm,
    statsMaterialTrips,
    statsRoutesKm,
    statsRoutesTrips,
    statsStationsKm,
    statsStationsTrips,
    statsYearKm,
    statsYearTrips,
    typeAvailable,
    updatePath,
    updateTripQuery,
    upsertPercent,
)
from py.stats import (
    getStatsCountries,
    getStatsGeneral,
    getStatsYears,
)
from py.svg import generate_sprite
from py.track import CustomMatomo
from py.transit_routing import (
    convert_google_response_to_trips,
    convert_here_response_to_trips,
)
from py.update_currency import run_currency_update
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
    load_config,
    remove_diacritics,
    rgb_to_hex,
    stringSimmilarity,
    unicodedata,
    validate_png_file,
)
from src.api.admin import admin_blueprint
from src.api.feature_requests import feature_requests_blueprint
from src.consts import DbNames
from src.pg import setup_db
from src.suspicious_activity import (
    check_denied_login,
    log_denied_login,
    log_suspicious_activity,
)
from src.utils import (
    getNameFromPath,
    processDates,
    getUser,
    isCurrentTrip,
    lang,
    mainConn,
    managed_cursor,
    owner,
    owner_required,
    pathConn,
    readLang,
    sendOwnerEmail,
    sendEmail,
)
from src.trips import (
    Trip,
    create_trip,
    duplicate_trip,
    update_trip,
    _update_trip_in_sqlite,
    delete_trip,
)
from src.paths import Path

app = Flask(__name__)
Compress(app)
app.autoversion = True
Autoversion(app)
app.url_map.strict_slashes = False

app.register_blueprint(admin_blueprint, url_prefix="/admin")
app.register_blueprint(feature_requests_blueprint)

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
    user = getUser()
    if user == "public":
        return "EUR"
    else:
        return User.query.filter_by(username=user).first().user_currency


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
dashboard.config.version = r.git.describe(tags=True).split("-")[0]
dashboard.config.group_by = getUser
dashboard.bind(app)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///{db}".format(db=DbNames.AUTH_DB)
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


authDb = SQLAlchemy(app)


def public_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        inspection = getcallargs(f, *args, **kwargs)
        username = inspection["username"]
        user = User.query.filter_by(username=username).first()

        friends = (
            authDb.session.query(User.uid, User.username)
            .join(Friendship, User.uid == Friendship.friend_id)
            .filter(Friendship.user_id == user.uid, Friendship.accepted != None)  # noqa: E711
            .all()
        )
        friendsList = [username for (uid, username) in friends]
        if user is None:
            abort(404)
        elif (
            not user.is_public()
            and not session.get(owner)
            and username != getUser()
            and getUser() not in friendsList
        ):
            abort(401)
        else:
            return f(*args, **kwargs)

    return decorated_function


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        inspection = getcallargs(f, *args, **kwargs)
        username = inspection["username"]
        user = User.query.filter_by(username=username).first()

        if not session.get("logged_in"):
            return redirect(url_for("login"))
        elif user is None:
            abort(404)
        elif not (session.get(username) or session.get(owner)):
            abort(401)

        user.last_login = datetime.utcnow()
        authDb.session.commit()
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = User.query.filter_by(username=session.get("logged_in")).first()
        if not ((user and user.admin) or session.get(owner)):
            abort(401)
        return f(*args, **kwargs)

    return decorated_function


def translator_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = User.query.filter_by(username=session.get("logged_in")).first()
        if not ((user and user.translator) or session.get(owner)):
            abort(401)
        return f(*args, **kwargs)

    return decorated_function


def check_and_increment_fr24_usage(username, limit=5):
    month_key = datetime.utcnow().strftime("%Y-%m")

    is_premium = bool(User.query.filter_by(username=username).first().premium)

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            SELECT fr24_calls FROM fr24_usage
            WHERE username = ? AND month_year = ?
        """,
            (username, month_key),
        )
        row = cursor.fetchone()

        if row:
            fr24_calls = row[0]
        else:
            fr24_calls = 0
            cursor.execute(
                """
                INSERT INTO fr24_usage (username, month_year, fr24_calls)
                VALUES (?, ?, 0)
                ON CONFLICT DO NOTHING
            """,
                (username, month_key),
            )

        # Increment usage
        cursor.execute(
            """
            UPDATE fr24_usage
            SET fr24_calls = fr24_calls + 1
            WHERE username = ? AND month_year = ?
        """,
            (username, month_key),
        )
        mainConn.commit()

    # Only apply limit if user is not premium
    if not is_premium and fr24_calls >= limit:
        return False

    return True


def fr24_usage(username):
    if User.query.filter_by(username=username).first().premium:
        return "premium"

    month_key = datetime.utcnow().strftime("%Y-%m")

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            SELECT fr24_calls
            FROM fr24_usage
            WHERE username = ? AND month_year = ?
        """,
            (username, month_key),
        )
        row = cursor.fetchone() or (0,)
    return row[0]


@app.errorhandler(Exception)
def all_exception_handler(error):
    logger.exception(error)

    # send an email to the owner if not running the app locally
    if not "127.0.0.1" in request.url and not "localhost" in request.url:
        trace = traceback.format_exc().replace("\n", "<br>")
        msg = "URL : {} <br><br> Logged in user : {}<br><br> Trace : <br><br>{}".format(
            request.url, getUser(), trace
        )
        sendOwnerEmail("Error : " + str(error), msg)


class User(authDb.Model):
    uid = authDb.Column(authDb.Integer, primary_key=True)
    username = authDb.Column(authDb.String(100), unique=True, nullable=False)
    email = authDb.Column(authDb.String(100), unique=True, nullable=False)
    pass_hash = authDb.Column(authDb.String(100), nullable=False)
    lang = authDb.Column(authDb.String(2), nullable=False, default="en")
    share_level = authDb.Column(authDb.Integer, nullable=False, default=0)
    leaderboard = authDb.Column(authDb.Boolean, nullable=False, default=False)
    creation_date = authDb.Column(
        authDb.DateTime, nullable=False, default=datetime.utcnow
    )
    last_login = authDb.Column(authDb.DateTime, nullable=False, default=datetime.utcnow)
    admin = authDb.Column(authDb.Boolean, nullable=False, default=False)
    alpha = authDb.Column(authDb.Boolean, nullable=False, default=False)
    translator = authDb.Column(authDb.Boolean, nullable=False, default=False)
    user_currency = authDb.Column(authDb.String(3), nullable=False, default="EUR")
    friend_search = authDb.Column(authDb.Boolean, nullable=False, default=True)
    reset_token = authDb.Column(authDb.String(100), default="")
    default_landing = authDb.Column(authDb.String(20), nullable=False, default="map")
    appear_on_global = authDb.Column(authDb.Boolean, nullable=False, default=False)
    tileserver = authDb.Column(authDb.String(50), nullable=False, default="default")
    globe = authDb.Column(authDb.Boolean, nullable=False, default=False)
    premium = authDb.Column(authDb.Boolean, nullable=False, default=False)

    def toDict(self):
        return {
            "uid": self.uid,
            "username": self.username,
            "email": self.email,
            "lang": self.lang,
            "leaderboard": self.leaderboard,
            "admin": self.admin,
            "alpha": self.alpha,
            "translator": self.translator,
            "creation_date": self.creation_date,
            "last_login": self.last_login,
            "reset_token": self.reset_token,
            "share_level": self.share_level,
            "user_currency": self.user_currency,
            "tileserver": self.tileserver,
            "globe": self.globe,
            "premium": self.premium,
        }

    def is_public(self):
        return True if self.share_level >= 2 else False

    def is_public_trips(self):
        return True if self.share_level >= 1 else False


class Friendship(authDb.Model):
    __tablename__ = "friendship"
    id = authDb.Column(authDb.Integer, primary_key=True)
    user_id = authDb.Column(
        authDb.Integer, authDb.ForeignKey("user.uid"), nullable=False
    )
    friend_id = authDb.Column(
        authDb.Integer, authDb.ForeignKey("user.uid"), nullable=False
    )
    created_at = authDb.Column(authDb.DateTime, nullable=False, default=datetime.utcnow)
    accepted = authDb.Column(authDb.DateTime, default=None)

    # Relationships
    user = authDb.relationship("User", foreign_keys=[user_id], backref="user_friends")
    friend = authDb.relationship(
        "User", foreign_keys=[friend_id], backref="friend_users"
    )


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

    with managed_cursor(mainConn) as cursor:
        for f in flights:
            orig_icao = f.get("orig_icao")
            dest_icao = f.get("dest_icao")
            takeoff_str = f.get("datetime_takeoff")
            landing_str = f.get("datetime_landed")

            if orig_icao and takeoff_str:
                cursor.execute(
                    "SELECT latitude, longitude FROM airports WHERE ident = :icao",
                    {"icao": orig_icao},
                )
                orig_coords = cursor.fetchone()
                if orig_coords:
                    try:
                        utc_takeoff = datetime.fromisoformat(
                            takeoff_str.replace("Z", "+00:00")
                        )
                        local_takeoff = getLocalDatetime(
                            orig_coords[0], orig_coords[1], utc_takeoff
                        )

                        if local_takeoff.date() == target_date:
                            f["datetime_takeoff_local"] = local_takeoff.isoformat()

                            if dest_icao and landing_str:
                                cursor.execute(
                                    "SELECT latitude, longitude FROM airports WHERE ident = :icao",
                                    {"icao": dest_icao},
                                )
                                dest_coords = cursor.fetchone()
                                if dest_coords:
                                    utc_landing = datetime.fromisoformat(
                                        landing_str.replace("Z", "+00:00")
                                    )
                                    local_landing = getLocalDatetime(
                                        dest_coords[0], dest_coords[1], utc_landing
                                    )
                                    f["datetime_landed_local"] = (
                                        local_landing.isoformat()
                                    )

                            filtered.append(f)
                    except Exception:
                        pass
    return {"data": filtered}, 200


@app.route("/api/<username>/flight_summary")
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


@app.route("/api/<username>/flight_summary_reg")
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


@app.route("/api/<username>/flight_tracks/<fr24_id>")
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

    # Extract lat/lon coordinates
    if not data or "tracks" not in data[0]:
        return jsonify({"error": "No track data found"}), 404

    coordinates = [
        [track["lat"], track["lon"]]
        for track in data[0]["tracks"]
        if "lat" in track and "lon" in track
    ]
    coordinates = interpolate_points_if_gaps(coordinates, 50)

    return jsonify(coordinates)


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
    session["userinfo"]["is_alpha"] = True if user and user.alpha else False
    session["userinfo"]["is_premium"] = True if user and user.premium else False
    session["userinfo"]["is_admin"] = True if user and user.admin else False
    session["userinfo"]["is_translator"] = True if user and user.translator else False
    session["userinfo"]["available_languages"] = available_languages
    session["userinfo"]["lang"] = langToSet


def get_country_codes_from_files():
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
        "NA": ["CA", "US", "MX", "CU", "KN", "PR"],
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

    # Iterate over all files in the directory to collect country codes
    for filename in os.listdir(path):
        if filename.endswith(".geojson"):
            # Extract country code from filename
            name = filename.replace(".geojson", "")
            if "-" in name:
                cc = name.split("-")[0].upper()
                continent = "Region_" + cc
            else:
                cc = name.upper()
                continent = country_to_continent.get(cc, "Unknown")
            if continent not in country_codes:
                country_codes[continent] = []
            country_codes[continent].append(name)

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


def getLocalDatetime(lat, lng, dateTime):
    # Instantiate TimezoneFinder and find timezone for given lat, lng
    tf = TimezoneFinder()
    timezone_str = tf.timezone_at(lat=lat, lng=lng)

    # Convert the UTC datetime to the local timezone
    local_timezone = pytz.timezone(timezone_str)
    local_datetime = dateTime.astimezone(local_timezone).replace(tzinfo=None)
    return local_datetime


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


def saveTripToDb(username, newTrip, newPath, trip_type="train"):
    newPath[0]["lat"] = float(newPath[0]["lat"])
    newPath[0]["lng"] = float(newPath[0]["lng"])
    newPath[-1]["lat"] = float(newPath[-1]["lat"])
    newPath[-1]["lng"] = float(newPath[-1]["lng"])
    if not starts_with_flag_emoji(newTrip["originStation"][1]):
        origin_country = getCountryFromCoordinates(newPath[0]["lat"], newPath[0]["lng"])
        newTrip["originStation"][1] = (
            f"{get_flag_emoji(origin_country['countryCode'])} {newTrip['originStation'][1]}"
        )
        if not newTrip["originManualLat"]:
            newTrip["originManualLat"] = newPath[0]["lat"]
        if not newTrip["originManualLng"]:
            newTrip["originManualLng"] = newPath[0]["lng"]

    if not starts_with_flag_emoji(newTrip["destinationStation"][1]):
        destination_country = getCountryFromCoordinates(
            newPath[-1]["lat"], newPath[-1]["lng"]
        )
        newTrip["destinationStation"][1] = (
            f"{get_flag_emoji(destination_country['countryCode'])} {newTrip['destinationStation'][1]}"
        )
        if not newTrip["destinationManualLat"]:
            newTrip["destinationManualLat"] = newPath[-1]["lat"]
        if not newTrip["destinationManualLng"]:
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
        countries = getCountriesFromPath(newPath, newTrip["type"])

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
        material_type=sanitize_param(newTrip["material_type"]),
        reg=sanitize_param(newTrip["reg"]),
        waypoints=sanitize_param(newTrip["waypoints"]),
        notes=sanitize_param(newTrip["notes"]),
        price=sanitize_param(newTrip["price"]),
        currency=sanitize_param(newTrip["currency"]),
        purchasing_date=sanitize_param(newTrip["purchasing_date"]),
        ticket_id=sanitize_param(newTrip["ticket_id"]),
        is_project=start_datetime == 1 or end_datetime == 1,
        path=newPath,
    )

    create_trip(trip)


def hasUncommonTrips(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM trip
                WHERE type NOT IN ('train', 'bus', 'air', 'ferry', 'helicopter', 'tram', 'metro', 'aerialway')
                AND username = :username
            ) AS has_uncommon_trips;
        """,
            {"username": username},
        )
        return cursor.fetchone()[0] == 1


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
        with managed_cursor(mainConn) as cursor:
            cursor.execute(getTicket, (trip["ticket_id"],))
            ticket = cursor.fetchall()[0]
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


def getDistinctStatYears(username, tripType):
    with managed_cursor(mainConn) as cursor:
        return [
            row[0]
            for row in cursor.execute(
                distinctStatYears, {"username": username, "tripType": tripType}
            ).fetchall()
        ]


def saveManualStation(creator, name, lat, lng, station_type):
    if station_type in (
        "train",
        "bus",
        "helicopter",
        "ferry",
        "aerialway",
        "tram",
        "metro",
    ):
        saveManQuery = saveQuery.format(
            table="manual_stations",
            keys=("creator", "name", "lat", "lng", "station_type"),
            values=", ".join(("?",) * 5),
        )

        with managed_cursor(mainConn) as cursor:
            cursor.execute(saveManQuery, (creator, name, lat, lng, station_type))
        mainConn.commit()


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
    if "userinfo" in session:
        language = session["userinfo"]["lang"]
        # Temp fix for de_CH to gsw
        if language == "de_CH":
            session["userinfo"]["lang"] = "gsw"
            language = "gsw"
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


@app.context_processor
def inject_distinct_types():
    if "userinfo" not in session:
        return {}

    username = session["userinfo"].get("logged_in_user")
    lang_code = session["userinfo"].get("lang")

    if not username:
        return {}

    # Icon mapping
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
    }

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            SELECT DISTINCT type FROM trip
            WHERE username = ?
              AND type NOT IN ('poi', 'accommodation', 'restaurant')
            """,
            (username,),
        )
        types = {
            row[0]: {
                "label": lang[lang_code].get(row[0], row[0]),
                "icon": icon_map.get(row[0], "fa-solid fa-question"),
            }
            for row in cursor.fetchall()
        }

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


@app.route("/<username>/new/auto")
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


@app.route("/<username>/new/<vehicle_type>")
@login_required
def new(username, vehicle_type):
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

    return render_template(
        "new.html",
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
        manualOrigin=manual_origin,
        currencyOptions=get_available_currencies(),
        user_currency=getLoggedUserCurrency(),
    )


@app.route("/<username>/new_tag")
@login_required
def new_tag(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT colour FROM tags WHERE username = ?", (username,))
        suggested_colour = generate_distinct_color(
            [color[0] for color in cursor.fetchall()]
        )
    return render_template(
        "new_tag.html",
        title="",
        suggested_colour=suggested_colour,
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/submit_tag", methods=["POST"])
def submit_tag(username):
    # Extract data from form
    tag_name = request.form["name"]
    tag_colour = request.form["colour"]
    tag_uuid = str(uuid.uuid4())
    tag_type = request.form["type"]

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "INSERT INTO tags (username, name, colour, uuid, type) VALUES (?, ?, ?, ?, ?)",
            (username, tag_name, tag_colour, tag_uuid, tag_type),
        )
        mainConn.commit()

    return redirect(url_for("new_tag", username=username))


@app.route("/<username>/attach_tag", methods=["POST"])
@login_required
def attach_tag(username):
    data = request.json
    tag_id = data.get("tag_id")
    trip_ids = data.get("trip_ids")

    if not tag_id or not trip_ids:
        return jsonify({"error": "Invalid input"}), 400

    with managed_cursor(mainConn) as cursor:
        for trip_id in trip_ids:
            cursor.execute(
                """
                    INSERT INTO tags_associations (tag_id, trip_id)
                    SELECT ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1 FROM tags_associations WHERE tag_id = ? AND trip_id = ?
                    )
                """,
                (tag_id, trip_id, tag_id, trip_id),
            )
        mainConn.commit()
    return ""


@app.route("/<username>/detach_tag", methods=["POST"])
@login_required
def detach_tag(username):
    data = request.json
    tag_id = data.get("tag_id")
    trip_ids = data.get("trip_ids")

    if not tag_id or not trip_ids:
        return jsonify({"error": "Invalid input"}), 400

    with managed_cursor(mainConn) as cursor:
        for trip_id in trip_ids:
            cursor.execute(
                """
                    DELETE FROM tags_associations
                    WHERE tag_id = ? AND trip_id = ?
                """,
                (tag_id, trip_id),
            )
        mainConn.commit()
    return ""


@app.route("/<username>/new_ticket")
@login_required
def new_ticket(username):
    return render_template(
        "new_ticket.html",
        title="new ticket",
        country_list=get_all_countries(),
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
        currencyOptions=get_available_currencies(),
        user_currency=getLoggedUserCurrency(),
    )


def getAddressFromCoords(lat, lng):
    geolocator = Nominatim(user_agent="Trainlog")
    details = geolocator.reverse(
        (lat, lng),
        timeout=10,
        addressdetails=True,  # Get detailed address components
    ).raw["address"]

    # Extract specific parts of the address
    country_code = details.get("country_code", "").upper()  # Get country code
    city = details.get(
        "city", details.get("town", details.get("village", ""))
    )  # Get city/town/village
    suburb = details.get(
        "neighbourhood", details.get("suburb", "")
    )  # Get suburb or neighborhood

    flag = get_flag_emoji(country_code)
    return f"{flag} {city}" + (f" - {suburb}" if suburb else "")


@app.route("/<username>/handle_gpx_upload/<source>", methods=["POST"])
@login_required
def handle_gpx_upload(username, source):
    files = request.files.getlist("gpx_files")
    notes = request.form.get("notes", "")

    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    for file in files:
        if file.filename.endswith(".gpx"):
            gpx = gpxpy.parse(file.stream)

            points = None
            start_time = None
            end_time = None
            distance = 0

            # Handle Tracks
            if gpx.tracks and any(track.segments for track in gpx.tracks):
                all_points = []
                total_distance = 0
                first_time = None
                last_time = None

                # 1. Gather all points from all segments
                for track in gpx.tracks:
                    for segment in track.segments:
                        if segment.points:
                            if first_time is None:
                                first_time = segment.points[
                                    0
                                ].time  # Only set start once
                            last_time = segment.points[
                                -1
                            ].time  # Continuously update end
                            all_points.extend(segment.points)

                # 2. Compute total distance across *all* points (including "gaps" between segments)
                for i in range(1, len(all_points)):
                    total_distance += getDistance(
                        {
                            "lat": all_points[i - 1].latitude,
                            "lng": all_points[i - 1].longitude,
                        },
                        {"lat": all_points[i].latitude, "lng": all_points[i].longitude},
                    )

                # Assign them back to your existing variables so you don't change the rest of your code.
                points = all_points
                start_time = first_time
                end_time = last_time
                distance = total_distance

            # Handle Routes
            elif gpx.routes and gpx.routes[0].points:
                points = gpx.routes[0].points
                # Routes typically don't include timestamps; set start/end times to None
                start_time = None
                end_time = None
                # Approximate route distance by summing distances between consecutive points
                for i in range(len(points) - 1):
                    distance += gpxpy.geo.distance(
                        points[i].latitude,
                        points[i].longitude,
                        0,
                        points[i + 1].latitude,
                        points[i + 1].longitude,
                        0,
                    )

            if points:
                # Generate path in [[lat, lng], [lat, lng]] format
                path = json.dumps(
                    [[point.latitude, point.longitude] for point in points]
                )

                # Extract start and end points
                start_point = points[0]
                end_point = points[-1]

                # Geocode start and end points
                origin = getAddressFromCoords(
                    lat=start_point.latitude, lng=start_point.longitude
                )
                destination = getAddressFromCoords(
                    lat=end_point.latitude, lng=end_point.longitude
                )

                # Calculate duration (only for tracks with timestamps)
                duration = 0
                if start_time and end_time:
                    duration = int(
                        (end_time - start_time).total_seconds()
                    )  # Duration in seconds

                    # Convert to local time
                    start_time = getLocalDatetime(
                        start_point.latitude, start_point.longitude, start_time
                    )
                    end_time = getLocalDatetime(
                        end_point.latitude, end_point.longitude, end_time
                    )

                    # Format to "YYYY-MM-DD HH:MM"
                    start_time = start_time.strftime("%Y-%m-%d %H:%M")
                    end_time = end_time.strftime("%Y-%m-%d %H:%M")

                with managed_cursor(mainConn) as cursor:
                    cursor.execute(
                        """
                        INSERT INTO gpx (source, username, origin, destination, start_time, end_time, duration, distance, path, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source,
                            username,
                            origin,
                            destination,
                            start_time,
                            end_time,
                            duration,
                            int(distance),
                            path,
                            notes,
                        ),
                    )

            else:
                return jsonify({"error": f"No points found in {file.filename}"}), 400
        else:
            return jsonify({"error": f"{file.filename} is not a valid GPX file"}), 400

    mainConn.commit()

    return jsonify({"message": "Files processed successfully"}), 200


@app.route("/<username>/upload_gpx")
@login_required
def upload_gpx(username):
    return render_template(
        "upload_gpx.html",
        title="gpx",
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/update_gpx", methods=["POST"])
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

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "SELECT duration FROM gpx WHERE uid = ? AND username = ?",
            (gpx_id, username),
        )
        row = cursor.fetchone()
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
            cursor.execute(
                """
                UPDATE gpx
                   SET origin     = ?,
                       destination = ?,
                       start_time  = ?,
                       end_time    = ?,
                       duration    = ?
                 WHERE uid = ?
                   AND username = ?
            """,
                (
                    origin,
                    destination,
                    start_time,
                    end_time,
                    new_duration,
                    gpx_id,
                    username,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE gpx
                   SET origin     = ?,
                       destination = ?,
                       start_time  = ?,
                       end_time    = ?
                 WHERE uid = ?
                   AND username = ?
            """,
                (origin, destination, start_time, end_time, gpx_id, username),
            )

        mainConn.commit()

    return jsonify({"success": True})


@app.route("/<username>/list_gpx", methods=["GET"])
@login_required
def list_gpx(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            SELECT *
            FROM gpx
            WHERE username = ?
            ORDER BY start_time DESC
        """,
            (username,),
        )
        gpx_files = cursor.fetchall()

    trip_types = {
        "train": lang[session["userinfo"]["lang"]]["train"],
        "tram": lang[session["userinfo"]["lang"]]["tram"],
        "metro": lang[session["userinfo"]["lang"]]["metro"],
        "bus": lang[session["userinfo"]["lang"]]["bus"],
        "ferry": lang[session["userinfo"]["lang"]]["ferry"],
        "car": lang[session["userinfo"]["lang"]]["car"],
        "cycle": lang[session["userinfo"]["lang"]]["cycle"],
        "walk": lang[session["userinfo"]["lang"]]["walk"],
        "aerialway": lang[session["userinfo"]["lang"]]["aerialway"],
        "air": lang[session["userinfo"]["lang"]]["air"],
        "helicopter": lang[session["userinfo"]["lang"]]["helicopter"],
    }

    # Pass the GPX files to the template
    return render_template(
        "list_gpx.html",
        trip_types=trip_types,
        username=username,
        gpxList=gpx_files,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/delete_gpx/<gpx_id>", methods=["POST"])
@login_required
def delete_gpx(username, gpx_id):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            DELETE FROM gpx
            WHERE uid = ? AND username = ?
        """,
            (gpx_id, username),
        )

    mainConn.commit()
    return redirect(url_for("list_gpx", username=username))


def cluster_waypoints(waypoints, min_distance_meters=10):
    """
    Group waypoints that are within min_distance_meters of each other
    and return the average position for each cluster.

    :param waypoints: List of {"lat": float, "lng": float} waypoints
    :param min_distance_meters: Minimum distance in meters to consider points as separate
    :return: List of simplified waypoints
    """
    from math import asin, cos, radians, sin, sqrt

    def haversine(lat1, lon1, lat2, lon2):
        """Calculate the great circle distance between two points in meters"""
        # Convert decimal degrees to radians
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])

        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))
        # Radius of earth in meters
        r = 6371000
        return c * r

    if not waypoints:
        return []

    simplified = []
    current_cluster = [waypoints[0]]

    for i in range(1, len(waypoints)):
        # Check distance from current point to the first point in the current cluster
        distance = haversine(
            waypoints[i]["lat"],
            waypoints[i]["lng"],
            current_cluster[0]["lat"],
            current_cluster[0]["lng"],
        )

        if distance <= min_distance_meters:
            # Add to current cluster
            current_cluster.append(waypoints[i])
        else:
            # Calculate the average position for the cluster
            avg_lat = sum(p["lat"] for p in current_cluster) / len(current_cluster)
            avg_lng = sum(p["lng"] for p in current_cluster) / len(current_cluster)
            simplified.append({"lat": avg_lat, "lng": avg_lng})

            # Start a new cluster with the current point
            current_cluster = [waypoints[i]]

    # Don't forget to add the last cluster
    if current_cluster:
        avg_lat = sum(p["lat"] for p in current_cluster) / len(current_cluster)
        avg_lng = sum(p["lng"] for p in current_cluster) / len(current_cluster)
        simplified.append({"lat": avg_lat, "lng": avg_lng})

    return simplified


@app.route("/<username>/save_trip_from_gpx/<gpx_id>", methods=["POST"])
@login_required
def saveTripFromGPX(username, gpx_id):
    try:
        request_data = request.get_json()
        trip_type = request_data.get("type", "train")
        use_routing = request_data.get("use_routing", False)

        # Retrieve the GPX data from the database
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                "SELECT * FROM gpx WHERE uid = ? AND username = ?", (gpx_id, username)
            )
            gpx = cursor.fetchone()

        if not gpx:
            return jsonify(
                {"error": "GPX file not found or does not belong to the user."}
            ), 404

        # Extract GPX details
        origin = gpx["origin"]
        destination = gpx["destination"]
        start_time = (
            gpx["start_time"].replace(" ", "T") if gpx["start_time"] is not None else -1
        )
        end_time = (
            gpx["end_time"].replace(" ", "T") if gpx["start_time"] is not None else -1
        )

        distance = gpx["distance"]
        duration = gpx["duration"] if gpx["duration"] not in (None, "") else 0
        notes = gpx["notes"]
        precision = "preciseDates" if start_time != -1 else "unknown"

        # Convert points to proper format for routing
        waypoints = [
            {"lat": point[0], "lng": point[1]} for point in json.loads(gpx["path"])
        ]

        # Use clustering to reduce number of points
        waypoints = cluster_waypoints(waypoints, 10)

        # Create a new trip structure
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
            "waypoints": json.dumps(waypoints),
        }

        if use_routing and trip_type in [
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
            # Convert waypoints to OSRM path format (lng,lat)
            path_str = ";".join(f"{wp['lng']},{wp['lat']}" for wp in waypoints)

            # Determine the routing type and forward the request
            router_type = "driving" if trip_type == "bus" else trip_type

            # Options for fuzzy routing - adding radiuses and overview
            options = "overview=full"

            # Add radiuses parameter - gives flexibility for each waypoint (in meters)
            # This is key for fuzzy routing - allows OSRM to find nearby roads within this radius
            radiuses = ";".join(
                ["50"] * len(waypoints)
            )  # 50 meters radius for each point
            options += f"&radiuses={radiuses}"

            # Add continue_straight=false for more natural routing at intersections
            options += "&continue_straight=false"

            # For better snapping tolerance in urban areas
            options += "&snapping=any"

            router_path = f"route/v1/{router_type}/{path_str}"

            response = forwardRouting(router_path, trip_type, options)

            # Parse the router response
            routing_result = json.loads(response)
            if routing_result["code"] != "Ok":
                path = [
                    {"lat": node[0], "lng": node[1]} for node in json.loads(gpx["path"])
                ]  # Convert JSON path to list
            else:
                # Process successful routing
                path = [
                    {"lat": coord[0], "lng": coord[1]}
                    for coord in polyline.decode(
                        routing_result["routes"][0]["geometry"]
                    )
                ]
                newTrip["trip_length"] = routing_result["routes"][0]["distance"]
                newTrip["estimated_trip_duration"] = routing_result["routes"][0][
                    "duration"
                ]
        else:
            path = [
                {"lat": node[0], "lng": node[1]} for node in json.loads(gpx["path"])
            ]  # Convert JSON path to list

        # Delete the GPX file after saving as a trip
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                "DELETE FROM gpx WHERE uid = ? AND username = ?", (gpx_id, username)
            )

        mainConn.commit()

        # Call the saveTripToDb function to save the trip
        saveTripToDb(
            username=username, newTrip=newTrip, newPath=path, trip_type=trip_type
        )

        return jsonify({"success": True}), 200

    except Exception as e:
        mainConn.rollback()
        raise (e)


@app.route("/<username>/submit_ticket", methods=["POST"])
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

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "INSERT INTO tickets (name, username, price, currency, purchasing_date, notes, active_countries) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                username,
                float(price),
                currency,
                purchasing_date,
                notes,
                active_countries_str,
            ),
        )
        mainConn.commit()
    return redirect(url_for("ticket_list", username=username))


@app.route("/<username>/edit_ticket", methods=["POST"])
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

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "UPDATE tickets SET name = ?, price = ?, currency = ?, purchasing_date = ?, notes = ?, active_countries = ? WHERE uid = ? AND username = ?",
            (
                name,
                float(price),
                currency,
                purchasing_date,
                notes,
                active_countries_str,
                ticket_id,
                username,
            ),
        )
        mainConn.commit()

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


@app.route("/<username>/ticket_list")
@login_required
def ticket_list(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(getTickets, (username,))
        tickets = cursor.fetchall()

    result = []
    user_currency = getLoggedUserCurrency()

    for ticket in tickets:
        end_ticket = dict(ticket)
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
                with managed_cursor(mainConn) as cursor:
                    cursor.execute(
                        "SELECT uid, countries FROM trip WHERE ticket_id = ?",
                        (ticket["uid"],),
                    )
                    trips = cursor.fetchall()

                active_countries = set(ticket["active_countries"].split(","))
                trips_in_active_countries = [
                    trip
                    for trip in trips
                    if any(
                        country in active_countries
                        for country in json.loads(trip["countries"]).keys()
                    )
                ]

                total_distance = sum(
                    sum(
                        distance
                        for country, distance in json.loads(trip["countries"]).items()
                        if country in active_countries
                    )
                    for trip in trips_in_active_countries
                )

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
        tickets=result,
        username=username,
        country_list=get_all_countries(),
        currencyOptions=get_available_currencies(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/tag_list")
@login_required
def tag_list(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            WITH UTC_Filtered AS (
                SELECT *,
                CASE
                    WHEN utc_start_datetime IS NOT NULL THEN utc_start_datetime
                    ELSE start_datetime
                END AS utc_filtered_start_datetime,
                CASE
                    WHEN utc_end_datetime IS NOT NULL THEN utc_end_datetime
                    ELSE end_datetime
                END AS utc_filtered_end_datetime
                FROM trip
            )
            SELECT t.*,
                   MAX(uf.utc_filtered_end_datetime) AS latest_trip_end,
                   COUNT(DISTINCT ta.trip_id) AS trip_count,
                   SUM(
                       CASE
                           WHEN uf.utc_filtered_start_datetime NOT IN (1, -1)
                                AND uf.utc_filtered_end_datetime NOT IN (1, -1)
                           THEN CAST((strftime('%s', uf.utc_filtered_end_datetime) - strftime('%s', uf.utc_filtered_start_datetime)) AS INTEGER)
                           WHEN uf.manual_trip_duration IS NOT NULL
                           THEN uf.manual_trip_duration
                           ELSE uf.estimated_trip_duration
                       END
                   ) AS total_trip_duration,
                   SUM(uf.trip_length) AS total_trip_length
            FROM tags t
            LEFT JOIN tags_associations ta ON t.uid = ta.tag_id
            LEFT JOIN UTC_Filtered uf ON ta.trip_id = uf.uid
            WHERE t.username = ?
            GROUP BY t.uid
            ORDER BY
                CASE
                    WHEN MAX(uf.utc_filtered_end_datetime) IS NULL AND ta.trip_id IS NULL THEN 1
                    WHEN MAX(uf.utc_filtered_end_datetime) IS NULL THEN 3
                    ELSE 2
                END,
                MAX(uf.utc_filtered_end_datetime) DESC
            """,
            (username,),
        )
        tags = [dict(tag) for tag in cursor.fetchall()]

    return render_template(
        "tag_list.html",
        tagsList=tags,
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/delete_tag/<tag_id>", methods=["POST"])
@login_required
def delete_tag(username, tag_id):
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT username from tags WHERE uid = ?", (tag_id,))
        if cursor.fetchone()["username"] != username:
            raise 401
        else:
            cursor.execute("DELETE FROM tags WHERE uid = ?", (tag_id,))
            cursor.execute("DELETE FROM tags_associations WHERE tag_id = ?", (tag_id,))
    mainConn.commit()
    return redirect(url_for("tag_list", username=username))


@app.route("/<username>/update_tag/<tag_id>", methods=["POST"])
@login_required
def update_tag(username, tag_id):
    tag_name = request.form["name"]
    tag_colour = request.form["colour"]
    tag_type = request.form["type"]
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT username from tags WHERE uid = ?", (tag_id,))
        if cursor.fetchone()["username"] != username:
            raise 401
        else:
            cursor.execute(
                "UPDATE tags SET name = ?, colour = ?, type = ? WHERE uid = ?",
                (tag_name, tag_colour, tag_type, tag_id),
            )
    mainConn.commit()
    return redirect(url_for("tag_list", username=username))


@app.route("/<username>/get_all_tickets")
@login_required
def get_all_tickets(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(getTickets, (username,))
        tickets = cursor.fetchall()
    return jsonify(tickets=[dict(ticket) for ticket in tickets])


@app.route("/<username>/get_all_tags")
@login_required
def get_all_tags(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(getTags, (username,))
        tags = cursor.fetchall()
    return jsonify(tags=[dict(tag) for tag in tags])


@app.route("/<username>/delete_ticket/<ticket_id>")
@login_required
def delete_ticket(username, ticket_id):
    try:
        # Using transaction management with context manager
        with managed_cursor(mainConn) as cursor:
            # Unassigning the ticket from trips
            cursor.execute(
                "UPDATE trip SET ticket_id = NULL WHERE username = ? AND ticket_id = ?",
                (username, ticket_id),
            )
            # Deleting the ticket
            cursor.execute(
                "DELETE FROM tickets WHERE username = ? AND uid = ?",
                (username, ticket_id),
            )
            # Committing changes to the database
            mainConn.commit()

        # If no exceptions, return success
        return jsonify({"success": True}), 200
    except Exception as e:
        # Roll back in case of error
        mainConn.rollback()
        # Return an error message
        print(e)
        return jsonify({"error": "An error occurred while deleting the ticket"}), 500


@app.route("/<username>/toggle_ticket_active/<ticket_id>")
@login_required
def toggle_ticket_active(username, ticket_id):
    try:
        # Using transaction management with context manager
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                "UPDATE tickets SET active = NOT active WHERE username = ? AND uid = ?",
                (username, ticket_id),
            )
            mainConn.commit()

        # If no exceptions, return success
        return jsonify({"success": True}), 200
    except Exception as e:
        # Roll back in case of error
        mainConn.rollback()
        # Return an error message
        print(e)
        return jsonify({"error": "An error occurred while toggling the ticket"}), 500


@app.route("/<username>/attachSelected")
@login_required
def attachSelected(username):
    requestedTrips = request.args.get("trips", default=None)
    ticket_id = request.args.get("ticket_id", default=None)

    with managed_cursor(mainConn) as cursor:
        query = "UPDATE trip SET ticket_id = '{ticket_id}' WHERE username = '{username}' AND uid IN ({requestedTrips})"
        formattedQuery = query.format(
            username=username,
            ticket_id=ticket_id,
            requestedTrips=", ".join(("?",) * len(requestedTrips.split(","))),
        )
        cursor.execute(formattedQuery, requestedTrips.split(","))
    mainConn.commit()

    return redirect(url_for("ticket_list", username=username))


@app.route("/<username>/new_flight")
@login_required
def new_flight(username):
    fr24_calls = fr24_usage(username)
    return render_template(
        "new_flight.html",
        title=lang[session["userinfo"]["lang"]]["newTripAir"],
        username=username,
        currencyOptions=get_available_currencies(),
        fr24_calls=fr24_calls,
        user_currency=getLoggedUserCurrency(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/new_train")
@login_required
def new_train(username):
    return render_template(
        "new_train.html",
        title=lang[session["userinfo"]["lang"]]["newTripTrain"],
        username=username,
        currencyOptions=get_available_currencies(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/routing")
@login_required
def routing(username):
    return render_template(
        "routing.html",
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/air_routing/<type>")
@login_required
def air_routing(username, type):
    return render_template(
        "air_routing.html",
        type=type,
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/freehand")
@login_required
def freehand(username):
    return render_template(
        "freehand.html",
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/ship_routing")
@login_required
def ship_routing(username):
    return render_template(
        "ship_routing.html",
        username=username,
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
        "convertCurrency",
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
        elif "@" in username or "." in username:
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
    twenty_four_hours_ago = datetime.utcnow() - timedelta(days=1)
    active_users_count = User.query.filter(
        User.last_login >= twenty_four_hours_ago
    ).count()
    today = datetime.utcnow().date()
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT number FROM daily_active_users WHERE date = ?", (today,))
        result = cursor.fetchone()
        if result:
            current_count = result["number"]
            if active_users_count > current_count:
                cursor.execute(
                    "UPDATE daily_active_users SET number = ? WHERE date = ?",
                    (active_users_count, today),
                )
        else:
            cursor.execute(
                "INSERT INTO daily_active_users (date, number) VALUES (?, ?)",
                (today, active_users_count),
            )
    mainConn.commit()


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
            if user.default_landing == "trips":
                return redirect(
                    url_for("dynamic_trips", username=username, time="trips")
                )
            elif user.default_landing == "projects":
                return redirect(
                    url_for("dynamic_trips", username=username, time="projects")
                )
            else:  # Default to map
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
    """

    # Check if the user is already logged in
    username = session.get("logged_in")
    if username and session.get(username):
        return redirect(url_for("user_home", username=username))

    # Handle POST request for login
    if request.method == "POST":
        # Safely get form data to avoid KeyError
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        # Check if username and password are provided
        if not username or not password:
            flash(lang[session["userinfo"]["lang"]]["credentialsCantEmpty"])
            log_denied_login(
                "missing_credentials", username, getRequestData(request), getIp(request)
            )
            return redirect(url_for("login"))

        # Check for denied login attempts (e.g., rate limiting)
        if not check_denied_login(getIp(request), username):
            logger.warning(f"Denying login for {username} after too many attempts")
            flash(lang[session["userinfo"]["lang"]]["tooManyErrors"])
            log_denied_login(
                "too_many_requests", username, getRequestData(request), getIp(request)
            )
            return redirect(url_for("login"))

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
            session.permanent = (
                True  # Extend session validity based on app configuration
            )
            changeLang(user.lang, session)

            return redirect(url_for("user_home", username=username))
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
            return redirect(url_for("login"))

    # Handle GET request: Render login form
    return render_template(
        "login_form.html",
        title=lang[session["userinfo"]["lang"]]["login"],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>")
@login_required
def user_home(username):
    """
    Home page for validated users.

    """
    return render_template(
        "map.html",
        title=lang[session["userinfo"]["lang"]]["map"],
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=isCurrentTrip(username),
        public=False,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/getVectorStyle/<language>/<style>.json")
def vector_style(language, style):
    json_path = os.path.join(
        app.static_folder, "styles/vector_maps/{style}.json".format(style=style)
    )

    with open(json_path, "r", encoding="utf-8") as f:
        file_contents = f.read()
        file_contents = file_contents.replace(
            "{{mapPinUrl}}",
            url_for(
                "static", filename="styles/vector_maps", _scheme="https", _external=True
            ),
        )
        template_url = "https://tiles.trainlog.me/tile/streets-v2+landcover-v1.1+hillshade-v1/{x}/{y}/{z}/{language}"
        final_url = template_url.replace("{language}", language)
        file_contents = file_contents.replace("{{tileServerUrl}}", final_url)
        file_contents = file_contents.replace("{{language}}", language)
        vectorStyle = json.loads(file_contents)

    # Return as proper JSON response
    return jsonify(vectorStyle)


@app.route("/<username>/new_map")
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
        isCurrent=isCurrentTrip(username),
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


@app.route("/<username>/countries/<cc>")
@app.route("/public/<username>/countries/<cc>")
@public_required
def countries(username, cc):
    """ """
    # If the username doesn't match the logged-in user and the accessed URL doesn't have 'public', redirect to the public URL
    if username != getUser() and "/public/" not in request.path:
        public_url = url_for("countries", username=username, cc=cc)
        return redirect(public_url)

    if username == getUser():
        nav = "bootstrap/navigation.html"
    else:
        nav = "bootstrap/public_nav.html"
    directory_path = "country_percent/countries/processed/"
    file_path = os.path.join(directory_path, f"{cc}.geojson")
    if not os.path.exists(file_path):
        abort(410)

    return render_template(
        "countries.html",
        title=lang[session["userinfo"]["lang"]]["map"],
        username=username,
        nav=nav,
        cc=cc,
        isCurrent=isCurrentTrip(username),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/countryGeoJSON/<cc>")
@public_required
def getCountryGeoJSON(username, cc):
    def midpoint(point1, point2):
        return ((point1[0] + point2[0]) / 2, (point1[1] + point2[1]) / 2)

    def getPolygonFromCoordinates(cc, lat, lng):
        return geopip_country.search(cc=cc, lat=lat, lng=lng)

    start_time = datetime.now()
    # Prepare the parameters
    if "-" in cc:
        params = {"username": username, "country": "%" + cc.split("-")[0] + "%"}
    else:
        params = {"username": username, "country": "%" + cc + "%"}

    with managed_cursor(mainConn) as cursor:
        idList = [
            row["uid"] for row in cursor.execute(getTripsCountry, params).fetchall()
        ]

    formattedGetUserLines = getUserLines.format(
        trip_ids=", ".join(("?",) * len(idList))
    )
    with managed_cursor(pathConn) as cursor:
        pathResult = cursor.execute(formattedGetUserLines, tuple(idList)).fetchall()

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

    directory_path = "country_percent/countries/processed/"

    file_path = os.path.join(directory_path, f"{cc}.geojson")
    with open(file_path, "r") as file:
        geojson_data = json.load(file)
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
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                upsertPercent, {"username": username, "cc": cc, "percent": percent}
            )
        mainConn.commit()
    end_time = datetime.now()  # End the timer
    render_time = end_time - start_time  # Calculate the difference
    print(render_time)
    return jsonify([percent, geojson_data])


@app.route("/admin/editCountries/<cc>")
@admin_required
def editCountries(cc):
    """ """
    directory_path = "country_percent/countries/processed/"
    file_path = os.path.join(directory_path, f"{cc}.geojson")
    if not os.path.exists(file_path):
        abort(410)

    return render_template(
        "admin/country_edit.html",
        title="Edit " + cc,
        username=getUser(),
        nav="bootstrap/navigation.html",
        cc=cc,
        isCurrent=isCurrentTrip(getUser()),
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
        isCurrent=isCurrentTrip(getUser()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/getGeojson/<cc>", methods=["GET"])
@admin_required
def get_full_geojson(cc):
    directory_path = "country_percent/countries/processed/"

    file_path = os.path.join(directory_path, f"{cc}.geojson")

    with open(file_path, "r") as file:
        geojson_data = json.load(file)

    return jsonify(geojson_data)


@app.route(
    "/convertCurrency/<baseCurrency>/<targetCurrency>/<date>/<price>", methods=["GET"]
)
def convertCurrency(baseCurrency, targetCurrency, date, price):
    convertedPrice = get_exchange_rate(
        base_currency=baseCurrency,
        target_currency=targetCurrency,
        date=date,
        price=price,
    )
    return jsonify(convertedPrice)


@app.route("/removePolygons/<cc>", methods=["POST"])
@admin_required
def remove_polygons(cc):
    ids_to_remove = request.json

    directory_path = "country_percent/countries/processed/"
    file_path = os.path.join(directory_path, f"{cc}.geojson")

    with open(file_path, "r") as file:
        geojson_data = json.load(file)

    # Calculate the total area to be subtracted
    total_area_to_subtract = sum(
        feature["properties"]["area_m2"]
        for feature in geojson_data["features"]
        if feature["properties"]["id"] in ids_to_remove
    )

    # Subtract the total area to be subtracted from the total_area_m2 property
    geojson_data["total_area_m2"] -= total_area_to_subtract

    # Filter out polygons based on their ID
    geojson_data["features"] = [
        feature
        for feature in geojson_data["features"]
        if feature["properties"]["id"] not in ids_to_remove
    ]

    with open(file_path, "w") as file:
        json.dump(geojson_data, file)

    return jsonify({"success": True})


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
                        f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}] "
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
                log_entry = f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}] User: {username}, Key: {key}, Old: {old_value}, New: {new_value}\n"
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
    """
    Public home
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


@app.route("/public/<username>/new_map")
@public_required
def public_new(username):
    """
    Public home
    """
    user = User.query.filter_by(username=getUser()).first()
    if user is not None:
        tileserver = (user.tileserver,)
        globe = (user.globe,)
    else:
        tileserver = ("default",)
        globe = (False,)

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
@app.route("/admin/borked_trips/<username>")
@owner_required
def borked_trips(username=None):
    with managed_cursor(mainConn) as main_cursor:
        with managed_cursor(pathConn) as path_cursor:
            # Get trips (filtered by username if provided)
            query = "SELECT uid, username, created FROM trip"
            params = []
            if username:
                query += " WHERE username = ?"
                params.append(username)

            main_cursor.execute(query, params)
            trips = main_cursor.fetchall()

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

            for i in range(0, len(all_uids), batch_size):
                batch = all_uids[i : i + batch_size]
                placeholders = ",".join(["?"] * len(batch))
                path_cursor.execute(
                    f"SELECT DISTINCT trip_id FROM paths WHERE trip_id IN ({placeholders})",
                    batch,
                )
                path_uids.update(row["trip_id"] for row in path_cursor.fetchall())

            if username:
                # Single user mode
                # trip_uids = {row['uid'] for row in trips}
                missing = [
                    {"uid": row["uid"], "created": row["created"]}
                    for row in trips
                    if row["uid"] not in path_uids
                ]
                return jsonify({"missing_trips": missing, "count": len(missing)})

            # Global mode
            result = {}
            for row in trips:
                if row["uid"] not in path_uids:
                    user = row["username"]
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

    print(selected_types)

    with managed_cursor(mainConn) as cursor:
        for logo_type in selected_types:
            # Fetch logos based on operator_type field
            cursor.execute(
                """
                SELECT o.short_name, l.logo_url
                FROM operators o
                JOIN operator_logos l ON o.uid = l.operator_id
                WHERE o.operator_type = ?
            """,
                (logo_type,),
            )

            for row in cursor.fetchall():
                logoURLs[row["short_name"]] = row["logo_url"]

    return logoURLs


def render_public_trip_page(
    tripIds=None, tagId=None, ticketId=None, template="public/public_trip.html"
):
    tag_type = None
    tag_name = None
    countries = []
    length = 0

    if tripIds is None and tagId is not None:
        with managed_cursor(mainConn) as cursor:
            result = cursor.execute(
                """
                SELECT GROUP_CONCAT(trip_id) AS trip_ids, tags.type as type, tags.name as name
                FROM tags_associations
                LEFT JOIN tags on tags.uid = tags_associations.tag_id
                WHERE uuid = ?
                """,
                (tagId,),
            ).fetchone()
            tripIds = result["trip_ids"]
            tag_type = result["type"]
            tag_name = result["name"]
    elif tripIds is None and ticketId is not None:
        with managed_cursor(mainConn) as cursor:
            result = cursor.execute(
                """
                SELECT GROUP_CONCAT(trip.uid) AS trip_ids, tickets.name AS ticket_name
                FROM trip
                LEFT JOIN tickets ON trip.ticket_id = tickets.uid
                WHERE tickets.uid = ?
                """,
                (ticketId,),
            ).fetchone()
            tripIds = result["trip_ids"]
            tag_name = result["ticket_name"]

    if not tripIds:
        abort(410)

    trip_list = []
    for trip_id in tripIds.split(","):
        with managed_cursor(mainConn) as cursor:
            trip = cursor.execute(getTrip, {"trip_id": trip_id}).fetchone()
        if trip is not None:
            for country in json.loads(trip["countries"]).keys():
                if country not in countries:
                    countries.append(country)
            length += trip["trip_length"]
            trip_list.append(dict(trip))
            user = User.query.filter_by(username=trip["username"]).first()
            if (
                not session.get(user.username)
                and not user.is_public_trips()
                and not session.get("owner")
            ):
                abort(401)
        else:
            abort(410)

    try:
        trip_list_sorted = sorted(
            trip_list, key=lambda trip: trip["utc_filtered_start_datetime"]
        )
    except TypeError:
        abort(416)
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

    return render_template(
        template,
        logosList=listOperatorsLogos(),
        tripIds=tripIds,
        collection_voyage=tag_type,
        tag_description=tag_name,
        special_og=True,
        tileserver=tileserver,
        globe=globe,
        og=og,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/public/trip/<tripIds>")
@app.route("/public/tag/<tagId>")
@app.route("/public/ticket/<ticketId>")
def public_trip(tripIds=None, tagId=None, ticketId=None):
    return render_public_trip_page(tripIds, tagId, ticketId)


@app.route("/public/new/trip/<tripIds>")
@app.route("/public/new/tag/<tagId>")
@app.route("/public/new/ticket/<ticketId>")
def public_trip_new(tripIds=None, tagId=None, ticketId=None):
    return render_public_trip_page(
        tripIds, tagId, ticketId, template="public/new_trip.html"
    )


@app.route("/public/multiTrip/<tripIds>")
def multi_trip(tripIds):
    """
    Public Trip
    """
    trip_list = []
    for trip in tripIds.split(","):
        with managed_cursor(mainConn) as cursor:
            trip = cursor.execute(getTrip, {"trip_id": trip}).fetchone()
        if trip is not None:
            trip_list.append(dict(trip))
            user = User.query.filter_by(username=trip["username"]).first()
            if (
                not session.get(user.username)
                and not user.is_public_trips()
                and not session.get(owner)
            ):
                abort(401)
        else:
            abort(410)

    return render_template(
        "public/multi_trip.html",
        tripIds=tripIds,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


def convert_path_to_format(path, output_format):
    """
    Convert the path data to the specified format (GPX or GeoJSON).
    """
    # Load path data from JSON
    coordinates = json.loads(path)

    if output_format == "gpx":
        # Create the GPX root element
        gpx = ET.Element("gpx", version="1.1", creator="Trainlog.me")

        # Create a GPX 'trk' (track) element
        trk = ET.SubElement(gpx, "trk")
        trk_name = ET.SubElement(trk, "name")
        trk_name.text = "Trip Path"

        # Create a 'trkseg' (track segment) and add 'trkpt' (track points) elements
        trkseg = ET.SubElement(trk, "trkseg")

        # Assuming 'coordinates' contains a list of lat, lon pairs
        # TODO check if this code is needed, it looks like it does nothing as the
        # variable is not read anywhere
        for point in coordinates:
            _trkpt = ET.SubElement(
                trkseg, "trkpt", lat=str(point[0]), lon=str(point[1])
            )

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
        with managed_cursor(mainConn) as cursor:
            trip = cursor.execute(getTrip, {"trip_id": trip_id}).fetchone()
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

        # 2) Retrieve the path from the database
        with managed_cursor(pathConn) as cursor:
            cursor.execute("SELECT path FROM paths WHERE trip_id = ?", (trip_id,))
            path = cursor.fetchone()

        if path is None:
            abort(404, description=f"Path not found for trip_id={trip_id}")

        # 3) Convert the path to the requested format
        output_data = convert_path_to_format(path["path"], format_type)

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
    from datetime import datetime

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


@app.route("/<username>/current")
@login_required
def current(username):
    """
    Current trip
    """
    return render_template(
        "current.html",
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/public/<username>/stats/<year>/<tripType>")
@app.route("/public/<username>/stats/<tripType>")
@app.route("/public/<username>/stats")
@public_required
def public_stats(username, tripType=None, year=None):
    if tripType in ("walk", "cycle", "car"):
        abort(401)
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "SELECT distinct type from trip where username = ? AND type not in ('poi', 'accommodation', 'restaurant', 'walk', 'cycle', 'car')",
            (username,),
        )
        types = {
            row[0]: lang[session["userinfo"]["lang"]][row[0]]
            for row in cursor.fetchall()
        }

    if tripType is None:
        return redirect(
            url_for("public_stats", username=username, tripType="train", year=year)
        )
    distinctStatYears = getDistinctStatYears(username, tripType)
    if year is not None and year not in distinctStatYears:
        return redirect(
            url_for("stats", username=username, tripType=tripType, year=None)
        )

    stats = fetch_stats(username, tripType, year)
    return render_template(
        "stats.html",
        nav="bootstrap/public_nav.html",
        isCurrent=isCurrentTrip(username),
        is_public=True,
        title=lang[session["userinfo"]["lang"]]["stats"],
        username=username,
        statYear=year,
        logosList=listOperatorsLogos(),
        tripType=tripType,
        statsData=jsonify(stats).json,
        publicDistinctTypes=types,
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
        nav="bootstrap/nav.html",
        **lang[chosen_lang],
        **session["userinfo"],
    )


@app.route("/<username>/logout")
def logout(username):
    """Logout user and redirect to login page with success message."""
    session.pop(username, None)
    session.pop("logged_in", None)
    flash(lang[session["userinfo"]["lang"]]["successLoggedOut"])
    return redirect(url_for("login"))


@app.route("/<username>/saveTrip", methods=["GET", "POST"])
def saveTrip(username):
    if not (session.get(username) or session.get(owner)):
        abort(401)
    if request.method == "POST":
        jsonPath = request.form["jsonPath"]
        newPath = json.loads(jsonPath)
        jsonNewTrip = request.form["newTrip"]
        newTrip = json.loads(jsonNewTrip)
        saveTripToDb(
            username=username, newTrip=newTrip, newPath=newPath, trip_type=newTrip["type"]
        )

    return ""


@app.route("/<username>/scottySaveTrip", methods=["GET", "POST"])
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
            response = forwardRouting(router_path, trip_type, "overview=full")

            # Parse the router response (if necessary for your DB structure)
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


@app.route("/<username>/saveFlight/<type>", methods=["GET", "POST"])
@login_required
def saveFlight(username, type):
    if request.method == "POST":
        jsonPath = request.form["jsonPath"]
        newPath = json.loads(jsonPath)
        jsonNewTrip = request.form["newTrip"]
        newTrip = json.loads(jsonNewTrip)
        airlineLogoProcess(newTrip)
        saveTripToDb(username=username, newTrip=newTrip, newPath=newPath, trip_type=type)

    return ""


@app.route("/<username>/deleteTrip", methods=["GET", "POST"])
@login_required
def deleteTrip(username):
    if request.method == "POST":
        data = json.loads(request.form["tripId"])
        tripIds = data if isinstance(data, list) else [data]
        for id in tripIds:
            delete_trip(id, username)

    return ""


@app.route("/<username>/updateTrip", methods=["GET", "POST"])
@login_required
def updateTrip(username):
    if request.method == "POST":
        formData = dict(request.form)
        trip_id = formData["trip_id"]

        check_current_user_owns_trip(trip_id)

        new_trip = update_trip_values_from_form_data(trip_id, formData)
        update_trip(trip_id, new_trip, formData)
    return ""


@app.route("/<username>/copyTrip", methods=["GET", "POST"])
@login_required
def copyTrip(username):
    if request.method == "POST":
        formData = dict(request.form)
        trip_id = formData["trip_id"]

        check_current_user_owns_trip(trip_id)

        new_trip_id = duplicate_trip(trip_id)
        new_trip = update_trip_values_from_form_data(new_trip_id, formData)

        update_trip(new_trip_id, new_trip, formData)
    return ""


def check_current_user_owns_trip(trip_id):
    """
    Ensures that a given trip belongs to the currently logged in user
    """
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "SELECT username FROM trip WHERE uid = :trip_id", {"trip_id": trip_id}
        )
        row = cursor.fetchone()

        if row is None:
            abort(404)  # Trip does not exist
        elif getUser() not in (row["username"], owner):
            logger.error(
                f"User {getUser()} tried to access trip {trip_id} owned by {row['username']}"
            )
            abort(404)  # Trip does not belong to the user


def get_user_id(username):
    return User.query.filter_by(username=username).first().uid


def get_trip(trip_id):
    with managed_cursor(mainConn) as cursor:
        trip = cursor.execute(getTrip, {"trip_id": trip_id}).fetchone()

    if trip is not None:
        trip = dict(trip)

    formattedGetUserLines = getUserLines.format(trip_ids=trip_id)
    with managed_cursor(pathConn) as cursor:
        pathResult = cursor.execute(formattedGetUserLines).fetchone()
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
        material_type=sanitize_param(trip["material_type"]),
        reg=sanitize_param(trip["reg"]),
        waypoints=sanitize_param(trip["waypoints"]),
        notes=sanitize_param(trip["notes"]),
        price=sanitize_param(trip["price"]),
        currency=sanitize_param(trip["currency"]),
        purchasing_date=sanitize_param(trip["purchasing_date"]),
        ticket_id=sanitize_param(trip["ticket_id"]),
        is_project=trip["start_datetime"] == 1 or trip["end_datetime"] == 1,
        path=path,
    )


def sanitize_param(param):
    return param if param != "" else None


def update_trip_values_from_form_data(trip_id, formData, update_created_ts=False):
    formattedGetUserLines = getUserLines.format(trip_ids=trip_id)
    with managed_cursor(pathConn) as cursor:
        pathResult = cursor.execute(formattedGetUserLines).fetchone()

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

    if "estimated_trip_duration" in formData and "trip_length" in formData:
        countries = getCountriesFromPath(
            [{"lat": coord[0], "lng": coord[1]} for coord in path], formData["type"]
        )
        estimated_trip_duration = sanitize_param(formData["estimated_trip_duration"])
        trip_length = sanitize_param(formData["trip_length"])
    else:
        countries = original_trip.countries
        estimated_trip_duration = original_trip.estimated_trip_duration
        trip_length = original_trip.trip_length

    created = datetime.now() if update_created_ts else original_trip.created

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
        material_type=sanitize_param(formData["material_type"]),
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
    )

    return trip


@app.route(
    "/<username>/hereRouteDisplay/<origin_name>/<destination_name>/<origin>/<destination>/<startDatetime>",
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
    trips = convert_here_response_to_trips(data, managed_cursor, mainConn)

    sortedTripList = sorted(
        trips,
        key=lambda d: d["trip"]["utc_filtered_start_datetime"],
        reverse=True,
    )

    # 4) Pass trips into the template as JSON
    trips_json = json.dumps(sortedTripList)

    return render_template(
        "here_routing.html",  # see below
        trips_json=trips_json,
        username=username,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route(
    "/<username>/googleRouteDisplay/<origin_name>/<destination_name>/<origin>/<destination>/<startDatetime>",
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
    trips = convert_google_response_to_trips(google_data, managed_cursor, mainConn)

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
def forwardRouting(path, routingType, args=None):
    if routingType in ("train", "tram", "metro"):
        routingType = "train"

    radiuses = None  # initialize in case ferry needs it later

    if routingType == "train":
        base = "http://routing.trainlog.me:5000"
    elif routingType == "ferry":
        base = "http://routing.trainlog.me:5001"
        coord_pairs = [
            {"lng": float(coord.split(",")[0]), "lat": float(coord.split(",")[1])}
            for coord in path.replace("route/v1/ferry/", "").split(";")
        ]
        radiuses = ";".join(["10000"] * len(coord_pairs))
    elif routingType == "aerialway":
        base = "http://routing.trainlog.me:5003"
    elif routingType == "car":
        base = "https://routing.openstreetmap.de/routed-car"
    elif routingType == "walk":
        base = "https://routing.openstreetmap.de/routed-foot"
    elif routingType == "cycle":
        base = "https://routing.openstreetmap.de/routed-bike"
    elif routingType == "bus":
        # Router base URLs with associated HTTP status codes used to inform the frontend
        # of which backend was used to compute the route:
        #   231 = Trainlog router
        #   232 = Chiel router
        #   233 = JKimb router
        #   234 = OSRM fallback (used directly if no match found)
        #   235 = Custom failure code indicating a router failed, and fallback to OSRM was triggered
        routers = {
            "trainlog": ("http://routing.trainlog.me:5002", 231),
            "chiel": ("https://busrouter.chiel.uk", 232),
            "jkimb": ("https://busrouter.jkimball.dev", 233),
            "fallback": ("https://routing.openstreetmap.de/routed-car", 234),
        }

        # Routing groups
        routing_groups = [
            {
                # Nordics + British Isles + Crown Deps
                "countries": {
                    "NO",
                    "SE",
                    "FI",
                    "DK",
                    "GB",
                    "IE",
                    "IS",
                    "IM",
                    "FO",
                    "GG",
                    "JE",
                },
                "router": routers["trainlog"],
            },
            {
                # Chiel: Central + Baltic + Western Europe
                "countries": {
                    "DE",
                    "AT",
                    "CH",
                    "LI",
                    "LU",  # DACH
                    "EE",
                    "LV",
                    "LT",  # Baltic
                    "FR",
                    "BE",
                    "NL",
                    "AD",
                    "MC",  # Western Europe
                    "PL",
                    "CZ",  # Central
                    "IT",
                    "ES",
                    "PT",  # Southern
                },
                "router": routers["chiel"],
            },
            {
                # North America
                "countries": {"US", "CA", "GL", "MX"},
                "router": routers["jkimb"],
            },
        ]

        # Parse coordinates from path
        coord_pairs = [
            {"lng": float(coord.split(",")[0]), "lat": float(coord.split(",")[1])}
            for coord in path.replace("route/v1/driving/", "").split(";")
        ]

        # Get country codes
        countries = []
        for wp in coord_pairs:
            try:
                countries.append(
                    getCountryFromCoordinates(wp["lat"], wp["lng"])["countryCode"]
                )
            except Exception:
                countries.append("UN")

        unique_countries = set(countries)

        # Determine base router
        base, return_code = routers["fallback"]
        for group in routing_groups:
            if unique_countries.issubset(group["countries"]):
                base, return_code = group["router"]
                break

    if not args:
        args = request.query_string.decode("utf-8")

    def build_url(base_url):
        full_url = f"{base_url}/{path}?{args}"
        if routingType == "ferry" and radiuses:
            full_url += f"&radiuses={radiuses}"
        return full_url

    # Only apply try/fallback logic for BUS routing
    if routingType == "bus":
        try:
            response = requests.get(build_url(base), timeout=5)
            if response.status_code != 200:
                raise Exception("Non-200 response")
            data = response.json()
            if data.get("status") == "NoRoute":
                raise Exception("Router responded with NoRoute")
            print(base)
            return make_response(response.json(), return_code)
        except Exception as e:
            print(f"Router failed: {base}, falling back to OSRM. Reason: {e}")
            fallback_url = build_url(routers["fallback"][0])
            return make_response(requests.get(fallback_url).json(), 235)
    else:
        # Other routing types: no fallback
        return requests.get(build_url(base)).text


latin_letters = {}


def is_latin(uchr):
    try:
        return latin_letters[uchr]
    except KeyError:
        return latin_letters.setdefault(uchr, "LATIN" in ud.name(uchr))


def only_roman_chars(unistr):
    return all(is_latin(uchr) for uchr in unistr if uchr.isalpha())


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


@app.route("/photon_status")
def router_status_photon():
    url = request.args.get("url")
    if not url:
        return jsonify({"status": "ERROR", "message": "Missing url"}), 400
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
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
        else:
            return jsonify(
                {"status": "DOWN", "message": f"HTTP {resp.status_code}"}
            ), resp.status_code
    except Exception as e:
        return jsonify({"status": "DOWN", "message": str(e)}), 500


@app.route("/airportAutocomplete/<searchPattern>")
def airportAutocomplete(searchPattern):
    with managed_cursor(mainConn) as cursor:
        airports = [
            dict(airport)
            for airport in cursor.execute(
                getAirports, {"searchPattern": "%" + searchPattern + "%"}
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
    with managed_cursor(mainConn) as cursor:
        trainStations = [
            dict(trainStation)
            for trainStation in cursor.execute(getTrainStations, params).fetchall()
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
    komoot = "https://photon.komoot.io/api"
    chiel = "https://photon.chiel.uk/api"  # Test Chiel's server
    args = request.query_string.decode("utf-8")
    timeout = 2
    en = "lang=en"

    primary = f"{chiel}?{args}&{en}"
    bkp = f"{komoot}?{args}&{en}"

    try:
        responseJson = requests.get(primary, timeout=timeout).json()
    except Exception:
        try:
            responseJson = requests.get(bkp).json()
        except Exception:
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


@app.route("/<username>/getManAndOps/<station_type>", methods=["GET", "POST"])
def getManAndOps(username, station_type):
    manualStations = {}
    visitedStations = {}
    with managed_cursor(mainConn) as cursor:
        for station in cursor.execute(
            getManualStationsQuery, (station_type,)
        ).fetchall():
            manualStations[station["name"]] = [
                [station["lat"], station["lng"]],
                station["name"],
            ]
    with managed_cursor(mainConn) as cursor:
        for station in cursor.execute(
            getNumberStations, {"trip_type": station_type, "username": username}
        ).fetchall():
            visitedStations[station["station"]] = station["total_occurrences"]
    tripType = station_type
    if tripType not in ["accommodation", "poi", "car"]:
        tripType = "operator"
    with managed_cursor(mainConn) as cursor:
        # Fetching operators from the database
        operators_from_db = [
            str(operator["operator"]).strip()
            for operator in cursor.execute(
                getOperators, {"username": username}
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

    manAndOps = {
        "operators": result,
        "manualStations": manualStations,
        "visitedStations": visitedStations,
    }
    return jsonify(manAndOps)


@app.route("/getAdminUsersData", methods=["POST"])
@owner_required
def getAdminUsersData():
    """
    Server-side processing endpoint for DataTables.
    Returns paginated user data based on DataTables parameters.
    """
    # Get DataTables parameters
    draw = int(request.form.get("draw", 1))
    start = int(request.form.get("start", 0))
    length = int(request.form.get("length", 10))
    search_value = request.form.get("search[value]", "")
    show_inactive = request.form.get("showInactive", "false") == "true"

    # Get sorting parameters
    order_column = int(request.form.get("order[0][column]", 1))
    order_dir = request.form.get("order[0][dir]", "asc")

    # Column mapping for sorting
    column_map = {
        1: "username",
        2: "lang",
        3: "active",
        4: "share_level",
        5: "trips",
        6: "length",
        7: "trips_per_day",
        8: "last_login",
        9: "email",
        10: "creation_date",
    }

    # Fetch all users (this could be optimized further with database-level pagination)
    all_users = {user.username: user.toDict() for user in User.query.all()}

    # Initialize trips and length for all users
    for user in all_users.values():
        user["trips"] = 0
        user["length"] = 0

    # Fetch users with trips from the main database
    with managed_cursor(mainConn) as cursor:
        for user_data in cursor.execute(adminStats).fetchall():
            username = user_data["username"]
            if username in all_users:
                user = all_users[username]
                user["trips"] = user_data["trips"]
                user["length"] = user_data["length"]

                if user["last_login"] is None:
                    user["last_login"] = datetime.strptime(
                        user_data["last_modified"], "%Y-%m-%d %H:%M:%S.%f"
                    )

    # Process users
    users_list = []
    for user in all_users.values():
        # Determine if user is active
        if (
            user["trips"] > 0
            and user["last_login"]
            and user["last_login"] > datetime.now() - timedelta(days=90)
            and user["username"] not in ["demo", "test"]
        ):
            user["active"] = True
        else:
            user["active"] = False

        # Filter by active status
        if not show_inactive and not user["active"]:
            continue

        # Calculate trips per day
        days_since_creation = (datetime.now() - user["creation_date"]).days or 1
        user["trips_per_day"] = round(user["trips"] / days_since_creation, 2)

        # Convert datetime objects to ISO format strings for JSON serialization
        if user.get("last_login"):
            user["last_login"] = user["last_login"].isoformat()
        if user.get("creation_date"):
            user["creation_date"] = user["creation_date"].isoformat()

        # Add to list
        users_list.append(user)

    # Apply search filter
    if search_value:
        search_lower = search_value.lower()
        users_list = [
            user
            for user in users_list
            if search_lower in user["username"].lower()
            or search_lower in user.get("email", "").lower()
            or search_lower in user.get("lang", "").lower()
        ]

    # Sort users
    if order_column in column_map:
        sort_key = column_map[order_column]
        reverse = order_dir == "desc"

        # Special handling for different data types
        if sort_key in ["trips", "length", "trips_per_day"]:
            users_list.sort(key=lambda x: x.get(sort_key, 0), reverse=reverse)
        elif sort_key in ["last_login", "creation_date"]:
            users_list.sort(
                key=lambda x: x.get(sort_key) or datetime.min, reverse=reverse
            )
        elif sort_key == "active":
            users_list.sort(key=lambda x: x.get(sort_key, False), reverse=reverse)
        else:
            users_list.sort(key=lambda x: x.get(sort_key, "").lower(), reverse=reverse)

    # Get total count before pagination
    total_filtered = len(users_list)

    # Apply pagination
    if length != -1:  # -1 means show all
        users_list = users_list[start : start + length]

    # Prepare response
    response = {
        "draw": draw,
        "recordsTotal": len(all_users),
        "recordsFiltered": total_filtered,
        "data": users_list,
    }

    return jsonify(response)


@app.route("/getAdminStats", methods=["GET"])
@owner_required
def getAdminStats():
    """
    Returns aggregated statistics without user data.
    This is called once on page load to populate the summary stats.
    """
    # Initialize counters and dictionaries
    active_users = 0
    active_today = 0
    total_trips = 0
    total_km = 0
    total_langs = {}
    active_langs = {}

    # Fetch all users from the auth database
    all_users = {user.username: user.toDict() for user in User.query.all()}
    total_users = len(all_users)

    # Initialize trips and length for all users
    for user in all_users.values():
        user["trips"] = 0
        user["length"] = 0

    # Fetch users with trips from the main database
    with managed_cursor(mainConn) as cursor:
        for user_data in cursor.execute(adminStats).fetchall():
            username = user_data["username"]
            if username in all_users:
                user = all_users[username]
                user["trips"] = user_data["trips"]
                user["length"] = user_data["length"]

                if user["last_login"] is None:
                    user["last_login"] = datetime.strptime(
                        user_data["last_modified"], "%Y-%m-%d %H:%M:%S.%f"
                    )

                # Check if user was active today
                if user["last_login"] > datetime.now() - timedelta(days=1):
                    active_today += 1

    # Calculate statistics
    for user in all_users.values():
        # Update total language count
        if user["lang"] in total_langs:
            total_langs[user["lang"]] += 1
        else:
            total_langs[user["lang"]] = 1

        # Check if the user is active
        if (
            user["trips"] > 0
            and user["last_login"]
            and user["last_login"] > datetime.now() - timedelta(days=90)
            and user["username"] not in ["demo", "test"]
        ):
            active_users += 1

            # Update active language count
            if user["lang"] in active_langs:
                active_langs[user["lang"]] += 1
            else:
                active_langs[user["lang"]] = 1

        # Update totals
        total_trips += user["trips"]
        total_km += user["length"]

    # Prepare response data
    response_data = {
        "stats": {
            "total_users": total_users,
            "active_users": active_users,
            "active_today": active_today,
            "total_trips": total_trips,
            "total_km": total_km,
            "langs": {"total": total_langs, "active": active_langs},
        }
    }

    return jsonify(response_data)


@app.route("/getLeaderboardUsers/<type>", methods=["GET"])
def getLeaderboardUsers(type):
    # Filter users with "leaderboard" set to True
    leaderboard_users = User.query.filter_by(leaderboard=True).all()
    user_list = [user.username for user in leaderboard_users]
    non_public_users = [
        username
        for username in user_list
        if not User.query.filter_by(username=username).first().is_public()
    ]

    if type not in ("train_countries", "world_squares", "country_count"):
        # Create a dictionary of leaderboard users with default values
        user_dict = {user.username: user.toDict() for user in leaderboard_users}
        for user in user_dict.values():
            user["trips"] = 0
            user["length"] = 0
            user["last_modified"] = None

        # Update the users with data from the adminStats query
        with managed_cursor(mainConn) as cursor:
            for user in cursor.execute(leaderboardStats).fetchall():
                username = user["username"]
                if username in user_dict and type == user["type"]:
                    user_dict[username]["trips"] = user["trips"]
                    user_dict[username]["length"] = user["length"]
                    if user["last_modified"] is not None:
                        user_dict[username]["last_modified"] = user["last_modified"]
        return jsonify(
            {
                "leaderboard_data": list(user_dict.values()),
                "non_public_users": non_public_users,
            }
        )
    elif type == "country_count":
        # Create a dictionary of leaderboard users with default values
        user_dict = {user.username: user.toDict() for user in leaderboard_users}
        for user in user_dict.values():
            user["country_count"] = 0
            user["countries_visited"] = {}

        # Update the users with data from the trip table
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                countriesLeaderboard.format(", ".join(["?" for _ in user_list])),
                user_list,
            )
            for row in cursor.fetchall():
                username = row["username"]
                countries = row["countries"]
                if username in user_dict:
                    trip_countries = json.loads(countries)
                    user_dict[username]["countries_visited"] = dict(
                        Counter(user_dict[username]["countries_visited"])
                        + Counter(trip_countries)
                    )
                    user_dict[username]["countries_visited"].pop("UN", None)

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
        return jsonify(
            {
                "leaderboard_data": leaderboard_data,
                "non_public_users": non_public_users,
            }
        )
    else:
        countries_dict = {}
        usernames_placeholders = ",".join(["?" for _ in user_list])
        with managed_cursor(mainConn) as cursor:
            for item in cursor.execute(
                getLeaderboardCountries.format(
                    usernames_placeholders=usernames_placeholders,
                    equals="==" if type == "world_squares" else "!=",
                ),
                user_list,
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


@app.route("/deleteUser/<int:uid>", methods=["POST"])
@owner_required
def delete_user(uid):
    """
    Deletes a user based on their unique user ID (uid).
    """
    user = User.query.get(uid)
    if not user:
        return ""

    try:
        with managed_cursor(mainConn) as cursor:
            idList = [
                row["uid"]
                for row in cursor.execute(
                    "SELECT uid FROM trip WHERE username=:username",
                    {"username": user.username},
                ).fetchall()
            ]

        formattedDeleteUserPath = deleteUserPath.format(
            trip_ids=", ".join(("?",) * len(idList))
        )
        with managed_cursor(pathConn) as cursor:
            cursor.execute(formattedDeleteUserPath, tuple(idList)).fetchall()
        with managed_cursor(mainConn) as cursor:
            cursor.execute(deleteUserTrips, {"username": user.username})
        authDb.session.delete(user)

        authDb.session.commit()
        pathConn.commit()
        mainConn.commit()
    except Exception as e:
        print(e)

    return ""


def fetchTripsPaths(username, lastLocal, public):
    tripList = []
    now = datetime.now()
    with managed_cursor(mainConn) as cursor:
        idList = [
            row["uid"]
            for row in cursor.execute(
                "SELECT uid FROM trip WHERE username=:username", {"username": username}
            ).fetchall()
        ]

        trips = cursor.execute(
            getUniqueUserTrips,
            {"username": username, "lastLocal": lastLocal, "public": public},
        ).fetchall()

    trips.reverse()
    tripIds = [trip["uid"] for trip in trips]
    print(public, len(tripIds))
    formattedGetUserLines = getUserLines.format(
        trip_ids=", ".join(("?",) * len(tripIds))
    )

    tripIds = []
    for trip in trips:
        tripIds.append(trip["uid"])
    formattedGetUserLines = getUserLines.format(
        trip_ids=", ".join(("?",) * len(tripIds))
    )

    tripIds = []
    for trip in trips:
        tripIds.append(trip["uid"])
    formattedGetUserLines = getUserLines.format(
        trip_ids=", ".join(("?",) * len(tripIds))
    )
    with managed_cursor(pathConn) as cursor:
        pathResult = cursor.execute(formattedGetUserLines, tuple(tripIds)).fetchall()

    paths = {path["trip_id"]: path["path"] for path in pathResult}

    for trip in trips:
        trip = dict(trip)
        trip.pop("past")
        trip.pop("plannedFuture")
        trip.pop("current")
        trip.pop("future")

        tripList.append(
            {"trip": trip, "path": json.loads(paths.get(trip["uid"], "{}"))}
        )

    print(datetime.now() - now)
    lastLocal = datetime.strftime(datetime.now(), "%Y-%m-%dT%H:%M:%S.%f")
    return {"trips": tripList, "lastLocal": lastLocal, "idList": idList}


@app.route("/public/<username>/getTripsPaths/<lastLocal>", methods=["GET", "POST"])
@public_required  # Public access check
def public_getTripsPaths(username, lastLocal):
    result = fetchTripsPaths(username, lastLocal, public=1)
    return jsonify(result)


@app.route("/<username>/getTripsPaths/<lastLocal>", methods=["GET", "POST"])
@login_required  # Login access check
def getTripsPaths(username, lastLocal):
    result = fetchTripsPaths(username, lastLocal, public=0)
    return jsonify(result)


@app.route("/<username>/getCurrentTrip", methods=["GET", "POST"])
@login_required
def getCurrentTripPath(username):
    with managed_cursor(mainConn) as cursor:
        trip = cursor.execute(getCurrentTrip, {"username": username}).fetchone()
    tripIds = [trip["uid"]]

    tripList = []

    formattedGetUserLines = getUserLines.format(
        trip_ids=", ".join(("?",) * len(tripIds))
    )
    with managed_cursor(pathConn) as cursor:
        pathResult = cursor.execute(formattedGetUserLines, tuple(tripIds)).fetchall()
    paths = {}
    for path in pathResult:
        paths[path["trip_id"]] = path["path"]

    for tripId in tripIds:
        with managed_cursor(mainConn) as cursor:
            trip = formatTrip(
                dict(cursor.execute(getTrip, {"trip_id": tripId}).fetchone())
            )
        user = User.query.filter_by(username=trip["username"]).first()
        if not session.get(user.username) and not user.is_public():
            abort(401)
        tripList.append(
            {
                "time": trip["time"],
                "trip": dict(trip),
                "path": json.loads(paths[trip["uid"]]),
                "distances": getDistanceFromPath(json.loads(paths[trip["uid"]])),
            }
        )
    sortedTripList = sorted(tripList, key=lambda d: d["trip"]["uid"], reverse=True)
    sortedTripList = sorted(
        sortedTripList, key=lambda d: d["trip"]["start_datetime"], reverse=True
    )
    return jsonify(sortedTripList)


def get_logo_url(operator, trip):
    operator_id = operator["uid"]
    utc_filtered_start_datetime = trip["utc_filtered_start_datetime"]
    with managed_cursor(mainConn) as cursor:
        if utc_filtered_start_datetime == -1:
            # Fetch the oldest logo
            logo = cursor.execute(
                """
                SELECT l.logo_url
                FROM operator_logos l
                WHERE l.operator_id = ?
                ORDER BY l.effective_date ASC
                LIMIT 1
            """,
                (operator_id,),
            ).fetchone()
        elif utc_filtered_start_datetime == 1:
            # Fetch the latest logo
            logo = cursor.execute(
                """
                SELECT l.logo_url
                FROM operator_logos l
                WHERE l.operator_id = ?
                ORDER BY l.effective_date DESC
                LIMIT 1
            """,
                (operator_id,),
            ).fetchone()
        else:
            # Fetch the logo closest to the trip start date
            logo = cursor.execute(
                """
                SELECT l.logo_url
                FROM operator_logos l
                WHERE l.operator_id = ?
                  AND (l.effective_date <= ? OR l.effective_date IS NULL)
                ORDER BY l.effective_date DESC
                LIMIT 1
            """,
                (operator_id, utc_filtered_start_datetime),
            ).fetchone()
    if logo:
        return logo["logo_url"]
    else:
        return None


def processPublicTrips(tripIds):
    user_currency = getLoggedUserCurrency()
    for trip in tripIds.split(","):
        with managed_cursor(mainConn) as cursor:
            trip = cursor.execute(getTrip, {"trip_id": trip}).fetchone()
        user = User.query.filter_by(username=trip["username"]).first()
        if (
            not session.get(user.username)
            and not user.is_public_trips()
            and not session.get(owner)
        ):
            abort(401)
    tripIds = tripIds.split(",")

    tripList = []

    formattedGetUserLines = getUserLines.format(
        trip_ids=", ".join(("?",) * len(tripIds))
    )
    with managed_cursor(pathConn) as cursor:
        pathResult = cursor.execute(formattedGetUserLines, tuple(tripIds)).fetchall()
    paths = {}
    for path in pathResult:
        paths[path["trip_id"]] = path["path"]

    total_price = 0
    for tripId in tripIds:
        with managed_cursor(mainConn) as cursor:
            trip = formatTrip(
                dict(cursor.execute(getTrip, {"trip_id": tripId}).fetchone())
            )

        # Process multi operator logos
        if "," in str(trip["operator"]):
            operator_names = trip["operator"]
            operator_list = [op.strip() for op in operator_names.split(",")]

            operator_logos = []
            for op_name in operator_list:
                with managed_cursor(mainConn) as cursor:
                    operator = cursor.execute(
                        "SELECT * FROM operators WHERE short_name = ?", (op_name,)
                    ).fetchone()
                if operator:
                    operator = dict(operator)
                    logo_url = get_logo_url(operator, trip)
                    operator_logos.append(
                        {"operator_name": operator["short_name"], "logo_url": logo_url}
                    )

            trip["multi_operators"] = operator_logos

            # Remove operator_name and logo_url from trip if they exist
            trip.pop("operator_name", None)
            trip.pop("logo_url", None)

        if trip["ticket_id"] not in (None, ""):
            with managed_cursor(mainConn) as cursor:
                cursor.execute(getTicket, (trip["ticket_id"],))
                ticket = cursor.fetchall()[0]
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

        user = User.query.filter_by(username=trip["username"]).first()
        if (
            not session.get(user.username)
            and not user.is_public_trips()
            and not session.get(owner)
        ):
            abort(401)
        tripList.append(
            {
                "time": trip["time"],
                "trip": dict(trip),
                "path": json.loads(paths[trip["uid"]]),
            }
        )
    sortedTripList = sorted(tripList, key=lambda d: d["trip"]["uid"], reverse=True)
    sortedTripList = sorted(
        sortedTripList,
        key=lambda d: d["trip"]["utc_filtered_start_datetime"],
        reverse=True,
    )
    priceDict = {"total_price": total_price, "user_currency": user_currency}
    return sortedTripList, priceDict


@app.route("/getPublicTrips", methods=["POST"])
def getPublicTrips():
    data = request.get_json()
    tripIds = data.get("tripIds")
    sortedTripList, priceDict = processPublicTrips(tripIds)
    for trip in sortedTripList:
        trip["trip"].pop("username")
    return jsonify([sortedTripList, priceDict])


@app.route("/<username>/toType/<tripType>/<tripIds>", methods=["GET"])
@login_required
def changeTripType(username, tripType, tripIds):
    # Define allowed type buckets
    allowed_buckets = {
        "car": {"car", "bus"},
        "bus": {"car", "bus"},
        "train": {"train", "tram", "metro"},
        "tram": {"train", "tram", "metro"},
        "metro": {"train", "tram", "metro"},
        "poi": {"poi", "accommodation", "restaurant"},
        "accommodation": {"poi", "accommodation", "restaurant"},
        "restaurant": {"poi", "accommodation", "restaurant"},
        "ferry": {},
        "walk": {},
        "cycle": {},
        "helicopter": {"air", "helicopter"},
        "air": {"air", "helicopter"},
        "aerialway": {},
    }

    # Fetch trips and verify permissions
    trips, _ = processPublicTrips(tripIds)
    if not trips:
        return jsonify({"error": "No trips found to update."}), 400

    # Check if all trips can be changed to the requested type
    for trip in trips:
        current_type = trip["trip"].get("type", "")
        if (
            current_type not in allowed_buckets
            or tripType not in allowed_buckets[current_type]
        ):
            return jsonify(
                {
                    "error": f"Cannot change trip type from '{current_type}' to '{tripType}'."
                }
            ), 400

    try:
        # Update each trip's type
        for trip in trips:
            with managed_cursor(mainConn) as cursor:
                cursor.execute(
                    "UPDATE trip SET type = :newType WHERE uid = :tripId",
                    {"newType": tripType, "tripId": trip["trip"]["uid"]},
                )
        mainConn.commit()

        return jsonify(
            {"message": "Trip types updated successfully", "updated_type": tripType}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/<username>/merge/<tripIds>", methods=["GET", "POST"])
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
    newTrip["waypoints"] = ""
    newTrip["notes"] = ""
    newTrip["onlyDateDuration"] = ""
    newTrip["originManualLat"] = None
    newTrip["originManualLng"] = None
    newTrip["destinationManualLat"] = None
    newTrip["destinationManualLng"] = None

    try:
        saveTripToDb(username, newTrip, final_path, tripType)
        return redirect(
            url_for("dynamic_trips", username=username, time="trips"), code=301
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/getMultiTrips/<tripIds>", methods=["GET", "POST"])
def getMultiTrips(tripIds):
    sortedTripList, priceDict = processPublicTrips(tripIds)
    userList = set()
    anonymous = {}
    for trip in sortedTripList:
        user = User.query.filter_by(username=trip["trip"]["username"]).first()
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


def fetch_stats(username, tripType, year=None):
    stats = {}
    with managed_cursor(mainConn) as cursor:
        for avType in cursor.execute(typeAvailable, {"username": username}).fetchall():
            if avType["type"] == tripType:
                stats["newOperators"] = {
                    "km": getStatsGeneral(
                        query=newStatsOperatorKm,
                        cursor=cursor,
                        username=username,
                        statName="operator",
                        tripType=tripType,
                        year=year,
                    ),
                    "trips": getStatsGeneral(
                        query=newStatsOperatorTrips,
                        cursor=cursor,
                        username=username,
                        statName="operator",
                        tripType=tripType,
                        year=year,
                    ),
                }
                stats["material"] = {
                    "km": getStatsGeneral(
                        query=statsMaterialKm,
                        cursor=cursor,
                        username=username,
                        statName="material",
                        tripType=tripType,
                        year=year,
                    ),
                    "trips": getStatsGeneral(
                        query=statsMaterialTrips,
                        cursor=cursor,
                        username=username,
                        statName="material",
                        tripType=tripType,
                        year=year,
                    ),
                }
                stats["countries"] = {
                    "km": getStatsCountries(
                        query=statsCountries,
                        cursor=cursor,
                        username=username,
                        km=True,
                        tripType=tripType,
                        year=year,
                    ),
                    "trips": getStatsCountries(
                        query=statsCountries,
                        cursor=cursor,
                        username=username,
                        km=False,
                        tripType=tripType,
                        year=year,
                    ),
                }
                stats["years"] = {
                    "km": getStatsYears(
                        query=statsYearKm,
                        cursor=cursor,
                        username=username,
                        lang=lang[session["userinfo"]["lang"]],
                        tripType=tripType,
                        year=year,
                    ),
                    "trips": getStatsYears(
                        query=statsYearTrips,
                        cursor=cursor,
                        username=username,
                        lang=lang[session["userinfo"]["lang"]],
                        tripType=tripType,
                        year=year,
                    ),
                }
                stats["newRoutes"] = {
                    "km": getStatsGeneral(
                        query=statsRoutesKm + " 10",
                        cursor=cursor,
                        username=username,
                        statName="route",
                        tripType=tripType,
                        year=year,
                    ),
                    "trips": getStatsGeneral(
                        query=statsRoutesTrips + " 10",
                        cursor=cursor,
                        username=username,
                        statName="route",
                        tripType=tripType,
                        year=year,
                    ),
                }
                stats["newStations"] = {
                    "km": getStatsGeneral(
                        query=statsStationsKm + " 10",
                        cursor=cursor,
                        username=username,
                        statName="station",
                        tripType=tripType,
                        year=year,
                    ),
                    "trips": getStatsGeneral(
                        query=statsStationsTrips + " 10",
                        cursor=cursor,
                        username=username,
                        statName="station",
                        tripType=tripType,
                        year=year,
                    ),
                }

    return stats


@app.route("/<username>/getStats/<year>/<tripType>", methods=["GET", "POST"])
@app.route("/<username>/getStats/<tripType>", methods=["GET", "POST"])
@public_required
def getStats(username, tripType, year=None):
    stats = fetch_stats(username, tripType, year)
    return jsonify(stats)


def getTrips(username, projects):
    tripList = []
    with managed_cursor(mainConn) as cursor:
        trips = list(cursor.execute(getUserTrips, (username,)).fetchall())
    if projects:
        trips.reverse()
    for trip in trips:
        trip = dict(trip)
        trip = formatTrip(trip)
        if (projects and (trip["future"] == 1 or trip["plannedFuture"] == 1)) or (
            not projects and trip["past"] == 1
        ):
            tripList.append(trip)

    return json.dumps(tripList)


def get_trips_api_internal(username, is_public=False):
    # Retrieve parameters from DataTables request
    start = request.form.get("start", type=int, default=0)
    length = request.form.get("length", type=int, default=10)
    search_value = request.form.get("search[value]", default="")
    draw = request.form.get("draw", type=int, default=1)  # For DataTables response
    past = int(request.args.get("projects") == "False")
    filter_types = request.form.get("filterTypes", type=int, default=0)

    # Force type filtering for public access
    if is_public:
        filter_types = 1

    # Sorting parameters
    sort_column = request.form.get(
        "order[0][column]", type=int, default=3
    )  # Default sort column
    sort_direction = request.form.get(
        "order[0][dir]", default="desc" if past == 1 else "asc"
    )  # Default sort direction
    column_names = [
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
        "price",
    ]
    sort_column_name = (
        column_names[sort_column]
        if 0 <= sort_column < len(column_names)
        else "default_column_name"
    )

    count_query = getDynamicUserTrips + "SELECT COUNT(*) FROM FilteredTrips"
    if filter_types:
        count_query += " WHERE type IN ('train', 'bus', 'air', 'ferry', 'helicopter', 'aerialway', 'tram', 'metro')"
    if sort_column_name != "start_datetime":
        data_query = getDynamicUserTrips + " SELECT * FROM FilteredTrips"
        if filter_types:
            data_query += " WHERE type IN ('train', 'bus', 'air', 'ferry', 'helicopter', 'aerialway', 'tram', 'metro')"
        data_query += (
            f" ORDER BY {sort_column_name} {sort_direction} LIMIT :limit OFFSET :offset"
        )
    else:
        data_query = getDynamicUserTrips + " SELECT * FROM FilteredTrips"
        if filter_types:
            data_query += " WHERE type IN ('train', 'bus', 'air', 'ferry', 'helicopter', 'aerialway', 'tram', 'metro')"
        data_query += f" ORDER BY utc_filtered_start_datetime = 1 {sort_direction}, utc_filtered_start_datetime {sort_direction}, uid {sort_direction} LIMIT :limit OFFSET :offset"

    mainConn.create_function("remove_diacritics", 1, remove_diacritics)

    # Ensure the sort direction is safe
    if sort_direction not in ["asc", "desc"]:
        sort_direction = "asc"

    with managed_cursor(mainConn) as cursor:
        # Fetch filtered count
        cursor.execute(
            count_query,
            {"username": username, "search": f"%{search_value}%", "past": past},
        )
        records_filtered = cursor.fetchone()[0]

        # Fetch the actual page data
        cursor.execute(
            data_query,
            {
                "username": username,
                "search": f"%{search_value}%",
                "limit": length,
                "offset": start,
                "past": past,
            },
        )
        trips = cursor.fetchall()

    # Convert trips to list of dictionaries
    trip_dicts = [dict(trip) for trip in trips]

    air_trip_uids = [
        trip["uid"] for trip in trip_dicts if trip["type"] in ("air", "helicopter")
    ]
    direct_flight_map = {}

    if air_trip_uids:
        with managed_cursor(pathConn) as path_cursor:
            path_cursor.execute(
                f"SELECT trip_id, json_extract(path, '$') as path_json FROM paths WHERE trip_id IN ({','.join(['?'] * len(air_trip_uids))})",
                air_trip_uids,
            )
            path_data = path_cursor.fetchall()
            for row in path_data:
                path_nodes = json.loads(row["path_json"]) if row["path_json"] else []
                direct_flight_map[row["trip_id"]] = len(path_nodes) == 2

    # Add is_geodesic flag to each trip
    for trip in trip_dicts:
        if trip["type"] in ("air", "helicopter"):
            trip["is_geodesic"] = direct_flight_map.get(trip["uid"], False)
        else:
            trip["is_geodesic"] = None

    # If public, remove price information
    if is_public:
        for trip in trip_dicts:
            trip.pop("price", None)

    # Format trips for display
    trip_list = [formatTrip(trip) for trip in trip_dicts]

    # Return the JSON for DataTables
    return jsonify(
        {
            "draw": draw,
            "recordsTotal": records_filtered,
            "recordsFiltered": records_filtered,
            "data": trip_list,
        }
    )


@app.route("/<username>/get_trips_api", methods=["POST"])
@login_required
def get_trips_api(username):
    return get_trips_api_internal(username, is_public=False)


@app.route("/<username>/get_trips_api_public", methods=["POST"])
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
    with managed_cursor(mainConn) as cursor:
        last_rate_date = cursor.execute(
            "SELECT rate_date FROM exchanges ORDER BY rate_date DESC LIMIT 1;"
        ).fetchone()

        if last_rate_date is not None:
            return jsonify(last_rate_date["rate_date"])
        else:
            return "None"


@app.route("/toggle_role/<int:uid>/<role>/<action>", methods=["POST", "GET"])
@owner_required
def toggle_role(uid, role, action):
    # Define a set of allowed roles to prevent arbitrary field manipulation
    allowed_roles = {"admin", "alpha", "translator", "premium"}

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

    authDb.session.commit()
    return jsonify(success=True)


@app.route("/<username>/settings", methods=["GET", "POST"])
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
        params["lang"] = request.form["lang"]
        params["user_currency"] = request.form["user_currency"]
        params["default_landing"] = request.form["default_landing"]
        params["tileserver"] = request.form["tileserver"]
        params["globe"] = "globe" in request.form

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
        user_currency=user.user_currency,
        default_landing=user.default_landing,
        user_tileserver=user.tileserver,
        user_globe=user.globe,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/dynamic/<time>")
def redirect_dynamic_trips(username, time):
    return redirect(url_for("dynamic_trips", username=username, time=time), code=301)


@app.route("/<username>/<time>")
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
        hasUncommonTrips=hasUncommonTrips(username),
        nav="bootstrap/navigation.html",
        isCurrent=isCurrentTrip(username),
        isPublic=False,
        projects=projects,
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
        hasUncommonTrips=hasUncommonTrips(username),
        nav="bootstrap/public_nav.html",
        isPublic=True,
        isCurrent=isCurrentTrip(username),
        projects=projects,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/stats/<tripType>")
@app.route("/admin/stats/<year>/<tripType>")
@app.route("/admin/stats")
@owner_required
def admin_stats(tripType=None, year=None):
    username = None  # For admin, username is None
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "SELECT distinct type from trip WHERE type NOT IN ('poi', 'accommodation', 'restaurant')"
        )
        types = {
            row[0]: lang[session["userinfo"]["lang"]][row[0]]
            for row in cursor.fetchall()
        }

    if tripType is None:
        return redirect(url_for("admin_stats", tripType="train", year=year))

    distinctStatYears = getDistinctStatYears(username, tripType)
    if year is not None and year not in distinctStatYears:
        return redirect(url_for("admin_stats", tripType=tripType, year=None))

    stats = fetch_stats(username, tripType, year)
    return render_template(
        "stats.html",  # Use a different template for admin stats
        nav="bootstrap/navigation.html",
        username=getUser(),  # This will be None for admin
        statYear=year,
        logosList=listOperatorsLogos(),
        tripType=tripType,
        admin=True,
        statsData=jsonify(stats).json,
        distinctTypes=types,
        distinctStatYears=distinctStatYears,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/stats/<year>/<tripType>")
@app.route("/<username>/stats/<tripType>")
@app.route("/<username>/stats")
@login_required
def stats(username, tripType=None, year=None):
    if tripType is None:
        return redirect(
            url_for("stats", username=username, tripType="train", year=year)
        )
    distinctStatYears = getDistinctStatYears(username, tripType)
    if year is not None and year not in distinctStatYears:
        return redirect(
            url_for("stats", username=username, tripType=tripType, year=None)
        )

    stats = fetch_stats(username, tripType, year)
    return render_template(
        "stats.html",
        nav="bootstrap/navigation.html",
        isCurrent=isCurrentTrip(username),
        is_public=False,
        title=lang[session["userinfo"]["lang"]]["stats"],
        username=username,
        statYear=year,
        logosList=listOperatorsLogos(),
        tripType=tripType,
        statsData=jsonify(stats).json,
        distinctStatYears=distinctStatYears,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/<edit_copy_type>/<tripId>")
def edit_copy_trip(username, tripId, edit_copy_type):
    """
    Edit or copy trip details
    """
    if "edit" in request.path:
        edit_copy_type = "edit"
    elif "copy" in request.path:
        edit_copy_type = "copy"

    formattedGetUserLines = getUserLines.format(
        trip_ids=", ".join(("?",) * len([tripId]))
    )
    with managed_cursor(mainConn) as cursor:
        trip = cursor.execute(getTrip, {"trip_id": tripId}).fetchone()
    with managed_cursor(pathConn) as cursor:
        path = json.loads(
            list(cursor.execute(formattedGetUserLines, (tripId,)).fetchone())[1]
        )
    user = User.query.filter_by(username=trip["username"]).first()
    if not (session.get(user.username) or session.get(owner)):
        abort(401)
    with managed_cursor(mainConn) as cursor:
        trip = cursor.execute(getTrip, {"trip_id": tripId}).fetchone()
    origin = trip["origin_station"]
    destination = trip["destination_station"]
    tripOperator = trip["operator"]
    tripLineName = trip["line_name"]
    tripMaterialType = trip["material_type"]
    tripSeat = trip["seat"]
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
    tripPurchasingDate = trip["purchasing_date"]
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

    return render_template(
        "edit_copy.html",
        title=lang[session["userinfo"]["lang"]][edit_copy_type],
        start_datetime=trip["start_datetime"],
        end_datetime=trip["end_datetime"],
        currencyOptions=get_available_currencies(),
        unknownType=unknownType,
        precision=precision,
        tripId=tripId,
        origin=origin,
        destination=destination,
        trip=trip,
        fr24_calls=fr24_usage(username),
        edit_copy_type=edit_copy_type,
        country_list=get_all_countries(),
        username=username,
        tripOperator=tripOperator or "",
        tripHours=tripHours or "",
        tripMinutes=tripMinutes or "",
        tripLineName=tripLineName or "",
        tripMaterialType=tripMaterialType or "",
        tripSeat=tripSeat or "",
        tripReg=tripReg or "",
        tripPrice=tripPrice or "",
        tripCurrency=tripCurrency or "",
        tripPurchasingDate=tripPurchasingDate or "",
        tripType=tripType,
        tripTicketId=tripTicketId or "",
        wplist=wplist,
        tripNotes=tripNotes or "",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/export")
@login_required
def export(username):
    requestedTrips = request.args.get("trips", default=None)

    si = StringIO()
    cw = csv.writer(si)
    with managed_cursor(mainConn) as cursor:
        if requestedTrips is None:
            cursor.execute(
                "SELECT * FROM trip WHERE username = :username", {"username": username}
            )
        else:
            query = "SELECT * FROM trip WHERE username = '{username}' AND uid IN ({requestedTrips})"
            formattedQuery = query.format(
                username=username,
                requestedTrips=", ".join(("?",) * len(requestedTrips.split(","))),
            )
            cursor.execute(formattedQuery, requestedTrips.split(","))
        rows = cursor.fetchall()
        cw.writerow(
            [i[0] for i in cursor.description if i[0] != "ticket_id"] + ["path"]
        )
        processedRows = []

        tripIds = []
        for row in rows:
            tripIds.append(row["uid"])

        formattedGetUserLines = getUserLines.format(
            trip_ids=", ".join(("?",) * len(tripIds))
        )
        with managed_cursor(pathConn) as cursor:
            pathResult = cursor.execute(
                formattedGetUserLines, tuple(tripIds)
            ).fetchall()
        paths = {}
        for path in pathResult:
            paths[path["trip_id"]] = path["path"]

        for row in rows:
            row = dict(row)
            row.pop("ticket_id")
            row["waypoints"] = json.dumps(row["waypoints"])
            row["operator"] = (
                row["operator"].replace(",", "&&")
                if row["operator"] not in (None, "")
                else row["operator"]
            )
            row["operator"] = (
                urllib.parse.quote(row["operator"])
                if row["operator"] not in (None, "")
                else row["operator"]
            )
            row["line_name"] = (
                urllib.parse.quote(row["line_name"])
                if row["line_name"] not in (None, "")
                else row["line_name"]
            )
            rowP = list(row.values())

            rowP.append(polyline.encode(json.loads(paths[row["uid"]])))
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


@app.route("/<username>/processMFR24", methods=["POST"])
@login_required
def processMFR24(username):
    newTrip = {}
    newPath = []
    if request.form != {}:
        data = list(request.form.to_dict().items())[0][0].split(",")

        newTrip["material_type"] = data[8].rsplit("(")[1].rsplit(")")[0]
        newTrip["seat"] = data[10]
        newTrip["reg"] = data[9]
        newTrip["notes"] = data[14]
        newTrip["price"] = newTrip["currency"] = newTrip["purchasing_date"] = None

        if "-" not in data[0]:
            # data[0] contains only a year
            data[0] = data[0] + "-01-01"  # Set to January 1st of that year
            data[4] = data[5] = "00:00:01"  # Set time to 00:00:01
            newTrip["precision"] = "onlyDate"
            newTrip["onlyDate"] = data[0]
            try:
                # Handle estimated trip duration if available
                hours, minutes, seconds = map(int, data[6].split(":"))
                newTrip["onlyDateDuration"] = hours * 3600 + minutes * 60 + seconds
            except (IndexError, ValueError):
                # Default duration if data[6] is missing or invalid
                newTrip["onlyDateDuration"] = 0
        else:
            # Existing code to handle full dates
            if data[4] == "00:00:00":
                data[4] = data[5] = "00:00:01"
                newTrip["precision"] = "onlyDate"
                newTrip["onlyDate"] = data[0]
                try:
                    hours, minutes, seconds = map(int, data[6].split(":"))
                    newTrip["onlyDateDuration"] = hours * 3600 + minutes * 60 + seconds
                except (IndexError, ValueError):
                    newTrip["onlyDateDuration"] = 0
            else:
                newTrip["newTripStart"] = (data[0] + "T" + data[4])[0:16]
                end_datetime = (data[0] + "T" + data[5])[0:16]
                if datetime.strptime(data[5], "%H:%M:%S") - datetime.strptime(
                    data[4], "%H:%M:%S"
                ) < timedelta(0):
                    end_datetime = datetime.strftime(
                        datetime.strptime(end_datetime, "%Y-%m-%dT%H:%M")
                        + timedelta(days=1),
                        "%Y-%m-%dT%H:%M",
                    )
                newTrip["newTripEnd"] = end_datetime
                newTrip["precision"] = "preciseDates"

        newTrip["lineName"] = data[1]
        origIata = data[2].rsplit("(")[-1].split("/")[0]
        destIata = data[3].rsplit("(")[-1].split("/")[0]

        timedeltaObj = datetime.strptime(data[6], "%H:%M:%S") - datetime(1900, 1, 1)
        newTrip["estimated_trip_duration"] = timedeltaObj.total_seconds()

        for iata, index in (
            (origIata, "originStation"),
            (destIata, "destinationStation"),
        ):
            with managed_cursor(mainConn) as cursor:
                airport = dict(
                    cursor.execute(
                        " SELECT * FROM airports WHERE iata = :searchPattern",
                        {"searchPattern": iata},
                    ).fetchone()
                )
            newTrip[index] = [
                [airport["latitude"], airport["longitude"]],
                "{} {} ({})".format(
                    flag(airport["iso_country"]), airport["name"], airport["iata"]
                ),
            ]
            newPath.append({"lat": airport["latitude"], "lng": airport["longitude"]})

        airline = data[7].strip('"').rsplit(" ", 1)[0]
        icao = data[7].strip('"').rsplit("/", 1)[1].replace(")", "")

        s = requests.Session()
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[502, 503, 504])
        s.mount("https://", HTTPAdapter(max_retries=retries))

        api_ninjas_config = load_config().get("api_ninjas", {})
        api_key = api_ninjas_config.get("api_key", "")
        api_url = "https://api.api-ninjas.com/v1/airlines?icao={}".format(icao)
        response = s.get(api_url, headers={"X-Api-Key": api_key})

        if response.status_code == requests.codes.ok:
            if response.json() != []:
                operatorAPI = response.json()[0]
                newTrip["operator"] = operatorAPI["name"]
                if "logo_url" in operatorAPI.keys():
                    newTrip["operatorLogoURL"] = operatorAPI["logo_url"]
            else:
                newTrip["operator"] = airline
        else:
            newTrip["operator"] = airline

        newTrip["trip_length"] = getDistance(newPath[0], newPath[1])

        options = {
            "orig": newTrip["originStation"][1],
            "dest": newTrip["destinationStation"][1],
            "username": username,
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
                datetime.strptime(newTrip["onlyDate"], "%Y-%m-%d")
                + timedelta(seconds=1),
                "%Y-%m-%d %H:%M:%S",
            )

        limits = [
            {
                "lat": newPath[0]["lat"],
                "lng": newPath[0]["lng"],
            },
            {
                "lat": newPath[-1]["lat"],
                "lng": newPath[-1]["lng"],
            },
        ]
        (
            manual_trip_duration,
            start_datetime,
            end_datetime,
            utc_start_datetime,
            utc_end_datetime,
        ) = processDates(newTrip, limits)
        countries = getCountriesFromPath(newPath, "air")
        now = datetime.now()

        with managed_cursor(mainConn) as cursor:
            sqlite_trip = cursor.execute(getDuplicate, options).fetchone()

        if sqlite_trip is not None:
            trip = get_trip(sqlite_trip["uid"])

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
            trip.estimated_trip_duration = sanitize_param(
                newTrip["estimated_trip_duration"]
            )
            trip.manual_trip_duration = manual_trip_duration
            trip.operator = sanitize_param(newTrip["operator"])
            trip.countries = sanitize_param(countries)
            trip.line_name = (sanitize_param(newTrip["lineName"]),)
            trip.last_modified = now
            trip.seat = sanitize_param(newTrip["seat"])
            trip.material_type = sanitize_param(newTrip["material_type"])
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
                estimated_trip_duration=sanitize_param(
                    newTrip["estimated_trip_duration"]
                ),
                manual_trip_duration=manual_trip_duration,
                operator=sanitize_param(newTrip["operator"]),
                countries=sanitize_param(countries),
                line_name=sanitize_param(newTrip["lineName"]),
                created=now,
                last_modified=now,
                type="air",
                seat=sanitize_param(newTrip["seat"]),
                material_type=sanitize_param(newTrip["material_type"]),
                reg=sanitize_param(newTrip["reg"]),
                waypoints=None,
                notes=sanitize_param(newTrip["notes"]),
                price=sanitize_param(newTrip["price"]),
                currency=sanitize_param(newTrip["currency"]),
                purchasing_date=sanitize_param(newTrip["purchasing_date"]),
                ticket_id=None,
                is_project=options["start_datetime"] == 1 or end_datetime == 1,
                path=newPath,
            )
            create_trip(trip)

        airlineLogoProcess(newTrip)

    return ""


@app.route("/getCountry", methods=["GET"])
def getCountry():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    return jsonify(getCountryFromCoordinates(lat, lng))


def getTimelineData(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            WITH UTC_Filtered AS (
                SELECT *,
                    CASE
                        WHEN utc_start_datetime IS NOT NULL AND utc_start_datetime NOT IN (-1, 1)
                        THEN utc_start_datetime
                        ELSE start_datetime
                    END AS utc_filtered_start_datetime,
                    CASE
                        WHEN utc_end_datetime IS NOT NULL AND utc_end_datetime NOT IN (-1, 1)
                        THEN utc_end_datetime
                        ELSE end_datetime
                    END AS utc_filtered_end_datetime
                FROM trip
            )
            SELECT origin_station, destination_station, utc_filtered_start_datetime, utc_filtered_end_datetime
            FROM UTC_Filtered
            WHERE username = :username
              AND utc_filtered_start_datetime NOT IN (-1, 1)
              AND utc_filtered_end_datetime NOT IN (-1, 1)
              AND origin_station IS NOT NULL
              AND destination_station IS NOT NULL
            ORDER BY utc_filtered_start_datetime
        """,
            {"username": username},
        )

        trips = cursor.fetchall()

    if not trips:
        return render_template("timeline.html", username=username, country_blocks=[])

    flag_set = set()
    trip_data = []

    for row in trips:
        start_datetime = datetime.fromisoformat(row["utc_filtered_start_datetime"])
        end_datetime = datetime.fromisoformat(row["utc_filtered_end_datetime"])
        origin_flag = row["origin_station"][:2]
        dest_flag = row["destination_station"][:2]
        flag_set.update([origin_flag, dest_flag])
        trip_data.append(
            {
                "start": start_datetime,
                "end": end_datetime,
                "origin_flag": origin_flag,
                "dest_flag": dest_flag,
            }
        )

    # Sort flags so each run is consistent
    sorted_flags = sorted(flag_set)

    # Generate distinct pastel-ish colors
    color_list = distinctipy.get_colors(len(sorted_flags), pastel_factor=0.7)

    # Convert to hex
    flag_colors = dict(zip(sorted_flags, [rgb_to_hex(c) for c in color_list]))

    # Build blocks
    blocks = []
    current_flag = trip_data[0]["origin_flag"]
    block_start = trip_data[0]["start"]

    for i, trip in enumerate(trip_data):
        origin_flag = trip["origin_flag"]
        dest_flag = trip["dest_flag"]
        start = trip["start"]
        end = trip["end"]

        # Transition if the origin of the next trip doesn't match destination of current
        next_origin_flag = (
            trip_data[i + 1]["origin_flag"] if i + 1 < len(trip_data) else None
        )

        # Close current block if trip ends in a new country
        if origin_flag != dest_flag or (
            next_origin_flag and dest_flag != next_origin_flag
        ):
            blocks.append(
                {
                    "flag": current_flag,
                    "start": block_start.isoformat(),
                    "end": end.isoformat(),
                    "color": flag_colors[current_flag],
                }
            )
            current_flag = dest_flag
            block_start = end

    # Close the last block
    blocks.append(
        {
            "flag": current_flag,
            "start": block_start.isoformat(),
            "end": str(datetime.now()),
            "color": flag_colors[current_flag],
        }
    )

    def daterange(start: datetime, end: datetime):
        current = start
        while current < end:
            yield current
            current += timedelta(days=1)

    # Step 1: track time per country per year
    country_time_by_year = defaultdict(
        lambda: defaultdict(float)
    )  # {year: {flag: seconds}}

    for block in blocks:
        start = datetime.fromisoformat(block["start"])
        end = datetime.fromisoformat(block["end"])
        flag = block["flag"]

        for single_day in daterange(start, end):
            year = single_day.year
            day_start = datetime.combine(single_day.date(), datetime.min.time())
            day_end = day_start + timedelta(days=1)

            overlap_start = max(start, day_start)
            overlap_end = min(end, day_end)

            seconds = (overlap_end - overlap_start).total_seconds()
            country_time_by_year[year][flag] += seconds

    # Step 2: determine residence country per year
    residence_country_by_year = {
        year: max(countries.items(), key=lambda x: x[1])[0]
        for year, countries in country_time_by_year.items()
    }

    # Step 3: compute time abroad per year
    seconds_abroad_by_year = {}
    for year, time_by_country in country_time_by_year.items():
        res_flag = residence_country_by_year[year]
        total = sum(time_by_country.values())
        seconds_abroad_by_year[year] = total - time_by_country[res_flag]

    # Step 4: convert to days
    days_abroad_by_year = {
        year: round(seconds / 86400, 1)
        for year, seconds in seconds_abroad_by_year.items()
    }
    return blocks, days_abroad_by_year, residence_country_by_year


@app.route("/<username>/timeline")
@login_required
def timeline(username):
    blocks, days_abroad_by_year, residence_country_by_year = getTimelineData(username)
    # Pass to the template
    return render_template(
        "timeline.html",
        username=username,
        country_blocks=blocks,
        days_abroad_by_year=days_abroad_by_year,
        residence_country_by_year=residence_country_by_year,
        nav="bootstrap/navigation.html",
        isCurrent=isCurrentTrip(getUser()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/public/<username>/timeline")
@public_required
def p_timeline(username):
    blocks, days_abroad_by_year, residence_country_by_year = getTimelineData(username)
    # Pass to the template
    return render_template(
        "timeline.html",
        username=username,
        country_blocks=blocks,
        days_abroad_by_year=days_abroad_by_year,
        residence_country_by_year=residence_country_by_year,
        nav="bootstrap/public_nav.html",
        isCurrent=isCurrentTrip(getUser()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/import", methods=["POST"])
@login_required
def importAll(username):
    if getUser() != username:
        abort(403)

    data = list(request.form.to_dict().items())[0][0]

    csvfile = StringIO(data)
    reader = csv.DictReader(csvfile)
    preprocessed_rows = (
        {k: (v if v != "" else None) for k, v in row.items()} for row in reader
    )
    dataDict = list(preprocessed_rows)[0]

    now = datetime.now()

    # Handle special cases
    if dataDict.get("uid"):
        dataDict.pop("uid")
    if dataDict.get("countries") is not None:
        dataDict["countries"] = (
            dataDict["countries"].replace(' "', ', "').replace(",,", ",")
        )
    if dataDict.get("waypoints") is not None:
        dataDict["waypoints"] = json.loads(dataDict["waypoints"])
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
        seat=sanitize_param(dataDict["seat"]),
        reg=sanitize_param(dataDict["reg"]),
        waypoints=sanitize_param(dataDict["waypoints"]),
        notes=sanitize_param(dataDict["notes"]),
        price=sanitize_param(dataDict["price"]),
        currency=sanitize_param(dataDict["currency"]),
        purchasing_date=sanitize_param(dataDict["purchasing_date"]),
        ticket_id=sanitize_param(dataDict["ticket_id"]),
        is_project=dataDict["start_datetime"] == 1 or dataDict["end_datetime"] == 1,
        path=path,
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
        datetime.strptime(start_date, "%Y-%m-%d %H:%M:%S")
        datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S")
        return "preciseDates"
    except ValueError:
        pass

    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.end_date(start_date, "%Y-%m-%d")
    return "onlyDate"


@app.route("/admin/manual")
@admin_required
def adminManual():
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT * FROM manual_stations")
        stationsList = cursor.fetchall()
    return render_template(
        "admin/manual.html",
        stationsList=stationsList,
        username=getUser(),
        nav="bootstrap/navigation.html",
        isCurrent=isCurrentTrip(getUser()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/ships", methods=["GET", "POST"])
@admin_required
def ships():
    if request.method == "POST":
        original_vessel_name = request.form.get("original_vessel_name")
        vessel_name = request.form.get("vessel_name")
        country_code = request.form.get("country_code")
        file = request.files.get("ship_picture")

        local_image_path = None

        # If a new image is uploaded, save it
        if file:
            filename = f"{country_code}_{vessel_name}.jpg".replace(" ", "_").replace(
                "/", ""
            )
            filepath = os.path.join("static/images/ship_pictures", filename)
            file.save(filepath)
            local_image_path = filename

        with managed_cursor(mainConn) as cursor:
            if original_vessel_name:  # If original_vessel_name is set, it's an update
                # Update ship data
                if local_image_path:  # If a new image was uploaded, update it
                    cursor.execute(
                        """
                        UPDATE ship_pictures
                        SET vessel_name = ?, country_code = ?, local_image_path = ?
                        WHERE vessel_name = ?
                        """,
                        (
                            vessel_name,
                            country_code,
                            local_image_path,
                            original_vessel_name,
                        ),
                    )
                else:  # If no new image, just update text fields
                    cursor.execute(
                        """
                        UPDATE ship_pictures
                        SET vessel_name = ?, country_code = ?
                        WHERE vessel_name = ?
                        """,
                        (vessel_name, country_code, original_vessel_name),
                    )
            else:  # Otherwise, it's an insert
                cursor.execute(
                    """
                    INSERT INTO ship_pictures (vessel_name, country_code, local_image_path)
                    VALUES (?, ?, ?)
                    """,
                    (vessel_name, country_code, local_image_path),
                )
            mainConn.commit()

        return jsonify({"success": True})

    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT * FROM ship_pictures")
        shipList = cursor.fetchall()

    return render_template(
        "admin/ships.html",
        shipList=shipList,
        username=getUser(),
        nav="bootstrap/navigation.html",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/admin/ships/delete", methods=["POST"])
@admin_required
def delete_ship():
    vessel_name = request.form.get("vessel_name")

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "DELETE FROM ship_pictures WHERE vessel_name = ?", (vessel_name,)
        )
        mainConn.commit()

    return jsonify({"success": True})


@app.route("/getAirliners")
def getAirliners():
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT * FROM airliners")
        airliners = [dict(row) for row in cursor.fetchall()]
    return jsonify(airliners)


@app.route("/admin/airliners/delete", methods=["POST"])
@admin_required
def delete_airliner():
    iata = request.form.get("iata")

    with managed_cursor(mainConn) as cursor:
        cursor.execute("DELETE FROM airliners WHERE iata = ?", (iata,))
        mainConn.commit()

    return jsonify({"success": True})


@app.route("/admin/airliners", methods=["GET", "POST"])
@admin_required
def airliners():
    if request.method == "POST":
        original_iata = request.form.get("original_iata")
        iata = request.form.get("iata")
        manufacturer = request.form.get("manufacturer")
        model = request.form.get("model")

        with managed_cursor(mainConn) as cursor:
            if original_iata:  # If original_iata is set, it's an update
                cursor.execute(
                    """
                    UPDATE airliners
                    SET iata = ?, manufacturer = ?, model = ?
                    WHERE iata = ?
                    """,
                    (iata, manufacturer, model, original_iata),
                )
            else:  # Otherwise, it's an insert
                cursor.execute(
                    """
                    INSERT INTO airliners (iata, manufacturer, model)
                    VALUES (?, ?, ?)
                    """,
                    (iata, manufacturer, model),
                )
            mainConn.commit()

        return jsonify({"success": True})

    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT * FROM airliners")
        airlinerList = cursor.fetchall()

    return render_template(
        "admin/airliners.html",
        airlinerList=airlinerList,
        username=getUser(),
        nav="bootstrap/navigation.html",
        isCurrent=isCurrentTrip(getUser()),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/importFlight")
def importFlight(username):
    return render_template(
        "import_flight.html",
        nav="bootstrap/navigation.html",
        username=username,
        title="Import Flight",
    )


@app.route("/<username>/upload_image", methods=["POST"])
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
@admin_required
def deleteManual(id):
    with managed_cursor(mainConn) as cursor:
        cursor.execute("DELETE FROM manual_stations WHERE uid=?", (id,))
    mainConn.commit()
    return redirect(url_for("adminManual"))


@app.route("/editStation/<int:id>", methods=["GET", "POST"])
@admin_required
def editStation(id):
    if request.method == "POST":
        action = request.form.get("action")

        if action == "delete":
            # Delete the station
            with managed_cursor(mainConn) as cursor:
                cursor.execute("DELETE FROM train_stations WHERE id=?", (id,))
            mainConn.commit()
            return redirect(url_for("stations"))
        else:
            # Update the station details
            with managed_cursor(mainConn) as cursor:
                cursor.execute(
                    """
                    UPDATE train_stations
                    SET name=?, latin_name=?, city=?, latin_city=?, country_code=?, latitude=?, longitude=?, processed_name=?
                    WHERE id=?
                """,
                    (
                        request.form.get("name"),
                        request.form.get("latin_name"),
                        request.form.get("city"),
                        request.form.get("latin_city"),
                        request.form.get("country_code"),
                        request.form.get("latitude"),
                        request.form.get("longitude"),
                        request.form.get("processed_name"),
                        id,
                    ),
                )
            mainConn.commit()
            return redirect(url_for("stations"))
    else:
        # Fetch the station details
        with managed_cursor(mainConn) as cursor:
            cursor.execute("SELECT * FROM train_stations WHERE id=?", (id,))
            station = cursor.fetchone()
        return render_template(
            "admin/edit_station.html",
            station=station,
            username=getUser(),
            nav="bootstrap/navigation.html",
            isCurrent=isCurrentTrip(getUser()),
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
    if search_value:
        search_terms = [f"{col} LIKE ?" for col in columns]
        where_clause = f"WHERE {' OR '.join(search_terms)}"
        search_value = f"%{search_value}%"

    # Count total records
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT COUNT(id) FROM train_stations")
        total_records = cursor.fetchone()[0]

    # Fetch filtered records
    query = f"SELECT * FROM train_stations {where_clause} {order_by_clause} LIMIT ? OFFSET ?"
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            query,
            ([search_value] * len(columns) if search_value else []) + [length, start],
        )
        stations = cursor.fetchall()

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

    # Count filtered records
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            f"SELECT COUNT(id) FROM train_stations {where_clause}",
            [search_value] * len(columns) if search_value else [],
        )
        filtered_records = cursor.fetchone()[0]

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
        username=session.get("logged_in"),
        nav="bootstrap/navigation.html",
        isCurrent=isCurrentTrip(session.get("logged_in")),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/editManual/<int:id>", methods=["GET", "POST"])
@admin_required
def editManual(id):
    with managed_cursor(mainConn) as cursor:
        if request.method == "POST":
            new_data = {
                "name": request.form.get("name"),
                "lat": request.form.get("lat"),
                "lng": request.form.get("lng"),
                "creator": request.form.get("creator"),
                "station_type": request.form.get("station_type"),
                "id": id,
            }
            cursor.execute(
                """
                UPDATE manual_stations
                SET name=:name, lat=:lat, lng=:lng, creator=:creator, station_type=:station_type
                WHERE uid=:id
            """,
                new_data,
            )
            mainConn.commit()
            return redirect(url_for("adminManual"))
        else:
            cursor.execute("SELECT * FROM manual_stations WHERE uid=?", (id,))
            station = cursor.fetchone()
            return render_template(
                "admin/edit_manual.html",
                station=station,
                username=session.get("logged_in"),
                nav="bootstrap/navigation.html",
                isCurrent=isCurrentTrip(session.get("logged_in")),
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
def handle_error(e):
    logger.error(e)
    error_code = getattr(e, "code", 500)  # Default to 500 if no code attribute
    template_data = {
        "errorTitle": lang[session["userinfo"]["lang"]].get(
            f"error{error_code}Title", "Error"
        ),
        "errorHeader": lang[session["userinfo"]["lang"]].get(
            f"error{error_code}Title", "Error"
        ),
        "errorImagePath": url_for("static", filename=f"images/errors/{error_code}.png"),
        "errorBody": lang[session["userinfo"]["lang"]].get(
            f"error{error_code}Body", "An error occurred."
        ),
    }
    if getUser() == "public":
        nav = "bootstrap/no_user_nav.html"
    else:
        nav = "bootstrap/navigation.html"
    return render_template(
        "errors.html",
        nav=nav,
        username=getUser(),
        **template_data,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    ), error_code


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
    else:
        template = "leaderboard.html"

    return render_template(
        template,
        nav=nav,
        username=getUser(),
        title="Leaderboard",
        type=type,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/getPublicStats")
def getPublicStats():
    with managed_cursor(mainConn) as cursor:
        stats = dict(cursor.execute(publicStats).fetchone())
    stats["users"] = len(User.query.all())
    print(stats)
    return jsonify(stats)


@app.route("/getVesselPhoto")
def getVesselPhoto():
    vesselName = request.args.get("vesselName")
    with managed_cursor(mainConn) as cursor:
        result = get_vessel_picture(vesselName, cursor)
    mainConn.commit()
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

    # SQL strftime format matching group_by
    group_by_format = {
        "year": "%Y",
        "month": "%Y-%m",
        "week": "%Y-%W",
        "day": "%Y-%m-%d",
    }[group_by]

    with managed_cursor(mainConn) as cursor:
        # Fetch and count all trips by type, grouping "poi", "restaurant", and "accommodation" under "poi"
        cursor.execute("""
            SELECT
                CASE
                    WHEN type IN ('poi', 'restaurant', 'accommodation') THEN 'poi'
                    ELSE type
                END as grouped_type,
                COUNT(*) as count
            FROM
                trip
            GROUP BY
                grouped_type
            ORDER BY
                count DESC;
        """)
        trip_type_counts = cursor.fetchall()
        trip_types = [row[0] for row in trip_type_counts]  # Sorted types

        # Fetch trips data grouped by the selected interval and type
        cursor.execute(f"""
            SELECT
                strftime('{group_by_format}', created) as date,
                CASE
                    WHEN type IN ('poi', 'restaurant', 'accommodation') THEN 'poi'
                    ELSE type
                END as grouped_type,
                COUNT(*) as count
            FROM
                trip
            WHERE created IS NOT NULL
            GROUP BY
                date, grouped_type
            ORDER BY
                date;
        """)
        trip_results = cursor.fetchall()

        # Fetch trips with 'None' created date and count them by grouped type
        cursor.execute("""
            SELECT
                CASE
                    WHEN type IN ('poi', 'restaurant', 'accommodation') THEN 'poi'
                    ELSE type
                END as grouped_type,
                COUNT(*) as count
            FROM
                trip
            WHERE created IS NULL
            GROUP BY
                grouped_type;
        """)
        trips_with_no_date = cursor.fetchall()

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

    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT json_group_array(DISTINCT username) from trip")
        users_with_trips = json.loads(cursor.fetchone()[0])

    results = (
        User.query.with_entities(
            group_func[group_by].label("date"),
            func.count(
                case(
                    [
                        (
                            and_(
                                User.last_login >= today - timedelta(days=90),
                                User.username.in_(users_with_trips),
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
                                User.username.notin_(users_with_trips),
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


@app.route("/admin/finances")
@admin_required
def finances():
    (
        labels,
        revenue_data_points,
        hosting_spending_data_points,
        translation_spending_data_points,
        api_subscription_spending_data_points,
        api_topup_spending_data_points,
        total_spending_data_points,
        profit_data_points,
        totals,
    ) = get_finances()
    return render_template(
        "admin/finances.html",
        labels=labels,
        revenue_data_points=revenue_data_points,
        hosting_spending_data_points=hosting_spending_data_points,
        translation_spending_data_points=translation_spending_data_points,
        api_subscription_spending_data_points=api_subscription_spending_data_points,
        api_topup_spending_data_points=api_topup_spending_data_points,
        total_spending_data_points=total_spending_data_points,
        profit_data_points=profit_data_points,
        totals=totals,
        username=getUser(),
        title="Finances",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/friends")
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
        title="Friends",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/<username>/cancelFriendship/<int:friendId>", methods=["GET"])
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


@app.route("/<username>/acceptFriendship/<int:friendId>", methods=["GET"])
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
    friendship.accepted = datetime.utcnow()

    # Create the reciprocal friendship record
    reciprocal_friendship = Friendship(
        user_id=user_id,  # The current user becomes the 'user_id'
        friend_id=friendId,  # The friend becomes the 'friend_id'
        accepted=datetime.utcnow(),  # Set the accepted date to now
    )
    authDb.session.add(reciprocal_friendship)

    authDb.session.commit()

    flash(
        lang[session["userinfo"]["lang"]]["friendFriendshipAccepted"], "success"
    )  # Ensure you have defined this message
    return redirect(url_for("friends", username=username))


@app.route("/<username>/requestFriend/<friendId>", methods=["GET"])
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
    user_id = User.query.filter_by(username=getUser()).first().uid
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


app.jinja_env.globals.update(getFriendsRequestsNumber=getFriendsRequestsNumber)


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
    image_path = "static/" + request.args.get("image_path").replace("%26", "&")

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


@app.route("/<username>/visited_squares")
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


@app.route("/<username>/visited_squares_data")
@public_required
def visited_squares_data(username):
    """Fetch the GeoJSON data for the visited squares."""
    geojson_data, land_percentage, air_percentage = generate_visited_squares_geojson(
        username
    )  # This is the updated function
    response = {
        "geojson": geojson_data,
        "land_percentage": land_percentage,
        "air_percentage": air_percentage,
    }
    return jsonify(response)  # Return the GeoJSON data and percentage as JSON


@app.route("/admin/active_users")
@admin_required
def active_users():
    with managed_cursor(mainConn) as cursor:
        cursor.execute("""
            SELECT date, number
            FROM daily_active_users
            ORDER BY date
        """)
        rows = cursor.fetchall()

    labels = [row[0] for row in rows]
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
    current_utc_datetime = datetime.now()

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
                SELECT uid, type
                FROM trip
                WHERE username = ?
                AND start_datetime NOT IN (1)
                AND (
                    CASE
                        WHEN utc_start_datetime IS NOT NULL THEN utc_start_datetime
                        ELSE start_datetime
                    END
                ) < ?
            """,
            (username, current_utc_datetime),
        )
        trips = cursor.fetchall()

        for trip in trips:
            trip_id = trip["uid"]
            trip_type = trip["type"]

            with managed_cursor(pathConn) as cursor:
                cursor.execute("SELECT path FROM paths WHERE trip_id = ?", (trip_id,))
                paths = cursor.fetchall()

            for path in paths:
                coordinates = json.loads(path["path"])

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

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            upsertPercent,
            {
                "username": username,
                "cc": "world_squares",
                "percent": round(land_percentage, 2),
            },
        )
    mainConn.commit()

    features = []
    for square, status in visited_squares.items():
        lat, lon = square
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [lon, lat],
                        [lon + 1, lat],
                        [lon + 1, lat + 1],
                        [lon, lat + 1],
                        [lon, lat],
                    ]
                ],
            },
            "properties": {"status": status},
        }
        features.append(feature)

    geojson_data = {"type": "FeatureCollection", "features": features}
    return geojson_data, land_percentage, air_percentage


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
    # Fetch all operators and their logos from the database
    with managed_cursor(mainConn) as cursor:
        cursor.execute("""
            SELECT
                o.uid,
                o.short_name,
                o.long_name,
                o.alias_of,
                o.operator_type,  -- New field
                l.logo_url
            FROM
                operators o
            LEFT JOIN operator_logos l ON l.operator_id = o.uid
                AND l.rowid = (
                    SELECT l1.rowid
                    FROM operator_logos l1
                    WHERE l1.operator_id = o.uid
                    ORDER BY
                        CASE WHEN l1.effective_date IS NULL THEN 1 ELSE 0 END ASC,
                        l1.effective_date DESC
                    LIMIT 1
                );
        """)
        operator_list = cursor.fetchall()

    return render_template(
        "admin/operators.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        operatorList=operator_list,
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


@app.route("/admin/operators", methods=["POST"])
@admin_required
def add_operator():
    short_name = request.form.get("short_name")
    long_name = request.form.get("long_name")
    alias_of = request.form.get("alias_of")
    operator_type = request.form.get("operator_type")
    logo = request.files.get("logo")
    error_log_name = "logs/operator_logo_save_errors.txt"
    log_name = "logs/operator_logo_log.txt"

    try:
        # Insert the operator into the database
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                """
                INSERT INTO operators (short_name, long_name, alias_of, operator_type)
                VALUES (?, ?, ?, ?)
            """,
                (short_name, long_name, alias_of if alias_of else None, operator_type),
            )

            operator_id = cursor.lastrowid  # Get the inserted operator ID

            # Handle the logo upload
            if logo:
                validate_png_file(logo)  # Validate the logo file
                filename = secure_filename(
                    f"{operator_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                )
                logo.save(os.path.join(LOGO_UPLOAD_FOLDER, filename))

                # Insert the logo into the operator_logos table
                cursor.execute(
                    """
                    INSERT INTO operator_logos (operator_id, logo_url)
                    VALUES (?, ?)
                """,
                    (operator_id, f"{REL_LOGO_UPLOAD_FOLDER}/{filename}"),
                )

        mainConn.commit()

        # Log the successful addition to the save log
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_name, "a", encoding="utf-8") as log:
            log.write(
                f"{current_time} - From: {getUser()} - Operator Added: {short_name} (ID: {operator_id})\n"
            )

        return jsonify(
            {"status": "success", "message": "Operator added successfully."}
        ), 200

    except Exception as e:
        # Log the error
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(error_log_name, "a", encoding="utf-8") as log:
            log.write(
                f"{current_time} - From: {getUser()} - Error: {e} - File: {short_name}\n"
            )
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/admin/operators/update", methods=["POST"])
@admin_required
def update_operator():
    uid = request.form.get("uid")
    field = request.form.get("field")
    value = request.form.get("value")
    log_name = "logs/operator_logo_log.txt"

    # Update the operator in the database
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            f"""
            UPDATE operators SET {field} = ? WHERE uid = ?
        """,
            (value, uid),
        )

    mainConn.commit()  # Commit the changes before returning the response

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_name, "a", encoding="utf-8") as log:
        log.write(
            f"{current_time} - From: {getUser()} - Operator Updated: Field '{field}' set to '{value}' (Operator ID: {uid})\n"
        )

    return jsonify({"status": "success", "message": "Operator updated successfully."})


@app.route("/admin/operators/upload-logo", methods=["POST"])
@admin_required
def upload_logo():
    uid = request.form.get("uid")
    logo = request.files.get("logo")
    effective_date = request.form.get("effective_date")
    action = request.form.get("action")
    error_log_name = "logs/operator_logo_save_errors.txt"
    log_name = "logs/operator_logo_log.txt"

    try:
        if logo:
            validate_png_file(logo)  # Validate the logo file

            # Save the new logo with a unique name using a timestamp
            filename = secure_filename(
                f"{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            )
            logo.save(os.path.join(LOGO_UPLOAD_FOLDER, filename))

            with managed_cursor(mainConn) as cursor:
                cursor.execute(
                    """
                    INSERT INTO operator_logos (operator_id, logo_url, effective_date)
                    VALUES (?, ?, ?)
                """,
                    (
                        uid,
                        f"{REL_LOGO_UPLOAD_FOLDER}/{filename}",
                        effective_date if effective_date else None,
                    ),
                )

            mainConn.commit()

            # Log the successful upload to the save log
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_name, "a", encoding="utf-8") as log:
                log.write(
                    f"{current_time} - From: {getUser()} - Logo {'Replaced' if action == 'replace' else 'Added'} for Operator ID: {uid} - Filename: {filename}\n"
                )

            return jsonify(
                {"status": "success", "message": "Logo uploaded successfully."}
            )

    except Exception as e:
        # Log the error
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(error_log_name, "a", encoding="utf-8") as log:
            log.write(
                f"{current_time} - From: {getUser()} - Error: {e} - Operator ID: {uid}\n"
            )
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/admin/operators/delete", methods=["POST"])
@admin_required
def delete_operator():
    uid = request.form.get("uid")

    # Delete the operator and their logos from the database
    with managed_cursor(mainConn) as cursor:
        cursor.execute("DELETE FROM operator_logos WHERE operator_id = ?", (uid,))
        cursor.execute("DELETE FROM operators WHERE uid = ?", (uid,))

        # Delete all logos related to this operator from the filesystem
        for file in os.listdir(LOGO_UPLOAD_FOLDER):
            if file.startswith(f"{uid}_"):
                logo_path = os.path.join(LOGO_UPLOAD_FOLDER, file)
                if os.path.exists(logo_path):
                    os.remove(logo_path)

    mainConn.commit()
    return jsonify(
        {
            "status": "success",
            "message": "Operator and associated logos deleted successfully.",
        }
    )


@app.route("/admin/operators/<int:uid>/logos", methods=["GET"])
@admin_required
def get_operator_logos(uid):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            SELECT uid, logo_url, effective_date FROM operator_logos WHERE operator_id = ? ORDER by effective_date
        """,
            (uid,),
        )
        logos = [
            {
                "uid": row["uid"],
                "logo_url": row["logo_url"],
                "effective_date": row["effective_date"],
            }
            for row in cursor.fetchall()
        ]

    return jsonify({"logos": logos})


@app.route("/admin/operators/delete-logo", methods=["POST"])
@admin_required
def delete_logo():
    logo_id = request.form.get("logo_id")
    print(logo_id)

    try:
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                """
                DELETE FROM operator_logos WHERE uid = ?
            """,
                (logo_id,),
            )
        mainConn.commit()
        return jsonify({"status": "success", "message": "Logo deleted successfully."})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


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
    with managed_cursor(mainConn) as cursor:
        for (name, logo_type), logo_url in logoURLs.items():
            # Insert into operators table
            cursor.execute(
                """
                INSERT INTO operators (short_name, long_name, operator_type)
                VALUES (?, ?, ?)
            """,
                (name, name, logo_type),
            )  # Store logo_type in operator_type field

            # Get the last inserted operator ID
            operator_id = cursor.lastrowid

            # Insert into operator_logos table
            cursor.execute(
                """
                INSERT INTO operator_logos (operator_id, logo_url, effective_date)
                VALUES (?, ?, ?)
            """,
                (operator_id, logo_url, None),
            )  # Set effective_date to None for now

    # Commit the changes
    mainConn.commit()

    return "Logos and types migrated successfully"


@app.route("/<username>/tll")
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


@app.route("/getBounds/<username>")
@login_required
def get_bounds(username):
    def get_location(lat, lon):
        try:
            response = requests.get(
                f"https://photon.komoot.io/reverse?lon={lon}&lat={lat}&lang=en"
            )
            if response.status_code == 200:
                data = response.json()
                if data["features"]:
                    properties = data["features"][0]["properties"]

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
                return {"location": "Unknown", "osm_link": None}
        except Exception:
            return {"location": "Unknown", "osm_link": None}

    # Dictionary to store boundary values
    bounds = {
        "north": {"coordinates": None, "place": None},
        "west": {"coordinates": None, "place": None},
        "south": {"coordinates": None, "place": None},
        "east": {"coordinates": None, "place": None},
    }

    with managed_cursor(mainConn) as main_cursor:
        # Fetch all trip IDs for the user
        main_cursor.execute(
            """
            WITH UTC_Filtered AS (
                    SELECT *,
                    CASE
                        WHEN utc_start_datetime IS NOT NULL
                        THEN utc_start_datetime
                        ELSE start_datetime
                    END AS 'utc_filtered_start_datetime'
                    FROM trip
                )

            SELECT uid from UTC_Filtered
                WHERE
                    (
                        julianday('now') > julianday(utc_filtered_start_datetime)
                        OR utc_filtered_start_datetime = -1
                    )
                    AND utc_filtered_start_datetime != 1
                AND type != 'ferry'
                AND username = :username
            """,
            {"username": username},
        )
        trip_ids = [row[0] for row in main_cursor.fetchall()]

    if not trip_ids:
        return jsonify({"error": "No trips found for this user"}), 404

    with managed_cursor(pathConn) as path_cursor:
        # Fetch all paths associated with the user's trips using IN
        path_cursor.execute(
            f"SELECT path FROM paths WHERE trip_id IN ({','.join(['?'] * len(trip_ids))})",
            trip_ids,
        )
        paths = path_cursor.fetchall()

    if not paths:
        return jsonify({"error": "No paths found for this user's trips"}), 404

    # Process each path to update the boundary values
    for path_row in paths:
        path = json.loads(path_row[0])  # path is a list of lists with coordinates
        for coord in path:
            lat, lon = coord
            # Update bounds with coordinates and place information
            if (
                bounds["north"]["coordinates"] is None
                or lat > bounds["north"]["coordinates"][0]
            ):
                bounds["north"]["coordinates"] = (lat, lon)
            if (
                bounds["west"]["coordinates"] is None
                or lon < bounds["west"]["coordinates"][1]
            ):
                bounds["west"]["coordinates"] = (lat, lon)
            if (
                bounds["south"]["coordinates"] is None
                or lat < bounds["south"]["coordinates"][0]
            ):
                bounds["south"]["coordinates"] = (lat, lon)
            if (
                bounds["east"]["coordinates"] is None
                or lon > bounds["east"]["coordinates"][1]
            ):
                bounds["east"]["coordinates"] = (lat, lon)

    # Fetch place names for each boundary using the stored coordinates
    for direction in bounds:
        coords = bounds[direction]["coordinates"]
        if coords:
            lat, lon = coords
            bounds[direction]["place"] = get_location(lat, lon)

    # Final response
    return jsonify(bounds), 200


@app.route("/<username>/bounds")
@login_required
def user_bounds(username):
    # Use your existing translations logic
    return render_template(
        "bounds.html",
        username=username,
        translations=lang[session["userinfo"]["lang"]],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/status")
def router_status():
    # Use your existing translations logic
    return render_template(
        "status.html",
        username=getUser(),
        translations=lang[session["userinfo"]["lang"]],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/public/current_trips")
def get_public_current_trips():
    # 1. Get all trips that are currently in progress
    with managed_cursor(mainConn) as cursor:
        cursor.execute("""
            SELECT *
            FROM trip
            WHERE utc_start_datetime <= dateTime() AND utc_end_datetime >= dateTime()
            AND type not in ('poi', 'accommodation', 'restaurant', 'walk', 'cycle', 'car')
        """)
        trips = cursor.fetchall()
    if not trips:
        return jsonify([])

    # 2. Collect usernames
    usernames = {trip["username"] for trip in trips}

    # 3. Batch fetch public users via SQLAlchemy
    public_users = {
        u.username
        for u in User.query.filter(
            User.username.in_(usernames), User.appear_on_global
        ).all()
    }

    # 4. Filter trips to only public users
    public_trips = [trip for trip in trips if trip["username"] in public_users]
    if not public_trips:
        return jsonify([])

    trip_ids = [trip["uid"] for trip in public_trips]

    # 5. Get paths
    formattedGetUserLines = getUserLines.format(
        trip_ids=", ".join(("?",) * len(trip_ids))
    )
    with managed_cursor(pathConn) as cursor:
        pathResult = cursor.execute(formattedGetUserLines, tuple(trip_ids)).fetchall()
    paths = {path["trip_id"]: path["path"] for path in pathResult}

    result = []
    for trip in public_trips:
        path = json.loads(paths.get(trip["uid"], "[]"))
        result.append(
            {
                "username": trip["username"],
                "trip": dict(trip),
                "path": path,
                "distances": getDistanceFromPath(path),
            }
        )
    return jsonify(result)


@app.route("/live_map")
def live_map():
    """
    Shows the global map of all public users currently traveling
    """
    return render_template(
        "public/current_global.html",
        username=getUser(),
        logosList=listOperatorsLogos(),
        translations=lang[session["userinfo"]["lang"]],
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@app.route("/api/user_completion/<username>")
@login_required
def user_completion(username):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            SELECT cc, percent
            FROM percents
            WHERE percent > 0 AND username = ?
            ORDER BY cc, percent DESC
            """,
            (username,),
        )
        rows = cursor.fetchall()

    countries = []
    regions = []

    for row in rows:
        entry = {"cc": row["cc"], "percent": row["percent"]}
        if len(row["cc"]) == 2:
            countries.append(entry)
        else:
            regions.append(entry)

    return jsonify({"countries": countries, "regions": regions})


@app.route("/<username>/dashboard")
@login_required
def user_dashboard(username):
    return render_template(
        "dashboard.html",
        username=username,
        nav="bootstrap/navigation.html",
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


if not database_exists(authDb.get_engine().url):
    create_authDb()
init_main(DbNames.MAIN_DB)
init_data(DbNames.MAIN_DB)
authDb.create_all()
with managed_cursor(pathConn) as cursor:
    cursor.execute(initPath)

setup_db()
