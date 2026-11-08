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


def test_base_snapshot_route_join(tmp_path, monkeypatch):
    import gzip
    import json

    from app.backend import data

    monkeypatch.setattr(data, "ROOT", tmp_path)
    folder = tmp_path / "data/landing/tripspark_vehicles/date=2026-11-07"
    folder.mkdir(parents=True)
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
                            "name": "Bus 7",
                            "passengersOnboard": 7,
                            "passengerCapacity": 25,
                            "location": {
                                "latitude": 30.61,
                                "longitude": -96.34,
                                "lastGpsDate": "2026-11-07T12:00:00",
                            },
                        }
                    ],
                }
            ],
        }
    ]
    with gzip.open(folder / "snapshot.jsonl.gz", "wt") as stream:
        stream.write(
            json.dumps(
                {
                    "http_status": 200,
                    "ingest_ts": "2026-11-07T18:00:00Z",
                    "payload": payload,
                }
            )
            + "\n"
        )
    rows = data.LocalBackend().latest()
    assert rows and all(r["route_short_name"] == "12" for r in rows)


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
    names = {f["properties"]["stop_name"] for f in static("stops.geojson")["features"]}
    assert all(r["origin_stop_name"] in names and r["destination_stop_name"] in names for r in rows)
    assert all(r["crowding_provenance"] == "SIMULATED" for r in rows)
    fallback = plan(pattern["stops"][0], pattern["stops"][-1], [])
    assert all(r["crowding_provenance"] == "SIMULATED" and r["crowding_scored_at"] is None for r in fallback)
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
    backend.hot_snapshot.return_value = {
        "snapshot_ts": fresh,
        "poll_ingest_ts": fresh,
        "vehicles": [{"vehicle_id": "hot"}],
    }
    rows, source, _, meta = select_vehicles(backend, now)
    assert rows[0]["vehicle_id"] == "hot" and source == "hot-path"
    assert meta["poll_ingest_ts"] == fresh
    backend.query.assert_not_called()
    backend.hot_snapshot.return_value["poll_ingest_ts"] = stale
    backend.query.return_value = [{"vehicle_id": "silver", "ts_gps": fresh}]
    rows, source, reason, _ = select_vehicles(backend, now)
    assert (
        source == "databricks"
        and rows[0]["vehicle_id"] == "silver"
        and "stale" in reason
    )
    backend.query.return_value = []
    rows, source, reason, _ = select_vehicles(backend, now)
    assert source == "local-snapshot" and rows and "empty or stale" in reason
    backend.hot_snapshot.side_effect = RuntimeError("network")
    backend.query.side_effect = RuntimeError("SQL offline")
    assert select_vehicles(backend, now)[1] == "local-snapshot"
    backend.hot_snapshot.side_effect = None
    backend.hot_snapshot.return_value = {
        "snapshot_ts": fresh,
        "poll_ingest_ts": fresh,
        "vehicles": [],
    }
    assert select_vehicles(backend, now)[:2] == ([], "hot-path")


def test_cached_vehicle_metadata_age_advances(monkeypatch):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock

    from app.backend import datasets as module

    now = datetime.now(UTC)
    stamp = (now - timedelta(seconds=25)).isoformat()
    backend = MagicMock()
    backend.hot_snapshot.return_value = {
        "snapshot_ts": stamp,
        "poll_ingest_ts": stamp,
        "vehicles": [{"vehicle_id": "hot", "ts_gps": stamp}],
    }
    monkeypatch.setenv("AGGIEPULSE_BACKEND", "databricks")
    monkeypatch.setattr(module, "DatabricksBackend", lambda: backend)
    data = module.Datasets()
    result = data.get("vehicles")
    assert result["source"] == "hot-path" and result["age_s"] >= 25
    monkeypatch.setattr(
        module, "refresh_vehicle_ages", lambda result: {**result, "age_s": 30}
    )
    assert data.get("vehicles")["age_s"] == 30
    backend.hot_snapshot.assert_called_once()


