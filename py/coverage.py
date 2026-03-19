import json
import os
from glob import glob


def get_coverage_file_path(cc):
    directory_path = "country_percent/countries/processed/"
    return os.path.join(directory_path, f"{cc}.geojson")


def has_coverage_file(cc):
    return os.path.exists(get_coverage_file_path(cc))


def get_coverage_geojson_dict(cc):
    with open(get_coverage_file_path(cc), "r") as file:
        return json.load(file)


def get_coverage_region_file_paths(cc):
    directory_path = "country_percent/countries/processed/"
    pattern = os.path.join(directory_path, f"{cc.upper()}-*.geojson")
    return sorted(glob(pattern))


def get_coverage_geojson_dict_from_regions(cc):
    region_file_paths = get_coverage_region_file_paths(cc)
    if not region_file_paths:
        raise FileNotFoundError(f"No region coverage files found for {cc}")

    region_payloads = []
    max_original_id = 0
    total_area_m2 = 0
    reference_crs = None
    first_payload = True

    for file_path in region_file_paths:
        with open(file_path, "r") as file:
            geojson_data = json.load(file)

        if geojson_data.get("type") != "FeatureCollection":
            raise ValueError(f"{file_path} is not a FeatureCollection")

        current_crs = geojson_data.get("crs")
        if first_payload:
            reference_crs = current_crs
            first_payload = False
        elif current_crs != reference_crs:
            raise ValueError(f"Mismatching crs in region files for {cc}")

        region_code = os.path.splitext(os.path.basename(file_path))[0]
        region_payloads.append((region_code, geojson_data))
        total_area_m2 += geojson_data.get("total_area_m2", 0)

        for feature in geojson_data.get("features", []):
            properties = feature.get("properties", {})
            feature_id = properties.get("id")
            if not isinstance(feature_id, int):
                raise ValueError(f"Invalid feature id in {file_path}: {feature_id!r}")
            if feature_id > max_original_id:
                max_original_id = feature_id

    offset = 10 ** len(str(max_original_id))
    merged_features = []

    for source_index, (region_code, geojson_data) in enumerate(region_payloads):
        for feature in geojson_data.get("features", []):
            properties = feature.get("properties", {})
            original_id = properties["id"]
            properties["original_id"] = original_id
            properties["source_cc"] = region_code
            properties["id"] = source_index * offset + original_id
            merged_features.append(feature)

    result = {
        "type": "FeatureCollection",
        "name": cc,
        "crs": reference_crs,
        "features": merged_features,
        "total_area_m2": total_area_m2,
    }

    return result
