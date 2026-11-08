# AggiePulse

Live occupancy and short-term crowding forecasts for Texas A&M buses.

**Site:** https://aggiepulse-7474654155800707.aws.databricksapps.com (sign in with a Databricks account that has access to the `transit` workspace).

## Pages

| Page | What it shows |
|---|---|
| **Live map** | Measured bus fullness, named and searchable route chips, route forecasts, event markers and service alerts. A clear no-service message gives the next scheduled start. Replay has a labelled time slider, play/pause and speed controls for recorded trips. |
| **Forecast** | Expected fullness in 15, 30 and 60 min, a likely range, chance of crowding, and readable reasons such as classes letting out or rain. Expired forecasts are hidden; outside service hours the page explains when forecasts resume. |
| **Events** | Dated event cards with readable venue names, expected demand and naturally sorted nearby route chips with “Show all”. The Tennessee game is featured first. Service alerts show dates, affected routes and an expandable notice without internal stop IDs. |
| **Plan my trip** | Search stop names, with route or geographic context for duplicates. Compare typical wait, ride, walking and fullness, plus named boarding and arrival stops. The planner explains that its estimates are not date-specific live departures. |
| **History** | Real passenger readings and busy bus fullness over time, with named routes. Forecast comparisons explain typical error and likely-range coverage. Empty scheduled windows are excluded; missing readings and insufficient comparisons have clear explanations. |

Each page has one “About this data” disclosure for source details, model assumptions and fallbacks. Small Measured, Predicted and Simulated badges explain the numbers. All times use Central time, and the layouts work on phones and desktops.

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

## Public mirror (no login)

`render.yaml` deploys the same app to Render as a public, read-only copy. It signs in to Databricks as the service principal `aggiepulse-public`, which can only `SELECT` the tables the app shows and read `transit.raw.live`, and caches each dataset for 30 s. To recreate it: create a service principal with the `databricks-sql-access` entitlement, apply those grants plus `CAN_USE` on the warehouse, create an OAuth secret, then in Render choose New → Blueprint, pick this repo, and paste the client ID and secret when asked. The laptop collector must be running for live buses.

## Caveats

- Collection started 2026-11-07, so real data is still thin. The models are trained mostly on simulated history calibrated to TAMU's NTD ridership. Treat forecasts as indicative until weekday data accumulates.
- Event impact (e.g. the 2026-11-14 Tennessee game at Kyle Field) is a simulated prior, not a measured effect.
- Data sources: TAMU Transportation Services (GTFS, TripSpark), TAMU events calendar, Open-Meteo (CC BY 4.0), NWS, and the National Transit Database.

For development and operations, see `aggiepulse/app/README.md`, `aggiepulse/scripts/platform/README.md` and `PLAN.MD`.