def test_databricks_queries_are_constant_and_reject_unknown_datasets():
    import pytest

    from app.backend.data import DatabricksBackend

    for statement in DatabricksBackend.QUERIES.values():
        assert statement.startswith("SELECT ") and "{" not in statement
    with pytest.raises(ValueError):
        DatabricksBackend().query("vehicles; DROP TABLE transit.gold.predictions")


def test_replay_query_uses_typed_parameter(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from databricks.sdk.service.sql import StatementParameterListItem

    from app.backend.data import DatabricksBackend
    from app.backend.datasets import Datasets

    monkeypatch.setenv("AGGIEPULSE_BACKEND", "databricks")
    sdk = MagicMock()
    sdk.statement_execution.execute_statement.return_value = SimpleNamespace(
        status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED")),
        manifest=SimpleNamespace(schema=SimpleNamespace(columns=[])),
        result=SimpleNamespace(data_array=[]),
    )
    monkeypatch.setattr(DatabricksBackend, "client", lambda self: sdk)
    result = Datasets().get("replay", hours=72)
    assert result["data"] == [] and result["source"] == "databricks"
    kwargs = sdk.statement_execution.execute_statement.call_args.kwargs
    assert kwargs["statement"] is DatabricksBackend.QUERIES["replay"]
    assert ":hours" in kwargs["statement"] and "LIMIT 10000" in kwargs["statement"]
    assert "date_trunc('minute', ts_gps)" in kwargs["statement"]
    assert kwargs["parameters"] == [
        StatementParameterListItem(name="hours", type="INT", value="72")
    ]


def test_replay_window_dedup_bounds_and_cap():
    from datetime import UTC, datetime, timedelta

    from app.backend.datasets import replay_window

    now = datetime(2026, 11, 7, 20, tzinfo=UTC)

    def row(bus, seconds):
        return {
            "vehicle_id": bus,
            "ts_gps": (now + timedelta(seconds=seconds)).isoformat(),
            "lat": 30.61,
            "lon": -96.34,
            "route_short_name": "12",
            "load_factor": 0.4,
        }

    rows = [row("a", -7200), row("a", -3599), row("a", -3590), row("b", 0)]
    kept = replay_window(rows, 1)
    assert len(kept) == 2
    assert kept[0]["ts_gps"] == rows[2]["ts_gps"]
    assert kept[0]["route"] == "12" and kept[0]["data_origin"] == "observed"
    assert len(replay_window([row(str(i), 0) for i in range(10005)], 1)) == 10000


def test_replay_endpoint_validation_and_offline_fallback(monkeypatch):
    from app.backend import datasets as module
    from app.backend import main
    from app.backend.data import DatabricksBackend

    monkeypatch.setenv("AGGIEPULSE_BACKEND", "databricks")
    monkeypatch.setattr(
        DatabricksBackend,
        "query",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SQL offline")),
    )
    monkeypatch.setattr(module, "local_replay", list)
    monkeypatch.setattr(main, "datasets", module.Datasets())
    for value in ["0", "169", "1;DROP TABLE", "1.5"]:
        assert client.get("/api/replay", params={"hours": value}).status_code == 422
    data = client.get("/api/replay?hours=24").json()
    assert data["source"] == "local-snapshot" and data["data"]
    assert data["mode"] == "REPLAY" and data["row_cap"] == 10000
    assert "SQL offline" in data["fallback_reason"]
    assert data["start_ts"] <= data["end_ts"]
    assert all(r["provenance"] == "MEASURED · APC" for r in data["data"])


def test_comparison_and_impacts_endpoints(monkeypatch):
    from app.backend import main
    from app.backend.datasets import Datasets

    monkeypatch.setenv("AGGIEPULSE_BACKEND", "local")
    monkeypatch.setattr(main, "datasets", Datasets())
    comparison = client.get("/api/history/comparison").json()
    assert comparison["source"] == "local-snapshot"
    assert all(r["actual_data_origin"] == "observed" for r in comparison["data"])
    impacts = client.get("/api/events/impacts").json()
    game = next(e for e in impacts["data"] if e["event_id"] == "tennessee-2026-11-14")
    assert game["affected_routes"] and game["time_tba"]
    assert game["impact_provenance"] == "SIMULATED PRIOR"
    assert game["impacts"] == []  # Local mocks must not invent game-day impact.


def test_event_impacts_require_nearby_route_and_overlapping_target(monkeypatch):
    from app.backend import datasets as module

    event = {
        "event_id": "event",
        "title": "Test",
        "start_ts": "2026-11-07T20:00:00Z",
        "lat": 30.61,
        "lon": -96.34,
    }
    predictions = [
        {
            "route": "12",
            "direction": "0",
            "t0": "2026-11-07T19:30:00Z",
            "horizon_min": 30,
            "event_uplift": 0.12,
        },
        {
            "route": "12",
            "t0": "2026-11-08T19:30:00Z",
            "horizon_min": 30,
            "event_uplift": 0.3,
        },
        {
            "route": "99",
            "t0": "2026-11-07T19:30:00Z",
            "horizon_min": 30,
            "event_uplift": 0.8,
        },
    ]
    monkeypatch.setattr(module, "affected", lambda event: ["12"])
    datasets = module.Datasets()
    monkeypatch.setattr(
        datasets,
        "get",
        lambda name: {
            "data": {
                "events": [event],
                "predictions": predictions,
                "route_directions": [],
            }[name],
            "source": "databricks",
        },
    )
    impacts = datasets.event_impacts()["data"][1]["impacts"]
    assert len(impacts) == 1 and impacts[0]["event_uplift"] == 0.12


def test_meta_freshness_uses_original_timestamps(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.backend.datasets import Datasets

    now = datetime.now(UTC)
    scoring = (now - timedelta(minutes=45)).isoformat()
    gps = (now - timedelta(minutes=75)).isoformat()
    datasets = Datasets()

    def get(name):
        rows = {
            "predictions": [{"scoring_ts": scoring, "source": "databricks"}],
            "pipeline": [
                {"latest_pipeline_gps_ts": gps, "latest_pipeline_ingest_ts": gps}
            ],
        }.get(name, [])
        return {
            "data": rows,
            "source": "local-snapshot" if name == "vehicles" else "databricks",
            "age_s": 900,
        }

    monkeypatch.setattr(datasets, "get", get)
    data = datasets.meta()["freshness"]
    assert data["last_scoring_ts"] == scoring
    assert data["scoring_age_s"] >= 2700 and data["pipeline_lag_s"] >= 4500
    assert data["bus_source"] == "local-snapshot" and data["bus_snapshot_age_s"] == 900


def test_comparison_query_excludes_simulated_actuals_and_unfinished_windows():
    from app.backend.data import DatabricksBackend

    query = DatabricksBackend.QUERIES["comparison"]
    assert "actual_data_origin = 'observed'" in query
    assert "actual_n_pings > 0" in query
    assert "target_window_start + INTERVAL 15 MINUTES" in query
    assert "ORDER BY scoring_ts DESC" in query


def test_alert_stop_names_and_event_venue_are_readable(monkeypatch):
    from app.backend.data import DatabricksBackend
    from app.backend.datasets import Datasets, static

    stop = static("stops.geojson")["features"][0]["properties"]
    monkeypatch.setenv("AGGIEPULSE_BACKEND", "databricks")
    monkeypatch.setattr(DatabricksBackend, "query", lambda self, name: [
        {"closed_stops": [stop["stop_id"], "unknown"], "venue_key": "rudder_theatre"}
    ])
    datasets = Datasets()
    assert datasets.get("alerts")["data"][0]["closed_stop_names"] == [stop["stop_name"]]
    monkeypatch.setattr("app.backend.datasets.affected", lambda event: [])
    assert datasets.get("events")["data"][0]["venue_name"] == "Rudder Theatre"
