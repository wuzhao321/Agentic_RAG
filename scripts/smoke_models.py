"""Run CPU smoke checks using synthetic strings only; never calls a cloud model."""

import json
import math
import time

from kb.config import load_config
from kb.retrieval.reranker import Reranker
from kb.retrieval.vector import LocalEmbedding
from kb.schema import Chunk, Hit


def main():
    start = time.perf_counter()
    embedding = LocalEmbedding(load_config("ingest")["embedding"])
    loaded = time.perf_counter()
    vectors = embedding.encode(["CreateOrder 接口超时为 3000 毫秒。"])
    encoded = time.perf_counter()
    print(
        json.dumps(
            {
                "embedding_dimensions": len(vectors[0]),
                "norm": math.sqrt(sum(x * x for x in vectors[0])),
                "load_seconds": loaded - start,
                "encode_seconds": encoded - loaded,
            }
        ),
        flush=True,
    )
    del embedding
    start = time.perf_counter()
    reranker = Reranker(load_config("retrieval")["reranker"])
    loaded = time.perf_counter()
    hits = [
        Hit(chunk=Chunk(id=str(i), text=text, metadata={}), score=0)
        for i, text in enumerate(["接口超时为 3000 毫秒。", "今天吃苹果。"])
    ]
    result = reranker.rank("接口超时是多少？", hits, 2)
    print(
        json.dumps(
            {
                "reranker_load_seconds": loaded - start,
                "rank_seconds": time.perf_counter() - loaded,
                "hits": [{"id": h.chunk.id, "score": h.score} for h in result],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
