from types import SimpleNamespace

from kb.eval.local_validation import retrieval_results
from kb.schema import Hit


def test_retrieval_metrics_count_misses_and_keep_denominators(make_chunk):
    cases = [
        SimpleNamespace(id="a", question="hit", reference_contexts=[{"chunk_ids": ["gold"]}]),
        SimpleNamespace(id="b", question="miss", reference_contexts=[{"chunk_ids": ["other"]}]),
        SimpleNamespace(id="c", question="absent", reference_contexts=[]),
    ]

    class Search:
        def search(self, query):
            return (
                [Hit(chunk=make_chunk("wrong"), score=1), Hit(chunk=make_chunk("gold"), score=0.5)]
                if query == "hit"
                else []
            )

    rows, stats = retrieval_results(cases, Search())
    assert stats["answerable_n"] == 2 and stats["hit_rate"] == 0.5
    assert stats["mrr"] == 0.25 and stats["no_answer_empty"] == 1
    assert rows[1]["evidence_hit"] is False and rows[2]["evidence_hit"] is None


def test_summary_trigger_alone_is_not_conversation_acceptance():
    from kb.eval.local_validation import conversation_passed

    result = dict(
        turns=12,
        summary_updates=3,
        checkpoint_restored=True,
        session_isolated=True,
        tool_pairs_intact=True,
        final_points_present=False,
    )
    assert not conversation_passed({"rag": result})
    result["final_points_present"] = True
    assert conversation_passed({"rag": result})
