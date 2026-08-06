"""
Wikidata lookups for ships.

Wikidata is the register's reference source: IMO is P458, MMSI is P587, the flag state is
P17, the operator P137 and a freely-licensed photo P18. The data is CC0 and the SPARQL
endpoint needs no key, which is what makes it usable here at all — the alternative was an
image search whose results are copyrighted stills credited to whoever hosted them.

Lives under src/ rather than in the backfill script because two callers need it: the
backfill, and the photo lookup that runs when somebody opens a trip on a ship we have
never seen.
"""

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

SPARQL_URL = "https://query.wikidata.org/sparql"

# Wikidata asks for a descriptive agent identifying the client; anonymous or
# library-default agents are throttled or refused outright.
USER_AGENT = "Trainlog/1.0 (https://trainlog.me; vessel register)"

# How many identifiers go into one VALUES clause. The endpoint has a 60s query timeout
# and these are index lookups, so this is about staying well inside it.
CHUNK = 250


def sparql(query, timeout=180):
    url = f"{SPARQL_URL}?{urllib.parse.urlencode({'query': query, 'format': 'json'})}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)["results"]["bindings"]


def value(binding, key):
    entry = binding.get(key)
    return entry["value"] if entry else None


def lookup(identifiers, prop):
    """
    {identifier: {name, imo, mmsi, image}} for the ones Wikidata knows, keyed on `prop`
    (P458 for IMO, P587 for MMSI).

    A number matching several items is dropped: that means Wikidata disagrees with itself
    about which hull it belongs to, and there is nothing to choose by.
    """
    found = {}
    dropped = set()

    for start in range(0, len(identifiers), CHUNK):
        chunk = identifiers[start : start + CHUNK]
        values = " ".join('"%s"' % i for i in chunk)
        rows = sparql(
            f"""
            SELECT ?key ?shipLabel ?imo ?mmsi ?image WHERE {{
                VALUES ?key {{ {values} }}
                ?ship wdt:{prop} ?key.
                OPTIONAL {{ ?ship wdt:P458 ?imo }}
                OPTIONAL {{ ?ship wdt:P587 ?mmsi }}
                OPTIONAL {{ ?ship wdt:P18 ?image }}
                SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }}
            """
        )
        for row in rows:
            key = value(row, "key")
            candidate = {
                "name": value(row, "shipLabel"),
                "imo": value(row, "imo"),
                "mmsi": value(row, "mmsi"),
                "image": value(row, "image"),
            }
            if key in found and found[key] != candidate:
                dropped.add(key)
            found[key] = candidate

    for key in dropped:
        logger.warning("%s %s matches several Wikidata items — skipped", prop, key)
        found.pop(key, None)

    return found


def find_ship_image(imo=None, mmsi=None):
    """
    A freely-licensed photo of the ship carrying this IMO or MMSI, as a Commons file URL,
    or None.

    Matched on a NUMBER only, deliberately. A name would match far more ships and would
    be wrong for a good share of them — several real vessels are called Gotland — and a
    photo of the wrong ship is worse than no photo. Matching a name to a hull is a
    judgement, and there is a place for a human to make it: the backfill's suggestions.
    """
    clauses = []
    if imo:
        clauses.append('{ ?ship wdt:P458 "%s" }' % imo)
    if mmsi:
        clauses.append('{ ?ship wdt:P587 "%s" }' % mmsi)
    if not clauses:
        return None

    try:
        rows = sparql(
            f"""
            SELECT ?image WHERE {{
                {" UNION ".join(clauses)}
                ?ship wdt:P18 ?image.
            }}
            LIMIT 1
            """,
            timeout=30,
        )
    except Exception as exc:
        # The photo is a nicety; the caller has another source to fall back on.
        logger.warning("Wikidata image lookup failed for imo=%s mmsi=%s: %s", imo, mmsi, exc)
        return None

    return value(rows[0], "image") if rows else None
