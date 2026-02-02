import logging
import json
import requests
import base64
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader
from icalendar import Calendar

from py.utils import load_config, getCountryFromCoordinates, get_flag_emoji, getDistance
from src.trips import Trip, create_trip
from src.routing import forward_routing_core
from src.utils import get_default_trip_visibility, pathConn, managed_cursor
from src.pg import pg_session
import re

logger = logging.getLogger(__name__)

class FakeRequest:
    def __init__(self):
        self.query_string = b"overview=full&geometries=geojson"
        self.args = {"use_new_router": "false"}

def route_path(origin, destination, trip_type):
    routable_types = {"train", "tram", "metro", "ferry", "aerialway", "bus", "car", "walk", "cycle"}
    if trip_type not in routable_types:
        return None
    
    routing_type_map = {"tram": "train", "metro": "train"}
    routing_type = routing_type_map.get(trip_type, trip_type)
    
    coords = f"{origin['lng']},{origin['lat']};{destination['lng']},{destination['lat']}"
    path = f"route/v1/{'driving' if routing_type in ('bus', 'car') else routing_type}/{coords}"
    
    try:
        result = forward_routing_core(routing_type, path, FakeRequest())
        if hasattr(result, 'get_json'):
            data = result.get_json()
        elif isinstance(result, str):
            data = json.loads(result)
        else:
            data = result
        
        if data and "routes" in data and data["routes"]:
            geometry = data["routes"][0].get("geometry", {})
            coords_list = geometry.get("coordinates", [])
            if coords_list:
                return [{"lat": c[1], "lng": c[0]} for c in coords_list]
    except Exception as e:
        logger.warning(f"Routing failed for {trip_type}: {e}")
    
    return None

def get_airport_by_iata(iata):
    from src.utils import mainConn, managed_cursor
    with managed_cursor(mainConn) as cursor:
        result = cursor.execute(
            "SELECT name, latitude, longitude, iso_country FROM airports WHERE iata = ?",
            (iata.upper(),)
        ).fetchone()
        if result:
            return dict(result)
    return None

STATION_EXPANSIONS = {
    r'\bHbf\b': 'Hauptbahnhof',
    r'\bBhf\b': 'Bahnhof',
    r'\bStn\b': 'Station',
    r'\bSt\.\s': 'Saint ',
    r'\bZOB\b': 'Zentraler Omnibusbahnhof',
    r'\bPl\.\b': 'Platz',
    r'\bStr\.\b': 'Strasse',
}

def normalize_station_name(name):
    result = name
    for pattern, replacement in STATION_EXPANSIONS.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result.strip()

def get_coords_from_past_trips(station_name, trip_type="train"):
    """Find coordinates from past trips matching station name using PG similarity."""
    try:
        with pg_session() as pg:
            result = pg.execute("""
                SELECT trip_id, origin_station, destination_station,
                       GREATEST(
                           similarity(origin_station, :name),
                           similarity(destination_station, :name)
                       ) as sim,
                       CASE 
                           WHEN similarity(origin_station, :name) >= similarity(destination_station, :name)
                           THEN 'origin'
                           ELSE 'destination'
                       END as matched_field
                FROM trips
                WHERE trip_type = :trip_type
                  AND (origin_station % :name OR destination_station % :name)
                  AND visibility = 'public'             
                ORDER BY sim DESC
                LIMIT 1
            """, {"name": station_name, "trip_type": trip_type}).fetchone()
        
        if not result:
            return None
        
        with managed_cursor(pathConn) as cursor:
            cursor.execute("SELECT path FROM paths WHERE trip_id = ?", (result.trip_id,))
            row = cursor.fetchone()
        
        if not row:
            return None
        
        path = json.loads(row["path"])
        if not path:
            return None
        
        point = path[0] if result.matched_field == 'origin' else path[-1]
        return {"lat": point[0], "lng": point[1]}
    except Exception as e:
        logger.debug(f"Past trips lookup failed: {e}")
        return None

