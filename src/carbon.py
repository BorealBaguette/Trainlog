import json, os, math
import pandas as pd
from geopy.distance import geodesic
from datetime import datetime

def load_aircraft_emissions():
    filepath = os.path.join("base_data/carbon", "aircraft_emissions.json")
    with open(filepath, "r") as f:
        src = json.load(f)
    fc = {k: dict(v) for k, v in src.get("flight_categories", {}).items()}
    ac = {k: {kk: float(vv) for kk, vv in v.items()} for k, v in src.get("aircraft", {}).items()}
    return fc, ac

def load_train_emissions():
    filepath = os.path.join("base_data/carbon", "train_emissions.json")
    with open(filepath, "r") as f:
        return json.load(f)

def load_grid_intensity_df():
    filepath = os.path.join("base_data/carbon", "carbon_intensity_by_year_country.json")
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    df = pd.DataFrame.from_dict(data, orient="index")  # rows: Year, cols: alpha2
    df.index = df.index.astype(int)
    df = df.sort_index()
    return df

def get_year_from_datetime(start_datetime):
    """Extract year from start_datetime string, handling special cases"""
    if start_datetime == -1:
        return 2020  # Default for "unknown past"
    elif start_datetime == 1:
        return 2024  # Default for "unknown future"
    elif isinstance(start_datetime, str):
        try:
            # Parse yyyy-mm-dd format
            return int(start_datetime.split('-')[0])
        except (ValueError, IndexError):
            return 2024  # Default if parsing fails
    else:
        return 2024  # Default fallback

def get_grid_intensity_for_country_year(country_code, year):
    """Get grid intensity for a specific country and year"""
    global GRID_INTENSITY_DF
    
    min_year = GRID_INTENSITY_DF.index.min()
    max_year = GRID_INTENSITY_DF.index.max()
    
    # Clamp year to available range
    if year > max_year:
        year = max_year  # Use last present year for future dates
    elif year < min_year:
        year = min_year  # Use earliest available year for past dates
    
    # Get intensity for the year and country
    if country_code in GRID_INTENSITY_DF.columns:
        intensity = GRID_INTENSITY_DF.loc[year, country_code]
        if pd.notna(intensity):
            return float(intensity)
    
    # Fallback to 445g default if country not present
    return 445.0

def get_weighted_grid_intensity(countries, start_datetime=None):
    """Distance-weighted average grid intensity (g CO2/kWh) across countries."""
    year = get_year_from_datetime(start_datetime) if start_datetime is not None else 2024
    if not countries:
        return get_grid_intensity_for_country_year('default', year)
    if isinstance(countries, str):
        try:
            countries = json.loads(countries)
        except Exception:
            return get_grid_intensity_for_country_year('default', year)
    total_m = 0.0
    weighted_sum = 0.0
    for cc, value in countries.items():
        dist_m = (value.get('elec', 0) + value.get('nonelec', 0)) if isinstance(value, dict) else (value or 0)
        weighted_sum += dist_m * get_grid_intensity_for_country_year(cc, year)
        total_m += dist_m
    if total_m == 0:
        return get_grid_intensity_for_country_year('default', year)
    return weighted_sum / total_m

GRID_INTENSITY_DF = load_grid_intensity_df()
TRAIN_FACTORS = load_train_emissions()
FLIGHT_CATEGORIES, AIRCRAFT_CATEGORY_CO2 = load_aircraft_emissions()

# Constants for train energy consumption and emissions
ELECTRIC_TRAIN_KWH_PER_KM = 0.04    # kWh per passenger-km for electric trains
DIESEL_TRAIN_LITERS_PER_KM = 0.015  # liters per passenger-km for diesel trains
DIESEL_CO2_KG_PER_LITER = 2.65      # kg CO2 per liter of diesel

# Constants for other electric vehicles
ELECTRIC_CAR_KWH_PER_KM = 0.20     # kWh per km for electric car (vehicle level, 1 passenger)
ELECTRIC_BUS_KWH_PER_KM = 0.043    # kWh per passenger-km for electric bus (~1.3 kWh/km / 30 pax)
ELECTRIC_CYCLE_KWH_PER_KM = 0.010  # kWh per km for e-bike
ELECTRIC_SCOOTER_KWH_PER_KM = 0.025  # kWh per km for electric kick scooter

