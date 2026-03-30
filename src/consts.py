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
