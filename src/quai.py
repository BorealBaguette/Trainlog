"""Station search through quai (trainlog_quai repo), the station index built from OSM.

quai groups every OSM object of a station into one result per mode, so its answers need
none of the deduplication Photon's do. Results are returned as Photon-shaped features, so
callers and the frontend treat both sources alike.
"""

import copy
import difflib
import logging
import re
import time
import unicodedata

import requests

from py.utils import load_config
from src.pg import pg_session

logger = logging.getLogger(__name__)

# Trip type -> quai mode. Anything else (car, walk, POIs, accommodation...) stays on Photon.
QUAI_MODES = {
    "train": "train",
    "rail": "train",
    "tram": "tram",
    "metro": "metro",
    "bus": "bus",
    "ferry": "ferry",
    "funicular": "funicular",
    "aerialway": "aerialway",
    "ski": "aerialway",
}


def quai_url():
    url = load_config().get("quai", {}).get("url", "https://quai.srv.trainlog.me")
    return url.rstrip("/") if url else None


def user_lang():
    """The language quai names stations and places in: as the user's settings say (theirs,
    "local" as written where they are, "int" in Latin letters), else the page's; English
    outside a request."""
    from flask import has_request_context, request, session

    from src.users import station_name_settings, valid_station_names

    if not has_request_context():
        return "en"
    # The testing panel sends an override for this request only, never a cookie.
    override = request.headers.get("X-Trainlog-Station-Lang", "")
    if override and valid_station_names(override):
        return override
    return (station_name_settings()[0]
            or (session.get("userinfo") or {}).get("lang") or "en")


# The user's "always in my language's script" setting (station_script): every name in the
# script of the language quai answers in. The letters of each script, as Unicode names them,
# and the ICU transform writing a Latin name in it (none for Chinese characters).
SCRIPT_LETTERS = {
    "Latn": ("LATIN",), "Cyrl": ("CYRILLIC",), "Grek": ("GREEK",), "Armn": ("ARMENIAN",),
    "Geor": ("GEORGIAN",), "Hebr": ("HEBREW",), "Arab": ("ARABIC",), "Thai": ("THAI",),
    "Hang": ("HANGUL",), "Jpan": ("HIRAGANA", "KATAKANA", "CJK"), "Hani": ("CJK",),
}
FROM_LATIN = {
    "Cyrl": "Latin-Cyrillic", "Grek": "Latin-Greek", "Armn": "Latin-Armenian",
    "Geor": "Latin-Georgian", "Hebr": "Latin-Hebrew", "Arab": "Latin-Arabic", "Thai": "Latin-Thai",
    "Hang": "Latin-Hangul", "Jpan": "Latin-Katakana",
}
# Scripts without capitals, whose transforms take a Latin capital for a letter of its own.
CASELESS = ("Geor", "Hebr", "Arab", "Thai", "Hang", "Jpan")
KANA = re.compile("[\u3040-\u30ff]")
# The kana ICU writes va, ve, wi, we and wo with, long out of use: ヴェ for ヹ ("vei" ヴェイ).
MODERN_KANA = str.maketrans({"ヷ": "ヴァ", "ヸ": "ヴィ", "ヹ": "ヴェ", "ヺ": "ヴォ", "ヰ": "ウィ",
                             "ヱ": "ウェ", "ヲ": "オ"})
PLAIN_LETTERS = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe",
                               "ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "þ": "th", "Þ": "Th"})
# The script each language is read in, where not Latin.
SCRIPT_OF_LANG = {"ja": "Jpan", "ko": "Hang", "zh": "Hani", "ru": "Cyrl", "uk": "Cyrl"}
_transliterators = {}


def forced_script():
    from flask import has_request_context, request

    from src.users import station_name_settings

    if not has_request_context():
        return None
    # Request-only override from the testing panel; old test cookies are ignored.
    forced = request.headers.get("X-Trainlog-Station-Script")
    if forced not in ("0", "1"):
        forced = "1" if station_name_settings()[1] else ""
    if forced != "1":
        return None
    # A local name is in its own script already.
    return None if user_lang() == "local" else SCRIPT_OF_LANG.get(user_lang(), "Latn")


