import imghdr
import json
import math
import os
import time
import unicodedata
from urllib.request import urlopen

import pycountry
import yaml
from geopy.distance import geodesic

from py import geopip_perso


def remove_accents(input_str):
    nfkd_form = unicodedata.normalize("NFKD", input_str)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])


def longest_common_substring(s1, s2):
    m = [[0] * (1 + len(s2)) for _ in range(1 + len(s1))]
    longest, x_longest = 0, 0
    for x in range(1, 1 + len(s1)):
        for y in range(1, 1 + len(s2)):
            if s1[x - 1] == s2[y - 1]:
                m[x][y] = m[x - 1][y - 1] + 1
                if m[x][y] > longest:
                    longest = m[x][y]
                    x_longest = x
            else:
                m[x][y] = 0
    return len(s1[x_longest - longest : x_longest])


def stringSimmilarity(a, b):
    # Lowercase and remove accents for better similarity detection
    a = remove_accents(a.lower())
    b = remove_accents(b.lower())

    lcs_val = longest_common_substring(a, b)

    ratioA = lcs_val / len(a)
    ratioB = lcs_val / len(b)

    combined = (ratioA + ratioB) / 2.0

    return combined * 100.0


def getCountryFromCoordinates(lat, lng):
    country = geopip_perso.search(lat=lat, lng=lng)
    if not country:
        country = {"countryCode": "UN"}
    return country


