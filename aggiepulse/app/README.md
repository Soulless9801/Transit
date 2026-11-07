# AggiePulse app

Run from the Python package root (`aggiepulse/`), with Python 3.12+ and Node 20.19+:

```sh
uv sync
uv run python app/scripts/export_gtfs_static.py
cd app/frontend
npm install
npm run build
cd ../..
uv run uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. For frontend development run uvicorn as above and, in another terminal, `cd app/frontend && npm run dev`. Vite proxies `/api` to port 8000. Static GeoJSON is served directly by Vite from `public/static`.

The export reuses the newest ZIP in `data/landing/gtfs/`, otherwise `app/.cache/`, otherwise downloads the TAMU feed. Pass `--cache` or `--output` to override. Shapes are grouped by route/direction; multiple shapes produce a MultiLineString and an array of shape IDs. Stops contain served route IDs. Static exports are included for a ready-to-run demo. Attribute TAMU Transportation Services; basemap attribution is displayed by MapLibre.

`AGGIEPULSE_BACKEND=local` (default) reads the latest successful complete poll from `data/landing/tripspark_vehicles/date=*/**/*.jsonl.gz`. No landing data produces an empty vehicle list. Failed polls fall back to the most recent successful poll; GPS age remains visible so stale observations can be identified. Responses are cached in memory for 15 seconds per worker. Passenger counts carry `MEASURED · APC`; GPS ages carry `DERIVED · GPS`. The UI displays unknown route vehicles even if no GTFS match exists. Forecast samples always carry `SIMULATED`; the four additional pages are placeholders.

API: `/api/health`, `/api/routes`, `/api/stops`, `/api/vehicles/latest`, `/api/predictions`. Tests: `uv run pytest tests/app -q` (export static first).

## Databricks deployment TODO

Use the package root as the App source directory and copy `app/app.yaml` to the deployed source root as `app.yaml` during packaging. Build the frontend before upload; include `app/frontend/dist`, static exports, `pyproject.toml`, and `uv.lock`. The command uses `DATABRICKS_APP_PORT` (fallback 8000), and selects `AGGIEPULSE_BACKEND=databricks`.

The SQL adapter is deliberately unimplemented and returns HTTP 503. Implement `DatabricksBackend.latest` using `databricks-sql-connector`, App service-principal OAuth, `DATABRICKS_HOST`, and `DATABRICKS_WAREHOUSE_ID`, executing `SELECT * FROM silver.vehicle_latest`. Normalize SQL rows to the local vehicle response fields and attach APC provenance. Grant the App principal warehouse and table access. Wire `gold.predictions` after scoring exists; replace the simulated endpoint and provenance together. No warehouse credentials are embedded.