def in_script(text, script):
    letters = [ch for ch in text or "" if ch.isalpha()]
    return bool(letters) and all(unicodedata.name(ch, "").startswith(SCRIPT_LETTERS[script])
                                 for ch in letters)


def written_in_script(label, names, latin):
    return script_written(label, names, latin)[0]


HAN = re.compile("[\u3400-\u4dbf\u4e00-\u9fff]+")

# Mandarin in katakana, as Japanese writes it (北京 ベイジン, 怀柔 ホワイロウ): each syllable's
# pinyin, from ICU, as Japanese romaji, which ICU's Latin-Katakana reads as it should. Its
# initial in romaji, then its final, as after a consonant; j, q and x read u as ü.
PINYIN_INITIALS = ("zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k", "h",
                   "j", "q", "x", "r", "z", "c", "s")
ROMAJI_INITIAL = {"zh": "j", "ch": "ch", "sh": "sh", "l": "r", "q": "ch", "x": "sh", "c": "ts"}
ROMAJI_FINAL = {
    "a": "a", "o": "ō", "e": "ō", "ai": "ai", "ei": "ei", "ao": "ao", "ou": "ou", "an": "an",
    "en": "en", "ang": "an", "eng": "on", "ong": "on", "er": "aru", "i": "ī", "ia": "ya",
    "ie": "ie", "iao": "yao", "iu": "iu", "ian": "ien", "in": "in", "iang": "yan", "ing": "in",
    "iong": "yon", "u": "ū", "ua": "ua", "uo": "uo", "uai": "uai", "ui": "ui", "uan": "uan",
    "un": "un", "uang": "uan", "v": "yū", "ve": "yue", "van": "yuen", "vn": "yun",
}
# Whole syllables that do not follow from their parts.
PINYIN_KANA = {
    "zhi": "ジー", "chi": "チー", "shi": "シー", "ri": "リー", "zi": "ズー", "ci": "ツー", "si": "スー",
    "yi": "イー", "wu": "ウー", "wo": "ウォ", "wei": "ウェイ", "wen": "ウェン", "weng": "ウォン",
    "hu": "フー", "hua": "ホア", "huai": "ホワイ", "hui": "ホイ", "huan": "ホアン", "huang": "ホアン",
    "e": "オー", "ye": "イエ", "yan": "イエン", "you": "ヨウ", "yuan": "ユエン",
}


def pinyin_kana(syllable):
    if syllable in PINYIN_KANA:
        return PINYIN_KANA[syllable]
    initial = next((i for i in PINYIN_INITIALS if syllable.startswith(i)), "")
    final = syllable[len(initial):]
    if initial in ("j", "q", "x") and final.startswith("u"):
        final = "v" + final[1:]
    # du, tu: ドゥ, トゥ, which romaji cannot say.
    if initial in ("d", "t") and final.startswith("u"):
        rest = ROMAJI_FINAL.get(final, final)[1:] or "ー"
        return {"d": "ドゥ", "t": "トゥ"}[initial] + (_icu("Latin-Katakana", rest) if rest != "ー" else rest)
    if syllable[:1] in ("y", "w") and not initial:
        romaji = syllable.replace("yi", "i", 1).replace("wu", "u", 1)
        romaji = ROMAJI_FINAL.get(romaji[1:] if romaji[0] in "yw" else romaji, romaji)
        romaji = ("y" if syllable[0] == "y" and not romaji.startswith(("y", "i", "ī")) else
                  "w" if syllable[0] == "w" and not romaji.startswith(("u", "ū")) else "") + romaji
    else:
        romaji = ROMAJI_INITIAL.get(initial, initial) + ROMAJI_FINAL.get(final, final)
        # A palatal initial takes the y of its final: jia ja, xiao shao, not jya, shyao.
        romaji = re.sub(r"^(j|ch|sh)y", r"\1", romaji)
    return _icu("Latin-Katakana", romaji)


def chinese_katakana(text):
    """The Chinese characters of `text` in katakana, by their Mandarin reading, or None."""
    han = "".join(HAN.findall(text or ""))
    if not han:
        return None
    pinyin = unicodedata.normalize("NFD", _icu("Han-Latin/Names", han)).replace("u\u0308", "v")
    syllables = "".join(ch for ch in pinyin if not unicodedata.combining(ch)).lower().split()
    # er after a syllable is its r: 哈尔滨 ハルビン.
    return "".join("ル" if s == "er" and i else pinyin_kana(s) for i, s in enumerate(syllables))


