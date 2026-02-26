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
