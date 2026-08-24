"""The station autocomplete pipeline: what Photon's candidates are called, and which of them
are the same place. Shared by the web UI and the MCP tool, which previously had its own copy
and produced different labels for the same station.

  1. merge_language_passes   join the lang=en and lang=default responses on (osm_type, osm_id)
  2. resolve_country         fix the country code where Photon's is wrong
  3. apply_international_names  choose the displayed name (see src/station_names.py)
  4. apply_city_prefix       "Gare de Lyon" -> "Paris - Gare de Lyon" where it disambiguates
  5. dedupe_features         collapse the several OSM objects Photon returns per station
  6. resolve_homonyms        distinguish what genuinely remains ambiguous

5 must precede 6, or the homonym step labels the six copies of one station "(a)" to "(f)".
"""

import logging
import unicodedata

from py.utils import getCountryFromCoordinates, getDistance, stringSimmilarity
from src.consts import STATION_OSM_TAGS
from src.photon import photonRequest, photonRequestLangs
from src.station_names import (
    international_name,
    normalise_for_comparison,
    preferred_spelling,
)

logger = logging.getLogger(__name__)

# Photon 400s on any parameter outside this list rather than ignoring it, failing the whole
# search. Taken verbatim from the error body.
PHOTON_PARAMS = frozenset(
    {
        "q",
        "lat",
        "lon",
        # /reverse only, in km. Missing here until now, so the map stop-picker's radius
        # control drew its circle but never reached Photon.
        "radius",
        "lang",
        "limit",
        "osm_tag",
        "bbox",
        "countrycode",
        "layer",
        "zoom",
        "location_bias_scale",
        "dedupe",
        "debug",
        "include",
        "exclude",
        "geometry",
        "suggest_addresses",
    }
)

# Photon's country code is wrong often enough in these two to be worth a coordinate lookup.
SPECIAL_COUNTRIES = ("CN", "FI")

# Below this similarity a city name adds information to the station name, so it gets prefixed.
CITY_PREFIX_SIMILARITY = 50

# Same name, same country, this close: one station seen through different OSM objects.
# Generous enough for a large terminus, far below the gap between two stations sharing a name.
DEDUPE_RADIUS_M = 500

# Which OSM object best represents a station when several describe it.
_OSM_VALUE_RANK = {
    "station": 0,
    "halt": 1,
    "stop": 2,
    "tram_stop": 3,
    "bus_stop": 4,
    "platform": 5,
    "subway_entrance": 6,
}


def _feature_key(feature):
    """The (osm_type, osm_id) identity of a Photon feature, or None if it has none."""
    props = feature.get("properties", {})
    osm_type, osm_id = props.get("osm_type"), props.get("osm_id")
    if osm_type is None or osm_id is None:
        return None
    return (osm_type, osm_id)


def merge_language_passes(responses, primary="en", local="default"):
    """Combine the per-language Photon responses into one feature list.

    The `primary` pass defines the result set and its ordering; the `local` pass only
    contributes names, matched by osm id. A partial or failed local pass degrades the naming
    rather than breaking the search.
    """
    primary_response = responses.get(primary)
    local_response = responses.get(local)

    # If the primary pass failed outright, the local one is better than nothing.
    if not primary_response or not primary_response.get("features"):
        primary_response = primary_response or local_response
        if not primary_response:
            return []

    local_props = {}
    if local_response:
        for feature in local_response.get("features", []):
            key = _feature_key(feature)
            if key:
                local_props[key] = feature.get("properties", {})

    features = primary_response.get("features", [])
    for feature in features:
        props = feature.setdefault("properties", {})
        key = _feature_key(feature)
        other = local_props.get(key, {}) if key else {}
        props["name_en"] = props.get("name")
        props["name_local"] = other.get("name")
        # The city is carried in both languages too: prefixing an international station name
        # with Photon's English city produced labels like "Munich - München Hbf".
        props["city_en"] = props.get("city")
        props["city_local"] = other.get("city")
    return features


