from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, Polygon
from shapely.prepared import prep

POLY_DIR = Path(__file__).resolve().parent.parent / "base_data" / "router_polys"


@lru_cache(maxsize=None)
def load_rings(name):
    """Outer rings of a Geofabrik-style .poly, as coordinate lists."""
    rings, current = [], None
    for line in (POLY_DIR / f"{name}.poly").read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            current.append((float(parts[0]), float(parts[1])))
        elif parts and parts[0] != "END":
            current = []
        elif current:
            rings.append(current)
            current = None
    return rings


@lru_cache(maxsize=None)
def load_region(name):
    prepared = [prep(Polygon(r)) for r in load_rings(name)]
    return lambda lng, lat: any(p.contains(Point(lng, lat)) for p in prepared)


def all_in_region(name, coord_pairs):
    contains = load_region(name)
    return all(contains(wp["lng"], wp["lat"]) for wp in coord_pairs)


def regions_geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": name},
                "geometry": {"type": "MultiPolygon", "coordinates": [[r] for r in load_rings(name)]},
            }
            for name in ("europe", "americas")
        ],
    }
