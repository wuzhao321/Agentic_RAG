from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.config import Settings

from kb.schema import Chunk, Hit


class LocalEmbedding:
    def __init__(self, config: dict):
        from sentence_transformers import SentenceTransformer

        path = Path(config["model"])
        if not path.exists() or not any(path.iterdir()):
            raise FileNotFoundError(f"本地 embedding 模型不存在: {path}；请先下载并记录 revision")
        self.config = config
        self.model = SentenceTransformer(str(path), device=config["device"], local_files_only=True)

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            texts,
            normalize_embeddings=self.config["normalize"],
            batch_size=self.config["batch_size"],
        ).tolist()
        if any(len(v) != self.config["dimensions"] for v in vectors):
            raise ValueError("embedding 维度与配置不一致")
        return vectors


def open_collection(path: Path, name: str, hnsw: dict):
    client = chromadb.PersistentClient(str(path), settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(
        name, embedding_function=None, configuration={"hnsw": hnsw}
    )


def where_clause(filters: dict | None) -> dict | None:
    pairs = [{k: {"$eq": v}} for k, v in (filters or {}).items()]
    return ({"$and": pairs} if len(pairs) > 1 else pairs[0]) if pairs else None


class VectorIndex:
    def __init__(self, collection, embedding, config: dict):
        self.collection, self.embedding, self.config = collection, embedding, config
        self.query_vector = lru_cache(maxsize=config["query_cache_size"])(
            lambda text: embedding.encode([text])[0]
        )

    def search(self, query: str, limit: int, filters: dict | None = None) -> list[Hit]:
        if not self.collection.count():
            return []
        prefix = self.config["query_prefix"] if self.config["use_query_prefix"] else ""
        result = self.collection.query(
            query_embeddings=[self.query_vector(prefix + query)],
            n_results=min(limit, self.collection.count()),
            where=where_clause(filters),
            include=["documents", "metadatas", "distances"],
        )
        return [
            Hit(chunk=Chunk(id=i, text=t, metadata=m), score=1 - d)
            for i, t, m, d in zip(
                result["ids"][0],
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
                strict=False,
            )
            if d <= self.config["vector_max_distance"]
        ]
