import logging

from flask import Blueprint, jsonify, render_template, request

from src.carbon import calculate_carbon_footprint_for_trip

logger = logging.getLogger(__name__)

carbon_blueprint = Blueprint("carbon", __name__)


@carbon_blueprint.route("/carbon-calculator")
@carbon_blueprint.route("/carbon-calculator/u/<username>")
def carbon_calculator(username=None):
    """Render the carbon calculator page"""
    return render_template("carbon.html", username=username)


@carbon_blueprint.route("/api/calculate-carbon", methods=["POST"])
def api_calculate_carbon():
    """
    Calculate carbon emissions for a trip without saving to database

    Expected JSON payload:
    {
        "trip": {
            "type": "train|bus|air|ferry|car|metro|tram|cycle|walk",
            "trip_length": distance_in_meters,
            "estimated_trip_duration": seconds,
            "countries": {}, // Optional country breakdown
            "material_type": "", // For aircraft type
            "passengers": 1, // For car trips
            "start_datetime": "2024-01-01", // Optional, for grid intensity
            "details": {} // Route details with country information
        },
        "path": [
            {"lat": latitude, "lng": longitude},
            ...
        ],
        "detect_countries": true // Optional flag to detect countries from path
    }
    """
    try:
        data = request.get_json()

        if not data or "trip" not in data:
            return jsonify({"error": "Invalid request format"}), 400

        trip = data["trip"]
        path = data.get("path", [])
        detect_countries = data.get("detect_countries", False)

        # Validate trip type
        valid_types = [
            "train",
            "bus",
            "air",
            "helicopter",
            "ferry",
            "car",
            "metro",
            "tram",
            "aerialway",
            "cycle",
            "walk",
        ]
        if trip.get("type") not in valid_types:
            return jsonify(
                {
                    "error": f"Invalid trip type. Must be one of: {', '.join(valid_types)}"
                }
            ), 400

        # Convert path to expected format (list of coordinate tuples)
        formatted_path = []
        for point in path:
            if isinstance(point, dict):
                formatted_path.append((point.get("lat", 0), point.get("lng", 0)))
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                formatted_path.append((point[0], point[1]))

        # If countries not provided and detection requested, detect them from path
        if detect_countries and (not trip.get("countries") or trip["countries"] == {}):
            # Import the function from your existing code
            from py.utils import getCountriesFromPath

            # Format path as expected by getCountriesFromPath
            path_dicts = [{"lat": lat, "lng": lng} for lat, lng in formatted_path]

            # Get countries based on path and details
            countries = getCountriesFromPath(
                path_dicts, trip.get("type"), trip.get("details", {})
            )
            trip["countries"] = countries

        # For air travel, if no countries specified, use origin/destination
        if trip.get("type") in ["air", "helicopter"] and not trip.get("countries"):
            if len(formatted_path) >= 2:
                from py.utils import getCountryFromCoordinates

                # Get origin and destination countries
                origin_country = getCountryFromCoordinates(
                    formatted_path[0][0], formatted_path[0][1]
                )
                dest_country = getCountryFromCoordinates(
                    formatted_path[-1][0], formatted_path[-1][1]
                )

                # Split distance equally between countries
                distance = trip.get("trip_length", 0)
                trip["countries"] = {
                    origin_country["countryCode"]: distance / 2,
                    dest_country["countryCode"]: distance / 2,
                }

        # Calculate carbon emissions with country information
        carbon_kg = calculate_carbon_footprint_for_trip(trip, formatted_path)

        # Prepare response with additional context
        response = {
            "carbon": round(carbon_kg, 6),
            "carbon_tons": round(carbon_kg / 1000, 6),
            "trip_type": trip.get("type"),
            "distance_km": round(trip.get("trip_length", 0) / 1000, 2),
            "duration_seconds": trip.get("estimated_trip_duration", 0),
            "countries": trip.get("countries", {}),  # Include countries in response
            "comparison": {
                "daily_average": round(
                    carbon_kg / 2.4, 1
                ),  # Average daily emissions ~2.4kg
                "trees_needed": round(carbon_kg / 21, 1),  # Tree absorbs ~21kg/year
                "car_km_equivalent": round(
                    carbon_kg / 0.192, 1
                ),  # Average car emissions
            },
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error calculating carbon: {str(e)}")
        return jsonify(
            {"error": "Failed to calculate carbon emissions", "details": str(e)}
        ), 500


@carbon_blueprint.route("/api/batch-calculate-carbon", methods=["POST"])
def api_batch_calculate_carbon():
    """
    Calculate carbon emissions for multiple trip segments

    Expected JSON payload:
    {
        "segments": [
            {
                "trip": {...},
                "path": [...]
            },
            ...
        ]
    }
    """
    try:
        data = request.get_json()

        if not data or "segments" not in data:
            return jsonify({"error": "Invalid request format"}), 400

        results = []
        total_carbon = 0
        total_distance = 0

        for segment in data["segments"]:
            trip = segment.get("trip", {})
            path = segment.get("path", [])

            # Format path
            formatted_path = []
            for point in path:
                if isinstance(point, dict):
                    formatted_path.append((point.get("lat", 0), point.get("lng", 0)))
                elif isinstance(point, (list, tuple)) and len(point) >= 2:
                    formatted_path.append((point[0], point[1]))

            # Calculate carbon
            carbon_kg = calculate_carbon_footprint_for_trip(trip, formatted_path)

            segment_result = {
                "carbon": round(carbon_kg, 6),
                "trip_type": trip.get("type"),
                "distance_km": round(trip.get("trip_length", 0) / 1000, 2),
            }

            results.append(segment_result)
            total_carbon += carbon_kg
            total_distance += trip.get("trip_length", 0)

        response = {
            "segments": results,
            "total_carbon": round(total_carbon, 6),
            "total_distance_km": round(total_distance / 1000, 2),
            "comparison": {
                "daily_average": round(total_carbon / 2.4, 1),
                "trees_needed": round(total_carbon / 21, 1),
                "car_km_equivalent": round(total_carbon / 0.192, 1),
            },
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in batch carbon calculation: {str(e)}")
        return jsonify({"error": "Failed to calculate carbon emissions"}), 500
