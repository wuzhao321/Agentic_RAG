from collections import defaultdict

from kb.schema import Hit


def fuse(rankings: list[list[Hit]], k: int, limit: int) -> list[Hit]:
    scores, chunks = defaultdict(float), {}
    for ranking in rankings:
        seen = set()
        for rank, hit in enumerate(ranking, 1):
            if hit.chunk.id in seen:
                continue
            seen.add(hit.chunk.id)
            scores[hit.chunk.id] += 1 / (k + rank)
            chunks[hit.chunk.id] = hit.chunk
    ids = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
    return [Hit(chunk=chunks[key], score=scores[key]) for key in ids]
