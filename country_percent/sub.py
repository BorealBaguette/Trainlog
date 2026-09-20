import json
import sys

import geopandas as gpd
import osm2geojson
import pycountry
import requests

UK_NUTS1 = {
    "UKC": 151164,
    "UKD": 151261,
    "UKE": 151012,
    "UKF": 151279,
    "UKG": 151283,
    "UKH": 151336,
    "UKI": 175342,
    "UKJ": 151304,
    "UKK": 151339,
    "UKL": 58437,
    "UKM": 58446,
    "UKN": 156393,
}


def get_subdivisions(country_code):
    # The UK is a special case: NUTS1 regions are not
    # ISO 3166-2 subdivisions.
    if country_code.upper() == "GB":
        return list(UK_NUTS1.keys())

    # Find the country by its ISO 3166-1 alpha-2, alpha-3, or numeric code
    country = (
        pycountry.countries.get(alpha_2=country_code)
        or pycountry.countries.get(alpha_3=country_code)
        or pycountry.countries.get(numeric=country_code)
    )

    subdivisions_list = []

    if country:
        # Get all subdivisions for the country
        subdivisions = list(pycountry.subdivisions.get(country_code=country.alpha_2))

        for subdivision in subdivisions:
            # Keep only 1st level subdivisions
            if subdivision.parent_code is None:
                subdivisions_list.append(subdivision.code)
    else:
        print("Country code not found.")

    return subdivisions_list


def get_subdivision_boundary(iso_code):
    query = f"""
    [out:json];
    relation["ISO3166-2"="{iso_code}"];
    (._; >;);
    out body;
    """

    url = "http://overpass-api.de/api/interpreter"
    response = requests.get(url, params={"data": query})

    if response.status_code == 200:
        osm_json = response.json()

        # Convert OSM JSON to GeoJSON using osm2geojson
        geojson = osm2geojson.json2geojson(
            osm_json,
            filter_used_refs=True,
            log_level="ERROR",
        )

        return geojson

    print("Error fetching data:", response.status_code)
    return None


def get_uk_nuts1_boundary(nuts_code):
    relation_id = UK_NUTS1[nuts_code]

    url = f"https://api.openstreetmap.org/api/0.6/relation/{relation_id}/full"

    response = requests.get(url, timeout=60)

    if response.status_code == 200:
        # OSM API returns XML
        osm_xml = response.text

        # Convert OSM XML to GeoJSON
        geojson = osm2geojson.xml2geojson(
            osm_xml,
            filter_used_refs=True,
            log_level="ERROR",
        )

        return geojson

    print(
        f"Error fetching UK NUTS1 boundary {nuts_code} "
        f"(relation {relation_id}): {response.status_code}"
    )
    return None


def clip_to_state(train_lines_gdf, state_boundary_geojson):
    state_gdf = gpd.GeoDataFrame.from_features(
        state_boundary_geojson["features"],
        crs=train_lines_gdf.crs,
    )

    clipped_lines = gpd.clip(train_lines_gdf, state_gdf)
    return clipped_lines


def process(country_code):
    train_lines_gdf = gpd.read_file(
        f"countries/processed/{country_code.lower()}.geojson"
    )

    for subdivision in get_subdivisions(country_code):
        sub_path = f"countries/processed/{subdivision}.geojson"

        print(f"Process subdivision {subdivision}")

        if country_code.upper() == "GB":
            subdivision_boundary = get_uk_nuts1_boundary(subdivision)
        else:
            subdivision_boundary = get_subdivision_boundary(subdivision)

        if subdivision_boundary is None:
            print(f"Skipping {subdivision}: failed to fetch boundary")
            continue

        clipped_lines = clip_to_state(
            train_lines_gdf,
            subdivision_boundary,
        )

        print("Clipped features:", len(clipped_lines))
        print("Missing area_m2:", clipped_lines["area_m2"].isna().sum())

        if clipped_lines["area_m2"].isna().any():
            print(clipped_lines[clipped_lines["area_m2"].isna()])

        # Write GeoJSON directly, avoiding Fiona
        data = json.loads(clipped_lines.to_json())

        print(
            "Written features:",
            len(data["features"]),
            "missing area_m2:",
            sum("area_m2" not in f["properties"] for f in data["features"]),
        )

        total_area = 0
        for element in data["features"]:
            total_area += element["properties"]["area_m2"]

        data["total_area_m2"] = total_area

        with open(sub_path, "w") as file:
            json.dump(data, file)
            print(f"Saved final file {subdivision}.geojson")


process(sys.argv[1])
