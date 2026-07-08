"""
Generate a coverage map for either a country or region by:
1. Fetch railway geometry as json from OSM
2. Convert to polygons
3. Filter polygons to railways used by passenger trains
4. Merge polygons with significant overlap
5. Compute areas
6. Save as .geojson
7. Clip polygons on borders to the borders of the country or region
8. Simplify the geojson using simplify_geojson

Usage:
    cd country_percent
    python ./scripts/overpass2.py <iso_code>
<iso_code> is the ISO3166 code for the region you want to generate. Can be either a country (ISO3166-1) or a subdivision (ISO3166-2)
"""

import json
import os
import sys
import time

import geopandas as gpd
import osm2geojson
import requests
from shapely.geometry import LineString, mapping, shape
from shapely.ops import unary_union
from simplify_geojson import process as simplify_geojson

RAIL_WIDTH_BUFFER_M = 50
OVERPASS_URL = "https://overpass.private.coffee/api/interpreter"
ISO3166_URL = "https://iso3166-2-api.vercel.app/api/all"
SUBDIVISION_QUERY = """
[out:json];
relation["ISO3166-{iso_spec}"="{iso_code}"];
(._; >;);
out body;
"""
GEOMETRY_QUERY = """
[out:json];
area["ISO3166-{iso_spec}"="{iso_code}"]->.searchArea;
(
    way["railway"="rail"](area.searchArea);
    way["railway"="narrow_gauge"](area.searchArea);
);
out body;
>;
out skel qt;
"""

MAX_OVERPASS_RETRIES = 20
RETRY_DELAY_SECONDS = 3


def get_overpass_data(iso_spec, iso_code, query_template):
    query = query_template.format(iso_code=iso_code, iso_spec=iso_spec)

    attempt = 0
    for attempt in range(MAX_OVERPASS_RETRIES):
        r = requests.get(OVERPASS_URL, params={"data": query})
        match r.status_code:
            case 200:
                return r.json()
            case 504:
                print(
                    f"Error fetching data: {r.status_code} - {r.reason} (attempt ${attempt} of ${MAX_OVERPASS_RETRIES})"
                )
                time.sleep(RETRY_DELAY_SECONDS * attempt)
                continue
            case _:
                print(f"Error fetching data: {r.status_code} - {r.reason}")
                sys.exit(1)
    print("Error fetching data: run out of attempts")
    exit(1)


def merge_overlapping_polygons(features):
    print("Starting to merge overlapping polygons...")
    polygons = [shape(feature["geometry"]) for feature in features]

    # This list keeps track of whether a polygon should be kept
    to_keep = [True for _ in polygons]

    total_polygons = len(polygons)
    processed_polygons = 0
    start_time = time.time()

    for i, polyA in enumerate(polygons):
        if not to_keep[i]:
            continue  # Skip polygons that are already merged

        overlapping_polygons = [polyA]
        for j, polyB in enumerate(polygons):
            if i != j and polyA.intersects(polyB):
                intersection_area = polyA.intersection(polyB).area

                # If the overlap is significant, add B to the list of polygons to be merged
                if intersection_area > 0.5 * polyA.area:
                    overlapping_polygons.append(polyB)
                    to_keep[j] = False

        # Merge all overlapping polygons using unary_union
        merged_polygon = unary_union(overlapping_polygons)
        polygons[i] = merged_polygon
        features[i]["geometry"] = mapping(
            merged_polygon
        )  # Update the feature's geometry

        processed_polygons += 1
        if (processed_polygons) % 20 == 0:
            progress = 100 * processed_polygons / total_polygons
            elapsed_time = time.time() - start_time
            eta = elapsed_time * total_polygons / processed_polygons - elapsed_time
            print(f"Progress: {progress:.2f}%, ETA: {eta:.2f} seconds", end="\r")

    print("\nPolygon merging completed!")
    return [feature for i, feature in enumerate(features) if to_keep[i]]


def get_subdivision_boundary(iso_code, iso_spec):
    osm_json = get_overpass_data(iso_spec, iso_code, SUBDIVISION_QUERY)
    # Convert OSM JSON to GeoJSON using osm2geojson
    geojson = osm2geojson.json2geojson(
        osm_json, filter_used_refs=True, log_level="ERROR"
    )
    return geojson


