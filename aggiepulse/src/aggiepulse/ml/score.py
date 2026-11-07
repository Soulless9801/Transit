"""Batch scoring every 15 minutes: features as of t0 -> @champion models -> gold tables.

t0 is the end of the newest completed observed APC window (never later than
the current quarter-hour), so recent-load features are complete rather than
silently missing while the pipeline catches up. ``feature_staleness_min`` says
how old each route-direction's newest APC window is at scoring time.
Writes (append) ``gold.predictions`` and rebuilds ``gold.predictions_latest``
and ``gold.pred_vs_actual``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Load the deployed source snapshot even when a warm environment cached an older wheel.
sys.path.insert(0, sys.argv[sys.argv.index('--source-root')+1] if '--source-root' in sys.argv
                else str(Path(globals().get('__file__', sys.argv[0])).resolve().parents[2]))

import numpy as np
import pandas as pd

HORIZONS = (15, 30, 60)
MAX_STATE_LAG = pd.Timedelta(minutes=60)
PREDICTION_COLUMNS = ['scoring_ts', 't0', 'route_short_name', 'direction_id', 'direction_name',
                      'horizon_min', 'pred_p10', 'pred_p50', 'pred_p90', 'p_crowd', 'event_uplift',
                      'top_factors', 'model_name', 'model_version', 'feature_staleness_min',
                      'training_data_origin']


def prediction_schema():
    from pyspark.sql import types as t
    factor = t.StructType([t.StructField('family', t.StringType()), t.StructField('feature', t.StringType()),
                           t.StructField('value', t.DoubleType()), t.StructField('contribution', t.DoubleType())])
    kinds = {'scoring_ts': t.TimestampType(), 't0': t.TimestampType(), 'route_short_name': t.StringType(),
             'direction_id': t.IntegerType(), 'direction_name': t.StringType(), 'horizon_min': t.IntegerType(),
             'pred_p10': t.DoubleType(), 'pred_p50': t.DoubleType(), 'pred_p90': t.DoubleType(),
             'p_crowd': t.DoubleType(), 'event_uplift': t.DoubleType(), 'top_factors': t.ArrayType(factor),
             'model_name': t.StringType(), 'model_version': t.StringType(),
             'feature_staleness_min': t.DoubleType(), 'training_data_origin': t.StringType()}
    return t.StructType([t.StructField(name, kinds[name], True) for name in PREDICTION_COLUMNS])


def choose_t0(observed, now):
    """Newest completed observed window end, capped at the current quarter-hour."""
    floor = pd.Timestamp(now).floor('15min')
    if observed is None or observed.empty:
        return floor
    measured = observed[observed.data_origin.eq('observed') & observed.avg_load_factor.notna()]
    ends = pd.to_datetime(measured.window_start, utc=True)+pd.Timedelta(minutes=15)
    ends = ends[ends <= floor]
    if ends.empty or floor-ends.max() > MAX_STATE_LAG:
        return floor
    return ends.max()


def direction_names(gtfs):
    trips = gtfs['trips'].merge(gtfs['routes'][['route_id', 'route_short_name']], on='route_id')
    names = trips[['route_short_name', 'direction_id', 'trip_headsign']].drop_duplicates(
        ['route_short_name', 'direction_id'])
    names['direction_id'] = names.direction_id.astype(int)
    return names.rename(columns={'trip_headsign': 'direction_name'})


def score_frame(features, models, versions, gtfs, scoring_ts, t0):
    """Pure pandas: rows for each horizon whose target window has scheduled service."""
    from aggiepulse.features.inputs import schedule

    t0 = pd.Timestamp(t0)
    sched = schedule(gtfs, t0, t0+pd.Timedelta(minutes=max(HORIZONS)+15))
    sched['route_short_name'] = sched.route_short_name.astype(str)
    names = direction_names(gtfs)
    rows = []
    for horizon, model in models.items():
        target = t0+pd.Timedelta(minutes=horizon)
        served = sched[sched.window_start.eq(target) & sched.sched_trips.gt(0)][['route_short_name', 'direction_id']]
        subset = features.assign(route_short_name=features.route_short_name.astype(str)).merge(
            served, on=['route_short_name', 'direction_id']).reset_index(drop=True)
        if subset.empty:
            continue
        output = model.forecast(subset)
        frame = pd.DataFrame({
            'scoring_ts': scoring_ts, 't0': t0, 'route_short_name': subset.route_short_name.astype(str),
            'direction_id': subset.direction_id.astype(int), 'horizon_min': horizon,
            'pred_p10': output.p10, 'pred_p50': output.p50, 'pred_p90': output.p90,
            'p_crowd': output.p_crowd, 'event_uplift': output.event_uplift,
            'top_factors': output.top_factors, 'model_name': versions[horizon]['name'],
            'model_version': str(versions[horizon]['version']),
            'feature_staleness_min': subset.get('feature_staleness_min', pd.Series(np.nan, index=subset.index)),
            'training_data_origin': model.metadata.get('training_data_origin', 'simulated')})
        rows.append(frame)
    if not rows:
        return pd.DataFrame(columns=PREDICTION_COLUMNS)
    result = pd.concat(rows, ignore_index=True).merge(names, on=['route_short_name', 'direction_id'], how='left')
    return result[PREDICTION_COLUMNS]


def to_spark_rows(frame):
    rows = []
    for record in frame.to_dict('records'):
        values = []
        for column in PREDICTION_COLUMNS:
            value = record[column]
            if column in {'scoring_ts', 't0'}:
                value = pd.Timestamp(value).tz_convert('UTC').tz_localize(None).to_pydatetime()
            elif column == 'top_factors':
                value = [(f['family'], f['feature'], None if f['value'] is None else float(f['value']),
                          float(f['contribution'])) for f in value]
            elif column in {'direction_id', 'horizon_min'}:
                value = int(value)
            elif isinstance(value, (float, np.floating)):
                value = None if np.isnan(value) else float(value)
            values.append(value)
        rows.append(tuple(values))
    return rows


def load_champions(catalog):
    import mlflow
    from mlflow import MlflowClient

    mlflow.set_registry_uri('databricks-uc')
    client = MlflowClient()
    models, versions = {}, {}
    for horizon in HORIZONS:
        name = f'{catalog}.ml.crowding_h{horizon}'
        version = client.get_model_version_by_alias(name, 'champion')
        models[horizon] = mlflow.pyfunc.load_model(f'models:/{name}@champion').unwrap_python_model()
        versions[horizon] = {'name': name, 'version': version.version}
    return models, versions


def load_inputs(spark, catalog, data_root, seed_root):
    from aggiepulse.common.paths import REPO_ROOT
    from aggiepulse.features.event_exposure import prepare_events
    from aggiepulse.features.inputs import load_local, seed_file
    from aggiepulse.features.job import prefer_silver

    seed_root = Path(seed_root or REPO_ROOT/'seeds')
    try:
        seed_file(seed_root, 'academic_days.csv')
    except FileNotFoundError:
        seed_root = REPO_ROOT/'seeds'
    preloaded, lineage = prefer_silver({}, spark, catalog)
    if 'observed' not in preloaded:
        preloaded['observed'] = pd.DataFrame()  # never parse every raw APC file every 15 minutes
    inputs = load_local(data_root, seed_root, preloaded)
    events = inputs.get('events')
    if events is None:
        events = prepare_events(inputs['raw_events'], inputs['venues'], inputs['seed_root'])
    inputs['events'] = events
    return inputs, lineage


def run(spark, args):
    from aggiepulse.features.build_training_set import (
        build_inference_features,
        static_features,
    )
    from aggiepulse.features.event_exposure import exposure_from_gtfs

    scoring_ts = pd.Timestamp.now(tz='UTC')
    try:
        models, versions = load_champions(args.catalog)
    except Exception as exc:
        message = str(exc).lower()
        if not any(text in message for text in ('alias', 'not found', 'does not exist')):
            raise
        # No @champion is a legitimate registry state (gate failed or not trained yet).
        print(json.dumps({'scoring_ts': str(scoring_ts), 'rows': 0, 'note': f'no @champion: {exc}'[:500]}))
        return pd.DataFrame(columns=PREDICTION_COLUMNS)
    inputs, lineage = load_inputs(spark, args.catalog, args.data_root, args.seed_root)
    observed = inputs['observed']
    t0 = choose_t0(observed, scoring_ts)
    gtfs, events = inputs['gtfs'], inputs['events']
    season = models[HORIZONS[0]].season
    features = build_inference_features(
        observed, gtfs, season, inputs['academic'], inputs['periods'],
        lambda grid: exposure_from_gtfs(grid, events, gtfs), inputs['weather'],
        static_features(gtfs, inputs['venues']), t0, scoring_ts)
    if features.empty:
        print(json.dumps({'scoring_ts': str(scoring_ts), 't0': str(t0), 'rows': 0,
                          'note': 'no scheduled service in any target window'}))
        return pd.DataFrame(columns=PREDICTION_COLUMNS)
    predictions = score_frame(features, models, versions, gtfs, scoring_ts, t0)
    write(spark, predictions, args.catalog)
    summary = {'scoring_ts': str(scoring_ts), 't0': str(t0), 'rows': len(predictions),
               'route_directions': int(features[['route_short_name', 'direction_id']].drop_duplicates().shape[0]),
               'rows_with_recent_state': int(features.load_t0.notna().sum()),
               'median_feature_staleness_min': float(features.feature_staleness_min.median())
               if features.feature_staleness_min.notna().any() else None,
               'models': {str(k): v for k, v in versions.items()}, 'inputs': lineage}
    print(json.dumps(summary, indent=2, default=str))
    return predictions


def write(spark, predictions, catalog):
    if predictions.empty:
        return
    sdf = spark.createDataFrame(to_spark_rows(predictions), prediction_schema())
    gold = f'{catalog}.gold'
    sdf.write.format('delta').mode('append').saveAsTable(f'{gold}.predictions')
    sdf.write.format('delta').mode('overwrite').option('overwriteSchema', 'true').saveAsTable(
        f'{gold}.predictions_latest')
    spark.sql(f"""
        CREATE OR REPLACE TABLE {gold}.pred_vs_actual AS
        SELECT p.*,
               p.t0 + make_interval(0, 0, 0, 0, 0, p.horizon_min, 0) AS target_window_start,
               a.p90_load_factor AS actual_p90_load,
               a.max_load_factor AS actual_max_load,
               CAST(a.max_load_factor >= 0.75 AS INT) AS actual_crowd,
               a.p90_load_factor - p.pred_p50 AS error_p50,
               a.p90_load_factor BETWEEN p.pred_p10 AND p.pred_p90 AS actual_in_p10_p90,
               a.n_pings AS actual_n_pings,
               a.data_origin AS actual_data_origin
        FROM {gold}.predictions p
        JOIN {gold}.route_dir_15m a
          ON a.route_short_name = p.route_short_name
         AND a.direction_id = p.direction_id
         AND a.window_start = p.t0 + make_interval(0, 0, 0, 0, 0, p.horizon_min, 0)
         AND a.data_origin = 'observed'
         AND a.p90_load_factor IS NOT NULL
        WHERE p.t0 + make_interval(0, 0, 0, 0, 0, p.horizon_min + 15, 0) <= current_timestamp()
    """)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root')
    parser.add_argument('--catalog', default='transit')
    parser.add_argument('--data-root', default=os.environ.get('AGGIEPULSE_DATA_ROOT'))
    parser.add_argument('--seed-root')
    args = parser.parse_args()
    if args.data_root:
        os.environ['AGGIEPULSE_DATA_ROOT'] = args.data_root
    os.environ.setdefault('MLFLOW_TRACKING_URI', 'databricks')
    os.environ.setdefault('MLFLOW_ENABLE_ARTIFACTS_PROGRESS_BAR', 'false')
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    spark.conf.set('spark.sql.session.timeZone', 'UTC')
    run(spark, args)


if __name__ == '__main__':
    main()
