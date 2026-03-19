import json
import os


def get_coverage_file_path(cc):
    directory_path = "country_percent/countries/processed/"
    return os.path.join(directory_path, f"{cc}.geojson")


def has_coverage_file(cc):
    return os.path.exists(get_coverage_file_path(cc))


def get_coverage_geojson_dict(cc):
    with open(get_coverage_file_path(cc), "r") as file:
        return json.load(file)