EMISSION_FACTORS = {
    'bus': {'construction': 4.42, 'fuel': 25.0, 'infrastructure': 0.7},
    'train': {'construction': 1.5, 'infrastructure': 6.5},
    'car': {'construction': 25.6, 'fuel': 192.0, 'infrastructure': 0.7, 'additional_passenger_factor': 0.04},
    'air': {
        'short': {'base_co2_per_km': 0.140},
        'medium': {'base_co2_per_km': 0.110},
        'long': {'base_co2_per_km': 0.095},
        'non_co2_factor': 1.7,
        'detour_factor': 1.076,
    },
    'ferry': {'combustion': 80.0, 'services': 30.0, 'construction': 11.0},
    'cycle': {'construction': 2.0, 'human_fuel': 0.1},
    'electric_cycle': {'construction': 6.0, 'infrastructure': 0.0},  # ~500 Wh battery / 15,000 km lifetime
    'scooter': {'construction': 1.0, 'human_fuel': 0.05},
    'electric_scooter': {'construction': 5.0, 'infrastructure': 0.0},  # small battery, short lifetime
    'electric_car': {'construction': 60.0, 'infrastructure': 0.7},  # battery ~100 kg CO₂/kWh over 200,000 km
    'electric_bus': {'construction': 9.0, 'infrastructure': 0.7},   # ~300 kWh battery / 500,000 km / 30 pax
    'walk': {'human_fuel': 0.2},
    'metro': {'construction': 0.8, 'infrastructure': 3.5},
    'tram': {'construction': 1.0, 'infrastructure': 4.0},
    'funicular': {'construction': 1.0, 'infrastructure': 4.0},
    'rail': {'construction': 1.5, 'infrastructure': 6.5},
    'aerialway': {'construction': 1.0, 'infrastructure': 4.0}
}

def calculate_great_circle_distance(p1, p2):
    return geodesic(p1, p2).meters

def calculate_path_distance(path):
    if len(path) < 2: return 0
    return sum(calculate_great_circle_distance(path[i], path[i+1]) for i in range(len(path)-1))

def get_flight_category(distance_km, categories=FLIGHT_CATEGORIES):
    cand = []
    for name, b in categories.items():
        dmin = b.get("distance_km_min", float("-inf"))
        dmax = b.get("distance_km_max", float("inf"))
        if dmin <= distance_km < dmax:
            span = (dmax - dmin) if math.isfinite(dmax) and math.isfinite(dmin) else float("inf")
            cand.append((span, dmin, name))
    return min(cand)[2] if cand else None

def get_aircraft_co2_value(aircraft_code, distance_km):
    per_cat = AIRCRAFT_CATEGORY_CO2.get(aircraft_code)
    if not per_cat: return None
    cat = get_flight_category(distance_km)
    return per_cat.get(cat) if cat and cat in per_cat else per_cat.get("all")

def calculate_air_emissions(distance_km, path_points, aircraft_code=''):
    f = EMISSION_FACTORS['air']
    v = get_aircraft_co2_value(aircraft_code, distance_km)
    if v is not None:
        return distance_km * v * f['non_co2_factor']
    cat = 'short' if distance_km < 1000 else ('medium' if distance_km < 3500 else 'long')
    return distance_km * f[cat]['base_co2_per_km'] * f['non_co2_factor']

def split_km_for_country(cc, value_m):
    """Split distance into electric and diesel kilometers based on country's diesel share"""
    if isinstance(value_m, dict):
        # If already split, convert from meters to km
        e_km = (value_m.get('electric_m', 0) or 0) / 1000
        d_km = (value_m.get('diesel_m', 0) or 0) / 1000
        return e_km, d_km
    
    # Convert total meters to km
    total_km = (value_m or 0) / 1000
    
    # Get diesel share for this country (default if not found)
    diesel_share = TRAIN_FACTORS.get(cc, TRAIN_FACTORS['default'])['diesel_share']
    
    # Calculate diesel and electric km
    diesel_km = total_km * diesel_share
    electric_km = total_km - diesel_km
    
    return electric_km, diesel_km

