# AggiePulse demo (5 minutes)

| Tab | Link |
|---|---|
| App | https://aggiepulse-n5me.onrender.com |
| Ops dashboard | https://dbc-595c228e-76f6.cloud.databricks.com/dashboardsv3/01f1db0925b81a47ad018d8fc898deaa/published?w=7474654155800707 |
| Genie | https://dbc-595c228e-76f6.cloud.databricks.com/genie/rooms/01f1db093b021033aabbdb939c631a81?w=7474654155800707 |
| Core pipeline | https://dbc-595c228e-76f6.cloud.databricks.com/pipelines/206b4370-b5cc-48eb-8eba-932dcea5dc95 |
| MLflow | https://dbc-595c228e-76f6.cloud.databricks.com/ml/experiments/2943343622901154 |
| Lineage | Catalog Explorer → `transit.gold.predictions` → Lineage |

## Before

- The app's status box is green, and `aggiepulse/scripts/ops/keepalive.sh status` shows recent 200s.
- Use the Render link on campus; TAMU Wi-Fi blocks the Databricks app link.
- Ask Genie the question below once, so you know the answer.
- If live data is thin (nights, weekends), use **Replay** on the live map. Keep a screen recording as a last resort.

## Script

1. **Hook (30 s) · Live map.** "TAMU carries about a million riders a month. These are the buses now; the ring is how full each one is." Click a bus.
2. **Honesty (30 s).** Point at the Measured / Predicted / Simulated badges. "Real data starts Nov 7. Earlier history is simulated, calibrated to NTD, and labelled everywhere."
3. **Databricks (90 s).** Pipeline graph and its data-quality expectations, then lineage to `gold.predictions`. In MLflow, the baselines vs LightGBM per horizon, with the model in UC as `@champion`. In Genie: *"Which routes bunched most this weekend?"*
4. **Forecast (60 s).** The highest-risk route in 30 min, its likely range, and the reasons shown.
5. **Events (60 s).** The Tennessee game at Kyle Field (Nov 14): nearest routes and TripSpark's detour alerts. Say "the impact is a simulated prior."
6. **Plan my trip (30 s).** Pick two stops and read the top option.

## Hard questions

- **Accuracy on real riders?** Too little data yet. Its likely ranges cover about 30% of real outcomes, against an 80% target. Calibration switches on automatically at 200 real windows across 2 weekdays.
- **Passenger counter accuracy?** These are the vendor's counters and we can't audit them. We flag stuck counts.
- **Data access?** We use TripSpark's public map feed only, which robots.txt allows. We poll every 30 s, fewer requests than one open browser tab, and we've emailed TAMU Transportation.
