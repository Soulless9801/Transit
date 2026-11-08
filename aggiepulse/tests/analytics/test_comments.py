from copy import deepcopy
from unittest.mock import Mock

from apply_comments import apply_comments, column_comment, table_comment
from client import quote_literal


def metadata():
    return (
        [{"table_schema": "silver", "table_name": "vehicle_pings", "comment": None}],
        [
            {
                "table_schema": "silver",
                "table_name": "vehicle_pings",
                "column_name": "load_factor",
                "data_type": "DOUBLE",
                "comment": None,
            }
        ],
    )


def test_rerun_does_not_write_unchanged_comments():
    tables, columns = metadata()
    client = Mock()
    report = apply_comments(client, tables, columns, workers=1)
    assert client.execute.call_count == 2
    assert report["summary"]["column_comments_applied"] == 1
    statements = [call.args[0] for call in client.execute.call_args_list]
    assert statements[0].startswith(
        "COMMENT ON TABLE `transit`.`silver`.`vehicle_pings`"
    )
    assert statements[1].startswith(
        "ALTER TABLE `transit`.`silver`.`vehicle_pings` ALTER COLUMN `load_factor` COMMENT"
    )
    tables[0]["comment"] = table_comment("silver", "vehicle_pings")
    columns[0]["comment"] = column_comment("load_factor", "DOUBLE")
    client.reset_mock()
    second = apply_comments(client, tables, columns, workers=1)
    client.execute.assert_not_called()
    assert second["tables"][0]["columns"][0]["status"] == "unchanged"


def test_pipeline_column_rejection_is_reported_and_rerun_is_safe():
    tables, columns = metadata()
    columns.append({**columns[0], "column_name": "ts_gps", "data_type": "TIMESTAMP"})
    client = Mock()

    def execute(sql):
        if sql.startswith("ALTER TABLE"):
            raise RuntimeError(
                "[EXPECT_TABLE_NOT_VIEW.NO_ALTERNATIVE] pipeline materialized view"
            )

    client.execute.side_effect = execute
    original = deepcopy((tables, columns))
    first = apply_comments(client, tables, columns, workers=1)
    assert first["summary"]["column_fallbacks"] == 2
    assert (
        client.execute.call_count == 2
    )  # table plus one representative unsupported ALTER
    assert first["tables"][0]["columns"][1]["comment"]
    assert apply_comments(client, tables, columns, workers=1) == first
    assert (tables, columns) == original


def test_units_and_sql_escaping():
    assert "unverified" in column_comment("speed", "DOUBLE")
    assert "actual_p90_load - pred_p50" in column_comment("error_p50", "DOUBLE")
    assert "''" in quote_literal("TAMU's route")
    assert "not gross boardings" in column_comment("net_board_proxy", "INT").lower()