def geocode_station(query, trip_type="train", fallback_coords=None, city_fallback=None):
    osm_tags = {
        "bus": ["amenity:bus_station", "highway:bus_stop"],
        "train": ["railway:halt", "railway:station"],
        "tram": ["railway:tram_stop", "railway:station", "railway:halt"],
        "metro": ["railway:station", "railway:subway_entrance"],
        "ferry": ["amenity:ferry_terminal"],
        "helicopter": ["aeroway:helipad", "aeroway:heliport", "aeroway:aerodrome"],
        "accommodation": ["tourism:alpine_hut", "tourism:apartment", "tourism:chalet", "tourism:guest_house", "tourism:hostel", "tourism:hotel", "tourism:motel", "tourism:wilderness_hut"],
        "restaurant": ["amenity:restaurant", "amenity:pub", "amenity:biergarten", "amenity:cafe", "amenity:bar"],
        "aerialway": ["aerialway:station"],
    }
    
    queries_to_try = [query]
    if city_fallback and city_fallback != query:
        queries_to_try.append(city_fallback)
    
    for q in queries_to_try:
        params = [("q", q), ("limit", 1), ("lang", "en")]
        for tag in osm_tags.get(trip_type, []):
            params.append(("osm_tag", tag))
        
        for url in ["https://photon.chiel.uk/api", "https://photon.komoot.io/api"]:
            try:
                resp = requests.get(url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                
                if data.get("features"):
                    feat = data["features"][0]
                    props = feat["properties"]
                    lng, lat = feat["geometry"]["coordinates"]
                    
                    # Validate against AI coords if provided
                    if fallback_coords:
                        dist = getDistance(
                            {"lat": lat, "lng": lng},
                            {"lat": fallback_coords[0], "lng": fallback_coords[1]}
                        )
                        if dist > 50:
                            logger.debug(f"Geocode result for '{q}' too far ({dist:.0f}km), skipping")
                            continue
                    
                    country_code = props.get("countrycode", "")
                    if not country_code or country_code in ["CN", "FI"]:
                        country = getCountryFromCoordinates(lat, lng)
                        country_code = country.get("countryCode", "")
                    
                    name = props.get("name", query)
                    city = props.get("city")
                    if city and city.lower() not in name.lower():
                        name = f"{city} - {name}"
                    
                    return {"name": name, "lat": lat, "lng": lng, "country_code": country_code}
            except Exception as e:
                logger.debug(f"Geocoding {url} failed: {e}")
    
    # Try past trips before AI fallback
    past_coords = get_coords_from_past_trips(query, trip_type)
    if past_coords:
        country = getCountryFromCoordinates(past_coords["lat"], past_coords["lng"])
        return {"name": query, "lat": past_coords["lat"], "lng": past_coords["lng"], 
                "country_code": country.get("countryCode", "")}
    
    if fallback_coords:
        lat, lng = fallback_coords
        country = getCountryFromCoordinates(lat, lng)
        return {"name": query, "lat": lat, "lng": lng, "country_code": country.get("countryCode", "")}
    
    return None

def extract_pdf_text(pdf_data):
    text = ""
    try:
        reader = PdfReader(BytesIO(pdf_data))
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
    return text

def parse_ics_content(ics_data):
    events = []
    try:
        cal = Calendar.from_ical(ics_data)
        for component in cal.walk():
            if component.name == "VEVENT":
                dtstart = component.get("dtstart")
                dtend = component.get("dtend")
                events.append({
                    "summary": str(component.get("summary", "")),
                    "description": str(component.get("description", "")),
                    "location": str(component.get("location", "")),
                    "dtstart": dtstart.dt if dtstart else None,
                    "dtend": dtend.dt if dtend else None,
                })
    except Exception as e:
        logger.error(f"ICS parsing error: {e}")
    return events

def parse_trip_with_ai(text, user_lang="en", images=None, ics_events=None, pdf_texts=None):
    config = load_config()
    api_key = config.get("infomaniak_ai", {}).get("api_key")
    if not api_key:
        logger.error("No AI API key found")
        return None
    
    lang_names = {"en": "English", "fr": "French", "de": "German", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "cs": "Czech", "ja": "Japanese", "zh": "Chinese", "ko": "Korean"}
    lang_name = lang_names.get(user_lang, "English")
    
    attachment_info = ""
    if ics_events:
        attachment_info += "\n\nICS CALENDAR DATA:\n"
        for i, evt in enumerate(ics_events, 1):
            attachment_info += f"Event {i}: {evt['summary']}\n  Location: {evt['location']}\n  Start: {evt['dtstart']}\n  End: {evt['dtend']}\n"
            if evt['description']:
                attachment_info += f"  Description: {evt['description'][:500]}\n"
    
    if pdf_texts:
        attachment_info += "\n\nPDF CONTENT:\n"
        for i, t in enumerate(pdf_texts, 1):
            attachment_info += f"--- PDF {i} ---\n{t[:3000]}\n"
    
    prompt = f"""Extract all trips from this text/image.
A trip is ONE segment (e.g., a flight with one connection = 2 trips).
Ignore walking trips that are between two public transit trips unless specified
When no date is given, default to today ({datetime.today().strftime('%Y-%m-%d')}), when date and time are given but no year, default to this year ({datetime.today().strftime('%Y')})
When only one price is given for a multi leg trip, default to dividing the price among each leg

Return ONLY valid JSON array, no markdown:
[{{
  "type": "train|air|bus|ferry|tram|metro|car|walk|cycle",
  "origin": "Station name as shown",
  "origin_city": "City name only for geocoding",
  "origin_iata": "ABC or null if not a flight",
  "origin_lat": latitude as number (REQUIRED - estimate if needed),
  "origin_lng": longitude as number (REQUIRED - estimate if needed),
  "destination": "Station name as shown", 
  "destination_city": "City name only for geocoding",
  "destination_iata": "XYZ or null if not a flight",
  "destination_lat": latitude as number (REQUIRED - estimate if needed),
  "destination_lng": longitude as number (REQUIRED - estimate if needed),
  "date": "YYYY-MM-DD departure date",
  "arrival_date": "YYYY-MM-DD or null if same day",
  "time_departure": "HH:MM or null",
  "time_arrival": "HH:MM or null",
  "operator": "Company name or null",
  "line_name": "Flight/train number or null",
  "price": number or null,
  "currency": "EUR|USD|etc or null",
  "aircraft_icao": "B738|A320 etc or null",
  "seat": "12A or null",
  "booking_reference": "PNR or null",
  "ticket_number": "Ticket number or null",
  "cabin_class": "Economy/Business/First or null",
  "notes": "Other info in {lang_name} or null"
}}]

IMPORTANT: Always provide origin_lat, origin_lng, destination_lat, destination_lng - use your knowledge to estimate coordinates for the city/station. Never return null for coordinates.

For multi-day trips (ferries, overnight trains), always provide arrival_date.
If no valid trip info, return []

Text: {text if text else "(see images)"}{attachment_info}"""

    # Build message content
    if images:
        content = []
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": f"data:{img['mime']};base64,{img['data']}"}})
        content.append({"type": "text", "text": prompt})
    else:
        content = prompt

    model = "qwen3" if images else "mistral3"

    try:
        response = requests.post(
            "https://api.infomaniak.com/2/ai/106774/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": content}]}
        )
        result = response.json()
        
        if "choices" not in result:
            logger.error(f"AI API error response: {result}")
            return None
        
        resp_content = result["choices"][0]["message"]["content"]
        resp_content = resp_content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(resp_content)
        
        # Normalize response to list
        if isinstance(parsed, dict):
            if parsed.get("origin") and parsed.get("destination"):
                parsed = [parsed]
            elif "trips" in parsed:
                parsed = parsed["trips"]
            elif "data" in parsed:
                parsed = parsed["data"]
            else:
                logger.error(f"AI returned unexpected dict structure: {parsed}")
                return None
        
        if not isinstance(parsed, list):
            logger.error(f"AI returned non-list: {type(parsed)} - {parsed}")
            return None
        
        valid_trips = [t for t in parsed if isinstance(t, dict) and t.get("origin") and t.get("destination")]
        
        if not valid_trips:
            logger.warning(f"No valid trips in AI response: {parsed}")
            return None
        
        return valid_trips
    except json.JSONDecodeError as e:
        logger.error(f"AI returned invalid JSON: {e} - {resp_content[:500]}")
        return None
    except Exception as e:
        logger.error(f"AI parsing error: {e}")
        return None

