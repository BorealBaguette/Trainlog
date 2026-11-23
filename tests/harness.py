"""Imports app.py the way the tests need it, then hands out the app and helpers.

- Postgres: a throwaway `trainlog_test` database, dropped and rebuilt from the
  migrations on every run, on the server the .env points at.
- Everything app.py reads from its working directory (config.yaml, auth.db,
  .flask_secret, logs) lives in a temporary directory, so the real ones are never
  touched; the rest of the checkout is symlinked into it. app.py itself is copied
  there, not linked: on import it moves to the directory of its resolved path.
- No outbound HTTP: requests raises at once, so routes that call routers,
  geocoders or APIs fail fast instead of hanging or spending quota.
"""

import os
import pathlib
import shutil
import sys
import tempfile
import uuid
from datetime import datetime

import requests.adapters
import sqlalchemy
import yaml
from flask.testing import FlaskClient

REPO = pathlib.Path(__file__).resolve().parents[1]
TEST_DB = "trainlog_test"
OWNER = "owner"
PASSWORD = "test-password"
HOST = "localhost:5000"  # one of before_request's allowed hosts
BASE_URL = f"https://{HOST}"

if os.environ.get("ENVIRONMENT") == "production":
    sys.exit("Refusing to run the tests with ENVIRONMENT=production")


def _offline(self, request, *args, **kwargs):
    raise requests.ConnectionError(f"no network in tests: {request.url}")


requests.adapters.HTTPAdapter.send = _offline


def _reset_database():
    env = os.environ
    url = sqlalchemy.engine.URL.create(
        "postgresql",
        username=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        host=env["POSTGRES_HOST"],
        port=env["POSTGRES_PORT"],
        # The .env's own database: POSTGRES_DB already names the test one if a
        # failed import makes the next test module run this again
        database=env.setdefault("TRAINLOG_TEST_ADMIN_DB", env["POSTGRES_DB"]),
    )
    engine = sqlalchemy.create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(sqlalchemy.text(f"CREATE DATABASE {TEST_DB}"))
    engine.dispose()
    env["POSTGRES_DB"] = TEST_DB


def _work_dir():
    work = pathlib.Path(tempfile.mkdtemp(prefix="trainlog-test-"))
    own = {"app.py", "config.yaml", "databases", ".flask_secret", "logs"}
    for entry in REPO.iterdir():
        if entry.name not in own:
            (work / entry.name).symlink_to(entry)
    shutil.copy2(REPO / "app.py", work / "app.py")
    (work / "databases").mkdir()
    config = yaml.safe_load((REPO / "config-example.yaml").read_text())
    config["owner"] = {"username": OWNER, "email": "owner@example.com", "password": PASSWORD}
    # Refused at once: password resets and friend emails fail fast
    config["smtp"] = {"server": "127.0.0.1", "port": 1, "user": "t@example.com", "password": "x"}
    (work / "config.yaml").write_text(yaml.safe_dump(config))
    return work


_reset_database()
WORK = _work_dir()
os.chdir(WORK)
sys.path.insert(0, str(WORK))


def _create_auth_db():
    # app.py alters the user table before its create_all(), so it cannot start
    # on an empty auth.db: make the tables from the models first.
    from flask import Flask
    from src.users import authDb

    seed = Flask("seed")
    seed.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{WORK / 'databases' / 'auth.db'}"
    seed.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    authDb.init_app(seed)
    with seed.app_context():
        authDb.create_all()


_create_auth_db()

import app as trainlog  # noqa: E402

if pathlib.Path.cwd().resolve() != WORK.resolve():
    sys.exit(f"app.py moved to {pathlib.Path.cwd()}: refusing to run against real files")
from src.trips import Trip, create_trip  # noqa: E402
from src.users import Friendship, User, authDb  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

app = trainlog.app
# A failing view answers 500 (traceback logged) instead of raising into the test,
# as it would for a visitor; app.py's DEBUG would otherwise propagate it.
app.config["PROPAGATE_EXCEPTIONS"] = False


class Client(FlaskClient):
    """Every request over https to an allowed host, as Traefik delivers them."""

    def open(self, *args, **kwargs):
        kwargs.setdefault("base_url", BASE_URL)
        return super().open(*args, **kwargs)


app.test_client_class = Client


def make_user(name=None, share_level=0, **fields):
    """A new account (unique name unless given); returns its username."""
    name = name or f"u{uuid.uuid4().hex[:10]}"
    with app.app_context():
        user = User(
            username=name,
            email=f"{name}@example.com",
            pass_hash=generate_password_hash(PASSWORD, "scrypt"),
            share_level=share_level,
            **fields,
        )
        authDb.session.add(user)
        authDb.session.commit()
    return name


def uid(username):
    with app.app_context():
        return User.query.filter_by(username=username).first().uid


def login(username):
    client = app.test_client()
    response = client.post(
        "/login/?raw=1", data={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.data
    return client


def make_trip(username, visibility="public", trip_type="train", start=None):
    """A two-hour Paris → Lyon trip, finished unless `start` says otherwise;
    returns its id."""
    now = start or datetime(2025, 5, 1, 10, 0)
    trip = Trip(
        username=username,
        user_id=uid(username),
        # Real station names start with their country's flag
        origin_station="🇫🇷 Paris Gare de Lyon",
        destination_station="🇫🇷 Lyon Part-Dieu",
        start_datetime=now,
        end_datetime=now.replace(hour=12),
        trip_length=400000,
        estimated_trip_duration=7200,
        operator="SNCF",
        countries='{"FR": 400000}',
        manual_trip_duration=None,
        utc_start_datetime=now,
        utc_end_datetime=now.replace(hour=12),
        created=now,
        last_modified=now,
        line_name="TGV",
        type=trip_type,
        material_type=None,
        material_type_advanced=None,
        seat=None,
        reg=None,
        waypoints="[]",
        notes=None,
        price=None,
        currency=None,
        purchasing_date=None,
        ticket_ids=None,
        path=[{"lat": 48.84, "lng": 2.37}, {"lat": 45.76, "lng": 4.86}],
        is_project=False,
        visibility=visibility,
    )
    with app.app_context():
        create_trip(trip)
    return trip.trip_id


def befriend(a, b):
    # Ids first: uid()'s own app context would discard the pending rows on exit
    ua, ub = uid(a), uid(b)
    with app.app_context():
        now = datetime.now()
        authDb.session.add(Friendship(user_id=ua, friend_id=ub, accepted=now))
        authDb.session.add(Friendship(user_id=ub, friend_id=ua, accepted=now))
        authDb.session.commit()


def are_friends(a, b):
    ua, ub = uid(a), uid(b)
    with app.app_context():
        return (
            Friendship.query.filter(
                Friendship.user_id == ua,
                Friendship.friend_id == ub,
                Friendship.accepted.isnot(None),
            ).first()
            is not None
        )


make_user(OWNER)
