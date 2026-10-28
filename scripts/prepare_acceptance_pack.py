"""Export real run evidence for human review without assigning human verdicts."""

import argparse
import json
import random
from pathlib import Path

from kb.eval.runs import file_hash, write_json

ROOT = Path("data/review/public-v1")


def latest(kind):
    runs = sorted(Path("data/demo/cloud-runs").glob(f"*_{kind}_*/summary.json"))
    if not runs:
        raise FileNotFoundError(f"尚无已完成的 {kind} 运行")
    run = runs[-1].parent
    status = json.loads((run / "status.json").read_text())["status"]
    if status not in {"complete", "completed_with_errors"}:
        raise ValueError(f"{kind} 运行尚未完成")
    return run


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_rows(path, rows):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def prepare_agents():
    for kind in ["agent_baseline", "agent_retest"]:
        run = latest(kind)
        rows = read_rows(run / "results.jsonl")
        lines = [
            f"# {kind} 逐条人工审核",
            "",
            f"运行：`{run}`",
            "",
            "依据工具证据判断回答是否完成用户任务。参数严格不一致不必然代表业务失败；"
            "同义词、项目简称、返回字段、遗漏与幻觉均应写明。",
            "审核者填写同目录任务模板后使用 `kb review-agent <运行目录> <审核JSONL>` 导入。",
            "",
        ]
        for row in rows:
            lines += [
                f"## {row['id']}",
                "",
                row["question"],
                "",
                "预期调用：",
                "```json",
                json.dumps(
                    dict(tools=row["expected_tools"], args=row["expected_args"]),
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "实际调用：",
                "```json",
                json.dumps(row["calls"], ensure_ascii=False, indent=2),
                "```",
                "回答：",
                "",
                str(row["answer"]),
                "",
                "工具证据：",
                "",
            ]
            for message in row["messages"]:
                if message.get("type") == "tool":
                    lines += ["```text", str(message["content"]), "```", ""]
            lines += [
                f"自动诊断：{row['failure'] or '未发现调用错误'}；"
                f"耗时 {row['latency_seconds']:.2f}s。",
                "",
            ]
        (ROOT / f"{kind}_review.md").write_text("\n".join(lines), encoding="utf-8")
        write_rows(
            ROOT / f"{kind}_completion.template.jsonl",
            [dict(id=r["id"], completed_human=None, failure_human=None, notes="") for r in rows],
        )
        write_json(
            ROOT / f"{kind}_review_binding.json",
            dict(run=str(run), results_sha256=file_hash(run / "results.jsonl")),
        )


def prepare_judge():
    run = latest("ragas")
    snapshot = json.loads((run / "snapshot.json").read_text())
    current = json.loads(Path("data/index/active.json").read_text())
    if snapshot["index"]["fingerprint"] != current["fingerprint"]:
        raise ValueError("最新 RAGAS 仍是历史索引，请先完成当前索引实测")
    sampled = []
    for mode in ["A", "D"]:
        rows = [r for r in read_rows(run / mode / "results.jsonl") if r["status"] == "complete"]
        if len(rows) < 10:
            raise ValueError("每种配置至少需要10条完整评分")
        chosen = random.Random(42).sample(rows, 10)
        sampled += [{**r, "review_id": mode + ":" + r["id"], "variant": mode} for r in chosen]
    write_rows(ROOT / "judge_review_20.jsonl", sampled)
    write_json(
        ROOT / "judge_reviews.template.json",
        {
            "run": str(run),
            "sample_sha256": file_hash(ROOT / "judge_review_20.jsonl"),
            "reviews": [
                {
                    "id": r["review_id"],
                    "reviewer": "",
                    "faithfulness_human": None,
                    "answer_relevancy_human": None,
                    "context_recall_human": None,
                    "notes": "",
                }
                for r in sampled
            ],
        },
    )
    lines = [
        "# 20 条裁判人工复核",
        "",
        f"来源：`{run}`；A/D 各10条，固定随机种子42。",
        "",
        "先依据问题、答案、检索证据和参考答案独立给三个指标打0到1分，并填写审核人及依据。",
        "忠实度检查回答各主张是否有证据；相关性检查是否直接回答所问；召回检查证据覆盖参考答案。",
        "自动评分列于每条末尾，仅供独立评分后比较。未填写值保持空，不默认为通过。",
        "",
    ]
    for row in sampled:
        lines += [
            f"## {row['review_id']}",
            "",
            "问题：" + row["question"],
            "",
            "模型回答：",
            "",
            row["answer"],
            "",
            "参考答案：",
            "",
            row["reference_answer"],
            "",
            "检索证据：",
            "",
        ]
        for source in row["sources"]:
            lines += [source["source"], "```text", source["text"], "```", ""]
        # Preserve retrieved contexts even when citation validation removed displayed sources.
        if not row["sources"]:
            lines += ["```text", "\n\n".join(row["retrieved_contexts"]), "```", ""]
        lines += ["自动指标：" + json.dumps(row["metrics"], ensure_ascii=False), ""]
    (ROOT / "judge_review_20.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents-only", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    ROOT = args.output_dir
    ROOT.mkdir(parents=True, exist_ok=True)
    prepare_agents()
    if not args.agents_only:
        prepare_judge()
    print(ROOT)
