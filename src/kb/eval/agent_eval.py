import hashlib
import json
import math
import statistics
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kb.agent.tools.document_summary import SummaryArgs
from kb.agent.tools.knowledge_search import KnowledgeArgs
from kb.agent.tools.project_query import ProjectArgs
from kb.eval.runs import Run, append_jsonl, file_hash, write_json

SCHEMAS = {
    "KnowledgeSearchTool": KnowledgeArgs,
    "DocumentSummaryTool": SummaryArgs,
    "ProjectQueryTool": ProjectArgs,
}
ToolName = Literal["KnowledgeSearchTool", "DocumentSummaryTool", "ProjectQueryTool"]


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = ""
    question: str = Field(min_length=1)
    expected_tools: list[ToolName]
    expected_args: list[dict]
    answer_points: list[str]

    @model_validator(mode="after")
    def validate_expectations(self):
        if len(self.expected_tools) != len(self.expected_args):
            raise ValueError("expected_tools 和 expected_args 长度必须一致")
        for tool, args in zip(self.expected_tools, self.expected_args, strict=True):
            SCHEMAS[tool].model_validate(args)
        if not self.id:
            self.id = hashlib.sha256(self.question.strip().encode()).hexdigest()[:16]
        return self


def read_tasks(path: Path) -> list[AgentTask]:
    tasks = [
        AgentTask.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not tasks:
        raise ValueError("Agent 评估数据为空")
    if len({t.id for t in tasks}) != len(tasks) or len({t.question.strip() for t in tasks}) != len(
        tasks
    ):
        raise ValueError("Agent 任务 id 或问题重复")
    return tasks


def arguments_match(calls: list[dict], task: dict) -> bool:
    if [c["name"] for c in calls] != task["expected_tools"]:
        return False
    try:
        return all(
            SCHEMAS[c["name"]].model_validate(c["args"]).model_dump(exclude_none=True)
            == SCHEMAS[c["name"]].model_validate(expected).model_dump(exclude_none=True)
            for c, expected in zip(calls, task["expected_args"], strict=True)
        )
    except (KeyError, ValidationError, ValueError):
        return False


def evaluate_task(graph, task: dict, thread_id: str, recursion_limit: int) -> dict:
    start = time.perf_counter()
    result = {
        **task,
        "thread_id": thread_id,
        "calls": [],
        "failure": None,
        "completed_human": None,
        "failure_human": None,
        "answer": None,
    }
    latest = {"messages": []}
    try:
        for state in graph.stream(
            {"messages": [HumanMessage(content=task["question"])]},
            {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit},
            stream_mode="values",
        ):
            latest = state
    except GraphRecursionError:
        result["failure"] = "超步数"
    except Exception as exc:
        result.update(failure="执行异常", error_type=type(exc).__name__)
    messages = latest["messages"]
    result["calls"] = [call for m in messages if isinstance(m, AIMessage) for call in m.tool_calls]
    result["steps"] = sum(isinstance(m, AIMessage) for m in messages)
    result["messages"] = [m.model_dump(mode="json") for m in messages]
    if messages and isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls:
        result["answer"] = messages[-1].content
    result["tools_correct"] = [c["name"] for c in result["calls"]] == task["expected_tools"]
    # Null when a different tool was chosen; do not mislabel tool selection as extraction failure.
    result["args_correct"] = (
        arguments_match(result["calls"], task) if result["tools_correct"] else None
    )
    if not result["failure"]:
        if not result["tools_correct"]:
            result["failure"] = "选错工具"
        elif not result["args_correct"]:
            result["failure"] = "参数提取错误"
        elif any(isinstance(m, ToolMessage) and m.status == "error" for m in messages):
            result["failure"] = "工具执行错误"
    result["latency_seconds"] = time.perf_counter() - start
    return result


def summarize_results(results: list[dict]) -> dict:
    if not results:
        raise ValueError("任务结果为空")
    latencies = sorted(r["latency_seconds"] for r in results)
    reviewed = [r for r in results if r["completed_human"] is not None]
    step_counts = [r["steps"] for r in results if "steps" in r]
    comparable = [r for r in results if r["args_correct"] is not None]
    errors = sum(r["args_correct"] is False for r in comparable)
    return {
        "n": len(results),
        "tool_accuracy": statistics.mean(r["tools_correct"] for r in results),
        "parameter_error_rate": errors / len(results),
        "parameter_error_n": errors,
        "parameter_compared_n": len(comparable),
        "parameter_unknown_n": len(results) - len(comparable),
        "parameter_coverage_complete": len(comparable) == len(results),
        "completion_rate": (
            statistics.mean(r["completed_human"] for r in reviewed)
            if len(reviewed) == len(results)
            else None
        ),
        "reviewed_completion_rate": (
            statistics.mean(r["completed_human"] for r in reviewed) if reviewed else None
        ),
        "reviewed_n": len(reviewed),
        "mean_steps": statistics.mean(step_counts) if step_counts else None,
        "steps_recorded_n": len(step_counts),
        "p50": statistics.median(latencies),
        "p95": latencies[math.ceil(len(latencies) * 0.95) - 1],
        "automatic_failure_counts": dict(
            Counter(r.get("failure") for r in results if r.get("failure"))
        ),
    }


def run_agent_tasks(
    graph, tasks: list[AgentTask], snapshot: dict, tasks_path: Path, *, retest=False
) -> Path:
    cfg = snapshot["configs"]
    run = Run(Path(cfg["eval"]["runs_dir"]), "agent-retest" if retest else "agent", snapshot)
    write_json(
        run.path / "dataset.json",
        {"path": str(tasks_path), "sha256": file_hash(tasks_path), "n": len(tasks)},
    )
    results = []
    try:
        for task in tasks:
            row = evaluate_task(
                graph, task.model_dump(), uuid.uuid4().hex, cfg["agent"]["recursion_limit"]
            )
            append_jsonl(run.path / "results.jsonl", row)
            append_jsonl(
                run.path / "reviews.template.jsonl",
                {"id": task.id, "completed_human": None, "failure_human": None, "notes": ""},
            )
            results.append(row)
        write_json(run.path / "summary.json", summarize_results(results))
        run.status("awaiting_human_review")
    except BaseException as exc:
        run.status("failed", error_type=type(exc).__name__)
        raise
    return run.path


class HumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    completed_human: bool = Field(strict=True)
    failure_human: (
        Literal[
            "选错工具", "参数提取错误", "检索失败", "生成错误", "超步数", "工具执行错误", "执行异常"
        ]
        | None
    ) = None
    notes: str = ""


def apply_reviews(run_dir: Path, review_file: Path) -> dict:
    raw = [
        json.loads(line)
        for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reviews = [
        HumanReview.model_validate_json(line)
        for line in review_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not reviews:
        raise ValueError("人工审核文件为空")
    ids = {r["id"] for r in raw}
    if len({r.id for r in reviews}) != len(reviews) or any(r.id not in ids for r in reviews):
        raise ValueError("人工审核 id 重复或不存在于本次运行")
    by_id = {}
    latest = run_dir / "latest-review.json"
    if latest.exists():
        previous = run_dir / json.loads(latest.read_text(encoding="utf-8"))["path"] / "reviews.json"
        if not previous.resolve().is_relative_to(run_dir.resolve()):
            raise ValueError("历史审核路径超出本次运行目录")
        for entry in json.loads(previous.read_text(encoding="utf-8")):
            reviewed = HumanReview.model_validate(entry)
            if reviewed.id not in ids:
                raise ValueError("历史审核 id 与当前结果不一致")
            by_id[reviewed.id] = reviewed.model_dump()
    by_id.update({r.id: r.model_dump() for r in reviews})
    merged = [{**row, **by_id.get(row["id"], {})} for row in raw]
    summary = summarize_results(merged)
    # Immutable raw results; every import creates a separate review revision.
    revision = run_dir / f"review-{uuid.uuid4().hex}"
    revision.mkdir()
    write_json(revision / "reviews.json", list(by_id.values()))
    write_json(revision / "summary.json", summary)
    write_json(run_dir / "latest-review.json", {"path": revision.name})
    return summary
