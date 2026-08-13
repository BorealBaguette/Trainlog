import csv
import json
import os
import sys

import duckdb
import osm2geojson
import requests

# How to run for testing: pick an uppercase country code, like CR, HU, NO, SE
# $ python overpass-duck.py HU


# install and load load duckdb extensions
duckdb.sql("INSTALL spatial;INSTALL json;")
duckdb.sql("LOAD spatial; LOAD json;")

DOWNLOAD_FILE_DIR = "countries/overpass_json/"
GEOJSON_FILE_DIR = "countries/geojson/"
PROCESSED_FILE_DIR = "countries/duck_processed/"

RAILS_BUFFER_WIDTH_M = 50


def make_file_dirs():
    os.makedirs(DOWNLOAD_FILE_DIR, exist_ok=True)
    os.makedirs(GEOJSON_FILE_DIR, exist_ok=True)
    os.makedirs(PROCESSED_FILE_DIR, exist_ok=True)


def convert_json_to_geojson(json_filename):
    geojson_filename = json_filename.replace(
        DOWNLOAD_FILE_DIR, GEOJSON_FILE_DIR
    ).replace(".json", ".geojson")
    # Read the json file and convert it to geojson with the osm2geo library
    with open(json_filename, "r") as data:
        json_content = data.read()
        # restructure to geojson
        geojson = osm2geojson.json2geojson(json_content)
        json_str = json.dumps(geojson)
        with open(geojson_filename, "w") as f:
            # f.write(ftfy.ftfy(json_str))
            f.write(json_str)
            return geojson_filename


def download_railways_from_overpass(country_code, force_refetch=False):
    print(f"Fetching or finding local data from Overpass for country {country_code}...")
    download_path = DOWNLOAD_FILE_DIR + country_code + ".json"

    if not os.path.exists(download_path) or force_refetch:
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

        with open(download_path, "w") as f:
            json.dump(data, f)
    return download_path


def process_railways(geojson_path, country_code):
    # put data to duckdb
    duckdb.sql(
        f"""
            CREATE TABLE railways_input AS
            SELECT * from
            ST_READ('{geojson_path}');
        """
    )
    # sanity check
    print("We have this many line segments as railways to process BEFORE filtering:")
    duckdb.sql("SELECT count(*) FROM railways_input;").show()
    # some debug print...
    # print("Peek into the data:")
    # duckdb.sql("SHOW railways_input;").show()
    # duckdb.sql("SELECT * FROM railways_input;").show()
    duckdb.sql("SELECT tags FROM railways_input;").show()
    filter_railways()
    # some debug print...
    # print("FILTERED")
    # duckdb.sql("SELECT tags FROM filtered_railways;").show()
    # print("filtered railway values")
    # duckdb.sql("SELECT json_extract(tags, '$.railway') FROM filtered_railways;").show()
    # print("ALL railway values")
    # duckdb.sql("SELECT json_extract(tags, '$.railway') FROM railways_input;").show()
    transform_lines_to_buffered_polygons()
    save_pre_merged_polygons_to_testfile(country_code)  # debug / check in a map
    merge_overlapping_polygons(country_code)
    total_area = calculate_areas()
    save_merged_polygons_to_file(country_code)  # to check again in a map/visualization
    save_final_geojson_file(country_code, total_area)


def filter_railways():
    # Moving data to next table: filtered_railways
    # We need to throwing out some rows...
    # Can we improve filtering with more filters on tags?
    # OG filters from original python script:
    #            element["tags"]["railway"]
    #            not in ["construction", "disused", "abandoned", "proposed"]
    #            and element["tags"].get("service") not in ["yard", "spur", "siding"]
    #            and element["tags"].get("usage") not in ["industrial"]
    duckdb.sql(
        """
        DROP TABLE IF EXISTS filtered_railways;
        CREATE TABLE filtered_railways AS (
            SELECT id, nodes, tags, geom FROM railways_input
            WHERE
                (json_extract(tags::JSON, '$.usage') != '"industrial"')  -- this will filter out small narrow-gauge tourism-type railways in case they do not include any "usage" value, which happens...
                -- (json_extract(tags::JSON, '$.usage') IS NULL OR json_extract(tags::JSON, '$.usage') != '"industrial"')  -- this will include small narrow-gauge tourism-type railways... and runs into a weird error on HU dataset
                AND (json_extract(tags::JSON, '$.passenger') IS NULL OR json_extract(tags::JSON, '$.passenger') != '"no"')
                AND (json_extract(tags::JSON, '$.railway') IS NULL OR json_extract(tags::JSON, '$.railway') NOT IN ('"construction"', '"disused"', '"abandoned"', '"proposed"'))
                AND (json_extract(tags::JSON, '$.service') IS NULL OR json_extract(tags::JSON, '$.service') NOT IN ('"yard"', '"spur"', '"siding"'))
            OR
                (json_extract(tags::JSON, '$.usage') IN ('"branch"', '"tourism"') )
                --AND (
                --    json_extract(tags::JSON, '$.railway:traffic_mode') = '"passenger"'
                --    OR json_extract(tags::JSON, '$.railway:traffic_mode') = '"mixed"'
                --    OR json_extract(tags::JSON, '$.passenger_lines') IS NOT NULL
                -- )
        );
    """
    )
    print("We have this many line segments as railways to process AFTER filtering:")
    duckdb.sql(
        """
        SELECT count(*) FROM filtered_railways;
        """
    ).show()


