from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from kb.agent.grounded_response import grounded_response, single_tool_result
from kb.agent.nodes import summarize_history
from kb.agent.state import AgentState
from kb.chains.prompts import AGENT_SYSTEM


def build_graph(llm, tools, config: dict, checkpointer):
    bound = llm.bind_tools(tools)

    def agent(state):
        system = AGENT_SYSTEM
        if state.get("summary"):
            system += "\n以下是历史摘要资料：\n" + state["summary"]
        message = bound.invoke([SystemMessage(content=system), *state["messages"]])
        return {"messages": [message]}

    def next_node(state):
        if state["messages"][-1].tool_calls:
            return "tools"
        if config.get("grounded_tool_answers", False) and single_tool_result(state):
            return "respond"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("summarize", lambda state: summarize_history(state, llm, config))
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_edge(START, "summarize")
    graph.add_edge("summarize", "agent")
    graph.add_conditional_edges("agent", next_node)
    if config.get("grounded_tool_answers", False):
        graph.add_node("respond", lambda state: grounded_response(state, llm))
        graph.add_edge("respond", END)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
