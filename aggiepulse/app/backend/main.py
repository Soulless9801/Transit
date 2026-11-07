import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .data import VehicleCache

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
app = FastAPI(title="AggiePulse")
cache = VehicleCache()


def static(name):
    path = FRONTEND / "public/static" / name
    if not path.exists():
        raise HTTPException(503, "Run app/scripts/export_gtfs_static.py first")
    return json.loads(path.read_text())


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/routes")
def routes():
    return static("routes.json")


@app.get("/api/stops")
def stops():
    return static("stops.geojson")


from .datasets import Datasets, plan

datasets = Datasets()


@app.get("/api/vehicles/latest")
def vehicles():
    return datasets.get("vehicles")["data"]


@app.get("/api/predictions")
def predictions():
    return datasets.get("predictions")["data"]


@app.get("/api/events")
def events():
    return datasets.get("events")


@app.get("/api/alerts")
def alerts():
    return datasets.get("alerts")


@app.get("/api/history")
def history():
    return datasets.get("history")


@app.get("/api/meta")
def meta():
    return {
        name: {k: v for k, v in datasets.get(name).items() if k != "data"}
        for name in ("vehicles", "predictions", "events", "alerts", "history")
    }


@app.get("/api/plan")
def trip_plan(origin: str, destination: str, hour: int = 12):
    return plan(origin, destination, predictions(), hour)


# Mount last so API errors are never swallowed by frontend serving.
if (FRONTEND / "dist").exists():
    app.mount("/", StaticFiles(directory=FRONTEND / "dist", html=True), name="frontend")