def load_config(filename="config.yaml"):
    with open(filename, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def hex_to_rgb(hex_color):
    # Convert hex color to an RGB tuple (values between 0 and 1)
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(rgb_color):
    # Convert an RGB tuple back to hex color
    return "#" + "".join(f"{int(x * 255):02x}" for x in rgb_color)


def get_flag_emoji(country_code):
    if country_code.lower() == "en":
        country_code = "GB"
    code_points = [ord(char) + 127397 for char in country_code.upper()]
    return "".join(chr(code_point) for code_point in code_points)


def get_all_countries():
    # Create a dictionary with alpha_2 as key and flag emoji as value
    country_dict = {
        country.alpha_2: get_flag_emoji(country.alpha_2)
        for country in pycountry.countries
    }
    return country_dict


# Function to remove diacritics with improved error handling
def remove_diacritics(text):
    if text is None:
        return ""
    try:
        return "".join(
            c
            for c in unicodedata.normalize("NFKD", text)
            if unicodedata.category(c) != "Mn"
        )
    except Exception as e:
        # Handle unexpected exceptions
        print(f"Error processing text {text}: {e}")
        return ""


def getIp(request):
    if request.headers.getlist("X-Forwarded-For"):
        ip_address = request.headers.getlist("X-Forwarded-For")[0]
    else:
        ip_address = request.remote_addr
    return ip_address


ip_cache = {}


def getIpDetails(ip):
    current_time = time.time()

    # Cache expiry time: 12 hours
    cache_duration = 12 * 60 * 60

    # Check if the IP is in cache and still valid
    if ip in ip_cache and current_time - ip_cache[ip]["timestamp"] < cache_duration:
        return ip_cache[ip]["data"]

    # If not in cache or expired, fetch new data
    url = f"http://ipinfo.io/{ip}/json"
    response = urlopen(url)
    rawData = json.load(response)
    data = {
        "city": rawData.get("city"),
        "country": rawData.get("country")
        if rawData.get("country") is not None
        else "UN",
        "region": rawData.get("region"),
        "org": rawData.get("org"),
        "loc": rawData.get("loc"),
    }

    # Store in cache with timestamp
    ip_cache[ip] = {"data": data, "timestamp": current_time}

    return data


def getRequestData(request):
    # Retrieve form data
    form_data = request.form.to_dict()
    if "password" in form_data.keys():
        form_data.pop("password")
    # Retrieve JSON data
    json_data = request.get_json(silent=True)
    # Retrieve headers
    headers = dict(request.headers)
    # Retrieve files
    files = {file: request.files[file].filename for file in request.files}
    # Collect all data into a single dictionary
    all_data = {
        "form_data": form_data,
        "json_data": json_data,
        "headers": headers,
        "files": files,
    }
    return json.dumps(all_data)


# Helper function to validate the PNG file
def validate_png_file(file):
    MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB in bytes
    if not file:
        raise Exception("No file provided")

    if file.filename == "":
        raise Exception("No selected file")

    if not file.filename.lower().endswith(".png"):  # Validate file extension
        raise Exception("Invalid file type, only PNG files are allowed")

    # Check if the file is indeed a PNG by reading its header
    file.seek(0)  # Reset file pointer to the start for imghdr
    if imghdr.what(None, h=file.stream.read()) != "png":
        raise Exception("File is broken or not a PNG")

    # Check the file size
    file.seek(0, os.SEEK_END)  # Move the cursor to the end of the file
    file_size = file.tell()  # Get the file size
    if file_size > MAX_FILE_SIZE:
        raise Exception("File too large, must be less than 1MB")

    file.seek(0)  # Reset the cursor to the beginning of the file
    return True


def getDistance(orig, dest):
    R = 6373000.0
    lat1 = math.radians(orig["lat"])
    lon1 = math.radians(orig["lng"])
    lat2 = math.radians(dest["lat"])
    lon2 = math.radians(dest["lng"])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c
    return distance


def getCountriesFromPath(path, type):
    countries = {}
    country = None

    if type in ["air", "helicopter"]:
        total_distance = 0
        for index in range(1, len(path)):
            total_distance += getDistance(path[index - 1], path[index])

        start_country_data = getCountryFromCoordinates(
            lat=path[0]["lat"], lng=path[0]["lng"]
        )
        end_country_data = getCountryFromCoordinates(
            lat=path[-1]["lat"], lng=path[-1]["lng"]
        )

        start_country = (
            start_country_data["countryCode"] if start_country_data else "UN"
        )
        end_country = end_country_data["countryCode"] if end_country_data else "UN"

        countries[start_country] = total_distance / 2
        countries[end_country] = countries.get(end_country, 0) + total_distance / 2

        return json.dumps(countries)

    for index in range(1, len(path)):
        segment_distance = getDistance(path[index - 1], path[index])

        if type == "ferry" and segment_distance > 10:
            num_fake_points = int(segment_distance / 10)
            interpolated_points = interpolate_points(
                path[index - 1], path[index], num_fake_points
            )
        else:
            interpolated_points = [path[index]]

        segment_countries = {}
        for node in interpolated_points:
            precountry = getCountryFromCoordinates(lat=node["lat"], lng=node["lng"])
            if precountry is not None:
                country = precountry["countryCode"]
            else:
                if type == "ferry":
                    country = "UN"
                else:
                    if country is None:
                        country = "UN"
            segment_countries[country] = segment_countries.get(country, 0) + 1

        for country, count in segment_countries.items():
            if country not in countries:
                countries[country] = 0
            countries[country] += (segment_distance * count) / len(interpolated_points)

    if countries == {}:
        country = getCountryFromCoordinates(lat=path[0]["lat"], lng=path[0]["lng"])[
            "countryCode"
        ]
        countries = {country: 0}

    return json.dumps(countries)


def getDistanceFromPath(path):
    distances = path.copy()
    for index, node in enumerate(path):
        if index == 0:
            distances[index] = 0
        else:
            previousDistance = distances[index - 1]
            previousNode = {"lat": path[index - 1][0], "lng": path[index - 1][1]}
            currentNode = {"lat": path[index][0], "lng": path[index][1]}
            currentDistance = previousDistance + int(
                getDistance(previousNode, currentNode)
            )
            distances[index] = currentDistance
    return distances


def interpolate_points(point1, point2, num_points):
    """Interpolate num_points between point1 and point2."""
    lat_diff = point2["lat"] - point1["lat"]
    lng_diff = point2["lng"] - point1["lng"]

    interpolated = []
    for i in range(1, num_points + 1):
        fraction = i / (num_points + 1)
        interpolated.append(
            {
                "lat": point1["lat"] + fraction * lat_diff,
                "lng": point1["lng"] + fraction * lng_diff,
            }
        )
    return interpolated


def to_radians(deg):
    return deg * math.pi / 180


def to_degrees(rad):
    return rad * 180 / math.pi


def interpolate_great_circle(start, end, max_distance_km=50):
    """Interpolates (lat, lon) points along the great-circle path using spherical interpolation."""
    distance_km = geodesic(start, end).km
    num_steps = int(distance_km // max_distance_km)

    if num_steps < 1:
        return []

    lat1, lon1 = map(to_radians, start)
    lat2, lon2 = map(to_radians, end)

    delta_lon = lon2 - lon1

    # Compute angular distance between points
    d = 2 * math.asin(
        math.sqrt(
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
    )

    if d == 0:
        return []

    points = []

    for i in range(1, num_steps + 1):
        f = i / (num_steps + 1)
        A = math.sin((1 - f) * d) / math.sin(d)
        B = math.sin(f * d) / math.sin(d)

        x = A * math.cos(lat1) * math.cos(lon1) + B * math.cos(lat2) * math.cos(lon2)
        y = A * math.cos(lat1) * math.sin(lon1) + B * math.cos(lat2) * math.sin(lon2)
        z = A * math.sin(lat1) + B * math.sin(lat2)

        interp_lat = math.atan2(z, math.sqrt(x**2 + y**2))
        interp_lon = math.atan2(y, x)

        points.append((to_degrees(interp_lat), to_degrees(interp_lon)))

    return points


def interpolate_points_if_gaps(points, max_distance_km=50):
    """Given a list of (lat, lon) points, interpolate between them when distance > max_distance_km."""
    if not points or len(points) < 2:
        return points

    print(len(points))

    interpolated = [points[0]]

    for i in range(1, len(points)):
        prev = points[i - 1]
        curr = points[i]
        distance = geodesic(prev, curr).km

        if distance > max_distance_km:
            # Add intermediate points
            intermediate = interpolate_great_circle(
                prev, curr, max_distance_km=max_distance_km
            )
            interpolated.extend(intermediate)

        interpolated.append(curr)
    print(len(interpolated))

    return interpolated
