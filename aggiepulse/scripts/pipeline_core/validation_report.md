# Core pipeline validation

Captured: 2026-11-07T20:19:01.375059+00:00

Pipeline: `206b4370-b5cc-48eb-8eba-932dcea5dc95` — [open pipeline](https://dbc-595c228e-76f6.cloud.databricks.com/pipelines/206b4370-b5cc-48eb-8eba-932dcea5dc95)

Schedule: every 15 minutes, UTC (`0 0/15 * * * ?`), unpaused.

Files: `pipelines/core/core.py`, `pipelines/core/lib/`, `resources/pipeline_core.yml`,
`scripts/pipeline_core/{prepare,status,report}.py`, `tests/pipeline_core/test_helpers.py`.

## Table counts

| Table | Rows |
|---|---:|
| bronze.events | 8,293 |
| bronze.gtfs_calendar_dates | 96 |
| bronze.gtfs_feed_info | 1 |
| bronze.gtfs_routes | 22 |
| bronze.gtfs_shapes | 2,414 |
| bronze.gtfs_stop_times | 57,870 |
| bronze.gtfs_stops | 203 |
| bronze.gtfs_trips | 6,312 |
| bronze.ntd_monthly | 25 |
| bronze.seed_academic_days | 153 |
| bronze.seed_basketball_home | 21 |
| bronze.seed_class_periods | 32 |
| bronze.seed_event_filters | 1 |
| bronze.seed_football_home | 12 |
| bronze.seed_venues | 27 |
| bronze.tripspark_base | 3 |
| bronze.tripspark_vehicles | 54 |
| bronze.weather_nws | 4 |
| bronze.weather_openmeteo | 7 |
| silver.academic_days | 153 |
| silver.class_periods | 32 |
| silver.events | 1,713 |
| silver.events_evaluated | 8,293 |
| silver.events_filter_stats | 8 |
| silver.gtfs_calendar_dates | 96 |
| silver.gtfs_routes | 22 |
| silver.gtfs_shapes | 2,414 |
| silver.gtfs_stop_times | 57,870 |
| silver.gtfs_stops | 203 |
| silver.gtfs_trips | 6,312 |
| silver.ntd_monthly | 25 |
| silver.schedule_expanded | 1,560,184 |
| silver.service_alerts | 8 |
| silver.tripspark_route_mismatches | 1 |
| silver.tripspark_routes | 41 |
| silver.vehicle_latest | 0 |
| silver.vehicle_pings | 483 |
| silver.vehicle_pings_candidates | 485 |
| silver.vehicle_pings_quarantine | 0 |
| silver.weather_hourly | 2,859 |

## Expectations

| Dataset | Rule | Passed | Failed | Pass rate |
|---|---|---:|---:|---:|
| transit.bronze.events | valid_json_envelope | 8,293 | 0 | 100.00% |
| transit.bronze.ntd_monthly | valid_json_envelope | 25 | 0 | 100.00% |
| transit.bronze.tripspark_base | valid_json_envelope | 3 | 0 | 100.00% |
| transit.bronze.tripspark_vehicles | valid_json_envelope | 54 | 0 | 100.00% |
| transit.bronze.weather_nws | valid_json_envelope | 1 | 0 | 100.00% |
| transit.bronze.weather_openmeteo | valid_json_envelope | 1 | 0 | 100.00% |
| transit.silver.events | event_has_start | 1,713 | 0 | 100.00% |
| transit.silver.schedule_expanded | scheduled_time_present | 1,560,184 | 0 | 100.00% |
| transit.silver.vehicle_pings | fresh_gps | 483 | 0 | 100.00% |
| transit.silver.vehicle_pings | local_bbox | 483 | 0 | 100.00% |
| transit.silver.vehicle_pings | route_present | 483 | 0 | 100.00% |
| transit.silver.vehicle_pings | valid_apc | 483 | 0 | 100.00% |
| transit.silver.vehicle_pings | vehicle_identity | 483 | 0 | 100.00% |
| transit.silver.vehicle_pings_candidates | fresh_gps | 485 | 0 | 100.00% |
| transit.silver.vehicle_pings_candidates | local_bbox | 485 | 0 | 100.00% |
| transit.silver.vehicle_pings_candidates | route_present | 485 | 0 | 100.00% |
| transit.silver.vehicle_pings_candidates | valid_apc | 485 | 0 | 100.00% |
| transit.silver.vehicle_pings_candidates | vehicle_identity | 485 | 0 | 100.00% |
| transit.silver.weather_hourly | weather_hour_present | 2,859 | 0 | 100.00% |

## Vehicle coverage

```json
[
  {
    "min_ts_gps": "2026-11-07T19:31:53.000Z",
    "max_ts_gps": "2026-11-07T19:59:26.000Z",
    "vehicles": 9,
    "hosts": 1
  }
]
```

## Route mismatches

```json
[
  {
    "route_key": "ab374f65-de4d-4e55-ad1e-944ec271350b",
    "route_short_name": "RD",
    "route_name": "Ring Day",
    "reason": "TripSpark short name absent from current GTFS"
  }
]
```

## Sample rows

### silver.vehicle_pings

```json
[
  {
    "route_key": "8e4ce203-ce21-4f50-b584-2a63b3940a8a",
    "direction_key": "d41089d8-10c8-4939-9b25-8958475aaa3e",
    "vehicle_id": "1d66d52c-0a4d-45b7-a9ee-279b09a3ce19",
    "vehicle_name": "B2022",
    "ts_gps": "2026-11-07T19:32:08.000Z",
    "ingest_ts": "2026-11-07T19:32:13.550Z",
    "poll_id": "5db8053a-227e-4ee5-97e1-d5407fa6fa6b",
    "lat": 30.615539833333333,
    "lon": -96.34297900000001,
    "speed": 0.0,
    "heading": 225.88999938964844,
    "onboard": 13,
    "capacity": 85,
    "is_extra_trip": false,
    "source_host": "JBOILAPTOP.local",
    "route_short_name": "03-05",
    "route_name": "Break, Nights, & Weekends",
    "direction_name": "West Campus Circulator",
    "load_factor": 0.15294117647058825,
    "data_origin": "observed",
    "nearest_stop_id": "0019",
    "nearest_stop_dist_m": 7.167750975320774,
    "h3_r10": 622700521048866815
  },
  {
    "route_key": "8e4ce203-ce21-4f50-b584-2a63b3940a8a",
    "direction_key": "d41089d8-10c8-4939-9b25-8958475aaa3e",
    "vehicle_id": "1d66d52c-0a4d-45b7-a9ee-279b09a3ce19",
    "vehicle_name": "B2022",
    "ts_gps": "2026-11-07T19:32:24.000Z",
    "ingest_ts": "2026-11-07T19:32:30.096Z",
    "poll_id": "90e1b7ec-9c3d-40a0-bf68-ab2f4447d7bd",
    "lat": 30.615901833333336,
    "lon": -96.3434585,
    "speed": 9.787489436689732,
    "heading": 242.9499969482422,
    "onboard": 17,
    "capacity": 85,
    "is_extra_trip": false,
    "source_host": "JBOILAPTOP.local",
    "route_short_name": "03-05",
    "route_name": "Break, Nights, & Weekends",
    "direction_name": "West Campus Circulator",
    "load_factor": 0.2,
    "data_origin": "observed",
    "nearest_stop_id": "0019",
    "nearest_stop_dist_m": 54.07160110032107,
    "h3_r10": 622700521048801279
  },
  {
    "route_key": "8e4ce203-ce21-4f50-b584-2a63b3940a8a",
    "direction_key": "d41089d8-10c8-4939-9b25-8958475aaa3e",
    "vehicle_id": "1d66d52c-0a4d-45b7-a9ee-279b09a3ce19",
    "vehicle_name": "B2022",
    "ts_gps": "2026-11-07T19:33:52.000Z",
    "ingest_ts": "2026-11-07T19:34:07.921Z",
    "poll_id": "f241446d-53bd-4473-8fec-4258f54a85a5",
    "lat": 30.611562000000003,
    "lon": -96.34719850000002,
    "speed": 4.067614630557544,
    "heading": 247.52999877929688,
    "onboard": 17,
    "capacity": 85,
    "is_extra_trip": false,
    "source_host": "JBOILAPTOP.local",
    "route_short_name": "03-05",
    "route_name": "Break, Nights, & Weekends",
    "direction_name": "West Campus Circulator",
    "load_factor": 0.2,
    "data_origin": "observed",
    "nearest_stop_id": "0048",
    "nearest_stop_dist_m": 144.9744448167889,
    "h3_r10": 622700520823455743
  }
]
```

### silver.schedule_expanded

```json
[
  {
    "service_date": "2026-10-23",
    "trip_id": "e5d998a3-f4bd-4ee6-9198-9daa1bddefad",
    "route_id": "26",
    "route_short_name": "26",
    "direction_id": 1,
    "shape_id": "af57c18b-6a88-416c-851a-4bd02e9b54a1",
    "stop_id": "2600",
    "stop_sequence": 69,
    "sched_arrival_ts": "2026-10-23T15:55:00.000Z",
    "sched_departure_ts": "2026-10-23T15:55:00.000Z",
    "is_timepoint": true
  },
  {
    "service_date": "2026-12-04",
    "trip_id": "2b381f47-cc4f-4d41-9d80-0248ae40e8cc",
    "route_id": "03",
    "route_short_name": "03",
    "direction_id": 0,
    "shape_id": "df048bd7-f9e8-456e-b870-c8475782af8d",
    "stop_id": "0317",
    "stop_sequence": 11,
    "sched_arrival_ts": "2026-12-04T13:12:52.000Z",
    "sched_departure_ts": "2026-12-04T13:12:52.000Z",
    "is_timepoint": false
  },
  {
    "service_date": "2026-12-04",
    "trip_id": "ed1c2000-f7bd-47b4-8d02-faa3a1784934",
    "route_id": "08",
    "route_short_name": "08",
    "direction_id": 1,
    "shape_id": "d3c2775e-3227-49db-a93e-45b0c4011b3c",
    "stop_id": "0810",
    "stop_sequence": 0,
    "sched_arrival_ts": "2026-12-04T18:18:00.000Z",
    "sched_departure_ts": "2026-12-04T18:18:00.000Z",
    "is_timepoint": true
  }
]
```

### silver.service_alerts

```json
[
  {
    "alert_id": "3861",
    "start_ts": "2026-11-12T22:30:00.000Z",
    "end_ts": "2026-11-13T04:00:00.000Z",
    "daily_window": {
      "start_local": "00:00:00",
      "end_local": "23:59:59",
      "timezone": "America/Chicago"
    },
    "title": "Multiple routes will be detoured on November 12 from 4:30 PM-10 PM",
    "text": "On Thursday, November 12, 2026 at 4:30 PM until 10 PM, the following routes will be affected by a service disruption: 01 Bonfire 01-04 Break, Nights, & Weekends 03-05 Break, Nights, & Weekends 05 Bush School 01 Bonfire The following bus stops will be closed during the service disruption: Rec Center - Outbound (ID: 0112) Lot 100G (ID: 0114) Reed Arena/Lot 100c (ID: 0050) Kleberg - Inbound (ID: 0028) 01-04 Break, Nights, & Weekends The following bus stops will be closed during the service disruption: Rec Center - Outbound (ID: 0112) Lot 100G (ID: 0114) Reed Arena/Lot 100c (ID: 0050) Kleberg - Inbound (ID: 0028) 03-05 Break, Nights, & Weekends The following bus stop will be closed during the service disruption: Reed Arena/Lot 100c (ID: 0050) A temporary stop will be provided at: Reed Arena - Inbound (ID: 0053) 05 Bush School to MSC (MSC) The following bus stop will be closed during the service disruption: Reed Arena/Lot 100c (ID: 0050) A temporary stop will be provided at: Reed Arena - Inbound (ID: 0053) to UPD (UPD) This direction is not affected by the service disruption.",
    "routes": [
      "03-05",
      "01-04",
      "05",
      "01"
    ],
    "closed_stops": [
      "0112",
      "0028",
      "0050",
      "0114"
    ]
  },
  {
    "alert_id": "3860",
    "start_ts": "2026-11-10T02:30:00.000Z",
    "end_ts": "2026-11-10T04:00:00.000Z",
    "daily_window": {
      "start_local": "00:00:00",
      "end_local": "23:59:59",
      "timezone": "America/Chicago"
    },
    "title": "Multiple routes will be detoured on November 9 from 8:30 PM-10 PM",
    "text": "On Monday, November 9, 2026 at 8:30 PM until 10 PM, the following routes will be affected by a service disruption: 07 Airport 35 Hullabaloo 40 Century Tree 40-41 Break, Nights, & Weekends 41 Good Bull 07 Airport This route will be affected by a service disruption from Turbo Lab (ID: 0708) to Texas A&M Hotel/Joe Routt (ID: 0700) . 35 Hullabaloo Inbound (MSC) The following bus stops will be closed during the service disruption: Physical Education - Inbound (ID: 0056) Reed Arena - Inbound (ID: 0053) A temporary stop will be provided at: Rec Center - Inbound (ID: 0814) Outbound (Jones Butler @ Harvey Mitchell) This direction is not affected by the service disruption. 40 Century Tree Inbound (MSC) The following bus stops will be closed during the service disruption: Physical Education - Inbound (ID: 0056) Reed Arena - Inbound (ID: 0053) A temporary stop will be provided at: Rec Center - Inbound (ID: 0814) Outbound (Deacon West) This direction is not affected by the service disruption. 40-41 Break, Nights, & Weekends Inbound (MSC) The following bus stops will be closed during the service disruption: Physical Education - Inbound (ID: 0056) Reed Arena - Inbound (ID: 0053) A temporary stop will be provided at: Rec Center - Inbound (ID: 0814) Outbound (Deacon West) This direction is not affected by the service disruption. 41 Good Bull Inbound (MSC) The following bus stops will be closed during the service disruption: Physical Education - Inbound (ID: 0056) Reed Arena - Inbound (ID: 0053) A temporary stop will be provided at: Rec Center - Inbound (ID: 0814) Outbound (Deacon West) This direction is not affected by the service disruption.",
    "routes": [
      "35",
      "40",
      "07",
      "40-41",
      "41"
    ],
    "closed_stops": [
      "0053",
      "0056"
    ]
  },
  {
    "alert_id": "3862",
    "start_ts": "2026-11-13T02:30:00.000Z",
    "end_ts": "2026-11-13T04:00:00.000Z",
    "daily_window": {
      "start_local": "00:00:00",
      "end_local": "23:59:59",
      "timezone": "America/Chicago"
    },
    "title": "Multiple routes will be detoured on November 12 from 8:30 PM-10 PM",
    "text": "On Thursday, November 12, 2026 at 8:30 PM until 10 PM, the following routes will be affected by a service disruption: 07 Airport 35 Hullabaloo 40 Century Tree 40-41 Break, Nights, & Weekends 41 Good Bull 07 Airport This route will be affected by a service disruption from Turbo Lab (ID: 0708) to Texas A&M Hotel/Joe Routt (ID: 0700) . 35 Hullabaloo Inbound (MSC) The following bus stops will be closed during the service disruption: Physical Education - Inbound (ID: 0056) Reed Arena - Inbound (ID: 0053) A temporary stop will be provided at: Rec Center - Inbound (ID: 0814) Outbound (Jones Butler @ Harvey Mitchell) This direction is not affected by the service disruption. 40 Century Tree Inbound (MSC) The following bus stops will be closed during the service disruption: Physical Education - Inbound (ID: 0056) Reed Arena - Inbound (ID: 0053) A temporary stop will be provided at: Rec Center - Inbound (ID: 0814) Outbound (Deacon West) This direction is not affected by the service disruption. 40-41 Break, Nights, & Weekends Inbound (MSC) The following bus stops will be closed during the service disruption: Physical Education - Inbound (ID: 0056) Reed Arena - Inbound (ID: 0053) A temporary stop will be provided at: Rec Center - Inbound (ID: 0814) Outbound (Deacon West) This direction is not affected by the service disruption. 41 Good Bull Inbound (MSC) The following bus stops will be closed during the service disruption: Physical Education - Inbound (ID: 0056) Reed Arena - Inbound (ID: 0053) A temporary stop will be provided at: Rec Center - Inbound (ID: 0814) Outbound (Deacon West) This direction is not affected by the service disruption.",
    "routes": [
      "07",
      "41",
      "40",
      "40-41",
      "35"
    ],
    "closed_stops": [
      "0053",
      "0056"
    ]
  }
]
```

### silver.events

```json
[
  {
    "event_id": "343091",
    "title": "Commencement and Commissioning - College Station",
    "start_ts": "2026-08-08T05:00:00.000Z",
    "end_ts": null,
    "is_all_day": true,
    "location_title": "Reed Arena",
    "venue_key": "reed_arena",
    "lat": 30.605854,
    "lon": -96.346216,
    "campus": "Bryan-College Station",
    "category": "Academic Calendar",
    "event_types": [
      "Academic Calendar"
    ],
    "audience": [],
    "group_title": "Academic Calendar",
    "rsvp_total": null,
    "is_athletics": false,
    "is_football_home": false,
    "est_attendance": 12989,
    "significance": 0.9471935109664646,
    "is_canceled": false
  },
  {
    "event_id": "353084",
    "title": "Rec-A-Palooza | Howdy Week Event",
    "start_ts": "2026-08-23T18:00:00.000Z",
    "end_ts": "2026-08-23T20:30:00.000Z",
    "is_all_day": false,
    "location_title": "Student Recreation Center",
    "venue_key": "student_rec",
    "lat": 30.607097,
    "lon": -96.342793,
    "campus": "Bryan-College Station",
    "category": "Campus Life",
    "event_types": [
      "Campus Life"
    ],
    "audience": [
      "Students"
    ],
    "group_title": "Rec Sports",
    "rsvp_total": null,
    "is_athletics": false,
    "is_football_home": false,
    "est_attendance": null,
    "significance": null,
    "is_canceled": false
  },
  {
    "event_id": "358086",
    "title": "REO Researcher Academy - New Faculty MINT",
    "start_ts": "2026-08-10T13:30:00.000Z",
    "end_ts": "2026-08-10T19:00:00.000Z",
    "is_all_day": false,
    "location_title": "Gilchrist Building (T.T.I.)",
    "venue_key": null,
    "lat": 30.601483,
    "lon": -96.353422,
    "campus": "Bryan-College Station",
    "category": "Speakers, Forums, Conferences, Training & Workshops",
    "event_types": [
      "Speakers, Forums, Conferences, Training & Workshops"
    ],
    "audience": [
      "Faculty",
      "Researcher"
    ],
    "group_title": "College of Education and Human Development",
    "rsvp_total": null,
    "is_athletics": false,
    "is_football_home": false,
    "est_attendance": null,
    "significance": null,
    "is_canceled": false
  }
]
```

### silver.weather_hourly

```json
[
  {
    "ts_hour": "2026-08-01T00:00:00.000Z",
    "temp_c": 34.7,
    "apparent_c": 36.9,
    "precip_mm": 0.0,
    "rain_mm": 0.0,
    "wind_kph": 14.8,
    "weather_code": 0,
    "source": "openmeteo_archive",
    "is_forecast": false,
    "forecast_issued_ts": null
  },
  {
    "ts_hour": "2026-08-01T01:00:00.000Z",
    "temp_c": 33.7,
    "apparent_c": 36.0,
    "precip_mm": 0.0,
    "rain_mm": 0.0,
    "wind_kph": 14.0,
    "weather_code": 0,
    "source": "openmeteo_archive",
    "is_forecast": false,
    "forecast_issued_ts": null
  },
  {
    "ts_hour": "2026-08-01T02:00:00.000Z",
    "temp_c": 32.7,
    "apparent_c": 34.8,
    "precip_mm": 0.0,
    "rain_mm": 0.0,
    "wind_kph": 14.8,
    "weather_code": 0,
    "source": "openmeteo_archive",
    "is_forecast": false,
    "forecast_issued_ts": null
  }
]
```

### silver.ntd_monthly

```json
[
  {
    "ntd_id": "60278",
    "agency": "Texas A&M University",
    "mode": "MB",
    "tos": "DO",
    "upt": 1003376,
    "vrm": 194449.0,
    "vrh": 16767.0,
    "voms": 66,
    "ingest_ts": "2026-11-07T19:28:02.079Z",
    "month": "2024-09-01"
  },
  {
    "ntd_id": "60278",
    "agency": "Texas A&M University",
    "mode": "MB",
    "tos": "DO",
    "upt": 971088,
    "vrm": 210771.0,
    "vrh": 18050.0,
    "voms": 66,
    "ingest_ts": "2026-11-07T19:28:02.079Z",
    "month": "2024-10-01"
  },
  {
    "ntd_id": "60278",
    "agency": "Texas A&M University",
    "mode": "MB",
    "tos": "DO",
    "upt": 742616,
    "vrm": 174684.0,
    "vrh": 14932.0,
    "voms": 66,
    "ingest_ts": "2026-11-07T19:28:02.079Z",
    "month": "2024-11-01"
  }
]
```

## Known gaps

- Vehicle coverage is the landed completed-hour history; current snapshots can be empty until newer files arrive.
- vehicle_latest is evaluated at refresh time; applications should also check ping age at request time.
- Speed units are undocumented; unverified venue sizes and coordinates remain null.
- Attendance, category/audience weights, and game durations are modeling priors; TBA kickoffs stay all-day.
- Forecast issue time is retrieval time; archive weather is not a finalized station-observation guarantee.
- Current Silver GTFS follows the newest feed; earlier versions remain in Bronze.

Validation: local pytest and ruff passed; see JSON for complete pipeline/job run evidence.
