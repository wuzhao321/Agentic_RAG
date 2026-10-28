"""Loopback-only demo UI backed by the real retrieval chain and LangGraph."""

import base64
import hashlib
import json
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError

from kb.agent.graph import build_graph
from kb.agent.tools import build_tools
from kb.agent.tools.project_query import read_ledger
from kb.chains.conversation import build_rag_graph
from kb.chains.rag_chain import RagChain, source_label
from kb.config import load_config
from kb.runtime import make_llm, make_retrieval

LOG = logging.getLogger(__name__)


class DemoService:
    def __init__(self, saver, cloud=False):
        self.demo = load_config("demo")
        self.config = load_config("agent")
        self.config["grounded_tool_answers"] = True
        self.config["ledger_source"] = "官方采购公告整理的演示台账（非企业内部数据）"
        self.cloud = cloud
        self.llm = make_llm(self.config) if cloud else ChatOpenAI(**self.demo["llm"])
        retrieval_config = {**load_config("retrieval"), **self.demo.get("retrieval", {})}
        self.retrieval, self.chunks = make_retrieval(retrieval_config=retrieval_config)
        self.rag = RagChain(self.retrieval, self.llm)
        self.rag_graph = build_rag_graph(self.rag, self.config, saver)
        self.graph = build_graph(
            self.llm,
            build_tools(self.retrieval, self.chunks, self.llm, self.config),
            self.config,
            saver,
        )
        self.manifest = json.loads(Path("data/demo/manifest.json").read_text())
        self.documents = {d["file_name"]: d for d in self.manifest["documents"]}
        if cloud:
            if any(c.metadata["doc_name"] not in self.documents for c in self.chunks):
                raise ValueError("云端演示只允许公开来源清单内的索引文档")
            public_ids = {d["project_id"] for d in self.documents.values()}
            if any(
                p.project_id not in public_ids
                for p in read_ledger(Path(self.config["ledger_path"]))
            ):
                raise ValueError("云端演示台账包含公开来源清单以外的项目")
        self.lock = threading.Lock()
        self.failed_sessions = set()

    def status(self):
        review = Path("data/demo/review.json")
        latest = Path("data/demo/latest-run.txt")
        run_summary = None
        if latest.exists():
            run = Path(latest.read_text().strip()).resolve()
            if (
                run.is_relative_to(Path("data/demo/runs").resolve())
                and (run / "summary.json").exists()
            ):
                run_summary = json.loads((run / "summary.json").read_text())
        development_runs = []
        active = json.loads(Path("data/index/active.json").read_text())
        for kind in ["ragas", "conversation", "agent_baseline", "agent_retest"]:
            candidates = sorted(Path("data/demo/cloud-runs").glob(f"*_{kind}_*/summary.json"))
            if candidates:
                directory = candidates[-1].parent
                snapshot = json.loads((directory / "snapshot.json").read_text())
                development_runs.append(
                    {
                        "kind": kind,
                        "run": directory.name,
                        "summary": json.loads(candidates[-1].read_text()),
                        "status": json.loads((directory / "status.json").read_text())["status"],
                        "current_index": snapshot.get("index", {}).get("fingerprint")
                        == active["fingerprint"],
                    }
                )
        return {
            "model": self.llm.model_name if self.cloud else "Qwen3-4B-Instruct-2507 · MLX 4-bit",
            "cloud": self.cloud,
            "retrieval": "BGE-large-zh + BM25 + RRF + BGE reranker",
            "documents": list(self.documents.values()),
            "chunks": len(self.chunks),
            "projects": [
                p.model_dump(mode="json") for p in read_ledger(Path(self.config["ledger_path"]))
            ],
            "evaluation": json.loads(review.read_text()) if review.exists() else None,
            "latest_run": run_summary,
            "development_runs": development_runs,
            "busy": self.lock.locked(),
            "examples": [
                {"mode": "rag", "question": "FastAPI 中如何把查询参数设置为必选？"},
                {
                    "mode": "rag",
                    "question": "FastAPI 的 response_model 和返回类型同时声明时，谁优先？",
                },
                {
                    "mode": "agent",
                    "question": "查询 2026 年签订、金额超过 200 万元的项目及供应商。",
                },
                {
                    "mode": "agent",
                    "question": "总结 public_demo/浙江税务GPU算力采购公告.pdf 的主要信息。",
                },
                {"mode": "rag", "question": "公司报销出租车费用的上限是多少？"},
            ],
        }

    def add_provenance(self, sources):
        for source in sources:
            name = source.get("source", "").split(" > ", 1)[0]
            source["source_url"] = self.documents.get(name, {}).get("source_url")
        return sources

    def query(self, mode, question, session):
        start = time.perf_counter()
        if mode == "rag":
            key = "rag-" + session
            if key in self.failed_sessions:
                raise ValueError("上次调用未完成，请点击“新建会话”后重试。")
            try:
                state = self.rag_graph.invoke(
                    {"messages": [HumanMessage(content=question)]},
                    {"configurable": {"thread_id": key}},
                )
            except Exception:
                self.failed_sessions.add(key)
                raise
            result = dict(state["result"])
            self.add_provenance(result["sources"])
            result["trace"] = []
        elif mode == "search":
            hits = self.retrieval.search(question)
            result = {
                "answer": f"找到 {len(hits)} 条证据，按重排得分排序。",
                "sources": [
                    {
                        "id": f"S{i}",
                        "source": source_label(h.chunk),
                        "chunk_id": h.chunk.id,
                        "text": h.chunk.text,
                        "score": h.score,
                    }
                    for i, h in enumerate(hits, 1)
                ],
                "trace": [],
            }
            self.add_provenance(result["sources"])
        else:
            if session in self.failed_sessions:
                raise ValueError("上次调用未完成，请点击“新建会话”后重试。")
            run_config = {
                "configurable": {"thread_id": "web-" + session},
                "recursion_limit": self.config["recursion_limit"],
            }
            before = self.graph.get_state(run_config)
            previous_ids = {m.id for m in before.values.get("messages", [])}
            try:
                state = self.graph.invoke(
                    {"messages": [HumanMessage(content=question)]}, run_config
                )
            except Exception:
                self.failed_sessions.add(session)
                raise
            messages = [m for m in state["messages"] if m.id not in previous_ids]
            trace, sources = [], []
            for message in messages:
                if isinstance(message, AIMessage):
                    trace.extend(
                        {"type": "call", "name": c["name"], "arguments": c["args"]}
                        for c in message.tool_calls
                    )
                elif isinstance(message, ToolMessage):
                    try:
                        content = json.loads(message.content)
                    except (TypeError, json.JSONDecodeError):
                        content = message.content
                    trace.append({"type": "result", "name": message.name, "content": content})
                    if message.name == "KnowledgeSearchTool" and isinstance(content, list):
                        sources.extend(
                            {"id": f"S{len(sources) + i}", **s} for i, s in enumerate(content, 1)
                        )
                    elif message.name == "DocumentSummaryTool" and isinstance(content, dict):
                        sources.extend(
                            {"id": f"S{len(sources) + i}", "source": s, "text": "全文摘要来源"}
                            for i, s in enumerate(content.get("sources", []), 1)
                        )
            self.add_provenance(sources)
            for i, source in enumerate(sources, 1):
                source["id"] = f"S{i}"
            result = {"answer": state["messages"][-1].content, "trace": trace, "sources": sources}
        result.update(mode=mode, latency_seconds=round(time.perf_counter() - start, 2))
        return result