def _icu(transform, text):
    if transform not in _transliterators:
        import icu

        _transliterators[transform] = icu.Transliterator.createInstance(transform)
    return _transliterators[transform].transliterate(text)


def script_written(label, names, latin):
    """`label` in the forced script, if any: as it is when written in it, else one of its OSM
    names that is (name:<language> first), else its Latin name, which ICU writes in it. With
    whether ICU wrote it, for the page to tell. In Japanese, a name of Chinese characters alone
    is Japanese only as the station's name:ja: 北京南 is Chinese, in katakana ベイジングナン."""
    script = forced_script()
    names = {key: value for key, value in (names or {}).items()
             if key == "name" or key.startswith("name:") or key.endswith("_name")}

    def fits(text):
        return in_script(text, script) and (
            script != "Jpan" or text == names.get("name:ja") or bool(KANA.search(text)))

    if not script or fits(label):
        return label, False
    own = f"name:{user_lang()}"
    for key in sorted(names, key=lambda key: (key != own, not key.startswith("name:"))):
        if fits(names[key]):
            return names[key], False
    if script == "Latn":
        return latin or label, False
    # A place named in Latin letters has no Latin name apart (Oslo): its own.
    latin = latin or (label if in_script(label, "Latn") else None)
    # A Chinese name in Japanese: by its Mandarin reading, not its Latin letters.
    if script == "Jpan":
        katakana = chinese_katakana(names.get("name:zh") or names.get("name") or label)
        if katakana:
            return katakana, True
    if script not in FROM_LATIN or not latin:
        return label, False
    # Letters the transforms do not know, as their plain ones: Skøyen as Skoyen, Kjelsås as
    # Kjelsas, not "Скøыен".
    plain = "".join(ch for ch in unicodedata.normalize("NFD", latin.translate(PLAIN_LETTERS))
                    if not unicodedata.combining(ch))
    written = _icu(FROM_LATIN[script], plain.lower() if script in CASELESS else plain)
    return (written.translate(MODERN_KANA) if script == "Jpan" else written), True


def place_transliterated(place):
    place = place or {}
    return script_written(place.get("label"), place, place.get("latin") or place.get("name:en"))[1]


def transliterated(station):
    """The parts of a station's name ICU wrote in the forced script, as ICU guesses where OSM
    has no name in it ([] if none): its own name, its place before it (station_label), or
    both; "ベルゲン - オラヴ クイッレス ガテ" has Bergen's from OSM. And whether its place
    (place_of) is one."""
    if not forced_script():
        return [], False
    parts = []
    own, by_icu = script_written(station["label"], station.get("names"), station.get("latin"))
    if by_icu:
        parts.append(own)
    if station.get("needs_place"):
        place = (station["city"] if station.get("city_override") and station.get("city")
                 else station.get("settlement") or station.get("city"))
        if place_transliterated(place):
            parts.append(_place_name(place))
    return parts, place_transliterated(station.get("settlement") or station.get("city"))


def _place_name(place):
    """A city's name in the user's script (quai's label), in its first language where bilingual
    ("Ixelles - Elsene" is Ixelles)."""
    place = place or {}
    # In the "local" language, each place as it names itself.
    label = place.get("name") if user_lang() == "local" else place.get("label")
    name = written_in_script(label, place, place.get("latin") or place.get("name:en"))
    return re.split(r"\s+-\s+|\s*/\s*", name)[0] if name else None


def _lift_end_name(name, end):
    """A lift's end where OSM maps no station, in the user's language (quai gives it as
    lift_end, lower or upper): "Tråstølheisen nedre stasjon", "Tråstølheisen, gare amont"."""
    from src.utils import lang

    texts = lang.get(user_lang(), lang["en"])
    return texts["liftLowerStation" if end == "lower" else "liftUpperStation"].replace("{name}", name)


def place_of(station):
    """Where a station is, as people say it: its town, village or hamlet (Åndalsnes), else its
    municipality (Rauma)."""
    return _place_name(station.get("settlement") or station.get("city"))


