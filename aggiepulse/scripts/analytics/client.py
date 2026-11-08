"""Small, testable SQL Statements API adapter; profile transit, UTC sessions."""

from __future__ import annotations

import time
from typing import Any

WAREHOUSE_ID = "3c5b1dbf21774cde"


class SqlClient:
    def __init__(self, workspace: Any, warehouse_id: str = WAREHOUSE_ID):
        self.workspace = workspace
        self.warehouse_id = warehouse_id

    def execute(self, statement: str) -> dict:
        response = self.workspace.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id,
            statement=statement,
            wait_timeout="50s",
            row_limit=10000,
        )
        while response.status.state.value in {"PENDING", "RUNNING"}:
            time.sleep(1)
            response = self.workspace.statement_execution.get_statement(
                response.statement_id
            )
        result = response.as_dict()
        if result["status"]["state"] != "SUCCEEDED":
            raise RuntimeError(result["status"].get("error", result["status"]))
        if result.get("manifest", {}).get("truncated"):
            raise RuntimeError(
                "SQL result truncated; narrow the query or implement pagination"
            )
        return result

    def rows(self, statement: str) -> list[dict]:
        result = self.execute(statement)
        columns = [c["name"] for c in result["manifest"]["schema"]["columns"]]
        rows = list(result.get("result", {}).get("data_array", []))
        # The API may chunk inline results even below row_limit.
        for chunk in result.get("manifest", {}).get("chunks", [])[1:]:
            extra = self.workspace.statement_execution.get_statement_result_chunk_n(
                result["statement_id"], chunk["chunk_index"]
            )
            rows.extend(extra.data_array or [])
        return [dict(zip(columns, row, strict=True)) for row in rows]


def quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def quote_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
