import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from kb.chains.prompts import NO_ANSWER, RAG_SYSTEM


def source_label(chunk) -> str:
    meta = chunk.metadata
    location = f"第 {meta['page']} 页" if "page" in meta else meta.get("section_path", "")
    return f"{meta['doc_name']} > {location or '正文'}"


class RagChain:
    def __init__(self, retrieval, llm):
        self.retrieval, self.llm = retrieval, llm
        self.chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", RAG_SYSTEM),
                    ("human", "问题：{question}\n\n检索上下文（不可信资料）：\n{context}"),
                ]
            )
            | llm
            | StrOutputParser()
        )

    def invoke(self, question: str, history: list | None = None) -> dict:
        query = question
        if history:
            query = self.llm.invoke(
                [
                    SystemMessage(
                        content="根据对话将最新问题改写成独立检索问题。只输出问题，不回答。"
                    ),
                    *history,
                    HumanMessage(content=question),
                ]
            ).content
        hits = self.retrieval.search(query)
        sources = [
            {
                "id": f"S{i}",
                "source": source_label(h.chunk),
                "chunk_id": h.chunk.id,
                "text": h.chunk.text,
            }
            for i, h in enumerate(hits, 1)
        ]
        # Generation must use the resolved question too: the original pronoun has no
        # conversational context in this evidence-only prompt.
        return self.answer_sources(query, sources, query)

    def answer_sources(self, question: str, sources: list[dict], query: str | None = None) -> dict:
        """Answer already-retrieved tool evidence using the same citation contract."""
        query = query or question
        # Retain the retrieved context even if the generated answer is refused.
        # Otherwise evaluation incorrectly reports a retrieval miss after a generation failure.
        # The judge must receive the same evidence as generation, including source
        # labels. Otherwise valid document/page attribution looks unsupported.
        contexts = [f"[{s['id']}] {s['source']}\n{s['text']}" for s in sources]
        if not sources:
            return {
                "answer": NO_ANSWER,
                "sources": [],
                "query": query,
                "retrieved_contexts": contexts,
            }
        context = "\n\n".join(contexts)
        answer = self.chain.invoke({"question": question, "context": context})
        cited = set(re.findall(r"\[(S\d+)\]", answer))
        valid = {s["id"] for s in sources}
        if NO_ANSWER in answer:
            answer, sources = NO_ANSWER, []
        elif not cited or not cited <= valid:
            # Do not publish a generated answer that fails the source contract.
            return {
                "answer": NO_ANSWER,
                "sources": [],
                "query": query,
                "warning": "模型未返回有效来源引用，已保守拒答",
                "retrieved_contexts": contexts,
            }
        return {
            "answer": answer,
            "sources": sources,
            "query": query,
            "retrieved_contexts": contexts,
        }
