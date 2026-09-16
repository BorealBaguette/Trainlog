"""
Simplify a processed GeoJSON file by:
1. unpacking features of type GeometryCollection,
2. deleting features of all types except Polygon and Multipolygon,
3. simplifying polygon outlines with Douglas-Peucker, keeping shared borders,
4. converting the output to CRS84,
5. truncating coordinate precision to the cm range,
6. recomputing polygon areas,
7. re-assign new IDs,
8. failing on very tiny or invalid polygons.

Usage:
    python simplify_geojson.py <COUNTRY_CODE>

The script reads from:
    countries/processed/<COUNTRY_CODE>.geojson

It assumes CRS84 if the input file has no CRS, performs distance
calculations in EPSG:3857, and always writes output in CRS84.
"""

import collections
import itertools
import json
import os
import sys

import geopandas as gpd
from shapely.geometry import LineString, MultiPolygon, Polygon, mapping, shape
from shapely.validation import explain_validity

DEFAULT_INPUT_CRS = "urn:ogc:def:crs:OGC:1.3:CRS84"
WEB_MERCATOR_CRS = "EPSG:3857"
OUTPUT_CRS = "urn:ogc:def:crs:OGC:1.3:CRS84"

PROPERTIES_TO_KEEP = ["station"]
MIN_AREA_M2 = 50  # lower once a real polygon this small shows up
SIMPLIFY_TOLERANCE_M = 1.0


def round_area(value):
    return round(value, 2)


def truncate_coords(coords):
    if coords and all(isinstance(item, (int, float)) for item in coords):
        # a single position
        assert all(e == 0 for e in coords[3:])
        return [round(item, 6) for item in coords[:2]]
    return [truncate_coords(item) for item in coords]


def truncate_geometry(geometry):
    geometry["coordinates"] = truncate_coords(geometry["coordinates"])
    return geometry


def polygons_of(geometry):
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    return [geometry]


def rings_of(geometry):
    for polygon in polygons_of(geometry):
        yield polygon.exterior
        yield from polygon.interiors


def count_points(geometries):
    return sum(len(ring.coords) for g in geometries for ring in rings_of(g))


def simplify_geometries_once(geometries, iteration):
    """Douglas-Peucker on every ring, simplifying borders shared between
    polygons only once so that they stay shared.

    A ring is cut into arcs where the set of polygons owning a vertex
    changes. An arc between two such junctions is simplified once and the
    result reused by every polygon containing it.
    """
    owners = collections.defaultdict(set)
    for idx, geometry in enumerate(geometries):
        for ring in rings_of(geometry):
            for coord in ring.coords[:-1]:
                owners[coord].add(idx)

    cache = {}

    def simplify_arc(arc):
        # the same arc is traversed in opposite directions by its two owners
        key = tuple(arc) if arc[0] <= arc[-1] else tuple(reversed(arc))
        if key not in cache:
            cache[key] = list(LineString(key).simplify(SIMPLIFY_TOLERANCE_M).coords)
        simplified = cache[key]
        return simplified if tuple(arc) == key else simplified[::-1]

    def simplify_ring(ring):
        coords = list(ring.coords[:-1])
        n = len(coords)
        cuts = [
            i
            for i in range(n)
            if owners[coords[i]] != owners[coords[i - 1]]
            or owners[coords[i]] != owners[coords[(i + 1) % n]]
        ]
        if len(cuts) < 2:
            return list(LineString(ring.coords).simplify(SIMPLIFY_TOLERANCE_M).coords)
        # rotate the ring to start at a junction, then simplify arc by arc
        start = cuts[0]
        coords = coords[start:] + coords[:start]
        cuts = sorted({(i - start) % n for i in cuts}) + [n]
        result = []
        for a, b in zip(cuts, cuts[1:]):
            arc = coords[a : b + 1] if b < n else coords[a:] + [coords[0]]
            result.extend(simplify_arc(arc)[:-1])
        result.append(result[0])
        return result

    def simplify_polygon(polygon):
        simplified = Polygon(
            simplify_ring(polygon.exterior),
            [simplify_ring(ring) for ring in polygon.interiors],
        )
        if polygon.is_valid and not simplified.is_valid:
            return polygon
        return simplified

    result = []
    for idx, geometry in enumerate(geometries):
        polygons = [simplify_polygon(polygon) for polygon in polygons_of(geometry)]
        if geometry.geom_type == "MultiPolygon":
            result.append(MultiPolygon(polygons))
        else:
            result.append(polygons[0])
        if idx % 10 == 0:
            progress = 100 * idx / len(geometries)
            print(
                f"Simplify iteration {iteration}, progress: {progress:.2f}%", end="\r"
            )
    print(f"Simplify iteration {iteration}, progress: 100.00%")
    return result