def transform_lines_to_buffered_polygons():
    # As input we have "line" geometries. We have to make polygons, to buffer them.
    # To make a polygon from a line, we'll list its nodes backwards, so we repeat the
    # dots to connect the line with itself to a polygon of 0 area. Then we can buffer it
    # To avoid "Invalid Input Error: ST_MakePolygon shell must be closed (first and last vertex must be equal)"
    duckdb.sql(
        f"""
        CREATE TABLE polygons_webmercator AS (
            SELECT
              filtered_railways.id AS way_id,
              geom AS orig_geom,
              -- ST_Astext(geom) as geom_text, -- for debugging if needed
              list_concat(
                string_split( (split_part( (split_part( ST_Astext(geom) , '(' , 2)) , ')', 1) ), ',' ),
                list_reverse(string_split( (split_part( (split_part( ST_Astext(geom) , '(' , 2)) , ')', 1) ), ',' ))
              ) AS polygon_points,
              ST_Buffer(ST_Transform(
                  ST_MakePolygon(
                    ST_GeomFromText(
                        'LINESTRING ( ' ||
                            list_aggr(
                                list_concat(
                                    string_split( (split_part( (split_part( ST_Astext(geom) , '(' , 2)) , ')', 1) ), ',' ),
                                    list_reverse(string_split( (split_part( (split_part( ST_Astext(geom) , '(' , 2)) , ')', 1) ), ',' ))
                                ),
                                'string_agg', ','
                            ) ||
                        ')'
                    )
                  ),
                  'EPSG:4326', 'EPSG:3857'), {RAILS_BUFFER_WIDTH_M})  -- transform from WGS84 (lat,lon) to WebMercator(metres) before buffering with metres
              AS geom
            FROM filtered_railways
        );
    """
    )
    print("Transformed lines from WGS84 data to polygons in WebMercator projection.")


def save_pre_merged_polygons_to_testfile(country_code):
    duckdb.sql(
        f"""
        CREATE TABLE testout_geojson_polys AS (
            SELECT
                ST_Transform(geom, 'EPSG:3857', 'EPSG:4326') as geom,
                way_id -- ,
            FROM polygons_webmercator
        );
        COPY testout_geojson_polys TO 'testpolys_premerge_{country_code}.geojson' WITH (FORMAT GDAL, DRIVER 'GeoJSON');
    """
    )


def save_merged_polygons_to_file(country_code: str):
    duckdb.sql(
        f"""
        CREATE TABLE merged_geojson_polys AS (
            SELECT
                ST_Transform(geom, 'EPSG:3857', 'EPSG:4326') as geom,
                set_id,
                area_m2
            FROM merged_polygons_with_areas
        );
        COPY merged_geojson_polys TO 'merged_polygons_{country_code}.geojson' WITH (FORMAT GDAL, DRIVER 'GeoJSON');
    """
    )


def merge_overlapping_polygons(country_code: str):
    print("Merging partially overlapping polygons...")
    # step 1: check if any overlaps pairwise, for the bufered extented segments
    find_mergeable_polygons_pairwise(country_code)  # will write a csv file
    # step 2: from the pairs of IDs, make bigger sets that can be all merged together (most often if there are parallel tracks)
    to_merge_id_sets = find_all_mergeable_id_sets(
        country_code
    )  # will read the csv file from above step
    # step 3: from the ID sets actually merge the polygons
    merge_polygons_from_id_sets(to_merge_id_sets)


def find_mergeable_polygons_pairwise(country_code):
    # For faster exec: do not calculate the whole ST_Intersection polygon, just use the boolean-valued ST_Intersects() function
    duckdb.sql(
        f"""
        CREATE TABLE mergeable_id_pairs AS (
            SELECT
                poly1.way_id AS way_id_1,
                poly2.way_id AS way_id_2
            FROM polygons_webmercator AS poly1
            JOIN polygons_webmercator AS poly2 ON (
                poly1.way_id < poly2.way_id AND ST_Intersects(poly1.geom, poly2.geom)
            )
            ORDER BY poly1.way_id, poly2.way_id
        );
        COPY mergeable_id_pairs TO 'pairwise_overlap_way_ids_{country_code}.csv' (FORMAT CSV, DELIMITER ',', HEADER);
    """
    )


