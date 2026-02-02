import json
import logging
import re
import smtplib
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from email.mime.text import MIMEText
from functools import wraps
from glob import glob
from inspect import getcallargs

import pytz
import requests
from flask import abort, redirect, request, session, url_for
from timezonefinder import TimezoneFinder

from py.utils import load_config
from src.consts import DbNames
from src.pg import pg_session
from src.sql.trips import get_current_trip_query
from src.users import Friendship, User, authDb

logger = logging.getLogger(__name__)

pathConn = sqlite3.connect(DbNames.PATH_DB.value, check_same_thread=False)
pathConn.row_factory = sqlite3.Row

mainConn = sqlite3.connect(DbNames.MAIN_DB.value, check_same_thread=False)
mainConn.row_factory = sqlite3.Row

authConn = sqlite3.connect(DbNames.AUTH_DB.value, check_same_thread=False)
authConn.row_factory = sqlite3.Row

owner = load_config()["owner"]["username"]


def getNameFromPath(path):
    return re.search(r"[A-Za-z0-9_\-.]+(?=\.[A-Za-z0-9]+$)", path).group(0)


def readLang():
    languages = {}
    for lang_path in glob("lang/*.json"):
        with open(lang_path, "r", encoding="utf-8") as lang:
            languages[getNameFromPath(lang_path)] = json.loads(lang.read())
    return languages


lang = readLang()


@contextmanager
def managed_cursor(connection):
    cursor = connection.cursor()
    try:
        yield cursor
    finally:
        cursor.close()


def owner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get(owner):
            abort(401)
        return f(*args, **kwargs)

    return decorated_function


def getUser():
    return session.get("logged_in") if session.get("logged_in") else "public"


# Test
def get_user_id(username: str | None = None) -> int | None:
    """
    Get the specified user's id, or the logged-in user's id if username is not specified.

    :param username: the username of the user to query, :code:`None` to query the currently logged-in user.
    :return: the user's id, or :code:`None` if the user is not logged-in or if the specified user does not exist.
    """
    if username is None:
        return (
            session.get("logged_in_user_id")
            if session.get("logged_in_user_id")
            else None
        )
    else:
        user = User.query.filter_by(username=username).first()
        return user.uid if user is not None else None


def get_username(user_id: int | None = None) -> str | None:
    """
    Get the specified user' s ussername, or the logged-in user's username if user_id is not specified.

    :param user_id: the id of the user to query, :code:`None` to query the currently logged-in user.
    :return: the user's username, or :code:`None` if the user is not logged-in or if the specified user does not exist.
    """
    if user_id is None:
        return session.get("logged_in") if session.get("logged_in") else None
    else:
        user = User.query.filter_by(uid=user_id).first()
        return user.username if user is not None else None


def has_current_trip(user_id: int | None = None) -> bool:
    """
    Whether the specified user has a current trip, or whether the currently logged in user has a current trip when user_id is not specified.
    """
    if not user_id:
        return False

    with pg_session() as pg:
        trip = pg.execute(get_current_trip_query(), {"user_id": user_id}).fetchone()
    return trip is not None


def processDates(new_trip, new_path):
    man_duration = utc_start_datetime = utc_end_datetime = None
    if new_trip["precision"] == "preciseDates":
        start_datetime = datetime.strptime(new_trip["newTripStart"], "%Y-%m-%dT%H:%M")
        end_datetime = datetime.strptime(new_trip["newTripEnd"], "%Y-%m-%dT%H:%M")
        utc_start_datetime = getUtcDatetime(dateTime=start_datetime, **new_path[0])
        utc_end_datetime = getUtcDatetime(dateTime=end_datetime, **new_path[-1])

    elif new_trip["precision"] == "onlyDate":
        start_datetime = datetime.strptime(
            new_trip["onlyDate"] + "T00:00:01", "%Y-%m-%dT%H:%M:%S"
        )
        end_datetime = datetime.strptime(
            new_trip["onlyDate"] + "T00:00:01", "%Y-%m-%dT%H:%M:%S"
        )
        if new_trip.get("onlyDateDuration") != "":
            man_duration = new_trip.get("onlyDateDuration")

    else:
        if new_trip.get("onlyDateDuration") != "":
            man_duration = new_trip.get("onlyDateDuration")
        if new_trip["unknownType"] == "past":
            start_datetime = end_datetime = -1
        else:
            start_datetime = end_datetime = 1
    return (
        man_duration,
        start_datetime,
        end_datetime,
        utc_start_datetime,
        utc_end_datetime,
    )


