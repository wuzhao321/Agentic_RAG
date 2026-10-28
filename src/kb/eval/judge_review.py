"""Versioned comparison of signed human scores and frozen automatic scores."""

import json
import math
import statistics
from pathlib import Path

from kb.eval.runs import Run, file_hash, write_json

METRICS = ("faithfulness", "answer_relevancy", "context_recall")


def compare_reviews(samples: list[dict], payload: dict, sample_hash: str) -> dict:
    if payload.get("sample_sha256") != sample_hash:
        raise ValueError("人工复核与抽样文件哈希不一致")
    ids = {s["review_id"] for s in samples}
    reviews = payload.get("reviews", [])
    if (
        len(samples) != 20
        or len(ids) != 20
        or len(reviews) != 20
        or {r.get("id") for r in reviews} != ids
    ):
        raise ValueError("必须完整审核20条不重复样本，不能缺失或增加 ID")
    by_id = {r["id"]: r for r in reviews}
    errors = {m: [] for m in METRICS}
    for sample in samples:
        review = by_id[sample["review_id"]]
        for field in ("reviewer", "notes"):
            if not isinstance(review.get(field), str) or not review[field].strip():
                raise ValueError("每条复核必须填写审核人和评分依据")
        for metric in METRICS:
            human, auto = review.get(metric + "_human"), sample["metrics"].get(metric)
            for value in (human, auto):
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    raise ValueError("人工与自动评分必须是0到1的有限数值，不能缺失")
            errors[metric].append(abs(human - auto))
    return {
        "n": 20,
        "human_reviewed": True,
        "agreement_rule": "absolute score difference <= 0.20",
        "tolerance": 0.20,
        "metrics": {
            metric: {
                "n": len(values),
                "mean_absolute_error": statistics.mean(values),
                "within_tolerance_n": sum(v <= 0.20 + 1e-12 for v in values),
                "agreement_rate": statistics.mean(v <= 0.20 + 1e-12 for v in values),
            }
            for metric, values in errors.items()
        },
        "note": "一致率为本项目预定义容差口径，不等于答案正确率或正式验收通过。",
    }


def import_judge_reviews(sample_path: Path, reviews_path: Path) -> Path:
    samples = [json.loads(x) for x in sample_path.read_text().splitlines() if x.strip()]
    payload = json.loads(reviews_path.read_text())
    digest = file_hash(sample_path)
    summary = compare_reviews(samples, payload, digest)
    run = Run(
        sample_path.parent / "judge-reviews",
        "human-judge",
        {"sample_sha256": digest, "reviews_sha256": file_hash(reviews_path)},
    )
    write_json(run.path / "samples.json", samples)
    write_json(run.path / "reviews.json", payload)
    write_json(run.path / "summary.json", summary)
    run.status("complete")
    return run.path
