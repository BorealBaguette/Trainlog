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
