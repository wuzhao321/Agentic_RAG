import json
from copy import deepcopy
from pathlib import Path

import pytest

from kb.chains.prompts import NO_ANSWER
from kb.config import load_config
from kb.eval.ablation import configurations
from kb.eval.dataset import QACase
from kb.eval.protocol import freeze, protect_tune_path, verify_frozen
from kb.eval.runner import METRICS, evaluate_cases, run_rag


@pytest.fixture
def snapshot(tmp_path):
    cfg = {
        **load_config("eval"),
        "tune_path": str(tmp_path / "tune.jsonl"),
        "holdout_path": str(tmp_path / "holdout.jsonl"),
        "frozen_path": str(tmp_path / "frozen.json"),
        "runs_dir": str(tmp_path / "runs"),
        "bootstrap_samples": 50,
    }
    return {
        "configs": {"eval": cfg, "retrieval": load_config("retrieval")},
        "qwen_model": "test-only",
    }


def cases():
    return [
        QACase(
            id="1",
            question="超时多少？",
            reference_answer="3000 毫秒",
            reference_contexts=[{"doc_id": "demo"}],
            doc_type="接口说明书",
            project_name="测试",
            question_type="表格类",
        ),
        QACase(
            id="2",
            question="火星企鹅？",
            reference_answer=NO_ANSWER,
            reference_contexts=[],
            doc_type="其他",
            project_name="测试",
            question_type="无答案类",
        ),
    ]


class SyntheticChain:
    def invoke(self, question):
        return {
            "answer": NO_ANSWER if question == "火星企鹅？" else "3000 毫秒 [S1]",
            "retrieved_contexts": [] if question == "火星企鹅？" else ["3000 毫秒"],
            "sources": [],
        }


class SyntheticScorer:
    def score(self, row):
        assert row["question_type"] != "无答案类"
        return dict.fromkeys(METRICS, 0.8)


def write_cases(path):
    path.write_text("".join(case.model_dump_json() + "\n" for case in cases()), encoding="utf-8")


def test_ablation_configurations_and_grid():
    base = load_config("retrieval")
    assert [name for name, _ in configurations(base)] == ["A", "B", "C", "D"]
    assert [name for name, _ in configurations(base, final=True)] == ["A", "D"]
    variants = configurations(base, grid=load_config("eval")["grid"])
    assert len(variants) == 18
    variants[0][1]["reranker"]["min_score"] = 999
    assert base["reranker"]["min_score"] != 999
    with pytest.raises(ValueError):
        configurations(base, grid=load_config("eval")["grid"], final=True)


def test_tune_never_reads_holdout(snapshot, monkeypatch):
    cfg = snapshot["configs"]["eval"]
    write_cases(Path(cfg["tune_path"]))
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert path != Path(cfg["holdout_path"]), "tuning must not read holdout"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    run = run_rag(snapshot, lambda cfg: SyntheticChain(), SyntheticScorer())
    summary = json.loads((run / "summary.json").read_text())
    assert set(summary) == {"A", "B", "C", "D"}
    assert summary["D"]["metrics"]["faithfulness"]["valid_n"] == 1
    assert summary["D"]["correct_refusal_rate"] == 1
    assert (run / "report.md").exists()


def test_final_reserves_before_read_and_runs_once(snapshot, monkeypatch):
    cfg = snapshot["configs"]["eval"]
    holdout = Path(cfg["holdout_path"])
    write_cases(holdout)
    freeze(snapshot, Path(cfg["frozen_path"]))
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        if path == holdout:
            assert (holdout.parent / "holdout-final.lock").exists()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    run = run_rag(snapshot, lambda cfg: SyntheticChain(), SyntheticScorer(), final=True)
    summary = json.loads((run / "summary.json").read_text())
    assert set(summary) == {"A", "D"}
    with pytest.raises(FileExistsError):
        run_rag(snapshot, lambda cfg: SyntheticChain(), SyntheticScorer(), final=True)


def test_freeze_mismatch_prevents_final_read(snapshot):
    cfg = snapshot["configs"]["eval"]
    freeze(snapshot, Path(cfg["frozen_path"]))
    changed = deepcopy(snapshot)
    changed["qwen_model"] = "changed"
    with pytest.raises(ValueError, match="冻结"):
        run_rag(changed, None, None, final=True)
    assert not Path(cfg["holdout_path"]).exists()
    assert not (Path(cfg["holdout_path"]).parent / "holdout-final.lock").exists()
    with pytest.raises(FileExistsError):
        freeze(snapshot, Path(cfg["frozen_path"]))
    verify_frozen(snapshot, Path(cfg["frozen_path"]))


def test_failed_final_is_not_automatically_retryable(snapshot):
    cfg = snapshot["configs"]["eval"]
    freeze(snapshot, Path(cfg["frozen_path"]))
    # Missing holdout is deliberately exercised at the runner boundary.
    with pytest.raises(FileNotFoundError):
        run_rag(snapshot, lambda cfg: SyntheticChain(), SyntheticScorer(), final=True)
    marker = Path(cfg["holdout_path"]).parent / "holdout-final.lock"
    assert json.loads(marker.read_text())["status"] == "failed"
    with pytest.raises(FileExistsError):
        run_rag(snapshot, lambda cfg: SyntheticChain(), SyntheticScorer(), final=True)


@pytest.mark.parametrize("link_type", ["symlink", "hardlink"])
def test_tune_alias_to_holdout_rejected(tmp_path, link_type):
    holdout, tune = tmp_path / "holdout", tmp_path / "tune"
    holdout.write_text("must not read")
    if link_type == "symlink":
        tune.symlink_to(holdout)
    else:
        tune.hardlink_to(holdout)
    with pytest.raises(ValueError):
        protect_tune_path(tune, holdout)


def test_metric_nan_is_missing_not_success(snapshot, tmp_path):
    class BadScorer:
        def score(self, row):
            return {**dict.fromkeys(METRICS, 0.99), "faithfulness": float("nan")}

    directory = tmp_path / "nan"
    summary = evaluate_cases(
        cases(), SyntheticChain(), BadScorer(), snapshot["configs"]["eval"], directory
    )
    assert summary["error_n"] == 1
    assert summary["metrics"]["faithfulness"]["statistics"] is None
    assert summary["metrics"]["faithfulness"]["target_met"] is None
    assert "NaN" not in (directory / "results.jsonl").read_text()


def test_generation_saved_before_scoring_failure(snapshot, tmp_path):
    directory = tmp_path / "failure"

    class BrokenScorer:
        def score(self, row):
            assert (directory / "generations.jsonl").exists()
            raise TimeoutError("judge timeout")

    summary = evaluate_cases(
        cases(), SyntheticChain(), BrokenScorer(), snapshot["configs"]["eval"], directory
    )
    results = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines()]
    assert results[0]["error_type"] == "TimeoutError"
    assert results[0]["answer"] == "3000 毫秒 [S1]"
    assert summary["error_n"] == 1


def test_generation_failure_keeps_denominator(snapshot, tmp_path):
    class BrokenChain:
        def invoke(self, question):
            raise TimeoutError("generation timeout")

    summary = evaluate_cases(
        cases(), BrokenChain(), SyntheticScorer(), snapshot["configs"]["eval"], tmp_path / "broken"
    )
    assert summary["error_n"] == 2
    assert summary["correct_refusal_rate"] == 0
    assert summary["metrics"]["faithfulness"]["eligible_n"] == 1
