"""Explicit, public-data-only LangSmith onboarding verification."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


def tracing_settings(env=None):
    env = os.environ if env is None else env
    endpoint = (env.get("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com").rstrip("/")
    url = urlsplit(endpoint)
    hosts = {
        "api.smith.langchain.com",
        "eu.api.smith.langchain.com",
        "apac.api.smith.langchain.com",
        "aws.api.smith.langchain.com",
    }
    if (
        url.scheme != "https"
        or url.hostname not in hosts
        or url.username
        or url.password
        or url.path
        or url.query
        or url.fragment
        or url.port
    ):
        raise ValueError("LANGSMITH_ENDPOINT 必须是官方区域 HTTPS API 地址")
    key = env.get("LANGSMITH_API_KEY")
    if not key:
        raise ValueError("请在本地 .env 填写 LANGSMITH_API_KEY，无需发送到聊天")
    return {
        "api_key": key,
        "api_url": endpoint,
        "workspace_id": env.get("LANGSMITH_WORKSPACE_ID") or None,
        "project_name": env.get("LANGSMITH_PROJECT") or "kb-rag-agent",
    }


@contextmanager
def agent_task_tracing(enabled, run):
    """Scope tracing to an explicitly requested, prevalidated public task batch."""
    if not enabled:
        yield lambda graph, task, thread_id, limit: _evaluate(graph, task, thread_id, limit)
        return
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith import Client, trace, tracing_context

    from kb.eval.runs import write_json

    settings = tracing_settings()
    project = settings.pop("project_name")
    errors, trace_ids = [], []
    client = Client(
        **settings,
        auto_batch_tracing=False,
        omit_traced_runtime_info=True,
        tracing_error_callback=lambda e: errors.append(type(e).__name__),
    )
    tracer = LangChainTracer(client=client, project_name=project)
    tracer.raise_error = True

    def evaluate(graph, task, thread_id, limit):
        with trace(
            "public-agent-task",
            client=client,
            project_name=project,
            inputs={"question": task["question"]},
            metadata={"task_id": task["id"], "local_run": run.path.name},
        ) as root:
            result = _evaluate(graph.with_config(callbacks=[tracer]), task, thread_id, limit)
            root.end(
                outputs={
                    "answer": result["answer"],
                    "calls": result["calls"],
                    "failure": result["failure"],
                }
            )
            result["langsmith_trace_id"] = str(root.id)
            trace_ids.append(str(root.id))
            return result

    try:
        with tracing_context(
            enabled=True,
            client=client,
            project_name=project,
            tags=["public-development", run.path.name],
        ):
            yield evaluate
        client.flush(timeout=30)
        if errors:
            raise RuntimeError("LangSmith 上传错误；请查看运行目录的 tracing.json")
    finally:
        client.close(timeout=30)
        write_json(
            run.path / "tracing.json",
            {
                "project": project,
                "endpoint": settings["api_url"],
                "trace_ids": trace_ids,
                "upload_errors": errors,
                "dashboard_verified": False,
                "latency_note": "包含同步追踪开销，与未开启追踪的延迟不可直接比较。",
            },
        )


def _evaluate(graph, task, thread_id, limit):
    from kb.eval.agent_eval import evaluate_task

    return evaluate_task(graph, task, thread_id, limit)


def trace_demo():
    from langchain_core.messages import HumanMessage
    from langchain_core.tracers.langchain import LangChainTracer
    from langgraph.checkpoint.memory import InMemorySaver
    from langsmith import Client, trace, tracing_context

    from kb.agent.graph import build_graph
    from kb.agent.tools import build_tools
    from kb.agent.tools.project_query import read_ledger
    from kb.chains.rag_chain import RagChain, source_label
    from kb.config import load_config
    from kb.eval.runs import Run, write_json
    from kb.runtime import make_llm, make_retrieval

    load_dotenv()
    settings = tracing_settings()
    project = settings.pop("project_name")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", LANGSMITH_TRACING="false")
    cfg = load_config("agent")
    cfg["ledger_source"] = "官方公开采购公告台账"
    # All checks and model loading happen before the tracing context opens.
    base, chunks = make_retrieval()
    manifest = json.loads(Path("data/demo/manifest.json").read_text())
    names = {d["file_name"] for d in manifest["documents"]}
    projects = {d["project_id"] for d in manifest["documents"]}
    if any(c.metadata["doc_name"] not in names for c in chunks):
        raise ValueError("索引包含公开来源清单以外的文档，停止上传追踪")
    if any(p.project_id not in projects for p in read_ledger(Path(cfg["ledger_path"]))):
        raise ValueError("台账包含公开来源清单以外的项目，停止上传追踪")
    llm = make_llm(cfg)
    run = Run(
        Path("data/demo/cloud-runs"),
        "langsmith",
        {
            "scope": "public-demo-only",
            "project": project,
            "endpoint": settings["api_url"],
            "human_reviewed": False,
            "formal_acceptance": False,
        },
    )
    errors = []
    client = Client(
        **settings,
        auto_batch_tracing=False,
        omit_traced_runtime_info=True,
        tracing_error_callback=lambda e: errors.append(type(e).__name__),
    )
    tracer = LangChainTracer(client=client, project_name=project)
    tracer.raise_error = True
    try:
        with tracing_context(
            enabled=True, client=client, project_name=project, tags=["public-demo", "onboarding"]
        ):
            with trace(
                "public-rag-agent-verification",
                client=client,
                project_name=project,
                inputs={"scope": "public-demo-only"},
            ) as root:
                question = "FastAPI 中如何把查询参数设置为必选？"
                with trace(
                    "public-document-retrieval", run_type="retriever", inputs={"question": question}
                ) as retrieval:
                    hits = base.search(question)
                    sources = [
                        {
                            "id": f"S{i}",
                            "source": source_label(h.chunk),
                            "chunk_id": h.chunk.id,
                            "text": h.chunk.text,
                        }
                        for i, h in enumerate(hits, 1)
                    ]
                    retrieval.end(outputs={"documents": sources})
                answer = RagChain(base, llm).answer_sources(question, sources)
                graph = build_graph(llm, build_tools(base, chunks, llm, cfg), cfg, InMemorySaver())
                state = graph.invoke(
                    {
                        "messages": [
                            HumanMessage(content="查询海口市AI+数字内容生产服务的合同金额。")
                        ]
                    },
                    {
                        "configurable": {"thread_id": run.path.name},
                        "callbacks": [tracer],
                        "recursion_limit": cfg["recursion_limit"],
                    },
                )
                root.end(
                    outputs={
                        "rag_answer": answer["answer"],
                        "agent_answer": state["messages"][-1].content,
                    }
                )
                trace_id = str(root.id)
        client.flush(timeout=30)
        if errors:
            raise RuntimeError("追踪上传报告错误，请检查本地报告中的错误类别")
        write_json(
            run.path / "summary.json",
            {
                "trace_id": trace_id,
                "project": project,
                "endpoint": settings["api_url"],
                "status": "submitted",
                "dashboard_verified": False,
                "note": "Tracing key 无读取权限；请在网页核对完整调用树。",
            },
        )
        run.status("submitted_awaiting_dashboard_check")
    except BaseException as exc:
        run.status("failed", error_type=type(exc).__name__, trace_errors=errors)
        raise
    finally:
        client.close(timeout=30)
    return run.path