def calculate_rail_emissions(distance_km, countries, rail_type='train', start_datetime=None, force_electric=False):
    """
    Calculate rail transport CO2 emissions in kg CO2e (train/metro/tram/aerialway)
   
    Args:
        distance_km: Total distance (not used when countries dict provided)
        countries: Dict of country codes and distances/breakdown, or JSON string
                  Old format: {"FR": 100} (distance in km)
                  New format: {"FR": {"elec": 773939.89, "nonelec": 62488.40}} (distances in meters)
        rail_type: Type of rail transport ('train', 'metro', 'tram', 'aerialway')
        start_datetime: Trip date (yyyy-mm-dd string, -1, or 1)
        force_electric: If True, treat all trains as electric
    
    Returns:
        float: Total CO2 emissions in kg CO2e
    """
    # Get rail base emissions (construction + infrastructure) based on type
    rail_base = EMISSION_FACTORS.get(rail_type, EMISSION_FACTORS['train'])
    base_emissions_g_per_km = rail_base['construction'] + rail_base['infrastructure']
    
    # Extract year from start_datetime
    year = get_year_from_datetime(start_datetime)
    
    # Handle case where no countries specified - use default values
    if not countries:
        factors = TRAIN_FACTORS['default']
        electric_km = distance_km
        diesel_km = 0 if force_electric else distance_km * factors['diesel_share']
        electric_km = distance_km - diesel_km if not force_electric else distance_km
        
        # Use global average grid intensity for the year
        grid_intensity_g_per_kwh = get_grid_intensity_for_country_year('default', year)
        
        # Calculate operational emissions (grid electricity + diesel fuel)
        electric_emissions = electric_km * ELECTRIC_TRAIN_KWH_PER_KM * grid_intensity_g_per_kwh / 1000
        diesel_emissions = diesel_km * DIESEL_TRAIN_LITERS_PER_KM * DIESEL_CO2_KG_PER_LITER
        
        # Add base emissions (construction + infrastructure)
        base_emissions = distance_km * base_emissions_g_per_km / 1000
        
        return electric_emissions + diesel_emissions + base_emissions
    
    # Parse countries if it's a JSON string
    if isinstance(countries, str):
        try:
            countries = json.loads(countries)
        except:
            countries = {}
    
    total_emissions = 0.0
    
    # Process each country
    for country_code, distance_value in countries.items():
        # Determine if this is old format (number) or new format (dict)
        if isinstance(distance_value, (int, float)):
            # Old format: {"FR": 200}
            # Get country-specific diesel share
            factors = TRAIN_FACTORS.get(country_code, TRAIN_FACTORS['default'])
            
            # Split into electric and diesel kilometers using existing logic
            electric_km, diesel_km = split_km_for_country(country_code, distance_value)
            
        elif isinstance(distance_value, dict):
            # New format: {"FR": {"elec": 120, "nonelec": 80}} 
            electric_km = distance_value.get('elec', 0) / 1000
            diesel_km = distance_value.get('nonelec', 0) / 1000
            
        else:
            # Fallback - treat as old format
            factors = TRAIN_FACTORS.get(country_code, TRAIN_FACTORS['default'])
            electric_km, diesel_km = split_km_for_country(country_code, distance_value)
       
        # Get country-specific grid intensity for the year
        grid_intensity_g_per_kwh = get_grid_intensity_for_country_year(country_code, year)
       
        # Force all to electric if requested
        if force_electric:
            electric_km += diesel_km
            diesel_km = 0
        
        total_km = electric_km + diesel_km
        
        # Calculate operational emissions (grid electricity + diesel fuel)
        electric_emissions = electric_km * ELECTRIC_TRAIN_KWH_PER_KM * grid_intensity_g_per_kwh / 1000
        diesel_emissions = diesel_km * DIESEL_TRAIN_LITERS_PER_KM * DIESEL_CO2_KG_PER_LITER
        
        # Add base emissions (construction + infrastructure) for this segment
        base_emissions = total_km * base_emissions_g_per_km / 1000
        
        total_emissions += electric_emissions + diesel_emissions + base_emissions
    return total_emissions

def calculate_bus_emissions(distance_km):
    g = EMISSION_FACTORS['bus']
    return distance_km * (g['construction'] + g['fuel'] + g['infrastructure']) / 1000

def calculate_car_emissions(distance_km, passengers=1):
    g = EMISSION_FACTORS['car']
    total = distance_km * (g['construction'] + g['fuel'] + g['infrastructure'])
    if passengers > 1:
        total += distance_km * g['fuel'] * g['additional_passenger_factor'] * (passengers - 1)
    return (total / passengers) / 1000

FERRY_COMBUSTION_FACTORS = {
    'thermic': 80.0,  # g/passenger-km, diesel/HFO average
    'electric':  0.0, # direct combustion = 0; grid electricity handled separately
}
ELECTRIC_FERRY_KWH_PER_KM = 0.1  # kWh per passenger-km for electric ferry

def calculate_ferry_emissions(distance_km, power_type='thermic', co2_override=None, countries=None, start_datetime=None):
    g = EMISSION_FACTORS['ferry']
    base = (g['services'] + g['construction'])  # fixed components in g/km
    if co2_override is not None:
        # User-supplied full figure (g/passenger-km) replaces combustion+services+construction
        return distance_km * co2_override / 1000
    if power_type == 'electric':
        grid_intensity = get_weighted_grid_intensity(countries, start_datetime)
        electricity = distance_km * ELECTRIC_FERRY_KWH_PER_KM * grid_intensity
        return (electricity + distance_km * base) / 1000
    combustion = FERRY_COMBUSTION_FACTORS.get(power_type, FERRY_COMBUSTION_FACTORS['thermic'])
    return distance_km * (combustion + base) / 1000

