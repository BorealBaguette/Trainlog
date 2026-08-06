import html
import json
import logging
import os
import re
import urllib.parse

import requests
from google_images_search import GoogleImagesSearch

from py.utils import load_config
from src.wikidata import find_ship_image

mids_list = json.load(open("static/data/mids.json"))

google_key = load_config()["google"]["key"]
cx = load_config()["google"]["cx"]

gis = GoogleImagesSearch(google_key, cx)

logger = logging.getLogger(__name__)

IMAGES_DIR = "static/images/ship_pictures"


COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Wikimedia asks that clients identify themselves; anonymous agents are throttled.
COMMONS_AGENT = "Trainlog/1.0 (https://trainlog.me; ship photo import)"

# Wide enough for the popup, small enough not to store a 6000px original.
COMMONS_WIDTH = 1024


def _strip_html(value):
    """Commons returns attribution as HTML fragments; the credit line is plain text."""
    if not value:
        return None
    text = re.sub(r"<[^>]+>", "", value)
    return html.unescape(text).strip() or None


def fetch_commons_picture(pg, registration_id, file_url):
    """
    Store a Wikimedia Commons photo against one registration, with its attribution.

    `file_url` is what Wikidata's P18 gives: a Special:FilePath link naming the file. The
    file itself is downloaded at a sane width, and the credit is read from the Commons
    API — author and licence both, because a CC BY-SA photo shown without them is a
    licence breach rather than a missing nicety. A file whose licence cannot be read is
    not stored at all.

    Returns the stored path, or None.
    """
    title = urllib.parse.unquote((file_url or "").rstrip("/").rsplit("/", 1)[-1])
    if not title:
        return None
    if not title.lower().startswith("file:"):
        title = "File:" + title

    response = requests.get(
        COMMONS_API,
        params={
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "iiprop": "extmetadata|url",
            "iiurlwidth": COMMONS_WIDTH,
            "titles": title,
        },
        headers={"User-Agent": COMMONS_AGENT},
        timeout=30,
    )
    if response.status_code != 200:
        return None

    pages = (response.json().get("query") or {}).get("pages") or {}
    info = next(
        (page["imageinfo"][0] for page in pages.values() if page.get("imageinfo")), None
    )
    if not info:
        return None

    meta = info.get("extmetadata") or {}
    license_name = _strip_html((meta.get("LicenseShortName") or {}).get("value"))
    author = _strip_html((meta.get("Artist") or {}).get("value"))
    if not license_name:
        logger.warning("No licence on Commons file %s — not stored", title)
        return None

    # The scaled version where one exists, the original otherwise (small files have no
    # thumbnail).
    download_url = info.get("thumburl") or info.get("url")
    image = requests.get(download_url, headers={"User-Agent": COMMONS_AGENT}, timeout=60)
    if image.status_code != 200:
        return None

    if not os.path.exists(IMAGES_DIR):
        os.makedirs(IMAGES_DIR)

    filename = f"commons_{registration_id}_{title[5:]}".replace(" ", "_").replace("/", "")
    if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
        filename += ".jpg"
    with open(os.path.join(IMAGES_DIR, filename), "wb") as handle:
        handle.write(image.content)

    pg.execute(
        """
        INSERT INTO ship_pictures
            (registration_id, vessel_name, image_url, referrer_url, local_image_path,
             source, author, license)
        VALUES (:registration_id, :vessel_name, :image_url, :referrer_url, :local_image_path,
                'wikimedia', :author, :license)
        """,
        {
            "registration_id": registration_id,
            "vessel_name": title[5:],
            "image_url": download_url,
            # The file's own page: where the licence and the author can be checked, and
            # what CC asks be linked to.
            "referrer_url": info.get("descriptionurl"),
            "local_image_path": filename,
            "author": author,
            "license": license_name,
        },
    )
    return filename


