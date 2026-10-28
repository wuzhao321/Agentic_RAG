"""Real local experiments, kept separate from the formal tune/holdout protocol."""

import json
import os
import statistics
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from langchain_core.messages import HumanMessage, ToolMessage, messages_to_dict
from langchain_core.messages.utils import count_tokens_approximately
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from kb.agent.graph import build_graph
from kb.agent.tools import build_tools
from kb.chains.conversation import build_rag_graph
from kb.chains.rag_chain import RagChain
from kb.config import load_config
from kb.eval.ablation import configurations
from kb.eval.dataset import read_cases
from kb.eval.protocol import protect_tune_path
from kb.eval.ragas_runner import RagasScorer
from kb.eval.runner import evaluate_cases, write_comparison
from kb.eval.runs import Run, append_jsonl, file_hash, source_hash, write_json
from kb.retrieval.pipeline import RetrievalPipeline
from kb.retrieval.vector import VectorIndex
from kb.runtime import llm_identity, make_llm, make_retrieval


def local_snapshot():
    cfg = {
        n: load_config(n) for n in ["ingest", "retrieval", "agent", "eval", "demo", "validation"]
    }
    path = Path(cfg["validation"]["qa_path"])
    protect_tune_path(path, Path(cfg["eval"]["holdout_path"]))
    # This command is deliberately limited to development data, including symlink resolution.
    if not path.resolve().is_relative_to(Path("data/demo").resolve()):
        raise ValueError("本地开发检查只读取 data/demo 内的数据")
    base_url = cfg["demo"]["llm"]["base_url"]
    if urlsplit(base_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("本地检查只允许回环模型地址")
    clean = deepcopy(cfg)
    clean["demo"]["llm"].pop("api_key", None)
    return (
        cfg,
        path,
        {
            "configs": clean,
            "scope": "public-development-only",
            "human_reviewed": False,
            "formal_acceptance": False,
            "dataset_sha256": file_hash(path),
            "index": json.loads(Path(cfg["ingest"]["index_dir"], "active.json").read_text()),
            "source_sha256": source_hash(Path(".")),
            "uv_lock_sha256": file_hash(Path("uv.lock")),
        },
    )


def pipeline_for(base, cfg):
    return RetrievalPipeline(
        VectorIndex(base.vector.collection, base.vector.embedding, cfg),
        base.bm25,
        base.reranker,
        cfg,
        base.projects,
    )


def retrieval_results(cases, pipeline):
    rows = []
    for case in cases:
        start = time.perf_counter()
        hits = pipeline.search(case.question)
        expected = {i for c in case.reference_contexts for i in c.get("chunk_ids", [])}
        ranked = [h.chunk.id for h in hits]
        ranks = [i + 1 for i, key in enumerate(ranked) if key in expected]
        rows.append(
            {
                "id": case.id,
                "question": case.question,
                "expected_chunk_ids": sorted(expected),
                "hits": [h.model_dump() for h in hits],
                "evidence_hit": bool(ranks) if expected else None,
                "reciprocal_rank": 1 / min(ranks) if ranks else 0,
                "empty": not hits,
                "seconds": time.perf_counter() - start,
            }
        )
    answerable = [r for r in rows if r["expected_chunk_ids"]]
    absent = [r for r in rows if not r["expected_chunk_ids"]]
    return rows, {
        "n": len(rows),
        "answerable_n": len(answerable),
        "evidence_hits": sum(r["evidence_hit"] for r in answerable),
        "hit_rate": statistics.mean(r["evidence_hit"] for r in answerable) if answerable else None,
        "mrr": statistics.mean(r["reciprocal_rank"] for r in answerable) if answerable else None,
        "no_answer_n": len(absent),
        "no_answer_empty": sum(r["empty"] for r in absent),
        "median_seconds": statistics.median(r["seconds"] for r in rows),
    }


def check_retrieval(run, cfg, cases, base):
    variants = configurations(cfg["retrieval"]) + configurations(
        cfg["retrieval"], grid=cfg["eval"]["grid"]
    )
    variants += [("demo_D", {**cfg["retrieval"], **cfg["demo"].get("retrieval", {})})]
    summaries = {}
    for name, params in variants:
        directory = run.path / name
        directory.mkdir()
        write_json(directory / "config.json", params)
        rows, summary = retrieval_results(cases, pipeline_for(base, params))
        for row in rows:
            append_jsonl(directory / "results.jsonl", row)
        summaries[name] = summary
        write_json(directory / "summary.json", summary)
        print(name, summary, flush=True)
    write_json(run.path / "summary.json", summaries)
    lines = [
        "# 本地检索实验",
        "",
        "开发集重复使用；不代表 RAGAS 或正式留出集验收。",
        "",
        "| 配置 | 参考证据命中 | MRR | 无答案零召回 | 检索中位秒 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, s in summaries.items():
        lines.append(
            f"| {name} | {s['evidence_hits']}/{s['answerable_n']} | {s['mrr']:.3f} | "
            f"{s['no_answer_empty']}/{s['no_answer_n']} | {s['median_seconds']:.3f} |"
        )
    (run.path / "report.md").write_text("\n".join(lines) + "\n")


def check_ragas(run, cfg, cases, base, llm):
    evaluation = {
        **cfg["eval"],
        "no_answer_type": "no_answer",
        "ragas": {**cfg["eval"]["ragas"], **cfg["validation"]["ragas"]},
    }
    with RagasScorer(llm, base.vector.embedding, evaluation) as scorer:
        results = {}
        for name, params in configurations(cfg["retrieval"]):
            write_json(run.path / f"{name}.config.json", params)
            results[name] = evaluate_cases(
                cases,
                RagChain(pipeline_for(base, params), llm),
                scorer,
                evaluation,
                run.path / name,
            )
            write_comparison(run, results)
            write_json(run.path / "summary.json", results)
            print(name, results[name], flush=True)
        return any(s["error_n"] for s in results.values())


def check_conversation(run, cfg, base, chunks, llm):
    agent_cfg = {
        **cfg["agent"],
        "grounded_tool_answers": True,
        "ledger_source": "公开采购台账，非企业内部数据",
    }
    # Use the actual 3000/4 policy; do not lower the threshold to manufacture a pass.
    questions = list(cfg["validation"]["conversation_questions"])
    allowed_sources = {
        Path(d["source_path"]).resolve()
        for d in json.loads(Path("data/demo/manifest.json").read_text())["documents"]
        if d.get("source_path")
    }
    for turn, source in cfg["validation"].get("conversation_contexts", {}).items():
        path = Path(source)
        if path.resolve() not in allowed_sources:
            raise ValueError("长对话附加资料必须来自公开来源清单")
        questions[int(turn) - 1] += (
            "\n以下是我阅读的公开文档笔记，作为本轮背景资料：\n" + path.read_text(encoding="utf-8")
        )
    write_json(run.path / "conversation_inputs.json", questions)
    results = {}
    for mode in ["rag", "agent"]:
        db = run.path / f"{mode}.sqlite"
        invocation = {
            "configurable": {"thread_id": mode},
            "recursion_limit": agent_cfg["recursion_limit"],
        }
        summaries = 0
        with SqliteSaver.from_conn_string(str(db)) as saver:
            graph = (
                build_rag_graph(RagChain(base, llm), agent_cfg, saver)
                if mode == "rag"
                else build_graph(llm, build_tools(base, chunks, llm, agent_cfg), agent_cfg, saver)
            )
            for i, question in enumerate(questions, 1):
                start = time.perf_counter()
                before = graph.get_state(invocation).values
                state = graph.invoke({"messages": [HumanMessage(content=question)]}, invocation)
                changed = bool(state.get("summary")) and state.get("summary") != before.get(
                    "summary"
                )
                summaries += int(changed)
                row = {
                    "turn": i,
                    "question": question,
                    "answer": state["messages"][-1].content,
                    "summary_changed": changed,
                    "summary": state.get("summary"),
                    "history_tokens": count_tokens_approximately(state["messages"]),
                    "messages": messages_to_dict(state["messages"]),
                    "rag_result": state.get("result"),
                    "seconds": time.perf_counter() - start,
                }
                append_jsonl(run.path / f"{mode}.jsonl", row)
                print(mode, "turn", i, "summary", changed, flush=True)
            state = graph.get_state(invocation).values
            prior_answer = state["messages"][-1].content
        # Close/reopen the actual SQLite connection and verify state survives.
        with SqliteSaver.from_conn_string(str(db)) as saver:
            restored = (
                build_rag_graph(RagChain(base, llm), agent_cfg, saver)
                if mode == "rag"
                else build_graph(llm, build_tools(base, chunks, llm, agent_cfg), agent_cfg, saver)
            )
            restored_state = restored.get_state(invocation).values
            isolated = restored.get_state({"configurable": {"thread_id": "unrelated"}}).values
            calls = {
                c["id"] for m in restored_state["messages"] for c in getattr(m, "tool_calls", [])
            }
            no_orphans = all(
                m.tool_call_id in calls
                for m in restored_state["messages"]
                if isinstance(m, ToolMessage)
            )
            results[mode] = {
                "turns": len(questions),
                "summary_updates": summaries,
                "checkpoint_restored": restored_state["messages"][-1].content == prior_answer,
                "session_isolated": not isolated,
                "tool_pairs_intact": no_orphans,
                "final_answer": prior_answer,
                "final_points_present": all(
                    any(point in str(prior_answer).replace(",", "") for point in options)
                    for options in cfg["validation"]["conversation_final_points"]
                ),
                "summary": state.get("summary"),
                "human_reviewed": False,
            }
        write_json(run.path / "summary.json", results)
    return results


def conversation_passed(results):
    return bool(results) and all(
        r["turns"] > 10
        and r["summary_updates"] > 0
        and r["checkpoint_restored"]
        and r["session_isolated"]
        and r["tool_pairs_intact"]
        and r["final_points_present"]
        for r in results.values()
    )


def validate_local(suite):
    if suite not in {"retrieval", "ragas", "conversation"}:
        raise ValueError("suite 必须为 retrieval / ragas / conversation")
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        LANGSMITH_TRACING="false",
        RAGAS_DO_NOT_TRACK="true",
    )
    cfg, path, snapshot = local_snapshot()
    cases = read_cases(path)
    run = Run(Path(cfg["validation"]["runs_dir"]), suite, snapshot)
    try:
        base, chunks = make_retrieval(retrieval_config={**cfg["retrieval"], "mode": "D"})
        if suite == "retrieval":
            check_retrieval(run, cfg, cases, base)
            errors = False
        else:
            llm = ChatOpenAI(**{**cfg["demo"]["llm"], **cfg["validation"]["llm"]})
            if suite == "ragas":
                errors = check_ragas(run, cfg, cases, base, llm)
            else:
                # Conversation matches the demo retrieval policy; A-D scoring does not.
                base.config.update(cfg["demo"].get("retrieval", {}))
                results = check_conversation(run, cfg, base, chunks, llm)
                errors = not conversation_passed(results)
        run.status("completed_with_errors" if errors else "complete")
    except BaseException as exc:
        run.status("failed", error_type=type(exc).__name__)
        raise
    return run.path


def validate_cloud(suite, *, trace_tasks=False):
    """Run authorized public development data with the configured cloud provider."""
    if suite not in {"ragas", "conversation", "agent", "agent_baseline", "agent_retest"}:
        raise ValueError(
            "suite 必须为 ragas / conversation / agent / agent_baseline / agent_retest"
        )
    if trace_tasks and suite not in {"agent", "agent_baseline", "agent_retest"}:
        raise ValueError("--trace 仅用于公开 Agent 任务批量追踪")
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        LANGSMITH_TRACING="false",
        RAGAS_DO_NOT_TRACK="true",
    )
    cfg, path, snapshot = local_snapshot()
    llm = make_llm(cfg["agent"])
    cfg["agent"]["ledger_source"] = "官方公开采购公告台账（非企业内部数据）"
    snapshot["configs"]["agent"]["ledger_source"] = cfg["agent"]["ledger_source"]
    snapshot["llm_identity"] = llm_identity(cfg["agent"])
    snapshot["cloud_provider"] = snapshot["llm_identity"]["provider"]
    snapshot["langsmith_tracing"] = trace_tasks
    task_paths = {
        "agent": Path("data/demo/agent_tasks.jsonl"),
        "agent_baseline": Path("data/review/public-v1/agent_baseline_40.jsonl"),
        "agent_retest": Path("data/review/public-v1/agent_retest_20.jsonl"),
    }
    if suite in task_paths:
        snapshot["task_dataset"] = {
            "path": str(task_paths[suite]),
            "sha256": file_hash(task_paths[suite]),
        }
    run = Run(Path("data/demo/cloud-runs"), suite, snapshot)
    try:
        base, chunks = make_retrieval(retrieval_config={**cfg["retrieval"], "mode": "D"})
        manifest = json.loads(Path("data/demo/manifest.json").read_text())
        public_names = {d["file_name"] for d in manifest["documents"]}
        if any(c.metadata["doc_name"] not in public_names for c in chunks):
            raise ValueError("索引含公开来源清单以外的文档，停止发送云端")
        from kb.agent.tools.project_query import read_ledger

        public_ids = {d["project_id"] for d in manifest["documents"]}
        if any(
            p.project_id not in public_ids for p in read_ledger(Path(cfg["agent"]["ledger_path"]))
        ):
            raise ValueError("台账含公开来源清单以外的项目，停止发送云端")
        if suite == "ragas":
            errors = check_ragas(run, cfg, read_cases(path), base, llm)
        elif suite == "conversation":
            results = check_conversation(run, cfg, base, chunks, llm)
            errors = not conversation_passed(results)
        else:
            from langgraph.checkpoint.memory import InMemorySaver

            from kb.eval.agent_eval import AgentTask, summarize_results
            from kb.observability import agent_task_tracing

            source = task_paths[suite]
            rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
            tasks = [
                AgentTask.model_validate({k: row[k] for k in AgentTask.model_fields if k in row})
                for row in rows
            ]
            graph = build_graph(
                llm, build_tools(base, chunks, llm, cfg["agent"]), cfg["agent"], InMemorySaver()
            )
            results = []
            with agent_task_tracing(trace_tasks, run) as evaluate:
                for task in tasks:
                    result = evaluate(
                        graph, task.model_dump(), task.id, cfg["agent"]["recursion_limit"]
                    )
                    append_jsonl(run.path / "results.jsonl", result)
                    results.append(result)
                    print(task.id, result["failure"], flush=True)
            write_json(run.path / "summary.json", summarize_results(results))
            for result in results:
                append_jsonl(
                    run.path / "reviews.template.jsonl",
                    {
                        "id": result["id"],
                        "completed_human": None,
                        "failure_human": None,
                        "notes": "",
                    },
                )
            errors = any(r["failure"] for r in results)
        run.status("completed_with_errors" if errors else "complete")
    except BaseException as exc:
        run.status("failed", error_type=type(exc).__name__)
        raise
    return run.path