def simplify_geometries(geometries):
    # repeat until nothing changes, so that re-running the script is a no-op
    for iteration in itertools.count(1):
        simplified = simplify_geometries_once(geometries, iteration)
        if count_points(simplified) == count_points(geometries):
            return simplified
        geometries = simplified


def explode_and_filter_geometries(gdf):
    allowed_types = {"Polygon", "MultiPolygon"}

    def flattened_geometries(geometry):
        if geometry is None:
            return []
        if geometry.geom_type != "GeometryCollection":
            return [geometry]

        geometries = []
        for child in geometry.geoms:
            geometries.extend(flattened_geometries(child))
        return geometries

    unpacked_rows = []
    for _, row in gdf.iterrows():
        geometries = flattened_geometries(row.geometry)
        if not geometries:
            continue
        for geometry in geometries:
            if geometry.geom_type not in allowed_types:
                continue
            expanded = row.copy()
            expanded.geometry = geometry
            unpacked_rows.append(expanded)

    unpacked_gdf = gpd.GeoDataFrame(unpacked_rows, columns=gdf.columns, crs=gdf.crs)
    return unpacked_gdf.reset_index(drop=True)


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
        print(f"Geojson file not found for {country_code}")
        return

    with open(path, "r") as file:
        data = json.load(file)

    input_crs = get_input_crs(data)

    assert data.get("type") == "FeatureCollection"

    # Convert the features to a GeoDataFrame
    geometries = [shape(feature["geometry"]) for feature in data.get("features", [])]
    gdf = gpd.GeoDataFrame(data["features"], geometry=geometries, crs=input_crs)

    # Drop unkown columns
    gdf = gdf[["type", "properties", "geometry"]]

    # Handle different types of features
    gdf = explode_and_filter_geometries(gdf)

    # Transform to Web Mercator for accurate distance calculations
    gdf_mercator = gdf.to_crs(WEB_MERCATOR_CRS)

    print("Simplifying...")
    gdf_mercator["geometry"] = simplify_geometries(list(gdf_mercator["geometry"]))

    # Transform to output crs
    gdf = gdf_mercator.to_crs(OUTPUT_CRS)

    # Truncate coordinates to reduce file size. Areas are computed from
    # the truncated geometry so that re-running the script is a no-op.
    print("Truncating coordinates...")
    gdf["geometry"] = gdf["geometry"].apply(
        lambda geometry: shape(truncate_geometry(mapping(geometry)))
    )

    print("Calculating areas...")

    # Compute the area for each geometry
    gdf["area_m2"] = gdf.to_crs(WEB_MERCATOR_CRS).area

    # Very tiny polygons are most likely editing mistakes, so report
    # them but keep them for manual inspection.
    tiny_ids = list(gdf.index[gdf["area_m2"] < MIN_AREA_M2])

    # Invalid polygons (self-intersections, degenerate rings) come from
    # manual edits and need fixing in QGIS, so report them too.
    invalid = {
        idx: explain_validity(geometry)
        for idx, geometry in gdf["geometry"].items()
        if not geometry.is_valid
    }

    # Update the data with the valid features
    data["features"] = gdf.to_dict("records")
    for idx, feature in enumerate(data["features"]):
        feature["geometry"] = mapping(feature["geometry"])
        old_properties = feature.get("properties", {})
        feature["properties"] = {}
        # assign new IDs
        feature["properties"]["id"] = idx
        # assign polygon area
        feature["properties"]["area_m2"] = round_area(feature.pop("area_m2"))
        for prop_key in old_properties:
            if prop_key in PROPERTIES_TO_KEEP:
                # keep some whitelist of other properties
                feature["properties"][prop_key] = old_properties[prop_key]

    # Compute the total area
    total_area_m2 = sum(
        feature["properties"]["area_m2"] for feature in data["features"]
    )
    data["total_area_m2"] = round_area(total_area_m2)

    set_output_crs(data)

    print("Writing output file...")
    with open(path, "w") as file:
        json.dump(data, file)
        print(f"Simplified {path}")

    errors = []
    if tiny_ids:
        errors.append(f"Polygons smaller than {MIN_AREA_M2} m^2, ids: {tiny_ids}")
    for idx, reason in invalid.items():
        errors.append(f"Invalid polygon, id {idx}: {reason}")
    if errors:
        sys.exit("\n".join(errors))


if __name__ == "__main__":
    process(sys.argv[1])
