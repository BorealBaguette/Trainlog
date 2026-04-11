import json
import os
import sys
import time

import geopandas as gpd
import pyproj
import requests
from shapely.geometry import LineString, mapping, shape
from shapely.ops import transform, unary_union
import osm2geojson

RAIL_WIDTH_BUFFER_M = 50
URL = "https://overpass.private.coffee/api/interpreter"
SUBDIVISION_QUERY = """
[out:json];
relation["ISO3166-2"="{iso_code}"];
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

def get_overpass_data(iso_code, query_template, iso_spec=None):
    query = query_template.format(iso_code=iso_code.upper(),iso_spec=iso_spec)
    r = requests.get(URL, params={"data": query})
    match r.status_code:
        case 200:
            return r.json()
        # case 504:
        #   to do: retry downloading data
        case _:
            print(f"Error fetching data: {r.status_code} - {r.reason}")
            sys.exit(1)

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


def compute_area_in_m2(polygon):
    """Compute the area of a polygon in square meters."""
    project = pyproj.Transformer.from_proj(
        pyproj.Proj("EPSG:4326"),  # WGS84
        pyproj.Proj("EPSG:3857"),  # Web Mercator (meters)
    ).transform
    return transform(project, polygon).area

def get_subdivision_boundary(iso_code):
    osm_json = get_overpass_data(iso_code,SUBDIVISION_QUERY)
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

def fetch_railway_geometry(iso_code,iso_spec):
    print(f"Fetching railway geometry for {iso_code} using ISO 3166-{iso_spec}")

    def buffer_linestring(line_coords):
        line = LineString(line_coords)
        gdf = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:4326")

        # Buffer the linestring and transform to Web Mercator for accurate distance calculations
        gdf = gdf.to_crs("EPSG:3857")
        gdf["geometry"] = gdf.buffer(RAIL_WIDTH_BUFFER_M)

        # Transform back to WGS84
        gdf = gdf.to_crs("EPSG:4326")

        return gdf.iloc[0].geometry

    preprocessed_path = "countries/preprocessed/" + country_code + ".json"
    processed_path = "countries/processed/" + country_code + ".geojson"

    os.makedirs("countries/preprocessed", exist_ok=True)
    os.makedirs("countries/processed", exist_ok=True)

    if not os.path.exists(preprocessed_path):
            data = get_overpass_data(iso_code,GEOMETRY_QUERY,iso_spec)
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
                    "id": element["id"],
                    "properties": {},
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

    print("Calculating areas...")
    total_area_m2 = 0
    # Convert the features to a GeoDataFrame
    geometries = [shape(feature["geometry"]) for feature in stripped_data["features"]]
    gdf = gpd.GeoDataFrame(
        stripped_data["features"], geometry=geometries, crs="EPSG:4326"
    )

    # Transform to Web Mercator for accurate area calculations
    gdf_mercator = gdf.to_crs("EPSG:3857")

    # Compute the area for each geometry
    gdf_mercator["area_m2"] = gdf_mercator["geometry"].area

    # Filter out invalid areas
    gdf_mercator = gdf_mercator[gdf_mercator["area_m2"].notna()]

    # Transform back to WGS84
    gdf = gdf_mercator.to_crs("EPSG:4326")

    # Update the stripped_data with the valid features
    stripped_data["features"] = gdf.drop(columns=["geometry"]).to_dict("records")
    for idx, feature in enumerate(stripped_data["features"]):
        feature["geometry"] = mapping(gdf.iloc[idx].geometry)
        feature["properties"]["id"] = idx
        feature["properties"]["area_m2"] = gdf_mercator.iloc[idx]["area_m2"]

    # Compute the total area
    total_area_m2 = gdf_mercator["area_m2"].sum()
    stripped_data["total_area_m2"] = total_area_m2

    print(f"Saving processed data for {iso_code}...")
    with open(processed_path, "w") as f:
        json.dump(stripped_data, f)
    print(f"Railway geometry processing for {iso_code} completed!")

    if iso_spec == 2:
        train_lines_gdf = gpd.read_file(processed_path)
        subdivision_boundary = get_subdivision_boundary(iso_code)
        clipped_lines = clip_to_state(train_lines_gdf, subdivision_boundary)
        clipped_lines.to_file(processed_path, driver="GeoJSON")
        print(f"Saved initial file {iso_code}.geojson")
        with open(processed_path, "r") as file:
            # update total area
            data = json.load(file)
            total_area = 0
            for element in data["features"]:
                total_area += element["properties"]["area_m2"]
            # Add the total area to the JSON data
            data["total_area_m2"] = total_area
            with open(processed_path, "w") as file:
                json.dump(data, file)
                print(f"Saved final file {iso_code}.geojson")


if __name__ == "__main__":
    match sys.argv[1]:
        case "1":
            if len(sys.argv[2]) != 2:
                print("Please provide a valid country's ISO code as an command-line argument.")
                sys.exit(1)

            iso_spec = 1
            iso_code = sys.argv[2].lower()
        case "2":
            if len(sys.argv[2]) != 5:
                print ("Please provide a valid subdivision's ISO code as an command-line argument.")
                sys.exit(1)

            iso_spec = 2
            iso_code = sys.argv[2].upper()
        case _:
            print("Please provide a valid ISO 3166 part (1 for countries, 2 for subdivisions)")
            sys.exit(1)

    fetch_railway_geometry(iso_code,iso_spec)