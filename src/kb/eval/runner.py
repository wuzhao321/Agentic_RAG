import math
import statistics
import time
from pathlib import Path

from kb.chains.prompts import NO_ANSWER
from kb.eval.ablation import configurations
from kb.eval.dataset import read_cases, reserve_final_run
from kb.eval.protocol import protect_tune_path, verify_frozen
from kb.eval.report import bootstrap_mean
from kb.eval.runs import Run, append_jsonl, file_hash, write_json

METRICS = ("faithfulness", "answer_relevancy", "context_recall")


def summarize_rag(rows: list[dict], config: dict) -> dict:
    answerable = [r for r in rows if r["question_type"] != config["no_answer_type"]]
    unanswerable = [r for r in rows if r["question_type"] == config["no_answer_type"]]
    metrics = {}
    for metric in METRICS:
        values = [r["metrics"][metric] for r in answerable if r["metrics"][metric] is not None]
        complete = bool(answerable) and len(values) == len(answerable)
        stats = (
            bootstrap_mean(values, config["bootstrap_samples"], config["seed"]) if values else None
        )
        metrics[metric] = {
            "statistics": stats,
            "valid_n": len(values),
            "eligible_n": len(answerable),
            "complete": complete,
            "target_met": (stats["mean"] >= config["targets"][metric] if complete else None),
        }
    return {
        "n": len(rows),
        "answerable_n": len(answerable),
        "metrics": metrics,
        "error_n": sum(r["status"].endswith("error") for r in rows),
        "no_answer_n": len(unanswerable),
        "correct_refusal_rate": (
            statistics.mean(r["answer"] == NO_ANSWER for r in unanswerable)
            if unanswerable
            else None
        ),
    }


def evaluate_cases(cases, chain, scorer, config: dict, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    rows = []
    for case in cases:
        start = time.perf_counter()
        row = {
            **case.model_dump(),
            "answer": None,
            "retrieved_contexts": [],
            "sources": [],
            "metrics": dict.fromkeys(METRICS),
            "status": "running",
        }
        try:
            result = chain.invoke(case.question)
            row.update(
                answer=result["answer"],
                retrieved_contexts=result["retrieved_contexts"],
                sources=result["sources"],
                warning=result.get("warning"),
            )
        except Exception as exc:
            row.update(status="generation_error", error_type=type(exc).__name__)
        row["generation_seconds"] = time.perf_counter() - start
        # Persist generation before the judge request so an interrupted judge cannot erase it.
        append_jsonl(directory / "generations.jsonl", row)
        if row["status"] != "generation_error":
            if case.question_type == config["no_answer_type"]:
                row["status"] = "refusal_check"
            else:
                try:
                    scores = scorer.score(row)
                    for metric in METRICS:
                        value = float(scores[metric])
                        row["metrics"][metric] = value if math.isfinite(value) else None
                    row["status"] = (
                        "complete"
                        if all(v is not None for v in row["metrics"].values())
                        else "metric_error"
                    )
                except Exception as exc:
                    row.update(status="metric_error", error_type=type(exc).__name__)
        row["total_seconds"] = time.perf_counter() - start
        append_jsonl(directory / "results.jsonl", row)
        rows.append(row)
    summary = summarize_rag(rows, config)
    write_json(directory / "summary.json", summary)
    return summary


def write_comparison(run: Run, results: dict):
    lines = [
        "# RAG 评估结果",
        "",
        "数值来自本次运行；缺失指标不计为 0，未满覆盖率不判断达标。",
        "",
        "| 配置 | Faithfulness | Answer Relevancy | Context Recall | 正确拒答率 | 错误数 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, summary in results.items():
        cells = []
        for metric in METRICS:
            value = summary["metrics"][metric]
            stat = value["statistics"]
            cells.append(
                (
                    f"{stat['mean']:.4f} [{stat['low']:.4f}, {stat['high']:.4f}] "
                    f"({value['valid_n']}/{value['eligible_n']})"
                )
                if stat
                else "未得到有效值"
            )
        refusal = summary["correct_refusal_rate"]
        cells.append(f"{refusal:.2%}" if refusal is not None else "无此题型")
        lines.append("| " + " | ".join([name, *cells, str(summary["error_n"])]) + " |")
    (run.path / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_rag(
    snapshot: dict, chain_factory, scorer, *, final=False, grid=False, root: Path = Path(".")
) -> Path:
    config = snapshot["configs"]["eval"]
    tune, holdout = root / config["tune_path"], root / config["holdout_path"]
    protect_tune_path(tune, holdout)
    variants = configurations(
        snapshot["configs"]["retrieval"], grid=config["grid"] if grid else None, final=final
    )
    marker = None
    if final:
        verify_frozen(snapshot, root / config["frozen_path"])
        # Anchor the one-time lock beside the canonical holdout, not in a movable output directory.
        marker = reserve_final_run(holdout.resolve().parent, final=True)
    run = None
    try:
        run = Run(root / config["runs_dir"], "final" if final else "tune", snapshot)
        if marker:
            write_json(
                marker, {"status": "running", "run": str(run.path), "configurations": ["A", "D"]}
            )
        dataset_path = holdout if final else tune
        cases = read_cases(dataset_path)
        write_json(
            run.path / "dataset.json",
            {"path": str(dataset_path), "sha256": file_hash(dataset_path), "n": len(cases)},
        )
        results = {}
        for name, retrieval_config in variants:
            write_json(run.path / f"{name}.config.json", retrieval_config)
            chain = chain_factory(retrieval_config)
            results[name] = evaluate_cases(cases, chain, scorer, config, run.path / name)
            write_comparison(run, results)
        write_json(run.path / "summary.json", results)
        has_errors = any(s["error_n"] for s in results.values())
        status = "completed_with_errors" if has_errors else "complete"
        if grid:
            candidates = [
                (name, statistics.mean(s["metrics"][m]["statistics"]["mean"] for m in METRICS))
                for name, s in results.items()
                if all(s["metrics"][m]["complete"] for m in METRICS)
            ]
            candidates.sort(key=lambda pair: (-pair[1], pair[0]))
            write_json(
                run.path / "recommendation.json",
                {
                    "criterion": "三个 RAGAS 指标等权均值；仅比较完整覆盖的配置，不自动改写配置",
                    "best": candidates[0][0] if candidates else None,
                    "ranking": candidates,
                },
            )
        run.status(status)
        if marker:
            write_json(
                marker, {"status": status, "run": str(run.path), "configurations": ["A", "D"]}
            )
        return run.path
    except BaseException as exc:
        if run:
            run.status("failed", error_type=type(exc).__name__)
        if marker:
            write_json(
                marker,
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "run": str(run.path) if run else None,
                },
            )
        raise
