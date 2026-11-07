# Model card: AggiePulse crowding forecasts (`transit.ml.crowding_h15/_h30/_h60`)

**Status: a demo model trained on a simulated prior.** Its only test on real
riders so far is a few minutes of weekend APC data, which is too little to
conclude anything (see the metrics). Every number it produces should be shown
as **PREDICTED**, and the app should say it was trained on simulated history.

## Intended use

- What it predicts: for each TAMU bus route-direction, the 90th-percentile
  APC-reported load factor (onboard ÷ capacity) in the 15-minute window that
  starts 15, 30 or 60 minutes after t0. It gives p10/p50/p90 values and
  P(max load ≥ 0.75) for that window. It also gives the top three signed
  feature-family contributions and an *event uplift* (attribution, not
  causation).
- Who uses it: the AggiePulse app (Forecast page, trip planner) through
  `transit.gold.predictions`, `predictions_latest` and `pred_vs_actual`.
- Not for: operational decisions (bus dispatch, safety, capacity planning);
  claims about how big real event or weather effects are; any stop-level
  demand estimate.

## Data

| | Source | Dates (UTC) | n |
|---|---|---|---|
| Simulated train | `gold.training_set` v4, `split='train'` | 2026-09-06 → 2026-10-20 01:15 | 168,400 rows; 55,026 h30 labels (745 crowded) |
| Simulated validation | `split='validation'` (later in time) | 2026-10-20 01:15 → 2026-10-29 11:00 | 12,974 h30 labels (71 crowded) |
| Simulated test | `split='test'` (latest) | 2026-10-29 11:00 → 2026-11-07 20:45 | 12,945 h30 labels (99 crowded) |
| Observed holdout | TripSpark APC via `gold.route_dir_15m`, `split='observed_holdout'` | 2026-11-07 (Saturday afternoon only) | 56 rows; h15/h30/h60 labels 45 / 23 / 19 (0 crowded); t0 19:30–20:30 |

- Simulated history comes from `aggiepulse.sim.simulate_history` (assumptions in
  `src/aggiepulse/sim/ASSUMPTIONS.md`). It is calibrated so that total boardings
  match NTD monthly UPT exactly. Its time profile, class-change waves, route
  groups, and event and weather effects are **our assumptions**. About 1.2% of
  in-service weekday windows are crowded (3.2% on on-campus routes), and that
  rate is also an assumption.
- Observed data never fits or selects anything. It is reported on its own line
  with its n.
- Splits are forward in time only. Hyperparameters and tree counts are chosen on
  validation, and the final models are fit on train only, the same data as the
  baselines. Labels whose windows cross a split boundary are dropped.

## Models

- **B0 persistence**: p50 = p90 load of the last completed window [t0−15, t0).
  The interval comes from train residual quantiles. If that window has no data,
  B0 uses B1.
- **B1 seasonal profile**: the train-split mean label for route × direction ×
  day-type × 15-minute slot of the target window.
- **LightGBM**: one set per horizon, across all routes, with route and direction
  as categorical features. Quantile regressors (α = .1/.5/.9) predict p90 load.
  A binary classifier predicts crowding; it up-weights positives (capped at 25×)
  and corrects back to prior odds at inference. A 12-trial Optuna TPE
  search tunes each horizon, logged as nested MLflow runs.
- **Explanations**: `pred_contrib` (exact TreeSHAP) from the p50 model, summed
  into the families Time, Class change, Event, Weather, Recent load,
  Bunching-service, Seasonal and Static. Each of the top three families is named
  by its largest feature.
- **Event uplift**: p50 minus p50 with event features neutralised (exposure set
  to 0, venue 'none', event timings null). It is a **simulated prior**.

## Metrics (holdout only; load-factor units)

| Origin | Horizon | Model | n | MAE | RMSE | Pinball p50 | p10–p90 coverage | Crowded (n) | PR-AUC | Brier |
|---|---|---|---|---|---|---|---|---|---|---|
| simulated test | 15 min | B0 | 12960 | 0.072 | 0.115 | 0.0362 | 83% | 99 | 0.063 | 0.0073 |
| simulated test | 15 min | B1 | 12960 | 0.081 | 0.117 | 0.0406 | 63% | 99 | 0.031 | 0.0085 |
| simulated test | 15 min | LightGBM | 12960 | 0.047 | 0.076 | 0.0234 | 72% | 99 | 0.111 | 0.0074 |
| simulated test | 30 min | B0 | 12945 | 0.078 | 0.123 | 0.0392 | 83% | 99 | 0.051 | 0.0074 |
| simulated test | 30 min | B1 | 12945 | 0.081 | 0.117 | 0.0406 | 63% | 99 | 0.031 | 0.0085 |
| simulated test | 30 min | LightGBM | 12945 | 0.047 | 0.078 | 0.0237 | 72% | 99 | 0.130 | 0.0074 |
| simulated test | 60 min | B0 | 12878 | 0.073 | 0.118 | 0.0364 | 82% | 99 | 0.046 | 0.0075 |
| simulated test | 60 min | B1 | 12878 | 0.082 | 0.117 | 0.0408 | 63% | 99 | 0.031 | 0.0085 |
| simulated test | 60 min | LightGBM | 12878 | 0.049 | 0.083 | 0.0245 | 72% | 99 | 0.099 | 0.0075 |
| **observed holdout (too small to conclude anything)** | 15 min | B0 | 45 | 0.090 | 0.119 | 0.0451 | 51% | 0 | n/a (no positives) | 0.0001 |
| **observed holdout (too small to conclude anything)** | 15 min | B1 | 45 | 0.124 | 0.144 | 0.0619 | 22% | 0 | n/a (no positives) | 0.0001 |
| **observed holdout (too small to conclude anything)** | 15 min | LightGBM | 45 | 0.120 | 0.148 | 0.0599 | 7% | 0 | n/a (no positives) | 0.0000 |
| **observed holdout (too small to conclude anything)** | 30 min | B0 | 23 | 0.074 | 0.107 | 0.0368 | 70% | 0 | n/a (no positives) | 0.0001 |
| **observed holdout (too small to conclude anything)** | 30 min | B1 | 23 | 0.121 | 0.142 | 0.0607 | 22% | 0 | n/a (no positives) | 0.0001 |
| **observed holdout (too small to conclude anything)** | 30 min | LightGBM | 23 | 0.143 | 0.161 | 0.0717 | 4% | 0 | n/a (no positives) | 0.0000 |
| **observed holdout (too small to conclude anything)** | 60 min | B0 | 19 | 0.098 | 0.121 | 0.0488 | 42% | 0 | n/a (no positives) | 0.0001 |
| **observed holdout (too small to conclude anything)** | 60 min | B1 | 19 | 0.126 | 0.145 | 0.0631 | 11% | 0 | n/a (no positives) | 0.0001 |
| **observed holdout (too small to conclude anything)** | 60 min | LightGBM | 19 | 0.063 | 0.074 | 0.0315 | 32% | 0 | n/a (no positives) | 0.0000 |

