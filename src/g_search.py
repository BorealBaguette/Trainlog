import json
import os

import requests
from google_images_search import GoogleImagesSearch

from py.utils import load_config

mids_list = json.load(open("static/data/mids.json"))

google_key = load_config()["google"]["key"]
cx = load_config()["google"]["cx"]

gis = GoogleImagesSearch(google_key, cx)


def get_vessel_picture(name, pg):
    # Check if the ship's picture is already in the database
    images_dir = "static/images/ship_pictures"
    existing_picture = pg.execute(
        "SELECT local_image_path, referrer_url, country_code FROM ship_pictures WHERE vessel_name = :name",
        {"name": name},
    ).fetchone()

    if existing_picture:
        image_url = os.path.join("/", images_dir, existing_picture["local_image_path"])
        referrer_url = existing_picture["referrer_url"]
        country_code = existing_picture["country_code"]

        # If found, return the existing picture data
        return image_url, referrer_url, country_code

    # If not found, fetch from Google Images
    _search_params = {
        "q": name,
        "num": 1,
    }
    gis.search(search_params=_search_params)

    if gis.results():
        for image in gis.results():
            MMSI = image.url.split("/")[-2].split("-")[1]
            MID = MMSI[:3]
            country_code = mids_list.get(MID)[0]
            image_url = image.url
            referrer_url = image.referrer_url

            # Download the image
            response = requests.get(image_url)
            if response.status_code == 200:
                # Create directory if it doesn't exist
                if not os.path.exists(images_dir):
                    os.makedirs(images_dir)

                image_filename = f"{country_code}_{name}_{MMSI}.jpg".replace(
                    " ", "_"
                ).replace("/", "")
                image_path = os.path.join(images_dir, image_filename)
                abs_image_path = os.path.join("/", images_dir, image_filename)

                # Save the image locally
                with open(image_path, "wb") as file:
                    file.write(response.content)

                # Insert the fetched picture data into the database with the local image path
                pg.execute(
                    """
                    INSERT INTO ship_pictures (vessel_name, image_url, referrer_url, country_code, local_image_path)
                    VALUES (:vessel_name, :image_url, :referrer_url, :country_code, :local_image_path)
                    """,
                    {
                        "vessel_name": name,
                        "image_url": image_url,
                        "referrer_url": referrer_url,
                        "country_code": country_code,
                        "local_image_path": image_filename,
                    },
                )

                # Return the local image path and other data
                return abs_image_path, referrer_url, country_code
    else:
        return None
