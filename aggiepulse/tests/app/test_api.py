import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient

from app.backend.data import flatten_vehicles
from app.backend.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_routes():
    response = client.get("/api/routes")
    assert response.status_code == 200
    assert all("route_id" in r for r in response.json())


def test_predictions():
    rows = client.get("/api/predictions").json()
    assert rows
    assert {r["horizon_min"] for r in rows} == {15, 30, 60}
    assert all(
        r["provenance"] == "SIMULATED"
        and r["pred_p10"] <= r["pred_p50"] <= r["pred_p90"]
        for r in rows
    )


def test_nested_vehicle_payload():
    payload = {
        "routes": [
            {
                "routeShortName": "12",
                "directions": [
                    {
                        "directionKey": "outbound",
                        "vehicles": [
                            {
                                "name": "Bus 7",
                                "lat": "30.61",
                                "lon": "-96.34",
                                "passengersOnboard": 21,
                                "passengerCapacity": 42,
                                "lastGpsDate": "2026-11-07T12:00:00",
                            },
                            {"name": "bad", "lat": "invalid", "lon": 0},
                        ],
                    }
                ],
            }
        ]
    }
    rows = flatten_vehicles(payload)
    assert len(rows) == 1
    assert rows[0]["route"] == "12"
    assert rows[0]["direction"] == "outbound"
    assert rows[0]["load_factor"] == 0.5
    assert rows[0]["provenance"] == "MEASURED · APC"
    assert rows[0]["gps_age_s"] is not None
    assert flatten_vehicles(None) == []


def test_verified_collector_envelope():
    payload = [
        {
            "routeKey": "route-guid",
            "routeShortName": "12",
            "vehiclesByDirections": [
                {
                    "directionKey": "dir-guid",
                    "vehicles": [
                        {
                            "key": "bus-guid",
                            "name": "B2011",
                            "passengersOnboard": 7,
                            "passengerCapacity": 25,
                            "location": {
                                "latitude": 30.615,
                                "longitude": -96.34,
                                "lastGpsDate": "2026-11-07T12:00:00",
                            },
                        }
                    ],
                }
            ],
        }
    ]
    rows = flatten_vehicles(payload, "2026-11-07T19:00:00+00:00")
    assert rows[0]["route"] == "12"
    assert rows[0]["load_factor"] == 7 / 25
    assert rows[0]["ts_gps"] == "2026-11-07T18:00:00+00:00"


def test_base_snapshot_route_join():
    from app.backend.data import LocalBackend

    rows = LocalBackend().latest()
    assert rows
    assert all(r["route_short_name"] for r in rows)


def test_sql_fallback(monkeypatch):
    from app.backend.data import DatabricksBackend
    from app.backend.datasets import Datasets

    monkeypatch.setenv("AGGIEPULSE_BACKEND", "databricks")

    def unavailable(*args):
        raise RuntimeError("TABLE_OR_VIEW_NOT_FOUND")

    monkeypatch.setattr(DatabricksBackend, "query", unavailable)
    data = Datasets().get("predictions")
    assert data["source"] == "mock"
    assert "TABLE_OR_VIEW_NOT_FOUND" in data["fallback_reason"]
    assert all(r["source"] == "mock" for r in data["data"])


def test_event_filter():
    import csv

    import yaml

    from app.backend.datasets import filter_event

    rules = yaml.safe_load((ROOT / "seeds/event_filters.yaml").read_text())
    venues = list(csv.DictReader((ROOT / "seeds/venues.csv").open()))
    event = {
        "title": "Football vs Tennessee",
        "location_title": "Kyle Field",
        "online_url": None,
    }
    assert filter_event(event, rules, venues)["venue_key"] == "Kyle Field"
    assert (
        filter_event({**event, "online_url": "https://zoom.us/test"}, rules, venues)
        is None
    )
    assert filter_event({**event, "title": "Group study hours"}, rules, venues) is None


def test_planner_scoring():
    from app.backend.datasets import mock_predictions, plan, static

    pattern = static("planner.json")[0]
    rows = plan(pattern["stops"][0], pattern["stops"][-1], mock_predictions())
    assert rows
    assert rows == sorted(rows, key=lambda r: r["score"])
    assert all(
        abs(
            r["score"]
            - (r["wait_min"] + r["ride_min"] + r["walk_min"] + 20 * r["crowding"])
        )
        < 0.2
        for r in rows
    )


def test_vehicle_source_selection():
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock

    from app.backend.datasets import select_vehicles

    now = datetime.now(UTC)
    fresh = now.isoformat()
    stale = (now - timedelta(minutes=4)).isoformat()
    backend = MagicMock()
    backend.hot_snapshot.return_value = {'snapshot_ts': fresh, 'poll_ingest_ts': fresh, 'vehicles': [{'vehicle_id': 'hot'}]}
    rows, source, _, meta = select_vehicles(backend, now)
    assert rows[0]['vehicle_id'] == 'hot' and source == 'hot-path'
    assert meta['poll_ingest_ts'] == fresh
    backend.query.assert_not_called()
    backend.hot_snapshot.return_value['poll_ingest_ts'] = stale
    backend.query.return_value = [{'vehicle_id': 'silver', 'ts_gps': fresh}]
    rows, source, reason, _ = select_vehicles(backend, now)
    assert source == 'databricks' and rows[0]['vehicle_id'] == 'silver' and 'stale' in reason
    backend.query.return_value = []
    rows, source, reason, _ = select_vehicles(backend, now)
    assert source == 'local-snapshot' and rows and 'empty or stale' in reason
    backend.hot_snapshot.side_effect = RuntimeError('network')
    backend.query.side_effect = RuntimeError('SQL offline')
    assert select_vehicles(backend, now)[1] == 'local-snapshot'
    backend.hot_snapshot.side_effect = None
    backend.hot_snapshot.return_value = {'snapshot_ts': fresh, 'poll_ingest_ts': fresh, 'vehicles': []}
    assert select_vehicles(backend, now)[:2] == ([], 'hot-path')


def test_cached_vehicle_metadata_age_advances(monkeypatch):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock

    from app.backend import datasets as module

    now = datetime.now(UTC)
    stamp = (now - timedelta(seconds=25)).isoformat()
    backend = MagicMock()
    backend.hot_snapshot.return_value = {'snapshot_ts': stamp, 'poll_ingest_ts': stamp, 'vehicles': [{'vehicle_id': 'hot', 'ts_gps': stamp}]}
    monkeypatch.setenv('AGGIEPULSE_BACKEND', 'databricks')
    monkeypatch.setattr(module, 'DatabricksBackend', lambda: backend)
    data = module.Datasets()
    result = data.get('vehicles')
    assert result['source'] == 'hot-path' and result['age_s'] >= 25
    monkeypatch.setattr(module, 'refresh_vehicle_ages', lambda result: {**result, 'age_s': 30})
    assert data.get('vehicles')['age_s'] == 30
    backend.hot_snapshot.assert_called_once()


def test_databricks_queries_are_constant_and_reject_unknown_datasets():
    import pytest

    from app.backend.data import DatabricksBackend

    for statement in DatabricksBackend.QUERIES.values():
        assert statement.startswith("SELECT ") and "{" not in statement
    with pytest.raises(ValueError):
        DatabricksBackend().query("vehicles; DROP TABLE transit.gold.predictions")