def clip_to_state(train_lines_gdf, state_boundary_geojson):
    state_gdf = gpd.GeoDataFrame.from_features(
        state_boundary_geojson["features"], crs=train_lines_gdf.crs
    )
    clipped_lines = gpd.clip(train_lines_gdf, state_gdf)
    return clipped_lines


def clip_to_region(iso_spec, iso_code, processed_path):
    train_lines_gdf = gpd.read_file(processed_path)
    subdivision_boundary = get_subdivision_boundary(iso_code, iso_spec)
    clipped_lines = clip_to_state(train_lines_gdf, subdivision_boundary)
    clipped_lines.to_file(processed_path, driver="GeoJSON")
    print(f"Saved initial file {iso_code}.geojson")


def buffer_linestring(line_coords):
    line = LineString(line_coords)
    gdf = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:4326")

    # Buffer the linestring and transform to Web Mercator for accurate distance calculations
    gdf = gdf.to_crs("EPSG:3857")
    gdf["geometry"] = gdf.buffer(RAIL_WIDTH_BUFFER_M)

    # Transform back to WGS84
    gdf = gdf.to_crs("EPSG:4326")

    return gdf.iloc[0].geometry
    
def process_railway_geometry(iso_code, iso_spec):
    print(f"Fetching railway geometry for {iso_code} using ISO 3166-{iso_spec}")

    preprocessed_path = "countries/preprocessed/" + iso_code + ".json"
    processed_path = "countries/processed/" + iso_code + ".geojson"

    os.makedirs("countries/preprocessed", exist_ok=True)
    os.makedirs("countries/processed", exist_ok=True)

    if not os.path.exists(preprocessed_path):
        data = get_overpass_data(iso_spec, iso_code, GEOMETRY_QUERY)
        with open(preprocessed_path, "w") as f:
            json.dump(data, f)
    else:
        print("Loading preprocessed data...")
        with open(preprocessed_path, "r") as f:
            data = json.load(f)
    nodes_dict = {
        node["id"]: (node["lon"], node["lat"])
        for node in data["elements"]
        if node["type"] == "node"
    }
    stripped_data = {"type": "FeatureCollection", "features": []}

    print("Buffering linestrings and creating polygons...")

    total_elements = len(data["elements"])
    processed_elements = 0
    start_time = time.time()

    for element in data["elements"]:
        if processed_elements not in []:  # [9013, 9410, 9411]:
            if (
                element["type"] == "way"
                and element["tags"]["railway"]
                not in ["construction", "disused", "abandoned", "proposed"]
                and element["tags"].get("service") not in ["yard", "spur", "siding"]
                and element["tags"].get("usage") not in ["industrial"]
            ):
                buffered_geometry = buffer_linestring(
                    [(nodes_dict[node_id]) for node_id in element["nodes"]]
                )
                feature = {
                    "type": "Feature",
                    "geometry": shape(buffered_geometry).__geo_interface__,
                }
                stripped_data["features"].append(feature)
        else:
            print(element)

        processed_elements += 1
        if (processed_elements) % 20 == 0 or processed_elements == total_elements:
            progress = 100 * processed_elements / total_elements
            elapsed_time = time.time() - start_time
            eta = elapsed_time * total_elements / processed_elements - elapsed_time
            print(
                f"ID: {processed_elements}, Progress: {progress:.2f}%, ETA: {eta:.2f} seconds",
                end="\r",
            )

    stripped_data["features"] = merge_overlapping_polygons(stripped_data["features"])

    print(f"Saving processed data for {iso_code}...")
    with open(processed_path, "w") as f:
        json.dump(stripped_data, f)
    print(f"Railway geometry processing for {iso_code} completed!")

    print("\nClip geojson to region boundary...")
    clip_to_region(iso_spec, iso_code, processed_path)

    print("\nSimplify geojson...")
    simplify_geojson(iso_code)

    print("\nDone!")


if __name__ == "__main__":
    try:
        iso_code = sys.argv[1].upper()
    except IndexError:
        raise ValueError("Invalid ISO3166 code: No code provided")
    r = requests.get(ISO3166_URL)
    for country, regions in r.json().items():
        if iso_code == country:
            iso_spec = 1
            break
        if iso_code in regions:
            iso_spec = 2
            break
    else:
        raise ValueError(
            "Invalid ISO3166 code: Please provide a ISO3166-1 or ISO3166-2 code"
        )

    process_railway_geometry(iso_code, iso_spec)
