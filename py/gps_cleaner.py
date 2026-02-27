import json
import math

import polyline
from flask.wrappers import Response
from shapely.geometry import LineString, Point


def clean_gps_route(
    raw_waypoints,
    forwardRouting,
    trip_type="train",
    deviation_threshold=500,
    max_search_points=50,
):
    """
    A much faster version of the cleaning algorithm using an exponential/binary search
    to drastically reduce network calls.

    Args:
        raw_waypoints: List of raw GPS points [{'lat': y, 'lng': x}].
        forwardRouting: The function to call the routing engine.
        trip_type: Type of trip, e.g., "train", "car".
        deviation_threshold: Max distance (meters) a raw GPS point can be from a candidate route segment.
        max_search_points: DEPRECATED - No longer used in optimized version, kept for backward compatibility.
    """
    if len(raw_waypoints) < 2:
        return {"success": False, "error": "Need at least 2 waypoints"}

    total_points = len(raw_waypoints)
    print(f"Processing {total_points} GPS points with OPTIMIZED search algorithm...")
    router_type = get_router_type(trip_type)

    key_waypoints_coords = [[raw_waypoints[0]["lng"], raw_waypoints[0]["lat"]]]
    final_route_coords = []

    last_anchor_idx = 0
    segment_counter = 0

    while last_anchor_idx < total_points - 1:
        segment_counter += 1
        percent_complete = (last_anchor_idx / (total_points - 1)) * 100
        print(
            f"Processing segment {segment_counter} ({last_anchor_idx}/{total_points - 1}) [{percent_complete:.1f}% complete]"
        )

        start_point = [
            raw_waypoints[last_anchor_idx]["lng"],
            raw_waypoints[last_anchor_idx]["lat"],
        ]

        lower_bound_idx = last_anchor_idx
        upper_bound_idx = -1
        probe_step = 1

        while True:
            probe_idx = last_anchor_idx + probe_step
            if probe_idx >= total_points:
                upper_bound_idx = total_points - 1
                break

            probe_point = [
                raw_waypoints[probe_idx]["lng"],
                raw_waypoints[probe_idx]["lat"],
            ]
            segment_coords = get_route_via_forward_routing(
                forwardRouting,
                router_type,
                [start_point, probe_point],
                trip_type=trip_type,
            )
            intermediate_gps = [
                [wp["lng"], wp["lat"]]
                for wp in raw_waypoints[last_anchor_idx + 1 : probe_idx]
            ]

            if not segment_coords or not validate_segment(
                segment_coords, intermediate_gps, deviation_threshold
            ):
                upper_bound_idx = probe_idx
                break
            else:
                lower_bound_idx = probe_idx
                probe_step *= 2

        best_next_idx = lower_bound_idx
        while lower_bound_idx <= upper_bound_idx:
            mid_idx = (lower_bound_idx + upper_bound_idx) // 2
            if mid_idx <= last_anchor_idx:
                break

            candidate_point = [
                raw_waypoints[mid_idx]["lng"],
                raw_waypoints[mid_idx]["lat"],
            ]
            segment_coords = get_route_via_forward_routing(
                forwardRouting,
                router_type,
                [start_point, candidate_point],
                trip_type=trip_type,
            )
            intermediate_gps = [
                [wp["lng"], wp["lat"]]
                for wp in raw_waypoints[last_anchor_idx + 1 : mid_idx]
            ]

            if segment_coords and validate_segment(
                segment_coords, intermediate_gps, deviation_threshold
            ):
                best_next_idx = mid_idx
                lower_bound_idx = mid_idx + 1
            else:
                upper_bound_idx = mid_idx - 1

        if best_next_idx <= last_anchor_idx:
            print(
                f"[WARNING] Could not find a valid route segment from point {last_anchor_idx}. Skipping."
            )
            last_anchor_idx += 1
            continue

        final_segment_point = [
            raw_waypoints[best_next_idx]["lng"],
            raw_waypoints[best_next_idx]["lat"],
        ]
        final_segment_coords = get_route_via_forward_routing(
            forwardRouting,
            router_type,
            [start_point, final_segment_point],
            trip_type=trip_type,
        )

        final_route_coords.extend(final_segment_coords[:-1])
        key_waypoints_coords.append(final_segment_point)
        last_anchor_idx = best_next_idx

    final_route_coords.append(key_waypoints_coords[-1])

    route_distance = calculate_path_distance_coords(final_route_coords)
    final_path = [{"lat": coord[1], "lng": coord[0]} for coord in final_route_coords]
    key_waypoints = [{"lat": wp[1], "lng": wp[0]} for wp in key_waypoints_coords]

    print("✅ Route cleaning completed: 100%")
    return {
        "success": True,
        "waypoints": key_waypoints,
        "path": final_path,
        "distance": route_distance,
        "duration": 0,
        "reroute_count": len(key_waypoints) - 2,
        "compression_ratio": len(raw_waypoints) / len(final_path) if final_path else 0,
    }


def validate_segment(route_coords, intermediate_points, threshold):
    """
    Checks if all intermediate GPS points lie within a certain distance
    of the proposed route segment.
    """
    if not intermediate_points:
        return True

    line = LineString(route_coords)
    for p_coords in intermediate_points:
        p = Point(p_coords)
        # distance = p.distance(line)

        projected_point = line.interpolate(line.project(p))
        real_distance = haversine_distance(
            p_coords, [projected_point.x, projected_point.y]
        )

        if real_distance > threshold:
            return False
    return True


def get_router_type(trip_type):
    router_mapping = {
        "bus": "driving",
        "car": "driving",
        "train": "rail",
        "metro": "rail",
        "tram": "rail",
        "ferry": "rail",
        "aerialway": "rail",
        "walk": "foot",
        "cycle": "cycling",
    }
    return router_mapping.get(trip_type, "rail")


def get_route_via_forward_routing(
    forwardRouting, router_type, waypoints, return_details=False, trip_type="train"
):
    path_str = ";".join(f"{wp[0]},{wp[1]}" for wp in waypoints)
    options = "overview=full&continue_straight=false&snapping=any"

    radius = "1000" if router_type == "rail" else "150"
    radiuses = ";".join([radius] * len(waypoints))
    options += f"&radiuses={radiuses}"

    router_path = f"route/v1/{router_type}/{path_str}"
    response = forwardRouting(router_path, trip_type, options)

    if isinstance(response, Response):
        routing_result = json.loads(response.get_data(as_text=True))
    elif isinstance(response, str):
        routing_result = json.loads(response)
    else:
        raise TypeError("Unsupported response type")

    if routing_result["code"] != "Ok":
        return None if not return_details else (None, 0, 0)

    encoded_geometry = routing_result["routes"][0]["geometry"]
    decoded_coords = polyline.decode(encoded_geometry)
    route_coords = [[coord[1], coord[0]] for coord in decoded_coords]

    if return_details:
        route_distance = routing_result["routes"][0].get("distance", 0)
        route_duration = routing_result["routes"][0].get("duration", 0)
        return route_coords, route_distance, route_duration

    return route_coords


def haversine_distance(point1, point2):
    R = 6371000
    lat1, lon1 = math.radians(point1[1]), math.radians(point1[0])
    lat2, lon2 = math.radians(point2[1]), math.radians(point2[0])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_path_distance_coords(coords):
    if len(coords) < 2:
        return 0
    total_distance = 0
    for i in range(len(coords) - 1):
        total_distance += haversine_distance(coords[i], coords[i + 1])
    return total_distance
