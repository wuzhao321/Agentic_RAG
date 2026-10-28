import importlib.metadata
import os
from pathlib import Path

from dotenv import dotenv_values

from kb.config import load_config


def inspect_setup(root: Path = Path(".")) -> dict:
    ingest, agent, evaluation = (load_config(name, root) for name in ["ingest", "agent", "eval"])
    retrieval = load_config("retrieval", root)
    env = {**dotenv_values(root / ".env"), **os.environ}
    raw = root / ingest["raw_dir"]
    documents = sum(p.is_file() and p.suffix.lower() in {".pdf", ".docx"} for p in raw.rglob("*"))
    result = {
        "raw_document_count": documents,
        "registry_exists": (root / ingest["registry"]).exists(),
        "index_exists": (root / ingest["index_dir"] / "active.json").exists(),
        "ledger_exists": (root / agent["ledger_path"]).exists(),
        "dashscope_key_configured": bool(env.get("DASHSCOPE_API_KEY")),
        "qwen_model_configured": bool(env.get("QWEN_MODEL")),
        "llm_provider": env.get("LLM_PROVIDER") or agent.get("provider", "dashscope"),
        "deepseek_key_configured": bool(env.get("DEEPSEEK_API_KEY")),
        "deepseek_model_configured": bool(env.get("DEEPSEEK_MODEL")),
        "tune_exists": (root / evaluation["tune_path"]).exists(),
        "holdout_exists": (root / evaluation["holdout_path"]).exists(),
        "frozen_exists": (root / evaluation["frozen_path"]).exists(),
        "models": {},
    }
    for name, config in [("embedding", ingest["embedding"]), ("reranker", retrieval["reranker"])]:
        result["models"][name] = {
            "config_exists": (root / config["model"] / "config.json").exists(),
            "revision_pinned": bool(config["revision"]),
        }
    try:
        result["ragas_version"] = importlib.metadata.version("ragas")
    except importlib.metadata.PackageNotFoundError:
        result["ragas_version"] = None
    result["next_steps"] = []
    if not documents:
        result["next_steps"].append("放入首批 PDF/DOCX 到 data/raw，再运行 kb scan")
    if not result["index_exists"]:
        result["next_steps"].append("补齐登记表元数据并运行 kb ingest")
    if not result["ledger_exists"]:
        result["next_steps"].append("准备 data/ledger/projects.json")
    profile = agent.get("providers", {}).get(result["llm_provider"], {})
    if not env.get(profile.get("key_env", "")) or not env.get(profile.get("model_env", "")):
        result["next_steps"].append(
            f"在本地 .env 填写 {profile.get('key_env', '供应商密钥')} "
            f"和 {profile.get('model_env', '模型名')}"
        )
    if not result["tune_exists"]:
        result["next_steps"].append("审核 QA 标注后运行 kb split-eval")
    return result
