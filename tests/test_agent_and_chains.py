import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from kb.agent.graph import build_graph
from kb.agent.nodes import summarize_history
from kb.agent.tools import build_tools
from kb.agent.tools.document_summary import SummaryArgs, summarize_document
from kb.agent.tools.project_query import Project, ProjectArgs, query_projects
from kb.chains.prompts import NO_ANSWER
from kb.chains.rag_chain import RagChain
from kb.config import load_config
from kb.schema import Hit


class StaticRetrieval:
    def __init__(self, hits):
        self.hits = hits

    def search(self, query, filters=None):
        return self.hits


def test_no_hits_never_calls_llm():
    def fail(_):
        raise AssertionError("no hits must not call LLM")

    result = RagChain(StaticRetrieval([]), RunnableLambda(fail)).invoke("无答案问题")
    assert result["answer"] == NO_ANSWER
    assert result["sources"] == []


@pytest.mark.parametrize(
    "answer, accepted",
    [
        ("参数为 3000 毫秒 [S1]", True),
        ("参数为 3000 毫秒 [S9]", False),
        ("没有任何引用的回答", False),
        (NO_ANSWER, False),
    ],
)
def test_rag_citation_contract(make_chunk, answer, accepted):
    llm = FakeMessagesListChatModel(responses=[AIMessage(content=answer)])
    retrieval = StaticRetrieval([Hit(chunk=make_chunk(text="timeout_ms 3000 毫秒"), score=1)])
    result = RagChain(retrieval, llm).invoke("超时是多少")
    assert (result["answer"] != NO_ANSWER) == accepted
    assert bool(result["sources"]) == accepted


def test_project_filters_and_decimal_boundaries():
    projects = [
        Project(
            project_id="1",
            project_name="星河项目",
            signed_date="2025-01-01",
            amount="1000000.01",
            owner="张三",
        ),
        Project(
            project_id="2",
            project_name="天河项目",
            signed_date="2024-01-01",
            amount="50",
            owner="李四",
        ),
    ]
    args = ProjectArgs(
        project_name="星河", signed_after="2025-01-01", min_amount="1000000.01", fields=["amount"]
    )
    assert query_projects(projects, args, 80) == [
        {"project_name": "星河项目", "amount": "1000000.01"}
    ]
    assert query_projects(projects, ProjectArgs(owner="未知"), 80) == []


def test_strict_amount_filters_preserve_fractional_boundaries():
    projects = [
        Project(
            project_id=str(i),
            project_name=str(i),
            signed_date="2026-01-01",
            amount=value,
            owner="未公开",
        )
        for i, value in enumerate(["2000000", "2000000.001", "2000000.01", "2000001"])
    ]
    results = query_projects(projects, ProjectArgs(amount_gt="2000000", amount_lt="2000001"), 80)
    assert [r["amount"] for r in results] == ["2000000.001", "2000000.01"]
    with pytest.raises(ValidationError):
        ProjectArgs(amount_gt=100, max_amount=100)
    with pytest.raises(ValidationError):
        ProjectArgs(min_amount=100, amount_lt=100)


def test_summary_unique_basename_and_ambiguous_name(make_chunk):
    llm = FakeMessagesListChatModel(responses=[AIMessage(content="摘要")])
    a = make_chunk("1", "A 的内容", doc_name="a/合同.pdf", doc_id="A")
    b = make_chunk("2", "B 的内容", doc_name="b/合同.pdf", doc_id="B")
    assert summarize_document([a], SummaryArgs(doc_name="合同.pdf"), llm, 100)["summary"] == "摘要"
    assert (
        "不唯一" in summarize_document([a, b], SummaryArgs(doc_name="合同.pdf"), llm, 100)["error"]
    )
    assert (
        summarize_document([a, b], SummaryArgs(doc_name="a/合同.pdf"), llm, 100)["summary"]
        == "摘要"
    )
    assert "error" in summarize_document([a], SummaryArgs(doc_name="wrong/合同.pdf"), llm, 100)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"signed_after": "2025-02-30"},
        {"signed_after": "2026-01-01", "signed_before": "2025-01-01"},
        {"min_amount": 100, "max_amount": 10},
        {"fields": ["sql"]},
        {"sql": "drop table"},
    ],
)
def test_project_invalid_arguments(kwargs):
    with pytest.raises(ValidationError):
        ProjectArgs(**kwargs)


