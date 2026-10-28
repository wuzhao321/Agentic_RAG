import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from kb.config import load_config
from kb.ingest.indexer import read_chunks
from kb.retrieval.bm25 import BM25Index
from kb.retrieval.pipeline import RetrievalPipeline
from kb.retrieval.reranker import Reranker
from kb.retrieval.vector import LocalEmbedding, VectorIndex, open_collection


def llm_settings(config, env=None, *, require_key=True):
    """Resolve a provider explicitly; never send one provider's key to another endpoint."""
    env = os.environ if env is None else env
    provider = env.get("LLM_PROVIDER") or config.get("provider", "dashscope")
    providers = config.get("providers", {})
    if provider not in providers:
        raise ValueError(f"未配置模型供应商：{provider}")
    profile = providers[provider]
    key, model = env.get(profile["key_env"]), env.get(profile["model_env"])
    if not model or (require_key and not key):
        raise ValueError(f"请在本地 .env 填写 {profile['key_env']} 和 {profile['model_env']}")
    params = {
        **config["llm"],
        **profile.get("parameters", {}),
        "base_url": profile["base_url"],
        "model": model,
    }
    if provider == "deepseek":
        # The documented DeepSeek Chat Completions schema has no seed parameter.
        params.pop("seed", None)
    return provider, key, params


def llm_identity(config, env=None):
    provider, _, params = llm_settings(config, env, require_key=False)
    return {
        "provider": provider,
        "parameters": params,
        "documented_version": config["providers"][provider].get("documented_version"),
        "immutable_model_version": False,
    }


def make_llm(config):
    load_dotenv()
    _, key, params = llm_settings(config)
    return ChatOpenAI(api_key=key, **params)


def make_retrieval(root: Path = Path("."), retrieval_config: dict | None = None):
    ingest = load_config("ingest", root)
    retrieval = retrieval_config or load_config("retrieval", root)
    index = root / ingest["index_dir"]
    manifest = json.loads((index / "active.json").read_text(encoding="utf-8"))
    if manifest["embedding"] != ingest["embedding"]:
        raise ValueError("当前 embedding 配置与索引不一致，请重新入库")
    chunks = read_chunks(index)
    collection = open_collection(index / "chroma", manifest["collection"], ingest["chroma"])
    vector = VectorIndex(collection, LocalEmbedding(ingest["embedding"]), retrieval)
    bm25 = BM25Index(chunks, root / ingest["domain_dictionary"])
    reranker = Reranker(retrieval["reranker"]) if retrieval["mode"] in {"C", "D"} else None
    projects = sorted(
        {str(c.metadata["project_name"]) for c in chunks if c.metadata["project_name"]}
    )
    return RetrievalPipeline(vector, bm25, reranker, retrieval, projects), chunks
