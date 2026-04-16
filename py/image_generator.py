import os
from io import BytesIO

import cairosvg
import geopandas as gpd
import pycountry
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from py.coverage import get_coverage_geojson_dict

regions = {
    "FR-ARA": "Auvergne-Rhône-Alpes",
    "FR-BFC": "Bourgogne-Franche-Comté",
    "FR-BRE": "Bretagne",
    "FR-CVL": "Centre-Val de Loire",
    "FR-20R": "Corse",
    "FR-GES": "Grand-Est",
    "FR-HDF": "Hauts-de-France",
    "FR-IDF": "Île-de-France",
    "FR-NOR": "Normandie",
    "FR-NAQ": "Nouvelle-Aquitaine",
    "FR-OCC": "Occitanie",
    "FR-PDL": "Pays-de-la-Loire",
    "FR-PAC": "Provence-Alpes-Côte d’Azur",
    "FR-NCL": "Nouvelle-Calédonie",
    "DE-BW": "Baden-Württemberg",
    "DE-BY": "Bayern",
    "DE-BE": "Berlin",
    "DE-BB": "Brandenburg",
    "DE-HB": "Bremen",
    "DE-HH": "Hamburg",
    "DE-HE": "Hessen",
    "DE-MV": "Mecklenburg-Vorpommern",
    "DE-NI": "Niedersachsen",
    "DE-NW": "Nordrhein-Westfalen",
    "DE-RP": "Rheinland-Pfalz",
    "DE-SL": "Saarland",
    "DE-SN": "Sachsen",
    "DE-ST": "Sachsen-Anhalt",
    "DE-SH": "Schleswig-Holstein",
    "DE-TH": "Thüringen",
    "IT-65": "Abruzzo",
    "IT-77": "Basilicata",
    "IT-78": "Calabria",
    "IT-72": "Campania",
    "IT-45": "Emilia-Romagna",
    "IT-36": "Friuli Venezia Giulia",
    "IT-62": "Lazio",
    "IT-42": "Liguria",
    "IT-25": "Lombardia",
    "IT-57": "Marche",
    "IT-67": "Molise",
    "IT-21": "Piemonte",
    "IT-75": "Puglia",
    "IT-88": "Sardegna",
    "IT-82": "Sicilia",
    "IT-52": "Toscana",
    "IT-32": "Trentino-Alto Adige",
    "IT-55": "Umbria",
    "IT-23": "Valle d'Aosta",
    "IT-34": "Veneto",
    "TR-01": "Adana",
    "TR-10": "Balıkesir",
    "TR-20": "Denizli",
    "TR-27": "Gaziantep",
    "TR-38": "Kayseri",
    "TR-44": "Malatya",
    "TR-80": "Osmaniye",
    "TR-62": "Tunceli",
    "TR-35": "İzmir",
    "TR-02": "Adıyaman",
    "TR-72": "Batman",
    "TR-21": "Diyarbakır",
    "TR-31": "Hatay",
    "TR-41": "Kocaeli",
    "TR-45": "Manisa",
    "TR-54": "Sakarya",
    "TR-64": "Uşak",
    "TR-63": "Şanlıurfa",
    "TR-03": "Afyonkarahisar",
    "TR-11": "Bilecik",
    "TR-22": "Edirne",
    "TR-32": "Isparta",
    "TR-42": "Konya",
    "TR-47": "Mardin",
    "TR-55": "Samsun",
    "TR-65": "Van",
    "TR-05": "Amasya",
    "TR-12": "Bingöl",
    "TR-23": "Elazığ",
    "TR-46": "Kahramanmaraş",
    "TR-43": "Kütahya",
    "TR-33": "Mersin",
    "TR-56": "Siirt",
    "TR-66": "Yozgat",
    "TR-06": "Ankara",
    "TR-13": "Bitlis",
    "TR-24": "Erzincan",
    "TR-78": "Karabük",
    "TR-39": "Kırklareli",
    "TR-49": "Muş",
    "TR-58": "Sivas",
    "TR-67": "Zonguldak",
    "TR-75": "Ardahan",
    "TR-15": "Burdur",
    "TR-25": "Erzurum",
    "TR-70": "Karaman",
    "TR-71": "Kırıkkale",
    "TR-50": "Nevşehir",
    "TR-59": "Tekirdağ",
    "TR-18": "Çankırı",
    "TR-09": "Aydın",
    "TR-16": "Bursa",
    "TR-26": "Eskişehir",
    "TR-36": "Kars",
    "TR-40": "Kırşehir",
    "TR-51": "Niğde",
    "TR-60": "Tokat",
    "TR-34": "İstanbul",
    "CH-LU": "Luzern",
    "CH-NE": "Neuchâtel",
    "CH-NW": "Nidwalden",
    "CH-OW": "Obwalden",
    "CH-SG": "Sankt Gallen",
    "CH-SH": "Schaffhausen",
    "CH-SO": "Solothurn",
    "CH-SZ": "Schwyz",
    "CH-TG": "Thurgau",
    "CH-TI": "Ticino",
    "CH-UR": "Uri",
    "CH-AG": "Aargau",
    "CH-VD": "Vaud",
    "CH-AI": "Appenzell Innerrhoden",
    "CH-VS": "Valais",
    "CH-AR": "Appenzell Ausserrhoden",
    "CH-ZG": "Zug",
    "CH-BE": "Bern",
    "CH-ZH": "Zürich",
    "CH-BL": "Basel-Landschaft",
    "CH-BS": "Basel-Stadt",
    "CH-FR": "Freiburg",
    "CH-GE": "Genève",
    "CH-GL": "Glarus",
    "CH-GR": "Graubünden",
    "CH-JU": "Jura",
    "US-AL": "Alabama",
    "US-AK": "Alaska",
    "US-AZ": "Arizona",
    "US-AR": "Arkansas",
    "US-CA": "California",
    "US-CO": "Colorado",
    "US-CT": "Connecticut",
    "US-DE": "Delaware",
    "US-DC": "District of Columbia",
    "US-FL": "Florida",
    "US-GA": "Georgia",
    "US-HI": "Hawaii",
    "US-ID": "Idaho",
    "US-IL": "Illinois",
    "US-IN": "Indiana",
    "US-IA": "Iowa",
    "US-KS": "Kansas",
    "US-KY": "Kentucky",
    "US-LA": "Louisiana",
    "US-ME": "Maine",
    "US-MD": "Maryland",
    "US-MA": "Massachusetts",
    "US-MI": "Michigan",
    "US-MN": "Minnesota",
    "US-MS": "Mississippi",
    "US-MO": "Missouri",
    "US-MT": "Montana",
    "US-NE": "Nebraska",
    "US-NV": "Nevada",
    "US-NH": "New Hampshire",
    "US-NJ": "New Jersey",
    "US-NM": "New Mexico",
    "US-NY": "New York",
    "US-NC": "North Carolina",
    "US-ND": "North Dakota",
    "US-OH": "Ohio",
    "US-OK": "Oklahoma",
    "US-OR": "Oregon",
    "US-PA": "Pennsylvania",
    "US-RI": "Rhode Island",
    "US-SC": "South Carolina",
    "US-SD": "South Dakota",
    "US-TN": "Tennessee",
    "US-TX": "Texas",
    "US-UT": "Utah",
    "US-VT": "Vermont",
    "US-VA": "Virginia",
    "US-WA": "Washington",
    "US-WV": "West Virginia",
    "US-WI": "Wisconsin",
    "US-WY": "Wyoming",
    "US-AS": "American Samoa",
    "US-GU": "Guam",
    "US-MP": "Northern Mariana Islands",
    "US-PR": "Puerto Rico",
    "US-UM": "United States Minor Outlying Islands",
    "US-VI": "Virgin Islands, U.S.",
    "CN-BJ": "Beijing",
    "CN-TJ": "Tianjin",
    "CN-HE": "Hebei",
    "CN-SX": "Shanxi",
    "CN-NM": "Inner Mongolia",
    "CN-LN": "Liaoning",
    "CN-JL": "Jilin",
    "CN-HL": "Heilongjiang",
    "CN-SH": "Shanghai",
    "CN-JS": "Jiangsu",
    "CN-ZJ": "Zhejiang",
    "CN-AH": "Anhui",
    "CN-FJ": "Fujian",
    "CN-JX": "Jiangxi",
    "CN-SD": "Shandong",
    "CN-HA": "Henan",
    "CN-HB": "Hubei",
    "CN-HN": "Hunan",
    "CN-GD": "Guangdong",
    "CN-GX": "Guangxi",
    "CN-HI": "Hainan",
    "CN-CQ": "Chongqing",
    "CN-SC": "Sichuan",
    "CN-GZ": "Guizhou",
    "CN-YN": "Yunnan",
    "CN-XZ": "Tibet",
    "CN-SN": "Shaanxi",
    "CN-GS": "Gansu",
    "CN-QH": "Qinghai",
    "CN-NX": "Ningxia",
    "CN-XJ": "Xinjiang",
    "CN-HK": "Hong Kong",
    "CN-MO": "Macao",
    "AT-2": "Kärnten",
    "AT-4": "Oberösterreich",
    "AT-6": "Steiermark",
    "AT-8": "Vorarlberg",
    "AT-5": "Salzburg",
    "AT-1": "Burgenland",
    "AT-3": "Niederösterreich",
    "AT-7": "Tirol",
    "AT-9": "Wien",
    "CZ-41": "Karlovarský kraj",
    "CZ-42": "Ústecký kraj",
    "CZ-51": "Liberecký kraj",
    "CZ-52": "Královéhradecký kraj",
    "CZ-53": "Pardubický kraj",
    "CZ-63": "Kraj Vysočina",
    "CZ-64": "Jihomoravský kraj",
    "CZ-71": "Olomoucký kraj",
    "CZ-72": "Zlínský kraj",
    "CZ-80": "Moravskoslezský kraj",
    "CZ-10": "Praha, Hlavní město",
    "CZ-20": "Středočeský kraj",
    "CZ-31": "Jihočeský kraj",
    "CZ-32": "Plzeňský kraj",
    "GB-NIR": "Northern Ireland",
    "GB-SCT": "Scotland",
    "GB-WLS": "Wales",
    "GB-ENG": "England",
    "IE-L": "Leinster",
    "IE-M": "Munster",
    "IE-C": "Connacht",
    "IE-U": "Ulster",
    "SE-K": "Blekinge",
    "SE-W": "Dalarna",
    "SE-I": "Gotland",
    "SE-X": "Gävleborg",
    "SE-N": "Halland",
    "SE-Z": "Jämtland",
    "SE-F": "Jönköping",
    "SE-H": "Kalmar",
    "SE-G": "Kronoberg",
    "SE-BD": "Norrbotten",
    "SE-M": "Skåne",
    "SE-AB": "Stockholm",
    "SE-D": "Södermanland",
    "SE-C": "Uppsala",
    "SE-S": "Värmland",
    "SE-AC": "Västerbotten",
    "SE-Y": "Västernorrland",
    "SE-U": "Västmanland",
    "SE-O": "Västra Götaland",
    "SE-T": "Örebro",
    "SE-E": "Östergötland",
}