def test_document_summary_missing_and_map_reduce(make_chunk):
    with pytest.raises(ValidationError):
        SummaryArgs()
    chunks = [make_chunk(str(i), "正文" * 80, chunk_index=i) for i in range(3)]
    llm = FakeMessagesListChatModel(responses=[AIMessage(content="摘要，数值来自接口.docx")])
    assert "error" in summarize_document(chunks, SummaryArgs(doc_name="不存在"), llm, 100)
    result = summarize_document(chunks, SummaryArgs(doc_id="doc1"), llm, 100)
    assert result["summary"] and len(result["sources"]) == 1


def test_real_tool_schema_and_execution(tmp_path, make_chunk):
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            [
                {
                    "project_id": "P1",
                    "project_name": "星河",
                    "signed_date": "2025-01-01",
                    "amount": "123",
                    "owner": "张三",
                }
            ]
        )
    )
    config = {**load_config("agent"), "ledger_path": str(path)}
    llm = FakeMessagesListChatModel(responses=[AIMessage(content="摘要")])
    tools = build_tools(
        StaticRetrieval([Hit(chunk=make_chunk(), score=1)]), [make_chunk()], llm, config
    )
    assert {t.name for t in tools} == {
        "KnowledgeSearchTool",
        "DocumentSummaryTool",
        "ProjectQueryTool",
    }
    assert tools[0].invoke({"query": "接口"})[0]["source"]
    assert tools[1].invoke({"doc_id": "doc1"})["summary"] == "摘要"
    assert tools[2].invoke({"owner": "张三"})["projects"][0]["amount"] == "123"


class ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_graph_function_call_and_sqlite_persistence(tmp_path):
    tool = StructuredTool.from_function(
        lambda query: "证据:" + query, name="Search", description="测试检索"
    )
    llm = ToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"id": "call1", "name": "Search", "args": {"query": "timeout_ms"}}],
            ),
            AIMessage(content="结果为 3000 毫秒"),
            AIMessage(content="继续回答"),
        ]
    )
    db = tmp_path / "checkpoints.sqlite"
    config = load_config("agent")
    invocation = {"configurable": {"thread_id": "session1"}, "recursion_limit": 8}
    with SqliteSaver.from_conn_string(str(db)) as saver:
        graph = build_graph(llm, [tool], config, saver)
        result = graph.invoke({"messages": [HumanMessage(content="查询超时")]}, invocation)
        assert any(isinstance(m, ToolMessage) for m in result["messages"])
        assert result["messages"][-1].content == "结果为 3000 毫秒"
    with SqliteSaver.from_conn_string(str(db)) as saver:
        graph = build_graph(llm, [tool], config, saver)
        result = graph.invoke({"messages": [HumanMessage(content="再解释一下")]}, invocation)
        assert sum(isinstance(m, HumanMessage) for m in result["messages"]) == 2
        assert not graph.get_state({"configurable": {"thread_id": "other"}}).values


def test_history_summary_preserves_turn_and_tool_pairs():
    messages = [
        HumanMessage(content="旧问题", id="h1"),
        AIMessage(
            content="",
            id="a1",
            tool_calls=[{"id": "c1", "name": "Search", "args": {"query": "old"}}],
        ),
        ToolMessage(content="旧证据", tool_call_id="c1", id="t1"),
        AIMessage(content="旧答案", id="a2"),
        HumanMessage(content="新问题", id="h2"),
        AIMessage(content="新答案", id="a3"),
    ]
    config = {**load_config("agent"), "summarize_trigger_tokens": 1, "keep_last_turns": 1}
    llm = FakeMessagesListChatModel(responses=[AIMessage(content="历史摘要")])
    update = summarize_history({"messages": messages}, llm, config)
    assert update["summary"] == "历史摘要"
    assert [m.id for m in update["messages"]] == ["h1", "a1", "t1", "a2"]
