# AggiePulse

How full Texas A&M buses are right now, and how crowded they're likely to be in 15–60 minutes.

**Site:** https://aggiepulse-n5me.onrender.com (public, read-only)

## Using the site

| Page | Use it to |
|---|---|
| **Live map** | See every bus and how full it is. Search routes by name; Replay plays back recorded trips. |
| **Forecast** | See expected fullness in 15, 30 and 60 minutes, the likely range, the chance of crowding, and why. |
| **Events** | See upcoming campus events and service alerts, and the nearest routes they affect. |
| **Plan my trip** | Pick two stops by name and compare options by wait, ride, walk and fullness. |
| **History** | Compare past passenger counts with what the model predicted. |

Each page link can be shared (e.g. `/#forecast`). Times are Central. Every number is labelled:

- **Measured:** the buses' own passenger counters (vendor accuracy unverified).
- **Derived:** computed from GPS, e.g. bunching.
- **Predicted:** model output.
- **Simulated:** modelled history or assumed effects, not observations.

The status box at the top shows how fresh the data is, and says when no buses are scheduled.

## How it works

1. **Collect.** A laptop polls TripSpark's public map feed every 30 s. Daily jobs pull TAMU GTFS, the campus events calendar, weather (Open-Meteo, NWS) and NTD ridership. Files go to a Unity Catalog Volume.
2. **Process.** Databricks Lakeflow pipelines (Auto Loader → Bronze → Silver → Gold) clean the data, and build stop visits, headways and 15-minute route-direction windows.
3. **Forecast.** LightGBM quantile models (p10/p50/p90) plus a crowding classifier, one set per horizon. They're tracked in MLflow, registered in Unity Catalog, and must beat persistence and seasonal baselines. Scoring runs every 15 min, and retraining runs nightly.
4. **Serve.** FastAPI and React/MapLibre, running as a Databricks App and as a public Render mirror. A Genie space and an AI/BI dashboard sit on the Gold tables.

## Running it

Code is in `aggiepulse/`. Use the Databricks CLI profile `transit`.

- **Laptop processes** (keep them running for live buses), from `aggiepulse/`:
  ```sh
  scripts/ops/start.sh             # collector, sync to Databricks, live publisher
  scripts/ops/start.sh keepalive   # also stop Render's free tier sleeping
  scripts/ops/status.sh            # what's running, and the age of the last bus poll
  scripts/ops/stop.sh              # stop everything (finishes the current poll/upload)
  scripts/ops/install_launchd.sh   # one-time: daily batch download at 06:00
  ```
  You can name single services: `start.sh sync`, `stop.sh publisher`, and so on. Logs are in `aggiepulse/data/logs/`.
- **Deploy Databricks** (pipelines, jobs, models, app):
  ```sh
  npm run build --prefix app/frontend && databricks bundle deploy -p transit
  databricks apps deploy aggiepulse -p transit \
    --source-code-path /Workspace/Users/<redacted-email>/.bundle/aggiepulse/transit/files/app
  ```
- **Public mirror:** `render.yaml` builds `aggiepulse/app/Dockerfile`, and each push to `main` redeploys it. It signs in as the read-only service principal `aggiepulse-public`, using the `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET` environment variables in Render.
- **Databricks app access:** the original app (https://aggiepulse-7474654155800707.aws.databricksapps.com) needs a workspace login. To give someone access:
  ```sh
  databricks users create --user-name someone@example.com -p transit
  databricks apps update-permissions aggiepulse -p transit \
    --json '{"access_control_list":[{"user_name":"someone@example.com","permission_level":"CAN_USE"}]}'
  ```
- **Tests:** `uv run pytest tests -q` and `npm test --prefix app/frontend`.

## Caveats

- Collection started 2026-11-07. The models are trained mostly on simulated history calibrated to NTD ridership, so treat forecasts as indicative until weekday data builds up.
- Event impact (e.g. Tennessee at Kyle Field, 2026-11-14) is a simulated prior, not a measured effect.
- On TAMU Wi-Fi, the campus DNS filter blocks the Databricks app link; use the Render link.
- Data: TAMU Transportation Services (GTFS, TripSpark), TAMU events calendar, Open-Meteo (CC BY 4.0), NWS, National Transit Database.
