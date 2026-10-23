import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from kb.agent.graph import build_graph
from kb.config import load_config
from kb.eval.agent_eval import (
    AgentTask,
    apply_reviews,
    arguments_match,
    evaluate_task,
    run_agent_tasks,
    summarize_results,
)


class CallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def task():
    return AgentTask(
        question="查询超时",
        expected_tools=["KnowledgeSearchTool"],
        expected_args=[{"query": "timeout_ms"}],
        answer_points=["3000 毫秒"],
    )


def graph_for(responses, tool=None):
    tool = tool or StructuredTool.from_function(
        lambda query: "3000 毫秒", name="KnowledgeSearchTool", description="测试检索"
    )
    return build_graph(
        CallingModel(responses=responses), [tool], load_config("agent"), InMemorySaver()
    )


def calling_message():
    return AIMessage(
        content="",
        tool_calls=[
            {"id": "call1", "name": "KnowledgeSearchTool", "args": {"query": "timeout_ms"}}
        ],
    )


def test_agent_trace_and_semantic_argument_normalization():
    graph = graph_for([calling_message(), AIMessage(content="3000 毫秒")])
    result = evaluate_task(graph, task().model_dump(), "isolated1", 8)
    assert result["steps"] == 2 and result["tools_correct"] and result["args_correct"]
    assert result["answer"] == "3000 毫秒" and result["completed_human"] is None
    assert len(result["messages"]) == 4
    assert arguments_match(
        [{"name": "ProjectQueryTool", "args": {"min_amount": 100, "owner": None}}],
        {"expected_tools": ["ProjectQueryTool"], "expected_args": [{"min_amount": "100.00"}]},
    )


def test_recursion_failure_preserves_partial_calls():
    # Each LLM response needs a fresh message ID; recycling one fake message would
    # replace an earlier graph message rather than simulate another reasoning step.
    graph = graph_for([calling_message(), calling_message(), calling_message()])
    result = evaluate_task(graph, task().model_dump(), "isolated2", 4)
    assert result["failure"] == "超步数"
    assert result["calls"] and result["messages"]
    assert result["steps"] >= 1
    assert result["answer"] is None


def test_tool_error_is_not_silent_success():
    def broken(query: str):
        raise RuntimeError("合成工具异常")

    tool = StructuredTool.from_function(broken, name="KnowledgeSearchTool", description="失败测试")
    graph = graph_for([calling_message(), AIMessage(content="工具暂不可用")], tool)
    result = evaluate_task(graph, task().model_dump(), "isolated3", 8)
    assert result["failure"] == "工具执行错误"


def test_bad_expected_arguments_rejected_before_call():
    with pytest.raises(ValidationError):
        AgentTask(
            question="test",
            expected_tools=["ProjectQueryTool"],
            expected_args=[{"signed_after": "2025-02-30"}],
            answer_points=[],
        )
    with pytest.raises(ValidationError):
        AgentTask(
            question="test", expected_tools=["ProjectQueryTool"], expected_args=[], answer_points=[]
        )


def test_wrong_tool_is_not_called_parameter_error():
    graph = graph_for([AIMessage(content="跳过了工具")])
    result = evaluate_task(graph, task().model_dump(), "isolated4", 8)
    assert result["failure"] == "选错工具" and result["args_correct"] is None
    summary = summarize_results([result])
    assert summary["parameter_unknown_n"] == 1 and summary["parameter_compared_n"] == 0
    assert summary["completion_rate"] is None


def test_batch_uses_new_sessions_and_review_revisions(tmp_path):
    first, second = task(), task().model_copy(update={"id": "second", "question": "再次查询"})
    graph = graph_for([calling_message(), AIMessage(content="3000 毫秒")])
    path = tmp_path / "tasks.jsonl"
    path.write_text(first.model_dump_json() + "\n" + second.model_dump_json() + "\n")
    snapshot = {
        "configs": {
            "agent": load_config("agent"),
            "eval": {**load_config("eval"), "runs_dir": str(tmp_path / "runs")},
        }
    }
    run = run_agent_tasks(graph, [first, second], snapshot, path)
    raw = (run / "results.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[0]["thread_id"] != rows[1]["thread_id"]
    assert all(row["steps"] == 2 for row in rows)
    review = tmp_path / "reviews.jsonl"
    review.write_text(json.dumps({"id": first.id, "completed_human": True}) + "\n")
    partial = apply_reviews(run, review)
    assert partial["reviewed_n"] == 1 and partial["completion_rate"] is None
    assert partial["reviewed_completion_rate"] == 1
    review.write_text(json.dumps({"id": rows[1]["id"], "completed_human": True}))
    assert apply_reviews(run, review)["completion_rate"] == 1
    assert (run / "results.jsonl").read_bytes() == raw
    assert len(list(run.glob("review-*"))) == 2
    review.write_text(json.dumps({"id": "unknown", "completed_human": True}))
    with pytest.raises(ValueError):
        apply_reviews(run, review)
