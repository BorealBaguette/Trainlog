import json
import re
import smtplib
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from email.mime.text import MIMEText
from functools import wraps
from glob import glob

import pytz
from flask import abort, request, session
from timezonefinder import TimezoneFinder

from py.sql import getCurrentTrip
from py.utils import load_config
from src.consts import DbNames

pathConn = sqlite3.connect(DbNames.PATH_DB, check_same_thread=False)
pathConn.row_factory = sqlite3.Row

mainConn = sqlite3.connect(DbNames.MAIN_DB, check_same_thread=False)
mainConn.row_factory = sqlite3.Row

authConn = sqlite3.connect(DbNames.AUTH_DB, check_same_thread=False)
authConn.row_factory = sqlite3.Row


owner = load_config()["owner"]["username"]


def getNameFromPath(path):
    return re.search(r"[A-Za-z0-9_\-\.]+(?=\.[A-Za-z0-9]+$)", path).group(0)


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


def isCurrentTrip(username):
    with managed_cursor(mainConn) as cursor:
        trip = cursor.execute(getCurrentTrip, {"username": username}).fetchone()
    if trip is not None:
        return True
    else:
        return False


def processDates(newTrip, newPath):
    manDuration = utc_start_datetime = utc_end_datetime = None
    if newTrip["precision"] == "preciseDates":
        start_datetime = datetime.strptime(newTrip["newTripStart"], "%Y-%m-%dT%H:%M")
        end_datetime = datetime.strptime(newTrip["newTripEnd"], "%Y-%m-%dT%H:%M")
        utc_start_datetime = getUtcDatetime(dateTime=start_datetime, **newPath[0])
        utc_end_datetime = getUtcDatetime(dateTime=end_datetime, **newPath[-1])

    elif newTrip["precision"] == "onlyDate":
        start_datetime = datetime.strptime(
            newTrip["onlyDate"] + "T00:00:01", "%Y-%m-%dT%H:%M:%S"
        )
        end_datetime = datetime.strptime(
            newTrip["onlyDate"] + "T00:00:01", "%Y-%m-%dT%H:%M:%S"
        )
        if newTrip.get("onlyDateDuration") != "":
            manDuration = newTrip.get("onlyDateDuration")

    else:
        if newTrip.get("onlyDateDuration") != "":
            manDuration = newTrip.get("onlyDateDuration")
        if newTrip["unknownType"] == "past":
            start_datetime = end_datetime = -1
        else:
            start_datetime = end_datetime = 1
    return (
        manDuration,
        start_datetime,
        end_datetime,
        utc_start_datetime,
        utc_end_datetime,
    )


def getUtcDatetime(lat, lng, dateTime):
    # Instantiate TimezoneFinder and find timezone for given lat, lng
    tf = TimezoneFinder()
    timezone_str = tf.timezone_at(lat=lat, lng=lng)

    # Localize the given datetime to the found timezone
    timezone = pytz.timezone(timezone_str)
    localized_datetime = timezone.localize(dateTime)

    # Convert the localized datetime to UTC
    utc_datetime = localized_datetime.astimezone(pytz.utc).replace(tzinfo=None)
    return utc_datetime


def get_user_id(username):
    with managed_cursor(authConn) as cursor:
        cursor.execute(
            """
            SELECT uid FROM user
            WHERE username = ?
        """,
            (username,),
        )
        row = cursor.fetchone()
    if row:
        return row[0]
    return None


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


def sendOwnerEmail(subject, message):
    address = load_config()["owner"]["email"]
    if "127.0.0.1" in request.url or "localhost" in request.url:
        print(f"Email to: {address}\nSubject: {subject}\nMessage: {message}")
    else:
        sendEmail(address, subject, message)
