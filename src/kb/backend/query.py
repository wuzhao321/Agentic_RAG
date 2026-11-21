"""Hosted access to the existing RAG/Agent implementation and knowledge data."""

import json
import os
import threading
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from kb.agent.graph import build_graph
from kb.agent.tools import build_tools
from kb.agent.tools.project_query import read_ledger
from kb.backend.service import ServiceError, list_documents
from kb.chains.conversation import build_rag_graph
from kb.chains.rag_chain import RagChain
from kb.config import load_config
from kb.runtime import make_llm, make_retrieval
from kb.web import DemoService


def evaluation_history(root, manifest):
    """Read saved results without starting or paying for another evaluation."""
    latest = root / "data/demo/latest-run.txt"
    local_run = None
    if latest.exists():
        path = Path(latest.read_text(encoding="utf-8").strip())
        if not path.is_absolute():
            path = root / path
        if path.resolve().is_relative_to((root / "data/demo/runs").resolve()):
            summary = path / "summary.json"
            if summary.exists():
                local_run = json.loads(summary.read_text(encoding="utf-8"))
    runs = []
    for kind in ["ragas", "conversation", "agent_baseline", "agent_retest"]:
        candidates = sorted((root / "data/demo/cloud-runs").glob(f"*_{kind}_*/summary.json"))
        if not candidates:
            continue
        summary = candidates[-1]
        snapshot = json.loads(summary.with_name("snapshot.json").read_text(encoding="utf-8"))
        status = json.loads(summary.with_name("status.json").read_text(encoding="utf-8"))
        runs.append(
            {
                "kind": kind,
                "run": summary.parent.name,
                "summary": json.loads(summary.read_text(encoding="utf-8")),
                "status": status["status"],
                "current_index": bool(manifest)
                and snapshot.get("index", {}).get("fingerprint") == manifest["fingerprint"],
            }
        )
    return local_run, runs


class HostedQueryService(DemoService):
    """Reuse demo query/trace behavior while removing demo-only dataset assumptions."""

    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.failed_sessions = set()
        self.generation = None
        self.llm = None
        self.saver_context = None
        self.saver = None
        self.documents = {}
        self.cloud = True

    def close(self):
        if self.saver_context is not None:
            self.saver_context.__exit__(None, None, None)
            self.saver_context = None

    def refresh(self):
        active = self.settings.index_dir / "active.json"
        if not active.exists():
            raise ServiceError(409, "index_missing", "知识库尚未入库，请先提交入库任务")
        manifest = json.loads(active.read_text(encoding="utf-8"))
        generation = manifest["collection"]
        if generation == self.generation:
            return
        config = load_config("agent", self.settings.root)
        config["grounded_tool_answers"] = True
        config["ledger_path"] = str((self.settings.root / config["ledger_path"]).resolve())
        config["ledger_source"] = "项目台账"
        retrieval_config = {
            **load_config("retrieval", self.settings.root),
            **load_config("demo", self.settings.root).get("retrieval", {}),
        }
        retrieval, chunks = make_retrieval(self.settings.root, retrieval_config)
        self.config, self.retrieval, self.chunks = config, retrieval, chunks
        self.documents = {
            c.metadata["doc_name"]: {"file_name": c.metadata["doc_name"]} for c in chunks
        }
        sources = self.settings.root / "data/demo/manifest.json"
        if sources.exists():
            for document in json.loads(sources.read_text(encoding="utf-8")).get("documents", []):
                if document["file_name"] in self.documents:
                    self.documents[document["file_name"]] = document
        self.generation = generation
        self.rag_graph = self.graph = None

    def ensure_generation_model(self):
        if self.rag_graph is not None:
            return
        if self.llm is None:
            try:
                self.llm = make_llm(self.config)
            except ValueError as exc:
                raise ServiceError(503, "model_unconfigured", str(exc)) from exc
        if self.saver is None:
            checkpoint = self.settings.root / "data/sessions/hosted.sqlite"
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            self.saver_context = SqliteSaver.from_conn_string(str(checkpoint))
            self.saver = self.saver_context.__enter__()
        self.rag = RagChain(self.retrieval, self.llm)
        self.rag_graph = build_rag_graph(self.rag, self.config, self.saver)
        tools = build_tools(self.retrieval, self.chunks, self.llm, self.config)
        if not Path(self.config["ledger_path"]).exists():
            tools = [tool for tool in tools if tool.name != "ProjectQueryTool"]
        self.graph = build_graph(self.llm, tools, self.config, self.saver)

    def query(self, mode, question, session):
        if not self.lock.acquire(blocking=False):
            raise ServiceError(429, "query_busy", "知识助手正在处理另一个请求，请稍后重试")
        try:
            self.refresh()
            if mode != "search":
                self.ensure_generation_model()
            return super().query(mode, question, session)
        finally:
            self.lock.release()

    def workspace_status(self, sessions):
        active = self.settings.index_dir / "active.json"
        manifest = json.loads(active.read_text(encoding="utf-8")) if active.exists() else None
        chunks = 0
        if manifest:
            with (self.settings.index_dir / manifest["chunks_file"]).open(
                encoding="utf-8"
            ) as handle:
                chunks = sum(bool(line.strip()) for line in handle)
        config = load_config("agent", self.settings.root)
        provider = os.environ.get("LLM_PROVIDER", config["provider"])
        profile = config["providers"].get(provider, {})
        model = os.environ.get(profile.get("model_env", ""), "")
        configured = bool(model and os.environ.get(profile.get("key_env", "")))
        ledger = self.settings.root / config["ledger_path"]
        review = self.settings.root / "data/demo/review.json"
        local_run, runs = evaluation_history(self.settings.root, manifest)
        return {
            "connected": True,
            "index_ready": bool(manifest),
            "document_count": list_documents(sessions, 0, 1)["total"],
            "chunks": chunks,
            "collection": manifest["collection"] if manifest else None,
            "model": model or "未配置生成模型",
            "model_configured": configured,
            "busy": self.lock.locked(),
            "retrieval": "BGE-large-zh + BM25 + RRF + BGE reranker",
            "projects": [p.model_dump(mode="json") for p in read_ledger(ledger)]
            if ledger.exists()
            else [],
            "evaluation": json.loads(review.read_text(encoding="utf-8"))
            if review.exists()
            else None,
            "latest_run": local_run,
            "development_runs": runs,
            "review_available": (self.settings.root / "data/review/public-v1/review.html").exists(),
        }
