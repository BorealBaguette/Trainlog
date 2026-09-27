"""What a place is called internationally, and which of its names to show a given person.

Photon's `lang=en` often returns a translation ("Munich Hbf") rather than the name the place
is known by. international_name() picks that name, first hit wins:

  1. `int_name`
  2. the local name, if it is in Latin script
  3. `name:<lang>-Latn`, a mapper's romanisation
  4. `name:en`, if it is itself a romanisation of the local name
  5. BGN/PCGN transliteration, for scripts ICU romanises well (see TRANSLITERABLE)
  6. `name:en`, then the local name

Rules 1 and 3 need OSM tags, so they apply only after enrichment. BGN rather than ICU's
generic Any-Latin because it gives the spellings timetables use (Moskva-Kazanskaya, not Moskva-Kazanskaâ).
"""

import difflib
import logging
import re
import unicodedata
from collections import Counter

import icu

logger = logging.getLogger(__name__)


# How close `name:en` must be to the transliteration to count as a romanisation of the
# local name rather than a translation. Lower starts preferring exonyms (Belgrade over
# Beograd); higher loses mapper romanisations (Kyiv-Volynskyi over Kyyiv-Volynskyy).
ROMANISATION_SIMILARITY = 85

# Scripts (ICU short names) whose BGN/PCGN romanisation is good enough to show a user.
TRANSLITERABLE = frozenset({"Cyrl", "Grek", "Armn", "Geor"})

# Cyrillic romanisation depends on the language, hence by country. KZ, KG, TJ and MN
# have no BGN transform of their own and use the Russian one.
_BGN_BY_COUNTRY = {
    "RU": "Russian-Latin/BGN",
    "KZ": "Russian-Latin/BGN",
    "KG": "Russian-Latin/BGN",
    "TJ": "Russian-Latin/BGN",
    "MN": "Russian-Latin/BGN",
    "UA": "Ukrainian-Latin/BGN",
    "BY": "Belarusian-Latin/BGN",
    "BG": "Bulgarian-Latin/BGN",
    "RS": "Serbian-Latin/BGN",
    "ME": "Serbian-Latin/BGN",
    "BA": "Serbian-Latin/BGN",
    "MK": "Macedonian-Latin/BGN",
    "GR": "Greek-Latin/BGN",
    "CY": "Greek-Latin/BGN",
    "AM": "Armenian-Latin/BGN",
    "GE": "Georgian-Latin/BGN",
}

# Per script, when the country is unknown or not in the table above.
_BGN_BY_SCRIPT = {
    "Cyrl": "Russian-Latin/BGN",
    "Grek": "Greek-Latin/BGN",
    "Armn": "Armenian-Latin/BGN",
    "Geor": "Georgian-Latin/BGN",
}

# BGN renders soft and hard signs as primes, which no timetable uses.
_PRIME_CHARS = str.maketrans("", "", "ʹʺ’ʼ'`")

_transliterator_cache = {}


def _get_transliterator(transform_id):
    if transform_id not in _transliterator_cache:
        try:
            _transliterator_cache[transform_id] = icu.Transliterator.createInstance(
                transform_id
            )
        except Exception as e:
            logger.warning(f"ICU transform {transform_id} unavailable: {e}")
            _transliterator_cache[transform_id] = None
    return _transliterator_cache[transform_id]


# Unicode character names begin with their script. Values are ICU short names.
_SCRIPT_BY_NAME_PREFIX = {
    "LATIN": "Latn",
    "CYRILLIC": "Cyrl",
    "GREEK": "Grek",
    "ARMENIAN": "Armn",
    "GEORGIAN": "Geor",
    "HANGUL": "Hang",
    "HIRAGANA": "Hira",
    "KATAKANA": "Kana",
    "THAI": "Thai",
    "LAO": "Laoo",
    "KHMER": "Khmr",
    "MYANMAR": "Mymr",
    "ARABIC": "Arab",
    "HEBREW": "Hebr",
    "DEVANAGARI": "Deva",
    "BENGALI": "Beng",
    "TAMIL": "Taml",
    "ETHIOPIC": "Ethi",
    "TIBETAN": "Tibt",
}


def _script_of_char(ch):
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    # 'CJK UNIFIED IDEOGRAPH-6771' and friends: the script word is not the first one.
    if "IDEOGRAPH" in name:
        return "Hani"
    return _SCRIPT_BY_NAME_PREFIX.get(name.split()[0])


def dominant_script(text):
    """The ICU short name of the script most of `text`'s letters are in, or None."""
    if not text:
        return None
    scripts = [
        script
        for script in (_script_of_char(ch) for ch in text if ch.isalpha())
        if script is not None
    ]
    if not scripts:
        return None
    return Counter(scripts).most_common(1)[0][0]


def is_latin(text):
    """True if `text` is written predominantly in the Latin script."""
    return dominant_script(text) == "Latn"


