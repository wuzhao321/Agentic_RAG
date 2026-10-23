from pathlib import Path

from langchain_core.tools import StructuredTool

from kb.agent.tools.document_summary import SummaryArgs, summarize_document
from kb.agent.tools.knowledge_search import KnowledgeArgs
from kb.agent.tools.project_query import ProjectArgs, query_projects, read_ledger
from kb.chains.rag_chain import source_label


def build_tools(retrieval, chunks, llm, config: dict):
    def knowledge(**kwargs):
        args = KnowledgeArgs.model_validate(kwargs)
        filters = args.model_dump(exclude_none=True, exclude={"query"})
        return [
            {"text": h.chunk.text, "source": source_label(h.chunk), "chunk_id": h.chunk.id}
            for h in retrieval.search(args.query, filters)
        ]

    def summary(**kwargs):
        return summarize_document(
            chunks, SummaryArgs.model_validate(kwargs), llm, config["summary_batch_chars"]
        )

    def projects(**kwargs):
        rows = query_projects(
            read_ledger(Path(config["ledger_path"])),
            ProjectArgs.model_validate(kwargs),
            config["project_match_score"],
        )
        return {
            "source": config.get("ledger_source", "内部项目台账"),
            "amount_unit": "人民币元",
            "projects": rows,
        }

    return [
        StructuredTool.from_function(
            knowledge,
            name="KnowledgeSearchTool",
            args_schema=KnowledgeArgs,
            description=(
                "检索技术规范、接口说明和交付报告，返回证据和来源；不用来查询台账金额或摘要全文。"
                '例：查 CreateOrder 的参数 → query="CreateOrder 请求参数"；'
                '查星河项目技术规范 → query="技术要求", project_name="星河", doc_type="技术规范"。'
            ),
        ),
        StructuredTool.from_function(
            summary,
            name="DocumentSummaryTool",
            args_schema=SummaryArgs,
            description=(
                "对明确指定的单份文档做全文摘要。doc_name 优先完整相对路径，也支持唯一文件名；"
                "doc_id 为文档编号，至少提供一个。focus 是可选关注点。"
                '例：总结接口.docx → doc_name="接口.docx"；'
                '概括交付.pdf的风险 → doc_name="交付.pdf", focus="风险"。'
            ),
        ),
        StructuredTool.from_function(
            projects,
            name="ProjectQueryTool",
            args_schema=ProjectArgs,
            description=(
                "查询项目台账的名称、签订日期、合同金额、负责人、采购人和供应商。"
                "日期 YYYY-MM-DD；金额为人民币元，万元必须先转换。"
                "min_amount/max_amount 含边界；超过/大于用 amount_gt，小于用 amount_lt，"
                "直接填写比较值，不要自行加减 1 元或 0.01 元。"
                "fields 可选 project_id/project_name/signed_date/amount/owner/buyer/supplier。"
                '例：星河谁负责 → project_name="星河", fields=["owner"]；'
                '2025年合同超过100万元 → signed_after="2025-01-01", '
                'signed_before="2025-12-31", amount_gt=1000000。'
            ),
        ),
    ]
