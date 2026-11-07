from datetime import UTC, datetime

import pytest

from aggiepulse.collector.parse import flatten_vehicles, gps_time_utc


def test_saved_real_payload(vehicles_payload, base_payload, ingest_ts):
    rows = flatten_vehicles(vehicles_payload, ingest_ts, base_payload)
    assert len(rows) == 3
    first = rows[0]
    assert first["vehicle_id"] == "448c7dc1-e837-4eff-939f-85baff75aacc"
    assert first["vehicle_name"] == "B2011"
    assert first["ts_gps_utc"] == datetime(2026, 11, 7, 19, 26, 35, tzinfo=UTC)
    assert first["route_short_name"] is not None
    assert first["direction_name"] == "Campus Circulator"
    assert first["onboard"] == 2
    assert first["capacity"] == 85
    assert first["load_factor"] == pytest.approx(2 / 85)
    assert first["lat"] == pytest.approx(30.614531)
    assert first["lon"] == pytest.approx(-96.342465)
    assert first["speed"] == 0.0
    assert first["is_extra_trip"] is False
    assert sum(row["onboard"] for row in rows) == 19


@pytest.mark.parametrize(
    ("local", "ingest", "expected"),
    [
        ("2026-11-01T00:59:59", "2026-11-01T06:01:00Z", "2026-11-01T05:59:59Z"),
        ("2026-11-01T01:30:00", "2026-11-01T06:35:00Z", "2026-11-01T06:30:00Z"),
        # Second fold is future, despite being closer than the first fold.
        ("2026-11-01T01:30:00", "2026-11-01T07:20:00Z", "2026-11-01T06:30:00Z"),
        ("2026-11-01T01:30:00", "2026-11-01T07:35:00Z", "2026-11-01T07:30:00Z"),
        ("2026-11-01T01:00:00", "2026-11-01T07:00:00Z", "2026-11-01T07:00:00Z"),
        ("2026-11-01T01:59:59", "2026-11-01T08:01:00Z", "2026-11-01T07:59:59Z"),
        ("2026-11-01T02:00:00", "2026-11-01T08:01:00Z", "2026-11-01T08:00:00Z"),
        ("2026-07-01T12:00:00", "2026-07-01T17:01:00Z", "2026-07-01T17:00:00Z"),
        ("2026-11-01T01:30:00-06:00", "2026-11-01T08:00:00Z", "2026-11-01T07:30:00Z"),
    ],
)
def test_gps_time_dst(local, ingest, expected):
    assert gps_time_utc(local, ingest) == datetime.fromisoformat(expected)


@pytest.mark.parametrize(
    ("local", "ingest", "message"),
    [
        ("2026-11-01T01:30:00", "2026-11-01T06:00:00Z", "Both ambiguous"),
        ("2026-03-08T02:30:00", "2026-03-08T09:00:00Z", "Nonexistent"),
        ("2026-11-07T12:00:00", "2026-11-07T19:00:00", "timezone"),
    ],
)
def test_invalid_times(local, ingest, message):
    with pytest.raises(ValueError, match=message):
        gps_time_utc(local, ingest)


def test_missing_values_and_group_fallback(vehicles_payload, ingest_ts):
    vehicle = vehicles_payload[0]["vehiclesByDirections"][0]["vehicles"][0]
    vehicle.pop("routeKey")
    vehicle.pop("directionKey")
    vehicle["location"] = None
    vehicle["passengerCapacity"] = 0
    vehicle["passengersOnboard"] = None
    rows = flatten_vehicles(vehicles_payload, ingest_ts)
    assert rows[0]["route_key"] == vehicles_payload[0]["routeKey"]
    assert rows[0]["direction_key"] is not None
    assert rows[0]["ts_gps_utc"] is None
    assert rows[0]["lat"] is None
    assert rows[0]["onboard"] is None
    assert rows[0]["load_factor"] is None
    assert rows[0]["route_short_name"] is None


def test_empty_feed(ingest_ts):
    assert flatten_vehicles([], ingest_ts) == []


def test_no_capacity_clamping_or_unit_guessing(vehicles_payload, ingest_ts):
    vehicle = vehicles_payload[0]["vehiclesByDirections"][0]["vehicles"][0]
    vehicle["passengersOnboard"] = 100
    vehicle["passengerCapacity"] = 85
    vehicle["location"]["speed"] = 16.347378869899785
    row = flatten_vehicles(vehicles_payload, ingest_ts)[0]
    assert row["load_factor"] == pytest.approx(100 / 85)
    assert row["speed"] == 16.347378869899785
