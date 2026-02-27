def convert_graphhopper_to_osrm(gh_response):
    """Convert GraphHopper response to OSRM format for compatibility"""
    if not gh_response.get("paths"):
        return {"code": "NoRoute", "message": "No route found"}

    path = gh_response["paths"][0]

    # Decode the main route polyline
    encoded_points = path.get("points", "")

    # Decode snapped waypoints (start and end points of the route)
    waypoints = []
    snapped_waypoints = path.get("snapped_waypoints", "")

    if snapped_waypoints:
        # Decode the snapped waypoints polyline
        snapped_coords = decode_polyline(snapped_waypoints, precision=5)

        # Create waypoints from snapped coordinates
        for idx, coord in enumerate(snapped_coords):
            waypoints.append(
                {
                    "name": "",
                    "location": [coord[1], coord[0]],  # [lng, lat]
                    "distance": 0,
                    "hint": "",
                    "waypoint_index": idx,
                }
            )
    elif encoded_points:
        # Fallback: use first and last coordinates from main route
        coordinates = decode_polyline(encoded_points, precision=5)
        if len(coordinates) >= 2:
            waypoints.append(
                {
                    "name": "",
                    "location": [coordinates[0][1], coordinates[0][0]],
                    "distance": 0,
                    "hint": "",
                    "waypoint_index": 0,
                }
            )
            waypoints.append(
                {
                    "name": "",
                    "location": [coordinates[-1][1], coordinates[-1][0]],
                    "distance": 0,
                    "hint": "",
                    "waypoint_index": 1,
                }
            )
    else:
        # Last fallback: use bbox if nothing else available
        bbox = path.get("bbox", [])
        if len(bbox) >= 4:
            waypoints.append(
                {
                    "name": "",
                    "location": [bbox[0], bbox[1]],
                    "distance": 0,
                    "hint": "",
                    "waypoint_index": 0,
                }
            )
            waypoints.append(
                {
                    "name": "",
                    "location": [bbox[2], bbox[3]],
                    "distance": 0,
                    "hint": "",
                    "waypoint_index": 1,
                }
            )

    # Create legs structure (required by Leaflet Routing Machine)
    legs = [
        {
            "distance": path.get("distance", 0),
            "duration": path.get("time", 0) / 1000,  # Convert ms to seconds
            "summary": "",
            "steps": [],  # Empty steps array
            "weight": path.get("weight", 0),
            "weight_name": "routability",
            "annotation": {
                "distance": [path.get("distance", 0)],
                "duration": [path.get("time", 0) / 1000],
            },
        }
    ]

    # Build OSRM-compatible response
    osrm_response = {
        "code": "Ok",
        "routes": [
            {
                "geometry": encoded_points,  # Keep the encoded polyline
                "distance": path.get("distance", 0),
                "duration": path.get("time", 0) / 1000,  # Convert ms to seconds
                "weight": path.get("weight", 0),
                "weight_name": "routability",
                "legs": legs,
                "details": path.get("details", 0),
            }
        ],
        "waypoints": waypoints,
    }

    return osrm_response


def decode_polyline(encoded, precision=5):
    """Decode a polyline string into a list of lat/lng tuples."""
    coordinates = []
    index = 0
    lat = 0
    lng = 0
    factor = 10**precision

    while index < len(encoded):
        # Latitude
        shift = 0
        result = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lat += (~result >> 1) if (result & 1) else (result >> 1)

        # Longitude
        shift = 0
        result = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        lng += (~result >> 1) if (result & 1) else (result >> 1)

        coordinates.append((lat / factor, lng / factor))

    return coordinates
