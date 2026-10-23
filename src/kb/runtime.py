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


def make_llm(config):
    load_dotenv()
    key, model = os.getenv("DASHSCOPE_API_KEY"), os.getenv("QWEN_MODEL")
    if not key or not model:
        raise ValueError("请在 .env 中填写 DASHSCOPE_API_KEY 和锁定版本的 QWEN_MODEL")
    return ChatOpenAI(api_key=key, model=model, **config["llm"])


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