def resolve_country(feature):
    """Correct the feature's country code where Photon's is unreliable."""
    props = feature.get("properties", {})
    if props.get("countrycode") in SPECIAL_COUNTRIES:
        try:
            lon, lat = feature["geometry"]["coordinates"]
            props["countrycode"] = getCountryFromCoordinates(lat, lon)["countryCode"]
        except Exception as e:
            logger.debug(f"Country lookup failed: {e}")
    return props.get("countrycode", "unknown")


def apply_international_names(features):
    """Set each feature's `name` to its international name."""
    for feature in features:
        props = feature.get("properties", {})
        country_code = resolve_country(feature)
        name = international_name(
            props.get("name_local"),
            props.get("name_en"),
            country_code=country_code,
        )
        if name:
            props["name"] = name
    return features


def _city_is_redundant(props, name):
    """True if the station name already tells you which city it is in.

    Checked in both languages: "Kyyiv-Pasazhyrskyy" against the English "Kyiv" scores too low
    and became "Kyiv - Kyyiv-Pasazhyrskyy", where Київ against Київ-Пасажирський matches.
    """
    candidates = [
        (props.get("city_en"), name),
        (props.get("city"), name),
        (props.get("city_local"), props.get("name_local")),
    ]
    for city, against in candidates:
        if not city or not against:
            continue
        if stringSimmilarity(city.lower(), against.lower()) >= CITY_PREFIX_SIMILARITY:
            return True
    return False


def apply_city_prefix(features):
    """Prefix the city where the station name alone does not identify the place.

    "Gare de Lyon" becomes "Paris - Gare de Lyon"; "München Hbf" is left alone. The prefix
    uses the city's international name so both halves of the label are in one language.
    """
    for feature in features:
        props = feature.get("properties", {})
        name = props.get("name")
        if not name:
            continue

        city = international_name(
            props.get("city_local"),
            props.get("city_en"),
            country_code=props.get("countrycode"),
        ) or props.get("city")
        if not city:
            continue

        if _city_is_redundant(props, name):
            continue

        district, locality = props.get("district"), props.get("locality")
        district_differs = (
            district
            and stringSimmilarity(district.lower(), name.lower())
            < CITY_PREFIX_SIMILARITY
        )
        locality_differs = (
            locality
            and stringSimmilarity(locality.lower(), name.lower())
            < CITY_PREFIX_SIMILARITY
        )
        if district_differs or locality_differs or (not district and not locality):
            props["name"] = f"{city} - {name}"
    return features


def _coords(feature):
    try:
        lon, lat = feature["geometry"]["coordinates"]
        return {"lat": lat, "lng": lon}
    except Exception:
        return None


def dedupe_features(features, radius_m=DEDUPE_RADIUS_M):
    """Collapse features that are the same station seen through different OSM objects.

    Two names count as the same station at two different distances:

      identical            within `radius_m` (500m), generous enough for a large terminus
      one contains other   within `radius_m / 2`, weaker evidence so it needs them closer

    Containment is needed because apply_city_prefix() runs first and can name two objects of
    one station differently: OSM's two Liverpool Street nodes, 90m apart, came out as "London
    Liverpool Street" and "London - Liverpool Street", were both offered and both registered,
    leaving 2,100 trip endpoints resolving to neither. The halved radius keeps it safe —
    "Richmond" is contained in "East Richmond", two Melbourne stations 677m apart.

    The survivor is the object that best represents a station, Photon's ordering breaking ties.
    """
    survivors = []
    # country -> list of [index into survivors, coords, folded name]
    clusters = {}

    for feature in features:
        props = feature.get("properties", {})
        country = props.get("countrycode")
        folded = normalise_for_comparison(props.get("name"))
        coords = _coords(feature)
        rank = _OSM_VALUE_RANK.get(props.get("osm_value"), 99)

        match_index = None
        if coords is not None and folded:
            for entry in clusters.get(country, []):
                index, other_coords, other_folded = entry
                if other_coords is None or not other_folded:
                    continue
                if folded == other_folded:
                    limit = radius_m
                elif folded in other_folded or other_folded in folded:
                    limit = radius_m / 2
                else:
                    continue
                try:
                    if getDistance(coords, other_coords) <= limit:
                        match_index = index
                        matched_entry = entry
                        break
                except Exception:
                    continue

        if match_index is None:
            survivors.append(feature)
            clusters.setdefault(country, []).append([len(survivors) - 1, coords, folded])
            continue

        incumbent = survivors[match_index]
        incumbent_rank = _OSM_VALUE_RANK.get(
            incumbent.get("properties", {}).get("osm_value"), 99
        )
        if rank < incumbent_rank:
            survivors[match_index] = feature
            # The cluster now stands for this object, so later features compare against the
            # name and position actually being shown.
            matched_entry[1], matched_entry[2] = coords, folded

    return survivors


