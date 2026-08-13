"""
Fill in the vessel register from Wikidata.

`vessels` (migration 0054) was seeded from what the old photo cache happened to hold:
mostly an MMSI parsed out of a cached filename, a name where the user typed one, and an
IMO only where the data agreed with itself. Most rows are therefore missing at least one
of the three, and a trip whose `reg` resolves to a nameless row still shows a number.

Wikidata is the source here because it is the only free one that is both machine-
queryable and licence-clean: IMO is P458, MMSI is P587, the data is CC0, and the SPARQL
endpoint needs no key. Coverage on this register is roughly half by MMSI and nearly all
by IMO — ferries with a Wikipedia article, which is most of the ones people log.

Three passes, each keyed on an exact number so a match cannot be the wrong ship:

  1. MMSI  -> fill in a missing name and IMO
  2. IMO   -> fill in a missing name and MMSI
  3. every ferry `reg` that is a bare 7- or 9-digit number and resolves to nothing ->
     create the vessel, which makes those trips read as a ship name

Existing values are never overwritten, only NULLs are filled: a name an admin curated
outranks a label from anywhere else. A number Wikidata offers that another vessel
already holds is reported and skipped rather than moved — the unique indexes would
reject it, and a clash means one of the two matches is wrong.

Names are deliberately NOT matched. A ship's name is not unique (a test run matched 25
of 120 unmatched registrations by exact English label, and 10 of those hit several
different ships called Gotland, Esperanza, Europalink…), so a name match is a suggestion
for a human, not a fact to write. Pass --report-names to list the candidates instead.

Run:
    docker compose exec trainlog python -m scripts.backfill_vessels          # dry run
    docker compose exec trainlog python -m scripts.backfill_vessels --apply
"""

import argparse
import json
import logging
import re

# The Wikidata access lives in src/ because the photo lookup needs it too.
from src.pg import pg_session
from src.wikidata import lookup, sparql
from src.wikidata import value as _value

logger = logging.getLogger(__name__)


# Which table each identifier lives on since migration 0056: the IMO is the hull's, the
# MMSI belongs to one of its registrations.
_NUMBER_TABLES = {"imo": "vessels", "mmsi": "vessel_registrations"}


def _claimed_elsewhere(pg, column, value, uid=None):
    """Whether a row other than `uid` already holds this IMO/MMSI. `uid` is the hull for
    an IMO and the registration for an MMSI — the row that would be written."""
    table = _NUMBER_TABLES[column]
    return bool(
        pg.execute(
            f"SELECT 1 FROM {table} WHERE {column} = :value"
            " AND (CAST(:uid AS INTEGER) IS NULL OR uid <> CAST(:uid AS INTEGER)) LIMIT 1",
            {"value": value, "uid": uid},
        ).fetchone()
    )


# ITU maritime identification digits: the first three of an MMSI name the flag state
# that issued it. The same table the photo lookup uses, loaded lazily so the CLI does not
# pay for it when only the numeric passes run.
_MIDS_PATH = "static/data/mids.json"
_mids = None


def _flag_from_mmsi(mmsi):
    """The country an MMSI was issued by, or None."""
    global _mids
    if not mmsi or len(mmsi) < 3:
        return None
    if _mids is None:
        try:
            with open(_MIDS_PATH) as handle:
                _mids = json.load(handle)
        except OSError:
            _mids = {}
    entry = _mids.get(mmsi[:3])
    return entry[0] if entry else None


def _valid_number(value, digits):
    value = (value or "").strip()
    return value if value.isdigit() and len(value) == digits else None


def _free_number(pg, column, value, digits):
    """`value` if it is a well-formed number no vessel holds yet, otherwise None."""
    value = _valid_number(value, digits)
    if not value:
        return None
    return None if _claimed_elsewhere(pg, column, value) else value


# Wikidata's label service answers with the bare Q-id when an item has no label in the
# requested language, and a handful of ship items are titled by their own IMO number.
# Neither is a name; a row is better left nameless than labelled "Q11281568".
_JUNK_NAME = re.compile(r"^(Q\d+|IMO\s*\d{7}|MMSI\s*\d{9})$", re.IGNORECASE)