def prefix_place_of(station):
    """The place put before a station's name where it needs one: a city places.csv names
    (London, city_override), whose parts are told apart by the hint only ("London - Euston
    Road", shown in Camden Town), else where it is (place_of)."""
    if station.get("city_override") and station.get("city"):
        return _place_name(station["city"])
    return place_of(station)


def station_label(station):
    """The name Trainlog gives a station: the one set for it by hand (apply_overrides), else
    quai's label, after its lift or funicular line if on one, else after its city where the
    name alone does not say where it is ("Royan - Gare"), as Trainlog has always named
    stations; "Lyon Part-Dieu" and "Brussels-Luxembourg" as they are."""
    if (station.get("override") or {}).get("name"):
        return station["override"]["name"]
    # In the "local" language, each station as it names itself (but for a name set in
    # Trainlog).
    label = station["label"] if user_lang() != "local" or station.get("named_by") else station["name"]
    own = written_in_script(label, station.get("names"), station.get("latin"))
    label = own
    if station.get("lift_end"):
        label = _lift_end_name(label, station["lift_end"])
    # A lift's or funicular's station goes by its ski area, else its line, as people know it:
    # "Val Thorens - Péclet", "Fløibanen - Fløyen" rather than "Bergen - Fløyen";
    # "Ulriksbanen øvre stasjon" says it already.
    line = station.get("ski_area") or station.get("line_name")
    if line:
        # Named after the line already, however it is written ("Fløibanen, nedre stasjon",
        # "Ulriksbanen nedre stasjon"): with the " - " the others have, but for a part in
        # brackets ("Voss Gondol (aval)").
        named = re.match(re.escape(line) + r"[\s,:;/–—-]+(?!\()(.+)$", label, re.IGNORECASE)
        if named:
            rest = named.group(1)
            return f"{label[:len(line)]} - {rest[:1].upper()}{rest[1:]}"
        if line.lower() in label.lower():
            return label
        return f"{line} - {label}"
    city = prefix_place_of(station)
    if station.get("needs_place") and city:
        return f"{city} - {own}"
    return own


def station_overrides(stations):
    """The overrides set for these quai stations (station_overrides): {(mode, station_key):
    {name, lat, lng, tracks, merged_into, names}}. Empty if the database cannot be read: an override is never worth
    failing a search for."""
    keys = [(s["mode"], s["station_key"]) for s in stations if s.get("station_key")]
    if not keys:
        return {}
    try:
        with pg_session() as pg:
            rows = pg.execute(
                """
                SELECT mode, station_key, name, lat, lng, tracks, merged_into, names
                FROM station_overrides
                WHERE (mode, station_key) IN (SELECT * FROM unnest(:modes, :keys))
                """,
                {"modes": [k[0] for k in keys], "keys": [k[1] for k in keys]},
            ).fetchall()
    except Exception as e:
        logger.warning(f"Station overrides unavailable: {e}")
        return {}
    return {
        (r.mode, r.station_key): {"name": r.name, "lat": r.lat, "lng": r.lng, "tracks": r.tracks,
                                  "merged_into": r.merged_into, "names": r.names or {}}
        for r in rows
    }


def merge_tracks(tracks, overrides):
    """OSM's tracks with those set by hand over them, by track: an override adds a track or
    moves OSM's of that ref (marked override), a hidden one removes OSM's."""
    by_ref = {track_key(t["ref"]): t for t in tracks or []}
    for t in overrides or []:
        key = track_key(t.get("ref"))
        if not key:
            continue
        if t.get("hidden"):
            by_ref.pop(key, None)
        else:
            by_ref[key] = {"ref": t["ref"], "lat": t["lat"], "lng": t["lng"],
                           "on_track": bool(t.get("on_track")), "override": True}
    return sorted(by_ref.values(), key=lambda t: (len(t["ref"]), t["ref"]))


# Merges change seldom and are looked up on every search: kept a minute, and dropped as soon
# as one is set (forget_station_merges).
MERGES_TTL_S = 60
_merges = {"at": None, "map": {}}


