from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from app.backend.main import app
from app.backend.data import flatten_vehicles

client = TestClient(app)


def test_health():
    assert client.get('/api/health').json() == {'status': 'ok'}


def test_routes():
    response = client.get('/api/routes')
    assert response.status_code == 200
    assert all('route_id' in r for r in response.json())


def test_predictions():
    rows = client.get('/api/predictions').json()
    assert rows
    assert {r['horizon_min'] for r in rows} == {15, 30, 60}
    assert all(r['provenance'] == 'SIMULATED' and r['pred_p10'] <= r['pred_p50'] <= r['pred_p90'] for r in rows)


def test_nested_vehicle_payload():
    payload = {'routes': [{'routeShortName': '12', 'directions': [{'directionKey': 'outbound', 'vehicles': [{'name': 'Bus 7', 'lat': '30.61', 'lon': '-96.34', 'passengersOnboard': 21, 'passengerCapacity': 42, 'lastGpsDate': '2026-11-07T12:00:00'}, {'name': 'bad', 'lat': 'invalid', 'lon': 0}]}]}]}
    rows = flatten_vehicles(payload)
    assert len(rows) == 1
    assert rows[0]['route'] == '12'
    assert rows[0]['direction'] == 'outbound'
    assert rows[0]['load_factor'] == 0.5
    assert rows[0]['provenance'] == 'MEASURED · APC'
    assert rows[0]['gps_age_s'] is not None
    assert flatten_vehicles(None) == []


def test_verified_collector_envelope():
    payload = [{'routeKey': 'route-guid', 'routeShortName': '12', 'vehiclesByDirections': [{'directionKey': 'dir-guid', 'vehicles': [{'key': 'bus-guid', 'name': 'B2011', 'passengersOnboard': 7, 'passengerCapacity': 25, 'location': {'latitude': 30.615, 'longitude': -96.34, 'lastGpsDate': '2026-11-07T12:00:00'}}]}]}]
    rows = flatten_vehicles(payload, '2026-11-07T19:00:00+00:00')
    assert rows[0]['route'] == '12'
    assert rows[0]['load_factor'] == 7 / 25
    assert rows[0]['ts_gps'] == '2026-11-07T18:00:00+00:00'