def _clean_name(value):
    value = (value or "").strip()
    if not value or _JUNK_NAME.match(value):
        return None
    return value


def _proposed_changes(vessel, source):
    """The fields `source` supplies that this vessel is missing. Format only — the
    uniqueness checks belong to whoever writes, since the register can move in
    between (see apply_plan)."""
    changes = {}

    name = _clean_name(source.get("name"))
    if name and not vessel["name"]:
        changes["name"] = name
    for column, digits in (("imo", 7), ("mmsi", 9)):
        incoming = _valid_number(source.get(column), digits)
        if incoming and not vessel[column]:
            changes[column] = incoming

    return changes


def _photo_candidates(pg):
    """
    Registrations whose photo could be a freely-licensed one instead.

    Every cached photo so far came from an image search: a copyrighted still, credited to
    the site that hosted it, kept on our disk. Where Wikidata knows the ship by number and
    has a P18, Commons offers the same ship under a licence that permits the use — with an
    author to credit, which is the condition attached.

    Offered, not applied: replacing a picture is a visible change and the Commons file may
    be worse, older, or of the ship in another livery. Photos an admin uploaded are left
    alone entirely.
    """
    rows = pg.execute(
        """
        SELECT r.uid AS registration_id,
               r.name,
               v.imo,
               r.mmsi,
               p.local_image_path AS current_image,
               p.source AS current_source
        FROM vessel_registrations r
        JOIN vessels v ON v.uid = r.vessel_id
        LEFT JOIN LATERAL (
            SELECT local_image_path, source
            FROM ship_pictures
            WHERE registration_id = r.uid AND local_image_path IS NOT NULL
            ORDER BY fetch_date DESC NULLS LAST, uid DESC
            LIMIT 1
        ) p ON TRUE
        WHERE (v.imo IS NOT NULL OR r.mmsi IS NOT NULL)
          -- Already free, or the admin's own: nothing to offer.
          AND (p.source IS NULL OR p.source = 'vesselfinder')
        """
    ).fetchall()
    if not rows:
        return []

    by_imo = lookup([r["imo"] for r in rows if r["imo"]], "P458")
    by_mmsi = lookup([r["mmsi"] for r in rows if r["mmsi"]], "P587")

    candidates = []
    for row in rows:
        source = by_imo.get(row["imo"]) or by_mmsi.get(row["mmsi"]) or {}
        image = source.get("image")
        if not image:
            continue
        candidates.append(
            {
                "registration_id": row["registration_id"],
                "name": row["name"],
                "imo": row["imo"],
                "mmsi": row["mmsi"],
                "current_image": (
                    f"/static/images/ship_pictures/{row['current_image']}"
                    if row["current_image"]
                    else None
                ),
                "current_source": row["current_source"],
                "image": image,
            }
        )

    # Ships with no picture at all first: those gain the most.
    candidates.sort(key=lambda c: (c["current_image"] is not None, c["name"] or ""))
    return candidates


