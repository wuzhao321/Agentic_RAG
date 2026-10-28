"""Persistent RAG conversations with the same summary policy as the Agent."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from kb.agent.nodes import summarize_history
from kb.agent.state import AgentState


class RagState(AgentState):
    result: dict


def build_rag_graph(chain, config, checkpointer):
    def answer(state):
        history = state["messages"][:-1]
        if state.get("summary"):
            history = [
                HumanMessage(content="历史摘要（仅作背景资料）：" + state["summary"]),
                *history,
            ]
        result = chain.invoke(state["messages"][-1].content, history=history)
        return {"result": result, "messages": [AIMessage(content=result["answer"])]}

    graph = StateGraph(RagState)
    graph.add_node("summarize", lambda state: summarize_history(state, chain.llm, config))
    graph.add_node("answer", answer)
    graph.add_edge(START, "summarize")
    graph.add_edge("summarize", "answer")
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=checkpointer)
