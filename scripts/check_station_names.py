"""
Fixtures for src.station_names.international_name().

The naming rule is a pile of per-script judgement calls, and every one of them was made by
measuring ICU output rather than by reasoning about it. This pins those measurements so a
later change to the script table, the BGN mapping or the prime-stripping cannot quietly
regress a language nobody on the team reads.

There is no test framework in this repo, so this is a standalone script in the same spirit as
the other tools in scripts/. Run it directly:

    python3 scripts/check_station_names.py

Exits non-zero on the first failing set, printing every case.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.station_names import (  # noqa: E402
    HAS_ICU,
    dominant_script,
    international_name,
    is_latin,
    looks_like_romanisation,
    preferred_spelling,
    transliterate,
)

# (local name, name:en, country, tags, expected)
CASES = [
    # ── Rule 2: a Latin local name is already the international name ──────────────
    # These are the ones lang=en actively got wrong: Photon returns "Munich Hbf",
    # "Prague Main Station", "Antwerp Central" for them.
    ("München Hbf", "Munich Hbf", "DE", {}, "München Hbf"),
    ("Antwerpen-Centraal", "Antwerp Central", "BE", {}, "Antwerpen-Centraal"),
    ("Praha hlavní nádraží", "Prague Main Station", "CZ", {}, "Praha hlavní nádraží"),
    ("Zürich Hauptbahnhof", None, "CH", {}, "Zürich Hauptbahnhof"),
    ("Gare de Lyon", None, "FR", {}, "Gare de Lyon"),
    # Diacritics must survive: Latin-ASCII folding would give "Munchen"/"nadrazi".
    ("Malmö Centralstation", None, "SE", {}, "Malmö Centralstation"),
    ("Gdańsk Główny", None, "PL", {}, "Gdańsk Główny"),
    # ── Rule 4: scripts ICU romanises well, via language-specific BGN ─────────────
    # name:en here is a translation ("Moscow Kazansky"), so the transliteration wins.
    ("Москва-Казанская", "Moscow Kazansky", "RU", {}, "Moskva-Kazanskaya"),
    ("Санкт-Петербург", None, "RU", {}, "Sankt-Peterburg"),
    # …and an exonym is a translation too: Belgrade is not how Београд is spelled.
    ("Београд", "Belgrade", "RS", {}, "Beograd"),
    ("Αθήνα", "Athens", "GR", {}, "Athína"),
    ("Երևան", "Yerevan", "AM", {}, "Yerevan"),
    # Georgian has no case; the transform yields lowercase and we titlecase it.
    ("თბილისი", "Tbilisi", "GE", {}, "Tbilisi"),
    # ── Rule 5: scripts it does not — name:en IS the international name ───────────
    # Left to ICU these become 'dong jing', 'seoul-yeog', 'krungtheph', 'lqاhrh',
    # 'tl ’vyv'. Measured, not assumed.
    ("東京", "Tokyo", "JP", {}, "Tokyo"),
    ("서울역", "Seoul Station", "KR", {}, "Seoul Station"),
    ("北京南", "Beijingnan", "CN", {}, "Beijingnan"),
    ("กรุงเทพ", "Bangkok", "TH", {}, "Bangkok"),
    ("القاهرة", "Cairo", "EG", {}, "Cairo"),
    ("תל אביב", "Tel Aviv", "IL", {}, "Tel Aviv"),
    # ── Rule 1: int_name overrides everything ────────────────────────────────────
    # The real tags on node/2644051429. Without int_name the BGN fallback gives
    # "Kyyiv-Pasazhyrskyy" (see below) — this is why the override exists.
    (
        "Київ-Пасажирський",
        "Kyiv Passenger Railway Station",
        "UA",
        {"int_name": "Kyiv-Pasazhyrskyi"},
        "Kyiv-Pasazhyrskyi",
    ),
    # ── Rule 3: a mapper's romanisation beats a generated one ────────────────────
    ("서울역", "Seoul Station", "KR", {"name:ko-Latn": "Seoulyeok"}, "Seoulyeok"),
    # ── Rule 4: name:en wins when it is a romanisation, not a translation ────────
    # Ukrainian mappers romanise in name:en, and their spelling is the one on the ticket.
    # BGN would give Kyyiv-Volynskyy / Kyyiv-Demiyivskyy / Kyyiv-Tovarnyy.
    ("Київ-Волинський", "Kyiv-Volynskyi", "UA", {}, "Kyiv-Volynskyi"),
    ("Київ-Деміївський", "Kyiv-Demiivskyi", "UA", {}, "Kyiv-Demiivskyi"),
    ("Київ-Товарний", "Kyiv-Tovarnyi", "UA", {}, "Kyiv-Tovarnyi"),
    # Sofia/Sofiya are the same word; the mapper's spelling is the accepted one.
    ("София", "Sofia", "BG", {}, "Sofia"),
    # But a translation of the same place must NOT displace the transliteration, however
    # much of the name it happens to share.
    ("Київ-Пасажирський", "Kyiv Passenger Railway Station", "UA", {}, "Kyyiv-Pasazhyrskyy"),
    ("Αεροδρόμιο", "Airport", "GR", {}, "Aërodhrómio"),
    # ── Degenerate inputs ────────────────────────────────────────────────────────
    (None, "Only English", "FR", {}, "Only English"),
    ("", "", None, {}, ""),
    (None, None, None, {}, ""),
    # A local name with no letters at all must not crash the script detection.
    ("42", None, "FR", {}, "42"),
]

# Cases that document a known limitation rather than a guarantee. Ukrainian BGN renders
# -ий as -yy and Ки- as Kyy-, so the generated form is not the one Ukrainians write. It is
# close enough to be recognisable and to search on, and int_name / name:uk-Latn /
# curated_name all override it — but pretending it is correct would be worse than recording
# that it is not.

# Which spelling to offer someone, once a station is known by several.
#
# The Finnish/Swedish pair is the case this exists for: a station whose OSM `name` is the
# Swedish "Jakobstad-Pedersöre" and whose name:fi is "Pietarsaari-Pedersöre". A Finnish
# speaker must not have to be answered in Swedish, and must not have to change an account
# setting to avoid it.
#
# The partial and mistyped queries are the point of the fixtures. An autocomplete is queried
# with prefixes, and an exact-containment test — which is what this used to do — throws the
# match away the moment a letter is missing or wrong, which is most of the time somebody is
# typing. (query, canonical, alias, expected)
_FI = ("Jakobstad-Pedersöre", "Pietarsaari-Pedersöre")
SPELLING_CASES = [
    # Typing the Finnish name, one letter at a time.
    ("Pie", *_FI, "Pietarsaari-Pedersöre"),
    ("Piet", *_FI, "Pietarsaari-Pedersöre"),
    ("Pietars", *_FI, "Pietarsaari-Pedersöre"),
    ("Pietarsaari", *_FI, "Pietarsaari-Pedersöre"),
    ("Pietarsaari-Pedersöre", *_FI, "Pietarsaari-Pedersöre"),
    # Typos, which the trigram search tolerates and this must not undo.
    ("Pietarsar", *_FI, "Pietarsaari-Pedersöre"),
    ("Pietarsari", *_FI, "Pietarsaari-Pedersöre"),
    ("Pietrsari", *_FI, "Pietarsaari-Pedersöre"),
    ("pietarsaari", *_FI, "Pietarsaari-Pedersöre"),
    # Typing the Swedish name: answered in Swedish, obviously.
    ("Jak", *_FI, "Jakobstad-Pedersöre"),
    ("Jakobstad", *_FI, "Jakobstad-Pedersöre"),
    # A query that merely dragged this station back as a weak trigram hit must not rename it.
    ("Helsink", *_FI, "Jakobstad-Pedersöre"),
    # The displayed name already contains the query: nothing to correct. This is what stops
    # a French user who set "my language" and typed "Oslo" being handed "Oslo S".
    ("Oslo", "Gare centrale d'Oslo", "Oslo S", "Gare centrale d'Oslo"),
    ("munchen", "München Hbf", "München Hauptbahnhof", "München Hbf"),
    # An abbreviation alias (OSM short_name) must never become the station's name.
    ("Kyiv", "Kyiv-Pasazhyrskyi", "Київ-Пас", "Kyiv-Pasazhyrskyi"),
]

KNOWN_IMPERFECT = [
    (
        "Київ-Пасажирський",
        "Kyiv Passenger Railway Station",
        "UA",
        {},
        "Kyyiv-Pasazhyrskyy",
        "accepted form is Kyiv-Pasazhyrskyi; needs int_name or curation",
    ),
]

# The country picks the transform: the same Cyrillic string romanises differently in
# Russian and Ukrainian, and using the wrong one produces a spelling nobody recognises.
COUNTRY_SENSITIVITY = [
    ("и", "RU", "i"),
    ("и", "UA", "y"),
]

SCRIPT_CASES = [
    ("München Hbf", "Latn", True),
    ("Москва", "Cyrl", False),
    ("Αθήνα", "Grek", False),
    ("東京", "Hani", False),
    ("서울역", "Hang", False),
    # Punctuation, digits and the flag emoji must not vote.
    ("🇫🇷 Gare de Lyon (a)", "Latn", True),
    ("123 - 456", None, False),
]


def main():
    failures = []

    # Without PyICU the romanisation cases fall back to name:en and fail. That is a real
    # failure worth reporting — but say why, so it does not read as a logic bug.
    if not HAS_ICU:
        print(
            "PyICU is NOT installed. Every case that romanises Cyrillic, Greek, Armenian or\n"
            "Georgian will fail below, because those names fall back to name:en instead.\n"
            "Install it with:\n"
            "    sudo apt-get install -y libicu-dev pkg-config\n"
            "    pip install PyICU\n"
        )

    print("international_name()")
    for local, en, cc, tags, expected in CASES:
        got = international_name(local, en, country_code=cc, tags=tags)
        ok = got == expected
        if not ok:
            failures.append(f"international_name({local!r}) -> {got!r}, want {expected!r}")
        print(f"  {'ok  ' if ok else 'FAIL'}  {str(local):24} -> {got!r}")

    print("\ndominant_script() / is_latin()")
    for text, expected_script, expected_latin in SCRIPT_CASES:
        script, latin = dominant_script(text), is_latin(text)
        ok = script == expected_script and latin == expected_latin
        if not ok:
            failures.append(
                f"dominant_script({text!r}) -> {script!r}/{latin}, "
                f"want {expected_script!r}/{expected_latin}"
            )
        print(f"  {'ok  ' if ok else 'FAIL'}  {text:24} -> {script} latin={latin}")

    print("\ntransliterate() picks the transform by country")
    for text, cc, expected_substring in COUNTRY_SENSITIVITY:
        got = transliterate(text, cc)
        ok = got is not None and expected_substring in got.lower()
        if not ok:
            failures.append(
                f"transliterate({text!r}, {cc}) -> {got!r}, want to contain "
                f"{expected_substring!r}"
            )
        print(f"  {'ok  ' if ok else 'FAIL'}  {text!r} as {cc} -> {got!r}")

    print("\ntransliterate() declines scripts it cannot romanise")
    for text in ["東京", "서울역", "กรุงเทพ", "القاهرة", "תל אביב"]:
        got = transliterate(text, None)
        ok = got is None
        if not ok:
            failures.append(f"transliterate({text!r}) -> {got!r}, want None")
        print(f"  {'ok  ' if ok else 'FAIL'}  {text:12} -> {got!r}")

    print("\nlooks_like_romanisation() — the 85 threshold, at its measured margin")
    for name_en, romanised, expected in [
        ("Kyiv-Demiivskyi", "Kyyiv-Demiyivskyy", True),  # 87.5, closest True
        ("Saint Petersburg", "Sankt-Peterburg", False),  # 83.9, closest False
        ("Sofia", "Sofiya", True),
        ("Belgrade", "Beograd", False),
        ("Moscow Kazansky", "Moskva-Kazanskaya", False),
        ("Airport", "Aërodhrómio", False),
        (None, "Anything", False),
    ]:
        got = looks_like_romanisation(name_en, romanised)
        ok = got == expected
        if not ok:
            failures.append(
                f"looks_like_romanisation({name_en!r}, {romanised!r}) -> {got}, "
                f"want {expected}"
            )
        print(f"  {'ok  ' if ok else 'FAIL'}  {str(name_en):18} vs {romanised:20} -> {got}")

    print("\npreferred_spelling() — which spelling to offer, incl. partial and mistyped input")
    for query, canonical, alias, expected in SPELLING_CASES:
        got = preferred_spelling(query, canonical, alias)
        ok = got == expected
        if not ok:
            failures.append(
                f"preferred_spelling({query!r}, {canonical!r}, {alias!r}) -> {got!r}, "
                f"want {expected!r}"
            )
        print(f"  {'ok  ' if ok else 'FAIL'}  {query!r:24} -> {got!r}")

    print("\nknown imperfect (recorded, not asserted correct)")
    for local, en, cc, tags, current, why in KNOWN_IMPERFECT:
        got = international_name(local, en, country_code=cc, tags=tags)
        # Assert the *current* behaviour so a change is noticed, not that it is right.
        ok = got == current
        if not ok:
            failures.append(f"known-imperfect {local!r} -> {got!r}, was {current!r}")
        print(f"  {'ok  ' if ok else 'CHANGED'}  {local} -> {got!r}  ({why})")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print("  -", f)
        return 1
    print(
        f"all {len(CASES) + len(SCRIPT_CASES) + len(COUNTRY_SENSITIVITY)
           + len(SPELLING_CASES) + 5} checks passed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
