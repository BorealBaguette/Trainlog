from enum import Enum


class Env(Enum):
    PROD = "production"
    DEV = "development"
    LOCAL = "local"


class DbNames(str, Enum):
    AUTH_DB = "databases/auth.db"
    PATH_DB = "databases/path.db"
    MAIN_DB = "databases/main.db"


class TripTypes(str, Enum):
    ACCOMODATION = "accomodation"
    AERIAL_WAY = "aerialway"
    AIR = "air"
    BUS = "bus"
    CAR = "car"
    CYCLE = "cycle"
    SCOOTER = "scooter"
    FERRY = "ferry"
    FUNICULAR = "funicular"
    HELICOPTER = "helicopter"
    METRO = "metro"
    OTHER = "other"
    POI = "poi"
    RAIL = "rail"
    RESTAURANT = "restaurant"
    SKI = "ski"
    TRAIN = "train"
    TRAM = "tram"
    WALK = "walk"

    @classmethod
    def can_transform(cls, origin_type, target_type) -> bool:
        """
        Check if a trip can be transformed from one type to another.
        Trip types within the same group can be transformed from one to the other.
        """
        groups = [
            (cls.ACCOMODATION, cls.POI, cls.RESTAURANT),
            (cls.AERIAL_WAY),
            (cls.AIR, cls.HELICOPTER),
            (cls.BUS, cls.CAR),
            (cls.CYCLE, cls.SCOOTER),
            (cls.FERRY,),
            (cls.METRO, cls.TRAIN, cls.TRAM, cls.FUNICULAR, cls.RAIL),
            (cls.SKI,),
            (cls.WALK,),
            (cls.OTHER,),
        ]
        for group in groups:
            if origin_type in group and target_type in group:
                return True
        return False

    @classmethod
    def from_str(cls, type_str: str):
        """
        Convert a string to a TripTypes enum member.
        """
        try:
            return cls[type_str.upper()]
        except KeyError:
            raise ValueError(f"Invalid trip type: {type_str}")


# The icon for each trip type. Lifted out of the inject_distinct_types context processor in
# app.py so the navbar, the stats menu and the admin panels all draw a bus with the same
# glyph — three copies of this list would drift the first time a type was added.
# Photon osm_tag filters per trip type. Without one Photon answers from the whole planet
# index, so a search for "Grenoble" returns the city boundary relation rather than its station.
# The trip form in templates/new.html sends these explicitly; anything that does not (the admin
# registry panel) gets them applied by search_stations().
#
# Personal modes are absent on purpose: their endpoints are addresses and houses, not tagged
# infrastructure, so a filter would return nothing.
STATION_OSM_TAGS = {
    "train": ("railway:halt", "railway:station"),
    "tram": ("railway:tram_stop", "railway:station", "railway:halt"),
    "metro": ("railway:station", "railway:subway_entrance"),
    "funicular": ("railway:halt", "railway:station"),
    "rail": ("railway:halt", "railway:station"),
    "bus": ("amenity:bus_station", "highway:bus_stop"),
    "ferry": ("amenity:ferry_terminal",),
    "helicopter": ("aeroway:helipad", "aeroway:heliport", "aeroway:aerodrome"),
    "aerialway": ("aerialway:station",),
    "ski": ("aerialway:station",),
}

TRIP_TYPE_ICONS = {
    "train": "fa-solid fa-train",
    "tram": "fa-solid fa-train-tram",
    "metro": "fa-solid fa-train-subway",
    "air": "fa-solid fa-plane-up",
    "bus": "fa-solid fa-bus",
    "ferry": "fa-solid fa-ship",
    "helicopter": "fa-solid fa-helicopter",
    "aerialway": "fa-solid fa-cable-car",
    "walk": "fa-solid fa-person-hiking",
    "cycle": "fa-solid fa-bicycle",
    "car": "fa-solid fa-car-side",
    "scooter": "bi bi-scooter",
    "funicular": "fa-solid fa-mountain",
    "rail": "fa-solid fa-dumbbell",
    "ski": "fa-solid fa-person-skiing",
    "accommodation": "fa-solid fa-bed",
    "poi": "fa-solid fa-map-location-dot",
    "restaurant": "fa-solid fa-utensils",
    "other": "fa-solid fa-circle-question",
}