def resolve_homonyms(features):
    """Tell apart distinct places that share a name and country.

    Uses the state where that separates them cleanly, an alphabetical marker where it does not.
    """
    homonyms = {}
    for feature in features:
        props = feature.get("properties", {})
        key = (props.get("name"), props.get("countrycode", "unknown"))
        entry = homonyms.setdefault(key, {"count": 0, "states": []})
        entry["count"] += 1
        entry["states"].append(props.get("state"))

    for (name, country), details in homonyms.items():
        if details["count"] <= 1:
            continue
        unique_states = set(details["states"])
        states_separate_them = (
            len(unique_states) == details["count"] and None not in unique_states
        )
        suffix = ord("a")
        for feature in features:
            props = feature.get("properties", {})
            if props.get("name") != name or props.get("countrycode", "unknown") != country:
                continue
            if states_separate_them:
                props["name"] += f" ({props['state']})"
            else:
                props["homonymy_order"] = f" ({chr(suffix)})"
                suffix += 1
    return features


def process_station_results(responses, primary="en", local="default"):
    """Run the full pipeline over the raw per-language Photon responses."""
    features = merge_language_passes(responses, primary=primary, local=local)
    apply_international_names(features)
    apply_city_prefix(features)
    features = dedupe_features(features)
    resolve_homonyms(features)
    return features


class PhotonUnavailable(Exception):
    """Photon answered for no requested language."""


def _registry_features(query, trip_type, user_id, limit=5, display=None):
    """Registry hits shaped like Photon features, so callers need no special case.

    Returns [] on any failure: the registry is an addition to the search, never a reason for
    it to break.
    """
    if not query:
        return []
    try:
        from src.stations import search_registry

        rows = search_registry(query, trip_type, user_id=user_id, limit=limit)
    except Exception as e:
        logger.warning(f"Registry search failed, falling back to Photon alone: {e}")
        return []

    mode, user_lang = display or ("international", None)

    from src.stations import display_name

    features = []
    for row in rows:
        canonical = display_name(row, mode=mode, user_lang=user_lang) or row["name"]

        # Did the display preference actually find a name for *this* station, or fall back?
        # An expressed preference wins over the typed spelling — a French user typing "Oslo"
        # wants "Gare centrale d'Oslo". A fallback means nothing was expressed about this
        # station, so what they typed is the better answer. Curated counts as expressed.
        preference_applied = bool(row.get("curated_name")) or (
            canonical != row.get("name_intl")
        )

        display_label = (
            canonical
            if preference_applied
            else preferred_spelling(query, canonical, row["matched_alias"])
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [row["lng"], row["lat"]]},
                "properties": {
                    "name": display_label,
                    "name_local": row["name_local"],
                    "name_en": canonical,
                    "countrycode": row["country_code"],
                    "osm_type": row["osm_type"],
                    "osm_id": row["osm_id"],
                    "wikidata": row["wikidata"],
                    "station_id": row["station_id"],
                    "matched_alias": row["matched_alias"],
                    # Shown alongside when it differs from what is offered, so a Finn offered
                    # the Finnish name still sees what the station is signposted as.
                    "canonical_name": canonical,
                    "from_registry": True,
                },
            }
        )
    return features


