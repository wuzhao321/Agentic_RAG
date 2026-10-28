import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("review_import", Path("scripts/import_qa_reviews.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def review_pack(tmp_path):
    source = tmp_path / "candidates.jsonl"
    kinds = ["表格类"] * 50 + ["关键词/标识符类"] * 50 + ["概念类"] * 80 + ["无答案类"] * 20
    rows = [
        dict(
            id=str(i),
            question=f"Question {i}",
            reference_answer="Answer",
            reference_contexts=[],
            doc_type="其他",
            project_name="test",
            question_type=k,
        )
        for i, k in enumerate(kinds)
    ]
    source.write_text("".join(json.dumps(r) + "\n" for r in rows))
    payload = {
        "dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "reviews": [dict(id=r["id"], approved=True, reviewer="test-reviewer") for r in rows],
    }
    decisions, output = tmp_path / "reviews.json", tmp_path / "approved.jsonl"
    return source, payload, decisions, output


@pytest.mark.parametrize(
    "failure", ["pending", "rejected", "unsigned", "missing", "wrong_hash", "duplicate"]
)
def test_unreviewed_or_mismatched_data_cannot_enter_formal_set(review_pack, failure):
    source, payload, decisions, output = review_pack
    if failure in {"pending", "rejected"}:
        payload["reviews"][0]["approved"] = None if failure == "pending" else False
    elif failure == "unsigned":
        payload["reviews"][0]["reviewer"] = ""
    elif failure == "missing":
        payload["reviews"].pop()
    elif failure == "duplicate":
        payload["reviews"][0] = payload["reviews"][1]
    else:
        payload["dataset_sha256"] = "wrong"
    decisions.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        module.import_reviews(source, decisions, output)
    assert not output.exists()


def test_approved_import_is_auditable_and_does_not_split_or_overwrite(review_pack):
    source, payload, decisions, output = review_pack
    decisions.write_text(json.dumps(payload))
    result = module.import_reviews(source, decisions, output)
    assert result["imported"] == 200 and not result["split_created"]
    assert json.loads(output.with_suffix(".reviews.json").read_text()) == payload
    with pytest.raises(FileExistsError):
        module.import_reviews(source, decisions, output)
