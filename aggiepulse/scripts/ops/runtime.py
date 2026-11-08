"""Small SQL adapter for local CLI and serverless Spark tasks."""
import re
import time


def identifier(value):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}', value):
        raise ValueError(f'Invalid SQL identifier: {value!r}')
    return '.'.join(f'`{part}`' for part in value.split('.'))


def literal(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


class SQL:
    def __init__(self, use_spark=False, profile='transit', warehouse='3c5b1dbf21774cde'):
        self.spark = None
        if use_spark:
            from pyspark.sql import SparkSession
            self.spark = SparkSession.builder.getOrCreate()
            self.spark.conf.set('spark.sql.session.timeZone', 'UTC')
        else:
            from databricks.sdk import WorkspaceClient
            self.client = WorkspaceClient(profile=profile)
            self.warehouse = warehouse

    def __call__(self, statement):
        if self.spark is not None:
            return [row.asDict(recursive=True) for row in self.spark.sql(statement).collect()]
        from databricks.sdk.service.sql import StatementState
        result = self.client.statement_execution.execute_statement(
            warehouse_id=self.warehouse, statement=statement, wait_timeout='50s')
        while result.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(1)
            result = self.client.statement_execution.get_statement(result.statement_id)
        if result.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(str(result.status.error))
        if not result.manifest or not result.result:
            return []
        columns = [c.name for c in result.manifest.schema.columns]
        rows = list(result.result.data_array or [])
        chunk = result.result.next_chunk_index
        while chunk is not None:
            data = self.client.statement_execution.get_statement_result_chunk_n(result.statement_id, chunk)
            rows.extend(data.data_array or [])
            chunk = data.next_chunk_index
        return [dict(zip(columns, row, strict=True)) for row in rows]
