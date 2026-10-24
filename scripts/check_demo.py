"""Exercise the live demo with real models and preserve every response."""

import hashlib
import json
import re
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from kb.chains.prompts import NO_ANSWER
from kb.config import load_config
from kb.eval.agent_eval import arguments_match


def normalize(text):
    return re.sub(r"[\s,，]", "", text).lower()


def query(base, mode, question, session=None):
    payload = {"mode": mode, "question": question, "session": session or str(uuid.uuid4())}
    request = Request(
        base + "/api/query",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=300) as response:
        return json.load(response)


def main():
    out = Path("data/demo/runs") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True)
    metadata = {
        "model": load_config("demo"),
        "index": json.loads(Path("data/index/active.json").read_text()),
        "review": json.loads(Path("data/demo/review.json").read_text()),
        "code_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path("src/kb").rglob("*.py"))
        },
        "retrieval": {**load_config("retrieval"), **load_config("demo").get("retrieval", {})},
        "agent": {
            **load_config("agent"),
            "grounded_tool_answers": True,
            "ledger_source": "官方采购公告整理的演示台账（非企业内部数据）",
        },
    }
    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    base = f"http://127.0.0.1:{load_config('demo')['web_port']}"
    qa, agents = [], []
    for file_name, mode in [("qa.jsonl", "rag"), ("agent_tasks.jsonl", "agent")]:
        for line in (Path("data/demo") / file_name).read_text().splitlines():
            case = json.loads(line)
            try:
                result = query(base, mode, case["question"])
                row = {
                    "id": case["id"],
                    "question": case["question"],
                    "response": result,
                    "answer_points_present": all(
                        normalize(p) in normalize(result["answer"]) for p in case["answer_points"]
                    ),
                }
                if mode == "rag":
                    expected = {
                        c for context in case["reference_contexts"] for c in context["chunk_ids"]
                    }
                    actual = {s["chunk_id"] for s in result["sources"]}
                    row["no_answer_case"] = case["question_type"] == "no_answer"
                    row["reference_evidence_retrieved"] = (
                        bool(expected & actual) if expected else None
                    )
                    row["refused"] = result["answer"] == NO_ANSWER
                else:
                    calls = [
                        {"name": t["name"], "args": t["arguments"]}
                        for t in result["trace"]
                        if t["type"] == "call"
                    ]
                    row["tools_correct"] = [c["name"] for c in calls] == case["expected_tools"]
                    row["strict_args_match"] = arguments_match(calls, case)
            except Exception as exc:
                row = {"id": case["id"], "error": f"{type(exc).__name__}: {exc}"}
            (qa if mode == "rag" else agents).append(row)
            with (out / f"{mode}.jsonl").open("a") as output:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                row["id"],
                {k: v for k, v in row.items() if k not in {"question", "response"}},
                flush=True,
            )
    session = str(uuid.uuid4())
    followups = []
    for q in ["查询海口市AI+数字内容生产服务的供应商。", "它的合同签订日期是什么时候？"]:
        followups.append({"question": q, "response": query(base, "agent", q, session)})
    (out / "multi-turn.json").write_text(json.dumps(followups, ensure_ascii=False, indent=2))
    answerable = [r for r in qa if not r.get("error") and not r["no_answer_case"]]
    refusals = [r for r in qa if not r.get("error") and r["no_answer_case"]]
    summary = {
        "demo_only": True,
        "model": load_config("demo")["model_repository"],
        "qa_count": len(qa),
        "agent_count": len(agents),
        "errors": sum("error" in r for r in qa + agents),
        "qa_answer_points_passed": sum(r.get("answer_points_present", False) for r in qa),
        "qa_reference_evidence_hits": sum(r["reference_evidence_retrieved"] for r in answerable),
        "qa_answerable_count": len(answerable),
        "no_answer_correct": sum(r["refused"] for r in refusals),
        "no_answer_count": len(refusals),
        "agent_tools_correct": sum(r.get("tools_correct", False) for r in agents),
        "agent_strict_args_match": sum(r.get("strict_args_match", False) for r in agents),
        "agent_answer_points_passed": sum(r.get("answer_points_present", False) for r in agents),
        "multi_turn_answer": followups[-1]["response"]["answer"],
        "median_seconds": statistics.median(
            r["response"]["latency_seconds"] for r in qa + agents if "response" in r
        ),
        "limitations": "演示集自动检查；答案要点与证据命中统计分别记录，不等同于 RAGAS 指标。",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    Path("data/demo/latest-run.txt").write_text(str(out))
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
