import json
import os

import requests
from google_images_search import GoogleImagesSearch

from py.utils import load_config

mids_list = json.load(open("static/data/mids.json"))

google_key = load_config()["google"]["key"]
cx = load_config()["google"]["cx"]

gis = GoogleImagesSearch(google_key, cx)

IMAGES_DIR = "static/images/ship_pictures"


def resolve_vessel(pg, identifier):
    """
    The vessel a written identifier names, as a row of `vessels`, or None.

    `identifier` is whatever the user typed into a trip's `reg`: a name, an IMO or an
    MMSI. All three resolve to the same row — see vessel_resolve() in migration 0054.
    """
    if not identifier or not identifier.strip():
        return None

    return pg.execute(
        "SELECT uid, name, imo, mmsi, country_code FROM vessels"
        " WHERE uid = vessel_resolve(:identifier)",
        {"identifier": identifier.strip()},
    ).fetchone()


def find_vessel_ids(pg, term):
    """
    Vessels whose name, IMO or MMSI matches `term`.

    Lets trip search follow the split the same way the display does: a trip logged as
    '9773064' is shown as Megastar, so searching "Megastar" has to find it, and
    searching the number has to find the ones logged by name. The counterpart of
    find_operator_ids for ships.

    Returns an empty list when nothing matches, in which case the caller falls back to
    plain text matching on the raw `reg` field alone.
    """
    if not term or not term.strip():
        return []

    rows = pg.execute(
        """
        SELECT uid FROM vessels
        WHERE remove_diacritics(LOWER(COALESCE(name, ''))) LIKE remove_diacritics(LOWER(:pattern))
           -- Also on the folded key, so a search for 'MS Fjordtroll' finds the trips
           -- logged against 'Fjordtroll' and the other way round (migration 0055).
           OR (vessel_normalize(:term) IS NOT NULL
               AND name_key LIKE '%' || vessel_normalize(:term) || '%')
           OR imo LIKE :pattern
           OR mmsi LIKE :pattern
        """,
        {"pattern": f"%{term.strip()}%", "term": term.strip()},
    ).fetchall()

    return [row[0] for row in rows]


def _record_vessel(pg, identifier, mmsi, country_code):
    """
    Record what the photo search just told us about a ship, and return its vessel uid.

    The MMSI is the identity here: it is the one fact the search itself produced (it
    comes out of the vesselfinder URL), so it is what the row is keyed on. Everything
    else is only filled in where it is still missing — an existing name or flag was
    either curated by an admin or learnt earlier, and neither should be overwritten by
    a fresh guess.

    The searched term is kept as the name only when it is not itself a number, and as
    the IMO only when no other vessel already claims that number. Google Images does
    answer a search for one ship with a photo of another — that is how MMSI 276829000
    (Megastar) came to be filed under Mystar's IMO before migration 0054 — so an IMO
    that is already spoken for is left alone rather than moved.
    """
    identifier = identifier.strip()
    is_number = identifier.isdigit()

    vessel_id = pg.execute(
        """
        INSERT INTO vessels (mmsi, name, country_code)
        VALUES (:mmsi, :name, :country_code)
        ON CONFLICT (mmsi) WHERE mmsi IS NOT NULL DO UPDATE
        SET name = COALESCE(vessels.name, EXCLUDED.name),
            country_code = COALESCE(vessels.country_code, EXCLUDED.country_code),
            updated_on = CURRENT_TIMESTAMP
        RETURNING uid
        """,
        {
            "mmsi": mmsi,
            "name": None if is_number else identifier,
            "country_code": country_code,
        },
    ).scalar()

    if is_number and len(identifier) == 7:
        pg.execute(
            """
            UPDATE vessels SET imo = :imo, updated_on = CURRENT_TIMESTAMP
            WHERE uid = :uid
              AND imo IS NULL
              AND NOT EXISTS (SELECT 1 FROM vessels other WHERE other.imo = :imo)
            """,
            {"imo": identifier, "uid": vessel_id},
        )

    return vessel_id


def _picture_response(vessel, local_image_path, referrer_url):
    """The shape /getVesselPhoto answers with: the photo, plus who the ship is."""
    return {
        "image": os.path.join("/", IMAGES_DIR, local_image_path),
        "link": referrer_url,
        "country": vessel["country_code"],
        # The display name, so a trip logged by IMO or MMSI still reads as the ship.
        # None when we hold no name for it; callers fall back to what was typed.
        "name": (vessel["name"] or "").strip() or None,
    }


def get_vessel_picture(identifier, pg):
    """
    Photo and identity for a vessel named by name, IMO or MMSI.

    Returns None when nothing is found. A miss runs a Google Images search and
    downloads the result, so call it on a deliberate user action only — never once per
    row of a listing.
    """
    if not identifier or not identifier.strip():
        return None
    identifier = identifier.strip()

    vessel = resolve_vessel(pg, identifier)

    if vessel:
        picture = pg.execute(
            """
            SELECT local_image_path, referrer_url
            FROM ship_pictures
            WHERE vessel_id = :vessel_id AND local_image_path IS NOT NULL
            ORDER BY fetch_date DESC NULLS LAST, uid DESC
            LIMIT 1
            """,
            {"vessel_id": vessel["uid"]},
        ).fetchone()

        if picture:
            return _picture_response(
                vessel, picture["local_image_path"], picture["referrer_url"]
            )

    # A known vessel with no cached photo falls through to the search, same as an
    # unknown one: the row may have been created by an admin, or by an earlier search
    # whose download failed.
    gis.search(search_params={"q": identifier, "num": 1})

    if not gis.results():
        return None

    for image in gis.results():
        MMSI = image.url.split("/")[-2].split("-")[1]
        MID = MMSI[:3]
        country_code = mids_list.get(MID)[0]
        image_url = image.url
        referrer_url = image.referrer_url

        response = requests.get(image_url)
        if response.status_code != 200:
            continue

        if not os.path.exists(IMAGES_DIR):
            os.makedirs(IMAGES_DIR)

        image_filename = f"{country_code}_{identifier}_{MMSI}.jpg".replace(
            " ", "_"
        ).replace("/", "")
        with open(os.path.join(IMAGES_DIR, image_filename), "wb") as file:
            file.write(response.content)

        vessel_id = _record_vessel(pg, identifier, MMSI, country_code)

        # vessel_name records the term that was searched — the file's provenance, and
        # nothing else. The ship's identity is vessel_id.
        pg.execute(
            """
            INSERT INTO ship_pictures
                (vessel_id, vessel_name, image_url, referrer_url, country_code, local_image_path)
            VALUES (:vessel_id, :vessel_name, :image_url, :referrer_url, :country_code, :local_image_path)
            """,
            {
                "vessel_id": vessel_id,
                "vessel_name": identifier,
                "image_url": image_url,
                "referrer_url": referrer_url,
                "country_code": country_code,
                "local_image_path": image_filename,
            },
        )

        vessel = pg.execute(
            "SELECT uid, name, country_code FROM vessels WHERE uid = :uid",
            {"uid": vessel_id},
        ).fetchone()

        return _picture_response(vessel, image_filename, referrer_url)

    return None