def build_plan(report_names=False):
    """
    What the register is missing and what Wikidata would put there — as data, written
    nowhere. This is the slow half: several SPARQL round trips, tens of seconds.

    The result is JSON-serialisable on purpose, so a preview can be handed straight
    back to apply_plan() instead of every button press querying Wikidata again.
    """
    filled = []
    created = []
    suggestions = []

    with pg_session() as pg:
        # A hull with its current identity, flattened — the three fields the passes
        # below fill in, wherever they happen to live now (migration 0056).
        vessels = [
            dict(row._mapping)
            for row in pg.execute(
                """
                SELECT v.uid, v.imo, r.uid AS registration_id, r.name, r.mmsi
                FROM vessels v
                LEFT JOIN vessel_registrations r ON r.uid = vessel_identity(v.uid, NULL)
                """
            ).fetchall()
        ]

        # Passes 1 and 2. A vessel already complete in all three fields has nothing to
        # gain and is not looked up at all.
        incomplete = [v for v in vessels if not (v["name"] and v["imo"] and v["mmsi"])]
        by_mmsi = lookup([v["mmsi"] for v in incomplete if v["mmsi"]], "P587")
        by_imo = lookup([v["imo"] for v in incomplete if v["imo"]], "P458")

        for vessel in incomplete:
            source = by_mmsi.get(vessel["mmsi"]) or by_imo.get(vessel["imo"])
            if not source:
                continue
            changes = _proposed_changes(vessel, source)
            if changes:
                filled.append(
                    {
                        "uid": vessel["uid"],
                        "label": vessel["name"] or vessel["imo"] or vessel["mmsi"],
                        "changes": changes,
                    }
                )

        # Pass 3: numbers logged on trips that name no vessel at all. Note this reads
        # the register as it stands, so a reg that pass 1 is about to rescue by filling
        # in an IMO still shows up here; apply_plan drops it when it turns out to
        # resolve by then.
        orphan_regs = [
            row[0]
            for row in pg.execute(
                """
                SELECT DISTINCT btrim(reg) FROM trips
                WHERE trip_type = 'ferry'
                  AND reg IS NOT NULL AND btrim(reg) <> ''
                  AND btrim(reg) ~ '^([0-9]{7}|[0-9]{9})$'
                  AND vessel_resolve(reg) IS NULL
                """
            ).fetchall()
        ]
        orphan_imos = [r for r in orphan_regs if len(r) == 7]
        orphan_mmsis = [r for r in orphan_regs if len(r) == 9]
        found = {**lookup(orphan_imos, "P458"), **lookup(orphan_mmsis, "P587")}

        for reg in orphan_regs:
            source = found.get(reg)
            if not source:
                continue
            # The reg is the number the trips carry, so it is the one the new row must
            # hold — it is what they have to resolve through. The other number comes
            # from Wikidata and is only taken when free.
            created.append(
                {
                    "reg": reg,
                    "name": _clean_name(source.get("name")),
                    "imo": reg if len(reg) == 7 else _free_number(pg, "imo", source.get("imo"), 7),
                    "mmsi": reg if len(reg) == 9 else _free_number(pg, "mmsi", source.get("mmsi"), 9),
                }
            )

        photos = []
        if report_names:
            suggestions = _name_candidates(pg)
            photos = _photo_candidates(pg)

    return {
        "filled": filled,
        "created": created,
        "suggestions": suggestions,
        "photos": photos,
    }


