import json

import pytest

from kb.eval.dataset import read_cases, reserve_final_run, split_dataset
from kb.eval.report import bootstrap_mean


def test_agent_metrics_do_not_invent_completion():
    from kb.eval.agent_eval import summarize_results

    rows = [
        {
            "tools_correct": True,
            "args_correct": i != 0,
            "completed_human": None,
            "steps": 2,
            "latency_seconds": i + 1,
        }
        for i in range(40)
    ]
    result = summarize_results(rows)
    assert result["completion_rate"] is None
    assert result["mean_steps"] == 2
    assert result["p95"] == 38
    assert result["parameter_error_rate"] == 1 / 40


def test_stratified_split_reproducible_and_holdout_protection(tmp_path):
    rows = [
        {
            "id": str(i),
            "question": f"问题{i}",
            "reference_answer": "答案",
            "reference_contexts": [],
            "doc_type": "其他",
            "project_name": "测试",
            "question_type": str(i % 5),
        }
        for i in range(200)
    ]
    source = tmp_path / "all.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    tune, holdout = tmp_path / "tune.jsonl", tmp_path / "holdout.jsonl"
    assert split_dataset(source, tune, holdout, 0.8, 42) == {"tune": 160, "holdout": 40, "seed": 42}
    # These are generated unit-test fixtures, not business evaluation data.
    assert len(read_cases(tune)) == 160 and len(read_cases(holdout)) == 40
    assert not holdout.stat().st_mode & 0o222
    with pytest.raises(FileExistsError):
        split_dataset(source, tune, holdout, 0.8, 42)
    second_tune, second_holdout = tmp_path / "t2.jsonl", tmp_path / "h2.jsonl"
    split_dataset(source, second_tune, second_holdout, 0.8, 42)
    assert tune.read_bytes() == second_tune.read_bytes()


def test_final_run_gate_before_read(tmp_path):
    with pytest.raises(ValueError):
        reserve_final_run(tmp_path, False)
    marker = reserve_final_run(tmp_path, True)
    assert marker.exists()
    with pytest.raises(FileExistsError):
        reserve_final_run(tmp_path, True)


def test_bootstrap_is_deterministic():
    a = bootstrap_mean([0.1, 0.3, 0.5], 1000, 42)
    assert a == bootstrap_mean([0.1, 0.3, 0.5], 1000, 42)
    assert a["low"] <= a["mean"] <= a["high"]