def find_all_mergeable_id_sets(country_code):
    # reading from csv file that was saved from DuckDB
    # it's ok to work with the IDs as strings
    prev_firstindex = 0
    tmp_collector = []
    all_sets = []
    with open(f"pairwise_overlap_way_ids_{country_code}.csv", mode="r") as file:
        csvFile = csv.reader(file)
        for line in csvFile:
            if line[0] == prev_firstindex:
                tmp_collector.append(line[1])
            else:
                # a new first index appeared: clear the tmp stuff and put into a new set
                all_sets.append(set([prev_firstindex] + tmp_collector))
                tmp_collector = []
                prev_firstindex = line[0]
                # dont forget the new pair, has to be in hte newly re-set tmp collector
                tmp_collector.append(line[1])

    all_sets = all_sets[2:]
    # second run through the sets so far
    final_sets = []
    set_indices_already_saved = set([])
    for i, partial_set_a in enumerate(all_sets):
        if i not in set_indices_already_saved:
            final_sets.append(partial_set_a)
            set_indices_already_saved.add(i)
            for j, partial_set_b in enumerate(all_sets):
                if i < j and j not in set_indices_already_saved:
                    if not partial_set_a.isdisjoint(partial_set_b):
                        final_sets[-1] = final_sets[-1].union(partial_set_b)
                        set_indices_already_saved.add(j)

    # third run to merge whatever remains
    final_sets_2 = []
    set_indices_already_saved_2 = set([])
    for i, partial_set_a in enumerate(final_sets):
        if i not in set_indices_already_saved_2:
            final_sets_2.append(partial_set_a)
            set_indices_already_saved_2.add(i)
            for j, partial_set_b in enumerate(final_sets):
                if i < j and j not in set_indices_already_saved_2:
                    if not partial_set_a.isdisjoint(partial_set_b):
                        final_sets_2[-1] = final_sets_2[-1].union(partial_set_b)
                        set_indices_already_saved_2.add(j)
    return final_sets_2


def merge_polygons_from_id_sets(sets_of_ids_to_merge):
    # now we have to get the actual polygons by IDs
    # first we write a CSV file to then quickly read it to DuckDB
    # we need to map original way-ids to a new id per set
    with open(f"mergeable_id_sets_{country_code}.csv", "w", newline="") as csvfile:
        csvwriter = csv.writer(
            csvfile, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL
        )
        for i, id_set in enumerate(sets_of_ids_to_merge):
            for poly_id in id_set:
                csvwriter.writerow([poly_id, i])

    # first we save the ids into a new table
    duckdb.sql(
        f"""
        CREATE TABLE id_set_map (
            way_id VARCHAR,
            set_id INTEGER
        );
        COPY id_set_map FROM 'mergeable_id_sets_{country_code}.csv';
        """
    )

    duckdb.sql(
        """
        CREATE TABLE merged_polygons AS (
            SELECT
            ST_Union_Agg(geom) AS geom,
            set_id AS set_id
            FROM polygons_webmercator AS polys
            JOIN id_set_map ON polys.way_id = id_set_map.way_id
            GROUP BY id_set_map.set_id
        )
    """
    )


def calculate_areas():
    # Q: should we pick a better CRS based on the country? this general solution will be distorted, especially in the north and south,
    # essentially we are giving far-up-north and far-down-south polygons "more areal weight" bc of the global projections's distortion
    duckdb.sql(
        """
        CREATE TABLE merged_polygons_with_areas AS (
            SELECT
            geom,
            ST_Area(geom) AS area_m2,
            set_id
            FROM merged_polygons
        );
    """
    )

    total_area = duckdb.sql(
        """
        SELECT sum(area_m2) AS total_area_m2
        FROM merged_polygons_with_areas;
    """
    ).fetchall()
    # we got a table result, just need the numeric value from 1st row's 1st cell
    return total_area[0][0]


def save_final_geojson_file(country_code, total_area):
    # we read in the lastfile we just wrote to disc with areas calculated for polygons, but not yet with total area
    # enough to read as simple general json file this time
    with open(f"merged_polygons_{country_code}.geojson", "r") as f_in:
        data = json.load(f_in)
        # set the total area value, calculated in prev step
        data["total_area_m2"] = total_area
        processed_path = PROCESSED_FILE_DIR + "/" + country_code + ".geojson"
        print(f"Saving processed data for {country_code}...")
        with open(processed_path, "w") as f_out:
            json.dump(data, f_out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Please provide a country's ISO code as a command-line argument.")
        sys.exit(1)
    country_code = sys.argv[1].upper()
    make_file_dirs()
    json_path = download_railways_from_overpass(country_code, force_refetch=False)
    geojson_path = convert_json_to_geojson(json_path)
    process_railways(geojson_path, country_code)
