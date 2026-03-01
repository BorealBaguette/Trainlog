import json
import sys

import geopandas as gpd
import osm2geojson
import pycountry
import requests


def get_subdivisions(country_code):
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
            # Keep only 1st level subs
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
            osm_json, filter_used_refs=True, log_level="ERROR"
        )
        return geojson
    else:
        print("Error fetching data:", response.status_code)
        return None


def clip_to_state(train_lines_gdf, state_boundary_geojson):
    state_gdf = gpd.GeoDataFrame.from_features(
        state_boundary_geojson["features"], crs=train_lines_gdf.crs
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
        subdivision_boundary = get_subdivision_boundary(subdivision)
        clipped_lines = clip_to_state(train_lines_gdf, subdivision_boundary)
        clipped_lines.to_file(sub_path, driver="GeoJSON")
        print(f"Saved initial file {subdivision}.geojson")
        with open(sub_path, "r") as file:
            # update total area
            data = json.load(file)
            total_area = 0
            for element in data["features"]:
                total_area += element["properties"]["area_m2"]
            # Add the total area to the JSON data
            data["total_area_m2"] = total_area
            with open(sub_path, "w") as file:
                json.dump(data, file)
                print(f"Saved final file {subdivision}.geojson")


process(sys.argv[1])
