import os

from langchain_core.embeddings import Embeddings

# Evaluation must not add a third-party telemetry destination.
os.environ["RAGAS_DO_NOT_TRACK"] = "true"


class BgeEmbeddings(Embeddings):
    def __init__(self, local_embedding):
        self.local_embedding = local_embedding

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.local_embedding.encode(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.local_embedding.encode([text])[0]


class RagasScorer:
    def __init__(self, llm, local_embedding, config: dict):
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import Faithfulness, LLMContextRecall, ResponseRelevancy
        from ragas.run_config import RunConfig

        cfg = config["ragas"]
        self.run_config = RunConfig(
            timeout=cfg["timeout"],
            max_retries=cfg["max_retries"],
            max_workers=cfg["max_workers"],
            seed=config["seed"],
        )
        # Keep the same generation model and temperature=0. RAGAS normally changes
        # temperature and n on the underlying model; bypass both to avoid shared-state mutation
        # and providers that do not support n>1. Repeated samples use separate requests.
        self.llm = LangchainLLMWrapper(
            llm, run_config=self.run_config, bypass_temperature=True, bypass_n=True
        )
        self.embeddings = LangchainEmbeddingsWrapper(
            BgeEmbeddings(local_embedding), run_config=self.run_config
        )
        self.metrics = [
            Faithfulness(),
            ResponseRelevancy(strictness=cfg["answer_relevancy_strictness"]),
            LLMContextRecall(),
        ]

    def score(self, row: dict) -> dict:
        from ragas import EvaluationDataset, evaluate

        dataset = EvaluationDataset.from_list(
            [
                {
                    "user_input": row["question"],
                    "response": row["answer"],
                    "retrieved_contexts": row["retrieved_contexts"],
                    "reference": row["reference_answer"],
                }
            ]
        )
        result = evaluate(
            dataset,
            metrics=self.metrics,
            llm=self.llm,
            embeddings=self.embeddings,
            run_config=self.run_config,
            show_progress=False,
            raise_exceptions=True,
        )
        return result.scores[0]
