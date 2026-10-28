import json
import os
from pathlib import Path

from dotenv import load_dotenv

from kb.config import load_config
from kb.eval.runs import file_hash, source_hash
from kb.runtime import llm_identity


def same_file(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))


def protect_tune_path(tune: Path, holdout: Path):
    if same_file(tune, holdout):
        raise ValueError("调参集不能指向留出集（包括软链接与硬链接）")


def evaluation_snapshot(root: Path = Path("."), *, include_tune: bool = True) -> dict:
    configs = {name: load_config(name, root) for name in ["ingest", "retrieval", "agent", "eval"]}
    cfg = configs["eval"]
    tune, holdout = root / cfg["tune_path"], root / cfg["holdout_path"]
    protect_tune_path(tune, holdout)
    load_dotenv(root / ".env")
    identity = llm_identity(configs["agent"], os.environ)
    index = root / configs["ingest"]["index_dir"] / "active.json"
    if not index.exists():
        raise FileNotFoundError("尚无业务索引，请先完成 kb scan / kb ingest")
    manifest = json.loads(index.read_text(encoding="utf-8"))
    if manifest["embedding"] != configs["ingest"]["embedding"]:
        raise ValueError("embedding 配置与当前索引不一致")
    for model_cfg in [configs["ingest"]["embedding"], configs["retrieval"]["reranker"]]:
        if not model_cfg["revision"]:
            raise ValueError("正式评估必须固定两个本地模型的 revision")
    dictionary = root / configs["ingest"]["domain_dictionary"]
    return {
        "configs": configs,
        "llm_identity": identity,
        "index_fingerprint": manifest["fingerprint"],
        "index_collection": manifest["collection"],
        "source_sha256": source_hash(root),
        "tune_sha256": file_hash(tune) if include_tune else None,
        "uv_lock_sha256": file_hash(root / "uv.lock"),
        "dictionary_sha256": file_hash(dictionary) if dictionary.exists() else None,
    }


def freeze(snapshot: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Freezing is write-once. It cannot be silently rewritten after a final run.
    with path.open("x", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, allow_nan=False)


def verify_frozen(snapshot: dict, path: Path):
    if not path.exists():
        raise FileNotFoundError("请先完成调参并运行 kb freeze-eval")
    if snapshot != json.loads(path.read_text(encoding="utf-8")):
        raise ValueError("配置、代码、模型、索引或调参数据已改变，与冻结版本不一致")
