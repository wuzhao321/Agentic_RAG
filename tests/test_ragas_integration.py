import asyncio
import json
import os

import pytest
from langchain_core.language_models.fake import FakeListLLM

from kb.config import load_config

os.environ["RAGAS_DO_NOT_TRACK"] = "true"
pytest.importorskip("ragas")

from kb.eval.ragas_runner import BgeEmbeddings, RagasScorer  # noqa: E402

seen_loops = []


class SyntheticJudge(FakeListLLM):
    """Return structured fixtures for the real RAGAS prompt parsers, without network."""

    def _call(self, prompt, stop=None, run_manager=None, **kwargs):
        if '"attributed"' in prompt:
            return json.dumps(
                {
                    "classifications": [
                        {"statement": "超时为 3000 毫秒", "reason": "资料明确写出", "attributed": 1}
                    ]
                }
            )
        if '"verdict"' in prompt:
            return json.dumps(
                {
                    "statements": [
                        {"statement": "超时为 3000 毫秒", "reason": "资料明确写出", "verdict": 1}
                    ]
                }
            )
        if '"noncommittal"' in prompt:
            return json.dumps({"question": "接口超时是多少？", "noncommittal": 0})
        return json.dumps({"statements": ["超时为 3000 毫秒"]})

    async def _acall(self, prompt, stop=None, run_manager=None, **kwargs):
        seen_loops.append(asyncio.get_running_loop())
        return self._call(prompt, stop, run_manager, **kwargs)


class SyntheticEmbedding:
    def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_actual_ragas_metrics_with_offline_structured_judge(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    cfg = load_config("eval")
    scorer = RagasScorer(SyntheticJudge(responses=[]), SyntheticEmbedding(), cfg)
    result = scorer.score(
        {
            "question": "接口超时是多少？",
            "answer": "超时为 3000 毫秒",
            "retrieved_contexts": ["接口超时为 3000 毫秒"],
            "reference_answer": "3000 毫秒",
        }
    )
    assert set(result) == {"faithfulness", "answer_relevancy", "context_recall"}
    assert all(value == pytest.approx(1.0) for value in result.values())
    assert scorer.llm.bypass_temperature and scorer.llm.bypass_n


def test_local_embedding_adapter():
    adapter = BgeEmbeddings(SyntheticEmbedding())
    assert adapter.embed_query("a") == [1.0, 0.0]
    assert len(adapter.embed_documents(["a", "b"])) == 2


def test_repeated_scores_share_live_event_loop_and_close_it(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    seen_loops.clear()
    row = {
        "question": "接口超时是多少？",
        "answer": "超时为 3000 毫秒",
        "retrieved_contexts": ["接口超时为 3000 毫秒"],
        "reference_answer": "3000 毫秒",
    }
    with RagasScorer(
        SyntheticJudge(responses=[]), SyntheticEmbedding(), load_config("eval")
    ) as scorer:
        for _ in range(3):
            assert scorer.score(row)["faithfulness"] == 1.0
        assert seen_loops and len({id(loop) for loop in seen_loops}) == 1
        assert not seen_loops[0].is_closed()
    assert seen_loops[0].is_closed()
