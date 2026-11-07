"""Training job steps: train -> evaluate -> register (serverless or local).

Steps share one MLflow parent run, found by the ``job_run_id`` tag, so each
Databricks task can run in its own serverless environment. Locally,
``--step all --local-dir DIR`` runs everything against Parquet output of
``aggiepulse.features.job --local-output DIR`` with a local SQLite MLflow store and
no registry.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

# Load the deployed source snapshot even when a warm environment cached an older wheel.
sys.path.insert(0, sys.argv[sys.argv.index('--source-root')+1] if '--source-root' in sys.argv
                else str(Path(globals().get('__file__', sys.argv[0])).resolve().parents[2]))

import pandas as pd

EXPERIMENT = '/Shared/aggiepulse'
MODEL_NAME = '{catalog}.ml.crowding_h{horizon}'


def package_dir():
    # Databricks runs this file via exec without __file__; the imported package knows its path.
    import aggiepulse
    return Path(aggiepulse.__file__).resolve().parent


def git_commit():
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True,
                              check=True, cwd=package_dir()).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def spark_session():
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    spark.conf.set('spark.sql.session.timeZone', 'UTC')
    return spark


def delta_version(spark, table):
    return int(spark.sql(f'DESCRIBE HISTORY {table} LIMIT 1').first()['version'])


def read_table(spark, table, version=None):
    reader = spark.read.option('versionAsOf', version) if version is not None else spark.read
    frame = reader.table(table).toPandas()
    for column in frame:
        if pd.api.types.is_datetime64_any_dtype(frame[column]):
            frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


def normalise_training(frame):
    """Spark round-trips nullable labels as object/float; restore numeric types."""
    for column in frame:
        if column.startswith(('y_', 'load_', 'p90_load')) or column in {'seasonal_slot_mean'}:
            frame[column] = pd.to_numeric(frame[column], errors='coerce').astype(float)
    frame['direction_id'] = pd.to_numeric(frame.direction_id).astype(int)
    frame['route_short_name'] = frame.route_short_name.astype(str)
    return frame


def find_parent_run(job_run_id):
    import mlflow
    runs = mlflow.search_runs(experiment_names=[EXPERIMENT], output_format='list',
                              filter_string=f"tags.job_run_id = '{job_run_id}' and tags.step = 'train'")
    if not runs:
        raise RuntimeError(f'No training run tagged job_run_id={job_run_id}')
    return runs[0].info.run_id


def load_inputs(args, spark):
    if args.local_dir:
        directory = Path(args.local_dir)
        training = pd.read_parquet(directory/'training_set.parquet')
        season = pd.read_parquet(directory/'seasonal_profile.parquet')
        return normalise_training(training), season, {'local_dir': str(directory)}
    versions = {}
    for name in ['training_set', 'seasonal_profile']:
        table = f'{args.catalog}.gold.{name}'
        versions[table] = delta_version(spark, table)
    training = read_table(spark, f'{args.catalog}.gold.training_set', versions[f'{args.catalog}.gold.training_set'])
    season = read_table(spark, f'{args.catalog}.gold.seasonal_profile', versions[f'{args.catalog}.gold.seasonal_profile'])
    return normalise_training(training), season, versions


def step_train(args, spark):
    import mlflow

    from aggiepulse.ml.train import HORIZONS, fit_horizon

    training, season, versions = load_inputs(args, spark)
    tags = {'step': 'train', 'job_run_id': args.job_run_id, 'git_commit': args.git_commit or git_commit(),
            'training_data_origin': 'simulated', 'observed_metrics_only': 'true',
            'event_uplift': 'simulated prior; attribution, not causation',
            **{f'delta_version.{k}': str(v) for k, v in versions.items()}}
    splits = training.groupby(['data_origin', 'split']).size()
    with mlflow.start_run(run_name='train_crowding') as run:
        mlflow.set_tags(tags)
        mlflow.log_params({'trials_per_horizon': args.trials, 'horizons': '15,30,60',
                           'selection': 'forward: fit train, select on validation (later in time)',
                           'final_fit': 'train split only (same data as baselines)'})
        mlflow.log_dict({f'{o}/{s}': int(n) for (o, s), n in splits.items()}, 'data/split_counts.json')
        if not args.local_dir:
            try:
                table = f'{args.catalog}.gold.training_set'
                dataset = mlflow.data.load_delta(table_name=table, version=versions[table])
                mlflow.log_input(dataset, context='training')
            except Exception as exc:  # noqa: BLE001 - lineage is best effort; the tag is authoritative
                print(f'log_input skipped: {exc}')
        summaries = {}
        for horizon in HORIZONS:
            # The frozen seasonal profile becomes available exactly at train_end.
            train_end = str(pd.to_datetime(season.available_at, utc=True).max()) if len(season) else None
            metadata = {'horizon': horizon, 'train_end': train_end,
                        'training_data_origin': 'simulated', **tags}
            model, baselines, summary = fit_horizon(training, horizon, args.trials, metadata, season)
            summaries[horizon] = summary
            mlflow.log_params({f'h{horizon}_{k}': v for k, v in summary['best_params'].items()})
            mlflow.log_metrics({f'h{horizon}_best_validation_mae': summary['best_validation_mae'],
                                f'h{horizon}_train_positives': summary['train_positives']})
            example = training[model.columns].head(5)
            mlflow.pyfunc.log_model(name=f'crowding_h{horizon}', python_model=model,
                                    code_paths=[str(package_dir())],
                                    input_example=example, pip_requirements=requirements())
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/f'baselines_h{horizon}.pkl'
                path.write_bytes(pickle.dumps(baselines))
                mlflow.log_artifact(str(path), 'baselines')
        mlflow.log_dict({str(k): v for k, v in summaries.items()}, 'training_summary.json')
        print(json.dumps({'run_id': run.info.run_id, 'summaries': summaries}, indent=2, default=str))
        return run.info.run_id


def requirements():
    import lightgbm
    import mlflow
    import numpy
    import sklearn
    return [f'lightgbm=={lightgbm.__version__}', f'mlflow=={mlflow.__version__}',
            f'pandas=={pd.__version__}', f'numpy=={numpy.__version__}',
            f'scikit-learn=={sklearn.__version__}', 'pyyaml', 'h3']


def load_run_models(run_id):
    import mlflow

    from aggiepulse.ml.train import HORIZONS
    models, baselines = {}, {}
    for horizon in HORIZONS:
        models[horizon] = mlflow.pyfunc.load_model(f'runs:/{run_id}/crowding_h{horizon}').unwrap_python_model()
        path = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path=f'baselines/baselines_h{horizon}.pkl')
        baselines[horizon] = pickle.loads(Path(path).read_bytes())
    return models, baselines


def step_evaluate(args, spark, run_id=None):
    import mlflow

    from aggiepulse.ml.evaluate import headline
    from aggiepulse.ml.train import evaluate_models, log_plots, promotion_gate

    run_id = run_id or find_parent_run(args.job_run_id)
    training, _, versions = load_inputs(args, spark)
    run = mlflow.get_run(run_id)
    for table, version in versions.items():
        recorded = run.data.tags.get(f'delta_version.{table}')
        if recorded is not None and recorded != str(version):
            # Evaluate against the exact version the models were trained on.
            training = normalise_training(read_table(spark, table, int(recorded))) if 'training_set' in table else training
    models, baselines = load_run_models(run_id)
    table = evaluate_models(training, models, baselines)
    gate = promotion_gate(table)
    with mlflow.start_run(run_id=run_id):
        summary = headline(table)
        for row in summary.to_dict('records'):
            for metric in ['n', 'mae', 'rmse', 'pinball_p50', 'coverage_p10_p90', 'pr_auc', 'brier']:
                if row[metric] is not None and pd.notna(row[metric]):
                    mlflow.log_metric(f"{row['origin']}_h{row['horizon']}_{row['model']}_{metric}", float(row[metric]))
        mlflow.log_table(table, 'metrics/by_group.json')
        mlflow.log_text(summary.to_csv(index=False), 'metrics/headline.csv')
        mlflow.log_dict(gate, 'metrics/promotion_gate.json')
        mlflow.set_tags({'promotion_gate': 'pass' if gate['promote'] else 'fail', 'step': 'train'})
        for horizon, model in models.items():
            log_plots(training, model, horizon)
    pd.set_option('display.width', 250)
    print(summary.to_string())
    print(json.dumps(gate, indent=2))
    return table, gate


def step_register(args, run_id=None):
    import mlflow
    from mlflow import MlflowClient

    from aggiepulse.ml.train import HORIZONS

    run_id = run_id or find_parent_run(args.job_run_id)
    gate = mlflow.artifacts.load_dict(f'runs:/{run_id}/metrics/promotion_gate.json')
    mlflow.set_registry_uri('databricks-uc')
    client = MlflowClient()
    result = {}
    for horizon in HORIZONS:
        name = MODEL_NAME.format(catalog=args.catalog, horizon=horizon)
        version = mlflow.register_model(f'runs:/{run_id}/crowding_h{horizon}', name)
        tags = {'promotion_rule': gate['rule'], 'promotion_gate': 'pass' if gate['promote'] else 'fail',
                'gate_lightgbm_mae_h30_sim_test': f"{gate['lightgbm_mae']:.5f}",
                'gate_b1_mae_h30_sim_test': f"{gate['b1_mae']:.5f}",
                'training_data_origin': 'simulated', 'mlflow_run_id': run_id,
                'event_uplift': 'simulated prior; attribution, not causation'}
        for key, value in tags.items():
            client.set_model_version_tag(name, version.version, key, value)
        if gate['promote']:
            client.set_registered_model_alias(name, 'champion', version.version)
        result[horizon] = {'name': name, 'version': version.version, 'champion': gate['promote']}
    with mlflow.start_run(run_id=run_id):
        mlflow.log_dict({'gate': gate, 'registered': result}, 'registry/registration.json')
    print(json.dumps({'gate': gate, 'registered': result}, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--step', choices=['train', 'evaluate', 'register', 'all'], required=True)
    parser.add_argument('--source-root')
    parser.add_argument('--catalog', default='transit')
    parser.add_argument('--job-run-id', default='local')
    parser.add_argument('--git-commit')
    parser.add_argument('--trials', type=int, default=15)
    parser.add_argument('--local-dir', help='Parquet output of the features job; uses a local SQLite MLflow store')
    args = parser.parse_args()
    os.environ.setdefault('MLFLOW_ENABLE_ARTIFACTS_PROGRESS_BAR', 'false')
    import mlflow
    if args.local_dir:
        local = Path(args.local_dir).resolve()
        mlflow.set_tracking_uri(f"sqlite:///{local/'mlflow.db'}")
        if mlflow.get_experiment_by_name(EXPERIMENT) is None:  # keep artifacts out of the cwd
            mlflow.create_experiment(EXPERIMENT, artifact_location=(local/'mlartifacts').as_uri())
        spark = None
    else:
        os.environ.setdefault('MLFLOW_TRACKING_URI', 'databricks')
        spark = spark_session()
    mlflow.set_experiment(EXPERIMENT)
    run_id = None
    if args.step in ('train', 'all'):
        run_id = step_train(args, spark)
    if args.step in ('evaluate', 'all'):
        step_evaluate(args, spark, run_id)
    if args.step == 'register' or (args.step == 'all' and not args.local_dir):
        step_register(args, run_id)


if __name__ == '__main__':
    main()
