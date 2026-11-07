"""Thin serverless Spark adapter for the offline simulator/feature APIs.

Run locally with --local-output DIR, or as the bundle's ml_data job. Writes only
this workstream's three gold tables and tracks their exact Delta versions.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

# Load the deployed source snapshot even when a warm environment cached an older wheel.
sys.path.insert(0, sys.argv[sys.argv.index('--source-root')+1] if '--source-root' in sys.argv
                else str(Path(globals().get('__file__', sys.argv[0])).resolve().parents[2]))

from aggiepulse.common.paths import REPO_ROOT
from aggiepulse.features.build_training_set import (
    SEASON_KEY,
    build_training_set,
    seasonal_profile_source,
    static_features,
)
from aggiepulse.features.event_exposure import exposure_from_gtfs, prepare_events
from aggiepulse.features.inputs import KEY, load_local, measured_windows, seed_file, utc
from aggiepulse.sim.simulate_history import simulate_history


def prefer_silver(inputs, spark, catalog):
    """Use compatible silver tables when present; retain raw-parser fallbacks."""
    from pyspark.errors import AnalysisException

    lineage = {}
    def read(name):
        full_name = f'{catalog}.{name}'
        if not spark.catalog.tableExists(full_name):
            return None
        frame = spark.table(full_name).toPandas()
        if frame.empty:
            return None
        try:
            lineage[full_name] = int(spark.sql(f'DESCRIBE HISTORY {full_name} LIMIT 1').first()['version'])
        except AnalysisException:
            lineage[full_name] = 'non-Delta/view'
        for c in frame:
            if pd.api.types.is_datetime64_any_dtype(frame[c]):
                frame[c] = pd.to_datetime(frame[c], utc=True)
        return frame
    gtfs = {}
    for table in ['routes','stops','trips','stop_times','shapes','calendar_dates','feed_info']:
        frame = read(f'silver.gtfs_{table}')
        if frame is not None:
            gtfs[table] = frame.astype('string')
    if {'routes','stops','trips','stop_times','calendar_dates','shapes'} <= set(gtfs):
        inputs['gtfs'] = gtfs
    for key, table, required in [
        ('weather','silver.weather_hourly', ['ts_hour','precip_mm','is_forecast','forecast_issued_ts']),
        ('ntd','silver.ntd_monthly',['month','upt','mode','tos']),
        ('academic','silver.academic_days',['date','is_class_day','notes']),
        ('periods','silver.class_periods',['pattern','start_time','end_time']),
        ('observed','gold.route_dir_15m',[*KEY,'avg_load_factor','p90_load_factor','max_load_factor',
                                        'headway_cv','bunching_rate','service_ratio','active_vehicles',
                                        'sched_trips','data_origin'])]:
        frame = read(table)
        if frame is not None and set(required) <= set(frame):
            inputs[key] = frame
    # silver.events is deliberately NOT used: it assigns a size and coordinates to
    # events the documented filter policy (event_filters.yaml, ASSUMPTIONS.md)
    # excludes or leaves unsized. On 2026-11-07 that made median route exposure
    # 14.8 (vs 0 under the policy) and the event prior dominated simulated demand.
    # The raw LiveWhale envelopes on the Volume go through prepare_events instead.
    if 'observed' in inputs and not inputs['observed'].empty:
        inputs['observed'] = inputs['observed'].query("data_origin == 'observed'")
    return inputs, lineage


def run_pipeline(inputs, end=None, seed=42, train_end=None, validation_end=None):
    cutoff = pd.Timestamp(end) if end else pd.Timestamp.now(tz='UTC')
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize('UTC')
    cutoff = cutoff.floor('15min')
    inputs = {**inputs, 'observed':measured_windows(inputs['observed'])}
    events = inputs.get('events')
    if events is None:
        events = prepare_events(inputs['raw_events'], inputs['venues'], inputs['seed_root'])
    simulated = simulate_history(inputs['gtfs'], inputs['academic'], inputs['periods'], events,
                                  inputs['weather'], inputs['ntd'], inputs['observed'], end=cutoff, seed=seed)
    observed = inputs['observed'].copy()
    if not observed.empty:
        observed = observed[observed.window_start+pd.Timedelta(minutes=15) <= cutoff]
    frames = [simulated, observed] if not observed.empty else [simulated]
    # pandas compares attrs during concat; these are output metadata, not row data.
    calibration = simulated.attrs.copy()
    clean = []
    for frame in frames:
        frame = frame.copy()
        frame.attrs = {}
        clean.append(frame)
    history = pd.concat(clean, ignore_index=True)
    exposure = exposure_from_gtfs(history[KEY].drop_duplicates(), events, inputs['gtfs'])
    training = build_training_set(history, inputs['academic'], inputs['periods'], exposure,
                                  inputs['weather'], static_features(inputs['gtfs'], inputs['venues']),
                                  train_end, validation_end)
    if simulated.p90_load_factor.notna().any() and not training.loc[
            training.data_origin.eq('simulated'), 'y_p90load_h15'].notna().any():
        raise ValueError('No simulated supervised labels survived; inspect origin precedence')
    # Frozen train-only slot profile: the exact seasonal feature values used after train_end.
    source = measured_windows(history)
    source['window_start'] = pd.to_datetime(source.window_start, utc=True)
    source['route_short_name'] = source.route_short_name.astype('string')
    source['direction_id'] = source.direction_id.astype(int)
    profile = seasonal_profile_source(source, utc(training.attrs['train_end']),
                                      inputs['academic'], inputs['periods'])
    profile = profile.sort_values('available_at').groupby(SEASON_KEY).tail(1).reset_index(drop=True)
    profile['available_at'] = utc(training.attrs['train_end'])
    sample = training[(training.y_p90load_h30.notna() | training.y_p90load_h15.notna())
                      & training.load_t0.notna()]
    simulated_sample = sample[sample.data_origin.eq('simulated') & sample.load_lag30.notna()]
    sample_cols = ['route_short_name','direction_id','t0','load_t0','load_lag30',
                   'event_exposure','y_p90load_h15','y_p90load_h30','y_crowd_h30','data_origin','split']
    report = {**calibration, 'training_metadata':training.attrs,
              'seasonal_profile_rows':len(profile),
              'simulated_shape':list(simulated.shape), 'event_exposure_shape':list(exposure.shape),
              'training_shape':list(training.shape),
              'training_groups':training.groupby(['data_origin','split']).size().rename('rows').reset_index().to_dict('records'),
              'label_counts':{f'h{h}':int(training[f'y_p90load_h{h}'].notna().sum()) for h in [15,30,60]},
              'observed_windows':len(observed), 'filtered_events':len(events),
              'non_null_labels_by_origin':training.groupby('data_origin')[
                  [f'y_p90load_h{h}' for h in [15,30,60]]].count().reset_index().to_dict('records'),
              'positive_crowd_h30':int(training.y_crowd_h30.sum()),
              'sample':pd.concat([simulated_sample.head(2), training[training.data_origin.eq('observed')].head(2)])[
                  sample_cols].to_dict('records')}
    return simulated, exposure, training, profile, report


def write_delta(spark, frame, table):
    """Explicit types keep all-null APC delay/alert hooks writable."""
    from pyspark.sql import types as t

    columns = []
    frame = frame.copy()
    for name, dtype in frame.dtypes.items():
        if pd.api.types.is_datetime64_any_dtype(dtype):
            kind = t.TimestampType()
            frame[name] = pd.to_datetime(frame[name], utc=True).dt.tz_localize(None)
        elif pd.api.types.is_bool_dtype(dtype):
            kind = t.BooleanType()
        elif pd.api.types.is_integer_dtype(dtype):
            kind = t.IntegerType() if name in {'direction_id','sched_trips','active_vehicles','n_pings'} else t.LongType()
        elif pd.api.types.is_numeric_dtype(dtype):
            kind = t.DoubleType()
        else:
            kind = t.StringType()
            frame[name] = frame[name].astype('string')
        columns.append(t.StructField(name, kind, True))
    sdf = spark.createDataFrame(frame, t.StructType(columns))
    sdf.write.format('delta').mode('overwrite').option('overwriteSchema','true').saveAsTable(table)
    return int(spark.sql(f'DESCRIBE HISTORY {table} LIMIT 1').first()['version'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', default=os.environ.get('AGGIEPULSE_DATA_ROOT'))
    parser.add_argument('--seed-root')
    parser.add_argument('--source-root', help='Bundle source path for Databricks exec-based script wrapper')
    parser.add_argument('--catalog', default='transit')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--end')
    parser.add_argument('--train-end')
    parser.add_argument('--validation-end')
    parser.add_argument('--local-output')
    args = parser.parse_args()
    if args.data_root:
        os.environ['AGGIEPULSE_DATA_ROOT'] = args.data_root
    # Seed Volume is preferred; bundle files provide a read-only safety net.
    seed_root = Path(args.seed_root or REPO_ROOT/'seeds')
    try:
        seed_file(seed_root, 'academic_days.csv')
    except FileNotFoundError:
        seed_root = REPO_ROOT/'seeds'
    input_versions = {}
    preloaded = {}
    spark = None
    if not args.local_output:
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        spark.conf.set('spark.sql.session.timeZone','UTC')
        preloaded, input_versions = prefer_silver({}, spark, args.catalog)
    inputs = load_local(args.data_root, seed_root, preloaded)
    simulated, exposure, training, profile, report = run_pipeline(
        inputs, args.end, args.seed, args.train_end, args.validation_end)
    report['input_delta_versions'] = input_versions
    if args.local_output:
        output = Path(args.local_output)
        output.mkdir(parents=True, exist_ok=True)
        for name, frame in [('route_dir_15m_sim',simulated),('event_exposure',exposure),('training_set',training),
                            ('seasonal_profile',profile)]:
            frame = frame.copy()
            frame.attrs = {}
            frame.to_parquet(output/f'{name}.parquet',index=False)
        (output/'report.json').write_text(json.dumps(report, indent=2, default=str))
    else:
        import mlflow
        spark.sql(f'CREATE SCHEMA IF NOT EXISTS {args.catalog}.gold')
        versions = {}
        for name, frame in [('route_dir_15m_sim',simulated),('event_exposure',exposure),('training_set',training),
                            ('seasonal_profile',profile)]:
            table = f'{args.catalog}.gold.{name}'
            versions[table] = write_delta(spark, frame, table)
        report['output_delta_versions'] = versions
        mlflow.set_experiment('/Shared/aggiepulse')
        with mlflow.start_run(run_name='phase5_ml_data') as run:
            mlflow.log_params({'seed':args.seed, 'turnover':report['turnover'],
                               'class_wave_coef':report.get('class_wave_coef'),
                               'day_effect_sigma':report.get('day_effect_sigma'),
                               'route_direction_sigma':report.get('route_direction_sigma'),
                               'train_end':training.attrs['train_end'],
                               'validation_end':training.attrs['validation_end']})
            mlflow.set_tags({f'delta_version.{name}':str(version)
                             for name, version in {**input_versions, **versions}.items()})
            mlflow.set_tags({'simulation':'explicit_prior', 'observed_metrics_only':'true',
                             'weather_proxy':'flagged', 'feature_state':'completed_intervals'})
            mlflow.log_dict(json.loads(json.dumps(report, default=str)), 'ml_data_report.json')
            mlflow.log_metrics({'simulated_rows':len(simulated),'training_rows':len(training),
                                'positive_crowd_h30':report['positive_crowd_h30'],
                                'observed_windows':report['observed_windows']})
            report['mlflow_run_id'] = run.info.run_id
    print(json.dumps(report, indent=2, default=str))


if __name__ == '__main__':
    main()
