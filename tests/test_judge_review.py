from copy import deepcopy

import pytest

from kb.eval.judge_review import METRICS, compare_reviews


def pack():
    samples = [dict(review_id=str(i), metrics=dict.fromkeys(METRICS, 0.5)) for i in range(20)]
    payload = {
        "sample_sha256": "hash",
        "reviews": [
            {
                "id": str(i),
                "reviewer": "Reviewer",
                "notes": "Evidence checked",
                **{m + "_human": 0.7 if i < 10 else 0.8 for m in METRICS},
            }
            for i in range(20)
        ],
    }
    return samples, payload


def test_agreement_preserves_disagreement_and_boundary():
    samples, payload = pack()
    before = deepcopy(payload)
    result = compare_reviews(samples, payload, "hash")
    for metric in METRICS:
        assert result["metrics"][metric]["agreement_rate"] == 0.5
        assert result["metrics"][metric]["mean_absolute_error"] == pytest.approx(0.25)
    assert payload == before


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), -0.1, 1.1, "0.5"])
def test_missing_or_invalid_score_cannot_be_reported_as_reviewed(bad):
    samples, payload = pack()
    payload["reviews"][0]["faithfulness_human"] = bad
    with pytest.raises(ValueError):
        compare_reviews(samples, payload, "hash")


@pytest.mark.parametrize("problem", ["hash", "duplicate", "missing", "unsigned", "no_notes"])
def test_rejects_unbound_or_incomplete_review(problem):
    samples, payload = pack()
    if problem == "hash":
        payload["sample_sha256"] = "wrong"
    elif problem == "duplicate":
        payload["reviews"][0] = payload["reviews"][1]
    elif problem == "missing":
        payload["reviews"].pop()
    else:
        payload["reviews"][0]["reviewer" if problem == "unsigned" else "notes"] = ""
    with pytest.raises(ValueError):
        compare_reviews(samples, payload, "hash")