def add_rounded_corners(image, radius):
    # Create a rounded corner mask
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0) + image.size, radius=radius, fill=255)

    # Apply the mask to the image
    image = ImageOps.fit(image, mask.size)
    image.putalpha(mask)
    return image


def add_drop_shadow(
    image,
    offset=(5, 5),
    background_color=0x00000000,
    shadow_color=0x444444FF,
    border=8,
    iterations=3,
):
    total_width = image.size[0] + abs(offset[0]) + 2 * border
    total_height = image.size[1] + abs(offset[1]) + 2 * border
    shadow = Image.new("RGBA", (total_width, total_height), background_color)

    shadow_left = border + max(offset[0], 0)
    shadow_top = border + max(offset[1], 0)
    shadow.paste(
        shadow_color,
        [
            shadow_left,
            shadow_top,
            shadow_left + image.size[0],
            shadow_top + image.size[1],
        ],
    )

    for _ in range(iterations):
        shadow = shadow.filter(ImageFilter.BLUR)

    shadow.paste(image, (border, border), image)
    return shadow


def get_country_name(country_code):
    try:
        country = pycountry.countries.get(alpha_2=country_code.upper())
        return country.name if country else country_code
    except KeyError:
        return country_code  # Fallback to the code if not found


def generate_image(cc):
    # Load the (immediate or stitched) coverage GeoJSON into a GeoDataFrame
    geojson_data = get_coverage_geojson_dict(cc)
    gdf = gpd.GeoDataFrame.from_features(geojson_data["features"])

    # Calculate bounds of the GeoDataFrame to determine image size
    minx, miny, maxx, maxy = gdf.total_bounds
    width_ratio = maxx - minx
    height_ratio = maxy - miny

    # Define the base size and calculate height to maintain aspect ratio
    base_size = 5000  # You can adjust this as needed
    margin = 50  # 50px margin

    # Calculate image dimensions maintaining aspect ratio
    aspect_ratio = width_ratio / height_ratio

    if aspect_ratio > 1:
        img_width = base_size
        img_height = int(base_size / aspect_ratio)
    else:
        img_height = base_size
        img_width = int(base_size * aspect_ratio)

    # Adjust image size to include margin
    img_width += 2 * margin
    img_height += 2 * margin

    # Increase resolution for anti-aliasing (2x scaling factor)
    scale_factor = 2
    high_res_width = img_width * scale_factor
    high_res_height = img_height * scale_factor

    # Adjust scaling to account for the margin and high resolution
    scale_x = (high_res_width - 2 * margin * scale_factor) / width_ratio
    scale_y = (high_res_height - 2 * margin * scale_factor) / height_ratio

    # Create a high-resolution blank image with transparent background
    img = Image.new("RGBA", (high_res_width, high_res_height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    # Define the forest green color
    forest_green_outline = (34, 139, 34, 255)  # RGBA for opaque outline
    forest_green_fill = (34, 139, 34, 128)  # RGBA for semi-transparent fill

    # Thickness parameter
    thickness = 20  # Increase this value to make lines thicker

    for geom in gdf.geometry:
        if geom.geom_type == "Polygon":
            # Convert the Polygon to pixel coordinates
            exterior_coords = [
                (
                    scale_x * (x - minx) + margin * scale_factor,
                    high_res_height - scale_y * (y - miny) - margin * scale_factor,
                )
                for x, y in geom.exterior.coords
            ]

            # Draw thicker lines by repeatedly drawing with small offsets
            for offset in range(-thickness, thickness + 1):
                offset_coords = [(x + offset, y + offset) for x, y in exterior_coords]
                draw.polygon(
                    offset_coords, outline=forest_green_outline, fill=forest_green_fill
                )

        elif geom.geom_type == "MultiPolygon":
            for poly in geom.geoms:
                exterior_coords = [
                    (
                        scale_x * (x - minx) + margin * scale_factor,
                        high_res_height - scale_y * (y - miny) - margin * scale_factor,
                    )
                    for x, y in poly.exterior.coords
                ]

                # Draw thicker lines by repeatedly drawing with small offsets
                for offset in range(-thickness, thickness + 1):
                    offset_coords = [
                        (x + offset, y + offset) for x, y in exterior_coords
                    ]
                    draw.polygon(
                        offset_coords,
                        outline=forest_green_outline,
                        fill=forest_green_fill,
                    )

    # Determine if cc represents a country or a region
    if len(cc) == 2:  # Country
        region_name = get_country_name(cc)
        flag_path = f"static/images/flags/{cc.lower()}.svg"
        region_flag_img = None  # No region flag for a country
    else:  # Region
        country_code = cc.split("-")[
            0
        ]  # Assuming the cc follows the pattern "country-region"
        region_name = regions.get(cc, cc)
        flag_path = f"static/images/flags/{country_code.lower()}.svg"
        region_flag_path = f"static/images/flags/{cc.lower()}.svg"

        # Check if the region flag SVG file exists
        if os.path.exists(region_flag_path):
            # Convert SVG region flag to PNG and load it
            region_flag_png = BytesIO()
            cairosvg.svg2png(
                url=region_flag_path, write_to=region_flag_png, output_height=500
            )
            region_flag_img = Image.open(region_flag_png).convert("RGBA")
        else:
            region_flag_img = (
                None  # If the file doesn't exist, do not load any region flag
            )

    # Convert SVG country flag to PNG and load it
    flag_png = BytesIO()
    cairosvg.svg2png(url=flag_path, write_to=flag_png, output_height=500)
    flag_img = Image.open(flag_png).convert("RGBA")

    # Determine the maximum flag height (e.g., 10% of the image width)
    max_flag_height = int(high_res_width * 0.1)

    # Resize the country flag to fit within the max height
    flag_img_ratio = max_flag_height / flag_img.height
    flag_img = flag_img.resize(
        (int(flag_img.width * flag_img_ratio), max_flag_height), Image.LANCZOS
    )

    # Resize the region flag if it exists
    if region_flag_img:
        region_flag_img_ratio = max_flag_height / region_flag_img.height
        region_flag_img = region_flag_img.resize(
            (int(region_flag_img.width * region_flag_img_ratio), max_flag_height),
            Image.LANCZOS,
        )

    # Apply rounded corners and drop shadow to both flags, except for Nepal, SE, CH special cases
    corner_radius = max_flag_height // 15
    if cc.lower() not in ["np"]:
        flag_img = add_rounded_corners(flag_img, corner_radius)
    if region_flag_img:
        if cc.lower().split("-")[0] not in ["ch", "se"]:
            region_flag_img = add_rounded_corners(region_flag_img, corner_radius)

    # Apply drop shadow to all flags
    flag_img = add_drop_shadow(flag_img)
    if region_flag_img:
        region_flag_img = add_drop_shadow(region_flag_img)

    # Create a box for the country flag
    country_flag_box = Image.new(
        "RGBA", (flag_img.width, max_flag_height), (255, 255, 255, 0)
    )
    country_flag_box.paste(flag_img, (0, 0), flag_img)

    # Create a box for the region flag (if applicable)
    if region_flag_img:
        region_flag_box = Image.new(
            "RGBA", (region_flag_img.width, max_flag_height), (255, 255, 255, 0)
        )
        region_flag_box.paste(region_flag_img, (0, 0), region_flag_img)
    else:
        region_flag_box = None

    # Load font and calculate text size
    font_path = (
        "static/styles/fonts/Montserrat-Bold.ttf"  # Specify the path to your font file
    )
    font_size = int(high_res_width * 0.05)
    font = ImageFont.truetype(font_path, font_size)
    text = f"I 100%-ed {region_name}"

    # Calculate text box width
    text_width = high_res_width - country_flag_box.width
    if region_flag_box:
        text_width -= region_flag_box.width
    text_width -= 40  # Additional padding

    # Adjust text box width and font size to fit within the available space
    while True:
        text_box = Image.new("RGBA", (text_width, max_flag_height), (255, 255, 255, 0))
        draw_text = ImageDraw.Draw(text_box)
        text_bbox = draw_text.textbbox((0, 0), text, font=font)
        text_x = (text_width - text_bbox[2]) // 2
        text_y = (max_flag_height - text_bbox[3]) // 2

        if text_bbox[2] + 150 <= text_width:
            break
        else:
            # Reduce font size if the text does not fit
            font_size -= 5
            font = ImageFont.truetype(font_path, font_size)

    draw_text.text((text_x, text_y), text, font=font, fill="black")

    # Combine the flag boxes and text box into one image (Country -> Region -> Text)
    total_width = country_flag_box.width + text_box.width
    if region_flag_box:
        total_width += region_flag_box.width
    total_height = max_flag_height

    combined_box = Image.new("RGBA", (total_width, total_height), (255, 255, 255, 0))
    combined_box.paste(country_flag_box, (0, 0), country_flag_box)
    if region_flag_box:
        combined_box.paste(
            region_flag_box, (country_flag_box.width + 20, 0), region_flag_box
        )
        combined_box.paste(
            text_box, (country_flag_box.width + region_flag_box.width + 40, 0), text_box
        )
    else:
        combined_box.paste(text_box, (country_flag_box.width + 20, 0), text_box)

    # Center the combined box within the image
    title_bar_height = total_height + 100
    title_bar = Image.new(
        "RGBA", (high_res_width, title_bar_height), (255, 255, 255, 0)
    )
    combined_x = (high_res_width - total_width) // 2
    title_bar.paste(combined_box, (combined_x, 50), combined_box)

    # Combine the title bar and the map image
    combined_height = title_bar_height + high_res_height
    combined_image = Image.new(
        "RGBA", (high_res_width, combined_height), (255, 255, 255, 0)
    )
    combined_image.paste(title_bar, (0, 0), title_bar)
    combined_image.paste(img, (0, title_bar_height), img)

    # Load the logo image
    logo_path = "static/images/logo.png"
    logo_img = Image.open(logo_path).convert("RGBA")

    # Resize the logo image
    logo_width = int(high_res_width * 0.2)  # 20% of the image width
    logo_height = int(logo_img.height * (logo_width / logo_img.width))
    logo_img = logo_img.resize((logo_width, logo_height), Image.LANCZOS)

    # Define logo bar height
    logo_bar_height = logo_height + 100  # Some padding

    # Create logo bar image with a transparent background
    logo_bar = Image.new("RGBA", (high_res_width, logo_bar_height), (0, 0, 0, 0))
    ImageDraw.Draw(logo_bar)

    # Calculate the position for the logo in the bottom right corner
    logo_x = high_res_width - logo_width - 50  # 50px margin
    logo_y = (logo_bar_height - logo_height) // 2

    # Paste the logo onto the logo bar
    logo_bar.paste(logo_img, (logo_x, logo_y), logo_img)

    # Combine the final image with the logo bar
    final_height = combined_height + logo_bar_height
    final_image = Image.new("RGBA", (high_res_width, final_height), (255, 255, 255, 0))
    final_image.paste(combined_image, (0, 0), combined_image)
    final_image.paste(logo_bar, (0, combined_height), logo_bar)

    # Downscale the high-resolution image to the final size for smoothing (anti-aliasing)
    final_img_width = img_width
    final_img_height = final_height // scale_factor
    final_image = final_image.resize((final_img_width, final_img_height), Image.LANCZOS)

    # Save the combined image to a BytesIO object
    img_io = BytesIO()
    final_image.save(img_io, "PNG")
    img_io.seek(0)

    return img_io
