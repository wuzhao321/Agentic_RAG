from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from kb.chains.conversation import build_rag_graph
from kb.chains.rag_chain import RagChain
from kb.config import load_config
from kb.schema import Hit


def test_judge_receives_exact_generation_evidence_even_after_citation_failure():
    seen = []

    class Model(FakeMessagesListChatModel):
        def _generate(self, messages, **kwargs):
            seen.append(messages[-1].content)
            return super()._generate(messages, **kwargs)

    sources = [{"id": "S1", "source": "合同.pdf > 第 2 页", "text": "金额为 100 元"}]
    model = Model(responses=[AIMessage(content="100 元 [S1]"), AIMessage(content="100 元")])
    chain = RagChain(None, model)
    for _ in range(2):
        result = chain.answer_sources("合同金额？", sources)
        assert result["retrieved_contexts"] == ["[S1] 合同.pdf > 第 2 页\n金额为 100 元"]
        assert seen[-1].endswith("\n\n".join(result["retrieved_contexts"]))
    assert result["sources"] == []
    assert result["warning"]


def test_followup_uses_resolved_question_in_retrieval_and_generation(make_chunk):
    seen = []

    class Search:
        def search(self, query):
            seen.append(query)
            return [Hit(chunk=make_chunk(text="海口项目合同金额为 2861000 元"), score=1)]

    class Model(FakeMessagesListChatModel):
        def _generate(self, messages, **kwargs):
            seen.append(messages[-1].content)
            return super()._generate(messages, **kwargs)

    model = Model(
        responses=[
            AIMessage(content="海口项目合同金额是多少？"),
            AIMessage(content="2861000 元 [S1]"),
        ]
    )
    result = RagChain(Search(), model).invoke("它多少钱？", [HumanMessage(content="海口项目")])
    assert result["query"] == "海口项目合同金额是多少？"
    assert seen[1] == "海口项目合同金额是多少？"
    assert "问题：海口项目合同金额是多少？" in seen[2]


def test_rag_summary_persistence_and_mode_isolation(tmp_path):
    class Chain:
        llm = FakeMessagesListChatModel(responses=[AIMessage(content="早期关注海口项目")])
        histories = []

        def invoke(self, question, history=None):
            self.histories.append(history)
            return {"answer": question + " [S1]", "sources": []}

    chain = Chain()
    cfg = {**load_config("agent"), "summarize_trigger_tokens": 1, "keep_last_turns": 2}
    invocation = {"configurable": {"thread_id": "rag-test"}}
    db = str(tmp_path / "test.sqlite")
    with SqliteSaver.from_conn_string(db) as saver:
        graph = build_rag_graph(chain, cfg, saver)
        for text in ["海口项目", "另一个话题", "第三个话题", "它的金额"]:
            state = graph.invoke({"messages": [HumanMessage(content=text)]}, invocation)
        assert state["summary"] == "早期关注海口项目"
        assert sum(isinstance(m, HumanMessage) for m in state["messages"]) == 2
        assert chain.histories[-1][0].content == "历史摘要（仅作背景资料）：早期关注海口项目"
        assert not graph.get_state({"configurable": {"thread_id": "web-test"}}).values
    with SqliteSaver.from_conn_string(db) as saver:
        graph = build_rag_graph(chain, cfg, saver)
        assert graph.get_state(invocation).values["result"]["answer"] == "它的金额 [S1]"
