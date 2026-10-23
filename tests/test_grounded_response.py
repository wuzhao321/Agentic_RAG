from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite import SqliteSaver

from kb.agent.graph import build_graph
from kb.agent.grounded_response import grounded_response, single_tool_result
from kb.chains.prompts import NO_ANSWER
from kb.config import load_config


def state(tool, content):
    import json

    return {
        "messages": [
            AIMessage(
                content="", tool_calls=[{"id": "c", "name": tool, "args": {"query": "问题"}}]
            ),
            ToolMessage(tool_call_id="c", name=tool, content=json.dumps(content)),
        ]
    }


def test_project_output_preserves_decimal_and_unknown_owner_without_llm():
    data = {
        "source": "公开台账",
        "projects": [{"project_name": "P", "amount": "2000000.01", "owner": "未公开"}],
    }
    result = grounded_response(state("ProjectQueryTool", data), None)
    text = result["messages"][0].content
    assert "合同金额（人民币元）：2000000.01" in text and "项目负责人：未公开" in text


def test_summary_not_rewritten():
    data = {"summary": "金额 168.679200 万元", "sources": ["合同.pdf > 第 1 页"]}
    result = grounded_response(state("DocumentSummaryTool", data), None)
    assert result["messages"][0].content == "金额 168.679200 万元\n\n来源：合同.pdf > 第 1 页"


def test_knowledge_uses_rag_citation_validation():
    llm = FakeMessagesListChatModel(responses=[AIMessage(content="不带引用的内容")])
    data = [{"text": "证据", "source": "接口.docx", "chunk_id": "1"}]
    result = grounded_response(state("KnowledgeSearchTool", data), llm)
    assert result["messages"][0].content == NO_ANSWER


def test_tool_errors_and_multiple_calls_continue_agent_loop():
    assert single_tool_result(state("DocumentSummaryTool", {"error": "文档名不唯一"})) is None
    multiple = state("KnowledgeSearchTool", [])
    multiple["messages"][0].tool_calls.append({"name": "ProjectQueryTool", "id": "d", "args": {}})
    assert single_tool_result(multiple) is None


def test_final_draft_is_replaced_in_history():
    current = state("ProjectQueryTool", {"source": "台账", "projects": []})
    current["messages"].append(AIMessage(id="draft", content="不受支持的推测"))
    result = grounded_response(current, None)
    assert result["messages"][0].id == "draft"
    assert "不受支持" not in result["messages"][0].content


class ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_graph_checkpoint_contains_grounded_answer_instead_of_draft():
    tool = StructuredTool.from_function(
        lambda query: {"source": "台账", "projects": [{"project_name": "P", "amount": "100.01"}]},
        name="ProjectQueryTool",
        description="查询项目",
    )
    llm = ToolCallingModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"id": "p", "name": tool.name, "args": {"query": "P"}}]
            ),
            AIMessage(content="错误草稿金额 1000 元"),
        ]
    )
    config = {**load_config("agent"), "grounded_tool_answers": True}
    with SqliteSaver.from_conn_string(":memory:") as saver:
        graph = build_graph(llm, [tool], config, saver)
        run = {"configurable": {"thread_id": "single"}, "recursion_limit": 8}
        result = graph.invoke({"messages": [HumanMessage(content="P 的金额？")]}, run)
        assert "100.01" in result["messages"][-1].content
        saved = graph.get_state(run).values["messages"]
        assert not any("错误草稿" in m.content for m in saved)
        assert len([m for m in saved if isinstance(m, AIMessage) and not m.tool_calls]) == 1


def test_grounded_mode_preserves_sequential_multi_tool_planning():
    first = StructuredTool.from_function(
        lambda query: [],
        name="KnowledgeSearchTool",
        description="检索知识",
    )
    second = StructuredTool.from_function(
        lambda query: {"source": "台账", "projects": []},
        name="ProjectQueryTool",
        description="查询项目",
    )
    llm = ToolCallingModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"id": "k", "name": first.name, "args": {"query": "P"}}]
            ),
            AIMessage(
                content="", tool_calls=[{"id": "p", "name": second.name, "args": {"query": "P"}}]
            ),
            AIMessage(content="知识库和台账都没有匹配结果。"),
        ]
    )
    config = {**load_config("agent"), "grounded_tool_answers": True}
    with SqliteSaver.from_conn_string(":memory:") as saver:
        graph = build_graph(llm, [first, second], config, saver)
        result = graph.invoke(
            {"messages": [HumanMessage(content="查知识库并查台账")]},
            {"configurable": {"thread_id": "multi"}, "recursion_limit": 8},
        )
    assert [m.name for m in result["messages"] if isinstance(m, ToolMessage)] == [
        first.name,
        second.name,
    ]
    assert result["messages"][-1].content == "知识库和台账都没有匹配结果。"
