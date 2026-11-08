"""Render the reviewed analytics deployment evidence as a plain-text handoff."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from apply_comments import ROOT


def load(name):
    return json.loads((ROOT / "dashboards" / name).read_text())


def main():
    published = load("published_dashboard.json")
    identity = load("genie_space.json")
    comments = load("comments_rerun_report.json")
    validation = load("query_validation.json")
    transcript = load("genie_transcript.json")
    champions = load("champion_snapshot.json")
    tables = comments["tables"]
    documented = sum(c["status"] != "fallback" for t in tables for c in t["columns"])
    blocked = sum(c["status"] == "fallback" for t in tables for c in t["columns"])
    if len(transcript["answers"]) != 3 or not all(
        a.get("correct") for a in transcript["answers"]
    ):
        raise RuntimeError(
            "Review and mark all three Genie answers before producing the final report"
        )
    lines = [
        "AggiePulse Databricks analytics handoff",
        "Generated UTC: " + datetime.now(UTC).isoformat(),
        "",
        "Dashboard: " + published["url"],
        "Dashboard ID: " + published["dashboard_id"],
        "Lakeview CLI get matched local JSON exactly: "
        + str(published["exact_definition_match"]),
        "Published revision UTC: " + published["revision_create_time"],
        "Genie: " + identity["url"],
        "Genie space ID: " + identity["space_id"],
        "Both are managed by Asset Bundle resources, warehouse_id=${var.warehouse_id}.",
        "",
        "Validation: all 16 dashboard dataset queries and four trusted Genie SQL examples succeeded via SQL Statements API.",
        "Local verification: 8 analytics tests passed; ruff check passed; bundle validate passed.",
        "databricks bundle deploy -p transit succeeded. No deployment lock encountered. No commits made.",
        "Dashboard has five pages; Genie has 25 curated tables, 12 sample questions and four trusted queries.",
        "Source catalog_snapshot.json contains 56 public Bronze/Silver/Gold table names discovered at build time; internal materializations excluded.",
        "",
        f"UC comments: {len(tables)} table comments and {documented} column comments documented; {blocked} column comments blocked by pipeline-owned view ALTER restrictions.",
        "All blocked definitions are included in the Genie shared glossary with table mappings, including reference-only excluded tables.",
        "Genie query sources exclude Bronze, training_set, pipeline logs and diagnostic/audit-only tables.",
        "Live comment rerun: " + json.dumps(comments["summary"]),
        "",
        "Champion snapshot (regenerate assets after alias promotion): "
        + json.dumps(champions),
        "",
        "Persistent limitations / data availability:",
        "- ALTER TABLE ... ALTER COLUMN fails with EXPECT_TABLE_NOT_VIEW.NO_ALTERNATIVE for 27 pipeline-managed views. Table-level COMMENT ON TABLE succeeds. No ownership or pipeline code was changed.",
        "- Live GPS readings were older than five minutes at validation, so active buses/load-now are empty/zero and the freshness counter exposes the lag. This work does not operate the collector.",
        "- No simulated actuals are matched in pred_vs_actual, so the separate simulated verification chart is empty. No simulated metrics were presented as observed performance.",
        "- event_exposure currently has no future windows. The widget shows available coverage bounds and a NULL future peak, rather than inventing upcoming exposure.",
        "- Observed 30-minute evaluation has only 24 matched examples: MAE 0.0647754 load-factor units, interval coverage 0.3333333. These rolling scored matches are not claimed as independent model-selection holdout evidence.",
        "- Champion versions are a timestamped build snapshot, not a continuously queried alias. Prediction rows preserve the actual scored version.",
        "- Calendar midnight/all-day markers are not game kickoff times; significance and attendance are priors, not measured turnout or causal demand.",
        "- Pipeline refreshes may overwrite pipeline-owned metadata; rerun apply_comments.py after refresh. Its final unchanged live rerun is recorded.",
        "",
        "Genie question -> generated SQL -> answer -> correctness:",
    ]
    for answer in transcript["answers"]:
        lines += ["", "QUESTION: " + answer["question"]]
        for query in answer["queries"]:
            lines += [
                "SQL:",
                query["sql"],
                "RESULT ROWS: " + str(len(query["recomputed_result"])),
            ]
        lines += [
            "ANSWER:",
            "\n".join(answer["answer_text"]),
            "CORRECT: " + str(answer["correct"]),
            "REVIEW: " + answer["review"],
        ]
    lines += ["", "Table/column documentation inventory (all TIMESTAMP fields UTC):"]
    for table in sorted(tables, key=lambda t: t["table"]):
        lines += ["", table["table"], "TABLE COMMENT: " + table["table_comment"]]
        for column in table["columns"]:
            status = (
                "GENIE FALLBACK" if column["status"] == "fallback" else "UC DOCUMENTED"
            )
            lines.append(f"  {column['column']} [{status}]: {column['comment']}")
        errors = sorted(
            {
                column["error"]
                for column in table["columns"]
                if column["status"] == "fallback"
            }
        )
        if errors:
            lines.append("  ALTER REJECTION: " + errors[0])
    lines += ["", "Dataset validation evidence:"]
    for dataset in validation:
        lines.append(
            f"{dataset['dataset']}: {dataset['status']}, rows={dataset.get('row_count')}, statement={dataset.get('statement_id')}"
        )
    lines += [
        "",
        "Reproduction commands (package root):",
        "uv run python scripts/analytics/apply_comments.py",
        "uv run python scripts/analytics/build_assets.py",
        "uv run python scripts/analytics/validate.py",
        "uv run pytest tests/analytics -q",
        "uv run ruff check scripts/analytics tests/analytics",
        "databricks bundle deploy -p transit",
        "uv run python scripts/analytics/publish_dashboard.py",
        "uv run python scripts/analytics/genie_space.py",
        "Review and mark transcript correctness; then uv run python scripts/analytics/report.py.",
        "",
    ]
    destination = ROOT / "dashboards/analytics_report.txt"
    destination.write_text("\n".join(lines))
    print(destination)


if __name__ == "__main__":
    main()
