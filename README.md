# AggiePulse

Live occupancy and short-term crowding forecasts for Texas A&M buses.

**Site:** https://aggiepulse-7474654155800707.aws.databricksapps.com (sign in with a Databricks account that has access to the `transit` workspace).

## Pages

| Page | What it shows |
|---|---|
| **Live map** | Active buses refresh every 15 s; the header shows snapshot age, last scoring time and pipeline lag. Circular rings show measured load; route lines show predicted +30-minute load (busiest direction): green under 50%, amber 50–74%, red 75%+, grey no data. Click a route or its Forecast button for +15/+30/+60 medians, intervals and factors. Route checkboxes, alert highlights and event venues remain available. REPLAY mode has a time slider, play/pause and speed control over up to 7 days of collected positions, with an offline observed snapshot fallback. Current forecasts are hidden during replay. |
| **Forecast** | Predicted load for each route 15, 30 and 60 minutes ahead (darker means fuller), the 5 routes most likely to be crowded in 30 min, a likely range (p10–p90) for each, and the top reasons behind each prediction (e.g. class change, event, weather). |
| **Events** | Significant campus events in the next 7 days, routes with stops within 600 m, mapped directions and service alerts. Event-window forecast uplift appears where available and is labelled SIMULATED PRIOR; missing uplift is explicit. The Tennessee card covers Saturday Nov 14, 2026 at Kyle Field, kickoff TBA. |
| **Plan my trip** | Pick an origin and destination stop. It lists route and departure-time options, ranked by wait, ride time, walking and expected crowding, each with a one-line reason. |
| **History** | Existing measured-load charts and route/time hotspots with reading counts, plus predicted-vs-observed load for a chosen route and +15/+30/+60 horizon: p10–p90 band, observed p90 line, MAE, interval coverage and matched-window n. Fewer than 5 matches shows an insufficient-data state. |

## Labels

Every number carries one of these labels:

- `MEASURED · APC`: passenger counts reported by the buses' own counters (vendor accuracy unverified).
- `DERIVED · GPS`: computed from bus positions, e.g. headways and bunching.
- `PREDICTED`: model output.
- `SIMULATED`: based on modelled history or assumed effects, not observations.

## Giving someone access

The site isn't public. Each viewer needs a login to the `transit` Databricks workspace plus permission to use the app. A workspace admin runs the following, or does it in the UI under Settings → Identity and access → Users, then Compute → Apps → aggiepulse → Permissions:

```sh
# 1. Add them to the workspace (they get an email invite)
databricks users create --user-name someone@example.com -p transit

# 2. Let them open the app (update-permissions adds; set-permissions would replace everyone)
databricks apps update-permissions aggiepulse -p transit \
  --json '{"access_control_list":[{"user_name":"someone@example.com","permission_level":"CAN_USE"}]}'
```

Viewers don't need table permissions, because the app reads data as its own service principal. To remove access, delete the user, or rerun step 2 with the permission removed using `set-permissions`.

## Caveats

- Collection started 2026-11-07, so real data is still thin. The models are trained mostly on simulated history calibrated to TAMU's NTD ridership. Treat forecasts as indicative until weekday data accumulates.
- Event impact (e.g. the 2026-11-14 Tennessee game at Kyle Field) is a simulated prior, not a measured effect.
- Data sources: TAMU Transportation Services (GTFS, TripSpark), TAMU events calendar, Open-Meteo (CC BY 4.0), NWS, and the National Transit Database.

For development and operations, see `aggiepulse/app/README.md`, `aggiepulse/scripts/platform/README.md` and `PLAN.MD`.