def build_notes(parsed_trip, user_lang="en", source="ai"):
    parts = [f"From Trainlog {source}"]
    if parsed_trip.get("booking_reference"):
        parts.append(f"PNR: {parsed_trip['booking_reference']}")
    if parsed_trip.get("ticket_number"):
        parts.append(f"Ticket: {parsed_trip['ticket_number']}")
    if parsed_trip.get("cabin_class"):
        parts.append(f"Class: {parsed_trip['cabin_class']}")
    if parsed_trip.get("notes"):
        parts.append(parsed_trip["notes"])
    return " | ".join(parts)

def enrich_parsed_trip(parsed_trip):
    """Geocode and route a parsed trip, adding resolved names and coordinates."""
    trip_type = parsed_trip.get("type", "train")
    
    if trip_type == "air":
        origin_iata = parsed_trip.get("origin_iata")
        dest_iata = parsed_trip.get("destination_iata")
        origin_airport = get_airport_by_iata(origin_iata) if origin_iata else None
        dest_airport = get_airport_by_iata(dest_iata) if dest_iata else None
        
        if not origin_airport or not dest_airport:
            logger.warning(f"Could not find airports: {origin_iata}, {dest_iata}")
            return None
        
        origin_flag = get_flag_emoji(origin_airport["iso_country"])
        dest_flag = get_flag_emoji(dest_airport["iso_country"])
        parsed_trip["_resolved_origin"] = f"{origin_flag} {origin_airport['name']} ({origin_iata.upper()})"
        parsed_trip["_resolved_destination"] = f"{dest_flag} {dest_airport['name']} ({dest_iata.upper()})"
        parsed_trip["_origin_coords"] = {"lat": origin_airport["latitude"], "lng": origin_airport["longitude"]}
        parsed_trip["_dest_coords"] = {"lat": dest_airport["latitude"], "lng": dest_airport["longitude"]}
        parsed_trip["_path"] = [parsed_trip["_origin_coords"], parsed_trip["_dest_coords"]]
    else:
        origin_fallback = (parsed_trip["origin_lat"], parsed_trip["origin_lng"]) if parsed_trip.get("origin_lat") and parsed_trip.get("origin_lng") else None
        dest_fallback = (parsed_trip["destination_lat"], parsed_trip["destination_lng"]) if parsed_trip.get("destination_lat") and parsed_trip.get("destination_lng") else None
        
        origin_geo = geocode_station(parsed_trip.get("origin", ""), trip_type, origin_fallback)
        dest_geo = geocode_station(parsed_trip.get("destination", ""), trip_type, dest_fallback)
        
        if not origin_geo or not dest_geo:
            logger.warning(f"Could not geocode: {parsed_trip.get('origin')}, {parsed_trip.get('destination')}")
            return None
        
        origin_flag = get_flag_emoji(origin_geo["country_code"])
        dest_flag = get_flag_emoji(dest_geo["country_code"])
        parsed_trip["_resolved_origin"] = f"{origin_flag} {origin_geo['name']}"
        parsed_trip["_resolved_destination"] = f"{dest_flag} {dest_geo['name']}"
        parsed_trip["_origin_coords"] = {"lat": origin_geo["lat"], "lng": origin_geo["lng"]}
        parsed_trip["_dest_coords"] = {"lat": dest_geo["lat"], "lng": dest_geo["lng"]}
        
        routed_path = route_path(parsed_trip["_origin_coords"], parsed_trip["_dest_coords"], trip_type)
        parsed_trip["_path"] = routed_path if routed_path else [parsed_trip["_origin_coords"], parsed_trip["_dest_coords"]]
    
    parsed_trip["_distance"] = getDistance(parsed_trip["_path"][0], parsed_trip["_path"][-1])
    return parsed_trip