Promotion gate: LightGBM must beat B1 on simulated-test MAE at 30 minutes.
**Passed** (MLflow run `d7a4c0e82ff44fd687bbaecbb6510236`, training job run 1102403897821456): LightGBM simulated-test MAE at 30 minutes is 0.0474, against 0.0813 for B1 and 0.0783 for B0 (n = 12,945). Version 1 of each horizon is `@champion`. On the observed holdout B0 has the lowest MAE at 15 and 30 minutes, but n = 45 and n = 23 are too small to rank anything.

How to read this:
- The simulated lines only show that the model recovers the structure we put
  into the simulator. They say nothing about real riders.
- The observed lines have n ≤ 45, all from one Saturday afternoon
  with near-empty buses. **That is too small to conclude anything**, in either
  direction.
- One warning sign in that tiny sample: LightGBM's p10–p90 intervals cover
  only 4–32% of the observed outcomes, against 72% on simulated test data. The
  model's intervals are probably too narrow for real data. Recheck this once
  weekday data exists.
- The plan's acceptance bar is "beats B1 on observed MAE at 30 minutes, with
  ~80% p10–p90 coverage". It **cannot be evaluated yet**.

Plots for each horizon (residuals by hour, SHAP summary, calibration) are in the
MLflow run, under `plots/`.

## Limitations

- **APC accuracy is unvalidated.** Labels are vendor-reported `passengersOnboard`.
  No manual counts back them up.
- **Observed data covers weekends only so far.** Collection started on Saturday,
  2026-11-07. Weekday class-change behaviour, which drives most simulated
  crowding, has never been observed.
- **The simulator's assumptions drive the event, weather and class-change
  effects.** Uplift, "Class change" factors and P(crowded) mostly reflect
  `ASSUMPTIONS.md`, not evidence. The turnover assumption, which the data cannot
  pin down (1.5; we checked it against VRM), shifts the crowding rate about
  four-fold between 1.5 and 2.0.
- **Weather features are proxies.** Historical training rows use observed hourly
  weather in place of archived forecasts (`weather_*_is_observed_proxy`), which
  is hindsight. Live scoring uses real forecasts. The proxy flags are excluded
  from the model inputs so the model cannot learn the difference.
- **Event schedules are hindsight.** Most historical events have no publication
  snapshot (`event_schedule_is_hindsight`).
- **The simulated test period includes the NTD-driven November ridership drop.**
  B1, which is frozen at train time, is penalised for that level shift.
  Persistence and LightGBM adapt to it.
- **The seasonal feature is sparse.** It is keyed on t0's slot, and only slots
  with a first-stop departure have a value. `serves_kyle_field` and `serves_msc`
  are true for every route, so they carry no information.
- **Headway CV, bunching and delay are null in observed data**, because APC pings
  alone cannot establish them. Live predictions therefore see missing service
  features, which training rarely did.
- **The interval is too narrow.** p10–p90 coverage is below the nominal 80% even
  on simulated test data (see the table).

## How to retrain

```sh
cd aggiepulse
uv run pytest tests/ml tests/sim tests/features -q
databricks bundle deploy -p transit
databricks bundle run ml_train -p transit      # ml-data refresh → train → evaluate → register
```

- The `register` task registers a new version of each horizon. It moves
  `@champion` only when the gate passes, and records the comparison as
  model-version tags and in `registry/registration.json`.
- The scoring job (`aggiepulse-ml-score`, every 15 minutes at :08/:23/:38/:53
  UTC) always loads `@champion`.
- Local dry run:
  `uv run python -m aggiepulse.features.job --local-output DIR` then
  `uv run python src/aggiepulse/ml/job.py --step all --local-dir DIR --trials 2`
  (uses a SQLite MLflow store and skips the registry).
- Retrain when observed weekday data exists. Once enough observed windows are
  collected, move observed data into a forward observed validation split, and
  make the promotion gate use observed MAE.
