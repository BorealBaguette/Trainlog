import json
import os
from glob import glob


def has_coverage_file(cc, immediate_only=False):
    if has_coverage_file_immediate(cc):
        return True

    if immediate_only:
        return False

    return has_coverage_file_from_regions(cc)


def get_coverage_geojson_dict(cc, immediate_only=False):
    if has_coverage_file_immediate(cc):
        return get_coverage_geojson_dict_immediate(cc)

    if immediate_only:
        raise FileNotFoundError(f"No immediate coverage file found for {cc}")

    return get_coverage_geojson_dict_from_regions(cc)


def get_coverage_file_path(cc):
    directory_path = "country_percent/countries/processed/"
    return os.path.join(directory_path, f"{cc}.geojson")


def has_coverage_file_immediate(cc):
    return os.path.exists(get_coverage_file_path(cc))


def get_coverage_geojson_dict_immediate(cc):
    with open(get_coverage_file_path(cc), "r") as file:
        data = json.load(file)
    if "total_area_m2" not in data:
        data["total_area_m2"] = sum(
            f.get("properties", {}).get("area_m2", 0) for f in data.get("features", [])
        )
    return data


def get_coverage_region_file_paths(cc):
    directory_path = "country_percent/countries/processed/"
    pattern = os.path.join(directory_path, f"{cc.upper()}-*.geojson")
    return sorted(glob(pattern))


def has_coverage_file_from_regions(cc):
    return len(get_coverage_region_file_paths(cc)) > 0


def get_coverage_geojson_dict_from_regions(cc):
    region_file_paths = get_coverage_region_file_paths(cc)
    if not region_file_paths:
        raise FileNotFoundError(f"No region coverage files found for {cc}")

    total_area_m2 = 0
    reference_crs = None
    first_payload = True

    offset = 10**6
    merged_features = []

    for file_index, file_path in enumerate(region_file_paths):
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
        total_area_m2 += geojson_data.get("total_area_m2") or sum(
            f.get("properties", {}).get("area_m2", 0) for f in geojson_data.get("features", [])
        )

        for feature in geojson_data.get("features", []):
            properties = feature.get("properties", {})
            original_id = properties["id"]
            if not isinstance(original_id, int):
                raise ValueError(f"Invalid feature id in {file_path}: {original_id!r}")
            if original_id >= offset:
                raise ValueError(
                    f"Too large id in {file_path}: {original_id!r}. offset in get_coverage_geojson_dict_from_regions needs to be increased"
                )

            properties["original_id"] = original_id
            properties["source_cc"] = region_code
            properties["id"] = file_index * offset + original_id
            merged_features.append(feature)

    result = {
        "type": "FeatureCollection",
        "name": cc,
        "crs": reference_crs,
        "features": merged_features,
        "total_area_m2": total_area_m2,
    }

    return result