def station_merges():
    """{(mode, station_key): station_key it is merged into} (station_overrides.merged_into).
    The last known if the database cannot be read."""
    if _merges["at"] is None or time.monotonic() - _merges["at"] > MERGES_TTL_S:
        try:
            with pg_session() as pg:
                rows = pg.execute(
                    "SELECT mode, station_key, merged_into FROM station_overrides "
                    "WHERE merged_into IS NOT NULL"
                ).fetchall()
            _merges["map"] = {(r.mode, r.station_key): r.merged_into for r in rows}
            _merges["at"] = time.monotonic()
        except Exception as e:
            logger.warning(f"Station merges unavailable: {e}")
    return _merges["map"]


def forget_station_merges():
    _merges["at"] = None


def resolve_key(mode, key):
    """The station a key stands for in Trainlog: the one it is merged into, if any."""
    return station_merges().get((mode, key), key)


# quai's stations by key, for merges: what a merged station is replaced by, and what its
# target gains. Kept ten minutes.
_station_cache = {}
STATION_CACHE_TTL_S = 600


def quai_station(mode, key):
    """quai's station of `mode` with that key (redirects followed), or None."""
    cache_key = (mode, key, user_lang())
    cached = _station_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < STATION_CACHE_TTL_S:
        return copy.deepcopy(cached[1])
    station = (quai_get(f"station/{mode}/{key}") or {}).get("station")
    if station:
        _station_cache[cache_key] = (time.monotonic(), station)
    return copy.deepcopy(station) if station else None


def _merge_stations(stations):
    """In place: each station merged into another (station_merges) becomes that one, keeping
    what the request found about it (distance, tier); and each station others are merged
    into gains their tracks and lines. Positions in the list are kept, so a station can then
    appear twice."""
    merges = station_merges()
    if not merges:
        return
    for station in stations:
        target = merges.get((station.get("mode"), station.get("station_key")))
        found = target and quai_station(station["mode"], target)
        if found:
            kept = {k: station[k] for k in ("distance_m", "distance_km", "tier", "score", "matched")
                    if k in station}
            station.clear()
            station.update(found, **kept)
    into = {}
    for (mode, key), target in merges.items():
        into.setdefault((mode, target), []).append(key)
    for station in stations:
        for key in into.get((station.get("mode"), station.get("station_key")), []):
            merged = quai_station(station["mode"], key)
            if not merged:
                continue
            tracks = {track_key(t["ref"]): t for t in station.get("tracks") or []}
            for t in merged.get("tracks") or []:
                tracks.setdefault(track_key(t["ref"]), t)
            station["tracks"] = sorted(tracks.values(), key=lambda t: (len(t["ref"]), t["ref"]))
            lines = {line.get("ref"): line for line in station.get("lines") or []}
            for line in merged.get("lines") or []:
                lines.setdefault(line.get("ref"), line)
            station["lines"] = list(lines.values())


def apply_overrides(stations, follow_merges=True):
    """quai stations as Trainlog takes them, in place: merged into another where set (unless
    follow_merges is False: the station explorer shows a station as it is), and with the
    names and positions set for them by hand. A name set for a language is among the
    station's names, as its name:<language>, and its label for readers of it (named_by)."""
    if follow_merges:
        _merge_stations(stations)
    overrides = station_overrides(stations)
    for station in stations:
        override = overrides.get((station.get("mode"), station.get("station_key")))
        if not override:
            continue
        station["override"] = override
        if override["lat"] is not None:
            station["lat"], station["lng"] = override["lat"], override["lng"]
        if override["tracks"]:
            station["tracks"] = merge_tracks(station.get("tracks"), override["tracks"])
        if override["names"]:
            station["names"] = {**(station.get("names") or {}),
                                **{f"name:{code}": name for code, name in override["names"].items()}}
            code = user_lang()
            name = override["names"].get(code) or override["names"].get(code.split("-")[0])
            if name:
                station["label"] = name
                station["named_by"] = "trainlog"
    return stations