def calculate_cycle_emissions(distance_km):
    g = EMISSION_FACTORS['cycle']
    return distance_km * (g['construction'] + g['human_fuel']) / 1000

def calculate_walk_emissions(distance_km):
    g = EMISSION_FACTORS['walk']
    return distance_km * g['human_fuel'] / 1000

def calculate_scooter_emissions(distance_km, power_type='manual', countries=None, start_datetime=None):
    if power_type == 'electric':
        g = EMISSION_FACTORS['electric_scooter']
        grid_intensity = get_weighted_grid_intensity(countries, start_datetime)
        electricity = distance_km * ELECTRIC_SCOOTER_KWH_PER_KM * grid_intensity
        return (electricity + distance_km * (g['construction'] + g['infrastructure'])) / 1000
    g = EMISSION_FACTORS['scooter']
    return distance_km * (g['construction'] + g['human_fuel']) / 1000

def calculate_electric_cycle_emissions(distance_km, countries=None, start_datetime=None):
    g = EMISSION_FACTORS['electric_cycle']
    grid_intensity = get_weighted_grid_intensity(countries, start_datetime)
    electricity = distance_km * ELECTRIC_CYCLE_KWH_PER_KM * grid_intensity
    return (electricity + distance_km * g['construction']) / 1000

def calculate_electric_bus_emissions(distance_km, countries=None, start_datetime=None):
    g = EMISSION_FACTORS['electric_bus']
    grid_intensity = get_weighted_grid_intensity(countries, start_datetime)
    electricity = distance_km * ELECTRIC_BUS_KWH_PER_KM * grid_intensity
    return (electricity + distance_km * (g['construction'] + g['infrastructure'])) / 1000

def calculate_electric_car_emissions(distance_km, passengers=1, countries=None, start_datetime=None):
    g = EMISSION_FACTORS['electric_car']
    grid_intensity = get_weighted_grid_intensity(countries, start_datetime)
    electricity = distance_km * ELECTRIC_CAR_KWH_PER_KM * grid_intensity
    total = electricity + distance_km * (g['construction'] + g['infrastructure'])
    return (total / passengers) / 1000

def calculate_carbon_footprint_for_trip(trip, path):
    t = trip.get('type', '').lower()
    if t == 'helicopter': t = 'air'
    distance_km = float(trip.get("trip_length", 0))/1000
    if distance_km == 0: return 0
    power_type = trip.get('power_type') or trip.get('powerType') or ''
    countries = trip.get('countries')
    start_datetime = trip.get('start_datetime')

    if t == 'air':
        return calculate_air_emissions(distance_km, len(path), trip.get('material_type',''))
    if t == 'train':
        force_electric = (power_type == 'electric')
        return calculate_rail_emissions(distance_km, countries, 'train', start_datetime, force_electric=force_electric)
    if t in ('metro', 'tram', 'aerialway', 'funicular'):
        return calculate_rail_emissions(distance_km, countries, t, start_datetime, force_electric=True)
    if t == 'rail':
        force_electric = (power_type == 'electric')
        return calculate_rail_emissions(distance_km, countries, 'rail', start_datetime, force_electric=force_electric)
    if t == 'bus':
        if power_type == 'electric':
            return calculate_electric_bus_emissions(distance_km, countries, start_datetime)
        return calculate_bus_emissions(distance_km)
    if t == 'car':
        if power_type == 'electric':
            return calculate_electric_car_emissions(distance_km, trip.get('passengers', 1), countries, start_datetime)
        return calculate_car_emissions(distance_km, trip.get('passengers', 1))
    if t == 'ferry':
        return calculate_ferry_emissions(distance_km, power_type or 'thermic', trip.get('co2_override'), countries, start_datetime)
    if t == 'cycle':
        if power_type == 'electric':
            return calculate_electric_cycle_emissions(distance_km, countries, start_datetime)
        return calculate_cycle_emissions(distance_km)
    if t == 'scooter':
        return calculate_scooter_emissions(distance_km, power_type or 'manual', countries, start_datetime)
    if t in ('walk', 'ski'):
        return calculate_walk_emissions(distance_km)
    # other, poi, accommodation, restaurant, etc. → no meaningful carbon figure
    return 0