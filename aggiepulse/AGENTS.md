# AggiePulse — agent conventions

Texas A&M bus crowding forecasts on Databricks. The full plan is `../PLAN.MD`; read the sections your task names.

## Layout
- Python package `aggiepulse` (uv, src layout, Python ≥3.12): `src/aggiepulse/{common,collector,ingest,...}`.
- `seeds/`: hand-curated CSVs and `event_filters.yaml` (see `seeds/README.md`).
- `app/`: FastAPI backend and React/MapLibre frontend (see `app/README.md`).
- `pipelines/`: Lakeflow Declarative Pipeline source (PySpark).
- `resources/*.yml`: Asset Bundle resources, **one file per workstream**. `databricks.yml` is shared; don't edit it unless your task says so.
- Tests: `tests/<area>/`. Run `uv run pytest tests/<area> -q` and `uv run ruff check <your paths>`.

## Databricks
- CLI profile `transit` (default). Always pass `-p transit` or set `DATABRICKS_CONFIG_PROFILE=transit`. The CLI is at `/opt/homebrew/bin/databricks`.
- Workspace is **serverless only**: no classic clusters. Use serverless jobs, serverless Lakeflow pipelines, and the Serverless Starter Warehouse `3c5b1dbf21774cde`.
- Unity Catalog: catalog `transit`. Schemas: `raw` (Volume `landing`), `bronze`, `silver`, `gold`, `ml`. The plan's `aggiepulse.<schema>` names map to `transit.<schema>`.
- Raw files: `/Volumes/transit/raw/landing/<source>/date=YYYY-MM-DD/...`. Code reads the root from `aggiepulse.common.paths.data_root()`; on Databricks set `AGGIEPULSE_DATA_ROOT=/Volumes/transit/raw`.
- **Raw files are immutable once they reach the Volume** (Auto Loader assumes this). The laptop collector appends to the current hour's file, so only completed hours may be uploaded.
- Bundle: `cd aggiepulse && databricks bundle validate|deploy -p transit`. Other agents deploy too. If you hit the deployment lock, wait and retry. Never `bundle destroy`, and never delete resources you don't own.
- Shared SQL from the CLI: `databricks api post /api/2.0/sql/statements -p transit --json '{"warehouse_id":"3c5b1dbf21774cde","statement":"...","wait_timeout":"50s"}'`.

## Data rules (from the plan)
- Store timestamps as UTC. TripSpark `lastGpsDate` is America/Chicago local time with no offset; use `aggiepulse.collector.parse`.
- Every modelled row carries `data_origin ∈ {'observed','simulated'}`. Never mix simulated data into observed metrics.
- Provenance labels in the UI: `MEASURED · APC`, `DERIVED · GPS`, `PREDICTED`, `SIMULATED`.
- Never call TripSpark `/Schedule/*` (robots.txt disallows it). Keep live calls to TripSpark minimal; the laptop collector already polls every 30 s.

## Parallel work
Several agents share this checkout. Edit only the paths your task assigns. Add Python deps only with `uv add <pkg>` (retry if the lock is busy). Do not `git commit`; the coordinator commits.