def create_trip_from_parsed(user, parsed_trip, purchase_date=None, source="ai"):
    from timezonefinder import TimezoneFinder
    import pytz
    from src.utils import getLocalDatetime
    
    trip_type = parsed_trip.get("type", "train")
    now = datetime.now()

    if "_resolved_origin" not in parsed_trip:
        parsed_trip = enrich_parsed_trip(parsed_trip)
        if not parsed_trip:
            return None
    
    if trip_type == "air":
        origin_iata = parsed_trip.get("origin_iata")
        dest_iata = parsed_trip.get("destination_iata")
        origin_airport = get_airport_by_iata(origin_iata) if origin_iata else None
        dest_airport = get_airport_by_iata(dest_iata) if dest_iata else None
        
        if not origin_airport or not dest_airport:
            logger.error(f"Could not find airports: {origin_iata}, {dest_iata}")
            return None
        
        origin_flag = get_flag_emoji(origin_airport["iso_country"])
        dest_flag = get_flag_emoji(dest_airport["iso_country"])
        origin_station = f"{origin_flag} {origin_airport['name']} ({origin_iata.upper()})"
        dest_station = f"{dest_flag} {dest_airport['name']} ({dest_iata.upper()})"
        path = [{"lat": origin_airport["latitude"], "lng": origin_airport["longitude"]}, {"lat": dest_airport["latitude"], "lng": dest_airport["longitude"]}]
        
        trip_length = getDistance(path[0], path[-1])
        origin_country = getCountryFromCoordinates(path[0]["lat"], path[0]["lng"])
        dest_country = getCountryFromCoordinates(path[-1]["lat"], path[-1]["lng"])
        countries = json.dumps({origin_country["countryCode"]: trip_length / 2, dest_country["countryCode"]: trip_length / 2})
        material_type = parsed_trip.get("aircraft_icao")
    else:
        origin_fallback = (parsed_trip["origin_lat"], parsed_trip["origin_lng"]) if parsed_trip.get("origin_lat") and parsed_trip.get("origin_lng") else None
        dest_fallback = (parsed_trip["destination_lat"], parsed_trip["destination_lng"]) if parsed_trip.get("destination_lat") and parsed_trip.get("destination_lng") else None
        
        origin_geo = geocode_station(parsed_trip["origin"], trip_type, origin_fallback)
        dest_geo = geocode_station(parsed_trip["destination"], trip_type, dest_fallback)
        
        if not origin_geo or not dest_geo:
            logger.error(f"Could not geocode: {parsed_trip['origin']}, {parsed_trip['destination']}")
            return None
        
        origin_flag = get_flag_emoji(origin_geo["country_code"])
        dest_flag = get_flag_emoji(dest_geo["country_code"])
        origin_station = f"{origin_flag} {origin_geo['name']}"
        dest_station = f"{dest_flag} {dest_geo['name']}"
        origin_point = {"lat": origin_geo["lat"], "lng": origin_geo["lng"]}
        dest_point = {"lat": dest_geo["lat"], "lng": dest_geo["lng"]}
        
        routed_path = route_path(origin_point, dest_point, trip_type)
        path = routed_path if routed_path else [origin_point, dest_point]
        trip_length = getDistance(path[0], path[-1])
        countries = "{}"
        material_type = None
    
    tf = TimezoneFinder()
    start_datetime = end_datetime = utc_start_datetime = utc_end_datetime = estimated_duration = None
    
    utc_start = parsed_trip.get("utc_start_datetime")
    utc_end = parsed_trip.get("utc_end_datetime")
    
    if utc_start:
        utc_start_datetime = utc_start.replace(tzinfo=None) if hasattr(utc_start, 'replace') else utc_start
        start_datetime = getLocalDatetime(path[0]["lat"], path[0]["lng"], utc_start.replace(tzinfo=pytz.UTC) if utc_start.tzinfo is None else utc_start)
        if utc_end:
            utc_end_datetime = utc_end.replace(tzinfo=None) if hasattr(utc_end, 'replace') else utc_end
            end_datetime = getLocalDatetime(path[-1]["lat"], path[-1]["lng"], utc_end.replace(tzinfo=pytz.UTC) if utc_end.tzinfo is None else utc_end)
        else:
            utc_end_datetime, end_datetime = utc_start_datetime, start_datetime
    else:
        trip_date = parsed_trip.get("date")
        arrival_date = parsed_trip.get("arrival_date") or trip_date
        dep_time = parsed_trip.get("time_departure")
        arr_time = parsed_trip.get("time_arrival")
        
        if trip_date:
            if dep_time:
                start_datetime = datetime.strptime(f"{trip_date} {dep_time}", "%Y-%m-%d %H:%M")
                tz_name = tf.timezone_at(lat=path[0]["lat"], lng=path[0]["lng"])
                if tz_name:
                    local_start = pytz.timezone(tz_name).localize(start_datetime)
                    utc_start_datetime = local_start.astimezone(pytz.UTC).replace(tzinfo=None)
            else:
                start_datetime = datetime.strptime(f"{trip_date} 00:00:01", "%Y-%m-%d %H:%M:%S")
            
            if arr_time:
                end_datetime = datetime.strptime(f"{arrival_date} {arr_time}", "%Y-%m-%d %H:%M")
                tz_name = tf.timezone_at(lat=path[-1]["lat"], lng=path[-1]["lng"])
                if tz_name:
                    local_end = pytz.timezone(tz_name).localize(end_datetime)
                    utc_end_datetime = local_end.astimezone(pytz.UTC).replace(tzinfo=None)
            elif arrival_date != trip_date:
                end_datetime = datetime.strptime(f"{arrival_date} 23:59", "%Y-%m-%d %H:%M")
                tz_name = tf.timezone_at(lat=path[-1]["lat"], lng=path[-1]["lng"])
                if tz_name:
                    local_end = pytz.timezone(tz_name).localize(end_datetime)
                    utc_end_datetime = local_end.astimezone(pytz.UTC).replace(tzinfo=None)
            else:
                end_datetime, utc_end_datetime = start_datetime, utc_start_datetime
    
    if utc_start_datetime and utc_end_datetime:
        estimated_duration = int((utc_end_datetime - utc_start_datetime).total_seconds())
        if estimated_duration < 0:
            estimated_duration = None
    
    trip = Trip(
        username=user.username, user_id=user.uid, origin_station=origin_station, destination_station=dest_station,
        start_datetime=start_datetime, end_datetime=end_datetime, utc_start_datetime=utc_start_datetime, utc_end_datetime=utc_end_datetime,
        trip_length=trip_length, estimated_trip_duration=estimated_duration, manual_trip_duration=None,
        operator=parsed_trip.get("operator"), line_name=parsed_trip.get("line_name"), created=now, last_modified=now,
        type=trip_type, price=parsed_trip.get("price"), currency=parsed_trip.get("currency"), purchasing_date=purchase_date,
        ticket_id=None, is_project=False, path=path, countries=countries, seat=parsed_trip.get("seat"),
        material_type=material_type, reg=None, waypoints=None, notes=build_notes(parsed_trip, user.lang, source), visibility=get_default_trip_visibility(trip_type)
    )
    
    create_trip(trip)
    logger.info(f"Created trip for {user.username}")
    return trip