def _features(stations):
    """quai stations as Photon features, with homonyms told apart by city, else region."""
    apply_overrides(stations)
    # Once merged, a station and the one it is merged into are the same: listed once, where
    # the first of them came.
    seen = set()
    stations = [s for s in stations
                if (s.get("mode"), s.get("station_key")) not in seen
                and not seen.add((s.get("mode"), s.get("station_key")))]
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]},
            "properties": {
                "name": station_label(s),
                "countrycode": s.get("country") or "",
                "city": place_of(s),
                "state": s.get("region"),
                "osm_type": s["osm_type"],
                "osm_id": s["osm_id"],
                "station_key": s["station_key"],
                # How well the name matched (0 exact … 4 fuzzy), for the page to keep quai's
                # order while moving up stations the user knows.
                "tier": s.get("tier"),
                # Rail stations' numbered tracks, and the lines calling there with a stop
                # of each: [{ref, lat, lng, on_track}] (lines also have colour).
                "tracks": s.get("tracks") or [],
                "lines": s.get("lines") or [],
                "source": "quai",
                # Whether ICU wrote the name and the place in the forced script, and whether
                # the name is one set in Trainlog for the reader's language.
                "named_by": "trainlog" if s.get("named_by") or (s.get("override") or {}).get("name") else "osm",
                "transliterated": transliterated(s)[0],
                "place_transliterated": transliterated(s)[1],
            },
        }
        for s in stations
    ]
    groups = {}
    for feature in features:
        props = feature["properties"]
        groups.setdefault((props["name"], props["countrycode"]), []).append(props)
    for homonyms in groups.values():
        if len(homonyms) < 2:
            continue
        for field in ("city", "state"):
            places = [props.get(field) for props in homonyms]
            if None not in places and len(set(places)) == len(places):
                for props in homonyms:
                    props["name"] += f" ({props[field]})"
                break
        else:
            for i, props in enumerate(homonyms):
                props["homonymy_order"] = f" ({chr(ord('a') + i)})"
    return features


def search_stations(trip_type, q=None, lat=None, lon=None, radius_km=None, limit=10,
                    timeout=None):
    """Stations of the trip type's mode named like `q`, or near lat/lon when `q` is None.

    Returns Photon-shaped features, or None if quai does not cover the type or cannot be
    reached.
    """
    mode = QUAI_MODES.get(trip_type)
    # Long enough for a quai answering from disk while it rebuilds (quai.timeout, seconds).
    timeout = timeout or load_config().get("quai", {}).get("timeout", 10)
    url = quai_url()
    if not mode or not url:
        return None
    if q is not None:
        endpoint, params = "search", {"q": q, "mode": mode, "limit": limit}
        if lat is not None and lon is not None:
            params.update(lat=lat, lon=lon)
    else:
        endpoint = "reverse"
        params = {"lat": lat, "lon": lon, "mode": mode, "limit": limit, "radius": radius_km or 1}
    params["lang"] = user_lang()
    try:
        resp = requests.get(f"{url}/{endpoint}", params=params, timeout=timeout)
        resp.raise_for_status()
        return _features(resp.json()["stations"])
    except Exception as e:
        logger.warning(f"quai {endpoint} failed: {e}")
        return None


def nearest_stations(mode, points, radius_m=400, candidates=1, timeout=30):
    """The quai station of `mode` nearest each [lat, lng] within `radius_m`, in the points'
    order (None where none), with Trainlog's overrides applied and its name for each
    ("trainlog_name"). With candidates above 1, that many of the nearest for each point, as a
    list, nearest first. Raises if quai cannot be reached."""
    url = quai_url()
    if not url:
        raise RuntimeError("quai is not configured")
    found = []
    for start in range(0, len(points), 5000):
        body = {"mode": mode, "radius": radius_m, "points": points[start:start + 5000]}
        if candidates > 1:
            body["candidates"] = candidates
        resp = requests.post(f"{url}/nearest", params={"lang": user_lang()}, json=body,
                             timeout=timeout)
        resp.raise_for_status()
        found.extend(resp.json()["stations"])
    stations = [s for item in found for s in (item if isinstance(item, list) else [item]) if s]
    apply_overrides(stations)
    for station in stations:
        station["trainlog_name"] = station_label(station)
    return found


def quai_get(path, params=None, timeout=5):
    """quai's own JSON for `path`, or None if it cannot be reached."""
    url = quai_url()
    if not url:
        return None
    try:
        resp = requests.get(f"{url}/{path.lstrip('/')}", params={**(params or {}), "lang": user_lang()},
                            timeout=timeout)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"quai {path} failed: {e}")
        return None


