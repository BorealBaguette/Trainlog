"""What a place is called internationally, and which of its names to show a given person.

Photon's `lang=en` often returns a translation rather than a name ("Munich Hbf", "Prague Main
Station"). international_name() picks the name the place is actually known by, first hit wins:

  1. `int_name`                    the tag meaning exactly this; only ~3% of stations have it
  2. local name, if Latin script   the common case, returned untouched: München Hbf
  3. `name:<lang>-Latn`            a mapper's romanisation beats a generated one
  4. `name:en`, if it is itself    Ukrainian mappers write "Kyiv-Volynskyi" there; see
     a romanisation                ROMANISATION_SIMILARITY
  5. transliteration               scripts ICU romanises well (see TRANSLITERABLE)
  6. `name:en`, then local, then ""

Rules 1 and 3 need OSM tags, so they apply only after enrichment, not in the autocomplete.

Why BGN/PCGN and not ICU's generic `Any-Latin`, measured:

    Москва-Казанская   Any-Latin -> 'Moskva-Kazanskaâ'      BGN -> 'Moskva-Kazanskaya'
    Київ-Пасажирський  Any-Latin -> 'Kií̈v-Pasažirsʹkij'     BGN -> 'Kyyiv-Pasazhyrsʹkyy'
    София              Any-Latin -> 'Sofiâ'                 BGN -> 'Sofiya'
    Երևան              Any-Latin -> 'Erevan'                BGN -> 'Yerevan'

Scripts ICU does not romanise usefully fall through to name:en instead. Measured: 東京 ->
'dong jing', 서울역 -> 'seoul-yeog', กรุงเทพ -> 'krungtheph', القاهرة -> 'lqاhrh' (which does not
even leave the Arabic script). Tokyo, Seoul Station and Bangkok are the real answers.

preferred_spelling() answers a different question — which known spelling to put in front of
the person who just searched. See its docstring.
"""

import difflib
import logging
import re
import unicodedata
from collections import Counter

logger = logging.getLogger(__name__)

# PyICU is optional: it has no wheels and needs an apt layer for the ICU headers. Script
# detection (rule 2, the common case) is pure Python below; only transliteration needs ICU.
# Without it, non-Latin names fall through to name:en — the old behaviour.
try:
    import icu

    HAS_ICU = True
except ImportError:  # pragma: no cover - depends on the deployment image
    icu = None
    HAS_ICU = False
    logger.warning(
        "PyICU is not installed: station names in Cyrillic, Greek, Armenian and Georgian "
        "will fall back to their English name instead of being romanised."
    )

# How close `name:en` must be to the generated transliteration before it counts as a
# romanisation of the local name rather than a translation. Whole countries tag name:en with
# a romanisation better than any transform's: "Kyiv-Volynskyi" over "Kyyiv-Volynskyy".
#
# Measured (difflib ratio, accents folded), which is where 85 comes from:
#
#     romanisations              Yerevan/Yerevan            100.0
#                                Sofia/Sofiya                90.9
#                                Kyiv-Volynskyi/Kyyiv-…      89.7
#                                Kyiv-Tovarnyi/Kyyiv-…       88.9
#                                Kyiv-Demiivskyi/Kyyiv-…     87.5
#     ── threshold 85 ──
#     translations / exonyms     Saint Petersburg/Sankt-…    83.9
#                                Belgrade/Beograd            80.0
#                                Moscow Kazansky/Moskva-…    68.8
#                                Athens/Athína               66.7
#                                Kyiv Passenger Railway…     37.5
#                                Airport/Aërodhrómio         33.3
#
# The gap between 87.5 and 83.9 is the basis for the number. Lower starts preferring exonyms
# ("Belgrade" over "Beograd"); higher discards good mapper romanisations.
ROMANISATION_SIMILARITY = 85

# Scripts (ICU short names) whose BGN/PCGN romanisation is good enough to show a user.
TRANSLITERABLE = frozenset({"Cyrl", "Grek", "Armn", "Geor"})

# Cyrillic is not one romanisation — Ukrainian and Russian differ — so the transform is picked
# by country. KZ, KG, TJ and MN have no BGN transform of their own and use the Russian one,
# which is what their own romanisations are based on.
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

# BGN renders the Cyrillic soft and hard signs as modifier primes ("Pasazhyrsʹkyy"). They
# appear in no timetable, so they are dropped; ASCII quotes are what some transforms emit.
_PRIME_CHARS = str.maketrans("", "", "ʹʺ’ʼ'`")

_transliterator_cache = {}


def _get_transliterator(transform_id):
    if not HAS_ICU:
        return None
    if transform_id not in _transliterator_cache:
        try:
            _transliterator_cache[transform_id] = icu.Transliterator.createInstance(
                transform_id
            )
        except Exception as e:
            logger.warning(f"ICU transform {transform_id} unavailable: {e}")
            _transliterator_cache[transform_id] = None
    return _transliterator_cache[transform_id]


