# AggiePulse app

From the package root, run `uv run python app/scripts/export_gtfs_static.py`, `npm ci --prefix app/frontend`, `npm run build --prefix app/frontend`, then `uv run uvicorn app.backend.main:app --port 8000`. Vite development mode proxies `/api` to port 8000.

`AGGIEPULSE_BACKEND=databricks` uses the SDK Statement Execution API with Apps environment OAuth credentials, or local profile `transit`, and warehouse `3c5b1dbf21774cde`. Every dataset is cached for 15 seconds. `/api/meta` reports backend, source and fallback reason. Dataset endpoints return explicit source fields. Missing tables and query/authentication failures use local landing data or packaged frozen snapshots; predictions use deterministic mock quantiles labelled SIMULATED. Snapshot GPS timestamps and ages remain visible. Empty SQL results remain empty rather than silently switching sources.

APIs: `/api/health`, `/api/meta`, `/api/routes`, `/api/stops`, `/api/vehicles/latest`, `/api/predictions`, `/api/events`, `/api/alerts`, `/api/history`, `/api/plan?origin=STOP_ID&destination=STOP_ID&hour=12`.

Forecast shows horizon strips, ranked route-directions, quantile bands and factor contributions. Events applies seed drop rules and local venue lookup, lists nearby routes and vendor alerts, and highlights the Tennessee game with a simulated prior. The trip planner searches direct route patterns and stop alternatives within 400 m; score is half scheduled headway + ride + walking + 20 × predicted load. Hourly headways are typical across feed trips, not a service-calendar-specific departure guarantee. History uses observed APC windows only, deduplicated by vehicle/GPS timestamp, with sample counts. A historical forecast archive is not yet available, so the UI reports that aligned error/comparison is unavailable rather than inventing historical predictions.

`planner.json` contains route-direction stop patterns, relative travel seconds and hourly headways. Static exports attribute TAMU Transportation Services. MapLibre loads in a separate chunk. The map displays route geometry, measured buses, active alert route highlights (not replacement detour geometry), and significance-scaled event venue markers.

Deployment: build the frontend first, then `databricks bundle deploy -p transit`, start `databricks apps start aggiepulse -p transit`, and deploy with `databricks apps deploy aggiepulse -p transit --source-code-path /Workspace/Users/<redacted-email>/.bundle/aggiepulse/transit/files/app`. `resources/app.yml` explicitly includes ignored frontend dist files. Apps installs `requirements.txt` and runs `backend.main:app`. The app resource grants CAN_USE on the warehouse. UC catalog/schema/SELECT grants must be applied to the generated service principal; they have been applied for this workspace.

Validation: `uv run pytest tests/app -q`, `uv run ruff check app/backend app/scripts tests/app`, `npm run build --prefix app/frontend`.
