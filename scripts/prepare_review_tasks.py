"""Prepare source-backed Agent candidates and a browser QA review form."""

import hashlib
import json
from pathlib import Path

from kb.eval.agent_eval import AgentTask
from kb.eval.dataset import read_cases

ROOT = Path("data/review/public-v1")


def write_rows(path, rows):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def agent_tasks():
    manifest = json.loads((ROOT / "source_manifest.json").read_text())["documents"]
    k8s = [d for d in manifest if d.get("schema_key")]
    ledger = json.loads(Path("data/ledger/projects.json").read_text())
    ledger = [r for r in ledger if r.get("source_url")][:2]
    batches = []
    labels = {
        "amount": "合同金额",
        "supplier": "供应商",
        "signed_date": "签订日期",
        "owner": "项目负责人",
        "buyer": "采购人",
        "project_id": "项目编号",
    }
    for batch in ["baseline", "retest"]:
        rows = []

        def add(question, tools, args, points, batch=batch, rows=rows):
            task = AgentTask(
                id=f"PUBLIC-{batch.upper()}-{len(rows) + 1:03d}",
                question=question,
                expected_tools=tools,
                expected_args=args,
                answer_points=points,
            )
            rows.append(task.model_dump())

        if batch == "baseline":
            for project in ledger:
                for field, label in labels.items():
                    add(
                        f"查询{project['project_name']}的{label}，只返回此字段。",
                        ["ProjectQueryTool"],
                        [{"project_name": project["project_name"], "fields": [field]}],
                        [str(project[field])],
                    )
        else:
            filters = [
                (
                    "2026年4月签订的项目",
                    {"signed_after": "2026-04-01", "signed_before": "2026-04-30"},
                    ledger[1],
                ),
                (
                    "2026年7月签订的项目",
                    {"signed_after": "2026-07-01", "signed_before": "2026-07-31"},
                    ledger[0],
                ),
                ("金额严格小于200万元的项目", {"amount_lt": "2000000"}, ledger[0]),
                ("金额大于等于280万元的项目", {"min_amount": "2800000"}, ledger[1]),
            ]
            for question, args, project in filters:
                add(
                    f"查询{question}，返回项目名称和金额。",
                    ["ProjectQueryTool"],
                    [{**args, "fields": ["project_name", "amount"]}],
                    [project["project_name"], project["amount"]],
                )
        offset = 0 if batch == "baseline" else 12
        selected = k8s[offset : offset + (12 if batch == "baseline" else 8)]
        for item in selected:
            name = item["id"].removeprefix("k8s-")
            query = f"Kubernetes {name} 对象说明"
            add(
                f"在 Kubernetes 项目中检索“{query}”，说明这个对象的用途。",
                ["KnowledgeSearchTool"],
                [{"query": query, "project_name": "Kubernetes"}],
                [name],
            )
        selected = k8s[(0 if batch == "baseline" else 12) : (8 if batch == "baseline" else 16)]
        for item in selected:
            add(
                f"总结 {item['file_name']} 的全文。",
                ["DocumentSummaryTool"],
                [{"doc_name": item["file_name"]}],
                [item["id"].removeprefix("k8s-")],
            )
        selected = k8s[(0 if batch == "baseline" else 20) : (8 if batch == "baseline" else 24)]
        for i, item in enumerate(selected):
            project = ledger[i % 2]
            field = "supplier" if batch == "baseline" else "signed_date"
            add(
                f"先查询{project['project_name']}的{labels[field]}（只返回该字段），"
                f"再总结 {item['file_name']} 全文。请分别列出两部分结果与来源。",
                ["ProjectQueryTool", "DocumentSummaryTool"],
                [
                    {"project_name": project["project_name"], "fields": [field]},
                    {"doc_name": item["file_name"]},
                ],
                [str(project[field]), item["id"].removeprefix("k8s-")],
            )
        assert len(rows) == (40 if batch == "baseline" else 20)
        batches.extend(rows)
        write_rows(ROOT / f"agent_{batch}_{len(rows)}.jsonl", rows)
    assert len({r["question"] for r in batches}) == 60
    write_rows(
        ROOT / "agent_reviews.template.jsonl",
        [{"id": r["id"], "approved": None, "reviewer": "", "notes": ""} for r in batches],
    )


def review_html():
    path = ROOT / "qa_candidates_200.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    read_cases(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = json.dumps({"sha256": sha, "rows": rows}, ensure_ascii=False).replace("<", "\\u003c")
    html = Path(__file__).with_name("templates").joinpath("qa_review.html").read_text()
    (ROOT / "review.html").write_text(html.replace("PAYLOAD", payload), encoding="utf-8")


if __name__ == "__main__":
    agent_tasks()
    review_html()
    print(ROOT)
