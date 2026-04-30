import json
import os
import sys
import time

import geopandas as gpd
import pyproj
import requests
from shapely.geometry import LineString, mapping, shape
from shapely.ops import transform, unary_union

RAIL_WIDTH_BUFFER_M = 50


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


def fetch_railway_geometry(country_code):
    print(f"Fetching railway geometry for country: {country_code}...")

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
        print("Fetching data from Overpass...")
        overpass_query = f"""
        [out:json];
        area["ISO3166-1"="{country_code}"]->.searchArea;
        (
            way["railway"="rail"](area.searchArea);
            way["railway"="narrow_gauge"](area.searchArea);
        );
        out body;
        >;
        out skel qt;
        """

        overpass_url = "http://overpass-api.de/api/interpreter"
        response = requests.get(overpass_url, params={"data": overpass_query})
        data = response.json()

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

    print(f"Saving processed data for {country_code}...")
    with open(processed_path, "w") as f:
        json.dump(stripped_data, f)
    print(f"Railway geometry processing for {country_code} completed!")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Please provide a country's ISO code as a command-line argument.")
        sys.exit(1)
    country_code = sys.argv[1]
    fetch_railway_geometry(country_code)