def getUtcDatetime(lat, lng, dateTime):
    tf = TimezoneFinder()
    timezone_str = tf.timezone_at(lat=lat, lng=lng)

    # Handle override for specific zones
    if timezone_str in ["Asia/Urumqi", "Asia/Kashgar"]:
        # Force UTC+8 manually
        timezone = pytz.FixedOffset(480)  # 480 minutes = 8 hours
        localized_datetime = timezone.localize(dateTime)
    else:
        timezone = pytz.timezone(timezone_str)
        localized_datetime = timezone.localize(dateTime)

    utc_datetime = localized_datetime.astimezone(pytz.utc).replace(tzinfo=None)
    return utc_datetime


def getLocalDatetime(lat, lng, dateTime):
    # Instantiate TimezoneFinder and find timezone for given lat, lng
    tf = TimezoneFinder()
    timezone_str = tf.timezone_at(lat=lat, lng=lng)

    if timezone_str in ["Asia/Urumqi", "Asia/Kashgar"]:
        local_timezone = pytz.FixedOffset(480)  # 480 minutes = 8 hours
    else:
        local_timezone = pytz.timezone(timezone_str)
    local_datetime = dateTime.astimezone(local_timezone).replace(tzinfo=None)
    return local_datetime


def sendEmail(address, subject, message):
    config = load_config()

    try:
        server = smtplib.SMTP(config["smtp"]["server"], config["smtp"]["port"])

        server.starttls()  # Secure the connection
        server.login(config["smtp"]["user"], config["smtp"]["password"])

        msg = MIMEText(message, "html")
        msg["From"] = config["smtp"]["user"]
        msg["To"] = address
        msg["Subject"] = subject
        server.sendmail(msg["From"], msg["To"], msg.as_string())

        server.quit()

    except Exception as e:
        print("Error:", e)


def sendEmailToUser(user_id, subject, message):
    try:
        user = User.query.filter_by(uid=user_id).first()
        if not user:
            print(f"User with ID {user_id} not found.")
            return False

        sendEmail(user.email, subject, message)
        return True

    except Exception as e:
        print("Error sending email to user:", e)
        return False


def sendOwnerEmail(subject, message):
    address = load_config()["owner"]["email"]
    if "127.0.0.1" in request.url or "localhost" in request.url:
        print(f"Email to: {address}\nSubject: {subject}\nMessage: {message}")
    else:
        sendEmail(address, subject, message)


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


