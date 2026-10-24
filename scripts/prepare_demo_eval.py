"""Build demo cases with source, parsed-document and indexed-evidence checks."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from kb.config import load_config
from kb.eval.agent_eval import AgentTask
from kb.ingest.indexer import read_chunks
from kb.ingest.metadata import parse

CASES = [
    (
        "body",
        "FastAPI 使用什么模型来声明请求体？",
        "使用 Pydantic 模型声明请求体。",
        "使用 Pydantic 模型来声明请求体",
        ["Pydantic"],
    ),
    (
        "body",
        "FastAPI 的请求数据模型需要继承什么类？",
        "需要继承 Pydantic 的 BaseModel 类。",
        "把数据模型声明为继承 BaseModel 的类",
        ["BaseModel"],
    ),
    (
        "body",
        "FastAPI 中请求体和响应体的数据传输方向分别是什么？",
        "请求体是客户端发送给 API 的数据，响应体是 API 发送给客户端的数据。",
        "请求体是客户端发送给你的 API 的数据",
        ["客户端", "API"],
    ),
    (
        "query-params",
        "FastAPI 中如何把查询参数设置为必选？",
        "不为该查询参数声明默认值。",
        "如果要把查询参数设置为必选，就不要声明默认值",
        ["默认值"],
    ),
    (
        "query-params",
        "FastAPI 查询字符串在 URL 中位于哪个符号后面，键值对如何分隔？",
        "位于 ? 之后，键值对使用 & 分隔。",
        "这些键值对位于 URL 的 ? 之后，以 & 分隔",
        ["?", "&"],
    ),
    (
        "query-params",
        "FastAPI 如何声明默认值为 None 的可选查询参数？",
        "把查询参数默认值设置为 None。",
        "把默认值设为 None 即可声明可选的查询参数",
        ["None"],
    ),
    (
        "path-params",
        "FastAPI 的 /users/me 和 /users/{user_id} 应按什么顺序声明？",
        "先声明 /users/me，再声明 /users/{user_id}。",
        "一定要在 /users/{user_id} 之前声明 /users/me",
        ["/users/me", "/users/{user_id}"],
    ),
    (
        "path-params",
        "FastAPI 预设路径参数的 Enum 类应该继承哪两个类？",
        "继承 str 和 Enum。",
        "导入 Enum 并创建继承自 str 和 Enum 的子类",
        ["str", "Enum"],
    ),
    (
        "path-params",
        "FastAPI 中如何获取枚举成员的实际值？",
        "使用 model_name.value 或 your_enum_member.value。",
        "使用 model_name.value",
        [".value"],
    ),
    (
        "response-model",
        "FastAPI 的 response_model 和返回类型同时声明时，谁优先？",
        "response_model 具有优先级并由 FastAPI 使用。",
        "response_model 会具有优先级",
        ["response_model", "优先"],
    ),
    (
        "response-model",
        "FastAPI 如何禁用某个路径操作的响应模型生成？",
        "设置 response_model=None。",
        "使用 response_model=None 来禁用",
        ["response_model", "None"],
    ),
    (
        "response-model",
        "FastAPI 的 response_model 是路径操作函数的参数吗？",
        "不是，它是路径操作装饰器 get、post 等方法的参数。",
        "是「装饰器」方法（get、post 等）的一个参数",
        ["装饰器"],
    ),
    (
        "handling-errors",
        "FastAPI 的 HTTPException 应该 return 还是 raise？",
        "应当 raise，不能 return。",
        "因为是 Python 异常，所以不能 return，只能 raise",
        ["raise", "return"],
    ),
    (
        "handling-errors",
        "FastAPI HTTPException 的 detail 只能传字符串吗？",
        "不是，可以传递任何能转换为 JSON 的值，也支持 dict、list。",
        "传递任何能转换为 JSON 的值，不仅限于 str",
        ["JSON"],
    ),
    (
        "handling-errors",
        "FastAPI 请求中包含无效数据时，内部会触发什么异常？",
        "RequestValidationError。",
        "内部会触发 RequestValidationError",
        ["RequestValidationError"],
    ),
    (
        "zhejiang-gpu",
        "浙江税务 GPU 算力服务租用项目的合同金额是多少万元？",
        "168.679200万元。",
        "168.679200万元",
        ["168.679200", "万元"],
    ),
    (
        "haikou-ai",
        "海口市 AI+ 数字内容生产服务的合同金额是多少？",
        "2,861,000.00元。",
        "2,861,000.00元",
        ["2861000", "元"],
    ),
    (
        "haikou-ai",
        "海口市 AI+ 数字内容生产服务合同的履约期限是什么？",
        "2026年04月08日至2027年04月08日。",
        "2026年04月08日至2027年04月08日",
        ["2026", "2027"],
    ),
]


def normalized(text):
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", text)).replace("*", "").replace("`", "")


def main():
    out = Path("data/demo")
    manifest = json.loads((out / "manifest.json").read_text())
    documents = {d["id"]: d for d in manifest["documents"]}
    chunks = read_chunks(Path("data/index"))
    qa, checks = [], []
    for i, (slug, question, answer, needle, points) in enumerate(CASES, 1):
        document = documents[slug]
        raw = Path(document["source_path"]).read_text()
        # Procurement source is a JSON snapshot; decode the recorded page text first.
        if document["source_path"].endswith(".json"):
            raw = next(r["snapshot"] for r in json.loads(raw) if r["id"] == slug)
        if normalized(needle) not in normalized(raw):
            raise ValueError(f"原始来源未包含参考证据: {question}")
        parsed = parse(Path("data/raw") / document["file_name"], load_config("ingest"))
        block = next((b for b in parsed.blocks if normalized(needle) in normalized(b.text)), None)
        matches = [
            c
            for c in chunks
            if c.metadata["doc_name"] == document["file_name"]
            and normalized(needle) in normalized(c.text)
        ]
        if not block or not matches:
            raise ValueError(f"解析文档或索引缺失参考证据: {question}")
        context = {
            "doc_name": document["file_name"],
            "text": block.text,
            "page": block.page,
            "section_path": block.section_path,
            "source_url": document["source_url"],
            "chunk_ids": [c.id for c in matches],
        }
        qa.append(
            {
                "id": f"DEMO-QA-{i:03}",
                "question": question,
                "reference_answer": answer,
                "reference_contexts": [context],
                "doc_type": document["doc_type"],
                "project_name": document["project_name"],
                "question_type": "table" if block.kind == "table" else "single",
                "answer_points": points,
                "demo_only": True,
                "review_status": "source_evidence_checked",
                "human_reviewed": False,
            }
        )
        checks.append(
            {
                "id": qa[-1]["id"],
                "evidence_quote": needle,
                "source_url": document["source_url"],
                "source_sha256": document["source_sha256"],
                "source_contains_evidence": True,
                "parsed_contains_evidence": True,
                "indexed_chunk_ids": context["chunk_ids"],
                "answer_review": "参考答案附来源证据；校验证据在原文、解析文档和索引中的存在性",
            }
        )
    for question in [
        "公司报销出租车费用的上限是多少？",
        "浙江税务 GPU 项目的 root 管理员密码是什么？",
    ]:
        qa.append(
            {
                "id": f"DEMO-QA-{len(qa) + 1:03}",
                "question": question,
                "reference_answer": "知识库中未检索到相关内容",
                "reference_contexts": [],
                "doc_type": "其他",
                "project_name": "",
                "question_type": "no_answer",
                "answer_points": ["知识库中未检索到相关内容"],
                "demo_only": True,
                "review_status": "source_evidence_checked",
                "human_reviewed": False,
            }
        )
    tasks = [
        (
            "查询海口市AI+数字内容生产服务的合同金额。",
            "ProjectQueryTool",
            {"project_name": "海口市AI+数字内容生产服务", "fields": ["amount"]},
            ["2861000"],
        ),
        (
            "查询2026年签订且合同金额大于200万元的项目，返回项目名称和金额。",
            "ProjectQueryTool",
            {
                "signed_after": "2026-01-01",
                "signed_before": "2026-12-31",
                "amount_gt": "2000000",
                "fields": ["project_name", "amount"],
            },
            ["海口", "2861000"],
        ),
        (
            "查询海口市AI+数字内容生产服务的供应商。",
            "ProjectQueryTool",
            {"project_name": "海口市AI+数字内容生产服务", "fields": ["supplier"]},
            ["北京昆仑联通"],
        ),
        (
            "查询海口市AI+数字内容生产服务的项目负责人。",
            "ProjectQueryTool",
            {"project_name": "海口市AI+数字内容生产服务", "fields": ["owner"]},
            ["未公开"],
        ),
        (
            "检索 FastAPI HTTPException 的使用方式。",
            "KnowledgeSearchTool",
            {"query": "FastAPI HTTPException 使用方式"},
            ["raise"],
        ),
        (
            "检索 FastAPI 必选查询参数的声明方式。",
            "KnowledgeSearchTool",
            {"query": "FastAPI 必选查询参数 声明方式"},
            ["默认值"],
        ),
        (
            "总结 public_demo/浙江税务GPU算力采购公告.pdf 的主要信息。",
            "DocumentSummaryTool",
            {"doc_name": "public_demo/浙江税务GPU算力采购公告.pdf"},
            ["168.679200", "2026"],
        ),
        (
            "总结 public_demo/海口AI数字内容采购公告.docx 的主要信息。",
            "DocumentSummaryTool",
            {"doc_name": "public_demo/海口AI数字内容采购公告.docx"},
            ["2861000", "2027"],
        ),
    ]
    agents = [
        AgentTask(
            id=f"DEMO-AG-{i:03}",
            question=q,
            expected_tools=[tool],
            expected_args=[args],
            answer_points=points,
        ).model_dump(mode="json")
        for i, (q, tool, args, points) in enumerate(tasks, 1)
    ]
    for name, rows in [("qa.jsonl", qa), ("agent_tasks.jsonl", agents)]:
        (out / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    review = {
        "reviewed_at": datetime.now(UTC).isoformat(),
        "check_method": "source_parse_index_evidence",
        "human_reviewed": False,
        "formal_acceptance": False,
        "qa_count": len(qa),
        "agent_task_count": len(agents),
        "evidence_checks_passed": len(checks),
        "evidence_checks_total": len(CASES),
        "no_answer_cases": 2,
        "qa_sha256": hashlib.sha256((out / "qa.jsonl").read_bytes()).hexdigest(),
        "agent_tasks_sha256": hashlib.sha256((out / "agent_tasks.jsonl").read_bytes()).hexdigest(),
        "checks": checks,
        "notes": [
            "拒答题按已导入语料范围设置，与证据字符串存在性检查分开管理。",
            "Agent query 同义改写可能被严格参数比较判为不一致，需结合轨迹复核。",
            "演示集用于开发验证；正式评估使用独立的 qa_holdout.jsonl 留出集。",
        ],
    }
    (out / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2))
    print(
        f"Prepared {len(qa)} QA cases, {len(agents)} Agent tasks; "
        f"{len(checks)} evidence checks passed."
    )


if __name__ == "__main__":
    main()
