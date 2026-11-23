-- A plan leg can be one of the user's real trips, linked rather than copied: the fixed
-- part of a plan (a booked flight) that the possibilities around it are built on. The
-- leg row only places it in the plan (plan_id, sort_order); everything else is read
-- from the trip, live, through plan_trips_resolved. Deleting the trip removes the leg;
-- removing the leg leaves the trip alone. A trip is in a given plan at most once.
ALTER TABLE plan_trips ADD COLUMN IF NOT EXISTS linked_trip_id INTEGER
    REFERENCES trips(trip_id) ON DELETE CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS plan_trips_plan_linked_trip
    ON plan_trips (plan_id, linked_trip_id) WHERE linked_trip_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS plan_trips_linked_trip ON plan_trips (linked_trip_id)
    WHERE linked_trip_id IS NOT NULL;

-- plan_trips with each linked leg's columns taken from its trip (and path). Readers of
-- a plan's legs read this; writers still write plan_trips. A linked leg is never
-- relative (the trip has real dates), always "booked", on no shared cost, never logged.
CREATE OR REPLACE VIEW plan_trips_resolved AS
SELECT pt.uid, pt.plan_id, pt.user_id, pt.sort_order,
       CASE WHEN t.trip_id IS NULL THEN pt.timing_mode
            WHEN t.start_datetime IS NULL OR t.is_project THEN 'unknown'
            WHEN EXTRACT(SECOND FROM t.start_datetime) = 1 THEN 'onlyDate'
            ELSE 'precise' END AS timing_mode,
       CASE WHEN t.trip_id IS NULL THEN pt.start_day END AS start_day,
       CASE WHEN t.trip_id IS NULL THEN pt.end_day END AS end_day,
       CASE WHEN t.trip_id IS NULL THEN pt.start_time END AS start_time,
       CASE WHEN t.trip_id IS NULL THEN pt.end_time END AS end_time,
       CASE WHEN t.trip_id IS NULL THEN pt.start_datetime
            WHEN t.is_project THEN NULL ELSE t.start_datetime END AS start_datetime,
       CASE WHEN t.trip_id IS NULL THEN pt.end_datetime
            WHEN t.is_project THEN NULL ELSE t.end_datetime END AS end_datetime,
       CASE WHEN t.trip_id IS NULL THEN pt.utc_start_datetime
            WHEN t.is_project THEN NULL ELSE t.utc_start_datetime END AS utc_start_datetime,
       CASE WHEN t.trip_id IS NULL THEN pt.utc_end_datetime
            WHEN t.is_project THEN NULL ELSE t.utc_end_datetime END AS utc_end_datetime,
       CASE WHEN t.trip_id IS NULL THEN pt.estimated_trip_duration
            ELSE CAST(ROUND(t.estimated_trip_duration) AS integer) END AS estimated_trip_duration,
       CASE WHEN t.trip_id IS NULL THEN pt.manual_trip_duration
            ELSE CAST(ROUND(t.manual_trip_duration) AS integer) END AS manual_trip_duration,
       CASE WHEN t.trip_id IS NULL THEN pt.origin_station ELSE t.origin_station END AS origin_station,
       CASE WHEN t.trip_id IS NULL THEN pt.destination_station ELSE t.destination_station END AS destination_station,
       CASE WHEN t.trip_id IS NULL THEN pt.trip_type ELSE t.trip_type END AS trip_type,
       CASE WHEN t.trip_id IS NULL THEN pt.operator ELSE t.operator END AS operator,
       CASE WHEN t.trip_id IS NULL THEN pt.line_name ELSE t.line_name END AS line_name,
       CASE WHEN t.trip_id IS NULL THEN pt.material_type ELSE t.material_type END AS material_type,
       CASE WHEN t.trip_id IS NULL THEN pt.material_type_advanced ELSE t.material_type_advanced END AS material_type_advanced,
       CASE WHEN t.trip_id IS NULL THEN pt.reg ELSE t.reg END AS reg,
       CASE WHEN t.trip_id IS NULL THEN pt.seat ELSE t.seat END AS seat,
       CASE WHEN t.trip_id IS NULL THEN pt.notes ELSE t.notes END AS notes,
       CASE WHEN t.trip_id IS NULL THEN pt.trip_length ELSE t.trip_length END AS trip_length,
       CASE WHEN t.trip_id IS NULL THEN pt.countries ELSE t.countries END AS countries,
       CASE WHEN t.trip_id IS NULL THEN pt.price ELSE t.price END AS price,
       CASE WHEN t.trip_id IS NULL THEN pt.currency ELSE t.currency END AS currency,
       CASE WHEN t.trip_id IS NULL THEN pt.purchase_date ELSE t.purchase_date END AS purchase_date,
       CASE WHEN t.trip_id IS NULL THEN pt.waypoints ELSE t.waypoints END AS waypoints,
       CASE WHEN t.trip_id IS NULL THEN pt.visibility ELSE t.visibility END AS visibility,
       CASE WHEN t.trip_id IS NULL THEN pt.carbon ELSE t.carbon END AS carbon,
       CASE WHEN t.trip_id IS NULL THEN pt.power_type ELSE t.power_type END AS power_type,
       CASE WHEN t.trip_id IS NULL THEN pt.co2_override ELSE t.co2_override END AS co2_override,
       CASE WHEN t.trip_id IS NULL THEN pt.geom ELSE p.geom END AS geom,
       pt.created, pt.last_modified,
       CASE WHEN t.trip_id IS NULL THEN pt.cost_id END AS cost_id,
       CASE WHEN t.trip_id IS NULL THEN pt.booked ELSE TRUE END AS booked,
       CASE WHEN t.trip_id IS NULL THEN pt.weekdays END AS weekdays,
       CASE WHEN t.trip_id IS NULL THEN pt.validated_trip_id END AS validated_trip_id,
       pt.linked_trip_id,
       t.ticket_ids AS linked_ticket_ids
FROM plan_trips pt
LEFT JOIN trips t ON t.trip_id = pt.linked_trip_id
LEFT JOIN paths p ON p.trip_id = pt.linked_trip_id;
