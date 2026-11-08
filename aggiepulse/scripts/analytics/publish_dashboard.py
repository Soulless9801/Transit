"""Verify deployed Lakeview JSON via CLI get, publish it, and save identity/evidence."""

from __future__ import annotations

import json
import subprocess

from apply_comments import ROOT
from databricks.sdk import WorkspaceClient
from genie_space import bundle_resources


def main():
    resources = bundle_resources()
    resource = resources["dashboards"]["aggiepulse_ops_dashboard"]
    dashboard_id = resource["id"]
    result = subprocess.run(
        ["databricks", "lakeview", "get", dashboard_id, "-p", "transit", "-o", "json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    deployed = json.loads(result.stdout)
    local = json.loads((ROOT / "dashboards/aggiepulse_ops.lvdash.json").read_text())
    if json.loads(deployed["serialized_dashboard"]) != local:
        raise RuntimeError(
            "Deployed dashboard differs from local definition; deploy first"
        )
    (ROOT / "dashboards/deployed_dashboard.json").write_text(
        json.dumps(deployed, indent=2) + "\n"
    )
    workspace = WorkspaceClient(profile="transit")
    published = workspace.lakeview.publish(
        dashboard_id,
        embed_credentials=True,
        warehouse_id=resource["warehouse_id"],
    ).as_dict()
    published.update(
        dashboard_id=dashboard_id, url=resource["url"], exact_definition_match=True
    )
    (ROOT / "dashboards/published_dashboard.json").write_text(
        json.dumps(published, indent=2) + "\n"
    )
    print(resource["url"])


if __name__ == "__main__":
    main()
