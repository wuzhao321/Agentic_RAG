from pathlib import Path

from kb.chains.rag_chain import RagChain
from kb.config import load_config
from kb.eval.protocol import evaluation_snapshot, verify_frozen
from kb.eval.ragas_runner import RagasScorer
from kb.eval.runner import run_rag
from kb.retrieval.pipeline import RetrievalPipeline
from kb.retrieval.vector import VectorIndex
from kb.runtime import make_llm, make_retrieval


def evaluate_rag(*, final=False, grid=False) -> Path:
    if final and grid:
        raise ValueError("--final 不能与 --grid 同时使用")
    snapshot = evaluation_snapshot()
    config = snapshot["configs"]
    eval_config = config["eval"]
    data = Path(eval_config["holdout_path"] if final else eval_config["tune_path"])
    if not data.exists():
        raise FileNotFoundError(f"缺少评估数据: {data}")
    if final:
        verify_frozen(snapshot, Path(eval_config["frozen_path"]))
    # Load dependencies and local models before reserving the holdout run.
    # All variants share one model instance and the same index snapshot.
    llm = make_llm(config["agent"])
    base, _ = make_retrieval(retrieval_config={**config["retrieval"], "mode": "D"})

    def chain_factory(retrieval_config):
        vector = VectorIndex(base.vector.collection, base.vector.embedding, retrieval_config)
        pipeline = RetrievalPipeline(
            vector, base.bm25, base.reranker, retrieval_config, base.projects
        )
        return RagChain(pipeline, llm)

    with RagasScorer(llm, base.vector.embedding, eval_config) as scorer:
        return run_rag(snapshot, chain_factory, scorer, final=final, grid=grid)


def freeze_evaluation() -> Path:
    from kb.eval.protocol import freeze

    snapshot = evaluation_snapshot()
    config = load_config("eval")
    path = Path(config["frozen_path"])
    lock = Path(config["holdout_path"]).resolve().parent / "holdout-final.lock"
    if lock.exists():
        raise FileExistsError("最终评估已经保留或运行，不能重新冻结")
    freeze(snapshot, path)
    return path


def evaluate_agent(*, retest=False) -> Path:
    from langgraph.checkpoint.memory import InMemorySaver

    from kb.agent.graph import build_graph
    from kb.agent.tools import build_tools
    from kb.eval.agent_eval import read_tasks, run_agent_tasks
    from kb.eval.protocol import same_file

    snapshot = evaluation_snapshot(include_tune=False)
    configs = snapshot["configs"]
    cfg = configs["eval"]
    path = Path(cfg["agent_retest_path"] if retest else cfg["agent_tasks_path"])
    if same_file(path, Path(cfg["holdout_path"])):
        raise ValueError("Agent 数据不能指向 QA 留出集")
    tasks = read_tasks(path)
    if retest:
        if same_file(Path(cfg["agent_tasks_path"]), Path(cfg["holdout_path"])):
            raise ValueError("首批 Agent 数据不能指向 QA 留出集")
        baseline = read_tasks(Path(cfg["agent_tasks_path"]))
        if {t.id for t in tasks} & {t.id for t in baseline} or {
            t.question.strip() for t in tasks
        } & {t.question.strip() for t in baseline}:
            raise ValueError("复测集与首批任务有重叠，请使用新标注任务")
    llm = make_llm(configs["agent"])
    retrieval, chunks = make_retrieval()
    graph = build_graph(
        llm,
        build_tools(retrieval, chunks, llm, configs["agent"]),
        configs["agent"],
        InMemorySaver(),
    )
    return run_agent_tasks(graph, tasks, snapshot, path, retest=retest)
