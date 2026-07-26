import json


def coords_to_ewkt(coords):
    """Convert a [[lat, lng], ...] coordinate list to EWKT (SRID 4326) for the
    PostGIS `paths.geom` column. PostGIS uses (lng lat) ordering. Returns None for
    an empty list (such a row should be skipped)."""
    if not coords:
        return None
    points = ", ".join(f"{c[1]} {c[0]}" for c in coords)
    if len(coords) == 1:
        return f"SRID=4326;POINT({points})"
    return f"SRID=4326;LINESTRING({points})"


def fetch_paths_map(pg, trip_ids):
    """Return {trip_id: [[lat, lng], ...]} for the given trip_ids from PostGIS."""
    if not trip_ids:
        return {}
    rows = pg.execute(
        "SELECT trip_id, ST_AsGeoJSON(geom) AS geojson FROM paths WHERE trip_id = ANY(:ids)",
        {"ids": [int(t) for t in trip_ids]},
    ).fetchall()
    return {row["trip_id"]: geom_geojson_to_coords(row["geojson"]) for row in rows}


def fetch_path(pg, trip_id):
    """Return [[lat, lng], ...] for a single trip_id (empty list if no path)."""
    row = pg.execute(
        "SELECT ST_AsGeoJSON(geom) AS geojson FROM paths WHERE trip_id = :trip_id",
        {"trip_id": trip_id},
    ).fetchone()
    return geom_geojson_to_coords(row["geojson"]) if row else []


def geom_geojson_to_coords(geojson):
    """Convert ST_AsGeoJSON(geom) output back to the app's [[lat, lng], ...] format
    (swapping PostGIS lng/lat order). Accepts the GeoJSON string or parsed dict."""
    if geojson is None:
        return []
    data = json.loads(geojson) if isinstance(geojson, str) else geojson
    coordinates = data.get("coordinates", [])
    if data.get("type") == "Point":
        return [[coordinates[1], coordinates[0]]]
    return [[c[1], c[0]] for c in coordinates]


class Node:
    def __init__(self, trip_id, node_order, lat, lng):
        self.trip_id = trip_id
        self.node_order = node_order
        self.lat = lat
        self.lng = lng

    def keys(self):
        return tuple(vars(self).keys())

    def values(self):
        return tuple(vars(self).values())

    def to_dict(self, *, include_trip_id: bool = False):
        d = {
            "node_order": self.node_order,
            "lat": self.lat,
            "lng": self.lng,
        }
        if include_trip_id:
            d["trip_id"] = self.trip_id
        return d


class Path:
    def __init__(self, path, trip_id):
        self.list = []
        for node_order, node in enumerate(path):
            new_node = Node(
                trip_id=trip_id, node_order=node_order, lat=node["lat"], lng=node["lng"]
            )
            self.list.append(new_node)

    def keys(self):
        return ("trip_id", "path")

    def values(self):
        return [self.list[0].trip_id, str([[node.lat, node.lng] for node in self.list])]

    def __len__(self):
        return len(self.list)

    def set_trip_id(self, trip_id):
        for node in self.list:
            node.trip_id = trip_id

    def to_dict(
        self, *, include_trip_id: bool = True, include_node_order: bool = False
    ):
        """
        JSON-ready dict.
        - include_trip_id=True -> {"trip_id":..., "path":[...]}
        - include_node_order controls whether each node has node_order
        """
        trip_id = self.list[0].trip_id if self.list else None
        nodes = []
        for n in self.list:
            if include_node_order:
                nodes.append({"lat": n.lat, "lng": n.lng, "node_order": n.node_order})
            else:
                nodes.append({"lat": n.lat, "lng": n.lng})

        if include_trip_id:
            return {"trip_id": trip_id, "path": nodes}
        return {"path": nodes}

    def to_json(self, **json_kwargs):
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, **json_kwargs)
