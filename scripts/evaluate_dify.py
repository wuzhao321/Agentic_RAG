"""Record a published local Dify workflow against the public 20-question demo set."""

import argparse
import json
import os
import shutil
import statistics
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

from kb.eval.dataset import read_cases
from kb.eval.runs import Run, append_jsonl, file_hash, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-snapshot", type=Path, required=True)
    args = parser.parse_args()
    snapshot = json.loads(args.workflow_snapshot.read_text())
    if (
        not snapshot.get("workflow_id")
        or any(d.get("status") != "completed" for d in snapshot.get("documents", []))
        or len(snapshot.get("documents", [])) != 7
    ):
        raise ValueError("需要已发布流程、7份索引完成文档的配置快照")
    load_dotenv()
    base = os.environ.get("DIFY_API_BASE", "").rstrip("/")
    key = os.environ.get("DIFY_APP_KEY")
    if not base or not key:
        raise ValueError("请配置 DIFY_API_BASE 和 DIFY_APP_KEY；这里使用 Dify 应用密钥")
    address = urlsplit(base)
    if address.hostname not in {"127.0.0.1", "localhost", "::1"} or address.scheme not in {
        "http",
        "https",
    }:
        raise ValueError("此对照工具只连接本机 Dify")
    source = Path("data/demo/qa.jsonl")
    cases = read_cases(source)
    if len(cases) != 20:
        raise ValueError("Dify 对照要求原始20题公开开发集")
    run = Run(
        Path("data/demo/dify-runs"),
        "workflow",
        {
            "dataset_path": str(source),
            "dataset_sha256": file_hash(source),
            "api_base": base,
            "dify_version": "1.18.0",
            "human_reviewed": False,
            "formal_acceptance": False,
            "workflow_snapshot_sha256": file_hash(args.workflow_snapshot),
            "workflow_id": snapshot["workflow_id"],
        },
    )
    shutil.copyfile(args.workflow_snapshot, run.path / "workflow-snapshot.json")
    failures = 0
    latencies = []
    try:
        with httpx.Client(timeout=240, follow_redirects=False) as client:
            for case in cases:
                start = time.perf_counter()
                row = {
                    "id": case.id,
                    "question": case.question,
                    "reference_answer": case.reference_answer,
                }
                try:
                    response = client.post(
                        base + "/workflows/run",
                        headers={"Authorization": "Bearer " + key},
                        json={
                            "inputs": {"query": case.question},
                            "response_mode": "blocking",
                            "user": "public-demo-evaluation",
                        },
                    )
                    response.raise_for_status()
                    result = response.json()
                    data = result.get("data", {})
                    row.update(
                        workflow_run_id=result.get("workflow_run_id"),
                        status=data.get("status"),
                        outputs=data.get("outputs"),
                        workflow_id=data.get("workflow_id"),
                        total_tokens=data.get("total_tokens"),
                    )
                    if (
                        row["status"] != "succeeded"
                        or not isinstance(row["outputs"], dict)
                        or not row["outputs"].get("answer")
                        or row["workflow_id"] != snapshot["workflow_id"]
                    ):
                        row["status"] = "invalid_workflow_output"
                        failures += 1
                except Exception as exc:
                    row.update(status="request_error", error_type=type(exc).__name__)
                    failures += 1
                row["seconds"] = time.perf_counter() - start
                latencies.append(row["seconds"])
                append_jsonl(run.path / "results.jsonl", row)
                print(case.id, row["status"], round(row["seconds"], 2), flush=True)
                append_jsonl(
                    run.path / "reviews.template.jsonl",
                    {
                        "id": case.id,
                        "correct_human": None,
                        "keyword_failure": None,
                        "table_failure": None,
                        "notes": "",
                    },
                )
        write_json(
            run.path / "summary.json",
            {
                "n": len(cases),
                "execution_errors": failures,
                "correctness": None,
                "p50_seconds": statistics.median(latencies),
                "p95_seconds": statistics.quantiles(latencies, n=100, method="inclusive")[94],
                "human_reviewed": False,
                "formal_acceptance": False,
            },
        )
        write_json(
            run.path / "review-binding.json",
            {
                "results_sha256": file_hash(run.path / "results.jsonl"),
                "workflow_snapshot_sha256": file_hash(run.path / "workflow-snapshot.json"),
            },
        )
        run.status("completed_with_errors" if failures else "complete")
    except BaseException as exc:
        run.status("failed", error_type=type(exc).__name__)
        raise
    print(run.path)


if __name__ == "__main__":
    main()
