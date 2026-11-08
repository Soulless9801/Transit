"""Freeze public Silver/Gold tables and champion identities, without mutating sources.

Lakeflow public materialized views do not support DESCRIBE HISTORY/time travel.
They are copied once to dedicated Delta tables in ml, then versioned there.
System __materialization_* and event_log_* tables are excluded. A snapshot is a
sequence of table-consistent reads, not an atomic transaction across tables.
"""
import argparse
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from runtime import SQL, identifier, literal


def freeze(sql, label, catalog='transit', client=None):
    from mlflow import MlflowClient

    identifier(catalog)
    snapshot_id = uuid.uuid4().hex
    client = client or MlflowClient(tracking_uri='databricks', registry_uri='databricks-uc')
    models = {}
    for horizon in (15, 30, 60):
        name = f'{catalog}.ml.crowding_h{horizon}'
        model = client.get_model_version_by_alias(name, 'champion')
        models[name] = {'version': str(model.version), 'run_id': model.run_id}
    inventory = sql(f"""SELECT table_schema, table_name, table_type
        FROM {identifier(catalog)}.information_schema.tables
        WHERE table_schema IN ('silver','gold')
        AND left(table_name, 2) <> '__' AND table_name NOT LIKE 'event_log_%'
        ORDER BY table_schema, table_name""")

    def capture(row):
        public = f"{catalog}.{row['table_schema']}.{row['table_name']}"
        replay = public
        if row['table_type'] in ('MATERIALIZED_VIEW', 'VIEW'):
            replay = f"{catalog}.ml.snapshot_{snapshot_id}_{row['table_schema']}_{row['table_name']}"
            sql(f'CREATE TABLE {identifier(replay)} USING DELTA AS SELECT * FROM {identifier(public)}')
        version = int(sql(f'DESCRIBE HISTORY {identifier(replay)} LIMIT 1')[0]['version'])
        return public, replay, version

    # Spark sessions are not thread safe across job execution contexts.
    if getattr(sql, 'spark', None) is not None:
        captured = [capture(row) for row in inventory]
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            captured = list(pool.map(capture, inventory))
    versions = {public: version for public, _, version in captured}
    replay = {public: actual for public, actual, _ in captured}
    table = identifier(f'{catalog}.ml.snapshots')
    sql(f'''CREATE TABLE IF NOT EXISTS {table} (
        snapshot_id STRING, label STRING, created_ts TIMESTAMP,
        table_versions MAP<STRING,BIGINT>, model_versions STRING, replay_tables MAP<STRING,STRING>) USING DELTA''')
    sql(f'''INSERT INTO {table} SELECT {literal(snapshot_id)}, {literal(label)}, current_timestamp(),
        from_json({literal(json.dumps(versions))}, 'MAP<STRING,BIGINT>'),
        {literal(json.dumps(models))}, from_json({literal(json.dumps(replay))}, 'MAP<STRING,STRING>')''')
    return {'snapshot_id': snapshot_id, 'label': label, 'tables': len(versions), 'model_versions': models}


def restore_check(sql, snapshot_id, catalog='transit'):
    table = identifier(f'{catalog}.ml.snapshots')
    rows = sql(f'''SELECT to_json(table_versions) versions, to_json(replay_tables) replay
                   FROM {table} WHERE snapshot_id = {literal(snapshot_id)}''')
    if len(rows) != 1:
        raise ValueError('Expected exactly one snapshot')
    versions, replay = json.loads(rows[0]['versions']), json.loads(rows[0]['replay'])
    counts = {}
    for public, version in versions.items():
        # COUNT forces access to every recorded table/version, including empty tables.
        counts[public] = int(sql(f'SELECT count(*) n FROM {identifier(replay[public])} '
                                 f'VERSION AS OF {int(version)}')[0]['n'])
    return {'snapshot_id': snapshot_id, 'checked': len(counts), 'row_counts': counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label')
    parser.add_argument('--restore-check', metavar='SNAPSHOT_ID')
    parser.add_argument('--catalog', default='transit')
    parser.add_argument('--spark', action='store_true')
    args = parser.parse_args()
    if not args.label and not args.restore_check:
        parser.error('--label or --restore-check required')
    sql = SQL(args.spark)
    result = (restore_check(sql, args.restore_check, args.catalog) if args.restore_check
              else freeze(sql, args.label, args.catalog))
    print(json.dumps(result, indent=2, default=str))
    if args.label:
        print(json.dumps(restore_check(sql, result['snapshot_id'], args.catalog), indent=2))


if __name__ == '__main__':
    main()
