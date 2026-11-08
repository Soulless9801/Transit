import json
from pathlib import Path

from validate import referenced_tables

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_structure_and_existing_sources():
    dashboard = json.loads((ROOT / "dashboards/aggiepulse_ops.lvdash.json").read_text())
    known = set(json.loads((ROOT / "dashboards/catalog_snapshot.json").read_text()))
    assert len(dashboard["pages"]) == 5
    datasets = {d["name"]: d for d in dashboard["datasets"]}
    assert len(datasets) == len(dashboard["datasets"])
    for dataset in datasets.values():
        query = "".join(dataset["queryLines"])
        assert query.strip()
        assert referenced_tables(query) <= known
    for page in dashboard["pages"]:
        assert page["layout"]
        for item in page["layout"]:
            widget = item["widget"]
            frame = widget["spec"]["frame"]
            assert frame["showTitle"] and frame["showDescription"]
            assert any(
                label in frame["title"]
                for label in ("MEASURED", "DERIVED", "SIMULATED", "PREDICTED")
            )
            assert widget["queries"][0]["query"]["datasetName"] in datasets


def test_source_check_finds_unknown_and_ignores_literals():
    sources = referenced_tables(
        "SELECT 'FROM transit.no.fake' FROM transit.silver.headways JOIN `transit`.`bad`.`missing` m ON true"
    )
    assert sources == {"transit.silver.headways", "transit.bad.missing"}


def test_genie_curated_sources_and_examples():
    space = json.loads(
        (ROOT / "dashboards/aggiepulse_analyst.geniespace.json").read_text()
    )
    known = set(json.loads((ROOT / "dashboards/catalog_snapshot.json").read_text()))
    tables = {t["identifier"] for t in space["data_sources"]["tables"]}
    assert tables <= known
    assert not any(".bronze." in t or t.endswith(".training_set") for t in tables)
    assert 8 <= len(space["config"]["sample_questions"]) <= 12
    for example in space["instructions"]["example_question_sqls"]:
        assert referenced_tables("".join(example["sql"])) <= tables
    instructions = "\n".join(
        "".join(i["content"]) for i in space["instructions"]["text_instructions"]
    )
    assert "actual_data_origin" in instructions and "America/Chicago" in instructions
    for table in tables:
        assert table in instructions
        for column in json.loads(
            (ROOT / "dashboards/catalog_snapshot.json").read_text()
        )[table]:
            assert column["name"] + ":" in instructions


def test_source_check_rejects_unqualified_physical_tables_and_keeps_ctes():
    query = "WITH good AS (SELECT * FROM transit.silver.headways) SELECT * FROM good JOIN missing ON true"
    assert referenced_tables(query) == {"transit.silver.headways", "missing"}


def test_genie_instructions_fit_api_limit():
    space = json.loads(
        (ROOT / "dashboards/aggiepulse_analyst.geniespace.json").read_text()
    )
    instructions = space["instructions"]["text_instructions"]
    assert len(instructions) == 1
    assert sum(map(len, instructions[0]["content"])) <= 65000