TRACK_WORDS = re.compile(
    r"^(voie|gleis|gl\.?|track|platform|quai|binario|v[ií]a|spoor|tor|peron|путь|платформа|第)\s*"
    r"|\s*(号?站台|號?月台|番線|番のりば|번\s*(승강장|홈)?)$", re.IGNORECASE
)


def track_key(ref):
    """"Gleis 7", "Voie 7", "7站台" and "7" are the same track. Mirrors stopKey() in stop_slots.js."""
    return TRACK_WORDS.sub("", str(ref or "").strip().lower())


def _fold(text):
    """Lower case, no accents, punctuation as spaces: how stop names are compared."""
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def name_likeness(name, station):
    """How much a name ("Bergen - Strandkaiterminalen båtkai", Entur's "Bryggen") is this
    station's, 0 to 1: 1 where one of its names is it; 0.95 where one contains it or is
    contained in it, a little less, so that the station of that very name wins ("Lausanne" is
    Lausanne, not Lausanne-Flon nearer the timetable's point); else how alike the closest
    of them is spelt."""
    wanted = _fold(name)
    if not wanted:
        return 0
    best = 0
    for candidate in [station.get("label"), station.get("trainlog_name"), station.get("name"),
                      station.get("latin"), *(station.get("names") or {}).values()]:
        have = _fold(candidate)
        if not have:
            continue
        if have == wanted:
            return 1
        if f" {wanted} " in f" {have} " or f" {have} " in f" {wanted} ":
            best = max(best, 0.95)
            continue
        best = max(best, difflib.SequenceMatcher(None, wanted, have).ratio())
    return best


# How alike a name must be spelt to be a station's ("Strandkaiterminalen båtkai" and
# "Strandkaiterminalen, båt Askøy" are; "Zachariasbryggen" and "Strandterminalen" are not).
NAME_LIKENESS = 0.75


# Stations about as near as the nearest, between which a name may decide: within this many
# times its distance, or this many metres further. Beyond, the nearest is the one, whatever
# the names say: "Lausanne" on Lausanne's platform 1 is not Lausanne-Flon, 400m off, which
# a name containing it would have made "alike".
NEAR_TIE_FACTOR = 1.5
NEAR_TIE_M = 30
# Beyond this, a station is only the one if named as the stop, not merely alike: where the
# right station is missing from quai (as Lausanne's was), Lausanne-Flon 439m off is no answer
# for "Lausanne". And a timetable's stop takes no station at all that far and not so named.
FAR_M = 150


def station_by_place(name, nearby):
    """Of `nearby` stations (nearest first, with distance_m), the one a stop or trip end is at,
    and whether it is named alike: the nearest, unless another about as near (NEAR_TIE_*) is
    named more like it (Bryggen's point 38m from Bryggen and 39m from another stop;
    Strandterminalen 141m off and Strandkaiterminalen 145m). (None, False) if none."""
    if not nearby:
        return None, False
    nearest = nearby[0]
    limit = max(nearest.get("distance_m", 0) * NEAR_TIE_FACTOR, nearest.get("distance_m", 0) + NEAR_TIE_M)
    close = [s for s in nearby if s.get("distance_m", 0) <= limit]
    if not name:
        return nearest, False
    def alike(s, likeness):
        return likeness >= NAME_LIKENESS and (likeness == 1 or s.get("distance_m", 0) <= FAR_M)

    likeness, _, station = max(((name_likeness(name, s), -i, s) for i, s in enumerate(close)),
                               key=lambda t: t[:2])
    if alike(station, likeness):
        return station, True
    return nearest, alike(nearest, name_likeness(name, nearest))


# Modes whose stops are placed by direction (stops_by_direction): where a line's two
# directions stop on tracks or kerbs a few metres apart. Not trains, which go by the platform
# the timetable gives (tracks), nor ferries or lifts.
SNAP_MODES = ("tram", "metro", "bus")


def stops_by_direction(mode, keys, timeout=5):
    """Where each station of a journey (quai keys, in the order travelled, None for none) is
    stopped at going that way: [[lat, lng] or None, ...], from OSM's route relations (quai's
    /directions). All None if quai cannot be reached."""
    if not any(keys):
        return [None] * len(keys)
    try:
        resp = requests.post(f"{quai_url()}/directions", json={"mode": mode, "keys": keys},
                             timeout=timeout)
        resp.raise_for_status()
        return resp.json()["positions"]
    except Exception as e:
        logger.warning(f"quai directions failed: {e}")
        return [None] * len(keys)


