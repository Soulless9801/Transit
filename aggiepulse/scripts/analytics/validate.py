"""Run every dashboard dataset and trusted Genie SQL on the SQL Statements API."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from apply_comments import ROOT
from client import SqlClient
from databricks.sdk.errors import DatabricksError


def referenced_tables(query: str) -> set[str]:
    # Full UC identifiers are mandatory for physical sources. Ignore SQL literals
    # and comments so prose/lineage labels do not become false source matches.
    clean = re.sub(r"'(?:''|\\.|[^'])*'", "''", query)
    clean = re.sub(r"--[^\n]*|/\*.*?\*/", "", clean, flags=re.DOTALL)
    identifier = r"`?\w+`?"
    ctes = {
        name.replace("`", "").lower()
        for name in re.findall(
            r"(?:\bWITH|,)\s*(" + identifier + r")\s+AS\s*\(", clean, re.IGNORECASE
        )
    }
    sources = {
        name.replace("`", "").lower()
        for name in re.findall(
            r"\b(?:FROM|JOIN)\s+(" + identifier + r"(?:\." + identifier + r"){0,2})",
            clean,
            re.IGNORECASE,
        )
    }
    return sources - ctes - {"values"}


def validate_queries(sql: SqlClient):
    dashboard = json.loads((ROOT / "dashboards/aggiepulse_ops.lvdash.json").read_text())
    genie = json.loads(
        (ROOT / "dashboards/aggiepulse_analyst.geniespace.json").read_text()
    )
    queries = [(d["name"], "".join(d["queryLines"])) for d in dashboard["datasets"]]
    queries += [
        ("genie_example_" + str(i), "".join(e["sql"]))
        for i, e in enumerate(genie["instructions"]["example_question_sqls"])
    ]
    known = set(json.loads((ROOT / "dashboards/catalog_snapshot.json").read_text()))

    def run(item):
        name, query = item
        unknown = referenced_tables(query) - known
        if unknown:
            return {
                "dataset": name,
                "status": "FAILED",
                "error": f"Unknown tables: {unknown}",
            }
        try:
            result = sql.execute(query)
            return {
                "dataset": name,
                "status": "SUCCEEDED",
                "statement_id": result["statement_id"],
                "row_count": result["manifest"]["total_row_count"],
                "columns": result["manifest"]["schema"]["columns"],
                "preview": result.get("result", {}).get("data_array", [])[:5],
            }
        except (RuntimeError, DatabricksError) as exc:
            return {"dataset": name, "status": "FAILED", "error": str(exc)}

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(run, queries))
    (ROOT / "dashboards/query_validation.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )
    for result in results:
        print(
            result["dataset"],
            result["status"],
            result.get("row_count", ""),
            result.get("error", ""),
        )
    return not any(r["status"] == "FAILED" for r in results)


if __name__ == "__main__":
    from databricks.sdk import WorkspaceClient

    raise SystemExit(
        0 if validate_queries(SqlClient(WorkspaceClient(profile="transit"))) else 1
    )
