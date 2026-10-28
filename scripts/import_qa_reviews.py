"""Import human-approved QA only when decisions match the exact candidate file."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from kb.eval.dataset import read_cases


def import_reviews(source: Path, decisions: Path, output: Path):
    if output.exists():
        raise FileExistsError("审核后的数据已存在，禁止覆盖")
    cases = read_cases(source)
    payload = json.loads(decisions.read_text(encoding="utf-8"))
    if payload.get("dataset_sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("审核文件与候选集哈希不匹配，请重新审核修改后的版本")
    reviews = payload.get("reviews", [])
    ids = {case.id for case in cases}
    if len(reviews) != len(ids) or {r.get("id") for r in reviews} != ids:
        raise ValueError("必须逐条审核所有候选题；不允许重复或未知 ID")
    if any(
        r.get("approved") is not True
        or not isinstance(r.get("reviewer"), str)
        or not r["reviewer"].strip()
        for r in reviews
    ):
        raise ValueError("存在待审核、驳回或未署名条目，不能进入正式评估集")
    types = Counter(case.question_type for case in cases)
    if (
        len(cases) != 200
        or types["关键词/标识符类"] < 50
        or types["表格类"] < 50
        or types["无答案类"] != 20
    ):
        raise ValueError("数据未满足200条、关键词/表格各至少50条、无答案20条的协议")
    if len({case.question.strip() for case in cases}) != 200:
        raise ValueError("候选问题重复")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the exact signed decisions next to the import for later audit.
    audit = output.with_suffix(".reviews.json")
    if audit.exists():
        raise FileExistsError("审核记录已存在，禁止覆盖")
    with audit.open("x", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with output.open("x", encoding="utf-8") as f:
        f.write("".join(case.model_dump_json() + "\n" for case in cases))
    return {"imported": len(cases), "output": str(output), "split_created": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("decisions", type=Path)
    parser.add_argument(
        "--source", type=Path, default=Path("data/review/public-v1/qa_candidates_200.jsonl")
    )
    parser.add_argument("--output", type=Path, default=Path("data/eval/qa_all.jsonl"))
    args = parser.parse_args()
    print(json.dumps(import_reviews(args.source, args.decisions, args.output), ensure_ascii=False))
