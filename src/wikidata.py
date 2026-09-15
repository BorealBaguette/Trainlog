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


WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Q11446 is "ship". Anything below it in the subclass tree counts — a ferry, a ro-ro, a
# cruise ship are all subclasses several steps down.
SHIP_CLASS = "Q11446"


def api(params, timeout=30):
    url = f"{WIKIDATA_API}?{urllib.parse.urlencode({**params, 'format': 'json'})}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def search_ships(name, limit=15):
    """
    Ships whose name matches, as [{qid, name, description, imo, mmsi, country, operator,
    image}], best match first.

    Two calls on purpose. wbsearchentities does the matching — it is the same index the
    Wikidata site search uses, so it forgives case, accents and a missing "MS", where a
    SPARQL regex over labels is both stricter and slow enough to time out. SPARQL then
    reads the properties off the handful of items it returned.

    Non-ships are dropped: a name like "Normandie" matches a region, a battle and a
    hotel before it matches the ferry. Anything carrying an IMO or an MMSI is kept even
    if its class says otherwise, since those numbers are only ever issued to ships.
    """
    name = (name or "").strip()
    if not name:
        return []

    order = {}

    def collect(qids):
        for qid in qids:
            order.setdefault(qid, len(order))

    # Full text, restricted to items that carry a ship's numbers. This is what finds the
    # ferry: the item is titled "MS Pont-Aven", and a prefix search for "Pont-Aven"
    # returns the commune, the art movement and a dozen paintings before it. IMO first,
    # then MMSI for the small ferry that has no IMO at all.
    for statement in ("P458", "P587"):
        try:
            hits = api(
                {
                    "action": "query",
                    "list": "search",
                    "srsearch": f"{name} haswbstatement:{statement}",
                    "srlimit": limit,
                }
            )
            collect(hit["title"] for hit in hits["query"]["search"])
        except Exception as exc:
            logger.warning("Wikidata full-text search failed for %r: %s", name, exc)

    # And the label index, which catches a ship Wikidata holds no number for — and
    # forgives case and accents, where the search above is stricter.
    try:
        hits = api(
            {
                "action": "wbsearchentities",
                "search": name,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": limit,
            }
        )
        collect(hit["id"] for hit in hits.get("search") or [])
    except Exception as exc:
        logger.warning("Wikidata label search failed for %r: %s", name, exc)

    if not order:
        return []

    values = " ".join("wd:%s" % qid for qid in order)

    rows = sparql(
        f"""
        SELECT ?ship ?shipLabel ?shipDescription ?imo ?mmsi ?image ?code ?operatorLabel WHERE {{
            VALUES ?ship {{ {values} }}
            FILTER(EXISTS {{ ?ship wdt:P31/wdt:P279* wd:{SHIP_CLASS} }}
                   || EXISTS {{ ?ship wdt:P458 [] }} || EXISTS {{ ?ship wdt:P587 [] }})
            OPTIONAL {{ ?ship wdt:P458 ?imo }}
            OPTIONAL {{ ?ship wdt:P587 ?mmsi }}
            OPTIONAL {{ ?ship wdt:P18 ?image }}
            OPTIONAL {{ ?ship wdt:P137 ?operator }}
            # The flag state as an ISO code (P297), which is what the register stores.
            # P8047 is "country of registry" — the flag she actually sails under, and
            # what ship items carry; P17 is the fallback for an item that only has that.
            OPTIONAL {{ ?ship wdt:P8047|wdt:P17 ?country. ?country wdt:P297 ?code }}
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}
        """,
        timeout=60,
    )

    # An item with two values for a property (two MMSIs over its life, say) comes back as
    # two rows. First one wins, and the rest are folded in.
    found = {}
    for row in rows:
        qid = (value(row, "ship") or "").rsplit("/", 1)[-1]
        if qid in found:
            continue
        label = value(row, "shipLabel")
        found[qid] = {
            "qid": qid,
            # The label service answers with the bare Q-id for an item with no English
            # label; that is not a name to offer as one.
            "name": None if label == qid else label,
            "description": value(row, "shipDescription"),
            "imo": value(row, "imo"),
            "mmsi": value(row, "mmsi"),
            "country": (value(row, "code") or "").upper() or None,
            "operator": value(row, "operatorLabel"),
            "image": value(row, "image"),
        }

    return sorted(found.values(), key=lambda item: order.get(item["qid"], 99))


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
