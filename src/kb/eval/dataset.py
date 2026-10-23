import json
import random
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field


class QACase(BaseModel):
    id: str
    question: str = Field(min_length=1)
    reference_answer: str
    reference_contexts: list[dict]
    doc_type: str
    project_name: str
    question_type: str


def read_cases(path: Path) -> list[QACase]:
    cases = [
        QACase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cases:
        raise ValueError("评估数据为空")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("评估样本 id 重复")
    return cases


def split_dataset(source: Path, tune: Path, holdout: Path, fraction: float, seed: int):
    if tune.exists() or holdout.exists():
        raise FileExistsError("划分结果已存在，禁止覆盖调参集或留出集")
    if not 0 < fraction < 1:
        raise ValueError("tune_fraction 必须位于 (0, 1)")
    cases = read_cases(source)
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("评估样本 id 重复")
    if len({c.question.strip() for c in cases}) != len(cases):
        raise ValueError("评估问题重复，可能导致数据泄漏")
    groups = defaultdict(list)
    for case in cases:
        groups[case.question_type].append(case)
    rng = random.Random(seed)
    train, test = [], []
    # Largest remainders ensure the global 160/40 split for 200 samples.
    counts = {key: int(len(rows) * fraction) for key, rows in groups.items()}
    remainder = round(len(cases) * fraction) - sum(counts.values())
    order = sorted(groups, key=lambda k: (-(len(groups[k]) * fraction - counts[k]), k))
    for key in order[:remainder]:
        counts[key] += 1
    for key in sorted(groups):
        rows = groups[key]
        rng.shuffle(rows)
        train.extend(rows[: counts[key]])
        test.extend(rows[counts[key] :])
    for path, rows in [(tune, train), (holdout, test)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as f:
            f.write("".join(c.model_dump_json() + "\n" for c in rows))
    holdout.chmod(0o444)
    return {"tune": len(train), "holdout": len(test), "seed": seed}


def reserve_final_run(runs_dir: Path, final: bool) -> Path:
    if not final:
        raise ValueError("留出集仅允许通过 --final 运行")
    runs_dir.mkdir(parents=True, exist_ok=True)
    marker = runs_dir / "holdout-final.lock"
    # Reserve BEFORE reading holdout; a failed run is not silently retried.
    with marker.open("x", encoding="utf-8") as f:
        json.dump({"status": "reserved", "configurations": ["A", "D"]}, f)
    return marker