def handler_class(service):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, body):
            self.send_body(
                status,
                json.dumps(body, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

        def send_body(self, status, body, content_type, policy=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                policy
                or "default-src 'self'; style-src 'self'; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def valid_origin(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected:
                return False
            origin = self.headers.get("Origin")
            return not origin or origin == f"http://{expected}"

        def do_GET(self):
            if not self.valid_origin():
                return self.send_json(403, {"error": "仅允许本机同源访问"})
            route = urlsplit(self.path).path
            if route == "/api/status":
                return self.send_json(200, service.status())
            if route == "/review":
                path = Path("data/review/public-v1/review.html")
                if not path.exists():
                    return self.send_json(404, {"error": "请先生成审核页面"})
                html = path.read_text(encoding="utf-8")

                def hashes(tag):
                    return " ".join(
                        "'sha256-"
                        + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()
                        + "'"
                        for s in re.findall(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", html, re.S)
                    )

                policy = (
                    f"default-src 'none'; script-src {hashes('script')}; "
                    f"style-src {hashes('style')}; frame-ancestors 'none'"
                )
                return self.send_body(200, html.encode(), "text/html; charset=utf-8", policy)
            assets = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
            }
            if route not in assets:
                return self.send_json(404, {"error": "未找到"})
            name, content_type = assets[route]
            self.send_body(
                200, (Path(__file__).parent / "web_assets" / name).read_bytes(), content_type
            )

        def do_POST(self):
            if not self.valid_origin():
                return self.send_json(403, {"error": "仅允许本机同源访问"})
            if self.path != "/api/query":
                return self.send_json(404, {"error": "未找到"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16000:
                    raise ValueError("请求大小须介于 1 与 16000 字节")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("需要 application/json 请求")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("请求必须是 JSON 对象")
                mode, question, session = (data.get(k) for k in ["mode", "question", "session"])
                if mode not in {"rag", "agent", "search"}:
                    raise ValueError("无效的问答模式")
                if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
                    raise ValueError("问题长度须介于 1 与 2000 字符")
                if not isinstance(session, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", session):
                    raise ValueError("无效的会话编号")
            except (ValueError, TypeError) as exc:
                return self.send_json(400, {"error": str(exc)})
            if not service.lock.acquire(blocking=False):
                return self.send_json(429, {"error": "模型正在处理另一个请求，请稍后重试。"})
            try:
                result = service.query(mode, question.strip(), session)
            except GraphRecursionError:
                self.send_json(422, {"error": "已达到工具调用步数上限，请新建会话。"})
            except ValueError as exc:
                self.send_json(422, {"error": str(exc)})
            except Exception:
                LOG.exception("Demo request failed")
                self.send_json(503, {"error": "调用失败，请检查模型服务及 data/demo 日志后重试。"})
            else:
                self.send_json(200, result)
            finally:
                service.lock.release()

    return Handler


def serve(port=8765, cloud=False):
    checkpoint = Path("data/sessions/demo-cloud.sqlite" if cloud else "data/sessions/demo.sqlite")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(checkpoint)) as saver:
        service = DemoService(saver, cloud=cloud)
        server = ThreadingHTTPServer(("127.0.0.1", port), handler_class(service))
        print(f"Demo ready: http://127.0.0.1:{port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