def search_stations(args, timeout=2, trip_type="train", user_id=None, display=None):
    """Answer a station autocomplete request. `args` is the query string as a multidict.

    Forward geocoding by default; `lat` and `lon` reverse geocode instead. A caller naming a
    language explicitly gets one pass in that language, so names come back as Photon gave
    them — the rest of the pipeline still runs, since none of it is a language choice.
    """
    params = args.to_dict(flat=False)
    is_reverse = params.get("lat") and params.get("lon")
    endpoint = "/reverse" if is_reverse else "/api"

    # A whitelist, not a blacklist: forwarding one application-level parameter (`trip_type`,
    # for the registry pool) 400s the whole search, and the forms keep adding their own.
    params = {k: v for k, v in params.items() if k in PHOTON_PARAMS}

    # Filter to the infrastructure this mode actually uses, unless the caller chose its own
    # tags — the trip form sends per-mode ones, and its `special` variants deliberately narrow
    # them further. Unfiltered, Photon answers "Grenoble" with the city boundary relation.
    if not params.get("osm_tag"):
        tags = STATION_OSM_TAGS.get(trip_type)
        if tags:
            params["osm_tag"] = list(tags)

    explicit_lang = params.pop("lang", None)
    if explicit_lang:
        lang = explicit_lang[0]
        responses = {
            lang: photonRequest(
                endpoint, params={**params, "lang": explicit_lang}, timeout=timeout
            )
        }
        primary = local = lang
    else:
        responses = photonRequestLangs(
            endpoint, params, ("en", "default"), timeout=timeout
        )
        primary, local = "en", "default"

    # Searched alongside Photon, hits first. Photon's index carries only default/de/en/fr, so
    # it finds nothing for "Pietarsaari-Pedersöre", "Moskva-Kazanskaya" or "Seoulyeok" —
    # verified against the live instance. An outage also degrades to registry-only, not a 500.
    registry = (
        _registry_features(
            (params.get("q") or [None])[0], trip_type, user_id, display=display
        )
        if not is_reverse
        else []
    )

    if all(response is None for response in responses.values()):
        if registry:
            return registry
        raise PhotonUnavailable()

    features = process_station_results(responses, primary=primary, local=local)

    if registry:
        features = _drop_already_in_registry(features, registry)
    return registry + features


def _drop_already_in_registry(features, registry):
    """Remove Photon results that are the same station as one the registry already returned.

    Comparing (osm_type, osm_id) alone showed stations twice, because Photon's top hit is
    often a different object of the same station than the registry was seeded from. Resolving
    the ids through station_osm_objects makes it exact rather than a name-and-distance guess.
    """
    registry_station_ids = {
        f["properties"].get("station_id")
        for f in registry
        if f["properties"].get("station_id")
    }
    if not registry_station_ids:
        return features

    pairs = [
        (f["properties"].get("osm_type"), f["properties"].get("osm_id"))
        for f in features
        if f["properties"].get("osm_id") is not None
    ]
    try:
        from src.stations import stations_for_osm_objects

        owner = stations_for_osm_objects(pairs)
    except Exception as e:
        logger.warning(f"Could not resolve OSM objects for deduplication: {e}")
        return features

    # Fallback for objects enrichment has not linked yet — a platform node with neither
    # wikidata nor uic_ref cannot be tied to its station from tags alone.
    registry_names = {
        (normalise_for_comparison(f["properties"].get("name")), f["properties"].get("countrycode"))
        for f in registry
    }

    kept = []
    for feature in features:
        props = feature.get("properties", {})
        key = (props.get("osm_type"), props.get("osm_id"))
        if owner.get(key) in registry_station_ids:
            continue
        if (
            normalise_for_comparison(props.get("name")),
            props.get("countrycode"),
        ) in registry_names:
            continue
        kept.append(feature)
    return kept
