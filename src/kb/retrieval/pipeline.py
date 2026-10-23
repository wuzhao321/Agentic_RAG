from kb.retrieval.router import route
from kb.retrieval.rrf import fuse


def exact_section_match(query, hit):
    path = hit.chunk.metadata.get("section_path", "")
    # A document title is too broad to promote above its more specific subsections.
    if " > " not in path:
        return False
    section = path.rsplit(" > ", 1)[-1]
    section = "".join(section.casefold().split())
    return len(section) >= 4 and section in "".join(query.casefold().split())


class RetrievalPipeline:
    def __init__(self, vector, bm25, reranker, config: dict, projects: list[str]):
        self.vector, self.bm25, self.reranker = vector, bm25, reranker
        self.config, self.projects = config, projects

    def search(self, query: str, filters: dict | None = None):
        cfg = self.config
        mode = cfg["mode"]
        if mode not in {"A", "B", "C", "D"}:
            raise ValueError("retrieval.mode 必须是 A/B/C/D")
        explicit = filters or {}
        inferred = route(query, self.projects, cfg["router"]) if mode == "D" else {}
        active = {**inferred, **explicit}

        def recall(where):
            vector = self.vector.search(query, cfg["recall_top_n"], where)
            if mode == "A":
                return vector[: cfg["rerank_in"]], set()
            keyword = self.bm25.search(query, cfg["recall_top_n"], where)
            candidates = fuse([vector, keyword], cfg["rrf_k"], cfg["rerank_in"])
            anchored = set()
            if mode == "D" and cfg.get("prefer_exact_section", False):
                # Preserve one literal chapter match from sparse recall. Semantic models can
                # confuse similarly worded but opposite topics such as required/optional.
                anchor = next((h for h in keyword if exact_section_match(query, h)), None)
                if anchor:
                    anchored.add(anchor.chunk.id)
                    candidates = [anchor, *(h for h in candidates if h.chunk.id not in anchored)]
                    candidates = candidates[: cfg["rerank_in"]]
            return candidates, anchored

        hits, anchored = recall(active)
        # Only inferred filters fall back. Explicit user constraints remain in force.
        if not hits and inferred and active != explicit:
            hits, anchored = recall(explicit)
        if mode in {"C", "D"}:
            ranked = self.reranker.rank(query, hits, len(hits) if anchored else cfg["rerank_out"])
            # Still require the normal reranker confidence threshold; never invent a score.
            if anchored:
                ranked.sort(key=lambda h: h.chunk.id not in anchored)
            return ranked[: cfg["rerank_out"]]
        return hits[: cfg["rerank_out"]]