def post_to_discord(
    webhook_type,
    title,
    description,
    url=None,
    fields=None,
    color=0x5865F2,
    footer_text=None,
):
    try:
        webhook_url = load_config()["discord"][webhook_type]
        embed_data = {
            "embeds": [
                {
                    "title": title,
                    "description": description[
                        :4096
                    ],  # Discord's max description length
                    "color": color,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ]
        }

        # Add URL if provided (makes title clickable)
        if url:
            embed_data["embeds"][0]["url"] = url

        # Add fields if provided
        if fields:
            embed_data["embeds"][0]["fields"] = fields

        # Add footer if provided
        if footer_text:
            embed_data["embeds"][0]["footer"] = {"text": footer_text}

        response = requests.post(
            webhook_url,
            data=json.dumps(embed_data),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )

        return response.status_code == 204

    except Exception as e:
        print(f"Discord webhook failed: {e}")
        return False


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
        if not session.get("logged_in") or not session.get("logged_in_user_id"):
            return redirect(url_for("login", next=request.path))

        inspection = getcallargs(f, *args, **kwargs)
        username = inspection["username"]
        user = User.query.filter_by(username=username).first()
        if user is None:
            abort(404)
        elif not (session.get(username) or session.get(owner)):
            abort(401)

        user.last_login = datetime.now(UTC)
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
    month_key = datetime.now(UTC).strftime("%Y-%m")

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

    month_key = datetime.now(UTC).strftime("%Y-%m")

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

def check_and_increment_ai_usage(username, count=1, limit=10):
    month_key = datetime.utcnow().strftime("%Y-%m")
    is_premium = bool(User.query.filter_by(username=username).first().premium)
    
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            SELECT ai_calls FROM ai_usage
            WHERE username = ? AND month_year = ?
            """,
            (username, month_key),
        )
        row = cursor.fetchone()
        if row:
            ai_calls = row[0]
        else:
            ai_calls = 0
            cursor.execute(
                """
                INSERT INTO ai_usage (username, month_year, ai_calls)
                VALUES (?, ?, 0)
                ON CONFLICT DO NOTHING
                """,
                (username, month_key),
            )
        
        # Check limit before incrementing (for non-premium)
        if not is_premium and ai_calls + count > limit:
            return False
        
        # Increment usage
        cursor.execute(
            """
            UPDATE ai_usage
            SET ai_calls = ai_calls + ?
            WHERE username = ? AND month_year = ?
            """,
            (count, username, month_key),
        )
        mainConn.commit()
    
    return True


def ai_usage(username):
    if User.query.filter_by(username=username).first().premium:
        return "premium"
    month_key = datetime.utcnow().strftime("%Y-%m")
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            """
            SELECT ai_calls
            FROM ai_usage
            WHERE username = ? AND month_year = ?
            """,
            (username, month_key),
        )
        row = cursor.fetchone() or (0,)
    return row[0]

def get_default_trip_visibility(x):
    match x:
        case "accommodation":
            return "private"
        case "aerialway":
            return "public"
        case "bus":
            return "public"
        case "car":
            return "private"
        case "cycle":
            return "private"
        case "ferry":
            return "public"
        case "helicopter":
            return "public"
        case "metro":
            return "public"
        case "poi":
            return "private"
        case "restaurant":
            return "private"
        case "train":
            return "public"
        case "tram":
            return "public"
        case "walk":
            return "private"
        case _:
            return "public"


def current_user_is_friend_with(target_username):
    current_user = getUser()
    if current_user != "public":
        current_user_id = User.query.filter_by(username=current_user).first().uid
        target_user_id = (
            authDb.session.query(User.uid, User.username)
            .filter(User.username == target_username)
            .first()
            .uid
        )
        return (
            current_user_id == target_user_id
            or (
                authDb.session.query(User.uid, User.username)
                .join(Friendship, User.uid == Friendship.friend_id)
                .filter(
                    Friendship.user_id == target_user_id,
                    Friendship.friend_id == current_user_id,
                    Friendship.accepted is not None,
                )
                .first()
            )
            is not None
        )
    else:
        return 0


def parse_date(date: str):
    try:
        return datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    try:
        return datetime.strptime(date, "%Y-%m-%d %H:%M:%S.%f")
    except Exception:
        pass
    try:
        return datetime.strptime(date, "%Y/%m/%d %H:%M:%S")
    except Exception:
        pass
    try:
        return datetime.strptime(date, "%d/%m/%Y %H:%M")
    except Exception:
        pass
    try:
        return datetime.strptime(date, "%Y-%m-%d")
    except Exception:
        logger.error(f"Date format not recognized: {date} ({type(date)})")
        raise
