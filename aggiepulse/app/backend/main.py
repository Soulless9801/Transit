from datetime import datetime, timezone
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from .data import VehicleCache

FRONTEND = Path(__file__).resolve().parents[1] / 'frontend'
app = FastAPI(title='AggiePulse')
cache = VehicleCache()


def static(name):
    path = FRONTEND / 'public/static' / name
    if not path.exists():
        raise HTTPException(503, 'Run app/scripts/export_gtfs_static.py first')
    return json.loads(path.read_text())


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.get('/api/routes')
def routes():
    return static('routes.json')


@app.get('/api/stops')
def stops():
    return static('stops.geojson')


@app.get('/api/vehicles/latest')
def vehicles():
    try:
        return cache.latest()
    except (NotImplementedError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get('/api/predictions')
def predictions():
    now = datetime.now(timezone.utc).isoformat()
    return [{'scoring_ts': now, 'route': r['route_short_name'], 'direction': direction, 'horizon_min': h, 'pred_p10': 0.15, 'pred_p50': 0.4, 'pred_p90': 0.7, 'p_crowd': 0.12, 'top_factors': [{'family': 'Time', 'feature': 'mock_weekend', 'value': 1, 'contribution': -0.1}], 'model_version': 'mock-v0', 'provenance': 'SIMULATED'} for r in routes() for direction in ('0', '1') for h in (15, 30, 60)]


# Mount last so API errors are never swallowed by frontend serving.
if (FRONTEND / 'dist').exists():
    app.mount('/', StaticFiles(directory=FRONTEND / 'dist', html=True), name='frontend')
