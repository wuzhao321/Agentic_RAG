from kb.config import load_config
from kb.retrieval.bm25 import BM25Index
from kb.retrieval.pipeline import RetrievalPipeline, exact_section_match
from kb.retrieval.router import route
from kb.retrieval.rrf import fuse
from kb.retrieval.vector import VectorIndex, where_clause
from kb.schema import Hit


def test_bm25_identifier_and_metadata(make_chunk):
    chunks = [
        make_chunk("a", "timeout_ms 3000", project_name="星河"),
        make_chunk("b", "timeout_ms 6000", project_name="天河"),
    ]
    index = BM25Index(chunks)
    assert [h.chunk.id for h in index.search("timeout_ms", 3, {"project_name": "星河"})] == ["a"]
    assert index.search("unknown_identifier", 3) == []
    assert BM25Index([]).search("hello", 3) == []


def test_rrf_scores_and_duplicate(make_chunk):
    a, b = Hit(chunk=make_chunk("a"), score=0.1), Hit(chunk=make_chunk("b"), score=99)
    hits = fuse([[a, b, a], [b]], k=60, limit=2)
    assert hits[0].chunk.id == "b"
    assert hits[1].score == 1 / 61


def test_router_ambiguous_and_exact():
    config = load_config("retrieval")["router"]
    assert route("星河的技术规范", ["星河", "天河"], config) == {
        "doc_type": "技术规范",
        "project_name": "星河",
    }
    assert "project_name" not in route("对比星河和天河", ["星河", "天河"], config)
    assert route("一般问题", ["星河"], config) == {}


class FakeSearch:
    def __init__(self, hit):
        self.hit, self.filters = hit, []

    def search(self, query, limit, filters):
        self.filters.append(filters)
        return [] if filters else [self.hit]


class FakeRanker:
    def rank(self, query, hits, limit):
        return hits[:limit]


def test_inferred_filter_fallback(make_chunk):
    vector = FakeSearch(Hit(chunk=make_chunk(), score=1))
    pipe = RetrievalPipeline(vector, vector, FakeRanker(), load_config("retrieval"), ["星河"])
    assert pipe.search("星河项目接口")
    assert {"project_name": "星河"} in vector.filters and {} in vector.filters


def test_explicit_filters_never_silently_discarded(make_chunk):
    vector = FakeSearch(Hit(chunk=make_chunk(), score=1))
    pipe = RetrievalPipeline(vector, vector, FakeRanker(), load_config("retrieval"), ["星河"])
    assert pipe.search("星河项目接口", {"project_name": "不存在"}) == []
    assert {} not in vector.filters


def test_chroma_filters():
    assert where_clause({}) is None
    assert where_clause({"project_name": "星河"}) == {"project_name": {"$eq": "星河"}}
    assert len(where_clause({"project_name": "星河", "doc_type": "其他"})["$and"]) == 2


def test_vector_threshold_and_query_cache(make_chunk):
    chunk = make_chunk()

    class Embedding:
        count = 0

        def encode(self, texts):
            self.count += 1
            return [[1, 0]]

    class Collection:
        def count(self):
            return 2

        def query(self, **kwargs):
            return {
                "ids": [["a", "b"]],
                "documents": [[chunk.text, chunk.text]],
                "metadatas": [[chunk.metadata, chunk.metadata]],
                "distances": [[0.1, 0.99]],
            }

    embedding = Embedding()
    vector = VectorIndex(Collection(), embedding, load_config("retrieval"))
    assert len(vector.search("query", 20)) == 1
    vector.search("query", 20)
    assert embedding.count == 1


def test_literal_chapter_anchor_survives_rrf_cutoff_and_preserves_scores(make_chunk):
    required = Hit(chunk=make_chunk("required", section_path="查询参数 > 必选查询参数"), score=9)
    optional = Hit(chunk=make_chunk("optional", section_path="查询参数 > 可选查询参数"), score=10)

    class Search:
        def __init__(self, hits):
            self.hits = hits

        def search(self, query, limit, filters):
            return [
                h
                for h in self.hits
                if all(h.chunk.metadata.get(k) == v for k, v in filters.items())
            ][:limit]

    class Ranker:
        def rank(self, query, hits, limit):
            return sorted(hits, key=lambda h: h.score, reverse=True)[:limit]

    config = {**load_config("retrieval"), "prefer_exact_section": True, "rerank_out": 1}
    pipe = RetrievalPipeline(Search([optional]), Search([required, optional]), Ranker(), config, [])
    hits = pipe.search("FastAPI 必选查询参数的声明方式")
    assert hits[0].chunk.id == "required" and hits[0].score == 9
    assert not exact_section_match("必选查询参数", optional)
    assert not exact_section_match(
        "FastAPI 中如何把查询参数设置为必选？",
        Hit(chunk=make_chunk(section_path="查询参数"), score=1),
    )
    assert pipe.search("必选查询参数", {"project_name": "不匹配"}) == []


def test_section_anchor_does_not_bypass_reranker_threshold(make_chunk):
    hit = Hit(chunk=make_chunk(section_path="查询参数 > 必选查询参数"), score=1)

    class RejectingRanker:
        def rank(self, query, hits, limit):
            return []

    search = FakeSearch(hit)
    config = {**load_config("retrieval"), "prefer_exact_section": True}
    pipe = RetrievalPipeline(search, search, RejectingRanker(), config, [])
    assert pipe.search("必选查询参数") == []
