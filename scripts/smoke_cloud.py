"""Verify configured cloud text, JSON and native tool calls without business data."""

import json
import os
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage

from kb.config import load_config
from kb.eval.runs import Run, write_json
from kb.runtime import llm_identity, make_llm


def main():
    os.environ["LANGSMITH_TRACING"] = "false"
    cfg = load_config("agent")
    llm = make_llm(cfg)
    identity = llm_identity(cfg)
    run = Run(Path("data/demo/cloud-runs"), "smoke", {"llm_identity": identity})
    try:
        records = []
        text = llm.invoke("只回复：连接成功")
        records.append(
            {
                "test": "text",
                "content": text.content,
                "metadata": text.response_metadata,
                "usage": text.usage_metadata,
            }
        )
        structured = llm.bind(response_format={"type": "json_object"}).invoke(
            "返回 JSON 对象，字段 status 的值为 ok，字段 count 的值为整数 2。只返回 JSON。"
        )
        value = json.loads(structured.content)
        if value != {"status": "ok", "count": 2}:
            raise ValueError("JSON response did not match the requested schema")
        records.append(
            {
                "test": "json",
                "content": value,
                "metadata": structured.response_metadata,
                "usage": structured.usage_metadata,
            }
        )
        schema = {
            "type": "function",
            "function": {
                "name": "lookup_project",
                "description": "查询项目",
                "parameters": {
                    "type": "object",
                    "properties": {"project_id": {"type": "string"}},
                    "required": ["project_id"],
                    "additionalProperties": False,
                },
            },
        }
        human = HumanMessage(content="请用 lookup_project 查询项目 DEMO-001。")
        call = llm.bind_tools(
            [schema], tool_choice={"type": "function", "function": {"name": "lookup_project"}}
        ).invoke([human])
        if len(call.tool_calls) != 1 or call.tool_calls[0]["args"] != {"project_id": "DEMO-001"}:
            raise ValueError("Tool call arguments did not match")
        records.append(
            {
                "test": "tool_call",
                "calls": call.tool_calls,
                "metadata": call.response_metadata,
                "usage": call.usage_metadata,
            }
        )
        final = llm.bind_tools([schema]).invoke(
            [
                human,
                call,
                ToolMessage(
                    content='{"project_id":"DEMO-001","status":"测试成功"}',
                    tool_call_id=call.tool_calls[0]["id"],
                ),
            ]
        )
        if final.tool_calls or "测试成功" not in final.content:
            raise ValueError("Tool response follow-up did not finish correctly")
        records.append(
            {
                "test": "tool_followup",
                "content": final.content,
                "metadata": final.response_metadata,
                "usage": final.usage_metadata,
            }
        )
        write_json(run.path / "results.json", records)
        run.status("complete")
        print(
            json.dumps(
                {
                    "status": "complete",
                    "provider": identity["provider"],
                    "model": identity["parameters"]["model"],
                    "tests": len(records),
                    "run": str(run.path),
                },
                ensure_ascii=False,
            )
        )
    except Exception as exc:
        run.status(
            "failed", error_type=type(exc).__name__, http_status=getattr(exc, "status_code", None)
        )
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "http_status": getattr(exc, "status_code", None),
                    "run": str(run.path),
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
