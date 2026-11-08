# AggiePulse demo script (5 minutes)

Workspace: `https://dbc-595c228e-76f6.cloud.databricks.com` (profile `transit`). Open every tab below before you start.

| Tab | Link |
|---|---|
| App | https://aggiepulse-7474654155800707.aws.databricksapps.com |
| Ops dashboard | https://dbc-595c228e-76f6.cloud.databricks.com/dashboardsv3/01f1db0925b81a47ad018d8fc898deaa/published?w=7474654155800707 |
| Genie | https://dbc-595c228e-76f6.cloud.databricks.com/genie/rooms/01f1db093b021033aabbdb939c631a81?w=7474654155800707 |
| Core pipeline | https://dbc-595c228e-76f6.cloud.databricks.com/pipelines/206b4370-b5cc-48eb-8eba-932dcea5dc95 |
| Ops pipeline | https://dbc-595c228e-76f6.cloud.databricks.com/pipelines/3db97dca-16f4-4d27-88eb-3563ac1749ee |
| MLflow | https://dbc-595c228e-76f6.cloud.databricks.com/ml/experiments/2943343622901154 |
| Lineage | Catalog Explorer → `transit.gold.predictions` → Lineage |

## 1 hour before

1. **Health:** the header chip in the app is green; `cat aggiepulse/data/collector_heartbeat.json` shows recent successes; `SELECT * FROM transit.gold.ops_health ORDER BY checked_ts DESC LIMIT 1`.
2. **Freeze:** `cd aggiepulse && DATABRICKS_CONFIG_PROFILE=transit uv run --with databricks-sdk python scripts/ops/freeze.py --label demo`. Note the snapshot id.
3. **Rehearse Genie** with the exact question below, so you know the answer.
4. **Network:** TAMU's campus network (Cisco Umbrella) has blocked the app's hostname as malware. A Firefox certificate warning or a `malware.opendns.com` page means that block. Don't click through it; present from a phone hotspot.
5. **Fallback:** if live data looks thin (nights, weekends) or TripSpark is down, use **Replay** on the live map instead of live buses. Keep a screen recording of a good run as the last resort.

## Script

1. **Hook (30 s) · App → Live map.** "TAMU moves about a million riders a month (NTD). These are the buses right now, and the ring is how full each one is." Click a bus.
2. **Honesty (30 s).** Point at the labels: `MEASURED · APC` is the buses' own passenger counters, `DERIVED · GPS`, `PREDICTED`, and `SIMULATED`. "We only had live data from Nov 7, so history before that is simulated and calibrated to NTD, and it's labelled everywhere."
3. **Databricks depth (90 s).**
   - Core pipeline graph: Auto Loader → Bronze → Silver, with expectations (APC stuck-counter warnings, bounding box, GPS age).
   - Lineage from the raw Volume to `gold.predictions`.
   - MLflow: B0 persistence vs B1 seasonal vs LightGBM per horizon, with observed and simulated metrics kept separate; models in UC as `transit.ml.crowding_h30@champion`.
   - Genie: *"Which routes bunched most this weekend?"*
4. **Forecast + why (60 s) · App → Forecast.** The highest-risk route in 30 min, its p10–p90 range, and the factor chips (class change, weather, recent load).
5. **Event intelligence (60 s) · App → Events.** Tennessee at Kyle Field, Sat Nov 14: the routes it affects and the detours TripSpark already announced. "This impact is a simulated prior until we collect game-day data." If the demo is after Nov 14, use History or Replay for the real game day.
6. **Student value (30 s) · App → Plan my trip.** Pick two stops and read the top option's one-line reason.

## Be ready to answer honestly

- **Accuracy on real riders:** see the model card (`aggiepulse/src/aggiepulse/ml/model_card.md`). Observed n is small, and the p10–p90 ranges currently cover only ~30% of outcomes, against the 80% target. Interval calibration switches on once there are ≥200 observed windows across ≥2 weekdays.
- **APC accuracy:** vendor counters, unvalidated. We flag stuck counts but can't audit them.
- **Data access:** TripSpark's public map feed (`/RouteMap` only; robots.txt allows it), polled every 30 s, which is fewer requests than one open browser tab. We've emailed TAMU Transportation.