def apply_plan(plan):
    """
    Write a plan from build_plan(). No network at all — this is what an "apply" after a
    preview runs, so the numbers shown are the numbers written.

    Every item is re-checked against the register first: a plan can be minutes old, and
    passes 1-2 of the same plan change what pass 3 needs to do. Only NULLs are ever
    filled, and a number another vessel has taken meanwhile is skipped, not moved.
    """
    applied_filled = []
    applied_created = []
    skipped = []

    with pg_session() as pg:
        for item in plan.get("filled") or []:
            vessel = pg.execute(
                """
                SELECT v.uid, v.imo, r.uid AS registration_id, r.name, r.mmsi
                FROM vessels v
                LEFT JOIN vessel_registrations r ON r.uid = vessel_identity(v.uid, NULL)
                WHERE v.uid = :uid
                """,
                {"uid": item["uid"]},
            ).fetchone()
            if not vessel:
                skipped.append(f"vessel #{item['uid']} no longer exists")
                continue

            vessel = dict(vessel._mapping)
            changes = _proposed_changes(vessel, item.get("changes") or {})
            registration_id = vessel["registration_id"]

            for column in ("imo", "mmsi"):
                if column not in changes:
                    continue
                owner = vessel["uid"] if column == "imo" else registration_id
                if _claimed_elsewhere(pg, column, changes[column], owner):
                    skipped.append(
                        f"vessel #{item['uid']}: {column.upper()} {changes[column]} is taken"
                    )
                    changes.pop(column)

            # The name and MMSI belong to a registration; a hull that has none cannot
            # take them until someone records one.
            if not registration_id:
                for column in ("name", "mmsi"):
                    if changes.pop(column, None):
                        skipped.append(
                            f"vessel #{item['uid']}: no registration to put its {column} on"
                        )
            if not changes:
                continue

            hull_changes = {k: v for k, v in changes.items() if k == "imo"}
            registration_changes = {k: v for k, v in changes.items() if k != "imo"}

            if hull_changes:
                assignments = ", ".join(f"{c} = :{c}" for c in hull_changes)
                pg.execute(
                    f"UPDATE vessels SET {assignments}, updated_on = CURRENT_TIMESTAMP"
                    " WHERE uid = :uid",
                    {**hull_changes, "uid": vessel["uid"]},
                )
            if registration_changes:
                assignments = ", ".join(f"{c} = :{c}" for c in registration_changes)
                pg.execute(
                    f"UPDATE vessel_registrations SET {assignments},"
                    " updated_on = CURRENT_TIMESTAMP WHERE uid = :uid",
                    {**registration_changes, "uid": registration_id},
                )
            applied_filled.append({**item, "changes": changes})

        for item in plan.get("created") or []:
            reg = (item.get("reg") or "").strip()
            # Passes 1-2 may have just given some other row this very number.
            if pg.execute(
                "SELECT vessel_resolve(:reg)", {"reg": reg}
            ).scalar() is not None:
                continue

            row = {
                "name": _clean_name(item.get("name")),
                "imo": _free_number(pg, "imo", item.get("imo"), 7),
                "mmsi": _free_number(pg, "mmsi", item.get("mmsi"), 9),
            }
            if not (row["name"] or row["imo"] or row["mmsi"]):
                skipped.append(f"{reg}: nothing left to identify it by")
                continue

            # A new ship is a hull plus the one identity we know it by.
            vessel_id = pg.execute(
                "INSERT INTO vessels (imo) VALUES (:imo) RETURNING uid",
                {"imo": row["imo"]},
            ).scalar()
            if row["name"] or row["mmsi"]:
                pg.execute(
                    """
                    INSERT INTO vessel_registrations (vessel_id, name, mmsi)
                    VALUES (:vessel_id, :name, :mmsi)
                    """,
                    {"vessel_id": vessel_id, "name": row["name"], "mmsi": row["mmsi"]},
                )
            applied_created.append({**item, **row})

    return {"filled": applied_filled, "created": applied_created, "skipped": skipped}


def backfill(apply=False, report_names=False):
    """Build a plan and, if asked, write it. The CLI's one-shot path."""
    plan = build_plan(report_names=report_names)
    if not apply:
        return {**plan, "applied": False, "skipped": []}

    written = apply_plan(plan)
    return {
        "filled": written["filled"],
        "created": written["created"],
        "suggestions": plan["suggestions"],
        "photos": plan.get("photos", []),
        "skipped": written["skipped"],
        "applied": True,
    }


