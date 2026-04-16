"""
Simplify a processed GeoJSON file by:
1. removing redundant polygon points,
2. recomputing polygon areas,
3. re-assign new IDs,
4. converting the output to CRS84,
5. truncating coordinate precision to the cm range.

Usage:
    python simplify_geojson.py <COUNTRY_CODE>

The script reads from:
    countries/processed/<COUNTRY_CODE>.geojson
    (falls back to lowercase filename if needed)

It assumes CRS84 if the input file has no CRS, performs distance
calculations in EPSG:3857, and always writes output in CRS84.
"""

import json
import os
import sys

import geopandas as gpd
from shapely.geometry import LineString, Point, mapping, shape

DEFAULT_INPUT_CRS = "urn:ogc:def:crs:OGC:1.3:CRS84"
WEB_MERCATOR_CRS = "EPSG:3857"
OUTPUT_CRS = "urn:ogc:def:crs:OGC:1.3:CRS84"


def round_float(value, decimals=6):
    factor = 10**decimals
    return round(value * factor) / factor


def truncate_coords(coords):
    if isinstance(coords, list) or isinstance(coords, tuple):
        if coords and all(isinstance(item, (int, float)) for item in coords):
            assert all(e == 0 for e in coords[3:])
            coords = coords[:2]
            updated = []
            for item in coords:
                if isinstance(item, int):
                    updated.append(item)
                else:
                    updated.append(round_float(item))
            return updated
        return [truncate_coords(item) for item in coords]
    return coords


def simplify_ring(ring):
    MIN_POINT_DISTANCE_M = 0.5

    MAX_ENDPOINT_DISTANCE_M = 15.0
    MAX_MIDPOINT_DISTANCE_FACTOR = 0.2

    if len(ring) < 4:
        return ring

    closed = ring[0] == ring[-1]
    work = list(ring[:-1]) if closed else list(ring[:])
    if len(work) < 3:
        return ring

    i = 0
    while i < len(work) - 2:
        line_i_i1 = LineString([work[i], work[i + 1]])
        line_i_i2 = LineString([work[i], work[i + 2]])
        point_i1 = Point(work[i + 1])

        if (
            line_i_i2.length <= MAX_ENDPOINT_DISTANCE_M
            and point_i1.distance(line_i_i2)
            <= line_i_i2.length * MAX_MIDPOINT_DISTANCE_FACTOR
        ) or (line_i_i1.length <= MIN_POINT_DISTANCE_M):
            del work[i + 1]
            if len(work) < 3:
                break
            continue
        i += 1

    if closed:
        work.append(work[0])
    return work


def simplify_polygon_coords(coords):
    simplified = []
    for ring in coords:
        simplified.append(simplify_ring(ring))
    return simplified


def simplify_multipolygon_coords(coords):
    simplified = []
    for polygon in coords:
        simplified.append(simplify_polygon_coords(polygon))
    return simplified


def simplify_geometry(geometry):
    if not geometry:
        return
    geom_type = geometry.get("type")
    if geom_type == "GeometryCollection":
        for sub in geometry.get("geometries", []):
            simplify_geometry(sub)
        return
    if geom_type == "Polygon":
        geometry["coordinates"] = simplify_polygon_coords(
            geometry.get("coordinates", [])
        )
    elif geom_type == "MultiPolygon":
        geometry["coordinates"] = simplify_multipolygon_coords(
            geometry.get("coordinates", [])
        )


def get_input_crs(data):
    crs = data.get("crs")
    if not crs:
        return DEFAULT_INPUT_CRS
    if isinstance(crs, dict):
        props = crs.get("properties", {})
        name = props.get("name")
        if name:
            return name
    return DEFAULT_INPUT_CRS


def set_output_crs(data):
    data["crs"] = {"type": "name", "properties": {"name": OUTPUT_CRS}}


def process(country_code):
    raw_path = f"countries/processed/{country_code}.geojson"
    path = raw_path
    if not os.path.exists(path):
        path = f"countries/processed/{country_code.lower()}.geojson"
    if not os.path.exists(path):
        print(f"Geojson file not found for {country_code}")
        return

    with open(path, "r") as file:
        data = json.load(file)

    input_crs = get_input_crs(data)

    assert data.get("type") == "FeatureCollection"

    features = data.get("features", [])
    # Convert the features to a GeoDataFrame
    geometries = [shape(feature["geometry"]) for feature in data.get("features", [])]
    gdf = gpd.GeoDataFrame(data["features"], geometry=geometries, crs=input_crs)

    # Transform to Web Mercator for accurate distance calculations
    gdf_mercator = gdf.to_crs(WEB_MERCATOR_CRS)

    # Update the geometries to Web Mercator for simplification
    total_features = len(features)
    for idx, feature in enumerate(features):
        geometry = mapping(gdf_mercator.iloc[idx].geometry)
        simplify_geometry(geometry)
        gdf_mercator.at[idx, "geometry"] = shape(geometry)
        if idx % 10 == 0:
            progress = 100 * idx / total_features
            print(f"Simplify progress: {progress:.2f}%", end="\r")
    print("Simplify progress: 100.00%")

    print("Calculating areas...")

    # Compute the area for each geometry
    gdf_mercator["area_m2"] = gdf_mercator["geometry"].area

    # Transform to output crs
    gdf = gdf_mercator.drop(columns=["area_m2"]).to_crs(OUTPUT_CRS)

    # Update the data with the valid features
    data["features"] = gdf.to_dict("records")
    for idx, feature in enumerate(data["features"]):
        feature["geometry"] = mapping(feature["geometry"])
        if "properties" not in feature or feature["properties"] is None:
            feature["properties"] = {}
        feature["properties"]["id"] = idx  # assign new IDs
        feature["properties"]["area_m2"] = round_float(
            gdf_mercator.iloc[idx]["area_m2"], decimals=2
        )  # assign polygon area

    # Compute the total area
    total_area_m2 = sum(
        feature["properties"]["area_m2"] for feature in data["features"]
    )
    data["total_area_m2"] = total_area_m2

    set_output_crs(data)

    # Truncate coordinates to reduce file size
    print("Truncating coordinates...")
    for feature in data.get("features", []):
        geometry = feature.get("geometry")
        if geometry and "coordinates" in geometry:
            pass
            geometry["coordinates"] = truncate_coords(geometry["coordinates"])

    print("Writing output file...")
    with open(path, "w") as file:
        json.dump(data, file)
        print(f"Simplified {path}")


process(sys.argv[1])