def stop_point_id(transitous_id):
    """The official id of a timetable's stop point in Transitous's id for it, as OSM tags it
    (ref:IFOPT): Germany's DHID "de:05315:16101:7:72" in DELFI's "de-DELFI_de:05315:16101:7:72",
    Switzerland's SLOID "ch:1:sloid:1120:0:668579". None for ids that are not one."""
    if not transitous_id or "_" not in transitous_id:
        return None
    point = transitous_id.split("_", 1)[1]
    # country:...:... with at least three parts, the shape of IFOPT ids.
    return point if re.match(r"^[a-z]{2}:[^:]+:[^:]+", point) else None


def quay_of(stations, point_id):
    """The station and its object carrying the stop-point id `point_id` (ref:IFOPT), from
    /reverse's quays: a stop position on the track rather than a platform beside it, where
    both carry it. (None, None) if none."""
    best = (None, None)
    for station in stations:
        for quay in station.get("quays") or []:
            if point_id in (quay.get("ids") or []):
                if quay.get("on_track"):
                    return station, quay
                best = best if best[1] else (station, quay)
    return best


def stations_at(trip_type, stops, radius_km=0.5):
    """The station at each timetable stop {lat, lng, platform?, name?, key?, id?}: of the trip
    type's mode within `radius_km`, the one holding the stop point its Transitous id names
    (stop_point_id: then its very platform, `platform` its own track, and point, `snap`), the one of that station key (a trip's saved end), else the nearest
    named alike, else the nearest, as {station, station_key, alike, tracks, lines, track, snap},
    or None. `track` is the stop's platform among the station's tracks, or None. `snap`, for
    stops given in the order travelled, is where the line stops there going that way
    (stops_by_direction), [lat, lng], or None. By name first, as points are rough: Bryggen's is 38m from Bryggen and
    39m from another stop.
    """
    mode = QUAI_MODES.get(trip_type)
    found = []
    for stop in stops:
        station = None
        if mode and stop.get("lat") is not None and stop.get("lng") is not None:
            data = quai_get("reverse", {"lat": stop["lat"], "lon": stop["lng"], "mode": mode,
                                        "radius": radius_km, "limit": 5, "quays": 1}, timeout=2)
            nearby = (data or {}).get("stations") or []
            # The station holding the stop point the timetable names; else that of the key;
            # else by place, a name only deciding between stations about as near
            # (station_by_place).
            point_station, quay = quay_of(nearby, stop_point_id(stop.get("id")))
            nearest = point_station or next(
                (s for s in nearby if stop.get("key") and s["station_key"] == stop["key"]), None)
            alike = nearest
            if nearest is None:
                nearest, named = station_by_place(stop.get("name"), nearby)
                alike = nearest if named else None
                if not named and nearest and nearest.get("distance_m", 0) > FAR_M:
                    nearest = None   # far, and not named as the stop: not its station
            if nearest:
                apply_overrides([nearest])
                ref = track_key(stop.get("platform"))
                tracks = nearest.get("tracks") or []
                station = {
                    "station": station_label(nearest),
                    "station_key": nearest["station_key"],
                    "alike": alike is not None,
                    "tracks": tracks,
                    "lines": nearest.get("lines") or [],
                    "track": next((t for t in tracks if ref and track_key(t["ref"]) == ref), None),
                    "snap": None,
                    "platform": None,
                }
                if quay and point_station is nearest:
                    # The very platform: its own track, not the timetable's numbering, and
                    # its point (on the track, for a stop position).
                    own = ((quay.get("ref") or "").split(";") or [""])[0].strip() or None
                    station["platform"] = own
                    station["track"] = next((t for t in tracks if own and track_key(t["ref"]) == track_key(own)),
                                            station["track"])
                    station["snap"] = [quay["lat"], quay["lng"]]
                    station["quay"] = True
        found.append(station)
    if mode in SNAP_MODES:
        keys = [station and station["station_key"] for station in found]
        for station, position in zip(found, stops_by_direction(mode, keys)):
            if station and position and not station.get("quay"):
                station["snap"] = position
    return found
