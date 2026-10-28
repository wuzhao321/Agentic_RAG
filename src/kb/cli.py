import json
from pathlib import Path

import typer
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError

from kb.config import load_config

app = typer.Typer(
    no_args_is_help=True, help="企业知识库 RAG 与 LangGraph Agent（从项目根目录运行）"
)


def output(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def evaluation_command(action):
    try:
        return action()
    except (ValueError, FileNotFoundError, FileExistsError, ImportError) as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def doctor():
    """检查缺少的文档、配置与模型；不联网，不读取留出集内容。"""
    from kb.doctor import inspect_setup

    output(inspect_setup())


@app.command("eval-rag")
def eval_rag(final: bool = False, grid: bool = False):
    """运行 A–D 或 --grid 调参；--final 一次性运行留出集 A/D。会调用云端模型。"""
    from kb.eval.service import evaluate_rag

    path = evaluation_command(lambda: evaluate_rag(final=final, grid=grid))
    output({"run_directory": str(path)})


@app.command("validate-local")
def validate_local(suite: str = "retrieval"):
    """公开开发集实测：retrieval / ragas / conversation；仅调用本机模型，不触碰正式留出集。"""
    from kb.eval.local_validation import validate_local as run_validation

    output({"run_directory": str(evaluation_command(lambda: run_validation(suite)))})


@app.command("freeze-eval")
def freeze_eval():
    """冻结配置、代码、模型和索引版本；不读取留出集，不调用模型。"""
    from kb.eval.service import freeze_evaluation

    output({"frozen_snapshot": str(evaluation_command(freeze_evaluation))})


@app.command("validate-cloud")
def validate_cloud(suite: str = "ragas", trace: bool = False):
    """公开开发集云端实测：ragas / conversation / agent；将公开片段发送给配置的模型。"""
    from kb.eval.local_validation import validate_cloud as run_validation

    output(
        {"run_directory": str(evaluation_command(lambda: run_validation(suite, trace_tasks=trace)))}
    )


@app.command("eval-agent")
def eval_agent(retest: bool = False):
    """批量评测 Agent；--retest 用新的复测任务。会调用云端模型。"""
    from kb.eval.service import evaluate_agent

    output({"run_directory": str(evaluation_command(lambda: evaluate_agent(retest=retest)))})


@app.command("trace-demo")
def trace_demo():
    """显式上传一组公开 RAG/Agent 追踪到已配置的 LangSmith 项目。"""
    from kb.observability import trace_demo as run_trace

    output({"run_directory": str(evaluation_command(run_trace))})


@app.command("review-agent")
def review_agent(run_directory: Path, reviews: Path):
    """导入人工完成率与失败归因；保留原始轨迹与每次审核版本。"""
    from kb.eval.agent_eval import apply_reviews

    config = load_config("eval")
    allowed = [Path(config["runs_dir"]), Path("data/demo/cloud-runs")]
    if not any(run_directory.resolve().is_relative_to(root.resolve()) for root in allowed):
        raise typer.BadParameter("运行目录必须位于正式 runs_dir 或公开开发 cloud-runs 内")
    from kb.eval.protocol import same_file

    if same_file(reviews, Path(config["holdout_path"])):
        raise typer.BadParameter("人工审核文件不能指向 QA 留出集")
    output(evaluation_command(lambda: apply_reviews(run_directory, reviews)))


@app.command("review-judge")
def review_judge(samples: Path, reviews: Path):
    """导入20条人工裁判评分并计算一致率，保存新的审核版本。"""
    from kb.eval.judge_review import import_judge_reviews
    from kb.eval.protocol import protect_tune_path

    holdout = Path(load_config("eval")["holdout_path"])
    evaluation_command(lambda: protect_tune_path(samples, holdout))
    evaluation_command(lambda: protect_tune_path(reviews, holdout))
    output(
        {"run_directory": str(evaluation_command(lambda: import_judge_reviews(samples, reviews)))}
    )


@app.command()
def demo():
    """运行无密钥、无模型下载的合成数据演示。"""
    from kb.demo import run_demo

    output(run_demo())


@app.command("demo-web")
def demo_web(cloud: bool = False):
    """启动网页 demo；--cloud 使用 .env 模型，默认启动本地 Qwen。"""
    if cloud:
        import os

        from kb.web import serve

        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", LANGSMITH_TRACING="false")
        evaluation_command(lambda: serve(load_config("demo")["web_port"], cloud=True))
        return
    from kb.local_demo import run_local_demo

    evaluation_command(run_local_demo)


@app.command()
def scan():
    """盘点 data/raw 下的 PDF/DOCX；保留已填写的元数据。"""
    from kb.ingest.metadata import scan as scan_documents

    config = load_config("ingest")
    rows = scan_documents(Path(config["raw_dir"]), Path(config["registry"]), config)
    output(
        {
            "documents": len(rows),
            "registry": config["registry"],
            "missing_metadata": [
                r["file_name"] for r in rows if not r["doc_type"] or not r["project_name"]
            ],
        }
    )


@app.command()
def ingest():
    """使用本地 BGE 入库；先执行 scan 并补齐登记表。"""
    from kb.ingest.indexer import ingest as build_index
    from kb.retrieval.vector import LocalEmbedding

    config = load_config("ingest")
    report = build_index(config, LocalEmbedding(config["embedding"]))
    path = Path("docs/reports/ingest-report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output(report)


@app.command()
def search(question: str, keyword_only: bool = False):
    """本地检索。--keyword-only 仅 BM25，不加载 BGE 模型。"""
    if keyword_only:
        from kb.ingest.indexer import read_chunks
        from kb.retrieval.bm25 import BM25Index

        config = load_config("ingest")
        retrieval = BM25Index(
            read_chunks(Path(config["index_dir"])), Path(config["domain_dictionary"])
        )
        hits = retrieval.search(question, load_config("retrieval")["rerank_out"])
    else:
        from kb.runtime import make_retrieval

        retrieval, _ = make_retrieval()
        hits = retrieval.search(question)
    output([h.model_dump() for h in hits])


@app.command()
def ask(question: str):
    """检索并调用通义千问作答，会向配置的 API 发送检索片段。"""
    from kb.chains.rag_chain import RagChain
    from kb.runtime import make_llm, make_retrieval

    config = load_config("agent")
    llm = make_llm(config)
    retrieval, _ = make_retrieval()
    output(RagChain(retrieval, llm).invoke(question))


@app.command()
def chat(session: str = "default"):
    """有状态 Agent 对话，会调用通义千问；输入 /exit 退出。"""
    from kb.agent.graph import build_graph
    from kb.agent.tools import build_tools
    from kb.runtime import make_llm, make_retrieval

    config = load_config("agent")
    llm = make_llm(config)
    retrieval, chunks = make_retrieval()
    tools = build_tools(retrieval, chunks, llm, config)
    checkpoint = Path(config["checkpoint_path"])
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(checkpoint)) as saver:
        graph = build_graph(llm, tools, config, saver)
        while True:
            try:
                question = typer.prompt("你")
            except (EOFError, KeyboardInterrupt):
                break
            if question.strip() == "/exit":
                break
            if not question.strip():
                continue
            try:
                state = graph.invoke(
                    {"messages": [HumanMessage(content=question)]},
                    {
                        "configurable": {"thread_id": session},
                        "recursion_limit": config["recursion_limit"],
                    },
                )
                typer.echo(state["messages"][-1].content)
            except GraphRecursionError:
                typer.echo("已达到最大推理步数。本会话可能有未完成工具调用，请换 --session 重试。")
                break


@app.command("split-eval")
def split_eval(source: Path):
    """将已人工审核的 QA 数据按题型分层划分，禁止覆盖已有留出集。"""
    from kb.eval.dataset import split_dataset

    config = load_config("eval")
    output(
        split_dataset(
            source,
            Path(config["tune_path"]),
            Path(config["holdout_path"]),
            config["tune_fraction"],
            config["seed"],
        )
    )


if __name__ == "__main__":
    app()