def _name_candidates(pg):
    """
    Ferry regs that are a name we hold no vessel for, and every ship Wikidata has under
    that exact English label.

    These are suggestions, never writes. A ship's name is not unique — several real ships
    are called Gotland, Esperanza or Europalink — so a label match is a lead for a human
    to confirm. Every candidate is returned rather than only the unambiguous ones,
    together with the two things that actually tell them apart:

      * where the trips went, from each trip's own `countries` breakdown. A ferry logged
        between Finland and Sweden is not the Gotland registered in Panama.
      * where each candidate is registered, from Wikidata's country (P17).

    Each carries the number of trips riding on it, because that is what says which are
    worth the click.
    """
    rows = pg.execute(
        """
        WITH unresolved AS (
            SELECT trip_id,
                   btrim(reg) AS reg,
                   operator,
                   -- Free text in principle; a malformed value must not abort the query.
                   CASE WHEN countries ~ '^\s*\{' THEN countries::jsonb END AS countries
            FROM trips
            WHERE trip_type = 'ferry'
              AND reg IS NOT NULL AND btrim(reg) <> ''
              AND btrim(reg) !~ '^[0-9]+$'
              AND vessel_resolve(reg) IS NULL
        ),
        per_country AS (
            -- Metres travelled per country across all the trips naming this ship, so the
            -- country it mostly sails in comes first.
            --
            -- Two shapes in the wild: a plain distance ({"NO": 56501}) and a breakdown by
            -- traction ({"NO": {"elec": 0, "nonelec": 24497.5}}). Anything else counts as
            -- zero — the ordering is a hint, and a country that appears at all is the part
            -- that matters.
            SELECT u.reg,
                   c.key AS country,
                   SUM(
                       CASE jsonb_typeof(c.value)
                           WHEN 'number' THEN c.value::numeric
                           WHEN 'object' THEN (
                               SELECT COALESCE(SUM(part.value::numeric), 0)
                               FROM jsonb_each_text(c.value) AS part
                               WHERE part.value ~ '^-?[0-9]+(\.[0-9]+)?$'
                           )
                           ELSE 0
                       END
                   ) AS metres
            FROM unresolved u, LATERAL jsonb_each(u.countries) c
            GROUP BY 1, 2
        )
        SELECT u.reg,
               COUNT(*) AS trips,
               (SELECT array_agg(p.country ORDER BY p.metres DESC)
                  FROM per_country p WHERE p.reg = u.reg) AS countries,
               -- Who ran the trips. The strongest signal of the three in practice: two
               -- ships may share a name and a flag, but not an operator on the route
               -- somebody actually sailed.
               MODE() WITHIN GROUP (
                   ORDER BY NULLIF(btrim(split_part(u.operator, ',', 1)), '')
               ) AS operator
        FROM unresolved u
        GROUP BY u.reg
        """
    ).fetchall()
    by_reg = {row["reg"]: row for row in rows}
    regs = list(by_reg)

    candidates = {}
    for start in range(0, len(regs), 40):
        values = " ".join('"%s"@en' % r.replace('"', "") for r in regs[start : start + 40])
        try:
            found = sparql(
                f"""
                SELECT ?label ?ship ?imo ?mmsi ?iso ?operatorLabel ?image WHERE {{
                    VALUES ?label {{ {values} }}
                    ?ship rdfs:label ?label.
                    ?ship wdt:P31/wdt:P279* wd:Q11446.
                    OPTIONAL {{ ?ship wdt:P458 ?imo }}
                    OPTIONAL {{ ?ship wdt:P587 ?mmsi }}
                    OPTIONAL {{ ?ship wdt:P17 ?country. ?country wdt:P297 ?iso }}
                    OPTIONAL {{ ?ship wdt:P137 ?operator }}
                    OPTIONAL {{ ?ship wdt:P18 ?image }}
                    SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
                }}
                """
            )
        except Exception as exc:  # a name chunk failing must not lose the numeric work
            logger.warning("name lookup chunk failed: %s", exc)
            continue

        for row in found:
            label = _value(row, "label")
            ship = _value(row, "ship")
            # One ship yields several rows when it has been flagged in several countries;
            # they are the same candidate wearing different ensigns.
            entry = candidates.setdefault(label, {}).setdefault(
                ship,
                {"imo": None, "mmsi": None, "countries": [], "operators": [], "image": None},
            )
            entry["imo"] = entry["imo"] or _valid_number(_value(row, "imo"), 7)
            entry["mmsi"] = entry["mmsi"] or _valid_number(_value(row, "mmsi"), 9)
            iso = _value(row, "iso")
            if iso and iso not in entry["countries"]:
                entry["countries"].append(iso)
            operator = _value(row, "operatorLabel")
            # A ship changes hands, so Wikidata lists every operator it has had; all of
            # them are worth showing, since the trip may be from any era.
            if operator and operator not in entry["operators"]:
                entry["operators"].append(operator)
            # A freely-licensed photo on Commons, if the item has one. Only the link is
            # collected here; it is downloaded, with its attribution, when an admin
            # actually picks this candidate.
            entry["image"] = entry["image"] or _value(row, "image")

    # Those operator names resolved to the logos the rest of the site draws, through
    # operator_aliases exactly as a trip resolves its own.
    logos = {}
    operator_names = {row["operator"] for row in rows if row["operator"]}
    if operator_names:
        for found in pg.execute(
            """
            SELECT o.written, op.short_name,
                   (SELECT l.logo_url FROM operator_logos l
                    WHERE l.operator_id = op.operator_id
                    ORDER BY l.effective_date DESC NULLS LAST, l.uid DESC
                    LIMIT 1) AS logo_url
            FROM UNNEST(CAST(:names AS text[])) AS o(written)
            JOIN operator_aliases a ON a.normalized = operator_normalize(o.written)
            JOIN operators op ON op.operator_id = a.operator_id
            """,
            {"names": sorted(operator_names)},
        ).fetchall():
            logos.setdefault(
                found["written"], {"name": found["short_name"], "logo": found["logo_url"]}
            )

    suggestions = []
    for name, ships in candidates.items():
        row = by_reg.get(name)
        operator = row["operator"] if row else None
        resolved = logos.get(operator or "", {})
        options = []
        for uri, ship in ships.items():
            countries = list(ship["countries"])
            # Wikidata records a country for only a minority of ships, but an MMSI is
            # itself a flag-state allocation — so where it is silent, the number says it.
            derived = _flag_from_mmsi(ship["mmsi"])
            if derived and derived not in countries:
                countries.append(derived)
            options.append(
                {
                    "imo": ship["imo"],
                    "mmsi": ship["mmsi"],
                    "countries": countries,
                    "operators": ship["operators"],
                    "image": ship["image"],
                    "wikidata": uri.rsplit("/", 1)[-1],
                }
            )
        # Something to go on first: a candidate with no number at all cannot be assigned.
        options.sort(key=lambda o: (o["imo"] is None, o["mmsi"] is None, o["imo"] or ""))
        suggestions.append(
            {
                "name": name,
                "trips": row["trips"] if row else 0,
                "trip_countries": list(row["countries"] or []) if row else [],
                "trip_operator": resolved.get("name") or operator,
                "trip_operator_logo": resolved.get("logo"),
                "options": options,
                # Kept for the CLI and for callers that only want the easy case.
                "ambiguous": len(options) > 1,
                "imo": options[0]["imo"] if len(options) == 1 else None,
                "mmsi": options[0]["mmsi"] if len(options) == 1 else None,
            }
        )

    # Busiest first: a name on forty trips is worth confirming, one on a single trip
    # probably is not.
    suggestions.sort(key=lambda s: (-s["trips"], s["name"]))
    return suggestions


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run)"
    )
    parser.add_argument(
        "--report-names",
        action="store_true",
        help="also list name-only candidates for manual entry (never written)",
    )
    args = parser.parse_args()
    result = backfill(apply=args.apply, report_names=args.report_names)

    verb = "Filled" if args.apply else "Would fill"
    print(f"\n{verb} {len(result['filled'])} existing vessel(s):")
    for item in result["filled"][:40]:
        changes = ", ".join(f"{k}={v}" for k, v in item["changes"].items())
        print(f"  #{item['uid']} {item['label']}: {changes}")
    if len(result["filled"]) > 40:
        print(f"  … and {len(result['filled']) - 40} more")

    verb = "Created" if args.apply else "Would create"
    print(f"\n{verb} {len(result['created'])} vessel(s) from unmatched trip regs:")
    for item in result["created"][:40]:
        print(f"  {item['reg']} -> {item['name']} (imo={item['imo']}, mmsi={item['mmsi']})")

    if result["skipped"]:
        print(f"\nSkipped {len(result['skipped'])}:")
        for reason in result["skipped"][:20]:
            print(f"  {reason}")

    if result["suggestions"]:
        print(f"\n{len(result['suggestions'])} name-only candidate(s) for manual entry:")
        for item in result["suggestions"]:
            where = "/".join(item["trip_countries"]) or "?"
            print(f"  {item['name']} ({item['trips']} trips, sailed in {where}"
                  f", operator {item['trip_operator'] or '?'}):")
            for option in item["options"]:
                flag = "/".join(option["countries"]) or "?"
                run_by = ", ".join(option["operators"]) or "?"
                print(f"      imo={option['imo']} mmsi={option['mmsi']}"
                      f" flag={flag} operator={run_by} wikidata={option['wikidata']}")

    if not args.apply:
        print("\nRe-run with --apply to write.")
