"""A plan-trip: the parallel-world analog of a Trip. Carries the rendering subset
of trip fields, the timing dict from process_plan_dates, and the path. Never written
to the `trips` table — only to `plan_trips` (via create_plan_trip)."""

from src.carbon import calculate_carbon_footprint_for_trip
from src.trips.trip import _strip_tags


def _int_seconds(value):
    """Durations are whole seconds; the form may send a float string (e.g. '1715.3')."""
    if value in (None, ""):
        return None
    return int(round(float(value)))


class PlanTrip:
    def __init__(
        self,
        plan_id,
        user_id,
        origin_station,
        destination_station,
        trip_type,
        operator,
        line_name,
        material_type,
        material_type_advanced,
        reg,
        seat,
        notes,
        trip_length,
        estimated_trip_duration,
        countries,
        price,
        currency,
        waypoints,
        visibility,
        path,
        timing,
        purchase_date=None,
        power_type=None,
        co2_override=None,
        sort_order=0,
        created=None,
        last_modified=None,
    ):
        self.uid = None
        self.plan_id = plan_id
        self.user_id = user_id
        self.sort_order = sort_order
        self.origin_station = _strip_tags(origin_station)
        self.destination_station = _strip_tags(destination_station)
        self.trip_type = trip_type
        self.operator = _strip_tags(operator)
        self.line_name = _strip_tags(line_name)
        self.material_type = _strip_tags(material_type)
        self.material_type_advanced = material_type_advanced
        self.reg = _strip_tags(reg)
        self.seat = _strip_tags(seat)
        self.notes = _strip_tags(notes)
        self.trip_length = trip_length
        self.estimated_trip_duration = _int_seconds(estimated_trip_duration)
        self.countries = countries
        self.price = price
        self.currency = currency
        self.purchase_date = purchase_date
        self.power_type = power_type
        self.co2_override = co2_override
        self.waypoints = waypoints
        self.visibility = visibility
        self.path = path
        self.created = created
        self.last_modified = last_modified

        # timing: dict produced by process_plan_dates()
        self.timing_mode = timing["timing_mode"]
        self.start_day = timing["start_day"]
        self.end_day = timing["end_day"]
        self.start_time = timing["start_time"]
        self.end_time = timing["end_time"]
        self.start_datetime = timing["start_datetime"]
        self.end_datetime = timing["end_datetime"]
        self.utc_start_datetime = timing["utc_start_datetime"]
        self.utc_end_datetime = timing["utc_end_datetime"]
        self.manual_trip_duration = _int_seconds(timing["manual_trip_duration"])

        # carbon — best-effort (calculate_carbon_footprint_for_trip uses .get()).
        carbon_input = {
            "type": trip_type,
            "trip_length": trip_length or 0,
            "countries": countries,
            "start_datetime": self.start_datetime,
            "material_type": material_type,
            "power_type": power_type,
            "co2_override": co2_override,
        }
        self.carbon = (
            calculate_carbon_footprint_for_trip(carbon_input, path) if path else None
        )