def transliterate(text, country_code=None):
    """Romanise `text` with the matching BGN/PCGN transform, or None.

    None for scripts ICU does not romanise usefully, so callers fall through to name:en
    rather than showing 'dong jing' for 東京.
    """
    if not text:
        return None
    script = dominant_script(text)
    if script not in TRANSLITERABLE:
        return None

    transform_id = None
    if country_code:
        transform_id = _BGN_BY_COUNTRY.get(country_code.upper())
    # The country's transform must match the script: a Greek name in Ukraine is not Ukrainian.
    if transform_id and _BGN_BY_SCRIPT.get(script) is not None:
        expected_script_family = _BGN_BY_SCRIPT[script].split("-")[0]
        cyrillic_family = {
            "Russian",
            "Ukrainian",
            "Belarusian",
            "Bulgarian",
            "Serbian",
            "Macedonian",
        }
        chosen = transform_id.split("-")[0]
        if script == "Cyrl":
            if chosen not in cyrillic_family:
                transform_id = None
        elif chosen != expected_script_family:
            transform_id = None
    if not transform_id:
        transform_id = _BGN_BY_SCRIPT.get(script)
    if not transform_id:
        return None

    tr = _get_transliterator(transform_id)
    if tr is None:
        return None

    result = tr.transliterate(text).translate(_PRIME_CHARS)
    # Georgian has no case distinction, so its transform yields 'tbilisi'.
    if script == "Geor":
        result = result.title()
    result = re.sub(r"\s+", " ", result).strip()
    # Some transforms leave the source script in place.
    if not result or not is_latin(result):
        return None
    return unicodedata.normalize("NFC", result)


def _fold(text):
    """Lowercase and strip accents, for comparing two spellings of the same name."""
    return "".join(
        ch
        for ch in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(ch) != "Mn"
    )


def looks_like_romanisation(name_en, romanised):
    """True if `name_en` looks like a romanisation of the local name, not a translation."""
    if not name_en or not romanised:
        return False
    ratio = difflib.SequenceMatcher(None, _fold(name_en), _fold(romanised)).ratio()
    return ratio * 100 >= ROMANISATION_SIMILARITY


def international_name(name_local, name_en, *, country_code=None, tags=None):
    """The name a place should be shown under internationally.

    `name_local` is the OSM `name`, `name_en` is `name:en`. `tags` is the full OSM tag dict,
    available after enrichment. Returns "" only when given nothing usable.
    """
    tags = tags or {}

    # 1. int_name: the tag that means exactly this.
    int_name = (tags.get("int_name") or "").strip()
    if int_name:
        return int_name

    name_local = (name_local or "").strip()
    name_en = (name_en or "").strip()

    # 2. A Latin-script local name is already the international name.
    if name_local and is_latin(name_local):
        return name_local

    # 3. A romanisation curated by mappers beats one we generate.
    for key, value in tags.items():
        if key.startswith("name:") and key.endswith("-Latn") and value.strip():
            return value.strip()

    # 4/5. A name:en that is itself a romanisation beats the generated one.
    if name_local:
        romanised = transliterate(name_local, country_code)
        if romanised:
            if looks_like_romanisation(name_en, romanised):
                return name_en
            return romanised

    # 6. name:en, then the local name.
    if name_en:
        return name_en
    return name_local or ""


def normalise_for_comparison(name):
    """Fold a name for comparison. Mirrors station_normalize() in SQL."""
    if not name:
        return None
    folded = unicodedata.normalize("NFD", name.lower())
    return "".join(
        ch for ch in folded if ch.isalnum() and unicodedata.category(ch) != "Mn"
    ) or None


# How closely a typed prefix must match a spelling for it to be offered, leaving room for
# a typo or two.
_SPELLING_MATCH_MIN = 0.7

# And by how much it must beat the station's own name, so the offered name does not
# flip back and forth as the user types.
_SPELLING_MARGIN = 0.2


def _prefix_similarity(folded_query, folded_name):
    """How well `folded_query` matches the beginning of `folded_name`, 0.0 to 1.0."""
    if not folded_query or not folded_name:
        return 0.0
    return difflib.SequenceMatcher(
        None, folded_query, folded_name[: len(folded_query)]
    ).ratio()


def preferred_spelling(query, canonical, matched_alias):
    """Which spelling of a station to offer someone who searched for `query`.

    A Finn typing "Pietarsaari" is offered the Finnish name rather than "Jakobstad". Both
    spellings resolve to the same station, so this only affects the stored label.
    """
    if not matched_alias or matched_alias == canonical:
        return canonical

    folded_query = normalise_for_comparison(query)
    folded_alias = normalise_for_comparison(matched_alias)
    folded_canonical = normalise_for_comparison(canonical)
    if not folded_query or not folded_alias:
        return canonical

    # 1. The name we would show already contains what was typed.
    if folded_canonical and folded_query in folded_canonical:
        return canonical

    # 2. The alias contains it: that is the language being typed.
    if folded_query in folded_alias:
        return matched_alias

    # 3. Mid-typing, neither contains it exactly, so compare fuzzily.
    alias_score = _prefix_similarity(folded_query, folded_alias)
    canonical_score = _prefix_similarity(folded_query, folded_canonical)
    if (alias_score >= _SPELLING_MATCH_MIN
            and alias_score - canonical_score >= _SPELLING_MARGIN):
        return matched_alias
    return canonical
