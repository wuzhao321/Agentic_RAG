"""Finish one successful tool call without a second unconstrained agent rewrite."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kb.chains.rag_chain import RagChain

LABELS = {
    "project_id": "项目编号",
    "project_name": "项目名称",
    "signed_date": "合同签订日期",
    "amount": "合同金额（人民币元）",
    "owner": "项目负责人",
    "buyer": "采购人",
    "supplier": "供应商",
    "source_url": "来源",
    "amount_original": "公告原始金额",
}


def single_tool_result(state):
    messages = state["messages"]
    starts = [i for i, message in enumerate(messages) if isinstance(message, HumanMessage)]
    current = messages[starts[-1] :] if starts else messages
    results = [message for message in current if isinstance(message, ToolMessage)]
    if len(results) != 1:
        return None
    last = results[0]
    call_message = next(
        (m for m in reversed(current) if isinstance(m, AIMessage) and m.tool_calls), None
    )
    if not call_message or len(call_message.tool_calls) != 1 or last.status == "error":
        return None
    call = call_message.tool_calls[0]
    if call["id"] != last.tool_call_id:
        return None
    try:
        value = json.loads(last.content)
    except (ValueError, TypeError):
        return None
    if isinstance(value, dict) and "error" in value:
        return None
    if call["name"] == "KnowledgeSearchTool" and isinstance(value, list):
        return call, value
    if call["name"] == "ProjectQueryTool" and isinstance(value, dict) and "projects" in value:
        return call, value
    if call["name"] == "DocumentSummaryTool" and isinstance(value, dict) and "summary" in value:
        return call, value
    return None


def grounded_response(state, llm):
    call, value = single_tool_result(state)
    if call["name"] == "KnowledgeSearchTool":
        sources = [{"id": f"S{i}", **source} for i, source in enumerate(value, 1)]
        # Retrieval has already happened in the real tool; no second query or replacement evidence.
        answer = RagChain(None, llm).answer_sources(call["args"]["query"], sources)["answer"]
    elif call["name"] == "DocumentSummaryTool":
        answer = value["summary"] + "\n\n来源：" + "；".join(value["sources"])
    else:
        projects = value["projects"]
        if not projects:
            answer = "台账中没有满足这些筛选条件的项目。"
        else:
            answer = "\n\n".join(
                "\n".join(f"{LABELS.get(key, key)}：{val}" for key, val in row.items())
                for row in projects
            )
        answer += "\n\n数据来源：" + value["source"]
    last = state["messages"][-1]
    # Replace the draft final message in checkpoint history as well as in the visible response.
    message_id = last.id if isinstance(last, AIMessage) and not last.tool_calls else None
    return {"messages": [AIMessage(content=answer, id=message_id)]}
