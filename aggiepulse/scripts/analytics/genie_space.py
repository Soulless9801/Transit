"""Record bundle Genie identity and exercise three sample questions via its API.

Provisioning uses resources/genie.yml because this CLI supports genie_spaces.
This script never creates duplicate spaces. Run after bundle deploy. Completed
answers are checkpointed to dashboards/genie_transcript.json for human review.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from apply_comments import ROOT
from build_assets import EXAMPLES
from client import SqlClient
from databricks.sdk import WorkspaceClient


def bundle_resources():
    result = subprocess.run(
        ["databricks", "bundle", "summary", "-p", "transit", "-o", "json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)["resources"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--question", choices=["all", "bunching", "mae", "events"], default="all"
    )
    args = parser.parse_args()
    workspace = WorkspaceClient(profile="transit")
    resources = bundle_resources()
    resource = resources["genie_spaces"]["aggiepulse_analyst"]
    space_id = resource["id"]
    identity = {
        "space_id": space_id,
        "url": resource["url"],
        "managed_by": "resources/genie.yml",
    }
    (ROOT / "dashboards/genie_space.json").write_text(
        json.dumps(identity, indent=2) + "\n"
    )
    sql = SqlClient(workspace)

    def ask(example):
        question, expected_sql = example
        message = workspace.genie.start_conversation_and_wait(
            space_id, question, timeout=timedelta(minutes=10)
        )
        result = {
            "question": question,
            "message": message.as_dict(),
            "answer_text": [],
            "queries": [],
            "correct": None,
            "trusted_sql": expected_sql,
            "trusted_result": sql.rows(expected_sql),
        }
        for attachment in message.attachments or []:
            if attachment.text:
                result["answer_text"].append(attachment.text.content)
            if attachment.query:
                query = attachment.query.query
                detail = {
                    "sql": query,
                    "description": attachment.query.description,
                    "recomputed_result": sql.rows(query),
                }
                # Keep the conversation API result as evidence as well as a direct rerun.
                api_result = workspace.genie.get_message_attachment_query_result(
                    space_id,
                    message.conversation_id,
                    message.message_id,
                    attachment.attachment_id,
                )
                detail["conversation_result"] = api_result.as_dict()
                result["queries"].append(detail)
        return result

    transcript = {
        "space": identity,
        "tested_utc": datetime.now(UTC).isoformat(),
        "answers": [],
    }
    selected = (
        EXAMPLES[:3]
        if args.question == "all"
        else [EXAMPLES[{"bunching": 0, "mae": 1, "events": 2}[args.question]]]
    )
    transcript_path = ROOT / "dashboards/genie_transcript.json"
    if args.question != "all" and transcript_path.exists():
        transcript["answers"] = [
            answer
            for answer in json.loads(transcript_path.read_text())["answers"]
            if answer["question"] not in {question for question, _ in selected}
        ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(ask, example) for example in selected]
        for future in as_completed(futures):
            result = future.result()
            transcript["answers"].append(result)
            (ROOT / "dashboards/genie_transcript.json").write_text(
                json.dumps(transcript, indent=2) + "\n"
            )
            print(
                "Answered:",
                result["question"],
                "SQL attachments:",
                len(result["queries"]),
                flush=True,
            )
    print("Review SQL, result and prose; record correctness in genie_transcript.json.")


if __name__ == "__main__":
    main()