def resolve_vessel(pg, identifier, at=None):
    """
    The ship a written identifier names, as of `at`, or None.

    `identifier` is whatever the user typed into a trip's `reg`: a hull key, a name, an
    IMO or an MMSI. All of them resolve to one hull (vessel_resolve), and the hull's
    identity at that moment is one registration (vessel_identity) — see migration 0056.
    `at` of None means the current identity.

    Returns the registration joined to its hull, so callers get the name and flag that
    were in force alongside the keys that never change.
    """
    if not identifier or not identifier.strip():
        return None

    return pg.execute(
        """
        SELECT v.uid AS vessel_id, v.imo, v.trainlog_id,
               COALESCE(v.imo, v.trainlog_id) AS hull_key,
               r.uid AS registration_id, r.name, r.mmsi, r.country_code
        FROM vessels v
        LEFT JOIN vessel_registrations r
               ON r.uid = vessel_identity(v.uid, CAST(:at AS timestamp))
        WHERE v.uid = vessel_resolve(:identifier)
        """,
        {"identifier": identifier.strip(), "at": at},
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
        SELECT DISTINCT v.uid
        FROM vessels v
        LEFT JOIN vessel_registrations r ON r.vessel_id = v.uid
        WHERE remove_diacritics(LOWER(COALESCE(r.name, ''))) LIKE remove_diacritics(LOWER(:pattern))
           -- Also on the folded key, so a search for 'MS Fjordtroll' finds the trips
           -- logged against 'Fjordtroll' and the other way round (migration 0055).
           OR (vessel_normalize(:term) IS NOT NULL
               AND r.name_key LIKE '%' || vessel_normalize(:term) || '%')
           OR v.imo LIKE :pattern
           OR r.mmsi LIKE :pattern
        -- Not trainlog_id: it means nothing to anybody and nobody searches for it.
        -- Matching ANY registration and returning the hull is deliberate — searching a
        -- ship's former name finds every trip on that hull, including the ones now
        -- displayed under its current name.
        """,
        {"pattern": f"%{term.strip()}%", "term": term.strip()},
    ).fetchall()

    return [row[0] for row in rows]


def _record_vessel(pg, identifier, mmsi, country_code):
    """
    Record what the photo search just told us about a ship, and return its registration
    uid — the identity the photo actually shows.

    The MMSI is what the search itself produced (it comes out of the vesselfinder URL)
    and it identifies one registration, so that is what the row is keyed on. A brand new
    MMSI means a hull we have not seen; it gets one, and the caller may later learn its
    IMO. Everything else is only filled in where it is still missing — an existing name
    or flag was either curated by an admin or learnt earlier, and neither should be
    overwritten by a fresh guess.

    The searched term is kept as the name only when it is not itself a number, and as
    the hull's IMO only when no other hull already claims it. Google Images does answer
    a search for one ship with a photo of another — that is how MMSI 276829000
    (Megastar) came to be filed under Mystar's IMO before migration 0054 — so an IMO
    that is already spoken for is left alone rather than moved.
    """
    identifier = identifier.strip()
    is_number = identifier.isdigit()

    existing = pg.execute(
        "SELECT uid, vessel_id FROM vessel_registrations WHERE mmsi = :mmsi",
        {"mmsi": mmsi},
    ).fetchone()

    if existing:
        registration_id, vessel_id = existing["uid"], existing["vessel_id"]
        pg.execute(
            """
            UPDATE vessel_registrations
            SET name = COALESCE(name, :name),
                country_code = COALESCE(country_code, :country_code),
                updated_on = CURRENT_TIMESTAMP
            WHERE uid = :uid
            """,
            {
                "name": None if is_number else identifier,
                "country_code": country_code,
                "uid": registration_id,
            },
        )
    else:
        vessel_id = pg.execute("INSERT INTO vessels DEFAULT VALUES RETURNING uid").scalar()
        registration_id = pg.execute(
            """
            INSERT INTO vessel_registrations (vessel_id, mmsi, name, country_code)
            VALUES (:vessel_id, :mmsi, :name, :country_code)
            RETURNING uid
            """,
            {
                "vessel_id": vessel_id,
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

    return registration_id


# How each source is credited. The text is the caller's to draw; what matters here is
# that it comes from the row rather than being assumed, now that photos arrive from more
# than one place — and that a CC licence carries its author and licence name, which is
# the condition on which the photo may be shown at all.
_CREDIT_LABELS = {"vesselfinder": "©Vesselfinder.com", "wikimedia": "Wikimedia Commons"}


def _credit(picture):
    """The attribution line for a cached photo, or '' when it needs none."""
    source = picture["source"]
    author = picture["author"]
    license_name = picture["license"]

    if source == "wikimedia":
        parts = [author or "Wikimedia Commons"]
        if license_name:
            parts.append(license_name)
        return " · ".join(parts)
    if source == "upload":
        # An admin's own photograph needs no credit — but uploading is also how a picture
        # from somewhere else gets in, and one filed with an author is one that has to be
        # credited. So the row decides, not the route it arrived by.
        return " · ".join([p for p in (author, license_name) if p])
    return _CREDIT_LABELS.get(source, "")


def _picture_response(vessel, picture):
    """The shape /getVesselPhoto answers with: the photo, its credit, plus who the ship is."""
    return {
        "image": os.path.join("/", IMAGES_DIR, picture["local_image_path"]),
        "link": picture["referrer_url"],
        # What must appear under the photo. Empty for an admin's own upload, which needs
        # no credit; never empty for a Commons file, whose licence requires one.
        "attribution": _credit(picture),
        "source": picture["source"],
        "license": picture["license"],
        "country": vessel["country_code"],
        # The display name, so a trip logged by IMO or MMSI still reads as the ship.
        # None when we hold no name for it; callers fall back to what was typed.
        "name": (vessel["name"] or "").strip() or None,
    }


def search_vessel_image(query):
    """
    Ask Google Images for a photo of `query` and download it.

    Returns (local_image_path, image_url, referrer_url, mmsi, country_code), or None. The
    MMSI comes out of the vesselfinder URL the result points at, which is the only fact
    the search itself produces about which ship this is.

    Split out from get_vessel_picture so the admin can aim a lookup at one registration —
    the search is identical, only what it gets filed against differs.
    """
    gis.search(search_params={"q": query, "num": 1})

    for image in gis.results():
        # The MMSI is read out of the vesselfinder URL the result points at
        # ('.../ship-photos/<something>-<mmsi>/...'), and the flag out of its MID. A
        # result from anywhere else, or one carrying an MID that is not allocated, parses
        # to nothing — that is a miss to report, not a 500 to raise. It reaches a user as
        # "nothing found" on the button they pressed.
        try:
            MMSI = image.url.split("/")[-2].split("-")[1]
            country_code = mids_list[MMSI[:3]][0]
        except (IndexError, KeyError, TypeError):
            logger.warning("Unparseable image result for %r: %s", query, image.url)
            continue

        response = requests.get(image.url)
        if response.status_code != 200:
            continue

        if not os.path.exists(IMAGES_DIR):
            os.makedirs(IMAGES_DIR)

        image_filename = f"{country_code}_{query}_{MMSI}.jpg".replace(" ", "_").replace(
            "/", ""
        )
        with open(os.path.join(IMAGES_DIR, image_filename), "wb") as file:
            file.write(response.content)

        return image_filename, image.url, image.referrer_url, MMSI, country_code

    return None


def commons_picture_for_registration(pg, registration_id):
    """
    A freely-licensed photo for this registration, from Wikimedia Commons via Wikidata.

    Tried before the image search, and preferred to it: a Commons file is licensed for
    reuse and carries its author, where a search result is a copyrighted still credited to
    whoever happened to host it. Matched on the ship's NUMBERS only — the hull's IMO or
    this period's MMSI — because a name would match the wrong ship often enough to matter
    and a photo of the wrong ship is worse than none.

    Returns the stored filename, or None.
    """
    row = pg.execute(
        """
        SELECT r.mmsi, v.imo
        FROM vessel_registrations r
        JOIN vessels v ON v.uid = r.vessel_id
        WHERE r.uid = :uid
        """,
        {"uid": registration_id},
    ).fetchone()
    if not row:
        return None

    file_url = find_ship_image(imo=row["imo"], mmsi=row["mmsi"])
    if not file_url:
        return None
    return fetch_commons_picture(pg, registration_id, file_url)


def fetch_picture_for_registration(pg, registration_id):
    """
    Look a photo up for one registration and file it against that registration.

    Commons first, the image search second — see commons_picture_for_registration. The
    search is by that period's own name and MMSI, so a photo of the ship under its former
    name is stored against the former name and not against the current one. Fills in the
    period's MMSI and flag only where they are still empty: what the search reports is a
    weaker source than what an admin typed.

    Returns the photo response, or None when nothing was found.
    """
    registration = pg.execute(
        "SELECT uid, name, mmsi, country_code FROM vessel_registrations WHERE uid = :uid",
        {"uid": registration_id},
    ).fetchone()
    if not registration:
        return None

    commons = commons_picture_for_registration(pg, registration_id)
    if commons:
        picture = pg.execute(
            "SELECT local_image_path, referrer_url, source, author, license"
            " FROM ship_pictures WHERE local_image_path = :path",
            {"path": commons},
        ).fetchone()
        return _picture_response(registration, picture)

    query = (registration["name"] or "").strip() or registration["mmsi"]
    if not query:
        return None

    found = search_vessel_image(query)
    if not found:
        return None

    image_filename, image_url, referrer_url, mmsi, country_code = found

    pg.execute(
        """
        INSERT INTO ship_pictures
            (registration_id, vessel_name, image_url, referrer_url, country_code,
             local_image_path, source)
        VALUES (:registration_id, :vessel_name, :image_url, :referrer_url, :country_code,
                :local_image_path, 'vesselfinder')
        """,
        {
            "registration_id": registration_id,
            "vessel_name": query,
            "image_url": image_url,
            "referrer_url": referrer_url,
            "country_code": country_code,
            "local_image_path": image_filename,
        },
    )

    pg.execute(
        """
        UPDATE vessel_registrations
        SET mmsi = COALESCE(mmsi, :mmsi),
            country_code = COALESCE(country_code, :country_code),
            updated_on = CURRENT_TIMESTAMP
        WHERE uid = :uid
          -- Only when the number is free: it may already belong to another hull, and a
          -- photo lookup is not evidence enough to move it.
          AND (:mmsi IS NULL OR NOT EXISTS (
                SELECT 1 FROM vessel_registrations o
                WHERE o.mmsi = :mmsi AND o.vessel_id <> vessel_registrations.vessel_id))
        """,
        {"mmsi": mmsi, "country_code": country_code, "uid": registration_id},
    )

    return {
        "image": os.path.join("/", IMAGES_DIR, image_filename),
        "link": referrer_url,
        "country": registration["country_code"] or country_code,
        "name": (registration["name"] or "").strip() or None,
    }


def get_vessel_picture(identifier, pg, at=None):
    """
    Photo and identity for a vessel named by name, IMO or MMSI.

    `at` is the moment to answer for — a trip's own date. The name, flag and photo are
    those of the registration in force then, so a 2017 crossing of IMO 8601915 answers
    Amorella under the Finnish flag and not Mega Victoria under the Italian one. None
    means now.

    Returns None when nothing is found. A miss runs a Google Images search and
    downloads the result, so call it on a deliberate user action only — never once per
    row of a listing.
    """
    if not identifier or not identifier.strip():
        return None
    identifier = identifier.strip()

    vessel = resolve_vessel(pg, identifier, at)

    if vessel and vessel["registration_id"]:
        picture = pg.execute(
            """
            SELECT local_image_path, referrer_url, source, author, license
            FROM ship_pictures
            WHERE registration_id = :registration_id AND local_image_path IS NOT NULL
            ORDER BY fetch_date DESC NULLS LAST, uid DESC
            LIMIT 1
            """,
            {"registration_id": vessel["registration_id"]},
        ).fetchone()

        if picture:
            return _picture_response(vessel, picture)

    # A known vessel with no cached photo falls through to a lookup, same as an unknown
    # one: the row may have been created by an admin, or by an earlier search whose
    # download failed. Commons first where the ship has a number to match on, because its
    # files are licensed for reuse; the image search is the fallback.
    if vessel and vessel["registration_id"]:
        commons = commons_picture_for_registration(pg, vessel["registration_id"])
        if commons:
            picture = pg.execute(
                "SELECT local_image_path, referrer_url, source, author, license"
                " FROM ship_pictures WHERE local_image_path = :path",
                {"path": commons},
            ).fetchone()
            return _picture_response(vessel, picture)

    found = search_vessel_image(identifier)
    if not found:
        return None

    image_filename, image_url, referrer_url, mmsi, country_code = found
    registration_id = _record_vessel(pg, identifier, mmsi, country_code)

    # vessel_name records the term that was searched — the file's provenance, and
    # nothing else. The ship's identity is registration_id.
    pg.execute(
        """
        INSERT INTO ship_pictures
            (registration_id, vessel_name, image_url, referrer_url, country_code,
             local_image_path, source)
        VALUES (:registration_id, :vessel_name, :image_url, :referrer_url, :country_code,
                :local_image_path, 'vesselfinder')
        """,
        {
            "registration_id": registration_id,
            "vessel_name": identifier,
            "image_url": image_url,
            "referrer_url": referrer_url,
            "country_code": country_code,
            "local_image_path": image_filename,
        },
    )

    vessel = pg.execute(
        "SELECT name, country_code FROM vessel_registrations WHERE uid = :uid",
        {"uid": registration_id},
    ).fetchone()

    return _picture_response(
        vessel,
        {
            "local_image_path": image_filename,
            "referrer_url": referrer_url,
            "source": "vesselfinder",
            "author": None,
            "license": None,
        },
    )
