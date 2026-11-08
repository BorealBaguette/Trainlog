"""
Build base_data/urban_areas.csv, the cities the rail network graph groups stations into,
from the GHSL Urban Centre Database (GHS-UCDB R2024A):

    https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_UCDB_GLOBE_R2024A/GHS_UCDB_GLOBE_R2024A/V1-2/

Source: European Commission, Joint Research Centre — GHS Urban Centre Database R2024A,
CC BY 4.0. Changed here: reprojected to WGS 84, widened by BUFFER_M and simplified.

An urban centre is a contiguous area of at least 1,500 inhabitants per km² totalling at least
50,000 people (about 11,400 worldwide), so Paris is the whole agglomeration, not its 20
arrondissements. Smaller towns aren't in it; the graph falls back to the station there.

Usage:
    python -m scripts.build_urban_areas path/to/GHS_UCDB_GLOBE_R2024A.gpkg
setup_db() loads the CSV into `urban_areas` (migration 0090) the next time the app starts.
"""
import csv
import sys

import geopandas as gpd
import pycountry
import shapely

LAYER = "GHSL_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A"
OUT = "base_data/urban_areas.csv"

# Urban centres are drawn on a 1 km grid, which leaves a station at the edge of town just
# outside; widening them by a cell catches it, and smooths the grid's staircase edges.
BUFFER_M = 1000
SIMPLIFY_M = 250

# GHSL country names pycountry doesn't resolve.
COUNTRY_FIXES = {
    "Russia": "RU", "Turkey": "TR", "Türkiye": "TR", "Kosovo": "XK", "Côte d'Ivoire": "CI",
    "Democratic Republic of the Congo": "CD", "Republic of the Congo": "CG", "Laos": "LA",
    "Vietnam": "VN", "Iran": "IR", "Syria": "SY", "Bolivia": "BO", "Venezuela": "VE",
    "Tanzania": "TZ", "Moldova": "MD", "North Korea": "KP", "South Korea": "KR",
    "Taiwan": "TW", "Palestine": "PS", "Brunei": "BN", "Cape Verde": "CV", "Swaziland": "SZ",
    "Eswatini": "SZ", "Micronesia": "FM", "Macedonia": "MK", "North Macedonia": "MK",
    "Czech Republic": "CZ", "Czechia": "CZ", "The Gambia": "GM", "Gambia": "GM",
    "East Timor": "TL", "Timor-Leste": "TL", "México": "MX", "Northern Cyprus": "CY",
}


def fix_text(s):
    """GHSL's names are UTF-8 read back as Latin-1 ("MÃ¼nchen"); undo that where it applies."""
    if not isinstance(s, str):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def iso2(name):
    if not name:
        return None
    if name in COUNTRY_FIXES:
        return COUNTRY_FIXES[name]
    try:
        return pycountry.countries.lookup(name).alpha_2
    except LookupError:
        try:
            return pycountry.countries.search_fuzzy(name)[0].alpha_2
        except LookupError:
            return None


def main(gpkg):
    gdf = gpd.read_file(gpkg, layer=LAYER,
                        columns=["ID_UC_G0", "GC_UCN_MAI_2025", "GC_CNT_GAD_2025", "GC_POP_TOT_2025"])
    gdf["geometry"] = gdf.geometry.buffer(BUFFER_M).simplify(SIMPLIFY_M)
    gdf = gdf.to_crs(4326)
    for col in ("GC_UCN_MAI_2025", "GC_CNT_GAD_2025"):
        gdf[col] = gdf[col].map(fix_text)

    unresolved = set()
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["id", "name", "country", "population", "geom"])
        for row in gdf.sort_values("ID_UC_G0").itertuples():
            country = iso2(row.GC_CNT_GAD_2025)
            if country is None:
                unresolved.add(row.GC_CNT_GAD_2025)
            geom = shapely.multipolygons(shapely.get_parts(row.geometry))
            out.writerow([int(row.ID_UC_G0), row.GC_UCN_MAI_2025, country or "",
                          round(row.GC_POP_TOT_2025), "SRID=4326;" + shapely.to_wkt(geom, rounding_precision=4)])

    print(f"{len(gdf)} urban areas written to {OUT}")
    if unresolved:
        print("Countries without an ISO code (country left empty):", sorted(unresolved))


if __name__ == "__main__":
    main(sys.argv[1])