# Unicode character names begin with their script, so stdlib can answer "what script is this"
# without ICU. Values are ICU short names, which is what the transform tables are keyed on.
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
    """The script short name most of `text`'s letters are written in ('Latn', 'Cyrl', …).

    Non-letters do not vote. None if the string has no letters, or an unlisted script.
    Stdlib-only so it works without PyICU; cross-checked against icu.Script.getScript().
    """
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
    """Romanise `text` with the most appropriate BGN/PCGN transform, or None if unavailable.

    Returns None rather than a poor result when the script is one ICU does not romanise
    usefully, so callers fall through to name:en instead of showing 'dong jing'.
    """
    if not text or not HAS_ICU:
        return None
    script = dominant_script(text)
    if script not in TRANSLITERABLE:
        return None

    transform_id = None
    if country_code:
        transform_id = _BGN_BY_COUNTRY.get(country_code.upper())
    # The country's transform must match the text's script, or a Greek-named place in Ukraine
    # goes through the Ukrainian Cyrillic transform.
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
    # Some transforms silently leave the source script in place (Arabic-Latin/BGN: 'lqاhrh').
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
    """True if `name_en` appears to be a romanisation of the local name, not a translation.

    Both describe the same place, so the question is only whether they are the same *word*.
    See ROMANISATION_SIMILARITY for the measurements behind the threshold.
    """
    if not name_en or not romanised:
        return False
    ratio = difflib.SequenceMatcher(None, _fold(name_en), _fold(romanised)).ratio()
    return ratio * 100 >= ROMANISATION_SIMILARITY


def international_name(name_local, name_en, *, country_code=None, tags=None):
    """The name a place should be shown under internationally.

    `name_local` is the OSM `name` (Photon `lang=default`), `name_en` is `name:en`
    (`lang=en`). `tags` is the full OSM tag dict when available — only the registry has it,
    the autocomplete does not — and unlocks rules 1 and 3.

    Returns "" only when given nothing usable.
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

    # 4/5. Romanise, where ICU does it well — but a name:en that is itself a romanisation is
    # a mapper's spelling of the same word and beats the generated one.
    if name_local:
        romanised = transliterate(name_local, country_code)
        if romanised:
            if looks_like_romanisation(name_en, romanised):
                return name_en
            return romanised

    # 6. For the scripts it does not, name:en *is* the international name.
    if name_en:
        return name_en

    # 6. Nothing better to offer than the local name as it stands.
    return name_local or ""


def normalise_for_comparison(name):
    """Fold a name for comparison. Mirrors station_normalize() in migration 0058."""
    if not name:
        return None
    folded = unicodedata.normalize("NFD", name.lower())
    return "".join(
        ch for ch in folded if ch.isalnum() and unicodedata.category(ch) != "Mn"
    ) or None


# How well a typed prefix must match the front of a spelling before that spelling is offered
# instead of the station's own name. 0.7 leaves room for a typo or two — "pietarsar" scores
# 0.89 against Pietarsaari-Pedersöre, "pietrsari" 0.78, while "helsink" scores 0.13.
_SPELLING_MATCH_MIN = 0.7

# And it must be clearly better than the station's own name, not fractionally: a query naming
# a *different* station scored a hair higher against this one's abbreviation than against its
# full name, so the name it was offered under changed as the user kept typing.
_SPELLING_MARGIN = 0.2


def _prefix_similarity(folded_query, folded_name):
    """How well `folded_query` matches the beginning of `folded_name`. 0.0 to 1.0.

    Compared against the leading slice, not the whole name: otherwise the length difference
    between a part-typed query and a full name dominates the score.
    """
    if not folded_query or not folded_name:
        return 0.0
    return difflib.SequenceMatcher(
        None, folded_query, folded_name[: len(folded_query)]
    ).ratio()


def preferred_spelling(query, canonical, matched_alias):
    """Which spelling of a station to offer someone who searched for `query`.

    When the matched spelling is closer to what they typed than the registry's own name is,
    that spelling is offered, and stored on their trip if they pick it. A Finn typing
    "Pietarsaari-Pedersöre" should not be answered "Jakobstad-Pedersöre".

    Not made redundant by the read path (migration 0060), which only applies once a user sets
    station_display='language'; the default is 'international' and defaults are what most
    people run. Nothing is lost either way — resolution is keyed on the normalised label, so
    both spellings still group as one station in every aggregate.
    """
    if not matched_alias or matched_alias == canonical:
        return canonical

    folded_query = normalise_for_comparison(query)
    folded_alias = normalise_for_comparison(matched_alias)
    folded_canonical = normalise_for_comparison(canonical)
    if not folded_query or not folded_alias:
        return canonical

    # 1. The name we would show already contains what was typed: nothing to fix. Stays first
    # and stays exact — a French user typing "Oslo" wants "Gare centrale d'Oslo", and a
    # prefix comparison here handed them "Oslo S" instead, overriding their own setting.
    if folded_canonical and folded_query in folded_canonical:
        return canonical

    # 2. The typed text appears in the alias: clearly the language being searched in.
    if folded_query in folded_alias:
        return matched_alias

    # 3. Neither contains it exactly — the normal case mid-typing. Containment alone was the
    # whole test here and threw away the Finnish spelling for one missing letter, since
    # "pietarsar" is not a substring of "pietarsaaripedersore". Compare fuzzily instead.
    alias_score = _prefix_similarity(folded_query, folded_alias)
    canonical_score = _prefix_similarity(folded_query, folded_canonical)
    if (alias_score >= _SPELLING_MATCH_MIN
            and alias_score - canonical_score >= _SPELLING_MARGIN):
        return matched_alias
    return canonical
